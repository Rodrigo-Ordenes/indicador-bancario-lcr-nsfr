"""
styles.py
=========
Paleta Bloomberg Terminal del panel de liquidez (exactamente los cinco colores definidos):

    #000000  Base negra       : fondo, tarjetas y lienzos de gráficos.
    #0168ff  Azul Bloomberg   : indicador principal (LCR / NSFR), elementos activos,
                                estado "bajo apetito" y proyecciones.
    #fe443d  Rojo Bloomberg   : NAF / FER, límite regulatorio (100%), variaciones
                                negativas e incumplimientos.
    #4af6c4  Menta Bloomberg  : ALAC / FED, apetito por riesgo (LCR 150%), variaciones
                                positivas y cumplimiento holgado.
    #fb8b1f  Naranja Bloomberg: alerta interna (LCR 120% / NSFR 105%), avisos preventivos
                                y resultados simulados.

Los grises son sólo tonos de apoyo para texto secundario y filetes; ningún otro color
transmite significado.
"""

BBG_BLACK = "#000000"
BBG_BLUE = "#0168ff"
BBG_RED = "#fe443d"
BBG_MINT = "#4af6c4"
BBG_ORANGE = "#fb8b1f"

# Tonos neutros de apoyo (no semánticos)
BBG_SURFACE = "#0A0A0A"
BBG_BORDER = "#262626"
BBG_TEXT = "#FFFFFF"
BBG_MUTED = "#A0A0A0"
BBG_FAINT = "#6B6B6B"

# Alias usados por módulos existentes
BBG_DARK_CARD = BBG_SURFACE
INK_BG, INK_SURFACE, INK_BORDER, INK_TEXT, INK_MUTED, INK_FAINT = (
    BBG_BLACK, BBG_SURFACE, BBG_BORDER, BBG_TEXT, BBG_MUTED, BBG_FAINT)
COLOR_PRIMARY, COLOR_POSITIVE, COLOR_NEGATIVE, COLOR_WARNING, COLOR_ACCENT = (
    BBG_BLUE, BBG_MINT, BBG_RED, BBG_ORANGE, BBG_ORANGE)

CUSTOM_CSS = f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap');

    html, body, [class*="css"], .stApp {{
        background-color: {BBG_BLACK} !important;
        color: {BBG_TEXT} !important;
        font-family: 'IBM Plex Sans', -apple-system, 'Segoe UI', sans-serif;
        font-feature-settings: "tnum" 1, "lnum" 1;
    }}
    .block-container {{ padding-top: 3.2rem !important; padding-bottom: 2rem !important; max-width: 1500px !important; }}

    h1, h2, h3, h4 {{ font-family: 'IBM Plex Sans', sans-serif !important; color: {BBG_TEXT} !important; letter-spacing: 0 !important; }}
    h3 {{ font-size: 1.25rem !important; font-weight: 700 !important; }}
    h4 {{ font-size: 1.02rem !important; font-weight: 600 !important; }}
    h5 {{ font-family: 'IBM Plex Mono', monospace !important; font-size: 0.78rem !important; font-weight: 600 !important;
          text-transform: uppercase; letter-spacing: 0.08em; color: {BBG_MUTED} !important; }}
    p, li {{ line-height: 1.5; }}

    /* Barra lateral */
    section[data-testid="stSidebar"] {{ background-color: {BBG_BLACK} !important; border-right: 1px solid {BBG_BORDER} !important; }}
    section[data-testid="stSidebar"] * {{ color: {BBG_TEXT}; }}
    .sidebar-brand {{ border-bottom: 2px solid {BBG_ORANGE}; padding: 2px 0 12px 0; margin-bottom: 8px; }}
    .sidebar-brand-title {{ font-family: 'IBM Plex Mono', monospace; font-size: 16px; font-weight: 700; color: {BBG_ORANGE}; letter-spacing: 0.04em; }}
    .sidebar-brand-sub {{ font-size: 11px; color: {BBG_MUTED}; letter-spacing: 0.06em; text-transform: uppercase; margin-top: 2px; }}
    .sidebar-section {{ font-family: 'IBM Plex Mono', monospace; font-size: 11px; font-weight: 600; letter-spacing: 0.1em;
                       text-transform: uppercase; color: {BBG_MUTED}; margin: 16px 0 4px 0; }}
    .sidebar-note {{ font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: {BBG_MUTED}; border: 1px solid {BBG_BORDER};
                    border-left: 3px solid {BBG_BLUE}; padding: 8px 10px; line-height: 1.5; }}

    /* Encabezado */
    .header-banner {{ border: 1px solid {BBG_BORDER}; border-top: 3px solid {BBG_ORANGE}; padding: 14px 22px; margin-bottom: 12px;
                     display: flex; justify-content: space-between; align-items: flex-end; gap: 24px; background: {BBG_BLACK}; }}
    .header-eyebrow {{ font-family: 'IBM Plex Mono', monospace; font-size: 11px; font-weight: 600; color: {BBG_ORANGE};
                      letter-spacing: 0.12em; text-transform: uppercase; }}
    .header-title {{ font-size: 24px; font-weight: 700; color: {BBG_TEXT}; margin-top: 4px; }}
    .header-subtitle {{ font-size: 13px; color: {BBG_MUTED}; margin-top: 2px; }}
    .header-meta {{ font-family: 'IBM Plex Mono', monospace; font-size: 12px; color: {BBG_MUTED}; text-align: right; line-height: 1.7; white-space: nowrap; }}
    .header-meta b {{ color: {BBG_TEXT}; }}

    /* Franja de situación permanente (LCR y NSFR) */
    .status-strip {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px; margin-bottom: 16px; }}
    .status-chip {{ border: 1px solid {BBG_BORDER}; border-left: 4px solid var(--c); padding: 8px 14px; display: flex;
                   justify-content: space-between; align-items: center; gap: 10px; font-family: 'IBM Plex Mono', monospace; }}
    .status-chip .col {{ display: flex; flex-direction: column; gap: 2px; min-width: 0; }}
    .status-chip .col.right {{ align-items: flex-end; text-align: right; }}
    .status-chip .name {{ font-size: 11px; color: {BBG_MUTED}; letter-spacing: 0.06em; white-space: nowrap; }}
    .status-chip .value {{ font-size: 20px; font-weight: 700; color: {BBG_TEXT}; white-space: nowrap; }}
    .status-chip .state {{ font-size: 10.5px; font-weight: 700; color: var(--c); border: 1px solid var(--c); padding: 2px 8px;
                          letter-spacing: 0.06em; white-space: nowrap; }}

    /* Tarjetas de indicadores */
    .kpi-card {{ border: 1px solid {BBG_BORDER}; border-top: 3px solid var(--c, {BBG_BLUE}); padding: 14px 16px; height: 100%; background: {BBG_BLACK}; }}
    .kpi-label {{ font-family: 'IBM Plex Mono', monospace; font-size: 11px; font-weight: 600; color: {BBG_MUTED};
                 letter-spacing: 0.08em; text-transform: uppercase; }}
    .kpi-value {{ font-family: 'IBM Plex Mono', monospace; font-size: clamp(17px, 1.9vw, 28px); font-weight: 700;
                 color: var(--c, {BBG_TEXT}); margin: 6px 0 2px 0; white-space: nowrap; }}
    .kpi-unit {{ font-size: 13px; color: {BBG_MUTED}; margin-left: 4px; font-weight: 500; }}
    .kpi-delta {{ font-family: 'IBM Plex Mono', monospace; font-size: clamp(11px, 0.95vw, 13px); font-weight: 600; }}
    .kpi-sub {{ font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: {BBG_MUTED}; margin-top: 6px;
               border-top: 1px solid {BBG_BORDER}; padding-top: 6px; line-height: 1.5; }}
    .up {{ color: {BBG_MINT}; }}
    .down {{ color: {BBG_RED}; }}
    .state-badge {{ display: inline-block; font-family: 'IBM Plex Mono', monospace; font-size: 13px; font-weight: 700;
                   color: var(--c); border: 1px solid var(--c); padding: 4px 10px; letter-spacing: 0.06em; margin: 6px 0; }}

    /* Etiquetas de naturaleza del dato */
    .tag {{ display: inline-block; font-family: 'IBM Plex Mono', monospace; font-size: 10.5px; font-weight: 700;
           letter-spacing: 0.08em; padding: 2px 7px; margin-left: 8px; vertical-align: middle; }}
    .tag-real {{ color: {BBG_MINT}; border: 1px solid {BBG_MINT}; }}
    .tag-proy {{ color: {BBG_BLUE}; border: 1px dashed {BBG_BLUE}; }}
    .tag-sim  {{ color: {BBG_ORANGE}; border: 1px solid {BBG_ORANGE}; }}

    /* Títulos de sección y notas */
    .section-title {{ display: flex; align-items: baseline; justify-content: space-between; border-bottom: 1px solid {BBG_BORDER};
                     padding-bottom: 6px; margin: 22px 0 10px 0; }}
    .section-title h3, .section-title .t {{ margin: 0 !important; font-size: 1.08rem; font-weight: 700; color: {BBG_TEXT}; }}
    .section-title span.s {{ font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: {BBG_MUTED}; letter-spacing: 0.06em; }}
    .note-box {{ border: 1px solid {BBG_BORDER}; border-left: 3px solid var(--c, {BBG_BLUE}); padding: 10px 14px; margin: 8px 0;
                font-size: 13px; color: {BBG_TEXT}; line-height: 1.55; }}
    .note-box b {{ color: var(--c, {BBG_BLUE}); }}
    .summary-block {{ border: 1px solid {BBG_BORDER}; padding: 12px 16px; height: 100%; }}
    .summary-block h6 {{ font-family: 'IBM Plex Mono', monospace; font-size: 11px; letter-spacing: 0.08em; color: var(--c, {BBG_BLUE});
                        text-transform: uppercase; margin: 0 0 6px 0; }}
    .summary-block ul {{ margin: 0; padding-left: 18px; font-size: 13px; }}
    .conclusion {{ border: 1px solid var(--c); padding: 14px 18px; font-size: 15px; line-height: 1.55; margin: 10px 0; }}

    /* Pestañas, botones, métricas, tablas */
    .stTabs [data-baseweb="tab-list"] {{ gap: 2px; border-bottom: 1px solid {BBG_BORDER}; }}
    .stTabs [data-baseweb="tab"] {{ height: 40px; padding: 0 16px; font-weight: 600; font-size: 13.5px; color: {BBG_MUTED}; border-radius: 0; }}
    .stTabs [aria-selected="true"] {{ color: {BBG_TEXT} !important; border-bottom: 2px solid {BBG_ORANGE} !important; }}
    .stButton > button, .stDownloadButton > button {{ background: {BBG_BLACK} !important; color: {BBG_TEXT} !important;
        border: 1px solid {BBG_BORDER} !important; border-radius: 0 !important; font-weight: 600 !important; }}
    .stButton > button:hover, .stDownloadButton > button:hover {{ border-color: {BBG_BLUE} !important; color: {BBG_BLUE} !important; }}
    div[data-testid="stMetric"] {{ border: 1px solid {BBG_BORDER}; padding: 10px 14px; background: {BBG_BLACK}; }}
    div[data-testid="stMetricLabel"] p {{ font-family: 'IBM Plex Mono', monospace; font-size: 11px !important; text-transform: uppercase;
        letter-spacing: 0.06em; color: {BBG_MUTED} !important; }}
    div[data-testid="stMetricValue"] {{ font-family: 'IBM Plex Mono', monospace !important; font-size: 1.4rem !important; }}
    div[data-testid="stDataFrame"] {{ border: 1px solid {BBG_BORDER}; }}
    div[data-testid="stAlert"] {{ border-radius: 0 !important; }}
    hr {{ border-color: {BBG_BORDER} !important; }}
    .report-footer {{ font-family: 'IBM Plex Mono', monospace; text-align: center; color: {BBG_FAINT}; font-size: 11px;
                     border-top: 1px solid {BBG_BORDER}; padding: 12px 0 0 0; margin-top: 26px; line-height: 1.6; }}
</style>
"""
