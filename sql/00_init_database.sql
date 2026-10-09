-- =============================================================================
-- 00_init_database.sql
-- =============================================================================
-- Propósito: Inicialización de la base de datos CoreBanking, configuración de
-- concurrencia sin bloqueos (RCSI), aislamiento y esquemas de cuatro capas:
--   1. oltp: Data Plane / Ledger Inmutable
--   2. stg:  Staging & Control (Kimball Kitchen & Auditoría ETL)
--   3. core: Core Bitemporal / ODS (System-Versioned Temporal Tables)
--   4. dwh:  Data Warehouse & Data Marts Dimensionales (Kimball Presentation)
-- Motor: Microsoft SQL Server 2017 Enterprise Edition
-- =============================================================================

USE [master];
GO

IF NOT EXISTS (SELECT 1 FROM sys.databases WHERE name = N'CoreBanking')
BEGIN
    CREATE DATABASE [CoreBanking]
    COLLATE Modern_Spanish_CI_AS;
END;
GO

-- 1. Configuración de Recuperación y Concurrencia sin Bloqueos
ALTER DATABASE [CoreBanking] SET RECOVERY FULL;
GO

-- Habilitar Read Committed Snapshot Isolation (RCSI)
-- Elimina bloqueos de lectura compartidos (S) frente a escritores OLTP
ALTER DATABASE [CoreBanking] SET ALLOW_SNAPSHOT_ISOLATION ON;
GO
ALTER DATABASE [CoreBanking] SET READ_COMMITTED_SNAPSHOT ON WITH ROLLBACK AFTER 10 SECONDS;
GO

-- Ajustes de rendimiento de motor
ALTER DATABASE [CoreBanking] SET AUTO_CREATE_STATISTICS ON(INCREMENTAL = ON);
ALTER DATABASE [CoreBanking] SET AUTO_UPDATE_STATISTICS ON;
ALTER DATABASE [CoreBanking] SET AUTO_UPDATE_STATISTICS_ASYNC ON;
GO

USE [CoreBanking];
GO

-- 2. Creación de los 4 Esquemas Desacoplados
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'oltp') 
    EXEC('CREATE SCHEMA [oltp];');
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'stg')  
    EXEC('CREATE SCHEMA [stg];');
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'core') 
    EXEC('CREATE SCHEMA [core];');
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'dwh')  
    EXEC('CREATE SCHEMA [dwh];');
GO

PRINT 'Inicialización completada: Base de datos CoreBanking y esquemas [oltp], [stg], [core], [dwh] creados con RCSI activo.';
GO
