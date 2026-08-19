"""Extraccion de direcciones de mail del HTML de un sitio."""

import pytest

from services.email_finder import es_mail_basura, extraer_mails


@pytest.mark.parametrize("mail", [
    "usuario@dominio.com",
    "tuemail@tudominio.com",
    "ejemplo@example.com",
    "noreply@inmobiliaria.com.uy",
    "no-reply@inmobiliaria.com.uy",
    "info@sentry.wixpress.com",
    "logo@2x.png",
    "donotreply@banco.com.uy",
    "tucorreo@radio.com.uy",
])
def test_direcciones_basura(mail):
    """Mandarle a una direccion de ejemplo es dano puro a la reputacion.

    `usuario@dominio.com` no es hipotetico: aparecio en el sondeo, dejado en
    la plantilla de una inmobiliaria real.
    """
    assert es_mail_basura(mail) is True


@pytest.mark.parametrize("mail", [
    "info@imas.uy",
    "hola@acsa.uy",
    "contacto@diegoalfonso.com.uy",
    "ferraripropiedades@adinet.com.uy",
    "inmobiliaria.uru@gmail.com",
    "correo@estudio.com.uy",
    "email@empresa.com.uy",
])
def test_direcciones_buenas(mail):
    """Salidas reales del sondeo: ninguna se puede descartar."""
    assert es_mail_basura(mail) is False


def test_extrae_del_mailto():
    html = '<a href="mailto:info@inmobiliaria.com.uy">Escribinos</a>'
    assert extraer_mails(html) == ["info@inmobiliaria.com.uy"]


def test_el_mailto_va_antes_que_el_texto_suelto():
    """Un mailto es una direccion que el dueno puso para que le escriban.
    Una suelta en el HTML puede ser cualquier cosa."""
    html = """
      <p>Escribile al contador: contador@estudio.com.uy</p>
      <a href="mailto:info@inmobiliaria.com.uy">Contacto</a>
    """
    assert extraer_mails(html)[0] == "info@inmobiliaria.com.uy"


def test_filtra_la_basura_del_html():
    html = """
      <a href="mailto:usuario@dominio.com">Mail</a>
      <p>info@inmobiliaria.com.uy</p>
    """
    assert extraer_mails(html) == ["info@inmobiliaria.com.uy"]


def test_no_repite_la_misma_direccion_con_otra_capitalizacion():
    html = """
      <a href="mailto:Info@Inmobiliaria.com.uy">Mail</a>
      <p>info@inmobiliaria.com.uy</p>
    """
    assert len(extraer_mails(html)) == 1


def test_sitio_sin_direcciones():
    assert extraer_mails("<html><body><p>Llamanos al 2900 1111</p></body></html>") == []


def test_ignora_el_query_del_mailto():
    html = '<a href="mailto:info@x.com.uy?subject=Consulta">Mail</a>'
    assert extraer_mails(html) == ["info@x.com.uy"]
