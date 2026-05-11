"""Google Calendar routes."""

import datetime
import uuid

import pytz
from flask import Blueprint, current_app, jsonify, request

from database import (
    create_meeting,
    get_meetings_for_client,
    get_meeting_by_calendar_id,
    update_meeting,
)

calendar_bp = Blueprint("calendar", __name__)

MVD = pytz.timezone("America/Montevideo")


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

        return jsonify({"ok": True, "meet_url": meet_url, "event_id": cal_event_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@calendar_bp.route("/api/calendar/meetings/<int:client_id>", methods=["GET"])
def api_client_meetings(client_id):
    meetings = get_meetings_for_client(_db(), client_id)
    return jsonify(meetings)


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
