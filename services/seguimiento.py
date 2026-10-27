"""Seguimiento de clientes: en qué está cada proyecto y de quién es la pelota.

Pedido de Juan (27/10): "siempre tiro la pelota para la cancha de los clientes,
pero los proyectos tardan un montón más de lo que deberían". Un proyecto se
frenaba entero esperando una cosa del cliente, cuando otra parte se podía
encarar mientras tanto.

Por eso cada cliente se parte en *frentes* (catálogo, facturación, dominio...)
y cada frente dice si le toca a Scalerics o si se está esperando al cliente,
qué se espera exactamente y desde cuándo. El tablero pone arriba lo que se
puede avanzar ya y avisa cuando un cliente no tiene nada de nuestro lado:
ese es el momento en que el proyecto entero se queda quieto.

Lo carga Claude por el conector MCP cada vez que se trabaja con un cliente
(services/mcp_tools.py); el panel Clientes lo muestra y deja mover un frente
de estado (llegó lo que se esperaba, se terminó).

Los clientes son los `businesses` en ETAPAS_CLIENTE, sin archivar: los mismos
de la tabla de Clientes, así que no hay una segunda lista que mantener.
"""

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from database import ETAPAS_CLIENTE, _connect, get_business, insert_business, update_business

FORMATO = "%Y-%m-%d %H:%M:%S"
ESTADOS = ("nos_toca", "esperando_cliente", "hecho")
ETIQUETAS = {"nos_toca": "Te toca", "esperando_cliente": "Esperando al cliente",
             "hecho": "Hecho"}
LARGO_TITULO = 120
LARGO_CORTO = 600
LARGO_DETALLE = 8000
LARGO_RESUMEN = 2000
LARGO_NOTA = 2000
TOPE_BITACORA = 40


class NoSePuede(Exception):
    pass


def init_seguimiento(conn: sqlite3.Connection) -> None:
    """Tablas del seguimiento. La llama `database.init_db` con su conexión."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seguimiento_clientes (
            business_id     INTEGER PRIMARY KEY,
            resumen         TEXT,
            actualizado_en  TEXT,
            actualizado_por TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seguimiento_frentes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id     INTEGER NOT NULL,
            titulo          TEXT NOT NULL,
            estado          TEXT NOT NULL,
            espera          TEXT,
            proximo_paso    TEXT,
            detalle         TEXT,
            orden           INTEGER NOT NULL DEFAULT 0,
            estado_desde    TEXT NOT NULL,
            creado_en       TEXT NOT NULL,
            actualizado_en  TEXT NOT NULL,
            actualizado_por TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_seg_frentes_biz "
                 "ON seguimiento_frentes(business_id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seguimiento_bitacora (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            frente_id   INTEGER,
            texto       TEXT NOT NULL,
            autor       TEXT,
            creado_en   TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_seg_bitacora_biz "
                 "ON seguimiento_bitacora(business_id, id)")


def _ahora() -> str:
    return datetime.now(timezone.utc).strftime(FORMATO)


def _dias(desde: Optional[str], ahora: Optional[datetime] = None) -> Optional[int]:
    if not desde:
        return None
    try:
        d = datetime.strptime(desde[:19], FORMATO).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0, ((ahora or datetime.now(timezone.utc)) - d).days)


def _texto(valor, nombre: str, largo: int, obligatorio: bool = False) -> Optional[str]:
    if valor is None:
        if obligatorio:
            raise NoSePuede(f"Falta {nombre}")
        return None
    if not isinstance(valor, str):
        raise NoSePuede(f"{nombre} tiene que ser texto")
    valor = valor.strip()
    if obligatorio and not valor:
        raise NoSePuede(f"Falta {nombre}")
    if len(valor) > largo:
        raise NoSePuede(f"{nombre} tiene como mucho {largo} caracteres")
    return valor


def _cliente_o_error(db: str, business_id: int) -> dict:
    biz = get_business(db, business_id)
    if (not biz or biz.get("archivado_en")
            or (biz.get("crm_status") or "") not in ETAPAS_CLIENTE):
        raise NoSePuede(f"No hay un cliente activo con id {business_id}")
    return biz


def _bitacora(conn, business_id: int, frente_id, texto: str, autor: str) -> None:
    conn.execute("INSERT INTO seguimiento_bitacora (business_id, frente_id, texto, autor, creado_en) "
                 "VALUES (?,?,?,?,?)", (business_id, frente_id, texto, autor, _ahora()))


def _frente_dict(f, ahora: datetime) -> dict:
    d = dict(f)
    d["etiqueta"] = ETIQUETAS.get(d["estado"], d["estado"])
    d["dias_en_estado"] = _dias(d["estado_desde"], ahora)
    return d


def _orden_frentes(frentes: list) -> list:
    # Lo que te toca primero, después lo que espera (lo más viejo arriba) y lo
    # hecho al final: es el orden en que se lee una mañana.
    rango = {e: i for i, e in enumerate(ESTADOS)}
    return sorted(frentes, key=lambda f: (rango.get(f["estado"], 9),
                                          -(f["dias_en_estado"] or 0) if f["estado"] == "esperando_cliente" else 0,
                                          f["orden"], f["id"]))


# ── lectura ──────────────────────────────────────────────────────────────────

def tablero(db: str) -> dict:
    """Todos los clientes activos con su resumen y sus frentes abiertos, más
    las dos listas de arriba del panel (lo que te toca y lo que esperás) y los
    clientes sin nada en tu cancha."""
    ahora = datetime.now(timezone.utc)
    marcadores = ",".join("?" * len(ETAPAS_CLIENTE))
    conn = _connect(db)
    try:
        clientes = conn.execute(f"""
            SELECT b.id, b.name, b.crm_status, s.resumen, s.actualizado_en, s.actualizado_por
            FROM businesses b
            LEFT JOIN seguimiento_clientes s ON s.business_id = b.id
            WHERE b.crm_status IN ({marcadores}) AND b.archivado_en IS NULL
            ORDER BY b.name COLLATE NOCASE
        """, list(ETAPAS_CLIENTE)).fetchall()
        ids = [c["id"] for c in clientes]
        frentes = []
        if ids:
            frentes = conn.execute(
                f"SELECT * FROM seguimiento_frentes WHERE business_id IN ({','.join('?' * len(ids))})",
                ids).fetchall()
    finally:
        conn.close()

    por_cliente: dict = {}
    for f in frentes:
        por_cliente.setdefault(f["business_id"], []).append(_frente_dict(f, ahora))

    salida, te_toca, esperando, sin_nada = [], [], [], []
    for c in clientes:
        todos = _orden_frentes(por_cliente.get(c["id"], []))
        abiertos = [f for f in todos if f["estado"] != "hecho"]
        cuenta = {e: sum(1 for f in todos if f["estado"] == e) for e in ESTADOS}
        item = {"id": c["id"], "nombre": c["name"], "etapa": c["crm_status"],
                "resumen": c["resumen"], "actualizado_en": c["actualizado_en"],
                "actualizado_por": c["actualizado_por"],
                "dias_sin_actualizar": _dias(c["actualizado_en"], ahora),
                "frentes": abiertos, "hechos": cuenta["hecho"], "cuenta": cuenta,
                "sin_nada_nuestro": bool(abiertos) and cuenta["nos_toca"] == 0}
        salida.append(item)
        for f in abiertos:
            fila = {"cliente_id": c["id"], "cliente": c["name"], **f}
            (te_toca if f["estado"] == "nos_toca" else esperando).append(fila)
        if item["sin_nada_nuestro"]:
            sin_nada.append({"cliente_id": c["id"], "cliente": c["name"],
                             "esperando": cuenta["esperando_cliente"]})

    esperando.sort(key=lambda f: -(f["dias_en_estado"] or 0))
    # Los clientes con algo esperando primero: ahí es donde avanzar en
    # paralelo evita que el proyecto se frene entero.
    con_espera = {f["cliente_id"] for f in esperando}
    te_toca.sort(key=lambda f: (f["cliente_id"] not in con_espera, f["cliente"].lower(), f["orden"]))
    # Clientes ordenados por lo que más urge: sin nada nuestro, después el que
    # espera hace más tiempo, al final los que no tienen frentes cargados.
    def _urgencia(c):
        espera_max = max([f["dias_en_estado"] or 0 for f in c["frentes"]
                          if f["estado"] == "esperando_cliente"] or [-1])
        return (not c["sin_nada_nuestro"], not c["frentes"], -espera_max, c["nombre"].lower())
    salida.sort(key=_urgencia)
    return {"clientes": salida, "te_toca": te_toca, "esperando": esperando,
            "sin_nada_nuestro": sin_nada}


def ver(db: str, business_id: int) -> dict:
    """Un cliente entero: resumen, todos sus frentes (también los hechos) y
    la bitácora, lo último primero."""
    biz = _cliente_o_error(db, business_id)
    ahora = datetime.now(timezone.utc)
    conn = _connect(db)
    try:
        s = conn.execute("SELECT * FROM seguimiento_clientes WHERE business_id = ?",
                         (business_id,)).fetchone()
        frentes = conn.execute("SELECT * FROM seguimiento_frentes WHERE business_id = ?",
                               (business_id,)).fetchall()
        bit = conn.execute("SELECT b.id, b.frente_id, b.texto, b.autor, b.creado_en, f.titulo AS frente "
                           "FROM seguimiento_bitacora b LEFT JOIN seguimiento_frentes f ON f.id = b.frente_id "
                           "WHERE b.business_id = ? ORDER BY b.id DESC LIMIT ?",
                           (business_id, TOPE_BITACORA)).fetchall()
    finally:
        conn.close()
    return {"id": business_id, "nombre": biz.get("name"), "etapa": biz.get("crm_status"),
            "resumen": s["resumen"] if s else None,
            "actualizado_en": s["actualizado_en"] if s else None,
            "actualizado_por": s["actualizado_por"] if s else None,
            "frentes": _orden_frentes([_frente_dict(f, ahora) for f in frentes]),
            "bitacora": [dict(b) for b in bit]}


# ── escritura ────────────────────────────────────────────────────────────────

def guardar_frente(db: str, quien: str, business_id: int, frente_id: Optional[int] = None,
                   titulo=None, estado=None, espera=None, proximo_paso=None, detalle=None,
                   orden=None) -> dict:
    """Crea un frente (sin `frente_id`) o cambia los campos que vengan de uno
    existente. Un texto vacío borra el campo; no mandarlo lo deja como estaba.
    Cambiar el estado reinicia el "desde cuándo" y deja una línea en la bitácora."""
    _cliente_o_error(db, business_id)
    titulo = _texto(titulo, "titulo", LARGO_TITULO, obligatorio=frente_id is None)
    if estado is not None and estado not in ESTADOS:
        raise NoSePuede(f"estado tiene que ser una de: {', '.join(ESTADOS)}")
    espera = _texto(espera, "espera", LARGO_CORTO)
    proximo_paso = _texto(proximo_paso, "proximo_paso", LARGO_CORTO)
    detalle = _texto(detalle, "detalle", LARGO_DETALLE)
    if orden is not None and (not isinstance(orden, int) or isinstance(orden, bool)):
        raise NoSePuede("orden tiene que ser un número entero")
    ahora = _ahora()

    conn = _connect(db)
    try:
        if frente_id is None:
            estado = estado or "nos_toca"
            cur = conn.execute(
                "INSERT INTO seguimiento_frentes (business_id, titulo, estado, espera, proximo_paso, "
                "detalle, orden, estado_desde, creado_en, actualizado_en, actualizado_por) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (business_id, titulo, estado, espera or None, proximo_paso or None, detalle or None,
                 orden or 0, ahora, ahora, ahora, quien))
            frente_id = cur.lastrowid
            _bitacora(conn, business_id, frente_id,
                      f"Frente nuevo: {titulo} ({ETIQUETAS[estado].lower()})", quien)
        else:
            previo = conn.execute("SELECT * FROM seguimiento_frentes WHERE id = ? AND business_id = ?",
                                  (frente_id, business_id)).fetchone()
            if not previo:
                raise NoSePuede(f"El cliente {business_id} no tiene un frente con id {frente_id}")
            campos = {}
            for nombre, valor in (("titulo", titulo), ("espera", espera),
                                  ("proximo_paso", proximo_paso), ("detalle", detalle),
                                  ("orden", orden)):
                if valor is not None:
                    campos[nombre] = valor if valor != "" else None
            if campos.get("titulo", "x") is None:
                raise NoSePuede("titulo no puede quedar vacío")
            if estado is not None and estado != previo["estado"]:
                campos["estado"] = estado
                campos["estado_desde"] = ahora
                _bitacora(conn, business_id, frente_id,
                          f"{campos.get('titulo') or previo['titulo']}: "
                          f"{ETIQUETAS[previo['estado']].lower()} → {ETIQUETAS[estado].lower()}", quien)
            if campos:
                campos["actualizado_en"] = ahora
                campos["actualizado_por"] = quien
                sets = ", ".join(f"{k} = ?" for k in campos)
                conn.execute(f"UPDATE seguimiento_frentes SET {sets} WHERE id = ?",
                             [*campos.values(), frente_id])
        conn.commit()
    finally:
        conn.close()
    return ver(db, business_id)


def cambiar_estado(db: str, quien: str, frente_id: int, estado: str) -> dict:
    """Lo que hace el panel: mover un frente de estado sin tocar el resto."""
    conn = _connect(db)
    try:
        f = conn.execute("SELECT business_id FROM seguimiento_frentes WHERE id = ?",
                         (frente_id,)).fetchone()
    finally:
        conn.close()
    if not f:
        raise NoSePuede("Ese frente ya no existe")
    return guardar_frente(db, quien, f["business_id"], frente_id, estado=estado)


def borrar_frente(db: str, quien: str, business_id: int, frente_id: int) -> dict:
    """Para un frente cargado por error. Uno terminado se marca hecho, no se borra."""
    _cliente_o_error(db, business_id)
    conn = _connect(db)
    try:
        f = conn.execute("SELECT titulo FROM seguimiento_frentes WHERE id = ? AND business_id = ?",
                         (frente_id, business_id)).fetchone()
        if not f:
            raise NoSePuede(f"El cliente {business_id} no tiene un frente con id {frente_id}")
        conn.execute("DELETE FROM seguimiento_frentes WHERE id = ?", (frente_id,))
        conn.execute("UPDATE seguimiento_bitacora SET frente_id = NULL WHERE frente_id = ?", (frente_id,))
        _bitacora(conn, business_id, None, f"Frente borrado: {f['titulo']}", quien)
        conn.commit()
    finally:
        conn.close()
    return ver(db, business_id)


def anotar(db: str, quien: str, business_id: int, resumen=None, nota=None) -> dict:
    """Cambia el resumen del cliente y/o suma una línea a la bitácora (qué se
    hizo hoy, qué se habló). El resumen vacío lo borra."""
    _cliente_o_error(db, business_id)
    resumen = _texto(resumen, "resumen", LARGO_RESUMEN)
    nota = _texto(nota, "nota", LARGO_NOTA)
    if resumen is None and not nota:
        raise NoSePuede("Mandá un resumen, una nota o las dos cosas")
    conn = _connect(db)
    try:
        if resumen is not None:
            conn.execute(
                "INSERT INTO seguimiento_clientes (business_id, resumen, actualizado_en, actualizado_por) "
                "VALUES (?,?,?,?) ON CONFLICT(business_id) DO UPDATE SET resumen = excluded.resumen, "
                "actualizado_en = excluded.actualizado_en, actualizado_por = excluded.actualizado_por",
                (business_id, resumen or None, _ahora(), quien))
        else:
            conn.execute(
                "INSERT INTO seguimiento_clientes (business_id, actualizado_en, actualizado_por) "
                "VALUES (?,?,?) ON CONFLICT(business_id) DO UPDATE SET "
                "actualizado_en = excluded.actualizado_en, actualizado_por = excluded.actualizado_por",
                (business_id, _ahora(), quien))
        if nota:
            _bitacora(conn, business_id, None, nota, quien)
        conn.commit()
    finally:
        conn.close()
    return ver(db, business_id)


def alta_cliente(db: str, quien: str, nombre=None, telefono=None, lead_id=None,
                 etapa: str = "en_desarrollo") -> dict:
    """Pone en Clientes a uno que todavía no está: con `lead_id` pasa ese lead
    a cliente (con su historial, como el botón Nuevo cliente); sin él, crea el
    negocio. Si ya era cliente no hace nada y lo devuelve."""
    if etapa not in ETAPAS_CLIENTE:
        raise NoSePuede(f"etapa tiene que ser una de: {', '.join(ETAPAS_CLIENTE)}")
    if lead_id is not None:
        lead = get_business(db, lead_id)
        if not lead:
            raise NoSePuede(f"No existe el lead {lead_id}")
        if lead.get("archivado_en"):
            raise NoSePuede(f"El lead {lead_id} está archivado: hay que desarchivarlo a mano")
        if (lead.get("crm_status") or "") not in ETAPAS_CLIENTE:
            # Evento y actividad como desde la ficha, pero sin mover la meta de
            # clientes cerrados (routes/leads.registrar_cambio_de_estado): por
            # acá entran clientes que cerraron hace meses y no estaban cargados,
            # no ventas de este mes. Además eso lee la sesión web, y el
            # conector no tiene.
            from database import add_lead_event, log_activity
            update_business(db, lead_id, crm_status=etapa)
            add_lead_event(db, lead_id, etapa, note="Pasado a cliente desde Seguimiento",
                           created_by=quien)
            log_activity(db, quien, "status_change", "lead", lead_id, lead.get("name", ""), etapa)
        return {"id": lead_id, "nombre": lead.get("name"), "creado": False}

    nombre = _texto(nombre, "nombre", 200, obligatorio=True)
    telefono = _texto(telefono, "telefono", 40) or None
    bid = insert_business(db, {"name": nombre, "phone": telefono, "source": "manual"})
    if not bid:
        raise NoSePuede("Ya hay un negocio con ese teléfono en el CRM: pasá su lead_id")
    update_business(db, bid, crm_status=etapa)
    from database import log_activity
    log_activity(db, quien, "cliente_creado", "lead", bid, nombre, "Alta desde Seguimiento")
    return {"id": bid, "nombre": nombre, "creado": True}
