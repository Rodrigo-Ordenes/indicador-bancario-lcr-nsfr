"""
app.py
======
Aplicación Principal en Streamlit para el Dashboard Ejecutivo de Indicadores Bancarios:
- LCR (Liquidity Coverage Ratio)
- NSFR (Net Stable Funding Ratio)

Vistas temporales: Diaria, Semanal, Mensual y Anual.
Vistas analíticas: Vista Ejecutiva, Vista de Descomposición Detallada y Simulador de Estrés ALM.
Integración con Inteligencia Artificial (Copilot / OpenAI / Motor Heurístico Cuantitativo).

Autor: Equipo de Finanzas / ALM
"""

import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, date

# Configuración de página de Streamlit (debe ser la primera llamada de Streamlit)
st.set_page_config(
    page_title="Dashboard Ejecutivo LCR & NSFR | ALM Bancario",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Importar módulos locales
from styles import CUSTOM_CSS
from data_loader import (
    load_lcr_historico,
    load_lcr_detalle,
    load_nsfr_historico,
    load_nsfr_detalle
)
from analytics import (
    resample_historico,
    get_kpis_indicador,
    analyze_decomposition
)
from components.ui_cards import render_kpi_cards
from components.charts import (
    plot_indicator_trend,
    plot_composition_evolution,
    plot_period_variations,
    plot_waterfall_bridge,
    plot_category_breakdown
)
from components.tables import render_decomposition_table
from ai_analyst import (
    generate_heuristic_executive_analysis,
    call_llm_copilot_analysis
)
from components.core_architecture_view import render_core_architecture_page

# Inyectar CSS personalizado
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# =============================================================================
# 1. BARRA LATERAL (SIDEBAR) - CONTROLES Y FILTROS GLOBALES
# =============================================================================
st.sidebar.image(
    "https://img.icons8.com/fluency/96/bank-building.png",
    width=64
)
st.sidebar.markdown("### 🏛️ Control ALM & Riesgo")
st.sidebar.caption("Supervisión de Liquidez y Balance Basilea III")

# Selector de Indicador
indicador_activo = st.sidebar.radio(
    "Seleccionar Indicador / Módulo:",
    ["💧 LCR (Cobertura de Liquidez)", "🏦 NSFR (Fondeo Estable Neto)", "📊 Visión Consolidada ALM", "🏛️ Arquitectura Core & Auditoría Bitemporal"],
    index=0
)

# Selector de Granularidad Temporal
st.sidebar.markdown("---")
st.sidebar.markdown("#### ⏱️ Granularidad Temporal")
granularidad = st.sidebar.selectbox(
    "Frecuencia de Análisis:",
    ["Diaria", "Semanal", "Mensual", "Anual"],
    index=0,
    help="Agrupa los datos al cierre de cada día, semana (viernes), fin de mes o fin de año."
)

# Selector de Tipo de Vista
st.sidebar.markdown("#### 👁️ Tipo de Vista")
tipo_vista = st.sidebar.radio(
    "Modo de Visualización:",
    ["📊 Vista Ejecutiva", "🔍 Vista Detallada Descompuesta", "🧪 Simulador de Estrés ALM"],
    index=0
)

# Umbrales Regulatorios
st.sidebar.markdown("---")
with st.sidebar.expander("⚙️ Parámetros Regulatorios y Apetito", expanded=False):
    limite_normativo = st.number_input("Mínimo Regulatorio CMF (%)", value=100.0, step=1.0)
    limite_interno = st.number_input("Límite Interno Apetito (%)", value=105.0, step=1.0)

# Configuración de IA (Copilot / OpenAI)
with st.sidebar.expander("🤖 Configuración de IA (Copilot)", expanded=False):
    st.caption("Configura tu API Key para activar el análisis con IA Generativa.")
    ai_provider = st.selectbox("Proveedor LLM:", ["OpenAI / Copilot API", "Azure OpenAI"], index=0)
    copilot_api_key = st.text_input("API Key (opcional):", type="password")
    copilot_model = st.text_input("Modelo:", value="gpt-4o")
    copilot_endpoint = st.text_input("Endpoint personalizado (opcional):", value="")

st.sidebar.markdown("---")
st.sidebar.info(
    "**Arquitectura de Datos:**\n"
    "Se alimenta de 4 DataFrames modulares en `data_loader.py`. "
    "Revisa los comentarios en el código para conectar tus consultas SQL."
)


# =============================================================================
# 2. CARGA DE DATOS (LOS 4 DATAFRAMES)
# =============================================================================
df_lcr_hist = load_lcr_historico()
df_lcr_det = load_lcr_detalle()
df_nsfr_hist = load_nsfr_historico()
df_nsfr_det = load_nsfr_detalle()


# =============================================================================
# 3. BANNER PRINCIPAL DE ENCABEZADO
# =============================================================================
fecha_hoy_str = datetime.today().strftime('%d/%m/%Y')
st.markdown(f"""
<div class="header-banner">
    <div>
        <h1 class="header-title">
            <span>PANEL EJECUTIVO DE LIQUIDEZ Y BALANCE</span>
            <span class="header-title-tag">BLOOMBERG ALM</span>
        </h1>
        <div class="header-subtitle">Supervisión Regulatoria Basilea III / CMF — Cierre al {fecha_hoy_str}</div>
    </div>
    <div style="text-align: right;">
        <span class="badge-status">● TERMINAL EN VIVO</span>
        <div style="font-size: 11px; color: #8b949e; margin-top: 6px; font-family: 'JetBrains Mono', monospace;">FRECUENCIA: {granularidad.upper()}</div>
    </div>
</div>
""", unsafe_allow_html=True)


# =============================================================================
# 4. FUNCIÓN RENDERIZADORA DE UN INDICADOR (LCR O NSFR)
# =============================================================================
def render_indicador_flow(
    df_hist: pd.DataFrame,
    df_det: pd.DataFrame,
    ratio_col: str,
    num_col: str,
    den_col: str,
    ratio_nombre: str,
    num_nombre: str,
    den_nombre: str,
    ratio_desc: str
):
    # 1. Remuestreo según granularidad seleccionada
    df_resampled = resample_historico(
        df=df_hist,
        fecha_col='fecha',
        ratio_col=ratio_col,
        num_col=num_col,
        den_col=den_col,
        granularidad=granularidad
    )

    # 2. Extracción de KPIs ejecutivos
    kpis = get_kpis_indicador(
        df_resampled=df_resampled,
        ratio_col=ratio_col,
        num_col=num_col,
        den_col=den_col,
        limite_normativo=limite_normativo,
        limite_interno=limite_interno
    )

    if not kpis:
        st.error("No se pudieron calcular las métricas con los datos disponibles.")
        return

    # Selector de subvista
    if tipo_vista == "📊 Vista Ejecutiva":
        # Despliegue de tarjetas de métricas
        render_kpi_cards(
            kpis=kpis,
            ratio_nombre=ratio_nombre,
            num_nombre=num_nombre,
            den_nombre=den_nombre,
            granularidad=granularidad
        )

        st.markdown("<div style='margin-top: 24px;'></div>", unsafe_allow_html=True)

        # Fila 1 de Gráficos: Cascada de Atribución (Waterfall) y Gráfico de Tendencia
        c1, c2 = st.columns([1, 1])
        with c1:
            fig_waterfall = plot_waterfall_bridge(
                ratio_ant=kpis['ratio_anterior'],
                ratio_act=kpis['ratio_actual'],
                num_ant=kpis['num_anterior'],
                num_act=kpis['num_actual'],
                den_ant=kpis['den_anterior'],
                den_act=kpis['den_actual'],
                num_nombre=num_nombre,
                den_nombre=den_nombre,
                ratio_nombre=ratio_nombre
            )
            st.plotly_chart(fig_waterfall, width='stretch')

        with c2:
            fig_trend = plot_indicator_trend(
                df=df_resampled,
                fecha_col='fecha',
                ratio_col=ratio_col,
                ratio_nombre=ratio_nombre,
                limite_normativo=limite_normativo,
                limite_interno=limite_interno
            )
            st.plotly_chart(fig_trend, width='stretch')

        # Fila 2 de Gráficos: Composición en Doble Eje y Variaciones Período a Período
        c3, c4 = st.columns([1, 1])
        with c3:
            fig_comp = plot_composition_evolution(
                df=df_resampled,
                fecha_col='fecha',
                num_col=num_col,
                den_col=den_col,
                num_nombre=num_nombre,
                den_nombre=den_nombre,
                ratio_col=ratio_col,
                ratio_nombre=ratio_nombre
            )
            st.plotly_chart(fig_comp, width='stretch')

        with c4:
            fig_var = plot_period_variations(
                df=df_resampled,
                fecha_col='fecha',
                ratio_col=ratio_col,
                num_col=num_col,
                den_col=den_col,
                num_nombre=num_nombre,
                den_nombre=den_nombre,
                ratio_nombre=ratio_nombre
            )
            st.plotly_chart(fig_var, width='stretch')

        # Apartado de Análisis con Inteligencia Artificial
        st.markdown("<div style='margin-top: 20px;'></div>", unsafe_allow_html=True)
        st.markdown(f"""
        <div class="ai-box">
            <div class="ai-header">
                <span>🤖 Informe y Diagnóstico Ejecutivo ALM</span>
                <span class="ai-badge">Motor Cuantitativo & Copilot</span>
            </div>
            <div style="font-size: 13px; color: #94a3b8; margin-bottom: 12px;">
                Análisis interpretativo automático de las variaciones de {ratio_nombre}, descomposición de drivers y recomendaciones para el Comité ALCO.
            </div>
        """, unsafe_allow_html=True)

        # Obtener detalle más reciente para enriquecer el análisis
        _, df_det_recent = analyze_decomposition(df_det)
        
        # Generar análisis heurístico instantáneo
        analisis_base = generate_heuristic_executive_analysis(
            kpis=kpis,
            df_detalle=df_det_recent,
            ratio_nombre=ratio_nombre,
            num_nombre=num_nombre,
            den_nombre=den_nombre,
            granularidad=granularidad
        )
        st.markdown(analisis_base)

        # Botón para generar con Copilot / LLM
        col_btn1, col_btn2 = st.columns([1, 3])
        with col_btn1:
            btn_copilot = st.button(
                f"✨ Generar Memo con Copilot ({copilot_model})", 
                key=f"btn_copilot_{ratio_nombre}"
            )

        if btn_copilot:
            with st.spinner("Conectando con motor Copilot / LLM para redactar el memorándum ALCO..."):
                copilot_result = call_llm_copilot_analysis(
                    kpis=kpis,
                    df_detalle=df_det_recent,
                    ratio_nombre=ratio_nombre,
                    num_nombre=num_nombre,
                    den_nombre=den_nombre,
                    granularidad=granularidad,
                    api_key=copilot_api_key if copilot_api_key else None,
                    api_base=copilot_endpoint if copilot_endpoint else None,
                    model_name=copilot_model
                )
                st.markdown("---")
                st.markdown("#### 📝 Memorándum de Alta Dirección Generado por Copilot:")
                st.markdown(copilot_result)

        st.markdown("</div>", unsafe_allow_html=True)

    elif tipo_vista == "🔍 Vista Detallada Descompuesta":
        st.markdown(f"### 🔍 Descomposición Analítica y Regulatoria de {ratio_nombre}")
        st.caption(f"Detalle granular por cuentas y factores de ponderación ({ratio_desc})")

        # Selector de Fecha dentro de las disponibles en el detalle
        fechas_det = sorted(pd.to_datetime(df_det['fecha']).unique(), reverse=True)
        if len(fechas_det) == 0:
            st.warning("No hay fechas de detalle registradas.")
            return

        col_f1, col_f2 = st.columns([1, 2])
        with col_f1:
            fecha_sel = st.selectbox(
                "Fecha de Corte del Detalle:",
                fechas_det,
                format_func=lambda d: d.strftime('%d-%m-%Y (%A)')
            )

        df_cat, df_det_res = analyze_decomposition(df_det, fecha_referencia=fecha_sel)

        tab1, tab2 = st.tabs([f"🟢 Descomposición de {num_nombre}", f"🔴 Descomposición de {den_nombre}"])

        with tab1:
            col_g1, col_t1 = st.columns([1, 1])
            df_cat_num = df_cat[df_cat['componente'] == num_nombre]
            with col_g1:
                fig_cat_num = plot_category_breakdown(
                    df_cat=df_cat_num,
                    componente_nombre=num_nombre,
                    titulo=f"Distribución por Categorías: {num_nombre}"
                )
                st.plotly_chart(fig_cat_num, width='stretch')

            with col_t1:
                st.markdown(f"##### Resumen de Categorías ({num_nombre})")
                st.dataframe(
                    df_cat_num[['categoria', 'monto_nominal', 'monto_ponderado', 'participacion_pct']].rename(
                        columns={
                            'categoria': 'Categoría',
                            'monto_nominal': 'Bruto (MM$)',
                            'monto_ponderado': 'Computable (MM$)',
                            'participacion_pct': 'Part. (%)'
                        }
                    ).style.format({
                        'Bruto (MM$)': '${:,.0f}',
                        'Computable (MM$)': '${:,.0f}',
                        'Part. (%)': '{:.1f}%'
                    }),
                    width='stretch',
                    hide_index=True
                )

            # Tabla completa detallada por subcategoría
            render_decomposition_table(df_det_res, componente=num_nombre, titulo=f"Detalle Completo de Cuentas: {num_nombre}")

        with tab2:
            col_g2, col_t2 = st.columns([1, 1])
            df_cat_den = df_cat[df_cat['componente'] == den_nombre]
            with col_g2:
                fig_cat_den = plot_category_breakdown(
                    df_cat=df_cat_den,
                    componente_nombre=den_nombre,
                    titulo=f"Distribución por Categorías: {den_nombre}"
                )
                st.plotly_chart(fig_cat_den, width='stretch')

            with col_t2:
                st.markdown(f"##### Resumen de Categorías ({den_nombre})")
                st.dataframe(
                    df_cat_den[['categoria', 'monto_nominal', 'monto_ponderado', 'participacion_pct']].rename(
                        columns={
                            'categoria': 'Categoría',
                            'monto_nominal': 'Bruto (MM$)',
                            'monto_ponderado': 'Computable (MM$)',
                            'participacion_pct': 'Part. (%)'
                        }
                    ).style.format({
                        'Bruto (MM$)': '${:,.0f}',
                        'Computable (MM$)': '${:,.0f}',
                        'Part. (%)': '{:.1f}%'
                    }),
                    width='stretch',
                    hide_index=True
                )

            # Tabla completa detallada por subcategoría
            render_decomposition_table(df_det_res, componente=den_nombre, titulo=f"Detalle Completo de Cuentas: {den_nombre}")

    elif tipo_vista == "🧪 Simulador de Estrés ALM":
        st.markdown(f"### 🧪 Simulador de Sensibilidad y Estrés (What-If): {ratio_nombre}")
        st.markdown(
            "Evalúa el impacto inmediato sobre el ratio y la holgura regulatoria ante shocks simultáneos "
            f"en los activos ({num_nombre}) y en las exigencias/salidas ({den_nombre})."
        )

        col_s1, col_s2 = st.columns(2)
        with col_s1:
            shock_num_pct = st.slider(
                f"Shock en {num_nombre} (%) [Ej. Caída en valor de activos / fugas]",
                min_value=-30.0,
                max_value=30.0,
                value=-5.0,
                step=1.0,
                help=f"Disminuye o aumenta el valor de {num_nombre}."
            )
        with col_s2:
            shock_den_pct = st.slider(
                f"Shock en {den_nombre} (%) [Ej. Incremento en corridas o fondeo requerido]",
                min_value=-30.0,
                max_value=30.0,
                value=8.0,
                step=1.0,
                help=f"Disminuye o aumenta el valor de {den_nombre}."
            )

        # Cálculo estresado
        num_stress = kpis['num_actual'] * (1.0 + shock_num_pct / 100.0)
        den_stress = kpis['den_actual'] * (1.0 + shock_den_pct / 100.0)
        ratio_stress = (num_stress / den_stress) * 100.0
        delta_stress_pp = ratio_stress - kpis['ratio_actual']

        st.markdown("#### 🎯 Resultados del Escenario de Estrés")
        r1, r2, r3, r4 = st.columns(4)
        with r1:
            st.metric(
                label=f"{ratio_nombre} Base",
                value=f"{kpis['ratio_actual']:.2f}%"
            )
        with r2:
            st.metric(
                label=f"{ratio_nombre} Estresado",
                value=f"{ratio_stress:.2f}%",
                delta=f"{delta_stress_pp:+.2f} pp"
            )
        with r3:
            st.metric(
                label=f"{num_nombre} Estresado",
                value=f"${num_stress:,.0f} MM",
                delta=f"{shock_num_pct:+.1f}%"
            )
        with r4:
            st.metric(
                label=f"{den_nombre} Estresado",
                value=f"${den_stress:,.0f} MM",
                delta=f"{shock_den_pct:+.1f}%"
            )

        # Diagnóstico de estrés
        if ratio_stress >= limite_interno:
            st.success(f"✅ **Escenario Resistente**: El banco absorbe el shock con éxito y mantiene el ratio en **{ratio_stress:.2f}%**, sobre el apetito interno ({limite_interno}%).")
        elif ratio_stress >= limite_normativo:
            st.warning(f"⚠️ **Vulnerabilidad Media**: El ratio estresado cae a **{ratio_stress:.2f}%**, por debajo del apetito interno ({limite_interno}%) pero aún en cumplimiento normativo (> {limite_normativo}%).")
        else:
            deficit_mm = den_stress - num_stress
            st.error(f"🚨 **Déficit Regulatorio**: Bajo este escenario el {ratio_nombre} colapsa a **{ratio_stress:.2f}%** (< {limite_normativo}%). El déficit de liquidez generado es de **${deficit_mm:,.0f} MM**.")


# =============================================================================
# 5. ENRUTAMIENTO SEGÚN SELECCIÓN DEL USUARIO
# =============================================================================
if "LCR" in indicador_activo:
    render_indicador_flow(
        df_hist=df_lcr_hist,
        df_det=df_lcr_det,
        ratio_col='lcr',
        num_col='alac',
        den_col='naf',
        ratio_nombre='LCR',
        num_nombre='ALAC',
        den_nombre='NAF',
        ratio_desc='Activos Líquidos de Alta Calidad (ALAC) / Salidas Netas de Efectivo (NAF)'
    )

elif "NSFR" in indicador_activo:
    render_indicador_flow(
        df_hist=df_nsfr_hist,
        df_det=df_nsfr_det,
        ratio_col='nsfr',
        num_col='fed',
        den_col='fer',
        ratio_nombre='NSFR',
        num_nombre='FED',
        den_nombre='FER',
        ratio_desc='Fondos Estables Disponibles (FED) / Fondos Estables Requeridos (FER)'
    )

elif "Consolidada" in indicador_activo:
    st.markdown("### 📊 Visión Consolidada ALM: LCR & NSFR")
    st.caption("Comparación simultánea de cobertura a corto plazo (30 días) y estabilidad de fondeo estructural (> 1 año).")

    df_lcr_res = resample_historico(df_lcr_hist, 'fecha', 'lcr', 'alac', 'naf', granularidad)
    df_nsfr_res = resample_historico(df_nsfr_hist, 'fecha', 'nsfr', 'fed', 'fer', granularidad)
    
    kpis_lcr = get_kpis_indicador(df_lcr_res, 'lcr', 'alac', 'naf', limite_normativo, limite_interno)
    kpis_nsfr = get_kpis_indicador(df_nsfr_res, 'nsfr', 'fed', 'fer', limite_normativo, limite_interno)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 💧 Ratio LCR (Liquidity Coverage Ratio)")
        render_kpi_cards(kpis_lcr, "LCR", "ALAC", "NAF", granularidad)
        st.plotly_chart(plot_indicator_trend(df_lcr_res, 'fecha', 'lcr', 'LCR', limite_normativo, limite_interno), width='stretch')

    with c2:
        st.markdown("#### 🏦 Ratio NSFR (Net Stable Funding Ratio)")
        render_kpi_cards(kpis_nsfr, "NSFR", "FED", "FER", granularidad)
        st.plotly_chart(plot_indicator_trend(df_nsfr_res, 'fecha', 'nsfr', 'NSFR', limite_normativo, limite_interno), width='stretch')

elif "Arquitectura" in indicador_activo:
    render_core_architecture_page()


# Footer Corporativo
st.markdown("---")
st.markdown("""
<div style="text-align: center; color: #94a3b8; font-size: 12px; padding: 12px;">
    Dashboard Financiero ALM | Cumplimiento Regulatorio Basilea III & CMF | Desarrollado para Gerencia de Finanzas y Riesgo
</div>
""", unsafe_allow_html=True)
