"""Demo generation routes — async via job queue."""

from flask import Blueprint, current_app, jsonify, request

from database import get_job
from services.demo_service import (
    DemoAlreadyExists,
    DemoCurrentlyGenerating,
    get_existing_demo,
    request_demo,
)

demos_bp = Blueprint("demos", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


@demos_bp.route("/api/demo/generate", methods=["POST"])
def api_demo_generate():
    data = request.get_json() or {}
    client_id = data.get("client_id")
    business_name = (data.get("business_name") or "").strip()
    rubro = (data.get("rubro") or "").strip()

    if not client_id:
        return jsonify({"ok": False, "error": "client_id requerido"}), 400
    if not business_name or not rubro:
        return jsonify({"ok": False, "error": "business_name y rubro requeridos"}), 400

    params = {
        "business_name": business_name,
        "rubro": rubro,
        "city": data.get("city", ""),
        "client_color": data.get("client_color", ""),
        "lead_name": data.get("lead_name", ""),
        "messages": data.get("messages", []),
        "phone": data.get("phone", ""),
    }

    try:
        result = request_demo(_db(), int(client_id), params)
        return jsonify({"ok": True, **result}), 202
    except DemoAlreadyExists as e:
        return jsonify({"ok": False, "error": "Demo ya existe", "url": e.url}), 409
    except DemoCurrentlyGenerating:
        return jsonify({"ok": False, "error": "Demo ya está siendo generada"}), 409


@demos_bp.route("/api/demo/status/<int:client_id>", methods=["GET"])
def api_demo_status(client_id):
    demo = get_existing_demo(_db(), client_id)
    if not demo:
        return jsonify({"status": None}), 200
    return jsonify(demo), 200


@demos_bp.route("/api/jobs/<int:job_id>", methods=["GET"])
def api_job_status(job_id):
    job = get_job(_db(), job_id)
    if not job:
        return jsonify({"error": "Job no encontrado"}), 404
    return jsonify(job), 200


@demos_bp.route("/api/demo/set-url", methods=["POST"])
def api_demo_set_url():
    """Admin endpoint: link an already-deployed demo URL to a client."""
    import os
    token = request.headers.get("x-admin-token", "")
    expected = os.environ.get("ADMIN_TOKEN", "")
    if not expected or token != expected:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    data = request.get_json() or {}
    client_id = data.get("client_id")
    url = (data.get("url") or "").strip()
    if not client_id or not url:
        return jsonify({"ok": False, "error": "client_id y url requeridos"}), 400

    db = _db()
    from database import get_demo_for_client, create_demo, update_demo, update_business
    existing = get_demo_for_client(db, int(client_id))
    if existing:
        update_demo(db, existing["id"], status="completed", url=url)
        demo_id = existing["id"]
    else:
        demo_id = create_demo(db, int(client_id))
        update_demo(db, demo_id, status="completed", url=url)
    update_business(db, int(client_id), demo_url=url, status="demo_deployed")
    return jsonify({"ok": True, "demo_id": demo_id, "url": url})


@demos_bp.route("/api/demo/prompt", methods=["POST"])
def api_demo_prompt():
    import demo_ai

    data = request.get_json() or {}
    business_name = (data.get("business_name") or "").strip()
    rubro = (data.get("rubro") or "").strip()
    if not business_name or not rubro:
        return jsonify({"ok": False, "error": "business_name y rubro requeridos"}), 400

    try:
        prompt = demo_ai._chat_prompt(
            phone=data.get("phone", ""),
            business_name=business_name,
            rubro=rubro,
            city=data.get("city", "Montevideo"),
            client_color=data.get("client_color", ""),
            lead_name=data.get("lead_name", ""),
            messages=data.get("messages", []),
        )
        return jsonify({"ok": True, "prompt": prompt})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
