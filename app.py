"""
app.py
======
Panel interactivo de liquidez (LCR y NSFR) para Riesgo Financiero, Finanzas,
Tesorería y la alta administración.

Módulos (requerimientos funcionales):
  1. Panel LCR  : indicadores, evolución y comparación, descomposición, pruebas de tensión y proyecciones.
  2. Panel NSFR : misma estructura y navegación que el LCR.
  3. Análisis transversal: puente de variaciones, holguras, precisión de proyecciones,
     concentración y resumen ejecutivo con observaciones y acciones.
  4. Informe PDF consolidado con período y escenarios seleccionables.
  5. Calidad y trazabilidad de datos.
"""

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Panel de Liquidez · LCR / NSFR", page_icon="▪", layout="wide",
                   initial_sidebar_state="expanded")

import analytics as an  # noqa: E402
import pdf_generator  # noqa: E402
import report_store  # noqa: E402
from components import charts  # noqa: E402
from components.core_architecture_view import render_core_architecture_page  # noqa: E402
from components.ui_cards import TAG, kpi_row, note, section, status_strip  # noqa: E402
from data_loader import (get_data_source_label, load_lcr_detalle, load_lcr_historico,  # noqa: E402
                         load_nsfr_detalle, load_nsfr_historico)
from styles import BBG_BLUE, BBG_MINT, BBG_ORANGE, BBG_RED, CUSTOM_CSS  # noqa: E402

st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

MODULES = ["1 · Panel LCR", "2 · Panel NSFR", "3 · Análisis transversal", "4 · Informe PDF",
           "5 · Calidad y trazabilidad de datos"]
GRANS = ["Diaria", "Semanal", "Mensual", "Anual"]
SCENARIOS = ["Base", "Adverso", "Severo"]


def money(v) -> str:
    return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:,.0f}"


# =============================================================================
# DATOS Y CALIDAD (se valida antes de calcular cualquier indicador)
# =============================================================================
lcr_hist, lcr_det = load_lcr_historico(), load_lcr_detalle()
nsfr_hist, nsfr_det = load_nsfr_historico(), load_nsfr_detalle()


@st.cache_data(ttl=600, show_spinner=False)
def quality(_h, _d, code: str, stamp) -> pd.DataFrame:
    return an.data_quality(_h, _d, an.LCR if code == "LCR" else an.NSFR)


stamp = (lcr_hist["fecha"].max(), len(lcr_hist), len(lcr_det))
dq_lcr, dq_nsfr = quality(lcr_hist, lcr_det, "LCR", stamp), quality(nsfr_hist, nsfr_det, "NSFR", stamp)
dq_errors = pd.concat([dq_lcr.assign(Indicador="LCR"), dq_nsfr.assign(Indicador="NSFR")])
dq_errors = dq_errors[dq_errors["Resultado"] == "ERROR"]
dq_observed = int(((dq_lcr["Resultado"] == "OBSERVADO").sum() + (dq_nsfr["Resultado"] == "OBSERVADO").sum()))

# =============================================================================
# BARRA LATERAL
# =============================================================================
st.sidebar.markdown("""<div class="sidebar-brand"><div class="sidebar-brand-title">ALM · LIQUIDEZ</div>
<div class="sidebar-brand-sub">Reportería de Riesgo Financiero</div></div>""", unsafe_allow_html=True)
st.sidebar.markdown('<div class="sidebar-section">Módulo</div>', unsafe_allow_html=True)
modulo = st.sidebar.radio("Módulo", MODULES, label_visibility="collapsed")

st.sidebar.markdown('<div class="sidebar-section">Vista temporal</div>', unsafe_allow_html=True)
gran = st.sidebar.radio("Vista temporal", GRANS, horizontal=True, label_visibility="collapsed")
st.sidebar.caption({
    "Diaria": "Cierre de hoy y variación respecto del cierre de ayer.",
    "Semanal": "Último cierre de cada semana (viernes) vs la semana anterior.",
    "Mensual": "Último cierre de cada mes vs el mes anterior.",
    "Anual": "Último cierre de cada año vs el año anterior.",
}[gran] + " Los datos son cierres diarios: no existe movimiento intradía.")

st.sidebar.markdown('<div class="sidebar-section">Umbrales</div>', unsafe_allow_html=True)
with st.sidebar.expander("Límites regulatorios e internos", expanded=False):
    st.markdown("**Regulatorio (fijo):** LCR y NSFR ≥ 100%.")
    st.markdown("**Internos (política ALM):**")
    lcr_alerta = st.number_input("LCR · alerta interna (%)", value=120.0, step=1.0, min_value=100.0)
    lcr_apetito = st.number_input("LCR · apetito por riesgo (%)", value=150.0, step=1.0, min_value=lcr_alerta)
    nsfr_alerta = st.number_input("NSFR · alerta interna (%)", value=105.0, step=1.0, min_value=100.0)
U_LCR = an.umbrales_lcr(100.0, lcr_alerta, lcr_apetito)
U_NSFR = an.umbrales_nsfr(100.0, nsfr_alerta)

st.sidebar.markdown('<div class="sidebar-section">Fuente y calidad</div>', unsafe_allow_html=True)
dq_txt = ("Validación OK" if dq_errors.empty and dq_observed == 0 else
          (f"{len(dq_errors)} controles con ERROR" if not dq_errors.empty else f"{dq_observed} controles observados"))
dq_color = BBG_RED if not dq_errors.empty else (BBG_ORANGE if dq_observed else BBG_MINT)
st.sidebar.markdown(f'<div class="sidebar-note">{get_data_source_label()}<br>'
                    f'<span style="color:{dq_color}">■ Calidad de datos: {dq_txt}</span><br>'
                    f'Cifras en MM$ (millones de CLP).</div>', unsafe_allow_html=True)

# =============================================================================
# ENCABEZADO Y FRANJA DE SITUACIÓN (visible en todos los módulos)
# =============================================================================
fecha_corte = pd.to_datetime(lcr_hist["fecha"]).max()
st.markdown(f"""<div class="header-banner"><div>
  <div class="header-eyebrow">Gerencia de Riesgo Financiero · Comité ALCO</div>
  <div class="header-title">Panel de liquidez: LCR y NSFR</div>
  <div class="header-subtitle">Basilea III (BCBS 238 / 295) · Normativa de liquidez CMF</div></div>
  <div class="header-meta">Fecha de corte <b>{fecha_corte:%d-%m-%Y}</b><br>Vista <b>{gran}</b><br>
  Generado <b>{datetime.now():%d-%m-%Y %H:%M}</b></div></div>""", unsafe_allow_html=True)

if not dq_errors.empty:
    st.error("La validación de calidad de datos detectó errores; los indicadores no se calculan hasta corregirlos.")
    st.dataframe(dq_errors, hide_index=True, width="stretch")
    st.stop()

closes_d_lcr = an.period_closes(lcr_hist, an.LCR, "Diaria")
closes_d_nsfr = an.period_closes(nsfr_hist, an.NSFR, "Diaria")
kd_lcr = an.kpi_snapshot(closes_d_lcr, an.LCR, U_LCR, "Diaria")
kd_nsfr = an.kpi_snapshot(closes_d_nsfr, an.NSFR, U_NSFR, "Diaria")
status_strip([{**kd_lcr, "codigo": "LCR"}, {**kd_nsfr, "codigo": "NSFR"}])


# =============================================================================
# PANEL GENÉRICO LCR / NSFR (estructura idéntica para ambos indicadores)
# =============================================================================

def panel(ind: an.Indicador, hist: pd.DataFrame, det: pd.DataFrame, umbrales, req: int):
    closes = an.period_closes(hist, ind, gran)
    k = an.kpi_snapshot(closes, ind, umbrales, gran)
    t1, t2, t3, t4 = st.tabs([f"{req}.1–{req}.2 Indicadores y evolución", f"{req}.3 Descomposición",
                              f"{req}.4 Pruebas de tensión", f"{req}.5 Proyecciones"])

    # ---------------------------------------------------------------- 1.1 / 1.2
    with t1:
        section(f"Indicadores principales · {ind.nombre}", f"{k['periodo']} {k['ref_ant']}", "REAL")
        kpi_row(k, ind)
        umb_txt = " · ".join(f"{u.nombre}: {u.valor:.0f}% ({u.tipo.lower()})" for u in sorted(umbrales, key=lambda u: u.valor))
        note(f"<b>Umbrales.</b> {umb_txt}. El mínimo regulatorio es obligatorio; la alerta y el apetito son "
             "niveles internos de gestión.")

        section("Evolución", f"{len(closes)} cierres {gran.lower()}es disponibles", "REAL")
        fmin, fmax = closes["fecha"].min().date(), closes["fecha"].max().date()
        default_from = max(fmin, fmax - timedelta(days=365)) if gran == "Diaria" else fmin
        rng = st.slider("Rango visualizado", min_value=fmin, max_value=fmax, value=(default_from, fmax),
                        format="DD-MM-YYYY", key=f"rng_{ind.codigo}_{gran}")
        view = closes[(closes["fecha"].dt.date >= rng[0]) & (closes["fecha"].dt.date <= rng[1])]
        st.plotly_chart(charts.evolution_chart(view, ind, umbrales, gran), width="stretch")

        section("Variaciones entre cierres", "absolutas y porcentuales", "REAL")
        modo = st.radio("Tipo de variación", ["Absoluta", "Porcentual"], horizontal=True, key=f"var_{ind.codigo}")
        st.plotly_chart(charts.variation_chart(view, ind, modo), width="stretch")
        tbl = view.tail(12).iloc[::-1]
        st.dataframe(pd.DataFrame({
            "Período": tbl["periodo"] + np.where(tbl["en_curso"], " (en curso)", ""),
            f"{ind.codigo} (%)": tbl[ind.ratio_col], "Δ (pp)": tbl["d_ratio"], "Δ (%)": tbl["d_ratio_pct"],
            f"{ind.num} (MM$)": tbl[ind.num_col], f"Δ {ind.num} (MM$)": tbl["d_num"], f"Δ {ind.num} (%)": tbl["d_num_pct"],
            f"{ind.den} (MM$)": tbl[ind.den_col], f"Δ {ind.den} (MM$)": tbl["d_den"], f"Δ {ind.den} (%)": tbl["d_den_pct"],
        }).style.format({f"{ind.codigo} (%)": "{:.2f}", "Δ (pp)": "{:+.2f}", "Δ (%)": "{:+.2f}",
                         f"{ind.num} (MM$)": "{:,.0f}", f"Δ {ind.num} (MM$)": "{:+,.0f}", f"Δ {ind.num} (%)": "{:+.2f}",
                         f"{ind.den} (MM$)": "{:,.0f}", f"Δ {ind.den} (MM$)": "{:+,.0f}", f"Δ {ind.den} (%)": "{:+.2f}"},
                        na_rep="—"), hide_index=True, width="stretch")

        section("Comparación entre períodos seleccionados", "dos cierres cualesquiera", "REAL")
        opts = list(closes["fecha"])
        lab = dict(zip(closes["fecha"], closes["periodo"]))
        ca, cb = st.columns(2)
        fa = ca.selectbox("Período A", opts, index=max(0, len(opts) - 2 if gran != "Diaria" else len(opts) - 31),
                          format_func=lambda d: lab[d], key=f"cmpa_{ind.codigo}_{gran}")
        fb = cb.selectbox("Período B", opts, index=len(opts) - 1, format_func=lambda d: lab[d],
                          key=f"cmpb_{ind.codigo}_{gran}")
        cmp = an.compare_periods(closes, ind, fa, fb)
        c1, c2 = st.columns([1, 1])
        c1.dataframe(cmp.style.format({"Período A": "{:,.2f}", "Período B": "{:,.2f}",
                                       "Variación absoluta": "{:+,.2f}", "Variación %": "{:+.2f}%"}),
                     hide_index=True, width="stretch")
        c2.plotly_chart(charts.comparison_chart(cmp, ind, lab[fa], lab[fb]), width="stretch")

    # ---------------------------------------------------------------- 1.3
    with t2:
        section(f"Descomposición de {ind.num} y {ind.den}", "montos computables por componente", "REAL")
        opts = list(closes["fecha"])
        lab = dict(zip(closes["fecha"], closes["periodo"]))
        c1, c2 = st.columns(2)
        f_act = c1.selectbox("Cierre analizado", opts[::-1], format_func=lambda d: lab[d], key=f"dec_a_{ind.codigo}_{gran}")
        prev = [d for d in opts if d < f_act]
        if not prev:
            st.info("No existe un cierre anterior para comparar.")
            return
        f_ant = c2.selectbox("Comparado con", prev[::-1], format_func=lambda d: lab[d], key=f"dec_b_{ind.codigo}_{gran}")
        dec = an.decompose(det, ind, f_act, f_ant)
        r_act = closes.loc[closes["fecha"] == f_act, ind.ratio_col].iloc[0]
        r_ant = closes.loc[closes["fecha"] == f_ant, ind.ratio_col].iloc[0]
        top = dec.head(3)
        bullets = "".join(f"<li><b>{r['subcategoria']}</b> ({r['categoria']}): {r['var_abs']:+,.0f} MM$ "
                          f"({r['var_pct']:+.1f}%), impacto {r['impacto_pp']:+.2f} pp.</li>" for _, r in top.iterrows())
        note(f"<b>{ind.codigo} {r_ant:.2f}% → {r_act:.2f}% ({r_act - r_ant:+.2f} pp).</b> Componentes que más explican "
             f"el cambio:<ul style='margin:6px 0 0 0'>{bullets}</ul>", BBG_BLUE)
        st.plotly_chart(charts.impact_chart(dec, "impacto_pp", "subcategoria",
                                            f"Principales componentes que explican el cambio del {ind.codigo} (pp)"),
                        width="stretch")
        st.caption("Impacto = cambio del indicador al actualizar sólo esa línea, manteniendo el resto en el cierre "
                   "anterior. En el LCR el recálculo reaplica los topes regulatorios.")
        for comp, color in ((ind.num, BBG_MINT), (ind.den, BBG_RED)):
            d = dec[dec["componente"] == comp].copy()
            st.markdown(f"##### Detalle de {comp}")
            total = pd.DataFrame([{"categoria": "TOTAL", "subcategoria": "", "factor_ponderacion": np.nan,
                                   "monto_ant": d["monto_ant"].sum(), "monto_act": d["monto_act"].sum(),
                                   "var_abs": d["var_abs"].sum(),
                                   "var_pct": d["var_abs"].sum() / abs(d["monto_ant"].sum()) * 100 if d["monto_ant"].sum() else np.nan,
                                   "impacto_pp": np.nan, "sentido": ""}])
            show = pd.concat([d.sort_values("monto_act", key=abs, ascending=False), total], ignore_index=True)
            show = show[["categoria", "subcategoria", "factor_ponderacion", "monto_ant", "monto_act", "var_abs",
                         "var_pct", "impacto_pp", "sentido"]].rename(columns={
                "categoria": "Categoría", "subcategoria": "Componente", "factor_ponderacion": "Factor",
                "monto_ant": f"Monto {lab[f_ant]} (MM$)", "monto_act": f"Monto {lab[f_act]} (MM$)",
                "var_abs": "Variación (MM$)", "var_pct": "Variación (%)", "impacto_pp": f"Impacto {ind.codigo} (pp)",
                "sentido": "Efecto"})
            st.dataframe(show.style.format({"Factor": "{:.0%}", f"Monto {lab[f_ant]} (MM$)": "{:,.0f}",
                                            f"Monto {lab[f_act]} (MM$)": "{:,.0f}", "Variación (MM$)": "{:+,.0f}",
                                            "Variación (%)": "{:+.2f}%", f"Impacto {ind.codigo} (pp)": "{:+.3f}"},
                                           na_rep="—"), hide_index=True, width="stretch")
        adj = an.adjustment_lines(det, f_act, f_ant)
        if not adj.empty and (adj[["monto_ponderado_act", "monto_ponderado_ant"]].abs().sum().sum() > 0):
            note("<b>Ajustes por topes regulatorios aplicados:</b> " + "; ".join(
                f"{r['subcategoria']}: {r['monto_ponderado_ant']:,.0f} → {r['monto_ponderado_act']:,.0f} MM$"
                for _, r in adj.iterrows()), BBG_ORANGE)
        st.download_button(f"Descargar descomposición {ind.codigo} (CSV)", dec.to_csv(index=False).encode("utf-8"),
                           f"descomposicion_{ind.codigo}_{pd.Timestamp(f_act):%Y%m%d}.csv", "text/csv")

    # ---------------------------------------------------------------- 1.4
    with t3:
        last = closes_d_lcr["fecha"].max() if ind.codigo == "LCR" else closes_d_nsfr["fecha"].max()
        lines = an.lines_at(det, last)
        section("Pruebas de tensión", f"montos al cierre {last:%d-%m-%Y}", "SIM")
        note("Modifique el monto computable de cualquier componente (shock en %). El indicador se recalcula "
             "automáticamente con las mismas ponderaciones y topes del cálculo oficial. Los resultados son "
             "<b>simulaciones</b>, no datos observados.", BBG_ORANGE)
        code = ind.codigo
        st.session_state.setdefault(f"ver_{code}", 0)

        def _reset(c=code):
            st.session_state[f"esc_{c}"] = "Base"
            st.session_state[f"ver_{c}"] += 1

        c1, c2 = st.columns([3, 1])
        esc = c1.radio("Escenario", SCENARIOS, horizontal=True, key=f"esc_{code}",
                       help="Base: sin shocks. Adverso y Severo: supuestos predefinidos (ver tabla al final).")
        c2.button("Restablecer montos originales", on_click=_reset, key=f"rst_{code}", width="stretch")
        st.caption(an.SCENARIO_DESCRIPTION[esc])
        defaults = an.scenario_shocks(ind, lines, esc)
        # Columna editable junto al nombre de la línea para que sea visible también en pantallas angostas.
        editor_df = pd.DataFrame({"Línea": lines["subcategoria"], "Shock (%)": defaults,
                                  "Monto original (MM$)": lines["base"], "Componente": lines["componente"],
                                  "Categoría": lines["categoria"]})
        edited = st.data_editor(
            editor_df, key=f"editor_{code}_{esc}_{st.session_state[f'ver_{code}']}", hide_index=True, width="stretch",
            disabled=["Componente", "Categoría", "Línea", "Monto original (MM$)"],
            column_config={"Monto original (MM$)": st.column_config.NumberColumn(format="%,.0f"),
                           "Shock (%)": st.column_config.NumberColumn(min_value=-100.0, max_value=300.0, step=1.0,
                                                                      format="%+.1f %%")})
        shocks = pd.Series(edited["Shock (%)"].astype(float).values, index=lines.index)
        custom = not np.allclose(shocks.values, defaults.values)
        res = an.run_stress(ind, lines, shocks)
        e_est = an.estado(res["estresado"]["ratio"], umbrales)
        label = f"{esc}{' (modificado)' if custom else ''}"

        m1, m2, m3, m4 = st.columns(4)
        m1.metric(f"{code} original", f"{res['base']['ratio']:.2f}%")
        m2.metric(f"{code} simulado · {label}", f"{res['estresado']['ratio']:.2f}%", f"{res['delta_pp']:+.2f} pp")
        m3.metric(f"{ind.num} simulado (MM$)", f"{res['estresado']['num']:,.0f}",
                  f"{res['estresado']['num'] - res['base']['num']:+,.0f}")
        m4.metric(f"{ind.den} simulado (MM$)", f"{res['estresado']['den']:,.0f}",
                  f"{res['estresado']['den'] - res['base']['den']:+,.0f}", delta_color="inverse")
        note(f"<b>Situación simulada: {e_est['codigo']}.</b> {e_est['detalle']}. "
             f"Efecto combinado {res['delta_pp']:+.2f} pp = suma de impactos individuales "
             f"{res['delta_pp'] - res['interaccion_pp']:+.2f} pp + interacción/topes {res['interaccion_pp']:+.2f} pp.",
             e_est["color"])

        c1, c2 = st.container(), st.container()
        with c1:
            if (res["lineas"]["shock_pct"] != 0).any():
                st.plotly_chart(charts.impact_chart(res["lineas"], "impacto_individual_pp", "subcategoria",
                                                    "Impacto individual de cada modificación (pp)"), width="stretch")
            else:
                st.info("Sin modificaciones: seleccione el escenario Adverso o Severo, o ingrese shocks en la tabla.")
        with c2:
            all_res = {}
            for s in SCENARIOS:
                all_res[s] = an.run_stress(ind, lines, an.scenario_shocks(ind, lines, s))["estresado"]["ratio"]
            if custom:
                all_res["Actual (modificado)"] = res["estresado"]["ratio"]
            st.plotly_chart(charts.scenario_bars(all_res, ind, umbrales, f"{code} por escenario (simulado)"),
                            width="stretch")

        chg = res["lineas"][res["lineas"]["shock_pct"] != 0]
        if not chg.empty:
            st.dataframe(chg[["componente", "subcategoria", "base", "shock_pct", "base_estresada", "variacion_monto",
                              "impacto_individual_pp"]].rename(columns={
                "componente": "Componente", "subcategoria": "Línea", "base": "Original (MM$)", "shock_pct": "Shock (%)",
                "base_estresada": "Simulado (MM$)", "variacion_monto": "Variación (MM$)",
                "impacto_individual_pp": f"Impacto {code} (pp)"}).style.format({
                "Original (MM$)": "{:,.0f}", "Shock (%)": "{:+.1f}", "Simulado (MM$)": "{:,.0f}",
                "Variación (MM$)": "{:+,.0f}", f"Impacto {code} (pp)": "{:+.3f}"}), hide_index=True, width="stretch")

        st.markdown("##### Trazabilidad del escenario")
        cc1, cc2 = st.columns([3, 1])
        comment = cc1.text_input("Comentario (opcional)", key=f"cmt_{code}",
                                 placeholder="Motivo o supuesto adicional del escenario")
        if cc2.button("Registrar escenario", key=f"log_{code}", width="stretch"):
            run = report_store.log_stress_run(
                code, label, last, res["base"]["ratio"], res["estresado"]["ratio"],
                {f"{r['componente']} · {r['subcategoria']}": float(r["shock_pct"]) for _, r in chg.iterrows()}, comment)
            st.success(f"Escenario registrado (#{run}) con sus shocks por línea, fecha de corte y usuario.")
        log = report_store.stress_log(code)
        if not log.empty:
            st.dataframe(log[["run_id", "registrado_en", "usuario", "escenario", "fecha_corte", "ratio_base",
                              "ratio_estresado", "shocks_json", "comentario"]].head(10).rename(columns={
                "run_id": "N°", "registrado_en": "Registrado", "usuario": "Usuario", "escenario": "Escenario",
                "fecha_corte": "Corte", "ratio_base": "Original (%)", "ratio_estresado": "Simulado (%)",
                "shocks_json": "Shocks aplicados", "comentario": "Comentario"}).style.format(
                {"Original (%)": "{:.2f}", "Simulado (%)": "{:.2f}"}), hide_index=True, width="stretch")
        with st.expander("Supuestos de los escenarios predefinidos"):
            rows = [{"Escenario": s, "Categoría afectada": c, "Shock sobre monto computable (%)": v}
                    for s in ("Adverso", "Severo") for c, v in an.SCENARIOS[code][s].items()]
            st.dataframe(pd.DataFrame(rows).style.format({"Shock sobre monto computable (%)": "{:+.1f}"}),
                         hide_index=True, width="stretch")

    # ---------------------------------------------------------------- 1.5
    with t4:
        last = hist["fecha"].max()
        lines = an.lines_at(det, last)
        section("Proyecciones", "estimaciones estadísticas", "PROY")
        note("Las proyecciones son <b>estimaciones</b> basadas en el comportamiento histórico y en supuestos de "
             "tensión; no constituyen resultados ciertos. Las bandas muestran un intervalo aproximado del 95%.",
             BBG_BLUE)
        c1, c2 = st.columns([1, 2])
        horizonte = c1.selectbox("Horizonte (días)", [30, 60, 90, 180], index=2, key=f"hz_{ind.codigo}")
        escs = c2.multiselect("Escenarios", SCENARIOS, default=SCENARIOS, key=f"pe_{ind.codigo}")
        if not escs:
            st.info("Seleccione al menos un escenario.")
            return
        proj = an.project_with_scenarios(hist, ind, lines, horizonte, escs)
        st.markdown(f"##### Proyección del {ind.codigo} bajo escenarios de tensión")
        st.plotly_chart(charts.projection_chart(hist, ind, proj, umbrales), width="stretch")
        rows = []
        for s, df in proj.items():
            row = {"Escenario": s, f"{ind.codigo} día 30 (%)": df["pred"].iloc[min(29, len(df) - 1)],
                   f"{ind.codigo} día {horizonte} (%)": df["pred"].iloc[-1], "Mínimo proyectado (%)": df["pred"].min(),
                   "Intervalo final 95%": f"[{df['lower'].iloc[-1]:.1f}% ; {df['upper'].iloc[-1]:.1f}%]"}
            last_real = float(hist[ind.ratio_col].iloc[-1])
            for u in sorted(umbrales, key=lambda u: -u.valor):
                row[f"Bajo {u.valor:.0f}% (estimado)"] = an.threshold_crossing(df, last_real, u.valor)
            rows.append(row)
        st.dataframe(pd.DataFrame(rows).style.format({c: "{:.2f}" for c in rows[0] if c.endswith("(%)")}),
                     hide_index=True, width="stretch")

        st.markdown(f"##### Proyección del {ind.codigo} con montos históricos de {ind.num} y {ind.den}")
        h = hist.set_index("fecha")
        fn, fd = an.forecast(h[ind.num_col], horizonte, "holt"), an.forecast(h[ind.den_col], horizonte, "holt")
        hist_proj = {"Base": proj["Base"]} if "Base" in proj else an.project_with_scenarios(hist, ind, lines, horizonte, ["Base"])
        cA, cB = st.columns(2)
        import plotly.graph_objects as go
        fig = go.Figure()
        hh = hist.tail(180)
        for col, f, name, color in ((ind.num_col, fn, ind.num, BBG_MINT), (ind.den_col, fd, ind.den, BBG_RED)):
            fig.add_trace(go.Scatter(x=hh["fecha"], y=hh[col], name=f"{name} real", line=dict(color=color, width=2)))
            fig.add_trace(go.Scatter(x=list(f["fecha"]) + list(f["fecha"][::-1]), y=list(f["upper"]) + list(f["lower"][::-1]),
                                     fill="toself", fillcolor=charts.rgba(color, 0.10), line=dict(width=0),
                                     hoverinfo="skip", showlegend=False))
            fig.add_trace(go.Scatter(x=f["fecha"], y=f["pred"], name=f"{name} proyectado",
                                     line=dict(color=color, width=2, dash="dash")))
        cA.plotly_chart(charts._layout(fig, f"{ind.num} y {ind.den}: real y proyectado (MM$)", 420), width="stretch")
        cB.plotly_chart(charts.projection_chart(hist, ind, hist_proj, umbrales,
                                                title=f"{ind.codigo} implícito ({ind.num} proyectado / {ind.den} proyectado)"),
                        width="stretch")
        first = hist["fecha"].min()
        note(f"<b>Supuestos.</b> Horizonte: {horizonte} días desde el cierre {last:%d-%m-%Y}. Datos usados: cierres diarios "
             f"del {first:%d-%m-%Y} al {last:%d-%m-%Y} ({len(hist)} días). Método: Holt amortiguado (φ = 0,90) "
             f"aplicado por separado a {ind.num} y {ind.den}; parámetros elegidos por mínimo error cuadrático a un paso. "
             f"Escenarios: el efecto relativo de los shocks sobre {ind.num} y {ind.den} al cierre se aplica de forma "
             f"gradual durante los primeros 30 días y luego se mantiene. Intervalo: ±1,96 desviaciones de los errores "
             f"a un paso, escaladas por la raíz del plazo.", BBG_BLUE)


# =============================================================================
# 3. ANÁLISIS TRANSVERSAL
# =============================================================================

def transversal():
    t1, t2, t3, t4, t5 = st.tabs(["3.1 Puente de variaciones", "3.2 Holgura frente a límites",
                                  "3.3 Precisión de proyecciones", "3.4 Concentración y vulnerabilidad",
                                  "3.5 Resumen ejecutivo y alertas"])
    pick = lambda code: (an.LCR, lcr_hist, lcr_det, U_LCR) if code == "LCR" else (an.NSFR, nsfr_hist, nsfr_det, U_NSFR)

    with t1:
        section("Puente de variaciones", "contribución de cada componente entre dos cierres", "REAL")
        code = st.radio("Indicador", ["LCR", "NSFR"], horizontal=True, key="wf_ind")
        ind, hist, det, _ = pick(code)
        closes = an.period_closes(hist, ind, gran)
        opts, lab = list(closes["fecha"]), dict(zip(closes["fecha"], closes["periodo"]))
        c1, c2 = st.columns(2)
        fa = c1.selectbox("Desde", opts, index=max(0, len(opts) - 2), format_func=lambda d: lab[d], key=f"wfa_{gran}")
        fb = c2.selectbox("Hasta", opts, index=len(opts) - 1, format_func=lambda d: lab[d], key=f"wfb_{gran}")
        wf = an.waterfall_by_category(det, ind, fa, fb)
        st.plotly_chart(charts.waterfall_chart(wf, ind, lab[fa], lab[fb]), width="stretch")
        pos = wf[(wf["contribucion_pp"] > 0) & ~wf["categoria"].str.startswith("Residual")]
        neg = wf[(wf["contribucion_pp"] < 0) & ~wf["categoria"].str.startswith("Residual")]
        resid = wf.iloc[-1]["contribucion_pp"]
        note(f"<b>Cambio total {wf.attrs['ratio_b'] - wf.attrs['ratio_a']:+.2f} pp.</b> Contribuciones positivas "
             f"{pos['contribucion_pp'].sum():+.2f} pp; negativas {neg['contribucion_pp'].sum():+.2f} pp; "
             f"residual {resid:+.3f} pp. El residual corresponde a la interacción entre numerador y denominador "
             f"y, en el LCR, al efecto de los topes regulatorios; no es un error de cálculo.", BBG_BLUE)
        st.dataframe(wf.rename(columns={"categoria": "Categoría", "componente": "Componente",
                                        "monto_a": f"Monto {lab[fa]} (MM$)", "monto_b": f"Monto {lab[fb]} (MM$)",
                                        "contribucion_pp": "Contribución (pp)"}).style.format(
            {f"Monto {lab[fa]} (MM$)": "{:,.0f}", f"Monto {lab[fb]} (MM$)": "{:,.0f}", "Contribución (pp)": "{:+.3f}"},
            na_rep="—"), hide_index=True, width="stretch")

    with t2:
        section("Holgura frente a límites", f"cierre {fecha_corte:%d-%m-%Y}", "REAL")
        for code in ("LCR", "NSFR"):
            ind, hist, det, umb = pick(code)
            k = kd_lcr if code == "LCR" else kd_nsfr
            st.markdown(f"#### {code}: {k['ratio']:.2f}%")
            c1, c2 = st.columns([1, 2])
            c1.plotly_chart(charts.gauge(k["ratio"], code, umb, k["estado"]), width="stretch", key=f"gauge_buf_{code}")
            buf = an.buffers(ind, k["ratio"], k["num"], k["den"], umb)
            c2.dataframe(buf.style.format({c: "{:+,.0f}" if "MM$" in c else "{:+.2f}" for c in buf.columns
                                           if pd.api.types.is_numeric_dtype(buf[c])}), hide_index=True, width="stretch")
            stats = an.alert_statistics(hist, ind, umb)
            c2.dataframe(stats.style.format({"Frecuencia histórica (%)": "{:.1f}"}), hide_index=True, width="stretch")
        st.markdown("##### Monto requerido para llevar un indicador a un nivel objetivo")
        c1, c2, c3 = st.columns(3)
        code = c1.selectbox("Indicador", ["LCR", "NSFR"], key="req_ind")
        k = kd_lcr if code == "LCR" else kd_nsfr
        ind = an.LCR if code == "LCR" else an.NSFR
        target = c2.number_input("Nivel objetivo (%)", value=150.0 if code == "LCR" else 110.0, step=1.0, key="req_t")
        adj = an.required_adjustment(target, k["num"], k["den"])
        verb_n = "aumentar" if adj["delta_num"] > 0 else "podría reducirse"
        verb_d = "reducir" if adj["delta_den"] < 0 else "podría aumentar"
        c3.metric(f"{ind.num} para {target:.0f}%", f"{adj['delta_num']:+,.0f} MM$")
        note(f"Para llevar el {code} de {k['ratio']:.2f}% a {target:.0f}%: {ind.num} debe {verb_n} en "
             f"<b>{abs(adj['delta_num']):,.0f} MM$</b>, o bien {ind.den} {verb_d} en <b>{abs(adj['delta_den']):,.0f} MM$</b> "
             f"(cada alternativa por sí sola). En el LCR, el aumento de ALAC supone activos de Nivel 1, que no "
             f"están sujetos a topes.", BBG_BLUE)

    with t3:
        section("Precisión de las proyecciones", "evaluación fuera de muestra con origen móvil", "PROY")
        c1, c2 = st.columns(2)
        code = c1.radio("Indicador", ["LCR", "NSFR"], horizontal=True, key="bt_ind")
        hz = c2.selectbox("Horizonte evaluado (días)", [5, 10, 20, 30], index=2, key="bt_h")
        ind, hist, _, _ = pick(code)
        metrics, det_bt = backtest_cached(code, hz, hist[ind.ratio_col].values.tobytes(), hist, ind)
        best = metrics.iloc[0]
        st.dataframe(metrics.drop(columns=["metodo"])[["Método", "MAE (pp)", "RMSE (pp)", "MAPE (%)", "Sesgo (pp)",
                                                       "Cobertura intervalo 95% (%)", "Pruebas"]].style.format(
            {"MAE (pp)": "{:.3f}", "RMSE (pp)": "{:.3f}", "MAPE (%)": "{:.2f}", "Sesgo (pp)": "{:+.3f}",
             "Cobertura intervalo 95% (%)": "{:.0f}", "Pruebas": "{:.0f}"}), hide_index=True, width="stretch")
        note(f"Con {int(best['Pruebas'])} pruebas a {hz} días, el método con menor error es <b>{best['Método']}</b> "
             f"(RMSE {best['RMSE (pp)']:.2f} pp, MAPE {best['MAPE (%)']:.2f}%). Su intervalo del 95% contuvo el valor "
             f"observado en el {best['Cobertura intervalo 95% (%)']:.0f}% de los casos. Los errores pasados no "
             f"garantizan el desempeño futuro.", BBG_BLUE)
        met = st.selectbox("Método a visualizar", list(an.METHODS), format_func=an.METHODS.get, key="bt_m",
                           index=list(an.METHODS).index(best["metodo"]))
        dsel = det_bt[det_bt["metodo"] == met]
        st.plotly_chart(charts.backtest_chart(dsel, an.METHODS[met], code), width="stretch")
        st.dataframe(dsel[["origen", "fecha_objetivo", "proyectado", "observado", "error", "lower", "upper"]].rename(
            columns={"origen": "Proyectado el", "fecha_objetivo": "Fecha objetivo", "proyectado": "Proyectado (%)",
                     "observado": "Observado (%)", "error": "Error (pp)", "lower": "Límite inf. (%)",
                     "upper": "Límite sup. (%)"}).style.format({"Proyectado (%)": "{:.2f}", "Observado (%)": "{:.2f}",
                                                                "Error (pp)": "{:+.2f}", "Límite inf. (%)": "{:.2f}",
                                                                "Límite sup. (%)": "{:.2f}", "Proyectado el": "{:%d-%m-%Y}",
                                                                "Fecha objetivo": "{:%d-%m-%Y}"}),
                     hide_index=True, width="stretch")

    with t4:
        section("Concentración y vulnerabilidad", f"cierre {fecha_corte:%d-%m-%Y}", "REAL")
        c1, c2 = st.columns(2)
        comp = c1.radio("Componente", ["ALAC", "NAF", "FED", "FER"], horizontal=True, key="cc_comp")
        dim = c2.radio("Agrupar por", ["Instrumento / producto", "Categoría regulatoria"], horizontal=True, key="cc_dim")
        dim_col = "subcategoria" if dim.startswith("Instrumento") else "categoria"
        det = lcr_det if comp in ("ALAC", "NAF") else nsfr_det
        cc = an.concentration(det, comp, fecha_corte, dim_col)
        m1, m2, m3 = st.columns(3)
        m1.metric("Índice HHI", f"{cc['hhi']:,.0f}", cc["nivel"], delta_color="off")
        m2.metric("Participación top 5", f"{cc['top5']:.1f}%")
        if cc["dependencia_mayorista"] is not None:
            m3.metric("Dependencia de fondeo mayorista", f"{cc['dependencia_mayorista']:.1f}%",
                      help=f"Porción de {comp} asociada a fondeo mayorista y no garantizado.")
        st.plotly_chart(charts.pareto_chart(cc["tabla"], dim_col, comp, cc["hhi"], cc["nivel"]), width="stretch")
        st.markdown("##### Componentes con mayor volatilidad histórica (últimos 90 días)")
        st.dataframe(cc["volatilidad"].rename(columns={dim_col: "Componente"}).style.format(
            {"Monto promedio (MM$)": "{:,.0f}", "Desv. estándar variación diaria (MM$)": "{:,.0f}",
             "Coef. de variación (%)": "{:.2f}", "Peor variación diaria (MM$)": "{:+,.0f}"}, na_rep="—"),
            hide_index=True, width="stretch")
        st.caption("HHI = suma de participaciones al cuadrado (0–10.000): < 1.500 baja, 1.500–2.500 moderada, "
                   "> 2.500 alta concentración. Análisis por emisor o contraparte individual requiere ese nivel de "
                   "detalle en la fuente.")

    with t5:
        executive_page()


@st.cache_data(ttl=600, show_spinner="Evaluando métodos de proyección…")
def backtest_cached(code, hz, key, _hist, _ind):
    return an.backtest(_hist.set_index("fecha")[_ind.ratio_col], horizon=hz)


def stress_pair(escenario: str):
    out = {}
    for ind, det in ((an.LCR, lcr_det), (an.NSFR, nsfr_det)):
        lines = an.lines_at(det, fecha_corte)
        r = an.run_stress(ind, lines, an.scenario_shocks(ind, lines, escenario))
        r["nombre"] = escenario
        out[ind.codigo] = r
    return out


def executive_context():
    k_l = an.kpi_snapshot(an.period_closes(lcr_hist, an.LCR, gran), an.LCR, U_LCR, gran)
    k_n = an.kpi_snapshot(an.period_closes(nsfr_hist, an.NSFR, gran), an.NSFR, U_NSFR, gran)
    dec_l = an.decompose(lcr_det, an.LCR, k_l["fecha"], k_l["fecha_ant"])
    dec_n = an.decompose(nsfr_det, an.NSFR, k_n["fecha"], k_n["fecha_ant"])
    return k_l, k_n, dec_l, dec_n


def executive_page():
    section("Resumen ejecutivo", f"{gran.lower()} · cierre {fecha_corte:%d-%m-%Y}", "REAL")
    k_l, k_n, dec_l, dec_n = executive_context()
    esc = st.radio("Escenario de tensión a destacar", ["Adverso", "Severo"], horizontal=True, key="ex_esc")
    st_pair = stress_pair(esc)
    summ = an.executive_summary(k_l, k_n, dec_l, dec_n, st_pair["LCR"], st_pair["NSFR"], U_LCR, U_NSFR)
    worst_color = min((k_l["estado"], k_n["estado"]), key=lambda e: [BBG_RED, BBG_ORANGE, BBG_BLUE, BBG_MINT].index(e["color"]))["color"]
    c1, c2 = st.columns(2)
    c1.plotly_chart(charts.gauge(k_l["ratio"], "LCR", U_LCR, k_l["estado"]), width="stretch", key="gauge_exec_lcr")
    c2.plotly_chart(charts.gauge(k_n["ratio"], "NSFR", U_NSFR, k_n["estado"]), width="stretch", key="gauge_exec_nsfr")
    st.markdown(f'<div class="conclusion" style="--c:{worst_color};border-color:{worst_color}"><b>Conclusión.</b> '
                f'{summ["conclusion"]}</div>', unsafe_allow_html=True)
    blocks = [("Principales movimientos del período", summ["movimientos"], BBG_BLUE, "REAL"),
              ("Indicadores próximos a sus umbrales", summ["proximos"], BBG_ORANGE, "REAL"),
              ("Componentes que contribuyen al deterioro", summ["deterioro"], BBG_RED, "REAL"),
              (f"Resultado del escenario {esc.lower()}", summ["adverso"], BBG_ORANGE, "SIM")]
    for i in range(0, 4, 2):
        cols = st.columns(2)
        for col, (title, items, color, tag) in zip(cols, blocks[i:i + 2]):
            col.markdown(f'<div class="summary-block" style="--c:{color}"><h6>{title}{TAG[tag]}</h6><ul>'
                         + "".join(f"<li>{x}</li>" for x in items) + "</ul></div>", unsafe_allow_html=True)

    section("Observaciones y acciones de mitigación", "registro trazable")
    with st.form("obs_form", clear_on_submit=True):
        c1, c2, c3 = st.columns([1, 1, 1])
        ind_o = c1.selectbox("Indicador", ["LCR", "NSFR", "Ambos"])
        resp = c2.text_input("Responsable", placeholder="Área o cargo")
        plazo = c3.date_input("Plazo", value=date.today() + timedelta(days=30))
        obs = st.text_area("Observación", placeholder="Hallazgo o situación observada")
        acc = st.text_area("Acción de mitigación", placeholder="Acción comprometida")
        estado_o = st.selectbox("Estado", ["Abierta", "En curso", "Cerrada"])
        if st.form_submit_button("Registrar observación"):
            if obs.strip() and acc.strip() and resp.strip():
                report_store.add_observation(fecha_corte, ind_o, obs.strip(), acc.strip(), resp.strip(), plazo, estado_o)
                st.success("Observación registrada.")
            else:
                st.warning("Complete observación, acción y responsable.")
    obs_df = report_store.observations()
    if obs_df.empty:
        st.caption("Aún no hay observaciones registradas.")
    else:
        st.dataframe(obs_df.rename(columns={"obs_id": "N°", "registrado_en": "Registrado", "usuario": "Usuario",
                                            "fecha_corte": "Corte", "indicador": "Indicador", "observacion": "Observación",
                                            "accion": "Acción", "responsable": "Responsable", "plazo": "Plazo",
                                            "estado": "Estado"}), hide_index=True, width="stretch")


# =============================================================================
# 4. INFORME PDF
# =============================================================================

def pdf_page():
    section("Informe PDF consolidado", "LCR y NSFR para la alta administración")
    c1, c2, c3 = st.columns(3)
    dates = sorted(pd.to_datetime(lcr_hist["fecha"]).unique())[::-1]
    corte = c1.selectbox("Fecha de corte", dates[:400], format_func=lambda d: pd.Timestamp(d).strftime("%d-%m-%Y"))
    vista = c2.selectbox("Comparación de variaciones", GRANS, index=GRANS.index(gran))
    meses = c3.selectbox("Período de tendencia", [3, 6, 12, 24], index=2, format_func=lambda m: f"Últimos {m} meses")
    c4, c5 = st.columns(2)
    escs = c4.multiselect("Escenarios de tensión a incluir", SCENARIOS, default=["Adverso", "Severo"])
    horizonte = c5.selectbox("Horizonte de proyección (días)", [30, 60, 90], index=1)
    obs_df = report_store.observations()
    sel_obs = st.multiselect("Observaciones y acciones a incluir", list(obs_df["obs_id"]) if not obs_df.empty else [],
                             default=list(obs_df["obs_id"].head(5)) if not obs_df.empty else [],
                             format_func=lambda i: f"#{i} · {obs_df.loc[obs_df['obs_id'] == i, 'observacion'].iloc[0][:70]}")
    extra = st.text_area("Comentario adicional de la gerencia (opcional)")
    if st.button("Generar informe PDF", type="primary"):
        with st.spinner("Calculando y generando el informe…"):
            pdf = pdf_generator.build_report(
                lcr_hist=lcr_hist, lcr_det=lcr_det, nsfr_hist=nsfr_hist, nsfr_det=nsfr_det, corte=pd.Timestamp(corte),
                granularidad=vista, meses_tendencia=meses, escenarios=escs, horizonte=horizonte,
                umbrales_lcr=U_LCR, umbrales_nsfr=U_NSFR,
                observaciones=obs_df[obs_df["obs_id"].isin(sel_obs)] if not obs_df.empty else obs_df,
                comentario=extra, calidad=pd.concat([dq_lcr.assign(Indicador="LCR"), dq_nsfr.assign(Indicador="NSFR")]),
                fuente=get_data_source_label())
        st.session_state["pdf_bytes"] = pdf
        st.session_state["pdf_name"] = f"Informe_Liquidez_LCR_NSFR_{pd.Timestamp(corte):%Y%m%d}.pdf"
    if "pdf_bytes" in st.session_state:
        st.download_button("Descargar informe PDF", st.session_state["pdf_bytes"], st.session_state["pdf_name"],
                           "application/pdf")
        try:
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(st.session_state["pdf_bytes"])
            st.caption(f"Vista previa · {len(doc)} páginas")
            cols = st.columns(min(3, len(doc)))
            for i, col in enumerate(cols):
                col.image(doc[i].render(scale=1.2).to_pil(), width="stretch")
        except Exception:
            pass


# =============================================================================
# 5. CALIDAD Y TRAZABILIDAD
# =============================================================================

def quality_page():
    section("Validación de calidad de datos", "se ejecuta antes de calcular los indicadores")
    c1, c2 = st.columns(2)
    for col, code, dq in ((c1, "LCR", dq_lcr), (c2, "NSFR", dq_nsfr)):
        col.markdown(f"##### {code}")
        col.dataframe(dq, hide_index=True, width="stretch")
    note("ERROR detiene el cálculo de los indicadores; OBSERVADO se informa pero no lo impide. Los datos provienen "
         "de la base regulatoria (ledger, core bitemporal y data warehouse) descrita a continuación.", BBG_BLUE)
    render_core_architecture_page()


# =============================================================================
# ENRUTAMIENTO
# =============================================================================
if modulo == MODULES[0]:
    panel(an.LCR, lcr_hist, lcr_det, U_LCR, 1)
elif modulo == MODULES[1]:
    panel(an.NSFR, nsfr_hist, nsfr_det, U_NSFR, 2)
elif modulo == MODULES[2]:
    transversal()
elif modulo == MODULES[3]:
    pdf_page()
else:
    quality_page()

st.markdown(f"""<div class="report-footer">Panel de liquidez · Gerencia de Riesgo Financiero · Uso interno<br>
Cifras en millones de pesos (MM$) · Fecha de corte {fecha_corte:%d-%m-%Y} ·
<span style="color:{BBG_MINT}">DATO REAL</span> observado ·
<span style="color:{BBG_BLUE}">PROYECCIÓN</span> estimada ·
<span style="color:{BBG_ORANGE}">SIMULACIÓN</span> de escenario</div>""", unsafe_allow_html=True)
