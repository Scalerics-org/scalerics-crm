"""Email marketing: lo que sale por Resend y lo que Resend cuenta después.

Pedido de Juan (15/9): "una sección en captación que se llame email marketing
que agarre las cosas de resend para ver lo que vamos mandando".

Ningún test acá habla con Resend: el envío usa un `requests.post` falso y
"Actualizar estados" un cliente HTTP falso.
"""

import base64
import hashlib
import hmac
import json
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import dashboard
import database
import routes.email_marketing as rutas
import services.email_marketing as em
import services.email_service as es
from database import create_user, init_db, insert_business

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
CSS = _entre(SRC, "/* ── Email marketing", "/* ── LinkedIn")
FUENTES = {"panel": PANEL, "js": JS, "css": CSS}

PROHIBIDAS = ("monto", "usd", "$", "precio", "pago", "factur", "sueldo")


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "em.db")
    init_db(ruta)
    return ruta


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    ruta = str(tmp_path / "em-app.db")
    init_db(ruta)
    a = dashboard.create_app(ruta)
    a.config["TESTING"] = True
    yield a
    es.configurar_registro(None)


def _rol(db_path, nombre, paneles):
    conn = sqlite3.connect(db_path)
    try:
        rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)",
                           (nombre, json.dumps(paneles))).lastrowid
        conn.commit()
        return rid
    finally:
        conn.close()


def _cli(app, email, paneles=None):
    db_path = app.config["DB_PATH"]
    uid = create_user(db_path, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    if paneles is not None:
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE users SET role_id=? WHERE id=?", (_rol(db_path, "rol-" + email, paneles), uid))
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


def _fila(db_path, **datos):
    """Una fila de emails_enviados escrita a mano, con la fecha en UTC."""
    base = {"resend_id": None, "tipo": "discovery", "destinatario": "a@x.uy", "asunto": "Hola",
            "enviado_at": "2026-09-10 15:00:00", "origen": "crm", "estado_envio": "enviado"}
    base.update(datos)
    eventos = {k: base.pop(k) for k in list(base) if k.endswith("_at") and k != "enviado_at"}
    base.update(eventos)
    conn = sqlite3.connect(db_path)
    try:
        cols = ", ".join(base)
        marcas = ", ".join("?" for _ in base)
        conn.execute(f"INSERT INTO emails_enviados ({cols}) VALUES ({marcas})", list(base.values()))
        conn.execute(f"UPDATE emails_enviados SET estado = {em._ESTADO_SQL}")
        conn.commit()
    finally:
        conn.close()


def _filas(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM emails_enviados ORDER BY id")]
    finally:
        conn.close()


class _RespuestaResend:
    def __init__(self, cuerpo=None, status=200):
        self._cuerpo = cuerpo if cuerpo is not None else {"id": "re_abc123456"}
        self.status_code = status

    def raise_for_status(self):
        pass

    def json(self):
        return self._cuerpo


@pytest.fixture
def resend_simulado(db, monkeypatch):
    """Resend falso que acepta todo y devuelve ids distintos."""
    monkeypatch.setenv("RESEND_API_KEY", "re_test_falsa")
    monkeypatch.setattr(es, "_DB_REGISTRO", db)
    pedidos = []

    def post(url, headers=None, json=None, timeout=None):
        pedidos.append(json)
        return _RespuestaResend({"id": f"re_id_{len(pedidos):04d}"})

    monkeypatch.setattr(es.requests, "post", post)
    return pedidos


# ── registro de envíos ───────────────────────────────────────────────────────

def test_cada_envio_queda_registrado_con_su_id_de_resend(db, resend_simulado, monkeypatch):
    monkeypatch.setenv("DISCOVERY_FROM_EMAIL", "Scalerics <hola@envios.scalerics.com>")
    bid = insert_business(db, {"name": "Peluquería Ana", "phone": "+59899111222", "email": "ana@x.uy"})
    with es.contexto_envio(business_id=bid, numero=1):
        assert es.send_discovery_email("ana@x.uy", "Peluquería Ana", "peluqueria",
                                       "https://crm/baja/t", 1) == "ok"
    assert es.send_reset_email("juan@scalerics.com", "https://crm/reset/secreto") is True

    filas = _filas(db)
    assert len(filas) == 2 == len(resend_simulado)
    disc, reset = filas
    assert disc["resend_id"] == "re_id_0001" and disc["tipo"] == "discovery"
    assert disc["business_id"] == bid and disc["numero"] == 1
    assert disc["destinatario"] == "ana@x.uy" and disc["asunto"] == "Una idea para Peluquería Ana"
    assert disc["estado"] == "enviado" and disc["origen"] == "crm"
    assert 0 < len(disc["extracto"]) <= 140
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", disc["enviado_at"])

    assert reset["tipo"] == "reset_password" and reset["business_id"] is None
    assert reset["extracto"] is None, "el link de reseteo no se guarda en ningún lado"
    assert "secreto" not in json.dumps(filas)
    columnas = set(disc)
    assert not columnas & {"html", "cuerpo", "text", "body"}, "el cuerpo del mail no se guarda"


def test_el_contexto_no_se_filtra_al_envio_siguiente(db, resend_simulado):
    with es.contexto_envio(business_id=77, numero=2):
        es.send_backup_alert("juan@scalerics.com", "algo")
    es.send_backup_alert("juan@scalerics.com", "otra cosa")
    a, b = _filas(db)
    assert (a["tipo"], a["business_id"], a["numero"]) == ("alerta", 77, 2)
    assert (b["tipo"], b["business_id"], b["numero"]) == ("alerta", None, None)


def test_el_aviso_de_lead_de_meta_queda_linkeado_al_lead(db, resend_simulado):
    es.send_new_meta_lead_notification("juan@scalerics.com", "Lead", "099", "Camp", "Mvd", 42)
    assert _filas(db)[0]["business_id"] == 42
    assert _filas(db)[0]["tipo"] == "aviso_equipo"


def test_si_registrar_falla_el_mail_igual_sale(db, resend_simulado, monkeypatch, caplog):
    def roto(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(em, "registrar_envio", roto)
    assert es._send_estado("destino@x.uy", "Asunto", "<p>hola</p>") == "ok"
    assert len(resend_simulado) == 1
    assert "destino@x.uy" not in caplog.text, "ninguna dirección en el log nuevo"


def test_si_la_base_no_existe_el_mail_igual_sale(tmp_path, resend_simulado, monkeypatch):
    monkeypatch.setattr(es, "_DB_REGISTRO", str(tmp_path / "no" / "existe.db"))
    assert es.send_task_assignment_email("x@x.uy", "X", "T", "", None, None, None, "Juan") is True


def test_sin_clave_de_resend_no_se_registra_nada(db, monkeypatch):
    monkeypatch.setattr(es, "_DB_REGISTRO", db)
    assert es._send_estado("a@x.uy", "S", "<p>h</p>") == "ok"
    assert _filas(db) == []


def test_un_timeout_queda_como_sin_confirmar(db, resend_simulado, monkeypatch):
    def colgado(*a, **k):
        raise es.requests.exceptions.ReadTimeout("lento")

    monkeypatch.setattr(es.requests, "post", colgado)
    assert es._send_estado("a@x.uy", "S", "<p>h</p>") == "desconocido"
    fila = _filas(db)[0]
    assert fila["estado"] == "incierto" and fila["resend_id"] is None


def test_un_rechazo_de_resend_no_se_registra(db, resend_simulado, monkeypatch):
    class _Rechazo(_RespuestaResend):
        def raise_for_status(self):
            raise es.requests.exceptions.HTTPError("422")

    monkeypatch.setattr(es.requests, "post", lambda *a, **k: _Rechazo())
    assert es._send_estado("a@x.uy", "S", "<p>h</p>") == "fallo"
    assert _filas(db) == []


def test_las_campanas_pasan_el_negocio_al_registro():
    """El tipo lo pone cada función de envío; el negocio y el contacto, la tanda."""
    for modulo in ("discovery_emails", "meta_reminders"):
        fuente = (RAIZ / "services" / f"{modulo}.py").read_text(encoding="utf-8")
        assert "with contexto_envio(business_id=" in fuente, modulo
    fuente = (RAIZ / "services" / "email_service.py").read_text(encoding="utf-8")
    funciones = re.findall(r"\ndef (send_\w+)\(", fuente)
    assert len(funciones) >= 13
    for nombre in funciones:
        assert re.search(r'@_tipo_envio\("\w+"\)\ndef ' + nombre + r"\(", fuente), nombre


# ── webhook ──────────────────────────────────────────────────────────────────

_SECRETO = "whsec_" + base64.b64encode(b"un secreto de prueba de 32 bytes!").decode()


def _firmar(cuerpo: bytes):
    ts = str(int(time.time()))
    secreto = base64.b64decode(_SECRETO.split("_", 1)[1])
    firma = hmac.new(secreto, f"msg_1.{ts}.".encode() + cuerpo, hashlib.sha256).digest()
    return {"svix-id": "msg_1", "svix-timestamp": ts,
            "svix-signature": "v1," + base64.b64encode(firma).decode(), "Content-Type": "application/json"}


def _evento(cliente, tipo, email_id, cuando="2026-09-10T18:05:00.000Z", **extra):
    datos = {"email_id": email_id, "to": ["lead@x.uy"], "subject": "Una idea",
             "created_at": "2026-09-10T18:00:00.000Z", **extra}
    cuerpo = json.dumps({"type": tipo, "created_at": cuando, "data": datos}).encode()
    return cliente.post("/api/resend/webhook", data=cuerpo, headers=_firmar(cuerpo))


@pytest.fixture
def webhook(app, monkeypatch):
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", _SECRETO)
    return app.test_client()


def test_el_webhook_actualiza_el_estado_aunque_lleguen_desordenados(app, webhook):
    db_path = app.config["DB_PATH"]
    _fila(db_path, resend_id="re_1", destinatario="lead@x.uy")

    assert _evento(webhook, "email.opened", "re_1", "2026-09-10T18:10:00Z").status_code == 200
    fila = _filas(db_path)[0]
    assert fila["estado"] == "abierto"
    assert fila["abierto_at"] == "2026-09-10 18:10:00"
    assert fila["entregado_at"] == "2026-09-10 18:10:00", "abrir implica que se entregó"

    _evento(webhook, "email.delivered", "re_1", "2026-09-10T18:01:00Z")
    fila = _filas(db_path)[0]
    assert fila["estado"] == "abierto", "un evento viejo no hace retroceder el estado"

    _evento(webhook, "email.clicked", "re_1", click={"link": "https://calendly.com"})
    assert _filas(db_path)[0]["estado"] == "clic"
    _evento(webhook, "email.complained", "re_1")
    assert _filas(db_path)[0]["estado"] == "spam", "la queja gana"


def test_un_rebote_marca_la_fila_y_sigue_vedando(app, webhook):
    from services.mails_vedados import esta_vedado

    db_path = app.config["DB_PATH"]
    _fila(db_path, resend_id="re_2", destinatario="lead@x.uy")
    r = _evento(webhook, "email.bounced", "re_2", bounce={"type": "Permanent"})
    assert r.status_code == 200 and r.get_json()["vedados"] == 1
    assert _filas(db_path)[0]["estado"] == "rebotado"
    assert esta_vedado(db_path, "lead@x.uy")


def test_un_evento_de_un_mail_que_el_crm_no_registro_crea_la_fila(app, webhook):
    db_path = app.config["DB_PATH"]
    _evento(webhook, "email.delivered", "re_de_afuera")
    fila = _filas(db_path)[0]
    assert (fila["resend_id"], fila["tipo"], fila["origen"]) == ("re_de_afuera", "otro", "webhook")
    assert fila["destinatario"] == "lead@x.uy" and fila["asunto"] == "Una idea"
    assert fila["enviado_at"] == "2026-09-10 18:00:00" and fila["estado"] == "entregado"

    # Si después se registra el envío (el webhook se adelantó), se completa la misma fila.
    em.registrar_envio(db_path, resend_id="re_de_afuera", tipo="discovery", destinatario="lead@x.uy",
                       asunto="Una idea", business_id=5, numero=1)
    filas = _filas(db_path)
    assert len(filas) == 1
    assert (filas[0]["tipo"], filas[0]["business_id"], filas[0]["estado"]) == ("discovery", 5, "entregado")


def test_un_evento_sin_email_id_o_desconocido_no_escribe(app, webhook):
    db_path = app.config["DB_PATH"]
    _evento(webhook, "email.delivered", "")
    _evento(webhook, "contact.created", "re_x")
    cuerpo = json.dumps({"type": "email.delivered", "data": ["no", "es", "dict"]}).encode()
    assert webhook.post("/api/resend/webhook", data=cuerpo, headers=_firmar(cuerpo)).status_code == 200
    assert _filas(db_path) == []


def test_si_registrar_el_evento_falla_el_webhook_igual_veda(app, webhook, monkeypatch):
    import routes.resend_webhook as rw
    from services.mails_vedados import esta_vedado

    def roto(*a, **k):
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(rw, "registrar_evento", roto)
    r = _evento(webhook, "email.complained", "re_3")
    assert r.status_code == 200
    assert esta_vedado(app.config["DB_PATH"], "lead@x.uy")


# ── contadores, tasas y período ──────────────────────────────────────────────

def test_contadores_y_tasas_del_mes_en_hora_de_montevideo(db):
    # 02:30 UTC del 1/9 son las 23:30 del 31/8 en Montevideo: es de agosto.
    _fila(db, resend_id="r0", enviado_at="2026-09-01 02:30:00", entregado_at="x")
    # 02:00 UTC del 1/10 son las 23:00 del 30/9: es de septiembre.
    _fila(db, resend_id="r1", enviado_at="2026-10-01 02:00:00", entregado_at="x", abierto_at="x")
    _fila(db, resend_id="r2", enviado_at="2026-09-01 03:00:00", entregado_at="x", abierto_at="x", clic_at="x")
    _fila(db, resend_id="r3", enviado_at="2026-09-15 12:00:00", rebotado_at="x")
    _fila(db, resend_id="r4", enviado_at="2026-09-15 13:00:00", entregado_at="x", spam_at="x")
    _fila(db, resend_id="r5", enviado_at="2026-09-16 13:00:00")

    r = em.resumen(db, "2026-09")
    assert r["contadores"] == {"enviados": 5, "entregados": 3, "abiertos": 2, "clics": 1,
                               "rebotados": 1, "spam": 1, "sin_eventos": 1}
    assert r["tasas"] == {"entregados": 60.0, "abiertos": 40.0, "clics": 20.0,
                          "rebotados": 20.0, "spam": 20.0}

    por_dia = {p["dia"]: p["n"] for p in r["por_dia"]}
    assert len(r["por_dia"]) == 30 and por_dia["2026-09-01"] == 1 and por_dia["2026-09-30"] == 1
    assert por_dia["2026-09-15"] == 2 and sum(por_dia.values()) == 5

    fechas = [e["fecha_local"] for e in r["envios"]]
    assert fechas[0] == "30/09/2026 23:00" and fechas[-1] == "01/09/2026 00:00"

    agosto = em.resumen(db, "2026-08")
    assert agosto["contadores"]["enviados"] == 1 and agosto["envios"][0]["fecha_local"] == "31/08/2026 23:30"
    vacio = em.resumen(db, "2026-07")
    assert vacio["contadores"]["enviados"] == 0 and vacio["tasas"]["entregados"] is None


def test_el_rango_del_mes_es_el_de_montevideo():
    assert em.rango_mes("2026-09") == ("2026-09-01 03:00:00", "2026-10-01 03:00:00")
    assert em.rango_mes("2026-12") == ("2026-12-01 03:00:00", "2027-01-01 03:00:00")
    assert em.parse_mes("2026-13") is None and em.parse_mes("sept") is None


# ── API: filtros, búsqueda, paginado ─────────────────────────────────────────

def _cargar_30(db_path):
    for i in range(30):
        _fila(db_path, resend_id=f"re_{i:03d}", tipo="discovery" if i < 20 else "recordatorio_meta",
              destinatario=f"lead{i}@Comercio.uy", asunto=f"Asunto {i}" + (" 100%_off" if i == 7 else ""),
              enviado_at=f"2026-09-{(i % 28) + 1:02d} 15:{i:02d}:00",
              entregado_at="x" if i % 2 else None)


def test_filtro_por_tipo_busqueda_y_paginado(app, jefe):
    db_path = app.config["DB_PATH"]
    _cargar_30(db_path)

    r = jefe.get("/api/email-marketing?mes=2026-09").get_json()
    assert r["encontrados"] == 30 and r["paginas"] == 2 and len(r["envios"]) == 25
    assert [t["clave"] for t in r["tipos"]] == ["discovery", "recordatorio_meta"]
    assert r["tipos"][0]["etiqueta"] == "Discovery en frío"

    p2 = jefe.get("/api/email-marketing?mes=2026-09&pagina=2").get_json()
    assert p2["pagina"] == 2 and len(p2["envios"]) == 5
    assert not {e["id"] for e in p2["envios"]} & {e["id"] for e in r["envios"]}
    assert jefe.get("/api/email-marketing?mes=2026-09&pagina=99").get_json()["pagina"] == 2

    meta = jefe.get("/api/email-marketing?mes=2026-09&tipo=recordatorio_meta").get_json()
    assert meta["contadores"]["enviados"] == 10 and meta["encontrados"] == 10
    assert all(e["tipo"] == "recordatorio_meta" for e in meta["envios"])

    q = jefe.get("/api/email-marketing?mes=2026-09&q=LEAD2%40comercio").get_json()
    assert {e["destinatario"] for e in q["envios"]} == {"lead2@Comercio.uy"}
    assert q["contadores"]["enviados"] == 30, "la búsqueda filtra la tabla, no los contadores"
    assert jefe.get("/api/email-marketing?mes=2026-09&q=asunto%2012").get_json()["encontrados"] == 1
    assert jefe.get("/api/email-marketing?mes=2026-09&q=100%25_").get_json()["encontrados"] == 1
    assert jefe.get("/api/email-marketing?mes=2026-09&q=%25").get_json()["encontrados"] == 1, \
        "un % se busca literal, no como comodín"

    entregados = jefe.get("/api/email-marketing?mes=2026-09&estado=entregado").get_json()
    assert entregados["encontrados"] == 15


@pytest.mark.parametrize("consulta", ["mes=2026-13", "mes=ayer", "tipo=newsletter", "estado=leido",
                                      "pagina=dos"])
def test_parametros_invalidos_dan_400(jefe, consulta):
    assert jefe.get("/api/email-marketing?" + consulta).status_code == 400


def test_sin_mes_es_el_mes_actual_y_trae_la_ficha_del_lead(app, jefe):
    db_path = app.config["DB_PATH"]
    bid = insert_business(db_path, {"name": "Ferretería Sur", "phone": "+59899000111"})
    em.registrar_envio(db_path, resend_id="re_hoy", tipo="discovery", destinatario="f@x.uy",
                       asunto="Hola", business_id=bid)
    em.registrar_envio(db_path, resend_id="re_huerfano", tipo="discovery", destinatario="g@x.uy",
                       asunto="Hola", business_id=999999)
    r = jefe.get("/api/email-marketing").get_json()
    assert r["mes"] == r["mes_actual"] == em.mes_actual()
    por_dest = {e["destinatario"]: e for e in r["envios"]}
    assert por_dest["f@x.uy"]["business_id"] == bid and por_dest["f@x.uy"]["negocio"] == "Ferretería Sur"
    assert por_dest["g@x.uy"]["business_id"] is None, "sin ficha no hay link muerto"
    assert r["es_admin"] is True and r["hay_api_key"] is False


# ── permisos ─────────────────────────────────────────────────────────────────

def test_sin_el_panel_da_403(app):
    c = _cli(app, "caller@scalerics.com", ["cola", "wa"])
    assert c.get("/api/email-marketing").status_code == 403
    assert c.post("/api/email-marketing/actualizar-estados", json={}).status_code == 403


def test_con_el_panel_se_ve_pero_actualizar_estados_es_solo_admin(app, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_falsa")
    c = _cli(app, "sdr@scalerics.com", ["email_mkt"])
    r = c.get("/api/email-marketing")
    assert r.status_code == 200 and r.get_json()["es_admin"] is False
    assert c.post("/api/email-marketing/actualizar-estados", json={}).status_code == 403


def test_sin_sesion_no_entra(app):
    assert app.test_client().get("/api/email-marketing").status_code in (401, 302)


# ── reparto a roles, una sola vez ────────────────────────────────────────────

def test_el_panel_se_reparte_una_vez_a_quien_tiene_outbound_o_inteligencia(db):
    conn = sqlite3.connect(db)
    try:
        # Los roles sembrados: Admin y Caller tienen cola, Ventas tiene metrics.
        # El Contador no tiene ninguno de los dos y no lo recibe.
        acceso = {n: json.loads(p) for n, p in conn.execute("SELECT name, panel_access FROM roles")}
        for nombre, paneles in acceso.items():
            # El vendedor de Fidelidad tiene Outbound e Inteligencia, pero es de
            # afuera: ningun reparto le suma paneles (services/fidelidad.py).
            espera = ("cola" in paneles or "metrics" in paneles) and nombre != "Vendedor Fidelidad"
            assert ("email_mkt" in paneles) is espera, (nombre, paneles)
        assert "email_mkt" in acceso["Admin"] and "email_mkt" in acceso["Ventas"]
        assert "email_mkt" not in acceso["Contador"]

        # Una base de producción, donde el panel todavía no se repartió nunca.
        conn.execute("DELETE FROM panel_grants_aplicados WHERE panel='email_mkt'")
        for nombre, paneles in (("Outbound", ["cola"]), ("Intel", ["metrics", "cal"]),
                                ("Solo WA", ["wa"]), ("Ya lo tiene", ["email_mkt", "cola"])):
            conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)", (nombre, json.dumps(paneles)))
        conn.commit()
        assert database._grant_panel_to_existing_roles(conn, "email_mkt", si_tiene=("cola", "metrics")) == 2
        acceso = {n: json.loads(p) for n, p in conn.execute("SELECT name, panel_access FROM roles")}
        assert acceso["Outbound"] == ["cola", "email_mkt"]
        assert acceso["Intel"] == ["metrics", "cal", "email_mkt"]
        assert acceso["Solo WA"] == ["wa"]
        assert acceso["Ya lo tiene"] == ["email_mkt", "cola"], "no se duplica"

        conn.execute("UPDATE roles SET panel_access=? WHERE name='Outbound'", (json.dumps(["cola"]),))
        conn.commit()
    finally:
        conn.close()
    init_db(db)
    conn = sqlite3.connect(db)
    outbound = json.loads(conn.execute("SELECT panel_access FROM roles WHERE name='Outbound'").fetchone()[0])
    conn.close()
    assert outbound == ["cola"], "lo que Juan saca no vuelve con un deploy"
    fuente_db = (RAIZ / "database.py").read_text(encoding="utf-8")
    assert '_grant_panel_to_existing_roles(conn, "email_mkt", si_tiene=("cola", "metrics"))' in fuente_db


# ── historico ────────────────────────────────────────────────────────────────

def test_los_envios_historicos_de_las_campanas_se_ven_desde_el_primer_dia(tmp_path):
    ruta = str(tmp_path / "hist.db")
    init_db(ruta)
    bid = insert_business(ruta, {"name": "Inmo Uno", "phone": "+59899222333", "email": "inmo@x.uy"})
    conn = sqlite3.connect(ruta)
    conn.execute("DROP TABLE emails_enviados")  # una base de antes de esta rama
    conn.execute("INSERT INTO discovery_reminders (business_id, numero, token, sent_at) VALUES (?,1,'t1',?)",
                 (bid, "2026-08-20 13:00:00"))
    conn.execute("INSERT INTO meta_reminders (business_id, estado, numero, token, sent_at) VALUES (?,?,2,'t2',?)",
                 (bid, "interesado", "2026-08-21 14:00:00"))
    conn.commit()
    conn.close()

    init_db(ruta)
    init_db(ruta)  # un segundo arranque no duplica
    filas = _filas(ruta)
    assert len(filas) == 2
    por_tipo = {f["tipo"]: f for f in filas}
    assert por_tipo["discovery"]["destinatario"] == "inmo@x.uy"
    assert por_tipo["discovery"]["business_id"] == bid and por_tipo["discovery"]["origen"] == "historico"
    assert por_tipo["recordatorio_meta"]["asunto"] == "Recordatorio a lead de Meta (interesado) · contacto 2"
    agosto = em.resumen(ruta, "2026-08")
    assert agosto["contadores"]["enviados"] == 2 and agosto["contadores"]["sin_eventos"] == 2


# ── Actualizar estados ───────────────────────────────────────────────────────

class _HttpFalso:
    def __init__(self, respuestas):
        self.respuestas = respuestas
        self.pedidos = []

    def get(self, url, headers=None, timeout=None):
        self.pedidos.append((url, headers, timeout))
        rid = url.rsplit("/", 1)[-1]
        r = self.respuestas.get(rid, (404, {}))
        return _RespuestaResend(r[1], r[0])


def test_actualizar_estados_consulta_solo_los_sin_evento_y_respeta_el_ritmo(db):
    ahora = em.ahora_utc()
    _fila(db, resend_id="re_sin_evento_1", enviado_at=ahora)
    _fila(db, resend_id="re_sin_evento_2", enviado_at=ahora)
    _fila(db, resend_id="re_sin_evento_3", enviado_at=ahora)
    _fila(db, resend_id="re_ya_entregado", enviado_at=ahora, entregado_at=ahora)
    _fila(db, resend_id=None, enviado_at=ahora)
    _fila(db, resend_id="re_de_hace_meses", enviado_at="2025-01-01 10:00:00")
    http = _HttpFalso({
        "re_sin_evento_1": (200, {"last_event": "opened", "html": "<p>cuerpo secreto</p>"}),
        "re_sin_evento_2": (200, {"last_event": "sent"}),
        "re_sin_evento_3": (200, {"last_event": "bounced"}),
    })
    pausas = []
    res = em.actualizar_estados(db, "re_clave", limite=10, http=http, dormir=pausas.append)

    consultados = sorted(u.rsplit("/", 1)[-1] for u, _, _ in http.pedidos)
    assert consultados == ["re_sin_evento_1", "re_sin_evento_2", "re_sin_evento_3"]
    assert all(h == {"Authorization": "Bearer re_clave"} and t for _, h, t in http.pedidos)
    assert len(pausas) == 2 and all(p >= 0.1 for p in pausas), "pausa entre pedidos, no antes del primero"
    assert res["consultados"] == 3 and res["actualizados"] == 2 and res["pendientes"] == 3
    estados = {f["resend_id"]: f["estado"] for f in _filas(db)}
    assert estados["re_sin_evento_1"] == "abierto" and estados["re_sin_evento_3"] == "rebotado"
    assert estados["re_sin_evento_2"] == "enviado"
    assert "cuerpo secreto" not in json.dumps(_filas(db))


def test_un_429_corta_la_tanda(db):
    ahora = em.ahora_utc()
    for i in range(3):
        _fila(db, resend_id=f"re_limite_{i}", enviado_at=ahora)
    http = _HttpFalso({f"re_limite_{i}": (429, {}) for i in range(3)})
    res = em.actualizar_estados(db, "k", http=http, dormir=lambda s: None)
    assert res["cortado_por_limite"] is True and len(http.pedidos) == 1


def test_sin_clave_el_servicio_no_consulta():
    with pytest.raises(ValueError):
        em.actualizar_estados("x.db", "", http=_HttpFalso({}))


def test_el_boton_sin_api_key_da_400_y_con_clave_usa_el_cliente(app, jefe, monkeypatch):
    r = jefe.post("/api/email-marketing/actualizar-estados", json={})
    assert r.status_code == 400 and "RESEND_API_KEY" in r.get_json()["error"]

    db_path = app.config["DB_PATH"]
    _fila(db_path, resend_id="re_boton_1", enviado_at=em.ahora_utc())
    http = _HttpFalso({"re_boton_1": (200, {"last_event": "delivered"})})
    monkeypatch.setenv("RESEND_API_KEY", "re_test_falsa")
    monkeypatch.setattr(rutas, "_cliente_http", lambda: http)
    monkeypatch.setattr(em, "_PAUSA_SEGUNDOS", 0)
    r = jefe.post("/api/email-marketing/actualizar-estados", json={"limite": 500})
    assert r.status_code == 200 and r.get_json()["actualizados"] == 1
    assert _filas(db_path)[0]["estado"] == "entregado"


# ── registro en el menú y el HTML ────────────────────────────────────────────

def test_esta_registrado_en_todos_lados():
    menu = HTML[HTML.index('<div class="nav-scroll">'):HTML.index('<div class="sidebar-bottom">')]
    # Juan (15/9): Email marketing pasa a MARKETING, abajo de Inteligencia marketing.
    marketing = menu[menu.index('nav-section-label">MARKETING'):menu.index('nav-section-label">FINANZAS')]
    assert re.findall(r'id="nav-(\w+)"', marketing) == ["meta", "marketing", "email_mkt", "linkedin", "instagram", "sombra"]
    assert ('<div class="nav-item" id="nav-email_mkt" onclick="showPanel(\'email_mkt\')">'
            '<i data-lucide="mail" class="nav-icon"></i> Email marketing</div>') in marketing
    captacion = menu[menu.index('nav-section-label">CAPTACIÓN'):]
    assert re.findall(r'id="nav-(\w+)"', captacion) == ["cola", "metrics"]   # SDR salio el 23/9

    prioridad = re.findall(r"'(\w+)'", re.search(r"const NAV_PRIORITY = \[([^\]]*)\]", HTML).group(1))
    assert prioridad.index("email_mkt") == prioridad.index("meta") + 1
    assert "email_mkt:'mail'" in re.search(r"const NAV_ICONS = \{(.*?)\n\}", HTML, re.S).group(1)
    assert "email_mkt:'Email mkt'" in re.search(r"const NAV_LABELS = \{(.*?)\n\}", HTML, re.S).group(1)
    listas = re.findall(r"const ALL_PANELS = \[([^\]]*)\]", SRC)
    assert len(listas) == 2 and all("'email_mkt'" in l for l in listas)
    assert "email_mkt:'Email marketing'" in re.search(r"const PANEL_LABELS = \{([^}]*)\}", SRC).group(1)
    assert "if (name === 'email_mkt') loadEmailMkt();" in HTML
    assert "from routes.email_marketing import email_mkt_bp" in SRC
    assert "email_mkt_bp" in re.search(r"for bp in \((.*?)\):", SRC, re.S).group(1)
    assert HTML.count('id="email_mkt-panel"') == 1


def test_el_icono_tiene_color_propio_en_los_dos_temas():
    oscuro = re.search(r"\n#nav-email_mkt \.nav-icon\{stroke:(#[0-9a-f]{6})\}", HTML).group(1)
    activo = re.search(r"\n#nav-email_mkt\.active \.nav-icon\{stroke:(#[0-9a-f]{6})\}", HTML).group(1)
    claro = re.search(r"\nbody\.light #nav-email_mkt \.nav-icon\{stroke:(#[0-9a-f]{6})\}", HTML).group(1)
    for patron, color in ((r"\n#nav-(\w+) \.nav-icon\{stroke:(#[0-9a-f]{6})\}", oscuro),
                          (r"\n#nav-(\w+)\.active \.nav-icon\{stroke:(#[0-9a-f]{6})\}", activo),
                          (r"\nbody\.light #nav-(\w+) \.nav-icon\{stroke:(#[0-9a-f]{6})\}", claro)):
        otros = {c for p, c in re.findall(patron, HTML) if p != "email_mkt"}
        assert color not in otros, (patron, color)


def test_la_carga_inicial_no_cambia():
    assert "calLoaded = true; renderCalendar();" in HTML
    assert "setInterval" not in JS and "showPanel" not in JS


@pytest.mark.parametrize("nombre", sorted(FUENTES))
def test_nada_que_jinja_o_python_interpreten(nombre):
    for trampa in ("{#", "{{", "{%"):
        assert trampa not in FUENTES[nombre], f"{trampa!r} en el {nombre} de Email marketing"
    assert chr(92) not in FUENTES[nombre], f"un backslash en el {nombre}"


@pytest.mark.parametrize("nombre", sorted(FUENTES))
def test_no_muestra_dinero(nombre):
    bajo = FUENTES[nombre].lower()
    assert not [p for p in PROHIBIDAS if p in bajo], nombre


def test_el_css_usa_solo_tokens_y_se_adapta_al_celular():
    reglas = re.findall(r"([^{}]+)\{([^{}]*)\}", CSS)
    assert len(reglas) > 30
    for selector, cuerpo in reglas:
        assert not re.search(r"#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])|rgba?\(", cuerpo), selector.strip()
    assert "body.light" not in CSS.split("*/", 1)[1]
    assert "style=" not in PANEL and "style=" not in JS
    assert re.search(r"\.em-tabla-wrap\{overflow-x:auto", CSS)
    assert "@media(max-width:560px)" in CSS


def test_todo_lo_del_js_lleva_el_prefijo_em():
    nombres = re.findall(r"^(?:async )?function ([A-Za-z_$][\w$]*)\(", JS, re.M)
    nombres += re.findall(r"^(?:const|let) ([A-Za-z_$][\w$]*)", JS, re.M)
    assert len(nombres) > 20
    assert not [n for n in nombres if not (n.startswith("em") or n.startswith("EM_") or n == "loadEmailMkt")]
    for nombre in nombres:
        assert len(re.findall(r"^(?:async )?(?:function|const|let) " + re.escape(nombre) + r"\b", HTML, re.M)) == 1


def test_la_pagina_abre_con_el_panel(jefe):
    r = jefe.get("/")
    assert r.status_code == 200
    assert b'id="email_mkt-panel"' in r.data


# ── se pinta de verdad (node) ────────────────────────────────────────────────

_ARNES = r"""
const _els = {};
function _el(id) {
  if (!_els[id]) {
    const clases = new Set();
    _els[id] = { id, innerHTML: '', textContent: '', value: '', disabled: false, style: {}, dataset: {},
                 _clases: clases,
                 classList: { add(c){ clases.add(c); }, remove(c){ clases.delete(c); },
                              toggle(){}, contains: c => clases.has(c) },
                 querySelectorAll: () => [], querySelector: () => null, closest: () => null,
                 setAttribute(){}, getAttribute: () => null, focus(){},
                 addEventListener(){}, appendChild(){}, remove(){} };
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


def _correr_js(tmp_path, respuestas, prueba):
    bloques = re.findall(r"<script>(.*?)</script>", HTML, re.S)
    charts = (RAIZ / "static" / "charts.js").read_text(encoding="utf-8")
    archivo = tmp_path / "email_mkt.js"
    archivo.write_text(_ARNES.replace("__RESPUESTAS__", json.dumps(respuestas, ensure_ascii=False))
                       + charts + "\n" + "\n".join(bloques) + "\n" + prueba, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, (r.stderr or "")[:2000]
    return json.loads(r.stdout.strip().splitlines()[-1])


@sin_node
def test_la_pantalla_se_pinta_con_lo_que_devuelve_el_servidor(app, jefe, tmp_path):
    db_path = app.config["DB_PATH"]
    bid = insert_business(db_path, {"name": "Veterinaria <Sol>", "phone": "+59899333444"})
    mes = em.mes_actual()
    ahora = em.ahora_utc()
    for i in range(27):
        _fila(db_path, resend_id=f"re_js_{i}", enviado_at=ahora, destinatario=f"d{i}@x.uy",
              asunto=f"Asunto {i}", business_id=bid if i == 26 else None,
              entregado_at=ahora if i % 3 == 0 else None, rebotado_at=ahora if i == 1 else None)
    base = jefe.get("/api/email-marketing").get_json()
    pagina2 = jefe.get("/api/email-marketing?pagina=2").get_json()
    anterior_mes = em.resumen(db_path, "2026-01")
    anterior_mes.update({"es_admin": True, "hay_api_key": False, "mes_actual": mes})
    anterior = str(int(mes[:4]) - (mes[5:] == "01")) + "-" + ("12" if mes[5:] == "01" else f"{int(mes[5:]) - 1:02d}")
    buscado = jefe.get("/api/email-marketing?q=d26%40x.uy").get_json()

    prueba = """
(async () => {
  const s = {};
  await loadEmailMkt();
  ['em-contadores', 'em-aviso', 'em-grafico', 'em-tabla', 'em-paginas'].forEach(id => { s[id] = _el(id).innerHTML; });
  s.mes = _el('em-mes-label').textContent;
  s.sigDeshabilitado = _el('em-mes-sig').disabled;
  s.tipos = _el('em-tipo').innerHTML;
  s.estadoOculto = _el('em-estado')._clases.has('em-oculto');
  s.botonOculto = _el('em-btn-estados')._clases.has('em-oculto');
  s.botonDeshabilitado = _el('em-btn-estados').disabled;
  s.nota = _el('em-estados-nota').textContent;

  emIrPagina(1);
  await new Promise(r => setTimeout(r, 10));
  s.pedidoPagina = _pedidos[_pedidos.length - 1][0];
  s.tablaPagina2 = _el('em-tabla').innerHTML;

  emMes(-1);
  await new Promise(r => setTimeout(r, 10));
  s.pedidoMes = _pedidos[_pedidos.length - 1][0];
  emMesHoy();
  await new Promise(r => setTimeout(r, 10));

  _el('em-buscar').value = 'd26@x.uy';
  emBuscar(); emBuscar(); emBuscar();
  await new Promise(r => setTimeout(r, 400));
  s.pedidosBusqueda = _pedidos.filter(p => p[0].indexOf('q=') !== -1).map(p => p[0]);
  s.tablaBuscada = _el('em-tabla').innerHTML;

  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
"""
    respuestas = {"/api/email-marketing": base, "/api/email-marketing?pagina=2": pagina2,
                  f"/api/email-marketing?mes={anterior}": anterior_mes,
                  "/api/email-marketing?q=d26%40x.uy": buscado}
    s = _correr_js(tmp_path, respuestas, prueba)

    c = s["em-contadores"]
    assert c.count('class="em-contador ') == 6
    assert '<div class="em-contador-num">27</div><div class="em-contador-rotulo">Enviados</div>' in c
    assert '<div class="em-contador-num">9</div><div class="em-contador-rotulo">Entregados</div>' in c
    assert "33,3 % de los enviados" in c and "3,7 % de los enviados" in c
    assert "17 sin datos de Resend" in c
    assert "17 de 27 envíos todavía no tienen datos de Resend" in s["em-aviso"]
    assert s["em-grafico"].startswith('<div class="sc-panel-serie">') and "<svg" in s["em-grafico"]

    t = s["em-tabla"]
    assert t.count("<tr>") == 26, "cabecera + 25 filas"
    assert "d26@x.uy" in t and 'em-chip em-chip-verde">Entregado' in t and 'em-chip em-chip-azul">Enviado' in t
    # Misma hora para todos: el orden sigue al id, y el rebotado (el segundo) cae en la página 2.
    assert 'em-chip em-chip-rojo">Rebotado' in s["tablaPagina2"]
    assert f'onclick="openClientPanel({bid})">Veterinaria &lt;Sol&gt;</button>' in t, "escapado y linkeado"
    assert "Página 1 de 2 · 27 envíos" in s["em-paginas"]
    assert s["mes"] and s["sigDeshabilitado"] is True and s["estadoOculto"] is True
    assert "Discovery en frío" in s["tipos"]
    assert s["botonOculto"] is False and s["botonDeshabilitado"] is True and "RESEND_API_KEY" in s["nota"]

    assert s["pedidoPagina"] == "/api/email-marketing?pagina=2"
    assert s["tablaPagina2"].count("<tr>") == 3
    assert s["pedidoMes"] == f"/api/email-marketing?mes={anterior}"
    assert s["pedidosBusqueda"] == ["/api/email-marketing?q=d26%40x.uy"], "la búsqueda espera a que termines"
    assert s["tablaBuscada"].count("<tr>") == 2


@sin_node
def test_si_el_servidor_falla_lo_dice_y_sin_admin_no_hay_boton(app, tmp_path):
    c = _cli(app, "sdr@scalerics.com", ["email_mkt"])
    vacio = c.get("/api/email-marketing").get_json()
    prueba = """
(async () => {
  const s = {};
  await loadEmailMkt();
  s.vacio = _el('em-estado').textContent;
  s.tabla = _el('em-tabla').innerHTML;
  s.botonOculto = _el('em-btn-estados')._clases.has('em-oculto');
  _RESPUESTAS['/api/email-marketing'] = 'falla';
  await loadEmailMkt();
  s.error = _el('em-estado').textContent;
  s.errorVisible = !_el('em-estado')._clases.has('em-oculto');
  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
"""
    s = _correr_js(tmp_path, {"/api/email-marketing": vacio}, prueba)
    assert s["vacio"].startswith("No hay envíos registrados en ")
    assert "No hay envíos que coincidan" in s["tabla"]
    assert s["botonOculto"] is True
    assert "No se pudieron cargar" in s["error"] and s["errorVisible"] is True
