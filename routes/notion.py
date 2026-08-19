"""Rutas del sync con Notion."""

import logging

from flask import Blueprint, current_app, jsonify, request, session

from database import get_task_by_id, log_activity
from services.notion_service import (GRUPOS, crear_pagina, empujar_estado_exacto,
                                     grupo_de, traer_clientes, traer_proyectos,
                                     traer_y_aplicar, vincular_pagina)

logger = logging.getLogger(__name__)

notion_bp = Blueprint("notion", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


@notion_bp.route("/api/tasks/<int:task_id>/notion", methods=["POST"])
def api_vincular_tarea(task_id):
    """Manda la tarea al tablero, o la pega a una tarjeta que ya existe.

    Nada de esto pasa solo: el tablero de Notion es del equipo y se llena a
    mano, tarea por tarea.
    """
    db = _db()
    tarea = get_task_by_id(db, task_id)
    if not tarea:
        return jsonify({"ok": False, "error": "la tarea no existe"}), 404

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    page_id = vincular_pagina(db, task_id, url) if url else crear_pagina(db, task_id)
    if not page_id:
        return jsonify({"ok": False, "error": "Notion no aceptó la operación"}), 502

    log_activity(db, session.get("user_name", "sistema"), "task_updated", "task",
                 task_id, tarea.get("title", ""),
                 "vinculada a Notion" if url else "enviada a Notion",
                 user_id=session.get("user_id"))
    # El estado que quedo guardado va en la respuesta para que el front pueda
    # pintar el title del badge sin recargar. Sale de la tarea, no se inventa.
    despues = get_task_by_id(db, task_id) or {}
    return jsonify({"ok": True, "notion_page_id": page_id,
                    "notion_status": despues.get("notion_status")})


@notion_bp.route("/api/notion/sync", methods=["POST"])
def api_sync():
    """Trae los cambios del tablero a pedido.

    Es el instrumento con el que una persona prueba que la configuracion de
    Notion quedo bien, asi que una consulta que fallo no puede contestar
    `ok: true`: el unico rastro seria un warning en los logs de Fly. Tambien
    trae proyectos -- la verificacion manual del plan arranca justamente por
    confirmar que aparecen los cinco -- en su propio try, para que un fallo
    ahi (devuelto o excepcion) no se lleve puesto el pull de tareas, que es lo
    que importa mantener al dia.
    """
    db = _db()
    try:
        proyectos, error_p = traer_proyectos(db)
    except Exception:
        proyectos, error_p = 0, "el pull de proyectos fallo inesperadamente"
        logger.warning("notion proyectos falló", exc_info=True)
    if error_p:
        logger.warning("notion proyectos: %s", error_p)

    try:
        clientes, error_c = traer_clientes(db)
    except Exception:
        clientes, error_c = 0, "el pull de clientes fallo inesperadamente"
        logger.warning("notion clientes falló", exc_info=True)
    if error_c:
        logger.warning("notion clientes: %s", error_c)

    cambiadas, error = traer_y_aplicar(db)
    if error:
        return jsonify({"ok": False, "error": error, "cambiadas": cambiadas,
                        "proyectos": proyectos, "proyectos_error": error_p,
                        "clientes": clientes, "clientes_error": error_c}), 502

    log_activity(db, session.get("user_name", "sistema"), "notion_sync", "", None, "",
                 f"{cambiadas} tarea(s) actualizada(s) desde Notion",
                 user_id=session.get("user_id"))
    return jsonify({"ok": True, "cambiadas": cambiadas,
                    "proyectos": proyectos, "proyectos_error": error_p,
                    "clientes": clientes, "clientes_error": error_c})


@notion_bp.route("/api/tasks/<int:task_id>/notion/estado", methods=["POST"])
def api_mover_a_columna(task_id):
    """Mueve una tarea a una columna del kanban, escribiendo en Notion primero.

    Sincrono a proposito: el front revierte la tarjeta a su columna si esto
    falla, asi que no puede contestar antes de saber si Notion acepto.
    """
    db = _db()
    tarea = get_task_by_id(db, task_id)
    if not tarea:
        return jsonify({"ok": False, "error": "la tarea no existe"}), 404

    estado = ((request.get_json(silent=True) or {}).get("estado") or "").strip()
    if estado not in GRUPOS:
        return jsonify({"ok": False, "error": f"'{estado}' no es una columna del tablero"}), 400

    ok, error = empujar_estado_exacto(db, task_id, estado)
    if not ok:
        return jsonify({"ok": False, "error": error}), 502

    log_activity(db, session.get("user_name", "sistema"), "task_updated", "task",
                 task_id, tarea.get("title", ""), f"movida a {estado} en Notion",
                 user_id=session.get("user_id"))
    return jsonify({"ok": True, "estado": estado, "status": grupo_de(estado)})
