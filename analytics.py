"""
analytics.py
============
Motor analítico del panel de liquidez. Todas las cifras se recalculan desde los
componentes (líneas de ALAC, NAF, FED y FER) con `regulatory_engine`, de modo que
pruebas de tensión, puentes de variación y proyecciones respetan los mismos
topes regulatorios que el cálculo oficial (40% Nivel 2, 15% Nivel 2B, 75% entradas).

Convenciones:
- Montos en MM$ (millones de CLP), ratios en %.
- Los datos son cierres diarios: no hay movimiento intradía. La vista diaria
  compara el cierre de hoy con el de ayer; las vistas semanal, mensual y anual
  comparan el último cierre disponible de cada período con el cierre del período
  anterior. El período en curso se marca como "en curso".
- Cada resultado indica su naturaleza: REAL (dato observado), PROYECTADO
  (estimación estadística) o SIMULADO (escenario de tensión).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

import regulatory_engine as reg
from styles import BBG_BLUE, BBG_MINT, BBG_ORANGE, BBG_RED

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
PERIOD_CODE = {"Semanal": "W-FRI", "Mensual": "M", "Anual": "Y"}
LINE_KEY = ["componente", "categoria", "subcategoria"]


# =============================================================================
# 1. INDICADORES Y UMBRALES
# =============================================================================

@dataclass(frozen=True)
class Indicador:
    """Metadatos de un indicador para que LCR y NSFR compartan la misma lógica y navegación."""
    codigo: str          # "LCR" | "NSFR"
    ratio_col: str
    num_col: str
    den_col: str
    num: str             # "ALAC" | "FED"
    den: str             # "NAF"  | "FER"
    nombre: str


LCR = Indicador("LCR", "lcr", "alac", "naf", "ALAC", "NAF", "Razón de Cobertura de Liquidez")
NSFR = Indicador("NSFR", "nsfr", "fed", "fer", "FED", "FER", "Razón de Financiamiento Estable Neto")


@dataclass(frozen=True)
class Umbral:
    valor: float
    nombre: str
    tipo: str            # "Regulatorio" | "Interno: alerta" | "Interno: apetito"
    color: str


def umbrales_lcr(minimo=100.0, alerta=120.0, apetito=150.0) -> List[Umbral]:
    return [Umbral(apetito, "Apetito por riesgo", "Interno: apetito", BBG_MINT),
            Umbral(alerta, "Alerta interna", "Interno: alerta", BBG_ORANGE),
            Umbral(minimo, "Mínimo regulatorio", "Regulatorio", BBG_RED)]


def umbrales_nsfr(minimo=100.0, alerta=105.0) -> List[Umbral]:
    return [Umbral(alerta, "Alerta interna", "Interno: alerta", BBG_ORANGE),
            Umbral(minimo, "Mínimo regulatorio", "Regulatorio", BBG_RED)]


def estado(ratio: float, umbrales: List[Umbral]) -> Dict[str, str]:
    """Semáforo: el límite regulatorio se distingue de las alertas y del apetito internos."""
    vals = {u.tipo: u.valor for u in umbrales}
    minimo, alerta, apetito = vals["Regulatorio"], vals["Interno: alerta"], vals.get("Interno: apetito")
    if ratio < minimo:
        return {"codigo": "INCUMPLIMIENTO", "color": BBG_RED,
                "detalle": f"Bajo el mínimo regulatorio de {minimo:.0f}%"}
    if ratio < alerta:
        return {"codigo": "ALERTA", "color": BBG_ORANGE,
                "detalle": f"Cumple el mínimo, pero está bajo la alerta interna de {alerta:.0f}%"}
    if apetito is not None and ratio < apetito:
        return {"codigo": "BAJO APETITO", "color": BBG_BLUE,
                "detalle": f"Sobre la alerta interna, bajo el apetito por riesgo de {apetito:.0f}%"}
    return {"codigo": "DENTRO DE APETITO" if apetito is not None else "CUMPLE", "color": BBG_MINT,
            "detalle": "Sobre todos los umbrales definidos"}


# =============================================================================
# 2. CIERRES POR PERÍODO Y VARIACIONES
# =============================================================================

def _period_label(fecha: pd.Timestamp, granularidad: str) -> str:
    if granularidad == "Diaria":
        return fecha.strftime("%d-%m-%Y")
    if granularidad == "Semanal":
        return f"Semana al {fecha.strftime('%d-%m-%Y')}"
    if granularidad == "Mensual":
        return f"{MESES[fecha.month - 1]}-{fecha.year}"
    return str(fecha.year)


def period_closes(df_hist: pd.DataFrame, ind: Indicador, granularidad: str) -> pd.DataFrame:
    """
    Cierres del período: último dato diario disponible de cada día, semana (viernes),
    mes o año, con variación absoluta y porcentual respecto del cierre anterior.
    """
    df = df_hist[["fecha", ind.ratio_col, ind.num_col, ind.den_col]].copy()
    df["fecha"] = pd.to_datetime(df["fecha"])
    df = df.sort_values("fecha").reset_index(drop=True)
    if granularidad != "Diaria":
        period = df["fecha"].dt.to_period(PERIOD_CODE[granularidad])
        df["_p"] = period
        df = df.groupby("_p", sort=True).tail(1).copy()
        last_p = df["_p"].iloc[-1]
        df["en_curso"] = (df["_p"] == last_p) & (df["fecha"] < last_p.end_time.normalize())
        df = df.drop(columns="_p").reset_index(drop=True)
    else:
        df["en_curso"] = False
    df["periodo"] = [_period_label(f, granularidad) for f in df["fecha"]]
    for col, name in ((ind.ratio_col, "ratio"), (ind.num_col, "num"), (ind.den_col, "den")):
        prev = df[col].shift(1)
        df[f"d_{name}"] = df[col] - prev
        df[f"d_{name}_pct"] = (df[col] / prev - 1.0) * 100.0
    return df


def kpi_snapshot(closes: pd.DataFrame, ind: Indicador, umbrales: List[Umbral], granularidad: str) -> Dict:
    """Valores del último cierre vs el cierre anterior, con estado y holgura sobre el mínimo regulatorio."""
    act = closes.iloc[-1]
    ant = closes.iloc[-2] if len(closes) > 1 else act
    minimo = next(u.valor for u in umbrales if u.tipo == "Regulatorio")
    ref_ant = {"Diaria": "vs cierre de ayer", "Semanal": "vs cierre semana anterior",
               "Mensual": "vs cierre mes anterior", "Anual": "vs cierre año anterior"}[granularidad]
    return {
        "fecha": act["fecha"], "fecha_ant": ant["fecha"],
        "periodo": act["periodo"] + (" (en curso)" if act["en_curso"] else ""), "periodo_ant": ant["periodo"],
        "ref_ant": ref_ant,
        "ratio": float(act[ind.ratio_col]), "ratio_ant": float(ant[ind.ratio_col]),
        "num": float(act[ind.num_col]), "num_ant": float(ant[ind.num_col]),
        "den": float(act[ind.den_col]), "den_ant": float(ant[ind.den_col]),
        "d_ratio": float(act[ind.ratio_col] - ant[ind.ratio_col]),
        "d_ratio_pct": float((act[ind.ratio_col] / ant[ind.ratio_col] - 1) * 100) if ant[ind.ratio_col] else 0.0,
        "d_num": float(act[ind.num_col] - ant[ind.num_col]),
        "d_num_pct": float((act[ind.num_col] / ant[ind.num_col] - 1) * 100) if ant[ind.num_col] else 0.0,
        "d_den": float(act[ind.den_col] - ant[ind.den_col]),
        "d_den_pct": float((act[ind.den_col] / ant[ind.den_col] - 1) * 100) if ant[ind.den_col] else 0.0,
        "estado": estado(float(act[ind.ratio_col]), umbrales),
        "holgura_pp": float(act[ind.ratio_col]) - minimo,
        "holgura_mm": float(act[ind.num_col] - minimo / 100.0 * act[ind.den_col]),
    }


def compare_periods(closes: pd.DataFrame, ind: Indicador, fecha_a, fecha_b) -> pd.DataFrame:
    """Tabla de comparación entre dos cierres seleccionados: valor A, valor B, variación absoluta y %."""
    a = closes.loc[closes["fecha"] == pd.Timestamp(fecha_a)].iloc[0]
    b = closes.loc[closes["fecha"] == pd.Timestamp(fecha_b)].iloc[0]
    rows = []
    for col, label, unit in ((ind.ratio_col, ind.codigo, "%"), (ind.num_col, ind.num, "MM$"), (ind.den_col, ind.den, "MM$")):
        va, vb = float(a[col]), float(b[col])
        rows.append({"Indicador": label, "Unidad": unit, "Período A": va, "Período B": vb,
                     "Variación absoluta": vb - va, "Variación %": (vb / va - 1) * 100 if va else np.nan})
    return pd.DataFrame(rows)


# =============================================================================
# 3. LÍNEAS DE COMPONENTES Y RECÁLCULO REGULATORIO
# =============================================================================

def classify_line(componente: str, categoria: str, subcategoria: str, ponderado: float) -> str:
    """Tipo regulatorio de cada línea del detalle (necesario para reaplicar topes)."""
    if categoria.startswith("Ajuste") or subcategoria.startswith("Ajuste"):
        return "AJUSTE"
    if componente == "ALAC":
        return "L2B" if "2B" in categoria else ("L2A" if "2A" in categoria else "L1")
    if componente == "NAF":
        return "ENTRADA" if (ponderado < 0 or categoria.startswith("Entradas")) else "SALIDA"
    return componente


def lines_at(df_det: pd.DataFrame, fecha, include_adjustments: bool = False) -> pd.DataFrame:
    """Líneas de una fecha con su tipo regulatorio y montos absolutos ponderados."""
    d = df_det.loc[pd.to_datetime(df_det["fecha"]) == pd.Timestamp(fecha)].copy()
    if d.empty:
        return d.assign(tipo=pd.Series(dtype=str), base=pd.Series(dtype=float))
    d["tipo"] = [classify_line(c, cat, sub, w) for c, cat, sub, w in
                 zip(d["componente"], d["categoria"], d["subcategoria"], d["monto_ponderado"])]
    if not include_adjustments:
        d = d[d["tipo"] != "AJUSTE"]
    d = d.groupby(LINE_KEY + ["tipo"], as_index=False).agg(
        monto_nominal=("monto_nominal", "sum"), monto_ponderado=("monto_ponderado", "sum"),
        factor_ponderacion=("factor_ponderacion", "first"))
    d["base"] = d["monto_ponderado"].abs()   # monto computable (entradas en valor absoluto)
    return d.reset_index(drop=True)


def _dec(x: float) -> Decimal:
    return reg.to_money(Decimal(str(round(float(x), 6))))


def ratio_from_lines(ind: Indicador, lines: pd.DataFrame, col: str = "base") -> Dict:
    """Recalcula el indicador desde las líneas aplicando topes (LCR) o la razón directa (NSFR)."""
    tot = lines.groupby("tipo")[col].sum() if not lines.empty else pd.Series(dtype=float)
    g = lambda t: _dec(tot.get(t, 0.0))
    if ind.codigo == "LCR":
        r = reg.compute_lcr(g("L1"), g("L2A"), g("L2B"), g("SALIDA"), g("ENTRADA"))
        return {"ratio": float(r.lcr_pct) if r.lcr_pct is not None else float("nan"),
                "num": float(r.hqla), "den": float(r.net_cash_outflows), "detalle": r}
    r = reg.compute_nsfr(g("FED"), g("FER"))
    return {"ratio": float(r.nsfr_pct) if r.nsfr_pct is not None else float("nan"),
            "num": float(r.available_stable_funding), "den": float(r.required_stable_funding), "detalle": r}


def decompose(df_det: pd.DataFrame, ind: Indicador, fecha_act, fecha_ant) -> pd.DataFrame:
    """
    Descomposición por línea entre dos cierres: monto actual, anterior, variación
    absoluta y % y su impacto en el indicador (pp), medido como el cambio del ratio
    al reemplazar sólo esa línea por su valor actual (resto en el período anterior).
    """
    a = lines_at(df_det, fecha_act)
    b = lines_at(df_det, fecha_ant)
    m = pd.merge(a[LINE_KEY + ["tipo", "base", "monto_ponderado", "factor_ponderacion"]],
                 b[LINE_KEY + ["tipo", "base", "monto_ponderado"]],
                 on=LINE_KEY + ["tipo"], how="outer", suffixes=("_act", "_ant")).fillna(
        {"base_act": 0.0, "base_ant": 0.0, "monto_ponderado_act": 0.0, "monto_ponderado_ant": 0.0})
    base_ratio = ratio_from_lines(ind, b.assign(base=b["base"]))["ratio"]
    impacts = []
    for _, row in m.iterrows():
        mixed = b.copy()
        mask = (mixed[LINE_KEY] == row[LINE_KEY].values).all(axis=1)
        if mask.any():
            mixed.loc[mask, "base"] = row["base_act"]
        else:
            mixed = pd.concat([mixed, pd.DataFrame([{**{k: row[k] for k in LINE_KEY}, "tipo": row["tipo"],
                                                     "base": row["base_act"]}])], ignore_index=True)
        impacts.append(ratio_from_lines(ind, mixed)["ratio"] - base_ratio)
    m["impacto_pp"] = impacts
    m["monto_act"] = m["monto_ponderado_act"]
    m["monto_ant"] = m["monto_ponderado_ant"]
    m["var_abs"] = m["monto_act"] - m["monto_ant"]
    m["var_pct"] = np.where(m["monto_ant"] != 0, m["var_abs"] / m["monto_ant"].abs() * 100, np.nan)
    m["sentido"] = np.where(m["impacto_pp"] > 0.005, "Mejora", np.where(m["impacto_pp"] < -0.005, "Deteriora", "Neutro"))
    m = m.sort_values("impacto_pp", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)
    return m[LINE_KEY + ["tipo", "factor_ponderacion", "monto_ant", "monto_act", "var_abs", "var_pct", "impacto_pp", "sentido"]]


def adjustment_lines(df_det: pd.DataFrame, fecha_act, fecha_ant) -> pd.DataFrame:
    """Ajustes por topes regulatorios (si existen) en ambos cierres, para transparentar su efecto."""
    a = lines_at(df_det, fecha_act, include_adjustments=True)
    b = lines_at(df_det, fecha_ant, include_adjustments=True)
    a, b = a[a["tipo"] == "AJUSTE"], b[b["tipo"] == "AJUSTE"]
    m = pd.merge(a[LINE_KEY + ["monto_ponderado"]], b[LINE_KEY + ["monto_ponderado"]], on=LINE_KEY,
                 how="outer", suffixes=("_act", "_ant")).fillna(0.0)
    return m


# =============================================================================
# 4. PRUEBAS DE TENSIÓN
# =============================================================================

# Supuestos de escenarios: shock sobre el monto computable de cada línea, según su
# categoría regulatoria (coincidencia por texto). Valores positivos aumentan el monto.
SCENARIOS: Dict[str, Dict[str, Dict[str, float]]] = {
    "LCR": {
        "Base": {},
        "Adverso": {
            "Nivel 2A": -10.0, "Nivel 2B": -25.0,
            "Depósitos Minoristas Menos Estables": 10.0, "Depósitos Mayoristas Operacionales": 10.0,
            "Depósitos Mayoristas No Operacionales": 20.0, "Fondeo Mayorista No Garantizado": 20.0,
            "Compromisos de Liquidez": 25.0, "Entradas de Efectivo": -15.0,
        },
        "Severo": {
            "Nivel 1: Soberanos": -3.0, "Nivel 2A": -20.0, "Nivel 2B": -50.0,
            "Depósitos Minoristas Estables": 10.0, "Depósitos Minoristas Menos Estables": 25.0,
            "Depósitos Mayoristas Operacionales": 20.0, "Depósitos Mayoristas No Operacionales": 40.0,
            "Fondeo Mayorista No Garantizado": 40.0, "Compromisos de Liquidez": 60.0, "Entradas de Efectivo": -30.0,
        },
    },
    "NSFR": {
        "Base": {},
        "Adverso": {
            "Fondeo Mayorista No Operacional": -15.0, "Fondeo Mayorista e Instrumentos": -10.0,
            "Fondeo Mayorista Operacional": -10.0, "Depósitos Minoristas Menos Estables": -5.0,
            "Créditos Comerciales": 5.0, "Créditos con Garantía Residencial": 3.0,
        },
        "Severo": {
            "Fondeo Mayorista No Operacional": -35.0, "Fondeo Mayorista e Instrumentos": -25.0,
            "Fondeo Mayorista Operacional": -20.0, "Depósitos Minoristas Menos Estables": -12.0,
            "Depósitos Minoristas Estables": -5.0, "Créditos Comerciales": 10.0,
            "Créditos con Garantía Residencial": 5.0, "Créditos de Consumo": 5.0,
        },
    },
}

SCENARIO_DESCRIPTION = {
    "Base": "Montos observados al cierre, sin shocks.",
    "Adverso": "Tensión moderada: caída de valor de activos Nivel 2, mayores retiros de fondeo mayorista y "
               "menores entradas.",
    "Severo": "Tensión severa sistémica: fuerte fuga de fondeo mayorista, uso intensivo de líneas comprometidas, "
              "castigo a activos Nivel 2 y caída de entradas.",
}


def scenario_shocks(ind: Indicador, lines: pd.DataFrame, escenario: str) -> pd.Series:
    """Shock (%) por línea según el escenario predefinido."""
    rules = SCENARIOS[ind.codigo][escenario]
    out = []
    for cat in lines["categoria"]:
        out.append(next((v for k, v in rules.items() if k.lower() in cat.lower()), 0.0))
    return pd.Series(out, index=lines.index, dtype=float)


def run_stress(ind: Indicador, lines: pd.DataFrame, shocks_pct: pd.Series) -> Dict:
    """
    Aplica shocks (%) al monto computable de cada línea, recalcula componentes y el
    indicador con topes, y mide el impacto individual de cada modificación.
    """
    base = ratio_from_lines(ind, lines)
    stressed = lines.copy()
    stressed["shock_pct"] = shocks_pct.reindex(lines.index).fillna(0.0).values
    stressed["base_estresada"] = stressed["base"] * (1.0 + stressed["shock_pct"] / 100.0)
    res = ratio_from_lines(ind, stressed, "base_estresada")
    impacts = []
    for i in stressed.index:
        if stressed.at[i, "shock_pct"] == 0:
            impacts.append(0.0)
            continue
        single = lines.copy()
        single.loc[i, "base"] = stressed.at[i, "base_estresada"]
        impacts.append(ratio_from_lines(ind, single)["ratio"] - base["ratio"])
    stressed["impacto_individual_pp"] = impacts
    stressed["variacion_monto"] = stressed["base_estresada"] - stressed["base"]
    return {"base": base, "estresado": res, "lineas": stressed,
            "delta_pp": res["ratio"] - base["ratio"],
            "interaccion_pp": (res["ratio"] - base["ratio"]) - float(np.nansum(impacts))}


# =============================================================================
# 5. PROYECCIONES (estimaciones, nunca resultados ciertos)
# =============================================================================

METHODS = {
    "holt": "Holt amortiguado (φ = 0,90)",
    "lineal": "Tendencia lineal (90 días)",
    "media_movil": "Media móvil (20 días)",
    "ingenuo": "Ingenuo (último valor)",
}


def _holt_fit(y: np.ndarray, phi: float = 0.90) -> Tuple[float, float, float, float, np.ndarray]:
    best = None
    for alpha in (0.1, 0.2, 0.3, 0.5, 0.7, 0.9):
        for beta in (0.01, 0.05, 0.1, 0.2):
            level, trend = y[0], y[1] - y[0] if len(y) > 1 else 0.0
            errs = []
            for t in range(1, len(y)):
                fc = level + phi * trend
                errs.append(y[t] - fc)
                new_level = alpha * y[t] + (1 - alpha) * fc
                trend = beta * (new_level - level) + (1 - beta) * phi * trend
                level = new_level
            sse = float(np.sum(np.square(errs)))
            if best is None or sse < best[0]:
                best = (sse, alpha, beta, level, trend, np.array(errs))
    _, alpha, beta, level, trend, errs = best
    return alpha, beta, level, trend, errs


def forecast(series: pd.Series, horizon: int, method: str = "holt") -> pd.DataFrame:
    """
    Proyección diaria con intervalo del 95%. `series` es diaria (índice fecha).
    El intervalo usa la dispersión de los errores a un paso dentro de muestra,
    escalada por la raíz del horizonte: es una aproximación, no una garantía.
    """
    s = series.dropna().astype(float)
    y = s.values
    idx = pd.date_range(s.index[-1] + pd.Timedelta(days=1), periods=horizon, freq="D")
    h = np.arange(1, horizon + 1)
    if method == "holt":
        # Amortiguamiento fuerte: un shock reciente no se extrapola indefinidamente.
        phi = 0.90
        _, _, level, trend, errs = _holt_fit(y[-365:], phi)
        damp = np.array([sum(phi ** k for k in range(1, i + 1)) for i in h])
        pred = level + damp * trend
        sigma = float(np.std(errs[-120:])) if len(errs) > 5 else float(np.std(np.diff(y)))
    elif method == "lineal":
        w = y[-90:]
        x = np.arange(len(w))
        b, a = np.polyfit(x, w, 1)
        pred = a + b * (len(w) - 1 + h)
        sigma = float(np.std(w - (a + b * x)))
        h = np.ones_like(h)  # error de nivel, no acumulativo
    elif method == "media_movil":
        w = y[-20:]
        pred = np.repeat(w.mean(), horizon)
        sigma = float(np.std(np.diff(y[-120:])))
    else:
        pred = np.repeat(y[-1], horizon)
        sigma = float(np.std(np.diff(y[-120:])))
    band = 1.96 * sigma * np.sqrt(h)
    return pd.DataFrame({"fecha": idx, "pred": pred, "lower": pred - band, "upper": pred + band})


def project_with_scenarios(df_hist: pd.DataFrame, ind: Indicador, lines: pd.DataFrame, horizon: int,
                           escenarios: Iterable[str], ramp_days: int = 30) -> Dict[str, pd.DataFrame]:
    """
    Proyección del indicador bajo escenarios. La trayectoria base proyecta por
    separado numerador y denominador con Holt usando sus montos históricos; cada
    escenario aplica gradualmente (rampa lineal en `ramp_days`) el efecto relativo
    sobre numerador y denominador obtenido al estresar los componentes del último cierre.
    """
    hist = df_hist.set_index(pd.to_datetime(df_hist["fecha"])).sort_index()
    fnum = forecast(hist[ind.num_col], horizon, "holt")
    fden = forecast(hist[ind.den_col], horizon, "holt")
    fratio = forecast(hist[ind.ratio_col], horizon, "holt")
    t = np.arange(1, horizon + 1)
    ramp = np.minimum(t / ramp_days, 1.0)
    out = {}
    for esc in escenarios:
        sres = run_stress(ind, lines, scenario_shocks(ind, lines, esc))
        k_num = sres["estresado"]["num"] / sres["base"]["num"] if sres["base"]["num"] else 1.0
        k_den = sres["estresado"]["den"] / sres["base"]["den"] if sres["base"]["den"] else 1.0
        num = fnum["pred"].values * (1 + (k_num - 1) * ramp)
        den = fden["pred"].values * (1 + (k_den - 1) * ramp)
        ratio = num / den * 100.0
        # Banda: dispersión del ratio proyectado directamente, centrada en la trayectoria del escenario.
        half = (fratio["upper"].values - fratio["lower"].values) / 2.0
        out[esc] = pd.DataFrame({"fecha": fnum["fecha"], "num": num, "den": den, "pred": ratio,
                                 "lower": ratio - half, "upper": ratio + half,
                                 "k_num": k_num, "k_den": k_den})
    return out


# =============================================================================
# 6. ANÁLISIS TRANSVERSAL
# =============================================================================

def waterfall_by_category(df_det: pd.DataFrame, ind: Indicador, fecha_a, fecha_b, top_n: int = 8) -> pd.DataFrame:
    """
    Puente del indicador entre dos cierres por categoría regulatoria. Cada
    contribución es el cambio del ratio al llevar sólo esa categoría de A a B; la
    diferencia contra el cambio total se informa como residual (interacción entre
    numerador y denominador y efecto de los topes regulatorios).
    """
    a = lines_at(df_det, fecha_a)
    b = lines_at(df_det, fecha_b)
    ra, rb = ratio_from_lines(ind, a), ratio_from_lines(ind, b)
    cats = sorted(set(a["categoria"]) | set(b["categoria"]))
    rows = []
    for cat in cats:
        mixed = pd.concat([a[a["categoria"] != cat], b[b["categoria"] == cat]], ignore_index=True)
        comp = b.loc[b["categoria"] == cat, "componente"]
        comp = comp.iloc[0] if len(comp) else a.loc[a["categoria"] == cat, "componente"].iloc[0]
        rows.append({"categoria": cat, "componente": comp,
                     "monto_a": a.loc[a["categoria"] == cat, "base"].sum(),
                     "monto_b": b.loc[b["categoria"] == cat, "base"].sum(),
                     "contribucion_pp": ratio_from_lines(ind, mixed)["ratio"] - ra["ratio"]})
    df = pd.DataFrame(rows).sort_values("contribucion_pp", key=lambda s: s.abs(), ascending=False)
    if len(df) > top_n:
        head, tail = df.iloc[:top_n], df.iloc[top_n:]
        df = pd.concat([head, pd.DataFrame([{"categoria": "Otras categorías", "componente": "",
                                             "monto_a": tail["monto_a"].sum(), "monto_b": tail["monto_b"].sum(),
                                             "contribucion_pp": tail["contribucion_pp"].sum()}])], ignore_index=True)
    residual = (rb["ratio"] - ra["ratio"]) - df["contribucion_pp"].sum()
    df = pd.concat([df, pd.DataFrame([{"categoria": "Residual (interacción y topes)", "componente": "",
                                       "monto_a": np.nan, "monto_b": np.nan, "contribucion_pp": residual}])],
                   ignore_index=True)
    df.attrs.update({"ratio_a": ra["ratio"], "ratio_b": rb["ratio"]})
    return df


def buffers(ind: Indicador, ratio: float, num: float, den: float, umbrales: List[Umbral]) -> pd.DataFrame:
    """
    Distancia y holgura monetaria frente a cada umbral, y el ajuste necesario para
    alcanzarlo: aumento del numerador o reducción del denominador (cada uno por sí solo).
    En LCR, el aumento de ALAC supone activos de Nivel 1 (no afectos a topes).
    """
    rows = []
    for u in umbrales:
        t = u.valor / 100.0
        holgura_num = num - t * den           # cuánto puede caer el numerador sin cruzar el umbral
        holgura_den = num / t - den           # cuánto puede subir el denominador sin cruzar el umbral
        rows.append({
            "Umbral": f"{u.nombre} ({u.valor:.0f}%)", "Tipo": u.tipo, "Distancia (pp)": ratio - u.valor,
            f"Holgura en {ind.num} (MM$)": holgura_num, f"Holgura en {ind.den} (MM$)": holgura_den,
            f"{ind.num} adicional requerido (MM$)": max(-holgura_num, 0.0),
            f"Reducción de {ind.den} requerida (MM$)": max(-holgura_den, 0.0),
            "Situación": "Sobre el umbral" if ratio >= u.valor else "Bajo el umbral",
        })
    return pd.DataFrame(rows)


def threshold_crossing(proj: pd.DataFrame, last_real: float, umbral: float) -> str:
    """Primera fecha estimada bajo el umbral, o 'Ya bajo el umbral' si el último dato real ya lo está."""
    if last_real < umbral:
        return "Ya bajo el umbral"
    cross = proj.loc[proj["pred"] < umbral, "fecha"]
    return cross.iloc[0].strftime("%d-%m-%Y") if len(cross) else "No cruza"


def required_adjustment(ratio_target: float, num: float, den: float) -> Dict[str, float]:
    """Monto que lleva el indicador exactamente a un umbral objetivo (positivo = aumento)."""
    t = ratio_target / 100.0
    return {"delta_num": t * den - num, "delta_den": num / t - den}


def alert_statistics(df_hist: pd.DataFrame, ind: Indicador, umbrales: List[Umbral]) -> pd.DataFrame:
    """Días consecutivos actuales bajo cada umbral, racha máxima y frecuencia histórica."""
    s = df_hist.sort_values("fecha")
    vals, fechas = s[ind.ratio_col].values, pd.to_datetime(s["fecha"]).values
    rows = []
    for u in umbrales:
        below = vals < u.valor
        streak = 0
        for v in below[::-1]:
            if not v:
                break
            streak += 1
        max_streak, cur = 0, 0
        for v in below:
            cur = cur + 1 if v else 0
            max_streak = max(max_streak, cur)
        last = pd.Timestamp(fechas[below][-1]).strftime("%d-%m-%Y") if below.any() else "—"
        rows.append({"Umbral": f"{u.nombre} ({u.valor:.0f}%)", "Tipo": u.tipo,
                     "Días consecutivos actuales bajo el umbral": streak,
                     "Días totales bajo el umbral": int(below.sum()),
                     "Frecuencia histórica (%)": float(below.mean() * 100) if len(below) else 0.0,
                     "Racha máxima (días)": max_streak, "Última fecha bajo el umbral": last})
    return pd.DataFrame(rows)


def backtest(series: pd.Series, horizon: int = 20, n_origins: int = 15, step: int = 10,
             methods: Iterable[str] = tuple(METHODS)) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Evaluación fuera de muestra con origen móvil: en cada origen se proyecta con la
    información disponible hasta ese día y se compara con el valor observado
    `horizon` días después. Devuelve métricas por método y el detalle observado vs proyectado.
    """
    s = series.dropna().astype(float)
    detail = []
    n = len(s)
    origins = [n - horizon - k * step for k in range(n_origins)]
    origins = sorted(o for o in origins if o > 120)
    for m in methods:
        for o in origins:
            train = s.iloc[:o]
            fc = forecast(train, horizon, m)
            obs = s.iloc[o + horizon - 1]
            row = fc.iloc[horizon - 1]
            detail.append({"metodo": m, "origen": s.index[o - 1], "fecha_objetivo": s.index[o + horizon - 1],
                           "proyectado": row["pred"], "observado": obs, "lower": row["lower"], "upper": row["upper"]})
    det = pd.DataFrame(detail)
    det["error"] = det["proyectado"] - det["observado"]
    metrics = det.groupby("metodo").apply(lambda g: pd.Series({
        "MAE (pp)": g["error"].abs().mean(),
        "RMSE (pp)": math.sqrt((g["error"] ** 2).mean()),
        "MAPE (%)": (g["error"].abs() / g["observado"].abs()).mean() * 100,
        "Sesgo (pp)": g["error"].mean(),
        "Cobertura intervalo 95% (%)": ((g["observado"] >= g["lower"]) & (g["observado"] <= g["upper"])).mean() * 100,
        "Pruebas": len(g),
    }), include_groups=False).reset_index()
    metrics["Método"] = metrics["metodo"].map(METHODS)
    metrics = metrics.sort_values("RMSE (pp)").reset_index(drop=True)
    return metrics, det


def concentration(df_det: pd.DataFrame, componente: str, fecha, dimension: str = "subcategoria",
                  ventana: int = 90) -> Dict:
    """
    Concentración (participación, top 5 e índice HHI) y volatilidad histórica de las
    líneas de un componente. La dimensión puede ser la línea (instrumento/producto)
    o la categoría regulatoria (segmento).
    """
    lines = lines_at(df_det, fecha)
    lines = lines[lines["componente"] == componente]
    by = lines.groupby(dimension, as_index=False)["base"].sum().sort_values("base", ascending=False)
    total = by["base"].sum()
    by["participacion"] = by["base"] / total * 100 if total else 0.0
    by["acumulado"] = by["participacion"].cumsum()
    hhi = float((by["participacion"] ** 2).sum())
    nivel = "Baja" if hhi < 1500 else ("Moderada" if hhi < 2500 else "Alta")

    d = df_det[df_det["componente"] == componente].copy()
    d["fecha"] = pd.to_datetime(d["fecha"])
    d = d[d["fecha"] > pd.Timestamp(fecha) - pd.Timedelta(days=ventana)]
    d = d[~d["categoria"].str.startswith("Ajuste") & ~d["subcategoria"].str.startswith("Ajuste")]
    piv = d.assign(v=d["monto_ponderado"].abs()).pivot_table(index="fecha", columns=dimension, values="v", aggfunc="sum")
    chg = piv.diff()
    vol = pd.DataFrame({
        dimension: piv.columns,
        "Monto promedio (MM$)": piv.mean().values,
        "Desv. estándar variación diaria (MM$)": chg.std().values,
        "Coef. de variación (%)": (piv.std() / piv.mean() * 100).values,
        "Peor variación diaria (MM$)": chg.min().values,
    }).sort_values("Coef. de variación (%)", ascending=False).reset_index(drop=True)

    dependencia = None
    if componente in ("NAF", "FED"):
        mayorista = lines["categoria"].str.contains("Mayorista|Fondeo", case=False)
        dependencia = float(lines.loc[mayorista, "base"].sum() / lines["base"].sum() * 100) if lines["base"].sum() else 0.0
    return {"tabla": by, "hhi": hhi, "nivel": nivel, "top5": float(by["participacion"].head(5).sum()),
            "volatilidad": vol, "dependencia_mayorista": dependencia}


# =============================================================================
# 7. CALIDAD DE DATOS (se ejecuta antes de calcular y mostrar indicadores)
# =============================================================================

def data_quality(df_hist: pd.DataFrame, df_det: pd.DataFrame, ind: Indicador) -> pd.DataFrame:
    checks = []

    def add(control, ok, detalle, nivel="ERROR"):
        checks.append({"Control": control, "Resultado": "OK" if ok else nivel, "Detalle": detalle})

    h = df_hist.copy()
    h["fecha"] = pd.to_datetime(h["fecha"])
    h = h.sort_values("fecha")
    full = pd.date_range(h["fecha"].min(), h["fecha"].max(), freq="D")
    missing = full.difference(h["fecha"])
    add("Calendario diario continuo", len(missing) == 0,
        "Sin fechas faltantes" if len(missing) == 0 else f"{len(missing)} fechas faltantes (p. ej. {missing[0]:%d-%m-%Y})")
    dups = int(h["fecha"].duplicated().sum())
    add("Sin fechas duplicadas", dups == 0, f"{dups} duplicados")
    nulls = int(h[[ind.ratio_col, ind.num_col, ind.den_col]].isna().sum().sum())
    add("Sin valores nulos", nulls == 0, f"{nulls} valores nulos")
    add(f"{ind.den} positivo", bool((h[ind.den_col] > 0).all()), "Denominador > 0 en todas las fechas")
    recomputed = h[ind.num_col] / h[ind.den_col] * 100
    gap = float((recomputed - h[ind.ratio_col]).abs().max())
    add("Ratio = numerador / denominador", gap < 0.01, f"Diferencia máxima {gap:.4f} pp")

    d = df_det.copy()
    d["fecha"] = pd.to_datetime(d["fecha"])
    sums = d.groupby(["fecha", "componente"])["monto_ponderado"].sum().unstack()
    j = h.set_index("fecha").join(sums, how="inner")
    gap_det = float(max((j[ind.num_col] - j[ind.num]).abs().max(), (j[ind.den_col] - j[ind.den]).abs().max()))
    add("Detalle cuadra con totales", gap_det < 0.01, f"Diferencia máxima {gap_det:.6f} MM$ en {len(j)} fechas")
    bad_f = int(((d["factor_ponderacion"] < 0) | (d["factor_ponderacion"] > 1)).sum())
    add("Factores de ponderación en [0, 1]", bad_f == 0, f"{bad_f} líneas fuera de rango")

    last = h["fecha"].max()
    rec = ratio_from_lines(ind, lines_at(df_det, last))
    gap_eng = abs(rec["ratio"] - float(h.loc[h["fecha"] == last, ind.ratio_col].iloc[0]))
    add("Recálculo independiente con topes", gap_eng < 0.01,
        f"Motor: {rec['ratio']:.4f}% vs publicado: {float(h[ind.ratio_col].iloc[-1]):.4f}%")
    jumps = h.loc[h[ind.ratio_col].diff().abs() > 5, "fecha"]
    add("Variaciones diarias atípicas (> 5 pp)", len(jumps) == 0,
        "Ninguna" if len(jumps) == 0 else f"{len(jumps)} días a revisar (último {jumps.iloc[-1]:%d-%m-%Y})", "OBSERVADO")
    lag = (pd.Timestamp.today().normalize() - last).days
    add("Vigencia del dato (cierre T+1)", lag <= 3, f"Último cierre {last:%d-%m-%Y} ({lag} días)", "OBSERVADO")
    return pd.DataFrame(checks)


# =============================================================================
# 8. RESUMEN EJECUTIVO
# =============================================================================

def executive_summary(k_lcr: Dict, k_nsfr: Dict, dec_lcr: pd.DataFrame, dec_nsfr: pd.DataFrame,
                      stress_lcr: Dict, stress_nsfr: Dict, u_lcr: List[Umbral], u_nsfr: List[Umbral]) -> Dict:
    """Hechos del período redactados para la alta administración (sin jerga innecesaria)."""
    def near(k, umbrales, margin):
        out = []
        for u in umbrales:
            dist = k["ratio"] - u.valor
            if 0 <= dist < margin:
                out.append(f"a {dist:.1f} pp de {u.nombre.lower()} ({u.valor:.0f}%)")
            elif dist < 0:
                out.append(f"{abs(dist):.1f} pp bajo {u.nombre.lower()} ({u.valor:.0f}%)")
        return out

    movimientos = []
    for nombre, k, num, den in (("LCR", k_lcr, "ALAC", "NAF"), ("NSFR", k_nsfr, "FED", "FER")):
        movimientos.append(
            f"{nombre} cerró en {k['ratio']:.2f}% ({k['d_ratio']:+.2f} pp {k['ref_ant']}); "
            f"{num} {k['d_num']:+,.0f} MM$ ({k['d_num_pct']:+.1f}%) y {den} {k['d_den']:+,.0f} MM$ ({k['d_den_pct']:+.1f}%).")

    proximos = []
    for nombre, k, u, m in (("LCR", k_lcr, u_lcr, 10.0), ("NSFR", k_nsfr, u_nsfr, 5.0)):
        for txt in near(k, u, m):
            proximos.append(f"{nombre} {txt}.")

    deterioro = []
    for nombre, dec in (("LCR", dec_lcr), ("NSFR", dec_nsfr)):
        neg = dec[dec["impacto_pp"] < -0.01].sort_values("impacto_pp").head(3)
        for _, r in neg.iterrows():
            deterioro.append(f"{nombre}: {r['subcategoria']} ({r['categoria']}) restó {abs(r['impacto_pp']):.2f} pp "
                             f"(variación {r['var_abs']:+,.0f} MM$).")

    adverso = [
        f"LCR bajo escenario {stress_lcr['nombre'].lower()}: {stress_lcr['estresado']['ratio']:.2f}% "
        f"({stress_lcr['delta_pp']:+.2f} pp; {estado(stress_lcr['estresado']['ratio'], u_lcr)['codigo'].lower()}).",
        f"NSFR bajo escenario {stress_nsfr['nombre'].lower()}: {stress_nsfr['estresado']['ratio']:.2f}% "
        f"({stress_nsfr['delta_pp']:+.2f} pp; {estado(stress_nsfr['estresado']['ratio'], u_nsfr)['codigo'].lower()}).",
    ]

    worst = min((k_lcr["estado"]["codigo"], k_nsfr["estado"]["codigo"]),
                key=lambda c: ["INCUMPLIMIENTO", "ALERTA", "BAJO APETITO", "DENTRO DE APETITO", "CUMPLE"].index(c))
    if worst == "INCUMPLIMIENTO":
        conclusion = ("La posición de liquidez incumple el mínimo regulatorio en al menos un indicador. "
                      "Se requiere activar el plan de contingencia de liquidez e informar al regulador.")
    elif worst == "ALERTA":
        conclusion = ("La posición cumple los mínimos regulatorios, pero al menos un indicador está bajo su alerta "
                      "interna. Se recomienda revisar el fondeo mayorista y reforzar activos líquidos.")
    elif worst == "BAJO APETITO":
        conclusion = ("La posición cumple los mínimos y las alertas internas, aunque el LCR opera bajo el apetito "
                      "por riesgo definido. La holgura existe, pero es menor a la objetivo.")
    else:
        conclusion = "La posición de liquidez cumple todos los umbrales regulatorios e internos."
    if stress_lcr["estresado"]["ratio"] < 100 or stress_nsfr["estresado"]["ratio"] < 100:
        conclusion += " Bajo el escenario de tensión evaluado, el banco incumpliría el mínimo regulatorio."
    return {"movimientos": movimientos, "proximos": proximos or ["Ningún indicador está próximo a sus umbrales."],
            "deterioro": deterioro or ["No se identifican componentes con deterioro relevante en el período."],
            "adverso": adverso, "conclusion": conclusion}
