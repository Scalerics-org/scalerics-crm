"""Lo que usa la tarea de Claude Code de Bruno y el lector de WhatsApp de Juan.

Va sin sesión y se valida con OFICINA_BOT_TOKEN (header x-oficina-token), que
solo abre estas rutas. Mismo criterio que /api/linkedin-bot/: no lo mezcles con
el ADMIN_TOKEN, que abre todo el CRM.
"""

import hmac
import os

from flask import Blueprint, current_app, jsonify, request

from services import oficina as of
from services import wa_juan

oficina_bot_bp = Blueprint("oficina_bot", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


def _bot_ok() -> bool:
    esperado = os.environ.get("OFICINA_BOT_TOKEN", "")
    recibido = request.headers.get("x-oficina-token", "")
    return bool(esperado) and len(esperado) >= 32 and hmac.compare_digest(esperado, recibido)


@oficina_bot_bp.before_request
def _candado_bot():
    if not _bot_ok():
        return jsonify({"ok": False, "error": "No autorizado"}), 403
    return None


@oficina_bot_bp.route("/api/oficina-bot/recordatorios")
def bot_recordatorios():
    return jsonify({"ok": True, **of.recordatorios_para_bot(_db())})


@oficina_bot_bp.route("/api/oficina-bot/recordatorios", methods=["POST"])
def bot_crear_recordatorio():
    try:
        return jsonify({"ok": True, **of.crear_recordatorio(_db(), request.get_json(silent=True) or {})})
    except of.NoSePuede as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@oficina_bot_bp.route("/api/oficina-bot/recordatorios/<int:rec_id>/borrador", methods=["POST"])
def bot_borrador(rec_id):
    try:
        of.guardar_borrador(_db(), rec_id, (request.get_json(silent=True) or {}).get("texto"))
    except of.NoSePuede as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True})


@oficina_bot_bp.route("/api/oficina-bot/recordatorios/<int:rec_id>/hecho", methods=["POST"])
def bot_hecho(rec_id):
    try:
        of.cerrar_por_bot(_db(), rec_id)
    except of.NoSePuede as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True})


# Plantillas que Juan mandó desde su WhatsApp: las manda el lector de su notebook.
@oficina_bot_bp.route("/api/oficina-bot/envios")
def bot_envios():
    return jsonify({"ok": True, "envios": wa_juan.pendientes(_db())})


@oficina_bot_bp.route("/api/oficina-bot/envios/<int:envio_id>/resultado", methods=["POST"])
def bot_resultado_envio(envio_id):
    datos = request.get_json(silent=True) or {}
    try:
        wa_juan.resultado(_db(), envio_id, bool(datos.get("ok")), datos.get("error"))
    except wa_juan.NoSePuede as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True})
