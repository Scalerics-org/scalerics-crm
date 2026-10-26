"""Fábrica de asistentes: el bot de WhatsApp de cada comercio, armado acá.

Hoy cada bot de cliente es un repo aparte (edishop-bot, crm-jose): se escribe
el conocimiento a mano en el código, se prueba mandándole mensajes desde un
celular y nadie sabe qué tan bien contesta hasta que un cliente se queja. Este
módulo hace lo que Intercom Fin, Voiceflow y las plataformas de agentes hacen
para las empresas grandes:

- **Fichas de conocimiento**: lo que el asistente sabe del comercio (horario,
  dónde queda, cómo se paga, envíos...), con las formas en que la gente lo
  pregunta. Se arranca de una plantilla por rubro y lo que falta queda marcado.
- **Derivación**: cuándo pasa la charla a una persona (lo pide, es un reclamo,
  o no sabe la respuesta), y qué dice según si el local está abierto o no.
- **Simulador y link de prueba**: el equipo y el dueño del comercio le
  escriben antes de conectarlo.
- **Evaluaciones**: preguntas de prueba con la respuesta esperada. Dan un
  puntaje; con menos de 90 % el asistente no está listo para entregar.
- **Sin respuesta**: lo que preguntaron y no supo contestar. Con un toque se
  convierte en ficha (los "content gaps" de Fin).
- **API**: `POST /asistente/api/<token>/mensaje` con la clave del asistente.
  La llama el puente de WhatsApp de cada cliente (Baileys o la Cloud API) y
  devuelve la respuesta y si hay que derivar.

El motor no usa IA ni red: busca la ficha que mejor cubre la pregunta (BM25
con raíces y sinónimos del español de acá) y contesta con su texto, tal cual
lo escribió el equipo. Así no inventa precios ni promete lo que el comercio no
hace, anda igual en la vista previa y cuesta cero por mensaje. Si la pregunta
no está cubierta, no adivina: deriva y lo anota en "Sin respuesta". Un modelo
de lenguaje puede venir después a redactar mejor sobre la misma ficha; la
estructura y las pruebas son lo que hace que se pueda entregar tranquilo.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone

import pytz

FORMATO = "%Y-%m-%d %H:%M:%S"
MVD = pytz.timezone("America/Montevideo")


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
# La API y el link de prueba los puede llamar cualquiera que tenga el token:
# los topes son para que nadie llene la base ni la use de servicio gratis.

MAX_FICHAS = 120
MAX_PRUEBAS = 150
MAX_VARIANTES = 12
MAX_TITULO = 80
MAX_CONTENIDO = 1200
MAX_MENSAJE = 500
MAX_PALABRAS_DERIVAR = 20
TOPE_API_HORA = 600        # mensajes por hora por asistente desde la API
TOPE_LINK_HORA = 120       # y desde el link de prueba
PUNTAJE_LISTO = 90
MIN_PRUEBAS = 10
MIN_FICHAS = 5
UMBRAL = 0.5               # cobertura mínima de la pregunta para contestar

DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
TONOS = {"vos": "Cercano (vos)", "usted": "Formal (usted)"}
ORIGENES = {"simulador": "Simulador", "link": "Link de prueba", "api": "WhatsApp"}
TIPOS = {"respuesta": "Respondió", "horario": "Horario", "derivar": "Derivó",
         "no_sabe": "No supo", "saludo": "Saludo", "gracias": "Agradecimiento"}
ESPERADOS = {"ficha": "Responde con la ficha", "derivar": "Pasa a una persona", "no_sabe": "Dice que no sabe"}


# ── texto ────────────────────────────────────────────────────────────────────

def _plano(texto: str) -> str:
    t = unicodedata.normalize("NFD", str(texto or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


VACIAS = set("""
a al algo algun alguna algunas alguno algunos ante con contra de del desde el ella ellas ellos en entre era es esa
esas ese eso esos esta estan estas este esto estos fue ha hay la las le les lo los mas me mi mis muy nos o os para
pero por porque que quien se si sin sobre son su sus te tiene tienen tu tus un una uno unos y ya yo usted ustedes
vos como cual cuales cuando ser estar puedo puede pueden podes podria quiero quisiera queria necesito saber
consulta consultar pregunta favor gracias hola buenas buen buenos dias tardes noches holaa ola che bueno
tenes tienes hacen hace dan dar ver mismo tambien aca alli ahi ok dale
""".split())

SALUDOS = set("hola holaa holis ola buenas buen buenos dia dias tarde tardes noche noches que tal como estas "
              "estan va todo bien saludos hey".split())
GRACIAS = set("gracias muchas mil genial perfecto dale ok listo barbaro buenisimo joya bien excelente".split())

# Sinónimos de acá: cada grupo suma un "concepto" a la pregunta y a la ficha,
# así "¿a qué hora abren?" encuentra la ficha "Horario" aunque no comparta
# ninguna palabra. Las claves van sin tildes y en raíz (ver _raiz).
CONCEPTOS = {
    "horario": "horario horarios hora horas abren abre abierto abiertos abierta cierran cierra cerrado cerrados "
               "atienden atencion atendes domingo domingos sabado sabados feriado feriados",
    "ubicacion": "donde direccion ubicados ubicacion ubican quedan queda local llegar llego mapa estan zona barrio",
    "pago": "pago pagos pagar pago tarjeta tarjetas efectivo transferencia mercadopago cuotas debito credito "
            "oca visa master prex abitab redpagos",
    "envio": "envio envios envian enviar delivery domicilio mandan mandar reparto llevan llevar entregan entrega "
             "interior dac correo",
    "reserva": "reserva reservas reservar turno turnos agendar agenda cita citas sacar anotarme lugar mesa",
    "precio": "precio precios cuesta cuestan sale salen valor valores costo costos tarifa tarifas cuanto lista",
    "cambio": "cambio cambios devolucion devoluciones devolver cambiar garantia falla fallado roto",
}
_CONCEPTO_DE: dict[str, str] = {}


def _raiz(p: str) -> str:
    """Una raíz gruesa: alcanza para que plural y singular den lo mismo."""
    if len(p) > 4 and p.endswith("es") and p[-3] not in "aeiou":
        p = p[:-2]
    elif len(p) > 3 and p.endswith("s"):
        p = p[:-1]
    return p[:6] if len(p) > 6 else p


for _c, _ps in CONCEPTOS.items():
    for _p in _ps.split():
        _CONCEPTO_DE[_raiz(_p)] = _c


def palabras(texto: str) -> list[str]:
    return re.findall(r"[a-z0-9ñ]+", _plano(texto))


def unidades(texto: str) -> list[tuple[str, ...]]:
    """Cada palabra útil de la pregunta, con su concepto si tiene: ('abren', '#horario')."""
    out, vistas = [], set()
    for p in palabras(texto):
        if p in VACIAS or len(p) < 2:
            continue
        r = _raiz(p)
        if r in vistas:
            continue
        vistas.add(r)
        out.append((r, "#" + _CONCEPTO_DE[r]) if r in _CONCEPTO_DE else (r,))
    return out


def fichas_de(texto: str) -> list[str]:
    """Los términos con que se indexa y se busca: raíces sin vacías + conceptos."""
    out = []
    for p in palabras(texto):
        if p in VACIAS or len(p) < 2:
            continue
        r = _raiz(p)
        out.append(r)
        if r in _CONCEPTO_DE:
            out.append("#" + _CONCEPTO_DE[r])
    return out


# ── horario ──────────────────────────────────────────────────────────────────

_RANGO = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(?:-|a|–)\s*(\d{1,2})(?::(\d{2}))?$")


def _hhmm(h: str, m: str | None) -> str:
    h, m = int(h), int(m or 0)
    if not (0 <= h <= 24 and 0 <= m < 60) or (h == 24 and m):
        raise NoSePuede("Una hora del horario no existe")
    return f"{h:02d}:{m:02d}"


def leer_dia(texto: str) -> list[list[str]]:
    """'9-13, 15:30-19' -> [['09:00','13:00'], ['15:30','19:00']]. Vacío o 'cerrado' -> []."""
    t = _plano(texto).strip()
    if t in ("", "cerrado", "-", "no"):
        return []
    rangos = []
    for parte in re.split(r"[,;y/]+", t):
        parte = parte.strip().replace("hs", "").replace("h", "").strip()
        if not parte:
            continue
        m = _RANGO.match(parte)
        if not m:
            raise NoSePuede(f"No entiendo el horario «{texto.strip()[:40]}»: escribilo como 9:00-13:00, 15:00-19:00")
        desde, hasta = _hhmm(m.group(1), m.group(2)), _hhmm(m.group(3), m.group(4))
        if hasta <= desde:
            raise NoSePuede(f"En «{texto.strip()[:40]}» la hora de cierre tiene que ser después de la de apertura")
        rangos.append([desde, hasta])
    if len(rangos) > 4:
        raise NoSePuede("Hasta 4 turnos por día")
    return sorted(rangos)


def validar_horario(data) -> dict:
    """{'0': '9-18', ...} o {'0': [['09:00','18:00']]} -> {'0': [[...]], ... '6': []}."""
    if not isinstance(data, dict):
        data = {}
    out = {}
    for i in range(7):
        v = data.get(str(i), data.get(i, ""))
        if isinstance(v, list):
            v = ", ".join(f"{a}-{b}" for a, b in v if isinstance(a, str) and isinstance(b, str))
        out[str(i)] = leer_dia(str(v or ""))
    return out


def _dia_txt(rangos: list) -> str:
    if not rangos:
        return "cerrado"
    return " y ".join(f"{a} a {b}" for a, b in rangos)


def texto_horario(horario: dict) -> str:
    """Agrupa los días con el mismo horario: 'Lunes a viernes de 09:00 a 18:00'."""
    if not horario or not any(horario.get(str(i)) for i in range(7)):
        return ""
    grupos: list[tuple[int, int, str]] = []
    for i in range(7):
        t = _dia_txt(horario.get(str(i), []))
        if grupos and grupos[-1][2] == t:
            grupos[-1] = (grupos[-1][0], i, t)
        else:
            grupos.append((i, i, t))
    lineas = []
    for a, b, t in grupos:
        dias = DIAS[a] if a == b else (f"{DIAS[a]} y {DIAS[b].lower()}" if b == a + 1 else f"{DIAS[a]} a {DIAS[b].lower()}")
        lineas.append(f"{dias}: {t}" if t == "cerrado" else f"{dias} de {t}")
    return "\n".join(lineas)


def _local(ahora: datetime | None) -> datetime:
    """Hora de pared de Montevideo. `ahora` es UTC sin zona (como el resto del CRM)."""
    ahora = ahora or _ahora()
    return pytz.utc.localize(ahora).astimezone(MVD).replace(tzinfo=None)


def estado_local(horario: dict, ahora: datetime | None = None) -> dict:
    """¿Está abierto ahora? Y si no, cuándo abre. Todo en hora de Montevideo."""
    if not horario or not any(horario.get(str(i)) for i in range(7)):
        return {"conocido": False, "abierto": False, "texto": ""}
    loc = _local(ahora)
    hm = loc.strftime("%H:%M")
    hoy = horario.get(str(loc.weekday()), [])
    for a, b in hoy:
        if a <= hm < b:
            return {"conocido": True, "abierto": True, "texto": f"Ahora estamos abiertos, hasta las {b}."}
    for extra in range(0, 8):
        d = (loc.weekday() + extra) % 7
        for a, _b in horario.get(str(d), []):
            if extra == 0 and a <= hm:
                continue
            cuando = "hoy" if extra == 0 else ("mañana" if extra == 1 else f"el {DIAS[d].lower()}")
            return {"conocido": True, "abierto": False, "texto": f"Ahora estamos cerrados: abrimos {cuando} a las {a}."}
    return {"conocido": True, "abierto": False, "texto": "Ahora estamos cerrados."}


# ── plantillas por rubro ─────────────────────────────────────────────────────
# Lo que pregunta la gente en cada tipo de comercio. El contenido arranca como
# PENDIENTE: una ficha sin completar no se usa para contestar (mejor derivar
# que decir "[completar]" a un cliente), y cuenta en los chequeos.

PENDIENTE = "[Completar]"

_COMUNES = [
    ("horario", "Horario", "¿a qué hora abren?\n¿están abiertos hoy?\n¿abren los domingos?\n¿hasta qué hora atienden?", ""),
    ("ubicacion", "Dónde estamos", "¿dónde quedan?\n¿cuál es la dirección?\n¿cómo llego?",
     PENDIENTE + " Dirección, barrio y una referencia para llegar."),
    ("pago", "Formas de pago", "¿aceptan tarjeta?\n¿puedo pagar con transferencia?\n¿hacen cuotas?",
     PENDIENTE + " Efectivo, débito, crédito (¿en cuántas cuotas?), transferencia, Mercado Pago."),
    ("precios", "Precios", "¿cuánto sale?\n¿me pasás la lista de precios?\n¿qué precio tiene?",
     PENDIENTE + " Precios o rangos de lo más pedido, o dónde ver la lista."),
]

FAMILIAS = {
    "turnos": {"nombre": "Turnos (peluquería, clínica, estética)", "fichas": [
        ("reserva", "Reservar un turno", "quiero sacar un turno\n¿tienen lugar para mañana?\n¿cómo reservo?",
         PENDIENTE + " Cómo se reserva (link, por acá, por teléfono) y con cuánta anticipación."),
        ("cancelar", "Cambiar o cancelar un turno", "necesito cancelar mi turno\n¿puedo cambiar la hora?\nno voy a poder ir",
         PENDIENTE + " Hasta cuándo se puede cancelar y si se cobra seña."),
        ("servicios", "Servicios", "¿qué servicios hacen?\n¿hacen tratamientos?\n¿qué ofrecen?",
         PENDIENTE + " La lista de servicios con su duración."),
    ]},
    "gastronomia": {"nombre": "Gastronomía (restaurante, pizzería, rotisería)", "fichas": [
        ("envio", "Delivery y zonas", "¿hacen delivery?\n¿llegan a mi barrio?\n¿cuánto sale el envío?",
         PENDIENTE + " Barrios a los que llegan, costo de envío y demora."),
        ("carta", "La carta", "¿me pasás el menú?\n¿qué tienen hoy?\n¿tienen la carta?",
         PENDIENTE + " Link a la carta o los platos más pedidos con precio."),
        ("reserva", "Reservar mesa", "quiero reservar una mesa\n¿tienen lugar para 6 el sábado?\n¿se puede reservar?",
         PENDIENTE + " Si se reserva, cómo y para cuántas personas."),
        ("dietas", "Vegetarianos y celíacos", "¿tienen opciones sin tacc?\n¿hay algo vegetariano?\n¿tienen opción vegana?",
         PENDIENTE + " Opciones aptas y cuáles."),
    ]},
    "comercio": {"nombre": "Comercio o tienda", "fichas": [
        ("envio", "Envíos", "¿hacen envíos al interior?\n¿mandan a domicilio?\n¿cuánto demora el envío?",
         PENDIENTE + " A dónde envían, por qué agencia, costo y demora."),
        ("stock", "Stock", "¿tienen stock?\n¿les queda en talle M?\n¿cuándo les vuelve a entrar?",
         PENDIENTE + " Cómo consultar stock y cada cuánto reponen."),
        ("cambio", "Cambios y devoluciones", "¿puedo cambiar un producto?\n¿hacen devoluciones?\n¿tiene garantía?",
         PENDIENTE + " Plazo para cambiar, con qué condiciones, y la garantía."),
    ]},
    "profesional": {"nombre": "Profesional (estudio, inmobiliaria, consultora)", "fichas": [
        ("primera", "Primera consulta", "¿cuánto sale la consulta?\n¿cómo agendo una reunión?\n¿la primera consulta se cobra?",
         PENDIENTE + " Cómo es la primera consulta, cuánto dura y si se cobra."),
        ("documentos", "Qué hay que traer", "¿qué documentación necesito?\n¿qué tengo que llevar?\n¿qué papeles piden?",
         PENDIENTE + " La documentación para los trámites más comunes."),
        ("online", "Atención a distancia", "¿atienden por videollamada?\n¿se puede hacer online?\n¿atienden en el interior?",
         PENDIENTE + " Si se puede hacer todo a distancia y cómo."),
    ]},
    "oficios": {"nombre": "Oficios (instalaciones, reparaciones, service)", "fichas": [
        ("zona", "Zonas que cubrimos", "¿vienen a mi barrio?\n¿trabajan en la costa?\n¿hasta dónde van?",
         PENDIENTE + " Zonas que cubren y si cobran traslado."),
        ("presupuesto", "Presupuesto", "¿el presupuesto tiene costo?\n¿me pueden pasar un presupuesto?\n¿cuánto me saldría?",
         PENDIENTE + " Si la visita para presupuestar se cobra y qué datos necesitan."),
        ("urgencia", "Urgencias", "¿atienden urgencias?\n¿pueden venir hoy?\n¿trabajan los fines de semana?",
         PENDIENTE + " Si atienden urgencias, en qué horario y con qué recargo."),
        ("garantia", "Garantía del trabajo", "¿el trabajo tiene garantía?\n¿qué pasa si vuelve a fallar?",
         PENDIENTE + " Cuánto dura la garantía y qué cubre."),
    ]},
    "general": {"nombre": "Otro", "fichas": []},
}

# Lo que dispara la derivación aunque haya una ficha que conteste. Se suman a
# las palabras que cargue el equipo para ese comercio.
DERIVAR_SIEMPRE = [
    "hablar con una persona", "hablar con alguien", "hablar con un humano", "con una persona", "un humano",
    "atencion al cliente", "un asesor", "una asesora", "quiero hablar con", "me atiende alguien", "me llamen",
    "reclamo", "queja", "denuncia", "estafa", "me cobraron mal", "no me llego", "no llego mi pedido",
]


# ── validación ───────────────────────────────────────────────────────────────

def _limpio(v, largo: int) -> str:
    return re.sub(r"[ \t]+", " ", str(v or "")).strip()[:largo]


def _lineas(v, maximo: int, largo: int) -> list[str]:
    if isinstance(v, list):
        partes = [str(x) for x in v]
    else:
        partes = str(v or "").splitlines()
    out, vistas = [], set()
    for p in partes:
        p = _limpio(p, largo)
        if p and _plano(p) not in vistas:
            vistas.add(_plano(p))
            out.append(p)
    return out[:maximo]


def validar_config(data: dict, previo: dict | None = None) -> dict:
    previo = previo or {}

    def tomar(k, defecto=""):
        return data[k] if k in data else previo.get(k, defecto)

    nombre = _limpio(tomar("nombre"), 80)
    if not nombre:
        raise NoSePuede("Falta el nombre del comercio")
    familia = tomar("familia", "general") or "general"
    if familia not in FAMILIAS:
        raise NoSePuede("Ese tipo de comercio no existe")
    tono = tomar("tono", "vos") or "vos"
    if tono not in TONOS:
        raise NoSePuede("El tono es vos o usted")
    humano = _limpio(tomar("humano"), 60)
    derivar = _lineas(tomar("palabras_derivar", []), MAX_PALABRAS_DERIVAR, 60)
    return {
        "nombre": nombre,
        "familia": familia,
        "tono": tono,
        "humano": humano,
        "bienvenida": _limpio(tomar("bienvenida"), 300),
        "palabras_derivar": derivar,
        "horario": validar_horario(tomar("horario", {})),
    }


def validar_ficha(data: dict) -> dict:
    titulo = _limpio(data.get("titulo"), MAX_TITULO)
    if not titulo:
        raise NoSePuede("La ficha necesita un título")
    contenido = str(data.get("contenido") or "").strip()[:MAX_CONTENIDO]
    contenido = re.sub(r"\n{3,}", "\n\n", contenido)
    variantes = _lineas(data.get("variantes"), MAX_VARIANTES, 160)
    clave = _limpio(data.get("clave"), 30)
    if clave != "horario" and not contenido:
        raise NoSePuede("La ficha necesita el texto de la respuesta")
    return {"titulo": titulo, "contenido": contenido, "variantes": variantes, "clave": clave,
            "activa": bool(data.get("activa", True))}


def pendiente(ficha: dict, horario: dict | None = None) -> bool:
    if ficha["clave"] == "horario":
        return not texto_horario(horario or {}) and not ficha["contenido"]
    return PENDIENTE.lower() in ficha["contenido"].lower()


# ── el motor ─────────────────────────────────────────────────────────────────

class Indice:
    """BM25 sobre las fichas que se pueden usar (activas y completas).

    El título y las variantes pesan doble: son cómo lo pregunta la gente. La
    confianza no es el puntaje BM25 (que no tiene techo) sino cuánto de la
    pregunta cubre la ficha, pesado por lo raro de cada término. Un término
    que no aparece en ninguna ficha pesa como el más raro: una pregunta sobre
    algo que el comercio no cargó baja sola de confianza.
    """

    K1, B = 1.4, 0.6

    def __init__(self, fichas: list[dict]):
        self.fichas = fichas
        self.docs = []
        for f in fichas:
            pregunta = f["titulo"] + "\n" + "\n".join(f["variantes"])
            self.docs.append(fichas_de(pregunta) * 2 + fichas_de(f["contenido"]))
        self.n = len(self.docs)
        self.prom = (sum(len(d) for d in self.docs) / self.n) if self.n else 1
        df: dict[str, int] = {}
        for d in self.docs:
            for t in set(d):
                df[t] = df.get(t, 0) + 1
        self.idf = {t: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.idf_max = math.log(1 + (self.n + 0.5) / 0.5) if self.n else 1.0

    def buscar(self, texto: str) -> tuple[dict | None, float]:
        q = list(dict.fromkeys(fichas_de(texto)))
        if not q or not self.n:
            return None, 0.0
        mejor, mejor_s, mejor_i = None, 0.0, 0
        for i, d in enumerate(self.docs):
            s = 0.0
            for t in q:
                tf = d.count(t)
                if tf:
                    idf = self.idf.get(t, 0.0)
                    s += idf * tf * (self.K1 + 1) / (tf + self.K1 * (1 - self.B + self.B * len(d) / self.prom))
            if s > mejor_s:
                mejor, mejor_s, mejor_i = self.fichas[i], s, i
        if not mejor:
            return None, 0.0
        # Cada palabra de la pregunta cuenta una vez, cubierta por su raíz o
        # por su concepto, y pesa lo que pesa su término más raro.
        doc = set(self.docs[mejor_i])
        total = cubierto = 0.0
        for u in unidades(texto):
            peso = max(max(self.idf.get(t, self.idf_max) for t in u), 0.3)
            total += peso
            if any(t in doc for t in u):
                cubierto += peso
        if not total:
            return None, 0.0
        return mejor, round(min(1.0, cubierto / total), 3)


def _hay(texto_plano: str, frase: str) -> bool:
    return re.search(r"(?<![a-z0-9ñ])" + re.escape(_plano(frase)) + r"(?![a-z0-9ñ])", texto_plano) is not None


def _usted(texto: str) -> str:
    """Pasa las frases fijas del bot a usted. Las fichas las escribe el equipo en su tono."""
    for a, b in (("te paso", "le paso"), ("Te paso", "Le paso"), ("te escribe", "le escribe"),
                 ("te contestamos", "le contestamos"), ("te puedo", "le puedo"), ("¿En qué te", "¿En qué le"),
                 ("contame", "cuénteme"), ("Contame", "Cuénteme"), ("escribinos", "escríbanos"),
                 ("tenés", "tiene"), ("Tenés", "Tiene"), ("querés", "quiere"), ("necesitás", "necesita")):
        texto = texto.replace(a, b)
    return texto


def responder(cfg: dict, fichas: list[dict], texto: str, primero: bool = True,
              ahora: datetime | None = None, indice: Indice | None = None) -> dict:
    """La respuesta del asistente a un mensaje. Pura: no lee ni escribe la base.

    Orden: pedir una persona o reclamo -> derivar; solo saludo o gracias ->
    contestar eso; pregunta por el horario -> el horario y si está abierto
    ahora; si no, la ficha que mejor cubra la pregunta, y si ninguna la cubre
    lo bastante, derivar avisando que no sabe.
    """
    texto = str(texto or "").strip()[:MAX_MENSAJE]
    tp = _plano(texto)
    horario = cfg.get("horario") or {}
    estado = estado_local(horario, ahora)
    humano = cfg.get("humano") or "alguien del equipo"
    usables = [f for f in fichas if f["activa"] and not pendiente(f, horario)]
    idx = indice or Indice(usables)
    # Las frases fijas del bot siguen el tono; las fichas y el saludo propio
    # los escribe el equipo y salen tal cual.
    t = _usted if cfg.get("tono") == "usted" else (lambda x: x)
    saludo = ""
    if primero:
        saludo = cfg.get("bienvenida") or f"¡Hola! Soy el asistente de {cfg['nombre']}."
        saludo += "\n\n"

    def pasar(motivo: str) -> str:
        if estado["conocido"] and not estado["abierto"]:
            return t(f"{motivo} Te paso con {humano}. {estado['texto']} Te contestamos apenas abramos.")
        return t(f"{motivo} Te paso con {humano}, que te escribe por acá en un rato.")

    def salida(tipo, respuesta, ficha=None, confianza=0.0, derivar=False):
        return {"tipo": tipo, "respuesta": respuesta, "ficha_id": ficha["id"] if ficha else None,
                "ficha": ficha["titulo"] if ficha else "", "confianza": confianza, "derivar": derivar}

    if not texto:
        return salida("no_sabe", saludo + t("¿En qué te puedo ayudar?"))

    frases = DERIVAR_SIEMPRE + list(cfg.get("palabras_derivar") or [])
    if any(_hay(tp, f) for f in frases):
        return salida("derivar", saludo + pasar("Entendido."), derivar=True, confianza=1.0)

    ps = palabras(texto)
    if ps and all(p in SALUDOS for p in ps):
        return salida("saludo", (saludo or "¡Hola! ") + t("¿En qué te puedo ayudar? Contame qué necesitás."))
    if ps and all(p in GRACIAS or p in SALUDOS for p in ps) and any(p in GRACIAS for p in ps):
        return salida("gracias", t("¡De nada! Si necesitás algo más, escribinos por acá."))

    ficha, conf = idx.buscar(texto)
    terminos = fichas_de(texto)
    pide_horario = "#horario" in terminos and texto_horario(horario)
    if pide_horario and (not ficha or conf < UMBRAL or ficha["clave"] == "horario"):
        h = next((f for f in usables if f["clave"] == "horario"), None)
        extra = ("\n\n" + h["contenido"]) if h and h["contenido"] else ""
        return salida("horario", saludo + f"Nuestro horario:\n{texto_horario(horario)}\n\n{estado['texto']}{extra}",
                      ficha=h, confianza=1.0)

    if ficha and conf >= UMBRAL:
        cuerpo = ficha["contenido"]
        if ficha["clave"] == "horario" and texto_horario(horario):
            cuerpo = f"Nuestro horario:\n{texto_horario(horario)}\n\n{estado['texto']}" + (
                "\n\n" + cuerpo if cuerpo else "")
        return salida("respuesta", saludo + cuerpo, ficha=ficha, confianza=conf)

    return salida("no_sabe", saludo + pasar("Eso no lo sé responder con seguridad."), ficha=None,
                  confianza=conf, derivar=True)


# ── base ─────────────────────────────────────────────────────────────────────

def init_asistentes(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS as_asistentes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            config TEXT NOT NULL,
            token TEXT NOT NULL UNIQUE,
            clave_hash TEXT,
            conectado INTEGER NOT NULL DEFAULT 0,
            business_id INTEGER,
            creado_por TEXT,
            creado_en TEXT NOT NULL,
            cambiado_en TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS as_fichas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asistente_id INTEGER NOT NULL,
            clave TEXT NOT NULL DEFAULT '',
            titulo TEXT NOT NULL,
            contenido TEXT NOT NULL DEFAULT '',
            variantes TEXT NOT NULL DEFAULT '[]',
            activa INTEGER NOT NULL DEFAULT 1,
            usos INTEGER NOT NULL DEFAULT 0,
            cambiada_en TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_as_fichas ON as_fichas(asistente_id);
        CREATE TABLE IF NOT EXISTS as_pruebas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asistente_id INTEGER NOT NULL,
            pregunta TEXT NOT NULL,
            esperado TEXT NOT NULL,
            ficha_id INTEGER,
            ok INTEGER,
            obtenido TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS ix_as_pruebas ON as_pruebas(asistente_id);
        CREATE TABLE IF NOT EXISTS as_corridas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asistente_id INTEGER NOT NULL,
            total INTEGER NOT NULL,
            bien INTEGER NOT NULL,
            puntaje INTEGER NOT NULL,
            creado_en TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_as_corridas ON as_corridas(asistente_id);
        -- `conversacion` es un hash, nunca el teléfono: el número del cliente
        -- del comercio no se guarda.
        CREATE TABLE IF NOT EXISTS as_mensajes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asistente_id INTEGER NOT NULL,
            origen TEXT NOT NULL,
            conversacion TEXT NOT NULL,
            texto TEXT NOT NULL,
            respuesta TEXT NOT NULL,
            tipo TEXT NOT NULL,
            ficha_id INTEGER,
            confianza REAL NOT NULL DEFAULT 0,
            resuelto INTEGER NOT NULL DEFAULT 0,
            creado_en TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_as_mensajes ON as_mensajes(asistente_id, creado_en);
    """)


def _fila(conn, aid: int) -> sqlite3.Row:
    f = conn.execute("SELECT * FROM as_asistentes WHERE id = ?", (aid,)).fetchone()
    if not f:
        raise NoSePuede("Ese asistente no existe")
    return f


def _ficha_dict(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "clave": r["clave"], "titulo": r["titulo"], "contenido": r["contenido"],
            "variantes": json.loads(r["variantes"] or "[]"), "activa": bool(r["activa"]), "usos": r["usos"],
            "cambiada_en": r["cambiada_en"]}


def _fichas(conn, aid: int) -> list[dict]:
    return [_ficha_dict(r) for r in conn.execute(
        "SELECT * FROM as_fichas WHERE asistente_id = ? ORDER BY id", (aid,))]


def _cfg(f: sqlite3.Row) -> dict:
    cfg = json.loads(f["config"])
    cfg["nombre"] = f["nombre"]
    return cfg


def _tocar(conn, aid: int) -> None:
    conn.execute("UPDATE as_asistentes SET cambiado_en = ? WHERE id = ?", (_txt(_ahora()), aid))


def crear(db: str, data: dict, quien: str) -> dict:
    cfg = validar_config(data)
    ahora = _txt(_ahora())
    conn = _connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO as_asistentes (nombre, config, token, business_id, creado_por, creado_en, cambiado_en) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cfg["nombre"], json.dumps({k: v for k, v in cfg.items() if k != "nombre"}, ensure_ascii=False),
             secrets.token_urlsafe(16), _business(conn, data.get("business_id")), quien, ahora, ahora))
        aid = cur.lastrowid
        for clave, titulo, variantes, contenido in _COMUNES + FAMILIAS[cfg["familia"]]["fichas"]:
            conn.execute("INSERT INTO as_fichas (asistente_id, clave, titulo, contenido, variantes, cambiada_en) "
                         "VALUES (?, ?, ?, ?, ?, ?)",
                         (aid, clave, titulo, contenido, json.dumps(variantes.split("\n"), ensure_ascii=False), ahora))
        conn.commit()
    finally:
        conn.close()
    return detalle(db, aid)


def _business(conn, valor) -> int | None:
    if valor in (None, "", 0, "0"):
        return None
    try:
        bid = int(valor)
    except (TypeError, ValueError):
        raise NoSePuede("Ese cliente no existe")
    try:
        ok = conn.execute("SELECT 1 FROM businesses WHERE id = ?", (bid,)).fetchone()
    except sqlite3.OperationalError:
        ok = None
    if not ok:
        raise NoSePuede("Ese cliente no existe")
    return bid


def editar(db: str, aid: int, data: dict) -> dict:
    conn = _connect(db)
    try:
        f = _fila(conn, aid)
        cfg = validar_config(data, _cfg(f))
        bid = _business(conn, data["business_id"]) if "business_id" in data else f["business_id"]
        conn.execute("UPDATE as_asistentes SET nombre = ?, config = ?, business_id = ? WHERE id = ?",
                     (cfg["nombre"], json.dumps({k: v for k, v in cfg.items() if k != "nombre"}, ensure_ascii=False),
                      bid, aid))
        _tocar(conn, aid)
        conn.commit()
    finally:
        conn.close()
    return detalle(db, aid)


def guardar_ficha(db: str, aid: int, data: dict, fid: int | None = None) -> dict:
    ficha = validar_ficha(data)
    ahora = _txt(_ahora())
    conn = _connect(db)
    try:
        _fila(conn, aid)
        if fid is None:
            n = conn.execute("SELECT COUNT(*) FROM as_fichas WHERE asistente_id = ?", (aid,)).fetchone()[0]
            if n >= MAX_FICHAS:
                raise NoSePuede(f"Hasta {MAX_FICHAS} fichas por asistente")
            fid = conn.execute(
                "INSERT INTO as_fichas (asistente_id, clave, titulo, contenido, variantes, activa, cambiada_en) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (aid, ficha["clave"], ficha["titulo"], ficha["contenido"],
                 json.dumps(ficha["variantes"], ensure_ascii=False), int(ficha["activa"]), ahora)).lastrowid
        else:
            previa = conn.execute("SELECT clave FROM as_fichas WHERE id = ? AND asistente_id = ?", (fid, aid)).fetchone()
            if not previa:
                raise NoSePuede("Esa ficha no existe")
            if previa["clave"] == "horario":
                ficha["clave"] = "horario"   # la del horario no deja de serlo desde la pantalla
            conn.execute("UPDATE as_fichas SET clave = ?, titulo = ?, contenido = ?, variantes = ?, activa = ?, "
                         "cambiada_en = ? WHERE id = ?",
                         (ficha["clave"], ficha["titulo"], ficha["contenido"],
                          json.dumps(ficha["variantes"], ensure_ascii=False), int(ficha["activa"]), ahora, fid))
        _tocar(conn, aid)
        conn.commit()
    finally:
        conn.close()
    return detalle(db, aid)


def borrar_ficha(db: str, aid: int, fid: int) -> dict:
    conn = _connect(db)
    try:
        _fila(conn, aid)
        if not conn.execute("DELETE FROM as_fichas WHERE id = ? AND asistente_id = ?", (fid, aid)).rowcount:
            raise NoSePuede("Esa ficha no existe")
        # Las pruebas que esperaban esa ficha quedan sin ficha: se ven en rojo
        # en la próxima corrida, que es lo correcto.
        conn.execute("UPDATE as_pruebas SET ficha_id = NULL WHERE asistente_id = ? AND ficha_id = ?", (aid, fid))
        _tocar(conn, aid)
        conn.commit()
    finally:
        conn.close()
    return detalle(db, aid)


# ── pruebas ──────────────────────────────────────────────────────────────────

def _validar_prueba(conn, aid: int, data: dict) -> tuple[str, str, int | None]:
    pregunta = _limpio(data.get("pregunta"), 300)
    if not pregunta:
        raise NoSePuede("Falta la pregunta de la prueba")
    esperado = data.get("esperado") or "ficha"
    if esperado not in ESPERADOS:
        raise NoSePuede("Lo esperado es una ficha, derivar o no saber")
    fid = None
    if esperado == "ficha":
        try:
            fid = int(data.get("ficha_id") or 0)
        except (TypeError, ValueError):
            fid = 0
        if not conn.execute("SELECT 1 FROM as_fichas WHERE id = ? AND asistente_id = ?", (fid, aid)).fetchone():
            raise NoSePuede("Elegí la ficha con la que tiene que responder")
    return pregunta, esperado, fid


def sumar_prueba(db: str, aid: int, data: dict) -> dict:
    conn = _connect(db)
    try:
        _fila(conn, aid)
        if conn.execute("SELECT COUNT(*) FROM as_pruebas WHERE asistente_id = ?", (aid,)).fetchone()[0] >= MAX_PRUEBAS:
            raise NoSePuede(f"Hasta {MAX_PRUEBAS} pruebas por asistente")
        pregunta, esperado, fid = _validar_prueba(conn, aid, data)
        conn.execute("INSERT INTO as_pruebas (asistente_id, pregunta, esperado, ficha_id) VALUES (?, ?, ?, ?)",
                     (aid, pregunta, esperado, fid))
        conn.commit()
    finally:
        conn.close()
    return detalle(db, aid)


def borrar_prueba(db: str, aid: int, pid: int) -> dict:
    conn = _connect(db)
    try:
        if not conn.execute("DELETE FROM as_pruebas WHERE id = ? AND asistente_id = ?", (pid, aid)).rowcount:
            raise NoSePuede("Esa prueba no existe")
        conn.commit()
    finally:
        conn.close()
    return detalle(db, aid)


# Pruebas que todo asistente tiene que pasar, sea del rubro que sea.
PRUEBAS_BASE = [
    ("Quiero hablar con una persona", "derivar"),
    ("Tengo un reclamo por una compra", "derivar"),
    ("¿Venden autos 0 km?", "no_sabe"),
    ("¿Cuál es la capital de Australia?", "no_sabe"),
]


def sugerir_pruebas(db: str, aid: int) -> int:
    """Suma pruebas a partir de las fichas completas (una por variante, la
    pregunta de otra forma) y las básicas de derivación. No repite."""
    conn = _connect(db)
    try:
        f = _fila(conn, aid)
        horario = _cfg(f).get("horario") or {}
        ya = {_plano(r["pregunta"]) for r in conn.execute("SELECT pregunta FROM as_pruebas WHERE asistente_id = ?", (aid,))}
        nuevas = []
        for fi in _fichas(conn, aid):
            if not fi["activa"] or pendiente(fi, horario):
                continue
            for v in fi["variantes"][:3]:
                nuevas.append((v, "ficha", fi["id"]))
        nuevas += [(p, e, None) for p, e in PRUEBAS_BASE]
        lugar = MAX_PRUEBAS - conn.execute("SELECT COUNT(*) FROM as_pruebas WHERE asistente_id = ?", (aid,)).fetchone()[0]
        n = 0
        for pregunta, esperado, fid in nuevas:
            if n >= lugar or _plano(pregunta) in ya:
                continue
            ya.add(_plano(pregunta))
            conn.execute("INSERT INTO as_pruebas (asistente_id, pregunta, esperado, ficha_id) VALUES (?, ?, ?, ?)",
                         (aid, pregunta, esperado, fid))
            n += 1
        conn.commit()
        return n
    finally:
        conn.close()


def _paso(prueba: dict, r: dict) -> bool:
    if prueba["esperado"] == "derivar":
        return r["tipo"] == "derivar"
    if prueba["esperado"] == "no_sabe":
        return r["tipo"] == "no_sabe"
    return r["ficha_id"] is not None and r["ficha_id"] == prueba["ficha_id"] and r["tipo"] in ("respuesta", "horario")


def correr_pruebas(db: str, aid: int, ahora: datetime | None = None) -> dict:
    conn = _connect(db)
    try:
        f = _fila(conn, aid)
        cfg = _cfg(f)
        fichas = _fichas(conn, aid)
        horario = cfg.get("horario") or {}
        idx = Indice([x for x in fichas if x["activa"] and not pendiente(x, horario)])
        pruebas = [dict(r) for r in conn.execute("SELECT * FROM as_pruebas WHERE asistente_id = ? ORDER BY id", (aid,))]
        if not pruebas:
            raise NoSePuede("Todavía no hay pruebas: sumá algunas o tocá «Sugerir pruebas»")
        bien = 0
        for p in pruebas:
            r = responder(cfg, fichas, p["pregunta"], primero=False, ahora=ahora, indice=idx)
            ok = _paso(p, r)
            bien += ok
            obtenido = r["ficha"] if r["tipo"] in ("respuesta", "horario") and r["ficha"] else TIPOS[r["tipo"]]
            conn.execute("UPDATE as_pruebas SET ok = ?, obtenido = ? WHERE id = ?", (int(ok), obtenido[:120], p["id"]))
        puntaje = round(100 * bien / len(pruebas))
        conn.execute("INSERT INTO as_corridas (asistente_id, total, bien, puntaje, creado_en) VALUES (?, ?, ?, ?, ?)",
                     (aid, len(pruebas), bien, puntaje, _txt(_ahora())))
        conn.commit()
    finally:
        conn.close()
    return detalle(db, aid)


# ── conversaciones ───────────────────────────────────────────────────────────

def _conversacion(origen: str, quien: str) -> str:
    return hashlib.sha256(f"{origen}:{quien}".encode()).hexdigest()[:20]


def conversar(db: str, aid: int, texto: str, origen: str, quien: str, ahora: datetime | None = None,
              tope: int | None = None) -> dict:
    """Contesta y lo anota. `quien` identifica la charla (sesión, teléfono): se
    guarda solo su hash."""
    if origen not in ORIGENES:
        raise NoSePuede("Origen desconocido")
    texto = str(texto or "").strip()
    if not texto:
        raise NoSePuede("El mensaje está vacío")
    if len(texto) > MAX_MENSAJE:
        raise NoSePuede(f"El mensaje es muy largo (hasta {MAX_MENSAJE} letras)")
    conv = _conversacion(origen, quien)
    momento = ahora or _ahora()
    conn = _connect(db)
    try:
        f = _fila(conn, aid)
        if tope is not None:
            hace_hora = _txt(momento - timedelta(hours=1))
            n = conn.execute("SELECT COUNT(*) FROM as_mensajes WHERE asistente_id = ? AND origen = ? AND creado_en > ?",
                             (aid, origen, hace_hora)).fetchone()[0]
            if n >= tope:
                raise NoSePuede("Demasiados mensajes en la última hora. Probá más tarde.")
        # "Primero" = no le escribió en las últimas 12 horas: vuelve a saludar.
        previo = conn.execute("SELECT 1 FROM as_mensajes WHERE asistente_id = ? AND conversacion = ? AND creado_en > ?",
                              (aid, conv, _txt(momento - timedelta(hours=12)))).fetchone()
        r = responder(_cfg(f), _fichas(conn, aid), texto, primero=not previo, ahora=ahora)
        conn.execute("INSERT INTO as_mensajes (asistente_id, origen, conversacion, texto, respuesta, tipo, ficha_id, "
                     "confianza, creado_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (aid, origen, conv, texto, r["respuesta"], r["tipo"], r["ficha_id"], r["confianza"], _txt(momento)))
        if r["ficha_id"]:
            conn.execute("UPDATE as_fichas SET usos = usos + 1 WHERE id = ?", (r["ficha_id"],))
        conn.commit()
    finally:
        conn.close()
    r["conversacion"] = conv
    return r


def historial(db: str, aid: int, origen: str, quien: str, limite: int = 30) -> list[dict]:
    conn = _connect(db)
    try:
        filas = conn.execute("SELECT texto, respuesta, tipo, creado_en FROM as_mensajes WHERE asistente_id = ? "
                             "AND conversacion = ? ORDER BY id DESC LIMIT ?",
                             (aid, _conversacion(origen, quien), limite)).fetchall()
    finally:
        conn.close()
    out = []
    for x in reversed(filas):
        d = dict(x)
        d["hora"] = _local(datetime.strptime(d["creado_en"], FORMATO)).strftime("%H:%M")
        out.append(d)
    return out


def resolver_mensaje(db: str, aid: int, mid: int) -> dict:
    conn = _connect(db)
    try:
        if not conn.execute("UPDATE as_mensajes SET resuelto = 1 WHERE id = ? AND asistente_id = ?", (mid, aid)).rowcount:
            raise NoSePuede("Ese mensaje no existe")
        conn.commit()
    finally:
        conn.close()
    return detalle(db, aid)


def ficha_desde_mensaje(db: str, aid: int, mid: int, data: dict) -> dict:
    """Lo que no supo contestar se vuelve una ficha (con la pregunta como
    variante) y una prueba que la espera, para que no vuelva a fallar."""
    conn = _connect(db)
    try:
        m = conn.execute("SELECT texto FROM as_mensajes WHERE id = ? AND asistente_id = ?", (mid, aid)).fetchone()
    finally:
        conn.close()
    if not m:
        raise NoSePuede("Ese mensaje no existe")
    variantes = _lineas(data.get("variantes"), MAX_VARIANTES, 160)
    if _plano(m["texto"]) not in {_plano(v) for v in variantes}:
        variantes = [m["texto"][:160]] + variantes
    guardar_ficha(db, aid, {**data, "variantes": variantes[:MAX_VARIANTES], "clave": ""})
    conn = _connect(db)
    try:
        fid = conn.execute("SELECT MAX(id) FROM as_fichas WHERE asistente_id = ?", (aid,)).fetchone()[0]
        n = conn.execute("SELECT COUNT(*) FROM as_pruebas WHERE asistente_id = ?", (aid,)).fetchone()[0]
        if n < MAX_PRUEBAS:
            conn.execute("INSERT INTO as_pruebas (asistente_id, pregunta, esperado, ficha_id) VALUES (?, ?, 'ficha', ?)",
                         (aid, m["texto"][:300], fid))
        conn.execute("UPDATE as_mensajes SET resuelto = 1 WHERE id = ?", (mid,))
        conn.commit()
    finally:
        conn.close()
    return detalle(db, aid)


# ── conexión ─────────────────────────────────────────────────────────────────

def _hash(clave: str) -> str:
    return hashlib.sha256(clave.encode()).hexdigest()


def nueva_clave(db: str, aid: int) -> str:
    """Genera la clave de la API. Se muestra una sola vez: se guarda el hash."""
    clave = "asis_" + secrets.token_urlsafe(24)
    conn = _connect(db)
    try:
        _fila(conn, aid)
        conn.execute("UPDATE as_asistentes SET clave_hash = ? WHERE id = ?", (_hash(clave), aid))
        conn.commit()
    finally:
        conn.close()
    return clave


def conectar(db: str, aid: int, conectado: bool) -> dict:
    d = detalle(db, aid)
    if conectado and not d["listo"]:
        raise NoSePuede("Todavía no está listo: " + "; ".join(c["texto"] for c in d["chequeos"] if not c["ok"]))
    if conectado and not d["tiene_clave"]:
        raise NoSePuede("Generá la clave de la API antes de conectarlo")
    conn = _connect(db)
    try:
        conn.execute("UPDATE as_asistentes SET conectado = ? WHERE id = ?", (int(conectado), aid))
        conn.commit()
    finally:
        conn.close()
    return detalle(db, aid)


def por_token(db: str, token: str) -> dict | None:
    if not token or len(token) > 64:
        return None
    conn = _connect(db)
    try:
        f = conn.execute("SELECT id FROM as_asistentes WHERE token = ?", (token,)).fetchone()
    finally:
        conn.close()
    return detalle(db, f["id"]) if f else None


def clave_valida(db: str, token: str, clave: str) -> dict | None:
    """El asistente si la clave corresponde y está conectado; None si no."""
    if not token or not clave or len(token) > 64 or len(clave) > 100:
        return None
    conn = _connect(db)
    try:
        f = conn.execute("SELECT id, clave_hash, conectado FROM as_asistentes WHERE token = ?", (token,)).fetchone()
    finally:
        conn.close()
    if not f or not f["clave_hash"] or not hmac.compare_digest(f["clave_hash"], _hash(clave)):
        return None
    return {"id": f["id"], "conectado": bool(f["conectado"])}


# ── lectura ──────────────────────────────────────────────────────────────────

def _chequeos(cfg: dict, fichas: list[dict], pruebas: list[dict], ultima: dict | None, cambiado_en: str) -> list[dict]:
    horario = cfg.get("horario") or {}
    usables = [f for f in fichas if f["activa"] and not pendiente(f, horario)]
    pend = [f["titulo"] for f in fichas if f["activa"] and pendiente(f, horario)]
    al_dia = bool(ultima) and ultima["creado_en"] >= cambiado_en
    return [
        {"clave": "fichas", "ok": len(usables) >= MIN_FICHAS,
         "texto": f"Al menos {MIN_FICHAS} fichas completas ({len(usables)})"},
        {"clave": "pendientes", "ok": not pend,
         "texto": "Fichas sin completar: " + ", ".join(pend[:4]) + ("…" if len(pend) > 4 else "") if pend
         else "Ninguna ficha a medio completar"},
        {"clave": "horario", "ok": bool(texto_horario(horario)), "texto": "Horario de atención cargado"},
        {"clave": "humano", "ok": bool(cfg.get("humano")), "texto": "A quién se deriva (nombre de la persona o del área)"},
        {"clave": "pruebas", "ok": len(pruebas) >= MIN_PRUEBAS, "texto": f"Al menos {MIN_PRUEBAS} pruebas ({len(pruebas)})"},
        {"clave": "puntaje", "ok": bool(ultima) and ultima["puntaje"] >= PUNTAJE_LISTO and al_dia,
         "texto": (f"Puntaje de {PUNTAJE_LISTO} % o más en la última corrida"
                   + (f" ({ultima['puntaje']} %)" if ultima else " (sin correr)")
                   + ("" if al_dia or not ultima else ": hubo cambios después, volvé a correrlas"))},
    ]


def detalle(db: str, aid: int) -> dict:
    conn = _connect(db)
    try:
        f = _fila(conn, aid)
        cfg = _cfg(f)
        fichas = _fichas(conn, aid)
        pruebas = [dict(r) for r in conn.execute("SELECT * FROM as_pruebas WHERE asistente_id = ? ORDER BY id", (aid,))]
        corridas = [dict(r) for r in conn.execute(
            "SELECT total, bien, puntaje, creado_en FROM as_corridas WHERE asistente_id = ? ORDER BY id DESC LIMIT 12",
            (aid,))]
        hueco = [dict(r) for r in conn.execute(
            "SELECT id, origen, texto, respuesta, confianza, creado_en FROM as_mensajes "
            "WHERE asistente_id = ? AND tipo = 'no_sabe' AND resuelto = 0 ORDER BY id DESC LIMIT 50", (aid,))]
        ultimos = [dict(r) for r in conn.execute(
            "SELECT m.id, m.origen, m.texto, m.respuesta, m.tipo, m.confianza, m.creado_en, fi.titulo AS ficha "
            "FROM as_mensajes m LEFT JOIN as_fichas fi ON fi.id = m.ficha_id "
            "WHERE m.asistente_id = ? ORDER BY m.id DESC LIMIT 40", (aid,))]
        semana = _txt(_ahora() - timedelta(days=7))
        st = conn.execute(
            "SELECT COUNT(*) AS n, COUNT(DISTINCT conversacion) AS charlas, "
            "SUM(CASE WHEN tipo IN ('respuesta','horario','saludo','gracias') THEN 1 ELSE 0 END) AS resueltos, "
            "SUM(CASE WHEN tipo = 'derivar' THEN 1 ELSE 0 END) AS derivados, "
            "SUM(CASE WHEN tipo = 'no_sabe' THEN 1 ELSE 0 END) AS no_sabe "
            "FROM as_mensajes WHERE asistente_id = ? AND creado_en > ?", (aid, semana)).fetchone()
    finally:
        conn.close()
    horario = cfg.get("horario") or {}
    nombres = {x["id"]: x["titulo"] for x in fichas}
    for p in pruebas:
        p["ficha"] = nombres.get(p["ficha_id"], "") if p["ficha_id"] else ""
        p["ok"] = None if p["ok"] is None else bool(p["ok"])
    for x in fichas:
        x["pendiente"] = pendiente(x, horario)
    ultima = corridas[0] if corridas else None
    chequeos = _chequeos(cfg, fichas, pruebas, ultima, f["cambiado_en"])
    listo = all(c["ok"] for c in chequeos)
    n = st["n"] or 0
    return {
        "id": f["id"], "nombre": f["nombre"], "token": f["token"], "business_id": f["business_id"],
        "config": cfg, "familia_nombre": FAMILIAS[cfg["familia"]]["nombre"],
        "horario_texto": texto_horario(horario), "estado_local": estado_local(horario),
        "fichas": fichas, "pruebas": pruebas, "corridas": corridas, "ultima": ultima,
        "sin_respuesta": hueco, "mensajes": ultimos,
        "chequeos": chequeos, "listo": listo, "conectado": bool(f["conectado"]),
        "tiene_clave": bool(f["clave_hash"]),
        "estado": "conectado" if f["conectado"] else ("listo" if listo else "armando"),
        "semana": {"mensajes": n, "charlas": st["charlas"] or 0, "derivados": st["derivados"] or 0,
                   "no_sabe": st["no_sabe"] or 0,
                   "resolucion": round(100 * (st["resueltos"] or 0) / n) if n else None},
        "creado_por": f["creado_por"], "creado_en": f["creado_en"], "cambiado_en": f["cambiado_en"],
    }


def listar(db: str) -> list[dict]:
    conn = _connect(db)
    try:
        ids = [r["id"] for r in conn.execute("SELECT id FROM as_asistentes ORDER BY cambiado_en DESC, id DESC")]
    finally:
        conn.close()
    out = []
    for i in ids:
        d = detalle(db, i)
        out.append({k: d[k] for k in ("id", "nombre", "familia_nombre", "estado", "listo", "conectado", "semana",
                                      "ultima", "cambiado_en")}
                   | {"n_fichas": sum(1 for x in d["fichas"] if x["activa"] and not x["pendiente"]),
                      "n_pendientes": sum(1 for x in d["fichas"] if x["activa"] and x["pendiente"]),
                      "n_pruebas": len(d["pruebas"]), "n_sin_respuesta": len(d["sin_respuesta"])})
    return out


def resumen(lista: list[dict]) -> dict:
    puntajes = [a["ultima"]["puntaje"] for a in lista if a["ultima"]]
    msj = sum(a["semana"]["mensajes"] for a in lista)
    resueltos = sum(round((a["semana"]["resolucion"] or 0) * a["semana"]["mensajes"] / 100) for a in lista)
    return {"total": len(lista), "conectados": sum(1 for a in lista if a["conectado"]),
            "listos": sum(1 for a in lista if a["listo"]),
            "puntaje": round(sum(puntajes) / len(puntajes)) if puntajes else None,
            "sin_respuesta": sum(a["n_sin_respuesta"] for a in lista),
            "mensajes": msj, "resolucion": round(100 * resueltos / msj) if msj else None}


def borrar(db: str, aid: int) -> None:
    conn = _connect(db)
    try:
        _fila(conn, aid)
        for t in ("as_fichas", "as_pruebas", "as_corridas", "as_mensajes"):
            conn.execute(f"DELETE FROM {t} WHERE asistente_id = ?", (aid,))
        conn.execute("DELETE FROM as_asistentes WHERE id = ?", (aid,))
        conn.commit()
    finally:
        conn.close()


def exportar(db: str, aid: int) -> dict:
    """La configuración entera, para el bot de un cliente que corra aparte
    (edishop-bot, crm-jose) o para respaldo. Sin la clave ni conversaciones."""
    d = detalle(db, aid)
    horario = d["config"].get("horario") or {}
    return {"version": 1, "nombre": d["nombre"], "config": d["config"],
            "fichas": [{k: x[k] for k in ("clave", "titulo", "contenido", "variantes")}
                       for x in d["fichas"] if x["activa"] and not pendiente(x, horario)],
            "pruebas": [{"pregunta": p["pregunta"], "esperado": p["esperado"], "ficha": p["ficha"]} for p in d["pruebas"]],
            "derivar_siempre": DERIVAR_SIEMPRE}


def clientes(db: str) -> list:
    conn = _connect(db)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, name FROM businesses WHERE crm_status IN ('cerrado','en_desarrollo','finalizado','acepto') "
            "ORDER BY name COLLATE NOCASE LIMIT 300")]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def catalogo() -> dict:
    return {"familias": {k: v["nombre"] for k, v in FAMILIAS.items()}, "tonos": TONOS, "dias": DIAS,
            "esperados": ESPERADOS, "tipos": TIPOS, "origenes": ORIGENES, "puntaje_listo": PUNTAJE_LISTO}
