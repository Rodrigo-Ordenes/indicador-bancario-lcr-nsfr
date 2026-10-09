"""
components/core_architecture_view.py
====================================
Vista ejecutiva e interactiva de la Arquitectura de 4 Capas y Auditoría Bitemporal:
1. OLTP / Ledger Inmutable (Multi-leg & Hash Chaining SHA-256)
2. Staging & Control Layer (Auditoría ETL, Linaje y Reconciliación)
3. Core Bitemporal / ODS (Time-Travel para Auditoría CMF C46/C55)
4. Data Warehouse Kimball (Minidimensiones, CCI y Particionamiento)
5. Visor interactivo de scripts T-SQL de producción (SQL Server 2017)
"""

import os
import streamlit as st
import pandas as pd
from datetime import datetime, date
import db_layer

def render_core_architecture_page():
    # Asegurar inicialización de la base de datos local
    db_layer.init_database()

    st.markdown("""
    <div style="margin-bottom: 20px;">
        <h2 style="color: #ffffff; font-family: 'JetBrains Mono', monospace; margin: 0;">
            🏛️ ARQUITECTURA DE DATOS BANCARIA & AUDITORÍA BITEMPORAL
        </h2>
        <div style="color: #94a3b8; font-size: 13px; margin-top: 4px;">
            Plataforma Enterprise SQL Server 2017 | Metodología Kimball | Cumplimiento Normativo CMF & Basilea III
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Métricas de Resumen de la Arquitectura
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        entries_df = db_layer.get_ledger_entries()
        st.metric(label="Asientos en Ledger Inmutable", value=f"{len(entries_df)} Asientos", delta="Append-Only Activo")
    with m2:
        is_chain_valid, _ = db_layer.verify_ledger_cryptographic_chain()
        chain_status = "● 100% VÁLIDO" if is_chain_valid else "● ALTERACIÓN DETECTADA"
        st.metric(label="Cadena Criptográfica SHA-256", value=chain_status, delta="Sin mutaciones")
    with m3:
        st.metric(label="Modelo ODS Bitemporal", value="System + Valid Time", delta="CMF Time-Travel OK")
    with m4:
        st.metric(label="Presentación Kimball DWH", value="CCI + Minidimensiones", delta="Batch Mode Activo")

    st.markdown("<div style='margin-top: 20px;'></div>", unsafe_allow_html=True)

    # Pestañas de las 4 Capas + Visor de Código SQL
    tab_ledger, tab_bitemporal, tab_dwh, tab_etl, tab_sql = st.tabs([
        "🔐 1. Ledger Inmutable (OLTP)",
        "⏳ 2. Auditoría Bitemporal (ODS)",
        "📊 3. DWH Dimensional (Kimball)",
        "🔄 4. Control ETL & Reconciliación",
        "📜 5. Scripts T-SQL Producción"
    ])

    # =========================================================================
    # TAB 1: LEDGER INMUTABLE
    # =========================================================================
    with tab_ledger:
        st.markdown("### 🔐 Capa 1: Ledger Contable Inmutable (Data Plane)")
        st.caption(
            "Garantiza inmutabilidad estricta sin Ledger nativo en SQL Server 2017 combinando "
            "DENY UPDATE/DELETE, triggers INSTEAD OF, contabilidad multi-leg y encadenamiento hash SHA-256."
        )

        col_led1, col_led2 = st.columns([2, 1])
        with col_led1:
            st.markdown("#### 📖 Cabeceras Contables (`oltp.journal_entry`)")
            st.dataframe(
                entries_df[['journal_entry_id', 'posting_date', 'transaction_code', 'description', 
                            'is_balanced', 'prev_entry_hash', 'entry_hash']],
                width='stretch',
                hide_index=True
            )

        with col_led2:
            st.markdown("#### 🛡️ Verificación Criptográfica Forense")
            st.markdown(
                "Verifica que el hash `entry_hash` de cada bloque esté matemáticamente encadenado "
                "con el `prev_entry_hash` del bloque anterior sin alteraciones físicas."
            )
            btn_verify = st.button("🔐 Ejecutar Verificación SHA-256", key="btn_verify_ledger")
            
            is_valid, audit_df = db_layer.verify_ledger_cryptographic_chain()
            if btn_verify or True:
                if is_valid:
                    st.success("✅ **Integridad Garantizada:** Todos los bloques SHA-256 coinciden exactamente con su predecesor.")
                else:
                    st.error("🚨 **Alerta Forense:** Se detectó una inconsistencia en la cadena de firmas.")
                st.dataframe(audit_df, width='stretch', hide_index=True)

        st.markdown("---")
        st.markdown("#### 📑 Detalle de Postings Multi-Leg (`oltp.ledger_posting`)")
        st.caption("Verificación de la invariante: $\\sum Débitos - \\sum Créditos = 0$")
        postings_df = db_layer.get_ledger_postings()
        st.dataframe(
            postings_df[['posting_id', 'journal_entry_id', 'line_number', 'account_id', 'contract_id', 
                         'currency_code', 'amount_debit', 'amount_credit', 'functional_debit', 'functional_credit', 'row_hash']].style.format({
                'amount_debit': '${:,.2f}',
                'amount_credit': '${:,.2f}',
                'functional_debit': '${:,.2f}',
                'functional_credit': '${:,.2f}'
            }),
            width='stretch',
            hide_index=True
        )

    # =========================================================================
    # TAB 2: AUDITORÍA BITEMPORAL
    # =========================================================================
    with tab_bitemporal:
        st.markdown("### ⏳ Capa 3: Core Bitemporal / ODS (Asserted Versioning)")
        st.markdown(
            "Combina **System Transaction Time** (`SysStartTime`, `SysEndTime` nativos) con "
            "**Business Effective Time** (`ValidFrom`, `ValidTo`) para resolver requerimientos regulatorios CMF (C46/C55)."
        )

        st.info(
            "💡 **Escenario de Prueba Forense Regulatoria:**\n"
            "El crédito `#90210` de Distribuidora Central tenía al corte económico del **31-Dic-2024** un saldo original de **$45,000,000** "
            "(reportado formalmente el **15-Ene-2025**). En Febrero se aplicó un abono extemporáneo retroactivo que redujo el saldo a **$42,000,000**. "
            "Utiliza los controles inferiores para viajar en el tiempo y contrastar ambas aserciones."
        )

        c_time1, c_time2 = st.columns(2)
        with c_time1:
            biz_date = st.date_input("1. Fecha Económica del Negocio (Valid Time):", value=date(2024, 12, 31))
        with c_time2:
            sys_mode = st.radio(
                "2. Momento de Aserción del Sistema (Transaction Time):",
                ["Como se reportó originalmente a CMF (15-Ene-2025)", "Como se conoce hoy con ajustes retrospectivos (Actual)"],
                index=0
            )

        sys_timestamp = "2025-01-15 12:00:00" if "15-Ene" in sys_mode else "2025-02-10 12:00:00"
        biz_timestamp = f"{biz_date.strftime('%Y-%m-%d')} 12:00:00"

        tt_results = db_layer.query_bitemporal_time_travel(biz_timestamp, sys_timestamp)

        st.markdown(f"#### 🔍 Cartera Visible al corte económico `{biz_date}` bajo aserción `{sys_timestamp}`:")
        st.dataframe(
            tt_results[['contract_id', 'contract_number', 'partner_rut', 'partner_name', 'product_type', 
                        'current_balance', 'annual_nominal_rate', 'contract_status', 'sys_start_time', 'sys_end_time']].style.format({
                'current_balance': '${:,.2f}',
                'annual_nominal_rate': '{:.2%}'
            }),
            width='stretch',
            hide_index=True
        )

        st.markdown("#### ⚖️ Cuadro Comparativo de Discrepancias Regulatorias:")
        rep_orig = db_layer.query_bitemporal_time_travel(biz_timestamp, "2025-01-15 12:00:00")
        rep_today = db_layer.query_bitemporal_time_travel(biz_timestamp, "2025-02-10 12:00:00")
        
        merged_audit = pd.merge(
            rep_orig[['contract_id', 'contract_number', 'current_balance', 'contract_status']],
            rep_today[['contract_id', 'current_balance', 'contract_status']],
            on='contract_id',
            suffixes=('_reportado_original', '_conocido_hoy')
        )
        merged_audit['delta_discrepancia'] = merged_audit['current_balance_conocido_hoy'] - merged_audit['current_balance_reportado_original']
        merged_audit['diagnostico_cmf'] = np.where(
            merged_audit['delta_discrepancia'] != 0,
            "AJUSTE_EXTEMPORANEO_POST_CORTE",
            "CONSISTENTE_SIN_OBSERVACIONES"
        )

        st.dataframe(
            merged_audit.rename(columns={
                'contract_id': 'ID Contrato',
                'contract_number': 'N° Operación',
                'current_balance_reportado_original': 'Saldo Declarado a CMF ($)',
                'current_balance_conocido_hoy': 'Saldo Real Hoy ($)',
                'delta_discrepancia': 'Diferencia ($)',
                'diagnostico_cmf': 'Diagnóstico Forense'
            }).style.format({
                'Saldo Declarado a CMF ($)': '${:,.2f}',
                'Saldo Real Hoy ($)': '${:,.2f}',
                'Diferencia ($)': '${:+,.2f}'
            }),
            width='stretch',
            hide_index=True
        )

    # =========================================================================
    # TAB 3: DWH DIMENSIONAL KIMBALL
    # =========================================================================
    with tab_dwh:
        st.markdown("### 📊 Capa 4: Data Warehouse & Data Marts Dimensionales (Ralph Kimball)")
        st.caption(
            "Presentación dimensional con granularidad atómica, Clustered Columnstore Indexes (CCI) "
            "y minidimensiones de riesgo para prevenir monstruos dimensionales SCD Tipo 2."
        )

        facts_df, risk_df, partners_df = db_layer.get_dwh_facts_and_dims()

        col_dwh1, col_dwh2 = st.columns([1, 1])
        with col_dwh1:
            st.markdown("#### 🎯 Minidimensión de Riesgo CMF (`dwh.dim_risk_band`)")
            st.caption("Valores discretos de clasificación CMF (A1..D2), DPD y tasa de provisión.")
            st.dataframe(
                risk_df[['risk_band_key', 'cmf_rating_category', 'regulatory_status', 'dpd_bucket', 'provision_rate', 'ltv_bucket']].style.format({
                    'provision_rate': '{:.2%}'
                }),
                width='stretch',
                hide_index=True
            )

        with col_dwh2:
            st.markdown("#### 👥 Dimensión Conformada de Socios (`dwh.dim_partner`)")
            st.caption("Atributos demográficos estables gestionados bajo SCD Tipo 2.")
            st.dataframe(
                partners_df[['partner_key', 'partner_id', 'tax_id', 'full_name', 'segment_name', 'is_current']],
                width='stretch',
                hide_index=True
            )

        st.markdown("---")
        st.markdown("#### 📈 Tabla de Hechos: Snapshot Periódico de Saldos (`dwh.fact_account_balance_snapshot`)")
        st.caption("Optimizada con Clustered Columnstore Index (CCI) y particionamiento mensual para reportería C46 y C55.")
        st.dataframe(
            facts_df.rename(columns={
                'snapshot_date_key': 'Fecha Corte',
                'contract_id': 'Contrato',
                'partner_name': 'Cliente / Deudor',
                'partner_rut': 'RUT',
                'cmf_rating_category': 'Cat CMF',
                'regulatory_status': 'Estado Riesgo',
                'current_balance_clp': 'Saldo Cartera (CLP)',
                'regulatory_provision': 'Provisión C46 (CLP)',
                'is_hqla_eligible': 'Es HQLA',
                'hqla_level': 'Nivel HQLA',
                'nsfr_weighted_rsf_clp': 'FER Ponderado (CLP)'
            }).style.format({
                'Saldo Cartera (CLP)': '${:,.0f}',
                'Provisión C46 (CLP)': '${:,.0f}',
                'FER Ponderado (CLP)': '${:,.0f}'
            }),
            width='stretch',
            hide_index=True
        )

    # =========================================================================
    # TAB 4: CONTROL ETL & RECONCILIACIÓN
    # =========================================================================
    with tab_etl:
        st.markdown("### 🔄 Capa 2: Staging & Control Layer (Kimball Kitchen)")
        st.caption(
            "Gestión del linaje de datos, auditoría de ejecución de lotes, recuentos de cuadre "
            "y reconciliación nocturna entre el Core Legado y el nuevo Ledger Inmutable."
        )

        etl_df = db_layer.get_etl_execution_log()
        st.dataframe(
            etl_df.rename(columns={
                'batch_id': 'ID Lote',
                'pipeline_name': 'Pipeline ETL',
                'target_layer': 'Capa Destino',
                'source_entity': 'Entidad Origen',
                'target_entity': 'Entidad Destino',
                'extract_start_time': 'Inicio',
                'extract_end_time': 'Fin',
                'records_extracted': 'Extraídos',
                'records_loaded': 'Cargados',
                'records_rejected': 'Rechazados',
                'status': 'Estado Lote',
                'reconciliation_diff': 'Diferencia ($)'
            }),
            width='stretch',
            hide_index=True
        )

    # =========================================================================
    # TAB 5: SCRIPTS T-SQL DE PRODUCCIÓN
    # =========================================================================
    with tab_sql:
        st.markdown("### 📜 Scripts T-SQL de Nivel de Producción (SQL Server 2017)")
        st.caption("Scripts listos para ejecutar en SQL Server Management Studio (SSMS) o automatizar vía CI/CD.")

        sql_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sql")
        sql_files = sorted([f for f in os.listdir(sql_dir) if f.endswith(".sql")])

        script_sel = st.selectbox("Seleccionar Script para Inspeccionar:", sql_files, index=0)
        
        script_path = os.path.join(sql_dir, script_sel)
        if os.path.exists(script_path):
            with open(script_path, "r", encoding="utf-8") as f:
                code_content = f.read()
            
            col_b1, col_b2 = st.columns([1, 4])
            with col_b1:
                st.download_button(
                    label=f"📥 Descargar {script_sel}",
                    data=code_content,
                    file_name=script_sel,
                    mime="text/plain"
                )
            st.code(code_content, language="sql")
