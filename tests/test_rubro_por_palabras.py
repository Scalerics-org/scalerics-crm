"""El rubro que guarda Google no es la clave canonica.

Google no dice "inmobiliaria": dice "Agencia inmobiliaria", "Agentes
inmobiliarios", "Consultor inmobiliario", "Empresa de administracion de
propiedades". La busqueda por clave exacta fallaba con todos ellos y el mail
salia con la linea generica.

No es hipotetico: los primeros 30 mails de discovery salieron asi, porque en
produccion el `category` de esas filas era "Agencia inmobiliaria" y la prueba
manual se habia hecho pasando "Inmobiliaria".

El scraper ahora pisa `category` con la clave canonica (default_category), asi
que los rubros nuevos entran bien. Esto es para las filas viejas y para
cualquier cosa que entre por otra via.
"""

import pytest

from services.email_service import _LINEA_GENERICA, _linea_de_rubro
from services.rubros import rubro_de_texto

# Tal cual estan hoy en la tabla `businesses` de produccion.
_REALES_INMOBILIARIA = [
    "Agencia inmobiliaria",
    "Agencia de bienes inmuebles comerciales",
    "Agentes inmobiliarios",
    "Consultor inmobiliario",
    "Empresa de administración de propiedades",
    "Administrador de la propiedad",
    "Agencia de subastas inmobiliarias",
    "Agencia inmobiliaria especializada en alquileres",
    "Inmobiliaria",
]


@pytest.mark.parametrize("texto", _REALES_INMOBILIARIA)
def test_las_variantes_de_google_caen_en_inmobiliaria(texto):
    assert rubro_de_texto(texto) == "inmobiliaria"


@pytest.mark.parametrize("texto", _REALES_INMOBILIARIA)
def test_y_por_lo_tanto_reciben_la_linea_de_inmobiliaria(texto):
    assert _linea_de_rubro(texto) == _linea_de_rubro("inmobiliaria")
    assert _linea_de_rubro(texto) != _LINEA_GENERICA


@pytest.mark.parametrize("texto,esperado", [
    ("Clínica dental", "odontologia"),
    ("Dentista", "odontologia"),
    ("Peluquería canina", "veterinaria"),      # canina manda sobre peluqueria
    ("Salón de belleza", "peluqueria"),
    ("Barbería", "peluqueria"),
    ("Clínica veterinaria", "veterinaria"),
    ("Gimnasio", "gimnasio"),
    ("Ferretería", "ferreteria"),
    ("Venta de repuestos para automóviles", "repuestos"),
    ("Concesionario de automóviles", "automotora"),
    ("Restaurante", "restaurante"),
    ("Escribanía", "escribania"),
    ("Estudio contable", "contador"),
    ("Hotel", "hotel"),
    ("Imprenta", "imprenta"),
])
def test_otros_rubros_por_palabra(texto, esperado):
    assert rubro_de_texto(texto) == esperado


@pytest.mark.parametrize("texto", [
    "Oficinas de empresa", "Comercio", "Edificio de apartamentos",
    "Residencia de estudiantes", "Servicios de depósito de garantía",
    "", None, "asdfgh",
])
def test_lo_ambiguo_se_queda_sin_rubro(texto):
    """Adivinar mal es peor que no adivinar: la linea del rubro equivocado
    afirma algo falso sobre el negocio. Estos van a la generica a proposito."""
    assert rubro_de_texto(texto) == ""


def test_la_clave_canonica_sigue_funcionando():
    for clave in ("inmobiliaria", "peluqueria", "odontologia"):
        assert rubro_de_texto(clave) == clave
