"""Endpoints de la sección Plantillas (VENTAS).

Finos, como los de Equipo: validan, llaman a database/services y serializan.
Mandar un mensaje NO pasa por acá: la pantalla usa el `POST /api/wa/send` que
ya existe, después de pedir confirmación.
"""

from flask import Blueprint, current_app, jsonify, request, session

from database import (actualizar_plantilla, borrar_plantilla, crear_plantilla,
                      get_plantilla, listar_plantillas, log_activity)
from services.auth import require_panel, tiene_panel
from services.plantillas import (VARIABLES, buscar_leads, validar_plantilla,
                                 variables_del_lead)

plantillas_bp = Blueprint("plantillas", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


@plantillas_bp.before_request
def _candado():
    return require_panel(_db(), "plantillas")


def _quien() -> tuple[int | None, str]:
    return session.get("user_id"), session.get("user_name", "sistema")


@plantillas_bp.route("/api/plantillas")
def api_listar():
    return jsonify({"plantillas": listar_plantillas(_db()), "variables": list(VARIABLES)})


@plantillas_bp.route("/api/plantillas", methods=["POST"])
def api_crear():
    campos, error = validar_plantilla(request.get_json(silent=True))
    if error:
        return jsonify({"ok": False, "error": error}), 400
    db = _db()
    uid, quien = _quien()
    pid = crear_plantilla(db, created_by_name=quien, **campos)
    log_activity(db, quien, "plantilla_creada", "plantilla", pid, campos["titulo"], "",
                 user_id=uid)
    return jsonify({"ok": True, "id": pid}), 201


@plantillas_bp.route("/api/plantillas/<int:pid>", methods=["PUT"])
def api_actualizar(pid):
    db = _db()
    if not get_plantilla(db, pid):
        return jsonify({"ok": False, "error": "no existe"}), 404
    campos, error = validar_plantilla(request.get_json(silent=True))
    if error:
        return jsonify({"ok": False, "error": error}), 400
    actualizar_plantilla(db, pid, **campos)
    uid, quien = _quien()
    log_activity(db, quien, "plantilla_editada", "plantilla", pid, campos["titulo"], "",
                 user_id=uid)
    return jsonify({"ok": True})


@plantillas_bp.route("/api/plantillas/<int:pid>", methods=["DELETE"])
def api_borrar(pid):
    db = _db()
    plantilla = get_plantilla(db, pid)
    if not plantilla:
        return jsonify({"ok": False, "error": "no existe"}), 404
    borrar_plantilla(db, pid)
    uid, quien = _quien()
    log_activity(db, quien, "plantilla_borrada", "plantilla", pid, plantilla["titulo"], "",
                 user_id=uid)
    return jsonify({"ok": True})


@plantillas_bp.route("/api/plantillas/leads")
def api_buscar_leads():
    return jsonify({"leads": buscar_leads(_db(), request.args.get("q", ""))})


@plantillas_bp.route("/api/plantillas/leads/<int:lead_id>/variables")
def api_variables(lead_id):
    db = _db()
    con_finanzas = tiene_panel(db, session.get("user_id"), "finanzas")
    datos = variables_del_lead(db, lead_id, con_finanzas=con_finanzas)
    if datos is None:
        return jsonify({"ok": False, "error": "Lead no encontrado"}), 404
    return jsonify(datos)
