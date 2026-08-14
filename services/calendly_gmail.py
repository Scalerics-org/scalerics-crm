"""Carga los leads de Calendly leyendo los mails de confirmación.

Complementa a [calendly_gcal]: el evento de Google Calendar trae empresa,
servicio y teléfono, pero **no** el mail del invitado. El mail de "New Event"
sí lo trae, en un campo propio ("Invitee Email"), así que esta vía completa el
dato que faltaba.

Las dos usan el mismo `event_uri`, así que un evento no se carga dos veces.

Formato real del mail (capturado el 14/8/2026 de scalerics@gmail.com). Es
HTML, sin parte text/plain, y los labels van en inglés aunque el event type
esté en español:

    Hi Contacto Scalerics,
    A new event has been scheduled.
    Event Type:
    Consultoria gratuita de Scalerics
    Invitee:
    Ana Clara Veterinaria
    Invitee Email:
    scalerics@gmail.com
    Additional Guests:
    gonzasiuciak@gmail.com
    Event Date/Time:
    12:30 - Friday, 14 August 2026 (Eastern Time - US & Canada)
    ...
    Questions:
    Empresa
    Scalerics
    ¿Qué necesitás?
    Desarrollo a medida
    Teléfono / WhatsApp
    +598 92 781 598

El valor de cada pregunta viene en la línea siguiente al enunciado, no
separado por ":" como en el calendario.
"""

import base64
import html as _html
import os
import re

from services.calendly_gcal import (
    COMPANY_KEYWORDS,
    PHONE_KEYWORDS,
    SERVICE_KEYWORDS,
    _answer_for,
    sync_parsed,
    team_emails_from_env,
)

_EVENT_UUID_RE = re.compile(r"calendly\.com/events/([0-9a-fA-F-]{36})")
_LABELS = ("Event Type:", "Invitee:", "Invitee Email:", "Additional Guests:",
           "Event Date/Time:", "Location:", "Invitee Time Zone:", "Questions:")


def html_to_lines(raw_html: str) -> list:
    """HTML del mail a líneas de texto, que es como viene armado el cuerpo."""
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw_html or "",
               flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>|</tr>|</p>|</div>|</td>|</h\d>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = _html.unescape(t)
    return [ln.strip() for ln in t.splitlines() if ln.strip()]


def _value_after(lines: list, label: str) -> str:
    """El valor de un campo es la línea siguiente al label."""
    for i, ln in enumerate(lines):
        if ln == label and i + 1 < len(lines):
            nxt = lines[i + 1]
            if nxt in _LABELS:      # campo vacío
                return ""
            return nxt
    return ""


def _questions(lines: list) -> dict:
    """Pares pregunta/respuesta después de 'Questions:'."""
    try:
        start = lines.index("Questions:") + 1
    except ValueError:
        return {}
    answers, i = {}, start
    while i + 1 < len(lines):
        question = lines[i]
        # El bloque termina donde arranca el pie del mail.
        if question.startswith(("View event in Calendly", "Pro Tip", "Sent from")):
            break
        value = lines[i + 1]
        if value.startswith(("View event in Calendly", "Pro Tip", "Sent from")):
            break
        answers[question] = value
        i += 2
    return answers


def parse_calendly_email(raw_html: str, team_emails: set = None) -> dict:
    """Devuelve los datos del invitado, o None si el mail no es de un evento."""
    m = _EVENT_UUID_RE.search(raw_html or "")
    if not m:
        return None

    lines = html_to_lines(raw_html)
    if not any(ln == "Invitee:" for ln in lines):
        return None

    if team_emails is None:
        team_emails = team_emails_from_env()

    email = _value_after(lines, "Invitee Email:")
    # Si quien reservó puso una dirección del equipo, no es el mail del cliente.
    if email.lower() in {e.lower() for e in team_emails}:
        email = ""

    answers = _questions(lines)
    canceled = any(ln.startswith("This event has been canceled")
                   or ln == "Event Canceled" for ln in lines)

    return {
        "event_uri": f"https://api.calendly.com/scheduled_events/{m.group(1)}",
        "name": _value_after(lines, "Invitee:"),
        "email": email,
        "company": _answer_for(answers, COMPANY_KEYWORDS),
        "service": _answer_for(answers, SERVICE_KEYWORDS),
        "phone": _answer_for(answers, PHONE_KEYWORDS),
        "start_at": "",          # la fecha exacta la aporta el calendario
        "end_at": "",
        "meet_link": "",
        "canceled": canceled,
    }


def _message_html(service, msg_id: str) -> str:
    msg = service.users().messages().get(
        userId="me", id=msg_id, format="full").execute()
    found = []

    def walk(part):
        if part.get("body", {}).get("data") and "html" in (part.get("mimeType") or ""):
            found.append(base64.urlsafe_b64decode(
                part["body"]["data"]).decode("utf-8", "replace"))
        for sub in part.get("parts") or []:
            walk(sub)

    walk(msg["payload"])
    return found[0] if found else ""


def fetch_and_sync_gmail(db_path: str, days_back: int = 30,
                         dry_run: bool = False, max_messages: int = 100) -> dict:
    """Lee los mails de Calendly de la casilla del host y los sincroniza."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    client_id = os.environ.get("GMAIL_CLIENT_ID", "")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "")
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN", "")
    if not all([client_id, client_secret, refresh_token]):
        raise RuntimeError("Faltan GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET o "
                           "GMAIL_REFRESH_TOKEN")

    creds = Credentials(
        None, refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id, client_secret=client_secret,
    )
    service = build("gmail", "v1", credentials=creds)

    query = f"from:calendly.com newer_than:{days_back}d"
    ids, page_token = [], None
    while len(ids) < max_messages:
        resp = service.users().messages().list(
            userId="me", q=query, maxResults=100, pageToken=page_token).execute()
        ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    team = team_emails_from_env()
    parsed, ignored = [], 0
    for msg_id in ids[:max_messages]:
        data = parse_calendly_email(_message_html(service, msg_id), team)
        if data:
            parsed.append(data)
        else:
            ignored += 1

    return sync_parsed(db_path, parsed, dry_run=dry_run, ignored=ignored)
