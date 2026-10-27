"""Google solo: puntaje, reseñas y respuestas sugeridas (Reseñas, 27/10).

Hasta acá el puntaje de Google se anotaba a mano una vez por mes y nadie
miraba las reseñas que la gente deja directo en Maps, que son la mayoría.
Como Birdeye o Podium, esto las trae solo:

- una vez por día lee de Google el puntaje, la cantidad de reseñas y las
  últimas reseñas del comercio (Places API, con el Place ID);
- el puntaje queda en rs_google como una foto por día, la misma tabla que
  se llenaba a mano: la evolución y los KPI salen igual;
- cada reseña nueva entra a una bandeja "para responder" con una respuesta
  ya escrita: la de 5 estrellas agradece, la de 2 pide disculpas e invita
  a seguir por WhatsApp. El comercio la copia, la ajusta y la publica en
  Google. Responder rápido, y también a las buenas, es lo que más mueve el
  puntaje según Google y BrightLocal;
- quien responde la marca como respondida (en el CRM o en su panel) y queda
  con qué texto y quién.

**Lo que la Places API no da.** Devuelve como mucho 5 reseñas (las que Google
considera más relevantes), no dice si el dueño ya respondió y no deja
responder. Para tener todas y publicar la respuesta desde acá hace falta la
API de Google Business Profile con el OAuth del comercio: `pedir` es una
función justamente para cambiar de proveedor sin tocar el resto.

**Lo que se guarda.** Las condiciones de Google Maps Platform no dejan
guardar su contenido por tiempo indefinido. Por eso, a los DIAS_TEXTO días
de leída, a la reseña se le borra el texto y el autor: queda el puntaje, la
fecha y nuestra respuesta, que es lo que usan las métricas.

**Respuesta con IA.** Nace apagada (RESENAS_IA=on la prende, y hace falta
ANTHROPIC_API_KEY). Apagada, la respuesta sale de plantillas por tramo de
estrellas y por tema (atención, comida, demora, precio, limpieza). Prendida,
el modelo recibe solo la reseña pública, el nombre y el rubro del comercio;
lo que devuelve pasa por `validar_respuesta` y si no valida se usa la
plantilla. Nunca promete descuentos ni devoluciones ni inventa datos de
contacto: eso lo decide el dueño.

Topes, porque es una API paga y corre en un hilo que arranca en cada boot
(regla 3 de COORDINACION.md): una lectura automática por comercio cada
HORAS_ENTRE_LECTURAS, MAX_MANUALES_DIA lecturas a mano por comercio por día
y MAX_POR_HORA en total. Las fallidas también cuentan. Todo vive en
rs_google_lecturas, así que un deploy no regala cupo. Sin
GOOGLE_PLACES_API_KEY no hay hilo; RESENAS_GOOGLE=off lo apaga.
"""

import hashlib
import logging
import os
import re
import sqlite3
import threading
import time
import unicodedata
from datetime import datetime, timedelta
from typing import Callable, Optional
from urllib.parse import parse_qs, quote, urlparse

from services import resenas as rs

logger = logging.getLogger(__name__)

HORAS_ENTRE_LECTURAS = 20
MAX_MANUALES_DIA = 4
MAX_POR_HORA = 30
DIAS_TEXTO = 30
LARGO_RESPUESTA = 1000
LARGO_RESENA = 4000

REVISAR_CADA_S = 3600
_DEMORA_ARRANQUE_S = 240

ESTADOS = {"sin_responder": "Para responder", "respondida": "Respondida", "no_responde": "No hace falta"}

PLACES_URL = "https://places.googleapis.com/v1/places/"
CAMPOS = "id,displayName,rating,userRatingCount,googleMapsUri,reviews"
MODELO_IA = "claude-sonnet-5-5"

PLACE_ID = re.compile(r"[A-Za-z0-9_-]{16,300}")
_lock = threading.Lock()

Pedir = Callable[[str], dict]
Redactar = Callable[[dict, dict], Optional[str]]


# ── tablas ───────────────────────────────────────────────────────────────────

def init_google(conn: sqlite3.Connection) -> None:
    columnas = {c[1] for c in conn.execute("PRAGMA table_info(rs_negocios)")}
    if "place_id" not in columnas:
        conn.execute("ALTER TABLE rs_negocios ADD COLUMN place_id TEXT")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS rs_google_resenas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            negocio_id INTEGER NOT NULL REFERENCES rs_negocios(id) ON DELETE CASCADE,
            google_id TEXT NOT NULL,
            rating INTEGER NOT NULL,
            texto TEXT,
            autor TEXT,
            link TEXT,
            publicada_en TEXT,
            leida_en TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'sin_responder',
            sugerida TEXT,
            sugerida_por TEXT,
            respuesta TEXT,
            respondida_por TEXT,
            respondida_en TEXT,
            UNIQUE (negocio_id, google_id)
        );
        CREATE INDEX IF NOT EXISTS ix_rs_gres_neg ON rs_google_resenas (negocio_id, estado, publicada_en);
        CREATE TABLE IF NOT EXISTS rs_google_lecturas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            negocio_id INTEGER NOT NULL REFERENCES rs_negocios(id) ON DELETE CASCADE,
            tipo TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'leyendo',
            nuevas INTEGER NOT NULL DEFAULT 0,
            detalle TEXT,
            creado_en TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_rs_glec ON rs_google_lecturas (negocio_id, creado_en);
    """)


# ── Place ID ─────────────────────────────────────────────────────────────────

def place_id_valido(v) -> str:
    v = str(v or "").strip()
    if not v:
        return ""
    if not PLACE_ID.fullmatch(v):
        raise rs.NoSePuede("El Place ID son letras, números, - y _ (el de un comercio empieza casi siempre con ChIJ). "
                           "Se saca del buscador de Place ID de Google.")
    return v


def place_id_de(n: dict) -> str:
    """El Place ID cargado, o el que viene adentro del link para reseñar."""
    if n.get("place_id"):
        return n["place_id"]
    u = urlparse(n.get("google_url") or "")
    for v in parse_qs(u.query).get("placeid", []):
        if PLACE_ID.fullmatch(v):
            return v
    return ""


def guardar_place_id(db: str, nid: int, valor) -> dict:
    v = place_id_valido(valor)
    rs.negocio(db, nid)
    conn = rs._con(db)
    try:
        conn.execute("UPDATE rs_negocios SET place_id = ? WHERE id = ?", (v or None, nid))
        conn.commit()
    finally:
        conn.close()
    return rs.negocio(db, nid)


def link_responder(n: dict, r: Optional[dict] = None) -> str:
    """Dónde se responde. El link de la reseña abre Maps en esa reseña; el dueño,
    con su cuenta, ve ahí el botón Responder."""
    if r and r.get("link"):
        return r["link"]
    pid = place_id_de(n)
    return "https://search.google.com/local/reviews?placeid=" + quote(pid) if pid else "https://business.google.com/reviews"


# ── proveedor: Places API (New) ──────────────────────────────────────────────

def clave_places() -> str:
    return os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()


def pedir_places(place_id: str) -> dict:
    """GET a Places API (New). La clave va en una cabecera, nunca en la URL (así no queda en logs)."""
    import requests

    clave = clave_places()
    if not clave:
        raise rs.NoSePuede("Falta GOOGLE_PLACES_API_KEY: sin la clave de Google no se puede leer.")
    r = requests.get(PLACES_URL + quote(place_id, safe=""), params={"languageCode": "es", "regionCode": "UY"},
                     headers={"X-Goog-Api-Key": clave, "X-Goog-FieldMask": CAMPOS}, timeout=15)
    if r.status_code == 404 or r.status_code == 400:
        raise rs.NoSePuede("Google no encuentra ese Place ID. Revisalo en Ajustes.")
    if r.status_code in (401, 403):
        raise rs.NoSePuede("Google rechazó la clave (¿está habilitada la Places API (New) en el proyecto?).")
    if r.status_code == 429:
        raise rs.NoSePuede("Google dice que se pasó la cuota. Se vuelve a probar mañana.")
    if not r.ok:
        raise rs.NoSePuede(f"Google contestó {r.status_code}. Se vuelve a probar en la próxima vuelta.")
    return r.json()


def _resenas_de(datos: dict) -> list:
    """Lo que nos importa de cada reseña, con largos acotados y sin HTML."""
    out = []
    for x in datos.get("reviews") or []:
        gid = str(x.get("name") or "").strip()[:300]
        try:
            rating = int(x.get("rating"))
        except (TypeError, ValueError):
            continue
        if not gid or not 1 <= rating <= 5:
            continue
        texto = ((x.get("originalText") or {}).get("text") or (x.get("text") or {}).get("text") or "").strip()
        autor = ((x.get("authorAttribution") or {}).get("displayName") or "").strip()
        link = str(x.get("googleMapsUri") or "")
        host = (urlparse(link).hostname or "").lower()
        if not link.startswith("https://") or not (host == "google.com" or host.endswith(".google.com")
                                                   or host == "maps.app.goo.gl"):
            link = ""
        publicada = str(x.get("publishTime") or "")[:19].replace("T", " ")[:16] or None
        if publicada:
            try:
                publicada = rs._txt(datetime.strptime(publicada, "%Y-%m-%d %H:%M") + rs.UY)
            except ValueError:
                publicada = None
        out.append({"google_id": gid, "rating": rating, "texto": texto[:LARGO_RESENA] or None,
                    "autor": autor[:120] or None, "link": link[:500] or None, "publicada_en": publicada})
    return out


# ── respuestas sugeridas ─────────────────────────────────────────────────────

def _sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


# Temas que aparecen en las reseñas de comercios, con la frase que va en la
# respuesta según si se nombró para bien o para mal.
TEMAS = (
    ("atencion", ("atencion", "atendi", "mozo", "moza", "amable", "trato", "personal", "simpatic", "chicas", "chicos"),
     "Nos alegra mucho que te hayan atendido bien: se lo vamos a contar al equipo.",
     "Lo que contás de la atención no es lo que queremos: lo vamos a hablar con el equipo."),
    ("comida", ("comida", "plato", "carne", "pizza", "rico", "rica", "sabor", "delicios", "porcion", "postre", "asado"),
     "Qué bueno que te haya gustado lo que comiste.",
     "La comida tiene que llegar como corresponde: lo vamos a revisar en la cocina."),
    ("demora", ("demor", "tarda", "espera", "lento", "lenta", "rapido", "rapida", "tiempo"),
     "Gracias por notar que tratamos de que no esperes.",
     "Tenés razón con la espera: estamos revisando cómo hacerlo más rápido."),
    ("precio", ("precio", "caro", "cara", "barato", "cuenta", "costo", "vale la pena"),
     "Nos alegra que sientas que vale lo que cuesta.",
     "Tomamos nota de lo que decís del precio."),
    ("limpieza", ("limpi", "sucio", "sucia", "baño", "higiene", "olor"),
     "Gracias por fijarte en la limpieza: le ponemos mucho cuidado.",
     "Lo de la limpieza no puede pasar: ya lo estamos revisando."),
)

_APERTURAS = {
    5: ("¡Muchas gracias{nombre}!", "¡Gracias por tomarte el tiempo{nombre}!", "¡Qué lindo leer esto{nombre}!"),
    4: ("¡Gracias por tu reseña{nombre}!", "¡Muchas gracias{nombre}!", "Gracias por contarnos{nombre}."),
    3: ("Gracias por tu reseña{nombre}.", "Gracias por contarnos cómo te fue{nombre}."),
    2: ("Hola{hola}, gracias por contarnos y perdón por la experiencia.",
        "Hola{hola}, lamentamos mucho que tu visita no haya sido buena."),
}
_CIERRES = {
    5: ("Te esperamos de vuelta en {negocio}.", "Nos vemos pronto en {negocio}."),
    4: ("Te esperamos de vuelta en {negocio}.", "Si hay algo que podamos hacer mejor, contanos."),
    3: ("Nos gustaría saber qué podemos mejorar para que la próxima sea de 5.",),
    2: ("Queremos solucionarlo: {contacto}",),
}


def temas(texto: str) -> list:
    """Los temas que nombra la reseña, en el orden en que aparecen: lo primero que se dice es lo que más pesó."""
    t = _sin_tildes(texto or "")
    hallados = []
    for k, claves, _b, _m in TEMAS:
        pos = [t.find(c) for c in claves if c in t]
        if pos:
            hallados.append((min(pos), k))
    return [k for _p, k in sorted(hallados)]


def _nombre_pila(autor: Optional[str]) -> str:
    p = (autor or "").strip().split()
    if not p or not re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ'-]{2,20}", p[0]):
        return ""
    return p[0][:1].upper() + p[0][1:].lower()


def sugerir_plantilla(n: dict, r: dict) -> str:
    """Una respuesta lista para copiar. Varía según la reseña, siempre igual para la misma."""
    tramo = r["rating"] if r["rating"] >= 3 else 2
    sem = int(hashlib.sha256(r["google_id"].encode()).hexdigest()[:8], 16)
    elegir = lambda opciones: opciones[sem % len(opciones)]  # noqa: E731
    nombre = _nombre_pila(r.get("autor"))
    partes = [elegir(_APERTURAS[tramo]).format(nombre=(", " + nombre) if nombre else "",
                                               hola=(" " + nombre) if nombre else "")]
    buena = r["rating"] >= 4
    for k in temas(r.get("texto") or "")[:2]:
        _k, _c, bien, mal = next(t for t in TEMAS if t[0] == k)
        partes.append(bien if buena else mal)
    if tramo == 3 and len(partes) == 1:
        partes.append("Nos quedamos con lo que nos decís para mejorar.")
    if tramo == 2:
        tel = n.get("whatsapp") or ""
        contacto = (f"escribinos por WhatsApp al {rs_tel_visible(tel)} y lo vemos con vos."
                    if tel else f"escribinos por mensaje a {n['nombre']} y lo vemos con vos.")
        partes.append(elegir(_CIERRES[2]).format(contacto=contacto))
    else:
        partes.append(elegir(_CIERRES[tramo]).format(negocio=n["nombre"]))
    partes.append(f"Equipo de {n['nombre']}")
    return " ".join(partes[:-1]) + "\n" + partes[-1]


def rs_tel_visible(tel: str) -> str:
    """598 99 123 456 → 099 123 456, como se escribe en Uruguay."""
    d = re.sub(r"\D", "", tel or "")
    if d.startswith("598"):
        d = "0" + d[3:] if d[3:4] == "9" else d[3:]
    if len(d) == 9:
        return f"{d[:3]} {d[3:6]} {d[6:]}"
    return d


_PROHIBIDO = re.compile(
    r"https?://|www\.|@|descuento|gratis|reembolso|devoluci|te devolvemos|regal|cup[oó]n|\d+\s?%|"
    r"invitamos? (la|el|una|un) |abogad|denuncia|mentira|falso|fals[ao]s", re.I)


def validar_respuesta(texto: Optional[str], n: dict) -> Optional[str]:
    """La respuesta de la IA, si se puede publicar tal cual. Si no, None.

    Lo que se frena: compromisos que decide el dueño (descuentos, devoluciones,
    invitaciones), links y mails, discutirle al cliente, números que no son el
    WhatsApp del comercio, y largos raros.
    """
    t = (texto or "").strip().strip('"“”').strip()
    if not 40 <= len(t) <= 700 or _PROHIBIDO.search(t):
        return None
    tel = re.sub(r"\D", "", n.get("whatsapp") or "")
    propio = {tel, rs_tel_visible(tel).replace(" ", "")} if tel else set()
    for num in re.findall(r"\d[\d \-]{5,}\d", t):
        if re.sub(r"\D", "", num) not in propio:
            return None
    return t


def ia_activa() -> bool:
    return (os.environ.get("RESENAS_IA", "").strip().lower() in ("on", "1", "true", "si", "sí")
            and bool(os.environ.get("ANTHROPIC_API_KEY")))


def redactar_con_ia(n: dict, r: dict) -> Optional[str]:
    import anthropic

    cliente = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=30)
    sistema = (
        "Escribís la respuesta pública del dueño de un comercio de Montevideo a una reseña de Google. "
        "Español rioplatense, con voseo, cálido y concreto, de 2 a 4 frases. Respondé a lo que dice la reseña, "
        "sin frases de manual. Si la reseña es mala: pedí disculpas sin excusas y sin discutir, y ofrecé seguir "
        "por privado" + (f" por WhatsApp al {rs_tel_visible(n['whatsapp'])}" if n.get("whatsapp") else "") + ". "
        "Nunca prometas descuentos, devoluciones, regalos ni invitaciones, no pongas links ni mails ni otros "
        "teléfonos, y no inventes datos del comercio. Terminá con una línea aparte: Equipo de " + n["nombre"] + ". "
        "Devolvé solo el texto de la respuesta.")
    usuario = (f"Comercio: {n['nombre']}" + (f" ({n['rubro']})" if n.get("rubro") else "")
               + f"\nEstrellas: {r['rating']} de 5\nAutor: {_nombre_pila(r.get('autor')) or 'sin nombre'}"
               + "\nReseña:\n<resena>\n" + (r.get("texto") or "(sin texto)")[:2000] + "\n</resena>")
    m = cliente.messages.create(model=MODELO_IA, max_tokens=400, system=sistema,
                                messages=[{"role": "user", "content": usuario}])
    return "".join(b.text for b in m.content if getattr(b, "type", "") == "text")


def sugerir(n: dict, r: dict, redactar: Optional[Redactar] = None) -> tuple:
    """(texto, 'ia' | 'plantilla'). La IA solo si está prendida y lo que devuelve valida."""
    redactar = redactar if redactar is not None else (redactar_con_ia if ia_activa() else None)
    if redactar:
        try:
            t = validar_respuesta(redactar(n, r), n)
            if t:
                return t[:LARGO_RESPUESTA], "ia"
        except Exception:
            logger.warning("Reseñas: la IA no pudo sugerir la respuesta de %s", r.get("google_id"), exc_info=True)
    return sugerir_plantilla(n, r), "plantilla"


# ── lectura ──────────────────────────────────────────────────────────────────

def _cupo(conn, nid: int, tipo: str, ahora: datetime) -> Optional[str]:
    """None si se puede leer; si no, por qué."""
    hora = conn.execute("SELECT COUNT(*) FROM rs_google_lecturas WHERE creado_en >= ?",
                        (rs._txt(ahora - timedelta(hours=1)),)).fetchone()[0]
    if hora >= MAX_POR_HORA:
        return "Se llegó al tope de lecturas de esta hora. Probá en un rato."
    if tipo == "manual":
        hoy = conn.execute("SELECT COUNT(*) FROM rs_google_lecturas WHERE negocio_id = ? AND tipo = 'manual' "
                           "AND creado_en >= ?", (nid, ahora.date().isoformat())).fetchone()[0]
        if hoy >= MAX_MANUALES_DIA:
            return f"Ya se leyó {MAX_MANUALES_DIA} veces hoy a mano. Mañana se lee sola."
    elif conn.execute("SELECT 1 FROM rs_google_lecturas WHERE negocio_id = ? AND creado_en >= ?",
                      (nid, rs._txt(ahora - timedelta(hours=HORAS_ENTRE_LECTURAS)))).fetchone():
        return "ya leído"
    return None


def leer(db: str, nid: int, tipo: str = "auto", pedir: Optional[Pedir] = None, redactar: Optional[Redactar] = None,
         ahora: Optional[datetime] = None) -> dict:
    """Lee Google para un comercio: foto del puntaje del día y reseñas nuevas con su respuesta sugerida.

    La lectura se anota antes de pedir (con BEGIN IMMEDIATE), así dos pasadas
    cruzadas no gastan dos veces y una que falla igual cuenta para el tope.
    """
    ahora = ahora or rs.ahora_uy()
    if pedir is None and not clave_places():
        # Sin clave no se gasta cupo ni queda una lectura fallida.
        raise rs.NoSePuede("Falta GOOGLE_PLACES_API_KEY: sin la clave de Google no se puede leer.")
    pedir = pedir or pedir_places
    n = rs.negocio(db, nid)
    pid = place_id_de(n)
    if not pid:
        raise rs.NoSePuede("Falta el Place ID del comercio: cargalo en Ajustes.")
    conn = rs._con(db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        motivo = _cupo(conn, nid, tipo, ahora)
        if motivo:
            conn.rollback()
            raise rs.NoSePuede(motivo)
        lid = conn.execute("INSERT INTO rs_google_lecturas (negocio_id, tipo, creado_en) VALUES (?,?,?)",
                           (nid, tipo, rs._txt(ahora))).lastrowid
        conn.commit()
    finally:
        conn.close()
    try:
        datos = pedir(pid)
    except Exception as e:
        detalle = str(e) if isinstance(e, rs.NoSePuede) else "No se pudo hablar con Google."
        if not isinstance(e, rs.NoSePuede):
            logger.warning("Reseñas: falló la lectura de Google del comercio %s", nid, exc_info=True)
        _cerrar_lectura(db, lid, "fallo", 0, detalle)
        raise rs.NoSePuede(detalle)
    nuevas = guardar_lectura(db, n, datos, redactar=redactar, ahora=ahora)
    _cerrar_lectura(db, lid, "ok", nuevas, None)
    return {"nuevas": nuevas, "negocio": rs.negocio(db, nid)}


def _cerrar_lectura(db: str, lid: int, estado: str, nuevas: int, detalle: Optional[str]) -> None:
    conn = rs._con(db)
    try:
        conn.execute("UPDATE rs_google_lecturas SET estado = ?, nuevas = ?, detalle = ? WHERE id = ?",
                     (estado, nuevas, (detalle or "")[:300] or None, lid))
        conn.commit()
    finally:
        conn.close()


def guardar_lectura(db: str, n: dict, datos: dict, redactar: Optional[Redactar] = None,
                    ahora: Optional[datetime] = None) -> int:
    """Guarda lo que vino de Google. Devuelve cuántas reseñas nuevas entraron."""
    ahora = ahora or rs.ahora_uy()
    nid = n["id"]
    resenas = _resenas_de(datos)
    conn = rs._con(db)
    try:
        ya = {f[0] for f in conn.execute("SELECT google_id FROM rs_google_resenas WHERE negocio_id = ?", (nid,))}
    finally:
        conn.close()
    nuevas = [r for r in resenas if r["google_id"] not in ya]
    # La sugerencia se escribe afuera de la transacción: con IA puede tardar unos segundos.
    for r in nuevas:
        r["sugerida"], r["sugerida_por"] = sugerir(n, r, redactar)
    conn = rs._con(db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        rating, cantidad = datos.get("rating"), datos.get("userRatingCount")
        if isinstance(rating, (int, float)) and isinstance(cantidad, int) and 1 <= rating <= 5 and cantidad >= 0:
            hoy = ahora.date().isoformat()
            # Una foto automática por día: la de hoy se pisa con la última lectura.
            conn.execute("DELETE FROM rs_google WHERE negocio_id = ? AND fecha = ? AND cargado_por = ?",
                         (nid, hoy, "Google"))
            conn.execute("INSERT INTO rs_google (negocio_id, fecha, rating, cantidad, cargado_por) VALUES (?,?,?,?,?)",
                         (nid, hoy, round(float(rating), 1), cantidad, "Google"))
        entraron = 0
        for r in nuevas:
            entraron += conn.execute(
                "INSERT OR IGNORE INTO rs_google_resenas (negocio_id, google_id, rating, texto, autor, link, "
                "publicada_en, leida_en, sugerida, sugerida_por) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (nid, r["google_id"], r["rating"], r["texto"], r["autor"], r["link"], r["publicada_en"],
                 rs._txt(ahora), r["sugerida"], r["sugerida_por"])).rowcount
        conn.commit()
    finally:
        conn.close()
    return entraron


def purgar(db: str, ahora: Optional[datetime] = None) -> int:
    """Borra texto, autor y link de las reseñas leídas hace más de DIAS_TEXTO días."""
    ahora = ahora or rs.ahora_uy()
    conn = rs._con(db)
    try:
        n = conn.execute("UPDATE rs_google_resenas SET texto = NULL, autor = NULL, link = NULL "
                         "WHERE leida_en < ? AND (texto IS NOT NULL OR autor IS NOT NULL OR link IS NOT NULL)",
                         (rs._txt(ahora - timedelta(days=DIAS_TEXTO)),)).rowcount
        conn.commit()
    finally:
        conn.close()
    return n


# ── bandeja ──────────────────────────────────────────────────────────────────

def resenas(db: str, nid: int, filtro: str = "sin_responder", limite: int = 50) -> list:
    where = {"sin_responder": "AND estado = 'sin_responder'", "respondidas": "AND estado != 'sin_responder'"}.get(filtro, "")
    conn = rs._con(db)
    try:
        filas = conn.execute(
            f"SELECT * FROM rs_google_resenas WHERE negocio_id = ? {where} "
            "ORDER BY estado = 'sin_responder' DESC, rating ASC, IFNULL(publicada_en, leida_en) DESC, id DESC LIMIT ?",
            (nid, limite)).fetchall()
    finally:
        conn.close()
    return [dict(f) for f in filas]


def resena(db: str, nid: int, rid: int) -> dict:
    conn = rs._con(db)
    try:
        f = conn.execute("SELECT * FROM rs_google_resenas WHERE id = ? AND negocio_id = ?", (rid, nid)).fetchone()
    finally:
        conn.close()
    if not f:
        raise rs.NoSePuede("La reseña no existe.")
    return dict(f)


def marcar(db: str, nid: int, rid: int, estado: str, respuesta, quien: str) -> dict:
    """Respondida (con el texto que se publicó), no hace falta, o de vuelta para responder."""
    if estado not in ESTADOS:
        raise rs.NoSePuede("Estado no válido.")
    r = resena(db, nid, rid)
    texto = str(respuesta or "").strip()[:LARGO_RESPUESTA] or None
    if estado == "respondida" and not texto:
        texto = r["sugerida"]
    conn = rs._con(db)
    try:
        if estado == "sin_responder":
            conn.execute("UPDATE rs_google_resenas SET estado = ?, respondida_por = NULL, respondida_en = NULL "
                         "WHERE id = ?", (estado, rid))
        else:
            conn.execute("UPDATE rs_google_resenas SET estado = ?, respuesta = ?, respondida_por = ?, "
                         "respondida_en = ? WHERE id = ?",
                         (estado, texto if estado == "respondida" else r["respuesta"], str(quien)[:120],
                          rs._txt(rs.ahora_uy()), rid))
        conn.commit()
    finally:
        conn.close()
    return resena(db, nid, rid)


def resumen(db: str, nid: int) -> dict:
    """Para el CRM y el panel: qué hay para responder, la última lectura y cómo se responde."""
    conn = rs._con(db)
    try:
        c = conn.execute(
            "SELECT COUNT(*) total, SUM(estado = 'sin_responder') sin_responder, "
            "SUM(estado = 'sin_responder' AND rating <= 3) bajas_sin_responder, SUM(estado = 'respondida') respondidas "
            "FROM rs_google_resenas WHERE negocio_id = ?", (nid,)).fetchone()
        ult = conn.execute("SELECT tipo, estado, nuevas, detalle, creado_en FROM rs_google_lecturas "
                           "WHERE negocio_id = ? ORDER BY id DESC LIMIT 1", (nid,)).fetchone()
        ult_ok = conn.execute("SELECT creado_en FROM rs_google_lecturas WHERE negocio_id = ? AND estado = 'ok' "
                              "ORDER BY id DESC LIMIT 1", (nid,)).fetchone()
        horas = [f[0] for f in conn.execute(
            "SELECT (julianday(respondida_en) - julianday(IFNULL(publicada_en, leida_en))) * 24 FROM rs_google_resenas "
            "WHERE negocio_id = ? AND estado = 'respondida' AND respondida_en IS NOT NULL", (nid,)) if f[0] is not None]
    finally:
        conn.close()
    horas.sort()
    total = c["total"] or 0
    return {"total": total, "sin_responder": c["sin_responder"] or 0, "bajas_sin_responder": c["bajas_sin_responder"] or 0,
            "respondidas_pct": round(100 * (c["respondidas"] or 0) / total) if total else None,
            "horas_respuesta": round(max(0.0, horas[len(horas) // 2]), 1) if horas else None,
            "ultima": dict(ult) if ult else None, "ultima_ok": ult_ok[0] if ult_ok else None,
            "automatico": activo(), "puede_leer": bool(clave_places()), "ia": ia_activa()}


# ── hilo ─────────────────────────────────────────────────────────────────────

def revisar(db: str, pedir: Optional[Pedir] = None, ahora: Optional[datetime] = None) -> dict:
    """Una pasada: lee los comercios activos con Place ID que no se leyeron en HORAS_ENTRE_LECTURAS."""
    ahora = ahora or rs.ahora_uy()
    hechos = {"leidos": 0, "nuevas": 0, "fallos": 0, "purgadas": purgar(db, ahora)}
    conn = rs._con(db)
    try:
        ids = [f["id"] for f in conn.execute("SELECT id, place_id, google_url FROM rs_negocios WHERE activo = 1 "
                                             "ORDER BY id") if place_id_de(dict(f))]
    finally:
        conn.close()
    for nid in ids:
        try:
            r = leer(db, nid, "auto", pedir=pedir, ahora=ahora)
        except rs.NoSePuede as e:
            if str(e) != "ya leído":
                hechos["fallos"] += 1
                if "tope" in str(e):
                    break
            continue
        hechos["leidos"] += 1
        hechos["nuevas"] += r["nuevas"]
    return hechos


def activo() -> bool:
    return bool(clave_places()) and os.environ.get("RESENAS_GOOGLE", "on").strip().lower() not in (
        "off", "0", "false", "no")


def start_google_resenas(app) -> None:
    """Revisa cada hora; cada comercio se lee una vez cada HORAS_ENTRE_LECTURAS.
    El cupo vive en rs_google_lecturas: un deploy no dispara lecturas de más."""
    if not activo():
        logger.info("Lectura de Google de Reseñas apagada (sin GOOGLE_PLACES_API_KEY o RESENAS_GOOGLE=off)")
        return

    def _loop():
        time.sleep(_DEMORA_ARRANQUE_S)
        while True:
            try:
                with _lock:
                    r = revisar(app.config["DB_PATH"])
                if r["leidos"] or r["fallos"]:
                    logger.info("Reseñas Google: %s", r)
            except Exception as e:
                logger.warning(f"Reseñas Google: {e}")
            time.sleep(REVISAR_CADA_S)

    threading.Thread(target=_loop, daemon=True, name="resenas-google").start()
