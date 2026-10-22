"""Agentes: lo que proponen y hacen las tareas programadas de Claude del equipo.

Cada miembro de Scalerics tiene una tarea programada de Claude Code en la nube
que corre de noche con su cuenta (prompt en docs/agentes-rutina.md). Piensa
qué puede sumarle a Scalerics, lo hace (una PR, un borrador, una
investigación) y lo carga acá por el conector MCP (services/mcp_tools.py).
Una persona lo acepta, lo descarta o lo marca como hecho desde el panel Agentes.

La brújula es el texto que el equipo escribe arriba del panel: las prioridades
del momento, lo que no hace falta, lo que gusta. El agente la lee antes de
elegir qué hacer; sin ella elige solo con lo que ve en el CRM y los repos.

El agente nunca cambia el estado: eso es de las personas. Puede completar su
propia propuesta (detalle, enlaces) mientras siga propuesta o aceptada, que es
como entrega lo que le pidieron en el comentario de la revisión.
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from database import _connect

FORMATO = "%Y-%m-%d %H:%M:%S"
# Proyectos grandes, no tareas sueltas (pedido de Juan, 22/10): una sección
# nueva del CRM, un producto para vender, una tecnología para adoptar, una
# forma nueva de trabajar.
TIPOS = ("crm", "producto", "tecnologia", "proceso", "otro")
ESTADOS = ("propuesta", "aceptada", "descartada", "hecha")
# Qué estado deja cada acción del panel. Desde cualquier estado: una
# descartada se puede rescatar y una hecha se puede reabrir.
ACCIONES = {"aceptar": "aceptada", "descartar": "descartada", "hecha": "hecha",
            "reabrir": "propuesta"}
EDITABLES_POR_EL_AGENTE = ("propuesta", "aceptada")
LARGO_TITULO = 120
LARGO_RESUMEN = 600
LARGO_INSPIRACION = 300
LARGO_DETALLE = 20000
LARGO_COMENTARIO = 2000
LARGO_BRUJULA = 3000
LARGO_CLAVE = 100
LARGO_ETIQUETA = 60
LARGO_URL = 500
TOPE_ENLACES = 5
TOPE_LISTA = 100


class NoSePuede(Exception):
    pass


def init_agentes(conn: sqlite3.Connection) -> None:
    """Tabla de propuestas. La llama `database.init_db` con su conexión."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agentes_propuestas (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            autor_id       INTEGER,
            autor_nombre   TEXT NOT NULL,
            tipo           TEXT NOT NULL,
            titulo         TEXT NOT NULL,
            resumen        TEXT NOT NULL,
            detalle        TEXT,
            enlaces        TEXT NOT NULL DEFAULT '[]',   -- JSON [{etiqueta, url}]
            inspiracion    TEXT,                         -- qué empresa lo hace así
            clave          TEXT UNIQUE,                  -- la pone el agente, para no repetir
            estado         TEXT NOT NULL DEFAULT 'propuesta',
            comentario     TEXT,                         -- de quien revisa
            revisado_por   TEXT,
            revisado_en    TEXT,
            creado_en      TEXT NOT NULL,
            actualizado_en TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agentes_estado "
                 "ON agentes_propuestas (estado, actualizado_en)")
    # Una sola fila (id = 1): se pisa entera cada vez que alguien la guarda.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agentes_brujula (
            id             INTEGER PRIMARY KEY CHECK (id = 1),
            texto          TEXT NOT NULL,
            editado_por    TEXT NOT NULL,
            actualizado_en TEXT NOT NULL
        )
    """)


def _ahora() -> str:
    return datetime.now(timezone.utc).strftime(FORMATO)


def _texto(valor, largo: int, falta: Optional[str]) -> Optional[str]:
    """El texto sin espacios de los bordes. `falta` None = opcional."""
    texto = valor.strip() if isinstance(valor, str) else ""
    if not texto:
        if falta:
            raise NoSePuede(falta)
        return None
    if len(texto) > largo:
        raise NoSePuede(f"Es muy largo: máximo {largo} caracteres")
    return texto


def _enlaces(valor) -> list[dict]:
    """Solo http(s) con host: un `javascript:` en el panel sería un XSS con un
    clic. La lista se reemplaza entera, no se suma."""
    if valor is None:
        return []
    if not isinstance(valor, list):
        raise NoSePuede("enlaces tiene que ser una lista")
    if len(valor) > TOPE_ENLACES:
        raise NoSePuede(f"Como mucho {TOPE_ENLACES} enlaces")
    salida = []
    for e in valor:
        if not isinstance(e, dict) or set(e) - {"etiqueta", "url"}:
            raise NoSePuede("Cada enlace es {etiqueta, url}")
        url = _texto(e.get("url"), LARGO_URL, "Falta la url del enlace")
        partes = urlparse(url)
        if partes.scheme not in ("http", "https") or not partes.netloc:
            raise NoSePuede(f"La url tiene que empezar con http:// o https://: {url[:80]}")
        etiqueta = _texto(e.get("etiqueta"), LARGO_ETIQUETA, None) or partes.netloc
        salida.append({"etiqueta": etiqueta, "url": url})
    return salida


def _fila(f, con_detalle: bool) -> dict:
    d = dict(f)
    d["enlaces"] = json.loads(d["enlaces"] or "[]")
    if not con_detalle:
        d.pop("detalle", None)
    return d


def proponer(db: str, autor: dict, tipo, titulo, resumen, detalle=None,
             enlaces=None, clave=None, inspiracion=None) -> dict:
    """Crea la propuesta. Con una `clave` que ya existe no crea otra: devuelve
    la que está, con `repetida`, para que el agente lo sepa y no insista."""
    if tipo not in TIPOS:
        raise NoSePuede(f"tipo tiene que ser una de: {', '.join(TIPOS)}")
    titulo = _texto(titulo, LARGO_TITULO, "Falta el título")
    resumen = _texto(resumen, LARGO_RESUMEN, "Falta el resumen")
    detalle = _texto(detalle, LARGO_DETALLE, None)
    clave = _texto(clave, LARGO_CLAVE, None)
    inspiracion = _texto(inspiracion, LARGO_INSPIRACION, None)
    lista = _enlaces(enlaces)
    conn = _connect(db)
    try:
        if clave:
            previa = conn.execute("SELECT id, estado FROM agentes_propuestas WHERE clave = ?",
                                  (clave,)).fetchone()
            if previa:
                return {"id": previa["id"], "estado": previa["estado"], "repetida": True}
        ahora = _ahora()
        pid = conn.execute(
            "INSERT INTO agentes_propuestas (autor_id, autor_nombre, tipo, titulo, resumen, "
            "detalle, enlaces, clave, inspiracion, creado_en, actualizado_en) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (autor.get("id"), autor.get("name") or "sin nombre", tipo, titulo, resumen, detalle,
             json.dumps(lista, ensure_ascii=False), clave, inspiracion, ahora, ahora)).lastrowid
        conn.commit()
    finally:
        conn.close()
    return {"id": pid, "estado": "propuesta", "repetida": False}


def completar(db: str, autor: dict, pid: int, resumen=None, detalle=None, enlaces=None,
              titulo=None) -> dict:
    """El agente agrega a su propia propuesta lo que hizo. No toca el estado ni
    el comentario de la revisión: si estaba aceptada, sigue aceptada."""
    conn = _connect(db)
    try:
        f = conn.execute("SELECT autor_id, estado FROM agentes_propuestas WHERE id = ?",
                         (pid,)).fetchone()
        if not f:
            raise NoSePuede(f"No existe la propuesta {pid}")
        if f["autor_id"] != autor.get("id"):
            raise NoSePuede("Solo se puede completar una propuesta propia")
        if f["estado"] not in EDITABLES_POR_EL_AGENTE:
            raise NoSePuede(f"La propuesta está {f['estado']}: ya no se completa")
        cambios = {}
        if titulo is not None:
            cambios["titulo"] = _texto(titulo, LARGO_TITULO, "El título no puede quedar vacío")
        if resumen is not None:
            cambios["resumen"] = _texto(resumen, LARGO_RESUMEN, "El resumen no puede quedar vacío")
        if detalle is not None:
            cambios["detalle"] = _texto(detalle, LARGO_DETALLE, None)
        if enlaces is not None:
            cambios["enlaces"] = json.dumps(_enlaces(enlaces), ensure_ascii=False)
        if not cambios:
            raise NoSePuede("No hay nada para cambiar")
        cambios["actualizado_en"] = _ahora()
        sets = ", ".join(f"{k} = ?" for k in cambios)
        conn.execute(f"UPDATE agentes_propuestas SET {sets} WHERE id = ?", (*cambios.values(), pid))
        conn.commit()
    finally:
        conn.close()
    return {"id": pid, "estado": f["estado"]}


def listar(db: str, estado: Optional[str] = None, limite: int = 30,
           con_detalle: bool = False) -> list[dict]:
    """Las más recientes primero (por última actualización)."""
    if estado is not None and estado not in ESTADOS:
        raise NoSePuede(f"estado tiene que ser una de: {', '.join(ESTADOS)}")
    limite = max(1, min(int(limite), TOPE_LISTA))
    sql = "SELECT * FROM agentes_propuestas "
    args: list = []
    if estado:
        sql += "WHERE estado = ? "
        args.append(estado)
    sql += "ORDER BY actualizado_en DESC, id DESC LIMIT ?"
    args.append(limite)
    conn = _connect(db)
    try:
        return [_fila(f, con_detalle) for f in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def ver(db: str, pid: int) -> dict:
    conn = _connect(db)
    try:
        f = conn.execute("SELECT * FROM agentes_propuestas WHERE id = ?", (pid,)).fetchone()
    finally:
        conn.close()
    if not f:
        raise NoSePuede(f"No existe la propuesta {pid}")
    return _fila(f, con_detalle=True)


def conteos(db: str) -> dict:
    conn = _connect(db)
    try:
        filas = conn.execute("SELECT estado, COUNT(*) n FROM agentes_propuestas "
                             "GROUP BY estado").fetchall()
    finally:
        conn.close()
    salida = {e: 0 for e in ESTADOS}
    salida.update({f["estado"]: f["n"] for f in filas})
    return salida


def revisar(db: str, pid: int, accion: str, quien: str, comentario=None) -> dict:
    """Lo que hace una persona desde el panel. El comentario es lo que el
    agente lee en la próxima corrida: un comentario vacío no borra el anterior."""
    if accion not in ACCIONES:
        raise NoSePuede(f"Acción desconocida: {accion}")
    comentario = _texto(comentario, LARGO_COMENTARIO, None)
    estado = ACCIONES[accion]
    ahora = _ahora()
    conn = _connect(db)
    try:
        if not conn.execute("SELECT 1 FROM agentes_propuestas WHERE id = ?", (pid,)).fetchone():
            raise NoSePuede(f"No existe la propuesta {pid}")
        conn.execute(
            "UPDATE agentes_propuestas SET estado = ?, comentario = COALESCE(?, comentario), "
            "revisado_por = ?, revisado_en = ?, actualizado_en = ? WHERE id = ?",
            (estado, comentario, quien, ahora, ahora, pid))
        conn.commit()
    finally:
        conn.close()
    return {"id": pid, "estado": estado}


def leer_brujula(db: str) -> dict:
    conn = _connect(db)
    try:
        f = conn.execute("SELECT texto, editado_por, actualizado_en FROM agentes_brujula "
                         "WHERE id = 1").fetchone()
    finally:
        conn.close()
    return dict(f) if f else {"texto": "", "editado_por": None, "actualizado_en": None}


def guardar_brujula(db: str, texto, quien: str) -> dict:
    """Vacía se puede guardar: es volver a que el agente elija solo."""
    if not isinstance(texto, str):
        raise NoSePuede("Falta el texto")
    texto = texto.strip()
    if len(texto) > LARGO_BRUJULA:
        raise NoSePuede(f"Es muy largo: máximo {LARGO_BRUJULA} caracteres")
    conn = _connect(db)
    try:
        conn.execute("INSERT INTO agentes_brujula (id, texto, editado_por, actualizado_en) "
                     "VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET texto = excluded.texto, "
                     "editado_por = excluded.editado_por, actualizado_en = excluded.actualizado_en",
                     (texto, quien, _ahora()))
        conn.commit()
    finally:
        conn.close()
    return leer_brujula(db)
