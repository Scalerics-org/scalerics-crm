"""Reseñas y opiniones: QR en el mostrador para comercios (services/resenas.py).

Dos grupos:
- `/api/resenas/...` y el cartel para imprimir `/resenas/cartel/...` son del
  equipo: piden el panel `resenas`.
- La página donde opina el cliente del comercio, `/opina/<slug>`, abre sin
  sesión (exenta en dashboard.require_login): es pública a propósito, va en
  un QR. Cada opinión tiene su link `/opina/<slug>/r/<código>` con un código
  al azar para agregar el comentario; sin ese código no se ve nada de nadie.

La página no lleva scripts: las estrellas son botones de un formulario común,
así anda en cualquier celular y con CSP estricta.
"""

import html
import os
import secrets
from urllib.parse import urlencode

from flask import Blueprint, Response, current_app, jsonify, redirect, request, session

from database import log_activity
from services import resenas as rs
from services.auth import require_panel

resenas_bp = Blueprint("resenas", __name__)

PUBLICAS = ("/opina/",)


def _db() -> str:
    return current_app.config["DB_PATH"]


def _error(msg, codigo: int = 400):
    return jsonify({"ok": False, "error": str(msg)}), codigo


def _quien() -> str:
    return session.get("user_name", "sistema")


def _base() -> str:
    base = (os.environ.get("CRM_URL") or request.host_url).rstrip("/")
    if base.startswith("http://") and not base.startswith(("http://localhost", "http://127.")):
        base = "https://" + base[len("http://"):]
    return base


def _codigo_error(e: rs.NoSePuede) -> int:
    return 404 if "no existe" in str(e) else 400


@resenas_bp.before_request
def _candado():
    if request.path.startswith(PUBLICAS):
        return None
    return require_panel(_db(), "resenas")


# ── equipo ───────────────────────────────────────────────────────────────────

def _con_links(n: dict) -> dict:
    n = dict(n)
    n["link"] = rs.link_publico(_base(), n)
    n["fuentes"] = [{**f, "link": rs.link_publico(_base(), n, f)} for f in n.get("fuentes", [])]
    return n


def _opinion_json(n: dict, o: dict) -> dict:
    o = dict(o)
    o.pop("codigo", None)
    o["whatsapp"] = rs.whatsapp_respuesta(n, o)
    return o


@resenas_bp.route("/api/resenas")
def api_negocios():
    return jsonify({"ok": True, "negocios": rs.negocios(_db())})


@resenas_bp.route("/api/resenas/negocios", methods=["POST"])
def api_crear():
    try:
        n = rs.crear_negocio(_db(), request.get_json(silent=True) or {}, _quien())
        rs.crear_fuente(_db(), n["id"], "Mostrador")
        n = rs.negocio(_db(), n["id"])
    except rs.NoSePuede as e:
        return _error(e)
    log_activity(_db(), _quien(), "resenas_alta", "resenas", n["id"], n["nombre"], n["slug"],
                 user_id=session.get("user_id"))
    return jsonify({"ok": True, "negocio": _con_links(n)}), 201


@resenas_bp.route("/api/resenas/negocios/<int:nid>")
def api_negocio(nid):
    filtro = request.args.get("filtro") or "bandeja"
    try:
        n = rs.negocio(_db(), nid)
        dias = int(request.args.get("dias") or 30)
        m = rs.metricas(_db(), nid, dias if dias in (7, 30, 90, 365) else 30)
        ops = [_opinion_json(n, o) for o in rs.opiniones(_db(), nid, filtro)]
    except rs.NoSePuede as e:
        return _error(e, _codigo_error(e))
    except ValueError:
        return _error("Período no válido.")
    return jsonify({"ok": True, "negocio": _con_links(n), "metricas": m, "opiniones": ops, "filtro": filtro})


@resenas_bp.route("/api/resenas/negocios/<int:nid>", methods=["PATCH"])
def api_editar(nid):
    try:
        n = rs.editar_negocio(_db(), nid, request.get_json(silent=True) or {})
    except rs.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "negocio": _con_links(n)})


@resenas_bp.route("/api/resenas/negocios/<int:nid>", methods=["DELETE"])
def api_borrar(nid):
    try:
        n = rs.negocio(_db(), nid)
    except rs.NoSePuede as e:
        return _error(e, 404)
    rs.borrar_negocio(_db(), nid)
    log_activity(_db(), _quien(), "resenas_baja", "resenas", nid, n["nombre"], "", user_id=session.get("user_id"))
    return jsonify({"ok": True})


@resenas_bp.route("/api/resenas/negocios/<int:nid>/fuentes", methods=["POST"])
def api_fuente(nid):
    try:
        f = rs.crear_fuente(_db(), nid, (request.get_json(silent=True) or {}).get("nombre"))
    except rs.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "fuente": f}), 201


@resenas_bp.route("/api/resenas/negocios/<int:nid>/fuentes/<int:fid>", methods=["DELETE"])
def api_borrar_fuente(nid, fid):
    try:
        rs.borrar_fuente(_db(), nid, fid)
    except rs.NoSePuede as e:
        return _error(e, 404)
    return jsonify({"ok": True})


@resenas_bp.route("/api/resenas/negocios/<int:nid>/google", methods=["POST"])
def api_google(nid):
    try:
        n = rs.cargar_google(_db(), nid, request.get_json(silent=True) or {}, _quien())
    except rs.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "negocio": _con_links(n)}), 201


@resenas_bp.route("/api/resenas/negocios/<int:nid>/google/<int:gid>", methods=["DELETE"])
def api_borrar_google(nid, gid):
    rs.borrar_google(_db(), nid, gid)
    return jsonify({"ok": True})


@resenas_bp.route("/api/resenas/negocios/<int:nid>/opiniones/<int:oid>", methods=["PATCH"])
def api_atender(nid, oid):
    try:
        n = rs.negocio(_db(), nid)
        o = rs.atender(_db(), oid, request.get_json(silent=True) or {}, _quien(), nid=nid)
    except rs.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "opinion": _opinion_json(n, o)})


@resenas_bp.route("/resenas/qr/<int:nid>.svg")
def qr(nid):
    """El QR solo, para verlo en el panel o bajarlo y mandarlo a la imprenta."""
    try:
        n = rs.negocio(_db(), nid)
    except rs.NoSePuede:
        return _error("El comercio no existe.", 404)
    fid = request.args.get("f", type=int)
    fuente = next((f for f in n["fuentes"] if f["id"] == fid), None)
    svg = rs.qr_svg(rs.link_publico(_base(), n, fuente))
    if not svg:
        return _error("Falta la librería segno para dibujar el QR.", 501)
    resp = Response(svg, mimetype="image/svg+xml")
    resp.headers["Content-Security-Policy"] = "default-src 'none'"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if request.args.get("bajar"):
        nombre = n["slug"] + (f"-{fuente['codigo']}" if fuente else "")
        resp.headers["Content-Disposition"] = f'attachment; filename="qr-{nombre}.svg"'
    return resp


@resenas_bp.route("/resenas/cartel/<int:nid>")
def cartel(nid):
    """El cartel para imprimir y pegar en el mostrador: QR grande y una frase."""
    try:
        n = rs.negocio(_db(), nid)
    except rs.NoSePuede:
        return _no_existe()
    fid = request.args.get("f", type=int)
    fuente = next((f for f in n["fuentes"] if f["id"] == fid), None)
    link = rs.link_publico(_base(), n, fuente)
    qr = rs.qr_svg(link) or ""
    e = html.escape
    cuerpo = (f"<div class='cartel'><p class='chico'>{e(n['nombre'])}</p>"
              "<h1>¿Cómo te atendimos?</h1><p>Escaneá y contanos en 10 segundos.</p>"
              f"<div class='qr'>{qr}</div><p class='link'>{e(link)}</p>"
              "<p class='chico'>Tu opinión la lee el dueño. ¡Gracias!</p></div>")
    # El SVG del QR va inline y se pinta con atributos, no con style: entra en la CSP.
    return _pagina(n, f"Cartel · {n['nombre']}", cuerpo, cartel=True)


# ── página pública ───────────────────────────────────────────────────────────

_CSS = """
*{box-sizing:border-box}
body{margin:0;background:#f8fafc;color:#0f172a;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
line-height:1.45}
.wrap{max-width:520px;margin:0 auto;padding:28px 18px 40px}
.cab{text-align:center;margin-bottom:18px}
.cab .logo{width:52px;height:52px;border-radius:14px;background:var(--acc);color:#fff;display:inline-flex;
align-items:center;justify-content:center;font-weight:800;font-size:1.4rem;margin-bottom:8px}
h1{font-size:1.45rem;margin:4px 0}
h2{font-size:1.1rem;margin:22px 0 6px}
p{margin:6px 0}
.sub{color:#64748b;font-size:.92rem}
.estrellas{display:flex;justify-content:center;gap:6px;margin:22px 0 8px;flex-direction:row-reverse}
.estrellas button{background:#fff;border:1px solid #e2e8f0;border-radius:14px;width:58px;height:62px;
font-size:1.9rem;line-height:1;cursor:pointer;color:#cbd5e1;padding:0;display:flex;flex-direction:column;
align-items:center;justify-content:center}
.estrellas button small{font-size:.62rem;color:#64748b;margin-top:2px}
.estrellas button:hover,.estrellas button:hover~button,.estrellas button:focus-visible,
.estrellas button:focus-visible~button{color:#f59e0b;border-color:#fcd34d}
.estrellas button:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
.escala{display:flex;justify-content:space-between;color:#94a3b8;font-size:.75rem;max-width:330px;margin:0 auto}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:18px;margin-top:16px}
.btn{display:block;width:100%;background:var(--acc);color:#fff;border:0;border-radius:12px;padding:14px 18px;
font-weight:700;font-family:inherit;font-size:1.02rem;cursor:pointer;text-decoration:none;text-align:center}
.btn.google{background:#fff;color:#1f2937;border:1px solid #d1d5db}
.btn.google b{color:#1a73e8}
.btn+.btn{margin-top:10px}
label.l{display:block;font-size:.85rem;color:#475569;margin:12px 0 4px}
input[type=text],textarea{width:100%;border:1px solid #cbd5e1;border-radius:10px;padding:10px;font-family:inherit;
font-size:1rem;background:#fff;color:#0f172a}
textarea{min-height:96px;resize:vertical}
.chk{display:flex;gap:8px;align-items:flex-start;font-size:.88rem;color:#334155;margin-top:10px}
.trampa{position:absolute;left:-9999px}
.aviso{border-radius:12px;padding:12px 14px;margin-bottom:14px;font-size:.92rem}
.aviso.ok{background:#ecfdf5;border:1px solid #a7f3d0;color:#065f46}
.aviso.mal{background:#fef2f2;border:1px solid #fecaca;color:#991b1b}
.puntos{font-size:1.6rem;color:#f59e0b;letter-spacing:2px;text-align:center}
.centro{text-align:center}
.m0{margin-top:0}
.mt{margin-top:12px}
.apagada{color:#e2e8f0}
.pie{margin-top:32px;color:#94a3b8;font-size:.75rem;text-align:center}
.pie a{color:#94a3b8}
a:focus-visible,button:focus-visible,input:focus-visible,textarea:focus-visible{outline:2px solid var(--acc);
outline-offset:2px}
.cartel{text-align:center;padding:30px 10px}
.cartel h1{font-size:2.3rem;margin:6px 0}
.cartel .qr svg{width:300px;height:300px;margin:22px auto 6px;display:block}
.cartel .link{font-size:.85rem;color:#64748b;overflow-wrap:anywhere}
.cartel .chico{color:#64748b;font-size:1rem}
@media print{body{background:#fff}.pie{display:none}}
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


def _pagina(n, titulo: str, cuerpo: str, codigo: int = 200, cartel: bool = False) -> Response:
    nonce = secrets.token_urlsafe(12)
    color = rs._color(n.get("color") if n else None)
    pie = "" if cartel else "<p class='pie'>Opiniones con <a href='https://scalerics.com'>Scalerics</a></p>"
    doc = ("<!doctype html><html lang='es'><head><meta charset='utf-8'>"
           "<meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<meta name='theme-color' content='{color}'><meta name='robots' content='noindex,nofollow'>"
           f"<title>{html.escape(titulo)}</title><style nonce='{nonce}'>:root{{--acc:{color}}}{_CSS}</style></head>"
           f"<body><div class='wrap'>{cuerpo}{pie}</div></body></html>")
    return _cabeceras(Response(doc, status=codigo, mimetype="text/html"), nonce)


def _no_existe() -> Response:
    return _pagina(None, "No encontrado", "<div class='cab'><h1>Este link no existe</h1>"
                   "<p class='sub'>Revisalo o pedíselo al comercio.</p></div>", 404)


def _cabecera(n: dict, sub: str = "") -> str:
    e = html.escape
    inicial = e((n["nombre"].strip()[:1] or "·").upper())
    return (f"<div class='cab'><div class='logo' aria-hidden='true'>{inicial}</div>"
            f"<h1>{e(n['nombre'])}</h1>{f'<p class=sub>{sub}</p>' if sub else ''}</div>")


def _ip() -> str:
    return request.headers.get("Fly-Client-IP") or request.remote_addr or ""


ETIQUETAS = {1: "Muy mal", 2: "Mal", 3: "Más o menos", 4: "Bien", 5: "Excelente"}


@resenas_bp.route("/opina/<slug>")
def opina_pagina(slug):
    n = rs.por_slug(_db(), slug)
    if not n:
        return _no_existe()
    e = html.escape
    f = (request.args.get("f") or "")[:40]
    if not n["activo"]:
        return _pagina(n, n["nombre"], _cabecera(n) + "<p class='sub centro'>Por ahora no estamos tomando "
                       "opiniones por acá. ¡Gracias por pasar!</p>")
    if request.method == "GET" and not request.args.get("error"):
        rs.registrar_vista(_db(), n["id"], f)
    error = request.args.get("error")
    aviso = f"<div class='aviso mal' role='alert'>{e(error[:300])}</div>" if error else ""
    pregunta = n.get("pregunta") or f"¿Cómo te fue hoy en {n['nombre']}?"
    botones = "".join(
        f"<button type='submit' name='puntaje' value='{i}' aria-label='{i} de 5: {ETIQUETAS[i]}'>★"
        f"<small>{i}</small></button>" for i in range(5, 0, -1))
    cuerpo = (_cabecera(n) + aviso
              + f"<div class='card'><h2 class='centro m0'>{e(pregunta)}</h2>"
              "<p class='sub centro'>Tocá una estrella. Es anónimo y lleva 10 segundos.</p>"
              f"<form method='post' action='/opina/{e(n['slug'])}'>"
              f"<input type='hidden' name='f' value='{e(f)}'>"
              "<div class='trampa' aria-hidden='true'><label>No completar<input type='text' name='web' "
              "tabindex='-1' autocomplete='off'></label></div>"
              f"<div class='estrellas' role='group' aria-label='Puntaje'>{botones}</div>"
              "<div class='escala' aria-hidden='true'><span>Muy mal</span><span>Excelente</span></div></form></div>")
    return _pagina(n, f"Tu opinión · {n['nombre']}", cuerpo)


@resenas_bp.route("/opina/<slug>", methods=["POST"])
def opina_enviar(slug):
    n = rs.por_slug(_db(), slug)
    if not n:
        return _no_existe()
    f = (request.form.get("f") or "")[:40]
    if request.form.get("web"):
        # La trampa para robots: un humano no ve ese campo. Se le contesta igual.
        return redirect(f"/opina/{n['slug']}", 303)
    try:
        o = rs.opinar(_db(), n["id"], request.form.get("puntaje"), f, _ip())
    except rs.NoSePuede as err:
        return redirect(f"/opina/{n['slug']}?" + urlencode({"error": str(err), "f": f}), 303)
    return redirect(f"/opina/{n['slug']}/r/{o['codigo']}", 303)


def _opinion_de(slug: str, codigo: str):
    n = rs.por_slug(_db(), slug)
    o = rs.por_codigo(_db(), codigo) if n else None
    if not n or not o or o["negocio_id"] != n["id"]:
        return None, None
    return n, o


@resenas_bp.route("/opina/<slug>/r/<codigo>")
def opina_gracias(slug, codigo):
    n, o = _opinion_de(slug, codigo)
    if not n:
        return _no_existe()
    e = html.escape
    base = f"/opina/{e(n['slug'])}/r/{e(o['codigo'])}"
    bueno = o["puntaje"] > rs.QUEJA_HASTA
    partes = [_cabecera(n)]
    if request.args.get("ok"):
        partes.append("<div class='aviso ok' role='status'>¡Listo! Se lo pasamos al comercio. Gracias por tomarte "
                      "el tiempo.</div>")
    elif request.args.get("error"):
        partes.append(f"<div class='aviso mal' role='alert'>{e(request.args['error'][:300])}</div>")
    partes.append(f"<div class='puntos' aria-label='{o['puntaje']} de 5'>{'★' * o['puntaje']}"
                  f"<span class='apagada'>{'★' * (5 - o['puntaje'])}</span></div>")
    google = (f"<a class='btn google' href='{base}/google' rel='noopener'>Dejar mi reseña en <b>Google</b></a>"
              if n.get("google_url") else "")
    form_comentario = ""
    if rs.puede_comentar(o):
        ya = o.get("comentario") or ""
        titulo = "Contale al dueño qué pasó" if not bueno else "¿Algo para contarle al dueño? (opcional)"
        ayuda = ("Lo lee el dueño, no se publica. Si dejás un contacto, te escriben para solucionarlo."
                 if not bueno else "Lo lee el dueño, no se publica.")
        form_comentario = (
            f"<form class='card' method='post' action='{base}'><h2 class='m0'>{titulo}</h2>"
            f"<p class='sub'>{ayuda}</p>"
            f"<label class='l' for='c'>Qué pasó</label><textarea id='c' name='comentario' maxlength='{rs.LARGO_COMENTARIO}'>"
            f"{e(ya)}</textarea>"
            f"<label class='l' for='nm'>Tu nombre (opcional)</label><input type='text' id='nm' name='nombre' "
            f"maxlength='120' value='{e(o.get('nombre') or '')}' autocomplete='given-name'>"
            f"<label class='l' for='ct'>Celular o mail (opcional)</label><input type='text' id='ct' name='contacto' "
            f"maxlength='120' value='{e(o.get('contacto') or '')}' autocomplete='tel'>"
            "<label class='chk'><input type='checkbox' name='quiere_contacto' value='1'"
            f"{' checked' if o.get('quiere_contacto') or not bueno else ''}> Quiero que me contacten</label>"
            "<p class='mt'><button type='submit' class='btn'>Enviar al dueño</button></p></form>")
    if bueno:
        partes.append("<div class='card centro'><h2 class='m0'>¡Qué bueno! Gracias.</h2>")
        if google:
            partes.append("<p class='sub'>¿Nos ayudás con una reseña en Google? Es lo que más le sirve a un "
                          "comercio chico: lleva 30 segundos.</p><p class='mt'>" + google + "</p>")
        partes.append("</div>")
        partes.append(form_comentario)
    else:
        partes.append("<div class='card centro'><h2 class='m0'>Gracias por "
                      "decírnoslo.</h2><p class='sub'>Queremos arreglarlo. Contanos qué pasó acá abajo.</p></div>")
        partes.append(form_comentario)
        if google:
            # Sin "review gating": a todos se les ofrece Google, también a quien puntuó bajo.
            partes.append("<div class='card'><p class='sub'>También podés dejar tu opinión pública en Google."
                          f"</p><p class='mt'>{google}</p></div>")
    return _pagina(n, f"Gracias · {n['nombre']}", "".join(partes))


@resenas_bp.route("/opina/<slug>/r/<codigo>", methods=["POST"])
def opina_comentar(slug, codigo):
    n, o = _opinion_de(slug, codigo)
    if not n:
        return _no_existe()
    base = f"/opina/{n['slug']}/r/{o['codigo']}"
    try:
        rs.comentar(_db(), codigo, request.form)
    except rs.NoSePuede as err:
        return redirect(base + "?" + urlencode({"error": str(err)}), 303)
    return redirect(base + "?ok=1", 303)


@resenas_bp.route("/opina/<slug>/r/<codigo>/google")
def opina_google(slug, codigo):
    n, o = _opinion_de(slug, codigo)
    if not n:
        return _no_existe()
    destino = rs.ir_a_google(_db(), codigo)
    if not destino:
        return redirect(f"/opina/{n['slug']}/r/{o['codigo']}", 303)
    return redirect(destino, 302)
