"""Reseñas y opiniones: QR en el mostrador para comercios (services/resenas.py).

Dos grupos:
- `/api/resenas/...` y el cartel para imprimir `/resenas/cartel/...` son del
  equipo: piden el panel `resenas`.
- La página donde opina el cliente del comercio, `/opina/<slug>`, abre sin
  sesión (exenta en dashboard.require_login): es pública a propósito, va en
  un QR. Cada opinión tiene su link `/opina/<slug>/r/<código>` con un código
  al azar para agregar el comentario; sin ese código no se ve nada de nadie.
- El panel del comercio, `/comercio/<token>`, también abre sin sesión: el
  link privado ES la credencial (192 bits al azar, en la base solo su hash).
  El dueño ve sus números, trabaja su bandeja de quejas y baja sus QR. Cada
  link es de una persona y el equipo lo revoca desde PRODUCTOS › Reseñas.

La página no lleva scripts: las estrellas son botones de un formulario común,
así anda en cualquier celular y con CSP estricta.
"""

import html
import os
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from flask import Blueprint, Response, current_app, jsonify, redirect, request, session

from database import log_activity
from services import resenas as rs
from services import resenas_avisos as ra
from services import resenas_google as rg
from services.auth import require_panel

resenas_bp = Blueprint("resenas", __name__)

PUBLICAS = ("/opina/", "/comercio/")


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
    n["place_id_efectivo"] = rg.place_id_de(n)
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
    return jsonify({"ok": True, "negocio": _con_links(n), "metricas": m, "opiniones": ops, "filtro": filtro,
                    "accesos": rs.accesos(_db(), nid), "avisos": ra.avisos(_db(), nid),
                    "google_resumen": rg.resumen(_db(), nid),
                    "google_resenas": [_resena_json(n, r) for r in rg.resenas(_db(), nid, "todas")]})


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


def _resena_json(n: dict, r: dict) -> dict:
    return {**r, "responder": rg.link_responder(n, r)}


@resenas_bp.route("/api/resenas/negocios/<int:nid>/google/leer", methods=["POST"])
def api_leer_google(nid):
    try:
        r = rg.leer(_db(), nid, "manual")
    except rs.NoSePuede as e:
        return _error(e, _codigo_error(e))
    log_activity(_db(), _quien(), "resenas_google", "resenas", nid, r["negocio"]["nombre"], f"{r['nuevas']} nuevas",
                 user_id=session.get("user_id"))
    return jsonify({"ok": True, "nuevas": r["nuevas"]})


@resenas_bp.route("/api/resenas/negocios/<int:nid>/google/resenas/<int:rid>", methods=["PATCH"])
def api_marcar_resena(nid, rid):
    d = request.get_json(silent=True) or {}
    try:
        n = rs.negocio(_db(), nid)
        r = rg.marcar(_db(), nid, rid, d.get("estado"), d.get("respuesta"), _quien())
    except rs.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "resena": _resena_json(n, r)})


@resenas_bp.route("/api/resenas/negocios/<int:nid>/opiniones/<int:oid>", methods=["PATCH"])
def api_atender(nid, oid):
    try:
        n = rs.negocio(_db(), nid)
        o = rs.atender(_db(), oid, request.get_json(silent=True) or {}, _quien(), nid=nid)
    except rs.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "opinion": _opinion_json(n, o)})


def _link_acceso(token: str) -> str:
    return f"{_base()}/comercio/{token}"


@resenas_bp.route("/api/resenas/negocios/<int:nid>/accesos")
def api_accesos(nid):
    try:
        rs.negocio(_db(), nid)
    except rs.NoSePuede as e:
        return _error(e, 404)
    return jsonify({"ok": True, "accesos": rs.accesos(_db(), nid), "avisos": ra.avisos(_db(), nid)})


@resenas_bp.route("/api/resenas/negocios/<int:nid>/accesos", methods=["POST"])
def api_crear_acceso(nid):
    datos = request.get_json(silent=True) or {}
    email = (datos.get("email") or "").strip()
    try:
        n = rs.negocio(_db(), nid)
        if email and not ra.EMAIL.fullmatch(email):
            raise rs.NoSePuede("Ese mail no parece válido.")
        a = rs.crear_acceso(_db(), nid, datos.get("nombre"), _quien())
        if email:
            # Con mail, el aviso de quejas arranca prendido: es para lo que se pide el mail.
            ra.guardar_preferencias(_db(), a["id"], email, datos.get("aviso") or "al_momento", nid=nid)
            a.update({"email": email, "aviso": datos.get("aviso") if datos.get("aviso") in ra.MODOS else "al_momento"})
    except rs.NoSePuede as e:
        return _error(e, _codigo_error(e))
    log_activity(_db(), _quien(), "resenas_acceso", "resenas", nid, n["nombre"], a["nombre"],
                 user_id=session.get("user_id"))
    link = _link_acceso(a.pop("token"))
    # El link se ve una sola vez. El WhatsApp es un wa.me que abre una persona.
    wa = ""
    if n.get("whatsapp"):
        texto = (f"Hola, este es el link a tu panel de opiniones de {n['nombre']}: {link} . "
                 "Es personal, no lo compartas. Ahí ves las opiniones, las quejas para resolver y tus QR.")
        wa = f"https://wa.me/{n['whatsapp']}?" + urlencode({"text": texto})
    return jsonify({"ok": True, "acceso": a, "link": link, "whatsapp": wa}), 201


@resenas_bp.route("/api/resenas/negocios/<int:nid>/accesos/<int:aid>", methods=["DELETE"])
def api_revocar_acceso(nid, aid):
    try:
        a = rs.acceso(_db(), aid)
        rs.revocar_acceso(_db(), nid, aid)
    except rs.NoSePuede as e:
        return _error(e, 404)
    log_activity(_db(), _quien(), "resenas_revoca", "resenas", nid, "", a["nombre"], user_id=session.get("user_id"))
    return jsonify({"ok": True})


@resenas_bp.route("/api/resenas/negocios/<int:nid>/accesos/<int:aid>/avisos", methods=["PUT"])
def api_avisos_acceso(nid, aid):
    datos = request.get_json(silent=True) or {}
    try:
        p = ra.guardar_preferencias(_db(), aid, datos.get("email"), datos.get("aviso"), nid=nid)
    except rs.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "avisos": p})


_OPINIONES_EJEMPLO = [
    {"id": 0, "puntaje": 2, "comentario": "Pedimos la parrillada para dos y llegó fría. El mozo ni se disculpó.",
     "nombre": "Laura", "contacto": "099 123 456", "quiere_contacto": 1, "fuente": "Mesas"},
    {"id": 0, "puntaje": 3, "comentario": None, "nombre": None, "contacto": None, "quiere_contacto": 0,
     "fuente": "Mostrador"},
]


@resenas_bp.route("/resenas/aviso-ejemplo/<int:nid>")
def aviso_ejemplo(nid):
    """El mail tal cual le llega al comercio, con sus quejas abiertas (o dos de
    ejemplo si no tiene), para mostrárselo antes de prender el aviso."""
    try:
        n = rs.negocio(_db(), nid)
    except rs.NoSePuede:
        return _no_existe()
    ahora = rs.ahora_uy()
    a = {"nombre": request.args.get("para") or "Marcela"}
    link = f"{_base()}/comercio/(tu-link-personal)?desde=mail"
    conn = rs._con(_db())
    try:
        if request.args.get("tipo") == "resumen":
            mail = ra.armar_mail_resumen(n, a, ra.resumen_de_ayer(conn, nid, ahora), link)
        else:
            quejas = [dict(f) for f in conn.execute(
                "SELECT o.id, o.puntaje, o.comentario, o.nombre, o.contacto, o.quiere_contacto, o.creado_en, "
                "f.nombre fuente FROM rs_opiniones o LEFT JOIN rs_fuentes f ON f.id = o.fuente_id "
                "WHERE o.negocio_id = ? AND o.estado = 'abierta' ORDER BY o.creado_en DESC LIMIT 3", (nid,))]
            quejas = quejas or [{**o, "creado_en": rs._txt(ahora - timedelta(minutes=20 * (i + 1)))}
                                for i, o in enumerate(_OPINIONES_EJEMPLO)]
            # Y la reseña mala de Google sin responder, si la lectura diaria trajo alguna.
            google = [dict(f) for f in conn.execute(
                "SELECT id, rating, texto, autor, link, publicada_en, leida_en, sugerida FROM rs_google_resenas "
                "WHERE negocio_id = ? AND estado = 'sin_responder' AND rating <= ? "
                "ORDER BY IFNULL(publicada_en, leida_en) DESC LIMIT 2", (nid, rs.QUEJA_HASTA))]
            mail = ra.armar_mail_quejas(n, a, quejas, link, google)
    finally:
        conn.close()
    resp = Response(mail["html"], mimetype="text/html")
    resp.headers["Content-Security-Policy"] = ("default-src 'none'; style-src 'unsafe-inline'; img-src https: data:; "
                                               "frame-ancestors 'self'")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _fuente_de(n: dict):
    fid = request.args.get("f", type=int)
    return next((f for f in n["fuentes"] if f["id"] == fid), None)


def _qr_resp(n: dict) -> Response:
    fuente = _fuente_de(n)
    svg = rs.qr_svg(rs.link_publico(_base(), n, fuente))
    if not svg:
        return _error("Falta la librería segno para dibujar el QR.", 501)
    resp = Response(svg, mimetype="image/svg+xml")
    resp.headers["Content-Security-Policy"] = "default-src 'none'"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, max-age=300"
    if request.args.get("bajar"):
        nombre = n["slug"] + (f"-{fuente['codigo']}" if fuente else "")
        resp.headers["Content-Disposition"] = f'attachment; filename="qr-{nombre}.svg"'
    return resp


def _cartel_resp(n: dict) -> Response:
    link = rs.link_publico(_base(), n, _fuente_de(n))
    qr = rs.qr_svg(link) or ""
    e = html.escape
    cuerpo = (f"<div class='cartel'><p class='chico'>{e(n['nombre'])}</p>"
              "<h1>¿Cómo te atendimos?</h1><p>Escaneá y contanos en 10 segundos.</p>"
              f"<div class='qr'>{qr}</div><p class='link'>{e(link)}</p>"
              "<p class='chico'>Tu opinión la lee el dueño. ¡Gracias!</p></div>")
    # El SVG del QR va inline y se pinta con atributos, no con style: entra en la CSP.
    return _pagina(n, f"Cartel · {n['nombre']}", cuerpo, cartel=True)


@resenas_bp.route("/resenas/qr/<int:nid>.svg")
def qr(nid):
    """El QR solo, para verlo en el panel o bajarlo y mandarlo a la imprenta."""
    try:
        n = rs.negocio(_db(), nid)
    except rs.NoSePuede:
        return _error("El comercio no existe.", 404)
    return _qr_resp(n)


@resenas_bp.route("/resenas/cartel/<int:nid>")
def cartel(nid):
    """El cartel para imprimir y pegar en el mostrador: QR grande y una frase."""
    try:
        n = rs.negocio(_db(), nid)
    except rs.NoSePuede:
        return _no_existe()
    return _cartel_resp(n)


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
input[type=text],input[type=email],textarea{width:100%;border:1px solid #cbd5e1;border-radius:10px;padding:10px;font-family:inherit;
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
.wrap.ancho{max-width:880px}
.cab.izq{text-align:left;display:flex;gap:12px;align-items:center}
.cab.izq .logo{margin:0;flex:none}
.cab.izq h1{margin:0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:14px}
.kpi{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:12px 14px}
.kpi .l{font-size:.72rem;color:#64748b;text-transform:uppercase;letter-spacing:.4px}
.kpi .v{font-size:1.55rem;font-weight:800;font-variant-numeric:tabular-nums}
.kpi .s{font-size:.78rem;color:#64748b}
.bien{color:#047857}.mal{color:#b91c1c}
.novedad{background:#eff6ff;border:1px solid #bfdbfe;color:#1e3a8a;border-radius:12px;padding:10px 14px;
margin-top:14px;font-size:.92rem}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin:22px 0 4px}
.tabs a{padding:8px 12px;border-radius:999px;border:1px solid #e2e8f0;background:#fff;color:#334155;
text-decoration:none;font-size:.88rem}
.tabs a[aria-current]{background:var(--acc);border-color:var(--acc);color:#fff}
.tabs .n{display:inline-block;min-width:18px;padding:0 5px;border-radius:9px;background:#f59e0b;color:#fff;
font-size:.72rem;font-weight:700;text-align:center;margin-left:4px}
.periodo{display:flex;gap:6px;flex-wrap:wrap;align-items:center;font-size:.82rem;color:#64748b;margin-top:10px}
.periodo a{color:#334155;padding:3px 9px;border-radius:999px;border:1px solid #e2e8f0;text-decoration:none;
background:#fff}
.periodo a[aria-current]{border-color:var(--acc);color:var(--acc);font-weight:700}
.op{background:#fff;border:1px solid #e2e8f0;border-left:4px solid #10b981;border-radius:12px;padding:12px 14px;
margin-top:10px}
.op.baja{border-left-color:#ef4444}
.op.e-resuelta{border-left-color:#94a3b8}
.op .top{display:flex;gap:8px;flex-wrap:wrap;align-items:center;font-size:.82rem;color:#64748b}
.op .est{color:#f59e0b;font-size:1rem;letter-spacing:1px}
.op .txt{margin:8px 0 4px;font-size:.98rem}
.pill{border-radius:999px;padding:1px 9px;font-size:.74rem;font-weight:700;background:#f1f5f9;color:#475569}
.pill.e-abierta{background:#fef3c7;color:#92400e}
.pill.e-en_curso{background:#dbeafe;color:#1e40af}
.pill.e-resuelta{background:#dcfce7;color:#166534}
.nota-int{background:#f8fafc;border-radius:8px;padding:6px 10px;font-size:.85rem;color:#475569;margin-top:6px}
.acc{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;align-items:center}
.acc form{margin:0}
.b2{display:inline-block;border:1px solid #cbd5e1;background:#fff;color:#0f172a;border-radius:10px;padding:8px 12px;
font:inherit;font-size:.88rem;cursor:pointer;text-decoration:none}
.b2.prim{background:var(--acc);border-color:var(--acc);color:#fff;font-weight:700}
.cierre{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.cierre input[type=text]{flex:1 1 240px}
.campo{display:block;font-size:.85rem;color:#475569;margin:12px 0}
.campo input,.campo textarea{margin-top:4px}
.modos{border:0;padding:0;margin:0 0 14px}
.modos legend{font-size:.85rem;color:#475569;margin-bottom:6px;padding:0}
.modo{display:flex;gap:10px;align-items:center;border:1px solid #e2e8f0;border-radius:10px;padding:10px 12px;
margin-bottom:6px;cursor:pointer}
.modo input{accent-color:var(--acc);width:18px;height:18px;margin:0}
details.res summary{cursor:pointer;color:#334155;font-size:.88rem;margin-top:8px}
.barras{display:flex;gap:6px;align-items:flex-end;height:120px;margin-top:10px}
.barra{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%}
.barra .col{width:100%;max-width:40px;background:#cbd5e1;border-radius:6px 6px 0 0}
.barra .lb{font-size:.68rem;color:#94a3b8;margin-top:4px}
.barra .pv{font-size:.68rem;color:#475569;margin-bottom:2px}
.dist{display:flex;align-items:center;gap:8px;font-size:.85rem;margin:4px 0}
.dist .b{flex:1;height:10px;background:#f1f5f9;border-radius:6px;overflow:hidden}
.dist .f{height:100%;background:#f59e0b}
.dos{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
table.t{width:100%;border-collapse:collapse;font-size:.86rem;margin-top:8px}
table.t th{text-align:left;color:#64748b;font-weight:600;font-size:.75rem;border-bottom:1px solid #e2e8f0;padding:6px 4px}
table.t td{border-bottom:1px solid #f1f5f9;padding:7px 4px}
.qrs{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin-top:12px}
.qrs .card{margin:0;text-align:center}
.qrs img{width:150px;height:150px}
.qrs .link{font-size:.75rem;color:#64748b;overflow-wrap:anywhere}
.vacio{color:#64748b;text-align:center;padding:26px 10px}
"""
# Las barras y el reparto de estrellas van con clases y no con style="height:…":
# la CSP no deja estilos en línea.
_CSS += "".join(f".h{i}{{height:{i * 5}%}}.w{i}{{width:{i * 5}%}}" for i in range(21))


def _cabeceras(resp: Response, nonce: str) -> Response:
    resp.headers["Content-Security-Policy"] = (
        f"default-src 'none'; style-src 'nonce-{nonce}'; img-src 'self' data:; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


def _pagina(n, titulo: str, cuerpo: str, codigo: int = 200, cartel: bool = False, ancho: bool = False) -> Response:
    nonce = secrets.token_urlsafe(12)
    color = rs._color(n.get("color") if n else None)
    pie = "" if cartel else "<p class='pie'>Opiniones con <a href='https://scalerics.com'>Scalerics</a></p>"
    doc = ("<!doctype html><html lang='es'><head><meta charset='utf-8'>"
           "<meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<meta name='theme-color' content='{color}'><meta name='robots' content='noindex,nofollow'>"
           f"<title>{html.escape(titulo)}</title><style nonce='{nonce}'>:root{{--acc:{color}}}{_CSS}</style></head>"
           f"<body><div class='wrap{' ancho' if ancho else ''}'>{cuerpo}{pie}</div></body></html>")
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


# ── panel del comercio ───────────────────────────────────────────────────────
#
# Lo que ve el dueño con su link privado. Sin JavaScript, igual que la página
# donde opinan sus clientes: cada acción es un formulario que vuelve con 303.

VISTAS_COMERCIO = (("bandeja", "Para atender"), ("todas", "Todas"), ("resueltas", "Resueltas"),
                   ("google", "Reseñas de Google"), ("qr", "QR y carteles"), ("avisos", "Avisos"))
DIAS_COMERCIO = (7, 30, 90, 365)
_ACTUAL = " aria-current='page'"


def _acceso_de(token: str):
    a = rs.por_token(_db(), token, current_app.secret_key)
    return (a, a["negocio"]) if a else (None, None)


def _num(v, suf: str = "") -> str:
    return "—" if v is None else str(v).replace(".", ",") + suf


def _estrellas(p: int) -> str:
    return "★" * p + f"<span class='apagada'>{'★' * (5 - p)}</span>"


def _kpis_comercio(m: dict) -> str:
    g = m["google_evolucion"]
    k = [
        ("Opiniones", str(m["opiniones"]), f"{m['vistas']} abrieron el link", ""),
        ("Promedio", _num(m["promedio"]) + " ★", f"{_num(m['positivas_pct'], '%')} con 4 o 5 estrellas",
         "mal" if m["promedio"] is not None and m["promedio"] < 3.5 else ""),
        ("Fueron a Google", str(m["google"]), f"{_num(m['google_de_positivas_pct'], '%')} de los contentos", "bien"),
        ("Para atender", str(m["abiertas"] + m["en_curso"]), f"{m['abiertas']} sin tocar · {m['en_curso']} en curso",
         "mal" if m["abiertas"] else ""),
        ("Quejas resueltas", _num(m["resueltas_pct"], "%"),
         f"{m['quejas']} quejas en el período" if m["horas_resolucion"] is None
         else f"en {_num(m['horas_resolucion'])} h (mediana)", ""),
    ]
    if g:
        k.append(("Google", _num(g["ultimo"]["rating"]) + " ★",
                  f"{'+' if g['nuevas'] >= 0 else ''}{g['nuevas']} reseñas desde el "
                  + "/".join(reversed(g["primero"]["fecha"].split("-"))),
                  "bien" if g["delta_rating"] > 0 else ""))
    e = html.escape
    return "<div class='kpis'>" + "".join(
        f"<div class='kpi'><div class='l'>{e(l)}</div><div class='v {c}'>{e(v)}</div><div class='s'>{e(s)}</div></div>"
        for l, v, s, c in k) + "</div>"


def _graficos_comercio(m: dict) -> str:
    e = html.escape
    tope = max([1] + [s["opiniones"] for s in m["semanas"]])
    barras = "".join(
        f"<div class='barra' title='{e('Semana del ' + s['desde'] + ': ' + str(s['opiniones']) + ' opiniones')}'>"
        f"<div class='pv'>{_num(s['promedio']) if s['promedio'] else ''}</div>"
        f"<div class='col h{round(20 * s['opiniones'] / tope)}'></div>"
        f"<div class='lb'>{e(s['desde'][8:10] + '/' + s['desde'][5:7])}</div></div>" for s in m["semanas"])
    total = max(1, m["opiniones"])
    dist = "".join(
        f"<div class='dist'><span>{i} ★</span><div class='b'><div class='f w{round(20 * m['distribucion'][str(i)] / total)}'>"
        f"</div></div><b>{m['distribucion'][str(i)]}</b></div>" for i in range(5, 0, -1))
    fuentes = ""
    if m["por_fuente"]:
        fuentes = ("<div class='card'><h2 class='m0'>Qué QR rinde</h2><table class='t'><thead><tr><th>Dónde</th>"
                   "<th>Abrieron</th><th>Puntuaron</th><th>Promedio</th><th>A Google</th></tr></thead><tbody>"
                   + "".join(f"<tr><td>{e(f['nombre'])}</td><td>{f['vistas']}</td><td>{f['opiniones']}</td>"
                             f"<td>{_num(f['promedio'])}</td><td>{f['google']}</td></tr>" for f in m["por_fuente"])
                   + "</tbody></table></div>")
    return ("<div class='dos'><div class='card'><h2 class='m0'>Opiniones por semana</h2>"
            f"<p class='sub'>Arriba de cada barra, el promedio.</p><div class='barras'>{barras}</div></div>"
            f"<div class='card'><h2 class='m0'>Estrellas ({m['dias']} días)</h2>{dist}</div></div>" + fuentes)


def _opiniones_comercio(n: dict, ops: list, token: str, vista: str) -> str:
    e = html.escape
    if not ops:
        return ("<div class='card vacio'>" + ("Nada para atender. Cuando alguien puntúe de 1 a 3 o pida que lo "
                                              "contacten, aparece acá." if vista == "bandeja"
                                              else "No hay opiniones en esta vista.") + "</div>")
    partes = []
    for o in ops:
        accion = f"/comercio/{e(token)}/o/{int(o['id'])}"
        meta = [e(o["creado_en"][8:10] + "/" + o["creado_en"][5:7] + " " + o["creado_en"][11:16]),
                "QR " + e(o["fuente"]) if o.get("fuente") else "link directo"]
        if o.get("fue_a_google_en"):
            meta.append("fue a Google")
        quien = ""
        if o.get("nombre") or o.get("contacto"):
            quien = ("<p class='sub'>" + " · ".join(e(x) for x in (o.get("nombre"), o.get("contacto")) if x)
                     + (" · <b>quiere que lo contacten</b>" if o.get("quiere_contacto") else "") + "</p>")
        acc = []
        wa = rs.whatsapp_respuesta(n, o)
        if wa:
            acc.append(f"<a class='b2 prim' href='{e(wa)}' target='_blank' rel='noopener'>Contestar por WhatsApp</a>")
        if o["estado"] == "abierta":
            acc.append(f"<form method='post' action='{accion}'><input type='hidden' name='accion' value='tomar'>"
                       "<button class='b2' type='submit'>La tomo yo</button></form>")
        if o["estado"] == "resuelta":
            acc.append(f"<form method='post' action='{accion}'><input type='hidden' name='accion' value='reabrir'>"
                       "<button class='b2' type='submit'>Reabrir</button></form>")
        cierre = ""
        if o["estado"] in ("abierta", "en_curso"):
            cierre = (f"<details class='res'><summary>Marcar resuelta…</summary><form class='cierre' method='post' "
                      f"action='{accion}'><input type='hidden' name='accion' value='resolver'>"
                      f"<input type='text' name='nota' maxlength='{rs.LARGO_NOTA}' required "
                      "placeholder='Qué se hizo: lo llamé, le devolvimos, hablé con el mozo…' aria-label='Qué se hizo' "
                      f"value='{e(o.get('nota') or '')}'><button class='b2 prim' type='submit'>Resuelta</button>"
                      "</form></details>")
        partes.append(
            f"<div class='op e-{e(o['estado'])}{' baja' if o['puntaje'] <= rs.QUEJA_HASTA else ''}'>"
            f"<div class='top'><span class='est' aria-label='{int(o['puntaje'])} de 5'>{_estrellas(int(o['puntaje']))}"
            f"</span><span class='pill e-{e(o['estado'])}'>{e(ESTADOS_TXT.get(o['estado'], o['estado']))}</span>"
            f"<span>{' · '.join(meta)}</span></div>"
            + (f"<p class='txt'>“{e(o['comentario'])}”</p>" if o.get("comentario") else "<p class='sub'>Sin comentario.</p>")
            + quien
            + (f"<div class='nota-int'>{e(o.get('atendido_por') or '')}: {e(o['nota'])}</div>" if o.get("nota") else "")
            + (f"<div class='acc'>{''.join(acc)}</div>" if acc else "") + cierre + "</div>")
    return "".join(partes)


ESTADOS_TXT = {"abierta": "Para atender", "en_curso": "En curso", "resuelta": "Resuelta", "sin_accion": "Sin acción"}


def _qr_comercio(n: dict, token: str) -> str:
    e = html.escape
    base = f"/comercio/{e(token)}"
    items = [{"id": None, "nombre": "Link directo (Instagram, WhatsApp, firma de mail)"}] + n["fuentes"]
    tarjetas = []
    for f in items:
        q = f"?f={int(f['id'])}" if f["id"] else ""
        link = rs.link_publico(_base(), n, f if f["id"] else None)
        tarjetas.append(
            f"<div class='card'><img src='{base}/qr.svg{q}' alt='QR {e(f['nombre'])}' loading='lazy' width='150' "
            f"height='150'><p><b>{e(f['nombre'])}</b></p><p class='link'>{e(link)}</p><div class='acc'>"
            f"<a class='b2 prim' href='{base}/cartel{q}' target='_blank' rel='noopener'>Cartel para imprimir</a>"
            f"<a class='b2' href='{base}/qr.svg{q}{'&' if q else '?'}bajar=1'>Bajar QR</a></div></div>")
    return ("<p class='sub'>Un QR por lugar: así sabés cuál rinde. Si necesitás uno nuevo (otra mesa, el ticket, "
            "la bolsa), pedíselo a Scalerics.</p><div class='qrs'>" + "".join(tarjetas) + "</div>")


def _google_comercio(n: dict, token: str) -> str:
    """Las reseñas de Google con la respuesta ya escrita. Sin JavaScript: la
    respuesta va en un cuadro de texto que se edita y se copia a mano."""
    e = html.escape
    g = rg.resumen(_db(), n["id"])
    lista = rg.resenas(_db(), n["id"], "todas", limite=30)
    cab = ("<div class='card'><h2 class='m0'>Respondé tus reseñas de Google</h2>"
           "<p class='sub'>Responder rápido, también a las buenas, es lo que más ayuda a que Google te muestre "
           "primero. Cada reseña trae una respuesta escrita: ajustala si querés, copiala y publicala en Google "
           "con tu cuenta. Después tocá «Ya la respondí».</p>"
           + (f"<p class='sub'>Última lectura de Google: {e(g['ultima_ok'][8:10])}/{e(g['ultima_ok'][5:7])} "
              f"{e(g['ultima_ok'][11:16])}.</p>" if g["ultima_ok"] else
              "<p class='sub'>Todavía no leímos tus reseñas de Google. Scalerics lo activa con tu ficha.</p>")
           + "</div>")
    if not lista:
        return cab
    partes = [cab]
    for r in lista:
        accion = f"/comercio/{e(token)}/g/{int(r['id'])}"
        cuando = r.get("publicada_en") or r["leida_en"]
        meta = [e(cuando[8:10] + "/" + cuando[5:7]), e(r["autor"]) if r.get("autor") else "reseña de Google"]
        abierta = r["estado"] == "sin_responder"
        cuerpo = (f"<p class='txt'>“{e(r['texto'])}”</p>" if r.get("texto") else "<p class='sub'>Sin texto.</p>")
        if abierta:
            cuerpo += (f"<form class='resp' method='post' action='{accion}'>"
                       f"<label class='campo'>Respuesta sugerida<textarea name='respuesta' rows='4' "
                       f"maxlength='{rg.LARGO_RESPUESTA}'>{e(r.get('sugerida') or '')}</textarea></label>"
                       f"<div class='acc'><a class='b2' href='{e(rg.link_responder(n, r))}' target='_blank' "
                       "rel='noopener'>Abrir en Google</a>"
                       "<button class='b2 prim' type='submit' name='accion' value='respondida'>Ya la respondí</button>"
                       "<button class='b2' type='submit' name='accion' value='no_responde'>No hace falta</button>"
                       "</div></form>")
        else:
            if r.get("respuesta"):
                cuerpo += f"<div class='nota-int'>Respuesta: {e(r['respuesta'])}</div>"
            cuerpo += (f"<form method='post' action='{accion}'><div class='acc'><span class='sub'>"
                       f"{e(rg.ESTADOS[r['estado']])} · {e(r.get('respondida_por') or '')}</span>"
                       "<button class='b2' type='submit' name='accion' value='sin_responder'>Volver a pendientes"
                       "</button></div></form>")
        partes.append(
            f"<div class='op{' baja' if r['rating'] <= rs.QUEJA_HASTA else ''}'><div class='top'>"
            f"<span class='est' aria-label='{int(r['rating'])} de 5'>{_estrellas(int(r['rating']))}</span>"
            f"<span class='pill e-{'abierta' if abierta else 'resuelta'}'>{e(rg.ESTADOS[r['estado']])}</span>"
            f"<span>{' · '.join(meta)}</span></div>{cuerpo}</div>")
    return "".join(partes)


@resenas_bp.route("/comercio/<token>/g/<int:rid>", methods=["POST"])
def comercio_resena(token, rid):
    a, n = _acceso_de(token)
    if not a:
        return _no_existe()
    try:
        rg.marcar(_db(), n["id"], rid, request.form.get("accion"), request.form.get("respuesta"),
                  f"{a['nombre']} · comercio"[:120])
    except rs.NoSePuede as err:
        if _codigo_error(err) == 404:
            return _no_existe()
        return redirect(f"/comercio/{token}?v=google&" + urlencode({"error": str(err)}), 303)
    return redirect(f"/comercio/{token}?v=google&ok=1", 303)


def _avisos_comercio(a: dict, token: str) -> str:
    e = html.escape
    p = ra.preferencias(_db(), a["id"])
    modos = "".join(
        f"<label class='modo'><input type='radio' name='aviso' value='{k}'{' checked' if p['aviso'] == k else ''}>"
        f"<span>{e(t)}</span></label>" for k, t in ra.MODOS.items())
    ult = ""
    if p["ultimo"]:
        u = p["ultimo"]
        ult = (f"<p class='sub'>Último mail: {e(u['creado_en'][8:10])}/{e(u['creado_en'][5:7])} "
               f"{e(u['creado_en'][11:16])}" + (" (no salió)" if u["estado"] != "enviado" else "")
               + "</p>")
    return ("<div class='card'><h2 class='m0'>Enterate de las quejas en el momento</h2>"
            "<p class='sub'>Cuando alguien puntúa de 1 a 3 o pide que lo llamen, te llega un mail con lo que "
            "escribió y un botón a este panel. Lo mismo si entra una reseña de 1 a 3 estrellas en Google: el mail "
            f"trae la respuesta ya escrita para publicarla. Si entran varias juntas, van en un mail. De {ra.SILENCIO_DESDE} a "
            f"{ra.SILENCIO_HASTA} no te escribimos: lo de la noche te llega a las {ra.SILENCIO_HASTA}.</p>"
            f"<form method='post' action='/comercio/{e(token)}/avisos'>"
            "<label class='campo'>Tu mail<input type='email' name='email' maxlength='160' autocomplete='email' "
            f"placeholder='tunombre@gmail.com' value='{e(p['email'])}'></label>"
            f"<fieldset class='modos'><legend>Cómo querés enterarte</legend>{modos}</fieldset>"
            "<button class='b2 prim' type='submit'>Guardar</button></form>" + ult + "</div>")


@resenas_bp.route("/comercio/<token>/avisos", methods=["POST"])
def comercio_avisos(token):
    a, n = _acceso_de(token)
    if not a:
        return _no_existe()
    try:
        ra.guardar_preferencias(_db(), a["id"], request.form.get("email"), request.form.get("aviso"), nid=n["id"])
    except rs.NoSePuede as err:
        return redirect(f"/comercio/{token}?v=avisos&" + urlencode({"error": str(err)}), 303)
    return redirect(f"/comercio/{token}?v=avisos&ok=1", 303)


@resenas_bp.route("/comercio/<token>")
def comercio_panel(token):
    a, n = _acceso_de(token)
    if not a:
        return _no_existe()
    e = html.escape
    antes = rs.marcar_visita(_db(), a["id"])
    vista = request.args.get("v") or "bandeja"
    if vista not in dict(VISTAS_COMERCIO):
        vista = "bandeja"
    dias = request.args.get("dias", type=int) or 30
    if dias not in DIAS_COMERCIO:
        dias = 30
    m = rs.metricas(_db(), n["id"], dias)
    base = f"/comercio/{e(token)}"
    partes = [f"<div class='cab izq'><div class='logo' aria-hidden='true'>{e((n['nombre'].strip()[:1] or '·').upper())}"
              f"</div><div><h1>{e(n['nombre'])}</h1><p class='sub m0'>Tu panel de opiniones · hola, "
              f"{e(a['nombre'])}</p></div></div>"]
    if request.args.get("ok"):
        partes.append("<div class='aviso ok mt' role='status'>Listo, quedó guardado.</div>")
    elif request.args.get("error"):
        partes.append(f"<div class='aviso mal mt' role='alert'>{e(request.args['error'][:300])}</div>")
    nov = rs.novedades(_db(), n["id"], antes)
    if nov["desde"] and not request.args.get("ok"):
        if nov["opiniones"]:
            partes.append(f"<div class='novedad'>Desde tu última visita ({e(nov['desde'][8:10])}/"
                          f"{e(nov['desde'][5:7])}): <b>{nov['opiniones']}</b> opiniones nuevas, "
                          f"{nov['google']} fueron a Google y {nov['quejas']} "
                          f"{'queja' if nov['quejas'] == 1 else 'quejas'}.</div>")
        else:
            partes.append("<div class='novedad'>Nada nuevo desde tu última visita.</div>")
    if not n["activo"]:
        partes.append("<div class='aviso mal mt'>El link para opinar está pausado: tus clientes no pueden dejar "
                      "opiniones nuevas. Hablá con Scalerics para reactivarlo.</div>")
    partes.append("<div class='periodo'>Período: " + "".join(
        f"<a href='{base}?v={vista}&amp;dias={d}'{_ACTUAL if d == dias else ''}>{d} días</a>"
        for d in DIAS_COMERCIO) + "</div>")
    partes.append(_kpis_comercio(m))
    pendientes = m["abiertas"] + m["en_curso"]
    g_pend = rg.resumen(_db(), n["id"])["sin_responder"]
    partes.append("<nav class='tabs' aria-label='Vista'>" + "".join(
        f"<a href='{base}?v={k}&amp;dias={dias}'{_ACTUAL if k == vista else ''}>{e(t)}"
        + (f"<span class='n'>{pendientes}</span>" if k == "bandeja" and pendientes else "")
        + (f"<span class='n'>{g_pend}</span>" if k == "google" and g_pend else "") + "</a>"
        for k, t in VISTAS_COMERCIO) + "</nav>")
    if vista == "qr":
        partes.append(_qr_comercio(n, token))
    elif vista == "avisos":
        partes.append(_avisos_comercio(a, token))
    elif vista == "google":
        partes.append(_google_comercio(n, token))
    else:
        ops = rs.opiniones(_db(), n["id"], vista, limite=100)
        partes.append(_opiniones_comercio(n, ops, token, vista))
        if vista == "bandeja":
            partes.append("<h2>Cómo venís</h2>" + _graficos_comercio(m))
    partes.append("<p class='sub mt'>Este link es personal: no lo compartas. Si lo perdiste o lo vio alguien que no "
                  "debía, pedile a Scalerics uno nuevo y este deja de andar.</p>")
    return _pagina(n, f"Panel · {n['nombre']}", "".join(partes), ancho=True)


@resenas_bp.route("/comercio/<token>/o/<int:oid>", methods=["POST"])
def comercio_atender(token, oid):
    a, n = _acceso_de(token)
    if not a:
        return _no_existe()
    accion = request.form.get("accion")
    datos = {"tomar": {"estado": "en_curso"}, "reabrir": {"estado": "abierta"},
             "resolver": {"estado": "resuelta", "nota": request.form.get("nota", "")}}.get(accion)
    volver = f"/comercio/{token}?v=" + ("resueltas" if accion == "resolver" else "bandeja")
    if not datos:
        return redirect(volver + "&" + urlencode({"error": "Acción no válida."}), 303)
    try:
        rs.atender(_db(), oid, datos, f"{a['nombre']} · comercio"[:120], nid=n["id"])
    except rs.NoSePuede as err:
        if _codigo_error(err) == 404:
            return _no_existe()
        return redirect(f"/comercio/{token}?v=bandeja&" + urlencode({"error": str(err)}), 303)
    return redirect(volver + "&ok=1", 303)


@resenas_bp.route("/comercio/<token>/qr.svg")
def comercio_qr(token):
    a, n = _acceso_de(token)
    if not a:
        return _error("El link no existe.", 404)
    return _qr_resp(n)


@resenas_bp.route("/comercio/<token>/cartel")
def comercio_cartel(token):
    a, n = _acceso_de(token)
    if not a:
        return _no_existe()
    return _cartel_resp(n)
