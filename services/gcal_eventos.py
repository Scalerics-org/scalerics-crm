"""Las reuniones del CRM, creadas y mantenidas en Google Calendar (Juan, 15/9).

"Si más adelante querés que el CRM vuelva a crear las reuniones en Google y
mande las invitaciones solo... Hacelo".

Historia, para no repetirla. El 1/6/2026 (`81f19fb`) se dejo de crear eventos
en Google: el calendario leia Google en vivo y la app de OAuth estaba "En
prueba", donde el refresh token vence a los 7 dias. Cuando vencia, el panel y
el alta de reuniones se caian enteros. Hoy la app esta publicada y el token no
vence (ver `routes/tokens.py`), y aca la base del CRM manda: si Google falla,
la reunion queda guardada igual y se marca para reintentar.

El riesgo real de volver es otro: el import de Google (`_sync_gcal_to_db`, que
volvio el 4/6) trae todo evento que no conoce y le inventa un lead al primer
invitado. Por eso cada reunion guarda `google_event_id` y el import saltea ese
id y sus instancias (`<id>_<fecha>`, o `recurringEventId`).

Conexion: OAuth de la cuenta de Scalerics (GCAL_CLIENT_ID, GCAL_CLIENT_SECRET,
GCAL_REFRESH_TOKEN), calendario "primary". No es un service account, asi que
Google si manda invitaciones a invitados de afuera. `sendUpdates="all"` en
cada escritura es lo que hace que Google mande el mail.

Horas: siempre 'AAAA-MM-DDTHH:MM:SS' de pared con timeZone America/Montevideo;
Google hace la cuenta. Lo unico que va en UTC es el UNTIL de la RRULE, que la
especificacion exige en UTC cuando el evento tiene zona.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import uuid

import pytz

from services import recurrencia as rec

MVD = pytz.timezone("America/Montevideo")
CALENDARIO = "primary"
SIN_PERMISO = "falta permiso de escritura en Google Calendar"
NO_CONECTADO = "Google Calendar no está conectado"

_ESCRITURA = {"https://www.googleapis.com/auth/calendar",
              "https://www.googleapis.com/auth/calendar.events"}
_APAGADO = {"off", "0", "false", "no"}
_BYDAY = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
_CREDENCIALES = ("GCAL_CLIENT_ID", "GCAL_CLIENT_SECRET", "GCAL_REFRESH_TOKEN")


# ── interruptor y permisos ───────────────────────────────────────────────────

def creacion_activada() -> bool:
    """GCAL_CREAR_EVENTOS=off apaga la creacion sin tocar codigo. Por defecto
    esta prendida, pero solo si hay credenciales de Google cargadas."""
    valor = (os.environ.get("GCAL_CREAR_EVENTOS") or "on").strip().lower()
    if valor in _APAGADO:
        return False
    return all(os.environ.get(k) for k in _CREDENCIALES)


def permiso_de_escritura(service) -> bool | None:
    """True / False si las credenciales ya dijeron que scopes les concedio
    Google (lo sabe despues del primer refresh); None si todavia no se sabe."""
    creds = getattr(getattr(service, "_http", None), "credentials", None)
    scopes = getattr(creds, "granted_scopes", None)
    if isinstance(scopes, str):
        scopes = scopes.split()
    if not isinstance(scopes, (list, tuple, set, frozenset)) or not scopes:
        return None
    return bool(_ESCRITURA & set(scopes))


def estado_http(exc) -> int | None:
    status = getattr(getattr(exc, "resp", None), "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def es_falta_de_permiso(exc) -> bool:
    texto = str(exc).lower()
    if "invalid_scope" in texto or "insufficient" in texto:
        return True
    return estado_http(exc) == 403 and ("permission" in texto or "scope" in texto)


def mensaje_error(exc) -> str:
    """Lo que se le muestra a la persona. Nunca el cuerpo crudo de Google."""
    if es_falta_de_permiso(exc):
        return SIN_PERMISO
    status = estado_http(exc)
    if status:
        return f"Google respondió con error {status}"
    return "no hubo respuesta de Google"


def ya_no_existe(exc) -> bool:
    return estado_http(exc) in (404, 410)


# ── el evento ────────────────────────────────────────────────────────────────

def _inicio(fila: dict) -> dt.datetime:
    return dt.datetime.fromisoformat(str(fila["start_at"]).replace(" ", "T")[:19])


def _cuando(momento: dt.datetime) -> dict:
    return {"dateTime": momento.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": MVD.zone}


def rrule(regla: dict, inicio: dt.datetime) -> str:
    """La regla del CRM como RRULE de Google. Semanal los viernes:
    'RRULE:FREQ=WEEKLY;BYDAY=FR'."""
    partes = {"diaria": ["FREQ=DAILY"], "semanal": ["FREQ=WEEKLY"],
              "quincenal": ["FREQ=WEEKLY", "INTERVAL=2"],
              "mensual": ["FREQ=MONTHLY"]}[regla["freq"]]
    partes = list(partes)
    if regla["freq"] in ("semanal", "quincenal"):
        dias = regla.get("dias") or [inicio.weekday()]
        partes.append("BYDAY=" + ",".join(_BYDAY[d] for d in dias))
    if regla["freq"] == "mensual":
        partes.append(f"BYMONTHDAY={inicio.day}")
    if regla.get("fin") == "veces":
        partes.append(f"COUNT={int(regla['veces'])}")
    elif regla.get("fin") == "fecha" and regla.get("hasta"):
        # El ultimo segundo del dia de fin en Montevideo, dicho en UTC.
        hasta = dt.date.fromisoformat(regla["hasta"])
        fin = MVD.localize(dt.datetime.combine(hasta, dt.time(23, 59, 59)))
        partes.append("UNTIL=" + fin.astimezone(pytz.utc).strftime("%Y%m%dT%H%M%SZ"))
    return "RRULE:" + ";".join(partes)


def invitados_de(fila: dict, email_cliente: str | None = None) -> list[str]:
    """El mail del cliente (si hay) y los invitados, sin repetir."""
    try:
        guardados = json.loads(fila.get("invitados") or "[]")
    except (TypeError, ValueError):
        guardados = []
    lista, vistos = [], set()
    for mail in ([email_cliente] if email_cliente else []) + list(guardados or []):
        mail = str(mail or "").strip()
        if mail and mail.lower() not in vistos:
            vistos.add(mail.lower())
            lista.append(mail)
    return lista


def cuerpo(fila: dict, email_cliente: str | None = None, *, con_meet: bool = False) -> dict:
    inicio = _inicio(fila)
    fin = inicio + dt.timedelta(minutes=rec.duracion_min(fila))
    body = {
        "summary": fila.get("title") or "Reunión",
        "description": fila.get("description") or "",
        "start": _cuando(inicio),
        "end": _cuando(fin),
        "attendees": [{"email": m} for m in invitados_de(fila, email_cliente)],
    }
    regla = rec.regla_de(fila)
    if regla:
        body["recurrence"] = [rrule(regla, inicio)]
    if con_meet:
        body["conferenceData"] = {"createRequest": {
            "requestId": uuid.uuid4().hex,
            "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
    return body


# ── escribir en Google ───────────────────────────────────────────────────────
# Todas con sendUpdates="all": es lo que hace que Google les avise a los invitados.

def nuevo_id(tipo: str, rid: int) -> str:
    """Id del evento elegido por el CRM (Google lo acepta si es base32hex: 0-9
    y a-v). Elegirlo antes de crear es lo que hace seguro el reintento: si la
    respuesta de Google se pierde, el mismo id da 409 y no un segundo evento."""
    return f"scalerics{'c' if tipo == 'cliente' else 'a'}{int(rid)}{uuid.uuid4().hex[:12]}"


def crear(service, fila: dict, email_cliente: str | None = None, *, event_id: str) -> dict:
    """Un solo evento (recurrente si la reunion se repite): una sola invitacion.
    Con Meet, como antes de 81f19fb, salvo que la reunion ya traiga su link."""
    con_meet = not (fila.get("meet_link") or "").strip()
    body = cuerpo(fila, email_cliente, con_meet=con_meet)
    body["id"] = event_id
    return service.events().insert(
        calendarId=CALENDARIO, body=body, conferenceDataVersion=1,
        sendUpdates="all").execute()


def actualizar(service, event_id: str, fila: dict, email_cliente: str | None = None) -> None:
    """La reunion entera (o la serie entera, con su RRULE) como esta en el CRM."""
    service.events().patch(calendarId=CALENDARIO, eventId=event_id,
                           body=cuerpo(fila, email_cliente), sendUpdates="all").execute()


def cambiar_regla(service, event_id: str, fila: dict) -> None:
    """Solo la RRULE: es como se corta una serie ("esta y las siguientes")."""
    regla = rec.regla_de(fila)
    service.events().patch(calendarId=CALENDARIO, eventId=event_id,
                           body={"recurrence": [rrule(regla, _inicio(fila))]},
                           sendUpdates="all").execute()


def borrar(service, event_id: str) -> None:
    service.events().delete(calendarId=CALENDARIO, eventId=event_id,
                            sendUpdates="all").execute()


def instancia(service, event_id: str, fila: dict, ocurrencia: str) -> str:
    """El id de la instancia de la serie cuya fecha ORIGINAL es `ocurrencia`.

    Se pide con `originalStart`, asi aparece aunque ya se haya movido a otro
    dia. Si Google no la devuelve, se arma el id con el formato de Google.
    """
    original = MVD.localize(dt.datetime.combine(dt.date.fromisoformat(ocurrencia),
                                                _inicio(fila).time()))
    items = service.events().instances(
        calendarId=CALENDARIO, eventId=event_id, originalStart=original.isoformat(),
        showDeleted=True).execute().get("items", [])
    if items:
        return items[0]["id"]
    return f"{event_id}_{original.astimezone(pytz.utc).strftime('%Y%m%dT%H%M%SZ')}"


def mover_instancia(service, event_id: str, fila: dict, ocurrencia: str, cambio: dict) -> None:
    """ "Solo esta": una excepcion de instancia en Google."""
    iid = instancia(service, event_id, fila, ocurrencia)
    inicio = dt.datetime.combine(dt.date.fromisoformat(cambio["date"]),
                                 dt.time.fromisoformat(cambio["time"]))
    minutos = int(cambio.get("duration_min") or rec.duracion_min(fila))
    service.events().patch(
        calendarId=CALENDARIO, eventId=iid, sendUpdates="all",
        body={"summary": cambio.get("title") or fila.get("title") or "Reunión",
              "start": _cuando(inicio),
              "end": _cuando(inicio + dt.timedelta(minutes=minutos))}).execute()


def cancelar_instancia(service, event_id: str, fila: dict, ocurrencia: str) -> None:
    service.events().delete(calendarId=CALENDARIO, sendUpdates="all",
                            eventId=instancia(service, event_id, fila, ocurrencia)).execute()


def aplicar_excepciones(service, event_id: str, fila: dict) -> None:
    """Las ocurrencias borradas o movidas en el CRM, repetidas en Google. Lo usa
    "Reintentar": una instancia que ya estaba cancelada no es un error."""
    for clave, cambio in rec.excepciones_de(fila).items():
        try:
            if cambio is None:
                cancelar_instancia(service, event_id, fila, clave)
            elif isinstance(cambio, dict):
                mover_instancia(service, event_id, fila, clave, cambio)
        except Exception as e:  # noqa: BLE001 - se decide por el estado HTTP
            if not ya_no_existe(e):
                raise


def es_del_crm(evento: dict, ids: set) -> bool:
    """Un evento de Google que creo el CRM: el evento, o una instancia suya."""
    eid = str(evento.get("id") or "")
    return bool(ids) and (eid in ids or str(evento.get("recurringEventId") or "") in ids
                          or eid.split("_")[0] in ids)
