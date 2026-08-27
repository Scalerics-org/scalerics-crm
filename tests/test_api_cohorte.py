"""El parametro `cohorte` de /api/leads.

Nace del import de estados del 27-8-2026: el panel de seguimientos paso de ~35
a 128 leads y 93 eran de Meta. Sin poder separar cohortes, una lista de a quien
llamar deja de ser una lista de trabajo.
"""
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db


@pytest.fixture
def ruta(tmp_path):
    r = str(tmp_path / "leads.db")
    init_db(r)
    conn = sqlite3.connect(r)
    filas = [
        (1, "Meta interesado",      "interesado",     "meta"),
        (2, "Padron interesado",    "interesado",     None),
        (3, "Discovery interesado", "interesado",     "discovery"),
        (4, "Meta a llamar",        "llamar_despues", "meta"),
        (5, "Padron a llamar",      "llamar_despues", None),
    ]
    conn.executemany(
        "INSERT INTO businesses (id, name, crm_status, source) VALUES (?,?,?,?)", filas
    )
    conn.commit()
    conn.close()
    return r


@pytest.fixture
def cliente(ruta):
    app = dashboard.create_app(ruta)
    app.config["TESTING"] = True
    uid = create_user(ruta, name="test", email="test@scalerics.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "test"
    return c


def _ids(res):
    return sorted(b["id"] for b in res.get_json())


def test_sin_cohorte_trae_todas(cliente):
    assert _ids(cliente.get("/api/leads?crm_status=interesado")) == [1, 2, 3]


def test_cohorte_meta(cliente):
    assert _ids(cliente.get("/api/leads?crm_status=interesado&cohorte=meta")) == [1]
    assert _ids(cliente.get("/api/leads?crm_status=llamar_despues&cohorte=meta")) == [4]


def test_cohorte_sin_web_es_source_nulo(cliente):
    assert _ids(cliente.get("/api/leads?crm_status=interesado&cohorte=sin_web")) == [2]
    assert _ids(cliente.get("/api/leads?crm_status=llamar_despues&cohorte=sin_web")) == [5]


def test_cohorte_desconocida_no_trae_nada(cliente):
    """Un valor que no existe devuelve vacio, no todo: un typo no puede abrir la vista."""
    assert _ids(cliente.get("/api/leads?crm_status=interesado&cohorte=noexiste")) == []


def test_la_cohorte_no_rompe_el_panel_de_meta(cliente):
    """crm_group=meta es la vista entera de un origen y no la toca la cohorte."""
    assert _ids(cliente.get("/api/leads?crm_group=meta")) == [1, 4]
