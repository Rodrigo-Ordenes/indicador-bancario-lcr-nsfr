"""
components/charts.py
====================
Generación de gráficos financieros interactivos con Plotly para análisis de liquidez y balance.
Diseñados con la paleta oficial Bloomberg Terminal Dark:
- Base Negra: #000000 / #080a0f
- Rojo Bloomberg (Salidas / Caídas / Alertas): #fe443d
- Azul Bloomberg (Indicador Principal / Primario): #0168ff
- Menta Bloomberg (Activos / Alzas / Óptimo): #4af6c4
- Naranja Bloomberg (Apetito / Advertencias / Acentos): #fb8b1f
"""

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np

# Paleta Bloomberg Terminal Oficial
BBG_BLACK = "#000000"
BBG_PLOT_BG = "#07090e"
BBG_GRID = "#18202d"
BBG_TEXT = "#e2e8f0"
BBG_MUTED = "#8b949e"

COLOR_BLUE = "#0168ff"     # Azul Bloomberg
COLOR_RED = "#fe443d"      # Rojo Bloomberg
COLOR_MINT = "#4af6c4"     # Menta Bloomberg
COLOR_ORANGE = "#fb8b1f"   # Naranja Bloomberg


def _apply_bloomberg_layout(fig: go.Figure, title_text: str) -> go.Figure:
    """Aplica formato base de terminal Bloomberg a cualquier figura."""
    fig.update_layout(
        template="plotly_dark",
        title=dict(
            text=f"<b>{title_text}</b>",
            font=dict(size=15, color="#ffffff", family="JetBrains Mono, Inter, monospace")
        ),
        paper_bgcolor=BBG_BLACK,
        plot_bgcolor=BBG_PLOT_BG,
        font=dict(color=BBG_TEXT, family="Inter, sans-serif"),
        margin=dict(l=45, r=45, t=50, b=40),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=12, color=BBG_TEXT)
        ),
        hoverlabel=dict(
            bgcolor="#0e131d",
            font_size=12,
            font_family="JetBrains Mono, monospace"
        )
    )
    fig.update_xaxes(
        showgrid=True,
        gridcolor=BBG_GRID,
        gridwidth=1,
        zeroline=False,
        tickfont=dict(color=BBG_MUTED, family="JetBrains Mono, monospace")
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor=BBG_GRID,
        gridwidth=1,
        zeroline=False,
        tickfont=dict(color=BBG_MUTED, family="JetBrains Mono, monospace")
    )
    return fig


def plot_indicator_trend(
    df: pd.DataFrame, 
    fecha_col: str, 
    ratio_col: str, 
    ratio_nombre: str, 
    limite_normativo: float = 100.0, 
    limite_interno: float = 105.0
) -> go.Figure:
    """
    Gráfico de tendencia principal del ratio con umbrales normativos estilo Bloomberg.
    """
    fig = go.Figure()

    # Área sombreada bajo la curva
    fig.add_trace(go.Scatter(
        x=df[fecha_col],
        y=df[ratio_col],
        mode='lines+markers',
        name=f"{ratio_nombre} (%)",
        line=dict(color=COLOR_BLUE, width=3),
        marker=dict(size=6, color=COLOR_BLUE, line=dict(color="#ffffff", width=1)),
        fill='tozeroy',
        fillcolor='rgba(1, 104, 255, 0.12)',
        hovertemplate='<b>Fecha:</b> %{x|%d-%b-%Y}<br>' +
                      f'<b>{ratio_nombre}:</b> %{{y:.2f}}%<extra></extra>'
    ))

    # Línea Mínimo Regulatorio (100%) - Rojo Bloomberg
    fig.add_hline(
        y=limite_normativo,
        line=dict(color=COLOR_RED, width=2, dash="dash"),
        annotation_text=f"Mínimo Regulatorio CMF ({limite_normativo:.0f}%)",
        annotation_position="top left",
        annotation_font=dict(color=COLOR_RED, size=11, family="JetBrains Mono")
    )

    # Línea Apetito de Riesgo Interno (105%) - Naranja Bloomberg
    fig.add_hline(
        y=limite_interno,
        line=dict(color=COLOR_ORANGE, width=1.5, dash="dot"),
        annotation_text=f"Apetito de Riesgo ({limite_interno:.0f}%)",
        annotation_position="bottom left",
        annotation_font=dict(color=COLOR_ORANGE, size=11, family="JetBrains Mono")
    )

    fig = _apply_bloomberg_layout(fig, f"EVOLUCIÓN HISTÓRICA: {ratio_nombre}")
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
    ratio_nombre: str
) -> go.Figure:
    """
    Gráfico de doble eje estilo Bloomberg:
    - Numerador (Menta) vs Denominador (Rojo) en MM$ (Eje Izquierdo)
    - Línea del Ratio (Azul o Naranja) en % (Eje Derecho)
    """
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Numerador (ALAC o FED) - Menta Bloomberg
    fig.add_trace(
        go.Bar(
            x=df[fecha_col],
            y=df[num_col],
            name=num_nombre,
            marker_color=COLOR_MINT,
            opacity=0.88,
            hovertemplate='<b>' + num_nombre + ':</b> $%{y:,.0f} MM<extra></extra>'
        ),
        secondary_y=False
    )

    # Denominador (NAF o FER) - Rojo Bloomberg
    fig.add_trace(
        go.Bar(
            x=df[fecha_col],
            y=df[den_col],
            name=den_nombre,
            marker_color=COLOR_RED,
            opacity=0.88,
            hovertemplate='<b>' + den_nombre + ':</b> $%{y:,.0f} MM<extra></extra>'
        ),
        secondary_y=False
    )

    # Ratio en eje secundario - Naranja Bloomberg
    fig.add_trace(
        go.Scatter(
            x=df[fecha_col],
            y=df[ratio_col],
            name=f"{ratio_nombre} (%)",
            line=dict(color=COLOR_ORANGE, width=3),
            marker=dict(size=6, color=COLOR_ORANGE),
            hovertemplate='<b>' + ratio_nombre + ':</b> %{y:.2f}%<extra></extra>'
        ),
        secondary_y=True
    )

    fig = _apply_bloomberg_layout(fig, f"COMPOSICIÓN DE {ratio_nombre}: {num_nombre} vs {den_nombre}")
    fig.update_layout(
        barmode='group',
        hovermode="x unified"
    )
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
    ratio_nombre: str
) -> go.Figure:
    """
    Gráfico de variaciones periódicas:
    - Variación del ratio en pp (Menta si positiva, Roja si negativa)
    - Variación de componentes en MM$ (Menta para numerador, Roja para denominador)
    """
    df_var = df.dropna(subset=['delta_ratio']).copy()
    if len(df_var) == 0:
        return go.Figure()

    colores_delta = [COLOR_MINT if val >= 0 else COLOR_RED for val in df_var['delta_ratio']]

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.14,
        subplot_titles=[
            f"Δ {ratio_nombre} Período a Período (puntos porcentuales)",
            f"Variación Neta: Δ {num_nombre} vs Δ {den_nombre} (MM$)"
        ]
    )

    # Panel 1: Variación del Ratio
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

    # Panel 2: Variación de Componentes
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

    fig = _apply_bloomberg_layout(fig, f"ANÁLISIS DE VARIACIONES DE {ratio_nombre}")
    fig.update_layout(barmode='group')
    fig.update_yaxes(title_text="Δ pp", ticksuffix=" pp", showgrid=True, gridcolor=BBG_GRID, row=1, col=1)
    fig.update_yaxes(title_text="Δ MM$", showgrid=True, gridcolor=BBG_GRID, row=2, col=1)

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
    ratio_nombre: str
) -> go.Figure:
    """
    Gráfico de cascada (Waterfall / Bridge) con paleta Bloomberg:
    Descompone matemáticamente la contribución de Δ Numerador y Δ Denominador.
    """
    delta_total_pp = ratio_act - ratio_ant
    
    if den_ant != 0:
        efecto_num = ((num_act - num_ant) / den_ant) * 100
        efecto_den = delta_total_pp - efecto_num
    else:
        efecto_num = delta_total_pp / 2
        efecto_den = delta_total_pp / 2

    x_labels = [
        f"{ratio_nombre} Ant.",
        f"Efecto Δ {num_nombre}",
        f"Efecto Δ {den_nombre}",
        f"{ratio_nombre} Act."
    ]

    y_values = [
        ratio_ant,
        efecto_num,
        efecto_den,
        ratio_act
    ]

    measure = ["absolute", "relative", "relative", "total"]

    fig = go.Figure(go.Waterfall(
        name="Atribución",
        orientation="v",
        measure=measure,
        x=x_labels,
        textposition="outside",
        text=[f"{v:.2f}%" if m in ["absolute", "total"] else f"{v:+.2f} pp" for v, m in zip(y_values, measure)],
        y=y_values,
        connector=dict(line=dict(color="#475569", width=1.2, dash="dot")),
        decreasing=dict(marker=dict(color=COLOR_RED)),
        increasing=dict(marker=dict(color=COLOR_MINT)),
        totals=dict(marker=dict(color=COLOR_BLUE))
    ))

    fig = _apply_bloomberg_layout(fig, f"ATRIBUCIÓN DE DRIVERS (WATERFALL): {ratio_nombre}")
    fig.update_layout(
        yaxis=dict(title=f"{ratio_nombre} (%)", ticksuffix="%", showgrid=True, gridcolor=BBG_GRID)
    )

    return fig


def plot_category_breakdown(
    df_cat: pd.DataFrame,
    componente_nombre: str,
    titulo: str = None
) -> go.Figure:
    """
    Gráfico de barras horizontales estilo Bloomberg para descomposición de categorías.
    """
    if len(df_cat) == 0:
        return go.Figure()

    df_plot = df_cat.sort_values(by="monto_ponderado", ascending=True)

    # Menta para activos (ALAC/FED), Rojo para salidas/fondeo requerido (NAF/FER)
    bar_color = COLOR_MINT if componente_nombre in ['ALAC', 'FED'] else COLOR_RED

    fig = go.Figure(go.Bar(
        x=df_plot["monto_ponderado"],
        y=df_plot["categoria"],
        orientation='h',
        marker=dict(color=bar_color, opacity=0.88),
        text=[f"${v:,.0f} MM ({p:.1f}%)" for v, p in zip(df_plot["monto_ponderado"], df_plot["participacion_pct"])],
        textposition="inside",
        hovertemplate='<b>%{y}</b><br>Monto Computable: $%{x:,.0f} MM<extra></extra>'
    ))

    fig = _apply_bloomberg_layout(fig, titulo or f"DESCOMPOSICIÓN POR CATEGORÍA: {componente_nombre}")
    fig.update_layout(
        xaxis=dict(title="Monto Computable (MM$)", showgrid=True, gridcolor=BBG_GRID),
        yaxis=dict(title="", showgrid=False)
    )

    return fig
