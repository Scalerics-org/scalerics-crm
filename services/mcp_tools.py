"""Las herramientas que el CRM le ofrece a Claude por MCP.

Solo lectura, leads y pipeline. Cada herramienta es un dict con `name`,
`description`, `inputSchema` (JSON Schema) y `fn`. La validación de argumentos
es propia y chica: tipos básicos, requeridos, rangos, enum y
`additionalProperties: false`. No vale la pena una librería para tres schemas.

Dos clases de error, y la diferencia importa para Claude:

- `ArgumentosInvalidos`: el pedido está mal armado (argumento que falta, tipo
  equivocado, herramienta que no existe). Vuelve como error JSON-RPC -32602.
- `ErrorHerramienta`: el pedido estaba bien pero no hay resultado (lead que no
  existe). Vuelve como `isError: true` con texto, que Claude lee y explica.

Cada llamada deja una fila en `mcp_llamadas`: quién, qué, con qué, si salió,
cuánto tardó. Si registrar falla, la llamada sale igual (mismo criterio que
`database.log_activity`).
"""

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from database import (ETAPAS_CLIENTE, ETAPAS_PRECLIENTE, _connect, get_business,
                      get_client_info, get_lead_events)

logger = logging.getLogger(__name__)

TOPE_LEADS = 200
TOPE_HISTORIAL = 20


class ArgumentosInvalidos(Exception):
    pass


class ErrorHerramienta(Exception):
    pass


def init_mcp(conn: sqlite3.Connection) -> None:
    """Tabla de llamadas. La llama `database.init_db` con su conexión."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mcp_llamadas (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER,
            user_name    TEXT NOT NULL,
            herramienta  TEXT NOT NULL,
            argumentos   TEXT,
            ok           INTEGER NOT NULL,
            error        TEXT,
            ms           INTEGER,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


# ── validación ───────────────────────────────────────────────────────────────

_TIPOS = {"string": str, "integer": int, "boolean": bool}


def validar_argumentos(schema: dict, args) -> dict:
    """Devuelve los argumentos con los defaults completados, o levanta
    ArgumentosInvalidos con un mensaje que dice qué está mal."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ArgumentosInvalidos("arguments tiene que ser un objeto")
    props = schema.get("properties", {})
    sobran = set(args) - set(props)
    if sobran:
        raise ArgumentosInvalidos(f"Argumentos desconocidos: {', '.join(sorted(sobran))}")
    for req in schema.get("required", []):
        if req not in args:
            raise ArgumentosInvalidos(f"Falta el argumento obligatorio '{req}'")
    salida = {}
    for nombre, prop in props.items():
        if nombre not in args:
            if "default" in prop:
                salida[nombre] = prop["default"]
            continue
        valor = args[nombre]
        if valor is None:
            # Un null explícito cuenta como ausente: error si era obligatorio,
            # default si lo tenía, y si no, se omite.
            if nombre in schema.get("required", []):
                raise ArgumentosInvalidos(f"'{nombre}' no puede ser null")
            if "default" in prop:
                salida[nombre] = prop["default"]
            continue
        tipo = _TIPOS[prop["type"]]
        # bool es subclase de int en Python: un True no es un integer válido.
        if not isinstance(valor, tipo) or (tipo is int and isinstance(valor, bool)):
            raise ArgumentosInvalidos(f"'{nombre}' tiene que ser {prop['type']}")
        if tipo is str and len(valor) < prop.get("minLength", 0):
            raise ArgumentosInvalidos(f"'{nombre}' tiene que tener al menos {prop['minLength']} caracteres")
        if tipo is int and ("minimum" in prop and valor < prop["minimum"]
                            or "maximum" in prop and valor > prop["maximum"]):
            raise ArgumentosInvalidos(f"'{nombre}' tiene que estar entre {prop.get('minimum')} y {prop.get('maximum')}")
        if "enum" in prop and valor not in prop["enum"]:
            raise ArgumentosInvalidos(f"'{nombre}' tiene que ser una de: {', '.join(prop['enum'])}")
        salida[nombre] = valor
    return salida


# ── herramientas ─────────────────────────────────────────────────────────────

ETAPAS_DESCRIPCION = {
    "demo_agendada": "demo pactada, todavía no se dio",
    "no_se_conecto": "estaba agendada y el lead no se conectó, hay que reagendar",
    "demo_1": "primera demo dada",
    "demo_2": "segunda demo",
    "demo_3": "tercera demo",
    "presupuesto_enviado": "se mandó el presupuesto",
    "follow_up_1": "primer seguimiento después del presupuesto",
    "follow_up_2": "segundo seguimiento",
    "acepto": "dijo que sí, falta firma o pago",
    "en_espera": "frenado por el cliente, sin cerrar",
    "rechazo": "dijo que no después de haber avanzado",
    "cerrado": "cliente: el acuerdo se concretó",
    "en_desarrollo": "cliente: el proyecto está en desarrollo",
    "finalizado": "cliente: proyecto entregado",
}

_ETAPAS_TODAS = tuple(ETAPAS_PRECLIENTE) + tuple(ETAPAS_CLIENTE)


def _descripcion_pipeline() -> str:
    lineas = "; ".join(f"{e} ({ETAPAS_DESCRIPCION.get(e, '')})" for e in _ETAPAS_TODAS)
    return ("Los leads en seguimiento comercial, agrupados por etapa, en el orden en que avanza "
            "una venta. Filtra por etapa y por nombre del responsable. Por defecto muestra solo "
            "pre-clientes; con incluir_clientes=true agrega los cerrados y en desarrollo. "
            f"Etapas válidas: {lineas}.")


def _buscar_leads(db: str, texto: str, limite: int) -> dict:
    return {}


def _ver_lead(db: str, lead_id: int) -> dict:
    return {}


def _pipeline(db: str, etapa: Optional[str], responsable: Optional[str], incluir_clientes: bool) -> dict:
    return {}


HERRAMIENTAS: list[dict] = [
    {
        "name": "buscar_leads",
        "description": ("Busca leads del CRM por nombre del negocio, email o teléfono. Devuelve "
                        "hasta `limite` resultados con id, nombre, etapa y contacto. Usá el id "
                        "con ver_lead para la ficha completa."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "texto": {"type": "string", "minLength": 2,
                          "description": "Nombre, parte del nombre, email o teléfono"},
                "limite": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            "required": ["texto"],
            "additionalProperties": False,
        },
        "fn": lambda db, a: _buscar_leads(db, a["texto"], a["limite"]),
    },
    {
        "name": "ver_lead",
        "description": ("La ficha completa de un lead: datos de contacto, etapa, origen, notas, "
                        "responsables, lo relevado en la reunión, los últimos eventos del "
                        "pipeline y las últimas llamadas."),
        "inputSchema": {
            "type": "object",
            "properties": {"lead_id": {"type": "integer", "minimum": 1}},
            "required": ["lead_id"],
            "additionalProperties": False,
        },
        "fn": lambda db, a: _ver_lead(db, a["lead_id"]),
    },
    {
        "name": "pipeline",
        "description": _descripcion_pipeline(),
        "inputSchema": {
            "type": "object",
            "properties": {
                "etapa": {"type": "string", "enum": list(_ETAPAS_TODAS),
                          "description": "Una sola etapa; sin este argumento trae todas"},
                "responsable": {"type": "string", "minLength": 2,
                                "description": "Nombre (o parte) del responsable del lead"},
                "incluir_clientes": {"type": "boolean", "default": False},
            },
            "required": [],
            "additionalProperties": False,
        },
        "fn": lambda db, a: _pipeline(db, a.get("etapa"), a.get("responsable"), a["incluir_clientes"]),
    },
]

_POR_NOMBRE = {h["name"]: h for h in HERRAMIENTAS}


def listar_herramientas() -> list[dict]:
    """Lo que va en tools/list: todo menos la función."""
    return [{k: v for k, v in h.items() if k != "fn"} for h in HERRAMIENTAS]


def ejecutar(db: str, usuario: dict, nombre: str, argumentos) -> dict:
    """Corre una herramienta y devuelve el `result` de tools/call:
    {"content": [{"type": "text", "text": ...}], "isError": bool}."""
    h = _POR_NOMBRE.get(nombre)
    if not h:
        raise ArgumentosInvalidos(f"No existe la herramienta '{nombre}'")
    args = validar_argumentos(h["inputSchema"], argumentos)
    inicio = time.monotonic()
    ok, error, texto = True, None, ""
    try:
        resultado = h["fn"](db, args)
        texto = json.dumps(resultado, ensure_ascii=False, default=str)
    except ErrorHerramienta as e:
        ok, error, texto = False, str(e), str(e)
    except Exception:
        logger.exception("Falló la herramienta MCP %s con %s", nombre, args)
        ok, error = False, "excepción"
        texto = "La herramienta falló por un error interno del CRM. Quedó registrado."
    ms = int((time.monotonic() - inicio) * 1000)
    _registrar(db, usuario, nombre, args, ok, error, ms)
    return {"content": [{"type": "text", "text": texto}], "isError": not ok}


def _registrar(db, usuario, nombre, args, ok, error, ms) -> None:
    try:
        conn = _connect(db)
        try:
            conn.execute(
                "INSERT INTO mcp_llamadas (user_id, user_name, herramienta, argumentos, ok, error, ms) "
                "VALUES (?,?,?,?,?,?,?)",
                (usuario.get("id"), usuario.get("name") or "sistema", nombre,
                 json.dumps(args, ensure_ascii=False), 1 if ok else 0, error, ms))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("No se pudo registrar la llamada MCP %s", nombre)
