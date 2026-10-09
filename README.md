# 🏦 Dashboard Ejecutivo de Indicadores Bancarios: LCR & NSFR

Plataforma ejecutiva e interactiva de analítica bancaria y supervisión regulatoria (Basilea III / CMF), desarrollada en Python y Streamlit para la Gerencia de Finanzas, Riesgo Financiero y el Comité ALCO (Asset & Liability Management).

---

## 🚀 Inicio Rápido (Localhost)

Para iniciar el sitio web en tu navegador:

```bash
# Opción 1: Con el script directo
./run.sh

# Opción 2: Con el entorno virtual
source .venv/bin/activate
streamlit run app.py
```

Luego abre en tu navegador: **`http://localhost:8501`**

---

## 🎨 Tema y Paleta Oficial Bloomberg Terminal

La interfaz está inspirada en los monitores profesionales **Bloomberg Terminal**, construida con una base negra profunda y colores de alto contraste:

| Color | Código Hex | Rol en el Dashboard |
|---|---|---|
| **Base Negra** | `#000000` | Fondo general, contenedores y canvas de gráficos |
| **Rojo Bloomberg** | `#fe443d` | Salidas Netas (NAF), Fondeo Requerido (FER), deltas negativas ($\Delta < 0$) y alertas críticas |
| **Azul Bloomberg** | `#0168ff` | Indicadores principales (LCR / NSFR), botones de acción y títulos |
| **Menta / Turquesa** | `#4af6c4` | Activos Líquidos (ALAC), Fondeo Disponible (FED), deltas positivas ($\Delta > 0$) y estado óptimo |
| **Naranja Bloomberg** | `#fb8b1f` | Umbral de apetito de riesgo (105%), alertas preventivas y acentos secundarios |

---

## 📊 Características y Capacidades Principales

### 1. Indicadores Regulatorios
- **LCR (Liquidity Coverage Ratio / Ratio de Cobertura de Liquidez)**:
  - $\text{LCR} = \frac{\text{ALAC}}{\text{NAF}} \times 100\%$
  - **ALAC**: Activos Líquidos de Alta Calidad (HQLA Nivel 1, 2A, 2B).
  - **NAF**: Salidas Netas de Efectivo a 30 días (Depósitos minoristas, mayoristas, líneas comprometidas menos entradas).
- **NSFR (Net Stable Funding Ratio / Ratio de Fondeo Estable Neto)**:
  - $\text{NSFR} = \frac{\text{FED}}{\text{FER}} \times 100\%$
  - **FED**: Fondos Estables Disponibles (ASF: Capital, depósitos estables, fondeo a largo plazo).
  - **FER**: Fondos Estables Requeridos (RSF: Hipotecarios, créditos corporativos, inversiones).

### 2. Vistas Temporales (Granularidad)
- **📅 Diaria**: Variación día a día ($\Delta$ pp y $\Delta$ monto).
- **📆 Semanal**: Cierre semanal al viernes, midiendo la dinámica semana contra semana.
- **🗓️ Mensual**: Cierre de fin de mes (para comités ALCO y reportes a directorio).
- **📈 Anual**: Tendencia interanual y evolución estructural del balance.

### 3. Vistas Analíticas
- **📊 Vista Ejecutiva**:
  - 4 Tarjetas KPI con deltas y semáforo normativo en tiempo real.
  - **Gráfico de Cascada (Waterfall / Bridge)**: Descompone matemáticamente cuánto del cambio del ratio fue explicado por el numerador ($\Delta$ ALAC / $\Delta$ FED) y cuánto por el denominador ($\Delta$ NAF / $\Delta$ FER).
  - **Gráfico de Tendencia con Bandas**: Muestra la evolución frente al mínimo normativo CMF (100%) y el apetito de riesgo interno (ej. 105%).
  - **Gráfico de Composición (Doble Eje)**: Montos de Activos vs Pasivos en MM$ en el eje izquierdo, y Ratio en % en el eje derecho.
  - **Gráfico de Variaciones**: Barras periódicas de $\Delta$ ratio y $\Delta$ componentes.
- **🔍 Vista Detallada Descompuesta**:
  - Filtro por fecha de corte.
  - Descomposición jerárquica por categorías regulatorias y cuentas individuales.
  - Visualización gráfica de participación (%) y montos ponderados.
  - Tabla interactiva con ordenamiento, búsqueda y botón de **Descarga en CSV**.
- **🧪 Simulador de Estrés ALM (Propuesta de Alto Valor)**:
  - Pruebas de estrés interactivas (What-If): Aplica shocks porcentuales a activos y pasivos para verificar si el banco resiste escenarios de corridas de depósitos o caídas de mercado sin quebrar los límites regulatorios.

### 4. Análisis Ejecutivo con Inteligencia Artificial (Copilot)
- **Motor Cuantitativo Automático (Offline)**: Genera instantáneamente el diagnóstico de cumplimiento, holgura, descomposición matemática de drivers y recomendaciones para la Mesa de Dinero sin necesidad de API keys.
- **Conector a Copilot / OpenAI / Azure**: Con un solo clic (`✨ Generar Memo con Copilot`), la IA redacta un memorándum ejecutivo formal para la Alta Dirección.

### 5. Arquitectura Empresarial de 4 Capas (SQL Server 2017 & Kimball)
- **🔐 Capa 1: OLTP / Ledger Inmutable**: Contabilidad bancaria multi-leg con invariante $\sum \text{Debit} - \sum \text{Credit} = 0$, `DENY UPDATE/DELETE`, triggers defensivos `INSTEAD OF` y encadenamiento hash SHA-256 (`HASHBYTES`).
- **⏳ Capa 2: Core Bitemporal / ODS**: Asserted Versioning framework combinando **System Transaction Time** (`SysStartTime`, `SysEndTime` nativos) con **Business Effective Time** (`ValidFrom`, `ValidTo`) para resolver auditorías CMF (C46 Provisiones / C55 Liquidez).
- **📊 Capa 3: Data Warehouse Kimball**: Minidimensiones de riesgo (`dim_risk_band`), dimensión conformada (`dim_partner`), `dim_date` con role-playing views y tablas de hechos con **Clustered Columnstore Indexes (CCI)** particionadas mensualmente.
- **🔄 Capa 4: Staging & Linaje ETL**: Registro de lotes (`stg.load_execution_log`), reconciliación nocturna Legacy vs Ledger y conmutación masiva zero-lock vía **Partition Switching**.
- **📜 Scripts T-SQL de Producción (`sql/`)**:
  - `00_init_database.sql`: Creación de base de datos con RCSI y 4 esquemas.
  - `01_oltp_immutable_ledger.sql`: DDL de cabeceras/postings, triggers y procedimientos seguros.
  - `02_stg_audit_lineage.sql`: Bitácora de ejecución, linaje y función de checksum SHA-256.
  - `03_core_bitemporal_ods.sql`: Tablas temporal bitemporales con versionado nativo.
  - `04_dwh_kimball_presentation.sql`: Esquema Kimball, minidimensiones, fechas y hechos con CCI.
  - `05_regulatory_time_travel_audit.sql`: Consultas de auditoría retrospectiva CMF.
  - `06_migration_and_partition_switching.sql`: Activación CDC, backfilling, partition switching y reconciliación.

---

## 🗄️ Cómo Conectar tus Tablas de SQL (Arquitectura de 4 DataFrames)

El sistema está desacoplado limpiamente en **`data_loader.py`**, donde se nutren los 4 DataFrames requeridos:

| DataFrame | Función en `data_loader.py` | Columnas Esperadas | Descripción |
|---|---|---|---|
| `df_lcr_historico` | `load_lcr_historico()` | `fecha`, `lcr`, `alac`, `naf` | Serie histórica diaria del ratio LCR y totales |
| `df_lcr_detalle` | `load_lcr_detalle()` | `fecha`, `componente`, `categoria`, `subcategoria`, `monto_nominal`, `factor_ponderacion`, `monto_ponderado` | Cuentas descompuestas de ALAC y NAF |
| `df_nsfr_historico` | `load_nsfr_historico()` | `fecha`, `nsfr`, `fed`, `fer` | Serie histórica diaria del ratio NSFR y totales |
| `df_nsfr_detalle` | `load_nsfr_detalle()` | `fecha`, `componente`, `categoria`, `subcategoria`, `monto_nominal`, `factor_ponderacion`, `monto_ponderado` | Cuentas descompuestas de FED y FER |

### Ejemplo de Conexión en `data_loader.py`:

```python
import pandas as pd
from sqlalchemy import create_engine

# 1. Configurar tu motor de base de datos
engine = create_engine("postgresql://usuario:password@servidor:5432/banco_riesgo")
# o para SQL Server: create_engine("mssql+pyodbc://usuario:password@servidor/db?driver=ODBC+Driver+17+for+SQL+Server")

@st.cache_data(ttl=600)
def load_lcr_historico() -> pd.DataFrame:
    query = \"\"\"
        SELECT 
            CAST(fecha AS DATE) AS fecha,
            lcr,
            alac,
            naf
        FROM tabla_lcr_diario
        ORDER BY fecha ASC
    \"\"\"
    df = pd.read_sql(query, engine)
    df['fecha'] = pd.to_datetime(df['fecha'])
    return df
```

---

## 📁 Estructura del Proyecto

```
Indicador bancario/
├── app.py                      # Interfaz principal Streamlit y orquestación
├── data_loader.py              # Extracción y carga de los 4 DataFrames (con soporte SQL)
├── analytics.py                # Remuestreo temporal (diario/semanal/mensual/anual), deltas y fórmulas
├── ai_analyst.py               # Motor de IA: Análisis heurístico financiero y conector Copilot/OpenAI
├── styles.py                   # CSS ejecutivo con estilo institucional bancario
├── components/
│   ├── ui_cards.py             # Tarjetas ejecutivas KPI y semáforos de cumplimiento
│   ├── charts.py               # Gráficos Plotly: Tendencia, doble eje, variaciones, cascada y categorías
│   └── tables.py               # Tablas estilizadas de descomposición y exportación CSV
├── requirements.txt            # Librerías de Python
├── run.sh                      # Script ejecutable de inicio
└── README.md                   # Documentación técnica y funcional
```
