import json
from unittest.mock import patch

import pytest

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
