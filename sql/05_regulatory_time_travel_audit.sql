-- =============================================================================
-- 05_regulatory_time_travel_audit.sql
-- =============================================================================
-- Capa 4 complementaria: Vistas y procedimientos de reportería y auditoría
-- regulatoria CMF / Basilea III sobre el Data Warehouse Kimball y el Core ODS.
--
-- Contiene:
--   1. dwh.vw_c55_lcr_components_daily : desagregación diaria de ALAC y NAF por
--      línea (utilizada por el modo SQL Server de data_loader.py).
--   2. dwh.vw_c55_nsfr_components_daily: desagregación diaria de FED y FER por
--      línea (utilizada por data_loader.py).
--   3. dwh.vw_cmf_c55_lcr_nsfr_official : cálculo oficial de ratios con topes
--      Basilea III exactos (40% Nivel 2, 15% Nivel 2B y 75% Inflow Cap).
--   4. dwh.vw_c46_provisions_audit / dwh.usp_audit_c46_provisions_time_travel:
--      auditoría retrospectiva bitemporal de cartera y provisiones (C46).
--   5. dwh.vw_c54_liquidity_gaps / dwh.itvf_c54_maturity_ladder: matriz de
--      descalces contractuales y acumulados con límites de capital básico (C54).
--   6. dwh.vw_c56_irrbb_summary / dwh.itvf_c56_irrbb: riesgo de tasa de interés
--      en el libro bancario, sensibilidad EVE ante shocks y prueba de outlier (C56).
--   7. Consultas forenses de Time-Travel bitemporal (Asserted Versioning).
-- =============================================================================

USE [CoreBanking];
GO

-- Limpieza preventiva
DROP PROCEDURE IF EXISTS [dwh].[usp_audit_c46_provisions_time_travel];
DROP FUNCTION  IF EXISTS [dwh].[itvf_c56_irrbb];
DROP FUNCTION  IF EXISTS [dwh].[itvf_c54_maturity_ladder];
DROP VIEW      IF EXISTS [dwh].[vw_c56_irrbb_summary];
DROP VIEW      IF EXISTS [dwh].[vw_c54_liquidity_gaps];
DROP VIEW      IF EXISTS [dwh].[vw_c46_provisions_audit];
DROP VIEW      IF EXISTS [dwh].[vw_cmf_c55_lcr_nsfr_official];
DROP VIEW      IF EXISTS [dwh].[vw_c55_nsfr_components_daily];
DROP VIEW      IF EXISTS [dwh].[vw_c55_lcr_components_daily];
GO

-- =============================================================================
-- 1. VISTA: COMPONENTES DIARIOS LCR (ALAC Y NAF)
--    Desagrega cada contrato o pool en su categoría regulatoria correspondiente.
--    Toma la última reexpresión conocida para cada snapshot diario.
-- =============================================================================
CREATE VIEW [dwh].[vw_c55_lcr_components_daily]
AS
WITH LatestSnapshot AS (
    SELECT s.*,
           ROW_NUMBER() OVER (
               PARTITION BY s.[snapshot_date_key], s.[contract_key]
               ORDER BY s.[restatement_seq] DESC
           ) AS [rn]
    FROM [dwh].[fact_account_balance_snapshot] s
),
Cashflows30D AS (
    SELECT cf.[position_date_key],
           cf.[contract_key],
           cf.[restatement_seq],
           SUM(CASE WHEN cf.[direction] = 'OUTFLOW' THEN cf.[total_amount] ELSE 0 END) AS [liq_out_30d_clp],
           SUM(CASE WHEN cf.[direction] = 'INFLOW'  THEN cf.[total_amount] ELSE 0 END) AS [liq_in_30d_clp]
    FROM [dwh].[fact_contractual_cashflows] cf
    WHERE cf.[include_in_liquidity] = 1
      AND cf.[days_to_cashflow] <= 30
    GROUP BY cf.[position_date_key], cf.[contract_key], cf.[restatement_seq]
)
-- 1.1. Activos Líquidos de Alta Calidad (ALAC / HQLA)
SELECT 
    s.[snapshot_date_key],
    CAST('ALAC' AS VARCHAR(8)) AS [componente],
    t.[lcr_category]           AS [categoria],
    CAST(CASE 
        WHEN c.[contract_number] LIKE 'POOL%' THEN pr.[product_name]
        ELSE CONCAT(pr.[product_name], ' ', c.[contract_number])
    END AS NVARCHAR(150))      AS [subcategoria],
    t.[hqla_level]             AS [hqla_level],
    CAST('ALAC' AS VARCHAR(8)) AS [flow_tag],
    s.[market_value_clp]       AS [monto_nominal_clp],
    CAST(1.000000 - t.[hqla_haircut] AS DECIMAL(12,6)) AS [factor_ponderacion],
    CAST(ROUND(s.[market_value_clp] * (1.000000 - t.[hqla_haircut]), 4) AS DECIMAL(19,4)) AS [monto_ponderado_clp]
FROM LatestSnapshot s
JOIN [dwh].[dim_account_contract] c ON c.[contract_key] = s.[contract_key]
JOIN [dwh].[dim_product] pr ON pr.[product_key] = s.[product_key]
JOIN [dwh].[dim_liquidity_treatment] t ON t.[liquidity_treatment_key] = s.[liquidity_treatment_key]
WHERE s.[rn] = 1
  AND t.[lcr_component] = 'ALAC'
  AND s.[market_value_clp] > 0

UNION ALL

-- 1.2. Salidas Contractuales y de Depósitos (OUTFLOWS)
SELECT 
    s.[snapshot_date_key],
    CAST('NAF' AS VARCHAR(8))  AS [componente],
    t.[lcr_category]           AS [categoria],
    CAST(CASE 
        WHEN t.[lcr_basis] = 'BALANCE' THEN 
            CASE WHEN c.[contract_number] LIKE 'POOL%' THEN pr.[product_name]
                 ELSE CONCAT(pr.[product_name], ' ', c.[contract_number]) END
        ELSE CONCAT(pr.[product_name], ' ', c.[contract_number], ' (vence <= 30 días)')
    END AS NVARCHAR(150))      AS [subcategoria],
    CAST('NO_HQLA' AS VARCHAR(8)) AS [hqla_level],
    CAST('OUT' AS VARCHAR(8))  AS [flow_tag],
    CASE WHEN t.[lcr_basis] = 'BALANCE' THEN s.[balance_clp] ELSE ISNULL(cf.[liq_out_30d_clp], 0) END AS [monto_nominal_clp],
    t.[lcr_rate]               AS [factor_ponderacion],
    CAST(ROUND(
        (CASE WHEN t.[lcr_basis] = 'BALANCE' THEN s.[balance_clp] ELSE ISNULL(cf.[liq_out_30d_clp], 0) END) * t.[lcr_rate], 4
    ) AS DECIMAL(19,4))        AS [monto_ponderado_clp]
FROM LatestSnapshot s
JOIN [dwh].[dim_account_contract] c ON c.[contract_key] = s.[contract_key]
JOIN [dwh].[dim_product] pr ON pr.[product_key] = s.[product_key]
JOIN [dwh].[dim_liquidity_treatment] t ON t.[liquidity_treatment_key] = s.[liquidity_treatment_key]
LEFT JOIN Cashflows30D cf ON cf.[position_date_key] = s.[snapshot_date_key]
                         AND cf.[contract_key] = s.[contract_key]
                         AND cf.[restatement_seq] = s.[restatement_seq]
WHERE s.[rn] = 1
  AND t.[lcr_component] = 'OUTFLOW'
  AND (CASE WHEN t.[lcr_basis] = 'BALANCE' THEN s.[balance_clp] ELSE ISNULL(cf.[liq_out_30d_clp], 0) END) > 0

UNION ALL

-- 1.3. Entradas Contractuales a 30 días (INFLOWS)
SELECT 
    s.[snapshot_date_key],
    CAST('NAF' AS VARCHAR(8))  AS [componente],
    t.[lcr_category]           AS [categoria],
    CAST(CONCAT(pr.[product_name], ' ', c.[contract_number], ' (cobros <= 30 días)') AS NVARCHAR(150)) AS [subcategoria],
    CAST('NO_HQLA' AS VARCHAR(8)) AS [hqla_level],
    CAST('IN' AS VARCHAR(8))   AS [flow_tag],
    ISNULL(cf.[liq_in_30d_clp], 0) AS [monto_nominal_clp],
    t.[lcr_rate]               AS [factor_ponderacion],
    CAST(ROUND(ISNULL(cf.[liq_in_30d_clp], 0) * t.[lcr_rate], 4) AS DECIMAL(19,4)) AS [monto_ponderado_clp]
FROM LatestSnapshot s
JOIN [dwh].[dim_account_contract] c ON c.[contract_key] = s.[contract_key]
JOIN [dwh].[dim_product] pr ON pr.[product_key] = s.[product_key]
JOIN [dwh].[dim_liquidity_treatment] t ON t.[liquidity_treatment_key] = s.[liquidity_treatment_key]
LEFT JOIN Cashflows30D cf ON cf.[position_date_key] = s.[snapshot_date_key]
                         AND cf.[contract_key] = s.[contract_key]
                         AND cf.[restatement_seq] = s.[restatement_seq]
WHERE s.[rn] = 1
  AND t.[lcr_component] = 'INFLOW'
  AND ISNULL(cf.[liq_in_30d_clp], 0) > 0

UNION ALL

-- 1.4. Compromisos de Crédito No Utilizados (Líneas comprometidas disponibles)
SELECT 
    s.[snapshot_date_key],
    CAST('NAF' AS VARCHAR(8))  AS [componente],
    N'Compromisos de Liquidez y Crédito' AS [categoria],
    CAST(CONCAT(pr.[product_name], ' ', c.[contract_number], ': monto no girado') AS NVARCHAR(150)) AS [subcategoria],
    CAST('NO_HQLA' AS VARCHAR(8)) AS [hqla_level],
    CAST('OUT' AS VARCHAR(8))  AS [flow_tag],
    CASE 
        WHEN s.[undrawn_commitment_clp] > (CASE WHEN pr.[balance_side] IN ('ASSET','OFF_BALANCE') THEN s.[pending_debits_clp] ELSE 0 END)
        THEN s.[undrawn_commitment_clp] - (CASE WHEN pr.[balance_side] IN ('ASSET','OFF_BALANCE') THEN s.[pending_debits_clp] ELSE 0 END)
        ELSE 0 
    END AS [monto_nominal_clp],
    t.[undrawn_runoff_rate]    AS [factor_ponderacion],
    CAST(ROUND(
        (CASE 
            WHEN s.[undrawn_commitment_clp] > (CASE WHEN pr.[balance_side] IN ('ASSET','OFF_BALANCE') THEN s.[pending_debits_clp] ELSE 0 END)
            THEN s.[undrawn_commitment_clp] - (CASE WHEN pr.[balance_side] IN ('ASSET','OFF_BALANCE') THEN s.[pending_debits_clp] ELSE 0 END)
            ELSE 0 
         END) * t.[undrawn_runoff_rate], 4
    ) AS DECIMAL(19,4))        AS [monto_ponderado_clp]
FROM LatestSnapshot s
JOIN [dwh].[dim_account_contract] c ON c.[contract_key] = s.[contract_key]
JOIN [dwh].[dim_product] pr ON pr.[product_key] = s.[product_key]
JOIN [dwh].[dim_liquidity_treatment] t ON t.[liquidity_treatment_key] = s.[liquidity_treatment_key]
WHERE s.[rn] = 1
  AND s.[undrawn_commitment_clp] > 0
  AND t.[undrawn_runoff_rate] > 0
  AND (s.[undrawn_commitment_clp] - (CASE WHEN pr.[balance_side] IN ('ASSET','OFF_BALANCE') THEN s.[pending_debits_clp] ELSE 0 END)) > 0

UNION ALL

-- 1.5. Giros Reservados en Dos Fases (Pending debits = salida líquida al 100%)
SELECT 
    s.[snapshot_date_key],
    CAST('NAF' AS VARCHAR(8))  AS [componente],
    N'Compromisos de Liquidez y Crédito' AS [categoria],
    CAST(CONCAT(pr.[product_name], ' ', c.[contract_number], ': giros reservados (pending)') AS NVARCHAR(150)) AS [subcategoria],
    CAST('NO_HQLA' AS VARCHAR(8)) AS [hqla_level],
    CAST('OUT' AS VARCHAR(8))  AS [flow_tag],
    s.[pending_debits_clp]     AS [monto_nominal_clp],
    CAST(1.000000 AS DECIMAL(12,6)) AS [factor_ponderacion],
    s.[pending_debits_clp]     AS [monto_ponderado_clp]
FROM LatestSnapshot s
JOIN [dwh].[dim_account_contract] c ON c.[contract_key] = s.[contract_key]
JOIN [dwh].[dim_product] pr ON pr.[product_key] = s.[product_key]
WHERE s.[rn] = 1
  AND pr.[balance_side] IN ('ASSET','OFF_BALANCE')
  AND s.[pending_debits_clp] > 0;
GO

-- =============================================================================
-- 2. VISTA: COMPONENTES DIARIOS NSFR (FED Y FER)
--    Calcula los Fondos Estables Disponibles y Requeridos aplicando factores por
--    plazo residual o ponderación por perfil de vencimientos conductuales.
-- =============================================================================
CREATE VIEW [dwh].[vw_c55_nsfr_components_daily]
AS
WITH LatestSnapshot AS (
    SELECT s.*,
           ROW_NUMBER() OVER (
               PARTITION BY s.[snapshot_date_key], s.[contract_key]
               ORDER BY s.[restatement_seq] DESC
           ) AS [rn]
    FROM [dwh].[fact_account_balance_snapshot] s
),
LadderFactors AS (
    SELECT 
        lp.[product_code],
        SUM(lp.[share] * (
            CASE 
                WHEN tb.[nsfr_maturity_band] = 'LT_6M' THEN t.[nsfr_factor_lt_6m]
                WHEN tb.[nsfr_maturity_band] = '6M_1Y' THEN t.[nsfr_factor_6m_1y]
                ELSE t.[nsfr_factor_ge_1y]
            END
        )) AS [ladder_weighted_factor]
    FROM [dwh].[behavioral_ladder_profile] lp
    JOIN [dwh].[dim_time_bucket] tb ON tb.[bucket_code] = lp.[bucket_code]
    JOIN [dwh].[dim_product] pr ON pr.[product_code] = lp.[product_code]
    JOIN [dwh].[dim_liquidity_treatment] t ON 1=1
    GROUP BY lp.[product_code], t.[liquidity_treatment_key]
)
-- 2.1. Saldos de Balance ASF y RSF
SELECT 
    s.[snapshot_date_key],
    CAST(CASE WHEN t.[nsfr_side] = 'ASF' THEN 'FED' ELSE 'FER' END AS VARCHAR(8)) AS [componente],
    t.[nsfr_category]           AS [categoria],
    CAST(CASE 
        WHEN c.[contract_number] LIKE 'POOL%' THEN pr.[product_name]
        ELSE CONCAT(pr.[product_name], ' ', c.[contract_number])
    END AS NVARCHAR(150))       AS [subcategoria],
    s.[balance_clp]             AS [monto_nominal_clp],
    CAST(ISNULL(lf.[ladder_weighted_factor], 
        CASE 
            WHEN s.[residual_maturity_days] IS NULL OR s.[residual_maturity_days] < 180 THEN t.[nsfr_factor_lt_6m]
            WHEN s.[residual_maturity_days] BETWEEN 180 AND 364 THEN t.[nsfr_factor_6m_1y]
            ELSE t.[nsfr_factor_ge_1y]
        END
    ) AS DECIMAL(12,6))         AS [factor_ponderacion],
    CAST(ROUND(
        s.[balance_clp] * ISNULL(lf.[ladder_weighted_factor], 
            CASE 
                WHEN s.[residual_maturity_days] IS NULL OR s.[residual_maturity_days] < 180 THEN t.[nsfr_factor_lt_6m]
                WHEN s.[residual_maturity_days] BETWEEN 180 AND 364 THEN t.[nsfr_factor_6m_1y]
                ELSE t.[nsfr_factor_ge_1y]
            END
        ), 4
    ) AS DECIMAL(19,4))         AS [monto_ponderado_clp]
FROM LatestSnapshot s
JOIN [dwh].[dim_account_contract] c ON c.[contract_key] = s.[contract_key]
JOIN [dwh].[dim_product] pr ON pr.[product_key] = s.[product_key]
JOIN [dwh].[dim_liquidity_treatment] t ON t.[liquidity_treatment_key] = s.[liquidity_treatment_key]
OUTER APPLY (
    SELECT TOP (1)
           SUM(lp.[share] * (
               CASE 
                   WHEN tb.[nsfr_maturity_band] = 'LT_6M' THEN t.[nsfr_factor_lt_6m]
                   WHEN tb.[nsfr_maturity_band] = '6M_1Y' THEN t.[nsfr_factor_6m_1y]
                   ELSE t.[nsfr_factor_ge_1y]
               END
           )) AS [ladder_weighted_factor]
    FROM [dwh].[behavioral_ladder_profile] lp
    JOIN [dwh].[dim_time_bucket] tb ON tb.[bucket_code] = lp.[bucket_code]
    WHERE lp.[product_code] = pr.[product_code]
      AND c.[contract_number] LIKE 'POOL%'
) lf
WHERE s.[rn] = 1
  AND t.[nsfr_side] IN ('ASF', 'RSF')
  AND s.[balance_clp] <> 0

UNION ALL

-- 2.2. Líneas Comprometidas No Utilizadas (5% RSF bajo Basilea III)
SELECT 
    s.[snapshot_date_key],
    CAST('FER' AS VARCHAR(8))  AS [componente],
    N'Líneas Comprometidas No Utilizadas (5% RSF)' AS [categoria],
    CAST(CONCAT(pr.[product_name], ' ', c.[contract_number], ': monto no girado') AS NVARCHAR(150)) AS [subcategoria],
    s.[undrawn_commitment_clp] AS [monto_nominal_clp],
    t.[undrawn_rsf_factor]     AS [factor_ponderacion],
    CAST(ROUND(s.[undrawn_commitment_clp] * t.[undrawn_rsf_factor], 4) AS DECIMAL(19,4)) AS [monto_ponderado_clp]
FROM LatestSnapshot s
JOIN [dwh].[dim_account_contract] c ON c.[contract_key] = s.[contract_key]
JOIN [dwh].[dim_product] pr ON pr.[product_key] = s.[product_key]
JOIN [dwh].[dim_liquidity_treatment] t ON t.[liquidity_treatment_key] = s.[liquidity_treatment_key]
WHERE s.[rn] = 1
  AND s.[undrawn_commitment_clp] > 0
  AND t.[undrawn_rsf_factor] > 0;
GO

-- =============================================================================
-- 3. VISTA: RATIOS OFICIALES CMF C55 (LCR & NSFR CON TOPES BASILEA III)
--    Aplica matemáticamente:
--      * Cap 75% sobre entradas (Inflows) en NAF.
--      * Cap 15% sobre activos Nivel 2B en ALAC.
--      * Cap 40% sobre total activos Nivel 2 en ALAC.
-- =============================================================================
CREATE VIEW [dwh].[vw_cmf_c55_lcr_nsfr_official]
AS
WITH LcrRawTotals AS (
    SELECT 
        v.[snapshot_date_key],
        SUM(CASE WHEN v.[componente] = 'ALAC' AND v.[hqla_level] = 'L1'  THEN v.[monto_ponderado_clp] ELSE 0 END) AS [l1],
        SUM(CASE WHEN v.[componente] = 'ALAC' AND v.[hqla_level] = 'L2A' THEN v.[monto_ponderado_clp] ELSE 0 END) AS [l2a],
        SUM(CASE WHEN v.[componente] = 'ALAC' AND v.[hqla_level] = 'L2B' THEN v.[monto_ponderado_clp] ELSE 0 END) AS [l2b],
        SUM(CASE WHEN v.[flow_tag] = 'OUT' THEN v.[monto_ponderado_clp] ELSE 0 END) AS [total_outflows],
        SUM(CASE WHEN v.[flow_tag] = 'IN'  THEN v.[monto_ponderado_clp] ELSE 0 END) AS [total_inflows]
    FROM [dwh].[vw_c55_lcr_components_daily] v
    GROUP BY v.[snapshot_date_key]
),
LcrCapped AS (
    SELECT 
        r.[snapshot_date_key],
        r.[l1],
        r.[l2a],
        r.[l2b],
        r.[total_outflows],
        r.[total_inflows],
        -- Inflows cap (máximo 75% de las salidas)
        CASE 
            WHEN r.[total_inflows] < (r.[total_outflows] * 0.750000) THEN r.[total_inflows]
            ELSE r.[total_outflows] * 0.750000
        END AS [capped_inflows],
        -- NAF = Outflows - Inflows computables
        r.[total_outflows] - (
            CASE 
                WHEN r.[total_inflows] < (r.[total_outflows] * 0.750000) THEN r.[total_inflows]
                ELSE r.[total_outflows] * 0.750000
            END
        ) AS [net_cash_outflows_clp],
        -- Cap 15% Nivel 2B
        CASE 
            WHEN r.[l2b] <= (r.[l1] + r.[l2a]) * (15.0 / 85.0) THEN r.[l2b]
            ELSE (r.[l1] + r.[l2a]) * (15.0 / 85.0)
        END AS [l2b_capped]
    FROM LcrRawTotals r
),
LcrFinal AS (
    SELECT 
        c.[snapshot_date_key],
        c.[net_cash_outflows_clp],
        -- Cap 40% Nivel 2 Total (L2A + L2B_capped <= 40/60 * L1)
        c.[l1] + (
            CASE 
                WHEN (c.[l2a] + c.[l2b_capped]) <= c.[l1] * (40.0 / 60.0) THEN (c.[l2a] + c.[l2b_capped])
                ELSE c.[l1] * (40.0 / 60.0)
            END
        ) AS [hqla_clp]
    FROM LcrCapped c
),
NsfrTotals AS (
    SELECT 
        v.[snapshot_date_key],
        SUM(CASE WHEN v.[componente] = 'FED' THEN v.[monto_ponderado_clp] ELSE 0 END) AS [asf_clp],
        SUM(CASE WHEN v.[componente] = 'FER' THEN v.[monto_ponderado_clp] ELSE 0 END) AS [rsf_clp]
    FROM [dwh].[vw_c55_nsfr_components_daily] v
    GROUP BY v.[snapshot_date_key]
)
SELECT 
    l.[snapshot_date_key],
    l.[hqla_clp],
    l.[net_cash_outflows_clp],
    CAST(CASE 
        WHEN l.[net_cash_outflows_clp] > 0 THEN ROUND((l.[hqla_clp] / l.[net_cash_outflows_clp]) * 100.0, 4)
        ELSE 999.0000
    END AS DECIMAL(9,4)) AS [lcr_pct],
    n.[asf_clp]         AS [fed_clp],
    n.[rsf_clp]         AS [fer_clp],
    CAST(CASE 
        WHEN n.[rsf_clp] > 0 THEN ROUND((n.[asf_clp] / n.[rsf_clp]) * 100.0, 4)
        ELSE 999.0000
    END AS DECIMAL(9,4)) AS [nsfr_pct]
FROM LcrFinal l
JOIN NsfrTotals n ON n.[snapshot_date_key] = l.[snapshot_date_key];
GO

-- =============================================================================
-- 4. PROCEDIMIENTO Y VISTA: AUDITORÍA RETROSPECTIVA BITEMPORAL CMF (C46)
--    Compara lo que el banco reportó en una fecha de emisión histórica (@p_reported_at)
--    contra lo que se conoce hoy o en otra fecha de conocimiento (@p_known_at).
-- =============================================================================
CREATE PROCEDURE [dwh].[usp_audit_c46_provisions_time_travel]
    @p_snapshot_date DATE,
    @p_reported_at   DATETIME2(2),
    @p_known_at      DATETIME2(2) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    IF @p_known_at IS NULL SET @p_known_at = SYSUTCDATETIME();
    DECLARE @p_effective DATETIME2(2) = CAST(@p_snapshot_date AS DATETIME2(2));

    WITH AsReported AS (
        SELECT 
            c.[contract_id],
            c.[contract_number],
            c.[partner_id],
            c.[product_code],
            c.[currency_code],
            c.[current_balance]       AS [balance_reported],
            c.[annual_nominal_rate]   AS [rate_reported],
            c.[cmf_rating]            AS [rating_reported],
            c.[days_past_due]         AS [dpd_reported],
            c.[contract_status]       AS [status_reported],
            c.[AssertedFrom]          AS [asserted_from_rep],
            c.[AssertedTo]            AS [asserted_to_rep]
        FROM [core].[itvf_contract_as_of](@p_effective, @p_reported_at) c
    ),
    AsKnownToday AS (
        SELECT 
            c.[contract_id],
            c.[contract_number],
            c.[partner_id],
            c.[product_code],
            c.[currency_code],
            c.[current_balance]       AS [balance_known],
            c.[annual_nominal_rate]   AS [rate_known],
            c.[cmf_rating]            AS [rating_known],
            c.[days_past_due]         AS [dpd_known],
            c.[contract_status]       AS [status_known],
            c.[change_type]           AS [change_type_known],
            c.[change_reason]         AS [change_reason_known],
            c.[source_journal_entry_id] AS [adjustment_journal_entry_id],
            c.[AssertedFrom]          AS [asserted_from_today],
            c.[AssertedTo]            AS [asserted_to_today]
        FROM [core].[itvf_contract_as_of](@p_effective, @p_known_at) c
    )
    SELECT 
        ISNULL(r.[contract_id], k.[contract_id])         AS [contract_id],
        ISNULL(r.[contract_number], k.[contract_number]) AS [contract_number],
        p.[tax_id]                                       AS [partner_rut],
        p.[full_name]                                    AS [partner_name],
        ISNULL(r.[product_code], k.[product_code])       AS [product_code],
        ISNULL(r.[currency_code], k.[currency_code])     AS [currency_code],

        -- Saldos
        ISNULL(r.[balance_reported], 0.0000)             AS [saldo_reportado_original],
        ISNULL(k.[balance_known], 0.0000)                AS [saldo_corregido_hoy],
        (ISNULL(k.[balance_known], 0.0000) - 
         ISNULL(r.[balance_reported], 0.0000))           AS [delta_saldo],

        -- Clasificación y Calificación
        r.[rating_reported]                              AS [rating_reportado],
        k.[rating_known]                                 AS [rating_hoy],
        r.[status_reported]                              AS [estado_reportado],
        k.[status_known]                                 AS [estado_hoy],

        -- Provisiones C46 (calculadas según escala normativa dim_risk_band)
        ROUND(ISNULL(r.[balance_reported], 0) * ISNULL(rb_r.[provision_rate], 0), 4) AS [provision_reportada_clp],
        ROUND(ISNULL(k.[balance_known], 0) * ISNULL(rb_k.[provision_rate], 0), 4)    AS [provision_corregida_clp],
        ROUND(ISNULL(k.[balance_known], 0) * ISNULL(rb_k.[provision_rate], 0), 4) -
        ROUND(ISNULL(r.[balance_reported], 0) * ISNULL(rb_r.[provision_rate], 0), 4) AS [delta_provision_clp],

        -- Auditoría y Causa Raíz
        k.[change_type_known]                            AS [tipo_ajuste_realizado],
        k.[change_reason_known]                          AS [motivo_justificacion],
        k.[adjustment_journal_entry_id]                  AS [asiento_contable_ajuste],

        -- Diagnóstico Normativo CMF
        CASE 
            WHEN r.[contract_id] IS NULL THEN 'OPERACION_INGRESADA_RETROACTIVAMENTE'
            WHEN k.[contract_id] IS NULL THEN 'OPERACION_ANULADA_POST_CORTE'
            WHEN r.[balance_reported] <> k.[balance_known] THEN 'AJUSTE_DE_SALDO_EXTEMPORANEO'
            WHEN r.[rating_reported] <> k.[rating_known] THEN 'RECATEGORIZACION_RIESGO_RETROACTIVA'
            WHEN r.[status_reported] <> k.[status_known] THEN 'CAMBIO_ESTADO_OPERATIVO_RETROACTIVO'
            ELSE 'CONSISTENTE_SIN_OBSERVACIONES'
        END AS [diagnostico_auditoria_cmf]
    FROM AsReported r
    FULL OUTER JOIN AsKnownToday k ON k.[contract_id] = r.[contract_id]
    LEFT JOIN [dwh].[dim_partner] p ON p.[partner_id] = ISNULL(r.[partner_id], k.[partner_id]) AND p.[is_current] = 1
    LEFT JOIN [dwh].[dim_risk_band] rb_r ON rb_r.[cmf_rating_category] = r.[rating_reported]
    LEFT JOIN [dwh].[dim_risk_band] rb_k ON rb_k.[cmf_rating_category] = k.[rating_known]
    ORDER BY ABS(ISNULL(k.[balance_known], 0) - ISNULL(r.[balance_reported], 0)) DESC;
END;
GO

-- =============================================================================
-- 5. MATRIZ DE DESCALCES DE PLAZOS Y LIQUIDEZ CMF (C54)
--    Flujos agrupados por las 8 bandas CMF, brechas acumuladas y límites Tier 1.
-- =============================================================================
CREATE FUNCTION [dwh].[itvf_c54_maturity_ladder] (@p_position_date_key INT)
RETURNS TABLE
AS
RETURN
WITH CashflowsGrouped AS (
    SELECT 
        tb.[bucket_code],
        tb.[bucket_label],
        tb.[sort_order],
        SUM(CASE WHEN cf.[direction] = 'INFLOW'  THEN cf.[total_amount] ELSE 0 END) AS [activos_inflow_clp],
        SUM(CASE WHEN cf.[direction] = 'OUTFLOW' THEN cf.[total_amount] ELSE 0 END) AS [pasivos_outflow_clp]
    FROM [dwh].[dim_time_bucket] tb
    LEFT JOIN [dwh].[fact_contractual_cashflows] cf 
        ON cf.[time_bucket_key] = tb.[time_bucket_key]
       AND cf.[position_date_key] = @p_position_date_key
       AND cf.[include_in_liquidity] = 1
       AND cf.[restatement_seq] = (
           SELECT MAX(cf2.[restatement_seq]) 
           FROM [dwh].[fact_contractual_cashflows] cf2
           WHERE cf2.[position_date_key] = cf.[position_date_key]
             AND cf2.[contract_key] = cf.[contract_key]
       )
    GROUP BY tb.[bucket_code], tb.[bucket_label], tb.[sort_order]
),
LadderWithGaps AS (
    SELECT 
        c.[bucket_code],
        c.[bucket_label],
        c.[sort_order],
        c.[activos_inflow_clp],
        c.[pasivos_outflow_clp],
        c.[activos_inflow_clp] - c.[pasivos_outflow_clp] AS [descalce_neto_clp],
        SUM(c.[activos_inflow_clp] - c.[pasivos_outflow_clp]) OVER (
            ORDER BY c.[sort_order] ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS [descalce_acumulado_clp]
    FROM CashflowsGrouped c
)
SELECT 
    @p_position_date_key AS [position_date_key],
    l.[bucket_code],
    l.[bucket_label],
    l.[sort_order],
    l.[activos_inflow_clp],
    l.[pasivos_outflow_clp],
    l.[descalce_neto_clp],
    l.[descalce_acumulado_clp],
    rp.[param_value] AS [capital_basico_tier1_clp],
    CASE 
        WHEN l.[bucket_code] IN ('B01','B02') AND ABS(l.[descalce_acumulado_clp]) > (rp.[param_value] * 1.0)
            THEN N'EXCEDE LÍMITE C54 (1x Tier 1 a 30 días)'
        WHEN l.[bucket_code] = 'B03' AND ABS(l.[descalce_acumulado_clp]) > (rp.[param_value] * 2.0)
            THEN N'EXCEDE LÍMITE C54 (2x Tier 1 a 90 días)'
        ELSE N'CUMPLE'
    END AS [estado_normativo_c54]
FROM LadderWithGaps l
CROSS JOIN [dwh].[regulatory_parameter] rp
WHERE rp.[param_code] = 'CAPITAL_BASICO_CLP';
GO

-- =============================================================================
-- 6. RIESGO DE TASA DE INTERÉS EN EL LIBRO BANCARIO CMF (C56 / IRRBB)
--    Sensibilidad del Valor Económico del Patrimonio (Delta EVE) ante shocks.
-- =============================================================================
CREATE FUNCTION [dwh].[itvf_c56_irrbb] (@p_position_date_key INT)
RETURNS TABLE
AS
RETURN
WITH RepricingGaps AS (
    SELECT 
        tb.[bucket_code],
        tb.[bucket_label],
        tb.[midpoint_days],
        tb.[sort_order],
        ISNULL(yc.[zero_rate], 0.0520) AS [curva_cero],
        SUM(CASE WHEN cf.[direction] = 'INFLOW'  THEN cf.[total_amount] ELSE 0 END) AS [activos_repreciables],
        SUM(CASE WHEN cf.[direction] = 'OUTFLOW' THEN cf.[total_amount] ELSE 0 END) AS [pasivos_repreciables]
    FROM [dwh].[dim_time_bucket] tb
    LEFT JOIN [dwh].[fact_contractual_cashflows] cf 
        ON cf.[time_bucket_key] = tb.[time_bucket_key]
       AND cf.[position_date_key] = @p_position_date_key
       AND cf.[include_in_irrbb] = 1
       AND cf.[restatement_seq] = (
           SELECT MAX(cf2.[restatement_seq]) 
           FROM [dwh].[fact_contractual_cashflows] cf2
           WHERE cf2.[position_date_key] = cf.[position_date_key]
             AND cf2.[contract_key] = cf.[contract_key]
       )
    LEFT JOIN [dwh].[yield_curve_point] yc ON yc.[currency_code] = 'CLP' AND yc.[bucket_code] = tb.[bucket_code]
    GROUP BY tb.[bucket_code], tb.[bucket_label], tb.[midpoint_days], tb.[sort_order], yc.[zero_rate]
),
SensitivityCalculations AS (
    SELECT 
        g.[bucket_code],
        g.[bucket_label],
        g.[sort_order],
        g.[activos_repreciables],
        g.[pasivos_repreciables],
        g.[activos_repreciables] - g.[pasivos_repreciables] AS [brecha_repreciacion_clp],
        g.[midpoint_days] / 365.0 AS [plazo_anos_t],
        -- Duración modificada aproximada = t / (1 + r)
        (g.[midpoint_days] / 365.0) / (1.0 + g.[curva_cero]) AS [duracion_modificada],
        -- Shock paralelo estandarizado (+200 bps = +0.0200)
        -1.0 * (g.[activos_repreciables] - g.[pasivos_repreciables]) 
             * ((g.[midpoint_days] / 365.0) / (1.0 + g.[curva_cero])) 
             * 0.0200 AS [delta_eve_shock_up_clp],
        -- Shock paralelo a la baja (-200 bps = -0.0200)
        1.0 * (g.[activos_repreciables] - g.[pasivos_repreciables]) 
            * ((g.[midpoint_days] / 365.0) / (1.0 + g.[curva_cero])) 
            * 0.0200 AS [delta_eve_shock_down_clp]
    FROM RepricingGaps g
)
SELECT 
    @p_position_date_key AS [position_date_key],
    s.[bucket_code],
    s.[bucket_label],
    s.[sort_order],
    s.[activos_repreciables],
    s.[pasivos_repreciables],
    s.[brecha_repreciacion_clp],
    s.[duracion_modificada],
    s.[delta_eve_shock_up_clp],
    s.[delta_eve_shock_down_clp]
FROM SensitivityCalculations s;
GO

-- Vista resumen ejecutivo C56 IRRBB
CREATE VIEW [dwh].[vw_c56_irrbb_summary]
AS
WITH LatestPosition AS (
    SELECT MAX([position_date_key]) AS [max_pos_key]
    FROM [dwh].[fact_contractual_cashflows]
),
AggregatedShocks AS (
    SELECT 
        pos.[max_pos_key] AS [position_date_key],
        SUM(i.[delta_eve_shock_up_clp])   AS [delta_eve_subida_200pbs_clp],
        SUM(i.[delta_eve_shock_down_clp]) AS [delta_eve_bajada_200pbs_clp]
    FROM LatestPosition pos
    CROSS APPLY [dwh].[itvf_c56_irrbb](pos.[max_pos_key]) i
    GROUP BY pos.[max_pos_key]
)
SELECT 
    a.[position_date_key],
    a.[delta_eve_subida_200pbs_clp],
    a.[delta_eve_bajada_200pbs_clp],
    rp.[param_value] AS [capital_basico_tier1_clp],
    CAST(ROUND((ABS(CASE 
        WHEN a.[delta_eve_subida_200pbs_clp] < a.[delta_eve_bajada_200pbs_clp] THEN a.[delta_eve_subida_200pbs_clp] 
        ELSE a.[delta_eve_bajada_200pbs_clp] 
    END) / rp.[param_value]) * 100.0, 4) AS DECIMAL(9,4)) AS [peor_impacto_eve_pct_tier1],
    CASE 
        WHEN (ABS(CASE 
            WHEN a.[delta_eve_subida_200pbs_clp] < a.[delta_eve_bajada_200pbs_clp] THEN a.[delta_eve_subida_200pbs_clp] 
            ELSE a.[delta_eve_bajada_200pbs_clp] 
        END) / rp.[param_value]) > 0.1500
        THEN N'ALERTA: BANCO ATÍPICO C56 (Delta EVE > 15% Tier 1)'
        ELSE N'CUMPLE TOLERANCIA IRRBB'
    END AS [diagnostico_irrbb]
FROM AggregatedShocks a
CROSS JOIN [dwh].[regulatory_parameter] rp
WHERE rp.[param_code] = 'CAPITAL_BASICO_CLP';
GO

-- -----------------------------------------------------------------------------
-- Permisos de lectura para el rol de reportería
-- -----------------------------------------------------------------------------
GRANT SELECT  ON [dwh].[vw_c55_lcr_components_daily]   TO [app_report_reader];
GRANT SELECT  ON [dwh].[vw_c55_nsfr_components_daily]  TO [app_report_reader];
GRANT SELECT  ON [dwh].[vw_cmf_c55_lcr_nsfr_official]  TO [app_report_reader];
GRANT SELECT  ON [dwh].[itvf_c54_maturity_ladder]      TO [app_report_reader];
GRANT SELECT  ON [dwh].[itvf_c56_irrbb]                TO [app_report_reader];
GRANT SELECT  ON [dwh].[vw_c56_irrbb_summary]          TO [app_report_reader];
GRANT EXECUTE ON [dwh].[usp_audit_c46_provisions_time_travel] TO [app_report_reader];
GO

PRINT N'05_regulatory_time_travel_audit.sql compilado exitosamente: vistas LCR/NSFR, C46 bitemporal, C54 y C56.';
GO
