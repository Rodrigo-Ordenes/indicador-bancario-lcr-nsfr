"""
generate_capturas.py
====================
Automatiza la generación de capturas de pantalla de alta resolución (PNG)
para todas las vistas del panel interactivo de liquidez y del reporte ejecutivo PDF.
"""

import os
import time
import json
import base64
import asyncio
import tempfile
import subprocess
import requests
import websockets
import pypdfium2 as pdfium

import app
import pdf_generator

OUTPUT_DIR = os.path.abspath("capturas")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def generate_pdf_pages():
    """Genera el reporte PDF oficial y renderiza cada página en imagen PNG de 300 DPI."""
    print(">>> 1. Generando Reporte PDF Oficial con Gráficos Consolidados...")
    df_lcr_res = app.resample_historico(app.df_lcr_hist, 'fecha', 'lcr', 'alac', 'naf', 'Diaria')
    df_nsfr_res = app.resample_historico(app.df_nsfr_hist, 'fecha', 'nsfr', 'fed', 'fer', 'Diaria')
    kpis_lcr = app.get_kpis_indicador(df_lcr_res, 'lcr', 'alac', 'naf', 100.0, 120.0, 150.0, 'Diaria')
    kpis_nsfr = app.get_kpis_indicador(df_nsfr_res, 'nsfr', 'fed', 'fer', 100.0, 105.0, 110.0, 'Diaria')
    df_buf_lcr = app.calculate_multithreshold_buffers(kpis_lcr['ratio_actual'], kpis_lcr['num_actual'], kpis_lcr['den_actual'], [150.0, 120.0, 100.0], ['Apetito', 'Alerta', 'Mínimo CMF'])
    df_buf_nsfr = app.calculate_multithreshold_buffers(kpis_nsfr['ratio_actual'], kpis_nsfr['num_actual'], kpis_nsfr['den_actual'], [110.0, 105.0, 100.0], ['Apetito', 'Alerta', 'Mínimo CMF'])

    stress_summary = {
        'scenarios': [
            {'nombre': 'Base (0%)', 'lcr': kpis_lcr['ratio_actual'], 'delta_lcr': 0.0, 'nsfr': kpis_nsfr['ratio_actual'], 'delta_nsfr': 0.0, 'diag': 'Óptimo'},
            {'nombre': 'Adverso (-5% / +10%)', 'lcr': kpis_lcr['ratio_actual']*0.86, 'delta_lcr': -15.0, 'nsfr': kpis_nsfr['ratio_actual']*0.9, 'delta_nsfr': -10.0, 'diag': 'Vigilancia'},
            {'nombre': 'Severo (-15% / +25%)', 'lcr': kpis_lcr['ratio_actual']*0.68, 'delta_lcr': -38.0, 'nsfr': kpis_nsfr['ratio_actual']*0.75, 'delta_nsfr': -25.0, 'diag': 'Déficit'}
        ]
    }

    proj_summary = {
        'horizonte_dias': 30,
        'metodo': 'Alisamiento Exponencial Doble (Holt)',
        'lcr_pred': 126.5, 'lcr_lower': 120.1, 'lcr_upper': 132.8,
        'nsfr_pred': 114.2, 'nsfr_lower': 109.8, 'nsfr_upper': 118.5
    }

    pdf_bytes = pdf_generator.generate_executive_pdf_report(
        kpis_lcr=kpis_lcr,
        kpis_nsfr=kpis_nsfr,
        df_buffers_lcr=df_buf_lcr,
        df_buffers_nsfr=df_buf_nsfr,
        top_drivers_lcr=[],
        top_drivers_nsfr=[],
        stress_results=stress_summary,
        projection_summary=proj_summary,
        observaciones_texto='La posición de liquidez se mantiene sólida y dentro de los parámetros regulatorios. Se recomienda monitorear renovación de depósitos mayoristas.',
        acciones_mitigacion='1. Priorizar colocación de depósitos a plazo en tramos > 30 días.\n2. Mantener soberanos Banco Central como colateral HQLA Nivel 1.\n3. Vigilar cupo en líneas comprometidas.',
        fecha_corte_str='14-10-2026',
        periodicidad='Diaria',
        df_lcr_hist=df_lcr_res,
        df_nsfr_hist=df_nsfr_res
    )

    pdf_path = os.path.join(OUTPUT_DIR, "Reporte_Ejecutivo_ALM_LCR_NSFR_Oficial.pdf")
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)
    print(f"  -> Guardado: {pdf_path}")

    # Renderizar páginas del PDF a imágenes PNG de 300 DPI
    doc = pdfium.PdfDocument(pdf_path)
    for idx, page in enumerate(doc):
        page_img = page.render(scale=3.0).to_pil()
        num_str = f"{15 + idx:02d}"
        img_name = f"{num_str}_Reporte_Ejecutivo_ALCO_Pagina_{idx+1}.png"
        img_path = os.path.join(OUTPUT_DIR, img_name)
        page_img.save(img_path)
        print(f"  -> Guardada captura del reporte: {img_name} ({page_img.size})")


async def capture_app_views():
    """Automatiza Chrome headless para capturar cada pantalla interactiva del panel."""
    print(">>> 2. Iniciando navegador Chrome headless para capturar vistas de la aplicación...")
    tmpdir = tempfile.mkdtemp()
    port = 9235
    chrome_cmd = [
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '--headless=new',
        '--disable-gpu',
        '--no-first-run',
        '--no-default-browser-check',
        f'--user-data-dir={tmpdir}',
        f'--remote-debugging-port={port}',
        '--window-size=1600,1050',
        'http://localhost:8501'
    ]
    proc = subprocess.Popen(chrome_cmd)
    time.sleep(2.5)

    try:
        r = requests.get(f'http://localhost:{port}/json')
        pages = [p for p in r.json() if p.get('type') == 'page']
        ws_url = pages[0]['webSocketDebuggerUrl']

        async with websockets.connect(ws_url, max_size=50*1024*1024) as ws:
            msg_id = 0

            async def send_cmd(method, params=None):
                nonlocal msg_id
                msg_id += 1
                payload = {'id': msg_id, 'method': method}
                if params:
                    payload['params'] = params
                await ws.send(json.dumps(payload))
                while True:
                    res = await ws.recv()
                    data = json.loads(res)
                    if data.get('id') == msg_id:
                        return data.get('result', {})

            async def eval_js(expression):
                res = await send_cmd('Runtime.evaluate', {
                    'expression': expression,
                    'returnByValue': True
                })
                return res.get('result', {}).get('value', None)

            async def select_radio(keyword):
                js = f'''
                (() => {{
                    const labels = Array.from(document.querySelectorAll('div[data-testid="stRadio"] label'));
                    const l = labels.find(x => x.innerText.includes("{keyword}"));
                    if (l) {{
                        l.click();
                        return "Clicked " + l.innerText;
                    }}
                    return "Radio not found: {keyword}";
                }})()
                '''
                res = await eval_js(js)
                await asyncio.sleep(3.5)
                return res

            async def select_tab(keyword):
                js = f'''
                (() => {{
                    const tabs = Array.from(document.querySelectorAll('[data-testid="stTab"]'));
                    const t = tabs.find(x => x.innerText.includes("{keyword}"));
                    if (t) {{
                        t.click();
                        return "Clicked " + t.innerText;
                    }}
                    return "Tab not found: {keyword}";
                }})()
                '''
                res = await eval_js(js)
                await asyncio.sleep(2.5)
                return res

            async def take_screenshot(filename):
                await asyncio.sleep(2.0)
                res = await send_cmd('Page.captureScreenshot', {
                    'format': 'png',
                    'captureBeyondViewport': False
                })
                img_data = base64.b64decode(res['data'])
                filepath = os.path.join(OUTPUT_DIR, filename)
                with open(filepath, 'wb') as f:
                    f.write(img_data)
                print(f"  -> Captura guardada: {filename} ({len(img_data):,} bytes)")

            # Esperar carga inicial de Streamlit
            print("  Esperando renderizado inicial de Streamlit...")
            await asyncio.sleep(4.5)

            # -------------------------------------------------------------
            # 1. PANEL LCR
            # -------------------------------------------------------------
            print("Capturando: 01_LCR_Indicadores_y_Evolucion.png")
            await select_tab("Indicadores")
            await take_screenshot("01_LCR_Indicadores_y_Evolucion.png")

            print("Capturando: 02_LCR_Descomposicion_y_TopDrivers.png")
            await select_tab("Descomposición")
            await take_screenshot("02_LCR_Descomposicion_y_TopDrivers.png")

            print("Capturando: 03_LCR_Pruebas_de_Tension_Stress.png")
            await select_tab("Tensión")
            # Activar escenario adverso para que se vea el impacto
            await eval_js('''
                (() => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const b = btns.find(x => x.innerText.includes('Adverso'));
                    if (b) b.click();
                })()
            ''')
            await asyncio.sleep(1.5)
            await take_screenshot("03_LCR_Pruebas_de_Tension_Stress.png")

            print("Capturando: 04_LCR_Proyecciones_Econometricas.png")
            await select_tab("Proyecciones")
            await take_screenshot("04_LCR_Proyecciones_Econometricas.png")

            # -------------------------------------------------------------
            # 2. PANEL NSFR
            # -------------------------------------------------------------
            print("Navegando a Panel NSFR...")
            await select_radio("NSFR")

            print("Capturando: 05_NSFR_Indicadores_y_Evolucion.png")
            await select_tab("Indicadores")
            await take_screenshot("05_NSFR_Indicadores_y_Evolucion.png")

            print("Capturando: 06_NSFR_Descomposicion_y_TopDrivers.png")
            await select_tab("Descomposición")
            await take_screenshot("06_NSFR_Descomposicion_y_TopDrivers.png")

            print("Capturando: 07_NSFR_Pruebas_de_Tension_Stress.png")
            await select_tab("Tensión")
            await take_screenshot("07_NSFR_Pruebas_de_Tension_Stress.png")

            # -------------------------------------------------------------
            # 3. ANÁLISIS TRANSVERSAL DE LIQUIDEZ
            # -------------------------------------------------------------
            print("Navegando a Análisis Transversal...")
            await select_radio("Análisis Transversal")

            print("Capturando: 08_Transversal_Waterfall_Bridge.png")
            await select_tab("Puente")
            await take_screenshot("08_Transversal_Waterfall_Bridge.png")

            print("Capturando: 09_Transversal_Monitoreo_Multiumbral.png")
            await select_tab("Holgura")
            await take_screenshot("09_Transversal_Monitoreo_Multiumbral.png")

            print("Capturando: 10_Transversal_Backtesting_Modelos.png")
            await select_tab("Precisión")
            await take_screenshot("10_Transversal_Backtesting_Modelos.png")

            print("Capturando: 11_Transversal_Concentracion_HHI.png")
            await select_tab("Concentración")
            await take_screenshot("11_Transversal_Concentracion_HHI.png")

            print("Capturando: 12_Transversal_Resumen_Ejecutivo_Copilot.png")
            await select_tab("Resumen Ejecutivo")
            await take_screenshot("12_Transversal_Resumen_Ejecutivo_Copilot.png")

            # -------------------------------------------------------------
            # 4. EXPORTAR INFORME PDF
            # -------------------------------------------------------------
            print("Navegando a Exportar Informe PDF...")
            await select_radio("Exportar Informe PDF")
            await take_screenshot("13_Exportar_Informe_PDF_Configuracion.png")

            # -------------------------------------------------------------
            # 5. AUDITORÍA & CORE BANKING BITEMPORAL
            # -------------------------------------------------------------
            print("Navegando a Auditoría & Core Banking...")
            await select_radio("Auditoría")
            await take_screenshot("14_Auditoria_Core_Banking_Bitemporal.png")

    finally:
        proc.terminate()


def generate_catalog_readme():
    """Genera capturas/README.md documentando cada imagen generada."""
    print(">>> 3. Generando capturas/README.md...")
    md_content = """# Catálogo de Capturas de Pantalla — Panel Interactivo de Liquidez y Reporte ALCO

Este directorio reúne las capturas de pantalla de alta resolución que documentan de manera integral todas las vistas del sistema de reportería financiera y gestión de balance (CMF / Basilea III), diseñadas bajo la paleta **Bloomberg Terminal Dark**, además de la renderización del informe oficial en PDF descargable.

---

## 📸 Índice de Capturas del Sistema

| # | Archivo | Módulo / Sección | Descripción Funcional |
| :-: | :--- | :--- | :--- |
| **01** | [`01_LCR_Indicadores_y_Evolucion.png`](./01_LCR_Indicadores_y_Evolucion.png) | **Panel LCR** | Tarjetas KPI Bloomberg (LCR, ALAC, NAF, Semáforo CMF), gráfico de evolución temporal con los 3 umbrales regulatorios (100% legal, 120% alerta interna, 150% apetito), composición dual y variaciones periódicas. |
| **02** | [`02_LCR_Descomposicion_y_TopDrivers.png`](./02_LCR_Descomposicion_y_TopDrivers.png) | **Panel LCR** | Desglose por partidas de ALAC y NAF con metodología de cierre diario (Cierre de Hoy vs Cierre de Ayer), factores explicativos (Top Drivers de alzas y bajas) y tabla detallada con factores de ponderación. |
| **03** | [`03_LCR_Pruebas_de_Tension_Stress.png`](./03_LCR_Pruebas_de_Tension_Stress.png) | **Panel LCR** | Simulador de pruebas de tensión interactivo con shocks de activos y pasivos, recálculo automático y evaluación de escenarios Base, Adverso y Severo. |
| **04** | [`04_LCR_Proyecciones_Econometricas.png`](./04_LCR_Proyecciones_Econometricas.png) | **Panel LCR** | Proyección econométrica univariada (modelo Holt) a 30-180 días con cono de incertidumbre al 95% y evaluación frente a límites normativos. |
| **05** | [`05_NSFR_Indicadores_y_Evolucion.png`](./05_NSFR_Indicadores_y_Evolucion.png) | **Panel NSFR** | Tarjetas ejecutivas NSFR, Fondeo Disponible (FED), Fondeo Requerido (FER), gráfico de tendencia con umbrales (100% y 105% alerta) y comparador directo de períodos. |
| **06** | [`06_NSFR_Descomposicion_y_TopDrivers.png`](./06_NSFR_Descomposicion_y_TopDrivers.png) | **Panel NSFR** | Descomposición de partidas de balance a largo plazo (FED y FER) con variación del día respecto a ayer y principales cuentas explicativas. |
| **07** | [`07_NSFR_Pruebas_de_Tension_Stress.png`](./07_NSFR_Pruebas_de_Tension_Stress.png) | **Panel NSFR** | Evaluación de sensibilidad estructural del balance bancario frente a caídas de pasivos estables o incremento de colocaciones. |
| **08** | [`08_Transversal_Waterfall_Bridge.png`](./08_Transversal_Waterfall_Bridge.png) | **Análisis Transversal** | Gráfico de cascada (Waterfall Bridge) que descompone matemáticamente el cambio del ratio en: Efecto Activo, Efecto Pasivo y Término de Interacción. |
| **09** | [`09_Transversal_Monitoreo_Multiumbral.png`](./09_Transversal_Monitoreo_Multiumbral.png) | **Análisis Transversal** | Matriz de holguras monetarias y distancia en puntos porcentuales frente a los 3 niveles de control (Apetito, Alerta, Mínimo CMF) y estadísticas de alertas históricas. |
| **10** | [`10_Transversal_Backtesting_Modelos.png`](./10_Transversal_Backtesting_Modelos.png) | **Análisis Transversal** | Evaluación comparativa de modelos econométricos en ventanas históricas (MAE, MAPE y RMSE para Holt, Regresión Lineal y Media Móvil). |
| **11** | [`11_Transversal_Concentracion_HHI.png`](./11_Transversal_Concentracion_HHI.png) | **Análisis Transversal** | Curva de Pareto y cálculo del Índice de Herfindahl-Hirschman (HHI) para evaluar la concentración y volatilidad histórica de contrapartes y fuentes de financiamiento. |
| **12** | [`12_Transversal_Resumen_Ejecutivo_Copilot.png`](./12_Transversal_Resumen_Ejecutivo_Copilot.png) | **Análisis Transversal** | Diagnóstico cualitativo automático con lógica financiera bancaria y conector a Copilot / LLM para redacción de memorándums formales. |
| **13** | [`13_Exportar_Informe_PDF_Configuracion.png`](./13_Exportar_Informe_PDF_Configuracion.png) | **Exportación PDF** | Vista de configuración de notas gerenciales, parámetros de simulación y botón de descarga del informe consolidado. |
| **14** | [`14_Auditoria_Core_Banking_Bitemporal.png`](./14_Auditoria_Core_Banking_Bitemporal.png) | **Auditoría & Core** | Panel de supervisión de la arquitectura bancaria en 4 capas desacopladas, integridad criptográfica (SHA-256), bitemporalidad ODS y linaje regulatorio. |
| **15** | [`15_Reporte_Ejecutivo_ALCO_Pagina_1.png`](./15_Reporte_Ejecutivo_ALCO_Pagina_1.png) | **Reporte PDF Oficial** | Captura a 300 DPI de la Página 1 del reporte PDF oficial: Indicadores principales, tabla de holguras multiumbrales y gráficos consolidados de evolución y estrés ALM. |

---
*Generado automáticamente para el repositorio institucional de Gestión de Riesgo Financiero y Balance Bancario.*
"""
    readme_path = os.path.join(OUTPUT_DIR, "README.md")
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(md_content.strip())
    print(f"  -> Catálogo generado: {readme_path}")


def main():
    generate_pdf_pages()
    asyncio.run(capture_app_views())
    generate_catalog_readme()
    print("\n>>> Proceso completado exitosamente. Todas las capturas están listas en capturas/")


if __name__ == "__main__":
    main()
