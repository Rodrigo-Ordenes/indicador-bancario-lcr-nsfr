"""
analytics.py
============
Módulo de analítica cuantitativa, modelación financiera, proyecciones y estrés
para la supervisión de liquidez y balance (Basilea III: LCR & NSFR / CMF).

Principios metodológicos de liquidez bancaria:
- Los indicadores y sus componentes representan cierres diarios de fin de día (EOD).
- No existe movimiento intradía: la variación diaria es estrictamente el cierre de hoy respecto al cierre de ayer.
- En vistas semanal, mensual y anual, los montos diarios se consolidan al cierre de cada período respectivo
  (viernes para semanas, fin de mes para meses, fin de año para años) calculando la variación respecto al período previo.
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, List, Optional
from decimal import Decimal
import regulatory_engine as reg

def resample_historico(
    df: pd.DataFrame, 
    fecha_col: str, 
    ratio_col: str, 
    num_col: str, 
    den_col: str, 
    granularidad: str = "Diaria",
    metodo: str = "cierre",
    *args,
    **kwargs
) -> pd.DataFrame:
    """
    Agrupa y remuestrea la serie histórica según la granularidad seleccionada.
    En banca ALM, los indicadores diarios representan el cierre de cada día (EOD).
    Para vistas semanal, mensual y anual se toma el cierre de cada período (viernes, fin de mes, fin de año).
    """
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df_work = df.copy()
    df_work[fecha_col] = pd.to_datetime(df_work[fecha_col])
    df_work = df_work.sort_values(by=fecha_col)

    if granularidad == "Diaria":
        # Serie diaria pura: cada fila es el cierre del día
        df_resampled = df_work.copy()
    else:
        freq_map = {
            "Semanal": "W-FRI",  # Cierre semanal al viernes hábil
            "Mensual": "ME",     # Cierre de fin de mes
            "Anual": "YE"        # Cierre de fin de año
        }
        freq = freq_map.get(granularidad, "D")
        df_indexed = df_work.set_index(fecha_col)
        
        if metodo == "cierre":
            df_resampled = df_indexed.resample(freq).last().dropna(subset=[ratio_col]).reset_index()
        else:
            df_resampled = df_indexed.resample(freq).mean().dropna(subset=[ratio_col]).reset_index()
            df_resampled[ratio_col] = (df_resampled[num_col] / df_resampled[den_col]) * 100

    # Variación período a período (Hoy vs Ayer en Diaria; Semana vs Semana ant. en Semanal, etc.)
    df_resampled['delta_ratio'] = df_resampled[ratio_col].diff()
    df_resampled['delta_num'] = df_resampled[num_col].diff()
    df_resampled['delta_den'] = df_resampled[den_col].diff()

    # Variaciones porcentuales (%)
    df_resampled['delta_ratio_pct'] = df_resampled[ratio_col].pct_change() * 100
    df_resampled['delta_num_pct'] = df_resampled[num_col].pct_change() * 100
    df_resampled['delta_den_pct'] = df_resampled[den_col].pct_change() * 100

    # Holguras frente a 100%
    df_resampled['holgura_pp'] = df_resampled[ratio_col] - 100.0
    df_resampled['holgura_monto'] = df_resampled[num_col] - df_resampled[den_col]

    return df_resampled


def get_kpis_indicador(
    df_resampled: pd.DataFrame = None,
    ratio_col: str = "lcr",
    num_col: str = "alac",
    den_col: str = "naf",
    limite_normativo: float = 100.0,
    limite_interno: float = 120.0,
    apetito_riesgo: float = 150.0,
    granularidad: str = "Diaria",
    df_hist: pd.DataFrame = None
) -> Dict[str, Any]:
    """
    Extrae KPIs ejecutivos con semáforos de cumplimiento bajo los umbrales definidos
    y genera etiquetas precisas según la granularidad (Hoy vs Ayer en diaria, etc.).
    """
    df = df_resampled if df_resampled is not None else df_hist
    if df is None or len(df) == 0:
        return {}

    ultimo = df.iloc[-1]
    anterior = df.iloc[-2] if len(df) > 1 else ultimo
    ratio_val = float(ultimo[ratio_col])

    # Etiquetas dinámicas según granularidad
    labels_map = {
        "Diaria": {
            "periodo_actual": "Cierre de Hoy",
            "periodo_anterior": "Cierre de Ayer",
            "vs_label": "vs ayer"
        },
        "Semanal": {
            "periodo_actual": "Cierre Semana Actual",
            "periodo_anterior": "Cierre Semana Anterior",
            "vs_label": "vs semana ant."
        },
        "Mensual": {
            "periodo_actual": "Cierre Mes Actual",
            "periodo_anterior": "Cierre Mes Anterior",
            "vs_label": "vs mes ant."
        },
        "Anual": {
            "periodo_actual": "Cierre Año Actual",
            "periodo_anterior": "Cierre Año Anterior",
            "vs_label": "vs año ant."
        }
    }
    lbl = labels_map.get(granularidad, labels_map["Diaria"])

    # Clasificación estricta según umbrales Bloomberg
    if ratio_val < limite_normativo:
        semaforo = "INCUMPLIMIENTO"
        semaforo_color = "#fe443d"  # Rojo Bloomberg
        semaforo_badge = "badge-status-critical"
        semaforo_msg = f"Déficit regulatorio CMF (< {limite_normativo:.0f}%)"
    elif ratio_val < limite_interno:
        semaforo = "BAJO ALERTA"
        semaforo_color = "#ffd15c"  # Amarillo Bloomberg
        semaforo_badge = "badge-status-warning"
        semaforo_msg = f"Bajo nivel de alerta interno (< {limite_interno:.0f}%)"
    elif ratio_val < apetito_riesgo:
        semaforo = "BAJO APETITO"
        semaforo_color = "#fb8b1f"  # Naranja Bloomberg
        semaforo_badge = "badge-status-alert"
        semaforo_msg = f"En cumplimiento, pero bajo apetito (< {apetito_riesgo:.0f}%)"
    else:
        semaforo = "ÓPTIMO"
        semaforo_color = "#4af6c4"  # Menta Bloomberg
        semaforo_badge = "badge-status-optimal"
        semaforo_msg = f"Sobre apetito por riesgo (>= {apetito_riesgo:.0f}%)"

    # Deltas calculados con resiliencia
    if 'delta_ratio' in ultimo and not pd.isna(ultimo['delta_ratio']):
        delta_ratio = float(ultimo['delta_ratio'])
    else:
        delta_ratio = ratio_val - float(anterior[ratio_col]) if len(df) > 1 else 0.0

    if 'delta_num' in ultimo and not pd.isna(ultimo['delta_num']):
        delta_num = float(ultimo['delta_num'])
    else:
        delta_num = float(ultimo[num_col]) - float(anterior[num_col]) if len(df) > 1 else 0.0

    if 'delta_den' in ultimo and not pd.isna(ultimo['delta_den']):
        delta_den = float(ultimo['delta_den'])
    else:
        delta_den = float(ultimo[den_col]) - float(anterior[den_col]) if len(df) > 1 else 0.0

    if 'delta_ratio_pct' in ultimo and not pd.isna(ultimo['delta_ratio_pct']):
        delta_ratio_pct = float(ultimo['delta_ratio_pct'])
    else:
        prev_r = float(anterior[ratio_col])
        delta_ratio_pct = (delta_ratio / prev_r * 100.0) if prev_r != 0 else 0.0

    if 'delta_num_pct' in ultimo and not pd.isna(ultimo['delta_num_pct']):
        delta_num_pct = float(ultimo['delta_num_pct'])
    else:
        prev_n = float(anterior[num_col])
        delta_num_pct = (delta_num / prev_n * 100.0) if prev_n != 0 else 0.0

    if 'delta_den_pct' in ultimo and not pd.isna(ultimo['delta_den_pct']):
        delta_den_pct = float(ultimo['delta_den_pct'])
    else:
        prev_d = float(anterior[den_col])
        delta_den_pct = (delta_den / prev_d * 100.0) if prev_d != 0 else 0.0

    return {
        "fecha_actual": ultimo['fecha'],
        "fecha_anterior": anterior['fecha'],
        "ratio_actual": ratio_val,
        "ratio_anterior": float(anterior[ratio_col]),
        "delta_ratio": delta_ratio,
        "delta_ratio_pct": delta_ratio_pct,
        
        "num_actual": float(ultimo[num_col]),
        "num_anterior": float(anterior[num_col]),
        "delta_num": delta_num,
        "delta_num_pct": delta_num_pct,

        "den_actual": float(ultimo[den_col]),
        "den_anterior": float(anterior[den_col]),
        "delta_den": delta_den,
        "delta_den_pct": delta_den_pct,

        "holgura_pp": ratio_val - limite_normativo,
        "holgura_monto": float(ultimo[num_col]) - float(ultimo[den_col]),
        
        "semaforo": semaforo,
        "semaforo_color": semaforo_color,
        "semaforo_badge": semaforo_badge,
        "semaforo_msg": semaforo_msg,
        "limite_normativo": limite_normativo,
        "limite_interno": limite_interno,
        "apetito_riesgo": apetito_riesgo,

        "granularidad": granularidad,
        "periodo_actual_label": lbl["periodo_actual"],
        "periodo_anterior_label": lbl["periodo_anterior"],
        "vs_label": lbl["vs_label"]
    }


def calculate_multithreshold_buffers(
    ratio_val: float,
    num_val: float,
    den_val: float,
    thresholds: List[float],
    labels: List[str]
) -> pd.DataFrame:
    """
    Calcula la distancia y holgura monetaria frente a múltiples umbrales (Req 3.2).
    """
    rows = []
    for t, label in zip(thresholds, labels):
        t_frac = t / 100.0
        dist_pp = ratio_val - t
        holgura_activo = num_val - (t_frac * den_val)
        max_delta_den = (num_val / t_frac) - den_val if t_frac > 0 else 0.0
        
        rows.append({
            "Umbral (%)": f"{t:.0f}%",
            "Nivel": label,
            "Distancia (pp)": dist_pp,
            "Holgura Monetaria (MM$)": holgura_activo,
            "Capacidad Absorción Pasivo (MM$)": max_delta_den,
            "Estado": "CUMPLE" if dist_pp >= 0 else "BRECHA / DÉFICIT"
        })
    return pd.DataFrame(rows)


def calculate_alert_statistics(df_hist: pd.DataFrame, ratio_col: str, thresholds: Dict[str, float]) -> Dict[str, Any]:
    """
    Calcula días consecutivos bajo alerta y frecuencia histórica de incumplimientos (Req 3.2).
    """
    series = df_hist[ratio_col].dropna()
    total_days = len(series)
    if total_days == 0:
        return {}

    stats = {}
    for name, threshold in thresholds.items():
        below_mask = series < threshold
        pct_below = (below_mask.sum() / total_days) * 100.0
        
        consecutive = 0
        for val in reversed(series.values):
            if val < threshold:
                consecutive += 1
            else:
                break
                
        stats[name] = {
            "threshold": threshold,
            "dias_historicos_bajo": int(below_mask.sum()),
            "frecuencia_pct": pct_below,
            "dias_consecutivos_actuales": consecutive
        }
    return stats


def compute_waterfall_attribution(
    num_0: float, den_0: float,
    num_1: float, den_1: float
) -> Dict[str, float]:
    """
    Descompone matemáticamente el cambio del ratio entre dos períodos (Req 3.1).
    Delta Ratio = Efecto Numerador + Efecto Denominador + Efecto Interacción.
    """
    if den_0 <= 0 or den_1 <= 0:
        return {"delta_ratio": 0.0, "efecto_num": 0.0, "efecto_den": 0.0, "efecto_interaccion": 0.0}

    ratio_0 = (num_0 / den_0) * 100.0
    ratio_1 = (num_1 / den_1) * 100.0
    delta_ratio = ratio_1 - ratio_0

    delta_num = num_1 - num_0
    delta_den = den_1 - den_0

    efecto_num = (delta_num / den_0) * 100.0
    efecto_den = - (num_0 * delta_den / (den_0 * den_1)) * 100.0
    efecto_interaccion = delta_ratio - (efecto_num + efecto_den)

    return {
        "ratio_inicio": ratio_0,
        "ratio_fin": ratio_1,
        "delta_ratio": delta_ratio,
        "efecto_num": efecto_num,
        "efecto_den": efecto_den,
        "efecto_interaccion": efecto_interaccion
    }


def project_time_series(
    series: pd.Series,
    horizon_days: int = 30,
    method: str = "holt"
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Proyecta una serie de tiempo univariada con bandas de incertidumbre (Req 1.5, 2.5).
    """
    y = series.dropna().values
    n = len(y)
    if n < 5:
        last = y[-1] if n > 0 else 100.0
        pred = pd.Series([last] * horizon_days)
        return pred, pred * 0.95, pred * 1.05

    if method == "linear":
        x = np.arange(n)
        slope, intercept = np.polyfit(x, y, 1)
        x_pred = np.arange(n, n + horizon_days)
        pred_vals = slope * x_pred + intercept
        residuals = y - (slope * x + intercept)
        sigma = np.std(residuals)

    elif method == "ma":
        window = min(30, n)
        weights = np.arange(1, window + 1)
        weights = weights / weights.sum()
        weighted_mean = np.sum(y[-window:] * weights)
        pred_vals = np.full(horizon_days, weighted_mean)
        sigma = np.std(y[-window:])

    else:  # 'holt'
        alpha, beta = 0.3, 0.1
        level = y[0]
        trend = y[1] - y[0]
        for val in y[1:]:
            last_level = level
            level = alpha * val + (1 - alpha) * (level + trend)
            trend = beta * (level - last_level) + (1 - beta) * trend
        pred_vals = np.array([level + (h + 1) * trend for h in range(horizon_days)])
        sigma = np.std(np.diff(y))

    time_scale = np.sqrt(np.arange(1, horizon_days + 1))
    lower_vals = pred_vals - 1.96 * sigma * (time_scale / np.sqrt(horizon_days))
    upper_vals = pred_vals + 1.96 * sigma * (time_scale / np.sqrt(horizon_days))

    return pd.Series(pred_vals), pd.Series(lower_vals), pd.Series(upper_vals)


def backtest_projection_models(series: pd.Series, test_days: int = 30) -> pd.DataFrame:
    """
    Evalúa y compara la precisión histórica de distintos modelos de proyección (Req 3.3).
    """
    clean_series = series.dropna()
    if len(clean_series) <= test_days + 15:
        test_days = max(5, len(clean_series) // 4)

    train = clean_series.iloc[:-test_days]
    test = clean_series.iloc[-test_days:].values
    results = []

    for method, label in [("holt", "Alisamiento Doble (Holt)"),
                          ("linear", "Regresión Lineal"),
                          ("ma", "Media Móvil Ponderada")]:
        pred, _, _ = project_time_series(train, horizon_days=test_days, method=method)
        p_vals = pred.values[:len(test)]
        
        errors = test - p_vals
        mae = np.mean(np.abs(errors))
        mape = np.mean(np.abs(errors / test)) * 100.0
        rmse = np.sqrt(np.mean(errors ** 2))
        
        results.append({
            "Método de Estimación": label,
            "MAE (pp)": round(mae, 3),
            "MAPE (%)": round(mape, 2),
            "RMSE (pp)": round(rmse, 3),
            "Desempeño": "Óptimo" if mape < 2.0 else "Aceptable" if mape < 5.0 else "Incierto"
        })
    return pd.DataFrame(results).sort_values(by="MAPE (%)")


def analyze_concentration_and_volatility(df_det: pd.DataFrame, componente: str) -> Dict[str, Any]:
    """
    Calcula concentración (HHI, Pareto Top 5) y volatilidad histórica por componente (Req 3.4).
    """
    df = df_det[df_det['componente'] == componente].copy()
    if df.empty:
        return {}

    fechas = sorted(df['fecha'].unique())
    last_date = fechas[-1]
    df_last = df[df['fecha'] == last_date].copy()
    
    cat_summary = df_last.groupby('categoria')['monto_ponderado'].sum().reset_index()
    total = cat_summary['monto_ponderado'].sum()
    if total > 0:
        cat_summary['share_pct'] = (cat_summary['monto_ponderado'] / total) * 100.0
    else:
        cat_summary['share_pct'] = 0.0

    cat_summary = cat_summary.sort_values(by='share_pct', ascending=False)
    
    hhi = (cat_summary['share_pct'] ** 2).sum()
    if hhi > 2500:
        hhi_diag = "ALTA CONCENTRACIÓN (HHI > 2500)"
    elif hhi > 1500:
        hhi_diag = "CONCENTRACIÓN MODERADA (1500 - 2500)"
    else:
        hhi_diag = "DIVERSIFICADO (HHI < 1500)"

    top5_share = cat_summary.head(5)['share_pct'].sum()

    piv = df.pivot_table(index='fecha', columns='categoria', values='monto_ponderado', aggfunc='sum').fillna(0)
    vol_rows = []
    for cat in piv.columns:
        s = piv[cat]
        mu = s.mean()
        std = s.std()
        cv = (std / mu * 100.0) if mu > 0 else 0.0
        vol_rows.append({
            "Categoría": cat,
            "Monto Promedio (MM$)": mu,
            "Desviación Estándar (MM$)": std,
            "Coef. Variación (%)": cv
        })
    df_vol = pd.DataFrame(vol_rows).sort_values(by="Coef. Variación (%)", ascending=False)

    return {
        "cat_summary": cat_summary,
        "hhi": hhi,
        "hhi_diagnostico": hhi_diag,
        "top5_share_pct": top5_share,
        "df_volatilidad": df_vol
    }


def analyze_decomposition(
    df_detalle: pd.DataFrame,
    fecha_referencia: pd.Timestamp = None,
    componente_filtro: str = None,
    granularidad: str = "Diaria",
    *args,
    **kwargs
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """
    Analiza la descomposición de un indicador en la fecha seleccionada
    y calcula la variación respecto a la fecha anterior según la granularidad:
    - Diaria: Variación del día de hoy respecto a ayer.
    - Semanal: Variación de la semana actual respecto a la semana anterior (~7 días antes).
    - Mensual: Variación del mes actual respecto al mes anterior (~30 días antes).
    - Anual: Variación del año actual respecto al año anterior (~365 días antes).
    
    Retorna: (df_cat, df_detalle_res, metadata_comparacion)
    """
    if df_detalle is None or len(df_detalle) == 0:
        return pd.DataFrame(), pd.DataFrame(), {}
    df = df_detalle.copy()
    df['fecha'] = pd.to_datetime(df['fecha'])
    
    if componente_filtro:
        df = df[df['componente'] == componente_filtro]

    fechas_disponibles = sorted(df['fecha'].unique())
    if len(fechas_disponibles) == 0:
        return pd.DataFrame(), pd.DataFrame(), {}

    if fecha_referencia is None or fecha_referencia not in fechas_disponibles:
        fecha_act = fechas_disponibles[-1]
    else:
        fecha_act = fecha_referencia

    idx_act = fechas_disponibles.index(fecha_act)

    # Identificar fecha anterior según granularidad
    if granularidad == "Diaria":
        fecha_ant = fechas_disponibles[idx_act - 1] if idx_act > 0 else None
        comp_label = "vs ayer"
        periodo_act_lbl = "Cierre de Hoy"
        periodo_ant_lbl = "Cierre de Ayer"
    elif granularidad == "Semanal":
        # Buscar la fecha disponible ~7 días antes (entre 5 y 10 días antes)
        candidatos = [f for f in fechas_disponibles if (fecha_act - f).days >= 5]
        fecha_ant = candidatos[-1] if candidatos else (fechas_disponibles[idx_act - 1] if idx_act > 0 else None)
        comp_label = "vs semana ant."
        periodo_act_lbl = "Cierre Semana Actual"
        periodo_ant_lbl = "Cierre Semana Anterior"
    elif granularidad == "Mensual":
        # Buscar la fecha disponible ~30 días antes
        candidatos = [f for f in fechas_disponibles if (fecha_act - f).days >= 20]
        fecha_ant = candidatos[-1] if candidatos else (fechas_disponibles[idx_act - 1] if idx_act > 0 else None)
        comp_label = "vs mes ant."
        periodo_act_lbl = "Cierre Mes Actual"
        periodo_ant_lbl = "Cierre Mes Anterior"
    else:  # Anual
        # Buscar la fecha disponible ~365 días antes o la más lejana
        candidatos = [f for f in fechas_disponibles if (fecha_act - f).days >= 300]
        fecha_ant = candidatos[-1] if candidatos else (fechas_disponibles[0] if len(fechas_disponibles) > 1 else None)
        comp_label = "vs año ant."
        periodo_act_lbl = "Cierre Año Actual"
        periodo_ant_lbl = "Cierre Año Anterior"

    df_act = df[df['fecha'] == fecha_act].copy()
    
    if fecha_ant is not None:
        df_ant = df[df['fecha'] == fecha_ant].copy()
        merged = pd.merge(
            df_act, 
            df_ant[['componente', 'categoria', 'subcategoria', 'monto_ponderado', 'monto_nominal']],
            on=['componente', 'categoria', 'subcategoria'],
            how='left',
            suffixes=('', '_ant')
        )
        merged['monto_ponderado_ant'] = merged['monto_ponderado_ant'].fillna(0)
        merged['monto_nominal_ant'] = merged['monto_nominal_ant'].fillna(0)
        merged['delta_ponderado'] = merged['monto_ponderado'] - merged['monto_ponderado_ant']
        merged['delta_nominal'] = merged['monto_nominal'] - merged['monto_nominal_ant']
        merged['delta_pct'] = np.where(
            merged['monto_ponderado_ant'] != 0,
            (merged['delta_ponderado'] / merged['monto_ponderado_ant']) * 100,
            0.0
        )
        df_detalle_res = merged
    else:
        df_act['monto_ponderado_ant'] = 0.0
        df_act['monto_nominal_ant'] = 0.0
        df_act['delta_ponderado'] = 0.0
        df_act['delta_nominal'] = 0.0
        df_act['delta_pct'] = 0.0
        df_detalle_res = df_act

    # Agrupación por categoría
    df_cat = df_detalle_res.groupby(['componente', 'categoria']).agg(
        monto_nominal=('monto_nominal', 'sum'),
        monto_nominal_ant=('monto_nominal_ant', 'sum'),
        monto_ponderado=('monto_ponderado', 'sum'),
        monto_ponderado_ant=('monto_ponderado_ant', 'sum'),
        delta_ponderado=('delta_ponderado', 'sum')
    ).reset_index()

    df_cat['delta_pct'] = np.where(
        df_cat['monto_ponderado_ant'] != 0,
        (df_cat['delta_ponderado'] / df_cat['monto_ponderado_ant']) * 100,
        0.0
    )

    total_por_comp = df_cat.groupby('componente')['monto_ponderado'].transform('sum')
    df_cat['participacion_pct'] = np.where(total_por_comp != 0, (df_cat['monto_ponderado'] / total_por_comp) * 100, 0)

    # Identificación de Top Drivers (mayores aumentos y reducciones)
    top_alzas = df_detalle_res.sort_values(by='delta_ponderado', ascending=False)
    top_alzas = top_alzas[top_alzas['delta_ponderado'] > 0].head(3)
    
    top_bajas = df_detalle_res.sort_values(by='delta_ponderado', ascending=True)
    top_bajas = top_bajas[top_bajas['delta_ponderado'] < 0].head(3)

    metadata = {
        "fecha_act": fecha_act,
        "fecha_ant": fecha_ant,
        "granularidad": granularidad,
        "comp_label": comp_label,
        "periodo_act_lbl": periodo_act_lbl,
        "periodo_ant_lbl": periodo_ant_lbl,
        "top_alzas": top_alzas,
        "top_bajas": top_bajas
    }
    
    return df_cat, df_detalle_res, metadata
