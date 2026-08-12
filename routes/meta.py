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

from database import insert_business, update_business, get_business, log_activity
from services.email_service import send_new_meta_lead_notification, send_meta_token_alert

logger = logging.getLogger(__name__)
meta_bp = Blueprint("meta", __name__)

VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN", "scalerics_meta_webhook_2026")
APP_SECRET   = os.environ.get("META_APP_SECRET", "")
PAGE_TOKEN   = os.environ.get("META_PAGE_TOKEN", "")


def _db() -> str:
    return current_app.config["DB_PATH"]


def _get_admin_emails(db: str) -> list[str]:
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


def _redact_secrets(text: str) -> str:
    """Saca el access_token de un mensaje de error antes de que viaje por mail.

    requests.raise_for_status() incluye la URL completa en el mensaje de
    excepción, y esa URL lleva el PAGE_TOKEN en el query string. El log
    puede quedarse con el error crudo; el mail no.
    """
    return re.sub(r"access_token=[^&\s]+", "access_token=***", text)


def _verify_signature(payload: bytes, sig_header: str) -> bool:
    if not APP_SECRET or not sig_header:
        return True  # skip in dev if not configured
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


def _fetch_and_store_lead(app, lead_id: str, form_id: str):
    with app.app_context():
        db = app.config["DB_PATH"]
        try:
            if not PAGE_TOKEN:
                logger.warning("META_PAGE_TOKEN not set — cannot fetch lead")
                return

            r = requests.get(
                f"https://graph.facebook.com/v20.0/{lead_id}",
                params={"access_token": PAGE_TOKEN, "fields": "field_data,created_time,ad_name,campaign_name,form_id"},
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
                logger.info(f"Meta lead stored: {name} ({phone}) → id {biz_id}")
                threading.Thread(
                    target=_notify_new_meta_lead,
                    args=(db, name, phone, campaign_name or ad_name or "", city, biz_id),
                    daemon=True,
                ).start()
            else:
                logger.info(f"Meta lead duplicate skipped: {name} ({phone})")

        except Exception as e:
            logger.error(f"Error processing Meta lead {lead_id}: {e}")
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

@meta_bp.route("/api/meta/reset-import", methods=["POST"])
def meta_reset_import():
    """Delete all Meta leads and reimport fresh."""
    import sqlite3
    token = request.headers.get("x-admin-token", "")
    expected = os.environ.get("ADMIN_TOKEN", "")
    if not (expected and token == expected):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    db = _db()
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute("DELETE FROM businesses WHERE category='Meta Lead Ad'")
        deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    # Trigger reimport in background
    requests.post(
        request.url_root + "api/meta/import-leads",
        headers={"x-admin-token": expected, "Content-Type": "application/json"},
        timeout=5
    )
    return jsonify({"ok": True, "deleted": deleted, "message": "Reimport iniciado"})


@meta_bp.route("/api/meta/import-leads", methods=["POST"])
def meta_import_leads():
    from flask import session
    token = request.headers.get("x-admin-token", "")
    expected = os.environ.get("ADMIN_TOKEN", "")
    if not (session.get("user_id") or (expected and token == expected)):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    db = _db()
    pt = PAGE_TOKEN
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
                f"https://graph.facebook.com/v20.0/{page_id}/leadgen_forms",
                {"access_token": pt, "fields": "id,name,status"}
            )
            for form in forms:
                leads = get_all(
                    f"https://graph.facebook.com/v20.0/{form['id']}/leads",
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
                        dup += 1
        except Exception as e:
            logger.error(f"Import error: {e}")
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
        r = requests.get("https://graph.facebook.com/v20.0/oauth/access_token", params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id, "client_secret": app_secret,
            "fb_exchange_token": user_token,
        }, timeout=10)
        r.raise_for_status()
        ll_user_token = r.json()["access_token"]

        # 2. Page token for the configured page
        page_id = os.environ.get("META_PAGE_ID", "")
        r2 = requests.get(f"https://graph.facebook.com/v20.0/{page_id}", params={
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
    pt = os.environ.get("META_PAGE_TOKEN", "") or PAGE_TOKEN
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
        forms = _ga(f"https://graph.facebook.com/v20.0/{page_id}/leadgen_forms",
                    {"access_token": pt, "fields": "id,name,leads_count"})
        for form in forms:
            leads = _ga(f"https://graph.facebook.com/v20.0/{form['id']}/leads",
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
                else: dup += 1
    except Exception as e:
        errors.append(str(e))
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

_CHECK_INTERVAL = 10 * 60  # 10 minutos


def _check_token_once(db: str) -> None:
    token = os.environ.get("META_PAGE_TOKEN", "")
    if not token:
        return
    try:
        r = requests.get(
            "https://graph.facebook.com/v20.0/me",
            params={"fields": "name", "access_token": token},
            timeout=10,
        )
        data = r.json()
        if "error" in data:
            err = data["error"]
            detail = f"[{err.get('code')}] {err.get('message', '')}"
            logger.error(f"Meta token invalid: {detail}")
            for email in _get_admin_emails(db):
                send_meta_token_alert(email, detail)
        else:
            logger.debug(f"Meta token OK — page: {data.get('name')}")
    except Exception as e:
        logger.warning(f"Meta token check failed (network?): {e}")


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
    logger.info("Meta token monitor started (checks every 24h)")


# ── Daily import cron ─────────────────────────────────────────────────────────

def _run_import_sync(db: str) -> tuple[int, int]:
    pt = os.environ.get("META_PAGE_TOKEN", "") or PAGE_TOKEN
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
        f"https://graph.facebook.com/v20.0/{page_id}/leadgen_forms",
        {"access_token": pt, "fields": "id,name,leads_count"},
    )
    for form in forms:
        leads = _ga(
            f"https://graph.facebook.com/v20.0/{form['id']}/leads",
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
                logger.warning(f"Meta daily import error: {e}")
            time.sleep(_CHECK_INTERVAL)

    t = threading.Thread(target=_loop, daemon=True, name="meta-daily-import")
    t.start()
    logger.info("Meta daily import started (runs every 24h)")
