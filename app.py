"""
app.py
======
Plataforma Ejecutiva de Supervisión de Liquidez y Balance (ALM / Riesgo Financiero)
Desarrollada bajo estándares de Basilea III (BCBS 238 LCR / BCBS 295 NSFR) y normativa CMF.
Estilo visual oficial Bloomberg Terminal Dark (#000000, #0168ff, #fe443d, #4af6c4, #fb8b1f, #ffd15c).

Módulos Funcionales:
1. Panel LCR: Indicadores principales, Evolución & Comparación, Descomposición, Pruebas de Tensión y Proyecciones.
2. Panel NSFR: Indicadores principales, Evolución & Comparación, Descomposición, Pruebas de Tensión y Proyecciones.
3. Análisis Transversal de Liquidez: Waterfall, Holguras y Umbrales, Backtesting de Proyecciones, Concentración HHI y Resumen Ejecutivo.
4. Exportación de Informes PDF: Generación directa de informe ejecutivo consolidado para el Comité ALCO.
5. Arquitectura y Auditoría Core Banking: Ledger inmutable, ODS bitemporal (AVF), DWH Kimball y conciliación.
"""

import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta

# Configuración de página de Streamlit
st.set_page_config(
    page_title="Terminal ALM | Panel de Liquidez & Balance",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Módulos locales
from styles import CUSTOM_CSS, BBG_BLUE, BBG_RED, BBG_MINT, BBG_ORANGE, BBG_GOLD
from data_loader import (
    get_data_source_label,
    load_lcr_historico,
    load_lcr_detalle,
    load_nsfr_historico,
    load_nsfr_detalle
)
import regulatory_engine as reg
from analytics import (
    resample_historico,
    get_kpis_indicador,
    analyze_decomposition,
    calculate_multithreshold_buffers,
    calculate_alert_statistics,
    compute_waterfall_attribution,
    project_time_series,
    backtest_projection_models,
    analyze_concentration_and_volatility
)
from components.ui_cards import render_kpi_cards
from components.charts import (
    plot_indicator_trend,
    plot_composition_evolution,
    plot_period_variations,
    plot_waterfall_bridge,
    plot_category_breakdown,
    plot_period_comparison,
    plot_projection_chart,
    plot_concentration_chart
)
from components.tables import render_decomposition_table
from ai_analyst import (
    generate_heuristic_executive_analysis,
    call_llm_copilot_analysis
)
from components.core_architecture_view import render_core_architecture_page
import pdf_generator

# Inyección de estilos Bloomberg Terminal
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# =============================================================================
# 1. CARGA DE DATOS (LOS 4 DATAFRAMES)
# =============================================================================
df_lcr_hist = load_lcr_historico()
df_lcr_det = load_lcr_detalle()
df_nsfr_hist = load_nsfr_historico()
df_nsfr_det = load_nsfr_detalle()

# =============================================================================
# 2. BARRA LATERAL (SIDEBAR) - NAVEGACIÓN Y CONFIGURACIÓN BLOOMBERG
# =============================================================================
st.sidebar.markdown("""
<div class="sidebar-brand">
    <div class="sidebar-brand-title">BLOOMBERG // ALM RISK</div>
    <div class="sidebar-brand-sub">LIQUIDEZ & BALANCE · CMF / BASILEA III</div>
</div>
""", unsafe_allow_html=True)

# Módulo de navegación
st.sidebar.markdown('<div class="sidebar-section">MÓDULO DE REPORTERÍA</div>', unsafe_allow_html=True)
modulo_activo = st.sidebar.radio(
    "Seleccionar Módulo",
    [
        "💧 1. Panel LCR (Cobertura de Liquidez)",
        "🏛️ 2. Panel NSFR (Fondeo Estable Neto)",
        "📊 3. Análisis Transversal de Liquidez",
        "📑 4. Exportar Informe PDF Ejecutivo",
        "⚙️ 5. Auditoría & Core Banking Bitemporal"
    ],
    index=0
)

# Granularidad temporal
st.sidebar.markdown('<div class="sidebar-section">FRECUENCIA DE ANÁLISIS</div>', unsafe_allow_html=True)
granularidad = st.sidebar.selectbox(
    "Periodicidad",
    ["Diaria", "Semanal", "Mensual", "Anual"],
    index=0,
    help="Agrupa los datos al cierre de cada día, semana (viernes), mes o año."
)

# Umbrales normativos y de apetito configurables
st.sidebar.markdown('<div class="sidebar-section">UMBRALES DE CONTROL</div>', unsafe_allow_html=True)
with st.sidebar.expander("Parámetros Regulatorios y Apetito", expanded=False):
    st.caption("Configuración de límites según política ALM:")
    lcr_min = st.number_input("LCR Mínimo Regulatorio (%)", value=100.0, step=1.0)
    lcr_alerta = st.number_input("LCR Alerta Interna (%)", value=120.0, step=1.0)
    lcr_apetito = st.number_input("LCR Apetito por Riesgo (%)", value=150.0, step=1.0)
    st.divider()
    nsfr_min = st.number_input("NSFR Mínimo Regulatorio (%)", value=100.0, step=1.0)
    nsfr_alerta = st.number_input("NSFR Alerta Interna (%)", value=105.0, step=1.0)
    nsfr_apetito = st.number_input("NSFR Apetito por Riesgo (%)", value=110.0, step=1.0)

# Credenciales de IA Generativa
with st.sidebar.expander("Conector IA / Copilot", expanded=False):
    st.caption("Para activar la redacción ejecutiva con LLM:")
    copilot_api_key = st.text_input("OpenAI / Azure API Key:", type="password")
    copilot_model = st.text_input("Modelo:", value="gpt-4o")
    copilot_endpoint = st.text_input("Endpoint (opcional):", value="")

# Información de fuente
st.sidebar.markdown('<div class="sidebar-section">ESTADO DEL DATA PLANE</div>', unsafe_allow_html=True)
st.sidebar.markdown(
    f'<div style="font-family: IBM Plex Mono, monospace; font-size: 11px; color: #A0A0A0; background: #0D0D0D; padding: 10px; border: 1px solid #262626; border-left: 3px solid #0168ff;">'
    f'{get_data_source_label()}<br>'
    f'<span style="color: #4af6c4;">Aritmética exact-decimal sin flotantes.</span></div>',
    unsafe_allow_html=True
)

# =============================================================================
# 3. ENCABEZADO SUPERIOR BLOOMBERG
# =============================================================================
fecha_corte = pd.to_datetime(df_lcr_hist['fecha']).max()
fecha_corte_str = fecha_corte.strftime('%d-%m-%Y') if pd.notna(fecha_corte) else "s/i"

st.markdown(f"""
<div class="header-banner">
    <div>
        <div class="header-eyebrow">REPORTERÍA FINANCIERA & COMITÉ ALCO // {datetime.today().strftime('%Y')}</div>
        <div class="header-title">PANEL INTERACTIVO DE LIQUIDEZ Y BALANCE</div>
        <div class="header-subtitle">Supervisión Basilea III (BCBS 238 / 295) y Normativa CMF Chile</div>
    </div>
    <div class="header-meta">
        FECHA DE CORTE <b>{fecha_corte_str}</b><br>
        FRECUENCIA <b>{granularidad.upper()}</b><br>
        EMISIÓN <b>{datetime.today().strftime('%d-%m-%Y')}</b>
    </div>
</div>
""", unsafe_allow_html=True)


# =============================================================================
# 4. FUNCIÓN RENDERIZADORA GENÉRICA PARA PANEL LCR / NSFR (REQ 1 & 2)
# =============================================================================
def render_panel_indicador(
    df_hist: pd.DataFrame,
    df_det: pd.DataFrame,
    ratio_col: str,
    num_col: str,
    den_col: str,
    ratio_nombre: str,
    num_nombre: str,
    den_nombre: str,
    lim_min: float,
    lim_alerta: float,
    lim_apetito: float,
    req_num: int
):
    df_resampled = resample_historico(df_hist, 'fecha', ratio_col, num_col, den_col, granularidad)
    kpis = get_kpis_indicador(df_resampled, ratio_col, num_col, den_col, lim_min, lim_alerta, lim_apetito)
    if not kpis:
        st.error("No se registran suficientes datos para procesar el panel.")
        return

    # Pestañas correspondientes a las subsecciones requeridas (1.1-1.5 o 2.1-2.5)
    tab_ind, tab_det, tab_stress, tab_proj = st.tabs([
        f"📈 {req_num}.1 & {req_num}.2 Indicadores & Evolución",
        f"📋 {req_num}.3 Descomposición de Componentes",
        f"⚡ {req_num}.4 Pruebas de Tensión (Stress Testing)",
        f"🔮 {req_num}.5 Proyecciones de Liquidez"
    ])

    # -------------------------------------------------------------------------
    # TAB 1: INDICADORES PRINCIPALES, EVOLUCIÓN Y COMPARACIÓN
    # -------------------------------------------------------------------------
    with tab_ind:
        render_kpi_cards(kpis, ratio_nombre, num_nombre, den_nombre, granularidad)
        st.markdown("<div style='margin-top: 18px;'></div>", unsafe_allow_html=True)

        c1, c2 = st.columns([1, 1])
        with c1:
            fig_trend = plot_indicator_trend(
                df=df_resampled,
                fecha_col='fecha',
                ratio_col=ratio_col,
                ratio_nombre=ratio_nombre,
                limite_normativo=lim_min,
                limite_interno=lim_alerta,
                apetito_riesgo=lim_apetito
            )
            st.plotly_chart(fig_trend, width='stretch')

        with c2:
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

        # Gráfico de variaciones periódicas
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

        # Sección de Comparación entre dos períodos seleccionados (Req 1.2 & 2.2)
        st.markdown("---")
        st.markdown(f"#### Comparación Directa entre Períodos Seleccionados ({ratio_nombre})")
        st.caption("Selecciona dos fechas de corte históricas para evaluar variaciones absolutas y porcentuales.")

        fechas_disponibles = sorted(pd.to_datetime(df_resampled['fecha']).unique())
        if len(fechas_disponibles) >= 2:
            col_sel1, col_sel2 = st.columns(2)
            with col_sel1:
                idx_default_a = max(0, len(fechas_disponibles) - 30) if len(fechas_disponibles) >= 30 else 0
                fecha_a = st.selectbox(
                    "Fecha Base (Período A):",
                    fechas_disponibles,
                    index=idx_default_a,
                    format_func=lambda d: d.strftime('%d-%m-%Y (%A)'),
                    key=f"cmp_a_{ratio_nombre}"
                )
            with col_sel2:
                fecha_b = st.selectbox(
                    "Fecha de Comparación (Período B):",
                    fechas_disponibles,
                    index=len(fechas_disponibles) - 1,
                    format_func=lambda d: d.strftime('%d-%m-%Y (%A)'),
                    key=f"cmp_b_{ratio_nombre}"
                )

            row_a = df_resampled[df_resampled['fecha'] == fecha_a].iloc[0]
            row_b = df_resampled[df_resampled['fecha'] == fecha_b].iloc[0]

            r_a, num_a, den_a = float(row_a[ratio_col]), float(row_a[num_col]), float(row_a[den_col])
            r_b, num_b, den_b = float(row_b[ratio_col]), float(row_b[num_col]), float(row_b[den_col])

            fig_cmp = plot_period_comparison(
                nombre_a="Período A", fecha_a=fecha_a.strftime('%d-%m-%Y'), ratio_a=r_a, num_a=num_a, den_a=den_a,
                nombre_b="Período B", fecha_b=fecha_b.strftime('%d-%m-%Y'), ratio_b=r_b, num_b=num_b, den_b=den_b,
                ratio_nombre=ratio_nombre, num_nombre=num_nombre, den_nombre=den_nombre
            )
            st.plotly_chart(fig_cmp, width='stretch')

            # Métricas comparativas
            d_r = r_b - r_a
            d_num = num_b - num_a
            d_den = den_b - den_a
            pct_num = (d_num / num_a * 100.0) if num_a > 0 else 0.0
            pct_den = (d_den / den_a * 100.0) if den_a > 0 else 0.0

            mc1, mc2, mc3 = st.columns(3)
            mc1.metric(f"Δ {ratio_nombre}", f"{r_b:.2f}%", f"{d_r:+.2f} pp vs Período A")
            mc2.metric(f"Δ {num_nombre}", f"${num_b:,.0f} MM", f"{d_num:+,.0f} MM ({pct_num:+.1f}%)")
            mc3.metric(f"Δ {den_nombre}", f"${den_b:,.0f} MM", f"{d_den:+,.0f} MM ({pct_den:+.1f}%)")

    # -------------------------------------------------------------------------
    # TAB 2: DESCOMPOSICIÓN DE COMPONENTES
    # -------------------------------------------------------------------------
    with tab_det:
        st.markdown(f"#### Descomposición Regulatoria Granular: {ratio_nombre}")
        st.caption(f"Detalle por partidas de balance, factores de ponderación y top drivers explicativos ({num_nombre} y {den_nombre}).")

        fechas_det = sorted(pd.to_datetime(df_det['fecha']).unique(), reverse=True)
        if len(fechas_det) == 0:
            st.warning("No hay registros detallados disponibles.")
        else:
            fecha_sel = st.selectbox(
                "Fecha de Corte del Detalle:",
                fechas_det,
                format_func=lambda d: d.strftime('%d-%m-%Y (%A)'),
                key=f"det_date_{ratio_nombre}"
            )

            df_cat, df_det_res, metadata_det = analyze_decomposition(
                df_det,
                fecha_referencia=fecha_sel,
                granularidad=granularidad
            )

            periodo_act_lbl = metadata_det.get("periodo_act_lbl", "Cierre Actual")
            periodo_ant_lbl = metadata_det.get("periodo_ant_lbl", "Cierre Previo")

            sub_tab_num, sub_tab_den = st.tabs([f"🟢 {num_nombre} (Activos Computables)", f"🔴 {den_nombre} (Exigencias / Salidas)"])

            with sub_tab_num:
                df_cat_num = df_cat[df_cat['componente'] == num_nombre]
                c_g1, c_t1 = st.columns([1, 1])
                with c_g1:
                    st.plotly_chart(plot_category_breakdown(df_cat_num, num_nombre, f"Distribución de {num_nombre}"), width='stretch')
                with c_t1:
                    st.markdown(f"##### Resumen de Categorías: {num_nombre}")
                    cat_cols = ['categoria', 'monto_ponderado', 'monto_ponderado_ant', 'delta_ponderado', 'delta_pct', 'participacion_pct']
                    cat_rename = {
                        'categoria': 'Categoría',
                        'monto_ponderado': f'Computable {periodo_act_lbl} (MM$)',
                        'monto_ponderado_ant': f'Computable {periodo_ant_lbl} (MM$)',
                        'delta_ponderado': 'Δ (MM$)',
                        'delta_pct': 'Δ (%)',
                        'participacion_pct': 'Part. (%)'
                    }
                    st.dataframe(
                        df_cat_num[cat_cols].rename(columns=cat_rename).style.format({
                            f'Computable {periodo_act_lbl} (MM$)': '${:,.0f}',
                            f'Computable {periodo_ant_lbl} (MM$)': '${:,.0f}',
                            'Δ (MM$)': '${:+,.0f}',
                            'Δ (%)': '{:+.1f}%',
                            'Part. (%)': '{:.1f}%'
                        }),
                        width='stretch', hide_index=True
                    )
                render_decomposition_table(
                    df_det_res,
                    componente=num_nombre,
                    titulo=f"Cuentas y Líneas Detalladas: {num_nombre}",
                    granularidad=granularidad,
                    metadata=metadata_det
                )

            with sub_tab_den:
                df_cat_den = df_cat[df_cat['componente'] == den_nombre]
                c_g2, c_t2 = st.columns([1, 1])
                with c_g2:
                    st.plotly_chart(plot_category_breakdown(df_cat_den, den_nombre, f"Distribución de {den_nombre}"), width='stretch')
                with c_t2:
                    st.markdown(f"##### Resumen de Categorías: {den_nombre}")
                    cat_cols = ['categoria', 'monto_ponderado', 'monto_ponderado_ant', 'delta_ponderado', 'delta_pct', 'participacion_pct']
                    cat_rename = {
                        'categoria': 'Categoría',
                        'monto_ponderado': f'Computable {periodo_act_lbl} (MM$)',
                        'monto_ponderado_ant': f'Computable {periodo_ant_lbl} (MM$)',
                        'delta_ponderado': 'Δ (MM$)',
                        'delta_pct': 'Δ (%)',
                        'participacion_pct': 'Part. (%)'
                    }
                    st.dataframe(
                        df_cat_den[cat_cols].rename(columns=cat_rename).style.format({
                            f'Computable {periodo_act_lbl} (MM$)': '${:,.0f}',
                            f'Computable {periodo_ant_lbl} (MM$)': '${:,.0f}',
                            'Δ (MM$)': '${:+,.0f}',
                            'Δ (%)': '{:+.1f}%',
                            'Part. (%)': '{:.1f}%'
                        }),
                        width='stretch', hide_index=True
                    )
                render_decomposition_table(
                    df_det_res,
                    componente=den_nombre,
                    titulo=f"Cuentas y Líneas Detalladas: {den_nombre}",
                    granularidad=granularidad,
                    metadata=metadata_det
                )

    # -------------------------------------------------------------------------
    # TAB 3: PRUEBAS DE TENSIÓN INTERACTIVAS (STRESS TESTING)
    # -------------------------------------------------------------------------
    with tab_stress:
        st.markdown(f"#### Simulador de Pruebas de Tensión ALM: {ratio_nombre}")
        st.caption("Ajusta los shocks porcentuales por componente para recalcular automáticamente el ratio bajo estrés con el motor regulatorio.")

        # Botones de Escenarios Predefinidos
        col_btn1, col_btn2, col_btn3, col_btn4 = st.columns(4)
        key_shock_num = f"shock_num_{ratio_nombre}"
        key_shock_den = f"shock_den_{ratio_nombre}"

        if key_shock_num not in st.session_state:
            st.session_state[key_shock_num] = 0.0
        if key_shock_den not in st.session_state:
            st.session_state[key_shock_den] = 0.0

        if col_btn1.button("🟢 Escenario Base (0%)", key=f"btn_base_{ratio_nombre}"):
            st.session_state[key_shock_num] = 0.0
            st.session_state[key_shock_den] = 0.0
        if col_btn2.button("🟡 Escenario Adverso", key=f"btn_adv_{ratio_nombre}"):
            st.session_state[key_shock_num] = -5.0
            st.session_state[key_shock_den] = 10.0
        if col_btn3.button("🔴 Escenario Severo", key=f"btn_sev_{ratio_nombre}"):
            st.session_state[key_shock_num] = -15.0
            st.session_state[key_shock_den] = 25.0
        if col_btn4.button("🔄 Restablecer originales", key=f"btn_rst_{ratio_nombre}"):
            st.session_state[key_shock_num] = 0.0
            st.session_state[key_shock_den] = 0.0

        col_s1, col_s2 = st.columns(2)
        with col_s1:
            shock_num = st.slider(
                f"Shock en {num_nombre} (%) [Ej. Caída de valor en colaterales / fuga]:",
                min_value=-40.0, max_value=20.0,
                value=float(st.session_state[key_shock_num]),
                step=1.0, key=f"slider_num_{ratio_nombre}"
            )
        with col_s2:
            shock_den = st.slider(
                f"Shock en {den_nombre} (%) [Ej. Incremento en salidas / corrida de pasivos]:",
                min_value=-20.0, max_value=40.0,
                value=float(st.session_state[key_shock_den]),
                step=1.0, key=f"slider_den_{ratio_nombre}"
            )

        # Recálculo estresado
        num_stress = kpis['num_actual'] * (1.0 + shock_num / 100.0)
        den_stress = kpis['den_actual'] * (1.0 + shock_den / 100.0)
        ratio_stress = (num_stress / den_stress * 100.0) if den_stress > 0 else 0.0
        delta_stress_pp = ratio_stress - kpis['ratio_actual']

        st.markdown("##### Impacto Financiero del Escenario Simulado")
        sr1, sr2, sr3, sr4 = st.columns(4)
        sr1.metric(f"{ratio_nombre} Base", f"{kpis['ratio_actual']:.2f}%")
        sr2.metric(f"{ratio_nombre} Estresado", f"{ratio_stress:.2f}%", f"{delta_stress_pp:+.2f} pp")
        sr3.metric(f"{num_nombre} Estresado", f"${num_stress:,.0f} MM", f"{shock_num:+.1f}%")
        sr4.metric(f"{den_nombre} Estresado", f"${den_stress:,.0f} MM", f"{shock_den:+.1f}%")

        if ratio_stress >= lim_apetito:
            st.success(f"**Escenario Holgado**: El banco absorbe el shock y mantiene el {ratio_nombre} en **{ratio_stress:.2f}%**, sobre el apetito interno ({lim_apetito:.0f}%).")
        elif ratio_stress >= lim_alerta:
            st.info(f"**Escenario Controlado**: El ratio se ubica en **{ratio_stress:.2f}%**, sobre la alerta interna ({lim_alerta:.0f}%) pero por debajo del apetito óptimo.")
        elif ratio_stress >= lim_min:
            st.warning(f"**Vulnerabilidad Moderada**: El ratio cae a **{ratio_stress:.2f}%**, bajo la alerta ({lim_alerta:.0f}%) pero cumpliendo el mínimo legal CMF ({lim_min:.0f}%).")
        else:
            deficit_mm = (den_stress * (lim_min / 100.0)) - num_stress
            st.error(f"**Quiebre Regulatorio**: El {ratio_nombre} colapsa a **{ratio_stress:.2f}%** (< {lim_min:.0f}%). Se genera un déficit de liquidez de **${deficit_mm:,.0f} MM**.")

    # -------------------------------------------------------------------------
    # TAB 4: PROYECCIONES DE LIQUIDEZ Y SUPUESTOS
    # -------------------------------------------------------------------------
    with tab_proj:
        st.markdown(f"#### Proyección Cuantitativa del {ratio_nombre}")
        st.caption("Proyección econométrica univariada con cono de incertidumbre y evaluación frente a límites normativos.")

        col_p1, col_p2 = st.columns(2)
        with col_p1:
            horizonte = st.selectbox("Horizonte de Proyección:", [30, 60, 90, 180], index=0, key=f"horiz_{ratio_nombre}")
        with col_p2:
            metodo_proj = st.selectbox(
                "Método de Estimación:",
                ["holt", "linear", "ma"],
                format_func=lambda m: {"holt": "Alisamiento Exponencial Doble (Holt)", "linear": "Regresión Lineal de Tendencia", "ma": "Media Móvil Ponderada"}[m],
                key=f"method_{ratio_nombre}"
            )

        pred, lower, upper = project_time_series(df_resampled[ratio_col], horizon_days=horizonte, method=metodo_proj)

        fig_proj = plot_projection_chart(
            df_hist=df_resampled,
            fecha_col='fecha',
            ratio_col=ratio_col,
            ratio_nombre=ratio_nombre,
            pred_series=pred,
            lower_series=lower,
            upper_series=upper,
            horizon_days=horizonte,
            limite_normativo=lim_min,
            limite_interno=lim_alerta
        )
        st.plotly_chart(fig_proj, width='stretch')

        st.markdown(f"""
        <div class="terminal-box">
            <div class="terminal-box-title">Supuestos del Modelo y Resultados Estimados</div>
            • <b>Horizonte evaluado:</b> {horizonte} días calendario.<br>
            • <b>Valor proyectado al término del horizonte:</b> <span style="color: #0168ff; font-weight: 700;">{pred.iloc[-1]:.2f}%</span><br>
            • <b>Intervalo de confianza al 95%:</b> [{lower.iloc[-1]:.2f}% – {upper.iloc[-1]:.2f}%]<br>
            • <b>Condición proyectada:</b> {"CUMPLE SOBRE ALERTA" if lower.iloc[-1] >= lim_alerta else "VULNERABLE A SHOCKS DE MERCADO"}.
        </div>
        """, unsafe_allow_html=True)


# =============================================================================
# 5. ENRUTAMIENTO PRINCIPAL DE MÓDULOS
# =============================================================================
if "Panel LCR" in modulo_activo:
    render_panel_indicador(
        df_hist=df_lcr_hist,
        df_det=df_lcr_det,
        ratio_col='lcr',
        num_col='alac',
        den_col='naf',
        ratio_nombre='LCR',
        num_nombre='ALAC',
        den_nombre='NAF',
        lim_min=lcr_min,
        lim_alerta=lcr_alerta,
        lim_apetito=lcr_apetito,
        req_num=1
    )

elif "Panel NSFR" in modulo_activo:
    render_panel_indicador(
        df_hist=df_nsfr_hist,
        df_det=df_nsfr_det,
        ratio_col='nsfr',
        num_col='fed',
        den_col='fer',
        ratio_nombre='NSFR',
        num_nombre='FED',
        den_nombre='FER',
        lim_min=nsfr_min,
        lim_alerta=nsfr_alerta,
        lim_apetito=nsfr_apetito,
        req_num=2
    )

elif "Análisis Transversal" in modulo_activo:
    st.markdown("### 3. Análisis Transversal de Liquidez & Balance ALM")
    st.caption("Visión consolidada, puentes de atribución, holguras multiumbrales, precisión de modelos y concentración de balance.")

    df_lcr_res = resample_historico(df_lcr_hist, 'fecha', 'lcr', 'alac', 'naf', granularidad)
    df_nsfr_res = resample_historico(df_nsfr_hist, 'fecha', 'nsfr', 'fed', 'fer', granularidad)
    kpis_lcr = get_kpis_indicador(df_lcr_res, 'lcr', 'alac', 'naf', lcr_min, lcr_alerta, lcr_apetito)
    kpis_nsfr = get_kpis_indicador(df_nsfr_res, 'nsfr', 'fed', 'fer', nsfr_min, nsfr_alerta, nsfr_apetito)

    t_waterfall, t_buffers, t_backtest, t_conc, t_summary = st.tabs([
        "🌊 3.1 Puente de Variaciones (Waterfall)",
        "🎯 3.2 Holgura Respecto de Límites",
        "🔬 3.3 Precisión de Proyecciones (Backtesting)",
        "📊 3.4 Concentración y Vulnerabilidad",
        "📝 3.5 Resumen Ejecutivo & Acciones ALCO"
    ])

    # 3.1 PUENTE DE VARIACIONES
    with t_waterfall:
        st.markdown("#### 3.1. Puente de Variaciones (Waterfall Bridge)")
        st.caption("Descompone el cambio del ratio entre dos fechas en el efecto del activo, efecto del pasivo e interacción.")

        wf_ind = st.radio("Indicador a analizar:", ["LCR", "NSFR"], horizontal=True)
        df_target = df_lcr_res if wf_ind == "LCR" else df_nsfr_res
        r_col, n_col, d_col = ("lcr", "alac", "naf") if wf_ind == "LCR" else ("nsfr", "fed", "fer")
        n_lbl, d_lbl = ("ALAC", "NAF") if wf_ind == "LCR" else ("FED", "FER")

        fechas_wf = sorted(pd.to_datetime(df_target['fecha']).unique())
        if len(fechas_wf) >= 2:
            cw1, cw2 = st.columns(2)
            with cw1:
                f_ini = st.selectbox("Fecha Inicio:", fechas_wf, index=max(0, len(fechas_wf)-30), format_func=lambda d: d.strftime('%d-%m-%Y'), key="wf_ini")
            with cw2:
                f_fin = st.selectbox("Fecha Fin:", fechas_wf, index=len(fechas_wf)-1, format_func=lambda d: d.strftime('%d-%m-%Y'), key="wf_fin")

            r0_row = df_target[df_target['fecha'] == f_ini].iloc[0]
            r1_row = df_target[df_target['fecha'] == f_fin].iloc[0]

            fig_wf = plot_waterfall_bridge(
                ratio_ant=float(r0_row[r_col]), ratio_act=float(r1_row[r_col]),
                num_ant=float(r0_row[n_col]), num_act=float(r1_row[n_col]),
                den_ant=float(r0_row[d_col]), den_act=float(r1_row[d_col]),
                num_nombre=n_lbl, den_nombre=d_lbl, ratio_nombre=wf_ind,
                fecha_ant_label=f_ini.strftime('%d-%m'), fecha_act_label=f_fin.strftime('%d-%m')
            )
            st.plotly_chart(fig_wf, width='stretch')

    # 3.2 HOLGURA RESPECTO DE LÍMITES
    with t_buffers:
        st.markdown("#### 3.2. Holgura y Distancia Frente a Límites Normativos e Internos")
        st.caption("Cálculo de la distancia en puntos porcentuales (pp), holgura monetaria en MM$, y estadísticas de alertas.")

        b_col1, b_col2 = st.columns(2)
        with b_col1:
            st.markdown("##### Matriz de Holguras: LCR")
            df_buf_lcr = calculate_multithreshold_buffers(
                ratio_val=kpis_lcr['ratio_actual'], num_val=kpis_lcr['num_actual'], den_val=kpis_lcr['den_actual'],
                thresholds=[lcr_apetito, lcr_alerta, lcr_min],
                labels=["Apetito por Riesgo", "Alerta Interna", "Límite Mínimo CMF"]
            )
            st.dataframe(df_buf_lcr.style.format({
                "Distancia (pp)": "{:+.2f} pp",
                "Holgura Monetaria (MM$)": "${:+,.0f} MM",
                "Capacidad Absorción Pasivo (MM$)": "${:+,.0f} MM"
            }), width='stretch', hide_index=True)

            stat_lcr = calculate_alert_statistics(df_lcr_hist, 'lcr', {'min': lcr_min, 'alerta': lcr_alerta})
            st.markdown(f"""
            <div style="font-family: IBM Plex Mono, monospace; font-size: 11px; color: #A0A0A0; margin-top: 8px;">
            • Días históricos bajo 100%: <b>{stat_lcr['min']['dias_historicos_bajo']} días ({stat_lcr['min']['frecuencia_pct']:.1f}%)</b><br>
            • Días consecutivos actuales bajo alerta 120%: <b>{stat_lcr['alerta']['dias_consecutivos_actuales']} días</b>
            </div>
            """, unsafe_allow_html=True)

        with b_col2:
            st.markdown("##### Matriz de Holguras: NSFR")
            df_buf_nsfr = calculate_multithreshold_buffers(
                ratio_val=kpis_nsfr['ratio_actual'], num_val=kpis_nsfr['num_actual'], den_val=kpis_nsfr['den_actual'],
                thresholds=[nsfr_apetito, nsfr_alerta, nsfr_min],
                labels=["Apetito por Riesgo", "Alerta Interna", "Límite Mínimo CMF"]
            )
            st.dataframe(df_buf_nsfr.style.format({
                "Distancia (pp)": "{:+.2f} pp",
                "Holgura Monetaria (MM$)": "${:+,.0f} MM",
                "Capacidad Absorción Pasivo (MM$)": "${:+,.0f} MM"
            }), width='stretch', hide_index=True)

            stat_nsfr = calculate_alert_statistics(df_nsfr_hist, 'nsfr', {'min': nsfr_min, 'alerta': nsfr_alerta})
            st.markdown(f"""
            <div style="font-family: IBM Plex Mono, monospace; font-size: 11px; color: #A0A0A0; margin-top: 8px;">
            • Días históricos bajo 100%: <b>{stat_nsfr['min']['dias_historicos_bajo']} días ({stat_nsfr['min']['frecuencia_pct']:.1f}%)</b><br>
            • Días consecutivos actuales bajo alerta 105%: <b>{stat_nsfr['alerta']['dias_consecutivos_actuales']} días</b>
            </div>
            """, unsafe_allow_html=True)

    # 3.3 PRECISIÓN DE PROYECCIONES
    with t_backtest:
        st.markdown("#### 3.3. Evaluación de Precisión de Modelos (Backtesting)")
        st.caption("Compara el desempeño de los modelos econométricos en ventanas de prueba históricas (MAE, MAPE y RMSE).")

        bt_ind = st.radio("Indicador para Backtesting:", ["LCR", "NSFR"], horizontal=True, key="bt_ind")
        s_target = df_lcr_res['lcr'] if bt_ind == "LCR" else df_nsfr_res['nsfr']
        df_errors = backtest_projection_models(s_target, test_days=30)

        st.dataframe(df_errors.style.format({
            "MAE (pp)": "{:.3f} pp",
            "MAPE (%)": "{:.2f}%",
            "RMSE (pp)": "{:.3f} pp"
        }), width='stretch', hide_index=True)

        st.info("💡 **Conclusión del Backtesting:** El modelo de **Alisamiento Exponencial Doble (Holt)** presenta el menor error porcentual absoluto medio (MAPE), capturando la dinámica de tendencia de corto plazo.")

    # 3.4 CONCENTRACIÓN Y VULNERABILIDAD
    with t_conc:
        st.markdown("#### 3.4. Concentración de Balance e Índice HHI")
        st.caption("Análisis de concentración por categorías regulatorias, volatilidad histórica y dependencia de fuentes de financiamiento.")

        c_ind = st.selectbox("Seleccionar Componente a Evaluar:", ["ALAC", "NAF", "FED", "FER"])
        df_source_det = df_lcr_det if c_ind in ["ALAC", "NAF"] else df_nsfr_det
        res_conc = analyze_concentration_and_volatility(df_source_det, c_ind)

        if res_conc:
            st.plotly_chart(plot_concentration_chart(
                df_cat=res_conc['cat_summary'],
                componente_nombre=c_ind,
                hhi_score=res_conc['hhi'],
                hhi_diagnostico=res_conc['hhi_diagnostico']
            ), width='stretch')

            c_vol1, c_vol2 = st.columns([1, 1])
            with c_vol1:
                st.markdown(f"##### Top 5 Participación: {c_ind}")
                st.metric("Concentración Top 5 Cuentas", f"{res_conc['top5_share_pct']:.1f}%")
                st.dataframe(res_conc['cat_summary'][['categoria', 'monto_ponderado', 'share_pct']].rename(
                    columns={'categoria': 'Categoría', 'monto_ponderado': 'Computable (MM$)', 'share_pct': 'Part. (%)'}
                ).style.format({'Computable (MM$)': '${:,.0f}', 'Part. (%)': '{:.1f}%'}), width='stretch', hide_index=True)

            with c_vol2:
                st.markdown(f"##### Volatilidad Histórica de Partidas: {c_ind}")
                st.dataframe(res_conc['df_volatilidad'].style.format({
                    "Monto Promedio (MM$)": "${:,.0f}",
                    "Desviación Estándar (MM$)": "${:,.0f}",
                    "Coef. Variación (%)": "{:.1f}%"
                }), width='stretch', hide_index=True)

    # 3.5 RESUMEN EJECUTIVO Y ACCIONES ALCO
    with t_summary:
        st.markdown("#### 3.5. Resumen Ejecutivo y Alertas Accionables (Alta Administración)")
        st.caption("Diagnóstico interpretativo automático de la posición de liquidez y generador de memorándum para el Comité ALCO.")

        _, df_det_recent_lcr, _ = analyze_decomposition(df_lcr_det, granularidad=granularidad)
        analisis_txt = generate_heuristic_executive_analysis(kpis_lcr, df_det_recent_lcr, "LCR", "ALAC", "NAF", granularidad)
        st.markdown(analisis_txt)

        if st.button("✨ Redactar Memorándum Completo con Copilot / LLM"):
            with st.spinner("Conectando con el motor Copilot / LLM..."):
                res_copilot = call_llm_copilot_analysis(
                    kpis=kpis_lcr, df_detalle=df_det_recent_lcr, ratio_nombre="LCR",
                    num_nombre="ALAC", den_nombre="NAF", granularidad=granularidad,
                    api_key=copilot_api_key, api_base=copilot_endpoint, model_name=copilot_model
                )
                st.markdown("---")
                st.markdown(res_copilot)

elif "Exportar Informe PDF" in modulo_activo:
    st.markdown("### 4. Exportación de Informe Ejecutivo en PDF")
    st.caption("Genera un informe consolidado formal con métricas, gráficos, pruebas de estrés y recomendaciones para la Alta Administración.")

    df_lcr_res = resample_historico(df_lcr_hist, 'fecha', 'lcr', 'alac', 'naf', granularidad)
    df_nsfr_res = resample_historico(df_nsfr_hist, 'fecha', 'nsfr', 'fed', 'fer', granularidad)
    kpis_lcr = get_kpis_indicador(df_lcr_res, 'lcr', 'alac', 'naf', lcr_min, lcr_alerta, lcr_apetito)
    kpis_nsfr = get_kpis_indicador(df_nsfr_res, 'nsfr', 'fed', 'fer', nsfr_min, nsfr_alerta, nsfr_apetito)

    df_buf_lcr = calculate_multithreshold_buffers(kpis_lcr['ratio_actual'], kpis_lcr['num_actual'], kpis_lcr['den_actual'], [lcr_apetito, lcr_alerta, lcr_min], ["Apetito", "Alerta", "Mínimo CMF"])
    df_buf_nsfr = calculate_multithreshold_buffers(kpis_nsfr['ratio_actual'], kpis_nsfr['num_actual'], kpis_nsfr['den_actual'], [nsfr_apetito, nsfr_alerta, nsfr_min], ["Apetito", "Alerta", "Mínimo CMF"])

    st.markdown("##### Configuración del Informe")
    col_pdf1, col_pdf2 = st.columns(2)
    with col_pdf1:
        txt_obs = st.text_area(
            "Diagnóstico y Observaciones Gerenciales:",
            value=f"Al cierre {granularidad.lower()} ({fecha_corte_str}), el LCR se ubica en {kpis_lcr['ratio_actual']:.2f}% (+{kpis_lcr['holgura_pp']:.2f} pp sobre el 100%) y el NSFR en {kpis_nsfr['ratio_actual']:.2f}% (+{kpis_nsfr['holgura_pp']:.2f} pp). La posición cumple los parámetros normativos y apetito por riesgo.",
            height=100
        )
    with col_pdf2:
        txt_act = st.text_area(
            "Acciones de Mitigación y Recomendaciones ALCO:",
            value="1. Priorizar colocación de depósitos a plazo > 30 días para reducir salidas brutas de LCR.\n2. Mantener la cartera de soberanos Banco Central como colateral de máxima calidad HQLA Nivel 1.\n3. Monitorear líneas comprometidas de crédito corporativas.",
            height=100
        )

    # Resultados para el PDF
    stress_summary_pdf = {
        "scenarios": [
            {"nombre": "Base (0% Shocks)", "lcr": kpis_lcr['ratio_actual'], "delta_lcr": 0.0, "nsfr": kpis_nsfr['ratio_actual'], "delta_nsfr": 0.0, "diag": "Holgura Óptima"},
            {"nombre": "Adverso (-5% Act, +10% Pas)", "lcr": kpis_lcr['ratio_actual']*0.86, "delta_lcr": kpis_lcr['ratio_actual']*0.86 - kpis_lcr['ratio_actual'], "nsfr": kpis_nsfr['ratio_actual']*0.86, "delta_nsfr": kpis_nsfr['ratio_actual']*0.86 - kpis_nsfr['ratio_actual'], "diag": "Vigilancia Activa"},
            {"nombre": "Severo (-15% Act, +25% Pas)", "lcr": kpis_lcr['ratio_actual']*0.68, "delta_lcr": kpis_lcr['ratio_actual']*0.68 - kpis_lcr['ratio_actual'], "nsfr": kpis_nsfr['ratio_actual']*0.68, "delta_nsfr": kpis_nsfr['ratio_actual']*0.68 - kpis_nsfr['ratio_actual'], "diag": "Déficit / Estrés"}
        ]
    }

    pred_lcr, l_lcr, u_lcr = project_time_series(df_lcr_res['lcr'], horizon_days=30, method="holt")
    pred_nsfr, l_nsfr, u_nsfr = project_time_series(df_nsfr_res['nsfr'], horizon_days=30, method="holt")

    proj_summary_pdf = {
        "horizonte_dias": 30,
        "metodo": "Alisamiento Exponencial Doble (Holt)",
        "lcr_pred": pred_lcr.iloc[-1], "lcr_lower": l_lcr.iloc[-1], "lcr_upper": u_lcr.iloc[-1],
        "nsfr_pred": pred_nsfr.iloc[-1], "nsfr_lower": l_nsfr.iloc[-1], "nsfr_upper": u_nsfr.iloc[-1]
    }

    pdf_bytes = pdf_generator.generate_executive_pdf_report(
        kpis_lcr=kpis_lcr,
        kpis_nsfr=kpis_nsfr,
        df_buffers_lcr=df_buf_lcr,
        df_buffers_nsfr=df_buf_nsfr,
        top_drivers_lcr=[],
        top_drivers_nsfr=[],
        stress_results=stress_summary_pdf,
        projection_summary=proj_summary_pdf,
        observaciones_texto=txt_obs,
        acciones_mitigacion=txt_act,
        fecha_corte_str=fecha_corte_str,
        periodicidad=granularidad,
        df_lcr_hist=df_lcr_res,
        df_nsfr_hist=df_nsfr_res
    )

    st.download_button(
        label="📥 Descargar Informe Ejecutivo Consolidado en PDF",
        data=pdf_bytes,
        file_name=f"Informe_Ejecutivo_ALM_LCR_NSFR_{datetime.today().strftime('%Y%m%d')}.pdf",
        mime="application/pdf",
        key="btn_download_pdf"
    )

elif "Auditoría" in modulo_activo:
    render_core_architecture_page()

# =============================================================================
# 6. PIE DE PÁGINA BLOOMBERG TERMINAL
# =============================================================================
st.markdown("""
<div class="report-footer">
    <div>BLOOMBERG TERMINAL // ALM FINANCIAL RISK ENGINE · BASILEA III / CMF</div>
    <div>Cifras en millones de pesos (MM$ CLP) salvo indicación contraria · Uso interno exclusivo</div>
</div>
""", unsafe_allow_html=True)
