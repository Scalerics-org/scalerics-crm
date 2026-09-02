"""El interruptor de on/off del bot, por lead, desde el panel de WhatsApp.

Es el respaldo, no el mecanismo principal: el bot ya se pausa solo por 12 horas
cuando Juan escribe desde el telefono, y esa pausa vence sola. Este interruptor
es para apagarlo a proposito y por tiempo indefinido.

Lo que se prueba aca es el proxy: que el CRM le pase al bot lo que el panel
pidio, y no lo contrario. Apagar cuando se queria prender deja a un lead mudo
sin que nadie lo note, que es justo el problema que este diseño quiere evitar.
"""

from unittest.mock import patch

import pytest

import dashboard
from database import init_db


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "token-de-test")
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    app = dashboard.create_app(ruta)
    app.config["TESTING"] = True
    return app.test_client()


_AUTH = {"x-admin-token": "token-de-test"}


def test_apagar_le_pide_al_bot_activo_false(cliente):
    with patch("routes.wa._bot_req", return_value=({"ok": True}, None)) as bot:
        r = cliente.post("/api/wa/leads/59899123456/bot", headers=_AUTH, json={"activo": False})

    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "activo": False}
    metodo, ruta = bot.call_args[0]
    assert metodo == "POST"
    assert ruta == "leads/phone/59899123456/bot"
    assert bot.call_args[1]["json"] == {"activo": False}


def test_prender_le_pide_al_bot_activo_true(cliente):
    with patch("routes.wa._bot_req", return_value=({"ok": True}, None)) as bot:
        r = cliente.post("/api/wa/leads/59899123456/bot", headers=_AUTH, json={"activo": True})

    assert r.get_json()["activo"] is True
    assert bot.call_args[1]["json"] == {"activo": True}


def test_sin_cuerpo_se_asume_prender(cliente):
    # Un POST pelado no puede apagar el bot por accidente.
    with patch("routes.wa._bot_req", return_value=({"ok": True}, None)) as bot:
        r = cliente.post("/api/wa/leads/59899123456/bot", headers=_AUTH, json={})

    assert r.get_json()["activo"] is True
    assert bot.call_args[1]["json"] == {"activo": True}


def test_si_el_bot_falla_el_panel_se_entera(cliente):
    # Sin esto el panel dibujaria el interruptor movido sobre un cambio que
    # nunca ocurrio, y Juan creeria que el bot esta apagado cuando sigue
    # contestando.
    with patch("routes.wa._bot_req", return_value=(None, "bot caido")):
        r = cliente.post("/api/wa/leads/59899123456/bot", headers=_AUTH, json={"activo": False})

    assert r.get_json() == {"ok": False, "error": "bot caido"}
