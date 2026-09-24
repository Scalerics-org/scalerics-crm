"""Respuesta automatica a los comentarios de Instagram.

Pedido de Juan (21/9): que cada comentario reciba una respuesta que derive al
WhatsApp de ventas. Revisa una vez por hora las ultimas publicaciones propias y
los anuncios activos, responde cada comentario nuevo con una de tres frases
(para que no parezca un robot ni Instagram lo tome por spam) y avisa por mail a
contacto@, porque quien comenta es un posible cliente.

**Cuantos hay:** medido el 21/9/2026, 3 comentarios en 152 anuncios y ninguno en
las ultimas 30 publicaciones. Por eso no hay cola de aprobacion: seria
administrar una cola casi vacia.

**Que no responde:** los comentarios propios, los que ya tienen respuesta de la
cuenta, los de mas de 90 dias y cualquiera que ya este en `ig_comentarios`
(cada uno se responde una sola vez, pase lo que pase). Tope de 10 por vuelta.

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
PUBLICACIONES_A_MIRAR = 12
_JOB = "ig_comentarios"


def activo() -> bool:
    return os.environ.get("IG_RESPUESTAS", "").strip().lower() != "off"


def whatsapp() -> str:
    return os.environ.get("IG_WHATSAPP", "").strip() or WHATSAPP_POR_DEFECTO


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


def revisar(db_path: str, ahora: datetime | None = None, api: Api | None = None,
            avisar=None) -> dict:
    """Busca comentarios nuevos y responde. Devuelve un resumen."""
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
        n_respondidos = conn.execute(
            "SELECT COUNT(*) FROM ig_comentarios WHERE estado = 'respondido'").fetchone()[0]
    finally:
        conn.close()

    resumen = {"respondidos": 0, "omitidos": 0, "errores": 0}
    limite = ahora - timedelta(days=MAX_DIAS)
    for mid, conteo in conteos.items():
        if conteo <= vistos.get(mid, 0):
            continue
        origen, nombre = medias.get(mid, ("publicacion", ""))
        completo = True
        for c in api.comentarios(mid):
            cid = c.get("id")
            if not cid or cid in ya:
                continue
            usuario = c.get("username") or ""
            respuestas = (c.get("replies") or {}).get("data", [])
            fecha = _fecha(c.get("timestamp") or "")
            estado, respuesta, reply_id, error = "omitido", None, None, None
            if usuario == propio:
                error = "comentario propio"
            elif any(r.get("username") == propio for r in respuestas):
                error = "ya tenia respuesta"
            elif fecha and fecha < limite:
                error = f"de hace mas de {MAX_DIAS} dias"
            elif resumen["respondidos"] + resumen["errores"] >= TOPE_POR_VUELTA:
                completo = False
                continue
            else:
                respuesta = frase(n_respondidos + resumen["respondidos"])
                try:
                    reply_id = api.responder(cid, respuesta)
                    estado = "respondido"
                except Exception as e:
                    estado, error = "error", str(e)[:300]
            conn = _connect(db_path)
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO ig_comentarios (comment_id, media_id, origen, origen_nombre, "
                    "usuario, texto, creado_en, estado, respuesta, reply_id, error) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, mid, origen, nombre, usuario, (c.get("text") or "")[:1000],
                     (c.get("timestamp") or "")[:19], estado, respuesta, reply_id, error))
                conn.commit()
            finally:
                conn.close()
            ya.add(cid)
            clave = {"respondido": "respondidos", "error": "errores"}.get(estado, "omitidos")
            resumen[clave] += 1
            if estado != "omitido":
                try:
                    avisar(usuario, c.get("text") or "", respuesta, estado, origen, nombre)
                except Exception as e:
                    logger.warning(f"Comentarios IG: no se pudo avisar por mail ({e})")
        if completo:
            conn = _connect(db_path)
            try:
                conn.execute("INSERT INTO ig_media_conteo (media_id, conteo) VALUES (?, ?) "
                             "ON CONFLICT(media_id) DO UPDATE SET conteo = excluded.conteo",
                             (mid, conteo))
                conn.commit()
            finally:
                conn.close()
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


def listar(db_path: str, limite: int = 50) -> list[dict]:
    conn = _connect(db_path)
    try:
        crear_tablas(conn)
        return [dict(r) for r in conn.execute(
            "SELECT * FROM ig_comentarios WHERE NOT (estado = 'omitido' AND error = 'comentario propio') "
            "ORDER BY creado_en DESC LIMIT ?", (limite,))]
    finally:
        conn.close()
