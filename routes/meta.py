"""Meta Lead Ads webhook — recibe leads de formularios de Facebook/Instagram."""

import hashlib
import hmac
import json
import logging
import os
import re
import threading

import requests
from flask import Blueprint, current_app, jsonify, request

import sqlite3 as _sq_meta

import time

from database import (
    insert_business,
    update_business,
    get_business_by_phone,
    log_activity,
)
from services.email_service import send_new_meta_lead_notification, send_meta_token_alert
from meta_config import GRAPH_VERSION, GRAPH  # re-exportados: mismo nombre para no tocar los usos internos

logger = logging.getLogger(__name__)
meta_bp = Blueprint("meta", __name__)

VERIFY_TOKEN   = os.environ.get("META_VERIFY_TOKEN", "scalerics_meta_webhook_2026")
APP_SECRET     = os.environ.get("META_APP_SECRET", "")
PAGE_TOKEN     = os.environ.get("META_PAGE_TOKEN", "")
ALLOW_UNSIGNED = os.environ.get("META_ALLOW_UNSIGNED", "").lower() == "true"


def _db() -> str:
    return current_app.config["DB_PATH"]


def _get_admin_emails(db: str) -> list[str]:
    # Valvula para probar la integracion sin escribirle a todo el equipo:
    # si META_NOTIFY_OVERRIDE tiene una direccion, los avisos de Meta van solo
    # ahi. REEMPLAZA la lista, no la suma — ADMIN_EMAIL sumaba, y para esto
    # hace falta lo contrario. En produccion tiene que quedar vacia.
    override = os.environ.get("META_NOTIFY_OVERRIDE", "").strip().lower()
    if override:
        logger.warning(f"META_NOTIFY_OVERRIDE activo: los avisos de Meta van solo a {override}")
        return [override]

    emails = set()
    admin_env = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    try:
        conn = _sq_meta.connect(db); conn.row_factory = _sq_meta.Row
        rows = conn.execute("""
            SELECT u.email FROM users u
            LEFT JOIN roles r ON u.role_id = r.id
            WHERE LOWER(r.name) = 'admin'
        """).fetchall()
        for r in rows:
            if r["email"]:
                emails.add(r["email"].strip().lower())
        conn.close()
    except Exception as e:
        logger.warning(f"Could not query admin emails: {e}")
    if admin_env:
        emails.add(admin_env)
    return list(emails)


def _notify_new_meta_lead(db: str, lead_name: str, phone: str, campaign: str, city: str, lead_id: int):
    for email in _get_admin_emails(db):
        try:
            send_new_meta_lead_notification(email, lead_name, phone, campaign, city, lead_id)
        except Exception as e:
            logger.error(f"Failed to notify {email} of new Meta lead: {e}")
    # WhatsApp — configurar ADMIN_WA_PHONE en .env cuando esté listo
    # wa_phone = os.environ.get("ADMIN_WA_PHONE", "")
    # if wa_phone:
    #     _send_wa_notification(wa_phone, lead_name, phone, campaign)


def _page_token() -> str:
    """El token vigente.

    En Fly el token se renueva con `flyctl secrets set META_PAGE_TOKEN=...`,
    que reinicia el proceso con el entorno nuevo. Leer `os.environ` en cada
    llamada — y no la constante de módulo, que quedó congelada en el import —
    es lo que hace que el valor nuevo se use sin depender de un redeploy.
    """
    return os.environ.get("META_PAGE_TOKEN") or PAGE_TOKEN


def _redact_secrets(text: str) -> str:
    """Saca el access_token de un mensaje de error antes de que viaje por mail.

    requests.raise_for_status() incluye la URL completa en el mensaje de
    excepción, y esa URL lleva el PAGE_TOKEN en el query string. El log
    puede quedarse con el error crudo; el mail no.
    """
    return re.sub(r"access_token=[^&\s]+", "access_token=***", text)


def _verify_signature(payload: bytes, sig_header: str) -> bool:
    if not APP_SECRET:
        if ALLOW_UNSIGNED:
            logger.warning("META_APP_SECRET sin configurar y META_ALLOW_UNSIGNED=true — firma no verificada")
            return True
        logger.error("META_APP_SECRET sin configurar — se rechaza el webhook")
        return False
    if not sig_header:
        return False
    try:
        expected = "sha256=" + hmac.new(APP_SECRET.encode(), payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig_header)
    except Exception:
        return False


# ── Webhook verification (GET) ────────────────────────────────────────────────

@meta_bp.route("/api/meta/webhook", methods=["GET"])
def meta_webhook_verify():
    mode      = request.args.get("hub.mode")
    token     = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("Meta webhook verified OK")
        return challenge, 200
    return "Forbidden", 403


# ── Webhook events (POST) ─────────────────────────────────────────────────────

@meta_bp.route("/api/meta/webhook", methods=["POST"])
def meta_webhook_receive():
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(request.data, sig):
        logger.warning(f"Meta webhook: invalid signature (sig={sig[:20] if sig else 'empty'})")
        return "Invalid signature", 403

    data = request.get_json(silent=True) or {}
    if data.get("object") != "page":
        return "ok", 200

    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "leadgen":
                continue
            lead_id = change.get("value", {}).get("leadgen_id")
            form_id = change.get("value", {}).get("form_id")
            if lead_id:
                logger.info(f"Meta webhook received: leadgen_id={lead_id} form_id={form_id}")
                threading.Thread(
                    target=_fetch_and_store_lead,
                    args=(current_app._get_current_object(), lead_id, form_id),
                    daemon=True,
                ).start()

    return "ok", 200


def _merge_lead_into_existing(db: str, phone: str, fields: dict) -> int | None:
    """El INSERT no creó fila: ya hay un negocio con ese teléfono.

    `insert_business` usa `INSERT OR IGNORE` sobre `businesses.phone UNIQUE`,
    así que un negocio ya scrapeado de Google que después llena el formulario
    de Meta se descartaba entero. El lead es real y llegó: al negocio que ya
    estaba se le devuelve `source='meta'` y se le guarda el `form_data`.

    Devuelve el id del negocio existente, o None si no se pudo ubicar
    (sin teléfono no hay por dónde buscarlo).
    """
    if not phone:
        return None
    try:
        existente = get_business_by_phone(db, phone)
    except Exception as e:
        logger.error(f"No se pudo buscar el negocio existente por teléfono: {_redact_secrets(str(e))}")
        return None
    if not existente:
        return None
    biz_id = existente["id"]
    update_business(db, biz_id,
                    source="meta",
                    form_data=json.dumps(fields, ensure_ascii=False))
    return biz_id


def _fetch_and_store_lead(app, lead_id: str, form_id: str):
    with app.app_context():
        db = app.config["DB_PATH"]
        try:
            page_token = _page_token()
            if not page_token:
                logger.warning("META_PAGE_TOKEN not set — cannot fetch lead")
                return

            r = requests.get(
                f"{GRAPH}/{lead_id}",
                params={"access_token": page_token, "fields": "field_data,created_time,ad_name,campaign_name,form_id"},
                timeout=10,
            )
            r.raise_for_status()
            lead_data = r.json()

            fields = {f["name"].lower(): f["values"][0] if f.get("values") else ""
                      for f in lead_data.get("field_data", [])}

            name  = fields.get("full_name") or fields.get("nombre") or fields.get("name") or "Lead Meta"
            phone = (fields.get("phone_number") or fields.get("telefono") or
                     fields.get("phone") or fields.get("celular") or "")
            email = fields.get("email") or fields.get("correo") or ""
            city  = fields.get("city") or fields.get("ciudad") or ""

            ad_name       = lead_data.get("ad_name", "")
            campaign_name = lead_data.get("campaign_name", "")
            notes = f"Meta Lead Ad · {campaign_name or ad_name or form_id or ''}".strip(" ·")

            created_at = lead_data.get("created_time", "")
            if created_at:
                try:
                    from datetime import datetime, timezone
                    created_at = datetime.fromisoformat(created_at.replace("+0000","")).replace(tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    created_at = ""
            biz_id = insert_business(db, {
                "name":       name,
                "phone":      phone or None,
                # El email se extraia del formulario y despues se descartaba: no
                # se pasaba a insert_business, que ademas tampoco lo guardaba. Sin
                # el no se le puede escribir al lead, ni invitarlo a una reunion,
                # ni mandarle un recordatorio.
                "email":      email or None,
                "city":       city or None,
                "category":   "Meta Lead Ad",
                "status":     "scraped",
                "notes":      notes,
                "score":      70,
                "source":     "meta",
                "form_data":  json.dumps(fields, ensure_ascii=False),
                "scraped_at": created_at or None,
            })

            if biz_id:
                log_activity(db, "meta_webhook", "lead_created", "lead", biz_id, name,
                             f"Fuente: Meta Lead Ad · {campaign_name or ad_name}", user_id=None)
                # Sin PII: el nombre y el telefono en claro terminaban en los logs de
                # Fly. El id alcanza para rastrear el lead en la base.
                logger.info(f"Meta lead stored: id={biz_id} campaign={campaign_name or ad_name!r}")
                threading.Thread(
                    target=_notify_new_meta_lead,
                    args=(db, name, phone, campaign_name or ad_name or "", city, biz_id),
                    daemon=True,
                ).start()
            else:
                existente_id = _merge_lead_into_existing(db, phone, fields)
                if existente_id:
                    logger.warning(
                        f"Meta lead sobre un negocio que ya existia: {name} ({phone}) "
                        f"→ id {existente_id}; se le devolvio source='meta' y se guardo el form_data"
                    )
                    log_activity(db, "meta_webhook", "lead_updated", "lead", existente_id, name,
                                 f"Lead de Meta sobre un negocio ya existente · {campaign_name or ad_name}",
                                 user_id=None)
                    threading.Thread(
                        target=_notify_new_meta_lead,
                        args=(db, name, phone, campaign_name or ad_name or "", city, existente_id),
                        daemon=True,
                    ).start()
                else:
                    logger.warning(
                        f"Meta lead descartado por el INSERT y sin negocio existente que lo reciba: "
                        f"{name} ({phone}) — recuperar a mano desde el panel de formularios de Meta"
                    )

        except Exception as e:
            logger.error(f"Error processing Meta lead {lead_id}: {_redact_secrets(str(e))}")
            from services.email_service import send_meta_lead_failure_alert
            admins = _get_admin_emails(db)
            if not admins:
                logger.error(
                    f"Meta lead {lead_id} failed with no admin email configured — "
                    f"nobody was alerted, recover manually from the Meta forms panel"
                )
            for admin in admins:
                try:
                    send_meta_lead_failure_alert(admin, lead_id, _redact_secrets(str(e)))
                except Exception as mail_err:
                    logger.error(f"Tampoco se pudo avisar del fallo: {mail_err}")


# ── Trigger historical import from production server ─────────────────────────
#
# Acá vivía `POST /api/meta/reset-import`, que hacía
# `DELETE FROM businesses WHERE category='Meta Lead Ad'` y reimportaba desde
# Graph. Graph solo retiene ~90 días: una llamada borraba para siempre los
# leads viejos que esta rama restaura. Se eliminó a propósito — no volver a
# agregarlo.


@meta_bp.route("/api/meta/import-leads", methods=["POST"])
def meta_import_leads():
    from flask import session
    token = request.headers.get("x-admin-token", "")
    expected = os.environ.get("ADMIN_TOKEN", "")
    if not (session.get("user_id") or (expected and token == expected)):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    db = _db()
    pt = _page_token()
    page_id = os.environ.get("META_PAGE_ID", "")
    if not pt or not page_id:
        return jsonify({"ok": False, "error": "META_PAGE_TOKEN o META_PAGE_ID no configurado"}), 400

    def _run():
        new, dup = 0, 0
        try:
            def get_all(url, params):
                results = []
                while url:
                    r = requests.get(url, params=params, timeout=15)
                    if not r.ok:
                        break
                    d = r.json()
                    results.extend(d.get("data", []))
                    url = d.get("paging", {}).get("next")
                    params = {}
                return results

            forms = get_all(
                f"{GRAPH}/{page_id}/leadgen_forms",
                {"access_token": pt, "fields": "id,name,status"}
            )
            for form in forms:
                leads = get_all(
                    f"{GRAPH}/{form['id']}/leads",
                    {"access_token": pt, "fields": "id,created_time,field_data,ad_name,campaign_name"}
                )
                for lead in leads:
                    fields = {f["name"].lower(): f["values"][0] if f.get("values") else ""
                              for f in lead.get("field_data", [])}
                    name  = (fields.get("full_name") or fields.get("nombre") or
                             fields.get("name") or "Lead Meta")
                    phone = (fields.get("phone_number") or fields.get("telefono") or
                             fields.get("phone") or fields.get("celular") or "")
                    city  = fields.get("city") or fields.get("ciudad") or ""
                    campaign = lead.get("campaign_name") or lead.get("ad_name") or form.get("name", "")
                    notes = f"Meta Lead Ad · {campaign}".strip(" ·")
                    ct = lead.get("created_time","")
                    if ct:
                        try:
                            from datetime import datetime, timezone as tz
                            ct = datetime.fromisoformat(ct.replace("+0000","")).replace(tzinfo=tz.utc).strftime("%Y-%m-%d %H:%M:%S")
                        except Exception:
                            ct = ""
                    biz_id = insert_business(db, {
                        "name":       name,
                        "phone":      phone or None,
                        "city":       city or None,
                        "category":   "Meta Lead Ad",
                        "status":     "scraped",
                        "notes":      notes,
                        "score":      70,
                        "source":     "meta",
                        "form_data":  json.dumps(fields, ensure_ascii=False),
                        "scraped_at": ct or None,
                    })
                    if biz_id:
                        new += 1
                    else:
                        # Import masivo: acá el `else` se cuenta y nada más.
                        # A diferencia del webhook no se pisa el negocio que ya
                        # existe ni se notifica: esta importación repasa todos
                        # los formularios de la página, así que casi todos los
                        # leads ya están y notificarlos sería spam.
                        dup += 1
        except Exception as e:
            logger.error(f"Import error: {_redact_secrets(str(e))}")
        logger.info(f"Meta import done: {new} new, {dup} dup")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "Importación iniciada en background"})


# ── One-time setup: exchange user token → long-lived page token ───────────────

@meta_bp.route("/api/meta/setup-token", methods=["POST"])
def meta_setup_token():
    """Exchange a short-lived user token for a long-lived page token and save to env."""
    from flask import session
    if not session.get("user_id"):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    data        = request.get_json() or {}
    user_token  = data.get("user_token", "").strip()
    app_id      = os.environ.get("META_APP_ID", "")
    app_secret  = os.environ.get("META_APP_SECRET", "")

    if not user_token or not app_id or not app_secret:
        return jsonify({"ok": False, "error": "Faltan credenciales"}), 400

    try:
        # 1. Long-lived user token
        r = requests.get(f"{GRAPH}/oauth/access_token", params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id, "client_secret": app_secret,
            "fb_exchange_token": user_token,
        }, timeout=10)
        r.raise_for_status()
        ll_user_token = r.json()["access_token"]

        # 2. Page token for the configured page
        page_id = os.environ.get("META_PAGE_ID", "")
        r2 = requests.get(f"{GRAPH}/{page_id}", params={
            "fields": "access_token,name",
            "access_token": ll_user_token,
        }, timeout=10)
        r2.raise_for_status()
        page_data  = r2.json()
        page_token = page_data.get("access_token", "")
        page_name  = page_data.get("name", "")

        # 3. Save to .env
        _update_env("META_PAGE_TOKEN", page_token)

        return jsonify({"ok": True, "page": page_name, "token_preview": page_token[:20] + "..."})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@meta_bp.route("/api/meta/import-sync", methods=["POST"])
def meta_import_sync():
    token = request.headers.get("x-admin-token", "")
    expected = os.environ.get("ADMIN_TOKEN", "")
    if not (expected and token == expected):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    db = _db()
    pt = _page_token()
    page_id = os.environ.get("META_PAGE_ID", "")
    if not pt or not page_id:
        return jsonify({"ok": False, "error": f"Missing: page_token={bool(pt)} page_id={bool(page_id)}"}), 400
    new_c, dup, errors = 0, 0, []
    try:
        def _ga(url, params):
            results = []
            while url:
                r = requests.get(url, params=params, timeout=15)
                if not r.ok:
                    errors.append(f"HTTP {r.status_code}: {r.text[:100]}")
                    break
                d = r.json()
                if "error" in d:
                    errors.append(str(d["error"])[:200])
                    break
                results.extend(d.get("data", []))
                url = d.get("paging", {}).get("next")
                params = {}
            return results
        forms = _ga(f"{GRAPH}/{page_id}/leadgen_forms",
                    {"access_token": pt, "fields": "id,name,leads_count"})
        for form in forms:
            leads = _ga(f"{GRAPH}/{form['id']}/leads",
                        {"access_token": pt, "fields": "id,created_time,field_data,ad_name,campaign_name"})
            for lead in leads:
                fields = {f["name"].lower(): (f.get("values") or [""])[0] for f in lead.get("field_data", [])}
                name  = fields.get("full_name") or fields.get("nombre") or fields.get("name") or "Lead Meta"
                phone = fields.get("phone_number") or fields.get("telefono") or fields.get("phone") or ""
                city  = fields.get("city") or fields.get("ciudad") or ""
                ct = lead.get("created_time", "")
                if ct:
                    try:
                        from datetime import datetime, timezone as tz
                        ct = datetime.fromisoformat(ct.replace("+0000","")).replace(tzinfo=tz.utc).strftime("%Y-%m-%d %H:%M:%S")
                    except Exception as e2:
                        errors.append(f"date: {e2}"); ct = ""
                biz_id = insert_business(db, {
                    "name": name, "phone": phone or None, "city": city or None,
                    "category": "Meta Lead Ad", "status": "scraped",
                    "notes": f"Meta Lead Ad · {lead.get('campaign_name') or form.get('name','')}".strip(" ·"),
                    "score": 70, "source": "meta",
                    "form_data": json.dumps(fields, ensure_ascii=False),
                    "scraped_at": ct or None,
                })
                if biz_id: new_c += 1
                else: dup += 1  # import masivo: solo se cuenta (ver meta_import_leads)
    except Exception as e:
        errors.append(_redact_secrets(str(e)))
    return jsonify({"ok": True, "new": new_c, "dup": dup, "forms": len(forms) if "forms" in dir() else 0, "errors": errors})


def _update_env(key: str, value: str):
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    try:
        lines = open(env_path).readlines()
        found = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}\n"
                found = True
                break
        if not found:
            lines.append(f"{key}={value}\n")
        with open(env_path, "w") as f:
            f.writelines(lines)
        os.environ[key] = value
    except Exception as e:
        logger.warning(f"Could not update .env: {e}")


# ── Background token health monitor ──────────────────────────────────────────

# Cada cuánto se revisa que el token de página siga vivo.
_CHECK_INTERVAL = 10 * 60  # 10 minutos

# Cada cuánto corre el import de respaldo contra Graph. Es el colchón por si
# el webhook se cae; no hace falta más seguido, y compartía constante con el
# monitor de token, así que el "import diario" corría cada 10 minutos.
_IMPORT_INTERVAL = 24 * 60 * 60  # 24 horas

# Cada cuánto se repite el mail de "token vencido" mientras el mismo
# incidente sigue vivo. Ni cada _CHECK_INTERVAL (spam: ~288 mails/día por
# admin, y de paso revientan la cuota de Resend que usa todo el CRM) ni una
# sola vez (un token vencido un fin de semana largo queda sin nadie avisado).
_TOKEN_ALERT_RESEND_INTERVAL = 6 * 60 * 60  # 6 horas → 4 mails/día por admin mientras dure


def _token_alert_key(err: dict) -> str:
    """Identifica el *tipo* de falla, no el mensaje textual completo.

    Dos chequeos seguidos del mismo token vencido traen el mismo
    `code`/`error_subcode` de Meta: eso es "lo mismo" y no debe repetir el
    mail. Un `code`/`error_subcode` distinto (por ejemplo revocado en vez de
    vencido) es un incidente nuevo y tiene que avisar aunque el anterior
    siga silenciado.
    """
    return f"{err.get('code', '')}:{err.get('error_subcode', '')}"


def _should_send_token_alert(db: str, alert_key: str, detail: str) -> bool:
    """True si hay que mandar el mail para `alert_key`: primera vez que se ve
    ese tipo de falla, o ya pasó `_TOKEN_ALERT_RESEND_INTERVAL` desde el
    último envío. Registra el intento en `meta_token_alerts` (misma base que
    `leads.db`, montada en /data en Fly), así el silencio sobrevive a un
    restart o redeploy en vez de vivir en memoria del proceso.

    Si la tabla no está disponible por lo que sea, se manda igual: ante la
    duda, un mail de más es preferible a un incidente real que quede mudo.
    """
    now = time.time()
    try:
        conn = _sq_meta.connect(db)
        row = conn.execute(
            "SELECT last_sent_at FROM meta_token_alerts WHERE alert_key = ?",
            (alert_key,),
        ).fetchone()
        if row and (now - row[0]) < _TOKEN_ALERT_RESEND_INTERVAL:
            conn.close()
            return False
        conn.execute(
            """
            INSERT INTO meta_token_alerts (alert_key, detail, last_sent_at)
            VALUES (?, ?, ?)
            ON CONFLICT(alert_key) DO UPDATE SET detail = excluded.detail, last_sent_at = excluded.last_sent_at
            """,
            (alert_key, detail, now),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.warning(f"No se pudo evaluar el estado de la alerta de token, se avisa igual: {e}")
        return True


def _check_token_once(db: str) -> None:
    token = _page_token()  # el mismo que usa el webhook: vigilar otro no sirve
    if not token:
        return
    try:
        r = requests.get(
            f"{GRAPH}/me",
            params={"fields": "name", "access_token": token},
            timeout=10,
        )
        data = r.json()
        if "error" in data:
            err = data["error"]
            detail = f"[{err.get('code')}] {err.get('message', '')}"
            logger.error(f"Meta token invalid: {detail}")
            alert_key = _token_alert_key(err)
            if _should_send_token_alert(db, alert_key, detail):
                for email in _get_admin_emails(db):
                    send_meta_token_alert(email, detail)
            else:
                logger.info(f"Meta token alert silenciada (mismo incidente hace menos de {_TOKEN_ALERT_RESEND_INTERVAL}s): {alert_key}")
        else:
            logger.debug(f"Meta token OK — page: {data.get('name')}")
    except Exception as e:
        logger.warning(f"Meta token check failed (network?): {_redact_secrets(str(e))}")


def start_meta_token_monitor(app) -> None:
    def _loop():
        time.sleep(60)  # esperar a que la app levante
        while True:
            try:
                with app.app_context():
                    _check_token_once(app.config["DB_PATH"])
            except Exception as e:
                logger.warning(f"Token monitor error: {e}")
            time.sleep(_CHECK_INTERVAL)

    t = threading.Thread(target=_loop, daemon=True, name="meta-token-monitor")
    t.start()
    logger.info("Meta token monitor started (checks every 10 min)")


# ── Daily import cron ─────────────────────────────────────────────────────────

def _run_import_sync(db: str) -> tuple[int, int]:
    pt = _page_token()
    page_id = os.environ.get("META_PAGE_ID", "")
    if not pt or not page_id:
        logger.warning("Meta daily import: PAGE_TOKEN or PAGE_ID not set")
        return 0, 0

    new_c, dup = 0, 0

    def _ga(url, params):
        results = []
        while url:
            r = requests.get(url, params=params, timeout=15)
            if not r.ok:
                break
            d = r.json()
            if "error" in d:
                logger.error(f"Meta API error during import: {d['error']}")
                break
            results.extend(d.get("data", []))
            url = d.get("paging", {}).get("next")
            params = {}
        return results

    forms = _ga(
        f"{GRAPH}/{page_id}/leadgen_forms",
        {"access_token": pt, "fields": "id,name,leads_count"},
    )
    for form in forms:
        leads = _ga(
            f"{GRAPH}/{form['id']}/leads",
            {"access_token": pt, "fields": "id,created_time,field_data,ad_name,campaign_name"},
        )
        for lead in leads:
            fields = {f["name"].lower(): (f.get("values") or [""])[0] for f in lead.get("field_data", [])}
            name  = fields.get("full_name") or fields.get("nombre") or fields.get("name") or "Lead Meta"
            phone = fields.get("phone_number") or fields.get("telefono") or fields.get("phone") or fields.get("celular") or ""
            city  = fields.get("city") or fields.get("ciudad") or ""
            ct = lead.get("created_time", "")
            if ct:
                try:
                    from datetime import datetime, timezone as tz
                    ct = datetime.fromisoformat(ct.replace("+0000", "")).replace(tzinfo=tz.utc).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    ct = ""
            campaign = lead.get("campaign_name") or form.get("name", "")
            biz_id = insert_business(db, {
                "name": name, "phone": phone or None, "city": city or None,
                "category": "Meta Lead Ad", "status": "scraped",
                "notes": f"Meta Lead Ad · {campaign}".strip(" ·"),
                "score": 70, "source": "meta",
                "form_data": json.dumps(fields, ensure_ascii=False),
                "scraped_at": ct or None,
            })
            if biz_id:
                new_c += 1
                log_activity(db, "meta_daily_import", "lead_created", "lead", biz_id, name,
                             f"Fuente: Meta Lead Ad · {campaign}", user_id=None)
                threading.Thread(
                    target=_notify_new_meta_lead,
                    args=(db, name, phone, campaign, city, biz_id),
                    daemon=True,
                ).start()
            else:
                # Repaso diario de todos los formularios: la enorme mayoría de
                # estas filas ya está en la base. Se cuentan y nada más — pisar
                # el negocio existente o notificar acá repetiría el aviso una
                # vez por día, todos los días. El camino que sí lo hace, una
                # sola vez y cuando el lead llega, es el webhook.
                dup += 1

    logger.info(f"Meta daily import done: {new_c} new, {dup} dup")
    return new_c, dup


def start_meta_daily_import(app) -> None:
    def _loop():
        time.sleep(120)  # esperar a que la app levante
        while True:
            try:
                with app.app_context():
                    _run_import_sync(app.config["DB_PATH"])
            except Exception as e:
                logger.warning(f"Meta daily import error: {_redact_secrets(str(e))}")
            time.sleep(_IMPORT_INTERVAL)

    t = threading.Thread(target=_loop, daemon=True, name="meta-daily-import")
    t.start()
    logger.info("Meta daily import started (runs every 24h)")
