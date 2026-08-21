"""Los rubros que se scrapean y como se le habla a cada uno.

Un rubro vive aca o no existe: este modulo ata en un solo lugar las tres cosas
que tienen que moverse juntas.

1. `queries`  — como se busca en Google Maps. Siempre varias: el mismo negocio
   se anuncia como "escribania", "escribano" o "estudio notarial", y cada
   variante devuelve una lista distinta. Buscar con un solo nombre deja afuera
   la mayor parte del rubro.
2. `familia`  — que se hace a mano en ese rubro. De ahi sale la linea de
   apertura del mail. Agrupar por eso y no por industria es lo que permite
   cubrir 45 rubros sin escribir 45 textos: a una odontologia y a una
   veterinaria les duele lo mismo (la agenda), y a una ferreteria y a una casa
   de repuestos tambien (la lista de precios).
3. `clave`    — el nombre canonico. Es lo que se guarda en `category` y lo que
   despues busca `_linea_de_rubro` en email_service.

La regla que hace que esto no se pudra: si agregas un rubro aca, su familia
tiene que tener linea escrita. Sin linea, el mail cae en la generica, que sirve
de red pero no dice nada especifico y responde bastante peor.
"""

# Las familias son "que se hace a mano", no "de que industria es".
FAMILIAS = {
    "turnos": "La agenda y los recordatorios se llevan a mano.",
    "stock_precios": "El catalogo y la lista de precios se actualizan a mano.",
    "pedidos": "Los pedidos entran por WhatsApp y se pasan a mano al sistema.",
    "expedientes": "Cada caso es una carpeta y el seguimiento vive en la cabeza de alguien.",
    "reservas": "La disponibilidad y las reservas se controlan a mano.",
    "cuotas": "Las cuotas, los vencimientos y los avisos de pago salen uno por uno.",
    "presupuestos": "Cada presupuesto se arma de cero y despues nadie sabe en que quedo.",
}


class Rubro:
    __slots__ = ("clave", "familia", "queries")

    def __init__(self, clave: str, familia: str, queries: list[str]):
        assert familia in FAMILIAS, f"familia desconocida: {familia}"
        self.clave = clave
        self.familia = familia
        self.queries = queries

    def __repr__(self) -> str:
        return f"Rubro({self.clave!r}, {self.familia!r}, {len(self.queries)} queries)"


RUBROS = [
    # ── turnos ────────────────────────────────────────────────────────────────
    Rubro("peluqueria", "turnos",
          ["peluqueria", "barberia", "salon de belleza", "peluqueria unisex"]),
    Rubro("odontologia", "turnos",
          ["odontologia", "dentista", "clinica dental", "consultorio odontologico",
           "implantes dentales", "ortodoncia"]),
    Rubro("veterinaria", "turnos",
          ["veterinaria", "clinica veterinaria", "veterinario"]),
    Rubro("gimnasio", "turnos",
          ["gimnasio", "gym", "crossfit", "centro de entrenamiento", "box de crossfit"]),
    Rubro("estetica", "turnos",
          ["centro de estetica", "depilacion", "cosmetologia", "spa", "manicuria"]),
    Rubro("fisioterapia", "turnos",
          ["fisioterapia", "fisioterapeuta", "kinesiologia", "centro de rehabilitacion"]),
    Rubro("psicologia", "turnos",
          ["psicologo", "consultorio psicologico", "psicoterapia"]),
    Rubro("optica", "turnos",
          ["optica", "oftalmologo", "centro oftalmologico"]),
    Rubro("policlinica", "turnos",
          ["policlinica", "centro medico", "consultorio medico"]),
    Rubro("laboratorio", "turnos",
          ["laboratorio de analisis clinicos", "laboratorio clinico"]),
    Rubro("autoescuela", "turnos",
          ["escuela de conductores", "autoescuela", "clases de manejo"]),

    # ── stock y precios ───────────────────────────────────────────────────────
    Rubro("ferreteria", "stock_precios",
          ["ferreteria", "corralon", "barraca de materiales",
           "materiales de construccion", "tienda de herramientas"]),
    Rubro("repuestos", "stock_precios",
          ["repuestos automotor", "autopartes", "casa de repuestos",
           "repuestos originales"]),
    Rubro("automotora", "stock_precios",
          ["automotora", "concesionaria de autos", "compraventa de autos",
           "venta de autos usados"]),
    Rubro("gomeria", "stock_precios",
          ["gomeria", "neumaticos", "cubiertas"]),
    Rubro("muebleria", "stock_precios",
          ["muebleria", "fabrica de muebles", "muebles a medida"]),
    Rubro("vivero", "stock_precios",
          ["vivero", "garden center", "plantas y jardineria"]),
    Rubro("libreria", "stock_precios",
          ["libreria", "papeleria", "articulos de oficina"]),
    Rubro("informatica", "stock_precios",
          ["casa de computacion", "servicio tecnico de computadoras",
           "venta de computadoras"]),
    Rubro("electrodomesticos", "stock_precios",
          ["electrodomesticos", "casa de electrodomesticos"]),
    Rubro("bicicleteria", "stock_precios",
          ["bicicleteria", "venta de bicicletas"]),
    Rubro("indumentaria", "stock_precios",
          ["tienda de ropa", "boutique", "indumentaria"]),
    Rubro("farmacia", "stock_precios",
          ["farmacia"]),

    # ── pedidos ───────────────────────────────────────────────────────────────
    Rubro("restaurante", "pedidos",
          ["restaurante", "parrillada", "bar restaurante", "cantina"]),
    Rubro("pizzeria", "pedidos",
          ["pizzeria", "rotiseria", "delivery de comida"]),
    Rubro("panaderia", "pedidos",
          ["panaderia", "confiteria", "pasteleria"]),
    Rubro("catering", "pedidos",
          ["catering", "servicio de catering", "empresa de eventos"]),
    Rubro("distribuidora", "pedidos",
          ["distribuidora", "mayorista", "deposito mayorista",
           "distribuidor de alimentos"]),
    Rubro("floreria", "pedidos",
          ["floreria", "venta de flores"]),

    # ── expedientes ───────────────────────────────────────────────────────────
    Rubro("inmobiliaria", "expedientes",
          ["inmobiliaria", "agencia inmobiliaria", "agente inmobiliario",
           "administracion de propiedades", "corredor inmobiliario"]),
    Rubro("contador", "expedientes",
          ["estudio contable", "contador publico", "asesoramiento contable"]),
    Rubro("escribania", "expedientes",
          ["escribania", "escribano", "estudio notarial"]),
    Rubro("abogado", "expedientes",
          ["estudio juridico", "abogado", "asesoramiento legal"]),
    Rubro("seguros", "expedientes",
          ["corredor de seguros", "agencia de seguros", "productor de seguros"]),
    Rubro("despachante", "expedientes",
          ["despachante de aduana", "agencia de carga", "comercio exterior"]),

    # ── reservas ──────────────────────────────────────────────────────────────
    Rubro("hotel", "reservas",
          ["hotel", "hosteria", "posada", "cabanas", "apart hotel"]),
    Rubro("agencia_viajes", "reservas",
          ["agencia de viajes", "turismo receptivo"]),
    Rubro("salon_eventos", "reservas",
          ["salon de fiestas", "salon de eventos", "chacra para eventos"]),

    # ── cuotas ────────────────────────────────────────────────────────────────
    Rubro("colegio", "cuotas",
          ["colegio privado", "instituto educativo", "liceo privado"]),
    Rubro("jardin", "cuotas",
          ["jardin de infantes", "guarderia"]),
    Rubro("academia", "cuotas",
          ["academia de danza", "escuela de musica", "instituto de ingles",
           "academia de idiomas"]),

    # ── presupuestos ──────────────────────────────────────────────────────────
    Rubro("constructora", "presupuestos",
          ["constructora", "empresa constructora", "empresa de obras"]),
    Rubro("arquitectura", "presupuestos",
          ["estudio de arquitectura", "arquitecto"]),
    Rubro("imprenta", "presupuestos",
          ["imprenta", "grafica", "serigrafia", "cartelería"]),
    Rubro("mudanzas", "presupuestos",
          ["empresa de mudanzas", "fletes y mudanzas"]),
    Rubro("climatizacion", "presupuestos",
          ["aire acondicionado instalacion", "refrigeracion service",
           "climatizacion"]),
    Rubro("aberturas", "presupuestos",
          ["aberturas de aluminio", "carpinteria de obra", "herreria"]),
    Rubro("piscinas", "presupuestos",
          ["construccion de piscinas", "mantenimiento de piscinas"]),
]


DEPARTAMENTOS = [
    "Montevideo", "Canelones", "Maldonado", "Colonia", "San Jose",
    "Soriano", "Rio Negro", "Paysandu", "Salto", "Artigas",
    "Rivera", "Tacuarembo", "Cerro Largo", "Treinta y Tres",
    "Rocha", "Lavalleja", "Florida", "Flores", "Durazno",
]


def todas_las_busquedas(rubros=None, departamentos=None):
    """(rubro, texto de busqueda) para cada combinacion rubro x variante x depto.

    Es el producto completo a proposito: Google devuelve listas distintas por
    variante y por zona, y el deduplicado real lo hace el UNIQUE de la base, no
    esta funcion. Pedir de menos aca es lo unico que no se puede arreglar
    despues.
    """
    for r in (rubros if rubros is not None else RUBROS):
        for q in r.queries:
            for d in (departamentos if departamentos is not None else DEPARTAMENTOS):
                yield r, f"{q} {d} Uruguay"


def cuantas_busquedas(rubros=None, departamentos=None) -> int:
    return sum(1 for _ in todas_las_busquedas(rubros, departamentos))


# Google no guarda la clave canonica: dice "Agencia inmobiliaria", "Agentes
# inmobiliarios", "Consultor inmobiliario". La busqueda por clave exacta fallaba
# con todos ellos y el mail salia con la linea generica — los primeros 30 mails
# de discovery salieron asi.
#
# El orden importa: gana el primero que coincide, y por eso lo mas especifico va
# arriba. "Peluqueria canina" tiene que caer en veterinaria, no en peluqueria.
_PALABRAS_POR_RUBRO = [
    (("canin", "mascota", "veterinar"), "veterinaria"),
    (("dentist", "odontolog", "dental", "ortodon"), "odontologia"),
    (("peluquer", "barber", "salon de belleza", "estilista"), "peluqueria"),
    (("gimnasio", "gym", "crossfit", "fitness"), "gimnasio"),
    (("depilacion", "cosmetolog", "estetica", "manicur", "spa"), "estetica"),
    (("fisioterap", "kinesiolog", "rehabilitacion"), "fisioterapia"),
    (("psicolog", "psicoterap"), "psicologia"),
    (("optica", "oftalmolog"), "optica"),
    (("policlinica", "centro medico", "consultorio medico"), "policlinica"),
    (("laboratorio de analisis", "laboratorio clinico"), "laboratorio"),
    (("escuela de conductores", "autoescuela", "clases de manejo"), "autoescuela"),
    (("inmobiliar", "inmueble", "propiedad", "bienes raices"), "inmobiliaria"),
    (("repuesto", "autoparte"), "repuestos"),
    (("concesionar", "automotora", "venta de auto", "compraventa de auto"), "automotora"),
    (("gomeria", "neumatico", "cubierta"), "gomeria"),
    (("ferreter", "corralon", "barraca", "material de construccion",
      "materiales de construccion", "materiales para la construccion",
      "herramienta"), "ferreteria"),
    (("muebler", "fabrica de muebles", "mueble"), "muebleria"),
    (("vivero", "garden", "jardineria"), "vivero"),
    (("librer", "papeler", "articulos de oficina"), "libreria"),
    (("computacion", "computadora", "informatica"), "informatica"),
    (("electrodomestico",), "electrodomesticos"),
    (("bicicleter", "bicicleta"), "bicicleteria"),
    (("farmacia",), "farmacia"),
    (("tienda de ropa", "boutique", "indumentaria"), "indumentaria"),
    (("pizzeria", "rotiseria", "delivery de comida"), "pizzeria"),
    (("panader", "confiter", "pasteler"), "panaderia"),
    (("catering", "empresa de eventos"), "catering"),
    (("distribuidora", "mayorista", "deposito mayorista"), "distribuidora"),
    (("floreria", "venta de flores"), "floreria"),
    (("restaurante", "parrillada", "cantina", "bar "), "restaurante"),
    (("escribania", "escribano", "notarial"), "escribania"),
    (("estudio contable", "contador", "contable"), "contador"),
    (("estudio juridico", "abogado", "asesoramiento legal"), "abogado"),
    (("seguro",), "seguros"),
    (("despachante", "aduana", "comercio exterior"), "despachante"),
    (("hotel", "hosteria", "posada", "cabana", "apart hotel"), "hotel"),
    (("agencia de viajes", "turismo"), "agencia_viajes"),
    (("salon de fiestas", "salon de eventos"), "salon_eventos"),
    (("colegio", "liceo", "instituto educativo"), "colegio"),
    (("jardin de infantes", "guarderia"), "jardin"),
    (("academia", "escuela de musica", "instituto de ingles"), "academia"),
    (("constructora", "empresa de obras"), "constructora"),
    (("arquitect",), "arquitectura"),
    (("imprenta", "grafica", "serigrafia", "carteler"), "imprenta"),
    (("mudanza", "flete"), "mudanzas"),
    (("aire acondicionado", "refrigeracion", "climatizacion"), "climatizacion"),
    (("aberturas", "carpinteria", "herreria"), "aberturas"),
    (("piscina",), "piscinas"),
]

_CLAVES = {r.clave for r in RUBROS}


def _sin_tildes(t: str) -> str:
    return (t.replace("á", "a").replace("é", "e").replace("í", "i")
             .replace("ó", "o").replace("ú", "u").replace("ñ", "n"))


def rubro_de_texto(texto: str) -> str:
    """La clave canonica del rubro que describe `texto`, o "" si no se sabe.

    Adivinar mal es peor que no adivinar: la linea de apertura del rubro
    equivocado afirma algo falso sobre el negocio y se nota. Lo ambiguo
    ("Comercio", "Oficinas de empresa") devuelve "" a proposito y termina en la
    linea generica, que no afirma nada.
    """
    t = _sin_tildes(" ".join((texto or "").lower().split()))
    if not t:
        return ""
    if t in _CLAVES:
        return t
    for palabras, clave in _PALABRAS_POR_RUBRO:
        if any(p in t for p in palabras):
            return clave
    return ""
