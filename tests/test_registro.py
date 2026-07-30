"""El registro estaba abierto a cualquiera: /register creaba la cuenta y la
logueaba en el acto, sin invitacion ni aprobacion. Ahora exige un codigo."""
import os

import pytest

import dashboard
from database import get_user_by_email, init_db

DATOS = {"name": "Alguien", "email": "alguien@ejemplo.com",
         "phone": "+59899111222", "password": "unaclavelarga"}


@pytest.fixture
def app_y_db(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    init_db(db)
    app = dashboard.create_app(db)
    app.config["TESTING"] = True
    return app, db


def test_sin_codigo_configurado_el_registro_esta_deshabilitado(app_y_db, monkeypatch):
    app, db = app_y_db
    monkeypatch.delenv("REGISTER_CODE", raising=False)

    r = app.test_client().post("/register", data=DATOS)

    assert get_user_by_email(db, DATOS["email"]) is None
    assert r.status_code in (200, 403)


def test_codigo_incorrecto_no_crea_la_cuenta(app_y_db, monkeypatch):
    app, db = app_y_db
    monkeypatch.setenv("REGISTER_CODE", "el-bueno")

    r = app.test_client().post("/register", data={**DATOS, "code": "el-malo"})

    assert get_user_by_email(db, DATOS["email"]) is None
    assert r.status_code in (200, 403)


def test_sin_mandar_codigo_no_crea_la_cuenta(app_y_db, monkeypatch):
    app, db = app_y_db
    monkeypatch.setenv("REGISTER_CODE", "el-bueno")

    app.test_client().post("/register", data=DATOS)

    assert get_user_by_email(db, DATOS["email"]) is None


def test_con_el_codigo_correcto_si_crea_la_cuenta(app_y_db, monkeypatch):
    app, db = app_y_db
    monkeypatch.setenv("REGISTER_CODE", "el-bueno")

    app.test_client().post("/register", data={**DATOS, "code": "el-bueno"})

    creado = get_user_by_email(db, DATOS["email"])
    assert creado is not None
    assert creado["name"] == DATOS["name"]


def test_un_usuario_recien_creado_no_tiene_paneles(app_y_db, monkeypatch):
    """Sigue valiendo la regla de siempre: sin rol, sin acceso."""
    app, db = app_y_db
    monkeypatch.setenv("REGISTER_CODE", "el-bueno")
    c = app.test_client()

    c.post("/register", data={**DATOS, "code": "el-bueno"})
    me = c.get("/api/me").get_json()

    assert me["panel_access"] in ("[]", None)
