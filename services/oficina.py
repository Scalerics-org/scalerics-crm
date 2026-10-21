"""Oficina: el equipo de agentes de Juan (pedido del 20/10).

Cinco agentes con nombre y cara. Juan les escribe desde el CRM, les deja
indicaciones que quedan fijas y les corrige respuestas; cada corrección queda
como "aprendizaje" que el agente tiene en cuenta de ahí en más.

Las respuestas NO las escribe un modelo pago: las escribe una sesión de Claude
Code programada en la compu de Juan, que lee los pendientes por
/api/oficina-bot/ (scripts/oficina_responder.py) con todo el contexto del
agente (indicaciones, aprendizajes, últimos mensajes) y devuelve la respuesta.
Por eso tarda unos minutos. Juan pidió que sea gratis.

Solo admin. Mateo (marketing) solo mira: la pauta la maneja Andrés.
"""

import sqlite3
from datetime import date, datetime, timedelta, timezone

from database import _connect

AGENTES = {
    "sofia": {
        "nombre": "Sofía", "rol": "Jefa de gabinete",
        "descripcion": "Junta lo de todos y te dice qué tenés que decidir.",
        "mira": "Los informes de los demás agentes, el calendario y el Daily Admin.",
    },
    "tomas": {
        "nombre": "Tomás", "rol": "Comercial",
        "descripcion": "Mira el pipeline, Outbound y lo que carga Lucas. Te avisa qué lead se enfría.",
        "mira": "Seguimiento de leads, proceso de venta, Outbound e Inteligencia comercial.",
    },
    "lucia": {
        "nombre": "Lucía", "rol": "Finanzas",
        "descripcion": "Caja, cobros, fijos y Plexo. Te avisa antes de que algo venza.",
        "mira": "Finanzas (movimientos, fijos, cobros y pagos) y métricas financieras.",
    },
    "mateo": {
        "nombre": "Mateo", "rol": "Marketing · solo mira",
        "descripcion": "Lee Meta, Instagram y LinkedIn. La pauta la maneja Andrés: Mateo no toca nada.",
        "mira": "Meta Ads, recomendaciones de pauta, Instagram y LinkedIn. Solo lectura.",
    },
    "bruno": {
        "nombre": "Bruno", "rol": "WhatsApp y mails",
        "descripcion": "Lee tus chats y contacto@, arma recordatorios y borradores. Nunca manda nada solo.",
        "mira": "Tus chats de WhatsApp (menos los que dejaste afuera) y el mail de contacto@.",
    },
}

LARGO_MENSAJE = 2000
LARGO_RESPUESTA = 6000
LARGO_INDICACION = 300
LARGO_APRENDIZAJE = 300
TOPE_HISTORIAL = 40
CONTEXTO_BOT = 10
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oficina_mensajes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            agente        TEXT NOT NULL,
            autor         TEXT NOT NULL,              -- 'juan' | 'agente'
            texto         TEXT NOT NULL,
            estado        TEXT,                       -- de Juan: 'pendiente' | 'respondido'
            responde_a    INTEGER,                    -- del agente: id del mensaje de Juan
            fuente        TEXT,
            feedback      TEXT,                       -- del agente: 'ok' | 'corregido'
            creado_en     TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS ix_oficina_mensajes_agente ON oficina_mensajes(agente, id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oficina_indicaciones (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            agente      TEXT NOT NULL,
            texto       TEXT NOT NULL,
            creado_en   TEXT NOT NULL,
            borrado_en  TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oficina_aprendizajes (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            agente       TEXT NOT NULL,
            texto        TEXT NOT NULL,
            origen       TEXT NOT NULL,               -- de dónde salió, para mostrarle a Juan
            mensaje_id   INTEGER,
            creado_en    TEXT NOT NULL,
            olvidado_en  TEXT
        )
    """)


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


def _agente(agente: str) -> str:
    if agente not in AGENTES:
        raise NoSePuede("Ese agente no existe")
    return agente


def _texto(valor, largo: int, falta: str) -> str:
    texto = (valor or "").strip() if isinstance(valor, str) else ""
    if not texto:
        raise NoSePuede(falta)
    if len(texto) > largo:
        raise NoSePuede(f"Es muy largo: máximo {largo} caracteres")
    return texto


def _filas(conn, sql, args=()) -> list[dict]:
    return [dict(f) for f in conn.execute(sql, args).fetchall()]


# ── lo que ve Juan ───────────────────────────────────────────────────────────

def listar_agentes(db_path: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        pend = dict(conn.execute(
            "SELECT agente, COUNT(*) FROM oficina_mensajes "
            "WHERE autor = 'juan' AND estado = 'pendiente' GROUP BY agente").fetchall())
        ind = dict(conn.execute(
            "SELECT agente, COUNT(*) FROM oficina_indicaciones WHERE borrado_en IS NULL "
            "GROUP BY agente").fetchall())
        apr = dict(conn.execute(
            "SELECT agente, COUNT(*) FROM oficina_aprendizajes WHERE olvidado_en IS NULL "
            "GROUP BY agente").fetchall())
    finally:
        conn.close()
    return [{"id": k, **v, "pendientes": pend.get(k, 0), "indicaciones": ind.get(k, 0),
             "aprendizajes": apr.get(k, 0)} for k, v in AGENTES.items()]


def ficha(db_path: str, agente: str) -> dict:
    """Todo lo de un agente: conversación, indicaciones vigentes y aprendizajes."""
    _agente(agente)
    conn = _connect(db_path)
    try:
        mensajes = _filas(conn,
            "SELECT id, autor, texto, estado, responde_a, fuente, feedback, creado_en "
            "FROM oficina_mensajes WHERE agente = ? ORDER BY id DESC LIMIT ?",
            (agente, TOPE_HISTORIAL))
        indicaciones = _filas(conn,
            "SELECT id, texto, creado_en FROM oficina_indicaciones "
            "WHERE agente = ? AND borrado_en IS NULL ORDER BY id DESC", (agente,))
        aprendizajes = _filas(conn,
            "SELECT id, texto, origen, creado_en FROM oficina_aprendizajes "
            "WHERE agente = ? AND olvidado_en IS NULL ORDER BY id DESC", (agente,))
    finally:
        conn.close()
    return {"agente": {"id": agente, **AGENTES[agente]}, "mensajes": mensajes[::-1],
            "indicaciones": indicaciones, "aprendizajes": aprendizajes}


def preguntar(db_path: str, agente: str, texto) -> int:
    _agente(agente)
    texto = _texto(texto, LARGO_MENSAJE, "Escribí algo antes de enviar")
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO oficina_mensajes (agente, autor, texto, estado, creado_en) "
            "VALUES (?, 'juan', ?, 'pendiente', ?)", (agente, texto, _ahora()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def agregar_indicacion(db_path: str, agente: str, texto) -> int:
    _agente(agente)
    texto = _texto(texto, LARGO_INDICACION, "Escribí la indicación")
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO oficina_indicaciones (agente, texto, creado_en) VALUES (?, ?, ?)",
            (agente, texto, _ahora()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def borrar_indicacion(db_path: str, ind_id: int) -> None:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE oficina_indicaciones SET borrado_en = ? WHERE id = ? AND borrado_en IS NULL",
            (_ahora(), ind_id))
        conn.commit()
    finally:
        conn.close()
    if not cur.rowcount:
        raise NoSePuede("Esa indicación no existe o ya estaba borrada")


def olvidar(db_path: str, apr_id: int) -> None:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE oficina_aprendizajes SET olvidado_en = ? WHERE id = ? AND olvidado_en IS NULL",
            (_ahora(), apr_id))
        conn.commit()
    finally:
        conn.close()
    if not cur.rowcount:
        raise NoSePuede("Eso ya estaba olvidado")


def opinar(db_path: str, mensaje_id: int, tipo: str, correccion=None) -> None:
    """Juan marca una respuesta: 'ok' (le sirvió) o 'corregir' (con qué tendría
    que haber hecho). La corrección queda como aprendizaje del agente."""
    if tipo not in ("ok", "corregir"):
        raise NoSePuede("Opción inválida")
    if tipo == "corregir":
        correccion = _texto(correccion, LARGO_APRENDIZAJE, "Escribí qué tendría que haber hecho")
    conn = _connect(db_path)
    try:
        msj = conn.execute(
            "SELECT id, agente, autor FROM oficina_mensajes WHERE id = ?", (mensaje_id,)).fetchone()
        if not msj or msj["autor"] != "agente":
            raise NoSePuede("Esa respuesta no existe")
        ahora = _ahora()
        conn.execute("UPDATE oficina_mensajes SET feedback = ? WHERE id = ?",
                     ("ok" if tipo == "ok" else "corregido", mensaje_id))
        if tipo == "corregir":
            conn.execute(
                "INSERT INTO oficina_aprendizajes (agente, texto, origen, mensaje_id, creado_en) "
                "VALUES (?, ?, 'Lo corregiste en una respuesta', ?, ?)",
                (msj["agente"], correccion, mensaje_id, ahora))
        conn.commit()
    finally:
        conn.close()


# ── lo que usa la sesión de Claude Code ──────────────────────────────────────

def pendientes(db_path: str) -> list[dict]:
    """Cada pregunta sin responder con el contexto que necesita el agente."""
    conn = _connect(db_path)
    try:
        preguntas = _filas(conn,
            "SELECT id, agente, texto, creado_en FROM oficina_mensajes "
            "WHERE autor = 'juan' AND estado = 'pendiente' ORDER BY id")
        salida = []
        for p in preguntas:
            if p["agente"] not in AGENTES:
                continue
            ag = p["agente"]
            previos = _filas(conn,
                "SELECT autor, texto, creado_en FROM oficina_mensajes "
                "WHERE agente = ? AND id < ? ORDER BY id DESC LIMIT ?", (ag, p["id"], CONTEXTO_BOT))
            salida.append({
                "id": p["id"], "pregunta": p["texto"], "creado_en": p["creado_en"],
                "agente": {"id": ag, **AGENTES[ag]},
                "indicaciones": [f["texto"] for f in _filas(conn,
                    "SELECT texto FROM oficina_indicaciones WHERE agente = ? AND borrado_en IS NULL "
                    "ORDER BY id", (ag,))],
                "aprendizajes": [f["texto"] for f in _filas(conn,
                    "SELECT texto FROM oficina_aprendizajes WHERE agente = ? AND olvidado_en IS NULL "
                    "ORDER BY id", (ag,))],
                "conversacion_previa": previos[::-1],
            })
    finally:
        conn.close()
    return salida


def responder(db_path: str, pregunta_id: int, texto, fuente=None) -> int:
    texto = _texto(texto, LARGO_RESPUESTA, "Falta el texto de la respuesta")
    fuente = (fuente or "").strip()[:200] if isinstance(fuente, str) else ""
    conn = _connect(db_path)
    try:
        p = conn.execute(
            "SELECT agente FROM oficina_mensajes WHERE id = ? AND autor = 'juan' AND estado = 'pendiente'",
            (pregunta_id,)).fetchone()
        if not p:
            raise NoSePuede("Esa pregunta no existe o ya está respondida")
        conn.execute("UPDATE oficina_mensajes SET estado = 'respondido' WHERE id = ?", (pregunta_id,))
        cur = conn.execute(
            "INSERT INTO oficina_mensajes (agente, autor, texto, responde_a, fuente, creado_en) "
            "VALUES (?, 'agente', ?, ?, ?, ?)", (p["agente"], texto, pregunta_id, fuente, _ahora()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


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
