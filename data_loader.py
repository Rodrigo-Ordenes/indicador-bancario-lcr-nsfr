"""
data_loader.py
==============
Carga de los 4 DataFrames del dashboard ALM a partir de la base de datos
relacional, aplicando las fórmulas exactas de Basilea III (`regulatory_engine`):
topes de 40% Nivel 2, 15% Nivel 2B y 75% de entradas.

DataFrames (montos en MM$ = millones de CLP):
1. df_lcr_historico : [fecha, lcr, alac, naf]
2. df_lcr_detalle   : [fecha, componente, categoria, subcategoria, monto_nominal,
                       factor_ponderacion, monto_ponderado, fuente]
3. df_nsfr_historico: [fecha, nsfr, fed, fer]
4. df_nsfr_detalle  : [fecha, componente, categoria, subcategoria, monto_nominal,
                       factor_ponderacion, monto_ponderado, fuente]

Invariante garantizado en todos los modos: para cada fecha, la suma de
`monto_ponderado` del detalle es exactamente el ALAC / NAF / FED / FER del
histórico. Los topes regulatorios aparecen como líneas de ajuste explícitas.

Fuente de datos (variable de entorno INDICADOR_DATA_SOURCE):
- HIBRIDO   (por defecto): SQL Server si CORE_BANKING_SQLSERVER_URL está
              definida, si no la base local `core_banking.db`. Si la historia
              disponible es menor a HISTORY_DAYS, las fechas anteriores se
              completan con un backfill calibrado sobre la primera fecha real
              (marcado con fuente = 'BACKFILL_CALIBRADO').
- SQLITE    : sólo la base local (db_layer.py).
- SQLSERVER : sólo SQL Server (vistas de sql/05_regulatory_time_travel_audit.sql).
- SINTETICO : series sintéticas a nivel de componente, también pasadas por el
              motor regulatorio (útil sin base de datos).
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st

import regulatory_engine as reg

log = logging.getLogger(__name__)

HISTORY_DAYS = 365
DETAIL_DAYS = 800   # detalle diario completo: comparaciones anuales y volatilidad por componente
MM = Decimal(1_000_000)
DETAIL_COLUMNS = ["fecha", "componente", "categoria", "subcategoria", "monto_nominal",
                  "factor_ponderacion", "monto_ponderado", "fuente"]


def _mm(value: Decimal) -> float:
    """CLP Decimal -> MM$ float (sólo para presentación; sin redondeo intermedio para no romper sumas)."""
    return float(value / MM)


def _pct(value: Optional[Decimal]) -> float:
    return float(value) if value is not None else float("nan")


# =============================================================================
# 1. MODO BASE LOCAL (SQLite vía db_layer)
# =============================================================================

def _bundle_from_sqlite() -> Dict[str, pd.DataFrame]:
    import db_layer

    db_layer.init_database()
    series = db_layer.get_regulatory_series(detail_days=DETAIL_DAYS)
    lcr_hist = pd.DataFrame([{
        "fecha": pd.Timestamp(h["fecha"]),
        "lcr": round(_pct(h["result"].lcr_pct), 4),
        "alac": _mm(h["result"].hqla),
        "naf": _mm(h["result"].net_cash_outflows),
    } for h in series["lcr_hist"]])
    nsfr_hist = pd.DataFrame([{
        "fecha": pd.Timestamp(h["fecha"]),
        "nsfr": round(_pct(h["result"].nsfr_pct), 4),
        "fed": _mm(h["result"].available_stable_funding),
        "fer": _mm(h["result"].required_stable_funding),
    } for h in series["nsfr_hist"]])
    lcr_det = pd.DataFrame([{
        "fecha": pd.Timestamp(f), "componente": comp, "categoria": cat, "subcategoria": sub,
        "monto_nominal": _mm(nom), "factor_ponderacion": float(fac), "monto_ponderado": _mm(w), "fuente": "CORE_DB",
    } for f, comp, cat, sub, nom, fac, w in series["lcr_det"]], columns=DETAIL_COLUMNS)
    nsfr_det = pd.DataFrame([{
        "fecha": pd.Timestamp(f), "componente": comp, "categoria": cat, "subcategoria": sub,
        "monto_nominal": _mm(nom), "factor_ponderacion": float(fac), "monto_ponderado": _mm(w), "fuente": "CORE_DB",
    } for f, comp, cat, sub, nom, fac, w in series["nsfr_det"]], columns=DETAIL_COLUMNS)
    return {"lcr_hist": lcr_hist, "lcr_det": lcr_det, "nsfr_hist": nsfr_hist, "nsfr_det": nsfr_det}


# =============================================================================
# 2. MODO SQL SERVER (vistas del DWH de producción)
# =============================================================================

SQLSERVER_LCR_COMPONENTS = """
SELECT CAST(d.full_date AS DATE) AS fecha, v.componente, v.categoria, v.subcategoria, v.hqla_level, v.flow_tag,
       v.monto_nominal_clp, v.factor_ponderacion, v.monto_ponderado_clp
FROM dwh.vw_c55_lcr_components_daily AS v
JOIN dwh.dim_date AS d ON d.date_key = v.snapshot_date_key
WHERE v.snapshot_date_key >= ?
ORDER BY v.snapshot_date_key;
"""
SQLSERVER_NSFR_COMPONENTS = """
SELECT CAST(d.full_date AS DATE) AS fecha, v.componente, v.categoria, v.subcategoria,
       v.monto_nominal_clp, v.factor_ponderacion, v.monto_ponderado_clp
FROM dwh.vw_c55_nsfr_components_daily AS v
JOIN dwh.dim_date AS d ON d.date_key = v.snapshot_date_key
WHERE v.snapshot_date_key >= ?
ORDER BY v.snapshot_date_key;
"""
SQLSERVER_OFFICIAL = """
SELECT CAST(d.full_date AS DATE) AS fecha, o.lcr_pct, o.hqla_clp, o.net_cash_outflows_clp, o.nsfr_pct
FROM dwh.vw_cmf_c55_lcr_nsfr_official AS o
JOIN dwh.dim_date AS d ON d.date_key = o.snapshot_date_key
WHERE o.snapshot_date_key >= ?
ORDER BY o.snapshot_date_key;
"""


def _bundle_from_sqlserver(url: str) -> Dict[str, pd.DataFrame]:
    """
    Lee los componentes regulatorios desde las vistas del DWH y RECALCULA LCR/NSFR
    con `regulatory_engine` (Decimal). Si el resultado difiere de la vista oficial
    `dwh.vw_cmf_c55_lcr_nsfr_official` se registra una alerta: ambas
    implementaciones (T-SQL y Python) deben coincidir al cuarto decimal.
    """
    from sqlalchemy import create_engine, text  # dependencia opcional

    engine = create_engine(url, fast_executemany=True)
    first_key = int((date.today() - timedelta(days=HISTORY_DAYS + 30)).strftime("%Y%m%d"))
    with engine.connect() as cx:
        lcr_raw = pd.read_sql(text(SQLSERVER_LCR_COMPONENTS.replace("?", ":k")), cx, params={"k": first_key})
        nsfr_raw = pd.read_sql(text(SQLSERVER_NSFR_COMPONENTS.replace("?", ":k")), cx, params={"k": first_key})
        official = pd.read_sql(text(SQLSERVER_OFFICIAL.replace("?", ":k")), cx, params={"k": first_key})

    to_dec = lambda v: reg.to_money(Decimal(str(v)))
    lcr_hist_rows, lcr_det_rows = [], []
    for f, g in lcr_raw.groupby("fecha"):
        tags = g["flow_tag"].fillna("")
        lvl = g["hqla_level"].fillna("")
        sum_where = lambda mask: sum((to_dec(v) for v in g.loc[mask, "monto_ponderado_clp"]), Decimal(0))
        res = reg.compute_lcr(
            sum_where((g["componente"] == "ALAC") & (lvl == "L1")),
            sum_where((g["componente"] == "ALAC") & (lvl == "L2A")),
            sum_where((g["componente"] == "ALAC") & (lvl == "L2B")),
            sum_where(tags == "OUT"),
            -sum_where(tags == "IN"),  # la vista publica las entradas con signo negativo dentro del NAF
        )
        lcr_hist_rows.append({"fecha": pd.Timestamp(f), "lcr": round(_pct(res.lcr_pct), 4),
                              "alac": _mm(res.hqla), "naf": _mm(res.net_cash_outflows)})
        for _, r in g[g["flow_tag"].isin(["OUT", "IN", "ALAC"]) | (g["componente"] == "ALAC")].iterrows():
            lcr_det_rows.append({"fecha": pd.Timestamp(f), "componente": r["componente"], "categoria": r["categoria"],
                                 "subcategoria": r["subcategoria"], "monto_nominal": _mm(to_dec(r["monto_nominal_clp"])),
                                 "factor_ponderacion": float(r["factor_ponderacion"]),
                                 "monto_ponderado": _mm(to_dec(r["monto_ponderado_clp"])), "fuente": "SQLSERVER"})
        for adj_cat, adj_sub, comp, amt in (
                ("Ajuste Tope 15% Nivel 2B", "Exceso Nivel 2B sobre 15% del stock HQLA", "ALAC", -res.adjustment_15pct_cap),
                ("Ajuste Tope 40% Nivel 2", "Exceso Nivel 2 sobre 40% del stock HQLA", "ALAC", -res.adjustment_40pct_cap),
                ("Entradas de Efectivo (Inflows con Cap 75%)", "Ajuste tope 75%: entradas no computables", "NAF",
                 res.inflow_cap_adjustment)):
            if amt != 0:
                lcr_det_rows.append({"fecha": pd.Timestamp(f), "componente": comp, "categoria": adj_cat,
                                     "subcategoria": adj_sub, "monto_nominal": _mm(amt), "factor_ponderacion": 1.0,
                                     "monto_ponderado": _mm(amt), "fuente": "SQLSERVER"})

    nsfr_hist_rows, nsfr_det_rows = [], []
    for f, g in nsfr_raw.groupby("fecha"):
        asf = sum((to_dec(v) for v in g.loc[g["componente"] == "FED", "monto_ponderado_clp"]), Decimal(0))
        rsf = sum((to_dec(v) for v in g.loc[g["componente"] == "FER", "monto_ponderado_clp"]), Decimal(0))
        res = reg.compute_nsfr(asf, rsf)
        nsfr_hist_rows.append({"fecha": pd.Timestamp(f), "nsfr": round(_pct(res.nsfr_pct), 4),
                               "fed": _mm(asf), "fer": _mm(rsf)})
        for _, r in g.iterrows():
            nsfr_det_rows.append({"fecha": pd.Timestamp(f), "componente": r["componente"], "categoria": r["categoria"],
                                  "subcategoria": r["subcategoria"], "monto_nominal": _mm(to_dec(r["monto_nominal_clp"])),
                                  "factor_ponderacion": float(r["factor_ponderacion"]),
                                  "monto_ponderado": _mm(to_dec(r["monto_ponderado_clp"])), "fuente": "SQLSERVER"})

    lcr_hist = pd.DataFrame(lcr_hist_rows)
    if not official.empty and not lcr_hist.empty:
        chk = lcr_hist.merge(official.assign(fecha=pd.to_datetime(official["fecha"])), on="fecha", how="inner")
        diff = (chk["lcr"] - chk["lcr_pct"].astype(float)).abs()
        if (diff > 0.0001).any():
            log.warning("Divergencia LCR Python vs vista T-SQL en %d fechas (máx %.6f pp).", int((diff > 0.0001).sum()), diff.max())

    detail_from = pd.Timestamp(date.today() - timedelta(days=DETAIL_DAYS))
    lcr_det = pd.DataFrame(lcr_det_rows, columns=DETAIL_COLUMNS)
    nsfr_det = pd.DataFrame(nsfr_det_rows, columns=DETAIL_COLUMNS)
    return {"lcr_hist": lcr_hist, "lcr_det": lcr_det[lcr_det["fecha"] >= detail_from],
            "nsfr_hist": pd.DataFrame(nsfr_hist_rows), "nsfr_det": nsfr_det[nsfr_det["fecha"] >= detail_from]}


# =============================================================================
# 3. MODO SINTÉTICO (a nivel de componente, con topes regulatorios reales)
# =============================================================================

# (componente, categoría, subcategoría, nominal base MM$, factor, nivel/tag)
SYNTH_LCR_ITEMS = [
    ("ALAC", "Nivel 1: Efectivo y BCCh", "Caja en Billetes y Monedas", 65000, "1.00", "L1"),
    ("ALAC", "Nivel 1: Efectivo y BCCh", "Depósito en Banco Central (BCCh)", 310000, "1.00", "L1"),
    ("ALAC", "Nivel 1: Soberanos 0% Riesgo", "Bonos de Tesorería BTP / BTU", 480000, "1.00", "L1"),
    ("ALAC", "Nivel 1: Soberanos 0% Riesgo", "Pagarés y Bonos Banco Central (PDBC)", 220000, "1.00", "L1"),
    ("ALAC", "Nivel 2A: Activos Alta Calidad", "Bonos Bancarios Locales Calificación AA+", 95000, "0.85", "L2A"),
    ("ALAC", "Nivel 2A: Activos Alta Calidad", "Bonos Corporativos Grado de Inversión AAA", 75000, "0.85", "L2A"),
    ("ALAC", "Nivel 2B: Activos Menor Liquidez", "Acciones Índices Principales (IPSA)", 25000, "0.50", "L2B"),
    ("NAF", "Depósitos Minoristas Estables", "Depósitos a la Vista Personas Naturales", 450000, "0.05", "OUT"),
    ("NAF", "Depósitos Minoristas Menos Estables", "Depósitos a Plazo Personas Naturales", 320000, "0.10", "OUT"),
    ("NAF", "Depósitos Mayoristas Operacionales", "Cuentas Corrientes Empresas (Operacional)", 380000, "0.25", "OUT"),
    ("NAF", "Depósitos Mayoristas No Operacionales", "Depósitos Institucionales (AFPs/Fondos/Bancos) <= 30 días", 700000, "1.00", "OUT"),
    ("NAF", "Fondeo Mayorista No Garantizado", "Efectos de Comercio y Papeles Comerciales <= 30 días", 300000, "1.00", "OUT"),
    ("NAF", "Compromisos de Liquidez y Crédito", "Líneas de Crédito Corporativas No Utilizadas", 210000, "0.30", "OUT"),
    ("NAF", "Entradas de Efectivo (Inflows con Cap 75%)", "Cobros de Cartera de Créditos a 30 Días", 240000, "0.50", "IN"),
    ("NAF", "Entradas de Efectivo (Inflows con Cap 75%)", "Inversiones Financieras que Vencen a 30 Días", 60000, "1.00", "IN"),
]
SYNTH_NSFR_ITEMS = [
    ("FED", "Capital Regulatorio e Instrumentos > 1 Año", "Patrimonio Efectivo y Utilidades Acumuladas", 420000, "1.00"),
    ("FED", "Capital Regulatorio e Instrumentos > 1 Año", "Bonos Subordinados y Deuda a Largo Plazo (>1a)", 310000, "1.00"),
    ("FED", "Depósitos Minoristas Estables (95% ASF)", "Depósitos a la Vista y Plazo Personas (Estables)", 620000, "0.95"),
    ("FED", "Depósitos Minoristas Menos Estables (90% ASF)", "Depósitos de Personas sin Seguro Garantía", 240000, "0.90"),
    ("FED", "Fondeo Mayorista Operacional (50% ASF)", "Depósitos Operacionales Institucionales", 380000, "0.50"),
    ("FED", "Fondeo Mayorista No Operacional (0%-100% ASF)", "Depósitos Corporativos < 6 Meses", 290000, "0.50"),
    ("FER", "Efectivo y Reservas Centrales (0% RSF)", "Caja y Cuentas de Reserva en BCCh", 360000, "0.00"),
    ("FER", "Activos Líquidos Nivel 1 (5% RSF)", "Bonos Soberanos de Tesorería BTP/BTU", 450000, "0.05"),
    ("FER", "Créditos con Garantía Residencial (65% RSF)", "Mutuos Hipotecarios de Vivienda (<80% LTV)", 920000, "0.65"),
    ("FER", "Créditos Comerciales y Corporativos (85% RSF)", "Préstamos a Empresas con Vencimiento > 1 Año", 810000, "0.85"),
    ("FER", "Créditos de Consumo (50%-85% RSF)", "Créditos de Consumo y Préstamos No Garantizados", 340000, "1.00"),
    ("FER", "Otros Activos (100% RSF)", "Activos Fijos, Intangibles y Margen de Derivados", 160000, "1.00"),
]


def _lcr_from_items(f: pd.Timestamp, items: List[Tuple], fuente: str):
    """items: (componente, categoría, subcategoría, nominal Decimal MM$, factor Decimal, tag)."""
    w = [(it, reg.weighted(it[3], it[4])) for it in items]
    total = lambda tag: sum((x for it, x in w if it[5] == tag), Decimal(0))
    res = reg.compute_lcr(total("L1"), total("L2A"), total("L2B"), total("OUT"), total("IN"))
    det = []
    for it, x in w:
        sign = -1 if it[5] == "IN" else 1
        det.append({"fecha": f, "componente": it[0], "categoria": it[1], "subcategoria": it[2],
                    "monto_nominal": float(sign * it[3]), "factor_ponderacion": float(it[4]),
                    "monto_ponderado": float(sign * x), "fuente": fuente})
    for comp, cat, sub, amt in (
            ("ALAC", "Ajuste Tope 15% Nivel 2B", "Exceso Nivel 2B sobre 15% del stock HQLA", -res.adjustment_15pct_cap),
            ("ALAC", "Ajuste Tope 40% Nivel 2", "Exceso Nivel 2 sobre 40% del stock HQLA", -res.adjustment_40pct_cap),
            ("NAF", "Entradas de Efectivo (Inflows con Cap 75%)", "Ajuste tope 75%: entradas no computables",
             res.inflow_cap_adjustment)):
        if amt != 0:
            det.append({"fecha": f, "componente": comp, "categoria": cat, "subcategoria": sub,
                        "monto_nominal": float(amt), "factor_ponderacion": 1.0, "monto_ponderado": float(amt),
                        "fuente": fuente})
    hist = {"fecha": f, "lcr": round(_pct(res.lcr_pct), 4), "alac": float(res.hqla), "naf": float(res.net_cash_outflows)}
    return hist, det


def _nsfr_from_items(f: pd.Timestamp, items: List[Tuple], fuente: str):
    w = [(it, reg.weighted(it[3], it[4])) for it in items]
    asf = sum((x for it, x in w if it[0] == "FED"), Decimal(0))
    rsf = sum((x for it, x in w if it[0] == "FER"), Decimal(0))
    res = reg.compute_nsfr(asf, rsf)
    det = [{"fecha": f, "componente": it[0], "categoria": it[1], "subcategoria": it[2],
            "monto_nominal": float(it[3]), "factor_ponderacion": float(it[4]), "monto_ponderado": float(x),
            "fuente": fuente} for it, x in w]
    return {"fecha": f, "nsfr": round(_pct(res.nsfr_pct), 4), "fed": float(asf), "fer": float(rsf)}, det


def _synthetic_bundle(fechas: pd.DatetimeIndex, lcr_items, nsfr_items, fuente: str,
                      seed: int = 42) -> Dict[str, pd.DataFrame]:
    """
    Genera posiciones a nivel de componente (paseo aleatorio suave por línea) y
    calcula ALAC/NAF/FED/FER con el motor regulatorio, de modo que el histórico
    y el detalle son consistentes por construcción y los topes se aplican.
    `lcr_items` / `nsfr_items` pueden ser las líneas reales de la primera fecha de
    la base (backfill calibrado) o el catálogo de referencia SYNTH_*.
    """
    rng = np.random.default_rng(seed)
    n = len(fechas)
    lcr_paths = [np.cumprod(1.0 + rng.normal(0.00004, 0.004, n)) for _ in lcr_items]
    nsfr_paths = [np.cumprod(1.0 + rng.normal(0.00003, 0.003, n)) for _ in nsfr_items]
    detail_from = fechas[-DETAIL_DAYS] if n > DETAIL_DAYS else fechas[0]
    lcr_hist, lcr_det, nsfr_hist, nsfr_det = [], [], [], []
    for i, f in enumerate(fechas):
        seasonal = 1.0 + 0.02 * np.sin(f.dayofyear / 15.0)
        items = [(c, cat, sub, reg.to_money(Decimal(str(round(base * lcr_paths[k][i] * seasonal, 4)))), Decimal(fac), tag)
                 for k, (c, cat, sub, base, fac, tag) in enumerate(lcr_items)]
        h, d = _lcr_from_items(f, items, fuente)
        lcr_hist.append(h)
        if f >= detail_from:
            lcr_det.extend(d)
        items_n = [(c, cat, sub, reg.to_money(Decimal(str(round(base * nsfr_paths[k][i], 4)))), Decimal(fac))
                   for k, (c, cat, sub, base, fac) in enumerate(nsfr_items)]
        h, d = _nsfr_from_items(f, items_n, fuente)
        nsfr_hist.append(h)
        if f >= detail_from:
            nsfr_det.extend(d)
    return {"lcr_hist": pd.DataFrame(lcr_hist), "lcr_det": pd.DataFrame(lcr_det, columns=DETAIL_COLUMNS),
            "nsfr_hist": pd.DataFrame(nsfr_hist), "nsfr_det": pd.DataFrame(nsfr_det, columns=DETAIL_COLUMNS)}


def _synthetic_reference_bundle() -> Dict[str, pd.DataFrame]:
    fechas = pd.date_range(end=pd.Timestamp(date.today()), periods=HISTORY_DAYS + 1, freq="D")
    return _synthetic_bundle(fechas, SYNTH_LCR_ITEMS, SYNTH_NSFR_ITEMS, "SINTETICO", seed=42)


def _backfill(bundle: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    """
    Completa la historia faltante antes de la primera fecha real usando como
    base las líneas reales de esa fecha (no un catálogo genérico), para que el
    empalme sea continuo. Las filas quedan identificadas con su fuente.
    """
    hist = bundle["lcr_hist"]
    if hist.empty:
        return bundle
    first = pd.Timestamp(hist["fecha"].min())
    target_start = pd.Timestamp(date.today() - timedelta(days=HISTORY_DAYS))
    if first <= target_start:
        return bundle
    # El detalle cubre sólo los últimos DETAIL_DAYS: se calibra con la fecha más antigua que tenga detalle.
    if bundle["lcr_det"].empty or bundle["nsfr_det"].empty:
        return bundle
    calib = pd.Timestamp(bundle["lcr_det"]["fecha"].min())
    first_lcr = bundle["lcr_det"][bundle["lcr_det"]["fecha"] == calib]
    first_nsfr = bundle["nsfr_det"][bundle["nsfr_det"]["fecha"] == calib]
    if first_lcr.empty or first_nsfr.empty:
        return bundle
    lcr_items = []
    for _, r in first_lcr[~first_lcr["categoria"].str.startswith("Ajuste")
                          & ~first_lcr["subcategoria"].str.startswith("Ajuste")].iterrows():
        if r["componente"] == "ALAC":
            tag = "L2B" if "2B" in r["categoria"] else ("L2A" if "2A" in r["categoria"] else "L1")
        else:
            tag = "IN" if r["monto_ponderado"] < 0 else "OUT"
        lcr_items.append((r["componente"], r["categoria"], r["subcategoria"], abs(r["monto_nominal"]),
                          str(r["factor_ponderacion"]), tag))
    nsfr_items = [(r["componente"], r["categoria"], r["subcategoria"], r["monto_nominal"], str(r["factor_ponderacion"]))
                  for _, r in first_nsfr.iterrows()]
    fechas = pd.date_range(start=target_start, end=first - timedelta(days=1), freq="D")
    # Se genera en reversa temporal para que el último día del backfill empalme con la primera fecha real.
    back = _synthetic_bundle(fechas[::-1], lcr_items, nsfr_items, "BACKFILL_CALIBRADO", seed=7)
    for k in back:
        back[k] = back[k].sort_values("fecha")
    return {k: pd.concat([back[k], bundle[k]], ignore_index=True) for k in bundle}


# =============================================================================
# 4. RESOLUCIÓN DE FUENTE Y CACHÉ
# =============================================================================

def get_data_source_mode() -> str:
    return os.environ.get("INDICADOR_DATA_SOURCE", "HIBRIDO").upper()


@st.cache_data(ttl=600, show_spinner="Calculando LCR / NSFR desde la base regulatoria…")
def load_regulatory_bundle() -> Tuple[Dict[str, pd.DataFrame], str]:
    """Devuelve (bundle, descripción de la fuente efectiva)."""
    mode = get_data_source_mode()
    url = os.environ.get("CORE_BANKING_SQLSERVER_URL")
    if mode == "SINTETICO":
        return _synthetic_reference_bundle(), "SINTÉTICO (sin base de datos)"
    if mode == "SQLSERVER":
        if not url:
            raise RuntimeError("INDICADOR_DATA_SOURCE=SQLSERVER requiere CORE_BANKING_SQLSERVER_URL.")
        return _bundle_from_sqlserver(url), "SQL Server (dwh.vw_c55_*)"
    if mode == "SQLITE":
        return _bundle_from_sqlite(), "Base local core_banking.db"
    # HIBRIDO
    try:
        if url:
            bundle, label = _bundle_from_sqlserver(url), "SQL Server (dwh.vw_c55_*)"
        else:
            bundle, label = _bundle_from_sqlite(), "Base local core_banking.db"
        before = len(bundle["lcr_hist"])
        bundle = _backfill(bundle)
        if len(bundle["lcr_hist"]) > before:
            label += f" + backfill calibrado ({len(bundle['lcr_hist']) - before} días)"
        return bundle, label
    except Exception as exc:  # degradación controlada: el dashboard sigue operativo
        log.exception("Fuente de datos principal no disponible; se usa modo sintético.")
        return _synthetic_reference_bundle(), f"SINTÉTICO (fallback: {type(exc).__name__}: {exc})"


def load_lcr_historico() -> pd.DataFrame:
    """[fecha, lcr, alac, naf] — LCR = ALAC / NAF * 100 con topes 40% / 15% / 75%."""
    return load_regulatory_bundle()[0]["lcr_hist"]


def load_lcr_detalle() -> pd.DataFrame:
    """Descomposición de ALAC y NAF por línea (incluye líneas de ajuste por topes)."""
    return load_regulatory_bundle()[0]["lcr_det"]


def load_nsfr_historico() -> pd.DataFrame:
    """[fecha, nsfr, fed, fer] — NSFR = FED / FER * 100."""
    return load_regulatory_bundle()[0]["nsfr_hist"]


def load_nsfr_detalle() -> pd.DataFrame:
    """Descomposición de FED y FER por línea (factor ASF/RSF por vencimiento residual)."""
    return load_regulatory_bundle()[0]["nsfr_det"]


def get_data_source_label() -> str:
    return load_regulatory_bundle()[1]
