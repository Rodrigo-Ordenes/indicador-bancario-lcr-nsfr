"""
components/charts.py
====================
Gráficos Plotly sobre superficie blanca, con los colores de datos definidos en styles.py.

Convención visual común a LCR y NSFR:
- Indicador (LCR / NSFR): azul tinta. Numerador (ALAC / FED): verde azulado.
  Denominador (NAF / FER): ciruela. Variación favorable: verde; desfavorable: rojo profundo.
- Umbrales: mínimo regulatorio en rojo (línea continua), alerta interna en ámbar
  (discontinua), apetito por riesgo en verde (punteada).
- Datos reales: línea continua. Proyecciones: línea discontinua con banda de
  incertidumbre. Simulaciones: ámbar.
- El rojo de marca Coopeuch no se usa en los datos.
"""

from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from styles import (C_ALERT, C_DEN, C_DOWN, C_IND, C_LOW, C_NUM, C_UP, FONT_TEXT, FONT_UI, INK, INK_2, INK_3, LINE,
                    SCENARIO_COLOR, SURFACE)

GRID = "#EFEDE8"
FONT = FONT_TEXT
DASH = {"Regulatorio": "solid", "Interno: alerta": "dash", "Interno: apetito": "dot"}


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}, {alpha})"


def _layout(fig: go.Figure, title: str, height: int = 420) -> go.Figure:
    fig.update_layout(
        template="plotly_white", height=height, paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        title=dict(text=title, x=0.01, xanchor="left", y=0.97, yanchor="top",
                   font=dict(size=15, color=INK, family=FONT_UI)),
        font=dict(color=INK_2, family=FONT, size=12),
        margin=dict(l=60, r=30, t=78, b=42),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1, font=dict(size=11.5, color=INK_2),
                    bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(bgcolor=SURFACE, bordercolor=LINE, font=dict(family=FONT, size=12, color=INK)),
        bargap=0.3,
    )
    # Fechas en formato local (día-mes-año), independiente del idioma del navegador
    x = getattr(fig.data[0], "x", None) if fig.data else None
    if x is not None and len(x) and isinstance(x[0], (datetime, np.datetime64)):
        fig.update_xaxes(tickformat="%m-%Y", hoverformat="%d-%m-%Y")
    fig.update_xaxes(showgrid=False, zeroline=False, showline=True, linecolor=LINE, ticks="outside", tickcolor=LINE,
                     tickfont=dict(color=INK_3, family=FONT, size=11), title_font=dict(color=INK_3, size=11.5))
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=False, showline=False,
                     tickfont=dict(color=INK_3, family=FONT, size=11), title_font=dict(color=INK_3, size=11.5))
    return fig


SHORT = {"Regulatorio": "mínimo", "Interno: alerta": "alerta", "Interno: apetito": "apetito"}


def _threshold_lines(fig, umbrales, row=None, col=None):
    """Umbrales como líneas con su rótulo fuera del área de datos, a la derecha (no tapa la serie)."""
    for u in umbrales:
        kw = dict(y=u.valor, line=dict(color=u.color, width=1.3, dash=DASH[u.tipo]),
                  annotation_text=f"<b>{u.valor:.0f}%</b> {SHORT[u.tipo]}", annotation_position="right",
                  annotation_font=dict(color=u.color, size=11, family=FONT))
        if row:
            kw.update(row=row, col=col)
        fig.add_hline(**kw)


# -----------------------------------------------------------------------------
# Evolución y variaciones
# -----------------------------------------------------------------------------

def evolution_chart(closes: pd.DataFrame, ind, umbrales, granularidad: str) -> go.Figure:
    """Panel superior: indicador con umbrales. Panel inferior: numerador y denominador (MM$)."""
    mode = "lines" if len(closes) > 60 else "lines+markers"
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08, row_heights=[0.58, 0.42])
    fig.add_trace(go.Scatter(
        x=closes["fecha"], y=closes[ind.ratio_col], name=ind.codigo, mode=mode,
        line=dict(color=C_IND, width=2.4), marker=dict(size=6),
        customdata=closes["periodo"],
        hovertemplate=f"%{{customdata}}<br>{ind.codigo}: %{{y:.2f}}%<extra></extra>"), row=1, col=1)
    lo = min(closes[ind.ratio_col].min(), min(u.valor for u in umbrales)) - 5
    hi = max(closes[ind.ratio_col].max(), max(u.valor for u in umbrales)) + 5
    fig.update_yaxes(range=[lo, hi], ticksuffix="%", row=1, col=1)
    _threshold_lines(fig, umbrales, 1, 1)
    for col, name, color in ((ind.num_col, ind.num, C_NUM), (ind.den_col, ind.den, C_DEN)):
        fig.add_trace(go.Scatter(
            x=closes["fecha"], y=closes[col], name=f"{name} (MM$)", mode=mode, line=dict(color=color, width=1.8),
            marker=dict(size=5), customdata=closes["periodo"],
            hovertemplate=f"%{{customdata}}<br>{name}: $%{{y:,.0f}} MM<extra></extra>"), row=2, col=1)
    fig.update_yaxes(title_text="MM$", tickformat=",.0f", row=2, col=1)
    fig.update_yaxes(title_text=f"{ind.codigo} (%)", row=1, col=1)
    plural = {"Diaria": "diarios", "Semanal": "semanales", "Mensual": "mensuales", "Anual": "anuales"}[granularidad]
    fig = _layout(fig, f"Evolución del {ind.codigo}, {ind.num} y {ind.den} · cierres {plural}", 560)
    fig.update_layout(margin=dict(r=100))
    return fig


def variation_chart(closes: pd.DataFrame, ind, modo: str) -> go.Figure:
    """Variaciones período a período: absolutas (pp y MM$) o porcentuales (%)."""
    d = closes.dropna(subset=["d_ratio"])
    pct = modo == "Porcentual"
    r_col = "d_ratio_pct" if pct else "d_ratio"
    unit_r, unit_c = ("%", "%") if pct else ("pp", "MM$")
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.14,
                        subplot_titles=[f"Variación del {ind.codigo} ({unit_r})",
                                        f"Variación de {ind.num} y {ind.den} ({unit_c})"])
    fig.add_trace(go.Bar(x=d["fecha"], y=d[r_col], name=f"Δ {ind.codigo}",
                         marker_color=[C_UP if v >= 0 else C_DOWN for v in d[r_col]], marker_line_width=0,
                         customdata=d["periodo"],
                         hovertemplate=f"%{{customdata}}<br>Δ {ind.codigo}: %{{y:+.2f}} {unit_r}<extra></extra>"),
                  row=1, col=1)
    for col, name, color in ((f"d_num{'_pct' if pct else ''}", ind.num, C_NUM),
                             (f"d_den{'_pct' if pct else ''}", ind.den, C_DEN)):
        fig.add_trace(go.Bar(x=d["fecha"], y=d[col], name=f"Δ {name}", marker_color=color, marker_line_width=0,
                             customdata=d["periodo"],
                             hovertemplate=f"%{{customdata}}<br>Δ {name}: %{{y:+,.2f}} {unit_c}<extra></extra>"),
                      row=2, col=1)
    fig.update_layout(barmode="group", bargap=0.05 if len(d) > 90 else 0.25)
    for a in fig.layout.annotations:
        a.font = dict(size=12.5, color=INK_2, family=FONT_UI)
    return _layout(fig, f"Variaciones {'porcentuales' if pct else 'absolutas'} entre cierres", 480)


def comparison_chart(cmp: pd.DataFrame, ind, label_a: str, label_b: str) -> go.Figure:
    """Comparación de numerador y denominador entre dos cierres (barras) con el ratio anotado."""
    rows = cmp[cmp["Unidad"] == "MM$"]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=rows["Indicador"], y=rows["Período A"], name=label_a, marker_color=rgba(C_IND, 0.30),
                         marker_line_width=0, text=[f"${v:,.0f}" for v in rows["Período A"]], textposition="outside"))
    fig.add_trace(go.Bar(x=rows["Indicador"], y=rows["Período B"], name=label_b, marker_color=C_IND,
                         marker_line_width=0, text=[f"${v:,.0f}" for v in rows["Período B"]], textposition="outside"))
    r = cmp[cmp["Unidad"] == "%"].iloc[0]
    fig.update_layout(barmode="group")
    return _layout(fig, f"{ind.codigo}: {r['Período A']:.2f}% → {r['Período B']:.2f}% ({r['Variación absoluta']:+.2f} pp)", 360)


# -----------------------------------------------------------------------------
# Descomposición, tensión y puente
# -----------------------------------------------------------------------------

def impact_chart(df: pd.DataFrame, value_col: str, label_col: str, title: str, top: int = 10,
                 unit: str = "pp") -> go.Figure:
    d = df[df[value_col].abs() > 1e-9].copy()
    d = d.reindex(d[value_col].abs().sort_values(ascending=False).index).head(top).iloc[::-1]
    labels = [t if len(t) <= 44 else t[:42] + "…" for t in d[label_col].astype(str)]
    fig = go.Figure(go.Bar(
        x=d[value_col], y=labels, orientation="h", marker_line_width=0, customdata=d[label_col],
        marker_color=[C_UP if v >= 0 else C_DOWN for v in d[value_col]],
        text=[f"{v:+.2f} {unit}" for v in d[value_col]], textposition="outside",
        textfont=dict(family=FONT, size=11), cliponaxis=False,
        hovertemplate=f"%{{customdata}}<br>%{{x:+.3f}} {unit}<extra></extra>"))
    lo, hi = min(0.0, float(d[value_col].min())), max(0.0, float(d[value_col].max()))
    pad = (hi - lo) * 0.25 or 1.0                     # espacio para la etiqueta fuera de la barra
    fig.update_xaxes(ticksuffix=f" {unit}", range=[lo - (pad if lo < 0 else 0), hi + (pad if hi > 0 else 0)])
    fig.update_yaxes(automargin=True)
    return _layout(fig, title, max(300, 34 * len(d) + 120))


def scenario_bars(results: Dict[str, float], ind, umbrales, title: str) -> go.Figure:
    """Barras horizontales por escenario; los umbrales son líneas verticales rotuladas arriba."""
    names = list(results)[::-1]
    vals = [results[n] for n in names]
    colors = [SCENARIO_COLOR.get(n, C_ALERT) for n in names]
    fig = go.Figure(go.Bar(
        y=names, x=vals, orientation="h", marker_color=colors, marker_line_width=0,
        text=[f"{v:.2f}%" for v in vals], textposition="outside", textfont=dict(family=FONT, size=12),
        hovertemplate="%{y}: %{x:.2f}%<extra></extra>"))
    top = max(vals + [u.valor for u in umbrales]) * 1.2
    for i, u in enumerate(sorted(umbrales, key=lambda u: u.valor)):
        fig.add_vline(x=u.valor, line=dict(color=u.color, width=1.4, dash=DASH[u.tipo]))
        fig.add_annotation(x=u.valor, y=1.0 + 0.09 * i, yref="paper", text=f"{u.valor:.0f}%", showarrow=False,
                           font=dict(color=u.color, size=10.5, family=FONT), yanchor="bottom")
    fig.update_xaxes(ticksuffix="%", range=[0, top])
    fig = _layout(fig, title, 360)
    fig.update_layout(margin=dict(l=70, r=30, t=100, b=40), showlegend=False)
    return fig


def waterfall_chart(wf: pd.DataFrame, ind, label_a: str, label_b: str) -> go.Figure:
    ra, rb = wf.attrs["ratio_a"], wf.attrs["ratio_b"]
    x = [f"{ind.codigo} {label_a}"] + list(wf["categoria"]) + [f"{ind.codigo} {label_b}"]
    y = [ra] + list(wf["contribucion_pp"]) + [rb]
    measure = ["absolute"] + ["relative"] * len(wf) + ["total"]
    fig = go.Figure(go.Waterfall(
        x=x, y=y, measure=measure, connector=dict(line=dict(color=INK_3, width=1, dash="dot")),
        increasing=dict(marker=dict(color=C_UP)), decreasing=dict(marker=dict(color=C_DOWN)),
        totals=dict(marker=dict(color=C_IND)),
        text=[f"{ra:.2f}%"] + [f"{v:+.2f}" for v in wf["contribucion_pp"]] + [f"{rb:.2f}%"],
        textposition="outside", textfont=dict(family=FONT, size=11)))
    span = max(abs(rb - ra), wf["contribucion_pp"].abs().sum(), 1.0)
    low = min(ra, rb) - span * 1.2
    fig.update_yaxes(range=[low, max(ra, rb) + span * 1.2], ticksuffix="%")
    fig.update_xaxes(tickangle=-30)
    return _layout(fig, f"Puente del {ind.codigo}: {label_a} → {label_b} (contribución en pp)", 500)


# -----------------------------------------------------------------------------
# Proyecciones
# -----------------------------------------------------------------------------

def projection_chart(hist: pd.DataFrame, ind, projections: Dict[str, pd.DataFrame], umbrales,
                     history_days: int = 180, title: Optional[str] = None) -> go.Figure:
    h = hist.sort_values("fecha").tail(history_days)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=h["fecha"], y=h[ind.ratio_col], name=f"{ind.codigo} real", mode="lines",
                             line=dict(color=C_IND, width=2.4),
                             hovertemplate=f"%{{x|%d-%m-%Y}}<br>Real: %{{y:.2f}}%<extra></extra>"))
    for esc, df in projections.items():
        color = SCENARIO_COLOR.get(esc, C_ALERT)
        fig.add_trace(go.Scatter(x=list(df["fecha"]) + list(df["fecha"][::-1]),
                                 y=list(df["upper"]) + list(df["lower"][::-1]), fill="toself",
                                 fillcolor=rgba(color, 0.09), line=dict(width=0), hoverinfo="skip",
                                 name=f"Banda 95% {esc}", showlegend=False))
        fig.add_trace(go.Scatter(x=df["fecha"], y=df["pred"], name=f"Proyección {esc}", mode="lines",
                                 line=dict(color=color, width=2, dash="dash"),
                                 hovertemplate=f"%{{x|%d-%m-%Y}}<br>{esc} (estimado): %{{y:.2f}}%<extra></extra>"))
    _threshold_lines(fig, umbrales)
    allv = [h[ind.ratio_col].min(), h[ind.ratio_col].max()] + [df["lower"].min() for df in projections.values()] + \
           [df["upper"].max() for df in projections.values()] + [u.valor for u in umbrales]
    fig.update_yaxes(range=[min(allv) - 5, max(allv) + 5], ticksuffix="%")
    fig.add_vline(x=h["fecha"].max(), line=dict(color=INK_3, width=1, dash="dot"))
    fig = _layout(fig, title or f"Proyección del {ind.codigo} (estimación, no resultado cierto)", 460)
    fig.update_layout(margin=dict(r=100))
    return fig


def backtest_chart(det: pd.DataFrame, metodo_label: str, ind_code: str) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=det["fecha_objetivo"], y=det["observado"], name="Observado (real)",
                             mode="lines+markers", line=dict(color=INK, width=2), marker=dict(size=6)))
    fig.add_trace(go.Scatter(
        x=det["fecha_objetivo"], y=det["proyectado"], name="Proyectado", mode="markers",
        marker=dict(color=C_LOW, size=9, symbol="diamond"),
        error_y=dict(type="data", symmetric=False, array=det["upper"] - det["proyectado"],
                     arrayminus=det["proyectado"] - det["lower"], color=rgba(C_LOW, 0.45), thickness=1.2)))
    fig.update_yaxes(ticksuffix="%")
    return _layout(fig, f"{ind_code}: proyectado vs observado · {metodo_label}", 400)


# -----------------------------------------------------------------------------
# Concentración
# -----------------------------------------------------------------------------

def pareto_chart(tabla: pd.DataFrame, dimension: str, componente: str, hhi: float, nivel: str) -> go.Figure:
    t = tabla.head(15)
    color = C_NUM if componente in ("ALAC", "FED") else C_DEN
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=t[dimension], y=t["base"], name="Monto computable (MM$)", marker_color=color,
                         marker_line_width=0, hovertemplate="%{x}<br>$%{y:,.0f} MM<extra></extra>"), secondary_y=False)
    fig.add_trace(go.Scatter(x=t[dimension], y=t["acumulado"], name="Participación acumulada (%)", mode="lines+markers",
                             line=dict(color=INK_2, width=2), marker=dict(size=6)), secondary_y=True)
    fig.update_yaxes(title_text="MM$", secondary_y=False)
    fig.update_yaxes(ticksuffix="%", range=[0, 105], showgrid=False, secondary_y=True)
    fig.update_xaxes(tickangle=-30)
    return _layout(fig, f"Concentración de {componente} · HHI {hhi:,.0f} ({nivel})", 460)
