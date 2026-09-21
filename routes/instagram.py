"""Rutas del panel Instagram y la URL publica de las imagenes.

El panel pide `instagram`. Las imagenes publicas (`/pub/ig/...`) van en otro
blueprint sin candado: Meta las baja sin sesion. Ver services/instagram.py.

`/api/instagram-bot/` es lo que usa la sesion de Claude Code que resuelve los
pedidos de correccion. Va sin sesion y se valida con IG_BOT_TOKEN, que solo
abre estas rutas (mismo criterio que PLANILLA_TOKEN).
"""

import hmac
import os
from datetime import date

from flask import Blueprint, abort, current_app, jsonify, request, send_file, session

from services import estrategia as est
from services import instagram as ig
from services.auth import is_admin, require_panel

instagram_bp = Blueprint("instagram", __name__)
instagram_pub_bp = Blueprint("instagram_pub", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


def _usuario() -> str:
    return str(session.get("user_name") or session.get("user_email") or session.get("user_id") or "")


@instagram_bp.before_request
def _candado():
    return require_panel(_db(), "instagram")


def _publica(pub: dict) -> dict:
    pub = dict(pub)
    pub.pop("img_token", None)
    local = ig._dt(pub["programada_para"]).astimezone(ig._UY)
    pub["fecha"] = local.date().isoformat()
    pub["hora"] = local.strftime("%H:%M")
    pub["imagenes_url"] = [
        f"/api/instagram/publicaciones/{pub['id']}/imagen/{n}?v={pub['version']}"
        for n in range(1, pub["imagenes"] + 1)]
    pub["estilos"] = [{"id": e, "nombre": ig.ig_render.nombre_estilo(e)}
                      for e in ig.ig_render.estilos_para(pub["formato"])]
    return pub


def _responder(fn, *args):
    try:
        return jsonify({"ok": True, "publicacion": _publica(fn(*args))})
    except ig.NoSePuede as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@instagram_bp.route("/api/instagram/semana")
def api_semana():
    ahora = ig.ahora_utc()
    crudo = request.args.get("semana")
    try:
        lunes = ig.lunes_de(date.fromisoformat(crudo)) if crudo else ig.semana_actual(ahora)
    except ValueError:
        return jsonify({"ok": False, "error": "semana tiene que ser AAAA-MM-DD"}), 400
    conn = ig._connect(_db())
    try:
        quedan = ig.disponibles(conn, ahora)
    finally:
        conn.close()
    pubs = [_publica(p) for p in ig.listar_semana(_db(), lunes)]
    pedidos = ig.correcciones_de(_db(), [p["id"] for p in pubs])
    for p in pubs:
        p["correcciones"] = pedidos.get(p["id"], [])[:3]
    return jsonify({
        "ok": True,
        "semana": lunes.isoformat(),
        "semana_actual": ig.semana_actual(ahora).isoformat(),
        "publicaciones": pubs,
        "banco": quedan,
        "plan": ig.plan_para_panel(lunes),
        "puede_armar": is_admin(_db(), session.get("user_id")),
    })


@instagram_bp.route("/api/instagram/publicaciones/<int:pub_id>/imagen/<int:n>")
def api_imagen(pub_id, n):
    pub = ig.obtener(_db(), pub_id)
    if not pub or not 1 <= n <= pub["imagenes"]:
        abort(404)
    ruta = ig.ruta_imagen(_db(), pub, n)
    if not os.path.isfile(ruta):
        abort(404)
    resp = send_file(ruta, mimetype="image/jpeg", max_age=3600)
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@instagram_bp.route("/api/instagram/publicaciones/<int:pub_id>", methods=["PUT"])
def api_editar(pub_id):
    datos = request.get_json(silent=True) or {}
    cambios = {k: datos[k] for k in ("caption", "slides", "estilo", "fecha", "hora") if k in datos}
    return _responder(ig.editar, _db(), pub_id, cambios, _usuario())


@instagram_bp.route("/api/instagram/publicaciones/<int:pub_id>/<accion>", methods=["POST"])
def api_accion(pub_id, accion):
    acciones = {
        "aprobar": lambda: ig.aprobar(_db(), pub_id, _usuario()),
        "desaprobar": lambda: ig.desaprobar(_db(), pub_id),
        "descartar": lambda: ig.descartar(_db(), pub_id),
        "restaurar": lambda: ig.restaurar(_db(), pub_id),
        "otra-idea": lambda: ig.otra_idea(_db(), pub_id),
    }
    if accion not in acciones:
        abort(404)
    return _responder(acciones[accion])


@instagram_bp.route("/api/instagram/publicaciones/<int:pub_id>/correccion", methods=["POST"])
def api_pedir_correccion(pub_id):
    datos = request.get_json(silent=True) or {}
    try:
        ig.pedir_correccion(_db(), pub_id, datos.get("pedido"), _usuario())
    except ig.NoSePuede as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True,
                    "correcciones": ig.correcciones_de(_db(), [pub_id]).get(pub_id, [])[:3]})


@instagram_bp.route("/api/instagram/grilla")
def api_grilla():
    crudo = request.args.get("semana")
    try:
        lunes = ig.lunes_de(date.fromisoformat(crudo)) if crudo else ig.semana_actual(ig.ahora_utc())
    except ValueError:
        return jsonify({"ok": False, "error": "semana tiene que ser AAAA-MM-DD"}), 400
    g = ig.grilla(_db(), lunes)
    for n in g["nuevas"]:
        n["imagen"] = f"/api/instagram/publicaciones/{n['id']}/imagen/1?v={n['version']}"
    return jsonify({"ok": True, "semana": lunes.isoformat(), **g})


@instagram_bp.route("/api/instagram/comentarios")
def api_comentarios():
    from services import ig_comentarios
    return jsonify({"ok": True, "whatsapp": ig_comentarios.whatsapp(),
                    "activo": ig_comentarios.activo(),
                    "comentarios": ig_comentarios.listar(_db())})


@instagram_bp.route("/api/instagram/armar-semana", methods=["POST"])
def api_armar():
    if not is_admin(_db(), session.get("user_id")):
        return jsonify({"ok": False, "error": "Solo un administrador puede armar la semana"}), 403
    datos = request.get_json(silent=True) or {}
    try:
        lunes = ig.lunes_de(date.fromisoformat(datos.get("semana") or ""))
    except ValueError:
        return jsonify({"ok": False, "error": "semana tiene que ser AAAA-MM-DD"}), 400
    ig.sembrar_banco(_db())
    creadas = ig.armar_semana(_db(), lunes)
    return jsonify({"ok": True, "creadas": len(creadas)})


@instagram_pub_bp.route("/pub/ig/<token>/<int:n>.jpg")
def imagen_publica(token, n):
    ruta = ig.imagen_publica(current_app.config["DB_PATH"], token, n)
    if not ruta:
        abort(404)
    resp = send_file(ruta, mimetype="image/jpeg", max_age=0)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


# ── robot de correcciones ────────────────────────────────────────────────────

def _bot_ok() -> bool:
    esperado = os.environ.get("IG_BOT_TOKEN", "")
    recibido = request.headers.get("x-ig-token", "")
    return bool(esperado) and len(esperado) >= 32 and hmac.compare_digest(esperado, recibido)


@instagram_pub_bp.before_request
def _candado_bot():
    if request.path.startswith("/api/instagram-bot/") and not _bot_ok():
        return jsonify({"ok": False, "error": "No autorizado"}), 403
    return None


@instagram_pub_bp.route("/api/instagram-bot/pendientes")
def bot_pendientes():
    return jsonify({"ok": True, "pendientes": ig.pendientes(current_app.config["DB_PATH"])})


@instagram_pub_bp.route("/api/instagram-bot/correcciones/<int:corr_id>/resolver", methods=["POST"])
def bot_resolver(corr_id):
    datos = request.get_json(silent=True) or {}
    try:
        pub = ig.resolver_correccion(current_app.config["DB_PATH"], corr_id,
                                     datos.get("cambios") or {}, datos.get("respuesta") or "")
    except ig.NoSePuede as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "version": pub["version"], "estado": pub["estado"]})


@instagram_pub_bp.route("/api/instagram-bot/correcciones/<int:corr_id>/rechazar", methods=["POST"])
def bot_rechazar(corr_id):
    datos = request.get_json(silent=True) or {}
    try:
        ig.rechazar_correccion(current_app.config["DB_PATH"], corr_id, datos.get("respuesta") or "")
    except ig.NoSePuede as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True})


@instagram_pub_bp.route("/api/instagram-bot/publicaciones/<int:pub_id>/imagen/<int:n>")
def bot_imagen(pub_id, n):
    """Para que la sesion de Claude mire como quedo la version corregida."""
    db = current_app.config["DB_PATH"]
    pub = ig.obtener(db, pub_id)
    if not pub or not 1 <= n <= pub["imagenes"]:
        abort(404)
    ruta = ig.ruta_imagen(db, pub, n)
    if not os.path.isfile(ruta):
        abort(404)
    return send_file(ruta, mimetype="image/jpeg", max_age=0)


@instagram_pub_bp.route("/api/instagram-bot/estrategias/pendientes")
def bot_estrategias_pendientes():
    """Los PDF mensuales que el agente todavia no leyo."""
    return jsonify({"ok": True, "pendientes": est.pendientes(_db())})


@instagram_pub_bp.route("/api/instagram-bot/estrategias/<int:est_id>/pdf")
def bot_estrategia_pdf(est_id):
    fila = est.pdf_de(_db(), est_id)
    if not fila:
        abort(404)
    return current_app.response_class(fila[1], mimetype="application/pdf")


@instagram_pub_bp.route("/api/instagram-bot/estrategias/<int:est_id>/plan", methods=["POST"])
def bot_estrategia_plan(est_id):
    try:
        est.guardar_plan(_db(), est_id, (request.get_json(silent=True) or {}).get("plan"))
    except est.NoSePuede as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True})
