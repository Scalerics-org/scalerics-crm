"""WhatsApp panel routes — proxy to bot admin API."""

import logging
import os

import requests as http_requests
from flask import Blueprint, current_app, jsonify, request

logger = logging.getLogger(__name__)
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
    # Token propio para hablar con el bot. Antes se reusaba ADMIN_TOKEN, que es
    # el que protege los endpoints de ENTRADA del CRM —el formulario de la web,
    # los leads de Meta, las demos—. Compartirlos obliga a que el bot conozca
    # ese valor, y cambiarlo por cualquier motivo romperia esas integraciones.
    # Son dos cosas distintas y ahora tienen dos variables.
    token = os.environ.get("BOT_ADMIN_TOKEN") or os.environ.get("ADMIN_TOKEN", "")
    if not base:
        return None, "BOT_API_URL no configurada en .env"
    if not token:
        return None, "Falta BOT_ADMIN_TOKEN: es la clave con la que el CRM se autentica ante el bot"
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

    # Los errores de red se traducen a algo que se pueda leer. El mensaje crudo
    # de requests es un parrafo de internals de urllib3 —"HTTPSConnectionPool(...)
    # Max retries exceeded ... NameResolutionError"— que termina en pantalla y no
    # le dice a nadie que el bot no esta levantado.
    except http_requests.exceptions.ConnectionError:
        host = base.split("//")[-1].split("/")[0]
        return None, (
            f"No hay conexion con el bot de WhatsApp ({host}). "
            "Puede estar apagado, o BOT_API_URL apuntando a una direccion que ya no existe."
        )
    except http_requests.exceptions.Timeout:
        return None, "El bot de WhatsApp no respondio a tiempo. Puede estar sobrecargado o reiniciandose."
    except ValueError:
        # r.json() sobre algo que no es JSON: casi siempre un proxy o un login
        # devolviendo HTML donde deberia estar el bot.
        return None, "El bot de WhatsApp devolvio una respuesta que no se entiende. Revisar BOT_API_URL."
    except Exception as e:
        logger.warning(f"Error hablando con el bot: {e}")
        return None, f"No se pudo consultar el bot de WhatsApp: {type(e).__name__}"


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


@wa_bp.route("/api/bot/lead-qualified", methods=["POST"])
def api_bot_lead_qualified():
    """Receives a push from the bot when a lead qualifies (MEETING_SENT / HUMAN_QUEUED)."""
    from database import (
        get_business_by_phone, insert_business, update_business, upsert_client_info,
    )

    token = request.headers.get("x-admin-token", "")
    expected = os.environ.get("ADMIN_TOKEN", "")
    if token != expected:
        logger.warning(f"[bot-sync] 401 — token mismatch (received={token!r}, expected len={len(expected)})")
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json() or {}
    phone = (data.get("phone") or "").strip()
    logger.info(f"[bot-sync] lead-qualified received: phone={phone!r}, state={data.get('state')!r}")
    if not phone:
        return jsonify({"ok": False, "error": "phone requerido"}), 400

    BTYPE = {1: "Página web", 2: "E-commerce", 3: "Automatización", 4: "App a medida"}
    BUDGET = {1: "Menos de $500 USD", 2: "$500–$3.000 USD", 3: "Más de $3.000 USD", 4: "Sin definir"}
    TEAM = {1: "Solo yo", 2: "2–5 personas", 3: "6–20 personas", 4: "Más de 20"}
    STATE_TO_CRM = {
        "MEETING_SENT": "reunion_agendada",
        "SCHEDULED": "reunion_agendada",
        "HUMAN_QUEUED": "contactado",
    }

    category = BTYPE.get(data.get("business_type"), "desarrollo web")
    budget_range = BUDGET.get(data.get("budget"), "no especificado")
    team_label = TEAM.get(data.get("team_size"), "no especificado")
    crm_status = STATE_TO_CRM.get(data.get("state"), "contactado")

    db = current_app.config["DB_PATH"]
    biz = get_business_by_phone(db, phone)
    if biz:
        biz_id = biz["id"]
        update_business(
            db, biz_id,
            crm_status=crm_status,
            name=data.get("business_name") or biz.get("name") or "Sin nombre",
        )
    else:
        biz_id = insert_business(db, {
            "phone": phone,
            "name": data.get("business_name") or data.get("name") or "Sin nombre",
            "category": category,
            "city": "Montevideo",
            "status": "bot_qualified",
        })
        if biz_id:
            update_business(db, biz_id, crm_status=crm_status)

    if biz_id:
        upsert_client_info(
            db, biz_id,
            lead_name=data.get("name"),
            business_name=data.get("business_name"),
            rubro=category,
            budget_range=budget_range,
            colors=data.get("colors"),
            instagram=data.get("instagram_web"),
            needs=data.get("needs") or team_label,
            meeting_time=data.get("meeting_time"),
            meeting_url=data.get("meeting_url"),
        )

    logger.info(f"[bot-sync] lead-qualified done: business_id={biz_id}, crm_status={crm_status!r}")
    return jsonify({"ok": True, "business_id": biz_id})


@wa_bp.route("/api/wa/templates", methods=["GET"])
def api_wa_templates_list():
    from database import get_wa_templates
    return jsonify(get_wa_templates(current_app.config["DB_PATH"]))


@wa_bp.route("/api/wa/templates", methods=["POST"])
def api_wa_templates_create():
    from database import create_wa_template
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    body = (data.get("body") or "").strip()
    if not name or not body:
        return jsonify({"ok": False, "error": "name y body requeridos"}), 400
    tid = create_wa_template(current_app.config["DB_PATH"], name, body)
    return jsonify({"ok": True, "id": tid}), 201


@wa_bp.route("/api/wa/templates/<int:template_id>", methods=["DELETE"])
def api_wa_templates_delete(template_id):
    from database import delete_wa_template
    delete_wa_template(current_app.config["DB_PATH"], template_id)
    return jsonify({"ok": True})
