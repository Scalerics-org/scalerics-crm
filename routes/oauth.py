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
import threading
import time
from urllib.parse import urlencode, urlsplit

from flask import (Blueprint, current_app, jsonify, redirect, render_template_string,
                   request, session, url_for)

from services import oauth_clientes, oauth_tokens
from services.oauth_tokens import CHALLENGE_RE, SCOPE_LECTURA, base_url, recurso_mcp

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
# Cuatro hilos de gunicorn comparten el dict: leer, filtrar y anotar tiene que
# ser un solo paso.
_CERROJO_REGISTROS = threading.Lock()


def _db() -> str:
    return current_app.config["DB_PATH"]


@oauth_bp.after_request
def _cabeceras_oauth(resp):
    """Nada de este blueprint se embebe ni se cachea: la pantalla de
    consentimiento en un iframe ajeno es clickjacking, y las respuestas
    llevan nonce, código o token."""
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if "Cache-Control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Pragma"] = "no-cache"
    return resp


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
    with _CERROJO_REGISTROS:
        # Si crece más allá del máximo se vacía todo: es un tope contra abuso, no
        # un registro de auditoría, y así la memoria del proceso nunca crece sin
        # límite.
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
        resp = _error_json("invalid_client_metadata",
                           "Demasiados registros desde esta dirección", 429)
        resp.headers["Retry-After"] = str(VENTANA_REGISTROS_SEG)
        return resp
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


# ── autorización ─────────────────────────────────────────────────────────────

class _ErrorPagina(Exception):
    """No se puede confiar en la redirect_uri: se muestra la página, no se redirige."""


class _ErrorRedirect(Exception):
    """Error OAuth que vuelve al cliente. Lleva la redirect_uri y el state ya
    validados (la misma cadena que pasó `redirect_permitido`, sin espacios)."""

    def __init__(self, codigo: str, descripcion: str, redirect_uri: str, state: str):
        super().__init__(descripcion)
        self.codigo, self.descripcion = codigo, descripcion
        self.redirect_uri, self.state = redirect_uri, state


def _validar_pedido(params) -> dict:
    """Valida un pedido de autorización (GET o el form del POST, que repite los
    mismos campos). Primero cliente y redirect, que deciden si se puede
    redirigir; después el resto, que vuelve al cliente como error OAuth."""
    client_id = (params.get("client_id") or "").strip()
    redirect_uri = (params.get("redirect_uri") or "").strip()
    registradas = oauth_clientes.redirect_uris_de(_db(), client_id)
    if registradas is None:
        raise _ErrorPagina("Cliente desconocido. Volvé a agregar el conector en Claude.")
    if not oauth_clientes.redirect_permitido(redirect_uri, registradas):
        raise _ErrorPagina("La dirección de vuelta no es una de las registradas por el cliente.")
    state = params.get("state") or ""
    if params.get("response_type") != "code":
        raise _ErrorRedirect("unsupported_response_type", "Solo response_type=code",
                             redirect_uri, state)
    challenge = (params.get("code_challenge") or "").strip()
    if (not challenge or params.get("code_challenge_method") != "S256"
            or not CHALLENGE_RE.fullmatch(challenge)):
        raise _ErrorRedirect("invalid_request", "PKCE S256 es obligatorio", redirect_uri, state)
    scope = (params.get("scope") or SCOPE_LECTURA).strip()
    if set(scope.split()) != {SCOPE_LECTURA}:
        raise _ErrorRedirect("invalid_scope", f"El único scope es {SCOPE_LECTURA}",
                             redirect_uri, state)
    resource = (params.get("resource") or recurso_mcp()).strip()
    if resource != recurso_mcp():
        raise _ErrorRedirect("invalid_target", f"El recurso es {recurso_mcp()}",
                             redirect_uri, state)
    return {"client_id": client_id, "redirect_uri": redirect_uri, "challenge": challenge,
            "scope": SCOPE_LECTURA, "resource": resource, "state": state}


def _volver(redirect_uri: str, **campos):
    sep = "&" if "?" in redirect_uri else "?"
    return redirect(redirect_uri + sep + urlencode({k: v for k, v in campos.items() if v}))


@oauth_bp.route("/oauth/authorize", methods=["GET", "POST"])
def autorizar():
    params = request.form if request.method == "POST" else request.args
    try:
        pedido = _validar_pedido(params)
    except _ErrorPagina as e:
        return render_template_string(ERROR_HTML, mensaje=str(e)), 400
    except _ErrorRedirect as e:
        return _volver(e.redirect_uri, error=e.codigo, error_description=e.descripcion,
                       state=e.state)

    if not session.get("logged_in") or not session.get("user_id"):
        # Volver acá después de entrar. /login solo honra next si empieza con
        # /oauth/authorize, así que no sirve para mandar a nadie a otro lado.
        vuelta = request.full_path if request.method == "GET" else url_for("oauth.autorizar")
        return redirect(url_for("login", next=vuelta))

    if request.method == "GET":
        nonce = secrets.token_urlsafe(16)
        session["oauth_nonce"] = nonce
        return render_template_string(
            CONSENTIMIENTO_HTML, pedido=pedido, nonce=nonce,
            usuario=session.get("user_name") or "",
            host_destino=urlsplit(pedido["redirect_uri"]).netloc)

    esperado = session.pop("oauth_nonce", None)
    if not esperado or not hmac.compare_digest(esperado, request.form.get("nonce", "")):
        return render_template_string(ERROR_HTML, mensaje="El formulario venció. Volvé a intentar desde Claude."), 403
    if request.form.get("accion") != "autorizar":
        return _volver(pedido["redirect_uri"], error="access_denied",
                       error_description="La persona canceló", state=pedido["state"])
    code = oauth_tokens.emitir_codigo(_db(), session["user_id"], pedido["client_id"],
                                      pedido["redirect_uri"], pedido["challenge"],
                                      pedido["scope"], pedido["resource"])
    return _volver(pedido["redirect_uri"], code=code, state=pedido["state"])


_ESTILO = """
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:#0a0f1a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#111827;border:1px solid #1e293b;border-radius:16px;padding:40px 36px;width:420px;max-width:92vw}
.logo-wrap{margin-bottom:24px;display:flex;flex-direction:column;align-items:flex-start;gap:10px}
.logo-wrap img{height:44px;object-fit:contain}
.logo-sub{font-size:.75rem;color:#475569}
h1{font-size:1.1rem;margin-bottom:12px}
p{font-size:.9rem;color:#94a3b8;line-height:1.5;margin-bottom:12px}
ul{margin:0 0 18px 18px;font-size:.9rem;color:#cbd5e1}
.host{font-family:monospace;color:#e2e8f0}
.botones{display:flex;gap:10px;margin-top:8px}
button{flex:1;font-size:.88rem;font-weight:700;padding:12px;border-radius:8px;border:none;cursor:pointer;font-family:'Inter',sans-serif}
.ok{background:linear-gradient(135deg,#0088cc,#3db648);color:#fff}
.no{background:#1e293b;color:#cbd5e1}
"""

CONSENTIMIENTO_HTML = """<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Scalerics — Autorizar a Claude</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
<style>""" + _ESTILO + """</style></head>
<body><div class="card">
  <div class="logo-wrap">
    <img src="https://raw.githubusercontent.com/Scalerics-org/scalerics-assets/main/logo_full_alt.png" alt="Scalerics">
    <span class="logo-sub">CRM interno</span>
  </div>
  <h1>Claude quiere entrar al CRM como {{ usuario }}</h1>
  <p>Si autorizás, Claude va a poder, en tu nombre:</p>
  <ul>
    <li>Leer leads y el pipeline (buscar, ver fichas, historial)</li>
  </ul>
  <p>No va a poder modificar nada. Cada consulta queda registrada con tu usuario.</p>
  <p>Después de autorizar vas a volver a <span class="host">{{ host_destino }}</span>.</p>
  <form method="POST" action="/oauth/authorize">
    <input type="hidden" name="response_type" value="code">
    <input type="hidden" name="client_id" value="{{ pedido.client_id }}">
    <input type="hidden" name="redirect_uri" value="{{ pedido.redirect_uri }}">
    <input type="hidden" name="code_challenge" value="{{ pedido.challenge }}">
    <input type="hidden" name="code_challenge_method" value="S256">
    <input type="hidden" name="state" value="{{ pedido.state }}">
    <input type="hidden" name="scope" value="{{ pedido.scope }}">
    <input type="hidden" name="resource" value="{{ pedido.resource }}">
    <input type="hidden" name="nonce" value="{{ nonce }}">
    <div class="botones">
      <button class="no" type="submit" name="accion" value="cancelar">Cancelar</button>
      <button class="ok" type="submit" name="accion" value="autorizar">Autorizar</button>
    </div>
  </form>
</div></body></html>"""

ERROR_HTML = """<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Scalerics — No se pudo autorizar</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
<style>""" + _ESTILO + """</style></head>
<body><div class="card">
  <h1>No se pudo autorizar a Claude</h1>
  <p>{{ mensaje }}</p>
</div></body></html>"""


# ── token ────────────────────────────────────────────────────────────────────

@oauth_bp.route("/oauth/token", methods=["POST"])
def token():
    # Mismo tope que el registro: un cuerpo enorme recibe JSON, no el HTML del 413
    # de Werkzeug.
    if (request.content_length or 0) > TOPE_CUERPO_REGISTRO:
        return _error_json("invalid_request", "Cuerpo demasiado grande", 413)
    # RFC 6749 §4.1.3: form-urlencoded. Claude manda eso; un JSON acá es un
    # cliente mal armado, y la respuesta correcta es 400, no 415.
    if request.mimetype != "application/x-www-form-urlencoded":
        return _error_json("invalid_request", "El cuerpo tiene que ser application/x-www-form-urlencoded")
    form = request.form
    grant = form.get("grant_type", "")
    if grant == "authorization_code":
        tokens = oauth_tokens.canjear_codigo(_db(), form.get("code", ""), form.get("client_id", ""),
                                             form.get("redirect_uri", ""), form.get("code_verifier", ""))
    elif grant == "refresh_token":
        tokens = oauth_tokens.refrescar(_db(), form.get("refresh_token", ""), form.get("client_id", ""))
    else:
        return _error_json("unsupported_grant_type", "Solo authorization_code y refresh_token")
    if tokens is None:
        # Un código o refresh reusado revoca la familia en el servicio y vuelve como
        # el mismo 400; este log es la única señal de un posible robo. Nunca lleva
        # el código ni el token.
        current_app.logger.warning("oauth token: %s rechazado client_id=%r ip=%s",
                                   grant, form.get("client_id", ""), _ip_cliente())
        return _error_json("invalid_grant", "Código o refresh inválido, vencido o ya usado")
    resp = jsonify({**tokens, "token_type": "Bearer"})
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Pragma"] = "no-cache"
    return resp


@oauth_bp.route("/oauth/revoke", methods=["POST"])
def revocar():
    oauth_tokens.revocar(_db(), request.form.get("token", ""))
    # Cache-Control y Pragma los pone el after_request del blueprint.
    return jsonify({})
