-- =============================================================================
-- 06_migration_and_partition_switching.sql
-- =============================================================================
-- Migración sin ventana de mantención, publicación del DWH por conmutación de
-- particiones y conciliación de cuatro vías (legado, core, ledger y DWH).
--
-- Orden de una migración (Caso Real 4):
--   1. core.usp_backfill_legacy_month_ends      : historia mensual del legado
--      (aserciones históricas del sistema anterior), una sola vez.
--   2. core.usp_migrate_legacy_portfolio_future_assertion : carga en horario hábil
--      del estado vigente con aserción diferida al instante del corte.
--   3. stg.usp_ingest_cdc_legacy_contracts      : CDC continuo del legado hasta el
--      corte (los cambios del día se aplican como updates bitemporales).
--   4. Al cruzar el corte, las versiones migradas quedan vigentes sin ningún job.
--   5. stg.usp_reconcile_four_way              : diferencia exigida 0,0000 por contrato.
--
-- Publicación diaria del DWH:
--   dwh.usp_publish_snapshot_partition_switch : arma el mes completo en una tabla
--   de carga alineada a la misma función de partición y la conmuta en una sola
--   transacción de metadatos (SWITCH OUT + SWITCH IN), sin bloquear lectores más
--   allá de un lock de esquema de milisegundos (WAIT_AT_LOW_PRIORITY).
--
-- Outbox: oltp.transactional_outbox se publica con CDC hacia stg.event_inbox
-- (stg.usp_relay_outbox_to_inbox); la clave única del inbox da effectively-once.
-- =============================================================================

USE [CoreBanking];
GO

DROP PROCEDURE IF EXISTS [dwh].[usp_extend_monthly_partitions];
DROP PROCEDURE IF EXISTS [stg].[usp_reconcile_four_way];
DROP PROCEDURE IF EXISTS [dwh].[usp_publish_snapshot_partition_switch];
DROP PROCEDURE IF EXISTS [core].[usp_migrate_legacy_portfolio_future_assertion];
DROP PROCEDURE IF EXISTS [core].[usp_backfill_legacy_month_ends];
DROP PROCEDURE IF EXISTS [stg].[usp_relay_outbox_to_inbox];
DROP PROCEDURE IF EXISTS [stg].[usp_ingest_cdc_legacy_contracts];
-- Objetos de versiones anteriores de este script
DROP PROCEDURE IF EXISTS [stg].[usp_reconcile_legacy_vs_ledger];
DROP PROCEDURE IF EXISTS [dwh].[usp_load_partition_switch_balance_snapshot];
DROP PROCEDURE IF EXISTS [core].[usp_backfill_contract_profile_history];
DROP TABLE IF EXISTS [stg].[fact_account_balance_staging];
GO

-- =============================================================================
-- 1. TABLAS LEGADAS (en producción ya existen; aquí se crean sólo si faltan)
-- =============================================================================
IF OBJECT_ID(N'dbo.tbl_prestamos_legacy', N'U') IS NULL
    CREATE TABLE [dbo].[tbl_prestamos_legacy] (
        [id_prestamo]         BIGINT        NOT NULL PRIMARY KEY,
        [id_socio]            BIGINT        NOT NULL,
        [num_operacion]       VARCHAR(50)   NOT NULL UNIQUE,
        [tipo_credito]        VARCHAR(30)   NOT NULL,          -- clave de stg.map_legacy_product
        [codigo_sucursal]     VARCHAR(20)   NOT NULL DEFAULT ('CL-SCL-01'),
        [moneda]              CHAR(3)       NOT NULL DEFAULT ('CLP'),
        [fecha_otorgamiento]  DATE          NOT NULL,
        [fecha_vencimiento]   DATE          NULL,
        [monto_otorgado]      DECIMAL(19,4) NOT NULL,
        [saldo_capital]       DECIMAL(19,4) NOT NULL,
        [linea_comprometida]  DECIMAL(19,4) NOT NULL DEFAULT (0),
        [tasa_anual]          DECIMAL(12,6) NOT NULL,
        [spread]              DECIMAL(12,6) NOT NULL DEFAULT (0),
        [frec_revision]       VARCHAR(12)   NOT NULL DEFAULT ('FIJO'),
        [clasif_riesgo_cmf]   VARCHAR(4)    NOT NULL DEFAULT ('A1'),
        [dias_mora]           INT           NOT NULL DEFAULT (0),
        [estado_prestamo]     VARCHAR(20)   NOT NULL DEFAULT ('VIGENTE'),
        [fecha_ult_mod]       DATETIME2(2)  NOT NULL DEFAULT (SYSUTCDATETIME())
    );
GO

-- Cierres mensuales históricos del legado (fuente del backfill).
IF OBJECT_ID(N'dbo.tbl_prestamos_legacy_cierre_mensual', N'U') IS NULL
    CREATE TABLE [dbo].[tbl_prestamos_legacy_cierre_mensual] (
        [fecha_cierre]       DATE          NOT NULL,
        [id_prestamo]        BIGINT        NOT NULL,
        [saldo_capital]      DECIMAL(19,4) NOT NULL,
        [tasa_anual]         DECIMAL(12,6) NOT NULL,
        [clasif_riesgo_cmf]  VARCHAR(4)    NOT NULL,
        [dias_mora]          INT           NOT NULL,
        [estado_prestamo]    VARCHAR(20)   NOT NULL,
        PRIMARY KEY ([id_prestamo], [fecha_cierre])
    );
GO

-- =============================================================================
-- 2. CDC: LEGADO Y OUTBOX
-- =============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.databases WHERE [name] = DB_NAME() AND [is_cdc_enabled] = 1)
    EXEC sys.sp_cdc_enable_db;
GO

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE [object_id] = OBJECT_ID(N'dbo.tbl_prestamos_legacy') AND [is_tracked_by_cdc] = 1)
    EXEC sys.sp_cdc_enable_table @source_schema = N'dbo', @source_name = N'tbl_prestamos_legacy',
         @role_name = NULL, @supports_net_changes = 1, @capture_instance = N'dbo_prestamos_legacy';
GO

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE [object_id] = OBJECT_ID(N'oltp.transactional_outbox') AND [is_tracked_by_cdc] = 1)
    EXEC sys.sp_cdc_enable_table @source_schema = N'oltp', @source_name = N'transactional_outbox',
         @role_name = NULL, @supports_net_changes = 0, @capture_instance = N'oltp_transactional_outbox';
GO

-- Lectura del log en ciclos acotados para no competir con el OLTP.
IF EXISTS (SELECT 1 FROM msdb.dbo.cdc_jobs WHERE [database_id] = DB_ID() AND [job_type] = N'capture')
    EXEC sys.sp_cdc_change_job @job_type = N'capture', @maxscans = 10, @maxtrans = 5000, @pollinginterval = 5;
GO

MERGE [stg].[cdc_watermark] AS t
USING (VALUES (N'dbo_prestamos_legacy'), (N'oltp_transactional_outbox')) s ([capture_instance])
   ON t.[capture_instance] = s.[capture_instance]
WHEN NOT MATCHED THEN INSERT ([capture_instance], [last_lsn], [updated_at_utc])
                      VALUES (s.[capture_instance], 0x00000000000000000000, SYSUTCDATETIME());
GO

-- -----------------------------------------------------------------------------
-- 2.1 Ingesta CDC del legado -> aterrizaje con checksum -> core bitemporal
-- -----------------------------------------------------------------------------
CREATE PROCEDURE [stg].[usp_ingest_cdc_legacy_contracts]
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    DECLARE @last BINARY(10), @from BINARY(10), @to BINARY(10), @min BINARY(10), @batch BIGINT, @n BIGINT;

    SELECT @last = [last_lsn] FROM [stg].[cdc_watermark] WHERE [capture_instance] = N'dbo_prestamos_legacy';
    SET @min = sys.fn_cdc_get_min_lsn(N'dbo_prestamos_legacy');
    SET @to  = sys.fn_cdc_get_max_lsn();
    -- El watermark guarda el último LSN ya procesado: se parte en el siguiente.
    SET @from = CASE WHEN @last = 0x00000000000000000000 THEN @min ELSE sys.fn_cdc_increment_lsn(@last) END;
    IF @from < @min
        THROW 54001, N'CDC_TRUNCADO: el log de cambios fue limpiado antes de procesarse; ejecute una recarga completa.', 1;
    IF @from > @to
        RETURN 0;

    EXEC [stg].[usp_log_batch_start] 'CDC_LEGADO_A_CORE', 'STG', 'dbo.tbl_prestamos_legacy',
         'stg.cdc_landing_contract', @batch OUTPUT;

    BEGIN TRANSACTION;
    INSERT INTO [stg].[cdc_landing_contract] ([batch_id], [cdc_start_lsn], [cdc_operation], [contract_id], [partner_id],
        [contract_number], [legacy_product_code], [branch_code], [currency_code], [origination_date], [maturity_date],
        [original_amount], [current_balance], [committed_limit], [annual_nominal_rate], [spread_rate],
        [repricing_frequency], [cmf_rating], [days_past_due], [contract_status], [valid_from], [row_checksum])
    SELECT @batch, c.[__$start_lsn], c.[__$operation], c.[id_prestamo], c.[id_socio], c.[num_operacion], c.[tipo_credito],
           c.[codigo_sucursal], c.[moneda], c.[fecha_otorgamiento], c.[fecha_vencimiento], c.[monto_otorgado],
           c.[saldo_capital], c.[linea_comprometida], c.[tasa_anual], c.[spread], c.[frec_revision], c.[clasif_riesgo_cmf],
           c.[dias_mora], c.[estado_prestamo], CAST(c.[fecha_otorgamiento] AS DATETIME2(2)), k.[row_checksum]
    FROM cdc.fn_cdc_get_net_changes_dbo_prestamos_legacy(@from, @to, N'all') c
    CROSS APPLY [stg].[itvf_contract_row_checksum](c.[id_socio], c.[num_operacion], c.[tipo_credito], c.[moneda],
        c.[fecha_vencimiento], c.[saldo_capital], c.[linea_comprometida], c.[tasa_anual], c.[spread],
        c.[clasif_riesgo_cmf], c.[dias_mora], c.[estado_prestamo]) k;
    SET @n = @@ROWCOUNT;
    UPDATE [stg].[cdc_watermark] SET [last_lsn] = @to, [updated_at_utc] = SYSUTCDATETIME()
    WHERE [capture_instance] = N'dbo_prestamos_legacy';
    COMMIT TRANSACTION;

    IF @n > 0
        EXEC [core].[usp_sync_contracts_from_staging] @batch;   -- registra el cierre del lote
    ELSE
        EXEC [stg].[usp_log_batch_end] @batch, 'SUCCESS';
END;
GO

-- -----------------------------------------------------------------------------
-- 2.2 Relay del Transactional Outbox (CDC) -> inbox del ODS (effectively-once)
-- -----------------------------------------------------------------------------
CREATE PROCEDURE [stg].[usp_relay_outbox_to_inbox]
    @p_consumer_name VARCHAR(60) = 'ODS_BITEMPORAL'
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    DECLARE @last BINARY(10), @from BINARY(10), @to BINARY(10), @min BINARY(10), @max_id BIGINT;

    SELECT @last = [last_lsn] FROM [stg].[cdc_watermark] WHERE [capture_instance] = N'oltp_transactional_outbox';
    SET @min = sys.fn_cdc_get_min_lsn(N'oltp_transactional_outbox');
    SET @to  = sys.fn_cdc_get_max_lsn();
    SET @from = CASE WHEN @last = 0x00000000000000000000 THEN @min ELSE sys.fn_cdc_increment_lsn(@last) END;
    IF @from < @min
        THROW 54002, N'CDC_TRUNCADO: eventos del outbox no procesados fueron limpiados; reconstruya desde la tabla.', 1;
    IF @from > @to
        RETURN 0;

    BEGIN TRANSACTION;
    INSERT INTO [stg].[event_inbox] ([consumer_name], [event_uuid], [outbox_id], [event_type], [payload_json], [received_at_utc])
    SELECT @p_consumer_name, c.[event_uuid], c.[outbox_id], c.[event_type], c.[payload_json], SYSUTCDATETIME()
    FROM cdc.fn_cdc_get_all_changes_oltp_transactional_outbox(@from, @to, N'all') c
    WHERE c.[__$operation] = 2
      AND NOT EXISTS (SELECT 1 FROM [stg].[event_inbox] i
                      WHERE i.[consumer_name] = @p_consumer_name AND i.[event_uuid] = c.[event_uuid]);
    SELECT @max_id = MAX([outbox_id]) FROM [stg].[event_inbox] WHERE [consumer_name] = @p_consumer_name;
    MERGE [stg].[outbox_checkpoint] AS t
    USING (SELECT @p_consumer_name AS [consumer_name]) s ON t.[consumer_name] = s.[consumer_name]
    WHEN MATCHED THEN UPDATE SET [last_outbox_id] = ISNULL(@max_id, t.[last_outbox_id]), [updated_at_utc] = SYSUTCDATETIME()
    WHEN NOT MATCHED THEN INSERT ([consumer_name], [last_outbox_id], [updated_at_utc])
                          VALUES (@p_consumer_name, ISNULL(@max_id, 0), SYSUTCDATETIME());
    UPDATE [stg].[cdc_watermark] SET [last_lsn] = @to, [updated_at_utc] = SYSUTCDATETIME()
    WHERE [capture_instance] = N'oltp_transactional_outbox';
    COMMIT TRANSACTION;
END;
GO

-- =============================================================================
-- 3. BACKFILL DE CIERRES MENSUALES DEL LEGADO
--    Cada cierre mensual se carga como una versión efectiva durante su mes y
--    asertada desde el día siguiente al cierre: reproduce lo que el sistema
--    anterior sabía en cada momento. Es la única excepción a "no asertar en el
--    pasado" y se ejecuta una sola vez, antes de migrar el estado vigente.
--    El trigger de integridad AVF sigue validando que no haya solapamientos.
-- =============================================================================
CREATE PROCEDURE [core].[usp_backfill_legacy_month_ends]
    @p_from DATE,
    @p_to   DATE
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    DECLARE @batch BIGINT, @n BIGINT;
    EXEC [stg].[usp_log_batch_start] 'BACKFILL_CIERRES_LEGADO', 'CORE', 'dbo.tbl_prestamos_legacy_cierre_mensual',
         'core.contract_profile', @batch OUTPUT;

    BEGIN TRANSACTION;
    INSERT INTO [core].[contract_profile] ([contract_id], [partner_id], [contract_number], [product_code], [branch_code],
        [currency_code], [gl_account_id], [origination_date], [maturity_date], [original_amount], [current_balance],
        [committed_limit], [undrawn_commitment], [annual_nominal_rate], [spread_rate], [repricing_frequency],
        [next_repricing_date], [payment_frequency_months], [amortization_type], [cmf_portfolio_type], [cmf_rating],
        [days_past_due], [collateral_value], [liquidity_treatment_code], [contract_status], [balance_maintained_in_core],
        [ValidFrom], [ValidTo], [AssertedFrom], [AssertedTo], [change_type], [change_reason], [source_journal_entry_id])
    SELECT h.[id_prestamo], l.[id_socio], l.[num_operacion], m.[product_code], l.[codigo_sucursal], l.[moneda],
           m.[gl_account_id], l.[fecha_otorgamiento], l.[fecha_vencimiento], l.[monto_otorgado], h.[saldo_capital],
           l.[linea_comprometida],
           CASE WHEN l.[linea_comprometida] > h.[saldo_capital] THEN l.[linea_comprometida] - h.[saldo_capital] ELSE 0 END,
           h.[tasa_anual], l.[spread], l.[frec_revision], NULL, m.[payment_frequency_months], m.[amortization_type],
           m.[cmf_portfolio_type], h.[clasif_riesgo_cmf], h.[dias_mora], 0, m.[liquidity_treatment_code],
           h.[estado_prestamo], 1,
           CAST(DATEFROMPARTS(YEAR(h.[fecha_cierre]), MONTH(h.[fecha_cierre]), 1) AS DATETIME2(2)),
           CAST(DATEADD(DAY, 1, h.[fecha_cierre]) AS DATETIME2(2)),
           CAST(DATEADD(DAY, 1, h.[fecha_cierre]) AS DATETIME2(2)),
           '9999-12-31 23:59:59.99', 'INSERT', N'Backfill cierre mensual legado', NULL
    FROM [dbo].[tbl_prestamos_legacy_cierre_mensual] h
    JOIN [dbo].[tbl_prestamos_legacy] l ON l.[id_prestamo] = h.[id_prestamo]
    JOIN [stg].[map_legacy_product] m ON m.[legacy_product_code] = l.[tipo_credito]
    WHERE h.[fecha_cierre] BETWEEN @p_from AND @p_to
      AND h.[fecha_cierre] = EOMONTH(h.[fecha_cierre])
      AND NOT EXISTS (SELECT 1 FROM [core].[contract_profile] c
                      WHERE c.[contract_id] = h.[id_prestamo]
                        AND c.[ValidFrom] = CAST(DATEFROMPARTS(YEAR(h.[fecha_cierre]), MONTH(h.[fecha_cierre]), 1) AS DATETIME2(2)));
    SET @n = @@ROWCOUNT;
    COMMIT TRANSACTION;
    EXEC [stg].[usp_log_batch_end] @batch, 'SUCCESS', @n, @n;
END;
GO

-- =============================================================================
-- 4. MIGRACIÓN CON ASERCIÓN DIFERIDA (FUTURE ASSERTION)
--    Contratos nuevos: inserción masiva por lotes (set-based) con AssertedFrom = corte.
--    Contratos ya presentes en el core: aserción diferida de su nuevo estado
--    (core.usp_future_assertion), que deja AssertedTo de la versión actual = corte.
--    Antes del corte nada cambia para las consultas "as of now"; al cruzarlo, toda
--    la cartera migrada queda vigente a la vez, sin jobs ni ventana de mantención.
-- =============================================================================
CREATE PROCEDURE [core].[usp_migrate_legacy_portfolio_future_assertion]
    @p_cutover_utc DATETIME2(2),
    @p_batch_size  INT = 50000,
    @p_reason      NVARCHAR(300) = N'Migración de cartera legada con aserción diferida'
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @p_cutover_utc <= SYSUTCDATETIME()
        THROW 54010, N'MIGRACION: el corte debe estar en el futuro.', 1;

    DECLARE @batch BIGINT, @inserted BIGINT = 0, @rows INT = 1, @updated INT = 0;
    EXEC [stg].[usp_log_batch_start] 'MIGRACION_ASERCION_DIFERIDA', 'CORE', 'dbo.tbl_prestamos_legacy',
         'core.contract_profile', @batch OUTPUT;

    -- 4.1 Contratos nuevos, por lotes (transacciones cortas, log controlado).
    WHILE @rows > 0
    BEGIN
        BEGIN TRANSACTION;
        INSERT INTO [core].[contract_profile] ([contract_id], [partner_id], [contract_number], [product_code],
            [branch_code], [currency_code], [gl_account_id], [origination_date], [maturity_date], [original_amount],
            [current_balance], [committed_limit], [undrawn_commitment], [annual_nominal_rate], [spread_rate],
            [repricing_frequency], [next_repricing_date], [payment_frequency_months], [amortization_type],
            [cmf_portfolio_type], [cmf_rating], [days_past_due], [collateral_value], [liquidity_treatment_code],
            [contract_status], [balance_maintained_in_core], [ValidFrom], [ValidTo], [AssertedFrom], [AssertedTo],
            [change_type], [change_reason], [source_journal_entry_id])
        SELECT TOP (@p_batch_size)
               l.[id_prestamo], l.[id_socio], l.[num_operacion], m.[product_code], l.[codigo_sucursal], l.[moneda],
               m.[gl_account_id], l.[fecha_otorgamiento], l.[fecha_vencimiento], l.[monto_otorgado], l.[saldo_capital],
               l.[linea_comprometida],
               CASE WHEN l.[linea_comprometida] > l.[saldo_capital] THEN l.[linea_comprometida] - l.[saldo_capital] ELSE 0 END,
               l.[tasa_anual], l.[spread], l.[frec_revision], NULL, m.[payment_frequency_months], m.[amortization_type],
               m.[cmf_portfolio_type], l.[clasif_riesgo_cmf], l.[dias_mora], 0, m.[liquidity_treatment_code],
               l.[estado_prestamo], 1,
               -- Continúa donde termina el backfill (si existe), sin solapar su vigencia.
               ISNULL(CAST(DATEADD(DAY, 1, hb.[ultimo_cierre]) AS DATETIME2(2)), CAST(l.[fecha_otorgamiento] AS DATETIME2(2))),
               '9999-12-31 23:59:59.99', @p_cutover_utc, '9999-12-31 23:59:59.99', 'FUTURE_ASSERTION', @p_reason, NULL
        FROM [dbo].[tbl_prestamos_legacy] l
        JOIN [stg].[map_legacy_product] m ON m.[legacy_product_code] = l.[tipo_credito]
        OUTER APPLY (SELECT MAX(h.[fecha_cierre]) AS [ultimo_cierre]
                     FROM [dbo].[tbl_prestamos_legacy_cierre_mensual] h WHERE h.[id_prestamo] = l.[id_prestamo]) hb
        WHERE NOT EXISTS (SELECT 1 FROM [core].[contract_profile] c
                          WHERE c.[contract_id] = l.[id_prestamo] AND c.[AssertedTo] > @p_cutover_utc
                            AND c.[ValidTo] = '9999-12-31 23:59:59.99')
        ORDER BY l.[id_prestamo];
        SET @rows = @@ROWCOUNT;
        SET @inserted += @rows;
        COMMIT TRANSACTION;
    END;

    -- 4.2 Contratos que ya tenían versión vigente abierta: aserción diferida del nuevo estado.
    DECLARE @id BIGINT, @json NVARCHAR(MAX), @from DATETIME2(2) = CAST(CAST(@p_cutover_utc AS DATE) AS DATETIME2(2));
    DECLARE c CURSOR LOCAL FAST_FORWARD FOR
        SELECT l.[id_prestamo],
               (SELECT l.[saldo_capital] AS [current_balance], l.[tasa_anual] AS [annual_nominal_rate],
                       l.[clasif_riesgo_cmf] AS [cmf_rating], l.[dias_mora] AS [days_past_due],
                       l.[estado_prestamo] AS [contract_status]
                FOR JSON PATH, WITHOUT_ARRAY_WRAPPER)
        FROM [dbo].[tbl_prestamos_legacy] l
        JOIN [core].[contract_profile] cp ON cp.[contract_id] = l.[id_prestamo]
             AND cp.[AssertedTo] = '9999-12-31 23:59:59.99' AND cp.[AssertedFrom] < @p_cutover_utc
             AND cp.[ValidTo] = '9999-12-31 23:59:59.99'
        WHERE cp.[current_balance] <> l.[saldo_capital] OR cp.[cmf_rating] <> l.[clasif_riesgo_cmf]
           OR cp.[days_past_due] <> l.[dias_mora] OR cp.[contract_status] <> l.[estado_prestamo]
           OR cp.[annual_nominal_rate] <> l.[tasa_anual];
    OPEN c;
    FETCH NEXT FROM c INTO @id, @json;
    WHILE @@FETCH_STATUS = 0
    BEGIN
        EXEC [core].[usp_future_assertion] @p_contract_id = @id, @p_effective_from = @from, @p_changes_json = @json,
             @p_cutover_utc = @p_cutover_utc, @p_reason = @p_reason;
        SET @updated += 1;
        FETCH NEXT FROM c INTO @id, @json;
    END;
    CLOSE c;
    DEALLOCATE c;

    EXEC [stg].[usp_log_batch_end] @batch, 'SUCCESS', @p_extracted = @inserted, @p_loaded = @inserted;
    PRINT FORMATMESSAGE(N'Migración preparada: %I64d contratos nuevos y %d actualizados, vigentes desde %s UTC.',
                        @inserted, @updated, CONVERT(VARCHAR(30), @p_cutover_utc, 126));
END;
GO

-- =============================================================================
-- 5. PUBLICACIÓN DEL SNAPSHOT POR CONMUTACIÓN DE PARTICIONES
--    stg.fact_snapshot_day_input   : filas del día calculadas por el ETL (ledger + core).
--    stg.fact_snapshot_load        : mes completo a publicar (mismo esquema de partición
--                                    e índices que el hecho, requisito de SWITCH).
--    stg.fact_snapshot_switchout   : recibe la partición saliente.
--    Las versiones existentes del mes se conservan; una fila del día sólo genera
--    nueva versión (restatement_seq + 1) si su checksum cambió.
-- =============================================================================
DROP TABLE IF EXISTS [stg].[fact_snapshot_day_input];
DROP TABLE IF EXISTS [stg].[fact_snapshot_load];
DROP TABLE IF EXISTS [stg].[fact_snapshot_switchout];
GO

CREATE TABLE [stg].[fact_snapshot_day_input] (
    [snapshot_date_key] INT NOT NULL, [contract_key] INT NOT NULL, [snapshot_asserted_at] DATETIME2(2) NOT NULL,
    [partner_key] INT NOT NULL, [product_key] INT NOT NULL, [branch_key] INT NOT NULL, [gl_account_key] INT NOT NULL,
    [risk_band_key] INT NOT NULL, [liquidity_treatment_key] INT NOT NULL, [currency_code] CHAR(3) NOT NULL,
    [balance_orig] DECIMAL(19,4) NOT NULL, [balance_clp] DECIMAL(19,4) NOT NULL, [fx_rate_to_clp] DECIMAL(12,6) NOT NULL,
    [accrued_interest_clp] DECIMAL(19,4) NOT NULL, [contractual_rate] DECIMAL(12,6) NOT NULL,
    [regulatory_provision_clp] DECIMAL(19,4) NOT NULL, [collateral_value_clp] DECIMAL(19,4) NOT NULL,
    [net_exposure_clp] DECIMAL(19,4) NOT NULL, [market_value_clp] DECIMAL(19,4) NOT NULL,
    [undrawn_commitment_clp] DECIMAL(19,4) NOT NULL, [pending_debits_clp] DECIMAL(19,4) NOT NULL,
    [pending_credits_clp] DECIMAL(19,4) NOT NULL, [residual_maturity_days] INT NULL, [row_checksum] BINARY(32) NOT NULL,
    CONSTRAINT [PK_fact_snapshot_day_input] PRIMARY KEY ([snapshot_date_key], [contract_key])
);
GO

-- Mismas columnas, tipos, nulabilidad, esquema de partición e índices que dwh.fact_account_balance_snapshot.
CREATE TABLE [stg].[fact_snapshot_load] (
    [snapshot_date_key] INT NOT NULL, [contract_key] INT NOT NULL, [restatement_seq] SMALLINT NOT NULL,
    [snapshot_asserted_at] DATETIME2(2) NOT NULL, [partner_key] INT NOT NULL, [product_key] INT NOT NULL,
    [branch_key] INT NOT NULL, [gl_account_key] INT NOT NULL, [risk_band_key] INT NOT NULL,
    [liquidity_treatment_key] INT NOT NULL, [currency_code] CHAR(3) NOT NULL, [balance_orig] DECIMAL(19,4) NOT NULL,
    [balance_clp] DECIMAL(19,4) NOT NULL, [fx_rate_to_clp] DECIMAL(12,6) NOT NULL, [accrued_interest_clp] DECIMAL(19,4) NOT NULL,
    [contractual_rate] DECIMAL(12,6) NOT NULL, [regulatory_provision_clp] DECIMAL(19,4) NOT NULL,
    [collateral_value_clp] DECIMAL(19,4) NOT NULL, [net_exposure_clp] DECIMAL(19,4) NOT NULL,
    [market_value_clp] DECIMAL(19,4) NOT NULL, [undrawn_commitment_clp] DECIMAL(19,4) NOT NULL,
    [pending_debits_clp] DECIMAL(19,4) NOT NULL, [pending_credits_clp] DECIMAL(19,4) NOT NULL,
    [residual_maturity_days] INT NULL, [row_checksum] BINARY(32) NOT NULL
) ON [ps_monthly_date_key] ([snapshot_date_key]);
CREATE CLUSTERED COLUMNSTORE INDEX [CCI_fact_snapshot_load] ON [stg].[fact_snapshot_load]
    ON [ps_monthly_date_key] ([snapshot_date_key]);
CREATE UNIQUE NONCLUSTERED INDEX [UX_fact_snapshot_load_grain] ON [stg].[fact_snapshot_load]
    ([snapshot_date_key], [contract_key], [restatement_seq]) INCLUDE ([snapshot_asserted_at], [row_checksum])
    ON [ps_monthly_date_key] ([snapshot_date_key]);
GO

CREATE TABLE [stg].[fact_snapshot_switchout] (
    [snapshot_date_key] INT NOT NULL, [contract_key] INT NOT NULL, [restatement_seq] SMALLINT NOT NULL,
    [snapshot_asserted_at] DATETIME2(2) NOT NULL, [partner_key] INT NOT NULL, [product_key] INT NOT NULL,
    [branch_key] INT NOT NULL, [gl_account_key] INT NOT NULL, [risk_band_key] INT NOT NULL,
    [liquidity_treatment_key] INT NOT NULL, [currency_code] CHAR(3) NOT NULL, [balance_orig] DECIMAL(19,4) NOT NULL,
    [balance_clp] DECIMAL(19,4) NOT NULL, [fx_rate_to_clp] DECIMAL(12,6) NOT NULL, [accrued_interest_clp] DECIMAL(19,4) NOT NULL,
    [contractual_rate] DECIMAL(12,6) NOT NULL, [regulatory_provision_clp] DECIMAL(19,4) NOT NULL,
    [collateral_value_clp] DECIMAL(19,4) NOT NULL, [net_exposure_clp] DECIMAL(19,4) NOT NULL,
    [market_value_clp] DECIMAL(19,4) NOT NULL, [undrawn_commitment_clp] DECIMAL(19,4) NOT NULL,
    [pending_debits_clp] DECIMAL(19,4) NOT NULL, [pending_credits_clp] DECIMAL(19,4) NOT NULL,
    [residual_maturity_days] INT NULL, [row_checksum] BINARY(32) NOT NULL
) ON [ps_monthly_date_key] ([snapshot_date_key]);
CREATE CLUSTERED COLUMNSTORE INDEX [CCI_fact_snapshot_switchout] ON [stg].[fact_snapshot_switchout]
    ON [ps_monthly_date_key] ([snapshot_date_key]);
CREATE UNIQUE NONCLUSTERED INDEX [UX_fact_snapshot_switchout_grain] ON [stg].[fact_snapshot_switchout]
    ([snapshot_date_key], [contract_key], [restatement_seq]) INCLUDE ([snapshot_asserted_at], [row_checksum])
    ON [ps_monthly_date_key] ([snapshot_date_key]);
GO

CREATE PROCEDURE [dwh].[usp_publish_snapshot_partition_switch]
    @p_snapshot_date_key INT
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    DECLARE @p INT = $PARTITION.[pf_monthly_date_key](@p_snapshot_date_key);
    DECLARE @month_from INT = (@p_snapshot_date_key / 100) * 100 + 1;
    DECLARE @month_to INT = CAST(CONVERT(CHAR(8), DATEADD(MONTH, 1, CONVERT(DATE, CAST(@month_from AS CHAR(8)), 112)), 112) AS INT);
    DECLARE @batch BIGINT, @new INT, @sql NVARCHAR(400);

    IF NOT EXISTS (SELECT 1 FROM [stg].[fact_snapshot_day_input] WHERE [snapshot_date_key] = @p_snapshot_date_key)
        THROW 54020, N'PUBLICACION: no hay filas del día en stg.fact_snapshot_day_input.', 1;
    IF EXISTS (SELECT 1 FROM [stg].[fact_snapshot_day_input] WHERE [snapshot_date_key] <> @p_snapshot_date_key)
        THROW 54021, N'PUBLICACION: la entrada contiene fechas distintas a la publicada.', 1;

    EXEC [stg].[usp_log_batch_start] 'DWH_PUBLICACION_SNAPSHOT', 'DWH', 'stg.fact_snapshot_day_input',
         'dwh.fact_account_balance_snapshot', @batch OUTPUT;

    -- 1. Tablas de trabajo vacías en la partición del mes.
    SET @sql = CONCAT(N'TRUNCATE TABLE [stg].[fact_snapshot_load] WITH (PARTITIONS (', @p, N'));');
    EXEC sys.sp_executesql @sql;
    SET @sql = CONCAT(N'TRUNCATE TABLE [stg].[fact_snapshot_switchout] WITH (PARTITIONS (', @p, N'));');
    EXEC sys.sp_executesql @sql;

    -- 2. Mes vigente completo (todas las versiones) + versiones nuevas del día.
    INSERT INTO [stg].[fact_snapshot_load] WITH (TABLOCK)
    SELECT * FROM [dwh].[fact_account_balance_snapshot]
    WHERE [snapshot_date_key] >= @month_from AND [snapshot_date_key] < @month_to;

    INSERT INTO [stg].[fact_snapshot_load] WITH (TABLOCK) ([snapshot_date_key], [contract_key], [restatement_seq],
        [snapshot_asserted_at], [partner_key], [product_key], [branch_key], [gl_account_key], [risk_band_key],
        [liquidity_treatment_key], [currency_code], [balance_orig], [balance_clp], [fx_rate_to_clp], [accrued_interest_clp],
        [contractual_rate], [regulatory_provision_clp], [collateral_value_clp], [net_exposure_clp], [market_value_clp],
        [undrawn_commitment_clp], [pending_debits_clp], [pending_credits_clp], [residual_maturity_days], [row_checksum])
    SELECT i.[snapshot_date_key], i.[contract_key], ISNULL(cur.[seq], 0) + 1, i.[snapshot_asserted_at], i.[partner_key],
           i.[product_key], i.[branch_key], i.[gl_account_key], i.[risk_band_key], i.[liquidity_treatment_key],
           i.[currency_code], i.[balance_orig], i.[balance_clp], i.[fx_rate_to_clp], i.[accrued_interest_clp],
           i.[contractual_rate], i.[regulatory_provision_clp], i.[collateral_value_clp], i.[net_exposure_clp],
           i.[market_value_clp], i.[undrawn_commitment_clp], i.[pending_debits_clp], i.[pending_credits_clp],
           i.[residual_maturity_days], i.[row_checksum]
    FROM [stg].[fact_snapshot_day_input] i
    OUTER APPLY (SELECT TOP (1) s.[restatement_seq] AS [seq], s.[row_checksum] AS [ck]
                 FROM [dwh].[fact_account_balance_snapshot] s
                 WHERE s.[snapshot_date_key] = i.[snapshot_date_key] AND s.[contract_key] = i.[contract_key]
                 ORDER BY s.[restatement_seq] DESC) cur
    WHERE cur.[ck] IS NULL OR cur.[ck] <> i.[row_checksum];
    SET @new = @@ROWCOUNT;

    -- 3. Conmutación atómica: sale la partición actual y entra la nueva, en una transacción.
    --    WAIT_AT_LOW_PRIORITY evita encolar a los lectores detrás del lock de esquema.
    BEGIN TRANSACTION;
    ALTER TABLE [dwh].[fact_account_balance_snapshot] SWITCH PARTITION @p TO [stg].[fact_snapshot_switchout] PARTITION @p
        WITH (WAIT_AT_LOW_PRIORITY (MAX_DURATION = 1 MINUTES, ABORT_AFTER_WAIT = SELF));
    ALTER TABLE [stg].[fact_snapshot_load] SWITCH PARTITION @p TO [dwh].[fact_account_balance_snapshot] PARTITION @p
        WITH (WAIT_AT_LOW_PRIORITY (MAX_DURATION = 1 MINUTES, ABORT_AFTER_WAIT = SELF));
    COMMIT TRANSACTION;

    SET @sql = CONCAT(N'TRUNCATE TABLE [stg].[fact_snapshot_switchout] WITH (PARTITIONS (', @p, N'));');
    EXEC sys.sp_executesql @sql;
    DELETE FROM [stg].[fact_snapshot_day_input] WHERE [snapshot_date_key] = @p_snapshot_date_key;
    EXEC [stg].[usp_log_batch_end] @batch, 'SUCCESS', @p_extracted = @new, @p_loaded = @new;
END;
GO

-- =============================================================================
-- 6. CONCILIACIÓN DE CUATRO VÍAS (diferencia exigida: 0,0000 exacto)
--    Por contrato, a la fecha indicada y con todo lo conocido hoy:
--      legado (si el contrato existe allí) · core (si mantiene saldo en el core)
--      · ledger (saldo de la cuenta principal del contrato con su signo normal y
--        fecha valor <= fecha) · DWH (última reexpresión del snapshot).
-- =============================================================================
CREATE PROCEDURE [stg].[usp_reconcile_four_way]
    @p_reconciliation_date DATE
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    DECLARE @batch BIGINT, @bad INT, @total INT, @diff DECIMAL(19,4);
    DECLARE @date_key INT = CAST(CONVERT(CHAR(8), @p_reconciliation_date, 112) AS INT);
    DECLARE @biz DATETIME2(2) = DATEADD(MILLISECOND, -10, CAST(DATEADD(DAY, 1, @p_reconciliation_date) AS DATETIME2(2)));
    DECLARE @now DATETIME2(2) = SYSUTCDATETIME();

    EXEC [stg].[usp_log_batch_start] 'CONCILIACION_4_VIAS', 'STG', 'legado / core / ledger / DWH',
         'stg.reconciliation_result', @batch OUTPUT;

    WITH core_now AS (
        SELECT c.[contract_id], c.[gl_account_id], c.[current_balance], c.[balance_maintained_in_core], c.[product_code]
        FROM [core].[itvf_contract_as_of](@biz, @now) c
        WHERE c.[product_code] <> 'LINEA_NO_GIRADA'
    ),
    ledger AS (
        SELECT cn.[contract_id],
               SUM(CASE a.[normal_balance] WHEN 'D' THEN p.[functional_debit] - p.[functional_credit]
                                           ELSE p.[functional_credit] - p.[functional_debit] END) AS [balance]
        FROM core_now cn
        JOIN [oltp].[ledger_posting] p ON p.[contract_id] = cn.[contract_id] AND p.[account_id] = cn.[gl_account_id]
                                      AND p.[posting_status] = 'POSTED'
        JOIN [oltp].[journal_entry] j ON j.[journal_entry_id] = p.[journal_entry_id] AND j.[value_date] <= @p_reconciliation_date
        JOIN [oltp].[ledger_account] a ON a.[account_id] = p.[account_id]
        GROUP BY cn.[contract_id]
    ),
    dwh AS (
        SELECT c.[contract_id], s.[balance_clp] AS [balance]
        FROM [dwh].[vw_snapshot_latest] s
        JOIN [dwh].[dim_account_contract] c ON c.[contract_key] = s.[contract_key]
        WHERE s.[snapshot_date_key] = @date_key
    )
    INSERT INTO [stg].[reconciliation_result] ([batch_id], [reconciliation_date], [contract_id], [legacy_balance],
                                               [core_balance], [ledger_balance], [dwh_balance], [status])
    SELECT @batch, @p_reconciliation_date, cn.[contract_id], leg.[saldo_capital],
           CASE WHEN cn.[balance_maintained_in_core] = 1 THEN cn.[current_balance] END,
           ISNULL(l.[balance], 0), d.[balance],
           CASE WHEN ISNULL(leg.[saldo_capital], ISNULL(l.[balance], 0)) = ISNULL(l.[balance], 0)
                 AND (cn.[balance_maintained_in_core] = 0 OR cn.[current_balance] = ISNULL(l.[balance], 0))
                 AND ISNULL(d.[balance], 0) = ISNULL(l.[balance], 0)
                THEN 'CUADRADO' ELSE 'DESCUADRE' END
    FROM core_now cn
    LEFT JOIN ledger l ON l.[contract_id] = cn.[contract_id]
    LEFT JOIN dwh d ON d.[contract_id] = cn.[contract_id]
    LEFT JOIN [dbo].[tbl_prestamos_legacy] leg ON leg.[id_prestamo] = cn.[contract_id];

    SELECT @total = COUNT(*), @bad = SUM(CASE WHEN [status] <> 'CUADRADO' THEN 1 ELSE 0 END),
           @diff = SUM(ABS([diff_legacy_ledger]) + ABS([diff_core_ledger]) + ABS([diff_ledger_dwh]))
    FROM [stg].[reconciliation_result] WHERE [batch_id] = @batch;

    IF ISNULL(@bad, 0) = 0
        EXEC [stg].[usp_log_batch_end] @batch, 'RECONCILED', @total, @total, 0, 0;
    ELSE
    BEGIN
        DECLARE @msg NVARCHAR(400) = FORMATMESSAGE(N'DESCUADRE: %d de %d contratos con diferencias; suma de diferencias %s CLP.',
                                                   @bad, @total, CONVERT(VARCHAR(40), @diff));
        EXEC [stg].[usp_log_batch_end] @batch, 'DISCREPANCY', @total, @total - @bad, @bad, @diff, @msg;
        THROW 54030, @msg, 1;
    END;
END;
GO

-- =============================================================================
-- 7. EXTENSIÓN DE PARTICIONES MENSUALES (ventana deslizante)
--    Sólo se divide la última partición si está vacía en los hechos con columnstore
--    (SQL Server no permite dividir una partición columnstore con datos).
-- =============================================================================
CREATE PROCEDURE [dwh].[usp_extend_monthly_partitions]
    @p_months_ahead INT = 12
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @max_boundary INT, @next DATE, @key INT, @sql NVARCHAR(300);
    DECLARE @target DATE = DATEADD(MONTH, @p_months_ahead, CAST(SYSDATETIME() AS DATE));

    SELECT @max_boundary = MAX(CAST(prv.[value] AS INT))
    FROM sys.partition_range_values prv
    JOIN sys.partition_functions pf ON pf.[function_id] = prv.[function_id]
    WHERE pf.[name] = N'pf_monthly_date_key';
    SET @next = DATEADD(MONTH, 1, CONVERT(DATE, CAST(@max_boundary AS CHAR(8)), 112));

    IF EXISTS (SELECT 1 FROM [dwh].[fact_account_balance_snapshot] WHERE [snapshot_date_key] >= @max_boundary)
       OR EXISTS (SELECT 1 FROM [dwh].[fact_contractual_cashflows] WHERE [position_date_key] >= @max_boundary)
       OR EXISTS (SELECT 1 FROM [dwh].[fact_gl_transaction_postings] WHERE [posting_date_key] >= @max_boundary)
        THROW 54040, N'PARTICIONES: la última partición ya tiene datos; extienda la función antes de cargar ese mes.', 1;

    WHILE @next <= @target
    BEGIN
        SET @key = CAST(CONVERT(CHAR(8), @next, 112) AS INT);
        ALTER PARTITION SCHEME [ps_monthly_date_key] NEXT USED [PRIMARY];
        SET @sql = CONCAT(N'ALTER PARTITION FUNCTION [pf_monthly_date_key] () SPLIT RANGE (', @key, N');');
        EXEC sys.sp_executesql @sql;
        SET @next = DATEADD(MONTH, 1, @next);
    END;
END;
GO

GRANT EXECUTE ON [stg].[usp_ingest_cdc_legacy_contracts]                TO [app_etl_operator];
GRANT EXECUTE ON [stg].[usp_relay_outbox_to_inbox]                      TO [app_etl_operator];
GRANT EXECUTE ON [core].[usp_backfill_legacy_month_ends]                TO [app_etl_operator];
GRANT EXECUTE ON [core].[usp_migrate_legacy_portfolio_future_assertion] TO [app_etl_operator];
GRANT EXECUTE ON [dwh].[usp_publish_snapshot_partition_switch]          TO [app_etl_operator];
GRANT EXECUTE ON [stg].[usp_reconcile_four_way]                         TO [app_etl_operator];
GRANT EXECUTE ON [dwh].[usp_extend_monthly_partitions]                  TO [app_etl_operator];
GRANT INSERT, SELECT ON [stg].[fact_snapshot_day_input]                 TO [app_etl_operator];
GO

-- -----------------------------------------------------------------------------
-- Ejemplo: migración con corte el domingo a medianoche (hora de Chile = 03:00 UTC en verano)
-- -----------------------------------------------------------------------------
/*
EXEC core.usp_backfill_legacy_month_ends @p_from = '2024-01-31', @p_to = '2026-09-30';
EXEC core.usp_migrate_legacy_portfolio_future_assertion @p_cutover_utc = '2026-10-19 03:00:00';
-- Antes del corte: invisible. Después: vigente.
SELECT COUNT(*) FROM core.itvf_contract_as_of(SYSDATETIME(), '2026-10-19 02:59:59') WHERE change_type = 'FUTURE_ASSERTION';
SELECT COUNT(*) FROM core.itvf_contract_as_of(SYSDATETIME(), '2026-10-19 03:00:01') WHERE change_type = 'FUTURE_ASSERTION';
EXEC stg.usp_reconcile_four_way @p_reconciliation_date = '2026-10-19';
*/
