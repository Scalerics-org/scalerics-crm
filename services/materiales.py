"""Portal de materiales del cliente (24/10).

Lo que más traba una entrega no es programar: es esperar el logo, los textos,
las fotos y el catálogo del cliente, que llegan por WhatsApp de a pedazos, en
cuatro chats distintos y sin que nadie sepa qué falta. Esto lo ordena como
Content Snare o el acceso de clientes de Basecamp:

- cada proyecto tiene un portal con una checklist (sale de una plantilla por
  tipo de proyecto y se puede tocar) y un link `/m/<código>` que el cliente
  abre sin cuenta;
- el cliente sube archivos o escribe el texto de cada ítem y lo manda; el
  equipo lo aprueba o le pide otra cosa con un comentario que el cliente ve;
- el panel dice qué portales tienen algo para revisar y cuáles están trabados
  esperando al cliente, y baja todo lo aprobado en un zip ordenado por ítem.

Los archivos van a disco, en `MATERIALES_DIR` (por defecto `materiales/` al
lado de la base, que en Fly es el volumen /data), con un nombre al azar: el
nombre que puso el cliente solo se muestra. Solo los baja el equipo, siempre
como adjunto.

Nada de esto le manda nada a nadie. Las rutas están en routes/materiales.py.
"""

import hashlib
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import unicodedata
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from database import _connect

FORMATO = "%Y-%m-%d %H:%M:%S"

MAX_ARCHIVO = int(os.environ.get("MATERIALES_MAX_MB", "15")) * 1024 * 1024
MAX_POR_ITEM = 30
MAX_POR_PORTAL = 600 * 1024 * 1024
MAX_ITEMS = 40
LARGO_NOMBRE = 120
LARGO_TITULO = 120
LARGO_DESC = 600
LARGO_TEXTO = 8000
LARGO_COMENTARIO = 1000
LARGO_MENSAJE = 1500
# Sin noticias del cliente en tantos días, el portal está trabado.
DIAS_TRABADO = 5
# Una apertura del mismo link dentro de esta ventana es la misma visita.
VENTANA_VISITA = timedelta(minutes=30)

# Lo que se puede subir. Sin ejecutables ni HTML: igual se bajan como adjunto,
# pero no hay por qué guardarlos.
PERMITIDAS = {
    "png", "jpg", "jpeg", "webp", "gif", "heic", "svg", "ai", "eps", "psd",
    "pdf", "doc", "docx", "odt", "txt", "rtf", "md",
    "xls", "xlsx", "ods", "csv",
    "ppt", "pptx", "zip", "mp4", "mov", "mp3", "ttf", "otf", "woff", "woff2",
}

TIPOS = ("web", "tienda", "puntos", "bot", "otro")
CLASES = ("archivo", "texto", "link")
ESTADOS_ITEM = ("pendiente", "enviado", "aprobado", "cambios")
ESTADOS = ("revisar", "trabado", "esperando", "completo", "cerrado")

_ACCESOS = ("Acceso al dominio y al hosting",
            "¿Quién tiene el dominio y dónde está el hosting? Contanos la empresa y a nombre de quién está. "
            "No escribas contraseñas acá: te escribimos para pasarlas de forma segura.", "texto", True)
_LOGO = ("Logo", "En la mejor calidad que tengas: SVG, AI, PDF o PNG con fondo transparente.", "archivo", True)
_COLORES = ("Colores y tipografía",
            "Si tenés un manual de marca, subilo. Si no, contanos los colores que usan (o mandá una foto del local o "
            "de un folleto).", "texto", False)
_CONTACTO = ("Datos de contacto y horarios",
             "Dirección, teléfono, WhatsApp, mail y horarios de atención tal como querés que aparezcan.", "texto", True)
_REDES = ("Redes sociales", "Los links de Instagram, Facebook, TikTok o lo que usen.", "link", False)

# Las checklists de partida. Se copian al crear el portal: cambiar esto no
# toca los portales que ya existen.
PLANTILLAS = {
    "web": ("Sitio web", [
        _LOGO, _COLORES,
        ("Quiénes somos", "Dos o tres párrafos: cómo empezaron, qué los diferencia, a quién atienden.", "texto", True),
        ("Servicios", "Cada servicio con un nombre y una o dos líneas. Si tienen precios para mostrar, también.",
         "texto", True),
        ("Fotos", "Del local, del equipo, de trabajos hechos. Mejor muchas y sin filtro: nosotros elegimos.",
         "archivo", True),
        _CONTACTO, _REDES,
        ("Testimonios de clientes", "Capturas de reseñas de Google o mensajes de clientes contentos.", "archivo",
         False),
        _ACCESOS,
    ]),
    "tienda": ("Tienda online", [
        _LOGO, _COLORES,
        ("Catálogo de productos",
         "Una planilla con nombre, precio, categoría, stock y descripción corta. Si ya la tienen en el sistema de "
         "facturación, exportala tal cual.", "archivo", True),
        ("Fotos de productos", "Idealmente con fondo blanco. Si el nombre del archivo es el código del producto, "
         "mejor.", "archivo", True),
        ("Medios de pago", "Cuáles quieren aceptar: tarjetas, Mercado Pago, transferencia, efectivo al retirar.",
         "texto", True),
        ("Envíos y retiro", "Zonas, costos, demoras y si se puede retirar en el local.", "texto", True),
        ("Cambios y devoluciones", "La política que usan hoy, aunque sea informal.", "texto", False),
        _CONTACTO, _REDES, _ACCESOS,
    ]),
    "puntos": ("App de puntos", [
        _LOGO, _COLORES,
        ("Premios y puntos", "Qué premios hay, cuántos puntos vale cada uno y cuántos puntos da cada compra.",
         "texto", True),
        ("Fotos de los premios", "Una por premio.", "archivo", False),
        ("Sucursales", "Dirección y horario de cada local donde se suman o canjean puntos.", "texto", True),
        ("Clientes actuales", "Si ya tienen una lista de clientes para invitar, subila (planilla).", "archivo",
         False),
    ]),
    "bot": ("Bot de WhatsApp", [
        _LOGO,
        ("Preguntas frecuentes", "Lo que más les preguntan por WhatsApp y cómo lo contestan hoy.", "texto", True),
        ("Catálogo o lista de precios", "Lo que el bot tiene que poder ofrecer.", "archivo", True),
        ("Cuándo pasa a una persona", "En qué casos el bot tiene que derivar, y a quién.", "texto", True),
        _CONTACTO,
    ]),
    "otro": ("Proyecto", [_LOGO, _CONTACTO]),
}


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


def _limpio(v, largo: int) -> str:
    return str(v if v is not None else "").strip()[:largo]


def carpeta(db: str) -> str:
    base = os.environ.get("MATERIALES_DIR") or os.path.join(os.path.dirname(os.path.abspath(db)), "materiales")
    os.makedirs(base, exist_ok=True)
    return base


# ── tablas ───────────────────────────────────────────────────────────────────

def init_materiales(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mat_portales (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id       INTEGER REFERENCES businesses(id) ON DELETE SET NULL,
            nombre            TEXT NOT NULL,
            tipo              TEXT NOT NULL DEFAULT 'web',
            codigo            TEXT NOT NULL UNIQUE,
            mensaje           TEXT,
            fecha_limite      TEXT,
            creado_por        TEXT NOT NULL,
            creado_en         TEXT NOT NULL,
            cerrado_en        TEXT,
            ultima_visita     TEXT,
            ultimo_cliente    TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mat_items (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            portal_id     INTEGER NOT NULL REFERENCES mat_portales(id) ON DELETE CASCADE,
            orden         INTEGER NOT NULL DEFAULT 0,
            titulo        TEXT NOT NULL,
            descripcion   TEXT,
            clase         TEXT NOT NULL DEFAULT 'archivo',
            obligatorio   INTEGER NOT NULL DEFAULT 1,
            estado        TEXT NOT NULL DEFAULT 'pendiente',
            texto         TEXT,
            comentario    TEXT,
            enviado_en    TEXT,
            revisado_en   TEXT,
            revisado_por  TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mat_items_portal ON mat_items(portal_id, orden)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mat_archivos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id     INTEGER NOT NULL REFERENCES mat_items(id) ON DELETE CASCADE,
            portal_id   INTEGER NOT NULL,
            nombre      TEXT NOT NULL,
            guardado    TEXT NOT NULL,
            bytes       INTEGER NOT NULL,
            sha256      TEXT NOT NULL,
            subido_por  TEXT NOT NULL,
            subido_en   TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mat_archivos_item ON mat_archivos(item_id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mat_eventos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            portal_id   INTEGER NOT NULL,
            item_id     INTEGER,
            quien       TEXT NOT NULL,
            accion      TEXT NOT NULL,
            detalle     TEXT,
            en          TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mat_eventos_portal ON mat_eventos(portal_id, en)")


def _evento(conn, pid: int, quien: str, accion: str, item_id: Optional[int] = None, detalle: str = "") -> None:
    conn.execute("INSERT INTO mat_eventos (portal_id, item_id, quien, accion, detalle, en) VALUES (?,?,?,?,?,?)",
                 (pid, item_id, quien, accion, detalle[:300], _txt(_ahora())))


# ── portales ─────────────────────────────────────────────────────────────────

def _fecha(v) -> Optional[str]:
    v = _limpio(v, 10)
    if not v:
        return None
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        raise NoSePuede("La fecha límite no es una fecha válida (AAAA-MM-DD).")


def crear(db: str, datos: dict, quien: str) -> dict:
    nombre = _limpio(datos.get("nombre"), LARGO_NOMBRE)
    if not nombre:
        raise NoSePuede("Falta el nombre del proyecto.")
    tipo = datos.get("tipo") or "web"
    if tipo not in TIPOS:
        raise NoSePuede("Tipo de proyecto desconocido.")
    bid = datos.get("business_id")
    bid = int(bid) if str(bid or "").isdigit() else None
    conn = _connect(db)
    try:
        if bid and not conn.execute("SELECT 1 FROM businesses WHERE id = ?", (bid,)).fetchone():
            raise NoSePuede("Ese cliente no existe.")
        pid = conn.execute("""
            INSERT INTO mat_portales (business_id, nombre, tipo, codigo, mensaje, fecha_limite, creado_por, creado_en)
            VALUES (?,?,?,?,?,?,?,?)""",
            (bid, nombre, tipo, secrets.token_urlsafe(16), _limpio(datos.get("mensaje"), LARGO_MENSAJE) or None,
             _fecha(datos.get("fecha_limite")), quien, _txt(_ahora()))).lastrowid
        for i, (titulo, desc, clase, oblig) in enumerate(PLANTILLAS[tipo][1]):
            conn.execute("""INSERT INTO mat_items (portal_id, orden, titulo, descripcion, clase, obligatorio)
                            VALUES (?,?,?,?,?,?)""", (pid, i, titulo, desc, clase, int(oblig)))
        _evento(conn, pid, quien, "creado", detalle=PLANTILLAS[tipo][0])
        conn.commit()
    finally:
        conn.close()
    return portal(db, pid)


def portal(db: str, pid: int) -> dict:
    p = _portales(db, "WHERE p.id = ?", (pid,))
    if not p:
        raise NoSePuede("Ese portal no existe.")
    return p[0]


def por_codigo(db: str, codigo: str) -> Optional[dict]:
    if not codigo or len(codigo) > 64:
        return None
    p = _portales(db, "WHERE p.codigo = ?", (codigo,))
    return p[0] if p else None


def listar(db: str) -> list:
    return _portales(db, "", ())


def _portales(db: str, donde: str, args: tuple) -> list:
    conn = _connect(db)
    try:
        filas = [dict(f) for f in conn.execute(f"""
            SELECT p.*, b.name AS cliente_nombre FROM mat_portales p
            LEFT JOIN businesses b ON b.id = p.business_id {donde} ORDER BY p.creado_en DESC, p.id DESC""", args)]
        if not filas:
            return []
        ids = [f["id"] for f in filas]
        marcas = ",".join("?" * len(ids))
        items = conn.execute(f"SELECT portal_id, estado, obligatorio FROM mat_items WHERE portal_id IN ({marcas})",
                             ids).fetchall()
        pesos = dict(conn.execute(f"SELECT portal_id, COALESCE(SUM(bytes), 0) FROM mat_archivos "
                                  f"WHERE portal_id IN ({marcas}) GROUP BY portal_id", ids).fetchall())
    finally:
        conn.close()
    por = {}
    for it in items:
        por.setdefault(it["portal_id"], []).append(it)
    ahora = _ahora()
    for f in filas:
        f.update(_progreso(f, por.get(f["id"], []), ahora))
        f["bytes"] = pesos.get(f["id"], 0)
    return filas


def _progreso(p: dict, items: list, ahora: datetime) -> dict:
    cuenta = {e: 0 for e in ESTADOS_ITEM}
    oblig = aprob_oblig = 0
    for it in items:
        cuenta[it["estado"]] = cuenta.get(it["estado"], 0) + 1
        if it["obligatorio"]:
            oblig += 1
            aprob_oblig += it["estado"] == "aprobado"
    total = len(items)
    hechos = cuenta["aprobado"]
    desde = _dt(p.get("ultimo_cliente")) or _dt(p["creado_en"]) or ahora
    dias = max(0, (ahora - desde).days)
    vencido = False
    if p.get("fecha_limite") and not p.get("cerrado_en"):
        try:
            vencido = date.fromisoformat(p["fecha_limite"]) < ahora.date()
        except ValueError:
            pass
    if p.get("cerrado_en"):
        estado = "cerrado"
    elif oblig and aprob_oblig == oblig:
        estado = "completo"
    elif cuenta["enviado"]:
        estado = "revisar"
    elif dias >= DIAS_TRABADO:
        estado = "trabado"
    else:
        estado = "esperando"
    return {"estado": estado, "conteo": cuenta, "total": total, "hechos": hechos,
            "obligatorios": oblig, "obligatorios_ok": aprob_oblig,
            "pct": round(100 * hechos / total) if total else 0,
            "dias_sin_cliente": dias, "vencido": vencido and estado != "completo"}


def resumen(portales: list) -> dict:
    abiertos = [p for p in portales if p["estado"] != "cerrado"]
    return {
        "total": len(portales),
        "abiertos": len(abiertos),
        "para_revisar": sum(p["conteo"]["enviado"] for p in abiertos),
        "portales_revisar": sum(1 for p in abiertos if p["estado"] == "revisar"),
        "trabados": sum(1 for p in abiertos if p["estado"] == "trabado"),
        "completos": sum(1 for p in abiertos if p["estado"] == "completo"),
        "vencidos": sum(1 for p in abiertos if p["vencido"]),
        "pct_medio": round(sum(p["pct"] for p in abiertos) / len(abiertos)) if abiertos else None,
    }


def editar(db: str, pid: int, datos: dict, quien: str) -> dict:
    p = portal(db, pid)
    campos, args = [], []
    if "nombre" in datos:
        nombre = _limpio(datos["nombre"], LARGO_NOMBRE)
        if not nombre:
            raise NoSePuede("Falta el nombre del proyecto.")
        campos.append("nombre = ?")
        args.append(nombre)
    if "mensaje" in datos:
        campos.append("mensaje = ?")
        args.append(_limpio(datos["mensaje"], LARGO_MENSAJE) or None)
    if "fecha_limite" in datos:
        campos.append("fecha_limite = ?")
        args.append(_fecha(datos["fecha_limite"]))
    accion = None
    if "cerrado" in datos:
        cerrar = bool(datos["cerrado"])
        if cerrar != bool(p["cerrado_en"]):
            campos.append("cerrado_en = ?")
            args.append(_txt(_ahora()) if cerrar else None)
            accion = "cerrado" if cerrar else "reabierto"
    if campos:
        conn = _connect(db)
        try:
            conn.execute(f"UPDATE mat_portales SET {', '.join(campos)} WHERE id = ?", (*args, pid))
            if accion:
                _evento(conn, pid, quien, accion)
            conn.commit()
        finally:
            conn.close()
    return portal(db, pid)


def borrar(db: str, pid: int) -> None:
    portal(db, pid)
    conn = _connect(db)
    try:
        conn.execute("DELETE FROM mat_archivos WHERE portal_id = ?", (pid,))
        conn.execute("DELETE FROM mat_items WHERE portal_id = ?", (pid,))
        conn.execute("DELETE FROM mat_eventos WHERE portal_id = ?", (pid,))
        conn.execute("DELETE FROM mat_portales WHERE id = ?", (pid,))
        conn.commit()
    finally:
        conn.close()
    shutil.rmtree(os.path.join(carpeta(db), str(int(pid))), ignore_errors=True)


def detalle(db: str, pid: int) -> dict:
    p = portal(db, pid)
    p["items"] = items(db, pid)
    conn = _connect(db)
    try:
        p["eventos"] = [dict(f) for f in conn.execute(
            "SELECT * FROM mat_eventos WHERE portal_id = ? ORDER BY en DESC, id DESC LIMIT 40", (pid,))]
    finally:
        conn.close()
    return p


def items(db: str, pid: int) -> list:
    conn = _connect(db)
    try:
        its = [dict(f) for f in conn.execute(
            "SELECT * FROM mat_items WHERE portal_id = ? ORDER BY orden, id", (pid,))]
        archivos = conn.execute("""SELECT id, item_id, nombre, bytes, subido_por, subido_en FROM mat_archivos
                                   WHERE portal_id = ? ORDER BY id""", (pid,)).fetchall()
    finally:
        conn.close()
    por = {}
    for a in archivos:
        por.setdefault(a["item_id"], []).append(dict(a))
    for it in its:
        it["archivos"] = por.get(it["id"], [])
    return its


# ── ítems ────────────────────────────────────────────────────────────────────

def _item(conn, iid: int) -> dict:
    f = conn.execute("SELECT * FROM mat_items WHERE id = ?", (iid,)).fetchone()
    if not f:
        raise NoSePuede("Ese ítem no existe.")
    return dict(f)


def agregar_item(db: str, pid: int, datos: dict, quien: str) -> dict:
    portal(db, pid)
    titulo = _limpio(datos.get("titulo"), LARGO_TITULO)
    if not titulo:
        raise NoSePuede("Falta el título del ítem.")
    clase = datos.get("clase") or "archivo"
    if clase not in CLASES:
        raise NoSePuede("Clase de ítem desconocida.")
    conn = _connect(db)
    try:
        n, orden = conn.execute("SELECT COUNT(*), COALESCE(MAX(orden), -1) FROM mat_items WHERE portal_id = ?",
                                (pid,)).fetchone()
        if n >= MAX_ITEMS:
            raise NoSePuede(f"Un portal tiene como mucho {MAX_ITEMS} ítems.")
        iid = conn.execute("""INSERT INTO mat_items (portal_id, orden, titulo, descripcion, clase, obligatorio)
                              VALUES (?,?,?,?,?,?)""",
                           (pid, orden + 1, titulo, _limpio(datos.get("descripcion"), LARGO_DESC) or None, clase,
                            0 if datos.get("obligatorio") in (False, 0, "0", "") else 1)).lastrowid
        _evento(conn, pid, quien, "item_nuevo", iid, titulo)
        conn.commit()
        return _item(conn, iid)
    finally:
        conn.close()


def borrar_item(db: str, iid: int) -> int:
    conn = _connect(db)
    try:
        it = _item(conn, iid)
        guardados = [f[0] for f in conn.execute("SELECT guardado FROM mat_archivos WHERE item_id = ?", (iid,))]
        conn.execute("DELETE FROM mat_archivos WHERE item_id = ?", (iid,))
        conn.execute("DELETE FROM mat_items WHERE id = ?", (iid,))
        conn.commit()
    finally:
        conn.close()
    for g in guardados:
        _borrar_disco(db, it["portal_id"], g)
    return it["portal_id"]


def revisar(db: str, iid: int, accion: str, comentario: str, quien: str) -> dict:
    """El equipo aprueba, pide un cambio (con comentario) o vuelve a abrir."""
    comentario = _limpio(comentario, LARGO_COMENTARIO)
    conn = _connect(db)
    try:
        it = _item(conn, iid)
        if accion == "aprobar":
            estado = "aprobado"
        elif accion == "pedir":
            if not comentario:
                raise NoSePuede("Escribí qué le falta, así el cliente sabe qué mandar.")
            estado = "cambios"
        elif accion == "reabrir":
            estado = "pendiente"
        else:
            raise NoSePuede("Acción desconocida.")
        conn.execute("""UPDATE mat_items SET estado = ?, comentario = ?, revisado_en = ?, revisado_por = ?
                        WHERE id = ?""",
                     (estado, comentario or None, _txt(_ahora()), quien, iid))
        _evento(conn, it["portal_id"], quien, {"aprobado": "aprobado", "cambios": "cambios_pedidos",
                                               "pendiente": "reabierto_item"}[estado], iid, comentario)
        conn.commit()
        return _item(conn, iid)
    finally:
        conn.close()


# ── lo que manda el cliente (o el equipo por él) ─────────────────────────────

def extension(nombre: str) -> str:
    return nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""


def nombre_seguro(nombre: str) -> str:
    """Para el zip: sin barras, sin acentos raros y sin nombres vacíos."""
    n = unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode()
    n = re.sub(r"[^A-Za-z0-9._ -]+", "_", n).strip(" ._")
    return n[:100] or "archivo"


def _borrar_disco(db: str, pid: int, guardado: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{32}", guardado or ""):
        return
    try:
        os.remove(os.path.join(carpeta(db), str(int(pid)), guardado))
    except FileNotFoundError:
        pass


def ruta_archivo(db: str, a: dict) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", a["guardado"]):
        raise NoSePuede("Archivo inválido.")
    return os.path.join(carpeta(db), str(int(a["portal_id"])), a["guardado"])


def archivo(db: str, aid: int) -> dict:
    conn = _connect(db)
    try:
        f = conn.execute("SELECT * FROM mat_archivos WHERE id = ?", (aid,)).fetchone()
    finally:
        conn.close()
    if not f:
        raise NoSePuede("Ese archivo no existe.")
    return dict(f)


def enviar(db: str, pid: int, iid: int, quien: str, texto: Optional[str] = None,
           archivos: tuple = (), cliente: bool = True) -> dict:
    """Carga lo que manda el cliente para un ítem y lo deja para revisar.

    `archivos` es una lista de (nombre, flujo) con flujo legible (FileStorage
    de Flask o BytesIO). Se valida todo antes de escribir nada: si un archivo
    no pasa, no se guarda ninguno.
    """
    conn = _connect(db)
    try:
        it = _item(conn, iid)
        p = conn.execute("SELECT * FROM mat_portales WHERE id = ?", (pid,)).fetchone()
        if not p or it["portal_id"] != pid:
            raise NoSePuede("Ese ítem no existe.")
        if p["cerrado_en"]:
            raise NoSePuede("Este portal ya está cerrado. Escribinos si te falta mandar algo.")
        if it["estado"] == "aprobado" and cliente:
            raise NoSePuede("Este ítem ya está aprobado.")
        archivos = [(n, f) for n, f in archivos if n]
        if it["clase"] != "archivo" and archivos:
            raise NoSePuede("Este ítem se contesta con texto.")
        texto = _limpio(texto, LARGO_TEXTO) if texto is not None else None
        if it["clase"] == "link" and texto:
            for linea in texto.split():
                if not re.match(r"^(https?://|www\.|@)", linea, re.I) and "." not in linea:
                    raise NoSePuede("Pegá los links completos, uno por línea.")
        ya = conn.execute("SELECT COUNT(*) FROM mat_archivos WHERE item_id = ?", (iid,)).fetchone()[0]
        peso = conn.execute("SELECT COALESCE(SUM(bytes), 0) FROM mat_archivos WHERE portal_id = ?",
                            (pid,)).fetchone()[0]
        if ya + len(archivos) > MAX_POR_ITEM:
            raise NoSePuede(f"Como mucho {MAX_POR_ITEM} archivos por ítem. Si son más, mandá un zip.")
        for nombre, _ in archivos:
            if extension(nombre) not in PERMITIDAS:
                raise NoSePuede(f"«{nombre[:80]}» no es un tipo de archivo que aceptemos. Si es un programa o una "
                                "página, comprimilo en un zip.")
        # A disco primero, a una carpeta temporal del mismo volumen: si uno se
        # pasa de tamaño no queda nada a medias.
        destino = os.path.join(carpeta(db), str(int(pid)))
        os.makedirs(destino, exist_ok=True)
        listos, tmp = [], None
        try:
            for nombre, flujo in archivos:
                guardado = secrets.token_hex(16)
                h, n = hashlib.sha256(), 0
                tmp = os.path.join(destino, guardado + ".part")
                with open(tmp, "wb") as out:
                    while True:
                        trozo = flujo.read(256 * 1024)
                        if not trozo:
                            break
                        n += len(trozo)
                        if n > MAX_ARCHIVO:
                            raise NoSePuede(f"«{nombre[:80]}» pesa más de {MAX_ARCHIVO // (1024 * 1024)} MB. "
                                            "Mandá un link de Google Drive o WeTransfer en su lugar.")
                        h.update(trozo)
                        out.write(trozo)
                if n == 0:
                    raise NoSePuede(f"«{nombre[:80]}» está vacío.")
                peso += n
                if peso > MAX_POR_PORTAL:
                    raise NoSePuede("Se llenó el espacio de este portal. Escribinos y lo ampliamos.")
                listos.append((nombre[:200], guardado, n, h.hexdigest(), tmp))
        except Exception:
            for *_, hecho in listos:
                _quitar(hecho)
            if tmp:
                _quitar(tmp)
            raise
        if not listos and not texto and it["clase"] == "archivo" and ya == 0:
            raise NoSePuede("Elegí al menos un archivo.")
        if not listos and not texto and it["clase"] != "archivo":
            raise NoSePuede("Escribí la respuesta antes de mandarla.")
        ahora = _txt(_ahora())
        for nombre, guardado, n, sha, tmp in listos:
            os.replace(tmp, os.path.join(destino, guardado))
            conn.execute("""INSERT INTO mat_archivos (item_id, portal_id, nombre, guardado, bytes, sha256, subido_por,
                            subido_en) VALUES (?,?,?,?,?,?,?,?)""", (iid, pid, nombre, guardado, n, sha, quien, ahora))
        nuevo = "enviado" if cliente or it["estado"] != "aprobado" else it["estado"]
        conn.execute("UPDATE mat_items SET estado = ?, texto = COALESCE(?, texto), enviado_en = ? WHERE id = ?",
                     (nuevo, texto if texto else None, ahora, iid))
        if cliente:
            conn.execute("UPDATE mat_portales SET ultimo_cliente = ? WHERE id = ?", (ahora, pid))
        detalle_ev = it["titulo"] + (f" · {len(listos)} archivo(s)" if listos else "")
        _evento(conn, pid, quien, "enviado", iid, detalle_ev)
        conn.commit()
        return _item(conn, iid)
    finally:
        conn.close()


def _quitar(ruta: str) -> None:
    try:
        os.remove(ruta)
    except OSError:
        pass


def quitar_archivo(db: str, aid: int, quien: str, pid: Optional[int] = None) -> None:
    """El cliente (con `pid`, para que solo toque lo de su portal) o el equipo."""
    a = archivo(db, aid)
    if pid is not None and a["portal_id"] != pid:
        raise NoSePuede("Ese archivo no existe.")
    conn = _connect(db)
    try:
        it = _item(conn, a["item_id"])
        if pid is not None:
            p = conn.execute("SELECT cerrado_en FROM mat_portales WHERE id = ?", (pid,)).fetchone()
            if p["cerrado_en"] or it["estado"] == "aprobado":
                raise NoSePuede("Este ítem ya está aprobado: si hay que cambiar algo, escribinos.")
        conn.execute("DELETE FROM mat_archivos WHERE id = ?", (aid,))
        quedan = conn.execute("SELECT COUNT(*) FROM mat_archivos WHERE item_id = ?", (it["id"],)).fetchone()[0]
        if not quedan and it["clase"] == "archivo" and it["estado"] == "enviado":
            conn.execute("UPDATE mat_items SET estado = 'pendiente' WHERE id = ?", (it["id"],))
        _evento(conn, a["portal_id"], quien, "archivo_quitado", it["id"], a["nombre"])
        conn.commit()
    finally:
        conn.close()
    _borrar_disco(db, a["portal_id"], a["guardado"])


def visita(db: str, pid: int) -> None:
    """Anota que el cliente abrió el link; la misma visita no se cuenta dos veces."""
    ahora = _ahora()
    conn = _connect(db)
    try:
        u = conn.execute("SELECT ultima_visita FROM mat_portales WHERE id = ?", (pid,)).fetchone()
        previa = _dt(u["ultima_visita"]) if u else None
        conn.execute("UPDATE mat_portales SET ultima_visita = ? WHERE id = ?", (_txt(ahora), pid))
        if not previa or ahora - previa > VENTANA_VISITA:
            _evento(conn, pid, "cliente", "abrio")
        conn.commit()
    finally:
        conn.close()


# ── entrega al equipo ────────────────────────────────────────────────────────

def zip_entrega(db: str, pid: int, solo_aprobados: bool = False):
    """Todo lo del portal en un zip, una carpeta por ítem, más los textos.

    Devuelve (archivo temporal abierto, nombre del zip). Va a un temporal en
    disco y no a memoria: un portal puede pesar cientos de MB.
    """
    p = portal(db, pid)
    its = items(db, pid)
    if solo_aprobados:
        its = [i for i in its if i["estado"] == "aprobado"]
    tmp = tempfile.TemporaryFile()
    textos = [f"# {p['nombre']}\n"]
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for n, it in enumerate(its, 1):
            dir_ = f"{n:02d} {nombre_seguro(it['titulo'])}"
            if it.get("texto"):
                textos.append(f"\n## {it['titulo']}\n\n{it['texto']}\n")
            usados = set()
            for a in it["archivos"]:
                completo = archivo(db, a["id"])
                ruta = ruta_archivo(db, completo)
                if not os.path.exists(ruta):
                    continue
                nombre = nombre_seguro(a["nombre"])
                base, k = nombre, 2
                while nombre.lower() in usados:
                    raiz, punto, ext = base.rpartition(".")
                    nombre = f"{raiz or base} ({k}){punto}{ext}" if raiz else f"{base} ({k})"
                    k += 1
                usados.add(nombre.lower())
                z.write(ruta, f"{dir_}/{nombre}")
        z.writestr("textos.md", "".join(textos))
    tmp.seek(0)
    return tmp, f"materiales-{nombre_seguro(p['nombre']).replace(' ', '-').lower()}.zip"


def clientes(db: str) -> list:
    """Para el selector del alta: los clientes del CRM, sin portal abierto primero."""
    from database import ETAPAS_CLIENTE
    conn = _connect(db)
    try:
        marcas = ",".join("?" * len(ETAPAS_CLIENTE))
        filas = conn.execute(f"""
            SELECT b.id, b.name,
                   EXISTS(SELECT 1 FROM mat_portales p WHERE p.business_id = b.id AND p.cerrado_en IS NULL) AS con_portal
            FROM businesses b WHERE b.crm_status IN ({marcas})
            ORDER BY con_portal, b.name LIMIT 300""", ETAPAS_CLIENTE).fetchall()
        return [dict(f) for f in filas]
    finally:
        conn.close()
