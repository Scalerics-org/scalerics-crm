"""Lo que usa la sesión de Claude Code que responde por los agentes de la Oficina.

Va sin sesión y se valida con OFICINA_BOT_TOKEN (header x-oficina-token), que
solo abre estas rutas. Mismo criterio que /api/linkedin-bot/: no lo mezcles con
el ADMIN_TOKEN, que abre todo el CRM.
"""

import hmac
import os

from flask import Blueprint, current_app, jsonify, request

from services import oficina as of

oficina_bot_bp = Blueprint("oficina_bot", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


def _bot_ok() -> bool:
    esperado = os.environ.get("OFICINA_BOT_TOKEN", "")
    recibido = request.headers.get("x-oficina-token", "")
    return bool(esperado) and len(esperado) >= 32 and hmac.compare_digest(esperado, recibido)


@oficina_bot_bp.before_request
def _candado_bot():
    if not _bot_ok():
        return jsonify({"ok": False, "error": "No autorizado"}), 403
    return None


@oficina_bot_bp.route("/api/oficina-bot/pendientes")
def bot_pendientes():
    return jsonify({"ok": True, "pendientes": of.pendientes(_db())})


@oficina_bot_bp.route("/api/oficina-bot/mensajes/<int:msj_id>/responder", methods=["POST"])
def bot_responder(msj_id):
    datos = request.get_json(silent=True) or {}
    try:
        nuevo = of.responder(_db(), msj_id, datos.get("texto"), datos.get("fuente"))
    except of.NoSePuede as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "id": nuevo})
