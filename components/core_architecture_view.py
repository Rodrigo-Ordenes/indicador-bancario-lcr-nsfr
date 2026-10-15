"""
components/core_architecture_view.py
====================================
Módulo de arquitectura y auditoría: presenta, con datos vivos de
`core_banking.db`, las cuatro capas y los reportes normativos derivados.

1. Ledger inmutable: asientos, dos fases (pending / posted), idempotencia,
   outbox y verificación criptográfica completa.
2. Core bitemporal (AVF): time travel, historial de aserciones y aserciones futuras.
3. DWH Kimball: snapshot con reexpresiones, flujos contractuales y saldo point-in-time.
4. Control ETL: bitácora de lotes y conciliación de tres vías.
5. Reportes normativos: C46, C54, C55 y C56.
6. Scripts T-SQL de producción.
"""

import os
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

import db_layer

MONEY = "${:,.0f}"


def _section(title: str, note: str = "") -> None:
    st.markdown(f'<div class="section-title"><h3>{title}</h3><span>{note}</span></div>', unsafe_allow_html=True)


def _fmt_money(df: pd.DataFrame, cols) -> "pd.io.formats.style.Styler":
    return df.style.format({c: MONEY for c in cols if c in df.columns}, na_rep="—")


@st.cache_resource(show_spinner="Inicializando la base de datos regulatoria…")
def _ensure_db() -> bool:
    db_layer.init_database()
    return True


def render_core_architecture_page():
    _ensure_db()

    st.markdown("### Arquitectura de datos y auditoría regulatoria")
    st.caption("Cuatro capas desacopladas: ledger OLTP inmutable · staging y control · core bitemporal (AVF) · "
               "data warehouse dimensional (Kimball). Cifras en pesos chilenos (CLP).")

    is_chain_valid, audit_df = db_layer.verify_ledger_cryptographic_chain()
    recon = db_layer.get_latest_reconciliation()
    counts = db_layer.get_dwh_row_counts().set_index("tabla")["filas"]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Asientos en el ledger", f"{db_layer.count_ledger_entries():,}", "append-only")
    m2.metric("Cadena SHA-256", "Íntegra" if is_chain_valid else "Alterada",
              f"{len(audit_df):,} bloques recalculados", delta_color="off")
    descuadres = int((recon["status"] != "CUADRADO").sum()) if not recon.empty else 0
    m3.metric("Conciliación 3 vías", "0,0000" if descuadres == 0 else f"{descuadres} descuadres",
              "core = ledger = DWH" if descuadres == 0 else "revisar", delta_color="off")
    m4.metric("Hechos de snapshot", f"{counts['dwh_fact_account_balance_snapshot']:,}",
              f"{counts['dwh_fact_contractual_cashflows']:,} flujos", delta_color="off")

    tabs = st.tabs(["1 · Ledger inmutable", "2 · Core bitemporal", "3 · Data warehouse",
                    "4 · Control ETL", "5 · Reportes CMF", "6 · Scripts T-SQL"])

    # ---------------------------------------------------------------------
    with tabs[0]:
        _section("Ledger contable inmutable", "Capa 1 · data plane")
        st.caption("Cada asiento se contabiliza en una única transacción no interactiva: validación de partida doble, "
                   "límites de sobregiro, encadenamiento SHA-256, registro de idempotencia y evento de outbox, "
                   "todo en el mismo commit. UPDATE y DELETE están bloqueados por triggers en el motor.")
        entries = db_layer.get_ledger_entries(limit=40)
        st.markdown("##### Asientos de operaciones nominadas (excluye movimiento neto diario del libro ALM)")
        st.dataframe(
            entries[["journal_entry_id", "entry_kind", "posting_date", "value_date", "transaction_code", "description",
                     "total_debit", "pending_entry_id", "entry_hash"]].rename(columns={
                "journal_entry_id": "Asiento", "entry_kind": "Tipo", "posting_date": "Fecha contable",
                "value_date": "Fecha valor", "transaction_code": "Código", "description": "Glosa",
                "total_debit": "Monto", "pending_entry_id": "Reserva", "entry_hash": "Hash SHA-256"}
            ).style.format({"Monto": MONEY, "Reserva": "{:.0f}"}, na_rep="—"),
            width="stretch", hide_index=True, height=320,
        )

        c1, c2 = st.columns([1, 1])
        with c1:
            st.markdown("##### Saldos contables y disponibles (dos fases)")
            st.caption("Las reservas PENDING reducen el saldo disponible sin alterar el contable hasta su liquidación.")
            bal = db_layer.get_account_balances()
            bal = bal[bal["contract_id"].isin([60300, 30101, 30102, 90210, 80450, 70100, 10001, 19901])]
            st.dataframe(_fmt_money(bal[["account_id", "account_name", "contract_id", "debits_pending", "credits_pending",
                                         "saldo_contable", "saldo_disponible"]].rename(columns={
                "account_id": "Cuenta", "account_name": "Nombre", "contract_id": "Contrato",
                "debits_pending": "Debe pendiente", "credits_pending": "Haber pendiente",
                "saldo_contable": "Saldo contable", "saldo_disponible": "Saldo disponible"}),
                ["Debe pendiente", "Haber pendiente", "Saldo contable", "Saldo disponible"]),
                width="stretch", hide_index=True)
        with c2:
            st.markdown("##### Registro de idempotencia")
            st.caption("El reintento de la compra de BTP (misma clave, mismo payload) fue respondido desde este "
                       "registro sin generar un segundo cargo; un payload distinto con la misma clave se rechaza.")
            idem = db_layer.get_idempotency_records(limit=20)
            st.dataframe(idem[["idempotency_key", "operation", "status", "journal_entry_id", "payload_hash"]].rename(columns={
                "idempotency_key": "Clave", "operation": "Operación", "status": "Estado",
                "journal_entry_id": "Asiento", "payload_hash": "Hash payload"}),
                width="stretch", hide_index=True)

        c3, c4 = st.columns([1, 1])
        with c3:
            st.markdown("##### Transactional outbox")
            ob = db_layer.get_outbox_status()
            st.caption(f"{ob['total_events']:,} eventos publicados · {ob['inbox_events']:,} recibidos por el consumidor "
                       "ODS (entrega effectively-once mediante inbox con clave única).")
            st.dataframe(ob["recent"].rename(columns={"outbox_id": "Evento", "event_type": "Tipo",
                                                      "aggregate_id": "Agregado", "journal_entry_id": "Asiento",
                                                      "created_at": "Creado (UTC)"}),
                         width="stretch", hide_index=True)
        with c4:
            st.markdown("##### Verificación criptográfica")
            st.caption("Recalcula el hash de cada línea, de cada cabecera y el encadenamiento con el bloque previo.")
            if is_chain_valid:
                st.success(f"Integridad verificada en {len(audit_df):,} bloques.")
            else:
                st.error("Se detectaron bloques alterados o una ruptura de la cadena.")
            st.dataframe(audit_df.tail(12).rename(columns={
                "entry_id": "Asiento", "code": "Código", "kind": "Tipo", "prev_hash_actual": "Hash previo",
                "expected_prev": "Esperado", "hash_recalculado": "Hash recalculado", "status": "Estado"}),
                width="stretch", hide_index=True)

        st.markdown("##### Líneas de detalle (multi-pata)")
        postings = db_layer.get_ledger_postings(limit=60)
        st.dataframe(_fmt_money(postings[["journal_entry_id", "line_number", "account_id", "contract_id", "posting_status",
                                          "functional_debit", "functional_credit", "released_debit", "released_credit",
                                          "row_hash"]].rename(columns={
            "journal_entry_id": "Asiento", "line_number": "Línea", "account_id": "Cuenta", "contract_id": "Contrato",
            "posting_status": "Estado", "functional_debit": "Debe", "functional_credit": "Haber",
            "released_debit": "Debe liberado", "released_credit": "Haber liberado", "row_hash": "Hash de línea"}),
            ["Debe", "Haber", "Debe liberado", "Haber liberado"]), width="stretch", hide_index=True, height=300)

    # ---------------------------------------------------------------------
    with tabs[1]:
        _section("Core bitemporal · Asserted Versioning Framework", "Capa 3 · ODS")
        st.caption("Tiempo efectivo (valid_from / valid_to) y tiempo asertivo (asserted_from / asserted_to), con cierre "
                   "9999-12-31 23:59:59.99. Un trigger impide solapamientos y sólo permite retirar aserciones.")
        st.info("Caso de auditoría C46. El crédito OPER-COM-90210 se informó a la CMF con saldo de $45.000.000 al "
                "31-12-2024. El 05-02-2025 se aplicó retroactivamente un abono de $3.000.000 recibido el 28-12-2024 "
                "(asiento de ajuste con fecha valor 28-12-2024 y corrección bitemporal ligada a ese asiento).")
        c1, c2 = st.columns(2)
        with c1:
            biz_date = st.date_input("Fecha económica (tiempo efectivo)", value=date(2024, 12, 31))
        with c2:
            assertion_options = {
                "Según se informó a la CMF (15-01-2025)": "2025-01-15 12:00:00",
                "Según se conoce después del ajuste (10-02-2025)": "2025-02-10 12:00:00",
                "Hoy": db_layer.ts(db_layer.utc_now()),
                "Posterior al corte de migración (02-01-2027)": "2027-01-02 12:00:00",
            }
            sys_mode = st.radio("Momento de aserción (tiempo asertivo, UTC)", list(assertion_options), index=0)
        sys_ts = assertion_options[sys_mode]
        biz_ts = f"{biz_date:%Y-%m-%d} 12:00:00"
        tt = db_layer.query_bitemporal_time_travel(biz_ts, sys_ts)
        st.markdown(f"##### Cartera vigente al {biz_date:%d-%m-%Y}, según lo asertado al {sys_ts[:16]} UTC")
        st.dataframe(_fmt_money(tt[["contract_number", "partner_name", "product_type", "current_balance",
                                    "undrawn_commitment", "annual_nominal_rate", "contract_status", "change_type",
                                    "asserted_from", "asserted_to"]].rename(columns={
            "contract_number": "Operación", "partner_name": "Contraparte", "product_type": "Producto",
            "current_balance": "Saldo", "undrawn_commitment": "No girado", "annual_nominal_rate": "Tasa",
            "contract_status": "Estado", "change_type": "Origen", "asserted_from": "Asertado desde",
            "asserted_to": "Asertado hasta"}), ["Saldo", "No girado"]).format({"Tasa": "{:.2%}"}, subset=["Tasa"]),
            width="stretch", hide_index=True)
        st.caption("Las operaciones HIP-LEG-50100 y HIP-LEG-50101 se cargaron como aserción futura con corte "
                   "01-01-2027 00:00: son invisibles para toda consulta anterior a ese instante.")

        st.markdown("##### Historial bitemporal completo de OPER-COM-90210")
        hist = db_layer.get_contract_bitemporal_history(90210)
        st.dataframe(_fmt_money(hist[["current_balance", "annual_nominal_rate", "valid_from", "valid_to", "asserted_from",
                                      "asserted_to", "change_type", "change_reason", "source_journal_entry_id"]].rename(columns={
            "current_balance": "Saldo", "annual_nominal_rate": "Tasa", "valid_from": "Efectivo desde",
            "valid_to": "Efectivo hasta", "asserted_from": "Asertado desde", "asserted_to": "Asertado hasta",
            "change_type": "Operación AVF", "change_reason": "Motivo", "source_journal_entry_id": "Asiento"}),
            ["Saldo"]).format({"Tasa": "{:.2%}", "Asiento": "{:.0f}"}, na_rep="—"), width="stretch", hide_index=True)

    # ---------------------------------------------------------------------
    with tabs[2]:
        _section("Data warehouse dimensional", "Capa 4 · Kimball")
        st.caption("Hechos complementarios: snapshot periódico con reexpresiones (restatement_seq), postings "
                   "transaccionales con secuencia por cuenta y marca de cierre diario, y flujos contractuales por banda CMF.")
        facts, risk, partners = db_layer.get_dwh_facts_and_dims()
        st.markdown("##### Snapshot de saldos de operaciones nominadas (todas las versiones)")
        st.dataframe(_fmt_money(facts[["snapshot_date_key", "contract_number", "partner_name", "household_group_name",
                                       "cmf_rating_category", "balance_clp", "regulatory_provision_clp",
                                       "undrawn_commitment_clp", "pending_debits_clp", "hqla_level", "restatement_seq",
                                       "snapshot_asserted_at"]].rename(columns={
            "snapshot_date_key": "Fecha", "contract_number": "Operación", "partner_name": "Contraparte",
            "household_group_name": "Grupo económico", "cmf_rating_category": "Cat.", "balance_clp": "Saldo",
            "regulatory_provision_clp": "Provisión", "undrawn_commitment_clp": "No girado",
            "pending_debits_clp": "Reservado", "hqla_level": "HQLA", "restatement_seq": "Versión",
            "snapshot_asserted_at": "Conocido al (UTC)"}), ["Saldo", "Provisión", "No girado", "Reservado"]),
            width="stretch", hide_index=True, height=330)

        c1, c2 = st.columns(2)
        with c1:
            last = db_layer.latest_snapshot_date() or date.today()
            pos = st.date_input("Fecha de posición para flujos", value=min(date(2025, 5, 25), last),
                                min_value=db_layer.BOOK_START, max_value=last)
            st.markdown("##### Flujos contractuales proyectados")
            cf = db_layer.get_contractual_cashflows(pos)
            st.dataframe(_fmt_money(cf[["contract_number", "cashflow_date_key", "cashflow_type", "direction",
                                        "bucket_label", "total", "present_value_clp", "include_in_liquidity",
                                        "include_in_irrbb"]].rename(columns={
                "contract_number": "Operación", "cashflow_date_key": "Fecha flujo", "cashflow_type": "Tipo",
                "direction": "Sentido", "bucket_label": "Banda", "total": "Monto", "present_value_clp": "Valor presente",
                "include_in_liquidity": "Liquidez", "include_in_irrbb": "IRRBB"}), ["Monto", "Valor presente"]),
                width="stretch", hide_index=True, height=300)
        with c2:
            pit = st.date_input("Fecha para saldo point-in-time", value=date(2025, 1, 31),
                                min_value=db_layer.BOOK_START, max_value=last)
            st.markdown("##### Saldo point-in-time (Kimball fig. 9.5)")
            st.caption("Última transacción con marca de cierre diario por cuenta y contrato, con fecha contable "
                       "menor o igual a la consultada.")
            g = db_layer.get_point_in_time_gl_balances(pit)
            g = g[~g["contract_number"].str.startswith("POOL-")]
            st.dataframe(_fmt_money(g[["account_code", "account_name", "contract_number", "ultima_fecha_movimiento",
                                       "account_sequence_number", "saldo"]].rename(columns={
                "account_code": "Cuenta", "account_name": "Nombre", "contract_number": "Contrato",
                "ultima_fecha_movimiento": "Último movimiento", "account_sequence_number": "Secuencia",
                "saldo": "Saldo"}), ["Saldo"]), width="stretch", hide_index=True, height=300)

        c3, c4 = st.columns(2)
        with c3:
            st.markdown("##### Minidimensión de riesgo")
            st.dataframe(risk[["cmf_rating_category", "regulatory_status", "dpd_bucket", "provision_rate", "ltv_bucket"]]
                         .rename(columns={"cmf_rating_category": "Categoría", "regulatory_status": "Estado",
                                          "dpd_bucket": "Mora", "provision_rate": "Provisión", "ltv_bucket": "LTV"})
                         .style.format({"Provisión": "{:.2%}"}), width="stretch", hide_index=True)
        with c4:
            st.markdown("##### Dimensión de contrapartes")
            st.dataframe(partners[["partner_id", "tax_id", "full_name", "segment_name", "household_group_name"]]
                         .rename(columns={"partner_id": "ID", "tax_id": "RUT", "full_name": "Nombre",
                                          "segment_name": "Segmento", "household_group_name": "Grupo económico"}),
                         width="stretch", hide_index=True)

    # ---------------------------------------------------------------------
    with tabs[3]:
        _section("Control de cargas y conciliación", "Capa 2 · staging")
        st.markdown("##### Conciliación de tres vías (core · ledger · DWH)")
        st.caption("Diferencia aceptada: exactamente 0,0000. Las carteras agregadas mantienen su saldo sólo en el "
                   "ledger, por lo que se concilian ledger contra DWH.")
        st.dataframe(_fmt_money(recon[["reconciliation_date", "contract_number", "core_balance", "ledger_balance",
                                       "dwh_balance", "diff_core_ledger", "diff_ledger_dwh", "status"]].rename(columns={
            "reconciliation_date": "Fecha", "contract_number": "Contrato", "core_balance": "Core", "ledger_balance": "Ledger",
            "dwh_balance": "DWH", "diff_core_ledger": "Dif. core-ledger", "diff_ledger_dwh": "Dif. ledger-DWH",
            "status": "Estado"}), ["Core", "Ledger", "DWH", "Dif. core-ledger", "Dif. ledger-DWH"]),
            width="stretch", hide_index=True, height=320)
        st.markdown("##### Bitácora de ejecución de lotes")
        etl = db_layer.get_etl_execution_log()
        st.dataframe(etl.rename(columns={
            "batch_id": "Lote", "pipeline_name": "Proceso", "target_layer": "Capa", "source_entity": "Origen",
            "target_entity": "Destino", "extract_start_time": "Inicio (UTC)", "extract_end_time": "Fin (UTC)",
            "records_extracted": "Leídos", "records_loaded": "Cargados", "records_rejected": "Rechazados",
            "status": "Estado", "reconciliation_diff": "Diferencia", "error_message": "Observación"}),
            width="stretch", hide_index=True)

    # ---------------------------------------------------------------------
    with tabs[4]:
        _section("Reportes normativos", "C46 · C54 · C55 · C56")
        last = db_layer.latest_snapshot_date() or date.today()
        rep_date = st.date_input("Fecha de reporte", value=last, min_value=db_layer.BOOK_START, max_value=last,
                                 key="rep_date")

        st.markdown("##### C55 · LCR oficial con topes de composición")
        lcr, lcr_det = db_layer.compute_lcr_from_db(rep_date)
        nsfr, _ = db_layer.compute_nsfr_from_db(rep_date)
        k = st.columns(3) + st.columns(3)
        k[0].metric("Nivel 1", f"${float(lcr.level1) / 1e6:,.0f} MM")
        k[1].metric("Nivel 2A (post desc.)", f"${float(lcr.level2a_post_haircut) / 1e6:,.0f} MM")
        k[2].metric("Nivel 2B (post desc.)", f"${float(lcr.level2b_post_haircut) / 1e6:,.0f} MM")
        k[3].metric("Ajustes tope 15% / 40%",
                    f"${float(lcr.adjustment_15pct_cap + lcr.adjustment_40pct_cap) / 1e6:,.0f} MM")
        k[4].metric("Entradas excluidas (75%)", f"${float(lcr.inflow_cap_adjustment) / 1e6:,.0f} MM")
        k[5].metric("LCR / NSFR", f"{float(lcr.lcr_pct or 0):.2f}% / {float(nsfr.nsfr_pct or 0):.2f}%")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("##### C54 · Descalce de plazos (base contractual)")
            c54 = db_layer.get_c54_maturity_ladder(rep_date)
            st.dataframe(c54.rename(columns={"bucket_label": "Banda", "ingresos": "Ingresos", "egresos": "Egresos",
                                             "descalce": "Descalce", "descalce_acumulado": "Acumulado",
                                             "limite": "Límite", "cumple": "Cumple"})
                         .drop(columns=["bucket_code"]).style.format(
                {c: "${:,.0f}" for c in ["Ingresos", "Egresos", "Descalce", "Acumulado", "Límite"]}, na_rep="—"),
                width="stretch", hide_index=True)
        with c2:
            st.markdown("##### C56 · Riesgo de tasa en el libro de banca (IRRBB)")
            c56 = db_layer.get_c56_irrbb(rep_date)
            st.dataframe(c56.rename(columns={"escenario": "Escenario", "delta_eve": "Δ EVE", "delta_nii_12m": "Δ NII 12m",
                                             "delta_eve_pct_tier1": "Δ EVE / Tier 1", "valor_atipico": "Atípico"})
                         .style.format({"Δ EVE": "${:,.0f}", "Δ NII 12m": "${:,.0f}", "Δ EVE / Tier 1": "{:+.2f}%"}),
                         width="stretch", hide_index=True)

        st.markdown("##### C46 · Provisiones: informado versus reexpresado")
        c46_date = st.date_input("Cierre informado", value=date(2024, 12, 31), min_value=db_layer.BOOK_START,
                                 max_value=last, key="c46_date")
        c46 = db_layer.get_c46_provisions(c46_date, reported_at=datetime(2025, 1, 10, 12, 0))
        st.caption("Versión informada: la vigente al 10-01-2025 12:00 UTC (envío del archivo de diciembre).")
        st.dataframe(c46[["contract_number", "deudor", "categoria", "saldo_reportado", "saldo_hoy", "prov_reportada",
                          "prov_hoy", "delta_provision", "diagnostico"]].rename(columns={
            "contract_number": "Operación", "deudor": "Deudor", "categoria": "Cat.", "saldo_reportado": "Saldo informado",
            "saldo_hoy": "Saldo vigente", "prov_reportada": "Provisión informada", "prov_hoy": "Provisión vigente",
            "delta_provision": "Diferencia", "diagnostico": "Diagnóstico"}).style.format(
            {c: "${:,.0f}" for c in ["Saldo informado", "Saldo vigente", "Provisión informada", "Provisión vigente",
                                     "Diferencia"]}), width="stretch", hide_index=True)

    # ---------------------------------------------------------------------
    with tabs[5]:
        _section("Scripts T-SQL de producción", "SQL Server 2017")
        sql_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sql")
        sql_files = sorted(f for f in os.listdir(sql_dir) if f.endswith(".sql"))
        script_sel = st.selectbox("Script", sql_files, index=0)
        with open(os.path.join(sql_dir, script_sel), "r", encoding="utf-8") as f:
            code_content = f.read()
        st.download_button(label=f"Descargar {script_sel}", data=code_content, file_name=script_sel, mime="text/plain")
        st.code(code_content, language="sql")
