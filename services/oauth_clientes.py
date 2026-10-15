"""Qué clientes OAuth acepta el CRM para el conector MCP.

Dos caminos, en este orden:

1. **CIMD** (Client ID Metadata Document): Claude se identifica con una URL
   `https://` que sirve un JSON con sus `redirect_uris`. Solo se aceptan
   documentos alojados en claude.ai o claude.com: es un CRM privado, no hace
   falta confiar en cualquier cliente de internet. Se descarga con timeout
   corto (claude.ai espera 10 s por el flujo entero) y se cachea un día.

2. **DCR** (registro dinámico, RFC 7591): si la metadata no anunciara CIMD, o
   un cliente no lo usara, Claude se registra en `/oauth/register` y queda en
   `oauth_clientes`. Hay un tope porque cada conexión nueva registra un cliente
   nuevo y la tabla crecería sin fin.

La comparación de `redirect_uri` es exacta. Única excepción: las loopback de
Claude Code (`http://localhost/callback`, `http://127.0.0.1/callback`), que
llegan con un puerto distinto cada vez y se comparan sin él (RFC 8252 §7.3).
"""

import json
import logging
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit

import requests

from database import _connect

logger = logging.getLogger(__name__)

HOSTS_CIMD = ("claude.ai", "claude.com")
REDIRECTS_LOOPBACK = ("http://localhost/callback", "http://127.0.0.1/callback")
HOSTS_LOOPBACK = ("localhost", "127.0.0.1")
CACHE_SEGUNDOS = 24 * 3600
# Un CIMD que falla se recuerda poco: lo justo para que un flood de client_id
# inventados bajo claude.ai no dispare un pedido saliente de hasta 5 s cada uno,
# pero sin dejar a Claude afuera si su documento tuvo un hipo.
CACHE_FALLO_SEGUNDOS = 60
TIMEOUT_CIMD = 5
TOPE_CLIENTES = 200
TOPE_REDIRECTS = 10
TOPE_CACHE = 1000
LARGO_MAX_REDIRECT = 2048

# client_id -> (vence, redirect_uris o None si el documento no sirvió).
_cache_cimd: dict[str, tuple[float, Optional[list[str]]]] = {}


def limpiar_cache() -> None:
    """Para los tests."""
    _cache_cimd.clear()


def es_cimd(client_id: str) -> bool:
    partes = urlsplit(client_id or "")
    return partes.scheme == "https" and partes.hostname in HOSTS_CIMD


def _descargar_cimd(client_id: str) -> Optional[list[str]]:
    try:
        # Sin redirects: el CIMD real se sirve directo, y seguir un 30x podría
        # llevarnos a un host fuera de la lista de claude.ai / claude.com.
        resp = requests.get(client_id, timeout=TIMEOUT_CIMD, allow_redirects=False)
        resp.raise_for_status()
        doc = resp.json()
    except Exception as exc:
        logger.warning("CIMD %s: no se pudo descargar: %s", client_id, exc)
        return None
    uris = doc.get("redirect_uris") if isinstance(doc, dict) else None
    if (not isinstance(doc, dict) or doc.get("client_id") != client_id
            or not isinstance(uris, list) or not uris
            or not all(isinstance(u, str) and _redirect_valida(u) for u in uris)):
        logger.warning("CIMD %s: documento inválido", client_id)
        return None
    return uris


def _cliente_dcr(db: str, client_id: str) -> Optional[list[str]]:
    conn = _connect(db)
    try:
        fila = conn.execute("SELECT redirect_uris FROM oauth_clientes WHERE client_id = ?",
                            (client_id,)).fetchone()
    finally:
        conn.close()
    if not fila:
        return None
    try:
        uris = json.loads(fila["redirect_uris"])
    except (TypeError, ValueError):
        return None
    return uris if isinstance(uris, list) else None


def _guardar_en_cache(client_id: str, uris: Optional[list[str]], duracion: float) -> None:
    """El cache negativo hace que cada client_id inventado bajo claude.ai deje una
    entrada; sin tope crecería hasta agotar la memoria del worker. Al llenarse se
    purgan las vencidas y, si sigue lleno, no se guarda: el costo es volver a
    descargar, no perder el servicio."""
    ahora = time.monotonic()
    if len(_cache_cimd) >= TOPE_CACHE:
        for clave in [c for c, (vence, _) in _cache_cimd.items() if vence <= ahora]:
            del _cache_cimd[clave]
        if len(_cache_cimd) >= TOPE_CACHE:
            return
    _cache_cimd[client_id] = (ahora + duracion, uris)


def redirect_uris_de(db: str, client_id: str) -> Optional[list[str]]:
    """Las `redirect_uris` registradas para ese cliente, o None si no es un
    cliente que aceptemos."""
    if not client_id:
        return None
    if es_cimd(client_id):
        entrada = _cache_cimd.get(client_id)
        if entrada and entrada[0] > time.monotonic():
            return entrada[1]
        uris = _descargar_cimd(client_id)
        duracion = CACHE_SEGUNDOS if uris is not None else CACHE_FALLO_SEGUNDOS
        _guardar_en_cache(client_id, uris, duracion)
        return uris
    return _cliente_dcr(db, client_id)


def _redirect_valida(uri: str) -> bool:
    if len(uri) > LARGO_MAX_REDIRECT:
        return False
    partes = urlsplit(uri)
    if partes.fragment:  # RFC 6749 §3.1.2: la redirect_uri no lleva fragmento.
        return False
    if partes.scheme == "https" and partes.netloc:
        return True
    return partes.scheme == "http" and partes.hostname in HOSTS_LOOPBACK


def registrar_cliente(db: str, datos: dict) -> Optional[dict]:
    """RFC 7591. None si el pedido no sirve (el endpoint responde 400)."""
    if not isinstance(datos, dict):
        return None
    uris = datos.get("redirect_uris")
    if not isinstance(uris, list) or not uris or len(uris) > TOPE_REDIRECTS:
        return None
    if not all(isinstance(u, str) and _redirect_valida(u) for u in uris):
        return None
    nombre = datos.get("client_name")
    nombre = nombre[:120] if isinstance(nombre, str) else None
    client_id = secrets.token_urlsafe(24)
    ahora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = _connect(db)
    try:
        _podar(conn, ahora)
        conn.execute(
            "INSERT INTO oauth_clientes (client_id, client_name, redirect_uris, created_at) "
            "VALUES (?,?,?,?)", (client_id, nombre, json.dumps(uris), ahora))
        conn.commit()
    finally:
        conn.close()
    return {"client_id": client_id, "client_name": nombre, "redirect_uris": uris,
            "token_endpoint_auth_method": "none"}


def _podar(conn: sqlite3.Connection, ahora: str) -> None:
    """Si ya hay TOPE_CLIENTES, borra los más viejos que no tengan tokens vivos,
    los justos para que entre uno más. Si los TOPE_CLIENTES tienen tokens vivos no
    se poda nada y el tope se excede a propósito: es mejor que rechazar un registro."""
    total = conn.execute("SELECT COUNT(*) FROM oauth_clientes").fetchone()[0]
    sobran = total - TOPE_CLIENTES + 1
    if sobran <= 0:
        return
    conn.execute(
        "DELETE FROM oauth_clientes WHERE client_id IN ("
        "  SELECT c.client_id FROM oauth_clientes c"
        "  WHERE NOT EXISTS (SELECT 1 FROM oauth_tokens t WHERE t.client_id = c.client_id"
        "                    AND t.revocado_en IS NULL AND t.refresh_expira > ?)"
        "  ORDER BY c.created_at LIMIT ?)", (ahora, sobran))


def redirect_permitido(redirect_uri: str, registradas: list[str]) -> bool:
    if not redirect_uri:
        return False
    if redirect_uri in registradas:
        return True
    pedida = urlsplit(redirect_uri)
    if pedida.scheme != "http" or pedida.hostname not in HOSTS_LOOPBACK:
        return False
    for reg in registradas:
        if reg not in REDIRECTS_LOOPBACK:
            continue
        r = urlsplit(reg)
        if (pedida.hostname == r.hostname and pedida.path == r.path
                and not pedida.query and not pedida.fragment):
            return True
    return False
