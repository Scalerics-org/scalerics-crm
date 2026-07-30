"""AI service for budget HTML editing and generation using Claude Haiku."""

import os
import json
import anthropic

MODEL = "claude-haiku-4-5-20251001"
MODEL_GEN = "claude-sonnet-4-6"

_EDIT_SYSTEM = """Sos un asistente que edita documentos HTML de presupuestos profesionales.
En lugar de devolver el HTML completo, devolvés ÚNICAMENTE un array JSON con los cambios a aplicar.
Cada cambio es un objeto con dos campos:
  - "old": el fragmento de texto exacto a reemplazar (debe ser único en el documento)
  - "new": el texto que lo reemplaza

Reglas:
- Devolvé SOLO el array JSON, sin explicaciones ni bloques markdown.
- Cada "old" debe ser lo más corto posible pero suficientemente único para identificar el lugar exacto.
- No toques nada que no sea necesario para cumplir las instrucciones.
- Si una instrucción no se puede aplicar de forma segura, omitila del array.

Ejemplo de respuesta:
[{"old": "U$S 1.000", "new": "U$S 1.200"}, {"old": "plazo de 3 semanas", "new": "plazo de 4 semanas"}]"""

_GEN_SYSTEM = """Sos un asistente que genera presupuestos HTML profesionales para
Scalerics, una agencia de desarrollo web en Uruguay.
Usá esta paleta de colores: navy #0f1f3d, accent #0088CC, green #0d9e6e, texto #1c2b40.
Incluí: encabezado con logo de Scalerics, datos del cliente, descripción del proyecto,
lista de lo que incluye el desarrollo, precio de desarrollo, precio mensual de mantenimiento,
notas y pie de página.
Devolvé ÚNICAMENTE el HTML completo listo para abrir en el navegador, sin explicaciones ni markdown."""


def _strip_markdown(text: str) -> str:
    """Remove markdown code fences if the model wrapped its response."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    return text.strip()


def ai_edit_html(original_html: str, instructions: str) -> str:
    """Apply AI instructions to an existing HTML budget using a diff-based approach.

    The model returns a JSON array of {old, new} pairs instead of the full HTML,
    avoiding token-limit truncation on large documents.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=_EDIT_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"HTML original:\n\n{original_html}\n\nInstrucciones: {instructions}",
            }
        ],
    )
    raw = _strip_markdown(message.content[0].text)
    try:
        changes = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError(f"La IA devolvió una respuesta no válida: {raw[:200]}")

    result = original_html
    for change in changes:
        old = change.get("old", "")
        new = change.get("new", "")
        if old and old in result:
            result = result.replace(old, new, 1)
    return result


def generate_budget_html(business_name: str, category: str, city: str, instructions: str = "") -> str:
    """Generate a full HTML budget from scratch for the given business."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = f"Generá un presupuesto HTML para:\n- Negocio: {business_name}\n- Rubro: {category}\n- Ciudad: {city}"
    if instructions:
        prompt += f"\n- Instrucciones adicionales: {instructions}"
    message = client.messages.create(
        model=MODEL,
        max_tokens=8096,
        system=_GEN_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return _strip_markdown(message.content[0].text)
