"""Google Calendar routes."""

import datetime
import json
import uuid

import pytz
from flask import Blueprint, current_app, jsonify, request, session

from database import (
    create_meeting,
    create_reunion_asunto,
    delete_meeting,
    delete_reunion_asunto,
    get_business,
    get_lead_contributor_ids,
    get_meeting,
    get_meetings_for_client,
    get_reunion_asunto,
    increment_task_progress,
    listar_reuniones_asunto,
    log_activity,
    update_meeting,
    update_reunion_asunto,
)
from services import gcal_eventos as gce
from services import recurrencia as rec

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
    if biz.get("crm_status") != "demo_agendada":
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
    from services.google_api import get_service
    creds, err = _build_creds()
    if err:
        return None, err
    try:
        return get_service("calendar", "v3", creds, account="gcal"), None
    except ImportError:
        return None, "google-api-python-client no instalado"


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
    from services.google_api import get_service
    creds, err = _build_creds()
    if err:
        return None, err
    try:
        return get_service("drive", "v3", creds, account="gcal"), None
    except ImportError:
        return None, "google-api-python-client no instalado"


def _db() -> str:
    return current_app.config["DB_PATH"]


def _contributors(db: str, lead_id: int, current_uid: int | None) -> list[int]:
    ids = set(get_lead_contributor_ids(db, lead_id))
    if current_uid:
        ids.add(current_uid)
    return list(ids)


def _json_lista(texto) -> list:
    try:
        valor = json.loads(texto) if texto else []
    except (TypeError, ValueError):
        return []
    return valor if isinstance(valor, list) else []


def _eventos_locales(db: str, start: str, end: str) -> list[dict]:
    """Todo lo que el calendario dibuja entre `start` y `end`, ya expandido.

    Reuniones con cliente (`meetings`) y de otro asunto (`reuniones_asunto`).
    Las que se repiten se devuelven una vez por ocurrencia, con id
    `<id>@<fecha original>` para que el editor y el arrastre sepan cual es.
    Sin rango no se puede expandir una serie sin fin: va solo la primera.
    """
    import sqlite3 as _sq
    conn = _sq.connect(db); conn.row_factory = _sq.Row
    try:
        filas = [dict(r) for r in conn.execute("""
            SELECT m.id, m.title, m.start_at, m.end_at, m.meet_link, m.status,
                   m.calendar_event_id, b.name as client_name, m.client_id,
                   m.invitados, m.repeticion, m.excepciones, m.description,
                   m.google_event_id, m.google_sync, m.google_error
            FROM meetings m
            LEFT JOIN businesses b ON m.client_id = b.id
            WHERE m.status != 'canceled'
              AND ((COALESCE(m.repeticion, '') = ''
                    AND (? = '' OR SUBSTR(m.start_at, 1, 10) >= ?)
                    AND (? = '' OR SUBSTR(m.start_at, 1, 10) <= ?))
                OR (COALESCE(m.repeticion, '') != ''
                    AND (? = '' OR SUBSTR(m.start_at, 1, 10) <= ?)))
            ORDER BY m.start_at ASC
        """, (start, start, end, end, end, end)).fetchall()]
    finally:
        conn.close()

    eventos = []

    def agregar(fila, tipo, base_id, extra):
        sa = fila["start_at"] or ""
        base = {
            "reunion_id": fila["id"],
            "tipo": tipo,
            "title": fila["title"] or fila.get("client_name") or "Reunión",
            "meeting_url": fila["meet_link"] or "",
            "client_id": fila.get("client_id"),
            "client_name": fila.get("client_name") or "",
            # De donde vino decide si se puede reprogramar desde aca y
            # de que color va la barra del chip.
            "origen": _origen(fila.get("calendar_event_id")),
            "invitados": _json_lista(fila.get("invitados")),
            "description": fila.get("description") or "",
            # Como quedo en Google Calendar: 'ok', 'error' (con el motivo) o ''
            # si no se intento. Con 'error' la pantalla ofrece "Reintentar".
            "google": {"estado": fila.get("google_sync") or "",
                       "error": fila.get("google_error") or ""},
        }
        regla = rec.regla_de(fila)
        if regla and start and end:
            for oc in rec.ocurrencias(fila, start, end):
                ev = dict(base, **extra)
                ev.update(id=f"{base_id}@{oc['ocurrencia']}", serie=True,
                          ocurrencia=oc["ocurrencia"], repeticion=regla,
                          title=oc["title"] or base["title"], date=oc["date"],
                          time=oc["time"], duration_min=oc["duration_min"])
                eventos.append(ev)
            return
        ev = dict(base, **extra)
        ev.update(id=str(base_id), serie=bool(regla), ocurrencia=sa[:10] if regla else "",
                  repeticion=regla, date=sa[:10] if sa else "",
                  time=sa[11:16] if "T" in sa else "",
                  duration_min=int(_meeting_duration(fila).total_seconds() // 60))
        eventos.append(ev)

    for fila in filas:
        agregar(fila, "cliente", fila["id"], {})
    for fila in listar_reuniones_asunto(db, start, end):
        agregar(fila, "asunto", f"asunto-{fila['id']}", {})

    eventos.sort(key=lambda e: (e["date"], e["time"]))
    return eventos


def _sync_gcal_to_db(db: str, start: str, end: str) -> None:
    """Pull Google Calendar events for the given date range and upsert into meetings table."""
    import re, sqlite3 as _sq
    from datetime import timezone as _tz
    service, err = _get_calendar_service()
    if err or not service:
        return
    # Lo que ya esta en el CRM y no es una fila suelta de `meetings`: las
    # ocurrencias de una serie y las reuniones de otro asunto. Si alguien crea
    # la misma reunion tambien en Google (por ejemplo, para que les llegue la
    # invitacion a los invitados), el sync la importaba como reunion con un
    # lead nuevo inventado a partir del primer invitado, una vez por ocurrencia.
    ya_en_el_crm = {
        (e["date"], e["time"], (e["title"] or "").strip().casefold())
        for e in _eventos_locales(db, start, end)
        if e["tipo"] == "asunto" or e["serie"]
    }
    # Los eventos que creo el propio CRM, por id: el evento y sus instancias.
    ids_del_crm = _ids_de_google_del_crm(db)
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
            # Un evento que creo el CRM ya esta en el CRM. Importarlo lo
            # duplicaria y le inventaria un lead al primer invitado.
            if gce.es_del_crm(ev, ids_del_crm):
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

            if (start_at[:10], start_at[11:16], summary.strip().casefold()) in ya_en_el_crm:
                continue

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
                    (name, invitee_email or None, "demo_agendada", "calendly_gcal"),
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


# ── Google Calendar: las reuniones que crea el CRM ───────────────────────────
# La base del CRM manda. Crear y editar: primero la base, despues Google; si
# Google falla, la reunion queda marcada "No sincronizada" para reintentar desde
# la pantalla (nunca sola, en loop). Borrar: primero Google, porque borrar solo
# en el CRM deja el evento vivo en Google y el import lo traeria de vuelta.

def _ids_de_google_del_crm(db: str) -> set:
    import sqlite3 as _sq
    conn = _sq.connect(db)
    try:
        return {r[0] for r in conn.execute(
            "SELECT google_event_id FROM meetings WHERE COALESCE(google_event_id, '') != '' "
            "UNION SELECT google_event_id FROM reuniones_asunto "
            "WHERE COALESCE(google_event_id, '') != ''")}
    finally:
        conn.close()


def _fila(db: str, tipo: str, rid: int):
    return get_meeting(db, rid) if tipo == "cliente" else get_reunion_asunto(db, rid)


def _guardar(db: str, tipo: str, rid: int, **campos) -> None:
    (update_meeting if tipo == "cliente" else update_reunion_asunto)(db, rid, **campos)


def _email_cliente(db: str, fila: dict, tipo: str):
    if tipo != "cliente" or not fila.get("client_id"):
        return None
    return (get_business(db, int(fila["client_id"])) or {}).get("email") or None


def _servicio_para_escribir():
    service, err = _get_calendar_service()
    if err or not service:
        return None, gce.NO_CONECTADO
    if gce.permiso_de_escritura(service) is False:
        return None, gce.SIN_PERMISO
    return service, None


def _log_google(tipo, rid, e) -> None:
    import logging
    logging.getLogger(__name__).warning(f"Google Calendar ({tipo} {rid}): {e}")


def _en_google(db: str, tipo: str, rid: int, operacion) -> dict:
    """Corre `operacion(service)` y deja anotado en la reunion como quedo."""
    service, error = _servicio_para_escribir()
    if not error:
        try:
            operacion(service)
            _guardar(db, tipo, rid, google_sync="ok", google_error=None)
            return {"estado": "ok", "error": ""}
        except Exception as e:  # noqa: BLE001 - cualquier falla se anota igual
            _log_google(tipo, rid, e)
            error = gce.mensaje_error(e)
    _guardar(db, tipo, rid, google_sync="error", google_error=error)
    return {"estado": "error", "error": error}


def _google_o_error(tipo: str, rid: int, operacion):
    """Para borrar. None si quedo hecho (o Google ya no lo tenia); si no, el motivo."""
    service, error = _servicio_para_escribir()
    if error:
        return error
    try:
        operacion(service)
        return None
    except Exception as e:  # noqa: BLE001
        if gce.ya_no_existe(e):
            return None
        _log_google(tipo, rid, e)
        return gce.mensaje_error(e)


def _subir_a_google(db: str, tipo: str, rid: int, *, reintento: bool = False) -> dict:
    """Crea el evento (o lo pone al dia, si ya existe) con todo lo que tiene la
    reunion en el CRM, incluidas las ocurrencias borradas o movidas.

    El id del evento se elige ANTES de pedirselo a Google y se guarda: si la
    respuesta se pierde, el reintento usa el mismo id y Google contesta 409 en
    vez de crear otro evento y mandar otra invitacion.
    """
    fila = _fila(db, tipo, rid)
    if not fila.get("google_event_id"):
        _guardar(db, tipo, rid, google_event_id=gce.nuevo_id(tipo, rid))
        fila = _fila(db, tipo, rid)
    gid = fila["google_event_id"]
    email = _email_cliente(db, fila, tipo)

    def operacion(service):
        creado = None
        if reintento and fila.get("google_sync") == "ok":
            gce.actualizar(service, gid, fila, email)
        else:
            try:
                creado = gce.crear(service, fila, email, event_id=gid)
            except Exception as e:  # noqa: BLE001
                if gce.estado_http(e) != 409:
                    raise
                gce.actualizar(service, gid, fila, email)   # ya estaba creado
        if creado and creado.get("hangoutLink") and not (fila.get("meet_link") or "").strip():
            _guardar(db, tipo, rid, meet_link=creado["hangoutLink"])
        if reintento:
            gce.aplicar_excepciones(service, gid, fila)

    return _en_google(db, tipo, rid, operacion)


def _editar_en_google(db: str, tipo: str, rid: int, antes: dict, *, alcance=None,
                      ocurrencia=None, cambios=None, invitados_cambiaron=False,
                      nueva_id=None) -> dict:
    """Lleva a Google lo que ya se guardo en la base. `antes` es la fila como
    estaba: la instancia de "solo esta" se busca con la hora original."""
    resultado = {"estado": "", "error": ""}
    gid = antes.get("google_event_id")
    if gid:
        fila = _fila(db, tipo, rid)
        email = _email_cliente(db, fila, tipo)

        def operacion(service):
            if alcance == "esta":
                gce.mover_instancia(service, gid, antes, ocurrencia, cambios)
                if invitados_cambiaron:
                    gce.actualizar(service, gid, fila, email)
            elif alcance == "siguientes" and nueva_id:
                gce.cambiar_regla(service, gid, fila)
            else:
                gce.actualizar(service, gid, fila, email)

        resultado = _en_google(db, tipo, rid, operacion)
    if nueva_id and gce.creacion_activada():
        nuevo = _subir_a_google(db, tipo, nueva_id)
        if nuevo["estado"] == "error" and resultado["estado"] != "error":
            resultado = nuevo
    return resultado


def _borrar_en_google(tipo: str, rid: int, fila: dict, alcance: str, ocurrencia: str,
                      plan: dict | None):
    """None si Google quedo al dia (o la reunion no esta en Google); si no, el motivo."""
    gid = fila.get("google_event_id")
    if not gid:
        return None
    if plan and not plan["borrar"]:
        cortada = dict(fila, **plan["actualizar"])
        if alcance == "esta":
            return _google_o_error(tipo, rid, lambda s: gce.cancelar_instancia(s, gid, fila, ocurrencia))
        return _google_o_error(tipo, rid, lambda s: gce.cambiar_regla(s, gid, cortada))
    return _google_o_error(tipo, rid, lambda s: gce.borrar(s, gid))


def _crear_si_corresponde(db: str, tipo: str, rid: int) -> dict:
    """Al crear: si GCAL_CREAR_EVENTOS esta apagado o no hay credenciales, no se
    intenta y la reunion queda solo en el CRM, como hasta ahora."""
    if not gce.creacion_activada():
        return {"estado": "", "error": ""}
    return _subir_a_google(db, tipo, rid)


def _no_se_borro(error: str):
    return jsonify({"ok": False, "error": f"No se borró: Google Calendar no respondió bien "
                                          f"({error}). Probá de nuevo en un rato."}), 502


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

        return jsonify({"events": _eventos_locales(db, start, end)})

    # POST — se guarda en la base del CRM y despues se crea el evento en Google
    # Calendar, que les manda la invitacion al cliente y a los invitados
    # (services/gcal_eventos.py). Si Google falla, la reunion queda igual.
    data = request.get_json() or {}
    tipo = data.get("tipo") or "cliente"
    title = (data.get("title") or "").strip()
    date = data.get("date", "")
    time = data.get("time", "")
    meet_link = (data.get("meet_link") or "").strip()
    description = (data.get("description") or "").strip()
    client_id = data.get("client_id")

    if tipo not in ("cliente", "asunto"):
        return jsonify({"ok": False, "error": "Tipo de reunión desconocido"})
    if tipo == "asunto" and not title:
        return jsonify({"ok": False, "error": "Escribí de qué es la reunión (el asunto)"})
    if not title or not date or not time:
        return jsonify({"ok": False, "error": "title, date y time requeridos"})
    # meetings.client_id es NOT NULL: sin cliente no hay reunion que guardar.
    # Antes se devolvia ok:true sin guardar nada y la reunion desaparecia.
    if tipo == "cliente" and not client_id:
        return jsonify({"ok": False, "error": "Eligi un cliente para la reunion"})
    try:
        duration_min = int(data.get("duration_min") or 60)
        start_dt = datetime.datetime.fromisoformat(f"{date}T{time}:00")
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Fecha, hora o duración inválidas"})
    if duration_min < 1:
        return jsonify({"ok": False, "error": "Fecha, hora o duración inválidas"})
    invitados, error = rec.validar_invitados(data.get("invitados"))
    if error:
        return jsonify({"ok": False, "error": error})
    regla, error = rec.validar_regla(data.get("repeticion"), start_dt.date())
    if error:
        return jsonify({"ok": False, "error": error})

    extra = {
        "description": description or None,
        "invitados": json.dumps(invitados) if invitados else None,
        "repeticion": json.dumps(regla) if regla else None,
    }

    try:
        end_dt = start_dt + datetime.timedelta(minutes=duration_min)
        db = _db()

        if tipo == "asunto":
            # Ni estado del lead, ni actividad de lead, ni metas de "reuniones
            # agendadas": no es una reunion de ventas.
            asunto_id = create_reunion_asunto(
                db, title=title, start_at=start_dt.isoformat(),
                end_at=end_dt.isoformat(), meet_link=meet_link, status="scheduled",
                created_by=session.get("user_name", "sistema"), **extra)
            log_activity(db, session.get("user_name", "sistema"), "asunto_agendado",
                         "asunto", asunto_id, title, f"{date} {time}",
                         user_id=session.get("user_id"))
            google = _crear_si_corresponde(db, "asunto", asunto_id)
            fila = get_reunion_asunto(db, asunto_id)
            return jsonify({"ok": True, "asunto_id": asunto_id,
                            "id": f"asunto-{asunto_id}", "meet_url": fila.get("meet_link") or "",
                            "event_id": fila.get("google_event_id"), "google": google})

        meeting_id = create_meeting(
            db, int(client_id),
            title=title,
            start_at=start_dt.isoformat(),
            end_at=end_dt.isoformat(),
            meet_link=meet_link,
            status="scheduled",
            **extra,
        )
        from database import update_business
        update_business(db, int(client_id), crm_status="demo_agendada")
        client = get_business(db, int(client_id)) or {}
        log_activity(db, session.get("user_name", "sistema"), "meeting_scheduled",
                     "lead", int(client_id), client.get("name", ""), title,
                     user_id=session.get("user_id"))
        uids = _contributors(db, int(client_id), session.get("user_id"))
        increment_task_progress(db, uids, "reuniones_agendadas",
                                lead_id=int(client_id), lead_name=client.get("name", ""))

        google = _crear_si_corresponde(db, "cliente", meeting_id)
        fila = get_meeting(db, meeting_id)
        return jsonify({"ok": True, "meeting_id": meeting_id,
                        "meet_url": fila.get("meet_link") or "",
                        "event_id": fila.get("google_event_id"), "google": google})
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

    # Despues de una reunion el lead avanza a 'demo_1' solo. Esto NO es
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
                "demo_agendada", "llamar_despues",
            }
            biz = get_business(_db(), cid)
            if biz and biz.get("crm_status") in _ANTES_DE_LA_REUNION:
                update_business(_db(), cid, crm_status="demo_1")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            f"No se pudo marcar demo_1 para la reunion {meeting_id}: {e}")

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

    # Una ocurrencia de una serie: "solo esta" o "esta y las siguientes" se
    # resuelven en la fila. Sin alcance (el panel del cliente) se borra todo.
    alcance = request.args.get("alcance") or "todas"
    ocurrencia = request.args.get("ocurrencia", "")
    plan = None
    if rec.regla_de(meeting) and alcance != "todas":
        try:
            plan = rec.borrar(meeting, ocurrencia, alcance)
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
    error = _borrar_en_google("cliente", meeting_id, meeting, alcance, ocurrencia, plan)
    if error:
        return _no_se_borro(error)
    if plan and not plan["borrar"]:
        update_meeting(_db(), meeting_id, **plan["actualizar"])
        return jsonify({"ok": True, "borrada": False})

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


_ALCANCE_TEXTO = {"esta": "solo esta", "siguientes": "esta y las siguientes",
                  "todas": "todas"}


def _cambios_del_pedido(data: dict, fila: dict):
    """Fecha, hora, titulo, duracion e invitados de un PATCH, validados.

    Devuelve (cambios, invitados, error). `invitados` es False si el pedido
    no los trae (no se tocan), o el JSON a guardar. `error` es la respuesta
    lista para devolver.
    """
    date = (data.get("date") or "").strip()
    time = (data.get("time") or "").strip()
    try:
        start_dt = datetime.datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None, False, (jsonify({"ok": False, "error": "Fecha u hora inválidas"}), 400)
    duracion = data.get("duration_min")
    if duracion in (None, ""):
        minutos = rec.duracion_min(fila)
    else:
        try:
            minutos = int(duracion)
        except (TypeError, ValueError):
            minutos = 0
        if minutos < 1:
            return None, False, (jsonify({"ok": False, "error": "Duración inválida"}), 400)
    invitados = False
    if "invitados" in data:
        lista, error = rec.validar_invitados(data.get("invitados"))
        if error:
            return None, False, (jsonify({"ok": False, "error": error}), 400)
        invitados = json.dumps(lista) if lista else None
    return ({"date": date, "time": start_dt.strftime("%H:%M"), "start_dt": start_dt,
             "title": (data.get("title") or "").strip(), "duration_min": minutos},
            invitados, None)


def _plan_de_serie(fila: dict, data: dict, cambios: dict):
    """Que cambia en la serie segun el alcance elegido. Sin `ocurrencia` (una
    pestaña vieja) se toma la serie entera desde su primera reunion."""
    ocurrencia = (data.get("ocurrencia") or "").strip()
    alcance = data.get("alcance") or ("esta" if ocurrencia else "todas")
    ocurrencia = ocurrencia or str(fila.get("start_at") or "")[:10]
    try:
        return rec.editar(fila, ocurrencia, alcance, cambios), alcance, None
    except ValueError as e:
        return None, alcance, (jsonify({"ok": False, "error": str(e)}), 400)


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

    # Una reunion que se repite se crea en el CRM (nunca se importa): se
    # resuelve en la base segun el alcance y despues se lleva a Google.
    if rec.regla_de(meeting):
        cambios, invitados, error = _cambios_del_pedido(data, meeting)
        if error:
            return error
        plan, alcance, error = _plan_de_serie(meeting, data, cambios)
        if error:
            return error
        ocurrencia = (data.get("ocurrencia") or "").strip() or str(meeting.get("start_at") or "")[:10]
        actualizar = dict(plan["actualizar"])
        if invitados is not False and not plan["nueva"]:
            actualizar["invitados"] = invitados
        update_meeting(db, meeting_id, **actualizar)
        nueva_id = None
        if plan["nueva"]:
            nueva_id = create_meeting(
                db, meeting["client_id"], meet_link=meeting.get("meet_link"),
                description=meeting.get("description"), status="scheduled",
                invitados=meeting.get("invitados") if invitados is False else invitados,
                **plan["nueva"])
        client = get_business(db, int(meeting["client_id"])) or {}
        log_activity(db, session.get("user_name", "sistema"), "meeting_rescheduled",
                     "lead", meeting["client_id"], client.get("name", ""),
                     f"{cambios['title'] or meeting.get('title') or 'Reunión'} → "
                     f"{cambios['date']} {cambios['time']} ({_ALCANCE_TEXTO[alcance]})",
                     user_id=session.get("user_id"))
        google = _editar_en_google(db, "cliente", meeting_id, meeting, alcance=alcance,
                                   ocurrencia=ocurrencia, cambios=cambios,
                                   invitados_cambiaron=invitados is not False,
                                   nueva_id=nueva_id)
        return jsonify({"ok": True, "nueva_id": nueva_id, "google": google})

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

    # Los invitados son opcionales en el pedido y quedan solo en el CRM.
    extra = {}
    if "invitados" in data:
        lista, error = rec.validar_invitados(data.get("invitados"))
        if error:
            return jsonify({"ok": False, "error": error}), 400
        extra["invitados"] = json.dumps(lista) if lista else None

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
                   start_at=start_dt.isoformat(), end_at=end_dt.isoformat(), **extra)

    client_id = meeting.get("client_id")
    client = get_business(db, int(client_id)) if client_id else {}
    log_activity(db, session.get("user_name", "sistema"), "meeting_rescheduled",
                 "lead", client_id, (client or {}).get("name", ""),
                 f"{titulo or 'Reunión'} → {date} {time}",
                 user_id=session.get("user_id"))

    # Las que creo el CRM en Google (las importadas ya se movieron arriba).
    google = _editar_en_google(db, "cliente", meeting_id, meeting,
                               invitados_cambiaron="invitados" in extra)

    return jsonify({"ok": True,
                    "title": titulo,
                    "start_at": start_dt.isoformat(),
                    "end_at": end_dt.isoformat(),
                    "google": google})


# ── Reuniones de otro asunto ─────────────────────────────────────────────────
# Sin cliente. En Google van igual que las de cliente, con sus invitados.

@calendar_bp.route("/api/calendar/asuntos/<int:asunto_id>", methods=["GET"])
def api_get_asunto(asunto_id):
    fila = get_reunion_asunto(_db(), asunto_id)
    if not fila:
        return jsonify({"ok": False, "error": "Reunión no encontrada"}), 404
    return jsonify(fila)


@calendar_bp.route("/api/calendar/asuntos/<int:asunto_id>", methods=["PATCH"])
def api_editar_asunto(asunto_id):
    db = _db()
    fila = get_reunion_asunto(db, asunto_id)
    if not fila:
        return jsonify({"ok": False, "error": "Reunión no encontrada"}), 404
    data = request.get_json(silent=True) or {}
    cambios, invitados, error = _cambios_del_pedido(data, fila)
    if error:
        return error

    alcance = None
    if rec.regla_de(fila):
        plan, alcance, error = _plan_de_serie(fila, data, cambios)
        if error:
            return error
        ocurrencia = (data.get("ocurrencia") or "").strip() or str(fila.get("start_at") or "")[:10]
        actualizar = dict(plan["actualizar"])
        if invitados is not False and not plan["nueva"]:
            actualizar["invitados"] = invitados
        update_reunion_asunto(db, asunto_id, **actualizar)
        nueva_id = None
        if plan["nueva"]:
            nueva_id = create_reunion_asunto(
                db, meet_link=fila.get("meet_link"), description=fila.get("description"),
                invitados=fila.get("invitados") if invitados is False else invitados,
                status="scheduled", created_by=session.get("user_name", "sistema"),
                **plan["nueva"])
        google = _editar_en_google(db, "asunto", asunto_id, fila, alcance=alcance,
                                   ocurrencia=ocurrencia, cambios=cambios,
                                   invitados_cambiaron=invitados is not False,
                                   nueva_id=nueva_id)
    else:
        campos = {
            "title": cambios["title"] or fila.get("title") or "",
            "start_at": cambios["start_dt"].isoformat(),
            "end_at": (cambios["start_dt"]
                       + datetime.timedelta(minutes=cambios["duration_min"])).isoformat(),
        }
        if invitados is not False:
            campos["invitados"] = invitados
        update_reunion_asunto(db, asunto_id, **campos)
        google = _editar_en_google(db, "asunto", asunto_id, fila,
                                   invitados_cambiaron=invitados is not False)

    log_activity(db, session.get("user_name", "sistema"), "asunto_movido", "asunto",
                 asunto_id, cambios["title"] or fila.get("title") or "",
                 f"{cambios['date']} {cambios['time']}"
                 + (f" ({_ALCANCE_TEXTO[alcance]})" if alcance else ""),
                 user_id=session.get("user_id"))
    return jsonify({"ok": True, "google": google})


@calendar_bp.route("/api/calendar/asuntos/<int:asunto_id>", methods=["DELETE"])
def api_borrar_asunto(asunto_id):
    db = _db()
    fila = get_reunion_asunto(db, asunto_id)
    if not fila:
        return jsonify({"ok": False, "error": "Reunión no encontrada"}), 404
    alcance = request.args.get("alcance") or "todas"
    ocurrencia = request.args.get("ocurrencia", "")
    plan = None
    if rec.regla_de(fila) and alcance != "todas":
        try:
            plan = rec.borrar(fila, ocurrencia, alcance)
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
    error = _borrar_en_google("asunto", asunto_id, fila, alcance, ocurrencia, plan)
    if error:
        return _no_se_borro(error)
    if plan and not plan["borrar"]:
        update_reunion_asunto(db, asunto_id, **plan["actualizar"])
        return jsonify({"ok": True, "borrada": False})
    delete_reunion_asunto(db, asunto_id)
    return jsonify({"ok": True, "borrada": True})


# ── Reintentar en Google ─────────────────────────────────────────────────────
# Lo dispara el boton "Reintentar en Google" de una reunion "No sincronizada".
# Sube la reunion entera tal como esta en el CRM. Nunca corre sola.

def _reintentar(tipo: str, rid: int):
    db = _db()
    fila = _fila(db, tipo, rid)
    if not fila:
        return jsonify({"ok": False, "error": "Reunión no encontrada"}), 404
    if fila.get("calendar_event_id"):
        return jsonify({"ok": False, "error": "Esta reunión ya vive en Google o en Calendly."}), 400
    google = _subir_a_google(db, tipo, rid, reintento=True)
    ok = google["estado"] == "ok"
    return jsonify({"ok": ok, "google": google, "error": google["error"]}), (200 if ok else 502)


@calendar_bp.route("/api/calendar/meetings/<int:meeting_id>/google", methods=["POST"])
def api_reintentar_meeting_google(meeting_id):
    return _reintentar("cliente", meeting_id)


@calendar_bp.route("/api/calendar/asuntos/<int:asunto_id>/google", methods=["POST"])
def api_reintentar_asunto_google(asunto_id):
    return _reintentar("asunto", asunto_id)
