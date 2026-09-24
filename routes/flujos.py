"""Endpoints del panel Flujos, en Recursos Humanos.

Leer: quien ve Flujos, Organigrama o Ausencias. Flujos era un bloque al final
de Ausencias; al pasar a panel propio, nadie pierde lo que ya leía.
Agregar, editar, reordenar o borrar pasos: solo un administrador, con la misma
`is_admin` que usa `/api/me` para decidirlo en la pantalla.
"""

from flask import Blueprint, current_app, jsonify, request, session

from database import (borrar_paso_flujo, crear_paso_flujo, editar_paso_flujo,
                      get_flujo, get_paso_flujo, log_activity, mover_paso_flujo)
from routes.equipo import PANELES_RRHH
from services.auth import is_admin, require_admin, require_panel, tiene_panel
from services.flujos import ROLES, estado_flujos, estilos_roles, validar_paso

flujos_bp = Blueprint("flujos", __name__)

# Cualquiera de estos tres abre la lectura.
PANELES_LECTURA = ("flujos",) + PANELES_RRHH


def _db() -> str:
    return current_app.config["DB_PATH"]


@flujos_bp.before_request
def _candado():
    db, uid = _db(), session.get("user_id")
    if not any(tiene_panel(db, uid, p) for p in PANELES_LECTURA):
        return require_panel(db, "flujos")
    if request.method != "GET":
        return require_admin(db)
    return None


def _quien() -> tuple[int | None, str]:
    return session.get("user_id"), session.get("user_name", "sistema")


@flujos_bp.route("/api/flujos")
def api_flujos():
    db = _db()
    return jsonify({"ok": True, "es_admin": is_admin(db, session.get("user_id")),
                    "roles": list(ROLES), "estilos": estilos_roles(),
                    "flujos": estado_flujos(db)})


@flujos_bp.route("/api/flujos/<int:fid>/pasos", methods=["POST"])
def api_crear_paso(fid):
    db = _db()
    flujo = get_flujo(db, fid)
    if not flujo:
        return jsonify({"ok": False, "error": "ese flujo no existe"}), 404
    campos, error = validar_paso(request.get_json(silent=True))
    if error:
        return jsonify({"ok": False, "error": error}), 400
    pid = crear_paso_flujo(db, fid, **campos)
    uid, quien = _quien()
    log_activity(db, quien, "flujo_paso_agregado", "flujo", fid, flujo["nombre"],
                 f"{campos['titulo']} · {campos['rol']}", user_id=uid)
    return jsonify({"ok": True, "id": pid}), 201


@flujos_bp.route("/api/flujos/pasos/<int:pid>", methods=["PUT"])
def api_editar_paso(pid):
    db = _db()
    paso = get_paso_flujo(db, pid)
    if not paso:
        return jsonify({"ok": False, "error": "ese paso no existe"}), 404
    campos, error = validar_paso(request.get_json(silent=True))
    if error:
        return jsonify({"ok": False, "error": error}), 400
    editar_paso_flujo(db, pid, **campos)
    uid, quien = _quien()
    log_activity(db, quien, "flujo_paso_editado", "flujo", paso["flujo_id"], campos["titulo"],
                 f"paso {paso['numero']} · {campos['rol']}", user_id=uid)
    return jsonify({"ok": True})


@flujos_bp.route("/api/flujos/pasos/<int:pid>", methods=["DELETE"])
def api_borrar_paso(pid):
    db = _db()
    paso = get_paso_flujo(db, pid)
    if not paso:
        return jsonify({"ok": False, "error": "ese paso no existe"}), 404
    borrar_paso_flujo(db, pid)
    uid, quien = _quien()
    log_activity(db, quien, "flujo_paso_borrado", "flujo", paso["flujo_id"], paso["titulo"],
                 f"era el paso {paso['numero']}", user_id=uid)
    return jsonify({"ok": True})


@flujos_bp.route("/api/flujos/pasos/<int:pid>/mover", methods=["POST"])
def api_mover_paso(pid):
    """`{"posicion": n}` lo lleva a esa posición; `{"delta": -1|1}` lo sube o
    lo baja uno. Los números del flujo se corrigen solos."""
    db = _db()
    paso = get_paso_flujo(db, pid)
    if not paso:
        return jsonify({"ok": False, "error": "ese paso no existe"}), 404
    datos = request.get_json(silent=True) or {}
    posicion = datos.get("posicion")
    delta = datos.get("delta")
    if isinstance(posicion, int) and not isinstance(posicion, bool):
        destino = posicion
    elif delta in (-1, 1) and not isinstance(delta, bool):
        destino = paso["numero"] + delta
    else:
        return jsonify({"ok": False, "error": "mandá posicion (número) o delta (-1 o 1)"}), 400
    numero = mover_paso_flujo(db, pid, destino)
    uid, quien = _quien()
    log_activity(db, quien, "flujo_paso_movido", "flujo", paso["flujo_id"], paso["titulo"],
                 f"del {paso['numero']} al {numero}", user_id=uid)
    return jsonify({"ok": True, "numero": numero})
