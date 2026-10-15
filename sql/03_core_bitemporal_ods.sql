-- =============================================================================
-- 03_core_bitemporal_ods.sql
-- =============================================================================
-- Capa 3: ODS bitemporal bajo el Asserted Versioning Framework (Johnston & Weis).
--
-- Tres líneas de tiempo, cada una con un propósito distinto:
--   1. Tiempo efectivo  [ValidFrom, ValidTo)       : cuándo el hecho fue verdad en la economía.
--   2. Tiempo asertivo  [AssertedFrom, AssertedTo) : desde/hasta cuándo el banco AFIRMA ese hecho.
--      Es explícito (no GENERATED) porque AVF exige poder asertar en el FUTURO
--      (aserciones diferidas para migraciones sin ventana de mantención), algo que
--      el system time nativo no permite.
--   3. System time nativo [SysStartTime, SysEndTime) con SYSTEM_VERSIONING: registro
--      físico, a prueba de manipulación, de cada retiro de aserción (cada UPDATE
--      de AssertedTo deja la imagen previa en core.contract_profile_history).
--
-- Reglas impuestas en el motor:
--   * Cierre abierto con 9999-12-31 23:59:59.99 (nunca NULL).
--   * Integridad temporal de entidad: para un contrato no pueden solaparse dos
--     filas en el rectángulo [efectivo x asertivo] (SQL Server 2017 no tiene
--     WITHOUT OVERLAPS; se impone con trigger AFTER, porque las tablas temporales
--     no admiten INSTEAD OF).
--   * Sin DELETE y sin UPDATE de datos: la única mutación permitida es adelantar
--     AssertedTo (retirar una aserción).
--   * Operaciones: Insert, Update (cambio real de negocio, no retroactivo),
--     Correction (enmienda retroactiva ligada a un asiento del ledger) y
--     Future Assertion (carga diferida con activación instantánea).
-- =============================================================================

USE [CoreBanking];
GO

DROP PROCEDURE IF EXISTS [core].[usp_sync_contracts_from_staging];
DROP PROCEDURE IF EXISTS [core].[usp_future_assertion];
DROP PROCEDURE IF EXISTS [core].[usp_bitemporal_correction];
DROP PROCEDURE IF EXISTS [core].[usp_bitemporal_update];
DROP PROCEDURE IF EXISTS [core].[usp_bitemporal_apply_change];
DROP PROCEDURE IF EXISTS [core].[usp_bitemporal_insert];
DROP VIEW      IF EXISTS [core].[vw_contract_profile_current];
DROP FUNCTION  IF EXISTS [core].[itvf_contract_as_of];
GO

IF OBJECT_ID(N'core.contract_profile', N'U') IS NOT NULL
BEGIN
    IF OBJECTPROPERTY(OBJECT_ID(N'core.contract_profile'), 'TableTemporalType') = 2
        ALTER TABLE [core].[contract_profile] SET (SYSTEM_VERSIONING = OFF);
    DROP TABLE [core].[contract_profile];
END;
DROP TABLE IF EXISTS [core].[contract_profile_history];
GO

-- -----------------------------------------------------------------------------
-- 1. Tabla bitemporal con system-versioning nativo
-- -----------------------------------------------------------------------------
CREATE TABLE [core].[contract_profile] (
    [contract_id]                 BIGINT         NOT NULL,
    [partner_id]                  BIGINT         NOT NULL,
    [contract_number]             VARCHAR(50)    NOT NULL,
    [product_code]                VARCHAR(30)    NOT NULL,
    [branch_code]                 VARCHAR(20)    NOT NULL,
    [currency_code]               CHAR(3)        NOT NULL,
    [gl_account_id]               BIGINT         NOT NULL,
    [origination_date]            DATE           NOT NULL,
    [maturity_date]               DATE           NULL,
    [original_amount]             DECIMAL(19,4)  NOT NULL,
    [current_balance]             DECIMAL(19,4)  NOT NULL,
    [committed_limit]             DECIMAL(19,4)  NOT NULL CONSTRAINT [DF_cp_committed] DEFAULT (0),
    [undrawn_commitment]          DECIMAL(19,4)  NOT NULL CONSTRAINT [DF_cp_undrawn] DEFAULT (0),
    [annual_nominal_rate]         DECIMAL(12,6)  NOT NULL,
    [spread_rate]                 DECIMAL(12,6)  NOT NULL CONSTRAINT [DF_cp_spread] DEFAULT (0),
    [repricing_frequency]         VARCHAR(12)    NOT NULL,
    [next_repricing_date]         DATE           NULL,
    [payment_frequency_months]    INT            NULL,
    [amortization_type]           VARCHAR(10)    NOT NULL,
    [cmf_portfolio_type]          VARCHAR(3)     NOT NULL,
    [cmf_rating]                  VARCHAR(4)     NOT NULL,
    [days_past_due]               INT            NOT NULL CONSTRAINT [DF_cp_dpd] DEFAULT (0),
    [collateral_value]            DECIMAL(19,4)  NOT NULL CONSTRAINT [DF_cp_collateral] DEFAULT (0),
    [liquidity_treatment_code]    VARCHAR(40)    NOT NULL,
    [contract_status]             VARCHAR(20)    NOT NULL,
    [balance_maintained_in_core]  BIT            NOT NULL CONSTRAINT [DF_cp_in_core] DEFAULT (1),

    -- Tiempo efectivo (negocio)
    [ValidFrom]                   DATETIME2(2)   NOT NULL,
    [ValidTo]                     DATETIME2(2)   NOT NULL CONSTRAINT [DF_cp_valid_to] DEFAULT ('9999-12-31 23:59:59.99'),
    -- Tiempo asertivo (AVF)
    [AssertedFrom]                DATETIME2(2)   NOT NULL,
    [AssertedTo]                  DATETIME2(2)   NOT NULL CONSTRAINT [DF_cp_asserted_to] DEFAULT ('9999-12-31 23:59:59.99'),
    [change_type]                 VARCHAR(20)    NOT NULL,
    [change_reason]               NVARCHAR(400)  NULL,
    [source_journal_entry_id]     BIGINT         NULL,

    -- Tiempo de sistema físico (nativo)
    [SysStartTime]                DATETIME2(7) GENERATED ALWAYS AS ROW START NOT NULL,
    [SysEndTime]                  DATETIME2(7) GENERATED ALWAYS AS ROW END   NOT NULL,
    PERIOD FOR SYSTEM_TIME ([SysStartTime], [SysEndTime]),

    CONSTRAINT [PK_contract_profile] PRIMARY KEY CLUSTERED ([contract_id], [ValidFrom], [AssertedFrom]),
    CONSTRAINT [CK_cp_effective_period] CHECK ([ValidFrom] < [ValidTo]),
    CONSTRAINT [CK_cp_asserted_period] CHECK ([AssertedFrom] < [AssertedTo]),
    CONSTRAINT [CK_cp_change_type] CHECK ([change_type] IN ('INSERT','UPDATE','CORRECTION','FUTURE_ASSERTION','CARRY_FORWARD')),
    CONSTRAINT [CK_cp_amortization] CHECK ([amortization_type] IN ('BULLET','POOL','NONE')),
    CONSTRAINT [CK_cp_undrawn] CHECK ([undrawn_commitment] >= 0 AND ([committed_limit] = 0 OR [undrawn_commitment] <= [committed_limit])),
    CONSTRAINT [CK_cp_correction_source] CHECK ([change_type] <> 'CORRECTION' OR [source_journal_entry_id] IS NOT NULL),
    CONSTRAINT [FK_cp_journal_entry] FOREIGN KEY ([source_journal_entry_id]) REFERENCES [oltp].[journal_entry] ([journal_entry_id])
)
WITH (SYSTEM_VERSIONING = ON (HISTORY_TABLE = [core].[contract_profile_history], DATA_CONSISTENCY_CHECK = ON));
GO

-- Soporte de la búsqueda de solapamientos y del time travel "as of".
CREATE NONCLUSTERED INDEX [IX_cp_assertion_lookup]
    ON [core].[contract_profile] ([contract_id], [AssertedTo], [AssertedFrom])
    INCLUDE ([ValidFrom], [ValidTo]);
CREATE NONCLUSTERED INDEX [IX_cp_current_assertions]
    ON [core].[contract_profile] ([ValidFrom], [ValidTo])
    INCLUDE ([contract_id], [current_balance], [contract_status], [liquidity_treatment_code])
    WHERE [AssertedTo] = '9999-12-31 23:59:59.99';
GO

-- -----------------------------------------------------------------------------
-- 2. Integridad AVF en el motor
-- -----------------------------------------------------------------------------
CREATE OR ALTER TRIGGER [core].[tr_contract_profile_avf_integrity] ON [core].[contract_profile]
AFTER INSERT, UPDATE
AS
BEGIN
    SET NOCOUNT ON;
    IF NOT EXISTS (SELECT 1 FROM inserted) RETURN;

    -- (a) En un UPDATE sólo puede cambiar AssertedTo, y sólo hacia atrás (retiro).
    IF EXISTS (SELECT 1 FROM deleted)
    BEGIN
        IF UPDATE([contract_id]) OR UPDATE([ValidFrom]) OR UPDATE([AssertedFrom])
            THROW 53001, N'AVF_INMUTABLE: la clave bitemporal no puede modificarse.', 1;
        IF EXISTS (
            SELECT 1 FROM inserted i
            JOIN deleted d ON d.[contract_id] = i.[contract_id] AND d.[ValidFrom] = i.[ValidFrom] AND d.[AssertedFrom] = i.[AssertedFrom]
            WHERE i.[AssertedTo] >= d.[AssertedTo]
               OR EXISTS (
                    SELECT i.[partner_id], i.[contract_number], i.[product_code], i.[branch_code], i.[currency_code],
                           i.[gl_account_id], i.[origination_date], i.[maturity_date], i.[original_amount], i.[current_balance],
                           i.[committed_limit], i.[undrawn_commitment], i.[annual_nominal_rate], i.[spread_rate],
                           i.[repricing_frequency], i.[next_repricing_date], i.[payment_frequency_months],
                           i.[amortization_type], i.[cmf_portfolio_type], i.[cmf_rating], i.[days_past_due],
                           i.[collateral_value], i.[liquidity_treatment_code], i.[contract_status],
                           i.[balance_maintained_in_core], i.[ValidTo], i.[change_type], i.[change_reason],
                           i.[source_journal_entry_id]
                    EXCEPT
                    SELECT d.[partner_id], d.[contract_number], d.[product_code], d.[branch_code], d.[currency_code],
                           d.[gl_account_id], d.[origination_date], d.[maturity_date], d.[original_amount], d.[current_balance],
                           d.[committed_limit], d.[undrawn_commitment], d.[annual_nominal_rate], d.[spread_rate],
                           d.[repricing_frequency], d.[next_repricing_date], d.[payment_frequency_months],
                           d.[amortization_type], d.[cmf_portfolio_type], d.[cmf_rating], d.[days_past_due],
                           d.[collateral_value], d.[liquidity_treatment_code], d.[contract_status],
                           d.[balance_maintained_in_core], d.[ValidTo], d.[change_type], d.[change_reason],
                           d.[source_journal_entry_id]))
            THROW 53002, N'AVF_INMUTABLE: sólo se permite adelantar AssertedTo (retiro de aserción).', 1;
    END;

    -- (b) Sin solapamiento en el rectángulo efectivo x asertivo para el mismo contrato.
    IF EXISTS (
        SELECT 1
        FROM inserted i
        JOIN [core].[contract_profile] c
          ON c.[contract_id] = i.[contract_id]
         AND NOT (c.[ValidFrom] = i.[ValidFrom] AND c.[AssertedFrom] = i.[AssertedFrom])
         AND c.[ValidFrom] < i.[ValidTo] AND i.[ValidFrom] < c.[ValidTo]
         AND c.[AssertedFrom] < i.[AssertedTo] AND i.[AssertedFrom] < c.[AssertedTo])
        THROW 53003, N'AVF_SOLAPAMIENTO: el intervalo efectivo/asertivo se solapa con una versión existente del contrato.', 1;
END;
GO

CREATE OR ALTER TRIGGER [core].[tr_contract_profile_no_delete] ON [core].[contract_profile]
AFTER DELETE
AS
BEGIN
    SET NOCOUNT ON;
    THROW 53004, N'AVF_INMUTABLE: las versiones bitemporales no se eliminan; retire la aserción.', 1;
END;
GO

DENY DELETE ON [core].[contract_profile] TO [public];
DENY INSERT, UPDATE ON [core].[contract_profile] TO [public];   -- sólo vía procedimientos
GO

-- -----------------------------------------------------------------------------
-- 3. Time travel parametrizado (sin SQL dinámico ni concatenación de cadenas)
-- -----------------------------------------------------------------------------
CREATE FUNCTION [core].[itvf_contract_as_of] (@p_business_ts DATETIME2(2), @p_assertion_ts DATETIME2(2))
RETURNS TABLE
AS
RETURN
    SELECT c.*
    FROM [core].[contract_profile] c
    WHERE c.[ValidFrom] <= @p_business_ts AND @p_business_ts < c.[ValidTo]
      AND c.[AssertedFrom] <= @p_assertion_ts AND @p_assertion_ts < c.[AssertedTo];
GO

CREATE VIEW [core].[vw_contract_profile_current]
AS
SELECT c.*
FROM [core].[contract_profile] c
WHERE c.[ValidFrom] <= SYSDATETIME() AND SYSDATETIME() < c.[ValidTo]
  AND c.[AssertedFrom] <= SYSUTCDATETIME() AND SYSUTCDATETIME() < c.[AssertedTo];
GO

-- -----------------------------------------------------------------------------
-- 4. Bitemporal Insert
--    @p_values_json contiene el estado completo del contrato (claves = columnas).
-- -----------------------------------------------------------------------------
CREATE PROCEDURE [core].[usp_bitemporal_insert]
    @p_contract_id    BIGINT,
    @p_values_json    NVARCHAR(MAX),
    @p_valid_from     DATETIME2(2),
    @p_asserted_from  DATETIME2(2) = NULL,   -- NULL = ahora; futuro = aserción diferida
    @p_reason         NVARCHAR(400) = N'Alta de contrato'
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    DECLARE @now DATETIME2(2) = SYSUTCDATETIME();
    DECLARE @a DATETIME2(2) = ISNULL(@p_asserted_from, @now);

    IF ISJSON(@p_values_json) <> 1 THROW 53010, N'AVF: @p_values_json no es JSON válido.', 1;
    IF @a < @now THROW 53011, N'AVF: no se puede asertar en el pasado (tiempo asertivo >= ahora).', 1;

    BEGIN TRANSACTION;
    IF EXISTS (SELECT 1 FROM [core].[contract_profile] WITH (UPDLOCK, HOLDLOCK)
               WHERE [contract_id] = @p_contract_id AND [AssertedTo] > @a)
        THROW 53012, N'AVF: el contrato ya existe; use usp_bitemporal_update o usp_bitemporal_correction.', 1;

    INSERT INTO [core].[contract_profile] ([contract_id], [partner_id], [contract_number], [product_code], [branch_code],
        [currency_code], [gl_account_id], [origination_date], [maturity_date], [original_amount], [current_balance],
        [committed_limit], [undrawn_commitment], [annual_nominal_rate], [spread_rate], [repricing_frequency],
        [next_repricing_date], [payment_frequency_months], [amortization_type], [cmf_portfolio_type], [cmf_rating],
        [days_past_due], [collateral_value], [liquidity_treatment_code], [contract_status], [balance_maintained_in_core],
        [ValidFrom], [ValidTo], [AssertedFrom], [AssertedTo], [change_type], [change_reason], [source_journal_entry_id])
    SELECT @p_contract_id, j.[partner_id], j.[contract_number], j.[product_code], j.[branch_code], j.[currency_code],
           j.[gl_account_id], j.[origination_date], j.[maturity_date], j.[original_amount], j.[current_balance],
           ISNULL(j.[committed_limit], 0), ISNULL(j.[undrawn_commitment], 0), j.[annual_nominal_rate], ISNULL(j.[spread_rate], 0),
           j.[repricing_frequency], j.[next_repricing_date], j.[payment_frequency_months], j.[amortization_type],
           j.[cmf_portfolio_type], j.[cmf_rating], ISNULL(j.[days_past_due], 0), ISNULL(j.[collateral_value], 0),
           j.[liquidity_treatment_code], j.[contract_status], ISNULL(j.[balance_maintained_in_core], 1),
           @p_valid_from, '9999-12-31 23:59:59.99', @a, '9999-12-31 23:59:59.99',
           CASE WHEN @a > @now THEN 'FUTURE_ASSERTION' ELSE 'INSERT' END, @p_reason, NULL
    FROM OPENJSON(@p_values_json) WITH (
        [partner_id] BIGINT, [contract_number] VARCHAR(50), [product_code] VARCHAR(30), [branch_code] VARCHAR(20),
        [currency_code] CHAR(3), [gl_account_id] BIGINT, [origination_date] DATE, [maturity_date] DATE,
        [original_amount] DECIMAL(19,4), [current_balance] DECIMAL(19,4), [committed_limit] DECIMAL(19,4),
        [undrawn_commitment] DECIMAL(19,4), [annual_nominal_rate] DECIMAL(12,6), [spread_rate] DECIMAL(12,6),
        [repricing_frequency] VARCHAR(12), [next_repricing_date] DATE, [payment_frequency_months] INT,
        [amortization_type] VARCHAR(10), [cmf_portfolio_type] VARCHAR(3), [cmf_rating] VARCHAR(4), [days_past_due] INT,
        [collateral_value] DECIMAL(19,4), [liquidity_treatment_code] VARCHAR(40), [contract_status] VARCHAR(20),
        [balance_maintained_in_core] BIT) AS j;
    COMMIT TRANSACTION;
END;
GO

-- -----------------------------------------------------------------------------
-- 5. Núcleo AVF: retiro y re-aserción sobre el rango efectivo [@from, @to)
--    1. Cierra (AssertedTo = @a) las aserciones vigentes en @a que se solapan.
--    2. Re-aserta el remanente izquierdo y derecho con los valores previos
--       (CARRY_FORWARD) y el tramo cubierto con los valores modificados.
--    Las claves de @p_changes_json se validan contra la lista de columnas versionables.
-- -----------------------------------------------------------------------------
CREATE PROCEDURE [core].[usp_bitemporal_apply_change]
    @p_contract_id              BIGINT,
    @p_effective_from           DATETIME2(2),
    @p_effective_to             DATETIME2(2),
    @p_changes_json             NVARCHAR(MAX),
    @p_asserted_from            DATETIME2(2),
    @p_change_type              VARCHAR(20),
    @p_reason                   NVARCHAR(400),
    @p_source_journal_entry_id  BIGINT = NULL,
    @o_rows_withdrawn           INT OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    DECLARE @EOT DATETIME2(2) = '9999-12-31 23:59:59.99';

    IF ISJSON(@p_changes_json) <> 1 THROW 53020, N'AVF: @p_changes_json no es JSON válido.', 1;
    IF EXISTS (SELECT 1 FROM OPENJSON(@p_changes_json) WHERE [key] NOT IN (
        'partner_id','contract_number','product_code','branch_code','currency_code','gl_account_id','origination_date',
        'maturity_date','original_amount','current_balance','committed_limit','undrawn_commitment','annual_nominal_rate',
        'spread_rate','repricing_frequency','next_repricing_date','payment_frequency_months','amortization_type',
        'cmf_portfolio_type','cmf_rating','days_past_due','collateral_value','liquidity_treatment_code','contract_status',
        'balance_maintained_in_core'))
        THROW 53021, N'AVF: @p_changes_json contiene columnas no versionables.', 1;
    IF @p_effective_from >= @p_effective_to THROW 53022, N'AVF: rango efectivo vacío.', 1;

    BEGIN TRANSACTION;

    IF EXISTS (SELECT 1 FROM [core].[contract_profile] WITH (UPDLOCK, HOLDLOCK)
               WHERE [contract_id] = @p_contract_id AND [AssertedFrom] > SYSUTCDATETIME())
        THROW 53023, N'AVF: el contrato tiene una aserción diferida pendiente; aplíquela o retírela antes de un nuevo cambio.', 1;

    SELECT [contract_id], [partner_id], [contract_number], [product_code], [branch_code], [currency_code], [gl_account_id],
           [origination_date], [maturity_date], [original_amount], [current_balance], [committed_limit], [undrawn_commitment],
           [annual_nominal_rate], [spread_rate], [repricing_frequency], [next_repricing_date], [payment_frequency_months],
           [amortization_type], [cmf_portfolio_type], [cmf_rating], [days_past_due], [collateral_value],
           [liquidity_treatment_code], [contract_status], [balance_maintained_in_core], [ValidFrom], [ValidTo], [AssertedFrom]
    INTO #affected
    FROM [core].[contract_profile] WITH (UPDLOCK, HOLDLOCK)
    WHERE [contract_id] = @p_contract_id
      AND [AssertedFrom] <= @p_asserted_from AND [AssertedTo] > @p_asserted_from
      AND [ValidFrom] < @p_effective_to AND [ValidTo] > @p_effective_from;

    SET @o_rows_withdrawn = @@ROWCOUNT;
    IF @o_rows_withdrawn = 0
        THROW 53024, N'AVF: no existe una versión vigente del contrato en el rango efectivo indicado.', 1;

    -- 1) Retiro de las aserciones afectadas (la imagen previa queda en la tabla de historia).
    UPDATE c SET c.[AssertedTo] = @p_asserted_from
    FROM [core].[contract_profile] c
    JOIN #affected a ON a.[contract_id] = c.[contract_id] AND a.[ValidFrom] = c.[ValidFrom] AND a.[AssertedFrom] = c.[AssertedFrom];

    -- 2a) Remanente izquierdo con los valores previos.
    INSERT INTO [core].[contract_profile] ([contract_id], [partner_id], [contract_number], [product_code], [branch_code],
        [currency_code], [gl_account_id], [origination_date], [maturity_date], [original_amount], [current_balance],
        [committed_limit], [undrawn_commitment], [annual_nominal_rate], [spread_rate], [repricing_frequency],
        [next_repricing_date], [payment_frequency_months], [amortization_type], [cmf_portfolio_type], [cmf_rating],
        [days_past_due], [collateral_value], [liquidity_treatment_code], [contract_status], [balance_maintained_in_core],
        [ValidFrom], [ValidTo], [AssertedFrom], [AssertedTo], [change_type], [change_reason], [source_journal_entry_id])
    SELECT [contract_id], [partner_id], [contract_number], [product_code], [branch_code], [currency_code], [gl_account_id],
           [origination_date], [maturity_date], [original_amount], [current_balance], [committed_limit], [undrawn_commitment],
           [annual_nominal_rate], [spread_rate], [repricing_frequency], [next_repricing_date], [payment_frequency_months],
           [amortization_type], [cmf_portfolio_type], [cmf_rating], [days_past_due], [collateral_value],
           [liquidity_treatment_code], [contract_status], [balance_maintained_in_core],
           [ValidFrom], @p_effective_from, @p_asserted_from, @EOT, 'CARRY_FORWARD',
           CONCAT(N'Remanente previo a ', CONVERT(CHAR(10), @p_effective_from, 120)), NULL
    FROM #affected WHERE [ValidFrom] < @p_effective_from;

    -- 2b) Tramo cubierto con los valores modificados.
    INSERT INTO [core].[contract_profile] ([contract_id], [partner_id], [contract_number], [product_code], [branch_code],
        [currency_code], [gl_account_id], [origination_date], [maturity_date], [original_amount], [current_balance],
        [committed_limit], [undrawn_commitment], [annual_nominal_rate], [spread_rate], [repricing_frequency],
        [next_repricing_date], [payment_frequency_months], [amortization_type], [cmf_portfolio_type], [cmf_rating],
        [days_past_due], [collateral_value], [liquidity_treatment_code], [contract_status], [balance_maintained_in_core],
        [ValidFrom], [ValidTo], [AssertedFrom], [AssertedTo], [change_type], [change_reason], [source_journal_entry_id])
    SELECT a.[contract_id],
           COALESCE(TRY_CONVERT(BIGINT,        JSON_VALUE(@p_changes_json, '$.partner_id')), a.[partner_id]),
           COALESCE(TRY_CONVERT(VARCHAR(50),   JSON_VALUE(@p_changes_json, '$.contract_number')), a.[contract_number]),
           COALESCE(TRY_CONVERT(VARCHAR(30),   JSON_VALUE(@p_changes_json, '$.product_code')), a.[product_code]),
           COALESCE(TRY_CONVERT(VARCHAR(20),   JSON_VALUE(@p_changes_json, '$.branch_code')), a.[branch_code]),
           COALESCE(TRY_CONVERT(CHAR(3),       JSON_VALUE(@p_changes_json, '$.currency_code')), a.[currency_code]),
           COALESCE(TRY_CONVERT(BIGINT,        JSON_VALUE(@p_changes_json, '$.gl_account_id')), a.[gl_account_id]),
           COALESCE(TRY_CONVERT(DATE,          JSON_VALUE(@p_changes_json, '$.origination_date')), a.[origination_date]),
           COALESCE(TRY_CONVERT(DATE,          JSON_VALUE(@p_changes_json, '$.maturity_date')), a.[maturity_date]),
           COALESCE(TRY_CONVERT(DECIMAL(19,4), JSON_VALUE(@p_changes_json, '$.original_amount')), a.[original_amount]),
           COALESCE(TRY_CONVERT(DECIMAL(19,4), JSON_VALUE(@p_changes_json, '$.current_balance')), a.[current_balance]),
           COALESCE(TRY_CONVERT(DECIMAL(19,4), JSON_VALUE(@p_changes_json, '$.committed_limit')), a.[committed_limit]),
           COALESCE(TRY_CONVERT(DECIMAL(19,4), JSON_VALUE(@p_changes_json, '$.undrawn_commitment')), a.[undrawn_commitment]),
           COALESCE(TRY_CONVERT(DECIMAL(12,6), JSON_VALUE(@p_changes_json, '$.annual_nominal_rate')), a.[annual_nominal_rate]),
           COALESCE(TRY_CONVERT(DECIMAL(12,6), JSON_VALUE(@p_changes_json, '$.spread_rate')), a.[spread_rate]),
           COALESCE(TRY_CONVERT(VARCHAR(12),   JSON_VALUE(@p_changes_json, '$.repricing_frequency')), a.[repricing_frequency]),
           COALESCE(TRY_CONVERT(DATE,          JSON_VALUE(@p_changes_json, '$.next_repricing_date')), a.[next_repricing_date]),
           COALESCE(TRY_CONVERT(INT,           JSON_VALUE(@p_changes_json, '$.payment_frequency_months')), a.[payment_frequency_months]),
           COALESCE(TRY_CONVERT(VARCHAR(10),   JSON_VALUE(@p_changes_json, '$.amortization_type')), a.[amortization_type]),
           COALESCE(TRY_CONVERT(VARCHAR(3),    JSON_VALUE(@p_changes_json, '$.cmf_portfolio_type')), a.[cmf_portfolio_type]),
           COALESCE(TRY_CONVERT(VARCHAR(4),    JSON_VALUE(@p_changes_json, '$.cmf_rating')), a.[cmf_rating]),
           COALESCE(TRY_CONVERT(INT,           JSON_VALUE(@p_changes_json, '$.days_past_due')), a.[days_past_due]),
           COALESCE(TRY_CONVERT(DECIMAL(19,4), JSON_VALUE(@p_changes_json, '$.collateral_value')), a.[collateral_value]),
           COALESCE(TRY_CONVERT(VARCHAR(40),   JSON_VALUE(@p_changes_json, '$.liquidity_treatment_code')), a.[liquidity_treatment_code]),
           COALESCE(TRY_CONVERT(VARCHAR(20),   JSON_VALUE(@p_changes_json, '$.contract_status')), a.[contract_status]),
           COALESCE(TRY_CONVERT(BIT,           JSON_VALUE(@p_changes_json, '$.balance_maintained_in_core')), a.[balance_maintained_in_core]),
           CASE WHEN a.[ValidFrom] > @p_effective_from THEN a.[ValidFrom] ELSE @p_effective_from END,
           CASE WHEN a.[ValidTo]   < @p_effective_to   THEN a.[ValidTo]   ELSE @p_effective_to   END,
           @p_asserted_from, @EOT, @p_change_type, @p_reason, @p_source_journal_entry_id
    FROM #affected a;

    -- 2c) Remanente derecho con los valores previos.
    INSERT INTO [core].[contract_profile] ([contract_id], [partner_id], [contract_number], [product_code], [branch_code],
        [currency_code], [gl_account_id], [origination_date], [maturity_date], [original_amount], [current_balance],
        [committed_limit], [undrawn_commitment], [annual_nominal_rate], [spread_rate], [repricing_frequency],
        [next_repricing_date], [payment_frequency_months], [amortization_type], [cmf_portfolio_type], [cmf_rating],
        [days_past_due], [collateral_value], [liquidity_treatment_code], [contract_status], [balance_maintained_in_core],
        [ValidFrom], [ValidTo], [AssertedFrom], [AssertedTo], [change_type], [change_reason], [source_journal_entry_id])
    SELECT [contract_id], [partner_id], [contract_number], [product_code], [branch_code], [currency_code], [gl_account_id],
           [origination_date], [maturity_date], [original_amount], [current_balance], [committed_limit], [undrawn_commitment],
           [annual_nominal_rate], [spread_rate], [repricing_frequency], [next_repricing_date], [payment_frequency_months],
           [amortization_type], [cmf_portfolio_type], [cmf_rating], [days_past_due], [collateral_value],
           [liquidity_treatment_code], [contract_status], [balance_maintained_in_core],
           @p_effective_to, [ValidTo], @p_asserted_from, @EOT, 'CARRY_FORWARD',
           CONCAT(N'Remanente posterior a ', CONVERT(CHAR(10), @p_effective_to, 120)), NULL
    FROM #affected WHERE [ValidTo] > @p_effective_to;

    COMMIT TRANSACTION;
END;
GO

-- -----------------------------------------------------------------------------
-- 6. Wrappers con la semántica de cada operación AVF
-- -----------------------------------------------------------------------------
CREATE PROCEDURE [core].[usp_bitemporal_update]
    @p_contract_id     BIGINT,
    @p_effective_from  DATE,
    @p_changes_json    NVARCHAR(MAX),
    @p_reason          NVARCHAR(400),
    @p_effective_to    DATETIME2(2) = '9999-12-31 23:59:59.99'
AS
BEGIN
    SET NOCOUNT ON;
    -- Un cambio real del negocio rige desde hoy o en el futuro: lo retroactivo es una corrección.
    DECLARE @today_local DATE = CAST((SYSUTCDATETIME() AT TIME ZONE 'UTC') AT TIME ZONE 'Pacific SA Standard Time' AS DATE);
    IF @p_effective_from < @today_local
        THROW 53030, N'AVF: un update no puede ser retroactivo; use core.usp_bitemporal_correction.', 1;
    DECLARE @a DATETIME2(2) = SYSUTCDATETIME(), @n INT, @from DATETIME2(2) = CAST(@p_effective_from AS DATETIME2(2));
    EXEC [core].[usp_bitemporal_apply_change] @p_contract_id, @from, @p_effective_to, @p_changes_json, @a,
         'UPDATE', @p_reason, NULL, @n OUTPUT;
END;
GO

CREATE PROCEDURE [core].[usp_bitemporal_correction]
    @p_contract_id              BIGINT,
    @p_effective_from           DATE,
    @p_changes_json             NVARCHAR(MAX),
    @p_reason                   NVARCHAR(400),
    @p_source_journal_entry_id  BIGINT,
    @p_effective_to             DATETIME2(2) = '9999-12-31 23:59:59.99'
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @today_local DATE = CAST((SYSUTCDATETIME() AT TIME ZONE 'UTC') AT TIME ZONE 'Pacific SA Standard Time' AS DATE);
    IF @p_effective_from >= @today_local
        THROW 53031, N'AVF: una corrección debe ser retroactiva; use core.usp_bitemporal_update.', 1;
    IF NOT EXISTS (SELECT 1 FROM [oltp].[journal_entry] WHERE [journal_entry_id] = @p_source_journal_entry_id)
        THROW 53032, N'AVF: la corrección debe referenciar el asiento de ajuste del ledger que la respalda.', 1;
    DECLARE @a DATETIME2(2) = SYSUTCDATETIME(), @n INT, @from DATETIME2(2) = CAST(@p_effective_from AS DATETIME2(2));
    EXEC [core].[usp_bitemporal_apply_change] @p_contract_id, @from, @p_effective_to, @p_changes_json, @a,
         'CORRECTION', @p_reason, @p_source_journal_entry_id, @n OUTPUT;
END;
GO

CREATE PROCEDURE [core].[usp_future_assertion]
    @p_contract_id     BIGINT,
    @p_effective_from  DATETIME2(2),
    @p_changes_json    NVARCHAR(MAX),
    @p_cutover_utc     DATETIME2(2),
    @p_reason          NVARCHAR(400),
    @p_effective_to    DATETIME2(2) = '9999-12-31 23:59:59.99'
AS
BEGIN
    SET NOCOUNT ON;
    IF @p_cutover_utc <= SYSUTCDATETIME()
        THROW 53033, N'AVF: el corte de una aserción futura debe estar en el futuro.', 1;
    DECLARE @n INT;
    -- AssertedTo de la versión vigente queda en @p_cutover_utc (futuro): hasta ese instante
    -- toda consulta "as of now" sigue viendo el estado anterior; después, el nuevo.
    EXEC [core].[usp_bitemporal_apply_change] @p_contract_id, @p_effective_from, @p_effective_to, @p_changes_json,
         @p_cutover_utc, 'FUTURE_ASSERTION', @p_reason, NULL, @n OUTPUT;
END;
GO

-- -----------------------------------------------------------------------------
-- 7. Sincronización incremental desde el aterrizaje CDC (operación diaria).
--    Contratos nuevos -> insert; checksum distinto -> update efectivo desde hoy.
--    Para cargas masivas iniciales use core.usp_migrate_legacy_portfolio_future_assertion (script 06).
-- -----------------------------------------------------------------------------
CREATE PROCEDURE [core].[usp_sync_contracts_from_staging]
    @p_batch_id BIGINT
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    DECLARE @now_utc DATETIME2(2) = SYSUTCDATETIME();
    DECLARE @today DATE = CAST((SYSUTCDATETIME() AT TIME ZONE 'UTC') AT TIME ZONE 'Pacific SA Standard Time' AS DATE);
    DECLARE @contract_id BIGINT, @json NVARCHAR(MAX), @valid_from DATETIME2(2), @is_new BIT, @loaded INT = 0;

    DECLARE c CURSOR LOCAL FAST_FORWARD FOR
        SELECT l.[contract_id],
               (SELECT l.[partner_id], l.[contract_number], m.[product_code], l.[branch_code], l.[currency_code],
                       m.[gl_account_id], l.[origination_date], l.[maturity_date], l.[original_amount], l.[current_balance],
                       l.[committed_limit], CASE WHEN l.[committed_limit] > 0 THEN l.[committed_limit] - l.[current_balance] ELSE 0 END AS [undrawn_commitment],
                       l.[annual_nominal_rate], l.[spread_rate], l.[repricing_frequency], m.[payment_frequency_months],
                       m.[amortization_type], m.[cmf_portfolio_type], l.[cmf_rating], l.[days_past_due],
                       m.[liquidity_treatment_code], l.[contract_status]
                FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),
               l.[valid_from],
               CASE WHEN cur.[contract_id] IS NULL THEN 1 ELSE 0 END
        FROM [stg].[cdc_landing_contract] l
        JOIN [stg].[map_legacy_product] m ON m.[legacy_product_code] = l.[legacy_product_code]
        OUTER APPLY (SELECT TOP (1) p.[contract_id], p.[partner_id], p.[contract_number], p.[currency_code], p.[maturity_date],
                            p.[current_balance], p.[committed_limit], p.[annual_nominal_rate], p.[spread_rate],
                            p.[cmf_rating], p.[days_past_due], p.[contract_status]
                     FROM [core].[itvf_contract_as_of](CAST(@today AS DATETIME2(2)), @now_utc) p
                     WHERE p.[contract_id] = l.[contract_id]) cur
        OUTER APPLY [stg].[itvf_contract_row_checksum](cur.[partner_id], cur.[contract_number], l.[legacy_product_code],
                        cur.[currency_code], cur.[maturity_date], cur.[current_balance], cur.[committed_limit],
                        cur.[annual_nominal_rate], cur.[spread_rate], cur.[cmf_rating], cur.[days_past_due],
                        cur.[contract_status]) ck
        WHERE l.[batch_id] = @p_batch_id AND l.[is_processed] = 0 AND l.[cdc_operation] IN (2, 4)
          AND (cur.[contract_id] IS NULL OR ck.[row_checksum] <> l.[row_checksum])
        ORDER BY l.[contract_id];

    OPEN c;
    FETCH NEXT FROM c INTO @contract_id, @json, @valid_from, @is_new;
    WHILE @@FETCH_STATUS = 0
    BEGIN
        IF @is_new = 1
            EXEC [core].[usp_bitemporal_insert] @contract_id, @json, @valid_from, NULL, N'Alta desde CDC legado';
        ELSE
            EXEC [core].[usp_bitemporal_update] @contract_id, @today, @json, N'Cambio detectado por checksum CDC';
        SET @loaded += 1;
        FETCH NEXT FROM c INTO @contract_id, @json, @valid_from, @is_new;
    END;
    CLOSE c;
    DEALLOCATE c;

    UPDATE [stg].[cdc_landing_contract] SET [is_processed] = 1 WHERE [batch_id] = @p_batch_id;
    EXEC [stg].[usp_log_batch_end] @p_batch_id, 'SUCCESS', @p_loaded = @loaded;
END;
GO

GRANT SELECT ON [core].[itvf_contract_as_of]           TO [app_report_reader];
GRANT SELECT ON [core].[vw_contract_profile_current]   TO [app_report_reader];
GRANT EXECUTE ON SCHEMA::[core]                        TO [app_etl_operator];
GO

-- -----------------------------------------------------------------------------
-- 8. Ejemplo: Caso Real 1 (corrección retroactiva ligada al asiento de ajuste)
-- -----------------------------------------------------------------------------
/*
EXEC [core].[usp_bitemporal_correction]
     @p_contract_id = 90210, @p_effective_from = '2024-12-28',
     @p_changes_json = N'{"current_balance": 42000000.0000}',
     @p_reason = N'Abono 28-12-2024 retenido en cámara aplicado retroactivamente',
     @p_source_journal_entry_id = 58;

-- Lo informado a la CMF (10-01-2025) vs lo conocido hoy, para el cierre del 31-12-2024:
SELECT 'informado' AS vista, current_balance FROM core.itvf_contract_as_of('2024-12-31 23:59:59.99', '2025-01-10 12:00') WHERE contract_id = 90210
UNION ALL
SELECT 'vigente', current_balance FROM core.itvf_contract_as_of('2024-12-31 23:59:59.99', SYSUTCDATETIME()) WHERE contract_id = 90210;

-- Huella física del retiro (system time nativo):
SELECT * FROM core.contract_profile FOR SYSTEM_TIME ALL WHERE contract_id = 90210 ORDER BY SysStartTime;
*/
