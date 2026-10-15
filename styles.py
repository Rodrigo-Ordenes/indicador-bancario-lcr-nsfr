"""
styles.py
=========
Hoja de estilos oficial Bloomberg Terminal Dark para el panel ejecutivo de liquidez y balance.

Paleta oficial Bloomberg Terminal:
- Base Negra: #000000 (fondo de terminal)
- Superficies: #0D0D0D / #141414 (paneles y tarjetas oscuras)
- Bordes: #262626 (filetes de contraste)
- Rojo Bloomberg (#fe443d): Salidas Netas (NAF), Fondeo Requerido (FER), caídas, alertas normativas (100%)
- Azul Bloomberg (#0168ff): Indicadores principales (LCR / NSFR), botones primarios, cabeceras activas
- Menta / Turquesa Bloomberg (#4af6c4): Activos Líquidos (ALAC), Fondeo Disponible (FED), estado óptimo, alzas
- Naranja Bloomberg (#fb8b1f): Apetito de riesgo (150% LCR / 110% NSFR), alertas preventivas
- Amarillo / Ámbar Bloomberg (#ffd15c): Nivel de alerta interno (120% LCR / 105% NSFR)
- Texto: #FFFFFF (primario), #A0A0A0 (secundario), #666666 (tenue)
"""

# Paleta oficial Bloomberg Terminal
BBG_BLACK = "#000000"          # Fondo negro terminal
BBG_DARK_CARD = "#0D0D0D"      # Superficies de tarjetas
BBG_DARK_CARD_ALT = "#141414"  # Superficies secundarias
BBG_BORDER = "#262626"         # Bordes y separadores
BBG_TEXT = "#FFFFFF"           # Texto principal alto contraste
BBG_MUTED = "#A0A0A0"          # Texto secundario
BBG_FAINT = "#666666"          # Texto tenue

# Colores funcionales Bloomberg
BBG_BLUE = "#0168ff"           # Azul Bloomberg: Indicador principal
BBG_RED = "#fe443d"            # Rojo Bloomberg: NAF / FER / Límite 100% / Déficit
BBG_MINT = "#4af6c4"           # Menta Bloomberg: ALAC / FED / Superávit
BBG_ORANGE = "#fb8b1f"         # Naranja Bloomberg: Apetito de riesgo (150%)
BBG_GOLD = "#ffd15c"           # Amarillo Bloomberg: Alerta interna (120% / 105%)

# Alias para compatibilidad con módulos
INK_BG = BBG_BLACK
INK_SURFACE = BBG_DARK_CARD
INK_SURFACE_ALT = BBG_DARK_CARD_ALT
INK_BORDER = BBG_BORDER
INK_TEXT = BBG_TEXT
INK_MUTED = BBG_MUTED
INK_FAINT = BBG_FAINT

COLOR_PRIMARY = BBG_BLUE
COLOR_POSITIVE = BBG_MINT
COLOR_NEGATIVE = BBG_RED
COLOR_WARNING = BBG_GOLD
COLOR_ACCENT = BBG_ORANGE

CUSTOM_CSS = f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=IBM+Plex+Sans:wght@300;400;500;600;700&family=Source+Serif+4:opsz,wght@8..60,600;8..60,700&display=swap');

    html, body, [class*="css"], .stApp {{
        background-color: {BBG_BLACK} !important;
        color: {BBG_TEXT} !important;
        font-family: 'IBM Plex Sans', -apple-system, sans-serif;
        font-feature-settings: "tnum" 1, "lnum" 1;
    }}

    /* Títulos y jerarquía Bloomberg */
    h1, h2, h3, h4 {{
        font-family: 'IBM Plex Sans', sans-serif !important;
        font-weight: 700 !important;
        color: {BBG_TEXT} !important;
        letter-spacing: -0.01em !important;
    }}
    h1 {{ font-size: 1.8rem !important; color: {BBG_TEXT} !important; }}
    h2 {{ font-size: 1.45rem !important; color: {BBG_BLUE} !important; }}
    h3 {{ font-size: 1.25rem !important; color: {BBG_TEXT} !important; margin-top: 0.5rem !important; }}
    h4 {{ font-size: 1.05rem !important; color: #E0E0E0 !important; }}
    h5 {{ 
        font-family: 'IBM Plex Mono', monospace !important; 
        font-size: 0.8rem !important;
        text-transform: uppercase; 
        letter-spacing: 0.08em; 
        color: {BBG_MUTED} !important; 
    }}

    /* Barra lateral Bloomberg Terminal */
    section[data-testid="stSidebar"] {{
        background-color: #080808 !important;
        border-right: 1px solid {BBG_BORDER} !important;
    }}
    section[data-testid="stSidebar"] * {{ 
        color: #E0E0E0 !important; 
    }}
    .sidebar-brand {{
        border-bottom: 2px solid {BBG_BLUE};
        padding: 8px 0 14px 0;
        margin-bottom: 12px;
    }}
    .sidebar-brand-title {{
        font-family: 'IBM Plex Sans', sans-serif;
        font-size: 18px;
        font-weight: 700;
        color: #FFFFFF;
        letter-spacing: 0.02em;
    }}
    .sidebar-brand-sub {{
        font-family: 'IBM Plex Mono', monospace;
        font-size: 11px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: {BBG_BLUE} !important;
        margin-top: 3px;
        font-weight: 600;
    }}
    .sidebar-section {{
        font-family: 'IBM Plex Mono', monospace;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: {BBG_BLUE} !important;
        margin: 16px 0 6px 0;
        border-bottom: 1px solid {BBG_BORDER};
        padding-bottom: 3px;
    }}

    /* Banner superior Bloomberg */
    .header-banner {{
        background: linear-gradient(180deg, #121212 0%, {BBG_BLACK} 100%);
        border: 1px solid {BBG_BORDER};
        border-top: 3px solid {BBG_BLUE};
        border-radius: 4px;
        padding: 16px 20px;
        margin-bottom: 18px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }}
    .header-eyebrow {{
        font-family: 'IBM Plex Mono', monospace;
        font-size: 11px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: {BBG_BLUE};
        margin-bottom: 2px;
    }}
    .header-title {{
        margin: 0;
        padding: 0;
        font-size: 24px !important;
        font-weight: 700;
        color: #FFFFFF !important;
    }}
    .header-subtitle {{
        font-size: 13px;
        color: {BBG_MUTED};
        margin-top: 3px;
    }}
    .header-meta {{
        font-family: 'IBM Plex Mono', monospace;
        font-size: 12px;
        color: {BBG_MUTED};
        text-align: right;
        line-height: 1.5;
        border-left: 1px solid {BBG_BORDER};
        padding-left: 16px;
    }}
    .header-meta b {{ color: #FFFFFF; font-weight: 600; }}

    /* Tarjetas KPI estilo Bloomberg Terminal */
    .kpi-card {{
        background-color: {BBG_DARK_CARD};
        border: 1px solid {BBG_BORDER};
        border-top: 3px solid {BBG_BLUE};
        border-radius: 4px;
        padding: 14px 16px;
        height: 100%;
        box-shadow: 0 2px 4px rgba(0,0,0,0.5);
    }}
    .kpi-card-mint {{ border-top-color: {BBG_MINT} !important; }}
    .kpi-card-red  {{ border-top-color: {BBG_RED} !important; }}
    .kpi-card-gold {{ border-top-color: {BBG_GOLD} !important; }}
    .kpi-card-orange {{ border-top-color: {BBG_ORANGE} !important; }}

    .kpi-label {{
        font-family: 'IBM Plex Mono', monospace;
        font-size: 11px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: {BBG_MUTED};
        margin-bottom: 6px;
    }}
    .kpi-value {{
        font-family: 'IBM Plex Mono', monospace;
        font-size: 28px;
        font-weight: 700;
        color: #FFFFFF;
        line-height: 1.1;
        font-feature-settings: "tnum" 1;
    }}
    .kpi-delta-row {{
        display: flex;
        align-items: center;
        gap: 8px;
        margin-top: 8px;
        font-family: 'IBM Plex Mono', monospace;
        font-size: 12px;
        font-weight: 600;
    }}
    .kpi-delta-pos {{ color: {BBG_MINT} !important; }}
    .kpi-delta-neg {{ color: {BBG_RED} !important; }}
    .kpi-delta-neu {{ color: {BBG_MUTED} !important; }}

    /* Alertas y Semáforos Bloomberg */
    .badge-status {{
        display: inline-block;
        padding: 3px 8px;
        border-radius: 2px;
        font-family: 'IBM Plex Mono', monospace;
        font-size: 11px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }}
    .badge-status-optimal {{
        background-color: rgba(74, 246, 196, 0.15);
        color: {BBG_MINT};
        border: 1px solid {BBG_MINT};
    }}
    .badge-status-warning {{
        background-color: rgba(255, 209, 92, 0.15);
        color: {BBG_GOLD};
        border: 1px solid {BBG_GOLD};
    }}
    .badge-status-alert {{
        background-color: rgba(251, 139, 31, 0.15);
        color: {BBG_ORANGE};
        border: 1px solid {BBG_ORANGE};
    }}
    .badge-status-critical {{
        background-color: rgba(254, 68, 61, 0.20);
        color: {BBG_RED};
        border: 1px solid {BBG_RED};
    }}

    /* Cajas analíticas e IA */
    .terminal-box {{
        background-color: {BBG_DARK_CARD};
        border: 1px solid {BBG_BORDER};
        border-left: 3px solid {BBG_BLUE};
        border-radius: 4px;
        padding: 14px 18px;
        margin: 14px 0;
    }}
    .terminal-box-title {{
        font-family: 'IBM Plex Mono', monospace;
        font-size: 12px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: {BBG_BLUE};
        margin-bottom: 6px;
    }}

    /* Botones estilo terminal */
    button[kind="primary"], .stButton > button {{
        background-color: {BBG_BLUE} !important;
        color: #FFFFFF !important;
        border: none !important;
        border-radius: 3px !important;
        font-family: 'IBM Plex Sans', sans-serif !important;
        font-weight: 600 !important;
        font-size: 13px !important;
        padding: 6px 14px !important;
        transition: all 0.15s ease-in-out !important;
    }}
    .stButton > button:hover {{
        background-color: #1e78ff !important;
        box-shadow: 0 0 8px rgba(1, 104, 255, 0.5) !important;
    }}

    /* Tablas */
    div[data-testid="stDataFrame"] {{
        background-color: {BBG_DARK_CARD} !important;
        border: 1px solid {BBG_BORDER} !important;
        border-radius: 4px !important;
    }}

    /* Pestañas (Tabs) estilo Bloomberg */
    .stTabs [data-baseweb="tab-list"] {{
        background-color: #080808 !important;
        border-bottom: 2px solid {BBG_BORDER} !important;
        gap: 4px;
    }}
    .stTabs [data-baseweb="tab"] {{
        color: {BBG_MUTED} !important;
        background-color: transparent !important;
        font-family: 'IBM Plex Sans', sans-serif !important;
        font-size: 13px !important;
        font-weight: 600 !important;
        border-radius: 3px 3px 0 0 !important;
        padding: 8px 16px !important;
    }}
    .stTabs [aria-selected="true"] {{
        color: #FFFFFF !important;
        background-color: {BBG_DARK_CARD} !important;
        border-bottom: 3px solid {BBG_BLUE} !important;
    }}

    /* Pie de página */
    .report-footer {{
        border-top: 1px solid {BBG_BORDER};
        padding-top: 12px;
        margin-top: 30px;
        font-family: 'IBM Plex Mono', monospace;
        font-size: 11px;
        color: {BBG_MUTED};
        display: flex;
        justify-content: space-between;
    }}
</style>
"""
