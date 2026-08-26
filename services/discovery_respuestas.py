"""Detecta que un comercio de discovery contesto y frena su seguimiento.

El segundo contacto sale a los 7 dias del primero. Sin esto, alguien que
contesta "si, contame" recibe una semana despues un "Ultimo mail para X"
automatico — que no es una molestia, es pisar una conversacion viva con un
robot.

El freno ya existia: `comercios_a_seguir` exige crm_status = 'sin_contactar',
asi que cualquier otro estado corta el seguimiento solo. Lo que faltaba era
quien cambia el estado.

Las respuestas caen en scalerics@gmail.com (contacto@scalerics.com reenvia
ahi), que es la misma casilla que ya lee services/calendly_gmail.py. Este
modulo reusa ese OAuth en vez de montar uno propio.

`buscar` se inyecta como parametro por la misma razon que `abrir` en
email_finder: la logica de a quien mirar, que descartar y que marcar se prueba
sin credenciales ni red.
"""

import logging
import os
import sqlite3

from database import add_lead_event, update_business

logger = logging.getLogger(__name__)

# Gmail no acepta una query infinita. Con miles de direcciones hay que partirla
# o rechaza la peticion entera.
_POR_CONSULTA = 25
_DIAS_ATRAS = 30

# El estado al que se mueve. El CRM no tiene un "respondio", y lo unico que se
# necesita a nivel mecanico es salir de 'sin_contactar' para frenar el
# seguimiento. Entre los que hay, 'interesado' es el que lo pone adelante de
# los ojos de Juan para que lo triage: contestar no es lo mismo que estar
# interesado, pero es lo que mas se le parece en la lista existente.
_ESTADO_AL_RESPONDER = "interesado"

# Marcas de respuesta automatica. Un fuera-de-oficina no es una conversacion:
# frenar el seguimiento por eso seria perder el unico contacto que quedaba.
_CABECERAS_AUTO = ("auto-submitted", "x-autoreply", "x-auto-response-suppress",
                   "precedence")
_ASUNTOS_AUTO = ("automatic reply", "respuesta automatica", "respuesta automática",
                 "out of office", "fuera de la oficina", "auto-reply",
                 "autorespuesta", "ausencia temporal")


def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def direcciones_contactadas(db_path: str) -> dict:
    """{direccion: business_id} de los que recibieron discovery y siguen sin mover.

    Los que ya salieron de 'sin_contactar' no se miran: alguien ya los movio, el
    seguimiento ya esta frenado y no hay nada que marcar de nuevo.
    """
    conn = _conn(db_path)
    try:
        filas = conn.execute(
            """
            SELECT DISTINCT LOWER(TRIM(b.email)) AS mail, b.id
              FROM businesses b
              JOIN discovery_reminders dr ON dr.business_id = b.id
             WHERE b.source = 'discovery'
               AND b.crm_status = 'sin_contactar'
               AND b.email IS NOT NULL AND LENGTH(TRIM(b.email)) > 3
            """
        ).fetchall()
    finally:
        conn.close()
    return {f["mail"]: f["id"] for f in filas}


def es_respuesta_automatica(cabeceras: dict, asunto: str) -> bool:
    """Un fuera-de-oficina o autorespondedor, que no cuenta como contestar."""
    for k, v in (cabeceras or {}).items():
        clave = str(k).lower()
        if clave in _CABECERAS_AUTO and str(v).strip().lower() not in ("", "no"):
            return True
    bajo = (asunto or "").lower()
    return any(marca in bajo for marca in _ASUNTOS_AUTO)


def partir_en_consultas(direcciones, por_consulta: int = _POR_CONSULTA,
                        days_back: int = _DIAS_ATRAS) -> list:
    """Las queries de Gmail que cubren todas las direcciones.

    Sin direcciones devuelve lista vacia y no una query con `from:()`, que le
    haria devolver media casilla.
    """
    direcciones = [d for d in direcciones if d]
    consultas = []
    for i in range(0, len(direcciones), max(1, por_consulta)):
        grupo = direcciones[i:i + max(1, por_consulta)]
        consultas.append(f"newer_than:{days_back}d from:({' OR '.join(grupo)})")
    return consultas


def marcar_respondio(db_path: str, business_id: int, direccion: str) -> None:
    """Saca al comercio de 'sin_contactar' y deja la marca en su linea de tiempo."""
    update_business(db_path, business_id, crm_status=_ESTADO_AL_RESPONDER)
    try:
        add_lead_event(db_path, business_id, "discovery_respuesta",
                       f"Contestó el mail de discovery desde {direccion}")
    except Exception as e:
        # El evento es la traza para el humano; que falte no puede deshacer el
        # freno del seguimiento, que es lo que de verdad importa.
        logger.warning(f"Discovery: no se pudo registrar el evento de {business_id}: {e}")


def sincronizar_respuestas(db_path: str, buscar, days_back: int = _DIAS_ATRAS,
                           dry_run: bool = False) -> dict:
    """Busca respuestas de la cohorte y frena el seguimiento de quien contesto.

    `buscar(query)` devuelve mensajes como dicts con "from", "subject" y
    "headers".
    """
    contactadas = direcciones_contactadas(db_path)
    res = {"revisados": len(contactadas), "respondieron": 0, "automaticas": 0}
    if not contactadas:
        return res

    ya_marcadas = set()
    for consulta in partir_en_consultas(sorted(contactadas), days_back=days_back):
        try:
            mensajes = buscar(consulta) or []
        except Exception as e:
            # Un grupo que falla no puede llevarse los demas: el resto de la
            # cohorte igual tiene que quedar revisado.
            logger.warning(f"Discovery respuestas: fallo una consulta: {e}")
            continue

        for msg in mensajes:
            direccion = (msg.get("from") or "").strip().lower()
            if direccion not in contactadas or direccion in ya_marcadas:
                continue
            if es_respuesta_automatica(msg.get("headers"), msg.get("subject")):
                res["automaticas"] += 1
                continue

            ya_marcadas.add(direccion)
            res["respondieron"] += 1
            if not dry_run:
                marcar_respondio(db_path, contactadas[direccion], direccion)
            logger.info(f"Discovery: {direccion} contesto, se frena su seguimiento")

    return res


def buscar_con_gmail(service):
    """Arma el `buscar` que usa produccion, contra el cliente de Gmail.

    Se piden solo las cabeceras (format="metadata"): el cuerpo no se mira y
    traerlo multiplicaria el trafico sin agregar nada.
    """
    def buscar(query):
        resp = service.users().messages().list(
            userId="me", q=query, maxResults=100).execute()
        mensajes = []
        for m in resp.get("messages", []):
            det = service.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Auto-Submitted",
                                 "X-Autoreply", "Precedence"]).execute()
            cabeceras = {h["name"]: h["value"]
                         for h in det.get("payload", {}).get("headers", [])}
            crudo = cabeceras.get("From", "")
            # "Nombre <mail@dominio>" -> "mail@dominio"
            direccion = crudo.split("<")[-1].strip(" >") if "<" in crudo else crudo
            mensajes.append({"from": direccion.strip(),
                             "subject": cabeceras.get("Subject", ""),
                             "headers": cabeceras})
        return mensajes
    return buscar


def sincronizar_desde_gmail(db_path: str, days_back: int = _DIAS_ATRAS,
                            dry_run: bool = False) -> dict:
    """Igual que sincronizar_respuestas pero contra la casilla de verdad."""
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
    return sincronizar_respuestas(db_path, buscar_con_gmail(service),
                                  days_back=days_back, dry_run=dry_run)
