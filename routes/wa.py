"""WhatsApp panel routes — proxy to bot admin API."""

import hmac
import logging
import os
import threading
from datetime import datetime, timezone

import requests as http_requests
from flask import Blueprint, current_app, jsonify, make_response, request

from services import wa_aviso_mail

logger = logging.getLogger(__name__)
wa_bp = Blueprint("wa", __name__)

_BTYPE = {
    1: "Agencia o consultora",
    2: "E-commerce / tienda online",
    3: "Servicios profesionales",
    4: "SaaS o software",
    5: "Otro",
}


def _token_del_bot() -> str:
    """El token con el que el CRM se autentica CONTRA el bot.

    Son dos flujos independientes y cada uno tiene su credencial:

        bot -> CRM   el bot manda su CRM_ADMIN_TOKEN, que el CRM valida contra
                     su propio ADMIN_TOKEN
        CRM -> bot   el CRM manda BOT_ADMIN_TOKEN, que el bot valida contra su
                     propio ADMIN_TOKEN

    Antes esta funcion usaba el ADMIN_TOKEN del CRM, o sea la credencial de
    ENTRADA, para salir. Eso obligaba a que el ADMIN_TOKEN del bot y el del CRM
    fueran el mismo valor — un acoplamiento que no esta escrito en ningun lado y
    que se rompe apenas alguien rota uno de los dos. Es exactamente lo que pasaba:
    el panel de WhatsApp devolvia "unauthorized" siempre, porque el bot esperaba
    un token distinto del que el CRM le mandaba.

    Se mantiene el fallback a ADMIN_TOKEN para no romper instalaciones donde hoy
    coinciden, pero se avisa por log: es una configuracion a corregir, no el
    camino esperado.
    """
    token = os.environ.get("BOT_ADMIN_TOKEN", "").strip()
    if token:
        return token
    heredado = os.environ.get("ADMIN_TOKEN", "").strip()
    if heredado:
        logger.warning(
            "BOT_ADMIN_TOKEN no esta configurado: se usa ADMIN_TOKEN para hablarle "
            "al bot. Funciona solo si el ADMIN_TOKEN del bot tiene ese mismo valor. "
            "Configura BOT_ADMIN_TOKEN con el ADMIN_TOKEN del bot."
        )
    return heredado


def _bot_req(method: str, path: str, **kwargs):
    """Call the bot's admin API. Returns (response_dict, error_string)."""
    base = os.environ.get("BOT_API_URL", "").rstrip("/")
    token = _token_del_bot()
    if not base:
        return None, "BOT_API_URL no configurada en .env"
    if not token:
        return None, "BOT_ADMIN_TOKEN no configurado en .env"
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
            # El 401 del bot es casi siempre el mismo problema, y el mensaje
            # generico no dice como salir de el.
            if r.status_code == 401:
                logger.warning("El bot rechazo el token del CRM (401)")
                return None, ("El bot rechazó la credencial. El BOT_ADMIN_TOKEN del CRM "
                              "tiene que valer lo mismo que el ADMIN_TOKEN del bot.")
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


@wa_bp.route("/api/wa/media/<int:msg_id>/<int:idx>")
def api_wa_media(msg_id, idx):
    """El archivo que vino con un mensaje: la nota de voz, la foto, el PDF.

    El bot no tiene IP publica —vive solo en la red privada de Fly, a
    proposito— asi que el navegador no puede pedirle el archivo. El camino es
    navegador -> CRM -> bot -> archivo, y esto es el del medio.

    No usa _bot_req porque eso devuelve JSON parseado y aca lo que viaja son
    bytes: pasarlos por json() los rompe.
    """
    base = os.environ.get("BOT_API_URL", "").rstrip("/")
    token = _token_del_bot()
    if not base or not token:
        return jsonify({"error": "el bot no esta configurado"}), 502

    try:
        r = http_requests.request(
            "GET",
            f"{base}/api/messages/{msg_id}/media/{idx}",
            headers={"x-admin-token": token},
            timeout=20,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    if r.status_code >= 400:
        # 404 y no 200 vacio: si no, el panel dibuja un reproductor que no suena
        # —o una imagen rota— y no hay forma de saber que el archivo ya se borro.
        return jsonify({"error": "archivo no disponible"}), r.status_code

    resp = make_response(r.content)
    resp.headers["Content-Type"] = r.headers.get("Content-Type", "application/octet-stream")
    # Los archivos no cambian nunca: el mismo id siempre es el mismo archivo.
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@wa_bp.route("/api/wa/leads/<path:phone>/release", methods=["POST"])
def api_wa_release(phone):
    data, err = _bot_req("POST", f"leads/phone/{phone}/release")
    if err:
        return jsonify({"ok": False, "error": err})
    return jsonify({"ok": True})


@wa_bp.route("/api/wa/leads/<path:phone>/bot", methods=["POST"])
def api_wa_bot(phone):
    """Prender o apagar el bot para un lead.

    Es el respaldo, no el mecanismo principal: el bot ya se pausa solo por 12
    horas cuando escribis vos desde el telefono, y esa pausa vence sola. Este
    interruptor es para apagarlo a proposito y por tiempo indefinido.

    Prenderlo tambien levanta la pausa automatica, del lado del bot.
    """
    body = request.get_json() or {}
    activo = bool(body.get("activo", True))
    data, err = _bot_req("POST", f"leads/phone/{phone}/bot", json={"activo": activo})
    if err:
        return jsonify({"ok": False, "error": err})
    return jsonify({"ok": True, "activo": activo})


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
        ETAPA_DEMO_AGENDADA,
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
        "MEETING_SENT": ETAPA_DEMO_AGENDADA,
        "SCHEDULED": ETAPA_DEMO_AGENDADA,
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


def _en_segundo_plano(funcion, *args) -> None:
    """Corre `funcion` en un hilo aparte. Separado para que los tests lo puedan
    correr en el mismo hilo y mirar lo que pasa."""
    threading.Thread(target=funcion, args=args, daemon=True).start()


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


@wa_bp.route("/api/bot/mensaje-entrante", methods=["POST"])
def api_bot_mensaje_entrante():
    """El bot avisa que un contacto escribio al WhatsApp.

    Lo llama el bot (otro repo) con la misma credencial que usa para
    /api/bot/lead-qualified: su CRM_ADMIN_TOKEN contra el ADMIN_TOKEN del CRM.
    Cuerpo: {"phone": "...", "name": "...", "text": "...", "direction": "in"}.

    Dispara el aviso por mail a contacto@ (ver services/wa_aviso_mail.py), que
    decide si corresponde: el primer mensaje de la conversacion, o el primero
    despues de 30 minutos sin mensajes de ese numero.

    Nada de lo que pase con el mail le llega al bot: la decision se guarda en
    la base y el envio corre en un hilo aparte. Si la base o Resend fallan, se
    loguea y el bot igual recibe su 200.
    """
    token = request.headers.get("x-admin-token", "")
    esperado = os.environ.get("ADMIN_TOKEN", "")
    if not esperado or not hmac.compare_digest(token.encode(), esperado.encode()):
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    phone = str(data.get("phone") or "").strip()
    if not phone:
        return jsonify({"ok": False, "error": "phone requerido"}), 400

    # Solo avisan los mensajes que ENTRAN. Si el bot un dia manda tambien los
    # suyos por aca, no pueden disparar mails ni correr la ventana.
    direccion = str(data.get("direction") or "in").strip().lower()
    if direccion not in ("in", "inbound"):
        return jsonify({"ok": True, "aviso": False})

    nombre = str(data.get("name") or "").strip()
    texto = str(data.get("text") or data.get("content") or "")
    ahora = _ahora()

    try:
        avisar = wa_aviso_mail.registrar_mensaje(current_app.config["DB_PATH"], phone, ahora)
    except Exception as e:  # noqa: BLE001 - el aviso nunca puede tirar el webhook
        logger.error(f"[wa-aviso] no se pudo registrar el mensaje de {phone}: {e}")
        avisar = False

    if avisar:
        try:
            _en_segundo_plano(wa_aviso_mail.mandar_aviso, nombre, phone, texto, ahora)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[wa-aviso] no se pudo lanzar el aviso de {phone}: {e}")

    return jsonify({"ok": True, "aviso": avisar})


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
