"""Rentabilidad de proyectos (services/rentabilidad.py).

Todo pide el panel `rentabilidad`; lo que escribe pide además poder editarlo
(un rol en solo lectura ve los números pero no carga ni cambia nada).
"""

from flask import Blueprint, Response, current_app, jsonify, request, session

from database import log_activity
from services import rentabilidad as rt
from services.auth import require_edicion, require_panel
from services.tipos_proyecto import TIPOS_PROYECTO

rentabilidad_bp = Blueprint("rentabilidad", __name__)

PANEL = "rentabilidad"


def _db() -> str:
    return current_app.config["DB_PATH"]


def _quien() -> str:
    return session.get("user_name", "sistema")


def _error(msg, codigo: int = 400):
    return jsonify({"ok": False, "error": str(msg)}), codigo


def _codigo(e: rt.NoSePuede) -> int:
    return 404 if "no existe" in str(e) else 400


@rentabilidad_bp.before_request
def _candado():
    if not request.path.startswith("/api/rentabilidad"):
        return None
    bloqueo = require_panel(_db(), PANEL)
    if bloqueo or request.method == "GET":
        return bloqueo
    return require_edicion(_db(), PANEL)


def _log(accion: str, entidad_id: int, detalle: str) -> None:
    try:
        log_activity(_db(), _quien(), accion, "rentabilidad", entidad_id, "", detalle,
                     user_id=session.get("user_id"))
    except Exception:  # noqa: BLE001 - el registro de actividad nunca frena la carga
        pass


@rentabilidad_bp.route("/api/rentabilidad")
def api_tablero():
    t = rt.tablero(_db())
    return jsonify({"ok": True, **t, "personas": rt.personas(_db()), "ajustes": rt.ajustes(_db()),
                    "fases": rt.FASES, "tipos": TIPOS_PROYECTO, "hoy": rt.hoy_uy().isoformat(),
                    "ahora": rt.ahora_uy().isoformat(" ")})


@rentabilidad_bp.route("/api/rentabilidad/proyectos/<int:pid>")
def api_detalle(pid):
    try:
        return jsonify({"ok": True, **rt.detalle(_db(), pid)})
    except rt.NoSePuede as e:
        return _error(e, _codigo(e))


@rentabilidad_bp.route("/api/rentabilidad/proyectos", methods=["POST"])
def api_crear():
    try:
        pid = rt.crear_proyecto(_db(), request.get_json(silent=True) or {}, _quien())
    except rt.NoSePuede as e:
        return _error(e)
    _log("rentabilidad_proyecto", pid, "Creó el proyecto")
    return jsonify({"ok": True, "id": pid}), 201


@rentabilidad_bp.route("/api/rentabilidad/proyectos/<int:pid>", methods=["PUT"])
def api_actualizar(pid):
    try:
        rt.actualizar_proyecto(_db(), pid, request.get_json(silent=True) or {})
    except rt.NoSePuede as e:
        return _error(e, _codigo(e))
    _log("rentabilidad_proyecto", pid, "Editó el proyecto")
    return jsonify({"ok": True})


@rentabilidad_bp.route("/api/rentabilidad/proyectos/<int:pid>", methods=["DELETE"])
def api_borrar(pid):
    try:
        rt.borrar_proyecto(_db(), pid)
    except rt.NoSePuede as e:
        return _error(e, _codigo(e))
    _log("rentabilidad_proyecto", pid, "Borró el proyecto con sus horas")
    return jsonify({"ok": True})


@rentabilidad_bp.route("/api/rentabilidad/horas", methods=["POST"])
def api_cargar():
    try:
        hid = rt.cargar_horas(_db(), request.get_json(silent=True) or {}, _quien())
    except rt.NoSePuede as e:
        return _error(e, _codigo(e))
    return jsonify({"ok": True, "id": hid}), 201


@rentabilidad_bp.route("/api/rentabilidad/horas/<int:hid>", methods=["DELETE"])
def api_borrar_horas(hid):
    try:
        rt.borrar_horas(_db(), hid)
    except rt.NoSePuede as e:
        return _error(e, _codigo(e))
    return jsonify({"ok": True})


@rentabilidad_bp.route("/api/rentabilidad/horas.csv")
def api_csv():
    try:
        texto = rt.horas_csv(_db(), request.args.get("desde"), request.args.get("hasta"))
    except rt.NoSePuede as e:
        return _error(e)
    return Response("﻿" + texto, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=horas.csv"})


@rentabilidad_bp.route("/api/rentabilidad/reloj", methods=["POST"])
def api_prender():
    try:
        rt.prender_reloj(_db(), request.get_json(silent=True) or {})
    except rt.NoSePuede as e:
        return _error(e, _codigo(e))
    return jsonify({"ok": True})


@rentabilidad_bp.route("/api/rentabilidad/reloj/frenar", methods=["POST"])
def api_frenar():
    data = request.get_json(silent=True) or {}
    try:
        r = rt.frenar_reloj(_db(), data.get("persona_id"), _quien(), data.get("nota"))
    except rt.NoSePuede as e:
        return _error(e, _codigo(e))
    return jsonify({"ok": True, **r})


@rentabilidad_bp.route("/api/rentabilidad/costos/<int:persona_id>", methods=["PUT"])
def api_costo(persona_id):
    try:
        rt.guardar_costo(_db(), persona_id, (request.get_json(silent=True) or {}).get("costo_hora"))
    except rt.NoSePuede as e:
        return _error(e, _codigo(e))
    _log("rentabilidad_costo", persona_id, "Cambió el costo por hora")
    return jsonify({"ok": True})


@rentabilidad_bp.route("/api/rentabilidad/ajustes", methods=["PUT"])
def api_ajustes():
    try:
        aj = rt.guardar_ajustes(_db(), request.get_json(silent=True) or {})
    except rt.NoSePuede as e:
        return _error(e)
    return jsonify({"ok": True, "ajustes": aj})


@rentabilidad_bp.route("/api/rentabilidad/estimar")
def api_estimar():
    try:
        return jsonify({"ok": True, "estimacion": rt.estimar(_db(), request.args.get("tipo", ""),
                                                             request.args.get("horas"))})
    except rt.NoSePuede as e:
        return _error(e)
