"""Servidor de autorización OAuth 2.1 del conector MCP para Claude.

Es el lado HTTP: valida y delega en `services/oauth_clientes.py` (quién es el
cliente) y `services/oauth_tokens.py` (códigos y tokens). Las reglas que
claude.ai exige están en el spec y en
https://claude.com/docs/connectors/building/authentication.

Estas rutas están exentas del candado de sesión de `dashboard.require_login`:
cada una aplica su propia autenticación. `/oauth/authorize` es la única que
pide la sesión del CRM, porque ahí la persona autoriza a Claude.
"""

import hmac
import secrets
import time
from urllib.parse import urlencode

from flask import (Blueprint, current_app, jsonify, redirect, render_template_string,
                   request, session, url_for)

from services import oauth_clientes, oauth_tokens
from services.oauth_tokens import SCOPE_LECTURA, base_url, recurso_mcp

oauth_bp = Blueprint("oauth", __name__)

CACHE_METADATA = "public, max-age=3600"

# `/oauth/register` es público y cada alta es una fila: sin tope, una ráfaga
# desde una sola dirección podría desalojar (por la poda) a los clientes
# legítimos que Claude registró.
TOPE_CUERPO_REGISTRO = 8 * 1024
TOPE_REGISTROS_POR_IP = 20
VENTANA_REGISTROS_SEG = 3600
MAX_IPS_REGISTRO = 1000
_REGISTROS_POR_IP: dict[str, list[float]] = {}


def _db() -> str:
    return current_app.config["DB_PATH"]


def _json_publico(datos: dict):
    resp = jsonify(datos)
    resp.headers["Cache-Control"] = CACHE_METADATA
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


def _error_json(codigo: str, descripcion: str, estado: int = 400):
    resp = jsonify({"error": codigo, "error_description": descripcion})
    resp.status_code = estado
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Pragma"] = "no-cache"
    return resp


def _ip_cliente() -> str:
    """La IP real de quien llama. Detrás del proxy de Fly, `remote_addr` es el
    proxy; `Fly-Client-IP` lo pone Fly y pisa lo que mande el cliente, a
    diferencia de `X-Forwarded-For`, cuyo primer valor controla quien llama."""
    return (request.headers.get("Fly-Client-IP") or request.remote_addr or "desconocida").strip()


def _paso_el_tope_de_registros(ip: str) -> bool:
    """True si esa IP todavía puede registrar un cliente en la última hora."""
    ahora = time.time()
    # Si crece más allá del máximo se vacía todo: es un tope contra abuso, no un
    # registro de auditoría, y así la memoria del proceso nunca crece sin límite.
    if len(_REGISTROS_POR_IP) >= MAX_IPS_REGISTRO and ip not in _REGISTROS_POR_IP:
        _REGISTROS_POR_IP.clear()
    recientes = [t for t in _REGISTROS_POR_IP.get(ip, [])
                 if ahora - t < VENTANA_REGISTROS_SEG]
    if len(recientes) >= TOPE_REGISTROS_POR_IP:
        _REGISTROS_POR_IP[ip] = recientes
        return False
    recientes.append(ahora)
    _REGISTROS_POR_IP[ip] = recientes
    return True


# ── metadata ─────────────────────────────────────────────────────────────────

@oauth_bp.route("/.well-known/oauth-authorization-server")
def metadata_servidor():
    base = base_url()
    return _json_publico({
        "issuer": base,
        "authorization_endpoint": base + "/oauth/authorize",
        "token_endpoint": base + "/oauth/token",
        "registration_endpoint": base + "/oauth/register",
        "revocation_endpoint": base + "/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        # Claude usa su identidad publicada (CIMD) solo si ve estas dos.
        "token_endpoint_auth_methods_supported": ["none"],
        "client_id_metadata_document_supported": True,
        "scopes_supported": [SCOPE_LECTURA],
    })


@oauth_bp.route("/.well-known/oauth-protected-resource")
@oauth_bp.route("/.well-known/oauth-protected-resource/mcp")
def metadata_recurso():
    return _json_publico({
        "resource": recurso_mcp(),
        "authorization_servers": [base_url()],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [SCOPE_LECTURA],
    })


# ── registro dinámico ────────────────────────────────────────────────────────

@oauth_bp.route("/oauth/register", methods=["POST"])
def registrar():
    if (request.content_length or 0) > TOPE_CUERPO_REGISTRO:
        return _error_json("invalid_client_metadata", "Cuerpo demasiado grande", 413)
    if not _paso_el_tope_de_registros(_ip_cliente()):
        return _error_json("invalid_client_metadata",
                           "Demasiados registros desde esta dirección", 429)
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        return _error_json("invalid_client_metadata", "El cuerpo tiene que ser JSON")
    cliente = oauth_clientes.registrar_cliente(_db(), datos)
    if cliente is None:
        return _error_json("invalid_redirect_uri",
                           "redirect_uris tiene que ser una lista de URLs https (o loopback)")
    # RFC 7591: un campo sin valor se omite, no se manda como null.
    resp = jsonify({k: v for k, v in cliente.items()
                    if not (k == "client_name" and v is None)})
    resp.status_code = 201
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Pragma"] = "no-cache"
    return resp
