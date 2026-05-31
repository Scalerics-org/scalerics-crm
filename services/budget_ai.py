"""AI service for budget HTML editing and generation using Claude Haiku."""

import os
import anthropic

MODEL = "claude-haiku-4-5-20251001"

_EDIT_SYSTEM = """Sos un asistente que edita documentos HTML de presupuestos profesionales.
Tu tarea es aplicar los cambios solicitados preservando exactamente la estructura,
los estilos CSS y el formato visual del documento original.
Devolvé ÚNICAMENTE el HTML completo modificado, sin explicaciones ni bloques markdown."""

_GEN_SYSTEM = """Sos un asistente que genera presupuestos HTML profesionales para
Scalerics, una agencia de desarrollo web en Uruguay.
Usá esta paleta de colores: navy #0f1f3d, accent #0088CC, green #0d9e6e, texto #1c2b40.
Incluí: encabezado con logo de Scalerics, datos del cliente, descripción del proyecto,
lista de lo que incluye el desarrollo, precio de desarrollo, precio mensual de mantenimiento,
notas y pie de página.
Devolvé ÚNICAMENTE el HTML completo listo para abrir en el navegador, sin explicaciones ni markdown."""


def ai_edit_html(original_html: str, instructions: str) -> str:
    """Apply AI instructions to an existing HTML budget. Returns modified HTML."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model=MODEL,
        max_tokens=8096,
        system=_EDIT_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"HTML original:\n\n{original_html}\n\nInstrucciones: {instructions}",
            }
        ],
    )
    return message.content[0].text


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
    return message.content[0].text
