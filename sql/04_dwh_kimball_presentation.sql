-- =============================================================================
-- 04_dwh_kimball_presentation.sql
-- =============================================================================
-- Capa 4: área de presentación dimensional (Kimball, The Data Warehouse Toolkit,
-- caps. 7 y 9: libro mayor y servicios financieros).
--
-- Triage de dimensiones (cap. 9): la cuenta/contrato NO absorbe atributos que
-- cambian con frecuencia; se separan en dimensiones propias para que el SCD tipo 2
-- no multiplique la dimensión de contratos ("mushrooming"):
--   dim_account_contract (atributos estables, SCD1) | dim_partner (SCD2 + grupo
--   económico) | dim_product | dim_branch | dim_gl_account | dim_date (+ vistas
--   role-playing) | minidimensión dim_risk_band | dim_liquidity_treatment
--   (catálogo Basilea III) | dim_time_bucket (bandas CMF).
--
-- Hechos complementarios (cap. 7):
--   fact_account_balance_snapshot  : snapshot periódico diario; incluye
--                                    restatement_seq para reexpresiones auditables.
--   fact_gl_transaction_postings   : grano transaccional con account_sequence_number
--                                    y is_final_daily_tx (fig. 9.5) para saldos
--                                    point-in-time y drill-down al asiento.
--   fact_contractual_cashflows     : flujos proyectados por banda (LCR, NSFR, C54, C56).
-- Las tres tablas de hechos son Clustered Columnstore particionadas por mes.
-- La integridad referencial de los hechos la garantiza el ETL (lookup de claves
-- sustitutas); no se declaran FOREIGN KEY físicas para no impedir SWITCH PARTITION.
-- =============================================================================

USE [CoreBanking];
GO

DROP VIEW IF EXISTS [dwh].[vw_dim_snapshot_date];
DROP VIEW IF EXISTS [dwh].[vw_dim_origination_date];
DROP VIEW IF EXISTS [dwh].[vw_dim_maturity_date];
DROP VIEW IF EXISTS [dwh].[vw_dim_repricing_date];
DROP VIEW IF EXISTS [dwh].[vw_dim_cashflow_date];
DROP VIEW IF EXISTS [dwh].[vw_dim_posting_date];
DROP VIEW IF EXISTS [dwh].[vw_dim_value_date];
DROP TABLE IF EXISTS [dwh].[fact_contractual_cashflows];
DROP TABLE IF EXISTS [dwh].[fact_gl_transaction_postings];
DROP TABLE IF EXISTS [dwh].[fact_account_balance_snapshot];
DROP TABLE IF EXISTS [dwh].[yield_curve_point];
DROP TABLE IF EXISTS [dwh].[regulatory_parameter];
DROP TABLE IF EXISTS [dwh].[behavioral_ladder_profile];
DROP TABLE IF EXISTS [dwh].[dim_time_bucket];
DROP TABLE IF EXISTS [dwh].[dim_liquidity_treatment];
DROP TABLE IF EXISTS [dwh].[dim_risk_band];
DROP TABLE IF EXISTS [dwh].[dim_account_contract];
DROP TABLE IF EXISTS [dwh].[dim_gl_account];
DROP TABLE IF EXISTS [dwh].[dim_branch];
DROP TABLE IF EXISTS [dwh].[dim_product];
DROP TABLE IF EXISTS [dwh].[dim_partner];
DROP TABLE IF EXISTS [dwh].[dim_date];
GO

-- =============================================================================
-- 1. DIM_DATE: clave semi-inteligente YYYYMMDD + número de día secuencial
-- =============================================================================
CREATE TABLE [dwh].[dim_date] (
    [date_key]             INT          NOT NULL,   -- YYYYMMDD: ordenable y alineada a la partición mensual
    [full_date]            DATE         NOT NULL,
    [day_number_in_epoch]  INT          NOT NULL,   -- secuencial continuo (aritmética de días sin DATEDIFF)
    [year]                 SMALLINT     NOT NULL,
    [quarter]              TINYINT      NOT NULL,
    [month_number]         TINYINT      NOT NULL,
    [month_name]           VARCHAR(12)  NOT NULL,
    [year_month]           INT          NOT NULL,
    [day_of_month]         TINYINT      NOT NULL,
    [day_of_week]          TINYINT      NOT NULL,   -- ISO: 1 = lunes
    [day_name]             VARCHAR(10)  NOT NULL,
    [is_month_end]         BIT          NOT NULL,
    [is_business_day]      BIT          NOT NULL,
    [fiscal_year]          SMALLINT     NOT NULL,
    CONSTRAINT [PK_dim_date] PRIMARY KEY CLUSTERED ([date_key]),
    CONSTRAINT [UQ_dim_date_full] UNIQUE ([full_date])
);
GO

-- Generación sin recursión (tabla de números por producto cartesiano) 2015-2040.
;WITH n10 AS (SELECT v FROM (VALUES (0),(1),(2),(3),(4),(5),(6),(7),(8),(9)) t(v)),
      nums AS (SELECT a.v + 10 * b.v + 100 * c.v + 1000 * d.v + 10000 * e.v AS n
               FROM n10 a CROSS JOIN n10 b CROSS JOIN n10 c CROSS JOIN n10 d CROSS JOIN n10 e),
      days AS (SELECT DATEADD(DAY, n, CAST('2015-01-01' AS DATE)) AS d FROM nums
               WHERE n <= DATEDIFF(DAY, '2015-01-01', '2040-12-31'))
INSERT INTO [dwh].[dim_date]
SELECT CAST(CONVERT(CHAR(8), d, 112) AS INT), d, DATEDIFF(DAY, '2000-01-01', d), YEAR(d), DATEPART(QUARTER, d), MONTH(d),
       CHOOSE(MONTH(d), 'Enero','Febrero','Marzo','Abril','Mayo','Junio','Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre'),
       YEAR(d) * 100 + MONTH(d), DAY(d),
       ((DATEPART(WEEKDAY, d) + @@DATEFIRST - 2) % 7) + 1,
       CHOOSE(((DATEPART(WEEKDAY, d) + @@DATEFIRST - 2) % 7) + 1, 'Lunes','Martes','Miércoles','Jueves','Viernes','Sábado','Domingo'),
       CASE WHEN d = EOMONTH(d) THEN 1 ELSE 0 END,
       CASE WHEN ((DATEPART(WEEKDAY, d) + @@DATEFIRST - 2) % 7) + 1 >= 6 THEN 0
            WHEN FORMAT(d, 'MM-dd') IN ('01-01','05-01','05-21','06-29','08-15','09-18','09-19','10-12','11-01','12-08','12-25') THEN 0
            ELSE 1 END,
       YEAR(d)
FROM days;
GO

-- Vistas role-playing: una sola dimensión física, múltiples roles semánticos.
CREATE VIEW [dwh].[vw_dim_snapshot_date]    AS SELECT [date_key] AS [snapshot_date_key],    [full_date] AS [snapshot_date],    [year_month] AS [snapshot_year_month], [is_month_end], [is_business_day] FROM [dwh].[dim_date];
GO
CREATE VIEW [dwh].[vw_dim_origination_date] AS SELECT [date_key] AS [origination_date_key], [full_date] AS [origination_date], [year] AS [origination_year], [month_name] AS [origination_month] FROM [dwh].[dim_date];
GO
CREATE VIEW [dwh].[vw_dim_maturity_date]    AS SELECT [date_key] AS [maturity_date_key],    [full_date] AS [maturity_date],    [year] AS [maturity_year],    [month_name] AS [maturity_month] FROM [dwh].[dim_date];
GO
CREATE VIEW [dwh].[vw_dim_repricing_date]   AS SELECT [date_key] AS [repricing_date_key],   [full_date] AS [repricing_date],   [year] AS [repricing_year],   [month_name] AS [repricing_month] FROM [dwh].[dim_date];
GO
CREATE VIEW [dwh].[vw_dim_cashflow_date]    AS SELECT [date_key] AS [cashflow_date_key],    [full_date] AS [cashflow_date],    [is_business_day] AS [cashflow_is_business_day] FROM [dwh].[dim_date];
GO
CREATE VIEW [dwh].[vw_dim_posting_date]     AS SELECT [date_key] AS [posting_date_key],     [full_date] AS [posting_date],     [year_month] AS [posting_year_month] FROM [dwh].[dim_date];
GO
CREATE VIEW [dwh].[vw_dim_value_date]       AS SELECT [date_key] AS [value_date_key],       [full_date] AS [value_date],       [year_month] AS [value_year_month] FROM [dwh].[dim_date];
GO

-- =============================================================================
-- 2. DIMENSIONES DE NEGOCIO
-- =============================================================================
CREATE TABLE [dwh].[dim_partner] (
    [partner_key]               INT IDENTITY(1,1) NOT NULL,
    [partner_id]                BIGINT            NOT NULL,   -- clave natural
    [tax_id]                    VARCHAR(20)       NOT NULL,
    [full_name]                 NVARCHAR(150)     NOT NULL,
    [partner_type]              VARCHAR(20)       NOT NULL,
    [segment_name]              VARCHAR(30)       NOT NULL,
    [economic_activity]         VARCHAR(20)       NOT NULL,
    [household_group_id]        BIGINT            NOT NULL,   -- grupo económico / hogar (concentración y límites)
    [household_group_name]      NVARCHAR(150)     NOT NULL,
    [is_financial_institution]  BIT               NOT NULL,   -- determina tasas de salida LCR y factores ASF
    [is_current]                BIT               NOT NULL CONSTRAINT [DF_dim_partner_current] DEFAULT (1),
    [effective_start_date]      DATE              NOT NULL,
    [effective_end_date]        DATE              NOT NULL CONSTRAINT [DF_dim_partner_end] DEFAULT ('9999-12-31'),
    CONSTRAINT [PK_dim_partner] PRIMARY KEY CLUSTERED ([partner_key])
);
CREATE UNIQUE NONCLUSTERED INDEX [UX_dim_partner_current] ON [dwh].[dim_partner] ([partner_id]) WHERE [is_current] = 1;
GO

CREATE TABLE [dwh].[dim_product] (
    [product_key]          INT IDENTITY(1,1) NOT NULL,
    [product_code]         VARCHAR(30)       NOT NULL,
    [product_name]         NVARCHAR(100)     NOT NULL,
    [product_family]       VARCHAR(30)       NOT NULL,
    [balance_side]         VARCHAR(12)       NOT NULL,
    [cmf_portfolio_type]   VARCHAR(3)        NOT NULL,
    [is_interest_bearing]  BIT               NOT NULL,
    [is_loan]              BIT               NOT NULL,
    CONSTRAINT [PK_dim_product] PRIMARY KEY CLUSTERED ([product_key]),
    CONSTRAINT [UQ_dim_product_code] UNIQUE ([product_code]),
    CONSTRAINT [CK_dim_product_side] CHECK ([balance_side] IN ('ASSET','LIABILITY','EQUITY','OFF_BALANCE'))
);
INSERT INTO [dwh].[dim_product] ([product_code], [product_name], [product_family], [balance_side], [cmf_portfolio_type], [is_interest_bearing], [is_loan]) VALUES
('BCCH_RESERVA', N'Reserva en Banco Central', 'TESORERIA', 'ASSET', 'N/A', 0, 0),
('CAJA', N'Caja billetes y monedas', 'TESORERIA', 'ASSET', 'N/A', 0, 0),
('BONO_SOB', N'Bonos de Tesorería BTP/BTU', 'INVERSIONES', 'ASSET', 'N/A', 1, 0),
('BONO_BCCH', N'Pagarés y bonos Banco Central', 'INVERSIONES', 'ASSET', 'N/A', 1, 0),
('BONO_BANC', N'Bonos bancarios AA+', 'INVERSIONES', 'ASSET', 'N/A', 1, 0),
('BONO_CORP', N'Bonos corporativos AAA', 'INVERSIONES', 'ASSET', 'N/A', 1, 0),
('ACCION_IPSA', N'Acciones IPSA', 'INVERSIONES', 'ASSET', 'N/A', 0, 0),
('COM_CREDITO', N'Crédito comercial', 'COLOCACIONES', 'ASSET', 'C', 1, 1),
('COM_LINEA', N'Línea de crédito comercial comprometida', 'COLOCACIONES', 'ASSET', 'C', 1, 1),
('HIPOTECARIO', N'Mutuo hipotecario vivienda', 'COLOCACIONES', 'ASSET', 'H', 1, 1),
('CONSUMO', N'Crédito de consumo', 'COLOCACIONES', 'ASSET', 'N', 1, 1),
('OTROS_ACT', N'Activo fijo, intangibles y otros', 'OTROS', 'ASSET', 'N/A', 0, 0),
('TRANSITO', N'Partidas en tránsito', 'OTROS', 'ASSET', 'N/A', 0, 0),
('VISTA_PERS', N'Depósitos a la vista personas', 'CAPTACIONES', 'LIABILITY', 'N/A', 0, 0),
('DP_PERS', N'Depósitos a plazo personas', 'CAPTACIONES', 'LIABILITY', 'N/A', 1, 0),
('CTACTE_EMP', N'Cuenta corriente empresas', 'CAPTACIONES', 'LIABILITY', 'N/A', 0, 0),
('DP_INST', N'Depósito a plazo institucional', 'CAPTACIONES', 'LIABILITY', 'N/A', 1, 0),
('EFECTOS_COMERCIO', N'Efectos de comercio', 'FONDEO_MAYORISTA', 'LIABILITY', 'N/A', 1, 0),
('OBLIG_BANCOS_EXT', N'Obligaciones con bancos del exterior', 'FONDEO_MAYORISTA', 'LIABILITY', 'N/A', 1, 0),
('BONO_SENIOR', N'Bonos senior', 'FONDEO_MAYORISTA', 'LIABILITY', 'N/A', 1, 0),
('BONO_SUBORD', N'Bonos subordinados', 'FONDEO_MAYORISTA', 'LIABILITY', 'N/A', 1, 0),
('OTROS_PAS', N'Otros pasivos', 'OTROS', 'LIABILITY', 'N/A', 0, 0),
('CAPITAL', N'Capital pagado y reservas', 'PATRIMONIO', 'EQUITY', 'N/A', 0, 0),
('LINEA_NO_GIRADA', N'Líneas comprometidas no giradas (contingente)', 'CONTINGENTES', 'OFF_BALANCE', 'C', 0, 0);
GO

CREATE TABLE [dwh].[dim_branch] (
    [branch_key]   INT IDENTITY(1,1) NOT NULL,
    [branch_code]  VARCHAR(20)       NOT NULL,
    [branch_name]  NVARCHAR(100)     NOT NULL,
    [city_name]    NVARCHAR(60)      NOT NULL,
    [region_name]  NVARCHAR(60)      NOT NULL,
    CONSTRAINT [PK_dim_branch] PRIMARY KEY CLUSTERED ([branch_key]),
    CONSTRAINT [UQ_dim_branch_code] UNIQUE ([branch_code])
);
INSERT INTO [dwh].[dim_branch] ([branch_code], [branch_name], [city_name], [region_name]) VALUES
('CL-SCL-01', N'Casa Matriz Santiago', N'Santiago', N'Metropolitana'),
('CL-SCL-02', N'Mesa de Dinero', N'Santiago', N'Metropolitana'),
('CL-VAP-01', N'Sucursal Valparaíso', N'Valparaíso', N'Valparaíso'),
('CL-CCP-01', N'Sucursal Concepción', N'Concepción', N'Biobío');
GO

CREATE TABLE [dwh].[dim_gl_account] (
    [gl_account_key]         INT IDENTITY(1,1) NOT NULL,
    [account_id]             BIGINT            NOT NULL,
    [account_code]           VARCHAR(20)       NOT NULL,
    [account_name]           NVARCHAR(150)     NOT NULL,
    [account_class]          VARCHAR(10)       NOT NULL,
    [normal_balance]         CHAR(1)           NOT NULL,
    [is_contract_principal]  BIT               NOT NULL,
    CONSTRAINT [PK_dim_gl_account] PRIMARY KEY CLUSTERED ([gl_account_key]),
    CONSTRAINT [UQ_dim_gl_account_id] UNIQUE ([account_id])
);
GO

-- Contrato: sólo atributos estables (SCD tipo 1). Rating, mora y LTV viven en la minidimensión.
CREATE TABLE [dwh].[dim_account_contract] (
    [contract_key]          INT IDENTITY(1,1) NOT NULL,
    [contract_id]           BIGINT            NOT NULL,
    [contract_number]       VARCHAR(50)       NOT NULL,
    [product_key]           INT               NOT NULL,
    [branch_key]            INT               NOT NULL,
    [origination_date_key]  INT               NOT NULL,
    [maturity_date_key]     INT               NULL,
    [currency_code]         CHAR(3)           NOT NULL,
    [source_system]         VARCHAR(30)       NOT NULL,
    CONSTRAINT [PK_dim_account_contract] PRIMARY KEY CLUSTERED ([contract_key]),
    CONSTRAINT [UQ_dim_account_contract_id] UNIQUE ([contract_id]),
    CONSTRAINT [FK_dim_contract_product] FOREIGN KEY ([product_key]) REFERENCES [dwh].[dim_product] ([product_key]),
    CONSTRAINT [FK_dim_contract_branch] FOREIGN KEY ([branch_key]) REFERENCES [dwh].[dim_branch] ([branch_key])
);
GO

-- Minidimensión de riesgo: combinaciones discretas de categoría CMF, tramo de mora y LTV.
CREATE TABLE [dwh].[dim_risk_band] (
    [risk_band_key]        INT IDENTITY(1,1) NOT NULL,
    [cmf_rating_category]  VARCHAR(4)        NOT NULL,
    [regulatory_status]    VARCHAR(20)       NOT NULL,
    [dpd_bucket]           VARCHAR(20)       NOT NULL,
    [min_dpd]              INT               NOT NULL,
    [max_dpd]              INT               NOT NULL,
    [provision_rate]       DECIMAL(12,6)     NOT NULL,
    [ltv_bucket]           VARCHAR(20)       NOT NULL,
    CONSTRAINT [PK_dim_risk_band] PRIMARY KEY CLUSTERED ([risk_band_key]),
    CONSTRAINT [UQ_dim_risk_band_category] UNIQUE ([cmf_rating_category])
);
INSERT INTO [dwh].[dim_risk_band] ([cmf_rating_category], [regulatory_status], [dpd_bucket], [min_dpd], [max_dpd], [provision_rate], [ltv_bucket]) VALUES
('A1', 'NORMAL',         '0 DIAS',     0,   0,    0.001000, '<=60%'),
('A2', 'NORMAL',         '0 DIAS',     0,   0,    0.005000, '<=60%'),
('A3', 'NORMAL',         '1-30 DIAS',  1,   30,   0.010000, '60-80%'),
('B1', 'SUBESTANDAR',    '31-60 DIAS', 31,  60,   0.040000, '60-80%'),
('B2', 'SUBESTANDAR',    '61-90 DIAS', 61,  90,   0.100000, '>80%'),
('C1', 'INCUMPLIMIENTO', '>90 DIAS',   91,  180,  0.250000, '>80%'),
('D1', 'INCUMPLIMIENTO', '>90 DIAS',   181, 365,  0.600000, '>80%'),
('D2', 'INCUMPLIMIENTO', '>90 DIAS',   366, 9999, 1.000000, '>80%'),
('NA', 'NO_APLICA',      'NO_APLICA',  0,   0,    0.000000, 'NO_APLICA');
GO

-- Catálogo de tratamientos Basilea III (BCBS 238 LCR / BCBS 295 NSFR).
-- Los factores son atributos de la dimensión, no del hecho: un cambio normativo se
-- versiona aquí sin reprocesar hechos.
CREATE TABLE [dwh].[dim_liquidity_treatment] (
    [liquidity_treatment_key]  INT IDENTITY(1,1) NOT NULL,
    [treatment_code]           VARCHAR(40)       NOT NULL,
    [lcr_component]            VARCHAR(8)        NOT NULL,
    [lcr_category]             NVARCHAR(100)     NOT NULL,
    [hqla_level]               VARCHAR(8)        NOT NULL,
    [hqla_haircut]             DECIMAL(12,6)     NOT NULL,
    [lcr_basis]                VARCHAR(15)       NOT NULL,
    [lcr_rate]                 DECIMAL(12,6)     NOT NULL,
    [undrawn_runoff_rate]      DECIMAL(12,6)     NOT NULL,
    [nsfr_side]                VARCHAR(4)        NOT NULL,
    [nsfr_category]            NVARCHAR(100)     NOT NULL,
    [nsfr_factor_lt_6m]        DECIMAL(12,6)     NOT NULL,
    [nsfr_factor_6m_1y]        DECIMAL(12,6)     NOT NULL,
    [nsfr_factor_ge_1y]        DECIMAL(12,6)     NOT NULL,
    [undrawn_rsf_factor]       DECIMAL(12,6)     NOT NULL,
    CONSTRAINT [PK_dim_liquidity_treatment] PRIMARY KEY CLUSTERED ([liquidity_treatment_key]),
    CONSTRAINT [UQ_dim_liquidity_treatment_code] UNIQUE ([treatment_code]),
    CONSTRAINT [CK_dim_lt_component] CHECK ([lcr_component] IN ('ALAC','OUTFLOW','INFLOW','NONE')),
    CONSTRAINT [CK_dim_lt_level] CHECK ([hqla_level] IN ('L1','L2A','L2B','NO_HQLA')),
    CONSTRAINT [CK_dim_lt_basis] CHECK ([lcr_basis] IN ('MARKET_VALUE','BALANCE','CASHFLOW_30D','NONE')),
    CONSTRAINT [CK_dim_lt_nsfr] CHECK ([nsfr_side] IN ('ASF','RSF','NONE'))
);
INSERT INTO [dwh].[dim_liquidity_treatment] ([treatment_code], [lcr_component], [lcr_category], [hqla_level], [hqla_haircut],
    [lcr_basis], [lcr_rate], [undrawn_runoff_rate], [nsfr_side], [nsfr_category], [nsfr_factor_lt_6m], [nsfr_factor_6m_1y],
    [nsfr_factor_ge_1y], [undrawn_rsf_factor]) VALUES
('HQLA_L1_CAJA', 'ALAC', N'Nivel 1: Efectivo y BCCh', 'L1', 0, 'MARKET_VALUE', 1, 0, 'RSF', N'Efectivo y Reservas Centrales (0% RSF)', 0, 0, 0, 0),
('HQLA_L1_BCCH', 'ALAC', N'Nivel 1: Efectivo y BCCh', 'L1', 0, 'MARKET_VALUE', 1, 0, 'RSF', N'Efectivo y Reservas Centrales (0% RSF)', 0, 0, 0, 0),
('HQLA_L1_SOBERANO', 'ALAC', N'Nivel 1: Soberanos 0% Riesgo', 'L1', 0, 'MARKET_VALUE', 1, 0, 'RSF', N'Activos Líquidos Nivel 1 (5% RSF)', 0.05, 0.05, 0.05, 0),
('HQLA_L2A', 'ALAC', N'Nivel 2A: Activos Alta Calidad', 'L2A', 0.15, 'MARKET_VALUE', 1, 0, 'RSF', N'Activos Líquidos Nivel 2A (15% RSF)', 0.15, 0.15, 0.15, 0),
('HQLA_L2B', 'ALAC', N'Nivel 2B: Activos Menor Liquidez', 'L2B', 0.50, 'MARKET_VALUE', 1, 0, 'RSF', N'Activos Líquidos Nivel 2B (50% RSF)', 0.50, 0.50, 0.50, 0),
('INFLOW_HIPOTECARIO', 'INFLOW', N'Entradas de Efectivo (Inflows con Cap 75%)', 'NO_HQLA', 0, 'CASHFLOW_30D', 0.50, 0, 'RSF', N'Créditos con Garantía Residencial (65% RSF)', 0.50, 0.50, 0.65, 0),
('INFLOW_COMERCIAL', 'INFLOW', N'Entradas de Efectivo (Inflows con Cap 75%)', 'NO_HQLA', 0, 'CASHFLOW_30D', 0.50, 0, 'RSF', N'Créditos Comerciales y Corporativos (85% RSF)', 0.50, 0.50, 0.85, 0),
('INFLOW_CONSUMO', 'INFLOW', N'Entradas de Efectivo (Inflows con Cap 75%)', 'NO_HQLA', 0, 'CASHFLOW_30D', 0.50, 0, 'RSF', N'Créditos de Consumo (50%-85% RSF)', 0.50, 0.50, 0.85, 0),
('LINEA_CREDITO_CORP', 'INFLOW', N'Entradas de Efectivo (Inflows con Cap 75%)', 'NO_HQLA', 0, 'CASHFLOW_30D', 0.50, 0.10, 'RSF', N'Créditos Comerciales y Corporativos (85% RSF)', 0.50, 0.50, 0.85, 0.05),
('LINEA_LIQUIDEZ_CORP', 'NONE', N'Compromisos de Liquidez y Crédito', 'NO_HQLA', 0, 'NONE', 0, 0.30, 'NONE', N'Líneas Comprometidas No Utilizadas (5% RSF)', 0, 0, 0, 0.05),
('DEP_MINORISTA_ESTABLE', 'OUTFLOW', N'Depósitos Minoristas Estables', 'NO_HQLA', 0, 'BALANCE', 0.05, 0, 'ASF', N'Depósitos Minoristas Estables (95% ASF)', 0.95, 0.95, 0.95, 0),
('DEP_MINORISTA_MENOS_ESTABLE', 'OUTFLOW', N'Depósitos Minoristas Menos Estables', 'NO_HQLA', 0, 'BALANCE', 0.10, 0, 'ASF', N'Depósitos Minoristas Menos Estables (90% ASF)', 0.90, 0.90, 0.90, 0),
('DEP_MAYORISTA_OPERACIONAL', 'OUTFLOW', N'Depósitos Mayoristas Operacionales', 'NO_HQLA', 0, 'BALANCE', 0.25, 0, 'ASF', N'Fondeo Mayorista Operacional (50% ASF)', 0.50, 0.50, 0.50, 0),
('DEP_MAYORISTA_NO_OPER_FIN', 'OUTFLOW', N'Depósitos Mayoristas No Operacionales', 'NO_HQLA', 0, 'CASHFLOW_30D', 1.00, 0, 'ASF', N'Fondeo Mayorista No Operacional (0%-100% ASF)', 0, 0.50, 1.00, 0),
('FONDEO_MAYORISTA_NO_GARANTIZADO', 'OUTFLOW', N'Fondeo Mayorista No Garantizado', 'NO_HQLA', 0, 'CASHFLOW_30D', 1.00, 0, 'ASF', N'Fondeo Mayorista e Instrumentos de Deuda (0%-100% ASF)', 0, 0.50, 1.00, 0),
('CAPITAL_REGULATORIO', 'NONE', N'No computable LCR', 'NO_HQLA', 0, 'NONE', 0, 0, 'ASF', N'Capital Regulatorio e Instrumentos > 1 Año', 1.00, 1.00, 1.00, 0),
('OTROS_PASIVOS', 'NONE', N'No computable LCR', 'NO_HQLA', 0, 'NONE', 0, 0, 'ASF', N'Otros Pasivos (0% ASF)', 0, 0, 0, 0),
('OTROS_ACTIVOS', 'NONE', N'No computable LCR', 'NO_HQLA', 0, 'NONE', 0, 0, 'RSF', N'Otros Activos (100% RSF)', 1.00, 1.00, 1.00, 0);
GO

-- Bandas temporales CMF (C54 brechas / C56 IRRBB) con su banda NSFR equivalente.
CREATE TABLE [dwh].[dim_time_bucket] (
    [time_bucket_key]     INT          NOT NULL,
    [bucket_code]         CHAR(3)      NOT NULL,
    [bucket_label]        NVARCHAR(20) NOT NULL,
    [min_days]            INT          NOT NULL,
    [max_days]            INT          NOT NULL,
    [midpoint_days]       INT          NOT NULL,
    [nsfr_maturity_band]  VARCHAR(6)   NOT NULL,
    [sort_order]          TINYINT      NOT NULL,
    CONSTRAINT [PK_dim_time_bucket] PRIMARY KEY CLUSTERED ([time_bucket_key]),
    CONSTRAINT [UQ_dim_time_bucket_code] UNIQUE ([bucket_code])
);
INSERT INTO [dwh].[dim_time_bucket] VALUES
(1, 'B01', N'1-7 días',     0,    7,     4,    'LT_6M', 1),
(2, 'B02', N'8-30 días',    8,    30,    19,   'LT_6M', 2),
(3, 'B03', N'31-90 días',   31,   90,    60,   'LT_6M', 3),
(4, 'B04', N'91-180 días',  91,   180,   135,  'LT_6M', 4),
(5, 'B05', N'181-365 días', 181,  365,   273,  '6M_1Y', 5),
(6, 'B06', N'1-3 años',     366,  1095,  730,  'GE_1Y', 6),
(7, 'B07', N'3-5 años',     1096, 1825,  1460, 'GE_1Y', 7),
(8, 'B08', N'> 5 años',     1826, 99999, 2920, 'GE_1Y', 8);
GO

-- Perfiles de vencimiento conductual de carteras agregadas (participación por banda).
CREATE TABLE [dwh].[behavioral_ladder_profile] (
    [product_code]  VARCHAR(30)   NOT NULL,
    [bucket_code]   CHAR(3)       NOT NULL,
    [share]         DECIMAL(12,6) NOT NULL,
    CONSTRAINT [PK_behavioral_ladder_profile] PRIMARY KEY CLUSTERED ([product_code], [bucket_code]),
    CONSTRAINT [FK_ladder_bucket] FOREIGN KEY ([bucket_code]) REFERENCES [dwh].[dim_time_bucket] ([bucket_code]),
    CONSTRAINT [CK_ladder_share] CHECK ([share] BETWEEN 0 AND 1)
);
INSERT INTO [dwh].[behavioral_ladder_profile] ([product_code], [bucket_code], [share])
SELECT p.[product_code], b.[bucket_code], v.[share]
FROM (VALUES
    ('VISTA_PERS',       0.03, 0.05, 0.07, 0.08, 0.12, 0.25, 0.20, 0.20),
    ('DP_PERS',          0.10, 0.20, 0.30, 0.20, 0.15, 0.05, 0.00, 0.00),
    ('CTACTE_EMP',       0.05, 0.10, 0.10, 0.10, 0.15, 0.25, 0.15, 0.10),
    ('DP_INST',          0.20, 0.30, 0.25, 0.10, 0.10, 0.05, 0.00, 0.00),
    ('EFECTOS_COMERCIO', 0.15, 0.20, 0.35, 0.30, 0.00, 0.00, 0.00, 0.00),
    ('OBLIG_BANCOS_EXT', 0.15, 0.25, 0.30, 0.20, 0.10, 0.00, 0.00, 0.00),
    ('BONO_SENIOR',      0.005, 0.015, 0.03, 0.05, 0.10, 0.30, 0.30, 0.20),
    ('BONO_SUBORD',      0.00, 0.00, 0.00, 0.00, 0.00, 0.10, 0.30, 0.60),
    ('HIPOTECARIO',      0.004, 0.011, 0.025, 0.035, 0.07, 0.18, 0.17, 0.505),
    ('COM_CREDITO',      0.03, 0.06, 0.12, 0.13, 0.16, 0.25, 0.15, 0.10),
    ('CONSUMO',          0.015, 0.04, 0.08, 0.11, 0.17, 0.35, 0.20, 0.035),
    ('BONO_SOB',         0.02, 0.03, 0.05, 0.10, 0.15, 0.25, 0.20, 0.20),
    ('BONO_BCCH',        0.10, 0.25, 0.30, 0.25, 0.10, 0.00, 0.00, 0.00),
    ('BONO_BANC',        0.01, 0.02, 0.05, 0.07, 0.15, 0.35, 0.25, 0.10),
    ('BONO_CORP',        0.01, 0.02, 0.05, 0.07, 0.15, 0.35, 0.25, 0.10)
) p ([product_code], s1, s2, s3, s4, s5, s6, s7, s8)
CROSS APPLY (VALUES ('B01', p.s1), ('B02', p.s2), ('B03', p.s3), ('B04', p.s4),
                    ('B05', p.s5), ('B06', p.s6), ('B07', p.s7), ('B08', p.s8)) v ([bucket_code], [share])
JOIN [dwh].[dim_time_bucket] b ON b.[bucket_code] = v.[bucket_code];
GO

CREATE TABLE [dwh].[regulatory_parameter] (
    [param_code]   VARCHAR(50)    NOT NULL,
    [param_value]  DECIMAL(28,6)  NOT NULL,
    [description]  NVARCHAR(300)  NOT NULL,
    CONSTRAINT [PK_regulatory_parameter] PRIMARY KEY CLUSTERED ([param_code])
);
INSERT INTO [dwh].[regulatory_parameter] VALUES
('CAPITAL_BASICO_CLP', 470000000000, N'Capital básico (Tier 1) para límites de descalce C54 e IRRBB C56'),
('C54_LIMITE_DESCALCE_30D_X_CAPITAL', 1, N'Descalce acumulado a 30 días <= 1 x capital básico'),
('C54_LIMITE_DESCALCE_90D_X_CAPITAL', 2, N'Descalce acumulado a 90 días <= 2 x capital básico'),
('IRRBB_SHOCK_PARALLEL', 0.02, N'Shock paralelo estándar (BCBS 368; calibrar por moneda)'),
('IRRBB_SHOCK_SHORT', 0.03, N'Shock de corto plazo estándar'),
('IRRBB_SHOCK_LONG', 0.015, N'Shock de largo plazo estándar'),
('IRRBB_OUTLIER_EVE_PCT_TIER1', 0.15, N'Prueba de valor atípico: delta EVE / Tier 1 > 15%');
GO

CREATE TABLE [dwh].[yield_curve_point] (
    [currency_code]  CHAR(3)       NOT NULL,
    [bucket_code]    CHAR(3)       NOT NULL,
    [zero_rate]      DECIMAL(12,6) NOT NULL,
    CONSTRAINT [PK_yield_curve_point] PRIMARY KEY CLUSTERED ([currency_code], [bucket_code]),
    CONSTRAINT [FK_curve_bucket] FOREIGN KEY ([bucket_code]) REFERENCES [dwh].[dim_time_bucket] ([bucket_code])
);
INSERT INTO [dwh].[yield_curve_point] VALUES
('CLP','B01',0.0500),('CLP','B02',0.0505),('CLP','B03',0.0510),('CLP','B04',0.0515),
('CLP','B05',0.0525),('CLP','B06',0.0545),('CLP','B07',0.0560),('CLP','B08',0.0590);
GO

-- =============================================================================
-- 3. HECHO 1: SNAPSHOT PERIÓDICO DE SALDOS (con reexpresiones)
--    Grano: contrato x fecha x restatement_seq. Una reexpresión nunca sobrescribe:
--    agrega una versión con snapshot_asserted_at = instante del nuevo conocimiento.
-- =============================================================================
CREATE TABLE [dwh].[fact_account_balance_snapshot] (
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
    [regulatory_provision_clp]    DECIMAL(19,4)  NOT NULL,   -- C46
    [collateral_value_clp]        DECIMAL(19,4)  NOT NULL,
    [net_exposure_clp]            DECIMAL(19,4)  NOT NULL,
    [market_value_clp]            DECIMAL(19,4)  NOT NULL,   -- base HQLA
    [undrawn_commitment_clp]      DECIMAL(19,4)  NOT NULL,   -- base salidas LCR / RSF 5%
    [pending_debits_clp]          DECIMAL(19,4)  NOT NULL,   -- reservas dos fases (salida 100% LCR)
    [pending_credits_clp]         DECIMAL(19,4)  NOT NULL,
    [residual_maturity_days]      INT            NULL,       -- banda NSFR
    [row_checksum]                BINARY(32)     NOT NULL
)
ON [ps_monthly_date_key] ([snapshot_date_key]);
GO
CREATE CLUSTERED COLUMNSTORE INDEX [CCI_fact_account_balance_snapshot]
    ON [dwh].[fact_account_balance_snapshot] ON [ps_monthly_date_key] ([snapshot_date_key]);
GO
-- Índice rowstore alineado para unicidad del grano y búsqueda de la última versión.
CREATE UNIQUE NONCLUSTERED INDEX [UX_fact_snapshot_grain]
    ON [dwh].[fact_account_balance_snapshot] ([snapshot_date_key], [contract_key], [restatement_seq])
    INCLUDE ([snapshot_asserted_at], [row_checksum])
    ON [ps_monthly_date_key] ([snapshot_date_key]);
GO

-- =============================================================================
-- 4. HECHO 2: POSTINGS CONTABLES (grano transaccional, Kimball fig. 9.5)
-- =============================================================================
CREATE TABLE [dwh].[fact_gl_transaction_postings] (
    [posting_id]                   BIGINT         NOT NULL,   -- degenerada (linaje al ledger)
    [journal_entry_id]             BIGINT         NOT NULL,   -- degenerada (drill-down al asiento)
    [line_number]                  INT            NOT NULL,
    [posting_date_key]             INT            NOT NULL,
    [value_date_key]               INT            NOT NULL,
    [gl_account_key]               INT            NOT NULL,
    [contract_key]                 INT            NOT NULL,   -- 0 = sin contrato
    [partner_key]                  INT            NOT NULL,
    [transaction_code]             VARCHAR(40)    NOT NULL,
    [entry_kind]                   VARCHAR(16)    NOT NULL,
    [transaction_timestamp_utc]    DATETIME2(7)   NOT NULL,
    [transaction_timestamp_local]  DATETIME2(7)   NOT NULL,
    [local_cutoff_date]            DATE           NOT NULL,
    [debit_amount]                 DECIMAL(19,4)  NOT NULL,
    [credit_amount]                DECIMAL(19,4)  NOT NULL,
    [signed_amount]                DECIMAL(19,4)  NOT NULL,   -- con signo del saldo normal
    [balance_after]                DECIMAL(19,4)  NOT NULL,
    [account_sequence_number]      BIGINT         NOT NULL,   -- continuo por (cuenta, contrato)
    [is_final_daily_tx]            BIT            NOT NULL    -- última transacción del día contable
)
ON [ps_monthly_date_key] ([posting_date_key]);
GO
CREATE CLUSTERED COLUMNSTORE INDEX [CCI_fact_gl_transaction_postings]
    ON [dwh].[fact_gl_transaction_postings] ON [ps_monthly_date_key] ([posting_date_key]);
GO
CREATE NONCLUSTERED INDEX [IX_fact_gl_point_in_time]
    ON [dwh].[fact_gl_transaction_postings] ([gl_account_key], [contract_key], [is_final_daily_tx], [posting_date_key])
    INCLUDE ([balance_after], [account_sequence_number])
    ON [ps_monthly_date_key] ([posting_date_key]);
GO

-- =============================================================================
-- 5. HECHO 3: FLUJOS CONTRACTUALES PROYECTADOS
--    Grano: fecha de posición x contrato x fecha de flujo x tipo x reexpresión.
-- =============================================================================
CREATE TABLE [dwh].[fact_contractual_cashflows] (
    [position_date_key]        INT            NOT NULL,
    [contract_key]             INT            NOT NULL,
    [restatement_seq]          SMALLINT       NOT NULL,
    [cashflow_date_key]        INT            NOT NULL,
    [cashflow_type]            VARCHAR(20)    NOT NULL,   -- PRINCIPAL | INTEREST | REPRICING_NOTIONAL
    [partner_key]              INT            NOT NULL,
    [product_key]              INT            NOT NULL,
    [risk_band_key]            INT            NOT NULL,
    [liquidity_treatment_key]  INT            NOT NULL,
    [time_bucket_key]          INT            NOT NULL,
    [currency_code]            CHAR(3)        NOT NULL,
    [direction]                VARCHAR(8)     NOT NULL,   -- INFLOW | OUTFLOW
    [principal_amount]         DECIMAL(19,4)  NOT NULL,
    [interest_amount]          DECIMAL(19,4)  NOT NULL,
    [total_amount]             DECIMAL(19,4)  NOT NULL,
    [days_to_cashflow]         INT            NOT NULL,
    [discount_factor]          DECIMAL(18,8)  NOT NULL,
    [present_value_clp]        DECIMAL(19,4)  NOT NULL,
    [include_in_liquidity]     BIT            NOT NULL,   -- C54 / LCR
    [include_in_irrbb]         BIT            NOT NULL    -- C56 (repricing en tasa variable)
)
ON [ps_monthly_date_key] ([position_date_key]);
GO
CREATE CLUSTERED COLUMNSTORE INDEX [CCI_fact_contractual_cashflows]
    ON [dwh].[fact_contractual_cashflows] ON [ps_monthly_date_key] ([position_date_key]);
GO

GRANT SELECT ON SCHEMA::[dwh] TO [app_report_reader];
GO
