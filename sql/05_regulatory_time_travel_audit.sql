-- =============================================================================
-- 05_regulatory_time_travel_audit.sql
-- =============================================================================
-- Vistas y funciones de reportería regulatoria y auditoría (CMF / Basilea III)
-- sobre el DWH Kimball (script 04) y el core bitemporal (script 03).
--
-- Regla de consistencia: cada objeto reproduce exactamente la lógica de la
-- implementación de referencia en Python (regulatory_engine.py, db_layer.py y
-- analytics.py). data_loader.py, en modo SQL Server, recalcula LCR y NSFR desde
-- dwh.vw_c55_*_components_daily y alerta si difiere de dwh.vw_cmf_c55_lcr_nsfr_official.
--
-- Contenido
--   1. Versión vigente del snapshot (última reexpresión) y "tal como se sabía".
--   2. dwh.vw_c55_lcr_components_daily  : líneas de ALAC y NAF por día.
--   3. dwh.vw_c55_nsfr_components_daily : líneas de FED y FER por día.
--   4. dwh.vw_cmf_c55_lcr_nsfr_official : LCR y NSFR con topes del Anexo 1 BCBS 238.
--   5. C46: dwh.vw_cmf_c46_provisions, dwh.itvf_c46_reported_vs_restated y
--      core.usp_audit_c46_as_was_vs_as_is (auditoría bitemporal en el ODS).
--   6. C54: dwh.itvf_c54_maturity_ladder y dwh.vw_cmf_c54_maturity_ladder_gaps.
--   7. C56: dwh.itvf_c56_irrbb y dwh.vw_cmf_c56_irrbb_eve_nii_sensitivity.
--   8. Kimball fig. 9.5: dwh.itvf_point_in_time_balance.
--
-- Convención de signos en el detalle LCR: las entradas se publican con signo
-- negativo dentro del NAF (flow_tag = 'IN'), de modo que la suma del detalle más
-- el ajuste por el tope del 75% es exactamente el NAF.
-- =============================================================================

USE [CoreBanking];
GO

DROP VIEW      IF EXISTS [dwh].[vw_cmf_c56_irrbb_eve_nii_sensitivity];
DROP FUNCTION  IF EXISTS [dwh].[itvf_c56_irrbb];
DROP VIEW      IF EXISTS [dwh].[vw_cmf_c54_maturity_ladder_gaps];
DROP FUNCTION  IF EXISTS [dwh].[itvf_c54_maturity_ladder];
DROP PROCEDURE IF EXISTS [core].[usp_audit_c46_as_was_vs_as_is];
DROP FUNCTION  IF EXISTS [dwh].[itvf_c46_reported_vs_restated];
DROP VIEW      IF EXISTS [dwh].[vw_cmf_c46_provisions];
DROP VIEW      IF EXISTS [dwh].[vw_cmf_c55_lcr_nsfr_official];
DROP VIEW      IF EXISTS [dwh].[vw_c55_nsfr_components_daily];
DROP VIEW      IF EXISTS [dwh].[vw_c55_lcr_components_daily];
DROP FUNCTION  IF EXISTS [dwh].[itvf_point_in_time_balance];
DROP FUNCTION  IF EXISTS [dwh].[itvf_snapshot_as_known];
DROP VIEW      IF EXISTS [dwh].[vw_snapshot_latest];
-- Objetos de versiones anteriores de este script
DROP VIEW      IF EXISTS [dwh].[vw_c56_irrbb_summary];
DROP PROCEDURE IF EXISTS [dwh].[usp_audit_c46_provisions_time_travel];
GO

-- =============================================================================
-- 1. VERSIONES DEL SNAPSHOT
-- =============================================================================

-- Última reexpresión de cada (fecha, contrato): la cifra vigente hoy.
CREATE VIEW [dwh].[vw_snapshot_latest]
AS
SELECT s.*
FROM [dwh].[fact_account_balance_snapshot] s
WHERE s.[restatement_seq] = (SELECT MAX(s2.[restatement_seq])
                             FROM [dwh].[fact_account_balance_snapshot] s2
                             WHERE s2.[snapshot_date_key] = s.[snapshot_date_key]
                               AND s2.[contract_key] = s.[contract_key]);
GO

-- Versión "tal como se sabía" en un instante: reproduce un reporte ya enviado.
CREATE FUNCTION [dwh].[itvf_snapshot_as_known] (@p_known_at DATETIME2(2))
RETURNS TABLE
AS
RETURN
    SELECT s.*
    FROM [dwh].[fact_account_balance_snapshot] s
    WHERE s.[restatement_seq] = (SELECT MAX(s2.[restatement_seq])
                                 FROM [dwh].[fact_account_balance_snapshot] s2
                                 WHERE s2.[snapshot_date_key] = s.[snapshot_date_key]
                                   AND s2.[contract_key] = s.[contract_key]
                                   AND s2.[snapshot_asserted_at] <= @p_known_at);
GO

-- =============================================================================
-- 2. COMPONENTES DIARIOS DEL LCR (ALAC y NAF)
--    Etiqueta de línea: nombre del producto para carteras agregadas (POOL-*),
--    producto + número de operación para contratos nominados.
-- =============================================================================
CREATE VIEW [dwh].[vw_c55_lcr_components_daily]
AS
WITH base AS (
    SELECT s.[snapshot_date_key], s.[contract_key], s.[restatement_seq], s.[balance_clp], s.[market_value_clp],
           s.[undrawn_commitment_clp], s.[pending_debits_clp], pr.[balance_side],
           t.[lcr_component], t.[lcr_category], t.[hqla_level], t.[hqla_haircut], t.[lcr_basis], t.[lcr_rate],
           t.[undrawn_runoff_rate],
           CAST(CASE WHEN c.[contract_number] LIKE 'POOL%' THEN pr.[product_name]
                     ELSE CONCAT(pr.[product_name], N' ', c.[contract_number]) END AS NVARCHAR(200)) AS [label],
           -- Reservas en dos fases sólo cuentan como salida en contratos de activo o contingentes.
           CASE WHEN pr.[balance_side] IN ('ASSET','OFF_BALANCE') THEN s.[pending_debits_clp] ELSE 0 END AS [pending_out]
    FROM [dwh].[vw_snapshot_latest] s
    JOIN [dwh].[dim_account_contract] c ON c.[contract_key] = s.[contract_key]
    JOIN [dwh].[dim_product] pr ON pr.[product_key] = s.[product_key]
    JOIN [dwh].[dim_liquidity_treatment] t ON t.[liquidity_treatment_key] = s.[liquidity_treatment_key]
),
cf30 AS (
    SELECT [position_date_key], [contract_key], [restatement_seq],
           SUM(CASE WHEN [direction] = 'OUTFLOW' THEN [total_amount] ELSE 0 END) AS [out_30d],
           SUM(CASE WHEN [direction] = 'INFLOW'  THEN [total_amount] ELSE 0 END) AS [in_30d]
    FROM [dwh].[fact_contractual_cashflows]
    WHERE [include_in_liquidity] = 1 AND [days_to_cashflow] <= 30
    GROUP BY [position_date_key], [contract_key], [restatement_seq]
),
lines AS (
    -- ALAC: valor de mercado x (1 - descuento)
    SELECT b.[snapshot_date_key], CAST('ALAC' AS VARCHAR(8)) AS [componente], b.[lcr_category] AS [categoria],
           b.[label] AS [subcategoria], b.[hqla_level], CAST('ALAC' AS VARCHAR(8)) AS [flow_tag],
           b.[market_value_clp] AS [monto_nominal_clp], CAST(1 - b.[hqla_haircut] AS DECIMAL(12,6)) AS [factor_ponderacion]
    FROM base b
    WHERE b.[lcr_component] = 'ALAC' AND b.[market_value_clp] <> 0
    UNION ALL
    -- Salidas: saldo (depósitos sin vencimiento) o flujos que vencen en 30 días
    SELECT b.[snapshot_date_key], 'NAF', b.[lcr_category],
           CASE WHEN b.[lcr_basis] = 'BALANCE' THEN b.[label] ELSE CONCAT(b.[label], N' (vence <= 30 días)') END,
           'NO_HQLA', 'OUT',
           CASE WHEN b.[lcr_basis] = 'BALANCE' THEN b.[balance_clp] ELSE ISNULL(cf.[out_30d], 0) END, b.[lcr_rate]
    FROM base b
    LEFT JOIN cf30 cf ON cf.[position_date_key] = b.[snapshot_date_key] AND cf.[contract_key] = b.[contract_key]
                     AND cf.[restatement_seq] = b.[restatement_seq]
    WHERE b.[lcr_component] = 'OUTFLOW'
      AND CASE WHEN b.[lcr_basis] = 'BALANCE' THEN b.[balance_clp] ELSE ISNULL(cf.[out_30d], 0) END <> 0
    UNION ALL
    -- Entradas contractuales a 30 días (signo negativo dentro del NAF)
    SELECT b.[snapshot_date_key], 'NAF', b.[lcr_category], CONCAT(b.[label], N' (cobros <= 30 días)'),
           'NO_HQLA', 'IN', -ISNULL(cf.[in_30d], 0), b.[lcr_rate]
    FROM base b
    LEFT JOIN cf30 cf ON cf.[position_date_key] = b.[snapshot_date_key] AND cf.[contract_key] = b.[contract_key]
                     AND cf.[restatement_seq] = b.[restatement_seq]
    WHERE b.[lcr_component] = 'INFLOW' AND ISNULL(cf.[in_30d], 0) <> 0
    UNION ALL
    -- Compromisos no girados: la porción libre (no reservada) con su tasa de salida
    SELECT b.[snapshot_date_key], 'NAF', N'Compromisos de Liquidez y Crédito', CONCAT(b.[label], N': monto no girado'),
           'NO_HQLA', 'OUT',
           CASE WHEN b.[undrawn_commitment_clp] > b.[pending_out] THEN b.[undrawn_commitment_clp] - b.[pending_out] ELSE 0 END,
           b.[undrawn_runoff_rate]
    FROM base b
    WHERE b.[undrawn_commitment_clp] > 0 AND b.[undrawn_runoff_rate] > 0
    UNION ALL
    -- Giros reservados (dos fases): salida al 100%
    SELECT b.[snapshot_date_key], 'NAF', N'Compromisos de Liquidez y Crédito',
           CONCAT(b.[label], N': giros reservados (pending)'), 'NO_HQLA', 'OUT', b.[pending_out], CAST(1 AS DECIMAL(12,6))
    FROM base b
    WHERE b.[pending_out] > 0
)
SELECT [snapshot_date_key], [componente], [categoria], [subcategoria], [hqla_level], [flow_tag],
       SUM([monto_nominal_clp]) AS [monto_nominal_clp], [factor_ponderacion],
       SUM(CAST(ROUND([monto_nominal_clp] * [factor_ponderacion], 4) AS DECIMAL(19,4))) AS [monto_ponderado_clp]
FROM lines
GROUP BY [snapshot_date_key], [componente], [categoria], [subcategoria], [hqla_level], [flow_tag], [factor_ponderacion];
GO

-- =============================================================================
-- 3. COMPONENTES DIARIOS DEL NSFR (FED y FER)
--    Carteras agregadas: factor = promedio de factores por banda ponderado por el
--    perfil de vencimientos. Contratos nominados: banda por vencimiento residual
--    (< 182 días, 182-364 días, >= 365 días; sin vencimiento = >= 1 año).
-- =============================================================================
CREATE VIEW [dwh].[vw_c55_nsfr_components_daily]
AS
WITH base AS (
    SELECT s.[snapshot_date_key], s.[balance_clp], s.[undrawn_commitment_clp], s.[residual_maturity_days],
           c.[contract_number], pr.[product_code], t.[nsfr_side], t.[nsfr_category], t.[nsfr_factor_lt_6m],
           t.[nsfr_factor_6m_1y], t.[nsfr_factor_ge_1y], t.[undrawn_rsf_factor],
           CAST(CASE WHEN c.[contract_number] LIKE 'POOL%' THEN pr.[product_name]
                     ELSE CONCAT(pr.[product_name], N' ', c.[contract_number]) END AS NVARCHAR(200)) AS [label]
    FROM [dwh].[vw_snapshot_latest] s
    JOIN [dwh].[dim_account_contract] c ON c.[contract_key] = s.[contract_key]
    JOIN [dwh].[dim_product] pr ON pr.[product_key] = s.[product_key]
    JOIN [dwh].[dim_liquidity_treatment] t ON t.[liquidity_treatment_key] = s.[liquidity_treatment_key]
),
lines AS (
    SELECT b.[snapshot_date_key], CAST(CASE WHEN b.[nsfr_side] = 'ASF' THEN 'FED' ELSE 'FER' END AS VARCHAR(8)) AS [componente],
           b.[nsfr_category] AS [categoria], b.[label] AS [subcategoria], b.[balance_clp] AS [monto_nominal_clp],
           CAST(COALESCE(lad.[factor],
                CASE WHEN b.[residual_maturity_days] IS NULL OR b.[residual_maturity_days] >= 365 THEN b.[nsfr_factor_ge_1y]
                     WHEN b.[residual_maturity_days] >= 182 THEN b.[nsfr_factor_6m_1y]
                     ELSE b.[nsfr_factor_lt_6m] END) AS DECIMAL(12,6)) AS [factor_ponderacion]
    FROM base b
    OUTER APPLY (
        SELECT SUM(lp.[share] * CASE tb.[nsfr_maturity_band] WHEN 'LT_6M' THEN b.[nsfr_factor_lt_6m]
                                                             WHEN '6M_1Y' THEN b.[nsfr_factor_6m_1y]
                                                             ELSE b.[nsfr_factor_ge_1y] END) AS [factor]
        FROM [dwh].[behavioral_ladder_profile] lp
        JOIN [dwh].[dim_time_bucket] tb ON tb.[bucket_code] = lp.[bucket_code]
        WHERE lp.[product_code] = b.[product_code] AND b.[contract_number] LIKE 'POOL%'
        HAVING COUNT(*) > 0
    ) lad
    WHERE b.[nsfr_side] IN ('ASF','RSF') AND b.[balance_clp] <> 0
    UNION ALL
    -- Líneas comprometidas no giradas: 5% RSF (BCBS 295 párr. 47)
    SELECT b.[snapshot_date_key], 'FER', N'Líneas Comprometidas No Utilizadas (5% RSF)',
           CONCAT(b.[label], N': monto no girado'), b.[undrawn_commitment_clp], b.[undrawn_rsf_factor]
    FROM base b
    WHERE b.[undrawn_commitment_clp] > 0 AND b.[undrawn_rsf_factor] > 0
)
SELECT [snapshot_date_key], [componente], [categoria], [subcategoria],
       SUM([monto_nominal_clp]) AS [monto_nominal_clp], [factor_ponderacion],
       SUM(CAST(ROUND([monto_nominal_clp] * [factor_ponderacion], 4) AS DECIMAL(19,4))) AS [monto_ponderado_clp]
FROM lines
GROUP BY [snapshot_date_key], [componente], [categoria], [subcategoria], [factor_ponderacion];
GO

-- =============================================================================
-- 4. LCR Y NSFR OFICIALES (C55)
--    Anexo 1 BCBS 238, sobre montos ya ponderados:
--      Adj15 = max(L2B - 15/85 (L1 + L2A), L2B - 15/60 L1, 0)
--      Adj40 = max(L2A + L2B - Adj15 - 2/3 L1, 0)
--      HQLA  = L1 + L2A + L2B - Adj15 - Adj40
--      NAF   = Salidas - min(Entradas, 75% Salidas)
--    Las fracciones se calculan con coeficientes enteros ((85 L2B - 15 (L1+L2A)) / 85)
--    para no perder precisión decimal antes del redondeo a 4 decimales.
-- =============================================================================
CREATE VIEW [dwh].[vw_cmf_c55_lcr_nsfr_official]
AS
WITH lcr AS (
    SELECT [snapshot_date_key],
           SUM(CASE WHEN [componente] = 'ALAC' AND [hqla_level] = 'L1'  THEN [monto_ponderado_clp] ELSE 0 END) AS [l1],
           SUM(CASE WHEN [componente] = 'ALAC' AND [hqla_level] = 'L2A' THEN [monto_ponderado_clp] ELSE 0 END) AS [l2a],
           SUM(CASE WHEN [componente] = 'ALAC' AND [hqla_level] = 'L2B' THEN [monto_ponderado_clp] ELSE 0 END) AS [l2b],
           SUM(CASE WHEN [flow_tag] = 'OUT' THEN [monto_ponderado_clp] ELSE 0 END) AS [outflows],
           -SUM(CASE WHEN [flow_tag] = 'IN' THEN [monto_ponderado_clp] ELSE 0 END) AS [inflows]
    FROM [dwh].[vw_c55_lcr_components_daily]
    GROUP BY [snapshot_date_key]
),
adj15 AS (
    SELECT l.*,
           CAST(ROUND((SELECT MAX(v) FROM (VALUES ((85 * l.[l2b] - 15 * (l.[l1] + l.[l2a])) / 85.0),
                                                  ((60 * l.[l2b] - 15 * l.[l1]) / 60.0),
                                                  (0.0)) x(v)), 4) AS DECIMAL(19,4)) AS [adjustment_15pct_cap],
           CAST(ROUND(0.75 * l.[outflows], 4) AS DECIMAL(19,4)) AS [inflow_cap]
    FROM lcr l
),
adj40 AS (
    SELECT a.*,
           CAST(ROUND((SELECT MAX(v) FROM (VALUES ((3 * (a.[l2a] + a.[l2b] - a.[adjustment_15pct_cap]) - 2 * a.[l1]) / 3.0),
                                                  (0.0)) x(v)), 4) AS DECIMAL(19,4)) AS [adjustment_40pct_cap],
           CASE WHEN a.[inflows] < a.[inflow_cap] THEN a.[inflows] ELSE a.[inflow_cap] END AS [capped_inflows]
    FROM adj15 a
),
nsfr AS (
    SELECT [snapshot_date_key],
           SUM(CASE WHEN [componente] = 'FED' THEN [monto_ponderado_clp] ELSE 0 END) AS [asf],
           SUM(CASE WHEN [componente] = 'FER' THEN [monto_ponderado_clp] ELSE 0 END) AS [rsf]
    FROM [dwh].[vw_c55_nsfr_components_daily]
    GROUP BY [snapshot_date_key]
)
SELECT a.[snapshot_date_key],
       a.[l1] AS [level1_clp], a.[l2a] AS [level2a_post_haircut_clp], a.[l2b] AS [level2b_post_haircut_clp],
       a.[adjustment_15pct_cap] AS [adjustment_15pct_cap_clp], a.[adjustment_40pct_cap] AS [adjustment_40pct_cap_clp],
       a.[l1] + a.[l2a] + a.[l2b] - a.[adjustment_15pct_cap] - a.[adjustment_40pct_cap] AS [hqla_clp],
       a.[outflows] AS [gross_outflows_clp], a.[inflows] AS [gross_inflows_clp], a.[capped_inflows] AS [capped_inflows_clp],
       a.[inflows] - a.[capped_inflows] AS [inflow_cap_adjustment_clp],
       a.[outflows] - a.[capped_inflows] AS [net_cash_outflows_clp],
       CAST(CASE WHEN a.[outflows] - a.[capped_inflows] > 0
                 THEN ROUND((a.[l1] + a.[l2a] + a.[l2b] - a.[adjustment_15pct_cap] - a.[adjustment_40pct_cap]) * 100
                            / (a.[outflows] - a.[capped_inflows]), 4) END AS DECIMAL(12,4)) AS [lcr_pct],
       n.[asf] AS [fed_clp], n.[rsf] AS [fer_clp],
       CAST(CASE WHEN n.[rsf] > 0 THEN ROUND(n.[asf] * 100 / n.[rsf], 4) END AS DECIMAL(12,4)) AS [nsfr_pct]
FROM adj40 a
JOIN nsfr n ON n.[snapshot_date_key] = a.[snapshot_date_key];
GO

-- =============================================================================
-- 5. C46 · CARTERA Y PROVISIONES
-- =============================================================================

-- 5.1 Provisiones vigentes (última reexpresión) de colocaciones.
CREATE VIEW [dwh].[vw_cmf_c46_provisions]
AS
SELECT s.[snapshot_date_key], c.[contract_number], p.[tax_id] AS [rut_deudor], p.[full_name] AS [deudor],
       p.[household_group_name] AS [grupo_economico], pr.[product_name] AS [producto], rb.[cmf_rating_category] AS [categoria],
       rb.[regulatory_status] AS [estado_riesgo], rb.[dpd_bucket] AS [tramo_mora], rb.[provision_rate] AS [tasa_provision],
       s.[balance_clp] AS [saldo_clp], s.[collateral_value_clp] AS [garantia_clp], s.[net_exposure_clp] AS [exposicion_neta_clp],
       s.[regulatory_provision_clp] AS [provision_clp], s.[restatement_seq] AS [version], s.[snapshot_asserted_at] AS [conocido_al]
FROM [dwh].[vw_snapshot_latest] s
JOIN [dwh].[dim_account_contract] c ON c.[contract_key] = s.[contract_key]
JOIN [dwh].[dim_product] pr ON pr.[product_key] = s.[product_key] AND pr.[is_loan] = 1
JOIN [dwh].[dim_partner] p ON p.[partner_key] = s.[partner_key]
JOIN [dwh].[dim_risk_band] rb ON rb.[risk_band_key] = s.[risk_band_key];
GO

-- 5.2 Informado vs reexpresado desde el DWH (Caso Real 1): versión vigente al envío
--     del archivo frente a la versión vigente hoy, para un cierre.
CREATE FUNCTION [dwh].[itvf_c46_reported_vs_restated] (@p_snapshot_date_key INT, @p_reported_at DATETIME2(2))
RETURNS TABLE
AS
RETURN
    SELECT c.[contract_number], p.[full_name] AS [deudor], p.[tax_id] AS [rut_deudor], rb.[cmf_rating_category] AS [categoria],
           rep.[balance_clp] AS [saldo_informado_clp], tod.[balance_clp] AS [saldo_vigente_clp],
           rep.[regulatory_provision_clp] AS [provision_informada_clp], tod.[regulatory_provision_clp] AS [provision_vigente_clp],
           tod.[regulatory_provision_clp] - rep.[regulatory_provision_clp] AS [delta_provision_clp],
           rep.[restatement_seq] AS [version_informada], tod.[restatement_seq] AS [version_vigente],
           CASE WHEN rep.[restatement_seq] <> tod.[restatement_seq] THEN 'REEXPRESADO_POST_ENVIO'
                ELSE 'CONSISTENTE' END AS [diagnostico]
    FROM [dwh].[vw_snapshot_latest] tod
    JOIN [dwh].[itvf_snapshot_as_known](@p_reported_at) rep
      ON rep.[snapshot_date_key] = tod.[snapshot_date_key] AND rep.[contract_key] = tod.[contract_key]
    JOIN [dwh].[dim_account_contract] c ON c.[contract_key] = tod.[contract_key]
    JOIN [dwh].[dim_product] pr ON pr.[product_key] = tod.[product_key] AND pr.[is_loan] = 1
    JOIN [dwh].[dim_partner] p ON p.[partner_key] = tod.[partner_key]
    JOIN [dwh].[dim_risk_band] rb ON rb.[risk_band_key] = tod.[risk_band_key]
    WHERE tod.[snapshot_date_key] = @p_snapshot_date_key;
GO

-- 5.3 Auditoría bitemporal en el ODS ("¿qué sabíamos y cuándo?"): estado del
--     contrato al cierre económico según lo asertado al enviar vs lo asertado hoy,
--     con el asiento de ajuste del ledger que respalda cada corrección.
CREATE PROCEDURE [core].[usp_audit_c46_as_was_vs_as_is]
    @p_business_date DATE,
    @p_reported_at   DATETIME2(2),
    @p_known_at      DATETIME2(2) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @known DATETIME2(2) = ISNULL(@p_known_at, SYSUTCDATETIME());
    -- Fin del día económico: incluye cambios efectivos desde las 00:00 del cierre.
    DECLARE @biz DATETIME2(2) = DATEADD(MILLISECOND, -10, CAST(DATEADD(DAY, 1, @p_business_date) AS DATETIME2(2)));

    WITH was AS (SELECT * FROM [core].[itvf_contract_as_of](@biz, @p_reported_at)),
         is_ AS (SELECT * FROM [core].[itvf_contract_as_of](@biz, @known))
    SELECT COALESCE(w.[contract_id], i.[contract_id]) AS [contract_id],
           COALESCE(w.[contract_number], i.[contract_number]) AS [contract_number],
           p.[tax_id] AS [rut_deudor], p.[full_name] AS [deudor],
           w.[current_balance] AS [saldo_informado], i.[current_balance] AS [saldo_vigente],
           ISNULL(i.[current_balance], 0) - ISNULL(w.[current_balance], 0) AS [diferencia_saldo],
           w.[cmf_rating] AS [categoria_informada], i.[cmf_rating] AS [categoria_vigente],
           ROUND(ISNULL(w.[current_balance], 0) * ISNULL(rw.[provision_rate], 0), 4) AS [provision_informada],
           ROUND(ISNULL(i.[current_balance], 0) * ISNULL(ri.[provision_rate], 0), 4) AS [provision_vigente],
           i.[change_type] AS [operacion_avf], i.[change_reason] AS [motivo], i.[source_journal_entry_id] AS [asiento_ajuste],
           CASE WHEN w.[contract_id] IS NULL THEN 'OPERACION_INGRESADA_RETROACTIVAMENTE'
                WHEN i.[contract_id] IS NULL THEN 'OPERACION_ANULADA_POST_ENVIO'
                WHEN w.[current_balance] <> i.[current_balance] THEN 'AJUSTE_DE_SALDO_RETROACTIVO'
                WHEN w.[cmf_rating] <> i.[cmf_rating] THEN 'RECATEGORIZACION_RETROACTIVA'
                WHEN w.[contract_status] <> i.[contract_status] THEN 'CAMBIO_DE_ESTADO_RETROACTIVO'
                ELSE 'CONSISTENTE' END AS [diagnostico]
    FROM was w
    FULL OUTER JOIN is_ i ON i.[contract_id] = w.[contract_id]
    LEFT JOIN [dwh].[dim_partner] p ON p.[partner_id] = COALESCE(w.[partner_id], i.[partner_id]) AND p.[is_current] = 1
    LEFT JOIN [dwh].[dim_risk_band] rw ON rw.[cmf_rating_category] = w.[cmf_rating]
    LEFT JOIN [dwh].[dim_risk_band] ri ON ri.[cmf_rating_category] = i.[cmf_rating]
    WHERE COALESCE(w.[product_code], i.[product_code]) IN (SELECT [product_code] FROM [dwh].[dim_product] WHERE [is_loan] = 1)
    ORDER BY ABS(ISNULL(i.[current_balance], 0) - ISNULL(w.[current_balance], 0)) DESC;
END;
GO

-- =============================================================================
-- 6. C54 · DESCALCE DE PLAZOS (BASE CONTRACTUAL)
--    Límites: descalce acumulado a 30 días >= -1 x capital básico; a 90 días >= -2 x.
--    Sólo un descalce NEGATIVO (egresos > ingresos) consume el límite.
-- =============================================================================
CREATE FUNCTION [dwh].[itvf_c54_maturity_ladder] (@p_position_date_key INT)
RETURNS TABLE
AS
RETURN
WITH flows AS (
    SELECT cf.[time_bucket_key],
           SUM(CASE WHEN cf.[direction] = 'INFLOW'  THEN cf.[total_amount] ELSE 0 END) AS [ingresos],
           SUM(CASE WHEN cf.[direction] = 'OUTFLOW' THEN cf.[total_amount] ELSE 0 END) AS [egresos]
    FROM [dwh].[fact_contractual_cashflows] cf
    JOIN [dwh].[vw_snapshot_latest] s ON s.[snapshot_date_key] = cf.[position_date_key]
                                     AND s.[contract_key] = cf.[contract_key]
                                     AND s.[restatement_seq] = cf.[restatement_seq]
    WHERE cf.[position_date_key] = @p_position_date_key AND cf.[include_in_liquidity] = 1
    GROUP BY cf.[time_bucket_key]
),
ladder AS (
    SELECT tb.[sort_order], tb.[bucket_code], tb.[bucket_label],
           ISNULL(f.[ingresos], 0) AS [ingresos], ISNULL(f.[egresos], 0) AS [egresos],
           ISNULL(f.[ingresos], 0) - ISNULL(f.[egresos], 0) AS [descalce],
           SUM(ISNULL(f.[ingresos], 0) - ISNULL(f.[egresos], 0)) OVER (ORDER BY tb.[sort_order]
                                                                     ROWS UNBOUNDED PRECEDING) AS [descalce_acumulado]
    FROM [dwh].[dim_time_bucket] tb
    LEFT JOIN flows f ON f.[time_bucket_key] = tb.[time_bucket_key]
),
lim AS (
    SELECT MAX(CASE WHEN [param_code] = 'CAPITAL_BASICO_CLP' THEN [param_value] END) AS [capital],
           MAX(CASE WHEN [param_code] = 'C54_LIMITE_DESCALCE_30D_X_CAPITAL' THEN [param_value] END) AS [x30],
           MAX(CASE WHEN [param_code] = 'C54_LIMITE_DESCALCE_90D_X_CAPITAL' THEN [param_value] END) AS [x90]
    FROM [dwh].[regulatory_parameter]
)
SELECT @p_position_date_key AS [position_date_key], l.[sort_order], l.[bucket_code], l.[bucket_label], l.[ingresos],
       l.[egresos], l.[descalce], l.[descalce_acumulado],
       CASE l.[bucket_code] WHEN 'B02' THEN -m.[capital] * m.[x30] WHEN 'B03' THEN -m.[capital] * m.[x90] END AS [limite],
       CASE WHEN l.[bucket_code] NOT IN ('B02','B03') THEN 'N/A'
            WHEN l.[descalce_acumulado] >= CASE l.[bucket_code] WHEN 'B02' THEN -m.[capital] * m.[x30]
                                                                ELSE -m.[capital] * m.[x90] END THEN 'CUMPLE'
            ELSE 'EXCEDE' END AS [cumple]
FROM ladder l
CROSS JOIN lim m;
GO

CREATE VIEW [dwh].[vw_cmf_c54_maturity_ladder_gaps]
AS
SELECT x.*
FROM (SELECT MAX([snapshot_date_key]) AS [k] FROM [dwh].[fact_account_balance_snapshot]) d
CROSS APPLY [dwh].[itvf_c54_maturity_ladder](d.[k]) x;
GO

-- =============================================================================
-- 7. C56 · IRRBB (ΔEVE y ΔNII a 12 meses, 6 escenarios BCBS 368)
--    Δr(t) por escenario (t = punto medio de la banda en años):
--      paralelo ±P · corto ±S·e^(-t/4) · empinamiento -0,65|corto| + 0,9|largo|
--      aplanamiento +0,8|corto| - 0,6|largo|, con largo = L·(1 - e^(-t/4)).
--    ΔEVE = Σ flujo neto × (e^(-(r+Δr)t) - e^(-rt))   (revaluación completa)
--    ΔNII = Σ_{t <= 1 año} brecha de repricing × Δr × (1 - t)
--    EXP opera en FLOAT por definición; el resultado se redondea a DECIMAL(19,4).
-- =============================================================================
CREATE FUNCTION [dwh].[itvf_c56_irrbb] (@p_position_date_key INT)
RETURNS TABLE
AS
RETURN
WITH flows AS (
    SELECT tb.[bucket_code], tb.[midpoint_days], yc.[zero_rate],
           SUM(CASE WHEN cf.[direction] = 'INFLOW' THEN cf.[total_amount] ELSE -cf.[total_amount] END) AS [net_cf],
           SUM(CASE WHEN cf.[cashflow_type] IN ('PRINCIPAL','REPRICING_NOTIONAL')
                    THEN CASE WHEN cf.[direction] = 'INFLOW' THEN cf.[principal_amount] ELSE -cf.[principal_amount] END
                    ELSE 0 END) AS [repricing_gap]
    FROM [dwh].[fact_contractual_cashflows] cf
    JOIN [dwh].[vw_snapshot_latest] s ON s.[snapshot_date_key] = cf.[position_date_key]
                                     AND s.[contract_key] = cf.[contract_key]
                                     AND s.[restatement_seq] = cf.[restatement_seq]
    JOIN [dwh].[dim_time_bucket] tb ON tb.[time_bucket_key] = cf.[time_bucket_key]
    JOIN [dwh].[yield_curve_point] yc ON yc.[bucket_code] = tb.[bucket_code] AND yc.[currency_code] = cf.[currency_code]
    WHERE cf.[position_date_key] = @p_position_date_key AND cf.[include_in_irrbb] = 1
    GROUP BY tb.[bucket_code], tb.[midpoint_days], yc.[zero_rate]
),
prm AS (
    SELECT MAX(CASE WHEN [param_code] = 'IRRBB_SHOCK_PARALLEL' THEN [param_value] END) AS [p],
           MAX(CASE WHEN [param_code] = 'IRRBB_SHOCK_SHORT' THEN [param_value] END) AS [s],
           MAX(CASE WHEN [param_code] = 'IRRBB_SHOCK_LONG' THEN [param_value] END) AS [l],
           MAX(CASE WHEN [param_code] = 'CAPITAL_BASICO_CLP' THEN [param_value] END) AS [tier1],
           MAX(CASE WHEN [param_code] = 'IRRBB_OUTLIER_EVE_PCT_TIER1' THEN [param_value] END) AS [outlier]
    FROM [dwh].[regulatory_parameter]
),
shocked AS (
    SELECT sc.[escenario], sc.[orden], f.[midpoint_days], f.[zero_rate], f.[net_cf], f.[repricing_gap],
           CAST(f.[midpoint_days] AS FLOAT) / 365.0 AS [t],
           CASE sc.[escenario]
               WHEN 'PARALELO_ALZA' THEN CAST(m.[p] AS FLOAT)
               WHEN 'PARALELO_BAJA' THEN -CAST(m.[p] AS FLOAT)
               WHEN 'CORTO_ALZA'    THEN CAST(m.[s] AS FLOAT) * EXP(-(f.[midpoint_days] / 365.0) / 4)
               WHEN 'CORTO_BAJA'    THEN -CAST(m.[s] AS FLOAT) * EXP(-(f.[midpoint_days] / 365.0) / 4)
               WHEN 'EMPINAMIENTO'  THEN -0.65 * ABS(CAST(m.[s] AS FLOAT) * EXP(-(f.[midpoint_days] / 365.0) / 4))
                                         + 0.9 * ABS(CAST(m.[l] AS FLOAT) * (1 - EXP(-(f.[midpoint_days] / 365.0) / 4)))
               ELSE                       0.8 * ABS(CAST(m.[s] AS FLOAT) * EXP(-(f.[midpoint_days] / 365.0) / 4))
                                         - 0.6 * ABS(CAST(m.[l] AS FLOAT) * (1 - EXP(-(f.[midpoint_days] / 365.0) / 4)))
           END AS [dr]
    FROM flows f
    CROSS JOIN prm m
    CROSS JOIN (VALUES ('PARALELO_ALZA', 1), ('PARALELO_BAJA', 2), ('CORTO_ALZA', 3), ('CORTO_BAJA', 4),
                       ('EMPINAMIENTO', 5), ('APLANAMIENTO', 6)) sc ([escenario], [orden])
),
agg AS (
    SELECT [escenario], [orden],
           SUM(CAST([net_cf] AS FLOAT) * (EXP(-(CAST([zero_rate] AS FLOAT) + [dr]) * [t]) - EXP(-CAST([zero_rate] AS FLOAT) * [t]))) AS [d_eve],
           SUM(CASE WHEN [midpoint_days] <= 365 THEN CAST([repricing_gap] AS FLOAT) * [dr] * (1 - [t]) ELSE 0 END) AS [d_nii]
    FROM shocked
    GROUP BY [escenario], [orden]
)
SELECT @p_position_date_key AS [position_date_key], a.[orden], a.[escenario],
       CAST(ROUND(a.[d_eve], 4) AS DECIMAL(19,4)) AS [delta_eve_clp],
       CAST(ROUND(a.[d_nii], 4) AS DECIMAL(19,4)) AS [delta_nii_12m_clp],
       CAST(ROUND(a.[d_eve] / CAST(m.[tier1] AS FLOAT) * 100, 4) AS DECIMAL(12,4)) AS [delta_eve_pct_tier1],
       CASE WHEN ABS(a.[d_eve]) / CAST(m.[tier1] AS FLOAT) > CAST(m.[outlier] AS FLOAT) THEN 'SI' ELSE 'NO' END AS [valor_atipico]
FROM agg a
CROSS JOIN prm m;
GO

CREATE VIEW [dwh].[vw_cmf_c56_irrbb_eve_nii_sensitivity]
AS
SELECT x.*
FROM (SELECT MAX([snapshot_date_key]) AS [k] FROM [dwh].[fact_account_balance_snapshot]) d
CROSS APPLY [dwh].[itvf_c56_irrbb](d.[k]) x;
GO

-- =============================================================================
-- 8. SALDO POINT-IN-TIME DESDE EL HECHO TRANSACCIONAL (Kimball, fig. 9.5)
--    Para cada (cuenta, contrato) toma la transacción marcada como última del día
--    con la mayor fecha contable <= @p_date_key: el saldo sale de una sola fila,
--    sin recorrer la historia completa.
-- =============================================================================
CREATE FUNCTION [dwh].[itvf_point_in_time_balance] (@p_date_key INT)
RETURNS TABLE
AS
RETURN
    SELECT g.[account_code], g.[account_name], c.[contract_number], f.[posting_date_key] AS [ultima_fecha_movimiento],
           f.[account_sequence_number], f.[balance_after] AS [saldo]
    FROM [dwh].[fact_gl_transaction_postings] f
    JOIN [dwh].[dim_gl_account] g ON g.[gl_account_key] = f.[gl_account_key]
    LEFT JOIN [dwh].[dim_account_contract] c ON c.[contract_key] = f.[contract_key]
    WHERE f.[is_final_daily_tx] = 1
      AND f.[posting_date_key] = (SELECT MAX(f2.[posting_date_key])
                                  FROM [dwh].[fact_gl_transaction_postings] f2
                                  WHERE f2.[gl_account_key] = f.[gl_account_key]
                                    AND f2.[contract_key] = f.[contract_key]
                                    AND f2.[is_final_daily_tx] = 1
                                    AND f2.[posting_date_key] <= @p_date_key);
GO

GRANT SELECT  ON [dwh].[vw_snapshot_latest]                   TO [app_report_reader];
GRANT SELECT  ON [dwh].[itvf_snapshot_as_known]               TO [app_report_reader];
GRANT SELECT  ON [dwh].[vw_c55_lcr_components_daily]          TO [app_report_reader];
GRANT SELECT  ON [dwh].[vw_c55_nsfr_components_daily]         TO [app_report_reader];
GRANT SELECT  ON [dwh].[vw_cmf_c55_lcr_nsfr_official]         TO [app_report_reader];
GRANT SELECT  ON [dwh].[vw_cmf_c46_provisions]                TO [app_report_reader];
GRANT SELECT  ON [dwh].[itvf_c46_reported_vs_restated]        TO [app_report_reader];
GRANT SELECT  ON [dwh].[itvf_c54_maturity_ladder]             TO [app_report_reader];
GRANT SELECT  ON [dwh].[vw_cmf_c54_maturity_ladder_gaps]      TO [app_report_reader];
GRANT SELECT  ON [dwh].[itvf_c56_irrbb]                       TO [app_report_reader];
GRANT SELECT  ON [dwh].[vw_cmf_c56_irrbb_eve_nii_sensitivity] TO [app_report_reader];
GRANT SELECT  ON [dwh].[itvf_point_in_time_balance]           TO [app_report_reader];
GRANT EXECUTE ON [core].[usp_audit_c46_as_was_vs_as_is]       TO [app_report_reader];
GO

-- -----------------------------------------------------------------------------
-- Ejemplos (Caso Real 1: crédito OPER-COM-90210, cierre 31-12-2024, C46 enviado el 10-01-2025)
-- -----------------------------------------------------------------------------
/*
SELECT * FROM dwh.itvf_c46_reported_vs_restated(20241231, '2025-01-10 12:00');
EXEC core.usp_audit_c46_as_was_vs_as_is @p_business_date = '2024-12-31', @p_reported_at = '2025-01-10 12:00';
SELECT * FROM dwh.vw_cmf_c55_lcr_nsfr_official WHERE snapshot_date_key = 20250530;
SELECT * FROM dwh.itvf_c54_maturity_ladder(20251231) ORDER BY sort_order;
SELECT * FROM dwh.itvf_c56_irrbb(20251231) ORDER BY orden;
SELECT * FROM dwh.itvf_point_in_time_balance(20250131) WHERE contract_number = 'OPER-COM-90210';
*/
