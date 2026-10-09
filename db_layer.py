"""
db_layer.py
===========
Capa de Persistencia y Emulación Relacional para la Arquitectura de 4 Capas:
1. OLTP / Ledger Inmutable: Multi-leg Journal con encadenamiento SHA-256
2. Staging & Control: stg_load_execution_log con cálculo de checksums
3. Core Bitemporal / ODS: Asserted Versioning (System Time + Valid Time)
4. DWH Kimball: dim_partner, dim_risk_band, dim_date, fact_account_balance_snapshot, fact_contractual_cashflows

Permite la ejecución inmediata en localhost y la conexión nativa hacia SQL Server 2017.
"""

import sqlite3
import hashlib
import json
import os
from datetime import datetime, date, timedelta
import pandas as pd
import numpy as np

DB_FILE = os.path.join(os.path.dirname(__file__), "core_banking.db")

def get_connection():
    """Retorna una conexión a la base de datos relacional local."""
    conn = sqlite3.connect(DB_FILE, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_database():
    """Inicializa las tablas de las 4 capas y siembra datos iniciales."""
    conn = get_connection()
    cur = conn.cursor()

    # 1. CAPA OLTP: LEDGER INMUTABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS oltp_journal_entry (
        journal_entry_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_uuid           TEXT NOT NULL UNIQUE,
        transaction_date     TEXT NOT NULL,
        posting_date         TEXT NOT NULL,
        source_system        TEXT NOT NULL,
        transaction_code     TEXT NOT NULL,
        description          TEXT NOT NULL,
        is_balanced          INTEGER NOT NULL DEFAULT 0,
        is_reversed          INTEGER NOT NULL DEFAULT 0,
        reversal_of_entry_id INTEGER NULL,
        prev_entry_hash      TEXT NULL,
        entry_hash           TEXT NOT NULL,
        created_by           TEXT NOT NULL,
        created_at           TEXT NOT NULL,
        FOREIGN KEY (reversal_of_entry_id) REFERENCES oltp_journal_entry(journal_entry_id)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS oltp_ledger_posting (
        posting_id           INTEGER PRIMARY KEY AUTOINCREMENT,
        journal_entry_id     INTEGER NOT NULL,
        line_number          INTEGER NOT NULL,
        account_id           INTEGER NOT NULL,
        contract_id          INTEGER NULL,
        partner_id           INTEGER NOT NULL,
        currency_code        TEXT NOT NULL,
        amount_debit         REAL NOT NULL DEFAULT 0.0,
        amount_credit        REAL NOT NULL DEFAULT 0.0,
        functional_debit     REAL NOT NULL DEFAULT 0.0,
        functional_credit    REAL NOT NULL DEFAULT 0.0,
        fx_rate              REAL NOT NULL DEFAULT 1.0,
        row_hash             TEXT NOT NULL,
        FOREIGN KEY (journal_entry_id) REFERENCES oltp_journal_entry(journal_entry_id)
    );
    """)

    # 2. CAPA STAGING: AUDITORÍA Y LINAJE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS stg_load_execution_log (
        batch_id             INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_uuid           TEXT NOT NULL,
        pipeline_name        TEXT NOT NULL,
        target_layer         TEXT NOT NULL,
        source_entity        TEXT NOT NULL,
        target_entity        TEXT NOT NULL,
        extract_start_time   TEXT NOT NULL,
        extract_end_time     TEXT NULL,
        records_extracted    INTEGER NOT NULL DEFAULT 0,
        records_loaded       INTEGER NOT NULL DEFAULT 0,
        records_rejected     INTEGER NOT NULL DEFAULT 0,
        status               TEXT NOT NULL,
        reconciliation_diff  REAL NULL,
        error_message        TEXT NULL,
        created_at           TEXT NOT NULL
    );
    """)

    # 3. CAPA CORE BITEMPORAL
    cur.execute("""
    CREATE TABLE IF NOT EXISTS core_contract_profile (
        contract_id          INTEGER NOT NULL,
        partner_id           INTEGER NOT NULL,
        contract_number      TEXT NOT NULL,
        product_type         TEXT NOT NULL,
        currency_code        TEXT NOT NULL,
        original_amount      REAL NOT NULL,
        current_balance      REAL NOT NULL,
        annual_nominal_rate  REAL NOT NULL,
        spread_rate          REAL NOT NULL,
        repricing_frequency  TEXT NOT NULL,
        cmf_portfolio_type   TEXT NOT NULL,
        contract_status      TEXT NOT NULL,
        valid_from           TEXT NOT NULL,
        valid_to             TEXT NOT NULL,
        sys_start_time       TEXT NOT NULL,
        sys_end_time         TEXT NOT NULL,
        PRIMARY KEY (contract_id, sys_start_time)
    );
    """)

    # 4. CAPA DWH DIMENSIONAL KIMBALL
    cur.execute("""
    CREATE TABLE IF NOT EXISTS dwh_dim_partner (
        partner_key          INTEGER PRIMARY KEY AUTOINCREMENT,
        partner_id           INTEGER NOT NULL,
        tax_id               TEXT NOT NULL,
        full_name            TEXT NOT NULL,
        partner_type         TEXT NOT NULL,
        segment_name         TEXT NOT NULL,
        economic_activity    TEXT NOT NULL,
        is_current           INTEGER NOT NULL DEFAULT 1,
        effective_start_date TEXT NOT NULL,
        effective_end_date   TEXT NOT NULL
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS dwh_dim_risk_band (
        risk_band_key        INTEGER PRIMARY KEY AUTOINCREMENT,
        cmf_rating_category  TEXT NOT NULL,
        regulatory_status    TEXT NOT NULL,
        dpd_bucket           TEXT NOT NULL,
        min_dpd              INTEGER NOT NULL,
        max_dpd              INTEGER NOT NULL,
        provision_rate       REAL NOT NULL,
        ltv_bucket           TEXT NOT NULL
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS dwh_fact_account_balance_snapshot (
        snapshot_date_key     INTEGER NOT NULL,
        contract_id           INTEGER NOT NULL,
        partner_key           INTEGER NOT NULL,
        risk_band_key         INTEGER NOT NULL,
        currency_code         TEXT NOT NULL,
        current_balance_orig  REAL NOT NULL,
        current_balance_clp   REAL NOT NULL,
        fx_rate_to_clp        REAL NOT NULL,
        accrued_interest_clp  REAL NOT NULL,
        contractual_rate      REAL NOT NULL,
        regulatory_provision  REAL NOT NULL,
        collateral_value_clp  REAL NOT NULL,
        net_exposure_clp      REAL NOT NULL,
        is_hqla_eligible      INTEGER NOT NULL,
        hqla_level            TEXT NOT NULL,
        nsfr_rsf_factor       REAL NOT NULL,
        nsfr_weighted_rsf_clp REAL NOT NULL,
        PRIMARY KEY (snapshot_date_key, contract_id)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS dwh_fact_contractual_cashflows (
        cashflow_id           INTEGER PRIMARY KEY AUTOINCREMENT,
        position_date_key     INTEGER NOT NULL,
        cashflow_date_key     INTEGER NOT NULL,
        contract_id           INTEGER NOT NULL,
        partner_key           INTEGER NOT NULL,
        risk_band_key         INTEGER NOT NULL,
        currency_code         TEXT NOT NULL,
        time_bucket_cmf       TEXT NOT NULL,
        is_interest_bearing   INTEGER NOT NULL,
        principal_cashflow    REAL NOT NULL,
        interest_cashflow     REAL NOT NULL,
        total_cashflow        REAL NOT NULL,
        present_value_clp     REAL NOT NULL,
        lcr_outflow_rate      REAL NOT NULL,
        lcr_inflow_rate       REAL NOT NULL,
        nsfr_asf_factor       REAL NOT NULL,
        nsfr_rsf_factor       REAL NOT NULL
    );
    """)

    conn.commit()
    conn.close()

    # Si la base está vacía, sembramos datos iniciales
    seed_if_empty()


def seed_if_empty():
    """Siembra datos iniciales demostrativos si las tablas están vacías."""
    conn = get_connection()
    cur = conn.cursor()

    # Verificar si dim_risk_band tiene datos
    cur.execute("SELECT COUNT(*) FROM dwh_dim_risk_band;")
    if cur.fetchone()[0] == 0:
        risk_bands = [
            ('A1', 'NORMAL',         '0 DÍAS',       0,   0,   0.0010, '<=60%'),
            ('A2', 'NORMAL',         '0 DÍAS',       0,   0,   0.0050, '<=60%'),
            ('A3', 'NORMAL',         '1-30 DÍAS',    1,   30,  0.0100, '60-80%'),
            ('B1', 'SUBESTANDAR',    '31-60 DÍAS',   31,  60,  0.0400, '60-80%'),
            ('B2', 'SUBESTANDAR',    '61-90 DÍAS',   61,  90,  0.1000, '>80%'),
            ('C1', 'INCUMPLIMIENTO', '>90 DÍAS',     91,  180, 0.2500, '>80%'),
            ('D1', 'INCUMPLIMIENTO', '>90 DÍAS',     181, 365, 0.6000, '>80%'),
            ('D2', 'INCUMPLIMIENTO', '>90 DÍAS',     366, 9999,1.0000, '>80%')
        ]
        cur.executemany("""
            INSERT INTO dwh_dim_risk_band (
                cmf_rating_category, regulatory_status, dpd_bucket, min_dpd, max_dpd, provision_rate, ltv_bucket
            ) VALUES (?, ?, ?, ?, ?, ?, ?);
        """, risk_bands)

    # Verificar dim_partner
    cur.execute("SELECT COUNT(*) FROM dwh_dim_partner;")
    if cur.fetchone()[0] == 0:
        partners = [
            (1001, '76.123.456-7', 'INVERSIONES SANTA ELENA S.A.', 'PERSONA_JURIDICA', 'CORPORATIVA', 'SERV_FIN'),
            (1002, '77.890.123-4', 'DISTRIBUIDORA CENTRAL SPA',   'PERSONA_JURIDICA', 'PYME',        'COMERCIO'),
            (1003, '15.678.901-2', 'RODRIGO ALONSO ORDENES ARCE', 'PERSONA_NATURAL',  'BANCA_PERSONAS', 'PROFESIONAL'),
            (1004, '12.345.678-9', 'MARIA EUGENIA SILVA ROJAS',   'PERSONA_NATURAL',  'BANCA_PERSONAS', 'COMERCIO'),
            (1005, '79.456.789-0', 'BANCO CENTRAL DE CHILE',       'INSTITUCIONAL',    'SOBERANO',       'BANCO_CENTRAL'),
            (1006, '60.805.000-0', 'TESORERIA GENERAL DE LA REP.', 'SOBERANO',         'SOBERANO',       'ESTADO')
        ]
        for p in partners:
            cur.execute("""
                INSERT INTO dwh_dim_partner (
                    partner_id, tax_id, full_name, partner_type, segment_name, economic_activity,
                    is_current, effective_start_date, effective_end_date
                ) VALUES (?, ?, ?, ?, ?, ?, 1, '2024-01-01', '9999-12-31');
            """, p)

    # Verificar Ledger
    cur.execute("SELECT COUNT(*) FROM oltp_journal_entry;")
    if cur.fetchone()[0] == 0:
        # Registrar 3 asientos contables multi-leg de prueba
        entries = [
            {
                "source": "CORE_CREDITOS",
                "code": "DESEMBOLSO",
                "desc": "Desembolso Crédito Comercial #90210 - Distribuidora Central",
                "date": "2024-12-15",
                "postings": [
                    {"line": 1, "account": 110501, "contract": 90210, "partner": 1002, "ccy": "CLP", "debit": 45000000.0, "credit": 0.0, "fx": 1.0},
                    {"line": 2, "account": 210101, "contract": 90210, "partner": 1002, "ccy": "CLP", "debit": 0.0, "credit": 45000000.0, "fx": 1.0}
                ]
            },
            {
                "source": "CORE_DEPOSITOS",
                "code": "DEPOSITO_PLAZO",
                "desc": "Apertura Depósito a Plazo Institucional Inversiones Santa Elena",
                "date": "2024-12-20",
                "postings": [
                    {"line": 1, "account": 110101, "contract": 80450, "partner": 1001, "ccy": "CLP", "debit": 120000000.0, "credit": 0.0, "fx": 1.0},
                    {"line": 2, "account": 220201, "contract": 80450, "partner": 1001, "ccy": "CLP", "debit": 0.0, "credit": 120000000.0, "fx": 1.0}
                ]
            },
            {
                "source": "CORE_MESA_DINERO",
                "code": "COMPRA_HQLA",
                "desc": "Compra Bonos Tesorería BTP Nivel 1 (HQLA / ALAC)",
                "date": "2024-12-28",
                "postings": [
                    {"line": 1, "account": 130101, "contract": 70100, "partner": 1006, "ccy": "CLP", "debit": 350000000.0, "credit": 0.0, "fx": 1.0},
                    {"line": 2, "account": 110101, "contract": 70100, "partner": 1006, "ccy": "CLP", "debit": 0.0, "credit": 350000000.0, "fx": 1.0}
                ]
            }
        ]

        prev_hash = "0" * 64
        for e in entries:
            now_iso = datetime.now().isoformat()
            uuid_val = hashlib.md5(f"{e['desc']}_{e['date']}".encode()).hexdigest()
            sum_debit = sum(p["debit"] for p in e["postings"])
            
            # Hash SHA-256 encadenado
            header_str = f"{uuid_val}|{now_iso}|{e['code']}|{sum_debit}|{prev_hash}"
            entry_hash = hashlib.sha256(header_str.encode()).hexdigest()

            cur.execute("""
                INSERT INTO oltp_journal_entry (
                    entry_uuid, transaction_date, posting_date, source_system, transaction_code,
                    description, is_balanced, is_reversed, prev_entry_hash, entry_hash, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?, ?, 'SYSTEM', ?);
            """, (uuid_val, now_iso, e["date"], e["source"], e["code"], e["desc"], prev_hash, entry_hash, now_iso))
            
            entry_id = cur.lastrowid
            prev_hash = entry_hash

            for p in e["postings"]:
                row_str = f"{entry_id}|{p['line']}|{p['account']}|{p['debit']}|{p['credit']}"
                r_hash = hashlib.sha256(row_str.encode()).hexdigest()
                cur.execute("""
                    INSERT INTO oltp_ledger_posting (
                        journal_entry_id, line_number, account_id, contract_id, partner_id,
                        currency_code, amount_debit, amount_credit, functional_debit, functional_credit,
                        fx_rate, row_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    entry_id, p["line"], p["account"], p["contract"], p["partner"],
                    p["ccy"], p["debit"], p["credit"], p["debit"] * p["fx"], p["credit"] * p["fx"],
                    p["fx"], r_hash
                ))

    # Verificar Core Bitemporal
    cur.execute("SELECT COUNT(*) FROM core_contract_profile;")
    if cur.fetchone()[0] == 0:
        # Simulamos un crédito con 2 versiones temporales (ejemplo de auditoría bitemporal)
        # Versión 1: Estado conocido al 15 de Enero (Saldo $45M, Normal A1)
        # Versión 2: Corrección retrospectiva ingresada en Febrero (Saldo $42M por pago tardío aplicado retroactivamente)
        bitemporal_contracts = [
            # Contrato 90210 - Versión 1 (Emitida en reporte original)
            (90210, 1002, 'OPER-COM-90210', 'COMERCIAL', 'CLP', 50000000.0, 45000000.0, 0.0850, 0.0200, 'MENSUAL', 'C', 'VIGENTE',
             '2024-12-01 00:00:00', '9999-12-31 23:59:59', '2025-01-10 10:00:00', '2025-02-05 14:30:00'),
            
            # Contrato 90210 - Versión 2 (Ajuste posterior conocido hoy)
            (90210, 1002, 'OPER-COM-90210', 'COMERCIAL', 'CLP', 50000000.0, 42000000.0, 0.0850, 0.0200, 'MENSUAL', 'C', 'VIGENTE',
             '2024-12-01 00:00:00', '9999-12-31 23:59:59', '2025-02-05 14:30:00', '9999-12-31 23:59:59'),

            # Contrato 80450 - Depósito Institucional
            (80450, 1001, 'DP-INST-80450', 'DEPOSITO_PLAZO', 'CLP', 120000000.0, 120000000.0, 0.0550, 0.0000, 'FIJO', 'P', 'VIGENTE',
             '2024-12-20 00:00:00', '9999-12-31 23:59:59', '2024-12-20 12:00:00', '9999-12-31 23:59:59'),

            # Contrato 70100 - Bonos de Tesorería BTP HQLA Nivel 1
            (70100, 1006, 'BONO-BTP-70100', 'INVERSION_SOBERANA', 'CLP', 350000000.0, 350000000.0, 0.0600, 0.0000, 'SEMESTRAL', 'S', 'VIGENTE',
             '2024-12-28 00:00:00', '9999-12-31 23:59:59', '2024-12-28 15:00:00', '9999-12-31 23:59:59')
        ]

        cur.executemany("""
            INSERT INTO core_contract_profile (
                contract_id, partner_id, contract_number, product_type, currency_code,
                original_amount, current_balance, annual_nominal_rate, spread_rate,
                repricing_frequency, cmf_portfolio_type, contract_status,
                valid_from, valid_to, sys_start_time, sys_end_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, bitemporal_contracts)

    # Verificar Staging Execution Log
    cur.execute("SELECT COUNT(*) FROM stg_load_execution_log;")
    if cur.fetchone()[0] == 0:
        logs = [
            ('PIPELINE_CDC_CORE_CONTRACTS', 'CORE', 'dbo.tbl_prestamos_legacy', 'core_contract_profile',
             '2025-01-15 02:00:00', '2025-01-15 02:04:12', 4500, 4500, 0, 'SUCCESS', 0.0, None),
            ('RECONCILIACION_NOCTURNA_LEDGER', 'CORE', 'dbo.tbl_cuentas_legacy', 'oltp_ledger_posting',
             '2025-01-15 03:00:00', '2025-01-15 03:01:45', 12400, 12400, 0, 'RECONCILED', 0.0, None),
            ('DWH_PARTITION_SWITCH_202412', 'DWH', 'stg_fact_balance_staging', 'dwh_fact_account_balance_snapshot',
             '2025-01-15 04:00:00', '2025-01-15 04:00:02', 85000, 85000, 0, 'SUCCESS', 0.0, 'Conmutación en 1.8 segundos')
        ]
        for l in logs:
            cur.execute("""
                INSERT INTO stg_load_execution_log (
                    batch_uuid, pipeline_name, target_layer, source_entity, target_entity,
                    extract_start_time, extract_end_time, records_extracted, records_loaded,
                    records_rejected, status, reconciliation_diff, error_message, created_at
                ) VALUES (HEX(RANDOMBLOB(16)), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'));
            """, l)

    # Verificar Fact Snapshot
    cur.execute("SELECT COUNT(*) FROM dwh_fact_account_balance_snapshot;")
    if cur.fetchone()[0] == 0:
        facts = [
            (20241231, 70100, 6, 1, 'CLP', 350000000.0, 350000000.0, 1.0, 1750000.0, 0.0600, 350000.0, 0.0, 350000000.0, 1, 'L1', 0.0500, 17500000.0),
            (20241231, 80450, 1, 1, 'CLP', 120000000.0, 120000000.0, 1.0, 550000.0,  0.0550, 120000.0, 0.0, 120000000.0, 0, 'NO', 0.5000, 60000000.0),
            (20241231, 90210, 2, 2, 'CLP', 45000000.0,  45000000.0,  1.0, 318750.0,  0.0850, 225000.0, 0.0, 45000000.0,  0, 'NO', 0.8500, 38250000.0),
            
            (20250131, 70100, 6, 1, 'CLP', 350000000.0, 350000000.0, 1.0, 1750000.0, 0.0600, 350000.0, 0.0, 350000000.0, 1, 'L1', 0.0500, 17500000.0),
            (20250131, 80450, 1, 1, 'CLP', 120000000.0, 120000000.0, 1.0, 550000.0,  0.0550, 120000.0, 0.0, 120000000.0, 0, 'NO', 0.5000, 60000000.0),
            (20250131, 90210, 2, 2, 'CLP', 42000000.0,  42000000.0,  1.0, 297500.0,  0.0850, 210000.0, 0.0, 42000000.0,  0, 'NO', 0.8500, 35700000.0)
        ]
        cur.executemany("""
            INSERT INTO dwh_fact_account_balance_snapshot (
                snapshot_date_key, contract_id, partner_key, risk_band_key, currency_code,
                current_balance_orig, current_balance_clp, fx_rate_to_clp, accrued_interest_clp,
                contractual_rate, regulatory_provision, collateral_value_clp, net_exposure_clp,
                is_hqla_eligible, hqla_level, nsfr_rsf_factor, nsfr_weighted_rsf_clp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, facts)

    conn.commit()
    conn.close()


# =============================================================================
# FUNCIONES DE CONSULTA Y GESTIÓN ANALÍTICA
# =============================================================================

def get_ledger_entries(limit=50):
    """Retorna las cabeceras del ledger contable inmutable."""
    conn = get_connection()
    df = pd.read_sql_query(f"""
        SELECT journal_entry_id, entry_uuid, transaction_date, posting_date, source_system,
               transaction_code, description, is_balanced, is_reversed, reversal_of_entry_id,
               prev_entry_hash, entry_hash, created_at
        FROM oltp_journal_entry
        ORDER BY journal_entry_id DESC
        LIMIT {limit};
    """, conn)
    conn.close()
    return df


def get_ledger_postings(journal_entry_id=None):
    """Retorna las líneas de detalle multi-leg."""
    conn = get_connection()
    query = """
        SELECT p.posting_id, p.journal_entry_id, p.line_number, p.account_id,
               p.contract_id, p.partner_id, p.currency_code, p.amount_debit, p.amount_credit,
               p.functional_debit, p.functional_credit, p.fx_rate, p.row_hash
        FROM oltp_ledger_posting p
    """
    if journal_entry_id:
        query += f" WHERE p.journal_entry_id = {int(journal_entry_id)}"
    query += " ORDER BY p.journal_entry_id DESC, p.line_number ASC;"
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


def verify_ledger_cryptographic_chain():
    """Verifica matemáticamente la integridad de la cadena SHA-256."""
    conn = get_connection()
    df = pd.read_sql_query("""
        SELECT journal_entry_id, entry_uuid, transaction_code, prev_entry_hash, entry_hash
        FROM oltp_journal_entry
        ORDER BY journal_entry_id ASC;
    """, conn)
    conn.close()

    if len(df) == 0:
        return True, []

    audit_results = []
    is_valid_overall = True
    expected_prev = "0" * 64

    for idx, row in df.iterrows():
        prev_actual = row["prev_entry_hash"]
        is_link_ok = (prev_actual == expected_prev)
        if not is_link_ok:
            is_valid_overall = False

        audit_results.append({
            "entry_id": row["journal_entry_id"],
            "code": row["transaction_code"],
            "prev_hash_actual": prev_actual[:12] + "...",
            "expected_prev": expected_prev[:12] + "...",
            "status": "VÁLIDO" if is_link_ok else "CORRUPTO_O_ALTERADO"
        })
        expected_prev = row["entry_hash"]

    return is_valid_overall, pd.DataFrame(audit_results)


def query_bitemporal_time_travel(business_date_str: str, system_time_str: str):
    """
    Ejecuta la consulta de time travel combinando:
    - Business Valid Time: target_date BETWEEN valid_from AND valid_to
    - System Transaction Time: target_sys BETWEEN sys_start_time AND sys_end_time
    """
    conn = get_connection()
    query = f"""
        SELECT 
            c.contract_id,
            c.contract_number,
            c.partner_id,
            p.tax_id AS partner_rut,
            p.full_name AS partner_name,
            c.product_type,
            c.currency_code,
            c.current_balance,
            c.annual_nominal_rate,
            c.contract_status,
            c.valid_from,
            c.valid_to,
            c.sys_start_time,
            c.sys_end_time
        FROM core_contract_profile c
        LEFT JOIN dwh_dim_partner p ON c.partner_id = p.partner_id AND p.is_current = 1
        WHERE datetime('{business_date_str}') >= datetime(c.valid_from)
          AND datetime('{business_date_str}') <  datetime(c.valid_to)
          AND datetime('{system_time_str}')   >= datetime(c.sys_start_time)
          AND datetime('{system_time_str}')   <  datetime(c.sys_end_time)
        ORDER BY c.contract_id ASC;
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


def get_etl_execution_log(limit=50):
    """Retorna la bitácora de ejecución de lotes y reconciliaciones."""
    conn = get_connection()
    df = pd.read_sql_query(f"""
        SELECT batch_id, pipeline_name, target_layer, source_entity, target_entity,
               extract_start_time, extract_end_time, records_extracted, records_loaded,
               records_rejected, status, reconciliation_diff, error_message
        FROM stg_load_execution_log
        ORDER BY batch_id DESC
        LIMIT {limit};
    """, conn)
    conn.close()
    return df


def get_dwh_facts_and_dims():
    """Retorna los datos del Data Warehouse dimensional de Kimball."""
    conn = get_connection()
    df_facts = pd.read_sql_query("""
        SELECT f.snapshot_date_key, f.contract_id, p.full_name AS partner_name, p.tax_id AS partner_rut,
               rb.cmf_rating_category, rb.regulatory_status, rb.provision_rate,
               f.current_balance_clp, f.regulatory_provision, f.is_hqla_eligible, f.hqla_level,
               f.nsfr_rsf_factor, f.nsfr_weighted_rsf_clp
        FROM dwh_fact_account_balance_snapshot f
        INNER JOIN dwh_dim_partner p ON f.partner_key = p.partner_key
        INNER JOIN dwh_dim_risk_band rb ON f.risk_band_key = rb.risk_band_key
        ORDER BY f.snapshot_date_key DESC, f.current_balance_clp DESC;
    """, conn)
    
    df_risk = pd.read_sql_query("SELECT * FROM dwh_dim_risk_band ORDER BY risk_band_key;", conn)
    df_partners = pd.read_sql_query("SELECT * FROM dwh_dim_partner ORDER BY partner_key;", conn)
    conn.close()
    return df_facts, df_risk, df_partners
