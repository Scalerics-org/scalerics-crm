"""Backups de la base: disparar uno a mano y ver los que hay. Solo admin.

La logica vive en `services/backup_db.py`; ver `docs/BACKUPS.md`.
"""

from flask import Blueprint, current_app, jsonify

from services import backup_db
from services.auth import require_admin

backups_bp = Blueprint("backups", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


@backups_bp.before_request
def _candado():
    return require_admin(_db())


@backups_bp.route("/api/admin/backup-ahora", methods=["POST"])
def api_backup_ahora():
    res = backup_db.hacer_backup(_db())
    if res.get("en_curso"):
        return jsonify(res), 409
    return jsonify(res), 200 if res.get("ok") else 500


@backups_bp.route("/api/admin/backups", methods=["GET"])
def api_listar_backups():
    db = _db()
    salida = {"ok": True, "r2_activo": backup_db.r2_configurado(), "backups": [],
              "locales": backup_db.listar_backups_locales(db), "error": None}
    cliente = backup_db.cliente_desde_entorno()
    if cliente is not None:
        try:
            salida["backups"] = backup_db.listar_backups_r2(cliente)
        except Exception as e:
            salida["ok"] = False
            salida["error"] = f"no se pudo listar R2: {e}"
    return jsonify(salida), 200 if salida["ok"] else 502
