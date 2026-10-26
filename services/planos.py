"""Planificador de sitios: del brief al mapa del sitio con secciones y textos.

Antes de abrir Elementor hay que saber qué páginas lleva el sitio, qué
secciones tiene cada una y qué dice cada sección. Hoy eso se arma de cero en
cada proyecto, en la cabeza de quien diseña, y el cliente ve los textos recién
cuando el sitio está hecho: ahí aparecen los "esto no es lo que hacemos" que
obligan a rehacer.

Este módulo hace lo que Relume hace para las agencias: con el brief (rubro,
servicios, zona, objetivo) propone el mapa del sitio, las secciones de cada
página con un borrador de texto y los títulos SEO. El equipo lo edita, y el
cliente lo aprueba página por página desde un link (`/plano/<token>`) antes
de que se diseñe nada.

Nada de IA ni de red: las plantillas son por familia de rubro (lo que se
repite en una clínica, un restaurante o un estudio contable) y los textos se
arman con los datos del brief. Así anda igual en la vista previa, no cuesta
nada y el resultado es siempre el mismo para el mismo brief. La IA puede venir
después a pulir el texto; la estructura es lo que ahorra las horas.

El mapa se guarda como JSON en `pl_planos.mapa`: una lista de páginas, cada
una con su SEO y sus secciones. `validar_mapa` es la única puerta de entrada:
todo lo que viene de la pantalla pasa por ahí antes de guardarse.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import unicodedata
from datetime import datetime, timezone

FORMATO = "%Y-%m-%d %H:%M:%S"


class NoSePuede(Exception):
    pass


def _ahora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _txt(d: datetime) -> str:
    return d.strftime(FORMATO)


def _connect(db: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# ── topes ────────────────────────────────────────────────────────────────────
# El mapa entero viaja en cada guardado y se pinta en una página pública: los
# topes son para que nadie (ni un error de la pantalla) lo infle sin límite.

MAX_PAGINAS = 30
MAX_SECCIONES = 16
MAX_ITEMS = 12
MAX_SERVICIOS = 12
LARGO = {"nombre": 60, "slug": 80, "titulo": 140, "texto": 1200, "boton": 40, "item_titulo": 90,
         "item_texto": 400, "seo_titulo": 90, "seo_descripcion": 300, "h1": 140, "nota": 600}
MAX_COMENTARIO = 1500
MAX_COMENTARIOS = 300   # por plano: la página es pública, esto frena a un robot

# Lo que Google muestra antes de cortar. No es un tope duro: es un aviso.
SEO_TITULO_IDEAL = 60
SEO_DESCRIPCION_IDEAL = 155

ESTADOS = {
    "borrador": "Borrador",
    "enviado": "Esperando al cliente",
    "cambios": "El cliente pidió cambios",
    "aprobado": "Aprobado por el cliente",
}

OBJETIVOS = {
    "whatsapp": "Que escriban por WhatsApp",
    "reservas": "Que reserven un turno",
    "compras": "Que compren online",
    "presupuesto": "Que pidan presupuesto",
    "visitas": "Que vengan al local",
}

TIPOS = {"web": "Página web", "ecommerce": "Tienda online"}

TONOS = {
    "cercano": "Cercano (de vos)",
    "profesional": "Profesional (de usted)",
}


# ── familias de rubro ────────────────────────────────────────────────────────
# Qué páginas lleva el sitio y cómo se le dice a cada cosa. Agrupado por cómo
# vende el negocio, no por industria: a una odontología y a una peluquería les
# sirve el mismo sitio (turnos), a un estudio contable y a una inmobiliaria
# también (confianza y consulta).

FAMILIAS = {
    "turnos": {
        "nombre": "Salud, belleza y turnos",
        "ejemplos": "clínica, odontología, veterinaria, peluquería, estética, fisioterapia",
        "servicios": "Tratamientos", "servicio": "tratamiento", "cliente": "pacientes",
        "objetivo": "reservas", "pagina_por_servicio": True,
        "paginas": ["inicio", "servicios", "equipo", "turnos", "contacto"],
    },
    "gastronomia": {
        "nombre": "Gastronomía",
        "ejemplos": "restaurante, parrilla, pizzería, cafetería, rotisería",
        "servicios": "La carta", "servicio": "plato", "cliente": "clientes",
        "objetivo": "whatsapp", "pagina_por_servicio": False,
        "paginas": ["inicio", "carta", "reservas", "nosotros", "contacto"],
    },
    "comercio": {
        "nombre": "Comercio y tiendas",
        "ejemplos": "ferretería, óptica, tienda de ropa, librería, casa de repuestos",
        "servicios": "Productos", "servicio": "producto", "cliente": "clientes",
        "objetivo": "visitas", "pagina_por_servicio": False,
        "paginas": ["inicio", "productos", "nosotros", "contacto"],
    },
    "profesional": {
        "nombre": "Servicios profesionales",
        "ejemplos": "estudio contable, escribanía, abogados, inmobiliaria, consultora",
        "servicios": "Servicios", "servicio": "servicio", "cliente": "clientes",
        "objetivo": "whatsapp", "pagina_por_servicio": True,
        "paginas": ["inicio", "servicios", "nosotros", "faq", "contacto"],
    },
    "oficios": {
        "nombre": "Oficios y obras",
        "ejemplos": "construcción, electricista, sanitaria, aire acondicionado, mudanzas",
        "servicios": "Servicios", "servicio": "servicio", "cliente": "clientes",
        "objetivo": "presupuesto", "pagina_por_servicio": True,
        "paginas": ["inicio", "servicios", "trabajos", "zonas", "presupuesto"],
    },
    "educacion": {
        "nombre": "Educación y deporte",
        "ejemplos": "academia, instituto de idiomas, gimnasio, escuela de música",
        "servicios": "Cursos", "servicio": "curso", "cliente": "alumnos",
        "objetivo": "whatsapp", "pagina_por_servicio": True,
        "paginas": ["inicio", "servicios", "horarios", "nosotros", "contacto"],
    },
}

# Palabras del rubro que escribe el equipo -> familia. Si no matchea nada, el
# equipo la elige a mano en el formulario.
_PISTAS = {
    "turnos": ("clinic", "odonto", "dent", "veterin", "pelu", "barber", "estetic", "spa", "fisio", "psico",
               "medic", "nutri", "podolog", "kinesi", "manicur", "unas", "tatu", "salud", "laborator"),
    "gastronomia": ("restaur", "parrill", "pizz", "cafe", "rotiser", "bar", "heladeri", "panader", "confiter",
                    "sushi", "hamburgues", "cervecer", "catering", "comida", "bodegon"),
    "comercio": ("ferret", "optic", "ropa", "tienda", "librer", "repuesto", "jugueter", "boutique", "calzado",
                 "muebler", "bazar", "regaler", "kiosco", "almacen", "supermerc", "electrodom", "farmacia",
                 "perfumer", "vivero", "pinturer", "bicicleter", "celular", "informatica"),
    "profesional": ("contab", "contador", "escriban", "abogad", "jurid", "inmobil", "consult", "estudio",
                    "seguro", "arquitect", "despachant", "gestor", "rematador", "traductor"),
    "oficios": ("constru", "electric", "sanitar", "plomer", "aire acond", "climatiz", "mudanz", "pintor",
                "herrer", "carpinter", "vidrier", "cerrajer", "fumig", "limpieza", "jardin", "piscin",
                "techos", "albanil", "taller", "mecanic", "gomer", "chapa", "automotora"),
    "educacion": ("academ", "instituto", "idioma", "gimnas", "escuela", "colegio", "musica", "danza",
                  "yoga", "pilates", "crossfit", "clases", "jardin de infantes", "guarderia", "curso"),
}


def _plano(texto: str) -> str:
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return t.lower().strip()


def adivinar_familia(rubro: str) -> str | None:
    r = _plano(rubro)
    if not r:
        return None
    for fam, pistas in _PISTAS.items():
        if any(p in r for p in pistas):
            return fam
    return None


def slug(texto: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", _plano(texto)).strip("-")
    return s[:LARGO["slug"]].strip("-")


# ── bloques ──────────────────────────────────────────────────────────────────
# La biblioteca de secciones. `horas` es lo que lleva armarla en Elementor con
# el texto ya escrito, sin contar fotos: sirve para estimar el armado. `guia`
# es lo que ve quien diseña, como el wireframe de Relume.

BLOQUES = {
    "hero":        {"nombre": "Portada", "horas": 1.5, "guia": "Título grande, bajada y dos botones. Foto real del local o del equipo de fondo."},
    "confianza":   {"nombre": "Barra de confianza", "horas": 0.5, "guia": "Tres o cuatro datos cortos en fila: años, clientes, garantía, zona."},
    "servicios":   {"nombre": "Grilla de servicios", "horas": 1.0, "guia": "Tarjetas de 3 columnas con ícono, nombre y una línea. Cada una lleva a su página."},
    "beneficios":  {"nombre": "Por qué elegirnos", "horas": 0.75, "guia": "Tres beneficios con ícono. Hablan del cliente, no de la empresa."},
    "proceso":     {"nombre": "Cómo funciona", "horas": 0.75, "guia": "Tres pasos numerados, de izquierda a derecha."},
    "testimonios": {"nombre": "Testimonios", "horas": 1.0, "guia": "Tres reseñas reales con nombre. Si hay de Google, mejor: con las estrellas."},
    "galeria":     {"nombre": "Galería", "horas": 0.75, "guia": "Grilla de 6 a 9 fotos reales. Nada de stock."},
    "equipo":      {"nombre": "Equipo", "horas": 0.75, "guia": "Foto, nombre y especialidad de cada persona."},
    "historia":    {"nombre": "Nuestra historia", "horas": 0.5, "guia": "Texto a dos columnas con una foto. Corto: tres párrafos como mucho."},
    "faq":         {"nombre": "Preguntas frecuentes", "horas": 0.75, "guia": "Acordeón. Las preguntas que hoy contestan por WhatsApp todos los días."},
    "precios":     {"nombre": "Planes y precios", "horas": 1.0, "guia": "Dos o tres columnas comparables. La recomendada, destacada."},
    "destacados":  {"nombre": "Destacados", "horas": 1.0, "guia": "Los 3 a 6 productos o platos que más salen, con foto y precio si se publica."},
    "carta":       {"nombre": "Carta", "horas": 1.5, "guia": "Por categoría, con precio. Mejor texto que PDF: se lee en el celular y Google la indexa."},
    "catalogo":    {"nombre": "Catálogo", "horas": 1.5, "guia": "Categorías con foto que llevan a la tienda o al WhatsApp con el producto."},
    "detalle":     {"nombre": "Detalle del servicio", "horas": 1.0, "guia": "Qué es, para quién es, qué incluye y cuánto tarda. Una foto real."},
    "zonas":       {"nombre": "Zonas que cubrimos", "horas": 0.5, "guia": "Lista de barrios o ciudades y un mapa. Suma mucho al SEO local."},
    "horarios":    {"nombre": "Horarios", "horas": 0.5, "guia": "Tabla simple por día. Igual a la ficha de Google."},
    "formulario":  {"nombre": "Formulario", "horas": 1.0, "guia": "Pocos campos: nombre, celular y qué necesita. Llega por mail y WhatsApp."},
    "turnos":      {"nombre": "Reservar turno", "horas": 1.0, "guia": "El link de la agenda online embebido o un botón grande."},
    "envios":      {"nombre": "Envíos y cambios", "horas": 0.5, "guia": "Cómo llega, cuánto sale, en cuánto tiempo, y cómo se cambia."},
    "contacto":    {"nombre": "Contacto y mapa", "horas": 0.75, "guia": "Dirección, horario, teléfono, WhatsApp y el mapa de Google."},
    "cta":         {"nombre": "Llamado final", "horas": 0.5, "guia": "Una franja de color con una frase y el botón principal."},
}

# Lo que suma una página por existir: plantilla, menú, SEO y la revisión en el
# celular. Y lo que se suma al total por la vuelta de ajustes.
HORAS_POR_PAGINA = 0.5
MARGEN_AJUSTES = 0.2


# ── textos ───────────────────────────────────────────────────────────────────

def _vos(b: dict, vos: str, usted: str) -> str:
    return usted if b.get("tono") == "profesional" else vos


def _elegir(semilla: str, opciones: list) -> str:
    """Siempre la misma opción para el mismo brief: el plano no cambia solo."""
    n = int(hashlib.sha1(semilla.encode()).hexdigest()[:8], 16)
    return opciones[n % len(opciones)]


def _en_ciudad(b: dict) -> str:
    return f" en {b['ciudad']}" if b.get("ciudad") else ""


def _boton(b: dict) -> str:
    return {
        "whatsapp": "Escribinos por WhatsApp" if b.get("tono") != "profesional" else "Escríbanos por WhatsApp",
        "reservas": _vos(b, "Reservá tu turno", "Reserve su turno"),
        "compras": "Ver la tienda",
        "presupuesto": _vos(b, "Pedí tu presupuesto", "Solicite un presupuesto"),
        "visitas": "Cómo llegar",
    }[b["objetivo"]]


def _lista(xs: list[str]) -> str:
    xs = [x for x in xs if x]
    if len(xs) <= 1:
        return "".join(xs)
    return ", ".join(xs[:-1]) + " y " + xs[-1]


def _sec(bloque: str, titulo: str, texto: str = "", items=None, boton: str = "") -> dict:
    return {"bloque": bloque, "titulo": titulo, "texto": texto,
            "items": [{"titulo": t, "texto": x} for t, x in (items or [])], "boton": boton}


def _servicio_linea(b: dict, s: str) -> str:
    fam = FAMILIAS[b["familia"]]
    return _elegir(b["nombre"] + s, [
        f"{s} con turnos {('en ' + b['ciudad']) if b.get('ciudad') else 'disponibles'} y atención personalizada."
        if fam["objetivo"] == "reservas" else f"{s}: lo hacemos bien, a tiempo y con precio claro.",
        f"Todo lo que {_vos(b, 'necesitás', 'necesita')} en {s.lower()}, explicado sin vueltas.",
        f"{s} para {fam['cliente']} que quieren resolverlo de una.",
    ])


def _tema(b: dict) -> str:
    """De qué es el negocio, en dos o tres palabras: el rubro si lo escribieron."""
    if b.get("rubro"):
        return b["rubro"][:1].upper() + b["rubro"][1:]
    return b["servicios"][0] if b["servicios"] else FAMILIAS[b["familia"]]["servicios"]


def _hero(b: dict) -> dict:
    fam = FAMILIAS[b["familia"]]
    principal = _tema(b)
    titulo = {
        "turnos": f"{principal}{_en_ciudad(b)}, con turno en minutos",
        "gastronomia": f"{b['nombre']}: {_elegir(b['nombre'], ['comida de verdad', 'el sabor de siempre', 'para volver'])}{_en_ciudad(b)}",
        "comercio": f"{principal}{_en_ciudad(b)}, con quien sabe",
        "profesional": f"{principal}{_en_ciudad(b)}, sin sorpresas",
        "oficios": f"{principal}{_en_ciudad(b)}: presupuesto en el día",
        "educacion": f"{principal}{_en_ciudad(b)} para avanzar de verdad",
    }[b["familia"]]
    if b.get("diferencial"):
        bajada = b["diferencial"].rstrip(".") + "."
    else:
        # Sin inventar datos: "hace X años" solo si el brief lo dice.
        cierre = (f"Más de {b['anios']} años atendiendo a {fam['cliente']}{_en_ciudad(b)}." if b.get("anios")
                  else _elegir(b["nombre"] + "bajada", [
                      _vos(b, "Escribinos y te respondemos en el día.", "Escríbanos y le respondemos en el día."),
                      _vos(b, "Te atiende una persona, no un call center.", "Lo atiende una persona, no un call center."),
                  ]))
        bajada = f"{_lista(b['servicios'][:3]) or fam['servicios']}. {cierre}"
    return _sec("hero", titulo, bajada, boton=_boton(b))


def _confianza(b: dict) -> dict:
    items = []
    if b.get("anios"):
        items.append((f"+{b['anios']} años", f"atendiendo{_en_ciudad(b)}"))
    items += [("Respuesta en el día", "por WhatsApp o teléfono"),
              ("Precios claros", "sin costos escondidos"),
              ("Reseñas reales", "de clientes en Google")]
    return _sec("confianza", "", "", items=items[:4])


def _servicios(b: dict, con_links: bool) -> dict:
    fam = FAMILIAS[b["familia"]]
    items = [(s, _servicio_linea(b, s)) for s in b["servicios"][:9]] or [
        (f"{fam['servicio'].capitalize()} {i}", "Completar con el cliente.") for i in (1, 2, 3)]
    return _sec("servicios", f"{fam['servicios']}{'' if fam['servicios'] == 'La carta' else ' que ofrecemos'}",
                "Tocá cada uno para ver el detalle." if con_links else "", items=items)


def _beneficios(b: dict) -> dict:
    por_familia = {
        "turnos": [("Turnos online", "Reservás a cualquier hora, sin llamar."),
                   ("Recordatorio por WhatsApp", "Te avisamos el día antes."),
                   ("Profesionales con experiencia", "Te atiende siempre alguien que sabe.")],
        "gastronomia": [("Ingredientes frescos", "Compramos todos los días."),
                        ("Pedidos por WhatsApp", "Sin apps ni comisiones."),
                        ("Para ir o para llevar", "Como te quede mejor.")],
        "comercio": [("Asesoramiento", "Te ayudamos a elegir lo que de verdad necesitás."),
                     ("Stock real", "Consultá por WhatsApp antes de venir."),
                     ("Envíos", "Llegamos a tu casa o lo retirás en el local.")],
        "profesional": [("Respuesta rápida", "Te contestamos en el día."),
                        ("Honorarios claros", "Sabés cuánto sale antes de empezar."),
                        ("Trato directo", "Hablás siempre con el profesional, no con un intermediario.")],
        "oficios": [("Presupuesto sin cargo", "Vamos, medimos y te pasamos el precio cerrado."),
                    ("Garantía por escrito", "Si algo falla, volvemos."),
                    ("Puntualidad", "Llegamos el día y la hora que acordamos.")],
        "educacion": [("Grupos reducidos", "Para que nadie se quede atrás."),
                      ("Clase de prueba", "Probás antes de inscribirte."),
                      ("Horarios flexibles", "Mañana, tarde y noche.")],
    }[b["familia"]]
    items = [(t, x if b.get("tono") != "profesional" else _a_usted(x)) for t, x in por_familia]
    return _sec("beneficios", f"Por qué elegir {b['nombre']}", "", items=items)


def _a_usted(x: str) -> str:
    cambios = {"Reservás": "Reserva", "Te avisamos": "Le avisamos", "Te ayudamos": "Lo ayudamos",
               "necesitás": "necesita", "Consultá": "Consulte", "tu casa": "su casa", "lo retirás": "lo retira",
               "Te contestamos": "Le contestamos", "Sabés": "Sabe", "Hablás": "Habla",
               "te pasamos": "le pasamos", "Probás": "Prueba", "inscribirte": "inscribirse",
               "Te atiende": "Lo atiende", "te quede": "le quede"}
    for k, v in cambios.items():
        x = x.replace(k, v)
    return x


def _proceso(b: dict) -> dict:
    pasos = {
        "reservas": [("Elegí el turno", "Desde el celular, a cualquier hora."), ("Te confirmamos", "Por WhatsApp, al instante."),
                     ("Te esperamos", "Y te recordamos el día antes.")],
        "presupuesto": [("Contanos qué necesitás", "Por WhatsApp o con el formulario."),
                        ("Te pasamos el presupuesto", "Cerrado, en el día."),
                        ("Lo hacemos", "En la fecha acordada y con garantía.")],
        "compras": [("Elegí", "Entrá a la tienda y sumá al carrito."), ("Pagá como quieras", "Tarjeta, Mercado Pago o transferencia."),
                    ("Recibilo", "En tu casa o retiralo en el local.")],
    }.get(b["objetivo"], [("Escribinos", "Por WhatsApp, cuando quieras."), ("Te asesoramos", "Te respondemos en el día."),
                          ("Listo", "Coordinamos y lo resolvemos.")])
    if b.get("tono") == "profesional":
        pasos = [(_a_usted(t.replace("Elegí", "Elija").replace("Contanos", "Cuéntenos").replace("Escribinos", "Escríbanos")
                           .replace("Pagá", "Pague").replace("Recibilo", "Recíbalo").replace("Te ", "Le ")),
                  _a_usted(x.replace("retiralo", "retírelo").replace("tu casa", "su casa").replace("Te ", "Le ")))
                 for t, x in pasos]
    return _sec("proceso", "Cómo funciona", "", items=pasos)


def _testimonios(b: dict) -> dict:
    return _sec("testimonios", f"Lo que dicen nuestros {FAMILIAS[b['familia']]['cliente']}",
                "Pedirle al cliente 3 reseñas reales de Google, con nombre. Si no tiene, sacar la sección: "
                "testimonios inventados restan.",
                items=[("Nombre y apellido", "Reseña real del cliente."), ("Nombre y apellido", "Reseña real del cliente."),
                       ("Nombre y apellido", "Reseña real del cliente.")])


def _faq(b: dict) -> dict:
    fam = b["familia"]
    preguntas = {
        "turnos": [("¿Cómo reservo un turno?", "Desde esta página, en el botón Reservar, o por WhatsApp."),
                   ("¿Atienden mutualistas o seguros?", "Completar con el cliente."),
                   ("¿Puedo cancelar o cambiar el turno?", "Sí, desde el link que te llega al reservar.")],
        "gastronomia": [("¿Hacen delivery?", "Completar zonas y costo con el cliente."),
                        ("¿Tienen opciones sin TACC o veganas?", "Completar con el cliente."),
                        ("¿Se puede reservar?", "Sí, por WhatsApp.")],
        "comercio": [("¿Hacen envíos?", "Completar zonas y costo con el cliente."),
                     ("¿Qué medios de pago aceptan?", "Completar con el cliente."),
                     ("¿Puedo consultar stock antes de ir?", "Sí, por WhatsApp.")],
        "profesional": [("¿Cuánto cuesta la primera consulta?", "Completar con el cliente."),
                        ("¿Atienden en forma virtual?", "Completar con el cliente."),
                        ("¿Qué documentación tengo que traer?", "Completar con el cliente.")],
        "oficios": [("¿El presupuesto tiene costo?", "No: vamos, medimos y te lo pasamos sin cargo."),
                    ("¿Qué garantía tienen los trabajos?", "Completar con el cliente."),
                    ("¿Trabajan en mi zona?", "Mirá la lista de zonas o escribinos.")],
        "educacion": [("¿Hay clase de prueba?", "Completar con el cliente."),
                      ("¿Cuánto sale la cuota?", "Completar con el cliente."),
                      ("¿Qué nivel necesito?", "Hacemos una prueba de nivel sin costo.")],
    }[fam]
    if b.get("tono") == "profesional":
        preguntas = [(t, _a_usted(x.replace("te llega", "le llega").replace("te lo pasamos", "se lo pasamos")
                                  .replace("Mirá", "Vea").replace("escribinos", "escríbanos"))) for t, x in preguntas]
    return _sec("faq", "Preguntas frecuentes", "", items=preguntas)


def _contacto(b: dict) -> dict:
    datos = [x for x in (b.get("direccion"), b.get("ciudad"), b.get("whatsapp") and f"WhatsApp {b['whatsapp']}") if x]
    return _sec("contacto", _vos(b, "Vení a vernos", "Visítenos") if b["objetivo"] == "visitas" else "Contacto",
                " · ".join(datos) or "Dirección, horario, teléfono y WhatsApp: completar con el cliente.",
                boton=_boton(b))


def _cta(b: dict) -> dict:
    frase = {
        "reservas": "¿Lo dejamos agendado?",
        "presupuesto": _vos(b, "Contanos qué necesitás y te pasamos el precio hoy", "Cuéntenos qué necesita y le pasamos el precio hoy"),
        "compras": _vos(b, "Encontrá lo que buscás en la tienda", "Encuentre lo que busca en la tienda"),
        "visitas": _vos(b, "Te esperamos en el local", "Lo esperamos en el local"),
        "whatsapp": _vos(b, "¿Hablamos?", "¿Conversamos?"),
    }[b["objetivo"]]
    return _sec("cta", frase, _vos(b, "Te respondemos en el día.", "Le respondemos en el día."), boton=_boton(b))


# ── páginas ──────────────────────────────────────────────────────────────────

def _seo(b: dict, tema: str, descripcion: str) -> dict:
    titulo = f"{tema}{_en_ciudad(b)} | {b['nombre']}"
    if len(titulo) > SEO_TITULO_IDEAL:
        titulo = f"{tema} | {b['nombre']}"
    return {"titulo": titulo, "descripcion": descripcion[:LARGO["seo_descripcion"]]}


def _pagina(clave: str, nombre: str, ruta: str, seo: dict, h1: str, secciones: list, padre: str | None = None) -> dict:
    return {"clave": clave, "nombre": nombre, "slug": ruta, "padre": padre, "h1": h1,
            "seo": seo, "secciones": secciones}


def _paginas_de(b: dict) -> list[dict]:
    fam = FAMILIAS[b["familia"]]
    tienda = b["tipo"] == "ecommerce"
    claves = list(fam["paginas"])
    if tienda:
        # Una tienda reemplaza el catálogo y suma lo que se pregunta antes de comprar.
        claves = [c for c in claves if c not in ("productos", "carta")]
        claves[1:1] = ["tienda"]
        if "envios" not in claves:
            claves.insert(-1, "envios")
    sub = fam["pagina_por_servicio"] and "servicios" in claves and len(b["servicios"]) >= 2
    servicios_txt = _lista(b["servicios"][:3]) or fam["servicios"]
    paginas = []
    for c in claves:
        if c == "inicio":
            secs = [_hero(b), _confianza(b)]
            if tienda:
                secs.append(_sec("catalogo", "Comprá por categoría", "", items=[(s, "") for s in b["servicios"][:6]]))
                secs.append(_sec("destacados", "Lo más vendido", "Elegir con el cliente los 4 a 6 productos que más salen."))
            elif b["familia"] == "gastronomia":
                secs.append(_sec("destacados", "Los que más salen", "Elegir con el cliente los 3 a 6 platos estrella, con foto.",
                                 items=[(s, "") for s in b["servicios"][:6]]))
            else:
                secs.append(_servicios(b, sub))
            secs += [_beneficios(b)]
            if b["objetivo"] in ("reservas", "presupuesto", "compras"):
                secs.append(_proceso(b))
            secs += [_testimonios(b), _cta(b)]
            paginas.append(_pagina("inicio", "Inicio", "/", _seo(b, _tema(b), (
                f"{b['nombre']}: {servicios_txt.lower()}{_en_ciudad(b)}. {_boton(b)}.")), _hero(b)["titulo"], secs))
        elif c == "servicios":
            secs = [_sec("hero", fam["servicios"], f"{servicios_txt}{_en_ciudad(b)}."), _servicios(b, sub), _faq(b), _cta(b)]
            paginas.append(_pagina("servicios", fam["servicios"], "/" + slug(fam["servicios"]),
                                   _seo(b, fam["servicios"], f"{servicios_txt}{_en_ciudad(b)}. Conocé cada {fam['servicio']} de {b['nombre']}."),
                                   f"{fam['servicios']} de {b['nombre']}", secs))
            if sub:
                for s in b["servicios"][:MAX_SERVICIOS]:
                    cs = "servicio-" + slug(s)
                    paginas.append(_pagina(
                        cs, s, f"/{slug(fam['servicios'])}/{slug(s)}",
                        _seo(b, s, f"{s}{_en_ciudad(b)} · {b['nombre']}. {_servicio_linea(b, s)}"),
                        f"{s}{_en_ciudad(b)}",
                        [_sec("hero", f"{s}{_en_ciudad(b)}", _servicio_linea(b, s), boton=_boton(b)),
                         _sec("detalle", f"Qué es y para quién es",
                              f"Completar con el cliente: qué incluye {s.lower()}, cuánto dura y para quién está pensado."),
                         _proceso(b), _sec("faq", f"Preguntas sobre {s.lower()}", "", items=[
                             (f"¿Cuánto sale {s.lower()}?", "Completar con el cliente."),
                             (f"¿Cuánto tarda?", "Completar con el cliente.")]),
                         _cta(b)],
                        padre="servicios"))
        elif c == "carta":
            paginas.append(_pagina("carta", "La carta", "/carta", _seo(b, "Carta", f"La carta de {b['nombre']}{_en_ciudad(b)}: {servicios_txt.lower()}. Pedí por WhatsApp."),
                                   f"La carta de {b['nombre']}",
                                   [_sec("carta", "La carta", "Por categoría y con precios. Pedirle al cliente la carta actualizada.",
                                         items=[(s, "") for s in b["servicios"][:MAX_ITEMS]]), _cta(b)]))
        elif c == "productos":
            paginas.append(_pagina("productos", "Productos", "/productos", _seo(b, "Productos", f"{servicios_txt}{_en_ciudad(b)}. Consultá stock y precios por WhatsApp."),
                                   f"Productos de {b['nombre']}",
                                   [_sec("catalogo", "Lo que vas a encontrar", "", items=[(s, "") for s in b["servicios"][:MAX_ITEMS]]),
                                    _sec("destacados", "Destacados", "Los que más salen. Elegir con el cliente."), _cta(b)]))
        elif c == "tienda":
            paginas.append(_pagina("tienda", "Tienda", "/tienda", _seo(b, "Tienda online", f"Comprá online en {b['nombre']}: {servicios_txt.lower()}. Envíos{_en_ciudad(b) or ' a todo el país'}."),
                                   "Tienda online",
                                   [_sec("catalogo", "Categorías", "La arma WooCommerce: acá va el orden de categorías y los filtros.",
                                         items=[(s, "") for s in b["servicios"][:MAX_ITEMS]])]))
        elif c == "envios":
            paginas.append(_pagina("envios", "Envíos y cambios", "/envios-y-cambios", _seo(b, "Envíos y cambios", f"Cómo llegan los pedidos de {b['nombre']}, cuánto sale el envío y cómo se cambia."),
                                   "Envíos y cambios",
                                   [_sec("envios", "Envíos", "Completar con el cliente: zonas, costo, plazos y retiro en local."),
                                    _sec("faq", "Cambios y devoluciones", "", items=[
                                        ("¿Cuánto tengo para cambiar?", "Completar con el cliente."),
                                        ("¿Qué medios de pago aceptan?", "Completar con el cliente.")])]))
        elif c == "equipo":
            paginas.append(_pagina("equipo", "Equipo", "/equipo", _seo(b, "Nuestro equipo", f"Conocé a los profesionales de {b['nombre']}{_en_ciudad(b)}."),
                                   f"El equipo de {b['nombre']}",
                                   [_sec("equipo", "Quién te atiende", "Pedir foto, nombre y especialidad de cada profesional."), _testimonios(b), _cta(b)]))
        elif c == "turnos":
            paginas.append(_pagina("turnos", "Reservar turno", "/turnos", _seo(b, "Reservar turno", f"Reservá tu turno en {b['nombre']}{_en_ciudad(b)} en minutos, a cualquier hora."),
                                   "Reservá tu turno",
                                   [_sec("turnos", "Elegí día y hora", "Agenda online embebida (la de Scalerics u otra que use el cliente).", boton=_boton(b)),
                                    _sec("horarios", "Horarios de atención", "Completar con el cliente."), _faq(b)]))
        elif c == "reservas":
            pedir = b["objetivo"] in ("whatsapp", "compras")
            paginas.append(_pagina("reservas", "Reservas y pedidos", "/reservas", _seo(b, "Reservas y pedidos", f"Reservá mesa o hacé tu pedido en {b['nombre']}{_en_ciudad(b)}."),
                                   "Reservas y pedidos",
                                   [_sec("formulario", "Reservá tu mesa", "Fecha, hora, cuántos son y un celular.", boton="Reservar"),
                                    _sec("cta", "¿Pedís para llevar?" if pedir else "Pedidos", "Pedí por WhatsApp o desde la carta online.", boton=_boton(b)),
                                    _sec("horarios", "Horarios", "Completar con el cliente.")]))
        elif c == "nosotros":
            paginas.append(_pagina("nosotros", "Nosotros", "/nosotros", _seo(b, "Quiénes somos", f"La historia de {b['nombre']}{_en_ciudad(b)}" + (f": más de {b['anios']} años." if b.get("anios") else ".")),
                                   f"Quiénes somos",
                                   [_sec("historia", f"La historia de {b['nombre']}",
                                         (f"Más de {b['anios']} años{_en_ciudad(b)}. " if b.get("anios") else "")
                                         + "Pedirle al cliente cómo empezó, qué los diferencia y una foto del equipo o del local."),
                                    _sec("galeria", "El local", "Fotos reales del local y del equipo."), _cta(b)]))
        elif c == "trabajos":
            paginas.append(_pagina("trabajos", "Trabajos realizados", "/trabajos", _seo(b, "Trabajos realizados", f"Obras y trabajos de {b['nombre']}{_en_ciudad(b)}: {servicios_txt.lower()}."),
                                   "Trabajos realizados",
                                   [_sec("galeria", "Antes y después", "Fotos reales de obras terminadas, con una línea de qué se hizo y dónde."),
                                    _testimonios(b), _cta(b)]))
        elif c == "zonas":
            paginas.append(_pagina("zonas", "Zonas", "/zonas", _seo(b, "Zonas que cubrimos", f"{b['nombre']} trabaja{_en_ciudad(b)} y alrededores. Mirá si llegamos a tu zona."),
                                   "Zonas que cubrimos",
                                   [_sec("zonas", "¿Llegamos a tu zona?", "Lista de barrios o ciudades: completar con el cliente."), _cta(b)]))
        elif c == "horarios":
            paginas.append(_pagina("horarios", "Horarios y precios", "/horarios-y-precios", _seo(b, "Horarios y precios", f"Horarios y cuotas de {b['nombre']}{_en_ciudad(b)}."),
                                   "Horarios y precios",
                                   [_sec("horarios", "Horarios", "Grilla por día y por curso. Completar con el cliente."),
                                    _sec("precios", "Cuotas", "", items=[("Mensual", "Completar"), ("Trimestral", "Completar")]), _cta(b)]))
        elif c == "faq":
            paginas.append(_pagina("faq", "Preguntas frecuentes", "/preguntas-frecuentes", _seo(b, "Preguntas frecuentes", f"Las dudas más comunes sobre {servicios_txt.lower()} en {b['nombre']}."),
                                   "Preguntas frecuentes", [_faq(b), _cta(b)]))
        elif c in ("contacto", "presupuesto"):
            es_presu = c == "presupuesto"
            secs = []
            if es_presu:
                secs.append(_sec("formulario", "Pedí tu presupuesto", "Nombre, celular, zona y qué hay que hacer. Con foto opcional.",
                                 boton="Pedir presupuesto"))
            secs.append(_contacto(b))
            secs.append(_sec("horarios", "Horarios", "Completar con el cliente."))
            paginas.append(_pagina(c, "Presupuesto" if es_presu else "Contacto", "/" + c,
                                   _seo(b, "Pedí tu presupuesto" if es_presu else "Contacto",
                                        f"Contactá a {b['nombre']}{_en_ciudad(b)}" + (f" por WhatsApp al {b['whatsapp']}." if b.get("whatsapp") else ".")),
                                   "Pedí tu presupuesto" if es_presu else f"Contacto", secs))
    if b.get("tono") == "profesional":
        for p in paginas:
            p["seo"]["descripcion"] = _a_usted(p["seo"]["descripcion"].replace("Conocé", "Conozca").replace("Reservá", "Reserve")
                                               .replace("Pedí", "Pida").replace("Comprá", "Compre").replace("Consultá", "Consulte")
                                               .replace("Contactá", "Contacte").replace("hacé tu", "haga su").replace("tu turno", "su turno")
                                               .replace("tu zona", "su zona").replace("Mirá", "Vea"))
            p["h1"] = p["h1"].replace("Reservá tu", "Reserve su").replace("Pedí tu", "Solicite su")
    for p in paginas:
        for i, s in enumerate(p["secciones"]):
            s["id"] = f"{p['clave']}-{i + 1}"
    return paginas


# ── brief ────────────────────────────────────────────────────────────────────

def _limpio(v, largo: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:largo]


def validar_brief(data: dict) -> dict:
    nombre = _limpio(data.get("nombre"), 80)
    if len(nombre) < 2:
        raise NoSePuede("Falta el nombre del negocio")
    rubro = _limpio(data.get("rubro"), 80)
    familia = str(data.get("familia") or "").strip() or adivinar_familia(rubro)
    if familia not in FAMILIAS:
        raise NoSePuede("Elegí qué tipo de negocio es")
    tipo = str(data.get("tipo") or "web")
    if tipo not in TIPOS:
        raise NoSePuede("El tipo de sitio no existe")
    objetivo = str(data.get("objetivo") or "").strip()
    if not objetivo:
        objetivo = "compras" if tipo == "ecommerce" else FAMILIAS[familia]["objetivo"]
    if objetivo not in OBJETIVOS:
        raise NoSePuede("El objetivo no existe")
    tono = str(data.get("tono") or "cercano")
    if tono not in TONOS:
        raise NoSePuede("El tono no existe")
    crudos = data.get("servicios") or []
    if isinstance(crudos, str):
        crudos = re.split(r"[\n,;]+", crudos)
    servicios, vistos = [], set()
    for s in crudos:
        s = _limpio(s, LARGO["nombre"])
        if s and slug(s) and slug(s) not in vistos:
            vistos.add(slug(s))
            servicios.append(s[:1].upper() + s[1:])
    if len(servicios) > MAX_SERVICIOS:
        raise NoSePuede(f"Hasta {MAX_SERVICIOS} servicios: agrupá los parecidos")
    anios = data.get("anios")
    if anios in ("", None):
        anios = None
    else:
        try:
            anios = int(anios)
        except (TypeError, ValueError):
            raise NoSePuede("Los años tienen que ser un número")
        if not 0 < anios < 150:
            raise NoSePuede("Los años tienen que ser entre 1 y 149")
    return {"nombre": nombre, "rubro": rubro, "familia": familia, "tipo": tipo, "objetivo": objetivo, "tono": tono,
            "servicios": servicios, "ciudad": _limpio(data.get("ciudad"), 60),
            "direccion": _limpio(data.get("direccion"), 120), "whatsapp": _limpio(data.get("whatsapp"), 30),
            "diferencial": _limpio(data.get("diferencial"), 240), "anios": anios,
            "dominio": _limpio(data.get("dominio"), 120).lower().removeprefix("https://").removeprefix("http://").strip("/")}


def generar(brief: dict) -> list[dict]:
    """El mapa propuesto para un brief ya validado."""
    return validar_mapa(_paginas_de(brief))


# ── el mapa ──────────────────────────────────────────────────────────────────

def _slug_ruta(v) -> str:
    partes = [slug(p) for p in str(v or "").split("/")]
    ruta = "/" + "/".join(p for p in partes if p)
    return ruta[:LARGO["slug"]]


def validar_mapa(paginas) -> list[dict]:
    """La única puerta del mapa: tipos, topes, claves únicas y padres que existan."""
    if not isinstance(paginas, list) or not paginas:
        raise NoSePuede("El sitio necesita al menos una página")
    if len(paginas) > MAX_PAGINAS:
        raise NoSePuede(f"Hasta {MAX_PAGINAS} páginas por sitio")
    salida, claves, rutas = [], set(), set()
    for i, p in enumerate(paginas):
        if not isinstance(p, dict):
            raise NoSePuede("Página mal armada")
        nombre = _limpio(p.get("nombre"), LARGO["nombre"])
        if not nombre:
            raise NoSePuede(f"La página {i + 1} no tiene nombre")
        clave = slug(p.get("clave") or nombre)[:60] or f"pagina-{i + 1}"
        base, n = clave, 2
        while clave in claves:
            clave, n = f"{base}-{n}", n + 1
        claves.add(clave)
        ruta = "/" if i == 0 and str(p.get("slug") or "/").strip() in ("", "/") else _slug_ruta(p.get("slug") or nombre)
        if ruta == "/" and i:
            ruta = "/" + slug(nombre)
        if ruta in rutas:
            raise NoSePuede(f"Dos páginas tienen la misma dirección: {ruta}")
        rutas.add(ruta)
        seo = p.get("seo") if isinstance(p.get("seo"), dict) else {}
        secciones = p.get("secciones") or []
        if not isinstance(secciones, list):
            raise NoSePuede(f"Las secciones de {nombre} están mal armadas")
        if len(secciones) > MAX_SECCIONES:
            raise NoSePuede(f"{nombre}: hasta {MAX_SECCIONES} secciones por página")
        secs = []
        for j, s in enumerate(secciones):
            if not isinstance(s, dict) or s.get("bloque") not in BLOQUES:
                raise NoSePuede(f"{nombre}: la sección {j + 1} no es de un tipo conocido")
            items = s.get("items") or []
            if not isinstance(items, list) or len(items) > MAX_ITEMS:
                raise NoSePuede(f"{nombre}: hasta {MAX_ITEMS} elementos por sección")
            secs.append({
                "id": slug(s.get("id") or "")[:80] or f"{clave}-{j + 1}",
                "bloque": s["bloque"],
                "titulo": _limpio(s.get("titulo"), LARGO["titulo"]),
                "texto": str(s.get("texto") or "").strip()[:LARGO["texto"]],
                "items": [{"titulo": _limpio(it.get("titulo"), LARGO["item_titulo"]),
                           "texto": _limpio(it.get("texto"), LARGO["item_texto"])}
                          for it in items if isinstance(it, dict) and (it.get("titulo") or it.get("texto"))],
                "boton": _limpio(s.get("boton"), LARGO["boton"]),
            })
        salida.append({"clave": clave, "nombre": nombre, "slug": ruta, "padre": p.get("padre") or None,
                       "h1": _limpio(p.get("h1"), LARGO["h1"]),
                       "seo": {"titulo": _limpio(seo.get("titulo"), LARGO["seo_titulo"]),
                               "descripcion": _limpio(seo.get("descripcion"), LARGO["seo_descripcion"])},
                       "secciones": secs})
    for p in salida:
        if p["padre"] and (p["padre"] not in claves or p["padre"] == p["clave"]):
            p["padre"] = None
    # Un solo nivel de anidado: el menú de un sitio de comercio no tiene más.
    padres = {p["clave"]: p["padre"] for p in salida}
    for p in salida:
        if p["padre"] and padres.get(p["padre"]):
            p["padre"] = padres[p["padre"]]
    return salida


def chequeos(paginas: list[dict]) -> list[dict]:
    """Lo que conviene mirar antes de mandárselo al cliente. No frena nada."""
    avisos = []
    titulos: dict[str, list[str]] = {}
    for p in paginas:
        t = p["seo"]["titulo"]
        if not t:
            avisos.append({"pagina": p["clave"], "texto": f"{p['nombre']}: falta el título SEO"})
        elif len(t) > SEO_TITULO_IDEAL:
            avisos.append({"pagina": p["clave"], "texto": f"{p['nombre']}: el título SEO tiene {len(t)} caracteres y Google corta en {SEO_TITULO_IDEAL}"})
        titulos.setdefault(t.lower(), []).append(p["nombre"]) if t else None
        d = p["seo"]["descripcion"]
        if not d:
            avisos.append({"pagina": p["clave"], "texto": f"{p['nombre']}: falta la descripción SEO"})
        elif len(d) > SEO_DESCRIPCION_IDEAL:
            avisos.append({"pagina": p["clave"], "texto": f"{p['nombre']}: la descripción tiene {len(d)} caracteres y Google corta en {SEO_DESCRIPCION_IDEAL}"})
        if not p["h1"]:
            avisos.append({"pagina": p["clave"], "texto": f"{p['nombre']}: falta el H1"})
        if not p["secciones"]:
            avisos.append({"pagina": p["clave"], "texto": f"{p['nombre']}: no tiene secciones"})
        pendientes = sum(1 for s in p["secciones"] if "completar" in (s["texto"] + " ".join(i["texto"] for i in s["items"])).lower())
        if pendientes:
            avisos.append({"pagina": p["clave"], "nivel": "pendiente",
                           "texto": f"{p['nombre']}: {pendientes} {'secciones' if pendientes > 1 else 'sección'} con datos para completar con el cliente"})
    for t, nombres in titulos.items():
        if len(nombres) > 1:
            avisos.append({"pagina": None, "texto": f"Título SEO repetido en {_lista(nombres)}"})
    return avisos


def estimar_horas(paginas: list[dict]) -> dict:
    """Cuánto lleva armarlo en Elementor con los textos ya aprobados."""
    por_pagina = []
    for p in paginas:
        h = HORAS_POR_PAGINA + sum(BLOQUES[s["bloque"]]["horas"] for s in p["secciones"])
        por_pagina.append({"clave": p["clave"], "horas": round(h, 2)})
    armado = sum(x["horas"] for x in por_pagina)
    total = armado * (1 + MARGEN_AJUSTES)
    return {"por_pagina": por_pagina, "armado": round(armado, 1), "ajustes": round(total - armado, 1),
            "total": round(total * 2) / 2}


# ── base ─────────────────────────────────────────────────────────────────────

def init_planos(conn: sqlite3.Connection) -> None:
    """Tablas del panel. La llama `database.init_db` con su conexión."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pl_planos (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre        TEXT NOT NULL,
            brief         TEXT NOT NULL,
            mapa          TEXT NOT NULL,
            token         TEXT NOT NULL UNIQUE,
            estado        TEXT NOT NULL DEFAULT 'borrador',
            business_id   INTEGER,
            creado_por    TEXT,
            creado_en     TEXT NOT NULL,
            actualizado_en TEXT NOT NULL,
            enviado_en    TEXT,
            aprobado_en   TEXT
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pl_respuestas (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            plano_id   INTEGER NOT NULL,
            pagina     TEXT NOT NULL,
            decision   TEXT NOT NULL,
            comentario TEXT,
            nombre     TEXT,
            resuelta   INTEGER NOT NULL DEFAULT 0,
            creado_en  TEXT NOT NULL
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pl_resp ON pl_respuestas (plano_id, pagina, id)")


def _fila(conn, pid: int) -> sqlite3.Row:
    f = conn.execute("SELECT * FROM pl_planos WHERE id = ?", (pid,)).fetchone()
    if not f:
        raise NoSePuede("El plano no existe")
    return f


def _respuestas(conn, pid: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, pagina, decision, comentario, nombre, resuelta, creado_en FROM pl_respuestas "
        "WHERE plano_id = ? ORDER BY id", (pid,))]


def estado_paginas(paginas: list[dict], respuestas: list[dict]) -> dict:
    """La última decisión del cliente en cada página: aprobada, cambios o nada.

    Una página editada después de que el cliente pidió cambios sigue en
    "cambios" hasta que el equipo marca el pedido como resuelto: ahí vuelve a
    quedar pendiente para que el cliente la mire de nuevo.
    """
    ultima: dict[str, dict] = {}
    for r in respuestas:
        ultima[r["pagina"]] = r
    salida = {}
    for p in paginas:
        r = ultima.get(p["clave"])
        if not r or (r["decision"] == "cambios" and r["resuelta"]):
            salida[p["clave"]] = "pendiente"
        else:
            salida[p["clave"]] = "aprobada" if r["decision"] == "aprobada" else "cambios"
    return salida


def _armar(f: sqlite3.Row, respuestas: list[dict] | None = None, completo: bool = True) -> dict:
    brief = json.loads(f["brief"])
    paginas = json.loads(f["mapa"])
    d = {k: f[k] for k in ("id", "nombre", "token", "estado", "business_id", "creado_por", "creado_en",
                           "actualizado_en", "enviado_en", "aprobado_en")}
    d["brief"] = brief
    d["familia_nombre"] = FAMILIAS[brief["familia"]]["nombre"]
    d["estado_nombre"] = ESTADOS.get(f["estado"], f["estado"])
    d["n_paginas"] = len(paginas)
    d["n_secciones"] = sum(len(p["secciones"]) for p in paginas)
    d["horas"] = estimar_horas(paginas)["total"]
    if respuestas is not None:
        ep = estado_paginas(paginas, respuestas)
        d["aprobadas"] = sum(1 for v in ep.values() if v == "aprobada")
        d["con_cambios"] = sum(1 for v in ep.values() if v == "cambios")
        # Lo pedido sobre una página que el equipo después borró ya no cuenta.
        d["sin_resolver"] = sum(1 for r in respuestas
                                if r["decision"] == "cambios" and not r["resuelta"] and r["pagina"] in ep)
        if completo:
            d["estado_paginas"] = ep
            d["respuestas"] = respuestas
    if completo:
        d["paginas"] = paginas
        d["chequeos"] = chequeos(paginas)
        d["estimacion"] = estimar_horas(paginas)
    return d


def crear(db: str, data: dict, quien: str) -> dict:
    brief = validar_brief(data)
    paginas = generar(brief)
    business_id = _business(db, data.get("business_id"))
    ahora = _txt(_ahora())
    conn = _connect(db)
    try:
        pid = conn.execute(
            "INSERT INTO pl_planos (nombre, brief, mapa, token, business_id, creado_por, creado_en, actualizado_en) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (brief["nombre"], json.dumps(brief, ensure_ascii=False), json.dumps(paginas, ensure_ascii=False),
             secrets.token_urlsafe(16), business_id, quien, ahora, ahora)).lastrowid
        conn.commit()
    finally:
        conn.close()
    return detalle(db, pid)


def _business(db: str, valor) -> int | None:
    if valor in (None, ""):
        return None
    try:
        bid = int(valor)
    except (TypeError, ValueError):
        raise NoSePuede("El cliente no existe")
    conn = _connect(db)
    try:
        if not conn.execute("SELECT 1 FROM businesses WHERE id = ?", (bid,)).fetchone():
            raise NoSePuede("El cliente no existe")
    finally:
        conn.close()
    return bid


def listar(db: str) -> list[dict]:
    conn = _connect(db)
    try:
        filas = conn.execute("SELECT * FROM pl_planos ORDER BY actualizado_en DESC, id DESC").fetchall()
        return [_armar(f, _respuestas(conn, f["id"]), completo=False) for f in filas]
    finally:
        conn.close()


def detalle(db: str, pid: int) -> dict:
    conn = _connect(db)
    try:
        f = _fila(conn, pid)
        return _armar(f, _respuestas(conn, pid))
    finally:
        conn.close()


def por_token(db: str, token: str) -> dict | None:
    token = str(token or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token):
        return None
    conn = _connect(db)
    try:
        f = conn.execute("SELECT * FROM pl_planos WHERE token = ?", (token,)).fetchone()
        return _armar(f, _respuestas(conn, f["id"])) if f else None
    finally:
        conn.close()


def guardar_mapa(db: str, pid: int, paginas) -> dict:
    limpio = validar_mapa(paginas)
    conn = _connect(db)
    try:
        _fila(conn, pid)
        conn.execute("UPDATE pl_planos SET mapa = ?, actualizado_en = ? WHERE id = ?",
                     (json.dumps(limpio, ensure_ascii=False), _txt(_ahora()), pid))
        # Una página nueva en un plano aprobado vuelve a esperar al cliente.
        _recalcular_estado(conn, pid)
        conn.commit()
    finally:
        conn.close()
    return detalle(db, pid)


def editar_brief(db: str, pid: int, data: dict, regenerar: bool = False) -> dict:
    """Cambia el brief. Con `regenerar`, rehace el mapa entero desde cero.

    Sin regenerar, el mapa queda como está: si el equipo ya editó textos, un
    cambio en el WhatsApp no tiene por qué borrárselos.
    """
    brief = validar_brief(data)
    business_id = _business(db, data.get("business_id"))
    conn = _connect(db)
    try:
        f = _fila(conn, pid)
        mapa = json.dumps(generar(brief), ensure_ascii=False) if regenerar else f["mapa"]
        conn.execute("UPDATE pl_planos SET nombre = ?, brief = ?, mapa = ?, business_id = ?, actualizado_en = ? WHERE id = ?",
                     (brief["nombre"], json.dumps(brief, ensure_ascii=False), mapa, business_id, _txt(_ahora()), pid))
        conn.commit()
    finally:
        conn.close()
    return detalle(db, pid)


def marcar_enviado(db: str, pid: int) -> dict:
    conn = _connect(db)
    try:
        f = _fila(conn, pid)
        nuevo = f["estado"] if f["estado"] in ("cambios", "aprobado") else "enviado"
        conn.execute("UPDATE pl_planos SET estado = ?, enviado_en = COALESCE(enviado_en, ?) WHERE id = ?",
                     (nuevo, _txt(_ahora()), pid))
        conn.commit()
    finally:
        conn.close()
    return detalle(db, pid)


def resolver_respuesta(db: str, pid: int, rid: int, resuelta: bool = True) -> dict:
    conn = _connect(db)
    try:
        _fila(conn, pid)
        n = conn.execute("UPDATE pl_respuestas SET resuelta = ? WHERE id = ? AND plano_id = ? AND decision = 'cambios'",
                         (1 if resuelta else 0, rid, pid)).rowcount
        if not n:
            raise NoSePuede("El pedido de cambio no existe")
        _recalcular_estado(conn, pid)
        conn.commit()
    finally:
        conn.close()
    return detalle(db, pid)


def _recalcular_estado(conn, pid: int) -> None:
    f = _fila(conn, pid)
    if f["estado"] == "borrador":
        return
    paginas = json.loads(f["mapa"])
    ep = estado_paginas(paginas, _respuestas(conn, pid))
    if ep and all(v == "aprobada" for v in ep.values()):
        conn.execute("UPDATE pl_planos SET estado = 'aprobado', aprobado_en = COALESCE(aprobado_en, ?) WHERE id = ?",
                     (_txt(_ahora()), pid))
    elif any(v == "cambios" for v in ep.values()):
        conn.execute("UPDATE pl_planos SET estado = 'cambios', aprobado_en = NULL WHERE id = ?", (pid,))
    else:
        conn.execute("UPDATE pl_planos SET estado = 'enviado', aprobado_en = NULL WHERE id = ?", (pid,))


def responder(db: str, token: str, pagina: str, decision: str, comentario: str = "", nombre: str = "") -> dict:
    """Lo que hace el cliente desde el link: aprobar una página o pedir cambios."""
    if decision not in ("aprobada", "cambios"):
        raise NoSePuede("Elegí si la aprobás o pedís cambios")
    comentario = str(comentario or "").strip()[:MAX_COMENTARIO]
    if decision == "cambios" and len(comentario) < 3:
        raise NoSePuede("Contanos qué querés cambiar")
    nombre = _limpio(nombre, 80)
    conn = _connect(db)
    try:
        f = conn.execute("SELECT * FROM pl_planos WHERE token = ?", (str(token or ""),)).fetchone()
        if not f:
            raise NoSePuede("El plano no existe")
        if f["estado"] == "borrador":
            raise NoSePuede("Este plano todavía no está listo para revisar")
        claves = [p["clave"] for p in json.loads(f["mapa"])]
        objetivo = claves if pagina == "*" else [pagina]
        if pagina != "*" and pagina not in claves:
            raise NoSePuede("La página no existe")
        if pagina == "*" and decision != "aprobada":
            raise NoSePuede("Los cambios se piden página por página")
        hechas = conn.execute("SELECT COUNT(*) FROM pl_respuestas WHERE plano_id = ?", (f["id"],)).fetchone()[0]
        if hechas + len(objetivo) > MAX_COMENTARIOS:
            raise NoSePuede("Este plano ya recibió demasiadas respuestas. Escribinos por WhatsApp.")
        ahora = _txt(_ahora())
        for clave in objetivo:
            conn.execute("INSERT INTO pl_respuestas (plano_id, pagina, decision, comentario, nombre, creado_en) "
                         "VALUES (?,?,?,?,?,?)", (f["id"], clave, decision, comentario or None, nombre or None, ahora))
        _recalcular_estado(conn, f["id"])
        conn.commit()
        return _armar(_fila(conn, f["id"]), _respuestas(conn, f["id"]))
    finally:
        conn.close()


def borrar(db: str, pid: int) -> None:
    conn = _connect(db)
    try:
        _fila(conn, pid)
        conn.execute("DELETE FROM pl_respuestas WHERE plano_id = ?", (pid,))
        conn.execute("DELETE FROM pl_planos WHERE id = ?", (pid,))
        conn.commit()
    finally:
        conn.close()


def clientes(db: str) -> list:
    conn = _connect(db)
    try:
        return [dict(f) for f in conn.execute(
            "SELECT id, name FROM businesses WHERE crm_status IN ('cerrado','en_desarrollo','finalizado','acepto') "
            "ORDER BY name COLLATE NOCASE LIMIT 300")]
    finally:
        conn.close()


def resumen(planos: list[dict]) -> dict:
    por_estado = {k: 0 for k in ESTADOS}
    for p in planos:
        por_estado[p["estado"]] = por_estado.get(p["estado"], 0) + 1
    return {"total": len(planos), "por_estado": por_estado,
            "sin_resolver": sum(p.get("sin_resolver", 0) for p in planos),
            "horas": round(sum(p["horas"] for p in planos if p["estado"] != "aprobado"), 1)}


def texto_brief(d: dict) -> str:
    """El plano entero en Markdown: para pegarlo en Notion o pasárselo a quien arma."""
    b = d["brief"]
    est = d.get("estimacion") or estimar_horas(d["paginas"])
    lineas = [f"# {d['nombre']} · mapa del sitio", "",
              f"- Tipo: {TIPOS[b['tipo']]} · {FAMILIAS[b['familia']]['nombre']}" + (f" ({b['rubro']})" if b.get("rubro") else ""),
              f"- Objetivo: {OBJETIVOS[b['objetivo']]}"]
    if b.get("dominio"):
        lineas.append(f"- Dominio: {b['dominio']}")
    lineas.append(f"- Armado estimado: {str(est['total']).replace('.', ',')} h ({len(d['paginas'])} páginas)")
    lineas.append("")
    hijos: dict[str, list] = {}
    for p in d["paginas"]:
        if p["padre"]:
            hijos.setdefault(p["padre"], []).append(p)
    lineas.append("## Mapa")
    for p in d["paginas"]:
        if p["padre"]:
            continue
        lineas.append(f"- {p['nombre']} `{p['slug']}`")
        for h in hijos.get(p["clave"], []):
            lineas.append(f"  - {h['nombre']} `{h['slug']}`")
    for p in d["paginas"]:
        lineas += ["", f"## {p['nombre']} `{p['slug']}`",
                   f"- Título SEO: {p['seo']['titulo']}", f"- Descripción: {p['seo']['descripcion']}", f"- H1: {p['h1']}"]
        for s in p["secciones"]:
            lineas += ["", f"### {BLOQUES[s['bloque']]['nombre']}" + (f": {s['titulo']}" if s["titulo"] else "")]
            if s["texto"]:
                lineas.append(s["texto"])
            for it in s["items"]:
                lineas.append(f"- **{it['titulo']}**" + (f": {it['texto']}" if it["texto"] else ""))
            if s["boton"]:
                lineas.append(f"[Botón: {s['boton']}]")
    return "\n".join(lineas) + "\n"


def catalogo() -> dict:
    """Lo que la pantalla necesita para armar los selectores."""
    return {"familias": {k: {"nombre": v["nombre"], "ejemplos": v["ejemplos"], "objetivo": v["objetivo"]}
                         for k, v in FAMILIAS.items()},
            "bloques": {k: {"nombre": v["nombre"], "guia": v["guia"], "horas": v["horas"]} for k, v in BLOQUES.items()},
            "objetivos": OBJETIVOS, "tipos": TIPOS, "tonos": TONOS, "estados": ESTADOS}
