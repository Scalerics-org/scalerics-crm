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
import re
import sqlite3
from datetime import datetime, timezone

from database import add_lead_event, get_business, update_business
# La tabla de cuan avanzado esta cada estado vive en planilla_semaforo y es
# una sola en todo el codigo: dos copias se desincronizan.
from services.planilla_semaforo import RANK
from services.secuencia_contactos import ESTADOS_CON_SECUENCIA

logger = logging.getLogger(__name__)

# Gmail no acepta una query infinita. Con miles de direcciones hay que partirla
# o rechaza la peticion entera.
_POR_CONSULTA = 25
_DIAS_ATRAS = 30

# El estado al que se mueve vive en CAMPANAS: no puede ser el mismo para las
# dos, porque lo que frena una secuencia no frena la otra.

# Marcas de respuesta automatica. Un fuera-de-oficina no es una conversacion:
# frenar el seguimiento por eso seria perder el unico contacto que quedaba.
_CABECERAS_AUTO = ("auto-submitted", "x-autoreply", "x-auto-response-suppress",
                   "precedence")
_ASUNTOS_AUTO = ("automatic reply", "respuesta automatica", "respuesta automática",
                 "out of office", "fuera de la oficina", "auto-reply",
                 "autorespuesta", "ausencia temporal")


# Las dos campanas que mandan correo. Se miran las dos: los leads de Meta
# tambien reciben una secuencia y tambien hay que frenarla si contestan.
# Los asuntos ya no son prefijos: desde que cada estado de Meta tiene su propio
# texto, el nombre del negocio aparece al principio ("Casa Garrido — el
# presupuesto"), al final ("Cerramos lo de Casa Garrido") y en el medio
# ("¿Dejamos lo de Casa Garrido para más adelante?"). Un patron por forma, con
# el nombre en el grupo `n`.
#
# Esta lista es un espejo de lo que arma email_service y puede quedar vieja sin
# que nada falle en produccion: el lead que contesta desde otra casilla
# simplemente no se detecta. Por eso hay un test que renderiza TODOS los asuntos
# reales y exige que cada uno se pueda leer aca.
_PATRONES_META = (
    r"^(?P<n>.+?) — ",                                  # el nombre al principio
    r"^sobre tu consulta para (?P<n>.+)$",
    r"^¿dejamos lo de (?P<n>.+) para más adelante\?$",
    r"^¿retomamos lo de (?P<n>.+)\?$",
    r"^¿sigue en pie lo de (?P<n>.+)\?$",
    r"^¿cerramos lo de (?P<n>.+)\?$",
    r"^cerramos lo de (?P<n>.+)$",
    r"^¿damos por cerrado lo de (?P<n>.+)\?$",
    r"^nos queda un mail más para (?P<n>.+)$",
    r"^último mail para (?P<n>.+)$",
)
_PATRONES_DISCOVERY = (
    r"^una idea para (?P<n>.+)$",
    r"^[uú]ltimo mail para (?P<n>.+)$",
)

CAMPANAS = {
    "discovery": {
        "tabla": "discovery_reminders",
        "source": "discovery",
        # Discovery tiene una sola secuencia y corta al salir de sin_contactar.
        "estados": ("sin_contactar",),
        "patrones": _PATRONES_DISCOVERY,
        # 'interesado' no tiene secuencia de discovery, asi que frena. Y
        # contestar no es lo mismo que estar interesado, pero es lo que mas se
        # le parece entre los estados que hay.
        "estado_al_responder": "interesado",
        # Fragmentos literales para la busqueda de Gmail, que no entiende regex.
        "fragmentos": ("Una idea para", "Ultimo mail para", "Último mail para"),
    },
    "meta": {
        "tabla": "meta_reminders",
        "source": "meta",
        # Meta tiene una secuencia POR ESTADO: mirar solo 'sin_contactar' dejaba
        # afuera a los 134 que estan en las otras cuatro, incluidos los de
        # 'presupuesto_enviado', que son los que mas importa frenar.
        "estados": ESTADOS_CON_SECUENCIA,
        "patrones": _PATRONES_META,
        # Para Meta NO sirve 'interesado': desde que cada estado tiene su
        # secuencia, ese tambien manda un mail, y le diria "no llegamos a
        # agendar" a alguien que acaba de escribir. 'follow_up_1' es el unico
        # estado sin secuencia que describe lo que realmente pasa —hay una
        # conversacion abierta— y ademas aparece en el panel de Pipeline, que
        # es donde alguien lo va a ver.
        "estado_al_responder": "follow_up_1",
        # La RED que busca en Gmail, distinta del LECTOR de arriba: no hace
        # falta que cubra los 13 asuntos, porque el camino principal es el
        # remitente y este es solo el respaldo para quien contesta desde otra
        # casilla. Gmail no entiende regex, asi que van literales.
        "fragmentos": (
            "Sobre tu consulta para", "cómo lo resolveríamos", "Cerramos lo de",
            "Dejamos lo de", "Retomamos lo de", "Sigue en pie lo de",
            "Damos por cerrado lo de", "qué te frenó",
            "quedó pendiente el presupuesto", "intentamos comunicarnos",
        ),
    },
}

# Nuestros propios remitentes. Sin excluirlos, cada mail que mandamos aparece
# en la busqueda por asunto y se contaria como una respuesta.
_REMITENTES_PROPIOS = ("scalerics.com", "novedades.scalerics.com")

# Los asuntos que NO llevan nombre de negocio, para no confundir el texto que
# viene despues con un nombre.
_ASUNTOS_SIN_NEGOCIO = ("una idea para tu negocio", "ultimo mail de scalerics",
                        "último mail de scalerics", "sobre tu consulta a scalerics")

_PREFIJOS_RESPUESTA = ("re:", "rv:", "fwd:", "fw:")

# Quien contesta pidiendo la baja NO esta negociando: esta pidiendo algo que
# tenemos que ejecutar. Si cae en 'follow_up_1' queda como oportunidad abierta
# en el pipeline, nadie hace lo que pidio, y si mas adelante entra de nuevo por
# otro formulario vuelve a recibir mails.
#
# Se mira SOLO el asunto, que en una respuesta suele arrastrar lo que la
# persona escribio arriba. El cuerpo no se descarga nunca.
#
# Frases largas y sin ambiguedad a proposito: "baja" suelto aparece en
# "baja de precios" y en nombres de negocio, y equivocarse aca da de baja a
# alguien que queria comprar.
_FRASES_BAJA = (
    "sacame de la lista", "sacar de la lista", "saquenme", "sáquenme",
    "sacame de aca", "sacame de acá",
    "no me escriban", "no me escribas", "no me manden", "no me mandes",
    "no quiero recibir", "no deseo recibir",
    "dar de baja", "darme de baja", "darse de baja", "baja de la lista",
    "unsubscribe", "remove me",
)


def pide_la_baja(asunto) -> bool:
    """Si el asunto de la respuesta pide explicitamente dejar de recibir mails."""
    bajo = " ".join((asunto or "").split()).lower()
    return any(f in bajo for f in _FRASES_BAJA)


def negocio_del_asunto(asunto) -> str:
    """El nombre del negocio que lleva el asunto, en minusculas, o "".

    Es la unica pista cuando el dueno contesta desde otra casilla: la direccion
    no coincide con ninguna de la cohorte, pero el asunto viaja con la
    respuesta. Devolver algo dudoso aca haria marcar al comercio equivocado, asi
    que ante cualquier duda devuelve "".
    """
    texto = " ".join((asunto or "").split())
    bajo = texto.lower()
    for p in _PREFIJOS_RESPUESTA:
        while bajo.startswith(p):
            texto = texto[len(p):].strip()
            bajo = texto.lower()
    if bajo in _ASUNTOS_SIN_NEGOCIO:
        return ""
    for campana in CAMPANAS.values():
        for patron in campana["patrones"]:
            m = re.match(patron, bajo)
            if m:
                # El nombre sale del texto original, no del bajo, para no
                # devolver algo que no coincida con lo que hay en la base.
                nombre = texto[m.start("n"):m.end("n")].strip()
                return nombre.lower()
    return ""


def consultas_por_asunto(days_back: int = _DIAS_ATRAS) -> list:
    """Las queries que buscan respuestas por ASUNTO, no por remitente.

    `in:anywhere` no es un detalle: las consultas de Gmail excluyen spam y
    papelera por defecto, y el 27/8/2026 habia un mensaje sin leer justo ahi.
    Una respuesta de un comercio que cae en spam es exactamente la que no
    podemos perder.
    """
    excluir = " ".join(f"-from:{d}" for d in _REMITENTES_PROPIOS)
    consultas = []
    for campana in CAMPANAS.values():
        for enc in campana["fragmentos"]:
            consultas.append(
                f'in:anywhere newer_than:{days_back}d subject:"{enc}" {excluir}')
    return consultas


def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _contactados(db_path: str) -> list:
    """Las filas (id, email, nombre) de todo lead que recibio algun mail nuestro.

    Cubre las DOS campanas: los leads de Meta reciben siete contactos y tambien
    hay que frenarlos si contestan. Antes solo se miraba discovery.

    Cada campana aporta los estados que TIENE en secuencia. Para Meta son
    cinco desde que cada estado lleva su propio texto: mirar solo
    'sin_contactar' dejaba sin vigilar a los 134 que estan en los otros cuatro,
    entre ellos 'presupuesto_enviado', que es justo el que mas duele pisar.
    """
    partes = " UNION ".join(
        """
        SELECT DISTINCT b.id AS id, LOWER(TRIM(b.email)) AS mail,
               LOWER(TRIM(COALESCE(b.name,''))) AS nombre
          FROM businesses b
          JOIN {tabla} r ON r.business_id = b.id
         WHERE b.source = '{source}'
           AND b.crm_status IN ({estados})
           AND b.email IS NOT NULL AND LENGTH(TRIM(b.email)) > 3
        """
        .format(tabla=c["tabla"], source=c["source"], estados=", ".join("'" + e + "'" for e in c["estados"]))
        for c in CAMPANAS.values()
    )
    conn = _conn(db_path)
    try:
        return conn.execute(partes).fetchall()
    finally:
        conn.close()


def ultimo_envio_por_lead(db_path: str) -> dict:
    """{business_id: 'YYYY-MM-DD HH:MM:SS' del ultimo mail que le mandamos}.

    Es lo que convierte "nos escribio" en "nos respondio": sin esta fecha, un
    mail que el lead nos mando ANTES de entrar a la secuencia se cuenta como
    respuesta, le frena el seguimiento y le abre una negociacion que no existe.
    """
    partes = " UNION ALL ".join(
        "SELECT business_id, sent_at FROM {}".format(c["tabla"])
        for c in CAMPANAS.values()
    )
    conn = _conn(db_path)
    try:
        filas = conn.execute(
            f"SELECT business_id, MAX(sent_at) AS ultimo FROM ({partes}) "
            f"GROUP BY business_id").fetchall()
    finally:
        conn.close()
    return {f["business_id"]: f["ultimo"] for f in filas}


def direcciones_contactadas(db_path: str) -> dict:
    """{direccion: business_id} de los leads a vigilar, de las dos campanas."""
    return {f["mail"]: f["id"] for f in _contactados(db_path)}


def negocios_contactados(db_path: str) -> dict:
    """{nombre en minusculas: business_id}, para casar respuestas por asunto.

    Un nombre repetido entre dos comercios se descarta: marcar al equivocado es
    peor que no marcar a ninguno, porque le corta el seguimiento a alguien que
    nunca contesto.
    """
    cuenta, salida = {}, {}
    for f in _contactados(db_path):
        if not f["nombre"]:
            continue
        cuenta[f["nombre"]] = cuenta.get(f["nombre"], 0) + 1
        salida[f["nombre"]] = f["id"]
    return {n: i for n, i in salida.items() if cuenta[n] == 1}


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
    """Frena la secuencia del lead y deja la marca en su linea de tiempo.

    **Nunca retrocede.** Alguien en 'presupuesto_enviado' que contesta es lo mas
    caliente que hay; moverlo a 'interesado' porque escribio seria empujarlo
    para atras en el embudo y ademas meterlo en otra secuencia de mails.
    """
    biz = get_business(db_path, business_id) or {}
    actual = biz.get("crm_status") or "sin_contactar"
    campana = CAMPANAS.get("meta" if biz.get("source") == "meta" else "discovery")
    destino = campana["estado_al_responder"]
    if RANK.get(destino, 0) <= RANK.get(actual, 0):
        # Ya esta igual o mas adelante: no se toca el estado. Igual queda el
        # evento, que es lo que le avisa al humano que hay algo que leer.
        destino = None
    if destino:
        update_business(db_path, business_id, crm_status=destino)
    try:
        add_lead_event(db_path, business_id, destino or actual,
                       f"Contestó el mail desde {direccion}")
    except Exception as e:
        # El evento es la traza para el humano; que falte no puede deshacer el
        # freno del seguimiento, que es lo que de verdad importa.
        logger.warning(f"Discovery: no se pudo registrar el evento de {business_id}: {e}")


# Cada cuanto, como mucho, se avisa que Gmail esta caido. Un token vencido no
# mejora porque le manden veinte mails.
_HORAS_ENTRE_AVISOS_GMAIL = 6


def _admins(db_path: str) -> list:
    """Las direcciones de los admins, sin importar una ruta desde un servicio."""
    conn = _conn(db_path)
    try:
        filas = conn.execute(
            "SELECT u.email FROM users u LEFT JOIN roles r ON u.role_id = r.id "
            "WHERE LOWER(r.name) = 'admin' AND u.email IS NOT NULL"
        ).fetchall()
    except Exception as e:
        logger.warning(f"No se pudieron leer los admins: {e}")
        filas = []
    finally:
        conn.close()
    direcciones = {f["email"].strip().lower() for f in filas if f["email"]}
    extra = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    if extra:
        direcciones.add(extra)
    return sorted(direcciones)


def avisar_que_gmail_fallo(db_path: str, detalle: str) -> bool:
    """Le grita a los admins que la deteccion de respuestas quedo ciega.

    Sin esto el fallo es mudo: la corrida sigue y manda igual —por decision
    explicita, una tanda demorada para todos es peor que un solapamiento para
    unos pocos— pero nadie se entera de que salio a ciegas, y los mails le
    siguen escribiendo encima a quien ya contesto.

    No es un monitor aparte a proposito. El token de Meta tiene un poller cada
    10 minutos porque no hay otro momento donde se note; este se revisa solo,
    porque el sync corre justo antes de cada tanda. El aviso donde ya esta el
    fallo no puede desincronizarse del uso real.

    Devuelve si el aviso salio. Se usa la tabla `corridas`, que ya lleva la
    cuenta de cuando paso cada cosa, para no repetirlo cada seis horas.
    """
    from services.corridas import marcar_corrida, puede_correr
    if not puede_correr(db_path, "alerta_gmail", cada_horas=_HORAS_ENTRE_AVISOS_GMAIL):
        logger.info("Gmail sigue caido, aviso silenciado (ya se aviso hace poco)")
        return False
    marcar_corrida(db_path, "alerta_gmail")

    from services.email_service import send_meta_token_alert
    cuerpo = (
        "La detección de respuestas por Gmail está fallando, así que el CRM no "
        "se está enterando de quién contesta los mails. Las secuencias siguen "
        "saliendo igual, o sea que pueden escribirle encima a alguien que ya "
        "respondió. "
        f"Detalle: {detalle}"
    )
    mandados = 0
    for direccion in _admins(db_path):
        try:
            send_meta_token_alert(direccion, cuerpo)
            mandados += 1
        except Exception as e:
            logger.warning(f"No se pudo avisar a {direccion}: {e}")
    logger.error(f"Gmail caido, avisados {mandados} admins: {detalle}")
    return mandados > 0


def marcar_baja_pedida(db_path: str, business_id: int, direccion: str,
                       asunto: str) -> None:
    """Ejecuta la baja que la persona pidio, y la deja registrada.

    La direccion va a `mails_vedados`, que es lo que corta el mail en las DOS
    campanas y sigue valiendo si el mismo mail entra de nuevo por otro
    formulario. El estado solo se mueve si el lead estaba siendo nutrido: a un
    cliente que pide no recibir mas mails se le corta el correo, no se lo
    degrada a 'no_interesa'.
    """
    from services.mails_vedados import vedar
    biz = get_business(db_path, business_id) or {}
    correo = direccion or (biz.get("email") or "")
    vedar(db_path, correo, "baja_pedida", (asunto or "")[:200])
    actual = biz.get("crm_status") or "sin_contactar"
    if actual in ESTADOS_CON_SECUENCIA:
        update_business(db_path, business_id, crm_status="no_interesa")
        actual = "no_interesa"
    try:
        add_lead_event(db_path, business_id, actual,
                       f"Pidió la baja por mail desde {correo or '(sin remitente)'}")
    except Exception as e:
        logger.warning(f"No se pudo registrar el evento de baja de {business_id}: {e}")


def sincronizar_respuestas(db_path: str, buscar, days_back: int = _DIAS_ATRAS,
                           dry_run: bool = False) -> dict:
    """Busca respuestas de la cohorte y frena el seguimiento de quien contesto.

    `buscar(query)` devuelve mensajes como dicts con "from", "subject" y
    "headers".
    """
    contactadas = direcciones_contactadas(db_path)
    por_nombre = negocios_contactados(db_path)
    ultimo_envio = ultimo_envio_por_lead(db_path)
    res = {"revisados": len(contactadas), "respondieron": 0, "automaticas": 0,
           "previas": 0, "bajas": 0}
    if not contactadas:
        return res

    ya_marcadas, autos, previas, bajas = set(), set(), set(), set()
    # Dos vias: por remitente (la casilla a la que escribimos) y por asunto (el
    # dueno contestando desde otra cuenta, o una respuesta que cayo en spam).
    consultas = (partir_en_consultas(sorted(contactadas), days_back=days_back)
                 + consultas_por_asunto(days_back=days_back))
    for consulta in consultas:
        try:
            mensajes = buscar(consulta) or []
        except Exception as e:
            # Un grupo que falla no puede llevarse los demas: el resto de la
            # cohorte igual tiene que quedar revisado.
            logger.warning(f"Discovery respuestas: fallo una consulta: {e}")
            continue

        for msg in mensajes:
            direccion = (msg.get("from") or "").strip().lower()
            asunto = msg.get("subject")

            # Primero por remitente; si no es de la cohorte, por el nombre del
            # negocio que viaja en el asunto.
            bid = contactadas.get(direccion)
            if bid is None:
                bid = por_nombre.get(negocio_del_asunto(asunto))
            if bid is None or bid in ya_marcadas:
                continue

            if es_respuesta_automatica(msg.get("headers"), asunto):
                autos.add(bid)
                continue

            # Que nos haya escrito no quiere decir que nos haya respondido: un
            # mail suyo ANTERIOR a nuestro ultimo envio es otra conversacion.
            # Sin fecha se cuenta como respuesta a proposito: perder una
            # respuesta real y seguir escribiendole encima es peor que abrir una
            # negociacion de mas, que un humano descarta en diez segundos.
            enviado = ultimo_envio.get(bid)
            fecha = (msg.get("date") or "").strip()
            if enviado and fecha and fecha < enviado:
                previas.add(bid)
                continue

            ya_marcadas.add(bid)
            if pide_la_baja(asunto):
                bajas.add(bid)
                if not dry_run:
                    marcar_baja_pedida(db_path, bid, direccion, asunto)
                logger.info(f"Baja pedida a mano por {direccion or asunto!r}")
                continue
            res["respondieron"] += 1
            if not dry_run:
                marcar_respondio(db_path, bid, direccion or "(sin remitente)")
            logger.info(f"Respuesta de {direccion or asunto!r}: se frena su seguimiento")

    # Se cuenta por lead, no por mensaje, y solo lo que NO termino marcado: un
    # lead con un autorespondedor y despues una respuesta de verdad cuenta como
    # respuesta y nada mas.
    res["bajas"] = len(bajas)
    res["automaticas"] = len(autos - ya_marcadas)
    res["previas"] = len(previas - ya_marcadas)
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
            # internalDate viene en milisegundos epoch UTC. Se guarda en el
            # formato de la casa para poder compararlo con sent_at, que es UTC
            # naive con ese mismo molde.
            fecha = ""
            try:
                ms = int(det.get("internalDate") or 0)
                if ms:
                    fecha = datetime.fromtimestamp(ms / 1000, timezone.utc)                                    .strftime("%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError):
                fecha = ""
            mensajes.append({"from": direccion.strip(),
                             "subject": cabeceras.get("Subject", ""),
                             "date": fecha,
                             "headers": cabeceras})
        return mensajes
    return buscar


def sincronizar_desde_gmail(db_path: str, days_back: int = _DIAS_ATRAS,
                            dry_run: bool = False) -> dict:
    """Igual que sincronizar_respuestas pero contra la casilla de verdad.

    Si Gmail falla, avisa a los admins ANTES de propagar la excepcion: quien
    llama la atrapa y manda la tanda igual, asi que este es el unico lugar
    donde el fallo todavia se puede contar. Sin el aviso, la deteccion de
    respuestas se apaga en silencio y las secuencias siguen escribiendole
    encima a gente que ya contesto.
    """
    try:
        return _sincronizar_desde_gmail(db_path, days_back, dry_run)
    except Exception as e:
        try:
            avisar_que_gmail_fallo(db_path, f"{type(e).__name__}: {e}")
        except Exception as fallo_aviso:
            logger.error(f"Gmail fallo y ademas no se pudo avisar: {fallo_aviso}")
        raise


def _sincronizar_desde_gmail(db_path: str, days_back: int, dry_run: bool) -> dict:
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
