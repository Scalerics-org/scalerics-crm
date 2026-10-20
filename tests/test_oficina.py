"""Oficina: el equipo de agentes de Juan (services/oficina.py, 20/10)."""

import json
import re
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services import oficina as of

TOKEN = "t" * 40


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "of.db")
    init_db(ruta)
    return ruta


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.setenv("OFICINA_BOT_TOKEN", TOKEN)
    ruta = str(tmp_path / "of-app.db")
    init_db(ruta)
    a = dashboard.create_app(ruta)
    a.config["TESTING"] = True
    return a


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
        s["user_name"] = "Juan"
    return c


# ── servicio ─────────────────────────────────────────────────────────────────

def test_son_cinco_agentes_y_cada_uno_tiene_su_cara():
    import os
    assert list(of.AGENTES) == ["sofia", "tomas", "lucia", "mateo", "bruno"]
    for ag in of.AGENTES:
        assert os.path.isfile(os.path.join("static", "oficina", ag + ".svg"))


def test_mateo_solo_mira_la_pauta():
    assert "Andrés" in of.AGENTES["mateo"]["descripcion"]
    assert "solo mira" in of.AGENTES["mateo"]["rol"]


def test_pregunta_queda_pendiente_hasta_que_el_bot_responde(db):
    pid = of.preguntar(db, "lucia", "  ¿Qué clientes me deben plata?  ")
    pend = of.pendientes(db)
    assert [p["id"] for p in pend] == [pid]
    assert pend[0]["pregunta"] == "¿Qué clientes me deben plata?"
    assert pend[0]["agente"]["nombre"] == "Lucía"
    assert of.listar_agentes(db)[2]["pendientes"] == 1

    rid = of.responder(db, pid, "Dos abonos vencidos.", "Finanzas")
    assert of.pendientes(db) == []
    mensajes = of.ficha(db, "lucia")["mensajes"]
    assert [(m["autor"], m["estado"]) for m in mensajes] == [("juan", "respondido"), ("agente", None)]
    assert mensajes[1]["id"] == rid and mensajes[1]["fuente"] == "Finanzas"
    with pytest.raises(of.NoSePuede):
        of.responder(db, pid, "otra vez")


def test_el_bot_recibe_indicaciones_aprendizajes_y_lo_anterior(db):
    of.agregar_indicacion(db, "tomas", "Los lunes decime qué leads se enfriaron")
    borrada = of.agregar_indicacion(db, "tomas", "Esto ya no")
    of.borrar_indicacion(db, borrada)
    of.agregar_indicacion(db, "lucia", "No es de Tomás")
    p1 = of.preguntar(db, "tomas", "¿Cómo viene Lucas?")
    r1 = of.responder(db, p1, "Bien.")
    of.opinar(db, r1, "corregir", "Dame números, no adjetivos")
    of.preguntar(db, "tomas", "¿Y los leads?")

    [p] = of.pendientes(db)
    assert p["indicaciones"] == ["Los lunes decime qué leads se enfriaron"]
    assert p["aprendizajes"] == ["Dame números, no adjetivos"]
    assert [m["texto"] for m in p["conversacion_previa"]] == ["¿Cómo viene Lucas?", "Bien."]


def test_corregir_crea_un_aprendizaje_y_olvidar_lo_saca(db):
    rid = of.responder(db, of.preguntar(db, "bruno", "¿Qué mails hay?"), "Tres.")
    of.opinar(db, rid, "corregir", "Decime de quién son")
    f = of.ficha(db, "bruno")
    assert f["mensajes"][-1]["feedback"] == "corregido"
    [apr] = f["aprendizajes"]
    assert apr["texto"] == "Decime de quién son" and "corregiste" in apr["origen"]
    of.olvidar(db, apr["id"])
    assert of.ficha(db, "bruno")["aprendizajes"] == []
    with pytest.raises(of.NoSePuede):
        of.olvidar(db, apr["id"])


def test_me_sirvio_no_crea_aprendizaje(db):
    rid = of.responder(db, of.preguntar(db, "sofia", "¿Qué decido hoy?"), "Nada.")
    of.opinar(db, rid, "ok")
    f = of.ficha(db, "sofia")
    assert f["mensajes"][-1]["feedback"] == "ok" and f["aprendizajes"] == []


@pytest.mark.parametrize("llamada", [
    lambda db: of.preguntar(db, "nadie", "hola"),
    lambda db: of.preguntar(db, "sofia", "   "),
    lambda db: of.preguntar(db, "sofia", "x" * (of.LARGO_MENSAJE + 1)),
    lambda db: of.agregar_indicacion(db, "sofia", ""),
    lambda db: of.borrar_indicacion(db, 999),
    lambda db: of.opinar(db, 999, "ok"),
    lambda db: of.opinar(db, of.preguntar(db, "sofia", "hola"), "ok"),
    lambda db: of.opinar(db, 1, "cualquiera"),
])
def test_rechaza_lo_que_no_corresponde(db, llamada):
    with pytest.raises(of.NoSePuede):
        llamada(db)


def test_init_es_idempotente(db):
    init_db(db)
    assert of.listar_agentes(db)[0]["id"] == "sofia"


# ── rutas ────────────────────────────────────────────────────────────────────

def test_el_panel_es_solo_para_admin(app):
    otro = _cli(app, "otro@scalerics.com", paneles=["oficina", "finanzas"])
    assert otro.get("/api/oficina/agentes").status_code == 403
    assert otro.post("/api/oficina/agentes/sofia/mensajes", json={"texto": "hola"}).status_code == 403
    jefe = _cli(app, "jefe@scalerics.com")
    r = jefe.get("/api/oficina/agentes")
    assert r.status_code == 200 and len(r.get_json()["agentes"]) == 5


def test_flujo_completo_por_la_api(app):
    jefe = _cli(app, "jefe@scalerics.com")
    assert jefe.post("/api/oficina/agentes/lucia/mensajes", json={"texto": "¿Llegamos?"}).get_json()["ok"]
    assert jefe.post("/api/oficina/agentes/lucia/indicaciones", json={"texto": "Avisame 3 días antes"}).get_json()["ok"]
    assert jefe.post("/api/oficina/agentes/lucia/mensajes", json={"texto": ""}).status_code == 400
    assert jefe.get("/api/oficina/agentes/nadie").status_code == 404

    bot = app.test_client()
    pend = bot.get("/api/oficina-bot/pendientes", headers={"x-oficina-token": TOKEN}).get_json()["pendientes"]
    assert pend[0]["indicaciones"] == ["Avisame 3 días antes"]
    r = bot.post(f"/api/oficina-bot/mensajes/{pend[0]['id']}/responder",
                 headers={"x-oficina-token": TOKEN}, json={"texto": "Sí, justo.", "fuente": "Finanzas"})
    rid = r.get_json()["id"]

    assert jefe.post(f"/api/oficina/mensajes/{rid}/opinion",
                     json={"tipo": "corregir", "correccion": "Decime el saldo"}).get_json()["ok"]
    f = jefe.get("/api/oficina/agentes/lucia").get_json()
    assert f["aprendizajes"][0]["texto"] == "Decime el saldo"
    assert jefe.delete(f"/api/oficina/aprendizajes/{f['aprendizajes'][0]['id']}").get_json()["ok"]
    assert jefe.delete(f"/api/oficina/indicaciones/{f['indicaciones'][0]['id']}").get_json()["ok"]
    f = jefe.get("/api/oficina/agentes/lucia").get_json()
    assert f["aprendizajes"] == [] and f["indicaciones"] == []


def test_el_bot_necesita_su_token(app, monkeypatch):
    bot = app.test_client()
    assert bot.get("/api/oficina-bot/pendientes").status_code == 403
    assert bot.get("/api/oficina-bot/pendientes", headers={"x-oficina-token": "malo"}).status_code == 403
    monkeypatch.setenv("OFICINA_BOT_TOKEN", "corto")
    assert bot.get("/api/oficina-bot/pendientes", headers={"x-oficina-token": "corto"}).status_code == 403


def test_el_token_del_bot_no_abre_el_resto(app):
    bot = app.test_client()
    r = bot.get("/api/oficina/agentes", headers={"x-oficina-token": TOKEN})
    assert r.status_code in (302, 401, 403)


# ── pantalla ─────────────────────────────────────────────────────────────────

HTML = dashboard.DASHBOARD_HTML


def test_la_pantalla_esta_y_arranca_oculta():
    assert '<div id="oficina-panel" class="panel">' in HTML
    assert re.search(r'id="nav-oficina"[^>]*style="display:none"', HTML)
    assert "if (name === 'oficina') ofCargar();" in HTML
    assert "getElementById('nav-oficina')" in HTML


def test_oficina_no_se_reparte_por_rol():
    todos = re.search(r"const ALL_PANELS = \[([^\]]*)\]", HTML).group(1)
    assert "oficina" not in todos
