"""Panel Agentes: revisar lo que proponen las tareas programadas del equipo
(services/agentes.py). Los agentes cargan por MCP; acá solo leen y deciden
las personas.

`require_panel("agentes")`: un admin lo ve siempre; a otro rol se le da desde
Equipo. No se suma solo a los roles existentes, a propósito.
"""

from flask import Blueprint, current_app, jsonify, request, session

from database import log_activity
from services import agentes as ag
from services.auth import require_panel

agentes_bp = Blueprint("agentes", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


@agentes_bp.before_request
def _candado():
    return require_panel(_db(), "agentes")


def _error(e):
    return jsonify({"ok": False, "error": str(e)}), 400


@agentes_bp.route("/api/agentes")
def api_listar():
    estado = request.args.get("estado") or None
    try:
        propuestas = ag.listar(_db(), estado, limite=ag.TOPE_LISTA)
    except ag.NoSePuede as e:
        return _error(e)
    return jsonify({"ok": True, "conteos": ag.conteos(_db()), "propuestas": propuestas})


@agentes_bp.route("/api/agentes/brujula")
def api_brujula():
    return jsonify({"ok": True, **ag.leer_brujula(_db())})


@agentes_bp.route("/api/agentes/brujula", methods=["PUT"])
def api_guardar_brujula():
    datos = request.get_json(silent=True) or {}
    quien = session.get("user_name", "sistema")
    try:
        b = ag.guardar_brujula(_db(), datos.get("texto"), quien)
    except ag.NoSePuede as e:
        return _error(e)
    log_activity(_db(), quien, "agentes_brujula", "agentes_brujula", 1, "", "",
                 user_id=session.get("user_id"))
    return jsonify({"ok": True, **b})


@agentes_bp.route("/api/agentes/<int:pid>")
def api_ver(pid):
    try:
        return jsonify({"ok": True, "propuesta": ag.ver(_db(), pid)})
    except ag.NoSePuede as e:
        return jsonify({"ok": False, "error": str(e)}), 404


@agentes_bp.route("/api/agentes/<int:pid>/<accion>", methods=["POST"])
def api_revisar(pid, accion):
    datos = request.get_json(silent=True) or {}
    quien = session.get("user_name", "sistema")
    db = _db()
    try:
        r = ag.revisar(db, pid, accion, quien, datos.get("comentario"))
    except ag.NoSePuede as e:
        return _error(e)
    log_activity(db, quien, f"agente_{r['estado']}", "agente_propuesta", pid, "", "",
                 user_id=session.get("user_id"))
    return jsonify({"ok": True, **r})
