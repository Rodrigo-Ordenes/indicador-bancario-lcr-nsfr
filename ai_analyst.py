"""
ai_analyst.py
=============
Módulo de Inteligencia Artificial para la generación de análisis ejecutivos
de liquidez y balance bancario (ALM / Riesgo Financiero).

Incluye:
1. Motor Cuantitativo Heurístico Institucional:
   Genera análisis y diagnósticos matemáticos automáticos basados en directrices
   de Basilea III / CMF y Comités ALCO (funciona 100% offline sin costo ni API key).
2. Conexión a LLM (Copilot / OpenAI / Azure OpenAI):
   Permite conectar un endpoint de IA generativa (OpenAI / Azure Copilot / GitHub Models)
   para redactar un memo ejecutivo completo para la Gerencia General o Directorio.
"""

import os
import streamlit as st
import pandas as pd
from typing import Dict, Any, Optional

def generate_heuristic_executive_analysis(
    kpis: Dict[str, Any],
    df_detalle: pd.DataFrame,
    ratio_nombre: str,
    num_nombre: str,
    den_nombre: str,
    granularidad: str
) -> str:
    """
    Genera un análisis ejecutivo automatizado con lógica financiera bancaria rigurosa.
    """
    if not kpis:
        return "No hay suficientes datos históricos para estructurar el análisis."

    ratio_act = kpis['ratio_actual']
    ratio_ant = kpis['ratio_anterior']
    delta_ratio = kpis['delta_ratio']
    delta_num = kpis['delta_num']
    delta_den = kpis['delta_den']
    holgura_pp = kpis['holgura_pp']
    holgura_monto = kpis['holgura_monto']
    semaforo = kpis['semaforo']

    # 1. Diagnóstico de Cumplimiento
    if ratio_act >= 105.0:
        diagnostico = (
            f"El indicador **{ratio_nombre}** se sitúa en **{ratio_act:.2f}%**, manteniendo una posición "
            f"altamente sólida y confortable frente al requerimiento normativo del 100.0% (+{holgura_pp:.2f} pp de holgura, "
            f"equivalente a un colchón excedente de **${holgura_monto:,.0f} MM**)."
        )
    elif ratio_act >= 100.0:
        diagnostico = (
            f"El indicador **{ratio_nombre}** se ubica en **{ratio_act:.2f}%**, en cumplimiento estricto "
            f"pero dentro de la zona de vigilancia activa (apetito interno fijado en 105.0%). "
            f"La holgura normativa es de **+{holgura_pp:.2f} pp** (${holgura_monto:,.0f} MM)."
        )
    else:
        diagnostico = (
            f"⚠️ **ALERTA CRÍTICA**: El indicador **{ratio_nombre}** registra **{ratio_act:.2f}%**, "
            f"incumpliendo el límite regulatorio mínimo de 100.0% con un déficit de **{abs(holgura_pp):.2f} pp** "
            f"(-${abs(holgura_monto):,.0f} MM). Requiere activación inmediata del plan de contingencia de liquidez."
        )

    # 2. Atribución de Drivers (Numerador vs Denominador)
    direccion = "aumento" if delta_ratio >= 0 else "contracción"
    if delta_ratio >= 0:
        explicacion_driver = (
            f"La variación positiva de **+{delta_ratio:.2f} pp** en el período {granularidad.lower()} fue explicada por: "
        )
    else:
        explicacion_driver = (
            f"La reducción de **{delta_ratio:.2f} pp** en el período {granularidad.lower()} obedeció a: "
        )

    drivers_list = []
    if delta_num != 0:
        sign_num = "+" if delta_num > 0 else ""
        impacto_num = "favoreciendo" if delta_num > 0 else "restando liquidez a"
        drivers_list.append(f"Variación en {num_nombre} de **{sign_num}${delta_num:,.0f} MM** ({impacto_num} el ratio).")
    if delta_den != 0:
        sign_den = "+" if delta_den > 0 else ""
        impacto_den = "presionando a la baja el indicador" if delta_den > 0 else "aliviando la exigencia del indicador"
        drivers_list.append(f"Variación en {den_nombre} de **{sign_den}${delta_den:,.0f} MM** ({impacto_den}).")

    # 3. Top drivers desde la descomposición detallada (si existe)
    detalle_bullets = []
    if len(df_detalle) > 0 and 'delta_ponderado' in df_detalle.columns:
        # Mayor alza y mayor baja en detalle
        df_sorted = df_detalle.sort_values(by='delta_ponderado', ascending=False)
        top_alza = df_sorted.iloc[0]
        top_baja = df_sorted.iloc[-1]

        if top_alza['delta_ponderado'] > 0:
            detalle_bullets.append(
                f"**Mayor incremento en cuentas:** *{top_alza['subcategoria']}* ({top_alza['componente']}) con +${top_alza['delta_ponderado']:,.0f} MM."
            )
        if top_baja['delta_ponderado'] < 0:
            detalle_bullets.append(
                f"**Mayor reducción en cuentas:** *{top_baja['subcategoria']}* ({top_baja['componente']}) con ${top_baja['delta_ponderado']:,.0f} MM."
            )

    # 4. Recomendaciones para ALCO y Mesa de Dinero
    recomendaciones = []
    if ratio_act < 105.0:
        if ratio_nombre == "LCR":
            recomendaciones.append("Incrementar la posición en Activos Nivel 1 (Pagarés BCCh / Bonos BTP) vía operaciones repo o captaciones mayoristas de plazo > 30 días.")
            recomendaciones.append("Monitorear la renovación de depósitos institucionales no operacionales que vencen en el tramo de 30 días.")
        else:
            recomendaciones.append("Extender el plazo promedio del fondeo pasivo hacia horizontes superiores a 1 año para incrementar FED.")
            recomendaciones.append("Limitar el crecimiento acelerado de colocaciones con alto ponderador RSF hasta recomponer el buffer.")
    else:
        recomendaciones.append("Mantener la estructura actual de calce de liquidez y optimizar el costo de acarreo (carry cost) de los activos líquidos.")
        recomendaciones.append("Verificar que la concentración de contrapartes mayoristas se mantenga dentro de los límites de apetito de riesgo.")

    # Ensamblar Markdown final
    analysis_md = f"""
### 📊 Diagnóstico Ejecutivo ALM ({granularidad})
{diagnostico}

#### 🔍 Atribución del Movimiento
{explicacion_driver}
- {"<br>- ".join(drivers_list)}

"""
    if detalle_bullets:
        analysis_md += f"""#### 📌 Cuentas con Mayor Dinamismo
- {"<br>- ".join(detalle_bullets)}

"""

    analysis_md += f"""#### 💡 Recomendaciones para Comité ALCO / Mesa de Dinero
- {"<br>- ".join(recomendaciones)}
"""
    return analysis_md


def call_llm_copilot_analysis(
    kpis: Dict[str, Any],
    df_detalle: pd.DataFrame,
    ratio_nombre: str,
    num_nombre: str,
    den_nombre: str,
    granularidad: str,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    model_name: str = "gpt-4o"
) -> str:
    """
    Invoca un LLM (OpenAI / Azure OpenAI / GitHub Copilot API) para generar un informe
    ejecutivo de alta dirección estructurado y enriquecido.
    """
    key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("GITHUB_TOKEN")
    if not key:
        return (
            "⚠️ **API Key no configurada para Copilot / LLM.**\n\n"
            "Para activar el análisis en tiempo real con Copilot / OpenAI, ingrese su API Key "
            "en el panel lateral (Configuración de IA) o defina la variable de entorno `OPENAI_API_KEY`.\n\n"
            "👉 *Se ha generado el análisis cuantitativo interno arriba de forma automática.*"
        )

    try:
        from openai import OpenAI

        client_kwargs = {"api_key": key}
        if api_base:
            client_kwargs["base_url"] = api_base

        client = OpenAI(**client_kwargs)

        # Preparar resumen de datos para el prompt
        summary_text = f"""
        Indicador Financiero: {ratio_nombre} ({granularidad})
        Valor Actual: {kpis['ratio_actual']:.2f}%
        Valor Período Anterior: {kpis['ratio_anterior']:.2f}%
        Variación Delta: {kpis['delta_ratio']:+.2f} pp ({kpis['delta_ratio_pct']:+.2f}%)
        
        Componente {num_nombre}:
        - Monto Actual: ${kpis['num_actual']:,.0f} MM
        - Variación: {kpis['delta_num']:+,.0f} MM ({kpis['delta_num_pct']:+.2f}%)
        
        Componente {den_nombre}:
        - Monto Actual: ${kpis['den_actual']:,.0f} MM
        - Variación: {kpis['delta_den']:+,.0f} MM ({kpis['delta_den_pct']:+.2f}%)
        
        Holgura sobre Mínimo Normativo CMF (100%): {kpis['holgura_pp']:+.2f} pp
        Colchón Excedente: ${kpis['holgura_monto']:+,.0f} MM
        Semáforo: {kpis['semaforo']}
        """

        # Agregar top 5 cuentas por variación si están disponibles
        if len(df_detalle) > 0 and 'delta_ponderado' in df_detalle.columns:
            top_var = df_detalle.sort_values(by='delta_ponderado', ascending=False)[
                ['componente', 'categoria', 'subcategoria', 'monto_ponderado', 'delta_ponderado']
            ].head(5).to_string(index=False)
            summary_text += f"\n\nTop Cuentas y Variaciones:\n{top_var}"

        prompt = f"""
        Eres el Chief Risk Officer (CRO) y Director de ALM (Asset & Liability Management) de un banco líder.
        Escribe un memorándum ejecutivo conciso, elegante y de alto impacto para la Gerencia General y el Comité ALCO
        analizando los siguientes resultados del indicador {ratio_nombre}:

        {summary_text}

        Estructura tu informe exactamente en:
        1. 🏛️ **Resumen Ejecutivo para la Alta Dirección** (1 párrafo de síntesis clara sobre cumplimiento y solidez).
        2. 🔍 **Diagnóstico de Atribución y Sensibilidad** (explicar matemáticamente qué componente originó la variación).
        3. ⚠️ **Riesgos Emergentes y Pruebas de Estrés** (qué vulnerabilidades o concentraciones vigilar).
        4. 📋 **Plan de Acción para la Mesa de Dinero / Fondeo** (2 a 3 recomendaciones tácticas de tesorería y balance).

        Mantén un tono estrictamente bancario, profesional y cuantitativo.
        """

        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "Eres un experto cuantitativo en riesgo bancario, regulación de Basilea III y gestión ALM."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=900
        )

        return response.choices[0].message.content

    except Exception as e:
        return f"⚠️ **Error al conectar con el servicio de IA (Copilot/OpenAI):** {str(e)}"
