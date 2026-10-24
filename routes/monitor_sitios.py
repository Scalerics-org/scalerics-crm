"""Monitor de sitios de clientes (services/monitor_sitios.py).

Dos grupos:
- `/api/monitor/...` para el equipo: piden el panel `monitor`.
- El reporte del mes bajo `/r/<código>`, que abre el cliente sin sesión
  (exento en dashboard.require_login). La única llave es el código al azar del
  sitio: un código que no existe da 404 sin decir nada más. Muestra solo el
  nombre y la dirección del sitio y sus números, nada de la ficha del cliente.

`POST /api/monitor/tick` es para un despertador de afuera (un cron de
Cloudflare, por ejemplo): la máquina de Fly se duerme sin tráfico y el hilo
del monitor se duerme con ella. Pide MONITOR_TICK_TOKEN; sin esa variable no
existe.
"""

import hmac
import html
import os
import secrets
import threading
from datetime import timezone

from flask import Blueprint, Response, current_app, jsonify, request, session

from database import log_activity
from services import monitor_sitios as ms
from services.auth import require_panel

monitor_sitios_bp = Blueprint("monitor_sitios", __name__)

PUBLICAS = ("/r/", "/api/monitor/tick")


def _db() -> str:
    return current_app.config["DB_PATH"]


def _error(msg, codigo: int = 400):
    return jsonify({"ok": False, "error": str(msg)}), codigo


def _vista_previa() -> bool:
    return os.environ.get("CRM_PREVIEW") == "1"


def _base() -> str:
    base = (os.environ.get("CRM_URL") or request.host_url).rstrip("/")
    if base.startswith("http://") and not base.startswith(("http://localhost", "http://127.")):
        base = "https://" + base[len("http://"):]
    return base


@monitor_sitios_bp.before_request
def _candado():
    if request.path.startswith(PUBLICAS):
        return None
    return require_panel(_db(), "monitor")


def _con_link(s: dict) -> dict:
    s = dict(s)
    s["reporte_url"] = f"{_base()}/r/{s['codigo']}"
    return s


# ── equipo ───────────────────────────────────────────────────────────────────

@monitor_sitios_bp.route("/api/monitor/sitios")
def api_listar():
    sitios = [_con_link(s) for s in ms.listar(_db())]
    return jsonify({"ok": True, "resumen": ms.resumen(sitios), "sitios": sitios,
                    "intervalo_min": ms.INTERVALO.seconds // 60, "vista_previa": _vista_previa()})


@monitor_sitios_bp.route("/api/monitor/sitios", methods=["POST"])
def api_crear():
    quien = session.get("user_name", "sistema")
    try:
        s = ms.crear(_db(), request.get_json(silent=True) or {}, quien)
    except ms.NoSePuede as e:
        return _error(e)
    log_activity(_db(), quien, "monitor_sitio_alta", "monitor", s["id"], s["nombre"], s["url"],
                 user_id=session.get("user_id"))
    # El primer chequeo sale ya, en otro hilo: no hace esperar al que lo cargó.
    if not _vista_previa():
        db = _db()
        threading.Thread(target=_primer_chequeo, args=(db, s["id"]), daemon=True).start()
    return jsonify({"ok": True, "sitio": _con_link(s)}), 201


def _primer_chequeo(db: str, sid: int) -> None:
    try:
        ms.revisar(db, sid)
    except Exception:  # el hilo de fondo lo vuelve a intentar
        pass


@monitor_sitios_bp.route("/api/monitor/sitios/<int:sid>")
def api_detalle(sid):
    try:
        d = ms.detalle(_db(), sid)
        d["meses"] = ms.meses_con_datos(_db(), sid)
    except ms.NoSePuede as e:
        return _error(e, 404)
    return jsonify({"ok": True, "sitio": _con_link(d)})


@monitor_sitios_bp.route("/api/monitor/sitios/<int:sid>", methods=["PATCH"])
def api_editar(sid):
    try:
        s = ms.editar(_db(), sid, request.get_json(silent=True) or {})
    except ms.NoSePuede as e:
        return _error(e, 404 if "no existe" in str(e) else 400)
    return jsonify({"ok": True, "sitio": _con_link(s)})


@monitor_sitios_bp.route("/api/monitor/sitios/<int:sid>", methods=["DELETE"])
def api_borrar(sid):
    try:
        ms.borrar(_db(), sid)
    except ms.NoSePuede as e:
        return _error(e, 404)
    log_activity(_db(), session.get("user_name", "sistema"), "monitor_sitio_baja", "monitor", sid, "", "",
                 user_id=session.get("user_id"))
    return jsonify({"ok": True})


@monitor_sitios_bp.route("/api/monitor/sitios/<int:sid>/revisar", methods=["POST"])
def api_revisar(sid):
    if _vista_previa():
        return _error("En la vista previa no se sale a internet: los chequeos son inventados.")
    try:
        ms.revisar_manual(_db(), sid)
    except ms.NoSePuede as e:
        return _error(e, 404 if "no existe" in str(e) else 429)
    sitio = next((s for s in ms.listar(_db()) if s["id"] == sid), None)
    return jsonify({"ok": True, "sitio": _con_link(sitio) if sitio else None})


@monitor_sitios_bp.route("/api/monitor/sitios/<int:sid>/nota", methods=["PUT"])
def api_nota(sid):
    datos = request.get_json(silent=True) or {}
    try:
        ms.guardar_nota(_db(), sid, datos.get("mes"), datos.get("texto"), session.get("user_name", "sistema"))
    except ms.NoSePuede as e:
        return _error(e, 404 if "no existe" in str(e) else 400)
    return jsonify({"ok": True})


@monitor_sitios_bp.route("/api/monitor/sugeridos")
def api_sugeridos():
    return jsonify({"ok": True, "clientes": ms.sugeridos(_db())})


@monitor_sitios_bp.route("/api/monitor/tick", methods=["POST"])
def api_tick():
    token = os.environ.get("MONITOR_TICK_TOKEN", "")
    if len(token) < 24:
        return _error("No existe", 404)
    dado = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(dado.encode(), token.encode()):
        return _error("No autorizado", 403)
    if not ms.activo():
        return jsonify({"ok": True, "revisados": 0, "apagado": True})
    return jsonify({"ok": True, "revisados": ms.vuelta(_db())})


# ── reporte para el cliente ──────────────────────────────────────────────────

_CSS = """
:root{--bg:#09090f;--surface:rgba(255,255,255,.04);--border:rgba(255,255,255,.07);--text:#e2e8f0;
--muted:#64748b;--blue:#0088CC;--blue-lt:#33aadd;--blue-glow:rgba(0,136,204,.14);--blue-border:rgba(0,136,204,.28);
--green:#10B981;--green-lt:#34d399;--green-glow:rgba(16,185,129,.12);--amber:#f59e0b;--red:#ef4444;
--ff:'Inter',system-ui,-apple-system,'Segoe UI',sans-serif;--ff-d:'Raleway','Inter',system-ui,sans-serif}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--ff);line-height:1.5}
.wrap{max-width:860px;margin:0 auto;padding:32px 16px 56px}
.top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:28px}
.marca{font-family:var(--ff-d);font-weight:800;letter-spacing:.02em;font-size:1.1rem}
.marca span{color:var(--blue)}
.badge{border:1px solid var(--blue-border);background:var(--blue-glow);color:var(--blue-lt);border-radius:999px;
padding:4px 12px;font-size:.78rem;font-weight:600}
h1{font-family:var(--ff-d);font-size:clamp(1.5rem,4vw,2.2rem);margin:0 0 4px;line-height:1.2}
.sub{color:var(--muted);margin:0 0 24px;overflow-wrap:anywhere}
.sub a{color:var(--blue-lt);text-decoration:none}
.meses{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 24px}
.meses a{color:var(--muted);text-decoration:none;border:1px solid var(--border);border-radius:999px;padding:3px 10px;font-size:.8rem}
.meses a[aria-current]{color:var(--text);border-color:var(--blue-border);background:var(--blue-glow)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:24px}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:16px}
.kpi-l{font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.kpi-v{font-size:1.7rem;font-weight:800;margin-top:4px;font-variant-numeric:tabular-nums}
.kpi-s{font-size:.8rem;color:var(--muted)}
.ok{color:var(--green-lt)}.warn{color:var(--amber)}.bad{color:var(--red)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:18px;margin-bottom:16px}
.card h2{font-family:var(--ff-d);font-size:1rem;margin:0 0 12px}
.dias{display:grid;grid-template-columns:repeat(auto-fill,minmax(18px,1fr));gap:4px}
.dia{aspect-ratio:1/2;border-radius:4px;background:rgba(255,255,255,.06)}
.dia.v{background:var(--green)}.dia.a{background:var(--amber)}.dia.r{background:var(--red)}
.leyenda{display:flex;gap:14px;flex-wrap:wrap;font-size:.75rem;color:var(--muted);margin-top:10px}
.leyenda i{display:inline-block;width:10px;height:10px;aspect-ratio:auto;border-radius:3px;margin-right:5px;vertical-align:-1px}
ul.inc{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
ul.inc li{border-left:3px solid var(--red);padding:2px 0 2px 10px;font-size:.9rem}
ul.inc small{color:var(--muted)}
.nota{white-space:pre-line;font-size:.95rem}
.vacio{color:var(--muted);font-size:.9rem}
.pie{margin-top:28px;color:var(--muted);font-size:.78rem;text-align:center}
@media print{body{background:#fff;color:#0f172a}.kpi,.card{border-color:#e2e8f0;background:#fff}.meses{display:none}}
"""


def _pct(v) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.2f}%" if v < 1 else "100%"


def _clase_disp(v) -> str:
    if v is None:
        return ""
    return "ok" if v >= 0.999 else ("warn" if v >= 0.99 else "bad")


def _duracion(minutos: int) -> str:
    if minutos < 60:
        return f"{minutos} min"
    h, m = divmod(minutos, 60)
    if h < 48:
        return f"{h} h {m} min" if m else f"{h} h"
    return f"{h // 24} días"


def _hora(utc_txt) -> str:
    d = ms._dt(utc_txt)
    if not d:
        return ""
    loc = d.replace(tzinfo=timezone.utc).astimezone(ms.MVD)
    return loc.strftime("%d/%m %H:%M")


def _cabeceras(resp: Response, nonce: str) -> Response:
    resp.headers["Content-Security-Policy"] = (
        f"default-src 'none'; style-src 'nonce-{nonce}'; img-src 'self' data:; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def _pagina(titulo: str, cuerpo: str, codigo: int = 200) -> Response:
    nonce = secrets.token_urlsafe(12)
    doc = ("<!doctype html><html lang='es'><head><meta charset='utf-8'>"
           "<meta name='viewport' content='width=device-width,initial-scale=1'>"
           "<meta name='robots' content='noindex,nofollow'>"
           f"<title>{html.escape(titulo)}</title><style nonce='{nonce}'>{_CSS}</style></head>"
           f"<body><div class='wrap'>{cuerpo}</div></body></html>")
    return _cabeceras(Response(doc, status=codigo, mimetype="text/html"), nonce)


@monitor_sitios_bp.route("/r/<codigo>")
def reporte_publico(codigo):
    s = ms.por_codigo(_db(), codigo)
    if not s:
        return _pagina("No encontrado · Scalerics",
                       "<h1>Este reporte no existe</h1><p class='sub'>Revisá el link o pedinos uno nuevo.</p>", 404)
    meses = ms.meses_con_datos(_db(), s["id"])
    mes = request.args.get("mes") or meses[0]
    if mes not in meses:
        mes = meses[0]
    r = ms.reporte(_db(), s["id"], mes)
    e = html.escape
    disp = r["disponibilidad"]
    ssl_txt, ssl_cls = "—", ""
    if r["ssl_dias"] is not None:
        ssl_txt = f"{r['ssl_dias']} días"
        ssl_cls = "ok" if r["ssl_dias"] >= ms.SSL_AVISO_DIAS else ("warn" if r["ssl_dias"] >= 0 else "bad")
    ms_txt = f"{r['ms_medio'] / 1000:.2f} s".replace(".", ",") if r["ms_medio"] else "—"
    kpis = [
        ("Disponibilidad", _pct(disp).replace(".", ","), _clase_disp(disp),
         "en curso" if r["en_curso"] else "del mes"),
        ("Tiempo caído", _duracion(r["minutos_caido"]) if r["minutos_caido"] else "0 min",
         "ok" if not r["minutos_caido"] else "warn", f"{len(r['incidentes'])} incidente(s)"),
        ("Velocidad media", ms_txt, "ok" if r["ms_medio"] and r["ms_medio"] <= ms.LENTO_MS else "warn",
         "respuesta de la página de inicio"),
        ("Certificado SSL", ssl_txt, ssl_cls, "hasta que vence" if r["ssl_dias"] is not None else "sin datos"),
    ]
    kpis_html = "".join(
        f"<div class='kpi'><div class='kpi-l'>{e(l)}</div><div class='kpi-v {c}'>{e(v)}</div>"
        f"<div class='kpi-s'>{e(sub)}</div></div>" for l, v, c, sub in kpis)

    def _cls_dia(d):
        if d["disp"] is None:
            return ""
        return "v" if d["disp"] >= 0.999 else ("a" if d["disp"] >= 0.95 else "r")

    dias_html = "".join(
        f"<div class='dia {_cls_dia(d)}' title='{e(d['dia'])}: {e(_pct(d['disp']))}'></div>" for d in r["dias"])
    if r["incidentes"]:
        inc_html = "<ul class='inc'>" + "".join(
            f"<li><b>{e(_hora(i['empezo']))}</b> · {e(_duracion(i['minutos']))}"
            f"{' · sigue abierto' if not i['termino'] else ''}<br><small>{e(i['causa'] or '')}</small></li>"
            for i in r["incidentes"]) + "</ul>"
    else:
        inc_html = "<p class='vacio'>Sin caídas este mes.</p>"
    actual = " aria-current='page'"
    meses_html = "".join(
        f"<a href='?mes={m}'{actual if m == mes else ''}>{e(ms.fecha_larga(m))}</a>" for m in meses[:12])
    nota_html = (f"<div class='card'><h2>Qué hicimos este mes</h2><div class='nota'>{e(r['nota'])}</div></div>"
                 if r["nota"] else "")
    plan = "Plan de mantenimiento" if r["sitio"]["plan"] == "mantenimiento" else "Monitoreo"
    cuerpo = (
        f"<div class='top'><div class='marca'>SCALE<span>RICS</span></div><div class='badge'>{plan}</div></div>"
        f"<h1>{e(r['sitio']['nombre'])} · {e(ms.fecha_larga(mes))}</h1>"
        f"<p class='sub'><a href='{e(r['sitio']['url'])}' rel='noopener noreferrer'>{e(r['sitio']['url'])}</a>"
        f" · {r['chequeos']} chequeos en el mes</p>"
        f"<nav class='meses' aria-label='Meses'>{meses_html}</nav>"
        f"<div class='kpis'>{kpis_html}</div>"
        f"<div class='card'><h2>Día por día</h2><div class='dias'>{dias_html}</div>"
        "<div class='leyenda'><span><i class='dia v'></i>Arriba todo el día</span>"
        "<span><i class='dia a'></i>Caída corta</span>"
        "<span><i class='dia r'></i>Caída de más de una hora</span>"
        "<span><i class='dia'></i>Sin datos</span></div></div>"
        f"<div class='card'><h2>Incidentes</h2>{inc_html}</div>"
        f"{nota_html}"
        "<p class='pie'>Reporte generado por Scalerics · scalerics.com</p>"
    )
    return _pagina(f"{r['sitio']['nombre']} · {ms.fecha_larga(mes)} · Scalerics", cuerpo)
