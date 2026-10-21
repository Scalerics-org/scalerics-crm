"""Plantillas desde el WhatsApp Business de Juan (services/wa_juan.py, 21/10)."""

import json
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services import wa_juan

TOKEN = "t" * 40


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "wj.db")
    init_db(ruta)
    return ruta


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.setenv("OFICINA_BOT_TOKEN", TOKEN)
    ruta = str(tmp_path / "wj-app.db")
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
    return c


@pytest.mark.parametrize("entrada,salida", [
    ("099 123 456", "59899123456"), ("99123456", "59899123456"), ("+598 99 123 456", "59899123456"),
    ("0059899123456", "59899123456"), ("5491155556666", "5491155556666"),
])
def test_normaliza_telefonos(entrada, salida):
    assert wa_juan.normalizar_telefono(entrada) == salida


@pytest.mark.parametrize("malo", ["", "123", "abc"])
def test_rechaza_telefonos_invalidos(malo):
    with pytest.raises(wa_juan.NoSePuede):
        wa_juan.normalizar_telefono(malo)


def test_cola_y_resultado(db):
    eid = wa_juan.encolar(db, "099123456", "Hola Ana", "Ana", 1)
    assert wa_juan.pendientes(db) == [{"id": eid, "telefono": "59899123456", "texto": "Hola Ana"}]
    wa_juan.resultado(db, eid, True)
    assert wa_juan.estado(db, eid)["estado"] == "enviado" and wa_juan.pendientes(db) == []
    with pytest.raises(wa_juan.NoSePuede):
        wa_juan.resultado(db, eid, False, "x")
    otro = wa_juan.encolar(db, "099123456", "Hola", None, 1)
    wa_juan.resultado(db, otro, False, "sin conexión")
    assert wa_juan.estado(db, otro)["error"] == "sin conexión"


def test_tope_diario(db, monkeypatch):
    monkeypatch.setattr(wa_juan, "TOPE_DIARIO", 2)
    wa_juan.encolar(db, "099123456", "uno")
    error = wa_juan.encolar(db, "099123456", "dos")
    wa_juan.resultado(db, error, False, "falló")
    wa_juan.encolar(db, "099123456", "tres")
    with pytest.raises(wa_juan.NoSePuede):
        wa_juan.encolar(db, "099123456", "cuatro")


def test_solo_admin_encola_y_el_bot_manda(app):
    otro = _cli(app, "otro@scalerics.com", paneles=["plantillas"])
    assert otro.post("/api/wa-juan/enviar", json={"phone": "099123456", "text": "hola"}).status_code == 403
    jefe = _cli(app, "jefe@scalerics.com")
    assert jefe.post("/api/wa-juan/enviar", json={"phone": "099123456", "text": ""}).status_code == 400
    eid = jefe.post("/api/wa-juan/enviar", json={"phone": "099123456", "text": "Hola Ana", "destino": "Ana"}).get_json()["id"]
    bot = app.test_client()
    h = {"x-oficina-token": TOKEN}
    assert bot.get("/api/oficina-bot/envios").status_code == 403
    assert bot.get("/api/oficina-bot/envios", headers=h).get_json()["envios"][0]["id"] == eid
    assert bot.post(f"/api/oficina-bot/envios/{eid}/resultado", headers=h, json={"ok": True}).get_json()["ok"]
    assert jefe.get(f"/api/wa-juan/envios/{eid}").get_json()["estado"] == "enviado"


def test_la_pantalla_deja_elegir_el_numero():
    html = dashboard.DASHBOARD_HTML
    assert 'id="pl-conf-desde"' in html and "/api/wa-juan/enviar" in html
    assert "document.getElementById('pl-conf-desde').hidden = !window._isAdmin;" in html
