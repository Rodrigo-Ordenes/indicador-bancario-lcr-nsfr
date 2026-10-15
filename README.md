# 🏦 Terminal ALM: Panel Interactivo de Liquidez & Balance (LCR & NSFR)
### Reportería Ejecutiva para la Alta Administración y Comité ALCO (Basilea III / CMF Chile)

Plataforma ejecutiva e interactiva de analítica bancaria y supervisión financiera, desarrollada para la **Gerencia de Riesgo Financiero, Tesorería, Finanzas y el Comité ALCO**. Diseñada bajo la **paleta oficial Bloomberg Terminal Dark** (`#000000`, `#0168ff`, `#fe443d`, `#4af6c4`, `#fb8b1f`, `#ffd15c`), combina rigor matemático estricto según **Basilea III (BCBS 238 LCR / BCBS 295 NSFR)** con una experiencia intuitiva de alta dirección y capacidades de **exportación de informes PDF ejecutivos**.

---

## 🎨 Paleta Oficial Bloomberg Terminal Dark

La plataforma adopta el estándar de monitores profesionales **Bloomberg Terminal**, maximizando el contraste y la claridad de lectura para ejecutivos:

| Elemento | Código Hex | Rol en el Dashboard |
| :--- | :--- | :--- |
| **Fondo Base** | `#000000` | Fondo negro terminal puro de alta concentración y descanso visual. |
| **Superficies** | `#0D0D0D` / `#141414` | Tarjetas KPI, contenedores analíticos y paneles de control. |
| **Azul Bloomberg** | `#0168ff` | Indicadores principales (LCR / NSFR), acentos activos y botones primarios. |
| **Rojo Bloomberg** | `#fe443d` | Salidas Netas (NAF), Fondeo Requerido (FER), límite mínimo normativo (100%) y alertas críticas. |
| **Menta Bloomberg** | `#4af6c4` | Activos Líquidos (ALAC), Fondeo Disponible (FED), estado óptimo y variaciones positivas. |
| **Naranja Bloomberg** | `#fb8b1f` | Apetito por riesgo (150% LCR / 110% NSFR) y alertas intermedias. |
| **Amarillo Bloomberg** | `#ffd15c` | Nivel de alerta interna (120% LCR / 105% NSFR) y notas de advertencia. |

---

## 📋 Módulos y Requerimientos Funcionales Implementados

### 1. Panel LCR (Ratio de Cobertura de Liquidez - 30 Días)
- **1.1. Indicadores Principales**:
  - Muestra LCR actual, ALAC (Activos Líquidos de Alta Calidad) y NAF (Salidas Netas de Efectivo).
  - Variaciones período a período en puntos porcentuales ($\Delta$ pp) y montos monetarios ($\Delta$ MM$).
  - Alertas visuales automáticas según los umbrales normativos:
    - **100%**: Límite mínimo de referencia CMF (Rojo `#fe443d`).
    - **120%**: Nivel de alerta interna ALCO (Amarillo `#ffd15c`).
    - **150%**: Apetito por riesgo institucional (Naranja `#fb8b1f`).
- **1.2. Evolución y Comparación**:
  - Gráfico temporal de LCR, ALAC y NAF con líneas de alertas, límites y apetitos.
  - Filtros de granularidad temporal: Diaria, Semanal (viernes), Mensual (fin de mes) y Anual.
  - Módulo de comparación directa entre dos períodos seleccionados (Fecha A vs Fecha B) con variaciones absolutas y porcentuales.
- **1.3. Descomposición de Componentes**:
  - Desglose jerárquico por categorías y partidas contables de ALAC y NAF.
  - Montos actuales, montos del período anterior y variaciones absolutas/porcentuales.
  - Identificación de los principales factores que explican los cambios (Top Drivers).
  - Exportación de la descomposición en planillas CSV.
- **1.4. Pruebas de Tensión (Stress Testing)**:
  - Sliders interactivos para aumentar o disminuir montos de colaterales y pasivos.
  - Recálculo dinámico con el motor regulatorio respetando los topes de Basilea III (Cap 40% Nivel 2, Cap 15% Nivel 2B y Cap 75% Inflows).
  - Escenarios predefinidos de un clic: **Base (0%)**, **Adverso (-5% ALAC, +10% NAF)** y **Severo (-15% ALAC, +25% NAF)**.
  - Botón **Restablecer montos originales**.
- **1.5. Proyecciones de Liquidez**:
  - Proyección del LCR a 30, 60, 90 y 180 días con cono de incertidumbre al 95%.
  - Detalle de supuestos, método de estimación y resultados proyectados frente a los límites.

---

### 2. Panel NSFR (Ratio de Fondeo Estable Neto - Estructural > 1 Año)
- **2.1. Indicadores Principales**:
  - Muestra NSFR actual, FED (Fondeo Estable Disponible) y FER (Fondeo Estable Requerido).
  - Variaciones respecto de períodos anteriores.
  - Alertas visuales según los umbrales definidos:
    - **100%**: Límite mínimo de referencia CMF.
    - **105%**: Nivel de alerta interna ALCO.
    - **110%**: Apetito por riesgo institucional.
- **2.2. Evolución y Comparación**:
  - Gráfico temporal de NSFR, FED y FER con límites y apetitos.
  - Filtros diaria, semanal, mensual y anual.
  - Comparador entre períodos seleccionados con deltas absolutos y porcentuales.
- **2.3. Descomposición de Componentes**:
  - Tabla detallada de partidas de FED (capital, depósitos) y FER (hipotecarios, créditos corporativos, inversiones).
  - Variación absoluta y porcentual por componente e identificación de top drivers.
  - Descarga en CSV.
- **2.4. Pruebas de Tensión**:
  - Modificación interactiva de componentes de FED y FER.
  - Recálculo automático del ratio y evaluación de impacto en la holgura estructural.
  - Escenarios Base, Adverso y Severo con botón de restablecimiento.
- **2.5. Proyecciones**:
  - Proyección del NSFR con montos históricos, bandas de confianza y supuestos cuantitativos.

---

### 3. Análisis Transversal de Liquidez
- **3.1. Puente de Variaciones (Waterfall Bridge)**:
  - Explica la variación del ratio entre cualquier par de fechas seleccionadas ($t_0 \to t_1$).
  - Descompone matemáticamente el cambio en: **Efecto Activo / Numerador**, **Efecto Pasivo / Denominador** e **Interacción / Ajuste de topes**.
- **3.2. Holgura Respecto de los Límites**:
  - Matriz de distancias a 150%, 120% y 100% (LCR) y 105%, 100% (NSFR) en puntos porcentuales (pp) y holgura monetaria (MM$ CLP).
  - Capacidad máxima de absorción de salidas o fondeo requerido antes de quebrar cada umbral.
  - Estadísticas históricas: días consecutivos bajo alerta y frecuencia histórica de incumplimiento.
- **3.3. Precisión de las Proyecciones (Backtesting)**:
  - Comparación objetiva de valores proyectados vs observados en ventanas de test.
  - Métricas de error: **MAE (pp)**, **MAPE (%)** y **RMSE (pp)**.
  - Comparación de modelos: Alisamiento Exponencial Doble (Holt), Regresión Lineal de Tendencia y Media Móvil Ponderada.
- **3.4. Concentración y Vulnerabilidad**:
  - Índice de Concentración **Herfindahl-Hirschman (HHI)** por componente con diagnóstico regulatorio.
  - Curva de Pareto y participación acumulada Top 5 / 80%.
  - Volatilidad histórica por partida (Media, Desviación Estándar y Coeficiente de Variación %).
- **3.5. Resumen Ejecutivo y Alertas Accionables**:
  - Síntesis de movimientos del período y diagnóstico de vulnerabilidad.
  - Cuadro de observaciones gerenciales y plan de acción de mitigación.
  - Generación de Memorándum ALCO asistido por Inteligencia Artificial (Copilot / OpenAI).

---

### 4. Exportación de Informes en PDF
- **Generación directa de informe PDF** consolidado con formato ejecutivo formal.
- Incluye indicadores actuales, semáforos, tablas de holguras, pruebas de estrés y proyecciones.
- Registra fecha de corte, fecha de generación, supuestos relevantes y acciones de mitigación.
- Descarga inmediata en el navegador mediante botón de descarga.

---

## 🚀 Inicio Rápido en Localhost

```bash
# 1. Iniciar con el script automatizado
./run.sh

# O manualmente con el entorno virtual:
source .venv/bin/activate
streamlit run app.py
```
Abre en tu navegador: **`http://localhost:8501`**

---

## 🧪 Pruebas Unitarias

```bash
.venv/bin/python -m unittest discover -s tests -v
```
> **15/15 Pruebas Unitarias Aprobadas (Ran in 0.16s - OK)**:
> Cobertura sobre topes de Basilea III (40%, 15%, 75%), inmutabilidad contable, transacciones en dos fases, idempotencia y detección criptográfica de manipulaciones.
