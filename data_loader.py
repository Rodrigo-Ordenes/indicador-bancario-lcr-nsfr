"""
data_loader.py
==============
Módulo encargado de la extracción, carga y preparación de datos para los
indicadores bancarios LCR (Liquidity Coverage Ratio) y NSFR (Net Stable Funding Ratio).

Estructura de 4 DataFrames principales:
1. df_lcr_historico: [fecha, lcr, alac, naf]
2. df_lcr_detalle:   [fecha, componente, categoria, subcategoria, monto_nominal, factor_ponderacion, monto_ponderado]
3. df_nsfr_historico:[fecha, nsfr, fed, fer]
4. df_nsfr_detalle:  [fecha, componente, categoria, subcategoria, monto_nominal, factor_ponderacion, monto_ponderado]

INSTRUCCIONES PARA EL USUARIO:
- Para conectar sus tablas de SQL, modifique los bloques marcados con:
  "====== CONEXIÓN SQL USUARIO ======"
- Actualmente el archivo provee datos bancarios simulados realistas y coherentes
  para que la aplicación funcione y se visualice de inmediato en localhost.
"""

import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# =============================================================================
# 1. CARGA DE LCR HISTÓRICO
# =============================================================================
@st.cache_data(ttl=600, show_spinner=False)
def load_lcr_historico() -> pd.DataFrame:
    """
    Carga el DataFrame de LCR Histórico.
    Columnas requeridas:
        - fecha: datetime64[ns]
        - lcr: float (Ratio LCR en porcentaje, ej: 114.5%)
        - alac: float (Activos Líquidos de Alta Calidad en MM$ / USD)
        - naf: float (Salidas Netas de Efectivo a 30 días en MM$ / USD)
    
    Fórmula regulatoria: LCR = (ALAC / NAF) * 100
    """
    # =========================================================================
    # ====== CONEXIÓN SQL SERVER (Descomentar y adaptar cuando conectes BD) ===
    # =========================================================================
    # from sqlalchemy import create_engine
    # # Opción A: Autenticación SQL Server (usuario y contraseña)
    # # conn_str = "mssql+pyodbc://usuario:password@servidor:1433/banco_db?driver=ODBC+Driver+17+for+SQL+Server"
    # # Opción B: Autenticación Windows integrada (Trusted Connection)
    # # conn_str = "mssql+pyodbc://servidor/banco_db?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes"
    # # engine = create_engine(conn_str)
    # # query = "SELECT CAST(fecha AS DATE) AS fecha, lcr, alac, naf FROM tabla_lcr_diario ORDER BY fecha ASC"
    # # df = pd.read_sql(query, engine)
    # # df['fecha'] = pd.to_datetime(df['fecha'])
    # # return df
    # =========================================================================

    # --- Generación de datos simulados realistas (365 días históricos) ---
    np.random.seed(42)
    end_date = datetime.today()
    start_date = end_date - timedelta(days=365)
    fechas = pd.date_range(start=start_date, end=end_date, freq='D')

    n = len(fechas)
    # Base ALAC ~ 1,250,000 MM$ con tendencia y ruido diario
    alac_base = 1250000 + np.cumsum(np.random.normal(120, 4500, n))
    # Base NAF ~ 1,100,000 MM$
    naf_base = 1100000 + np.cumsum(np.random.normal(90, 4000, n))

    # Asegurar valores mínimos lógicos
    alac = np.maximum(alac_base, 800000)
    naf = np.maximum(naf_base, 700000)

    # LCR = (ALAC / NAF) * 100
    lcr = (alac / naf) * 100

    df_lcr = pd.DataFrame({
        'fecha': fechas,
        'lcr': np.round(lcr, 2),
        'alac': np.round(alac, 2),
        'naf': np.round(naf, 2)
    })

    return df_lcr


# =============================================================================
# 2. CARGA DE LCR DETALLE (DESCOMPOSICIÓN ALAC Y NAF)
# =============================================================================
@st.cache_data(ttl=600, show_spinner=False)
def load_lcr_detalle() -> pd.DataFrame:
    """
    Carga el DataFrame de LCR Detalle (Descomposición de ALAC y NAF).
    Columnas requeridas:
        - fecha: datetime64[ns]
        - componente: str ('ALAC' o 'NAF')
        - categoria: str (Familia regulatoria / tramo)
        - subcategoria: str (Glosa o cuenta contable)
        - monto_nominal: float (Monto bruto en MM$)
        - factor_ponderacion: float (Ponderador Basilea/CMF: 1.0, 0.85, 0.5, etc.)
        - monto_ponderado: float (Monto neto computable para ALAC o NAF)
    """
    # =========================================================================
    # ====== CONEXIÓN SQL SERVER (Descomentar y adaptar cuando conectes BD) ===
    # =========================================================================
    # from sqlalchemy import create_engine
    # # engine = create_engine("mssql+pyodbc://usuario:password@servidor:1433/banco_db?driver=ODBC+Driver+17+for+SQL+Server")
    # # query = """
    # #     SELECT CAST(fecha AS DATE) AS fecha, componente, categoria, subcategoria,
    # #            monto_nominal, factor_ponderacion, monto_ponderado
    # #     FROM tabla_lcr_detalle
    # #     ORDER BY fecha ASC, componente, categoria
    # # """
    # # df = pd.read_sql(query, engine)
    # # df['fecha'] = pd.to_datetime(df['fecha'])
    # # return df
    # =========================================================================

    # Simulación de descomposición para los últimos 90 días diarios
    np.random.seed(101)
    end_date = datetime.today()
    fechas = pd.date_range(end=end_date, periods=90, freq='D')

    # Estructura regulatoria típica LCR (Basilea III / CMF)
    items_alac = [
        # (Categoria, Subcategoria, Base Nominal, Ponderador)
        ('Nivel 1: Efectivo y BCCh', 'Caja en Billetes y Monedas', 65000, 1.00),
        ('Nivel 1: Efectivo y BCCh', 'Depósito en Banco Central (BCCh)', 310000, 1.00),
        ('Nivel 1: Soberanos 0% Riego', 'Bonos de Tesorería BTP / BTU', 480000, 1.00),
        ('Nivel 1: Soberanos 0% Riego', 'Pagarés y Bonos Banco Central (PDBC)', 220000, 1.00),
        ('Nivel 2A: Activos Alta Calidad', 'Bonos Bancarios Locales Calificación AA+', 95000, 0.85),
        ('Nivel 2A: Activos Alta Calidad', 'Bonos Corporativos Grado de Inversión AAA', 75000, 0.85),
        ('Nivel 2B: Activos Menor Liquidez', 'Acciones Índices Principales (IPSA)', 25000, 0.50),
    ]

    items_naf = [
        ('Depósitos Minoristas Estables', 'Depósitos a la Vista Personas Naturales', 450000, 0.05),
        ('Depósitos Minoristas Menos Estables', 'Depósitos a Plazo Personas Naturales', 320000, 0.10),
        ('Depósitos Mayoristas Operacionales', 'Cuentas Corrientes Empresas (Operacional)', 380000, 0.25),
        ('Depósitos Mayoristas No Operacionales', 'Depósitos Institucionales (AFPs/Fondos/Bancos)', 420000, 1.00),
        ('Fondeo Mayorista No Garantizado', 'Efectos de Comercio y Papeles Comerciales', 150000, 1.00),
        ('Compromisos de Liquidez y Crédito', 'Líneas de Crédito Corporativas No Utilizadas', 210000, 0.30),
        ('Entradas de Efectivo (Inflows con Cap 75%)', 'Cobros de Cartera de Créditos a 30 Días', -280000, 1.00),
        ('Entradas de Efectivo (Inflows con Cap 75%)', 'Inversiones Financieras que Vencen a 30 Días', -140000, 1.00),
    ]

    records = []
    for f in fechas:
        # Variación aleatoria suave por día
        factor_dia = 1.0 + np.sin(f.dayofyear / 15.0) * 0.03 + np.random.normal(0, 0.015)
        
        # ALAC
        for cat, subcat, base_nom, pond in items_alac:
            nom = base_nom * factor_dia * (1.0 + np.random.normal(0, 0.02))
            pond_val = nom * pond
            records.append({
                'fecha': f,
                'componente': 'ALAC',
                'categoria': cat,
                'subcategoria': subcat,
                'monto_nominal': round(nom, 2),
                'factor_ponderacion': pond,
                'monto_ponderado': round(pond_val, 2)
            })
            
        # NAF
        for cat, subcat, base_nom, pond in items_naf:
            nom = base_nom * factor_dia * (1.0 + np.random.normal(0, 0.02))
            pond_val = nom * pond
            records.append({
                'fecha': f,
                'componente': 'NAF',
                'categoria': cat,
                'subcategoria': subcat,
                'monto_nominal': round(nom, 2),
                'factor_ponderacion': pond,
                'monto_ponderado': round(pond_val, 2)
            })

    return pd.DataFrame(records)


# =============================================================================
# 3. CARGA DE NSFR HISTÓRICO
# =============================================================================
@st.cache_data(ttl=600, show_spinner=False)
def load_nsfr_historico() -> pd.DataFrame:
    """
    Carga el DataFrame de NSFR Histórico.
    Columnas requeridas:
        - fecha: datetime64[ns]
        - nsfr: float (Ratio NSFR en porcentaje, ej: 109.8%)
        - fed: float (Fondos Estables Disponibles / ASF en MM$ / USD)
        - fer: float (Fondos Estables Requeridos / RSF en MM$ / USD)
    
    Fórmula regulatoria: NSFR = (FED / FER) * 100
    """
    # =========================================================================
    # ====== CONEXIÓN SQL SERVER (Descomentar y adaptar cuando conectes BD) ===
    # =========================================================================
    # from sqlalchemy import create_engine
    # # engine = create_engine("mssql+pyodbc://usuario:password@servidor:1433/banco_db?driver=ODBC+Driver+17+for+SQL+Server")
    # # query = "SELECT CAST(fecha AS DATE) AS fecha, nsfr, fed, fer FROM tabla_nsfr_diario ORDER BY fecha ASC"
    # # df = pd.read_sql(query, engine)
    # # df['fecha'] = pd.to_datetime(df['fecha'])
    # # return df
    # =========================================================================

    np.random.seed(88)
    end_date = datetime.today()
    start_date = end_date - timedelta(days=365)
    fechas = pd.date_range(start=start_date, end=end_date, freq='D')

    n = len(fechas)
    # FED (Available Stable Funding) ~ 1,800,000 MM$
    fed_base = 1800000 + np.cumsum(np.random.normal(150, 3200, n))
    # FER (Required Stable Funding) ~ 1,650,000 MM$
    fer_base = 1650000 + np.cumsum(np.random.normal(110, 3000, n))

    fed = np.maximum(fed_base, 1200000)
    fer = np.maximum(fer_base, 1100000)

    # NSFR = (FED / FER) * 100
    nsfr = (fed / fer) * 100

    df_nsfr = pd.DataFrame({
        'fecha': fechas,
        'nsfr': np.round(nsfr, 2),
        'fed': np.round(fed, 2),
        'fer': np.round(fer, 2)
    })

    return df_nsfr


# =============================================================================
# 4. CARGA DE NSFR DETALLE (DESCOMPOSICIÓN FED Y FER)
# =============================================================================
@st.cache_data(ttl=600, show_spinner=False)
def load_nsfr_detalle() -> pd.DataFrame:
    """
    Carga el DataFrame de NSFR Detalle (Descomposición de FED y FER).
    Columnas requeridas:
        - fecha: datetime64[ns]
        - componente: str ('FED' o 'FER')
        - categoria: str (Categoría ASF o RSF)
        - subcategoria: str (Glosa contable o tipología)
        - monto_nominal: float (Monto nominal bruto en MM$)
        - factor_ponderacion: float (Factor ASF o RSF: 1.0, 0.85, 0.65, 0.50, etc.)
        - monto_ponderado: float (Monto ponderado computable para FED o FER)
    """
    # =========================================================================
    # ====== CONEXIÓN SQL SERVER (Descomentar y adaptar cuando conectes BD) ===
    # =========================================================================
    # from sqlalchemy import create_engine
    # # engine = create_engine("mssql+pyodbc://usuario:password@servidor:1433/banco_db?driver=ODBC+Driver+17+for+SQL+Server")
    # # query = """
    # #     SELECT CAST(fecha AS DATE) AS fecha, componente, categoria, subcategoria,
    # #            monto_nominal, factor_ponderacion, monto_ponderado
    # #     FROM tabla_nsfr_detalle
    # #     ORDER BY fecha ASC, componente, categoria
    # # """
    # # df = pd.read_sql(query, engine)
    # # df['fecha'] = pd.to_datetime(df['fecha'])
    # # return df
    # =========================================================================

    np.random.seed(99)
    end_date = datetime.today()
    fechas = pd.date_range(end=end_date, periods=90, freq='D')

    # Componentes regulatorios NSFR (Basilea III / CMF)
    items_fed = [
        # (Categoria, Subcategoria, Base Nominal, Ponderador ASF)
        ('Capital Regulatorio e Instrumentos > 1 Año', 'Patrimonio Efectivo y Utilidades Acumuladas', 420000, 1.00),
        ('Capital Regulatorio e Instrumentos > 1 Año', 'Bonos Subordinados y Deuda a Largo Plazo (>1a)', 310000, 1.00),
        ('Depósitos Minoristas Estables', 'Depósitos a la Vista y Plazo Personas (Estables)', 620000, 0.95),
        ('Depósitos Minoristas Menos Estables', 'Depósitos de Personas sin Seguro Garantía', 240000, 0.90),
        ('Fondeo Mayorista Operacional', 'Depósitos Operacionales Institucionales', 380000, 0.50),
        ('Fondeo Mayorista No Operacional', 'Depósitos Corporativos < 6 Meses', 290000, 0.50),
    ]

    items_fer = [
        # (Categoria, Subcategoria, Base Nominal, Ponderador RSF)
        ('Efectivo y Reservas Centrales (0% RSF)', 'Caja y Cuentas de Reserva en BCCh', 360000, 0.00),
        ('Activos Líquidos Nivel 1 (5% RSF)', 'Bonos Soberanos de Tesorería BTP/BTU', 450000, 0.05),
        ('Créditos con Garantía Residencial (65% RSF)', 'Mutuos Hipotecarios de Vivienda (<80% LTV)', 920000, 0.65),
        ('Créditos Comerciales y Corporativos (85% RSF)', 'Préstamos a Empresas con Vencimiento > 1 Año', 810000, 0.85),
        ('Créditos de Consumo y Tarjetas (85%-100% RSF)', 'Créditos de Consumo y Préstamos No Garantizados', 340000, 1.00),
        ('Otros Activos y Derivados (100% RSF)', 'Activos Fijos, Intangibles y Margen de Derivados', 160000, 1.00),
    ]

    records = []
    for f in fechas:
        factor_dia = 1.0 + np.cos(f.dayofyear / 20.0) * 0.025 + np.random.normal(0, 0.01)
        
        # FED
        for cat, subcat, base_nom, pond in items_fed:
            nom = base_nom * factor_dia * (1.0 + np.random.normal(0, 0.015))
            pond_val = nom * pond
            records.append({
                'fecha': f,
                'componente': 'FED',
                'categoria': cat,
                'subcategoria': subcat,
                'monto_nominal': round(nom, 2),
                'factor_ponderacion': pond,
                'monto_ponderado': round(pond_val, 2)
            })

        # FER
        for cat, subcat, base_nom, pond in items_fer:
            nom = base_nom * factor_dia * (1.0 + np.random.normal(0, 0.015))
            pond_val = nom * pond
            records.append({
                'fecha': f,
                'componente': 'FER',
                'categoria': cat,
                'subcategoria': subcat,
                'monto_nominal': round(nom, 2),
                'factor_ponderacion': pond,
                'monto_ponderado': round(pond_val, 2)
            })

    return pd.DataFrame(records)
