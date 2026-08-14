"""Tests del parser de los mails de Calendly.

El HTML de ejemplo es el de un mail real de scalerics@gmail.com, leído el
14/8/2026 — no inventado a partir de la documentación.
"""

import sqlite3

import pytest

from database import init_db, insert_business
from services.calendly_gmail import (
    html_to_lines,
    parse_calendly_email,
)

UUID = "1ffe1bb4-a096-498d-8dac-2cec5dcf3d6d"

MAIL = f"""<html><body>
<p>Hi Contacto Scalerics,</p>
<p>A new event has been scheduled.</p>
<div>Event Type:</div><div>Consultoria gratuita de Scalerics</div>
<div>Invitee:</div><div>Ana Clara Veterinaria</div>
<div>Invitee Email:</div><div>cliente@veterinaria.com.uy</div>
<div>Additional Guests:</div><div>gonzasiuciak@gmail.com</div>
<div>Event Date/Time:</div>
<div>12:30 - Friday, 14 August 2026 (Eastern Time - US &amp; Canada)</div>
<div>Location:</div><div>This is a Google Meet web conference.</div>
<div>Invitee Time Zone:</div><div>Montevideo Time</div>
<div>Questions:</div>
<div>Empresa</div><div>Veterinaria Ana Clara</div>
<div>&iquest;Qu&eacute; necesit&aacute;s?</div><div>Desarrollo a medida</div>
<div>Tel&eacute;fono / WhatsApp</div><div>+598 92 781 598</div>
<a href="https://calendly.com/events/{UUID}">View event in Calendly</a>
<p>Pro Tip!</p><p>Sent from Calendly</p>
</body></html>"""

TEAM = {"juan.pereyra.comunicacion@gmail.com", "gonzasiuciak@gmail.com",
        "scalerics@gmail.com"}


def test_saca_los_cinco_campos_del_mail():
    p = parse_calendly_email(MAIL, team_emails=TEAM)
    assert p["name"] == "Ana Clara Veterinaria"
    assert p["email"] == "cliente@veterinaria.com.uy"
    assert p["company"] == "Veterinaria Ana Clara"
    assert p["service"] == "Desarrollo a medida"
    assert p["phone"] == "+598 92 781 598"


def test_el_event_uri_coincide_con_el_del_calendario():
    # Es lo que evita cargar dos veces el mismo evento.
    p = parse_calendly_email(MAIL, team_emails=TEAM)
    assert p["event_uri"] == f"https://api.calendly.com/scheduled_events/{UUID}"


def test_descarta_el_mail_del_equipo_como_invitado():
    # Pasó de verdad: una reserva del equipo con Invitee Email de Scalerics.
    mail = MAIL.replace("cliente@veterinaria.com.uy", "scalerics@gmail.com")
    assert parse_calendly_email(mail, team_emails=TEAM)["email"] == ""


def test_no_confunde_additional_guests_con_el_invitado():
    p = parse_calendly_email(MAIL, team_emails=TEAM)
    assert p["email"] != "gonzasiuciak@gmail.com"


def test_las_entidades_html_se_decodifican():
    lines = html_to_lines(MAIL)
    assert "¿Qué necesitás?" in lines
    assert "Teléfono / WhatsApp" in lines


def test_un_mail_que_no_es_de_evento_se_descarta():
    assert parse_calendly_email("<p>Newsletter de Calendly</p>") is None


def test_campo_vacio_no_se_come_el_label_siguiente():
    mail = MAIL.replace(
        "<div>Additional Guests:</div><div>gonzasiuciak@gmail.com</div>",
        "<div>Additional Guests:</div>")
    p = parse_calendly_email(mail, team_emails=TEAM)
    assert p["name"] == "Ana Clara Veterinaria"
    assert p["company"] == "Veterinaria Ana Clara"


def test_el_pie_del_mail_no_entra_como_pregunta():
    p = parse_calendly_email(MAIL, team_emails=TEAM)
    # "View event in Calendly" / "Pro Tip!" están después de las preguntas
    assert "Pro Tip" not in (p["phone"] or "")
    assert p["phone"] == "+598 92 781 598"


# ─── escritura compartida con la vía calendario ──────────────────────────────

@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "leads.db")
    init_db(path)
    return path


def test_el_mail_carga_el_lead_con_su_email(db):
    from services.calendly_gcal import sync_parsed
    p = parse_calendly_email(MAIL, team_emails=TEAM)
    sync_parsed(db, [p], now="2026-08-01T00:00:00")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        lead = dict(conn.execute("SELECT * FROM businesses").fetchone())
    finally:
        conn.close()
    assert lead["name"] == "Veterinaria Ana Clara"
    assert lead["email"] == "cliente@veterinaria.com.uy"
    assert lead["phone"] == "+59892781598"
    assert lead["interest"] == "Desarrollo a medida"


def test_no_duplica_lo_que_ya_entro_por_el_calendario(db):
    from services.calendly_gcal import sync_parsed
    p = parse_calendly_email(MAIL, team_emails=TEAM)
    sync_parsed(db, [p], now="2026-08-01T00:00:00")
    res = sync_parsed(db, [p], now="2026-08-01T00:00:00")

    assert res["created"] == 0
    assert res["skipped"] == 1
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] == 1
    finally:
        conn.close()
