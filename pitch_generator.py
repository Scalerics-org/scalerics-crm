import logging
import re

from database import get_all_businesses, get_pitch_templates, increment_template_usage, update_business

logger = logging.getLogger(__name__)

# Map category keywords → template group
CATEGORY_MAP = [
    (["gimnasio", "gym", "fitness", "crossfit", "pilates", "yoga"],       "gym"),
    (["peluquer", "barber", "coiff", "estilista", "cabello"],              "peluqueria"),
    (["restaurante", "parrilla", "rotisería", "rotiseria", "comida"],      "restaurante"),
    (["bar", "pub", "cervecería", "cerveceria", "cantina"],                "bar"),
    (["panadería", "panaderia", "confitería", "confiteria", "pastelería"], "panaderia"),
    (["pizzería", "pizzeria", "hamburgues", "sándwich", "sandwiche"],      "delivery"),
    (["clínica", "clinica", "médico", "medico", "dentista", "óptica"],    "salud"),
    (["hotel", "hostel", "apart", "alojamiento"],                          "hotel"),
    (["ferretería", "ferreteria", "materiales", "construcción"],           "ferreteria"),
    (["veterinaria", "veterinario", "mascotas"],                           "veterinaria"),
]


def _get_template_group(category: str) -> str:
    cat = (category or "").lower()
    for keywords, group in CATEGORY_MAP:
        if any(kw in cat for kw in keywords):
            return group
    return "default"


def generate_pitch(business: dict, db_path: str) -> str:
    group = _get_template_group(business.get("category", ""))
    templates = get_pitch_templates(db_path, category_group=group)
    if not templates:
        templates = get_pitch_templates(db_path, category_group="default")
    if not templates:
        raise RuntimeError("No pitch templates found in DB — run seed_pitch_templates first")

    # Use the least-used template for rotation
    tmpl = min(templates, key=lambda t: t["usage_count"])
    increment_template_usage(db_path, tmpl["id"])

    rating = business.get("rating")
    rating_str = str(rating) if rating else "muy bueno"
    city = business.get("city", "tu ciudad")
    city = re.sub(r"(?i)departamento\s+de\s+", "", city).strip()

    return tmpl["content"].format(
        name=business.get("name", ""),
        city=city,
        rating=rating_str,
        review_count=business.get("review_count", ""),
    )


def run(db_path: str) -> None:
    all_businesses = get_all_businesses(db_path)
    # La cohorte de discovery queda afuera: todas las plantillas semilla dicen
    # literalmente "sin sitio web propio", y estos comercios lo tienen. Ademas
    # aparecen mezclados con el padron de WhatsApp en el listado por defecto.
    businesses = [b for b in all_businesses if not b.get("pitch_text")
                  and (b.get("source") or "").strip().lower() != "discovery"]
    logger.info(f"Generando pitches para {len(businesses)} negocios")

    for biz in businesses:
        try:
            pitch = generate_pitch(biz, db_path)
            update_business(db_path, biz["id"], pitch_text=pitch)
            logger.info(f"Pitch listo: {biz['name']}")
        except Exception as e:
            logger.error(f"Error generando pitch para {biz['name']}: {e}")
