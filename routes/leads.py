"""Lead / business CRUD routes."""

import os
import threading

from flask import Blueprint, current_app, jsonify, request

from database import get_all_businesses, update_business, delete_business, get_business, get_client_info, insert_business
from pitch_generator import generate_pitch

leads_bp = Blueprint("leads", __name__)

_CATEGORY_BLOCKLIST_KEYWORDS = [
    "agregar ", "add website", "e-commerce", "centro comercial",
]

_CATEGORY_KEYWORD_MAP = [
    # construcción — cualquier variante
    (["construc", "bloquera", "bloque", "hormigon", "barraca", "prefabric",
      "ladrillo", "materiales de construc"], "Construcción"),
    # ferretería
    (["ferreteri", "ferreter", "herramientas"], "Ferretería"),
    # distribución
    (["distribui", "mayorist", "proveedor mayorist", "servicio de distribu"], "Distribuidora"),
    # peluquería
    (["hairdress", "peluquer", "estilis", "barber", "coiffeur"], "Hair salon"),
    # flores
    (["mercado de flores", "floriste", "floral"], "Mercado de flores"),
]


def _normalize_category(raw: str) -> str | None:
    low = (raw or "").strip().lower()
    if not low:
        return None
    if any(kw in low for kw in _CATEGORY_BLOCKLIST_KEYWORDS):
        return None
    for keywords, canonical in _CATEGORY_KEYWORD_MAP:
        if any(kw in low for kw in keywords):
            return canonical
    return raw.strip()


# Full set of valid CRM states
_VALID_CRM_STATES = {
    "sin_contactar", "contactado", "reunion_agendada", "demo_generada",
    "reunion_hecha", "presupuesto_enviado", "negociacion",
    "cliente_cerrado", "en_desarrollo", "finalizado",
    # legacy aliases kept for backwards compat
    "agendo", "firmo",
}


def _db() -> str:
    return current_app.config["DB_PATH"]


def _bg_pitch(db_path: str, business_id: int, data: dict) -> None:
    try:
        pitch = generate_pitch(data, db_path)
        if pitch:
            update_business(db_path, business_id, pitch_text=pitch)
    except Exception:
        pass


@leads_bp.route("/api/leads", methods=["POST"])
def api_create_lead():
    """Remote insert used by local scraper → Railway CRM sync."""
    token = request.headers.get("x-admin-token", "")
    expected = os.environ.get("ADMIN_TOKEN", "")
    if not expected or token != expected:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    business_id = insert_business(_db(), data)
    if business_id:
        threading.Thread(target=_bg_pitch, args=(_db(), business_id, data), daemon=True).start()
        return jsonify({"ok": True, "id": business_id}), 201
    return jsonify({"ok": False, "reason": "duplicate"}), 200


@leads_bp.route("/api/leads")
def api_leads():
    businesses = get_all_businesses(_db())
    crm_status = request.args.get("crm_status")
    category = request.args.get("category")
    search = (request.args.get("search") or "").lower()
    if crm_status:
        businesses = [b for b in businesses if (b.get("crm_status") or "sin_contactar") == crm_status]
    if category:
        businesses = [b for b in businesses if _normalize_category(b.get("category") or "") == category]
    if search:
        businesses = [b for b in businesses if search in (b.get("name") or "").lower()]
    return jsonify(businesses)


@leads_bp.route("/api/leads/<int:biz_id>", methods=["GET"])
def api_get_lead(biz_id):
    biz = get_business(_db(), biz_id)
    if not biz:
        return jsonify({"error": "Lead no encontrado"}), 404
    biz["client_info"] = get_client_info(_db(), biz_id) or {}
    return jsonify(biz)


@leads_bp.route("/api/leads/<int:biz_id>/crm-status", methods=["POST"])
def api_crm_status(biz_id):
    data = request.get_json() or {}
    crm_status = data.get("crm_status", "sin_contactar")
    if crm_status not in _VALID_CRM_STATES:
        return jsonify({"ok": False, "error": f"Estado inválido: {crm_status}"}), 400
    update_business(_db(), biz_id, crm_status=crm_status)
    return jsonify({"ok": True})


@leads_bp.route("/api/leads/<int:biz_id>", methods=["DELETE"])
def api_delete_lead(biz_id):
    delete_business(_db(), biz_id)
    return jsonify({"ok": True})


@leads_bp.route("/api/leads/<int:biz_id>/contact", methods=["POST"])
def api_contact(biz_id):
    data = request.get_json() or {}
    note = data.get("note", "")
    update_business(_db(), biz_id, status="contacted", notes=note, crm_status="contactado")
    return jsonify({"ok": True})


@leads_bp.route("/api/leads/<int:biz_id>/notes", methods=["PUT"])
def api_update_notes(biz_id):
    data = request.get_json() or {}
    notes = data.get("notes", "")
    update_business(_db(), biz_id, notes=notes)
    return jsonify({"ok": True})


@leads_bp.route("/api/leads/<int:biz_id>/pitch", methods=["PUT"])
def api_update_pitch(biz_id):
    data = request.get_json() or {}
    pitch_text = data.get("pitch_text", "")
    update_business(_db(), biz_id, pitch_text=pitch_text)
    return jsonify({"ok": True})


@leads_bp.route("/api/stats")
def api_stats():
    businesses = get_all_businesses(_db())
    cats = set()
    for b in businesses:
        n = _normalize_category(b.get("category") or "")
        if n:
            cats.add(n)
    categories = sorted(cats)
    return jsonify({
        "total": len(businesses),
        "with_pitch": sum(1 for b in businesses if b.get("pitch_text")),
        "with_demo": sum(1 for b in businesses if b.get("demo_url")),
        "contacted": sum(1 for b in businesses if (b.get("crm_status") or "") not in ("sin_contactar", "")),
        "categories": categories,
    })


