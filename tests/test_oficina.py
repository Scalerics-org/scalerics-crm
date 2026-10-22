"""Oficina: los recordatorios que arma Bruno (services/oficina.py)."""

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


# ── solo queda Bruno (22/10) ─────────────────────────────────────────────────
# Juan sacó a Sofía, Tomás, Lucía y Mateo, y el chat con indicaciones y
# aprendizajes. La tarea que contestaba abría una sesión de Claude cada 3
# minutos y trababa la notebook. Queda Bruno con los recordatorios.

def test_quedo_solo_la_cara_de_bruno():
    import os
    assert os.listdir(os.path.join("static", "oficina")) == ["bruno.svg"]


def test_no_quedan_rutas_del_chat_con_los_agentes(app):
    jefe = _cli(app, "jefe@scalerics.com")
    for metodo, url in [("get", "/api/oficina/agentes"), ("get", "/api/oficina/agentes/bruno"),
                        ("post", "/api/oficina/agentes/bruno/mensajes"),
                        ("post", "/api/oficina/agentes/bruno/indicaciones"),
                        ("delete", "/api/oficina/indicaciones/1"), ("delete", "/api/oficina/aprendizajes/1"),
                        ("post", "/api/oficina/mensajes/1/opinion")]:
        assert getattr(jefe, metodo)(url, json={}).status_code in (404, 405), url
    bot = app.test_client()
    h = {"x-oficina-token": TOKEN}
    for metodo, url in [("get", "/api/oficina-bot/pendientes"), ("post", "/api/oficina-bot/mensajes/1/responder"),
                        ("get", "/api/oficina-bot/esquema"), ("post", "/api/oficina-bot/consulta")]:
        assert getattr(bot, metodo)(url, headers=h, json={}).status_code in (404, 405), url


def test_el_script_de_bruno_ya_no_tiene_los_comandos_del_chat():
    with open("scripts/oficina_responder.py", encoding="utf-8") as f:
        script = f.read()
    for cmd in ("recordatorios", "crear-recordatorio", "borrador", "hecho"):
        assert '"%s"' % cmd in script
    for cmd in ("pendientes", "responder", "esquema", "consulta"):
        assert 'cmd == "%s"' % cmd not in script


def test_init_es_idempotente(db):
    init_db(db)
    assert of.recordatorios(db)["recordatorios"] == []


# ── rutas ────────────────────────────────────────────────────────────────────

def test_el_bot_necesita_su_token(app, monkeypatch):
    bot = app.test_client()
    url = "/api/oficina-bot/recordatorios"
    assert bot.get(url).status_code == 403
    assert bot.get(url, headers={"x-oficina-token": "malo"}).status_code == 403
    monkeypatch.setenv("OFICINA_BOT_TOKEN", "corto")
    assert bot.get(url, headers={"x-oficina-token": "corto"}).status_code == 403


def test_el_token_del_bot_no_abre_el_resto(app):
    bot = app.test_client()
    r = bot.get("/api/oficina/recordatorios", headers={"x-oficina-token": TOKEN})
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


# ── recordatorios (Bruno + WhatsApp, 21/10) ──────────────────────────────────

def test_bruno_crea_recordatorios_sin_repetir(db):
    hoy = of._hoy().isoformat()
    r1 = of.crear_recordatorio(db, {"que": "Mandarle la propuesta a TNT Burger", "cuando": hoy,
                                    "contacto": "TNT Burger", "cita": "el jueves te paso la propuesta",
                                    "clave": "tnt-propuesta"})
    r2 = of.crear_recordatorio(db, {"que": "otra cosa", "cuando": hoy, "clave": "tnt-propuesta"})
    assert r1["repetido"] is False and r2 == {"id": r1["id"], "repetido": True}
    [rec] = of.recordatorios(db)["recordatorios"]
    assert rec["contacto"] == "TNT Burger" and rec["cita"].startswith("el jueves")


def test_recordame_manana_hecho_y_borrador(db):
    hoy = of._hoy()
    rid = of.crear_recordatorio(db, {"que": "Llamar a Rodelú", "cuando": hoy.isoformat()})["id"]
    of.resolver_recordatorio(db, rid, "manana")
    assert of.recordatorios(db)["recordatorios"][0]["cuando"] == (hoy + of.timedelta(days=1)).isoformat()
    of.resolver_recordatorio(db, rid, "borrador")
    assert [b["id"] for b in of.recordatorios_para_bot(db)["borradores_pedidos"]] == [rid]
    of.guardar_borrador(db, rid, "Hola! ¿Coordinamos la demo?")
    assert of.recordatorios_para_bot(db)["borradores_pedidos"] == []
    assert of.recordatorios(db)["recordatorios"][0]["borrador"].startswith("Hola")
    of.resolver_recordatorio(db, rid, "hecho")
    assert of.recordatorios(db)["recordatorios"] == []
    with pytest.raises(of.NoSePuede):
        of.resolver_recordatorio(db, rid, "hecho")


def test_solo_muestra_la_proxima_semana(db):
    lejos = (of._hoy() + of.timedelta(days=of.DIAS_A_LA_VISTA + 3)).isoformat()
    of.crear_recordatorio(db, {"que": "Cobrar el abono", "cuando": lejos})
    assert of.recordatorios(db)["recordatorios"] == []
    assert len(of.recordatorios_para_bot(db)["abiertos"]) == 1


@pytest.mark.parametrize("datos", [{"que": "", "cuando": "2026-10-21"}, {"que": "x", "cuando": "mañana"}])
def test_recordatorio_invalido(db, datos):
    with pytest.raises(of.NoSePuede):
        of.crear_recordatorio(db, datos)


def test_recordatorios_por_la_api(app):
    bot = app.test_client()
    h = {"x-oficina-token": TOKEN}
    hoy = of._hoy().isoformat()
    rid = bot.post("/api/oficina-bot/recordatorios", headers=h,
                   json={"que": "Responderle a Trouville", "cuando": hoy}).get_json()["id"]
    jefe = _cli(app, "jefe@scalerics.com")
    assert jefe.get("/api/oficina/recordatorios").get_json()["recordatorios"][0]["id"] == rid
    assert jefe.post(f"/api/oficina/recordatorios/{rid}/borrador").get_json()["ok"]
    assert jefe.post(f"/api/oficina/recordatorios/{rid}/cualquiera").status_code == 400
    assert bot.get("/api/oficina-bot/recordatorios", headers=h).get_json()["borradores_pedidos"][0]["id"] == rid
    assert bot.post(f"/api/oficina-bot/recordatorios/{rid}/borrador", headers=h, json={"texto": "Hola!"}).get_json()["ok"]
    assert bot.post(f"/api/oficina-bot/recordatorios/{rid}/hecho", headers=h).get_json()["ok"]
    assert jefe.get("/api/oficina/recordatorios").get_json()["recordatorios"] == []
    otro = _cli(app, "otro@scalerics.com", paneles=["oficina"])
    assert otro.get("/api/oficina/recordatorios").status_code == 403
    assert app.test_client().post("/api/oficina-bot/recordatorios", json={"que": "x", "cuando": hoy}).status_code == 403


def test_la_pantalla_tiene_los_recordatorios():
    assert 'id="of-recs"' in HTML and "function ofCargarRecs()" in HTML


def test_la_pantalla_no_tiene_mas_el_equipo_ni_el_chat():
    for resto in ('id="of-equipo"', 'id="of-ficha"', "/api/oficina/agentes", "ofAgentes",
                  "of-msg", ".of-equipo{", "/static/oficina/sofia.svg"):
        assert resto not in HTML, resto


def test_si_fallan_los_recordatorios_se_avisa():
    """Ahora es lo único del panel: un error no puede quedar como pantalla vacía."""
    cuerpo = HTML[HTML.index("async function ofCargarRecs()"):HTML.index("async function ofRecAccion(")]
    catch = cuerpo[cuerpo.index("} catch (e) {"):]
    assert "nota.textContent = e.message" in catch[:300]
