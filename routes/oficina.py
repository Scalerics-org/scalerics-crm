"""Panel Oficina: el equipo de agentes de Juan (services/oficina.py).

Solo admin, igual que Contraseñas: `require_admin` cierra todo el blueprint y
el panel no se reparte por rol.
"""

from flask import Blueprint, current_app, jsonify, request

from services import oficina as of
from services.auth import require_admin

oficina_bp = Blueprint("oficina", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


@oficina_bp.before_request
def _candado():
    return require_admin(_db())


def _error(e):
    return jsonify({"ok": False, "error": str(e)}), 400


def _texto(campo="texto"):
    return (request.get_json(silent=True) or {}).get(campo)


@oficina_bp.route("/api/oficina/agentes")
def api_agentes():
    return jsonify({"ok": True, "agentes": of.listar_agentes(_db())})


@oficina_bp.route("/api/oficina/agentes/<agente>")
def api_ficha(agente):
    try:
        return jsonify({"ok": True, **of.ficha(_db(), agente)})
    except of.NoSePuede as e:
        return jsonify({"ok": False, "error": str(e)}), 404


@oficina_bp.route("/api/oficina/agentes/<agente>/mensajes", methods=["POST"])
def api_preguntar(agente):
    try:
        return jsonify({"ok": True, "id": of.preguntar(_db(), agente, _texto())})
    except of.NoSePuede as e:
        return _error(e)


@oficina_bp.route("/api/oficina/agentes/<agente>/indicaciones", methods=["POST"])
def api_indicar(agente):
    try:
        return jsonify({"ok": True, "id": of.agregar_indicacion(_db(), agente, _texto())})
    except of.NoSePuede as e:
        return _error(e)


@oficina_bp.route("/api/oficina/indicaciones/<int:ind_id>", methods=["DELETE"])
def api_borrar_indicacion(ind_id):
    try:
        of.borrar_indicacion(_db(), ind_id)
    except of.NoSePuede as e:
        return _error(e)
    return jsonify({"ok": True})


@oficina_bp.route("/api/oficina/aprendizajes/<int:apr_id>", methods=["DELETE"])
def api_olvidar(apr_id):
    try:
        of.olvidar(_db(), apr_id)
    except of.NoSePuede as e:
        return _error(e)
    return jsonify({"ok": True})


@oficina_bp.route("/api/oficina/mensajes/<int:msj_id>/opinion", methods=["POST"])
def api_opinar(msj_id):
    datos = request.get_json(silent=True) or {}
    try:
        of.opinar(_db(), msj_id, datos.get("tipo"), datos.get("correccion"))
    except of.NoSePuede as e:
        return _error(e)
    return jsonify({"ok": True})
