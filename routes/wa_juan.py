"""Plantillas que salen del WhatsApp Business de Juan (services/wa_juan.py).

Solo admin: es el número personal de Juan con sus clientes.
"""

from flask import Blueprint, current_app, jsonify, request, session

from services import wa_juan
from services.auth import require_admin

wa_juan_bp = Blueprint("wa_juan", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


@wa_juan_bp.before_request
def _candado():
    return require_admin(_db())


@wa_juan_bp.route("/api/wa-juan/enviar", methods=["POST"])
def api_enviar():
    datos = request.get_json(silent=True) or {}
    try:
        envio = wa_juan.encolar(_db(), datos.get("phone"), datos.get("text"), datos.get("destino"),
                                session.get("user_id"))
    except wa_juan.NoSePuede as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "id": envio})


@wa_juan_bp.route("/api/wa-juan/envios/<int:envio_id>")
def api_estado(envio_id):
    try:
        return jsonify({"ok": True, **wa_juan.estado(_db(), envio_id)})
    except wa_juan.NoSePuede as e:
        return jsonify({"ok": False, "error": str(e)}), 404
