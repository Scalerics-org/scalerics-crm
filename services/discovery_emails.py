"""Secuencia de mails en frio a los comercios de la cohorte `discovery`.

Es un modulo aparte y no una generalizacion de `meta_reminders`: ese le manda
correo real a terceros todos los dias desde el 19-8-2026, y convertirlo en un
motor generico para meterle una segunda campana con reglas distintas es tocar
lo unico que funciona. Se acepta duplicar el bucle de envio.

La diferencia de fondo con la campana de Meta: alla el lead lleno un formulario
pidiendo que lo contacten; aca el comercio no pidio nada y su direccion salio
raspada de su propio sitio web. Eso cambia el numero de contactos (dos, no
siete), el dominio desde el que sale, y la cantidad de guardas antes de mandar.
"""

import logging
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone

from services.discovery_contactos import DIAS_DE_CADA_CONTACTO, TOTAL_CONTACTOS
from services.corridas import marcar_corrida, puede_correr, ultima_corrida
from services.discovery_respuestas import sincronizar_desde_gmail
from services.email_service import (send_discovery_email,
                                   send_discovery_queue_alert)

logger = logging.getLogger(__name__)

_FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"

# El techo no lo pone la cuota del plan —entre esto y los 15 diarios de Meta
# sobra margen de sobra—, lo pone la reputacion: el subdominio nace sin
# historial de envio y un pico el primer dia es la peor forma de estrenarlo.
# 30 vacia la lista de 88 en tres dias sin que ningun dia parezca una descarga.
_TOPE_DIARIO = 30
_PAUSA_ENTRE_ENVIOS = 0.6
_CADA_24_HORAS = 24 * 60 * 60


def _ahora() -> str:
    return datetime.now(timezone.utc).strftime(_FORMATO_FECHA)


def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# ─── Registro y baja ─────────────────────────────────────────────────────────

def registrar_envio(db_path: str, business_id: int, numero: int) -> str:
    """Deja la fila ANTES de mandar, y devuelve el token de baja.

    El orden importa: si se mandara primero y se registrara despues, un corte
    en el medio dejaria un mail mandado sin fila, y el comercio lo recibiria de
    nuevo. Al reves, el peor caso es una fila sin mail, que es recuperable.
    """
    token = uuid.uuid4().hex + uuid.uuid4().hex[:8]
    conn = _conn(db_path)
    try:
        conn.execute(
            "INSERT INTO discovery_reminders (business_id, numero, token, sent_at) "
            "VALUES (?, ?, ?, ?)",
            (business_id, int(numero), token, _ahora()),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def dar_de_baja(db_path: str, token: str) -> bool:
    """Marca la baja. Devuelve False si el token no es de esta campana.

    Se llama con cualquier token que llegue a /baja/<token>, incluidos los de
    Meta, asi que un token desconocido es lo normal y no un error.
    """
    conn = _conn(db_path)
    try:
        cur = conn.execute(
            "UPDATE discovery_reminders SET unsubscribed_at = ? "
            "WHERE token = ? AND unsubscribed_at IS NULL",
            (_ahora(), token),
        )
        conn.commit()
        if cur.rowcount:
            return True
        existe = conn.execute(
            "SELECT 1 FROM discovery_reminders WHERE token = ?", (token,)
        ).fetchone()
        return existe is not None
    finally:
        conn.close()


def esta_dado_de_baja(db_path: str, business_id: int) -> bool:
    conn = _conn(db_path)
    try:
        fila = conn.execute(
            "SELECT 1 FROM discovery_reminders "
            "WHERE business_id = ? AND unsubscribed_at IS NOT NULL",
            (business_id,),
        ).fetchone()
        return fila is not None
    finally:
        conn.close()


# ─── A quien le toca ─────────────────────────────────────────────────────────

# Las guardas viven en el WHERE a proposito: que un comercio quede fuera no
# puede depender de que el llamador se acuerde de filtrarlo.
_FILTRO_ELEGIBLE = (
    "b.source = 'discovery' "
    "AND b.crm_status = 'sin_contactar' "
    "AND b.email IS NOT NULL AND LENGTH(TRIM(b.email)) > 3"
)

# Direcciones que no pueden recibir NADA de esta campana:
#   1. las de cualquier cohorte que no sea discovery — sobre todo los leads de
#      Meta, que reciben su propia secuencia con correo real;
#   2. las de quien se dio de baja en discovery;
#   3. las de quien se dio de baja en Meta. Quien dijo basta, dijo basta, y no
#      le importa por cual de nuestras campanas le estabamos escribiendo;
#   4. las que en las otras cohortes viven DENTRO de form_data y no en la
#      columna. En Meta el mail llega en el JSON del formulario y sube a la
#      columna por un backfill manual que no corre solo: mirar solo la
#      columna deja fuera de la veda a todo lead al que no se le haya
#      corrido. Las claves son las mismas que usa scripts/backfill_meta_emails.py.
_DIRECCIONES_VEDADAS = """
    SELECT LOWER(TRIM(email)) FROM mails_vedados
    UNION
    SELECT LOWER(TRIM(b2.email)) FROM businesses b2
     WHERE b2.email IS NOT NULL AND COALESCE(b2.source, '') <> 'discovery'
    UNION
    SELECT LOWER(TRIM(b3.email)) FROM businesses b3
      JOIN discovery_reminders dr ON dr.business_id = b3.id
     WHERE dr.unsubscribed_at IS NOT NULL AND b3.email IS NOT NULL
    UNION
    SELECT LOWER(TRIM(b4.email)) FROM businesses b4
      JOIN meta_reminders mr ON mr.business_id = b4.id
     WHERE mr.unsubscribed_at IS NOT NULL AND b4.email IS NOT NULL
    UNION
    SELECT LOWER(TRIM(json_extract(b6.form_data, '$.email'))) FROM businesses b6
     WHERE COALESCE(b6.source, '') <> 'discovery'
       AND b6.form_data IS NOT NULL AND json_valid(b6.form_data)
       AND json_extract(b6.form_data, '$.email') IS NOT NULL
    UNION
    SELECT LOWER(TRIM(json_extract(b7.form_data, '$.correo'))) FROM businesses b7
     WHERE COALESCE(b7.source, '') <> 'discovery'
       AND b7.form_data IS NOT NULL AND json_valid(b7.form_data)
       AND json_extract(b7.form_data, '$.correo') IS NOT NULL
"""

_CAMPOS = "b.id, b.name, b.city, b.category, TRIM(b.email) AS email, b.website"


def enviados_ultimas_24h(db_path: str) -> int:
    """El tope es por ventana rodante, no por corrida: la maquina se reinicia
    sola (un secret nuevo en Fly la reinicia) y sin esto cada reinicio
    disparara otra tanda."""
    conn = _conn(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM discovery_reminders "
            "WHERE sent_at >= datetime('now', '-1 day')"
        ).fetchone()[0]
    finally:
        conn.close()


def _filas_a_dicts(filas, numero: int) -> list[dict]:
    return [{
        "id": f["id"],
        "name": f["name"] or "",
        "city": f["city"] or "",
        "category": f["category"] or "",
        "email": f["email"],
        "website": f["website"] or "",
        "numero": numero,
    } for f in filas]


def comercios_a_contactar(db_path: str, limite: int) -> list[dict]:
    """Los que nunca recibieron nada de esta campana.

    El GROUP BY por direccion es la deduplicacion dentro de la cohorte: dos
    sucursales de la misma firma tienen telefono y ficha de Maps distintos —asi
    que el UNIQUE de businesses no las fusiona— pero comparten sitio y casilla.
    Medido en la primera corrida real: 13 mails, 12 direcciones unicas. Sin
    esto, esa persona recibe el mismo mail en frio dos veces.
    """
    conn = _conn(db_path)
    try:
        filas = conn.execute(
            f"""
            SELECT {_CAMPOS}, MIN(b.scraped_at) AS primero
              FROM businesses b
         LEFT JOIN discovery_reminders dr ON dr.business_id = b.id
             WHERE {_FILTRO_ELEGIBLE}
               AND dr.id IS NULL
               AND LOWER(TRIM(b.email)) NOT IN ({_DIRECCIONES_VEDADAS})
               AND NOT EXISTS (
                     SELECT 1 FROM discovery_reminders dr2
                       JOIN businesses b5 ON b5.id = dr2.business_id
                      WHERE LOWER(TRIM(b5.email)) = LOWER(TRIM(b.email))
                   )
          GROUP BY LOWER(TRIM(b.email))
          ORDER BY primero ASC
             LIMIT ?
            """,
            (int(limite),),
        ).fetchall()
    finally:
        conn.close()
    return _filas_a_dicts(filas, 1)


def comercios_a_seguir(db_path: str, limite: int) -> list[dict]:
    """Los que ya recibieron el contacto 1 y les toca el 2, que es el ultimo.

    La veda va tambien aca y no solo en `comercios_a_contactar`: entre el
    contacto 1 y el 2 pasan siete dias, y en esos siete dias el duenio del
    comercio puede llenar el formulario de Meta. Si la veda faltara, esa persona
    quedaria recibiendo la secuencia de Meta Y el segundo mail en frio. Lo mismo
    con una baja: si se da de baja por un link de Meta, tiene que cortar esta
    campana tambien.
    """
    dias = DIAS_DE_CADA_CONTACTO[1]
    conn = _conn(db_path)
    try:
        filas = conn.execute(
            f"""
            SELECT {_CAMPOS}, MIN(dr.sent_at) AS primer_envio
              FROM businesses b
              JOIN discovery_reminders dr ON dr.business_id = b.id
             WHERE {_FILTRO_ELEGIBLE}
               AND LOWER(TRIM(b.email)) NOT IN ({_DIRECCIONES_VEDADAS})
               AND NOT EXISTS (
                     SELECT 1 FROM discovery_reminders x
                      WHERE x.business_id = b.id AND x.unsubscribed_at IS NOT NULL
                   )
          GROUP BY b.id
            HAVING MAX(dr.numero) < {TOTAL_CONTACTOS}
               AND primer_envio <= datetime('now', '-{dias} days')
          ORDER BY primer_envio ASC
             LIMIT ?
            """,
            (int(limite),),
        ).fetchall()
    finally:
        conn.close()
    return _filas_a_dicts(filas, 2)


# ─── La tanda ────────────────────────────────────────────────────────────────

def enviar_discovery(db_path: str, base_url: str, dry_run: bool = False) -> dict:
    """Una tanda. Los seguimientos primero, despues los contactos nuevos.

    El dry-run NO respeta el cupo a proposito: no escribe ni manda, asi que no
    lo gasta, y en produccion el cupo esta en 0 buena parte del dia. Si lo
    respetara, la herramienta de diagnostico mas segura seria inutilizable
    justo cuando hace falta.
    """
    res = {"candidatos": 0, "enviados": 0, "fallidos": 0, "inciertos": 0,
           "seguimientos": 0, "nuevos": 0}

    cupo = _TOPE_DIARIO if dry_run else max(0, _TOPE_DIARIO - enviados_ultimas_24h(db_path))
    if cupo <= 0:
        logger.info("Discovery: no se manda nada, ya se llego al tope de las ultimas 24 horas")
        return res

    # Los seguimientos primero: uno a destiempo pierde sentido, mientras que un
    # primer contacto puede esperar un dia sin costo.
    seguimientos = comercios_a_seguir(db_path, limite=cupo)
    faltan = cupo - len(seguimientos)
    nuevos = comercios_a_contactar(db_path, limite=faltan) if faltan > 0 else []
    candidatos = seguimientos + nuevos
    res["candidatos"] = len(candidatos)
    res["seguimientos"] = len(seguimientos)
    res["nuevos"] = len(nuevos)

    for comercio in candidatos:
        numero = comercio.get("numero", 1)

        if dry_run:
            logger.info(f"[dry-run] contacto {numero} a {comercio['email']} "
                        f"(comercio {comercio['id']})")
            continue

        if enviados_ultimas_24h(db_path) >= _TOPE_DIARIO:
            logger.info("Discovery: se llego al tope en el medio de la tanda, corto aca")
            break

        try:
            token = registrar_envio(db_path, comercio["id"], numero)
        except sqlite3.IntegrityError:
            # Otra corrida se adelanto con este mismo contacto.
            continue
        except sqlite3.OperationalError as e:
            # Base bloqueada u otro problema puntual: se saltea ESTE comercio,
            # no se cae la tanda entera. Sin esto, un lock en el segundo de
            # diez deja a los ocho restantes sin mandar y sin rastro.
            logger.warning(f"Discovery: no se pudo registrar el comercio "
                           f"{comercio['id']} contacto {numero}: {e}")
            continue

        estado = send_discovery_email(
            comercio["email"], comercio["name"], comercio["category"],
            f"{base_url.rstrip('/')}/baja/{token}", numero,
        )

        if estado == "ok":
            res["enviados"] += 1
        elif estado == "fallo":
            # Sabemos que no salio: se borra SOLO esa fila, para que se
            # reintente. Borrar por business_id se llevaria el contacto 1, cuyo
            # token ya viaja dentro de un mail que alguien recibio.
            try:
                conn = _conn(db_path)
                try:
                    conn.execute(
                        "DELETE FROM discovery_reminders WHERE business_id = ? AND numero = ?",
                        (comercio["id"], numero),
                    )
                    conn.commit()
                finally:
                    conn.close()
            except sqlite3.Error as e:
                # Si el borrado falla, la fila queda y el contacto 2 va a salir
                # a los 7 dias diciendo "te escribimos hace una semana" cuando
                # el primero nunca llego. Hay que saberlo, no morir en silencio.
                logger.error(
                    f"Discovery: fallo el envio Y fallo el borrado de la fila "
                    f"(business_id={comercio['id']}, numero={numero}): {e}. "
                    f"Borrar esa fila puntual a mano o el proximo contacto miente."
                )
            else:
                logger.error(
                    f"Discovery: fallo el envio del contacto {numero} al comercio "
                    f"{comercio['id']}. Se borro esa fila puntual "
                    f"(business_id={comercio['id']}, numero={numero}) para reintentar; "
                    f"NO borrar por business_id solo, eso se lleva los tokens ya publicados."
                )
            res["fallidos"] += 1
        else:
            # "desconocido": la peticion pudo haber llegado y el mail pudo haber
            # salido. La fila se queda puesta, porque reintentar significaria
            # mandar dos veces.
            res["inciertos"] += 1
            logger.warning(
                f"Discovery: estado desconocido en el contacto {numero} del comercio "
                f"{comercio['id']}. La fila queda puesta a proposito; si se confirma "
                f"que no salio, borrar esa fila puntual "
                f"(business_id={comercio['id']}, numero={numero}) a mano."
            )

        time.sleep(_PAUSA_ENTRE_ENVIOS)

    logger.info(f"Discovery: {res}")
    return res


def tanda_diaria(db_path: str, base_url: str):
    """La tanda de hoy, o None si ya corrio dentro del plazo.

    Es el segundo guard, independiente del tope rodante de 24 horas. El hilo
    arranca 600 segundos despues de CADA boot y Fly reinicia en cada deploy: el
    26/8/2026 hubo cinco releases en 42 minutos. Ahi el tope hizo bien su
    trabajo —la segunda tanda mando 24 en vez de 30 porque descontó lo ya
    enviado— pero era lo unico que separaba un deploy de una tanda repetida.

    La marca se deja ANTES de mandar, no despues: si la tanda se muere en el
    medio, el reinicio siguiente no puede volver a intentarla entera.
    """
    if not puede_correr(db_path, "discovery"):
        logger.info(
            f"Discovery: ya corrio el {ultima_corrida(db_path, 'discovery')}, "
            f"se saltea esta tanda (arranque por deploy)"
        )
        return None
    marcar_corrida(db_path, "discovery")
    return enviar_discovery(db_path, base_url)


def start_discovery_emails(app) -> None:
    """Corre una vez por dia. Arranca SOLO con DISCOVERY_EMAILS=on.

    El default es apagado por la misma razon que en Meta: el hilo corre 600
    segundos despues de CADA boot, y Fly reinicia la maquina para aplicar un
    secret, asi que un default encendido convierte cualquier deploy en una
    tanda de correo en frio que nadie pidio.

    Ojo: este interruptor es independiente del de Meta. Apagar uno no apaga el
    otro, y un `secrets set` reinicia la maquina y dispara el hilo de los dos.
    """
    if os.environ.get("DISCOVERY_EMAILS", "").strip().lower() != "on":
        logger.info("Discovery apagado (hace falta DISCOVERY_EMAILS=on para arrancarlo)")
        return

    def _loop():
        # 600 s y no 180 como Meta, a proposito: los dos hilos arrancan en el
        # mismo boot y los dos pausan 0.6 s entre envios, asi que con el mismo
        # retraso se pisan y superan el limite de 2 peticiones por segundo de
        # Resend. Un 429 se lee como "fallo" y saltea un recordatorio de Meta
        # —correo real, en produccion— sin que nadie se entere.
        time.sleep(600)
        while True:
            try:
                with app.app_context():
                    # Primero las respuestas y despues los envios, no al reves:
                    # si alguien contesto ayer y su seguimiento vence hoy, hay
                    # que frenarlo ANTES de que salga, no despues.
                    try:
                        r = sincronizar_desde_gmail(app.config["DB_PATH"])
                        logger.info(f"Discovery respuestas: {r}")
                    except Exception as e:
                        logger.warning(f"Discovery respuestas: {e}")

                    tanda_diaria(
                        app.config["DB_PATH"],
                        os.environ.get("CRM_URL", "https://scalerics-crm.fly.dev"),
                    )
                    # Despues de mandar, no antes: lo que importa es cuanto
                    # queda una vez descontada la tanda de hoy.
                    avisar_si_la_cola_esta_baja(app.config["DB_PATH"])
            except Exception as e:
                logger.warning(f"Discovery: {e}")
            time.sleep(_CADA_24_HORAS)

    threading.Thread(target=_loop, daemon=True, name="discovery-emails").start()
    logger.info(
        f"Discovery ACTIVO por DISCOVERY_EMAILS=on: una corrida por dia, hasta "
        f"{_TOPE_DIARIO} mails, la primera 600s despues de este arranque"
    )


# Cuantos dias puede seguir mandando la campana antes de quedarse sin nadie.
# No es una metrica decorativa: entre el 23 y el 26 de agosto de 2026 la
# campana no mando un solo mail porque se le acabo la cola, y nos enteramos
# tres dias despues mirando el panel de Resend.
_DIAS_PARA_AVISAR = 7


def dias_de_autonomia(db_path: str) -> int:
    """Dias que la campana puede seguir mandando con lo que tiene en la cola.

    Cuenta con `comercios_a_contactar` y no con una consulta propia: dos
    definiciones de "elegible" es como la alarma termina diciendo que hay cola
    cuando el enviador no encuentra a nadie.

    Solo cuentan los que nunca recibieron nada. Los seguimientos no dan
    autonomia: son finitos y se acaban solos, asi que una cola con doscientos
    seguimientos pendientes y ningun comercio nuevo tiene autonomia cero, que
    es exactamente lo que hay que saber.

    Se redondea para abajo: media tanda no es medio dia de tranquilidad, es que
    manana te quedas sin nada.
    """
    # El limite alto es para contar, no para mandar. Con la cohorte entera de
    # discovery esto son unos pocos miles de filas.
    pendientes = len(comercios_a_contactar(db_path, 100000))
    return pendientes // max(1, _TOPE_DIARIO)


def avisar_si_la_cola_esta_baja(db_path: str) -> bool:
    """Manda el aviso si quedan menos de `_DIAS_PARA_AVISAR` dias. Devuelve si aviso.

    Se traga cualquier error a proposito: el aviso es lo menos importante que
    hace este job, y que reviente no puede impedir que salgan los mails.
    """
    destino = os.environ.get("ADMIN_EMAIL", "").strip()
    if not destino:
        logger.warning("Discovery: sin ADMIN_EMAIL, no se puede avisar de la cola baja")
        return False

    dias = dias_de_autonomia(db_path)
    if dias >= _DIAS_PARA_AVISAR:
        return False

    pendientes = len(comercios_a_contactar(db_path, 100000))
    try:
        send_discovery_queue_alert(destino, dias=dias, pendientes=pendientes,
                                   tope=_TOPE_DIARIO)
    except Exception as e:
        logger.error(f"Discovery: no se pudo mandar el aviso de cola baja: {e}")
        return False
    logger.warning(f"Discovery: quedan {dias} dias de cola ({pendientes} sin contactar), avisado a {destino}")
    return True
