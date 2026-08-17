"""Rutas del sync con Notion."""

from flask import Blueprint, current_app, jsonify, request, session

from database import get_task_by_id, log_activity
from services.notion_service import crear_pagina, traer_y_aplicar, vincular_pagina

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
    return jsonify({"ok": True, "notion_page_id": page_id})


@notion_bp.route("/api/notion/sync", methods=["POST"])
def api_sync():
    """Trae los cambios del tablero a pedido.

    Es el instrumento con el que una persona prueba que la configuracion de
    Notion quedo bien, asi que una consulta que fallo no puede contestar
    `ok: true`: el unico rastro seria un warning en los logs de Fly.
    """
    db = _db()
    cambiadas, error = traer_y_aplicar(db)
    if error:
        return jsonify({"ok": False, "error": error, "cambiadas": cambiadas}), 502

    log_activity(db, session.get("user_name", "sistema"), "notion_sync", "", None, "",
                 f"{cambiadas} tarea(s) actualizada(s) desde Notion",
                 user_id=session.get("user_id"))
    return jsonify({"ok": True, "cambiadas": cambiadas})
