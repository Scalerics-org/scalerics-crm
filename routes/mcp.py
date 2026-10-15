"""El endpoint MCP del CRM: `POST /mcp`.

Transporte Streamable HTTP en modo stateless con respuesta JSON: cada POST es
un mensaje JSON-RPC y se contesta con un único JSON. No hay SSE, no hay
`Mcp-Session-Id`, no hay batch (la versión 2025-06-18 lo sacó). Para un
servidor de solo herramientas alcanza y es lo que mejor convive con gunicorn
sync.

Autenticación: Bearer emitido por `/oauth/token`. Sin token, o con uno malo,
`401` con el `WWW-Authenticate` que claude.ai necesita para arrancar el OAuth
(y lo necesita en un 401: en un 200 lo ignora). Con token, las mismas reglas
que la API web: el usuario existe y no es vendedor de Fidelidad.
"""

import logging

from flask import Blueprint, current_app, jsonify, request

from database import get_user_by_id
from services import mcp_tools, oauth_tokens
from services.fidelidad import es_vendedor
from services.oauth_tokens import SCOPE_LECTURA, base_url

logger = logging.getLogger(__name__)

mcp_bp = Blueprint("mcp", __name__)

VERSIONES = ("2025-11-25", "2025-06-18", "2025-03-26")
VERSION_DEFECTO = "2025-06-18"
INSTRUCCIONES = (
    "CRM de Scalerics, una agencia de Montevideo que vende sitios web y sistemas a comercios. "
    "Las herramientas son de solo lectura: buscar_leads para encontrar un negocio, ver_lead "
    "para su ficha e historial, pipeline para ver qué hay en cada etapa de venta."
)


class _ErrorRpc(Exception):
    def __init__(self, code: int, message: str, http: int = 200):
        super().__init__(message)
        self.code, self.message, self.http = code, message, http


def _db() -> str:
    return current_app.config["DB_PATH"]


def _rechazo_401():
    resp = jsonify({"error": "invalid_token"})
    resp.status_code = 401
    resp.headers["WWW-Authenticate"] = (
        f'Bearer resource_metadata="{base_url()}/.well-known/oauth-protected-resource/mcp", '
        f'scope="{SCOPE_LECTURA}"')
    return resp


def _autenticar():
    """(usuario, None) o (None, respuesta de error)."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, _rechazo_401()
    info = oauth_tokens.validar_access(_db(), auth[len("Bearer "):].strip())
    if not info:
        return None, _rechazo_401()
    usuario = get_user_by_id(_db(), info["user_id"])
    if not usuario or es_vendedor(_db(), usuario["id"]):
        return None, (jsonify({"error": "forbidden"}), 403)
    return usuario, None


def _respuesta_error(id_, code, message, http=200):
    resp = jsonify({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})
    resp.status_code = http
    return resp


@mcp_bp.route("/mcp", methods=["POST"])
def mcp():
    usuario, err = _autenticar()
    if err is not None:
        return err
    cuerpo = request.get_json(silent=True, force=True)
    if cuerpo is None:
        return _respuesta_error(None, -32700, "El cuerpo no es JSON", 400)
    if isinstance(cuerpo, list):
        return _respuesta_error(None, -32600, "Batch no soportado", 400)
    if not isinstance(cuerpo, dict) or cuerpo.get("jsonrpc") != "2.0" or not isinstance(cuerpo.get("method"), str):
        return _respuesta_error(cuerpo.get("id") if isinstance(cuerpo, dict) else None,
                                -32600, "No es un pedido JSON-RPC 2.0", 400)
    if cuerpo.get("id") is None:
        # Notificación: no se responde. Claude manda notifications/initialized.
        return "", 202
    id_ = cuerpo["id"]
    try:
        resultado = _despachar(cuerpo["method"], cuerpo.get("params") or {}, usuario)
    except _ErrorRpc as e:
        return _respuesta_error(id_, e.code, e.message, e.http)
    return jsonify({"jsonrpc": "2.0", "id": id_, "result": resultado})


def _despachar(metodo: str, params, usuario: dict):
    if not isinstance(params, dict):
        raise _ErrorRpc(-32602, "params tiene que ser un objeto")
    if metodo == "initialize":
        pedida = params.get("protocolVersion")
        return {
            "protocolVersion": pedida if pedida in VERSIONES else VERSION_DEFECTO,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "scalerics-crm", "version": "1"},
            "instructions": INSTRUCCIONES,
        }
    if metodo == "ping":
        return {}
    if metodo == "tools/list":
        return {"tools": mcp_tools.listar_herramientas()}
    if metodo == "tools/call":
        nombre = params.get("name")
        if not isinstance(nombre, str):
            raise _ErrorRpc(-32602, "Falta name")
        try:
            return mcp_tools.ejecutar(_db(), usuario, nombre, params.get("arguments"))
        except mcp_tools.ArgumentosInvalidos as e:
            raise _ErrorRpc(-32602, str(e))
    raise _ErrorRpc(-32601, f"Método no soportado: {metodo}")
