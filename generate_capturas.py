"""
generate_capturas.py
====================
Regenera las capturas de documentación del panel (capturas/*.png), el informe PDF
de ejemplo y el catálogo capturas/README.md.

Funcionamiento:
1. Levanta su propia instancia de Streamlit con el código actual en un puerto libre
   (no usa servidores ya abiertos, que podrían estar ejecutando una versión anterior).
2. Controla Google Chrome en modo headless mediante el protocolo DevTools: elige el
   módulo, la pestaña y, cuando corresponde, el escenario, y captura la página
   completa de la vista activa.
3. Genera el informe PDF con pdf_generator.build_report y renderiza cada página.

Uso:  .venv/bin/python generate_capturas.py
Requiere Google Chrome instalado (ruta CHROME).
"""

import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import pandas as pd
import requests
import websockets

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(HERE, "capturas")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
WIDTH = 1600

# (archivo, módulo, pestaña, escenario, descripción)
VIEWS = [
    ("01_LCR_Indicadores_y_Evolucion.png", "1 · Panel LCR", "Indicadores y evolución", None,
     "Indicadores LCR, ALAC y NAF con variación vs cierre anterior, situación frente a umbrales "
     "(100% regulatorio, 120% alerta, 150% apetito), evolución, variaciones y comparación de períodos."),
    ("02_LCR_Descomposicion.png", "1 · Panel LCR", "Descomposición", None,
     "Componentes de ALAC y NAF: monto actual y anterior, variación absoluta y %, impacto en el LCR y "
     "principales componentes que explican el cambio."),
    ("03_LCR_Pruebas_de_Tension_Severo.png", "1 · Panel LCR", "Pruebas de tensión", "Severo",
     "Escenario severo: shocks por componente, recálculo con topes Basilea III, impacto individual, "
     "comparación de escenarios y registro trazable."),
    ("04_LCR_Proyecciones.png", "1 · Panel LCR", "Proyecciones", None,
     "Proyección del LCR bajo escenarios base, adverso y severo con banda del 95%, cruces de umbrales y supuestos."),
    ("05_NSFR_Indicadores_y_Evolucion.png", "2 · Panel NSFR", "Indicadores y evolución", None,
     "Indicadores NSFR, FED y FER con umbrales (100% regulatorio, 105% alerta), evolución y comparación."),
    ("06_NSFR_Descomposicion.png", "2 · Panel NSFR", "Descomposición", None,
     "Componentes de FED y FER con variaciones y factores que explican el cambio."),
    ("07_NSFR_Pruebas_de_Tension_Adverso.png", "2 · Panel NSFR", "Pruebas de tensión", "Adverso",
     "Escenario adverso sobre FED y FER con impacto por componente."),
    ("08_NSFR_Proyecciones.png", "2 · Panel NSFR", "Proyecciones", None,
     "Proyección del NSFR bajo tensión y con montos históricos de FED y FER."),
    ("09_Transversal_Puente_de_Variaciones.png", "3 · Análisis transversal", "Puente de variaciones", None,
     "Puente del indicador por categoría con contribuciones positivas, negativas y residual por interacción y topes."),
    ("10_Transversal_Holgura.png", "3 · Análisis transversal", "Holgura frente a límites", None,
     "Distancia y holgura monetaria frente a cada umbral, días consecutivos bajo alerta y monto requerido."),
    ("11_Transversal_Precision_Proyecciones.png", "3 · Análisis transversal", "Precisión de proyecciones", None,
     "Backtesting fuera de muestra: MAE, RMSE, MAPE, sesgo y cobertura del intervalo por método."),
    ("12_Transversal_Concentracion.png", "3 · Análisis transversal", "Concentración y vulnerabilidad", None,
     "Concentración (HHI, top 5), volatilidad histórica y dependencia de fondeo mayorista."),
    ("13_Transversal_Resumen_Ejecutivo.png", "3 · Análisis transversal", "Resumen ejecutivo", None,
     "Situación consolidada, conclusión, movimientos, indicadores próximos a umbrales, deterioro, "
     "escenario adverso y registro de observaciones."),
    ("14_Informe_PDF.png", "4 · Informe PDF", None, None,
     "Configuración del informe: fecha de corte, período, escenarios y observaciones."),
    ("15_Calidad_y_Trazabilidad.png", "5 · Calidad y trazabilidad de datos", None, None,
     "Controles de calidad previos al cálculo y arquitectura de datos (ledger, core bitemporal, DWH)."),
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, timeout: float = 90) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if requests.get(url, timeout=2).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise TimeoutError(f"No respondió {url}")


class Page:
    def __init__(self, ws):
        self.ws, self.i = ws, 0

    async def cmd(self, method, params=None):
        self.i += 1
        msg_id = self.i
        await self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        while True:
            data = json.loads(await self.ws.recv())
            if data.get("id") == msg_id:
                if "error" in data:
                    raise RuntimeError(data["error"])
                return data.get("result", {})

    async def js(self, expr):
        r = await self.cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        return r.get("result", {}).get("value")

    async def settle(self, timeout: float = 90):
        """Espera a que Streamlit termine de ejecutar el script (sin indicador de ejecución durante 1,5 s)."""
        t0, quiet = time.time(), 0.0
        await asyncio.sleep(1.0)
        while time.time() - t0 < timeout:
            running = await self.js("!!document.querySelector('[data-testid=\"stStatusWidget\"]')")
            quiet = quiet + 0.5 if not running else 0.0
            if quiet >= 1.5:
                return
            await asyncio.sleep(0.5)

    async def click_text(self, scope_selector: str, element_selector: str, text: str) -> bool:
        """Clic real (eventos de mouse vía DevTools) sobre el primer elemento visible que contiene el texto."""
        finder = f"""(() => {{
            const scope = document.querySelector({json.dumps(scope_selector)}) || document;
            const els = [...scope.querySelectorAll({json.dumps(element_selector)})]
                .filter(e => e.offsetParent !== null && e.innerText.trim().includes({json.dumps(text)}));
            if (!els.length) return null;
            els[0].scrollIntoView({{block: 'center'}});
            const r = els[0].getBoundingClientRect();
            return {{x: r.left + Math.min(r.width / 2, 20), y: r.top + r.height / 2}}; }})()"""
        box, t0 = None, time.time()
        while box is None and time.time() - t0 < 30:   # Streamlit puede seguir dibujando tras navegar
            box = await self.js(finder)
            if box is None:
                await asyncio.sleep(0.5)
        if not box:
            print(f"     (no se encontró '{text}')")
            return False
        for kind in ("mousePressed", "mouseReleased"):
            await self.cmd("Input.dispatchMouseEvent",
                           {"type": kind, "x": box["x"], "y": box["y"], "button": "left", "clickCount": 1})
        await self.settle()
        return True

    async def screenshot(self, path: str):
        height = await self.js("""(() => {
            const m = document.querySelector('[data-testid="stMain"]') || document.body;
            const b = document.querySelector('[data-testid="stMainBlockContainer"]') || m;
            return Math.min(Math.max(b.scrollHeight + 120, 900), 6000); })()""")
        await self.js("window.scrollTo(0, 0); (document.querySelector('[data-testid=\"stMain\"]') || document.body).scrollTop = 0;")
        await self.cmd("Emulation.setDeviceMetricsOverride",
                       {"width": WIDTH, "height": int(height), "deviceScaleFactor": 1, "mobile": False})
        await asyncio.sleep(2.0)
        shot = await self.cmd("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        with open(path, "wb") as f:
            f.write(base64.b64decode(shot["data"]))
        await self.cmd("Emulation.setDeviceMetricsOverride",
                       {"width": WIDTH, "height": 1000, "deviceScaleFactor": 1, "mobile": False})


async def capture_views(app_url: str) -> None:
    tmp = tempfile.mkdtemp()
    port = free_port()
    chrome = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                               f"--user-data-dir={tmp}", f"--remote-debugging-port={port}",
                               f"--window-size={WIDTH},1000", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_http(f"http://127.0.0.1:{port}/json/version", 30)
        target = next(t for t in requests.get(f"http://127.0.0.1:{port}/json").json() if t["type"] == "page")
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=None) as ws:
            page = Page(ws)
            await page.cmd("Page.enable")
            await page.cmd("Runtime.enable")
            for fname, module, tab, scenario, _ in VIEWS:
                await page.cmd("Page.navigate", {"url": app_url})
                await page.settle()
                await page.click_text('section[data-testid="stSidebar"]', "label", module)
                if tab:
                    await page.click_text('[data-testid="stMain"]', '[role="tab"]', tab)
                if scenario:
                    await page.click_text('[data-testid="stMain"]', "label", scenario)
                await page.screenshot(os.path.join(OUTPUT_DIR, fname))
                print(f"  -> {fname}")
    finally:
        chrome.terminate()
        shutil.rmtree(tmp, ignore_errors=True)


def generate_pdf() -> list:
    """Informe PDF de ejemplo con los datos actuales; devuelve los nombres de las páginas renderizadas."""
    sys.path.insert(0, HERE)
    import analytics as an
    import data_loader as dl
    import pdf_generator
    import pypdfium2 as pdfium
    import report_store

    b = dl._bundle_from_sqlite()
    corte = pd.Timestamp(b["lcr_hist"]["fecha"].max())
    calidad = pd.concat([an.data_quality(b["lcr_hist"], b["lcr_det"], an.LCR).assign(Indicador="LCR"),
                         an.data_quality(b["nsfr_hist"], b["nsfr_det"], an.NSFR).assign(Indicador="NSFR")])
    pdf = pdf_generator.build_report(b["lcr_hist"], b["lcr_det"], b["nsfr_hist"], b["nsfr_det"], corte, "Mensual", 12,
                                     ["Adverso", "Severo"], 60, an.umbrales_lcr(), an.umbrales_nsfr(),
                                     report_store.observations(), "", calidad, "Base local core_banking.db")
    path = os.path.join(OUTPUT_DIR, "Informe_Liquidez_LCR_NSFR_ejemplo.pdf")
    with open(path, "wb") as f:
        f.write(pdf)
    names = []
    for i, p in enumerate(pdfium.PdfDocument(path)):
        name = f"PDF_pagina_{i + 1}.png"
        p.render(scale=2.0).to_pil().save(os.path.join(OUTPUT_DIR, name))
        names.append(name)
    print(f"  -> {os.path.basename(path)} ({len(names)} páginas)")
    return names


def write_catalog(pdf_pages: list) -> None:
    rows = "\n".join(f"| {f[:2]} | [`{f}`](./{f}) | {m} | {t or '—'} | {d} |" for f, m, t, _, d in VIEWS)
    pages = "\n".join(f"| [`{p}`](./{p}) |" for p in pdf_pages)
    with open(os.path.join(OUTPUT_DIR, "README.md"), "w", encoding="utf-8") as f:
        f.write(f"""# Capturas del panel de liquidez

Generadas con `generate_capturas.py` el {time.strftime('%d-%m-%Y %H:%M')} a partir del código y la base actuales.

| # | Archivo | Módulo | Pestaña | Contenido |
|---|---|---|---|---|
{rows}

## Informe PDF de ejemplo

[`Informe_Liquidez_LCR_NSFR_ejemplo.pdf`](./Informe_Liquidez_LCR_NSFR_ejemplo.pdf)

| Página |
|---|
{pages}
""")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    # Sólo se reemplazan archivos generados por este script (las capturas manuales se conservan).
    generated = {v[0] for v in VIEWS} | {"Informe_Liquidez_LCR_NSFR_ejemplo.pdf"}
    for f in os.listdir(OUTPUT_DIR):
        if f in generated or (f.startswith("PDF_pagina_") and f.endswith(".png")):
            os.remove(os.path.join(OUTPUT_DIR, f))
    print(">>> Informe PDF")
    pdf_pages = generate_pdf()
    port = free_port()
    print(f">>> Iniciando Streamlit en el puerto {port}")
    server = subprocess.Popen([sys.executable, "-m", "streamlit", "run", os.path.join(HERE, "app.py"),
                               "--server.port", str(port), "--server.headless", "true",
                               "--browser.gatherUsageStats", "false"],
                              cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_http(f"http://127.0.0.1:{port}/_stcore/health")
        print(">>> Capturando vistas")
        asyncio.run(capture_views(f"http://127.0.0.1:{port}"))
    finally:
        server.terminate()
    write_catalog(pdf_pages)
    print(">>> Listo:", OUTPUT_DIR)


if __name__ == "__main__":
    main()
