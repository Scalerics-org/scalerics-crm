"""Reprogramar una reunion arrastrandola en el calendario.

El front manda un PATCH con la fecha y la hora nuevas; el back tiene que
mover el evento en Google Calendar *antes* de tocar la base. El orden importa:
si Google falla y la base ya se movio, el CRM muestra una hora y el cliente
tiene otra en su calendario, que es justo lo que hace que alguien se conecte
solo a la hora vieja.
"""

from unittest.mock import MagicMock, patch

import pytest

import dashboard
from database import create_meeting, get_activity_feed, get_meeting, init_db, insert_business


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


def _reunion(db, *, calendar_event_id=None, start="2026-09-09T15:00:00",
             end="2026-09-09T15:45:00"):
    """Una reunion de 45 minutos el miercoles 9 a las 15:00."""
    client_id = insert_business(db, {"name": "Ferreteria El Sol"})
    meeting_id = create_meeting(
        db, client_id,
        title="Consultoria — Ferreteria El Sol",
        start_at=start,
        end_at=end,
        calendar_event_id=calendar_event_id,
        status="scheduled",
    )
    return client_id, meeting_id


def _servicio_falso():
    """Imita el objeto de googleapiclient: service.events().patch(...).execute()."""
    service = MagicMock()
    service.events.return_value.patch.return_value.execute.return_value = {}
    return service


# ─── mover ────────────────────────────────────────────────────────────────────

def test_mover_conserva_la_duracion(app, cliente):
    """Una reunion de 45 minutos sigue siendo de 45 minutos en el horario nuevo."""
    db = app.config["DB_PATH"]
    _, meeting_id = _reunion(db)

    r = cliente.patch(f"/api/calendar/meetings/{meeting_id}",
                      json={"date": "2026-09-10", "time": "16:30"}, headers=_AUTH)

    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    m = get_meeting(db, meeting_id)
    assert m["start_at"] == "2026-09-10T16:30:00"
    assert m["end_at"] == "2026-09-10T17:15:00"


def test_sin_evento_en_google_se_mueve_igual(app, cliente):
    """Las reuniones cargadas a mano no tienen calendar_event_id."""
    db = app.config["DB_PATH"]
    _, meeting_id = _reunion(db, calendar_event_id=None)

    with patch("routes.calendar._get_calendar_service") as get_service:
        r = cliente.patch(f"/api/calendar/meetings/{meeting_id}",
                          json={"date": "2026-09-11", "time": "09:00"}, headers=_AUTH)

    assert r.get_json()["ok"] is True
    get_service.assert_not_called()
    assert get_meeting(db, meeting_id)["start_at"] == "2026-09-11T09:00:00"


def test_queda_registrado_quien_la_movio(app, cliente):
    db = app.config["DB_PATH"]
    _, meeting_id = _reunion(db)

    cliente.patch(f"/api/calendar/meetings/{meeting_id}",
                  json={"date": "2026-09-10", "time": "16:30"}, headers=_AUTH)

    acciones = [a["action"] for a in get_activity_feed(db)]
    assert "meeting_rescheduled" in acciones


# ─── Google Calendar ──────────────────────────────────────────────────────────

def test_le_avisa_al_invitado_por_mail(app, cliente):
    """sendUpdates='all' es lo que hace que Google mande el mail de reprogramada."""
    db = app.config["DB_PATH"]
    _, meeting_id = _reunion(db, calendar_event_id="evento-de-google-1")
    service = _servicio_falso()

    with patch("routes.calendar._get_calendar_service", return_value=(service, None)):
        cliente.patch(f"/api/calendar/meetings/{meeting_id}",
                      json={"date": "2026-09-10", "time": "16:30"}, headers=_AUTH)

    kwargs = service.events.return_value.patch.call_args.kwargs
    assert kwargs["eventId"] == "evento-de-google-1"
    assert kwargs["sendUpdates"] == "all"


def test_manda_el_horario_nuevo_con_la_zona_de_montevideo(app, cliente):
    """Sin timeZone, Google interpreta el horario en UTC y la corre tres horas."""
    db = app.config["DB_PATH"]
    _, meeting_id = _reunion(db, calendar_event_id="evento-de-google-2")
    service = _servicio_falso()

    with patch("routes.calendar._get_calendar_service", return_value=(service, None)):
        cliente.patch(f"/api/calendar/meetings/{meeting_id}",
                      json={"date": "2026-09-10", "time": "16:30"}, headers=_AUTH)

    body = service.events.return_value.patch.call_args.kwargs["body"]
    assert body["start"] == {"dateTime": "2026-09-10T16:30:00",
                             "timeZone": "America/Montevideo"}
    assert body["end"] == {"dateTime": "2026-09-10T17:15:00",
                           "timeZone": "America/Montevideo"}


def test_si_google_falla_no_se_mueve_en_la_base(app, cliente):
    """Preferimos no mover nada antes que dejar el CRM y el calendario distintos."""
    db = app.config["DB_PATH"]
    _, meeting_id = _reunion(db, calendar_event_id="evento-de-google-3")
    service = _servicio_falso()
    service.events.return_value.patch.return_value.execute.side_effect = RuntimeError("500")

    with patch("routes.calendar._get_calendar_service", return_value=(service, None)):
        r = cliente.patch(f"/api/calendar/meetings/{meeting_id}",
                          json={"date": "2026-09-10", "time": "16:30"}, headers=_AUTH)

    assert r.get_json()["ok"] is False
    assert get_meeting(db, meeting_id)["start_at"] == "2026-09-09T15:00:00"


def test_si_no_hay_credenciales_de_google_no_se_mueve(app, cliente):
    """Mismo criterio: la reunion tiene evento en Google y no lo podemos tocar."""
    db = app.config["DB_PATH"]
    _, meeting_id = _reunion(db, calendar_event_id="evento-de-google-4")

    with patch("routes.calendar._get_calendar_service",
               return_value=(None, "Faltan GCAL_CLIENT_ID...")):
        r = cliente.patch(f"/api/calendar/meetings/{meeting_id}",
                          json={"date": "2026-09-10", "time": "16:30"}, headers=_AUTH)

    assert r.get_json()["ok"] is False
    assert get_meeting(db, meeting_id)["start_at"] == "2026-09-09T15:00:00"


# ─── entradas invalidas ───────────────────────────────────────────────────────

def test_reunion_inexistente_da_404(app, cliente):
    r = cliente.patch("/api/calendar/meetings/9999",
                      json={"date": "2026-09-10", "time": "16:30"}, headers=_AUTH)
    assert r.status_code == 404
    assert r.get_json()["ok"] is False


def test_sin_hora_no_toca_nada(app, cliente):
    db = app.config["DB_PATH"]
    _, meeting_id = _reunion(db)

    r = cliente.patch(f"/api/calendar/meetings/{meeting_id}",
                      json={"date": "2026-09-10"}, headers=_AUTH)

    assert r.status_code == 400
    assert r.get_json()["ok"] is False
    assert get_meeting(db, meeting_id)["start_at"] == "2026-09-09T15:00:00"


def test_hora_con_formato_raro_no_toca_nada(app, cliente):
    db = app.config["DB_PATH"]
    _, meeting_id = _reunion(db)

    r = cliente.patch(f"/api/calendar/meetings/{meeting_id}",
                      json={"date": "2026-09-10", "time": "las cuatro"}, headers=_AUTH)

    assert r.status_code == 400
    assert get_meeting(db, meeting_id)["start_at"] == "2026-09-09T15:00:00"


def test_reunion_sin_fin_toma_una_hora(app, cliente):
    """Los eventos importados a veces vienen sin end_at."""
    db = app.config["DB_PATH"]
    _, meeting_id = _reunion(db, end=None)

    cliente.patch(f"/api/calendar/meetings/{meeting_id}",
                  json={"date": "2026-09-10", "time": "16:30"}, headers=_AUTH)

    m = get_meeting(db, meeting_id)
    assert m["start_at"] == "2026-09-10T16:30:00"
    assert m["end_at"] == "2026-09-10T17:30:00"
