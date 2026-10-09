-- =============================================================================
-- 05_regulatory_time_travel_audit.sql
-- =============================================================================
-- Consultas de Auditoría Regulatoria y Time-Travel Bitemporal
-- Normativa CMF: C46 (Cartera y Provisiones), C54 (IRRBB), C55 (Liquidez)
-- Combina:
--   - FOR SYSTEM_TIME AS OF (Aserción de Sistema / Fecha de Emisión del Reporte)
--   - BETWEEN ValidFrom AND ValidTo (Vigencia Económica del Negocio)
-- =============================================================================

USE [CoreBanking];
GO

-- -----------------------------------------------------------------------------
-- CONSULTA 1: AUDITORÍA RETROSPECTIVA DE PROVISIONES Y SALDOS CMF (C46)
-- Resuelve: "¿Qué se reportó a la CMF al 31-Dic-2024 según lo conocido al 15-Ene-2025
--            versus lo que los sistemas saben hoy tras ajustes posteriores?"
-- -----------------------------------------------------------------------------

DECLARE @TargetBusinessDate DATETIME2(0) = '2024-12-31 23:59:59'; -- Corte económico normativo
DECLARE @AuditSystemTime    DATETIME2(0) = '2025-01-15 18:00:00'; -- Fecha de corte de envío regulatorio

WITH Snapshot_AsReportedAtSystemDate AS (
    -- 1. LO QUE EL BANCO REPORTÓ OFICIALMENTE AL REGULADOR
    SELECT 
        c.[contract_id],
        c.[contract_number],
        c.[partner_id],
        c.[product_type],
        c.[currency_code],
        c.[current_balance]       AS [balance_as_reported],
        c.[annual_nominal_rate]   AS [rate_as_reported],
        c.[contract_status]       AS [status_as_reported],
        c.[SysStartTime]          AS [sys_start_reported],
        c.[SysEndTime]            AS [sys_end_reported]
    FROM [core].[contract_profile] 
    FOR SYSTEM_TIME AS OF @AuditSystemTime AS c
    WHERE @TargetBusinessDate >= c.[ValidFrom] 
      AND @TargetBusinessDate <  c.[ValidTo]
),
Snapshot_AsKnownToday AS (
    -- 2. LO QUE EL BANCO SABE HOY SOBRE ESA MISMA FECHA ECONÓMICA
    SELECT 
        c.[contract_id],
        c.[contract_number],
        c.[partner_id],
        c.[product_type],
        c.[currency_code],
        c.[current_balance]       AS [balance_as_known_today],
        c.[annual_nominal_rate]   AS [rate_as_known_today],
        c.[contract_status]       AS [status_as_known_today],
        c.[SysStartTime]          AS [sys_start_today],
        c.[SysEndTime]            AS [sys_end_today]
    FROM [core].[contract_profile] AS c
    WHERE @TargetBusinessDate >= c.[ValidFrom] 
      AND @TargetBusinessDate <  c.[ValidTo]
)
SELECT 
    ISNULL(rep.[contract_id], tod.[contract_id])         AS [contract_id],
    ISNULL(rep.[contract_number], tod.[contract_number]) AS [contract_number],
    p.[tax_id]                                           AS [partner_rut],
    p.[full_name]                                        AS [partner_name],
    ISNULL(rep.[currency_code], tod.[currency_code])     AS [currency_code],
    
    -- Comparación de Saldos
    ISNULL(rep.[balance_as_reported], 0.0000)            AS [saldo_reportado_original_cmf],
    ISNULL(tod.[balance_as_known_today], 0.0000)         AS [saldo_real_corregido_hoy],
    (ISNULL(tod.[balance_as_known_today], 0.0000) - 
     ISNULL(rep.[balance_as_reported], 0.0000))          AS [delta_saldo_discrepancia],
    
    -- Comparación de Estados de Riesgo
    rep.[status_as_reported]                             AS [estado_declarado_original],
    tod.[status_as_known_today]                          AS [estado_actualizado_hoy],
    
    -- Diagnóstico Forense Regulatorio
    CASE 
        WHEN rep.[contract_id] IS NULL THEN 'OPERACION_INGRESADA_RETROACTIVAMENTE'
        WHEN tod.[contract_id] IS NULL THEN 'OPERACION_ANULADA_POST_CORTE'
        WHEN rep.[balance_as_reported] <> tod.[balance_as_known_today] THEN 'AJUSTE_DE_SALDO_EXTEMPORANEO'
        WHEN rep.[status_as_reported] <> tod.[status_as_known_today] THEN 'CAMBIO_DE_ESTADO_RETROACTIVO'
        ELSE 'CONSISTENTE_SIN_OBSERVACIONES'
    END AS [diagnostico_auditoria_cmf]
FROM Snapshot_AsReportedAtSystemDate rep
FULL OUTER JOIN Snapshot_AsKnownToday tod 
    ON rep.[contract_id] = tod.[contract_id]
LEFT JOIN [dwh].[dim_partner] p 
    ON ISNULL(rep.[partner_id], tod.[partner_id]) = p.[partner_id] 
   AND p.[is_current] = 1
WHERE (rep.[balance_as_reported] <> tod.[balance_as_known_today])
   OR (rep.[status_as_reported] <> tod.[status_as_known_today])
   OR (rep.[contract_id] IS NULL)
   OR (tod.[contract_id] IS NULL)
ORDER BY ABS(ISNULL(tod.[balance_as_known_today], 0) - ISNULL(rep.[balance_as_reported], 0)) DESC;
GO

-- -----------------------------------------------------------------------------
-- CONSULTA 2: RECONSTRUCCIÓN DE MATRIZ DE LIQUIDEZ Y LCR A CUALQUIER MOMENTO HISTÓRICO
-- -----------------------------------------------------------------------------
DECLARE @PointInTimeSystem DATETIME2(0) = '2025-01-31 23:59:59';

SELECT 
    f.[snapshot_date_key],
    SUM(CASE WHEN f.[is_hqla_eligible] = 1 AND f.[hqla_level] = 'L1' THEN f.[current_balance_clp] ELSE 0 END) AS [alac_nivel_1_clp],
    SUM(CASE WHEN f.[is_hqla_eligible] = 1 AND f.[hqla_level] IN ('L2A', 'L2B') THEN f.[current_balance_clp] ELSE 0 END) AS [alac_nivel_2_clp],
    SUM(CASE WHEN f.[is_hqla_eligible] = 1 THEN f.[current_balance_clp] ELSE 0 END) AS [alac_total_clp],
    SUM(f.[nsfr_weighted_rsf_clp]) AS [nsfr_fer_ponderado_clp]
FROM [dwh].[fact_account_balance_snapshot] f
WHERE f.[snapshot_date_key] = 20250131
GROUP BY f.[snapshot_date_key];
GO
