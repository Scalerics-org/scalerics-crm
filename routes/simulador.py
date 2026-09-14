"""Endpoints del simulador financiero.

Finos, como los de Finanzas: validan, llaman a `services/simulador.py` y
serializan. Ninguno escribe en las tablas de Finanzas: lo único que se escribe
es `simulador_escenarios`.
"""

import json

from flask import Blueprint, current_app, jsonify, request, session

from database import (actualizar_escenario, borrar_escenario, crear_escenario,
                      get_escenario, listar_escenarios, log_activity)
from services.auth import require_panel
from services.simulador import precarga, validar_escenario

simulador_bp = Blueprint("simulador", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


@simulador_bp.before_request
def _candado():
    """Una línea para todo el blueprint, igual que Finanzas: la precarga trae
    sueldos y lo que se le debe a la empresa."""
    return require_panel(_db(), "simulador")


def _quien() -> tuple[int | None, str]:
    return session.get("user_id"), session.get("user_name", "sistema")


@simulador_bp.route("/api/simulador/precarga")
def api_precarga():
    return jsonify(precarga(_db()))


@simulador_bp.route("/api/simulador/escenarios", methods=["GET"])
def api_listar_escenarios():
    return jsonify(listar_escenarios(_db()))


@simulador_bp.route("/api/simulador/escenarios/<int:esc_id>", methods=["GET"])
def api_get_escenario(esc_id):
    esc = get_escenario(_db(), esc_id)
    if not esc:
        return jsonify({"ok": False, "error": "no existe"}), 404
    try:
        datos = json.loads(esc["datos"])
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "el escenario guardado está dañado"}), 500
    return jsonify({**esc, "datos": datos})


@simulador_bp.route("/api/simulador/escenarios", methods=["POST"])
def api_crear_escenario():
    validado, error = validar_escenario(request.get_json(silent=True))
    if error:
        return jsonify({"ok": False, "error": error}), 400
    nombre, datos = validado
    uid, quien = _quien()
    db = _db()
    esc_id = crear_escenario(db, nombre, datos, created_by_id=uid,
                             created_by_name=quien)
    log_activity(db, quien, "simulador_escenario_guardado", "simulador", esc_id,
                 nombre, "", user_id=uid)
    return jsonify({"ok": True, "id": esc_id}), 201


@simulador_bp.route("/api/simulador/escenarios/<int:esc_id>", methods=["PUT"])
def api_actualizar_escenario(esc_id):
    db = _db()
    if not get_escenario(db, esc_id):
        return jsonify({"ok": False, "error": "no existe"}), 404
    validado, error = validar_escenario(request.get_json(silent=True))
    if error:
        return jsonify({"ok": False, "error": error}), 400
    nombre, datos = validado
    actualizar_escenario(db, esc_id, nombre, datos)
    uid, quien = _quien()
    log_activity(db, quien, "simulador_escenario_actualizado", "simulador",
                 esc_id, nombre, "", user_id=uid)
    return jsonify({"ok": True, "id": esc_id})


@simulador_bp.route("/api/simulador/escenarios/<int:esc_id>", methods=["DELETE"])
def api_borrar_escenario(esc_id):
    db = _db()
    esc = get_escenario(db, esc_id)
    if not esc:
        return jsonify({"ok": False, "error": "no existe"}), 404
    borrar_escenario(db, esc_id)
    uid, quien = _quien()
    log_activity(db, quien, "simulador_escenario_borrado", "simulador", esc_id,
                 esc["nombre"], "", user_id=uid)
    return jsonify({"ok": True})
