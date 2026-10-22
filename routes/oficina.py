"""Panel Oficina: los recordatorios que arma Bruno (services/oficina.py).

Solo admin, igual que Contraseñas: `require_admin` cierra todo el blueprint y
el panel no se reparte por rol.
"""

from flask import Blueprint, current_app, jsonify

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


@oficina_bp.route("/api/oficina/recordatorios")
def api_recordatorios():
    return jsonify({"ok": True, **of.recordatorios(_db())})


@oficina_bp.route("/api/oficina/recordatorios/<int:rec_id>/<accion>", methods=["POST"])
def api_resolver_recordatorio(rec_id, accion):
    try:
        of.resolver_recordatorio(_db(), rec_id, accion)
    except of.NoSePuede as e:
        return _error(e)
    return jsonify({"ok": True})
