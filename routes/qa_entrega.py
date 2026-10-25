"""Control de calidad antes de entregar (services/qa_entrega.py).

Todo pide el panel `qa`. No hay nada público: el resultado es para el equipo.

`POST /api/qa/proyectos/<id>/revisar` corre la revisión en el pedido mismo
(tarda hasta TIEMPO_MAX segundos) y devuelve el proyecto con el resultado. Es
a propósito: quien la pide está mirando la pantalla esperando el resultado, y
un hilo de fondo obligaría a la pantalla a preguntar cada tanto.
"""

from flask import Blueprint, current_app, jsonify, request, session

from database import log_activity
from services import qa_entrega as qa
from services.auth import require_panel

qa_entrega_bp = Blueprint("qa_entrega", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


def _quien() -> str:
    return session.get("user_name", "sistema")


def _error(msg, codigo: int = 400):
    return jsonify({"ok": False, "error": str(msg)}), codigo


def _codigo(e: qa.NoSePuede) -> int:
    return 404 if "no existe" in str(e) else 400


@qa_entrega_bp.before_request
def _candado():
    return require_panel(_db(), "qa")


@qa_entrega_bp.route("/api/qa/proyectos")
def api_listar():
    proyectos = qa.listar(_db())
    return jsonify({"ok": True, "proyectos": proyectos, "resumen": qa.resumen(proyectos),
                    "tipos": qa.TIPOS_PROYECTO, "clientes": qa.clientes(_db())})


@qa_entrega_bp.route("/api/qa/proyectos", methods=["POST"])
def api_crear():
    try:
        p = qa.crear(_db(), request.get_json(silent=True) or {}, _quien())
    except qa.NoSePuede as e:
        return _error(e)
    log_activity(_db(), _quien(), "qa_proyecto_alta", "qa", p["id"], p["nombre"], p["url"],
                 user_id=session.get("user_id"))
    return jsonify({"ok": True, "proyecto": p}), 201


@qa_entrega_bp.route("/api/qa/proyectos/<int:pid>")
def api_detalle(pid):
    try:
        p = qa.detalle(_db(), pid)
    except qa.NoSePuede as e:
        return _error(e, 404)
    return jsonify({"ok": True, "proyecto": p, "texto": qa.texto_resumen(p)})


@qa_entrega_bp.route("/api/qa/proyectos/<int:pid>", methods=["PATCH"])
def api_editar(pid):
    try:
        p = qa.editar(_db(), pid, request.get_json(silent=True) or {})
    except qa.NoSePuede as e:
        return _error(e, _codigo(e))
    return jsonify({"ok": True, "proyecto": p})


@qa_entrega_bp.route("/api/qa/proyectos/<int:pid>", methods=["DELETE"])
def api_borrar(pid):
    try:
        qa.borrar(_db(), pid)
    except qa.NoSePuede as e:
        return _error(e, 404)
    return jsonify({"ok": True})


@qa_entrega_bp.route("/api/qa/proyectos/<int:pid>/revisar", methods=["POST"])
def api_revisar(pid):
    try:
        p = qa.revisar(_db(), pid, _quien())
    except qa.NoSePuede as e:
        return _error(e, _codigo(e))
    log_activity(_db(), _quien(), "qa_revision", "qa", pid, p["nombre"],
                 f"{p['ultima']['puntaje']}/100", user_id=session.get("user_id"))
    return jsonify({"ok": True, "proyecto": p, "texto": qa.texto_resumen(p)})


@qa_entrega_bp.route("/api/qa/proyectos/<int:pid>/checklist", methods=["POST"])
def api_marcar(pid):
    datos = request.get_json(silent=True) or {}
    try:
        p = qa.marcar(_db(), pid, str(datos.get("clave") or ""), bool(datos.get("hecho")), _quien())
    except qa.NoSePuede as e:
        return _error(e, _codigo(e))
    return jsonify({"ok": True, "proyecto": p, "texto": qa.texto_resumen(p)})


@qa_entrega_bp.route("/api/qa/proyectos/<int:pid>/no-aplica", methods=["POST"])
def api_no_aplica(pid):
    datos = request.get_json(silent=True) or {}
    motivo = datos.get("motivo")
    try:
        p = qa.no_aplica(_db(), pid, str(datos.get("clave") or ""),
                         None if motivo is None else str(motivo), _quien())
    except qa.NoSePuede as e:
        return _error(e, _codigo(e))
    return jsonify({"ok": True, "proyecto": p, "texto": qa.texto_resumen(p)})


@qa_entrega_bp.route("/api/qa/proyectos/<int:pid>/entregar", methods=["POST"])
def api_entregar(pid):
    deshacer = bool((request.get_json(silent=True) or {}).get("deshacer"))
    try:
        p = qa.entregar(_db(), pid, _quien(), deshacer=deshacer)
    except qa.NoSePuede as e:
        return _error(e, _codigo(e))
    if not deshacer:
        log_activity(_db(), _quien(), "qa_entregado", "qa", pid, p["nombre"], p["url"],
                     user_id=session.get("user_id"))
    return jsonify({"ok": True, "proyecto": p, "texto": qa.texto_resumen(p)})
