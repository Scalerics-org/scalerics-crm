"""Task kanban routes."""

from flask import Blueprint, current_app, jsonify, request, session

import os
from database import (create_task, delete_task, get_task_by_id, get_tasks,
                      log_activity, update_task, get_task_progress_history)
from database import connect as _db_connect
from services.email_service import send_task_assignment_email

tasks_bp = Blueprint("tasks", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


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
    task = get_task_by_id(db, task_id) or {}
    update_task(db, task_id, **data)
    detail = f"estado: {data['status']}" if "status" in data else ""
    log_activity(db, session.get("user_name", "sistema"), "task_updated",
                 "task", task_id, task.get("title", ""), detail,
                 user_id=session.get("user_id"))
    return jsonify({"ok": True})


@tasks_bp.route("/api/tasks/<int:task_id>", methods=["DELETE"])
def api_delete_task(task_id):
    db = _db()
    task = get_task_by_id(db, task_id) or {}
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
    uid = session.get("user_id")
    admin_email = os.environ.get("ADMIN_EMAIL", "")
    import sqlite3 as _sq
    conn2 = _db_connect(db); conn2.row_factory = _sq.Row
    try:
        u = conn2.execute("SELECT id, email FROM users WHERE id=?", (uid,)).fetchone()
    finally:
        conn2.close()
    is_admin = bool(
        u and (
            (admin_email and u["email"].lower() == admin_email.lower())
            or (not admin_email and u["id"] == 1)
        )
    )
    if not is_admin and task.get("assignee_id") != uid and task.get("created_by_id") != uid:
        return jsonify({"error": "No autorizado"}), 403
    return jsonify(get_task_progress_history(db, task_id))
