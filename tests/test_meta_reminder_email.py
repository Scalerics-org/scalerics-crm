from unittest.mock import patch

from services.email_service import send_meta_lead_reminder


def _capturar():
    return patch("services.email_service._send", return_value=True)


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
