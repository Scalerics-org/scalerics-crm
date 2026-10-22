"""Oficina: los recordatorios que arma Bruno leyendo el WhatsApp de Juan.

Arrancó el 20/10 como un equipo de cinco agentes con chat, indicaciones y
aprendizajes. El 22/10 Juan sacó a los otros cuatro y el chat: queda solo
Bruno. Lo corre una tarea programada de Claude Code en la notebook de Juan, que
lee los chats con wa-lector/ y carga los recordatorios por /api/oficina-bot/
(scripts/oficina_responder.py). Las tablas viejas del chat (oficina_mensajes,
oficina_indicaciones, oficina_aprendizajes) quedan en la base de producción,
pero ya nada las crea ni las lee.

Solo admin.
"""

import sqlite3
from datetime import date, datetime, timedelta, timezone

from database import _connect

FORMATO = "%Y-%m-%d %H:%M:%S"
# Uruguay está en UTC-3 fijo y el servidor en UTC: "hoy" nunca es date.today().
MONTEVIDEO = timezone(timedelta(hours=-3))
LARGO_RECORDATORIO = 300
LARGO_CITA = 400
LARGO_BORRADOR = 2000
DIAS_A_LA_VISTA = 7


class NoSePuede(Exception):
    pass


def init_oficina(conn: sqlite3.Connection) -> None:
    """Tablas de la Oficina. La llama `database.init_db` con su conexión."""
    init_recordatorios(conn)


def init_recordatorios(conn: sqlite3.Connection) -> None:
    """Recordatorios que arma Bruno leyendo los chats de WhatsApp de Juan (21/10).

    Los chats no llegan al CRM: viven en la notebook de Juan (wa-lector/). Acá
    solo queda el recordatorio, con la frase del chat de donde salió."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oficina_recordatorios (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            agente           TEXT NOT NULL DEFAULT 'bruno',
            que              TEXT NOT NULL,
            contacto         TEXT,
            cita             TEXT,
            cuando           TEXT NOT NULL,          -- YYYY-MM-DD, hora de Montevideo
            clave            TEXT UNIQUE,            -- la pone Bruno, para no repetir
            estado           TEXT NOT NULL DEFAULT 'pendiente',   -- 'pendiente' | 'hecho'
            borrador_pedido  INTEGER NOT NULL DEFAULT 0,
            borrador         TEXT,
            creado_en        TEXT NOT NULL,
            resuelto_en      TEXT
        )
    """)


def _hoy() -> date:
    return datetime.now(MONTEVIDEO).date()


def _ahora() -> str:
    return datetime.now(timezone.utc).strftime(FORMATO)


def _texto(valor, largo: int, falta: str) -> str:
    texto = (valor or "").strip() if isinstance(valor, str) else ""
    if not texto:
        raise NoSePuede(falta)
    if len(texto) > largo:
        raise NoSePuede(f"Es muy largo: máximo {largo} caracteres")
    return texto


def _filas(conn, sql, args=()) -> list[dict]:
    return [dict(f) for f in conn.execute(sql, args).fetchall()]


# ── recordatorios ────────────────────────────────────────────────────────────

def _fecha(valor) -> str:
    try:
        return date.fromisoformat(str(valor or "").strip()).isoformat()
    except ValueError:
        raise NoSePuede("La fecha va como AAAA-MM-DD")


def recordatorios(db_path: str) -> dict:
    """Los pendientes hasta dentro de una semana, los vencidos primero."""
    hoy = _hoy()
    hasta = (hoy + timedelta(days=DIAS_A_LA_VISTA)).isoformat()
    conn = _connect(db_path)
    try:
        filas = _filas(conn,
            "SELECT id, agente, que, contacto, cita, cuando, borrador_pedido, borrador, creado_en "
            "FROM oficina_recordatorios WHERE estado = 'pendiente' AND cuando <= ? "
            "ORDER BY cuando, id", (hasta,))
    finally:
        conn.close()
    return {"hoy": hoy.isoformat(), "recordatorios": filas}


def resolver_recordatorio(db_path: str, rec_id: int, accion: str) -> None:
    """Lo que toca Juan: 'hecho', 'manana' (que vuelva mañana) o 'borrador'
    (que Bruno le arme el mensaje para mandar)."""
    if accion not in ("hecho", "manana", "borrador"):
        raise NoSePuede("Opción inválida")
    conn = _connect(db_path)
    try:
        if accion == "hecho":
            cur = conn.execute(
                "UPDATE oficina_recordatorios SET estado = 'hecho', resuelto_en = ? "
                "WHERE id = ? AND estado = 'pendiente'", (_ahora(), rec_id))
        elif accion == "manana":
            cur = conn.execute(
                "UPDATE oficina_recordatorios SET cuando = ? WHERE id = ? AND estado = 'pendiente'",
                ((_hoy() + timedelta(days=1)).isoformat(), rec_id))
        else:
            cur = conn.execute(
                "UPDATE oficina_recordatorios SET borrador_pedido = 1 WHERE id = ? AND estado = 'pendiente'",
                (rec_id,))
        conn.commit()
    finally:
        conn.close()
    if not cur.rowcount:
        raise NoSePuede("Ese recordatorio ya no está pendiente")


def crear_recordatorio(db_path: str, datos: dict) -> dict:
    """Lo usa Bruno. Si ya existe uno con la misma clave, no lo repite."""
    que = _texto(datos.get("que"), LARGO_RECORDATORIO, "Falta qué hay que hacer")
    cuando = _fecha(datos.get("cuando"))
    clave = str(datos.get("clave") or "").strip()[:200] or None
    contacto = str(datos.get("contacto") or "").strip()[:120] or None
    cita = str(datos.get("cita") or "").strip()[:LARGO_CITA] or None
    conn = _connect(db_path)
    try:
        if clave:
            previo = conn.execute("SELECT id FROM oficina_recordatorios WHERE clave = ?", (clave,)).fetchone()
            if previo:
                return {"id": previo["id"], "repetido": True}
        cur = conn.execute(
            "INSERT INTO oficina_recordatorios (que, contacto, cita, cuando, clave, creado_en) "
            "VALUES (?, ?, ?, ?, ?, ?)", (que, contacto, cita, cuando, clave, _ahora()))
        conn.commit()
        return {"id": cur.lastrowid, "repetido": False}
    finally:
        conn.close()


def recordatorios_para_bot(db_path: str) -> dict:
    """Los abiertos (para no repetirlos y cerrar los que ya se hicieron) y los
    que esperan que Bruno arme el mensaje."""
    conn = _connect(db_path)
    try:
        abiertos = _filas(conn,
            "SELECT id, que, contacto, cita, cuando, clave FROM oficina_recordatorios "
            "WHERE estado = 'pendiente' ORDER BY id")
        borradores = _filas(conn,
            "SELECT id, que, contacto, cita, cuando FROM oficina_recordatorios "
            "WHERE estado = 'pendiente' AND borrador_pedido = 1 AND borrador IS NULL ORDER BY id")
    finally:
        conn.close()
    return {"hoy": _hoy().isoformat(), "abiertos": abiertos, "borradores_pedidos": borradores}


def guardar_borrador(db_path: str, rec_id: int, texto) -> None:
    texto = _texto(texto, LARGO_BORRADOR, "Falta el texto del mensaje")
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE oficina_recordatorios SET borrador = ? WHERE id = ? AND estado = 'pendiente'",
            (texto, rec_id))
        conn.commit()
    finally:
        conn.close()
    if not cur.rowcount:
        raise NoSePuede("Ese recordatorio ya no está pendiente")


def cerrar_por_bot(db_path: str, rec_id: int) -> None:
    """Bruno vio en el chat que ya se hizo (Juan mandó la propuesta, etc.)."""
    resolver_recordatorio(db_path, rec_id, "hecho")
