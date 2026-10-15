"""
components/tables.py
====================
Componentes de presentación para tablas de descomposición analítica y regulatoria.
Formateo numérico institucional estilo Bloomberg Terminal, identificación de Top Drivers,
cierre diario (Hoy vs Ayer) y consolidación temporal (Semanal, Mensual, Anual).
"""

import streamlit as st
import pandas as pd
import numpy as np
from typing import Dict, Any, Optional

def render_decomposition_table(
    df_detalle: pd.DataFrame,
    componente: str,
    titulo: Optional[str] = None,
    granularidad: str = "Diaria",
    metadata: Optional[Dict[str, Any]] = None,
    *args,
    **kwargs
):
    """
    Renderiza la tabla de descomposición detallada con formato bancario institucional:
    - Vista diaria: Cierre de Hoy vs Cierre de Ayer (EOD sin movimiento intradía).
    - Vistas agrupadas: Semanal, Mensual, Anual.
    - Identificación automática de Top Drivers (mayores aumentos y reducciones).
    - Métricas de resumen, ponderadores regulatorios y descarga CSV.
    """
    if df_detalle is None or len(df_detalle) == 0:
        st.info(f"No hay registros de descomposición para {componente}.")
        return

    # Filtrar por componente
    df_comp = df_detalle[df_detalle['componente'] == componente].copy()
    if len(df_comp) == 0:
        st.info(f"No hay registros para {componente}")
        return

    # Verificar si ya cuenta con columnas de período anterior, si no, prepararlas
    if 'monto_ponderado_ant' not in df_comp.columns:
        df_comp['monto_ponderado_ant'] = 0.0
        df_comp['monto_nominal_ant'] = 0.0
        df_comp['delta_ponderado'] = 0.0
        df_comp['delta_nominal'] = 0.0
        df_comp['delta_pct'] = 0.0

    # Labels temporales
    if metadata and "periodo_act_lbl" in metadata:
        periodo_act_lbl = metadata["periodo_act_lbl"]
        periodo_ant_lbl = metadata["periodo_ant_lbl"]
        comp_label = metadata.get("comp_label", "vs período ant.")
    else:
        if granularidad == "Diaria":
            periodo_act_lbl = "Cierre de Hoy"
            periodo_ant_lbl = "Cierre de Ayer"
            comp_label = "vs ayer"
        elif granularidad == "Semanal":
            periodo_act_lbl = "Cierre Semana Actual"
            periodo_ant_lbl = "Cierre Semana Anterior"
            comp_label = "vs semana ant."
        elif granularidad == "Mensual":
            periodo_act_lbl = "Cierre Mes Actual"
            periodo_ant_lbl = "Cierre Mes Anterior"
            comp_label = "vs mes ant."
        else:
            periodo_act_lbl = "Cierre Año Actual"
            periodo_ant_lbl = "Cierre Año Anterior"
            comp_label = "vs año ant."

    st.markdown(f"#### {titulo or f'Detalle Descompuesto de {componente}'}")

    # Mensaje metodológico ALM para la Alta Administración
    if granularidad == "Diaria":
        st.caption(
            "📌 **Metodología de Balance EOD (End of Day)**: Las posiciones de balance bancario se consolidan al cierre de cada jornada. "
            "No existe movimiento intradía en la reportería regulatoria; la variación refleja estrictamente **Cierre de Hoy vs Cierre de Ayer**."
        )
    else:
        st.caption(
            f"📌 **Consolidación Temporal {granularidad.upper()}**: Comparación de posiciones de cierre entre **{periodo_act_lbl}** y **{periodo_ant_lbl}**."
        )

    # Totales generales del componente
    total_ponderado = float(df_comp['monto_ponderado'].sum())
    total_ponderado_ant = float(df_comp['monto_ponderado_ant'].sum())
    total_delta = total_ponderado - total_ponderado_ant
    total_delta_pct = (total_delta / total_ponderado_ant * 100.0) if total_ponderado_ant != 0 else 0.0

    fecha_corte = pd.to_datetime(df_comp['fecha'].iloc[0])
    fecha_corte_str = fecha_corte.strftime('%d-%m-%Y')

    # Fila de métricas resumen del componente
    m1, m2, m3 = st.columns(3)
    with m1:
        st.metric(
            label=f"Total Computable ({periodo_act_lbl})",
            value=f"${total_ponderado:,.0f} MM",
            delta=f"${total_delta:+,.0f} MM ({total_delta_pct:+.1f}%) {comp_label}"
        )
    with m2:
        st.metric(
            label=f"Total Computable ({periodo_ant_lbl})",
            value=f"${total_ponderado_ant:,.0f} MM"
        )
    with m3:
        num_cuentas = df_comp['subcategoria'].nunique()
        st.metric(
            label="Líneas Contables / Instrumentos",
            value=f"{num_cuentas} cuentas",
            delta=f"Corte: {fecha_corte_str}"
        )

    # -------------------------------------------------------------------------
    # IDENTIFICACIÓN DE PRINCIPALES FACTORES DE CAMBIO (TOP DRIVERS) (Req 1.3 & 2.3)
    # -------------------------------------------------------------------------
    top_alzas = df_comp[df_comp['delta_ponderado'] > 0].sort_values(by='delta_ponderado', ascending=False).head(3)
    top_bajas = df_comp[df_comp['delta_ponderado'] < 0].sort_values(by='delta_ponderado', ascending=True).head(3)

    st.markdown(f"##### Factores Explicativos de la Variación ({comp_label.upper()})")
    col_d1, col_d2 = st.columns(2)

    with col_d1:
        st.markdown(f"""
        <div style="background: #0D0D0D; border: 1px solid #262626; border-left: 3px solid #4af6c4; padding: 12px; border-radius: 4px; min-height: 110px;">
            <div style="font-family: IBM Plex Mono, monospace; font-size: 11px; font-weight: 700; color: #4af6c4; text-transform: uppercase;">
                ▲ Mayores Incrementos ({comp_label})
            </div>
        """, unsafe_allow_html=True)
        if len(top_alzas) > 0:
            for _, r in top_alzas.iterrows():
                subcat = r['subcategoria']
                d_val = r['delta_ponderado']
                d_p = r['delta_pct']
                st.markdown(
                    f'<div style="font-size: 12px; color: #FFFFFF; margin-top: 4px; display: flex; justify-content: space-between;">'
                    f'<span>• {subcat}</span>'
                    f'<span style="font-family: IBM Plex Mono, monospace; color: #4af6c4; font-weight: 600;">+${d_val:,.0f} MM (+{d_p:.1f}%)</span>'
                    f'</div>',
                    unsafe_allow_html=True
                )
        else:
            st.markdown('<div style="font-size: 12px; color: #888888; margin-top: 6px;">Sin incrementos registrados en el período.</div>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with col_d2:
        st.markdown(f"""
        <div style="background: #0D0D0D; border: 1px solid #262626; border-left: 3px solid #fe443d; padding: 12px; border-radius: 4px; min-height: 110px;">
            <div style="font-family: IBM Plex Mono, monospace; font-size: 11px; font-weight: 700; color: #fe443d; text-transform: uppercase;">
                ▼ Mayores Reducciones ({comp_label})
            </div>
        """, unsafe_allow_html=True)
        if len(top_bajas) > 0:
            for _, r in top_bajas.iterrows():
                subcat = r['subcategoria']
                d_val = r['delta_ponderado']
                d_p = r['delta_pct']
                st.markdown(
                    f'<div style="font-size: 12px; color: #FFFFFF; margin-top: 4px; display: flex; justify-content: space-between;">'
                    f'<span>• {subcat}</span>'
                    f'<span style="font-family: IBM Plex Mono, monospace; color: #fe443d; font-weight: 600;">-${abs(d_val):,.0f} MM ({d_p:.1f}%)</span>'
                    f'</div>',
                    unsafe_allow_html=True
                )
        else:
            st.markdown('<div style="font-size: 12px; color: #888888; margin-top: 6px;">Sin reducciones registradas en el período.</div>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    st.write("")

    # -------------------------------------------------------------------------
    # TABLA DETALLADA CON FORMATO NUMÉRICO INSTITUCIONAL
    # -------------------------------------------------------------------------
    # Determinar nombres de columnas según granularidad
    if granularidad == "Diaria":
        col_nom_act = "Nominal Hoy (MM$)"
        col_nom_ant = "Nominal Ayer (MM$)"
        col_comp_act = "Computable Hoy (MM$)"
        col_comp_ant = "Computable Ayer (MM$)"
        col_part = "Part. Hoy (%)"
    else:
        col_nom_act = f"Nominal {periodo_act_lbl} (MM$)"
        col_nom_ant = f"Nominal {periodo_ant_lbl} (MM$)"
        col_comp_act = f"Computable {periodo_act_lbl} (MM$)"
        col_comp_ant = f"Computable {periodo_ant_lbl} (MM$)"
        col_part = "Part. Actual (%)"

    cols_source = [
        'categoria', 'subcategoria',
        'monto_nominal', 'monto_nominal_ant',
        'factor_ponderacion',
        'monto_ponderado', 'monto_ponderado_ant',
        'delta_ponderado', 'delta_pct'
    ]

    rename_map = {
        'categoria': 'Categoría Regulatoria',
        'subcategoria': 'Cuenta / Instrumento',
        'monto_nominal': col_nom_act,
        'monto_nominal_ant': col_nom_ant,
        'factor_ponderacion': 'Ponderador (%)',
        'monto_ponderado': col_comp_act,
        'monto_ponderado_ant': col_comp_ant,
        'delta_ponderado': 'Δ Variación (MM$)',
        'delta_pct': 'Δ Var (%)'
    }

    df_view = df_comp[cols_source].copy()

    # Participación sobre el total del componente al corte actual
    if total_ponderado != 0:
        df_view[col_part] = (df_view['monto_ponderado'] / total_ponderado) * 100.0
    else:
        df_view[col_part] = 0.0

    df_view = df_view.rename(columns=rename_map)

    # Ordenar por monto computable actual descendente
    df_view = df_view.sort_values(by=col_comp_act, ascending=False)

    # Formateo numérico para Streamlit
    format_dict = {
        col_nom_act: '${:,.0f}',
        col_nom_ant: '${:,.0f}',
        'Ponderador (%)': '{:.1%}' if df_view['Ponderador (%)'].max() <= 1.0 else '{:.1f}%',
        col_comp_act: '${:,.0f}',
        col_comp_ant: '${:,.0f}',
        'Δ Variación (MM$)': '${:+,.0f}',
        'Δ Var (%)': '{:+.1f}%',
        col_part: '{:.1f}%'
    }

    st.dataframe(
        df_view.style.format(format_dict),
        width='stretch',
        hide_index=True
    )

    # Descarga CSV
    csv_data = df_view.to_csv(index=False).encode('utf-8')
    st.download_button(
        label=f"📥 Descargar Detalle {componente} ({granularidad}) en CSV",
        data=csv_data,
        file_name=f"detalle_{componente.lower()}_{granularidad.lower()}_{fecha_corte.strftime('%Y%m%d')}.csv",
        mime="text/csv"
    )
