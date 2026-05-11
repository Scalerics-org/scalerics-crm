"""Budget generation and management routes."""

import json
import os

from flask import Blueprint, current_app, jsonify, request

from database import (
    create_budget,
    get_budget_for_client,
    get_business,
    get_client_info,
    get_meetings_for_client,
    update_budget,
    update_business,
)

budgets_bp = Blueprint("budgets", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


@budgets_bp.route("/api/leads/<int:client_id>/budget", methods=["GET"])
def api_get_budget(client_id):
    budget = get_budget_for_client(_db(), client_id)
    if not budget:
        return jsonify(None), 200
    if budget.get("items") and isinstance(budget["items"], str):
        try:
            budget["items"] = json.loads(budget["items"])
        except Exception:
            budget["items"] = []
    if budget.get("notes") and isinstance(budget["notes"], str):
        try:
            budget["notes"] = json.loads(budget["notes"])
        except Exception:
            pass
    return jsonify(budget)


@budgets_bp.route("/api/leads/<int:client_id>/budget/generate", methods=["POST"])
def api_generate_budget(client_id):
    import anthropic

    data = request.get_json() or {}
    client = get_business(_db(), client_id)
    if not client:
        return jsonify({"ok": False, "error": "Lead no encontrado"}), 404

    client_info = get_client_info(_db(), client_id) or {}
    meetings = get_meetings_for_client(_db(), client_id)

    # Gather requirements from all sources
    extra_req = (data.get("requirements") or "").strip()
    meeting_reqs = "\n".join(
        m["requirements"] for m in meetings if m.get("requirements")
    )
    requirements = "\n".join(filter(None, [extra_req, meeting_reqs])) or "sitio web profesional con diseño moderno"

    service_type = (
        client_info.get("rubro")
        or data.get("service_type")
        or client.get("category", "desarrollo web")
    )
    budget_range = client_info.get("budget_range") or data.get("budget_range") or "no especificado"
    team_size = client_info.get("needs") or "no especificado"

    prompt = f"""Generá un presupuesto profesional para un proyecto de {service_type} para el negocio "{client.get('name', '')}".

Información del cliente:
- Negocio: {client.get('name', '')} ({client.get('category', '')})
- Ciudad: {client.get('city', 'Montevideo')}, Uruguay
- Tamaño del equipo: {team_size}
- Rango de presupuesto indicado: {budget_range}
- Requerimientos del proyecto:
{requirements}

Devolvé SOLO un JSON con este formato exacto (sin texto extra, sin markdown):
{{
  "items": [
    {{"name": "Diseño y maquetado", "description": "Diseño responsive en Figma, paleta de marca", "hours": 20, "unit_price": 50, "total": 1000}},
    {{"name": "Desarrollo frontend", "description": "HTML/CSS/JS responsive, SEO básico", "hours": 30, "unit_price": 50, "total": 1500}}
  ],
  "subtotal": 2500,
  "discount": 0,
  "discount_note": "",
  "total": 2500,
  "currency": "USD",
  "payment_terms": "50% adelanto, 50% al entregar",
  "validity_days": 30,
  "notes": "Incluye 2 rondas de revisiones. Hosting y dominio no incluidos."
}}

Precios en USD, realistas para el mercado uruguayo (desarrolladores ~$40-70/h).
Mínimo 3 ítems, máximo 7. Adaptá los ítems al tipo de proyecto."""

    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        ai = anthropic.Anthropic(api_key=api_key)
        msg = ai.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rstrip("`").strip()
        budget_data = json.loads(raw)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Error generando presupuesto: {e}"}), 500

    items_json = json.dumps(budget_data.get("items", []), ensure_ascii=False)
    total = float(budget_data.get("total", 0))
    meta = {k: v for k, v in budget_data.items() if k != "items"}
    notes_json = json.dumps(meta, ensure_ascii=False)

    existing = get_budget_for_client(_db(), client_id)
    if existing:
        update_budget(_db(), existing["id"], items=items_json, total_amount=total, notes=notes_json, status="draft")
        budget_id = existing["id"]
    else:
        budget_id = create_budget(_db(), client_id, items=items_json, total_amount=total, notes=notes_json)

    return jsonify({"ok": True, "budget_id": budget_id, "data": budget_data})


@budgets_bp.route("/api/budgets/<int:budget_id>", methods=["PUT"])
def api_update_budget(budget_id):
    data = request.get_json() or {}
    fields: dict = {}
    if "items" in data:
        fields["items"] = json.dumps(data["items"], ensure_ascii=False) if isinstance(data["items"], list) else data["items"]
    if "total_amount" in data:
        fields["total_amount"] = float(data["total_amount"])
    if "notes" in data:
        fields["notes"] = json.dumps(data["notes"], ensure_ascii=False) if isinstance(data["notes"], dict) else data["notes"]
    if "status" in data:
        fields["status"] = data["status"]
    if fields:
        update_budget(_db(), budget_id, **fields)
    return jsonify({"ok": True})


@budgets_bp.route("/api/budgets/<int:budget_id>/mark-sent", methods=["POST"])
def api_mark_budget_sent(budget_id):
    import datetime
    update_budget(_db(), budget_id, status="sent", sent_at=datetime.datetime.now().isoformat())
    return jsonify({"ok": True})
