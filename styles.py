"""
styles.py
=========
Sistema visual del panel de liquidez con la identidad de Coopeuch.

Principios (uso diario, lectura prolongada):
- Fondo papel cálido y superficies blancas: menos fatiga que el negro puro o el blanco puro.
- El rojo Coopeuch es identidad, no señal: sólo marca, navegación activa y acciones principales.
  En los datos el incumplimiento usa un rojo más profundo (C_BREACH) y siempre va con texto.
- Tipografía Ubuntu (la de coopeuch.cl) para títulos y navegación; Inter con cifras tabulares
  para el texto y los números.

Colores de datos:
    C_IND   azul tinta   indicador (LCR / NSFR), dato real
    C_NUM   verde azulado numerador (ALAC / FED)
    C_DEN   ciruela      denominador (NAF / FER)
Colores de situación (umbrales):
    C_BREACH  incumplimiento / mínimo regulatorio / variación desfavorable
    C_ALERT   alerta interna / simulación
    C_LOW     bajo apetito
    C_OK      dentro de apetito, cumple / variación favorable
"""

from pathlib import Path
import base64

# Marca Coopeuch
BRAND_RED = "#E81E2B"
BRAND_RED_DARK = "#B00009"
BRAND_TINT = "#FDEEEE"

# Neutros
PAPER = "#F6F5F2"
SURFACE = "#FFFFFF"
SURFACE_ALT = "#FAF9F7"
LINE = "#E6E3DD"
LINE_STRONG = "#D2CDC5"
INK = "#2B2B2B"
INK_2 = "#5E5B57"
INK_3 = "#8C8882"

# Datos
C_IND = "#1F3A5F"
C_NUM = "#2A8C82"
C_DEN = "#7B5EA7"

# Situación
C_OK = "#2E7D5B"
C_LOW = "#3B6EA8"
C_ALERT = "#B7791F"
C_BREACH = "#B3261E"
C_UP, C_DOWN = C_OK, C_BREACH

SCENARIO_COLOR = {"Base": C_IND, "Adverso": C_ALERT, "Severo": C_BREACH}
SEVERITY = [C_BREACH, C_ALERT, C_LOW, C_OK]          # de peor a mejor

FONT_UI = "Ubuntu, 'Segoe UI', system-ui, sans-serif"
FONT_TEXT = "Inter, 'Segoe UI', system-ui, sans-serif"

LOGO_PATH = Path(__file__).parent / "assets" / "logo_coopeuch.svg"


def logo_html(height: int = 26) -> str:
    """Logo oficial si existe en assets/; si no, el nombre en tipografía de marca."""
    if LOGO_PATH.exists():
        b64 = base64.b64encode(LOGO_PATH.read_bytes()).decode()
        return f'<img class="brand-logo" style="height:{height}px" src="data:image/svg+xml;base64,{b64}" alt="Coopeuch">'
    return f'<span class="brand-word" style="font-size:{height * 0.95:.0f}px">Coopeuch</span>'


CUSTOM_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Ubuntu:wght@400;500;700&display=swap');

:root {{
  --brand: {BRAND_RED}; --brand-dark: {BRAND_RED_DARK}; --brand-tint: {BRAND_TINT};
  --paper: {PAPER}; --surface: {SURFACE}; --surface-alt: {SURFACE_ALT};
  --line: {LINE}; --line-strong: {LINE_STRONG};
  --ink: {INK}; --ink-2: {INK_2}; --ink-3: {INK_3};
  --ind: {C_IND}; --ok: {C_OK}; --low: {C_LOW}; --alert: {C_ALERT}; --breach: {C_BREACH};
  --ui: {FONT_UI}; --text: {FONT_TEXT};
}}

html, body, .stApp {{ background: var(--paper) !important; color: var(--ink); font-family: var(--text);
  font-feature-settings: "tnum" 1, "lnum" 1; -webkit-font-smoothing: antialiased; }}
.stApp::before {{ content: ""; position: fixed; inset: 0 0 auto 0; height: 3px; background: var(--brand); z-index: 1000000; }}
header[data-testid="stHeader"] {{ background: transparent !important; }}
[data-testid="stAppDeployButton"], [data-testid="stDecoration"] {{ display: none !important; }}
.block-container {{ padding-top: 1.6rem !important; padding-bottom: 3rem !important; max-width: 1360px !important; }}
section[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] {{ display: none !important; }}

h1, h2, h3, h4, h5 {{ font-family: var(--ui) !important; color: var(--ink) !important; letter-spacing: -0.005em; }}
h4 {{ font-size: 1.05rem !important; font-weight: 500 !important; }}
h5 {{ font-size: 0.95rem !important; font-weight: 500 !important; color: var(--ink-2) !important; margin-top: .6rem !important; }}
p, li {{ line-height: 1.55; }}

/* ---------- Cabecera de marca ---------- */
.mast {{ display: flex; align-items: center; justify-content: space-between; gap: 24px; flex-wrap: wrap;
  padding: 6px 0 14px 0; border-bottom: 1px solid var(--line); margin-bottom: 14px; }}
.mast-brand {{ display: flex; align-items: center; gap: 18px; min-width: 0; }}
.brand-logo {{ display: block; }}
.brand-word {{ font-family: var(--ui); font-weight: 700; color: var(--brand); letter-spacing: -0.01em; line-height: 1; }}
.mast-rule {{ width: 1px; height: 34px; background: var(--line-strong); }}
.mast-title {{ font-family: var(--ui); font-size: 19px; font-weight: 500; color: var(--ink); line-height: 1.15; }}
.mast-sub {{ font-size: 12.5px; color: var(--ink-3); margin-top: 2px; }}
.mast-meta {{ display: flex; gap: 22px; flex-wrap: wrap; font-size: 12.5px; color: var(--ink-3); }}
.mast-meta b {{ color: var(--ink); font-weight: 600; }}
.mast-meta .dot {{ display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; vertical-align: 1px; }}

/* ---------- Navegación (segmented control) ---------- */
div[data-testid="stButtonGroup"] button {{ font-family: var(--ui) !important; font-weight: 500 !important; }}
div[data-testid="stButtonGroup"] button[aria-checked="false"] {{ background: var(--surface) !important; }}
/* Navegación principal: el único control con el rojo de marca */
.st-key-modulo button[aria-checked="true"] {{ color: var(--brand) !important; border-color: var(--brand) !important;
  background: var(--brand-tint) !important; }}
button[kind="primary"], button[data-testid="stBaseButton-primary"] {{ background: var(--brand) !important;
  border-color: var(--brand) !important; color: #fff !important; }}
button[kind="primary"]:hover, button[data-testid="stBaseButton-primary"]:hover {{ background: var(--brand-dark) !important; }}
.nav-label {{ font-size: 11px; color: var(--ink-3); text-transform: uppercase; letter-spacing: .08em; margin: 0 0 2px 2px; }}

/* ---------- Titular del día ---------- */
.brief {{ background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 22px 26px 8px 26px; margin: 6px 0 18px 0; }}
.brief-kicker {{ font-family: var(--ui); font-size: 12px; font-weight: 500; color: var(--brand); text-transform: uppercase; letter-spacing: .09em; }}
.brief-lede {{ font-family: var(--ui); font-size: clamp(17px, 1.6vw, 21px); line-height: 1.45; color: var(--ink); margin: 8px 0 14px 0; max-width: 980px; font-weight: 400; }}
.brief-lede b {{ font-weight: 700; }}
.meters {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 0; border-top: 1px solid var(--line); }}
.meter {{ padding: 16px 0 14px 0; display: grid; grid-template-columns: 190px 1fr; gap: 24px; align-items: center; }}
.meter + .meter {{ border-left: 1px solid var(--line); padding-left: 26px; }}
.meter-code {{ font-family: var(--ui); font-size: 14px; color: var(--ink); font-weight: 700; letter-spacing: .02em; }}
.meter-name {{ font-size: 12px; color: var(--ink-3); margin: 1px 0 4px 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.meter-value {{ font-size: 34px; font-weight: 600; color: var(--ink); letter-spacing: -0.02em; line-height: 1.1; white-space: nowrap; }}
.meter-value small {{ font-size: 17px; font-weight: 500; color: var(--ink-3); margin-left: 2px; }}
.meter-delta {{ font-size: 12.5px; font-weight: 600; white-space: nowrap; }}
.meter-delta span {{ color: var(--ink-3); font-weight: 400; }}
@media (max-width: 860px) {{ .meter + .meter {{ border-left: 0; padding-left: 0; border-top: 1px solid var(--line); }}
  .meter {{ grid-template-columns: 1fr; gap: 10px; }} }}

.meters-only {{ padding: 2px 26px 2px 26px; }}
.meters-only .meters {{ border-top: 0; }}

/* Regla de umbrales */
.ruler {{ position: relative; height: 54px; }}
.ruler-track {{ position: absolute; left: 0; right: 0; top: 15px; height: 10px; border-radius: 5px; overflow: hidden; display: flex; }}
.ruler-track i {{ display: block; height: 100%; }}
.ruler-tick {{ position: absolute; top: 11px; width: 1.5px; height: 18px; margin-left: -0.75px; background: var(--ink-3); }}
.ruler-tick-label {{ position: absolute; top: 31px; transform: translateX(-50%); font-size: 10.5px; color: var(--ink-3); white-space: nowrap; }}
.ruler-mark {{ position: absolute; top: 7px; width: 4px; height: 26px; margin-left: -2px; background: var(--ink); border-radius: 2px;
  box-shadow: 0 0 0 2px var(--surface); }}
.ruler-prev {{ position: absolute; top: 13px; width: 9px; height: 9px; margin-left: -4.5px; border: 2px solid var(--ink-3); background: var(--surface); border-radius: 50%; }}
.ruler-state {{ position: absolute; top: -6px; transform: translateX(-50%); font-size: 10.5px; font-weight: 700; letter-spacing: .05em; white-space: nowrap; }}

/* ---------- Libro de cifras (indicador, numerador, denominador, situación) ---------- */
.ledger {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); background: var(--surface); border: 1px solid var(--line);
  border-radius: 12px; margin: 6px 0 12px 0; }}
.ledger > div {{ padding: 16px 20px 14px 20px; min-width: 0; }}
.ledger > div + div {{ border-left: 1px solid var(--line); }}
.l-label {{ font-family: var(--ui); font-size: 12.5px; color: var(--ink-2); font-weight: 500; display: flex; align-items: center; gap: 7px; }}
.l-swatch {{ width: 8px; height: 8px; border-radius: 2px; display: inline-block; }}
.l-value {{ font-size: clamp(20px, 2vw, 28px); font-weight: 600; color: var(--ink); margin: 8px 0 3px 0; letter-spacing: -0.015em; white-space: nowrap; }}
.l-value small {{ font-size: 13px; color: var(--ink-3); font-weight: 500; margin-left: 4px; }}
.l-delta {{ font-size: 12.5px; font-weight: 600; white-space: nowrap; }}
.l-prev {{ font-size: 12px; color: var(--ink-3); margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--line); line-height: 1.5; }}
.l-state {{ display: inline-flex; align-items: center; gap: 7px; font-family: var(--ui); font-size: 14px; font-weight: 700;
  color: var(--c); margin: 8px 0 4px 0; letter-spacing: .02em; }}
.l-state::before {{ content: ""; width: 9px; height: 9px; border-radius: 50%; background: var(--c); }}
.up {{ color: var(--ok); }} .down {{ color: var(--breach); }} .flat {{ color: var(--ink-3); }}
@media (max-width: 1000px) {{ .ledger {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  .ledger > div:nth-child(3) {{ border-left: 0; }} .ledger > div:nth-child(n+3) {{ border-top: 1px solid var(--line); }} }}

/* ---------- Etiquetas de naturaleza del dato ---------- */
.tag {{ display: inline-flex; align-items: center; gap: 6px; font-family: var(--ui); font-size: 11px; font-weight: 500;
  padding: 2px 9px; border-radius: 999px; margin-left: 10px; vertical-align: 2px; white-space: nowrap; letter-spacing: .02em; }}
.tag::before {{ content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }}
.tag-real {{ color: var(--ink-2); background: #EFEDE8; }}
.tag-proy {{ color: var(--ind); background: #E7EDF5; }}
.tag-proy::before {{ background: transparent; border: 1.5px dashed currentColor; width: 5px; height: 5px; }}
.tag-sim {{ color: #8A5A10; background: #F8EEDB; }}

/* ---------- Secciones y notas ---------- */
.sec {{ margin: 26px 0 10px 0; }}
.sec-h {{ display: flex; align-items: center; flex-wrap: wrap; gap: 4px; }}
.sec-h .t {{ font-family: var(--ui); font-size: 18px; font-weight: 500; color: var(--ink); }}
.sec-s {{ font-size: 12.5px; color: var(--ink-3); margin-top: 2px; }}
.note {{ background: var(--surface); border: 1px solid var(--line); border-left: 3px solid var(--c, var(--ind)); border-radius: 0 10px 10px 0;
  padding: 11px 16px; margin: 8px 0 12px 0; font-size: 13.5px; color: var(--ink); line-height: 1.6; }}
.note b {{ font-weight: 600; }}
.note ul {{ margin: 6px 0 0 0; padding-left: 18px; }}
.summary-block {{ background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 14px 18px; height: 100%; }}
.summary-block h6 {{ font-family: var(--ui); font-size: 14px; font-weight: 500; color: var(--ink); margin: 0 0 8px 0;
  padding-left: 10px; border-left: 3px solid var(--c, var(--ind)); }}
.summary-block ul {{ margin: 0; padding-left: 18px; font-size: 13.5px; color: var(--ink); }}
.conclusion {{ background: var(--surface); border: 1px solid var(--line); border-top: 3px solid var(--c); border-radius: 12px;
  padding: 16px 20px; font-family: var(--ui); font-size: 16px; line-height: 1.55; margin: 12px 0; }}

/* ---------- Componentes nativos ---------- */
.stTabs [data-baseweb="tab-list"], div[data-testid="stTabs"] [role="tablist"] {{ gap: 26px; border-bottom: 1px solid var(--line); }}
.stTabs [data-baseweb="tab"], div[data-testid="stTab"] {{ height: 44px; padding: 0 2px; font-family: var(--ui); font-weight: 500;
  font-size: 14.5px; color: var(--ink-3); background: transparent !important; }}
.stTabs [aria-selected="true"], div[data-testid="stTab"][aria-selected="true"] {{ color: var(--ink) !important; }}
.stTabs [data-baseweb="tab-highlight"] {{ background-color: var(--brand) !important; height: 2px !important; }}
.stTabs [data-baseweb="tab-border"] {{ display: none; }}
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {{ font-family: var(--ui) !important; font-weight: 500 !important; }}
div[data-testid="stMetric"] {{ background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 12px 16px; }}
div[data-testid="stMetricLabel"] p {{ font-family: var(--ui); font-size: 12.5px !important; color: var(--ink-2) !important; }}
div[data-testid="stMetricValue"] {{ font-weight: 600 !important; font-size: 1.55rem !important; letter-spacing: -0.015em; }}
div[data-testid="stPlotlyChart"] {{ background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 6px 4px 0 4px; }}
div[data-testid="stExpander"] details {{ background: var(--surface); border-radius: 10px; }}
div[data-testid="stCaptionContainer"] {{ color: var(--ink-3); }}
hr {{ border-color: var(--line) !important; }}
.report-footer {{ text-align: center; color: var(--ink-3); font-size: 12px; border-top: 1px solid var(--line);
  padding: 14px 0 0 0; margin-top: 34px; line-height: 1.7; }}
.report-footer i {{ font-style: normal; display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin: 0 5px 0 10px; }}
</style>
"""
