"""Endpoints del cobro automático con Plexo (services/plexo.py).

Dos grupos:
- `/api/plexo/...` para la pestaña Cobro con tarjeta de Finanzas: piden el
  panel `finanzas` y, para escribir, que no esté en solo lectura. Mismo candado
  que routes/finanzas.py.
- Los públicos, que abre el cliente o llama Plexo y por eso no llevan sesión
  (están exentos en dashboard.require_login): el link para cargar la tarjeta,
  la página de "listo" y el callback. Ninguno confía en lo que recibe: el link
  solo abre una página de Plexo para un código que existe, y el callback no
  toma nada del cuerpo salvo el id del cliente, con el que se le vuelve a
  preguntar a Plexo.
"""

from datetime import date

from flask import Blueprint, current_app, jsonify, redirect, render_template_string, request

from services import plexo
from services.auth import is_admin, require_edicion, require_panel

plexo_bp = Blueprint("plexo", __name__)

PUBLICAS = ("/api/plexo/callback/", "/plexo/tarjeta/", "/plexo/listo")


def _db() -> str:
    return current_app.config["DB_PATH"]


def _base() -> str:
    """La URL pública del CRM. En Fly el request llega por http detrás del proxy."""
    base = (current_app.config.get("PUBLIC_URL") or request.host_url).rstrip("/")
    if base.startswith("http://") and not base.startswith(("http://localhost", "http://127.")):
        base = "https://" + base[len("http://"):]
    return base


def _error(msg: str, codigo: int = 400):
    return jsonify({"ok": False, "error": msg}), codigo


@plexo_bp.before_request
def _candado():
    if request.path.startswith(PUBLICAS):
        return None
    bloqueo = require_panel(_db(), "finanzas")
    if bloqueo:
        return bloqueo
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    return require_edicion(_db(), "finanzas")


def _fijo(rec_id: int):
    from database import get_business, get_recurrente
    fijo = get_recurrente(_db(), rec_id)
    if not fijo or fijo["tipo"] != "ingreso" or not fijo.get("tarjeta"):
        return None, None
    cliente = get_business(_db(), fijo["client_id"]) if fijo.get("client_id") else None
    return fijo, cliente


@plexo_bp.route("/api/plexo/estado")
def api_estado():
    datos = plexo.resumen(_db())
    base = _base()
    for f in datos["fijos"]:
        f["link"] = f"{base}/plexo/tarjeta/{f.pop('codigo')}" if f.get("codigo") else None
    return jsonify(datos)


@plexo_bp.route("/api/plexo/fijos/<int:rec_id>/link", methods=["POST"])
def api_link(rec_id):
    fijo, cliente = _fijo(rec_id)
    if not fijo:
        return _error("ese ingreso fijo no existe o no se cobra con tarjeta", 404)
    email = (request.get_json(silent=True) or {}).get("email")
    try:
        r = plexo.pedir_tarjeta(_db(), fijo, cliente, _base(), email=email)
    except plexo.PlexoError as e:
        return _error(str(e), 502)
    return jsonify({"ok": True, **r})


@plexo_bp.route("/api/plexo/fijos/<int:rec_id>/verificar", methods=["POST"])
def api_verificar(rec_id):
    try:
        return jsonify({"ok": True, **plexo.verificar_tarjeta(_db(), rec_id)})
    except plexo.PlexoError as e:
        return _error(str(e), 502)


@plexo_bp.route("/api/plexo/fijos/<int:rec_id>/cobrar", methods=["POST"])
def api_cobrar(rec_id):
    """Cobrar el mes ahora, sin esperar al día de cobro. Nunca cobra dos veces
    un mes: la guarda es la misma que la del cobro automático."""
    from flask import session
    if not is_admin(_db(), session.get("user_id")):
        return _error("solo un administrador puede cobrar a mano", 403)
    fijo, _ = _fijo(rec_id)
    if not fijo:
        return _error("ese ingreso fijo no existe o no se cobra con tarjeta", 404)
    return jsonify({"ok": True, **plexo.cobrar_mes(_db(), fijo, date.today(), manual=True)})


@plexo_bp.route("/api/plexo/fijos/<int:rec_id>/pausar", methods=["POST"])
def api_pausar(rec_id):
    pausada = bool((request.get_json(silent=True) or {}).get("pausada", True))
    plexo.pausar(_db(), rec_id, pausada)
    return jsonify({"ok": True})


# ── públicos ─────────────────────────────────────────────────────────────────

_PAGINA = """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Scalerics</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f1f5f9;
font-family:Inter,Arial,sans-serif;color:#0f172a}.c{background:#fff;border-radius:16px;padding:36px 28px;
max-width:420px;margin:16px;text-align:center;box-shadow:0 4px 24px rgba(15,23,42,.08)}
h1{font-size:1.3rem;margin:12px 0 8px}p{color:#475569;line-height:1.5;margin:0}.i{font-size:2.4rem}</style>
</head><body><div class="c"><div class="i">{{ icono }}</div><h1>{{ titulo }}</h1><p>{{ texto }}</p></div></body></html>"""


@plexo_bp.route("/plexo/tarjeta/<codigo>")
def pagina_tarjeta(codigo):
    try:
        url = plexo.abrir_link(_db(), codigo, _base())
    except plexo.PlexoError:
        return render_template_string(_PAGINA, icono="⚠️", titulo="No pudimos abrir la página de pago",
                                      texto="Probá de nuevo en unos minutos. Si sigue, escribinos."), 503
    if not url:
        return render_template_string(_PAGINA, icono="🔗", titulo="Este link ya no está activo",
                                      texto="Pedinos uno nuevo y te lo mandamos."), 404
    return redirect(url, code=302)


@plexo_bp.route("/plexo/listo")
@plexo_bp.route("/plexo/listo/<codigo>")
def pagina_listo(codigo=None):
    # Al volver de Plexo se aprovecha para confirmar la tarjeta, por si el
    # callback no llegó. Si falla, no importa: el callback o el botón lo hacen.
    if codigo:
        s = plexo.por_link(_db(), codigo)
        if s:
            try:
                plexo.verificar_tarjeta(_db(), s["recurrente_id"])
            except Exception:
                pass
    return render_template_string(_PAGINA, icono="✅", titulo="¡Listo! Tu tarjeta quedó registrada",
                                  texto="Gracias. Los próximos pagos se van a cobrar automáticamente. Ya podés cerrar esta página.")


@plexo_bp.route("/api/plexo/callback/tarjeta", methods=["POST"])
def callback_tarjeta():
    datos = request.get_json(silent=True) or {}
    cliente = ((datos.get("Data") or {}).get("Customer") or {}).get("Id") \
        or ((datos.get("Data") or {}).get("PaymentInstrument") or {}).get("CustomerId")
    s = plexo.por_customer(_db(), cliente) if cliente else None
    if s:
        try:
            plexo.verificar_tarjeta(_db(), s["recurrente_id"])
        except plexo.PlexoError:
            pass
    # Siempre 200: un error acá haría que Plexo reintente algo que igual se
    # confirma al volver a la página o con el botón.
    return jsonify({"ok": True})
