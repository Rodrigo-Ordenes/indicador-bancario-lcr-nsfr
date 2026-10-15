"""
components/charts.py
====================
Generación de gráficos interactivos con Plotly para análisis de liquidez y balance.
Diseñados bajo la paleta oficial Bloomberg Terminal Dark:
- Base Negra: #000000
- Superficie Gráficos: #0D0D0D
- Rejilla: #222222
- Rojo Bloomberg (NAF / FER / Límite 100%): #fe443d
- Azul Bloomberg (Indicador Principal): #0168ff
- Menta / Turquesa Bloomberg (ALAC / FED / Superávit): #4af6c4
- Naranja Bloomberg (Apetito 150% / 110%): #fb8b1f
- Amarillo / Ámbar Bloomberg (Alerta 120% / 105%): #ffd15c
"""

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional

# Paleta Bloomberg Terminal Oficial
BBG_BLACK = "#000000"
BBG_PLOT_BG = "#0D0D0D"
BBG_GRID = "#222222"
BBG_TEXT = "#FFFFFF"
BBG_MUTED = "#A0A0A0"

COLOR_BLUE = "#0168ff"     # Azul Bloomberg: indicador principal
COLOR_RED = "#fe443d"      # Rojo Bloomberg: salidas / requerimientos / 100%
COLOR_MINT = "#4af6c4"     # Menta Bloomberg: activos / fondos disponibles
COLOR_ORANGE = "#fb8b1f"   # Naranja Bloomberg: apetito de riesgo
COLOR_GOLD = "#ffd15c"     # Amarillo Bloomberg: alerta interna


def _apply_bloomberg_layout(fig: go.Figure, title_text: str) -> go.Figure:
    """Aplica formato base de terminal Bloomberg a cualquier figura."""
    fig.update_layout(
        template="plotly_dark",
        title=dict(
            text=f"<b>{title_text.upper()}</b>",
            x=0.01, xanchor="left", y=0.97, yanchor="top",
            font=dict(size=14, color="#FFFFFF", family="IBM Plex Sans, sans-serif")
        ),
        paper_bgcolor=BBG_BLACK,
        plot_bgcolor=BBG_PLOT_BG,
        font=dict(color=BBG_TEXT, family="IBM Plex Sans, sans-serif"),
        margin=dict(l=50, r=45, t=65, b=45),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=11, color=BBG_TEXT, family="IBM Plex Mono, monospace"),
            bgcolor="rgba(0,0,0,0.5)"
        ),
        hoverlabel=dict(
            bgcolor="#1A1A1A",
            font_size=12,
            font_family="IBM Plex Mono, monospace",
            font_color="#FFFFFF"
        )
    )
    fig.update_xaxes(
        showgrid=True,
        gridcolor=BBG_GRID,
        gridwidth=1,
        zeroline=False,
        tickfont=dict(color=BBG_MUTED, family="IBM Plex Mono, monospace", size=10)
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor=BBG_GRID,
        gridwidth=1,
        zeroline=False,
        tickfont=dict(color=BBG_MUTED, family="IBM Plex Mono, monospace", size=10)
    )
    return fig


def plot_indicator_trend(
    df: pd.DataFrame, 
    fecha_col: str, 
    ratio_col: str, 
    ratio_nombre: str, 
    limite_normativo: float = 100.0, 
    limite_interno: float = 120.0,
    apetito_riesgo: float = 150.0,
    *args,
    **kwargs
) -> go.Figure:
    """
    Gráfico de tendencia principal del ratio con los tres umbrales definidos:
    - 100%: Mínimo normativo (Rojo #fe443d)
    - Alerta interna (120% LCR / 105% NSFR) (Amarillo #ffd15c)
    - Apetito de riesgo (150% LCR / 110% NSFR) (Naranja #fb8b1f)
    """
    fig = go.Figure()

    # Traza principal con gradiente sombreado
    fig.add_trace(go.Scatter(
        x=df[fecha_col],
        y=df[ratio_col],
        mode='lines+markers' if len(df) <= 45 else 'lines',
        name=f"{ratio_nombre} (%)",
        line=dict(color=COLOR_BLUE, width=2.5),
        marker=dict(size=5, color=COLOR_BLUE, line=dict(color="#FFFFFF", width=0.8)),
        fill='tozeroy',
        fillcolor='rgba(1, 104, 255, 0.12)',
        hovertemplate='<b>Fecha:</b> %{x|%d-%b-%Y}<br>' +
                      f'<b>{ratio_nombre}:</b> %{{y:.2f}}%<extra></extra>'
    ))

    # Umbral 1: Mínimo Normativo CMF (100%) - Rojo Bloomberg
    fig.add_hline(
        y=limite_normativo,
        line=dict(color=COLOR_RED, width=1.8, dash="dash"),
        annotation_text=f"Mínimo {limite_normativo:.0f}%",
        annotation_position="bottom right",
        annotation_font=dict(color=COLOR_RED, size=10, family="IBM Plex Mono")
    )

    # Umbral 2: Nivel de Alerta Interno - Amarillo Bloomberg
    fig.add_hline(
        y=limite_interno,
        line=dict(color=COLOR_GOLD, width=1.5, dash="dot"),
        annotation_text=f"Alerta {limite_interno:.0f}%",
        annotation_position="top left",
        annotation_font=dict(color=COLOR_GOLD, size=10, family="IBM Plex Mono")
    )

    # Umbral 3: Apetito de Riesgo - Naranja Bloomberg
    if apetito_riesgo and apetito_riesgo > limite_interno:
        fig.add_hline(
            y=apetito_riesgo,
            line=dict(color=COLOR_ORANGE, width=1.5, dash="dashdot"),
            annotation_text=f"Apetito {apetito_riesgo:.0f}%",
            annotation_position="top right",
            annotation_font=dict(color=COLOR_ORANGE, size=10, family="IBM Plex Mono")
        )

    fig = _apply_bloomberg_layout(fig, f"Evolución y Umbrales: {ratio_nombre}")
    fig.update_layout(
        yaxis=dict(title=f"{ratio_nombre} (%)", ticksuffix="%"),
        hovermode="x unified"
    )
    return fig


def plot_composition_evolution(
    df: pd.DataFrame,
    fecha_col: str,
    num_col: str,
    den_col: str,
    num_nombre: str,
    den_nombre: str,
    ratio_col: str,
    ratio_nombre: str,
    *args,
    **kwargs
) -> go.Figure:
    """
    Gráfico de doble eje estilo Bloomberg:
    - Numerador (Menta #4af6c4) vs Denominador (Rojo #fe443d) en MM$ (Eje Izquierdo)
    - Línea del Ratio (Azul #0168ff) en % (Eje Derecho)
    """
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    dense = len(df) > 90

    for col, nombre, color in ((num_col, num_nombre, COLOR_MINT), (den_col, den_nombre, COLOR_RED)):
        hover = '<b>' + nombre + ':</b> $%{y:,.0f} MM<extra></extra>'
        if dense:
            trace = go.Scatter(x=df[fecha_col], y=df[col], name=nombre, mode='lines',
                               line=dict(color=color, width=2.0), hovertemplate=hover)
        else:
            trace = go.Bar(x=df[fecha_col], y=df[col], name=nombre, marker_color=color, opacity=0.85,
                           marker_line_width=0, hovertemplate=hover)
        fig.add_trace(trace, secondary_y=False)

    fig.add_trace(
        go.Scatter(
            x=df[fecha_col],
            y=df[ratio_col],
            name=f"{ratio_nombre} (%)",
            line=dict(color=COLOR_BLUE, width=2.5),
            mode='lines',
            hovertemplate='<b>' + ratio_nombre + ':</b> %{y:.2f}%<extra></extra>'
        ),
        secondary_y=True
    )

    fig = _apply_bloomberg_layout(fig, f"Composición de Balance: {num_nombre} vs {den_nombre}")
    fig.update_layout(barmode='group', hovermode="x unified")
    fig.update_yaxes(title_text="Monto (MM$)", showgrid=True, gridcolor=BBG_GRID, secondary_y=False)
    fig.update_yaxes(title_text=f"{ratio_nombre} (%)", ticksuffix="%", showgrid=False, secondary_y=True)

    return fig


def plot_period_variations(
    df: pd.DataFrame,
    fecha_col: str,
    ratio_col: str,
    num_col: str,
    den_col: str,
    num_nombre: str,
    den_nombre: str,
    ratio_nombre: str,
    *args,
    **kwargs
) -> go.Figure:
    """
    Gráfico de variaciones periódicas:
    - Variación del ratio en pp (Menta positiva, Roja negativa)
    - Variación de componentes en MM$
    """
    df_var = df.dropna(subset=['delta_ratio']).copy()
    if len(df_var) == 0:
        return go.Figure()

    colores_delta = [COLOR_MINT if val >= 0 else COLOR_RED for val in df_var['delta_ratio']]

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.12,
        subplot_titles=[
            f"Δ {ratio_nombre} por período (pp)",
            f"Variación Neta: {num_nombre} y {den_nombre} (MM$)"
        ]
    )

    fig.add_trace(
        go.Bar(
            x=df_var[fecha_col],
            y=df_var['delta_ratio'],
            name=f"Δ {ratio_nombre} (pp)",
            marker_color=colores_delta,
            hovertemplate='<b>Fecha:</b> %{x|%d-%b-%Y}<br><b>Δ Ratio:</b> %{y:+.2f} pp<extra></extra>'
        ),
        row=1, col=1
    )

    fig.add_trace(
        go.Bar(
            x=df_var[fecha_col],
            y=df_var['delta_num'],
            name=f"Δ {num_nombre}",
            marker_color=COLOR_MINT,
            hovertemplate='<b>Δ ' + num_nombre + ':</b> %{y:+,.0f} MM$<extra></extra>'
        ),
        row=2, col=1
    )

    fig.add_trace(
        go.Bar(
            x=df_var[fecha_col],
            y=df_var['delta_den'],
            name=f"Δ {den_nombre}",
            marker_color=COLOR_RED,
            hovertemplate='<b>Δ ' + den_nombre + ':</b> %{y:+,.0f} MM$<extra></extra>'
        ),
        row=2, col=1
    )

    fig = _apply_bloomberg_layout(fig, f"Dinámica de Variaciones Periódicas: {ratio_nombre}")
    fig.update_traces(marker_line_width=0)
    fig.update_layout(barmode='group')
    return fig


def plot_waterfall_bridge(
    ratio_ant: float,
    ratio_act: float,
    num_ant: float,
    num_act: float,
    den_ant: float,
    den_act: float,
    num_nombre: str,
    den_nombre: str,
    ratio_nombre: str,
    fecha_ant_label: str = "Inicio",
    fecha_act_label: str = "Cierre",
    *args,
    **kwargs
) -> go.Figure:
    """
    Gráfico de cascada (Waterfall / Bridge) que explica la variación en pp
    descompuesta en: Efecto Numerador, Efecto Denominador e Interacción.
    """
    if den_ant <= 0 or den_act <= 0:
        return go.Figure()

    delta_num = num_act - num_ant
    delta_den = den_act - den_ant

    efecto_num = (delta_num / den_ant) * 100.0
    efecto_den = - (num_ant * delta_den / (den_ant * den_act)) * 100.0
    efecto_interaccion = (ratio_act - ratio_ant) - (efecto_num + efecto_den)

    x_labels = [
        f"{ratio_nombre} {fecha_ant_label}",
        f"Efecto {num_nombre}",
        f"Efecto {den_nombre}",
        "Ajuste / Interacción",
        f"{ratio_nombre} {fecha_act_label}"
    ]

    measures = ["absolute", "relative", "relative", "relative", "total"]
    y_values = [ratio_ant, efecto_num, efecto_den, efecto_interaccion, ratio_act]
    text_values = [
        f"{ratio_ant:.2f}%",
        f"{efecto_num:+.2f} pp",
        f"{efecto_den:+.2f} pp",
        f"{efecto_interaccion:+.2f} pp",
        f"{ratio_act:.2f}%"
    ]

    fig = go.Figure(go.Waterfall(
        name="Atribución",
        orientation="v",
        measure=measures,
        x=x_labels,
        textposition="outside",
        text=text_values,
        y=y_values,
        connector=dict(line=dict(color=BBG_MUTED, width=1, dash="dot")),
        decreasing=dict(marker=dict(color=COLOR_RED)),
        increasing=dict(marker=dict(color=COLOR_MINT)),
        totals=dict(marker=dict(color=COLOR_BLUE)),
        hovertemplate='<b>%{x}</b><br>Impacto: %{text}<extra></extra>'
    ))

    fig = _apply_bloomberg_layout(fig, f"Puente de Variaciones: {ratio_nombre}")
    fig.update_layout(yaxis=dict(title=f"{ratio_nombre} (%)", ticksuffix="%"))
    return fig


def plot_period_comparison(
    nombre_a: str, fecha_a: str, ratio_a: float, num_a: float, den_a: float,
    nombre_b: str, fecha_b: str, ratio_b: float, num_b: float, den_b: float,
    ratio_nombre: str, num_nombre: str, den_nombre: str,
    *args,
    **kwargs
) -> go.Figure:
    """
    Gráfico comparativo directo entre dos períodos seleccionados (Req 1.2, 2.2).
    """
    categories = [f"{ratio_nombre} (%)", f"{num_nombre} (MM$)", f"{den_nombre} (MM$)"]

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Montos en MM$
    fig.add_trace(go.Bar(
        name=f"{nombre_a} ({fecha_a})",
        x=[num_nombre, den_nombre],
        y=[num_a, den_a],
        marker_color="#2A5C99",
        text=[f"${num_a:,.0f} M", f"${den_a:,.0f} M"],
        textposition="auto"
    ), secondary_y=False)

    fig.add_trace(go.Bar(
        name=f"{nombre_b} ({fecha_b})",
        x=[num_nombre, den_nombre],
        y=[num_b, den_b],
        marker_color=COLOR_BLUE,
        text=[f"${num_b:,.0f} M", f"${den_b:,.0f} M"],
        textposition="auto"
    ), secondary_y=False)

    # Ratios en %
    fig.add_trace(go.Scatter(
        name=f"{ratio_nombre} {nombre_a}",
        x=[num_nombre],
        y=[ratio_a],
        mode="markers+text",
        marker=dict(size=14, color=COLOR_GOLD, symbol="diamond"),
        text=[f"{ratio_a:.2f}%"],
        textposition="top center"
    ), secondary_y=True)

    fig.add_trace(go.Scatter(
        name=f"{ratio_nombre} {nombre_b}",
        x=[den_nombre],
        y=[ratio_b],
        mode="markers+text",
        marker=dict(size=14, color=COLOR_MINT, symbol="diamond"),
        text=[f"{ratio_b:.2f}%"],
        textposition="top center"
    ), secondary_y=True)

    fig = _apply_bloomberg_layout(fig, f"Comparación Período A vs Período B: {ratio_nombre}")
    fig.update_layout(barmode="group")
    fig.update_yaxes(title_text="Montos (MM$)", secondary_y=False)
    fig.update_yaxes(title_text=f"{ratio_nombre} (%)", ticksuffix="%", secondary_y=True)
    return fig


def plot_projection_chart(
    df_hist: pd.DataFrame,
    fecha_col: str,
    ratio_col: str,
    ratio_nombre: str,
    pred_series: pd.Series,
    lower_series: pd.Series,
    upper_series: pd.Series,
    horizon_days: int = 30,
    limite_normativo: float = 100.0,
    limite_interno: float = 120.0,
    *args,
    **kwargs
) -> go.Figure:
    """
    Gráfico de proyección temporal con bandas de incertidumbre al 95% (Req 1.5, 2.5).
    """
    fig = go.Figure()
    df_recent = df_hist.tail(90).copy()
    last_date = pd.to_datetime(df_recent[fecha_col].iloc[-1])
    future_dates = [last_date + pd.Timedelta(days=i+1) for i in range(horizon_days)]

    # 1. Historia reciente
    fig.add_trace(go.Scatter(
        x=df_recent[fecha_col],
        y=df_recent[ratio_col],
        name="Histórico Observado",
        mode="lines",
        line=dict(color=BBG_MUTED, width=1.8),
        hovertemplate='<b>%{x|%d-%b-%Y}:</b> %{y:.2f}%<extra></extra>'
    ))

    # 2. Banda de incertidumbre (Cono de confianza al 95%)
    fig.add_trace(go.Scatter(
        x=future_dates + future_dates[::-1],
        y=list(upper_series) + list(lower_series)[::-1],
        fill="toself",
        fillcolor="rgba(1, 104, 255, 0.15)",
        line=dict(color="rgba(255,255,255,0)"),
        name="Intervalo Confianza (95%)",
        hoverinfo="skip"
    ))

    # 3. Proyección central
    fig.add_trace(go.Scatter(
        x=[df_recent[fecha_col].iloc[-1]] + future_dates,
        y=[df_recent[ratio_col].iloc[-1]] + list(pred_series),
        name="Proyección Estimada",
        mode="lines+markers",
        line=dict(color=COLOR_BLUE, width=2.5, dash="dash"),
        marker=dict(size=4, color=COLOR_BLUE),
        hovertemplate='<b>Proyectado %{x|%d-%b-%Y}:</b> %{y:.2f}%<extra></extra>'
    ))

    # Umbrales
    fig.add_hline(y=limite_normativo, line=dict(color=COLOR_RED, width=1.5, dash="dash"),
                  annotation_text=f"Mínimo {limite_normativo:.0f}%", annotation_position="bottom right")
    fig.add_hline(y=limite_interno, line=dict(color=COLOR_GOLD, width=1.5, dash="dot"),
                  annotation_text=f"Alerta {limite_interno:.0f}%", annotation_position="top left")

    fig = _apply_bloomberg_layout(fig, f"Proyección y Cono de Incertidumbre a {horizon_days} Días: {ratio_nombre}")
    fig.update_layout(yaxis=dict(title=f"{ratio_nombre} (%)", ticksuffix="%"))
    return fig


def plot_category_breakdown(
    df_cat: pd.DataFrame,
    componente_nombre: str,
    titulo: str = "Descomposición por Categoría",
    *args,
    **kwargs
) -> go.Figure:
    """
    Gráfico de barras horizontales para distribución de categorías.
    """
    df_sorted = df_cat.sort_values(by='monto_ponderado', ascending=True)

    fig = go.Figure(go.Bar(
        x=df_sorted['monto_ponderado'],
        y=df_sorted['categoria'],
        orientation='h',
        marker=dict(
            color=COLOR_MINT if componente_nombre in ['ALAC', 'FED'] else COLOR_RED,
            opacity=0.88
        ),
        text=[f"${v:,.0f} MM ({p:.1f}%)" for v, p in zip(df_sorted['monto_ponderado'], df_sorted['participacion_pct'])],
        textposition='auto',
        hovertemplate='<b>%{y}</b><br>Computable: $%{x:,.0f} MM<extra></extra>'
    ))

    fig = _apply_bloomberg_layout(fig, titulo)
    fig.update_layout(xaxis=dict(title="Monto Computable (MM$)"), margin=dict(l=220, r=40, t=65, b=45))
    return fig


def plot_concentration_chart(
    df_cat: pd.DataFrame,
    componente_nombre: str,
    hhi_score: float,
    hhi_diagnostico: str,
    *args,
    **kwargs
) -> go.Figure:
    """
    Gráfico de concentración y vulnerabilidad (Curva de Pareto y HHI) (Req 3.4).
    """
    df_sorted = df_cat.sort_values(by='monto_ponderado', ascending=False).copy()
    df_sorted['cum_share'] = df_sorted['share_pct'].cumsum()

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(go.Bar(
        x=df_sorted['categoria'],
        y=df_sorted['share_pct'],
        name="Participación (%)",
        marker_color=COLOR_BLUE,
        hovertemplate='<b>%{x}</b><br>Part.: %{y:.1f}%<extra></extra>'
    ), secondary_y=False)

    fig.add_trace(go.Scatter(
        x=df_sorted['categoria'],
        y=df_sorted['cum_share'],
        name="Acumulado (%)",
        mode="lines+markers",
        line=dict(color=COLOR_ORANGE, width=2.2),
        hovertemplate='<b>%{x}</b><br>Acumulado: %{y:.1f}%<extra></extra>'
    ), secondary_y=True)

    # Línea del 80% (Pareto)
    fig.add_hline(y=80.0, line=dict(color=BBG_MUTED, dash="dot", width=1),
                  annotation_text="80% Pareto", annotation_position="top left", secondary_y=True)

    fig = _apply_bloomberg_layout(fig, f"Concentración Pareto e Índice HHI: {componente_nombre} (HHI: {hhi_score:.0f} - {hhi_diagnostico})")
    fig.update_yaxes(title_text="Participación Individual (%)", ticksuffix="%", secondary_y=False)
    fig.update_yaxes(title_text="Participación Acumulada (%)", ticksuffix="%", range=[0, 105], secondary_y=True)
    fig.update_xaxes(tickangle=-25)
    return fig
