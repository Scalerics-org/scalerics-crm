import hashlib
import hmac
import json
import os
import re

from flask import Blueprint, request, jsonify

from database import create_meeting, get_business_by_phone, get_all_businesses, log_activity, update_business

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

        # Strip Z suffix and microseconds so SQLite DATE() works correctly
        def _norm_time(t):
            return t.replace("Z", "").split(".")[0] if t else ""
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

        # Create Recall bot to transcribe the Google Meet
        recall_bot_id = None
        if meet_link:
            try:
                import requests as _req
                recall_key = os.environ.get("RECALL_API_KEY", "")
                if recall_key:
                    rb = _req.post(
                        "https://us-east-1.recall.ai/api/v1/bot/",
                        headers={"Authorization": f"Token {recall_key}"},
                        json={"meeting_url": meet_link, "bot_name": "Scalerics Bot"},
                        timeout=10,
                    )
                    if rb.ok:
                        recall_bot_id = rb.json().get("id")
            except Exception:
                pass

        try:
            meeting_id = create_meeting(
                db_path,
                client_id=client_id,
                calendar_event_id=event_uri or None,
                title=title,
                start_at=start_at,
                end_at=end_at,
                meet_link=meet_link,
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
