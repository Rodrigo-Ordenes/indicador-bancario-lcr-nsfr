"""
components/tables.py
====================
Componentes de presentación para tablas de descomposición analítica y regulatoria.
Formateo numérico institucional, filtros interactivos y exportación de datos.
"""

import streamlit as st
import pandas as pd
import numpy as np

def render_decomposition_table(df_detalle: pd.DataFrame, componente: str, titulo: str = None):
    """
    Renderiza la tabla de descomposición detallada con formato bancario y métricas.
    """
    if len(df_detalle) == 0:
        st.info(f"No hay registros de descomposición para {componente}.")
        return

    st.markdown(f"#### 📋 {titulo or f'Detalle Descompuesto de {componente}'}")

    # Filtrar por componente
    df_comp = df_detalle[df_detalle['componente'] == componente].copy()
    if len(df_comp) == 0:
        st.info(f"No hay registros para {componente}")
        return

    # Total general del componente
    total_ponderado = df_comp['monto_ponderado'].sum()
    total_delta = df_comp['delta_ponderado'].sum() if 'delta_ponderado' in df_comp.columns else 0.0

    m1, m2, m3 = st.columns(3)
    with m1:
        st.metric(
            label=f"Total Computable {componente}",
            value=f"${total_ponderado:,.0f} MM",
            delta=f"${total_delta:+,.0f} MM vs período ant."
        )
    with m2:
        num_cuentas = df_comp['subcategoria'].nunique()
        st.metric(label="Líneas Contables / Cuentas", value=f"{num_cuentas}")
    with m3:
        st.metric(label="Fecha de Corte Detalle", value=f"{df_comp['fecha'].iloc[0].strftime('%d-%m-%Y')}")

    # Preparar DataFrame para visualización
    cols_display = ['categoria', 'subcategoria', 'monto_nominal', 'factor_ponderacion', 'monto_ponderado']
    rename_dict = {
        'categoria': 'Categoría Regulatoria',
        'subcategoria': 'Cuenta / Instrumento',
        'monto_nominal': 'Monto Bruto (MM$)',
        'factor_ponderacion': 'Ponderador (%)',
        'monto_ponderado': 'Monto Computable (MM$)'
    }

    if 'delta_ponderado' in df_comp.columns:
        cols_display.append('delta_ponderado')
        rename_dict['delta_ponderado'] = 'Variación Δ (MM$)'
    
    if 'delta_pct' in df_comp.columns:
        cols_display.append('delta_pct')
        rename_dict['delta_pct'] = 'Var %'

    df_view = df_comp[cols_display].copy()
    
    # Calcular % de participación sobre el total del componente
    if total_ponderado != 0:
        df_view['participacion'] = (df_view['monto_ponderado'] / total_ponderado) * 100
        cols_display.append('participacion')
        rename_dict['participacion'] = 'Part. (%)'

    df_view = df_view.rename(columns=rename_dict)

    # Ordenar por monto computable descendente
    df_view = df_view.sort_values(by='Monto Computable (MM$)', ascending=False)

    # Estilizar tabla con formato numérico
    st.dataframe(
        df_view.style.format({
            'Monto Bruto (MM$)': '${:,.0f}',
            'Ponderador (%)': '{:.1%}' if df_view['Ponderador (%)'].max() <= 1.0 else '{:.1f}%',
            'Monto Computable (MM$)': '${:,.0f}',
            'Variación Δ (MM$)': '${:+,.0f}',
            'Var %': '{:+.1f}%',
            'Part. (%)': '{:.1f}%'
        }),
        width='stretch',
        hide_index=True
    )

    # Botón para descargar en CSV
    csv_data = df_view.to_csv(index=False).encode('utf-8')
    st.download_button(
        label=f"📥 Descargar Detalle {componente} (CSV)",
        data=csv_data,
        file_name=f"detalle_{componente.lower()}_{df_comp['fecha'].iloc[0].strftime('%Y%m%d')}.csv",
        mime="text/csv"
    )
