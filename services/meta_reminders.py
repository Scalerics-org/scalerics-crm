"""Registro de recordatorios enviados a leads de Meta y su baja de la lista."""

import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone

from services.email_service import send_meta_lead_reminder

CLAVE_NEGOCIO = "¿cómo_se_llama_tu_negocio?"
CLAVE_RUBRO = "¿que_es_lo_que_buscás_para_tu_negocio?"

logger = logging.getLogger(__name__)

# Resend free permite 2 envios por segundo. A 15 por dia sobra, pero el codigo
# no tiene que depender de que el volumen sea bajo.
_PAUSA_ENTRE_ENVIOS = 0.6
_CADA_24_HORAS = 24 * 60 * 60

# El resto de la base guarda las fechas asi (scraped_at, entre otras) y las
# compara contra datetime('now', ...) de SQLite, que devuelve este mismo
# formato. Un isoformat() con 'T' y offset no compara: rompe lexicograficamente
# en la posicion 10.
_FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"


def _ahora() -> str:
    """UTC naive en el formato de la casa, comparable con datetime('now')."""
    return datetime.now(timezone.utc).strftime(_FORMATO_FECHA)


def _conn(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def registrar_envio(db_path: str, business_id: int) -> str:
    """Deja constancia del envio y devuelve el token de baja.

    Lanza sqlite3.IntegrityError si ese lead ya tenia un recordatorio: es la
    red que impide mandar dos veces, y tiene que fallar ruidosamente.
    """
    token = secrets.token_urlsafe(24)
    ahora = _ahora()
    conn = _conn(db_path)
    try:
        conn.execute(
            "INSERT INTO meta_reminders (business_id, token, sent_at) VALUES (?, ?, ?)",
            (business_id, token, ahora),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def dar_de_baja(db_path: str, token: str) -> bool:
    """Marca la baja. Devuelve False si el token no existe."""
    ahora = _ahora()
    conn = _conn(db_path)
    try:
        cur = conn.execute(
            "UPDATE meta_reminders SET unsubscribed_at = ? WHERE token = ? AND unsubscribed_at IS NULL",
            (ahora, token),
        )
        conn.commit()
        if cur.rowcount:
            return True
        existe = conn.execute(
            "SELECT 1 FROM meta_reminders WHERE token = ?", (token,)
        ).fetchone()
        return bool(existe)
    finally:
        conn.close()


def esta_dado_de_baja(db_path: str, business_id: int) -> bool:
    """Solo lo usan los tests hoy, y esta bien que asi sea: ver la nota de la
    Task 5 sobre por que la baja ya queda cubierta por la seleccion."""
    conn = _conn(db_path)
    try:
        fila = conn.execute(
            "SELECT unsubscribed_at FROM meta_reminders WHERE business_id = ?",
            (business_id,),
        ).fetchone()
    finally:
        conn.close()
    return bool(fila and fila[0])


def _texto(campos: dict, clave: str) -> str:
    """Los valores de Meta vienen como 'una_nueva_página_web'."""
    return (campos.get(clave) or "").replace("_", " ").strip()


def leads_a_recordar(db_path: str, dias_minimos: int = 3, limite: int = 15) -> list[dict]:
    """Leads de Meta que corresponde recordar hoy, del mas viejo al mas nuevo.

    Las guardas viven todas en el WHERE a proposito: que un lead quede fuera
    no puede depender de que el llamador se acuerde de filtrarlo.
    """
    conn = _conn(db_path)
    conn.row_factory = sqlite3.Row
    try:
        filas = conn.execute(
            """
            SELECT b.id, b.name, b.email, b.form_data
              FROM businesses b
         LEFT JOIN meta_reminders r ON r.business_id = b.id
             WHERE b.source = 'meta'
               AND b.crm_status = 'sin_contactar'
               AND b.email IS NOT NULL AND LENGTH(TRIM(b.email)) > 3
               AND r.id IS NULL
               AND b.scraped_at IS NOT NULL
               AND b.scraped_at <= datetime('now', ?)
          ORDER BY b.scraped_at ASC
             LIMIT ?
            """,
            (f"-{int(dias_minimos)} days", int(limite)),
        ).fetchall()
    finally:
        conn.close()

    salida = []
    for f in filas:
        try:
            campos = json.loads(f["form_data"] or "{}")
        except (ValueError, TypeError):
            campos = {}
        if not isinstance(campos, dict):
            campos = {}
        salida.append({
            "id": f["id"],
            "name": f["name"] or "",
            "email": f["email"],
            "negocio": _texto(campos, CLAVE_NEGOCIO),
            "rubro": _texto(campos, CLAVE_RUBRO),
        })
    return salida


def enviar_recordatorios(db_path: str, base_url: str, dry_run: bool = False) -> dict:
    candidatos = leads_a_recordar(db_path)
    res = {"candidatos": len(candidatos), "enviados": 0, "fallidos": 0, "inciertos": 0}

    for lead in candidatos:
        if dry_run:
            logger.info(f"[dry-run] recordatorio a {lead['email']} (lead {lead['id']})")
            continue
        try:
            token = registrar_envio(db_path, lead["id"])
        except sqlite3.IntegrityError:
            # Otra corrida se le adelanto. No es un error: es la guarda haciendo
            # su trabajo.
            continue
        estado = send_meta_lead_reminder(
            lead["email"], lead["name"], lead["negocio"], lead["rubro"],
            f"{base_url.rstrip('/')}/baja/{token}",
        )
        if estado == "ok":
            res["enviados"] += 1
        elif estado == "desconocido":
            # No sabemos si el mail salio. Si borramos la fila, manana el lead
            # vuelve a ser elegible y le llega un segundo mail; y si con el link
            # del primero se dio de baja, el token se fue con la fila y la baja
            # no lo protege. Se queda puesta: el silencio se arregla a mano.
            res["inciertos"] += 1
            logger.error(
                f"Recordatorios Meta: envio incierto al lead {lead['id']} "
                f"(business_id={lead['id']}, {lead['email']}): la peticion a Resend no "
                f"confirmo ni fallo, pudo haber salido. Se DEJA el registro en "
                f"meta_reminders para no mandarle dos veces; si se confirma que no "
                f"llego, borrar la fila a mano para reintentar."
            )
        else:
            # Se borra el registro para que manana se reintente: dejarlo puesto
            # significaria que ese lead nunca recibe nada.
            conn = _conn(db_path)
            try:
                try:
                    conn.execute("DELETE FROM meta_reminders WHERE business_id = ?", (lead["id"],))
                    conn.commit()
                except Exception:
                    # Si esto tambien falla (ej. "database is locked"), el lead
                    # queda registrado como si hubiera recibido el mail sin
                    # haberlo recibido. No podemos arreglarlo solos: que quede
                    # gritado en el log, con el id, para desmarcarlo a mano. Y
                    # sobre todo, que no aborte la tanda de hoy por un lead.
                    logger.error(
                        f"Recordatorios Meta: el mail al lead {lead['id']} fallo y ademas "
                        f"no se pudo limpiar el registro de envio (business_id={lead['id']}). "
                        f"Va a quedar marcado como contactado sin haber recibido nada: "
                        f"revisar/borrar manualmente el registro en meta_reminders.",
                        exc_info=True,
                    )
            finally:
                conn.close()
            res["fallidos"] += 1
        time.sleep(_PAUSA_ENTRE_ENVIOS)

    logger.info(f"Recordatorios Meta: {res}")
    return res


def start_meta_reminders(app) -> None:
    """Corre una vez por dia. Se apaga con META_RECORDATORIOS=off."""
    if os.environ.get("META_RECORDATORIOS", "").lower() == "off":
        logger.info("Recordatorios de Meta apagados por META_RECORDATORIOS=off")
        return

    def _loop():
        time.sleep(180)  # dejar que la app termine de levantar
        while True:
            try:
                with app.app_context():
                    enviar_recordatorios(
                        app.config["DB_PATH"],
                        os.environ.get("CRM_URL", "https://scalerics-crm.fly.dev"),
                    )
            except Exception as e:
                logger.warning(f"Recordatorios Meta: {e}")
            time.sleep(_CADA_24_HORAS)

    threading.Thread(target=_loop, daemon=True, name="meta-reminders").start()
    logger.info("Recordatorios de Meta activos (una corrida por dia)")
