import json
import os
import sqlite3

import pytest
from flask import Flask

from database import init_db, insert_business
from routes.calendly import calendly_bp


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = str(tmp_path / "leads.db")
    init_db(db_path)
    monkeypatch.setenv("DB_PATH", db_path)
    monkeypatch.delenv("CALENDLY_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("RECALL_API_KEY", raising=False)
    flask_app = Flask(__name__)
    flask_app.register_blueprint(calendly_bp)
    flask_app.config["DB_PATH"] = db_path
    return flask_app


def _payload(questions):
    return {
        "event": "invitee.created",
        "payload": {
            "invitee": {
                "name": "Juan Pérez",
                "email": "juan@ferreteriasol.com.uy",
                "questions_and_answers": questions,
            },
            "event": {
                "uri": "https://api.calendly.com/scheduled_events/EVT123",
                "start_time": "2026-08-20T13:00:00.000000Z",
                "end_time": "2026-08-20T13:45:00.000000Z",
                "location": {"join_url": "https://meet.google.com/abc-defg-hij"},
            },
        },
    }


FULL_FORM = [
    {"question": "Empresa", "answer": "Ferretería El Sol"},
    {"question": "¿Qué necesitás?", "answer": "E-commerce / tienda online"},
    {"question": "Teléfono / WhatsApp", "answer": "+598 99 123 456"},
]


def _lead(db_path, email):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM businesses WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def test_new_lead_stores_company_service_and_phone(app):
    db_path = app.config["DB_PATH"]
    res = app.test_client().post("/api/calendly/webhook", json=_payload(FULL_FORM))
    assert res.status_code == 200, res.data

    lead = _lead(db_path, "juan@ferreteriasol.com.uy")
    assert lead is not None
    assert lead["name"] == "Ferretería El Sol"          # empresa, no la persona
    assert lead["interest"] == "E-commerce / tienda online"
    assert lead["phone"] == "+59899123456"
    assert "Contacto: Juan Pérez" in lead["notes"]
    assert "Interés: E-commerce / tienda online" in lead["notes"]
    assert lead["crm_status"] == "reunion_agendada"


def test_falls_back_to_person_name_when_no_company(app):
    db_path = app.config["DB_PATH"]
    questions = [q for q in FULL_FORM if q["question"] != "Empresa"]
    res = app.test_client().post("/api/calendly/webhook", json=_payload(questions))
    assert res.status_code == 200

    lead = _lead(db_path, "juan@ferreteriasol.com.uy")
    assert lead["name"] == "Juan Pérez"
    assert lead["interest"] == "E-commerce / tienda online"


def test_phone_falls_back_to_text_reminder_number(app):
    db_path = app.config["DB_PATH"]
    payload = _payload([q for q in FULL_FORM if "Teléfono" not in q["question"]])
    payload["payload"]["invitee"]["text_reminder_number"] = "+59891555444"

    res = app.test_client().post("/api/calendly/webhook", json=payload)
    assert res.status_code == 200
    assert _lead(db_path, "juan@ferreteriasol.com.uy")["phone"] == "+59891555444"


def test_existing_lead_is_enriched_without_losing_data(app):
    db_path = app.config["DB_PATH"]
    insert_business(db_path, {
        "name": "Ferretería El Sol",
        "email": "juan@ferreteriasol.com.uy",
        "phone": "+59899123456",
        "category": "ferreteria",
        "notes": "Lo scrapeamos en junio",
    })

    res = app.test_client().post("/api/calendly/webhook", json=_payload(FULL_FORM))
    assert res.status_code == 200

    lead = _lead(db_path, "juan@ferreteriasol.com.uy")
    assert lead["category"] == "ferreteria"              # el rubro no se toca
    assert lead["interest"] == "E-commerce / tienda online"  # el servicio va aparte
    assert "Lo scrapeamos en junio" in lead["notes"]     # la nota vieja sobrevive
    assert "Interés: E-commerce / tienda online" in lead["notes"]
    assert lead["crm_status"] == "reunion_agendada"
    assert lead["source"] != "calendly_unmatched"        # matcheó, no es lead huérfano
