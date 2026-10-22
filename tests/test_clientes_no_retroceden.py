"""Un cliente no se va de Clientes porque el sistema hizo algo solo.

El 21/10 Clientes paso de 13 a 11: a dos clientes les agendaron una reunion
desde el calendario del CRM, y agendar los paso a "demo agendada". Con un cliente hay reuniones y llamadas todo el tiempo.

Estos tests preguntan si el cliente sigue apareciendo en Clientes, que es lo
que a alguien le importa, y que el resto (la reunion, el llamado) igual quede.
"""

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import (ETAPAS_CLIENTE, create_user, get_business, get_meetings_for_client,
                      init_db, insert_business, update_business)
from services.calendly_gcal import sync_events
from tests.test_calendly_gcal import HOST, TEAM, _event


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    monkeypatch.setenv("ADMIN_TOKEN", "token-de-prueba")
    db = str(tmp_path / "cli.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


@pytest.fixture
def db(app):
    return app.config["_DB"]


@pytest.fixture
def cli(app, db):
    uid = create_user(db, name="Jefe", email="jefe@test.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Jefe"
    return c


def _cliente(db, etapa="cerrado", **extra):
    datos = {"name": "Estudio Contable", "phone": "+59899555111", **extra}
    bid = insert_business(db, datos)
    update_business(db, bid, crm_status=etapa)
    return bid


def _en_clientes(cli, bid):
    return any(c["id"] == bid for c in cli.get("/api/clientes-activos").get_json()["clientes"])


@pytest.mark.parametrize("etapa", ETAPAS_CLIENTE)
def test_agendar_una_reunion_no_saca_al_cliente(cli, db, etapa):
    bid = _cliente(db, etapa)

    r = cli.post("/api/calendar/events", json={
        "client_id": bid, "title": "Reunion de seguimiento", "date": "2026-10-23",
        "time": "15:00", "duration_min": 60,
    })

    assert r.get_json()["ok"] is True
    assert get_business(db, bid)["crm_status"] == etapa
    assert _en_clientes(cli, bid)
    assert len(get_meetings_for_client(db, bid)) == 1   # la reunion igual queda


def test_una_llamada_con_reunion_no_saca_al_cliente(cli, db):
    bid = _cliente(db)

    r = cli.post(f"/api/leads/{bid}/calls", json={"outcome": "reunion", "notes": ""})

    assert r.status_code == 201
    assert get_business(db, bid)["crm_status"] == "cerrado"
    assert _en_clientes(cli, bid)


@pytest.mark.parametrize("outcome", ["llamar_despues", "interesado"])
def test_agendar_un_llamado_no_saca_al_cliente_pero_guarda_la_fecha(cli, db, outcome):
    bid = _cliente(db, "en_desarrollo")

    r = cli.post(f"/api/leads/{bid}/callback",
                 json={"callback_date": "2026-10-28", "outcome": outcome})

    assert r.get_json()["ok"] is True
    negocio = get_business(db, bid)
    assert negocio["crm_status"] == "en_desarrollo"
    assert negocio["callback_date"] == "2026-10-28"


def test_una_reunion_futura_de_calendly_no_saca_al_cliente(cli, db):
    bid = _cliente(db, email="cliente@ferreteriasol.com.uy")

    sync_events(db, [_event(guest="cliente@ferreteriasol.com.uy")],
                host_email=HOST, team_emails=TEAM, now="2026-08-12T00:00:00")

    assert get_business(db, bid)["crm_status"] == "cerrado"
    assert len(get_meetings_for_client(db, bid)) == 1


@pytest.mark.parametrize("estado_bot", ["MEETING_SENT", "HUMAN_QUEUED"])
def test_si_el_cliente_le_escribe_al_bot_sigue_siendo_cliente(cli, db, estado_bot):
    bid = _cliente(db, "finalizado")

    r = cli.post("/api/bot/lead-qualified", headers={"x-admin-token": "token-de-prueba"},
                 json={"phone": "+59899555111", "state": estado_bot})

    assert r.status_code == 200
    assert get_business(db, bid)["crm_status"] == "finalizado"
    assert _en_clientes(cli, bid)


def test_a_un_lead_que_no_es_cliente_se_le_sigue_moviendo_la_etapa(cli, db):
    """El freno es solo para clientes: el pipeline sigue igual."""
    bid = _cliente(db, "interesado")

    cli.post("/api/calendar/events", json={
        "client_id": bid, "title": "Demo", "date": "2026-10-23",
        "time": "15:00", "duration_min": 60,
    })

    assert get_business(db, bid)["crm_status"] == "demo_agendada"
