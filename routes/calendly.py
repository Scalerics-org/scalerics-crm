import hashlib
import hmac
import json
import logging
import os
import re

from flask import Blueprint, request, jsonify

from database import create_meeting, get_business_by_phone, get_all_businesses, log_activity, update_business

logger = logging.getLogger(__name__)

calendly_bp = Blueprint("calendly", __name__)


def _verify_signature(payload: bytes, signature_header: str) -> bool:
    secret = os.environ.get("CALENDLY_WEBHOOK_SECRET", "")
    if not secret:
        return True  # skip verification if secret not configured
    # Calendly v2 format: "t=<unix_timestamp>,v1=<hmac_hex>"
    # Signed content: "<timestamp>.<body>"
    try:
        parts = dict(p.split("=", 1) for p in (signature_header or "").split(","))
        timestamp = parts.get("t", "")
        v1 = parts.get("v1", "")
        if not timestamp or not v1:
            return False
    except Exception:
        return False
    signed = (timestamp + ".").encode() + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)


def _normalize_phone(phone: str) -> str:
    return "".join(c for c in (phone or "") if c.isdigit() or c == "+")


def _find_client(db_path: str, email: str, phone: str, name: str):
    # 1. Try phone match
    if phone:
        normalized = _normalize_phone(phone)
        biz = get_business_by_phone(db_path, normalized)
        if biz:
            return biz
        # Try without country code
        if normalized.startswith("+598"):
            biz = get_business_by_phone(db_path, normalized[4:])
            if biz:
                return biz

    # 2. Try email match
    if email:
        all_biz = get_all_businesses(db_path)
        for b in all_biz:
            if (b.get("email") or "").lower() == email.lower():
                return b

    return None


@calendly_bp.route("/api/calendly/webhook", methods=["POST"])
def calendly_webhook():
    db_path = os.environ.get("DB_PATH", "leads.db")

    raw = request.get_data()
    sig = request.headers.get("Calendly-Webhook-Signature", "")
    if not _verify_signature(raw, sig):
        return jsonify({"ok": False, "error": "invalid signature"}), 401

    try:
        payload = json.loads(raw)
    except Exception:
        return jsonify({"ok": False, "error": "invalid json"}), 400

    event_type = payload.get("event", "")

    if event_type == "invitee.created":
        invitee   = payload.get("payload", {}).get("invitee", {})
        event     = payload.get("payload", {}).get("event", {})

        name      = invitee.get("name", "")
        email     = invitee.get("email", "")

        # Reuniones internas (el equipo agendandose entre si, pruebas) no son leads.
        blocked = {e.strip().lower() for e in os.environ.get("CALENDLY_BLOCKED_EMAILS", "").split(",") if e.strip()}
        if email and email.lower() in blocked:
            return jsonify({"ok": True, "skipped": "blocked_email"})

        # Extract phone using keyword matching, then fallback to regex
        PHONE_KEYWORDS = ["whatsapp", "teléfono", "telefono", "celular", "phone",
                          "número", "numero", "mobile", "cel"]
        phone_raw = ""
        qas = invitee.get("questions_and_answers", [])
        # First pass: match by keyword
        for q in qas:
            if any(kw in q.get("question", "").lower() for kw in PHONE_KEYWORDS):
                phone_raw = q.get("answer", "")
                break
        # Fallback: first answer that looks like a phone number
        if not phone_raw:
            for q in qas:
                ans = (q.get("answer") or "").strip()
                if re.match(r"^\+?[\d\s\-\(\)]{7,20}$", ans):
                    phone_raw = ans
                    break

        # Convert UTC to Uruguay local time (UTC-3) and strip microseconds
        def _norm_time(t):
            if not t:
                return ""
            import datetime as _dt
            try:
                d = _dt.datetime.fromisoformat(t.replace("Z", "+00:00").split(".")[0] + ("+00:00" if t.endswith("Z") else ""))
                d = d.astimezone(_dt.timezone(- _dt.timedelta(hours=3))).replace(tzinfo=None)
                return d.strftime("%Y-%m-%dT%H:%M:%S")
            except Exception:
                return t.replace("Z", "").split(".")[0]
        start_at  = _norm_time(event.get("start_time", ""))
        end_at    = _norm_time(event.get("end_time", ""))
        meet_link = event.get("location", {}).get("join_url", "") or event.get("location", {}).get("location", "")
        event_uri = event.get("uri", "")
        title     = f"Reunión con {name}" if name else "Reunión Calendly"

        client = _find_client(db_path, email, phone_raw, name)
        if not client:
            # Create a placeholder business so the meeting isn't lost
            import sqlite3, datetime
            conn = sqlite3.connect(db_path)
            try:
                cur = conn.execute(
                    "INSERT INTO businesses (name, email, phone, crm_status, source) VALUES (?,?,?,?,?)",
                    (name or email, email or None, _normalize_phone(phone_raw) or None, "reunion_agendada", "calendly_unmatched")
                )
                conn.commit()
                client_id = cur.lastrowid
            finally:
                conn.close()
        else:
            client_id = client["id"]
            update_business(db_path, client_id, crm_status="reunion_agendada")

        # Calendly manda su propio link de redireccion, no el de Meet. Recall
        # necesita la URL real, asi que se resuelve el redirect antes de llamarlo.
        actual_meet_url = meet_link
        if meet_link and "meet.google.com" not in meet_link:
            try:
                import requests as _req2
                r_head = _req2.head(meet_link, allow_redirects=True, timeout=8)
                if "meet.google.com" in r_head.url:
                    actual_meet_url = r_head.url
            except Exception as _e:
                logger.warning(f"No se pudo resolver el link de Calendly {meet_link}: {_e}")

        # Create Recall bot to transcribe the Google Meet
        recall_bot_id = None
        if actual_meet_url and "meet.google.com" in actual_meet_url:
            try:
                import requests as _req
                recall_key = os.environ.get("RECALL_API_KEY", "")
                if recall_key:
                    rb = _req.post(
                        "https://us-east-1.recall.ai/api/v1/bot/",
                        headers={"Authorization": f"Token {recall_key}"},
                        json={"meeting_url": actual_meet_url, "bot_name": "Scalerics Bot"},
                        timeout=10,
                    )
                    if rb.ok:
                        recall_bot_id = rb.json().get("id")
                    else:
                        logger.warning(f"Recall bot creation failed: {rb.status_code} {rb.text}")
            except Exception as _e:
                logger.warning(f"Recall bot exception: {_e}")

        try:
            meeting_id = create_meeting(
                db_path,
                client_id=client_id,
                calendar_event_id=event_uri or None,
                title=title,
                start_at=start_at,
                end_at=end_at,
                meet_link=actual_meet_url or meet_link,
                status="scheduled",
                recall_bot_id=recall_bot_id,
            )
        except Exception:
            # Duplicate webhook from Calendly — meeting already exists
            return jsonify({"ok": True, "duplicate": True})

        log_activity(db_path, "calendly", "meeting_scheduled", "lead", client_id,
                     client["name"] if client else name,
                     f"Calendly: {title} · {start_at[:16] if start_at else ''}")

        return jsonify({"ok": True, "meeting_id": meeting_id, "recall_bot_id": recall_bot_id})

    if event_type == "invitee.canceled":
        event_uri = payload.get("payload", {}).get("event", {}).get("uri", "")
        if event_uri:
            import sqlite3
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "UPDATE meetings SET status='canceled' WHERE calendar_event_id=?",
                    (event_uri,)
                )
                conn.commit()
            finally:
                conn.close()
        return jsonify({"ok": True})

    return jsonify({"ok": True, "skipped": event_type})
