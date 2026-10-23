"""Propuesta online (services/propuestas_online.py): el presupuesto como link.

Dos grupos:
- `/api/propuestas-online/...` para el equipo: piden el panel `propuestas`.
- Los públicos bajo `/p/`, que abre el cliente sin sesión (exentos en
  dashboard.require_login). La única llave es el código del link: todo pasa
  por `po.por_codigo` y un código que no existe da 404 sin decir nada más.

El documento es HTML hecho con IA y editable a mano, así que nunca se sirve
como página del CRM: `/p/<código>/doc` sale con `CSP: sandbox` (sin scripts,
origen opaco) y la página de la propuesta lo muestra en un iframe sandbox.
"""

import html
import json
import os
import secrets
import sqlite3
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, current_app, jsonify, request, session

from database import get_budget_for_client, get_business, log_activity
from services import propuestas_online as po
from services.auth import require_panel

propuestas_online_bp = Blueprint("propuestas_online", __name__)

PUBLICAS = ("/p/",)
_MVD = ZoneInfo("America/Montevideo")


def _db() -> str:
    return current_app.config["DB_PATH"]


def _base() -> str:
    """La URL pública del CRM. En Fly el request llega por http detrás del proxy."""
    base = (os.environ.get("CRM_URL") or request.host_url).rstrip("/")
    if base.startswith("http://") and not base.startswith(("http://localhost", "http://127.")):
        base = "https://" + base[len("http://"):]
    return base


def _ip() -> str:
    # Fly pone la IP real del cliente en Fly-Client-IP; detrás de otro proxy,
    # el primer X-Forwarded-For. Solo se usa para el hash de po.huella_ip.
    return (request.headers.get("Fly-Client-IP")
            or (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            or request.remote_addr or "")


def _interna() -> bool:
    """El equipo mirando el link no es el cliente leyéndolo."""
    return bool(session.get("logged_in") and session.get("user_id"))


def _error(msg, codigo: int = 400):
    return jsonify({"ok": False, "error": str(msg)}), codigo


@propuestas_online_bp.before_request
def _candado():
    if request.path.startswith(PUBLICAS):
        return None
    return require_panel(_db(), "propuestas")


def _con_link(p: dict) -> dict:
    p = dict(p)
    p["url"] = f"{_base()}/p/{p['codigo']}"
    return p


# ── equipo ───────────────────────────────────────────────────────────────────

@propuestas_online_bp.route("/api/propuestas-online", methods=["POST"])
def api_publicar():
    datos = request.get_json(silent=True) or {}
    try:
        aid = int(datos.get("attachment_id"))
    except (TypeError, ValueError):
        return _error("Falta el presupuesto a publicar")
    conn = sqlite3.connect(_db())
    conn.row_factory = sqlite3.Row
    try:
        adj = conn.execute(
            "SELECT id, lead_id, section, name, file_data, mime_type FROM lead_attachments WHERE id = ?",
            (aid,)).fetchone()
    finally:
        conn.close()
    if not adj or adj["section"] != "budget" or adj["mime_type"] != "text/html" or not adj["file_data"]:
        return _error("Solo se publica un presupuesto en HTML de la ficha", 404)
    lead = get_business(_db(), adj["lead_id"]) or {}
    monto = datos.get("monto")
    if monto is None:
        budget = get_budget_for_client(_db(), adj["lead_id"]) or {}
        monto = budget.get("total_amount")
    quien = session.get("user_name", "sistema")
    titulo = datos.get("titulo") or f"Propuesta para {lead.get('name') or 'el cliente'}"
    try:
        r = po.publicar(_db(), adj["lead_id"], adj["file_data"], titulo, quien,
                        attachment_id=aid, monto=monto,
                        dias=datos.get("dias") or po.DIAS_VIGENCIA)
    except po.NoSePuede as e:
        return _error(e)
    log_activity(_db(), quien, "propuesta_publicada", "lead", adj["lead_id"],
                 lead.get("name", ""), adj["name"], user_id=session.get("user_id"))
    return jsonify({"ok": True, "propuesta": _con_link(po.ver(_db(), r["id"]))}), 201


@propuestas_online_bp.route("/api/propuestas-online")
def api_listar():
    lead_id = request.args.get("lead_id", type=int)
    propuestas = [_con_link(p) for p in po.listar(_db(), lead_id)]
    return jsonify({"ok": True, "resumen": po.resumen(propuestas), "propuestas": propuestas})


@propuestas_online_bp.route("/api/propuestas-online/<int:pid>")
def api_ver(pid):
    try:
        return jsonify({"ok": True, "propuesta": _con_link(po.ver(_db(), pid))})
    except po.NoSePuede as e:
        return _error(e, 404)


@propuestas_online_bp.route("/api/propuestas-online/<int:pid>/revocar", methods=["POST"])
def api_revocar(pid):
    quien = session.get("user_name", "sistema")
    try:
        p = po.revocar(_db(), pid, quien)
    except po.NoSePuede as e:
        return _error(e)
    log_activity(_db(), quien, "propuesta_revocada", "lead", p["lead_id"],
                 p.get("lead_nombre") or "", p["titulo"], user_id=session.get("user_id"))
    return jsonify({"ok": True, "propuesta": _con_link(p)})


# ── públicos ─────────────────────────────────────────────────────────────────

def _cabeceras(resp: Response, csp: str) -> Response:
    resp.headers["Content-Security-Policy"] = csp
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def _fecha(texto) -> str:
    dt = po._dt(texto)
    return dt.astimezone(_MVD).strftime("%d/%m/%Y") if dt else ""


def _monto(p) -> str:
    if p.get("monto") is None:
        return ""
    entero = int(p["monto"]) == p["monto"]
    cifra = f"{p['monto']:,.0f}" if entero else f"{p['monto']:,.2f}"
    return f"{p['moneda']} {cifra.replace(',', 'X').replace('.', ',').replace('X', '.')}"


_CSS = """
:root{--bg:#09090f;--surface:rgba(255,255,255,.04);--border:rgba(255,255,255,.07);--text:#e2e8f0;
--muted:#64748b;--blue:#0088CC;--blue-lt:#33aadd;--blue-dk:#005f8e;--green:#10B981;--green-lt:#34d399;
--ff:'Inter',system-ui,sans-serif}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{background:var(--bg);color:var(--text);font-family:var(--ff);display:flex;flex-direction:column;font-size:15px}
.top{display:flex;align-items:center;gap:14px;padding:12px 20px;border-bottom:1px solid var(--border);flex-wrap:wrap}
.top img{height:26px}
.top .t{flex:1;min-width:180px}
.top h1{font-size:15px;font-weight:600;line-height:1.3}
.top small{color:var(--muted);font-size:12.5px}
.monto{font-weight:700;color:var(--blue-lt);font-size:15px;white-space:nowrap}
.doc{flex:1;min-height:0;background:#f7f8fc}
.doc iframe{width:100%;height:100%;border:0;display:block;background:#fff}
.bar{display:flex;gap:10px;padding:12px 20px;border-top:1px solid var(--border);align-items:center;flex-wrap:wrap;background:#0d0d14}
.bar .q{flex:1;color:var(--muted);font-size:13px;min-width:160px}
.btn{font:inherit;font-weight:600;font-size:14px;border-radius:10px;padding:11px 18px;border:1px solid var(--border);
background:var(--surface);color:var(--text);cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:6px}
.btn:hover{border-color:rgba(255,255,255,.2)}
.btn.ok{background:var(--green);border-color:var(--green);color:#04130d}
.btn.ok:hover{background:var(--green-lt)}
.btn.link{background:none;border:none;color:var(--muted);padding:11px 8px}
dialog{margin:auto;background:#11111a;color:var(--text);border:1px solid var(--border);border-radius:16px;padding:24px;
width:min(440px,calc(100vw - 32px))}
dialog::backdrop{background:rgba(0,0,0,.6)}
dialog h2{font-size:18px;margin-bottom:6px}
dialog p{color:var(--muted);font-size:13.5px;margin-bottom:16px;line-height:1.5}
label{display:block;font-size:12.5px;color:var(--muted);margin:12px 0 5px}
input[type=text],input[type=email],textarea{width:100%;font:inherit;font-size:14px;background:var(--bg);color:var(--text);
border:1px solid rgba(255,255,255,.14);border-radius:9px;padding:10px 12px}
input:focus,textarea:focus{outline:2px solid var(--blue);border-color:transparent}
.chk{display:flex;gap:10px;align-items:flex-start;margin:16px 0;font-size:13.5px;color:var(--text);line-height:1.45}
.chk input{margin-top:3px;width:16px;height:16px;accent-color:var(--green)}
.acc{display:flex;gap:8px;justify-content:flex-end;margin-top:18px;flex-wrap:wrap}
.err{color:#fca5a5;font-size:13px;min-height:18px;margin-top:8px}
.firma{font-family:'Brush Script MT','Segoe Script',cursive;font-size:26px;color:var(--blue-lt);min-height:34px;margin-top:6px}
.fin{flex:1;display:grid;place-items:center;padding:24px}
.fin .c{max-width:460px;text-align:center;background:var(--surface);border:1px solid var(--border);border-radius:18px;padding:36px 28px}
.fin .ic{font-size:38px;margin-bottom:10px}
.fin h2{font-size:21px;margin-bottom:8px}
.fin p{color:var(--muted);line-height:1.55;font-size:14px}
.fin .btn{margin-top:20px}
@media(max-width:600px){.top{padding:10px 14px}.bar{padding:10px 14px}.bar .q{display:none}.bar .btn{flex:1;justify-content:center}}
"""

_LOGO = "https://raw.githubusercontent.com/Scalerics-org/scalerics-assets/main/logo_full_alt.png"


def _wa(texto: str) -> str:
    tel = "".join(c for c in os.environ.get("FACTORY_PHONE", "") if c.isdigit())
    if not tel:
        return ""
    from urllib.parse import quote
    return f"https://wa.me/{tel}?text={quote(texto)}"


def _pagina(cuerpo: str, titulo: str, nonce: str, script: str = "", codigo: int = 200) -> Response:
    doc = (
        '<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex,nofollow">'
        f"<title>{html.escape(titulo)}</title>"
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">'
        f"<style>{_CSS}</style></head><body>{cuerpo}"
        + (f'<script nonce="{nonce}">{script}</script>' if script else "")
        + "</body></html>")
    resp = Response(doc, status=codigo, mimetype="text/html")
    return _cabeceras(resp, (
        "default-src 'none'; "
        f"script-src 'nonce-{nonce}'; "
        "style-src 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src https: data:; "
        "frame-src 'self'; connect-src 'self'; form-action 'none'; "
        "base-uri 'none'; frame-ancestors 'none'"))


def _cierre(icono: str, titulo: str, texto: str, nonce: str, wa: str = "", codigo: int = 200) -> Response:
    boton = (f'<a class="btn" href="{html.escape(wa)}" target="_blank" rel="noopener">'
             "Escribinos por WhatsApp</a>") if wa else ""
    cuerpo = (f'<header class="top"><img src="{_LOGO}" alt="Scalerics"></header>'
              f'<main class="fin"><div class="c"><div class="ic" aria-hidden="true">{icono}</div>'
              f"<h2>{html.escape(titulo)}</h2><p>{html.escape(texto)}</p>{boton}</div></main>")
    return _pagina(cuerpo, f"{titulo} · Scalerics", nonce, codigo=codigo)


_NO_EXISTE = ("Este link no existe", "Revisá que esté completo, o pedinos uno nuevo.")


def _estado_final(p: dict, nonce: str):
    """La página para una propuesta que ya no se puede responder, o None."""
    s = p["senal"]
    wa = _wa(f"Hola, tengo una consulta sobre la propuesta para {p.get('lead_nombre') or 'mi negocio'}.")
    if s == "aceptada":
        return _cierre("✅", "¡Propuesta aceptada!",
                       f"La aceptó {p.get('firma_nombre') or 'tu equipo'} el {_fecha(p['resuelta_en'])}. "
                       "Te vamos a escribir para coordinar el arranque.", nonce, wa)
    if s == "rechazada":
        return _cierre("👋", "Gracias por avisarnos",
                       "Registramos tu respuesta. Si cambia algo, escribinos y lo retomamos.", nonce, wa)
    if s == "vencida":
        return _cierre("⏳", "Esta propuesta venció",
                       f"Era válida hasta el {_fecha(p['vence_en'])}. Pedinos una actualizada.", nonce, wa)
    if s == "revocada":
        return _cierre("🔒", "Esta propuesta ya no está disponible",
                       "La reemplazamos por otra. Pedinos el link nuevo.", nonce, wa)
    return None


_SCRIPT = """
(function(){
var C=%(codigo)s, base='/p/'+C;
function post(ruta, datos){return fetch(base+ruta,{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify(datos||{}),credentials:'same-origin'}).then(function(r){return r.json().catch(function(){return {ok:false,error:'Error de conexión'}})});}
var paso=%(paso)d*1000;
setInterval(function(){if(document.visibilityState==='visible'&&document.hasFocus())post('/lectura');},paso);
function abrir(id){var d=document.getElementById(id);d.showModal();var i=d.querySelector('input,textarea');if(i)i.focus();}
function cerrar(id){document.getElementById(id).close();}
document.getElementById('b-aceptar').onclick=function(){abrir('d-aceptar');};
document.getElementById('b-no').onclick=function(){abrir('d-no');};
document.querySelectorAll('[data-cerrar]').forEach(function(b){b.onclick=function(){cerrar(b.dataset.cerrar);};});
var nom=document.getElementById('f-nombre'), firma=document.getElementById('f-firma');
nom.oninput=function(){firma.textContent=nom.value;};
function enviar(boton, ruta, datos, err){
  boton.disabled=true; err.textContent='';
  post(ruta, datos).then(function(r){
    if(r.ok){location.reload();return;}
    err.textContent=r.error||'No se pudo enviar. Probá de nuevo.'; boton.disabled=false;
  });
}
document.getElementById('f-ok').onclick=function(e){
  e.preventDefault();
  enviar(this,'/aceptar',{nombre:nom.value,email:document.getElementById('f-email').value,
    acepto:document.getElementById('f-chk').checked},document.getElementById('f-err'));
};
document.getElementById('n-ok').onclick=function(e){
  e.preventDefault();
  enviar(this,'/rechazar',{motivo:document.getElementById('n-motivo').value},document.getElementById('n-err'));
};
})();
"""


@propuestas_online_bp.route("/p/<codigo>")
def publica(codigo):
    nonce = secrets.token_urlsafe(12)
    p = po.por_codigo(_db(), codigo)
    if not p:
        return _cierre("🔎", *_NO_EXISTE, nonce, codigo=404)
    final = _estado_final(p, nonce)
    if final:
        return final
    conto = po.registrar_vista(_db(), p, _ip(), request.headers.get("User-Agent"), _interna())
    if conto and p["vistas"] == 0:
        log_activity(_db(), "cliente", "propuesta_abierta", "lead", p["lead_id"],
                     p.get("lead_nombre") or "", p["titulo"])
    negocio = html.escape(p.get("lead_nombre") or "tu negocio")
    monto = _monto(p)
    wa = _wa(f"Hola, tengo una consulta sobre la propuesta para {p.get('lead_nombre') or 'mi negocio'}.")
    aviso = ('<div style="background:#0088CC;color:#fff;font-size:12.5px;text-align:center;padding:6px">'
             "Estás viendo esto con tu sesión del CRM: tus visitas no se cuentan.</div>") if _interna() else ""
    cuerpo = (
        f"{aviso}"
        f'<header class="top"><img src="{_LOGO}" alt="Scalerics">'
        f'<div class="t"><h1>{html.escape(p["titulo"])}</h1>'
        f"<small>Para {negocio} · válida hasta el {_fecha(p['vence_en'])}</small></div>"
        + (f'<div class="monto">{html.escape(monto)}</div>' if monto else "")
        + "</header>"
        f'<main class="doc"><iframe src="/p/{html.escape(p["codigo"])}/doc" sandbox="" '
        'referrerpolicy="no-referrer" title="Documento de la propuesta"></iframe></main>'
        '<footer class="bar"><span class="q">¿Todo claro? Aceptala desde acá y arrancamos.</span>'
        + (f'<a class="btn" href="{html.escape(wa)}" target="_blank" rel="noopener">Tengo una duda</a>' if wa else "")
        + '<button class="btn link" id="b-no" type="button">No, gracias</button>'
        '<button class="btn ok" id="b-aceptar" type="button">Aceptar propuesta</button></footer>'
        '<dialog id="d-aceptar" aria-labelledby="d-aceptar-t"><form method="dialog">'
        '<h2 id="d-aceptar-t">Aceptar la propuesta</h2>'
        f"<p>Al aceptar confirmás el alcance y las condiciones del documento"
        + (f" por <b>{html.escape(monto)}</b>" if monto else "") + ".</p>"
        '<label for="f-nombre">Nombre y apellido</label>'
        f'<input type="text" id="f-nombre" maxlength="{po.LARGO_NOMBRE}" autocomplete="name" required>'
        '<div class="firma" aria-hidden="true" id="f-firma"></div>'
        '<label for="f-email">Email (opcional, te mandamos la confirmación)</label>'
        f'<input type="email" id="f-email" maxlength="{po.LARGO_EMAIL}" autocomplete="email">'
        '<label class="chk"><input type="checkbox" id="f-chk">'
        f"<span>Leí y acepto la propuesta para {negocio}.</span></label>"
        '<div class="err" id="f-err" role="alert"></div>'
        '<div class="acc"><button class="btn" type="button" data-cerrar="d-aceptar">Volver</button>'
        '<button class="btn ok" id="f-ok" type="submit">Aceptar</button></div></form></dialog>'
        '<dialog id="d-no" aria-labelledby="d-no-t"><form method="dialog">'
        '<h2 id="d-no-t">¿Nos contás por qué?</h2>'
        "<p>Es opcional, pero nos ayuda mucho: precio, tiempos, alcance, o lo dejan para más adelante.</p>"
        f'<textarea id="n-motivo" rows="4" maxlength="{po.LARGO_MOTIVO}" aria-label="Motivo"></textarea>'
        '<div class="err" id="n-err" role="alert"></div>'
        '<div class="acc"><button class="btn" type="button" data-cerrar="d-no">Volver</button>'
        '<button class="btn" id="n-ok" type="submit">Enviar respuesta</button></div></form></dialog>')
    script = _SCRIPT % {"codigo": json.dumps(p["codigo"]), "paso": po.PASO_LATIDO}
    return _pagina(cuerpo, f"{p['titulo']} · Scalerics", nonce, script)


@propuestas_online_bp.route("/p/<codigo>/doc")
def publica_doc(codigo):
    p = po.por_codigo(_db(), codigo)
    if not p or p["senal"] in ("revocada", "vencida"):
        return Response("No encontrado", status=404, mimetype="text/plain")
    resp = Response(p["html"], mimetype="text/html")
    # Sin scripts y con origen opaco: el HTML del presupuesto no corre nada.
    return _cabeceras(resp, "sandbox; frame-ancestors 'self'")


def _json_publico():
    if request.content_length and request.content_length > 8 * 1024:
        return None
    datos = request.get_json(silent=True)
    return datos if isinstance(datos, dict) else {}


@propuestas_online_bp.route("/p/<codigo>/lectura", methods=["POST"])
def publica_lectura(codigo):
    p = po.por_codigo(_db(), codigo)
    if not p:
        return _error("No encontrado", 404)
    return jsonify({"ok": True, "contado": po.registrar_lectura(_db(), p, _ip(), _interna())})


@propuestas_online_bp.route("/p/<codigo>/aceptar", methods=["POST"])
def publica_aceptar(codigo):
    p = po.por_codigo(_db(), codigo)
    datos = _json_publico()
    if not p:
        return _error("No encontrado", 404)
    if datos is None:
        return _error("Pedido demasiado grande", 413)
    if _interna():
        return _error("Estás con tu sesión del CRM: la acepta el cliente desde su link.", 403)
    ya = p["estado"] == "aceptada"
    try:
        r = po.aceptar(_db(), p, datos.get("nombre"), datos.get("email"), datos.get("acepto"),
                       _ip(), request.headers.get("User-Agent"))
    except po.NoSePuede as e:
        return _error(e, 409)
    if not ya:
        log_activity(_db(), "cliente", "propuesta_aceptada", "lead", p["lead_id"],
                     p.get("lead_nombre") or "", f"{r['firma_nombre']} · {_monto(p) or p['titulo']}")
    return jsonify({"ok": True})


@propuestas_online_bp.route("/p/<codigo>/rechazar", methods=["POST"])
def publica_rechazar(codigo):
    p = po.por_codigo(_db(), codigo)
    datos = _json_publico()
    if not p:
        return _error("No encontrado", 404)
    if datos is None:
        return _error("Pedido demasiado grande", 413)
    if _interna():
        return _error("Estás con tu sesión del CRM: responde el cliente desde su link.", 403)
    ya = p["estado"] == "rechazada"
    try:
        r = po.rechazar(_db(), p, datos.get("motivo"), _ip(), request.headers.get("User-Agent"))
    except po.NoSePuede as e:
        return _error(e, 409)
    if not ya:
        log_activity(_db(), "cliente", "propuesta_rechazada", "lead", p["lead_id"],
                     p.get("lead_nombre") or "", r.get("motivo") or "")
    return jsonify({"ok": True})
