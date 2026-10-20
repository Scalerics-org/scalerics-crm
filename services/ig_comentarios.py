"""Respuesta a los comentarios de Instagram.

Pedido de Juan (21/9): que cada comentario reciba una respuesta que derive al
WhatsApp de ventas. Revisa una vez por hora las ultimas publicaciones propias y
los anuncios activos y avisa por mail a contacto@, porque quien comenta es un
posible cliente.

**Quien escribe la respuesta (20/10):** hasta aca eran tres frases fijas que se
turnaban, y Juan noto que "siempre da la misma respuesta generica". Ahora cada
comentario nuevo queda `pendiente` con el texto de la publicacion o del anuncio
al lado, y lo contesta la sesion programada de Claude Code de la maquina de Juan
(`scripts/ig_correcciones.py comentarios`), igual que las correcciones: gratis,
sin la API de Anthropic. Si a la sesion le parece que tiene que contestar una
persona (una queja, algo que no sabe), lo `deriva` y sale el mail.

**Si la maquina esta apagada:** el comentario espera. Pasadas `ESPERA_HORAS`
sin respuesta, el servidor contesta con una de las frases de siempre, para que
nadie quede colgado. `IG_RESPUESTA_ESPERA_HORAS=0` saca ese respaldo.

**Cuantos hay:** medido el 21/9/2026, 3 comentarios en 152 anuncios y ninguno en
las ultimas 30 publicaciones.

**Que no responde:** los comentarios propios, los que ya tienen respuesta de la
cuenta, los de mas de 90 dias y cualquiera que ya este en `ig_comentarios`
(cada uno se responde una sola vez, pase lo que pase). Tope de 10 respaldos por
vuelta.

**Solo Instagram.** Los comentarios de Facebook piden `pages_read_user_content`,
que el token no tiene. `IG_RESPUESTAS=off` lo apaga; `IG_WHATSAPP` cambia el
numero.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

from database import _connect
from services.corridas import marcar_corrida, puede_correr

logger = logging.getLogger(__name__)

WHATSAPP_POR_DEFECTO = "097 250 713"
FRASES = (
    "¡Hola! Gracias por tu mensaje. Escribinos por WhatsApp al {wa} y te contamos cómo te podemos ayudar.",
    "¡Gracias por comentar! Para darte toda la info, escribinos por WhatsApp al {wa} y lo vemos juntos.",
    "¡Hola! Te respondemos enseguida por WhatsApp: escribinos al {wa} y coordinamos una videollamada.",
)
TOPE_POR_VUELTA = 10
MAX_DIAS = 90
ESPERA_HORAS = 24
MAX_LARGO = 600
PUBLICACIONES_A_MIRAR = 12
_JOB = "ig_comentarios"


class NoSePuede(Exception):
    pass


def activo() -> bool:
    return os.environ.get("IG_RESPUESTAS", "").strip().lower() != "off"


def whatsapp() -> str:
    return os.environ.get("IG_WHATSAPP", "").strip() or WHATSAPP_POR_DEFECTO


def espera_horas() -> int:
    try:
        return max(0, int(os.environ.get("IG_RESPUESTA_ESPERA_HORAS", ESPERA_HORAS)))
    except ValueError:
        return ESPERA_HORAS


def frase(n: int) -> str:
    return FRASES[n % len(FRASES)].format(wa=whatsapp())


def crear_tablas(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ig_comentarios (
            comment_id    TEXT PRIMARY KEY,
            media_id      TEXT NOT NULL,
            origen        TEXT,
            origen_nombre TEXT,
            usuario       TEXT,
            texto         TEXT,
            creado_en     TEXT,
            estado        TEXT NOT NULL,
            respuesta     TEXT,
            reply_id      TEXT,
            error         TEXT,
            procesado_en  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 20/10: el texto de la publicacion (para que la respuesta hable de eso) y
    # quien escribio la respuesta: 'claude' o 'frase' (el respaldo).
    columnas = {r[1] for r in conn.execute("PRAGMA table_info(ig_comentarios)")}
    for col in ("contexto", "por"):
        if col not in columnas:
            conn.execute(f"ALTER TABLE ig_comentarios ADD COLUMN {col} TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ig_media_conteo (
            media_id  TEXT PRIMARY KEY,
            conteo    INTEGER NOT NULL
        )
    """)


# ── Meta ─────────────────────────────────────────────────────────────────────

class Api:
    """Las llamadas a Meta, aparte para poder reemplazarlas en los tests."""

    def _get(self, ruta, **params):
        from services.instagram import _get
        return _get(ruta, **params)

    def _post(self, ruta, **params):
        from services.instagram import _post
        return _post(ruta, **params)

    def cuenta(self) -> tuple[str, str]:
        from services.instagram import _cuenta_ig
        ig = _cuenta_ig()
        return ig, self._get(ig, fields="username").get("username", "")

    def medias(self, ig: str) -> dict:
        """{media_id: (origen, nombre)}: publicaciones recientes y anuncios activos."""
        salida = {}
        for m in self._get(f"{ig}/media", limit=PUBLICACIONES_A_MIRAR, fields="id").get("data", []):
            salida[m["id"]] = ("publicacion", "Publicación")
        cuenta = os.environ["META_AD_ACCOUNT_ID"]
        ads = self._get(f"{cuenta}/ads", limit=200, effective_status='["ACTIVE"]',
                        fields="name,creative{effective_instagram_media_id}").get("data", [])
        for a in ads:
            mid = (a.get("creative") or {}).get("effective_instagram_media_id")
            if mid:
                salida.setdefault(mid, ("anuncio", a.get("name") or "Anuncio"))
        return salida

    def conteos(self, ids: list[str]) -> dict:
        """Una llamada por publicacion: el parametro `ids` de Meta dejo de andar en v26."""
        salida = {}
        for mid in ids:
            try:
                d = self._get(mid, fields="comments_count")
            except Exception as e:      # una publicacion borrada no frena a las demas
                logger.warning(f"Comentarios IG: no se pudo leer {mid} ({e})")
                continue
            salida[mid] = int((d or {}).get("comments_count") or 0)
        return salida

    def texto_de(self, media_id: str) -> str:
        """El texto de la publicacion o del anuncio, para darle contexto a la respuesta."""
        try:
            return (self._get(media_id, fields="caption").get("caption") or "")[:1500]
        except Exception as e:          # sin contexto igual se puede responder
            logger.warning(f"Comentarios IG: no se pudo leer el texto de {media_id} ({e})")
            return ""

    def comentarios(self, media_id: str) -> list[dict]:
        return self._get(f"{media_id}/comments", limit=50,
                         fields="id,text,username,timestamp,replies{username}").get("data", [])

    def responder(self, comment_id: str, texto: str) -> str:
        return self._post(f"{comment_id}/replies", message=texto)["id"]


# ── corrida ──────────────────────────────────────────────────────────────────

def _fecha(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _guardar(db_path: str, fila: tuple) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO ig_comentarios (comment_id, media_id, origen, origen_nombre, "
            "usuario, texto, creado_en, estado, error, contexto, procesado_en) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)", fila)
        conn.commit()
    finally:
        conn.close()


def _tomar(db_path: str, cid: str) -> dict | None:
    """Pasa un pendiente a 'respondiendo'. Si otro lo tomo antes, None.

    Sin esto el respaldo y la sesion de Claude podrian contestar el mismo
    comentario dos veces si coinciden en el mismo minuto.
    """
    conn = _connect(db_path)
    try:
        crear_tablas(conn)
        cur = conn.execute("UPDATE ig_comentarios SET estado = 'respondiendo' "
                           "WHERE comment_id = ? AND estado = 'pendiente'", (cid,))
        conn.commit()
        if not cur.rowcount:
            return None
        return dict(conn.execute("SELECT * FROM ig_comentarios WHERE comment_id = ?", (cid,)).fetchone())
    finally:
        conn.close()


def _contestar(db_path: str, c: dict, texto: str, por: str, api, avisar) -> dict:
    """Publica la respuesta de un comentario ya tomado, la anota y avisa por mail."""
    estado, reply_id, error = "respondido", None, None
    try:
        reply_id = api.responder(c["comment_id"], texto)
    except Exception as e:
        estado, error = "error", str(e)[:300]
    conn = _connect(db_path)
    try:
        conn.execute("UPDATE ig_comentarios SET estado = ?, respuesta = ?, reply_id = ?, error = ?, "
                     "por = ?, procesado_en = CURRENT_TIMESTAMP WHERE comment_id = ?",
                     (estado, texto, reply_id, error, por, c["comment_id"]))
        conn.commit()
    finally:
        conn.close()
    try:
        avisar(c["usuario"], c["texto"] or "", texto, estado, c["origen"], c["origen_nombre"])
    except Exception as e:
        logger.warning(f"Comentarios IG: no se pudo avisar por mail ({e})")
    return {**c, "estado": estado, "respuesta": texto, "reply_id": reply_id, "error": error, "por": por}


def revisar(db_path: str, ahora: datetime | None = None, api: Api | None = None,
            avisar=None) -> dict:
    """Anota los comentarios nuevos y contesta con frase los que esperaron demasiado."""
    from services.email_service import send_instagram_comentario

    ahora = ahora or datetime.now(timezone.utc)
    api = api or Api()
    avisar = avisar or send_instagram_comentario
    ig, propio = api.cuenta()
    medias = api.medias(ig)
    conteos = api.conteos(list(medias))

    conn = _connect(db_path)
    try:
        crear_tablas(conn)
        vistos = {r["media_id"]: r["conteo"] for r in conn.execute("SELECT * FROM ig_media_conteo")}
        ya = {r["comment_id"] for r in conn.execute("SELECT comment_id FROM ig_comentarios")}
    finally:
        conn.close()

    resumen = {"nuevos": 0, "respondidos": 0, "omitidos": 0, "errores": 0}
    limite = ahora - timedelta(days=MAX_DIAS)
    for mid, conteo in conteos.items():
        if conteo <= vistos.get(mid, 0):
            continue
        origen, nombre = medias.get(mid, ("publicacion", ""))
        contexto = None
        for c in api.comentarios(mid):
            cid = c.get("id")
            if not cid or cid in ya:
                continue
            usuario = c.get("username") or ""
            respuestas = (c.get("replies") or {}).get("data", [])
            fecha = _fecha(c.get("timestamp") or "")
            estado, error = "omitido", None
            if usuario == propio:
                error = "comentario propio"
            elif any(r.get("username") == propio for r in respuestas):
                error = "ya tenia respuesta"
            elif fecha and fecha < limite:
                error = f"de hace mas de {MAX_DIAS} dias"
            else:
                estado = "pendiente"
                if contexto is None:
                    contexto = api.texto_de(mid)
            _guardar(db_path, (cid, mid, origen, nombre, usuario, (c.get("text") or "")[:1000],
                               (c.get("timestamp") or "")[:19], estado, error,
                               contexto if estado == "pendiente" else None,
                               ahora.strftime("%Y-%m-%d %H:%M:%S")))
            ya.add(cid)
            resumen["nuevos" if estado == "pendiente" else "omitidos"] += 1
        conn = _connect(db_path)
        try:
            conn.execute("INSERT INTO ig_media_conteo (media_id, conteo) VALUES (?, ?) "
                         "ON CONFLICT(media_id) DO UPDATE SET conteo = excluded.conteo",
                         (mid, conteo))
            conn.commit()
        finally:
            conn.close()

    # el respaldo: lo que la sesion de Claude no contesto a tiempo
    horas = espera_horas()
    if horas:
        corte = (ahora - timedelta(hours=horas)).strftime("%Y-%m-%d %H:%M:%S")
        conn = _connect(db_path)
        try:
            viejos = [r["comment_id"] for r in conn.execute(
                "SELECT comment_id FROM ig_comentarios WHERE estado = 'pendiente' AND procesado_en <= ? "
                "ORDER BY procesado_en LIMIT ?", (corte, TOPE_POR_VUELTA))]
            n_frases = conn.execute("SELECT COUNT(*) FROM ig_comentarios WHERE por = 'frase'").fetchone()[0]
        finally:
            conn.close()
        for cid in viejos:
            c = _tomar(db_path, cid)
            if not c:
                continue
            hecho = _contestar(db_path, c, frase(n_frases), "frase", api, avisar)
            n_frases += 1
            resumen["respondidos" if hecho["estado"] == "respondido" else "errores"] += 1
    return resumen


def corrida(db_path: str, ahora: datetime | None = None, api=None, avisar=None) -> dict | None:
    """Una vez por hora, si hay credenciales y no esta apagado."""
    from services.meta_insights import hay_credenciales

    if not activo():
        return None
    if api is None and not (hay_credenciales() and os.environ.get("META_PAGE_ID")):
        return None
    if not puede_correr(db_path, _JOB, 1):
        return None
    marcar_corrida(db_path, _JOB)
    try:
        return revisar(db_path, ahora, api, avisar)
    except Exception as e:
        logger.warning(f"Comentarios IG: {e}")
        return {"error": str(e)[:200]}


# ── la sesion de Claude Code ─────────────────────────────────────────────────

def pendientes(db_path: str) -> list[dict]:
    """Lo que espera respuesta, con lo que hace falta para escribirla."""
    conn = _connect(db_path)
    try:
        crear_tablas(conn)
        filas = [dict(r) for r in conn.execute(
            "SELECT comment_id, origen, origen_nombre, contexto, usuario, texto, creado_en "
            "FROM ig_comentarios WHERE estado = 'pendiente' ORDER BY procesado_en")]
    finally:
        conn.close()
    return [{**f, "whatsapp": whatsapp()} for f in filas]


def responder(db_path: str, cid: str, texto: str, api=None, avisar=None) -> dict:
    """La sesion de Claude manda su respuesta: se publica en Instagram."""
    from services.email_service import send_instagram_comentario

    texto = (texto or "").strip()
    if not texto:
        raise NoSePuede("La respuesta está vacía.")
    if len(texto) > MAX_LARGO:
        raise NoSePuede(f"La respuesta pasa los {MAX_LARGO} caracteres.")
    c = _tomar(db_path, cid)
    if not c:
        raise NoSePuede("Ese comentario no está esperando respuesta.")
    return _contestar(db_path, c, texto, "claude", api or Api(), avisar or send_instagram_comentario)


def derivar(db_path: str, cid: str, motivo: str, avisar=None) -> dict:
    """La sesion de Claude prefiere que conteste una persona: no se publica nada."""
    from services.email_service import send_instagram_comentario

    motivo = (motivo or "").strip()[:300] or "mejor que conteste una persona"
    c = _tomar(db_path, cid)
    if not c:
        raise NoSePuede("Ese comentario no está esperando respuesta.")
    conn = _connect(db_path)
    try:
        conn.execute("UPDATE ig_comentarios SET estado = 'derivado', error = ?, por = 'claude', "
                     "procesado_en = CURRENT_TIMESTAMP WHERE comment_id = ?", (motivo, cid))
        conn.commit()
    finally:
        conn.close()
    try:
        (avisar or send_instagram_comentario)(c["usuario"], c["texto"] or "", None, "derivado",
                                              c["origen"], c["origen_nombre"], motivo)
    except Exception as e:
        logger.warning(f"Comentarios IG: no se pudo avisar por mail ({e})")
    return {**c, "estado": "derivado", "error": motivo}


def listar(db_path: str, limite: int = 50) -> list[dict]:
    conn = _connect(db_path)
    try:
        crear_tablas(conn)
        return [dict(r) for r in conn.execute(
            "SELECT * FROM ig_comentarios WHERE NOT (estado = 'omitido' AND error = 'comentario propio') "
            "ORDER BY creado_en DESC LIMIT ?", (limite,))]
    finally:
        conn.close()
