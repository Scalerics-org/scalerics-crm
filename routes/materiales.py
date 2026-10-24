"""Portal de materiales del cliente (services/materiales.py).

Dos grupos:
- `/api/materiales/...` para el equipo: piden el panel `materiales`.
- La página del cliente bajo `/m/<código>`, que abre sin sesión (exenta en
  dashboard.require_login). La única llave es el código al azar del portal:
  uno que no existe da 404 sin decir nada más. Muestra el nombre del proyecto
  y la checklist, nada de la ficha del cliente. No lleva scripts: son
  formularios comunes que vuelven a la misma página, así anda en cualquier
  celular. Los archivos subidos no se pueden bajar desde acá: solo el equipo.
"""

import html
import os
import secrets
from urllib.parse import quote

from flask import Blueprint, Response, current_app, jsonify, redirect, request, send_file, session

from database import log_activity
from services import materiales as mt
from services.auth import require_panel

materiales_bp = Blueprint("materiales", __name__)

PUBLICAS = ("/m/",)
# Un pedido del cliente con varios archivos juntos; cada uno igual tiene su tope.
MAX_PEDIDO = 8 * mt.MAX_ARCHIVO


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


@materiales_bp.before_request
def _candado():
    if request.path.startswith(PUBLICAS):
        return None
    return require_panel(_db(), "materiales")


def _con_link(p: dict) -> dict:
    p = dict(p)
    p["link"] = f"{_base()}/m/{p['codigo']}"
    return p


def _codigo_error(e: mt.NoSePuede) -> int:
    return 404 if "no existe" in str(e) else 400


# ── equipo ───────────────────────────────────────────────────────────────────

@materiales_bp.route("/api/materiales")
def api_listar():
    portales = [_con_link(p) for p in mt.listar(_db())]
    return jsonify({"ok": True, "portales": portales, "resumen": mt.resumen(portales),
                    "tipos": {k: v[0] for k, v in mt.PLANTILLAS.items()},
                    "dias_trabado": mt.DIAS_TRABADO, "max_mb": mt.MAX_ARCHIVO // (1024 * 1024)})


@materiales_bp.route("/api/materiales", methods=["POST"])
def api_crear():
    try:
        p = mt.crear(_db(), request.get_json(silent=True) or {}, _quien())
    except mt.NoSePuede as e:
        return _error(e)
    log_activity(_db(), _quien(), "materiales_portal_alta", "materiales", p["id"], p["nombre"], p["tipo"],
                 user_id=session.get("user_id"))
    return jsonify({"ok": True, "portal": _con_link(p)}), 201


@materiales_bp.route("/api/materiales/clientes")
def api_clientes():
    return jsonify({"ok": True, "clientes": mt.clientes(_db())})


@materiales_bp.route("/api/materiales/<int:pid>")
def api_detalle(pid):
    try:
        return jsonify({"ok": True, "portal": _con_link(mt.detalle(_db(), pid))})
    except mt.NoSePuede as e:
        return _error(e, 404)


@materiales_bp.route("/api/materiales/<int:pid>", methods=["PATCH"])
def api_editar(pid):
    try:
        p = mt.editar(_db(), pid, request.get_json(silent=True) or {}, _quien())
    except mt.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "portal": _con_link(p)})


@materiales_bp.route("/api/materiales/<int:pid>", methods=["DELETE"])
def api_borrar(pid):
    try:
        p = mt.portal(_db(), pid)
        mt.borrar(_db(), pid)
    except mt.NoSePuede as e:
        return _error(e, 404)
    log_activity(_db(), _quien(), "materiales_portal_baja", "materiales", pid, p["nombre"], "",
                 user_id=session.get("user_id"))
    return jsonify({"ok": True})


@materiales_bp.route("/api/materiales/<int:pid>/items", methods=["POST"])
def api_agregar_item(pid):
    try:
        it = mt.agregar_item(_db(), pid, request.get_json(silent=True) or {}, _quien())
    except mt.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "item": it}), 201


@materiales_bp.route("/api/materiales/items/<int:iid>", methods=["DELETE"])
def api_borrar_item(iid):
    try:
        mt.borrar_item(_db(), iid)
    except mt.NoSePuede as e:
        return _error(e, 404)
    return jsonify({"ok": True})


@materiales_bp.route("/api/materiales/items/<int:iid>/revision", methods=["POST"])
def api_revision(iid):
    datos = request.get_json(silent=True) or {}
    try:
        it = mt.revisar(_db(), iid, datos.get("accion", ""), datos.get("comentario", ""), _quien())
    except mt.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "item": it})


@materiales_bp.route("/api/materiales/items/<int:iid>/archivos", methods=["POST"])
def api_subir(iid):
    """El equipo sube por el cliente lo que llegó por WhatsApp o por mail."""
    if (request.content_length or 0) > MAX_PEDIDO:
        return _error("Demasiado grande para un solo envío.", 413)
    from database import _connect
    conn = _connect(_db())
    try:
        f = conn.execute("SELECT portal_id FROM mat_items WHERE id = ?", (iid,)).fetchone()
    finally:
        conn.close()
    if not f:
        return _error("Ese ítem no existe.", 404)
    archivos = [(a.filename, a.stream) for a in request.files.getlist("archivos")]
    try:
        it = mt.enviar(_db(), f["portal_id"], iid, _quien(), texto=request.form.get("texto"), archivos=archivos,
                       cliente=False)
    except mt.NoSePuede as e:
        return _error(e)
    return jsonify({"ok": True, "item": it})


@materiales_bp.route("/api/materiales/archivos/<int:aid>")
def api_bajar(aid):
    try:
        a = mt.archivo(_db(), aid)
        ruta = mt.ruta_archivo(_db(), a)
    except mt.NoSePuede as e:
        return _error(e, 404)
    if not os.path.exists(ruta):
        return _error("El archivo no está en el disco.", 404)
    # Siempre como adjunto y sin adivinar el tipo: un SVG o un PDF del
    # cliente no se abre en el origen del CRM.
    resp = send_file(ruta, as_attachment=True, download_name=mt.nombre_seguro(a["nombre"]),
                     mimetype="application/octet-stream")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return resp


@materiales_bp.route("/api/materiales/archivos/<int:aid>", methods=["DELETE"])
def api_quitar(aid):
    try:
        mt.quitar_archivo(_db(), aid, _quien())
    except mt.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True})


@materiales_bp.route("/api/materiales/<int:pid>/zip")
def api_zip(pid):
    try:
        tmp, nombre = mt.zip_entrega(_db(), pid, solo_aprobados=request.args.get("aprobados") == "1")
    except mt.NoSePuede as e:
        return _error(e, 404)
    return send_file(tmp, as_attachment=True, download_name=nombre, mimetype="application/zip")


# ── página del cliente ───────────────────────────────────────────────────────

_CSS = """
:root{--bg:#09090f;--surface:rgba(255,255,255,.04);--border:rgba(255,255,255,.07);--text:#e2e8f0;
--muted:#64748b;--blue:#0088CC;--blue-lt:#33aadd;--blue-dk:#005f8e;--blue-glow:rgba(0,136,204,.14);
--blue-border:rgba(0,136,204,.28);--green:#10B981;--green-lt:#34d399;--green-glow:rgba(16,185,129,.12);
--amber:#f59e0b;--red:#ef4444;
--ff:'Inter',system-ui,-apple-system,'Segoe UI',sans-serif;--ff-d:'Raleway','Inter',system-ui,sans-serif}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--ff);line-height:1.5}
.wrap{max-width:760px;margin:0 auto;padding:32px 16px 56px}
.top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:28px}
.marca{font-family:var(--ff-d);font-weight:800;letter-spacing:.02em;font-size:1.1rem}
.marca span{color:var(--blue)}
.badge{border:1px solid var(--blue-border);background:var(--blue-glow);color:var(--blue-lt);border-radius:999px;
padding:4px 12px;font-size:.78rem;font-weight:600}
h1{font-family:var(--ff-d);font-size:clamp(1.5rem,4vw,2.1rem);margin:0 0 6px;line-height:1.2;overflow-wrap:anywhere}
.sub{color:var(--muted);margin:0 0 20px}
.msj{white-space:pre-line;background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:14px 16px;
margin-bottom:20px;overflow-wrap:anywhere}
.prog{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:16px;margin-bottom:24px}
.prog-n{display:flex;justify-content:space-between;gap:10px;font-size:.9rem;margin-bottom:8px;flex-wrap:wrap}
.prog-n b{font-size:1.2rem}
.barra{height:10px;border-radius:999px;background:rgba(255,255,255,.07);overflow:hidden}
.barra span{display:block;height:100%;background:linear-gradient(90deg,var(--blue),var(--green))}
.aviso{border-radius:12px;padding:12px 14px;margin-bottom:18px;font-size:.92rem}
.aviso.ok{background:var(--green-glow);border:1px solid rgba(16,185,129,.35);color:var(--green-lt)}
.aviso.mal{background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.35);color:#fca5a5}
.item{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:16px;margin-bottom:12px;
scroll-margin-top:16px}
.item.cambios{border-color:rgba(245,158,11,.5)}
.item-cab{display:flex;gap:10px;align-items:flex-start;justify-content:space-between;flex-wrap:wrap}
.item h2{font-family:var(--ff-d);font-size:1.02rem;margin:0;overflow-wrap:anywhere}
.item h2 small{color:var(--muted);font-weight:500;font-family:var(--ff);font-size:.78rem;margin-left:6px}
.desc{color:var(--muted);font-size:.88rem;margin:6px 0 10px}
.pill{border-radius:999px;padding:2px 10px;font-weight:600;font-size:.74rem;white-space:nowrap;
background:rgba(255,255,255,.06);color:var(--muted)}
.pill.enviado{background:var(--blue-glow);color:var(--blue-lt)}
.pill.aprobado{background:var(--green-glow);color:var(--green-lt)}
.pill.cambios{background:rgba(245,158,11,.14);color:var(--amber)}
.coment{border-left:3px solid var(--amber);padding:4px 0 4px 10px;margin:8px 0 10px;font-size:.9rem;white-space:pre-line;
overflow-wrap:anywhere}
.coment b{color:var(--amber)}
ul.arch{list-style:none;margin:0 0 10px;padding:0;display:flex;flex-direction:column;gap:6px}
ul.arch li{display:flex;justify-content:space-between;gap:10px;align-items:center;font-size:.86rem;
background:rgba(255,255,255,.03);border-radius:8px;padding:6px 10px}
ul.arch li span{overflow-wrap:anywhere}
ul.arch form{margin:0}
.resp{white-space:pre-line;font-size:.9rem;background:rgba(255,255,255,.03);border-radius:8px;padding:8px 10px;
margin-bottom:10px;overflow-wrap:anywhere}
textarea,input[type=file]{width:100%;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.12);
color:var(--text);border-radius:10px;padding:10px;font-family:inherit;font-size:.92rem}
textarea{min-height:110px;resize:vertical}
input[type=file]::file-selector-button{background:var(--blue-glow);color:var(--blue-lt);border:1px solid var(--blue-border);
border-radius:8px;padding:6px 12px;margin-right:10px;font-family:inherit;cursor:pointer}
label.l{display:block;font-size:.8rem;color:var(--muted);margin:8px 0 4px}
.acc{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:10px}
button{background:var(--blue);color:#fff;border:0;border-radius:10px;padding:10px 18px;font-weight:600;font-family:inherit;
font-size:.9rem;cursor:pointer}
button:hover{background:var(--blue-dk)}
button.chico{background:transparent;color:var(--muted);border:1px solid var(--border);padding:3px 10px;font-size:.78rem}
button:focus-visible,a:focus-visible,textarea:focus-visible,input:focus-visible{outline:2px solid var(--blue-lt);
outline-offset:2px}
.nota{color:var(--muted);font-size:.78rem}
.pie{margin-top:28px;color:var(--muted);font-size:.78rem;text-align:center}
"""

_ESTADO_CLIENTE = {"pendiente": "Falta", "enviado": "Recibido", "aprobado": "Listo ✓", "cambios": "Falta algo"}


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
                   "<h1>Este link no existe</h1><p class='sub'>Revisá el link o pedinos uno nuevo.</p>", 404)


def _peso(n: int) -> str:
    if n < 1024 * 1024:
        return f"{max(1, n // 1024)} KB"
    return f"{n / (1024 * 1024):.1f} MB".replace(".", ",")


def _item_html(codigo: str, it: dict, cerrado: bool) -> str:
    e = html.escape
    iid = int(it["id"])
    estado = it["estado"]
    cab = (f"<div class='item-cab'><h2>{e(it['titulo'])}"
           f"{'' if it['obligatorio'] else '<small>opcional</small>'}</h2>"
           f"<span class='pill {e(estado)}'>{_ESTADO_CLIENTE.get(estado, estado)}</span></div>")
    partes = [cab]
    if it.get("descripcion"):
        partes.append(f"<p class='desc'>{e(it['descripcion'])}</p>")
    if estado == "cambios" and it.get("comentario"):
        partes.append(f"<div class='coment'><b>Nos falta:</b> {e(it['comentario'])}</div>")
    editable = not cerrado and estado != "aprobado"
    if it["archivos"]:
        filas = []
        for a in it["archivos"]:
            quitar = (f"<form method='post' action='/m/{e(codigo)}/a/{int(a['id'])}/quitar'>"
                      f"<button class='chico' type='submit' aria-label='Quitar {e(a['nombre'])}'>Quitar</button></form>"
                      if editable else "")
            filas.append(f"<li><span>{e(a['nombre'])} · {_peso(a['bytes'])}</span>{quitar}</li>")
        partes.append("<ul class='arch'>" + "".join(filas) + "</ul>")
    # En un ítem de texto la respuesta ya está adentro del formulario.
    if it.get("texto") and (it["clase"] == "archivo" or not editable):
        partes.append(f"<div class='resp'>{e(it['texto'])}</div>")
    if editable:
        accion = f"/m/{e(codigo)}/i/{iid}"
        if it["clase"] == "archivo":
            campo = (f"<label class='l' for='f{iid}'>Archivos (podés elegir varios, hasta "
                     f"{mt.MAX_ARCHIVO // (1024 * 1024)} MB cada uno)</label>"
                     f"<input id='f{iid}' type='file' name='archivos' multiple>"
                     f"<label class='l' for='t{iid}'>Comentario (opcional)</label>"
                     f"<textarea id='t{iid}' name='texto' maxlength='{mt.LARGO_TEXTO}' "
                     f"placeholder='Algo que tengamos que saber de estos archivos'></textarea>")
            boton = "Subir"
        else:
            ph = "Un link por línea" if it["clase"] == "link" else "Escribilo acá, como te salga: nosotros lo pulimos."
            campo = (f"<label class='l' for='t{iid}'>Tu respuesta</label>"
                     f"<textarea id='t{iid}' name='texto' maxlength='{mt.LARGO_TEXTO}' required "
                     f"placeholder='{e(ph)}'>{e(it.get('texto') or '')}</textarea>")
            boton = "Guardar"
        partes.append(f"<form method='post' action='{accion}' enctype='multipart/form-data'>{campo}"
                      f"<div class='acc'><button type='submit'>{boton}</button></div></form>")
    return f"<section class='item {e(estado)}' id='i{iid}'>{''.join(partes)}</section>"


def _fecha_corta(iso: str) -> str:
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
             "noviembre", "diciembre"]
    try:
        a, m, d = (int(x) for x in iso.split("-"))
        return f"{d} de {meses[m - 1]}"
    except (ValueError, IndexError):
        return ""


@materiales_bp.route("/m/<codigo>")
def portal_publico(codigo):
    p = mt.por_codigo(_db(), codigo)
    if not p:
        return _no_existe()
    mt.visita(_db(), p["id"])
    its = mt.items(_db(), p["id"])
    e = html.escape
    cerrado = bool(p["cerrado_en"])
    aviso = ""
    if request.args.get("ok") == "quitado":
        aviso = "<div class='aviso ok' role='status'>Listo, lo sacamos.</div>"
    elif request.args.get("ok"):
        aviso = "<div class='aviso ok' role='status'>¡Recibido! Lo revisamos y te avisamos si falta algo.</div>"
    elif request.args.get("error"):
        aviso = f"<div class='aviso mal' role='alert'>{e(request.args['error'][:300])}</div>"
    faltan = [i for i in its if i["estado"] in ("pendiente", "cambios") and i["obligatorio"]]
    if cerrado:
        estado_txt = "Ya tenemos todo. ¡Gracias!"
    elif not faltan:
        estado_txt = "No falta nada obligatorio: lo estamos revisando."
    else:
        estado_txt = f"Faltan {len(faltan)} cosa(s) obligatoria(s)."
    limite = ""
    if p.get("fecha_limite") and not cerrado:
        limite = f" Lo ideal es tenerlo antes del <b>{e(_fecha_corta(p['fecha_limite']))}</b>."
    tipo = mt.PLANTILLAS.get(p["tipo"], ("Proyecto",))[0]
    cuerpo = (
        f"<div class='top'><div class='marca'>SCALE<span>RICS</span></div><div class='badge'>{e(tipo)}</div></div>"
        f"<h1>{e(p['nombre'])}</h1>"
        "<p class='sub'>Lo que necesitamos de tu lado para armar tu proyecto. Podés mandarlo de a partes y volver "
        "a este link cuando quieras.</p>"
        + (f"<div class='msj'>{e(p['mensaje'])}</div>" if p.get("mensaje") else "")
        + f"<div class='prog'><div class='prog-n'><span><b>{p['hechos']}</b> de {p['total']} listos</span>"
        f"<span>{estado_txt}{limite}</span></div>"
        f"<div class='barra' role='progressbar' aria-valuemin='0' aria-valuemax='100' aria-valuenow='{p['pct']}' "
        f"aria-label='Avance'><span></span></div></div>"
        + aviso
        + "".join(_item_html(codigo, it, cerrado) for it in its)
        + "<p class='nota'>Lo que subís acá solo lo ve el equipo de Scalerics. No escribas contraseñas: para eso "
          "te escribimos aparte.</p>"
        "<p class='pie'>Scalerics · scalerics.com</p>")
    # El ancho de la barra va en la hoja con nonce: la CSP no deja style en línea.
    return _pagina(f"{p['nombre']} · Materiales · Scalerics", cuerpo, css=f".barra span{{width:{int(p['pct'])}%}}")


def _volver(codigo: str, iid=None, error: str = "", ok: str = "1"):
    q = f"?error={quote(error[:300])}" if error else f"?ok={ok}"
    return redirect(f"/m/{codigo}{q}" + (f"#i{int(iid)}" if iid else ""), code=303)


@materiales_bp.route("/m/<codigo>/i/<int:iid>", methods=["POST"])
def portal_enviar(codigo, iid):
    p = mt.por_codigo(_db(), codigo)
    if not p:
        return _no_existe()
    if (request.content_length or 0) > MAX_PEDIDO:
        return _volver(codigo, iid, "Es demasiado para un solo envío: mandalo de a partes.")
    archivos = [(a.filename, a.stream) for a in request.files.getlist("archivos")]
    try:
        it = mt.enviar(_db(), p["id"], iid, "cliente", texto=request.form.get("texto"), archivos=archivos)
    except mt.NoSePuede as e:
        return _volver(codigo, iid, str(e))
    log_activity(_db(), "cliente", "materiales_envio", "materiales", p["id"], p["nombre"], it["titulo"])
    return _volver(codigo, iid)


@materiales_bp.route("/m/<codigo>/a/<int:aid>/quitar", methods=["POST"])
def portal_quitar(codigo, aid):
    p = mt.por_codigo(_db(), codigo)
    if not p:
        return _no_existe()
    try:
        a = mt.archivo(_db(), aid)
        mt.quitar_archivo(_db(), aid, "cliente", pid=p["id"])
    except mt.NoSePuede as e:
        return _volver(codigo, None, str(e))
    return _volver(codigo, a["item_id"], ok="quitado")

