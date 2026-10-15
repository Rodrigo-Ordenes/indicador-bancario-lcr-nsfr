"""
pdf_generator.py
================
Informe PDF consolidado de liquidez (requerimiento 4) para la alta administración.

Todo el contenido se calcula con `analytics` a partir de los datos cortados a la
fecha seleccionada: indicadores, umbrales, holguras, tendencias, principales
variaciones, escenarios de tensión (recálculo por componente con topes),
proyecciones (rotuladas como estimaciones), alertas, conclusiones, observaciones
y acciones, supuestos y resultado de la validación de calidad de datos.

Formato: página blanca A4 con encabezado negro y filete naranja; gráficos en
paneles negros con la paleta Bloomberg (azul, rojo, menta, naranja).
"""

import io
import os
from datetime import datetime
from typing import Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402
import pandas as pd  # noqa: E402
from fpdf import FPDF  # noqa: E402
from fpdf.fonts import FontFace  # noqa: E402

import analytics as an  # noqa: E402
from styles import BBG_BLACK, BBG_BLUE, BBG_MINT, BBG_ORANGE, BBG_RED  # noqa: E402

FONT_DIR = os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data", "fonts", "ttf")
SCEN_COLOR = {"Base": BBG_BLUE, "Adverso": BBG_ORANGE, "Severo": BBG_RED}
DASH = {"Regulatorio": "-", "Interno: alerta": "--", "Interno: apetito": ":"}


def _hex(c: str):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


class Report(FPDF):
    def __init__(self, corte: pd.Timestamp, generado: datetime):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.corte, self.generado = corte, generado
        self.add_font("dv", "", os.path.join(FONT_DIR, "DejaVuSans.ttf"))
        self.add_font("dv", "B", os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf"))
        self.add_font("dvm", "", os.path.join(FONT_DIR, "DejaVuSansMono.ttf"))
        self.set_auto_page_break(True, margin=16)
        self.set_margins(14, 24, 14)

    def header(self):
        self.set_fill_color(*_hex(BBG_BLACK))
        self.rect(0, 0, 210, 15, "F")
        self.set_fill_color(*_hex(BBG_ORANGE))
        self.rect(0, 15, 210, 0.9, "F")
        self.set_xy(14, 4.5)
        self.set_font("dv", "B", 10)
        self.set_text_color(255, 255, 255)
        self.cell(120, 6, "INFORME DE LIQUIDEZ · LCR Y NSFR")
        self.set_font("dvm", "", 8)
        self.set_text_color(*_hex(BBG_ORANGE))
        self.cell(62, 6, f"Corte {self.corte:%d-%m-%Y}", align="R")
        self.set_y(22)

    def footer(self):
        self.set_y(-11)
        self.set_font("dv", "", 7)
        self.set_text_color(110, 110, 110)
        self.cell(0, 5, f"Gerencia de Riesgo Financiero · Uso interno · Generado {self.generado:%d-%m-%Y %H:%M} · "
                        f"Página {self.page_no()}/{{nb}}", align="C")

    # -- utilidades de maquetación ------------------------------------------
    def h1(self, text: str):
        self.ln(2)
        self.set_font("dv", "B", 12)
        self.set_text_color(0, 0, 0)
        self.cell(0, 7, text, new_x="LMARGIN", new_y="NEXT")
        self.set_fill_color(*_hex(BBG_ORANGE))
        self.rect(self.l_margin, self.get_y(), 18, 0.7, "F")
        self.ln(3)

    def tag(self, text: str, color: str):
        # Texto negro (legible en papel) con marco del color semántico de la paleta.
        self.set_font("dvm", "", 7)
        self.set_text_color(0, 0, 0)
        self.set_draw_color(*_hex(color))
        self.set_line_width(0.6)
        w = self.get_string_width(text) + 4
        self.cell(w, 4.5, text, border=1, align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_line_width(0.2)
        self.ln(1.5)
        self.set_text_color(0, 0, 0)

    def para(self, text: str, size: float = 9, bold: bool = False):
        self.set_font("dv", "B" if bold else "", size)
        self.set_text_color(20, 20, 20)
        self.multi_cell(0, 4.6, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def bullets(self, items: List[str], size: float = 8.8):
        self.set_font("dv", "", size)
        self.set_text_color(20, 20, 20)
        for it in items:
            self.multi_cell(0, 4.4, f"•  {it}", new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def box(self, text: str, color: str):
        self.set_draw_color(*_hex(color))
        self.set_line_width(0.6)
        self.set_font("dv", "", 9.2)
        self.set_text_color(0, 0, 0)
        self.multi_cell(0, 5, text, border=1, padding=2.5, new_x="LMARGIN", new_y="NEXT")
        self.set_line_width(0.2)
        self.ln(2)

    def table(self, df: pd.DataFrame, widths=None, size: float = 7.6, align_num: bool = True):
        self.set_font("dv", "", size)
        self.set_text_color(0, 0, 0)
        self.set_draw_color(200, 200, 200)
        self.set_fill_color(242, 242, 242)   # filas alternas en gris (evita heredar el color del último filete)
        head = FontFace(emphasis="BOLD", color=(255, 255, 255), fill_color=_hex(BBG_BLACK))
        with super().table(col_widths=widths, headings_style=head, line_height=4.4, text_align="LEFT",
                           borders_layout="HORIZONTAL_LINES", cell_fill_color=(246, 246, 246),
                           cell_fill_mode="ROWS") as t:
            t.row([str(c) for c in df.columns])
            for _, r in df.iterrows():
                t.row([str(v) for v in r.values])
        self.ln(2)

    def image_fig(self, fig, h: float):
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=170, facecolor=BBG_BLACK)
        plt.close(fig)
        buf.seek(0)
        if self.get_y() + h > self.h - 18:
            self.add_page()
        self.image(buf, x=self.l_margin, w=self.w - self.l_margin - self.r_margin, h=h)
        self.ln(2)


# -----------------------------------------------------------------------------
# Gráficos (matplotlib, panel negro con paleta Bloomberg)
# -----------------------------------------------------------------------------

def _style(ax):
    ax.set_facecolor(BBG_BLACK)
    for s in ax.spines.values():
        s.set_color("#333333")
    ax.tick_params(colors="#BBBBBB", labelsize=7)
    ax.grid(color="#1F1F1F", linewidth=0.6)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%y"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))


def _trend_fig(hist: pd.DataFrame, ind, umbrales, title: str):
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(9, 4.4), sharex=True, gridspec_kw={"height_ratios": [1.4, 1]})
    fig.patch.set_facecolor(BBG_BLACK)
    for ax in (a1, a2):
        _style(ax)
    a1.plot(hist["fecha"], hist[ind.ratio_col], color=BBG_BLUE, lw=1.6, label=ind.codigo)
    for u in umbrales:
        a1.axhline(u.valor, color=u.color, ls=DASH[u.tipo], lw=1, label=f"{u.nombre} {u.valor:.0f}%")
    a1.set_title(title, color="white", fontsize=9, loc="left")
    a1.legend(fontsize=6.5, facecolor=BBG_BLACK, edgecolor="#333333", labelcolor="white", ncol=4, loc="lower left")
    a2.plot(hist["fecha"], hist[ind.num_col], color=BBG_MINT, lw=1.3, label=f"{ind.num} (MM$)")
    a2.plot(hist["fecha"], hist[ind.den_col], color=BBG_RED, lw=1.3, label=f"{ind.den} (MM$)")
    a2.legend(fontsize=6.5, facecolor=BBG_BLACK, edgecolor="#333333", labelcolor="white", ncol=2, loc="upper left")
    fig.tight_layout()
    return fig


def _projection_fig(hist: pd.DataFrame, ind, proj: Dict[str, pd.DataFrame], umbrales):
    fig, ax = plt.subplots(figsize=(9, 3.2))
    fig.patch.set_facecolor(BBG_BLACK)
    _style(ax)
    h = hist.tail(120)
    ax.plot(h["fecha"], h[ind.ratio_col], color=BBG_BLUE, lw=1.6, label=f"{ind.codigo} real")
    for esc, df in proj.items():
        c = SCEN_COLOR.get(esc, BBG_ORANGE)
        ax.fill_between(df["fecha"], df["lower"], df["upper"], color=c, alpha=0.12, lw=0)
        ax.plot(df["fecha"], df["pred"], color=c, lw=1.4, ls="--", label=f"Proyección {esc} (estimada)")
    for u in umbrales:
        ax.axhline(u.valor, color=u.color, ls=DASH[u.tipo], lw=0.9)
    ax.set_title(f"Proyección del {ind.codigo} por escenario (estimación, banda 95%)", color="white", fontsize=9, loc="left")
    ax.legend(fontsize=6.5, facecolor=BBG_BLACK, edgecolor="#333333", labelcolor="white", ncol=4, loc="lower left")
    fig.tight_layout()
    return fig


# -----------------------------------------------------------------------------
# Construcción del informe
# -----------------------------------------------------------------------------

def _cut(hist, det, corte):
    h = hist[pd.to_datetime(hist["fecha"]) <= corte].copy()
    d = det[pd.to_datetime(det["fecha"]) <= corte].copy()
    return h, d


def _f(v, fmt="{:,.0f}"):
    return "—" if v is None or pd.isna(v) else fmt.format(v)


def build_report(lcr_hist, lcr_det, nsfr_hist, nsfr_det, corte: pd.Timestamp, granularidad: str,
                 meses_tendencia: int, escenarios: List[str], horizonte: int, umbrales_lcr, umbrales_nsfr,
                 observaciones: pd.DataFrame, comentario: str, calidad: pd.DataFrame, fuente: str) -> bytes:
    generado = datetime.now()
    data = {}
    for ind, hist, det, umb in ((an.LCR, lcr_hist, lcr_det, umbrales_lcr), (an.NSFR, nsfr_hist, nsfr_det, umbrales_nsfr)):
        h, d = _cut(hist, det, corte)
        closes = an.period_closes(h, ind, granularidad)
        k = an.kpi_snapshot(closes, ind, umb, granularidad)
        lines = an.lines_at(d, k["fecha"])
        stress = {e: an.run_stress(ind, lines, an.scenario_shocks(ind, lines, e)) for e in ["Base"] + list(escenarios)}
        for e, r in stress.items():
            r["nombre"] = e
        data[ind.codigo] = {
            "ind": ind, "hist": h, "det": d, "umb": umb, "k": k,
            "dec": an.decompose(d, ind, k["fecha"], k["fecha_ant"]),
            "buf": an.buffers(ind, k["ratio"], k["num"], k["den"], umb),
            "alerts": an.alert_statistics(h, ind, umb), "stress": stress,
            "proj": an.project_with_scenarios(h, ind, lines, horizonte, ["Base"] + list(escenarios)),
        }
    L, N = data["LCR"], data["NSFR"]
    worst = (escenarios[-1] if escenarios else "Base")
    summ = an.executive_summary(L["k"], N["k"], L["dec"], N["dec"], L["stress"][worst], N["stress"][worst],
                                umbrales_lcr, umbrales_nsfr)

    pdf = Report(corte, generado)
    pdf.alias_nb_pages()
    pdf.add_page()

    # 1. Portada y resumen ------------------------------------------------------
    pdf.set_font("dv", "B", 17)
    pdf.cell(0, 9, "Informe de liquidez: LCR y NSFR", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("dv", "", 9)
    pdf.set_text_color(90, 90, 90)
    pdf.multi_cell(0, 4.6, f"Fecha de corte: {corte:%d-%m-%Y}  ·  Fecha de generación: {generado:%d-%m-%Y %H:%M}  ·  "
                           f"Variaciones: {granularidad.lower()} ({L['k']['periodo']} {L['k']['ref_ant']})  ·  "
                           f"Tendencia: últimos {meses_tendencia} meses\nFuente: {fuente}",
                   new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.h1("1. Situación consolidada")
    pdf.tag("DATO REAL", BBG_MINT)
    rows = []
    for code, D in (("LCR", L), ("NSFR", N)):
        k = D["k"]
        dist = " / ".join(f"{u.valor:.0f}%: {k['ratio'] - u.valor:+.1f} pp" for u in D["umb"])
        rows.append([code, f"{k['ratio']:.2f}%", f"{k['d_ratio']:+.2f} pp", k["estado"]["codigo"],
                     f"{k['num']:,.0f}", f"{k['den']:,.0f}", dist])
    pdf.table(pd.DataFrame(rows, columns=["Indicador", "Valor", "Variación", "Situación", "Numerador (MM$)",
                                          "Denominador (MM$)", "Distancia a umbrales"]),
              widths=(19, 17, 18, 28, 24, 25, 51))
    worst_color = min((L["k"]["estado"]["color"], N["k"]["estado"]["color"]),
                      key=[BBG_RED, BBG_ORANGE, BBG_BLUE, BBG_MINT].index)
    pdf.box("Conclusión: " + summ["conclusion"] + (f"\n\nComentario de la gerencia: {comentario}" if comentario else ""),
            worst_color)
    pdf.para("Principales movimientos del período", 9.5, True)
    pdf.bullets(summ["movimientos"])
    pdf.para("Indicadores próximos a sus umbrales", 9.5, True)
    pdf.bullets(summ["proximos"])

    # 2. Tendencias ------------------------------------------------------------
    pdf.add_page()
    pdf.h1("2. Tendencias e indicadores frente a umbrales")
    pdf.tag("DATO REAL", BBG_MINT)
    start = corte - pd.DateOffset(months=meses_tendencia)
    for code, D in (("LCR", L), ("NSFR", N)):
        h = D["hist"][D["hist"]["fecha"] >= start]
        pdf.image_fig(_trend_fig(h, D["ind"], D["umb"], f"{code}, {D['ind'].num} y {D['ind'].den} · {meses_tendencia} meses"), 92)
    umb_txt = "; ".join(f"{u.nombre} {u.valor:.0f}% ({u.tipo.lower()})" for u in umbrales_lcr)
    pdf.para(f"Umbrales LCR: {umb_txt}. Umbrales NSFR: " +
             "; ".join(f"{u.nombre} {u.valor:.0f}% ({u.tipo.lower()})" for u in umbrales_nsfr) +
             ". El mínimo regulatorio es obligatorio; alerta y apetito son niveles internos.", 8)

    # 3. Holguras ------------------------------------------------------------
    pdf.add_page()
    pdf.h1("3. Holgura frente a los límites")
    pdf.tag("DATO REAL", BBG_MINT)
    for code, D in (("LCR", L), ("NSFR", N)):
        ind = D["ind"]
        b = D["buf"]
        t = pd.DataFrame({"Umbral": b["Umbral"], "Tipo": b["Tipo"], "Distancia": b["Distancia (pp)"].map("{:+.2f} pp".format),
                          f"Holgura {ind.num} (MM$)": b[f"Holgura en {ind.num} (MM$)"].map("{:+,.0f}".format),
                          f"Holgura {ind.den} (MM$)": b[f"Holgura en {ind.den} (MM$)"].map("{:+,.0f}".format),
                          f"{ind.num} requerido (MM$)": b[f"{ind.num} adicional requerido (MM$)"].map("{:,.0f}".format)})
        pdf.para(f"{code} · {D['k']['ratio']:.2f}%", 9.5, True)
        pdf.table(t, widths=(44, 26, 20, 30, 30, 32))
        a = D["alerts"]
        pdf.table(pd.DataFrame({"Umbral": a["Umbral"],
                                "Días consecutivos actuales": a["Días consecutivos actuales bajo el umbral"],
                                "Días totales bajo": a["Días totales bajo el umbral"],
                                "Frecuencia": a["Frecuencia histórica (%)"].map("{:.1f}%".format),
                                "Racha máxima": a["Racha máxima (días)"], "Última vez": a["Última fecha bajo el umbral"]}),
                  widths=(44, 30, 24, 22, 22, 40))

    # 4. Principales variaciones ---------------------------------------------
    pdf.h1("4. Principales variaciones del período")
    pdf.tag("DATO REAL", BBG_MINT)
    for code, D in (("LCR", L), ("NSFR", N)):
        k = D["k"]
        pdf.para(f"{code}: {k['periodo_ant']} → {k['periodo']} ({k['d_ratio']:+.2f} pp)", 9.5, True)
        top = D["dec"].head(6)
        pdf.table(pd.DataFrame({"Componente": top["subcategoria"].str.slice(0, 48), "Bloque": top["componente"],
                                "Anterior (MM$)": top["monto_ant"].map("{:,.0f}".format),
                                "Actual (MM$)": top["monto_act"].map("{:,.0f}".format),
                                "Variación (MM$)": top["var_abs"].map("{:+,.0f}".format),
                                "Variación %": top["var_pct"].map(lambda v: _f(v, "{:+.1f}%")),
                                "Impacto (pp)": top["impacto_pp"].map("{:+.2f}".format)}),
                  widths=(62, 15, 24, 24, 24, 17, 16))

    # 5. Escenarios de tensión -------------------------------------------------
    pdf.add_page()
    pdf.h1("5. Pruebas de tensión")
    pdf.tag("SIMULACIÓN", BBG_ORANGE)
    rows = []
    for e in ["Base"] + list(escenarios):
        for code, D in (("LCR", L), ("NSFR", N)):
            r = D["stress"][e]
            rows.append([e, code, f"{r['base']['ratio']:.2f}%", f"{r['estresado']['ratio']:.2f}%",
                         f"{r['delta_pp']:+.2f} pp", f"{r['estresado']['num']:,.0f}", f"{r['estresado']['den']:,.0f}",
                         an.estado(r["estresado"]["ratio"], D["umb"])["codigo"]])
    pdf.table(pd.DataFrame(rows, columns=["Escenario", "Indicador", "Original", "Simulado", "Impacto",
                                          "Numerador (MM$)", "Denominador (MM$)", "Situación"]),
              widths=(20, 17, 19, 19, 20, 27, 29, 31))
    for e in escenarios:
        pdf.para(f"Escenario {e}: {an.SCENARIO_DESCRIPTION[e]}", 8.5)
        for code, D in (("LCR", L), ("NSFR", N)):
            ln = D["stress"][e]["lineas"]
            ln = ln[ln["shock_pct"] != 0].sort_values("impacto_individual_pp").head(5)
            if not ln.empty:
                pdf.table(pd.DataFrame({f"{code} · componente": ln["subcategoria"].str.slice(0, 55),
                                        "Shock": ln["shock_pct"].map("{:+.0f}%".format),
                                        "Variación (MM$)": ln["variacion_monto"].map("{:+,.0f}".format),
                                        "Impacto (pp)": ln["impacto_individual_pp"].map("{:+.2f}".format)}),
                          widths=(100, 22, 30, 30), size=7.2)

    # 6. Proyecciones -----------------------------------------------------------
    pdf.add_page()
    pdf.h1("6. Proyecciones")
    pdf.tag("PROYECCIÓN · ESTIMACIÓN", BBG_BLUE)
    pdf.para("Las proyecciones son estimaciones estadísticas sujetas a incertidumbre; no constituyen resultados "
             "ciertos. Se proyectan numerador y denominador con sus montos históricos (Holt amortiguado) y cada "
             "escenario aplica gradualmente, durante 30 días, el efecto de sus shocks.", 8.3)
    for code, D in (("LCR", L), ("NSFR", N)):
        pdf.image_fig(_projection_fig(D["hist"], D["ind"], D["proj"], D["umb"]), 64)
        rows = []
        for e, df in D["proj"].items():
            last_real = float(D["hist"][D["ind"].ratio_col].iloc[-1])
            cross = [f"{u.valor:.0f}%: " + an.threshold_crossing(df, last_real, u.valor).lower() for u in D["umb"]]
            rows.append([e, f"{df['pred'].iloc[min(29, len(df) - 1)]:.2f}%", f"{df['pred'].iloc[-1]:.2f}%",
                         f"[{df['lower'].iloc[-1]:.1f}% ; {df['upper'].iloc[-1]:.1f}%]", " · ".join(cross)])
        pdf.table(pd.DataFrame(rows, columns=["Escenario", "Día 30", f"Día {horizonte}", "Intervalo 95% final",
                                              "Cruce de umbrales (estimado)"]), widths=(22, 18, 20, 38, 84))

    # 7. Alertas, observaciones y acciones -------------------------------------
    pdf.add_page()
    pdf.h1("7. Alertas, conclusiones y acciones de mitigación")
    pdf.para("Componentes que contribuyen al deterioro", 9.5, True)
    pdf.bullets(summ["deterioro"])
    pdf.para(f"Resultado del escenario {worst.lower()}", 9.5, True)
    pdf.bullets(summ["adverso"])
    pdf.para("Observaciones y acciones registradas", 9.5, True)
    if observaciones is None or observaciones.empty:
        pdf.para("No se seleccionaron observaciones para este informe.", 8.5)
    else:
        o = observaciones
        pdf.table(pd.DataFrame({"N°": o["obs_id"], "Indicador": o["indicador"], "Observación": o["observacion"],
                                "Acción": o["accion"], "Responsable": o["responsable"], "Plazo": o["plazo"],
                                "Estado": o["estado"]}), widths=(9, 16, 48, 48, 24, 18, 19), size=7.2)

    # 8. Supuestos y calidad de datos -------------------------------------------
    pdf.h1("8. Supuestos, metodología y calidad de datos")
    pdf.bullets([
        "LCR = ALAC / NAF. ALAC aplica descuentos por nivel y topes de composición (Nivel 2 ≤ 40%, Nivel 2B ≤ 15%); "
        "NAF = salidas − min(entradas; 75% de las salidas) (BCBS 238).",
        "NSFR = FED / FER con factores por vencimiento residual (< 6 meses, 6-12 meses, ≥ 1 año) (BCBS 295).",
        "Los datos son cierres diarios sin movimiento intradía; las variaciones comparan cierres de período.",
        "Pruebas de tensión: shocks porcentuales sobre montos computables por categoría; el indicador se recalcula "
        "con las mismas ponderaciones y topes del cálculo oficial.",
        "Proyecciones: Holt amortiguado (φ = 0,90) sobre montos históricos; intervalo ±1,96σ de errores a un paso.",
        "Cifras en millones de pesos (MM$). Límite regulatorio distinto de alertas y apetito internos.",
    ], 8.2)
    q = calidad
    pdf.table(pd.DataFrame({"Indicador": q["Indicador"], "Control": q["Control"], "Resultado": q["Resultado"],
                            "Detalle": q["Detalle"].str.slice(0, 60)}), widths=(17, 55, 22, 88), size=7)
    out = pdf.output()
    return bytes(out)
