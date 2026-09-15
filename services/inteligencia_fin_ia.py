"""El resumen en castellano de Inteligencia financiera, con IA opcional.

Usa la integración que el CRM ya tiene para la radiografía de marketing
(services/radiografia_ia.py): el mismo modelo, el mismo cliente `anthropic` y la
misma bandera `RADIOGRAFIA_IA_ACTIVA`, que nace apagada. Con la bandera apagada,
sin `ANTHROPIC_API_KEY`, si la API falla o si el texto trae un número que no
está en lo calculado, se usa el texto determinístico. Nunca levanta.

El modelo solo recibe el diagnóstico y las sugerencias ya calculados (títulos,
textos y montos), nunca filas de la base ni datos de personas. Cada número del
texto tiene que existir en ese JSON: lo verifica `numeros_inventados`.

Se llama desde el recálculo diario y el resultado se guarda en `if_calculos`:
no se pide en cada carga de la pantalla.
"""

import json
import logging
import os

from services.inteligencia_fin import usd
from services.radiografia_ia import MODELO, _NUMERO, _a_float, ia_activa

logger = logging.getLogger(__name__)

_INSTRUCCIONES = """Sos el analista financiero de una software factory chica de Uruguay.
Te paso en JSON el diagnóstico del mes y las sugerencias para ganar plata, ya
calculados con los datos del sistema.

Escribí UN párrafo corto (entre 4 y 6 oraciones), en castellano rioplatense
simple, para el dueño: cómo está la empresa y qué conviene hacer primero.

Reglas que no se negocian:
1. No inventes ningún número. Todo número que escribas tiene que estar tal cual
   en el JSON. No sumes, no restes, no redondees y no estimes.
2. Los montos van en dólares, escritos igual que en el JSON (por ejemplo USD 1.234).
3. Sin títulos, sin viñetas y sin markdown: solo el párrafo."""


def preparar(diagnostico: list[dict], sugerencias: list[dict]) -> dict:
    """Lo único que ve el modelo. Campo por campo, a propósito."""
    return {
        "diagnostico": [{"titulo": d["titulo"], "valor": d["valor"], "texto": d["texto"],
                         "nivel": d["nivel"]} for d in diagnostico],
        "sugerencias": [{"titulo": r["titulo"], "detalle": r["detalle"], "confianza": r["confianza"],
                         "impacto": (usd(r["impacto_mensual"]) if r.get("impacto_mensual") is not None
                                     else "revisar"),
                         "periodicidad": "por única vez" if r.get("unica_vez") else "por mes"}
                        for r in sugerencias],
    }


def _valores(texto: str) -> set:
    valores = set()
    for crudo in _NUMERO.findall(texto or ""):
        n = _a_float(crudo)
        if n is not None:
            valores.add(round(n, 2))
    return valores


def numeros_inventados(texto: str, payload: dict) -> list[str]:
    """Los números del texto que no aparecen en el JSON que se le pasó."""
    legitimos = _valores(json.dumps(payload, ensure_ascii=False))
    sueltos = []
    for crudo in _NUMERO.findall(texto or ""):
        n = _a_float(crudo)
        if n is None:
            continue
        if 1900 <= n <= 2100 and float(n).is_integer():
            continue
        if any(abs(n - ok) <= 0.01 for ok in legitimos):
            continue
        sueltos.append(crudo)
    return sueltos


def texto_deterministico(diagnostico: list[dict], sugerencias: list[dict]) -> str:
    if not diagnostico and not sugerencias:
        return "Todavía no hay movimientos en el sistema para sacar conclusiones."
    alertas = [d for d in diagnostico if d["nivel"] == "mal"]
    partes = [d["texto"] for d in (alertas or diagnostico)[:3]]
    con_plata = [r for r in sugerencias if r.get("impacto_mensual") is not None]
    if con_plata:
        r = con_plata[0]
        titulo = r["titulo"][:1].lower() + r["titulo"][1:]
        partes.append(f"Lo que más plata mueve ahora: {titulo} "
                      f"({usd(r['impacto_mensual'])} {'por única vez' if r.get('unica_vez') else 'por mes'}).")
    return " ".join(partes)


def _llamar_a_la_api(payload: dict) -> str:
    """El párrafo del modelo. El import va acá adentro, igual que la radiografía:
    `anthropic` pesa y la máquina es chica."""
    import anthropic

    cliente = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    r = cliente.messages.create(
        model=MODELO,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=_INSTRUCCIONES,
        messages=[{"role": "user", "content": "Estos son los números:\n\n"
                   + json.dumps(payload, ensure_ascii=False, indent=1)}],
        # Si el modelo declina, la API reintenta sola con otro modelo. Va por
        # extra_headers/extra_body para no depender de la versión del SDK.
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
        extra_body={"fallbacks": "default"},
    )
    if r.stop_reason == "refusal":
        raise RuntimeError("el modelo no redactó el resumen")
    return "".join(b.text for b in r.content if b.type == "text").strip()


def redactar(diagnostico: list[dict], sugerencias: list[dict], llamar=None) -> tuple[str, str]:
    """(texto, origen), con origen 'ia' o 'deterministico'.

    `llamar` es para los tests: recibe el JSON preparado y devuelve el texto.
    Sin `llamar`, solo se usa la API si la bandera está prendida y hay clave.
    """
    base = texto_deterministico(diagnostico, sugerencias)
    if not diagnostico and not sugerencias:
        return base, "deterministico"
    if llamar is None:
        if not ia_activa() or not os.environ.get("ANTHROPIC_API_KEY"):
            return base, "deterministico"
        llamar = _llamar_a_la_api
    payload = preparar(diagnostico, sugerencias)
    try:
        texto = (llamar(payload) or "").strip()
    except Exception as e:
        logger.warning("inteligencia financiera: el resumen con IA falló (%s)", e)
        return base, "deterministico"
    inventados = numeros_inventados(texto, payload)
    if not texto or inventados:
        logger.warning("inteligencia financiera: resumen descartado, números que no existen: %s", inventados)
        return base, "deterministico"
    return texto, "ia"
