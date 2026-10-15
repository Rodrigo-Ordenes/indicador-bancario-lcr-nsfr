"""
db_layer.py
===========
Emulación local (SQLite) de la arquitectura de 4 capas cuyo DDL de producción
vive en `sql/00..06` (SQL Server 2017). Cada regla que en SQL Server se impone
con constraints, triggers o procedimientos se impone aquí también dentro del
motor (CHECK, triggers BEFORE UPDATE/DELETE, índices únicos) y no sólo en Python.

Capas
-----
1. OLTP / Ledger inmutable (Data Plane)
   - Partida doble multi-pata, append-only (triggers bloquean UPDATE/DELETE).
   - Transferencias en dos fases (PENDING -> POST / VOID / EXPIRE) con saldos
     `debits_pending / credits_pending / debits_posted / credits_posted`
     (modelo de TigerBeetle) y límites de sobregiro por cuenta.
   - Idempotencia atómica (`oltp_idempotency_records`) y Transactional Outbox
     escritos en la MISMA transacción que el asiento.
   - Cadena SHA-256 verificable que recalcula el hash de cada fila y cabecera.
   - Transacción no interactiva: cada operación contable es una sola llamada que
     abre `BEGIN IMMEDIATE`, valida, escribe y confirma, sin devolver el control
     al llamador con bloqueos tomados (equivalente al stored procedure
     `oltp.usp_post_atomic_journal_entry`).
2. Staging & control: bitácora de lotes, checksums de fila, relay del outbox con
   checkpoint (effectively-once), reconciliación de tres vías.
3. Core bitemporal / ODS (Asserted Versioning Framework):
   - Tiempo efectivo (`valid_from/valid_to`) y tiempo asertivo
     (`asserted_from/asserted_to`), siempre cerrados con 9999-12-31 23:59:59.99.
   - Trigger que impide solapamientos bitemporales, que impide DELETE y que sólo
     permite "cerrar" `asserted_to` (retiro de una aserción).
   - Operaciones: insert, update (cambio real de negocio, no retroactivo),
     correction (enmienda retroactiva ligada a un asiento del ledger) y
     future assertion (aserción diferida para migraciones sin downtime).
4. DWH Kimball: dim_date, dim_partner (grupo económico), dim_product, dim_branch,
   dim_gl_account, dim_contract, minidimensión dim_risk_band, dim_liquidity_treatment,
   dim_time_bucket; hechos complementarios: snapshot periódico (con
   reexpresiones), postings transaccionales (secuencia por cuenta + final_flag,
   Kimball fig. 9.5) y flujos contractuales (C54 / C55 / C56).

Representación monetaria
------------------------
SQLite no tiene DECIMAL nativo (NUMERIC se degrada a REAL). Por eso todo monto se
almacena como INTEGER escalado 10^4 (sufijo `_e4`, equivalente exacto a
DECIMAL(19,4)) y toda tasa/factor como INTEGER escalado 10^6 (sufijo `_e6`,
equivalente a DECIMAL(12,6)). Python opera con `Decimal`; los float sólo
aparecen en los DataFrames de presentación.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

import regulatory_engine as reg

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("America/Santiago")
except Exception:  # pragma: no cover - entornos sin tzdata
    LOCAL_TZ = timezone(timedelta(hours=-3))

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "core_banking.db")
SCHEMA_VERSION = 2

END_OF_TIME = "9999-12-31 23:59:59.99"
ZERO_HASH = "0" * 64
BOOK_START = date(2024, 12, 1)
LOCAL_CUTOFF_HOUR = 18           # Asientos después de las 18:00 hora local van al día hábil siguiente
IDEMPOTENCY_TTL_DAYS = 3650      # Retención regulatoria de claves (10 años) en el demo
MM = Decimal(1_000_000)          # 1 MM$ = 1.000.000 CLP

E4 = 10_000
E6 = 1_000_000
E8 = 100_000_000


# =============================================================================
# 0. UTILIDADES DE TIPOS, TIEMPO Y HASH
# =============================================================================

def m2i(value) -> int:
    """Decimal/str/int -> INTEGER escalado 10^4 (DECIMAL(19,4))."""
    d = reg.to_money(value)
    return int((d * E4).to_integral_value(rounding=ROUND_HALF_EVEN))


def i2m(value: Optional[int]) -> Decimal:
    if value is None:
        return Decimal("0.0000")
    return (Decimal(int(value)) / E4).quantize(reg.MONEY_QUANTUM)


def f2i(value) -> int:
    """Tasa/factor -> INTEGER escalado 10^6 (DECIMAL(12,6))."""
    d = reg.to_factor(value)
    return int((d * E6).to_integral_value(rounding=ROUND_HALF_EVEN))


def i2f(value: Optional[int]) -> Decimal:
    if value is None:
        return Decimal("0.000000")
    return (Decimal(int(value)) / E6).quantize(reg.FACTOR_QUANTUM)


def ts(dt: datetime) -> str:
    """Formato canónico de timestamp (2 decimales, comparable lexicográficamente)."""
    return f"{dt:%Y-%m-%d %H:%M:%S}.{dt.microsecond // 10000:02d}"


def parse_ts(value: str) -> datetime:
    return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def local_to_utc(d: date, hour: int, minute: int = 0) -> datetime:
    local = datetime(d.year, d.month, d.day, hour, minute, tzinfo=LOCAL_TZ)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def utc_to_local(dt_utc: datetime) -> datetime:
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ).replace(tzinfo=None)


def end_of_local_day_utc(d: date) -> datetime:
    """Último instante (UTC) del día hábil local `d`: frontera de conocimiento del cierre diario."""
    nxt = datetime(d.year, d.month, d.day, tzinfo=LOCAL_TZ) + timedelta(days=1)
    return nxt.astimezone(timezone.utc).replace(tzinfo=None) - timedelta(microseconds=10000)


def local_cutoff_date(dt_utc: datetime) -> date:
    """Fecha contable: hora local con corte a las 18:00 (después del corte -> día siguiente)."""
    local = utc_to_local(dt_utc)
    d = local.date()
    if local.hour >= LOCAL_CUTOFF_HOUR:
        d = d + timedelta(days=1)
    return d


def date_key(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


def key_to_date(k: int) -> date:
    return date(k // 10000, (k // 100) % 100, k % 100)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


class LedgerRejection(Exception):
    """Rechazo de negocio determinista (se registra como FAILED en idempotencia)."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class IdempotencyConflict(LedgerRejection):
    """Misma clave de idempotencia con un payload distinto: nunca se procesa."""


class BitemporalViolation(Exception):
    pass


# =============================================================================
# 1. CONEXIÓN Y DDL
# =============================================================================

def get_connection(path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_FILE, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")      # lectores no bloquean al escritor (análogo a RCSI)
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


@contextmanager
def atomic(conn: sqlite3.Connection):
    """
    Transacción de escritura no interactiva. `BEGIN IMMEDIATE` toma el lock de
    escritura al inicio (sin escalamiento lectura->escritura que provoque
    deadlocks) y se libera al confirmar; ningún código de red se ejecuta dentro.
    """
    conn.execute("BEGIN IMMEDIATE;")
    try:
        yield conn
        conn.execute("COMMIT;")
    except BaseException:
        conn.execute("ROLLBACK;")
        raise


DDL = r"""
-- ---------------------------------------------------------------------------
-- CAPA 1: OLTP / LEDGER INMUTABLE
-- ---------------------------------------------------------------------------
CREATE TABLE oltp_ledger_account (
    account_id                      INTEGER PRIMARY KEY,
    account_code                    TEXT    NOT NULL UNIQUE,
    account_name                    TEXT    NOT NULL,
    account_class                   TEXT    NOT NULL CHECK (account_class IN ('ASSET','LIABILITY','EQUITY','INCOME','EXPENSE','CLEARING')),
    normal_balance                  TEXT    NOT NULL CHECK (normal_balance IN ('D','C')),
    currency_code                   TEXT    NOT NULL,
    ledger_id                       INTEGER NOT NULL DEFAULT 1,
    debits_must_not_exceed_credits  INTEGER NOT NULL DEFAULT 0 CHECK (debits_must_not_exceed_credits IN (0,1)),
    credits_must_not_exceed_debits  INTEGER NOT NULL DEFAULT 0 CHECK (credits_must_not_exceed_debits IN (0,1)),
    is_contract_principal           INTEGER NOT NULL DEFAULT 0 CHECK (is_contract_principal IN (0,1)),
    created_at                      TEXT    NOT NULL,
    CHECK (NOT (debits_must_not_exceed_credits = 1 AND credits_must_not_exceed_debits = 1))
) STRICT;

-- Saldos por (cuenta, contrato). Tabla separada del diario append-only: sólo la
-- operación atómica la modifica; nunca se borra.
CREATE TABLE oltp_account_balance (
    account_id            INTEGER NOT NULL REFERENCES oltp_ledger_account(account_id),
    contract_id           INTEGER NOT NULL DEFAULT 0,
    debits_pending_e4     INTEGER NOT NULL DEFAULT 0 CHECK (debits_pending_e4  >= 0),
    credits_pending_e4    INTEGER NOT NULL DEFAULT 0 CHECK (credits_pending_e4 >= 0),
    debits_posted_e4      INTEGER NOT NULL DEFAULT 0 CHECK (debits_posted_e4   >= 0),
    credits_posted_e4     INTEGER NOT NULL DEFAULT 0 CHECK (credits_posted_e4  >= 0),
    last_journal_entry_id INTEGER NULL,
    updated_at            TEXT    NOT NULL,
    PRIMARY KEY (account_id, contract_id)
) STRICT;

CREATE TABLE oltp_journal_entry (
    journal_entry_id            INTEGER PRIMARY KEY,
    entry_uuid                  TEXT    NOT NULL UNIQUE,
    idempotency_key             TEXT    NOT NULL UNIQUE,
    entry_kind                  TEXT    NOT NULL CHECK (entry_kind IN ('POSTED','PENDING','POST_PENDING','VOID_PENDING','EXPIRE_PENDING')),
    pending_entry_id            INTEGER NULL REFERENCES oltp_journal_entry(journal_entry_id),
    pending_expires_at          TEXT    NULL,
    transaction_timestamp_utc   TEXT    NOT NULL,
    transaction_timestamp_local TEXT    NOT NULL,
    local_cutoff_date           TEXT    NOT NULL,
    posting_date                TEXT    NOT NULL,
    value_date                  TEXT    NOT NULL,
    source_system               TEXT    NOT NULL,
    transaction_code            TEXT    NOT NULL,
    description                 TEXT    NOT NULL,
    total_debit_e4              INTEGER NOT NULL CHECK (total_debit_e4  >= 0),
    total_credit_e4             INTEGER NOT NULL CHECK (total_credit_e4 >= 0),
    line_count                  INTEGER NOT NULL CHECK (line_count >= 2),
    reversal_of_entry_id        INTEGER NULL REFERENCES oltp_journal_entry(journal_entry_id),
    prev_entry_hash             TEXT    NOT NULL,
    entry_hash                  TEXT    NOT NULL UNIQUE,
    created_by                  TEXT    NOT NULL,
    CHECK (total_debit_e4 = total_credit_e4),
    CHECK (entry_kind IN ('VOID_PENDING','EXPIRE_PENDING') OR total_debit_e4 > 0),
    CHECK ((entry_kind IN ('POST_PENDING','VOID_PENDING','EXPIRE_PENDING')) = (pending_entry_id IS NOT NULL)),
    CHECK ((entry_kind = 'PENDING') = (pending_expires_at IS NOT NULL)),
    CHECK (posting_date = local_cutoff_date)
) STRICT;

-- Una reserva sólo puede resolverse una vez (post, void o expire).
CREATE UNIQUE INDEX ux_journal_single_resolution
    ON oltp_journal_entry(pending_entry_id) WHERE pending_entry_id IS NOT NULL;
CREATE INDEX ix_journal_ts ON oltp_journal_entry(transaction_timestamp_utc);

CREATE TABLE oltp_ledger_posting (
    posting_id                     INTEGER PRIMARY KEY,
    journal_entry_id               INTEGER NOT NULL REFERENCES oltp_journal_entry(journal_entry_id),
    line_number                    INTEGER NOT NULL CHECK (line_number >= 1),
    account_id                     INTEGER NOT NULL REFERENCES oltp_ledger_account(account_id),
    contract_id                    INTEGER NOT NULL DEFAULT 0,
    partner_id                     INTEGER NOT NULL,
    currency_code                  TEXT    NOT NULL,
    posting_status                 TEXT    NOT NULL CHECK (posting_status IN ('PENDING','POSTED','RELEASED')),
    amount_debit_e4                INTEGER NOT NULL DEFAULT 0 CHECK (amount_debit_e4  >= 0),
    amount_credit_e4               INTEGER NOT NULL DEFAULT 0 CHECK (amount_credit_e4 >= 0),
    functional_debit_e4            INTEGER NOT NULL DEFAULT 0 CHECK (functional_debit_e4  >= 0),
    functional_credit_e4           INTEGER NOT NULL DEFAULT 0 CHECK (functional_credit_e4 >= 0),
    functional_released_debit_e4   INTEGER NOT NULL DEFAULT 0 CHECK (functional_released_debit_e4  >= 0),
    functional_released_credit_e4  INTEGER NOT NULL DEFAULT 0 CHECK (functional_released_credit_e4 >= 0),
    fx_rate_e6                     INTEGER NOT NULL CHECK (fx_rate_e6 > 0),
    row_hash                       TEXT    NOT NULL,
    UNIQUE (journal_entry_id, line_number),
    CHECK (NOT (amount_debit_e4 > 0 AND amount_credit_e4 > 0)),
    CHECK (NOT (functional_released_debit_e4 > 0 AND functional_released_credit_e4 > 0)),
    CHECK (posting_status = 'RELEASED' OR amount_debit_e4 + amount_credit_e4 > 0)
) STRICT;
CREATE INDEX ix_posting_account_contract ON oltp_ledger_posting(account_id, contract_id);

CREATE TABLE oltp_idempotency_records (
    idempotency_key       TEXT    PRIMARY KEY,
    request_payload_hash  TEXT    NOT NULL,
    operation             TEXT    NOT NULL,
    status                TEXT    NOT NULL CHECK (status IN ('PENDING','POSTED','FAILED')),
    journal_entry_id      INTEGER NULL REFERENCES oltp_journal_entry(journal_entry_id),
    response_json         TEXT    NOT NULL,
    created_at            TEXT    NOT NULL,
    expires_at            TEXT    NOT NULL
) STRICT;

CREATE TABLE oltp_transactional_outbox (
    outbox_id         INTEGER PRIMARY KEY,
    event_uuid        TEXT    NOT NULL UNIQUE,
    aggregate_type    TEXT    NOT NULL,
    aggregate_id      TEXT    NOT NULL,
    event_type        TEXT    NOT NULL,
    payload_json      TEXT    NOT NULL,
    journal_entry_id  INTEGER NULL REFERENCES oltp_journal_entry(journal_entry_id),
    created_at        TEXT    NOT NULL
) STRICT;

-- Inmutabilidad en el motor (equivalente a INSTEAD OF UPDATE, DELETE + DENY).
CREATE TRIGGER trg_journal_entry_no_update BEFORE UPDATE ON oltp_journal_entry
BEGIN SELECT RAISE(ABORT, 'VIOLACION_INMUTABILIDAD: oltp_journal_entry es append-only; use un asiento de reverso.'); END;
CREATE TRIGGER trg_journal_entry_no_delete BEFORE DELETE ON oltp_journal_entry
BEGIN SELECT RAISE(ABORT, 'VIOLACION_INMUTABILIDAD: oltp_journal_entry es append-only; use un asiento de reverso.'); END;
CREATE TRIGGER trg_posting_no_update BEFORE UPDATE ON oltp_ledger_posting
BEGIN SELECT RAISE(ABORT, 'VIOLACION_INMUTABILIDAD: oltp_ledger_posting es append-only.'); END;
CREATE TRIGGER trg_posting_no_delete BEFORE DELETE ON oltp_ledger_posting
BEGIN SELECT RAISE(ABORT, 'VIOLACION_INMUTABILIDAD: oltp_ledger_posting es append-only.'); END;
CREATE TRIGGER trg_outbox_no_update BEFORE UPDATE ON oltp_transactional_outbox
BEGIN SELECT RAISE(ABORT, 'VIOLACION_INMUTABILIDAD: el outbox es insert-only; el avance se registra en stg_outbox_checkpoint.'); END;
CREATE TRIGGER trg_outbox_no_delete BEFORE DELETE ON oltp_transactional_outbox
BEGIN SELECT RAISE(ABORT, 'VIOLACION_INMUTABILIDAD: el outbox es insert-only.'); END;
CREATE TRIGGER trg_idem_no_update BEFORE UPDATE ON oltp_idempotency_records
BEGIN SELECT RAISE(ABORT, 'VIOLACION_INMUTABILIDAD: un registro de idempotencia no se reescribe.'); END;
CREATE TRIGGER trg_idem_purge_only_expired BEFORE DELETE ON oltp_idempotency_records
WHEN OLD.expires_at >= strftime('%Y-%m-%d %H:%M:%S', 'now')
BEGIN SELECT RAISE(ABORT, 'VIOLACION_RETENCION: sólo se purgan claves de idempotencia expiradas.'); END;
CREATE TRIGGER trg_balance_no_delete BEFORE DELETE ON oltp_account_balance
BEGIN SELECT RAISE(ABORT, 'VIOLACION_INMUTABILIDAD: los saldos no se eliminan.'); END;

-- Defensa en profundidad del invariante de partida doble: el detalle nunca
-- puede exceder lo declarado (y cuadrado por CHECK) en la cabecera.
CREATE TRIGGER trg_posting_within_header AFTER INSERT ON oltp_ledger_posting
WHEN (SELECT SUM(functional_debit_e4)  FROM oltp_ledger_posting WHERE journal_entry_id = NEW.journal_entry_id)
       > (SELECT total_debit_e4  FROM oltp_journal_entry WHERE journal_entry_id = NEW.journal_entry_id)
  OR (SELECT SUM(functional_credit_e4) FROM oltp_ledger_posting WHERE journal_entry_id = NEW.journal_entry_id)
       > (SELECT total_credit_e4 FROM oltp_journal_entry WHERE journal_entry_id = NEW.journal_entry_id)
BEGIN SELECT RAISE(ABORT, 'DESCUADRE_CONTABLE: el detalle excede los totales declarados del asiento.'); END;

CREATE VIEW oltp_v_unbalanced_entries AS
SELECT j.journal_entry_id,
       j.total_debit_e4,
       COALESCE(SUM(p.functional_debit_e4), 0)  AS detail_debit_e4,
       COALESCE(SUM(p.functional_credit_e4), 0) AS detail_credit_e4
FROM oltp_journal_entry j
LEFT JOIN oltp_ledger_posting p ON p.journal_entry_id = j.journal_entry_id
GROUP BY j.journal_entry_id
HAVING COALESCE(SUM(p.functional_debit_e4), 0) <> j.total_debit_e4
    OR COALESCE(SUM(p.functional_credit_e4), 0) <> j.total_credit_e4
    OR COUNT(p.posting_id) <> j.line_count;

-- ---------------------------------------------------------------------------
-- CAPA 2: STAGING, CONTROL Y LINAJE
-- ---------------------------------------------------------------------------
CREATE TABLE stg_load_execution_log (
    batch_id               INTEGER PRIMARY KEY,
    batch_uuid             TEXT    NOT NULL UNIQUE,
    pipeline_name          TEXT    NOT NULL,
    target_layer           TEXT    NOT NULL,
    source_entity          TEXT    NOT NULL,
    target_entity          TEXT    NOT NULL,
    extract_start_time     TEXT    NOT NULL,
    extract_end_time       TEXT    NULL,
    records_extracted      INTEGER NOT NULL DEFAULT 0,
    records_loaded         INTEGER NOT NULL DEFAULT 0,
    records_rejected       INTEGER NOT NULL DEFAULT 0,
    status                 TEXT    NOT NULL CHECK (status IN ('RUNNING','SUCCESS','FAILED','RECONCILED','DISCREPANCY')),
    reconciliation_diff_e4 INTEGER NULL,
    error_message          TEXT    NULL,
    created_at             TEXT    NOT NULL
) STRICT;

CREATE TABLE stg_outbox_checkpoint (
    consumer_name   TEXT    PRIMARY KEY,
    last_outbox_id  INTEGER NOT NULL,
    updated_at      TEXT    NOT NULL
) STRICT;

-- Inbox del consumidor: event_uuid único => effectively-once ante reentregas.
CREATE TABLE stg_event_inbox (
    consumer_name  TEXT NOT NULL,
    event_uuid     TEXT NOT NULL,
    outbox_id      INTEGER NOT NULL,
    event_type     TEXT NOT NULL,
    received_at    TEXT NOT NULL,
    PRIMARY KEY (consumer_name, event_uuid)
) STRICT;

CREATE TABLE stg_restatement_event_log (
    event_key     TEXT PRIMARY KEY,
    known_at      TEXT NOT NULL,
    first_date    TEXT NOT NULL,
    last_date     TEXT NOT NULL,
    rows_restated INTEGER NOT NULL,
    processed_at  TEXT NOT NULL
) STRICT;

CREATE TABLE stg_reconciliation_result (
    batch_id             INTEGER NOT NULL REFERENCES stg_load_execution_log(batch_id),
    reconciliation_date  TEXT    NOT NULL,
    contract_id          INTEGER NOT NULL,
    core_balance_e4      INTEGER NULL,
    ledger_balance_e4    INTEGER NOT NULL,
    dwh_balance_e4       INTEGER NULL,
    diff_core_ledger_e4  INTEGER NULL,
    diff_ledger_dwh_e4   INTEGER NULL,
    status               TEXT    NOT NULL,
    PRIMARY KEY (batch_id, contract_id)
) STRICT;

-- ---------------------------------------------------------------------------
-- CAPA 3: CORE BITEMPORAL / ODS (ASSERTED VERSIONING FRAMEWORK)
-- ---------------------------------------------------------------------------
CREATE TABLE core_contract_profile (
    contract_id                  INTEGER NOT NULL,
    partner_id                   INTEGER NOT NULL,
    contract_number              TEXT    NOT NULL,
    product_code                 TEXT    NOT NULL,
    branch_code                  TEXT    NOT NULL,
    currency_code                TEXT    NOT NULL,
    gl_account_id                INTEGER NOT NULL,
    origination_date             TEXT    NOT NULL,
    maturity_date                TEXT    NULL,
    original_amount_e4           INTEGER NOT NULL,
    current_balance_e4           INTEGER NOT NULL,
    committed_limit_e4           INTEGER NOT NULL DEFAULT 0,
    undrawn_commitment_e4        INTEGER NOT NULL DEFAULT 0 CHECK (undrawn_commitment_e4 >= 0),
    annual_nominal_rate_e6       INTEGER NOT NULL,
    spread_rate_e6               INTEGER NOT NULL DEFAULT 0,
    repricing_frequency          TEXT    NOT NULL,
    next_repricing_date          TEXT    NULL,
    payment_frequency_months     INTEGER NULL,
    amortization_type            TEXT    NOT NULL CHECK (amortization_type IN ('BULLET','POOL','NONE')),
    cmf_portfolio_type           TEXT    NOT NULL,
    cmf_rating                   TEXT    NOT NULL,
    days_past_due                INTEGER NOT NULL DEFAULT 0,
    collateral_value_e4          INTEGER NOT NULL DEFAULT 0,
    liquidity_treatment_code     TEXT    NOT NULL,
    contract_status              TEXT    NOT NULL,
    balance_maintained_in_core   INTEGER NOT NULL DEFAULT 1 CHECK (balance_maintained_in_core IN (0,1)),
    valid_from                   TEXT    NOT NULL,
    valid_to                     TEXT    NOT NULL,
    asserted_from                TEXT    NOT NULL,
    asserted_to                  TEXT    NOT NULL,
    change_type                  TEXT    NOT NULL CHECK (change_type IN ('INSERT','UPDATE','CORRECTION','FUTURE_ASSERTION','CARRY_FORWARD')),
    change_reason                TEXT    NULL,
    source_journal_entry_id      INTEGER NULL REFERENCES oltp_journal_entry(journal_entry_id),
    row_created_at               TEXT    NOT NULL,
    PRIMARY KEY (contract_id, valid_from, asserted_from),
    CHECK (valid_from < valid_to),
    CHECK (asserted_from < asserted_to),
    CHECK (committed_limit_e4 = 0 OR undrawn_commitment_e4 <= committed_limit_e4)
) STRICT;
CREATE INDEX ix_core_asserted ON core_contract_profile(asserted_from, asserted_to);

-- Restricción bitemporal (AVF "temporal entity integrity"): para un contrato no
-- pueden existir dos filas cuyo rectángulo [efectivo x asertivo] se solape.
CREATE TRIGGER trg_core_no_bitemporal_overlap BEFORE INSERT ON core_contract_profile
WHEN EXISTS (
    SELECT 1 FROM core_contract_profile c
    WHERE c.contract_id = NEW.contract_id
      AND c.valid_from    < NEW.valid_to    AND NEW.valid_from    < c.valid_to
      AND c.asserted_from < NEW.asserted_to AND NEW.asserted_from < c.asserted_to
)
BEGIN SELECT RAISE(ABORT, 'AVF_SOLAPAMIENTO: el intervalo efectivo/asertivo se solapa con una versión existente.'); END;

CREATE TRIGGER trg_core_no_delete BEFORE DELETE ON core_contract_profile
BEGIN SELECT RAISE(ABORT, 'AVF_INMUTABLE: las versiones bitemporales no se eliminan; retire la aserción.'); END;

-- La única mutación física permitida es retirar una aserción (cerrar asserted_to).
CREATE TRIGGER trg_core_only_withdraw BEFORE UPDATE ON core_contract_profile
WHEN NEW.contract_id IS NOT OLD.contract_id OR NEW.partner_id IS NOT OLD.partner_id
  OR NEW.contract_number IS NOT OLD.contract_number OR NEW.product_code IS NOT OLD.product_code
  OR NEW.branch_code IS NOT OLD.branch_code OR NEW.currency_code IS NOT OLD.currency_code
  OR NEW.gl_account_id IS NOT OLD.gl_account_id OR NEW.origination_date IS NOT OLD.origination_date
  OR NEW.maturity_date IS NOT OLD.maturity_date OR NEW.original_amount_e4 IS NOT OLD.original_amount_e4
  OR NEW.current_balance_e4 IS NOT OLD.current_balance_e4 OR NEW.committed_limit_e4 IS NOT OLD.committed_limit_e4
  OR NEW.undrawn_commitment_e4 IS NOT OLD.undrawn_commitment_e4 OR NEW.annual_nominal_rate_e6 IS NOT OLD.annual_nominal_rate_e6
  OR NEW.spread_rate_e6 IS NOT OLD.spread_rate_e6 OR NEW.repricing_frequency IS NOT OLD.repricing_frequency
  OR NEW.next_repricing_date IS NOT OLD.next_repricing_date OR NEW.payment_frequency_months IS NOT OLD.payment_frequency_months
  OR NEW.amortization_type IS NOT OLD.amortization_type OR NEW.cmf_portfolio_type IS NOT OLD.cmf_portfolio_type
  OR NEW.cmf_rating IS NOT OLD.cmf_rating OR NEW.days_past_due IS NOT OLD.days_past_due
  OR NEW.collateral_value_e4 IS NOT OLD.collateral_value_e4 OR NEW.liquidity_treatment_code IS NOT OLD.liquidity_treatment_code
  OR NEW.contract_status IS NOT OLD.contract_status OR NEW.balance_maintained_in_core IS NOT OLD.balance_maintained_in_core
  OR NEW.valid_from IS NOT OLD.valid_from OR NEW.valid_to IS NOT OLD.valid_to
  OR NEW.asserted_from IS NOT OLD.asserted_from OR NEW.change_type IS NOT OLD.change_type
  OR NEW.change_reason IS NOT OLD.change_reason OR NEW.source_journal_entry_id IS NOT OLD.source_journal_entry_id
  OR NEW.row_created_at IS NOT OLD.row_created_at
  OR NEW.asserted_to >= OLD.asserted_to
BEGIN SELECT RAISE(ABORT, 'AVF_INMUTABLE: sólo se permite adelantar asserted_to (retiro de aserción).'); END;

-- ---------------------------------------------------------------------------
-- CAPA 4: DWH KIMBALL
-- ---------------------------------------------------------------------------
CREATE TABLE dwh_dim_date (
    date_key            INTEGER PRIMARY KEY,   -- YYYYMMDD (semi-inteligente, ordenable, partición mensual)
    full_date           TEXT    NOT NULL UNIQUE,
    day_number_in_epoch INTEGER NOT NULL UNIQUE, -- secuencial continuo para aritmética de días
    year                INTEGER NOT NULL,
    quarter             INTEGER NOT NULL,
    month_number        INTEGER NOT NULL,
    month_name          TEXT    NOT NULL,
    year_month          INTEGER NOT NULL,
    day_of_month        INTEGER NOT NULL,
    day_of_week         INTEGER NOT NULL,
    day_name            TEXT    NOT NULL,
    is_month_end        INTEGER NOT NULL,
    is_business_day     INTEGER NOT NULL,
    fiscal_year         INTEGER NOT NULL
) STRICT;

CREATE TABLE dwh_dim_partner (
    partner_key               INTEGER PRIMARY KEY,
    partner_id                INTEGER NOT NULL,
    tax_id                    TEXT    NOT NULL,
    full_name                 TEXT    NOT NULL,
    partner_type              TEXT    NOT NULL,
    segment_name              TEXT    NOT NULL,
    economic_activity         TEXT    NOT NULL,
    household_group_id        INTEGER NOT NULL,
    household_group_name      TEXT    NOT NULL,
    is_financial_institution  INTEGER NOT NULL,
    is_current                INTEGER NOT NULL DEFAULT 1,
    effective_start_date      TEXT    NOT NULL,
    effective_end_date        TEXT    NOT NULL
) STRICT;
CREATE UNIQUE INDEX ux_dim_partner_current ON dwh_dim_partner(partner_id) WHERE is_current = 1;

CREATE TABLE dwh_dim_product (
    product_key          INTEGER PRIMARY KEY,
    product_code         TEXT    NOT NULL UNIQUE,
    product_name         TEXT    NOT NULL,
    product_family       TEXT    NOT NULL,
    balance_side         TEXT    NOT NULL CHECK (balance_side IN ('ASSET','LIABILITY','EQUITY','OFF_BALANCE')),
    cmf_portfolio_type   TEXT    NOT NULL,
    is_interest_bearing  INTEGER NOT NULL,
    is_loan              INTEGER NOT NULL
) STRICT;

CREATE TABLE dwh_dim_branch (
    branch_key   INTEGER PRIMARY KEY,
    branch_code  TEXT NOT NULL UNIQUE,
    branch_name  TEXT NOT NULL,
    city_name    TEXT NOT NULL,
    region_name  TEXT NOT NULL
) STRICT;

CREATE TABLE dwh_dim_gl_account (
    gl_account_key        INTEGER PRIMARY KEY,
    account_id            INTEGER NOT NULL UNIQUE,
    account_code          TEXT    NOT NULL,
    account_name          TEXT    NOT NULL,
    account_class         TEXT    NOT NULL,
    normal_balance        TEXT    NOT NULL,
    is_contract_principal INTEGER NOT NULL
) STRICT;

CREATE TABLE dwh_dim_contract (
    contract_key          INTEGER PRIMARY KEY,
    contract_id           INTEGER NOT NULL UNIQUE,
    contract_number       TEXT    NOT NULL,
    product_key           INTEGER NOT NULL REFERENCES dwh_dim_product(product_key),
    branch_key            INTEGER NOT NULL REFERENCES dwh_dim_branch(branch_key),
    origination_date_key  INTEGER NOT NULL,
    maturity_date_key     INTEGER NULL,
    currency_code         TEXT    NOT NULL,
    source_system         TEXT    NOT NULL
) STRICT;

-- Minidimensión de riesgo (Kimball cap. 9): bandas discretas fuera de la dimensión contrato.
CREATE TABLE dwh_dim_risk_band (
    risk_band_key        INTEGER PRIMARY KEY,
    cmf_rating_category  TEXT    NOT NULL,
    regulatory_status    TEXT    NOT NULL,
    dpd_bucket           TEXT    NOT NULL,
    min_dpd              INTEGER NOT NULL,
    max_dpd              INTEGER NOT NULL,
    provision_rate_e6    INTEGER NOT NULL,
    ltv_bucket           TEXT    NOT NULL,
    UNIQUE (cmf_rating_category)
) STRICT;

CREATE TABLE dwh_dim_liquidity_treatment (
    liquidity_treatment_key  INTEGER PRIMARY KEY,
    treatment_code           TEXT    NOT NULL UNIQUE,
    lcr_component            TEXT    NOT NULL CHECK (lcr_component IN ('ALAC','OUTFLOW','INFLOW','NONE')),
    lcr_category             TEXT    NOT NULL,
    hqla_level               TEXT    NOT NULL CHECK (hqla_level IN ('L1','L2A','L2B','NO_HQLA')),
    hqla_haircut_e6          INTEGER NOT NULL,
    lcr_basis                TEXT    NOT NULL CHECK (lcr_basis IN ('MARKET_VALUE','BALANCE','CASHFLOW_30D','NONE')),
    lcr_rate_e6              INTEGER NOT NULL,
    undrawn_runoff_rate_e6   INTEGER NOT NULL,
    nsfr_side                TEXT    NOT NULL CHECK (nsfr_side IN ('ASF','RSF','NONE')),
    nsfr_category            TEXT    NOT NULL,
    nsfr_factor_lt_6m_e6     INTEGER NOT NULL,
    nsfr_factor_6m_1y_e6     INTEGER NOT NULL,
    nsfr_factor_ge_1y_e6     INTEGER NOT NULL,
    undrawn_rsf_factor_e6    INTEGER NOT NULL
) STRICT;

CREATE TABLE dwh_dim_time_bucket (
    time_bucket_key        INTEGER PRIMARY KEY,
    bucket_code            TEXT    NOT NULL UNIQUE,
    bucket_label           TEXT    NOT NULL,
    min_days               INTEGER NOT NULL,
    max_days               INTEGER NOT NULL,
    midpoint_days          INTEGER NOT NULL,
    nsfr_maturity_band     TEXT    NOT NULL CHECK (nsfr_maturity_band IN ('LT_6M','6M_1Y','GE_1Y')),
    sort_order             INTEGER NOT NULL
) STRICT;

CREATE TABLE dwh_behavioral_ladder_profile (
    product_code  TEXT    NOT NULL,
    bucket_code   TEXT    NOT NULL REFERENCES dwh_dim_time_bucket(bucket_code),
    share_e6      INTEGER NOT NULL CHECK (share_e6 BETWEEN 0 AND 1000000),
    PRIMARY KEY (product_code, bucket_code)
) STRICT;

CREATE TABLE dwh_regulatory_parameter (
    param_code      TEXT PRIMARY KEY,
    param_value_e6  INTEGER NOT NULL,
    description     TEXT NOT NULL
) STRICT;

CREATE TABLE dwh_yield_curve_point (
    currency_code  TEXT    NOT NULL,
    bucket_code    TEXT    NOT NULL REFERENCES dwh_dim_time_bucket(bucket_code),
    zero_rate_e6   INTEGER NOT NULL,
    PRIMARY KEY (currency_code, bucket_code)
) STRICT;

-- Hecho 1: snapshot periódico. Grano = contrato x fecha x reexpresión.
CREATE TABLE dwh_fact_account_balance_snapshot (
    snapshot_date_key            INTEGER NOT NULL REFERENCES dwh_dim_date(date_key),
    contract_key                 INTEGER NOT NULL REFERENCES dwh_dim_contract(contract_key),
    restatement_seq              INTEGER NOT NULL,
    snapshot_asserted_at         TEXT    NOT NULL,
    partner_key                  INTEGER NOT NULL REFERENCES dwh_dim_partner(partner_key),
    product_key                  INTEGER NOT NULL REFERENCES dwh_dim_product(product_key),
    branch_key                   INTEGER NOT NULL REFERENCES dwh_dim_branch(branch_key),
    gl_account_key               INTEGER NOT NULL REFERENCES dwh_dim_gl_account(gl_account_key),
    risk_band_key                INTEGER NOT NULL REFERENCES dwh_dim_risk_band(risk_band_key),
    liquidity_treatment_key      INTEGER NOT NULL REFERENCES dwh_dim_liquidity_treatment(liquidity_treatment_key),
    currency_code                TEXT    NOT NULL,
    balance_orig_e4              INTEGER NOT NULL,
    balance_clp_e4               INTEGER NOT NULL,
    fx_rate_to_clp_e6            INTEGER NOT NULL,
    accrued_interest_clp_e4      INTEGER NOT NULL,
    contractual_rate_e6          INTEGER NOT NULL,
    regulatory_provision_clp_e4  INTEGER NOT NULL,
    collateral_value_clp_e4      INTEGER NOT NULL,
    net_exposure_clp_e4          INTEGER NOT NULL,
    market_value_clp_e4          INTEGER NOT NULL,
    undrawn_commitment_clp_e4    INTEGER NOT NULL,
    pending_debits_clp_e4        INTEGER NOT NULL,
    pending_credits_clp_e4       INTEGER NOT NULL,
    residual_maturity_days       INTEGER NULL,
    row_checksum                 TEXT    NOT NULL,
    PRIMARY KEY (snapshot_date_key, contract_key, restatement_seq)
) STRICT;

-- Hecho 2: grano transaccional contable (Kimball cap. 9, fig. 9.5).
CREATE TABLE dwh_fact_gl_transaction_postings (
    posting_id                  INTEGER PRIMARY KEY,     -- degenerada (linaje al ledger)
    journal_entry_id            INTEGER NOT NULL,        -- degenerada (drill-down al asiento)
    line_number                 INTEGER NOT NULL,
    posting_date_key            INTEGER NOT NULL REFERENCES dwh_dim_date(date_key),
    value_date_key              INTEGER NOT NULL REFERENCES dwh_dim_date(date_key),
    gl_account_key              INTEGER NOT NULL REFERENCES dwh_dim_gl_account(gl_account_key),
    contract_key                INTEGER NOT NULL,        -- 0 = sin contrato
    partner_key                 INTEGER NOT NULL,
    transaction_code            TEXT    NOT NULL,
    entry_kind                  TEXT    NOT NULL,
    transaction_timestamp_utc   TEXT    NOT NULL,
    transaction_timestamp_local TEXT    NOT NULL,
    local_cutoff_date           TEXT    NOT NULL,
    debit_amount_e4             INTEGER NOT NULL,
    credit_amount_e4            INTEGER NOT NULL,
    signed_amount_e4            INTEGER NOT NULL,        -- con signo del saldo normal de la cuenta
    balance_after_e4            INTEGER NOT NULL,
    account_sequence_number     INTEGER NOT NULL,
    is_final_daily_tx           INTEGER NOT NULL CHECK (is_final_daily_tx IN (0,1)),
    UNIQUE (gl_account_key, contract_key, account_sequence_number)
) STRICT;
CREATE INDEX ix_gl_fact_final ON dwh_fact_gl_transaction_postings(gl_account_key, contract_key, is_final_daily_tx, posting_date_key);

-- Hecho 3: flujos contractuales proyectados. Grano = posición x contrato x flujo x reexpresión.
CREATE TABLE dwh_fact_contractual_cashflows (
    position_date_key        INTEGER NOT NULL REFERENCES dwh_dim_date(date_key),
    contract_key             INTEGER NOT NULL REFERENCES dwh_dim_contract(contract_key),
    restatement_seq          INTEGER NOT NULL,
    cashflow_date_key        INTEGER NOT NULL,
    cashflow_type            TEXT    NOT NULL CHECK (cashflow_type IN ('PRINCIPAL','INTEREST','REPRICING_NOTIONAL')),
    partner_key              INTEGER NOT NULL,
    product_key              INTEGER NOT NULL,
    risk_band_key            INTEGER NOT NULL,
    liquidity_treatment_key  INTEGER NOT NULL,
    time_bucket_key          INTEGER NOT NULL REFERENCES dwh_dim_time_bucket(time_bucket_key),
    currency_code            TEXT    NOT NULL,
    direction                TEXT    NOT NULL CHECK (direction IN ('INFLOW','OUTFLOW')),
    principal_e4             INTEGER NOT NULL,
    interest_e4              INTEGER NOT NULL,
    total_e4                 INTEGER NOT NULL,
    days_to_cashflow         INTEGER NOT NULL,
    discount_factor_e8       INTEGER NOT NULL,
    present_value_clp_e4     INTEGER NOT NULL,
    include_in_liquidity     INTEGER NOT NULL,
    include_in_irrbb         INTEGER NOT NULL,
    PRIMARY KEY (position_date_key, contract_key, restatement_seq, cashflow_date_key, cashflow_type)
) STRICT;
CREATE INDEX ix_cf_position ON dwh_fact_contractual_cashflows(position_date_key, days_to_cashflow);
"""


def _schema_is_current(conn: sqlite3.Connection) -> bool:
    return conn.execute("PRAGMA user_version;").fetchone()[0] == SCHEMA_VERSION


def init_database(force_rebuild: bool = False, refresh: bool = True) -> None:
    """
    Crea el esquema si no existe (o si es de una versión anterior, en cuyo caso el
    archivo previo se conserva como respaldo), siembra el escenario y lleva el
    libro y el DWH al día de hoy. Es idempotente: llamarla en cada render de
    Streamlit sólo procesa los días faltantes.
    """
    if os.path.exists(DB_FILE):
        probe = get_connection()
        current = _schema_is_current(probe)
        probe.close()
        if force_rebuild or not current:
            backup = DB_FILE.replace(".db", f".v1_backup_{datetime.now():%Y%m%d%H%M%S}.db")
            os.replace(DB_FILE, backup)
            for suffix in ("-wal", "-shm"):
                if os.path.exists(DB_FILE + suffix):
                    os.remove(DB_FILE + suffix)

    conn = get_connection()
    try:
        if conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'oltp_journal_entry';").fetchone()[0] == 0:
            conn.executescript(DDL)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")
            _seed_reference_data(conn)
        if refresh:
            ensure_book_current(conn)
    finally:
        conn.close()


# =============================================================================
# 2. DATOS DE REFERENCIA: CUENTAS, CONTRAPARTES, PRODUCTOS, TRATAMIENTOS
# =============================================================================

# (account_id, code, name, class, normal, dmnec, cmned, is_principal)
LEDGER_ACCOUNTS = [
    (110101, "1.1.01.01", "Depósitos en Banco Central de Chile (cuenta LBTR)", "ASSET", "D", 0, 1, 1),
    (110201, "1.1.02.01", "Caja: billetes y monedas", "ASSET", "D", 0, 1, 1),
    (130101, "1.3.01.01", "Bonos de Tesorería BTP/BTU", "ASSET", "D", 0, 1, 1),
    (130201, "1.3.02.01", "Pagarés y bonos Banco Central (PDBC/BCP)", "ASSET", "D", 0, 1, 1),
    (130301, "1.3.03.01", "Bonos bancarios locales AA+", "ASSET", "D", 0, 1, 1),
    (130401, "1.3.04.01", "Bonos corporativos AAA", "ASSET", "D", 0, 1, 1),
    (130501, "1.3.05.01", "Acciones IPSA", "ASSET", "D", 0, 1, 1),
    (140101, "1.4.01.01", "Colocaciones comerciales", "ASSET", "D", 0, 1, 1),
    (140102, "1.4.01.02", "Líneas de crédito comerciales giradas", "ASSET", "D", 0, 1, 1),
    (140201, "1.4.02.01", "Colocaciones hipotecarias para vivienda", "ASSET", "D", 0, 1, 1),
    (140301, "1.4.03.01", "Colocaciones de consumo", "ASSET", "D", 0, 1, 1),
    (150101, "1.5.01.01", "Activo fijo, intangibles y otros activos", "ASSET", "D", 0, 1, 1),
    (199001, "1.9.90.01", "Partidas en tránsito cámara de compensación", "CLEARING", "D", 0, 0, 1),
    (210101, "2.1.01.01", "Cuentas corrientes empresas", "LIABILITY", "C", 1, 0, 1),
    (210201, "2.1.02.01", "Depósitos a la vista personas", "LIABILITY", "C", 1, 0, 1),
    (220101, "2.2.01.01", "Depósitos a plazo personas", "LIABILITY", "C", 1, 0, 1),
    (220201, "2.2.02.01", "Depósitos a plazo institucionales", "LIABILITY", "C", 1, 0, 1),
    (230101, "2.3.01.01", "Efectos de comercio", "LIABILITY", "C", 1, 0, 1),
    (230201, "2.3.02.01", "Obligaciones con bancos del exterior", "LIABILITY", "C", 1, 0, 1),
    (240101, "2.4.01.01", "Bonos senior largo plazo", "LIABILITY", "C", 1, 0, 1),
    (250101, "2.5.01.01", "Bonos subordinados", "LIABILITY", "C", 1, 0, 1),
    (260101, "2.6.01.01", "Otros pasivos", "LIABILITY", "C", 1, 0, 1),
    (310101, "3.1.01.01", "Capital pagado y reservas", "EQUITY", "C", 1, 0, 1),
    (510101, "5.1.01.01", "Gasto por intereses depósitos a plazo", "EXPENSE", "D", 0, 0, 0),
]

# (partner_id, rut, nombre, tipo, segmento, actividad, grupo_id, grupo_nombre, es_IF)
PARTNERS = [
    (1001, "76.123.456-7", "INVERSIONES SANTA ELENA S.A.", "PERSONA_JURIDICA", "CORPORATIVA", "SERV_FIN", 501, "GRUPO SANTA ELENA", 1),
    (1002, "77.890.123-4", "DISTRIBUIDORA CENTRAL SPA", "PERSONA_JURIDICA", "PYME", "COMERCIO", 502, "GRUPO DISTRIBUIDORA CENTRAL", 0),
    (1003, "15.678.901-2", "RODRIGO ALONSO ORDENES ARCE", "PERSONA_NATURAL", "BANCA_PERSONAS", "PROFESIONAL", 503, "HOGAR 1003", 0),
    (1004, "12.345.678-9", "MARIA EUGENIA SILVA ROJAS", "PERSONA_NATURAL", "BANCA_PERSONAS", "COMERCIO", 504, "HOGAR 1004", 0),
    (1005, "60.910.000-1", "BANCO CENTRAL DE CHILE", "INSTITUCIONAL", "SOBERANO", "BANCO_CENTRAL", 505, "SECTOR PUBLICO", 1),
    (1006, "60.805.000-0", "TESORERIA GENERAL DE LA REP.", "SOBERANO", "SOBERANO", "ESTADO", 505, "SECTOR PUBLICO", 0),
    (9000, "00.000.000-0", "CARTERA AGREGADA LIBRO ALM", "AGREGADO", "AGREGADO", "MULTIPLE", 900, "CARTERA AGREGADA", 0),
    (9001, "97.000.000-0", "BANCO (CUENTA PROPIA)", "PERSONA_JURIDICA", "PROPIA", "BANCA", 901, "BANCO", 1),
]

# (code, nombre, familia, lado, cartera CMF, devenga interés, es colocación)
PRODUCTS = [
    ("BCCH_RESERVA", "Reserva en Banco Central", "TESORERIA", "ASSET", "N/A", 0, 0),
    ("CAJA", "Caja billetes y monedas", "TESORERIA", "ASSET", "N/A", 0, 0),
    ("BONO_SOB", "Bonos de Tesorería BTP/BTU", "INVERSIONES", "ASSET", "N/A", 1, 0),
    ("BONO_BCCH", "Pagarés y bonos Banco Central", "INVERSIONES", "ASSET", "N/A", 1, 0),
    ("BONO_BANC", "Bonos bancarios AA+", "INVERSIONES", "ASSET", "N/A", 1, 0),
    ("BONO_CORP", "Bonos corporativos AAA", "INVERSIONES", "ASSET", "N/A", 1, 0),
    ("ACCION_IPSA", "Acciones IPSA", "INVERSIONES", "ASSET", "N/A", 0, 0),
    ("COM_CREDITO", "Crédito comercial", "COLOCACIONES", "ASSET", "C", 1, 1),
    ("COM_LINEA", "Línea de crédito comercial comprometida", "COLOCACIONES", "ASSET", "C", 1, 1),
    ("HIPOTECARIO", "Mutuo hipotecario vivienda", "COLOCACIONES", "ASSET", "H", 1, 1),
    ("CONSUMO", "Crédito de consumo", "COLOCACIONES", "ASSET", "N", 1, 1),
    ("OTROS_ACT", "Activo fijo, intangibles y otros", "OTROS", "ASSET", "N/A", 0, 0),
    ("TRANSITO", "Partidas en tránsito", "OTROS", "ASSET", "N/A", 0, 0),
    ("VISTA_PERS", "Depósitos a la vista personas", "CAPTACIONES", "LIABILITY", "N/A", 0, 0),
    ("DP_PERS", "Depósitos a plazo personas", "CAPTACIONES", "LIABILITY", "N/A", 1, 0),
    ("CTACTE_EMP", "Cuenta corriente empresas", "CAPTACIONES", "LIABILITY", "N/A", 0, 0),
    ("DP_INST", "Depósito a plazo institucional", "CAPTACIONES", "LIABILITY", "N/A", 1, 0),
    ("EFECTOS_COMERCIO", "Efectos de comercio", "FONDEO_MAYORISTA", "LIABILITY", "N/A", 1, 0),
    ("OBLIG_BANCOS_EXT", "Obligaciones con bancos del exterior", "FONDEO_MAYORISTA", "LIABILITY", "N/A", 1, 0),
    ("BONO_SENIOR", "Bonos senior", "FONDEO_MAYORISTA", "LIABILITY", "N/A", 1, 0),
    ("BONO_SUBORD", "Bonos subordinados", "FONDEO_MAYORISTA", "LIABILITY", "N/A", 1, 0),
    ("OTROS_PAS", "Otros pasivos", "OTROS", "LIABILITY", "N/A", 0, 0),
    ("CAPITAL", "Capital pagado y reservas", "PATRIMONIO", "EQUITY", "N/A", 0, 0),
    ("LINEA_NO_GIRADA", "Líneas comprometidas no giradas (contingente)", "CONTINGENTES", "OFF_BALANCE", "C", 0, 0),
]

BRANCHES = [
    ("CL-SCL-01", "Casa Matriz Santiago", "Santiago", "Metropolitana"),
    ("CL-SCL-02", "Mesa de Dinero", "Santiago", "Metropolitana"),
    ("CL-VAP-01", "Sucursal Valparaíso", "Valparaíso", "Valparaíso"),
    ("CL-CCP-01", "Sucursal Concepción", "Concepción", "Biobío"),
]

# Catálogo de minidimensión de riesgo (categorías usadas por el proyecto, A1..D2).
RISK_BANDS = [
    ("A1", "NORMAL", "0 DIAS", 0, 0, "0.001000", "<=60%"),
    ("A2", "NORMAL", "0 DIAS", 0, 0, "0.005000", "<=60%"),
    ("A3", "NORMAL", "1-30 DIAS", 1, 30, "0.010000", "60-80%"),
    ("B1", "SUBESTANDAR", "31-60 DIAS", 31, 60, "0.040000", "60-80%"),
    ("B2", "SUBESTANDAR", "61-90 DIAS", 61, 90, "0.100000", ">80%"),
    ("C1", "INCUMPLIMIENTO", ">90 DIAS", 91, 180, "0.250000", ">80%"),
    ("D1", "INCUMPLIMIENTO", ">90 DIAS", 181, 365, "0.600000", ">80%"),
    ("D2", "INCUMPLIMIENTO", ">90 DIAS", 366, 9999, "1.000000", ">80%"),
    ("NA", "NO_APLICA", "NO_APLICA", 0, 0, "0.000000", "NO_APLICA"),
]

# Tratamientos Basilea III (BCBS 238 / 295). Factores NSFR por banda de vencimiento residual.
# (code, lcr_component, lcr_category, hqla, haircut, basis, lcr_rate, undrawn_runoff,
#  nsfr_side, nsfr_category, f_lt6m, f_6m1y, f_ge1y, undrawn_rsf)
LIQUIDITY_TREATMENTS = [
    ("HQLA_L1_CAJA", "ALAC", "Nivel 1: Efectivo y BCCh", "L1", "0", "MARKET_VALUE", "1", "0",
     "RSF", "Efectivo y Reservas Centrales (0% RSF)", "0", "0", "0", "0"),
    ("HQLA_L1_BCCH", "ALAC", "Nivel 1: Efectivo y BCCh", "L1", "0", "MARKET_VALUE", "1", "0",
     "RSF", "Efectivo y Reservas Centrales (0% RSF)", "0", "0", "0", "0"),
    ("HQLA_L1_SOBERANO", "ALAC", "Nivel 1: Soberanos 0% Riesgo", "L1", "0", "MARKET_VALUE", "1", "0",
     "RSF", "Activos Líquidos Nivel 1 (5% RSF)", "0.05", "0.05", "0.05", "0"),
    ("HQLA_L2A", "ALAC", "Nivel 2A: Activos Alta Calidad", "L2A", "0.15", "MARKET_VALUE", "1", "0",
     "RSF", "Activos Líquidos Nivel 2A (15% RSF)", "0.15", "0.15", "0.15", "0"),
    ("HQLA_L2B", "ALAC", "Nivel 2B: Activos Menor Liquidez", "L2B", "0.50", "MARKET_VALUE", "1", "0",
     "RSF", "Activos Líquidos Nivel 2B (50% RSF)", "0.50", "0.50", "0.50", "0"),
    ("INFLOW_HIPOTECARIO", "INFLOW", "Entradas de Efectivo (Inflows con Cap 75%)", "NO_HQLA", "0", "CASHFLOW_30D", "0.50", "0",
     "RSF", "Créditos con Garantía Residencial (65% RSF)", "0.50", "0.50", "0.65", "0"),
    ("INFLOW_COMERCIAL", "INFLOW", "Entradas de Efectivo (Inflows con Cap 75%)", "NO_HQLA", "0", "CASHFLOW_30D", "0.50", "0",
     "RSF", "Créditos Comerciales y Corporativos (85% RSF)", "0.50", "0.50", "0.85", "0"),
    ("INFLOW_CONSUMO", "INFLOW", "Entradas de Efectivo (Inflows con Cap 75%)", "NO_HQLA", "0", "CASHFLOW_30D", "0.50", "0",
     "RSF", "Créditos de Consumo (50%-85% RSF)", "0.50", "0.50", "0.85", "0"),
    ("LINEA_CREDITO_CORP", "INFLOW", "Entradas de Efectivo (Inflows con Cap 75%)", "NO_HQLA", "0", "CASHFLOW_30D", "0.50", "0.10",
     "RSF", "Créditos Comerciales y Corporativos (85% RSF)", "0.50", "0.50", "0.85", "0.05"),
    ("LINEA_LIQUIDEZ_CORP", "NONE", "Compromisos de Liquidez y Crédito", "NO_HQLA", "0", "NONE", "0", "0.30",
     "NONE", "Líneas Comprometidas No Utilizadas (5% RSF)", "0", "0", "0", "0.05"),
    ("DEP_MINORISTA_ESTABLE", "OUTFLOW", "Depósitos Minoristas Estables", "NO_HQLA", "0", "BALANCE", "0.05", "0",
     "ASF", "Depósitos Minoristas Estables (95% ASF)", "0.95", "0.95", "0.95", "0"),
    ("DEP_MINORISTA_MENOS_ESTABLE", "OUTFLOW", "Depósitos Minoristas Menos Estables", "NO_HQLA", "0", "BALANCE", "0.10", "0",
     "ASF", "Depósitos Minoristas Menos Estables (90% ASF)", "0.90", "0.90", "0.90", "0"),
    ("DEP_MAYORISTA_OPERACIONAL", "OUTFLOW", "Depósitos Mayoristas Operacionales", "NO_HQLA", "0", "BALANCE", "0.25", "0",
     "ASF", "Fondeo Mayorista Operacional (50% ASF)", "0.50", "0.50", "0.50", "0"),
    ("DEP_MAYORISTA_NO_OPER_FIN", "OUTFLOW", "Depósitos Mayoristas No Operacionales", "NO_HQLA", "0", "CASHFLOW_30D", "1.00", "0",
     "ASF", "Fondeo Mayorista No Operacional (0%-100% ASF)", "0", "0.50", "1.00", "0"),
    ("FONDEO_MAYORISTA_NO_GARANTIZADO", "OUTFLOW", "Fondeo Mayorista No Garantizado", "NO_HQLA", "0", "CASHFLOW_30D", "1.00", "0",
     "ASF", "Fondeo Mayorista e Instrumentos de Deuda (0%-100% ASF)", "0", "0.50", "1.00", "0"),
    ("CAPITAL_REGULATORIO", "NONE", "No computable LCR", "NO_HQLA", "0", "NONE", "0", "0",
     "ASF", "Capital Regulatorio e Instrumentos > 1 Año", "1.00", "1.00", "1.00", "0"),
    ("OTROS_PASIVOS", "NONE", "No computable LCR", "NO_HQLA", "0", "NONE", "0", "0",
     "ASF", "Otros Pasivos (0% ASF)", "0", "0", "0", "0"),
    ("OTROS_ACTIVOS", "NONE", "No computable LCR", "NO_HQLA", "0", "NONE", "0", "0",
     "RSF", "Otros Activos (100% RSF)", "1.00", "1.00", "1.00", "0"),
]

# Bandas temporales CMF (C54 brechas / C56 IRRBB). (code, label, min, max, midpoint, nsfr_band)
TIME_BUCKETS = [
    ("B01", "1-7 días", 0, 7, 4, "LT_6M"),
    ("B02", "8-30 días", 8, 30, 19, "LT_6M"),
    ("B03", "31-90 días", 31, 90, 60, "LT_6M"),
    ("B04", "91-180 días", 91, 180, 135, "LT_6M"),
    ("B05", "181-365 días", 181, 365, 273, "6M_1Y"),
    ("B06", "1-3 años", 366, 1095, 730, "GE_1Y"),
    ("B07", "3-5 años", 1096, 1825, 1460, "GE_1Y"),
    ("B08", "> 5 años", 1826, 99999, 2920, "GE_1Y"),
]

# Perfiles de vencimiento contractual/conductual de las carteras agregadas (participación por banda).
LADDER_PROFILES = {
    "VISTA_PERS":       [0.03, 0.05, 0.07, 0.08, 0.12, 0.25, 0.20, 0.20],
    "DP_PERS":          [0.10, 0.20, 0.30, 0.20, 0.15, 0.05, 0.00, 0.00],
    "CTACTE_EMP":       [0.05, 0.10, 0.10, 0.10, 0.15, 0.25, 0.15, 0.10],
    "DP_INST":          [0.20, 0.30, 0.25, 0.10, 0.10, 0.05, 0.00, 0.00],
    "EFECTOS_COMERCIO": [0.15, 0.20, 0.35, 0.30, 0.00, 0.00, 0.00, 0.00],
    "OBLIG_BANCOS_EXT": [0.15, 0.25, 0.30, 0.20, 0.10, 0.00, 0.00, 0.00],
    "BONO_SENIOR":      [0.005, 0.015, 0.03, 0.05, 0.10, 0.30, 0.30, 0.20],
    "BONO_SUBORD":      [0.00, 0.00, 0.00, 0.00, 0.00, 0.10, 0.30, 0.60],
    "HIPOTECARIO":      [0.004, 0.011, 0.025, 0.035, 0.07, 0.18, 0.17, 0.505],
    "COM_CREDITO":      [0.03, 0.06, 0.12, 0.13, 0.16, 0.25, 0.15, 0.10],
    "CONSUMO":          [0.015, 0.04, 0.08, 0.11, 0.17, 0.35, 0.20, 0.035],
    "BONO_SOB":         [0.02, 0.03, 0.05, 0.10, 0.15, 0.25, 0.20, 0.20],
    "BONO_BCCH":        [0.10, 0.25, 0.30, 0.25, 0.10, 0.00, 0.00, 0.00],
    "BONO_BANC":        [0.01, 0.02, 0.05, 0.07, 0.15, 0.35, 0.25, 0.10],
    "BONO_CORP":        [0.01, 0.02, 0.05, 0.07, 0.15, 0.35, 0.25, 0.10],
}

REGULATORY_PARAMETERS = [
    ("CAPITAL_BASICO_CLP", "470000000000", "Capital básico (Tier 1) en CLP para límites de descalce C54 e IRRBB C56"),
    ("C54_LIMITE_DESCALCE_30D_X_CAPITAL", "1", "Descalce acumulado a 30 días <= 1 x capital básico"),
    ("C54_LIMITE_DESCALCE_90D_X_CAPITAL", "2", "Descalce acumulado a 90 días <= 2 x capital básico"),
    ("IRRBB_SHOCK_PARALLEL", "0.02", "Shock paralelo estándar (BCBS 368, calibrar por moneda)"),
    ("IRRBB_SHOCK_SHORT", "0.03", "Shock de corto plazo estándar"),
    ("IRRBB_SHOCK_LONG", "0.015", "Shock de largo plazo estándar"),
    ("IRRBB_OUTLIER_EVE_PCT_TIER1", "0.15", "Prueba de valor atípico: delta EVE / Tier 1 > 15%"),
]

YIELD_CURVE_CLP = {"B01": "0.0500", "B02": "0.0505", "B03": "0.0510", "B04": "0.0515",
                   "B05": "0.0525", "B06": "0.0545", "B07": "0.0560", "B08": "0.0590"}

# -----------------------------------------------------------------------------
# Libro ALM (carteras agregadas a escala bancaria, en MM$) + contratos nominados.
# Cada cartera es un "contrato" del core cuyo saldo vive en el ledger.
# (contract_id, número, producto, cuenta, contraparte, tratamiento, saldo base MM$,
#  tasa, sucursal, crecimiento anual, amplitud estacional, sensibilidad a estrés)
# -----------------------------------------------------------------------------
POOL_BOOK = [
    (10002, "POOL-CAJA",            "CAJA",             110201, 9000, "HQLA_L1_CAJA",                 65000,  "0",      "CL-SCL-01", 0.04, 0.050, 0.0),
    (70001, "POOL-BTP-BTU",         "BONO_SOB",         130101, 1006, "HQLA_L1_SOBERANO",             40000,  "0.0580", "CL-SCL-02", 0.03, 0.020, 0.0),
    (70002, "POOL-PDBC",            "BONO_BCCH",        130201, 1005, "HQLA_L1_SOBERANO",            120000,  "0.0500", "CL-SCL-02", 0.02, 0.060, 0.0),
    (72001, "POOL-BONOS-BANC-AA",   "BONO_BANC",        130301, 9000, "HQLA_L2A",                     95000,  "0.0610", "CL-SCL-02", 0.03, 0.030, 0.0),
    (72002, "POOL-BONOS-CORP-AAA",  "BONO_CORP",        130401, 9000, "HQLA_L2A",                     75000,  "0.0640", "CL-SCL-02", 0.03, 0.030, 0.0),
    (73001, "POOL-ACCIONES-IPSA",   "ACCION_IPSA",      130501, 9000, "HQLA_L2B",                     25000,  "0",      "CL-SCL-02", 0.05, 0.080, 0.0),
    (40001, "POOL-HIPOTECARIO",     "HIPOTECARIO",      140201, 9000, "INFLOW_HIPOTECARIO",          920000,  "0.0450", "CL-SCL-01", 0.07, 0.005, 0.0),
    (40002, "POOL-COMERCIAL",       "COM_CREDITO",      140101, 9000, "INFLOW_COMERCIAL",           2050000,  "0.0820", "CL-SCL-01", 0.06, 0.015, 0.0),
    (40003, "POOL-CONSUMO",         "CONSUMO",          140301, 9000, "INFLOW_CONSUMO",              340000,  "0.1650", "CL-SCL-01", 0.06, 0.020, 0.0),
    (50001, "POOL-OTROS-ACTIVOS",   "OTROS_ACT",        150101, 9001, "OTROS_ACTIVOS",               160000,  "0",      "CL-SCL-01", 0.02, 0.000, 0.0),
    (20001, "POOL-VISTA-PERS",      "VISTA_PERS",       210201, 9000, "DEP_MINORISTA_ESTABLE",       450000,  "0",      "CL-SCL-01", 0.06, 0.030, 0.02),
    (20002, "POOL-DP-PERS",         "DP_PERS",          220101, 9000, "DEP_MINORISTA_MENOS_ESTABLE", 320000,  "0.0480", "CL-SCL-01", 0.05, 0.020, 0.04),
    (20003, "POOL-CTACTE-EMP",      "CTACTE_EMP",       210101, 9000, "DEP_MAYORISTA_OPERACIONAL",   450000,  "0",      "CL-SCL-01", 0.05, 0.040, 0.06),
    (20004, "POOL-DP-INST",         "DP_INST",          220201, 9000, "DEP_MAYORISTA_NO_OPER_FIN",   950000,  "0.0540", "CL-SCL-02", 0.05, 0.030, 0.50),
    (20005, "POOL-EFECTOS-COMERCIO", "EFECTOS_COMERCIO", 230101, 9000, "FONDEO_MAYORISTA_NO_GARANTIZADO", 250000, "0.0560", "CL-SCL-02", 0.04, 0.030, 0.55),
    (20006, "POOL-BANCOS-EXTERIOR", "OBLIG_BANCOS_EXT", 230201, 9000, "FONDEO_MAYORISTA_NO_GARANTIZADO", 150000, "0.0610", "CL-SCL-02", 0.04, 0.020, 0.35),
    (20007, "POOL-BONOS-SENIOR",    "BONO_SENIOR",      240101, 9000, "FONDEO_MAYORISTA_NO_GARANTIZADO", 1250000, "0.0520", "CL-SCL-02", 0.05, 0.000, 0.0),
    (20008, "POOL-BONOS-SUBORD",    "BONO_SUBORD",      250101, 9000, "CAPITAL_REGULATORIO",         310000,  "0.0590", "CL-SCL-02", 0.03, 0.000, 0.0),
    (20009, "POOL-OTROS-PASIVOS",   "OTROS_PAS",        260101, 9001, "OTROS_PASIVOS",               100000,  "0",      "CL-SCL-01", 0.03, 0.050, 0.0),
    (31001, "CAPITAL-PAGADO",       "CAPITAL",          310101, 9001, "CAPITAL_REGULATORIO",         470000,  "0",      "CL-SCL-01", 0.06, 0.000, 0.0),
]

# Líneas comprometidas no giradas (fuera de balance): viven sólo en el core (MM$).
OFF_BALANCE_POOLS = [
    (60001, "POOL-LINEAS-CREDITO-CORP",  "LINEA_NO_GIRADA", "LINEA_CREDITO_CORP",  150000),
    (60002, "POOL-LINEAS-LIQUIDEZ-CORP", "LINEA_NO_GIRADA", "LINEA_LIQUIDEZ_CORP",  60000),
]

CASH_CONTRACT = 10001      # Reserva BCCh: cuenta caliente (hot account) contra la que liquida todo
TRANSIT_CONTRACT = 19901   # Partidas en tránsito cámara


def _seed_reference_data(conn: sqlite3.Connection) -> None:
    now = ts(utc_now())
    with atomic(conn):
        conn.executemany(
            """INSERT INTO oltp_ledger_account (account_id, account_code, account_name, account_class, normal_balance,
                   currency_code, debits_must_not_exceed_credits, credits_must_not_exceed_debits, is_contract_principal, created_at)
               VALUES (?, ?, ?, ?, ?, 'CLP', ?, ?, ?, ?);""",
            [(a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], now) for a in LEDGER_ACCOUNTS],
        )
        conn.executemany(
            """INSERT INTO dwh_dim_gl_account (account_id, account_code, account_name, account_class, normal_balance, is_contract_principal)
               VALUES (?, ?, ?, ?, ?, ?);""",
            [(a[0], a[1], a[2], a[3], a[4], a[7]) for a in LEDGER_ACCOUNTS],
        )
        conn.executemany(
            """INSERT INTO dwh_dim_partner (partner_id, tax_id, full_name, partner_type, segment_name, economic_activity,
                   household_group_id, household_group_name, is_financial_institution, is_current,
                   effective_start_date, effective_end_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, '2024-01-01', '9999-12-31');""",
            PARTNERS,
        )
        conn.executemany(
            """INSERT INTO dwh_dim_product (product_code, product_name, product_family, balance_side,
                   cmf_portfolio_type, is_interest_bearing, is_loan) VALUES (?, ?, ?, ?, ?, ?, ?);""",
            PRODUCTS,
        )
        conn.executemany(
            "INSERT INTO dwh_dim_branch (branch_code, branch_name, city_name, region_name) VALUES (?, ?, ?, ?);",
            BRANCHES,
        )
        conn.executemany(
            """INSERT INTO dwh_dim_risk_band (cmf_rating_category, regulatory_status, dpd_bucket, min_dpd, max_dpd,
                   provision_rate_e6, ltv_bucket) VALUES (?, ?, ?, ?, ?, ?, ?);""",
            [(r[0], r[1], r[2], r[3], r[4], f2i(r[5]), r[6]) for r in RISK_BANDS],
        )
        conn.executemany(
            """INSERT INTO dwh_dim_liquidity_treatment (treatment_code, lcr_component, lcr_category, hqla_level,
                   hqla_haircut_e6, lcr_basis, lcr_rate_e6, undrawn_runoff_rate_e6, nsfr_side, nsfr_category,
                   nsfr_factor_lt_6m_e6, nsfr_factor_6m_1y_e6, nsfr_factor_ge_1y_e6, undrawn_rsf_factor_e6)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
            [(t[0], t[1], t[2], t[3], f2i(t[4]), t[5], f2i(t[6]), f2i(t[7]), t[8], t[9],
              f2i(t[10]), f2i(t[11]), f2i(t[12]), f2i(t[13])) for t in LIQUIDITY_TREATMENTS],
        )
        conn.executemany(
            """INSERT INTO dwh_dim_time_bucket (bucket_code, bucket_label, min_days, max_days, midpoint_days,
                   nsfr_maturity_band, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?);""",
            [(b[0], b[1], b[2], b[3], b[4], b[5], i + 1) for i, b in enumerate(TIME_BUCKETS)],
        )
        for product_code, shares in LADDER_PROFILES.items():
            conn.executemany(
                "INSERT INTO dwh_behavioral_ladder_profile (product_code, bucket_code, share_e6) VALUES (?, ?, ?);",
                [(product_code, TIME_BUCKETS[i][0], f2i(str(s))) for i, s in enumerate(shares)],
            )
        conn.executemany(
            "INSERT INTO dwh_regulatory_parameter (param_code, param_value_e6, description) VALUES (?, ?, ?);",
            [(p[0], f2i(p[1]), p[2]) for p in REGULATORY_PARAMETERS],
        )
        conn.executemany(
            "INSERT INTO dwh_yield_curve_point (currency_code, bucket_code, zero_rate_e6) VALUES ('CLP', ?, ?);",
            [(k, f2i(v)) for k, v in YIELD_CURVE_CLP.items()],
        )
        _populate_dim_date(conn, date(2024, 1, 1), date(2027, 12, 31))
        conn.execute(
            "INSERT INTO stg_outbox_checkpoint (consumer_name, last_outbox_id, updated_at) VALUES ('ODS_BITEMPORAL', 0, ?);",
            (now,),
        )


CL_FIXED_HOLIDAYS = {(1, 1), (5, 1), (5, 21), (6, 29), (8, 15), (9, 18), (9, 19), (10, 12), (11, 1), (12, 8), (12, 25)}
MONTH_NAMES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
               "Septiembre", "Octubre", "Noviembre", "Diciembre"]
DAY_NAMES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def _populate_dim_date(conn: sqlite3.Connection, start: date, end: date) -> None:
    rows = []
    d = start
    epoch = date(2000, 1, 1)
    while d <= end:
        nxt = d + timedelta(days=1)
        rows.append((
            date_key(d), d.isoformat(), (d - epoch).days, d.year, (d.month - 1) // 3 + 1, d.month,
            MONTH_NAMES[d.month - 1], d.year * 100 + d.month, d.day, d.isoweekday(), DAY_NAMES[d.weekday()],
            1 if nxt.month != d.month else 0,
            1 if d.weekday() < 5 and (d.month, d.day) not in CL_FIXED_HOLIDAYS else 0,
            d.year,
        ))
        d = nxt
    conn.executemany(
        """INSERT OR IGNORE INTO dwh_dim_date (date_key, full_date, day_number_in_epoch, year, quarter, month_number,
               month_name, year_month, day_of_month, day_of_week, day_name, is_month_end, is_business_day, fiscal_year)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
        rows,
    )


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and (d.month, d.day) not in CL_FIXED_HOLIDAYS


# =============================================================================
# 3. CAPA OLTP: OPERACIÓN CONTABLE ATÓMICA NO INTERACTIVA
# =============================================================================

@dataclass
class _Line:
    line_number: int
    account_id: int
    contract_id: int
    partner_id: int
    currency_code: str
    amount_debit_e4: int
    amount_credit_e4: int
    functional_debit_e4: int
    functional_credit_e4: int
    fx_rate_e6: int
    released_debit_e4: int = 0
    released_credit_e4: int = 0


def _normalize_lines(lines: List[dict]) -> List[_Line]:
    out = []
    seen = set()
    for raw in lines:
        ln = int(raw["line_number"])
        if ln in seen:
            raise LedgerRejection("LINEA_DUPLICADA", f"line_number {ln} repetido.")
        seen.add(ln)
        debit = reg.to_money(raw.get("amount_debit", 0))
        credit = reg.to_money(raw.get("amount_credit", 0))
        if debit < 0 or credit < 0:
            raise LedgerRejection("MONTO_NEGATIVO", f"Línea {ln}: los montos deben ser >= 0.")
        if (debit > 0) == (credit > 0):
            raise LedgerRejection("LINEA_AMBIGUA", f"Línea {ln}: exactamente uno de debe/haber debe ser > 0.")
        fx = reg.to_factor(raw.get("fx_rate", "1"))
        if fx <= 0:
            raise LedgerRejection("FX_INVALIDO", f"Línea {ln}: tipo de cambio debe ser > 0.")
        out.append(_Line(
            line_number=ln,
            account_id=int(raw["account_id"]),
            contract_id=int(raw.get("contract_id") or 0),
            partner_id=int(raw["partner_id"]),
            currency_code=str(raw.get("currency_code", "CLP")),
            amount_debit_e4=m2i(debit),
            amount_credit_e4=m2i(credit),
            functional_debit_e4=m2i(debit * fx),
            functional_credit_e4=m2i(credit * fx),
            fx_rate_e6=f2i(fx),
        ))
    return sorted(out, key=lambda x: x.line_number)


def _row_hash(entry_uuid: str, ln: _Line, status: str) -> str:
    return sha256_hex("|".join(str(v) for v in (
        entry_uuid, ln.line_number, ln.account_id, ln.contract_id, ln.partner_id, ln.currency_code, status,
        ln.amount_debit_e4, ln.amount_credit_e4, ln.functional_debit_e4, ln.functional_credit_e4,
        ln.released_debit_e4, ln.released_credit_e4, ln.fx_rate_e6,
    )))


def _entry_hash(header: dict, row_hashes: List[str]) -> str:
    lines_digest = sha256_hex("".join(row_hashes))
    return sha256_hex("|".join(str(header[k]) for k in (
        "entry_uuid", "idempotency_key", "entry_kind", "pending_entry_id", "pending_expires_at",
        "transaction_timestamp_utc", "transaction_timestamp_local", "local_cutoff_date", "posting_date",
        "value_date", "source_system", "transaction_code", "description", "total_debit_e4",
        "total_credit_e4", "line_count", "reversal_of_entry_id", "prev_entry_hash",
    )) + "|" + lines_digest)


def _idempotency_lookup(conn, key: str, payload_hash: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT request_payload_hash, status, response_json FROM oltp_idempotency_records WHERE idempotency_key = ?;",
        (key,),
    ).fetchone()
    if row is None:
        return None
    if row["request_payload_hash"] != payload_hash:
        raise IdempotencyConflict(
            "IDEMPOTENCIA_PAYLOAD_DISTINTO",
            f"La clave {key} ya fue usada con otro payload; la solicitud no se procesa.",
        )
    response = json.loads(row["response_json"])
    response["replayed"] = True
    return response


def _record_failure(conn, key: str, payload_hash: str, operation: str, err: LedgerRejection, now: datetime) -> None:
    """Registra el rechazo determinista para que un reintento obtenga exactamente la misma respuesta."""
    response = {"status": "FAILED", "error_code": err.code, "error_message": err.message, "idempotency_key": key}
    with atomic(conn):
        if conn.execute("SELECT 1 FROM oltp_idempotency_records WHERE idempotency_key = ?;", (key,)).fetchone():
            return
        conn.execute(
            """INSERT INTO oltp_idempotency_records (idempotency_key, request_payload_hash, operation, status,
                   journal_entry_id, response_json, created_at, expires_at)
               VALUES (?, ?, ?, 'FAILED', NULL, ?, ?, ?);""",
            (key, payload_hash, operation, canonical_json(response), ts(now), ts(now + timedelta(days=IDEMPOTENCY_TTL_DAYS))),
        )


def _apply_balance_deltas(conn, lines: List[_Line], status: str, entry_id: int, now_s: str) -> None:
    """
    Actualiza saldos en orden determinista (account_id, contract_id) — el mismo
    orden de adquisición de locks en todas las transacciones evita deadlocks en
    cuentas calientes — y valida límites al estilo TigerBeetle:
      debits_must_not_exceed_credits : debits_pending + debits_posted <= credits_posted
      credits_must_not_exceed_debits : credits_pending + credits_posted <= debits_posted
    """
    deltas: Dict[Tuple[int, int], List[int]] = {}
    for ln in lines:
        k = (ln.account_id, ln.contract_id)
        d = deltas.setdefault(k, [0, 0, 0, 0])  # dp, cp, dpost, cpost
        if status == "PENDING":
            d[0] += ln.functional_debit_e4
            d[1] += ln.functional_credit_e4
        else:  # POSTED o resolución
            d[0] -= ln.released_debit_e4
            d[1] -= ln.released_credit_e4
            d[2] += ln.functional_debit_e4
            d[3] += ln.functional_credit_e4

    for (account_id, contract_id) in sorted(deltas):
        dp, cp, dpost, cpost = deltas[(account_id, contract_id)]
        acct = conn.execute(
            "SELECT account_name, debits_must_not_exceed_credits AS dmnec, credits_must_not_exceed_debits AS cmned "
            "FROM oltp_ledger_account WHERE account_id = ?;", (account_id,),
        ).fetchone()
        if acct is None:
            raise LedgerRejection("CUENTA_INEXISTENTE", f"La cuenta {account_id} no existe en el plan de cuentas.")
        conn.execute(
            "INSERT OR IGNORE INTO oltp_account_balance (account_id, contract_id, updated_at) VALUES (?, ?, ?);",
            (account_id, contract_id, now_s),
        )
        bal = conn.execute(
            "SELECT debits_pending_e4, credits_pending_e4, debits_posted_e4, credits_posted_e4 "
            "FROM oltp_account_balance WHERE account_id = ? AND contract_id = ?;",
            (account_id, contract_id),
        ).fetchone()
        n_dp = bal[0] + dp
        n_cp = bal[1] + cp
        n_dpost = bal[2] + dpost
        n_cpost = bal[3] + cpost
        if min(n_dp, n_cp) < 0:
            raise LedgerRejection("RESERVA_INCONSISTENTE", f"Cuenta {account_id}/{contract_id}: liberación mayor a la reserva.")
        if acct["dmnec"] and n_dp + n_dpost > n_cpost:
            raise LedgerRejection(
                "FONDOS_INSUFICIENTES",
                f"Cuenta {account_id} ({acct['account_name']}) contrato {contract_id}: débitos excederían créditos "
                f"(disponible {i2m(n_cpost - n_dpost - n_dp + dp):,.4f}).",
            )
        if acct["cmned"] and n_cp + n_cpost > n_dpost:
            raise LedgerRejection(
                "SOBREGIRO_NO_PERMITIDO",
                f"Cuenta {account_id} ({acct['account_name']}) contrato {contract_id}: créditos excederían débitos "
                f"(disponible {i2m(n_dpost - n_cpost - n_cp + cp):,.4f}).",
            )
        conn.execute(
            """UPDATE oltp_account_balance
               SET debits_pending_e4 = ?, credits_pending_e4 = ?, debits_posted_e4 = ?, credits_posted_e4 = ?,
                   last_journal_entry_id = ?, updated_at = ?
               WHERE account_id = ? AND contract_id = ?;""",
            (n_dp, n_cp, n_dpost, n_cpost, entry_id, now_s, account_id, contract_id),
        )


def _write_entry(conn, *, idempotency_key: str, payload_hash: str, operation: str, entry_kind: str,
                 lines: List[_Line], line_status: str, source_system: str, transaction_code: str,
                 description: str, value_date: Optional[date], now: datetime, created_by: str,
                 pending_entry_id: Optional[int] = None, pending_expires_at: Optional[datetime] = None,
                 reversal_of_entry_id: Optional[int] = None) -> dict:
    """Núcleo del 'stored procedure': debe ejecutarse dentro de `atomic(conn)`."""
    if len(lines) < 2:
        raise LedgerRejection("ASIENTO_INCOMPLETO", "Un asiento requiere al menos dos líneas.")
    total_debit = sum(ln.functional_debit_e4 for ln in lines)
    total_credit = sum(ln.functional_credit_e4 for ln in lines)
    if total_debit != total_credit:
        raise LedgerRejection(
            "DESCUADRE_CONTABLE",
            f"Debe {i2m(total_debit):,.4f} != Haber {i2m(total_credit):,.4f} (moneda funcional).",
        )
    if entry_kind in ("POSTED", "PENDING", "POST_PENDING") and total_debit == 0:
        raise LedgerRejection("ASIENTO_VACIO", "El asiento no mueve montos.")

    posting_d = local_cutoff_date(now)
    value_d = value_date or posting_d
    if value_d > posting_d:
        raise LedgerRejection("FECHA_VALOR_FUTURA", "La fecha valor no puede ser posterior a la fecha contable.")
    now_s = ts(now)
    prev = conn.execute("SELECT entry_hash FROM oltp_journal_entry ORDER BY journal_entry_id DESC LIMIT 1;").fetchone()
    header = {
        "entry_uuid": str(uuid.uuid4()),
        "idempotency_key": idempotency_key,
        "entry_kind": entry_kind,
        "pending_entry_id": pending_entry_id,
        "pending_expires_at": ts(pending_expires_at) if pending_expires_at else None,
        "transaction_timestamp_utc": now_s,
        "transaction_timestamp_local": ts(utc_to_local(now)),
        "local_cutoff_date": posting_d.isoformat(),
        "posting_date": posting_d.isoformat(),
        "value_date": value_d.isoformat(),
        "source_system": source_system,
        "transaction_code": transaction_code,
        "description": description,
        "total_debit_e4": total_debit,
        "total_credit_e4": total_credit,
        "line_count": len(lines),
        "reversal_of_entry_id": reversal_of_entry_id,
        "prev_entry_hash": prev["entry_hash"] if prev else ZERO_HASH,
    }
    row_hashes = [_row_hash(header["entry_uuid"], ln, line_status if ln.functional_debit_e4 + ln.functional_credit_e4 > 0 else "RELEASED")
                  for ln in lines]
    header["entry_hash"] = _entry_hash(header, row_hashes)

    cur = conn.execute(
        """INSERT INTO oltp_journal_entry (entry_uuid, idempotency_key, entry_kind, pending_entry_id, pending_expires_at,
               transaction_timestamp_utc, transaction_timestamp_local, local_cutoff_date, posting_date, value_date,
               source_system, transaction_code, description, total_debit_e4, total_credit_e4, line_count,
               reversal_of_entry_id, prev_entry_hash, entry_hash, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
        (header["entry_uuid"], idempotency_key, entry_kind, pending_entry_id, header["pending_expires_at"],
         now_s, header["transaction_timestamp_local"], header["local_cutoff_date"], header["posting_date"],
         header["value_date"], source_system, transaction_code, description, total_debit, total_credit,
         len(lines), reversal_of_entry_id, header["prev_entry_hash"], header["entry_hash"], created_by),
    )
    entry_id = cur.lastrowid
    conn.executemany(
        """INSERT INTO oltp_ledger_posting (journal_entry_id, line_number, account_id, contract_id, partner_id,
               currency_code, posting_status, amount_debit_e4, amount_credit_e4, functional_debit_e4,
               functional_credit_e4, functional_released_debit_e4, functional_released_credit_e4, fx_rate_e6, row_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
        [(entry_id, ln.line_number, ln.account_id, ln.contract_id, ln.partner_id, ln.currency_code,
          line_status if ln.functional_debit_e4 + ln.functional_credit_e4 > 0 else "RELEASED",
          ln.amount_debit_e4, ln.amount_credit_e4, ln.functional_debit_e4, ln.functional_credit_e4,
          ln.released_debit_e4, ln.released_credit_e4, ln.fx_rate_e6, rh)
         for ln, rh in zip(lines, row_hashes)],
    )
    _apply_balance_deltas(conn, lines, "PENDING" if line_status == "PENDING" else "POSTED", entry_id, now_s)

    # Verificación final dentro de la misma transacción (análogo a la validación del SP antes del COMMIT).
    if conn.execute("SELECT 1 FROM oltp_v_unbalanced_entries WHERE journal_entry_id = ?;", (entry_id,)).fetchone():
        raise LedgerRejection("DESCUADRE_CONTABLE", "El detalle persistido no cuadra con la cabecera.")

    response = {
        "status": "POSTED",
        "journal_entry_id": entry_id,
        "entry_uuid": header["entry_uuid"],
        "entry_kind": entry_kind,
        "entry_hash": header["entry_hash"],
        "posting_date": header["posting_date"],
        "value_date": header["value_date"],
        "idempotency_key": idempotency_key,
    }
    conn.execute(
        """INSERT INTO oltp_transactional_outbox (event_uuid, aggregate_type, aggregate_id, event_type, payload_json,
               journal_entry_id, created_at) VALUES (?, 'JOURNAL_ENTRY', ?, ?, ?, ?, ?);""",
        (str(uuid.uuid4()), str(entry_id), f"LEDGER.{entry_kind}",
         canonical_json({**response, "transaction_code": transaction_code, "source_system": source_system,
                         "lines": [{"line": ln.line_number, "account_id": ln.account_id, "contract_id": ln.contract_id,
                                    "debit_e4": ln.functional_debit_e4, "credit_e4": ln.functional_credit_e4,
                                    "released_debit_e4": ln.released_debit_e4,
                                    "released_credit_e4": ln.released_credit_e4} for ln in lines]}),
         entry_id, now_s),
    )
    conn.execute(
        """INSERT INTO oltp_idempotency_records (idempotency_key, request_payload_hash, operation, status,
               journal_entry_id, response_json, created_at, expires_at)
           VALUES (?, ?, ?, 'POSTED', ?, ?, ?, ?);""",
        (idempotency_key, payload_hash, operation, entry_id, canonical_json(response), now_s,
         ts(now + timedelta(days=IDEMPOTENCY_TTL_DAYS))),
    )
    response["replayed"] = False
    return response


def post_journal_entry(idempotency_key: str, source_system: str, transaction_code: str, description: str,
                       lines: List[dict], value_date: Optional[date] = None, phase: str = "POSTED",
                       pending_timeout: Optional[timedelta] = None, created_by: str = "SYSTEM",
                       now: Optional[datetime] = None, conn: Optional[sqlite3.Connection] = None) -> dict:
    """
    Contabiliza un asiento multi-pata en UNA transacción no interactiva.

    - `phase='POSTED'`  : liquidación inmediata (saldos posted).
    - `phase='PENDING'` : reserva de fondos de dos fases (saldos pending) que
                          vence en `pending_timeout` y se resuelve con
                          `resolve_pending_entry` o `expire_pending_entries`.
    Un reintento con la misma `idempotency_key` y el mismo payload devuelve la
    respuesta original (`replayed=True`) sin volver a contabilizar.
    """
    if phase not in ("POSTED", "PENDING"):
        raise ValueError("phase debe ser POSTED o PENDING")
    own = conn is None
    conn = conn or get_connection()
    now = now or utc_now()
    try:
        norm = _normalize_lines(lines)
        payload = {
            "op": "POST_JOURNAL_ENTRY", "phase": phase, "source": source_system, "code": transaction_code,
            "description": description, "value_date": value_date.isoformat() if value_date else None,
            "timeout_s": int(pending_timeout.total_seconds()) if pending_timeout else None,
            "lines": [ln.__dict__ for ln in norm],
        }
        payload_hash = sha256_hex(canonical_json(payload))
        if phase == "PENDING" and not pending_timeout:
            raise LedgerRejection("TIMEOUT_REQUERIDO", "Una reserva PENDING requiere pending_timeout.")
        try:
            with atomic(conn):
                replay = _idempotency_lookup(conn, idempotency_key, payload_hash)
                if replay is not None:
                    return replay
                return _write_entry(
                    conn, idempotency_key=idempotency_key, payload_hash=payload_hash,
                    operation="POST_JOURNAL_ENTRY", entry_kind=phase, lines=norm, line_status=phase,
                    source_system=source_system, transaction_code=transaction_code, description=description,
                    value_date=value_date, now=now, created_by=created_by,
                    pending_expires_at=(now + pending_timeout) if phase == "PENDING" else None,
                )
        except IdempotencyConflict:
            raise
        except LedgerRejection as err:
            _record_failure(conn, idempotency_key, payload_hash, "POST_JOURNAL_ENTRY", err, now)
            raise
    finally:
        if own:
            conn.close()


def resolve_pending_entry(idempotency_key: str, pending_entry_id: int, action: str,
                          post_amounts: Optional[Dict[int, Decimal]] = None, description: Optional[str] = None,
                          now: Optional[datetime] = None, conn: Optional[sqlite3.Connection] = None,
                          _expire: bool = False) -> dict:
    """
    Resuelve una reserva de dos fases:
      - POST: liquida el monto (total o parcial por línea vía `post_amounts`);
              lo no liquidado se libera.
      - VOID: libera la reserva completa.
    La unicidad de `pending_entry_id` en el diario garantiza una sola resolución.
    """
    if action not in ("POST", "VOID"):
        raise ValueError("action debe ser POST o VOID")
    own = conn is None
    conn = conn or get_connection()
    now = now or utc_now()
    kind = "EXPIRE_PENDING" if _expire else ("POST_PENDING" if action == "POST" else "VOID_PENDING")
    payload = {"op": kind, "pending_entry_id": int(pending_entry_id),
               "post_amounts": {str(k): str(reg.to_money(v)) for k, v in (post_amounts or {}).items()}}
    payload_hash = sha256_hex(canonical_json(payload))
    try:
        try:
            with atomic(conn):
                replay = _idempotency_lookup(conn, idempotency_key, payload_hash)
                if replay is not None:
                    return replay
                pend = conn.execute(
                    "SELECT * FROM oltp_journal_entry WHERE journal_entry_id = ?;", (pending_entry_id,)
                ).fetchone()
                if pend is None or pend["entry_kind"] != "PENDING":
                    raise LedgerRejection("RESERVA_INEXISTENTE", f"El asiento {pending_entry_id} no es una reserva PENDING.")
                if conn.execute("SELECT 1 FROM oltp_journal_entry WHERE pending_entry_id = ?;", (pending_entry_id,)).fetchone():
                    raise LedgerRejection("RESERVA_YA_RESUELTA", f"La reserva {pending_entry_id} ya fue resuelta.")
                if action == "POST" and not _expire and ts(now) >= pend["pending_expires_at"]:
                    raise LedgerRejection("RESERVA_EXPIRADA", f"La reserva {pending_entry_id} expiró; no puede liquidarse.")
                originals = conn.execute(
                    "SELECT * FROM oltp_ledger_posting WHERE journal_entry_id = ? ORDER BY line_number;", (pending_entry_id,)
                ).fetchall()
                lines = []
                for p in originals:
                    pend_amt = p["functional_debit_e4"] + p["functional_credit_e4"]
                    if action == "POST":
                        req = post_amounts.get(p["line_number"]) if post_amounts else None
                        posted = m2i(req) if req is not None else pend_amt
                    else:
                        posted = 0
                    if posted > pend_amt or posted < 0:
                        raise LedgerRejection("MONTO_POST_INVALIDO", f"Línea {p['line_number']}: el monto liquidado excede la reserva.")
                    is_debit = p["functional_debit_e4"] > 0
                    fx = p["fx_rate_e6"]
                    orig_posted = int((Decimal(posted) * E6 / Decimal(fx)).to_integral_value(rounding=ROUND_HALF_EVEN))
                    lines.append(_Line(
                        line_number=p["line_number"], account_id=p["account_id"], contract_id=p["contract_id"],
                        partner_id=p["partner_id"], currency_code=p["currency_code"],
                        amount_debit_e4=orig_posted if is_debit else 0,
                        amount_credit_e4=0 if is_debit else orig_posted,
                        functional_debit_e4=posted if is_debit else 0,
                        functional_credit_e4=0 if is_debit else posted,
                        fx_rate_e6=fx,
                        released_debit_e4=pend_amt if is_debit else 0,
                        released_credit_e4=0 if is_debit else pend_amt,
                    ))
                verb = {"POST_PENDING": "Liquidación", "VOID_PENDING": "Anulación", "EXPIRE_PENDING": "Expiración"}[kind]
                return _write_entry(
                    conn, idempotency_key=idempotency_key, payload_hash=payload_hash, operation=kind,
                    entry_kind=kind, lines=lines, line_status="POSTED", source_system=pend["source_system"],
                    transaction_code=f"{pend['transaction_code']}_{kind}",
                    description=description or f"{verb} de reserva #{pending_entry_id}: {pend['description']}",
                    value_date=None, now=now, created_by="SYSTEM", pending_entry_id=pending_entry_id,
                )
        except IdempotencyConflict:
            raise
        except LedgerRejection as err:
            _record_failure(conn, idempotency_key, payload_hash, kind, err, now)
            raise
    finally:
        if own:
            conn.close()


def expire_pending_entries(now: Optional[datetime] = None, conn: Optional[sqlite3.Connection] = None) -> List[dict]:
    """Job de expiración: libera toda reserva vencida y no resuelta (idempotente por clave EXPIRE-<id>)."""
    own = conn is None
    conn = conn or get_connection()
    now = now or utc_now()
    try:
        overdue = conn.execute(
            """SELECT j.journal_entry_id FROM oltp_journal_entry j
               WHERE j.entry_kind = 'PENDING' AND j.pending_expires_at <= ?
                 AND NOT EXISTS (SELECT 1 FROM oltp_journal_entry r WHERE r.pending_entry_id = j.journal_entry_id)
               ORDER BY j.journal_entry_id;""",
            (ts(now),),
        ).fetchall()
        return [resolve_pending_entry(f"EXPIRE-{r[0]}", r[0], "VOID", now=now, conn=conn, _expire=True) for r in overdue]
    finally:
        if own:
            conn.close()


def reverse_journal_entry(idempotency_key: str, original_entry_id: int, reason: str,
                          now: Optional[datetime] = None, conn: Optional[sqlite3.Connection] = None) -> dict:
    """Reverso compensatorio append-only (nunca UPDATE/DELETE sobre el asiento original)."""
    own = conn is None
    conn = conn or get_connection()
    try:
        orig = conn.execute("SELECT * FROM oltp_journal_entry WHERE journal_entry_id = ?;", (original_entry_id,)).fetchone()
        if orig is None or orig["entry_kind"] not in ("POSTED", "POST_PENDING"):
            raise LedgerRejection("REVERSO_INVALIDO", "Sólo se revierten asientos liquidados.")
        rows = conn.execute("SELECT * FROM oltp_ledger_posting WHERE journal_entry_id = ? ORDER BY line_number;",
                            (original_entry_id,)).fetchall()
        lines = [{
            "line_number": r["line_number"], "account_id": r["account_id"], "contract_id": r["contract_id"],
            "partner_id": r["partner_id"], "currency_code": r["currency_code"],
            "amount_debit": i2m(r["amount_credit_e4"]), "amount_credit": i2m(r["amount_debit_e4"]),
            "fx_rate": i2f(r["fx_rate_e6"]),
        } for r in rows if r["amount_debit_e4"] + r["amount_credit_e4"] > 0]
        return post_journal_entry(idempotency_key, orig["source_system"], "REVERSO_CONTABLE",
                                  f"Reverso asiento #{original_entry_id}: {reason}", lines, now=now, conn=conn)
    finally:
        if own:
            conn.close()


def verify_ledger_cryptographic_chain(conn: Optional[sqlite3.Connection] = None):
    """
    Verificación forense completa: (1) recalcula el hash de cada fila de detalle,
    (2) recalcula el hash de cada cabecera a partir de sus campos y del digest de
    sus filas, y (3) comprueba el encadenamiento con el bloque anterior.
    Detecta tanto la ruptura de la cadena como la alteración de cualquier monto.
    """
    own = conn is None
    conn = conn or get_connection()
    try:
        entries = conn.execute("SELECT * FROM oltp_journal_entry ORDER BY journal_entry_id;").fetchall()
        postings = conn.execute("SELECT * FROM oltp_ledger_posting ORDER BY journal_entry_id, line_number;").fetchall()
    finally:
        if own:
            conn.close()
    by_entry: Dict[int, list] = {}
    for p in postings:
        by_entry.setdefault(p["journal_entry_id"], []).append(p)

    results = []
    ok_all = True
    expected_prev = ZERO_HASH
    for e in entries:
        row_hashes, rows_ok = [], True
        for p in by_entry.get(e["journal_entry_id"], []):
            ln = _Line(p["line_number"], p["account_id"], p["contract_id"], p["partner_id"], p["currency_code"],
                       p["amount_debit_e4"], p["amount_credit_e4"], p["functional_debit_e4"], p["functional_credit_e4"],
                       p["fx_rate_e6"], p["functional_released_debit_e4"], p["functional_released_credit_e4"])
            rh = _row_hash(e["entry_uuid"], ln, p["posting_status"])
            rows_ok &= rh == p["row_hash"]
            row_hashes.append(p["row_hash"])
        recomputed = _entry_hash(dict(e), row_hashes)
        link_ok = e["prev_entry_hash"] == expected_prev
        hash_ok = recomputed == e["entry_hash"]
        status = "VÁLIDO" if (link_ok and hash_ok and rows_ok) else (
            "CADENA_ROTA" if not link_ok else ("DETALLE_ALTERADO" if not rows_ok else "CABECERA_ALTERADA"))
        ok_all &= status == "VÁLIDO"
        results.append({
            "entry_id": e["journal_entry_id"],
            "code": e["transaction_code"],
            "kind": e["entry_kind"],
            "prev_hash_actual": e["prev_entry_hash"][:12] + "...",
            "expected_prev": expected_prev[:12] + "...",
            "hash_recalculado": recomputed[:12] + "...",
            "status": status,
        })
        expected_prev = e["entry_hash"]
    return ok_all, pd.DataFrame(results)


def run_outbox_relay(consumer_name: str = "ODS_BITEMPORAL", batch_size: int = 500,
                     conn: Optional[sqlite3.Connection] = None) -> int:
    """
    Relay del Transactional Outbox (en producción: CDC sobre oltp.transactional_outbox).
    Entrega at-least-once + inbox con clave única => procesamiento effectively-once.
    """
    own = conn is None
    conn = conn or get_connection()
    try:
        with atomic(conn):
            last = conn.execute("SELECT last_outbox_id FROM stg_outbox_checkpoint WHERE consumer_name = ?;",
                                (consumer_name,)).fetchone()
            last_id = last[0] if last else 0
            events = conn.execute(
                "SELECT outbox_id, event_uuid, event_type FROM oltp_transactional_outbox WHERE outbox_id > ? "
                "ORDER BY outbox_id LIMIT ?;", (last_id, batch_size),
            ).fetchall()
            now_s = ts(utc_now())
            conn.executemany(
                "INSERT OR IGNORE INTO stg_event_inbox (consumer_name, event_uuid, outbox_id, event_type, received_at) "
                "VALUES (?, ?, ?, ?, ?);",
                [(consumer_name, e["event_uuid"], e["outbox_id"], e["event_type"], now_s) for e in events],
            )
            if events:
                conn.execute(
                    "INSERT INTO stg_outbox_checkpoint (consumer_name, last_outbox_id, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(consumer_name) DO UPDATE SET last_outbox_id = excluded.last_outbox_id, updated_at = excluded.updated_at;",
                    (consumer_name, events[-1]["outbox_id"], now_s),
                )
            return len(events)
    finally:
        if own:
            conn.close()


# =============================================================================
# 4. CAPA CORE BITEMPORAL (ASSERTED VERSIONING FRAMEWORK)
# =============================================================================

CORE_VALUE_COLUMNS = [
    "partner_id", "contract_number", "product_code", "branch_code", "currency_code", "gl_account_id",
    "origination_date", "maturity_date", "original_amount_e4", "current_balance_e4", "committed_limit_e4",
    "undrawn_commitment_e4", "annual_nominal_rate_e6", "spread_rate_e6", "repricing_frequency",
    "next_repricing_date", "payment_frequency_months", "amortization_type", "cmf_portfolio_type", "cmf_rating",
    "days_past_due", "collateral_value_e4", "liquidity_treatment_code", "contract_status",
    "balance_maintained_in_core",
]


def _valid_ts(d) -> str:
    if isinstance(d, str):
        if len(d) == 10:
            return f"{d} 00:00:00.00"
        return d if len(d) > 19 else f"{d[:19]}.00"
    if isinstance(d, datetime):
        return ts(d)
    return f"{d.isoformat()} 00:00:00.00"


def _insert_core_row(conn, contract_id: int, values: dict, valid_from: str, valid_to: str,
                     asserted_from: str, change_type: str, reason: Optional[str],
                     source_entry: Optional[int], now: datetime) -> None:
    cols = ["contract_id"] + CORE_VALUE_COLUMNS + [
        "valid_from", "valid_to", "asserted_from", "asserted_to", "change_type", "change_reason",
        "source_journal_entry_id", "row_created_at"]
    vals = [contract_id] + [values.get(c) for c in CORE_VALUE_COLUMNS] + [
        valid_from, valid_to, asserted_from, END_OF_TIME, change_type, reason, source_entry, ts(now)]
    conn.execute(f"INSERT INTO core_contract_profile ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))});", vals)


def bitemporal_insert(contract_id: int, values: dict, valid_from, valid_to: str = END_OF_TIME,
                      asserted_from: Optional[datetime] = None, reason: str = "Alta de contrato",
                      now: Optional[datetime] = None, conn: Optional[sqlite3.Connection] = None) -> None:
    """Bitemporal Insert: alta de un contrato cuyo primer estado efectivo comienza en `valid_from`."""
    own = conn is None
    conn = conn or get_connection()
    now = now or utc_now()
    a_from = asserted_from or now
    if a_from < now:
        raise BitemporalViolation("AVF: no se puede asertar en el pasado (tiempo asertivo >= ahora).")
    change_type = "FUTURE_ASSERTION" if a_from > now else "INSERT"
    try:
        with atomic(conn):
            if conn.execute("SELECT 1 FROM core_contract_profile WHERE contract_id = ? AND asserted_to > ?;",
                            (contract_id, ts(a_from))).fetchone():
                raise BitemporalViolation(f"AVF: el contrato {contract_id} ya existe; use update o correction.")
            _insert_core_row(conn, contract_id, values, _valid_ts(valid_from), valid_to, ts(a_from),
                             change_type, reason, None, now)
    finally:
        if own:
            conn.close()


def _apply_change(conn, contract_id: int, eff_from: str, eff_to: str, changes: dict, asserted_from: datetime,
                  change_type: str, reason: str, source_entry: Optional[int], now: datetime) -> int:
    """
    Algoritmo AVF de "retiro y re-aserción" sobre el rango efectivo [eff_from, eff_to):
      1. Se cierran (retiran) las aserciones vigentes que se solapan con el rango,
         fijando asserted_to = asserted_from de la nueva aserción.
      2. Se re-asertan los remanentes izquierdo/derecho con los valores previos
         (CARRY_FORWARD) y el tramo cubierto con los valores nuevos.
    Nada se borra; la versión retirada sigue consultable "tal como se sabía".
    """
    unknown = set(changes) - set(CORE_VALUE_COLUMNS)
    if unknown:
        raise BitemporalViolation(f"Columnas no versionables: {sorted(unknown)}")
    a_s = ts(asserted_from)
    if conn.execute("SELECT 1 FROM core_contract_profile WHERE contract_id = ? AND asserted_from > ?;",
                    (contract_id, ts(now))).fetchone():
        raise BitemporalViolation(f"AVF: el contrato {contract_id} tiene una aserción diferida pendiente; "
                                  "aplíquela o retírela antes de un nuevo cambio.")
    rows = conn.execute(
        """SELECT * FROM core_contract_profile
           WHERE contract_id = ? AND asserted_from <= ? AND asserted_to > ?
             AND valid_from < ? AND valid_to > ?
           ORDER BY valid_from;""",
        (contract_id, a_s, a_s, eff_to, eff_from),
    ).fetchall()
    if not rows:
        raise BitemporalViolation(f"AVF: no hay versión vigente del contrato {contract_id} en el rango efectivo.")
    for r in rows:
        conn.execute(
            "UPDATE core_contract_profile SET asserted_to = ? WHERE contract_id = ? AND valid_from = ? AND asserted_from = ?;",
            (a_s, contract_id, r["valid_from"], r["asserted_from"]),
        )
    for r in rows:
        old = {c: r[c] for c in CORE_VALUE_COLUMNS}
        if r["valid_from"] < eff_from:
            _insert_core_row(conn, contract_id, old, r["valid_from"], eff_from, a_s, "CARRY_FORWARD",
                             f"Remanente previo a {eff_from[:10]}", None, now)
        new_vals = {**old, **changes}
        _insert_core_row(conn, contract_id, new_vals, max(r["valid_from"], eff_from), min(r["valid_to"], eff_to),
                         a_s, change_type, reason, source_entry, now)
        if r["valid_to"] > eff_to:
            _insert_core_row(conn, contract_id, old, eff_to, r["valid_to"], a_s, "CARRY_FORWARD",
                             f"Remanente posterior a {eff_to[:10]}", None, now)
    return len(rows)


def bitemporal_update(contract_id: int, effective_from, changes: dict, reason: str,
                      effective_to: str = END_OF_TIME, source_journal_entry_id: Optional[int] = None,
                      now: Optional[datetime] = None, conn: Optional[sqlite3.Connection] = None) -> int:
    """Bitemporal Update: un cambio REAL del negocio desde hoy o una fecha futura (no retroactivo)."""
    own = conn is None
    conn = conn or get_connection()
    now = now or utc_now()
    eff = _valid_ts(effective_from)
    if eff[:10] < utc_to_local(now).date().isoformat():
        raise BitemporalViolation("AVF: un update no puede ser retroactivo; use bitemporal_correction.")
    try:
        with atomic(conn):
            return _apply_change(conn, contract_id, eff, effective_to, changes, now, "UPDATE", reason,
                                 source_journal_entry_id, now)
    finally:
        if own:
            conn.close()


def bitemporal_correction(contract_id: int, effective_from, changes: dict, reason: str,
                          source_journal_entry_id: int, effective_to: str = END_OF_TIME,
                          now: Optional[datetime] = None, conn: Optional[sqlite3.Connection] = None) -> int:
    """
    Bitemporal Correction: la creencia previa sobre un período PASADO era errónea.
    Exige el asiento de ajuste del ledger que la respalda (trazabilidad contable).
    """
    own = conn is None
    conn = conn or get_connection()
    now = now or utc_now()
    eff = _valid_ts(effective_from)
    if eff[:10] >= utc_to_local(now).date().isoformat():
        raise BitemporalViolation("AVF: una corrección debe ser retroactiva; use bitemporal_update.")
    try:
        with atomic(conn):
            if not conn.execute("SELECT 1 FROM oltp_journal_entry WHERE journal_entry_id = ?;",
                                (source_journal_entry_id,)).fetchone():
                raise BitemporalViolation("AVF: la corrección debe referenciar un asiento existente del ledger.")
            return _apply_change(conn, contract_id, eff, effective_to, changes, now, "CORRECTION", reason,
                                 source_journal_entry_id, now)
    finally:
        if own:
            conn.close()


def bitemporal_future_assertion(contract_id: int, effective_from, changes: dict, cutover: datetime, reason: str,
                                effective_to: str = END_OF_TIME, now: Optional[datetime] = None,
                                conn: Optional[sqlite3.Connection] = None) -> int:
    """
    Future (deferred) Assertion: se carga hoy, en horario hábil, una versión cuya
    aserción comienza en `cutover`. Antes de ese instante es invisible para toda
    consulta "as of now"; al cruzarlo queda vigente sin jobs ni ventana de mantención.
    """
    own = conn is None
    conn = conn or get_connection()
    now = now or utc_now()
    if cutover <= now:
        raise BitemporalViolation("AVF: el corte de una aserción futura debe estar en el futuro.")
    try:
        with atomic(conn):
            return _apply_change(conn, contract_id, _valid_ts(effective_from), effective_to, changes, cutover,
                                 "FUTURE_ASSERTION", reason, None, now)
    finally:
        if own:
            conn.close()


def _core_display_frame(df: pd.DataFrame) -> pd.DataFrame:
    for col in [c for c in df.columns if c.endswith("_e4")]:
        df[col[:-3]] = df[col].astype("int64") / E4
    for col in [c for c in df.columns if c.endswith("_e6")]:
        df[col[:-3]] = df[col].astype("int64") / E6
    return df


def query_bitemporal_time_travel(business_date_str: str, system_time_str: str,
                                 conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """
    Time travel bitemporal parametrizado (sin interpolación de strings):
      tiempo efectivo  : valid_from <= :business < valid_to
      tiempo asertivo  : asserted_from <= :assertion < asserted_to
    `system_time_str` es UTC; `business_date_str` es fecha/hora de negocio local.
    """
    own = conn is None
    conn = conn or get_connection()
    try:
        df = pd.read_sql_query(
            """SELECT c.contract_id, c.contract_number, c.partner_id, p.tax_id AS partner_rut, p.full_name AS partner_name,
                      c.product_code AS product_type, c.currency_code, c.current_balance_e4, c.undrawn_commitment_e4,
                      c.annual_nominal_rate_e6, c.cmf_rating, c.contract_status, c.valid_from, c.valid_to,
                      c.asserted_from, c.asserted_to, c.change_type, c.change_reason, c.source_journal_entry_id
               FROM core_contract_profile c
               LEFT JOIN dwh_dim_partner p ON p.partner_id = c.partner_id AND p.is_current = 1
               WHERE c.valid_from <= :biz AND :biz < c.valid_to
                 AND c.asserted_from <= :sys AND :sys < c.asserted_to
                 AND c.contract_number NOT LIKE 'POOL-%'
                 AND c.product_code NOT IN ('BCCH_RESERVA', 'TRANSITO', 'CAPITAL', 'CTACTE_EMP')
               ORDER BY c.contract_id;""",
            conn, params={"biz": _valid_ts(business_date_str), "sys": _valid_ts(system_time_str)},
        )
    finally:
        if own:
            conn.close()
    return _core_display_frame(df)


def get_contract_bitemporal_history(contract_id: int, conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    own = conn is None
    conn = conn or get_connection()
    try:
        df = pd.read_sql_query(
            """SELECT contract_id, contract_number, current_balance_e4, annual_nominal_rate_e6, contract_status,
                      valid_from, valid_to, asserted_from, asserted_to, change_type, change_reason, source_journal_entry_id
               FROM core_contract_profile WHERE contract_id = ? ORDER BY asserted_from, valid_from;""",
            conn, params=(contract_id,),
        )
    finally:
        if own:
            conn.close()
    return _core_display_frame(df)


# =============================================================================
# 5. ESCENARIO: LIBRO ALM + CASOS REALES (sembrado vía las APIs anteriores)
# =============================================================================

def _pool_target_clp(pool: tuple, d: date) -> Decimal:
    """
    Saldo objetivo determinista de una cartera agregada en la fecha `d` (CLP).
    Tendencia de crecimiento + estacionalidad + episodio de estrés de fondeo
    mayorista (mayo-junio 2025 y marzo 2026) + ruido pseudoaleatorio reproducible.
    """
    cid, _, _, _, _, _, base_mm, _, _, growth, season_amp, stress_sens = pool
    t = (d - BOOK_START).days
    trend = 1.0 + growth * t / 365.0
    seasonal = 1.0 + season_amp * math.sin(2 * math.pi * (t / 30.4 + (cid % 7) / 7.0))
    stress = 1.0
    for center, width, depth in ((date(2025, 5, 27), 18.0, 1.0), (date(2026, 3, 16), 12.0, 0.45)):
        z = (d - center).days / width
        stress -= stress_sens * depth * math.exp(-0.5 * z * z)
    h = int(sha256_hex(f"{cid}|{d.isoformat()}")[:8], 16) / 0xFFFFFFFF
    noise = 1.0 + (h - 0.5) * 0.012
    value = Decimal(str(base_mm)) * MM * Decimal(str(round(trend * seasonal * stress * noise, 8)))
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)


def _pool_line(pool: tuple, amount: Decimal, increase: bool, line_number: int) -> dict:
    cid, _, product, account, partner, *_ = pool
    is_asset = account < 200000
    debit_side = increase if is_asset else not increase
    return {"line_number": line_number, "account_id": account, "contract_id": cid, "partner_id": partner,
            "amount_debit": amount if debit_side else 0, "amount_credit": 0 if debit_side else amount}


def _cash_line(amount: Decimal, debit: bool, line_number: int) -> dict:
    return {"line_number": line_number, "account_id": 110101, "contract_id": CASH_CONTRACT, "partner_id": 1005,
            "amount_debit": amount if debit else 0, "amount_credit": 0 if debit else amount}


def _post_daily_alm(conn, d: date) -> None:
    """Movimiento neto diario de las carteras agregadas contra la reserva BCCh (un asiento multi-pata)."""
    prev = d - timedelta(days=1)
    while not is_business_day(prev) and prev > BOOK_START:
        prev -= timedelta(days=1)
    lines, net_cash_debit = [], Decimal(0)
    for i, pool in enumerate(POOL_BOOK, start=1):
        delta = _pool_target_clp(pool, d) - _pool_target_clp(pool, prev)
        if delta == 0:
            continue
        is_asset = pool[3] < 200000
        lines.append(_pool_line(pool, abs(delta), delta > 0, i))
        # Activo que sube consume caja; pasivo/patrimonio que sube aporta caja.
        net_cash_debit += -delta if is_asset else delta
    if net_cash_debit != 0:
        lines.append(_cash_line(abs(net_cash_debit), net_cash_debit > 0, 99))
    if len(lines) >= 2:
        post_journal_entry(f"ALM-DAILY-{d:%Y%m%d}", "ALM_BOOK", "MOV_NETO_DIARIO",
                           f"Movimiento neto diario libro ALM {d:%d-%m-%Y}", lines,
                           now=local_to_utc(d, 17, 0), conn=conn)


def _pool_core_values(pool: tuple, d: date) -> dict:
    cid, number, product, account, partner, treatment, _, rate, branch, *_ = pool
    bal = m2i(_pool_target_clp(pool, d))
    return {
        "partner_id": partner, "contract_number": number, "product_code": product, "branch_code": branch,
        "currency_code": "CLP", "gl_account_id": account, "origination_date": d.isoformat(), "maturity_date": None,
        "original_amount_e4": bal, "current_balance_e4": bal, "committed_limit_e4": 0, "undrawn_commitment_e4": 0,
        "annual_nominal_rate_e6": f2i(rate), "spread_rate_e6": 0, "repricing_frequency": "FIJO",
        "next_repricing_date": None, "payment_frequency_months": None,
        "amortization_type": "POOL" if product in LADDER_PROFILES else "NONE",
        "cmf_portfolio_type": "C" if product == "COM_CREDITO" else ("H" if product == "HIPOTECARIO" else ("N" if product == "CONSUMO" else "N/A")),
        "cmf_rating": "A2" if product in ("COM_CREDITO", "HIPOTECARIO", "CONSUMO") else "NA",
        "days_past_due": 0, "collateral_value_e4": 0, "liquidity_treatment_code": treatment,
        "contract_status": "VIGENTE", "balance_maintained_in_core": 0,
    }


def _golden_contract(contract_id: int, partner: int, number: str, product: str, branch: str, gl: int,
                     origination: date, maturity: Optional[date], amount: Decimal, rate: str, repricing: str,
                     pay_months: Optional[int], amort: str, portfolio: str, rating: str, treatment: str,
                     committed: Decimal = Decimal(0), undrawn: Decimal = Decimal(0),
                     in_core: int = 1, next_reprice: Optional[date] = None) -> dict:
    return {
        "partner_id": partner, "contract_number": number, "product_code": product, "branch_code": branch,
        "currency_code": "CLP", "gl_account_id": gl, "origination_date": origination.isoformat(),
        "maturity_date": maturity.isoformat() if maturity else None, "original_amount_e4": m2i(amount),
        "current_balance_e4": m2i(amount), "committed_limit_e4": m2i(committed), "undrawn_commitment_e4": m2i(undrawn),
        "annual_nominal_rate_e6": f2i(rate), "spread_rate_e6": f2i("0.02") if repricing != "FIJO" else 0,
        "repricing_frequency": repricing, "next_repricing_date": next_reprice.isoformat() if next_reprice else None,
        "payment_frequency_months": pay_months, "amortization_type": amort, "cmf_portfolio_type": portfolio,
        "cmf_rating": rating, "days_past_due": 0, "collateral_value_e4": 0, "liquidity_treatment_code": treatment,
        "contract_status": "VIGENTE", "balance_maintained_in_core": in_core,
    }


def _seed_scenario(conn: sqlite3.Connection, up_to: date) -> None:
    """
    Siembra cronológica del escenario completo usando exclusivamente las APIs
    públicas (ledger atómico + operaciones AVF), con un reloj de simulación.
    """
    d0 = BOOK_START
    t_open = local_to_utc(d0, 9, 0)

    # --- 2024-12-01: alta de carteras en el core y asiento de apertura del libro ---
    for pool in POOL_BOOK:
        bitemporal_insert(pool[0], _pool_core_values(pool, d0), d0, reason="Migración saldo de apertura libro ALM",
                          now=t_open, conn=conn)
    for cid, number, product, treatment, undrawn_mm in OFF_BALANCE_POOLS:
        undrawn = Decimal(undrawn_mm) * MM
        bitemporal_insert(cid, {
            **_golden_contract(cid, 9000, number, product, "CL-SCL-01", 140102, d0, None, Decimal(0), "0", "FIJO",
                               None, "NONE", "C", "A2", treatment, committed=undrawn, undrawn=undrawn, in_core=0),
        }, d0, reason="Migración líneas comprometidas no giradas", now=t_open, conn=conn)
    for cid, number, product, gl, partner, treatment in (
            (CASH_CONTRACT, "RESERVA-BCCH-LBTR", "BCCH_RESERVA", 110101, 1005, "HQLA_L1_BCCH"),
            (TRANSIT_CONTRACT, "TRANSITO-CAMARA", "TRANSITO", 199001, 9001, "OTROS_ACTIVOS")):
        bitemporal_insert(cid, _golden_contract(cid, partner, number, product, "CL-SCL-02", gl, d0, None, Decimal(0),
                                                "0", "FIJO", None, "NONE", "N/A", "NA", treatment, in_core=0),
                          d0, reason="Cuenta propia del banco", now=t_open, conn=conn)

    opening_lines, cash = [], Decimal(0)
    for i, pool in enumerate(POOL_BOOK, start=1):
        amt = _pool_target_clp(pool, d0)
        opening_lines.append(_pool_line(pool, amt, True, i))
        cash += -amt if pool[3] < 200000 else amt
    opening_lines.append(_cash_line(abs(cash), cash > 0, 99))
    post_journal_entry("OPENING-20241201", "MIGRACION", "SALDO_APERTURA",
                       "Asiento de apertura: migración de saldos del libro ALM", opening_lines, now=t_open, conn=conn)

    golden_events = []

    def ev(day: date, hour: int, fn):
        golden_events.append((local_to_utc(day, hour, 0), fn))

    # --- Cuentas corrientes de clientes (contratos de pasivo con límite de no sobregiro) ---
    def open_current_accounts(now):
        for cid, partner, number in ((30101, 1001, "CTACTE-1001"), (30102, 1002, "CTACTE-1002")):
            bitemporal_insert(cid, _golden_contract(cid, partner, number, "CTACTE_EMP", "CL-SCL-01", 210101,
                                                    date(2024, 12, 2), None, Decimal(0), "0", "FIJO", None, "NONE",
                                                    "N/A", "NA", "DEP_MAYORISTA_OPERACIONAL", in_core=0),
                              date(2024, 12, 2), reason="Apertura cuenta corriente", now=now, conn=conn)
    ev(date(2024, 12, 2), 9, open_current_accounts)

    # --- Línea comprometida #60300 (Inversiones Santa Elena), 200 MM, sin giros ---
    def open_credit_line(now):
        bitemporal_insert(60300, _golden_contract(
            60300, 1001, "LINEA-COM-60300", "COM_LINEA", "CL-SCL-01", 140102, date(2024, 12, 10), date(2027, 1, 4),
            Decimal(0), "0.0780", "FIJO", 1, "BULLET", "C", "A1", "LINEA_CREDITO_CORP",
            committed=Decimal(200_000_000), undrawn=Decimal(200_000_000)),
            date(2024, 12, 10), reason="Otorgamiento línea comprometida", now=now, conn=conn)
    ev(date(2024, 12, 10), 11, open_credit_line)

    # --- Caso 1: desembolso crédito comercial #90210 por $45.000.000 ---
    def disburse_90210(now):
        post_journal_entry("CORE-CRED-90210-DESEMBOLSO", "CORE_CREDITOS", "DESEMBOLSO",
                           "Desembolso crédito comercial #90210 - Distribuidora Central", [
                               {"line_number": 1, "account_id": 140101, "contract_id": 90210, "partner_id": 1002, "amount_debit": "45000000"},
                               {"line_number": 2, "account_id": 210101, "contract_id": 30102, "partner_id": 1002, "amount_credit": "45000000"},
                           ], now=now, conn=conn)
        bitemporal_insert(90210, _golden_contract(
            90210, 1002, "OPER-COM-90210", "COM_CREDITO", "CL-SCL-01", 140101, date(2024, 12, 15), date(2026, 12, 15),
            Decimal(45_000_000), "0.0850", "MENSUAL", 1, "BULLET", "C", "A2", "INFLOW_COMERCIAL",
            next_reprice=date(2025, 1, 15)),
            date(2024, 12, 15), reason="Alta crédito comercial", now=now, conn=conn)
    ev(date(2024, 12, 15), 10, disburse_90210)

    # --- Caso 2: depósito a plazo institucional #80450 por $120.000.000 a 180 días ---
    def open_80450(now):
        post_journal_entry("CORE-DEP-80450-APERTURA", "CORE_DEPOSITOS", "DEPOSITO_PLAZO",
                           "Apertura DP institucional #80450 - Inversiones Santa Elena", [
                               _cash_line(Decimal(120_000_000), True, 1),
                               {"line_number": 2, "account_id": 220201, "contract_id": 80450, "partner_id": 1001, "amount_credit": "120000000"},
                           ], now=now, conn=conn)
        bitemporal_insert(80450, _golden_contract(
            80450, 1001, "DP-INST-80450", "DP_INST", "CL-SCL-02", 220201, date(2024, 12, 20), date(2025, 6, 18),
            Decimal(120_000_000), "0.0550", "FIJO", None, "BULLET", "N/A", "NA", "DEP_MAYORISTA_NO_OPER_FIN"),
            date(2024, 12, 20), reason="Captación DP institucional", now=now, conn=conn)
    ev(date(2024, 12, 20), 12, open_80450)

    # --- Caso 3: compra de BTP $350.000.000 contra la cuenta caliente LBTR + reintento por timeout ---
    btp_lines = [
        {"line_number": 1, "account_id": 130101, "contract_id": 70100, "partner_id": 1006, "amount_debit": "350000000"},
        _cash_line(Decimal(350_000_000), False, 2),
    ]

    def buy_btp(now):
        post_journal_entry("MESA-HQLA-70100-COMPRA", "CORE_MESA_DINERO", "COMPRA_HQLA",
                           "Compra Bonos Tesorería BTP Nivel 1 (HQLA / ALAC)", btp_lines, now=now, conn=conn)
        bitemporal_insert(70100, _golden_contract(
            70100, 1006, "BONO-BTP-70100", "BONO_SOB", "CL-SCL-02", 130101, date(2024, 12, 28), date(2034, 12, 28),
            Decimal(350_000_000), "0.0600", "FIJO", 6, "BULLET", "N/A", "NA", "HQLA_L1_SOBERANO"),
            date(2024, 12, 28), reason="Compra HQLA Nivel 1", now=now, conn=conn)

    def retry_btp(now):
        # Mismo payload, misma clave, 5 segundos después del timeout de red: respuesta reproducida, sin doble cargo.
        post_journal_entry("MESA-HQLA-70100-COMPRA", "CORE_MESA_DINERO", "COMPRA_HQLA",
                           "Compra Bonos Tesorería BTP Nivel 1 (HQLA / ALAC)", btp_lines, now=now, conn=conn)
    golden_events.append((local_to_utc(date(2024, 12, 27), 13, 55), buy_btp))
    golden_events.append((local_to_utc(date(2024, 12, 27), 13, 55) + timedelta(seconds=5), retry_btp))

    # --- Caso 1 (cont.): abono del cliente queda retenido en cuenta transitoria de cámara ---
    def payment_to_transit(now):
        post_journal_entry("CAMARA-ABONO-90210-20241228", "CAMARA_COMPENSACION", "ABONO_TRANSITO",
                           "Abono $3.000.000 crédito #90210 recibido en cámara (pendiente de aplicar)", [
                               _cash_line(Decimal(3_000_000), True, 1),
                               {"line_number": 2, "account_id": 199001, "contract_id": TRANSIT_CONTRACT, "partner_id": 1002, "amount_credit": "3000000"},
                           ], now=now, conn=conn)
    ev(date(2024, 12, 28), 11, payment_to_transit)

    # --- Transferencias en dos fases sobre la línea #60300 ---
    pending_ids = {}

    def drawdown_line(key, amount, timeout, day, hour, tag):
        def _fn(now):
            r = post_journal_entry(key, "CORE_CREDITOS", "GIRO_LINEA",
                                   f"Solicitud de giro línea #60300 por ${amount:,.0f} ({tag})", [
                                       {"line_number": 1, "account_id": 140102, "contract_id": 60300, "partner_id": 1001, "amount_debit": str(amount)},
                                       {"line_number": 2, "account_id": 210101, "contract_id": 30101, "partner_id": 1001, "amount_credit": str(amount)},
                                   ], phase="PENDING", pending_timeout=timeout, now=now, conn=conn)
            pending_ids[key] = r["journal_entry_id"]
        ev(day, hour, _fn)

    drawdown_line("LINEA-60300-GIRO-001", 80_000_000, timedelta(days=5), date(2025, 1, 2), 10, "giro parcial")

    def post_partial(now):
        resolve_pending_entry("LINEA-60300-GIRO-001-POST", pending_ids["LINEA-60300-GIRO-001"], "POST",
                              post_amounts={1: Decimal(50_000_000), 2: Decimal(50_000_000)}, now=now, conn=conn)
        bitemporal_update(60300, date(2025, 1, 3), {
            "current_balance_e4": m2i(50_000_000), "undrawn_commitment_e4": m2i(150_000_000)},
            reason="Giro liquidado $50.000.000 (se liberan $30.000.000)", now=now, conn=conn)
    ev(date(2025, 1, 3), 10, post_partial)

    drawdown_line("LINEA-60300-GIRO-002", 10_000_000, timedelta(days=5), date(2025, 1, 21), 10, "anulado")

    def void_second(now):
        resolve_pending_entry("LINEA-60300-GIRO-002-VOID", pending_ids["LINEA-60300-GIRO-002"], "VOID", now=now, conn=conn)
    ev(date(2025, 1, 22), 10, void_second)

    drawdown_line("LINEA-60300-GIRO-003", 5_000_000, timedelta(days=2), date(2025, 1, 23), 10, "expira sin uso")
    ev(date(2025, 1, 27), 6, lambda now: expire_pending_entries(now=now, conn=conn))

    # Reserva vigente que permanece PENDING: muestra saldo disponible vs contable y alimenta el LCR.
    drawdown_line("LINEA-60300-GIRO-004", 25_000_000, timedelta(days=3650), date(2025, 1, 24), 10, "comprometido en tránsito")

    # --- 2025-01-10: envío del reporte C46 de diciembre (hito de auditoría) ---
    def log_c46(now):
        _log_batch(conn, "REPORTE_CMF_C46_202412", "DWH", "dwh_fact_account_balance_snapshot", "CMF/C46",
                   now, now + timedelta(minutes=3), 0, 0, "SUCCESS", None, "Archivo C46 dic-2024 enviado a CMF")
    ev(date(2025, 1, 10), 7, log_c46)

    # --- 2025-02-05 14:30: corrección retroactiva del crédito #90210 ---
    def correct_90210(now):
        r = post_journal_entry("AJUSTE-90210-ABONO-20241228", "OPERACIONES", "AJUSTE_RETROACTIVO",
                               "Aplicación retroactiva abono $3.000.000 crédito #90210 (cámara 28-12-2024)", [
                                   {"line_number": 1, "account_id": 199001, "contract_id": TRANSIT_CONTRACT, "partner_id": 1002, "amount_debit": "3000000"},
                                   {"line_number": 2, "account_id": 140101, "contract_id": 90210, "partner_id": 1002, "amount_credit": "3000000"},
                               ], value_date=date(2024, 12, 28), now=now, conn=conn)
        bitemporal_correction(90210, date(2024, 12, 28), {"current_balance_e4": m2i(42_000_000)},
                              reason="Abono 28-12-2024 retenido en cámara aplicado retroactivamente",
                              source_journal_entry_id=r["journal_entry_id"], now=now, conn=conn)
    ev(date(2025, 2, 5), 11, correct_90210)

    # --- 2025-02-25: update de negocio (repricing a 9,10% desde 01-03-2025) ---
    def reprice_90210(now):
        bitemporal_update(90210, date(2025, 3, 1), {"annual_nominal_rate_e6": f2i("0.0910"),
                                                    "next_repricing_date": "2025-04-01"},
                          reason="Repricing mensual: tasa base + spread = 9,10%", now=now, conn=conn)
    ev(date(2025, 2, 25), 16, reprice_90210)

    # --- 2025-06-18: vencimiento del DP #80450 (capital + intereses 180/360) ---
    def mature_80450(now):
        interest = (Decimal(120_000_000) * Decimal("0.055") * Decimal(180) / Decimal(360)).quantize(Decimal("1"))
        post_journal_entry("CORE-DEP-80450-VENCIMIENTO", "CORE_DEPOSITOS", "VENCIMIENTO_DP",
                           "Pago al vencimiento DP #80450 (capital + intereses)", [
                               {"line_number": 1, "account_id": 220201, "contract_id": 80450, "partner_id": 1001, "amount_debit": "120000000"},
                               {"line_number": 2, "account_id": 510101, "contract_id": 0, "partner_id": 1001, "amount_debit": str(interest)},
                               _cash_line(Decimal(120_000_000) + interest, False, 3),
                           ], now=now, conn=conn)
        bitemporal_update(80450, date(2025, 6, 18), {"current_balance_e4": 0, "contract_status": "CANCELADO"},
                          reason="Vencimiento y pago", now=now, conn=conn)
    ev(date(2025, 6, 18), 9, mature_80450)

    # Ejecuta eventos dorados y movimientos diarios en orden cronológico estricto.
    daily = []
    d = d0 + timedelta(days=1)
    while d <= up_to:
        if is_business_day(d):
            daily.append((local_to_utc(d, 17, 0), d))
        d += timedelta(days=1)
    timeline = [(t, 0, fn, None) for t, fn in golden_events if t.date() <= up_to + timedelta(days=1)]
    timeline += [(t, 1, None, day) for t, day in daily]
    for t, _, fn, day in sorted(timeline, key=lambda x: (x[0], x[1])):
        if local_cutoff_date(t) > up_to:
            continue
        if fn is not None:
            fn(t)
        else:
            _post_daily_alm(conn, day)

    # --- Caso 4: migración cartera hipotecaria legado con aserción futura (corte 01-01-2027) ---
    cutover = local_to_utc(date(2027, 1, 1), 0, 0)
    seed_now = utc_now()
    if seed_now < cutover:
        for cid, number, amount in ((50100, "HIP-LEG-50100", Decimal(85_000_000)), (50101, "HIP-LEG-50101", Decimal(62_500_000))):
            vals = _golden_contract(cid, 1003 if cid == 50100 else 1004, number, "HIPOTECARIO", "CL-VAP-01", 140201,
                                    date(2019, 3, 1), date(2039, 3, 1), amount, "0.0390", "FIJO", 1, "BULLET", "H", "A1",
                                    "INFLOW_HIPOTECARIO", in_core=0)
            bitemporal_insert(cid, vals, date(2019, 3, 1), asserted_from=cutover,
                              reason="Migración legado sin downtime: aserción diferida al corte 01-01-2027",
                              now=seed_now, conn=conn)


def _log_batch(conn, pipeline: str, layer: str, source: str, target: str, start: datetime, end: Optional[datetime],
               extracted: int, loaded: int, status: str, diff_e4: Optional[int], message: Optional[str]) -> int:
    cur = conn.execute(
        """INSERT INTO stg_load_execution_log (batch_uuid, pipeline_name, target_layer, source_entity, target_entity,
               extract_start_time, extract_end_time, records_extracted, records_loaded, records_rejected, status,
               reconciliation_diff_e4, error_message, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?);""",
        (str(uuid.uuid4()), pipeline, layer, source, target, ts(start), ts(end) if end else None, extracted, loaded,
         status, diff_e4, message, ts(utc_now())),
    )
    return cur.lastrowid


def ensure_book_current(conn: Optional[sqlite3.Connection] = None, up_to: Optional[date] = None) -> None:
    """Siembra el escenario la primera vez y, en cada ejecución posterior, agrega los días faltantes."""
    own = conn is None
    conn = conn or get_connection()
    up_to = up_to or datetime.now(LOCAL_TZ).date()
    try:
        if conn.execute("SELECT COUNT(*) FROM oltp_journal_entry;").fetchone()[0] == 0:
            _seed_scenario(conn, up_to)
        else:
            last = conn.execute("SELECT MAX(posting_date) FROM oltp_journal_entry WHERE transaction_code = 'MOV_NETO_DIARIO';").fetchone()[0]
            d = date.fromisoformat(last) + timedelta(days=1) if last else BOOK_START + timedelta(days=1)
            while d <= up_to:
                if is_business_day(d) and local_to_utc(d, 17, 0) <= utc_now():
                    _post_daily_alm(conn, d)
                d += timedelta(days=1)
        run_outbox_relay(conn=conn, batch_size=100000)
        refresh_dwh(conn, up_to)
    finally:
        if own:
            conn.close()


# =============================================================================
# 6. CAPA DWH: ETL DE DIMENSIONES Y HECHOS
# =============================================================================

def _dim_maps(conn) -> dict:
    q = lambda sql: {r[0]: r[1] for r in conn.execute(sql).fetchall()}
    treatments = {r["treatment_code"]: dict(r) for r in conn.execute("SELECT * FROM dwh_dim_liquidity_treatment;")}
    return {
        "partner": q("SELECT partner_id, partner_key FROM dwh_dim_partner WHERE is_current = 1;"),
        "product": q("SELECT product_code, product_key FROM dwh_dim_product;"),
        "product_side": q("SELECT product_code, balance_side FROM dwh_dim_product;"),
        "product_is_loan": q("SELECT product_code, is_loan FROM dwh_dim_product;"),
        "branch": q("SELECT branch_code, branch_key FROM dwh_dim_branch;"),
        "gl": q("SELECT account_id, gl_account_key FROM dwh_dim_gl_account;"),
        "gl_normal": q("SELECT account_id, normal_balance FROM dwh_dim_gl_account;"),
        "risk": q("SELECT cmf_rating_category, risk_band_key FROM dwh_dim_risk_band;"),
        "risk_rate": q("SELECT risk_band_key, provision_rate_e6 FROM dwh_dim_risk_band;"),
        "treatment": treatments,
        "bucket": [dict(r) for r in conn.execute("SELECT * FROM dwh_dim_time_bucket ORDER BY sort_order;")],
        "curve": q("SELECT bucket_code, zero_rate_e6 FROM dwh_yield_curve_point WHERE currency_code = 'CLP';"),
        "ladder": {},
    }


def _load_ladders(conn, maps: dict) -> None:
    for r in conn.execute("SELECT product_code, bucket_code, share_e6 FROM dwh_behavioral_ladder_profile;"):
        maps["ladder"].setdefault(r[0], {})[r[1]] = r[2]


def _sync_dim_contract(conn, maps: dict) -> Dict[int, int]:
    rows = conn.execute(
        """SELECT contract_id, contract_number, product_code, branch_code, origination_date, maturity_date, currency_code,
                  balance_maintained_in_core
           FROM core_contract_profile c
           WHERE row_created_at = (SELECT MIN(row_created_at) FROM core_contract_profile x WHERE x.contract_id = c.contract_id)
           GROUP BY contract_id;"""
    ).fetchall()
    for r in rows:
        conn.execute(
            """INSERT OR IGNORE INTO dwh_dim_contract (contract_id, contract_number, product_key, branch_key,
                   origination_date_key, maturity_date_key, currency_code, source_system)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?);""",
            (r["contract_id"], r["contract_number"], maps["product"][r["product_code"]], maps["branch"][r["branch_code"]],
             date_key(date.fromisoformat(r["origination_date"])),
             date_key(date.fromisoformat(r["maturity_date"])) if r["maturity_date"] else None, r["currency_code"],
             "CORE_NOMINADO" if r["balance_maintained_in_core"] else "LIBRO_ALM"),
        )
    return {r[0]: r[1] for r in conn.execute("SELECT contract_id, contract_key FROM dwh_dim_contract;")}


def _bucket_for(days: int, buckets: List[dict]) -> dict:
    for b in buckets:
        if b["min_days"] <= days <= b["max_days"]:
            return b
    return buckets[-1]


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    return date(y, m, 28)


def _project_cashflows(core: dict, balance_e4: int, d: date, maps: dict, full_ladder: bool) -> List[dict]:
    """
    Proyección de flujos contractuales (capital e interés) de un contrato a la
    fecha de posición `d`. Carteras agregadas usan su perfil conductual por
    banda; contratos nominados usan su calendario contractual. Para contratos de
    tasa variable se emite además el nocional de repricing (IRRBB).
    """
    if balance_e4 <= 0:
        return []
    side = maps["product_side"][core["product_code"]]
    direction = "INFLOW" if side == "ASSET" else "OUTFLOW"
    rate = i2f(core["annual_nominal_rate_e6"])
    bal = i2m(balance_e4)
    flows = []  # (cashflow_date, type, principal, interest, liq, irrbb)
    horizon = 99999 if full_ladder else 30

    if core["amortization_type"] == "POOL":
        ladder = maps["ladder"].get(core["product_code"], {})
        for b in maps["bucket"]:
            share = i2f(ladder.get(b["bucket_code"], 0))
            if share == 0 or b["midpoint_days"] > horizon:
                continue
            principal = (bal * share).quantize(reg.MONEY_QUANTUM)
            interest = (principal * rate * Decimal(b["midpoint_days"]) / Decimal(360)).quantize(reg.MONEY_QUANTUM)
            flows.append((d + timedelta(days=b["midpoint_days"]), "PRINCIPAL", principal, Decimal(0), 1, 1))
            if interest > 0:
                flows.append((d + timedelta(days=b["midpoint_days"]), "INTEREST", Decimal(0), interest, 1, 1))
    elif core["amortization_type"] == "BULLET" and core["maturity_date"]:
        maturity = date.fromisoformat(core["maturity_date"])
        if maturity <= d:
            return []
        reprice = date.fromisoformat(core["next_repricing_date"]) if core["next_repricing_date"] else None
        while reprice and reprice <= d:
            reprice = _add_months(reprice, 1)
        floating = core["repricing_frequency"] not in ("FIJO",) and reprice is not None and reprice < maturity
        months = core["payment_frequency_months"] or 0
        if months:
            pay = date.fromisoformat(core["origination_date"])
            prev = pay
            while pay <= d:
                prev = pay
                pay = _add_months(pay, months)
            while pay < maturity and (pay - d).days <= horizon:
                interest = (bal * rate * Decimal((pay - prev).days) / Decimal(360)).quantize(reg.MONEY_QUANTUM)
                flows.append((pay, "INTEREST", Decimal(0), interest, 1, 0 if (floating and pay > reprice) else 1))
                prev, pay = pay, _add_months(pay, months)
            last_period_start = prev
        else:
            last_period_start = date.fromisoformat(core["origination_date"])
        if (maturity - d).days <= horizon:
            final_int = (bal * rate * Decimal((maturity - last_period_start).days) / Decimal(360)).quantize(reg.MONEY_QUANTUM)
            flows.append((maturity, "PRINCIPAL", bal, Decimal(0), 1, 0 if floating else 1))
            if final_int > 0:
                flows.append((maturity, "INTEREST", Decimal(0), final_int, 1, 0 if floating else 1))
        if floating and (reprice - d).days <= horizon:
            flows.append((reprice, "REPRICING_NOTIONAL", bal, Decimal(0), 0, 1))
    out = []
    for cf_date, cf_type, principal, interest, liq, irrbb in flows:
        days = (cf_date - d).days
        bucket = _bucket_for(days, maps["bucket"])
        r = i2f(maps["curve"][bucket["bucket_code"]])
        df = (-(r * Decimal(days) / Decimal(365))).exp().quantize(Decimal("0.00000001"))
        total = principal + interest
        key = (cf_date, cf_type)
        out.append({
            "cashflow_date": cf_date, "cashflow_type": cf_type, "key": key, "direction": direction,
            "principal_e4": m2i(principal), "interest_e4": m2i(interest), "total_e4": m2i(total),
            "days": days, "time_bucket_key": bucket["time_bucket_key"], "discount_factor_e8": int(df * E8),
            "pv_e4": m2i(total * df), "liq": liq, "irrbb": irrbb,
        })
    # Consolidar duplicados de fecha/tipo (dos bandas con el mismo punto medio no ocurren, pero se protege el grano).
    merged: Dict[tuple, dict] = {}
    for f in out:
        if f["key"] in merged:
            m = merged[f["key"]]
            for k in ("principal_e4", "interest_e4", "total_e4", "pv_e4"):
                m[k] += f[k]
        else:
            merged[f["key"]] = f
    return list(merged.values())


def _core_rows_as_of(all_core: List[sqlite3.Row], business_ts: str, assertion_ts: str) -> Dict[int, dict]:
    out = {}
    for r in all_core:
        if r["valid_from"] <= business_ts < r["valid_to"] and r["asserted_from"] <= assertion_ts < r["asserted_to"]:
            out[r["contract_id"]] = dict(r)
    return out


def _positions_as_of(conn, business_date: date, known_at: datetime) -> Dict[Tuple[int, int], List[int]]:
    """Posiciones del ledger: [neto_posted_debe, pendiente_debe, pendiente_haber] por (cuenta, contrato)."""
    rows = conn.execute(
        """SELECT p.account_id, p.contract_id,
                  SUM(CASE WHEN p.posting_status <> 'PENDING' THEN p.functional_debit_e4 - p.functional_credit_e4 ELSE 0 END),
                  SUM(CASE WHEN p.posting_status = 'PENDING' THEN p.functional_debit_e4 ELSE 0 END) - SUM(p.functional_released_debit_e4),
                  SUM(CASE WHEN p.posting_status = 'PENDING' THEN p.functional_credit_e4 ELSE 0 END) - SUM(p.functional_released_credit_e4)
           FROM oltp_ledger_posting p JOIN oltp_journal_entry j ON j.journal_entry_id = p.journal_entry_id
           WHERE j.transaction_timestamp_utc <= ? AND j.value_date <= ?
           GROUP BY p.account_id, p.contract_id;""",
        (ts(known_at), business_date.isoformat()),
    ).fetchall()
    return {(r[0], r[1]): [r[2], r[3], r[4]] for r in rows}


def _snapshot_rows_for_date(d: date, known_at: datetime, core_rows: Dict[int, dict],
                            positions: Dict[Tuple[int, int], List[int]], maps: dict,
                            contract_keys: Dict[int, int]) -> List[dict]:
    rows = []
    for cid, core in core_rows.items():
        acct = core["gl_account_id"]
        net_dr, pend_dr, pend_cr = positions.get((acct, cid), [0, 0, 0])
        side = maps["product_side"][core["product_code"]]
        balance = net_dr if maps["gl_normal"][acct] == "D" else -net_dr
        if side == "OFF_BALANCE":
            balance = 0
        undrawn = core["undrawn_commitment_e4"]
        if balance == 0 and undrawn == 0 and pend_dr == 0 and pend_cr == 0:
            continue
        rating = core["cmf_rating"]
        risk_key = maps["risk"].get(rating, maps["risk"]["NA"])
        provision = 0
        if maps["product_is_loan"][core["product_code"]] and balance > 0:
            net_exp = max(balance - core["collateral_value_e4"], 0)
            provision = m2i(i2m(net_exp) * i2f(maps["risk_rate"][risk_key]))
        rate = core["annual_nominal_rate_e6"]
        orig = date.fromisoformat(core["origination_date"])
        accrual_days = (d - orig).days % 30 if core["payment_frequency_months"] else (d - orig).days % 30
        accrued = m2i(i2m(abs(balance)) * i2f(rate) * Decimal(accrual_days) / Decimal(360))
        residual = (date.fromisoformat(core["maturity_date"]) - d).days if core["maturity_date"] else None
        measures = {
            "balance_orig_e4": balance, "balance_clp_e4": balance, "fx_rate_to_clp_e6": E6,
            "accrued_interest_clp_e4": accrued, "contractual_rate_e6": rate,
            "regulatory_provision_clp_e4": provision, "collateral_value_clp_e4": core["collateral_value_e4"],
            "net_exposure_clp_e4": max(balance - core["collateral_value_e4"], 0),
            "market_value_clp_e4": balance, "undrawn_commitment_clp_e4": undrawn,
            "pending_debits_clp_e4": pend_dr, "pending_credits_clp_e4": pend_cr,
            "residual_maturity_days": residual,
        }
        treat = maps["treatment"][core["liquidity_treatment_code"]]
        dims = {
            "partner_key": maps["partner"][core["partner_id"]], "product_key": maps["product"][core["product_code"]],
            "branch_key": maps["branch"][core["branch_code"]], "gl_account_key": maps["gl"][acct],
            "risk_band_key": risk_key, "liquidity_treatment_key": treat["liquidity_treatment_key"],
        }
        checksum = sha256_hex(canonical_json({**measures, **dims, "rating": rating}))
        rows.append({
            "snapshot_date_key": date_key(d), "contract_id": cid, "contract_key": contract_keys[cid],
            "snapshot_asserted_at": ts(known_at), "currency_code": core["currency_code"], "core": core,
            "row_checksum": checksum, **measures, **dims,
        })
    return rows


SNAPSHOT_COLUMNS = [
    "snapshot_date_key", "contract_key", "restatement_seq", "snapshot_asserted_at", "partner_key", "product_key",
    "branch_key", "gl_account_key", "risk_band_key", "liquidity_treatment_key", "currency_code", "balance_orig_e4",
    "balance_clp_e4", "fx_rate_to_clp_e6", "accrued_interest_clp_e4", "contractual_rate_e6",
    "regulatory_provision_clp_e4", "collateral_value_clp_e4", "net_exposure_clp_e4", "market_value_clp_e4",
    "undrawn_commitment_clp_e4", "pending_debits_clp_e4", "pending_credits_clp_e4", "residual_maturity_days",
    "row_checksum",
]
CASHFLOW_COLUMNS = [
    "position_date_key", "contract_key", "restatement_seq", "cashflow_date_key", "cashflow_type", "partner_key",
    "product_key", "risk_band_key", "liquidity_treatment_key", "time_bucket_key", "currency_code", "direction",
    "principal_e4", "interest_e4", "total_e4", "days_to_cashflow", "discount_factor_e8", "present_value_clp_e4",
    "include_in_liquidity", "include_in_irrbb",
]


def _persist_snapshot(conn, rows: List[dict], seq_by_contract: Dict[int, int], d: date, maps: dict,
                      full_ladder: bool) -> Tuple[int, int]:
    snap_vals, cf_vals = [], []
    for r in rows:
        r["restatement_seq"] = seq_by_contract.get(r["contract_key"], 1)
        snap_vals.append([r[c] for c in SNAPSHOT_COLUMNS])
        for f in _project_cashflows(r["core"], r["balance_clp_e4"], d, maps, full_ladder):
            cf_vals.append([
                r["snapshot_date_key"], r["contract_key"], r["restatement_seq"], date_key(f["cashflow_date"]),
                f["cashflow_type"], r["partner_key"], r["product_key"], r["risk_band_key"], r["liquidity_treatment_key"],
                f["time_bucket_key"], r["currency_code"], f["direction"], f["principal_e4"], f["interest_e4"],
                f["total_e4"], f["days"], f["discount_factor_e8"], f["pv_e4"], f["liq"], f["irrbb"],
            ])
    conn.executemany(f"INSERT INTO dwh_fact_account_balance_snapshot ({', '.join(SNAPSHOT_COLUMNS)}) "
                     f"VALUES ({', '.join('?' * len(SNAPSHOT_COLUMNS))});", snap_vals)
    conn.executemany(f"INSERT INTO dwh_fact_contractual_cashflows ({', '.join(CASHFLOW_COLUMNS)}) "
                     f"VALUES ({', '.join('?' * len(CASHFLOW_COLUMNS))});", cf_vals)
    return len(snap_vals), len(cf_vals)


def _build_daily_snapshots(conn, maps: dict, contract_keys: Dict[int, int], start: date, end: date, today: date) -> int:
    """
    Construcción incremental de snapshots diarios "tal como se sabía al cierre de
    cada día". Las posiciones del ledger se acumulan en una sola pasada usando la
    fecha de conocimiento efectiva = max(fecha local de la transacción, fecha valor).
    """
    all_core = conn.execute("SELECT * FROM core_contract_profile;").fetchall()
    deltas = conn.execute(
        """SELECT MAX(substr(j.transaction_timestamp_local, 1, 10), j.value_date) AS known_date, p.account_id, p.contract_id,
                  SUM(CASE WHEN p.posting_status <> 'PENDING' THEN p.functional_debit_e4 - p.functional_credit_e4 ELSE 0 END),
                  SUM(CASE WHEN p.posting_status = 'PENDING' THEN p.functional_debit_e4 ELSE 0 END) - SUM(p.functional_released_debit_e4),
                  SUM(CASE WHEN p.posting_status = 'PENDING' THEN p.functional_credit_e4 ELSE 0 END) - SUM(p.functional_released_credit_e4)
           FROM oltp_ledger_posting p JOIN oltp_journal_entry j ON j.journal_entry_id = p.journal_entry_id
           GROUP BY known_date, p.account_id, p.contract_id ORDER BY known_date;"""
    ).fetchall()
    positions: Dict[Tuple[int, int], List[int]] = {}
    idx = 0
    total = 0
    d = BOOK_START
    while d <= end:
        while idx < len(deltas) and deltas[idx][0] <= d.isoformat():
            _, acct, cid, net, pdr, pcr = deltas[idx]
            p = positions.setdefault((acct, cid), [0, 0, 0])
            p[0] += net
            p[1] += pdr
            p[2] += pcr
            idx += 1
        if d >= start:
            known_at = min(end_of_local_day_utc(d), utc_now())
            core_rows = _core_rows_as_of(all_core, f"{d.isoformat()} 23:59:59.99", ts(known_at))
            rows = _snapshot_rows_for_date(d, known_at, core_rows, positions, maps, contract_keys)
            nxt = d + timedelta(days=1)
            full = nxt.month != d.month or d == end
            _persist_snapshot(conn, rows, {}, d, maps, full)
            total += len(rows)
        d += timedelta(days=1)
    return total


def _process_restatements(conn, maps: dict, contract_keys: Dict[int, int]) -> int:
    """
    Reexpresión de snapshots (Caso Real 1). Cada hecho tardío —asiento con fecha
    valor anterior a su fecha contable, o corrección bitemporal en el core—
    genera, para las fechas ya publicadas que afecta, una nueva versión con
    `restatement_seq + 1` y `snapshot_asserted_at = instante del conocimiento`.
    Las versiones previas permanecen: el reporte "tal como se envió" es reproducible.
    """
    events = conn.execute(
        """SELECT 'LEDGER-' || journal_entry_id, transaction_timestamp_utc, value_date
           FROM oltp_journal_entry WHERE value_date < substr(transaction_timestamp_local, 1, 10)
           UNION ALL
           SELECT 'CORE-' || contract_id || '-' || asserted_from, asserted_from, substr(valid_from, 1, 10)
           FROM core_contract_profile WHERE change_type = 'CORRECTION'
           ORDER BY 2;"""
    ).fetchall()
    all_core = conn.execute("SELECT * FROM core_contract_profile;").fetchall()
    total = 0
    for key, known_s, first_s in events:
        if conn.execute("SELECT 1 FROM stg_restatement_event_log WHERE event_key = ?;", (key,)).fetchone():
            continue
        known_at = parse_ts(known_s) + timedelta(seconds=1)
        first = date.fromisoformat(first_s)
        last = utc_to_local(known_at).date() - timedelta(days=1)
        restated = 0
        d = first
        while d <= last:
            dk = date_key(d)
            current = {r[0]: (r[1], r[2]) for r in conn.execute(
                """SELECT contract_key, MAX(restatement_seq), row_checksum FROM dwh_fact_account_balance_snapshot
                   WHERE snapshot_date_key = ? GROUP BY contract_key;""", (dk,))}
            if current:
                core_rows = _core_rows_as_of(all_core, f"{d.isoformat()} 23:59:59.99", ts(known_at))
                positions = _positions_as_of(conn, d, known_at)
                rows = _snapshot_rows_for_date(d, known_at, core_rows, positions, maps, contract_keys)
                changed, seqs = [], {}
                for r in rows:
                    prev = current.get(r["contract_key"])
                    latest_checksum = conn.execute(
                        "SELECT row_checksum FROM dwh_fact_account_balance_snapshot WHERE snapshot_date_key = ? "
                        "AND contract_key = ? ORDER BY restatement_seq DESC LIMIT 1;", (dk, r["contract_key"])).fetchone()
                    if prev is None or latest_checksum[0] != r["row_checksum"]:
                        changed.append(r)
                        seqs[r["contract_key"]] = (prev[0] + 1) if prev else 1
                if changed:
                    full = (d + timedelta(days=1)).month != d.month
                    _persist_snapshot(conn, changed, seqs, d, maps, full)
                    restated += len(changed)
            d += timedelta(days=1)
        conn.execute(
            "INSERT INTO stg_restatement_event_log (event_key, known_at, first_date, last_date, rows_restated, processed_at) "
            "VALUES (?, ?, ?, ?, ?, ?);", (key, ts(known_at), first.isoformat(), last.isoformat(), restated, ts(utc_now())))
        total += restated
    return total


def _load_gl_transaction_fact(conn, maps: dict, contract_keys: Dict[int, int], cutoff: date) -> int:
    """
    Carga incremental del hecho transaccional (Kimball cap. 9): número de
    secuencia continuo por (cuenta, contrato), saldo posterior y marca de última
    transacción del día. Sólo días contables cerrados (< cutoff), de modo que
    `is_final_daily_tx` nunca necesita reescribirse.
    """
    last_loaded = conn.execute("SELECT COALESCE(MAX(posting_id), 0) FROM dwh_fact_gl_transaction_postings;").fetchone()[0]
    rows = conn.execute(
        """SELECT p.posting_id, p.journal_entry_id, p.line_number, p.account_id, p.contract_id, p.partner_id,
                  p.posting_status, p.functional_debit_e4, p.functional_credit_e4,
                  j.posting_date, j.value_date, j.transaction_code, j.entry_kind, j.transaction_timestamp_utc,
                  j.transaction_timestamp_local, j.local_cutoff_date
           FROM oltp_ledger_posting p JOIN oltp_journal_entry j ON j.journal_entry_id = p.journal_entry_id
           WHERE p.posting_id > ? AND j.posting_date < ? AND p.posting_status = 'POSTED'
           ORDER BY j.posting_date, p.journal_entry_id, p.line_number;""",
        (last_loaded, cutoff.isoformat()),
    ).fetchall()
    if not rows:
        return 0
    state = {(r[0], r[1]): [r[2], r[3]] for r in conn.execute(
        """SELECT gl_account_key, contract_key, MAX(account_sequence_number),
                  (SELECT f2.balance_after_e4 FROM dwh_fact_gl_transaction_postings f2
                   WHERE f2.gl_account_key = f.gl_account_key AND f2.contract_key = f.contract_key
                   ORDER BY f2.account_sequence_number DESC LIMIT 1)
           FROM dwh_fact_gl_transaction_postings f GROUP BY gl_account_key, contract_key;""")}
    staged = []
    for r in rows:
        gk = maps["gl"][r["account_id"]]
        ck = contract_keys.get(r["contract_id"], 0)
        sign = 1 if maps["gl_normal"][r["account_id"]] == "D" else -1
        signed = sign * (r["functional_debit_e4"] - r["functional_credit_e4"])
        st = state.setdefault((gk, ck), [0, 0])
        st[0] += 1
        st[1] += signed
        staged.append([r["posting_id"], r["journal_entry_id"], r["line_number"],
                       date_key(date.fromisoformat(r["posting_date"])), date_key(date.fromisoformat(r["value_date"])),
                       gk, ck, maps["partner"].get(r["partner_id"], 0), r["transaction_code"], r["entry_kind"],
                       r["transaction_timestamp_utc"], r["transaction_timestamp_local"], r["local_cutoff_date"],
                       r["functional_debit_e4"], r["functional_credit_e4"], signed, st[1], st[0], 0])
    # final_flag: última transacción de cada (cuenta, contrato, día contable).
    last_of_day = {}
    for i, s in enumerate(staged):
        last_of_day[(s[5], s[6], s[3])] = i
    for i in last_of_day.values():
        staged[i][-1] = 1
    conn.executemany(
        """INSERT INTO dwh_fact_gl_transaction_postings (posting_id, journal_entry_id, line_number, posting_date_key,
               value_date_key, gl_account_key, contract_key, partner_key, transaction_code, entry_kind,
               transaction_timestamp_utc, transaction_timestamp_local, local_cutoff_date, debit_amount_e4,
               credit_amount_e4, signed_amount_e4, balance_after_e4, account_sequence_number, is_final_daily_tx)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
        staged,
    )
    return len(staged)


def refresh_dwh(conn: Optional[sqlite3.Connection] = None, up_to: Optional[date] = None) -> None:
    """Orquestador ETL incremental: dimensiones -> snapshots -> reexpresiones -> hecho transaccional -> conciliación."""
    own = conn is None
    conn = conn or get_connection()
    today = up_to or datetime.now(LOCAL_TZ).date()
    try:
        start_t = utc_now()
        with atomic(conn):
            maps = _dim_maps(conn)
            _load_ladders(conn, maps)
            contract_keys = _sync_dim_contract(conn, maps)
            # El DWH publica días contables cerrados (T+1): el último snapshot es el cierre de ayer.
            last_closed = today - timedelta(days=1)
            last = conn.execute("SELECT MAX(snapshot_date_key) FROM dwh_fact_account_balance_snapshot;").fetchone()[0]
            start = key_to_date(last) + timedelta(days=1) if last else BOOK_START
            built = 0
            if start <= last_closed:
                built = _build_daily_snapshots(conn, maps, contract_keys, start, last_closed, today)
                _log_batch(conn, "DWH_SNAPSHOT_DIARIO", "DWH", "oltp_ledger_posting + core_contract_profile",
                           "dwh_fact_account_balance_snapshot", start_t, utc_now(), built, built, "SUCCESS", None,
                           f"Snapshots {start.isoformat()} -> {last_closed.isoformat()}")
            restated = _process_restatements(conn, maps, contract_keys)
            if restated:
                _log_batch(conn, "DWH_REEXPRESION_RETROACTIVA", "DWH", "oltp_journal_entry (fecha valor) / core CORRECTION",
                           "dwh_fact_account_balance_snapshot", start_t, utc_now(), restated, restated, "SUCCESS", None,
                           "Nuevas versiones restatement_seq por hechos tardíos")
            gl = _load_gl_transaction_fact(conn, maps, contract_keys, today)
            if gl:
                _log_batch(conn, "DWH_HECHO_TRANSACCIONAL_GL", "DWH", "oltp_ledger_posting",
                           "dwh_fact_gl_transaction_postings", start_t, utc_now(), gl, gl, "SUCCESS", None, None)
        if built or restated:
            reconcile_legacy_vs_ledger(today - timedelta(days=1), conn=conn)
    finally:
        if own:
            conn.close()


# =============================================================================
# 7. RECONCILIACIÓN DE TRES VÍAS
# =============================================================================

def reconcile_legacy_vs_ledger(reconciliation_date: date, conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """
    Concilia, por contrato y a la fecha indicada (con todo lo conocido hoy):
      core (saldo asertivo vigente) vs ledger (suma de postings) vs DWH (última
      reexpresión del snapshot). La diferencia aceptada es exactamente 0.0000.
    """
    own = conn is None
    conn = conn or get_connection()
    try:
        now = utc_now()
        start = now
        positions = _positions_as_of(conn, reconciliation_date, now)
        all_core = conn.execute("SELECT * FROM core_contract_profile;").fetchall()
        core = _core_rows_as_of(all_core, f"{reconciliation_date.isoformat()} 23:59:59.99", ts(now))
        normals = {r[0]: r[1] for r in conn.execute("SELECT account_id, normal_balance FROM oltp_ledger_account;")}
        dwh = {r[0]: r[1] for r in conn.execute(
            """SELECT c.contract_id, s.balance_clp_e4 FROM dwh_fact_account_balance_snapshot s
               JOIN dwh_dim_contract c ON c.contract_key = s.contract_key
               WHERE s.snapshot_date_key = ? AND s.restatement_seq = (
                   SELECT MAX(s2.restatement_seq) FROM dwh_fact_account_balance_snapshot s2
                   WHERE s2.snapshot_date_key = s.snapshot_date_key AND s2.contract_key = s.contract_key);""",
            (date_key(reconciliation_date),))}
        results = []
        for cid, row in sorted(core.items()):
            if row["product_code"] == "LINEA_NO_GIRADA":
                continue
            net = positions.get((row["gl_account_id"], cid), [0, 0, 0])[0]
            ledger_bal = net if normals[row["gl_account_id"]] == "D" else -net
            core_bal = row["current_balance_e4"] if row["balance_maintained_in_core"] else None
            dwh_bal = dwh.get(cid, 0 if ledger_bal == 0 else None)
            d_cl = (core_bal - ledger_bal) if core_bal is not None else None
            d_ld = (ledger_bal - dwh_bal) if dwh_bal is not None else None
            ok = (d_cl in (None, 0)) and d_ld == 0
            results.append((cid, core_bal, ledger_bal, dwh_bal, d_cl, d_ld, "CUADRADO" if ok else "DESCUADRE"))
        total_diff = sum(abs(r[4] or 0) + abs(r[5] or 0) for r in results)
        with atomic(conn):
            batch = _log_batch(conn, "RECONCILIACION_3_VIAS", "STG", "core_contract_profile / oltp_ledger_posting",
                               "dwh_fact_account_balance_snapshot", start, utc_now(), len(results), len(results),
                               "RECONCILED" if total_diff == 0 else "DISCREPANCY", total_diff,
                               f"Fecha de conciliación {reconciliation_date.isoformat()}")
            conn.executemany(
                """INSERT INTO stg_reconciliation_result (batch_id, reconciliation_date, contract_id, core_balance_e4,
                       ledger_balance_e4, dwh_balance_e4, diff_core_ledger_e4, diff_ledger_dwh_e4, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                [(batch, reconciliation_date.isoformat(), *r) for r in results],
            )
        df = pd.DataFrame(results, columns=["contract_id", "core_balance_e4", "ledger_balance_e4", "dwh_balance_e4",
                                            "diff_core_ledger_e4", "diff_ledger_dwh_e4", "status"])
        for c in ["core_balance_e4", "ledger_balance_e4", "dwh_balance_e4", "diff_core_ledger_e4", "diff_ledger_dwh_e4"]:
            df[c[:-3]] = df[c].map(lambda v: None if v is None or pd.isna(v) else float(i2m(int(v))))
        return df
    finally:
        if own:
            conn.close()


# =============================================================================
# 8. CÁLCULO REGULATORIO DESDE LA BASE (LCR / NSFR / C46 / C54 / C56)
# =============================================================================

def _latest_snapshot_sql(known_at_param: bool) -> str:
    cond = "AND s2.snapshot_asserted_at <= :known_at" if known_at_param else ""
    return f"""
        SELECT s.*, c.contract_id, c.contract_number, pr.product_code, pr.product_name, pr.balance_side,
               t.treatment_code, t.lcr_component, t.lcr_category, t.hqla_level, t.hqla_haircut_e6, t.lcr_basis,
               t.lcr_rate_e6, t.undrawn_runoff_rate_e6, t.nsfr_side, t.nsfr_category, t.nsfr_factor_lt_6m_e6,
               t.nsfr_factor_6m_1y_e6, t.nsfr_factor_ge_1y_e6, t.undrawn_rsf_factor_e6,
               COALESCE(cf.liq_out_30d_e4, 0) AS cf_out_30d_e4, COALESCE(cf.liq_in_30d_e4, 0) AS cf_in_30d_e4
        FROM dwh_fact_account_balance_snapshot s
        JOIN dwh_dim_contract c ON c.contract_key = s.contract_key
        JOIN dwh_dim_product pr ON pr.product_key = s.product_key
        JOIN dwh_dim_liquidity_treatment t ON t.liquidity_treatment_key = s.liquidity_treatment_key
        LEFT JOIN (
            SELECT position_date_key, contract_key, restatement_seq,
                   SUM(CASE WHEN direction = 'OUTFLOW' THEN total_e4 ELSE 0 END) AS liq_out_30d_e4,
                   SUM(CASE WHEN direction = 'INFLOW'  THEN total_e4 ELSE 0 END) AS liq_in_30d_e4
            FROM dwh_fact_contractual_cashflows
            WHERE include_in_liquidity = 1 AND days_to_cashflow <= 30
            GROUP BY position_date_key, contract_key, restatement_seq
        ) cf ON cf.position_date_key = s.snapshot_date_key AND cf.contract_key = s.contract_key
            AND cf.restatement_seq = s.restatement_seq
        WHERE s.restatement_seq = (
            SELECT MAX(s2.restatement_seq) FROM dwh_fact_account_balance_snapshot s2
            WHERE s2.snapshot_date_key = s.snapshot_date_key AND s2.contract_key = s.contract_key {cond})
    """


def _line_label(row) -> str:
    return row["product_name"] if row["contract_number"].startswith("POOL") else f"{row['product_name']} {row['contract_number']}"


def _ladder_nsfr_factor(row, ladders: dict, buckets: List[dict]) -> Decimal:
    """Factor NSFR de una cartera agregada: promedio de los factores por banda ponderado por su perfil de vencimientos."""
    profile = ladders.get(row["product_code"])
    f = {"LT_6M": i2f(row["nsfr_factor_lt_6m_e6"]), "6M_1Y": i2f(row["nsfr_factor_6m_1y_e6"]),
         "GE_1Y": i2f(row["nsfr_factor_ge_1y_e6"])}
    if not profile:
        return reg.nsfr_factor_for_residual_maturity(row["residual_maturity_days"], f["LT_6M"], f["6M_1Y"], f["GE_1Y"])
    total = sum((i2f(profile.get(b["bucket_code"], 0)) * f[b["nsfr_maturity_band"]] for b in buckets), Decimal(0))
    return reg.to_factor(total)


def _regulatory_lines(rows: Iterable, ladders: dict, buckets: List[dict]):
    """
    Traduce filas de snapshot a líneas regulatorias (componente, categoría,
    subcategoría, nominal, factor, ponderado) para LCR y NSFR. Montos en CLP Decimal.
    """
    lcr, nsfr = [], []
    for r in rows:
        bal = i2m(r["balance_clp_e4"])
        mv = i2m(r["market_value_clp_e4"])
        label = _line_label(r)
        comp = r["lcr_component"]
        if comp == "ALAC" and mv != 0:
            factor = Decimal(1) - i2f(r["hqla_haircut_e6"])
            lcr.append(("ALAC", r["lcr_category"], label, mv, factor, reg.weighted(mv, factor), r["hqla_level"]))
        elif comp == "OUTFLOW":
            base = bal if r["lcr_basis"] == "BALANCE" else i2m(r["cf_out_30d_e4"])
            if base != 0:
                factor = i2f(r["lcr_rate_e6"])
                label_cf = label if r["lcr_basis"] == "BALANCE" else f"{label} (vence <= 30 días)"
                lcr.append(("NAF", r["lcr_category"], label_cf, base, factor, reg.weighted(base, factor), "OUT"))
        elif comp == "INFLOW":
            base = i2m(r["cf_in_30d_e4"])
            if base != 0:
                factor = i2f(r["lcr_rate_e6"])
                lcr.append(("NAF", r["lcr_category"], f"{label} (cobros <= 30 días)", base, factor,
                            reg.weighted(base, factor), "IN"))
        undrawn = i2m(r["undrawn_commitment_clp_e4"])
        pending = i2m(r["pending_debits_clp_e4"]) if r["balance_side"] in ("ASSET", "OFF_BALANCE") else Decimal(0)
        if undrawn > 0 and i2f(r["undrawn_runoff_rate_e6"]) > 0:
            free = max(undrawn - pending, Decimal(0))
            factor = i2f(r["undrawn_runoff_rate_e6"])
            lcr.append(("NAF", "Compromisos de Liquidez y Crédito", f"{label}: monto no girado", free, factor,
                        reg.weighted(free, factor), "OUT"))
        if pending > 0:
            lcr.append(("NAF", "Compromisos de Liquidez y Crédito", f"{label}: giros reservados (pending)", pending,
                        Decimal(1), reg.weighted(pending, 1), "OUT"))

        side = r["nsfr_side"]
        if side in ("ASF", "RSF") and bal != 0:
            factor = _ladder_nsfr_factor(r, ladders, buckets)
            nsfr.append(("FED" if side == "ASF" else "FER", r["nsfr_category"], label, bal, factor, reg.weighted(bal, factor)))
        if undrawn > 0 and i2f(r["undrawn_rsf_factor_e6"]) > 0:
            factor = i2f(r["undrawn_rsf_factor_e6"])
            nsfr.append(("FER", "Líneas Comprometidas No Utilizadas (5% RSF)", f"{label}: monto no girado", undrawn,
                         factor, reg.weighted(undrawn, factor)))
    return lcr, nsfr


def _aggregate_lines(lines: list, n_key: int) -> list:
    agg: Dict[tuple, list] = {}
    for ln in lines:
        k = ln[:3] + (ln[4],) + ((ln[6],) if n_key == 7 else ())
        a = agg.setdefault(k, [Decimal(0), Decimal(0)])
        a[0] += ln[3]
        a[1] += ln[5]
    return [(k[0], k[1], k[2], v[0], k[3], v[1]) + ((k[4],) if n_key == 7 else ()) for k, v in agg.items()]


def _lcr_from_lines(lcr_lines: list):
    l1 = sum((ln[5] for ln in lcr_lines if ln[0] == "ALAC" and ln[6] == "L1"), Decimal(0))
    l2a = sum((ln[5] for ln in lcr_lines if ln[0] == "ALAC" and ln[6] == "L2A"), Decimal(0))
    l2b = sum((ln[5] for ln in lcr_lines if ln[0] == "ALAC" and ln[6] == "L2B"), Decimal(0))
    out = sum((ln[5] for ln in lcr_lines if ln[6] == "OUT"), Decimal(0))
    inf = sum((ln[5] for ln in lcr_lines if ln[6] == "IN"), Decimal(0))
    return reg.compute_lcr(l1, l2a, l2b, out, inf)


def _with_cap_adjustments(lcr_lines: list, res: reg.LCRResult) -> list:
    """
    Agrega líneas de ajuste explícitas para que la suma del detalle sea
    exactamente igual al ALAC y al NAF regulatorios: los topes no se esconden.
    Las entradas se presentan con signo negativo dentro del NAF.
    """
    out = []
    for ln in lcr_lines:
        comp, cat, sub, nominal, factor, weighted, tag = ln
        if tag == "IN":
            out.append((comp, cat, sub, -nominal, factor, -weighted))
        else:
            out.append((comp, cat, sub, nominal, factor, weighted))
    if res.adjustment_15pct_cap > 0:
        out.append(("ALAC", "Ajuste Tope 15% Nivel 2B", "Exceso Nivel 2B sobre 15% del stock HQLA",
                    -res.adjustment_15pct_cap, Decimal(1), -res.adjustment_15pct_cap))
    if res.adjustment_40pct_cap > 0:
        out.append(("ALAC", "Ajuste Tope 40% Nivel 2", "Exceso Nivel 2 sobre 40% del stock HQLA",
                    -res.adjustment_40pct_cap, Decimal(1), -res.adjustment_40pct_cap))
    if res.inflow_cap_adjustment > 0:
        out.append(("NAF", "Entradas de Efectivo (Inflows con Cap 75%)", "Ajuste tope 75%: entradas no computables",
                    res.inflow_cap_adjustment, Decimal(1), res.inflow_cap_adjustment))
    return out


def get_regulatory_series(known_at: Optional[datetime] = None, detail_days: int = 120,
                          conn: Optional[sqlite3.Connection] = None) -> dict:
    """
    Serie diaria completa de LCR y NSFR calculada desde el DWH con las fórmulas
    exactas de Basilea III (topes 40% / 15% / 75%), más el detalle por línea de
    los últimos `detail_days` días. Montos en CLP como Decimal.
    `known_at` permite reconstruir la serie "tal como se sabía" en un instante.
    """
    own = conn is None
    conn = conn or get_connection()
    try:
        sql = _latest_snapshot_sql(known_at is not None) + " ORDER BY s.snapshot_date_key;"
        params = {"known_at": ts(known_at)} if known_at else {}
        rows = conn.execute(sql, params).fetchall()
        ladders: Dict[str, dict] = {}
        for r in conn.execute("SELECT product_code, bucket_code, share_e6 FROM dwh_behavioral_ladder_profile;"):
            ladders.setdefault(r[0], {})[r[1]] = r[2]
        buckets = [dict(r) for r in conn.execute("SELECT * FROM dwh_dim_time_bucket ORDER BY sort_order;")]
    finally:
        if own:
            conn.close()

    by_date: Dict[int, list] = {}
    for r in rows:
        by_date.setdefault(r["snapshot_date_key"], []).append(r)
    dates = sorted(by_date)
    detail_from = dates[-detail_days] if len(dates) > detail_days else (dates[0] if dates else 0)
    lcr_hist, nsfr_hist, lcr_det, nsfr_det = [], [], [], []
    for dk in dates:
        lcr_lines, nsfr_lines = _regulatory_lines(by_date[dk], ladders, buckets)
        lcr_lines = _aggregate_lines(lcr_lines, 7)
        nsfr_lines = _aggregate_lines(nsfr_lines, 6)
        res = _lcr_from_lines(lcr_lines)
        asf = sum((ln[5] for ln in nsfr_lines if ln[0] == "FED"), Decimal(0))
        rsf = sum((ln[5] for ln in nsfr_lines if ln[0] == "FER"), Decimal(0))
        nres = reg.compute_nsfr(asf, rsf)
        f = key_to_date(dk)
        lcr_hist.append({"fecha": f, "result": res})
        nsfr_hist.append({"fecha": f, "result": nres})
        if dk >= detail_from:
            for ln in _with_cap_adjustments(lcr_lines, res):
                lcr_det.append((f,) + ln)
            for ln in nsfr_lines:
                nsfr_det.append((f,) + ln)
    return {"lcr_hist": lcr_hist, "nsfr_hist": nsfr_hist, "lcr_det": lcr_det, "nsfr_det": nsfr_det}


def compute_lcr_from_db(position_date: date, known_at: Optional[datetime] = None,
                        conn: Optional[sqlite3.Connection] = None) -> Tuple[reg.LCRResult, pd.DataFrame]:
    """LCR oficial a una fecha (con desglose de topes) y su detalle por línea."""
    own = conn is None
    conn = conn or get_connection()
    try:
        sql = _latest_snapshot_sql(known_at is not None) + " AND s.snapshot_date_key = :dk;"
        params = {"dk": date_key(position_date), **({"known_at": ts(known_at)} if known_at else {})}
        rows = conn.execute(sql, params).fetchall()
        ladders: Dict[str, dict] = {}
        buckets = [dict(r) for r in conn.execute("SELECT * FROM dwh_dim_time_bucket ORDER BY sort_order;")]
    finally:
        if own:
            conn.close()
    lcr_lines, _ = _regulatory_lines(rows, ladders, buckets)
    lcr_lines = _aggregate_lines(lcr_lines, 7)
    res = _lcr_from_lines(lcr_lines)
    df = pd.DataFrame(_with_cap_adjustments(lcr_lines, res),
                      columns=["componente", "categoria", "subcategoria", "monto_nominal", "factor_ponderacion", "monto_ponderado"])
    return res, df


def compute_nsfr_from_db(position_date: date, known_at: Optional[datetime] = None,
                         conn: Optional[sqlite3.Connection] = None) -> Tuple[reg.NSFRResult, pd.DataFrame]:
    own = conn is None
    conn = conn or get_connection()
    try:
        sql = _latest_snapshot_sql(known_at is not None) + " AND s.snapshot_date_key = :dk;"
        params = {"dk": date_key(position_date), **({"known_at": ts(known_at)} if known_at else {})}
        rows = conn.execute(sql, params).fetchall()
        ladders: Dict[str, dict] = {}
        for r in conn.execute("SELECT product_code, bucket_code, share_e6 FROM dwh_behavioral_ladder_profile;"):
            ladders.setdefault(r[0], {})[r[1]] = r[2]
        buckets = [dict(r) for r in conn.execute("SELECT * FROM dwh_dim_time_bucket ORDER BY sort_order;")]
    finally:
        if own:
            conn.close()
    _, nsfr_lines = _regulatory_lines(rows, ladders, buckets)
    nsfr_lines = _aggregate_lines(nsfr_lines, 6)
    asf = sum((ln[5] for ln in nsfr_lines if ln[0] == "FED"), Decimal(0))
    rsf = sum((ln[5] for ln in nsfr_lines if ln[0] == "FER"), Decimal(0))
    df = pd.DataFrame(nsfr_lines, columns=["componente", "categoria", "subcategoria", "monto_nominal",
                                           "factor_ponderacion", "monto_ponderado"])
    return reg.compute_nsfr(asf, rsf), df


def _param(conn, code: str) -> Decimal:
    return i2f(conn.execute("SELECT param_value_e6 FROM dwh_regulatory_parameter WHERE param_code = ?;", (code,)).fetchone()[0])


def get_c54_maturity_ladder(position_date: date, conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """C54: brechas de liquidez por banda (base contractual) con descalce acumulado y límites x capital básico."""
    own = conn is None
    conn = conn or get_connection()
    try:
        df = pd.read_sql_query(
            """SELECT b.sort_order, b.bucket_code, b.bucket_label,
                      SUM(CASE WHEN f.direction = 'INFLOW'  THEN f.total_e4 ELSE 0 END) AS inflows_e4,
                      SUM(CASE WHEN f.direction = 'OUTFLOW' THEN f.total_e4 ELSE 0 END) AS outflows_e4
               FROM dwh_dim_time_bucket b
               LEFT JOIN dwh_fact_contractual_cashflows f ON f.time_bucket_key = b.time_bucket_key
                    AND f.position_date_key = :dk AND f.include_in_liquidity = 1
                    AND f.restatement_seq = (SELECT MAX(s.restatement_seq) FROM dwh_fact_account_balance_snapshot s
                                             WHERE s.snapshot_date_key = f.position_date_key AND s.contract_key = f.contract_key)
               GROUP BY b.sort_order, b.bucket_code, b.bucket_label ORDER BY b.sort_order;""",
            conn, params={"dk": date_key(position_date)},
        )
        capital = _param(conn, "CAPITAL_BASICO_CLP")
        lim30 = _param(conn, "C54_LIMITE_DESCALCE_30D_X_CAPITAL")
        lim90 = _param(conn, "C54_LIMITE_DESCALCE_90D_X_CAPITAL")
    finally:
        if own:
            conn.close()
    df["ingresos"] = df["inflows_e4"].fillna(0).astype("int64") / E4
    df["egresos"] = df["outflows_e4"].fillna(0).astype("int64") / E4
    df["descalce"] = df["ingresos"] - df["egresos"]
    df["descalce_acumulado"] = df["descalce"].cumsum()
    df["limite"] = None
    df.loc[df["bucket_code"] == "B02", "limite"] = -float(capital * lim30)
    df.loc[df["bucket_code"] == "B03", "limite"] = -float(capital * lim90)
    df["cumple"] = df.apply(lambda r: "N/A" if r["limite"] is None else ("SÍ" if r["descalce_acumulado"] >= r["limite"] else "NO"), axis=1)
    return df[["bucket_code", "bucket_label", "ingresos", "egresos", "descalce", "descalce_acumulado", "limite", "cumple"]]


def _irrbb_shock(scenario: str, t: Decimal, p: Dict[str, Decimal]) -> Decimal:
    """Curvas de shock estándar BCBS 368 (parallel / short / steepener / flattener)."""
    decay = (-(t / Decimal(4))).exp()
    short = p["short"] * decay
    long_ = p["long"] * (Decimal(1) - decay)
    return {
        "PARALELO_ALZA": p["parallel"],
        "PARALELO_BAJA": -p["parallel"],
        "CORTO_ALZA": short,
        "CORTO_BAJA": -short,
        "EMPINAMIENTO": Decimal("-0.65") * abs(short) + Decimal("0.9") * abs(long_),
        "APLANAMIENTO": Decimal("0.8") * abs(short) - Decimal("0.6") * abs(long_),
    }[scenario]


def get_c56_irrbb(position_date: date, conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """
    C56: sensibilidad del valor económico (ΔEVE) y del margen financiero a 12
    meses (ΔNII) ante los 6 escenarios estándar. EVE se revalúa con factores de
    descuento continuos por banda; NII usa la brecha de repricing < 1 año.
    """
    own = conn is None
    conn = conn or get_connection()
    try:
        rows = conn.execute(
            """SELECT b.bucket_code, b.midpoint_days, y.zero_rate_e6,
                      SUM(CASE WHEN f.direction = 'INFLOW' THEN f.total_e4 ELSE -f.total_e4 END) AS net_e4,
                      SUM(CASE WHEN f.cashflow_type IN ('PRINCIPAL','REPRICING_NOTIONAL')
                               THEN CASE WHEN f.direction = 'INFLOW' THEN f.principal_e4 ELSE -f.principal_e4 END ELSE 0 END) AS gap_e4
               FROM dwh_fact_contractual_cashflows f
               JOIN dwh_dim_time_bucket b ON b.time_bucket_key = f.time_bucket_key
               JOIN dwh_yield_curve_point y ON y.bucket_code = b.bucket_code AND y.currency_code = f.currency_code
               WHERE f.position_date_key = ? AND f.include_in_irrbb = 1
                 AND f.restatement_seq = (SELECT MAX(s.restatement_seq) FROM dwh_fact_account_balance_snapshot s
                                          WHERE s.snapshot_date_key = f.position_date_key AND s.contract_key = f.contract_key)
               GROUP BY b.bucket_code, b.midpoint_days, y.zero_rate_e6 ORDER BY b.sort_order;""",
            (date_key(position_date),),
        ).fetchall()
        p = {"parallel": _param(conn, "IRRBB_SHOCK_PARALLEL"), "short": _param(conn, "IRRBB_SHOCK_SHORT"),
             "long": _param(conn, "IRRBB_SHOCK_LONG")}
        tier1 = _param(conn, "CAPITAL_BASICO_CLP")
        outlier = _param(conn, "IRRBB_OUTLIER_EVE_PCT_TIER1")
    finally:
        if own:
            conn.close()
    out = []
    for sc in ("PARALELO_ALZA", "PARALELO_BAJA", "CORTO_ALZA", "CORTO_BAJA", "EMPINAMIENTO", "APLANAMIENTO"):
        d_eve, d_nii = Decimal(0), Decimal(0)
        for r in rows:
            t = Decimal(r["midpoint_days"]) / Decimal(365)
            base = i2f(r["zero_rate_e6"])
            shock = _irrbb_shock(sc, t, p)
            cf = i2m(r["net_e4"])
            d_eve += cf * ((-(base + shock) * t).exp() - (-base * t).exp())
            if r["midpoint_days"] <= 365:
                d_nii += i2m(r["gap_e4"]) * shock * (Decimal(1) - t)
        d_eve = reg.to_money(d_eve)
        out.append({"escenario": sc, "delta_eve": float(d_eve), "delta_nii_12m": float(reg.to_money(d_nii)),
                    "delta_eve_pct_tier1": float(d_eve / tier1 * 100) if tier1 else None,
                    "valor_atipico": "SÍ" if tier1 and abs(d_eve) / tier1 > outlier else "NO"})
    return pd.DataFrame(out)


def get_c46_provisions(snapshot_date: date, reported_at: Optional[datetime] = None,
                       conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """C46: provisiones por deudor tal como se reportaron (al instante `reported_at`) vs reexpresadas hoy."""
    own = conn is None
    conn = conn or get_connection()
    try:
        df = pd.read_sql_query(
            """WITH ranked AS (
                   SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s.contract_key ORDER BY s.restatement_seq DESC) AS rn_today,
                          ROW_NUMBER() OVER (PARTITION BY s.contract_key
                                             ORDER BY CASE WHEN s.snapshot_asserted_at <= :rep THEN s.restatement_seq ELSE -1 END DESC) AS rn_rep
                   FROM dwh_fact_account_balance_snapshot s
                   JOIN dwh_dim_product pr ON pr.product_key = s.product_key AND pr.is_loan = 1
                   WHERE s.snapshot_date_key = :dk)
               SELECT c.contract_number, p.full_name AS deudor, p.tax_id AS rut, rb.cmf_rating_category AS categoria,
                      rb.provision_rate_e6, rep.balance_clp_e4 AS saldo_reportado_e4, rep.regulatory_provision_clp_e4 AS prov_reportada_e4,
                      tod.balance_clp_e4 AS saldo_hoy_e4, tod.regulatory_provision_clp_e4 AS prov_hoy_e4,
                      rep.restatement_seq AS version_reportada, tod.restatement_seq AS version_vigente
               FROM ranked tod
               JOIN ranked rep ON rep.contract_key = tod.contract_key AND rep.rn_rep = 1
               JOIN dwh_dim_contract c ON c.contract_key = tod.contract_key
               JOIN dwh_dim_partner p ON p.partner_key = tod.partner_key
               JOIN dwh_dim_risk_band rb ON rb.risk_band_key = tod.risk_band_key
               WHERE tod.rn_today = 1
               ORDER BY tod.balance_clp_e4 DESC;""",
            conn, params={"dk": date_key(snapshot_date), "rep": ts(reported_at or utc_now())},
        )
    finally:
        if own:
            conn.close()
    for c in ["saldo_reportado_e4", "prov_reportada_e4", "saldo_hoy_e4", "prov_hoy_e4"]:
        df[c[:-3]] = df[c].astype("int64") / E4
    df["tasa_provision"] = df["provision_rate_e6"] / E6
    df["delta_provision"] = df["prov_hoy"] - df["prov_reportada"]
    df["diagnostico"] = df.apply(lambda r: "REEXPRESADO_POST_ENVIO" if r["version_vigente"] != r["version_reportada"]
                                 else "CONSISTENTE", axis=1)
    return df


def get_point_in_time_gl_balances(business_date: date, conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """
    Saldo point-in-time desde el hecho transaccional (Kimball fig. 9.5): para
    cada (cuenta, contrato) se toma la última transacción final del día con
    posting_date_key <= fecha, sin escanear la historia completa.
    """
    own = conn is None
    conn = conn or get_connection()
    try:
        df = pd.read_sql_query(
            """SELECT g.account_code, g.account_name, COALESCE(c.contract_number, '(sin contrato)') AS contract_number,
                      f.posting_date_key AS ultima_fecha_movimiento, f.account_sequence_number, f.balance_after_e4
               FROM dwh_fact_gl_transaction_postings f
               JOIN dwh_dim_gl_account g ON g.gl_account_key = f.gl_account_key
               LEFT JOIN dwh_dim_contract c ON c.contract_key = f.contract_key
               WHERE f.is_final_daily_tx = 1
                 AND f.posting_date_key = (
                     SELECT MAX(f2.posting_date_key) FROM dwh_fact_gl_transaction_postings f2
                     WHERE f2.gl_account_key = f.gl_account_key AND f2.contract_key = f.contract_key
                       AND f2.is_final_daily_tx = 1 AND f2.posting_date_key <= :dk)
               ORDER BY g.account_code, contract_number;""",
            conn, params={"dk": date_key(business_date)},
        )
    finally:
        if own:
            conn.close()
    df["saldo"] = df["balance_after_e4"].astype("int64") / E4
    return df


# =============================================================================
# 9. CONSULTAS PARA LA INTERFAZ
# =============================================================================

def _read(sql: str, params=None, conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    own = conn is None
    conn = conn or get_connection()
    try:
        return pd.read_sql_query(sql, conn, params=params or {})
    finally:
        if own:
            conn.close()


def get_ledger_entries(limit: int = 50, exclude_daily: bool = True) -> pd.DataFrame:
    df = _read(
        f"""SELECT journal_entry_id, entry_uuid, idempotency_key, entry_kind, pending_entry_id, transaction_timestamp_utc,
                   transaction_timestamp_local, posting_date, value_date, source_system, transaction_code, description,
                   total_debit_e4, prev_entry_hash, entry_hash
            FROM oltp_journal_entry {"WHERE transaction_code <> 'MOV_NETO_DIARIO'" if exclude_daily else ""}
            ORDER BY journal_entry_id DESC LIMIT :limit;""", {"limit": int(limit)})
    df["total_debit"] = df["total_debit_e4"].astype("int64") / E4
    df["is_balanced"] = 1  # garantizado por CHECK (total_debit_e4 = total_credit_e4)
    return df


def count_ledger_entries() -> int:
    return int(_read("SELECT COUNT(*) AS n FROM oltp_journal_entry;")["n"].iloc[0])


def get_ledger_postings(journal_entry_id: Optional[int] = None, exclude_daily: bool = True, limit: int = 200) -> pd.DataFrame:
    where, params = [], {"limit": int(limit)}
    if journal_entry_id:
        where.append("p.journal_entry_id = :jid")
        params["jid"] = int(journal_entry_id)
    if exclude_daily:
        where.append("j.transaction_code <> 'MOV_NETO_DIARIO'")
    df = _read(
        f"""SELECT p.posting_id, p.journal_entry_id, p.line_number, p.account_id, p.contract_id, p.partner_id,
                   p.currency_code, p.posting_status, p.amount_debit_e4, p.amount_credit_e4, p.functional_debit_e4,
                   p.functional_credit_e4, p.functional_released_debit_e4, p.functional_released_credit_e4, p.fx_rate_e6, p.row_hash
            FROM oltp_ledger_posting p JOIN oltp_journal_entry j ON j.journal_entry_id = p.journal_entry_id
            {"WHERE " + " AND ".join(where) if where else ""}
            ORDER BY p.journal_entry_id DESC, p.line_number LIMIT :limit;""", params)
    return _core_display_frame(df).rename(columns={
        "functional_released_debit": "released_debit", "functional_released_credit": "released_credit"})


def get_account_balances() -> pd.DataFrame:
    df = _read(
        """SELECT b.account_id, a.account_name, b.contract_id, a.normal_balance,
                  b.debits_pending_e4, b.credits_pending_e4, b.debits_posted_e4, b.credits_posted_e4
           FROM oltp_account_balance b JOIN oltp_ledger_account a ON a.account_id = b.account_id
           ORDER BY b.account_id, b.contract_id;""")
    df = _core_display_frame(df)
    sign = df["normal_balance"].map({"D": 1, "C": -1})
    df["saldo_contable"] = sign * (df["debits_posted"] - df["credits_posted"])
    # Disponible = contable menos las reservas que lo reducen (haber pendiente en cuentas deudoras,
    # debe pendiente en cuentas acreedoras). Las reservas que lo aumentan no se consideran disponibles.
    df["saldo_disponible"] = df["saldo_contable"] - df["credits_pending"].where(sign == 1, df["debits_pending"])
    return df


def get_idempotency_records(limit: int = 30) -> pd.DataFrame:
    return _read(
        """SELECT idempotency_key, operation, status, journal_entry_id, substr(request_payload_hash, 1, 16) || '...' AS payload_hash,
                  response_json, created_at
           FROM oltp_idempotency_records WHERE idempotency_key NOT LIKE 'ALM-DAILY-%'
           ORDER BY created_at DESC LIMIT :limit;""", {"limit": int(limit)})


def get_outbox_status() -> dict:
    total = _read("SELECT COUNT(*) AS n FROM oltp_transactional_outbox;")["n"].iloc[0]
    ck = _read("SELECT consumer_name, last_outbox_id, updated_at FROM stg_outbox_checkpoint;")
    inbox = _read("SELECT COUNT(*) AS n FROM stg_event_inbox;")["n"].iloc[0]
    recent = _read(
        """SELECT outbox_id, event_type, aggregate_id, journal_entry_id, created_at FROM oltp_transactional_outbox
           WHERE event_type <> 'LEDGER.POSTED' OR journal_entry_id IN (
               SELECT journal_entry_id FROM oltp_journal_entry WHERE transaction_code <> 'MOV_NETO_DIARIO')
           ORDER BY outbox_id DESC LIMIT 15;""")
    return {"total_events": int(total), "checkpoints": ck, "inbox_events": int(inbox), "recent": recent}


def get_etl_execution_log(limit: int = 50) -> pd.DataFrame:
    df = _read(
        """SELECT batch_id, pipeline_name, target_layer, source_entity, target_entity, extract_start_time, extract_end_time,
                  records_extracted, records_loaded, records_rejected, status, reconciliation_diff_e4, error_message
           FROM stg_load_execution_log ORDER BY batch_id DESC LIMIT :limit;""", {"limit": int(limit)})
    df["reconciliation_diff"] = df["reconciliation_diff_e4"].map(lambda v: None if pd.isna(v) else int(v) / E4)
    return df.drop(columns=["reconciliation_diff_e4"])


def get_latest_reconciliation() -> pd.DataFrame:
    df = _read(
        """SELECT r.reconciliation_date, r.contract_id, c.contract_number, r.core_balance_e4, r.ledger_balance_e4,
                  r.dwh_balance_e4, r.diff_core_ledger_e4, r.diff_ledger_dwh_e4, r.status
           FROM stg_reconciliation_result r LEFT JOIN dwh_dim_contract c ON c.contract_id = r.contract_id
           WHERE r.batch_id = (SELECT MAX(batch_id) FROM stg_reconciliation_result)
           ORDER BY r.contract_id;""")
    for c in ["core_balance_e4", "ledger_balance_e4", "dwh_balance_e4", "diff_core_ledger_e4", "diff_ledger_dwh_e4"]:
        df[c[:-3]] = df[c].map(lambda v: None if pd.isna(v) else int(v) / E4)
    return df


def get_dwh_facts_and_dims(snapshot_date_key: Optional[int] = None):
    """Hechos de snapshot (última reexpresión) para contratos nominados + dimensiones de riesgo y socios."""
    df_facts = _read(
        """SELECT s.snapshot_date_key, c.contract_id, c.contract_number, p.full_name AS partner_name, p.tax_id AS partner_rut,
                  p.household_group_name, pr.product_name, rb.cmf_rating_category, rb.regulatory_status,
                  rb.provision_rate_e6, s.balance_clp_e4, s.regulatory_provision_clp_e4, s.undrawn_commitment_clp_e4,
                  s.pending_debits_clp_e4, t.hqla_level, t.treatment_code, s.restatement_seq, s.snapshot_asserted_at
           FROM dwh_fact_account_balance_snapshot s
           JOIN dwh_dim_contract c ON c.contract_key = s.contract_key
           JOIN dwh_dim_partner p ON p.partner_key = s.partner_key
           JOIN dwh_dim_product pr ON pr.product_key = s.product_key
           JOIN dwh_dim_risk_band rb ON rb.risk_band_key = s.risk_band_key
           JOIN dwh_dim_liquidity_treatment t ON t.liquidity_treatment_key = s.liquidity_treatment_key
           WHERE c.source_system = 'CORE_NOMINADO'
             AND s.snapshot_date_key IN (20241231, 20250131, (SELECT MAX(snapshot_date_key) FROM dwh_fact_account_balance_snapshot))
           ORDER BY s.snapshot_date_key DESC, c.contract_id, s.restatement_seq;""")
    df_facts = _core_display_frame(df_facts)
    df_facts["current_balance_clp"] = df_facts.get("balance_clp")
    df_facts["regulatory_provision"] = df_facts.get("regulatory_provision_clp")
    df_facts["is_hqla_eligible"] = (df_facts["hqla_level"] != "NO_HQLA").astype(int)
    df_risk = _core_display_frame(_read("SELECT * FROM dwh_dim_risk_band ORDER BY risk_band_key;"))
    df_partners = _read("SELECT * FROM dwh_dim_partner ORDER BY partner_key;")
    return df_facts, df_risk, df_partners


def get_contractual_cashflows(position_date: date, contract_numbers: Optional[List[str]] = None) -> pd.DataFrame:
    df = _read(
        """SELECT f.position_date_key, c.contract_number, f.cashflow_date_key, f.cashflow_type, f.direction,
                  b.bucket_label, f.days_to_cashflow, f.principal_e4, f.interest_e4, f.total_e4, f.present_value_clp_e4,
                  f.include_in_liquidity, f.include_in_irrbb, f.restatement_seq
           FROM dwh_fact_contractual_cashflows f
           JOIN dwh_dim_contract c ON c.contract_key = f.contract_key
           JOIN dwh_dim_time_bucket b ON b.time_bucket_key = f.time_bucket_key
           WHERE f.position_date_key = :dk AND c.source_system = 'CORE_NOMINADO'
             AND f.restatement_seq = (SELECT MAX(s.restatement_seq) FROM dwh_fact_account_balance_snapshot s
                                      WHERE s.snapshot_date_key = f.position_date_key AND s.contract_key = f.contract_key)
           ORDER BY c.contract_number, f.cashflow_date_key, f.cashflow_type;""", {"dk": date_key(position_date)})
    df = _core_display_frame(df)
    if contract_numbers:
        df = df[df["contract_number"].isin(contract_numbers)]
    return df


def get_dwh_row_counts() -> pd.DataFrame:
    tables = ["oltp_journal_entry", "oltp_ledger_posting", "oltp_idempotency_records", "oltp_transactional_outbox",
              "core_contract_profile", "dwh_fact_account_balance_snapshot", "dwh_fact_gl_transaction_postings",
              "dwh_fact_contractual_cashflows", "stg_load_execution_log"]
    return pd.DataFrame([{"tabla": t, "filas": int(_read(f"SELECT COUNT(*) AS n FROM {t};")["n"].iloc[0])} for t in tables])


def latest_snapshot_date() -> Optional[date]:
    v = _read("SELECT MAX(snapshot_date_key) AS k FROM dwh_fact_account_balance_snapshot;")["k"].iloc[0]
    return key_to_date(int(v)) if v and not pd.isna(v) else None
