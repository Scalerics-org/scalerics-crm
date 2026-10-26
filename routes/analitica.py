"""Analítica de contactos de los sitios de clientes (services/analitica.py).

Tres grupos:
- `/api/analitica/...` para el equipo: piden el panel `analitica`.
- `/a/s.js` (el script que se pega en el sitio), `/a/e` (adonde manda los
  eventos) y `/a/prueba/<clave>` (una página de mentira con el script puesto,
  para probar la instalación sin tocar el sitio del cliente). Sin sesión.
- El informe del mes bajo `/informe/<código>`, que abre el dueño del comercio
  sin sesión. La única llave es el código al azar: uno que no existe da 404.
  Muestra el nombre del sitio y sus números, nada de la ficha del cliente.

Las públicas están exentas en dashboard.require_login (PUBLICAS).
"""

import html
import os
import secrets
from flask import Blueprint, Response, current_app, jsonify, request, session

from database import log_activity
from services import analitica as an
from services.auth import require_panel

analitica_bp = Blueprint("analitica", __name__)

PUBLICAS = ("/a/", "/informe/")


def _db() -> str:
    return current_app.config["DB_PATH"]


def _error(msg, codigo: int = 400):
    return jsonify({"ok": False, "error": str(msg)}), codigo


def _base() -> str:
    base = (os.environ.get("CRM_URL") or request.host_url).rstrip("/")
    if base.startswith("http://") and not base.startswith(("http://localhost", "http://127.")):
        base = "https://" + base[len("http://"):]
    return base


@analitica_bp.before_request
def _candado():
    if request.path.startswith(PUBLICAS):
        return None
    return require_panel(_db(), "analitica")


def _con_links(s: dict) -> dict:
    s = dict(s)
    base = _base()
    s["informe_url"] = f"{base}/informe/{s['codigo']}"
    s["prueba_url"] = f"{base}/a/prueba/{s['clave']}"
    s["snippet"] = f'<script defer src="{base}/a/s.js" data-sitio="{s["clave"]}"></script>'
    return s


def _dias() -> int:
    try:
        d = int(request.args.get("dias", 30))
    except ValueError:
        d = 30
    return d if d in (7, 30, 90) else 30


# ── equipo ───────────────────────────────────────────────────────────────────

@analitica_bp.route("/api/analitica/sitios")
def api_listar():
    dias = _dias()
    sitios = [_con_links(s) for s in an.listar(_db(), dias)]
    return jsonify({"ok": True, "dias": dias, "resumen": an.resumen(sitios), "sitios": sitios,
                    "vista_previa": os.environ.get("CRM_PREVIEW") == "1"})


@analitica_bp.route("/api/analitica/sitios", methods=["POST"])
def api_crear():
    quien = session.get("user_name", "sistema")
    try:
        s = an.crear(_db(), request.get_json(silent=True) or {}, quien)
    except an.NoSePuede as e:
        return _error(e)
    log_activity(_db(), quien, "analitica_sitio_alta", "analitica", s["id"], s["nombre"], ", ".join(s["dominios"]),
                 user_id=session.get("user_id"))
    return jsonify({"ok": True, "sitio": _con_links(s)}), 201


@analitica_bp.route("/api/analitica/sitios/<int:sid>")
def api_detalle(sid):
    try:
        d = an.detalle(_db(), sid, _dias())
    except an.NoSePuede as e:
        return _error(e, 404)
    return jsonify({"ok": True, "sitio": _con_links(d)})


@analitica_bp.route("/api/analitica/sitios/<int:sid>", methods=["PATCH"])
def api_editar(sid):
    try:
        s = an.editar(_db(), sid, request.get_json(silent=True) or {})
    except an.NoSePuede as e:
        return _error(e, 404 if "no existe" in str(e) else 400)
    return jsonify({"ok": True, "sitio": _con_links(s)})


@analitica_bp.route("/api/analitica/sitios/<int:sid>/nuevo-link", methods=["POST"])
def api_nuevo_link(sid):
    try:
        s = an.nuevo_codigo(_db(), sid)
    except an.NoSePuede as e:
        return _error(e, 404)
    return jsonify({"ok": True, "sitio": _con_links(s)})


@analitica_bp.route("/api/analitica/sitios/<int:sid>", methods=["DELETE"])
def api_borrar(sid):
    try:
        an.borrar(_db(), sid)
    except an.NoSePuede as e:
        return _error(e, 404)
    log_activity(_db(), session.get("user_name", "sistema"), "analitica_sitio_baja", "analitica", sid, "", "",
                 user_id=session.get("user_id"))
    return jsonify({"ok": True})


@analitica_bp.route("/api/analitica/sugeridos")
def api_sugeridos():
    return jsonify({"ok": True, "clientes": an.sugeridos(_db())})


# ── el script y adonde manda ─────────────────────────────────────────────────

# Sin cookies y sin nada de afuera. Lo que guarda en sessionStorage es de dónde
# vino la visita, para atribuirle el contacto aunque pase por tres páginas.
# Se detectan solos: wa.me y api.whatsapp.com, tel:, mailto:, el envío de un
# formulario (no el buscador ni el carrito) y cualquier elemento con
# data-scalerics="whatsapp|llamada|mail|formulario".
SCRIPT = r"""(function(){
var s=document.currentScript;if(!s||navigator.webdriver)return;
var k=s.getAttribute('data-sitio');if(!k)return;
var api=s.src.replace(/\/a\/s\.js.*$/,'/a/e');
var o=null,nueva=false;try{o=JSON.parse(sessionStorage.getItem('sc_a')||'null')}catch(e){}
if(!o){nueva=true;var q=new URLSearchParams(location.search);
o={r:document.referrer||'',us:q.get('utm_source')||'',um:q.get('utm_medium')||'',uc:q.get('utm_campaign')||'',
g:(q.has('gclid')||q.has('gbraid')||q.has('wbraid'))?1:0,f:q.has('fbclid')?1:0};
try{sessionStorage.setItem('sc_a',JSON.stringify(o))}catch(e){}}
function env(t,d){var b={k:k,t:t,u:location.href.split('#')[0],e:(t==='vista'&&nueva)?1:0,w:window.innerWidth||0,d:d||''};
for(var x in o)b[x]=o[x];var j=JSON.stringify(b);
try{if(navigator.sendBeacon&&navigator.sendBeacon(api,new Blob([j],{type:'text/plain'})))return}catch(e){}
try{fetch(api,{method:'POST',body:j,keepalive:true,mode:'no-cors',credentials:'omit'})}catch(e){}}
var TIPOS={whatsapp:1,llamada:1,mail:1,formulario:1};
document.addEventListener('click',function(ev){
var a=ev.target&&ev.target.closest&&ev.target.closest('a[href],[data-scalerics]');if(!a)return;
var c=a.getAttribute('data-scalerics');if(c){if(TIPOS[c])env(c);return}
var h=(a.getAttribute('href')||'').trim();
if(/^(https?:)?\/\/(wa\.me|api\.whatsapp\.com|web\.whatsapp\.com|wa\.link)\b/i.test(h)||/^whatsapp:/i.test(h))env('whatsapp');
else if(/^tel:/i.test(h))env('llamada');else if(/^mailto:/i.test(h))env('mail');},true);
document.addEventListener('submit',function(ev){var f=ev.target;if(!f||f.tagName!=='FORM')return;
if(f.getAttribute('role')==='search'||f.querySelector('input[type=search],input[name=s]'))return;
if(/cart|carrito|woocommerce-checkout|login|register/i.test(f.className+' '+(f.id||'')))return;
env('formulario',f.id||f.getAttribute('name')||'');},true);
env('vista');nueva=false;
})();
"""


@analitica_bp.route("/a/s.js")
def script():
    resp = Response(SCRIPT, mimetype="application/javascript")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
    return resp


def _ip() -> str:
    # Fly pone la IP real en Fly-Client-IP; el primer X-Forwarded-For lo puede
    # escribir cualquiera, así que va solo como respaldo.
    return (request.headers.get("Fly-Client-IP")
            or (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            or request.remote_addr or "desconocida")


@analitica_bp.route("/a/e", methods=["POST", "OPTIONS"])
def recibir():
    if request.method == "POST":
        an.registrar(_db(), request.get_data(cache=False)[:an.LARGO_CUERPO + 1], _ip(),
                     request.headers.get("User-Agent", ""), request.headers.get("Origin", ""),
                     request.host)
    # 202 a todo: el que prueba claves o dominios no aprende nada.
    resp = Response(status=202)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "POST"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ── páginas públicas ─────────────────────────────────────────────────────────

_CSS = """
:root{--bg:#09090f;--surface:rgba(255,255,255,.04);--border:rgba(255,255,255,.07);--text:#e2e8f0;
--muted:#64748b;--blue:#0088CC;--blue-lt:#33aadd;--blue-dk:#005f8e;--blue-glow:rgba(0,136,204,.14);
--blue-border:rgba(0,136,204,.28);--green:#10B981;--green-lt:#34d399;--green-glow:rgba(16,185,129,.12);
--amber:#f59e0b;--red:#ef4444;
--ff:'Inter',system-ui,-apple-system,'Segoe UI',sans-serif;--ff-d:'Raleway','Inter',system-ui,sans-serif}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--ff);line-height:1.5}
.wrap{max-width:900px;margin:0 auto;padding:32px 16px 56px}
.top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:28px}
.marca{font-family:var(--ff-d);font-weight:800;letter-spacing:.02em;font-size:1.1rem}
.marca span{color:var(--blue)}
.badge{border:1px solid var(--blue-border);background:var(--blue-glow);color:var(--blue-lt);border-radius:999px;
padding:4px 12px;font-size:.78rem;font-weight:600}
h1{font-family:var(--ff-d);font-size:clamp(1.5rem,4vw,2.2rem);margin:0 0 4px;line-height:1.2}
.sub{color:var(--muted);margin:0 0 24px;overflow-wrap:anywhere}
.meses{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 24px}
.meses a{color:var(--muted);text-decoration:none;border:1px solid var(--border);border-radius:999px;padding:3px 10px;font-size:.8rem}
.meses a[aria-current]{color:var(--text);border-color:var(--blue-border);background:var(--blue-glow)}
.heroe{background:linear-gradient(135deg,rgba(0,136,204,.18),rgba(16,185,129,.10));border:1px solid var(--blue-border);
border-radius:18px;padding:22px;margin-bottom:16px;display:flex;gap:18px;align-items:center;flex-wrap:wrap}
.heroe-n{font-family:var(--ff-d);font-size:clamp(2.6rem,9vw,3.6rem);font-weight:800;line-height:1;color:#fff;
font-variant-numeric:tabular-nums}
.heroe-t{flex:1;min-width:200px}
.heroe-t b{display:block;font-size:1.1rem}
.heroe-t span{color:var(--muted);font-size:.9rem}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin-bottom:16px}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:14px 16px}
.kpi-l{font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.kpi-v{font-size:1.5rem;font-weight:800;margin-top:4px;font-variant-numeric:tabular-nums}
.kpi-s{font-size:.78rem;color:var(--muted)}
.sube{color:var(--green-lt)}.baja{color:var(--red)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:18px;margin-bottom:16px}
.card h2{font-family:var(--ff-d);font-size:1rem;margin:0 0 12px}
.grafico svg{width:100%;height:auto;display:block}
.leyenda{display:flex;gap:14px;flex-wrap:wrap;font-size:.75rem;color:var(--muted);margin-top:8px}
.leyenda i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px}
.c-v{background:rgba(0,136,204,.45)}.c-c{background:var(--green)}
table{width:100%;border-collapse:collapse;font-size:.88rem}
th{text-align:left;color:var(--muted);font-weight:600;font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;
padding:0 8px 8px 0}
td{padding:8px 8px 8px 0;border-top:1px solid var(--border);font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
td.n,th.n{text-align:right}
.dos{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}
.dos .card{margin-bottom:0}
.dato{font-size:.92rem;margin:0 0 8px}
.dato b{color:#fff}
.vacio{color:var(--muted);font-size:.9rem}
.sep{height:16px}
.pie{margin-top:28px;color:var(--muted);font-size:.78rem;text-align:center}
@media print{body{background:#fff;color:#0f172a}.kpi,.card,.heroe{border-color:#e2e8f0;background:#fff}
.heroe-n,.dato b{color:#0f172a}.meses{display:none}}
"""


def _cabeceras(resp: Response, nonce: str, script: bool = False) -> Response:
    resp.headers["Content-Security-Policy"] = (
        f"default-src 'none'; style-src 'nonce-{nonce}'; img-src 'self' data:; "
        + ("script-src 'self'; connect-src 'self'; form-action 'self'; " if script else "form-action 'none'; ")
        + "base-uri 'none'; frame-ancestors 'none'")
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Referrer-Policy"] = "no-referrer" if not script else "strict-origin-when-cross-origin"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def _pagina(titulo: str, cuerpo: str, codigo: int = 200, css: str = _CSS, cabeza: str = "",
            script: bool = False) -> Response:
    nonce = secrets.token_urlsafe(12)
    doc = ("<!doctype html><html lang='es'><head><meta charset='utf-8'>"
           "<meta name='viewport' content='width=device-width,initial-scale=1'>"
           "<meta name='robots' content='noindex,nofollow'>"
           f"<title>{html.escape(titulo)}</title><style nonce='{nonce}'>{css}</style>{cabeza}</head>"
           f"<body>{cuerpo}</body></html>")
    return _cabeceras(Response(doc, status=codigo, mimetype="text/html"), nonce, script)


def _num(n) -> str:
    return f"{n:,}".replace(",", ".")


def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%".replace(".", ",")


def _var(v) -> str:
    if v is None:
        return ""
    flecha, cls = ("▲", "sube") if v >= 0 else ("▼", "baja")
    return f"<span class='{cls}'>{flecha} {abs(v) * 100:.0f}%</span> contra el mes anterior"


def _grafico(dias: list) -> str:
    """Barras de visitas con los contactos encima, en SVG plano (sin scripts)."""
    if not dias:
        return ""
    ancho, alto, pad = 620, 170, 18
    n = len(dias)
    paso = (ancho - 2) / n
    tope_v = max((d["visitas"] for d in dias), default=0) or 1
    tope_c = max((d["contactos"] for d in dias), default=0) or 1
    barras = []
    for i, d in enumerate(dias):
        x = 1 + i * paso
        hv = (alto - pad) * d["visitas"] / tope_v
        hc = (alto - pad) * 0.6 * d["contactos"] / tope_c
        etiqueta = html.escape(f"{d['dia'][8:]}/{d['dia'][5:7]}: {d['visitas']} visitas, {d['contactos']} contactos")
        barras.append(
            f"<g><title>{etiqueta}</title>"
            f"<rect x='{x:.1f}' y='{alto - hv:.1f}' width='{paso * .78:.1f}' height='{hv:.1f}' rx='2' "
            "fill='rgba(0,136,204,.45)'/>"
            f"<rect x='{x + paso * .2:.1f}' y='{alto - hc:.1f}' width='{paso * .38:.1f}' height='{hc:.1f}' rx='2' "
            "fill='#10B981'/></g>")
    return (f"<svg viewBox='0 0 {ancho} {alto}' role='img' aria-label='Visitas y contactos por día'>"
            + "".join(barras) + "</svg>")


@analitica_bp.route("/informe/<codigo>")
def informe(codigo):
    s = an.por_codigo(_db(), codigo)
    if not s:
        return _pagina("No encontrado · Scalerics", "<div class='wrap'><h1>Este informe no existe</h1>"
                       "<p class='sub'>Revisá el link o pedinos uno nuevo.</p></div>", 404)
    meses = an.meses(s)
    mes = request.args.get("mes") or meses[0]
    if mes not in meses:
        mes = meses[0]
    r = an.reporte(_db(), s["id"], mes)
    e = html.escape
    pt = r["por_tipo"]
    por = {"whatsapp": "por WhatsApp", "llamada": "por teléfono", "mail": "por mail", "formulario": "por formulario"}
    tipos_txt = " · ".join(f"{_num(pt[t])} {por[t]}" for t in por if pt[t])
    heroe = (f"<div class='heroe'><div class='heroe-n'>{_num(r['contactos'])}</div><div class='heroe-t'>"
             f"<b>personas te escribieron o llamaron desde la web</b>"
             f"<span>{e(tipos_txt) or 'Todavía no hay contactos este mes.'}</span><br>"
             f"<span>{_var(r['var_contactos'])}</span></div></div>")
    kpis = [("WhatsApp", _num(pt["whatsapp"]), "personas que tocaron el botón"),
            ("Llamadas", _num(pt["llamada"]), "desde el celular"),
            ("Formularios y mails", _num(pt["formulario"] + pt["mail"]), "consultas escritas"),
            ("Visitas", _num(r["visitas"]), f"{_num(r['visitantes'])} personas distintas"),
            ("Conversión", _pct(r["conversion"]), "de cada 100 visitas, cuántas te contactan")]
    if r["compras"]:
        kpis.insert(3, ("Compras online", _num(r["compras"]), "pedidos terminados en la tienda"))
    if r["valor_estimado"]:
        kpis.append(("Valor estimado", "$ " + _num(r["valor_estimado"]), "según tu ticket medio y cierre"))
    kpis_html = "".join(f"<div class='kpi'><div class='kpi-l'>{e(l)}</div><div class='kpi-v'>{e(v)}</div>"
                        f"<div class='kpi-s'>{e(sub)}</div></div>" for l, v, sub in kpis)

    if r["canales"]:
        canales = "".join(f"<tr><td>{e(c['canal'])}</td><td class='n'>{_num(c['visitas'])}</td>"
                          f"<td class='n'>{_num(c['contactos'])}</td></tr>" for c in r["canales"][:8])
        canales = ("<table><thead><tr><th>Origen</th><th class='n'>Visitas</th><th class='n'>Contactos</th></tr>"
                   f"</thead><tbody>{canales}</tbody></table>")
    else:
        canales = "<p class='vacio'>Sin visitas este mes.</p>"
    if r["paginas"]:
        paginas = "".join(f"<tr><td>{e(p['pagina'])}</td><td class='n'>{_num(p['vistas'])}</td>"
                          f"<td class='n'>{_num(p['contactos'])}</td></tr>" for p in r["paginas"][:8])
        paginas = ("<table><thead><tr><th>Página</th><th class='n'>Vistas</th><th class='n'>Contactos</th></tr>"
                   f"</thead><tbody>{paginas}</tbody></table>")
    else:
        paginas = "<p class='vacio'>Sin visitas este mes.</p>"

    datos = []
    if r["movil_pct"] is not None:
        datos.append(f"<p class='dato'><b>{r['movil_pct']}%</b> de las visitas fueron desde el celular.</p>")
    if r["contactos"] and r["fuera_de_hora"]:
        datos.append(f"<p class='dato'><b>{_num(r['fuera_de_hora'])}</b> contactos llegaron fuera del horario "
                     "comercial (antes de las 9 o después de las 19): la web atiende cuando el local está cerrado.</p>")
    mejor = next((c for c in r["canales"] if c["contactos"]), None)
    if mejor:
        datos.append(f"<p class='dato'>El origen que más contactos trajo fue <b>{e(mejor['canal'])}</b>.</p>")
    datos_html = f"<div class='card'><h2>Para tener en cuenta</h2>{''.join(datos)}</div>" if datos else ""

    actual = " aria-current='page'"
    meses_html = "".join(f"<a href='?mes={m}'{actual if m == mes else ''}>{e(an.fecha_larga(m))}</a>"
                         for m in meses)
    cuerpo = (
        "<div class='wrap'>"
        "<div class='top'><div class='marca'>SCALE<span>RICS</span></div><div class='badge'>Informe de la web</div></div>"
        f"<h1>{e(s['nombre'])} · {e(an.fecha_larga(mes))}</h1>"
        f"<p class='sub'>{e(', '.join(s['dominios']))}{' · mes en curso' if r['en_curso'] else ''}</p>"
        f"<nav class='meses' aria-label='Meses'>{meses_html}</nav>"
        f"{heroe}<div class='kpis'>{kpis_html}</div>"
        f"<div class='card'><h2>Día por día</h2><div class='grafico'>{_grafico(r['dias'])}</div>"
        "<div class='leyenda'><span><i class='c-v'></i>Visitas</span><span><i class='c-c'></i>Contactos</span></div></div>"
        f"<div class='dos'><div class='card'><h2>De dónde vienen</h2>{canales}</div>"
        f"<div class='card'><h2>Las páginas que más venden</h2>{paginas}</div></div>"
        f"<div class='sep'></div>{datos_html}"
        "<p class='pie'>Medido sin cookies por Scalerics · scalerics.com</p></div>"
    )
    return _pagina(f"{s['nombre']} · {an.fecha_larga(mes)} · Scalerics", cuerpo)


_CSS_PRUEBA = """
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;background:#f8fafc;
color:#0f172a;line-height:1.5}
.aviso{background:#0088CC;color:#fff;text-align:center;font-size:.85rem;padding:8px 16px}
.wrap{max-width:760px;margin:0 auto;padding:32px 16px}
h1{font-size:1.8rem;margin:0 0 6px}p{color:#475569}
.botones{display:flex;gap:10px;flex-wrap:wrap;margin:22px 0}
.b{display:inline-block;padding:11px 18px;border-radius:10px;text-decoration:none;font-weight:600;border:0;
font-size:.95rem;cursor:pointer;font-family:inherit}
.wa{background:#10B981;color:#fff}.tel{background:#0088CC;color:#fff}.mail{background:#e2e8f0;color:#0f172a}
form{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:18px;display:flex;flex-direction:column;gap:10px}
input,textarea{font:inherit;padding:9px 11px;border:1px solid #cbd5e1;border-radius:8px}
.nav{display:flex;gap:14px;font-size:.9rem;margin-bottom:24px}.nav a{color:#0088CC}
small{color:#64748b}
"""


@analitica_bp.route("/a/prueba/<clave>")
def prueba(clave):
    s = an.por_clave(_db(), clave)
    if not s:
        return _pagina("No encontrado", "<div class='wrap'><h1>Esta página de prueba no existe</h1></div>", 404,
                       css=_CSS_PRUEBA)
    e = html.escape
    enviado = "nombre" in request.args
    cabeza = f"<script defer src='/a/s.js' data-sitio='{e(s['clave'])}'></script>"
    cuerpo = (
        "<div class='aviso'>Página de prueba de Scalerics: cada botón suma un contacto en la analítica de "
        f"<b>{e(s['nombre'])}</b>. Nadie recibe nada.</div><div class='wrap'>"
        "<div class='nav'><a href='?p=servicios'>Servicios</a><a href='?p=precios'>Precios</a></div>"
        f"<h1>{e(s['nombre'])}</h1><p>Así se mide la web del comercio: tocá un botón o mandá el formulario y "
        "mirá cómo aparece en el panel Analítica del CRM en unos segundos.</p>"
        "<div class='botones'>"
        "<a class='b wa' href='https://wa.me/59800000000?text=Hola' target='_blank' rel='noopener'>Escribinos por WhatsApp</a>"
        "<a class='b tel' href='tel:+59800000000'>Llamar</a>"
        "<a class='b mail' href='mailto:hola@ejemplo.invalid'>Mandar un mail</a></div>"
        + ("<p><b>¡Gracias! Recibimos tu consulta.</b></p>" if enviado else
           "<form id='contacto' method='get'><b>Pedí presupuesto</b>"
           "<input name='nombre' placeholder='Tu nombre' maxlength='60' required>"
           "<textarea name='msg' placeholder='Contanos qué necesitás' maxlength='200'></textarea>"
           "<button class='b tel' type='submit'>Enviar</button>"
           "<small>No se guarda lo que escribas: solo cuenta que se mandó un formulario.</small></form>")
        + "</div>")
    return _pagina(f"Prueba · {s['nombre']}", cuerpo, css=_CSS_PRUEBA, cabeza=cabeza, script=True)
