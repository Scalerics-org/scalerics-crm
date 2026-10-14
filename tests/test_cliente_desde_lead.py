"""Nuevo cliente eligiendo un lead que ya esta en el CRM (pedido de Juan, 14/10).

"Que en clientes cuando se agregue uno nuevo te lo deje seleccionar por leads
asi no cargo los datos a mano."
"""

import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import ETAPAS_CLIENTE, create_user, get_business, init_db, insert_business


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    db = str(tmp_path / "c.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


@pytest.fixture
def db(app):
    return app.config["_DB"]


@pytest.fixture
def cli(app, db):
    uid = create_user(db, name="jefe", email="jefe@test.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Jefe"
    return c


def _lead(db, nombre, phone, etapa="sin_contactar", **extra):
    lid = insert_business(db, {"name": nombre, "phone": phone, **extra})
    conn = sqlite3.connect(db)
    conn.execute("UPDATE businesses SET crm_status=? WHERE id=?", (etapa, lid))
    conn.commit()
    conn.close()
    return lid


def _cuantos(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0]
    finally:
        conn.close()


# ── buscar candidatos ────────────────────────────────────────────────────────

def test_busca_leads_por_nombre_telefono_y_mail_sin_tildes(cli, db):
    a = _lead(db, "Óptica Luz", "+59899111222", email="luz@optica.uy", city="Salto")
    _lead(db, "Taller Sur", "+59899333444")
    buscar = lambda q: [l["id"] for l in cli.get(f"/api/clientes-activos/candidatos?q={q}").get_json()["leads"]]
    assert buscar("optica") == [a]
    assert buscar("99111") == [a]
    assert buscar("luz@optica") == [a]
    assert buscar("luz salto") == [a]
    assert buscar("x") == []          # menos de 2 letras no busca


def test_no_ofrece_clientes_ni_archivados(cli, db):
    _lead(db, "Bloquera Cliente", "+59899000001", etapa="cerrado")
    archivado = _lead(db, "Bloquera Archivada", "+59899000002")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE businesses SET archivado_en='2026-10-01' WHERE id=?", (archivado,))
    conn.commit()
    conn.close()
    vivo = _lead(db, "Bloquera Lead", "+59899000003", etapa="demo_1")
    leads = cli.get("/api/clientes-activos/candidatos?q=bloquera").get_json()["leads"]
    assert [l["id"] for l in leads] == [vivo]


# ── pasar a cliente ──────────────────────────────────────────────────────────

def test_el_lead_pasa_a_cliente_sin_duplicarse(cli, db):
    lid = _lead(db, "Óptica Luz", "+59899111222", etapa="presupuesto_enviado")
    antes = _cuantos(db)
    r = cli.post("/api/clientes-activos", json={"lead_id": lid, "crm_status": "en_desarrollo",
                                                 "monto": "1500", "moneda": "USD",
                                                 "name": "esto se ignora"})
    assert r.status_code == 201, r.get_data(as_text=True)
    assert r.get_json()["id"] == lid
    assert _cuantos(db) == antes
    b = get_business(db, lid)
    assert (b["name"], b["crm_status"], b["monto_pagado"], b["moneda_pagado"]) == \
        ("Óptica Luz", "en_desarrollo", 1500.0, "USD")
    # Aparece en la lista de Clientes.
    ids = [c["id"] for c in cli.get("/api/clientes-activos").get_json()["clientes"]]
    assert lid in ids


def test_deja_el_mismo_rastro_que_cambiar_el_estado(cli, db):
    lid = _lead(db, "Óptica Luz", "+59899111222")
    cli.post("/api/clientes-activos", json={"lead_id": lid})
    conn = sqlite3.connect(db)
    evento = conn.execute("SELECT new_status, note, created_by FROM lead_events WHERE lead_id=?",
                          (lid,)).fetchone()
    conn.close()
    assert evento == (ETAPAS_CLIENTE[0], "Pasado a cliente desde Clientes", "Jefe")


def test_sin_monto_no_borra_el_que_tenia(cli, db):
    lid = _lead(db, "Óptica Luz", "+59899111222")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE businesses SET monto_pagado=800, moneda_pagado='UYU' WHERE id=?", (lid,))
    conn.commit()
    conn.close()
    assert cli.post("/api/clientes-activos", json={"lead_id": lid}).status_code == 201
    b = get_business(db, lid)
    assert (b["monto_pagado"], b["moneda_pagado"]) == (800.0, "UYU")


@pytest.mark.parametrize("cuerpo,status", [
    ({"lead_id": 999}, 404),
    ({"lead_id": "abc"}, 400),
    ({"lead_id": 1, "crm_status": "demo_1"}, 400),
    ({"lead_id": 1, "monto": "mucho"}, 400),
])
def test_pedidos_invalidos_no_tocan_nada(cli, db, cuerpo, status):
    lid = _lead(db, "Óptica Luz", "+59899111222")
    assert lid == 1
    assert cli.post("/api/clientes-activos", json=cuerpo).status_code == status
    assert get_business(db, lid)["crm_status"] == "sin_contactar"


def test_un_cliente_no_se_vuelve_a_dar_de_alta(cli, db):
    lid = _lead(db, "Óptica Luz", "+59899111222", etapa="finalizado")
    r = cli.post("/api/clientes-activos", json={"lead_id": lid, "crm_status": "cerrado"})
    assert r.status_code == 409
    assert get_business(db, lid)["crm_status"] == "finalizado"


def test_la_carga_a_mano_sigue_igual(cli, db):
    r = cli.post("/api/clientes-activos", json={"name": "Bloquera Norte", "phone": "099 1"})
    assert r.status_code == 201
    assert get_business(db, r.get_json()["id"])["name"] == "Bloquera Norte"
    assert cli.post("/api/clientes-activos", json={"name": ""}).status_code == 400


def test_el_modal_tiene_el_buscador():
    html = dashboard.DASHBOARD_HTML
    assert 'id="cli-nuevo-buscar"' in html and "cliNuevoBuscar(this.value)" in html
    assert "/api/clientes-activos/candidatos?q=" in html
    assert "lead_id: lead.id" in html
