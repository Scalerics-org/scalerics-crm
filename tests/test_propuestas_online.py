"""Propuesta online: el presupuesto como link con seguimiento y aceptación
(services/propuestas_online.py, routes/propuestas_online.py)."""

import json
import sqlite3
from datetime import timedelta

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import add_attachment, create_user, init_db
from services import propuestas_online as po

HTML = b"<html><body><h1>Presupuesto</h1><script>alert(1)</script></body></html>"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("CRM_URL", "https://crm.test")
    monkeypatch.setenv("FACTORY_PHONE", "+598 99 000 000")
    r = str(tmp_path / "po.db")
    init_db(r)
    return r


@pytest.fixture
def lead(db):
    conn = sqlite3.connect(db)
    lid = conn.execute("INSERT INTO businesses (name, category, city) VALUES (?,?,?)",
                       ("Panadería <La Espiga>", "panaderia", "Montevideo")).lastrowid
    conn.commit()
    conn.close()
    return lid


@pytest.fixture
def adjunto(db, lead):
    return add_attachment(db, lead, "budget", "presupuesto.html", file_data=HTML, mime_type="text/html")


def _usuario(db, email, paneles=None):
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    if paneles is not None:
        conn = sqlite3.connect(db)
        rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)",
                           ("rol-" + email, json.dumps(paneles))).lastrowid
        conn.execute("UPDATE users SET role_id=? WHERE id=?", (rid, uid))
        conn.commit()
        conn.close()
    return uid


def _app(db):
    app = dashboard.create_app(db)
    app.config["TESTING"] = True
    return app


def _equipo(db, email="vende@scalerics.com", paneles=("propuestas",)):
    uid = _usuario(db, email, list(paneles) if paneles is not None else None)
    c = _app(db).test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Vende"
    return c


def _cliente(db, ip="200.40.1.1"):
    """El navegador del cliente: sin sesión y con su IP."""
    c = _app(db).test_client()
    c.environ_base["REMOTE_ADDR"] = ip
    return c


def _publicar(db, lead, monto=1500, dias=30):
    r = po.publicar(db, lead, HTML, "Propuesta web", "Vende", monto=monto, dias=dias)
    return po.por_codigo(db, r["codigo"])


def _correr_reloj(monkeypatch, delta):
    real = po._ahora
    monkeypatch.setattr(po, "_ahora", lambda: real() + delta)


# ── servicio ─────────────────────────────────────────────────────────────────

def test_publicar_congela_el_documento(db, lead):
    p = _publicar(db, lead)
    assert p["html"] == HTML
    assert len(p["codigo"]) >= 20
    assert p["senal"] == "sin_abrir"
    assert p["monto"] == 1500
    assert p["vence_en"] > p["creado_en"]


@pytest.mark.parametrize("campos, error", [
    ({"monto": "mucho"}, "número"),
    ({"monto": -5}, "negativo"),
    ({"dias": 0}, "vigencia"),
    ({"dias": 500}, "vigencia"),
])
def test_publicar_valida(db, lead, campos, error):
    with pytest.raises(po.NoSePuede, match=error):
        po.publicar(db, lead, HTML, "t", "Vende", **campos)


def test_codigo_inexistente_o_raro(db, lead):
    _publicar(db, lead)
    assert po.por_codigo(db, "no-existe-este-codigo") is None
    assert po.por_codigo(db, "") is None
    assert po.por_codigo(db, None) is None
    assert po.por_codigo(db, "x" * 500) is None


def test_la_misma_visita_no_se_cuenta_dos_veces(db, lead, monkeypatch):
    p = _publicar(db, lead)
    assert po.registrar_vista(db, p, "1.1.1.1", "ua")
    assert not po.registrar_vista(db, po.por_codigo(db, p["codigo"]), "1.1.1.1", "ua")
    assert po.registrar_vista(db, po.por_codigo(db, p["codigo"]), "2.2.2.2", "ua")
    _correr_reloj(monkeypatch, timedelta(minutes=31))
    assert po.registrar_vista(db, po.por_codigo(db, p["codigo"]), "1.1.1.1", "ua")
    assert po.por_codigo(db, p["codigo"])["vistas"] == 3


def test_el_equipo_no_suma_visitas(db, lead):
    p = _publicar(db, lead)
    assert not po.registrar_vista(db, p, "1.1.1.1", "ua", interna=True)
    assert po.por_codigo(db, p["codigo"])["vistas"] == 0


def test_no_guarda_la_ip(db, lead):
    p = _publicar(db, lead)
    po.registrar_vista(db, p, "190.64.12.34", "ua")
    conn = sqlite3.connect(db)
    filas = conn.execute("SELECT ip FROM propuestas_online_eventos WHERE ip IS NOT NULL").fetchall()
    conn.close()
    assert filas and all("190.64" not in f[0] for f in filas)


def test_lectura_pide_visita_previa_y_respeta_el_paso(db, lead):
    p = _publicar(db, lead)
    assert not po.registrar_lectura(db, p, "1.1.1.1")  # sin abrir
    po.registrar_vista(db, p, "1.1.1.1", "ua")
    p = po.por_codigo(db, p["codigo"])
    assert not po.registrar_lectura(db, p, "9.9.9.9")  # otro lugar, sin visita
    assert po.registrar_lectura(db, p, "1.1.1.1")
    assert not po.registrar_lectura(db, p, "1.1.1.1")  # latido muy seguido
    assert po.por_codigo(db, p["codigo"])["segundos"] == po.PASO_LATIDO


def test_lectura_tiene_tope(db, lead, monkeypatch):
    p = _publicar(db, lead)
    po.registrar_vista(db, p, "1.1.1.1", "ua")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE propuestas_online SET segundos = ? WHERE id = ?", (po.TOPE_SEGUNDOS, p["id"]))
    conn.commit()
    conn.close()
    assert not po.registrar_lectura(db, po.por_codigo(db, p["codigo"]), "1.1.1.1")


def test_senales(db, lead, monkeypatch):
    p = _publicar(db, lead)
    assert p["senal"] == "sin_abrir"
    po.registrar_vista(db, p, "1.1.1.1", "ua")
    assert po.por_codigo(db, p["codigo"])["senal"] == "vista"
    po.registrar_vista(db, po.por_codigo(db, p["codigo"]), "2.2.2.2", "ua")
    assert po.por_codigo(db, p["codigo"])["senal"] == "caliente"
    _correr_reloj(monkeypatch, timedelta(days=4))
    assert po.por_codigo(db, p["codigo"])["senal"] == "vista"  # se enfrió
    _correr_reloj(monkeypatch, timedelta(days=30))
    assert po.por_codigo(db, p["codigo"])["senal"] == "vencida"


def test_aceptar_guarda_la_firma_y_no_se_responde_dos_veces(db, lead):
    p = _publicar(db, lead)
    r = po.aceptar(db, p, "  Ana   Pérez ", "ana@espiga.uy", True, "1.1.1.1", "ua")
    assert r["estado"] == "aceptada" and r["firma_nombre"] == "Ana Pérez"
    p = po.por_codigo(db, p["codigo"])
    assert p["senal"] == "aceptada" and p["firma_email"] == "ana@espiga.uy"
    # doble click: no falla y no cambia nada
    assert po.aceptar(db, p, "Otra", None, True)["firma_nombre"] == "Ana Pérez"
    with pytest.raises(po.NoSePuede, match="aceptada"):
        po.rechazar(db, p, "caro")
    ev = [e["tipo"] for e in po.ver(db, p["id"])["eventos"]]
    assert ev.count("aceptada") == 1
    assert any(p["sha256"][:12] in (e["detalle"] or "") for e in po.ver(db, p["id"])["eventos"])


@pytest.mark.parametrize("nombre, email, acepto, error", [
    ("", None, True, "nombre"),
    ("Al", None, True, "nombre"),
    ("Ana Pérez", "no-es-mail", True, "email"),
    ("Ana Pérez", None, False, "marcar"),
    ("Ana Pérez", None, "true", "marcar"),
])
def test_aceptar_valida(db, lead, nombre, email, acepto, error):
    p = _publicar(db, lead)
    with pytest.raises(po.NoSePuede, match=error):
        po.aceptar(db, p, nombre, email, acepto)
    assert po.por_codigo(db, p["codigo"])["estado"] == "activa"


def test_dos_respuestas_a_la_vez(db, lead):
    """La segunda llega con la propuesta leída antes de que se guarde la primera."""
    p = _publicar(db, lead)
    vieja = dict(p)
    po.rechazar(db, p, "caro")
    with pytest.raises(po.NoSePuede):
        po.aceptar(db, vieja, "Ana Pérez", None, True)
    assert po.por_codigo(db, p["codigo"])["estado"] == "rechazada"


def test_no_se_acepta_vencida_ni_revocada(db, lead, monkeypatch):
    p = _publicar(db, lead, dias=1)
    q = _publicar(db, lead)
    po.revocar(db, q["id"], "Vende")
    with pytest.raises(po.NoSePuede, match="disponible"):
        po.aceptar(db, po.por_codigo(db, q["codigo"]), "Ana Pérez", None, True)
    with pytest.raises(po.NoSePuede, match="abierta"):
        po.revocar(db, q["id"], "Vende")
    _correr_reloj(monkeypatch, timedelta(days=2))
    with pytest.raises(po.NoSePuede, match="venció"):
        po.aceptar(db, po.por_codigo(db, p["codigo"]), "Ana Pérez", None, True)


def test_resumen(db, lead):
    a = _publicar(db, lead, monto=1000)
    b = _publicar(db, lead, monto=2000)
    _publicar(db, lead, monto=500)
    po.registrar_vista(db, a, "1.1.1.1", "ua")
    po.registrar_vista(db, b, "1.1.1.1", "ua")
    po.aceptar(db, po.por_codigo(db, a["codigo"]), "Ana Pérez", None, True)
    po.rechazar(db, po.por_codigo(db, b["codigo"]), None)
    r = po.resumen(po.listar(db))
    assert r["enviadas"] == 3 and r["abiertas"] == 2 and r["aceptadas"] == 1
    assert r["monto_aceptado"] == 1000 and r["monto_abierto"] == 500
    assert r["tasa_aceptacion"] == 0.5
    assert r["horas_a_respuesta"] is not None
    assert r["por_senal"] == {"aceptada": 1, "rechazada": 1, "sin_abrir": 1}


# ── rutas del equipo ─────────────────────────────────────────────────────────

def test_sin_panel_es_403(db, adjunto):
    c = _equipo(db, paneles=["cola"])
    assert c.get("/api/propuestas-online").status_code == 403
    assert c.post("/api/propuestas-online", json={"attachment_id": adjunto}).status_code == 403


def test_sin_sesion_la_api_no_abre(db, adjunto):
    c = _cliente(db)
    assert c.get("/api/propuestas-online").status_code == 401


def test_publicar_desde_la_ficha(db, lead, adjunto):
    c = _equipo(db)
    r = c.post("/api/propuestas-online", json={"attachment_id": adjunto, "monto": "1200.50"})
    assert r.status_code == 201
    p = r.get_json()["propuesta"]
    assert p["url"].startswith("https://crm.test/p/")
    assert p["monto"] == 1200.5 and p["lead_id"] == lead
    assert "html" not in p
    d = c.get(f"/api/propuestas-online?lead_id={lead}").get_json()
    assert [x["id"] for x in d["propuestas"]] == [p["id"]]
    assert d["resumen"]["enviadas"] == 1


def test_publicar_toma_el_monto_del_presupuesto(db, lead, adjunto):
    from database import create_budget
    create_budget(db, lead, total_amount=900)
    c = _equipo(db)
    p = c.post("/api/propuestas-online", json={"attachment_id": adjunto}).get_json()["propuesta"]
    assert p["monto"] == 900


def test_solo_se_publica_un_presupuesto_html(db, lead):
    demo = add_attachment(db, lead, "demo", "demo.html", file_data=HTML, mime_type="text/html")
    pdf = add_attachment(db, lead, "budget", "p.pdf", file_data=b"%PDF", mime_type="application/pdf")
    link = add_attachment(db, lead, "budget", "drive", url="https://drive.google.com/x")
    c = _equipo(db)
    for aid in (demo, pdf, link, 99999):
        assert c.post("/api/propuestas-online", json={"attachment_id": aid}).status_code == 404
    assert c.post("/api/propuestas-online", json={}).status_code == 400


def test_revocar(db, lead, adjunto):
    c = _equipo(db)
    p = c.post("/api/propuestas-online", json={"attachment_id": adjunto}).get_json()["propuesta"]
    assert c.post(f"/api/propuestas-online/{p['id']}/revocar").get_json()["propuesta"]["senal"] == "revocada"
    assert c.post(f"/api/propuestas-online/{p['id']}/revocar").status_code == 400
    assert _cliente(db).get(f"/p/{p['codigo']}").status_code == 200  # muestra "no disponible"
    assert _cliente(db).get(f"/p/{p['codigo']}/doc").status_code == 404


# ── lo que ve el cliente ─────────────────────────────────────────────────────

def test_pagina_publica(db, lead):
    p = _publicar(db, lead)
    r = _cliente(db).get(f"/p/{p['codigo']}")
    assert r.status_code == 200
    cuerpo = r.get_data(as_text=True)
    assert "Panadería &lt;La Espiga&gt;" in cuerpo and "<La Espiga>" not in cuerpo
    assert "USD 1.500" in cuerpo
    assert "Aceptar propuesta" in cuerpo
    assert "wa.me/59899000000" in cuerpo
    assert f'src="/p/{p["codigo"]}/doc" sandbox=""' in cuerpo
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert "nonce-" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Robots-Tag"].startswith("noindex")
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert po.por_codigo(db, p["codigo"])["vistas"] == 1


def test_el_documento_sale_sin_scripts(db, lead):
    p = _publicar(db, lead)
    r = _cliente(db).get(f"/p/{p['codigo']}/doc")
    assert r.data == HTML
    assert r.headers["Content-Security-Policy"].startswith("sandbox;")


def test_codigo_que_no_existe_es_404(db, lead):
    _publicar(db, lead)
    assert _cliente(db).get("/p/estecodigonoexiste123").status_code == 404
    assert _cliente(db).post("/p/estecodigonoexiste123/aceptar", json={}).status_code == 404


def test_el_equipo_ve_el_link_sin_contar_y_sin_aceptar(db, lead):
    p = _publicar(db, lead)
    c = _equipo(db)
    r = c.get(f"/p/{p['codigo']}")
    assert "tus visitas no se cuentan" in r.get_data(as_text=True)
    assert po.por_codigo(db, p["codigo"])["vistas"] == 0
    r = c.post(f"/p/{p['codigo']}/aceptar", json={"nombre": "Ana Pérez", "acepto": True})
    assert r.status_code == 403
    assert po.por_codigo(db, p["codigo"])["estado"] == "activa"


def test_el_cliente_acepta(db, lead):
    p = _publicar(db, lead)
    c = _cliente(db)
    c.get(f"/p/{p['codigo']}")
    r = c.post(f"/p/{p['codigo']}/aceptar", json={"nombre": "Ana Pérez", "email": "", "acepto": True})
    assert r.get_json() == {"ok": True}
    r = c.post(f"/p/{p['codigo']}/aceptar", json={"nombre": "Ana Pérez", "acepto": True})
    assert r.get_json() == {"ok": True}  # doble click
    assert "Propuesta aceptada" in c.get(f"/p/{p['codigo']}").get_data(as_text=True)
    conn = sqlite3.connect(db)
    acciones = [f[0] for f in conn.execute(
        "SELECT action FROM activity_log WHERE entity_id = ? ORDER BY id", (lead,))]
    conn.close()
    assert acciones == ["propuesta_abierta", "propuesta_aceptada"]


def test_el_cliente_acepta_mal(db, lead):
    p = _publicar(db, lead)
    r = _cliente(db).post(f"/p/{p['codigo']}/aceptar", json={"nombre": "Ana Pérez", "acepto": False})
    assert r.status_code == 409 and "marcar" in r.get_json()["error"]
    r = _cliente(db).post(f"/p/{p['codigo']}/aceptar", data="x" * 9000,
                          content_type="application/json")
    assert r.status_code == 413


def test_el_cliente_dice_que_no(db, lead):
    p = _publicar(db, lead)
    r = _cliente(db).post(f"/p/{p['codigo']}/rechazar", json={"motivo": "Lo vemos en marzo"})
    assert r.get_json() == {"ok": True}
    assert po.por_codigo(db, p["codigo"])["motivo"] == "Lo vemos en marzo"
    assert "Gracias por avisarnos" in _cliente(db).get(f"/p/{p['codigo']}").get_data(as_text=True)


def test_latido_de_lectura(db, lead):
    p = _publicar(db, lead)
    c = _cliente(db)
    c.get(f"/p/{p['codigo']}")
    assert c.post(f"/p/{p['codigo']}/lectura").get_json()["contado"] is True
    assert _cliente(db, ip="8.8.8.8").post(f"/p/{p['codigo']}/lectura").get_json()["contado"] is False


def test_vencida_muestra_aviso(db, lead, monkeypatch):
    p = _publicar(db, lead, dias=1)
    _correr_reloj(monkeypatch, timedelta(days=2))
    r = _cliente(db).get(f"/p/{p['codigo']}")
    assert "venció" in r.get_data(as_text=True)
    assert po.por_codigo(db, p["codigo"])["vistas"] == 0


def test_el_panel_se_reparte_a_quien_vende(tmp_path):
    r = str(tmp_path / "roles.db")
    init_db(r)
    conn = sqlite3.connect(r)
    conn.execute("DELETE FROM panel_grants_aplicados WHERE panel = 'propuestas'")
    conn.execute("INSERT INTO roles (name, panel_access) VALUES ('Vende PO', ?)", (json.dumps(["notion_clients"]),))
    conn.execute("INSERT INTO roles (name, panel_access) VALUES ('Dev PO', ?)", (json.dumps(["tasks"]),))
    conn.commit()
    conn.close()
    init_db(r)
    conn = sqlite3.connect(r)
    roles = dict(conn.execute("SELECT name, panel_access FROM roles WHERE name IN ('Vende PO','Dev PO')"))
    conn.close()
    assert "propuestas" in json.loads(roles["Vende PO"])
    assert "propuestas" not in json.loads(roles["Dev PO"])
