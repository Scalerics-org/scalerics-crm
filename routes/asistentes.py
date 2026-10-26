"""Fábrica de asistentes (services/asistentes.py).

Tres grupos:
- `/api/asistentes/...` es del equipo: pide el panel `asistentes`.
- `/asistente/<token>` es el link de prueba que se le pasa al dueño del
  comercio para que le escriba a su asistente antes de conectarlo. Abre sin
  sesión (exenta en dashboard.require_login): el token es al azar, de 16 bytes.
  Es un chat sin scripts: cada mensaje es un formulario, así anda en cualquier
  celular y con CSP estricta.
- `/asistente/api/<token>/mensaje` es la API que llama el puente de WhatsApp
  del cliente. Pide además la clave del asistente (se guarda solo su hash) y
  que esté conectado. No guarda el teléfono: solo un hash de la charla.

Todo lo que pinta la página pública pasa por html.escape: las fichas las
escribe el equipo, pero los mensajes los escribe cualquiera con el link.
"""

import html
import os
import re
import secrets
from urllib.parse import quote

from flask import Blueprint, Response, current_app, jsonify, redirect, request, session

from database import log_activity
from services import asistentes as asis
from services.auth import require_panel

asistentes_bp = Blueprint("asistentes", __name__)

PUBLICAS = ("/asistente/",)
_CONV = re.compile(r"^[A-Za-z0-9_-]{16,40}$")


def _db() -> str:
    return current_app.config["DB_PATH"]


def _quien() -> str:
    return session.get("user_name", "sistema")


def _error(msg, codigo: int = 400):
    return jsonify({"ok": False, "error": str(msg)}), codigo


def _codigo(e: asis.NoSePuede) -> int:
    return 404 if "no existe" in str(e) else 400


def _base() -> str:
    base = (os.environ.get("CRM_URL") or request.host_url).rstrip("/")
    if base.startswith("http://") and not base.startswith(("http://localhost", "http://127.")):
        base = "https://" + base[len("http://"):]
    return base


@asistentes_bp.before_request
def _candado():
    if request.path.startswith(PUBLICAS):
        return None
    return require_panel(_db(), "asistentes")


# ── equipo ───────────────────────────────────────────────────────────────────

def _con_links(d: dict) -> dict:
    d = dict(d)
    d["link"] = f"{_base()}/asistente/{d['token']}"
    d["api"] = f"{_base()}/asistente/api/{d['token']}/mensaje"
    return d


def _ok(d: dict, codigo: int = 200):
    return jsonify({"ok": True, "asistente": _con_links(d)}), codigo


def _hacer(funcion, *args):
    try:
        return _ok(funcion(_db(), *args))
    except asis.NoSePuede as e:
        return _error(e, _codigo(e))


@asistentes_bp.route("/api/asistentes")
def api_listar():
    lista = asis.listar(_db())
    return jsonify({"ok": True, "asistentes": lista, "resumen": asis.resumen(lista),
                    "catalogo": asis.catalogo(), "clientes": asis.clientes(_db())})


@asistentes_bp.route("/api/asistentes", methods=["POST"])
def api_crear():
    try:
        d = asis.crear(_db(), request.get_json(silent=True) or {}, _quien())
    except asis.NoSePuede as e:
        return _error(e)
    log_activity(_db(), _quien(), "asistente_alta", "asistente", d["id"], d["nombre"], d["familia_nombre"],
                 user_id=session.get("user_id"))
    return _ok(d, 201)


@asistentes_bp.route("/api/asistentes/<int:aid>")
def api_detalle(aid):
    return _hacer(asis.detalle, aid)


@asistentes_bp.route("/api/asistentes/<int:aid>", methods=["PUT"])
def api_editar(aid):
    return _hacer(asis.editar, aid, request.get_json(silent=True) or {})


@asistentes_bp.route("/api/asistentes/<int:aid>", methods=["DELETE"])
def api_borrar(aid):
    try:
        d = asis.detalle(_db(), aid)
        asis.borrar(_db(), aid)
    except asis.NoSePuede as e:
        return _error(e, 404)
    log_activity(_db(), _quien(), "asistente_baja", "asistente", aid, d["nombre"], "", user_id=session.get("user_id"))
    return jsonify({"ok": True})


@asistentes_bp.route("/api/asistentes/<int:aid>/fichas", methods=["POST"])
def api_ficha_nueva(aid):
    return _hacer(asis.guardar_ficha, aid, request.get_json(silent=True) or {})


@asistentes_bp.route("/api/asistentes/<int:aid>/fichas/<int:fid>", methods=["PUT"])
def api_ficha(aid, fid):
    return _hacer(asis.guardar_ficha, aid, request.get_json(silent=True) or {}, fid)


@asistentes_bp.route("/api/asistentes/<int:aid>/fichas/<int:fid>", methods=["DELETE"])
def api_ficha_borrar(aid, fid):
    return _hacer(asis.borrar_ficha, aid, fid)


@asistentes_bp.route("/api/asistentes/<int:aid>/pruebas", methods=["POST"])
def api_prueba_nueva(aid):
    return _hacer(asis.sumar_prueba, aid, request.get_json(silent=True) or {})


@asistentes_bp.route("/api/asistentes/<int:aid>/pruebas/<int:pid>", methods=["DELETE"])
def api_prueba_borrar(aid, pid):
    return _hacer(asis.borrar_prueba, aid, pid)


@asistentes_bp.route("/api/asistentes/<int:aid>/pruebas/sugerir", methods=["POST"])
def api_sugerir(aid):
    try:
        n = asis.sugerir_pruebas(_db(), aid)
        d = asis.detalle(_db(), aid)
    except asis.NoSePuede as e:
        return _error(e, _codigo(e))
    return jsonify({"ok": True, "sumadas": n, "asistente": _con_links(d)})


@asistentes_bp.route("/api/asistentes/<int:aid>/pruebas/correr", methods=["POST"])
def api_correr(aid):
    return _hacer(asis.correr_pruebas, aid)


@asistentes_bp.route("/api/asistentes/<int:aid>/simular", methods=["POST"])
def api_simular(aid):
    data = request.get_json(silent=True) or {}
    sesion = str(data.get("sesion") or "")[:40]
    try:
        r = asis.conversar(_db(), aid, data.get("texto", ""), "simulador", f"{session.get('user_id')}:{sesion}")
    except asis.NoSePuede as e:
        return _error(e, _codigo(e))
    return jsonify({"ok": True, "respuesta": r})


@asistentes_bp.route("/api/asistentes/<int:aid>/mensajes/<int:mid>/ficha", methods=["POST"])
def api_mensaje_ficha(aid, mid):
    return _hacer(asis.ficha_desde_mensaje, aid, mid, request.get_json(silent=True) or {})


@asistentes_bp.route("/api/asistentes/<int:aid>/mensajes/<int:mid>/resolver", methods=["POST"])
def api_mensaje_resolver(aid, mid):
    return _hacer(asis.resolver_mensaje, aid, mid)


@asistentes_bp.route("/api/asistentes/<int:aid>/clave", methods=["POST"])
def api_clave(aid):
    """La clave nueva se ve una sola vez. Generarla invalida la anterior."""
    try:
        clave = asis.nueva_clave(_db(), aid)
        d = asis.detalle(_db(), aid)
    except asis.NoSePuede as e:
        return _error(e, _codigo(e))
    log_activity(_db(), _quien(), "asistente_clave", "asistente", aid, d["nombre"], "", user_id=session.get("user_id"))
    resp = jsonify({"ok": True, "clave": clave, "asistente": _con_links(d)})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@asistentes_bp.route("/api/asistentes/<int:aid>/conectar", methods=["POST"])
def api_conectar(aid):
    conectado = bool((request.get_json(silent=True) or {}).get("conectado"))
    try:
        d = asis.conectar(_db(), aid, conectado)
    except asis.NoSePuede as e:
        return _error(e, _codigo(e))
    log_activity(_db(), _quien(), "asistente_conectado" if conectado else "asistente_desconectado", "asistente",
                 aid, d["nombre"], "", user_id=session.get("user_id"))
    return _ok(d)


@asistentes_bp.route("/api/asistentes/<int:aid>/exportar")
def api_exportar(aid):
    try:
        datos = asis.exportar(_db(), aid)
    except asis.NoSePuede as e:
        return _error(e, 404)
    return jsonify({"ok": True, "exportado": datos})


# ── la API para el puente de WhatsApp ────────────────────────────────────────

@asistentes_bp.route("/asistente/api/<token>/mensaje", methods=["POST"])
def api_publica(token):
    clave = request.headers.get("X-Asistente-Clave", "")
    if not clave and request.headers.get("Authorization", "").startswith("Bearer "):
        clave = request.headers["Authorization"][7:]
    a = asis.clave_valida(_db(), token, clave.strip())
    if not a:
        return _error("No autorizado", 401)
    if not a["conectado"]:
        return _error("El asistente no está conectado", 409)
    data = request.get_json(silent=True) or {}
    de = str(data.get("de") or "").strip()[:60]
    if not de:
        return _error("Falta «de»: quién escribe (el teléfono o un id de la charla)")
    try:
        r = asis.conversar(_db(), a["id"], data.get("texto", ""), "api", de, tope=asis.TOPE_API_HORA)
    except asis.NoSePuede as e:
        return _error(e, 429 if "Demasiados" in str(e) else 400)
    return jsonify({"ok": True, "respuesta": r["respuesta"], "derivar": r["derivar"], "tipo": r["tipo"],
                    "ficha": r["ficha"], "confianza": r["confianza"]})


# ── el link de prueba ────────────────────────────────────────────────────────

_CSS = """
*{box-sizing:border-box}
body{margin:0;background:#e7e1d8;color:#111b21;font:16px/1.45 Inter,system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:560px;margin:0 auto;min-height:100vh;display:flex;flex-direction:column;background:#efeae2}
.cab{background:#0b5e55;color:#fff;padding:14px 16px;display:flex;gap:12px;align-items:center}
.avatar{width:40px;height:40px;border-radius:50%;background:#25d366;display:flex;align-items:center;justify-content:center;font-weight:700;flex:none}
.cab h1{font-size:1rem;margin:0;line-height:1.2}
.cab .sub{font-size:.78rem;opacity:.85}
.aviso{background:#fff7d6;color:#5b4a00;font-size:.8rem;padding:8px 14px;text-align:center}
.chat{flex:1;padding:14px 12px;display:flex;flex-direction:column;gap:6px}
.m{max-width:85%;padding:7px 10px 5px;border-radius:9px;font-size:.93rem;white-space:pre-line;box-shadow:0 1px .5px rgba(0,0,0,.13);overflow-wrap:anywhere}
.m.yo{align-self:flex-end;background:#d9fdd3}
.m.bot{align-self:flex-start;background:#fff}
.m .h{display:block;text-align:right;font-size:.68rem;color:#667781;margin-top:2px}
.m.deriva{border-left:3px solid #f59e0b}
.vacio{text-align:center;color:#54656f;font-size:.88rem;margin:30px 10px}
.vacio b{color:#111b21}
.chips{display:flex;flex-wrap:wrap;gap:6px;justify-content:center;margin-top:10px}
.chips button{background:#fff;border:1px solid #d1d7db;color:#0b5e55;border-radius:16px;padding:5px 12px;font:inherit;font-size:.82rem;cursor:pointer}
form.env{display:flex;gap:8px;padding:10px;background:#f0f2f5;position:sticky;bottom:0}
form.env input[type=text]{flex:1;border:0;border-radius:20px;padding:10px 14px;font:inherit;background:#fff;color:#111b21}
form.env button{border:0;border-radius:20px;background:#0b5e55;color:#fff;font:inherit;font-weight:600;padding:0 16px;cursor:pointer}
.err{background:#fef2f2;color:#991b1b;font-size:.85rem;padding:8px 14px}
.trampa{position:absolute;left:-9999px}
.pie{text-align:center;color:#667781;font-size:.75rem;padding:10px}
.pie a{color:#0b5e55}
"""


def _cabeceras(resp: Response, nonce: str) -> Response:
    resp.headers["Content-Security-Policy"] = (
        f"default-src 'none'; style-src 'nonce-{nonce}'; img-src 'self' data:; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


def _pagina(titulo: str, cuerpo: str, codigo: int = 200) -> Response:
    nonce = secrets.token_urlsafe(12)
    doc = ("<!doctype html><html lang='es'><head><meta charset='utf-8'>"
           "<meta name='viewport' content='width=device-width,initial-scale=1'>"
           "<meta name='robots' content='noindex,nofollow'>"
           f"<title>{html.escape(titulo)}</title><style nonce='{nonce}'>{_CSS}</style></head>"
           f"<body><div class='wrap'>{cuerpo}<p class='pie'>Asistente armado con "
           "<a href='https://scalerics.com'>Scalerics</a></p></div></body></html>")
    return _cabeceras(Response(doc, status=codigo, mimetype="text/html"), nonce)


def _no_existe() -> Response:
    return _pagina("No encontrado", "<div class='vacio'><b>Este link no existe</b><br>"
                   "Revisalo o pedíselo a quien te lo mandó.</div>", 404)


EJEMPLOS = ["¿A qué hora abren?", "¿Dónde quedan?", "¿Aceptan tarjeta?", "Quiero hablar con una persona"]


@asistentes_bp.route("/asistente/<token>")
def link_prueba(token):
    a = asis.por_token(_db(), token)
    if not a:
        return _no_existe()
    e = html.escape
    conv = request.args.get("c", "")
    if not _CONV.match(conv):
        conv = secrets.token_urlsafe(16)
    msgs = asis.historial(_db(), a["id"], "link", conv)
    burbujas = []
    for m in msgs:
        hora = e(m["hora"])
        burbujas.append(f"<div class='m yo'>{e(m['texto'])}<span class='h'>{hora}</span></div>")
        clase = "m bot deriva" if m["tipo"] in ("derivar", "no_sabe") else "m bot"
        burbujas.append(f"<div class='{clase}'>{e(m['respuesta'])}<span class='h'>{hora}</span></div>")
    accion = f"/asistente/{e(a['token'])}/c/{e(conv)}"
    trampa = ("<div class='trampa' aria-hidden='true'><label>No completar<input type='text' name='web' "
              "tabindex='-1' autocomplete='off'></label></div>")
    if not burbujas:
        chips = "".join(f"<button type='submit' name='texto' value='{e(x)}'>{e(x)}</button>" for x in EJEMPLOS)
        burbujas.append("<div class='vacio'><b>Probá tu asistente</b><br>Escribile como te escribiría un cliente "
                        f"por WhatsApp.<form method='post' action='{accion}' class='chips'>{trampa}{chips}</form></div>")
    err = f"<div class='err' role='alert'>{e(request.args['error'][:200])}</div>" if request.args.get("error") else ""
    inicial = e(a["nombre"][:1].upper())
    estado = a["estado_local"]["texto"] or "Asistente virtual"
    cuerpo = (f"<header class='cab'><div class='avatar' aria-hidden='true'>{inicial}</div><div><h1>{e(a['nombre'])}</h1>"
              f"<div class='sub'>{e(estado)}</div></div></header>"
              "<div class='aviso'>Es una prueba: no le llega a ningún cliente. El equipo ve estas charlas para mejorar el asistente.</div>"
              f"<main class='chat' aria-live='polite'>{''.join(burbujas)}<span id='fin'></span></main>{err}"
              f"<form class='env' method='post' action='{accion}'>{trampa}"
              f"<label class='trampa' for='t'>Mensaje</label>"
              f"<input type='text' id='t' name='texto' maxlength='{asis.MAX_MENSAJE}' placeholder='Escribí un mensaje' "
              "autocomplete='off' required autofocus><button type='submit'>Enviar</button></form>")
    return _pagina(f"{a['nombre']} · asistente", cuerpo)


@asistentes_bp.route("/asistente/<token>/c/<conv>", methods=["POST"])
def link_mensaje(token, conv):
    a = asis.por_token(_db(), token)
    if not a or not _CONV.match(conv):
        return _no_existe()
    base = f"/asistente/{a['token']}?c={conv}"
    if request.form.get("web"):
        return redirect(base, 303)
    try:
        asis.conversar(_db(), a["id"], request.form.get("texto", ""), "link", conv, tope=asis.TOPE_LINK_HORA)
    except asis.NoSePuede as err:
        return redirect(f"{base}&error={quote(str(err))}", 303)
    return redirect(f"{base}#fin", 303)
