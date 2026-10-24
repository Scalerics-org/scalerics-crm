"""Agenda online: reservas con link para comercios con turnos (24/10).

Un producto para vender a peluquerías, barberías, estéticas, clínicas,
veterinarias y talleres: hoy toman los turnos por WhatsApp, a mano, en una
libreta o en una planilla, contestando a cualquier hora "¿tenés lugar el
jueves?". Esto lo resuelve como Fresha, Booksy, AgendaPro o Calendly:

- el comercio configura servicios (duración y precio), profesionales, horario
  de cada uno (con turnos partidos) y bloqueos (feriados, vacaciones);
- su cliente entra a `/reservar/<slug>` desde el celular, elige servicio,
  profesional (o "el primero libre"), día y hora, deja nombre y teléfono, y
  recibe un link propio para ver, cambiar o cancelar su turno;
- el comercio ve la agenda del día por profesional, marca quién vino y quién
  faltó, carga los turnos que le llegan por teléfono y manda el recordatorio
  por WhatsApp con un toque (un link wa.me con el texto armado: no se manda
  nada solo);
- las métricas dicen ocupación, ausencias, de dónde salen las reservas y
  cuánto hay agendado.

Las horas son de Montevideo y se guardan así ('YYYY-MM-DD HH:MM'): Uruguay no
tiene horario de verano desde 2015, así que la hora local es UTC-3 fija. Las
rutas están en routes/agenda.py. Nada de esto le manda nada a nadie.
"""

import json
import re
import secrets
import sqlite3
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

from database import _connect

UY = timedelta(hours=-3)
FORMATO = "%Y-%m-%d %H:%M"
DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
DIAS_CORTO = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]

ESTADOS = ("confirmado", "asistio", "falto", "cancelado")
# Los que ocupan el lugar en la agenda.
OCUPAN = ("confirmado", "asistio", "falto")
INTERVALOS = (10, 15, 20, 30, 60)

LARGO_NOMBRE = 120
LARGO_TEXTO = 500
LARGO_MENSAJE = 1000
MAX_DURACION = 8 * 60
# Topes del link público: un comercio chico no llega y frenan a quien lo
# quiera llenar de turnos falsos.
MAX_RESERVAS_DIA = 120
MAX_FUTUROS_POR_TEL = 3


class NoSePuede(ValueError):
    """Lo que se pidió no se puede hacer; el mensaje se muestra tal cual."""


# ── tiempo ───────────────────────────────────────────────────────────────────

def ahora_uy() -> datetime:
    return (datetime.now(timezone.utc) + UY).replace(tzinfo=None, second=0, microsecond=0)


def hoy_uy() -> date:
    return ahora_uy().date()


def _txt(d: datetime) -> str:
    return d.strftime(FORMATO)


def _dt(s) -> Optional[datetime]:
    try:
        return datetime.strptime(str(s or "")[:16], FORMATO)
    except ValueError:
        return None


def _fecha(s) -> date:
    try:
        return datetime.strptime(str(s or ""), "%Y-%m-%d").date()
    except ValueError:
        raise NoSePuede("La fecha no es válida.")


def _hhmm(s) -> int:
    """'09:30' a minutos desde la medianoche."""
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(s or "").strip())
    if not m:
        raise NoSePuede(f"La hora {s!r} no es válida: usá HH:MM.")
    return int(m.group(1)) * 60 + int(m.group(2))


def _min_a_hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def fecha_larga(d: date) -> str:
    return f"{DIAS[d.weekday()].lower()} {d.day} de {MESES[d.month - 1]}"


# ── texto ────────────────────────────────────────────────────────────────────

def _limpio(v, largo: int) -> str:
    return re.sub(r"[ \t]+", " ", str(v or "").replace("\r\n", "\n")).strip()[:largo]


def _slug(texto: str) -> str:
    s = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40] or "agenda"


def tel_normal(tel) -> str:
    """El teléfono como lo entiende WhatsApp: 59899123456. Acepta 099 123 456."""
    d = re.sub(r"\D", "", str(tel or ""))
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("09") and len(d) == 9:
        d = "598" + d[1:]
    elif d.startswith("9") and len(d) == 8:
        d = "598" + d
    if not 8 <= len(d) <= 15:
        raise NoSePuede("Poné un teléfono válido, por ejemplo 099 123 456.")
    return d


def _num(v, nombre: str, minimo: float = 0, maximo: float = 10 ** 7, entero: bool = False):
    if v in (None, ""):
        return None
    try:
        n = float(str(v).replace(",", "."))
    except ValueError:
        raise NoSePuede(f"{nombre}: tiene que ser un número.")
    if not minimo <= n <= maximo:
        raise NoSePuede(f"{nombre}: entre {minimo:g} y {maximo:g}.")
    return int(n) if entero else n


def _color(v) -> str:
    v = str(v or "").strip()
    return v.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", v) else "#0088cc"


# ── tablas ───────────────────────────────────────────────────────────────────

def init_agenda(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS rv_negocios (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            slug              TEXT NOT NULL UNIQUE,
            nombre            TEXT NOT NULL,
            rubro             TEXT,
            direccion         TEXT,
            whatsapp          TEXT,
            mensaje           TEXT,
            color             TEXT NOT NULL DEFAULT '#0088cc',
            intervalo_min     INTEGER NOT NULL DEFAULT 30,
            anticipacion_min  INTEGER NOT NULL DEFAULT 120,
            dias_max          INTEGER NOT NULL DEFAULT 30,
            cancelar_hasta_h  INTEGER NOT NULL DEFAULT 3,
            business_id       INTEGER,
            activo            INTEGER NOT NULL DEFAULT 1,
            creado_por        TEXT,
            creado_en         TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rv_servicios (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            negocio_id    INTEGER NOT NULL REFERENCES rv_negocios(id) ON DELETE CASCADE,
            nombre        TEXT NOT NULL,
            descripcion   TEXT,
            duracion_min  INTEGER NOT NULL CHECK (duracion_min > 0),
            precio        REAL,
            activo        INTEGER NOT NULL DEFAULT 1,
            orden         INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS rv_profesionales (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            negocio_id  INTEGER NOT NULL REFERENCES rv_negocios(id) ON DELETE CASCADE,
            nombre      TEXT NOT NULL,
            servicios   TEXT NOT NULL DEFAULT '[]',
            activo      INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS rv_horarios (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            profesional_id  INTEGER NOT NULL REFERENCES rv_profesionales(id) ON DELETE CASCADE,
            dia             INTEGER NOT NULL CHECK (dia BETWEEN 0 AND 6),
            desde           INTEGER NOT NULL,
            hasta           INTEGER NOT NULL,
            CHECK (hasta > desde)
        );
        CREATE TABLE IF NOT EXISTS rv_bloqueos (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            negocio_id      INTEGER NOT NULL REFERENCES rv_negocios(id) ON DELETE CASCADE,
            profesional_id  INTEGER,
            desde           TEXT NOT NULL,
            hasta           TEXT NOT NULL,
            motivo          TEXT
        );
        CREATE TABLE IF NOT EXISTS rv_turnos (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            negocio_id      INTEGER NOT NULL REFERENCES rv_negocios(id) ON DELETE CASCADE,
            profesional_id  INTEGER NOT NULL,
            servicio_id     INTEGER NOT NULL,
            inicio          TEXT NOT NULL,
            fin             TEXT NOT NULL,
            cliente         TEXT NOT NULL,
            telefono        TEXT NOT NULL,
            nota            TEXT,
            precio          REAL,
            estado          TEXT NOT NULL DEFAULT 'confirmado',
            codigo          TEXT NOT NULL UNIQUE,
            origen          TEXT NOT NULL DEFAULT 'link',
            creado_por      TEXT,
            creado_en       TEXT NOT NULL,
            cambiado_en     TEXT,
            recordado_en    TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_rv_turnos_agenda ON rv_turnos(negocio_id, inicio);
        CREATE INDEX IF NOT EXISTS ix_rv_turnos_prof ON rv_turnos(profesional_id, inicio);
    """)


# ── negocios ─────────────────────────────────────────────────────────────────

_CAMPOS_NEGOCIO = ("nombre", "rubro", "direccion", "whatsapp", "mensaje", "color", "intervalo_min",
                   "anticipacion_min", "dias_max", "cancelar_hasta_h", "business_id", "activo")


def _validar_negocio(conn, datos: dict, parcial: bool = False) -> dict:
    v = {}
    if "nombre" in datos or not parcial:
        v["nombre"] = _limpio(datos.get("nombre"), LARGO_NOMBRE)
        if not v["nombre"]:
            raise NoSePuede("Poné el nombre del comercio.")
    for k in ("rubro", "direccion"):
        if k in datos:
            v[k] = _limpio(datos.get(k), LARGO_NOMBRE) or None
    if "mensaje" in datos:
        v["mensaje"] = _limpio(datos.get("mensaje"), LARGO_MENSAJE) or None
    if "whatsapp" in datos:
        v["whatsapp"] = tel_normal(datos["whatsapp"]) if str(datos.get("whatsapp") or "").strip() else None
    if "color" in datos:
        v["color"] = _color(datos.get("color"))
    if "intervalo_min" in datos:
        n = _num(datos.get("intervalo_min"), "Cada cuánto arrancan los turnos", 5, 120, entero=True)
        if n not in INTERVALOS:
            raise NoSePuede("Los turnos arrancan cada 10, 15, 20, 30 o 60 minutos.")
        v["intervalo_min"] = n
    for k, nombre, mx in (("anticipacion_min", "Anticipación mínima", 7 * 24 * 60),
                          ("dias_max", "Días hacia adelante", 180),
                          ("cancelar_hasta_h", "Horas para cancelar", 7 * 24)):
        if k in datos:
            n = _num(datos.get(k), nombre, 0, mx, entero=True)
            if n is not None:
                v[k] = n
    if "dias_max" in v and v["dias_max"] < 1:
        raise NoSePuede("Días hacia adelante: al menos 1.")
    if "business_id" in datos:
        bid = _num(datos.get("business_id"), "Cliente", 1, 10 ** 12, entero=True)
        if bid and not conn.execute("SELECT 1 FROM businesses WHERE id = ?", (bid,)).fetchone():
            raise NoSePuede("Ese cliente no existe en el CRM.")
        v["business_id"] = bid
    if "activo" in datos:
        v["activo"] = 1 if datos.get("activo") in (True, 1, "1", "true", "on") else 0
    return v


def crear_negocio(db: str, datos: dict, quien: str) -> dict:
    conn = _connect(db)
    try:
        v = _validar_negocio(conn, datos)
        base = _slug(_limpio(datos.get("slug"), 40) or v["nombre"])
        slug = base
        while conn.execute("SELECT 1 FROM rv_negocios WHERE slug = ?", (slug,)).fetchone():
            slug = f"{base}-{secrets.token_hex(2)}"
        cols = ["slug", "creado_por", "creado_en"] + list(v)
        vals = [slug, quien, _txt(ahora_uy())] + list(v.values())
        cur = conn.execute(f"INSERT INTO rv_negocios ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                           vals)
        conn.commit()
        nid = cur.lastrowid
    finally:
        conn.close()
    return negocio(db, nid)


def editar_negocio(db: str, nid: int, datos: dict) -> dict:
    conn = _connect(db)
    try:
        if not conn.execute("SELECT 1 FROM rv_negocios WHERE id = ?", (nid,)).fetchone():
            raise NoSePuede("Ese comercio no existe.")
        v = _validar_negocio(conn, {k: datos[k] for k in _CAMPOS_NEGOCIO if k in datos}, parcial=True)
        if "slug" in datos:
            slug = _slug(_limpio(datos["slug"], 40))
            if conn.execute("SELECT 1 FROM rv_negocios WHERE slug = ? AND id != ?", (slug, nid)).fetchone():
                raise NoSePuede("Ese link ya lo usa otro comercio.")
            v["slug"] = slug
        if v:
            conn.execute(f"UPDATE rv_negocios SET {', '.join(k + ' = ?' for k in v)} WHERE id = ?",
                         [*v.values(), nid])
            conn.commit()
    finally:
        conn.close()
    return negocio(db, nid)


def borrar_negocio(db: str, nid: int) -> None:
    conn = _connect(db)
    try:
        profs = [r[0] for r in conn.execute("SELECT id FROM rv_profesionales WHERE negocio_id = ?", (nid,))]
        for t in ("rv_turnos", "rv_bloqueos", "rv_servicios"):
            conn.execute(f"DELETE FROM {t} WHERE negocio_id = ?", (nid,))
        conn.executemany("DELETE FROM rv_horarios WHERE profesional_id = ?", [(p,) for p in profs])
        conn.execute("DELETE FROM rv_profesionales WHERE negocio_id = ?", (nid,))
        if not conn.execute("DELETE FROM rv_negocios WHERE id = ?", (nid,)).rowcount:
            raise NoSePuede("Ese comercio no existe.")
        conn.commit()
    finally:
        conn.close()


def _fila(r) -> dict:
    return dict(r) if r else None


def negocio(db: str, nid: int, completo: bool = True) -> dict:
    conn = _connect(db)
    try:
        n = _fila(conn.execute("SELECT * FROM rv_negocios WHERE id = ?", (nid,)).fetchone())
        if not n:
            raise NoSePuede("Ese comercio no existe.")
        if completo:
            _completar(conn, n)
        return n
    finally:
        conn.close()


def por_slug(db: str, slug: str) -> Optional[dict]:
    if not re.fullmatch(r"[a-z0-9-]{1,60}", str(slug or "")):
        return None
    conn = _connect(db)
    try:
        n = _fila(conn.execute("SELECT * FROM rv_negocios WHERE slug = ? AND activo = 1", (slug,)).fetchone())
        if n:
            _completar(conn, n, solo_activos=True)
        return n
    finally:
        conn.close()


def _completar(conn, n: dict, solo_activos: bool = False) -> None:
    filtro = " AND activo = 1" if solo_activos else ""
    n["servicios"] = [dict(r) for r in conn.execute(
        f"SELECT * FROM rv_servicios WHERE negocio_id = ?{filtro} ORDER BY orden, id", (n["id"],))]
    n["profesionales"] = []
    for r in conn.execute(f"SELECT * FROM rv_profesionales WHERE negocio_id = ?{filtro} ORDER BY id", (n["id"],)):
        p = dict(r)
        p["servicios"] = json.loads(p["servicios"] or "[]")
        p["horarios"] = [{"id": h["id"], "dia": h["dia"], "desde": _min_a_hhmm(h["desde"]),
                          "hasta": _min_a_hhmm(h["hasta"])}
                         for h in conn.execute("SELECT * FROM rv_horarios WHERE profesional_id = ? "
                                               "ORDER BY dia, desde", (p["id"],))]
        n["profesionales"].append(p)
    n["bloqueos"] = [dict(r) for r in conn.execute(
        "SELECT * FROM rv_bloqueos WHERE negocio_id = ? AND hasta >= ? ORDER BY desde",
        (n["id"], _txt(ahora_uy())))]


def negocios(db: str) -> list:
    conn = _connect(db)
    try:
        hoy = hoy_uy().isoformat()
        filas = conn.execute("""
            SELECT n.*,
              (SELECT COUNT(*) FROM rv_turnos t WHERE t.negocio_id = n.id AND t.estado = 'confirmado'
                 AND t.inicio >= ?) AS proximos,
              (SELECT COUNT(*) FROM rv_turnos t WHERE t.negocio_id = n.id AND t.estado IN ('confirmado','asistio','falto')
                 AND substr(t.inicio, 1, 10) = ?) AS hoy
            FROM rv_negocios n ORDER BY n.activo DESC, n.nombre COLLATE NOCASE
        """, (_txt(ahora_uy()), hoy)).fetchall()
        return [dict(r) for r in filas]
    finally:
        conn.close()


def clientes_crm(db: str) -> list:
    conn = _connect(db)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, name FROM businesses WHERE crm_status IN ('acepto','cerrado','en_desarrollo','entregado') "
            "OR id IN (SELECT business_id FROM rv_negocios WHERE business_id IS NOT NULL) "
            "ORDER BY name COLLATE NOCASE LIMIT 500")]
    finally:
        conn.close()


# ── servicios, profesionales, horarios, bloqueos ─────────────────────────────

def _es_del_negocio(conn, tabla: str, fid: int, nid: int) -> None:
    if not conn.execute(f"SELECT 1 FROM {tabla} WHERE id = ? AND negocio_id = ?", (fid, nid)).fetchone():
        raise NoSePuede("No existe en este comercio.")


def guardar_servicio(db: str, nid: int, datos: dict, sid: Optional[int] = None) -> dict:
    nombre = _limpio(datos.get("nombre"), LARGO_NOMBRE)
    if not nombre:
        raise NoSePuede("Poné el nombre del servicio.")
    dur = _num(datos.get("duracion_min"), "Duración en minutos", 5, MAX_DURACION, entero=True)
    if not dur:
        raise NoSePuede("Poné cuánto dura, en minutos.")
    precio = _num(datos.get("precio"), "Precio", 0, 10 ** 7)
    desc = _limpio(datos.get("descripcion"), LARGO_TEXTO) or None
    activo = 0 if datos.get("activo") in (False, 0, "0", "false") else 1
    conn = _connect(db)
    try:
        negocio_existe(conn, nid)
        if sid:
            _es_del_negocio(conn, "rv_servicios", sid, nid)
            conn.execute("UPDATE rv_servicios SET nombre=?, descripcion=?, duracion_min=?, precio=?, activo=? "
                         "WHERE id = ?", (nombre, desc, dur, precio, activo, sid))
        else:
            orden = conn.execute("SELECT COALESCE(MAX(orden), 0) + 1 FROM rv_servicios WHERE negocio_id = ?",
                                 (nid,)).fetchone()[0]
            sid = conn.execute("INSERT INTO rv_servicios (negocio_id, nombre, descripcion, duracion_min, precio, "
                               "activo, orden) VALUES (?,?,?,?,?,?,?)",
                               (nid, nombre, desc, dur, precio, activo, orden)).lastrowid
        conn.commit()
        return dict(conn.execute("SELECT * FROM rv_servicios WHERE id = ?", (sid,)).fetchone())
    finally:
        conn.close()


def negocio_existe(conn, nid: int) -> None:
    if not conn.execute("SELECT 1 FROM rv_negocios WHERE id = ?", (nid,)).fetchone():
        raise NoSePuede("Ese comercio no existe.")


def borrar_servicio(db: str, nid: int, sid: int) -> None:
    """Con turnos se desactiva en vez de borrarse: los turnos viejos lo nombran."""
    conn = _connect(db)
    try:
        _es_del_negocio(conn, "rv_servicios", sid, nid)
        if conn.execute("SELECT 1 FROM rv_turnos WHERE servicio_id = ?", (sid,)).fetchone():
            conn.execute("UPDATE rv_servicios SET activo = 0 WHERE id = ?", (sid,))
        else:
            conn.execute("DELETE FROM rv_servicios WHERE id = ?", (sid,))
        conn.commit()
    finally:
        conn.close()


def _horarios(texto) -> list:
    """[{dia, desde, hasta}] validados. Acepta la lista o el texto
    'lun-vie 09:00-13:00 15:00-19:00; sab 09:00-13:00'."""
    if isinstance(texto, list):
        salida = []
        for h in texto:
            dia = _num(h.get("dia"), "Día", 0, 6, entero=True)
            if dia is None:
                raise NoSePuede("Falta el día del horario.")
            salida.append((dia, _hhmm(h.get("desde")), _hhmm(h.get("hasta"))))
    else:
        salida = []
        nombres = {_slug(d)[:3]: i for i, d in enumerate(DIAS)}
        for parte in re.split(r"[;\n]+", str(texto or "")):
            parte = parte.strip()
            if not parte:
                continue
            m = re.match(r"([a-záéíóú]+)(?:\s*-\s*([a-záéíóú]+))?\s+(.+)$", parte, re.I)
            if not m:
                raise NoSePuede(f"No entiendo {parte!r}: escribilo como 'lun-vie 09:00-18:00'.")
            d1 = nombres.get(_slug(m.group(1))[:3])
            d2 = nombres.get(_slug(m.group(2) or m.group(1))[:3])
            if d1 is None or d2 is None or d2 < d1:
                raise NoSePuede(f"No entiendo los días de {parte!r}.")
            rangos = re.findall(r"(\d{1,2}:\d\d)\s*-\s*(\d{1,2}:\d\d)", m.group(3))
            if not rangos:
                raise NoSePuede(f"Faltan las horas en {parte!r}.")
            for dia in range(d1, d2 + 1):
                for a, b in rangos:
                    salida.append((dia, _hhmm(a), _hhmm(b)))
    for dia, a, b in salida:
        if b <= a:
            raise NoSePuede(f"{DIAS[dia]}: la hora de cierre tiene que ser después de la de apertura.")
    por_dia = {}
    for dia, a, b in sorted(salida):
        if por_dia.get(dia) and a < por_dia[dia][-1][1]:
            raise NoSePuede(f"{DIAS[dia]}: hay horarios que se pisan.")
        por_dia.setdefault(dia, []).append((a, b))
    return sorted(salida)


def horarios_texto(horarios: list) -> str:
    """El inverso de _horarios para mostrar: 'lun-vie 09:00-13:00 15:00-19:00; sáb 09:00-13:00'."""
    por_dia = {}
    for h in horarios:
        por_dia.setdefault(h["dia"], []).append(f"{h['desde']}-{h['hasta']}")
    partes, dia = [], 0
    while dia < 7:
        if dia not in por_dia:
            dia += 1
            continue
        fin = dia
        while fin + 1 in por_dia and por_dia[fin + 1] == por_dia[dia]:
            fin += 1
        dias = DIAS_CORTO[dia] if fin == dia else f"{DIAS_CORTO[dia]}-{DIAS_CORTO[fin]}"
        partes.append(f"{dias} {' '.join(por_dia[dia])}")
        dia = fin + 1
    return "; ".join(partes)


def guardar_profesional(db: str, nid: int, datos: dict, pid: Optional[int] = None) -> dict:
    nombre = _limpio(datos.get("nombre"), LARGO_NOMBRE)
    if not nombre:
        raise NoSePuede("Poné el nombre.")
    horarios = _horarios(datos.get("horarios"))
    activo = 0 if datos.get("activo") in (False, 0, "0", "false") else 1
    conn = _connect(db)
    try:
        negocio_existe(conn, nid)
        servicios = []
        for s in datos.get("servicios") or []:
            sid = _num(s, "Servicio", 1, 10 ** 12, entero=True)
            _es_del_negocio(conn, "rv_servicios", sid, nid)
            servicios.append(sid)
        if pid:
            _es_del_negocio(conn, "rv_profesionales", pid, nid)
            conn.execute("UPDATE rv_profesionales SET nombre=?, servicios=?, activo=? WHERE id = ?",
                         (nombre, json.dumps(servicios), activo, pid))
            conn.execute("DELETE FROM rv_horarios WHERE profesional_id = ?", (pid,))
        else:
            pid = conn.execute("INSERT INTO rv_profesionales (negocio_id, nombre, servicios, activo) VALUES (?,?,?,?)",
                               (nid, nombre, json.dumps(servicios), activo)).lastrowid
        conn.executemany("INSERT INTO rv_horarios (profesional_id, dia, desde, hasta) VALUES (?,?,?,?)",
                         [(pid, d, a, b) for d, a, b in horarios])
        conn.commit()
    finally:
        conn.close()
    return next(p for p in negocio(db, nid)["profesionales"] if p["id"] == pid)


def borrar_profesional(db: str, nid: int, pid: int) -> None:
    conn = _connect(db)
    try:
        _es_del_negocio(conn, "rv_profesionales", pid, nid)
        if conn.execute("SELECT 1 FROM rv_turnos WHERE profesional_id = ? AND estado = 'confirmado' AND inicio >= ?",
                        (pid, _txt(ahora_uy()))).fetchone():
            raise NoSePuede("Tiene turnos por delante: cancelalos o pasalos a otra persona antes.")
        if conn.execute("SELECT 1 FROM rv_turnos WHERE profesional_id = ?", (pid,)).fetchone():
            conn.execute("UPDATE rv_profesionales SET activo = 0 WHERE id = ?", (pid,))
        else:
            conn.execute("DELETE FROM rv_horarios WHERE profesional_id = ?", (pid,))
            conn.execute("DELETE FROM rv_profesionales WHERE id = ?", (pid,))
        conn.commit()
    finally:
        conn.close()


def crear_bloqueo(db: str, nid: int, datos: dict) -> dict:
    """Un día o un rato sin turnos: feriado, vacaciones, un trámite."""
    desde_d = _fecha(datos.get("desde"))
    hasta_d = _fecha(datos.get("hasta") or datos.get("desde"))
    h1 = _hhmm(datos["hora_desde"]) if datos.get("hora_desde") else 0
    h2 = _hhmm(datos["hora_hasta"]) if datos.get("hora_hasta") else 24 * 60
    desde = datetime.combine(desde_d, datetime.min.time()) + timedelta(minutes=h1)
    hasta = datetime.combine(hasta_d, datetime.min.time()) + timedelta(minutes=h2)
    if hasta <= desde:
        raise NoSePuede("El bloqueo termina antes de empezar.")
    if hasta - desde > timedelta(days=120):
        raise NoSePuede("Un bloqueo puede durar hasta 120 días.")
    pid = _num(datos.get("profesional_id"), "Profesional", 1, 10 ** 12, entero=True)
    motivo = _limpio(datos.get("motivo"), LARGO_NOMBRE) or None
    conn = _connect(db)
    try:
        negocio_existe(conn, nid)
        if pid:
            _es_del_negocio(conn, "rv_profesionales", pid, nid)
        bid = conn.execute("INSERT INTO rv_bloqueos (negocio_id, profesional_id, desde, hasta, motivo) "
                           "VALUES (?,?,?,?,?)", (nid, pid, _txt(desde), _txt(hasta), motivo)).lastrowid
        choques = conn.execute(
            "SELECT COUNT(*) FROM rv_turnos WHERE negocio_id = ? AND estado = 'confirmado' AND inicio < ? AND fin > ?"
            + (" AND profesional_id = ?" if pid else ""),
            (nid, _txt(hasta), _txt(desde), *([pid] if pid else []))).fetchone()[0]
        conn.commit()
        b = dict(conn.execute("SELECT * FROM rv_bloqueos WHERE id = ?", (bid,)).fetchone())
        b["turnos_que_chocan"] = choques
        return b
    finally:
        conn.close()


def borrar_bloqueo(db: str, nid: int, bid: int) -> None:
    conn = _connect(db)
    try:
        if not conn.execute("DELETE FROM rv_bloqueos WHERE id = ? AND negocio_id = ?", (bid, nid)).rowcount:
            raise NoSePuede("Ese bloqueo no existe.")
        conn.commit()
    finally:
        conn.close()


# ── disponibilidad ───────────────────────────────────────────────────────────

def _hace(p: dict, sid: int) -> bool:
    return not p["servicios"] or sid in p["servicios"]


def _ocupado(conn, nid: int, dia: date) -> tuple:
    """Turnos por profesional y bloqueos (por profesional o de todos) que tocan ese día."""
    a, b = f"{dia.isoformat()} 00:00", f"{(dia + timedelta(days=1)).isoformat()} 00:00"
    turnos = {}
    for r in conn.execute("SELECT id, profesional_id, inicio, fin FROM rv_turnos WHERE negocio_id = ? "
                          "AND estado IN ('confirmado','asistio','falto') AND inicio < ? AND fin > ?", (nid, b, a)):
        turnos.setdefault(r["profesional_id"], []).append((_dt(r["inicio"]), _dt(r["fin"]), r["id"]))
    bloqueos = [(r["profesional_id"], _dt(r["desde"]), _dt(r["hasta"])) for r in conn.execute(
        "SELECT profesional_id, desde, hasta FROM rv_bloqueos WHERE negocio_id = ? AND desde < ? AND hasta > ?",
        (nid, b, a))]
    return turnos, bloqueos


def _libre(pid: int, ini: datetime, fin: datetime, turnos: dict, bloqueos: list, ignorar: Optional[int]) -> bool:
    for a, b, tid in turnos.get(pid, []):
        if tid != ignorar and a < fin and b > ini:
            return False
    for bp, a, b in bloqueos:
        if (bp is None or bp == pid) and a < fin and b > ini:
            return False
    return True


def horarios_libres(db: str, n: dict, sid: int, dia: date, pid: Optional[int] = None,
                    ignorar: Optional[int] = None, para_equipo: bool = False) -> list:
    """Los arranques libres de ese día para ese servicio: [{hora, profesionales: [id...]}].

    `pid` None es "el primero libre". `ignorar` es el turno que se está
    cambiando (su lugar cuenta como libre). El equipo puede cargar turnos sin
    respetar la anticipación ni el máximo de días; el link público no.
    """
    serv = next((s for s in n["servicios"] if s["id"] == sid and s["activo"]), None)
    if not serv:
        raise NoSePuede("Ese servicio no existe.")
    ahora = ahora_uy()
    if not para_equipo:
        if dia < ahora.date() or dia > ahora.date() + timedelta(days=n["dias_max"]):
            return []
    minimo = ahora + timedelta(minutes=0 if para_equipo else n["anticipacion_min"])
    dur = timedelta(minutes=serv["duracion_min"])
    paso = n["intervalo_min"]
    profs = [p for p in n["profesionales"] if p["activo"] and _hace(p, sid) and (pid is None or p["id"] == pid)]
    conn = _connect(db)
    try:
        turnos, bloqueos = _ocupado(conn, n["id"], dia)
    finally:
        conn.close()
    base = datetime.combine(dia, datetime.min.time())
    libres = {}
    for p in profs:
        for h in p["horarios"]:
            if h["dia"] != dia.weekday():
                continue
            a, b = _hhmm(h["desde"]), _hhmm(h["hasta"])
            m = a
            while m + serv["duracion_min"] <= b:
                ini = base + timedelta(minutes=m)
                if ini >= minimo and _libre(p["id"], ini, ini + dur, turnos, bloqueos, ignorar):
                    libres.setdefault(_min_a_hhmm(m), []).append(p["id"])
                m += paso
    return [{"hora": h, "profesionales": libres[h]} for h in sorted(libres)]


def dias_con_lugar(db: str, n: dict, sid: int, pid: Optional[int] = None, dias: int = 14,
                   ignorar: Optional[int] = None) -> list:
    """Los próximos días que el link puede ofrecer, con cuántos horarios libres tiene cada uno."""
    hoy = hoy_uy()
    tope = min(dias, n["dias_max"] + 1)
    return [{"fecha": d.isoformat(), "libres": len(horarios_libres(db, n, sid, d, pid, ignorar))}
            for d in (hoy + timedelta(days=i) for i in range(tope))]


# ── turnos ───────────────────────────────────────────────────────────────────

def _codigo_turno() -> str:
    return secrets.token_urlsafe(16)


def _elegir(conn, candidatos: list, dia: date) -> int:
    """'El primero libre': el que menos turnos tiene ese día, para repartir."""
    def carga(pid):
        return conn.execute("SELECT COUNT(*) FROM rv_turnos WHERE profesional_id = ? AND substr(inicio,1,10) = ? "
                            "AND estado IN ('confirmado','asistio','falto')", (pid, dia.isoformat())).fetchone()[0]
    return min(candidatos, key=lambda p: (carga(p), p))


def reservar(db: str, nid: int, datos: dict, origen: str = "link", quien: str = "cliente",
             cambiar: Optional[str] = None) -> dict:
    """Crea el turno (o mueve el de `cambiar`, el código del cliente) si el lugar sigue libre.

    La revisión y el alta van en la misma transacción con BEGIN IMMEDIATE:
    dos personas que eligen la misma hora a la vez no pueden quedar las dos.
    """
    para_equipo = origen == "equipo"
    n = negocio(db, nid)
    if not n["activo"] and not para_equipo:
        raise NoSePuede("Este comercio no está tomando reservas online.")
    sid = _num(datos.get("servicio_id"), "Servicio", 1, 10 ** 12, entero=True)
    pid = _num(datos.get("profesional_id"), "Profesional", 0, 10 ** 12, entero=True) or None
    dia = _fecha(datos.get("fecha"))
    hora = _min_a_hhmm(_hhmm(datos.get("hora")))
    viejo = None
    if cambiar:
        viejo = turno_por_codigo(db, cambiar)
        if not viejo or viejo["negocio_id"] != nid:
            raise NoSePuede("Ese turno no existe.")
        if not puede_cambiar(n, viejo) and not para_equipo:
            raise NoSePuede("Ya no se puede cambiar este turno por acá: escribile al comercio.")
        cliente, tel, nota = viejo["cliente"], viejo["telefono"], viejo["nota"]
    else:
        cliente = _limpio(datos.get("cliente"), LARGO_NOMBRE)
        if not cliente:
            raise NoSePuede("Poné tu nombre.")
        tel = tel_normal(datos.get("telefono"))
        nota = _limpio(datos.get("nota"), LARGO_TEXTO) or None
    serv = next((s for s in n["servicios"] if s["id"] == sid and s["activo"]), None)
    if not serv:
        raise NoSePuede("Ese servicio no existe.")
    conn = _connect(db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not para_equipo and not viejo:
            hoy = hoy_uy().isoformat()
            if conn.execute("SELECT COUNT(*) FROM rv_turnos WHERE negocio_id = ? AND origen = 'link' "
                            "AND substr(creado_en,1,10) = ?", (nid, hoy)).fetchone()[0] >= MAX_RESERVAS_DIA:
                raise NoSePuede("Hoy no se pueden tomar más reservas online. Escribile al comercio.")
            if conn.execute("SELECT COUNT(*) FROM rv_turnos WHERE negocio_id = ? AND telefono = ? "
                            "AND estado = 'confirmado' AND inicio >= ?",
                            (nid, tel, _txt(ahora_uy()))).fetchone()[0] >= MAX_FUTUROS_POR_TEL:
                raise NoSePuede(f"Ya tenés {MAX_FUTUROS_POR_TEL} turnos por delante con este número.")
        libres = horarios_libres(db, n, sid, dia, pid, ignorar=viejo["id"] if viejo else None,
                                 para_equipo=para_equipo)
        lugar = next((x for x in libres if x["hora"] == hora), None)
        if not lugar:
            raise NoSePuede("Ese horario se acaba de ocupar. Elegí otro.")
        elegido = pid if pid in lugar["profesionales"] else _elegir(conn, lugar["profesionales"], dia)
        ini = datetime.combine(dia, datetime.min.time()) + timedelta(minutes=_hhmm(hora))
        fin = ini + timedelta(minutes=serv["duracion_min"])
        ahora = _txt(ahora_uy())
        if viejo:
            conn.execute("UPDATE rv_turnos SET profesional_id=?, servicio_id=?, inicio=?, fin=?, precio=?, "
                         "cambiado_en=?, recordado_en=NULL WHERE id = ?",
                         (elegido, sid, _txt(ini), _txt(fin), serv["precio"], ahora, viejo["id"]))
            tid = viejo["id"]
        else:
            tid = conn.execute(
                "INSERT INTO rv_turnos (negocio_id, profesional_id, servicio_id, inicio, fin, cliente, telefono, "
                "nota, precio, codigo, origen, creado_por, creado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (nid, elegido, sid, _txt(ini), _txt(fin), cliente, tel, nota, serv["precio"], _codigo_turno(),
                 origen, quien, ahora)).lastrowid
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return turno(db, tid)


_SELECT_TURNO = """
    SELECT t.*, s.nombre AS servicio, s.duracion_min, p.nombre AS profesional, n.nombre AS negocio, n.slug
    FROM rv_turnos t JOIN rv_servicios s ON s.id = t.servicio_id
    JOIN rv_profesionales p ON p.id = t.profesional_id JOIN rv_negocios n ON n.id = t.negocio_id
"""


def turno(db: str, tid: int) -> dict:
    conn = _connect(db)
    try:
        t = _fila(conn.execute(_SELECT_TURNO + " WHERE t.id = ?", (tid,)).fetchone())
    finally:
        conn.close()
    if not t:
        raise NoSePuede("Ese turno no existe.")
    return t


def turno_por_codigo(db: str, codigo: str) -> Optional[dict]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,40}", str(codigo or "")):
        return None
    conn = _connect(db)
    try:
        return _fila(conn.execute(_SELECT_TURNO + " WHERE t.codigo = ?", (codigo,)).fetchone())
    finally:
        conn.close()


def puede_cambiar(n: dict, t: dict) -> bool:
    if t["estado"] != "confirmado":
        return False
    return _dt(t["inicio"]) - ahora_uy() >= timedelta(hours=n["cancelar_hasta_h"])


def cancelar(db: str, codigo: str, por_cliente: bool = True) -> dict:
    t = turno_por_codigo(db, codigo)
    if not t:
        raise NoSePuede("Ese turno no existe.")
    n = negocio(db, t["negocio_id"], completo=False)
    if por_cliente and not puede_cambiar(n, t):
        raise NoSePuede("Ya no se puede cancelar por acá: escribile al comercio.")
    return cambiar_estado(db, t["id"], "cancelado")


def cambiar_estado(db: str, tid: int, estado: str) -> dict:
    if estado not in ESTADOS:
        raise NoSePuede("Estado no válido.")
    conn = _connect(db)
    try:
        if not conn.execute("UPDATE rv_turnos SET estado = ?, cambiado_en = ? WHERE id = ?",
                            (estado, _txt(ahora_uy()), tid)).rowcount:
            raise NoSePuede("Ese turno no existe.")
        conn.commit()
    finally:
        conn.close()
    return turno(db, tid)


def marcar_recordado(db: str, tid: int) -> dict:
    conn = _connect(db)
    try:
        conn.execute("UPDATE rv_turnos SET recordado_en = ? WHERE id = ?", (_txt(ahora_uy()), tid))
        conn.commit()
    finally:
        conn.close()
    return turno(db, tid)


def agenda(db: str, nid: int, desde: date, dias: int = 1) -> list:
    conn = _connect(db)
    try:
        a = f"{desde.isoformat()} 00:00"
        b = f"{(desde + timedelta(days=dias)).isoformat()} 00:00"
        filas = conn.execute(_SELECT_TURNO + " WHERE t.negocio_id = ? AND t.inicio >= ? AND t.inicio < ? "
                             "ORDER BY t.inicio, p.id", (nid, a, b)).fetchall()
        return [dict(r) for r in filas]
    finally:
        conn.close()


def link_turno(base: str, t: dict) -> str:
    return f"{base}/reservar/{t['slug']}/t/{t['codigo']}"


def whatsapp_recordatorio(base: str, t: dict) -> str:
    """Link wa.me con el recordatorio armado. Lo abre una persona: no se manda solo."""
    d = _dt(t["inicio"])
    texto = (f"¡Hola {t['cliente'].split(' ')[0]}! Te recordamos tu turno en {t['negocio']}: "
             f"{t['servicio']} el {fecha_larga(d.date())} a las {d:%H:%M} con {t['profesional']}. "
             f"Si no podés venir, lo cambiás o cancelás acá: {link_turno(base, t)}")
    return f"https://wa.me/{t['telefono']}?text={quote(texto)}"


def ics(t: dict, base: str) -> str:
    """El turno como evento de calendario (.ics), para que el cliente lo agende."""
    ini, fin = _dt(t["inicio"]) - UY, _dt(t["fin"]) - UY

    def esc(s):
        return str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Scalerics//Agenda online//ES", "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT", f"UID:{t['codigo']}@scalerics", f"DTSTAMP:{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
        f"DTSTART:{ini:%Y%m%dT%H%M%SZ}", f"DTEND:{fin:%Y%m%dT%H%M%SZ}",
        f"SUMMARY:{esc(t['servicio'] + ' · ' + t['negocio'])}",
        f"DESCRIPTION:{esc('Con ' + t['profesional'] + '. Cambiar o cancelar: ' + link_turno(base, t))}",
        "BEGIN:VALARM", "TRIGGER:-PT2H", "ACTION:DISPLAY", f"DESCRIPTION:{esc(t['servicio'])}", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR", ""])


# ── métricas ─────────────────────────────────────────────────────────────────

def _minutos_abiertos(db: str, n: dict, desde: date, dias: int) -> int:
    """Cuántos minutos de agenda hay en esos días, descontando bloqueos."""
    conn = _connect(db)
    try:
        total = 0
        for i in range(dias):
            d = desde + timedelta(days=i)
            _, bloqueos = _ocupado(conn, n["id"], d)
            base = datetime.combine(d, datetime.min.time())
            for p in n["profesionales"]:
                if not p["activo"]:
                    continue
                for h in p["horarios"]:
                    if h["dia"] != d.weekday():
                        continue
                    a, b = base + timedelta(minutes=_hhmm(h["desde"])), base + timedelta(minutes=_hhmm(h["hasta"]))
                    mins = (b - a).seconds // 60
                    for bp, x, y in bloqueos:
                        if bp is None or bp == p["id"]:
                            cruce = (min(b, y) - max(a, x)).total_seconds() // 60
                            mins -= max(0, int(cruce))
                    total += max(0, mins)
        return total
    finally:
        conn.close()


def metricas(db: str, nid: int) -> dict:
    n = negocio(db, nid)
    hoy = hoy_uy()
    prox = agenda(db, nid, hoy, 7)
    ocupan = [t for t in prox if t["estado"] in OCUPAN]
    abiertos = _minutos_abiertos(db, n, hoy, 7)
    ocupados = sum(t["duracion_min"] for t in ocupan)
    pasados = agenda(db, nid, hoy - timedelta(days=30), 30)
    cerrados = [t for t in pasados if t["estado"] in ("asistio", "falto")]
    faltas = sum(1 for t in cerrados if t["estado"] == "falto")
    cancelados = sum(1 for t in pasados if t["estado"] == "cancelado")
    online = [t for t in pasados + prox if t["estado"] != "cancelado"]
    por_link = sum(1 for t in online if t["origen"] == "link")
    sin_marcar = sum(1 for t in pasados if t["estado"] == "confirmado")
    por_servicio = {}
    for t in pasados + prox:
        if t["estado"] != "cancelado":
            por_servicio[t["servicio"]] = por_servicio.get(t["servicio"], 0) + 1
    fuera_de_hora = 0
    for t in online:
        c = _dt(t["creado_en"])
        if t["origen"] == "link" and c and (c.hour < 9 or c.hour >= 19 or c.weekday() == 6):
            fuera_de_hora += 1
    return {
        "proximos_7": len(ocupan),
        "hoy": sum(1 for t in ocupan if t["inicio"][:10] == hoy.isoformat()),
        "ocupacion_7": round(100 * ocupados / abiertos) if abiertos else None,
        "agendado_7": round(sum(t["precio"] or 0 for t in ocupan)),
        "ausencias_30": round(100 * faltas / len(cerrados)) if cerrados else None,
        "faltas_30": faltas,
        "cancelados_30": cancelados,
        "por_link_pct": round(100 * por_link / len(online)) if online else None,
        "por_link": por_link,
        "fuera_de_hora": fuera_de_hora,
        "sin_marcar": sin_marcar,
        "sin_recordar": sum(1 for t in ocupan if t["estado"] == "confirmado" and not t["recordado_en"]
                            and t["inicio"][:10] <= (hoy + timedelta(days=1)).isoformat()),
        "top_servicios": sorted(por_servicio.items(), key=lambda x: -x[1])[:5],
    }
