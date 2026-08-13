"""Tests de listado, paginacion y export de leads."""

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import (connect, count_businesses, create_user,
                      get_all_businesses, init_db, insert_business)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ALLOW_INSECURE_DEV_KEY", "1")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    db = str(tmp_path / "l.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


@pytest.fixture
def cli(app):
    db = app.config["_DB"]
    uid = create_user(db, name="Jefe", email="jefe@test.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Jefe"
    return c


_telefono = iter(range(100000, 999999))


def _sembrar(db, n, prefijo="Lead"):
    """Cada lead con un telefono UNICO: businesses.phone tiene indice unico y el
    INSERT es OR IGNORE, asi que repetir telefono descarta la fila en silencio."""
    for i in range(n):
        insert_business(db, {"name": f"{prefijo} {i:03d}",
                             "phone": f"+598{next(_telefono)}",
                             "crm_status": "sin_contactar"})


# ── paginacion ───────────────────────────────────────────────────────────────

def test_pagina_en_la_base_no_en_python(app, cli):
    db = app.config["_DB"]
    _sembrar(db, 120)
    r = cli.get("/api/leads?page=1")
    d = r.get_json()
    assert d["total"] == 120
    assert d["pages"] == 3
    assert len(d["items"]) == 50


def test_la_segunda_pagina_trae_otros_leads(app, cli):
    db = app.config["_DB"]
    _sembrar(db, 120)
    p1 = {b["id"] for b in cli.get("/api/leads?page=1").get_json()["items"]}
    p2 = {b["id"] for b in cli.get("/api/leads?page=2").get_json()["items"]}
    assert len(p1 & p2) == 0, "las paginas se solapan"
    assert len(p1) == len(p2) == 50


def test_pagina_fuera_de_rango_se_acota(app, cli):
    db = app.config["_DB"]
    _sembrar(db, 10)
    d = cli.get("/api/leads?page=99").get_json()
    assert d["page"] == 1 and d["pages"] == 1


def test_pagina_invalida_no_rompe(app, cli):
    db = app.config["_DB"]
    _sembrar(db, 5)
    assert cli.get("/api/leads?page=abc").status_code == 200


def test_sin_page_devuelve_la_lista_completa(app, cli):
    """Los llamadores viejos (stats, metricas) esperan la lista entera."""
    db = app.config["_DB"]
    _sembrar(db, 60)
    d = cli.get("/api/leads").get_json()
    assert isinstance(d, list) and len(d) == 60


def test_la_busqueda_se_aplica_en_sql_y_el_total_coincide(app, cli):
    db = app.config["_DB"]
    _sembrar(db, 30, prefijo="Panaderia")
    _sembrar(db, 20, prefijo="Ferreteria")
    d = cli.get("/api/leads?page=1&search=ferreteria").get_json()
    assert d["total"] == 20
    assert all("Ferreteria" in b["name"] for b in d["items"])


def test_el_total_y_la_lista_cuentan_el_mismo_universo(app):
    """count_businesses() y get_all_businesses() comparten el WHERE, para que la
    paginacion no diga 'pagina 1 de 3' sobre un conjunto distinto."""
    db = app.config["_DB"]
    _sembrar(db, 40, prefijo="Ferreteria")
    _sembrar(db, 10, prefijo="Otro")
    for filtros in ({"search": "ferreteria"}, {"crm_status": "sin_contactar"}, {}):
        assert count_businesses(db, **filtros) == len(get_all_businesses(db, **filtros))


# ── export ───────────────────────────────────────────────────────────────────

def test_el_export_trae_todo_no_solo_la_pagina(app, cli):
    """Se armaba en el browser desde _allLeads, que tenia la pagina actual."""
    db = app.config["_DB"]
    _sembrar(db, 120)
    r = cli.get("/api/leads/export.csv")
    assert r.status_code == 200
    lineas = r.get_data(as_text=True).strip().split("\r\n")
    assert len(lineas) == 121, "faltan filas (cabecera + 120)"
    assert "attachment" in r.headers["Content-Disposition"]


def test_el_export_respeta_los_filtros(app, cli):
    db = app.config["_DB"]
    _sembrar(db, 30, prefijo="Panaderia")
    _sembrar(db, 12, prefijo="Ferreteria")
    lineas = cli.get("/api/leads/export.csv?search=ferreteria").get_data(as_text=True).strip().split("\r\n")
    assert len(lineas) == 13


def test_el_export_neutraliza_formulas(app, cli):
    """Excel y Sheets ejecutan lo que empieza con = + - @. Los nombres vienen del
    scraping y de webhooks publicos."""
    db = app.config["_DB"]
    insert_business(db, {"name": '=HYPERLINK("http://evil","click")',
                         "phone": "+59899000999"})
    cuerpo = cli.get("/api/leads/export.csv").get_data(as_text=True)
    assert '"=HYPERLINK' not in cuerpo
    assert "\"'=HYPERLINK" in cuerpo


def test_el_export_escapa_las_comillas(app, cli):
    db = app.config["_DB"]
    insert_business(db, {"name": 'Bar "El Rincon"', "phone": "+59899000998"})
    cuerpo = cli.get("/api/leads/export.csv").get_data(as_text=True)
    assert 'Bar ""El Rincon""' in cuerpo


def test_el_export_incluye_el_email(app, cli):
    """insert_business() no guardaba email; ahora que si, tiene que salir."""
    db = app.config["_DB"]
    insert_business(db, {"name": "Ana", "phone": "+59899000997", "email": "ana@x.com"})
    cuerpo = cli.get("/api/leads/export.csv").get_data(as_text=True)
    assert "ana@x.com" in cuerpo
