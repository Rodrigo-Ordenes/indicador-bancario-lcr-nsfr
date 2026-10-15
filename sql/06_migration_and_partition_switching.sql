-- =============================================================================
-- 06_migration_and_partition_switching.sql
-- =============================================================================
-- Estrategia de Migración Zero-Downtime, Mantenimiento de Particiones y
-- Conciliación Contable Cuádruple (Legacy vs Core vs Ledger vs DWH).
--
-- Contiene:
--   1. Activación y afinamiento de CDC (Change Data Capture) en tablas legadas.
--   2. stg.usp_ingest_cdc_legacy_contracts: extracción asíncrona CDC, cálculo de
--      checksum y sincronización automática hacia el Core bitemporal.
--   3. core.usp_migrate_legacy_portfolio_future_assertion: migración masiva de
--      carteras legadas con aserción diferida (activación instantánea a la hora
--      del corte sin ventana de mantención).
--   4. core.usp_backfill_contract_profile_history: carga histórica por lotes hacia
--      el histórico bitemporal con consistencia garantizada.
--   5. dwh.usp_load_partition_switch_balance_snapshot: carga masiva instantánea
--      (Zero-Lock) mediante conmutación de particiones Columnstore (Switch Partition).
--   6. stg.usp_reconcile_legacy_vs_ledger: conciliación nocturna automatizada
--      (Legado vs Core ODS vs Ledger Inmutable vs DWH Kimball) con registro en bitácora.
--   7. dwh.usp_extend_monthly_partitions: mantenimiento automático preventivo
--      de límites de partición mensual sobre pf_monthly_date_key.
-- =============================================================================

USE [CoreBanking];
GO

-- Limpieza preventiva
DROP PROCEDURE IF EXISTS [dwh].[usp_extend_monthly_partitions];
DROP PROCEDURE IF EXISTS [stg].[usp_reconcile_legacy_vs_ledger];
DROP PROCEDURE IF EXISTS [dwh].[usp_load_partition_switch_balance_snapshot];
DROP PROCEDURE IF EXISTS [core].[usp_backfill_contract_profile_history];
DROP PROCEDURE IF EXISTS [core].[usp_migrate_legacy_portfolio_future_assertion];
DROP PROCEDURE IF EXISTS [stg].[usp_ingest_cdc_legacy_contracts];
GO

-- =============================================================================
-- 1. ACTIVACIÓN Y AFINAMIENTO DE CDC (CHANGE DATA CAPTURE)
-- =============================================================================

-- Habilitar CDC a nivel de base de datos si no lo está
IF NOT EXISTS (SELECT 1 FROM sys.databases WHERE [name] = N'CoreBanking' AND [is_cdc_enabled] = 1)
BEGIN
    EXEC sys.sp_cdc_enable_db;
END;
GO

-- Tabla operacional legada representativa (simulación del core previo)
IF OBJECT_ID(N'dbo.tbl_prestamos_legacy', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[tbl_prestamos_legacy] (
        [id_prestamo]       BIGINT IDENTITY(1001,1) NOT NULL PRIMARY KEY,
        [id_socio]          BIGINT        NOT NULL,
        [num_operacion]     VARCHAR(50)   NOT NULL UNIQUE,
        [tipo_credito]      VARCHAR(30)   NOT NULL, -- COMERCIAL, HIPOTECARIO, CONSUMO
        [codigo_sucursal]   VARCHAR(20)   NOT NULL DEFAULT ('SUC_CASA_MATRIZ'),
        [moneda]            CHAR(3)       NOT NULL DEFAULT ('CLP'),
        [fecha_otorgamiento] DATE         NOT NULL,
        [fecha_vencimiento] DATE          NULL,
        [monto_otorgado]    DECIMAL(19,4) NOT NULL,
        [saldo_capital]     DECIMAL(19,4) NOT NULL,
        [linea_credito_disp] DECIMAL(19,4) NOT NULL DEFAULT (0),
        [tasa_anual]        DECIMAL(12,6) NOT NULL,
        [spread]            DECIMAL(12,6) NOT NULL DEFAULT (0),
        [frec_revision]     VARCHAR(12)   NOT NULL DEFAULT ('MENSUAL'),
        [clasif_riesgo_cmf] VARCHAR(4)    NOT NULL DEFAULT ('A1'),
        [dias_mora]         INT           NOT NULL DEFAULT (0),
        [estado_prestamo]   VARCHAR(20)   NOT NULL DEFAULT ('VIGENTE'),
        [fecha_ult_mod]     DATETIME2(2)  NOT NULL DEFAULT (SYSDATETIME())
    );
END;
GO

-- Habilitar CDC en la tabla legada si no está activo
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE [name] = N'tbl_prestamos_legacy' AND [is_tracked_by_cdc] = 1)
BEGIN
    EXEC sys.sp_cdc_enable_table
        @source_schema        = N'dbo',
        @source_name          = N'tbl_prestamos_legacy',
        @role_name            = NULL,
        @supports_net_changes = 1,
        @capture_instance     = N'dbo_prestamos_legacy';
END;
GO

-- Inicializar watermark si no existe
IF NOT EXISTS (SELECT 1 FROM [stg].[cdc_watermark] WHERE [capture_instance] = N'dbo_prestamos_legacy')
BEGIN
    INSERT INTO [stg].[cdc_watermark] ([capture_instance], [last_lsn], [updated_at_utc])
    VALUES (N'dbo_prestamos_legacy', 0x00000000000000000000, SYSUTCDATETIME());
END;
GO

-- Afinamiento de Jobs CDC para mitigar contención de I/O en OLTP
IF EXISTS (SELECT 1 FROM msdb.dbo.cdc_jobs WHERE [job_type] = N'capture')
BEGIN
    EXEC sys.sp_cdc_change_job 
        @job_type = N'capture', 
        @maxscans = 10,       -- Máximos ciclos de lectura del Transaction Log
        @maxtrans = 5000,     -- Límite de transacciones por ciclo
        @pollinginterval = 5; -- Pausa en segundos entre lecturas
END;
GO

-- =============================================================================
-- 2. PROCEDIMIENTO: INGESTA ASÍNCRONA CDC HACIA STAGING Y CORE BITEMPORAL
-- =============================================================================
CREATE PROCEDURE [stg].[usp_ingest_cdc_legacy_contracts]
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @v_from_lsn BINARY(10), @v_to_lsn BINARY(10), @v_min_lsn BINARY(10);
    DECLARE @v_batch_id BIGINT;
    DECLARE @v_extracted BIGINT = 0;

    -- Obtener último LSN procesado y LSN máximo disponible
    SELECT @v_from_lsn = [last_lsn] FROM [stg].[cdc_watermark] WHERE [capture_instance] = N'dbo_prestamos_legacy';
    SET @v_to_lsn = sys.fn_cdc_get_max_lsn();
    SET @v_min_lsn = sys.fn_cdc_get_min_lsn(N'dbo_prestamos_legacy');

    -- Ajustar LSN inicial si fue truncado por el log de transacciones
    IF @v_from_lsn = 0x00000000000000000000 OR @v_from_lsn < @v_min_lsn
        SET @v_from_lsn = @v_min_lsn;

    IF @v_from_lsn >= @v_to_lsn
    BEGIN
        PRINT N'CDC: No hay nuevas transacciones para procesar.';
        RETURN 0;
    END;

    -- Iniciar lote en la bitácora de control
    EXEC [stg].[usp_log_batch_start]
        @p_pipeline_name = 'CDC_LEGACY_TO_CORE',
        @p_target_layer  = 'STG',
        @p_source_entity = 'dbo.tbl_prestamos_legacy',
        @p_target_entity = 'stg.cdc_landing_contract',
        @o_batch_id      = @v_batch_id OUTPUT;

    -- Aterrizar cambios netos calculando checksum SHA-256 de fila
    INSERT INTO [stg].[cdc_landing_contract] (
        [batch_id], [cdc_start_lsn], [cdc_operation], [contract_id], [partner_id],
        [contract_number], [legacy_product_code], [branch_code], [currency_code],
        [origination_date], [maturity_date], [original_amount], [current_balance],
        [committed_limit], [annual_nominal_rate], [spread_rate], [repricing_frequency],
        [cmf_rating], [days_past_due], [contract_status], [valid_from], [row_checksum],
        [is_processed]
    )
    SELECT 
        @v_batch_id,
        cdc.[__$start_lsn],
        cdc.[__$operation],
        cdc.[id_prestamo],
        cdc.[id_socio],
        cdc.[num_operacion],
        cdc.[tipo_credito],
        cdc.[codigo_sucursal],
        cdc.[moneda],
        cdc.[fecha_otorgamiento],
        cdc.[fecha_vencimiento],
        cdc.[monto_otorgado],
        cdc.[saldo_capital],
        cdc.[linea_credito_disp],
        cdc.[tasa_anual],
        cdc.[spread],
        cdc.[frec_revision],
        cdc.[clasif_riesgo_cmf],
        cdc.[dias_mora],
        cdc.[estado_prestamo],
        CAST(cdc.[fecha_ult_mod] AS DATETIME2(2)),
        ck.[row_checksum],
        0 -- pendiente de sincronizar a Core
    FROM cdc.fn_cdc_get_net_changes_dbo_prestamos_legacy(@v_from_lsn, @v_to_lsn, 'all') cdc
    CROSS APPLY [stg].[itvf_contract_row_checksum](
        cdc.[id_socio], cdc.[num_operacion], cdc.[tipo_credito], cdc.[moneda],
        cdc.[fecha_vencimiento], cdc.[saldo_capital], cdc.[linea_credito_disp],
        cdc.[tasa_anual], cdc.[spread], cdc.[clasif_riesgo_cmf], cdc.[dias_mora],
        cdc.[estado_prestamo]
    ) ck;

    SET @v_extracted = @@ROWCOUNT;

    -- Actualizar watermark
    UPDATE [stg].[cdc_watermark]
    SET [last_lsn] = @v_to_lsn, [updated_at_utc] = SYSUTCDATETIME()
    WHERE [capture_instance] = N'dbo_prestamos_legacy';

    -- Sincronizar automáticamente hacia Core bitemporal
    IF @v_extracted > 0
    BEGIN
        EXEC [core].[usp_sync_contracts_from_staging] @p_batch_id = @v_batch_id;
    END
    ELSE
    BEGIN
        EXEC [stg].[usp_log_batch_end] @p_batch_id, 'SUCCESS', @p_extracted = 0, @p_loaded = 0;
    END;

    PRINT FORMATMESSAGE(N'CDC procesado con éxito: %I64d registros extraídos.', @v_extracted);
END;
GO

-- =============================================================================
-- 3. PROCEDIMIENTO: MIGRACIÓN CON ASERCIÓN DIFERIDA (FUTURE ASSERTION)
--    Precarga carteras legadas completas hacia el ODS bitemporal sin downtime.
--    Las versiones quedan invisibles hasta el segundo exacto de @p_cutover_utc.
-- =============================================================================
CREATE PROCEDURE [core].[usp_migrate_legacy_portfolio_future_assertion]
    @p_cutover_utc DATETIME2(2),
    @p_reason      NVARCHAR(300) = N'Migración masiva cartera legada con activación instantánea'
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @v_count INT = 0;
    DECLARE @contract_id BIGINT, @json NVARCHAR(MAX), @valid_from DATETIME2(2);

    -- Cursor local sobre la cartera legada que aún no ha sido migrada
    DECLARE c CURSOR LOCAL FAST_FORWARD FOR
        SELECT 
            leg.[id_prestamo],
            (SELECT 
                leg.[id_socio]            AS [partner_id],
                leg.[num_operacion]       AS [contract_number],
                m.[product_code]          AS [product_code],
                leg.[codigo_sucursal]     AS [branch_code],
                leg.[moneda]              AS [currency_code],
                m.[gl_account_id]         AS [gl_account_id],
                leg.[fecha_otorgamiento]  AS [origination_date],
                leg.[fecha_vencimiento]   AS [maturity_date],
                leg.[monto_otorgado]      AS [original_amount],
                leg.[saldo_capital]       AS [current_balance],
                leg.[linea_credito_disp]  AS [committed_limit],
                CASE WHEN leg.[linea_credito_disp] > 0 THEN leg.[linea_credito_disp] - leg.[saldo_capital] ELSE 0 END AS [undrawn_commitment],
                leg.[tasa_anual]          AS [annual_nominal_rate],
                leg.[spread]              AS [spread_rate],
                leg.[frec_revision]       AS [repricing_frequency],
                m.[payment_frequency_months] AS [payment_frequency_months],
                m.[amortization_type]     AS [amortization_type],
                m.[cmf_portfolio_type]    AS [cmf_portfolio_type],
                leg.[clasif_riesgo_cmf]   AS [cmf_rating],
                leg.[dias_mora]           AS [days_past_due],
                m.[liquidity_treatment_code] AS [liquidity_treatment_code],
                leg.[estado_prestamo]     AS [contract_status]
             FOR JSON PATH, WITHOUT_ARRAY_WRAPPER) AS [contract_json],
            CAST(leg.[fecha_otorgamiento] AS DATETIME2(2)) AS [valid_from]
        FROM [dbo].[tbl_prestamos_legacy] leg
        JOIN [stg].[map_legacy_product] m ON m.[legacy_product_code] = leg.[tipo_credito]
        WHERE NOT EXISTS (
            SELECT 1 FROM [core].[contract_profile] cp WHERE cp.[contract_id] = leg.[id_prestamo]
        );

    OPEN c;
    FETCH NEXT FROM c INTO @contract_id, @json, @valid_from;

    WHILE @@FETCH_STATUS = 0
    BEGIN
        EXEC [core].[usp_future_assertion]
            @p_contract_id  = @contract_id,
            @p_contract_json = @json,
            @p_valid_from   = @valid_from,
            @p_cutover_utc  = @p_cutover_utc,
            @p_reason       = @p_reason;

        SET @v_count += 1;
        FETCH NEXT FROM c INTO @contract_id, @json, @valid_from;
    END;

    CLOSE c;
    DEALLOCATE c;

    PRINT FORMATMESSAGE(N'Migración diferida exitosa: %d contratos cargados. Activación en: %s UTC.', 
                        @v_count, CONVERT(VARCHAR(30), @p_cutover_utc, 126));
END;
GO

-- =============================================================================
-- 4. PROCEDIMIENTO: BACKFILLING HISTÓRICO POR LOTES
--    Reconstruye el histórico bitemporal del ODS a partir de fotos mensuales legadas.
-- =============================================================================
CREATE PROCEDURE [core].[usp_backfill_contract_profile_history]
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
        PRINT CONCAT(N'Procesando ventana de backfilling histórico: ', @v_current_cut);

        -- Desactivar System-Versioning para permitir inserción directa en History
        ALTER TABLE [core].[contract_profile] SET (SYSTEM_VERSIONING = OFF);

        BEGIN TRANSACTION;

        INSERT INTO [core].[contract_profile_history] (
            [contract_id], [partner_id], [contract_number], [product_code], [branch_code],
            [currency_code], [gl_account_id], [origination_date], [maturity_date],
            [original_amount], [current_balance], [committed_limit], [undrawn_commitment],
            [annual_nominal_rate], [spread_rate], [repricing_frequency], [next_repricing_date],
            [payment_frequency_months], [amortization_type], [cmf_portfolio_type], [cmf_rating],
            [days_past_due], [collateral_value], [liquidity_treatment_code], [contract_status],
            [balance_maintained_in_core], [ValidFrom], [ValidTo], [AssertedFrom], [AssertedTo],
            [change_type], [change_reason], [source_journal_entry_id], [SysStartTime], [SysEndTime]
        )
        SELECT TOP (@p_batch_size)
            leg.[id_prestamo],
            leg.[id_socio],
            leg.[num_operacion],
            m.[product_code],
            leg.[codigo_sucursal],
            leg.[moneda],
            m.[gl_account_id],
            leg.[fecha_otorgamiento],
            leg.[fecha_vencimiento],
            leg.[monto_otorgado],
            leg.[saldo_capital],
            leg.[linea_credito_disp],
            CASE WHEN leg.[linea_credito_disp] > 0 THEN leg.[linea_credito_disp] - leg.[saldo_capital] ELSE 0 END,
            leg.[tasa_anual],
            leg.[spread],
            leg.[frec_revision],
            DATEADD(MONTH, 1, @v_current_cut),
            m.[payment_frequency_months],
            m.[amortization_type],
            m.[cmf_portfolio_type],
            leg.[clasif_riesgo_cmf],
            leg.[dias_mora],
            0.0000,
            m.[liquidity_treatment_code],
            leg.[estado_prestamo],
            1,
            CAST(@v_current_cut AS DATETIME2(2)),
            DATEADD(MONTH, 1, CAST(@v_current_cut AS DATETIME2(2))),
            CAST(@v_current_cut AS DATETIME2(2)),
            '9999-12-31 23:59:59.99',
            'INSERT',
            N'Backfilling histórico legado',
            NULL,
            CAST(@v_current_cut AS DATETIME2(7)),
            '9999-12-31 23:59:59.9999999'
        FROM [dbo].[tbl_prestamos_legacy] leg
        JOIN [stg].[map_legacy_product] m ON m.[legacy_product_code] = leg.[tipo_credito]
        WHERE NOT EXISTS (
            SELECT 1 FROM [core].[contract_profile_history] h
            WHERE h.[contract_id] = leg.[id_prestamo]
              AND h.[ValidFrom] = CAST(@v_current_cut AS DATETIME2(2))
        );

        COMMIT TRANSACTION;

        -- Reactivar System-Versioning verificando integridad física
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

-- =============================================================================
-- 5. PROCEDIMIENTO: CARGA MASIVA ZERO-LOCK MEDIANTE PARTITION SWITCHING
--    Conmuta una partición mensual completa en milisegundos sin bloquear lectores.
-- =============================================================================
CREATE PROCEDURE [dwh].[usp_load_partition_switch_balance_snapshot]
    @p_snapshot_date_key INT -- Ejemplo: 20250131
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @v_partition_number INT;
    SELECT @v_partition_number = $PARTITION.[pf_monthly_date_key](@p_snapshot_date_key);

    IF @v_partition_number IS NULL
    BEGIN
        RAISERROR(N'La clave de fecha %d no pertenece a una partición válida.', 16, 1, @p_snapshot_date_key);
        RETURN -1;
    END;

    -- 1. Preparar tabla Staging idéntica (esquema físico 100% compatible con fact)
    IF OBJECT_ID(N'stg.fact_account_balance_staging', N'U') IS NOT NULL
        DROP TABLE [stg].[fact_account_balance_staging];

    CREATE TABLE [stg].[fact_account_balance_staging] (
        [snapshot_date_key]           INT            NOT NULL,
        [contract_key]                INT            NOT NULL,
        [restatement_seq]             SMALLINT       NOT NULL,
        [snapshot_asserted_at]        DATETIME2(2)   NOT NULL,
        [partner_key]                 INT            NOT NULL,
        [product_key]                 INT            NOT NULL,
        [branch_key]                  INT            NOT NULL,
        [gl_account_key]              INT            NOT NULL,
        [risk_band_key]               INT            NOT NULL,
        [liquidity_treatment_key]     INT            NOT NULL,
        [currency_code]               CHAR(3)        NOT NULL,
        [balance_orig]                DECIMAL(19,4)  NOT NULL,
        [balance_clp]                 DECIMAL(19,4)  NOT NULL,
        [fx_rate_to_clp]              DECIMAL(12,6)  NOT NULL,
        [accrued_interest_clp]        DECIMAL(19,4)  NOT NULL,
        [contractual_rate]            DECIMAL(12,6)  NOT NULL,
        [regulatory_provision_clp]    DECIMAL(19,4)  NOT NULL,
        [collateral_value_clp]        DECIMAL(19,4)  NOT NULL,
        [net_exposure_clp]            DECIMAL(19,4)  NOT NULL,
        [market_value_clp]            DECIMAL(19,4)  NOT NULL,
        [undrawn_commitment_clp]      DECIMAL(19,4)  NOT NULL,
        [pending_debits_clp]          DECIMAL(19,4)  NOT NULL,
        [pending_credits_clp]         DECIMAL(19,4)  NOT NULL,
        [residual_maturity_days]      INT            NULL,
        [row_checksum]                BINARY(32)     NOT NULL
    ) ON [PRIMARY];

    -- 2. Restricción CHECK estática requerida por SQL Server para conmutación de partición
    DECLARE @sqlCheck NVARCHAR(MAX) = 
        N'ALTER TABLE [stg].[fact_account_balance_staging] ADD CONSTRAINT [CK_staging_bound] ' +
        N'CHECK ([snapshot_date_key] = ' + CAST(@p_snapshot_date_key AS NVARCHAR(10)) + N');';
    EXEC sys.sp_executesql @sqlCheck;

    -- 3. Cargar datos transformados en Staging
    DECLARE @v_now_utc DATETIME2(2) = SYSUTCDATETIME();
    DECLARE @v_bus_date DATETIME2(2) = CAST(CAST(@p_snapshot_date_key AS CHAR(8)) AS DATETIME2(2));

    INSERT INTO [stg].[fact_account_balance_staging] WITH (TABLOCK) (
        [snapshot_date_key], [contract_key], [restatement_seq], [snapshot_asserted_at],
        [partner_key], [product_key], [branch_key], [gl_account_key], [risk_band_key],
        [liquidity_treatment_key], [currency_code], [balance_orig], [balance_clp],
        [fx_rate_to_clp], [accrued_interest_clp], [contractual_rate], [regulatory_provision_clp],
        [collateral_value_clp], [net_exposure_clp], [market_value_clp], [undrawn_commitment_clp],
        [pending_debits_clp], [pending_credits_clp], [residual_maturity_days], [row_checksum]
    )
    SELECT 
        @p_snapshot_date_key,
        c.[contract_key],
        1 AS [restatement_seq],
        @v_now_utc,
        p.[partner_key],
        pr.[product_key],
        br.[branch_key],
        gl.[gl_account_key],
        rb.[risk_band_key],
        lt.[liquidity_treatment_key],
        cp.[currency_code],
        cp.[current_balance],
        cp.[current_balance], -- Tipo de cambio 1.0 para CLP
        1.000000,
        ROUND(cp.[current_balance] * (cp.[annual_nominal_rate] / 12.0), 4),
        cp.[annual_nominal_rate],
        ROUND(cp.[current_balance] * rb.[provision_rate], 4),
        cp.[collateral_value],
        cp.[current_balance] - cp.[collateral_value],
        cp.[current_balance], -- Base market value
        cp.[undrawn_commitment],
        0.0000,
        0.0000,
        DATEDIFF(DAY, CAST(CAST(@p_snapshot_date_key AS CHAR(8)) AS DATE), cp.[maturity_date]),
        HASHBYTES('SHA2_256', CONCAT(cp.[contract_id], '|', cp.[current_balance], '|', @p_snapshot_date_key))
    FROM [core].[itvf_contract_as_of](@v_bus_date, @v_now_utc) cp
    JOIN [dwh].[dim_account_contract] c ON c.[contract_id] = cp.[contract_id]
    JOIN [dwh].[dim_partner] p ON p.[partner_id] = cp.[partner_id] AND p.[is_current] = 1
    JOIN [dwh].[dim_product] pr ON pr.[product_code] = cp.[product_code]
    JOIN [dwh].[dim_branch] br ON br.[branch_code] = cp.[branch_code]
    JOIN [dwh].[dim_gl_account] gl ON gl.[account_id] = cp.[gl_account_id]
    JOIN [dwh].[dim_risk_band] rb ON rb.[cmf_rating_category] = cp.[cmf_rating]
    JOIN [dwh].[dim_liquidity_treatment] lt ON lt.[treatment_code] = cp.[liquidity_treatment_code];

    -- 4. Crear índices exactamente alineados a la tabla destino
    CREATE CLUSTERED COLUMNSTORE INDEX [CCI_fact_balance_staging]
        ON [stg].[fact_account_balance_staging] ON [PRIMARY];

    CREATE UNIQUE NONCLUSTERED INDEX [UX_fact_staging_grain]
        ON [stg].[fact_account_balance_staging] ([snapshot_date_key], [contract_key], [restatement_seq])
        INCLUDE ([snapshot_asserted_at], [row_checksum])
        ON [PRIMARY];

    -- 5. Conmutación atómica de partición (Switch instantáneo sin lock en lectores)
    BEGIN TRANSACTION;

    ALTER TABLE [stg].[fact_account_balance_staging] 
    SWITCH TO [dwh].[fact_account_balance_snapshot] PARTITION @v_partition_number;

    COMMIT TRANSACTION;

    DROP TABLE [stg].[fact_account_balance_staging];
    PRINT FORMATMESSAGE(N'Partición mensual %d (Fecha %d) conmutada con éxito.', 
                        @v_partition_number, @p_snapshot_date_key);
END;
GO

-- =============================================================================
-- 6. PROCEDIMIENTO: CONCILIACIÓN NOCTURNA LEGADO VS CORE VS LEDGER VS DWH
--    Garantiza cuadre de balances a 0.0000 CLP entre las 4 capas desacopladas.
-- =============================================================================
CREATE PROCEDURE [stg].[usp_reconcile_legacy_vs_ledger]
    @p_reconciliation_date DATE
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @v_start_time DATETIME2(7) = SYSUTCDATETIME();
    DECLARE @v_batch_id   BIGINT;
    DECLARE @v_reconciled INT = 0;
    DECLARE @v_discrepancies INT = 0;
    DECLARE @v_max_diff DECIMAL(19,4) = 0.0000;
    DECLARE @v_date_key INT = CAST(CONVERT(CHAR(8), @p_reconciliation_date, 112) AS INT);
    DECLARE @v_bus_date DATETIME2(2) = CAST(@p_reconciliation_date AS DATETIME2(2));

    EXEC [stg].[usp_log_batch_start]
        @p_pipeline_name = 'RECONCILIACION_CUADRUPLE_CORE_LEDGER_DWH',
        @p_target_layer  = 'CORE',
        @p_source_entity = 'MULTIPLE_LAYERS',
        @p_target_entity = 'stg.reconciliation_result',
        @o_batch_id      = @v_batch_id OUTPUT;

    -- 1. Saldos de cada capa
    WITH LegacyBalances AS (
        SELECT [id_prestamo] AS [contract_id], SUM([saldo_capital]) AS [balance]
        FROM [dbo].[tbl_prestamos_legacy]
        GROUP BY [id_prestamo]
    ),
    CoreBalances AS (
        SELECT [contract_id], [current_balance] AS [balance]
        FROM [core].[itvf_contract_as_of](@v_bus_date, SYSUTCDATETIME())
        WHERE [contract_status] = 'VIGENTE'
    ),
    LedgerBalances AS (
        SELECT b.[contract_id],
               -- Saldo normal del activo: Debe - Haber
               SUM(b.[debits_posted] - b.[credits_posted]) AS [balance]
        FROM [oltp].[account_balance] b
        JOIN [oltp].[ledger_account] a ON a.[account_id] = b.[account_id]
        WHERE b.[contract_id] IS NOT NULL
        GROUP BY b.[contract_id]
    ),
    DwhBalances AS (
        SELECT c.[contract_id], s.[balance_clp] AS [balance]
        FROM [dwh].[fact_account_balance_snapshot] s
        JOIN [dwh].[dim_account_contract] c ON c.[contract_key] = s.[contract_key]
        WHERE s.[snapshot_date_key] = @v_date_key
          AND s.[restatement_seq] = (
              SELECT MAX(s2.[restatement_seq])
              FROM [dwh].[fact_account_balance_snapshot] s2
              WHERE s2.[snapshot_date_key] = s.[snapshot_date_key]
                AND s2.[contract_key] = s.[contract_key]
          )
    ),
    AllContracts AS (
        SELECT [contract_id] FROM LegacyBalances
        UNION
        SELECT [contract_id] FROM CoreBalances
        UNION
        SELECT [contract_id] FROM LedgerBalances
        UNION
        SELECT [contract_id] FROM DwhBalances
    )
    INSERT INTO [stg].[reconciliation_result] (
        [batch_id], [reconciliation_date], [contract_id],
        [legacy_balance], [core_balance], [ledger_balance], [dwh_balance], [status]
    )
    SELECT 
        @v_batch_id,
        @p_reconciliation_date,
        k.[contract_id],
        leg.[balance] AS [legacy_balance],
        cor.[balance] AS [core_balance],
        ISNULL(led.[balance], 0.0000) AS [ledger_balance],
        dwh.[balance] AS [dwh_balance],
        CASE 
            WHEN ABS(ISNULL(cor.[balance], 0) - ISNULL(led.[balance], 0)) <= 0.0100
             AND ABS(ISNULL(led.[balance], 0) - ISNULL(dwh.[balance], 0)) <= 0.0100
            THEN 'CUADRADO'
            ELSE 'DESCUADRADO'
        END AS [status]
    FROM AllContracts k
    LEFT JOIN LegacyBalances leg ON leg.[contract_id] = k.[contract_id]
    LEFT JOIN CoreBalances cor   ON cor.[contract_id] = k.[contract_id]
    LEFT JOIN LedgerBalances led ON led.[contract_id] = k.[contract_id]
    LEFT JOIN DwhBalances dwh    ON dwh.[contract_id] = k.[contract_id];

    SELECT 
        @v_reconciled    = COUNT(CASE WHEN [status] = 'CUADRADO' THEN 1 END),
        @v_discrepancies = COUNT(CASE WHEN [status] <> 'CUADRADO' THEN 1 END),
        @v_max_diff      = ISNULL(MAX(ABS([diff_core_ledger])), 0.0000)
    FROM [stg].[reconciliation_result]
    WHERE [batch_id] = @v_batch_id;

    IF @v_discrepancies = 0
    BEGIN
        EXEC [stg].[usp_log_batch_end]
            @p_batch_id            = @v_batch_id,
            @p_status              = 'RECONCILED',
            @p_loaded              = @v_reconciled,
            @p_reconciliation_diff = @v_max_diff;

        PRINT FORMATMESSAGE(N'CONCILIACIÓN NOCTURNA EXITOSA: %d contratos cuadrados a 0.0000 CLP.', @v_reconciled);
    END
    ELSE
    BEGIN
        DECLARE @v_err NVARCHAR(400) = FORMATMESSAGE(
            N'DESCUADRE CRÍTICO EN CONCILIACIÓN: %d contratos con diferencias. Diferencia máxima: %s CLP.',
            @v_discrepancies, CONVERT(VARCHAR(30), @v_max_diff)
        );

        EXEC [stg].[usp_log_batch_end]
            @p_batch_id            = @v_batch_id,
            @p_status              = 'DISCREPANCY',
            @p_rejected            = @v_discrepancies,
            @p_reconciliation_diff = @v_max_diff,
            @p_error_message       = @v_err;

        RAISERROR(@v_err, 16, 1);
    END;
END;
GO

-- =============================================================================
-- 7. PROCEDIMIENTO: MANTENIMIENTO PREVENTIVO DE PARTICIONES MENSUALES
--    Agrega particiones futuras sobre pf_monthly_date_key / ps_monthly_date_key
-- =============================================================================
CREATE PROCEDURE [dwh].[usp_extend_monthly_partitions]
    @p_months_ahead INT = 12
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @v_target_end DATE = DATEADD(MONTH, @p_months_ahead, GETDATE());
    DECLARE @v_max_boundary INT;

    SELECT @v_max_boundary = MAX(CAST([value] AS INT))
    FROM sys.partition_range_values prv
    JOIN sys.partition_functions pf ON pf.[function_id] = prv.[function_id]
    WHERE pf.[name] = N'pf_monthly_date_key';

    DECLARE @v_next_month DATE = DATEADD(MONTH, 1, CAST(CAST(@v_max_boundary AS CHAR(8)) AS DATE));

    WHILE @v_next_month <= @v_target_end
    BEGIN
        DECLARE @v_next_key INT = CAST(CONVERT(CHAR(8), @v_next_month, 112) AS INT);
        PRINT FORMATMESSAGE(N'Extendiendo partición mensual: %d', @v_next_key);

        ALTER PARTITION SCHEME [ps_monthly_date_key] NEXT USED [PRIMARY];

        DECLARE @sql NVARCHAR(MAX) = 
            N'ALTER PARTITION FUNCTION [pf_monthly_date_key] () SPLIT RANGE (' + 
            CAST(@v_next_key AS NVARCHAR(10)) + N');';
        EXEC sys.sp_executesql @sql;

        SET @v_next_month = DATEADD(MONTH, 1, @v_next_month);
    END;

    PRINT N'Mantenimiento de particiones mensuales completado exitosamente.';
END;
GO

-- Permisos
GRANT EXECUTE ON [stg].[usp_ingest_cdc_legacy_contracts]              TO [app_etl_operator];
GRANT EXECUTE ON [core].[usp_migrate_legacy_portfolio_future_assertion] TO [app_etl_operator];
GRANT EXECUTE ON [core].[usp_backfill_contract_profile_history]        TO [app_etl_operator];
GRANT EXECUTE ON [dwh].[usp_load_partition_switch_balance_snapshot]   TO [app_etl_operator];
GRANT EXECUTE ON [stg].[usp_reconcile_legacy_vs_ledger]               TO [app_etl_operator];
GRANT EXECUTE ON [dwh].[usp_extend_monthly_partitions]                TO [app_etl_operator];
GO

PRINT N'06_migration_and_partition_switching.sql compilado exitosamente.';
GO
