"""La linea de apertura de cada rubro, en sus tres niveles.

El mail en frio no puede afirmar nada del negocio que lo recibe: no miramos su
sitio, solo sabemos que existe. Lo unico concreto y cierto que tenemos es el
RUBRO, asi que la apertura habla de como trabaja ese rubro —siempre en
condicional— y nunca de ellos.

Con 48 rubros no se pueden escribir 48 textos sin que se noten hechos a
desgano, pero la generica no dice nada. De ahi los tres niveles: linea propia
del rubro si la tiene, si no la de su familia (que se hace a mano ahi), y la
generica solo como red para un rubro que no este en la lista.
"""

import pytest

from services.email_service import (_LINEA_GENERICA, _LINEAS_POR_FAMILIA,
                                    _LINEAS_POR_RUBRO, _linea_de_rubro)
from services.rubros import FAMILIAS, RUBROS


# ─── Los tres niveles ─────────────────────────────────────────────────────────

def test_un_rubro_con_linea_propia_usa_la_suya():
    """La propia es mas especifica que la de la familia y tiene que ganar."""
    assert _linea_de_rubro("peluqueria") == _LINEAS_POR_RUBRO["peluqueria"]


def test_un_rubro_sin_linea_propia_usa_la_de_su_familia():
    assert "escribania" not in _LINEAS_POR_RUBRO
    assert _linea_de_rubro("escribania") == _LINEAS_POR_FAMILIA["expedientes"]


def test_un_rubro_desconocido_cae_en_la_generica():
    assert _linea_de_rubro("taxidermista") == _LINEA_GENERICA
    assert _linea_de_rubro("") == _LINEA_GENERICA
    assert _linea_de_rubro(None) == _LINEA_GENERICA


# ─── La garantia que importa ──────────────────────────────────────────────────

@pytest.mark.parametrize("rubro", RUBROS, ids=lambda r: r.clave)
def test_ningun_rubro_de_la_lista_cae_en_la_generica(rubro):
    """Si un rubro se scrapea, se le escribe. Este test es lo que hace que
    agregar un rubro a rubros.py sin darle texto se vea aca y no cuando el mail
    ya salio."""
    assert _linea_de_rubro(rubro.clave) != _LINEA_GENERICA


def test_todas_las_familias_tienen_linea():
    assert set(_LINEAS_POR_FAMILIA) == set(FAMILIAS)


# ─── Como estan escritas ──────────────────────────────────────────────────────

_TODAS = list(_LINEAS_POR_RUBRO.values()) + list(_LINEAS_POR_FAMILIA.values())


@pytest.mark.parametrize("linea", _TODAS)
def test_ninguna_afirma_nada_del_negocio(linea):
    """No miramos su sitio. Cualquier frase en indicativo sobre ellos es mentira
    y se nota. Todas tienen que ir en condicional."""
    assert linea.lstrip().startswith("Si "), linea


@pytest.mark.parametrize("linea", _TODAS)
def test_ninguna_promete_ni_vende_en_la_apertura(linea):
    prohibidas = ["te ofrecemos", "aprovecha", "oferta", "descuento", "gratis",
                  "no te pierdas", "unico", "increible"]
    bajo = linea.lower()
    for p in prohibidas:
        assert p not in bajo, f"{p!r} en: {linea}"


@pytest.mark.parametrize("linea", _TODAS)
def test_ninguna_es_larga_de_mas(linea):
    """Es la primera frase del mail: si no entra de un vistazo, no se lee."""
    assert len(linea) <= 190, f"{len(linea)} caracteres: {linea}"


def test_no_hay_dos_familias_con_el_mismo_texto():
    """Dos familias con la misma linea significa que una de las dos no estaba
    pensada."""
    textos = list(_LINEAS_POR_FAMILIA.values())
    assert len(set(textos)) == len(textos)
