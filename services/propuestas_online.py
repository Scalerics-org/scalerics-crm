"""Propuesta online: el presupuesto como link para el cliente, con seguimiento
y aceptación (23/10).

Hoy el presupuesto es un HTML adjunto en la ficha del lead que se manda como
PDF, y después de mandarlo no sabemos nada hasta que el cliente contesta. Acá
se publica ese HTML en un link (`/p/<código>`) que:

- congela una copia del documento: lo que el cliente acepta es exactamente lo
  que se le mandó, aunque después se edite el adjunto (con su sha256);
- cuenta las aperturas y el tiempo de lectura, sin contar las del equipo;
- deja aceptar con nombre y tilde ("firma" escrita, como Qwilr o PandaDoc), o
  decir que no con un motivo, y deja todo en el historial del lead.

Las rutas están en routes/propuestas_online.py. Las públicas no llevan sesión:
la única llave es el código al azar del link, así que nada de lo que reciben
se usa sin pasar por `por_codigo`.

No se guarda la IP de nadie: solo un hash con la SECRET_KEY, que alcanza para
no contar dos veces la misma visita y para dejar constancia de la aceptación.
"""

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from database import _connect

FORMATO = "%Y-%m-%d %H:%M:%S"

DIAS_VIGENCIA = 30
DIAS_MAXIMO = 120
# Dos aperturas desde el mismo lugar dentro de esta ventana son una sola visita.
VENTANA_VISITA = timedelta(minutes=30)
# La página manda un latido cada PASO_LATIDO segundos mientras está a la vista.
PASO_LATIDO = 15
# Tope de lectura por propuesta: una pestaña olvidada no es interés.
TOPE_SEGUNDOS = 4 * 3600
# Una propuesta abierta hace menos de esto y con interés es "caliente".
VENTANA_CALIENTE = timedelta(hours=72)
LARGO_NOMBRE = 120
LARGO_EMAIL = 160
LARGO_MOTIVO = 1000
LARGO_UA = 200

ESTADOS = ("activa", "aceptada", "rechazada", "revocada")


class NoSePuede(ValueError):
    """Lo que se pidió no se puede hacer; el mensaje se muestra tal cual."""


def init_propuestas_online(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS propuestas_online (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo          TEXT NOT NULL UNIQUE,
            lead_id         INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            attachment_id   INTEGER,
            titulo          TEXT NOT NULL,
            html            BLOB NOT NULL,
            sha256          TEXT NOT NULL,
            monto           REAL,
            moneda          TEXT NOT NULL DEFAULT 'USD',
            estado          TEXT NOT NULL DEFAULT 'activa',
            creado_por      TEXT NOT NULL,
            creado_en       TEXT NOT NULL,
            vence_en        TEXT NOT NULL,
            vistas          INTEGER NOT NULL DEFAULT 0,
            segundos        INTEGER NOT NULL DEFAULT 0,
            primera_vista   TEXT,
            ultima_vista    TEXT,
            resuelta_en     TEXT,
            firma_nombre    TEXT,
            firma_email     TEXT,
            firma_ip        TEXT,
            motivo          TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_propuestas_online_lead "
                 "ON propuestas_online(lead_id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS propuestas_online_eventos (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            propuesta_id    INTEGER NOT NULL REFERENCES propuestas_online(id) ON DELETE CASCADE,
            tipo            TEXT NOT NULL,
            en              TEXT NOT NULL,
            ip              TEXT,
            ua              TEXT,
            detalle         TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_propuestas_online_eventos "
                 "ON propuestas_online_eventos(propuesta_id, en)")


# ── utilidades ───────────────────────────────────────────────────────────────

def _ahora() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _txt(dt: datetime) -> str:
    return dt.strftime(FORMATO)


def _dt(texto: Optional[str]) -> Optional[datetime]:
    if not texto:
        return None
    return datetime.strptime(texto, FORMATO).replace(tzinfo=timezone.utc)


def huella_ip(ip: Optional[str]) -> Optional[str]:
    """La IP no se guarda: solo un hash con la SECRET_KEY."""
    if not ip:
        return None
    clave = (os.environ.get("SECRET_KEY") or "scalerics-dev-key-change-in-prod").encode()
    return hmac.new(clave, ip.encode(), hashlib.sha256).hexdigest()[:16]


def _texto(valor, largo: int) -> Optional[str]:
    texto = " ".join(valor.split()) if isinstance(valor, str) else ""
    return texto[:largo] or None


def _evento(conn, pid: int, tipo: str, en: datetime, ip=None, ua=None, detalle=None) -> None:
    conn.execute(
        "INSERT INTO propuestas_online_eventos (propuesta_id, tipo, en, ip, ua, detalle) "
        "VALUES (?,?,?,?,?,?)",
        (pid, tipo, _txt(en), ip, _texto(ua, LARGO_UA), detalle))


# ── publicar ─────────────────────────────────────────────────────────────────

def publicar(db: str, lead_id: int, html: bytes, titulo: str, quien: str,
             attachment_id: Optional[int] = None, monto=None,
             dias: int = DIAS_VIGENCIA) -> dict:
    """Congela una copia del presupuesto y le da un link. Cada publicación es
    un link nuevo: el anterior sigue andando hasta que se revoque o venza."""
    if not html:
        raise NoSePuede("El presupuesto está vacío")
    try:
        dias = int(dias)
    except (TypeError, ValueError):
        raise NoSePuede("La vigencia tiene que ser un número de días")
    if not 1 <= dias <= DIAS_MAXIMO:
        raise NoSePuede(f"La vigencia va de 1 a {DIAS_MAXIMO} días")
    if monto in ("", None):
        monto = None
    else:
        try:
            monto = round(float(monto), 2)
        except (TypeError, ValueError):
            raise NoSePuede("El monto tiene que ser un número")
        if monto < 0:
            raise NoSePuede("El monto no puede ser negativo")
    ahora = _ahora()
    codigo = secrets.token_urlsafe(18)
    conn = _connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO propuestas_online (codigo, lead_id, attachment_id, titulo, html, sha256, "
            "monto, creado_por, creado_en, vence_en) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (codigo, lead_id, attachment_id, _texto(titulo, 200) or "Presupuesto", html,
             hashlib.sha256(html).hexdigest(), monto, quien or "sistema", _txt(ahora),
             _txt(ahora + timedelta(days=dias))))
        _evento(conn, cur.lastrowid, "publicada", ahora, detalle=quien)
        conn.commit()
        return {"id": cur.lastrowid, "codigo": codigo}
    finally:
        conn.close()


# ── leer ─────────────────────────────────────────────────────────────────────

_COLUMNAS = ("p.id, p.codigo, p.lead_id, p.attachment_id, p.titulo, p.sha256, p.monto, "
             "p.moneda, p.estado, p.creado_por, p.creado_en, p.vence_en, p.vistas, "
             "p.segundos, p.primera_vista, p.ultima_vista, p.resuelta_en, p.firma_nombre, "
             "p.firma_email, p.motivo, b.name AS lead_nombre")


def senal(p: dict, ahora: Optional[datetime] = None) -> str:
    """En qué está la propuesta, para ordenar a quién llamar primero.

    aceptada / rechazada / revocada / vencida: ya se resolvió.
    caliente: la abrió hace poco y volvió, o la leyó un buen rato.
    vista: la abrió. sin_abrir: todavía no.
    """
    ahora = ahora or _ahora()
    if p["estado"] != "activa":
        return p["estado"]
    if _dt(p["vence_en"]) <= ahora:
        return "vencida"
    if not p["vistas"]:
        return "sin_abrir"
    reciente = _dt(p["ultima_vista"]) >= ahora - VENTANA_CALIENTE
    if reciente and (p["vistas"] >= 2 or p["segundos"] >= 180):
        return "caliente"
    return "vista"


def _con_senal(fila, ahora) -> dict:
    p = dict(fila)
    p["senal"] = senal(p, ahora)
    return p


def por_codigo(db: str, codigo: str) -> Optional[dict]:
    """La propuesta de un link, con el HTML. None si el código no existe."""
    if not isinstance(codigo, str) or not 10 <= len(codigo) <= 64:
        return None
    conn = _connect(db)
    try:
        fila = conn.execute(
            f"SELECT {_COLUMNAS}, p.html FROM propuestas_online p "
            "LEFT JOIN businesses b ON b.id = p.lead_id WHERE p.codigo = ?", (codigo,)).fetchone()
    finally:
        conn.close()
    if not fila or not hmac.compare_digest(fila["codigo"], codigo):
        return None
    return _con_senal(fila, _ahora())


def listar(db: str, lead_id: Optional[int] = None, limite: int = 300) -> list[dict]:
    sql = f"SELECT {_COLUMNAS} FROM propuestas_online p LEFT JOIN businesses b ON b.id = p.lead_id"
    args: tuple = ()
    if lead_id is not None:
        sql += " WHERE p.lead_id = ?"
        args = (lead_id,)
    sql += " ORDER BY p.id DESC LIMIT ?"
    conn = _connect(db)
    try:
        filas = conn.execute(sql, args + (limite,)).fetchall()
    finally:
        conn.close()
    ahora = _ahora()
    return [_con_senal(f, ahora) for f in filas]


def ver(db: str, pid: int) -> dict:
    conn = _connect(db)
    try:
        fila = conn.execute(
            f"SELECT {_COLUMNAS} FROM propuestas_online p "
            "LEFT JOIN businesses b ON b.id = p.lead_id WHERE p.id = ?", (pid,)).fetchone()
        if not fila:
            raise NoSePuede("Esa propuesta no existe")
        eventos = conn.execute(
            "SELECT tipo, en, detalle FROM propuestas_online_eventos "
            "WHERE propuesta_id = ? AND tipo != 'lectura' ORDER BY id DESC LIMIT 100",
            (pid,)).fetchall()
    finally:
        conn.close()
    p = _con_senal(fila, _ahora())
    p["eventos"] = [dict(e) for e in eventos]
    return p


def resumen(propuestas: list[dict]) -> dict:
    """Los números de arriba del panel: cuántas, cuántas se abrieron, cuánta
    plata se aceptó y cuánto tardan en contestar."""
    enviadas = len(propuestas)
    abiertas = sum(1 for p in propuestas if p["vistas"])
    aceptadas = [p for p in propuestas if p["estado"] == "aceptada"]
    resueltas = [p for p in propuestas if p["estado"] in ("aceptada", "rechazada")]
    horas = sorted((_dt(p["resuelta_en"]) - _dt(p["creado_en"])).total_seconds() / 3600
                   for p in resueltas if p["resuelta_en"])
    conteo = {}
    for p in propuestas:
        conteo[p["senal"]] = conteo.get(p["senal"], 0) + 1
    return {
        "enviadas": enviadas,
        "abiertas": abiertas,
        "aceptadas": len(aceptadas),
        "tasa_apertura": round(abiertas / enviadas, 3) if enviadas else None,
        "tasa_aceptacion": round(len(aceptadas) / len(resueltas), 3) if resueltas else None,
        "monto_aceptado": round(sum(p["monto"] or 0 for p in aceptadas), 2),
        "monto_abierto": round(sum(p["monto"] or 0 for p in propuestas
                                   if p["senal"] in ("sin_abrir", "vista", "caliente")), 2),
        "horas_a_respuesta": round(horas[len(horas) // 2], 1) if horas else None,
        "por_senal": conteo,
    }


# ── lo que hace el cliente ───────────────────────────────────────────────────

def registrar_vista(db: str, p: dict, ip: Optional[str], ua: Optional[str],
                    interna: bool = False) -> bool:
    """Suma una visita. No cuenta las del equipo (con sesión del CRM), ni las
    de una propuesta ya resuelta, ni repetir desde el mismo lugar enseguida.
    Devuelve si contó."""
    if interna or p["senal"] not in ("sin_abrir", "vista", "caliente"):
        return False
    ahora = _ahora()
    huella = huella_ip(ip)
    conn = _connect(db)
    try:
        if huella and conn.execute(
                "SELECT 1 FROM propuestas_online_eventos WHERE propuesta_id = ? AND tipo = 'vista' "
                "AND ip = ? AND en >= ?",
                (p["id"], huella, _txt(ahora - VENTANA_VISITA))).fetchone():
            return False
        conn.execute(
            "UPDATE propuestas_online SET vistas = vistas + 1, ultima_vista = ?, "
            "primera_vista = COALESCE(primera_vista, ?) WHERE id = ?",
            (_txt(ahora), _txt(ahora), p["id"]))
        _evento(conn, p["id"], "vista", ahora, huella, ua)
        conn.commit()
        return True
    finally:
        conn.close()


def registrar_lectura(db: str, p: dict, ip: Optional[str], interna: bool = False) -> bool:
    """Un latido de la página abierta y a la vista: suma PASO_LATIDO segundos.

    Para que un script no infle el número, solo cuenta si hubo una visita de
    ese mismo lugar en la última hora y no hubo otro latido de ahí en los
    últimos PASO_LATIDO - 3 segundos."""
    if interna or p["senal"] not in ("vista", "caliente"):
        return False
    ahora = _ahora()
    huella = huella_ip(ip)
    if not huella:
        return False
    conn = _connect(db)
    try:
        if not conn.execute(
                "SELECT 1 FROM propuestas_online_eventos WHERE propuesta_id = ? AND tipo = 'vista' "
                "AND ip = ? AND en >= ?",
                (p["id"], huella, _txt(ahora - timedelta(hours=1)))).fetchone():
            return False
        if conn.execute(
                "SELECT 1 FROM propuestas_online_eventos WHERE propuesta_id = ? AND tipo = 'lectura' "
                "AND ip = ? AND en > ?",
                (p["id"], huella, _txt(ahora - timedelta(seconds=PASO_LATIDO - 3)))).fetchone():
            return False
        cur = conn.execute(
            "UPDATE propuestas_online SET segundos = MIN(?, segundos + ?), ultima_vista = ? "
            "WHERE id = ? AND segundos < ?",
            (TOPE_SEGUNDOS, PASO_LATIDO, _txt(ahora), p["id"], TOPE_SEGUNDOS))
        if not cur.rowcount:
            return False
        _evento(conn, p["id"], "lectura", ahora, huella)
        conn.commit()
        return True
    finally:
        conn.close()


def _resolver(db: str, p: dict, estado: str, campos: dict, ip, ua, detalle) -> dict:
    if p["estado"] == estado:
        return p  # doble click o volver a mandar el formulario: ya está
    if p["senal"] not in ("sin_abrir", "vista", "caliente"):
        raise NoSePuede({
            "aceptada": "Esta propuesta ya fue aceptada.",
            "rechazada": "Esta propuesta ya fue respondida.",
            "revocada": "Esta propuesta ya no está disponible.",
            "vencida": "Esta propuesta venció. Pedinos una actualizada.",
        }[p["senal"]])
    ahora = _ahora()
    huella = huella_ip(ip)
    sets = ", ".join(f"{k} = ?" for k in campos)
    conn = _connect(db)
    try:
        # El WHERE estado='activa' es la guarda contra dos respuestas a la vez.
        cur = conn.execute(
            f"UPDATE propuestas_online SET estado = ?, resuelta_en = ?, firma_ip = ?{', ' + sets if sets else ''} "
            "WHERE id = ? AND estado = 'activa'",
            (estado, _txt(ahora), huella, *campos.values(), p["id"]))
        if not cur.rowcount:
            raise NoSePuede("Esta propuesta ya fue respondida.")
        _evento(conn, p["id"], estado, ahora, huella, ua, detalle)
        conn.commit()
    finally:
        conn.close()
    return {**p, "estado": estado, "senal": estado, "resuelta_en": _txt(ahora), **campos}


def aceptar(db: str, p: dict, nombre, email, acepto, ip=None, ua=None) -> dict:
    nombre = _texto(nombre, LARGO_NOMBRE)
    email = _texto(email, LARGO_EMAIL)
    if not nombre or len(nombre) < 3:
        raise NoSePuede("Escribí tu nombre completo para aceptar.")
    if email and ("@" not in email or " " in email):
        raise NoSePuede("Ese email no parece válido.")
    if acepto is not True:
        raise NoSePuede("Tenés que marcar que leíste y aceptás la propuesta.")
    return _resolver(db, p, "aceptada", {"firma_nombre": nombre, "firma_email": email},
                     ip, ua, f"{nombre} · sha256 {p['sha256'][:12]}")


def rechazar(db: str, p: dict, motivo, ip=None, ua=None) -> dict:
    motivo = _texto(motivo, LARGO_MOTIVO)
    return _resolver(db, p, "rechazada", {"motivo": motivo}, ip, ua, motivo)


def revocar(db: str, pid: int, quien: str) -> dict:
    p = ver(db, pid)
    if p["estado"] != "activa":
        raise NoSePuede("Solo se puede dar de baja una propuesta que sigue abierta")
    ahora = _ahora()
    conn = _connect(db)
    try:
        conn.execute("UPDATE propuestas_online SET estado = 'revocada', resuelta_en = ? "
                     "WHERE id = ? AND estado = 'activa'", (_txt(ahora), pid))
        _evento(conn, pid, "revocada", ahora, detalle=quien)
        conn.commit()
    finally:
        conn.close()
    return ver(db, pid)
