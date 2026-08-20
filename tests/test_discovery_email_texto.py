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
    """Estos comercios YA tienen sitio: es el criterio con el que se los eligio."""
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t", 1)
    cuerpo = enviar.call_args.args[2].lower()
    assert "no tenés página" not in cuerpo and "no tenes pagina" not in cuerpo


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
