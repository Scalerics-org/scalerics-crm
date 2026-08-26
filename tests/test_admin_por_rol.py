"""El rol "Admin" tiene que otorgar admin de verdad.

El acceso a usuarios y roles dependia UNICAMENTE de ADMIN_EMAIL, que admite un
solo valor. En produccion hay cuatro personas con el rol "Admin" y ninguna podia
abrir la seccion de usuarios: solo entraba quien coincidiera con esa variable.
Dos cosas distintas se llamaban igual, y la que se veia en pantalla no mandaba.
"""

import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services.auth import is_admin


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "raiz@scalerics.com")
    db = str(tmp_path / "a.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


def _rol(db, nombre, paneles='["cola"]'):
    conn = sqlite3.connect(db)
    try:
        fila = conn.execute("SELECT id FROM roles WHERE name=?", (nombre,)).fetchone()
        if fila:
            return fila[0]
        rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)",
                           (nombre, paneles)).lastrowid
        conn.commit()
        return rid
    finally:
        conn.close()


def _usuario(db, email, role_id=None):
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    if role_id is not None:
        conn = sqlite3.connect(db)
        conn.execute("UPDATE users SET role_id=? WHERE id=?", (role_id, uid))
        conn.commit()
        conn.close()
    return uid


def _cli(app, uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "test"
    return c


# ── quien es admin ───────────────────────────────────────────────────────────

def test_el_rol_admin_otorga_admin(app):
    db = app.config["_DB"]
    uid = _usuario(db, "gonza@scalerics.com", _rol(db, "Admin"))
    assert is_admin(db, uid) is True


def test_admin_email_es_admin_aunque_no_tenga_rol(app):
    """Salida de emergencia: esa cuenta entra aunque le saquen el rol."""
    db = app.config["_DB"]
    assert is_admin(db, _usuario(db, "raiz@scalerics.com")) is True


def test_un_rol_cualquiera_no_otorga_admin(app):
    db = app.config["_DB"]
    uid = _usuario(db, "ventas@scalerics.com", _rol(db, "Ventas"))
    assert is_admin(db, uid) is False


def test_sin_rol_no_es_admin(app):
    db = app.config["_DB"]
    assert is_admin(db, _usuario(db, "nadie@scalerics.com")) is False


def test_el_id_1_no_es_admin_encubierto_si_hay_admin_email(app):
    """Con ADMIN_EMAIL configurado, ser el primer usuario no alcanza."""
    db = app.config["_DB"]
    primero = _usuario(db, "primero@scalerics.com", _rol(db, "Ventas"))
    assert primero == 1
    assert is_admin(db, primero) is False


def test_sin_admin_email_el_id_1_si_es_admin(app, monkeypatch):
    """Compatibilidad con instalaciones que no configuran la variable."""
    db = app.config["_DB"]
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    assert is_admin(db, _usuario(db, "primero@scalerics.com")) is True


# ── efecto en los endpoints ──────────────────────────────────────────────────

def test_el_rol_admin_puede_ver_la_lista_de_usuarios(app):
    db = app.config["_DB"]
    c = _cli(app, _usuario(db, "gonza@scalerics.com", _rol(db, "Admin")))
    assert c.get("/api/admin/users-data").status_code == 200


def test_api_me_le_dice_al_frontend_que_es_admin(app):
    """Sin esto el backend lo deja entrar pero el nav no le muestra el link."""
    db = app.config["_DB"]
    c = _cli(app, _usuario(db, "gonza@scalerics.com", _rol(db, "Admin")))
    assert c.get("/api/me").get_json()["is_admin"] is True


def test_un_rol_sin_admin_no_ve_la_lista(app):
    db = app.config["_DB"]
    c = _cli(app, _usuario(db, "ventas@scalerics.com", _rol(db, "Ventas")))
    assert c.get("/api/admin/users-data").status_code == 403


def test_la_pagina_de_admin_redirige_a_quien_no_es_admin(app):
    db = app.config["_DB"]
    c = _cli(app, _usuario(db, "ventas@scalerics.com", _rol(db, "Ventas")))
    r = c.get("/admin/users")
    assert r.status_code in (301, 302)
