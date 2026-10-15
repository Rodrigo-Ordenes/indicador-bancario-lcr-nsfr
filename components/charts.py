"""
components/charts.py
====================
Gráficos Plotly con la paleta Bloomberg Terminal (negro, azul, rojo, menta, naranja).

Convención visual común a LCR y NSFR:
- Indicador (LCR / NSFR): azul. Numerador (ALAC / FED): menta. Denominador (NAF / FER): rojo.
- Umbrales: mínimo regulatorio en rojo (línea continua), alerta interna en naranja
  (discontinua), apetito por riesgo en menta (punteada).
- Datos reales: línea continua. Proyecciones: línea discontinua con banda de
  incertidumbre. Simulaciones: naranja.
"""

from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from styles import BBG_BLACK, BBG_BLUE, BBG_BORDER, BBG_MINT, BBG_MUTED, BBG_ORANGE, BBG_RED, BBG_TEXT

GRID = "#1A1A1A"
FONT = "IBM Plex Sans, sans-serif"
MONO = "IBM Plex Mono, monospace"
DASH = {"Regulatorio": "solid", "Interno: alerta": "dash", "Interno: apetito": "dot"}
SCENARIO_COLOR = {"Base": BBG_BLUE, "Adverso": BBG_ORANGE, "Severo": BBG_RED}


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}, {alpha})"


def _layout(fig: go.Figure, title: str, height: int = 420) -> go.Figure:
    fig.update_layout(
        template="plotly_dark", height=height, paper_bgcolor=BBG_BLACK, plot_bgcolor=BBG_BLACK,
        title=dict(text=title, x=0.0, xanchor="left", y=0.98, yanchor="top",
                   font=dict(size=14, color=BBG_TEXT, family=FONT)),
        font=dict(color=BBG_TEXT, family=FONT, size=12),
        margin=dict(l=55, r=30, t=72, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1, font=dict(size=11)),
        hoverlabel=dict(bgcolor=BBG_BLACK, bordercolor=BBG_BORDER, font=dict(family=MONO, size=12)),
    )
    fig.update_xaxes(showgrid=True, gridcolor=GRID, zeroline=False, linecolor=BBG_BORDER,
                     tickfont=dict(color=BBG_MUTED, family=MONO, size=11))
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=False, linecolor=BBG_BORDER,
                     tickfont=dict(color=BBG_MUTED, family=MONO, size=11))
    return fig


def _threshold_lines(fig, umbrales, row=None, col=None, x_label_pos="right"):
    for u in umbrales:
        kw = dict(y=u.valor, line=dict(color=u.color, width=1.4, dash=DASH[u.tipo]),
                  annotation_text=f"{u.nombre} {u.valor:.0f}%", annotation_position=f"top {x_label_pos}",
                  annotation_font=dict(color=u.color, size=10.5, family=MONO))
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
        line=dict(color=BBG_BLUE, width=2.2), marker=dict(size=6),
        customdata=closes["periodo"],
        hovertemplate=f"%{{customdata}}<br>{ind.codigo}: %{{y:.2f}}%<extra></extra>"), row=1, col=1)
    lo = min(closes[ind.ratio_col].min(), min(u.valor for u in umbrales)) - 5
    hi = max(closes[ind.ratio_col].max(), max(u.valor for u in umbrales)) + 5
    fig.update_yaxes(range=[lo, hi], ticksuffix="%", row=1, col=1)
    _threshold_lines(fig, umbrales, 1, 1)
    for col, name, color in ((ind.num_col, ind.num, BBG_MINT), (ind.den_col, ind.den, BBG_RED)):
        fig.add_trace(go.Scatter(
            x=closes["fecha"], y=closes[col], name=f"{name} (MM$)", mode=mode, line=dict(color=color, width=1.8),
            marker=dict(size=5), customdata=closes["periodo"],
            hovertemplate=f"%{{customdata}}<br>{name}: $%{{y:,.0f}} MM<extra></extra>"), row=2, col=1)
    fig.update_yaxes(title_text="MM$", row=2, col=1)
    fig.update_yaxes(title_text=f"{ind.codigo} (%)", row=1, col=1)
    return _layout(fig, f"Evolución del {ind.codigo}, {ind.num} y {ind.den} · cierres {granularidad.lower()}s", 560)


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
                         marker_color=[BBG_MINT if v >= 0 else BBG_RED for v in d[r_col]], marker_line_width=0,
                         customdata=d["periodo"],
                         hovertemplate=f"%{{customdata}}<br>Δ {ind.codigo}: %{{y:+.2f}} {unit_r}<extra></extra>"),
                  row=1, col=1)
    for col, name, color in ((f"d_num{'_pct' if pct else ''}", ind.num, BBG_MINT),
                             (f"d_den{'_pct' if pct else ''}", ind.den, BBG_RED)):
        fig.add_trace(go.Bar(x=d["fecha"], y=d[col], name=f"Δ {name}", marker_color=color, marker_line_width=0,
                             customdata=d["periodo"],
                             hovertemplate=f"%{{customdata}}<br>Δ {name}: %{{y:+,.2f}} {unit_c}<extra></extra>"),
                      row=2, col=1)
    fig.update_layout(barmode="group", bargap=0.05 if len(d) > 90 else 0.25)
    for a in fig.layout.annotations:
        a.font = dict(size=12, color=BBG_MUTED, family=MONO)
    return _layout(fig, f"Variaciones {'porcentuales' if pct else 'absolutas'} entre cierres", 480)


def comparison_chart(cmp: pd.DataFrame, ind, label_a: str, label_b: str) -> go.Figure:
    """Comparación de numerador y denominador entre dos cierres (barras) con el ratio anotado."""
    rows = cmp[cmp["Unidad"] == "MM$"]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=rows["Indicador"], y=rows["Período A"], name=label_a, marker_color=rgba(BBG_BLUE, 0.45),
                         marker_line_width=0, text=[f"${v:,.0f}" for v in rows["Período A"]], textposition="outside"))
    fig.add_trace(go.Bar(x=rows["Indicador"], y=rows["Período B"], name=label_b, marker_color=BBG_BLUE,
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
        marker_color=[BBG_MINT if v >= 0 else BBG_RED for v in d[value_col]],
        text=[f"{v:+.2f} {unit}" for v in d[value_col]], textposition="outside",
        textfont=dict(family=MONO, size=11), cliponaxis=False,
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
    colors = [BBG_BLUE if n == "Base" else (BBG_RED if n == "Severo" else BBG_ORANGE) for n in names]
    fig = go.Figure(go.Bar(
        y=names, x=vals, orientation="h", marker_color=colors, marker_line_width=0,
        text=[f"{v:.2f}%" for v in vals], textposition="outside", textfont=dict(family=MONO, size=12),
        hovertemplate="%{y}: %{x:.2f}%<extra></extra>"))
    top = max(vals + [u.valor for u in umbrales]) * 1.2
    for i, u in enumerate(sorted(umbrales, key=lambda u: u.valor)):
        fig.add_vline(x=u.valor, line=dict(color=u.color, width=1.4, dash=DASH[u.tipo]))
        fig.add_annotation(x=u.valor, y=1.0 + 0.09 * i, yref="paper", text=f"{u.valor:.0f}%", showarrow=False,
                           font=dict(color=u.color, size=10.5, family=MONO), yanchor="bottom")
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
        x=x, y=y, measure=measure, connector=dict(line=dict(color=BBG_BORDER, width=1)),
        increasing=dict(marker=dict(color=BBG_MINT)), decreasing=dict(marker=dict(color=BBG_RED)),
        totals=dict(marker=dict(color=BBG_BLUE)),
        text=[f"{ra:.2f}%"] + [f"{v:+.2f}" for v in wf["contribucion_pp"]] + [f"{rb:.2f}%"],
        textposition="outside", textfont=dict(family=MONO, size=11)))
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
                             line=dict(color=BBG_BLUE, width=2.2),
                             hovertemplate=f"%{{x|%d-%m-%Y}}<br>Real: %{{y:.2f}}%<extra></extra>"))
    for esc, df in projections.items():
        color = SCENARIO_COLOR.get(esc, BBG_ORANGE)
        fig.add_trace(go.Scatter(x=list(df["fecha"]) + list(df["fecha"][::-1]),
                                 y=list(df["upper"]) + list(df["lower"][::-1]), fill="toself",
                                 fillcolor=rgba(color, 0.10), line=dict(width=0), hoverinfo="skip",
                                 name=f"Banda 95% {esc}", showlegend=False))
        fig.add_trace(go.Scatter(x=df["fecha"], y=df["pred"], name=f"Proyección {esc}", mode="lines",
                                 line=dict(color=color, width=2, dash="dash"),
                                 hovertemplate=f"%{{x|%d-%m-%Y}}<br>{esc} (estimado): %{{y:.2f}}%<extra></extra>"))
    _threshold_lines(fig, umbrales, x_label_pos="left")
    allv = [h[ind.ratio_col].min(), h[ind.ratio_col].max()] + [df["lower"].min() for df in projections.values()] + \
           [df["upper"].max() for df in projections.values()] + [u.valor for u in umbrales]
    fig.update_yaxes(range=[min(allv) - 5, max(allv) + 5], ticksuffix="%")
    fig.add_vline(x=h["fecha"].max(), line=dict(color=BBG_MUTED, width=1, dash="dot"))
    return _layout(fig, title or f"Proyección del {ind.codigo} (estimación, no resultado cierto)", 460)


def backtest_chart(det: pd.DataFrame, metodo_label: str, ind_code: str) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=det["fecha_objetivo"], y=det["observado"], name="Observado (real)",
                             mode="lines+markers", line=dict(color=BBG_BLUE, width=2), marker=dict(size=7)))
    fig.add_trace(go.Scatter(
        x=det["fecha_objetivo"], y=det["proyectado"], name="Proyectado", mode="markers",
        marker=dict(color=BBG_ORANGE, size=9, symbol="diamond"),
        error_y=dict(type="data", symmetric=False, array=det["upper"] - det["proyectado"],
                     arrayminus=det["proyectado"] - det["lower"], color=rgba(BBG_ORANGE, 0.5), thickness=1.2)))
    fig.update_yaxes(ticksuffix="%")
    return _layout(fig, f"{ind_code}: proyectado vs observado · {metodo_label}", 400)


# -----------------------------------------------------------------------------
# Resumen ejecutivo y concentración
# -----------------------------------------------------------------------------

def gauge(ratio: float, ind_code: str, umbrales, estado: Dict) -> go.Figure:
    vals = sorted(u.valor for u in umbrales)
    top = max(ratio, vals[-1]) * 1.25
    low = min(60.0, ratio - 10.0)          # la escala parte en 60%: lo relevante está alrededor de los umbrales
    bands = [low] + vals + [top]
    colors = [BBG_RED, BBG_ORANGE, BBG_BLUE, BBG_MINT][-(len(bands) - 1):] if len(vals) == 3 else [BBG_RED, BBG_ORANGE, BBG_MINT]
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=ratio,
        number=dict(suffix="%", font=dict(family=MONO, size=34, color=estado["color"]), valueformat=".2f"),
        title=dict(text=f"{ind_code}<br><span style='font-size:12px;color:{estado['color']}'>{estado['codigo']}</span>",
                   font=dict(family=MONO, size=15, color=BBG_TEXT)),
        gauge=dict(axis=dict(range=[low, top], tickcolor=BBG_MUTED, tickfont=dict(family=MONO, size=10)),
                   bar=dict(color=BBG_TEXT, thickness=0.22), bgcolor=BBG_BLACK, borderwidth=1, bordercolor=BBG_BORDER,
                   steps=[dict(range=[bands[i], bands[i + 1]], color=rgba(colors[i], 0.35)) for i in range(len(bands) - 1)],
                   threshold=dict(line=dict(color=BBG_RED, width=3), thickness=0.9, value=vals[0]))))
    fig.update_layout(height=260, margin=dict(l=25, r=25, t=60, b=10), paper_bgcolor=BBG_BLACK,
                      font=dict(color=BBG_TEXT, family=FONT))
    return fig


def pareto_chart(tabla: pd.DataFrame, dimension: str, componente: str, hhi: float, nivel: str) -> go.Figure:
    t = tabla.head(15)
    color = BBG_MINT if componente in ("ALAC", "FED") else BBG_RED
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=t[dimension], y=t["base"], name="Monto computable (MM$)", marker_color=color,
                         marker_line_width=0, hovertemplate="%{x}<br>$%{y:,.0f} MM<extra></extra>"), secondary_y=False)
    fig.add_trace(go.Scatter(x=t[dimension], y=t["acumulado"], name="Participación acumulada (%)", mode="lines+markers",
                             line=dict(color=BBG_ORANGE, width=2), marker=dict(size=6)), secondary_y=True)
    fig.update_yaxes(title_text="MM$", secondary_y=False)
    fig.update_yaxes(ticksuffix="%", range=[0, 105], showgrid=False, secondary_y=True)
    fig.update_xaxes(tickangle=-30)
    return _layout(fig, f"Concentración de {componente} · HHI {hhi:,.0f} ({nivel})", 460)
