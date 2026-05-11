"""Lead / business CRUD routes."""

import base64
import datetime
import html as html_lib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import Blueprint, current_app, jsonify, request

from database import get_all_businesses, update_business, delete_business, get_business

leads_bp = Blueprint("leads", __name__)

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


@leads_bp.route("/api/leads")
def api_leads():
    businesses = get_all_businesses(_db())
    crm_status = request.args.get("crm_status")
    category = request.args.get("category")
    search = (request.args.get("search") or "").lower()
    if crm_status:
        businesses = [b for b in businesses if (b.get("crm_status") or "sin_contactar") == crm_status]
    if category:
        businesses = [b for b in businesses if (b.get("category") or "").lower() == category.lower()]
    if search:
        businesses = [b for b in businesses if search in (b.get("name") or "").lower()]
    return jsonify(businesses)


@leads_bp.route("/api/leads/<int:biz_id>", methods=["GET"])
def api_get_lead(biz_id):
    biz = get_business(_db(), biz_id)
    if not biz:
        return jsonify({"error": "Lead no encontrado"}), 404
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


@leads_bp.route("/api/stats")
def api_stats():
    businesses = get_all_businesses(_db())
    categories = sorted({b.get("category") or "" for b in businesses if b.get("category")})
    return jsonify({
        "total": len(businesses),
        "with_pitch": sum(1 for b in businesses if b.get("pitch_text")),
        "with_demo": sum(1 for b in businesses if b.get("demo_url")),
        "contacted": sum(1 for b in businesses if (b.get("crm_status") or "") not in ("sin_contactar", "")),
        "categories": categories,
    })


@leads_bp.route("/api/leads/<int:biz_id>/send-email", methods=["POST"])
def api_send_email(biz_id):
    """Legacy email sending — kept for backwards compat."""
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        return jsonify({"ok": False, "error": "google-api-python-client no instalado"})

    client_id = os.environ.get("GMAIL_CLIENT_ID")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET")
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN")
    sender_email = os.environ.get("FACTORY_EMAIL", "")

    if not all([client_id, client_secret, refresh_token, sender_email]):
        return jsonify({"ok": False, "error": "Faltan variables Gmail en .env"})

    biz = get_business(_db(), biz_id)
    if not biz:
        return jsonify({"ok": False, "error": "Lead no encontrado"}), 404
    if not biz.get("email"):
        return jsonify({"ok": False, "error": "Este negocio no tiene email"})

    try:
        creds = Credentials(
            token=None, refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id, client_secret=client_secret,
            scopes=["https://www.googleapis.com/auth/gmail.send"],
        )
        service = build("gmail", "v1", credentials=creds)
        factory_name = os.environ.get("FACTORY_NAME", "Scalerics")
        factory_phone = os.environ.get("FACTORY_PHONE", "")
        name_esc = html_lib.escape(biz.get("name", "") or "")
        phone_line = f"📞 {factory_phone}" if factory_phone else ""
        email_html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333;padding:20px">
  <div style="border-top:4px solid #7c3aed;padding-top:24px">
    <h2 style="color:#7c3aed;margin-bottom:4px">{html_lib.escape(factory_name)}</h2>
    <p style="color:#666;margin-top:0;font-size:0.9em">Software factory · www.scalerics.com</p>
  </div>
  <p style="margin-top:24px">Hola equipo de <strong>{name_esc}</strong>,</p>
  <p>Notamos que todavía no tienen página web propia. Hoy la mayoría de los clientes busca en Google antes de visitar un negocio — y sin web, no aparecen.</p>
  <p>En <strong>{html_lib.escape(factory_name)}</strong> desarrollamos sitios web para negocios locales uruguayos, rápido y a precios accesibles.</p>
  <p>¿Charlamos 15 minutos esta semana?</p>
  <div style="margin-top:32px;padding:16px;background:#f5f3ff;border-radius:8px;font-size:0.9em">
    <strong>{html_lib.escape(factory_name)}</strong><br>
    📧 <a href="mailto:{html_lib.escape(sender_email)}" style="color:#7c3aed">{html_lib.escape(sender_email)}</a><br>
    {phone_line}
  </div>
  <p style="font-size:0.75em;color:#999;margin-top:24px">Si no querés recibir más mensajes, respondé con "no gracias".</p>
</body></html>"""
        message = MIMEMultipart("alternative")
        message["From"] = sender_email
        message["To"] = biz["email"]
        message["Subject"] = f"¿Le puedo mostrar algo a {biz['name']}?"
        message.attach(MIMEText(email_html, "html", "utf-8"))
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        update_business(_db(), biz_id, status="email_sent",
                        email_sent_at=datetime.datetime.now().isoformat())
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
