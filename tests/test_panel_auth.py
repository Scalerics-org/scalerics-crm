"""Tests de autorizacion por panel.

Antes panel_access solo escondia items del nav en JavaScript: un grep por
enforcement del lado del servidor en routes/ y services/ devolvia dos lineas en
toda la app. Cualquier usuario logueado podia leer o escribir en areas que su rol
no le habilitaba con un fetch desde la consola.
"""

import json
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services.auth import ALL_PANELS, paneles_del_usuario


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.setenv("ALLOW_INSECURE_DEV_KEY", "1")
    db = str(tmp_path / "auth.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["DB_PATH"] = db
    return a


def _crear_rol(db, nombre, paneles):
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)",
                           (nombre, json.dumps(paneles)))
        conn.commit()
        return cur.lastrowid
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


def _cliente(app, uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "test"
    return c


# ── la lista de paneles ──────────────────────────────────────────────────────

def test_sdr_esta_en_all_panels():
    """El nav tiene un panel SDR (showPanel('sdr')) que faltaba en ALL_PANELS.
    Como el editor de roles arma panel_access iterando esa lista, guardar
    cualquier rol borraba el permiso de SDR sin avisar."""
    assert "sdr" in ALL_PANELS


def test_api_me_sirve_la_lista_de_paneles(app):
    db = app.config["DB_PATH"]
    uid = _usuario(db, "jefe@scalerics.com")
    r = _cliente(app, uid).get("/api/me")
    assert r.status_code == 200
    assert r.get_json()["all_panels"] == list(ALL_PANELS)


# ── resolucion de permisos ───────────────────────────────────────────────────

def test_admin_tiene_acceso_total(app):
    db = app.config["DB_PATH"]
    uid = _usuario(db, "jefe@scalerics.com")
    assert paneles_del_usuario(db, uid) is None


def test_usuario_sin_rol_no_tiene_acceso(app):
    db = app.config["DB_PATH"]
    uid = _usuario(db, "nadie@scalerics.com")
    assert paneles_del_usuario(db, uid) == set()


def test_rol_limita_a_sus_paneles(app):
    db = app.config["DB_PATH"]
    rid = _crear_rol(db, "TestCola", ["cola", "sdr"])
    uid = _usuario(db, "caller@scalerics.com", rid)
    assert paneles_del_usuario(db, uid) == {"cola", "sdr"}


def test_panel_access_corrupto_no_da_acceso_total(app):
    """Un JSON roto no puede interpretarse como 'sin restricciones'."""
    db = app.config["DB_PATH"]
    rid = _crear_rol(db, "TestRoto", [])
    conn = sqlite3.connect(db)
    conn.execute("UPDATE roles SET panel_access='no-es-json' WHERE id=?", (rid,))
    conn.commit()
    conn.close()
    uid = _usuario(db, "roto@scalerics.com", rid)
    assert paneles_del_usuario(db, uid) == set()


# ── enforcement real sobre los endpoints ─────────────────────────────────────

def test_rol_sin_calendario_no_puede_leer_el_calendario(app):
    db = app.config["DB_PATH"]
    rid = _crear_rol(db, "TestCola", ["cola"])
    c = _cliente(app, _usuario(db, "caller@scalerics.com", rid))
    r = c.get("/api/calendar/events?start=2026-08-01&end=2026-08-31")
    assert r.status_code == 403
    assert r.get_json()["error"] == "panel_no_autorizado"


def test_rol_sin_leads_no_puede_borrar_un_lead(app):
    """El caso del reporte: un fetch de una linea desde la consola borraba un
    cliente cerrado."""
    db = app.config["DB_PATH"]
    rid = _crear_rol(db, "TestSoloCal", ["cal"])
    c = _cliente(app, _usuario(db, "cal@scalerics.com", rid))
    assert c.delete("/api/leads/1").status_code == 403


def test_rol_con_el_panel_si_puede_entrar(app):
    db = app.config["DB_PATH"]
    rid = _crear_rol(db, "TestAgenda", ["cal"])
    c = _cliente(app, _usuario(db, "agenda@scalerics.com", rid))
    r = c.get("/api/calendar/events?start=2026-08-01&end=2026-08-31")
    assert r.status_code == 200


def test_leads_alcanza_con_cualquiera_de_sus_paneles(app):
    """leads_bp sirve a Cola, Seguimientos, Clientes y Pipeline: se exige tener
    al menos uno, no todos."""
    db = app.config["DB_PATH"]
    rid = _crear_rol(db, "TestPipe", ["pipeline"])
    c = _cliente(app, _usuario(db, "pipe@scalerics.com", rid))
    assert c.get("/api/leads").status_code == 200


def test_admin_no_queda_bloqueado_por_ningun_panel(app):
    db = app.config["DB_PATH"]
    c = _cliente(app, _usuario(db, "jefe@scalerics.com"))
    for ruta in ("/api/leads", "/api/calendar/events?start=2026-08-01&end=2026-08-31"):
        assert c.get(ruta).status_code == 200, ruta


def test_el_token_de_admin_saltea_el_chequeo_de_panel(app, monkeypatch):
    """El acceso server-to-server no depende de una sesion con rol."""
    monkeypatch.setenv("ADMIN_TOKEN", "t" * 40)
    r = app.test_client().get("/api/leads", headers={"x-admin-token": "t" * 40})
    assert r.status_code == 200


def test_sin_sesion_sigue_dando_401_no_403(app):
    r = app.test_client().get("/api/leads")
    assert r.status_code == 401
