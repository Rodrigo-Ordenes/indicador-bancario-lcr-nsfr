"""
pdf_generator.py
================
Generador de informes ejecutivos en formato PDF para el Comité ALCO, Gerencia de
Finanzas, Riesgo Financiero y la Alta Administración.

Cumple con el Requerimiento Funcional 4 y entrega:
- Vista consolidada de LCR y NSFR.
- Indicadores actuales, umbrales y holguras monetarias.
- Gráficos consolidados de evolución temporal e impacto de estrés ALM.
- Principales variaciones del período y factores explicativos.
- Resultados de proyecciones y escenarios de estrés (Base, Adverso, Severo).
- Alertas, conclusiones y acciones de mitigación recomendadas.
- Metadatos formales: Fecha de corte, fecha de emisión, emisor y supuestos.
"""

import io
from fpdf import FPDF
from datetime import datetime, date
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional


class ALCOReportPDF(FPDF):
    def __init__(self, fecha_corte_str: str, periodicidad: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.fecha_corte_str = fecha_corte_str
        self.periodicidad = periodicidad
        self.set_auto_page_break(auto=True, margin=15)

    def header(self):
        # Franja superior Bloomberg Dark
        self.set_fill_color(0, 0, 0)
        self.rect(0, 0, 210, 18, "F")
        self.set_fill_color(1, 104, 255)  # Azul Bloomberg
        self.rect(0, 18, 210, 1.5, "F")

        self.set_font("Helvetica", "B", 10)
        self.set_text_color(255, 255, 255)
        self.set_xy(10, 4)
        self.cell(100, 5, "GERENCIA DE RIESGO FINANCIERO & ALM | BANCO")

        self.set_font("Helvetica", "", 8)
        self.set_text_color(160, 160, 160)
        self.set_xy(110, 4)
        self.cell(90, 5, f"CORTE: {self.fecha_corte_str} | FRECUENCIA: {self.periodicidad.upper()}", align="R")

        self.set_font("Helvetica", "I", 7)
        self.set_text_color(74, 246, 196)  # Menta Bloomberg
        self.set_xy(10, 10)
        self.cell(190, 4, "INFORME EJECUTIVO DE SUPERVISION DE LIQUIDEZ Y FONDEO (BASILEA III / CMF)")
        self.ln(12)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(120, 120, 120)
        self.cell(100, 4, "USO INTERNO EXCLUSIVO - COMITE ALCO & ALTA ADMINISTRACION")
        self.cell(90, 4, f"Página {self.page_no()}", align="R")


def _render_consolidated_charts_image(
    df_lcr: Optional[pd.DataFrame],
    df_nsfr: Optional[pd.DataFrame],
    stress_results: Dict[str, Any]
) -> Optional[io.BytesIO]:
    """Genera un panel de dos gráficos institucionales de alta resolución para el PDF."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 3.8), dpi=220)

        # 1. Gráfico de Evolución LCR y NSFR vs Umbrales
        has_ts = False
        if df_lcr is not None and not df_lcr.empty and 'fecha' in df_lcr.columns and 'lcr' in df_lcr.columns:
            lcr_plot = df_lcr.tail(60).copy()
            ax1.plot(pd.to_datetime(lcr_plot['fecha']), lcr_plot['lcr'], label='LCR (30 Días)', color='#0168ff', lw=2.2)
            has_ts = True

        if df_nsfr is not None and not df_nsfr.empty and 'fecha' in df_nsfr.columns and 'nsfr' in df_nsfr.columns:
            nsfr_plot = df_nsfr.tail(60).copy()
            ax1.plot(pd.to_datetime(nsfr_plot['fecha']), nsfr_plot['nsfr'], label='NSFR (> 1 Año)', color='#00b894', lw=2.2)
            has_ts = True

        ax1.axhline(100.0, color='#fe443d', linestyle='--', lw=1.5, label='Límite Mínimo CMF (100%)')
        ax1.axhline(120.0, color='#d39e00', linestyle=':', lw=1.2, label='Alerta Interna (120%)')
        ax1.axhline(150.0, color='#e67e22', linestyle='-.', lw=1.2, label='Apetito Riesgo (150%)')

        ax1.set_title("Evolución Consolidada de Ratios vs Umbrales (%)", fontsize=10, fontweight='bold', pad=8)
        ax1.set_ylabel("Ratio (%)", fontsize=8.5)
        ax1.grid(True, linestyle='--', alpha=0.5, color='#d0d0d0')
        ax1.legend(loc='lower left', fontsize=7.2, framealpha=0.85)

        if has_ts:
            ax1.xaxis.set_major_formatter(mdates.DateFormatter('%d-%b'))
            fig.autofmt_xdate(rotation=25, ha='right')

        # 2. Gráfico de Escenarios de Estrés ALM
        scenarios = stress_results.get("scenarios", [])
        if scenarios:
            names = [s.get("nombre", f"Esc {i+1}") for i, s in enumerate(scenarios)]
            lcr_vals = [s.get("lcr", 0.0) for s in scenarios]
            nsfr_vals = [s.get("nsfr", 0.0) for s in scenarios]

            x = np.arange(len(names))
            width = 0.35

            rects1 = ax2.bar(x - width/2, lcr_vals, width, label='LCR Estresado', color='#0168ff', alpha=0.88)
            rects2 = ax2.bar(x + width/2, nsfr_vals, width, label='NSFR Estresado', color='#00b894', alpha=0.88)

            ax2.axhline(100.0, color='#fe443d', linestyle='--', lw=1.5, label='Límite 100%')
            ax2.set_title("Sensibilidad y Estrés ALM: Ratios por Escenario (%)", fontsize=10, fontweight='bold', pad=8)
            ax2.set_ylabel("Ratio (%)", fontsize=8.5)
            ax2.set_xticks(x)
            ax2.set_xticklabels(names, fontsize=8)
            ax2.grid(True, linestyle='--', alpha=0.5, color='#d0d0d0', axis='y')
            ax2.legend(loc='upper right', fontsize=7.5, framealpha=0.85)

            for r in rects1:
                h = r.get_height()
                ax2.annotate(f'{h:.1f}%', xy=(r.get_x() + r.get_width() / 2, h), xytext=(0, 2),
                             textcoords="offset points", ha='center', va='bottom', fontsize=7.0, fontweight='bold')
            for r in rects2:
                h = r.get_height()
                ax2.annotate(f'{h:.1f}%', xy=(r.get_x() + r.get_width() / 2, h), xytext=(0, 2),
                             textcoords="offset points", ha='center', va='bottom', fontsize=7.0, fontweight='bold')
        else:
            ax2.text(0.5, 0.5, "Sin datos de estrés", ha='center', va='center')

        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', dpi=200)
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"Error generando gráficos para PDF: {e}")
        return None


def generate_executive_pdf_report(
    kpis_lcr: Dict[str, Any],
    kpis_nsfr: Dict[str, Any],
    df_buffers_lcr: pd.DataFrame,
    df_buffers_nsfr: pd.DataFrame,
    top_drivers_lcr: List[Dict[str, Any]],
    top_drivers_nsfr: List[Dict[str, Any]],
    stress_results: Dict[str, Any],
    projection_summary: Dict[str, Any],
    observaciones_texto: str = "",
    acciones_mitigacion: str = "",
    fecha_corte_str: str = "",
    periodicidad: str = "Mensual",
    df_lcr_hist: Optional[pd.DataFrame] = None,
    df_nsfr_hist: Optional[pd.DataFrame] = None,
    *args,
    **kwargs
) -> bytes:
    """
    Construye el documento PDF consolidado con tablas regulatorias y gráficos integrados.
    """
    pdf = ALCOReportPDF(fecha_corte_str=fecha_corte_str, periodicidad=periodicidad)
    pdf.add_page()
    pdf.set_text_color(30, 30, 30)

    # 1. TÍTULO Y METADATOS
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_text_color(1, 104, 255)
    pdf.cell(0, 8, "INFORME CONSOLIDADO DE LIQUIDEZ Y ESTRUCTURA DE BALANCE", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(0, 5, f"Fecha de Emisión: {datetime.today().strftime('%d-%m-%Y %H:%M')} | Supervisor Normativo: CMF Chile (Basilea III)", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # 2. TABLA CONSOLIDADA LCR & NSFR
    pdf.set_fill_color(240, 242, 245)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 6, "1. INDICADORES PRINCIPALES DE LIQUIDEZ Y FONDEO", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # Encabezados de tabla según periodicidad
    if periodicidad == "Diaria":
        col_act_lbl = "Cierre Hoy (%)"
        col_ant_lbl = "Cierre Ayer (%)"
        col_delta_lbl = "Delta Hoy/Ayer"
    elif periodicidad == "Semanal":
        col_act_lbl = "Semana Act (%)"
        col_ant_lbl = "Semana Ant (%)"
        col_delta_lbl = "Delta Semanal"
    elif periodicidad == "Mensual":
        col_act_lbl = "Mes Act (%)"
        col_ant_lbl = "Mes Ant (%)"
        col_delta_lbl = "Delta Mensual"
    else:
        col_act_lbl = "Año Act (%)"
        col_ant_lbl = "Año Ant (%)"
        col_delta_lbl = "Delta Anual"

    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(225, 230, 238)
    cols = [("Indicador", 28), (col_act_lbl, 24), (col_ant_lbl, 24), (col_delta_lbl, 22), 
            ("Numerador", 30), ("Denominador", 30), ("Holgura (100%)", 32)]
    for title, width in cols:
        pdf.cell(width, 6, title, border=1, fill=True, align="C")
    pdf.ln()

    # Filas de datos
    pdf.set_font("Helvetica", "", 8)
    for ind, kpis, num_name, den_name in [
        ("LCR (30 Días)", kpis_lcr, "ALAC", "NAF"),
        ("NSFR (> 1 Año)", kpis_nsfr, "FED", "FER")
    ]:
        if not kpis:
            continue
        pdf.cell(28, 6, ind, border=1)
        pdf.cell(24, 6, f"{kpis['ratio_actual']:.2f}%", border=1, align="R")
        pdf.cell(24, 6, f"{kpis['ratio_anterior']:.2f}%", border=1, align="R")
        delta_sign = "+" if kpis['delta_ratio'] >= 0 else ""
        pdf.cell(22, 6, f"{delta_sign}{kpis['delta_ratio']:.2f} pp", border=1, align="R")
        pdf.cell(30, 6, f"${kpis['num_actual']:,.0f} MM", border=1, align="R")
        pdf.cell(30, 6, f"${kpis['den_actual']:,.0f} MM", border=1, align="R")
        pdf.cell(32, 6, f"${kpis['holgura_monto']:,.0f} MM", border=1, align="R")
        pdf.ln()

    pdf.ln(3)

    # 3. TABLA DE HOLGURAS FRENTE A UMBRALES REGULATORIOS Y APETITO
    pdf.set_fill_color(240, 242, 245)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, "2. DISTANCIA Y HOLGURA FRENTE A UMBRALES DE RIESGO", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(225, 230, 238)
    cols_buf = [("Indicador", 22), ("Umbral", 20), ("Nivel", 42), ("Distancia", 26), ("Holgura Activo (MM$)", 40), ("Estado", 40)]
    for title, width in cols_buf:
        pdf.cell(width, 6, title, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 8)
    if not df_buffers_lcr.empty:
        for _, r in df_buffers_lcr.iterrows():
            pdf.cell(22, 5, "LCR", border=1)
            pdf.cell(20, 5, str(r["Umbral (%)"]), border=1, align="C")
            pdf.cell(42, 5, str(r["Nivel"])[:28], border=1)
            dist_val = float(r["Distancia (pp)"])
            pdf.cell(26, 5, f"{dist_val:+.2f} pp", border=1, align="R")
            pdf.cell(40, 5, f"${float(r['Holgura Monetaria (MM$)']):,.0f} MM", border=1, align="R")
            pdf.cell(40, 5, str(r["Estado"]), border=1, align="C")
            pdf.ln()

    if not df_buffers_nsfr.empty:
        for _, r in df_buffers_nsfr.iterrows():
            pdf.cell(22, 5, "NSFR", border=1)
            pdf.cell(20, 5, str(r["Umbral (%)"]), border=1, align="C")
            pdf.cell(42, 5, str(r["Nivel"])[:28], border=1)
            dist_val = float(r["Distancia (pp)"])
            pdf.cell(26, 5, f"{dist_val:+.2f} pp", border=1, align="R")
            pdf.cell(40, 5, f"${float(r['Holgura Monetaria (MM$)']):,.0f} MM", border=1, align="R")
            pdf.cell(40, 5, str(r["Estado"]), border=1, align="C")
            pdf.ln()

    pdf.ln(3)

    # 4. GRÁFICOS CONSOLIDADOS (EVOLUCIÓN Y SENSIBILIDAD ALM)
    pdf.set_fill_color(240, 242, 245)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, "3. GRAFICOS CONSOLIDADOS: EVOLUCION HISTORICA Y SENSIBILIDAD ALM", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    chart_img = _render_consolidated_charts_image(df_lcr_hist, df_nsfr_hist, stress_results)
    if chart_img is not None:
        pdf.image(chart_img, x=10, w=190)
        pdf.ln(3)

    # 5. PRUEBAS DE TENSIÓN Y ESCENARIOS DE ESTRÉS
    pdf.set_fill_color(240, 242, 245)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, "4. RESULTADOS DE PRUEBAS DE TENSION Y ESCENARIOS ALM", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(225, 230, 238)
    cols_st = [("Escenario", 32), ("LCR Estresado", 30), ("Impacto LCR", 26), 
               ("NSFR Estresado", 30), ("Impacto NSFR", 26), ("Diagnóstico ALM", 46)]
    for title, width in cols_st:
        pdf.cell(width, 6, title, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 8)
    for row in stress_results.get("scenarios", []):
        pdf.cell(32, 5, row.get("nombre", ""), border=1)
        pdf.cell(30, 5, f"{row.get('lcr', 0.0):.2f}%", border=1, align="R")
        pdf.cell(26, 5, f"{row.get('delta_lcr', 0.0):+.2f} pp", border=1, align="R")
        pdf.cell(30, 5, f"{row.get('nsfr', 0.0):.2f}%", border=1, align="R")
        pdf.cell(26, 5, f"{row.get('delta_nsfr', 0.0):+.2f} pp", border=1, align="R")
        pdf.cell(46, 5, row.get("diag", "")[:30], border=1)
        pdf.ln()

    pdf.ln(3)

    # 6. PROYECCIONES Y MODELOS
    pdf.set_fill_color(240, 242, 245)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, "5. PROYECCIONES DE LIQUIDEZ Y HORIZONTE ESTIMADO", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    pdf.set_font("Helvetica", "", 8)
    proj_text = (
        f"Horizonte evaluado: {projection_summary.get('horizonte_dias', 30)} dias. "
        f"Metodo principal: {projection_summary.get('metodo', 'Alisamiento Exponencial Doble (Holt)')}. "
        f"LCR proyectado central: {projection_summary.get('lcr_pred', 0.0):.2f}% "
        f"[Banda 95%: {projection_summary.get('lcr_lower', 0.0):.2f}% - {projection_summary.get('lcr_upper', 0.0):.2f}%]. "
        f"NSFR proyectado central: {projection_summary.get('nsfr_pred', 0.0):.2f}% "
        f"[Banda 95%: {projection_summary.get('nsfr_lower', 0.0):.2f}% - {projection_summary.get('nsfr_upper', 0.0):.2f}%]."
    )
    pdf.multi_cell(0, 4.5, proj_text)
    pdf.ln(3)

    # 7. OBSERVACIONES Y ACCIONES DE MITIGACIÓN (ALTA ADMINISTRACIÓN)
    pdf.set_fill_color(240, 242, 245)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, "6. CONCLUSIONES EJECUTIVAS Y PLAN DE ACCION DE MITIGACION", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(0, 4.5, "Diagnóstico y Alertas Accionables:", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    obs = observaciones_texto if observaciones_texto.strip() else (
        "La posicion de liquidez se mantiene dentro de los parametros normativos. "
        "Se recomienda monitorear la renovacion de depositos mayoristas y vigilar la brecha frente al apetito interno."
    )
    pdf.multi_cell(0, 4.2, obs)
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(0, 4.5, "Acciones de Mitigación Propuestas para Mesa de Dinero / ALCO:", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    act = acciones_mitigacion if acciones_mitigacion.strip() else (
        "1. Priorizar colocación de depósitos a plazo en tramos > 30 días para reducir salidas brutas de LCR.\n"
        "2. Mantener la cartera de soberanos Banco Central como colateral de máxima calidad (HQLA Nivel 1).\n"
        "3. Vigilar el cupo disponible en líneas de crédito comprometidas corporativas."
    )
    pdf.multi_cell(0, 4.2, act)

    return bytes(pdf.output())
