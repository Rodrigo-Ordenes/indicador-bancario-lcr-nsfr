"""
analytics.py
============
Módulo de cálculo financiero y agregación temporal para métricas bancarias.
Procesa granularidades:
- Diaria ('D')
- Semanal ('W')
- Mensual ('M')
- Anual ('Y')

Calcula:
- Valores del indicador (LCR / NSFR)
- Variaciones entre períodos (delta absoluto en pp y delta porcentual %)
- Variaciones de componentes (ALAC y NAF para LCR; FED y FER para NSFR)
- Atribución de cambios y descomposición detallada
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, Tuple

def resample_historico(
    df: pd.DataFrame, 
    fecha_col: str, 
    ratio_col: str, 
    num_col: str, 
    den_col: str, 
    granularidad: str = "Diaria",
    metodo: str = "cierre"
) -> pd.DataFrame:
    """
    Agrupa y remuestrea la serie histórica según la granularidad seleccionada.
    
    Args:
        df: DataFrame original con frecuencia diaria.
        fecha_col: Nombre de la columna de fecha.
        ratio_col: Nombre del ratio ('lcr' o 'nsfr').
        num_col: Nombre del numerador ('alac' o 'fed').
        den_col: Nombre del denominador ('naf' o 'fer').
        granularidad: 'Diaria', 'Semanal', 'Mensual', 'Anual'.
        metodo: 'cierre' (último dato del período) o 'promedio'.
    
    Returns:
        DataFrame remuestreado con cálculos de deltas período a período.
    """
    df_work = df.copy()
    df_work[fecha_col] = pd.to_datetime(df_work[fecha_col])
    df_work = df_work.sort_values(by=fecha_col)

    if granularidad == "Diaria":
        # Mantiene la serie diaria tal cual
        df_resampled = df_work.copy()
    else:
        # Definir regla de pandas
        freq_map = {
            "Semanal": "W-FRI",
            "Mensual": "ME",
            "Anual": "YE"
        }
        freq = freq_map.get(granularidad, "D")
        
        df_indexed = df_work.set_index(fecha_col)
        
        if metodo == "cierre":
            df_resampled = df_indexed.resample(freq).last().dropna(subset=[ratio_col]).reset_index()
        else:
            # Promedio ponderado o aritmético
            df_resampled = df_indexed.resample(freq).mean().dropna(subset=[ratio_col]).reset_index()
            # Recalcular ratio con los montos promediados para consistencia estricta
            df_resampled[ratio_col] = (df_resampled[num_col] / df_resampled[den_col]) * 100

    # Calcular variaciones período a período (Lag = 1)
    df_resampled['delta_ratio'] = df_resampled[ratio_col].diff()
    df_resampled['delta_num'] = df_resampled[num_col].diff()
    df_resampled['delta_den'] = df_resampled[den_col].diff()

    # Variaciones porcentuales (%)
    df_resampled['delta_ratio_pct'] = df_resampled[ratio_col].pct_change() * 100
    df_resampled['delta_num_pct'] = df_resampled[num_col].pct_change() * 100
    df_resampled['delta_den_pct'] = df_resampled[den_col].pct_change() * 100

    # Holgura sobre el requerimiento normativo (100%)
    df_resampled['holgura_pp'] = df_resampled[ratio_col] - 100.0
    df_resampled['holgura_monto'] = df_resampled[num_col] - df_resampled[den_col]

    return df_resampled


def get_kpis_indicador(
    df_resampled: pd.DataFrame,
    ratio_col: str,
    num_col: str,
    den_col: str,
    limite_normativo: float = 100.0,
    limite_interno: float = 105.0
) -> Dict[str, Any]:
    """
    Extrae los KPIs ejecutivos del último período disponible y su comparación
    con el período inmediatamente anterior.
    """
    if len(df_resampled) == 0:
        return {}

    ultimo = df_resampled.iloc[-1]
    anterior = df_resampled.iloc[-2] if len(df_resampled) > 1 else ultimo

    # Estado de semáforo regulatorio (Paleta Bloomberg)
    ratio_val = float(ultimo[ratio_col])
    if ratio_val < limite_normativo:
        semaforo = "CRÍTICO"  # Rojo Bloomberg
        semaforo_color = "#fe443d"
        semaforo_msg = "Incumplimiento Normativo (< 100%)"
    elif ratio_val < limite_interno:
        semaforo = "ALERTA"   # Naranja Bloomberg
        semaforo_color = "#fb8b1f"
        semaforo_msg = f"Bajo Límite Interno Apetito ({limite_interno}%)"
    else:
        semaforo = "ÓPTIMO"   # Menta Bloomberg
        semaforo_color = "#4af6c4"
        semaforo_msg = "En Cumplimiento y Holgura Confortable"

    delta_ratio = float(ultimo['delta_ratio']) if not pd.isna(ultimo['delta_ratio']) else 0.0
    delta_num = float(ultimo['delta_num']) if not pd.isna(ultimo['delta_num']) else 0.0
    delta_den = float(ultimo['delta_den']) if not pd.isna(ultimo['delta_den']) else 0.0

    delta_ratio_pct = float(ultimo['delta_ratio_pct']) if not pd.isna(ultimo['delta_ratio_pct']) else 0.0
    delta_num_pct = float(ultimo['delta_num_pct']) if not pd.isna(ultimo['delta_num_pct']) else 0.0
    delta_den_pct = float(ultimo['delta_den_pct']) if not pd.isna(ultimo['delta_den_pct']) else 0.0

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
        "semaforo_msg": semaforo_msg,
        "limite_normativo": limite_normativo,
        "limite_interno": limite_interno
    }


def analyze_decomposition(
    df_detalle: pd.DataFrame,
    fecha_referencia: pd.Timestamp = None,
    componente_filtro: str = None
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Analiza la descomposición de un indicador en la fecha seleccionada
    y calcula la variación respecto a la fecha anterior disponible.
    
    Args:
        df_detalle: DataFrame de detalle con columnas [fecha, componente, categoria, subcategoria, ...]
        fecha_referencia: Fecha a analizar. Si es None, toma la más reciente.
        componente_filtro: 'ALAC', 'NAF', 'FED' o 'FER'. Si es None, trae ambos.
        
    Returns:
        (df_resumen_categoria, df_detalle_completo)
    """
    df = df_detalle.copy()
    df['fecha'] = pd.to_datetime(df['fecha'])
    
    if componente_filtro:
        df = df[df['componente'] == componente_filtro]

    fechas_disponibles = sorted(df['fecha'].unique())
    if len(fechas_disponibles) == 0:
        return pd.DataFrame(), pd.DataFrame()

    if fecha_referencia is None or fecha_referencia not in fechas_disponibles:
        fecha_act = fechas_disponibles[-1]
    else:
        fecha_act = fecha_referencia

    # Buscar fecha previa para variación
    idx_act = fechas_disponibles.index(fecha_act)
    fecha_ant = fechas_disponibles[idx_act - 1] if idx_act > 0 else None

    df_act = df[df['fecha'] == fecha_act].copy()
    
    if fecha_ant is not None:
        df_ant = df[df['fecha'] == fecha_ant].copy()
        
        # Merge para calcular variación por subcategoría
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
        df_act['delta_ponderado'] = 0.0
        df_act['delta_nominal'] = 0.0
        df_act['delta_pct'] = 0.0
        df_detalle_res = df_act

    # Agrupación por Categoría ejecutiva
    df_cat = df_detalle_res.groupby(['componente', 'categoria']).agg(
        monto_nominal=('monto_nominal', 'sum'),
        monto_ponderado=('monto_ponderado', 'sum'),
        delta_ponderado=('delta_ponderado', 'sum')
    ).reset_index()

    # Participación porcentual dentro de cada componente
    total_por_comp = df_cat.groupby('componente')['monto_ponderado'].transform('sum')
    df_cat['participacion_pct'] = np.where(total_por_comp != 0, (df_cat['monto_ponderado'] / total_por_comp) * 100, 0)
    
    return df_cat, df_detalle_res
