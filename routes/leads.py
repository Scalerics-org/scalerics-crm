"""Lead / business CRUD routes."""

import os
import re
import threading

from flask import Blueprint, Response, current_app, jsonify, request, session
from werkzeug.utils import secure_filename

from database import (count_businesses, get_all_businesses, update_business, delete_business, get_business,
                      get_client_info, insert_business, merge_business,
                      add_attachment, get_attachments, get_attachment_file, delete_attachment,
                      add_lead_event, get_lead_events,
                      add_call_log, get_call_logs,
                      increment_task_progress, get_lead_contributor_ids, log_activity)
from database import get_attachment_file, update_attachment_file, get_attachments
from pitch_generator import generate_pitch
from database import connect as _db_connect
from services.budget_ai import ai_edit_html, generate_budget_html

leads_bp = Blueprint("leads", __name__)

# Tipos que se pueden renderizar inline sin riesgo. NO incluye image/svg+xml:
# los SVG admiten <script> y se ejecutarian en el origen del CRM.
_MIME_INLINE_SEGUROS = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp",
    "application/pdf", "text/plain",
})


def _normalizar_mime(raw: str) -> str:
    """Saca parametros ('; charset=utf-8') y normaliza a minusculas, para que las
    comparaciones por tipo no se puedan esquivar cambiando el Content-Type."""
    return (raw or "").split(";")[0].strip().lower() or "application/octet-stream"


def _content_disposition(modo: str, nombre: str) -> str:
    """Arma el header sin permitir inyeccion: el nombre puede venir de un adjunto
    creado por otra ruta que no paso por secure_filename()."""
    limpio = re.sub(r'[^A-Za-z0-9._ -]', "_", (nombre or "archivo"))[:120]
    return f'{modo}; filename="{limpio}"'


def _contributors(db: str, lead_id: int, current_uid: int | None) -> list[int]:
    ids = set(get_lead_contributor_ids(db, lead_id))
    if current_uid:
        ids.add(current_uid)
    return list(ids)


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
    "sin_contactar", "interesado", "contactado", "reunion_agendada",
    "reunion_hecha", "presupuesto_enviado", "negociacion",
    "cliente_cerrado", "en_desarrollo", "finalizado",
    "llamar_despues", "no_interesa",
    # legacy aliases kept for backwards compat
    "agendo", "firmo",
}

_PIPELINE_STATUSES = ["reunion_agendada", "reunion_hecha", "presupuesto_enviado", "negociacion"]
_CLIENT_STATUSES   = ["cliente_cerrado", "en_desarrollo", "finalizado"]

_PER_PAGE = 50
_VALID_OUTCOMES = {"contestó", "no_contestó", "buzón", "no_interesa", "llamar_despues", "interesado", "reunion"}


def _db() -> str:
    return current_app.config["DB_PATH"]


def _pagina(valor: str) -> int:
    try:
        return max(1, int(valor))
    except (TypeError, ValueError):
        return 1


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
    filtros = {"search": search or None}
    if crm_group == "pipeline":
        filtros["crm_statuses"] = _PIPELINE_STATUSES
    elif crm_group == "clientes":
        filtros["crm_statuses"] = _CLIENT_STATUSES
    elif crm_group == "meta":
        filtros["source"] = "meta"
    else:
        filtros["crm_status"] = crm_status

    # El filtro por categoria pasa por _normalize_category(), que son keyword maps
    # en Python y no se pueden expresar en SQL. Cuando esta activo hay que traer
    # todo y filtrar en memoria; el resto de los casos pagina en la base.
    if category:
        businesses = [b for b in get_all_businesses(_db(), **filtros)
                      if _normalize_category(b.get("category") or "") == category]
        if page_str is None:
            return jsonify(businesses)
        page = _pagina(page_str)
        total = len(businesses)
        paginas = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
        page = min(page, paginas)
        inicio = (page - 1) * _PER_PAGE
        return jsonify({"items": businesses[inicio:inicio + _PER_PAGE],
                        "total": total, "pages": paginas, "page": page})

    if page_str is None:
        return jsonify(get_all_businesses(_db(), **filtros))

    # Paginacion real: antes se traia la tabla ENTERA y se recortaba en Python, asi
    # que el costo base -> proceso no bajaba por mas que el usuario viera 50 filas.
    total = count_businesses(_db(), **filtros)
    paginas = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
    page = min(_pagina(page_str), paginas)
    items = get_all_businesses(_db(), **filtros, limit=_PER_PAGE,
                               offset=(page - 1) * _PER_PAGE)
    return jsonify({"items": items, "total": total, "pages": paginas, "page": page})


_EXPORT_COLUMNAS = [
    ("id", "ID"), ("name", "Nombre"), ("phone", "Teléfono"), ("email", "Email"),
    ("category", "Rubro"), ("city", "Ciudad"), ("crm_status", "Estado CRM"),
    ("rating", "Rating"), ("address", "Dirección"), ("scraped_at", "Fecha scrape"),
]


def _celda_csv(valor) -> str:
    """Escapa una celda y neutraliza la inyeccion de formulas.

    Excel y Sheets ejecutan lo que empieza con = + - @: un lead llamado
    '=HYPERLINK("http://evil","click")' se convierte en una formula viva en la
    maquina de quien abra el export. Los nombres vienen del scraping y de webhooks
    publicos, asi que son input no confiable.
    """
    texto = "" if valor is None else str(valor)
    if texto[:1] in ("=", "+", "-", "@", "\t", "\r"):
        texto = "'" + texto
    return '"' + texto.replace('"', '""') + '"'


@leads_bp.route("/api/leads/export.csv")
def api_export_leads_csv():
    """Export completo, del lado del servidor.

    El boton armaba el CSV en el browser desde _allLeads, que segun el panel que
    hubiera cargado ultimo contenia la pagina actual (50 filas) o la lista entera:
    el archivo salia incompleto o vacio sin ningun aviso. Con la paginacion real
    ese camino exportaria siempre 50 filas.
    """
    crm_group = request.args.get("crm_group")
    category = request.args.get("category")
    filtros = {"search": (request.args.get("search") or "").lower() or None}
    if crm_group == "pipeline":
        filtros["crm_statuses"] = _PIPELINE_STATUSES
    elif crm_group == "clientes":
        filtros["crm_statuses"] = _CLIENT_STATUSES
    elif crm_group == "meta":
        filtros["source"] = "meta"
    else:
        filtros["crm_status"] = request.args.get("crm_status")

    filas = get_all_businesses(_db(), **filtros)
    if category:
        filas = [b for b in filas if _normalize_category(b.get("category") or "") == category]

    def generar():
        # BOM para que Excel abra los acentos bien.
        yield "﻿" + ",".join(e for _, e in _EXPORT_COLUMNAS) + "\r\n"
        for b in filas:
            yield ",".join(_celda_csv(b.get(c)) for c, _ in _EXPORT_COLUMNAS) + "\r\n"

    return Response(
        generar(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": _content_disposition("attachment", "leads_scalerics.csv")},
    )


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
    _STATUS_TO_GOAL = {
        "interesado":      "leads_contactados",
        "reunion_hecha":   "reuniones_hechas",
        "cliente_cerrado": "clientes_cerrados",
    }
    if crm_status in _STATUS_TO_GOAL:
        uids = _contributors(db, biz_id, session.get("user_id"))
        increment_task_progress(db, uids, _STATUS_TO_GOAL[crm_status],
                                lead_id=biz_id, lead_name=biz.get("name", ""))
    return jsonify({"ok": True})


@leads_bp.route("/api/leads/remap-category", methods=["POST"])
def api_remap_category():
    """Bulk rename a category across all leads. Admin only."""
    data = request.get_json() or {}
    from_cat = (data.get("from") or "").strip()
    to_cat = (data.get("to") or "").strip()
    if not from_cat or not to_cat:
        return jsonify({"ok": False, "error": "from y to requeridos"}), 400
    db = _db()
    businesses = get_all_businesses(db)
    updated = 0
    for b in businesses:
        if (b.get("category") or "").strip().lower() == from_cat.lower():
            update_business(db, b["id"], category=to_cat)
            updated += 1
    return jsonify({"ok": True, "updated": updated})


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
    _STATUS_TO_GOAL_BATCH = {
        "interesado":      "leads_contactados",
        "reunion_hecha":   "reuniones_hechas",
        "cliente_cerrado": "clientes_cerrados",
    }
    if crm_status in _STATUS_TO_GOAL_BATCH:
        goal = _STATUS_TO_GOAL_BATCH[crm_status]
        for bid in ids:
            bid = int(bid)
            biz_name = (get_business(db, bid) or {}).get("name", "")
            uids = _contributors(db, bid, session.get("user_id"))
            increment_task_progress(db, uids, goal, lead_id=bid, lead_name=biz_name)
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


@leads_bp.route("/api/leads/<int:biz_id>/merge", methods=["POST"])
def api_merge_lead(biz_id):
    """Merge biz_id (duplicate) into another existing lead."""
    if not session.get("user_id"):
        token = request.headers.get("x-admin-token", "")
        expected = os.environ.get("ADMIN_TOKEN", "")
        if not expected or token != expected:
            return jsonify({"error": "unauthorized"}), 401

    data = request.get_json() or {}
    into_id = data.get("into_id")
    if not into_id:
        return jsonify({"ok": False, "error": "into_id requerido"}), 400
    try:
        into_id = int(into_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "into_id inválido"}), 400
    # Fusionar consigo mismo borraba el lead y, con las FK activas, arrastraba en
    # cascada sus reuniones, adjuntos, eventos y llamadas.
    if into_id == biz_id:
        return jsonify({"ok": False, "error": "No se puede fusionar un lead consigo mismo"}), 400

    db = _db()
    source = get_business(db, biz_id)
    target = get_business(db, int(into_id))
    if not source:
        return jsonify({"ok": False, "error": "Lead origen no encontrado"}), 404
    if not target:
        return jsonify({"ok": False, "error": "Lead destino no encontrado"}), 404

    merge_business(db, biz_id, int(into_id))
    log_activity(db, session.get("user_name", "sistema"), "lead_merged",
                 "lead", int(into_id), target.get("name", ""),
                 f"Fusionado con {source.get('name', '')} (id {biz_id})",
                 user_id=session.get("user_id"))
    return jsonify({"ok": True, "target_id": int(into_id)})


@leads_bp.route("/api/leads/<int:biz_id>/confirm-new", methods=["POST"])
def api_confirm_new_lead(biz_id):
    """Mark a calendly_unmatched lead as confirmed new client."""
    db = _db()
    biz = get_business(db, biz_id)
    if not biz:
        return jsonify({"ok": False, "error": "Lead no encontrado"}), 404
    update_business(db, biz_id, source="calendly")
    log_activity(db, session.get("user_name", "sistema"), "lead_confirmed_new",
                 "lead", biz_id, biz.get("name", ""), "Confirmado como nuevo cliente desde Calendly",
                 user_id=session.get("user_id"))
    return jsonify({"ok": True})


@leads_bp.route("/api/leads/<int:biz_id>/contact", methods=["POST"])
def api_contact(biz_id):
    data = request.get_json() or {}
    note = data.get("note", "")
    db = _db()
    biz = get_business(db, biz_id) or {}
    user_name = session.get("user_name", "sistema")
    update_business(db, biz_id, status="contacted", notes=note, crm_status="interesado")
    add_lead_event(db, biz_id, "interesado", note=note, created_by=user_name)
    log_activity(db, user_name, "status_change", "lead", biz_id, biz.get("name", ""), "interesado",
                 user_id=session.get("user_id"))
    uids = _contributors(db, biz_id, session.get("user_id"))
    increment_task_progress(db, uids, "leads_contactados",
                            lead_id=biz_id, lead_name=biz.get("name", ""))
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
        "sin_contactar", "interesado", "reunion_agendada",
        "reunion_hecha", "presupuesto_enviado", "negociacion",
        "cliente_cerrado", "en_desarrollo", "finalizado",
    ]
    _legacy = {"firmo": "cliente_cerrado", "agendo": "reunion_agendada", "contactado": "interesado"}
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
    _contacted_st = {"interesado", "llamar_despues", "no_interesa",
                     "contactado", "reunion_agendada", "reunion_hecha",
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
    conn3 = _db_connect(_db()); conn3.row_factory = _sq.Row
    try:
        rows = conn3.execute("""
            SELECT cl.outcome, COUNT(DISTINCT cl.lead_id) as cnt
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
    conn4 = _db_connect(_db()); conn4.row_factory = _sq4.Row
    try:
        u = conn4.execute("""
            SELECT u.id, u.email, r.name as role_name
            FROM users u LEFT JOIN roles r ON u.role_id = r.id
            WHERE u.id=?
        """, (uid,)).fetchone()
    finally:
        conn4.close()
    if not u:
        return jsonify({"error": "No autorizado"}), 403
    admin_email = _os4.environ.get("ADMIN_EMAIL", "")
    is_admin = bool(
        (admin_email and u["email"].lower() == admin_email.lower())
        or (not admin_email and u["id"] == 1)
        or (u["role_name"] or "").lower() == "admin"
    )
    if not is_admin:
        return jsonify({"error": "No autorizado"}), 403

    businesses = get_all_businesses(_db(), source="meta")

    _legacy = {"firmo": "cliente_cerrado", "agendo": "reunion_agendada", "contactado": "interesado"}
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
    funnel_order = ["sin_contactar", "interesado", "reunion_agendada", "reunion_hecha",
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
    # Antes no se validaba: adjuntar a un lead inexistente creaba una fila
    # huerfana en silencio (asi quedaron los 31 huerfanos de la base productiva).
    # Con las foreign keys ya activas seria un IntegrityError -> 500; un 404 dice
    # lo que realmente pasa.
    if not get_business(_db(), biz_id):
        return jsonify({"ok": False, "error": "lead inexistente"}), 404
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
    mime_type = _normalizar_mime(f.content_type)
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
    # El mime lo elegia quien subia el archivo y se comparaba con == "text/html"
    # exacto, asi que 'text/html; charset=utf-8' o 'image/svg+xml' (los SVG
    # ejecutan <script>) esquivaban el sandbox y corrian en el origen del CRM.
    mime = _normalizar_mime(row["mime_type"])
    resp = Response(row["file_data"], mimetype=mime)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if mime == "text/html":
        # Los presupuestos son HTML generado por IA y editable: si se renderizan
        # en el origen del CRM, un <script> inyectado corre con la sesion del
        # usuario. sandbox sin allow-scripts los deja verse pero no ejecutar.
        resp.headers["Content-Security-Policy"] = "sandbox"
        resp.headers["Content-Disposition"] = _content_disposition("inline", row["name"])
    elif mime in _MIME_INLINE_SEGUROS:
        resp.headers["Content-Disposition"] = _content_disposition("inline", row["name"])
    else:
        # Todo lo desconocido se descarga en vez de renderizarse. Cubre SVG,
        # XML, octet-stream y cualquier tipo nuevo que aparezca.
        resp.headers["Content-Disposition"] = _content_disposition("attachment", row["name"])
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
    outcome = data.get("outcome", "llamar_despues")
    if outcome not in ("llamar_despues", "interesado"):
        outcome = "llamar_despues"
    if not callback_date:
        return jsonify({"ok": False, "error": "callback_date requerida"}), 400
    db = _db()
    user_name = session.get("user_name", "sistema")
    biz = get_business(db, biz_id) or {}
    update_business(db, biz_id, crm_status=outcome, callback_date=callback_date)
    if notes:
        update_business(db, biz_id, notes=notes)
    add_lead_event(db, biz_id, outcome, note=f"Callback: {callback_date}", created_by=user_name)
    add_call_log(db, biz_id, outcome, notes or f"Callback: {callback_date}", user_name)
    log_activity(db, user_name, "callback_set", "lead", biz_id, biz.get("name", ""), callback_date,
                 user_id=session.get("user_id"))
    if outcome == "interesado":
        uids = _contributors(db, biz_id, session.get("user_id"))
        increment_task_progress(db, uids, "leads_contactados",
                                lead_id=biz_id, lead_name=biz.get("name", ""))
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
    uids = _contributors(db, biz_id, session.get("user_id"))
    increment_task_progress(db, uids, "llamadas_realizadas",
                            lead_id=biz_id, lead_name=biz.get("name", ""))
    if outcome == "contestó":
        increment_task_progress(db, uids, "llamadas_contestadas",
                                lead_id=biz_id, lead_name=biz.get("name", ""))
    if outcome == "reunion":
        from database import update_business, add_lead_event
        update_business(db, biz_id, crm_status="reunion_agendada")
        add_lead_event(db, biz_id, "reunion_agendada", created_by=created_by)
        log_activity(db, created_by, "status_change", "lead", biz_id, biz.get("name", ""),
                     "reunion_agendada", user_id=session.get("user_id"))
        increment_task_progress(db, uids, "reuniones_agendadas",
                                lead_id=biz_id, lead_name=biz.get("name", ""))
    return jsonify({"ok": True}), 201


@leads_bp.route("/api/leads/<int:biz_id>/calls", methods=["GET"])
def api_get_calls(biz_id):
    return jsonify(get_call_logs(_db(), biz_id))


@leads_bp.route("/api/attachments/<int:attach_id>/ai-edit", methods=["POST"])
def api_attachment_ai_edit(attach_id):
    data = request.get_json() or {}
    instructions = (data.get("instructions") or "").strip()
    if not instructions:
        return jsonify({"ok": False, "error": "instructions requeridas"}), 400
    row = get_attachment_file(_db(), attach_id)
    if not row or not row["file_data"]:
        return jsonify({"ok": False, "error": "Adjunto no encontrado"}), 404
    if (row.get("mime_type") or "") != "text/html":
        return jsonify({"ok": False, "error": "El adjunto no es HTML"}), 400
    try:
        original_html = row["file_data"].decode("utf-8")
        modified_html = ai_edit_html(original_html, instructions)
        return jsonify({"ok": True, "html": modified_html})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@leads_bp.route("/api/attachments/<int:attach_id>/ai-apply", methods=["POST"])
def api_attachment_ai_apply(attach_id):
    data = request.get_json() or {}
    html = (data.get("html") or "").strip()
    if not html:
        return jsonify({"ok": False, "error": "html requerido"}), 400
    update_attachment_file(_db(), attach_id, html.encode("utf-8"))
    return jsonify({"ok": True})


@leads_bp.route("/api/attachments/<int:attach_id>/print")
def api_attachment_print(attach_id):
    """Serve the HTML with auto-print injected so the browser opens the print dialog."""
    row = get_attachment_file(_db(), attach_id)
    if not row or not row["file_data"]:
        return jsonify({"error": "not found"}), 404
    html = row["file_data"].decode("utf-8")
    print_script = "<script>window.addEventListener('load',()=>window.print())</script>"
    if "</body>" in html:
        html = html.replace("</body>", f"{print_script}</body>", 1)
    else:
        html += print_script
    resp = Response(html, mimetype="text/html")
    # sandbox allow-scripts: print script runs, but the page gets a unique opaque
    # origin — document.cookie and localStorage are inaccessible to any injected JS.
    resp.headers["Content-Security-Policy"] = "sandbox allow-scripts"
    return resp


@leads_bp.route("/api/attachments/<int:attach_id>/pdf")
def api_attachment_pdf(attach_id):
    """Render HTML attachment as PDF using Playwright and return as download."""
    from playwright.sync_api import sync_playwright

    row = get_attachment_file(_db(), attach_id)
    if not row or not row["file_data"]:
        return jsonify({"error": "not found"}), 404

    html = row["file_data"].decode("utf-8")
    name = (row.get("name") or "presupuesto").replace(".html", "")

    try:
        with sync_playwright() as p:
            # Estos flags son los que necesita Chromium para arrancar dentro de un
            # contenedor (produccion corre en Docker sobre Fly).
            browser = p.chromium.launch(args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-setuid-sandbox",
                "--no-zygote",
            ])
            page = browser.new_page()

            # Sin esto el render se queda esperando a Google Fonts.
            def _block_fonts(route):
                if any(d in route.request.url for d in ("fonts.googleapis.com", "fonts.gstatic.com")):
                    route.abort()
                else:
                    route.continue_()
            page.route("**/*", _block_fonts)

            page.set_content(html, wait_until="load", timeout=30000)
            pdf_bytes = page.pdf(format="A4", print_background=True,
                                 margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
            browser.close()

        resp = Response(pdf_bytes, mimetype="application/pdf")
        resp.headers["Content-Disposition"] = f'attachment; filename="{name}.pdf"'
        return resp
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@leads_bp.route("/api/leads/<int:biz_id>/budget/generate", methods=["POST"])
def api_budget_generate(biz_id):
    biz = get_business(_db(), biz_id)
    if not biz:
        return jsonify({"ok": False, "error": "Lead no encontrado"}), 404
    data = request.get_json() or {}
    instructions = (data.get("instructions") or "").strip()
    try:
        html = generate_budget_html(
            business_name=biz.get("name", ""),
            category=biz.get("category", ""),
            city=biz.get("city", ""),
            instructions=instructions,
        )
        file_data = html.encode("utf-8")
        db = _db()
        existing = get_attachments(db, biz_id, "budget")
        html_attachments = [a for a in existing if (a.get("mime_type") or "") == "text/html"]
        if html_attachments:
            attach_id = html_attachments[0]["id"]
            update_attachment_file(db, attach_id, file_data)
        else:
            safe_name = re.sub(r"[^a-z0-9]", "-", (biz.get("name") or "cliente").lower()).strip("-")
            attach_id = add_attachment(db, biz_id, "budget", f"presupuesto-{safe_name}.html",
                                       file_data=file_data, mime_type="text/html")
        return jsonify({"ok": True, "attachment_id": attach_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
