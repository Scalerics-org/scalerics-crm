"""Carga los leads de Calendly leyendo Google Calendar.

Los webhooks de Calendly son plan Standard en adelante, y la cuenta de
Scalerics está en Free desde que el trial venció el 29/4/2026. Pero Calendly
igual crea el evento en el Google Calendar del host, y mete ahí las respuestas
del formulario de reserva. El CRM ya tiene el calendario conectado, así que
leemos de ahí.

Formato real de la descripción que arma Calendly (verificado con una reserva
de prueba el 12/8/2026):

    Nombre del evento
    Consultoria gratuita de Scalerics

    Ubicación: Esto es una conferencia web de Google Meet.
    ...
    https://calendly.com/events/<uuid>/google_meet

    Empresa: Ferretería El Sol

    ¿Qué necesitás?: E-commerce / tienda online

    Teléfono / WhatsApp: +598 99 123 456

    ¿Necesitas realizar algún cambio a este evento?
    Cancelar: https://calendly.com/cancellations/<uuid>
    ...
"""

import logging
import os
import re
import sqlite3

from database import (
    create_meeting,
    get_all_businesses,
    get_business_by_phone,
    log_activity,
    update_business,
)

log = logging.getLogger(__name__)

# Mismas keywords que usa el webhook, para que las dos vías coincidan.
PHONE_KEYWORDS = ["whatsapp", "teléfono", "telefono", "celular", "phone",
                  "número", "numero", "mobile", "cel"]
COMPANY_KEYWORDS = ["empresa", "negocio", "company", "comercio", "rubro"]
SERVICE_KEYWORDS = ["necesit", "servicio", "interesa", "buscás", "buscas",
                    "qué querés", "que queres"]

# Líneas "clave: valor" que Calendly arma solo y que no son respuestas.
_NOT_ANSWERS = ["ubicación", "ubicacion", "location", "cancelar", "reprogramar",
                "cancel", "reschedule", "nombre del evento"]

_EVENT_UUID_RE = re.compile(r"calendly\.com/events/([0-9a-fA-F-]{36})")
_ANSWER_RE = re.compile(r"^([^:\n]{1,80}):[ \t]*(.*)$")
_CANCELED_PREFIXES = ("cancelado:", "canceled:", "cancelled:")


def _normalize_phone(phone: str) -> str:
    return "".join(c for c in (phone or "") if c.isdigit() or c == "+")


def _answer_for(answers: dict, keywords: list) -> str:
    for question, value in answers.items():
        if any(kw in question.lower() for kw in keywords):
            return value
    return ""


def _extract_answers(description: str) -> dict:
    """Saca los pares 'Pregunta: respuesta' que puso el invitado."""
    answers = {}
    for raw in (description or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _ANSWER_RE.match(line)
        if not m:
            continue
        question, value = m.group(1).strip(), m.group(2).strip()
        if any(skip in question.lower() for skip in _NOT_ANSWERS):
            continue
        if not value or value.startswith("http"):
            continue
        answers[question] = value
    return answers


def _guest_name(summary: str, host_name: str) -> str:
    """Calendly titula el evento '<invitado> y <host>'."""
    name = (summary or "").strip()
    low = name.lower()
    for prefix in _CANCELED_PREFIXES:
        if low.startswith(prefix):
            name = name[len(prefix):].strip()
            break
    # Cortar por el sufijo exacto del host es lo más seguro...
    if host_name:
        suffix = f" y {host_name}"
        if name.endswith(suffix):
            return name[: -len(suffix)].strip()
    # ...pero Google no siempre devuelve displayName del organizador (en los
    # eventos reales de Scalerics viene sólo el email). Cortamos por el ÚLTIMO
    # " y ", que es el separador que pone Calendly: así "Pedro y Pablo SRL y
    # Contacto Scalerics" queda como "Pedro y Pablo SRL".
    head, sep, _tail = name.rpartition(" y ")
    if sep and head.strip():
        return head.strip()
    return name


def team_emails_from_env() -> set:
    """Mails del equipo, que Calendly suma como attendees de las reuniones.

    Sin esto los tomábamos por el cliente y cuatro reuniones distintas
    terminaban con el mismo mail.
    """
    raw = os.environ.get("CALENDLY_TEAM_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def parse_calendly_event(event: dict, host_email: str,
                         team_emails: set = None) -> dict:
    """Devuelve los datos del invitado, o None si el evento no es de Calendly."""
    description = event.get("description") or ""
    m = _EVENT_UUID_RE.search(description)
    if not m:
        return None

    answers = _extract_answers(description)
    host_name = (event.get("organizer") or {}).get("displayName") or ""

    if team_emails is None:
        team_emails = team_emails_from_env()
    ignorar = {(host_email or "").lower()} | {e.lower() for e in team_emails}

    email = ""
    for a in event.get("attendees") or []:
        addr = (a.get("email") or "").lower()
        if addr and addr not in ignorar:
            email = a.get("email")
            break

    summary = event.get("summary") or ""
    canceled = summary.strip().lower().startswith(_CANCELED_PREFIXES)

    return {
        "event_uri": f"https://api.calendly.com/scheduled_events/{m.group(1)}",
        "name": _guest_name(summary, host_name),
        "email": email,
        "company": _answer_for(answers, COMPANY_KEYWORDS),
        "service": _answer_for(answers, SERVICE_KEYWORDS),
        "phone": _answer_for(answers, PHONE_KEYWORDS),
        "start_at": (event.get("start") or {}).get("dateTime", "")[:19],
        "end_at": (event.get("end") or {}).get("dateTime", "")[:19],
        "meet_link": event.get("hangoutLink") or "",
        "canceled": canceled,
    }


def _find_client(db_path: str, email: str, phone: str):
    if phone:
        normalized = _normalize_phone(phone)
        biz = get_business_by_phone(db_path, normalized)
        if biz:
            return biz
        if normalized.startswith("+598"):
            biz = get_business_by_phone(db_path, normalized[4:])
            if biz:
                return biz
    if email:
        for b in get_all_businesses(db_path):
            if (b.get("email") or "").lower() == email.lower():
                return b
    return None


def _existing_meeting(db_path: str, event_uri: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM meetings WHERE calendar_event_id = ?", (event_uri,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def sync_events(db_path: str, events: list, host_email: str,
                dry_run: bool = False, team_emails: set = None,
                now: str = None) -> dict:
    """Carga al CRM los eventos de Calendly que todavía no estén.

    Idempotente: se apoya en calendar_event_id, así que se puede correr
    cuantas veces se quiera. Con dry_run=True no escribe nada y devuelve en
    `would_create` lo que cargaría — útil para el primer sync, que barre
    reuniones viejas ya existentes.
    """
    if team_emails is None:
        team_emails = team_emails_from_env()

    parsed, ignored = [], 0
    for event in events or []:
        data = parse_calendly_event(event, host_email, team_emails)
        if data:
            parsed.append(data)
        else:
            ignored += 1
    return sync_parsed(db_path, parsed, dry_run=dry_run, now=now,
                       ignored=ignored)


def sync_parsed(db_path: str, datas: list, dry_run: bool = False,
                now: str = None, ignored: int = 0) -> dict:
    """Escribe al CRM una lista ya parseada, venga del calendario o del mail.

    Las dos vías comparten el mismo `event_uri`, así que un evento que ya
    entró por una no se duplica por la otra.
    """
    stats = {"created": 0, "skipped": 0, "canceled": 0, "ignored": ignored,
             "sin_contacto": 0, "would_create": []}
    if now is None:
        import datetime
        now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    for data in datas or []:

        existing = _existing_meeting(db_path, data["event_uri"])

        if data["canceled"]:
            if existing and existing.get("status") != "canceled":
                if dry_run:
                    stats["canceled"] += 1
                    continue
                conn = sqlite3.connect(db_path)
                try:
                    conn.execute(
                        "UPDATE meetings SET status='canceled' WHERE calendar_event_id=?",
                        (data["event_uri"],),
                    )
                    conn.commit()
                finally:
                    conn.close()
                stats["canceled"] += 1
            else:
                stats["skipped"] += 1
            continue

        if existing:
            stats["skipped"] += 1
            continue

        # Las reuniones viejas, de antes de que el formulario pidiera datos, no
        # traen con qué contactar a nadie: cargarlas sólo ensucia el CRM.
        if not data["phone"] and not data["email"]:
            stats["sin_contacto"] += 1
            continue

        note_lines = []
        if data["name"]:
            note_lines.append(f"Contacto: {data['name']}")
        if data["service"]:
            note_lines.append(f"Interés: {data['service']}")
        booking_note = "\n".join(note_lines)

        client = _find_client(db_path, data["email"], data["phone"])

        if dry_run:
            stats["would_create"].append({
                "lead": data["company"] or data["name"] or data["email"],
                "contacto": data["name"],
                "email": data["email"],
                "telefono": data["phone"],
                "servicio": data["service"],
                "fecha": data["start_at"][:16],
                "match": (client or {}).get("name") if client else None,
            })
            stats["created"] += 1
            continue

        if not client:
            conn = sqlite3.connect(db_path)
            try:
                cur = conn.execute(
                    "INSERT INTO businesses (name, email, phone, interest, notes, crm_status, source) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (data["company"] or data["name"] or data["email"],
                     data["email"] or None,
                     _normalize_phone(data["phone"]) or None,
                     data["service"] or None,
                     booking_note or None,
                     "reunion_agendada",
                     "calendly_unmatched"),
                )
                conn.commit()
                client_id = cur.lastrowid
            finally:
                conn.close()
        else:
            client_id = client["id"]
            updates = {}
            # Una reunión que ya pasó no dice nada sobre dónde está hoy el
            # lead: pisarle el estado lo haría retroceder desde "cliente".
            if data["start_at"] and data["start_at"] > now:
                updates["crm_status"] = "reunion_agendada"
            if data["service"] and not (client.get("interest") or "").strip():
                updates["interest"] = data["service"]
            if data["email"] and not (client.get("email") or "").strip():
                updates["email"] = data["email"]
            if booking_note:
                current = (client.get("notes") or "").strip()
                if booking_note not in current:
                    updates["notes"] = f"{current}\n\n{booking_note}".strip()
            update_business(db_path, client_id, **updates)

        title = f"Reunión con {data['name']}" if data["name"] else "Reunión Calendly"
        try:
            create_meeting(
                db_path,
                client_id=client_id,
                calendar_event_id=data["event_uri"],
                title=title,
                start_at=data["start_at"],
                end_at=data["end_at"],
                meet_link=data["meet_link"],
                status="scheduled",
            )
        except Exception:
            stats["skipped"] += 1
            continue

        log_activity(db_path, "calendly-sync", "meeting_scheduled", "lead", client_id,
                     data["company"] or data["name"],
                     f"Calendly: {title} · {data['start_at'][:16]}"
                     + (f" · {data['service']}" if data["service"] else ""))
        stats["created"] += 1

    return stats


def fetch_and_sync(db_path: str, days_back: int = 30, days_ahead: int = 90,
                   dry_run: bool = False) -> dict:
    """Trae los eventos del Google Calendar del host y los sincroniza."""
    import datetime

    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    client_id = os.environ.get("GCAL_CLIENT_ID", "")
    client_secret = os.environ.get("GCAL_CLIENT_SECRET", "")
    refresh_token = os.environ.get("GCAL_REFRESH_TOKEN", "")
    if not all([client_id, client_secret, refresh_token]):
        raise RuntimeError("Faltan GCAL_CLIENT_ID, GCAL_CLIENT_SECRET o GCAL_REFRESH_TOKEN")

    creds = Credentials(
        None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
    )
    service = build("calendar", "v3", credentials=creds)

    now = datetime.datetime.now(datetime.timezone.utc)
    time_min = (now - datetime.timedelta(days=days_back)).isoformat()
    time_max = (now + datetime.timedelta(days=days_ahead)).isoformat()

    events, page_token = [], None
    while True:
        resp = service.events().list(
            calendarId="primary", timeMin=time_min, timeMax=time_max,
            singleEvents=True, orderBy="startTime", maxResults=250,
            pageToken=page_token,
        ).execute()
        events.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    host_email = os.environ.get("CALENDLY_HOST_EMAIL", "")
    if not host_email:
        host_email = service.calendars().get(calendarId="primary").execute().get("id", "")

    return sync_events(db_path, events, host_email=host_email, dry_run=dry_run,
                       team_emails=team_emails_from_env())
