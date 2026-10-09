"""
styles.py
=========
Estilos CSS personalizados con temática Bloomberg Terminal Dark:
- Base Negra: #000000 / #0a0c10
- Rojo Bloomberg (Salidas / Caídas / Alertas): #fe443d
- Azul Bloomberg (Indicador Principal / Primario): #0168ff
- Menta / Turquesa Bloomberg (Activos / Alzas / Óptimo): #4af6c4
- Naranja Bloomberg (Apetito / Advertencias / Acentos): #fb8b1f
"""

# Códigos HEX oficiales de la paleta extraída
BBG_BLACK = "#000000"
BBG_DARK_CARD = "#0d1017"
BBG_BORDER = "#1e2430"
BBG_RED = "#fe443d"
BBG_BLUE = "#0168ff"
BBG_MINT = "#4af6c4"
BBG_ORANGE = "#fb8b1f"

CUSTOM_CSS = f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Inter:wght@300;400;500;600;700&display=swap');
    
    html, body, [class*="css"], .stApp {{
        background-color: {BBG_BLACK} !important;
        color: #f1f5f9 !important;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }}

    /* Sidebar Dark */
    section[data-testid="stSidebar"] {{
        background-color: #08090c !important;
        border-right: 1px solid {BBG_BORDER} !important;
    }}
    
    section[data-testid="stSidebar"] * {{
        color: #e2e8f0 !important;
    }}

    /* Contenedor principal */
    .block-container {{
        padding-top: 1.2rem !important;
        padding-bottom: 2.5rem !important;
        max-width: 95% !important;
        background-color: {BBG_BLACK} !important;
    }}

    /* Header Estilo Terminal Bloomberg */
    .header-banner {{
        background: linear-gradient(135deg, #090b0e 0%, #0f141c 50%, #080a0f 100%);
        border: 1px solid {BBG_BORDER};
        border-top: 3px solid {BBG_ORANGE};
        border-radius: 8px;
        padding: 20px 28px;
        color: #ffffff;
        margin-bottom: 20px;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.8);
        display: flex;
        justify-content: space-between;
        align-items: center;
    }}

    .header-title {{
        font-size: 24px;
        font-weight: 700;
        letter-spacing: -0.5px;
        margin: 0;
        color: #ffffff;
        display: flex;
        align-items: center;
        gap: 10px;
    }}

    .header-title-tag {{
        color: {BBG_ORANGE};
        font-family: 'JetBrains Mono', monospace;
        font-size: 14px;
        background: rgba(251, 139, 31, 0.15);
        border: 1px solid rgba(251, 139, 31, 0.4);
        padding: 2px 8px;
        border-radius: 4px;
    }}

    .header-subtitle {{
        font-size: 13px;
        color: #94a3b8;
        margin-top: 6px;
        font-weight: 400;
    }}

    .badge-status {{
        background-color: rgba(74, 246, 196, 0.12);
        border: 1px solid rgba(74, 246, 196, 0.35);
        padding: 6px 14px;
        border-radius: 4px;
        font-size: 12px;
        font-weight: 600;
        letter-spacing: 0.5px;
        color: {BBG_MINT};
        font-family: 'JetBrains Mono', monospace;
    }}

    /* Tarjetas de Métricas KPI */
    .kpi-card {{
        background: {BBG_DARK_CARD};
        border: 1px solid {BBG_BORDER};
        border-radius: 8px;
        padding: 18px;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.5);
        transition: border-color 0.2s ease, transform 0.15s ease;
        height: 100%;
        position: relative;
    }}

    .kpi-card:hover {{
        border-color: {BBG_BLUE};
        transform: translateY(-2px);
    }}

    .kpi-title {{
        font-size: 12px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.8px;
        color: #8b949e;
        margin-bottom: 8px;
        font-family: 'JetBrains Mono', monospace;
    }}

    .kpi-value {{
        font-size: 30px;
        font-weight: 700;
        color: #ffffff;
        line-height: 1.1;
        letter-spacing: -0.5px;
        font-family: 'JetBrains Mono', monospace;
    }}

    .kpi-delta-box {{
        display: inline-flex;
        align-items: center;
        gap: 6px;
        margin-top: 10px;
        padding: 4px 10px;
        border-radius: 4px;
        font-size: 12px;
        font-weight: 600;
        font-family: 'JetBrains Mono', monospace;
    }}

    .delta-positive {{
        background-color: rgba(74, 246, 196, 0.12);
        color: {BBG_MINT};
        border: 1px solid rgba(74, 246, 196, 0.25);
    }}

    .delta-negative {{
        background-color: rgba(254, 68, 61, 0.12);
        color: {BBG_RED};
        border: 1px solid rgba(254, 68, 61, 0.25);
    }}

    .delta-neutral {{
        background-color: rgba(251, 139, 31, 0.12);
        color: {BBG_ORANGE};
        border: 1px solid rgba(251, 139, 31, 0.25);
    }}

    .kpi-subtext {{
        font-size: 11px;
        color: #64748b;
        margin-top: 8px;
    }}

    /* Caja de Análisis de IA Bloomberg Terminal */
    .ai-box {{
        background: #090c10;
        border: 1px solid {BBG_BORDER};
        border-left: 4px solid {BBG_BLUE};
        border-radius: 8px;
        padding: 20px 24px;
        margin-top: 22px;
        margin-bottom: 22px;
        color: #e2e8f0;
    }}

    .ai-header {{
        display: flex;
        align-items: center;
        gap: 12px;
        font-size: 16px;
        font-weight: 700;
        color: #ffffff;
        margin-bottom: 12px;
    }}

    .ai-badge {{
        background: rgba(1, 104, 255, 0.2);
        color: {BBG_BLUE};
        border: 1px solid rgba(1, 104, 255, 0.4);
        font-size: 11px;
        font-weight: 600;
        padding: 2px 8px;
        border-radius: 4px;
        font-family: 'JetBrains Mono', monospace;
        text-transform: uppercase;
    }}

    /* Estilos de Tabs */
    .stTabs [data-baseweb="tab-list"] {{
        gap: 6px;
        border-bottom: 1px solid {BBG_BORDER};
        background-color: transparent;
    }}

    .stTabs [data-baseweb="tab"] {{
        height: 44px;
        font-weight: 600;
        font-size: 14px;
        padding: 0 18px;
        border-radius: 6px 6px 0 0;
        color: #94a3b8;
        background-color: transparent;
        border: none;
    }}

    .stTabs [aria-selected="true"] {{
        color: {BBG_MINT} !important;
        border-bottom: 2px solid {BBG_MINT} !important;
        background-color: rgba(74, 246, 196, 0.05) !important;
    }}

    /* Botones con estilo Bloomberg */
    .stButton > button {{
        background-color: #121721 !important;
        color: #ffffff !important;
        border: 1px solid {BBG_BORDER} !important;
        border-radius: 6px !important;
        font-weight: 600 !important;
        transition: all 0.2s ease !important;
    }}

    .stButton > button:hover {{
        border-color: {BBG_BLUE} !important;
        color: {BBG_BLUE} !important;
        box-shadow: 0 0 10px rgba(1, 104, 255, 0.3) !important;
    }}

    /* Dataframe y Tablas */
    div[data-testid="stDataFrame"] {{
        border: 1px solid {BBG_BORDER};
        border-radius: 6px;
        background-color: {BBG_DARK_CARD};
    }}
</style>
"""
