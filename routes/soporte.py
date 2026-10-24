"""Mesa de pedidos de los clientes de mantenimiento (services/soporte.py).

Dos grupos:
- `/api/soporte/...` para el equipo: piden el panel `soporte`.
- La página del cliente bajo `/pedidos/<código>`, que abre sin sesión (exenta
  en dashboard.require_login). La única llave es el código al azar del plan:
  uno que no existe o está pausado da 404 sin decir nada más. Muestra sus
  pedidos, los mensajes que no son notas internas y la bolsa de horas, nada
  de la ficha del cliente. No lleva scripts: son formularios comunes que
  vuelven a la misma página, así anda en cualquier celular. Los adjuntos no
  se bajan desde acá: solo el equipo.
"""

import html
import os
import secrets
from datetime import timedelta
from urllib.parse import quote

from flask import Blueprint, Response, current_app, jsonify, redirect, request, send_file, session

from database import log_activity
from services import soporte as sp
from services.auth import require_panel

soporte_bp = Blueprint("soporte", __name__)

PUBLICAS = ("/pedidos/",)
MAX_ENVIO = sp.MAX_ARCHIVOS_ENVIO * sp.MAX_ARCHIVO + 64 * 1024


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


@soporte_bp.before_request
def _candado():
    if request.path.startswith(PUBLICAS):
        return None
    return require_panel(_db(), "soporte")


def _con_link(p: dict) -> dict:
    p = dict(p)
    p["link"] = f"{_base()}/pedidos/{p['codigo']}"
    return p


def _codigo_error(e: sp.NoSePuede) -> int:
    return 404 if "no existe" in str(e) else 400


def _archivos():
    return [(a.filename, a.stream) for a in request.files.getlist("archivos") if a.filename]


# ── equipo ───────────────────────────────────────────────────────────────────

@soporte_bp.route("/api/soporte")
def api_listar():
    mes = request.args.get("mes")
    planes = [_con_link(p) for p in sp.planes(_db(), mes)]
    pedidos = sorted(sp.pedidos(_db()), key=sp.orden_bandeja)
    return jsonify({"ok": True, "pedidos": pedidos, "planes": planes, "resumen": sp.resumen(pedidos, planes),
                    "mes": planes[0]["mes"] if planes else sp._mes_valido(mes),
                    "prioridades": {k: {"nombre": v[0], "respuesta_h": v[1], "resolucion_h": v[2]}
                                    for k, v in sp.PRIORIDADES.items()},
                    "tipos": sp.TIPOS})


@soporte_bp.route("/api/soporte/clientes")
def api_clientes():
    return jsonify({"ok": True, "clientes": sp.clientes(_db())})


@soporte_bp.route("/api/soporte/planes", methods=["POST"])
def api_crear_plan():
    try:
        p = sp.crear_plan(_db(), request.get_json(silent=True) or {}, _quien())
    except sp.NoSePuede as e:
        return _error(e)
    log_activity(_db(), _quien(), "soporte_plan_alta", "soporte", p["id"], p["nombre"], f"{p['horas_mes']:g} h/mes",
                 user_id=session.get("user_id"))
    return jsonify({"ok": True, "plan": _con_link(p)}), 201


@soporte_bp.route("/api/soporte/planes/<int:pid>")
def api_plan(pid):
    try:
        p = sp.plan(_db(), pid, request.args.get("mes"))
    except sp.NoSePuede as e:
        return _error(e, 404)
    return jsonify({"ok": True, "plan": _con_link(p), "horas": sp.horas_del_mes(_db(), pid, p["mes"])})


@soporte_bp.route("/api/soporte/planes/<int:pid>", methods=["PATCH"])
def api_editar_plan(pid):
    try:
        p = sp.editar_plan(_db(), pid, request.get_json(silent=True) or {})
    except sp.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "plan": _con_link(p)})


@soporte_bp.route("/api/soporte/planes/<int:pid>", methods=["DELETE"])
def api_borrar_plan(pid):
    try:
        p = sp.plan(_db(), pid)
        sp.borrar_plan(_db(), pid)
    except sp.NoSePuede as e:
        return _error(e, 404)
    log_activity(_db(), _quien(), "soporte_plan_baja", "soporte", pid, p["nombre"], "", user_id=session.get("user_id"))
    return jsonify({"ok": True})


@soporte_bp.route("/api/soporte/planes/<int:pid>/pedidos", methods=["POST"])
def api_crear_pedido(pid):
    """El equipo carga un pedido que llegó por WhatsApp o por teléfono."""
    try:
        d = sp.crear_pedido(_db(), pid, request.get_json(silent=True) or {}, _quien(), de_cliente=False)
    except sp.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "pedido": d}), 201


@soporte_bp.route("/api/soporte/pedidos/<int:did>")
def api_pedido(did):
    try:
        return jsonify({"ok": True, "pedido": sp.pedido(_db(), did)})
    except sp.NoSePuede as e:
        return _error(e, 404)


@soporte_bp.route("/api/soporte/pedidos/<int:did>", methods=["PATCH"])
def api_cambiar(did):
    try:
        d = sp.cambiar(_db(), did, request.get_json(silent=True) or {})
    except sp.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "pedido": d})


@soporte_bp.route("/api/soporte/pedidos/<int:did>/mensajes", methods=["POST"])
def api_responder(did):
    if (request.content_length or 0) > MAX_ENVIO:
        return _error("Demasiado grande para un solo envío.", 413)
    if request.is_json:
        datos, archivos = request.get_json(silent=True) or {}, []
    else:
        datos, archivos = request.form, _archivos()
    interno = str(datos.get("interno", "")).lower() in ("1", "true", "on")
    try:
        d = sp.responder(_db(), did, datos.get("texto", ""), _quien(), de_cliente=False, interno=interno,
                         estado=datos.get("estado") or None, archivos=archivos)
    except sp.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "pedido": d})


@soporte_bp.route("/api/soporte/pedidos/<int:did>/horas", methods=["POST"])
def api_horas(did):
    datos = request.get_json(silent=True) or {}
    try:
        d = sp.cargar_horas(_db(), did, datos.get("horas"), datos.get("fecha"), datos.get("nota", ""), _quien())
    except sp.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "pedido": d})


@soporte_bp.route("/api/soporte/horas/<int:hid>", methods=["DELETE"])
def api_borrar_horas(hid):
    try:
        did = sp.borrar_horas(_db(), hid)
    except sp.NoSePuede as e:
        return _error(e, 404)
    return jsonify({"ok": True, "pedido": sp.pedido(_db(), did)})


@soporte_bp.route("/api/soporte/archivos/<int:aid>")
def api_bajar(aid):
    try:
        a = sp.archivo(_db(), aid)
        ruta = sp.ruta_archivo(_db(), a)
    except sp.NoSePuede as e:
        return _error(e, 404)
    if not os.path.exists(ruta):
        return _error("El archivo no está en el disco.", 404)
    # Siempre como adjunto y sin adivinar el tipo: lo que sube el cliente no
    # se abre en el origen del CRM.
    resp = send_file(ruta, as_attachment=True, download_name=sp.nombre_seguro(a["nombre"]),
                     mimetype="application/octet-stream")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return resp


# ── página del cliente ───────────────────────────────────────────────────────

_CSS = """
:root{--bg:#09090f;--surface:rgba(255,255,255,.04);--border:rgba(255,255,255,.07);--text:#e2e8f0;
--muted:#64748b;--blue:#0088CC;--blue-lt:#33aadd;--blue-dk:#005f8e;--blue-glow:rgba(0,136,204,.14);
--blue-border:rgba(0,136,204,.28);--green:#10B981;--green-lt:#34d399;--green-glow:rgba(16,185,129,.12);
--amber:#f59e0b;--red:#ef4444;
--ff:'Inter',system-ui,-apple-system,'Segoe UI',sans-serif;--ff-d:'Raleway','Inter',system-ui,sans-serif}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--ff);line-height:1.5}
a{color:var(--blue-lt)}
.wrap{max-width:760px;margin:0 auto;padding:32px 16px 56px}
.top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:28px}
.marca{font-family:var(--ff-d);font-weight:800;letter-spacing:.02em;font-size:1.1rem}
.marca span{color:var(--blue)}
.badge{border:1px solid var(--blue-border);background:var(--blue-glow);color:var(--blue-lt);border-radius:999px;
padding:4px 12px;font-size:.78rem;font-weight:600}
h1{font-family:var(--ff-d);font-size:clamp(1.5rem,4vw,2.1rem);margin:0 0 6px;line-height:1.2;overflow-wrap:anywhere}
h2{font-family:var(--ff-d);font-size:1.1rem;margin:28px 0 12px}
.sub{color:var(--muted);margin:0 0 20px}
.msj{white-space:pre-line;background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:14px 16px;
margin-bottom:20px;overflow-wrap:anywhere}
.bolsa{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:16px;margin-bottom:24px}
.bolsa-n{display:flex;justify-content:space-between;gap:10px;font-size:.9rem;margin-bottom:8px;flex-wrap:wrap}
.bolsa-n b{font-size:1.2rem;font-variant-numeric:tabular-nums}
.barra{height:10px;border-radius:999px;background:rgba(255,255,255,.07);overflow:hidden}
.barra span{display:block;height:100%;background:linear-gradient(90deg,var(--blue),var(--green))}
.barra.pasado span{background:var(--amber)}
.extra{color:var(--amber);font-size:.86rem;margin-top:8px}
.meses{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;font-size:.8rem}
.meses a{color:var(--muted);text-decoration:none;border:1px solid var(--border);border-radius:999px;padding:2px 10px}
.meses a[aria-current]{color:var(--blue-lt);border-color:var(--blue-border)}
details.cargas{margin-top:10px;font-size:.84rem;color:var(--muted)}
details.cargas summary{cursor:pointer}
details.cargas li{margin:4px 0}
.aviso{border-radius:12px;padding:12px 14px;margin-bottom:18px;font-size:.92rem}
.aviso.ok{background:var(--green-glow);border:1px solid rgba(16,185,129,.35);color:var(--green-lt)}
.aviso.mal{background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.35);color:#fca5a5}
.form{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:16px;margin-bottom:12px}
.ped{display:block;text-decoration:none;color:inherit;background:var(--surface);border:1px solid var(--border);
border-radius:14px;padding:14px 16px;margin-bottom:10px}
.ped:hover{border-color:var(--blue-border)}
.ped-cab{display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap}
.ped b{overflow-wrap:anywhere}
.ped-meta{color:var(--muted);font-size:.8rem;margin-top:4px}
.pill{border-radius:999px;padding:2px 10px;font-weight:600;font-size:.74rem;white-space:nowrap;
background:rgba(255,255,255,.06);color:var(--muted)}
.pill.en_curso,.pill.nuevo{background:var(--blue-glow);color:var(--blue-lt)}
.pill.hecho{background:var(--green-glow);color:var(--green-lt)}
.pill.esperando{background:rgba(245,158,11,.14);color:var(--amber)}
.hilo{display:flex;flex-direction:column;gap:10px;margin:16px 0}
.m{border-radius:12px;padding:10px 14px;max-width:90%;white-space:pre-line;overflow-wrap:anywhere;font-size:.92rem}
.m.cli{align-self:flex-end;background:var(--blue-glow);border:1px solid var(--blue-border)}
.m.eq{align-self:flex-start;background:var(--surface);border:1px solid var(--border)}
.m small{display:block;color:var(--muted);font-size:.74rem;margin-bottom:2px}
.adj{color:var(--muted);font-size:.8rem;margin-top:4px}
input[type=text],textarea,select,input[type=file]{width:100%;background:rgba(255,255,255,.04);
border:1px solid rgba(255,255,255,.12);color:var(--text);border-radius:10px;padding:10px;font-family:inherit;font-size:.92rem}
select option{background:#14141c}
textarea{min-height:110px;resize:vertical}
input[type=file]::file-selector-button{background:var(--blue-glow);color:var(--blue-lt);border:1px solid var(--blue-border);
border-radius:8px;padding:6px 12px;margin-right:10px;font-family:inherit;cursor:pointer}
label.l{display:block;font-size:.8rem;color:var(--muted);margin:10px 0 4px}
.fila{display:grid;grid-template-columns:1fr 1fr;gap:10px}
@media (max-width:520px){.fila{grid-template-columns:1fr}}
.acc{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:12px}
button{background:var(--blue);color:#fff;border:0;border-radius:10px;padding:10px 18px;font-weight:600;font-family:inherit;
font-size:.9rem;cursor:pointer}
button:hover{background:var(--blue-dk)}
button:focus-visible,a:focus-visible,textarea:focus-visible,input:focus-visible,select:focus-visible{
outline:2px solid var(--blue-lt);outline-offset:2px}
.nota{color:var(--muted);font-size:.78rem}
.vacio{color:var(--muted);font-size:.9rem}
.pie{margin-top:28px;color:var(--muted);font-size:.78rem;text-align:center}
"""

_ESTADO_CLIENTE = {"nuevo": "Recibido", "en_curso": "En curso", "esperando": "Te necesitamos",
                   "hecho": "Listo ✓", "descartado": "Cerrado"}
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
          "noviembre", "diciembre"]


def _cabeceras(resp: Response, nonce: str) -> Response:
    resp.headers["Content-Security-Policy"] = (
        f"default-src 'none'; style-src 'nonce-{nonce}'; img-src 'self' data:; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def _pagina(titulo: str, cuerpo: str, codigo: int = 200, css: str = "") -> Response:
    nonce = secrets.token_urlsafe(12)
    doc = ("<!doctype html><html lang='es'><head><meta charset='utf-8'>"
           "<meta name='viewport' content='width=device-width,initial-scale=1'>"
           "<meta name='robots' content='noindex,nofollow'>"
           f"<title>{html.escape(titulo)}</title><style nonce='{nonce}'>{_CSS}{css}</style></head>"
           f"<body><div class='wrap'>{cuerpo}</div></body></html>")
    return _cabeceras(Response(doc, status=codigo, mimetype="text/html"), nonce)


def _no_existe() -> Response:
    return _pagina("No encontrado · Scalerics",
                   "<h1>Este link no existe</h1><p class='sub'>Revisá el link o escribinos para pedir uno nuevo.</p>", 404)


def _h(n: float) -> str:
    """Horas para mostrar: 1,5 h; 2 h."""
    return f"{n:g}".replace(".", ",") + " h"


def _nombre_mes(mes: str) -> str:
    a, m = mes.split("-")
    return f"{_MESES[int(m) - 1]} {a}"


def _fecha(utc: str) -> str:
    d = sp._dt(utc)
    if not d:
        return ""
    d = d + sp.UY
    return f"{d.day} de {_MESES[d.month - 1]}, {d:%H:%M}"


def _ultimos_meses(n: int = 4) -> list:
    hoy = sp.hoy_uy().replace(day=1)
    salida = []
    for _ in range(n):
        salida.append(hoy.strftime("%Y-%m"))
        hoy = (hoy - timedelta(days=1)).replace(day=1)
    return salida


def _aviso() -> str:
    e = html.escape
    if request.args.get("ok") == "nuevo":
        return "<div class='aviso ok' role='status'>¡Recibido! Ya está en nuestra mesa y te contestamos acá.</div>"
    if request.args.get("ok"):
        return "<div class='aviso ok' role='status'>Listo, lo mandamos.</div>"
    if request.args.get("error"):
        return f"<div class='aviso mal' role='alert'>{e(request.args['error'][:300])}</div>"
    return ""


def _cabecera(p: dict) -> str:
    e = html.escape
    return (f"<div class='top'><div class='marca'>SCALE<span>RICS</span></div>"
            f"<div class='badge'>Plan de mantenimiento</div></div><h1>{e(p['nombre'])}</h1>")


def _bolsa(p: dict, codigo: str) -> str:
    e = html.escape
    mes = p["mes"]
    if p["horas_mes"]:
        cifra = f"<span><b>{_h(p['usadas'])}</b> usadas de {_h(p['horas_mes'])}</span>"
        lado = (f"<span>Te quedan <b>{_h(p['restantes'])}</b></span>" if not p["extra"]
                else "<span>Usaste todas las horas del plan</span>")
    else:
        cifra = f"<span><b>{_h(p['usadas'])}</b> trabajadas</span>"
        lado = "<span>Se cobran por hora</span>"
    extra = ""
    if p["extra"] and p["horas_mes"]:
        extra = (f"<div class='extra'>{_h(p['extra'])} por fuera del plan este mes. Te las detallamos en la "
                 "factura.</div>")
    cargas = sp.horas_del_mes(current_app.config["DB_PATH"], p["id"], mes)
    detalle = ""
    if cargas:
        filas = "".join(f"<li>{e(c['fecha'][8:10] + '/' + c['fecha'][5:7])} · {_h(c['horas'])} · "
                        f"#{int(c['numero'])} {e(c['titulo'])}{(' · ' + e(c['nota'])) if c['nota'] else ''}</li>"
                        for c in cargas)
        detalle = f"<details class='cargas'><summary>En qué se usaron</summary><ul>{filas}</ul></details>"
    actual = ' aria-current="page"'
    meses = "".join(f"<a href='/pedidos/{e(codigo)}?mes={m}'{actual if m == mes else ''}>"
                    f"{e(_nombre_mes(m))}</a>" for m in _ultimos_meses())
    clase = " pasado" if p["pasado"] else ""
    return (f"<div class='bolsa'><div class='bolsa-n'>{cifra}{lado}</div>"
            f"<div class='barra{clase}' role='progressbar' aria-valuemin='0' aria-valuemax='100' "
            f"aria-valuenow='{int(p['pct'])}' aria-label='Horas usadas en {e(_nombre_mes(mes))}'><span></span></div>"
            f"{extra}{detalle}<div class='meses'>{meses}</div></div>")


def _form_nuevo(codigo: str) -> str:
    e = html.escape
    tipos = "".join(f"<option value='{k}'>{e(v)}</option>" for k, v in sp.TIPOS.items())
    prios = "".join(f"<option value='{k}'{' selected' if k == 'normal' else ''}>{e(v[0])}</option>"
                    for k, v in sp.PRIORIDADES.items())
    return (f"<form class='form' method='post' action='/pedidos/{e(codigo)}' enctype='multipart/form-data'>"
            "<label class='l' for='titulo'>¿Qué necesitás? (una línea)</label>"
            f"<input type='text' id='titulo' name='titulo' maxlength='{sp.LARGO_TITULO}' required "
            "placeholder='Cambiar el precio del menú ejecutivo'>"
            "<label class='l' for='descripcion'>Contanos más (opcional)</label>"
            f"<textarea id='descripcion' name='descripcion' maxlength='{sp.LARGO_TEXTO}' "
            "placeholder='Dónde está, cómo tiene que quedar, desde cuándo pasa. Si tenés una captura, adjuntala.'>"
            "</textarea>"
            "<div class='fila'><div><label class='l' for='tipo'>Tipo</label>"
            f"<select id='tipo' name='tipo'>{tipos}</select></div>"
            f"<div><label class='l' for='prioridad'>Prioridad</label><select id='prioridad' name='prioridad'>{prios}"
            "</select></div></div>"
            f"<label class='l' for='archivos'>Capturas o archivos (hasta {sp.MAX_ARCHIVOS_ENVIO}, "
            f"{sp.MAX_ARCHIVO // (1024 * 1024)} MB cada uno)</label>"
            "<input type='file' id='archivos' name='archivos' multiple>"
            "<div class='acc'><button type='submit'>Mandar pedido</button></div></form>")


def _ped_html(codigo: str, d: dict) -> str:
    e = html.escape
    meta = [f"#{int(d['numero'])}", e(sp.TIPOS.get(d["tipo"], d["tipo"])), _fecha(d["creado_en"])]
    if d["horas"]:
        meta.append(_h(d["horas"]))
    if d["estado"] == "esperando":
        meta.append("<b>te hicimos una pregunta</b>")
    return (f"<a class='ped' href='/pedidos/{e(codigo)}/{int(d['numero'])}'><div class='ped-cab'><b>{e(d['titulo'])}</b>"
            f"<span class='pill {e(d['estado'])}'>{_ESTADO_CLIENTE.get(d['estado'], d['estado'])}</span></div>"
            f"<div class='ped-meta'>{' · '.join(meta)}</div></a>")


@soporte_bp.route("/pedidos/<codigo>")
def portal_publico(codigo):
    db = _db()
    p = sp.por_codigo(db, codigo)
    if not p:
        return _no_existe()
    sp.visita(db, p["id"])
    p = sp.plan(db, p["id"], request.args.get("mes"))
    e = html.escape
    todos = sp.pedidos(db, plan_id=p["id"], limite=100)
    abiertos = [d for d in todos if d["estado"] in sp.ABIERTOS]
    cerrados = [d for d in todos if d["estado"] not in sp.ABIERTOS][:20]
    lista = "".join(_ped_html(codigo, d) for d in abiertos) or "<p class='vacio'>No tenés pedidos abiertos.</p>"
    viejos = ""
    if cerrados:
        viejos = "<h2>Resueltos</h2>" + "".join(_ped_html(codigo, d) for d in cerrados)
    cuerpo = (
        _cabecera(p)
        + "<p class='sub'>Pedinos cambios acá: queda todo anotado, ves en qué está cada uno y cuántas horas del plan "
          "llevamos este mes.</p>"
        + (f"<div class='msj'>{e(p['mensaje'])}</div>" if p.get("mensaje") else "")
        + _aviso()
        + _bolsa(p, codigo)
        + "<h2>Pedir algo</h2>" + _form_nuevo(codigo)
        + "<p class='nota'>Te contestamos en esta página. Si algo dejó de andar, marcalo urgente. No escribas "
          "contraseñas: si hacen falta, te escribimos aparte.</p>"
        + "<h2>Tus pedidos</h2>" + lista + viejos
        + "<p class='pie'>Scalerics · scalerics.com</p>")
    return _pagina(f"{p['nombre']} · Pedidos · Scalerics", cuerpo, css=f".barra span{{width:{int(p['pct'])}%}}")


def _volver(url: str, error: str = "", ok: str = "1"):
    q = f"?error={quote(error[:300])}" if error else f"?ok={ok}"
    return redirect(url + q, code=303)


@soporte_bp.route("/pedidos/<codigo>", methods=["POST"])
def portal_crear(codigo):
    p = sp.por_codigo(_db(), codigo)
    if not p:
        return _no_existe()
    if (request.content_length or 0) > MAX_ENVIO:
        return _volver(f"/pedidos/{codigo}", "Es demasiado para un solo envío: mandá menos archivos.")
    try:
        d = sp.crear_pedido(_db(), p["id"], request.form, "cliente", de_cliente=True, archivos=_archivos())
    except sp.NoSePuede as e:
        return _volver(f"/pedidos/{codigo}", str(e))
    log_activity(_db(), "cliente", "soporte_pedido", "soporte", d["id"], p["nombre"], d["titulo"])
    return _volver(f"/pedidos/{codigo}/{int(d['numero'])}", ok="nuevo")


@soporte_bp.route("/pedidos/<codigo>/<int:numero>")
def portal_pedido(codigo, numero):
    db = _db()
    p = sp.por_codigo(db, codigo)
    if not p:
        return _no_existe()
    d = sp.pedido_por_numero(db, p["id"], numero)
    if not d:
        return _no_existe()
    sp.visita(db, p["id"])
    e = html.escape
    adj_por_msj = {}
    for a in d["archivos"]:
        adj_por_msj.setdefault(a["mensaje_id"], []).append(a["nombre"])

    def adjuntos(mid):
        nombres = adj_por_msj.get(mid)
        return f"<div class='adj'>Adjuntos: {e(', '.join(nombres))}</div>" if nombres else ""

    hilo = [f"<div class='m cli'><small>Vos · {_fecha(d['creado_en'])}</small>"
            f"{e(d['descripcion']) if d['descripcion'] else '<i>Sin detalle</i>'}{adjuntos(None)}</div>"]
    for m in d["mensajes"]:
        quien = "Vos" if m["de_cliente"] else f"{e(m['quien'])} · Scalerics"
        hilo.append(f"<div class='m {'cli' if m['de_cliente'] else 'eq'}'><small>{quien} · {_fecha(m['creado_en'])}"
                    f"</small>{e(m['texto'])}{adjuntos(m['id'])}</div>")
    horas = ""
    if d["cargas"]:
        horas = (f"<p class='nota'>Llevamos {_h(d['horas'])} en este pedido: "
                 + "; ".join(f"{e(c['fecha'][8:10] + '/' + c['fecha'][5:7])} {_h(c['horas'])}"
                             + (f" ({e(c['nota'])})" if c["nota"] else "") for c in d["cargas"]) + ".</p>")
    if d["estado"] == "descartado":
        form = "<p class='nota'>Este pedido está cerrado. Si necesitás algo más, abrí uno nuevo.</p>"
    else:
        etiqueta = "Contestanos" if d["estado"] == "esperando" else (
            "¿Algo no quedó bien? Contanos y lo reabrimos" if d["estado"] == "hecho" else "Agregar algo")
        form = (f"<form class='form' method='post' action='/pedidos/{e(codigo)}/{int(numero)}' "
                "enctype='multipart/form-data'>"
                f"<label class='l' for='texto'>{etiqueta}</label>"
                f"<textarea id='texto' name='texto' maxlength='{sp.LARGO_TEXTO}'></textarea>"
                "<label class='l' for='archivos'>Capturas o archivos (opcional)</label>"
                "<input type='file' id='archivos' name='archivos' multiple>"
                "<div class='acc'><button type='submit'>Mandar</button></div></form>")
    cuerpo = (
        _cabecera(p)
        + f"<p class='sub'><a href='/pedidos/{e(codigo)}'>← Todos tus pedidos</a></p>"
        + _aviso()
        + f"<div class='ped-cab'><h2>#{int(d['numero'])} · {e(d['titulo'])}</h2>"
          f"<span class='pill {e(d['estado'])}'>{_ESTADO_CLIENTE.get(d['estado'], d['estado'])}</span></div>"
        + f"<p class='nota'>{e(sp.TIPOS.get(d['tipo'], ''))} · {e(sp.PRIORIDADES[d['prioridad']][0])}</p>"
        + "<div class='hilo'>" + "".join(hilo) + "</div>"
        + horas + form
        + "<p class='pie'>Scalerics · scalerics.com</p>")
    return _pagina(f"#{numero} {d['titulo']} · Scalerics", cuerpo)


@soporte_bp.route("/pedidos/<codigo>/<int:numero>", methods=["POST"])
def portal_responder(codigo, numero):
    db = _db()
    p = sp.por_codigo(db, codigo)
    if not p:
        return _no_existe()
    d = sp.pedido_por_numero(db, p["id"], numero)
    if not d:
        return _no_existe()
    url = f"/pedidos/{codigo}/{int(numero)}"
    if (request.content_length or 0) > MAX_ENVIO:
        return _volver(url, "Es demasiado para un solo envío: mandá menos archivos.")
    try:
        sp.responder(db, d["id"], request.form.get("texto", ""), "cliente", de_cliente=True, archivos=_archivos())
    except sp.NoSePuede as e:
        return _volver(url, str(e))
    log_activity(db, "cliente", "soporte_respuesta", "soporte", d["id"], p["nombre"], d["titulo"])
    return _volver(url)
