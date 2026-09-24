"""Lo que usa la sesion de Claude Code que resuelve correcciones de LinkedIn.

Va sin sesion y se valida con LINKEDIN_BOT_TOKEN (header x-linkedin-token),
que solo abre estas rutas. Mismo criterio que /api/instagram-bot/ en
routes/instagram.py: no lo mezcles con el ADMIN_TOKEN, que abre todo el CRM.
"""

import hmac
import os

from flask import Blueprint, current_app, jsonify, request

from services import linkedin_borradores as lb

linkedin_bot_bp = Blueprint("linkedin_bot", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


def _bot_ok() -> bool:
    esperado = os.environ.get("LINKEDIN_BOT_TOKEN", "")
    recibido = request.headers.get("x-linkedin-token", "")
    return bool(esperado) and len(esperado) >= 32 and hmac.compare_digest(esperado, recibido)


@linkedin_bot_bp.before_request
def _candado_bot():
    if not _bot_ok():
        return jsonify({"ok": False, "error": "No autorizado"}), 403
    return None


@linkedin_bot_bp.route("/api/linkedin-bot/pendientes")
def bot_pendientes():
    return jsonify({"ok": True, "pendientes": lb.pendientes(_db())})


@linkedin_bot_bp.route("/api/linkedin-bot/correcciones/<int:corr_id>/resolver", methods=["POST"])
def bot_resolver(corr_id):
    datos = request.get_json(silent=True) or {}
    try:
        borrador = lb.resolver_correccion(
            _db(), corr_id, datos.get("texto"), datos.get("frase") or "", datos.get("respuesta") or "")
    except lb.NoSePuede as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "estado": borrador["estado"]})


@linkedin_bot_bp.route("/api/linkedin-bot/correcciones/<int:corr_id>/rechazar", methods=["POST"])
def bot_rechazar(corr_id):
    datos = request.get_json(silent=True) or {}
    try:
        lb.rechazar_correccion(_db(), corr_id, datos.get("respuesta") or "")
    except lb.NoSePuede as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True})
