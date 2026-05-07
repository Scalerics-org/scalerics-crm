import logging
import random
import re

from database import get_all_businesses, update_business

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

TEMPLATES = {
    "gym": [
        "Buenas, ¿cómo andás? Soy de Scalerics, una software factory uruguaya. Vimos que {name} no tiene página web propia — con {rating} estrellas en Google, una web te ayudaría a convertir más búsquedas en socios nuevos. ¿Te interesaría que charlemos unos minutos?",
        "Buenas. Te escribo desde Scalerics, desarrollamos sitios web para negocios en Uruguay. Notamos que {name} no tiene web propia, y con el buen rating que tienen podrían estar atrayendo muchos más clientes por internet. ¿Tienen unos minutos para charlar?",
    ],
    "peluqueria": [
        "Buenas, ¿cómo andás? Soy de Scalerics, una software factory uruguaya. Vimos que {name} no tiene página web — hoy en día los clientes buscan peluquerías en Google antes de llamar. Una web propia puede hacer la diferencia. ¿Les interesaría charlar?",
        "Buenas. Te contacto desde Scalerics, desarrollamos webs para negocios locales en Uruguay. Notamos que {name} todavía no tiene sitio web propio. Con una web podrían aparecer en Google cuando alguien busca peluquerías en {city}. ¿Charlamos unos minutos?",
    ],
    "restaurante": [
        "Buenas, ¿cómo andás? Soy de Scalerics, software factory uruguaya. Vimos que {name} no tiene página web propia — con {rating} estrellas en Google, una web con el menú y reservas online les traería muchos más clientes. ¿Les interesaría charlar?",
        "Buenas. Te escribo desde Scalerics, desarrollamos sitios web para restaurantes en Uruguay. Notamos que {name} no tiene web propia, y con las buenas reseñas que tienen, una presencia online les ayudaría a llenar más mesas. ¿Tienen unos minutos?",
    ],
    "bar": [
        "Buenas, ¿cómo andás? Soy de Scalerics, una software factory uruguaya. Vimos que {name} no tiene página web — hoy la gente busca bares en Google antes de salir. Con una web aparecen en esas búsquedas y traen más gente. ¿Charlamos unos minutos?",
        "Buenas. Te contacto desde Scalerics, desarrollamos webs para bares y locales en Uruguay. {name} no tiene sitio web propio, y con {rating} estrellas en Google podrían estar atrayendo mucho más público. ¿Les interesa charlar?",
    ],
    "panaderia": [
        "Buenas, ¿cómo andás? Soy de Scalerics, software factory uruguaya. Vimos que {name} no tiene página web propia — una web sencilla con horarios, productos y ubicación hace que mucha más gente los encuentre en Google. ¿Les interesaría charlar?",
        "Buenas. Te escribo desde Scalerics, desarrollamos sitios web para negocios locales. Notamos que {name} no tiene presencia web propia. Con una página básica aparecen en Google cuando alguien busca panaderías en {city}. ¿Charlamos unos minutos?",
    ],
    "delivery": [
        "Buenas, ¿cómo andás? Soy de Scalerics, software factory uruguaya. Vimos que {name} no tiene página web — tener un sitio propio con el menú y pedidos online reduce la dependencia de las apps de delivery y sus comisiones. ¿Les interesaría charlar?",
        "Buenas. Te contacto desde Scalerics, desarrollamos webs para negocios gastronómicos en Uruguay. {name} no tiene web propia, y con una página podrían recibir pedidos directos sin pagar comisiones a terceros. ¿Charlamos?",
    ],
    "salud": [
        "Buenas, ¿cómo andás? Soy de Scalerics, software factory uruguaya. Vimos que {name} no tiene página web propia — los pacientes buscan profesionales de salud en Google antes de llamar. Una web con servicios y contacto hace la diferencia. ¿Charlamos?",
        "Buenas. Te escribo desde Scalerics, desarrollamos sitios web para profesionales de la salud en Uruguay. Notamos que {name} no tiene presencia web y eso puede significar pacientes que no los encuentran. ¿Les interesaría charlar unos minutos?",
    ],
    "hotel": [
        "Buenas, ¿cómo andás? Soy de Scalerics, software factory uruguaya. Vimos que {name} no tiene página web propia — los viajeros reservan en Google y sin web propia dependen de Booking y sus comisiones. ¿Les interesaría charlar?",
        "Buenas. Te contacto desde Scalerics, desarrollamos webs para hoteles y alojamientos en Uruguay. {name} no tiene sitio propio, y con una web pueden recibir reservas directas sin intermediarios. ¿Charlamos unos minutos?",
    ],
    "ferreteria": [
        "Buenas, ¿cómo andás? Soy de Scalerics, software factory uruguaya. Vimos que {name} no tiene página web — hoy los clientes buscan ferreterías en Google antes de ir. Una web con catálogo básico y horarios les trae más gente. ¿Charlamos?",
        "Buenas. Te escribo desde Scalerics, desarrollamos webs para comercios locales en Uruguay. Notamos que {name} no tiene presencia web. Con una página básica aparecen en Google cuando alguien busca en {city}. ¿Les interesa charlar?",
    ],
    "veterinaria": [
        "Buenas, ¿cómo andás? Soy de Scalerics, software factory uruguaya. Vimos que {name} no tiene página web propia — los dueños de mascotas buscan veterinarias en Google. Una web con servicios y contacto les trae más consultas. ¿Charlamos?",
        "Buenas. Te contacto desde Scalerics, desarrollamos webs para veterinarias en Uruguay. {name} no tiene sitio web propio y eso puede hacerles perder clientes que buscan en Google. ¿Les interesaría charlar unos minutos?",
    ],
    "default": [
        "Buenas, ¿cómo andás? Soy de Scalerics, software factory uruguaya. Notamos que {name} no tiene página web propia — hoy la mayoría de los clientes busca en Google antes de visitar un negocio, y sin web no aparecen. ¿Les interesaría charlar unos minutos?",
        "Buenas. Te escribo desde Scalerics, desarrollamos sitios web para negocios locales en Uruguay. Notamos que {name} todavía no tiene presencia web. Con una web sencilla empiezan a aparecer en Google cuando alguien busca en {city}. ¿Charlamos?",
        "Buenas, ¿cómo andás? Soy de Scalerics, una software factory uruguaya. Vimos que {name} no tiene página web propia. Hoy sin web un negocio pierde visibilidad en Google. Lo podemos solucionar rápido y a buen precio. ¿Les interesa charlar?",
    ],
}

def _get_template_group(category: str) -> str:
    cat = (category or "").lower()
    for keywords, group in CATEGORY_MAP:
        if any(kw in cat for kw in keywords):
            return group
    return "default"

def generate_pitch(business: dict) -> str:
    group = _get_template_group(business.get("category", ""))
    templates = TEMPLATES[group]
    template = random.choice(templates)

    rating = business.get("rating")
    rating_str = str(rating) if rating else "muy bueno"
    city = business.get("city", "tu ciudad")
    # Strip department prefix if present (e.g. "Departamento de Montevideo" → "Montevideo")
    city = re.sub(r"(?i)departamento\s+de\s+", "", city).strip()

    return template.format(
        name=business.get("name", ""),
        city=city,
        rating=rating_str,
        review_count=business.get("review_count", ""),
    )

def run(db_path: str) -> None:
    all_businesses = get_all_businesses(db_path)
    businesses = [b for b in all_businesses if not b.get("pitch_text")]
    logger.info(f"Generando pitches para {len(businesses)} negocios")

    for biz in businesses:
        try:
            pitch = generate_pitch(biz)
            update_business(db_path, biz["id"], pitch_text=pitch)
            logger.info(f"Pitch listo: {biz['name']}")
        except Exception as e:
            logger.error(f"Error generando pitch para {biz['name']}: {e}")
