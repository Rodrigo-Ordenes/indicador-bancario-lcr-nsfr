"""
components/ui_cards.py
======================
Elementos de interfaz compartidos por los paneles LCR y NSFR: tarjetas de
indicadores, franja de situación, títulos de sección y etiquetas que distinguen
datos reales, proyectados y simulados.
"""

from typing import Dict, List

import streamlit as st

from styles import BBG_BLUE, BBG_MINT, BBG_RED

TAG = {"REAL": '<span class="tag tag-real">DATO REAL</span>',
       "PROY": '<span class="tag tag-proy">PROYECCIÓN · ESTIMACIÓN</span>',
       "SIM": '<span class="tag tag-sim">SIMULACIÓN</span>'}


def section(title: str, subtitle: str = "", tag: str = "") -> None:
    st.markdown(f'<div class="section-title"><span class="t">{title}{TAG.get(tag, "")}</span>'
                f'<span class="s">{subtitle}</span></div>', unsafe_allow_html=True)


def note(html: str, color: str = BBG_BLUE) -> None:
    st.markdown(f'<div class="note-box" style="--c:{color}">{html}</div>', unsafe_allow_html=True)


def _delta(value: float, pct: float, unit: str, favorable_up: bool = True) -> str:
    up = value >= 0
    cls = "up" if (up == favorable_up) else "down"
    arrow = "▲" if up else "▼"
    amount = f"{value:+.2f} pp" if unit == "pp" else f"{value:+,.0f} MM$"
    return f'<span class="kpi-delta {cls}">{arrow} {amount} ({pct:+.2f}%)</span>'


def status_strip(items: List[Dict]) -> None:
    """Franja permanente con la situación de LCR y NSFR al último cierre."""
    chips = "".join(
        f'<div class="status-chip" style="--c:{it["estado"]["color"]}">'
        f'<div class="col"><span class="name">{it["codigo"]} · {it["fecha"]:%d-%m-%Y}</span>'
        f'<span class="value">{it["ratio"]:.2f}%</span></div>'
        f'<div class="col right"><span class="state">{it["estado"]["codigo"]}</span>'
        f'<span class="name">{it["d_ratio"]:+.2f} pp vs ayer</span></div></div>'
        for it in items)
    st.markdown(f'<div class="status-strip">{chips}</div>', unsafe_allow_html=True)


def kpi_row(k: Dict, ind) -> None:
    """Cuatro tarjetas: indicador, numerador, denominador y situación frente a umbrales."""
    c1, c2, c3, c4 = st.columns(4)
    sub = f'{k["ref_ant"]} ({k["periodo_ant"]})'
    with c1:
        st.markdown(f"""<div class="kpi-card" style="--c:{BBG_BLUE}">
            <div class="kpi-label">{ind.codigo} · {k["periodo"]}</div>
            <div class="kpi-value">{k["ratio"]:.2f}<span class="kpi-unit">%</span></div>
            {_delta(k["d_ratio"], k["d_ratio_pct"], "pp")}
            <div class="kpi-sub">{sub}: {k["ratio_ant"]:.2f}%</div></div>""", unsafe_allow_html=True)
    with c2:
        st.markdown(f"""<div class="kpi-card" style="--c:{BBG_MINT}">
            <div class="kpi-label">{ind.num} · {k["periodo"]}</div>
            <div class="kpi-value">{k["num"]:,.0f}<span class="kpi-unit">MM$</span></div>
            {_delta(k["d_num"], k["d_num_pct"], "mm")}
            <div class="kpi-sub">{sub}: {k["num_ant"]:,.0f} MM$</div></div>""", unsafe_allow_html=True)
    with c3:
        # Un aumento del denominador reduce el indicador: se marca como desfavorable.
        st.markdown(f"""<div class="kpi-card" style="--c:{BBG_RED}">
            <div class="kpi-label">{ind.den} · {k["periodo"]}</div>
            <div class="kpi-value">{k["den"]:,.0f}<span class="kpi-unit">MM$</span></div>
            {_delta(k["d_den"], k["d_den_pct"], "mm", favorable_up=False)}
            <div class="kpi-sub">{sub}: {k["den_ant"]:,.0f} MM$</div></div>""", unsafe_allow_html=True)
    with c4:
        e = k["estado"]
        st.markdown(f"""<div class="kpi-card" style="--c:{e["color"]}">
            <div class="kpi-label">Situación frente a umbrales</div>
            <div class="state-badge" style="--c:{e["color"]}">{e["codigo"]}</div>
            <div class="kpi-delta" style="color:#FFFFFF">Holgura sobre mínimo: {k["holgura_pp"]:+.2f} pp</div>
            <div class="kpi-sub">{e["detalle"]}.<br>Equivale a {k["holgura_mm"]:,.0f} MM$ de {ind.num}.</div>
            </div>""", unsafe_allow_html=True)
