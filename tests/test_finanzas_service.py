"""Conversión de moneda y aritmética de períodos.

Nada de esto toca la base ni Flask: es la parte que, si da un número mal, se
cree. Un total de egresos equivocado en un panel financiero es peor que un
bug de UI.
"""

import pytest

from services.finanzas import (CATEGORIAS, a_usd, meses_entre, periodo_anterior,
                               periodo_de)


def test_un_movimiento_en_dolares_no_se_convierte():
    assert a_usd(4.18, "USD", None) == 4.18


def test_un_movimiento_en_pesos_se_divide_por_el_tipo_de_cambio():
    assert a_usd(40000, "UYU", 40.0) == 1000.0


def test_pesos_sin_tipo_de_cambio_es_un_error():
    # Guardarlo con monto_usd=0 ensuciaría el mes en silencio.
    with pytest.raises(ValueError):
        a_usd(40000, "UYU", None)


def test_pesos_con_tipo_de_cambio_cero_o_negativo_es_un_error():
    with pytest.raises(ValueError):
        a_usd(40000, "UYU", 0)
    with pytest.raises(ValueError):
        a_usd(40000, "UYU", -40)


def test_una_moneda_que_no_existe_es_un_error():
    with pytest.raises(ValueError):
        a_usd(100, "EUR", None)


def test_el_periodo_sale_de_la_fecha():
    assert periodo_de("2026-09-20") == "2026-09"


def test_meses_entre_incluye_las_dos_puntas():
    assert meses_entre("2026-11", "2027-02") == ["2026-11", "2026-12",
                                                 "2027-01", "2027-02"]


def test_meses_entre_un_solo_mes():
    assert meses_entre("2026-09", "2026-09") == ["2026-09"]


def test_meses_entre_al_reves_da_vacio():
    assert meses_entre("2026-09", "2026-08") == []


def test_el_periodo_anterior_tiene_el_mismo_largo():
    # Tres meses (jul-sep) -> los tres anteriores (abr-jun).
    assert periodo_anterior("2026-07", "2026-09") == ("2026-04", "2026-06")


def test_el_periodo_anterior_de_un_mes_es_el_mes_de_antes():
    assert periodo_anterior("2026-01", "2026-01") == ("2025-12", "2025-12")


def test_las_categorias_no_se_pisan_entre_tipos():
    assert "infraestructura" in CATEGORIAS["egreso"]
    assert "desarrollo_web" in CATEGORIAS["ingreso"]
    assert "infraestructura" not in CATEGORIAS["ingreso"]
