"""Varias reuniones en el mismo horario.

Agendar dos reuniones a la misma hora es valido y pasa seguido con Calendly. El
dedup del sync existe SOLO para unir la doble entrada (el webhook de Calendly crea
la reunion guardando su URI, y despues el sync de Google ve el mismo evento con el
id real de Google), no para impedir el solapamiento.

Casos que estaban rotos:
  - el mismo cliente no podia tener dos reuniones a la misma hora
  - si un cliente cancelaba y reagendaba el MISMO horario, la nueva no entraba
    nunca, porque el dedup contaba tambien las canceladas. Ese era el "a veces".
"""

from unittest.mock import MagicMock, patch

import pytest

from database import _connect, create_meeting, init_db, insert_business
from routes.calendar import _sync_gcal_to_db


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "s.db")
    init_db(p)
    return p


def _evento(gid, titulo, inicio, fin, email):
    return {"id": gid, "status": "confirmed", "summary": titulo,
            "start": {"dateTime": inicio}, "end": {"dateTime": fin},
            "attendees": [{"email": email, "displayName": email.split("@")[0]}]}


def _sync(db, eventos):
    svc = MagicMock()
    svc.events.return_value.list.return_value.execute.return_value = {"items": eventos}
    with patch("routes.calendar._get_calendar_service", return_value=(svc, None)):
        _sync_gcal_to_db(db, "2026-09-01", "2026-09-30")


def _reuniones(db, dia=None):
    conn = _connect(db)
    try:
        q = "SELECT * FROM meetings"
        p = ()
        if dia:
            q += " WHERE start_at LIKE ?"
            p = (f"{dia}%",)
        return [dict(r) for r in conn.execute(q + " ORDER BY id", p)]
    finally:
        conn.close()


def test_dos_clientes_distintos_en_el_mismo_horario(db):
    _sync(db, [
        _evento("g1", "Ana",  "2026-09-10T10:00:00-03:00", "2026-09-10T10:30:00-03:00", "ana@x.com"),
        _evento("g2", "Beto", "2026-09-10T10:00:00-03:00", "2026-09-10T10:30:00-03:00", "beto@x.com"),
    ])
    assert len(_reuniones(db, "2026-09-10")) == 2


def test_el_mismo_cliente_puede_tener_dos_reuniones_a_la_misma_hora(db):
    _sync(db, [
        _evento("g1", "Sesión 1", "2026-09-11T15:00:00-03:00", "2026-09-11T15:30:00-03:00", "ana@x.com"),
        _evento("g2", "Sesión 2", "2026-09-11T15:00:00-03:00", "2026-09-11T15:30:00-03:00", "ana@x.com"),
    ])
    assert len(_reuniones(db, "2026-09-11")) == 2


def test_horarios_parcialmente_solapados(db):
    _sync(db, [
        _evento("g1", "Carlos", "2026-09-12T10:00:00-03:00", "2026-09-12T11:00:00-03:00", "carlos@x.com"),
        _evento("g2", "Diana",  "2026-09-12T10:15:00-03:00", "2026-09-12T10:45:00-03:00", "diana@x.com"),
    ])
    assert len(_reuniones(db, "2026-09-12")) == 2


def test_cancelar_y_reagendar_el_mismo_horario(db):
    """El dedup contaba las canceladas, asi que la reagendada no entraba nunca."""
    _sync(db, [_evento("g1", "Ana", "2026-09-13T09:00:00-03:00",
                       "2026-09-13T09:30:00-03:00", "ana@x.com")])
    conn = _connect(db)
    conn.execute("UPDATE meetings SET status='canceled'")
    conn.commit()
    conn.close()

    _sync(db, [_evento("g2", "Ana reagenda", "2026-09-13T09:00:00-03:00",
                       "2026-09-13T09:30:00-03:00", "ana@x.com")])
    activas = [m for m in _reuniones(db, "2026-09-13") if m["status"] != "canceled"]
    assert len(activas) == 1
    assert activas[0]["title"] == "Ana reagenda"


# ── que el dedup siga cumpliendo su unica funcion ────────────────────────────

def test_no_duplica_la_reunion_creada_por_el_webhook_de_calendly(db):
    """El webhook guarda la URI de Calendly; despues el sync ve el MISMO evento con
    el id de Google. Tiene que quedar UNA sola fila, no dos."""
    lead = insert_business(db, {"name": "Ana", "phone": "+59899000001", "email": "ana@x.com"})
    create_meeting(db, lead, title="Reunión con Ana",
                   start_at="2026-09-14T11:00:00", end_at="2026-09-14T11:30:00",
                   calendar_event_id="https://api.calendly.com/scheduled_events/abc",
                   status="scheduled")

    _sync(db, [_evento("gcal-real", "Reunión con Ana", "2026-09-14T11:00:00-03:00",
                       "2026-09-14T11:30:00-03:00", "ana@x.com")])

    filas = _reuniones(db, "2026-09-14")
    assert len(filas) == 1, "se duplico la reunion de Calendly"
    # y queda enlazada con el id REAL de Google: es lo que permite cancelarla
    # despues desde el CRM (con la URI de Calendly, el delete fallaba siempre)
    assert filas[0]["calendar_event_id"] == "gcal-real"


def test_el_mismo_evento_de_google_no_se_duplica_entre_syncs(db):
    ev = _evento("g1", "Ana", "2026-09-15T10:00:00-03:00", "2026-09-15T10:30:00-03:00", "ana@x.com")
    _sync(db, [ev])
    _sync(db, [ev])
    _sync(db, [ev])
    assert len(_reuniones(db, "2026-09-15")) == 1
