"""Quien puede ver un panel financiero.

El `panel_access` del CRM solo escondia el item del menu: nada frenaba un
fetch de un usuario logueado sin ese panel. Para los leads es tolerable; para
la plata no. Estos tests prueban `tiene_panel` directo, sin HTTP y sin Flask,
porque la ruta que la va a usar todavia no existe (Task 6).
"""

import sqlite3

import pytest
from werkzeug.security import generate_password_hash

from database import create_user, init_db
from services.auth import tiene_panel


@pytest.fixture(autouse=True)
def _admin_email_definido(monkeypatch):
    """Fija ADMIN_EMAIL para que el respaldo de arranque no tape lo que se prueba.

    `is_admin` termina con `return not admin_email and current["id"] == 1`: sin
    ADMIN_EMAIL, el PRIMER usuario de la base es admin, y un admin ve todos los
    paneles. Como cada test crea su usuario en una base limpia, ese usuario es
    siempre el id 1 — asi que sin esta fijacion los siete casos que esperan
    False daban True y el test decia "puede ver finanzas" cuando lo que pasaba
    era "es el admin de arranque".

    Peor: pasaban o fallaban segun si quien corria la suite tenia ADMIN_EMAIL en
    su ambiente. En una maquina con `.env` cargado pasaban; en CI no. Se fija un
    mail que no es el de ningun usuario de estos tests.
    """
    monkeypatch.setenv("ADMIN_EMAIL", "nadie@scalerics.invalid")


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "a.db")
    init_db(ruta)
    return ruta


def _rol(db, nombre, paneles):
    conn = sqlite3.connect(db)
    try:
        fila = conn.execute("SELECT id FROM roles WHERE name=?", (nombre,)).fetchone()
        if fila:
            conn.execute("UPDATE roles SET panel_access=? WHERE id=?",
                         (paneles, fila[0]))
            conn.commit()
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


def test_un_rol_sin_el_panel_da_false(db):
    uid = _usuario(db, "caller@scalerics.com", _rol(db, "Caller", '["cola"]'))
    assert tiene_panel(db, uid, "finanzas") is False


def test_un_rol_con_el_panel_da_true(db):
    uid = _usuario(db, "socio@scalerics.com", _rol(db, "Socio", '["cola", "finanzas"]'))
    assert tiene_panel(db, uid, "finanzas") is True


def test_un_admin_da_true_aunque_su_rol_no_lo_liste(db, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "raiz@scalerics.com")
    uid = _usuario(db, "raiz@scalerics.com", _rol(db, "Caller", '["cola"]'))
    assert tiene_panel(db, uid, "finanzas") is True


def test_un_usuario_sin_rol_da_false(db):
    uid = _usuario(db, "sinrol@scalerics.com")
    assert tiene_panel(db, uid, "finanzas") is False


def test_panel_access_con_json_invalido_da_false_sin_explotar(db):
    uid = _usuario(db, "roto@scalerics.com", _rol(db, "Roto", "{no es json"))
    assert tiene_panel(db, uid, "finanzas") is False


def test_panel_access_vacio_da_false(db):
    """`panel_access` es NOT NULL en la tabla; vacio es el caso limite real."""
    uid = _usuario(db, "vacio@scalerics.com", _rol(db, "Vacio", ""))
    assert tiene_panel(db, uid, "finanzas") is False


def test_panel_access_null_por_join_da_false(db):
    """Un role_id que no matchea ningun rol deja panel_access NULL por el LEFT JOIN."""
    uid = _usuario(db, "huerfano@scalerics.com", role_id=99999)
    assert tiene_panel(db, uid, "finanzas") is False


def test_panel_access_que_no_es_lista_da_false(db):
    uid = _usuario(db, "objeto@scalerics.com", _rol(db, "Objeto", '{"finanzas": true}'))
    assert tiene_panel(db, uid, "finanzas") is False


def test_usuario_inexistente_da_false(db):
    assert tiene_panel(db, 999999, "finanzas") is False


def test_un_panel_access_que_es_un_string_no_abre_nada(db):
    """'"finanzas"' es JSON válido pero no una lista.

    Sin el isinstance previo, `"finanzas" in "finanzas"` daría True y el string
    suelto abriría el panel.
    """
    uid = _usuario(db, "string@scalerics.com", _rol(db, "String", '"finanzas"'))
    assert tiene_panel(db, uid, "finanzas") is False
