-- =============================================================================
-- 02_stg_audit_lineage.sql
-- =============================================================================
-- Capa 2: Staging, control de cargas y linaje (la "cocina" de Kimball).
--
-- Contiene:
--   * stg.load_execution_log        : bitácora de cada lote (conteos y diferencia de conciliación).
--   * stg.cdc_landing_contract      : aterrizaje de cambios netos CDC del sistema legado.
--   * stg.itvf_contract_row_checksum: checksum SHA-256 de fila para detección de deltas.
--     Se implementa como función inline (iTVF) y no escalar: en SQL Server 2017 una
--     UDF escalar impide planes paralelos y se ejecuta fila a fila (el inlining de
--     UDF escalares recién existe en 2019).
--   * stg.map_legacy_product        : traducción de códigos legados a producto,
--                                     cuenta contable y tratamiento regulatorio.
--   * stg.cdc_watermark / stg.outbox_checkpoint / stg.event_inbox: avance de
--     consumidores con semántica effectively-once.
--   * stg.reconciliation_result / stg.restatement_event_log.
-- =============================================================================

USE [CoreBanking];
GO

DROP PROCEDURE IF EXISTS [stg].[usp_log_batch_end];
DROP PROCEDURE IF EXISTS [stg].[usp_log_batch_start];
DROP FUNCTION  IF EXISTS [stg].[itvf_contract_row_checksum];
DROP TABLE IF EXISTS [stg].[reconciliation_result];
DROP TABLE IF EXISTS [stg].[restatement_event_log];
DROP TABLE IF EXISTS [stg].[event_inbox];
DROP TABLE IF EXISTS [stg].[outbox_checkpoint];
DROP TABLE IF EXISTS [stg].[cdc_watermark];
DROP TABLE IF EXISTS [stg].[cdc_landing_contract];
DROP TABLE IF EXISTS [stg].[map_legacy_product];
DROP TABLE IF EXISTS [stg].[load_execution_log];
GO

-- -----------------------------------------------------------------------------
-- 1. Bitácora de lotes
-- -----------------------------------------------------------------------------
CREATE TABLE [stg].[load_execution_log] (
    [batch_id]             BIGINT IDENTITY(1,1) NOT NULL,
    [batch_uuid]           UNIQUEIDENTIFIER NOT NULL CONSTRAINT [DF_load_log_uuid] DEFAULT (NEWSEQUENTIALID()),
    [pipeline_name]        VARCHAR(100)     NOT NULL,
    [target_layer]         VARCHAR(10)      NOT NULL,
    [source_entity]        VARCHAR(200)     NOT NULL,
    [target_entity]        VARCHAR(200)     NOT NULL,
    [extract_start_time]   DATETIME2(7)     NOT NULL,
    [extract_end_time]     DATETIME2(7)     NULL,
    [records_extracted]    BIGINT           NOT NULL CONSTRAINT [DF_load_log_ext] DEFAULT (0),
    [records_loaded]       BIGINT           NOT NULL CONSTRAINT [DF_load_log_lod] DEFAULT (0),
    [records_rejected]     BIGINT           NOT NULL CONSTRAINT [DF_load_log_rej] DEFAULT (0),
    [status]               VARCHAR(15)      NOT NULL,
    [reconciliation_diff]  DECIMAL(19,4)    NULL,
    [error_message]        NVARCHAR(4000)   NULL,
    [created_at_utc]       DATETIME2(7)     NOT NULL CONSTRAINT [DF_load_log_created] DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT [PK_load_execution_log] PRIMARY KEY CLUSTERED ([batch_id]),
    CONSTRAINT [UQ_load_execution_log_uuid] UNIQUE ([batch_uuid]),
    CONSTRAINT [CK_load_execution_log_layer] CHECK ([target_layer] IN ('STG','CORE','DWH','OLTP')),
    CONSTRAINT [CK_load_execution_log_status] CHECK ([status] IN ('RUNNING','SUCCESS','FAILED','RECONCILED','DISCREPANCY'))
);
CREATE NONCLUSTERED INDEX [IX_load_execution_log_pipeline]
    ON [stg].[load_execution_log] ([pipeline_name], [status], [extract_start_time] DESC);
GO

CREATE PROCEDURE [stg].[usp_log_batch_start]
    @p_pipeline_name VARCHAR(100), @p_target_layer VARCHAR(10), @p_source_entity VARCHAR(200),
    @p_target_entity VARCHAR(200), @o_batch_id BIGINT OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO [stg].[load_execution_log] ([pipeline_name], [target_layer], [source_entity], [target_entity],
                                            [extract_start_time], [status])
    VALUES (@p_pipeline_name, @p_target_layer, @p_source_entity, @p_target_entity, SYSUTCDATETIME(), 'RUNNING');
    SET @o_batch_id = SCOPE_IDENTITY();
END;
GO

CREATE PROCEDURE [stg].[usp_log_batch_end]
    @p_batch_id BIGINT, @p_status VARCHAR(15), @p_extracted BIGINT = 0, @p_loaded BIGINT = 0, @p_rejected BIGINT = 0,
    @p_reconciliation_diff DECIMAL(19,4) = NULL, @p_error_message NVARCHAR(4000) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE [stg].[load_execution_log]
    SET [extract_end_time] = SYSUTCDATETIME(), [status] = @p_status, [records_extracted] = @p_extracted,
        [records_loaded] = @p_loaded, [records_rejected] = @p_rejected, [reconciliation_diff] = @p_reconciliation_diff,
        [error_message] = @p_error_message
    WHERE [batch_id] = @p_batch_id;
END;
GO

-- -----------------------------------------------------------------------------
-- 2. Mapeo de productos legados -> modelo canónico
-- -----------------------------------------------------------------------------
CREATE TABLE [stg].[map_legacy_product] (
    [legacy_product_code]       VARCHAR(30)  NOT NULL,
    [product_code]              VARCHAR(30)  NOT NULL,
    [gl_account_id]             BIGINT       NOT NULL,
    [liquidity_treatment_code]  VARCHAR(40)  NOT NULL,
    [amortization_type]         VARCHAR(10)  NOT NULL,
    [payment_frequency_months]  INT          NULL,
    [cmf_portfolio_type]        VARCHAR(3)   NOT NULL,
    CONSTRAINT [PK_map_legacy_product] PRIMARY KEY CLUSTERED ([legacy_product_code]),
    CONSTRAINT [CK_map_legacy_amortization] CHECK ([amortization_type] IN ('BULLET','POOL','NONE'))
);
INSERT INTO [stg].[map_legacy_product] VALUES
    ('COMERCIAL',          'COM_CREDITO', 140101, 'INFLOW_COMERCIAL',          'BULLET', 1,    'C'),
    ('LINEA_COMERCIAL',    'COM_LINEA',   140102, 'LINEA_CREDITO_CORP',        'BULLET', 1,    'C'),
    ('HIPOTECARIO',        'HIPOTECARIO', 140201, 'INFLOW_HIPOTECARIO',        'BULLET', 1,    'H'),
    ('CONSUMO',            'CONSUMO',     140301, 'INFLOW_CONSUMO',            'BULLET', 1,    'N'),
    ('DEPOSITO_PLAZO_INST','DP_INST',     220201, 'DEP_MAYORISTA_NO_OPER_FIN', 'BULLET', NULL, 'N/A'),
    ('INVERSION_SOBERANA', 'BONO_SOB',    130101, 'HQLA_L1_SOBERANO',          'BULLET', 6,    'N/A');
GO

-- -----------------------------------------------------------------------------
-- 3. Aterrizaje CDC (cambios netos del legado; ver stg.usp_ingest_cdc_legacy_contracts en el script 06)
-- -----------------------------------------------------------------------------
CREATE TABLE [stg].[cdc_landing_contract] (
    [staging_id]           BIGINT IDENTITY(1,1) NOT NULL,
    [batch_id]             BIGINT        NOT NULL,
    [cdc_start_lsn]        BINARY(10)    NOT NULL,
    [cdc_operation]        INT           NOT NULL,   -- 1 = delete, 2 = insert, 4 = update (net changes)
    [contract_id]          BIGINT        NOT NULL,
    [partner_id]           BIGINT        NOT NULL,
    [contract_number]      VARCHAR(50)   NOT NULL,
    [legacy_product_code]  VARCHAR(30)   NOT NULL,
    [branch_code]          VARCHAR(20)   NOT NULL,
    [currency_code]        CHAR(3)       NOT NULL,
    [origination_date]     DATE          NOT NULL,
    [maturity_date]        DATE          NULL,
    [original_amount]      DECIMAL(19,4) NOT NULL,
    [current_balance]      DECIMAL(19,4) NOT NULL,
    [committed_limit]      DECIMAL(19,4) NOT NULL,
    [annual_nominal_rate]  DECIMAL(12,6) NOT NULL,
    [spread_rate]          DECIMAL(12,6) NOT NULL,
    [repricing_frequency]  VARCHAR(12)   NOT NULL,
    [cmf_rating]           VARCHAR(4)    NOT NULL,
    [days_past_due]        INT           NOT NULL,
    [contract_status]      VARCHAR(20)   NOT NULL,
    [valid_from]           DATETIME2(2)  NOT NULL,
    [row_checksum]         BINARY(32)    NOT NULL,
    [is_processed]         BIT           NOT NULL CONSTRAINT [DF_cdc_landing_processed] DEFAULT (0),
    [created_at_utc]       DATETIME2(7)  NOT NULL CONSTRAINT [DF_cdc_landing_created] DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT [PK_cdc_landing_contract] PRIMARY KEY CLUSTERED ([staging_id]),
    CONSTRAINT [FK_cdc_landing_batch] FOREIGN KEY ([batch_id]) REFERENCES [stg].[load_execution_log] ([batch_id])
);
CREATE NONCLUSTERED INDEX [IX_cdc_landing_pending]
    ON [stg].[cdc_landing_contract] ([batch_id], [is_processed], [contract_id]) INCLUDE ([row_checksum]);
GO

-- -----------------------------------------------------------------------------
-- 4. Checksum de fila (iTVF determinista; se usa con CROSS APPLY)
--    Formato canónico: decimales con escala fija, fechas ISO, NULL explícito.
-- -----------------------------------------------------------------------------
CREATE FUNCTION [stg].[itvf_contract_row_checksum] (
    @partner_id BIGINT, @contract_number VARCHAR(50), @legacy_product_code VARCHAR(30), @currency_code CHAR(3),
    @maturity_date DATE, @current_balance DECIMAL(19,4), @committed_limit DECIMAL(19,4),
    @annual_nominal_rate DECIMAL(12,6), @spread_rate DECIMAL(12,6), @cmf_rating VARCHAR(4), @days_past_due INT,
    @contract_status VARCHAR(20))
RETURNS TABLE
WITH SCHEMABINDING
AS
RETURN
    SELECT HASHBYTES('SHA2_256', CONCAT(
        @partner_id, '|', @contract_number, '|', @legacy_product_code, '|', @currency_code, '|',
        ISNULL(CONVERT(CHAR(8), @maturity_date, 112), 'NULL'), '|', CONVERT(VARCHAR(40), @current_balance), '|',
        CONVERT(VARCHAR(40), @committed_limit), '|', CONVERT(VARCHAR(40), @annual_nominal_rate), '|',
        CONVERT(VARCHAR(40), @spread_rate), '|', @cmf_rating, '|', @days_past_due, '|', @contract_status)) AS [row_checksum];
GO

-- -----------------------------------------------------------------------------
-- 5. Avance de consumidores (effectively-once)
-- -----------------------------------------------------------------------------
CREATE TABLE [stg].[cdc_watermark] (
    [capture_instance]  SYSNAME      NOT NULL,
    [last_lsn]          BINARY(10)   NOT NULL,
    [updated_at_utc]    DATETIME2(7) NOT NULL,
    CONSTRAINT [PK_cdc_watermark] PRIMARY KEY CLUSTERED ([capture_instance])
);

CREATE TABLE [stg].[outbox_checkpoint] (
    [consumer_name]   VARCHAR(60)  NOT NULL,
    [last_outbox_id]  BIGINT       NOT NULL,
    [updated_at_utc]  DATETIME2(7) NOT NULL,
    CONSTRAINT [PK_outbox_checkpoint] PRIMARY KEY CLUSTERED ([consumer_name])
);

-- La clave (consumer_name, event_uuid) convierte la entrega at-least-once del relay
-- en procesamiento effectively-once: una reentrega choca con la PK y se ignora.
CREATE TABLE [stg].[event_inbox] (
    [consumer_name]   VARCHAR(60)      NOT NULL,
    [event_uuid]      UNIQUEIDENTIFIER NOT NULL,
    [outbox_id]       BIGINT           NOT NULL,
    [event_type]      VARCHAR(60)      NOT NULL,
    [payload_json]    NVARCHAR(MAX)    NOT NULL,
    [received_at_utc] DATETIME2(7)     NOT NULL,
    [processed_at_utc] DATETIME2(7)    NULL,
    CONSTRAINT [PK_event_inbox] PRIMARY KEY CLUSTERED ([consumer_name], [event_uuid])
);
GO

-- -----------------------------------------------------------------------------
-- 6. Resultados de conciliación y bitácora de reexpresiones
-- -----------------------------------------------------------------------------
CREATE TABLE [stg].[reconciliation_result] (
    [batch_id]             BIGINT        NOT NULL,
    [reconciliation_date]  DATE          NOT NULL,
    [contract_id]          BIGINT        NOT NULL,
    [legacy_balance]       DECIMAL(19,4) NULL,
    [core_balance]         DECIMAL(19,4) NULL,
    [ledger_balance]       DECIMAL(19,4) NOT NULL,
    [dwh_balance]          DECIMAL(19,4) NULL,
    [diff_legacy_ledger]   AS (ISNULL([legacy_balance], [ledger_balance]) - [ledger_balance]) PERSISTED,
    [diff_core_ledger]     AS (ISNULL([core_balance], [ledger_balance]) - [ledger_balance]) PERSISTED,
    [diff_ledger_dwh]      AS ([ledger_balance] - ISNULL([dwh_balance], 0)) PERSISTED,
    [status]               VARCHAR(12)   NOT NULL,
    CONSTRAINT [PK_reconciliation_result] PRIMARY KEY CLUSTERED ([batch_id], [contract_id]),
    CONSTRAINT [FK_reconciliation_batch] FOREIGN KEY ([batch_id]) REFERENCES [stg].[load_execution_log] ([batch_id])
);

CREATE TABLE [stg].[restatement_event_log] (
    [event_key]       VARCHAR(100) NOT NULL,
    [known_at_utc]    DATETIME2(7) NOT NULL,
    [first_date]      DATE         NOT NULL,
    [last_date]       DATE         NOT NULL,
    [rows_restated]   INT          NOT NULL,
    [processed_at_utc] DATETIME2(7) NOT NULL,
    CONSTRAINT [PK_restatement_event_log] PRIMARY KEY CLUSTERED ([event_key])
);
GO

GRANT EXECUTE ON SCHEMA::[stg] TO [app_etl_operator];
GRANT SELECT  ON [stg].[load_execution_log]     TO [app_report_reader];
GRANT SELECT  ON [stg].[reconciliation_result]  TO [app_report_reader];
GO
