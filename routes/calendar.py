"""Google Calendar routes."""

import datetime
import uuid

import pytz
from flask import Blueprint, current_app, jsonify, request, session

from database import (
    create_meeting,
    delete_meeting,
    get_business,
    get_lead_contributor_ids,
    get_meeting,
    get_meetings_for_client,
    increment_task_progress,
    log_activity,
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


_GCAL_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive.readonly",
]


def _build_creds():
    import os
    try:
        from google.oauth2.credentials import Credentials
    except ImportError:
        return None, "google-api-python-client no instalado"

    client_id = os.environ.get("GCAL_CLIENT_ID", "")
    client_secret = os.environ.get("GCAL_CLIENT_SECRET", "")
    refresh_token = os.environ.get("GCAL_REFRESH_TOKEN", "")
    if not all([client_id, client_secret, refresh_token]):
        return None, "Faltan GCAL_CLIENT_ID, GCAL_CLIENT_SECRET o GCAL_REFRESH_TOKEN en .env"

    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=_GCAL_SCOPES,
    ), None


def _get_calendar_service():
    try:
        from googleapiclient.discovery import build
    except ImportError:
        return None, "google-api-python-client no instalado"
    creds, err = _build_creds()
    if err:
        return None, err
    return build("calendar", "v3", credentials=creds), None


def _create_recall_bot(meet_url: str) -> str | None:
    import os, requests
    api_key = os.environ.get("RECALL_API_KEY", "")
    if not api_key or not meet_url:
        return None
    try:
        r = requests.post(
            "https://us-east-1.recall.ai/api/v1/bot/",
            headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
            json={"meeting_url": meet_url, "bot_name": "Scalerics Bot"},
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("id")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Recall bot creation failed: {e}")
        return None


def _get_drive_service():
    try:
        from googleapiclient.discovery import build
    except ImportError:
        return None, "google-api-python-client no instalado"
    creds, err = _build_creds()
    if err:
        return None, err
    return build("drive", "v3", credentials=creds), None


def _db() -> str:
    return current_app.config["DB_PATH"]


def _contributors(db: str, lead_id: int, current_uid: int | None) -> list[int]:
    ids = set(get_lead_contributor_ids(db, lead_id))
    if current_uid:
        ids.add(current_uid)
    return list(ids)


def _sync_gcal_to_db(db: str, start: str, end: str) -> None:
    """Pull Google Calendar events for the given date range and upsert into meetings table."""
    import re, sqlite3 as _sq
    from datetime import timezone as _tz
    service, err = _get_calendar_service()
    if err or not service:
        return
    try:
        items = service.events().list(
            calendarId="primary",
            timeMin=f"{start}T00:00:00Z",
            timeMax=f"{end}T23:59:59Z",
            singleEvents=True,
            orderBy="startTime",
            maxResults=100,
        ).execute().get("items", [])
    except Exception as e:
        import logging; logging.getLogger(__name__).warning(f"GCal sync error: {e}")
        return

    conn = _sq.connect(db); conn.row_factory = _sq.Row
    try:
        for ev in items:
            gcal_id = ev.get("id", "")
            if not gcal_id:
                continue
            # Skip cancelled events
            if ev.get("status") == "cancelled":
                continue
            summary = ev.get("summary", "Reunión")
            if summary.startswith("Cancelado:"):
                continue

            if conn.execute("SELECT id FROM meetings WHERE calendar_event_id=?", (gcal_id,)).fetchone():
                continue

            # Will be set after UTC conversion — check for existing meeting at same hour


            raw_start = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date", "")
            raw_end   = ev.get("end",   {}).get("dateTime") or ev.get("end",   {}).get("date", "")

            # Keep local Uruguay time — strip timezone offset but preserve local value
            def _to_utc(raw):
                if not raw:
                    return ""
                try:
                    import datetime as _dt
                    d = _dt.datetime.fromisoformat(raw)
                    # Convert to Uruguay (UTC-3) local time
                    if d.tzinfo:
                        d = d.astimezone(_dt.timezone(-_dt.timedelta(hours=3))).replace(tzinfo=None)
                    return d.strftime("%Y-%m-%dT%H:%M:%S")
                except Exception:
                    return re.sub(r"(\.\d+)?([+-]\d{2}:\d{2}|Z)$", "", raw)

            start_at = _to_utc(raw_start)
            end_at   = _to_utc(raw_end)

            # Skip if meeting already exists at same UTC hour
            if start_at and conn.execute(
                "SELECT id FROM meetings WHERE SUBSTR(start_at,1,13)=?", (start_at[:13],)
            ).fetchone():
                continue

            meet_link = ""
            for ep in (ev.get("conferenceData") or {}).get("entryPoints", []):
                if ep.get("entryPointType") == "video":
                    meet_link = ep.get("uri", "")
                    break

            # Find invitee (first non-organizer attendee)
            invitee_email = invitee_name = ""
            for att in (ev.get("attendees") or []):
                if att.get("organizer") or att.get("self"):
                    continue
                invitee_email = att.get("email", "")
                invitee_name  = att.get("displayName", "")
                break

            # Try to match to existing client
            client_id = None
            if invitee_email:
                row = conn.execute("SELECT id FROM businesses WHERE email=?", (invitee_email,)).fetchone()
                if row:
                    client_id = row["id"]
            if not client_id:
                name = invitee_name or invitee_email or summary
                cur = conn.execute(
                    "INSERT INTO businesses (name, email, crm_status, source) VALUES (?,?,?,?)",
                    (name, invitee_email or None, "reunion_agendada", "calendly_gcal"),
                )
                client_id = cur.lastrowid

            conn.execute(
                "INSERT INTO meetings (client_id, calendar_event_id, title, start_at, end_at, meet_link, status) VALUES (?,?,?,?,?,?,?)",
                (client_id, gcal_id, summary, start_at, end_at, meet_link, "scheduled"),
            )
        conn.commit()
    except Exception as e:
        import logging; logging.getLogger(__name__).warning(f"GCal upsert error: {e}")
    finally:
        conn.close()


@calendar_bp.route("/api/calendar/events", methods=["GET", "POST"])
def api_calendar_events():
    if request.method == "GET":
        import sqlite3 as _sq
        start = request.args.get("start", "")
        end = request.args.get("end", "")
        db = _db()

        # Sync new Google Calendar events (Calendly creates them there)
        if start and end:
            _sync_gcal_to_db(db, start, end)

        conn = _sq.connect(db); conn.row_factory = _sq.Row
        try:
            rows = conn.execute("""
                SELECT m.id, m.title, m.start_at, m.end_at, m.meet_link, m.status,
                       m.calendar_event_id, b.name as client_name, m.client_id
                FROM meetings m
                LEFT JOIN businesses b ON m.client_id = b.id
                WHERE m.status != 'canceled'
                  AND (? = '' OR SUBSTR(m.start_at, 1, 10) >= ?)
                  AND (? = '' OR SUBSTR(m.start_at, 1, 10) <= ?)
                ORDER BY m.start_at ASC
            """, (start, start, end, end)).fetchall()
            events = []
            for r in rows:
                sa = r["start_at"] or ""
                day_str = sa[:10] if sa else ""
                time_str = sa[11:16] if "T" in sa else ""
                events.append({
                    "id": str(r["id"]),
                    "title": r["title"] or r["client_name"] or "Reunión",
                    "date": day_str,
                    "time": time_str,
                    "meeting_url": r["meet_link"] or "",
                    "client_id": r["client_id"],
                    "client_name": r["client_name"] or "",
                    # De donde vino decide si se puede reprogramar desde aca y
                    # de que color va la barra del chip.
                    "origen": _origen(r["calendar_event_id"]),
                    "duration_min": int(
                        _meeting_duration({"start_at": r["start_at"],
                                           "end_at": r["end_at"]}).total_seconds() // 60),
                })
            return jsonify({"events": events})
        finally:
            conn.close()

    # POST — save meeting to DB only (no Google Calendar)
    data = request.get_json() or {}
    title = (data.get("title") or "").strip()
    date = data.get("date", "")
    time = data.get("time", "")
    duration_min = int(data.get("duration_min") or 60)
    meet_link = (data.get("meet_link") or "").strip()
    description = (data.get("description") or "").strip()
    client_id = data.get("client_id")

    if not title or not date or not time:
        return jsonify({"ok": False, "error": "title, date y time requeridos"})
    # meetings.client_id es NOT NULL: sin cliente no hay reunion que guardar.
    # Antes se devolvia ok:true sin guardar nada y la reunion desaparecia.
    if not client_id:
        return jsonify({"ok": False, "error": "Eligi un cliente para la reunion"})

    try:
        start_dt = datetime.datetime.fromisoformat(f"{date}T{time}:00")
        end_dt = start_dt + datetime.timedelta(minutes=duration_min)
        db = _db()

        meeting_id = create_meeting(
            db, int(client_id),
            title=title,
            start_at=start_dt.isoformat(),
            end_at=end_dt.isoformat(),
            meet_link=meet_link,
            status="scheduled",
        )
        from database import update_business
        update_business(db, int(client_id), crm_status="reunion_agendada")
        client = get_business(db, int(client_id)) or {}
        log_activity(db, session.get("user_name", "sistema"), "meeting_scheduled",
                     "lead", int(client_id), client.get("name", ""), title,
                     user_id=session.get("user_id"))
        uids = _contributors(db, int(client_id), session.get("user_id"))
        increment_task_progress(db, uids, "reuniones_agendadas",
                                lead_id=int(client_id), lead_name=client.get("name", ""))

        return jsonify({"ok": True, "meeting_id": meeting_id, "meet_url": meet_link, "event_id": None})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@calendar_bp.route("/api/calendar/clients/<int:client_id>/meetings", methods=["GET"])
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
        status="completed",
    )

    # Despues de una reunion el lead avanza a 'reunion_hecha' solo. Esto NO es
    # la generacion de presupuesto —esa se saco el 28-8-2026 porque no se usaba,
    # 3 presupuestos generados contra 151 reuniones— sino el unico lugar donde
    # el sistema mueve un estado por su cuenta a partir de algo que paso.
    #
    # Con la generacion se fue tambien lo unico que ponia 'presupuesto_enviado'
    # automaticamente. Ese estado, que dispara la secuencia de recuperacion,
    # ahora depende del color violeta de la planilla
    # (services/planilla_semaforo.py).
    try:
        from database import get_meeting as _get_meeting, get_business, update_business
        meeting_record = _get_meeting(_db(), meeting_id)
        if meeting_record and meeting_record.get("client_id"):
            cid = meeting_record["client_id"]
            # No pisa a quien ya esta mas adelante.
            _ANTES_DE_LA_REUNION = {
                "sin_contactar", "interesado", "contactado",
                "reunion_agendada", "llamar_despues",
            }
            biz = get_business(_db(), cid)
            if biz and biz.get("crm_status") in _ANTES_DE_LA_REUNION:
                update_business(_db(), cid, crm_status="reunion_hecha")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            f"No se pudo marcar reunion_hecha para la reunion {meeting_id}: {e}")

    return jsonify({"ok": True, "summary": result})


@calendar_bp.route("/api/calendar/meetings/<int:meeting_id>/recall-transcript", methods=["GET"])
def api_recall_transcript(meeting_id):
    import os, requests
    meeting = get_meeting(_db(), meeting_id)
    if not meeting:
        return jsonify({"ok": False, "error": "Reunión no encontrada"}), 404

    bot_id = meeting.get("recall_bot_id")
    if not bot_id:
        return jsonify({"ok": False, "error": "Esta reunión no tiene bot de Recall asignado"})

    api_key = os.environ.get("RECALL_API_KEY", "")
    if not api_key:
        return jsonify({"ok": False, "error": "RECALL_API_KEY no configurada"})

    headers = {"Authorization": f"Token {api_key}"}

    # Check bot status first
    try:
        status_r = requests.get(
            f"https://us-east-1.recall.ai/api/v1/bot/{bot_id}/",
            headers=headers, timeout=10,
        )
        status_r.raise_for_status()
        status_data = status_r.json()
        status_changes = status_data.get("status_changes") or []
        latest = status_changes[-1]["code"] if status_changes else "unknown"
        if latest not in ("call_ended", "done", "recording_done"):
            return jsonify({"ok": False, "error": f"La reunión aún no terminó (estado: {latest})"})
    except Exception as e:
        return jsonify({"ok": False, "error": f"No se pudo consultar el bot: {e}"})

    # Fetch transcript
    try:
        tr_r = requests.get(
            f"https://us-east-1.recall.ai/api/v1/bot/{bot_id}/transcript/",
            headers=headers, timeout=15,
        )
        tr_r.raise_for_status()
        segments = tr_r.json()
    except Exception as e:
        return jsonify({"ok": False, "error": f"Error al obtener la transcripción: {e}"})

    if not segments:
        return jsonify({"ok": False, "error": "La transcripción está vacía todavía, esperá unos minutos."})

    lines = []
    for seg in segments:
        speaker = seg.get("speaker") or "Participante"
        words = seg.get("words") or []
        text = " ".join(w.get("text", "") for w in words).strip()
        if text:
            lines.append(f"{speaker}: {text}")
    transcript_text = "\n".join(lines)

    return jsonify({"ok": True, "transcript": transcript_text})


@calendar_bp.route("/api/calendar/meetings/<int:meeting_id>/fetch-transcript", methods=["GET"])
def api_fetch_transcript(meeting_id):
    meeting = get_meeting(_db(), meeting_id)
    if not meeting:
        return jsonify({"ok": False, "error": "Reunión no encontrada"}), 404

    cal_event_id = meeting.get("calendar_event_id")
    if not cal_event_id:
        return jsonify({"ok": False, "error": "Reunión sin evento de Calendar asociado"})

    cal_service, err = _get_calendar_service()
    if err:
        return jsonify({"ok": False, "error": err})

    drive_service, derr = _get_drive_service()
    if derr:
        return jsonify({"ok": False, "error": derr})

    try:
        event = cal_service.events().get(calendarId="primary", eventId=cal_event_id).execute()
    except Exception as e:
        return jsonify({"ok": False, "error": f"No se pudo obtener el evento: {e}"})

    # 1. Buscar transcript en los attachments del evento de Calendar
    file_id = None
    for att in event.get("attachments", []):
        title = (att.get("title") or "").lower()
        if "transcript" in title or "transcripci" in title:
            file_id = att.get("fileId")
            break

    # 2. Si no está en attachments, buscar en Drive por nombre
    if not file_id:
        event_title = (event.get("summary") or "").replace("'", "\\'")
        try:
            q = (
                f"(name contains '{event_title}' or name contains 'Transcript')"
                " and mimeType = 'application/vnd.google-apps.document'"
                " and trashed = false"
            )
            results = drive_service.files().list(
                q=q,
                spaces="drive",
                fields="files(id,name,createdTime)",
                orderBy="createdTime desc",
                pageSize=5,
            ).execute()
            files = results.get("files", [])
            if files:
                file_id = files[0]["id"]
        except Exception:
            pass

    if not file_id:
        return jsonify({
            "ok": False,
            "error": "No se encontró la transcripción en Drive. "
                     "Verificá que la transcripción esté habilitada en Meet y que la reunión haya finalizado."
        })

    try:
        content = drive_service.files().export(
            fileId=file_id,
            mimeType="text/plain",
        ).execute()
        text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
        return jsonify({"ok": True, "transcript": text})
    except Exception as e:
        return jsonify({"ok": False, "error": f"No se pudo leer el archivo: {e}"})


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


def _es_uri_de_calendly(event_id) -> bool:
    """routes/calendly.py guarda la URI de Calendly en `calendar_event_id`.

    Comparte columna con el eventId de Google pero no es lo mismo, y pedirle a
    Google un patch con una URI falla siempre. Sin este chequeo el usuario ve
    "Google Calendar rechazó el cambio", que no le dice que lo que tiene que
    hacer es reprogramarla en Calendly.
    """
    return str(event_id or "").startswith("http")


def _origen(event_id) -> str:
    """De donde vino la reunion: 'crm', 'google' o 'calendly'.

    Las tres se reprograman distinto, asi que la vista necesita distinguirlas
    para no ofrecer un boton que va a fallar.
    """
    if not event_id:
        return "crm"
    return "calendly" if _es_uri_de_calendly(event_id) else "google"


def _meeting_duration(meeting: dict) -> datetime.timedelta:
    """Cuanto dura la reunion, para conservarlo al moverla.

    Los eventos importados a veces no tienen `end_at`; ahi asumimos una hora,
    que es la duracion por defecto del formulario de reunion del CRM.
    """
    try:
        start = datetime.datetime.fromisoformat(meeting.get("start_at") or "")
        end = datetime.datetime.fromisoformat(meeting.get("end_at") or "")
    except (TypeError, ValueError):
        return datetime.timedelta(hours=1)
    delta = end - start
    return delta if delta > datetime.timedelta(0) else datetime.timedelta(hours=1)


@calendar_bp.route("/api/calendar/meetings/<int:meeting_id>", methods=["PATCH"])
def api_reschedule_meeting(meeting_id):
    """Mueve una reunion a otra fecha/hora — es lo que dispara el arrastre.

    Google va primero a proposito. Si la reunion tiene evento en Calendar y no
    lo podemos mover, no tocamos la base: que el arrastre no funcione es
    molesto, pero que el CRM diga una hora y el invitado tenga otra en su
    calendario es como se pierde una reunion.
    """
    db = _db()
    meeting = get_meeting(db, meeting_id)
    if not meeting:
        return jsonify({"ok": False, "error": "Reunión no encontrada"}), 404

    data = request.get_json(silent=True) or {}
    date = (data.get("date") or "").strip()
    time = (data.get("time") or "").strip()
    try:
        start_dt = datetime.datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    except ValueError:
        return jsonify({"ok": False, "error": "Fecha u hora inválidas"}), 400

    # Cambiar cuanto dura es opcional: si no viene, se conserva la que tenia.
    duracion = data.get("duration_min")
    if duracion in (None, ""):
        largo = _meeting_duration(meeting)
    else:
        try:
            minutos = int(duracion)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Duración inválida"}), 400
        if minutos < 1:
            return jsonify({"ok": False, "error": "Duración inválida"}), 400
        largo = datetime.timedelta(minutes=minutos)

    end_dt = start_dt + largo

    # Renombrar tambien es opcional. Un titulo en blanco NO borra el que estaba:
    # el modal manda el campo siempre, y vaciarlo sin querer dejaria la reunion
    # sin nombre en el calendario del invitado.
    titulo_nuevo = (data.get("title") or "").strip()
    titulo = titulo_nuevo or (meeting.get("title") or "")

    cal_event_id = meeting.get("calendar_event_id")
    if _es_uri_de_calendly(cal_event_id):
        return jsonify({
            "ok": False,
            "error": "Esta reunión la creó Calendly. Reprogramala desde Calendly "
                     "y el cambio baja solo en el próximo sync.",
        }), 409

    if cal_event_id:
        service, err = _get_calendar_service()
        if err or not service:
            return jsonify({
                "ok": False,
                "error": f"No se pudo mover en Google Calendar: {err or 'sin servicio'}",
            }), 502
        try:
            service.events().patch(
                calendarId="primary",
                eventId=cal_event_id,
                body={
                    "summary": titulo,
                    "start": {"dateTime": start_dt.isoformat(), "timeZone": MVD.zone},
                    "end": {"dateTime": end_dt.isoformat(), "timeZone": MVD.zone},
                },
                sendUpdates="all",
            ).execute()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                f"No se pudo reprogramar el evento {cal_event_id} en Calendar: {e}")
            return jsonify({
                "ok": False,
                "error": f"Google Calendar rechazó el cambio: {e}",
            }), 502

    update_meeting(db, meeting_id, title=titulo,
                   start_at=start_dt.isoformat(), end_at=end_dt.isoformat())

    client_id = meeting.get("client_id")
    client = get_business(db, int(client_id)) if client_id else {}
    log_activity(db, session.get("user_name", "sistema"), "meeting_rescheduled",
                 "lead", client_id, (client or {}).get("name", ""),
                 f"{titulo or 'Reunión'} → {date} {time}",
                 user_id=session.get("user_id"))

    return jsonify({"ok": True,
                    "title": titulo,
                    "start_at": start_dt.isoformat(),
                    "end_at": end_dt.isoformat()})
