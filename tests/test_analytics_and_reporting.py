"""
tests/test_analytics_and_reporting.py
======================================
Pruebas unitarias para:
- Análisis de descomposición diaria (Cierre de Hoy vs Cierre de Ayer) y vistas periódicas (Semanal, Mensual, Anual).
- Identificación de Top Drivers (factores explicativos).
- Generación de KPIs ejecutivos con etiquetas EOD institucionales.
- Atribución matemática Waterfall de variaciones.
- Generación de informe PDF consolidado para el Comité ALCO.
"""

import unittest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from analytics import (
    get_kpis_indicador,
    analyze_decomposition,
    compute_waterfall_attribution,
    calculate_multithreshold_buffers,
    calculate_alert_statistics
)
import pdf_generator


class AnalyticsAndReportingTests(unittest.TestCase):

    def setUp(self):
        # Serie histórica sintética de 60 días
        fechas = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(60)]
        self.df_hist = pd.DataFrame({
            "fecha": fechas,
            "lcr": [120.0 + (i * 0.1) for i in range(60)],
            "alac": [1200000.0 + (i * 1000.0) for i in range(60)],
            "naf": [1000000.0 + (i * 500.0) for i in range(60)]
        })

        # Detalle de balance con 2 días consecutivos: Ayer (t-1) y Hoy (t)
        fecha_ayer = fechas[-2]
        fecha_hoy = fechas[-1]

        rows = [
            # Ayer
            {"fecha": fecha_ayer, "componente": "ALAC", "categoria": "Nivel 1", "subcategoria": "Billetes y Monedas", "monto_nominal": 100000.0, "factor_ponderacion": 1.0, "monto_ponderado": 100000.0},
            {"fecha": fecha_ayer, "componente": "ALAC", "categoria": "Nivel 1", "subcategoria": "Depósitos en Banco Central", "monto_nominal": 500000.0, "factor_ponderacion": 1.0, "monto_ponderado": 500000.0},
            {"fecha": fecha_ayer, "componente": "ALAC", "categoria": "Nivel 2A", "subcategoria": "Bonos Soberanos Extranjeros", "monto_nominal": 200000.0, "factor_ponderacion": 0.85, "monto_ponderado": 170000.0},
            {"fecha": fecha_ayer, "componente": "NAF", "categoria": "Minorista", "subcategoria": "Depósitos a la Vista Personas", "monto_nominal": 400000.0, "factor_ponderacion": 0.05, "monto_ponderado": 20000.0},
            # Hoy: Banco Central sube +50k, Soberanos cae -20k
            {"fecha": fecha_hoy, "componente": "ALAC", "categoria": "Nivel 1", "subcategoria": "Billetes y Monedas", "monto_nominal": 100000.0, "factor_ponderacion": 1.0, "monto_ponderado": 100000.0},
            {"fecha": fecha_hoy, "componente": "ALAC", "categoria": "Nivel 1", "subcategoria": "Depósitos en Banco Central", "monto_nominal": 550000.0, "factor_ponderacion": 1.0, "monto_ponderado": 550000.0},
            {"fecha": fecha_hoy, "componente": "ALAC", "categoria": "Nivel 2A", "subcategoria": "Bonos Soberanos Extranjeros", "monto_nominal": 180000.0, "factor_ponderacion": 0.85, "monto_ponderado": 153000.0},
            {"fecha": fecha_hoy, "componente": "NAF", "categoria": "Minorista", "subcategoria": "Depósitos a la Vista Personas", "monto_nominal": 420000.0, "factor_ponderacion": 0.05, "monto_ponderado": 21000.0},
        ]
        self.df_det = pd.DataFrame(rows)

    def test_kpis_indicador_daily_eod_labels(self):
        """Verifica que en vista diaria, los KPIs reporten estrictamente Cierre de Hoy vs Cierre de Ayer."""
        kpis = get_kpis_indicador(
            df_hist=self.df_hist,
            ratio_col="lcr",
            num_col="alac",
            den_col="naf",
            limite_normativo=100.0,
            limite_interno=120.0,
            apetito_riesgo=150.0,
            granularidad="Diaria"
        )
        self.assertEqual(kpis["periodo_actual_label"], "Cierre de Hoy")
        self.assertEqual(kpis["periodo_anterior_label"], "Cierre de Ayer")
        self.assertEqual(kpis["vs_label"], "vs ayer")
        self.assertAlmostEqual(kpis["delta_num"], 1000.0, places=1)
        self.assertAlmostEqual(kpis["delta_den"], 500.0, places=1)
        self.assertGreater(kpis["ratio_actual"], 100.0)

    def test_kpis_indicador_weekly_and_monthly_labels(self):
        """Verifica que en vistas agrupadas, las etiquetas reflejen la consolidación de ciclo."""
        kpis_sem = get_kpis_indicador(self.df_hist, "lcr", "alac", "naf", 100.0, 120.0, 150.0, granularidad="Semanal")
        self.assertEqual(kpis_sem["periodo_actual_label"], "Cierre Semana Actual")
        self.assertEqual(kpis_sem["periodo_anterior_label"], "Cierre Semana Anterior")
        self.assertEqual(kpis_sem["vs_label"], "vs semana ant.")

        kpis_mes = get_kpis_indicador(self.df_hist, "lcr", "alac", "naf", 100.0, 120.0, 150.0, granularidad="Mensual")
        self.assertEqual(kpis_mes["periodo_actual_label"], "Cierre Mes Actual")
        self.assertEqual(kpis_mes["periodo_anterior_label"], "Cierre Mes Anterior")
        self.assertEqual(kpis_mes["vs_label"], "vs mes ant.")

    def test_analyze_decomposition_daily_hoy_vs_ayer(self):
        """Verifica la descomposición diaria y cálculo de deltas Hoy vs Ayer en cuentas y partidas."""
        df_cat, df_det_res, metadata = analyze_decomposition(
            df_detalle=self.df_det,
            componente_filtro="ALAC",
            granularidad="Diaria"
        )

        self.assertEqual(metadata["granularidad"], "Diaria")
        self.assertEqual(metadata["periodo_act_lbl"], "Cierre de Hoy")
        self.assertEqual(metadata["periodo_ant_lbl"], "Cierre de Ayer")
        self.assertEqual(metadata["comp_label"], "vs ayer")

        # Verificar depósitos en Banco Central: +50k
        bc_row = df_det_res[df_det_res['subcategoria'] == "Depósitos en Banco Central"].iloc[0]
        self.assertEqual(bc_row['monto_ponderado'], 550000.0)
        self.assertEqual(bc_row['monto_ponderado_ant'], 500000.0)
        self.assertEqual(bc_row['delta_ponderado'], 50000.0)
        self.assertAlmostEqual(bc_row['delta_pct'], 10.0, places=2)

        # Verificar Bonos Soberanos: 153k vs 170k (-17k)
        sob_row = df_det_res[df_det_res['subcategoria'] == "Bonos Soberanos Extranjeros"].iloc[0]
        self.assertEqual(sob_row['monto_ponderado'], 153000.0)
        self.assertEqual(sob_row['monto_ponderado_ant'], 170000.0)
        self.assertEqual(sob_row['delta_ponderado'], -17000.0)
        self.assertAlmostEqual(sob_row['delta_pct'], -10.0, places=2)

        # Top Drivers: alza principal debe ser Banco Central
        top_alzas = metadata["top_alzas"]
        self.assertEqual(len(top_alzas), 1)
        self.assertEqual(top_alzas.iloc[0]['subcategoria'], "Depósitos en Banco Central")

        # Top Drivers: baja principal debe ser Soberanos
        top_bajas = metadata["top_bajas"]
        self.assertEqual(len(top_bajas), 1)
        self.assertEqual(top_bajas.iloc[0]['subcategoria'], "Bonos Soberanos Extranjeros")

    def test_waterfall_attribution_invariant(self):
        """Verifica que la atribución Waterfall sume exactamente el cambio total del ratio."""
        res = compute_waterfall_attribution(num_0=1200000.0, den_0=1000000.0, num_1=1300000.0, den_1=1050000.0)
        sum_effects = res["efecto_num"] + res["efecto_den"] + res["efecto_interaccion"]
        self.assertAlmostEqual(res["delta_ratio"], sum_effects, places=4)

    def test_multithreshold_buffers(self):
        """Verifica el cálculo de distancias y holguras monetarias frente a 100%, 120%, 150%."""
        df_buf = calculate_multithreshold_buffers(
            ratio_val=130.0,
            num_val=1300000.0,
            den_val=1000000.0,
            thresholds=[150.0, 120.0, 100.0],
            labels=["Apetito de Riesgo", "Alerta Interna", "Mínimo CMF"]
        )
        self.assertEqual(len(df_buf), 3)
        cmf_row = df_buf[df_buf["Umbral (%)"] == "100%"].iloc[0]
        self.assertEqual(cmf_row["Distancia (pp)"], 30.0)
        self.assertEqual(cmf_row["Holgura Monetaria (MM$)"], 300000.0)
        self.assertEqual(cmf_row["Estado"], "CUMPLE")

    def test_pdf_generation_format_and_integrity(self):
        """Verifica que el informe ejecutivo en PDF se genere correctamente con cabecera %PDF."""
        kpis_dummy = {
            "ratio_actual": 126.2, "ratio_anterior": 125.0, "delta_ratio": 1.2,
            "num_actual": 1262000.0, "den_actual": 1000000.0, "holgura_monto": 262000.0
        }
        df_buf = calculate_multithreshold_buffers(126.2, 1262000.0, 1000000.0, [150.0, 120.0, 100.0], ["Apetito", "Alerta", "Mínimo CMF"])
        pdf_bytes = pdf_generator.generate_executive_pdf_report(
            kpis_lcr=kpis_dummy,
            kpis_nsfr=kpis_dummy,
            df_buffers_lcr=df_buf,
            df_buffers_nsfr=df_buf,
            top_drivers_lcr=[],
            top_drivers_nsfr=[],
            stress_results={"scenarios": [{"nombre": "Base", "lcr": 126.2, "delta_lcr": 0.0, "nsfr": 115.0, "delta_nsfr": 0.0, "diag": "Normal"}]},
            projection_summary={"horizonte_dias": 30, "metodo": "Holt", "lcr_pred": 127.0, "lcr_lower": 120.0, "lcr_upper": 134.0, "nsfr_pred": 115.0, "nsfr_lower": 110.0, "nsfr_upper": 120.0},
            fecha_corte_str="14-10-2026",
            periodicidad="Diaria",
            df_lcr_hist=self.df_hist,
            df_nsfr_hist=self.df_hist.rename(columns={"lcr": "nsfr", "alac": "fed", "naf": "fer"})
        )
        self.assertIsInstance(pdf_bytes, bytes)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertGreater(len(pdf_bytes), 15000)


if __name__ == "__main__":
    unittest.main()
