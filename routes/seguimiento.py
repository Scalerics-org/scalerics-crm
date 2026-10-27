"""Pestaña Seguimiento del panel Clientes (services/seguimiento.py).

Lo carga Claude por el conector MCP; acá se lee, y desde el panel solo se
mueve un frente de estado (llegó lo que se esperaba, se terminó). Mismo
candado que ver Clientes.
"""

from flask import Blueprint, current_app, jsonify, request, session

from services import seguimiento as seg
from services.auth import require_panel

seguimiento_bp = Blueprint("seguimiento", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


@seguimiento_bp.before_request
def _candado():
    return require_panel(_db(), "clientes")


@seguimiento_bp.route("/api/seguimiento")
def api_tablero():
    return jsonify({"ok": True, **seg.tablero(_db())})


@seguimiento_bp.route("/api/seguimiento/<int:business_id>")
def api_ver(business_id):
    try:
        return jsonify({"ok": True, **seg.ver(_db(), business_id)})
    except seg.NoSePuede as e:
        return jsonify({"ok": False, "error": str(e)}), 404


@seguimiento_bp.route("/api/seguimiento/frentes/<int:frente_id>/estado", methods=["PUT"])
def api_estado(frente_id):
    datos = request.get_json(silent=True) or {}
    try:
        cliente = seg.cambiar_estado(_db(), session.get("user_name", "sistema"),
                                     frente_id, datos.get("estado"))
    except seg.NoSePuede as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, **cliente})
