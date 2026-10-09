"""
components/ui_cards.py
======================
Renderizado de tarjetas ejecutivas de métricas (KPIs), semáforos regulatorios
y badges de variación para la alta dirección.
"""

import streamlit as st
from typing import Dict, Any

def render_kpi_cards(kpis: Dict[str, Any], ratio_nombre: str, num_nombre: str, den_nombre: str, granularidad: str):
    """
    Despliega 4 tarjetas ejecutivas en fila:
    1. Ratio Actual y Variación
    2. Componente Numerador (ALAC o FED)
    3. Componente Denominador (NAF o FER)
    4. Estado Regulatorio y Holgura
    """
    if not kpis:
        st.warning("No hay datos disponibles para el período seleccionado.")
        return

    col1, col2, col3, col4 = st.columns(4)

    # 1. Ratio Actual
    with col1:
        delta_ratio = kpis['delta_ratio']
        delta_class = "delta-positive" if delta_ratio >= 0 else "delta-negative"
        sign = "+" if delta_ratio >= 0 else ""
        icon = "▲" if delta_ratio >= 0 else "▼"

        html_kpi1 = f"""
        <div class="kpi-card">
            <div class="kpi-title">{ratio_nombre} ({granularidad})</div>
            <div class="kpi-value">{kpis['ratio_actual']:.2f}%</div>
            <div class="kpi-delta-box {delta_class}">
                <span>{icon} {sign}{delta_ratio:.2f} pp</span>
                <span style="font-weight: 400; opacity: 0.85;">({sign}{kpis['delta_ratio_pct']:.1f}%)</span>
            </div>
            <div class="kpi-subtext">vs período anterior ({kpis['ratio_anterior']:.2f}%)</div>
        </div>
        """
        st.markdown(html_kpi1, unsafe_allow_html=True)

    # 2. Numerador (ALAC / FED)
    with col2:
        delta_num = kpis['delta_num']
        delta_class = "delta-positive" if delta_num >= 0 else "delta-negative"
        sign = "+" if delta_num >= 0 else ""
        icon = "▲" if delta_num >= 0 else "▼"

        html_kpi2 = f"""
        <div class="kpi-card">
            <div class="kpi-title">{num_nombre}</div>
            <div class="kpi-value">${kpis['num_actual']:,.0f} <span style="font-size:16px; font-weight:500; color:#64748b;">MM</span></div>
            <div class="kpi-delta-box {delta_class}">
                <span>{icon} {sign}${delta_num:,.0f} MM</span>
                <span style="font-weight: 400; opacity: 0.85;">({sign}{kpis['delta_num_pct']:.1f}%)</span>
            </div>
            <div class="kpi-subtext">vs período anterior (${kpis['num_anterior']:,.0f} MM)</div>
        </div>
        """
        st.markdown(html_kpi2, unsafe_allow_html=True)

    # 3. Denominador (NAF / FER)
    with col3:
        # Para el denominador, un incremento generalmente presiona a la baja el ratio
        delta_den = kpis['delta_den']
        delta_class = "delta-negative" if delta_den > 0 else "delta-positive"
        sign = "+" if delta_den >= 0 else ""
        icon = "▲" if delta_den >= 0 else "▼"

        html_kpi3 = f"""
        <div class="kpi-card">
            <div class="kpi-title">{den_nombre}</div>
            <div class="kpi-value">${kpis['den_actual']:,.0f} <span style="font-size:16px; font-weight:500; color:#64748b;">MM</span></div>
            <div class="kpi-delta-box {delta_class}">
                <span>{icon} {sign}${delta_den:,.0f} MM</span>
                <span style="font-weight: 400; opacity: 0.85;">({sign}{kpis['delta_den_pct']:.1f}%)</span>
            </div>
            <div class="kpi-subtext">vs período anterior (${kpis['den_anterior']:,.0f} MM)</div>
        </div>
        """
        st.markdown(html_kpi3, unsafe_allow_html=True)

    # 4. Holgura Regulatoria & Semáforo
    with col4:
        holgura_pp = kpis['holgura_pp']
        semaforo_color = kpis['semaforo_color']
        semaforo = kpis['semaforo']

        html_kpi4 = f"""
        <div class="kpi-card" style="border-top: 3px solid {semaforo_color};">
            <div class="kpi-title">ESTADO NORMATIVO (CMF)</div>
            <div class="kpi-value" style="color: {semaforo_color}; font-size: 26px;">
                ● {semaforo}
            </div>
            <div class="kpi-delta-box" style="background-color: rgba(255, 255, 255, 0.06); color: #f1f5f9; border: 1px solid #1e2430;">
                <span>Holgura: <b>+{holgura_pp:.2f} pp</b></span>
            </div>
            <div class="kpi-subtext">Colchón: +${kpis['holgura_monto']:,.0f} MM sobre el 100%</div>
        </div>
        """
        st.markdown(html_kpi4, unsafe_allow_html=True)
