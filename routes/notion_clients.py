"""Panel de clientes: espejo de la database Clientes, con arrastre de estado.

Lo unico que el CRM le escribe a esa database es el estado de una ficha,
cuando alguien la arrastra a otra columna del tablero. Nombre, descripcion,
fechas y proyecto se siguen manejando solo en Notion: no hay POST para crear
ni PUT para editar una ficha.
"""

import os

from flask import Blueprint, current_app, jsonify, request, session

from database import (ETAPAS_CLIENTE, get_business, get_notion_client_by_id,
                      get_notion_clients, get_projects, log_activity,
                      vincular_notion_client)
from services.auth import require_panel
from services.notion_service import (ESTADO_ACEPTADO, GRUPOS_CLIENTES,
                                     estados_de_clientes, grupo_de_cliente,
                                     mover_cliente, pasar_a_cliente)

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

    business_id = cliente.get("business_id")
    antes = get_business(db, business_id) if business_id else None

    ok, error = mover_cliente(db, cliente_id, estado)
    if not ok:
        return jsonify({"ok": False, "error": error}), 502

    if cliente.get("status") != estado:
        log_activity(db, session.get("user_name", "sistema"), "notion_client_moved",
                     "notion_client", cliente_id, cliente.get("name", ""), estado,
                     user_id=session.get("user_id"))
    # El paso a Clientes lo hace `cliente_cambio_de_estado` adentro de
    # `mover_cliente`; aca solo se mira si paso, para avisarle a quien arrastro.
    paso = False
    if antes:
        despues = get_business(db, business_id) or {}
        paso = ((antes.get("crm_status") or "") not in ETAPAS_CLIENTE
                and (despues.get("crm_status") or "") in ETAPAS_CLIENTE)
    return jsonify({"ok": True, "estado": estado, "grupo": grupo_de_cliente(estado),
                    "paso_a_clientes": paso,
                    "sin_conectar": estado == ESTADO_ACEPTADO and not business_id})


@notion_clients_bp.route("/api/notion-clients/<int:cliente_id>/cliente-crm", methods=["PUT"])
def api_vincular_cliente(cliente_id):
    """Conecta una ficha con su negocio del CRM, o la desconecta.

    Body: `{"business_id": <id>}` para conectar, `{"business_id": null}` para
    desconectar. Si la ficha ya esta en "Presupuesto Aceptado", conectarla pasa
    al negocio a Clientes en el momento: si no, una ficha aceptada antes de
    conectarse no llegaria nunca.
    """
    db = current_app.config["DB_PATH"]
    if not _token_admin_ok():
        candado = require_panel(db, "notion_clients")
        if candado:
            return candado

    ficha = get_notion_client_by_id(db, cliente_id)
    if not ficha:
        return jsonify({"ok": False, "error": "la ficha no existe"}), 404

    datos = request.get_json(silent=True)
    if not isinstance(datos, dict) or "business_id" not in datos:
        return jsonify({"ok": False, "error": "falta business_id"}), 400
    business_id = datos["business_id"]
    negocio = None
    if business_id is not None:
        if isinstance(business_id, bool) or not isinstance(business_id, int) or business_id <= 0:
            return jsonify({"ok": False,
                            "error": "business_id tiene que ser el id de una persona del CRM"}), 400
        negocio = get_business(db, business_id)
        if not negocio:
            return jsonify({"ok": False, "error": "esa persona no existe en el CRM"}), 404

    quien = session.get("user_name", "sistema")
    vincular_notion_client(db, cliente_id, business_id)
    paso = pasar_a_cliente(db, get_notion_client_by_id(db, cliente_id), quien=quien) if negocio else False
    log_activity(db, quien, "notion_client_linked", "notion_client", cliente_id,
                 ficha.get("name", ""), negocio.get("name", "") if negocio else "",
                 user_id=session.get("user_id"))
    return jsonify({"ok": True, "business_id": business_id,
                    "business_name": negocio.get("name") if negocio else None,
                    "paso_a_clientes": paso})
