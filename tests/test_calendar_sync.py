"""Tests del sync de Google Calendar.

Es el unico modulo en produccion y no tenia ninguna cobertura. Cada test de aca
cubre un bug concreto que estaba vivo.
"""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from database import _connect, init_db
from routes.calendar import _sync_gcal_to_db


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "cal.db")
    init_db(p)
    return p


def _evento(gcal_id, summary, inicio, fin, asistentes=None):
    """Un item tal como lo devuelve la API de Google Calendar."""
    ev = {
        "id": gcal_id,
        "status": "confirmed",
        "summary": summary,
        "start": {"dateTime": inicio},
        "end": {"dateTime": fin},
    }
    if asistentes is not None:
        ev["attendees"] = asistentes
    return ev


def _sync(db_path, eventos):
    """Corre el sync con la API de Google mockeada."""
    service = MagicMock()
    service.events.return_value.list.return_value.execute.return_value = {"items": eventos}
    with patch("routes.calendar._get_calendar_service", return_value=(service, None)):
        _sync_gcal_to_db(db_path, "2026-08-01", "2026-08-31")


def _filas(db_path, tabla):
    conn = _connect(db_path)
    try:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {tabla} ORDER BY id")]
    finally:
        conn.close()


# ── el dedup por hora de reloj ───────────────────────────────────────────────

def test_importa_dos_reuniones_en_la_misma_hora(db):
    """El dedup comparaba SUBSTR(start_at,1,13) global: con los slots de 30 min de
    Calendly, la reunion de las 10:30 no entraba nunca porque ya habia una a las 10."""
    _sync(db, [
        _evento("g1", "Reunión con Ana", "2026-08-12T10:00:00-03:00", "2026-08-12T10:30:00-03:00",
                [{"email": "ana@cliente.com", "displayName": "Ana"}]),
        _evento("g2", "Reunión con Beto", "2026-08-12T10:30:00-03:00", "2026-08-12T11:00:00-03:00",
                [{"email": "beto@cliente.com", "displayName": "Beto"}]),
    ])
    reuniones = _filas(db, "meetings")
    assert len(reuniones) == 2, "se perdio una reunion por el dedup de hora"
    assert {r["title"] for r in reuniones} == {"Reunión con Ana", "Reunión con Beto"}


def test_no_duplica_el_mismo_evento_entre_syncs(db):
    ev = _evento("g1", "Reunión con Ana", "2026-08-12T10:00:00-03:00", "2026-08-12T10:30:00-03:00",
                 [{"email": "ana@cliente.com", "displayName": "Ana"}])
    _sync(db, [ev])
    _sync(db, [ev])
    assert len(_filas(db, "meetings")) == 1


# ── reprogramar en Google ────────────────────────────────────────────────────

def test_reprogramar_en_google_se_propaga_al_crm(db):
    """El sync era insert-only: si el evento ya existia se salteaba, asi que el
    equipo veia una hora en el CRM y otra en su agenda real."""
    asistentes = [{"email": "ana@cliente.com", "displayName": "Ana"}]
    _sync(db, [_evento("g1", "Reunión con Ana", "2026-08-12T10:00:00-03:00",
                       "2026-08-12T10:30:00-03:00", asistentes)])
    _sync(db, [_evento("g1", "Reunión con Ana (movida)", "2026-08-13T15:00:00-03:00",
                       "2026-08-13T16:00:00-03:00", asistentes)])

    reuniones = _filas(db, "meetings")
    assert len(reuniones) == 1
    assert reuniones[0]["start_at"] == "2026-08-13T15:00:00"
    assert reuniones[0]["title"] == "Reunión con Ana (movida)"


# ── leads fantasma ───────────────────────────────────────────────────────────

def test_evento_personal_no_crea_lead_pero_si_reunion(db):
    """Dentista, gimnasio, standups: se ven en el calendario, no son oportunidades."""
    _sync(db, [_evento("g1", "Dentista", "2026-08-12T09:00:00-03:00",
                       "2026-08-12T09:30:00-03:00", asistentes=[])])

    assert len(_filas(db, "businesses")) == 0, "creo un lead fantasma"
    reuniones = _filas(db, "meetings")
    assert len(reuniones) == 1, "el evento personal tiene que seguir visible"
    assert reuniones[0]["client_id"] is None
    assert reuniones[0]["title"] == "Dentista"


def test_evento_sin_asistentes_tampoco_crea_lead(db):
    _sync(db, [_evento("g1", "Gimnasio", "2026-08-12T07:00:00-03:00",
                       "2026-08-12T08:00:00-03:00")])
    assert len(_filas(db, "businesses")) == 0


def test_invitado_externo_si_crea_lead(db):
    _sync(db, [_evento("g1", "Reunión con Ana", "2026-08-12T10:00:00-03:00",
                       "2026-08-12T10:30:00-03:00",
                       [{"email": "ana@cliente.com", "displayName": "Ana"}])])
    leads = _filas(db, "businesses")
    assert len(leads) == 1
    assert leads[0]["email"] == "ana@cliente.com"
    assert leads[0]["crm_status"] == "reunion_agendada"


def test_mail_interno_no_crea_lead(db, monkeypatch):
    """CALENDLY_BLOCKED_EMAILS ya existia, pero el sync de Google la ignoraba."""
    monkeypatch.setenv("CALENDLY_BLOCKED_EMAILS", "socio@scalerics.com")
    _sync(db, [_evento("g1", "Interna", "2026-08-12T10:00:00-03:00",
                       "2026-08-12T10:30:00-03:00",
                       [{"email": "socio@scalerics.com", "displayName": "Socio"}])])
    assert len(_filas(db, "businesses")) == 0
    assert len(_filas(db, "meetings")) == 1


def test_no_duplica_el_lead_si_ya_existe_por_email(db):
    asistentes = [{"email": "ana@cliente.com", "displayName": "Ana"}]
    _sync(db, [_evento("g1", "Primera", "2026-08-12T10:00:00-03:00",
                       "2026-08-12T10:30:00-03:00", asistentes)])
    _sync(db, [_evento("g2", "Segunda", "2026-08-20T16:00:00-03:00",
                       "2026-08-20T16:30:00-03:00", asistentes)])
    assert len(_filas(db, "businesses")) == 1
    assert len(_filas(db, "meetings")) == 2


# ── migracion del esquema ────────────────────────────────────────────────────

def test_migracion_hace_client_id_nullable(tmp_path):
    """Bases viejas tienen meetings.client_id NOT NULL. init_db reconstruye la
    tabla conservando las filas."""
    p = str(tmp_path / "vieja.db")
    init_db(p)

    # Se vuelve la tabla al esquema viejo (client_id NOT NULL) para simular una
    # base anterior a la migracion, con una reunion ya cargada.
    conn = sqlite3.connect(p)
    conn.executescript("""
        DROP TABLE meetings;
        CREATE TABLE meetings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            calendar_event_id TEXT UNIQUE, title TEXT, start_at TIMESTAMP,
            end_at TIMESTAMP, meet_link TEXT, status TEXT DEFAULT 'scheduled',
            transcript TEXT, summary TEXT, requirements TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        INSERT INTO businesses (id, name) VALUES (1, 'Cliente');
        INSERT INTO meetings (id, client_id, title) VALUES (1, 1, 'Existente');
    """)
    conn.commit()
    conn.close()

    init_db(p)

    conn = sqlite3.connect(p)
    try:
        cols = {r[1]: r for r in conn.execute("PRAGMA table_info(meetings)")}
        assert cols["client_id"][3] == 0, "client_id sigue siendo NOT NULL"
        filas = conn.execute("SELECT id, title FROM meetings").fetchall()
        assert filas == [(1, "Existente")], "la migracion perdio datos"
    finally:
        conn.close()
