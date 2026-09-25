"""Endpoints de Scalerics Fidelidad: Outbound e Inteligencia comercial del socio.

Finos, como los de Seguimiento de leads: validan y llaman a
`services/fidelidad.py`. Todo pide el panel `cola` (Outbound), menos
`/intel`, que pide `metrics` (Inteligencia comercial). Se reusan esos nombres
de panel porque así están guardados los permisos de cada rol.
"""

import os
import secrets
import sqlite3

from flask import Blueprint, Response, current_app, jsonify, redirect, request, session

from services import fidelidad as fid
from services import gmail_usuario as gmail
from services.auth import is_admin, require_panel
from services.mails_vedados import esta_vedado

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


@fidelidad_bp.route("/api/fidelidad/agenda")
def api_agenda():
    d = fid.armar_agenda(_db(), request.args.get("desde") or "", request.args.get("hasta") or "",
                         con_llamadas=request.args.get("llamadas") != "0")
    if d.get("error"):
        return _error(d["error"], 400)
    return jsonify(d)


@fidelidad_bp.route("/api/fidelidad/eventos", methods=["POST"])
def api_evento_crear():
    eid, error = fid.crear_evento(_db(), request.get_json(silent=True) or {}, _usuario())
    if error:
        return _error(error, 400)
    return jsonify({"ok": True, "id": eid}), 201


@fidelidad_bp.route("/api/fidelidad/eventos/<int:eid>", methods=["PUT"])
def api_evento_editar(eid):
    error = fid.editar_evento(_db(), eid, request.get_json(silent=True) or {})
    if error:
        return _error(error, 404 if error == "el evento no existe" else 400)
    return jsonify({"ok": True})


@fidelidad_bp.route("/api/fidelidad/eventos/<int:eid>", methods=["DELETE"])
def api_evento_borrar(eid):
    if not fid.borrar_evento(_db(), eid):
        return _error("el evento no existe", 404)
    return jsonify({"ok": True})


@fidelidad_bp.route("/api/fidelidad/prospectos/<int:pid>/reunion", methods=["POST"])
def api_reunion(pid):
    d = request.get_json(silent=True) or {}
    p, error = fid.agendar_reunion(_db(), pid, d.get("inicio") or "", _usuario(),
                                   minutos=d.get("minutos"), lugar=d.get("lugar"))
    if error:
        return _error(error, 404 if error == "el prospecto no existe" else 400)
    return jsonify({"ok": True, "prospecto": p})


@fidelidad_bp.route("/api/fidelidad/prospectos", methods=["POST"])
def api_crear():
    datos = request.get_json(silent=True) or {}
    pid, que = fid.crear_prospecto(_db(), datos, fuente=datos.get("fuente") or "manual")
    if que == "sin_nombre":
        return _error("falta el nombre del restaurante", 400)
    if que == "fuera_de_zona":
        return _error("en Montevideo, por ahora solo Municipio CH y Carrasco", 400)
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


# ── lista única (25/9) ───────────────────────────────────────────────────────

@fidelidad_bp.route("/api/fidelidad/lista")
def api_lista():
    try:
        limite = min(2000, max(1, int(request.args.get("limite") or 150)))
    except ValueError:
        limite = 150
    return jsonify(fid.armar_lista(_db(), ciudad=request.args.get("ciudad") or None,
                                   rubro=request.args.get("rubro") or None,
                                   q=request.args.get("q") or None, cuando=_ahora(), limite=limite))


@fidelidad_bp.route("/api/fidelidad/prospectos/<int:pid>/accion", methods=["POST"])
def api_accion(pid):
    d = request.get_json(silent=True) or {}
    p, lid, error = fid.registrar_accion(_db(), pid, d.get("accion") or "", _usuario(),
                                         fecha=d.get("fecha"), nota=d.get("nota") or "", cuando=_ahora())
    if error:
        return _error(error, 404 if error == "el prospecto no existe" else 400)
    return jsonify({"ok": True, "llamada_id": lid, "prospecto": p}), 201


@fidelidad_bp.route("/api/fidelidad/llamadas/<int:lid>", methods=["PUT"])
def api_llamada_editar(lid):
    d = request.get_json(silent=True) or {}
    p, error = fid.editar_llamada(_db(), lid, fecha=d.get("fecha"), nota=d.get("nota"), cuando=_ahora())
    if error:
        return _error(error, 404 if error == "la llamada no existe" else 400)
    return jsonify({"ok": True, "prospecto": p})


@fidelidad_bp.route("/api/fidelidad/llamadas/<int:lid>", methods=["DELETE"])
def api_llamada_deshacer(lid):
    p, error = fid.deshacer_llamada(_db(), lid)
    if error:
        return _error(error, 404 if error == "la llamada no existe" else 400)
    return jsonify({"ok": True, "prospecto": p})



# ── mail con borrador (25/9) ─────────────────────────────────────────────────
# Sale de la casilla de quien lo manda: cada uno conecta su Gmail una vez.

def _base() -> str:
    """La URL pública del CRM. En Fly el request llega por http detrás del proxy."""
    base = (current_app.config.get("PUBLIC_URL") or request.host_url).rstrip("/")
    if base.startswith("http://") and not base.startswith(("http://localhost", "http://127.")):
        base = "https://" + base[len("http://"):]
    return base


def _vuelta_gmail() -> str:
    return _base() + "/oauth/gmail/callback"


def _yo() -> dict:
    c = sqlite3.connect(_db())
    c.row_factory = sqlite3.Row
    try:
        f = c.execute("SELECT name, email, phone FROM users WHERE id = ?", (session.get("user_id"),)).fetchone()
    finally:
        c.close()
    return dict(f) if f else {"name": _usuario(), "email": None, "phone": None}


@fidelidad_bp.route("/api/fidelidad/prospectos/<int:pid>/borrador")
def api_borrador(pid):
    p = fid.get_prospecto(_db(), pid)
    if not p:
        return _error("el prospecto no existe", 404)
    yo = _yo()
    return jsonify({**fid.borrador(p, yo["name"], yo.get("phone")),
                    "gmail": gmail.estado(_db(), session.get("user_id"))})


@fidelidad_bp.route("/api/fidelidad/prospectos/<int:pid>/mail", methods=["POST"])
def api_mandar_mail(pid):
    d = request.get_json(silent=True) or {}
    para, asunto, cuerpo = (d.get("para") or "").strip(), (d.get("asunto") or "").strip(), (d.get("cuerpo") or "").strip()
    if not fid.get_prospecto(_db(), pid):
        return _error("el prospecto no existe", 404)
    if not fid.es_mail(para):
        return _error("poné un mail válido", 400)
    if not asunto or not cuerpo:
        return _error("falta el asunto o el mensaje", 400)
    if esta_vedado(_db(), para):
        return _error("ese mail pidió no recibir más correos", 400)
    yo = _yo()
    try:
        enviado = gmail.enviar(_db(), session.get("user_id"), yo["name"], para, asunto, cuerpo)
    except gmail.Desconectado as e:
        return jsonify({"ok": False, "error": str(e), "reconectar": True}), 409
    except Exception as e:
        return _error(str(e), 502)
    p = fid.registrar_mail(_db(), pid, _usuario(), enviado["de"], para, asunto, enviado.get("id"), cuando=_ahora())
    return jsonify({"ok": True, "de": enviado["de"], "prospecto": p}), 201


@fidelidad_bp.route("/oauth/gmail/conectar")
def oauth_gmail_conectar():
    if not gmail.configurado():
        return "Falta configurar el acceso a Gmail del CRM (GMAIL_WEB_CLIENT_ID).", 503
    session["gmail_state"] = secrets.token_urlsafe(24)
    return redirect(gmail.url_autorizacion(_vuelta_gmail(), session["gmail_state"], _yo().get("email")))


@fidelidad_bp.route("/oauth/gmail/callback")
def oauth_gmail_callback():
    estado = session.pop("gmail_state", None)
    if not estado or request.args.get("state") != estado:
        return redirect("/?gmail=error#cola")
    if request.args.get("error") or not request.args.get("code"):
        return redirect("/?gmail=cancelado#cola")
    try:
        gmail.conectar(_db(), session.get("user_id"), request.args["code"], _vuelta_gmail())
    except Exception as e:
        current_app.logger.warning(f"No se pudo conectar Gmail: {e}")
        return redirect("/?gmail=error#cola")
    return redirect("/?gmail=ok#cola")


@fidelidad_bp.route("/api/fidelidad/gmail", methods=["DELETE"])
def api_gmail_desconectar():
    gmail.desconectar(_db(), session.get("user_id"))
    return jsonify({"ok": True})
