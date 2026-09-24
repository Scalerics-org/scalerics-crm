"""Endpoints de Recursos Humanos > Horarios.

Finos, como los de Equipo: validan con `services/horarios.py`, guardan y
serializan. Igual que en Organigrama y Ausencias, quien tiene el panel lo ve
y lo edita.
"""

from flask import Blueprint, current_app, jsonify, request, session

from database import get_persona_equipo, log_activity, reemplazar_horario_persona
from services.auth import require_panel
from services.horarios import estado, validar_semana

horarios_bp = Blueprint("horarios", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


@horarios_bp.before_request
def _candado():
    return require_panel(_db(), "horarios")


@horarios_bp.route("/api/horarios")
def api_estado():
    return jsonify(estado(_db()))


@horarios_bp.route("/api/horarios/<int:persona_id>", methods=["PUT"])
def api_guardar(persona_id):
    """Reemplaza la semana entera de una persona."""
    db = _db()
    persona = get_persona_equipo(db, persona_id)
    if not persona or not persona.get("activo") or not persona.get("lleva_horas"):
        return jsonify({"ok": False, "error": "esa persona no está en Horarios"}), 404
    tramos, error = validar_semana(request.get_json(silent=True))
    if error:
        return jsonify({"ok": False, "error": error}), 400
    reemplazar_horario_persona(db, persona_id, tramos)

    fila = next(f for f in estado(db)["personas"] if f["id"] == persona_id)
    uid, quien = session.get("user_id"), session.get("user_name", "sistema")
    log_activity(db, quien, "horario_editado", "horarios", persona_id, persona["nombre"],
                 f"{len(tramos)} tramos · {fila['texto_semana']} por semana", user_id=uid)
    return jsonify({"ok": True, "persona": fila})
