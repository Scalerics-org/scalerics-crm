"""Contraseñas de la empresa, solo admin (pedido de Juan, 22/9).

"Abajo del todo, en el CRM, hay que poner una seccion de contraseñas [...]
pone 3 campos, el nombre de la red social o lo que sea, la clave, y si tiene
codigo de autentificacion y de quien es y cual es."

Nunca se guarda una clave en texto plano: `services/credenciales.py` cifra
antes de INSERT/UPDATE. La clave de cifrado sale de CREDENCIALES_KEY, un
secret de Fly, no de la base ni del código.
"""

import json
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services import credenciales as cred

CLAVE_DE_TEST = "b" * 43 + "="  # 32 bytes en base64 urlsafe, formato de Fernet


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "cred.db")
    init_db(ruta)
    return ruta


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.setenv("CREDENCIALES_KEY", CLAVE_DE_TEST)
    ruta = str(tmp_path / "cred-app.db")
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


@pytest.fixture
def jefe(app):
    return _cli(app, "jefe@scalerics.com")


# ── cifrado ──────────────────────────────────────────────────────────────────

def test_cifrar_y_descifrar_da_lo_mismo(monkeypatch):
    monkeypatch.setenv("CREDENCIALES_KEY", CLAVE_DE_TEST)
    token = cred.cifrar("Contraseña123!")
    assert token != "Contraseña123!", "no puede quedar en texto plano"
    assert cred.descifrar(token) == "Contraseña123!"


def test_sin_clave_no_se_puede_cifrar(monkeypatch):
    monkeypatch.delenv("CREDENCIALES_KEY", raising=False)
    assert cred.activo() is False
    with pytest.raises(cred.SinClave):
        cred.cifrar("algo")


def test_descifrar_con_otra_clave_avisa_en_vez_de_reventar(monkeypatch):
    monkeypatch.setenv("CREDENCIALES_KEY", CLAVE_DE_TEST)
    token = cred.cifrar("secreto")
    monkeypatch.setenv("CREDENCIALES_KEY", "c" * 43 + "=")
    assert "no se pudo descifrar" in cred.descifrar(token)


# ── rutas ────────────────────────────────────────────────────────────────────

def test_sin_admin_da_403(app):
    c = _cli(app, "vendedor@scalerics.com", ["cola"])
    assert c.get("/api/credenciales").status_code == 403
    assert c.post("/api/credenciales", json={"servicio": "x", "clave": "y"}).status_code == 403


def test_admin_crea_lista_edita_y_borra(jefe):
    r = jefe.post("/api/credenciales", json={
        "servicio": "Instagram", "usuario": "contacto@scalerics.com",
        "clave": "Sup3rClave!", "codigo_2fa": "llega al mail de Juan",
        "notas": "cuenta de la empresa"})
    assert r.status_code == 201
    cid = r.get_json()["credencial"]["id"]
    assert r.get_json()["credencial"]["clave"] == "Sup3rClave!"

    lista = jefe.get("/api/credenciales").get_json()["credenciales"]
    assert len(lista) == 1 and lista[0]["servicio"] == "Instagram"
    assert lista[0]["clave"] == "Sup3rClave!", "se devuelve descifrada para mostrarla"

    r = jefe.put(f"/api/credenciales/{cid}", json={
        "servicio": "Instagram", "usuario": "contacto@scalerics.com",
        "clave": "OtraClave!", "codigo_2fa": "", "notas": ""})
    assert r.status_code == 200 and r.get_json()["credencial"]["clave"] == "OtraClave!"

    assert jefe.delete(f"/api/credenciales/{cid}").status_code == 200
    assert jefe.get("/api/credenciales").get_json()["credenciales"] == []


def test_la_base_nunca_tiene_la_clave_en_texto_plano(jefe, app):
    jefe.post("/api/credenciales", json={"servicio": "Plexo", "clave": "ClaveVisible123"})
    conn = sqlite3.connect(app.config["DB_PATH"])
    fila = conn.execute("SELECT clave_cifrada FROM credenciales").fetchone()
    conn.close()
    assert "ClaveVisible123" not in fila[0]


@pytest.mark.parametrize("cuerpo,mensaje", [
    ({"clave": "x"}, "nombre"),
    ({"servicio": "Mail"}, "obligatoria"),
    ({"servicio": "Mail", "clave": "   "}, "obligatoria"),
])
def test_validaciones(jefe, cuerpo, mensaje):
    r = jefe.post("/api/credenciales", json=cuerpo)
    assert r.status_code == 400 and mensaje in r.get_json()["error"]


def test_editar_o_borrar_una_que_no_existe_da_404(jefe):
    assert jefe.put("/api/credenciales/999", json={"servicio": "x", "clave": "y"}).status_code == 404
    assert jefe.delete("/api/credenciales/999").status_code == 404


def test_sin_la_clave_de_cifrado_avisa_en_vez_de_500(app, jefe, monkeypatch):
    monkeypatch.delenv("CREDENCIALES_KEY", raising=False)
    r = jefe.get("/api/credenciales")
    assert r.status_code == 503 and "CREDENCIALES_KEY" in r.get_json()["error"]
