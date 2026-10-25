"""Pedidos directos: carta online con pedidos para comercios (services/pedidos.py).

Dos grupos:
- `/api/pedidos/...` y la comanda para imprimir `/pedidos/comanda/...` son del
  equipo: piden el panel `pedidos`.
- La carta del comercio, `/pedir/<slug>`, abre sin sesión (exenta en
  dashboard.require_login): es pública a propósito, va en el Instagram del
  comercio, en su Google y en un QR en la caja. Cada pedido tiene su link
  `/pedir/<slug>/p/<código>` con un código al azar para seguirlo; sin ese
  código no se ve nada de nadie.

La página no lleva scripts: el carrito viaja en la URL y cada botón es un
formulario común que vuelve a la carta, así anda en cualquier celular y con
CSP estricta. La página del pedido se recarga sola con un meta refresh.
"""

import html
import os
import secrets
from urllib.parse import urlencode

from flask import Blueprint, Response, current_app, jsonify, redirect, request, session

from database import log_activity
from services import pedidos as pd
from services.auth import require_panel

pedidos_bp = Blueprint("pedidos", __name__)

PUBLICAS = ("/pedir/",)


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


def _codigo_error(e: pd.NoSePuede) -> int:
    return 404 if "no existe" in str(e) else 400


@pedidos_bp.before_request
def _candado():
    if request.path.startswith(PUBLICAS):
        return None
    return require_panel(_db(), "pedidos")


# ── equipo ───────────────────────────────────────────────────────────────────

def _con_link(loc: dict) -> dict:
    loc = dict(loc)
    loc["link"] = f"{_base()}/pedir/{loc['slug']}"
    loc["apertura"] = pd.estado_apertura(loc)
    loc["pagos_disponibles"] = pd.PAGOS
    return loc


def _pedido_json(loc: dict, p: dict) -> dict:
    p = dict(p)
    p.pop("ip", None)
    p["link"] = pd.link_pedido(_base(), loc, p)
    p["whatsapp"] = pd.whatsapp_cliente(_base(), loc, p)
    return p


@pedidos_bp.route("/api/pedidos")
def api_locales():
    return jsonify({"ok": True, "locales": [_con_link(x) for x in pd.locales(_db())]})


@pedidos_bp.route("/api/pedidos/locales", methods=["POST"])
def api_crear():
    try:
        loc = pd.crear_local(_db(), request.get_json(silent=True) or {}, _quien())
    except pd.NoSePuede as e:
        return _error(e)
    log_activity(_db(), _quien(), "pedidos_alta", "pedidos", loc["id"], loc["nombre"], loc["slug"],
                 user_id=session.get("user_id"))
    return jsonify({"ok": True, "local": _con_link(loc)}), 201


@pedidos_bp.route("/api/pedidos/locales/<int:lid>")
def api_local(lid):
    try:
        loc = pd.local(_db(), lid)
    except pd.NoSePuede as e:
        return _error(e, 404)
    return jsonify({"ok": True, "local": _con_link(loc), "carta": pd.carta(loc)})


@pedidos_bp.route("/api/pedidos/locales/<int:lid>", methods=["PATCH"])
def api_editar(lid):
    try:
        loc = pd.editar_local(_db(), lid, request.get_json(silent=True) or {})
    except pd.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "local": _con_link(loc)})


@pedidos_bp.route("/api/pedidos/locales/<int:lid>", methods=["DELETE"])
def api_borrar(lid):
    try:
        loc = pd.local(_db(), lid, completo=False)
        pd.borrar_local(_db(), lid)
    except pd.NoSePuede as e:
        return _error(e, 404)
    log_activity(_db(), _quien(), "pedidos_baja", "pedidos", lid, loc["nombre"], "", user_id=session.get("user_id"))
    return jsonify({"ok": True})


@pedidos_bp.route("/api/pedidos/locales/<int:lid>/tablero")
def api_tablero(lid):
    try:
        loc = pd.local(_db(), lid, completo=False)
        t = pd.tablero(_db(), lid)
    except pd.NoSePuede as e:
        return _error(e, 404)
    t["columnas"] = {k: [_pedido_json(loc, p) for p in v] for k, v in t["columnas"].items()}
    return jsonify({"ok": True, "apertura": pd.estado_apertura(loc), **t})


@pedidos_bp.route("/api/pedidos/locales/<int:lid>/historial")
def api_historial(lid):
    try:
        loc = pd.local(_db(), lid, completo=False)
        dias = max(1, min(366, int(request.args.get("dias") or 30)))
        ps = pd.historial(_db(), lid, dias, request.args.get("q") or "")
    except (pd.NoSePuede, ValueError) as e:
        return _error(e, 404 if isinstance(e, pd.NoSePuede) else 400)
    return jsonify({"ok": True, "pedidos": [_pedido_json(loc, p) for p in ps]})


@pedidos_bp.route("/api/pedidos/locales/<int:lid>/metricas")
def api_metricas(lid):
    try:
        dias = max(7, min(366, int(request.args.get("dias") or 30)))
        m = pd.metricas(_db(), lid, dias)
    except (pd.NoSePuede, ValueError) as e:
        return _error(e, 404 if isinstance(e, pd.NoSePuede) else 400)
    return jsonify({"ok": True, "metricas": m})


@pedidos_bp.route("/api/pedidos/locales/<int:lid>/pedidos/<int:pid>", methods=["PATCH"])
def api_avanzar(lid, pid):
    d = request.get_json(silent=True) or {}
    try:
        p = pd.avanzar(_db(), lid, pid, str(d.get("estado") or ""), _quien(), d.get("demora_min"), d.get("motivo"))
        loc = pd.local(_db(), lid, completo=False)
    except pd.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "pedido": _pedido_json(loc, p)})


def _crud(fn_guardar, fn_borrar, clave: str, lid: int, oid=None):
    try:
        if request.method == "DELETE":
            fn_borrar(_db(), lid, oid)
            return jsonify({"ok": True})
        x = fn_guardar(_db(), lid, request.get_json(silent=True) or {}, oid)
    except pd.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, clave: x}), 201 if request.method == "POST" else 200


@pedidos_bp.route("/api/pedidos/locales/<int:lid>/zonas", methods=["POST"])
@pedidos_bp.route("/api/pedidos/locales/<int:lid>/zonas/<int:oid>", methods=["PUT", "DELETE"])
def api_zona(lid, oid=None):
    return _crud(pd.guardar_zona, pd.borrar_zona, "zona", lid, oid)


@pedidos_bp.route("/api/pedidos/locales/<int:lid>/categorias", methods=["POST"])
@pedidos_bp.route("/api/pedidos/locales/<int:lid>/categorias/<int:oid>", methods=["PUT", "DELETE"])
def api_categoria(lid, oid=None):
    return _crud(pd.guardar_categoria, pd.borrar_categoria, "categoria", lid, oid)


@pedidos_bp.route("/api/pedidos/locales/<int:lid>/productos", methods=["POST"])
@pedidos_bp.route("/api/pedidos/locales/<int:lid>/productos/<int:oid>", methods=["PATCH", "DELETE"])
def api_producto(lid, oid=None):
    return _crud(pd.guardar_producto, pd.borrar_producto, "producto", lid, oid)


@pedidos_bp.route("/pedidos/comanda/<int:lid>/<int:pid>")
def comanda(lid, pid):
    """La comanda para la impresora térmica de la cocina (80 mm)."""
    try:
        loc = pd.local(_db(), lid, completo=False)
        p = pd.pedido(_db(), lid, pid)
    except pd.NoSePuede:
        return _no_existe()
    e = html.escape
    items = "".join(f"<tr><td class='c'>{it['cantidad']}</td><td><b>{e(it['nombre'])}</b>"
                    + (f"<div>{e(it['opciones'])}</div>" if it.get("opciones") else "")
                    + f"</td><td class='r'>{e(pd.plata(it['subtotal']))}</td></tr>" for it in p["items"])
    entrega = (f"<p><b>DELIVERY</b> · {e(p['zona'] or '')}<br>{e(p['direccion'] or '')}</p>" if p["tipo"] == "delivery"
               else "<p><b>RETIRA EN EL LOCAL</b></p>")
    pago = e(p["pago_texto"]) + (f" · paga con {e(pd.plata(p['paga_con']))}" if p.get("paga_con") else "")
    cuerpo = (f"<div class='comanda'><h1>#{p['numero']}</h1><p>{e(loc['nombre'])} · {e(p['creado_en'])}</p>"
              f"<p><b>{e(p['cliente'])}</b> · {e(p['telefono_lindo'])}</p>{entrega}"
              f"<table>{items}</table>"
              + (f"<p class='nota'>Nota: {e(p['nota'])}</p>" if p.get("nota") else "")
              + f"<p class='r'>Subtotal {e(pd.plata(p['subtotal']))}"
              + (f"<br>Envío {e(pd.plata(p['envio']))}" if p["envio"] else "")
              + f"<br><b>TOTAL {e(pd.plata(p['total']))}</b></p><p>Pago: {pago}</p></div>")
    return _pagina(loc, f"Comanda #{p['numero']}", cuerpo, comanda=True)


# ── página pública ───────────────────────────────────────────────────────────

_CSS = """
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;background:#f6f7f9;color:#0f172a;line-height:1.4}
.wrap{max-width:640px;margin:0 auto;padding:16px 16px 96px}
a{color:var(--acc)}
.cab{display:flex;gap:12px;align-items:center;margin:4px 0 12px}
.logo{width:52px;height:52px;border-radius:14px;background:var(--acc);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:1.5rem;flex:none}
h1{font-size:1.35rem;margin:0}
h2{font-size:1.05rem;margin:22px 0 8px}
.sub{color:#64748b;font-size:.88rem;margin:2px 0}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}
.chip{background:#fff;border:1px solid #e2e8f0;border-radius:999px;padding:4px 10px;font-size:.8rem;color:#334155}
.chip.ok{background:#dcfce7;border-color:#bbf7d0;color:#166534;font-weight:600}
.chip.no{background:#fee2e2;border-color:#fecaca;color:#991b1b;font-weight:600}
.msg{background:#fff;border-radius:12px;padding:10px 12px;font-size:.88rem;color:#334155;border:1px solid #e2e8f0}
.cats{display:flex;gap:6px;overflow-x:auto;padding:6px 0;position:sticky;top:0;background:#f6f7f9;z-index:2}
.cats a{white-space:nowrap;text-decoration:none;background:#fff;border:1px solid #e2e8f0;border-radius:999px;padding:6px 12px;font-size:.84rem;color:#0f172a}
.prod{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:12px;margin:8px 0}
.prod.agotado{opacity:.55}
.fila{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}
.nom{font-weight:700}
.desc{color:#64748b;font-size:.84rem;margin-top:2px}
.precio{font-weight:700;white-space:nowrap}
.dest{background:var(--acc);color:#fff;font-size:.68rem;border-radius:6px;padding:1px 6px;margin-left:6px;vertical-align:middle}
form.add{display:flex;gap:8px;align-items:center;margin-top:8px;flex-wrap:wrap}
details{margin-top:8px}
summary{cursor:pointer;font-weight:600;color:var(--acc);list-style:none;padding:6px 0}
summary::-webkit-details-marker{display:none}
fieldset{border:0;margin:6px 0;padding:0}
legend{font-weight:600;font-size:.86rem;margin-bottom:4px}
.op{display:flex;gap:8px;align-items:center;padding:6px 0;font-size:.9rem;border-bottom:1px dashed #e2e8f0}
.op span{margin-left:auto;color:#64748b;font-size:.84rem}
select,input[type=text],input[type=tel],input[type=number],textarea{font:inherit;padding:10px;border:1px solid #cbd5e1;border-radius:10px;background:#fff;width:100%}
select.cant{width:auto;padding:8px}
textarea{min-height:64px}
.btn{display:inline-block;background:var(--acc);color:#fff;border:0;border-radius:12px;padding:11px 16px;font:inherit;font-weight:700;text-decoration:none;cursor:pointer;text-align:center}
.btn.sec{background:#fff;color:#0f172a;border:1px solid #cbd5e1}
.btn.ancho{display:block;width:100%}
.btn.wa{background:#16a34a}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:14px;margin:12px 0}
.lin{display:flex;gap:8px;align-items:center;padding:8px 0;border-bottom:1px solid #f1f5f9}
.lin .t{flex:1;min-width:0}
.lin form{display:inline}
.mini{width:32px;height:32px;border-radius:9px;border:1px solid #cbd5e1;background:#fff;font:inherit;font-weight:700;cursor:pointer}
.tot{display:flex;justify-content:space-between;padding:4px 0}
.tot.g{font-size:1.1rem;font-weight:800;border-top:1px solid #e2e8f0;margin-top:4px;padding-top:8px}
.barra{position:fixed;left:0;right:0;bottom:0;padding:10px 16px;background:linear-gradient(transparent,#f6f7f9 30%)}
.barra .btn{max-width:608px;margin:0 auto;display:flex;justify-content:space-between}
label.l{display:block;font-weight:600;font-size:.86rem;margin:12px 0 4px}
.radio{display:flex;gap:10px;align-items:flex-start;border:1px solid #e2e8f0;border-radius:12px;padding:10px;margin:6px 0;background:#fff}
.radio small{display:block;color:#64748b}
.aviso{border-radius:12px;padding:10px 12px;margin:10px 0;font-size:.9rem}
.aviso.mal{background:#fee2e2;color:#991b1b}
.aviso.ok{background:#dcfce7;color:#166534}
.trampa{position:absolute;left:-9999px}
.num{font-size:2.6rem;font-weight:800;margin:0;color:var(--acc)}
.pasos{display:flex;gap:4px;margin:12px 0}
.paso{flex:1;text-align:center;font-size:.72rem;color:#94a3b8}
.paso i{display:block;height:6px;border-radius:3px;background:#e2e8f0;margin-bottom:4px}
.paso.hecho{color:#0f172a;font-weight:600}
.paso.hecho i{background:var(--acc)}
.centro{text-align:center}
.pie{text-align:center;color:#94a3b8;font-size:.75rem;margin-top:28px}
.pie a{color:#94a3b8}
.comanda{font-family:ui-monospace,Menlo,Consolas,monospace;max-width:300px;margin:0 auto;font-size:13px;color:#000}
.comanda h1{font-size:28px}
.comanda table{width:100%;border-collapse:collapse;margin:8px 0}
.comanda td{border-top:1px dashed #000;padding:4px 2px;vertical-align:top}
.comanda .c{width:24px;font-weight:700}
.comanda .r,.comanda td.r{text-align:right}
.comanda .nota{border:2px solid #000;padding:6px;font-weight:700}
@media print{body{background:#fff}.wrap{padding:0}.pie{display:none}}
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


def _pagina(loc, titulo: str, cuerpo: str, codigo: int = 200, comanda: bool = False,
            refresco: int = 0) -> Response:
    nonce = secrets.token_urlsafe(12)
    color = pd._color(loc.get("color") if loc else None)
    pie = "" if comanda else "<p class='pie'>Pedidos sin comisión con <a href='https://scalerics.com'>Scalerics</a></p>"
    meta = f"<meta http-equiv='refresh' content='{refresco}'>" if refresco else ""
    doc = ("<!doctype html><html lang='es'><head><meta charset='utf-8'>"
           "<meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<meta name='theme-color' content='{color}'><meta name='robots' content='noindex,nofollow'>{meta}"
           f"<title>{html.escape(titulo)}</title><style nonce='{nonce}'>:root{{--acc:{color}}}{_CSS}</style></head>"
           f"<body><div class='wrap'>{cuerpo}{pie}</div></body></html>")
    return _cabeceras(Response(doc, status=codigo, mimetype="text/html"), nonce)


def _no_existe() -> Response:
    return _pagina(None, "No encontrado", "<div class='cab'><h1>Este link no existe</h1></div>"
                   "<p class='sub'>Revisalo o pedíselo al comercio.</p>", 404)


def _cabecera(loc: dict, sub: str = "") -> str:
    e = html.escape
    inicial = e((loc["nombre"].strip()[:1] or "·").upper())
    detalle = sub or " · ".join(e(x) for x in (loc.get("rubro"), loc.get("direccion")) if x)
    return (f"<div class='cab'><div class='logo' aria-hidden='true'>{inicial}</div>"
            f"<div><h1>{e(loc['nombre'])}</h1>{f'<p class=sub>{detalle}</p>' if detalle else ''}</div></div>")


def _ip() -> str:
    return request.headers.get("Fly-Client-IP") or request.remote_addr or ""


def _url_carta(loc: dict, c: str, ancla: str = "") -> str:
    return f"/pedir/{loc['slug']}" + (f"?{urlencode({'c': c})}" if c else "") + ancla


def _local_publico(slug: str):
    loc = pd.por_slug(_db(), slug)
    return loc if loc and loc["activo"] else None


def _carrito_html(loc: dict, lineas: list, c: str, editable: bool = True) -> str:
    e = html.escape
    if not lineas:
        return ""
    filas = []
    for i, ln in enumerate(lineas):
        botones = ""
        if editable:
            base = f"action='/pedir/{e(loc['slug'])}/cantidad' method='post'"
            ocultos = f"<input type='hidden' name='c' value='{e(c)}'><input type='hidden' name='i' value='{i}'>"
            botones = (f"<form {base}>{ocultos}<input type='hidden' name='d' value='-1'>"
                       f"<button class='mini' aria-label='Uno menos de {e(ln['nombre'])}'>−</button></form>"
                       f"<b>{ln['cantidad']}</b>"
                       f"<form {base}>{ocultos}<input type='hidden' name='d' value='1'>"
                       f"<button class='mini' aria-label='Uno más de {e(ln['nombre'])}'>+</button></form>")
        else:
            botones = f"<b>{ln['cantidad']} ×</b>"
        filas.append(f"<div class='lin'>{botones}<div class='t'>{e(ln['nombre'])}"
                     + (f"<div class='sub'>{e(ln['opciones'])}</div>" if ln["opciones"] else "")
                     + f"</div><div class='precio'>{e(pd.plata(ln['subtotal']))}</div></div>")
    return "".join(filas)


@pedidos_bp.route("/pedir/<slug>")
def carta(slug):
    loc = _local_publico(slug)
    if not loc:
        return _no_existe()
    e = html.escape
    c = (request.args.get("c") or "")[:600]
    lineas = pd.leer_carrito(loc, c)
    c = pd.carrito_texto(lineas)
    ap = pd.estado_apertura(loc)
    error = request.args.get("error")
    chips = [f"<span class='chip {'ok' if ap['abierto'] else 'no'}'>{e(ap['texto'])}</span>"]
    if loc["delivery"]:
        chips.append(f"<span class='chip'>Delivery ~{loc['demora_delivery']} min</span>")
    if loc["retiro"]:
        chips.append(f"<span class='chip'>Retiro ~{loc['demora_retiro']} min</span>")
    if loc["pedido_minimo"] and loc["delivery"]:
        chips.append(f"<span class='chip'>Mínimo delivery {e(pd.plata(loc['pedido_minimo']))}</span>")
    secciones = pd.carta(loc)
    partes = [_cabecera(loc), "<div class='chips'>" + "".join(chips) + "</div>"]
    if loc.get("mensaje"):
        partes.append(f"<div class='msg'>{e(loc['mensaje'])}</div>")
    if error:
        partes.append(f"<div class='aviso mal' role='alert'>{e(error[:300])}</div>")
    if not secciones:
        partes.append("<div class='card centro'>La carta todavía está vacía.</div>")
    if len(secciones) > 1:
        partes.append("<nav class='cats' aria-label='Categorías'>" + "".join(
            f"<a href='#cat-{i}'>{e(s['nombre'])}</a>" for i, s in enumerate(secciones)) + "</nav>")
    for i, s in enumerate(secciones):
        partes.append(f"<h2 id='cat-{i}'>{e(s['nombre'])}</h2>")
        for p in s["productos"]:
            partes.append(_producto_html(loc, p, c))
    if lineas:
        cot = pd.cotizar(loc, lineas)
        cant = sum(ln["cantidad"] for ln in lineas)
        partes.append("<div class='card' id='carrito'><h2>Tu pedido</h2>")
        partes.append(_carrito_html(loc, lineas, c))
        partes.append(f"<div class='tot g'><span>Subtotal</span><span>{e(pd.plata(cot['subtotal']))}</span></div>"
                      "<p class='sub'>El envío se suma en el paso siguiente, según tu barrio.</p>" if loc["delivery"]
                      else f"<div class='tot g'><span>Total</span><span>{e(pd.plata(cot['subtotal']))}</span></div>")
        if ap["abierto"]:
            partes.append(f"<p><a class='btn ancho' href='/pedir/{e(loc['slug'])}/datos?{e(urlencode({'c': c}))}'>"
                          "Seguir con el pedido</a></p>")
        else:
            partes.append(f"<div class='aviso mal'>{e(ap['texto'])}. Podés armar el pedido y mandarlo cuando abra.</div>")
        partes.append("</div>")
        partes.append(f"<div class='barra'><a class='btn' href='#carrito'><span>Ver pedido · {cant}</span>"
                      f"<span>{e(pd.plata(cot['subtotal']))}</span></a></div>")
    return _pagina(loc, f"Pedí online · {loc['nombre']}", "".join(partes))


def _producto_html(loc: dict, p: dict, c: str) -> str:
    e = html.escape
    cab = (f"<div class='fila'><div><div class='nom'>{e(p['nombre'])}"
           + ("<span class='dest'>Recomendado</span>" if p["destacado"] else "") + "</div>"
           + (f"<div class='desc'>{e(p['descripcion'])}</div>" if p.get("descripcion") else "")
           + f"</div><div class='precio'>{e(pd.plata(p['precio']))}</div></div>")
    if not p["disponible"]:
        return f"<div class='prod agotado'>{cab}<p class='sub'>Agotado por hoy</p></div>"
    cantidades = "".join(f"<option value='{i}'>{i}</option>" for i in range(1, 11))
    accion = (f"<input type='hidden' name='c' value='{e(c)}'><input type='hidden' name='p' value='{p['id']}'>"
              f"<select name='n' class='cant' aria-label='Cantidad de {e(p['nombre'])}'>{cantidades}</select>"
              f"<button class='btn' type='submit'>Agregar</button>")
    form = f"action='/pedir/{e(loc['slug'])}/agregar' method='post'"
    if not p["opciones"]:
        return f"<div class='prod'>{cab}<form class='add' {form}>{accion}</form></div>"
    grupos = []
    for g, grupo in enumerate(p["opciones"]):
        tipo = "checkbox" if grupo["varios"] else "radio"
        ops = "".join(
            f"<label class='op'><input type='{tipo}' name='o' value='{g}.{i}'"
            + (" checked" if not grupo["varios"] and i == 0 else "") + f"> {e(it['nombre'])}"
            + (f"<span>+{e(pd.plata(it['precio']))}</span>" if it["precio"] else "") + "</label>"
            for i, it in enumerate(grupo["items"]))
        # Los radios de cada grupo necesitan su propio name para no pisarse entre grupos.
        if not grupo["varios"]:
            ops = ops.replace("name='o'", f"name='o{g}'")
        grupos.append(f"<fieldset><legend>{e(grupo['nombre'])}"
                      + (" <span class='sub'>(opcional)</span>" if grupo["varios"] else "") + f"</legend>{ops}</fieldset>")
    return (f"<div class='prod'>{cab}<details><summary>Elegir y agregar</summary>"
            f"<form {form}>{''.join(grupos)}<div class='add'>{accion}</div></form></details></div>")


def _seleccion(form) -> list[tuple[int, int]]:
    sel = []
    for k in form:
        if k == "o" or (k.startswith("o") and k[1:].isdigit()):
            for v in form.getlist(k):
                partes = str(v).split(".")
                if len(partes) == 2 and all(x.isdigit() for x in partes):
                    sel.append((int(partes[0]), int(partes[1])))
    return sel


@pedidos_bp.route("/pedir/<slug>/agregar", methods=["POST"])
def agregar(slug):
    loc = _local_publico(slug)
    if not loc:
        return _no_existe()
    c = (request.form.get("c") or "")[:600]
    try:
        c = pd.agregar(loc, c, request.form.get("p"), request.form.get("n"), _seleccion(request.form))
    except pd.NoSePuede as err:
        return redirect(f"/pedir/{loc['slug']}?" + urlencode({"c": c, "error": str(err)}), 303)
    return redirect(_url_carta(loc, c, "#carrito"), 303)


@pedidos_bp.route("/pedir/<slug>/cantidad", methods=["POST"])
def cantidad(slug):
    loc = _local_publico(slug)
    if not loc:
        return _no_existe()
    c = pd.cambiar_cantidad(loc, (request.form.get("c") or "")[:600], request.form.get("i"), request.form.get("d"))
    return redirect(_url_carta(loc, c, "#carrito" if c else ""), 303)


def _datos_html(loc: dict, c: str, lineas: list, v: dict, error: str = "") -> str:
    e = html.escape
    cot = pd.cotizar(loc, lineas)
    tipos = [t for t in pd.TIPOS if loc[t]]
    tipo = v.get("tipo") if v.get("tipo") in tipos else tipos[0]
    partes = [_cabecera(loc, "Últimos datos y listo")]
    if error:
        partes.append(f"<div class='aviso mal' role='alert'>{e(error)}</div>")
    partes.append("<div class='card'><h2>Tu pedido</h2>" + _carrito_html(loc, lineas, c, editable=False)
                  + f"<div class='tot g'><span>Subtotal</span><span>{e(pd.plata(cot['subtotal']))}</span></div>"
                  + f"<p><a href='{e(_url_carta(loc, c, '#carrito'))}'>Cambiar algo</a></p></div>")
    f = [f"<form class='card' method='post' action='/pedir/{e(loc['slug'])}/confirmar'>",
         f"<input type='hidden' name='c' value='{e(c)}'>",
         "<div class='trampa' aria-hidden='true'><label>No completar<input type='text' name='web' tabindex='-1' "
         "autocomplete='off'></label></div>",
         "<label class='l' for='cl'>Tu nombre</label>"
         f"<input type='text' id='cl' name='cliente' maxlength='80' required autocomplete='name' value='{e(v.get('cliente', ''))}'>",
         "<label class='l' for='tel'>Celular (por WhatsApp te avisan del pedido)</label>"
         f"<input type='tel' id='tel' name='telefono' required autocomplete='tel' placeholder='099 123 456' value='{e(v.get('telefono', ''))}'>"]
    if len(tipos) > 1:
        f.append("<label class='l'>¿Cómo lo querés?</label>")
        for t in tipos:
            demora = loc["demora_delivery"] if t == "delivery" else loc["demora_retiro"]
            nombre = "Delivery" if t == "delivery" else "Retiro en el local"
            extra = f"Llega en ~{demora} min" if t == "delivery" else (
                f"Listo en ~{demora} min" + (f" · {e(loc['direccion'])}" if loc.get("direccion") else ""))
            f.append(f"<label class='radio'><input type='radio' name='tipo' value='{t}'"
                     f"{' checked' if t == tipo else ''}><span><b>{nombre}</b><small>{extra}</small></span></label>")
    else:
        f.append(f"<input type='hidden' name='tipo' value='{tipos[0]}'>")
    if loc["delivery"]:
        f.append("<div><p class='sub'>Si es delivery:</p>" if len(tipos) > 1 else "<div>")
        if loc["zonas"]:
            ops = "".join(
                f"<option value='{z['id']}'{' selected' if str(z['id']) == str(v.get('zona_id')) else ''}>"
                f"{e(z['nombre'])} · envío {e(pd.plata(z['costo'])) if z['costo'] else 'gratis'}"
                + (f" · mínimo {e(pd.plata(z['minimo']))}" if z["minimo"] else "") + "</option>" for z in loc["zonas"])
            f.append("<label class='l' for='zn'>Barrio</label>"
                     f"<select id='zn' name='zona_id'><option value=''>Elegí tu barrio</option>{ops}</select>")
        f.append("<label class='l' for='dir'>Dirección (calle, número, apto, esquina)</label>"
                 f"<input type='text' id='dir' name='direccion' maxlength='{pd.LARGO_DIRECCION}' "
                 f"autocomplete='street-address' value='{e(v.get('direccion', ''))}'></div>")
    f.append("<label class='l'>¿Cómo pagás?</label>")
    pago = v.get("pago") if v.get("pago") in loc["pagos"] else loc["pagos"][0]
    for k in loc["pagos"]:
        extra = {"efectivo": "Al recibir o al retirar", "transferencia": "Te pasamos los datos al confirmar",
                 "tarjeta": "Llevamos el POS", "mercadopago": "Pagás con el link al confirmar"}[k]
        f.append(f"<label class='radio'><input type='radio' name='pago' value='{k}'{' checked' if k == pago else ''}>"
                 f"<span><b>{e(pd.PAGOS[k])}</b><small>{extra}</small></span></label>")
    if "efectivo" in loc["pagos"]:
        f.append("<label class='l' for='pc'>Si pagás en efectivo, ¿con cuánto? (para el cambio, opcional)</label>"
                 f"<input type='number' id='pc' name='paga_con' min='0' step='1' inputmode='numeric' value='{e(v.get('paga_con', ''))}'>")
    f.append("<label class='l' for='nt'>Algo más (opcional)</label>"
             f"<textarea id='nt' name='nota' maxlength='{pd.LARGO_NOTA}' placeholder='Sin cebolla, tocar timbre 2…'>"
             f"{e(v.get('nota', ''))}</textarea>")
    f.append("<p><button class='btn ancho' type='submit'>Confirmar pedido</button></p>"
             "<p class='sub centro'>El local confirma el pedido y te avisa por WhatsApp.</p></form>")
    partes.append("".join(f))
    return "".join(partes)


@pedidos_bp.route("/pedir/<slug>/datos")
def datos(slug):
    loc = _local_publico(slug)
    if not loc:
        return _no_existe()
    lineas = pd.leer_carrito(loc, (request.args.get("c") or "")[:600])
    if not lineas:
        return redirect(f"/pedir/{loc['slug']}", 303)
    c = pd.carrito_texto(lineas)
    return _pagina(loc, f"Tu pedido · {loc['nombre']}", _datos_html(loc, c, lineas, {}))


@pedidos_bp.route("/pedir/<slug>/confirmar", methods=["POST"])
def confirmar(slug):
    loc = _local_publico(slug)
    if not loc:
        return _no_existe()
    c = (request.form.get("c") or "")[:600]
    if request.form.get("web"):
        # La trampa para robots: un humano no ve ese campo.
        return redirect(f"/pedir/{loc['slug']}", 303)
    try:
        p = pd.pedir(_db(), loc["id"], c, request.form, _ip())
    except pd.NoSePuede as err:
        lineas = pd.leer_carrito(loc, c)
        if not lineas:
            return redirect(f"/pedir/{loc['slug']}?" + urlencode({"error": str(err)}), 303)
        valores = {k: request.form.get(k, "") for k in
                   ("cliente", "telefono", "tipo", "zona_id", "direccion", "pago", "paga_con", "nota")}
        return _pagina(loc, f"Tu pedido · {loc['nombre']}",
                       _datos_html(loc, pd.carrito_texto(lineas), lineas, valores, str(err)), 400)
    return redirect(f"/pedir/{loc['slug']}/p/{p['codigo']}", 303)


def _pedido_de(slug: str, codigo: str):
    loc = pd.por_slug(_db(), slug)
    p = pd.por_codigo(_db(), codigo) if loc else None
    if not loc or not p or p["local_id"] != loc["id"]:
        return None, None
    return loc, p


@pedidos_bp.route("/pedir/<slug>/p/<codigo>")
def seguimiento(slug, codigo):
    loc, p = _pedido_de(slug, codigo)
    if not loc:
        return _no_existe()
    e = html.escape
    pasos = ["nuevo", "preparando", "listo"] + (["en_camino"] if p["tipo"] == "delivery" else []) + ["entregado"]
    nombres = {"nuevo": "Recibido", "preparando": "Preparando",
               "listo": "Listo" if p["tipo"] == "delivery" else "Para retirar", "en_camino": "En camino",
               "entregado": "Entregado"}
    partes = [_cabecera(loc)]
    if request.args.get("error"):
        partes.append(f"<div class='aviso mal' role='alert'>{e(request.args['error'][:300])}</div>")
    titulo = {"nuevo": "¡Pedido enviado! El local lo está mirando.",
              "preparando": "Lo están preparando" + (f": listo a las {e(p['listo_estimado'])}" if p.get("listo_estimado") else ""),
              "listo": "¡Está listo para retirar!" if p["tipo"] == "retiro" else "Está listo, sale enseguida",
              "en_camino": "Va en camino", "entregado": "Entregado. ¡Que lo disfrutes!",
              "cancelado": "Este pedido se canceló"}[p["estado"]]
    partes.append(f"<div class='card centro'><p class='sub'>Pedido</p><p class='num'>#{p['numero']}</p><h2>{titulo}</h2>")
    if p["estado"] == "cancelado":
        if p.get("motivo"):
            partes.append(f"<p class='sub'>{e(p['motivo'])}</p>")
    else:
        hecho = pasos.index(p["estado"])
        partes.append("<div class='pasos' aria-hidden='true'>" + "".join(
            f"<div class='paso{' hecho' if i <= hecho else ''}'><i></i>{nombres[s]}</div>"
            for i, s in enumerate(pasos)) + "</div>")
    partes.append("</div>")
    if p["estado"] == "nuevo" and p["pago"] == "transferencia" and loc.get("alias_transferencia"):
        partes.append(f"<div class='card'><b>Para pagar por transferencia</b><p>{e(loc['alias_transferencia'])}</p>"
                      f"<p class='sub'>Total {e(pd.plata(p['total']))}. Mandá el comprobante por WhatsApp.</p></div>")
    if p["pago"] == "mercadopago" and loc.get("link_mercadopago") and p["estado"] not in pd.FINALES:
        partes.append(f"<p><a class='btn ancho' href='{e(loc['link_mercadopago'])}' rel='noopener'>"
                      f"Pagar {e(pd.plata(p['total']))} con Mercado Pago</a></p>")
    items = "".join(f"<div class='lin'><b>{it['cantidad']} ×</b><div class='t'>{e(it['nombre'])}"
                    + (f"<div class='sub'>{e(it['opciones'])}</div>" if it.get("opciones") else "")
                    + f"</div><div class='precio'>{e(pd.plata(it['subtotal']))}</div></div>" for it in p["items"])
    entrega = (f"Delivery a {e(p['direccion'] or '')}" + (f" ({e(p['zona'])})" if p.get("zona") else "")
               if p["tipo"] == "delivery" else "Retirás en el local" + (f": {e(loc['direccion'])}" if loc.get("direccion") else ""))
    partes.append(f"<div class='card'><h2>Detalle</h2>{items}"
                  f"<div class='tot'><span>Subtotal</span><span>{e(pd.plata(p['subtotal']))}</span></div>"
                  + (f"<div class='tot'><span>Envío</span><span>{e(pd.plata(p['envio']))}</span></div>" if p["envio"] else "")
                  + f"<div class='tot g'><span>Total</span><span>{e(pd.plata(p['total']))}</span></div>"
                  f"<p class='sub'>{entrega} · Pago: {e(p['pago_texto'])}"
                  + (f" (con {e(pd.plata(p['paga_con']))})" if p.get("paga_con") else "") + "</p>"
                  + (f"<p class='sub'>Nota: {e(p['nota'])}</p>" if p.get("nota") else "") + "</div>")
    wa = pd.whatsapp_local(_base(), loc, p)
    if wa and p["estado"] not in pd.FINALES:
        partes.append(f"<p><a class='btn wa ancho' href='{e(wa)}' rel='noopener'>Escribirle al local por WhatsApp</a></p>")
    if p["estado"] in pd.CLIENTE_CANCELA:
        partes.append(f"<form method='post' action='/pedir/{e(loc['slug'])}/p/{e(p['codigo'])}/cancelar'>"
                      "<p><button class='btn sec ancho' type='submit'>Cancelar el pedido</button></p></form>")
    if p["estado"] in pd.FINALES:
        partes.append(f"<p><a class='btn sec ancho' href='/pedir/{e(loc['slug'])}'>Hacer otro pedido</a></p>")
    else:
        partes.append("<p class='sub centro'>Esta página se actualiza sola. Guardala para seguir el pedido.</p>")
    return _pagina(loc, f"Pedido #{p['numero']} · {loc['nombre']}", "".join(partes),
                   refresco=0 if p["estado"] in pd.FINALES else 30)


@pedidos_bp.route("/pedir/<slug>/p/<codigo>/cancelar", methods=["POST"])
def cancelar(slug, codigo):
    loc, p = _pedido_de(slug, codigo)
    if not loc:
        return _no_existe()
    base = f"/pedir/{loc['slug']}/p/{p['codigo']}"
    try:
        pd.cancelar_cliente(_db(), codigo)
    except pd.NoSePuede as err:
        return redirect(base + "?" + urlencode({"error": str(err)}), 303)
    return redirect(base, 303)
