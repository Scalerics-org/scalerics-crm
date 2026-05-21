"""Token health status endpoint."""

import base64
import json
import os
from datetime import datetime, timezone, timedelta

from flask import Blueprint, jsonify

tokens_bp = Blueprint("tokens", __name__)

GOOGLE_TTL_DAYS = 7  # testing mode expiry


def _days_left(renewed_at_iso: str) -> float | None:
    if not renewed_at_iso:
        return None
    try:
        renewed = datetime.fromisoformat(renewed_at_iso)
        if renewed.tzinfo is None:
            renewed = renewed.replace(tzinfo=timezone.utc)
        expires = renewed + timedelta(days=GOOGLE_TTL_DAYS)
        delta = expires - datetime.now(timezone.utc)
        return delta.total_seconds() / 86400
    except Exception:
        return None


def _format_label(days: float | None) -> str:
    if days is None:
        return "Sin datos"
    if days <= 0:
        return "Vencido"
    if days < 1:
        hours = int(days * 24)
        return f"Vence en {hours}h"
    return f"Vence en {int(days)}d"


def _status_from_days(days: float | None) -> str:
    if days is None:
        return "unknown"
    if days <= 0:
        return "danger"
    if days <= 1:
        return "danger"
    if days <= 3:
        return "warning"
    return "ok"


def _decode_jwt_exp(token: str) -> float | None:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1] + "=="
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        if not exp:
            return None
        expires = datetime.fromtimestamp(exp, tz=timezone.utc)
        delta = expires - datetime.now(timezone.utc)
        return delta.total_seconds() / 86400
    except Exception:
        return None


@tokens_bp.route("/api/tokens/status")
def api_tokens_status():
    results = []

    # Gmail
    gmail_days = _days_left(os.environ.get("GMAIL_TOKEN_RENEWED_AT", ""))
    results.append({
        "name": "Gmail",
        "key": "GMAIL_REFRESH_TOKEN",
        "status": _status_from_days(gmail_days),
        "label": _format_label(gmail_days),
    })

    # Google Calendar
    gcal_days = _days_left(os.environ.get("GCAL_TOKEN_RENEWED_AT", ""))
    results.append({
        "name": "Google Calendar",
        "key": "GCAL_REFRESH_TOKEN",
        "status": _status_from_days(gcal_days),
        "label": _format_label(gcal_days),
    })

    # WhatsApp (try JWT decode)
    wa_token = os.environ.get("WA_ACCESS_TOKEN", "")
    wa_days = _decode_jwt_exp(wa_token) if wa_token else None
    results.append({
        "name": "WhatsApp",
        "key": "WA_ACCESS_TOKEN",
        "status": _status_from_days(wa_days) if wa_days is not None else "unknown",
        "label": _format_label(wa_days),
    })

    # Vercel — no expiry
    results.append({
        "name": "Vercel",
        "key": "VERCEL_TOKEN",
        "status": "permanent",
        "label": "Permanente",
    })

    return jsonify(results)
