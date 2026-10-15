-- =============================================================================
-- 00_init_database.sql
-- =============================================================================
-- Base de datos CoreBanking (SQL Server 2017 Enterprise / Developer).
--
-- Decisiones de diseño:
--   * RCSI + SNAPSHOT: los lectores (reportería, ETL, dashboard) leen versiones de
--     fila desde tempdb y nunca toman locks compartidos sobre el ledger; los
--     escritores OLTP sólo compiten entre sí, y por muy poco tiempo, porque cada
--     contabilización es una transacción no interactiva dentro de un procedimiento.
--   * Cuatro esquemas = cuatro planos con permisos distintos:
--       oltp : data plane (ledger, saldos, idempotencia, outbox). Sólo EXECUTE.
--       stg  : staging, CDC, linaje y conciliación.
--       core : ODS bitemporal (Asserted Versioning + system-versioning nativo).
--       dwh  : presentación dimensional Kimball y vistas normativas CMF.
--   * Partición mensual por clave de fecha entera YYYYMMDD (RANGE RIGHT sobre el
--     primer día de cada mes): cada partición contiene exactamente un mes, lo que
--     habilita SWITCH, TRUNCATE ... WITH (PARTITIONS) y archivado por mes.
-- Script idempotente: puede re-ejecutarse sin error.
-- =============================================================================

USE [master];
GO

IF NOT EXISTS (SELECT 1 FROM sys.databases WHERE name = N'CoreBanking')
BEGIN
    CREATE DATABASE [CoreBanking] COLLATE Modern_Spanish_CI_AS;
END;
GO

ALTER DATABASE [CoreBanking] SET RECOVERY FULL;                 -- requerido por CDC y log shipping
ALTER DATABASE [CoreBanking] SET PAGE_VERIFY CHECKSUM;
ALTER DATABASE [CoreBanking] SET ALLOW_SNAPSHOT_ISOLATION ON;
GO
ALTER DATABASE [CoreBanking] SET READ_COMMITTED_SNAPSHOT ON WITH ROLLBACK AFTER 10 SECONDS;
GO
ALTER DATABASE [CoreBanking] SET AUTO_CREATE_STATISTICS ON (INCREMENTAL = ON);  -- estadísticas por partición
ALTER DATABASE [CoreBanking] SET AUTO_UPDATE_STATISTICS ON;
ALTER DATABASE [CoreBanking] SET AUTO_UPDATE_STATISTICS_ASYNC ON;
ALTER DATABASE [CoreBanking] SET DELAYED_DURABILITY = DISABLED;  -- nunca se acepta pérdida de commits contables
GO

USE [CoreBanking];
GO

-- -----------------------------------------------------------------------------
-- 1. Esquemas
-- -----------------------------------------------------------------------------
IF SCHEMA_ID(N'oltp') IS NULL EXEC (N'CREATE SCHEMA [oltp] AUTHORIZATION [dbo];');
IF SCHEMA_ID(N'stg')  IS NULL EXEC (N'CREATE SCHEMA [stg]  AUTHORIZATION [dbo];');
IF SCHEMA_ID(N'core') IS NULL EXEC (N'CREATE SCHEMA [core] AUTHORIZATION [dbo];');
IF SCHEMA_ID(N'dwh')  IS NULL EXEC (N'CREATE SCHEMA [dwh]  AUTHORIZATION [dbo];');
GO

-- -----------------------------------------------------------------------------
-- 2. Roles de aplicación (mínimo privilegio)
--    Todos los objetos pertenecen a dbo: los procedimientos acceden a las tablas
--    por encadenamiento de propiedad, por lo que los DENY sobre tablas no afectan
--    a los procedimientos pero sí a cualquier sentencia ad hoc.
-- -----------------------------------------------------------------------------
IF DATABASE_PRINCIPAL_ID(N'app_ledger_writer') IS NULL CREATE ROLE [app_ledger_writer] AUTHORIZATION [dbo];
IF DATABASE_PRINCIPAL_ID(N'app_etl_operator')  IS NULL CREATE ROLE [app_etl_operator]  AUTHORIZATION [dbo];
IF DATABASE_PRINCIPAL_ID(N'app_report_reader') IS NULL CREATE ROLE [app_report_reader] AUTHORIZATION [dbo];
GO

-- -----------------------------------------------------------------------------
-- 3. Función y esquema de partición mensual (enero 2024 a diciembre 2027).
--    Los límites se generan dinámicamente para evitar errores de tipeo; la
--    extensión posterior la realiza dwh.usp_extend_monthly_partitions (script 06).
-- -----------------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.partition_functions WHERE name = N'pf_monthly_date_key')
BEGIN
    DECLARE @boundaries NVARCHAR(MAX) = N'';
    DECLARE @month DATE = '2024-01-01';
    WHILE @month <= '2027-12-01'
    BEGIN
        SET @boundaries += CASE WHEN LEN(@boundaries) > 0 THEN N', ' ELSE N'' END
                         + CONVERT(NVARCHAR(8), @month, 112);
        SET @month = DATEADD(MONTH, 1, @month);
    END;

    DECLARE @sql NVARCHAR(MAX) =
        N'CREATE PARTITION FUNCTION [pf_monthly_date_key] (INT) AS RANGE RIGHT FOR VALUES (' + @boundaries + N');';
    EXEC sys.sp_executesql @sql;
END;
GO

IF NOT EXISTS (SELECT 1 FROM sys.partition_schemes WHERE name = N'ps_monthly_date_key')
BEGIN
    -- En producción cada año puede mapearse a su propio filegroup (archivado y
    -- restauración parcial); aquí todas las particiones viven en PRIMARY.
    CREATE PARTITION SCHEME [ps_monthly_date_key]
    AS PARTITION [pf_monthly_date_key] ALL TO ([PRIMARY]);
END;
GO

ALTER PARTITION SCHEME [ps_monthly_date_key] NEXT USED [PRIMARY];
GO

PRINT N'CoreBanking inicializada: RCSI activo, esquemas oltp/stg/core/dwh, roles y partición mensual pf_monthly_date_key.';
GO
