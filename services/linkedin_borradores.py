"""Los borradores de LinkedIn en el CRM, semana por semana (panel LinkedIn).

Pedido de Juan (15/9): "todas las semanas arma dos plantillas nuevas para
linkedin, ya nos estan llegando al mail pero quiero que esten ahi para subir a
la pagina de scalerics de linkedin".

Cómo se generan NO cambia: el cron de GitHub (`.github/workflows/linkedin.yml`)
corre martes y viernes a las 08:00 de Montevideo, cada corrida saca dos posts
del banco ya escrito (`linkedin_banco`, sin IA), el runner renderiza la tarjeta
de cada uno y el mail sale igual que antes. Son cuatro por semana, no dos.

Lo único nuevo es que cada borrador además queda en `linkedin_borradores`
(cuando el job lo arma) con su imagen (cuando el runner la manda para el mail),
y ahí se copia, se edita, se descarga la imagen y se marca como publicado.

Las semanas son de lunes a domingo en hora de Montevideo.
"""

import base64
import binascii
import logging
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytz

logger = logging.getLogger(__name__)

MVD = pytz.timezone("America/Montevideo")
LIMITE_LINKEDIN = 3000
ESTADOS = ("borrador", "publicado", "descartado")
_FORMATO = "%Y-%m-%d %H:%M:%S"
_HASHTAG = re.compile(r"#\w+")
_FIRMA_PNG = b"\x89PNG\r\n\x1a\n"
_TOPE_IMAGEN = 5 * 1024 * 1024

# Lo que dice el cron: martes (1) y viernes (4) a las 08:00 de Montevideo.
DIAS_DE_GENERACION = (1, 4)
HORA_DE_GENERACION = 8
_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# ── fechas ───────────────────────────────────────────────────────────────────

def ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


def a_montevideo(momento: datetime) -> datetime:
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(MVD)


def lunes_de(dia: date) -> date:
    return dia - timedelta(days=dia.weekday())


def semana_de(momento: datetime) -> str:
    """El lunes (AAAA-MM-DD) de la semana de Montevideo en la que cae `momento`."""
    return lunes_de(a_montevideo(momento).date()).isoformat()


def hoy_montevideo(ahora: datetime | None = None) -> date:
    return a_montevideo(ahora or ahora_utc()).date()


def semana_actual(ahora: datetime | None = None) -> str:
    return lunes_de(hoy_montevideo(ahora)).isoformat()


def parse_fecha(texto) -> date | None:
    crudo = str(texto or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", crudo):
        return None
    try:
        return date.fromisoformat(crudo)
    except ValueError:
        return None


def _utc_de_texto(texto) -> datetime | None:
    """Las fechas de linkedin_posts: CURRENT_TIMESTAMP (UTC) o un isoformat sin zona."""
    try:
        momento = datetime.fromisoformat(str(texto).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)


def proxima_generacion(ahora: datetime | None = None) -> str:
    """'viernes 18/09 a las 08:00': la próxima corrida del cron."""
    local = a_montevideo(ahora or ahora_utc())
    for dias in range(0, 8):
        dia = local.date() + timedelta(days=dias)
        if dia.weekday() not in DIAS_DE_GENERACION:
            continue
        momento = MVD.localize(datetime(dia.year, dia.month, dia.day, HORA_DE_GENERACION))
        if momento > local:
            return f"{_DIAS[dia.weekday()]} {dia:%d/%m} a las {HORA_DE_GENERACION:02d}:00"
    return ""


# ── texto ────────────────────────────────────────────────────────────────────

def hashtags_de(texto: str | None) -> str:
    """Los hashtags del post, sin repetir. No vienen aparte: van dentro del texto."""
    vistos: list[str] = []
    for etiqueta in _HASHTAG.findall(texto or ""):
        if etiqueta.lower() not in (v.lower() for v in vistos):
            vistos.append(etiqueta)
    return " ".join(vistos)


def tema_de(borrador: dict) -> str:
    desc = (borrador.get("fuente_desc") or "").strip()
    if desc.startswith("Tema educativo:"):
        return desc[len("Tema educativo:"):].strip()
    if desc.startswith("Pedido a mano:"):
        return "Post sobre trabajo real"
    return desc


# ── guardar ──────────────────────────────────────────────────────────────────

def _siguiente_orden(conn: sqlite3.Connection, semana: str) -> int:
    return conn.execute("SELECT COALESCE(MAX(orden), 0) + 1 FROM linkedin_borradores WHERE semana = ?",
                        (semana,)).fetchone()[0]


def guardar_borradores(db_path: str, borradores: list[dict], ahora: datetime | None = None) -> int:
    """Guarda los borradores recién generados. Devuelve cuántos entraron.

    Idempotente por post: el mismo borrador (mismo id de linkedin_posts) no
    entra dos veces aunque se llame de nuevo. Cada uno toma el siguiente orden
    libre de su semana, así que la corrida del martes queda 1 y 2 y la del
    viernes 3 y 4. El texto se guarda tal cual, sin recortar.
    """
    momento = ahora or ahora_utc()
    semana = semana_de(momento)
    creado = momento.astimezone(timezone.utc).strftime(_FORMATO)
    nuevos = 0
    conn = _conn(db_path)
    try:
        for b in borradores:
            texto = b.get("texto") or ""
            if not texto.strip():
                continue
            post_id = b.get("id")
            if post_id is not None and conn.execute(
                    "SELECT 1 FROM linkedin_borradores WHERE post_id = ?", (post_id,)).fetchone():
                continue
            for _ in range(3):
                try:
                    conn.execute(
                        "INSERT INTO linkedin_borradores (post_id, semana, orden, tema, texto, texto_original, "
                        "hashtags, estado, created_at) VALUES (?,?,?,?,?,?,?,'borrador',?)",
                        (post_id, semana, _siguiente_orden(conn, semana), tema_de(b), texto, texto,
                         hashtags_de(texto), creado))
                    nuevos += 1
                    break
                except sqlite3.IntegrityError:
                    # Otra corrida tomó el mismo orden en el medio: se reintenta
                    # con el siguiente. Si lo que chocó es el post, ya está.
                    if post_id is not None and conn.execute(
                            "SELECT 1 FROM linkedin_borradores WHERE post_id = ?", (post_id,)).fetchone():
                        break
        conn.commit()
    finally:
        conn.close()
    return nuevos


def guardar_imagenes(db_path: str, imagenes: dict) -> int:
    """Las tarjetas que renderizó el runner, por id de linkedin_posts.

    Llegan en base64 al mismo pedido que manda el mail. Se guarda solo lo que
    es un PNG de verdad y de un tamaño razonable; lo demás se ignora.
    """
    guardadas = 0
    conn = _conn(db_path)
    try:
        for post_id, crudo in (imagenes or {}).items():
            if not crudo or not isinstance(crudo, str):
                continue
            try:
                png = base64.b64decode(crudo, validate=True)
            except (ValueError, binascii.Error):
                continue
            if not png.startswith(_FIRMA_PNG) or len(png) > _TOPE_IMAGEN:
                continue
            guardadas += conn.execute("UPDATE linkedin_borradores SET imagen_png = ? WHERE post_id = ?",
                                      (sqlite3.Binary(png), post_id)).rowcount
        conn.commit()
    finally:
        conn.close()
    return guardadas


def copiar_historico(conn: sqlite3.Connection) -> int:
    """Una sola vez, al crear la tabla: lo que ya se generó está en linkedin_posts.

    Los que el link del mail marcó como publicados entran publicados; el resto,
    como borrador. Las imágenes de esas semanas no se guardaron nunca (solo
    viajaron adjuntas al mail), así que los históricos entran sin imagen.
    """
    filas = conn.execute(
        "SELECT id, texto, fuente_desc, estado, creado_en, publicado_en FROM linkedin_posts "
        "ORDER BY creado_en, id").fetchall()
    ordenes: dict[str, int] = {}
    copiadas = 0
    for f in filas:
        creado = _utc_de_texto(f[4]) or ahora_utc()
        semana = semana_de(creado)
        if semana not in ordenes:
            ordenes[semana] = _siguiente_orden(conn, semana) - 1
        ordenes[semana] += 1
        publicado = f[3] == "publicado"
        cuando = _utc_de_texto(f[5]) if publicado else None
        cur = conn.execute(
            "INSERT OR IGNORE INTO linkedin_borradores (post_id, semana, orden, tema, texto, texto_original, "
            "hashtags, estado, publicado_en, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f[0], semana, ordenes[semana], tema_de({"fuente_desc": f[2]}), f[1] or "", f[1] or "",
             hashtags_de(f[1]), "publicado" if publicado else "borrador",
             a_montevideo(cuando).date().isoformat() if cuando else None,
             creado.astimezone(timezone.utc).strftime(_FORMATO)))
        copiadas += cur.rowcount
    if copiadas:
        logger.info(f"linkedin_borradores: {copiadas} borradores históricos copiados de linkedin_posts")
    return copiadas


# ── pantalla ─────────────────────────────────────────────────────────────────

_CAMPOS = ("id, post_id, semana, orden, tema, texto, hashtags, estado, publicado_en, "
           "editado_por, editado_en, created_at, imagen_png IS NOT NULL AS tiene_imagen")


def _a_dict(fila: sqlite3.Row) -> dict:
    d = dict(fila)
    d["tiene_imagen"] = bool(d.get("tiene_imagen"))
    d["caracteres"] = len(d["texto"] or "")
    d["pasa_limite"] = d["caracteres"] > LIMITE_LINKEDIN
    return d


def listar_semana(db_path: str, semana: str) -> list[dict]:
    conn = _conn(db_path)
    try:
        return [_a_dict(f) for f in conn.execute(
            f"SELECT {_CAMPOS} FROM linkedin_borradores WHERE semana = ? ORDER BY orden", (semana,))]
    finally:
        conn.close()


def obtener(db_path: str, borrador_id: int) -> dict | None:
    conn = _conn(db_path)
    try:
        fila = conn.execute(f"SELECT {_CAMPOS} FROM linkedin_borradores WHERE id = ?", (borrador_id,)).fetchone()
    finally:
        conn.close()
    return _a_dict(fila) if fila else None


def imagen_de(db_path: str, borrador_id: int) -> tuple[bytes, str, int] | None:
    conn = _conn(db_path)
    try:
        fila = conn.execute("SELECT imagen_png, semana, orden FROM linkedin_borradores WHERE id = ?",
                            (borrador_id,)).fetchone()
    finally:
        conn.close()
    if not fila or fila["imagen_png"] is None:
        return None
    return bytes(fila["imagen_png"]), fila["semana"], fila["orden"]


def editar(db_path: str, borrador_id: int, texto: str, quien: str) -> dict | None:
    """Guarda la versión editada. La original queda en `texto_original`."""
    conn = _conn(db_path)
    try:
        cur = conn.execute(
            "UPDATE linkedin_borradores SET texto = ?, hashtags = ?, editado_por = ?, editado_en = ? WHERE id = ?",
            (texto, hashtags_de(texto), quien or None, ahora_utc().strftime(_FORMATO), borrador_id))
        conn.commit()
    finally:
        conn.close()
    return obtener(db_path, borrador_id) if cur.rowcount else None


def cambiar_estado(db_path: str, borrador_id: int, estado: str, fecha: date | None = None,
                   ahora: datetime | None = None) -> dict | None:
    """Publicado lleva fecha (la elegida, o hoy en Montevideo). Los otros la limpian.

    Publicar también marca la fila de linkedin_posts, igual que el link del
    mail: así los dos caminos dicen lo mismo.
    """
    actual = obtener(db_path, borrador_id)
    if not actual:
        return None
    publicado_en = (fecha or hoy_montevideo(ahora)).isoformat() if estado == "publicado" else None
    conn = _conn(db_path)
    try:
        conn.execute("UPDATE linkedin_borradores SET estado = ?, publicado_en = ? WHERE id = ?",
                     (estado, publicado_en, borrador_id))
        if estado == "publicado" and actual["post_id"]:
            conn.execute("UPDATE linkedin_posts SET estado = 'publicado', publicado_en = COALESCE(publicado_en, ?) "
                         "WHERE id = ?", (publicado_en, actual["post_id"]))
        conn.commit()
    finally:
        conn.close()
    return obtener(db_path, borrador_id)


def marcar_publicado_por_post(db_path: str, post_id: int, ahora: datetime | None = None) -> None:
    """Lo llama el link "ya lo publiqué" del mail."""
    conn = _conn(db_path)
    try:
        conn.execute(
            "UPDATE linkedin_borradores SET estado = 'publicado', publicado_en = COALESCE(publicado_en, ?) "
            "WHERE post_id = ? AND estado <> 'publicado'",
            (hoy_montevideo(ahora).isoformat(), post_id))
        conn.commit()
    finally:
        conn.close()
