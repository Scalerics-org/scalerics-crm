"""Task kanban routes."""

from flask import Blueprint, current_app, jsonify, request, session

from database import create_task, delete_task, get_task_by_id, get_tasks, log_activity, update_task

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
    db = _db()
    task_id = create_task(db, **data)
    log_activity(db, session.get("user_name", "sistema"), "task_created",
                 "task", task_id, data["title"], data["title"],
                 user_id=session.get("user_id"))
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
