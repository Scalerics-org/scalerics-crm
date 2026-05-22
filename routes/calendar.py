"""Google Calendar routes."""

import datetime
import uuid

import pytz
from flask import Blueprint, current_app, jsonify, request

from database import (
    create_meeting,
    delete_meeting,
    get_meeting,
    get_meetings_for_client,
    update_meeting,
)

calendar_bp = Blueprint("calendar", __name__)

MVD = pytz.timezone("America/Montevideo")


def _maybe_revert_lead_status(db_path: str, client_id: int) -> None:
    """Revert lead CRM status to 'contactado' if they have no remaining meetings."""
    if not client_id:
        return
    from database import get_business, update_business
    biz = get_business(db_path, client_id)
    if not biz:
        return
    if biz.get("crm_status") != "reunion_agendada":
        return
    remaining = get_meetings_for_client(db_path, client_id)
    if not remaining:
        update_business(db_path, client_id, crm_status="contactado")


def _get_calendar_service():
    import os
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        return None, "google-api-python-client no instalado"

    client_id = os.environ.get("GCAL_CLIENT_ID", "")
    client_secret = os.environ.get("GCAL_CLIENT_SECRET", "")
    refresh_token = os.environ.get("GCAL_REFRESH_TOKEN", "")
    if not all([client_id, client_secret, refresh_token]):
        return None, "Faltan GCAL_CLIENT_ID, GCAL_CLIENT_SECRET o GCAL_REFRESH_TOKEN en .env"

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    service = build("calendar", "v3", credentials=creds)
    return service, None


def _db() -> str:
    return current_app.config["DB_PATH"]


@calendar_bp.route("/api/calendar/events", methods=["GET", "POST"])
def api_calendar_events():
    if request.method == "GET":
        service, err = _get_calendar_service()
        if err:
            return jsonify({"error": err})

        start = request.args.get("start")
        end = request.args.get("end")
        if not start or not end:
            return jsonify({"error": "Parámetros start y end requeridos"})

        try:
            time_min = start + "T00:00:00Z"
            time_max = end + "T23:59:59Z"
            result = service.events().list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=100,
            ).execute()

            events = []
            for item in result.get("items", []):
                start_data = item.get("start", {})
                date_str = start_data.get("dateTime", start_data.get("date", ""))
                time_str = ""
                day_str = ""
                if "T" in date_str:
                    dt = datetime.datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    dt_local = dt.astimezone(MVD)
                    day_str = dt_local.strftime("%Y-%m-%d")
                    time_str = dt_local.strftime("%H:%M")
                else:
                    day_str = date_str

                meet_url = item.get("hangoutLink") or ""
                if not meet_url:
                    for ep in (item.get("conferenceData") or {}).get("entryPoints", []):
                        if ep.get("entryPointType") == "video":
                            meet_url = ep.get("uri", "")
                            break
                if not meet_url:
                    loc = item.get("location", "")
                    if loc.startswith("http"):
                        meet_url = loc

                events.append({
                    "id": item.get("id"),
                    "title": item.get("summary", ""),
                    "description": item.get("description", ""),
                    "date": day_str,
                    "time": time_str,
                    "meeting_url": meet_url,
                })
            return jsonify({"events": events})
        except Exception as e:
            return jsonify({"error": str(e)})

    # POST — create event
    service, err = _get_calendar_service()
    if err:
        return jsonify({"ok": False, "error": err})

    data = request.get_json() or {}
    title = (data.get("title") or "").strip()
    date = data.get("date", "")
    time = data.get("time", "")
    duration_min = int(data.get("duration_min") or 60)
    attendee_email = (data.get("attendee_email") or "").strip()
    description = (data.get("description") or "").strip()
    client_id = data.get("client_id")

    if not title or not date or not time:
        return jsonify({"ok": False, "error": "title, date y time requeridos"})

    try:
        start_dt = datetime.datetime.fromisoformat(f"{date}T{time}:00")
        end_dt = start_dt + datetime.timedelta(minutes=duration_min)

        event_body = {
            "summary": title,
            "description": description,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/Montevideo"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "America/Montevideo"},
            "conferenceData": {
                "createRequest": {
                    "requestId": str(uuid.uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
        }
        if attendee_email:
            event_body["attendees"] = [{"email": attendee_email}]

        created = service.events().insert(
            calendarId="primary",
            body=event_body,
            conferenceDataVersion=1,
            sendUpdates="all" if attendee_email else "none",
        ).execute()

        meet_url = created.get("hangoutLink", "")
        cal_event_id = created.get("id", "")

        if client_id:
            create_meeting(
                _db(),
                int(client_id),
                calendar_event_id=cal_event_id,
                title=title,
                start_at=start_dt.isoformat(),
                end_at=end_dt.isoformat(),
                meet_link=meet_url,
                status="scheduled",
            )
            from database import update_business
            update_business(_db(), int(client_id), crm_status="reunion_agendada")

        return jsonify({"ok": True, "meet_url": meet_url, "event_id": cal_event_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@calendar_bp.route("/api/calendar/meetings/<int:client_id>", methods=["GET"])
def api_client_meetings(client_id):
    meetings = get_meetings_for_client(_db(), client_id)
    return jsonify(meetings)


@calendar_bp.route("/api/calendar/meetings/<int:meeting_id>", methods=["GET"])
def api_get_meeting(meeting_id):
    meeting = get_meeting(_db(), meeting_id)
    if not meeting:
        return jsonify({"error": "Reunión no encontrada"}), 404
    return jsonify(meeting)


@calendar_bp.route("/api/calendar/meetings/<int:meeting_id>/notes", methods=["POST"])
def api_meeting_notes(meeting_id):
    data = request.get_json() or {}
    update_meeting(
        _db(), meeting_id,
        transcript=data.get("transcript"),
        summary=data.get("summary"),
        requirements=data.get("requirements"),
    )
    return jsonify({"ok": True})


@calendar_bp.route("/api/calendar/meetings/<int:meeting_id>/summarize", methods=["POST"])
def api_summarize_meeting(meeting_id):
    import json, os
    import anthropic
    from database import get_meeting

    data = request.get_json() or {}
    transcript = (data.get("transcript") or "").strip()
    if not transcript:
        return jsonify({"ok": False, "error": "transcript requerido"}), 400

    prompt = f"""Resumí esta transcripción de reunión de ventas y extraé los requerimientos del proyecto.

TRANSCRIPCIÓN:
{transcript[:6000]}

Devolvé SOLO un JSON (sin texto extra, sin markdown):
{{
  "summary": "resumen de 2-3 oraciones de qué se habló y qué quiere el cliente",
  "requirements": "requerimientos detallados del proyecto (en bullet points con guión)",
  "service_type": "web|ecommerce|app|automatizacion|otro",
  "next_steps": ["acción concreta 1", "acción concreta 2"]
}}"""

    try:
        ai = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        msg = ai.messages.create(
            model="claude-haiku-4-5",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rstrip("`").strip()
        result = json.loads(raw)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Error resumiendo: {e}"}), 500

    update_meeting(
        _db(), meeting_id,
        transcript=transcript,
        summary=result.get("summary", ""),
        requirements=result.get("requirements", ""),
    )

    budget_generated = False
    try:
        from database import get_meeting as _get_meeting
        from routes.budgets import _generate_budget_internal
        meeting_record = _get_meeting(_db(), meeting_id)
        if meeting_record and meeting_record.get("client_id"):
            cid = meeting_record["client_id"]
            auto = _generate_budget_internal(
                _db(), cid,
                requirements=result.get("requirements", ""),
                service_type=result.get("service_type", ""),
            )
            if auto:
                from database import update_business
                update_business(_db(), cid, crm_status="presupuesto_enviado")
                budget_generated = True
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Auto-budget failed for meeting {meeting_id}: {e}")

    return jsonify({"ok": True, "summary": result, "budget_generated": budget_generated})


@calendar_bp.route("/api/calendar/events/<string:cal_event_id>/attendees", methods=["POST"])
def api_add_attendee(cal_event_id):
    service, err = _get_calendar_service()
    if err:
        return jsonify({"ok": False, "error": err})

    data = request.get_json() or {}
    email = (data.get("email") or "").strip()
    if not email:
        return jsonify({"ok": False, "error": "email requerido"})

    try:
        event = service.events().get(calendarId="primary", eventId=cal_event_id).execute()
        attendees = event.get("attendees", [])
        if not any(a.get("email") == email for a in attendees):
            attendees.append({"email": email})
        event["attendees"] = attendees
        service.events().update(
            calendarId="primary",
            eventId=cal_event_id,
            body=event,
            sendUpdates="all",
        ).execute()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@calendar_bp.route("/api/calendar/events/<string:cal_event_id>", methods=["DELETE"])
def api_delete_cal_event(cal_event_id):
    service, err = _get_calendar_service()
    if service:
        try:
            service.events().delete(
                calendarId="primary",
                eventId=cal_event_id,
                sendUpdates="all",
            ).execute()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"No se pudo borrar evento {cal_event_id} en Calendar: {e}")

    import sqlite3
    db = _db()
    client_id = None
    try:
        con = sqlite3.connect(db)
        row = con.execute("SELECT client_id FROM meetings WHERE calendar_event_id = ?", (cal_event_id,)).fetchone()
        client_id = row[0] if row else None
        con.execute("DELETE FROM meetings WHERE calendar_event_id = ?", (cal_event_id,))
        con.commit()
        con.close()
    except Exception:
        pass

    if client_id:
        _maybe_revert_lead_status(db, client_id)

    return jsonify({"ok": True})


@calendar_bp.route("/api/calendar/meetings/<int:meeting_id>", methods=["DELETE"])
def api_delete_meeting(meeting_id):
    meeting = get_meeting(_db(), meeting_id)
    if not meeting:
        return jsonify({"ok": False, "error": "Reunión no encontrada"}), 404

    cal_event_id = meeting.get("calendar_event_id")
    if cal_event_id:
        service, err = _get_calendar_service()
        if service:
            try:
                service.events().delete(
                    calendarId="primary",
                    eventId=cal_event_id,
                    sendUpdates="all",
                ).execute()
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"No se pudo cancelar evento en Calendar: {e}")

    delete_meeting(_db(), meeting_id)
    client_id = meeting.get("client_id")
    if client_id:
        _maybe_revert_lead_status(_db(), client_id)
    return jsonify({"ok": True})
