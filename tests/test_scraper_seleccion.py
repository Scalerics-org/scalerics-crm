"""La decision de guardar o descartar un negocio, en los dos modos del scraper."""

import pytest

from scraper import _debe_guardar


def _negocio(**extra):
    base = {"name": "Inmobiliaria Ejemplo", "phone": "+598 2900 1111"}
    base.update(extra)
    return base


def test_sin_telefono_nunca_se_guarda():
    """Sin telefono no hay forma de contactarlo, en ningun modo."""
    for solo_con_web in (False, True):
        guardar, motivo = _debe_guardar(
            _negocio(phone="", maps_website_url="https://x.com.uy"),
            solo_con_web=solo_con_web, skip_branded=False)
        assert guardar is False
        assert "teléfono" in motivo


def test_modo_por_defecto_descarta_al_que_tiene_web():
    guardar, motivo = _debe_guardar(
        _negocio(maps_website_url="https://inmobiliaria.com.uy"),
        solo_con_web=False, skip_branded=False)
    assert guardar is False
    assert "con web" in motivo


def test_modo_por_defecto_guarda_al_que_no_tiene_web():
    guardar, motivo = _debe_guardar(
        _negocio(maps_website_url=None), solo_con_web=False, skip_branded=False)
    assert guardar is True
    assert motivo == ""


def test_modo_discovery_guarda_al_que_tiene_web():
    guardar, motivo = _debe_guardar(
        _negocio(maps_website_url="https://inmobiliaria.com.uy"),
        solo_con_web=True, skip_branded=False)
    assert guardar is True
    assert motivo == ""


def test_modo_discovery_descarta_al_que_no_tiene_web():
    """Es el criterio invertido: sin web no hay de donde sacar el mail."""
    guardar, motivo = _debe_guardar(
        _negocio(maps_website_url=None), solo_con_web=True, skip_branded=False)
    assert guardar is False
    assert "sin web" in motivo


def test_franquicia_se_descarta_solo_si_se_pidio():
    """skip_branded sigue valiendo en los dos modos, y sigue siendo opt-in."""
    chevrolet = _negocio(name="Chevrolet Montevideo",
                         maps_website_url="https://chevrolet.com.uy")

    guardar, motivo = _debe_guardar(chevrolet, solo_con_web=True, skip_branded=True)
    assert guardar is False
    assert "franquicia" in motivo

    guardar, _ = _debe_guardar(chevrolet, solo_con_web=True, skip_branded=False)
    assert guardar is True


def test_franquicia_se_descarta_solo_si_se_pidio_tambien_en_el_modo_por_defecto():
    """skip_branded es ortogonal al modo: el padron de WhatsApp lo usa igual."""
    chevrolet = _negocio(name="Chevrolet Montevideo", maps_website_url=None)

    guardar, motivo = _debe_guardar(chevrolet, solo_con_web=False, skip_branded=True)
    assert guardar is False
    assert "franquicia" in motivo

    guardar, _ = _debe_guardar(chevrolet, solo_con_web=False, skip_branded=False)
    assert guardar is True
