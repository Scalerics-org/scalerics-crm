"""Mesa de pedidos de los clientes de mantenimiento (24/10).

Hoy un cliente con plan de mantenimiento pide un cambio por WhatsApp, al
celular de quien le atendió la última vez. No queda registro de qué pidió,
cuándo, cuánto llevó ni si ya se pasó de las horas que paga por mes, y la
discusión de fin de mes ("¿y esto entraba en el plan?") se tiene de memoria.

Esto lo ordena como una mesa de ayuda (Zendesk, Help Scout, Linear Asks) con
la bolsa de horas de un retainer (Productive, Teamwork):

- cada cliente con plan tiene un link `/pedidos/<código>`, sin cuenta, donde
  pide cambios con prioridad y adjuntos, sigue cada pedido y ve cuántas horas
  del mes le quedan;
- el equipo los ve en una bandeja con SLA de primera respuesta y de
  resolución por prioridad, contesta (o deja una nota interna), cambia el
  estado y carga las horas que llevó cada pedido;
- la bolsa del mes dice qué clientes se pasan de su plan y cuánto habría que
  facturar aparte.

El SLA corre en horas corridas, no hábiles, y se pausa mientras el pedido está
esperando al cliente. Los adjuntos van a disco en `SOPORTE_DIR` (por defecto
`soporte/` al lado de la base, que en Fly es el volumen /data) con un nombre
al azar; solo los baja el equipo, siempre como adjunto.

Nada de esto le manda nada a nadie. Las rutas están en routes/soporte.py.
"""

import os
import re
import secrets
import sqlite3
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from database import _connect

FORMATO = "%Y-%m-%d %H:%M:%S"
# Uruguay no tiene horario de verano desde 2015: la fecha local es UTC-3.
UY = timedelta(hours=-3)

MAX_ARCHIVO = int(os.environ.get("SOPORTE_MAX_MB", "10")) * 1024 * 1024
MAX_ARCHIVOS_ENVIO = 5
MAX_POR_PLAN = 300 * 1024 * 1024
# Topes del link público: alcanzan de sobra para un comercio y frenan a
# quien encuentre el link y lo quiera llenar de basura.
MAX_PEDIDOS_DIA = 15
MAX_MENSAJES = 150
LARGO_TITULO = 140
LARGO_TEXTO = 6000
LARGO_NOMBRE = 120
LARGO_MENSAJE_PLAN = 1500
LARGO_NOTA_HORAS = 300
MAX_HORAS_CARGA = 40

PERMITIDAS = {
    "png", "jpg", "jpeg", "webp", "gif", "heic", "pdf", "txt", "csv",
    "doc", "docx", "xls", "xlsx", "zip", "mp4", "mov",
}

# Prioridad: (cómo la ve el cliente, horas para la primera respuesta, horas para resolverlo).
PRIORIDADES = {
    "urgente": ("Urgente: algo dejó de andar", 4, 24),
    "normal": ("Normal", 24, 5 * 24),
    "baja": ("Sin apuro", 72, 15 * 24),
}
TIPOS = {
    "error": "Algo no anda",
    "cambio": "Cambio en el sitio",
    "contenido": "Actualizar textos, fotos o precios",
    "nuevo": "Algo nuevo",
}
ESTADOS = ("nuevo", "en_curso", "esperando", "hecho", "descartado")
ABIERTOS = ("nuevo", "en_curso", "esperando")
# Con menos de esta fracción del plazo por delante, el pedido está en riesgo.
RIESGO = 0.25


class NoSePuede(ValueError):
    """Lo que se pidió no se puede hacer; el mensaje se muestra tal cual."""


def _ahora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _txt(d: datetime) -> str:
    return d.strftime(FORMATO)


def _dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.strptime(s, FORMATO)
    except ValueError:
        return None


def hoy_uy() -> date:
    return (_ahora() + UY).date()


def mes_actual() -> str:
    return hoy_uy().strftime("%Y-%m")


def _mes_valido(mes: Optional[str]) -> str:
    if mes and re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", mes):
        return mes
    return mes_actual()


def _limpio(v, largo: int) -> str:
    return str(v or "").replace("\r\n", "\n").strip()[:largo]


def _dir(db: str) -> str:
    base = os.environ.get("SOPORTE_DIR") or os.path.join(os.path.dirname(os.path.abspath(db)), "soporte")
    os.makedirs(base, exist_ok=True)
    return base


def init_soporte(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sp_planes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo        TEXT NOT NULL UNIQUE,
            nombre        TEXT NOT NULL,
            business_id   INTEGER,
            horas_mes     REAL NOT NULL DEFAULT 0 CHECK (horas_mes >= 0),
            precio_hora   REAL CHECK (precio_hora IS NULL OR precio_hora >= 0),
            mensaje       TEXT,
            activo        INTEGER NOT NULL DEFAULT 1,
            creado_por    TEXT,
            creado_en     TEXT NOT NULL,
            ultima_visita TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sp_pedidos (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id              INTEGER NOT NULL REFERENCES sp_planes(id) ON DELETE CASCADE,
            numero               INTEGER NOT NULL,
            titulo               TEXT NOT NULL,
            descripcion          TEXT NOT NULL DEFAULT '',
            tipo                 TEXT NOT NULL CHECK (tipo IN ('error', 'cambio', 'contenido', 'nuevo')),
            prioridad            TEXT NOT NULL CHECK (prioridad IN ('urgente', 'normal', 'baja')),
            estado               TEXT NOT NULL DEFAULT 'nuevo'
                                 CHECK (estado IN ('nuevo', 'en_curso', 'esperando', 'hecho', 'descartado')),
            asignado             TEXT,
            creado_por           TEXT NOT NULL,
            de_cliente           INTEGER NOT NULL DEFAULT 1,
            creado_en            TEXT NOT NULL,
            actualizado_en       TEXT NOT NULL,
            primera_respuesta_en TEXT,
            resuelto_en          TEXT,
            esperando_desde      TEXT,
            pausado_seg          INTEGER NOT NULL DEFAULT 0,
            UNIQUE (plan_id, numero)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sp_mensajes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id  INTEGER NOT NULL REFERENCES sp_pedidos(id) ON DELETE CASCADE,
            quien      TEXT NOT NULL,
            de_cliente INTEGER NOT NULL DEFAULT 0,
            interno    INTEGER NOT NULL DEFAULT 0,
            texto      TEXT NOT NULL,
            creado_en  TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sp_archivos (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id  INTEGER NOT NULL REFERENCES sp_pedidos(id) ON DELETE CASCADE,
            mensaje_id INTEGER,
            nombre     TEXT NOT NULL,
            ruta       TEXT NOT NULL,
            bytes      INTEGER NOT NULL,
            subido_por TEXT NOT NULL,
            creado_en  TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sp_horas (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id  INTEGER NOT NULL REFERENCES sp_pedidos(id) ON DELETE CASCADE,
            plan_id    INTEGER NOT NULL,
            fecha      TEXT NOT NULL,
            horas      REAL NOT NULL CHECK (horas > 0),
            nota       TEXT,
            quien      TEXT NOT NULL,
            creado_en  TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sp_pedidos_plan ON sp_pedidos(plan_id, estado)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sp_mensajes_pedido ON sp_mensajes(pedido_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sp_horas_plan ON sp_horas(plan_id, fecha)")


# ── planes ───────────────────────────────────────────────────────────────────

def _numero(v, campo: str, minimo: float = 0, maximo: float = 10000, nulo: bool = False) -> Optional[float]:
    if v in (None, ""):
        if nulo:
            return None
        v = 0
    try:
        n = float(str(v).replace(",", "."))
    except ValueError:
        raise NoSePuede(f"{campo}: tiene que ser un número.")
    if n != n or n < minimo or n > maximo:
        raise NoSePuede(f"{campo}: tiene que estar entre {minimo:g} y {maximo:g}.")
    return round(n, 2)


def _business(conn, v) -> Optional[int]:
    if v in (None, "", 0, "0"):
        return None
    try:
        bid = int(v)
    except (TypeError, ValueError):
        raise NoSePuede("Ese cliente no existe.")
    if not conn.execute("SELECT 1 FROM businesses WHERE id = ?", (bid,)).fetchone():
        raise NoSePuede("Ese cliente no existe.")
    return bid


def crear_plan(db: str, datos: dict, quien: str) -> dict:
    nombre = _limpio(datos.get("nombre"), LARGO_NOMBRE)
    if not nombre:
        raise NoSePuede("Poné el nombre del cliente o del sitio.")
    horas = _numero(datos.get("horas_mes"), "Horas por mes", 0, 400)
    precio = _numero(datos.get("precio_hora"), "Precio de la hora extra", 0, 10000, nulo=True)
    conn = _connect(db)
    try:
        bid = _business(conn, datos.get("business_id"))
        cur = conn.execute(
            "INSERT INTO sp_planes (codigo, nombre, business_id, horas_mes, precio_hora, mensaje, creado_por, creado_en) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (secrets.token_urlsafe(18), nombre, bid, horas, precio,
             _limpio(datos.get("mensaje"), LARGO_MENSAJE_PLAN) or None, quien, _txt(_ahora())))
        conn.commit()
        pid = cur.lastrowid
    finally:
        conn.close()
    return plan(db, pid)


def plan(db: str, pid: int, mes: Optional[str] = None) -> dict:
    conn = _connect(db)
    try:
        f = conn.execute("""SELECT p.*, b.name AS cliente_nombre FROM sp_planes p
                            LEFT JOIN businesses b ON b.id = p.business_id WHERE p.id = ?""", (pid,)).fetchone()
        if not f:
            raise NoSePuede("Ese plan no existe.")
        return _con_consumo(conn, dict(f), _mes_valido(mes))
    finally:
        conn.close()


def por_codigo(db: str, codigo: str) -> Optional[dict]:
    if not codigo or len(codigo) > 64:
        return None
    conn = _connect(db)
    try:
        f = conn.execute("SELECT id FROM sp_planes WHERE codigo = ? AND activo = 1", (codigo,)).fetchone()
    finally:
        conn.close()
    return plan(db, f["id"]) if f else None


def _con_consumo(conn, p: dict, mes: str) -> dict:
    usadas = conn.execute("SELECT COALESCE(SUM(horas), 0) FROM sp_horas WHERE plan_id = ? AND substr(fecha, 1, 7) = ?",
                          (p["id"], mes)).fetchone()[0]
    usadas = round(float(usadas), 2)
    bolsa = float(p["horas_mes"] or 0)
    extra = round(max(0.0, usadas - bolsa), 2)
    abiertos = conn.execute(f"SELECT COUNT(*) FROM sp_pedidos WHERE plan_id = ? AND estado IN "
                            f"({','.join('?' * len(ABIERTOS))})", (p["id"], *ABIERTOS)).fetchone()[0]
    p.update({
        "mes": mes,
        "usadas": usadas,
        "restantes": round(max(0.0, bolsa - usadas), 2),
        "extra": extra,
        "monto_extra": round(extra * p["precio_hora"], 2) if p.get("precio_hora") is not None else None,
        "pct": min(100, round(100 * usadas / bolsa)) if bolsa else (100 if usadas else 0),
        "pasado": bool(extra and bolsa) or (not bolsa and usadas > 0),
        "abiertos": abiertos,
    })
    return p


def planes(db: str, mes: Optional[str] = None) -> list:
    mes = _mes_valido(mes)
    conn = _connect(db)
    try:
        filas = conn.execute("""SELECT p.*, b.name AS cliente_nombre FROM sp_planes p
                                LEFT JOIN businesses b ON b.id = p.business_id
                                ORDER BY p.activo DESC, p.nombre COLLATE NOCASE""").fetchall()
        return [_con_consumo(conn, dict(f), mes) for f in filas]
    finally:
        conn.close()


def editar_plan(db: str, pid: int, datos: dict) -> dict:
    plan(db, pid)
    cambios = {}
    if "nombre" in datos:
        cambios["nombre"] = _limpio(datos["nombre"], LARGO_NOMBRE)
        if not cambios["nombre"]:
            raise NoSePuede("El nombre no puede quedar vacío.")
    if "horas_mes" in datos:
        cambios["horas_mes"] = _numero(datos["horas_mes"], "Horas por mes", 0, 400)
    if "precio_hora" in datos:
        cambios["precio_hora"] = _numero(datos["precio_hora"], "Precio de la hora extra", 0, 10000, nulo=True)
    if "mensaje" in datos:
        cambios["mensaje"] = _limpio(datos["mensaje"], LARGO_MENSAJE_PLAN) or None
    if "activo" in datos:
        cambios["activo"] = 1 if datos["activo"] else 0
    conn = _connect(db)
    try:
        if "business_id" in datos:
            cambios["business_id"] = _business(conn, datos["business_id"])
        if datos.get("nuevo_link"):
            cambios["codigo"] = secrets.token_urlsafe(18)
        if cambios:
            sets = ", ".join(f"{k} = ?" for k in cambios)
            conn.execute(f"UPDATE sp_planes SET {sets} WHERE id = ?", (*cambios.values(), pid))
            conn.commit()
    finally:
        conn.close()
    return plan(db, pid)


def borrar_plan(db: str, pid: int) -> None:
    plan(db, pid)
    conn = _connect(db)
    try:
        rutas = [f["ruta"] for f in conn.execute(
            "SELECT a.ruta FROM sp_archivos a JOIN sp_pedidos p ON p.id = a.pedido_id WHERE p.plan_id = ?", (pid,))]
        ids = [f[0] for f in conn.execute("SELECT id FROM sp_pedidos WHERE plan_id = ?", (pid,))]
        for tabla in ("sp_archivos", "sp_mensajes", "sp_horas"):
            conn.executemany(f"DELETE FROM {tabla} WHERE pedido_id = ?", [(i,) for i in ids])
        conn.execute("DELETE FROM sp_pedidos WHERE plan_id = ?", (pid,))
        conn.execute("DELETE FROM sp_planes WHERE id = ?", (pid,))
        conn.commit()
    finally:
        conn.close()
    for r in rutas:
        _borrar_disco(db, r)


def visita(db: str, pid: int) -> None:
    conn = _connect(db)
    try:
        conn.execute("UPDATE sp_planes SET ultima_visita = ? WHERE id = ?", (_txt(_ahora()), pid))
        conn.commit()
    finally:
        conn.close()


def clientes(db: str) -> list:
    """Para el selector del alta: los clientes del CRM, sin plan primero."""
    from database import ETAPAS_CLIENTE
    conn = _connect(db)
    try:
        marcas = ",".join("?" * len(ETAPAS_CLIENTE))
        filas = conn.execute(f"""
            SELECT b.id, b.name, EXISTS(SELECT 1 FROM sp_planes p WHERE p.business_id = b.id) AS con_plan
            FROM businesses b WHERE b.crm_status IN ({marcas})
            ORDER BY con_plan, b.name LIMIT 300""", ETAPAS_CLIENTE).fetchall()
        return [dict(f) for f in filas]
    finally:
        conn.close()


# ── pedidos ──────────────────────────────────────────────────────────────────

def _plazos(p: dict) -> tuple:
    """Cuándo vence la primera respuesta y cuándo la resolución, con la pausa sumada."""
    _, h_resp, h_res = PRIORIDADES[p["prioridad"]]
    creado = _dt(p["creado_en"])
    pausa = timedelta(seconds=int(p.get("pausado_seg") or 0))
    return creado + timedelta(hours=h_resp), creado + timedelta(hours=h_res) + pausa


def sla(p: dict, ahora: Optional[datetime] = None) -> dict:
    """El estado del SLA de un pedido, para pintarlo.

    estado: cumplido | vencido (se resolvió tarde o ya se pasó) | riesgo | ok |
    pausado (esperando al cliente) | cerrado (descartado).
    """
    ahora = ahora or _ahora()
    responder, resolver = _plazos(p)
    _, h_resp, h_res = PRIORIDADES[p["prioridad"]]
    primera = _dt(p.get("primera_respuesta_en"))
    salida = {"responder_antes": _txt(responder), "resolver_antes": _txt(resolver),
              "respuesta_tarde": bool(primera and primera > responder)}
    if p["estado"] == "descartado":
        return {**salida, "estado": "cerrado", "que": "", "vence": None}
    if p["estado"] == "hecho":
        resuelto = _dt(p.get("resuelto_en")) or ahora
        return {**salida, "estado": "vencido" if resuelto > resolver else "cumplido", "que": "resolución", "vence": None}
    if p["estado"] == "esperando":
        return {**salida, "estado": "pausado", "que": "", "vence": None}
    if not primera:
        que, vence, total = "respuesta", responder, h_resp
    else:
        que, vence, total = "resolución", resolver, h_res
    falta = (vence - ahora).total_seconds()
    if falta < 0:
        estado = "vencido"
    elif falta < RIESGO * total * 3600:
        estado = "riesgo"
    else:
        estado = "ok"
    return {**salida, "estado": estado, "que": que, "vence": _txt(vence), "falta_min": int(falta // 60)}


def _pedido_fila(conn, pid: int) -> dict:
    f = conn.execute("""SELECT d.*, p.nombre AS plan_nombre, p.codigo AS plan_codigo FROM sp_pedidos d
                        JOIN sp_planes p ON p.id = d.plan_id WHERE d.id = ?""", (pid,)).fetchone()
    if not f:
        raise NoSePuede("Ese pedido no existe.")
    return dict(f)


def _enriquecer(conn, d: dict, ahora: datetime) -> dict:
    d["sla"] = sla(d, ahora)
    d["horas"] = round(float(conn.execute("SELECT COALESCE(SUM(horas), 0) FROM sp_horas WHERE pedido_id = ?",
                                          (d["id"],)).fetchone()[0]), 2)
    ult = conn.execute("SELECT quien, de_cliente, creado_en FROM sp_mensajes WHERE pedido_id = ? AND interno = 0 "
                       "ORDER BY id DESC LIMIT 1", (d["id"],)).fetchone()
    d["ultimo"] = dict(ult) if ult else None
    # Lo último que habló fue el cliente y nadie le contestó después: le toca al equipo.
    d["toca_responder"] = d["estado"] in ABIERTOS and (
        (ult is not None and ult["de_cliente"] == 1) or (ult is None and d["de_cliente"] == 1))
    return d


def pedidos(db: str, plan_id: Optional[int] = None, abiertos: bool = False, limite: int = 300) -> list:
    conn = _connect(db)
    ahora = _ahora()
    try:
        donde, args = [], []
        if plan_id is not None:
            donde.append("d.plan_id = ?")
            args.append(plan_id)
        if abiertos:
            donde.append(f"d.estado IN ({','.join('?' * len(ABIERTOS))})")
            args.extend(ABIERTOS)
        filas = conn.execute(
            "SELECT d.*, p.nombre AS plan_nombre, p.codigo AS plan_codigo FROM sp_pedidos d "
            "JOIN sp_planes p ON p.id = d.plan_id "
            + (("WHERE " + " AND ".join(donde)) if donde else "")
            + " ORDER BY d.actualizado_en DESC, d.id DESC LIMIT ?", (*args, limite)).fetchall()
        return [_enriquecer(conn, dict(f), ahora) for f in filas]
    finally:
        conn.close()


_ORDEN_SLA = {"vencido": 0, "riesgo": 1, "ok": 2, "pausado": 3, "cumplido": 4, "cerrado": 5}
_ORDEN_PRIORIDAD = {"urgente": 0, "normal": 1, "baja": 2}


def orden_bandeja(d: dict) -> tuple:
    """Primero lo que está vencido o por vencer, después lo urgente, después lo más viejo."""
    abierto = d["estado"] in ABIERTOS
    return (0 if abierto else 1, _ORDEN_SLA.get(d["sla"]["estado"], 9), _ORDEN_PRIORIDAD[d["prioridad"]],
            d["sla"].get("vence") or "9", d["creado_en"])


def _tipo_y_prioridad(datos: dict) -> tuple:
    tipo = datos.get("tipo") or "cambio"
    prioridad = datos.get("prioridad") or "normal"
    if tipo not in TIPOS:
        raise NoSePuede("Elegí qué tipo de pedido es.")
    if prioridad not in PRIORIDADES:
        raise NoSePuede("Elegí la prioridad.")
    return tipo, prioridad


def crear_pedido(db: str, plan_id: int, datos: dict, quien: str, de_cliente: bool, archivos=()) -> dict:
    titulo = _limpio(datos.get("titulo"), LARGO_TITULO)
    descripcion = _limpio(datos.get("descripcion"), LARGO_TEXTO)
    if not titulo:
        raise NoSePuede("Contanos en una línea qué necesitás.")
    tipo, prioridad = _tipo_y_prioridad(datos)
    archivos = [a for a in archivos if a[0]]
    _validar_archivos(archivos)
    ahora = _ahora()
    conn = _connect(db)
    try:
        if not conn.execute("SELECT 1 FROM sp_planes WHERE id = ?", (plan_id,)).fetchone():
            raise NoSePuede("Ese plan no existe.")
        if de_cliente:
            hoy = conn.execute("SELECT COUNT(*) FROM sp_pedidos WHERE plan_id = ? AND de_cliente = 1 AND creado_en >= ?",
                               (plan_id, _txt(ahora - timedelta(days=1)))).fetchone()[0]
            if hoy >= MAX_PEDIDOS_DIA:
                raise NoSePuede("Llegaste al tope de pedidos por hoy. Si es urgente, escribinos por WhatsApp.")
        numero = conn.execute("SELECT COALESCE(MAX(numero), 0) + 1 FROM sp_pedidos WHERE plan_id = ?",
                              (plan_id,)).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO sp_pedidos (plan_id, numero, titulo, descripcion, tipo, prioridad, creado_por, de_cliente, "
            "creado_en, actualizado_en) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (plan_id, numero, titulo, descripcion, tipo, prioridad, quien, 1 if de_cliente else 0,
             _txt(ahora), _txt(ahora)))
        did = cur.lastrowid
        _guardar_archivos(conn, db, plan_id, did, None, archivos, quien)
        conn.commit()
    finally:
        conn.close()
    return pedido(db, did)


def pedido(db: str, did: int, publico: bool = False) -> dict:
    conn = _connect(db)
    try:
        d = _enriquecer(conn, _pedido_fila(conn, did), _ahora())
        filtro = " AND interno = 0" if publico else ""
        d["mensajes"] = [dict(m) for m in conn.execute(
            f"SELECT * FROM sp_mensajes WHERE pedido_id = ?{filtro} ORDER BY id", (did,))]
        visibles = {m["id"] for m in d["mensajes"]}
        d["archivos"] = [dict(a) for a in conn.execute(
            "SELECT id, mensaje_id, nombre, bytes, subido_por, creado_en FROM sp_archivos WHERE pedido_id = ? ORDER BY id",
            (did,)) if a["mensaje_id"] is None or a["mensaje_id"] in visibles]
        d["cargas"] = [dict(h) for h in conn.execute(
            "SELECT id, fecha, horas, nota, quien FROM sp_horas WHERE pedido_id = ? ORDER BY fecha, id", (did,))]
        return d
    finally:
        conn.close()


def pedido_por_numero(db: str, plan_id: int, numero: int) -> Optional[dict]:
    conn = _connect(db)
    try:
        f = conn.execute("SELECT id FROM sp_pedidos WHERE plan_id = ? AND numero = ?", (plan_id, numero)).fetchone()
    finally:
        conn.close()
    return pedido(db, f["id"], publico=True) if f else None


def _pasar(d: dict, nuevo: str, ahora: datetime) -> dict:
    """Los campos que cambian al pasar un pedido de estado (pausa del SLA, resuelto)."""
    if nuevo not in ESTADOS:
        raise NoSePuede("Ese estado no es válido.")
    cambios = {"estado": nuevo}
    if d["estado"] == "esperando" and nuevo != "esperando" and d.get("esperando_desde"):
        desde = _dt(d["esperando_desde"]) or ahora
        cambios["pausado_seg"] = int(d.get("pausado_seg") or 0) + max(0, int((ahora - desde).total_seconds()))
        cambios["esperando_desde"] = None
    if nuevo == "esperando" and d["estado"] != "esperando":
        cambios["esperando_desde"] = _txt(ahora)
    if nuevo in ("hecho", "descartado"):
        if d["estado"] not in ("hecho", "descartado"):
            cambios["resuelto_en"] = _txt(ahora)
    else:
        cambios["resuelto_en"] = None
    return cambios


def _aplicar(conn, did: int, cambios: dict) -> None:
    sets = ", ".join(f"{k} = ?" for k in cambios)
    conn.execute(f"UPDATE sp_pedidos SET {sets} WHERE id = ?", (*cambios.values(), did))


def responder(db: str, did: int, texto: str, quien: str, de_cliente: bool, interno: bool = False,
              estado: Optional[str] = None, archivos=()) -> dict:
    texto = _limpio(texto, LARGO_TEXTO)
    archivos = [a for a in archivos if a[0]]
    if not texto and not archivos:
        raise NoSePuede("Escribí algo o adjuntá un archivo.")
    if de_cliente and interno:
        raise NoSePuede("El cliente no deja notas internas.")
    _validar_archivos(archivos)
    ahora = _ahora()
    conn = _connect(db)
    try:
        d = _pedido_fila(conn, did)
        if conn.execute("SELECT COUNT(*) FROM sp_mensajes WHERE pedido_id = ?", (did,)).fetchone()[0] >= MAX_MENSAJES:
            raise NoSePuede("Este pedido ya tiene demasiados mensajes: abrí uno nuevo.")
        if de_cliente and d["estado"] == "descartado":
            raise NoSePuede("Este pedido está cerrado: abrí uno nuevo.")
        cur = conn.execute("INSERT INTO sp_mensajes (pedido_id, quien, de_cliente, interno, texto, creado_en) "
                           "VALUES (?,?,?,?,?,?)",
                           (did, quien, 1 if de_cliente else 0, 1 if interno else 0, texto or "(adjunto)", _txt(ahora)))
        _guardar_archivos(conn, db, d["plan_id"], did, cur.lastrowid, archivos, quien)
        cambios = {"actualizado_en": _txt(ahora)}
        if de_cliente:
            # El cliente contesta: si estábamos esperándolo o lo dimos por hecho, vuelve a la mesa.
            if d["estado"] in ("esperando", "hecho"):
                cambios.update(_pasar(d, "en_curso", ahora))
        else:
            if not interno and not d["primera_respuesta_en"]:
                cambios["primera_respuesta_en"] = _txt(ahora)
            if estado and estado != d["estado"]:
                cambios.update(_pasar(d, estado, ahora))
            elif not interno and d["estado"] == "nuevo":
                cambios.update(_pasar(d, "en_curso", ahora))
        _aplicar(conn, did, cambios)
        conn.commit()
    finally:
        conn.close()
    return pedido(db, did)


def cambiar(db: str, did: int, datos: dict) -> dict:
    ahora = _ahora()
    conn = _connect(db)
    try:
        d = _pedido_fila(conn, did)
        cambios = {}
        if "estado" in datos and datos["estado"] != d["estado"]:
            cambios.update(_pasar(d, datos["estado"], ahora))
        if "prioridad" in datos:
            if datos["prioridad"] not in PRIORIDADES:
                raise NoSePuede("Esa prioridad no es válida.")
            cambios["prioridad"] = datos["prioridad"]
        if "tipo" in datos:
            if datos["tipo"] not in TIPOS:
                raise NoSePuede("Ese tipo no es válido.")
            cambios["tipo"] = datos["tipo"]
        if "asignado" in datos:
            cambios["asignado"] = _limpio(datos["asignado"], 60) or None
        if "titulo" in datos:
            cambios["titulo"] = _limpio(datos["titulo"], LARGO_TITULO)
            if not cambios["titulo"]:
                raise NoSePuede("El título no puede quedar vacío.")
        if cambios:
            cambios["actualizado_en"] = _txt(ahora)
            _aplicar(conn, did, cambios)
            conn.commit()
    finally:
        conn.close()
    return pedido(db, did)


# ── horas ────────────────────────────────────────────────────────────────────

def cargar_horas(db: str, did: int, horas, fecha: Optional[str], nota: str, quien: str) -> dict:
    h = _numero(horas, "Horas", 0, MAX_HORAS_CARGA)
    if not h:
        raise NoSePuede("Cargá cuántas horas llevó (por ejemplo 1,5).")
    fecha = (fecha or "").strip() or hoy_uy().isoformat()
    try:
        f = date.fromisoformat(fecha)
    except ValueError:
        raise NoSePuede("La fecha no es válida.")
    if f > hoy_uy() + timedelta(days=1):
        raise NoSePuede("No se cargan horas a futuro.")
    conn = _connect(db)
    try:
        d = _pedido_fila(conn, did)
        conn.execute("INSERT INTO sp_horas (pedido_id, plan_id, fecha, horas, nota, quien, creado_en) VALUES (?,?,?,?,?,?,?)",
                     (did, d["plan_id"], f.isoformat(), h, _limpio(nota, LARGO_NOTA_HORAS) or None, quien,
                      _txt(_ahora())))
        conn.commit()
    finally:
        conn.close()
    return pedido(db, did)


def borrar_horas(db: str, hid: int) -> int:
    conn = _connect(db)
    try:
        f = conn.execute("SELECT pedido_id FROM sp_horas WHERE id = ?", (hid,)).fetchone()
        if not f:
            raise NoSePuede("Esa carga no existe.")
        conn.execute("DELETE FROM sp_horas WHERE id = ?", (hid,))
        conn.commit()
        return f["pedido_id"]
    finally:
        conn.close()


def horas_del_mes(db: str, plan_id: int, mes: Optional[str] = None) -> list:
    """El detalle de la bolsa: cada carga del mes con el pedido al que fue."""
    mes = _mes_valido(mes)
    conn = _connect(db)
    try:
        return [dict(f) for f in conn.execute(
            """SELECT h.fecha, h.horas, h.nota, d.numero, d.titulo FROM sp_horas h
               JOIN sp_pedidos d ON d.id = h.pedido_id
               WHERE h.plan_id = ? AND substr(h.fecha, 1, 7) = ? ORDER BY h.fecha, h.id""", (plan_id, mes))]
    finally:
        conn.close()


# ── resumen ──────────────────────────────────────────────────────────────────

def resumen(lista_pedidos: list, lista_planes: list) -> dict:
    abiertos = [d for d in lista_pedidos if d["estado"] in ABIERTOS]
    hechos = [d for d in lista_pedidos if d["estado"] == "hecho"]
    a_tiempo = [d for d in hechos if d["sla"]["estado"] == "cumplido"]
    activos = [p for p in lista_planes if p["activo"]]
    return {
        "abiertos": len(abiertos),
        "sin_respuesta": sum(1 for d in abiertos if not d["primera_respuesta_en"]),
        "toca_responder": sum(1 for d in abiertos if d["toca_responder"]),
        "vencidos": sum(1 for d in abiertos if d["sla"]["estado"] == "vencido"),
        "en_riesgo": sum(1 for d in abiertos if d["sla"]["estado"] == "riesgo"),
        "pct_a_tiempo": round(100 * len(a_tiempo) / len(hechos)) if hechos else None,
        "planes": len(activos),
        "horas_usadas": round(sum(p["usadas"] for p in activos), 2),
        "horas_contratadas": round(sum(p["horas_mes"] for p in activos), 2),
        "pasados": sum(1 for p in activos if p["pasado"]),
        "monto_extra": round(sum(p["monto_extra"] or 0 for p in activos), 2),
    }


# ── archivos ─────────────────────────────────────────────────────────────────

def nombre_seguro(nombre: str) -> str:
    base = unicodedata.normalize("NFKD", os.path.basename(str(nombre or ""))).encode("ascii", "ignore").decode()
    base = re.sub(r"[^A-Za-z0-9._ -]+", "_", base).strip(" .") or "archivo"
    return base[:100]


def _extension(nombre: str) -> str:
    return nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""


def _validar_archivos(archivos) -> None:
    if len(archivos) > MAX_ARCHIVOS_ENVIO:
        raise NoSePuede(f"Hasta {MAX_ARCHIVOS_ENVIO} archivos por envío.")
    for nombre, _ in archivos:
        if _extension(nombre) not in PERMITIDAS:
            raise NoSePuede(f"{nombre_seguro(nombre)}: ese tipo de archivo no se puede subir. "
                            "Mandá capturas, PDF, planillas o videos cortos.")


def _guardar_archivos(conn, db: str, plan_id: int, did: int, mid: Optional[int], archivos, quien: str) -> None:
    if not archivos:
        return
    usado = conn.execute("SELECT COALESCE(SUM(a.bytes), 0) FROM sp_archivos a JOIN sp_pedidos p ON p.id = a.pedido_id "
                         "WHERE p.plan_id = ?", (plan_id,)).fetchone()[0]
    carpeta = _dir(db)
    escritos = []
    try:
        for nombre, stream in archivos:
            ruta = secrets.token_hex(16) + "." + _extension(nombre)
            total = 0
            with open(os.path.join(carpeta, ruta), "wb") as out:
                escritos.append(ruta)
                while True:
                    trozo = stream.read(64 * 1024)
                    if not trozo:
                        break
                    total += len(trozo)
                    if total > MAX_ARCHIVO:
                        raise NoSePuede(f"{nombre_seguro(nombre)}: pesa más de {MAX_ARCHIVO // (1024 * 1024)} MB.")
                    out.write(trozo)
            if total == 0:
                raise NoSePuede(f"{nombre_seguro(nombre)}: está vacío.")
            usado += total
            if usado > MAX_POR_PLAN:
                raise NoSePuede("Se llenó el espacio de este plan: avisanos y lo liberamos.")
            conn.execute("INSERT INTO sp_archivos (pedido_id, mensaje_id, nombre, ruta, bytes, subido_por, creado_en) "
                         "VALUES (?,?,?,?,?,?,?)",
                         (did, mid, nombre_seguro(nombre), ruta, total, quien, _txt(_ahora())))
    except BaseException:
        conn.rollback()
        for r in escritos:
            _borrar_disco(db, r)
        raise


def archivo(db: str, aid: int) -> dict:
    conn = _connect(db)
    try:
        f = conn.execute("SELECT * FROM sp_archivos WHERE id = ?", (aid,)).fetchone()
    finally:
        conn.close()
    if not f:
        raise NoSePuede("Ese archivo no existe.")
    return dict(f)


def ruta_archivo(db: str, a: dict) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}\.[a-z0-9]{1,5}", a["ruta"]):
        raise NoSePuede("Ese archivo no existe.")
    return os.path.join(_dir(db), a["ruta"])


def _borrar_disco(db: str, ruta: str) -> None:
    try:
        os.remove(ruta_archivo(db, {"ruta": ruta}))
    except (OSError, NoSePuede):
        pass
