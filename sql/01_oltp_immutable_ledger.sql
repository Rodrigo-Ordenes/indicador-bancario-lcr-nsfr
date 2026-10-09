-- =============================================================================
-- 01_oltp_immutable_ledger.sql
-- =============================================================================
-- Capa 1: OLTP / Ledger Inmutable (Data Plane)
-- Implementa:
--   - Contabilidad bancaria multi-leg (Header / Postings)
--   - Invariante contable estricta: SUM(Debit) - SUM(Credit) = 0
--   - Inmutabilidad sin Ledger Nativo: DENY UPDATE/DELETE, Triggers INSTEAD OF
--   - Encadenamiento criptográfico SHA-256 (Hash chaining tipo Blockchain)
--   - Reversiones contables compensatorias (Append-Only)
-- =============================================================================

USE [CoreBanking];
GO

-- 1. TABLA CABECERA: oltp.journal_entry
IF OBJECT_ID(N'oltp.ledger_posting', N'U') IS NOT NULL DROP TABLE [oltp].[ledger_posting];
IF OBJECT_ID(N'oltp.journal_entry', N'U') IS NOT NULL DROP TABLE [oltp].[journal_entry];
GO

CREATE TABLE [oltp].[journal_entry] (
    [journal_entry_id]     BIGINT IDENTITY(1,1) NOT NULL,
    [entry_uuid]           UNIQUEIDENTIFIER     NOT NULL CONSTRAINT [DF_journal_entry_uuid] DEFAULT (NEWID()),
    [transaction_date]     DATETIME2(0)         NOT NULL,
    [posting_date]         DATE                 NOT NULL,
    [source_system]        VARCHAR(30)          NOT NULL,
    [transaction_code]     VARCHAR(20)          NOT NULL, -- DESEMBOLSO, AMORTIZACION, DEPOSITO, CASTIGO
    [description]          NVARCHAR(255)        NOT NULL,
    [is_balanced]          BIT                  NOT NULL CONSTRAINT [DF_journal_entry_balanced] DEFAULT (0),
    [is_reversed]          BIT                  NOT NULL CONSTRAINT [DF_journal_entry_reversed] DEFAULT (0),
    [reversal_of_entry_id] BIGINT               NULL,
    [prev_entry_hash]      VARBINARY(32)        NULL,     -- Hash SHA-256 del bloque anterior
    [entry_hash]           VARBINARY(32)        NOT NULL, -- Hash SHA-256 de este bloque
    [created_by]           VARCHAR(50)          NOT NULL CONSTRAINT [DF_journal_entry_user] DEFAULT (SUSER_SNAME()),
    [created_at]           DATETIME2(0)         NOT NULL CONSTRAINT [DF_journal_entry_created] DEFAULT (SYSDATETIME()),
    CONSTRAINT [PK_journal_entry] PRIMARY KEY CLUSTERED ([journal_entry_id] ASC),
    CONSTRAINT [UQ_journal_entry_uuid] UNIQUE NONCLUSTERED ([entry_uuid] ASC),
    CONSTRAINT [FK_journal_entry_reversal] FOREIGN KEY ([reversal_of_entry_id]) 
        REFERENCES [oltp].[journal_entry] ([journal_entry_id])
);
GO

-- 2. TABLA DETALLE: oltp.ledger_posting (Multi-Leg)
CREATE TABLE [oltp].[ledger_posting] (
    [posting_id]           BIGINT IDENTITY(1,1) NOT NULL,
    [journal_entry_id]     BIGINT               NOT NULL,
    [line_number]          INT                  NOT NULL,
    [account_id]           BIGINT               NOT NULL,
    [contract_id]          BIGINT               NULL,     -- Crédito, Depósito a Plazo o Derivado
    [partner_id]           BIGINT               NOT NULL, -- Cliente / Contraparte
    [currency_code]        CHAR(3)              NOT NULL, -- CLP, CLF (UF), USD, EUR
    [amount_debit]         DECIMAL(19,4)        NOT NULL CONSTRAINT [DF_posting_debit] DEFAULT (0.0000),
    [amount_credit]        DECIMAL(19,4)        NOT NULL CONSTRAINT [DF_posting_credit] DEFAULT (0.0000),
    [functional_debit]     DECIMAL(19,4)        NOT NULL CONSTRAINT [DF_posting_fdebit] DEFAULT (0.0000),
    [functional_credit]    DECIMAL(19,4)        NOT NULL CONSTRAINT [DF_posting_fcredit] DEFAULT (0.0000),
    [fx_rate]              DECIMAL(14,6)        NOT NULL CONSTRAINT [DF_posting_fx] DEFAULT (1.000000),
    [row_hash]             VARBINARY(32)        NOT NULL,
    CONSTRAINT [PK_ledger_posting] PRIMARY KEY CLUSTERED ([posting_id] ASC),
    CONSTRAINT [UQ_posting_line] UNIQUE NONCLUSTERED ([journal_entry_id] ASC, [line_number] ASC),
    CONSTRAINT [FK_posting_journal] FOREIGN KEY ([journal_entry_id]) 
        REFERENCES [oltp].[journal_entry] ([journal_entry_id]),
    CONSTRAINT [CK_posting_positive_amounts] CHECK ([amount_debit] >= 0.0000 AND [amount_credit] >= 0.0000),
    CONSTRAINT [CK_posting_mutually_exclusive] CHECK (
        ([amount_debit] > 0.0000 AND [amount_credit] = 0.0000) OR
        ([amount_credit] > 0.0000 AND [amount_debit] = 0.0000)
    )
);
GO

CREATE NONCLUSTERED INDEX [IX_ledger_posting_account_date] 
ON [oltp].[ledger_posting] ([account_id] ASC, [contract_id] ASC)
INCLUDE ([amount_debit], [amount_credit], [functional_debit], [functional_credit]);
GO

-- 3. TRIGGERS DEFENSIVOS DE INMUTABILIDAD ESTRICTA
CREATE OR ALTER TRIGGER [oltp].[tr_prevent_journal_entry_mutation]
ON [oltp].[journal_entry]
INSTEAD OF UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    RAISERROR(N'VIOLACIÓN DE INMUTABILIDAD CONTABLE: Las cabeceras del Ledger no pueden ser modificadas ni eliminadas. Aplique una transacción de reversión contable (Compensating Journal Entry).', 16, 1);
    ROLLBACK TRANSACTION;
END;
GO

CREATE OR ALTER TRIGGER [oltp].[tr_prevent_ledger_posting_mutation]
ON [oltp].[ledger_posting]
INSTEAD OF UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    RAISERROR(N'VIOLACIÓN DE INMUTABILIDAD CONTABLE: Las líneas de detalle del Ledger son estrictamente append-only.', 16, 1);
    ROLLBACK TRANSACTION;
END;
GO

-- Denegar físicamente permisos de mutación DML
DENY UPDATE, DELETE ON [oltp].[journal_entry] TO [public];
DENY UPDATE, DELETE ON [oltp].[ledger_posting] TO [public];
GO

-- 4. PROCEDIMIENTO SEGURO: Registro y Validación Atómica de Asiento Contable
CREATE OR ALTER PROCEDURE [oltp].[usp_register_journal_entry]
    @p_source_system    VARCHAR(30),
    @p_transaction_code VARCHAR(20),
    @p_description      NVARCHAR(255),
    @p_posting_date     DATE,
    @p_postings_json    NVARCHAR(MAX), -- JSON con las líneas contables
    @o_journal_entry_id BIGINT OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    -- Validar que el JSON contiene líneas
    IF @p_postings_json IS NULL OR ISJSON(@p_postings_json) <> 1
    BEGIN
        RAISERROR(N'Error: El parámetro @p_postings_json debe ser un formato JSON válido.', 16, 1);
        ROLLBACK TRANSACTION;
        RETURN -1;
    END;

    -- 1. Parsear líneas contables a tabla temporal para validar balance ANTES de insertar
    DECLARE @tb_postings TABLE (
        [line_number]       INT,
        [account_id]        BIGINT,
        [contract_id]       BIGINT,
        [partner_id]        BIGINT,
        [currency_code]     CHAR(3),
        [amount_debit]      DECIMAL(19,4),
        [amount_credit]     DECIMAL(19,4),
        [functional_debit]  DECIMAL(19,4),
        [functional_credit] DECIMAL(19,4),
        [fx_rate]           DECIMAL(14,6)
    );

    INSERT INTO @tb_postings
    SELECT 
        j.[line_number],
        j.[account_id],
        j.[contract_id],
        j.[partner_id],
        j.[currency_code],
        j.[amount_debit],
        j.[amount_credit],
        ROUND(j.[amount_debit] * j.[fx_rate], 4),
        ROUND(j.[amount_credit] * j.[fx_rate], 4),
        j.[fx_rate]
    FROM OPENJSON(@p_postings_json)
    WITH (
        [line_number]   INT           '$.line_number',
        [account_id]    BIGINT        '$.account_id',
        [contract_id]   BIGINT        '$.contract_id',
        [partner_id]    BIGINT        '$.partner_id',
        [currency_code] CHAR(3)       '$.currency_code',
        [amount_debit]  DECIMAL(19,4) '$.amount_debit',
        [amount_credit] DECIMAL(19,4) '$.amount_credit',
        [fx_rate]       DECIMAL(14,6) '$.fx_rate'
    ) AS j;

    -- 2. VALIDAR INVARIANTE CONTABLE: Sum(Debit) = Sum(Credit)
    DECLARE @v_sum_debit  DECIMAL(19,4);
    DECLARE @v_sum_credit DECIMAL(19,4);

    SELECT 
        @v_sum_debit  = SUM([functional_debit]),
        @v_sum_credit = SUM([functional_credit])
    FROM @tb_postings;

    IF (@v_sum_debit <> @v_sum_credit) OR (@v_sum_debit IS NULL) OR (@v_sum_debit = 0.0000)
    BEGIN
        DECLARE @v_err NVARCHAR(300) = FORMATMESSAGE(
            N'DESCUADRE CONTABLE: Débitos funcionales (%s) no son iguales a Créditos funcionales (%s).',
            CONVERT(VARCHAR(30), ISNULL(@v_sum_debit, 0)), 
            CONVERT(VARCHAR(30), ISNULL(@v_sum_credit, 0))
        );
        RAISERROR(@v_err, 16, 1);
        ROLLBACK TRANSACTION;
        RETURN -2;
    END;

    -- 3. ENCADENAMIENTO CRIPTOGRÁFICO SHA-256
    DECLARE @v_prev_hash VARBINARY(32);
    SELECT TOP 1 @v_prev_hash = [entry_hash] 
    FROM [oltp].[journal_entry] 
    ORDER BY [journal_entry_id] DESC;

    IF @v_prev_hash IS NULL
        SET @v_prev_hash = 0x0000000000000000000000000000000000000000000000000000000000000000;

    DECLARE @v_uuid UNIQUEIDENTIFIER = NEWID();
    DECLARE @v_now DATETIME2(0) = SYSDATETIME();

    DECLARE @v_entry_hash VARBINARY(32) = HASHBYTES('SHA2_256', 
        CONCAT(
            CONVERT(VARCHAR(36), @v_uuid), '|',
            CONVERT(VARCHAR(19), @v_now, 120), '|',
            @p_transaction_code, '|',
            CONVERT(VARCHAR(30), @v_sum_debit), '|',
            CONVERT(VARCHAR(64), @v_prev_hash, 1)
        )
    );

    -- Insertar Cabecera ya validada como balanceada (is_balanced = 1)
    INSERT INTO [oltp].[journal_entry] (
        [entry_uuid], [transaction_date], [posting_date], [source_system],
        [transaction_code], [description], [is_balanced], [is_reversed],
        [prev_entry_hash], [entry_hash]
    )
    VALUES (
        @v_uuid, @v_now, @p_posting_date, @p_source_system,
        @p_transaction_code, @p_description, 1, 0,
        @v_prev_hash, @v_entry_hash
    );

    SET @o_journal_entry_id = SCOPE_IDENTITY();

    -- Insertar Líneas Multi-Leg con firma individual por fila
    INSERT INTO [oltp].[ledger_posting] (
        [journal_entry_id], [line_number], [account_id], [contract_id],
        [partner_id], [currency_code], [amount_debit], [amount_credit],
        [functional_debit], [functional_credit], [fx_rate], [row_hash]
    )
    SELECT 
        @o_journal_entry_id,
        p.[line_number],
        p.[account_id],
        p.[contract_id],
        p.[partner_id],
        p.[currency_code],
        p.[amount_debit],
        p.[amount_credit],
        p.[functional_debit],
        p.[functional_credit],
        p.[fx_rate],
        HASHBYTES('SHA2_256', 
            CONCAT(@o_journal_entry_id, '|', p.[line_number], '|', p.[account_id], '|', p.[amount_debit], '|', p.[amount_credit])
        )
    FROM @tb_postings p;

    COMMIT TRANSACTION;
    RETURN 0;
END;
GO

-- 5. PROCEDIMIENTO DE REVERSIÓN CONTABLE COMPENSATORIA (Compensating Entry)
CREATE OR ALTER PROCEDURE [oltp].[usp_reverse_journal_entry]
    @p_original_entry_id BIGINT,
    @p_reversal_reason   NVARCHAR(255),
    @o_new_reversal_id   BIGINT OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    IF NOT EXISTS (SELECT 1 FROM [oltp].[journal_entry] WHERE [journal_entry_id] = @p_original_entry_id)
    BEGIN
        RAISERROR(N'El asiento contable original especificado no existe.', 16, 1);
        ROLLBACK TRANSACTION;
        RETURN -1;
    END;

    -- Construir JSON con las líneas invertidas (Débito pasa a Crédito y viceversa)
    DECLARE @v_reversal_json NVARCHAR(MAX);
    SELECT @v_reversal_json = (
        SELECT 
            [line_number],
            [account_id],
            [contract_id],
            [partner_id],
            [currency_code],
            [amount_credit] AS [amount_debit],  -- Inversión contable
            [amount_debit]  AS [amount_credit], -- Inversión contable
            [fx_rate]
        FROM [oltp].[ledger_posting]
        WHERE [journal_entry_id] = @p_original_entry_id
        FOR JSON PATH
    );

    DECLARE @v_source_system VARCHAR(30);
    DECLARE @v_desc NVARCHAR(255);
    
    SELECT 
        @v_source_system = [source_system],
        @v_desc = CONCAT(N'REVERSION Asiento #', @p_original_entry_id, N': ', @p_reversal_reason)
    FROM [oltp].[journal_entry]
    WHERE [journal_entry_id] = @p_original_entry_id;

    -- Registrar el nuevo asiento compensatorio
    EXEC [oltp].[usp_register_journal_entry]
        @p_source_system    = @v_source_system,
        @p_transaction_code = 'REVERSION_CONTABLE',
        @p_description      = @v_desc,
        @p_posting_date     = CAST(GETDATE() AS DATE),
        @p_postings_json    = @v_reversal_json,
        @o_journal_entry_id = @o_new_reversal_id OUTPUT;

    COMMIT TRANSACTION;
    RETURN 0;
END;
GO

-- 6. PROCEDIMIENTO DE VERIFICACIÓN CRIPTOGRÁFICA DE INTEGRIDAD DEL LEDGER
CREATE OR ALTER PROCEDURE [oltp].[usp_verify_ledger_integrity]
AS
BEGIN
    SET NOCOUNT ON;

    WITH OrderedEntries AS (
        SELECT 
            [journal_entry_id],
            [entry_uuid],
            [transaction_date],
            [transaction_code],
            [prev_entry_hash],
            [entry_hash],
            LAG([entry_hash], 1, 0x0000000000000000000000000000000000000000000000000000000000000000) 
                OVER (ORDER BY [journal_entry_id] ASC) AS [expected_prev_hash]
        FROM [oltp].[journal_entry]
    )
    SELECT 
        [journal_entry_id],
        [entry_uuid],
        [transaction_date],
        [transaction_code],
        CASE 
            WHEN [prev_entry_hash] = [expected_prev_hash] THEN 'CADENA_VALIDA'
            ELSE 'ALTERACION_DETECTADA_EN_HASH'
        END AS [estado_integridad]
    FROM OrderedEntries
    ORDER BY [journal_entry_id] ASC;
END;
GO
