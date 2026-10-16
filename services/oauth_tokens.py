"""Códigos y tokens del OAuth del conector MCP para Claude.

El CRM es a la vez servidor de autorización y de recurso: emite los tokens que
Claude le presenta después en `/mcp`. Son opacos (bytes al azar), y en la base
vive solo su SHA-256: un volcado de `leads.db` no alcanza para entrar.

Duraciones: el código de autorización vence a los 10 minutos, el access a la
hora, el refresh a los 30 días. El refresh se rota en cada uso (OAuth 2.1 lo
exige para clientes públicos, y Claude se registra como público).

Una familia (todo lo que nace de un mismo código, rotación tras rotación) vive
como mucho 90 días desde el canje. Sin ese tope cada rotación volvía a dar 30
días, y un refresh robado que el ladrón siguiera rotando no vencía nunca. Pasados
los 90 días la persona vuelve a autorizar a Claude en la pantalla de
consentimiento. El refresh vence en `min(ahora + 30 días, familia_en + 90 días)`.

Un código reusado no solo falla: revoca lo que se emitió con él. Es la defensa
de OAuth 2.1 §4.1.3 contra un código robado que llega segundo.

Usuarios borrados: las tablas declaran `ON DELETE CASCADE`, pero `database._connect`
no activa `PRAGMA foreign_keys`, así que hoy la cascada no corre. Queda declarada
por si algún día se activa el pragma; lo que protege hoy es el JOIN con `users`
en `canjear_codigo`, `validar_access` y `refrescar`: un token o código de un
usuario que ya no existe devuelve None.
"""

import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from database import _connect

SCOPE_LECTURA = "crm:leer"
CODIGO_DURA = timedelta(minutes=10)
ACCESS_DURA = timedelta(hours=1)
REFRESH_DURA = timedelta(days=30)
# Tope de la familia entera, contado desde el canje del código.
FAMILIA_DURA = timedelta(days=90)
# Tokens cuyo refresh venció hace más de esto se borran en el barrido.
BASURA_DESPUES_DE = timedelta(days=30)
# `ultimo_uso` se escribe como mucho una vez por minuto, para no escribir en
# SQLite en cada llamada de Claude.
ULTIMO_USO_CADA = timedelta(minutes=1)

# RFC 7636 §4.1: el verifier son 43 a 128 caracteres del alfabeto "unreserved";
# el challenge S256 es un SHA-256 en base64url sin relleno, siempre de 43.
_VERIFIER_RE = re.compile(r"[A-Za-z0-9\-._~]{43,128}")
CHALLENGE_RE = re.compile(r"[A-Za-z0-9\-_]{43}")


def _antes_de_revocar() -> None:
    """Gancho vacío que `refrescar` llama entre leer el refresh y revocarlo.
    Existe para que un test pueda forzar la carrera entre dos hilos."""


def base_url() -> str:
    """La URL pública del CRM, sin barra final. Es el emisor OAuth y la base de
    todos los endpoints anunciados. Nunca se arma desde el `Host` del request:
    un header forjado no puede cambiar lo que anunciamos."""
    return os.environ.get("CRM_URL", "https://scalerics-crm.fly.dev").rstrip("/")


def recurso_mcp() -> str:
    """El identificador del recurso protegido. Tiene que ser exactamente la URL
    que la persona escribe en claude.ai."""
    return base_url() + "/mcp"


def init_oauth(conn: sqlite3.Connection) -> None:
    """Tablas del OAuth. Lo llama `database.init_db` con su conexión."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_clientes (
            client_id      TEXT PRIMARY KEY,
            client_name    TEXT,
            redirect_uris  TEXT NOT NULL,
            created_at     TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_codigos (
            code_hash       TEXT PRIMARY KEY,
            user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            client_id       TEXT NOT NULL,
            redirect_uri    TEXT NOT NULL,
            code_challenge  TEXT NOT NULL,
            scope           TEXT NOT NULL,
            resource        TEXT,
            expires_at      TEXT NOT NULL,
            used_at         TEXT,
            created_at      TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_tokens (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            client_id        TEXT NOT NULL,
            codigo_hash      TEXT,
            access_hash      TEXT NOT NULL UNIQUE,
            refresh_hash     TEXT NOT NULL UNIQUE,
            scope            TEXT NOT NULL,
            resource         TEXT,
            access_expira    TEXT NOT NULL,
            refresh_expira   TEXT NOT NULL,
            ultimo_uso       TEXT,
            revocado_en      TEXT,
            familia_en       TEXT,
            created_at       TEXT NOT NULL
        )
    """)


# ── utilidades ───────────────────────────────────────────────────────────────

def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(momento: datetime) -> str:
    return momento.isoformat(timespec="seconds")


def _hash(valor: str) -> str:
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()


def _secreto() -> str:
    return secrets.token_urlsafe(32)


def _mismo_cliente(esperado: str, recibido: Optional[str]) -> bool:
    """`client_id` viene del request y puede traer cualquier carácter:
    `compare_digest` sobre `str` revienta con no-ASCII, sobre bytes no."""
    return hmac.compare_digest(esperado.encode(), (recibido or "").encode())


def verificar_pkce(code_verifier: str, code_challenge: str) -> bool:
    """S256: BASE64URL(SHA256(verifier)) sin relleno tiene que ser el challenge."""
    # Se valida el alfabeto antes de codificar: así no se descarta nada en
    # silencio y `compare_digest` nunca recibe texto que no sea ASCII.
    if not _VERIFIER_RE.fullmatch(code_verifier or ""):
        return False
    if not CHALLENGE_RE.fullmatch(code_challenge or ""):
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    calculado = base64.urlsafe_b64encode(digest).rstrip(b"=")
    return hmac.compare_digest(calculado, code_challenge.encode("ascii"))


# ── códigos ──────────────────────────────────────────────────────────────────

def emitir_codigo(db: str, user_id: int, client_id: str, redirect_uri: str,
                  code_challenge: str, scope: str, resource: Optional[str]) -> str:
    """Devuelve el código en claro para mandárselo a Claude; guarda el hash."""
    code = _secreto()
    ahora = _ahora()
    conn = _connect(db)
    try:
        conn.execute(
            "INSERT INTO oauth_codigos (code_hash, user_id, client_id, redirect_uri, "
            "code_challenge, scope, resource, expires_at, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (_hash(code), user_id, client_id, redirect_uri, code_challenge, scope, resource,
             _iso(ahora + CODIGO_DURA), _iso(ahora)))
        _barrer(conn, ahora)
        conn.commit()
    finally:
        conn.close()
    return code


def canjear_codigo(db: str, code: str, client_id: str, redirect_uri: str,
                   code_verifier: str) -> Optional[dict]:
    """Cambia un código por access + refresh. None es `invalid_grant`."""
    code_hash = _hash(code or "")
    ahora = _ahora()
    conn = _connect(db)
    try:
        fila = conn.execute(
            "SELECT c.* FROM oauth_codigos c JOIN users u ON u.id = c.user_id "
            "WHERE c.code_hash = ?", (code_hash,)).fetchone()
        if not fila:
            return None
        if fila["used_at"]:
            # Segundo canje del mismo código: alguien lo robó, o Claude reintentó.
            # En los dos casos lo emitido con él deja de valer.
            conn.execute("UPDATE oauth_tokens SET revocado_en = ? WHERE codigo_hash = ? "
                         "AND revocado_en IS NULL", (_iso(ahora), code_hash))
            conn.commit()
            return None
        if fila["expires_at"] < _iso(ahora):
            return None
        if not _mismo_cliente(fila["client_id"], client_id):
            return None
        if fila["redirect_uri"] != (redirect_uri or ""):
            return None
        if not verificar_pkce(code_verifier, fila["code_challenge"]):
            return None
        marcado = conn.execute(
            "UPDATE oauth_codigos SET used_at = ? WHERE code_hash = ? AND used_at IS NULL",
            (_iso(ahora), code_hash))
        if marcado.rowcount != 1:
            return None
        # El canje es el nacimiento de la familia: de acá se cuentan los 90 días.
        tokens = _emitir_tokens(conn, fila["user_id"], fila["client_id"], fila["scope"],
                                fila["resource"], code_hash, ahora, familia_en=ahora)
        conn.commit()
        return tokens
    finally:
        conn.close()


def _emitir_tokens(conn: sqlite3.Connection, user_id: int, client_id: str, scope: str,
                   resource: Optional[str], codigo_hash: Optional[str], ahora: datetime,
                   familia_en: datetime) -> dict:
    """`familia_en` es cuándo se canjeó el código del que viene este par. El
    refresh no pasa nunca de `familia_en + FAMILIA_DURA`, por más que se rote."""
    access, refresh = _secreto(), _secreto()
    refresh_expira = min(ahora + REFRESH_DURA, familia_en + FAMILIA_DURA)
    conn.execute(
        "INSERT INTO oauth_tokens (user_id, client_id, codigo_hash, access_hash, refresh_hash, "
        "scope, resource, access_expira, refresh_expira, familia_en, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (user_id, client_id, codigo_hash, _hash(access), _hash(refresh), scope, resource,
         _iso(ahora + ACCESS_DURA), _iso(refresh_expira), _iso(familia_en), _iso(ahora)))
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": int(ACCESS_DURA.total_seconds()),
        "scope": scope,
    }


def _barrer(conn: sqlite3.Connection, ahora: datetime) -> None:
    """Borra códigos vencidos y tokens muertos hace rato. Corre al emitir, no en
    un hilo: COORDINACION.md regla 3, nada arranca solo en el boot."""
    conn.execute("DELETE FROM oauth_codigos WHERE expires_at < ?", (_iso(ahora),))
    conn.execute("DELETE FROM oauth_tokens WHERE refresh_expira < ?",
                 (_iso(ahora - BASURA_DESPUES_DE),))


# ── access / refresh ─────────────────────────────────────────────────────────

def validar_access(db: str, token: str) -> Optional[dict]:
    """El usuario, cliente, scope y resource detrás de un access token vivo, o
    None. Quién llama decide si el scope y el resource le sirven.

    Actualiza `ultimo_uso` como mucho una vez por minuto: sirve para el panel
    de tokens que vendrá, y no vale la pena una escritura por llamada."""
    if not token:
        return None
    ahora = _ahora()
    conn = _connect(db)
    try:
        fila = conn.execute(
            "SELECT t.id, t.user_id, t.client_id, t.scope, t.resource, t.access_expira, "
            "t.ultimo_uso "
            "FROM oauth_tokens t JOIN users u ON u.id = t.user_id "
            "WHERE t.access_hash = ? AND t.revocado_en IS NULL", (_hash(token),)).fetchone()
        if not fila or fila["access_expira"] < _iso(ahora):
            return None
        if not fila["ultimo_uso"] or fila["ultimo_uso"] < _iso(ahora - ULTIMO_USO_CADA):
            conn.execute("UPDATE oauth_tokens SET ultimo_uso = ? WHERE id = ?",
                         (_iso(ahora), fila["id"]))
            conn.commit()
        return {"token_id": fila["id"], "user_id": fila["user_id"],
                "client_id": fila["client_id"], "scope": fila["scope"],
                "resource": fila["resource"]}
    finally:
        conn.close()


def refrescar(db: str, refresh_token: str, client_id: str) -> Optional[dict]:
    """Nuevo par access + refresh; el refresh usado queda revocado en la misma
    transacción. None es `invalid_grant`."""
    if not refresh_token:
        return None
    ahora = _ahora()
    conn = _connect(db)
    try:
        fila = conn.execute(
            "SELECT t.* FROM oauth_tokens t JOIN users u ON u.id = t.user_id "
            "WHERE t.refresh_hash = ?", (_hash(refresh_token),)).fetchone()
        if not fila:
            return None
        if fila["revocado_en"]:
            # OAuth 2.1 §4.3.1: un refresh ya rotado que vuelve a aparecer es
            # robo o reintento, y en los dos casos la familia muere.
            if fila["codigo_hash"] is not None:
                conn.execute("UPDATE oauth_tokens SET revocado_en = ? WHERE codigo_hash = ? "
                             "AND revocado_en IS NULL", (_iso(ahora), fila["codigo_hash"]))
                conn.commit()
            return None
        if fila["refresh_expira"] < _iso(ahora):
            return None
        if not _mismo_cliente(fila["client_id"], client_id):
            return None
        _antes_de_revocar()
        # Revocado condicional y con rowcount, igual que `canjear_codigo`:
        # gunicorn corre 4 hilos, y un reintento de Claude o un refresh robado
        # presentado a la vez no pueden emitir dos pares con el mismo refresh.
        rev = conn.execute("UPDATE oauth_tokens SET revocado_en = ? "
                           "WHERE id = ? AND revocado_en IS NULL", (_iso(ahora), fila["id"]))
        if rev.rowcount != 1:
            return None
        # La familia se hereda: rotar no reinicia el reloj de los 90 días.
        tokens = _emitir_tokens(conn, fila["user_id"], fila["client_id"], fila["scope"],
                                fila["resource"], fila["codigo_hash"], ahora,
                                familia_en=datetime.fromisoformat(fila["familia_en"]))
        _barrer(conn, ahora)
        conn.commit()
        return tokens
    finally:
        conn.close()


def revocar(db: str, token: str) -> None:
    """RFC 7009: revoca el access o refresh que coincida, y con él su pareja.
    No dice si existía: la respuesta es 200 siempre."""
    if not token:
        return
    h = _hash(token)
    conn = _connect(db)
    try:
        conn.execute(
            "UPDATE oauth_tokens SET revocado_en = ? WHERE revocado_en IS NULL "
            "AND (access_hash = ? OR refresh_hash = ?)", (_iso(_ahora()), h, h))
        conn.commit()
    finally:
        conn.close()


def revocar_todos_de_usuario(db: str, user_id: int) -> None:
    """Corta todo acceso de Claude a nombre de esa persona: tokens vivos y códigos
    pendientes. Se llama al cambiar la contraseña; si no, quien la robó y ya
    conectó a Claude seguiría leyendo el CRM hasta que venza su familia de tokens.
También la llama el botón "Cortar acceso de Claude" del panel de usuarios."""
    conn = _connect(db)
    try:
        conn.execute("UPDATE oauth_tokens SET revocado_en = ? "
                     "WHERE user_id = ? AND revocado_en IS NULL", (_iso(_ahora()), user_id))
        conn.execute("DELETE FROM oauth_codigos WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def tokens_vivos_por_usuario(db: str) -> dict[int, int]:
    """Cuántos tokens vivos (sin revocar y con el refresh vigente) tiene cada
    persona. Lo muestra el panel de usuarios al lado del botón para cortar el
    acceso. Una sola consulta agrupada, no una por usuario. Quien no tiene
    ninguno no aparece en el dict."""
    conn = _connect(db)
    try:
        filas = conn.execute(
            "SELECT user_id, COUNT(*) AS n FROM oauth_tokens "
            "WHERE revocado_en IS NULL AND refresh_expira > ? GROUP BY user_id",
            (_iso(_ahora()),)).fetchall()
    finally:
        conn.close()
    return {f["user_id"]: f["n"] for f in filas}
