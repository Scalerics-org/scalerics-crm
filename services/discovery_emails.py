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
from services.email_service import send_discovery_email

logger = logging.getLogger(__name__)

_FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"

# Arranca bajo a proposito: el subdominio nace sin reputacion y una tanda
# grande el primer dia es la peor forma de estrenarlo. Se sube a mano despues
# de ver entrega limpia, no antes.
_TOPE_DIARIO = 10
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
#      le importa por cual de nuestras campanas le estabamos escribiendo.
_DIRECCIONES_VEDADAS = """
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
    """Los que ya recibieron el contacto 1 y les toca el 2, que es el ultimo."""
    dias = DIAS_DE_CADA_CONTACTO[1]
    conn = _conn(db_path)
    try:
        filas = conn.execute(
            f"""
            SELECT {_CAMPOS}, MIN(dr.sent_at) AS primer_envio
              FROM businesses b
              JOIN discovery_reminders dr ON dr.business_id = b.id
             WHERE {_FILTRO_ELEGIBLE}
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
            conn = _conn(db_path)
            try:
                conn.execute(
                    "DELETE FROM discovery_reminders WHERE business_id = ? AND numero = ?",
                    (comercio["id"], numero),
                )
                conn.commit()
            finally:
                conn.close()
            res["fallidos"] += 1
            logger.error(
                f"Discovery: fallo el envio del contacto {numero} al comercio "
                f"{comercio['id']}. Se borro esa fila puntual "
                f"(business_id={comercio['id']}, numero={numero}) para reintentar; "
                f"NO borrar por business_id solo, eso se lleva los tokens ya publicados."
            )
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


def start_discovery_emails(app) -> None:
    """Corre una vez por dia. Arranca SOLO con DISCOVERY_EMAILS=on.

    El default es apagado por la misma razon que en Meta: el hilo corre 180
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
        time.sleep(180)  # dejar que la app termine de levantar
        while True:
            try:
                with app.app_context():
                    enviar_discovery(
                        app.config["DB_PATH"],
                        os.environ.get("CRM_URL", "https://scalerics-crm.fly.dev"),
                    )
            except Exception as e:
                logger.warning(f"Discovery: {e}")
            time.sleep(_CADA_24_HORAS)

    threading.Thread(target=_loop, daemon=True, name="discovery-emails").start()
    logger.info(
        f"Discovery ACTIVO por DISCOVERY_EMAILS=on: una corrida por dia, hasta "
        f"{_TOPE_DIARIO} mails, la primera 180s despues de este arranque"
    )
