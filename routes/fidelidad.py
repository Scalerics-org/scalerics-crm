"""Endpoints de Scalerics Fidelidad: Outbound e Inteligencia comercial del socio.

Finos, como los de Seguimiento de leads: validan y llaman a
`services/fidelidad.py`. Todo pide el panel `cola` (Outbound), menos
`/intel`, que pide `metrics` (Inteligencia comercial). Se reusan esos nombres
de panel porque así están guardados los permisos de cada rol.
"""

import os

from flask import Blueprint, Response, current_app, jsonify, request, session

from services import fidelidad as fid
from services.auth import is_admin, require_panel

fidelidad_bp = Blueprint("fidelidad", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


def _ahora():
    """La hora de Montevideo. Aparte para poder fijarla en los tests."""
    return fid.ahora()


def _usuario() -> str:
    return session.get("user_name") or "sistema"


def _error(mensaje: str, codigo: int):
    return jsonify({"ok": False, "error": mensaje}), codigo


@fidelidad_bp.before_request
def _candado():
    # El scraper carga prospectos con x-admin-token, sin sesion: el
    # before_request del dashboard ya valido el token.
    token = os.environ.get("ADMIN_TOKEN", "")
    if token and request.headers.get("x-admin-token", "") == token:
        return None
    panel = "metrics" if request.path.startswith("/api/fidelidad/intel") else "cola"
    return require_panel(_db(), panel)


@fidelidad_bp.route("/api/fidelidad/hoy")
def api_hoy():
    datos = fid.armar_hoy(_db(), _ahora())
    datos["resultados"] = fid.RESULTADOS
    datos["motivos"] = fid.MOTIVOS
    return jsonify(datos)


@fidelidad_bp.route("/api/fidelidad/prospectos")
def api_listar():
    try:
        pagina = int(request.args.get("pagina") or 1)
    except ValueError:
        pagina = 1
    return jsonify(fid.listar(_db(), estado=request.args.get("estado") or None,
                              zona=request.args.get("zona") or None,
                              cat=request.args.get("categoria") or None,
                              q=request.args.get("q") or None, pagina=pagina))


@fidelidad_bp.route("/api/fidelidad/pipeline")
def api_pipeline():
    return jsonify(fid.armar_pipeline(_db(), zona=request.args.get("zona") or None,
                                      cat=request.args.get("categoria") or None))


@fidelidad_bp.route("/api/fidelidad/reuniones")
def api_reuniones():
    return jsonify(fid.listar_reuniones(_db(), _ahora()))


@fidelidad_bp.route("/api/fidelidad/prospectos", methods=["POST"])
def api_crear():
    datos = request.get_json(silent=True) or {}
    pid, que = fid.crear_prospecto(_db(), datos, fuente=datos.get("fuente") or "manual")
    if que == "sin_nombre":
        return _error("falta el nombre del restaurante", 400)
    if que == "fuera_de_zona":
        return _error("no es de Municipio CH ni de Carrasco", 400)
    return jsonify({"ok": True, "id": pid, "duplicado": que == "duplicado"}), 200 if que == "duplicado" else 201


@fidelidad_bp.route("/api/fidelidad/prospectos/<int:pid>")
def api_ficha(pid):
    p = fid.get_prospecto(_db(), pid)
    if not p:
        return _error("el prospecto no existe", 404)
    p["resultados"] = fid.RESULTADOS[fid.grupo_de_resultados(p["estado"])]
    p["categoria"] = fid.categoria(p.get("tipo"))
    return jsonify(p)


@fidelidad_bp.route("/api/fidelidad/prospectos/<int:pid>", methods=["PUT"])
def api_editar(pid):
    if not fid.get_prospecto(_db(), pid):
        return _error("el prospecto no existe", 404)
    fid.editar_prospecto(_db(), pid, request.get_json(silent=True) or {})
    return jsonify({"ok": True, "prospecto": fid.get_prospecto(_db(), pid)})


@fidelidad_bp.route("/api/fidelidad/prospectos/<int:pid>/llamadas", methods=["POST"])
def api_llamada(pid):
    d = request.get_json(silent=True) or {}
    p, error = fid.registrar_llamada(_db(), pid, d.get("resultado") or "", _usuario(),
                                     nota=d.get("nota") or "", fecha=d.get("fecha"),
                                     fecha_reunion=d.get("fecha_reunion"), motivo=d.get("motivo"),
                                     cuando=_ahora())
    if error:
        return _error(error, 404 if error == "el prospecto no existe" else 400)
    return jsonify({"ok": True, "prospecto": p}), 201


@fidelidad_bp.route("/api/fidelidad/prospectos/<int:pid>/estado", methods=["POST"])
def api_estado(pid):
    d = request.get_json(silent=True) or {}
    p, error = fid.mover_estado(_db(), pid, d.get("estado") or "", _usuario(),
                                fecha_reunion=d.get("fecha_reunion"), motivo=d.get("motivo"))
    if error:
        return _error(error, 404 if error == "el prospecto no existe" else 400)
    return jsonify({"ok": True, "prospecto": p})


@fidelidad_bp.route("/api/fidelidad/importar", methods=["POST"])
def api_importar():
    archivo = request.files.get("archivo")
    if not archivo:
        return _error("falta el archivo", 400)
    contenido = archivo.read()
    if len(contenido) > 5 * 1024 * 1024:
        return _error("el archivo pesa más de 5 MB", 400)
    res = fid.importar(_db(), contenido)
    return jsonify(res), 200 if res.get("ok") else 400


@fidelidad_bp.route("/api/fidelidad/export.csv")
def api_export():
    return Response("﻿" + fid.exportar_csv(_db()), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=prospectos_fidelidad.csv"})


@fidelidad_bp.route("/api/fidelidad/intel")
def api_intel():
    return jsonify(fid.armar_intel(_db(), periodo=request.args.get("periodo") or "mes",
                                   zona=request.args.get("zona") or None,
                                   cat=request.args.get("categoria") or None, cuando=_ahora()))


@fidelidad_bp.route("/api/fidelidad/config")
def api_config():
    return jsonify(fid.get_config(_db()))


@fidelidad_bp.route("/api/fidelidad/config", methods=["PUT"])
def api_config_guardar():
    # Las metas y la comision las fija Scalerics, no el vendedor.
    if not is_admin(_db(), session.get("user_id")):
        return _error("solo un administrador cambia las metas", 403)
    return jsonify({"ok": True, "config": fid.set_config(_db(), request.get_json(silent=True) or {})})
