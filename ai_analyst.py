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
    granularidad: str = "Diaria",
    *args,
    **kwargs
) -> str:
    """
    Genera un análisis ejecutivo automatizado con lógica financiera bancaria rigurosa
    considerando los umbrales de 100% (mínimo), alerta interna (120%/105%) y apetito (150%/110%).
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
    
    limite_norm = kpis.get('limite_normativo', 100.0)
    limite_alerta = kpis.get('limite_interno', 120.0 if ratio_nombre == 'LCR' else 105.0)
    apetito = kpis.get('apetito_riesgo', 150.0 if ratio_nombre == 'LCR' else 110.0)

    # 1. Diagnóstico de Cumplimiento
    if ratio_act >= apetito:
        diagnostico = (
            f"El indicador **{ratio_nombre}** se sitúa en **{ratio_act:.2f}%**, manteniendo una posición "
            f"altamente sólida y confortable sobre el apetito por riesgo ({apetito:.0f}%) y el mínimo normativo del {limite_norm:.0f}% "
            f"(holgura normativa de **+{holgura_pp:.2f} pp**, equivalente a un colchón excedente de **${holgura_monto:,.0f} MM**)."
        )
    elif ratio_act >= limite_alerta:
        diagnostico = (
            f"El indicador **{ratio_nombre}** registra **{ratio_act:.2f}%**, ubicándose en cumplimiento regulatorio "
            f"y sobre el nivel de alerta interna ({limite_alerta:.0f}%), con una distancia de **{apetito - ratio_act:.2f} pp** "
            f"frente al nivel óptimo de apetito ({apetito:.0f}%). Holgura frente al 100%: **+{holgura_pp:.2f} pp** (${holgura_monto:,.0f} MM)."
        )
    elif ratio_act >= limite_norm:
        diagnostico = (
            f"**Alerta interna ALCO**: El indicador **{ratio_nombre}** se sitúa en **{ratio_act:.2f}%**, por debajo del "
            f"nivel de alerta ({limite_alerta:.0f}%), aunque aún en cumplimiento del mínimo CMF ({limite_norm:.0f}%). "
            f"La holgura normativa remanente es de solo **+{holgura_pp:.2f} pp** (${holgura_monto:,.0f} MM). Se requiere vigilancia activa."
        )
    else:
        diagnostico = (
            f"**Incumplimiento normativo crítico**: El indicador **{ratio_nombre}** colapsa a **{ratio_act:.2f}%**, "
            f"quebrantando el requerimiento regulatorio del {limite_norm:.0f}% con un déficit de **{abs(holgura_pp):.2f} pp** "
            f"(-${abs(holgura_monto):,.0f} MM). Requiere activación inmediata del plan de contingencia de liquidez."
        )

    # 2. Atribución de Drivers
    if delta_ratio >= 0:
        explicacion_driver = f"La variación positiva de **+{delta_ratio:.2f} pp** en el período {granularidad.lower()} fue impulsada por: "
    else:
        explicacion_driver = f"La contracción de **{delta_ratio:.2f} pp** en el período {granularidad.lower()} obedeció a: "

    drivers_list = []
    if delta_num != 0:
        sign_num = "+" if delta_num > 0 else ""
        impacto_num = "favoreciendo la posición de liquidez" if delta_num > 0 else "restando colchón de activos"
        drivers_list.append(f"Variación en {num_nombre} de **{sign_num}${delta_num:,.0f} MM** ({impacto_num}).")
    if delta_den != 0:
        sign_den = "+" if delta_den > 0 else ""
        impacto_den = "presionando a la baja el ratio" if delta_den > 0 else "aliviando la exigencia requerida"
        drivers_list.append(f"Variación en {den_nombre} de **{sign_den}${delta_den:,.0f} MM** ({impacto_den}).")

    # 3. Top drivers desde la descomposición detallada
    detalle_bullets = []
    if len(df_detalle) > 0 and 'delta_ponderado' in df_detalle.columns:
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

    # 4. Recomendaciones para Mesa de Dinero / ALM
    if ratio_act < limite_alerta:
        rec_alm = (
            "1. Frenar crecimiento en activos ilíquidos que consuman requerimientos a corto plazo.\n"
            "2. Incentivar captación a plazo en tramos superiores a 30 días con clientes institucionales.\n"
            "3. Evaluar monetización transitoria de cartera HQLA Nivel 2 o repos en Banco Central."
        )
    else:
        rec_alm = (
            "1. Mantener la estructura de calce actual monitoreando renovaciones mayoristas relevantes.\n"
            "2. Optimizar el costo de fondeo aprovechando el exceso de holgura sobre el apetito interno.\n"
            "3. Verificar cupos en líneas de crédito contingentes corporativas."
        )

    # Ensamble del Markdown
    reporte = f"""
#### Diagnóstico de Posición y Cumplimiento
{diagnostico}

#### Atribución de Factores Clave
{explicacion_driver}
- {'<br>- '.join(drivers_list) if drivers_list else 'Variaciones netas menores.'}

{('#### Cuentas con Mayor Variación en el Período<br>' + '<br>'.join(detalle_bullets)) if detalle_bullets else ''}

#### Acciones Recomendadas para el Comité ALCO
{rec_alm}
"""
    return reporte


def call_llm_copilot_analysis(
    kpis: Dict[str, Any],
    df_detalle: pd.DataFrame,
    ratio_nombre: str,
    num_nombre: str,
    den_nombre: str,
    granularidad: str = "Diaria",
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    model_name: str = "gpt-4o",
    *args,
    **kwargs
) -> str:
    """
    Invoca un modelo LLM (OpenAI / Azure Copilot / GitHub Models) para redactar
    un memorándum ejecutivo completo para la Alta Dirección.
    """
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        return (
            "> ⚠️ **API Key no configurada**: Para habilitar la redacción automática con IA "
            "(OpenAI / Copilot), ingresa tu API Key en la barra lateral izquierda."
        )

    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=key,
            base_url=api_base if api_base and len(api_base.strip()) > 0 else None
        )

        top_cuentas_str = ""
        if len(df_detalle) > 0 and 'delta_ponderado' in df_detalle.columns:
            top_movs = df_detalle.sort_values(by=abs(df_detalle['delta_ponderado']), ascending=False).head(5)
            lineas = [f"- {r['subcategoria']} ({r['componente']}): Δ ${r['delta_ponderado']:+,.0f} MM (Total: ${r['monto_ponderado']:,.0f} MM)"
                      for _, r in top_movs.iterrows()]
            top_cuentas_str = "\n".join(lineas)

        prompt_system = (
            "Eres el Chief Financial Officer (CFO) y Head of Financial Risk de un banco internacional supervisado bajo Basilea III y CMF. "
            "Redacta un Memorándum Ejecutivo formal y conciso para la Alta Dirección y el Comité ALCO, "
            "explicando los movimientos del ratio de liquidez, analizando los principales drivers y entregando acciones accionables."
        )

        prompt_user = f"""
DATOS DEL PERÍODO ({granularidad.upper()}):
- Indicador: {ratio_nombre}
- Nivel Actual: {kpis['ratio_actual']:.2f}% (Período anterior: {kpis['ratio_anterior']:.2f}%, Variación: {kpis['delta_ratio']:+.2f} pp)
- {num_nombre} (Numerador): ${kpis['num_actual']:,.0f} MM (Variación: ${kpis['delta_num']:+,.0f} MM)
- {den_nombre} (Denominador): ${kpis['den_actual']:,.0f} MM (Variación: ${kpis['delta_den']:+,.0f} MM)
- Mínimo Normativo CMF: {kpis.get('limite_normativo', 100.0):.0f}%
- Alerta Interna: {kpis.get('limite_interno', 120.0 if ratio_nombre=='LCR' else 105.0):.0f}%
- Apetito por Riesgo: {kpis.get('apetito_riesgo', 150.0 if ratio_nombre=='LCR' else 110.0):.0f}%
- Holgura Regulatoria: {kpis['holgura_pp']:+.2f} pp (${kpis['holgura_monto']:+,.0f} MM)
- Semáforo Actual: {kpis['semaforo']}

PRINCIPALES MOVIMIENTOS EN CUENTAS:
{top_cuentas_str or "Sin movimientos relevantes desglosados."}

INSTRUCCIONES DE FORMATO:
1. Resumen Ejecutivo (en 2 líneas clave).
2. Diagnóstico de Cumplimiento y Holguras frente a umbrales (100%, alerta y apetito).
3. Atribución de Drivers (Numerador vs Denominador y cuentas principales).
4. Acciones y Estrategia de Liquidez Recomendada para la Mesa de Dinero.
Redacta en español profesional, conciso y técnico.
"""

        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": prompt_system},
                {"role": "user", "content": prompt_user}
            ],
            temperature=0.2,
            max_tokens=900
        )
        return response.choices[0].message.content

    except Exception as e:
        return f"> ❌ **Error al consultar Copilot / OpenAI API**: {str(e)}"
