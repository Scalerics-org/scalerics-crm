"""Tests del sync de Calendly via Google Calendar.

El formato de los eventos sale de una reserva real hecha el 12/8/2026 contra
calendly.com/scalerics/consultoriagratuita, no de la documentación.
"""

import sqlite3

import pytest

from database import init_db, insert_business
from services.calendly_gcal import parse_calendly_event, sync_events


DESCRIPTION = """Nombre del evento
Consultoria gratuita de Scalerics

Ubicación: Esto es una conferencia web de Google Meet.
Puede unirse a la reunión desde el ordenador, tablet o smartphone.
https://calendly.com/events/a91272e0-f2ca-4dc3-b1ca-0beb3e013f30/google_meet

Empresa: Ferretería El Sol

¿Qué necesitás?: E-commerce / tienda online

Teléfono / WhatsApp: +598 99 123 456

¿Necesitas realizar algún cambio a este evento?
Cancelar: https://calendly.com/cancellations/27814831-29bb-489c-bf11-9c0d3cc924b6
Reprogramar: https://calendly.com/reschedulings/27814831-29bb-489c-bf11-9c0d3cc924b6

Desarrollado por Calendly.com
"""

# Un evento viejo, de antes de que el formulario tuviera preguntas.
DESCRIPTION_SIN_PREGUNTAS = """Nombre del evento
Consultoria gratuita de Scalerics

Ubicación: Esto es una conferencia web de Google Meet.
Puede unirse a la reunión desde el ordenador, tablet o smartphone.
https://calendly.com/events/51973393-6e15-4d42-b45f-ac31620c0dc9/google_meet

¿Necesitas realizar algún cambio a este evento?
Cancelar: https://calendly.com/cancellations/369e3ef9-32eb-4fc3-a2e8-f7ddc584b237

Desarrollado por Calendly.com
"""

HOST = "scalerics@gmail.com"


def _event(summary="Juan Pérez y Contacto Scalerics", description=DESCRIPTION,
           guest="juan@ferreteriasol.com.uy"):
    return {
        "summary": summary,
        "description": description,
        "start": {"dateTime": "2026-08-21T19:00:00-03:00"},
        "end": {"dateTime": "2026-08-21T19:45:00-03:00"},
        "hangoutLink": "https://meet.google.com/abc-defg-hij",
        "organizer": {"email": HOST, "displayName": "Contacto Scalerics"},
        "attendees": [{"email": HOST}, {"email": guest}],
    }


# ─── parser ──────────────────────────────────────────────────────────────────

def test_parsea_los_cinco_campos():
    p = parse_calendly_event(_event(), host_email=HOST)
    assert p["name"] == "Juan Pérez"
    assert p["email"] == "juan@ferreteriasol.com.uy"
    assert p["company"] == "Ferretería El Sol"
    assert p["service"] == "E-commerce / tienda online"
    assert p["phone"] == "+598 99 123 456"


def test_event_uri_usa_el_mismo_formato_que_el_webhook():
    # Así un lead no se duplica si algún día se activa el webhook de Calendly.
    p = parse_calendly_event(_event(), host_email=HOST)
    assert p["event_uri"] == (
        "https://api.calendly.com/scheduled_events/"
        "a91272e0-f2ca-4dc3-b1ca-0beb3e013f30"
    )


def test_ignora_lineas_que_no_son_respuestas():
    p = parse_calendly_event(_event(), host_email=HOST)
    # "Ubicación:", "Cancelar:" y "Reprogramar:" también son "clave: valor"
    assert "calendly.com/cancellations" not in (p["company"] or "")
    assert p["company"] == "Ferretería El Sol"


def test_evento_viejo_sin_preguntas_no_rompe():
    p = parse_calendly_event(
        _event(description=DESCRIPTION_SIN_PREGUNTAS), host_email=HOST)
    assert p["company"] == ""
    assert p["service"] == ""
    assert p["phone"] == ""
    assert p["email"] == "juan@ferreteriasol.com.uy"   # esto sí lo tenemos


def test_detecta_evento_cancelado():
    ev = _event(summary="Cancelado: Juan Pérez y Contacto Scalerics")
    p = parse_calendly_event(ev, host_email=HOST)
    assert p["canceled"] is True
    assert p["name"] == "Juan Pérez"       # el prefijo no ensucia el nombre


def test_evento_ajeno_a_calendly_se_descarta():
    ev = _event(description="Reunión de equipo, nada que ver")
    assert parse_calendly_event(ev, host_email=HOST) is None


def test_nombre_con_y_no_se_corta_mal():
    ev = _event(summary="Pedro y Pablo SRL y Contacto Scalerics")
    p = parse_calendly_event(ev, host_email=HOST)
    assert p["name"] == "Pedro y Pablo SRL"


# ─── sync ────────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "leads.db")
    init_db(path)
    return path


def _lead(db_path, email):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM businesses WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _meetings(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM meetings").fetchall()]
    finally:
        conn.close()


def test_sync_crea_lead_y_reunion(db):
    res = sync_events(db, [_event()], host_email=HOST)
    assert res["created"] == 1

    lead = _lead(db, "juan@ferreteriasol.com.uy")
    assert lead["name"] == "Ferretería El Sol"
    assert lead["interest"] == "E-commerce / tienda online"
    assert lead["phone"] == "+59899123456"
    assert lead["crm_status"] == "demo_agendada"
    assert "Contacto: Juan Pérez" in lead["notes"]
    assert len(_meetings(db)) == 1


def test_sync_es_idempotente(db):
    sync_events(db, [_event()], host_email=HOST)
    res = sync_events(db, [_event()], host_email=HOST)

    assert res["created"] == 0
    assert res["skipped"] == 1
    assert len(_meetings(db)) == 1          # no duplica la reunión
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0] == 1
    finally:
        conn.close()


def test_sync_no_pisa_datos_de_un_lead_existente(db):
    insert_business(db, {
        "name": "Ferretería El Sol",
        "email": "juan@ferreteriasol.com.uy",
        "phone": "+59899123456",
        "category": "ferreteria",
        "notes": "Lo scrapeamos en junio",
    })
    sync_events(db, [_event()], host_email=HOST)

    lead = _lead(db, "juan@ferreteriasol.com.uy")
    assert lead["category"] == "ferreteria"
    assert lead["interest"] == "E-commerce / tienda online"
    assert "Lo scrapeamos en junio" in lead["notes"]
    assert lead["source"] != "calendly_unmatched"


def test_sync_marca_cancelada_la_reunion_ya_cargada(db):
    sync_events(db, [_event()], host_email=HOST)
    ev = _event(summary="Cancelado: Juan Pérez y Contacto Scalerics")
    res = sync_events(db, [ev], host_email=HOST)

    assert res["canceled"] == 1
    assert _meetings(db)[0]["status"] == "canceled"


def test_sync_ignora_eventos_que_no_son_de_calendly(db):
    otro = _event(description="Almuerzo con proveedores")
    res = sync_events(db, [otro], host_email=HOST)

    assert res["created"] == 0
    assert _meetings(db) == []


def test_dry_run_no_escribe_pero_muestra_que_haria(db):
    res = sync_events(db, [_event()], host_email=HOST, dry_run=True)

    assert res["created"] == 1
    assert res["would_create"][0]["lead"] == "Ferretería El Sol"
    assert res["would_create"][0]["servicio"] == "E-commerce / tienda online"
    assert res["would_create"][0]["match"] is None      # sería lead nuevo
    # nada tocó la base
    assert _meetings(db) == []
    assert _lead(db, "juan@ferreteriasol.com.uy") is None


def test_dry_run_avisa_cuando_el_lead_ya_existe(db):
    insert_business(db, {
        "name": "Ferretería El Sol",
        "email": "juan@ferreteriasol.com.uy",
        "phone": "+59899123456",
    })
    res = sync_events(db, [_event()], host_email=HOST, dry_run=True)

    assert res["would_create"][0]["match"] == "Ferretería El Sol"


def test_corta_el_sufijo_del_host_sin_displayName():
    # Google devuelve el organizador sin displayName en los eventos reales.
    ev = _event(summary="Gonzalo/Scalerics y Contacto Scalerics")
    ev["organizer"] = {"email": HOST}
    assert parse_calendly_event(ev, host_email=HOST)["name"] == "Gonzalo/Scalerics"


def test_corta_por_el_ultimo_y_sin_displayName():
    ev = _event(summary="Pedro y Pablo SRL y Contacto Scalerics")
    ev["organizer"] = {"email": HOST}
    assert parse_calendly_event(ev, host_email=HOST)["name"] == "Pedro y Pablo SRL"


def test_nombre_sin_sufijo_queda_intacto():
    ev = _event(summary="Ferretería El Sol")
    ev["organizer"] = {"email": HOST}
    assert parse_calendly_event(ev, host_email=HOST)["name"] == "Ferretería El Sol"


# ─── mails del equipo ────────────────────────────────────────────────────────
# Calendly suma a los co-hosts como attendees del evento. Antes los tomábamos
# como si fueran el cliente, y cuatro reuniones distintas terminaban con el
# mismo mail.

TEAM = {"juan.pereyra.comunicacion@gmail.com", "gonzasiuciak@gmail.com"}


def test_no_toma_el_mail_de_un_companero_como_cliente():
    ev = _event(guest="juan.pereyra.comunicacion@gmail.com")
    p = parse_calendly_event(ev, host_email=HOST, team_emails=TEAM)
    assert p["email"] == ""


def test_el_mail_de_un_cliente_real_si_se_toma():
    ev = _event(guest="cliente@ferreteriasol.com.uy")
    p = parse_calendly_event(ev, host_email=HOST, team_emails=TEAM)
    assert p["email"] == "cliente@ferreteriasol.com.uy"


def test_sin_mail_el_lead_se_crea_igual_y_matchea_por_telefono(db):
    ev = _event(guest="juan.pereyra.comunicacion@gmail.com")
    sync_events(db, [ev], host_email=HOST, team_emails=TEAM)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        lead = dict(conn.execute("SELECT * FROM businesses").fetchone())
    finally:
        conn.close()
    assert lead["phone"] == "+59899123456"
    assert lead["email"] is None          # mejor vacío que el mail equivocado
    assert lead["interest"] == "E-commerce / tienda online"


def test_sin_telefono_ni_mail_no_se_crea_basura(db):
    # Reuniones viejas, de antes de que el formulario pidiera datos.
    ev = _event(description=DESCRIPTION_SIN_PREGUNTAS,
                guest="juan.pereyra.comunicacion@gmail.com")
    res = sync_events(db, [ev], host_email=HOST, team_emails=TEAM)

    assert res["sin_contacto"] == 1
    assert res["created"] == 0
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0] == 0
    finally:
        conn.close()


# ─── reuniones que ya pasaron ────────────────────────────────────────────────

def test_reunion_pasada_no_retrocede_el_estado_del_lead(db):
    from database import update_business
    bid = insert_business(db, {
        "name": "Ferretería El Sol",
        "email": "cliente@ferreteriasol.com.uy",
        "phone": "+59899123456",
    })
    update_business(db, bid, crm_status="cliente")
    ev = _event(guest="cliente@ferreteriasol.com.uy")
    ev["start"] = {"dateTime": "2026-07-13T17:30:00-03:00"}
    ev["end"] = {"dateTime": "2026-07-13T18:15:00-03:00"}

    sync_events(db, [ev], host_email=HOST, team_emails=TEAM,
                now="2026-08-12T00:00:00")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        lead = dict(conn.execute("SELECT * FROM businesses").fetchone())
    finally:
        conn.close()
    assert lead["crm_status"] == "cliente"      # no vuelve a demo_agendada
    assert len(_meetings(db)) == 1              # la reunión igual queda cargada


def test_reunion_futura_si_marca_demo_agendada(db):
    from database import update_business
    bid = insert_business(db, {
        "name": "Ferretería El Sol",
        "email": "cliente@ferreteriasol.com.uy",
        "phone": "+59899123456",
    })
    update_business(db, bid, crm_status="interesado")
    sync_events(db, [_event(guest="cliente@ferreteriasol.com.uy")],
                host_email=HOST, team_emails=TEAM, now="2026-08-12T00:00:00")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        lead = dict(conn.execute("SELECT * FROM businesses").fetchone())
    finally:
        conn.close()
    assert lead["crm_status"] == "demo_agendada"
