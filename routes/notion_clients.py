"""Panel de clientes: espejo de solo lectura de la database Clientes."""

from flask import Blueprint, current_app, jsonify

from database import get_notion_clients, get_projects
from services.notion_service import grupo_de_cliente

notion_clients_bp = Blueprint("notion_clients", __name__)


@notion_clients_bp.route("/api/notion-clients")
def api_notion_clients():
    """Las fichas del tablero con su grupo y el nombre de su proyecto.

    Solo lectura, igual que Proyectos: no hay POST ni PUT. Estas fichas son el
    pipeline que el equipo maneja a mano en Notion, no los leads del CRM.
    """
    db = current_app.config["DB_PATH"]
    proyectos = {p["notion_page_id"]: p["name"] for p in get_projects(db)}
    return jsonify([
        {**c,
         "grupo": grupo_de_cliente(c.get("status")),
         "project_name": proyectos.get(c.get("notion_project_page_id"))}
        for c in get_notion_clients(db)
    ])
