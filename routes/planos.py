"""Planificador de sitios (services/planos.py).

Dos grupos:
- `/api/planos/...` es del equipo: pide el panel `planos`.
- `/plano/<token>` es lo que abre el cliente para revisar el mapa y los
  textos, y aprobar o pedir cambios página por página. Abre sin sesión
  (exenta en dashboard.require_login): el token es al azar, de 16 bytes, y es
  lo único que da acceso. Un plano en borrador no se muestra.

La página pública no lleva scripts: los botones son formularios comunes, así
anda en cualquier celular y con CSP estricta. Todo lo que pinta pasa por
html.escape: los textos los escribe el equipo, pero el comentario y el nombre
los escribe cualquiera que tenga el link.
"""

import html
import os
import secrets
from urllib.parse import urlencode

from flask import Blueprint, Response, current_app, jsonify, redirect, request, session

from database import log_activity
from services import planos as pl
from services.auth import require_panel

planos_bp = Blueprint("planos", __name__)

PUBLICAS = ("/plano/",)


def _db() -> str:
    return current_app.config["DB_PATH"]


def _quien() -> str:
    return session.get("user_name", "sistema")


def _error(msg, codigo: int = 400):
    return jsonify({"ok": False, "error": str(msg)}), codigo


def _codigo(e: pl.NoSePuede) -> int:
    return 404 if "no existe" in str(e) else 400


def _base() -> str:
    base = (os.environ.get("CRM_URL") or request.host_url).rstrip("/")
    if base.startswith("http://") and not base.startswith(("http://localhost", "http://127.")):
        base = "https://" + base[len("http://"):]
    return base


@planos_bp.before_request
def _candado():
    if request.path.startswith(PUBLICAS):
        return None
    return require_panel(_db(), "planos")


# ── equipo ───────────────────────────────────────────────────────────────────

def _con_link(p: dict) -> dict:
    p = dict(p)
    p["link"] = f"{_base()}/plano/{p['token']}"
    return p


@planos_bp.route("/api/planos")
def api_listar():
    planos = pl.listar(_db())
    return jsonify({"ok": True, "planos": [_con_link(p) for p in planos], "resumen": pl.resumen(planos),
                    "catalogo": pl.catalogo(), "clientes": pl.clientes(_db())})


@planos_bp.route("/api/planos/vista-previa", methods=["POST"])
def api_vista_previa():
    """El mapa que saldría con este brief, sin guardar nada: para el formulario."""
    try:
        brief = pl.validar_brief(request.get_json(silent=True) or {})
        paginas = pl.generar(brief)
    except pl.NoSePuede as e:
        return _error(e)
    return jsonify({"ok": True, "brief": brief, "paginas": paginas, "estimacion": pl.estimar_horas(paginas)})


@planos_bp.route("/api/planos", methods=["POST"])
def api_crear():
    try:
        p = pl.crear(_db(), request.get_json(silent=True) or {}, _quien())
    except pl.NoSePuede as e:
        return _error(e)
    log_activity(_db(), _quien(), "plano_alta", "plano", p["id"], p["nombre"],
                 f"{p['n_paginas']} páginas", user_id=session.get("user_id"))
    return jsonify({"ok": True, "plano": _con_link(p)}), 201


@planos_bp.route("/api/planos/<int:pid>")
def api_detalle(pid):
    try:
        p = pl.detalle(_db(), pid)
    except pl.NoSePuede as e:
        return _error(e, 404)
    return jsonify({"ok": True, "plano": _con_link(p)})


@planos_bp.route("/api/planos/<int:pid>/mapa", methods=["PUT"])
def api_mapa(pid):
    data = request.get_json(silent=True) or {}
    try:
        p = pl.guardar_mapa(_db(), pid, data.get("paginas"))
    except pl.NoSePuede as e:
        return _error(e, _codigo(e))
    return jsonify({"ok": True, "plano": _con_link(p)})


@planos_bp.route("/api/planos/<int:pid>/brief", methods=["PUT"])
def api_brief(pid):
    data = request.get_json(silent=True) or {}
    try:
        p = pl.editar_brief(_db(), pid, data, regenerar=bool(data.get("regenerar")))
    except pl.NoSePuede as e:
        return _error(e, _codigo(e))
    return jsonify({"ok": True, "plano": _con_link(p)})


@planos_bp.route("/api/planos/<int:pid>/enviar", methods=["POST"])
def api_enviar(pid):
    """Lo abre para el cliente. No manda nada: el link lo pasa el equipo."""
    try:
        p = pl.marcar_enviado(_db(), pid)
    except pl.NoSePuede as e:
        return _error(e, _codigo(e))
    log_activity(_db(), _quien(), "plano_enviado", "plano", pid, p["nombre"], "", user_id=session.get("user_id"))
    return jsonify({"ok": True, "plano": _con_link(p)})


@planos_bp.route("/api/planos/<int:pid>/respuestas/<int:rid>", methods=["PATCH"])
def api_resolver(pid, rid):
    data = request.get_json(silent=True) or {}
    try:
        p = pl.resolver_respuesta(_db(), pid, rid, bool(data.get("resuelta", True)))
    except pl.NoSePuede as e:
        return _error(e, _codigo(e))
    return jsonify({"ok": True, "plano": _con_link(p)})


@planos_bp.route("/api/planos/<int:pid>/texto")
def api_texto(pid):
    try:
        p = pl.detalle(_db(), pid)
    except pl.NoSePuede as e:
        return _error(e, 404)
    return jsonify({"ok": True, "texto": pl.texto_brief(p)})


@planos_bp.route("/api/planos/<int:pid>", methods=["DELETE"])
def api_borrar(pid):
    try:
        p = pl.detalle(_db(), pid)
        pl.borrar(_db(), pid)
    except pl.NoSePuede as e:
        return _error(e, 404)
    log_activity(_db(), _quien(), "plano_baja", "plano", pid, p["nombre"], "", user_id=session.get("user_id"))
    return jsonify({"ok": True})


# ── la página del cliente ────────────────────────────────────────────────────

_CSS = """
*{box-sizing:border-box}
body{margin:0;background:#f1f5f9;color:#0f172a;font:16px/1.5 Inter,system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:24px 16px 48px}
a{color:#0077b3}
h1{font-size:1.6rem;margin:0 0 4px;line-height:1.2}
h2{font-size:1.2rem;margin:0}
h3{font-size:.95rem;margin:0}
.sub{color:#475569;margin:4px 0 0}
.chico{font-size:.82rem;color:#64748b}
.marca{font-size:.75rem;letter-spacing:.08em;text-transform:uppercase;color:#0088cc;font-weight:700}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:18px;margin-top:16px}
.aviso{border-radius:10px;padding:12px 14px;margin-top:16px;font-size:.92rem}
.aviso.ok{background:#ecfdf5;color:#065f46;border:1px solid #a7f3d0}
.aviso.mal{background:#fef2f2;color:#991b1b;border:1px solid #fecaca}
.barra{display:block;width:100%;height:8px;margin-top:10px;border:0;border-radius:4px;background:#e2e8f0;accent-color:#10b981;appearance:none}
.barra::-webkit-progress-bar{background:#e2e8f0;border-radius:4px}
.barra::-webkit-progress-value{background:#10b981;border-radius:4px}
.barra::-moz-progress-bar{background:#10b981;border-radius:4px}
.arbol{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 0;padding:0;list-style:none}
.arbol li{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:6px 10px;font-size:.85rem}
.arbol li.hijo{border-style:dashed}
.arbol a{text-decoration:none;color:inherit}
.pill{display:inline-block;border-radius:999px;padding:2px 10px;font-size:.75rem;font-weight:600;background:#f1f5f9;color:#475569}
.pill.aprobada{background:#d1fae5;color:#065f46}
.pill.cambios{background:#fef3c7;color:#92400e}
.cab-pag{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;flex-wrap:wrap}
.ruta{font-family:ui-monospace,Menlo,monospace;font-size:.8rem;color:#64748b}
.google{border:1px solid #e2e8f0;border-radius:10px;padding:10px 12px;margin-top:12px;background:#fff}
.google .t{color:#1a0dab;font-size:1.05rem;line-height:1.3}
.google .u{color:#047857;font-size:.8rem}
.google .d{color:#4d5156;font-size:.85rem}
.sec{border:1px solid #e2e8f0;border-left:4px solid #0088cc;border-radius:10px;padding:12px 14px;margin-top:10px;background:#fcfdff}
.sec.hero{border-left-color:#0f172a;background:#f8fafc}
.tipo{font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;color:#64748b;font-weight:700}
.sec p{margin:6px 0 0;white-space:pre-line}
.sec ul{margin:8px 0 0;padding-left:18px}
.btn-falso{display:inline-block;margin-top:8px;background:#0088cc;color:#fff;border-radius:8px;padding:5px 12px;font-size:.85rem;font-weight:600}
form.resp{margin-top:14px;border-top:1px solid #e2e8f0;padding-top:12px}
textarea,input[type=text]{width:100%;font:inherit;border:1px solid #cbd5e1;border-radius:8px;padding:8px 10px;background:#fff;color:#0f172a}
textarea{min-height:80px}
label.l{display:block;font-size:.82rem;color:#475569;margin:8px 0 4px}
.botones{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
button{font:inherit;font-weight:600;border-radius:10px;padding:9px 16px;border:1px solid transparent;cursor:pointer}
button.ok{background:#10b981;color:#fff}
button.cambio{background:#fff;color:#92400e;border-color:#f59e0b}
.hist{margin-top:10px;font-size:.85rem;color:#475569}
.hist div{border-left:3px solid #f59e0b;padding:2px 0 2px 10px;margin-top:6px}
.hist div.aprobada{border-left-color:#10b981}
.trampa{position:absolute;left:-9999px}
.pie{text-align:center;color:#94a3b8;font-size:.8rem;margin-top:28px}
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
           f"<body><div class='wrap'>{cuerpo}<p class='pie'>Plan del sitio armado con "
           "<a href='https://scalerics.com'>Scalerics</a></p></div></body></html>")
    return _cabeceras(Response(doc, status=codigo, mimetype="text/html"), nonce)


def _no_existe() -> Response:
    return _pagina("No encontrado", "<div class='card'><h1>Este link no existe</h1>"
                   "<p class='sub'>Revisalo o pedíselo a quien te lo mandó.</p></div>", 404)


ESTADO_PAGINA = {"pendiente": "Para revisar", "aprobada": "Aprobada", "cambios": "Pediste cambios"}


def _seccion(s: dict) -> str:
    e = html.escape
    partes = [f"<div class='sec {e(s['bloque'])}'><div class='tipo'>{e(pl.BLOQUES[s['bloque']]['nombre'])}</div>"]
    if s["titulo"]:
        partes.append(f"<h3>{e(s['titulo'])}</h3>")
    if s["texto"]:
        partes.append(f"<p>{e(s['texto'])}</p>")
    if s["items"]:
        partes.append("<ul>" + "".join(
            f"<li><b>{e(i['titulo'])}</b>{(': ' + e(i['texto'])) if i['texto'] else ''}</li>" for i in s["items"]) + "</ul>")
    if s["boton"]:
        partes.append(f"<span class='btn-falso'>{e(s['boton'])}</span>")
    partes.append("</div>")
    return "".join(partes)


def _pagina_plano(p: dict, pg: dict, base: str) -> str:
    e = html.escape
    estado = p["estado_paginas"].get(pg["clave"], "pendiente")
    dominio = p["brief"].get("dominio") or "tusitio.com.uy"
    historia = [r for r in p["respuestas"] if r["pagina"] == pg["clave"] and (r["comentario"] or r["decision"] == "cambios")]
    hist = ""
    if historia:
        hist = "<div class='hist'>" + "".join(
            f"<div class='{e(r['decision'])}'><b>{e(r['nombre'] or 'Vos')}</b> · {e(r['creado_en'][:10])}"
            f"{' · resuelto' if r['resuelta'] else ''}<br>{e(r['comentario'] or 'Aprobada')}</div>"
            for r in historia[-5:]) + "</div>"
    form = ""
    if p["estado"] != "borrador":
        form = (f"<form class='resp' method='post' action='{base}/p/{e(pg['clave'])}'>"
                "<div class='trampa' aria-hidden='true'><label>No completar<input type='text' name='web' "
                "tabindex='-1' autocomplete='off'></label></div>"
                f"<label class='l' for='c-{e(pg['clave'])}'>¿Algo para cambiar en esta página? (opcional para aprobar)</label>"
                f"<textarea id='c-{e(pg['clave'])}' name='comentario' maxlength='{pl.MAX_COMENTARIO}'></textarea>"
                f"<label class='l' for='n-{e(pg['clave'])}'>Tu nombre (opcional)</label>"
                f"<input type='text' id='n-{e(pg['clave'])}' name='nombre' maxlength='80' autocomplete='name'>"
                "<div class='botones'><button type='submit' name='decision' value='aprobada' class='ok'>Aprobar esta página</button>"
                "<button type='submit' name='decision' value='cambios' class='cambio'>Pedir cambios</button></div></form>")
    return (f"<section class='card' id='{e(pg['clave'])}'><div class='cab-pag'><div>"
            f"<h2>{e(pg['nombre'])}</h2><div class='ruta'>{e(dominio)}{e(pg['slug'] if pg['slug'] != '/' else '')}</div></div>"
            f"<span class='pill {e(estado)}'>{e(ESTADO_PAGINA[estado])}</span></div>"
            "<div class='google' aria-label='Así se vería en Google'>"
            f"<div class='u'>{e(dominio)}{e(pg['slug'] if pg['slug'] != '/' else '')}</div>"
            f"<div class='t'>{e(pg['seo']['titulo'])}</div><div class='d'>{e(pg['seo']['descripcion'])}</div></div>"
            + "".join(_seccion(s) for s in pg["secciones"]) + hist + form + "</section>")


@planos_bp.route("/plano/<token>")
def plano_pagina(token):
    p = pl.por_token(_db(), token)
    if not p or p["estado"] == "borrador":
        return _no_existe()
    e = html.escape
    base = f"/plano/{e(p['token'])}"
    avisos = ""
    if request.args.get("ok"):
        avisos = "<div class='aviso ok' role='status'>¡Gracias! Ya le llegó al equipo.</div>"
    elif request.args.get("error"):
        avisos = f"<div class='aviso mal' role='alert'>{e(request.args['error'][:300])}</div>"
    total = len(p["paginas"])
    pct = round(100 * p["aprobadas"] / total) if total else 0
    hijos = {}
    for pg in p["paginas"]:
        if pg["padre"]:
            hijos.setdefault(pg["padre"], []).append(pg)
    arbol = []
    for pg in p["paginas"]:
        if pg["padre"]:
            continue
        arbol.append(f"<li><a href='#{e(pg['clave'])}'>{e(pg['nombre'])}</a></li>")
        arbol += [f"<li class='hijo'><a href='#{e(h['clave'])}'>↳ {e(h['nombre'])}</a></li>" for h in hijos.get(pg["clave"], [])]
    aprobar_todo = ""
    if p["estado"] != "aprobado":
        aprobar_todo = (f"<form method='post' action='{base}/p/*' class='resp'>"
                        "<div class='trampa' aria-hidden='true'><label>No completar<input type='text' name='web' "
                        "tabindex='-1' autocomplete='off'></label></div>"
                        "<input type='hidden' name='decision' value='aprobada'>"
                        "<label class='l' for='n-todo'>Tu nombre (opcional)</label>"
                        "<input type='text' id='n-todo' name='nombre' maxlength='80' autocomplete='name'>"
                        "<div class='botones'><button type='submit' class='ok'>Aprobar todo el sitio</button></div></form>")
    estado_txt = ("¡Aprobaste todo! Con esto empezamos a diseñar." if p["estado"] == "aprobado"
                  else f"Aprobaste {p['aprobadas']} de {total} páginas.")
    cuerpo = (f"<div class='marca'>Scalerics · plan del sitio</div><h1>{e(p['nombre'])}</h1>"
              "<p class='sub'>Este es el mapa de tu sitio con lo que va a decir cada página. Leelo con calma: "
              "aprobá cada página o pedinos cambios. Las fotos y el diseño vienen después.</p>"
              + avisos
              + f"<div class='card'><h2>Las páginas</h2><ul class='arbol'>{''.join(arbol)}</ul>"
              f"<progress class='barra' max='{total}' value='{p['aprobadas']}' aria-label='Páginas aprobadas: {pct}%'></progress>"
              f"<p class='chico'>{e(estado_txt)}</p>{aprobar_todo}</div>"
              + "".join(_pagina_plano(p, pg, base) for pg in p["paginas"]))
    return _pagina(f"Plan del sitio · {p['nombre']}", cuerpo)


@planos_bp.route("/plano/<token>/p/<pagina>", methods=["POST"])
def plano_responder(token, pagina):
    p = pl.por_token(_db(), token)
    if not p or p["estado"] == "borrador":
        return _no_existe()
    base = f"/plano/{p['token']}"
    if request.form.get("web"):
        # La trampa para robots: un humano no ve ese campo.
        return redirect(base, 303)
    try:
        pl.responder(_db(), p["token"], pagina, request.form.get("decision", ""),
                     request.form.get("comentario", ""), request.form.get("nombre", ""))
    except pl.NoSePuede as err:
        return redirect(f"{base}?" + urlencode({"error": str(err)}), 303)
    ancla = "" if pagina == "*" else "#" + pl.slug(pagina)
    return redirect(f"{base}?ok=1{ancla}", 303)
