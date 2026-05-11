"""WhatsApp panel routes — proxy to bot admin API."""

import os

import requests as http_requests
from flask import Blueprint, jsonify, request

wa_bp = Blueprint("wa", __name__)

_BTYPE = {
    1: "Agencia o consultora",
    2: "E-commerce / tienda online",
    3: "Servicios profesionales",
    4: "SaaS o software",
    5: "Otro",
}


def _bot_req(method: str, path: str, **kwargs):
    """Call the bot's admin API. Returns (response_dict, error_string)."""
    base = os.environ.get("BOT_API_URL", "").rstrip("/")
    token = os.environ.get("ADMIN_TOKEN", "")
    if not base:
        return None, "BOT_API_URL no configurada en .env"
    if not token:
        return None, "ADMIN_TOKEN no configurado en .env"
    headers = {"x-admin-token": token, "Content-Type": "application/json"}
    try:
        r = http_requests.request(
            method,
            f"{base}/api/{path.lstrip('/')}",
            headers=headers,
            timeout=12,
            **kwargs,
        )
        data = r.json()
        if r.status_code >= 400:
            return None, data.get("error", r.text)
        return data, None
    except Exception as e:
        return None, str(e)


def _phone_variants(phone: str) -> list:
    """Return all plausible formats for a Uruguayan phone number."""
    import re
    digits = re.sub(r"[^\d]", "", phone)
    variants = set()
    variants.add(digits)
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 9 and digits.startswith("0"):
        intl = "598" + digits[1:]
        variants.update([digits, intl, "+" + intl])
    elif len(digits) == 8:
        intl = "598" + digits
        variants.update([digits, intl, "+" + intl])
    elif len(digits) == 11 and digits.startswith("598"):
        variants.update([digits, "+" + digits, "0" + digits[3:]])
    for v in list(variants):
        if not v.startswith("+"):
            variants.add("+" + v)
    return list(variants)


@wa_bp.route("/api/wa/leads")
def api_wa_leads():
    data, err = _bot_req("GET", "leads?limit=200")
    if err:
        return jsonify({"error": err})
    return jsonify(data.get("leads", []))


@wa_bp.route("/api/wa/leads/<path:phone>/messages")
def api_wa_messages(phone):
    data, err = _bot_req("GET", f"leads/phone/{phone}")
    if err:
        return jsonify({"error": err})
    return jsonify(data.get("messages", []))


@wa_bp.route("/api/wa/leads/<path:phone>/release", methods=["POST"])
def api_wa_release(phone):
    data, err = _bot_req("POST", f"leads/phone/{phone}/release")
    if err:
        return jsonify({"ok": False, "error": err})
    return jsonify({"ok": True})


@wa_bp.route("/api/wa/send", methods=["POST"])
def api_wa_send():
    body = request.get_json() or {}
    phone = (body.get("phone") or "").strip()
    text = (body.get("text") or "").strip()
    if not phone or not text:
        return jsonify({"ok": False, "error": "phone y text requeridos"})
    data, err = _bot_req("POST", "send", json={"phone": phone, "text": text})
    if err:
        return jsonify({"ok": False, "error": err})
    return jsonify({"ok": True})


@wa_bp.route("/api/wa/lead-by-name/<path:name>")
def api_lead_by_name(name):
    data, err = _bot_req("GET", f"leads/search?name={name}")
    if err:
        return jsonify({"error": err}), 404
    lead = data.get("lead", {})
    bt = lead.get("business_type")
    lead["rubro_hint"] = _BTYPE.get(bt, "") if bt else ""
    return jsonify({"lead": lead, "messages": data.get("messages", [])})


@wa_bp.route("/api/wa/lead-by-phone/<path:phone>")
def api_lead_by_phone(phone):
    data, err = _bot_req("GET", f"leads/phone/{phone}")
    if err:
        return jsonify({"error": err}), 404
    lead = data.get("lead", {})
    bt = lead.get("business_type")
    lead["rubro_hint"] = _BTYPE.get(bt, "") if bt else ""
    return jsonify({"lead": lead, "messages": data.get("messages", [])})
