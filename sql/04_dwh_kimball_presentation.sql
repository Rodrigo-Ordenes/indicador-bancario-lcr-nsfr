-- =============================================================================
-- 04_dwh_kimball_presentation.sql
-- =============================================================================
-- Capa 4: Data Warehouse & Data Marts Dimensionales (Ralph Kimball)
-- Implementa:
--   - Granularidad atómica en tablas de hechos
--   - Desacople de atributos volátiles: Minidimensión de riesgo (dwh.dim_risk_band)
--   - Dimensión conformada de socios (dwh.dim_partner - SCD2 demográfico)
--   - Dimensión de fecha y vistas de rol (Role-Playing Views)
--   - Particionamiento mensual por esquema
--   - Tablas de hechos con Clustered Columnstore Indexes (CCI):
--     * fact_account_balance_snapshot (Provisiones C46 y Liquidez C55)
--     * fact_contractual_cashflows (Brechas C54, Flujos C56, LCR, NSFR, IRRBB)
-- =============================================================================

USE [CoreBanking];
GO

-- 1. ESQUEMA Y FUNCIÓN DE PARTICIONAMIENTO MENSUAL
IF NOT EXISTS (SELECT 1 FROM sys.partition_functions WHERE name = N'pf_monthly_snapshot')
BEGIN
    CREATE PARTITION FUNCTION [pf_monthly_snapshot] (INT)
    AS RANGE RIGHT FOR VALUES (
        20240131, 20240229, 20240331, 20240430, 20240531, 20240630, 
        20240731, 20240831, 20240930, 20241031, 20241130, 20241231,
        20250131, 20250228, 20250331, 20250430, 20250531, 20250630,
        20250731, 20250831, 20250930, 20251031, 20251130, 20251231,
        20260131, 20260228, 20260331, 20260430, 20260531, 20260630
    );
END;
GO

IF NOT EXISTS (SELECT 1 FROM sys.partition_schemes WHERE name = N'ps_monthly_snapshot')
BEGIN
    CREATE PARTITION SCHEME [ps_monthly_snapshot]
    AS PARTITION [pf_monthly_snapshot]
    ALL TO ([PRIMARY]);
END;
GO

-- 2. DIMENSIÓN CONFORMADA DE SOCIO: dwh.dim_partner
IF OBJECT_ID(N'dwh.fact_account_balance_snapshot', N'U') IS NOT NULL DROP TABLE [dwh].[fact_account_balance_snapshot];
IF OBJECT_ID(N'dwh.fact_contractual_cashflows', N'U') IS NOT NULL DROP TABLE [dwh].[fact_contractual_cashflows];
IF OBJECT_ID(N'dwh.dim_partner', N'U') IS NOT NULL DROP TABLE [dwh].[dim_partner];
GO

CREATE TABLE [dwh].[dim_partner] (
    [partner_key]            INT IDENTITY(1,1) NOT NULL,
    [partner_id]             BIGINT            NOT NULL, -- Clave Natural
    [tax_id]                 VARCHAR(20)       NOT NULL, -- RUT / Tax Identifier
    [full_name]              NVARCHAR(150)     NOT NULL,
    [partner_type]           VARCHAR(20)       NOT NULL, -- PERSONA_NATURAL, PERSONA_JURIDICA
    [segment_name]           VARCHAR(30)       NOT NULL, -- BANCA_PERSONAS, PYME, CORPORATIVA
    [economic_activity_code] VARCHAR(10)       NOT NULL, -- Clasificación CMF / SII
    
    -- Control SCD Tipo 2
    [is_current]             BIT               NOT NULL CONSTRAINT [DF_dim_partner_curr] DEFAULT (1),
    [effective_start_date]   DATE              NOT NULL,
    [effective_end_date]     DATE              NOT NULL CONSTRAINT [DF_dim_partner_end] DEFAULT ('9999-12-31'),
    CONSTRAINT [PK_dim_partner] PRIMARY KEY CLUSTERED ([partner_key] ASC)
);
GO

CREATE NONCLUSTERED INDEX [IX_dim_partner_lookup] 
ON [dwh].[dim_partner] ([partner_id] ASC, [is_current] ASC);
GO

-- 3. MINIDIMENSIÓN DE RIESGO: dwh.dim_risk_band
IF OBJECT_ID(N'dwh.dim_risk_band', N'U') IS NOT NULL DROP TABLE [dwh].[dim_risk_band];
GO

CREATE TABLE [dwh].[dim_risk_band] (
    [risk_band_key]          INT IDENTITY(1,1) NOT NULL,
    [cmf_rating_category]    VARCHAR(5)        NOT NULL, -- A1..A6, B1..B4, C1..C6, D1, D2
    [regulatory_status]      VARCHAR(20)       NOT NULL, -- NORMAL, SUBESTANDAR, INCUMPLIMIENTO
    [dpd_bucket]             VARCHAR(20)       NOT NULL, -- 0 DÍAS, 1-30 DÍAS, 31-60 DÍAS, >90 DÍAS
    [min_dpd]                INT               NOT NULL,
    [max_dpd]                INT               NOT NULL,
    [provision_rate]         DECIMAL(6,4)      NOT NULL, -- Tasa de provisión mínima exigida por CMF
    [ltv_bucket]             VARCHAR(20)       NOT NULL, -- <=60%, 60-80%, >80%, NO_APLICA
    CONSTRAINT [PK_dim_risk_band] PRIMARY KEY CLUSTERED ([risk_band_key] ASC)
);
GO

-- Catálogo de combinaciones discretas
INSERT INTO [dwh].[dim_risk_band] (
    [cmf_rating_category], [regulatory_status], [dpd_bucket], [min_dpd], [max_dpd], [provision_rate], [ltv_bucket]
)
VALUES
('A1', 'NORMAL',         '0 DÍAS',       0,   0,   0.0010, '<=60%'),
('A2', 'NORMAL',         '0 DÍAS',       0,   0,   0.0050, '<=60%'),
('A3', 'NORMAL',         '1-30 DÍAS',    1,   30,  0.0100, '60-80%'),
('B1', 'SUBESTANDAR',    '31-60 DÍAS',   31,  60,  0.0400, '60-80%'),
('B2', 'SUBESTANDAR',    '61-90 DÍAS',   61,  90,  0.1000, '>80%'),
('C1', 'INCUMPLIMIENTO', '>90 DÍAS',     91,  180, 0.2500, '>80%'),
('D1', 'INCUMPLIMIENTO', '>90 DÍAS',     181, 365, 0.6000, '>80%'),
('D2', 'INCUMPLIMIENTO', '>90 DÍAS',     366, 9999,1.0000, '>80%');
GO

-- 4. DIMENSIÓN DE FECHA Y ROLE-PLAYING VIEWS
IF OBJECT_ID(N'dwh.dim_date', N'U') IS NOT NULL DROP TABLE [dwh].[dim_date];
GO

CREATE TABLE [dwh].[dim_date] (
    [date_key]               INT         NOT NULL, -- YYYYMMDD
    [full_date]              DATE        NOT NULL,
    [year]                   SMALLINT    NOT NULL,
    [month_number]           TINYINT     NOT NULL,
    [month_name]             VARCHAR(20) NOT NULL,
    [quarter]                TINYINT     NOT NULL,
    [is_month_end]           BIT         NOT NULL,
    [is_business_day]        BIT         NOT NULL,
    [fiscal_year]            SMALLINT    NOT NULL,
    CONSTRAINT [PK_dim_date] PRIMARY KEY CLUSTERED ([date_key] ASC)
);
GO

-- Generador de fechas 2024-2026
;WITH DateGen AS (
    SELECT CAST('2024-01-01' AS DATE) AS [dt]
    UNION ALL
    SELECT DATEADD(DAY, 1, [dt])
    FROM DateGen
    WHERE [dt] < '2026-12-31'
)
INSERT INTO [dwh].[dim_date] (
    [date_key], [full_date], [year], [month_number], [month_name],
    [quarter], [is_month_end], [is_business_day], [fiscal_year]
)
SELECT 
    CAST(CONVERT(VARCHAR(8), [dt], 112) AS INT),
    [dt],
    YEAR([dt]),
    MONTH([dt]),
    DATENAME(MONTH, [dt]),
    DATEPART(QUARTER, [dt]),
    CASE WHEN [dt] = EOMONTH([dt]) THEN 1 ELSE 0 END,
    CASE WHEN DATEPART(WEEKDAY, [dt]) IN (1, 7) THEN 0 ELSE 1 END,
    YEAR([dt])
FROM DateGen
OPTION (MAXRECURSION 1200);
GO

-- Role-Playing Views
CREATE OR ALTER VIEW [dwh].[vw_dim_disbursement_date] AS
SELECT [date_key] AS [disbursement_date_key], [full_date] AS [disbursement_date], [year] AS [disbursement_year], [month_name] AS [disbursement_month]
FROM [dwh].[dim_date];
GO

CREATE OR ALTER VIEW [dwh].[vw_dim_maturity_date] AS
SELECT [date_key] AS [maturity_date_key], [full_date] AS [maturity_date], [year] AS [maturity_year], [month_name] AS [maturity_month]
FROM [dwh].[dim_date];
GO

CREATE OR ALTER VIEW [dwh].[vw_dim_repricing_date] AS
SELECT [date_key] AS [repricing_date_key], [full_date] AS [repricing_date], [year] AS [repricing_year], [month_name] AS [repricing_month]
FROM [dwh].[dim_date];
GO

-- 5. TABLA DE HECHOS: fact_account_balance_snapshot CON CLUSTERED COLUMNSTORE INDEX
CREATE TABLE [dwh].[fact_account_balance_snapshot] (
    [snapshot_date_key]        INT           NOT NULL, -- FK a dim_date (YYYYMMDD)
    [contract_id]              BIGINT        NOT NULL, -- Clave degenerada
    [partner_key]              INT           NOT NULL, -- FK a dim_partner
    [risk_band_key]            INT           NOT NULL, -- FK a dim_risk_band
    [currency_code]            CHAR(3)       NOT NULL,
    
    -- Métricas de Balance
    [current_balance_orig]     DECIMAL(19,4) NOT NULL,
    [current_balance_clp]      DECIMAL(19,4) NOT NULL,
    [fx_rate_to_clp]           DECIMAL(14,6) NOT NULL,
    [accrued_interest_clp]     DECIMAL(19,4) NOT NULL,
    [contractual_rate]         DECIMAL(8,4)  NOT NULL,
    
    -- Provisiones CMF (Normativa C46)
    [regulatory_provision_clp] DECIMAL(19,4) NOT NULL,
    [collateral_value_clp]     DECIMAL(19,4) NOT NULL,
    [net_exposure_clp]         DECIMAL(19,4) NOT NULL,
    
    -- Liquidez Regulatoria (Normativa C55 / Basilea III LCR & NSFR)
    [is_hqla_eligible]         BIT           NOT NULL,
    [hqla_level]               VARCHAR(5)    NOT NULL, -- L1, L2A, L2B, NO_HQLA
    [nsfr_rsf_factor]          DECIMAL(5,4)  NOT NULL, -- Factor de Fondeo Requerido (RSF)
    [nsfr_weighted_rsf_clp]    DECIMAL(19,4) NOT NULL  -- Saldo ponderado RSF
)
ON [ps_monthly_snapshot] ([snapshot_date_key]);
GO

-- Clustered Columnstore Index particionado
CREATE CLUSTERED COLUMNSTORE INDEX [CCI_fact_account_balance_snapshot]
ON [dwh].[fact_account_balance_snapshot]
ON [ps_monthly_snapshot] ([snapshot_date_key]);
GO

-- 6. TABLA DE HECHOS: fact_contractual_cashflows (Brechas C54 / Flujos C56 / IRRBB)
CREATE TABLE [dwh].[fact_contractual_cashflows] (
    [position_date_key]        INT           NOT NULL, -- Fecha de cálculo del balance
    [cashflow_date_key]        INT           NOT NULL, -- Fecha proyectada del flujo
    [contract_id]              BIGINT        NOT NULL,
    [partner_key]              INT           NOT NULL,
    [risk_band_key]            INT           NOT NULL,
    [currency_code]            CHAR(3)       NOT NULL,
    
    -- Clasificación de Brechas de Repreciación / Liquidez (C54 / C56)
    [time_bucket_cmf]          VARCHAR(20)   NOT NULL, -- 1-30D, 31-90D, 91-180D, 181-365D, 1-2Y, >2Y
    [is_interest_bearing]      BIT           NOT NULL,
    
    -- Flujos Proyectados
    [principal_cashflow]       DECIMAL(19,4) NOT NULL,
    [interest_cashflow]        DECIMAL(19,4) NOT NULL,
    [total_cashflow]           DECIMAL(19,4) NOT NULL,
    [discount_factor]          DECIMAL(10,8) NOT NULL,
    [present_value_clp]        DECIMAL(19,4) NOT NULL,
    
    -- Factores Basilea III ALM
    [lcr_outflow_rate]         DECIMAL(5,4)  NOT NULL, -- Tasa de fuga para pasivos
    [lcr_inflow_rate]          DECIMAL(5,4)  NOT NULL, -- Tasa de cobro para activos
    [nsfr_asf_factor]          DECIMAL(5,4)  NOT NULL, -- Fondeo Estable Disponible
    [nsfr_rsf_factor]          DECIMAL(5,4)  NOT NULL  -- Fondeo Estable Requerido
)
ON [ps_monthly_snapshot] ([position_date_key]);
GO

CREATE CLUSTERED COLUMNSTORE INDEX [CCI_fact_contractual_cashflows]
ON [dwh].[fact_contractual_cashflows]
ON [ps_monthly_snapshot] ([position_date_key]);
GO
