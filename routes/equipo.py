"""Endpoints de la sección Equipo.

Finos, como los de Finanzas y el Simulador: validan, llaman a
`services/equipo.py` y serializan. Nada de esto maneja plata.
"""

from datetime import date

from flask import Blueprint, current_app, jsonify, request, session

from database import (borrar_ausencia_equipo, borrar_recupero_equipo,
                      crear_ausencia_equipo, crear_recupero_equipo,
                      get_ausencia_equipo, get_persona_equipo,
                      get_recupero_equipo, listar_personas_equipo, log_activity)
from services.auth import require_panel, tiene_panel
from services.equipo import (capacidad, estado, parse_fecha, validar_ausencia,
                             validar_recupero)

equipo_bp = Blueprint("equipo", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


def _hoy() -> date:
    """Aparte para poder fijar el día en los tests."""
    return date.today()


@equipo_bp.before_request
def _candado():
    """Todo pide el panel `equipo`. La capacidad la lee también el Simulador
    financiero, así que ahí alcanza con cualquiera de los dos paneles."""
    if request.endpoint == "equipo.api_capacidad" and tiene_panel(
            _db(), session.get("user_id"), "simulador"):
        return None
    return require_panel(_db(), "equipo")


def _quien() -> tuple[int | None, str]:
    return session.get("user_id"), session.get("user_name", "sistema")


@equipo_bp.route("/api/equipo")
def api_estado():
    crudo = request.args.get("desde")
    desde = None
    if crudo:
        desde = parse_fecha(crudo)
        if desde is None:
            return jsonify({"ok": False, "error": "desde tiene que ser AAAA-MM-DD"}), 400
    return jsonify(estado(_db(), _hoy(), desde))


@equipo_bp.route("/api/equipo/capacidad")
def api_capacidad():
    crudo = request.args.get("semana")
    if crudo:
        semana = parse_fecha(crudo)
        if semana is None:
            return jsonify({"ok": False, "error": "semana tiene que ser AAAA-MM-DD"}), 400
    else:
        semana = _hoy()
    return jsonify(capacidad(_db(), semana))


@equipo_bp.route("/api/equipo/ausencias", methods=["POST"])
def api_crear_ausencia():
    db = _db()
    personas = {p["id"]: p for p in listar_personas_equipo(db)}
    campos, error = validar_ausencia(request.get_json(silent=True), personas)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    uid, quien = _quien()
    aid = crear_ausencia_equipo(db, created_by_id=uid, created_by_name=quien, **campos)
    persona = personas[campos["persona_id"]]["nombre"]
    log_activity(db, quien, "equipo_ausencia_registrada", "equipo", aid, persona,
                 f"{campos['fecha_desde']} a {campos['fecha_hasta']} · "
                 f"{campos['horas_totales']:g} h · {campos['motivo']}", user_id=uid)
    return jsonify({"ok": True, "id": aid, "horas_totales": campos["horas_totales"]}), 201


@equipo_bp.route("/api/equipo/ausencias/<int:aid>", methods=["DELETE"])
def api_borrar_ausencia(aid):
    db = _db()
    aus = get_ausencia_equipo(db, aid)
    if not aus:
        return jsonify({"ok": False, "error": "no existe"}), 404
    borrar_ausencia_equipo(db, aid)
    persona = get_persona_equipo(db, aus["persona_id"]) or {}
    uid, quien = _quien()
    log_activity(db, quien, "equipo_ausencia_borrada", "equipo", aid,
                 persona.get("nombre", ""), aus["motivo"], user_id=uid)
    return jsonify({"ok": True})


@equipo_bp.route("/api/equipo/ausencias/<int:aid>/recuperos", methods=["POST"])
def api_crear_recupero(aid):
    db = _db()
    aus = get_ausencia_equipo(db, aid)
    if not aus:
        return jsonify({"ok": False, "error": "esa ausencia no existe"}), 404
    campos, error = validar_recupero(request.get_json(silent=True))
    if error:
        return jsonify({"ok": False, "error": error}), 400
    uid, quien = _quien()
    rid = crear_recupero_equipo(db, aid, campos["fecha"], campos["horas"],
                                created_by_id=uid, created_by_name=quien)
    persona = get_persona_equipo(db, aus["persona_id"]) or {}
    log_activity(db, quien, "equipo_recupero_agendado", "equipo", aid,
                 persona.get("nombre", ""), f"{campos['fecha']} · {campos['horas']:g} h",
                 user_id=uid)
    return jsonify({"ok": True, "id": rid}), 201


@equipo_bp.route("/api/equipo/recuperos/<int:rid>", methods=["DELETE"])
def api_borrar_recupero(rid):
    db = _db()
    rec = get_recupero_equipo(db, rid)
    if not rec:
        return jsonify({"ok": False, "error": "no existe"}), 404
    borrar_recupero_equipo(db, rid)
    uid, quien = _quien()
    log_activity(db, quien, "equipo_recupero_borrado", "equipo", rec["ausencia_id"],
                 "", f"{rec['fecha']} · {rec['horas']:g} h", user_id=uid)
    return jsonify({"ok": True})
