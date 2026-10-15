"""
components/ui_cards.py
======================
Elementos de interfaz compartidos: cabecera de marca, titular del día con reglas de
umbrales, libro de cifras de cada panel, títulos de sección, notas y etiquetas que
distinguen datos reales, proyectados y simulados.
"""

import math
from typing import Dict, List

import streamlit as st

from styles import C_DEN, C_IND, C_NUM, logo_html

TAG = {"REAL": '<span class="tag tag-real">Dato real</span>',
       "PROY": '<span class="tag tag-proy">Proyección · estimación</span>',
       "SIM": '<span class="tag tag-sim">Simulación</span>'}

# Tintes de fondo de cada tramo de la regla (de peor a mejor situación)
ZONE_TINT = {"breach": "#EBC3BF", "alert": "#EED9B0", "low": "#C9D7EA", "ok": "#C3DFD0"}


def _html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def section(title: str, subtitle: str = "", tag: str = "") -> None:
    sub = f'<div class="sec-s">{subtitle}</div>' if subtitle else ""
    _html(f'<div class="sec"><div class="sec-h"><span class="t">{title}</span>{TAG.get(tag, "")}</div>{sub}</div>')


def note(html: str, color: str = C_IND) -> None:
    _html(f'<div class="note" style="--c:{color}">{html}</div>')


def masthead(fecha_corte, vista: str, fuente: str, calidad: str, calidad_color: str) -> None:
    _html(f"""<div class="mast"><div class="mast-brand">{logo_html(28)}<span class="mast-rule"></span>
      <div><div class="mast-title">Liquidez · LCR y NSFR</div>
      <div class="mast-sub">Gerencia de Riesgo Financiero · Herramienta de uso interno</div></div></div>
      <div class="mast-meta"><span>Cierre <b>{fecha_corte:%d-%m-%Y}</b></span><span>Vista <b>{vista.lower()}</b></span>
      <span>{fuente}</span><span><i class="dot" style="background:{calidad_color}"></i>{calidad}</span></div></div>""")


# -----------------------------------------------------------------------------
# Titular del día y reglas de umbrales
# -----------------------------------------------------------------------------

def _thresholds(umbrales) -> Dict[str, float]:
    return {u.tipo: u.valor for u in umbrales}


def ruler(value: float, prev: float, umbrales, estado: Dict) -> str:
    """Regla horizontal: tramos por umbral, marca del cierre actual y círculo del cierre anterior."""
    t = _thresholds(umbrales)
    minimo, alerta, apetito = t["Regulatorio"], t["Interno: alerta"], t.get("Interno: apetito")
    top_u = apetito or alerta
    lo = math.floor(min(minimo - 20, value - 10, prev - 10) / 10) * 10
    hi = math.ceil(max(top_u * 1.15, value + 10, prev + 10) / 10) * 10
    pos = lambda v: (min(max(v, lo), hi) - lo) / (hi - lo) * 100
    edges = [lo, minimo, alerta] + ([apetito] if apetito else []) + [hi]
    names = ["breach", "alert"] + (["low"] if apetito else []) + ["ok"]
    track = "".join(f'<i style="width:{pos(b) - pos(a):.3f}%;background:{ZONE_TINT[n]}"></i>'
                    for a, b, n in zip(edges[:-1], edges[1:], names))
    ticks = "".join(f'<span class="ruler-tick" style="left:{pos(u.valor):.2f}%" title="{u.nombre}"></span>'
                    f'<span class="ruler-tick-label" style="left:{pos(u.valor):.2f}%">{u.valor:.0f}%</span>'
                    for u in umbrales)
    p = pos(value)
    anchor = "translateX(-100%)" if p > 78 else ("translateX(0)" if p < 22 else "translateX(-50%)")
    return (f'<div class="ruler"><div class="ruler-track">{track}</div>{ticks}'
            f'<span class="ruler-prev" style="left:{pos(prev):.2f}%" title="Cierre anterior {prev:.2f}%"></span>'
            f'<span class="ruler-mark" style="left:{p:.2f}%" title="Cierre actual {value:.2f}%"></span>'
            f'<span class="ruler-state" style="left:{p:.2f}%;transform:{anchor};color:{estado["color"]}">'
            f'{estado["codigo"]}</span></div>')


def _state_phrase(codigo: str, umbrales) -> str:
    t = _thresholds(umbrales)
    return {
        "INCUMPLIMIENTO": f"bajo el mínimo regulatorio de {t['Regulatorio']:.0f}%",
        "ALERTA": f"sobre el mínimo regulatorio, pero bajo la alerta interna de {t['Interno: alerta']:.0f}%",
        "BAJO APETITO": f"sobre la alerta interna, pero bajo el apetito por riesgo de {t.get('Interno: apetito', 0):.0f}%",
        "DENTRO DE APETITO": "dentro del apetito por riesgo",
        "CUMPLE": f"sobre la alerta interna de {t['Interno: alerta']:.0f}%",
    }[codigo]


def _move_phrase(d: float) -> str:
    if abs(d) < 0.005:
        return "sin cambios respecto del cierre anterior"
    return f"{'sube' if d > 0 else 'baja'} {abs(d):.2f} pp respecto del cierre anterior"


def _meters_html(items: List[Dict]) -> str:
    out = ""
    for it in items:
        d = it["d_ratio"]
        cls = "flat" if abs(d) < 0.005 else ("up" if d > 0 else "down")
        out += (f'<div class="meter"><div><div class="meter-code">{it["ind"].codigo}</div>'
                f'<div class="meter-name" title="{it["ind"].nombre}">{it["ind"].nombre}</div>'
                f'<div class="meter-value">{it["ratio"]:.2f}<small>%</small></div>'
                f'<div class="meter-delta {cls}">{d:+.2f} pp <span>vs {it["periodo_ant"]}</span></div></div>'
                f'{ruler(it["ratio"], it["ratio_ant"], it["umbrales"], it["estado"])}</div>')
    return f'<div class="meters">{out}</div>'


def brief(items: List[Dict]) -> None:
    """Titular del día: una frase que resume la situación y una regla de umbrales por indicador.

    items: dicts de kpi_snapshot (vista diaria) con las claves extra ind y umbrales.
    """
    fecha = items[0]["fecha"]
    frases = [f'el {it["ind"].codigo} se ubica en <b>{it["ratio"]:.2f}%</b>, {_move_phrase(it["d_ratio"])}, '
              f'{_state_phrase(it["estado"]["codigo"], it["umbrales"])}' for it in items]
    lede = f"Al cierre del {fecha:%d-%m-%Y}, " + "; ".join(frases) + "."
    _html(f'<div class="brief"><div class="brief-kicker">Situación al cierre</div>'
          f'<p class="brief-lede">{lede}</p>{_meters_html(items)}</div>')


def meters(items: List[Dict]) -> None:
    """Reglas de umbrales sin titular (holguras y resumen ejecutivo)."""
    _html(f'<div class="brief meters-only">{_meters_html(items)}</div>')


# -----------------------------------------------------------------------------
# Libro de cifras de cada panel
# -----------------------------------------------------------------------------

def _delta(value: float, pct: float, unit: str, favorable_up: bool = True) -> str:
    if abs(value) < 1e-9:
        return '<div class="l-delta flat">Sin variación</div>'
    cls = "up" if ((value > 0) == favorable_up) else "down"
    arrow = "▲" if value > 0 else "▼"
    amount = f"{value:+.2f} pp" if unit == "pp" else f"{value:+,.0f} MM$"
    return f'<div class="l-delta {cls}">{arrow} {amount} <span class="flat">({pct:+.2f}%)</span></div>'


def kpi_row(k: Dict, ind) -> None:
    """Indicador, numerador, denominador y situación frente a umbrales, en una sola pieza."""
    prev = f'{k["ref_ant"].replace("vs ", "").capitalize()} ({k["periodo_ant"]})'
    e = k["estado"]
    cell = lambda swatch, label, value, unit, delta, prev_val: (
        f'<div><div class="l-label"><span class="l-swatch" style="background:{swatch}"></span>{label}</div>'
        f'<div class="l-value">{value}<small>{unit}</small></div>{delta}'
        f'<div class="l-prev">{prev}: {prev_val}</div></div>')
    _html('<div class="ledger">'
          + cell(C_IND, f'{ind.codigo} · {k["periodo"]}', f'{k["ratio"]:.2f}', "%",
                 _delta(k["d_ratio"], k["d_ratio_pct"], "pp"), f'{k["ratio_ant"]:.2f}%')
          + cell(C_NUM, f'{ind.num} · numerador', f'{k["num"]:,.0f}', "MM$",
                 _delta(k["d_num"], k["d_num_pct"], "mm"), f'{k["num_ant"]:,.0f} MM$')
          # Un aumento del denominador reduce el indicador: se marca como desfavorable.
          + cell(C_DEN, f'{ind.den} · denominador', f'{k["den"]:,.0f}', "MM$",
                 _delta(k["d_den"], k["d_den_pct"], "mm", favorable_up=False), f'{k["den_ant"]:,.0f} MM$')
          + f'<div><div class="l-label">Situación frente a umbrales</div>'
            f'<div class="l-state" style="--c:{e["color"]}">{e["codigo"]}</div>'
            f'<div class="l-delta flat">Holgura sobre el mínimo: <b style="color:var(--ink)">{k["holgura_pp"]:+.2f} pp</b></div>'
            f'<div class="l-prev">{e["detalle"]}. Equivale a {k["holgura_mm"]:,.0f} MM$ de {ind.num}.</div></div>'
          + '</div>')
