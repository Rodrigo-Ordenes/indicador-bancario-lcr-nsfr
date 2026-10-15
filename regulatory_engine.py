"""
regulatory_engine.py
====================
Motor de cálculo regulatorio puro (sin I/O) para LCR y NSFR bajo Basilea III
(BCBS 238 "Basel III: The Liquidity Coverage Ratio", enero 2013, y BCBS 295
"Basel III: the net stable funding ratio", octubre 2014), tal como se aplican en
la normativa de liquidez CMF / BCCh.

Diseño:
- Toda la aritmética monetaria usa `decimal.Decimal` con redondeo bancario
  (ROUND_HALF_EVEN) a 4 decimales, igual que DECIMAL(19,4) en SQL Server.
  Nunca se usa float para montos: los float sólo aparecen al final, en la capa
  de presentación (DataFrames para Plotly).
- Las mismas fórmulas están implementadas en T-SQL en
  `sql/05_regulatory_time_travel_audit.sql` (vista dwh.vw_cmf_c55_lcr_nsfr_official);
  este módulo es la referencia ejecutable y se usa para validar cruzadamente
  ambos resultados en `data_loader.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from decimal import Decimal, ROUND_HALF_EVEN, getcontext
from typing import Optional

# Precisión suficiente para fracciones como 15/85 sin pérdida antes del redondeo final.
getcontext().prec = 38

MONEY_QUANTUM = Decimal("0.0001")   # DECIMAL(19,4)
FACTOR_QUANTUM = Decimal("0.000001")  # DECIMAL(12,6)
ZERO = Decimal("0")

# -----------------------------------------------------------------------------
# Parámetros normativos Basilea III (BCBS 238 párrafos 47-54 y Anexo 1)
# -----------------------------------------------------------------------------
HAIRCUT_L2A = Decimal("0.15")          # Párrafo 52: descuento mínimo 15% Nivel 2A
HAIRCUT_L2B_DEFAULT = Decimal("0.50")  # Párrafo 54: acciones / bonos corporativos 2B
CAP_LEVEL2_TOTAL = Decimal("0.40")     # Párrafo 51: Nivel 2 <= 40% del stock HQLA
CAP_LEVEL2B = Decimal("0.15")          # Párrafo 53: Nivel 2B <= 15% del stock HQLA
CAP_INFLOWS = Decimal("0.75")          # Párrafo 69: entradas <= 75% de salidas brutas
NSFR_RSF_UNDRAWN_COMMITMENTS = Decimal("0.05")  # BCBS 295 párr. 47: 5% líneas no utilizadas


def to_money(value) -> Decimal:
    """Convierte cualquier entrada numérica a Decimal con escala 4 (sin pasar por float binario)."""
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    else:
        # str() evita arrastrar el error binario de un float (0.1 -> '0.1').
        d = Decimal(str(value))
    return d.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)


def to_factor(value) -> Decimal:
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return d.quantize(FACTOR_QUANTUM, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class LCRResult:
    """Desglose completo del cálculo LCR, en la misma moneda de las entradas."""
    level1: Decimal                 # Nivel 1 (valor de mercado, sin descuento)
    level2a_post_haircut: Decimal   # Nivel 2A tras descuento
    level2b_post_haircut: Decimal   # Nivel 2B tras descuento
    adjustment_15pct_cap: Decimal   # Ajuste por tope 15% Nivel 2B (Anexo 1)
    adjustment_40pct_cap: Decimal   # Ajuste por tope 40% Nivel 2 (Anexo 1)
    hqla: Decimal                   # ALAC computable
    gross_outflows: Decimal         # Salidas ponderadas a 30 días
    gross_inflows: Decimal          # Entradas ponderadas a 30 días (antes del tope)
    capped_inflows: Decimal         # min(entradas, 75% salidas)
    inflow_cap_adjustment: Decimal  # Entradas descartadas por el tope del 75%
    net_cash_outflows: Decimal      # NAF
    lcr_pct: Optional[Decimal]      # ALAC / NAF * 100 (None si NAF = 0)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class NSFRResult:
    available_stable_funding: Decimal  # FED
    required_stable_funding: Decimal   # FER
    nsfr_pct: Optional[Decimal]

    def as_dict(self) -> dict:
        return asdict(self)


def compute_hqla_with_caps(level1, level2a_post_haircut, level2b_post_haircut):
    """
    Stock de HQLA con los topes de composición del Anexo 1 de BCBS 238.

    Los topes se expresan sobre el stock total (L2 <= 40%, L2B <= 15%), lo que
    algebraicamente equivale a:
        Adj15 = max(L2B - 15/85 * (L1 + L2A), L2B - 15/60 * L1, 0)
        Adj40 = max(L2A + L2B - Adj15 - 2/3 * L1, 0)
        HQLA  = L1 + L2A + L2B - Adj15 - Adj40

    Nota: el Anexo 1 usa montos de Nivel 1/2 "ajustados" por el deshacer de
    operaciones de financiamiento garantizado (repos/reverse repos < 30 días).
    Si la entidad mantiene esas operaciones, las entradas de esta función deben
    venir ya ajustadas desde la vista de componentes.
    """
    l1 = to_money(level1)
    l2a = to_money(level2a_post_haircut)
    l2b = to_money(level2b_post_haircut)

    adj15 = max(
        l2b - Decimal(15) / Decimal(85) * (l1 + l2a),
        l2b - Decimal(15) / Decimal(60) * l1,
        ZERO,
    ).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)

    adj40 = max(
        l2a + l2b - adj15 - Decimal(2) / Decimal(3) * l1,
        ZERO,
    ).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)

    hqla = (l1 + l2a + l2b - adj15 - adj40).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)
    return l1, l2a, l2b, adj15, adj40, hqla


def compute_lcr(level1, level2a_post_haircut, level2b_post_haircut,
                gross_outflows, gross_inflows) -> LCRResult:
    """LCR = HQLA / (Salidas - min(Entradas, 75% * Salidas))  [BCBS 238 párr. 69]."""
    l1, l2a, l2b, adj15, adj40, hqla = compute_hqla_with_caps(
        level1, level2a_post_haircut, level2b_post_haircut
    )
    out = to_money(gross_outflows)
    inf = to_money(gross_inflows)
    if out < ZERO or inf < ZERO:
        raise ValueError("Salidas y entradas ponderadas deben ser montos no negativos.")

    cap = (CAP_INFLOWS * out).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)
    capped_inflows = min(inf, cap)
    nco = out - capped_inflows
    lcr_pct = None
    if nco > ZERO:
        lcr_pct = (hqla / nco * Decimal(100)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)

    return LCRResult(
        level1=l1,
        level2a_post_haircut=l2a,
        level2b_post_haircut=l2b,
        adjustment_15pct_cap=adj15,
        adjustment_40pct_cap=adj40,
        hqla=hqla,
        gross_outflows=out,
        gross_inflows=inf,
        capped_inflows=capped_inflows,
        inflow_cap_adjustment=inf - capped_inflows,
        net_cash_outflows=nco,
        lcr_pct=lcr_pct,
    )


def compute_nsfr(available_stable_funding, required_stable_funding) -> NSFRResult:
    asf = to_money(available_stable_funding)
    rsf = to_money(required_stable_funding)
    pct = None
    if rsf > ZERO:
        pct = (asf / rsf * Decimal(100)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)
    return NSFRResult(available_stable_funding=asf, required_stable_funding=rsf, nsfr_pct=pct)


def nsfr_factor_for_residual_maturity(residual_days: Optional[int],
                                      factor_lt_6m, factor_6m_1y, factor_ge_1y) -> Decimal:
    """
    Selecciona el factor ASF/RSF según el vencimiento residual (BCBS 295 usa las
    bandas < 6 meses, 6 meses a < 1 año y >= 1 año). Un instrumento sin
    vencimiento contractual (capital, depósitos a la vista, activo fijo) recibe
    `None` y se trata como >= 1 año salvo que la tabla de tratamiento indique
    otro factor para esa banda.
    """
    if residual_days is None:
        return to_factor(factor_ge_1y)
    if residual_days < 182:
        return to_factor(factor_lt_6m)
    if residual_days < 365:
        return to_factor(factor_6m_1y)
    return to_factor(factor_ge_1y)


def weighted(amount, factor) -> Decimal:
    """Monto ponderado = monto * factor, redondeado a escala monetaria."""
    return (to_money(amount) * to_factor(factor)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)
