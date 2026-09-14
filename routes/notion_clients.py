"""Panel de clientes: espejo de la database Clientes, con arrastre de estado.

Lo unico que el CRM le escribe a esa database es el estado de una ficha,
cuando alguien la arrastra a otra columna del tablero. Nombre, descripcion,
fechas y proyecto se siguen manejando solo en Notion: no hay POST para crear
ni PUT para editar una ficha.
"""

import os

from flask import Blueprint, current_app, jsonify, request, session

from database import (get_notion_client_by_id, get_notion_clients, get_projects,
                      log_activity)
from services.auth import require_panel
from services.notion_service import (GRUPOS_CLIENTES, estados_de_clientes,
                                     grupo_de_cliente, mover_cliente)

notion_clients_bp = Blueprint("notion_clients", __name__)


def _token_admin_ok() -> bool:
    esperado = os.environ.get("ADMIN_TOKEN", "")
    return bool(esperado) and request.headers.get("x-admin-token", "") == esperado


@notion_clients_bp.route("/api/notion-clients")
def api_notion_clients():
    """Las columnas del tablero y sus fichas.

    Devuelve `columnas` (un estado por columna, en el orden de Notion) y
    `clientes` (cada ficha con su grupo y el nombre de su proyecto).

    Estas fichas son el pipeline que el equipo maneja en Notion, no los leads
    del CRM.
    """
    db = current_app.config["DB_PATH"]
    proyectos = {p["notion_page_id"]: p["name"] for p in get_projects(db)}
    return jsonify({
        "columnas": estados_de_clientes(),
        "clientes": [
            {**c,
             "grupo": grupo_de_cliente(c.get("status")),
             "project_name": proyectos.get(c.get("notion_project_page_id"))}
            for c in get_notion_clients(db)
        ],
    })


@notion_clients_bp.route("/api/notion-clients/<int:cliente_id>/estado", methods=["POST"])
def api_mover_cliente(cliente_id):
    """Mueve una ficha a otra columna, escribiendo en Notion primero.

    Sincrono a proposito, igual que el arrastre de Tareas: el front devuelve
    la ficha a su columna si esto falla, asi que no puede contestar antes de
    saber si Notion acepto.

    Pide el panel `notion_clients`: esto le escribe a una database del equipo,
    y quien no ve el tablero no tiene por que poder moverlo con un fetch.
    """
    db = current_app.config["DB_PATH"]
    if not _token_admin_ok():
        candado = require_panel(db, "notion_clients")
        if candado:
            return candado

    cliente = get_notion_client_by_id(db, cliente_id)
    if not cliente:
        return jsonify({"ok": False, "error": "la ficha no existe"}), 404

    estado = ((request.get_json(silent=True) or {}).get("estado") or "").strip()
    if estado not in GRUPOS_CLIENTES:
        return jsonify({"ok": False,
                        "error": f"'{estado}' no es una columna del tablero"}), 400

    ok, error = mover_cliente(db, cliente_id, estado)
    if not ok:
        return jsonify({"ok": False, "error": error}), 502

    if cliente.get("status") != estado:
        log_activity(db, session.get("user_name", "sistema"), "notion_client_moved",
                     "notion_client", cliente_id, cliente.get("name", ""), estado,
                     user_id=session.get("user_id"))
    return jsonify({"ok": True, "estado": estado, "grupo": grupo_de_cliente(estado)})
