"""Analítica de contactos de los sitios de clientes.

Un script de 2 KB (`/a/s.js`) que se pega en los sitios que entregamos y
manda al CRM dos cosas: cada página vista y cada contacto (clic a WhatsApp,
llamada, mail, formulario enviado o compra terminada en WooCommerce). Lo que
le importa al dueño de un comercio no es cuántas visitas tuvo, es cuántos
clientes le trajo la web: esto lo contesta, por página y por origen.

Privacidad, igual que Plausible y Fathom:
- Sin cookies. El script guarda el origen de la visita en sessionStorage, que
  muere al cerrar la pestaña, solo para atribuir el contacto a quien lo trajo.
- No se guarda la IP ni el navegador. El visitante es un hash de IP, navegador
  y sitio con una sal del día que se borra al día siguiente: alcanza para
  contar visitantes únicos del día y no permite seguir a nadie entre días.
- Solo se guarda la ruta de la página, sin la query (ahí viajan mails y
  tokens de los formularios).

Defensas del endpoint público (`/a/e`), que cualquiera puede llamar:
- La clave del sitio va en el script y es pública: no es un secreto. Lo que
  frena la basura es que la página que manda el evento tenga que ser de uno
  de los dominios cargados del sitio, con el `Origin` del navegador igual.
- Tope por IP por minuto y tope de eventos por sitio por día.
- Robots afuera por User-Agent.
"""

import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

MVD = ZoneInfo("America/Montevideo")

TIPOS_CONTACTO = ("whatsapp", "llamada", "mail", "formulario", "compra")
TIPOS = ("vista",) + TIPOS_CONTACTO
NOMBRE_TIPO = {"whatsapp": "WhatsApp", "llamada": "Llamadas", "mail": "Mails",
               "formulario": "Formularios", "compra": "Compras"}

MAX_SITIOS = 200
MAX_DOMINIOS = 5
MAX_EVENTOS_DIA = 50_000      # por sitio: un sitio de comercio hace cientos
TOPE_IP_MINUTO = 60
RETENCION_DIAS = 400          # 13 meses: el reporte compara contra el año anterior
DIAS_SIN_DATOS = 3            # sin eventos en 3 días: el script se cayó del sitio
LARGO_PAGINA = 200
LARGO_DETALLE = 60
LARGO_CUERPO = 4096
PAGINA_PRUEBA = "(página de prueba)"

# Páginas de "gracias por tu compra" de WooCommerce, en inglés y en español.
_COMPRA = re.compile(r"/(order-received|pedido-recibido)(/|$)", re.I)

_ROBOT = re.compile(
    r"bot|crawl|spider|slurp|headless|lighthouse|pagespeed|gtmetrix|pingdom|uptime|monitor|"
    r"preview|facebookexternalhit|whatsapp|telegram|curl|wget|python|java/|go-http|axios|"
    r"node-fetch|phantom|selenium|puppeteer|playwright", re.I)

_DOMINIO = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class NoSePuede(Exception):
    """Un pedido del panel que no se puede cumplir, con el motivo para mostrar."""


# ── tiempo ───────────────────────────────────────────────────────────────────

def _ahora() -> datetime:
    """UTC sin tzinfo. Los tests la tapan."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _txt(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S")


def dia_mvd(d: datetime) -> str:
    return d.replace(tzinfo=timezone.utc).astimezone(MVD).strftime("%Y-%m-%d")


def dia_hora_mvd(d: datetime) -> str:
    return d.replace(tzinfo=timezone.utc).astimezone(MVD).strftime("%H")


def hoy_mvd() -> date:
    return date.fromisoformat(dia_mvd(_ahora()))


MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "setiembre", "octubre", "noviembre", "diciembre")


def fecha_larga(mes: str) -> str:
    a, m = mes.split("-")
    return f"{MESES[int(m) - 1].capitalize()} {a}"


# ── tablas ───────────────────────────────────────────────────────────────────

def init_analitica(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS analitica_sitios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        dominios TEXT NOT NULL,
        business_id INTEGER,
        clave TEXT NOT NULL UNIQUE,
        codigo TEXT NOT NULL UNIQUE,
        ticket_medio INTEGER,
        cierre_pct INTEGER,
        activo INTEGER NOT NULL DEFAULT 1,
        ultimo_evento TEXT,
        creado_en TEXT NOT NULL,
        creado_por TEXT
    );
    CREATE TABLE IF NOT EXISTS analitica_eventos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sitio_id INTEGER NOT NULL,
        en TEXT NOT NULL,
        dia TEXT NOT NULL,
        tipo TEXT NOT NULL,
        pagina TEXT NOT NULL,
        entrada INTEGER NOT NULL DEFAULT 0,
        canal TEXT NOT NULL,
        origen TEXT,
        campana TEXT,
        dispositivo TEXT NOT NULL,
        visitante TEXT NOT NULL,
        detalle TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_analitica_eventos_sitio_dia ON analitica_eventos (sitio_id, dia);
    CREATE TABLE IF NOT EXISTS analitica_sal (
        dia TEXT PRIMARY KEY,
        sal TEXT NOT NULL
    );
    """)


def _conn(db: str) -> sqlite3.Connection:
    c = sqlite3.connect(db, timeout=10)
    c.row_factory = sqlite3.Row
    return c


# ── sitios ───────────────────────────────────────────────────────────────────

def normalizar_dominio(crudo: str) -> str:
    """'https://www.Tienda.com.uy/contacto' -> 'tienda.com.uy'."""
    d = (crudo or "").strip().lower()
    if "://" not in d:
        d = "//" + d
    try:
        host = urlsplit(d).hostname or ""
    except ValueError:
        host = ""
    host = host.rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if not _DOMINIO.match(host):
        raise NoSePuede(f"«{(crudo or '').strip()[:60]}» no es un dominio válido")
    return host


def _dominios(crudo) -> list[str]:
    if isinstance(crudo, str):
        crudo = re.split(r"[\s,;]+", crudo)
    lista = []
    for d in crudo or []:
        if str(d).strip():
            n = normalizar_dominio(str(d))
            if n not in lista:
                lista.append(n)
    if not lista:
        raise NoSePuede("Falta el dominio del sitio")
    if len(lista) > MAX_DOMINIOS:
        raise NoSePuede(f"Hasta {MAX_DOMINIOS} dominios por sitio")
    return lista


def _entero(v, nombre: str, maximo: int):
    if v in (None, ""):
        return None
    try:
        n = int(str(v).replace(".", "").strip())
    except ValueError:
        raise NoSePuede(f"{nombre} tiene que ser un número")
    if n < 0 or n > maximo:
        raise NoSePuede(f"{nombre} fuera de rango")
    return n


def _fila(r: sqlite3.Row) -> dict:
    s = dict(r)
    s["dominios"] = json.loads(s["dominios"])
    return s


def crear(db: str, datos: dict, quien: str) -> dict:
    nombre = (datos.get("nombre") or "").strip()[:120]
    if not nombre:
        raise NoSePuede("Falta el nombre")
    dominios = _dominios(datos.get("dominios") or datos.get("dominio"))
    ticket = _entero(datos.get("ticket_medio"), "El ticket medio", 100_000_000)
    cierre = _entero(datos.get("cierre_pct"), "El porcentaje de cierre", 100)
    bid = _entero(datos.get("business_id"), "El cliente", 10**12)
    with _conn(db) as c:
        if c.execute("SELECT COUNT(*) FROM analitica_sitios").fetchone()[0] >= MAX_SITIOS:
            raise NoSePuede("Llegaste al tope de sitios")
        cur = c.execute(
            "INSERT INTO analitica_sitios (nombre, dominios, business_id, clave, codigo, ticket_medio, cierre_pct, "
            "creado_en, creado_por) VALUES (?,?,?,?,?,?,?,?,?)",
            (nombre, json.dumps(dominios), bid, secrets.token_urlsafe(12), secrets.token_urlsafe(18),
             ticket, cierre, _txt(_ahora()), quien))
        sid = cur.lastrowid
    return obtener(db, sid)


def obtener(db: str, sid: int) -> dict:
    with _conn(db) as c:
        r = c.execute("SELECT * FROM analitica_sitios WHERE id = ?", (sid,)).fetchone()
    if not r:
        raise NoSePuede("Ese sitio no existe")
    return _fila(r)


def por_clave(db: str, clave: str):
    if not clave or len(clave) > 40:
        return None
    with _conn(db) as c:
        r = c.execute("SELECT * FROM analitica_sitios WHERE clave = ?", (clave,)).fetchone()
    return _fila(r) if r else None


def por_codigo(db: str, codigo: str):
    if not codigo or len(codigo) > 40:
        return None
    with _conn(db) as c:
        r = c.execute("SELECT * FROM analitica_sitios WHERE codigo = ?", (codigo,)).fetchone()
    return _fila(r) if r else None


def editar(db: str, sid: int, datos: dict) -> dict:
    obtener(db, sid)
    cambios = {}
    if "nombre" in datos:
        nombre = (datos.get("nombre") or "").strip()[:120]
        if not nombre:
            raise NoSePuede("Falta el nombre")
        cambios["nombre"] = nombre
    if "dominios" in datos:
        cambios["dominios"] = json.dumps(_dominios(datos["dominios"]))
    if "ticket_medio" in datos:
        cambios["ticket_medio"] = _entero(datos["ticket_medio"], "El ticket medio", 100_000_000)
    if "cierre_pct" in datos:
        cambios["cierre_pct"] = _entero(datos["cierre_pct"], "El porcentaje de cierre", 100)
    if "business_id" in datos:
        cambios["business_id"] = _entero(datos["business_id"], "El cliente", 10**12)
    if "activo" in datos:
        cambios["activo"] = 1 if datos["activo"] else 0
    if cambios:
        sets = ", ".join(f"{k} = ?" for k in cambios)
        with _conn(db) as c:
            c.execute(f"UPDATE analitica_sitios SET {sets} WHERE id = ?", (*cambios.values(), sid))
    return obtener(db, sid)


def nuevo_codigo(db: str, sid: int) -> dict:
    """Corta el link del reporte que se mandó y da uno nuevo."""
    obtener(db, sid)
    with _conn(db) as c:
        c.execute("UPDATE analitica_sitios SET codigo = ? WHERE id = ?", (secrets.token_urlsafe(18), sid))
    return obtener(db, sid)


def borrar(db: str, sid: int) -> None:
    obtener(db, sid)
    with _conn(db) as c:
        c.execute("DELETE FROM analitica_eventos WHERE sitio_id = ?", (sid,))
        c.execute("DELETE FROM analitica_sitios WHERE id = ?", (sid,))


def sugeridos(db: str) -> list[dict]:
    """Clientes del CRM con web cargada y sin sitio en la analítica todavía."""
    from database import ETAPAS_CLIENTE
    marcas = ",".join("?" * len(ETAPAS_CLIENTE))
    with _conn(db) as c:
        filas = c.execute(
            f"SELECT b.id, b.name, b.website FROM businesses b WHERE b.crm_status IN ({marcas}) "
            "AND COALESCE(TRIM(b.website), '') != '' AND b.id NOT IN "
            "(SELECT business_id FROM analitica_sitios WHERE business_id IS NOT NULL) ORDER BY b.name LIMIT 100",
            ETAPAS_CLIENTE).fetchall()
    salida = []
    for f in filas:
        try:
            dom = normalizar_dominio(f["website"])
        except NoSePuede:
            continue
        salida.append({"business_id": f["id"], "nombre": f["name"], "dominio": dom})
    return salida[:12]


# ── recepción de eventos ─────────────────────────────────────────────────────

_IPS: dict[str, list[float]] = defaultdict(list)
_IPS_LOCK = threading.Lock()
_CUENTA_DIA: dict[tuple, int] = {}


def _paso_tope_ip(ip: str) -> bool:
    ahora = time.time()
    with _IPS_LOCK:
        if len(_IPS) > 20_000:   # que un barrido de IPs no llene la memoria
            _IPS.clear()
        recientes = [t for t in _IPS[ip] if ahora - t < 60]
        if len(recientes) >= TOPE_IP_MINUTO:
            _IPS[ip] = recientes
            return False
        recientes.append(ahora)
        _IPS[ip] = recientes
        return True


def _host(url: str) -> str:
    try:
        h = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def dominio_permitido(host: str, dominios: list[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in dominios)


def _pagina(url: str) -> str:
    """Solo la ruta: la query puede traer mails o tokens de un formulario."""
    try:
        ruta = urlsplit(url).path or "/"
    except ValueError:
        ruta = "/"
    ruta = re.sub(r"/{2,}", "/", ruta)
    if len(ruta) > 1:
        ruta = ruta.rstrip("/") or "/"
    return ruta[:LARGO_PAGINA]


def _sal(c: sqlite3.Connection, dia: str) -> str:
    r = c.execute("SELECT sal FROM analitica_sal WHERE dia = ?", (dia,)).fetchone()
    if r:
        return r[0]
    sal = secrets.token_hex(16)
    c.execute("INSERT OR IGNORE INTO analitica_sal (dia, sal) VALUES (?, ?)", (dia, sal))
    # La de ayer ya no hace falta: sin ella, el hash de ayer no se puede rehacer.
    c.execute("DELETE FROM analitica_sal WHERE dia < ?", (dia,))
    return c.execute("SELECT sal FROM analitica_sal WHERE dia = ?", (dia,)).fetchone()[0]


_BUSCADORES = ("bing.", "duckduckgo.", "yahoo.", "ecosia.", "yandex.", "baidu.", "search.brave.")
_PAGO = ("cpc", "ppc", "paid", "ads", "paid_social", "paidsocial", "display", "cpm")


def clasificar(ref: str, utm_source: str = "", utm_medium: str = "", gclid: bool = False,
               fbclid: bool = False, dominios: tuple = ()) -> tuple[str, str]:
    """(canal, origen) de una visita. El canal es lo que lee el dueño."""
    us = (utm_source or "").strip().lower()[:60]
    um = (utm_medium or "").strip().lower()[:60]
    host = _host(ref) if ref else ""
    if host and dominios and dominio_permitido(host, list(dominios)):
        host = ""   # vino de otra página del mismo sitio
    origen = us or host or ""
    pago = um in _PAGO
    if gclid or (pago and ("google" in us or not us and "google" in host)):
        return "Google Ads", origen or "google"
    if pago and any(x in (us or host) for x in ("facebook", "instagram", "meta", "fb", "ig")):
        return "Meta Ads", origen
    if pago:
        return "Otros anuncios", origen
    fuente = us or host
    if not fuente:
        return ("Facebook", "facebook") if fbclid else ("Directo", "")
    if "instagram" in fuente or fuente in ("ig", "l.instagram.com"):
        return "Instagram", origen
    if "facebook" in fuente or fuente in ("fb", "l.facebook.com", "m.facebook.com") or fbclid:
        return "Facebook", origen
    if "whatsapp" in fuente or fuente == "wa":
        return "WhatsApp", origen
    if fuente.startswith("google.") or ".google." in fuente or fuente == "google":
        return "Google", origen
    if any(b in fuente for b in _BUSCADORES):
        return "Otros buscadores", origen
    if um in ("email", "mail", "newsletter") or "mail" in fuente:
        return "Mail", origen
    if any(x in fuente for x in ("tiktok", "linkedin", "twitter", "t.co", "x.com", "youtube")):
        return "Otras redes", origen
    return "Otros sitios", origen


def _texto(v, largo: int) -> str:
    return str(v or "")[:largo].replace("\x00", "")


def registrar(db: str, cuerpo: bytes, ip: str, ua: str, origin: str, host_crm: str = "") -> str:
    """Guarda un evento que manda el script. Devuelve qué pasó, para los tests.

    Nunca levanta: el endpoint es público y contesta 202 a todo, así el que
    prueba claves al azar no aprende nada.
    """
    if not cuerpo or len(cuerpo) > LARGO_CUERPO:
        return "tamaño"
    if not ua or _ROBOT.search(ua):
        return "robot"
    try:
        d = json.loads(cuerpo.decode("utf-8", "replace"))
    except ValueError:
        return "json"
    if not isinstance(d, dict):
        return "json"
    tipo = str(d.get("t") or "")
    if tipo not in TIPOS:
        return "tipo"
    sitio = por_clave(db, str(d.get("k") or ""))
    if not sitio or not sitio["activo"]:
        return "clave"
    url = _texto(d.get("u"), 2000)
    host = _host(url)
    prueba = bool(host_crm) and host == _host("//" + host_crm) and _pagina(url) == f"/a/prueba/{sitio['clave']}"
    if not prueba and not dominio_permitido(host, sitio["dominios"]):
        return "dominio"
    # El navegador manda Origin en el POST del beacon y no se puede falsificar
    # desde una página: si viene, tiene que ser el mismo sitio que la URL.
    if origin and origin != "null" and _host(origin) != host:
        return "origen"
    if not _paso_tope_ip(ip):
        return "tope_ip"
    ahora = _ahora()
    dia = dia_mvd(ahora)
    clave_dia = (sitio["id"], dia)
    if _CUENTA_DIA.get(clave_dia, 0) >= MAX_EVENTOS_DIA:
        return "tope_sitio"
    if len(_CUENTA_DIA) > 5000:
        _CUENTA_DIA.clear()
    _CUENTA_DIA[clave_dia] = _CUENTA_DIA.get(clave_dia, 0) + 1

    pagina = PAGINA_PRUEBA if prueba else _pagina(url)
    canal, origen = clasificar(_texto(d.get("r"), 500), _texto(d.get("us"), 60), _texto(d.get("um"), 60),
                               bool(d.get("g")), bool(d.get("f")), tuple(sitio["dominios"]))
    try:
        ancho = int(d.get("w") or 0)
    except (TypeError, ValueError):
        ancho = 0
    dispositivo = "movil" if 0 < ancho < 768 or re.search(r"Mobi|Android|iPhone", ua) else "escritorio"
    entrada = 1 if tipo == "vista" and d.get("e") in (1, "1", True) else 0
    detalle = re.sub(r"[^\w .:/@#-]", "", _texto(d.get("d"), LARGO_DETALLE)) if tipo == "formulario" else ""
    campana = _texto(d.get("uc"), 80).strip() or None
    with _conn(db) as c:
        visitante = hashlib.sha256(f"{_sal(c, dia)}|{sitio['id']}|{ip}|{ua}".encode()).hexdigest()[:16]
        filas = [(sitio["id"], _txt(ahora), dia, tipo, pagina, entrada, canal, origen or None, campana,
                  dispositivo, visitante, detalle or None)]
        # La página de "gracias por tu compra" de WooCommerce es una compra.
        if tipo == "vista" and _COMPRA.search(pagina):
            filas.append((sitio["id"], _txt(ahora), dia, "compra", pagina, 0, canal, origen or None, campana,
                          dispositivo, visitante, None))
        c.executemany(
            "INSERT INTO analitica_eventos (sitio_id, en, dia, tipo, pagina, entrada, canal, origen, campana, "
            "dispositivo, visitante, detalle) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", filas)
        c.execute("UPDATE analitica_sitios SET ultimo_evento = ? WHERE id = ?", (_txt(ahora), sitio["id"]))
    return "ok"


def purgar(db: str) -> int:
    """Borra los eventos de más de 13 meses. Lo llama el listado, como mucho una vez por día."""
    corte = (hoy_mvd() - timedelta(days=RETENCION_DIAS)).isoformat()
    with _conn(db) as c:
        return c.execute("DELETE FROM analitica_eventos WHERE dia < ?", (corte,)).rowcount


_ULTIMA_PURGA = {"dia": None}


def _purgar_si_toca(db: str) -> None:
    hoy = hoy_mvd().isoformat()
    if _ULTIMA_PURGA["dia"] != hoy:
        _ULTIMA_PURGA["dia"] = hoy
        try:
            purgar(db)
        except sqlite3.Error:
            pass


# ── números ──────────────────────────────────────────────────────────────────

def _rango(desde: date, hasta: date) -> list[str]:
    return [(desde + timedelta(days=i)).isoformat() for i in range((hasta - desde).days + 1)]


def _contactos_sql(extra_grupo: str = "") -> str:
    """Un contacto = un visitante que hizo ese tipo de contacto ese día.

    Si alguien toca el botón de WhatsApp tres veces seguidas, es una persona
    que quiso escribir, no tres.
    """
    cols = f", {extra_grupo}" if extra_grupo else ""
    return (f"SELECT tipo{cols}, COUNT(*) n FROM (SELECT DISTINCT dia, visitante, tipo{cols} FROM analitica_eventos "
            "WHERE sitio_id = ? AND dia BETWEEN ? AND ? AND tipo != 'vista') GROUP BY tipo" + cols)


def numeros(db: str, sid: int, desde: date, hasta: date, sitio: dict = None) -> dict:
    """Todo lo de un sitio entre dos días (hora de Montevideo), los dos incluidos."""
    sitio = sitio or obtener(db, sid)
    d0, d1 = desde.isoformat(), hasta.isoformat()
    a = (sid, d0, d1)
    with _conn(db) as c:
        vistas = c.execute("SELECT COUNT(*), COALESCE(SUM(entrada),0) FROM analitica_eventos "
                           "WHERE sitio_id = ? AND dia BETWEEN ? AND ? AND tipo = 'vista'", a).fetchone()
        visitantes = c.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT dia, visitante FROM analitica_eventos "
            "WHERE sitio_id = ? AND dia BETWEEN ? AND ?)", a).fetchone()[0]
        por_tipo = {r["tipo"]: r["n"] for r in c.execute(_contactos_sql(), a)}
        clics = c.execute("SELECT COUNT(*) FROM analitica_eventos WHERE sitio_id = ? AND dia BETWEEN ? AND ? "
                          "AND tipo != 'vista'", a).fetchone()[0]
        dias_v = {r[0]: (r[1], r[2]) for r in c.execute(
            "SELECT dia, COUNT(*), SUM(entrada) FROM analitica_eventos WHERE sitio_id = ? AND dia BETWEEN ? AND ? "
            "AND tipo = 'vista' GROUP BY dia", a)}
        dias_c = {r[0]: r[1] for r in c.execute(
            "SELECT dia, COUNT(*) FROM (SELECT DISTINCT dia, visitante, tipo FROM analitica_eventos "
            "WHERE sitio_id = ? AND dia BETWEEN ? AND ? AND tipo != 'vista') GROUP BY dia", a)}
        canal_v = {r[0]: r[1] for r in c.execute(
            "SELECT canal, COUNT(*) FROM analitica_eventos WHERE sitio_id = ? AND dia BETWEEN ? AND ? "
            "AND tipo = 'vista' AND entrada = 1 GROUP BY canal", a)}
        canal_c = defaultdict(int)
        for r in c.execute(_contactos_sql("canal"), a):
            canal_c[r["canal"]] += r["n"]
        pag_v = {r[0]: r[1] for r in c.execute(
            "SELECT pagina, COUNT(*) FROM analitica_eventos WHERE sitio_id = ? AND dia BETWEEN ? AND ? "
            "AND tipo = 'vista' GROUP BY pagina", a)}
        pag_c = defaultdict(int)
        for r in c.execute(_contactos_sql("pagina"), a):
            if r["tipo"] != "compra":
                pag_c[r["pagina"]] += r["n"]
        disp = {r[0]: r[1] for r in c.execute(
            "SELECT dispositivo, COUNT(*) FROM analitica_eventos WHERE sitio_id = ? AND dia BETWEEN ? AND ? "
            "AND tipo = 'vista' AND entrada = 1 GROUP BY dispositivo", a)}
        campanas = [dict(r) for r in c.execute(
            "SELECT campana, COUNT(*) visitas FROM analitica_eventos WHERE sitio_id = ? AND dia BETWEEN ? AND ? "
            "AND tipo = 'vista' AND entrada = 1 AND campana IS NOT NULL GROUP BY campana "
            "ORDER BY visitas DESC LIMIT 8", a)]
        camp_c = defaultdict(int)
        for r in c.execute(_contactos_sql("campana"), a):
            if r["campana"]:
                camp_c[r["campana"]] += r["n"]
        hora = [0] * 24
        for r in c.execute("SELECT en FROM (SELECT dia, visitante, tipo, MIN(en) en FROM analitica_eventos "
                           "WHERE sitio_id = ? AND dia BETWEEN ? AND ? AND tipo IN ('whatsapp','llamada','mail',"
                           "'formulario') GROUP BY dia, visitante, tipo)", a):
            loc = datetime.fromisoformat(r[0]).replace(tzinfo=timezone.utc).astimezone(MVD)
            hora[loc.hour] += 1

    visitas = vistas[1]
    contactos = sum(n for t, n in por_tipo.items() if t != "compra")
    canales = sorted(
        ({"canal": k, "visitas": canal_v.get(k, 0), "contactos": canal_c.get(k, 0),
          "conversion": _conv(canal_c.get(k, 0), canal_v.get(k, 0))}
         for k in set(canal_v) | set(canal_c)),
        key=lambda x: (-x["contactos"], -x["visitas"], x["canal"]))
    paginas = sorted(
        ({"pagina": k, "vistas": pag_v.get(k, 0), "contactos": pag_c.get(k, 0)}
         for k in set(pag_v) | set(pag_c)),
        key=lambda x: (-x["contactos"], -x["vistas"], x["pagina"]))[:12]
    for cp in campanas:
        cp["contactos"] = camp_c.get(cp["campana"], 0)
    total_disp = sum(disp.values())
    fuera = sum(hora[h] for h in range(24) if h < 9 or h >= 19)
    valor = None
    if sitio.get("ticket_medio") and sitio.get("cierre_pct"):
        valor = round(contactos * sitio["cierre_pct"] / 100 * sitio["ticket_medio"])
    if por_tipo.get("compra") and sitio.get("ticket_medio"):
        valor = (valor or 0) + por_tipo["compra"] * sitio["ticket_medio"]
    return {
        "desde": d0, "hasta": d1,
        "visitas": visitas, "paginas_vistas": vistas[0], "visitantes": visitantes,
        "contactos": contactos, "compras": por_tipo.get("compra", 0), "clics": clics,
        "conversion": _conv(contactos, visitas),
        "por_tipo": {t: por_tipo.get(t, 0) for t in TIPOS_CONTACTO},
        "dias": [{"dia": d, "visitas": dias_v.get(d, (0, 0))[1] or 0, "vistas": dias_v.get(d, (0, 0))[0],
                  "contactos": dias_c.get(d, 0)} for d in _rango(desde, hasta)],
        "canales": canales, "paginas": paginas, "campanas": campanas,
        "movil_pct": round(disp.get("movil", 0) * 100 / total_disp) if total_disp else None,
        "por_hora": hora, "fuera_de_hora": fuera,
        "valor_estimado": valor,
    }


def _conv(contactos: int, visitas: int):
    return round(contactos / visitas, 4) if visitas else None


def _variacion(ahora: int, antes: int):
    if not antes:
        return None
    return round((ahora - antes) / antes, 3)


def estado(sitio: dict) -> str:
    if not sitio["activo"]:
        return "pausado"
    if not sitio["ultimo_evento"]:
        return "sin_instalar"
    ultimo = datetime.fromisoformat(sitio["ultimo_evento"])
    if _ahora() - ultimo > timedelta(days=DIAS_SIN_DATOS):
        return "sin_datos"
    return "ok"


def listar(db: str, dias: int = 30) -> list[dict]:
    """Los sitios con sus números de los últimos `dias` contra los `dias` anteriores."""
    _purgar_si_toca(db)
    hasta = hoy_mvd()
    desde = hasta - timedelta(days=dias - 1)
    antes_h = desde - timedelta(days=1)
    antes_d = antes_h - timedelta(days=dias - 1)
    with _conn(db) as c:
        filas = [_fila(r) for r in c.execute("SELECT * FROM analitica_sitios ORDER BY nombre COLLATE NOCASE")]
        try:
            clientes = dict(c.execute("SELECT id, name FROM businesses WHERE id IN (SELECT business_id FROM "
                                      "analitica_sitios WHERE business_id IS NOT NULL)").fetchall())
        except sqlite3.OperationalError:
            clientes = {}
    salida = []
    for s in filas:
        n = numeros(db, s["id"], desde, hasta, s)
        p = _corto(db, s["id"], antes_d, antes_h)
        s.update({
            "estado": estado(s), "cliente": clientes.get(s["business_id"]),
            "visitas": n["visitas"], "contactos": n["contactos"], "compras": n["compras"],
            "conversion": n["conversion"], "por_tipo": n["por_tipo"],
            "var_visitas": _variacion(n["visitas"], p["visitas"]),
            "var_contactos": _variacion(n["contactos"], p["contactos"]),
            "serie": [d["contactos"] for d in n["dias"]],
            "valor_estimado": n["valor_estimado"],
        })
        salida.append(s)
    return salida


def _corto(db: str, sid: int, desde: date, hasta: date) -> dict:
    a = (sid, desde.isoformat(), hasta.isoformat())
    with _conn(db) as c:
        visitas = c.execute("SELECT COALESCE(SUM(entrada),0) FROM analitica_eventos WHERE sitio_id = ? "
                            "AND dia BETWEEN ? AND ? AND tipo = 'vista'", a).fetchone()[0]
        contactos = c.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT dia, visitante, tipo FROM analitica_eventos WHERE sitio_id = ? "
            "AND dia BETWEEN ? AND ? AND tipo IN ('whatsapp','llamada','mail','formulario'))", a).fetchone()[0]
    return {"visitas": visitas, "contactos": contactos}


def resumen(sitios: list[dict]) -> dict:
    visitas = sum(s["visitas"] for s in sitios)
    contactos = sum(s["contactos"] for s in sitios)
    return {
        "sitios": len(sitios),
        "midiendo": sum(1 for s in sitios if s["estado"] == "ok"),
        "sin_datos": sum(1 for s in sitios if s["estado"] in ("sin_datos", "sin_instalar")),
        "visitas": visitas, "contactos": contactos,
        "conversion": _conv(contactos, visitas),
        "whatsapp": sum(s["por_tipo"]["whatsapp"] for s in sitios),
    }


def detalle(db: str, sid: int, dias: int = 30) -> dict:
    s = obtener(db, sid)
    hasta = hoy_mvd()
    desde = hasta - timedelta(days=dias - 1)
    n = numeros(db, sid, desde, hasta, s)
    antes_h = desde - timedelta(days=1)
    p = _corto(db, sid, antes_h - timedelta(days=dias - 1), antes_h)
    n["var_visitas"] = _variacion(n["visitas"], p["visitas"])
    n["var_contactos"] = _variacion(n["contactos"], p["contactos"])
    s["estado"] = estado(s)
    s["numeros"] = n
    s["meses"] = meses(s)
    return s


# ── reporte del mes ──────────────────────────────────────────────────────────

def meses(sitio: dict) -> list[str]:
    """Del mes actual para atrás, hasta el mes en que se dio de alta (máximo 13)."""
    hoy = hoy_mvd()
    alta = date.fromisoformat(dia_mvd(datetime.fromisoformat(sitio["creado_en"])))
    salida, a, m = [], hoy.year, hoy.month
    while (a, m) >= (alta.year, alta.month) and len(salida) < 13:
        salida.append(f"{a:04d}-{m:02d}")
        a, m = (a, m - 1) if m > 1 else (a - 1, 12)
    return salida


def _limites_mes(mes: str) -> tuple[date, date]:
    a, m = (int(x) for x in mes.split("-"))
    ini = date(a, m, 1)
    fin = (date(a + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)
    return ini, min(fin, hoy_mvd())


def reporte(db: str, sid: int, mes: str) -> dict:
    s = obtener(db, sid)
    ini, fin = _limites_mes(mes)
    n = numeros(db, sid, ini, fin, s)
    # Contra el mes anterior, los mismos días: el 10 de octubre se compara con
    # el 1 al 10 de setiembre, no con setiembre entero.
    a_ini = (ini - timedelta(days=1)).replace(day=1)
    a_fin = min(a_ini + timedelta(days=(fin - ini).days), ini - timedelta(days=1))
    p = _corto(db, sid, a_ini, a_fin)
    n["var_visitas"] = _variacion(n["visitas"], p["visitas"])
    n["var_contactos"] = _variacion(n["contactos"], p["contactos"])
    n["en_curso"] = fin == hoy_mvd()
    n["sitio"] = s
    n["mes"] = mes
    return n
