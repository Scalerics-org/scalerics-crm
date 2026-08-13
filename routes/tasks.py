"""Task kanban routes."""

from flask import Blueprint, current_app, jsonify, request, session

import os

from database import (create_task, delete_task, get_task_by_id, get_tasks,
                      log_activity, update_task, get_task_progress_history)
from services.auth import is_admin
from services.email_service import send_task_assignment_email

tasks_bp = Blueprint("tasks", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


def _puede_tocar(task: dict) -> bool:
    """Solo el asignado, quien la creo, o un admin.

    PUT y DELETE no chequeaban NADA: cualquier usuario logueado podia reasignar,
    reescribir o borrar la tarea de otro. El endpoint vecino
    (progress-history) si validaba con esta misma regla, o sea que era una
    omision y no una decision.
    """
    uid = session.get("user_id")
    if is_admin(_db(), uid):
        return True
    return uid is not None and uid in (task.get("assignee_id"), task.get("created_by_id"))


def _no_autorizado():
    return jsonify({"ok": False, "error": "Solo podés modificar tus propias tareas"}), 403


@tasks_bp.route("/api/tasks", methods=["GET"])
def api_list_tasks():
    client_id = request.args.get("client_id", type=int)
    status = request.args.get("status")
    tasks = get_tasks(_db(), client_id=client_id, status=status)
    return jsonify(tasks)


@tasks_bp.route("/api/tasks", methods=["POST"])
def api_create_task():
    data = request.get_json() or {}
    if not data.get("title"):
        return jsonify({"ok": False, "error": "title requerido"}), 400
    data["created_by_id"] = session.get("user_id")
    data["created_by_name"] = session.get("user_name", "sistema")
    db = _db()
    task_id = create_task(db, **data)
    log_activity(db, session.get("user_name", "sistema"), "task_created",
                 "task", task_id, data["title"], data["title"],
                 user_id=session.get("user_id"))
    # Send assignment email when task is assigned to someone else
    if data.get("assignee_email") and data.get("assignee_id") != session.get("user_id"):
        import threading
        import os
        crm_url = os.environ.get("CRM_URL", "")
        threading.Thread(
            target=send_task_assignment_email,
            args=(
                data["assignee_email"],
                data.get("assignee_name", ""),
                data["title"],
                data.get("description", ""),
                data.get("goal"),
                data.get("goal_type"),
                data.get("deadline"),
                session.get("user_name", "sistema"),
                crm_url,
            ),
            daemon=True,
        ).start()
    return jsonify({"ok": True, "id": task_id}), 201


@tasks_bp.route("/api/tasks/<int:task_id>", methods=["PUT"])
def api_update_task(task_id):
    data = request.get_json() or {}
    db = _db()
    task = get_task_by_id(db, task_id)
    if not task:
        return jsonify({"ok": False, "error": "Tarea no encontrada"}), 404
    if not _puede_tocar(task):
        return _no_autorizado()
    try:
        update_task(db, task_id, **data)
    except ValueError as e:
        # update_task rechaza columnas desconocidas. Es un error del cliente, no
        # del servidor: antes salia como 500.
        return jsonify({"ok": False, "error": str(e)}), 400
    detail = f"estado: {data['status']}" if "status" in data else ""
    log_activity(db, session.get("user_name", "sistema"), "task_updated",
                 "task", task_id, task.get("title", ""), detail,
                 user_id=session.get("user_id"))
    return jsonify({"ok": True})


@tasks_bp.route("/api/tasks/<int:task_id>", methods=["DELETE"])
def api_delete_task(task_id):
    db = _db()
    task = get_task_by_id(db, task_id)
    if not task:
        # Antes devolvia ok:true para una tarea inexistente, asi que el frontend
        # daba por borrado algo que nunca existio.
        return jsonify({"ok": False, "error": "Tarea no encontrada"}), 404
    if not _puede_tocar(task):
        return _no_autorizado()
    log_activity(db, session.get("user_name", "sistema"), "task_deleted",
                 "task", task_id, task.get("title", ""), "",
                 user_id=session.get("user_id"))
    delete_task(db, task_id)
    return jsonify({"ok": True})


@tasks_bp.route("/api/tasks/<int:task_id>/progress-history")
def api_task_progress_history(task_id):
    db = _db()
    task = get_task_by_id(db, task_id)
    if not task:
        return jsonify({"error": "Not found"}), 404
    # Este chequeo era una cuarta copia del mismo bloque de admin. Ahora sale de
    # services/auth, igual que PUT y DELETE.
    if not _puede_tocar(task):
        return jsonify({"error": "No autorizado"}), 403
    return jsonify(get_task_progress_history(db, task_id))
