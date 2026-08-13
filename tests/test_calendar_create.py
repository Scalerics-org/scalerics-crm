"""Tests de creacion de reuniones desde el CRM.

El POST guardaba SOLO en la base y devolvia event_id: None. La reunion no existia
en Google, asi que no llegaba invitacion, no habia recordatorio ni Meet, y no
aparecia en la agenda real del equipo: habia que cargarla a mano por segunda vez.
"""

from unittest.mock import MagicMock, patch

import pytest

import dashboard
from database import _connect, init_db, insert_business


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ALLOW_INSECURE_DEV_KEY", "1")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    db = str(tmp_path / "cal.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


@pytest.fixture
def cliente_admin(app):
    from werkzeug.security import generate_password_hash
    from database import create_user
    uid = create_user(app.config["_DB"], name="Jefe", email="jefe@test.com",
                      phone="099", password_hash=generate_password_hash("x" * 10))
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Jefe"
    return c


def _service_mock(event_id="gcal-123", meet="https://meet.google.com/abc-defg-hij"):
    service = MagicMock()
    service.events.return_value.insert.return_value.execute.return_value = {
        "id": event_id,
        "conferenceData": {"entryPoints": [
            {"entryPointType": "more", "uri": "https://x"},
            {"entryPointType": "video", "uri": meet},
        ]},
    }
    return service


def _lead(db, email="ana@cliente.com"):
    return insert_business(db, {"name": "Ana", "phone": "+59899111222", "email": email})


def _meeting(db, mid):
    conn = _connect(db)
    try:
        r = conn.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def test_crear_reunion_la_publica_en_google(app, cliente_admin):
    db = app.config["_DB"]
    lid = _lead(db)
    service = _service_mock()
    with patch("routes.calendar._get_calendar_service", return_value=(service, None)):
        r = cliente_admin.post("/api/calendar/events", json={
            "title": "Reunión con Ana", "date": "2026-09-10", "time": "15:00",
            "duration_min": 45, "client_id": lid,
        })
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    assert d["google_ok"] is True
    assert d["event_id"] == "gcal-123"
    assert d["meet_url"] == "https://meet.google.com/abc-defg-hij"

    # el id de Google queda guardado, si no el sync la duplicaria
    fila = _meeting(db, d["meeting_id"])
    assert fila["calendar_event_id"] == "gcal-123"
    assert fila["meet_link"] == "https://meet.google.com/abc-defg-hij"


def test_manda_la_hora_con_zona_de_uruguay(app, cliente_admin):
    """El resto del modulo guarda naive local. Sin localizar, Google interpreta la
    hora como UTC y el evento cae 3 horas corrido."""
    db = app.config["_DB"]
    lid = _lead(db)
    service = _service_mock()
    with patch("routes.calendar._get_calendar_service", return_value=(service, None)):
        cliente_admin.post("/api/calendar/events", json={
            "title": "T", "date": "2026-09-10", "time": "15:00", "client_id": lid,
        })
    cuerpo = service.events.return_value.insert.call_args.kwargs["body"]
    assert cuerpo["start"]["timeZone"] == "America/Montevideo"
    assert cuerpo["start"]["dateTime"].startswith("2026-09-10T15:00:00-03:00")
    assert cuerpo["end"]["dateTime"].startswith("2026-09-10T16:00:00-03:00")


def test_invita_al_cliente_si_tiene_email(app, cliente_admin):
    db = app.config["_DB"]
    lid = _lead(db, email="ana@cliente.com")
    service = _service_mock()
    with patch("routes.calendar._get_calendar_service", return_value=(service, None)):
        cliente_admin.post("/api/calendar/events", json={
            "title": "T", "date": "2026-09-10", "time": "10:00", "client_id": lid,
        })
    kwargs = service.events.return_value.insert.call_args.kwargs
    assert kwargs["body"]["attendees"] == [{"email": "ana@cliente.com"}]
    assert kwargs["sendUpdates"] == "all"


def test_avisa_cuando_el_cliente_no_tiene_email(app, cliente_admin):
    db = app.config["_DB"]
    lid = _lead(db, email="")
    service = _service_mock()
    with patch("routes.calendar._get_calendar_service", return_value=(service, None)):
        r = cliente_admin.post("/api/calendar/events", json={
            "title": "T", "date": "2026-09-10", "time": "10:00", "client_id": lid,
        })
    d = r.get_json()
    assert d["ok"] is True
    assert "no tiene email" in d["aviso"]
    kwargs = service.events.return_value.insert.call_args.kwargs
    assert "attendees" not in kwargs["body"]
    assert kwargs["sendUpdates"] == "none"


def test_si_google_falla_la_reunion_igual_se_guarda_y_se_avisa(app, cliente_admin):
    """No se puede perder la reunion por un problema de Google, ni mentir con un
    ok:true limpio como si se hubiera publicado."""
    db = app.config["_DB"]
    lid = _lead(db)
    service = MagicMock()
    service.events.return_value.insert.return_value.execute.side_effect = RuntimeError("403 quota")
    with patch("routes.calendar._get_calendar_service", return_value=(service, None)):
        r = cliente_admin.post("/api/calendar/events", json={
            "title": "T", "date": "2026-09-10", "time": "10:00", "client_id": lid,
        })
    d = r.get_json()
    assert d["ok"] is True
    assert d["google_ok"] is False
    assert d["event_id"] is None
    assert "Google" in d["aviso"]
    assert _meeting(db, d["meeting_id"]) is not None


def test_sin_credenciales_de_google_tampoco_pierde_la_reunion(app, cliente_admin):
    db = app.config["_DB"]
    lid = _lead(db)
    with patch("routes.calendar._get_calendar_service",
               return_value=(None, "Faltan GCAL_CLIENT_ID")):
        r = cliente_admin.post("/api/calendar/events", json={
            "title": "T", "date": "2026-09-10", "time": "10:00", "client_id": lid,
        })
    d = r.get_json()
    assert d["ok"] is True and d["google_ok"] is False
    assert "GCAL_CLIENT_ID" in d["aviso"]
    assert _meeting(db, d["meeting_id"]) is not None


def test_sin_cliente_devuelve_400(app, cliente_admin):
    r = cliente_admin.post("/api/calendar/events", json={
        "title": "T", "date": "2026-09-10", "time": "10:00",
    })
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_no_pide_meet_si_ya_hay_link(app, cliente_admin):
    db = app.config["_DB"]
    lid = _lead(db)
    service = _service_mock()
    with patch("routes.calendar._get_calendar_service", return_value=(service, None)):
        r = cliente_admin.post("/api/calendar/events", json={
            "title": "T", "date": "2026-09-10", "time": "10:00", "client_id": lid,
            "meet_link": "https://zoom.us/j/123",
        })
    kwargs = service.events.return_value.insert.call_args.kwargs
    assert "conferenceData" not in kwargs["body"]
    assert kwargs["conferenceDataVersion"] == 0
    assert r.get_json()["meet_url"] == "https://zoom.us/j/123"
