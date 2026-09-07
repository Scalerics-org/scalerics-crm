"""Ningun writer puede volver a dejar un lead fuera del tablero.

Las etapas del pipeline se renombraron (reunion_agendada -> demo_agendada,
reunion_hecha -> demo_1) y los datos existentes se migraron al arrancar. Pero
varios writers quedaron escribiendo los nombres viejos, y un lead en un estado
que ya no es etapa NO TIENE COLUMNA en el tablero de pre-clientes: el lead
existe en la base y desaparece de la vista. Agendar una reunion desde el CRM
hacia justo eso.

Por eso estos tests no comparan el string del estado: preguntan si el lead
aparece en el tablero, que es el efecto que a alguien le importa. Un rename
futuro los rompe solo si de verdad rompe la vista.
"""

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import (ETAPAS_PRECLIENTE, create_user, get_business, init_db,
                      insert_business, update_business)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    db = str(tmp_path / "cal.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


@pytest.fixture
def cli(app):
    uid = create_user(app.config["_DB"], name="Jefe", email="jefe@test.com",
                      phone="099", password_hash=generate_password_hash("x" * 10))
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Jefe"
    return c


_tel = iter(range(700000, 799999))


def _lead(db, nombre="Lead", etapa=None):
    lid = insert_business(db, {"name": nombre, "phone": f"+598{next(_tel)}"})
    if etapa:
        update_business(db, lid, crm_status=etapa)
    return lid


def _columna(cli, lead_id):
    """En que columna del tablero cae el lead. None = no aparece en ninguna."""
    tablero = cli.get("/api/preclientes").get_json()
    for etapa in tablero["etapas"]:
        if any(l["id"] == lead_id for l in etapa["leads"]):
            return etapa["key"]
    return None


# ── el bug: agendar dejaba al lead fuera del tablero ─────────────────────────

def test_agendar_una_reunion_deja_al_lead_en_el_tablero(app, cli):
    lid = _lead(app.config["_DB"], "Panaderia Rex")

    r = cli.post("/api/calendar/events", json={
        "client_id": lid, "title": "Demo", "date": "2026-10-01",
        "time": "15:00", "duration_min": 60,
    })

    assert r.get_json()["ok"] is True
    assert _columna(cli, lid) == "demo_agendada"


def test_una_llamada_que_termina_en_reunion_deja_al_lead_en_el_tablero(app, cli):
    lid = _lead(app.config["_DB"], "Ferreteria Sol")

    r = cli.post(f"/api/leads/{lid}/calls", json={"outcome": "reunion", "notes": ""})

    assert r.status_code == 201
    assert _columna(cli, lid) == "demo_agendada"


def test_el_timeline_del_lead_registra_una_etapa_que_existe(app, cli):
    """`lead_events` alimenta la linea de tiempo de la ficha. Si guarda un
    estado que ya no es etapa, la ficha muestra la clave cruda."""
    lid = _lead(app.config["_DB"], "Optica Luz")

    cli.post(f"/api/leads/{lid}/calls", json={"outcome": "reunion"})

    eventos = cli.get(f"/api/leads/{lid}/events").get_json()
    assert eventos, "la llamada con reunion tiene que dejar un evento"
    assert eventos[0]["new_status"] in ETAPAS_PRECLIENTE


def test_el_lead_que_nace_del_sync_de_google_cae_en_una_columna(app, monkeypatch, cli):
    """Calendly crea las reuniones en Google, y el sync inventa el lead que
    todavia no existe en el CRM. Nacia directo en un estado sin columna."""
    import routes.calendar as cal

    evento = {
        "id": "evt-google-1",
        "summary": "Demo con Vidrieria Norte",
        "start": {"dateTime": "2026-10-02T15:00:00-03:00"},
        "end": {"dateTime": "2026-10-02T16:00:00-03:00"},
        "attendees": [
            {"email": "yo@scalerics.com", "organizer": True},
            {"email": "cliente@vidrierianorte.com", "displayName": "Vidrieria Norte"},
        ],
    }

    class _Eventos:
        def list(self, **kw):
            return self

        def execute(self):
            return {"items": [evento]}

    class _Servicio:
        def events(self):
            return _Eventos()

    monkeypatch.setattr(cal, "_get_calendar_service", lambda: (_Servicio(), None))
    cal._sync_gcal_to_db(app.config["_DB"], "2026-10-01", "2026-10-03")

    tablero = cli.get("/api/preclientes").get_json()
    nombres = [l["name"] for e in tablero["etapas"] for l in e["leads"]]
    assert "Vidrieria Norte" in nombres


def test_cerrar_la_reunion_avanza_al_lead_a_la_primera_demo(app, monkeypatch, cli):
    """El unico lugar donde el sistema mueve un estado por su cuenta."""
    import json as _json

    lid = _lead(app.config["_DB"], "Taller Mecanico")
    cli.post("/api/calendar/events", json={
        "client_id": lid, "title": "Demo", "date": "2026-10-01",
        "time": "15:00", "duration_min": 60,
    })
    mid = cli.get(f"/api/calendar/clients/{lid}/meetings").get_json()[0]["id"]

    class _Bloque:
        text = _json.dumps({"summary": "Quiere una web", "requirements": "- landing",
                            "service_type": "web", "next_steps": ["mandar ppto"]})

    class _Mensajes:
        def create(self, **kw):
            return type("R", (), {"content": [_Bloque()]})()

    class _Anthropic:
        def __init__(self, **kw):
            self.messages = _Mensajes()

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", _Anthropic)

    r = cli.post(f"/api/calendar/meetings/{mid}/summarize",
                 json={"transcript": "hablamos de la web"})

    assert r.get_json()["ok"] is True
    assert _columna(cli, lid) == "demo_1"


# ── la vuelta atras: borrar la unica reunion ─────────────────────────────────

def test_borrar_la_unica_reunion_saca_al_lead_del_tablero(app, cli):
    """El revert comparaba contra el nombre viejo, asi que despues del rename
    no matcheaba nunca: el lead quedaba en 'demo agendada' sin ninguna reunion
    agendada."""
    lid = _lead(app.config["_DB"], "Kiosco Central")
    cli.post("/api/calendar/events", json={
        "client_id": lid, "title": "Demo", "date": "2026-10-01",
        "time": "15:00", "duration_min": 60,
    })
    mid = cli.get(f"/api/calendar/clients/{lid}/meetings").get_json()[0]["id"]

    cli.delete(f"/api/calendar/meetings/{mid}")

    assert get_business(app.config["_DB"], lid)["crm_status"] == "contactado"
    assert _columna(cli, lid) is None


def test_borrar_una_reunion_no_hace_retroceder_a_un_lead_mas_avanzado(app, cli):
    """Si ya se le mando el presupuesto, borrar una reunion vieja no puede
    devolverlo a 'contactado'."""
    lid = _lead(app.config["_DB"], "Estudio Contable")
    cli.post("/api/calendar/events", json={
        "client_id": lid, "title": "Demo", "date": "2026-10-01",
        "time": "15:00", "duration_min": 60,
    })
    mid = cli.get(f"/api/calendar/clients/{lid}/meetings").get_json()[0]["id"]
    update_business(app.config["_DB"], lid, crm_status="presupuesto_enviado")

    cli.delete(f"/api/calendar/meetings/{mid}")

    assert _columna(cli, lid) == "presupuesto_enviado"


# ── el borde: una pestaña vieja manda el nombre viejo ────────────────────────

def test_una_pestana_vieja_no_saca_al_lead_del_tablero(app, cli):
    """La API sigue aceptando el nombre viejo —alguien con el panel abierto
    desde antes del deploy lo manda— pero no lo guarda tal cual."""
    lid = _lead(app.config["_DB"], "Rotiseria Don Pepe")

    r = cli.post(f"/api/leads/{lid}/crm-status", json={"crm_status": "reunion_agendada"})

    assert r.get_json()["ok"] is True
    assert _columna(cli, lid) == "demo_agendada"


def test_una_pestana_vieja_tampoco_lo_saca_en_lote(app, cli):
    lid = _lead(app.config["_DB"], "Bar Aurora")

    r = cli.post("/api/leads/batch-status",
                 json={"ids": [lid], "crm_status": "reunion_hecha"})

    assert r.get_json()["ok"] is True
    assert _columna(cli, lid) == "demo_1"


def test_el_borde_no_toca_una_etapa_nueva(app, cli):
    """Idempotente: las etapas nuevas no estan en el mapa."""
    lid = _lead(app.config["_DB"], "Vinoteca Sur")

    cli.post(f"/api/leads/{lid}/crm-status", json={"crm_status": "follow_up_2"})

    assert _columna(cli, lid) == "follow_up_2"


def test_el_borde_no_toca_los_estados_de_la_cola(app, cli):
    """Solo se traducen los del pipeline. 'no_interesa' y compania son de otra
    etapa del embudo: no tienen columna y tienen que quedar como estan."""
    db = app.config["_DB"]
    for estado in ("sin_contactar", "interesado", "contactado",
                   "llamar_despues", "no_interesa"):
        lid = _lead(db, f"Lead {estado}")
        cli.post(f"/api/leads/{lid}/crm-status", json={"crm_status": estado})
        assert get_business(db, lid)["crm_status"] == estado


def test_update_business_guarda_lo_que_le_dan(app):
    """La traduccion vive en el borde, NO en la escritura.

    Traducir dentro de `update_business` rompe a cualquiera que haga
    leer-comparar-escribir: `services/planilla_semaforo.py` pedia 'negociacion',
    leia de vuelta 'follow_up_1', no coincidian y volvia a escribir en cada
    corrida. Este test es el que evita que alguien lo 'arregle' de nuevo asi.
    """
    db = app.config["_DB"]
    lid = _lead(db, "Kiosco Testigo")

    update_business(db, lid, crm_status="follow_up_1")

    assert get_business(db, lid)["crm_status"] == "follow_up_1"
