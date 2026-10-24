"""Agenda online: reservas con link para comercios (services/agenda.py).

Dos grupos:
- `/api/agenda/...` para el equipo: piden el panel `reservas`.
- La página de reservas del comercio, `/reservar/<slug>`, que abre sin sesión
  (exenta en dashboard.require_login): es pública a propósito, el comercio la
  pone en su Instagram y en su Google. Cada turno tiene además su propio link
  `/reservar/<slug>/t/<código>` con un código al azar, para verlo, agendarlo,
  cambiarlo o cancelarlo; sin ese código no se ve ningún dato de nadie.

La página no lleva scripts: cada paso es un link o un formulario común que
vuelve a la misma página, así anda en cualquier celular y con CSP estricta.
"""

import html
import os
import secrets
from urllib.parse import urlencode

from flask import Blueprint, Response, current_app, jsonify, redirect, request, session

from database import log_activity
from services import agenda as ag
from services.auth import require_panel

agenda_bp = Blueprint("agenda", __name__)

PUBLICAS = ("/reservar/",)


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


def _codigo_error(e: ag.NoSePuede) -> int:
    return 404 if "no existe" in str(e) else 400


@agenda_bp.before_request
def _candado():
    if request.path.startswith(PUBLICAS):
        return None
    return require_panel(_db(), "reservas")


# ── equipo ───────────────────────────────────────────────────────────────────

def _con_link(n: dict) -> dict:
    n = dict(n)
    n["link"] = f"{_base()}/reservar/{n['slug']}"
    for p in n.get("profesionales", []):
        p["horarios_texto"] = ag.horarios_texto(p["horarios"])
    return n


def _turno_json(t: dict) -> dict:
    t = dict(t)
    t["link"] = ag.link_turno(_base(), t)
    t["whatsapp"] = ag.whatsapp_recordatorio(_base(), t)
    return t


@agenda_bp.route("/api/agenda")
def api_negocios():
    return jsonify({"ok": True, "negocios": [_con_link(n) for n in ag.negocios(_db())],
                    "hoy": ag.hoy_uy().isoformat()})


@agenda_bp.route("/api/agenda/clientes")
def api_clientes():
    return jsonify({"ok": True, "clientes": ag.clientes_crm(_db())})


@agenda_bp.route("/api/agenda/negocios", methods=["POST"])
def api_crear():
    try:
        n = ag.crear_negocio(_db(), request.get_json(silent=True) or {}, _quien())
    except ag.NoSePuede as e:
        return _error(e)
    log_activity(_db(), _quien(), "agenda_alta", "agenda", n["id"], n["nombre"], n["slug"],
                 user_id=session.get("user_id"))
    return jsonify({"ok": True, "negocio": _con_link(n)}), 201


@agenda_bp.route("/api/agenda/negocios/<int:nid>")
def api_negocio(nid):
    try:
        n = ag.negocio(_db(), nid)
        dia = ag._fecha(request.args.get("dia") or ag.hoy_uy().isoformat())
        dias = 7 if request.args.get("vista") == "semana" else 1
        turnos = [_turno_json(t) for t in ag.agenda(_db(), nid, dia, dias)]
        m = ag.metricas(_db(), nid)
    except ag.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "negocio": _con_link(n), "turnos": turnos, "metricas": m, "dia": dia.isoformat()})


@agenda_bp.route("/api/agenda/negocios/<int:nid>", methods=["PATCH"])
def api_editar(nid):
    try:
        n = ag.editar_negocio(_db(), nid, request.get_json(silent=True) or {})
    except ag.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "negocio": _con_link(n)})


@agenda_bp.route("/api/agenda/negocios/<int:nid>", methods=["DELETE"])
def api_borrar(nid):
    try:
        n = ag.negocio(_db(), nid, completo=False)
        ag.borrar_negocio(_db(), nid)
    except ag.NoSePuede as e:
        return _error(e, 404)
    log_activity(_db(), _quien(), "agenda_baja", "agenda", nid, n["nombre"], "", user_id=session.get("user_id"))
    return jsonify({"ok": True})


@agenda_bp.route("/api/agenda/negocios/<int:nid>/servicios", methods=["POST"])
@agenda_bp.route("/api/agenda/negocios/<int:nid>/servicios/<int:sid>", methods=["PUT"])
def api_servicio(nid, sid=None):
    try:
        s = ag.guardar_servicio(_db(), nid, request.get_json(silent=True) or {}, sid)
    except ag.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "servicio": s}), 200 if sid else 201


@agenda_bp.route("/api/agenda/negocios/<int:nid>/servicios/<int:sid>", methods=["DELETE"])
def api_borrar_servicio(nid, sid):
    try:
        ag.borrar_servicio(_db(), nid, sid)
    except ag.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True})


@agenda_bp.route("/api/agenda/negocios/<int:nid>/profesionales", methods=["POST"])
@agenda_bp.route("/api/agenda/negocios/<int:nid>/profesionales/<int:pid>", methods=["PUT"])
def api_profesional(nid, pid=None):
    try:
        p = ag.guardar_profesional(_db(), nid, request.get_json(silent=True) or {}, pid)
    except ag.NoSePuede as e:
        return _error(e, _codigo_error(e))
    p["horarios_texto"] = ag.horarios_texto(p["horarios"])
    return jsonify({"ok": True, "profesional": p}), 200 if pid else 201


@agenda_bp.route("/api/agenda/negocios/<int:nid>/profesionales/<int:pid>", methods=["DELETE"])
def api_borrar_profesional(nid, pid):
    try:
        ag.borrar_profesional(_db(), nid, pid)
    except ag.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True})


@agenda_bp.route("/api/agenda/negocios/<int:nid>/bloqueos", methods=["POST"])
def api_bloqueo(nid):
    try:
        b = ag.crear_bloqueo(_db(), nid, request.get_json(silent=True) or {})
    except ag.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "bloqueo": b}), 201


@agenda_bp.route("/api/agenda/negocios/<int:nid>/bloqueos/<int:bid>", methods=["DELETE"])
def api_borrar_bloqueo(nid, bid):
    try:
        ag.borrar_bloqueo(_db(), nid, bid)
    except ag.NoSePuede as e:
        return _error(e, 404)
    return jsonify({"ok": True})


@agenda_bp.route("/api/agenda/negocios/<int:nid>/libres")
def api_libres(nid):
    """Los horarios libres para cargar un turno desde el panel (sin topes del link)."""
    try:
        n = ag.negocio(_db(), nid)
        sid = int(request.args.get("servicio_id") or 0)
        pid = int(request.args.get("profesional_id") or 0) or None
        libres = ag.horarios_libres(_db(), n, sid, ag._fecha(request.args.get("fecha")), pid, para_equipo=True)
    except (ag.NoSePuede, ValueError) as e:
        return _error(e)
    return jsonify({"ok": True, "libres": libres})


@agenda_bp.route("/api/agenda/negocios/<int:nid>/turnos", methods=["POST"])
def api_turno_nuevo(nid):
    """El equipo carga un turno que le pidieron por teléfono o en el mostrador."""
    try:
        t = ag.reservar(_db(), nid, request.get_json(silent=True) or {}, origen="equipo", quien=_quien())
    except ag.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "turno": _turno_json(t)}), 201


@agenda_bp.route("/api/agenda/turnos/<int:tid>", methods=["PATCH"])
def api_turno(tid):
    datos = request.get_json(silent=True) or {}
    try:
        if datos.get("recordado"):
            t = ag.marcar_recordado(_db(), tid)
        else:
            t = ag.cambiar_estado(_db(), tid, datos.get("estado"))
    except ag.NoSePuede as e:
        return _error(e, _codigo_error(e))
    return jsonify({"ok": True, "turno": _turno_json(t)})


# ── página pública ───────────────────────────────────────────────────────────

_CSS = """
:root{--bg:#f8fafc;--card:#fff;--border:#e2e8f0;--text:#0f172a;--muted:#64748b;--ok:#10B981;--mal:#dc2626;
--ff:'Inter',system-ui,-apple-system,'Segoe UI',sans-serif}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--ff);line-height:1.5}
a{color:var(--acc)}
.wrap{max-width:640px;margin:0 auto;padding:0 16px 48px}
.cab{background:var(--acc);color:#fff;margin:0 -16px 20px;padding:28px 20px 22px;border-radius:0 0 20px 20px}
.cab h1{margin:0 0 4px;font-size:clamp(1.4rem,5vw,1.9rem);line-height:1.2;overflow-wrap:anywhere}
.cab p{margin:0;opacity:.9;font-size:.92rem}
.msj{white-space:pre-line;background:var(--card);border:1px solid var(--border);border-radius:14px;padding:12px 14px;
margin-bottom:16px;font-size:.92rem;overflow-wrap:anywhere}
h2{font-size:1rem;margin:22px 0 10px;display:flex;gap:8px;align-items:center}
h2 .n{display:inline-grid;place-items:center;width:24px;height:24px;border-radius:50%;background:var(--acc);
color:#fff;font-size:.78rem}
.op{display:flex;justify-content:space-between;gap:12px;align-items:center;text-decoration:none;color:inherit;
background:var(--card);border:1px solid var(--border);border-radius:14px;padding:12px 14px;margin-bottom:8px}
.op:hover{border-color:var(--acc)}
.op[aria-current]{border:2px solid var(--acc);padding:11px 13px}
.op small{display:block;color:var(--muted);font-size:.8rem}
.op .pr{font-weight:700;white-space:nowrap}
.dias{display:flex;gap:8px;overflow-x:auto;padding-bottom:6px;scroll-snap-type:x mandatory}
.dia{flex:0 0 auto;min-width:64px;text-align:center;text-decoration:none;color:inherit;background:var(--card);
border:1px solid var(--border);border-radius:12px;padding:8px 6px;scroll-snap-align:start}
.dia b{display:block;font-size:1.15rem}
.dia small{display:block;color:var(--muted);font-size:.72rem}
.dia[aria-current]{background:var(--acc);border-color:var(--acc);color:#fff}
.dia[aria-current] small{color:#fff}
.dia.lleno{opacity:.45;pointer-events:none}
.horas{display:grid;grid-template-columns:repeat(auto-fill,minmax(84px,1fr));gap:8px}
.horas label{position:relative}
.horas input{position:absolute;opacity:0}
.horas span{display:block;text-align:center;background:var(--card);border:1px solid var(--border);border-radius:10px;
padding:10px 0;font-weight:600;cursor:pointer;font-variant-numeric:tabular-nums}
.horas input:checked+span{background:var(--acc);border-color:var(--acc);color:#fff}
.horas input:focus-visible+span{outline:2px solid var(--acc);outline-offset:2px}
.form{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px 16px;margin-top:16px}
label.l{display:block;font-size:.8rem;color:var(--muted);margin:10px 0 4px}
input[type=text],input[type=tel],textarea{width:100%;border:1px solid var(--border);border-radius:10px;padding:10px;
font-family:inherit;font-size:1rem;background:#fff;color:var(--text)}
textarea{min-height:70px;resize:vertical}
.trampa{position:absolute;left:-9999px}
button,.btn{display:inline-block;background:var(--acc);color:#fff;border:0;border-radius:12px;padding:12px 20px;
font-weight:700;font-family:inherit;font-size:1rem;cursor:pointer;text-decoration:none;text-align:center}
button.sec,.btn.sec{background:transparent;color:var(--acc);border:1px solid var(--border)}
button.peligro{background:transparent;color:var(--mal);border:1px solid #fecaca}
.acc{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
a:focus-visible,button:focus-visible,input:focus-visible,textarea:focus-visible{outline:2px solid var(--acc);
outline-offset:2px}
.aviso{border-radius:12px;padding:12px 14px;margin-bottom:16px;font-size:.92rem}
.aviso.ok{background:#ecfdf5;border:1px solid #a7f3d0;color:#065f46}
.aviso.mal{background:#fef2f2;border:1px solid #fecaca;color:#991b1b}
.ticket{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:18px}
.ticket .cuando{font-size:1.25rem;font-weight:800;margin:2px 0 8px}
.ticket dl{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;margin:0;font-size:.92rem}
.ticket dt{color:var(--muted)}
.ticket dd{margin:0;overflow-wrap:anywhere}
.estado{display:inline-block;border-radius:999px;padding:2px 10px;font-size:.78rem;font-weight:700;
background:#ecfdf5;color:#047857}
.estado.cancelado{background:#fef2f2;color:#b91c1c}
.vacio,.nota{color:var(--muted);font-size:.88rem}
.pie{margin-top:32px;color:var(--muted);font-size:.75rem;text-align:center}
.pie a{color:var(--muted)}
"""


def _cabeceras(resp: Response, nonce: str) -> Response:
    resp.headers["Content-Security-Policy"] = (
        f"default-src 'none'; style-src 'nonce-{nonce}'; img-src 'self' data:; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def _pagina(n: dict, titulo: str, cuerpo: str, codigo: int = 200, indexar: bool = False) -> Response:
    nonce = secrets.token_urlsafe(12)
    color = ag._color(n.get("color") if n else None)
    robots = "" if indexar else "<meta name='robots' content='noindex,nofollow'>"
    doc = ("<!doctype html><html lang='es'><head><meta charset='utf-8'>"
           "<meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<meta name='theme-color' content='{color}'>{robots}"
           f"<title>{html.escape(titulo)}</title><style nonce='{nonce}'>:root{{--acc:{color}}}{_CSS}</style></head>"
           f"<body><div class='wrap'>{cuerpo}"
           "<p class='pie'>Reservas con <a href='https://scalerics.com'>Scalerics</a></p></div></body></html>")
    resp = _cabeceras(Response(doc, status=codigo, mimetype="text/html"), nonce)
    if not indexar:
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


def _no_existe() -> Response:
    return _pagina(None, "No encontrado", "<div class='cab'><h1>Este link no existe</h1>"
                   "<p>Revisalo o pedile el link al comercio.</p></div>", 404)


def _cabecera(n: dict) -> str:
    e = html.escape
    sub = " · ".join(e(x) for x in (n.get("rubro"), n.get("direccion")) if x)
    return f"<div class='cab'><h1>{e(n['nombre'])}</h1>{f'<p>{sub}</p>' if sub else ''}</div>"


def _precio(p) -> str:
    if p is None:
        return ""
    return "$ " + f"{p:,.0f}".replace(",", ".")


def _dur(m: int) -> str:
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h" + (f" {m % 60} min" if m % 60 else "")


def _aviso() -> str:
    if request.args.get("error"):
        return f"<div class='aviso mal' role='alert'>{html.escape(request.args['error'][:300])}</div>"
    return ""


def _url(slug: str, **q) -> str:
    q = {k: v for k, v in q.items() if v not in (None, "")}
    return f"/reservar/{slug}" + (("?" + urlencode(q)) if q else "")


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


@agenda_bp.route("/reservar/<slug>")
def reservar_pagina(slug):
    db = _db()
    n = ag.por_slug(db, slug)
    if not n:
        return _no_existe()
    e = html.escape
    cambiar = request.args.get("cambiar") or ""
    viejo = ag.turno_por_codigo(db, cambiar) if cambiar else None
    if cambiar and (not viejo or viejo["negocio_id"] != n["id"] or not ag.puede_cambiar(n, viejo)):
        return _no_existe()
    servicios = n["servicios"]
    sid = _int(request.args.get("s")) or (viejo["servicio_id"] if viejo else None)
    serv = next((s for s in servicios if s["id"] == sid), None)
    pid = _int(request.args.get("p"))
    profs = [p for p in n["profesionales"] if serv and ag._hace(p, serv["id"])] if serv else []
    if pid and not any(p["id"] == pid for p in profs):
        pid = None
    partes = [_cabecera(n)]
    if n.get("mensaje") and not viejo:
        partes.append(f"<div class='msj'>{e(n['mensaje'])}</div>")
    if viejo:
        d = ag._dt(viejo["inicio"])
        partes.append(f"<div class='aviso ok'>Estás cambiando tu turno del {e(ag.fecha_larga(d.date()))} a las "
                      f"{d:%H:%M}. Elegí el nuevo horario: el anterior se libera al confirmar.</div>")
    partes.append(_aviso())
    if not servicios or not n["profesionales"]:
        partes.append("<p class='vacio'>Todavía no hay turnos para reservar online.</p>")
        return _pagina(n, f"Reservar · {n['nombre']}", "".join(partes))
    # 1. servicio
    partes.append("<h2><span class='n'>1</span>¿Qué te querés hacer?</h2>")
    for s in servicios:
        cur = " aria-current='true'" if serv and s["id"] == serv["id"] else ""
        desc = f"<small>{e(s['descripcion'])}</small>" if s.get("descripcion") else ""
        partes.append(f"<a class='op' href='{e(_url(slug, s=s['id'], cambiar=cambiar))}'{cur}>"
                      f"<span><b>{e(s['nombre'])}</b>{desc}<small>{_dur(s['duracion_min'])}</small></span>"
                      f"<span class='pr'>{_precio(s['precio'])}</span></a>")
    if not serv:
        return _pagina(n, f"Reservar · {n['nombre']}", "".join(partes), indexar=True)
    # 2. con quién
    paso = 2
    if len(profs) > 1:
        partes.append(f"<h2><span class='n'>{paso}</span>¿Con quién?</h2>")
        cur = "" if pid else " aria-current='true'"
        partes.append(f"<a class='op' href='{e(_url(slug, s=serv['id'], cambiar=cambiar))}'{cur}>"
                      "<span><b>Con quien esté libre</b><small>Te mostramos todos los horarios</small></span></a>")
        for p in profs:
            cur = " aria-current='true'" if p["id"] == pid else ""
            partes.append(f"<a class='op' href='{e(_url(slug, s=serv['id'], p=p['id'], cambiar=cambiar))}'{cur}>"
                          f"<span><b>{e(p['nombre'])}</b></span></a>")
        paso += 1
    # 3. día
    ignorar = viejo["id"] if viejo else None
    dias = ag.dias_con_lugar(db, n, serv["id"], pid, ignorar=ignorar)
    fecha = request.args.get("d")
    if fecha not in {d["fecha"] for d in dias if d["libres"]}:
        fecha = next((d["fecha"] for d in dias if d["libres"]), None)
    partes.append(f"<h2><span class='n'>{paso}</span>¿Qué día?</h2>")
    if not fecha:
        partes.append("<p class='vacio'>No quedan horarios libres en los próximos días. "
                      + (f"Escribile al comercio por <a href='https://wa.me/{e(n['whatsapp'])}'>WhatsApp</a>."
                         if n.get("whatsapp") else "Probá más adelante.") + "</p>")
        return _pagina(n, f"Reservar · {n['nombre']}", "".join(partes))
    chips = []
    for d in dias:
        f = ag._fecha(d["fecha"])
        cur = " aria-current='date'" if d["fecha"] == fecha else ""
        clase = "dia" + ("" if d["libres"] else " lleno")
        estado = f"{d['libres']} libres" if d["libres"] else "completo"
        chips.append(f"<a class='{clase}' href='{e(_url(slug, s=serv['id'], p=pid, d=d['fecha'], cambiar=cambiar))}'"
                     f"{cur} aria-label='{e(ag.fecha_larga(f))}, {estado}'><small>{ag.DIAS_CORTO[f.weekday()]}</small>"
                     f"<b>{f.day}</b><small>{ag.MESES[f.month - 1][:3]}</small></a>")
    partes.append(f"<div class='dias'>{''.join(chips)}</div>")
    paso += 1
    # 4. hora y datos
    libres = ag.horarios_libres(db, n, serv["id"], ag._fecha(fecha), pid, ignorar)
    nombres = {p["id"]: p["nombre"] for p in profs}
    partes.append(f"<h2><span class='n'>{paso}</span>¿A qué hora? <small class='nota'>"
                  f"{e(ag.fecha_larga(ag._fecha(fecha)))}</small></h2>")
    horas = []
    for i, x in enumerate(libres):
        quien = "" if pid or len(profs) < 2 else f" title='{e(', '.join(nombres[p] for p in x['profesionales']))}'"
        horas.append(f"<label{quien}><input type='radio' name='hora' value='{x['hora']}' required"
                     f"{' checked' if i == 0 and len(libres) == 1 else ''}><span>{x['hora']}</span></label>")
    campos = ""
    if not viejo:
        campos = ("<label class='l' for='cliente'>Tu nombre</label>"
                  f"<input type='text' id='cliente' name='cliente' maxlength='{ag.LARGO_NOMBRE}' required "
                  "autocomplete='name'>"
                  "<label class='l' for='telefono'>Tu celular (para avisarte si algo cambia)</label>"
                  "<input type='tel' id='telefono' name='telefono' required inputmode='tel' autocomplete='tel' "
                  "placeholder='099 123 456'>"
                  "<label class='l' for='nota'>¿Algo que tengamos que saber? (opcional)</label>"
                  f"<textarea id='nota' name='nota' maxlength='{ag.LARGO_TEXTO}'></textarea>"
                  "<div class='trampa' aria-hidden='true'><label>No completar<input type='text' name='web' "
                  "tabindex='-1' autocomplete='off'></label></div>")
    resumen = (f"{e(serv['nombre'])} · {_dur(serv['duracion_min'])}"
               + (f" · {_precio(serv['precio'])}" if serv["precio"] is not None else "")
               + (f" · con {e(nombres[pid])}" if pid else ""))
    partes.append(
        f"<form method='post' action='/reservar/{e(slug)}'>"
        f"<input type='hidden' name='servicio_id' value='{serv['id']}'>"
        f"<input type='hidden' name='profesional_id' value='{pid or ''}'>"
        f"<input type='hidden' name='fecha' value='{e(fecha)}'>"
        f"<input type='hidden' name='cambiar' value='{e(cambiar)}'>"
        f"<div class='horas' role='radiogroup' aria-label='Horarios libres'>{''.join(horas)}</div>"
        f"<div class='form'><p class='nota'>{resumen}</p>{campos}"
        f"<div class='acc'><button type='submit'>{'Cambiar a este horario' if viejo else 'Reservar turno'}</button>"
        "</div></div></form>")
    if n["cancelar_hasta_h"]:
        partes.append(f"<p class='nota'>Podés cambiar o cancelar hasta {n['cancelar_hasta_h']} h antes desde el "
                      "link que te damos al reservar.</p>")
    return _pagina(n, f"Reservar · {n['nombre']}", "".join(partes))


@agenda_bp.route("/reservar/<slug>", methods=["POST"])
def reservar_post(slug):
    db = _db()
    n = ag.por_slug(db, slug)
    if not n:
        return _no_existe()
    f = request.form
    cambiar = f.get("cambiar") or None
    volver = _url(slug, s=f.get("servicio_id"), p=f.get("profesional_id"), d=f.get("fecha"), cambiar=cambiar)
    if f.get("web"):
        # La trampa para robots: un humano no ve ese campo.
        return redirect(_url(slug), code=303)
    try:
        t = ag.reservar(db, n["id"], f, origen="link", cambiar=cambiar)
    except ag.NoSePuede as e:
        sep = "&" if "?" in volver else "?"
        return redirect(volver + sep + urlencode({"error": str(e)[:300]}), code=303)
    log_activity(db, "cliente", "agenda_cambio" if cambiar else "agenda_reserva", "agenda", t["id"], n["nombre"],
                 f"{t['servicio']} {t['inicio']}")
    return redirect(f"/reservar/{slug}/t/{t['codigo']}?ok={'cambio' if cambiar else 'nuevo'}", code=303)


def _turno_o_404(slug: str, codigo: str):
    n = ag.por_slug(_db(), slug)
    t = ag.turno_por_codigo(_db(), codigo) if n else None
    if not t or t["negocio_id"] != n["id"]:
        return None, None
    return n, t


@agenda_bp.route("/reservar/<slug>/t/<codigo>")
def turno_pagina(slug, codigo):
    n, t = _turno_o_404(slug, codigo)
    if not t:
        return _no_existe()
    e = html.escape
    d = ag._dt(t["inicio"])
    ok = request.args.get("ok")
    aviso = {"nuevo": "¡Listo! Tu turno quedó reservado. Guardá este link: desde acá lo cambiás o cancelás.",
             "cambio": "Listo, cambiamos tu turno.", "cancelado": "Cancelaste el turno. ¡Gracias por avisar!"}.get(ok)
    partes = [_cabecera(n)]
    if aviso:
        partes.append(f"<div class='aviso ok' role='status'>{aviso}</div>")
    partes.append(_aviso())
    estado = {"confirmado": "Confirmado", "asistio": "Realizado", "falto": "No asististe",
              "cancelado": "Cancelado"}[t["estado"]]
    filas = [("Servicio", e(t["servicio"])), ("Con", e(t["profesional"])), ("Duración", _dur(t["duracion_min"]))]
    if t["precio"] is not None:
        filas.append(("Precio", _precio(t["precio"])))
    if n.get("direccion"):
        filas.append(("Dónde", e(n["direccion"])))
    filas.append(("A nombre de", e(t["cliente"])))
    partes.append(
        f"<div class='ticket'><span class='estado {e(t['estado'])}'>{estado}</span>"
        f"<div class='cuando'>{e(ag.fecha_larga(d.date()).capitalize())}, {d:%H:%M}</div>"
        "<dl>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in filas) + "</dl></div>")
    if t["estado"] == "confirmado":
        acciones = [f"<a class='btn sec' href='/reservar/{e(slug)}/t/{e(codigo)}/turno.ics'>Agregar al calendario</a>"]
        if ag.puede_cambiar(n, t):
            acciones.append(f"<a class='btn sec' href='{e(_url(slug, cambiar=codigo))}'>Cambiar día u hora</a>")
            partes.append("<div class='acc'>" + "".join(acciones) + "</div>"
                          f"<form method='post' action='/reservar/{e(slug)}/t/{e(codigo)}/cancelar' class='acc'>"
                          "<button type='submit' class='peligro'>Cancelar turno</button></form>")
        else:
            partes.append("<div class='acc'>" + "".join(acciones) + "</div>"
                          f"<p class='nota'>Faltan menos de {n['cancelar_hasta_h']} h: para cambiarlo, escribile "
                          "al comercio.</p>")
    else:
        partes.append(f"<div class='acc'><a class='btn' href='{e(_url(slug))}'>Reservar otro turno</a></div>")
    if n.get("whatsapp"):
        partes.append(f"<p class='nota'>¿Dudas? Escribile a {e(n['nombre'])} por "
                      f"<a href='https://wa.me/{e(n['whatsapp'])}'>WhatsApp</a>.</p>")
    return _pagina(n, f"Tu turno · {n['nombre']}", "".join(partes))


@agenda_bp.route("/reservar/<slug>/t/<codigo>/cancelar", methods=["POST"])
def turno_cancelar(slug, codigo):
    n, t = _turno_o_404(slug, codigo)
    if not t:
        return _no_existe()
    try:
        ag.cancelar(_db(), codigo, por_cliente=True)
    except ag.NoSePuede as e:
        return redirect(f"/reservar/{slug}/t/{codigo}?" + urlencode({"error": str(e)}), code=303)
    log_activity(_db(), "cliente", "agenda_cancela", "agenda", t["id"], n["nombre"], t["inicio"])
    return redirect(f"/reservar/{slug}/t/{codigo}?ok=cancelado", code=303)


@agenda_bp.route("/reservar/<slug>/t/<codigo>/turno.ics")
def turno_ics(slug, codigo):
    n, t = _turno_o_404(slug, codigo)
    if not t:
        return _no_existe()
    resp = Response(ag.ics(t, _base()), mimetype="text/calendar")
    resp.headers["Content-Disposition"] = "attachment; filename=turno.ics"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp

