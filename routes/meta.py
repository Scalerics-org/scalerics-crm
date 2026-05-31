"""Meta Lead Ads webhook — recibe leads de formularios de Facebook/Instagram."""

import hashlib
import hmac
import json
import logging
import os
import threading

import requests
from flask import Blueprint, current_app, jsonify, request

from database import insert_business, update_business, get_business, log_activity

logger = logging.getLogger(__name__)
meta_bp = Blueprint("meta", __name__)

VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN", "scalerics_meta_webhook_2026")
APP_SECRET   = os.environ.get("META_APP_SECRET", "")
PAGE_TOKEN   = os.environ.get("META_PAGE_TOKEN", "")


def _db() -> str:
    return current_app.config["DB_PATH"]


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
            else:
                logger.info(f"Meta lead duplicate skipped: {name} ({phone})")

        except Exception as e:
            logger.error(f"Error processing Meta lead {lead_id}: {e}")


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
