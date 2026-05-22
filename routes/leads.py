"""Lead / business CRUD routes."""

import os
import threading

from flask import Blueprint, Response, current_app, jsonify, request
from werkzeug.utils import secure_filename

from database import (get_all_businesses, update_business, delete_business, get_business,
                      get_client_info, insert_business,
                      add_attachment, get_attachments, get_attachment_file, delete_attachment,
                      add_lead_event, get_lead_events)
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

_PER_PAGE = 50


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
    crm_status = request.args.get("crm_status")
    category = request.args.get("category")
    search = (request.args.get("search") or "").lower()
    page_str = request.args.get("page")
    businesses = get_all_businesses(_db(), crm_status=crm_status)
    if category:
        businesses = [b for b in businesses if _normalize_category(b.get("category") or "") == category]
    if search:
        businesses = [b for b in businesses if search in (b.get("name") or "").lower()]
    if page_str is not None:
        try:
            page = max(1, int(page_str))
        except ValueError:
            page = 1
        total = len(businesses)
        pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
        page = min(page, pages)
        offset = (page - 1) * _PER_PAGE
        return jsonify({"items": businesses[offset:offset + _PER_PAGE], "total": total, "pages": pages, "page": page})
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
    db = _db()
    update_business(db, biz_id, crm_status=crm_status)
    add_lead_event(db, biz_id, crm_status)
    return jsonify({"ok": True})


@leads_bp.route("/api/leads/batch-status", methods=["POST"])
def api_batch_status():
    data = request.get_json() or {}
    ids = data.get("ids", [])
    crm_status = data.get("crm_status", "")
    if not ids or crm_status not in _VALID_CRM_STATES:
        return jsonify({"ok": False, "error": "ids o estado inválido"}), 400
    db = _db()
    for biz_id in ids:
        biz_id = int(biz_id)
        update_business(db, biz_id, crm_status=crm_status)
        add_lead_event(db, biz_id, crm_status)
    return jsonify({"ok": True, "updated": len(ids)})


@leads_bp.route("/api/leads/<int:biz_id>/events")
def api_lead_events(biz_id):
    return jsonify(get_lead_events(_db(), biz_id))


@leads_bp.route("/api/leads/<int:biz_id>", methods=["DELETE"])
def api_delete_lead(biz_id):
    delete_business(_db(), biz_id)
    return jsonify({"ok": True})


@leads_bp.route("/api/leads/<int:biz_id>/contact", methods=["POST"])
def api_contact(biz_id):
    data = request.get_json() or {}
    note = data.get("note", "")
    db = _db()
    update_business(db, biz_id, status="contacted", notes=note, crm_status="contactado")
    add_lead_event(db, biz_id, "contactado", note=note)
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


@leads_bp.route("/api/metrics")
def api_metrics():
    from collections import Counter, defaultdict
    businesses = get_all_businesses(_db())

    # Funnel por estado CRM
    funnel_order = [
        "sin_contactar", "contactado", "reunion_agendada", "demo_generada",
        "reunion_hecha", "presupuesto_enviado", "negociacion",
        "cliente_cerrado", "en_desarrollo", "finalizado",
    ]
    _legacy = {"firmo": "cliente_cerrado", "agendo": "reunion_agendada"}
    def _norm(s): return _legacy.get(s or "sin_contactar", s or "sin_contactar")
    crm_counts = Counter(_norm(b.get("crm_status")) for b in businesses)
    funnel = [{"status": s, "count": crm_counts.get(s, 0)} for s in funnel_order]

    # Top rubros
    rubro_counts = Counter(
        _normalize_category(b.get("category") or "") for b in businesses
        if _normalize_category(b.get("category") or "")
    )
    top_rubros = [{"name": k, "count": v} for k, v in rubro_counts.most_common(10)]

    # Top ciudades
    city_counts = Counter(
        (b.get("city") or "").strip() for b in businesses if (b.get("city") or "").strip()
    )
    top_cities = [{"name": k, "count": v} for k, v in city_counts.most_common(10)]

    # Leads por mes (últimos 12)
    month_counts: dict = defaultdict(int)
    for b in businesses:
        ts = b.get("scraped_at") or ""
        if ts and len(ts) >= 7:
            month_counts[ts[:7]] += 1
    sorted_months = sorted(month_counts.items())[-12:]
    by_month = [{"month": m, "count": c} for m, c in sorted_months]

    # Tasa de conversión global
    total = len(businesses)
    closed = sum(1 for b in businesses if _norm(b.get("crm_status")) in ("cliente_cerrado", "finalizado"))
    conversion = round(closed / total * 100, 1) if total else 0

    return jsonify({
        "total": total,
        "closed": closed,
        "conversion": conversion,
        "funnel": funnel,
        "top_rubros": top_rubros,
        "top_cities": top_cities,
        "by_month": by_month,
    })


# ─── Attachments ─────────────────────────────────────────────────────────────

@leads_bp.route("/api/leads/<int:biz_id>/attachments")
def api_list_attachments(biz_id):
    section = request.args.get("section", "budget")
    return jsonify(get_attachments(_db(), biz_id, section))


@leads_bp.route("/api/leads/<int:biz_id>/attachments", methods=["POST"])
def api_add_attachment(biz_id):
    section = request.args.get("section", "budget")
    # Link upload (JSON)
    if request.content_type and "application/json" in request.content_type:
        data = request.get_json() or {}
        name = (data.get("name") or data.get("url") or "Link").strip()
        url = data.get("url", "").strip()
        if not url:
            return jsonify({"ok": False, "error": "url required"}), 400
        attach_id = add_attachment(_db(), biz_id, section, name, url=url)
        return jsonify({"ok": True, "id": attach_id}), 201
    # File upload (multipart)
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "file or url required"}), 400
    file_data = f.read()
    if len(file_data) > 10 * 1024 * 1024:
        return jsonify({"ok": False, "error": "Archivo demasiado grande (máx 10 MB)"}), 413
    mime_type = f.content_type or "application/octet-stream"
    name = secure_filename(f.filename) or "archivo"
    attach_id = add_attachment(_db(), biz_id, section, name, file_data=file_data, mime_type=mime_type)
    return jsonify({"ok": True, "id": attach_id}), 201


@leads_bp.route("/api/attachments/<int:attach_id>/file")
def api_attachment_file(attach_id):
    row = get_attachment_file(_db(), attach_id)
    if not row or not row["file_data"]:
        return jsonify({"error": "not found"}), 404
    mime = row["mime_type"] or "application/octet-stream"
    resp = Response(row["file_data"], mimetype=mime)
    resp.headers["Content-Disposition"] = f'inline; filename="{row["name"]}"'
    return resp


@leads_bp.route("/api/attachments/<int:attach_id>", methods=["DELETE"])
def api_delete_attachment(attach_id):
    delete_attachment(_db(), attach_id)
    return jsonify({"ok": True})
