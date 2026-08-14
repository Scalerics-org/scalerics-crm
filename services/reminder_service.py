"""Recordatorios de reuniones.

El CRM no avisaba nada antes de una reunion: ni al cliente ni al equipo. Los
no-shows son plata directa y no habia forma de reducirlos.

Se manda un aviso 24h antes y otro 1h antes. Cada envio se marca en la fila
(reminder_24h_at / reminder_1h_at), asi que el scheduler puede pasar cuantas veces
quiera sin repetir el mail.

DEPENDE de que la maquina siga viva: con auto_stop_machines y
min_machines_running=0 este loop casi nunca llegaba a correr. Por eso fly.toml
pasa a min_machines_running=1.
"""

import datetime
import logging
import threading
import time

import pytz

from database import connect
from services.email_service import send_meeting_reminder

logger = logging.getLogger(__name__)

MVD = pytz.timezone("America/Montevideo")

# Cada cuanto revisa. 5 minutos da una precision razonable para un aviso de 1h sin
# castigar la base.
INTERVALO_CHEQUEO = 5 * 60

# Ventanas: se avisa cuando faltan entre X y X+margen. El margen tiene que ser
# mayor al intervalo de chequeo, o una reunion puede caer entre dos pasadas y
# quedarse sin aviso.
_MARGEN = datetime.timedelta(minutes=15)

_AVISOS = (
    # (columna que marca el envio, cuanto antes, horas para el texto)
    ("reminder_24h_at", datetime.timedelta(hours=24), 24),
    ("reminder_1h_at", datetime.timedelta(hours=1), 1),
)


def _ahora() -> datetime.datetime:
    """Hora de Uruguay sin zona: es el formato en que se guarda start_at."""
    return datetime.datetime.now(MVD).replace(tzinfo=None)


def _formatear(iso: str) -> str:
    try:
        d = datetime.datetime.fromisoformat(iso)
        dias = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
        return f"{dias[d.weekday()]} {d.day}/{d.month} a las {d.strftime('%H:%M')}"
    except (TypeError, ValueError):
        return iso or ""


def reuniones_a_avisar(db_path: str, columna: str, antelacion: datetime.timedelta) -> list:
    """Reuniones agendadas cuyo aviso todavia no se mando y entran en la ventana."""
    ahora = _ahora()
    desde = (ahora + antelacion).isoformat()
    hasta = (ahora + antelacion + _MARGEN).isoformat()
    conn = connect(db_path)
    try:
        return [dict(r) for r in conn.execute(
            f"""SELECT m.id, m.title, m.start_at, m.meet_link, m.client_id,
                       b.name AS client_name, b.email AS client_email
                FROM meetings m
                LEFT JOIN businesses b ON m.client_id = b.id
                WHERE m.status = 'scheduled'
                  AND m.{columna} IS NULL
                  AND m.start_at >= ? AND m.start_at < ?
                ORDER BY m.start_at""",
            (desde, hasta),
        )]
    finally:
        conn.close()


def _marcar_enviado(db_path: str, meeting_id: int, columna: str) -> None:
    conn = connect(db_path)
    try:
        conn.execute(f"UPDATE meetings SET {columna} = ? WHERE id = ?",
                     (_ahora().isoformat(), meeting_id))
        conn.commit()
    finally:
        conn.close()


def procesar_recordatorios(db_path: str) -> int:
    """Manda los recordatorios pendientes. Devuelve cuantos se enviaron."""
    enviados = 0
    for columna, antelacion, horas in _AVISOS:
        for reunion in reuniones_a_avisar(db_path, columna, antelacion):
            email = (reunion.get("client_email") or "").strip()
            if not email:
                # Sin email no hay a quien avisarle. Se marca igual para no volver a
                # evaluarla en cada pasada.
                _marcar_enviado(db_path, reunion["id"], columna)
                logger.info("Reunion %s sin email del cliente — no se envia recordatorio",
                            reunion["id"])
                continue
            try:
                ok = send_meeting_reminder(
                    email,
                    nombre_cliente=reunion.get("client_name") or "",
                    titulo=reunion.get("title") or "Reunión",
                    cuando=_formatear(reunion.get("start_at") or ""),
                    meet_link=reunion.get("meet_link") or "",
                    horas_antes=horas,
                )
            except Exception as e:
                logger.error("Error enviando recordatorio de la reunion %s: %s",
                             reunion["id"], e)
                continue
            if ok:
                # Se marca DESPUES de enviar: si el envio falla se reintenta en la
                # proxima pasada, mientras la reunion siga dentro de la ventana.
                _marcar_enviado(db_path, reunion["id"], columna)
                enviados += 1
                logger.info("Recordatorio de %sh enviado para la reunion %s",
                            horas, reunion["id"])
    return enviados


def iniciar_scheduler(app) -> None:
    """Arranca el loop en background."""
    def _loop():
        time.sleep(30)  # dejar que la app termine de levantar
        while True:
            try:
                with app.app_context():
                    procesar_recordatorios(app.config["DB_PATH"])
            except Exception as e:
                logger.warning("Scheduler de recordatorios: %s", e)
            time.sleep(INTERVALO_CHEQUEO)

    threading.Thread(target=_loop, daemon=True, name="recordatorios").start()
    logger.info("Scheduler de recordatorios iniciado (cada %s min)", INTERVALO_CHEQUEO // 60)
