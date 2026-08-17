from unittest.mock import MagicMock, patch

import pytest
import requests

from services.email_service import _send, _send_estado, send_meta_lead_reminder


def _capturar():
    return patch("services.email_service._send_estado", return_value="ok")


# ── Tri-estado del envio ───────────────────────────────────────────────────────

def _respuesta(status=200):
    r = MagicMock()
    if status >= 400:
        r.raise_for_status.side_effect = requests.exceptions.HTTPError(f"{status}")
    return r


@pytest.mark.parametrize("efecto, esperado", [
    (None, "ok"),
    (requests.exceptions.HTTPError("422"), "fallo"),
    (requests.exceptions.ConnectionError("no hay red"), "fallo"),
    (requests.exceptions.Timeout("tardo mas de 10s"), "desconocido"),
    (ValueError("cualquier otra cosa"), "desconocido"),
])
def test_el_envio_distingue_fallo_de_no_se(monkeypatch, efecto, esperado):
    """Un timeout NO es un fallo: Resend pudo haber aceptado el mail igual."""
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    if isinstance(efecto, requests.exceptions.HTTPError):
        post = MagicMock(return_value=_respuesta(422))
    elif efecto is None:
        post = MagicMock(return_value=_respuesta(200))
    else:
        post = MagicMock(side_effect=efecto)

    with patch("services.email_service.requests.post", post):
        assert _send_estado("a@b.com", "asunto", "<p>x</p>") == esperado


@pytest.mark.parametrize("efecto, esperado", [
    (None, True),
    (requests.exceptions.HTTPError("422"), False),
    (requests.exceptions.Timeout("tardo"), False),
])
def test_send_sigue_devolviendo_bool_para_las_llamadas_de_siempre(monkeypatch, efecto, esperado):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    if efecto is None:
        post = MagicMock(return_value=_respuesta(200))
    elif isinstance(efecto, requests.exceptions.HTTPError):
        post = MagicMock(return_value=_respuesta(422))
    else:
        post = MagicMock(side_effect=efecto)

    with patch("services.email_service.requests.post", post):
        r = _send("a@b.com", "asunto", "<p>x</p>")

    assert r is esperado, "las 6 llamadas existentes esperan un bool, no el tri-estado"


def test_el_mail_sale_de_contacto_y_lleva_baja():
    with _capturar() as enviar:
        send_meta_lead_reminder(
            "lead@ejemplo.com", "Romyna", "RP Estudio Juridico",
            "una nueva pagina web", "https://crm/baja/abc123",
        )

    assert enviar.called
    kwargs = enviar.call_args.kwargs
    assert kwargs["from_email"] == "Scalerics <contacto@scalerics.com>"
    assert kwargs["headers"]["List-Unsubscribe"] == "<https://crm/baja/abc123>"

    html = enviar.call_args.args[2]
    assert "https://crm/baja/abc123" in html, "el link de baja va visible en el cuerpo"
    assert "calendly.com/scalerics/consultoriagratuita" in html
    assert "+598 97 250 713" in html


def test_personaliza_con_lo_que_pidio_el_lead():
    with _capturar() as enviar:
        send_meta_lead_reminder(
            "lead@ejemplo.com", "Romyna", "RP Estudio Juridico",
            "una nueva pagina web", "https://crm/baja/x",
        )

    html = enviar.call_args.args[2]
    assert "RP Estudio Juridico" in html
    assert "una nueva pagina web" in html


def test_escapa_la_entrada_del_formulario():
    with _capturar() as enviar:
        send_meta_lead_reminder(
            "lead@ejemplo.com", '<script>alert(1)</script>', "Neg<ocio>",
            "web", "https://crm/baja/x",
        )

    html = enviar.call_args.args[2]
    assert "<script>" not in html, "el nombre lo llena cualquiera en internet"
    assert "&lt;script&gt;" in html
