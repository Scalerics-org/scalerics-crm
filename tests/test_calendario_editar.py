"""Lo que se le agrega a reprogramar: renombrar, cambiar cuánto dura, y Calendly.

`tests/test_calendar_reprogramar.py` cubre mover una reunión de fecha y hora.
Acá va lo que se monta encima:

  renombrar        el modal edita el título junto con el horario, y el nombre
                   nuevo también viaja a Google (el invitado ve el evento
                   renombrado, no uno viejo con hora nueva)
  duración         cambiarla desde el modal, no solo conservarla
  Calendly         `routes/calendly.py` guarda la URI de Calendly en
                   `calendar_event_id`, que NO es un eventId de Google. Sin
                   chequearlo, el patch a Google falla siempre y el usuario lee
                   "Google Calendar rechazó el cambio" en vez de enterarse de
                   que tiene que reprogramarla en Calendly.
  origen           el GET dice de dónde vino cada reunión, que es lo que la
                   vista usa para no ofrecer un botón que va a fallar

Solapar dos reuniones sigue siendo válido y no se bloquea: pasa seguido con
Calendly y ya hubo que arreglar una vez que estuviera prohibido.
"""

from unittest.mock import MagicMock

import pytest

import dashboard
from database import create_meeting, get_meeting, init_db, insert_business


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "token-de-test")
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    app = dashboard.create_app(ruta)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def cliente(app):
    return app.test_client()


_AUTH = {"x-admin-token": "token-de-test"}


def _reunion(db, *, calendar_event_id=None, titulo="Consultoría — El Sol",
             start="2026-09-09T15:00:00", end="2026-09-09T15:45:00"):
    """Una reunión de 45 minutos el miércoles 9 a las 15:00."""
    client_id = insert_business(db, {"name": "Ferretería El Sol"})
    meeting_id = create_meeting(
        db, client_id, title=titulo, start_at=start, end_at=end,
        calendar_event_id=calendar_event_id, status="scheduled",
    )
    return client_id, meeting_id


def _servicio_falso():
    service = MagicMock()
    service.events.return_value.patch.return_value.execute.return_value = {}
    return service


# ── renombrar ────────────────────────────────────────────────────────────────

def test_se_puede_renombrar_al_reprogramar(app, cliente):
    db = app.config["DB_PATH"]
    _, meeting_id = _reunion(db)

    r = cliente.patch(f"/api/calendar/meetings/{meeting_id}",
                      json={"date": "2026-09-10", "time": "16:30",
                            "title": "Cierre — El Sol"},
                      headers=_AUTH)

    assert r.get_json()["ok"] is True
    m = get_meeting(db, meeting_id)
    assert m["title"] == "Cierre — El Sol"
    assert m["start_at"] == "2026-09-10T16:30:00"


def test_un_titulo_vacio_no_borra_el_que_estaba(app, cliente):
    """El modal manda el campo siempre; vaciarlo sin querer dejaría la reunión
    sin nombre en el calendario del invitado."""
    db = app.config["DB_PATH"]
    _, meeting_id = _reunion(db)

    cliente.patch(f"/api/calendar/meetings/{meeting_id}",
                  json={"date": "2026-09-10", "time": "16:30", "title": "   "},
                  headers=_AUTH)

    assert get_meeting(db, meeting_id)["title"] == "Consultoría — El Sol"


def test_el_nombre_nuevo_tambien_viaja_a_google(app, cliente, monkeypatch):
    """Si no, el invitado ve el evento viejo movido de hora."""
    import routes.calendar as cal
    db = app.config["DB_PATH"]
    _, meeting_id = _reunion(db, calendar_event_id="evt-google-1")
    service = _servicio_falso()
    monkeypatch.setattr(cal, "_get_calendar_service", lambda: (service, None))

    cliente.patch(f"/api/calendar/meetings/{meeting_id}",
                  json={"date": "2026-09-10", "time": "16:30",
                        "title": "Cierre — El Sol"},
                  headers=_AUTH)

    cuerpo = service.events.return_value.patch.call_args.kwargs["body"]
    assert cuerpo["summary"] == "Cierre — El Sol"


# ── duración ─────────────────────────────────────────────────────────────────

def test_se_puede_cambiar_cuanto_dura(app, cliente):
    db = app.config["DB_PATH"]
    _, meeting_id = _reunion(db)

    r = cliente.patch(f"/api/calendar/meetings/{meeting_id}",
                      json={"date": "2026-09-10", "time": "16:30",
                            "duration_min": 90},
                      headers=_AUTH)

    assert r.get_json()["ok"] is True
    m = get_meeting(db, meeting_id)
    assert m["start_at"] == "2026-09-10T16:30:00"
    assert m["end_at"] == "2026-09-10T18:00:00"


def test_sin_duracion_se_conserva_la_que_tenia(app, cliente):
    """No romper lo que ya hacía: mover sin tocar la duración la mantiene."""
    db = app.config["DB_PATH"]
    _, meeting_id = _reunion(db)

    cliente.patch(f"/api/calendar/meetings/{meeting_id}",
                  json={"date": "2026-09-10", "time": "16:30"}, headers=_AUTH)

    m = get_meeting(db, meeting_id)
    assert m["end_at"] == "2026-09-10T17:15:00"   # los 45 minutos siguen


@pytest.mark.parametrize("valor", ["cero", 0, -30])
def test_una_duracion_invalida_no_toca_la_reunion(app, cliente, valor):
    db = app.config["DB_PATH"]
    _, meeting_id = _reunion(db)

    r = cliente.patch(f"/api/calendar/meetings/{meeting_id}",
                      json={"date": "2026-09-10", "time": "16:30",
                            "duration_min": valor},
                      headers=_AUTH)

    assert r.status_code == 400
    assert get_meeting(db, meeting_id)["start_at"] == "2026-09-09T15:00:00"


# ── Calendly ─────────────────────────────────────────────────────────────────

def test_una_reunion_de_calendly_se_bloquea_con_un_mensaje_util(app, cliente, monkeypatch):
    import routes.calendar as cal
    db = app.config["DB_PATH"]
    _, meeting_id = _reunion(
        db, calendar_event_id="https://api.calendly.com/scheduled_events/AAA")
    service = _servicio_falso()
    monkeypatch.setattr(cal, "_get_calendar_service", lambda: (service, None))

    r = cliente.patch(f"/api/calendar/meetings/{meeting_id}",
                      json={"date": "2026-09-10", "time": "16:30"}, headers=_AUTH)

    d = r.get_json()
    assert d["ok"] is False
    assert "Calendly" in d["error"]
    service.events.return_value.patch.assert_not_called()
    assert get_meeting(db, meeting_id)["start_at"] == "2026-09-09T15:00:00"


# ── el origen viaja en el payload ────────────────────────────────────────────

@pytest.mark.parametrize("event_id,esperado", [
    (None, "crm"),
    ("evt-google-1", "google"),
    ("https://api.calendly.com/scheduled_events/AAA", "calendly"),
])
def test_el_listado_dice_de_donde_vino_cada_reunion(app, cliente, event_id, esperado):
    db = app.config["DB_PATH"]
    _reunion(db, calendar_event_id=event_id)

    eventos = cliente.get(
        "/api/calendar/events?start=2026-09-01&end=2026-09-30",
        headers=_AUTH).get_json()["events"]

    assert [e["origen"] for e in eventos] == [esperado]


def test_el_listado_dice_cuanto_dura_cada_reunion(app, cliente):
    """El modal lo necesita para proponer la duración que ya tenía."""
    db = app.config["DB_PATH"]
    _reunion(db)

    eventos = cliente.get(
        "/api/calendar/events?start=2026-09-01&end=2026-09-30",
        headers=_AUTH).get_json()["events"]

    assert eventos[0]["duration_min"] == 45


# ── solapar sigue permitido ──────────────────────────────────────────────────

def test_dos_reuniones_en_el_mismo_horario_no_se_bloquean(app, cliente):
    """Ya se arregló una vez que el dedup del sync lo impidiera; el editor no
    lo reintroduce."""
    db = app.config["DB_PATH"]
    _, primera = _reunion(db, start="2026-09-09T15:00:00", end="2026-09-09T16:00:00")
    _, segunda = _reunion(db, start="2026-09-11T11:00:00", end="2026-09-11T12:00:00")

    r = cliente.patch(f"/api/calendar/meetings/{segunda}",
                      json={"date": "2026-09-09", "time": "15:00"}, headers=_AUTH)

    assert r.get_json()["ok"] is True
    assert get_meeting(db, primera)["start_at"] == "2026-09-09T15:00:00"
    assert get_meeting(db, segunda)["start_at"] == "2026-09-09T15:00:00"
