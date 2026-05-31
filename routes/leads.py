"""Lead / business CRUD routes."""

import os
import threading

from flask import Blueprint, Response, current_app, jsonify, request, session
from werkzeug.utils import secure_filename

from database import (get_all_businesses, update_business, delete_business, get_business,
                      get_client_info, insert_business,
                      add_attachment, get_attachments, get_attachment_file, delete_attachment,
                      add_lead_event, get_lead_events,
                      add_call_log, get_call_logs,
                      increment_task_progress, log_activity)
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
    "sin_contactar", "contactado", "reunion_agendada",
    "reunion_hecha", "presupuesto_enviado", "negociacion",
    "cliente_cerrado", "en_desarrollo", "finalizado",
    "llamar_despues", "no_interesa",
    # legacy aliases kept for backwards compat
    "agendo", "firmo",
}

_PIPELINE_STATUSES = ["reunion_agendada", "reunion_hecha", "presupuesto_enviado", "negociacion"]
_CLIENT_STATUSES   = ["cliente_cerrado", "en_desarrollo", "finalizado"]

_PER_PAGE = 50
_VALID_OUTCOMES = {"contestó", "no_contestó", "buzón", "no_interesa", "llamar_despues"}


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
    crm_group  = request.args.get("crm_group")
    category = request.args.get("category")
    search = (request.args.get("search") or "").lower()
    page_str = request.args.get("page")
    if crm_group == "pipeline":
        businesses = get_all_businesses(_db(), crm_statuses=_PIPELINE_STATUSES)
    elif crm_group == "clientes":
        businesses = get_all_businesses(_db(), crm_statuses=_CLIENT_STATUSES)
    elif crm_group == "meta":
        businesses = get_all_businesses(_db(), source="meta")
    else:
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
    biz = get_business(db, biz_id) or {}
    user_name = session.get("user_name", "sistema")
    update_business(db, biz_id, crm_status=crm_status)
    add_lead_event(db, biz_id, crm_status, created_by=user_name)
    log_activity(db, user_name, "status_change", "lead", biz_id, biz.get("name", ""), crm_status,
                 user_id=session.get("user_id"))
    if crm_status == "contactado":
        increment_task_progress(db, session.get("user_id"), "leads_contactados")
    return jsonify({"ok": True})


@leads_bp.route("/api/leads/batch-status", methods=["POST"])
def api_batch_status():
    data = request.get_json() or {}
    ids = data.get("ids", [])
    crm_status = data.get("crm_status", "")
    if not ids or crm_status not in _VALID_CRM_STATES:
        return jsonify({"ok": False, "error": "ids o estado inválido"}), 400
    db = _db()
    user_name = session.get("user_name", "sistema")
    for biz_id in ids:
        biz_id = int(biz_id)
        update_business(db, biz_id, crm_status=crm_status)
        add_lead_event(db, biz_id, crm_status, created_by=user_name)
    log_activity(db, user_name, "batch_status", "", None, "",
                 f"{len(ids)} leads → {crm_status}", user_id=session.get("user_id"))
    return jsonify({"ok": True, "updated": len(ids)})


@leads_bp.route("/api/leads/<int:biz_id>/events")
def api_lead_events(biz_id):
    return jsonify(get_lead_events(_db(), biz_id))


@leads_bp.route("/api/leads/<int:biz_id>", methods=["DELETE"])
def api_delete_lead(biz_id):
    db = _db()
    biz = get_business(db, biz_id) or {}
    user_name = session.get("user_name", "sistema")
    log_activity(db, user_name, "lead_deleted", "lead", biz_id, biz.get("name", ""), "",
                 user_id=session.get("user_id"))
    delete_business(db, biz_id)
    return jsonify({"ok": True})


@leads_bp.route("/api/leads/<int:biz_id>/contact", methods=["POST"])
def api_contact(biz_id):
    data = request.get_json() or {}
    note = data.get("note", "")
    db = _db()
    biz = get_business(db, biz_id) or {}
    user_name = session.get("user_name", "sistema")
    update_business(db, biz_id, status="contacted", notes=note, crm_status="contactado")
    add_lead_event(db, biz_id, "contactado", note=note, created_by=user_name)
    log_activity(db, user_name, "status_change", "lead", biz_id, biz.get("name", ""), "contactado",
                 user_id=session.get("user_id"))
    increment_task_progress(db, session.get("user_id"), "leads_contactados")
    return jsonify({"ok": True})


@leads_bp.route("/api/leads/<int:biz_id>/notes", methods=["PUT"])
def api_update_notes(biz_id):
    data = request.get_json() or {}
    notes = data.get("notes", "")
    db = _db()
    biz = get_business(db, biz_id) or {}
    user_name = session.get("user_name", "sistema")
    update_business(db, biz_id, notes=notes)
    add_lead_event(db, biz_id, "nota_actualizada", created_by=user_name)
    log_activity(db, user_name, "note_updated", "lead", biz_id, biz.get("name", ""), "",
                 user_id=session.get("user_id"))
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
    import sqlite3 as _sq

    # Solo leads SDR (excluye Meta)
    all_biz = get_all_businesses(_db())
    businesses = [b for b in all_biz if (b.get("source") or "") != "meta"]

    funnel_order = [
        "sin_contactar", "contactado", "reunion_agendada",
        "reunion_hecha", "presupuesto_enviado", "negociacion",
        "cliente_cerrado", "en_desarrollo", "finalizado",
    ]
    _legacy = {"firmo": "cliente_cerrado", "agendo": "reunion_agendada"}
    def _norm(s): return _legacy.get(s or "sin_contactar", s or "sin_contactar")

    crm_counts = Counter(_norm(b.get("crm_status")) for b in businesses)
    funnel = [{"status": s, "count": crm_counts.get(s, 0)} for s in funnel_order]

    rubro_counts = Counter(
        _normalize_category(b.get("category") or "") for b in businesses
        if _normalize_category(b.get("category") or "")
    )
    top_rubros = [{"name": k, "count": v} for k, v in rubro_counts.most_common(10)]

    city_counts = Counter(
        (b.get("city") or "").strip() for b in businesses if (b.get("city") or "").strip()
    )
    top_cities = [{"name": k, "count": v} for k, v in city_counts.most_common(10)]

    month_counts: dict = defaultdict(int)
    for b in businesses:
        ts = b.get("scraped_at") or ""
        if ts and len(ts) >= 7:
            month_counts[ts[:7]] += 1
    by_month = [{"month": m, "count": c} for m, c in sorted(month_counts.items())[-12:]]

    total = len(businesses)
    _contacted_st = {"contactado", "reunion_agendada", "reunion_hecha",
                     "presupuesto_enviado", "negociacion",
                     "cliente_cerrado", "en_desarrollo", "finalizado"}
    _meeting_st   = {"reunion_agendada", "reunion_hecha", "presupuesto_enviado",
                     "negociacion", "cliente_cerrado", "en_desarrollo", "finalizado"}
    _closed_st    = {"cliente_cerrado", "en_desarrollo", "finalizado"}

    contacted = sum(1 for b in businesses if _norm(b.get("crm_status")) in _contacted_st)
    meetings  = sum(1 for b in businesses if _norm(b.get("crm_status")) in _meeting_st)
    closed    = sum(1 for b in businesses if _norm(b.get("crm_status")) in _closed_st)

    contact_rate = round(contacted / total * 100, 1) if total else 0
    meeting_rate = round(meetings / contacted * 100, 1) if contacted else 0
    conversion   = round(closed / total * 100, 1) if total else 0

    # Stats de llamadas para leads SDR desde call_logs
    conn3 = _sq.connect(_db()); conn3.row_factory = _sq.Row
    try:
        rows = conn3.execute("""
            SELECT cl.outcome, COUNT(*) as cnt
            FROM call_logs cl
            JOIN businesses b ON cl.lead_id = b.id
            WHERE (b.source IS NULL OR b.source != 'meta')
            GROUP BY cl.outcome
        """).fetchall()
    finally:
        conn3.close()
    call_stats = {r["outcome"]: r["cnt"] for r in rows}

    return jsonify({
        "total": total,
        "contacted": contacted,
        "meetings": meetings,
        "closed": closed,
        "contact_rate": contact_rate,
        "meeting_rate": meeting_rate,
        "conversion": conversion,
        "funnel": funnel,
        "top_rubros": top_rubros,
        "top_cities": top_cities,
        "by_month": by_month,
        "call_stats": call_stats,
    })



@leads_bp.route("/api/metrics/meta")
def api_metrics_meta():
    from collections import Counter, defaultdict
    from datetime import datetime, timezone, timedelta
    import sqlite3 as _sq4
    import json as _j4
    import os as _os4

    # Admin check
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "No autorizado"}), 403
    conn4 = _sq4.connect(_db()); conn4.row_factory = _sq4.Row
    try:
        u = conn4.execute("SELECT id, email FROM users WHERE id=?", (uid,)).fetchone()
    finally:
        conn4.close()
    if not u:
        return jsonify({"error": "No autorizado"}), 403
    admin_email = _os4.environ.get("ADMIN_EMAIL", "")
    is_admin = bool(
        (admin_email and u["email"].lower() == admin_email.lower())
        or (not admin_email and u["id"] == 1)
    )
    if not is_admin:
        return jsonify({"error": "No autorizado"}), 403

    businesses = get_all_businesses(_db(), source="meta")

    _legacy = {"firmo": "cliente_cerrado", "agendo": "reunion_agendada"}
    def _norm(s): return _legacy.get(s or "sin_contactar", s or "sin_contactar")
    _closed_st = {"cliente_cerrado", "en_desarrollo", "finalizado"}

    total  = len(businesses)
    now    = datetime.now(timezone.utc)
    month_prefix = now.strftime("%Y-%m")
    week_start   = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")

    this_month = sum(1 for b in businesses
                     if (b.get("scraped_at") or "").startswith(month_prefix))
    this_week  = sum(1 for b in businesses
                     if (b.get("scraped_at") or "")[:10] >= week_start)
    closed     = sum(1 for b in businesses if _norm(b.get("crm_status")) in _closed_st)
    conversion = round(closed / total * 100, 1) if total else 0

    # Leads por campaña (desde notes: "Meta Lead Ad · {campaign}")
    campaign_counts: Counter = Counter()
    PREFIX = "Meta Lead Ad · "
    for b in businesses:
        notes = b.get("notes") or ""
        campaign = notes[len(PREFIX):].strip() if notes.startswith(PREFIX) else "Sin campaña"
        if not campaign:
            campaign = "Sin campaña"
        campaign_counts[campaign] += 1
    by_campaign = [{"name": k, "count": v} for k, v in campaign_counts.most_common(10)]

    # Leads por mes
    month_counts: defaultdict = defaultdict(int)
    for b in businesses:
        ts = b.get("scraped_at") or ""
        if ts and len(ts) >= 7:
            month_counts[ts[:7]] += 1
    by_month = [{"month": m, "count": c} for m, c in sorted(month_counts.items())[-12:]]

    # Funnel CRM
    funnel_order = ["sin_contactar", "contactado", "reunion_agendada", "reunion_hecha",
                    "presupuesto_enviado", "negociacion", "cliente_cerrado",
                    "en_desarrollo", "finalizado"]
    crm_counts = Counter(_norm(b.get("crm_status")) for b in businesses)
    funnel = [{"status": s, "count": crm_counts.get(s, 0)} for s in funnel_order]

    # Qué buscan / presupuesto desde form_data JSON
    que_busca_counts: Counter = Counter()
    presupuesto_counts: Counter = Counter()
    for b in businesses:
        try:
            fd = _j4.loads(b.get("form_data") or "{}")
            qb = (fd.get("que_busca") or fd.get("que_buscas") or fd.get("servicio") or "").strip()
            if qb:
                que_busca_counts[qb] += 1
            pr = (fd.get("presupuesto") or fd.get("budget_range") or fd.get("budget") or "").strip()
            if pr:
                presupuesto_counts[pr] += 1
        except Exception:
            pass

    city_counts = Counter(
        (b.get("city") or "").strip() for b in businesses if (b.get("city") or "").strip()
    )

    return jsonify({
        "total":       total,
        "this_month":  this_month,
        "this_week":   this_week,
        "conversion":  conversion,
        "by_campaign": by_campaign,
        "by_month":    by_month,
        "funnel":      funnel,
        "que_busca":   [{"name": k, "count": v} for k, v in que_busca_counts.most_common(10)],
        "presupuesto": [{"name": k, "count": v} for k, v in presupuesto_counts.most_common(10)],
        "top_cities":  [{"name": k, "count": v} for k, v in city_counts.most_common(10)],
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
        log_activity(_db(), session.get("user_name", "sistema"), "attachment_added", "lead", biz_id,
                     (get_business(_db(), biz_id) or {}).get("name", ""), name,
                     user_id=session.get("user_id"))
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
    db = _db()
    attach_id = add_attachment(db, biz_id, section, name, file_data=file_data, mime_type=mime_type)
    log_activity(db, session.get("user_name", "sistema"), "attachment_added", "lead", biz_id,
                 (get_business(db, biz_id) or {}).get("name", ""), name,
                 user_id=session.get("user_id"))
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


@leads_bp.route("/api/leads/<int:biz_id>/callback", methods=["POST"])
def api_set_callback(biz_id):
    data = request.get_json() or {}
    callback_date = (data.get("callback_date") or "").strip()
    notes = (data.get("notes") or "").strip()
    if not callback_date:
        return jsonify({"ok": False, "error": "callback_date requerida"}), 400
    db = _db()
    user_name = session.get("user_name", "sistema")
    biz = get_business(db, biz_id) or {}
    update_business(db, biz_id, crm_status="llamar_despues", callback_date=callback_date)
    if notes:
        update_business(db, biz_id, notes=notes)
    add_lead_event(db, biz_id, "llamar_despues", note=f"Callback: {callback_date}", created_by=user_name)
    add_call_log(db, biz_id, "llamar_despues", notes or f"Callback: {callback_date}", user_name)
    log_activity(db, user_name, "callback_set", "lead", biz_id, biz.get("name", ""), callback_date,
                 user_id=session.get("user_id"))
    return jsonify({"ok": True})


@leads_bp.route("/api/leads/<int:biz_id>/calls", methods=["POST"])
def api_add_call(biz_id):
    data = request.get_json() or {}
    outcome = (data.get("outcome") or "").strip()
    notes = (data.get("notes") or "").strip()
    if outcome not in _VALID_OUTCOMES:
        return jsonify({"ok": False, "error": f"Outcome inválido: {outcome}"}), 400
    db = _db()
    created_by = session.get("user_name", "sistema")
    biz = get_business(db, biz_id) or {}
    add_call_log(db, biz_id, outcome, notes, created_by)
    log_activity(db, created_by, "call_logged", "lead", biz_id, biz.get("name", ""), outcome,
                 user_id=session.get("user_id"))
    return jsonify({"ok": True}), 201


@leads_bp.route("/api/leads/<int:biz_id>/calls", methods=["GET"])
def api_get_calls(biz_id):
    return jsonify(get_call_logs(_db(), biz_id))
