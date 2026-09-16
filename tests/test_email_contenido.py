"""Ver el mail que se le mandó a cada uno, desde Email marketing.

Pedido de Juan (15/9), con la captura de un mail de discovery: "Hay alguna
forma de en email marketing, ver el mail que se le manda a cada uno?"

Nada acá habla con Resend: el GET usa un cliente HTTP falso, y la
reconstrucción corre la función de envío dentro de `capturar_envio`, que no
toca la red.
"""

import json
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import dashboard
import routes.email_marketing as rutas
import services.email_contenido as ec
import services.email_marketing as em
import services.email_service as es
from database import create_user, init_db, insert_business
from services.meta_reminders import CLAVE_NEGOCIO, CLAVE_RUBRO

HTML = dashboard.DASHBOARD_HTML
RAIZ = Path(__file__).resolve().parents[1]
SRC = (RAIZ / "dashboard.py").read_text(encoding="utf-8")

sin_node = pytest.mark.skipif(shutil.which("node") is None, reason="node no esta instalado")


def _entre(texto, desde, hasta):
    i = texto.index(desde)
    return texto[i:texto.index(hasta, i + len(desde))]


PANEL = _entre(SRC, "<!-- ======= EMAIL MARKETING PANEL ======= -->",
               "<!-- ======= FIN EMAIL MARKETING PANEL ======= -->")
JS = _entre(SRC, "// ========== Email marketing ==========", "// ========== Daily Programador ==========")

REMITENTE_DISCOVERY = "Scalerics <hola@envios.scalerics.com>"


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _cache_limpio():
    ec.limpiar_cache()
    yield
    ec.limpiar_cache()


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "contenido.db")
    init_db(ruta)
    return ruta


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("CRM_URL", "https://crm.test")
    ruta = str(tmp_path / "contenido-app.db")
    init_db(ruta)
    a = dashboard.create_app(ruta)
    a.config["TESTING"] = True
    yield a
    es.configurar_registro(None)


def _cli(app, email, paneles=None):
    db_path = app.config["DB_PATH"]
    uid = create_user(db_path, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    if paneles is not None:
        conn = sqlite3.connect(db_path)
        rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)",
                           ("rol-" + email, json.dumps(paneles))).lastrowid
        conn.execute("UPDATE users SET role_id=? WHERE id=?", (rid, uid))
        conn.commit()
        conn.close()
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "test"
    return c


@pytest.fixture
def jefe(app):
    return _cli(app, "jefe@scalerics.com")


def _sql(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _envio(db_path, **datos):
    base = {"resend_id": None, "tipo": "discovery", "destinatario": "a@x.uy", "asunto": "Hola",
            "business_id": None, "numero": None, "enviado_at": em.ahora_utc(), "origen": "crm",
            "estado_envio": "enviado", "estado": "enviado"}
    base.update(datos)
    cols = ", ".join(base)
    return _sql(db_path, f"INSERT INTO emails_enviados ({cols}) VALUES ({', '.join('?' for _ in base)})",
                list(base.values()))


_telefonos = iter(range(700000, 799999))


def _comercio(db_path, nombre="Inmobiliaria Sur", rubro="inmobiliaria", mail="inmo@x.uy"):
    return insert_business(db_path, {"name": nombre, "phone": f"+598{next(_telefonos)}", "email": mail,
                                     "category": rubro, "source": "discovery"})


def _lead_meta(db_path, negocio="Zsoul", rubro="crear_mi_ecommerce", mail="lead@x.uy"):
    return insert_business(db_path, {"name": "Lead", "phone": f"+598{next(_telefonos)}", "email": mail,
                                     "source": "meta",
                                     "form_data": json.dumps({CLAVE_NEGOCIO: negocio, CLAVE_RUBRO: rubro})})


class _Resp:
    def __init__(self, status, cuerpo):
        self.status_code = status
        self._cuerpo = cuerpo

    def json(self):
        return self._cuerpo


class _HttpFalso:
    def __init__(self, respuestas):
        self.respuestas = respuestas
        self.pedidos = []

    def get(self, url, headers=None, timeout=None):
        self.pedidos.append((url, headers, timeout))
        estado, cuerpo = self.respuestas.get(url.rsplit("/", 1)[-1], (404, {}))
        return _Resp(estado, cuerpo)


CRUDO = ('<html><body><p onclick="robar()">Hola <b>Ana</b></p><script>alert(1)</script>'
         '<a href="https://calendly.com/scalerics" onmouseover="x()">Agendar</a>'
         '<a href="javascript:alert(2)">malo</a>'
         '<img src="https://t.resend.dev/abre.gif" width="1" height="1">'
         '<img src="https://assets/logo.png" alt="Scalerics" width="130"></body></html>')

DE_RESEND = {"html": CRUDO, "text": "Hola Ana\nBaja: https://crm.test/baja/tok123abc",
             "from": "Scalerics CRM <crm@noreply.scalerics.com>", "subject": "Asunto de Resend",
             "to": ["juan@scalerics.com"], "last_event": "delivered"}


# ── desde Resend ─────────────────────────────────────────────────────────────

def test_el_contenido_sale_de_resend_sanitizado_y_se_cachea(app, jefe, monkeypatch):
    db_path = app.config["DB_PATH"]
    eid = _envio(db_path, resend_id="re_contenido_1", tipo="aviso_equipo",
                 destinatario="juan@scalerics.com", asunto="Asunto guardado", enviado_at="2026-09-15 15:30:00")
    http = _HttpFalso({"re_contenido_1": (200, DE_RESEND)})
    monkeypatch.setenv("RESEND_API_KEY", "re_test_falsa")
    monkeypatch.setattr(rutas, "_cliente_http", lambda: http)
    monkeypatch.setattr(ec, "_PAUSA_MINIMA", 0)

    r = jefe.get(f"/api/email-mkt/envios/{eid}/contenido")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    d = r.get_json()
    assert (d["fuente"], d["avisos"], d["motivo"]) == ("resend", [], None)
    assert d["asunto"] == "Asunto de Resend" and d["remitente"] == "Scalerics CRM <crm@noreply.scalerics.com>"
    assert d["destinatario"] == "juan@scalerics.com" and d["fecha_local"] == "15/09/2026 12:30"
    assert d["tipo_etiqueta"] == "Avisos al equipo" and d["estado"] == "enviado"
    assert "Hola <b>Ana</b>" in d["html"] and 'width="130"' in d["html"]
    for prohibido in ("<script", "alert(", "onclick", "onmouseover", "javascript:", "href=", "abre.gif"):
        assert prohibido not in d["html"], prohibido
    assert 'title="Link: https://calendly.com/scalerics">Agendar</a>' in d["html"]
    assert d["text"] == "Hola Ana\nBaja: https://crm.test/baja/(token)"

    (url, cabeceras, timeout), = http.pedidos
    assert url == "https://api.resend.com/emails/re_contenido_1"
    assert cabeceras == {"Authorization": "Bearer re_test_falsa"} and 0 < timeout <= 15

    assert jefe.get(f"/api/email-mkt/envios/{eid}/contenido").get_json()["html"] == d["html"]
    assert len(http.pedidos) == 1, "el segundo pedido seguido sale del cache"

    conn = sqlite3.connect(db_path)
    guardado = json.dumps(conn.execute("SELECT * FROM emails_enviados").fetchall())
    conn.close()
    assert "Ana" not in guardado and "calendly" not in guardado, "el cuerpo no se guarda en la base"


def test_el_cache_vence_a_los_pocos_minutos(db):
    eid = _envio(db, resend_id="re_cache_vence", tipo="alerta")
    http = _HttpFalso({"re_cache_vence": (200, DE_RESEND)})
    reloj = [1000.0]
    for _ in range(3):
        ec.contenido_de_envio(db, eid, "k", http=http, reloj=lambda: reloj[0], dormir=lambda s: None)
    assert len(http.pedidos) == 1
    reloj[0] += ec._TTL_CACHE + 1
    ec.contenido_de_envio(db, eid, "k", http=http, reloj=lambda: reloj[0], dormir=lambda s: None)
    assert len(http.pedidos) == 2


def test_entre_dos_pedidos_a_resend_hay_una_pausa(db):
    uno = _envio(db, resend_id="re_pausa_uno", tipo="alerta")
    dos = _envio(db, resend_id="re_pausa_dos", tipo="alerta")
    http = _HttpFalso({"re_pausa_uno": (200, DE_RESEND), "re_pausa_dos": (200, DE_RESEND)})
    pausas = []
    ec.contenido_de_envio(db, uno, "k", http=http, reloj=lambda: 50.0, dormir=pausas.append)
    ec.contenido_de_envio(db, dos, "k", http=http, reloj=lambda: 50.0, dormir=pausas.append)
    assert len(http.pedidos) == 2
    assert pausas == [pytest.approx(ec._PAUSA_MINIMA)], "la primera no espera, la segunda sí"


def test_un_429_avisa_y_no_se_cachea(db):
    eid = _envio(db, resend_id="re_limite_429", tipo="tarea")
    http = _HttpFalso({"re_limite_429": (429, {})})
    for _ in range(2):
        d = ec.contenido_de_envio(db, eid, "k", http=http, dormir=lambda s: None)
    assert len(http.pedidos) == 2
    assert d["fuente"] == "no_disponible" and "Resend pidió esperar" in d["avisos"][0]
    assert d["motivo"] == ec.NO_DISPONIBLE


def test_si_resend_no_lo_tiene_un_aviso_dice_que_no_esta_disponible(db):
    eid = _envio(db, resend_id="re_viejo_404", tipo="aviso_equipo")
    d = ec.contenido_de_envio(db, eid, "k", http=_HttpFalso({}), dormir=lambda s: None)
    assert d["fuente"] == "no_disponible" and d["html"] is None and d["text"] is None
    assert d["avisos"] == ["Resend ya no tiene el contenido de este mail."]
    assert d["motivo"] == "El contenido de este mail no está disponible."


# ── reconstrucción ───────────────────────────────────────────────────────────

def _discovery_armado(db_path, monkeypatch, resend_id=None, numero=2):
    monkeypatch.setenv("DISCOVERY_FROM_EMAIL", REMITENTE_DISCOVERY)
    monkeypatch.setenv("CRM_URL", "https://crm.test")
    bid = _comercio(db_path)
    _sql(db_path, "INSERT INTO discovery_reminders (business_id, numero, token, sent_at) VALUES (?,?,?,?)",
         (bid, numero, "tokdiscovery99", "2026-08-20 13:00:00"))
    eid = _envio(db_path, resend_id=resend_id, tipo="discovery", destinatario="inmo@x.uy", business_id=bid,
                 numero=numero, asunto="Discovery en frío · contacto 2", origen="historico",
                 enviado_at="2026-08-20 13:00:00")
    with es.capturar_envio() as capturados:
        es.send_discovery_email("inmo@x.uy", "Inmobiliaria Sur", "inmobiliaria",
                                "https://crm.test/baja/tokdiscovery99", numero)
    return eid, capturados[0]


def test_un_discovery_historico_se_reconstruye_con_la_misma_funcion(db, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_falsa")  # igual no puede salir nada
    eid, esperado = _discovery_armado(db, monkeypatch)
    filas_antes = _sql(db, "SELECT 1")  # noqa: F841
    http = _HttpFalso({})
    d = ec.contenido_de_envio(db, eid, "", http=http)

    assert d["fuente"] == "reconstruido" and d["avisos"] == [ec.AVISO_RECONSTRUIDO]
    assert "Reconstruido con la plantilla actual: puede diferir un poco del mail original" in d["avisos"][0]
    assert d["html"] == ec.sanitizar_html(esperado["html"])
    assert d["text"] == esperado["text"].replace("tokdiscovery99", "(token)")
    assert d["asunto"] == esperado["subject"] == "Último mail para Inmobiliaria Sur"
    assert d["remitente"] == REMITENTE_DISCOVERY and d["destinatario"] == "inmo@x.uy"
    assert "tokdiscovery99" not in d["html"] + d["text"]
    assert http.pedidos == [], "sin id de Resend no se consulta"
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM emails_enviados").fetchone()[0] == 1, "reconstruir no registra un envío"
    conn.close()


def test_si_resend_da_404_cae_a_la_reconstruccion_con_los_dos_avisos(db, monkeypatch):
    eid, esperado = _discovery_armado(db, monkeypatch, resend_id="re_discovery_404")
    d = ec.contenido_de_envio(db, eid, "k", http=_HttpFalso({}), dormir=lambda s: None)
    assert d["fuente"] == "reconstruido"
    assert d["avisos"] == ["Resend ya no tiene el contenido de este mail.", ec.AVISO_RECONSTRUIDO]
    assert d["html"] == ec.sanitizar_html(esperado["html"])


def test_sin_api_key_cae_a_la_reconstruccion_o_avisa(app, jefe, monkeypatch):
    db_path = app.config["DB_PATH"]
    eid, _ = _discovery_armado(db_path, monkeypatch, resend_id="re_sin_clave_1")
    otro = _envio(db_path, resend_id="re_sin_clave_2", tipo="alerta")
    http = _HttpFalso({"re_sin_clave_1": (200, DE_RESEND), "re_sin_clave_2": (200, DE_RESEND)})
    monkeypatch.setattr(rutas, "_cliente_http", lambda: http)

    d = jefe.get(f"/api/email-mkt/envios/{eid}/contenido").get_json()
    assert d["fuente"] == "reconstruido" and "RESEND_API_KEY" in d["avisos"][0]
    assert d["avisos"][-1] == ec.AVISO_RECONSTRUIDO

    d = jefe.get(f"/api/email-mkt/envios/{otro}/contenido").get_json()
    assert d["fuente"] == "no_disponible" and "RESEND_API_KEY" in d["avisos"][0]
    assert d["motivo"] == ec.NO_DISPONIBLE
    assert http.pedidos == []


def test_un_recordatorio_de_meta_se_reconstruye_con_su_estado(db, monkeypatch):
    monkeypatch.setenv("CRM_URL", "https://crm.test")
    bid = _lead_meta(db)
    _sql(db, "INSERT INTO meta_reminders (business_id, estado, numero, token, sent_at) VALUES (?,?,?,?,?)",
         (bid, "sin_contactar", 1, "tokviejo111", "2026-08-01 12:00:00"))
    _sql(db, "INSERT INTO meta_reminders (business_id, estado, numero, token, sent_at) VALUES (?,?,?,?,?)",
         (bid, "interesado", 1, "tokinteresado", "2026-09-02 14:00:00"))
    eid = _envio(db, tipo="recordatorio_meta", destinatario="lead@x.uy", business_id=bid, numero=1,
                 origen="historico", enviado_at="2026-09-02 14:00:00",
                 asunto="Recordatorio a lead de Meta (interesado) · contacto 1")
    with es.capturar_envio() as capturados:
        es.send_meta_lead_reminder("lead@x.uy", "Zsoul", "crear mi ecommerce",
                                   "https://crm.test/baja/tokinteresado", 1, "interesado")
    esperado = capturados[0]

    d = ec.contenido_de_envio(db, eid, "")
    assert d["fuente"] == "reconstruido" and d["avisos"] == [ec.AVISO_RECONSTRUIDO]
    assert d["html"] == ec.sanitizar_html(esperado["html"])
    assert d["asunto"] == esperado["subject"] == "Zsoul — cómo lo resolveríamos"
    assert d["remitente"] == "Scalerics <contacto@scalerics.com>"
    assert "tokinteresado" not in d["text"] and "/baja/(token)" in d["text"]


@pytest.mark.parametrize("caso,parte_del_motivo", [
    ("sin_negocio", "Falta el negocio"),
    ("negocio_borrado", "ya no está en el CRM"),
    ("sin_registro", "No está el registro del contacto 2"),
    ("sin_numero", "Falta el número de contacto"),
    ("sin_remitente", "Falta DISCOVERY_FROM_EMAIL"),
])
def test_si_falta_un_dato_para_reconstruir_lo_dice(db, monkeypatch, caso, parte_del_motivo):
    monkeypatch.setenv("DISCOVERY_FROM_EMAIL", REMITENTE_DISCOVERY)
    bid = _comercio(db)
    if caso != "sin_registro":
        _sql(db, "INSERT INTO discovery_reminders (business_id, numero, token, sent_at) VALUES (?,2,'t',?)",
             (bid, "2026-08-20 13:00:00"))
    datos = {"tipo": "discovery", "business_id": bid, "numero": 2}
    if caso == "sin_negocio":
        datos["business_id"] = None
    elif caso == "negocio_borrado":
        datos["business_id"] = 987654
    elif caso == "sin_numero":
        datos["numero"] = None
    elif caso == "sin_remitente":
        monkeypatch.delenv("DISCOVERY_FROM_EMAIL")
    d = ec.contenido_de_envio(db, _envio(db, **datos), "")
    assert d["fuente"] == "no_disponible" and d["html"] is None
    assert parte_del_motivo in d["motivo"], d["motivo"]
    assert ec.AVISO_RECONSTRUIDO not in d["avisos"]


@pytest.mark.parametrize("tipo", ["aviso_equipo", "tarea", "alerta", "linkedin", "reset_password", "otro"])
def test_los_otros_tipos_sin_contenido_no_estan_disponibles(db, tipo):
    d = ec.contenido_de_envio(db, _envio(db, tipo=tipo), "")
    assert (d["fuente"], d["motivo"], d["avisos"]) == ("no_disponible", ec.NO_DISPONIBLE, [])


# ── sanitización ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("crudo,prohibido", [
    ('<p>a</p><script>alert(1)</script><p>b</p>', "alert"),
    ('<SCRIPT type="text/javascript">robar()</SCRIPT>', "robar"),
    ('<p>hola</p><script>sin cerrar nunca', "sin cerrar"),
    ('<img src="x.png" onerror="robar()">', "onerror"),
    ('<body ONLOAD="robar()"><p>x</p></body>', "onload"),
    ('<div onMouseOver="robar()">x</div>', "robar"),
    ('<a href="javascript:alert(1)">x</a>', "javascript"),
    ('<a href="  JaVaScRiPt:alert(1)">x</a>', "alert"),
    ('<a href="java&#x09;script:alert(1)">x</a>', "alert"),
    ('<img src="vbscript:msgbox(1)">', "vbscript"),
    ('<iframe src="https://malo.test"></iframe>', "iframe"),
    ('<object data="x.swf"><embed src="x.swf"></object>', "swf"),
    ('<div style="background:url(javascript:alert(1))">x</div>', "javascript"),
    ('<style>p{background:url("javascript:alert(1)")}</style>', "javascript"),
    ('<form action="https://malo.test"><input name="clave"></form>', "malo.test"),
    ('<meta http-equiv="refresh" content="0;url=https://malo.test">', "malo.test"),
    ('<base href="https://malo.test/">', "malo.test"),
    ('<svg><script>alert(1)</script></svg>', "alert"),
    ('<p><!-- <script>alert(1)</script> --></p>', "alert"),
])
def test_sanitizar_quita_scripts_handlers_y_javascript(crudo, prohibido):
    limpio = ec.sanitizar_html(crudo)
    assert prohibido.lower() not in limpio.lower(), limpio


def test_sanitizar_conserva_lo_que_es_del_mail_y_apaga_los_links():
    crudo = ('<!DOCTYPE html><html><head><meta charset="utf-8"><style>p{color:#1c2b40}</style></head>'
             '<body style="margin:0"><p style="font-size:15px">Hola &amp; chau &#8212; <b>ya</b></p>'
             '<a href="https://scalerics.com" target="_blank" title="otro">scalerics.com</a>'
             '<a href="https://crm.test/baja/abcDEF_123-x">No quiero recibir más</a>'
             '<img src="https://assets/logo.png" alt="Scalerics" width="130">'
             '<img src="https://t.test/pixel.gif" width="1" height="1"></body></html>')
    limpio = ec.sanitizar_html(crudo)
    for parte in ("<!DOCTYPE html>", '<meta charset="utf-8">', "<style>p{color:#1c2b40}</style>",
                  'style="font-size:15px"', "Hola &amp; chau &#8212; <b>ya</b>", 'width="130"'):
        assert parte in limpio, parte
    assert '<a title="Link: https://scalerics.com">scalerics.com</a>' in limpio
    assert '<a title="Link: https://crm.test/baja/(token)">No quiero recibir más</a>' in limpio
    assert "href" not in limpio and "target" not in limpio and "pixel" not in limpio
    assert ec.sanitizar_html("") == "" and ec.sanitizar_html(None) == ""


# ── permisos ─────────────────────────────────────────────────────────────────

def test_sin_el_panel_da_403_y_con_el_panel_alcanza(app):
    db_path = app.config["DB_PATH"]
    eid = _envio(db_path, tipo="tarea")
    sin = _cli(app, "caller@scalerics.com", ["cola", "wa"])
    assert sin.get(f"/api/email-mkt/envios/{eid}/contenido").status_code == 403
    con = _cli(app, "sdr@scalerics.com", ["email_mkt"])
    r = con.get(f"/api/email-mkt/envios/{eid}/contenido")
    assert r.status_code == 200 and r.get_json()["fuente"] == "no_disponible"
    assert con.post("/api/email-marketing/actualizar-estados", json={}).status_code == 403, \
        "Actualizar estados sigue siendo solo admin"
    assert con.get("/api/email-mkt/envios/999999/contenido").status_code == 404
    assert app.test_client().get(f"/api/email-mkt/envios/{eid}/contenido").status_code in (401, 302)


def test_capturar_no_manda_ni_registra(db, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_falsa")
    monkeypatch.setattr(es, "_DB_REGISTRO", db)
    with es.capturar_envio() as capturados:
        assert es.send_reset_email("x@x.uy", "https://crm/reset/1") is True
    assert len(capturados) == 1 and capturados[0]["subject"] == "Resetear contraseña — Scalerics CRM"
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM emails_enviados").fetchone()[0] == 0
    conn.close()


# ── pantalla ─────────────────────────────────────────────────────────────────

def test_el_modal_trae_el_iframe_con_sandbox_vacio_y_se_carga_por_srcdoc():
    iframe = re.search(r"<iframe[^>]*>", PANEL).group(0)
    assert 'id="em-mail-iframe"' in iframe and 'sandbox=""' in iframe
    assert "allow-" not in PANEL + JS and "srcdoc" not in iframe
    assert "iframe.srcdoc = d.html" in JS and "iframe.setAttribute('sandbox', '')" in JS
    assert "innerHTML = d.html" not in JS
    assert 'onclick="emVerMail(' in JS and ">Ver mail</button>" in JS
    assert 'id="em-mail-tab-texto"' in PANEL and ">Texto</button>" in PANEL
    assert re.search(r"\.em-mail-iframe\{[^}]*background:white", SRC)
    assert re.search(r"@media\(max-width:560px\)\{[^}]*\.em-mail-modal", SRC)


def test_la_pagina_abre_con_el_modal(jefe):
    r = jefe.get("/")
    assert r.status_code == 200 and b'id="em-mail-modal"' in r.data


_ARNES = r"""
const _els = {};
function _el(id) {
  if (!_els[id]) {
    const clases = new Set();
    const el = { id, innerHTML: '', textContent: '', value: '', srcdoc: '', disabled: false, style: {},
                 dataset: {}, _clases: clases, _attrs: {},
                 classList: { add(c){ clases.add(c); }, remove(c){ clases.delete(c); },
                              toggle(){}, contains: c => clases.has(c) },
                 querySelectorAll: () => [], querySelector: () => null, closest: () => null,
                 getAttribute: n => null, focus(){}, addEventListener(){}, appendChild(){}, remove(){} };
    el.setAttribute = (n, v) => { el._attrs[n] = String(v); };
    _els[id] = el;
  }
  return _els[id];
}
globalThis.document = {
  getElementById: _el, querySelectorAll: () => [], querySelector: () => null,
  body: { classList: { contains: () => false, add(){}, remove(){}, toggle(){} } },
  createElement: () => _el('tmp'), addEventListener(){},
};
globalThis.window = globalThis;
globalThis.localStorage = { getItem: () => null, setItem(){}, removeItem(){} };
globalThis.lucide = { createIcons(){} };
globalThis.alert = () => {};
globalThis.confirm = () => true;
globalThis.setInterval = () => 0;
const _RESPUESTAS = __RESPUESTAS__;
const _pedidos = [];
globalThis.fetch = (url, opciones) => {
  _pedidos.push([String(url), (opciones && opciones.method) || 'GET']);
  const r = _RESPUESTAS[String(url)];
  if (r === 'falla') return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) });
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(r === undefined ? {} : r) });
};
"""


@sin_node
def test_el_modal_se_pinta_con_iframe_sandbox_y_srcdoc(app, jefe, monkeypatch, tmp_path):
    db_path = app.config["DB_PATH"]
    ahora = em.ahora_utc()
    con_html = _envio(db_path, resend_id="re_modal_1", tipo="aviso_equipo", destinatario="juan@scalerics.com",
                      asunto="Asunto guardado", enviado_at=ahora)
    sin_contenido = _envio(db_path, tipo="alerta", destinatario="juan@scalerics.com", asunto="Alerta",
                           enviado_at=ahora)
    roto = _envio(db_path, tipo="tarea", asunto="Tarea", enviado_at=ahora)
    monkeypatch.setenv("RESEND_API_KEY", "re_test_falsa")
    monkeypatch.setattr(rutas, "_cliente_http", lambda: _HttpFalso({"re_modal_1": (200, DE_RESEND)}))
    monkeypatch.setattr(ec, "_PAUSA_MINIMA", 0)
    lista = jefe.get("/api/email-marketing").get_json()
    d1 = jefe.get(f"/api/email-mkt/envios/{con_html}/contenido").get_json()
    d2 = jefe.get(f"/api/email-mkt/envios/{sin_contenido}/contenido").get_json()

    prueba = """
(async () => {
  const s = {};
  await loadEmailMkt();
  s.tabla = _el('em-tabla').innerHTML;
  const abriendo = emVerMail(__ID1__);
  s.abiertoAlToque = _el('em-mail-modal')._clases.has('open');
  s.cargando = _el('em-mail-vacio').textContent;
  s.asuntoMientras = _el('em-mail-asunto').textContent;
  await abriendo;
  const iframe = _el('em-mail-iframe');
  s.srcdoc = iframe.srcdoc;
  s.sandbox = iframe._attrs.sandbox;
  s.iframeVisible = !iframe._clases.has('em-oculto');
  s.textoOculto = _el('em-mail-texto')._clases.has('em-oculto');
  s.texto = _el('em-mail-texto').textContent;
  s.datos = _el('em-mail-datos').innerHTML;
  s.asunto = _el('em-mail-asunto').textContent;
  s.avisoOculto = _el('em-mail-aviso')._clases.has('em-oculto');
  s.tabsVisibles = !_el('em-mail-tabs')._clases.has('em-oculto');
  s.vacioOculto = _el('em-mail-vacio')._clases.has('em-oculto');
  s.innerDelIframe = iframe.innerHTML;
  emMailPestana('texto');
  s.pestanaTexto = [iframe._clases.has('em-oculto'), !_el('em-mail-texto')._clases.has('em-oculto'),
                    _el('em-mail-tab-texto')._clases.has('em-activo'), _el('em-mail-tab-html')._clases.has('em-activo')];
  emMailPestana('html');
  s.pestanaHtml = !iframe._clases.has('em-oculto');

  await emVerMail(__ID2__);
  s.noDisponible = _el('em-mail-vacio').textContent;
  s.noDisponibleVisible = !_el('em-mail-vacio')._clases.has('em-oculto');
  s.srcdocNoDisponible = iframe.srcdoc;
  s.tabsOcultas = _el('em-mail-tabs')._clases.has('em-oculto');

  await emVerMail(__ID3__);
  s.falla = _el('em-mail-vacio').textContent;

  emCerrarMail();
  s.cerrado = !_el('em-mail-modal')._clases.has('open');
  s.srcdocCerrado = iframe.srcdoc;
  s.pedidos = _pedidos.map(p => p[0]);
  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""".replace("__ID1__", str(con_html)).replace("__ID2__", str(sin_contenido)).replace("__ID3__", str(roto))

    respuestas = {"/api/email-marketing": lista,
                  f"/api/email-mkt/envios/{con_html}/contenido": d1,
                  f"/api/email-mkt/envios/{sin_contenido}/contenido": d2,
                  f"/api/email-mkt/envios/{roto}/contenido": "falla"}
    bloques = re.findall(r"<script>(.*?)</script>", HTML, re.S)
    archivo = tmp_path / "ver_mail.js"
    archivo.write_text(_ARNES.replace("__RESPUESTAS__", json.dumps(respuestas, ensure_ascii=False))
                       + "\n".join(bloques) + "\n" + prueba, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, (r.stderr or "")[:2000]
    s = json.loads(r.stdout.strip().splitlines()[-1])

    assert f'onclick="emVerMail({con_html})">Ver mail</button>' in s["tabla"]
    assert s["abiertoAlToque"] is True and s["cargando"] == "Cargando el mail…"
    assert s["asuntoMientras"] == "Asunto guardado", "mientras carga, lo que ya estaba en la tabla"
    assert s["srcdoc"] == d1["html"] and "<script" not in s["srcdoc"]
    assert s["sandbox"] == "", "sandbox vacío: sin allow-scripts ni allow-same-origin"
    assert s["innerDelIframe"] == "", "el HTML nunca va al DOM del CRM"
    assert s["iframeVisible"] and s["textoOculto"] and s["tabsVisibles"] and s["vacioOculto"]
    assert s["texto"] == "Hola Ana\nBaja: https://crm.test/baja/(token)"
    assert s["asunto"] == "Asunto de Resend" and s["avisoOculto"] is True
    assert "Scalerics CRM &lt;crm@noreply.scalerics.com&gt;" in s["datos"]
    assert "(Montevideo)" in s["datos"] and "Avisos al equipo" in s["datos"]
    assert s["pestanaTexto"] == [True, True, True, False] and s["pestanaHtml"] is True

    assert s["noDisponible"] == "El contenido de este mail no está disponible."
    assert s["noDisponibleVisible"] and s["srcdocNoDisponible"] == "" and s["tabsOcultas"]
    assert s["falla"].startswith("No se pudo cargar el contenido de este mail")
    assert s["cerrado"] and s["srcdocCerrado"] == ""
    assert f"/api/email-mkt/envios/{con_html}/contenido" in s["pedidos"]
