"""Los dos textos del correo en frio y sus cabeceras.

El remitente es la guarda que mas importa: si `DISCOVERY_FROM_EMAIL` no esta,
la funcion NO manda. Una configuracion a medias no puede terminar mandando en
frio desde scalerics.com, que es el dominio con el que se le escribe a los
clientes y por el que salen los recordatorios de Meta.
"""

import os
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from services.email_service import send_discovery_email

_REMITENTE = "Scalerics <hola@novedades.scalerics.com>"


@contextmanager
def _capturar():
    anterior = os.environ.get("DISCOVERY_FROM_EMAIL")
    os.environ["DISCOVERY_FROM_EMAIL"] = _REMITENTE
    try:
        with patch("services.email_service._send_estado", return_value="ok") as enviar:
            yield enviar
    finally:
        if anterior is None:
            os.environ.pop("DISCOVERY_FROM_EMAIL", None)
        else:
            os.environ["DISCOVERY_FROM_EMAIL"] = anterior


def test_sin_remitente_configurado_no_manda(monkeypatch):
    monkeypatch.delenv("DISCOVERY_FROM_EMAIL", raising=False)
    with patch("services.email_service._send_estado") as enviar:
        estado = send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t")
        enviar.assert_not_called()
    assert estado == "fallo"


def test_sale_del_subdominio_configurado():
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t")
    assert "novedades.scalerics.com" in enviar.call_args.kwargs["from_email"]
    assert "@scalerics.com>" not in enviar.call_args.kwargs["from_email"]


def test_las_respuestas_van_a_contacto():
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t")
    assert enviar.call_args.kwargs["headers"]["Reply-To"] == "contacto@scalerics.com"


@pytest.mark.parametrize("numero,esperado", [(1, "Vimos el sitio"), (2, "una sola vez")])
def test_cada_contacto_dice_algo_distinto(numero, esperado):
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t", numero)
    assert esperado in enviar.call_args.args[2]


def test_el_segundo_avisa_que_es_el_ultimo():
    """Tiene que cumplir lo que dice: despues de ese, el comercio no vuelve a
    entrar nunca."""
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t", 2)
    html = enviar.call_args.args[2]
    assert "no te escribimos m" in html.lower()


def test_el_primero_no_ofrece_una_pagina_web():
    """Estos comercios YA tienen sitio: es el criterio con el que se los eligio.

    Se compara sobre el texto plano y no sobre el HTML: el HTML escapa las
    tildes a entidades, asi que buscar "pagina" con tilde ahi nunca puede dar
    positivo y el test pasaria con cualquier texto.
    """
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t", 1)
    texto = enviar.call_args.kwargs["text"].lower()
    assert "no tenés página" not in texto
    assert "no tenes pagina" not in texto
    assert "página web" not in texto


def test_los_dos_llevan_baja_y_cabecera_de_baja():
    for numero in (1, 2):
        with _capturar() as enviar:
            send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/tok", numero)
        html = enviar.call_args.args[2]
        texto = enviar.call_args.kwargs["text"]
        assert "https://c/baja/tok" in html, f"contacto {numero} sin link de baja"
        assert "https://c/baja/tok" in texto, f"contacto {numero} sin baja en el texto"
        assert enviar.call_args.kwargs["headers"]["List-Unsubscribe"] == "<https://c/baja/tok>"
        assert enviar.call_args.kwargs["headers"]["List-Unsubscribe-Post"] == \
            "List-Unsubscribe=One-Click"


def test_el_nombre_del_comercio_se_escapa():
    """`name` sale de Google Maps: lo escribe cualquiera."""
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", "Inmo <script>alert(1)</script>", "Inmobiliaria",
                             "https://c/baja/t")
    assert "<script>" not in enviar.call_args.args[2]


def test_los_dos_cuerpos_son_distintos():
    cuerpos = []
    for numero in (1, 2):
        with _capturar() as enviar:
            send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t", numero)
        cuerpos.append(enviar.call_args.args[2])
    assert cuerpos[0] != cuerpos[1]


def test_el_texto_plano_no_lleva_entidades_html():
    """El asunto y la parte en texto van sin escapar: si no, el lector ve
    &aacute; en vez de una tilde."""
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t", 2)
    assert "&" not in enviar.call_args.args[1], "entidad HTML en el asunto"
    assert "&aacute;" not in enviar.call_args.kwargs["text"]


def test_numero_none_no_revienta():
    with _capturar() as enviar:
        assert send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria",
                                    "https://c/baja/t", None) == "ok"


# ─── Lo que la revision encontro abierto ─────────────────────────────────────

@pytest.mark.parametrize("negocio", ["Inmobiliaria Sur", "ACSA", "Mas Aguada"])
def test_el_asunto_del_primero_esta_bien_escrito(negocio):
    """El asunto es la linea mas visible de todo mail en frio. La version vieja
    armaba "Una idea para de Inmobiliaria Sur", pegando dos preposiciones."""
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", negocio, "Inmobiliaria", "https://c/baja/t", 1)
    asunto = enviar.call_args.args[1]
    assert asunto == f"Una idea para {negocio}"
    assert " para de " not in asunto
    assert "  " not in asunto


def test_el_asunto_del_segundo_esta_bien_escrito():
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", "Inmobiliaria Sur", "Inmobiliaria",
                             "https://c/baja/t", 2)
    assert enviar.call_args.args[1] == "Último mail de Inmobiliaria Sur"


def test_sin_negocio_los_asuntos_siguen_teniendo_sentido():
    for numero, esperado in ((1, "Una idea para tu negocio"), (2, "Último mail de Scalerics")):
        with _capturar() as enviar:
            send_discovery_email("x@y.uy", "", "Inmobiliaria", "https://c/baja/t", numero)
        assert enviar.call_args.args[1] == esperado


def test_no_manda_desde_el_dominio_principal(monkeypatch):
    """Un dedo torcido en el `flyctl secrets set` pondria el correo en frio en
    el mismo dominio con el que se le escribe a los clientes."""
    for remitente in ("contacto@scalerics.com",
                      "Scalerics <crm@scalerics.com>",
                      "hola@SCALERICS.COM"):
        monkeypatch.setenv("DISCOVERY_FROM_EMAIL", remitente)
        with patch("services.email_service._send_estado") as enviar:
            assert send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria",
                                        "https://c/baja/t") == "fallo"
            enviar.assert_not_called()


def test_un_subdominio_si_puede_mandar(monkeypatch):
    """La guarda es sobre el dominio exacto, no sobre la cadena: un subdominio
    de scalerics.com es justamente lo que la campana tiene que usar."""
    monkeypatch.setenv("DISCOVERY_FROM_EMAIL", "Scalerics <hola@novedades.scalerics.com>")
    with patch("services.email_service._send_estado", return_value="ok") as enviar:
        assert send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria",
                                    "https://c/baja/t") == "ok"
        enviar.assert_called_once()


def test_un_remitente_sin_arroba_no_manda(monkeypatch):
    monkeypatch.setenv("DISCOVERY_FROM_EMAIL", "basura sin arroba")
    with patch("services.email_service._send_estado") as enviar:
        assert send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria",
                                    "https://c/baja/t") == "fallo"
        enviar.assert_not_called()
