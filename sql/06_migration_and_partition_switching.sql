-- =============================================================================
-- 06_migration_and_partition_switching.sql
-- =============================================================================
-- Estrategia de Migración Zero-Downtime y Mantenimiento de Particiones
-- Implementa:
--   - Activación y afinamiento de CDC asíncrono
--   - Procedimiento de Backfilling histórico hacia ODS bitemporal
--   - Carga masiva Zero-Lock mediante Partition Switching en el DWH
--   - Procedimiento de Reconciliación nocturna Legacy vs Ledger Inmutable
-- =============================================================================

USE [CoreBanking];
GO

-- -----------------------------------------------------------------------------
-- 1. ACTIVACIÓN DE CDC (CHANGE DATA CAPTURE) EN TABLAS VIVAS LEGADAS
-- -----------------------------------------------------------------------------

-- Habilitar CDC a nivel de base de datos
IF NOT EXISTS (SELECT 1 FROM sys.databases WHERE name = N'CoreBanking' AND is_cdc_enabled = 1)
BEGIN
    EXEC sys.sp_cdc_enable_db;
END;
GO

-- Simulación de tabla operativa legada para ejemplo de migración
IF OBJECT_ID(N'dbo.tbl_prestamos_legacy', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[tbl_prestamos_legacy] (
        [id_prestamo]       BIGINT IDENTITY(1001,1) PRIMARY KEY,
        [id_socio]          BIGINT        NOT NULL,
        [num_operacion]     VARCHAR(50)   NOT NULL,
        [tipo_credito]      VARCHAR(30)   NOT NULL,
        [moneda]            CHAR(3)       NOT NULL,
        [monto_otorgado]    DECIMAL(19,4) NOT NULL,
        [saldo_capital]     DECIMAL(19,4) NOT NULL,
        [tasa_anual]        DECIMAL(8,4)  NOT NULL,
        [spread]            DECIMAL(8,4)  NOT NULL,
        [frec_revision]     VARCHAR(10)   NOT NULL,
        [tipo_cartera_cmf]  CHAR(1)       NOT NULL,
        [estado_credito]    VARCHAR(20)   NOT NULL,
        [fecha_ult_mod]     DATETIME2(0)  NOT NULL DEFAULT (SYSDATETIME())
    );
END;
GO

-- Habilitar CDC en la tabla legada si no está activo
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = N'tbl_prestamos_legacy' AND is_tracked_by_cdc = 1)
BEGIN
    EXEC sys.sp_cdc_enable_table
        @source_schema        = N'dbo',
        @source_name          = N'tbl_prestamos_legacy',
        @role_name            = NULL,
        @supports_net_changes = 1,
        @capture_instance     = N'dbo_prestamos_legacy';
END;
GO

-- Afinamiento de Jobs CDC para mitigar impacto en I/O
EXEC sys.sp_cdc_change_job 
    @job_type = N'capture', 
    @maxscans = 10,       -- Máximos ciclos de escaneo del T-Log
    @maxtrans = 5000,     -- Límite de transacciones por ciclo
    @pollinginterval = 5; -- Pausa en segundos entre barridos
GO

-- -----------------------------------------------------------------------------
-- 2. PROCEDIMIENTO DE BACKFILLING HISTÓRICO POR LOTES
-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE [core].[usp_backfill_contract_profile_history]
    @p_start_date DATE,
    @p_end_date   DATE,
    @p_batch_size INT = 25000
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @v_current_cut DATE = @p_start_date;

    WHILE @v_current_cut <= @p_end_date
    BEGIN
        PRINT CONCAT('Procesando ventana de backfilling: ', @v_current_cut);

        -- Desactivar System-Versioning para inserción directa en History
        ALTER TABLE [core].[contract_profile] SET (SYSTEM_VERSIONING = OFF);

        BEGIN TRANSACTION;

        INSERT INTO [core].[contract_profile_history] (
            [contract_id], [partner_id], [contract_number], [product_type],
            [currency_code], [original_amount], [current_balance], [annual_nominal_rate],
            [spread_rate], [repricing_frequency], [cmf_portfolio_type], [contract_status],
            [ValidFrom], [ValidTo], [SysStartTime], [SysEndTime]
        )
        SELECT TOP (@p_batch_size)
            leg.[id_prestamo],
            leg.[id_socio],
            leg.[num_operacion],
            leg.[tipo_credito],
            leg.[moneda],
            leg.[monto_otorgado],
            leg.[saldo_capital],
            leg.[tasa_anual],
            leg.[spread],
            leg.[frec_revision],
            leg.[tipo_cartera_cmf],
            leg.[estado_credito],
            CAST(@v_current_cut AS DATETIME2(0)),
            DATEADD(MONTH, 1, CAST(@v_current_cut AS DATETIME2(0))),
            CAST(@v_current_cut AS DATETIME2(0)),
            '9999-12-31 23:59:59'
        FROM [dbo].[tbl_prestamos_legacy] leg
        WHERE NOT EXISTS (
            SELECT 1 FROM [core].[contract_profile_history] h
            WHERE h.[contract_id] = leg.[id_prestamo]
              AND h.[ValidFrom] = CAST(@v_current_cut AS DATETIME2(0))
        );

        COMMIT TRANSACTION;

        -- Reactivar System-Versioning con verificación de integridad
        ALTER TABLE [core].[contract_profile] SET (
            SYSTEM_VERSIONING = ON (
                HISTORY_TABLE = [core].[contract_profile_history],
                DATA_CONSISTENCY_CHECK = ON
            )
        );

        SET @v_current_cut = DATEADD(MONTH, 1, @v_current_cut);
    END;
END;
GO

-- -----------------------------------------------------------------------------
-- 3. PROCEDIMIENTO DE CARGA MASIVA ZERO-LOCK: PARTITION SWITCHING
-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE [dwh].[usp_load_partition_switch_balance_snapshot]
    @p_snapshot_date_key INT -- Ejemplo: 20250131
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @v_partition_number INT;
    SELECT @v_partition_number = $PARTITION.[pf_monthly_snapshot](@p_snapshot_date_key);

    IF @v_partition_number IS NULL
    BEGIN
        RAISERROR(N'La fecha especificada no corresponde a una partición válida.', 16, 1);
        RETURN -1;
    END;

    -- 1. Preparar tabla Staging idéntica no particionada
    IF OBJECT_ID(N'stg.fact_account_balance_staging', N'U') IS NOT NULL
        DROP TABLE [stg].[fact_account_balance_staging];

    CREATE TABLE [stg].[fact_account_balance_staging] (
        [snapshot_date_key]        INT           NOT NULL,
        [contract_id]              BIGINT        NOT NULL,
        [partner_key]              INT           NOT NULL,
        [risk_band_key]            INT           NOT NULL,
        [currency_code]            CHAR(3)       NOT NULL,
        [current_balance_orig]     DECIMAL(19,4) NOT NULL,
        [current_balance_clp]      DECIMAL(19,4) NOT NULL,
        [fx_rate_to_clp]           DECIMAL(14,6) NOT NULL,
        [accrued_interest_clp]     DECIMAL(19,4) NOT NULL,
        [contractual_rate]         DECIMAL(8,4)  NOT NULL,
        [regulatory_provision_clp] DECIMAL(19,4) NOT NULL,
        [collateral_value_clp]     DECIMAL(19,4) NOT NULL,
        [net_exposure_clp]         DECIMAL(19,4) NOT NULL,
        [is_hqla_eligible]         BIT           NOT NULL,
        [hqla_level]               VARCHAR(5)    NOT NULL,
        [nsfr_rsf_factor]          DECIMAL(5,4)  NOT NULL,
        [nsfr_weighted_rsf_clp]    DECIMAL(19,4) NOT NULL,
        CONSTRAINT [CK_staging_bound] CHECK ([snapshot_date_key] = @p_snapshot_date_key)
    ) ON [PRIMARY];

    -- 2. Cargar datos transformados en Staging
    INSERT INTO [stg].[fact_account_balance_staging] WITH (TABLOCK) (
        [snapshot_date_key], [contract_id], [partner_key], [risk_band_key],
        [currency_code], [current_balance_orig], [current_balance_clp],
        [fx_rate_to_clp], [accrued_interest_clp], [contractual_rate],
        [regulatory_provision_clp], [collateral_value_clp], [net_exposure_clp],
        [is_hqla_eligible], [hqla_level], [nsfr_rsf_factor], [nsfr_weighted_rsf_clp]
    )
    SELECT 
        @p_snapshot_date_key,
        c.[contract_id],
        p.[partner_key],
        rb.[risk_band_key],
        c.[currency_code],
        c.[current_balance],
        ROUND(c.[current_balance] * 980.00, 4),
        980.00,
        ROUND(c.[current_balance] * (c.[annual_nominal_rate] / 12.0), 4),
        c.[annual_nominal_rate],
        ROUND(c.[current_balance] * rb.[provision_rate], 4),
        0.0000,
        c.[current_balance],
        1,
        'L1',
        0.0500,
        ROUND(c.[current_balance] * 0.0500, 4)
    FROM [core].[contract_profile] c
    INNER JOIN [dwh].[dim_partner] p ON c.[partner_id] = p.[partner_id] AND p.[is_current] = 1
    CROSS JOIN [dwh].[dim_risk_band] rb
    WHERE rb.[cmf_rating_category] = 'A1';

    -- 3. Crear Clustered Columnstore Index sobre la tabla staging
    CREATE CLUSTERED COLUMNSTORE INDEX [CCI_fact_balance_staging]
    ON [stg].[fact_account_balance_staging];

    -- 4. Conmutación atómica de partición (Switch)
    BEGIN TRANSACTION;

    ALTER TABLE [stg].[fact_account_balance_staging] 
    SWITCH TO [dwh].[fact_account_balance_snapshot] PARTITION @v_partition_number;

    COMMIT TRANSACTION;

    DROP TABLE [stg].[fact_account_balance_staging];
    PRINT FORMATMESSAGE('Partición %d conmutada con éxito.', @v_partition_number);
END;
GO

-- -----------------------------------------------------------------------------
-- 4. PROCEDIMIENTO DE RECONCILIACIÓN NOCTURNA: LEGACY VS LEDGER INMUTABLE
-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE [stg].[usp_reconcile_legacy_vs_ledger]
    @p_reconciliation_date DATE
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @v_start_time DATETIME2(0) = SYSDATETIME();
    DECLARE @v_legacy_total DECIMAL(19,4) = 0.0000;
    DECLARE @v_ledger_total DECIMAL(19,4) = 0.0000;
    DECLARE @v_difference   DECIMAL(19,4) = 0.0000;
    DECLARE @v_batch_id     BIGINT;

    INSERT INTO [stg].[load_execution_log] (
        [pipeline_name], [target_layer], [source_entity], [target_entity],
        [extract_start_time], [status]
    )
    VALUES (
        'RECONCILIACION_NOCTURNA_LEDGER', 'CORE', 'dbo.tbl_prestamos_legacy', 'oltp.ledger_posting',
        @v_start_time, 'RUNNING'
    );
    SET @v_batch_id = SCOPE_IDENTITY();

    -- Saldo agregado sistema legado
    SELECT @v_legacy_total = ISNULL(SUM([saldo_capital]), 0.0000)
    FROM [dbo].[tbl_prestamos_legacy];

    -- Saldo acumulado ledger inmutable
    SELECT @v_ledger_total = ISNULL(SUM([functional_debit] - [functional_credit]), 0.0000)
    FROM [oltp].[ledger_posting] p
    INNER JOIN [oltp].[journal_entry] j ON p.[journal_entry_id] = j.[journal_entry_id]
    WHERE j.[posting_date] <= @p_reconciliation_date;

    SET @v_difference = ABS(@v_legacy_total - @v_ledger_total);

    IF @v_difference <= 0.0100
    BEGIN
        UPDATE [stg].[load_execution_log]
        SET [extract_end_time]    = SYSDATETIME(),
            [status]              = 'RECONCILED',
            [reconciliation_diff] = @v_difference,
            [records_loaded]      = (SELECT COUNT(*) FROM [oltp].[journal_entry] WHERE [posting_date] = @p_reconciliation_date)
        WHERE [batch_id] = @v_batch_id;

        PRINT 'CONCILIACIÓN EXITOSA: Legado y Ledger cuadran exactamente.';
    END
    ELSE
    BEGIN
        DECLARE @v_err NVARCHAR(300) = FORMATMESSAGE(
            'DESCUADRE CRÍTICO: Legado: %s | Ledger: %s | Dif: %s',
            CONVERT(VARCHAR(30), @v_legacy_total),
            CONVERT(VARCHAR(30), @v_ledger_total),
            CONVERT(VARCHAR(30), @v_difference)
        );

        UPDATE [stg].[load_execution_log]
        SET [extract_end_time]    = SYSDATETIME(),
            [status]              = 'FAILED',
            [reconciliation_diff] = @v_difference,
            [error_message]       = @v_err
        WHERE [batch_id] = @v_batch_id;

        RAISERROR(@v_err, 16, 1);
    END;
END;
GO
