"""Diagnóstico web gratuito (services/diagnostico.py).

Dos grupos:
- Públicas, sin sesión: `/diagnostico` (el formulario, que funciona sin
  JavaScript), `/diagnostico/api` (lo mismo en JSON, para el formulario de
  scalerics.com; CORS solo para los orígenes de routes/web.py) y
  `/diagnostico/<código>` (el informe). La única llave del informe es el
  código al azar. Muestra la dirección y el resultado; ni el mail ni el
  teléfono de quien lo pidió.
- `/api/diagnosticos/...` para el equipo: piden el panel `diagnostico`.

Las públicas están exentas en dashboard.require_login (PUBLICAS).
"""

import html
import json
import os
import secrets
from urllib.parse import quote

from flask import Blueprint, Response, current_app, jsonify, redirect, request, session

from database import log_activity
from routes.web import _origenes
from services import diagnostico as dg
from services.auth import require_panel

diagnostico_bp = Blueprint("diagnostico", __name__)

PUBLICAS = ("/diagnostico",)

WHATSAPP_POR_DEFECTO = "59897250713"   # el de scalerics.com


def _db() -> str:
    return current_app.config["DB_PATH"]


def _error(msg, codigo: int = 400):
    return jsonify({"ok": False, "error": str(msg)}), codigo


def _base() -> str:
    base = (os.environ.get("CRM_URL") or request.host_url).rstrip("/")
    if base.startswith("http://") and not base.startswith(("http://localhost", "http://127.")):
        base = "https://" + base[len("http://"):]
    return base


def _ip() -> str:
    # Igual que routes/analitica: Fly pone la IP real en Fly-Client-IP.
    return (request.headers.get("Fly-Client-IP")
            or (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            or request.remote_addr or "desconocida")


@diagnostico_bp.before_request
def _candado():
    if request.path == "/diagnostico" or request.path.startswith("/diagnostico/"):
        return None
    return require_panel(_db(), "diagnostico")


def _informe_url(d: dict) -> str:
    return f"{_base()}/diagnostico/{d['codigo']}"


# ── páginas públicas ─────────────────────────────────────────────────────────

e = html.escape

ESTILO = """
:root{--bg:#09090f;--surface:rgba(255,255,255,.04);--border:rgba(255,255,255,.07);--text:#e2e8f0;--muted:#94a3b8;
--blue:#0088CC;--blue-lt:#33aadd;--blue-glow:rgba(0,136,204,.14);--blue-border:rgba(0,136,204,.28);--green:#10B981;
--green-lt:#34d399;--amber:#f59e0b;--red:#f43f5e}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.55 Inter,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
a{color:var(--blue-lt)}
.wrap{max-width:860px;margin:0 auto;padding:28px 16px 56px}
.marca{display:flex;align-items:center;gap:10px;font-weight:800;letter-spacing:.02em;color:#fff;text-decoration:none;font-size:1.05rem}
.marca i{width:28px;height:28px;border-radius:8px;background:linear-gradient(135deg,var(--blue),var(--green));display:inline-block}
h1{font-size:clamp(1.6rem,4.6vw,2.4rem);line-height:1.15;margin:26px 0 10px;letter-spacing:-.02em}
h1 em{font-style:normal;color:var(--blue-lt)}
h2{font-size:1.2rem;margin:34px 0 12px}
.sub{color:var(--muted);margin:0 0 22px;max-width:640px}
.caja{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:20px}
label{display:block;font-size:.85rem;color:var(--muted);margin:14px 0 6px}
input{width:100%;padding:13px 14px;border-radius:10px;border:1px solid var(--border);background:rgba(255,255,255,.06);
color:var(--text);font:inherit}
input:focus{outline:2px solid var(--blue);border-color:transparent}
.fila2{display:grid;grid-template-columns:1fr 1fr;gap:0 14px}
@media(max-width:600px){.fila2{grid-template-columns:1fr}}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;border:0;border-radius:12px;padding:14px 22px;
font:inherit;font-weight:700;cursor:pointer;text-decoration:none;color:#fff;background:var(--blue)}
.btn:hover{background:var(--blue-lt)}.btn.wa{background:var(--green)}.btn.wa:hover{background:var(--green-lt)}
.btn[disabled]{opacity:.6;cursor:wait}
.ancho{width:100%;margin-top:20px}
.err{background:rgba(244,63,94,.1);border:1px solid rgba(244,63,94,.35);color:#fecdd3;border-radius:10px;padding:10px 14px;margin:0 0 14px}
.chica{font-size:.8rem;color:var(--muted)}
.pills{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0 0;padding:0;list-style:none}
.pills li{border:1px solid var(--blue-border);background:var(--blue-glow);border-radius:999px;padding:5px 12px;font-size:.82rem}
.cab{display:grid;grid-template-columns:auto 1fr;gap:24px;align-items:center;margin-top:24px}
@media(max-width:600px){.cab{grid-template-columns:1fr;justify-items:start}}
.anillo{--p:0;--c:var(--blue);width:150px;height:150px;border-radius:50%;display:grid;place-items:center;
background:conic-gradient(var(--c) calc(var(--p)*1%),rgba(255,255,255,.08) 0)}
.anillo div{width:118px;height:118px;border-radius:50%;background:var(--bg);display:grid;place-items:center;text-align:center}
.anillo b{font-size:2.6rem;line-height:1}.anillo span{font-size:.75rem;color:var(--muted)}
.nivel{display:inline-block;border-radius:999px;padding:4px 12px;font-weight:700;font-size:.85rem;margin-bottom:8px}
.nivel.bien{background:rgba(16,185,129,.15);color:var(--green-lt)}.nivel.mejorable{background:rgba(245,158,11,.15);color:#fcd34d}
.nivel.mal{background:rgba(244,63,94,.15);color:#fda4af}
.dom{font-size:1.4rem;font-weight:800;margin:0;word-break:break-all}
.cats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
@media(max-width:700px){.cats{grid-template-columns:repeat(2,1fr)}}
.cat{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:14px}
.cat b{font-size:1.5rem}.cat p{margin:4px 0 8px;font-size:.78rem;color:var(--muted)}
.barra{height:6px;border-radius:6px;background:rgba(255,255,255,.08);overflow:hidden}
.barra i{display:block;height:100%;border-radius:6px}
.prio{display:grid;gap:12px;counter-reset:p}
.prio .caja{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;border-color:var(--blue-border)}
.prio .caja::before{counter-increment:p;content:counter(p);grid-row:span 3;width:34px;height:34px;border-radius:10px;
display:grid;place-items:center;font-weight:800;background:var(--blue-glow);color:var(--blue-lt)}
.prio h3,.hall h3{margin:0;font-size:1rem}.prio p,.hall p{margin:0;color:var(--muted);font-size:.9rem}
.prio .como,.hall .como{color:var(--text)}
details.grupo{border:1px solid var(--border);border-radius:14px;margin:10px 0;background:var(--surface)}
details.grupo summary{cursor:pointer;padding:14px 16px;font-weight:700;display:flex;justify-content:space-between;gap:10px}
.hall{display:grid;grid-template-columns:22px 1fr;gap:12px;padding:12px 16px;border-top:1px solid var(--border)}
.hall>div{display:grid;gap:4px;min-width:0}
.ic{width:22px;height:22px;border-radius:50%;display:grid;place-items:center;font-size:.8rem;font-weight:800}
.ic.ok{background:rgba(16,185,129,.15);color:var(--green-lt)}.ic.aviso{background:rgba(245,158,11,.15);color:#fcd34d}
.ic.mal{background:rgba(244,63,94,.15);color:#fda4af}.ic.info{background:var(--blue-glow);color:var(--blue-lt)}
.cierre{margin-top:34px;text-align:center;border:1px solid var(--blue-border);background:linear-gradient(180deg,var(--blue-glow),transparent)}
.cierre h2{margin-top:0}
.pie{margin-top:30px;text-align:center}
"""


def _pagina(titulo: str, cuerpo: str, nonce: str | None = None, script: str = "") -> Response:
    js = f'<script nonce="{nonce}">{script}</script>' if nonce and script else ""
    doc = (f'<!doctype html><html lang="es"><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">'
           f'<title>{e(titulo)}</title><style>{ESTILO}</style></head><body><div class="wrap">'
           f'<a class="marca" href="https://scalerics.com" rel="noopener"><i></i>Scalerics</a>{cuerpo}'
           f'<p class="pie chica">Scalerics · sitios web, tiendas y sistemas para comercios de Uruguay</p></div>{js}</body></html>')
    resp = Response(doc, mimetype="text/html")
    marcos = " ".join(["'self'", *_origenes()])
    resp.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'self'; base-uri 'none'; "
        f"frame-ancestors {marcos}; " + (f"script-src 'nonce-{nonce}'" if nonce else "script-src 'none'"))
    resp.headers["X-Robots-Tag"] = "noindex"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


ESPERA_JS = ("document.getElementById('dg-form').addEventListener('submit',function(){var b=document.getElementById('dg-btn');"
             "b.disabled=true;b.textContent='Analizando tu sitio… (unos 10 segundos)';});")


def _formulario(datos: dict | None = None, error: str = "", codigo: int = 200) -> Response:
    d = datos or {}
    nonce = secrets.token_urlsafe(12)
    cuerpo = f"""
<h1>¿Tu sitio web te trae clientes <em>o te los hace perder</em>?</h1>
<p class="sub">Escribí la dirección y en unos segundos te mostramos cómo se ve desde el celular, si es fácil
escribirte por WhatsApp, si Google lo entiende, qué tan rápido carga y qué arreglaríamos primero. Gratis.</p>
<form class="caja" id="dg-form" method="post" action="/diagnostico">
  {f'<div class="err" role="alert">{e(error)}</div>' if error else ''}
  <label for="dg-url">Dirección del sitio</label>
  <input id="dg-url" name="url" required inputmode="url" autocomplete="url" placeholder="tucomercio.com.uy" value="{e(d.get('url') or '')}">
  <label for="dg-nombre">Nombre del comercio <span class="chica">(opcional)</span></label>
  <input id="dg-nombre" name="nombre" maxlength="120" autocomplete="organization" value="{e(d.get('nombre') or '')}">
  <div class="fila2">
    <div><label for="dg-tel">WhatsApp</label>
    <input id="dg-tel" name="telefono" inputmode="tel" autocomplete="tel" placeholder="099 123 456" value="{e(d.get('telefono') or '')}"></div>
    <div><label for="dg-mail">o mail</label>
    <input id="dg-mail" name="email" type="email" autocomplete="email" value="{e(d.get('email') or '')}"></div>
  </div>
  <button class="btn ancho" id="dg-btn" type="submit">Analizar mi sitio</button>
  <p class="chica">Con tu WhatsApp o mail te podemos mandar el informe y, si querés, ayudarte a arreglarlo.
  No te suscribimos a nada.</p>
</form>
<ul class="pills"><li>Celular</li><li>WhatsApp y contacto</li><li>Google</li><li>Velocidad</li><li>Seguridad</li><li>Medición</li></ul>
"""
    resp = _pagina("Diagnóstico web gratuito · Scalerics", cuerpo, nonce, ESPERA_JS)
    resp.status_code = codigo
    return resp


def _origen_permitido() -> bool:
    origen = (request.headers.get("Origin") or "").rstrip("/")
    if not origen or origen == "null":
        return True
    return origen == _base() or origen == request.host_url.rstrip("/") or origen in _origenes()


@diagnostico_bp.route("/diagnostico", methods=["GET"])
def formulario():
    return _formulario()


@diagnostico_bp.route("/diagnostico", methods=["POST"])
def formulario_enviar():
    datos = {k: (request.form.get(k) or "")[:300] for k in ("url", "nombre", "telefono", "email")}
    if not _origen_permitido():
        return _formulario(datos, "Este formulario se usa desde scalerics.com.", 403)
    try:
        d = dg.crear_publico(_db(), datos, _ip())
    except dg.NoSePuede as ex:
        return _formulario(datos, str(ex), 429 if "seguidos" in str(ex) else 400)
    return redirect(f"/diagnostico/{d['codigo']}", code=303)


def _cors(resp):
    origen = (request.headers.get("Origin") or "").rstrip("/")
    if origen in _origenes():
        resp.headers["Access-Control-Allow-Origin"] = origen
        resp.headers["Vary"] = "Origin"
    return resp


@diagnostico_bp.route("/diagnostico/api", methods=["OPTIONS"])
def api_publica_preflight():
    resp = Response(status=204)
    if (request.headers.get("Origin") or "").rstrip("/") in _origenes():
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Max-Age"] = "86400"
    return _cors(resp)


@diagnostico_bp.route("/diagnostico/api", methods=["POST"])
def api_publica():
    if not _origen_permitido():
        return _error("origen_no_permitido", 403)
    datos = request.get_json(silent=True) or {}
    try:
        d = dg.crear_publico(_db(), {k: str(datos.get(k) or "")[:300] for k in ("url", "nombre", "telefono", "email")},
                             _ip())
    except dg.NoSePuede as ex:
        return _cors(jsonify({"ok": False, "error": str(ex)})), 429 if "seguidos" in str(ex) else 400
    return _cors(jsonify({"ok": True, "estado": d["estado"], "puntaje": d["puntaje"], "informe": _informe_url(d)})), 201


def _color(p: int) -> str:
    return "var(--green)" if p >= 80 else ("var(--amber)" if p >= 55 else "var(--red)")


ICONOS = {"ok": "✓", "aviso": "!", "mal": "✕", "info": "i"}


def _hallazgo(h: dict) -> str:
    extra = ""
    if h["estado"] in ("aviso", "mal"):
        extra = (f'<p>{e(h["por_que"])}</p>' if h["por_que"] else "") + \
                (f'<p class="como"><b>Cómo se arregla:</b> {e(h["solucion"])}</p>' if h["solucion"] else "")
    detalle = f'<p>{e(h["detalle"])}</p>' if h["detalle"] else ""
    return (f'<div class="hall"><span class="ic {e(h["estado"])}" aria-hidden="true">{ICONOS.get(h["estado"], "")}</span>'
            f'<div><h3>{e(h["titulo"])}</h3>{detalle}{extra}</div></div>')


def _whatsapp(d: dict) -> str:
    numero = "".join(c for c in os.environ.get("SCALERICS_WHATSAPP", WHATSAPP_POR_DEFECTO) if c.isdigit())
    texto = f"Hola, hice el diagnóstico de {d['dominio']} y quiero ver cómo mejorarlo: {_informe_url(d)}"
    return f"https://wa.me/{numero}?text={quote(texto)}"


@diagnostico_bp.route("/diagnostico/<codigo>")
def informe(codigo):
    d = dg.por_codigo(_db(), codigo)
    if not d:
        return _pagina("No encontramos ese informe", "<h1>No encontramos ese informe</h1>"
                       '<p class="sub">Puede que el link haya cambiado. <a href="/diagnostico">Hacé un diagnóstico nuevo</a>.</p>'), 404
    if not session.get("logged_in"):
        dg.marcar_apertura(_db(), d["id"])
    fecha = dg.fecha_mvd(d["actualizado_en"])
    if d["estado"] != "ok":
        cuerpo = (f'<h1>No pudimos abrir <em>{e(d["dominio"])}</em></h1>'
                  f'<p class="sub">{e(d["error"] or "")}</p>'
                  '<div class="caja cierre"><h2>Si a nosotros no nos abrió, a tus clientes tampoco</h2>'
                  '<p class="sub" style="margin:0 auto 18px">Escribinos y lo miramos con vos.</p>'
                  f'<a class="btn wa" href="{e(_whatsapp(d))}" rel="noopener">Hablar por WhatsApp</a></div>'
                  f'<p class="chica">Diagnóstico del {e(fecha)} · <a href="/diagnostico">Probar con otra dirección</a></p>')
        return _pagina(f"Diagnóstico de {d['dominio']}", cuerpo)
    r = d["resultado"]
    hs = {h["clave"]: h for h in r["hallazgos"]}
    p = int(r["puntaje"])
    cats = "".join(
        f'<div class="cat"><div>{e(c["nombre"])}</div><b style="color:{_color(c["puntaje"])}">{c["puntaje"]}</b>'
        f'<p>{e(c["ayuda"])}</p><div class="barra"><i style="width:{c["puntaje"]}%;background:{_color(c["puntaje"])}"></i></div></div>'
        for c in r["categorias"])
    prio = "".join(
        f'<div class="caja"><h3>{e(hs[k]["titulo"])}</h3><p>{e(hs[k]["por_que"])}</p>'
        f'<p class="como"><b>Lo que haríamos:</b> {e(hs[k]["solucion"])}</p></div>'
        for k in r["prioridades"] if k in hs)
    grupos = ""
    for c in r["categorias"]:
        de = [h for h in r["hallazgos"] if h["cat"] == c["clave"]]
        de.sort(key=lambda h: ("mal", "aviso", "info", "ok").index(h["estado"]))
        problemas = f'{c["problemas"]} para mejorar' if c["problemas"] else "todo bien"
        grupos += (f'<details class="grupo"{" open" if c["problemas"] else ""}><summary><span>{e(c["nombre"])}</span>'
                   f'<span class="chica">{e(problemas)}</span></summary>{"".join(_hallazgo(h) for h in de)}</details>')
    m = r.get("medidas") or {}
    datos = [x for x in (m.get("plataforma") and f"Hecho con {m['plataforma']}",
                         m.get("segundos") and f"Responde en {m['segundos']} s",
                         m.get("imagenes") is not None and f"{m['imagenes']} imágenes") if x]
    cuerpo = f"""
<div class="cab">
  <div class="anillo" style="--p:{p};--c:{_color(p)}"><div><span><b>{p}</b><br>de 100</span></div></div>
  <div><span class="nivel {e(r['nivel']['clave'])}">{e(r['nivel']['texto'])}</span>
  <p class="dom">{e(d['dominio'])}</p>
  <p class="chica">{e(d['nombre'] or '')}{' · ' if d['nombre'] else ''}Diagnóstico del {e(fecha)}{' · ' if datos else ''}{e(' · '.join(datos))}</p></div>
</div>
<h2>Cómo le va en cada cosa</h2>
<div class="cats">{cats}</div>
{f'<h2>Lo primero que arreglaríamos</h2><div class="prio">{prio}</div>' if prio else
 '<h2>Está muy bien</h2><p class="sub">No encontramos nada urgente. Si querés saber cuántos clientes te trae, eso también lo medimos.</p>'}
<h2>El detalle</h2>
{grupos}
<div class="caja cierre">
  <h2>¿Lo arreglamos juntos?</h2>
  <p class="sub" style="margin:0 auto 18px">Mandanos este informe por WhatsApp y te decimos cuánto lleva y cuánto cuesta dejarlo bien.</p>
  <a class="btn wa" href="{e(_whatsapp(d))}" rel="noopener">Hablar por WhatsApp</a>
</div>
<p class="chica">Revisamos la página de inicio tal como la ve un visitante. Es una foto de hoy: no mira todas las páginas
ni reemplaza una auditoría completa. <a href="/diagnostico">Analizar otro sitio</a></p>
"""
    return _pagina(f"Diagnóstico de {d['dominio']} · {p}/100", cuerpo)


# ── equipo ───────────────────────────────────────────────────────────────────

def _con_links(d: dict) -> dict:
    d = dict(d)
    d["informe_url"] = _informe_url(d)
    return d


@diagnostico_bp.route("/api/diagnosticos")
def api_listar():
    datos = dg.listar(_db(), request.args.get("filtro", ""))
    datos["diagnosticos"] = [_con_links(d) for d in datos["diagnosticos"]]
    return jsonify({"ok": True, **datos, "publica_url": f"{_base()}/diagnostico"})


@diagnostico_bp.route("/api/diagnosticos", methods=["POST"])
def api_crear():
    quien = session.get("user_name", "sistema")
    try:
        d = dg.crear_equipo(_db(), request.get_json(silent=True) or {}, quien)
    except dg.NoSePuede as ex:
        return _error(ex)
    log_activity(_db(), quien, "diagnostico_web", "diagnostico", d["id"], d["dominio"],
                 f"{d['puntaje']}/100" if d["estado"] == "ok" else (d["error"] or ""), user_id=session.get("user_id"))
    return jsonify({"ok": True, "diagnostico": _con_links(d)}), 201


@diagnostico_bp.route("/api/diagnosticos/leads")
def api_leads():
    return jsonify({"ok": True, "leads": dg.leads_sugeridos(_db(), request.args.get("q", ""))})


@diagnostico_bp.route("/api/diagnosticos/<int:did>")
def api_ver(did):
    try:
        return jsonify({"ok": True, "diagnostico": _con_links(dg.ver(_db(), did))})
    except dg.NoSePuede as ex:
        return _error(ex, 404)


@diagnostico_bp.route("/api/diagnosticos/<int:did>", methods=["PATCH"])
def api_editar(did):
    try:
        d = dg.editar(_db(), did, request.get_json(silent=True) or {})
    except dg.NoSePuede as ex:
        return _error(ex, 404 if "no existe" in str(ex) else 400)
    return jsonify({"ok": True, "diagnostico": _con_links(d)})


@diagnostico_bp.route("/api/diagnosticos/<int:did>/repetir", methods=["POST"])
def api_repetir(did):
    try:
        d = dg.repetir(_db(), did, session.get("user_name", "sistema"))
    except dg.NoSePuede as ex:
        return _error(ex, 404)
    return jsonify({"ok": True, "diagnostico": _con_links(d)})


@diagnostico_bp.route("/api/diagnosticos/<int:did>/nuevo-link", methods=["POST"])
def api_nuevo_link(did):
    try:
        return jsonify({"ok": True, "diagnostico": _con_links(dg.nuevo_codigo(_db(), did))})
    except dg.NoSePuede as ex:
        return _error(ex, 404)


@diagnostico_bp.route("/api/diagnosticos/<int:did>", methods=["DELETE"])
def api_borrar(did):
    try:
        dg.borrar(_db(), did)
    except dg.NoSePuede as ex:
        return _error(ex, 404)
    log_activity(_db(), session.get("user_name", "sistema"), "diagnostico_baja", "diagnostico", did, "", "",
                 user_id=session.get("user_id"))
    return jsonify({"ok": True})
