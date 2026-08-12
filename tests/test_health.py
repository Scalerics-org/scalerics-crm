"""Tests del healthcheck.

Fly no tenia ningun check configurado: una maquina que respondia pero no podia
leer SQLite (volumen sin montar, disco lleno, base corrupta) se daba por sana.
"""

import os

import pytest

import dashboard
from database import init_db


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ALLOW_INSECURE_DEV_KEY", "1")
    db = str(tmp_path / "h.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["_DB_FILE"] = db
    return a


def test_health_responde_ok_sin_login(app):
    """Tiene que estar exento del login: si no, el chequeo de Fly recibe un
    redirect a /login y da por sana una app que no puede consultar nada."""
    r = app.test_client().get("/health")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok", "db": "ok"}


def test_health_falla_si_la_base_no_se_puede_leer(tmp_path, monkeypatch):
    """El punto del endpoint: tocar la base, no solo devolver 200."""
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ALLOW_INSECURE_DEV_KEY", "1")
    db = str(tmp_path / "rota.db")
    init_db(db)
    app = dashboard.create_app(db)

    # Se rompe la base despues de crear la app, como pasaria en produccion.
    with open(db, "wb") as f:
        f.write(b"esto no es una base sqlite")

    r = app.test_client().get("/health")
    assert r.status_code == 503
    assert r.get_json()["db"] == "unreachable"


def test_health_no_expone_detalles_internos(app):
    """El cuerpo no puede filtrar rutas ni mensajes de excepcion."""
    cuerpo = app.test_client().get("/health").get_data(as_text=True)
    assert "Traceback" not in cuerpo
    assert ".db" not in cuerpo
