-- =============================================================================
-- 03_core_bitemporal_ods.sql
-- =============================================================================
-- Capa 3: Core Bitemporal / ODS (Asserted Versioning Framework)
-- Implementa:
--   - Bitemporalidad real en SQL Server 2017:
--     1. System Time (Transaction Time): Native System-Versioned Temporal Tables
--     2. Valid Time (Business Effective Time): Columnas ValidFrom y ValidTo
--   - Tabla de historial dedicada: core.contract_profile_history
--   - Procedimiento de sincronización atómica desde Staging Delta
-- =============================================================================

USE [CoreBanking];
GO

-- 1. Desvincular temporalmente versionado si ya existía para permitir recreación limpia
IF OBJECT_ID(N'core.contract_profile', N'U') IS NOT NULL
BEGIN
    IF OBJECTPROPERTY(OBJECT_ID(N'core.contract_profile'), 'TableTemporalType') = 2
    BEGIN
        ALTER TABLE [core].[contract_profile] SET (SYSTEM_VERSIONING = OFF);
    END;
    DROP TABLE [core].[contract_profile];
END;
GO

IF OBJECT_ID(N'core.contract_profile_history', N'U') IS NOT NULL 
    DROP TABLE [core].[contract_profile_history];
GO

-- 2. TABLA HISTORIAL ASOCIADA (History Table)
CREATE TABLE [core].[contract_profile_history] (
    [contract_id]           BIGINT               NOT NULL,
    [partner_id]            BIGINT               NOT NULL,
    [contract_number]       VARCHAR(50)          NOT NULL,
    [product_type]          VARCHAR(30)          NOT NULL, -- Hipotecario, Comercial, Consumo
    [currency_code]         CHAR(3)              NOT NULL,
    [original_amount]       DECIMAL(19,4)        NOT NULL,
    [current_balance]       DECIMAL(19,4)        NOT NULL,
    [annual_nominal_rate]   DECIMAL(8,4)         NOT NULL,
    [spread_rate]           DECIMAL(8,4)         NOT NULL,
    [repricing_frequency]   VARCHAR(10)          NOT NULL, -- FIJO, MENSUAL, TRIMESTRAL
    [cmf_portfolio_type]    CHAR(1)              NOT NULL, -- H (Hipotecario), C (Comercial), N (Consumo)
    [contract_status]       VARCHAR(20)          NOT NULL, -- VIGENTE, MOROSO, CASTIGADO
    
    -- DIMENSIÓN 1: BUSINESS VALID TIME (Vigencia económica en el mundo real)
    [ValidFrom]             DATETIME2(0)         NOT NULL,
    [ValidTo]               DATETIME2(0)         NOT NULL,
    
    -- DIMENSIÓN 2: SYSTEM TRANSACTION TIME (Tiempo de aserción en el motor SQL)
    [SysStartTime]          DATETIME2(0)         NOT NULL,
    [SysEndTime]            DATETIME2(0)         NOT NULL
);
GO

CREATE CLUSTERED INDEX [IXC_contract_profile_history] 
ON [core].[contract_profile_history] ([contract_id] ASC, [SysEndTime] ASC, [SysStartTime] ASC);
GO

-- 3. TABLA PRINCIPAL BITEMPORAL CON SYSTEM-VERSIONING
CREATE TABLE [core].[contract_profile] (
    [contract_id]           BIGINT               NOT NULL,
    [partner_id]            BIGINT               NOT NULL,
    [contract_number]       VARCHAR(50)          NOT NULL,
    [product_type]          VARCHAR(30)          NOT NULL,
    [currency_code]         CHAR(3)              NOT NULL,
    [original_amount]       DECIMAL(19,4)        NOT NULL,
    [current_balance]       DECIMAL(19,4)        NOT NULL,
    [annual_nominal_rate]   DECIMAL(8,4)         NOT NULL,
    [spread_rate]           DECIMAL(8,4)         NOT NULL,
    [repricing_frequency]   VARCHAR(10)          NOT NULL,
    [cmf_portfolio_type]    CHAR(1)              NOT NULL,
    [contract_status]       VARCHAR(20)          NOT NULL,
    
    -- BUSINESS EFFECTIVE TIME
    [ValidFrom]             DATETIME2(0)         NOT NULL,
    [ValidTo]               DATETIME2(0)         NOT NULL,
    
    -- SYSTEM TRANSACTION TIME (Nativo SQL Server 2017)
    [SysStartTime]          DATETIME2(0) GENERATED ALWAYS AS ROW START NOT NULL,
    [SysEndTime]            DATETIME2(0) GENERATED ALWAYS AS ROW END   NOT NULL,
    PERIOD FOR SYSTEM_TIME ([SysStartTime], [SysEndTime]),

    CONSTRAINT [PK_contract_profile] PRIMARY KEY CLUSTERED ([contract_id] ASC),
    CONSTRAINT [CK_contract_business_period] CHECK ([ValidFrom] < [ValidTo])
)
WITH (
    SYSTEM_VERSIONING = ON (
        HISTORY_TABLE = [core].[contract_profile_history],
        DATA_CONSISTENCY_CHECK = ON
    )
);
GO

CREATE NONCLUSTERED INDEX [IX_contract_profile_business_time]
ON [core].[contract_profile] ([ValidFrom] ASC, [ValidTo] ASC)
INCLUDE ([partner_id], [current_balance], [contract_status]);
GO

-- 4. PROCEDIMIENTO: Sincronización Bitemporal desde Staging Delta
CREATE OR ALTER PROCEDURE [core].[usp_sync_contracts_from_staging]
    @p_batch_id BIGINT
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    -- Aplicar Updates e Inserts desde CDC Staging
    MERGE [core].[contract_profile] AS target
    USING (
        SELECT 
            d.[contract_id], d.[partner_id], d.[contract_number], d.[product_type],
            d.[currency_code], d.[original_amount], d.[current_balance], d.[annual_nominal_rate],
            d.[spread_rate], d.[repricing_frequency], d.[cmf_portfolio_type], d.[contract_status],
            d.[valid_from]
        FROM [stg].[cdc_raw_delta_contract] d
        WHERE d.[batch_id] = @p_batch_id AND d.[is_processed] = 0
    ) AS src
    ON (target.[contract_id] = src.[contract_id])
    WHEN MATCHED AND (
        target.[current_balance] <> src.[current_balance] OR
        target.[annual_nominal_rate] <> src.[annual_nominal_rate] OR
        target.[contract_status] <> src.[contract_status]
    ) THEN
        UPDATE SET 
            target.[current_balance]     = src.[current_balance],
            target.[annual_nominal_rate] = src.[annual_nominal_rate],
            target.[spread_rate]         = src.[spread_rate],
            target.[contract_status]     = src.[contract_status],
            target.[ValidFrom]           = src.[valid_from]
            -- SysStartTime y SysEndTime son gestionados automáticamente por el motor
    WHEN NOT MATCHED THEN
        INSERT (
            [contract_id], [partner_id], [contract_number], [product_type],
            [currency_code], [original_amount], [current_balance], [annual_nominal_rate],
            [spread_rate], [repricing_frequency], [cmf_portfolio_type], [contract_status],
            [ValidFrom], [ValidTo]
        )
        VALUES (
            src.[contract_id], src.[partner_id], src.[contract_number], src.[product_type],
            src.[currency_code], src.[original_amount], src.[current_balance], src.[annual_nominal_rate],
            src.[spread_rate], src.[repricing_frequency], src.[cmf_portfolio_type], src.[contract_status],
            src.[valid_from], '9999-12-31 23:59:59'
        );

    -- Marcar registros staging como procesados
    UPDATE [stg].[cdc_raw_delta_contract]
    SET [is_processed] = 1
    WHERE [batch_id] = @p_batch_id;

    COMMIT TRANSACTION;
END;
GO
