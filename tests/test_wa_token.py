"""El CRM se autentica contra el bot con su PROPIA credencial.

El panel de WhatsApp devolvia "unauthorized" siempre. La causa: _bot_req usaba el
ADMIN_TOKEN del CRM —la credencial de ENTRADA— para SALIR hacia el bot, y el bot
valida con su propio ADMIN_TOKEN, que es otro valor. Eso obligaba a que los dos
servicios compartieran el mismo secreto: un acoplamiento no escrito que se rompe
apenas alguien rota uno de los dos.

Son dos flujos independientes:
    bot -> CRM   el bot manda su CRM_ADMIN_TOKEN, el CRM lo valida con ADMIN_TOKEN
    CRM -> bot   el CRM manda BOT_ADMIN_TOKEN, el bot lo valida con su ADMIN_TOKEN
"""

from unittest.mock import MagicMock, patch

import pytest

from routes.wa import _bot_req, _token_del_bot


@pytest.fixture(autouse=True)
def entorno(monkeypatch):
    monkeypatch.setenv("BOT_API_URL", "http://bot.internal:8080")
    monkeypatch.delenv("BOT_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)


def _respuesta(status=200, cuerpo=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = cuerpo if cuerpo is not None else {"ok": True}
    r.text = str(cuerpo)
    return r


# ── que token se manda ───────────────────────────────────────────────────────

def test_usa_bot_admin_token(monkeypatch):
    monkeypatch.setenv("BOT_ADMIN_TOKEN", "token-del-bot")
    monkeypatch.setenv("ADMIN_TOKEN", "token-de-entrada-del-crm")
    assert _token_del_bot() == "token-del-bot"


def test_no_usa_el_admin_token_del_crm_si_hay_uno_propio(monkeypatch):
    """El bug original: salir con la credencial de entrada."""
    monkeypatch.setenv("BOT_ADMIN_TOKEN", "token-del-bot")
    monkeypatch.setenv("ADMIN_TOKEN", "token-de-entrada-del-crm")
    with patch("routes.wa.http_requests.request", return_value=_respuesta()) as req:
        _bot_req("GET", "leads")
    assert req.call_args.kwargs["headers"]["x-admin-token"] == "token-del-bot"


def test_cae_a_admin_token_si_no_hay_propio(monkeypatch):
    """Compatibilidad: instalaciones donde hoy los dos valores coinciden."""
    monkeypatch.setenv("ADMIN_TOKEN", "compartido")
    assert _token_del_bot() == "compartido"


def test_el_fallback_deja_aviso_en_el_log(monkeypatch, caplog):
    """Anda, pero es configuracion a corregir: tiene que quedar registrado."""
    monkeypatch.setenv("ADMIN_TOKEN", "compartido")
    with caplog.at_level("WARNING"):
        _token_del_bot()
    assert "BOT_ADMIN_TOKEN" in caplog.text


def test_sin_ningun_token_avisa_cual_falta(monkeypatch):
    _, err = _bot_req("GET", "leads")
    assert "BOT_ADMIN_TOKEN" in err


def test_sin_url_del_bot_avisa(monkeypatch):
    monkeypatch.setenv("BOT_ADMIN_TOKEN", "x")
    monkeypatch.delenv("BOT_API_URL", raising=False)
    _, err = _bot_req("GET", "leads")
    assert "BOT_API_URL" in err


# ── el error que veia el usuario ─────────────────────────────────────────────

def test_el_401_del_bot_explica_como_arreglarlo(monkeypatch):
    """Antes se veia "unauthorized" a secas, que no dice como salir de ahi."""
    monkeypatch.setenv("BOT_ADMIN_TOKEN", "no-coincide")
    with patch("routes.wa.http_requests.request",
               return_value=_respuesta(401, {"error": "unauthorized"})):
        datos, err = _bot_req("GET", "leads")
    assert datos is None
    assert "BOT_ADMIN_TOKEN" in err and "ADMIN_TOKEN del bot" in err


def test_otros_errores_conservan_el_mensaje_del_bot(monkeypatch):
    monkeypatch.setenv("BOT_ADMIN_TOKEN", "x")
    with patch("routes.wa.http_requests.request",
               return_value=_respuesta(500, {"error": "boom"})):
        _, err = _bot_req("GET", "leads")
    assert err == "boom"


def test_una_respuesta_ok_devuelve_los_datos(monkeypatch):
    monkeypatch.setenv("BOT_ADMIN_TOKEN", "x")
    with patch("routes.wa.http_requests.request",
               return_value=_respuesta(200, {"leads": [1, 2]})):
        datos, err = _bot_req("GET", "leads")
    assert err is None and datos == {"leads": [1, 2]}
