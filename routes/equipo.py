"""Endpoints de la sección Equipo.

Finos, como los de Finanzas y el Simulador: validan, llaman a
`services/equipo.py` y serializan. Nada de esto maneja plata.
"""

from datetime import date

from flask import Blueprint, current_app, jsonify, request, session

from database import (actualizar_rol_flujo_persona, borrar_ausencia_equipo,
                      borrar_recupero_equipo, crear_ausencia_equipo,
                      crear_recupero_equipo, get_ausencia_equipo,
                      get_persona_equipo, get_recupero_equipo,
                      listar_personas_equipo, log_activity)
from services.auth import is_admin, require_admin, require_panel, tiene_panel
from services.equipo import (capacidad, estado, parse_fecha, validar_ausencia,
                             validar_recupero)
from services.flujos import estilo_de_persona, validar_rol_flujo

equipo_bp = Blueprint("equipo", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


def _hoy() -> date:
    """Aparte para poder fijar el día en los tests."""
    return date.today()


# Recursos Humanos son dos paneles que leen el mismo `GET /api/equipo`:
# Organigrama (conserva el id `equipo`, así los permisos guardados siguen
# valiendo) y Ausencias. Cualquiera de los dos abre todas las rutas.
PANELES_RRHH = ("equipo", "ausencias")


@equipo_bp.before_request
def _candado():
    """Todo pide Organigrama o Ausencias. La capacidad la lee también el
    Simulador financiero, así que ahí alcanza además con ese panel."""
    db, uid = _db(), session.get("user_id")
    paneles = PANELES_RRHH
    if request.endpoint == "equipo.api_capacidad":
        paneles = PANELES_RRHH + ("simulador",)
    if any(tiene_panel(db, uid, p) for p in paneles):
        return None
    return require_panel(db, "equipo")


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
    datos = estado(_db(), _hoy(), desde)
    # Solo el admin cambia el rol en Flujos desde el organigrama.
    datos["es_admin"] = is_admin(_db(), session.get("user_id"))
    return jsonify(datos)


@equipo_bp.route("/api/equipo/personas/<int:pid>/rol-flujo", methods=["PUT"])
def api_rol_flujo(pid):
    """Cambia el rol en Flujos de una persona, que define su color en el
    organigrama. Solo admin; la lista de roles es la cerrada de Flujos."""
    db = _db()
    no_admin = require_admin(db)
    if no_admin:
        return no_admin
    persona = get_persona_equipo(db, pid)
    if not persona or not persona.get("activo"):
        return jsonify({"ok": False, "error": "esa persona no está en el organigrama"}), 404
    campos, error = validar_rol_flujo(request.get_json(silent=True))
    if error:
        return jsonify({"ok": False, "error": error}), 400
    actualizar_rol_flujo_persona(db, pid, campos["rol_flujo"])
    estilo = estilo_de_persona(campos["rol_flujo"])
    uid, quien = _quien()
    log_activity(db, quien, "equipo_rol_flujo", "equipo", pid, persona["nombre"],
                 estilo["etiqueta"], user_id=uid)
    return jsonify({"ok": True, "rol_flujo": estilo["rol"], "color": estilo["color"],
                    "etiqueta": estilo["etiqueta"]})


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
