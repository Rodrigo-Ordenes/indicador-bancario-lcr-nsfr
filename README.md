# Panel interactivo de liquidez: LCR y NSFR

Herramienta de reportería de Riesgo Financiero para Finanzas, Tesorería y la alta administración:
monitorea la posición de liquidez, explica las variaciones de los indicadores, evalúa escenarios
de tensión, anticipa deterioros y genera informes PDF consolidados.

```bash
./run.sh
```

o bien `.venv/bin/streamlit run app.py` y abrir `http://localhost:8501`.

---

## Navegación

La barra lateral tiene cinco módulos, una **vista temporal** común (diaria, semanal, mensual, anual)
y los **umbrales**. Una franja superior muestra siempre la situación de LCR y NSFR al último cierre.

| Módulo | Contenido |
|---|---|
| **1 · Panel LCR** | 1.1–1.2 Indicadores y evolución · 1.3 Descomposición · 1.4 Pruebas de tensión · 1.5 Proyecciones |
| **2 · Panel NSFR** | La misma estructura y navegación que el LCR |
| **3 · Análisis transversal** | 3.1 Puente de variaciones · 3.2 Holgura frente a límites · 3.3 Precisión de proyecciones · 3.4 Concentración y vulnerabilidad · 3.5 Resumen ejecutivo y alertas |
| **4 · Informe PDF** | Informe consolidado con fecha de corte, período, escenarios y observaciones seleccionables |
| **5 · Calidad y trazabilidad** | Controles de calidad de datos y arquitectura de la base regulatoria |

### Vista temporal

Los datos son **cierres diarios**: no existe movimiento intradía.

- **Diaria:** cierre de hoy y variación respecto del cierre de ayer.
- **Semanal, mensual y anual:** último cierre diario de cada período comparado con el cierre del
  período anterior. El período en curso se marca "(en curso)".

### Umbrales

| Indicador | Mínimo regulatorio | Alerta interna | Apetito por riesgo |
|---|---|---|---|
| LCR | 100% (fijo) | 120% | 150% |
| NSFR | 100% (fijo) | 105% | — |

Los niveles internos se ajustan en la barra lateral. Situaciones: **INCUMPLIMIENTO** (< mínimo),
**ALERTA** (< alerta), **BAJO APETITO** (< apetito), **DENTRO DE APETITO** / **CUMPLE**.

### Naturaleza de cada dato

Cada sección indica si muestra un **DATO REAL** (observado), una **PROYECCIÓN · ESTIMACIÓN**
(con banda de incertidumbre, nunca presentada como resultado cierto) o una **SIMULACIÓN** (escenario
de tensión).

---

## Paleta Bloomberg

| Color | Uso |
|---|---|
| `#000000` | Fondo |
| `#0168ff` azul | LCR / NSFR, proyecciones, situación "bajo apetito" |
| `#4af6c4` menta | ALAC / FED, apetito por riesgo, variaciones favorables, cumplimiento |
| `#fe443d` rojo | NAF / FER, mínimo regulatorio, variaciones desfavorables, incumplimiento |
| `#fb8b1f` naranja | Alerta interna, simulaciones |

Mínimo regulatorio: línea continua; alerta: discontinua; apetito: punteada.

---

## Metodología

- **LCR = ALAC / NAF.** ALAC aplica descuentos por nivel y los topes de composición (Nivel 2 ≤ 40%,
  Nivel 2B ≤ 15%); NAF = salidas − min(entradas; 75% de las salidas) (BCBS 238).
- **NSFR = FED / FER**, con factores por vencimiento residual (BCBS 295).
- **Descomposición:** el impacto de cada componente es el cambio del indicador al actualizar sólo
  esa línea, manteniendo el resto en el cierre anterior.
- **Pruebas de tensión:** shocks porcentuales por componente sobre montos computables. El
  indicador se recalcula con las mismas ponderaciones y topes; se informa el impacto individual de
  cada modificación y el efecto de interacción y topes. Escenarios Base, Adverso y Severo con
  supuestos visibles. Los escenarios pueden registrarse con usuario, fecha y shocks aplicados.
- **Puente de variaciones:** contribución por categoría entre dos cierres; el **residual** corresponde
  a la interacción entre numerador y denominador y al efecto de los topes.
- **Proyecciones:** Holt amortiguado (φ = 0,90) sobre los montos históricos de numerador y
  denominador. Los escenarios aplican sus shocks gradualmente en 30 días. Banda: ±1,96σ de los
  errores a un paso.
- **Precisión:** evaluación fuera de muestra con origen móvil (MAE, RMSE, MAPE, sesgo y cobertura
  del intervalo) para cuatro métodos.
- **Calidad de datos:** se valida antes de calcular (calendario, nulos, duplicados, detalle = total,
  factores, recálculo independiente). Un control con ERROR detiene el cálculo.

---

## Arquitectura de datos

| Archivo | Rol |
|---|---|
| `db_layer.py` | Base local (SQLite) de 4 capas: ledger inmutable, staging, core bitemporal y DWH Kimball |
| `regulatory_engine.py` | Fórmulas de Basilea III en aritmética decimal |
| `data_loader.py` | Carga los cuatro DataFrames (`INDICADOR_DATA_SOURCE`: HIBRIDO, SQLITE, SQLSERVER, SINTETICO) |
| `analytics.py` | Cierres, descomposición, tensión, proyecciones, puente, holguras, backtest, concentración, calidad |
| `report_store.py` | Registro append-only de escenarios y observaciones |
| `pdf_generator.py` | Informe PDF |
| `app.py`, `components/`, `styles.py` | Interfaz |
| `sql/00..06` | Scripts T-SQL de producción para SQL Server 2017 |

Para conectar SQL Server: `CORE_BANKING_SQLSERVER_URL="mssql+pyodbc://…"`.

### Scripts SQL Server 2017 (ejecutar en orden)

| Script | Contenido principal |
|---|---|
| `00` | Base, RCSI, esquemas, roles y partición mensual `pf_monthly_date_key` |
| `01` | Ledger inmutable: `oltp.usp_post_atomic_journal_entry` (TVP, idempotencia, outbox, dos fases), resolución y expiración de reservas, verificación SHA-256 |
| `02` | Bitácora de lotes, aterrizaje CDC, checksum de fila, inbox y conciliación |
| `03` | Core bitemporal AVF: insert, update, corrección, aserción futura e integridad sin solapamientos |
| `04` | DWH Kimball: dimensiones, tratamientos Basilea III y tres hechos columnstore particionados |
| `05` | `dwh.vw_c55_*_components_daily`, `dwh.vw_cmf_c55_lcr_nsfr_official` (topes del Anexo 1), C46, C54, C56 y saldo point-in-time |
| `06` | CDC del legado y del outbox, backfill de cierres, migración con aserción diferida, publicación por SWITCH PARTITION y conciliación de cuatro vías |

Las vistas de `05` replican exactamente la lógica de `regulatory_engine.py` y `db_layer.py`;
`data_loader.py` recalcula LCR y NSFR desde ellas y alerta ante cualquier diferencia. Los scripts no
se han ejecutado contra una instancia de SQL Server en este repositorio: valídelos en un ambiente de
pruebas antes de producción.

### Capturas de documentación

```bash
.venv/bin/python generate_capturas.py
```

Levanta su propia instancia del panel, recorre cada vista con Chrome headless y actualiza
`capturas/` (vistas, informe PDF de ejemplo y catálogo). Las capturas manuales se conservan.

## Pruebas

```bash
.venv/bin/python -m unittest discover -s tests -v
```
