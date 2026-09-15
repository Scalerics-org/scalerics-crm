"""Panel de proyectos: espejo de solo lectura de la database Projects."""

from flask import Blueprint, current_app, jsonify

from database import get_projects, get_tasks

projects_bp = Blueprint("projects", __name__)


@projects_bp.route("/api/projects")
def api_projects():
    """Los proyectos con sus tareas y el conteo por estado.

    Solo lectura: no hay ruta para crear ni editar. El panel linkea a Notion,
    que es donde el equipo los maneja.
    """
    db = current_app.config["DB_PATH"]
    tareas = get_tasks(db)
    # El esfuerzo se guarda del lado del CRM (Inteligencia financiera): el
    # proyecto sigue siendo un espejo de solo lectura de Notion.
    from services.inteligencia_fin import esfuerzos
    esfuerzo = esfuerzos(db)
    salida = []
    for p in get_projects(db):
        suyas = [t for t in tareas if t.get("notion_project_page_id") == p["notion_page_id"]]
        e = esfuerzo.get(p["id"]) or {}
        salida.append({
            **p,
            "esfuerzo_horas": e.get("horas"),
            "esfuerzo_valor": e.get("valor_cargado"),
            "esfuerzo_unidad": e.get("unidad_cargada"),
            "tasks": suyas,
            "conteo": {
                "todo": sum(1 for t in suyas if t.get("status") == "todo"),
                "in_progress": sum(1 for t in suyas if t.get("status") == "in_progress"),
                "done": sum(1 for t in suyas if t.get("status") == "done"),
            },
        })
    return jsonify(salida)
