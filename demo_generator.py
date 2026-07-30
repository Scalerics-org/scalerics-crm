import json
import logging
import re
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader

from database import get_businesses_by_status, update_business

load_dotenv()
logger = logging.getLogger(__name__)

ICONS_BY_SCHEME = {
    "warm":   ["🍕", "🥗", "☕", "🍰", "🍷"],
    "cool":   ["🔨", "🏗️", "🪚", "⚙️", "🔩"],
    "green":  ["💊", "🩺", "🌿", "❤️", "🐾"],
    "purple": ["✂️", "💅", "👗", "💄", "🌸"],
    "dark":   ["💡", "📱", "🖥️", "⚡", "🔧"],
}

DEFAULT_ICONS = ["✅", "⭐", "🚀"]

TEMPLATE_MAP = {
    "editorial": "template_editorial.html",
    "retro":     "template_retro.html",
    "boutique":  "template_boutique.html",
    "modern":    "template_modern.html",
}

SYSTEM_PROMPT = """Sos un copywriter experto en diseño web para pymes de Uruguay.
Respondé ÚNICAMENTE con un JSON válido, sin texto adicional, sin markdown, sin bloques de código."""

def build_prompt(business: dict) -> str:
    return f"""Creá el contenido para la página web de este negocio uruguayo:

Nombre: {business.get('name', '')}
Categoría: {business.get('category', '')}
Ciudad: {business.get('city', '')}
Rating: {business.get('rating', '')} ({business.get('review_count', '')} reseñas)
Horarios: {business.get('hours', '')}

Respondé con este JSON exacto:
{{
  "tagline": "slogan atractivo en máximo 10 palabras",
  "about": "descripción del negocio en 2-3 oraciones, tono cálido y profesional",
  "services": ["servicio 1", "servicio 2", "servicio 3"],
  "cta_text": "texto del botón de contacto (máx 4 palabras)",
  "color_scheme": "uno de: warm, cool, dark, green, purple según el rubro",
  "template": "uno de: editorial, retro, boutique, modern — editorial para bares/gyms/barberías/tattoo/peluquerías, retro para panaderías/almacenes/rotiserías/cafeterías, boutique para restaurantes/clínicas/spas/estudios/hoteles, modern para todo lo demás"
}}"""

def parse_claude_response(raw: str) -> dict:
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    candidate = match.group() if match else raw
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise ValueError(f"Claude response is not valid JSON: {e}\nRaw: {raw[:200]}") from e

def render_html(content: dict, business: dict) -> str:
    scheme = content.get("color_scheme", "dark")
    template_key = content.get("template", "modern")
    template_file = TEMPLATE_MAP.get(template_key, "template_modern.html")
    icons = ICONS_BY_SCHEME.get(scheme, DEFAULT_ICONS)
    services_with_icons = [
        {"icon": icons[i % len(icons)], "name": svc}
        for i, svc in enumerate(content.get("services", []))
    ]
    phone = business.get("phone", "") or ""
    wa_number = re.sub(r"[^0-9]", "", phone)

    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"))
    template = env.get_template(template_file)
    return template.render(
        business_name=business.get("name", ""),
        category=business.get("category", ""),
        tagline=content.get("tagline", ""),
        about=content.get("about", ""),
        services=services_with_icons,
        cta_text=content.get("cta_text", "Contactanos"),
        phone=phone,
        wa_number=wa_number,
        address=business.get("address", ""),
        city=business.get("city", ""),
        rating=business.get("rating"),
        review_count=business.get("review_count"),
        hours=business.get("hours", "") or "",
        color_scheme=scheme,
    )

def generate_content(business: dict, api_key: str) -> dict:
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_prompt(business)}],
    )
    raw = message.content[0].text
    return parse_claude_response(raw)

def run(db_path: str, api_key: str) -> None:
    businesses = (
        get_businesses_by_status(db_path, "email_found") +
        get_businesses_by_status(db_path, "no_email")
    )
    logger.info(f"Generando demos para {len(businesses)} negocios")
    output_dir = Path("generated_demos")
    output_dir.mkdir(exist_ok=True)

    for biz in businesses:
        try:
            logger.info(f"Generando demo para: {biz['name']}")
            content = generate_content(biz, api_key)
            html = render_html(content, biz)

            slug = re.sub(r'[^a-z0-9]+', '-', biz["name"].lower()).strip('-')
            filename = f"{int(biz['id'])}-{slug}.html"
            filepath = output_dir / filename
            filepath.write_text(html, encoding="utf-8")

            update_business(
                db_path, biz["id"],
                color_scheme=content.get("color_scheme"),
                demo_html_path=str(filepath),
                status="demo_generated",
            )
            time.sleep(1)
        except Exception as e:
            logger.error(f"Error generando demo para {biz['name']}: {e}")
            update_business(db_path, biz["id"], status="error", error_message=str(e))
