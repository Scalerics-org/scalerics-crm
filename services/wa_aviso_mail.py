"""Aviso por mail cuando alguien escribe al WhatsApp.

Pedido de Juan (14/9): "que nos llegue una notificacion por mail a
contacto@scalerics.com cuando alguien escribe al whatsapp".

Un mail por mensaje inunda la casilla: una conversacion normal son diez o
quince mensajes seguidos. Por eso se avisa solo con el PRIMER mensaje de un
numero, o con el primero despues de 30 minutos sin que ese numero escriba. El
ultimo mensaje de cada numero se guarda en la base, no en memoria: cada deploy
reinicia la maquina, y con un diccionario en memoria el primer mensaje despues
de un deploy avisaria de nuevo por cada conversacion abierta.

El mail sale por el mismo camino que el resto de las notificaciones del CRM
(Resend, `services/email_service.py`), con sus mismas variables de entorno.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

VENTANA = timedelta(minutes=30)
DESTINATARIO_POR_DEFECTO = "contacto@scalerics.com"
LARGO_MAXIMO_TEXTO = 500

# Uruguay no tiene horario de verano desde 2015: UTC-3 fijo todo el año.
MONTEVIDEO = timezone(timedelta(hours=-3))


def destinatario() -> str:
    """A quien le llega el aviso. `WA_AVISO_MAIL` lo cambia sin tocar codigo."""
    return (os.environ.get("WA_AVISO_MAIL") or "").strip() or DESTINATARIO_POR_DEFECTO


def normalizar_telefono(telefono) -> str:
    """Solo los digitos. "+598 99 123 456" y "59899123456" son el mismo numero,
    y si se guardaran distinto el mismo contacto contaria como dos."""
    return re.sub(r"[^0-9]", "", str(telefono or ""))


def recortar(texto: str, largo: int = LARGO_MAXIMO_TEXTO) -> str:
    t = (texto or "").strip()
    if len(t) <= largo:
        return t
    return t[:largo].rstrip() + "…"


def hora_montevideo(momento: datetime) -> str:
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(MONTEVIDEO).strftime("%d/%m/%Y %H:%M")


def _asegurar_tabla(conn: sqlite3.Connection) -> None:
    # La tabla vive aca y no en init_db a proposito: es de este modulo solo, y
    # database.py es zona compartida entre varias ramas abiertas a la vez.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wa_avisos_mail (
            telefono           TEXT PRIMARY KEY,
            ultimo_mensaje_at  TEXT NOT NULL,
            ultimo_aviso_at    TEXT
        )
    """)


def _leer_fecha(valor: str) -> datetime | None:
    try:
        d = datetime.fromisoformat(valor)
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def registrar_mensaje(db_path: str, telefono: str, ahora: datetime | None = None) -> bool:
    """Anota que `telefono` escribio ahora y devuelve si corresponde avisar.

    Corresponde si es el primer mensaje que se ve de ese numero, o si pasaron
    30 minutos o mas desde su mensaje anterior. Cada mensaje corre la ventana,
    haya avisado o no: una charla larga avisa una sola vez al principio.
    """
    tel = normalizar_telefono(telefono)
    if not tel:
        return False
    ahora = ahora or datetime.now(timezone.utc)
    if ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=timezone.utc)
    marca = ahora.astimezone(timezone.utc).isoformat()

    conn = sqlite3.connect(db_path, timeout=10, isolation_level=None)
    try:
        _asegurar_tabla(conn)
        # IMMEDIATE toma el lock de escritura antes de leer: dos mensajes del
        # mismo numero que llegan juntos no pueden avisar los dos.
        conn.execute("BEGIN IMMEDIATE")
        fila = conn.execute(
            "SELECT ultimo_mensaje_at FROM wa_avisos_mail WHERE telefono = ?", (tel,)
        ).fetchone()
        anterior = _leer_fecha(fila[0]) if fila else None
        avisar = anterior is None or (ahora - anterior) >= VENTANA
        if fila is None:
            conn.execute(
                "INSERT INTO wa_avisos_mail (telefono, ultimo_mensaje_at, ultimo_aviso_at) "
                "VALUES (?, ?, ?)", (tel, marca, marca))
        elif avisar:
            conn.execute(
                "UPDATE wa_avisos_mail SET ultimo_mensaje_at = ?, ultimo_aviso_at = ? "
                "WHERE telefono = ?", (marca, marca, tel))
        else:
            conn.execute(
                "UPDATE wa_avisos_mail SET ultimo_mensaje_at = ? WHERE telefono = ?",
                (marca, tel))
        conn.execute("COMMIT")
        return avisar
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def mandar_aviso(nombre: str, telefono: str, texto: str, ahora: datetime) -> bool:
    """Arma y manda el mail. Nunca levanta: un mail que no sale se loguea y ya.

    Corre en segundo plano, fuera del request del bot: si Resend tarda o se
    cae, el bot no se entera.
    """
    try:
        from services.email_service import send_wa_message_notification

        ok = send_wa_message_notification(
            destinatario(), nombre or "", str(telefono or ""), recortar(texto),
            hora_montevideo(ahora))
        if not ok:
            logger.error("[wa-aviso] no salio el aviso de WhatsApp de %s", telefono)
        return bool(ok)
    except Exception as e:  # noqa: BLE001 - el aviso nunca puede romper nada
        logger.error("[wa-aviso] error mandando el aviso de WhatsApp de %s: %s", telefono, e)
        return False
