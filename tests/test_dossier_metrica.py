"""El ladrillo del dossier: una metrica que se puede auditar.

Cada una trae su numerador, su denominador, su n y su intervalo. Sin eso, un
5% sobre 20 leads y un 5% sobre 2000 se leen igual, y no son lo mismo.
"""

import pytest

from services.dossier import MUESTRA_CHICA, metrica, proporcion, wilson


def test_wilson_sobre_una_muestra_conocida():
    bajo, alto = wilson(9, 83)
    assert bajo == pytest.approx(0.0581, abs=0.0005)
    assert alto == pytest.approx(0.1934, abs=0.0005)


def test_wilson_no_se_va_de_cero_a_uno():
    """Con 0 exitos el limite inferior no puede ser negativo."""
    bajo, alto = wilson(0, 20)
    assert bajo == 0.0
    assert alto < 1.0
    bajo, alto = wilson(20, 20)
    assert alto == 1.0
    assert bajo > 0.0


def test_wilson_sin_muestra_es_cero_a_uno():
    """Sin datos no se sabe nada: el intervalo es todo el rango."""
    assert wilson(0, 0) == (0.0, 1.0)


def test_wilson_se_angosta_con_mas_muestra():
    chico = wilson(5, 10)
    grande = wilson(500, 1000)
    assert (grande[1] - grande[0]) < (chico[1] - chico[0])


def test_proporcion_trae_todo_lo_que_hace_falta_para_auditarla():
    m = proporcion("campana.tasa_demo.uy", "Tasa de demo — UY", 9, 83, "crm")
    assert m["id"] == "campana.tasa_demo.uy"
    assert m["valor"] == pytest.approx(0.1084, abs=0.0001)
    assert m["numerador"] == 9
    assert m["denominador"] == 83
    assert m["n"] == 83
    assert m["fuente"] == "crm"
    assert m["formato"] == "porcentaje"
    assert len(m["ic95"]) == 2


def test_una_muestra_chica_se_marca():
    """Con 51 leads, cinco puntos de diferencia no significan nada."""
    assert proporcion("x", "X", 2, 20, "crm")["muestra_chica"] is True
    assert proporcion("x", "X", 20, 200, "crm")["muestra_chica"] is False
    assert MUESTRA_CHICA == 30


def test_proporcion_sin_denominador_vale_none_no_cero():
    m = proporcion("x", "X", 0, 0, "crm")
    assert m["valor"] is None
    assert m["muestra_chica"] is True


def test_metrica_calcula_el_delta_contra_el_periodo_anterior():
    m = metrica("x", "X", 120.0, "meta_insights", anterior=100.0)
    assert m["delta_periodo_anterior"] == 20.0


def test_metrica_sin_periodo_anterior_no_inventa_un_delta():
    """La primera corrida no tiene contra que comparar. Eso es None, no cero."""
    assert metrica("x", "X", 120.0, "meta_insights")["delta_periodo_anterior"] is None


def test_metrica_sin_valor_no_inventa_un_delta():
    assert metrica("x", "X", None, "derivada", anterior=10.0)["delta_periodo_anterior"] is None


def test_la_fuente_es_obligatoria_y_acotada():
    """No existe la fuente 'inferencia': las inferencias son del modelo y van
    en el informe, nunca mezcladas con los hechos."""
    with pytest.raises(ValueError):
        metrica("x", "X", 1.0, "inferencia")
    with pytest.raises(ValueError):
        metrica("x", "X", 1.0, "")
