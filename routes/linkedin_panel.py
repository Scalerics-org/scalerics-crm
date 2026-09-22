"""Endpoints del panel LinkedIn (MARKETING).

Finos: validan, llaman a `services/linkedin_borradores.py` y serializan. Todo
pide el panel `linkedin`. "Generar ahora" además pide admin.

No confundir con `routes/linkedin.py`, que es lo que usa el cron de GitHub
(generar, enviar el mail, avisar un fallo y el link "ya lo publiqué").
"""

import json
import secrets
from datetime import datetime

from flask import Blueprint, Response, current_app, jsonify, request, session

from database import create_job
from services import linkedin_borradores as lb
from services.auth import is_admin, require_admin, require_panel
from services.job_service import get_worker

linkedin_panel_bp = Blueprint("linkedin_panel", __name__)

_TOPE_TEXTO = 10000


def _db() -> str:
    return current_app.config["DB_PATH"]


def _ahora():
    """Aparte para poder fijar el momento en los tests."""
    return lb.ahora_utc()


@linkedin_panel_bp.before_request
def _candado():
    return require_panel(_db(), "linkedin")


@linkedin_panel_bp.route("/api/linkedin/borradores")
def api_borradores():
    ahora = _ahora()
    crudo = request.args.get("semana")
    if crudo:
        dia = lb.parse_fecha(crudo)
        if dia is None:
            return jsonify({"ok": False, "error": "semana tiene que ser AAAA-MM-DD"}), 400
        semana = lb.lunes_de(dia).isoformat()
    else:
        semana = lb.semana_actual(ahora)
    borradores = lb.listar_semana(_db(), semana)
    pedidos = lb.correcciones_de(_db(), [b["id"] for b in borradores])
    for b in borradores:
        b["correcciones"] = pedidos.get(b["id"], [])[:3]
    return jsonify({
        "ok": True,
        "semana": semana,
        "semana_actual": lb.semana_actual(ahora),
        "hoy": lb.hoy_montevideo(ahora).isoformat(),
        "limite": lb.LIMITE_LINKEDIN,
        "proxima_generacion": lb.proxima_generacion(ahora),
        "borradores": borradores,
        "puede_generar": is_admin(_db(), session.get("user_id")),
    })


@linkedin_panel_bp.route("/api/linkedin/borradores/<int:borrador_id>", methods=["PUT"])
def api_editar(borrador_id):
    data = request.get_json(silent=True) or {}
    texto = data.get("texto")
    if not isinstance(texto, str) or not texto.strip():
        return jsonify({"ok": False, "error": "El texto no puede quedar vacío."}), 400
    if len(texto) > _TOPE_TEXTO:
        return jsonify({"ok": False, "error": f"El texto no puede pasar de {_TOPE_TEXTO} caracteres."}), 400
    fila = lb.editar(_db(), borrador_id, texto, session.get("user_name") or "")
    if not fila:
        return jsonify({"ok": False, "error": "No existe ese borrador"}), 404
    return jsonify({"ok": True, "borrador": fila})


@linkedin_panel_bp.route("/api/linkedin/borradores/<int:borrador_id>/estado", methods=["POST"])
def api_estado(borrador_id):
    data = request.get_json(silent=True) or {}
    estado = data.get("estado")
    if estado not in lb.ESTADOS:
        return jsonify({"ok": False, "error": "estado tiene que ser borrador, publicado o descartado"}), 400
    fecha = None
    if data.get("fecha"):
        fecha = lb.parse_fecha(data["fecha"])
        if fecha is None:
            return jsonify({"ok": False, "error": "fecha tiene que ser AAAA-MM-DD"}), 400
    fila = lb.cambiar_estado(_db(), borrador_id, estado, fecha, _ahora())
    if not fila:
        return jsonify({"ok": False, "error": "No existe ese borrador"}), 404
    return jsonify({"ok": True, "borrador": fila})


@linkedin_panel_bp.route("/api/linkedin/borradores/<int:borrador_id>/otra-idea", methods=["POST"])
def api_otra_idea(borrador_id):
    try:
        fila = lb.otra_idea(_db(), borrador_id, _ahora())
    except lb.NoSePuede as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "borrador": fila})


@linkedin_panel_bp.route("/api/linkedin/borradores/<int:borrador_id>/correccion", methods=["POST"])
def api_pedir_correccion(borrador_id):
    data = request.get_json(silent=True) or {}
    quien = session.get("user_name") or session.get("user_email") or ""
    try:
        lb.pedir_correccion(_db(), borrador_id, data.get("pedido"), quien)
    except lb.NoSePuede as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True,
                    "correcciones": lb.correcciones_de(_db(), [borrador_id]).get(borrador_id, [])[:3]})


@linkedin_panel_bp.route("/api/linkedin/borradores/<int:borrador_id>/imagen")
def api_imagen(borrador_id):
    """La tarjeta que salió en el mail. Con ?descargar=1 baja como archivo."""
    datos = lb.imagen_de(_db(), borrador_id)
    if not datos:
        return jsonify({"ok": False, "error": "Este borrador no tiene imagen guardada"}), 404
    png, semana, orden = datos
    respuesta = Response(png, mimetype="image/png")
    respuesta.headers["Cache-Control"] = "private, max-age=3600"
    respuesta.headers["X-Content-Type-Options"] = "nosniff"
    if request.args.get("descargar"):
        respuesta.headers["Content-Disposition"] = f'attachment; filename="linkedin-{semana}-{orden}.png"'
    return respuesta


@linkedin_panel_bp.route("/api/linkedin/borradores/generar", methods=["POST"])
def api_generar():
    """Lo mismo que hace el cron al empezar: encola el job que arma dos
    borradores del banco. No manda el mail ni hace la imagen: eso lo hace la
    corrida de GitHub, que renderiza las tarjetas y llama a /enviar."""
    err = require_admin(_db())
    if err:
        return err
    if get_worker() is None:
        return jsonify({"ok": False, "error": "El generador no está corriendo en este momento."}), 503
    lote = secrets.token_urlsafe(12)
    payload = {"db_path": _db(), "lote": lote, "ahora": datetime.now().isoformat()}
    job_id = create_job(_db(), "linkedin", json.dumps(payload))
    return jsonify({"ok": True, "job_id": job_id, "lote": lote}), 202
