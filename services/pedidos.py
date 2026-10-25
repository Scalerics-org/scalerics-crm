"""Pedidos directos: carta online con pedidos para delivery y take away (25/10).

Un producto para vender a restaurantes, pizzerías, rotiserías, hamburgueserías
y heladerías. Hoy esos comercios reciben pedidos por dos lados y los dos
cuestan: por las apps (PedidosYa y Rappi se llevan entre 20 y 30% de cada
pedido y se quedan con el cliente) o por WhatsApp a mano (alguien pasa la
carta en fotos, pregunta dirección, suma mal, y el pedido se pierde entre
chats). Esto lo resuelve como Toast, Square Online, ChowNow u Owner.com:

- el comercio tiene su link `/pedir/<slug>` con la carta (en su Instagram, su
  Google, su WhatsApp Business y un QR en la caja); su cliente arma el pedido,
  elige delivery (con el costo de su zona) o retiro, cómo paga, y lo manda;
- el pedido entra a un tablero de cocina en el CRM (Nuevos, En preparación,
  Listos, En camino) donde el comercio lo acepta con una demora, lo avanza y
  le avisa al cliente por WhatsApp con un toque;
- el cliente sigue el estado en su link `/pedir/<slug>/p/<código>`;
- las métricas dicen ventas, ticket promedio, qué se vende, a qué hora, cuánto
  tarda la cocina, cuántos clientes repiten y cuánto se ahorró el comercio en
  comisiones contra una app. Ese número es el que vende el abono mensual.

El carrito no vive en la base ni en una cookie: va en la URL (`?c=12*2-0.1_7*1`),
así la página pública anda sin scripts ni sesión. Recién al confirmar se
guarda el pedido, con los precios del momento.

Las horas son de Montevideo (UTC-3 fija, sin horario de verano desde 2015).
Las rutas están en routes/pedidos.py. Nada de esto le manda nada a nadie: los
botones de WhatsApp son links wa.me que abre una persona.
"""

import json
import re
import secrets
import sqlite3
import unicodedata
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Optional
from urllib.parse import quote

from database import _connect

UY = timedelta(hours=-3)
FORMATO = "%Y-%m-%d %H:%M"

TIPOS = ("delivery", "retiro")
MODOS = ("auto", "abierto", "cerrado")
PAGOS = {
    "efectivo": "Efectivo",
    "transferencia": "Transferencia",
    "tarjeta": "Tarjeta al recibir (POS)",
    "mercadopago": "Mercado Pago",
}

# El recorrido de un pedido. Cancelar se puede desde cualquiera que no sea final.
ESTADOS = ("nuevo", "preparando", "listo", "en_camino", "entregado", "cancelado")
ESTADO_TEXTO = {
    "nuevo": "Recibido", "preparando": "En preparación", "listo": "Listo",
    "en_camino": "En camino", "entregado": "Entregado", "cancelado": "Cancelado",
}
SIGUIENTE = {
    ("nuevo", "delivery"): "preparando", ("nuevo", "retiro"): "preparando",
    ("preparando", "delivery"): "listo", ("preparando", "retiro"): "listo",
    ("listo", "delivery"): "en_camino", ("listo", "retiro"): "entregado",
    ("en_camino", "delivery"): "entregado",
}
FINALES = ("entregado", "cancelado")
# El cliente puede cancelar solo mientras nadie lo aceptó.
CLIENTE_CANCELA = ("nuevo",)

DIAS = ("lun", "mar", "mie", "jue", "vie", "sab", "dom")
DIAS_LARGOS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

LARGO_NOMBRE = 120
LARGO_DESC = 300
LARGO_NOTA = 300
LARGO_DIRECCION = 200
MAX_LINEAS = 30
MAX_CANTIDAD = 50
# Topes del link público: frenan a quien quiera llenar el tablero de pedidos falsos.
MAX_POR_IP_DIA = 10
MAX_POR_LOCAL_DIA = 600
# Comisión que se usa para calcular el ahorro si el local no cargó la suya.
COMISION_APP_PCT = 25


class NoSePuede(ValueError):
    """Lo que se pidió no se puede hacer; el mensaje se muestra tal cual."""


# ── tiempo y texto ───────────────────────────────────────────────────────────

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


def _limpio(v, largo: int) -> str:
    return re.sub(r"[ \t]+", " ", str(v or "").replace("\r\n", "\n")).strip()[:largo]


def _sin_tildes(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def _slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _sin_tildes(texto)).strip("-")[:40] or "pedidos"


def _color(v) -> str:
    v = str(v or "").strip()
    return v if re.fullmatch(r"#[0-9a-fA-F]{6}", v) else "#0088cc"


def _entero(v, campo: str, minimo: int = 0, maximo: int = 1_000_000) -> int:
    texto = str(v if v is not None else "").strip().replace("$", "").replace(".", "").replace(" ", "")
    if texto == "":
        return minimo
    try:
        n = int(float(texto.replace(",", ".")))
    except ValueError:
        raise NoSePuede(f"{campo}: tiene que ser un número")
    if not minimo <= n <= maximo:
        raise NoSePuede(f"{campo}: tiene que estar entre {minimo} y {maximo}")
    return n


def _si(v) -> bool:
    return v is True or str(v).lower() in ("1", "true", "on", "si", "sí")


def plata(n) -> str:
    """$ 1.250, como se escribe acá."""
    return "$ " + f"{int(round(n or 0)):,}".replace(",", ".")


def tel_normal(tel) -> str:
    """El celular como lo entiende WhatsApp: 59899123456. Acepta 099 123 456."""
    d = re.sub(r"\D", "", str(tel or ""))
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("598"):
        d = d[3:]
    if d.startswith("0"):
        d = d[1:]
    if re.fullmatch(r"9\d{7}", d) or re.fullmatch(r"[24]\d{7}", d):
        return "598" + d
    return ""


def tel_lindo(tel) -> str:
    t = tel_normal(tel)
    if not t:
        return str(tel or "")
    d = "0" + t[3:]
    return f"{d[:3]} {d[3:6]} {d[6:]}" if d.startswith("09") else f"{d[:4]} {d[4:]}"


# ── horarios ─────────────────────────────────────────────────────────────────

def _dia(texto: str) -> int:
    t = _sin_tildes(texto.strip())[:3]
    if t not in DIAS:
        raise NoSePuede(f"Horario: no entiendo el día «{texto.strip()}» (lun, mar, mié, jue, vie, sáb, dom)")
    return DIAS.index(t)


def _minutos(hhmm: str) -> int:
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", hhmm.strip())
    if not m or int(m.group(1)) > 24 or int(m.group(2) or 0) > 59:
        raise NoSePuede(f"Horario: «{hhmm.strip()}» no es una hora (19:30)")
    return min(24 * 60, int(m.group(1)) * 60 + int(m.group(2) or 0))


def parse_horarios(texto) -> list[dict]:
    """'mar-dom 11:30-15:00 19:00-00:30; lun 19:00-23:00' -> franjas por día.

    Una franja que termina antes de empezar cruza la medianoche (la pizzería
    que cierra a la 1): queda guardada en el día en que abre.
    """
    franjas = []
    for parte in re.split(r"[;\n]+", str(texto or "")):
        parte = parte.strip()
        if not parte:
            continue
        m = re.match(r"([^\d]+?)\s+(\d.*)$", parte)
        if not m:
            raise NoSePuede(f"Horario: «{parte}» tiene que ser días y horas, como «mar-dom 19:00-23:30»")
        dias = set()
        for trozo in re.split(r"\s*,\s*|\s+y\s+", m.group(1).strip()):
            if "-" in trozo:
                a, b = (_dia(x) for x in trozo.split("-", 1))
                i = a
                while True:
                    dias.add(i)
                    if i == b:
                        break
                    i = (i + 1) % 7
            elif trozo:
                dias.add(_dia(trozo))
        rangos = re.findall(r"(\d{1,2}(?::\d{2})?)\s*-\s*(\d{1,2}(?::\d{2})?)", m.group(2))
        if not rangos:
            raise NoSePuede(f"Horario: «{parte}» no tiene horas, como «19:00-23:30»")
        for desde, hasta in rangos:
            a, b = _minutos(desde), _minutos(hasta)
            if a == b:
                raise NoSePuede(f"Horario: {desde}-{hasta} no dura nada")
            for d in sorted(dias):
                franjas.append({"dia": d, "desde": a, "hasta": b})
    return sorted(franjas, key=lambda f: (f["dia"], f["desde"]))


def _hhmm(m: int) -> str:
    m %= 24 * 60
    return f"{m // 60:02d}:{m % 60:02d}"


def horarios_texto(franjas: list[dict]) -> str:
    """Lo inverso de parse_horarios, agrupando días seguidos con las mismas horas."""
    por_dia = {d: tuple((f["desde"], f["hasta"]) for f in franjas if f["dia"] == d) for d in range(7)}
    partes, d = [], 0
    while d < 7:
        if not por_dia[d]:
            d += 1
            continue
        fin = d
        while fin + 1 < 7 and por_dia[fin + 1] == por_dia[d]:
            fin += 1
        dias = DIAS[d] if fin == d else f"{DIAS[d]}-{DIAS[fin]}"
        horas = " ".join(f"{_hhmm(a)}-{_hhmm(b)}" for a, b in por_dia[d])
        partes.append(f"{dias} {horas}")
        d = fin + 1
    return "; ".join(partes).replace("mie", "mié").replace("sab", "sáb")


def _intervalos_semana(franjas: list[dict]) -> list[tuple[int, int]]:
    """Las franjas como minutos desde el lunes 00:00, partiendo las que cruzan la semana."""
    semana = 7 * 24 * 60
    out = []
    for f in franjas:
        a = f["dia"] * 1440 + f["desde"]
        b = f["dia"] * 1440 + f["hasta"] + (1440 if f["hasta"] <= f["desde"] else 0)
        if b > semana:
            out += [(a, semana), (0, b - semana)]
        else:
            out.append((a, b))
    return out


def estado_apertura(local: dict, ahora: Optional[datetime] = None) -> dict:
    """{abierto, texto}: si toma pedidos ahora y qué se le dice al cliente."""
    ahora = ahora or ahora_uy()
    if not local.get("activo"):
        return {"abierto": False, "texto": "Por ahora no toma pedidos por acá"}
    modo = local.get("modo") or "auto"
    if modo == "cerrado":
        return {"abierto": False, "texto": "Cerrado por ahora: no está tomando pedidos"}
    franjas = local.get("horarios") or []
    if modo == "abierto" or not franjas:
        return {"abierto": True, "texto": "Abierto: tomando pedidos"}
    semana = 7 * 1440
    pos = ahora.weekday() * 1440 + ahora.hour * 60 + ahora.minute
    ints = _intervalos_semana(franjas)
    for a, b in ints:
        if a <= pos < b:
            # Une franjas pegadas (domingo hasta 24:00 + lunes desde 00:00).
            fin = b
            for _ in range(len(ints)):
                pegada = next((y - x for x, y in ints if x == fin % semana), None)
                if not pegada:
                    break
                fin += pegada
            return {"abierto": True, "texto": f"Abierto hasta las {_hhmm(fin)}"}
    # El próximo arranque.
    prox = min(((a - pos) % semana for a, _ in ints), default=None)
    if prox is None:
        return {"abierto": False, "texto": "Cerrado"}
    cuando = ahora + timedelta(minutes=prox)
    if cuando.date() == ahora.date():
        dia = "hoy"
    elif cuando.date() == ahora.date() + timedelta(days=1):
        dia = "mañana"
    else:
        dia = "el " + DIAS_LARGOS[cuando.weekday()]
    return {"abierto": False, "texto": f"Cerrado. Abre {dia} a las {cuando.strftime('%H:%M')}"}


# ── opciones de un producto ──────────────────────────────────────────────────

def _precio_opcion(texto: str) -> tuple[str, int]:
    m = re.match(r"^(.*?)\s*\+\s*\$?\s*(\d[\d.]*)\s*$", texto)
    if m:
        return m.group(1).strip(), int(m.group(2).replace(".", ""))
    return texto.strip(), 0


def parse_opciones(texto) -> list[dict]:
    """Un grupo por renglón: 'Tamaño: chica, grande +250' (elige uno) o
    'Extras (varios): huevo +60, jamón +90' (cualquiera, opcional)."""
    grupos = []
    for renglon in str(texto or "").splitlines():
        renglon = renglon.strip()
        if not renglon:
            continue
        if ":" not in renglon:
            raise NoSePuede(f"Opciones: «{renglon}» tiene que ser «Grupo: opción, opción +precio»")
        nombre, resto = renglon.split(":", 1)
        varios = bool(re.search(r"\((varios|opcional|opcionales)\)", nombre, re.I))
        nombre = _limpio(re.sub(r"\(.*?\)", "", nombre), 60)
        items = []
        for t in resto.split(","):
            if t.strip():
                n, p = _precio_opcion(t)
                items.append({"nombre": _limpio(n, 60), "precio": min(p, 100000)})
        if not nombre or not items:
            raise NoSePuede(f"Opciones: «{renglon}» no tiene nombre u opciones")
        grupos.append({"nombre": nombre, "varios": varios, "items": items[:20]})
    return grupos[:8]


def opciones_texto(grupos: list[dict]) -> str:
    renglones = []
    for g in grupos or []:
        items = ", ".join(i["nombre"] + (f" +{i['precio']}" if i["precio"] else "") for i in g["items"])
        renglones.append(f"{g['nombre']}{' (varios)' if g['varios'] else ''}: {items}")
    return "\n".join(renglones)


# ── base ─────────────────────────────────────────────────────────────────────

def init_pedidos(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pd_locales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            rubro TEXT,
            direccion TEXT,
            whatsapp TEXT,
            color TEXT,
            mensaje TEXT,
            horarios TEXT NOT NULL DEFAULT '[]',
            modo TEXT NOT NULL DEFAULT 'auto',
            activo INTEGER NOT NULL DEFAULT 1,
            delivery INTEGER NOT NULL DEFAULT 1,
            retiro INTEGER NOT NULL DEFAULT 1,
            demora_delivery INTEGER NOT NULL DEFAULT 45,
            demora_retiro INTEGER NOT NULL DEFAULT 25,
            pedido_minimo INTEGER NOT NULL DEFAULT 0,
            pagos TEXT NOT NULL DEFAULT '["efectivo"]',
            alias_transferencia TEXT,
            link_mercadopago TEXT,
            comision_app INTEGER NOT NULL DEFAULT 25,
            creado_por TEXT,
            creado_en TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pd_zonas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            local_id INTEGER NOT NULL REFERENCES pd_locales(id) ON DELETE CASCADE,
            nombre TEXT NOT NULL,
            costo INTEGER NOT NULL DEFAULT 0,
            minimo INTEGER NOT NULL DEFAULT 0,
            orden INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS pd_categorias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            local_id INTEGER NOT NULL REFERENCES pd_locales(id) ON DELETE CASCADE,
            nombre TEXT NOT NULL,
            orden INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS pd_productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            local_id INTEGER NOT NULL REFERENCES pd_locales(id) ON DELETE CASCADE,
            categoria_id INTEGER REFERENCES pd_categorias(id) ON DELETE SET NULL,
            nombre TEXT NOT NULL,
            descripcion TEXT,
            precio INTEGER NOT NULL,
            opciones TEXT NOT NULL DEFAULT '[]',
            disponible INTEGER NOT NULL DEFAULT 1,
            destacado INTEGER NOT NULL DEFAULT 0,
            orden INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS pd_pedidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            local_id INTEGER NOT NULL REFERENCES pd_locales(id) ON DELETE CASCADE,
            numero INTEGER NOT NULL,
            codigo TEXT NOT NULL UNIQUE,
            cliente TEXT NOT NULL,
            telefono TEXT NOT NULL,
            tipo TEXT NOT NULL,
            zona_id INTEGER,
            zona TEXT,
            direccion TEXT,
            pago TEXT NOT NULL,
            paga_con INTEGER,
            nota TEXT,
            subtotal INTEGER NOT NULL,
            envio INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL,
            estado TEXT NOT NULL DEFAULT 'nuevo',
            demora_min INTEGER,
            motivo TEXT,
            ip TEXT,
            creado_en TEXT NOT NULL,
            aceptado_en TEXT,
            listo_en TEXT,
            salio_en TEXT,
            entregado_en TEXT,
            cancelado_en TEXT,
            atendido_por TEXT
        );
        CREATE TABLE IF NOT EXISTS pd_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id INTEGER NOT NULL REFERENCES pd_pedidos(id) ON DELETE CASCADE,
            producto_id INTEGER,
            nombre TEXT NOT NULL,
            opciones TEXT,
            cantidad INTEGER NOT NULL,
            precio_unit INTEGER NOT NULL,
            subtotal INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_pd_pedidos_local ON pd_pedidos(local_id, creado_en);
        CREATE INDEX IF NOT EXISTS idx_pd_pedidos_estado ON pd_pedidos(local_id, estado);
        CREATE INDEX IF NOT EXISTS idx_pd_items_pedido ON pd_items(pedido_id);
        CREATE INDEX IF NOT EXISTS idx_pd_productos_local ON pd_productos(local_id, categoria_id);
    """)


def _con(db: str) -> sqlite3.Connection:
    conn = _connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _local_fila(r) -> dict:
    d = dict(r)
    d["horarios"] = json.loads(d.get("horarios") or "[]")
    d["horarios_texto"] = horarios_texto(d["horarios"])
    d["pagos"] = [p for p in json.loads(d.get("pagos") or "[]") if p in PAGOS]
    for k in ("activo", "delivery", "retiro"):
        d[k] = bool(d[k])
    return d


def _producto_fila(r) -> dict:
    d = dict(r)
    d["opciones"] = json.loads(d.get("opciones") or "[]")
    d["opciones_texto"] = opciones_texto(d["opciones"])
    d["disponible"] = bool(d["disponible"])
    d["destacado"] = bool(d["destacado"])
    return d


# ── locales ──────────────────────────────────────────────────────────────────

CAMPOS_LOCAL = ("nombre", "slug", "rubro", "direccion", "whatsapp", "color", "mensaje", "horarios", "modo",
                "activo", "delivery", "retiro", "demora_delivery", "demora_retiro", "pedido_minimo", "pagos",
                "alias_transferencia", "link_mercadopago", "comision_app")


def _validar_local(datos: dict, actual: Optional[dict] = None) -> dict:
    v = {}
    a = actual or {}
    if "nombre" in datos or not actual:
        v["nombre"] = _limpio(datos.get("nombre"), LARGO_NOMBRE)
        if not v["nombre"]:
            raise NoSePuede("Falta el nombre del local")
    for k, largo in (("rubro", 80), ("direccion", LARGO_DIRECCION), ("mensaje", 400), ("alias_transferencia", 200)):
        if k in datos:
            v[k] = _limpio(datos[k], largo) or None
    if "whatsapp" in datos:
        w = str(datos["whatsapp"] or "").strip()
        v["whatsapp"] = tel_normal(w) if w else None
        if w and not v["whatsapp"]:
            raise NoSePuede("El WhatsApp del local tiene que ser un número de Uruguay (099 123 456)")
    if "color" in datos:
        v["color"] = _color(datos["color"])
    if "horarios" in datos:
        h = datos["horarios"]
        v["horarios"] = json.dumps(h if isinstance(h, list) else parse_horarios(h))
    if "modo" in datos:
        if datos["modo"] not in MODOS:
            raise NoSePuede("Modo: auto, abierto o cerrado")
        v["modo"] = datos["modo"]
    for k in ("activo", "delivery", "retiro"):
        if k in datos:
            v[k] = 1 if _si(datos[k]) else 0
    if not (v.get("delivery", a.get("delivery", 1)) or v.get("retiro", a.get("retiro", 1))):
        raise NoSePuede("El local tiene que hacer delivery, retiro o los dos")
    if "demora_delivery" in datos:
        v["demora_delivery"] = _entero(datos["demora_delivery"], "Demora del delivery", 5, 240)
    if "demora_retiro" in datos:
        v["demora_retiro"] = _entero(datos["demora_retiro"], "Demora para retirar", 5, 240)
    if "pedido_minimo" in datos:
        v["pedido_minimo"] = _entero(datos["pedido_minimo"], "Pedido mínimo", 0, 100000)
    if "comision_app" in datos:
        v["comision_app"] = _entero(datos["comision_app"], "Comisión de la app", 0, 60)
    if "pagos" in datos:
        p = datos["pagos"]
        lista = p if isinstance(p, list) else [x.strip() for x in str(p or "").split(",")]
        lista = [x for x in PAGOS if x in lista]
        if not lista:
            raise NoSePuede("Tiene que haber al menos una forma de pago")
        v["pagos"] = json.dumps(lista)
    if "link_mercadopago" in datos:
        link = str(datos["link_mercadopago"] or "").strip()
        if link and not re.fullmatch(r"https://(link\.)?mercadopago\.com(\.uy)?/\S{1,200}|https://mpago\.(la|li)/\S{1,60}", link):
            raise NoSePuede("El link de pago tiene que ser de Mercado Pago (https://mpago.la/…)")
        v["link_mercadopago"] = link or None
    return v


def _slug_libre(conn, base: str, sin_id: Optional[int] = None) -> str:
    slug, i = base, 2
    while conn.execute("SELECT 1 FROM pd_locales WHERE slug = ? AND id IS NOT ?", (slug, sin_id)).fetchone():
        slug = f"{base[:36]}-{i}"
        i += 1
    return slug


def crear_local(db: str, datos: dict, quien: str) -> dict:
    v = _validar_local(datos)
    conn = _con(db)
    try:
        v["slug"] = _slug_libre(conn, _slug(datos.get("slug") or v["nombre"]))
        v.setdefault("color", "#0088cc")
        v.setdefault("horarios", json.dumps(parse_horarios("mar-dom 19:30-23:30")))
        v.setdefault("pagos", json.dumps(["efectivo", "transferencia"]))
        v["creado_por"] = quien
        v["creado_en"] = _txt(ahora_uy())
        cols = ", ".join(v)
        lid = conn.execute(f"INSERT INTO pd_locales ({cols}) VALUES ({', '.join('?' * len(v))})",
                           tuple(v.values())).lastrowid
        conn.commit()
    finally:
        conn.close()
    return local(db, lid)


def editar_local(db: str, lid: int, datos: dict) -> dict:
    actual = local(db, lid, completo=False)
    v = _validar_local({k: datos[k] for k in CAMPOS_LOCAL if k in datos}, actual)
    conn = _con(db)
    try:
        if "slug" in datos:
            nuevo = _slug(datos.get("slug") or "")
            if conn.execute("SELECT 1 FROM pd_locales WHERE slug = ? AND id != ?", (nuevo, lid)).fetchone():
                raise NoSePuede(f"El link /pedir/{nuevo} ya lo usa otro local")
            v["slug"] = nuevo
        if v:
            conn.execute(f"UPDATE pd_locales SET {', '.join(k + ' = ?' for k in v)} WHERE id = ?",
                         (*v.values(), lid))
            conn.commit()
    finally:
        conn.close()
    return local(db, lid)


def borrar_local(db: str, lid: int) -> None:
    conn = _con(db)
    try:
        if not conn.execute("DELETE FROM pd_locales WHERE id = ?", (lid,)).rowcount:
            raise NoSePuede("Ese local no existe")
        conn.commit()
    finally:
        conn.close()


def local(db: str, lid: int, completo: bool = True) -> dict:
    conn = _con(db)
    try:
        r = conn.execute("SELECT * FROM pd_locales WHERE id = ?", (lid,)).fetchone()
        if not r:
            raise NoSePuede("Ese local no existe")
        d = _local_fila(r)
        if completo:
            _completar(conn, d)
    finally:
        conn.close()
    return d


def _completar(conn, d: dict) -> None:
    d["zonas"] = [dict(z) for z in conn.execute(
        "SELECT * FROM pd_zonas WHERE local_id = ? ORDER BY orden, costo, id", (d["id"],))]
    d["categorias"] = [dict(c) for c in conn.execute(
        "SELECT * FROM pd_categorias WHERE local_id = ? ORDER BY orden, id", (d["id"],))]
    d["productos"] = [_producto_fila(p) for p in conn.execute(
        "SELECT * FROM pd_productos WHERE local_id = ? ORDER BY orden, id", (d["id"],))]


def por_slug(db: str, slug: str) -> Optional[dict]:
    conn = _con(db)
    try:
        r = conn.execute("SELECT * FROM pd_locales WHERE slug = ?", (str(slug or "")[:60],)).fetchone()
        if not r:
            return None
        d = _local_fila(r)
        _completar(conn, d)
    finally:
        conn.close()
    return d


def locales(db: str) -> list[dict]:
    hoy = hoy_uy().isoformat()
    conn = _con(db)
    try:
        filas = conn.execute(
            "SELECT l.*, "
            " (SELECT COUNT(*) FROM pd_pedidos p WHERE p.local_id = l.id AND p.estado = 'nuevo') AS nuevos, "
            " (SELECT COUNT(*) FROM pd_pedidos p WHERE p.local_id = l.id AND p.estado NOT IN ('entregado','cancelado')) AS activos, "
            " (SELECT COUNT(*) FROM pd_pedidos p WHERE p.local_id = l.id AND p.estado != 'cancelado' AND p.creado_en >= ?) AS hoy "
            "FROM pd_locales l ORDER BY l.nombre", (hoy,)).fetchall()
    finally:
        conn.close()
    out = []
    for r in filas:
        d = _local_fila(r)
        d["abierto"] = estado_apertura(d)
        out.append(d)
    return out


# ── zonas, categorías y productos ────────────────────────────────────────────

def _del_local(conn, tabla: str, lid: int, oid: int) -> None:
    if not conn.execute(f"SELECT 1 FROM {tabla} WHERE id = ? AND local_id = ?", (oid, lid)).fetchone():
        raise NoSePuede("Eso no existe en este local")


def guardar_zona(db: str, lid: int, datos: dict, zid: Optional[int] = None) -> dict:
    local(db, lid, completo=False)
    nombre = _limpio(datos.get("nombre"), 80)
    if not nombre:
        raise NoSePuede("Falta el nombre de la zona (Pocitos, Buceo…)")
    v = (nombre, _entero(datos.get("costo"), "Costo de envío", 0, 10000),
         _entero(datos.get("minimo"), "Mínimo de la zona", 0, 100000), _entero(datos.get("orden"), "Orden", 0, 999))
    conn = _con(db)
    try:
        if zid:
            _del_local(conn, "pd_zonas", lid, zid)
            conn.execute("UPDATE pd_zonas SET nombre = ?, costo = ?, minimo = ?, orden = ? WHERE id = ?", (*v, zid))
        else:
            zid = conn.execute("INSERT INTO pd_zonas (local_id, nombre, costo, minimo, orden) VALUES (?,?,?,?,?)",
                               (lid, *v)).lastrowid
        conn.commit()
        return dict(conn.execute("SELECT * FROM pd_zonas WHERE id = ?", (zid,)).fetchone())
    finally:
        conn.close()


def borrar_zona(db: str, lid: int, zid: int) -> None:
    conn = _con(db)
    try:
        _del_local(conn, "pd_zonas", lid, zid)
        conn.execute("DELETE FROM pd_zonas WHERE id = ?", (zid,))
        conn.commit()
    finally:
        conn.close()


def guardar_categoria(db: str, lid: int, datos: dict, cid: Optional[int] = None) -> dict:
    local(db, lid, completo=False)
    nombre = _limpio(datos.get("nombre"), 60)
    if not nombre:
        raise NoSePuede("Falta el nombre de la categoría (Pizzas, Bebidas…)")
    conn = _con(db)
    try:
        if cid:
            _del_local(conn, "pd_categorias", lid, cid)
            orden = _entero(datos.get("orden"), "Orden", 0, 999) if "orden" in datos else None
            conn.execute("UPDATE pd_categorias SET nombre = ?, orden = COALESCE(?, orden) WHERE id = ?",
                         (nombre, orden, cid))
        else:
            orden = conn.execute("SELECT COALESCE(MAX(orden), 0) + 1 FROM pd_categorias WHERE local_id = ?",
                                 (lid,)).fetchone()[0]
            cid = conn.execute("INSERT INTO pd_categorias (local_id, nombre, orden) VALUES (?,?,?)",
                               (lid, nombre, orden)).lastrowid
        conn.commit()
        return dict(conn.execute("SELECT * FROM pd_categorias WHERE id = ?", (cid,)).fetchone())
    finally:
        conn.close()


def borrar_categoria(db: str, lid: int, cid: int) -> None:
    """Los productos de la categoría quedan en "Otros", no se borran."""
    conn = _con(db)
    try:
        _del_local(conn, "pd_categorias", lid, cid)
        conn.execute("DELETE FROM pd_categorias WHERE id = ?", (cid,))
        conn.commit()
    finally:
        conn.close()


def guardar_producto(db: str, lid: int, datos: dict, pid: Optional[int] = None) -> dict:
    """Crea o edita. Al editar, solo cambia los campos que vienen (sirve para
    marcar "agotado" con un toque sin mandar todo el producto)."""
    local(db, lid, completo=False)
    v = {}
    if "nombre" in datos or not pid:
        v["nombre"] = _limpio(datos.get("nombre"), 80)
        if not v["nombre"]:
            raise NoSePuede("Falta el nombre del producto")
    if "precio" in datos or not pid:
        v["precio"] = _entero(datos.get("precio"), "Precio", 1, 1000000)
    if "descripcion" in datos:
        v["descripcion"] = _limpio(datos["descripcion"], LARGO_DESC) or None
    if "opciones" in datos:
        o = datos["opciones"]
        v["opciones"] = json.dumps(o if isinstance(o, list) else parse_opciones(o))
    for k in ("disponible", "destacado"):
        if k in datos:
            v[k] = 1 if _si(datos[k]) else 0
    if "orden" in datos:
        v["orden"] = _entero(datos["orden"], "Orden", 0, 9999)
    conn = _con(db)
    try:
        if "categoria_id" in datos:
            cat = datos["categoria_id"]
            if cat in (None, "", 0, "0"):
                v["categoria_id"] = None
            else:
                v["categoria_id"] = _entero(cat, "Categoría", 1, 10**9)
                _del_local(conn, "pd_categorias", lid, v["categoria_id"])
        if pid:
            _del_local(conn, "pd_productos", lid, pid)
            if v:
                conn.execute(f"UPDATE pd_productos SET {', '.join(k + ' = ?' for k in v)} WHERE id = ?",
                             (*v.values(), pid))
        else:
            v["local_id"] = lid
            v.setdefault("orden", conn.execute("SELECT COALESCE(MAX(orden), 0) + 1 FROM pd_productos "
                                               "WHERE local_id = ?", (lid,)).fetchone()[0])
            pid = conn.execute(f"INSERT INTO pd_productos ({', '.join(v)}) VALUES ({', '.join('?' * len(v))})",
                               tuple(v.values())).lastrowid
        conn.commit()
        return _producto_fila(conn.execute("SELECT * FROM pd_productos WHERE id = ?", (pid,)).fetchone())
    finally:
        conn.close()


def borrar_producto(db: str, lid: int, pid: int) -> None:
    conn = _con(db)
    try:
        _del_local(conn, "pd_productos", lid, pid)
        conn.execute("DELETE FROM pd_productos WHERE id = ?", (pid,))
        conn.commit()
    finally:
        conn.close()


def carta(loc: dict, solo_disponibles: bool = False) -> list[dict]:
    """[{categoria, productos}] en el orden de la carta, con "Otros" al final."""
    prods = [p for p in loc["productos"] if p["disponible"] or not solo_disponibles]
    secciones = []
    for c in loc["categorias"]:
        ps = [p for p in prods if p["categoria_id"] == c["id"]]
        if ps:
            secciones.append({"id": c["id"], "nombre": c["nombre"], "productos": ps})
    ids = {c["id"] for c in loc["categorias"]}
    sueltos = [p for p in prods if p["categoria_id"] not in ids]
    if sueltos:
        secciones.append({"id": None, "nombre": "Otros", "productos": sueltos})
    return secciones


# ── carrito (vive en la URL) ─────────────────────────────────────────────────

def _token(pid: int, cant: int, sel: list[tuple[int, int]]) -> str:
    return f"{pid}*{cant}" + "".join(f"-{g}.{i}" for g, i in sel)


def _elegidas(prod: dict, sel) -> list[tuple[int, int]]:
    """Valida lo elegido: un grupo "elige uno" siempre queda con una (la primera
    si no vino ninguna); uno "varios" con las que vengan, sin repetir."""
    pedidas = {}
    for g, i in sel:
        if 0 <= g < len(prod["opciones"]) and 0 <= i < len(prod["opciones"][g]["items"]):
            pedidas.setdefault(g, [])
            if i not in pedidas[g]:
                pedidas[g].append(i)
    out = []
    for g, grupo in enumerate(prod["opciones"]):
        if grupo["varios"]:
            out += [(g, i) for i in sorted(pedidas.get(g, []))]
        else:
            out.append((g, pedidas.get(g, [0])[0]))
    return out


def _linea(prod: dict, cant: int, sel: list[tuple[int, int]]) -> dict:
    extra = sum(prod["opciones"][g]["items"][i]["precio"] for g, i in sel)
    textos = []
    for g, grupo in enumerate(prod["opciones"]):
        nombres = [grupo["items"][i]["nombre"] for gg, i in sel if gg == g]
        if nombres:
            textos.append(", ".join(nombres) if not grupo["varios"] else grupo["nombre"] + ": " + ", ".join(nombres))
    unit = prod["precio"] + extra
    return {"producto_id": prod["id"], "nombre": prod["nombre"], "cantidad": cant, "sel": sel,
            "opciones": " · ".join(textos), "precio_unit": unit, "subtotal": unit * cant,
            "token": _token(prod["id"], cant, sel)}


def leer_carrito(loc: dict, texto) -> list[dict]:
    """El carrito de la URL, contra la carta de hoy: lo que ya no existe o se
    agotó se cae solo, y los precios son siempre los de la carta."""
    prods = {p["id"]: p for p in loc["productos"] if p["disponible"]}
    lineas: list[dict] = []
    for tok in str(texto or "").split("_")[:MAX_LINEAS]:
        m = re.fullmatch(r"(\d{1,9})\*(\d{1,3})((?:-\d{1,2}\.\d{1,2})*)", tok.strip())
        if not m or int(m.group(1)) not in prods:
            continue
        prod = prods[int(m.group(1))]
        cant = max(1, min(MAX_CANTIDAD, int(m.group(2))))
        sel = _elegidas(prod, [tuple(int(x) for x in s.split(".")) for s in m.group(3).split("-") if s])
        tok = _token(prod["id"], 0, sel)
        igual = next((ln for ln in lineas if _token(ln["producto_id"], 0, ln["sel"]) == tok), None)
        if igual:
            nueva = _linea(prod, min(MAX_CANTIDAD, igual["cantidad"] + cant), sel)
            lineas[lineas.index(igual)] = nueva
        else:
            lineas.append(_linea(prod, cant, sel))
    return lineas


def carrito_texto(lineas: list[dict]) -> str:
    return "_".join(ln["token"] for ln in lineas)


def agregar(loc: dict, texto, pid, cant, sel) -> str:
    try:
        pid, cant = int(pid), int(cant or 1)
    except (TypeError, ValueError):
        raise NoSePuede("Ese producto no existe")
    prod = next((p for p in loc["productos"] if p["id"] == pid), None)
    if not prod:
        raise NoSePuede("Ese producto no existe")
    if not prod["disponible"]:
        raise NoSePuede(f"{prod['nombre']} se agotó por hoy")
    lineas = leer_carrito(loc, texto)
    if len(lineas) >= MAX_LINEAS:
        raise NoSePuede("El pedido ya es muy largo: si es para un evento, escribile al local")
    nuevo = _token(pid, max(1, min(MAX_CANTIDAD, cant)), _elegidas(prod, sel))
    return carrito_texto(leer_carrito(loc, "_".join([carrito_texto(lineas), nuevo]).strip("_")))


def cambiar_cantidad(loc: dict, texto, indice, delta) -> str:
    lineas = leer_carrito(loc, texto)
    try:
        i, delta = int(indice), int(delta)
    except (TypeError, ValueError):
        return carrito_texto(lineas)
    if 0 <= i < len(lineas):
        cant = lineas[i]["cantidad"] + delta
        prod = next(p for p in loc["productos"] if p["id"] == lineas[i]["producto_id"])
        if cant <= 0:
            lineas.pop(i)
        else:
            lineas[i] = _linea(prod, min(MAX_CANTIDAD, cant), lineas[i]["sel"])
    return carrito_texto(lineas)


def cotizar(loc: dict, lineas: list[dict], tipo: str = "retiro", zona_id=None) -> dict:
    """Subtotal, envío, total y qué falta para poder mandar el pedido."""
    subtotal = sum(ln["subtotal"] for ln in lineas)
    zona = None
    if tipo == "delivery" and zona_id not in (None, ""):
        try:
            zona = next((z for z in loc["zonas"] if z["id"] == int(zona_id)), None)
        except (TypeError, ValueError):
            zona = None
    envio = zona["costo"] if zona else 0
    minimo = max(loc["pedido_minimo"], zona["minimo"] if zona else 0) if tipo == "delivery" else 0
    falta = max(0, minimo - subtotal)
    return {"subtotal": subtotal, "envio": envio, "total": subtotal + envio, "minimo": minimo,
            "falta_minimo": falta, "zona": zona}


# ── pedidos ──────────────────────────────────────────────────────────────────

def _fila_pedido(conn, r) -> dict:
    d = dict(r)
    d["items"] = [dict(i) for i in conn.execute(
        "SELECT nombre, opciones, cantidad, precio_unit, subtotal, producto_id FROM pd_items "
        "WHERE pedido_id = ? ORDER BY id", (d["id"],))]
    d["estado_texto"] = ESTADO_TEXTO.get(d["estado"], d["estado"])
    if d["estado"] == "listo" and d["tipo"] == "retiro":
        d["estado_texto"] = "Listo para retirar"
    d["pago_texto"] = PAGOS.get(d["pago"], d["pago"])
    d["siguiente"] = SIGUIENTE.get((d["estado"], d["tipo"]))
    d["telefono_lindo"] = tel_lindo(d["telefono"])
    d["listo_estimado"] = None
    if d["aceptado_en"] and d["demora_min"]:
        d["listo_estimado"] = _txt(_dt(d["aceptado_en"]) + timedelta(minutes=d["demora_min"]))[11:]
    return d


def pedir(db: str, lid: int, carrito, datos: dict, ip: str = "") -> dict:
    """El cliente manda el pedido desde la página pública."""
    loc = local(db, lid)
    ahora = ahora_uy()
    ap = estado_apertura(loc, ahora)
    if not ap["abierto"]:
        raise NoSePuede(ap["texto"] + ".")
    lineas = leer_carrito(loc, carrito)
    if not lineas:
        raise NoSePuede("El pedido está vacío: agregá algo de la carta")
    cliente = _limpio(datos.get("cliente"), 80)
    if len(cliente) < 2:
        raise NoSePuede("Poné tu nombre, para saber de quién es el pedido")
    tel = tel_normal(datos.get("telefono"))
    if not tel:
        raise NoSePuede("Poné un celular de Uruguay (099 123 456): por ahí te avisan del pedido")
    tipo = datos.get("tipo") if datos.get("tipo") in TIPOS else ""
    if not tipo or not loc[tipo]:
        disponibles = [t for t in TIPOS if loc[t]]
        if len(disponibles) == 1:
            tipo = disponibles[0]
        else:
            raise NoSePuede("Elegí si es delivery o para retirar")
    direccion = _limpio(datos.get("direccion"), LARGO_DIRECCION)
    zona_id = datos.get("zona_id")
    if tipo == "delivery":
        if loc["zonas"] and zona_id in (None, ""):
            raise NoSePuede("Elegí tu barrio para calcular el envío")
        if len(direccion) < 4:
            raise NoSePuede("Poné la dirección de entrega")
    cot = cotizar(loc, lineas, tipo, zona_id)
    if tipo == "delivery" and loc["zonas"] and not cot["zona"]:
        raise NoSePuede("Esa zona no existe: elegí otra")
    if cot["falta_minimo"]:
        raise NoSePuede(f"El pedido mínimo para delivery es {plata(cot['minimo'])}: "
                        f"faltan {plata(cot['falta_minimo'])}")
    pago = datos.get("pago") if datos.get("pago") in loc["pagos"] else ""
    if not pago:
        raise NoSePuede("Elegí cómo pagás")
    paga_con = None
    if pago == "efectivo" and str(datos.get("paga_con") or "").strip():
        paga_con = _entero(datos.get("paga_con"), "Con cuánto pagás", 0, 1000000)
        if paga_con and paga_con < cot["total"]:
            raise NoSePuede(f"Con {plata(paga_con)} no llega: el total es {plata(cot['total'])}")
    nota = _limpio(datos.get("nota"), LARGO_NOTA) or None
    hoy = ahora.date().isoformat()
    conn = _con(db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if ip and conn.execute("SELECT COUNT(*) FROM pd_pedidos WHERE ip = ? AND creado_en >= ?",
                               (ip, hoy)).fetchone()[0] >= MAX_POR_IP_DIA:
            raise NoSePuede("Ya mandaste muchos pedidos hoy desde acá: escribile al local por WhatsApp")
        n_hoy = conn.execute("SELECT COUNT(*) FROM pd_pedidos WHERE local_id = ? AND creado_en >= ?",
                             (lid, hoy)).fetchone()[0]
        if n_hoy >= MAX_POR_LOCAL_DIA:
            raise NoSePuede("El local no está tomando más pedidos por hoy")
        codigo = secrets.token_urlsafe(9)
        pid = conn.execute(
            "INSERT INTO pd_pedidos (local_id, numero, codigo, cliente, telefono, tipo, zona_id, zona, direccion, "
            "pago, paga_con, nota, subtotal, envio, total, estado, ip, creado_en) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'nuevo',?,?)",
            (lid, n_hoy + 1, codigo, cliente, tel, tipo, cot["zona"]["id"] if cot["zona"] else None,
             cot["zona"]["nombre"] if cot["zona"] else None, direccion if tipo == "delivery" else None,
             pago, paga_con, nota, cot["subtotal"], cot["envio"], cot["total"], ip[:64] or None,
             _txt(ahora))).lastrowid
        conn.executemany(
            "INSERT INTO pd_items (pedido_id, producto_id, nombre, opciones, cantidad, precio_unit, subtotal) "
            "VALUES (?,?,?,?,?,?,?)",
            [(pid, ln["producto_id"], ln["nombre"], ln["opciones"] or None, ln["cantidad"], ln["precio_unit"],
              ln["subtotal"]) for ln in lineas])
        conn.commit()
        return _fila_pedido(conn, conn.execute("SELECT * FROM pd_pedidos WHERE id = ?", (pid,)).fetchone())
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def por_codigo(db: str, codigo: str) -> Optional[dict]:
    if not re.fullmatch(r"[\w-]{6,40}", str(codigo or "")):
        return None
    conn = _con(db)
    try:
        r = conn.execute("SELECT * FROM pd_pedidos WHERE codigo = ?", (codigo,)).fetchone()
        return _fila_pedido(conn, r) if r else None
    finally:
        conn.close()


def pedido(db: str, lid: int, pid: int) -> dict:
    conn = _con(db)
    try:
        r = conn.execute("SELECT * FROM pd_pedidos WHERE id = ? AND local_id = ?", (pid, lid)).fetchone()
        if not r:
            raise NoSePuede("Ese pedido no existe")
        return _fila_pedido(conn, r)
    finally:
        conn.close()


SELLO = {"preparando": "aceptado_en", "listo": "listo_en", "en_camino": "salio_en",
         "entregado": "entregado_en", "cancelado": "cancelado_en"}


def avanzar(db: str, lid: int, pid: int, estado: str, quien: str, demora_min=None, motivo=None) -> dict:
    """El equipo del local mueve el pedido. Solo hacia adelante, o cancelado."""
    p = pedido(db, lid, pid)
    if p["estado"] in FINALES:
        raise NoSePuede(f"El pedido ya está {p['estado_texto'].lower()}")
    if estado == "cancelado":
        motivo = _limpio(motivo, 200)
        if not motivo:
            raise NoSePuede("Poné por qué se cancela (se agotó, no contesta, dirección fuera de zona…)")
    elif estado != p["siguiente"]:
        raise NoSePuede(f"Un pedido {p['estado_texto'].lower()} no puede pasar a {ESTADO_TEXTO.get(estado, estado)}")
    cambios = {"estado": estado, SELLO[estado]: _txt(ahora_uy()), "atendido_por": quien}
    if estado == "preparando":
        loc = local(db, lid, completo=False)
        defecto = loc["demora_delivery"] if p["tipo"] == "delivery" else loc["demora_retiro"]
        cambios["demora_min"] = _entero(demora_min, "Demora", 5, 240) if demora_min not in (None, "") else defecto
    if estado == "cancelado":
        cambios["motivo"] = motivo
    conn = _con(db)
    try:
        # El WHERE con el estado viejo frena a dos personas tocando el mismo pedido a la vez.
        n = conn.execute(f"UPDATE pd_pedidos SET {', '.join(k + ' = ?' for k in cambios)} "
                         "WHERE id = ? AND estado = ?", (*cambios.values(), pid, p["estado"])).rowcount
        conn.commit()
    finally:
        conn.close()
    if not n:
        raise NoSePuede("Alguien más movió este pedido recién: recargá el tablero")
    return pedido(db, lid, pid)


def cancelar_cliente(db: str, codigo: str) -> dict:
    p = por_codigo(db, codigo)
    if not p:
        raise NoSePuede("Ese pedido no existe")
    if p["estado"] not in CLIENTE_CANCELA:
        raise NoSePuede("El local ya lo está preparando: para cancelarlo, escribiles por WhatsApp")
    conn = _con(db)
    try:
        conn.execute("UPDATE pd_pedidos SET estado = 'cancelado', cancelado_en = ?, motivo = ? "
                     "WHERE id = ? AND estado = 'nuevo'", (_txt(ahora_uy()), "Lo canceló el cliente", p["id"]))
        conn.commit()
    finally:
        conn.close()
    return por_codigo(db, codigo)


def tablero(db: str, lid: int) -> dict:
    """Lo que está en curso, más lo que se cerró hoy (para ver el día)."""
    hoy = hoy_uy().isoformat()
    conn = _con(db)
    try:
        filas = conn.execute(
            "SELECT * FROM pd_pedidos WHERE local_id = ? AND (estado NOT IN ('entregado','cancelado') "
            "OR creado_en >= ?) ORDER BY creado_en, id", (lid, hoy)).fetchall()
        pedidos = [_fila_pedido(conn, r) for r in filas]
    finally:
        conn.close()
    columnas = {"nuevo": [], "preparando": [], "listo": [], "en_camino": [], "cerrados": []}
    for p in pedidos:
        columnas["cerrados" if p["estado"] in FINALES else p["estado"]].append(p)
    columnas["cerrados"].reverse()
    hechos = [p for p in pedidos if p["estado"] == "entregado"]
    return {"columnas": columnas, "hoy": {"pedidos": len([p for p in pedidos if p["creado_en"] >= hoy
                                                         and p["estado"] != "cancelado"]),
                                          "ventas": sum(p["total"] for p in pedidos if p["creado_en"] >= hoy
                                                        and p["estado"] != "cancelado"),
                                          "entregados": len(hechos)}}


def historial(db: str, lid: int, dias: int = 30, buscar: str = "") -> list[dict]:
    desde = (hoy_uy() - timedelta(days=dias - 1)).isoformat()
    sql = "SELECT * FROM pd_pedidos WHERE local_id = ? AND creado_en >= ?"
    args: list = [lid, desde]
    b = _limpio(buscar, 60)
    if b:
        sql += " AND (cliente LIKE ? OR substr(telefono, 4) LIKE ? OR CAST(numero AS TEXT) = ?)"
        # El celular se guarda como 59899123456: "099 123" se busca como "99123"
        # en lo que va después del 598.
        crudos = re.sub(r"\D", "", b)
        local = re.sub(r"^(00)?(598)?0?", "", crudos)
        args += [f"%{b}%", f"%{local}%" if len(crudos) >= 3 and local else "-", b.lstrip("#")]
    conn = _con(db)
    try:
        filas = conn.execute(sql + " ORDER BY creado_en DESC, id DESC LIMIT 300", args).fetchall()
        return [_fila_pedido(conn, r) for r in filas]
    finally:
        conn.close()


def metricas(db: str, lid: int, dias: int = 30) -> dict:
    """Lo que se le muestra al comercio para que vea que el producto rinde."""
    loc = local(db, lid, completo=False)
    hoy = hoy_uy()
    desde = hoy - timedelta(days=dias - 1)
    conn = _con(db)
    try:
        filas = [dict(r) for r in conn.execute(
            "SELECT * FROM pd_pedidos WHERE local_id = ? AND creado_en >= ? ORDER BY creado_en",
            (lid, desde.isoformat()))]
        items = [dict(r) for r in conn.execute(
            "SELECT i.nombre, i.cantidad, i.subtotal FROM pd_items i JOIN pd_pedidos p ON p.id = i.pedido_id "
            "WHERE p.local_id = ? AND p.creado_en >= ? AND p.estado != 'cancelado'", (lid, desde.isoformat()))]
        antes = {r[0] for r in conn.execute(
            "SELECT DISTINCT telefono FROM pd_pedidos WHERE local_id = ? AND creado_en < ? AND estado != 'cancelado'",
            (lid, desde.isoformat()))}
    finally:
        conn.close()
    validos = [p for p in filas if p["estado"] != "cancelado"]
    ventas = sum(p["total"] for p in validos)
    comida = sum(p["subtotal"] for p in validos)
    pct = loc["comision_app"] if loc["comision_app"] is not None else COMISION_APP_PCT
    prep = [(_dt(p["listo_en"]) - _dt(p["creado_en"])).total_seconds() / 60
            for p in validos if p["listo_en"] and _dt(p["listo_en"]) and _dt(p["creado_en"])]
    acepta = [(_dt(p["aceptado_en"]) - _dt(p["creado_en"])).total_seconds() / 60
              for p in validos if p["aceptado_en"] and _dt(p["aceptado_en"]) and _dt(p["creado_en"])]
    por_tel: dict[str, int] = {}
    for p in validos:
        por_tel[p["telefono"]] = por_tel.get(p["telefono"], 0) + 1
    repiten = sum(1 for t, n in por_tel.items() if n > 1 or t in antes)
    serie = []
    for i in range(dias):
        d = (desde + timedelta(days=i)).isoformat()
        del_dia = [p for p in validos if p["creado_en"][:10] == d]
        serie.append({"fecha": d, "pedidos": len(del_dia), "ventas": sum(p["total"] for p in del_dia)})
    horas = [0] * 24
    for p in validos:
        horas[int(p["creado_en"][11:13])] += 1
    top: dict[str, dict] = {}
    for it in items:
        t = top.setdefault(it["nombre"], {"nombre": it["nombre"], "cantidad": 0, "ventas": 0})
        t["cantidad"] += it["cantidad"]
        t["ventas"] += it["subtotal"]
    zonas: dict[str, dict] = {}
    for p in validos:
        if p["tipo"] == "delivery":
            z = zonas.setdefault(p["zona"] or "Sin zona", {"nombre": p["zona"] or "Sin zona", "pedidos": 0, "ventas": 0})
            z["pedidos"] += 1
            z["ventas"] += p["total"]
    cancelados = [p for p in filas if p["estado"] == "cancelado"]
    return {
        "dias": dias,
        "pedidos": len(validos),
        "ventas": ventas,
        "ticket": round(ventas / len(validos)) if validos else None,
        "delivery_pct": round(100 * sum(p["tipo"] == "delivery" for p in validos) / len(validos)) if validos else None,
        "cancelados": len(cancelados),
        "cancelados_pct": round(100 * len(cancelados) / len(filas)) if filas else None,
        "minutos_preparacion": round(median(prep)) if prep else None,
        "minutos_aceptar": round(median(acepta), 1) if acepta else None,
        "clientes": len(por_tel),
        "repiten": repiten,
        "repiten_pct": round(100 * repiten / len(por_tel)) if por_tel else None,
        "comision_pct": pct,
        "ahorro": round(comida * pct / 100),
        "serie": serie,
        "horas": horas,
        "top": sorted(top.values(), key=lambda t: (-t["cantidad"], t["nombre"]))[:10],
        "zonas": sorted(zonas.values(), key=lambda z: -z["pedidos"]),
        "pagos": {k: sum(1 for p in validos if p["pago"] == k) for k in PAGOS if any(p["pago"] == k for p in validos)},
    }


# ── links ────────────────────────────────────────────────────────────────────

def link_pedido(base: str, loc: dict, p: dict) -> str:
    return f"{base}/pedir/{loc['slug']}/p/{p['codigo']}"


def resumen_texto(p: dict) -> str:
    renglones = [f"{it['cantidad']} x {it['nombre']}" + (f" ({it['opciones']})" if it.get("opciones") else "")
                 for it in p["items"]]
    return "\n".join(renglones)


def whatsapp_cliente(base: str, loc: dict, p: dict) -> str:
    """El link wa.me para que el local le escriba al cliente según el estado."""
    nombre = p["cliente"].split()[0] if p["cliente"].strip() else ""
    link = link_pedido(base, loc, p)
    textos = {
        "nuevo": f"¡Hola {nombre}! Recibimos tu pedido #{p['numero']} en {loc['nombre']}. Ya lo miramos.",
        "preparando": (f"¡Hola {nombre}! Tu pedido #{p['numero']} en {loc['nombre']} ya está en preparación"
                       + (f", estimado para las {p['listo_estimado']}" if p.get("listo_estimado") else "") + "."),
        "listo": (f"¡Hola {nombre}! Tu pedido #{p['numero']} está listo para retirar en {loc['nombre']}"
                  + (f" ({loc['direccion']})" if loc.get("direccion") else "") + "."
                  if p["tipo"] == "retiro" else f"¡Hola {nombre}! Tu pedido #{p['numero']} está listo y sale enseguida."),
        "en_camino": f"¡Hola {nombre}! Tu pedido #{p['numero']} de {loc['nombre']} va en camino.",
        "entregado": f"¡Gracias por tu pedido, {nombre}! Esperamos que lo hayas disfrutado.",
        "cancelado": f"Hola {nombre}, tuvimos que cancelar tu pedido #{p['numero']}"
                     + (f": {p['motivo']}" if p.get("motivo") else "") + ". Perdón por la molestia.",
    }
    texto = textos.get(p["estado"], "")
    if p["estado"] not in ("entregado", "cancelado"):
        texto += f" Seguilo acá: {link}"
    if p["estado"] == "nuevo" and p["pago"] == "transferencia" and loc.get("alias_transferencia"):
        texto += f"\nPara la transferencia: {loc['alias_transferencia']}. Total {plata(p['total'])}."
    return f"https://wa.me/{p['telefono']}?text={quote(texto)}"


def whatsapp_local(base: str, loc: dict, p: dict) -> str:
    """El link wa.me para que el cliente le avise al local que pidió (opcional)."""
    if not loc.get("whatsapp"):
        return ""
    entrega = (f"Delivery a {p['direccion']}" + (f" ({p['zona']})" if p.get("zona") else "")
               if p["tipo"] == "delivery" else "Retiro en el local")
    texto = (f"Hola! Hice el pedido #{p['numero']} por la web a nombre de {p['cliente']}:\n{resumen_texto(p)}\n"
             f"{entrega}. Pago: {p['pago_texto']}. Total {plata(p['total'])}.\n{link_pedido(base, loc, p)}")
    return f"https://wa.me/{loc['whatsapp']}?text={quote(texto)}"
