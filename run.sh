#!/usr/bin/env bash
# run.sh - Inicia la aplicación Streamlit en localhost:8501

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

# Activar entorno virtual
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

echo "=========================================================="
echo "🏦 Iniciando Dashboard Ejecutivo LCR & NSFR..."
echo "🌐 URL Local: http://localhost:8501"
echo "=========================================================="

streamlit run app.py --server.port 8501 --server.headless false
