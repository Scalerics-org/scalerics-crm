"""Monitor de sitios de clientes (24/10).

Los sitios y tiendas que entregamos quedan solos después de la entrega: si se
cae el hosting, vence el SSL o un plugin rompe el checkout, nos enteramos
cuando llama el cliente. Esto los vigila y deja el historial para mostrárselo:

- cada sitio se revisa cada `INTERVALO` (la URL principal y, si tiene, una
  segunda, como el carrito de una tienda): código HTTP, tiempo de respuesta,
  una palabra que tiene que aparecer y los mensajes típicos de WordPress roto;
- el certificado SSL se mira cada `CADA_SSL` y avisa con `SSL_AVISO_DIAS`;
- dos fallas seguidas abren un incidente y el primer chequeo bien lo cierra,
  como el "confirmation period" de Better Stack: un timeout suelto no es caída;
- el reporte del mes (`/r/<código>`) es lo que ve el cliente del plan de
  mantenimiento: disponibilidad, incidentes, velocidad, SSL y lo que hicimos.

La disponibilidad se cuenta por tiempo (minutos con un incidente abierto sobre
minutos vigilados) y no por chequeos: la máquina de Fly se duerme sin tráfico y
los chequeos no caen parejos, pero un incidente abierto se cierra recién con el
primer chequeo bien, así que el tiempo caído no se pierde.

Los chequeos crudos se guardan `RETENCION_DIAS`; para los meses viejos quedan
el resumen por día (`monitor_dias`) y los incidentes, que no se borran.

Las rutas están en routes/monitor_sitios.py. Nada de esto le manda nada a
nadie: solo hace GET a los sitios que el equipo cargó.
"""

import ipaddress
import logging
import os
import re
import secrets
import socket
import sqlite3
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from database import _connect

logger = logging.getLogger(__name__)

FORMATO = "%Y-%m-%d %H:%M:%S"
MVD = ZoneInfo("America/Montevideo")

# Piso entre dos chequeos del mismo sitio. Es el tope propio del proceso de
# fondo (regla 3 de COORDINACION.md): un deploy o un reinicio no lo acelera.
INTERVALO = timedelta(minutes=10)
# "Revisar ahora" desde el panel no puede usarse para martillar un sitio.
PISO_MANUAL = timedelta(seconds=30)
CADA_SSL = timedelta(hours=6)
SSL_AVISO_DIAS = 21
TIMEOUT = 15
LENTO_MS = 3000
FALLAS_PARA_INCIDENTE = 2
RETENCION_DIAS = 35
MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECCIONES = 5
MAX_POR_VUELTA = 60
HILOS = 8
LARGO_NOMBRE = 120
LARGO_URL = 500
LARGO_PALABRA = 120
LARGO_NOTA = 4000
AGENTE = "ScalericsMonitor/1.0 (+https://scalerics.com)"

# Lo que muestra un WordPress roto con código 200 o 500. Un sitio que dice esto
# está caído para el cliente aunque el servidor conteste.
ROTO = (
    "error establishing a database connection",
    "error al establecer una conexión con la base de datos",
    "there has been a critical error on this website",
    "ha habido un error crítico en esta web",
    "hay un error crítico en esta web",
    "briefly unavailable for scheduled maintenance",
    "account suspended",
    "this account has been suspended",
)

ESTADOS = ("ok", "lento", "ssl", "falla", "caido", "sin_datos", "pausado")
PLANES = ("mantenimiento", "sin_plan")


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
        return datetime.strptime(s[:19], FORMATO)
    except ValueError:
        return None


def _dia_mvd(utc: datetime) -> str:
    return utc.replace(tzinfo=timezone.utc).astimezone(MVD).date().isoformat()


def init_monitor_sitios(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS monitor_sitios (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id      INTEGER REFERENCES businesses(id) ON DELETE SET NULL,
            nombre           TEXT NOT NULL,
            url              TEXT NOT NULL,
            url_extra        TEXT,
            palabra          TEXT,
            plan             TEXT NOT NULL DEFAULT 'sin_plan',
            activo           INTEGER NOT NULL DEFAULT 1,
            codigo           TEXT NOT NULL UNIQUE,
            creado_por       TEXT NOT NULL,
            creado_en        TEXT NOT NULL,
            ultimo_chequeo   TEXT,
            ultimo_ok        INTEGER,
            ultimo_ms        INTEGER,
            ultimo_error     TEXT,
            fallas_seguidas  INTEGER NOT NULL DEFAULT 0,
            ssl_vence        TEXT,
            ssl_revisado_en  TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS monitor_chequeos (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            sitio_id   INTEGER NOT NULL REFERENCES monitor_sitios(id) ON DELETE CASCADE,
            en         TEXT NOT NULL,
            ok         INTEGER NOT NULL,
            codigo     INTEGER,
            ms         INTEGER,
            error      TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_monitor_chequeos_sitio "
                 "ON monitor_chequeos(sitio_id, en)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS monitor_incidentes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            sitio_id   INTEGER NOT NULL REFERENCES monitor_sitios(id) ON DELETE CASCADE,
            empezo     TEXT NOT NULL,
            termino    TEXT,
            causa      TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_monitor_incidentes_sitio "
                 "ON monitor_incidentes(sitio_id, empezo)")
    # Resumen por día de Montevideo: lo que queda de los meses viejos cuando
    # los chequeos crudos ya se borraron.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS monitor_dias (
            sitio_id   INTEGER NOT NULL REFERENCES monitor_sitios(id) ON DELETE CASCADE,
            dia        TEXT NOT NULL,
            chequeos   INTEGER NOT NULL DEFAULT 0,
            fallas     INTEGER NOT NULL DEFAULT 0,
            ms_suma    INTEGER NOT NULL DEFAULT 0,
            ms_n       INTEGER NOT NULL DEFAULT 0,
            ms_max     INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (sitio_id, dia)
        )
    """)
    # "Qué hicimos este mes": lo escribe el equipo y sale en el reporte.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS monitor_notas (
            sitio_id       INTEGER NOT NULL REFERENCES monitor_sitios(id) ON DELETE CASCADE,
            mes            TEXT NOT NULL,
            texto          TEXT NOT NULL,
            editado_por    TEXT,
            editado_en     TEXT,
            PRIMARY KEY (sitio_id, mes)
        )
    """)


# ── URLs ─────────────────────────────────────────────────────────────────────

def _ip_publica(ip: str) -> bool:
    try:
        d = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return d.is_global and not d.is_multicast


def _resolver(host: str) -> list:
    return [info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)]


def normalizar_url(url: str) -> str:
    """https:// si falta, sin espacios. NoSePuede si no es una URL web."""
    url = (url or "").strip()
    if not url:
        raise NoSePuede("Falta la dirección del sitio")
    if "://" not in url:
        url = "https://" + url
    if len(url) > LARGO_URL:
        raise NoSePuede("La dirección es demasiado larga")
    partes = urlsplit(url)
    if partes.scheme not in ("http", "https") or not partes.hostname:
        raise NoSePuede("La dirección tiene que ser http o https")
    if partes.username or partes.password:
        raise NoSePuede("La dirección no puede llevar usuario ni contraseña")
    return url


def _host_permitido(url: str, resolver: Optional[Callable] = None) -> bool:
    """Que la URL no apunte a la red interna (la de Fly incluida).

    El que carga el sitio es del equipo, pero una redirección del sitio vigilado
    no lo es: por eso se mira en cada salto y no solo al cargar.
    """
    resolver = resolver or _resolver
    host = urlsplit(url).hostname or ""
    if not host or host == "localhost" or host.endswith((".internal", ".local", ".localhost")):
        return False
    try:
        ipaddress.ip_address(host)
        ips = [host]
    except ValueError:
        try:
            ips = resolver(host)
        except OSError:
            return True  # que falle el GET con "no se encontró", no con "no permitida"
    return bool(ips) and all(_ip_publica(ip) for ip in ips)


# ── el chequeo ───────────────────────────────────────────────────────────────

def _obtener(url: str, resolver: Optional[Callable] = None) -> dict:
    """GET siguiendo redirecciones a mano, para validar cada salto.

    Devuelve {codigo, texto, ms} o {error}. El tiempo es hasta el último byte
    leído de la última respuesta, con tope de MAX_BYTES.
    """
    import requests

    inicio = time.monotonic()
    actual = url
    for _ in range(MAX_REDIRECCIONES + 1):
        if not _host_permitido(actual, resolver):
            return {"error": "La dirección apunta a una red interna"}
        try:
            r = requests.get(actual, timeout=TIMEOUT, allow_redirects=False, stream=True,
                             headers={"User-Agent": AGENTE, "Accept": "text/html,*/*"})
        except requests.exceptions.SSLError:
            return {"error": "Certificado SSL inválido o vencido"}
        except requests.exceptions.Timeout:
            return {"error": f"No contestó en {TIMEOUT} s"}
        except requests.exceptions.ConnectionError as e:
            texto = str(e).lower()
            if "name or service not known" in texto or "nodename nor servname" in texto or "getaddrinfo" in texto:
                return {"error": "El dominio no resuelve (DNS)"}
            return {"error": "No se pudo conectar"}
        except requests.exceptions.RequestException:
            return {"error": "No se pudo conectar"}
        try:
            if r.is_redirect and r.headers.get("Location"):
                actual = urljoin(actual, r.headers["Location"])
                continue
            leido = b""
            for trozo in r.iter_content(65536):
                leido += trozo
                if len(leido) >= MAX_BYTES:
                    break
            ms = int((time.monotonic() - inicio) * 1000)
            texto = leido.decode(r.encoding or "utf-8", errors="replace")
            return {"codigo": r.status_code, "texto": texto, "ms": ms}
        finally:
            r.close()
    return {"error": "Demasiadas redirecciones"}


def _vence_ssl(host: str, puerto: int = 443) -> Optional[datetime]:
    ctx = ssl.create_default_context()
    with socket.create_connection((host, puerto), timeout=10) as s:
        with ctx.wrap_socket(s, server_hostname=host) as t:
            cert = t.getpeercert()
    if not cert or "notAfter" not in cert:
        return None
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), timezone.utc).replace(tzinfo=None)


def evaluar(respuesta: dict, palabra: Optional[str] = None) -> dict:
    """Si una respuesta de `_obtener` está bien para el cliente. Pura, para los tests."""
    if respuesta.get("error"):
        return {"ok": False, "codigo": None, "ms": None, "error": respuesta["error"]}
    codigo, ms = respuesta.get("codigo"), respuesta.get("ms")
    texto = (respuesta.get("texto") or "").lower()
    for frase in ROTO:
        if frase in texto:
            return {"ok": False, "codigo": codigo, "ms": ms, "error": f"El sitio muestra «{frase}»"}
    if codigo is None or codigo >= 400:
        return {"ok": False, "codigo": codigo, "ms": ms, "error": f"Contestó {codigo}"}
    if palabra and palabra.strip().lower() not in texto:
        return {"ok": False, "codigo": codigo, "ms": ms, "error": f"No aparece «{palabra.strip()}»"}
    return {"ok": True, "codigo": codigo, "ms": ms, "error": None}


def chequear(sitio: dict, obtener: Optional[Callable] = None) -> dict:
    """La URL principal y la extra. Está bien solo si las dos están bien."""
    obtener = obtener or _obtener
    r = evaluar(obtener(sitio["url"]), sitio.get("palabra"))
    if r["ok"] and sitio.get("url_extra"):
        extra = evaluar(obtener(sitio["url_extra"]))
        if not extra["ok"]:
            r = {**r, "ok": False, "error": "Segunda dirección: " + extra["error"]}
    return r


# ── guardar ──────────────────────────────────────────────────────────────────

def _fila(conn, sitio_id: int) -> dict:
    f = conn.execute("SELECT * FROM monitor_sitios WHERE id = ?", (sitio_id,)).fetchone()
    if not f:
        raise NoSePuede("Ese sitio no existe")
    return dict(f)


def registrar(db: str, sitio_id: int, r: dict, en: Optional[datetime] = None,
              ssl_vence: Optional[datetime] = None, ssl_revisado: bool = False) -> dict:
    """Guarda un chequeo y abre o cierra el incidente. Devuelve el sitio."""
    en = en or _ahora()
    conn = _connect(db)
    try:
        s = _fila(conn, sitio_id)
        fallas = 0 if r["ok"] else s["fallas_seguidas"] + 1
        conn.execute("INSERT INTO monitor_chequeos (sitio_id, en, ok, codigo, ms, error) VALUES (?,?,?,?,?,?)",
                     (sitio_id, _txt(en), int(r["ok"]), r.get("codigo"), r.get("ms"), r.get("error")))
        ms = r.get("ms") if r["ok"] else None
        conn.execute("""
            INSERT INTO monitor_dias (sitio_id, dia, chequeos, fallas, ms_suma, ms_n, ms_max)
            VALUES (?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT(sitio_id, dia) DO UPDATE SET
                chequeos = chequeos + 1, fallas = fallas + excluded.fallas,
                ms_suma = ms_suma + excluded.ms_suma, ms_n = ms_n + excluded.ms_n,
                ms_max = MAX(ms_max, excluded.ms_max)
        """, (sitio_id, _dia_mvd(en), 0 if r["ok"] else 1, ms or 0, 1 if ms is not None else 0, ms or 0))
        abierto = conn.execute("SELECT id FROM monitor_incidentes WHERE sitio_id = ? AND termino IS NULL",
                               (sitio_id,)).fetchone()
        if r["ok"] and abierto:
            conn.execute("UPDATE monitor_incidentes SET termino = ? WHERE id = ?", (_txt(en), abierto["id"]))
        elif not r["ok"] and not abierto and fallas >= FALLAS_PARA_INCIDENTE:
            # Empieza con la primera falla de la racha, no con la que lo confirmó.
            primera = conn.execute(
                "SELECT en FROM monitor_chequeos WHERE sitio_id = ? AND ok = 0 ORDER BY en DESC LIMIT 1 OFFSET ?",
                (sitio_id, fallas - 1)).fetchone()
            conn.execute("INSERT INTO monitor_incidentes (sitio_id, empezo, causa) VALUES (?,?,?)",
                         (sitio_id, primera["en"] if primera else _txt(en), r.get("error")))
        campos = {"ultimo_chequeo": _txt(en), "ultimo_ok": int(r["ok"]), "ultimo_ms": r.get("ms"),
                  "ultimo_error": r.get("error"), "fallas_seguidas": fallas}
        if ssl_revisado:
            campos["ssl_revisado_en"] = _txt(en)
            campos["ssl_vence"] = _txt(ssl_vence) if ssl_vence else None
        conn.execute("UPDATE monitor_sitios SET " + ", ".join(f"{k} = ?" for k in campos) + " WHERE id = ?",
                     (*campos.values(), sitio_id))
        conn.commit()
        return _fila(conn, sitio_id)
    finally:
        conn.close()


def revisar(db: str, sitio_id: int, obtener: Optional[Callable] = None,
            vence_ssl: Optional[Callable] = None, ahora: Optional[datetime] = None) -> dict:
    """Un chequeo completo de un sitio (sin mirar el intervalo)."""
    vence_ssl = vence_ssl or _vence_ssl
    ahora = ahora or _ahora()
    conn = _connect(db)
    try:
        s = _fila(conn, sitio_id)
    finally:
        conn.close()
    r = chequear(s, obtener)
    ssl_kw = {}
    revisado = _dt(s["ssl_revisado_en"])
    host = urlsplit(s["url"]).hostname
    if urlsplit(s["url"]).scheme == "https" and host and (not revisado or ahora - revisado >= CADA_SSL):
        try:
            ssl_kw = {"ssl_vence": vence_ssl(host), "ssl_revisado": True}
        except (OSError, ssl.SSLError, ValueError):
            ssl_kw = {"ssl_vence": None, "ssl_revisado": True}
    return registrar(db, sitio_id, r, en=ahora, **ssl_kw)


def revisar_manual(db: str, sitio_id: int, **kw) -> dict:
    conn = _connect(db)
    try:
        s = _fila(conn, sitio_id)
    finally:
        conn.close()
    ultimo = _dt(s["ultimo_chequeo"])
    if ultimo and _ahora() - ultimo < PISO_MANUAL:
        raise NoSePuede("Se revisó hace menos de 30 segundos")
    return revisar(db, sitio_id, **kw)


def vencidos(db: str, ahora: Optional[datetime] = None) -> list:
    """Los sitios activos a los que ya les toca, el más atrasado primero."""
    ahora = ahora or _ahora()
    conn = _connect(db)
    try:
        filas = conn.execute(
            "SELECT id FROM monitor_sitios WHERE activo = 1 AND (ultimo_chequeo IS NULL OR ultimo_chequeo <= ?) "
            "ORDER BY ultimo_chequeo IS NOT NULL, ultimo_chequeo LIMIT ?",
            (_txt(ahora - INTERVALO), MAX_POR_VUELTA)).fetchall()
        return [f["id"] for f in filas]
    finally:
        conn.close()


_vuelta = threading.Lock()


def vuelta(db: str, obtener: Optional[Callable] = None, vence_ssl: Optional[Callable] = None) -> int:
    """Revisa los sitios a los que les toca. Una vuelta a la vez. Devuelve cuántos."""
    if not _vuelta.acquire(blocking=False):
        return 0
    try:
        ids = vencidos(db)
        if not ids:
            return 0

        def uno(sid):
            try:
                revisar(db, sid, obtener=obtener, vence_ssl=vence_ssl)
            except Exception as e:  # un sitio raro no frena a los demás
                logger.warning(f"Monitor de sitios: sitio {sid}: {e}")

        with ThreadPoolExecutor(max_workers=HILOS) as ex:
            list(ex.map(uno, ids))
        purgar(db)
        return len(ids)
    finally:
        _vuelta.release()


def purgar(db: str, ahora: Optional[datetime] = None) -> int:
    ahora = ahora or _ahora()
    conn = _connect(db)
    try:
        n = conn.execute("DELETE FROM monitor_chequeos WHERE en < ?",
                         (_txt(ahora - timedelta(days=RETENCION_DIAS)),)).rowcount
        conn.commit()
        return n
    finally:
        conn.close()


# ── alta y edición ───────────────────────────────────────────────────────────

def _limpiar(datos: dict, parcial: bool = False) -> dict:
    out = {}
    if not parcial or "nombre" in datos:
        nombre = (datos.get("nombre") or "").strip()
        if not nombre:
            raise NoSePuede("Falta el nombre")
        out["nombre"] = nombre[:LARGO_NOMBRE]
    if not parcial or "url" in datos:
        out["url"] = normalizar_url(datos.get("url"))
    if not parcial or "url_extra" in datos:
        extra = (datos.get("url_extra") or "").strip()
        out["url_extra"] = normalizar_url(extra) if extra else None
    if not parcial or "palabra" in datos:
        out["palabra"] = (datos.get("palabra") or "").strip()[:LARGO_PALABRA] or None
    if not parcial or "plan" in datos:
        plan = datos.get("plan") or "sin_plan"
        if plan not in PLANES:
            raise NoSePuede("Plan desconocido")
        out["plan"] = plan
    if "business_id" in datos:
        try:
            out["business_id"] = int(datos["business_id"]) if datos["business_id"] not in (None, "") else None
        except (TypeError, ValueError):
            raise NoSePuede("Cliente inválido")
    if parcial and "activo" in datos:
        out["activo"] = 1 if datos["activo"] else 0
    for clave in ("url", "url_extra"):
        if out.get(clave) and not _host_permitido(out[clave]):
            raise NoSePuede("Esa dirección apunta a una red interna")
    return out


def crear(db: str, datos: dict, quien: str) -> dict:
    campos = _limpiar(datos)
    campos.update(codigo=secrets.token_urlsafe(18), creado_por=quien or "sistema", creado_en=_txt(_ahora()))
    conn = _connect(db)
    try:
        if conn.execute("SELECT 1 FROM monitor_sitios WHERE url = ?", (campos["url"],)).fetchone():
            raise NoSePuede("Ese sitio ya está en el monitor")
        cur = conn.execute(f"INSERT INTO monitor_sitios ({', '.join(campos)}) VALUES ({', '.join('?' * len(campos))})",
                           tuple(campos.values()))
        conn.commit()
        return _fila(conn, cur.lastrowid)
    finally:
        conn.close()


def editar(db: str, sitio_id: int, datos: dict) -> dict:
    campos = _limpiar(datos, parcial=True)
    conn = _connect(db)
    try:
        _fila(conn, sitio_id)
        if campos:
            conn.execute("UPDATE monitor_sitios SET " + ", ".join(f"{k} = ?" for k in campos) + " WHERE id = ?",
                         (*campos.values(), sitio_id))
            if campos.get("activo") == 0:
                # Pausado no es caído: el incidente abierto se cierra acá.
                conn.execute("UPDATE monitor_incidentes SET termino = ? WHERE sitio_id = ? AND termino IS NULL",
                             (_txt(_ahora()), sitio_id))
                conn.execute("UPDATE monitor_sitios SET fallas_seguidas = 0 WHERE id = ?", (sitio_id,))
            conn.commit()
        return _fila(conn, sitio_id)
    finally:
        conn.close()


def borrar(db: str, sitio_id: int) -> None:
    conn = _connect(db)
    try:
        _fila(conn, sitio_id)
        for t in ("monitor_chequeos", "monitor_incidentes", "monitor_dias", "monitor_notas"):
            conn.execute(f"DELETE FROM {t} WHERE sitio_id = ?", (sitio_id,))
        conn.execute("DELETE FROM monitor_sitios WHERE id = ?", (sitio_id,))
        conn.commit()
    finally:
        conn.close()


def guardar_nota(db: str, sitio_id: int, mes: str, texto: str, quien: str) -> None:
    mes = validar_mes(mes)
    texto = (texto or "").strip()[:LARGO_NOTA]
    conn = _connect(db)
    try:
        _fila(conn, sitio_id)
        if texto:
            conn.execute("""
                INSERT INTO monitor_notas (sitio_id, mes, texto, editado_por, editado_en) VALUES (?,?,?,?,?)
                ON CONFLICT(sitio_id, mes) DO UPDATE SET texto = excluded.texto,
                    editado_por = excluded.editado_por, editado_en = excluded.editado_en
            """, (sitio_id, mes, texto, quien, _txt(_ahora())))
        else:
            conn.execute("DELETE FROM monitor_notas WHERE sitio_id = ? AND mes = ?", (sitio_id, mes))
        conn.commit()
    finally:
        conn.close()


def sugeridos(db: str) -> list:
    """Clientes con sitio cargado que todavía no están en el monitor."""
    from database import ETAPAS_CLIENTE
    conn = _connect(db)
    try:
        marcas = ",".join("?" * len(ETAPAS_CLIENTE))
        filas = conn.execute(f"""
            SELECT b.id, b.name, b.website FROM businesses b
            WHERE b.crm_status IN ({marcas}) AND COALESCE(TRIM(b.website), '') != ''
              AND b.id NOT IN (SELECT business_id FROM monitor_sitios WHERE business_id IS NOT NULL)
            ORDER BY b.name LIMIT 100
        """, ETAPAS_CLIENTE).fetchall()
        return [dict(f) for f in filas]
    finally:
        conn.close()


# ── números ──────────────────────────────────────────────────────────────────

def _caido_entre(incidentes: list, desde: datetime, hasta: datetime, ahora: datetime) -> float:
    """Segundos con un incidente abierto dentro de [desde, hasta)."""
    total = 0.0
    for i in incidentes:
        a = max(_dt(i["empezo"]), desde)
        b = min(_dt(i["termino"]) or ahora, hasta)
        if b > a:
            total += (b - a).total_seconds()
    return total


def disponibilidad(incidentes: list, desde: datetime, hasta: datetime, creado: datetime,
                   ahora: datetime) -> Optional[float]:
    """Fracción del tiempo vigilado sin incidentes. None si no hubo tiempo vigilado."""
    desde = max(desde, creado)
    hasta = min(hasta, ahora)
    if hasta <= desde:
        return None
    vigilado = (hasta - desde).total_seconds()
    return max(0.0, 1 - _caido_entre(incidentes, desde, hasta, ahora) / vigilado)


def estado(s: dict, incidente_abierto: bool, ahora: datetime) -> str:
    if not s["activo"]:
        return "pausado"
    if not s["ultimo_chequeo"]:
        return "sin_datos"
    if incidente_abierto:
        return "caido"
    if not s["ultimo_ok"]:
        return "falla"
    vence = _dt(s["ssl_vence"])
    if vence and (vence - ahora).days < SSL_AVISO_DIAS:
        return "ssl"
    if s["ultimo_ms"] and s["ultimo_ms"] > LENTO_MS:
        return "lento"
    return "ok"


def _incidentes(conn, sitio_id: int, desde: Optional[datetime] = None) -> list:
    if desde:
        filas = conn.execute(
            "SELECT * FROM monitor_incidentes WHERE sitio_id = ? AND (termino IS NULL OR termino >= ?) ORDER BY empezo",
            (sitio_id, _txt(desde))).fetchall()
    else:
        filas = conn.execute("SELECT * FROM monitor_incidentes WHERE sitio_id = ? ORDER BY empezo",
                             (sitio_id,)).fetchall()
    return [dict(f) for f in filas]


def _barras(conn, sitio_id: int, ahora: datetime, horas: int = 48) -> list:
    """Una barra por hora: None sin chequeos, si no {ok, ms} (ok = ninguna falla)."""
    desde = ahora - timedelta(hours=horas)
    filas = conn.execute("SELECT en, ok, ms FROM monitor_chequeos WHERE sitio_id = ? AND en >= ? ORDER BY en",
                         (sitio_id, _txt(desde))).fetchall()
    cubos = [None] * horas
    for f in filas:
        i = int((_dt(f["en"]) - desde).total_seconds() // 3600)
        if 0 <= i < horas:
            c = cubos[i] or {"ok": True, "ms": [], "n": 0}
            c["ok"] = c["ok"] and bool(f["ok"])
            c["n"] += 1
            if f["ms"] is not None and f["ok"]:
                c["ms"].append(f["ms"])
            cubos[i] = c
    return [None if c is None else {"ok": c["ok"], "ms": round(sum(c["ms"]) / len(c["ms"])) if c["ms"] else None}
            for c in cubos]


def _ms_medio(conn, sitio_id: int, desde_dia: str) -> Optional[int]:
    f = conn.execute("SELECT SUM(ms_suma) s, SUM(ms_n) n FROM monitor_dias WHERE sitio_id = ? AND dia >= ?",
                     (sitio_id, desde_dia)).fetchone()
    return round(f["s"] / f["n"]) if f and f["n"] else None


def _publico(s: dict) -> dict:
    return {k: s[k] for k in ("id", "business_id", "nombre", "url", "url_extra", "palabra", "plan", "activo",
                              "codigo", "creado_por", "creado_en", "ultimo_chequeo", "ultimo_ok", "ultimo_ms",
                              "ultimo_error", "ssl_vence")}


def listar(db: str, ahora: Optional[datetime] = None) -> list:
    ahora = ahora or _ahora()
    conn = _connect(db)
    try:
        filas = conn.execute("""
            SELECT s.*, b.name AS cliente_nombre FROM monitor_sitios s
            LEFT JOIN businesses b ON b.id = s.business_id ORDER BY s.nombre COLLATE NOCASE
        """).fetchall()
        hace7 = _dia_mvd(ahora - timedelta(days=7))
        out = []
        for f in filas:
            s = dict(f)
            creado = _dt(s["creado_en"])
            inc = _incidentes(conn, s["id"], ahora - timedelta(days=30))
            abierto = next((i for i in inc if not i["termino"]), None)
            vence = _dt(s["ssl_vence"])
            out.append({
                **_publico(s),
                "cliente_nombre": s["cliente_nombre"],
                "estado": estado(s, bool(abierto), ahora),
                "caido_desde": abierto["empezo"] if abierto else None,
                "disp_24h": disponibilidad(inc, ahora - timedelta(hours=24), ahora, creado, ahora),
                "disp_30d": disponibilidad(inc, ahora - timedelta(days=30), ahora, creado, ahora),
                "incidentes_30d": len(inc),
                "ms_7d": _ms_medio(conn, s["id"], hace7),
                "ssl_dias": (vence - ahora).days if vence else None,
                "barras": _barras(conn, s["id"], ahora),
            })
        return out
    finally:
        conn.close()


def resumen(sitios: list) -> dict:
    activos = [s for s in sitios if s["activo"]]
    disp = [s["disp_30d"] for s in activos if s["disp_30d"] is not None]
    por_estado = {e: 0 for e in ESTADOS}
    for s in sitios:
        por_estado[s["estado"]] += 1
    return {
        "total": len(sitios),
        "activos": len(activos),
        "por_estado": por_estado,
        "disp_30d": sum(disp) / len(disp) if disp else None,
        "ssl_por_vencer": sum(1 for s in activos if s["ssl_dias"] is not None and s["ssl_dias"] < SSL_AVISO_DIAS),
        "con_plan": sum(1 for s in sitios if s["plan"] == "mantenimiento"),
    }


def detalle(db: str, sitio_id: int, ahora: Optional[datetime] = None) -> dict:
    ahora = ahora or _ahora()
    conn = _connect(db)
    try:
        s = _fila(conn, sitio_id)
        chequeos = [dict(f) for f in conn.execute(
            "SELECT en, ok, codigo, ms, error FROM monitor_chequeos WHERE sitio_id = ? ORDER BY en DESC LIMIT 30",
            (sitio_id,)).fetchall()]
        incidentes = _incidentes(conn, sitio_id)[-20:][::-1]
        notas = [dict(f) for f in conn.execute(
            "SELECT mes, texto, editado_por, editado_en FROM monitor_notas WHERE sitio_id = ? ORDER BY mes DESC",
            (sitio_id,)).fetchall()]
    finally:
        conn.close()
    for i in incidentes:
        fin = _dt(i["termino"]) or ahora
        i["minutos"] = round((fin - _dt(i["empezo"])).total_seconds() / 60)
    return {**_publico(s), "chequeos": chequeos, "incidentes": incidentes, "notas": notas}


# ── reporte mensual ──────────────────────────────────────────────────────────

def validar_mes(mes: Optional[str]) -> str:
    if not mes or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", mes):
        raise NoSePuede("Mes inválido (AAAA-MM)")
    return mes


def mes_actual(ahora: Optional[datetime] = None) -> str:
    ahora = ahora or _ahora()
    return ahora.replace(tzinfo=timezone.utc).astimezone(MVD).strftime("%Y-%m")


def _limites_mes(mes: str) -> tuple:
    """[inicio, fin) del mes de Montevideo, en UTC sin tz."""
    a, m = int(mes[:4]), int(mes[5:])
    inicio = datetime(a, m, 1, tzinfo=MVD)
    fin = datetime(a + (m == 12), m % 12 + 1, 1, tzinfo=MVD)
    utc = lambda d: d.astimezone(timezone.utc).replace(tzinfo=None)  # noqa: E731
    return utc(inicio), utc(fin)


def por_codigo(db: str, codigo: str) -> Optional[dict]:
    if not codigo or len(codigo) > 64:
        return None
    conn = _connect(db)
    try:
        f = conn.execute("SELECT * FROM monitor_sitios WHERE codigo = ?", (codigo,)).fetchone()
        return dict(f) if f else None
    finally:
        conn.close()


def reporte(db: str, sitio_id: int, mes: str, ahora: Optional[datetime] = None) -> dict:
    """Lo que ve el cliente: disponibilidad, incidentes, velocidad, SSL y notas del mes."""
    ahora = ahora or _ahora()
    mes = validar_mes(mes)
    inicio, fin = _limites_mes(mes)
    conn = _connect(db)
    try:
        s = _fila(conn, sitio_id)
        creado = _dt(s["creado_en"])
        inc = [i for i in _incidentes(conn, sitio_id, inicio) if _dt(i["empezo"]) < fin]
        dia_ini = inicio.replace(tzinfo=timezone.utc).astimezone(MVD).date()
        dia_fin = (fin - timedelta(seconds=1)).replace(tzinfo=timezone.utc).astimezone(MVD).date()
        filas = {f["dia"]: dict(f) for f in conn.execute(
            "SELECT * FROM monitor_dias WHERE sitio_id = ? AND dia BETWEEN ? AND ?",
            (sitio_id, dia_ini.isoformat(), dia_fin.isoformat())).fetchall()}
        nota = conn.execute("SELECT texto FROM monitor_notas WHERE sitio_id = ? AND mes = ?",
                            (sitio_id, mes)).fetchone()
    finally:
        conn.close()
    dias = []
    d = dia_ini
    while d <= dia_fin:
        d_ini = datetime(d.year, d.month, d.day, tzinfo=MVD).astimezone(timezone.utc).replace(tzinfo=None)
        d_fin = d_ini + timedelta(days=1)
        f = filas.get(d.isoformat())
        dias.append({
            "dia": d.isoformat(),
            "disp": disponibilidad(inc, d_ini, d_fin, creado, ahora),
            "chequeos": f["chequeos"] if f else 0,
        })
        d += timedelta(days=1)
    ms_s = sum(f["ms_suma"] for f in filas.values())
    ms_n = sum(f["ms_n"] for f in filas.values())
    caido = _caido_entre(inc, max(inicio, creado), min(fin, ahora), ahora)
    vence = _dt(s["ssl_vence"])
    return {
        "sitio": {"nombre": s["nombre"], "url": s["url"], "plan": s["plan"]},
        "mes": mes,
        "en_curso": inicio <= ahora < fin,
        "disponibilidad": disponibilidad(inc, inicio, fin, creado, ahora),
        "minutos_caido": round(caido / 60),
        "incidentes": [{"empezo": i["empezo"], "termino": i["termino"], "causa": i["causa"],
                        "minutos": round(((_dt(i["termino"]) or ahora) - _dt(i["empezo"])).total_seconds() / 60)}
                       for i in inc],
        "chequeos": sum(f["chequeos"] for f in filas.values()),
        "ms_medio": round(ms_s / ms_n) if ms_n else None,
        "ms_max": max((f["ms_max"] for f in filas.values()), default=None) or None,
        "ssl_vence": s["ssl_vence"],
        "ssl_dias": (vence - ahora).days if vence else None,
        "dias": dias,
        "nota": nota["texto"] if nota else "",
    }


def meses_con_datos(db: str, sitio_id: int, ahora: Optional[datetime] = None) -> list:
    """Del mes de alta al actual, el más nuevo primero (máximo 24)."""
    conn = _connect(db)
    try:
        s = _fila(conn, sitio_id)
    finally:
        conn.close()
    ini = mes_actual(_dt(s["creado_en"]))
    actual = mes_actual(ahora)
    out = []
    a, m = int(actual[:4]), int(actual[5:])
    while len(out) < 24:
        mes = f"{a:04d}-{m:02d}"
        out.append(mes)
        if mes <= ini:
            break
        a, m = (a - 1, 12) if m == 1 else (a, m - 1)
    return out


# ── proceso de fondo ─────────────────────────────────────────────────────────

def activo() -> bool:
    return os.environ.get("MONITOR_SITIOS", "on").lower() not in ("off", "0", "false", "no")


def start_monitor_sitios(app) -> None:
    """Un hilo que da una vuelta por minuto; el intervalo por sitio lo pone `vencidos`.

    MONITOR_SITIOS=off lo apaga. Sin sitios cargados no hace nada.
    """
    if not activo():
        logger.info("Monitor de sitios apagado por MONITOR_SITIOS=off")
        return

    def _loop():
        time.sleep(120)
        while True:
            try:
                vuelta(app.config["DB_PATH"])
            except Exception as e:
                logger.warning(f"Monitor de sitios: {e}")
            time.sleep(60)

    threading.Thread(target=_loop, daemon=True, name="monitor-sitios").start()
    logger.info("Monitor de sitios ACTIVO: cada sitio se revisa cada %d min", INTERVALO.seconds // 60)


def fecha_larga(mes: str) -> str:
    nombres = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
               "septiembre", "octubre", "noviembre", "diciembre")
    return f"{nombres[int(mes[5:]) - 1]} {mes[:4]}"


def dia_corto(iso: str) -> str:
    return str(date.fromisoformat(iso).day)
