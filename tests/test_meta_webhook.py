import json
import logging
from unittest.mock import MagicMock, patch

import pytest
import requests as _requests

from dashboard import create_app


@pytest.fixture
def app(tmp_path):
    # create_app(db_path) requiere la ruta de la base — no tiene default.
    application = create_app(str(tmp_path / "test.db"))
    application.config["TESTING"] = True
    return application


def test_fetch_failure_sends_alert(app, monkeypatch):
    """Si Graph falla, tiene que avisar por mail con el leadgen_id, no comerse el error."""
    from routes import meta

    with patch.object(meta.requests, "get", side_effect=RuntimeError("graph caido")), \
         patch.object(meta, "_get_admin_emails", return_value=["juan@scalerics.com"]), \
         patch("services.email_service.send_meta_lead_failure_alert") as alert:
        monkeypatch.setattr(meta, "PAGE_TOKEN", "token-de-prueba")
        meta._fetch_and_store_lead(app, "LEAD-123", "FORM-456")

    assert alert.called, "un fallo de Graph tiene que disparar el mail de alerta"
    args = alert.call_args[0]
    assert "LEAD-123" in args, "la alerta tiene que traer el leadgen_id para recuperarlo a mano"


def test_fetch_failure_does_not_leak_token_in_alert(app, monkeypatch):
    """El HTTPError de Graph trae el access_token en la URL; no puede viajar por mail."""
    from routes import meta

    secret = "SECRET_TOKEN_ABC"
    fake_response = MagicMock()
    fake_response.raise_for_status.side_effect = _requests.exceptions.HTTPError(
        f"400 Client Error: Bad Request for url: "
        f"https://graph.facebook.com/v20.0/LEAD-123?access_token={secret}&fields=field_data"
    )

    with patch.object(meta.requests, "get", return_value=fake_response), \
         patch.object(meta, "_get_admin_emails", return_value=["juan@scalerics.com"]), \
         patch("services.email_service.send_meta_lead_failure_alert") as alert:
        monkeypatch.setattr(meta, "PAGE_TOKEN", secret)
        meta._fetch_and_store_lead(app, "LEAD-123", "FORM-456")

    assert alert.called, "el fallo tiene que disparar el mail de alerta igual"
    error_arg = alert.call_args[0][2]
    assert secret not in error_arg, "el access_token no puede viajar en el mail de alerta"


def test_fetch_failure_without_admins_logs_explicitly(app, monkeypatch, caplog):
    """Si no hay ningun admin configurado, tiene que quedar un log explicito y distinguible."""
    from routes import meta

    with patch.object(meta.requests, "get", side_effect=RuntimeError("graph caido")), \
         patch.object(meta, "_get_admin_emails", return_value=[]), \
         patch("services.email_service.send_meta_lead_failure_alert") as alert:
        monkeypatch.setattr(meta, "PAGE_TOKEN", "token-de-prueba")
        with caplog.at_level(logging.ERROR):
            meta._fetch_and_store_lead(app, "LEAD-789", "FORM-456")

    assert not alert.called, "sin admins no hay a quien mandarle el mail"
    assert any(
        "LEAD-789" in record.message and "admin" in record.message.lower()
        for record in caplog.records
    ), "tiene que quedar un log explicito de que nadie fue avisado, distinguible del caso normal"
