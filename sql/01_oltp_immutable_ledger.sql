-- =============================================================================
-- 01_oltp_immutable_ledger.sql
-- =============================================================================
-- Capa 1: Data plane contable (ledger inmutable de partida doble).
--
-- Principios (TigerBeetle / Formance / Crassula / Helland):
--   1. Transacciones NO interactivas: la aplicación envía el asiento completo en
--      un Table-Valued Parameter; validación, saldos, hash, idempotencia y outbox
--      ocurren en una sola transacción dentro del motor. Ningún lock sobrevive a
--      un viaje de red, lo que acota la contención sobre cuentas calientes (p. ej.
--      la cuenta LBTR en el BCCh) al tiempo de CPU del procedimiento.
--   2. Montos DECIMAL(19,4), factores DECIMAL(12,6). Prohibido FLOAT/REAL.
--   3. Dos fases: reservas PENDING (debits_pending / credits_pending) que se
--      resuelven con POST (total o parcial), VOID o EXPIRE mediante un nuevo
--      asiento que referencia la reserva; nunca con UPDATE del original.
--   4. Idempotencia atómica: oltp.idempotency_records se escribe en la MISMA
--      transacción que el asiento. Reintento con mismo payload -> respuesta
--      original; mismo key con otro payload -> rechazo. Los rechazos de negocio
--      también se registran (FAILED) para que el reintento sea determinista.
--   5. Transactional Outbox insert-only en la misma transacción (sin 2PC ni
--      doble escritura); lo publica CDC (script 06).
--   6. Inmutabilidad: DENY a public + triggers INSTEAD OF UPDATE/DELETE +
--      cadena SHA-256 verificable (detecta incluso manipulación por sysadmin).
-- =============================================================================

USE [CoreBanking];
GO

-- -----------------------------------------------------------------------------
-- 0. Limpieza en orden de dependencias (sólo para despliegues desde cero)
-- -----------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS [oltp].[usp_verify_ledger_integrity];
DROP PROCEDURE IF EXISTS [oltp].[usp_reverse_journal_entry];
DROP PROCEDURE IF EXISTS [oltp].[usp_expire_pending_entries];
DROP PROCEDURE IF EXISTS [oltp].[usp_resolve_pending_entry];
DROP PROCEDURE IF EXISTS [oltp].[usp_post_atomic_journal_entry];
DROP PROCEDURE IF EXISTS [oltp].[usp_record_idempotent_failure];
DROP PROCEDURE IF EXISTS [oltp].[usp_purge_expired_idempotency_keys];
DROP FUNCTION  IF EXISTS [oltp].[fn_entry_hash];
DROP FUNCTION  IF EXISTS [oltp].[fn_row_hash];
DROP FUNCTION  IF EXISTS [oltp].[fn_local_cutoff_date];
DROP TABLE IF EXISTS [oltp].[transactional_outbox];
DROP TABLE IF EXISTS [oltp].[idempotency_records];
DROP TABLE IF EXISTS [oltp].[ledger_posting];
DROP TABLE IF EXISTS [oltp].[journal_entry];
DROP TABLE IF EXISTS [oltp].[ledger_chain_tip];
DROP TABLE IF EXISTS [oltp].[account_balance];
DROP TABLE IF EXISTS [oltp].[ledger_account];
DROP TYPE  IF EXISTS [oltp].[tt_journal_line];
DROP TYPE  IF EXISTS [oltp].[tt_post_amount];
GO

-- -----------------------------------------------------------------------------
-- 1. Plan de cuentas con banderas de límite (semántica TigerBeetle)
--    debits_must_not_exceed_credits : pasivos de clientes (sin sobregiro)
--    credits_must_not_exceed_debits : activos propios (p. ej. reserva LBTR >= 0)
-- -----------------------------------------------------------------------------
CREATE TABLE [oltp].[ledger_account] (
    [account_id]                      BIGINT        NOT NULL,
    [account_code]                    VARCHAR(20)   NOT NULL,
    [account_name]                    NVARCHAR(150) NOT NULL,
    [account_class]                   VARCHAR(10)   NOT NULL,
    [normal_balance]                  CHAR(1)       NOT NULL,
    [currency_code]                   CHAR(3)       NOT NULL,
    [ledger_id]                       INT           NOT NULL CONSTRAINT [DF_ledger_account_ledger] DEFAULT (1),
    [debits_must_not_exceed_credits]  BIT           NOT NULL CONSTRAINT [DF_ledger_account_dmnec] DEFAULT (0),
    [credits_must_not_exceed_debits]  BIT           NOT NULL CONSTRAINT [DF_ledger_account_cmned] DEFAULT (0),
    [is_contract_principal]           BIT           NOT NULL CONSTRAINT [DF_ledger_account_principal] DEFAULT (0),
    [created_at_utc]                  DATETIME2(7)  NOT NULL CONSTRAINT [DF_ledger_account_created] DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT [PK_ledger_account] PRIMARY KEY CLUSTERED ([account_id]),
    CONSTRAINT [UQ_ledger_account_code] UNIQUE ([account_code]),
    CONSTRAINT [CK_ledger_account_class] CHECK ([account_class] IN ('ASSET','LIABILITY','EQUITY','INCOME','EXPENSE','CLEARING')),
    CONSTRAINT [CK_ledger_account_normal] CHECK ([normal_balance] IN ('D','C')),
    CONSTRAINT [CK_ledger_account_flags] CHECK (NOT ([debits_must_not_exceed_credits] = 1 AND [credits_must_not_exceed_debits] = 1))
);
GO

-- -----------------------------------------------------------------------------
-- 2. Saldos por (cuenta, contrato): pending vs posted.
--    Tabla separada del diario append-only: el diario crece por inserción pura y
--    la contención se concentra en una fila corta por (cuenta, contrato).
-- -----------------------------------------------------------------------------
CREATE TABLE [oltp].[account_balance] (
    [account_id]             BIGINT         NOT NULL,
    [contract_id]            BIGINT         NOT NULL,   -- 0 = sin contrato
    [debits_pending]         DECIMAL(19,4)  NOT NULL CONSTRAINT [DF_balance_dp] DEFAULT (0),
    [credits_pending]        DECIMAL(19,4)  NOT NULL CONSTRAINT [DF_balance_cp] DEFAULT (0),
    [debits_posted]          DECIMAL(19,4)  NOT NULL CONSTRAINT [DF_balance_dpo] DEFAULT (0),
    [credits_posted]         DECIMAL(19,4)  NOT NULL CONSTRAINT [DF_balance_cpo] DEFAULT (0),
    [last_journal_entry_id]  BIGINT         NULL,
    [updated_at_utc]         DATETIME2(7)   NOT NULL,
    CONSTRAINT [PK_account_balance] PRIMARY KEY CLUSTERED ([account_id], [contract_id]),
    CONSTRAINT [FK_account_balance_account] FOREIGN KEY ([account_id]) REFERENCES [oltp].[ledger_account] ([account_id]),
    CONSTRAINT [CK_account_balance_non_negative] CHECK (
        [debits_pending] >= 0 AND [credits_pending] >= 0 AND [debits_posted] >= 0 AND [credits_posted] >= 0)
);
GO

-- Saldo contable y disponible derivados (nunca almacenados dos veces).
CREATE OR ALTER VIEW [oltp].[vw_account_available_balance]
AS
SELECT b.[account_id], b.[contract_id], a.[account_name], a.[normal_balance],
       b.[debits_pending], b.[credits_pending], b.[debits_posted], b.[credits_posted],
       CASE a.[normal_balance] WHEN 'D' THEN b.[debits_posted] - b.[credits_posted]
                               ELSE b.[credits_posted] - b.[debits_posted] END AS [current_balance],
       CASE a.[normal_balance] WHEN 'D' THEN b.[debits_posted] - b.[credits_posted] - b.[credits_pending]
                               ELSE b.[credits_posted] - b.[debits_posted] - b.[debits_pending] END AS [available_balance]
FROM [oltp].[account_balance] b
JOIN [oltp].[ledger_account] a ON a.[account_id] = b.[account_id];
GO

-- -----------------------------------------------------------------------------
-- 3. Punta de la cadena hash por ledger. Es la única sección serializada del
--    sistema; se toma al final del procedimiento y se libera en el COMMIT, de modo
--    que su duración es de microsegundos (no hay viajes de red con el lock tomado).
-- -----------------------------------------------------------------------------
CREATE TABLE [oltp].[ledger_chain_tip] (
    [ledger_id]         INT          NOT NULL,
    [last_entry_id]     BIGINT       NULL,
    [last_entry_hash]   BINARY(32)   NOT NULL,
    [updated_at_utc]    DATETIME2(7) NOT NULL,
    CONSTRAINT [PK_ledger_chain_tip] PRIMARY KEY CLUSTERED ([ledger_id])
);
INSERT INTO [oltp].[ledger_chain_tip] ([ledger_id], [last_entry_id], [last_entry_hash], [updated_at_utc])
VALUES (1, NULL, 0x0000000000000000000000000000000000000000000000000000000000000000, SYSUTCDATETIME());
GO

-- -----------------------------------------------------------------------------
-- 4. Cabecera del diario
-- -----------------------------------------------------------------------------
CREATE TABLE [oltp].[journal_entry] (
    [journal_entry_id]            BIGINT IDENTITY(1,1) NOT NULL,
    [entry_uuid]                  UNIQUEIDENTIFIER NOT NULL,
    [idempotency_key]             UNIQUEIDENTIFIER NOT NULL,
    [ledger_id]                   INT              NOT NULL,
    [entry_kind]                  VARCHAR(16)      NOT NULL,
    [pending_entry_id]            BIGINT           NULL,
    [pending_expires_at_utc]      DATETIME2(7)     NULL,
    [transaction_timestamp_utc]   DATETIME2(7)     NOT NULL,   -- simultaneidad global (Kimball cap. 11)
    [transaction_timestamp_local] DATETIME2(7)     NOT NULL,   -- hora local Chile
    [local_cutoff_date]           DATE             NOT NULL,   -- fecha contable con corte 18:00
    [posting_date]                DATE             NOT NULL,
    [value_date]                  DATE             NOT NULL,   -- tiempo efectivo económico
    [source_system]               VARCHAR(30)      NOT NULL,
    [transaction_code]            VARCHAR(40)      NOT NULL,
    [description]                 NVARCHAR(255)    NOT NULL,
    [total_debit]                 DECIMAL(19,4)    NOT NULL,
    [total_credit]                DECIMAL(19,4)    NOT NULL,
    [line_count]                  INT              NOT NULL,
    [reversal_of_entry_id]        BIGINT           NULL,
    [prev_entry_hash]             BINARY(32)       NOT NULL,
    [entry_hash]                  BINARY(32)       NOT NULL,
    [created_by]                  SYSNAME          NOT NULL CONSTRAINT [DF_journal_entry_user] DEFAULT (ORIGINAL_LOGIN()),
    CONSTRAINT [PK_journal_entry] PRIMARY KEY CLUSTERED ([journal_entry_id]),
    CONSTRAINT [UQ_journal_entry_uuid] UNIQUE ([entry_uuid]),
    CONSTRAINT [UQ_journal_entry_idempotency] UNIQUE ([idempotency_key]),
    CONSTRAINT [UQ_journal_entry_hash] UNIQUE ([entry_hash]),
    CONSTRAINT [FK_journal_entry_pending] FOREIGN KEY ([pending_entry_id]) REFERENCES [oltp].[journal_entry] ([journal_entry_id]),
    CONSTRAINT [FK_journal_entry_reversal] FOREIGN KEY ([reversal_of_entry_id]) REFERENCES [oltp].[journal_entry] ([journal_entry_id]),
    CONSTRAINT [CK_journal_entry_kind] CHECK ([entry_kind] IN ('POSTED','PENDING','POST_PENDING','VOID_PENDING','EXPIRE_PENDING')),
    CONSTRAINT [CK_journal_entry_balanced] CHECK ([total_debit] = [total_credit]),
    CONSTRAINT [CK_journal_entry_amount] CHECK ([entry_kind] IN ('VOID_PENDING','EXPIRE_PENDING') OR [total_debit] > 0),
    CONSTRAINT [CK_journal_entry_resolution] CHECK (
        (CASE WHEN [entry_kind] IN ('POST_PENDING','VOID_PENDING','EXPIRE_PENDING') THEN 1 ELSE 0 END)
      = (CASE WHEN [pending_entry_id] IS NOT NULL THEN 1 ELSE 0 END)),
    CONSTRAINT [CK_journal_entry_timeout] CHECK (
        (CASE WHEN [entry_kind] = 'PENDING' THEN 1 ELSE 0 END)
      = (CASE WHEN [pending_expires_at_utc] IS NOT NULL THEN 1 ELSE 0 END)),
    CONSTRAINT [CK_journal_entry_dates] CHECK ([value_date] <= [posting_date] AND [posting_date] = [local_cutoff_date]),
    CONSTRAINT [CK_journal_entry_lines] CHECK ([line_count] >= 2)
);
GO

-- Una reserva se resuelve una sola vez (POST, VOID o EXPIRE): índice único filtrado.
CREATE UNIQUE NONCLUSTERED INDEX [UX_journal_entry_single_resolution]
    ON [oltp].[journal_entry] ([pending_entry_id]) WHERE [pending_entry_id] IS NOT NULL;
CREATE NONCLUSTERED INDEX [IX_journal_entry_pending_open]
    ON [oltp].[journal_entry] ([pending_expires_at_utc]) INCLUDE ([entry_kind]) WHERE [entry_kind] = 'PENDING';
CREATE NONCLUSTERED INDEX [IX_journal_entry_posting_date]
    ON [oltp].[journal_entry] ([posting_date], [journal_entry_id]) INCLUDE ([value_date], [transaction_timestamp_utc]);
GO

-- -----------------------------------------------------------------------------
-- 5. Detalle multi-pata
-- -----------------------------------------------------------------------------
CREATE TABLE [oltp].[ledger_posting] (
    [posting_id]                 BIGINT IDENTITY(1,1) NOT NULL,
    [journal_entry_id]           BIGINT        NOT NULL,
    [line_number]                INT           NOT NULL,
    [account_id]                 BIGINT        NOT NULL,
    [contract_id]                BIGINT        NOT NULL,
    [partner_id]                 BIGINT        NOT NULL,
    [currency_code]              CHAR(3)       NOT NULL,
    [posting_status]             VARCHAR(10)   NOT NULL,
    [amount_debit]               DECIMAL(19,4) NOT NULL,
    [amount_credit]              DECIMAL(19,4) NOT NULL,
    [functional_debit]           DECIMAL(19,4) NOT NULL,
    [functional_credit]          DECIMAL(19,4) NOT NULL,
    [functional_released_debit]  DECIMAL(19,4) NOT NULL CONSTRAINT [DF_posting_rel_d] DEFAULT (0),
    [functional_released_credit] DECIMAL(19,4) NOT NULL CONSTRAINT [DF_posting_rel_c] DEFAULT (0),
    [fx_rate]                    DECIMAL(12,6) NOT NULL,
    [row_hash]                   BINARY(32)    NOT NULL,
    CONSTRAINT [PK_ledger_posting] PRIMARY KEY CLUSTERED ([posting_id]),
    CONSTRAINT [UQ_ledger_posting_line] UNIQUE ([journal_entry_id], [line_number]),
    CONSTRAINT [FK_ledger_posting_entry] FOREIGN KEY ([journal_entry_id]) REFERENCES [oltp].[journal_entry] ([journal_entry_id]),
    CONSTRAINT [FK_ledger_posting_account] FOREIGN KEY ([account_id]) REFERENCES [oltp].[ledger_account] ([account_id]),
    CONSTRAINT [CK_ledger_posting_status] CHECK ([posting_status] IN ('PENDING','POSTED','RELEASED')),
    CONSTRAINT [CK_ledger_posting_non_negative] CHECK (
        [amount_debit] >= 0 AND [amount_credit] >= 0 AND [functional_debit] >= 0 AND [functional_credit] >= 0
        AND [functional_released_debit] >= 0 AND [functional_released_credit] >= 0),
    CONSTRAINT [CK_ledger_posting_one_side] CHECK (NOT ([amount_debit] > 0 AND [amount_credit] > 0)),
    CONSTRAINT [CK_ledger_posting_release_side] CHECK (NOT ([functional_released_debit] > 0 AND [functional_released_credit] > 0)),
    CONSTRAINT [CK_ledger_posting_amount] CHECK ([posting_status] = 'RELEASED' OR [amount_debit] + [amount_credit] > 0),
    CONSTRAINT [CK_ledger_posting_fx] CHECK ([fx_rate] > 0)
);
GO

CREATE NONCLUSTERED INDEX [IX_ledger_posting_account_contract]
    ON [oltp].[ledger_posting] ([account_id], [contract_id], [journal_entry_id])
    INCLUDE ([posting_status], [functional_debit], [functional_credit], [functional_released_debit], [functional_released_credit]);
GO

-- -----------------------------------------------------------------------------
-- 6. Idempotencia (cold path en la base primaria; el hot path de caché vive en
--    la capa de servicio y siempre se valida contra esta tabla).
-- -----------------------------------------------------------------------------
CREATE TABLE [oltp].[idempotency_records] (
    [idempotency_key]       UNIQUEIDENTIFIER NOT NULL,
    [request_payload_hash]  BINARY(32)       NOT NULL,
    [operation]             VARCHAR(30)      NOT NULL,
    [status]                VARCHAR(10)      NOT NULL,
    [journal_entry_id]      BIGINT           NULL,
    [response_json]         NVARCHAR(4000)   NOT NULL,
    [created_at_utc]        DATETIME2(7)     NOT NULL,
    [expires_at_utc]        DATETIME2(7)     NOT NULL,
    CONSTRAINT [PK_idempotency_records] PRIMARY KEY CLUSTERED ([idempotency_key]),
    CONSTRAINT [FK_idempotency_entry] FOREIGN KEY ([journal_entry_id]) REFERENCES [oltp].[journal_entry] ([journal_entry_id]),
    -- PENDING queda reservado para sagas de larga duración que reservan la clave
    -- antes de orquestar pasos externos; el procedimiento atómico escribe POSTED/FAILED.
    CONSTRAINT [CK_idempotency_status] CHECK ([status] IN ('PENDING','POSTED','FAILED')),
    CONSTRAINT [CK_idempotency_json] CHECK (ISJSON([response_json]) = 1)
);
GO

-- -----------------------------------------------------------------------------
-- 7. Transactional Outbox (insert-only; CDC lo publica, script 06)
-- -----------------------------------------------------------------------------
CREATE TABLE [oltp].[transactional_outbox] (
    [outbox_id]         BIGINT IDENTITY(1,1) NOT NULL,
    [event_uuid]        UNIQUEIDENTIFIER NOT NULL CONSTRAINT [DF_outbox_uuid] DEFAULT (NEWSEQUENTIALID()),
    [aggregate_type]    VARCHAR(40)      NOT NULL,
    [aggregate_id]      VARCHAR(64)      NOT NULL,
    [event_type]        VARCHAR(60)      NOT NULL,
    [payload_json]      NVARCHAR(MAX)    NOT NULL,
    [journal_entry_id]  BIGINT           NULL,
    [created_at_utc]    DATETIME2(7)     NOT NULL,
    CONSTRAINT [PK_transactional_outbox] PRIMARY KEY CLUSTERED ([outbox_id]),
    CONSTRAINT [UQ_transactional_outbox_event] UNIQUE ([event_uuid]),
    CONSTRAINT [CK_transactional_outbox_json] CHECK (ISJSON([payload_json]) = 1)
);
GO

-- -----------------------------------------------------------------------------
-- 8. Tipos tabla para los parámetros de entrada (TVP)
-- -----------------------------------------------------------------------------
CREATE TYPE [oltp].[tt_journal_line] AS TABLE (
    [line_number]    INT           NOT NULL PRIMARY KEY,
    [account_id]     BIGINT        NOT NULL,
    [contract_id]    BIGINT        NOT NULL DEFAULT (0),
    [partner_id]     BIGINT        NOT NULL,
    [currency_code]  CHAR(3)       NOT NULL,
    [amount_debit]   DECIMAL(19,4) NOT NULL DEFAULT (0),
    [amount_credit]  DECIMAL(19,4) NOT NULL DEFAULT (0),
    [fx_rate]        DECIMAL(12,6) NOT NULL DEFAULT (1)
);
GO

CREATE TYPE [oltp].[tt_post_amount] AS TABLE (
    [line_number]       INT           NOT NULL PRIMARY KEY,
    [functional_amount] DECIMAL(19,4) NOT NULL
);
GO

-- -----------------------------------------------------------------------------
-- 9. Funciones deterministas compartidas por escritura y verificación
-- -----------------------------------------------------------------------------
CREATE FUNCTION [oltp].[fn_local_cutoff_date] (@ts_utc DATETIME2(7))
RETURNS DATE
WITH SCHEMABINDING
AS
BEGIN
    -- Fecha contable local (America/Santiago, 'Pacific SA Standard Time') con corte 18:00.
    DECLARE @local DATETIME2(7) = CONVERT(DATETIME2(7), (@ts_utc AT TIME ZONE 'UTC') AT TIME ZONE 'Pacific SA Standard Time');
    RETURN CASE WHEN DATEPART(HOUR, @local) >= 18 THEN DATEADD(DAY, 1, CAST(@local AS DATE)) ELSE CAST(@local AS DATE) END;
END;
GO

CREATE FUNCTION [oltp].[fn_row_hash] (
    @entry_uuid UNIQUEIDENTIFIER, @line_number INT, @account_id BIGINT, @contract_id BIGINT, @partner_id BIGINT,
    @currency_code CHAR(3), @status VARCHAR(10), @amount_debit DECIMAL(19,4), @amount_credit DECIMAL(19,4),
    @functional_debit DECIMAL(19,4), @functional_credit DECIMAL(19,4), @released_debit DECIMAL(19,4),
    @released_credit DECIMAL(19,4), @fx_rate DECIMAL(12,6))
RETURNS BINARY(32)
WITH SCHEMABINDING
AS
BEGIN
    RETURN HASHBYTES('SHA2_256', CONCAT(
        CONVERT(VARCHAR(36), @entry_uuid), '|', @line_number, '|', @account_id, '|', @contract_id, '|', @partner_id, '|',
        @currency_code, '|', @status, '|', CONVERT(VARCHAR(40), @amount_debit), '|', CONVERT(VARCHAR(40), @amount_credit), '|',
        CONVERT(VARCHAR(40), @functional_debit), '|', CONVERT(VARCHAR(40), @functional_credit), '|',
        CONVERT(VARCHAR(40), @released_debit), '|', CONVERT(VARCHAR(40), @released_credit), '|', CONVERT(VARCHAR(40), @fx_rate)));
END;
GO

CREATE FUNCTION [oltp].[fn_entry_hash] (
    @entry_uuid UNIQUEIDENTIFIER, @idempotency_key UNIQUEIDENTIFIER, @entry_kind VARCHAR(16), @pending_entry_id BIGINT,
    @pending_expires_at_utc DATETIME2(7), @ts_utc DATETIME2(7), @posting_date DATE, @value_date DATE,
    @source_system VARCHAR(30), @transaction_code VARCHAR(40), @description NVARCHAR(255), @total DECIMAL(19,4),
    @line_count INT, @reversal_of_entry_id BIGINT, @prev_entry_hash BINARY(32), @lines_digest BINARY(32))
RETURNS BINARY(32)
WITH SCHEMABINDING
AS
BEGIN
    -- ISNULL explícito: CONCAT trata NULL como cadena vacía y eso volvería ambiguo el hash.
    RETURN HASHBYTES('SHA2_256', CONCAT(
        CONVERT(VARCHAR(36), @entry_uuid), N'|', CONVERT(VARCHAR(36), @idempotency_key), N'|', @entry_kind, N'|',
        ISNULL(CONVERT(VARCHAR(20), @pending_entry_id), 'NULL'), N'|',
        ISNULL(CONVERT(VARCHAR(27), @pending_expires_at_utc, 126), 'NULL'), N'|',
        CONVERT(VARCHAR(27), @ts_utc, 126), N'|', CONVERT(CHAR(8), @posting_date, 112), N'|',
        CONVERT(CHAR(8), @value_date, 112), N'|', @source_system, N'|', @transaction_code, N'|', @description, N'|',
        CONVERT(VARCHAR(40), @total), N'|', @line_count, N'|', ISNULL(CONVERT(VARCHAR(20), @reversal_of_entry_id), 'NULL'), N'|',
        CONVERT(VARCHAR(64), @prev_entry_hash, 2), N'|', CONVERT(VARCHAR(64), @lines_digest, 2)));
END;
GO

-- -----------------------------------------------------------------------------
-- 10. Inmutabilidad: triggers INSTEAD OF + DENY
-- -----------------------------------------------------------------------------
CREATE OR ALTER TRIGGER [oltp].[tr_journal_entry_immutable] ON [oltp].[journal_entry]
INSTEAD OF UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    THROW 51001, N'VIOLACION_INMUTABILIDAD: oltp.journal_entry es append-only; registre un asiento de reverso (oltp.usp_reverse_journal_entry).', 1;
END;
GO

CREATE OR ALTER TRIGGER [oltp].[tr_ledger_posting_immutable] ON [oltp].[ledger_posting]
INSTEAD OF UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    THROW 51002, N'VIOLACION_INMUTABILIDAD: oltp.ledger_posting es append-only.', 1;
END;
GO

CREATE OR ALTER TRIGGER [oltp].[tr_transactional_outbox_immutable] ON [oltp].[transactional_outbox]
INSTEAD OF UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    THROW 51003, N'VIOLACION_INMUTABILIDAD: el outbox es insert-only; el avance del consumidor se registra en stg.outbox_checkpoint.', 1;
END;
GO

CREATE OR ALTER TRIGGER [oltp].[tr_idempotency_records_guard] ON [oltp].[idempotency_records]
INSTEAD OF UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    IF EXISTS (SELECT 1 FROM inserted)
        THROW 51004, N'VIOLACION_INMUTABILIDAD: un registro de idempotencia no se reescribe.', 1;
    IF EXISTS (SELECT 1 FROM deleted WHERE [expires_at_utc] >= SYSUTCDATETIME())
        THROW 51005, N'VIOLACION_RETENCION: sólo se purgan claves de idempotencia expiradas.', 1;
    DELETE r FROM [oltp].[idempotency_records] r JOIN deleted d ON d.[idempotency_key] = r.[idempotency_key];
END;
GO

CREATE OR ALTER TRIGGER [oltp].[tr_account_balance_no_delete] ON [oltp].[account_balance]
INSTEAD OF DELETE
AS
BEGIN
    SET NOCOUNT ON;
    THROW 51006, N'VIOLACION_INMUTABILIDAD: los saldos no se eliminan.', 1;
END;
GO

-- Ninguna sentencia ad hoc puede escribir en el data plane: sólo los procedimientos
-- (encadenamiento de propiedad dbo -> dbo). TRUNCATE exige ALTER, que tampoco se concede.
DENY INSERT, UPDATE, DELETE ON SCHEMA::[oltp] TO [public];
GO

-- -----------------------------------------------------------------------------
-- 11. Registro determinista de rechazos (transacción propia)
-- -----------------------------------------------------------------------------
CREATE PROCEDURE [oltp].[usp_record_idempotent_failure]
    @p_idempotency_key UNIQUEIDENTIFIER,
    @p_payload_hash    BINARY(32),
    @p_operation       VARCHAR(30),
    @p_error_number    INT,
    @p_error_message   NVARCHAR(2048)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    BEGIN TRANSACTION;
    IF NOT EXISTS (SELECT 1 FROM [oltp].[idempotency_records] WITH (UPDLOCK, HOLDLOCK) WHERE [idempotency_key] = @p_idempotency_key)
    BEGIN
        INSERT INTO [oltp].[idempotency_records] ([idempotency_key], [request_payload_hash], [operation], [status],
                                                  [journal_entry_id], [response_json], [created_at_utc], [expires_at_utc])
        VALUES (@p_idempotency_key, @p_payload_hash, @p_operation, 'FAILED', NULL,
                (SELECT 'FAILED' AS [status], @p_error_number AS [error_number], LEFT(@p_error_message, 3500) AS [error_message]
                 FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),
                SYSUTCDATETIME(), DATEADD(YEAR, 10, SYSUTCDATETIME()));
    END;
    COMMIT TRANSACTION;
END;
GO

-- -----------------------------------------------------------------------------
-- 12. PROCEDIMIENTO ATÓMICO NO INTERACTIVO: oltp.usp_post_atomic_journal_entry
--     Errores de negocio: 52001-52099 (se registran como FAILED y se relanzan).
--     Errores de idempotencia/infraestructura: 51010+ (no se registran).
-- -----------------------------------------------------------------------------
CREATE PROCEDURE [oltp].[usp_post_atomic_journal_entry]
    @p_idempotency_key          UNIQUEIDENTIFIER,
    @p_source_system            VARCHAR(30),
    @p_transaction_code         VARCHAR(40),
    @p_description              NVARCHAR(255),
    @p_lines                    [oltp].[tt_journal_line] READONLY,
    @p_value_date               DATE          = NULL,      -- NULL = fecha contable
    @p_phase                    VARCHAR(10)   = 'POSTED',  -- POSTED | PENDING
    @p_pending_timeout_seconds  INT           = NULL,
    @p_reversal_of_entry_id     BIGINT        = NULL,
    @p_ledger_id                INT           = 1,
    @o_journal_entry_id         BIGINT         OUTPUT,
    @o_replayed                 BIT            OUTPUT,
    @o_response_json            NVARCHAR(4000) OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    -- 1) Hash canónico del payload (antes de cualquier lock).
    DECLARE @payload NVARCHAR(MAX) = (
        SELECT @p_phase AS [phase], @p_source_system AS [source], @p_transaction_code AS [code],
               @p_description AS [description], CONVERT(CHAR(8), @p_value_date, 112) AS [value_date],
               @p_pending_timeout_seconds AS [timeout_s], @p_reversal_of_entry_id AS [reversal_of],
               (SELECT l.[line_number], l.[account_id], l.[contract_id], l.[partner_id], l.[currency_code],
                       CONVERT(VARCHAR(40), l.[amount_debit]) AS [debit], CONVERT(VARCHAR(40), l.[amount_credit]) AS [credit],
                       CONVERT(VARCHAR(40), l.[fx_rate]) AS [fx]
                FROM @p_lines l ORDER BY l.[line_number] FOR JSON PATH) AS [lines]
        FOR JSON PATH, WITHOUT_ARRAY_WRAPPER);
    DECLARE @payload_hash BINARY(32) = HASHBYTES('SHA2_256', @payload);  -- sin límite de 8000 bytes desde SQL 2016

    DECLARE @now_utc DATETIME2(7) = SYSUTCDATETIME();
    DECLARE @now_local DATETIME2(7) = CONVERT(DATETIME2(7), (@now_utc AT TIME ZONE 'UTC') AT TIME ZONE 'Pacific SA Standard Time');
    DECLARE @posting_date DATE = [oltp].[fn_local_cutoff_date](@now_utc);
    DECLARE @value_date DATE = ISNULL(@p_value_date, @posting_date);
    DECLARE @line_status VARCHAR(10) = CASE WHEN @p_phase = 'PENDING' THEN 'PENDING' ELSE 'POSTED' END;
    DECLARE @existing_hash BINARY(32), @existing_response NVARCHAR(4000), @existing_entry BIGINT;

    SET @o_replayed = 0;

    BEGIN TRY
        BEGIN TRANSACTION;

        -- 2) Idempotencia: UPDLOCK + HOLDLOCK toma un lock de rango sobre la clave aunque
        --    no exista, de modo que dos reintentos concurrentes se serializan aquí.
        SELECT @existing_hash = [request_payload_hash], @existing_response = [response_json], @existing_entry = [journal_entry_id]
        FROM [oltp].[idempotency_records] WITH (UPDLOCK, HOLDLOCK)
        WHERE [idempotency_key] = @p_idempotency_key;

        IF @existing_hash IS NOT NULL
        BEGIN
            IF @existing_hash <> @payload_hash
                THROW 51010, N'IDEMPOTENCIA_PAYLOAD_DISTINTO: la clave ya fue usada con otro payload.', 1;
            SELECT @o_replayed = 1, @o_response_json = @existing_response, @o_journal_entry_id = @existing_entry;
            COMMIT TRANSACTION;
            RETURN 0;
        END;

        -- 3) Validaciones de negocio sobre el TVP (sin tocar tablas calientes todavía).
        IF @p_phase NOT IN ('POSTED','PENDING')
            THROW 52001, N'FASE_INVALIDA: @p_phase debe ser POSTED o PENDING.', 1;
        IF @p_phase = 'PENDING' AND ISNULL(@p_pending_timeout_seconds, 0) <= 0
            THROW 52002, N'TIMEOUT_REQUERIDO: una reserva PENDING requiere @p_pending_timeout_seconds > 0.', 1;
        IF (SELECT COUNT(*) FROM @p_lines) < 2
            THROW 52003, N'ASIENTO_INCOMPLETO: se requieren al menos dos líneas.', 1;
        IF EXISTS (SELECT 1 FROM @p_lines WHERE [amount_debit] < 0 OR [amount_credit] < 0 OR [fx_rate] <= 0
                   OR ([amount_debit] > 0 AND [amount_credit] > 0) OR ([amount_debit] = 0 AND [amount_credit] = 0))
            THROW 52004, N'LINEA_INVALIDA: cada línea debe tener exactamente un lado (debe o haber) mayor que cero.', 1;
        IF EXISTS (SELECT 1 FROM @p_lines l LEFT JOIN [oltp].[ledger_account] a ON a.[account_id] = l.[account_id]
                   WHERE a.[account_id] IS NULL OR a.[ledger_id] <> @p_ledger_id)
            THROW 52005, N'CUENTA_INEXISTENTE: hay líneas con cuentas fuera del plan de cuentas del ledger.', 1;
        IF @value_date > @posting_date
            THROW 52006, N'FECHA_VALOR_FUTURA: la fecha valor no puede ser posterior a la fecha contable.', 1;

        DECLARE @lines TABLE (
            [line_number] INT PRIMARY KEY, [account_id] BIGINT, [contract_id] BIGINT, [partner_id] BIGINT,
            [currency_code] CHAR(3), [amount_debit] DECIMAL(19,4), [amount_credit] DECIMAL(19,4),
            [functional_debit] DECIMAL(19,4), [functional_credit] DECIMAL(19,4), [fx_rate] DECIMAL(12,6), [row_hash] BINARY(32));
        INSERT INTO @lines
        SELECT l.[line_number], l.[account_id], l.[contract_id], l.[partner_id], l.[currency_code], l.[amount_debit],
               l.[amount_credit], ROUND(l.[amount_debit] * l.[fx_rate], 4), ROUND(l.[amount_credit] * l.[fx_rate], 4),
               l.[fx_rate], NULL
        FROM @p_lines l;

        DECLARE @total_debit DECIMAL(19,4), @total_credit DECIMAL(19,4), @line_count INT;
        SELECT @total_debit = SUM([functional_debit]), @total_credit = SUM([functional_credit]), @line_count = COUNT(*) FROM @lines;
        IF @total_debit <> @total_credit
        BEGIN
            DECLARE @msg NVARCHAR(400) = FORMATMESSAGE(N'DESCUADRE_CONTABLE: debe %s <> haber %s (moneda funcional).',
                                                       CONVERT(VARCHAR(40), @total_debit), CONVERT(VARCHAR(40), @total_credit));
            THROW 52007, @msg, 1;
        END;

        -- 4) Saldos: deltas agregados por (cuenta, contrato), filas creadas si faltan,
        --    actualización en orden de clave clúster (orden determinista de locks).
        DECLARE @deltas TABLE ([account_id] BIGINT, [contract_id] BIGINT, [d_pending] DECIMAL(19,4), [c_pending] DECIMAL(19,4),
                               [d_posted] DECIMAL(19,4), [c_posted] DECIMAL(19,4), PRIMARY KEY ([account_id], [contract_id]));
        INSERT INTO @deltas
        SELECT [account_id], [contract_id],
               SUM(CASE WHEN @line_status = 'PENDING' THEN [functional_debit]  ELSE 0 END),
               SUM(CASE WHEN @line_status = 'PENDING' THEN [functional_credit] ELSE 0 END),
               SUM(CASE WHEN @line_status = 'POSTED'  THEN [functional_debit]  ELSE 0 END),
               SUM(CASE WHEN @line_status = 'POSTED'  THEN [functional_credit] ELSE 0 END)
        FROM @lines GROUP BY [account_id], [contract_id];

        INSERT INTO [oltp].[account_balance] ([account_id], [contract_id], [updated_at_utc])
        SELECT d.[account_id], d.[contract_id], @now_utc
        FROM @deltas d
        WHERE NOT EXISTS (SELECT 1 FROM [oltp].[account_balance] b WITH (UPDLOCK, HOLDLOCK)
                          WHERE b.[account_id] = d.[account_id] AND b.[contract_id] = d.[contract_id]);

        UPDATE b
        SET b.[debits_pending]  = b.[debits_pending]  + d.[d_pending],
            b.[credits_pending] = b.[credits_pending] + d.[c_pending],
            b.[debits_posted]   = b.[debits_posted]   + d.[d_posted],
            b.[credits_posted]  = b.[credits_posted]  + d.[c_posted],
            b.[updated_at_utc]  = @now_utc
        FROM [oltp].[account_balance] b WITH (ROWLOCK)
        JOIN @deltas d ON d.[account_id] = b.[account_id] AND d.[contract_id] = b.[contract_id];

        IF EXISTS (
            SELECT 1 FROM [oltp].[account_balance] b
            JOIN @deltas d ON d.[account_id] = b.[account_id] AND d.[contract_id] = b.[contract_id]
            JOIN [oltp].[ledger_account] a ON a.[account_id] = b.[account_id]
            WHERE (a.[debits_must_not_exceed_credits] = 1 AND b.[debits_pending] + b.[debits_posted] > b.[credits_posted])
               OR (a.[credits_must_not_exceed_debits] = 1 AND b.[credits_pending] + b.[credits_posted] > b.[debits_posted]))
            THROW 52008, N'FONDOS_INSUFICIENTES: el asiento excede el límite de sobregiro de al menos una cuenta.', 1;

        -- 5) Cadena hash: sección serializada mínima.
        DECLARE @entry_uuid UNIQUEIDENTIFIER = NEWID();
        DECLARE @prev_hash BINARY(32);
        SELECT @prev_hash = [last_entry_hash] FROM [oltp].[ledger_chain_tip] WITH (UPDLOCK, ROWLOCK) WHERE [ledger_id] = @p_ledger_id;

        UPDATE @lines SET [row_hash] = [oltp].[fn_row_hash](@entry_uuid, [line_number], [account_id], [contract_id], [partner_id],
            [currency_code], @line_status, [amount_debit], [amount_credit], [functional_debit], [functional_credit], 0, 0, [fx_rate]);

        DECLARE @lines_digest BINARY(32) = (
            SELECT HASHBYTES('SHA2_256', STRING_AGG(CONVERT(VARCHAR(MAX), CONVERT(VARCHAR(64), [row_hash], 2)), '') WITHIN GROUP (ORDER BY [line_number]))
            FROM @lines);
        DECLARE @expires DATETIME2(7) = CASE WHEN @p_phase = 'PENDING' THEN DATEADD(SECOND, @p_pending_timeout_seconds, @now_utc) END;
        DECLARE @entry_hash BINARY(32) = [oltp].[fn_entry_hash](@entry_uuid, @p_idempotency_key, @p_phase, NULL, @expires,
            @now_utc, @posting_date, @value_date, @p_source_system, @p_transaction_code, @p_description, @total_debit,
            @line_count, @p_reversal_of_entry_id, @prev_hash, @lines_digest);

        INSERT INTO [oltp].[journal_entry] ([entry_uuid], [idempotency_key], [ledger_id], [entry_kind], [pending_entry_id],
            [pending_expires_at_utc], [transaction_timestamp_utc], [transaction_timestamp_local], [local_cutoff_date],
            [posting_date], [value_date], [source_system], [transaction_code], [description], [total_debit], [total_credit],
            [line_count], [reversal_of_entry_id], [prev_entry_hash], [entry_hash])
        VALUES (@entry_uuid, @p_idempotency_key, @p_ledger_id, @p_phase, NULL, @expires, @now_utc, @now_local, @posting_date,
            @posting_date, @value_date, @p_source_system, @p_transaction_code, @p_description, @total_debit, @total_credit,
            @line_count, @p_reversal_of_entry_id, @prev_hash, @entry_hash);
        SET @o_journal_entry_id = SCOPE_IDENTITY();

        INSERT INTO [oltp].[ledger_posting] ([journal_entry_id], [line_number], [account_id], [contract_id], [partner_id],
            [currency_code], [posting_status], [amount_debit], [amount_credit], [functional_debit], [functional_credit],
            [functional_released_debit], [functional_released_credit], [fx_rate], [row_hash])
        SELECT @o_journal_entry_id, [line_number], [account_id], [contract_id], [partner_id], [currency_code], @line_status,
               [amount_debit], [amount_credit], [functional_debit], [functional_credit], 0, 0, [fx_rate], [row_hash]
        FROM @lines;

        UPDATE b SET b.[last_journal_entry_id] = @o_journal_entry_id
        FROM [oltp].[account_balance] b JOIN @deltas d ON d.[account_id] = b.[account_id] AND d.[contract_id] = b.[contract_id];

        UPDATE [oltp].[ledger_chain_tip]
        SET [last_entry_id] = @o_journal_entry_id, [last_entry_hash] = @entry_hash, [updated_at_utc] = @now_utc
        WHERE [ledger_id] = @p_ledger_id;

        -- 6) Respuesta, outbox e idempotencia en la misma transacción.
        SET @o_response_json = (
            SELECT 'POSTED' AS [status], @o_journal_entry_id AS [journal_entry_id], @entry_uuid AS [entry_uuid],
                   @p_phase AS [entry_kind], CONVERT(VARCHAR(64), @entry_hash, 2) AS [entry_hash],
                   @posting_date AS [posting_date], @value_date AS [value_date]
            FOR JSON PATH, WITHOUT_ARRAY_WRAPPER);

        INSERT INTO [oltp].[transactional_outbox] ([aggregate_type], [aggregate_id], [event_type], [payload_json],
                                                   [journal_entry_id], [created_at_utc])
        VALUES ('JOURNAL_ENTRY', CONVERT(VARCHAR(64), @o_journal_entry_id), 'LEDGER.' + @p_phase,
                (SELECT JSON_QUERY(@o_response_json) AS [entry], @p_transaction_code AS [transaction_code],
                        @p_source_system AS [source_system],
                        (SELECT [line_number], [account_id], [contract_id], [functional_debit], [functional_credit]
                         FROM @lines ORDER BY [line_number] FOR JSON PATH) AS [lines]
                 FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),
                @o_journal_entry_id, @now_utc);

        INSERT INTO [oltp].[idempotency_records] ([idempotency_key], [request_payload_hash], [operation], [status],
                                                  [journal_entry_id], [response_json], [created_at_utc], [expires_at_utc])
        VALUES (@p_idempotency_key, @payload_hash, 'POST_JOURNAL_ENTRY', 'POSTED', @o_journal_entry_id, @o_response_json,
                @now_utc, DATEADD(YEAR, 10, @now_utc));

        COMMIT TRANSACTION;
        RETURN 0;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
        IF ERROR_NUMBER() BETWEEN 52001 AND 52099
        BEGIN
            DECLARE @err_no INT = ERROR_NUMBER(), @err_msg NVARCHAR(2048) = ERROR_MESSAGE();
            EXEC [oltp].[usp_record_idempotent_failure] @p_idempotency_key, @payload_hash, 'POST_JOURNAL_ENTRY', @err_no, @err_msg;
        END;
        THROW;
    END CATCH;
END;
GO

-- -----------------------------------------------------------------------------
-- 13. Resolución de reservas: POST (total o parcial), VOID, EXPIRE
-- -----------------------------------------------------------------------------
CREATE PROCEDURE [oltp].[usp_resolve_pending_entry]
    @p_idempotency_key    UNIQUEIDENTIFIER,
    @p_pending_entry_id   BIGINT,
    @p_action             VARCHAR(10),                      -- POST | VOID | EXPIRE
    @p_post_amounts       [oltp].[tt_post_amount] READONLY, -- vacío = liquidación total
    @o_journal_entry_id   BIGINT         OUTPUT,
    @o_replayed           BIT            OUTPUT,
    @o_response_json      NVARCHAR(4000) OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @kind VARCHAR(16) = CASE @p_action WHEN 'POST' THEN 'POST_PENDING' WHEN 'VOID' THEN 'VOID_PENDING'
                                               WHEN 'EXPIRE' THEN 'EXPIRE_PENDING' END;
    DECLARE @payload_hash BINARY(32) = HASHBYTES('SHA2_256', (
        SELECT @kind AS [op], @p_pending_entry_id AS [pending_entry_id],
               (SELECT [line_number], CONVERT(VARCHAR(40), [functional_amount]) AS [amount]
                FROM @p_post_amounts ORDER BY [line_number] FOR JSON PATH) AS [post_amounts]
        FOR JSON PATH, WITHOUT_ARRAY_WRAPPER));
    DECLARE @now_utc DATETIME2(7) = SYSUTCDATETIME();
    DECLARE @now_local DATETIME2(7) = CONVERT(DATETIME2(7), (@now_utc AT TIME ZONE 'UTC') AT TIME ZONE 'Pacific SA Standard Time');
    DECLARE @posting_date DATE = [oltp].[fn_local_cutoff_date](@now_utc);
    DECLARE @existing_hash BINARY(32), @existing_response NVARCHAR(4000), @existing_entry BIGINT;
    SET @o_replayed = 0;

    BEGIN TRY
        BEGIN TRANSACTION;

        SELECT @existing_hash = [request_payload_hash], @existing_response = [response_json], @existing_entry = [journal_entry_id]
        FROM [oltp].[idempotency_records] WITH (UPDLOCK, HOLDLOCK) WHERE [idempotency_key] = @p_idempotency_key;
        IF @existing_hash IS NOT NULL
        BEGIN
            IF @existing_hash <> @payload_hash
                THROW 51010, N'IDEMPOTENCIA_PAYLOAD_DISTINTO: la clave ya fue usada con otro payload.', 1;
            SELECT @o_replayed = 1, @o_response_json = @existing_response, @o_journal_entry_id = @existing_entry;
            COMMIT TRANSACTION;
            RETURN 0;
        END;

        IF @kind IS NULL THROW 52020, N'ACCION_INVALIDA: use POST, VOID o EXPIRE.', 1;

        DECLARE @src VARCHAR(30), @code VARCHAR(40), @desc NVARCHAR(255), @expires DATETIME2(7), @ledger_id INT;
        SELECT @src = [source_system], @code = [transaction_code], @desc = [description], @expires = [pending_expires_at_utc],
               @ledger_id = [ledger_id]
        FROM [oltp].[journal_entry] WITH (UPDLOCK, HOLDLOCK)
        WHERE [journal_entry_id] = @p_pending_entry_id AND [entry_kind] = 'PENDING';
        IF @src IS NULL THROW 52021, N'RESERVA_INEXISTENTE: el asiento indicado no es una reserva PENDING.', 1;
        IF EXISTS (SELECT 1 FROM [oltp].[journal_entry] WITH (UPDLOCK, HOLDLOCK) WHERE [pending_entry_id] = @p_pending_entry_id)
            THROW 52022, N'RESERVA_YA_RESUELTA: la reserva ya fue liquidada, anulada o expirada.', 1;
        IF @p_action = 'POST' AND @now_utc >= @expires
            THROW 52023, N'RESERVA_EXPIRADA: la reserva venció y no puede liquidarse.', 1;
        IF @p_action = 'EXPIRE' AND @now_utc < @expires
            THROW 52024, N'RESERVA_VIGENTE: no puede expirarse antes de su vencimiento.', 1;
        IF @p_action <> 'POST' AND EXISTS (SELECT 1 FROM @p_post_amounts)
            THROW 52025, N'MONTOS_NO_APLICAN: sólo POST admite montos parciales.', 1;

        DECLARE @lines TABLE (
            [line_number] INT PRIMARY KEY, [account_id] BIGINT, [contract_id] BIGINT, [partner_id] BIGINT, [currency_code] CHAR(3),
            [is_debit] BIT, [pending_amount] DECIMAL(19,4), [posted_amount] DECIMAL(19,4), [fx_rate] DECIMAL(12,6),
            [row_hash] BINARY(32));
        INSERT INTO @lines
        SELECT p.[line_number], p.[account_id], p.[contract_id], p.[partner_id], p.[currency_code],
               CASE WHEN p.[functional_debit] > 0 THEN 1 ELSE 0 END,
               p.[functional_debit] + p.[functional_credit],
               CASE WHEN @p_action = 'POST' THEN ISNULL(a.[functional_amount], p.[functional_debit] + p.[functional_credit]) ELSE 0 END,
               p.[fx_rate], NULL
        FROM [oltp].[ledger_posting] p
        LEFT JOIN @p_post_amounts a ON a.[line_number] = p.[line_number]
        WHERE p.[journal_entry_id] = @p_pending_entry_id;

        IF EXISTS (SELECT 1 FROM @lines WHERE [posted_amount] < 0 OR [posted_amount] > [pending_amount])
            THROW 52026, N'MONTO_POST_INVALIDO: el monto liquidado excede la reserva de la línea.', 1;

        DECLARE @total_debit DECIMAL(19,4), @total_credit DECIMAL(19,4), @line_count INT;
        SELECT @total_debit = SUM(CASE WHEN [is_debit] = 1 THEN [posted_amount] ELSE 0 END),
               @total_credit = SUM(CASE WHEN [is_debit] = 0 THEN [posted_amount] ELSE 0 END), @line_count = COUNT(*)
        FROM @lines;
        IF @total_debit <> @total_credit
            THROW 52027, N'DESCUADRE_CONTABLE: la liquidación parcial no cuadra debe/haber.', 1;

        -- Saldos: se libera toda la reserva y se contabiliza lo liquidado.
        UPDATE b
        SET b.[debits_pending]  = b.[debits_pending]  - x.[rel_d],
            b.[credits_pending] = b.[credits_pending] - x.[rel_c],
            b.[debits_posted]   = b.[debits_posted]   + x.[post_d],
            b.[credits_posted]  = b.[credits_posted]  + x.[post_c],
            b.[updated_at_utc]  = @now_utc
        FROM [oltp].[account_balance] b WITH (ROWLOCK)
        JOIN (SELECT [account_id], [contract_id],
                     SUM(CASE WHEN [is_debit] = 1 THEN [pending_amount] ELSE 0 END) AS [rel_d],
                     SUM(CASE WHEN [is_debit] = 0 THEN [pending_amount] ELSE 0 END) AS [rel_c],
                     SUM(CASE WHEN [is_debit] = 1 THEN [posted_amount]  ELSE 0 END) AS [post_d],
                     SUM(CASE WHEN [is_debit] = 0 THEN [posted_amount]  ELSE 0 END) AS [post_c]
              FROM @lines GROUP BY [account_id], [contract_id]) x
          ON x.[account_id] = b.[account_id] AND x.[contract_id] = b.[contract_id];

        IF EXISTS (
            SELECT 1 FROM [oltp].[account_balance] b
            JOIN (SELECT DISTINCT [account_id], [contract_id] FROM @lines) l ON l.[account_id] = b.[account_id] AND l.[contract_id] = b.[contract_id]
            JOIN [oltp].[ledger_account] a ON a.[account_id] = b.[account_id]
            WHERE (a.[debits_must_not_exceed_credits] = 1 AND b.[debits_pending] + b.[debits_posted] > b.[credits_posted])
               OR (a.[credits_must_not_exceed_debits] = 1 AND b.[credits_pending] + b.[credits_posted] > b.[debits_posted]))
            THROW 52028, N'FONDOS_INSUFICIENTES: la liquidación excede el límite de sobregiro.', 1;

        DECLARE @entry_uuid UNIQUEIDENTIFIER = NEWID();
        DECLARE @prev_hash BINARY(32);
        SELECT @prev_hash = [last_entry_hash] FROM [oltp].[ledger_chain_tip] WITH (UPDLOCK, ROWLOCK) WHERE [ledger_id] = @ledger_id;

        UPDATE @lines SET [row_hash] = [oltp].[fn_row_hash](@entry_uuid, [line_number], [account_id], [contract_id], [partner_id],
            [currency_code], CASE WHEN [posted_amount] > 0 THEN 'POSTED' ELSE 'RELEASED' END,
            CASE WHEN [is_debit] = 1 THEN ROUND([posted_amount] / [fx_rate], 4) ELSE 0 END,
            CASE WHEN [is_debit] = 0 THEN ROUND([posted_amount] / [fx_rate], 4) ELSE 0 END,
            CASE WHEN [is_debit] = 1 THEN [posted_amount] ELSE 0 END, CASE WHEN [is_debit] = 0 THEN [posted_amount] ELSE 0 END,
            CASE WHEN [is_debit] = 1 THEN [pending_amount] ELSE 0 END, CASE WHEN [is_debit] = 0 THEN [pending_amount] ELSE 0 END,
            [fx_rate]);

        DECLARE @lines_digest BINARY(32) = (
            SELECT HASHBYTES('SHA2_256', STRING_AGG(CONVERT(VARCHAR(MAX), CONVERT(VARCHAR(64), [row_hash], 2)), '') WITHIN GROUP (ORDER BY [line_number]))
            FROM @lines);
        DECLARE @new_desc NVARCHAR(255) = LEFT(CONCAT(CASE @p_action WHEN 'POST' THEN N'Liquidación' WHEN 'VOID' THEN N'Anulación'
                                                                     ELSE N'Expiración' END,
                                                      N' de reserva #', @p_pending_entry_id, N': ', @desc), 255);
        DECLARE @new_code VARCHAR(40) = LEFT(CONCAT(@code, '_', @kind), 40);
        DECLARE @entry_hash BINARY(32) = [oltp].[fn_entry_hash](@entry_uuid, @p_idempotency_key, @kind, @p_pending_entry_id, NULL,
            @now_utc, @posting_date, @posting_date, @src, @new_code, @new_desc, @total_debit, @line_count, NULL, @prev_hash,
            @lines_digest);

        INSERT INTO [oltp].[journal_entry] ([entry_uuid], [idempotency_key], [ledger_id], [entry_kind], [pending_entry_id],
            [pending_expires_at_utc], [transaction_timestamp_utc], [transaction_timestamp_local], [local_cutoff_date],
            [posting_date], [value_date], [source_system], [transaction_code], [description], [total_debit], [total_credit],
            [line_count], [reversal_of_entry_id], [prev_entry_hash], [entry_hash])
        VALUES (@entry_uuid, @p_idempotency_key, @ledger_id, @kind, @p_pending_entry_id, NULL, @now_utc, @now_local,
            @posting_date, @posting_date, @posting_date, @src, @new_code, @new_desc, @total_debit, @total_credit, @line_count,
            NULL, @prev_hash, @entry_hash);
        SET @o_journal_entry_id = SCOPE_IDENTITY();

        INSERT INTO [oltp].[ledger_posting] ([journal_entry_id], [line_number], [account_id], [contract_id], [partner_id],
            [currency_code], [posting_status], [amount_debit], [amount_credit], [functional_debit], [functional_credit],
            [functional_released_debit], [functional_released_credit], [fx_rate], [row_hash])
        SELECT @o_journal_entry_id, [line_number], [account_id], [contract_id], [partner_id], [currency_code],
               CASE WHEN [posted_amount] > 0 THEN 'POSTED' ELSE 'RELEASED' END,
               CASE WHEN [is_debit] = 1 THEN ROUND([posted_amount] / [fx_rate], 4) ELSE 0 END,
               CASE WHEN [is_debit] = 0 THEN ROUND([posted_amount] / [fx_rate], 4) ELSE 0 END,
               CASE WHEN [is_debit] = 1 THEN [posted_amount] ELSE 0 END, CASE WHEN [is_debit] = 0 THEN [posted_amount] ELSE 0 END,
               CASE WHEN [is_debit] = 1 THEN [pending_amount] ELSE 0 END, CASE WHEN [is_debit] = 0 THEN [pending_amount] ELSE 0 END,
               [fx_rate], [row_hash]
        FROM @lines;

        UPDATE [oltp].[ledger_chain_tip]
        SET [last_entry_id] = @o_journal_entry_id, [last_entry_hash] = @entry_hash, [updated_at_utc] = @now_utc
        WHERE [ledger_id] = @ledger_id;

        SET @o_response_json = (
            SELECT 'POSTED' AS [status], @o_journal_entry_id AS [journal_entry_id], @entry_uuid AS [entry_uuid],
                   @kind AS [entry_kind], @p_pending_entry_id AS [pending_entry_id],
                   CONVERT(VARCHAR(64), @entry_hash, 2) AS [entry_hash], @total_debit AS [posted_amount]
            FOR JSON PATH, WITHOUT_ARRAY_WRAPPER);

        INSERT INTO [oltp].[transactional_outbox] ([aggregate_type], [aggregate_id], [event_type], [payload_json],
                                                   [journal_entry_id], [created_at_utc])
        VALUES ('JOURNAL_ENTRY', CONVERT(VARCHAR(64), @o_journal_entry_id), 'LEDGER.' + @kind, @o_response_json,
                @o_journal_entry_id, @now_utc);

        INSERT INTO [oltp].[idempotency_records] ([idempotency_key], [request_payload_hash], [operation], [status],
                                                  [journal_entry_id], [response_json], [created_at_utc], [expires_at_utc])
        VALUES (@p_idempotency_key, @payload_hash, @kind, 'POSTED', @o_journal_entry_id, @o_response_json, @now_utc,
                DATEADD(YEAR, 10, @now_utc));

        COMMIT TRANSACTION;
        RETURN 0;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
        IF ERROR_NUMBER() BETWEEN 52001 AND 52099
        BEGIN
            DECLARE @err_no INT = ERROR_NUMBER(), @err_msg NVARCHAR(2048) = ERROR_MESSAGE();
            EXEC [oltp].[usp_record_idempotent_failure] @p_idempotency_key, @payload_hash, @kind, @err_no, @err_msg;
        END;
        THROW;
    END CATCH;
END;
GO

-- -----------------------------------------------------------------------------
-- 14. Job de expiración (SQL Agent cada minuto). Clave determinista por reserva:
--     re-ejecutar el job nunca duplica la expiración.
-- -----------------------------------------------------------------------------
CREATE PROCEDURE [oltp].[usp_expire_pending_entries]
    @p_max_entries INT = 1000
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @pending_id BIGINT, @key UNIQUEIDENTIFIER, @jid BIGINT, @replayed BIT, @resp NVARCHAR(4000);
    DECLARE @none [oltp].[tt_post_amount];

    DECLARE c CURSOR LOCAL FAST_FORWARD FOR
        SELECT TOP (@p_max_entries) j.[journal_entry_id]
        FROM [oltp].[journal_entry] j
        WHERE j.[entry_kind] = 'PENDING' AND j.[pending_expires_at_utc] <= SYSUTCDATETIME()
          AND NOT EXISTS (SELECT 1 FROM [oltp].[journal_entry] r WHERE r.[pending_entry_id] = j.[journal_entry_id])
        ORDER BY j.[pending_expires_at_utc];
    OPEN c;
    FETCH NEXT FROM c INTO @pending_id;
    WHILE @@FETCH_STATUS = 0
    BEGIN
        SET @key = CAST(HASHBYTES('MD5', CONCAT('EXPIRE-', @pending_id)) AS UNIQUEIDENTIFIER);
        BEGIN TRY
            EXEC [oltp].[usp_resolve_pending_entry] @key, @pending_id, 'EXPIRE', @none, @jid OUTPUT, @replayed OUTPUT, @resp OUTPUT;
        END TRY
        BEGIN CATCH
            -- Una carrera con un POST concurrente (52022) es esperable: la reserva ya quedó resuelta.
            IF ERROR_NUMBER() <> 52022 THROW;
        END CATCH;
        FETCH NEXT FROM c INTO @pending_id;
    END;
    CLOSE c;
    DEALLOCATE c;
END;
GO

-- -----------------------------------------------------------------------------
-- 15. Reverso compensatorio (append-only)
-- -----------------------------------------------------------------------------
CREATE PROCEDURE [oltp].[usp_reverse_journal_entry]
    @p_idempotency_key     UNIQUEIDENTIFIER,
    @p_original_entry_id   BIGINT,
    @p_reason              NVARCHAR(200),
    @o_journal_entry_id    BIGINT OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @lines [oltp].[tt_journal_line], @src VARCHAR(30), @desc NVARCHAR(255), @replayed BIT, @resp NVARCHAR(4000);

    SELECT @src = [source_system] FROM [oltp].[journal_entry]
    WHERE [journal_entry_id] = @p_original_entry_id AND [entry_kind] IN ('POSTED','POST_PENDING');
    IF @src IS NULL THROW 52040, N'REVERSO_INVALIDO: sólo se revierten asientos liquidados.', 1;

    INSERT INTO @lines ([line_number], [account_id], [contract_id], [partner_id], [currency_code], [amount_debit], [amount_credit], [fx_rate])
    SELECT [line_number], [account_id], [contract_id], [partner_id], [currency_code], [amount_credit], [amount_debit], [fx_rate]
    FROM [oltp].[ledger_posting]
    WHERE [journal_entry_id] = @p_original_entry_id AND [posting_status] = 'POSTED';

    SET @desc = LEFT(CONCAT(N'Reverso asiento #', @p_original_entry_id, N': ', @p_reason), 255);
    EXEC [oltp].[usp_post_atomic_journal_entry]
        @p_idempotency_key = @p_idempotency_key, @p_source_system = @src, @p_transaction_code = 'REVERSO_CONTABLE',
        @p_description = @desc, @p_lines = @lines, @p_reversal_of_entry_id = @p_original_entry_id,
        @o_journal_entry_id = @o_journal_entry_id OUTPUT, @o_replayed = @replayed OUTPUT, @o_response_json = @resp OUTPUT;
END;
GO

-- -----------------------------------------------------------------------------
-- 16. Purga de claves de idempotencia expiradas (respeta el trigger de retención)
-- -----------------------------------------------------------------------------
CREATE PROCEDURE [oltp].[usp_purge_expired_idempotency_keys]
    @p_batch_size INT = 5000
AS
BEGIN
    SET NOCOUNT ON;
    DELETE TOP (@p_batch_size) FROM [oltp].[idempotency_records]
    WHERE [expires_at_utc] < SYSUTCDATETIME();
END;
GO

-- -----------------------------------------------------------------------------
-- 17. Verificación forense completa de la cadena
--     Recalcula el hash de cada línea y de cada cabecera, y compara el
--     encadenamiento con el bloque anterior del mismo ledger.
-- -----------------------------------------------------------------------------
CREATE PROCEDURE [oltp].[usp_verify_ledger_integrity]
    @p_ledger_id INT = 1
AS
BEGIN
    SET NOCOUNT ON;

    WITH rows_check AS (
        SELECT p.[journal_entry_id],
               SUM(CASE WHEN p.[row_hash] = [oltp].[fn_row_hash](j.[entry_uuid], p.[line_number], p.[account_id], p.[contract_id],
                        p.[partner_id], p.[currency_code], p.[posting_status], p.[amount_debit], p.[amount_credit],
                        p.[functional_debit], p.[functional_credit], p.[functional_released_debit],
                        p.[functional_released_credit], p.[fx_rate]) THEN 0 ELSE 1 END) AS [bad_rows],
               HASHBYTES('SHA2_256', STRING_AGG(CONVERT(VARCHAR(MAX), CONVERT(VARCHAR(64), p.[row_hash], 2)), '') WITHIN GROUP (ORDER BY p.[line_number])) AS [lines_digest]
        FROM [oltp].[ledger_posting] p
        JOIN [oltp].[journal_entry] j ON j.[journal_entry_id] = p.[journal_entry_id]
        WHERE j.[ledger_id] = @p_ledger_id
        GROUP BY p.[journal_entry_id]
    ),
    chain AS (
        SELECT j.*, r.[bad_rows], r.[lines_digest],
               LAG(j.[entry_hash], 1, 0x0000000000000000000000000000000000000000000000000000000000000000)
                   OVER (ORDER BY j.[journal_entry_id]) AS [expected_prev_hash]
        FROM [oltp].[journal_entry] j
        JOIN rows_check r ON r.[journal_entry_id] = j.[journal_entry_id]
        WHERE j.[ledger_id] = @p_ledger_id
    )
    SELECT [journal_entry_id], [entry_kind], [transaction_code], [posting_date],
           CASE
               WHEN [prev_entry_hash] <> [expected_prev_hash] THEN 'CADENA_ROTA'
               WHEN [bad_rows] > 0 THEN 'DETALLE_ALTERADO'
               WHEN [entry_hash] <> [oltp].[fn_entry_hash]([entry_uuid], [idempotency_key], [entry_kind], [pending_entry_id],
                        [pending_expires_at_utc], [transaction_timestamp_utc], [posting_date], [value_date], [source_system],
                        [transaction_code], [description], [total_debit], [line_count], [reversal_of_entry_id],
                        [prev_entry_hash], [lines_digest]) THEN 'CABECERA_ALTERADA'
               ELSE 'VALIDO'
           END AS [estado_integridad]
    FROM chain
    ORDER BY [journal_entry_id];
END;
GO

GRANT EXECUTE ON [oltp].[usp_post_atomic_journal_entry] TO [app_ledger_writer];
GRANT EXECUTE ON [oltp].[usp_resolve_pending_entry]     TO [app_ledger_writer];
GRANT EXECUTE ON [oltp].[usp_reverse_journal_entry]     TO [app_ledger_writer];
GRANT EXECUTE ON TYPE::[oltp].[tt_journal_line]         TO [app_ledger_writer];
GRANT EXECUTE ON TYPE::[oltp].[tt_post_amount]          TO [app_ledger_writer];
GRANT EXECUTE ON [oltp].[usp_expire_pending_entries]    TO [app_etl_operator];
GRANT EXECUTE ON [oltp].[usp_verify_ledger_integrity]   TO [app_etl_operator];
GRANT SELECT  ON [oltp].[vw_account_available_balance]  TO [app_report_reader];
GO

-- -----------------------------------------------------------------------------
-- 18. Ejemplo de uso: compra de BTP con reintento por timeout (Caso Real 3)
-- -----------------------------------------------------------------------------
/*
DECLARE @k UNIQUEIDENTIFIER = '6F9619FF-8B86-D011-B42D-00C04FC964FF';   -- generado por el cliente, no por el servidor
DECLARE @l [oltp].[tt_journal_line];
INSERT INTO @l ([line_number], [account_id], [contract_id], [partner_id], [currency_code], [amount_debit], [amount_credit])
VALUES (1, 130101, 70100, 1006, 'CLP', 350000000.0000, 0), (2, 110101, 10001, 1005, 'CLP', 0, 350000000.0000);
DECLARE @id BIGINT, @rep BIT, @resp NVARCHAR(4000);
EXEC [oltp].[usp_post_atomic_journal_entry] @k, 'CORE_MESA_DINERO', 'COMPRA_HQLA', N'Compra BTP Nivel 1', @l,
     @o_journal_entry_id = @id OUTPUT, @o_replayed = @rep OUTPUT, @o_response_json = @resp OUTPUT;
-- Reintento idéntico tras un timeout: @rep = 1, mismo @id, ningún cargo adicional.
EXEC [oltp].[usp_post_atomic_journal_entry] @k, 'CORE_MESA_DINERO', 'COMPRA_HQLA', N'Compra BTP Nivel 1', @l,
     @o_journal_entry_id = @id OUTPUT, @o_replayed = @rep OUTPUT, @o_response_json = @resp OUTPUT;
SELECT @id AS journal_entry_id, @rep AS replayed, @resp AS response_json;
*/
