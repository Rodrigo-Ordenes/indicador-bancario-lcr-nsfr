-- =============================================================================
-- 02_stg_audit_lineage.sql
-- =============================================================================
-- Capa 2: Staging & Control Layer (Kimball ETL Kitchen & Linaje)
-- Implementa:
--   - Bitácora de ejecución de lotes y linaje (stg.load_execution_log)
--   - Función determinista de Checksum SHA-256 para detección delta
--   - Tablas de preparación CDC Raw Delta
-- =============================================================================

USE [CoreBanking];
GO

-- 1. TABLA: stg.load_execution_log (Bitácora de Lotes y Auditoría ETL)
IF OBJECT_ID(N'stg.load_execution_log', N'U') IS NOT NULL DROP TABLE [stg].[load_execution_log];
GO

CREATE TABLE [stg].[load_execution_log] (
    [batch_id]              BIGINT IDENTITY(1,1) NOT NULL,
    [batch_uuid]            UNIQUEIDENTIFIER     NOT NULL CONSTRAINT [DF_batch_uuid] DEFAULT (NEWID()),
    [pipeline_name]         VARCHAR(100)         NOT NULL,
    [target_layer]          VARCHAR(20)          NOT NULL, -- STG, CORE, DWH
    [source_entity]         VARCHAR(100)         NOT NULL,
    [target_entity]         VARCHAR(100)         NOT NULL,
    [extract_start_time]    DATETIME2(0)         NOT NULL,
    [extract_end_time]      DATETIME2(0)         NULL,
    [records_extracted]     INT                  NOT NULL CONSTRAINT [DF_batch_ext] DEFAULT (0),
    [records_loaded]        INT                  NOT NULL CONSTRAINT [DF_batch_lod] DEFAULT (0),
    [records_rejected]      INT                  NOT NULL CONSTRAINT [DF_batch_rej] DEFAULT (0),
    [status]                VARCHAR(20)          NOT NULL, -- RUNNING, SUCCESS, FAILED, RECONCILED
    [reconciliation_diff]   DECIMAL(19,4)        NULL,     -- Diferencia financiera encontrada en cuadre
    [error_message]         NVARCHAR(MAX)        NULL,
    [created_at]            DATETIME2(0)         NOT NULL CONSTRAINT [DF_batch_created] DEFAULT (SYSDATETIME()),
    CONSTRAINT [PK_load_execution_log] PRIMARY KEY CLUSTERED ([batch_id] ASC)
);
GO

CREATE NONCLUSTERED INDEX [IX_load_execution_log_status]
ON [stg].[load_execution_log] ([pipeline_name] ASC, [status] ASC, [extract_start_time] DESC);
GO

-- 2. FUNCIÓN: Cálculo de Checksum Hash SHA-256 para Detección Delta
CREATE OR ALTER FUNCTION [stg].[fn_calculate_contract_checksum] (
    @p_partner_id         BIGINT,
    @p_contract_number    VARCHAR(50),
    @p_product_code       VARCHAR(20),
    @p_currency_code      CHAR(3),
    @p_current_balance    DECIMAL(19,4),
    @p_contract_rate      DECIMAL(8,4),
    @p_risk_category_cmf  VARCHAR(5),
    @p_days_past_due      INT
)
RETURNS VARBINARY(32)
WITH SCHEMABINDING
AS
BEGIN
    RETURN HASHBYTES('SHA2_256', 
        CONCAT(
            @p_partner_id, N'|',
            @p_contract_number, N'|',
            @p_product_code, N'|',
            @p_currency_code, N'|',
            CONVERT(NVARCHAR(30), @p_current_balance), N'|',
            CONVERT(NVARCHAR(20), @p_contract_rate), N'|',
            ISNULL(@p_risk_category_cmf, N'NA'), N'|',
            @p_days_past_due
        )
    );
END;
GO

-- 3. TABLA DE STAGING DELTA CDC: stg.cdc_raw_delta_contract
IF OBJECT_ID(N'stg.cdc_raw_delta_contract', N'U') IS NOT NULL DROP TABLE [stg].[cdc_raw_delta_contract];
GO

CREATE TABLE [stg].[cdc_raw_delta_contract] (
    [staging_id]          BIGINT IDENTITY(1,1) NOT NULL,
    [batch_id]            BIGINT               NOT NULL,
    [cdc_operation]       INT                  NOT NULL, -- 1=Delete, 2=Insert, 3=Before Update, 4=After Update
    [contract_id]         BIGINT               NOT NULL,
    [partner_id]          BIGINT               NOT NULL,
    [contract_number]     VARCHAR(50)          NOT NULL,
    [product_type]        VARCHAR(30)          NOT NULL,
    [currency_code]       CHAR(3)              NOT NULL,
    [original_amount]     DECIMAL(19,4)        NOT NULL,
    [current_balance]     DECIMAL(19,4)        NOT NULL,
    [annual_nominal_rate] DECIMAL(8,4)         NOT NULL,
    [spread_rate]         DECIMAL(8,4)         NOT NULL,
    [repricing_frequency] VARCHAR(10)          NOT NULL,
    [cmf_portfolio_type]  CHAR(1)              NOT NULL,
    [contract_status]     VARCHAR(20)          NOT NULL,
    [valid_from]          DATETIME2(0)         NOT NULL,
    [row_checksum]        VARBINARY(32)        NOT NULL,
    [is_processed]        BIT                  NOT NULL CONSTRAINT [DF_delta_processed] DEFAULT (0),
    [created_at]          DATETIME2(0)         NOT NULL CONSTRAINT [DF_delta_created] DEFAULT (SYSDATETIME()),
    CONSTRAINT [PK_cdc_raw_delta_contract] PRIMARY KEY CLUSTERED ([staging_id] ASC),
    CONSTRAINT [FK_cdc_delta_batch] FOREIGN KEY ([batch_id]) 
        REFERENCES [stg].[load_execution_log] ([batch_id])
);
GO

CREATE NONCLUSTERED INDEX [IX_cdc_raw_delta_unprocessed]
ON [stg].[cdc_raw_delta_contract] ([is_processed] ASC, [contract_id] ASC);
GO
