"""Reseñas y opiniones: QR en el mostrador para comercios (25/10).

Un producto para vender a restaurantes, peluquerías, talleres, clínicas y
tiendas: casi todos tienen menos reseñas en Google de las que se merecen
(el cliente contento se va sin escribir) y se enteran de las quejas cuando ya
son una estrella pública. Esto lo resuelve como Birdeye, Podium o Trustpilot:

- el comercio pega un QR en el mostrador, la mesa o el ticket (uno por lugar,
  para saber cuál rinde) o manda el link por WhatsApp después de la venta;
- su cliente abre `/opina/<slug>`, puntúa la visita con 1 a 5 estrellas y
  después se le ofrece dejar la reseña en Google con un toque;
- si algo salió mal, además se le ofrece contárselo al dueño en privado, con
  un contacto si quiere que lo llamen: entra a una bandeja que el comercio
  trabaja hasta resolver;
- las métricas dicen cuántos abrieron, cuántos puntuaron, cuántos fueron a
  Google, cuál QR rinde más, el promedio por semana y cómo se movió el puntaje
  de Google desde que arrancó.

A TODOS se les ofrece Google, también a los que puntuaron bajo. Filtrar quién
llega a Google según el puntaje ("review gating") está prohibido por Google y
por la FTC, y te pueden borrar las reseñas: acá no se hace. Lo que cambia con
el puntaje es solo el orden y el tono.

Las horas son de Montevideo (UTC-3 fija, sin horario de verano desde 2015).
Las rutas están en routes/resenas.py. Nada de esto le manda nada a nadie: el
"Contestar por WhatsApp" es un link wa.me que abre una persona.
"""

import hashlib
import re
import secrets
import sqlite3
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote, urlparse

from database import _connect

UY = timedelta(hours=-3)
FORMATO = "%Y-%m-%d %H:%M"

ESTADOS = ("abierta", "en_curso", "resuelta", "sin_accion")
# Hasta este puntaje (incluido) la opinión es una queja que alguien tiene que mirar.
QUEJA_HASTA = 3

LARGO_NOMBRE = 120
LARGO_COMENTARIO = 1500
LARGO_NOTA = 1000
# El cliente puede agregar o corregir su comentario un rato después de puntuar.
MINUTOS_PARA_COMENTAR = 120
# Topes del link público: frenan a quien lo quiera llenar de opiniones falsas.
MAX_POR_IP_DIA = 8
MAX_POR_NEGOCIO_DIA = 400

# Adónde puede mandar el botón de Google: solo dominios de Google, para que el
# link público no sirva de redirección abierta.
HOSTS_GOOGLE = ("g.page", "maps.app.goo.gl", "goo.gl", "g.co", "google.com", "www.google.com",
                "search.google.com", "maps.google.com", "business.google.com", "www.google.com.uy",
                "google.com.uy")


class NoSePuede(ValueError):
    """Lo que se pidió no se puede hacer; el mensaje se muestra tal cual."""


# ── tiempo y texto ───────────────────────────────────────────────────────────

def ahora_uy() -> datetime:
    return (datetime.now(timezone.utc) + UY).replace(tzinfo=None, second=0, microsecond=0)


def hoy_uy() -> date:
    return ahora_uy().date()


def _txt(d: datetime) -> str:
    return d.strftime(FORMATO)


def _dt(s) -> Optional[datetime]:
    try:
        return datetime.strptime(str(s or "")[:16], FORMATO)
    except ValueError:
        return None


def _limpio(v, largo: int) -> str:
    return re.sub(r"[ \t]+", " ", str(v or "").replace("\r\n", "\n")).strip()[:largo]


def _slug(texto: str) -> str:
    s = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40] or "opina"


def _color(v) -> str:
    v = str(v or "").strip()
    return v if re.fullmatch(r"#[0-9a-fA-F]{6}", v) else "#0088cc"


def tel_normal(tel) -> str:
    """El teléfono como lo entiende WhatsApp: 59899123456. Acepta 099 123 456."""
    d = re.sub(r"\D", "", str(tel or ""))
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("598"):
        d = d[3:]
    if d.startswith("0"):
        d = d[1:]
    if re.fullmatch(r"9\d{7}", d) or re.fullmatch(r"[24]\d{7}", d):
        return "598" + d
    return ""


def google_link(v) -> str:
    """El link para escribir la reseña. Acepta el Place ID o un link de Google.

    Con el Place ID (empieza con ChIJ, se saca del buscador de Place ID de
    Google) arma el link que abre directo el cuadro de las estrellas.
    """
    v = str(v or "").strip()
    if not v:
        return ""
    if re.fullmatch(r"ChIJ[\w-]{10,200}", v):
        return "https://search.google.com/local/writereview?placeid=" + v
    u = urlparse(v)
    host = (u.hostname or "").lower()
    if u.scheme != "https" or host not in HOSTS_GOOGLE or u.username or u.password:
        raise NoSePuede("El link de Google tiene que ser https y de Google (g.page, maps.app.goo.gl, "
                        "google.com) o el Place ID que empieza con ChIJ.")
    return v[:500]


# ── tablas ───────────────────────────────────────────────────────────────────

def init_resenas(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS rs_negocios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            rubro TEXT,
            google_url TEXT,
            whatsapp TEXT,
            color TEXT,
            pregunta TEXT,
            activo INTEGER NOT NULL DEFAULT 1,
            creado_por TEXT,
            creado_en TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rs_fuentes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            negocio_id INTEGER NOT NULL REFERENCES rs_negocios(id) ON DELETE CASCADE,
            nombre TEXT NOT NULL,
            codigo TEXT NOT NULL,
            creado_en TEXT NOT NULL,
            UNIQUE (negocio_id, codigo)
        );
        CREATE TABLE IF NOT EXISTS rs_vistas (
            negocio_id INTEGER NOT NULL REFERENCES rs_negocios(id) ON DELETE CASCADE,
            fuente_id INTEGER,
            dia TEXT NOT NULL,
            n INTEGER NOT NULL DEFAULT 0
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ix_rs_vistas ON rs_vistas (negocio_id, IFNULL(fuente_id, 0), dia);
        CREATE TABLE IF NOT EXISTS rs_opiniones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            negocio_id INTEGER NOT NULL REFERENCES rs_negocios(id) ON DELETE CASCADE,
            fuente_id INTEGER,
            codigo TEXT NOT NULL UNIQUE,
            puntaje INTEGER NOT NULL,
            comentario TEXT,
            nombre TEXT,
            contacto TEXT,
            quiere_contacto INTEGER NOT NULL DEFAULT 0,
            fue_a_google_en TEXT,
            estado TEXT NOT NULL DEFAULT 'sin_accion',
            nota TEXT,
            atendido_por TEXT,
            resuelta_en TEXT,
            ip_hash TEXT,
            creado_en TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_rs_opiniones_neg ON rs_opiniones (negocio_id, creado_en);
        CREATE TABLE IF NOT EXISTS rs_google (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            negocio_id INTEGER NOT NULL REFERENCES rs_negocios(id) ON DELETE CASCADE,
            fecha TEXT NOT NULL,
            rating REAL NOT NULL,
            cantidad INTEGER NOT NULL,
            cargado_por TEXT
        );
        CREATE TABLE IF NOT EXISTS rs_accesos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            negocio_id INTEGER NOT NULL REFERENCES rs_negocios(id) ON DELETE CASCADE,
            nombre TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            creado_por TEXT,
            creado_en TEXT NOT NULL,
            visto_en TEXT,
            visto_antes TEXT,
            revocado_en TEXT
        );
    """)
    from services.resenas_avisos import init_avisos
    from services.resenas_google import init_google
    init_avisos(conn)
    init_google(conn)


def _con(db: str) -> sqlite3.Connection:
    conn = _connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ── comercios ────────────────────────────────────────────────────────────────

def _datos_negocio(datos: dict, parcial: bool = False) -> dict:
    out = {}
    if not parcial or "nombre" in datos:
        out["nombre"] = _limpio(datos.get("nombre"), LARGO_NOMBRE)
        if not out["nombre"]:
            raise NoSePuede("Falta el nombre del comercio.")
    for k in ("rubro",):
        if k in datos:
            out[k] = _limpio(datos.get(k), LARGO_NOMBRE) or None
    if "pregunta" in datos:
        out["pregunta"] = _limpio(datos.get("pregunta"), 200) or None
    if "google_url" in datos:
        out["google_url"] = google_link(datos.get("google_url")) or None
    if "whatsapp" in datos:
        w = str(datos.get("whatsapp") or "").strip()
        out["whatsapp"] = tel_normal(w) or None
        if w and not out["whatsapp"]:
            raise NoSePuede("El WhatsApp no parece un celular o fijo de Uruguay.")
    if "place_id" in datos:
        from services.resenas_google import place_id_valido
        out["place_id"] = place_id_valido(datos.get("place_id")) or None
    if "color" in datos:
        out["color"] = _color(datos.get("color"))
    if "activo" in datos:
        out["activo"] = 1 if datos.get("activo") in (True, 1, "1", "on", "true") else 0
    if "slug" in datos and str(datos.get("slug") or "").strip():
        out["slug"] = _slug(str(datos["slug"]))
    return out


def crear_negocio(db: str, datos: dict, quien: str) -> dict:
    d = _datos_negocio(datos)
    base = d.pop("slug", None) or _slug(d["nombre"])
    conn = _con(db)
    try:
        slug, i = base, 2
        while conn.execute("SELECT 1 FROM rs_negocios WHERE slug = ?", (slug,)).fetchone():
            slug, i = f"{base[:36]}-{i}", i + 1
        cols = ["nombre", "slug", "creado_por", "creado_en"] + list(d.keys() - {"nombre"})
        vals = [d["nombre"], slug, quien, _txt(ahora_uy())] + [d[k] for k in cols[4:]]
        nid = conn.execute(f"INSERT INTO rs_negocios ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                           vals).lastrowid
        conn.commit()
    finally:
        conn.close()
    return negocio(db, nid)


def editar_negocio(db: str, nid: int, datos: dict) -> dict:
    negocio(db, nid)
    d = _datos_negocio(datos, parcial=True)
    if not d:
        return negocio(db, nid)
    conn = _con(db)
    try:
        if "slug" in d and conn.execute("SELECT 1 FROM rs_negocios WHERE slug = ? AND id != ?",
                                        (d["slug"], nid)).fetchone():
            raise NoSePuede("Ese link ya lo usa otro comercio.")
        conn.execute(f"UPDATE rs_negocios SET {', '.join(k + ' = ?' for k in d)} WHERE id = ?",
                     [*d.values(), nid])
        conn.commit()
    finally:
        conn.close()
    return negocio(db, nid)


def borrar_negocio(db: str, nid: int) -> None:
    conn = _con(db)
    try:
        conn.execute("DELETE FROM rs_negocios WHERE id = ?", (nid,))
        conn.commit()
    finally:
        conn.close()


def negocio(db: str, nid: int) -> dict:
    conn = _con(db)
    try:
        n = conn.execute("SELECT * FROM rs_negocios WHERE id = ?", (nid,)).fetchone()
        if not n:
            raise NoSePuede("El comercio no existe.")
        n = dict(n)
        n["fuentes"] = [dict(f) for f in conn.execute(
            "SELECT * FROM rs_fuentes WHERE negocio_id = ? ORDER BY id", (nid,))]
        n["google"] = [dict(g) for g in conn.execute(
            "SELECT id, fecha, rating, cantidad, cargado_por FROM rs_google WHERE negocio_id = ? "
            "ORDER BY fecha, id", (nid,))]
    finally:
        conn.close()
    n["color"] = _color(n.get("color"))
    return n


def por_slug(db: str, slug: str) -> Optional[dict]:
    if not re.fullmatch(r"[a-z0-9-]{1,60}", slug or ""):
        return None
    conn = _con(db)
    try:
        f = conn.execute("SELECT id FROM rs_negocios WHERE slug = ?", (slug,)).fetchone()
    finally:
        conn.close()
    return negocio(db, f["id"]) if f else None


def negocios(db: str) -> list:
    """Los comercios con lo justo para el selector: cuántas quejas abiertas y el promedio del mes."""
    desde = _txt(ahora_uy() - timedelta(days=30))
    conn = _con(db)
    try:
        filas = conn.execute(
            "SELECT n.id, n.nombre, n.slug, n.activo, "
            "(SELECT COUNT(*) FROM rs_opiniones o WHERE o.negocio_id = n.id AND o.estado IN ('abierta','en_curso')) abiertas, "
            "(SELECT ROUND(AVG(puntaje), 2) FROM rs_opiniones o WHERE o.negocio_id = n.id AND o.creado_en >= ?) promedio "
            "FROM rs_negocios n ORDER BY n.nombre COLLATE NOCASE", (desde,)).fetchall()
    finally:
        conn.close()
    return [dict(f) for f in filas]


# ── QR y links por lugar ─────────────────────────────────────────────────────

def crear_fuente(db: str, nid: int, nombre: str) -> dict:
    negocio(db, nid)
    nombre = _limpio(nombre, 60)
    if not nombre:
        raise NoSePuede("Poné dónde va el QR: mostrador, mesas, ticket, WhatsApp…")
    base = _slug(nombre)[:20]
    conn = _con(db)
    try:
        codigo, i = base, 2
        while conn.execute("SELECT 1 FROM rs_fuentes WHERE negocio_id = ? AND codigo = ?", (nid, codigo)).fetchone():
            codigo, i = f"{base}-{i}", i + 1
        fid = conn.execute("INSERT INTO rs_fuentes (negocio_id, nombre, codigo, creado_en) VALUES (?,?,?,?)",
                           (nid, nombre, codigo, _txt(ahora_uy()))).lastrowid
        conn.commit()
        return dict(conn.execute("SELECT * FROM rs_fuentes WHERE id = ?", (fid,)).fetchone())
    finally:
        conn.close()


def borrar_fuente(db: str, nid: int, fid: int) -> None:
    """Las opiniones que entraron por ahí quedan, como "link directo"."""
    conn = _con(db)
    try:
        if not conn.execute("DELETE FROM rs_fuentes WHERE id = ? AND negocio_id = ?", (fid, nid)).rowcount:
            raise NoSePuede("Ese QR no existe.")
        conn.execute("UPDATE rs_opiniones SET fuente_id = NULL WHERE fuente_id = ?", (fid,))
        conn.execute("DELETE FROM rs_vistas WHERE fuente_id = ?", (fid,))
        conn.commit()
    finally:
        conn.close()


def _fuente_id(conn, nid: int, codigo) -> Optional[int]:
    if not codigo:
        return None
    f = conn.execute("SELECT id FROM rs_fuentes WHERE negocio_id = ? AND codigo = ?",
                     (nid, str(codigo)[:40])).fetchone()
    return f["id"] if f else None


def registrar_vista(db: str, nid: int, fuente_codigo=None) -> None:
    conn = _con(db)
    try:
        fid = _fuente_id(conn, nid, fuente_codigo)
        dia = hoy_uy().isoformat()
        cur = conn.execute("UPDATE rs_vistas SET n = n + 1 WHERE negocio_id = ? AND IFNULL(fuente_id, 0) = ? "
                           "AND dia = ?", (nid, fid or 0, dia))
        if not cur.rowcount:
            conn.execute("INSERT INTO rs_vistas (negocio_id, fuente_id, dia, n) VALUES (?,?,?,1)", (nid, fid, dia))
        conn.commit()
    finally:
        conn.close()


def link_publico(base: str, n: dict, fuente: Optional[dict] = None) -> str:
    url = f"{base.rstrip('/')}/opina/{n['slug']}"
    return url + (f"?f={quote(fuente['codigo'])}" if fuente else "")


def qr_svg(texto: str) -> Optional[str]:
    """El QR como SVG, o None si falta la librería (el panel muestra el link igual)."""
    try:
        import io

        import segno
    except ImportError:
        return None
    buf = io.BytesIO()
    segno.make(texto, error="m").save(buf, kind="svg", scale=8, border=2, xmldecl=False, svgns=True,
                                      dark="#0f172a", light="#ffffff")
    return buf.getvalue().decode()


# ── lo que hace el cliente ───────────────────────────────────────────────────

def _ip_hash(ip: str) -> str:
    # Solo para el tope diario: no se guarda la IP, y el día entra en la sal
    # para que no sirva para seguir a nadie de un día al otro.
    return hashlib.sha256(f"{hoy_uy().isoformat()}|{ip or ''}".encode()).hexdigest()[:16]


def opinar(db: str, nid: int, puntaje, fuente_codigo=None, ip: str = "") -> dict:
    try:
        puntaje = int(puntaje)
    except (TypeError, ValueError):
        raise NoSePuede("Elegí de 1 a 5 estrellas.")
    if not 1 <= puntaje <= 5:
        raise NoSePuede("Elegí de 1 a 5 estrellas.")
    n = negocio(db, nid)
    if not n["activo"]:
        raise NoSePuede("Este comercio no está tomando opiniones por acá.")
    ih = _ip_hash(ip)
    hoy = hoy_uy().isoformat()
    conn = _con(db)
    try:
        if conn.execute("SELECT COUNT(*) FROM rs_opiniones WHERE negocio_id = ? AND ip_hash = ? AND creado_en >= ?",
                        (nid, ih, hoy)).fetchone()[0] >= MAX_POR_IP_DIA:
            raise NoSePuede("Ya recibimos varias opiniones desde esta conexión hoy. ¡Gracias!")
        if conn.execute("SELECT COUNT(*) FROM rs_opiniones WHERE negocio_id = ? AND creado_en >= ?",
                        (nid, hoy)).fetchone()[0] >= MAX_POR_NEGOCIO_DIA:
            raise NoSePuede("Hoy no podemos tomar más opiniones por acá. Probá mañana.")
        codigo = secrets.token_urlsafe(12)
        estado = "abierta" if puntaje <= QUEJA_HASTA else "sin_accion"
        oid = conn.execute(
            "INSERT INTO rs_opiniones (negocio_id, fuente_id, codigo, puntaje, estado, ip_hash, creado_en) "
            "VALUES (?,?,?,?,?,?,?)",
            (nid, _fuente_id(conn, nid, fuente_codigo), codigo, puntaje, estado, ih, _txt(ahora_uy()))).lastrowid
        conn.commit()
    finally:
        conn.close()
    return opinion(db, oid)


def opinion(db: str, oid: int) -> dict:
    conn = _con(db)
    try:
        o = conn.execute("SELECT o.*, f.nombre fuente FROM rs_opiniones o LEFT JOIN rs_fuentes f ON f.id = o.fuente_id "
                         "WHERE o.id = ?", (oid,)).fetchone()
    finally:
        conn.close()
    if not o:
        raise NoSePuede("La opinión no existe.")
    o = dict(o)
    o.pop("ip_hash", None)
    return o


def por_codigo(db: str, codigo: str) -> Optional[dict]:
    if not re.fullmatch(r"[\w-]{12,40}", codigo or ""):
        return None
    conn = _con(db)
    try:
        f = conn.execute("SELECT id FROM rs_opiniones WHERE codigo = ?", (codigo,)).fetchone()
    finally:
        conn.close()
    return opinion(db, f["id"]) if f else None


def puede_comentar(o: dict) -> bool:
    d = _dt(o["creado_en"])
    return bool(d) and ahora_uy() - d <= timedelta(minutes=MINUTOS_PARA_COMENTAR)


def comentar(db: str, codigo: str, datos: dict) -> dict:
    o = por_codigo(db, codigo)
    if not o:
        raise NoSePuede("La opinión no existe.")
    if not puede_comentar(o):
        raise NoSePuede("Ya pasó el rato para agregar comentarios. Si querés, escribile al comercio.")
    comentario = _limpio(datos.get("comentario"), LARGO_COMENTARIO)
    nombre = _limpio(datos.get("nombre"), LARGO_NOMBRE)
    contacto = _limpio(datos.get("contacto"), LARGO_NOMBRE)
    quiere = 1 if datos.get("quiere_contacto") in (True, 1, "1", "on", "true") and contacto else 0
    if not comentario and not contacto:
        raise NoSePuede("Escribí qué pasó o dejá un contacto.")
    estado = o["estado"]
    # Un 4 o 5 con un pedido de contacto también es algo que alguien tiene que mirar.
    if estado == "sin_accion" and quiere:
        estado = "abierta"
    conn = _con(db)
    try:
        conn.execute("UPDATE rs_opiniones SET comentario = ?, nombre = ?, contacto = ?, quiere_contacto = ?, "
                     "estado = ? WHERE id = ?", (comentario or None, nombre or None, contacto or None, quiere,
                                                 estado, o["id"]))
        conn.commit()
    finally:
        conn.close()
    return opinion(db, o["id"])


def ir_a_google(db: str, codigo: str) -> Optional[str]:
    """Anota el clic y devuelve adónde mandar. None si el comercio no cargó Google."""
    o = por_codigo(db, codigo)
    if not o:
        return None
    n = negocio(db, o["negocio_id"])
    if not n.get("google_url"):
        return None
    if not o["fue_a_google_en"]:
        conn = _con(db)
        try:
            conn.execute("UPDATE rs_opiniones SET fue_a_google_en = ? WHERE id = ?", (_txt(ahora_uy()), o["id"]))
            conn.commit()
        finally:
            conn.close()
    return n["google_url"]


# ── lo que hace el comercio ──────────────────────────────────────────────────

def opiniones(db: str, nid: int, filtro: str = "bandeja", limite: int = 200) -> list:
    """bandeja: lo que hay que mirar (abiertas y en curso, la más vieja primero);
    comentarios: todo lo que trae texto; todas: todo, lo último primero."""
    where, orden = "negocio_id = ?", "creado_en DESC, id DESC"
    if filtro == "bandeja":
        where += " AND estado IN ('abierta','en_curso')"
        orden = "creado_en, id"
    elif filtro == "comentarios":
        where += " AND comentario IS NOT NULL"
    elif filtro == "resueltas":
        where += " AND estado = 'resuelta'"
        orden = "resuelta_en DESC, id DESC"
    conn = _con(db)
    try:
        ids = [f[0] for f in conn.execute(f"SELECT id FROM rs_opiniones WHERE {where} ORDER BY {orden} LIMIT ?",
                                          (nid, max(1, min(int(limite), 500))))]
    finally:
        conn.close()
    return [opinion(db, i) for i in ids]


def atender(db: str, oid: int, datos: dict, quien: str, nid: Optional[int] = None) -> dict:
    o = opinion(db, oid)
    if nid is not None and o["negocio_id"] != nid:
        raise NoSePuede("La opinión no existe.")
    cambios = {}
    if "estado" in datos:
        e = str(datos.get("estado") or "")
        if e not in ESTADOS:
            raise NoSePuede("Estado no válido.")
        if e == "resuelta" and not _limpio(datos.get("nota", o.get("nota")), LARGO_NOTA):
            raise NoSePuede("Para cerrarla, anotá qué se hizo (le devolvimos, lo llamamos, se corrigió…).")
        cambios["estado"] = e
        cambios["resuelta_en"] = _txt(ahora_uy()) if e == "resuelta" else None
    if "nota" in datos:
        cambios["nota"] = _limpio(datos.get("nota"), LARGO_NOTA) or None
    if not cambios:
        return o
    cambios["atendido_por"] = quien
    conn = _con(db)
    try:
        conn.execute(f"UPDATE rs_opiniones SET {', '.join(k + ' = ?' for k in cambios)} WHERE id = ?",
                     [*cambios.values(), oid])
        conn.commit()
    finally:
        conn.close()
    return opinion(db, oid)


def whatsapp_respuesta(n: dict, o: dict) -> str:
    """wa.me al cliente con un primer mensaje armado. Vacío si no dejó celular."""
    tel = tel_normal(o.get("contacto"))
    if not tel or not o.get("quiere_contacto"):
        return ""
    hola = f"Hola {o['nombre'].split()[0]}" if o.get("nombre") else "Hola"
    texto = (f"{hola}, te escribo de {n['nombre']}. Leímos lo que nos contaste de tu visita y queremos "
             "solucionarlo. ¿Tenés un minuto para contarme un poco más?")
    return f"https://wa.me/{tel}?text={quote(texto)}"


def cargar_google(db: str, nid: int, datos: dict, quien: str) -> dict:
    """Una foto del puntaje de Google (el que se ve en Maps) para ver cómo se mueve."""
    negocio(db, nid)
    try:
        rating = round(float(str(datos.get("rating", "")).replace(",", ".")), 1)
        cantidad = int(str(datos.get("cantidad", "")).replace(".", "").strip())
    except ValueError:
        raise NoSePuede("Poné el puntaje (ej. 4,3) y la cantidad de reseñas que muestra Google.")
    if not 1 <= rating <= 5 or not 0 <= cantidad <= 1_000_000:
        raise NoSePuede("El puntaje va de 1 a 5 y la cantidad no puede ser negativa.")
    fecha = str(datos.get("fecha") or hoy_uy().isoformat())
    try:
        datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError:
        raise NoSePuede("La fecha no es válida.")
    conn = _con(db)
    try:
        conn.execute("INSERT INTO rs_google (negocio_id, fecha, rating, cantidad, cargado_por) VALUES (?,?,?,?,?)",
                     (nid, fecha, rating, cantidad, quien))
        conn.commit()
    finally:
        conn.close()
    return negocio(db, nid)


def borrar_google(db: str, nid: int, gid: int) -> None:
    conn = _con(db)
    try:
        conn.execute("DELETE FROM rs_google WHERE id = ? AND negocio_id = ?", (gid, nid))
        conn.commit()
    finally:
        conn.close()


# ── acceso del comercio ──────────────────────────────────────────────────────
#
# El dueño (o el encargado) entra a su panel con un link privado, sin usuario
# ni clave: /comercio/<token>. Como las API keys de Stripe, el token se muestra
# una sola vez al crearlo y en la base queda solo su hash: si se pierde, se
# hace otro y el viejo se revoca. Un link por persona, así la bandeja dice
# quién resolvió cada queja y se le puede cortar el acceso a uno solo.

MAX_ACCESOS = 10


def _hash_token(token: str) -> str:
    return hashlib.sha256(("rs-acceso|" + token).encode()).hexdigest()


def crear_acceso(db: str, nid: int, nombre: str, quien: str) -> dict:
    """Devuelve el acceso con el `token` en claro: es la única vez que se ve."""
    negocio(db, nid)
    nombre = _limpio(nombre, 60)
    if not nombre:
        raise NoSePuede("Poné para quién es el link: el dueño, la encargada, Martín…")
    token = secrets.token_urlsafe(24)
    conn = _con(db)
    try:
        if conn.execute("SELECT COUNT(*) FROM rs_accesos WHERE negocio_id = ? AND revocado_en IS NULL",
                        (nid,)).fetchone()[0] >= MAX_ACCESOS:
            raise NoSePuede(f"El comercio ya tiene {MAX_ACCESOS} links activos. Revocá alguno antes.")
        aid = conn.execute("INSERT INTO rs_accesos (negocio_id, nombre, token_hash, creado_por, creado_en) "
                           "VALUES (?,?,?,?,?)", (nid, nombre, _hash_token(token), quien, _txt(ahora_uy()))).lastrowid
        conn.commit()
    finally:
        conn.close()
    return {**acceso(db, aid), "token": token}


def acceso(db: str, aid: int) -> dict:
    conn = _con(db)
    try:
        a = conn.execute("SELECT id, negocio_id, nombre, creado_por, creado_en, visto_en, revocado_en, email, aviso "
                         "FROM rs_accesos WHERE id = ?", (aid,)).fetchone()
    finally:
        conn.close()
    if not a:
        raise NoSePuede("El acceso no existe.")
    return dict(a)


def accesos(db: str, nid: int) -> list:
    """Los activos primero; los revocados quedan para saber quién tuvo link."""
    conn = _con(db)
    try:
        return [dict(f) for f in conn.execute(
            "SELECT id, negocio_id, nombre, creado_por, creado_en, visto_en, revocado_en, email, aviso "
            "FROM rs_accesos "
            "WHERE negocio_id = ? ORDER BY revocado_en IS NOT NULL, id", (nid,))]
    finally:
        conn.close()


def revocar_acceso(db: str, nid: int, aid: int) -> None:
    conn = _con(db)
    try:
        if not conn.execute("UPDATE rs_accesos SET revocado_en = ? WHERE id = ? AND negocio_id = ? "
                            "AND revocado_en IS NULL", (_txt(ahora_uy()), aid, nid)).rowcount:
            raise NoSePuede("El acceso no existe o ya estaba revocado.")
        conn.commit()
    finally:
        conn.close()


def por_token(db: str, token: str, secreto: Optional[str] = None) -> Optional[dict]:
    """El acceso vigente de ese link, con su comercio, o None.

    Un comercio pausado sigue entrando: pausar corta las opiniones nuevas, no
    el panel. Uno borrado se lleva sus accesos (ON DELETE CASCADE). Con
    `secreto` también vale el link firmado de los mails de aviso
    (services/resenas_avisos.py), que se corta igual al revocar.
    """
    if not re.fullmatch(r"[\w-]{24,64}", token or ""):
        return None
    from services.resenas_avisos import acceso_firmado
    firmado = acceso_firmado(token, secreto) if secreto else None
    conn = _con(db)
    try:
        if firmado:
            a = conn.execute("SELECT id FROM rs_accesos WHERE id = ? AND revocado_en IS NULL", (firmado,)).fetchone()
        else:
            a = conn.execute("SELECT id FROM rs_accesos WHERE token_hash = ? AND revocado_en IS NULL",
                             (_hash_token(token),)).fetchone()
    finally:
        conn.close()
    if not a:
        return None
    a = acceso(db, a["id"])
    a["negocio"] = negocio(db, a["negocio_id"])
    return a


def marcar_visita(db: str, aid: int) -> Optional[str]:
    """Anota que entró y devuelve cuándo había entrado antes, para las novedades.

    Las visitas a menos de 30 minutos cuentan como la misma: recargar la página
    o resolver una queja no borra el "desde tu última visita".
    """
    ahora = ahora_uy()
    conn = _con(db)
    try:
        f = conn.execute("SELECT visto_en, visto_antes FROM rs_accesos WHERE id = ?", (aid,)).fetchone()
        visto = _dt(f["visto_en"]) if f else None
        if visto and ahora - visto < timedelta(minutes=30):
            conn.execute("UPDATE rs_accesos SET visto_en = ? WHERE id = ?", (_txt(ahora), aid))
            antes = f["visto_antes"]
        else:
            antes = f["visto_en"] if f else None
            conn.execute("UPDATE rs_accesos SET visto_antes = visto_en, visto_en = ? WHERE id = ?", (_txt(ahora), aid))
        conn.commit()
    finally:
        conn.close()
    return antes


def novedades(db: str, nid: int, desde: Optional[str]) -> dict:
    """Lo que entró desde la última visita: el "qué me perdí" del dueño."""
    if not desde:
        return {"desde": None, "opiniones": 0, "quejas": 0, "google": 0}
    conn = _con(db)
    try:
        f = conn.execute("SELECT COUNT(*) c, SUM(puntaje <= ?) q, SUM(fue_a_google_en IS NOT NULL) g "
                         "FROM rs_opiniones WHERE negocio_id = ? AND creado_en > ?",
                         (QUEJA_HASTA, nid, desde)).fetchone()
    finally:
        conn.close()
    return {"desde": desde, "opiniones": f["c"] or 0, "quejas": f["q"] or 0, "google": f["g"] or 0}


# ── métricas ─────────────────────────────────────────────────────────────────

def _pct(a, b) -> Optional[int]:
    return round(100 * a / b) if b else None


def metricas(db: str, nid: int, dias: int = 30) -> dict:
    n = negocio(db, nid)
    ahora = ahora_uy()
    desde = _txt(ahora - timedelta(days=dias))
    desde_dia = (ahora - timedelta(days=dias)).date().isoformat()
    conn = _con(db)
    try:
        ops = [dict(f) for f in conn.execute(
            "SELECT puntaje, fuente_id, fue_a_google_en, comentario, estado, creado_en, resuelta_en "
            "FROM rs_opiniones WHERE negocio_id = ? AND creado_en >= ?", (nid, desde))]
        vistas = {(f["fuente_id"] or 0): f["n"] for f in conn.execute(
            "SELECT fuente_id, SUM(n) n FROM rs_vistas WHERE negocio_id = ? AND dia >= ? GROUP BY IFNULL(fuente_id, 0)",
            (nid, desde_dia))}
        abiertas = conn.execute("SELECT COUNT(*) FROM rs_opiniones WHERE negocio_id = ? AND estado = 'abierta'",
                                (nid,)).fetchone()[0]
        en_curso = conn.execute("SELECT COUNT(*) FROM rs_opiniones WHERE negocio_id = ? AND estado = 'en_curso'",
                                (nid,)).fetchone()[0]
        semanas = []
        lunes = ahora.date() - timedelta(days=ahora.weekday())
        for i in range(7, -1, -1):
            a = lunes - timedelta(weeks=i)
            f = conn.execute("SELECT COUNT(*) c, AVG(puntaje) p, SUM(fue_a_google_en IS NOT NULL) g FROM rs_opiniones "
                             "WHERE negocio_id = ? AND creado_en >= ? AND creado_en < ?",
                             (nid, a.isoformat(), (a + timedelta(weeks=1)).isoformat())).fetchone()
            semanas.append({"desde": a.isoformat(), "opiniones": f["c"], "promedio": round(f["p"], 2) if f["p"] else None,
                            "google": f["g"] or 0})
    finally:
        conn.close()
    total = len(ops)
    dist = {str(i): sum(1 for o in ops if o["puntaje"] == i) for i in range(1, 6)}
    google = sum(1 for o in ops if o["fue_a_google_en"])
    quejas = [o for o in ops if o["puntaje"] <= QUEJA_HASTA]
    horas = sorted((_dt(o["resuelta_en"]) - _dt(o["creado_en"])).total_seconds() / 3600
                   for o in quejas if o["estado"] == "resuelta" and _dt(o["resuelta_en"]))
    v_total = sum(vistas.values())
    fuentes = [{"id": None, "nombre": "Link directo", "codigo": None}] + n["fuentes"]
    por_fuente = []
    for f in fuentes:
        de = [o for o in ops if (o["fuente_id"] or None) == f["id"]]
        v = vistas.get(f["id"] or 0, 0)
        if f["id"] is None and not de and not v:
            continue
        por_fuente.append({"id": f["id"], "nombre": f["nombre"], "vistas": v, "opiniones": len(de),
                           "google": sum(1 for o in de if o["fue_a_google_en"]),
                           "promedio": round(sum(o["puntaje"] for o in de) / len(de), 2) if de else None,
                           "conversion": _pct(len(de), v)})
    g = n["google"]
    evolucion = None
    if g:
        evolucion = {"primero": g[0], "ultimo": g[-1], "nuevas": g[-1]["cantidad"] - g[0]["cantidad"],
                     "delta_rating": round(g[-1]["rating"] - g[0]["rating"], 1)}
    return {
        "dias": dias, "vistas": v_total, "opiniones": total,
        "promedio": round(sum(o["puntaje"] for o in ops) / total, 2) if total else None,
        "distribucion": dist,
        "positivas_pct": _pct(dist["4"] + dist["5"], total),
        "google": google, "google_pct": _pct(google, total),
        "google_de_positivas_pct": _pct(sum(1 for o in ops if o["fue_a_google_en"] and o["puntaje"] >= 4),
                                        dist["4"] + dist["5"]),
        "respuesta_pct": _pct(total, v_total),
        "comentarios": sum(1 for o in ops if o["comentario"]),
        "quejas": len(quejas), "abiertas": abiertas, "en_curso": en_curso,
        "resueltas_pct": _pct(sum(1 for o in quejas if o["estado"] == "resuelta"), len(quejas)),
        "horas_resolucion": round(horas[len(horas) // 2], 1) if horas else None,
        "por_fuente": por_fuente, "semanas": semanas, "google_evolucion": evolucion,
    }
