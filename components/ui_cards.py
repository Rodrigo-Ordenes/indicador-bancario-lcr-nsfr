"""
components/ui_cards.py
======================
Tarjetas KPI ejecutivas estilo Bloomberg Terminal para la alta dirección y comité ALCO:
- Ratio Actual, Numerador, Denominador y Semáforo Normativo.
- Visión diaria: Cierre de Hoy y variación respecto a ayer (sin movimiento intradía).
- Vistas agrupadas: Semanal, Mensual, Anual con deltas respecto al cierre del ciclo previo.
- Deltas en puntos porcentuales (pp) y montos (MM$) con formato visual de terminal.
- Umbrales regulatorios: 100% mínimo, alerta interna y apetito de riesgo.
"""

import streamlit as st
from typing import Dict, Any

def _delta_badge(value: float, pct: float, unit: str, favorable_when_up: bool = True) -> str:
    up = value >= 0
    # En salidas/pasivos, subir es desfavorable
    is_favorable = up if favorable_when_up else (not up)
    color = "#4af6c4" if is_favorable else "#fe443d"
    arrow = "▲" if up else "▼"
    sign = "+" if up else "−"
    amount_str = f"{abs(value):,.2f} pp" if unit == "pp" else f"${abs(value):,.0f} MM"
    return f'<span style="color: {color}; font-weight: 700; font-family: IBM Plex Mono, monospace;">{arrow} {sign}{amount_str} ({sign}{abs(pct):.1f}%)</span>'


def render_kpi_cards(kpis: Dict[str, Any], ratio_nombre: str, num_nombre: str, den_nombre: str, granularidad: str = "Diaria", *args, **kwargs):
    """Fila de cuatro tarjetas ejecutivas estilo Bloomberg Terminal."""
    if not kpis:
        st.warning("No hay datos disponibles para el período seleccionado.")
        return

    act_lbl = kpis.get("periodo_actual_label", f"Cierre {granularidad}")
    ant_lbl = kpis.get("periodo_anterior_label", "Previo")
    vs_lbl = kpis.get("vs_label", "vs ant.")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">{ratio_nombre} · {act_lbl.upper()}</div>
            <div class="kpi-value">{kpis['ratio_actual']:.2f}<span style="font-size: 16px; color: #0168ff;">%</span></div>
            <div class="kpi-delta-row">
                {_delta_badge(kpis['delta_ratio'], kpis['delta_ratio_pct'], 'pp')}
                <span style="font-family: IBM Plex Mono, monospace; font-size: 11px; color: #888888; margin-left: 4px;">{vs_lbl}</span>
            </div>
            <div style="font-size: 11px; color: #888888; margin-top: 6px; font-family: IBM Plex Mono, monospace;">
                {ant_lbl}: {kpis['ratio_anterior']:.2f}%
            </div>
        </div>
        """, unsafe_allow_html=True)

    with col2:
        st.markdown(f"""
        <div class="kpi-card kpi-card-mint">
            <div class="kpi-label">{num_nombre} · {act_lbl.upper()}</div>
            <div class="kpi-value" style="color: #4af6c4;">${kpis['num_actual']:,.0f}<span style="font-size: 14px; color: #A0A0A0;"> MM</span></div>
            <div class="kpi-delta-row">
                {_delta_badge(kpis['delta_num'], kpis['delta_num_pct'], 'mm')}
                <span style="font-family: IBM Plex Mono, monospace; font-size: 11px; color: #888888; margin-left: 4px;">{vs_lbl}</span>
            </div>
            <div style="font-size: 11px; color: #888888; margin-top: 6px; font-family: IBM Plex Mono, monospace;">
                {ant_lbl}: ${kpis['num_anterior']:,.0f} MM
            </div>
        </div>
        """, unsafe_allow_html=True)

    with col3:
        st.markdown(f"""
        <div class="kpi-card kpi-card-red">
            <div class="kpi-label">{den_nombre} · {act_lbl.upper()}</div>
            <div class="kpi-value" style="color: #fe443d;">${kpis['den_actual']:,.0f}<span style="font-size: 14px; color: #A0A0A0;"> MM</span></div>
            <div class="kpi-delta-row">
                {_delta_badge(kpis['delta_den'], kpis['delta_den_pct'], 'mm', favorable_when_up=False)}
                <span style="font-family: IBM Plex Mono, monospace; font-size: 11px; color: #888888; margin-left: 4px;">{vs_lbl}</span>
            </div>
            <div style="font-size: 11px; color: #888888; margin-top: 6px; font-family: IBM Plex Mono, monospace;">
                {ant_lbl}: ${kpis['den_anterior']:,.0f} MM
            </div>
        </div>
        """, unsafe_allow_html=True)

    with col4:
        color = kpis['semaforo_color']
        badge_cls = kpis.get('semaforo_badge', 'badge-status-optimal')
        holgura_pp = kpis['holgura_pp']
        sign = "+" if holgura_pp >= 0 else "−"
        st.markdown(f"""
        <div class="kpi-card" style="border-top: 3px solid {color};">
            <div class="kpi-label">ESTADO REGULATORIO CMF</div>
            <div style="margin: 4px 0;">
                <span class="badge-status {badge_cls}">{kpis['semaforo']}</span>
            </div>
            <div style="font-family: IBM Plex Mono, monospace; font-size: 13px; font-weight: 700; color: #FFFFFF; margin-top: 8px;">
                Holgura: <span style="color: {color};">{sign}{abs(holgura_pp):.2f} pp</span> (${kpis['holgura_monto']:,.0f} MM)
            </div>
            <div style="font-size: 11px; color: #888888; margin-top: 4px; font-family: IBM Plex Mono, monospace;">
                {kpis['semaforo_msg']}
            </div>
        </div>
        """, unsafe_allow_html=True)
