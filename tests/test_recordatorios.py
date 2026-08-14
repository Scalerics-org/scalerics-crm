"""Recordatorios de reuniones.

El CRM no mandaba ningun aviso previo: ni al cliente ni al equipo. Los no-shows
son plata directa y no habia forma de reducirlos.
"""

import datetime
from unittest.mock import patch

import pytest
import pytz

from database import connect, create_meeting, init_db, insert_business
from services import reminder_service as rs

MVD = pytz.timezone("America/Montevideo")


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "rec.db")
    init_db(p)
    return p


def _en(horas, minutos=0):
    """Un start_at a X horas de ahora, en el mismo formato naive local que guarda
    el resto del modulo."""
    ahora = datetime.datetime.now(MVD).replace(tzinfo=None)
    return (ahora + datetime.timedelta(hours=horas, minutes=minutos)).isoformat()


def _reunion(db, cuando, email="ana@cliente.com", titulo="Reunión con Ana"):
    lead = insert_business(db, {"name": "Ana", "phone": f"+5989{abs(hash(cuando)) % 10**7:07d}",
                                "email": email})
    return create_meeting(db, lead, title=titulo, start_at=cuando,
                          end_at=cuando, status="scheduled",
                          meet_link="https://meet.google.com/abc-defg-hij")


def _col(db, mid, columna):
    conn = connect(db)
    try:
        return conn.execute(f"SELECT {columna} FROM meetings WHERE id=?", (mid,)).fetchone()[0]
    finally:
        conn.close()


# ── a quien se avisa ─────────────────────────────────────────────────────────

def test_avisa_24h_antes(db):
    mid = _reunion(db, _en(24, 5))
    with patch("services.reminder_service.send_meeting_reminder", return_value=True) as env:
        assert rs.procesar_recordatorios(db) == 1
    assert env.call_args.kwargs["horas_antes"] == 24
    assert env.call_args[0][0] == "ana@cliente.com"
    assert _col(db, mid, "reminder_24h_at") is not None


def test_avisa_1h_antes(db):
    mid = _reunion(db, _en(1, 5))
    with patch("services.reminder_service.send_meeting_reminder", return_value=True) as env:
        rs.procesar_recordatorios(db)
    assert env.call_args.kwargs["horas_antes"] == 1
    assert _col(db, mid, "reminder_1h_at") is not None


def test_el_recordatorio_lleva_el_link_de_meet(db):
    _reunion(db, _en(24, 5))
    with patch("services.reminder_service.send_meeting_reminder", return_value=True) as env:
        rs.procesar_recordatorios(db)
    assert env.call_args.kwargs["meet_link"] == "https://meet.google.com/abc-defg-hij"


# ── no repetir ───────────────────────────────────────────────────────────────

def test_no_repite_el_aviso_en_cada_pasada(db):
    """El scheduler corre cada 5 minutos: sin la marca, el cliente recibiria el
    mismo mail una y otra vez."""
    _reunion(db, _en(24, 5))
    with patch("services.reminder_service.send_meeting_reminder", return_value=True) as env:
        rs.procesar_recordatorios(db)
        rs.procesar_recordatorios(db)
        rs.procesar_recordatorios(db)
    assert env.call_count == 1


def test_si_el_envio_falla_se_reintenta(db):
    """No se marca como enviado si el mail no salio."""
    mid = _reunion(db, _en(24, 5))
    with patch("services.reminder_service.send_meeting_reminder", return_value=False):
        rs.procesar_recordatorios(db)
    assert _col(db, mid, "reminder_24h_at") is None

    with patch("services.reminder_service.send_meeting_reminder", return_value=True) as env:
        rs.procesar_recordatorios(db)
    assert env.call_count == 1
    assert _col(db, mid, "reminder_24h_at") is not None


# ── a quien NO se avisa ──────────────────────────────────────────────────────

def test_no_avisa_reuniones_fuera_de_la_ventana(db):
    _reunion(db, _en(48))   # muy lejos
    _reunion(db, _en(6))    # entre las dos ventanas
    with patch("services.reminder_service.send_meeting_reminder", return_value=True) as env:
        rs.procesar_recordatorios(db)
    assert env.call_count == 0


def test_no_avisa_reuniones_pasadas(db):
    _reunion(db, _en(-2))
    with patch("services.reminder_service.send_meeting_reminder", return_value=True) as env:
        rs.procesar_recordatorios(db)
    assert env.call_count == 0


def test_no_avisa_reuniones_canceladas(db):
    mid = _reunion(db, _en(24, 5))
    conn = connect(db)
    conn.execute("UPDATE meetings SET status='canceled' WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    with patch("services.reminder_service.send_meeting_reminder", return_value=True) as env:
        rs.procesar_recordatorios(db)
    assert env.call_count == 0


def test_sin_email_no_manda_pero_no_lo_reevalua_siempre(db):
    mid = _reunion(db, _en(24, 5), email="")
    with patch("services.reminder_service.send_meeting_reminder", return_value=True) as env:
        rs.procesar_recordatorios(db)
    assert env.call_count == 0
    assert _col(db, mid, "reminder_24h_at") is not None


def test_evento_personal_sin_cliente_no_genera_aviso(db):
    """Los eventos personales entran con client_id NULL desde el sync."""
    create_meeting(db, None, title="Dentista", start_at=_en(24, 5),
                   end_at=_en(24, 35), status="scheduled")
    with patch("services.reminder_service.send_meeting_reminder", return_value=True) as env:
        rs.procesar_recordatorios(db)
    assert env.call_count == 0


# ── formato ──────────────────────────────────────────────────────────────────

def test_la_fecha_se_muestra_legible(db):
    assert rs._formatear("2026-09-10T15:30:00") == "jueves 10/9 a las 15:30"


def test_una_fecha_invalida_no_rompe(db):
    assert rs._formatear("no es una fecha") == "no es una fecha"
