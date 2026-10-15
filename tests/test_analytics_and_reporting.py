"""
Pruebas del motor analítico del panel y del informe PDF.
Usan un conjunto sintético pequeño y controlado (sin base de datos) para verificar
las reglas de negocio: cierres por período, topes en pruebas de tensión, puente
con residual, holguras, alertas, calidad de datos y generación del PDF.
Ejecutar:  .venv/bin/python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analytics as an  # noqa: E402


def _lcr_dataset(days: int = 400, l2b: float = 20.0, inflows: float = 300.0):
    """Historia diaria LCR con 4 líneas (L1, L2B, salida, entrada), coherente detalle = total."""
    fechas = pd.date_range("2025-01-01", periods=days, freq="D")
    hist, det = [], []
    for i, f in enumerate(fechas):
        l1 = 1000.0 + i
        out = 800.0 + 0.5 * i
        lines = [("ALAC", "Nivel 1: Efectivo y BCCh", "Reserva BCCh", l1, 1.0, l1),
                 ("ALAC", "Nivel 2B: Activos Menor Liquidez", "Acciones", l2b * 2, 0.5, l2b),
                 ("NAF", "Depósitos Mayoristas No Operacionales", "DP institucional", out, 1.0, out),
                 ("NAF", "Entradas de Efectivo (Inflows con Cap 75%)", "Cobros cartera", -inflows, 1.0, -inflows)]
        import regulatory_engine as reg
        r = reg.compute_lcr(l1, 0, l2b, out, inflows)
        for comp, cat, sub, nom, fac, w in lines:
            det.append({"fecha": f, "componente": comp, "categoria": cat, "subcategoria": sub,
                        "monto_nominal": nom, "factor_ponderacion": fac, "monto_ponderado": w, "fuente": "TEST"})
        if r.adjustment_15pct_cap > 0:
            det.append({"fecha": f, "componente": "ALAC", "categoria": "Ajuste Tope 15% Nivel 2B", "subcategoria": "Exceso",
                        "monto_nominal": -float(r.adjustment_15pct_cap), "factor_ponderacion": 1.0,
                        "monto_ponderado": -float(r.adjustment_15pct_cap), "fuente": "TEST"})
        if r.inflow_cap_adjustment > 0:
            det.append({"fecha": f, "componente": "NAF", "categoria": "Entradas de Efectivo (Inflows con Cap 75%)",
                        "subcategoria": "Ajuste tope 75%", "monto_nominal": float(r.inflow_cap_adjustment),
                        "factor_ponderacion": 1.0, "monto_ponderado": float(r.inflow_cap_adjustment), "fuente": "TEST"})
        hist.append({"fecha": f, "lcr": float(r.lcr_pct), "alac": float(r.hqla), "naf": float(r.net_cash_outflows)})
    return pd.DataFrame(hist), pd.DataFrame(det)


class AnalyticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hist, cls.det = _lcr_dataset()
        cls.u = an.umbrales_lcr()

    def test_period_closes_use_last_daily_close(self):
        m = an.period_closes(self.hist, an.LCR, "Mensual")
        jan = m[m["fecha"].dt.month == 1].iloc[0]
        self.assertEqual(jan["fecha"], pd.Timestamp("2025-01-31"))
        self.assertTrue(bool(m["en_curso"].iloc[-1]))  # último mes no cerrado
        d = an.period_closes(self.hist, an.LCR, "Diaria")
        self.assertAlmostEqual(d["d_num"].iloc[-1], 1.0, places=6)  # ALAC sube 1 por día

    def test_status_distinguishes_regulatory_and_internal(self):
        self.assertEqual(an.estado(99.0, self.u)["codigo"], "INCUMPLIMIENTO")
        self.assertEqual(an.estado(110.0, self.u)["codigo"], "ALERTA")
        self.assertEqual(an.estado(130.0, self.u)["codigo"], "BAJO APETITO")
        self.assertEqual(an.estado(160.0, self.u)["codigo"], "DENTRO DE APETITO")
        self.assertEqual(an.estado(104.0, an.umbrales_nsfr())["codigo"], "ALERTA")

    def test_recalculation_matches_published_ratio(self):
        last = self.hist["fecha"].max()
        r = an.ratio_from_lines(an.LCR, an.lines_at(self.det, last))
        self.assertAlmostEqual(r["ratio"], self.hist["lcr"].iloc[-1], places=3)

    def test_stress_reapplies_caps(self):
        hist, det = _lcr_dataset(days=5, l2b=400.0, inflows=900.0)  # L2B y entradas sobre sus topes
        lines = an.lines_at(det, hist["fecha"].max())
        base = an.ratio_from_lines(an.LCR, lines)
        self.assertGreater(base["detalle"].adjustment_15pct_cap, 0)
        self.assertGreater(base["detalle"].inflow_cap_adjustment, 0)
        shocks = pd.Series(0.0, index=lines.index)
        shocks[lines["tipo"] == "L2B"] = -50.0  # con tope activo, perder L2B casi no mueve el ratio
        res = an.run_stress(an.LCR, lines, shocks)
        self.assertLess(abs(res["delta_pp"]), 15.0)
        self.assertAlmostEqual(res["delta_pp"], res["lineas"]["impacto_individual_pp"].sum() + res["interaccion_pp"], 9)

    def test_scenarios_are_ordered(self):
        lines = an.lines_at(self.det, self.hist["fecha"].max())
        r = {e: an.run_stress(an.LCR, lines, an.scenario_shocks(an.LCR, lines, e))["estresado"]["ratio"]
             for e in ("Base", "Adverso", "Severo")}
        self.assertGreater(r["Base"], r["Adverso"])
        self.assertGreater(r["Adverso"], r["Severo"])

    def test_waterfall_reconciles_with_residual(self):
        a, b = self.hist["fecha"].iloc[-31], self.hist["fecha"].iloc[-1]
        wf = an.waterfall_by_category(self.det, an.LCR, a, b)
        self.assertAlmostEqual(wf["contribucion_pp"].sum(), wf.attrs["ratio_b"] - wf.attrs["ratio_a"], places=9)

    def test_buffers_and_required_amounts(self):
        buf = an.buffers(an.LCR, 110.0, 1100.0, 1000.0, self.u)
        apetito = buf[buf["Tipo"] == "Interno: apetito"].iloc[0]
        self.assertAlmostEqual(apetito["ALAC adicional requerido (MM$)"], 400.0, places=6)
        self.assertAlmostEqual(apetito["Reducción de NAF requerida (MM$)"], 1000 - 1100 / 1.5, places=6)
        adj = an.required_adjustment(150.0, 1100.0, 1000.0)
        self.assertAlmostEqual(adj["delta_num"], 400.0, places=6)

    def test_alert_statistics_streaks(self):
        h = pd.DataFrame({"fecha": pd.date_range("2025-01-01", periods=6), "lcr": [130, 115, 118, 125, 119, 117],
                          "alac": 1.0, "naf": 1.0})
        s = an.alert_statistics(h, an.LCR, self.u).set_index("Tipo")
        self.assertEqual(s.loc["Interno: alerta", "Días consecutivos actuales bajo el umbral"], 2)
        self.assertEqual(s.loc["Interno: alerta", "Racha máxima (días)"], 2)
        self.assertEqual(s.loc["Interno: alerta", "Días totales bajo el umbral"], 4)

    def test_forecast_and_backtest(self):
        f = an.forecast(self.hist.set_index("fecha")["lcr"], 30, "holt")
        self.assertEqual(len(f), 30)
        self.assertTrue((f["upper"] >= f["lower"]).all())
        metrics, det = an.backtest(self.hist.set_index("fecha")["lcr"], horizon=10, n_origins=5)
        self.assertEqual(set(metrics["metodo"]), set(an.METHODS))
        self.assertTrue((metrics["RMSE (pp)"].diff().dropna() >= 0).all())  # ordenado por RMSE

    def test_threshold_crossing_labels(self):
        proj = pd.DataFrame({"fecha": pd.date_range("2025-01-01", periods=3), "pred": [125.0, 119.0, 118.0]})
        self.assertEqual(an.threshold_crossing(proj, 130.0, 120.0), "02-01-2025")
        self.assertEqual(an.threshold_crossing(proj, 110.0, 120.0), "Ya bajo el umbral")
        self.assertEqual(an.threshold_crossing(proj, 130.0, 100.0), "No cruza")

    def test_data_quality_detects_broken_detail(self):
        ok = an.data_quality(self.hist, self.det, an.LCR)
        self.assertFalse((ok["Resultado"] == "ERROR").any())
        broken = self.det.copy()
        broken.loc[broken.index[-1], "monto_ponderado"] += 50.0
        bad = an.data_quality(self.hist, broken, an.LCR).set_index("Control")
        self.assertEqual(bad.loc["Detalle cuadra con totales", "Resultado"], "ERROR")

    def test_concentration_hhi(self):
        cc = an.concentration(self.det, "ALAC", self.hist["fecha"].max())
        shares = cc["tabla"]["participacion"]
        self.assertAlmostEqual(cc["hhi"], float((shares ** 2).sum()), places=6)
        self.assertAlmostEqual(shares.sum(), 100.0, places=6)


class PdfTests(unittest.TestCase):
    def test_pdf_is_generated(self):
        import pdf_generator
        hist, det = _lcr_dataset()
        nh = hist.rename(columns={"lcr": "nsfr", "alac": "fed", "naf": "fer"})
        nd = det.copy()
        nd["componente"] = np.where(nd["componente"] == "ALAC", "FED", "FER")
        nd = nd[~nd["categoria"].str.startswith("Ajuste") & ~nd["subcategoria"].str.startswith("Ajuste")]
        nd["monto_ponderado"] = nd["monto_ponderado"].abs()
        sums = nd.groupby(["fecha", "componente"])["monto_ponderado"].sum().unstack()
        nh = pd.DataFrame({"fecha": sums.index, "fed": sums["FED"].values, "fer": sums["FER"].values})
        nh["nsfr"] = nh["fed"] / nh["fer"] * 100
        q = pd.DataFrame([{"Indicador": "LCR", "Control": "x", "Resultado": "OK", "Detalle": "ok"}])
        pdf = pdf_generator.build_report(hist, det, nh, nd, hist["fecha"].max(), "Mensual", 6, ["Adverso", "Severo"], 30,
                                         an.umbrales_lcr(), an.umbrales_nsfr(), pd.DataFrame(), "", q, "test")
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 20_000)


if __name__ == "__main__":
    unittest.main()
