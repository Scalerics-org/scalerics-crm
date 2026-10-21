"""Mandar plantillas desde el WhatsApp Business de Juan (pedido del 21/10).

En Plantillas, al confirmar un envío, Juan elige si sale del número de
Scalerics (el bot, /api/wa/send) o del suyo. El suyo no lo maneja el CRM: está
vinculado al lector de la notebook de Juan (wa-lector/). Por eso acá el
mensaje solo queda en cola; el lector lo pide por /api/oficina-bot/envios, lo
manda y avisa cómo salió. Si la notebook está apagada, sale cuando se prenda.

Tope de 30 por día: es una conexión no oficial y Meta puede bloquear el número
si ve muchos envíos automáticos. Solo admin.
"""

import re
import sqlite3
from datetime import datetime, timedelta, timezone

from database import _connect

TOPE_DIARIO = 30
LARGO_TEXTO = 4000
FORMATO = "%Y-%m-%d %H:%M:%S"
MONTEVIDEO = timezone(timedelta(hours=-3))


class NoSePuede(Exception):
    pass


def init_wa_juan(conn: sqlite3.Connection) -> None:
    """La cola de envíos. La llama `database.init_db` con su conexión."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wa_juan_envios (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            telefono    TEXT NOT NULL,
            texto       TEXT NOT NULL,
            destino     TEXT,                     -- nombre del lead, para mostrar
            estado      TEXT NOT NULL DEFAULT 'pendiente',   -- 'pendiente' | 'enviado' | 'error'
            error       TEXT,
            creado_por  INTEGER,
            creado_en   TEXT NOT NULL,
            enviado_en  TEXT
        )
    """)


def _ahora() -> str:
    return datetime.now(timezone.utc).strftime(FORMATO)


def normalizar_telefono(valor) -> str:
    """A formato internacional sin '+'. Los uruguayos sin código de país
    (099 123 456, 99123456) pasan a 59899123456."""
    digitos = re.sub(r"\D", "", str(valor or ""))
    if digitos.startswith("00"):
        digitos = digitos[2:]
    if len(digitos) == 9 and digitos.startswith("09"):
        digitos = "598" + digitos[1:]
    elif len(digitos) == 8 and digitos.startswith("9"):
        digitos = "598" + digitos
    if not 10 <= len(digitos) <= 15:
        raise NoSePuede("El teléfono no es válido")
    return digitos


def _inicio_del_dia_utc() -> str:
    hoy = datetime.now(MONTEVIDEO).replace(hour=0, minute=0, second=0, microsecond=0)
    return hoy.astimezone(timezone.utc).strftime(FORMATO)


def encolar(db_path: str, telefono, texto, destino=None, user_id=None) -> int:
    telefono = normalizar_telefono(telefono)
    texto = (texto or "").strip() if isinstance(texto, str) else ""
    if not texto:
        raise NoSePuede("Falta el texto")
    if len(texto) > LARGO_TEXTO:
        raise NoSePuede(f"El texto es muy largo: máximo {LARGO_TEXTO} caracteres")
    conn = _connect(db_path)
    try:
        hoy = conn.execute(
            "SELECT COUNT(*) FROM wa_juan_envios WHERE creado_en >= ? AND estado != 'error'",
            (_inicio_del_dia_utc(),)).fetchone()[0]
        if hoy >= TOPE_DIARIO:
            raise NoSePuede(f"Ya salieron {TOPE_DIARIO} mensajes hoy desde tu WhatsApp. "
                            "Es el tope para cuidar el número: mandalo desde el de Scalerics o mañana.")
        cur = conn.execute(
            "INSERT INTO wa_juan_envios (telefono, texto, destino, creado_por, creado_en) VALUES (?, ?, ?, ?, ?)",
            (telefono, texto, (str(destino or "").strip()[:120] or None), user_id, _ahora()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def estado(db_path: str, envio_id: int) -> dict:
    conn = _connect(db_path)
    try:
        f = conn.execute("SELECT id, estado, error, creado_en, enviado_en FROM wa_juan_envios WHERE id = ?",
                         (envio_id,)).fetchone()
    finally:
        conn.close()
    if not f:
        raise NoSePuede("Ese envío no existe")
    return dict(f)


def pendientes(db_path: str, limite: int = 5) -> list[dict]:
    """Lo que tiene que mandar el lector de la notebook, el más viejo primero."""
    conn = _connect(db_path)
    try:
        return [dict(f) for f in conn.execute(
            "SELECT id, telefono, texto FROM wa_juan_envios WHERE estado = 'pendiente' ORDER BY id LIMIT ?",
            (limite,))]
    finally:
        conn.close()


def resultado(db_path: str, envio_id: int, ok, error=None) -> None:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE wa_juan_envios SET estado = ?, error = ?, enviado_en = ? WHERE id = ? AND estado = 'pendiente'",
            ("enviado" if ok else "error", None if ok else (str(error or "No se pudo enviar"))[:300],
             _ahora(), envio_id))
        conn.commit()
    finally:
        conn.close()
    if not cur.rowcount:
        raise NoSePuede("Ese envío ya no está pendiente")
