"""Las definiciones del embudo viven en un solo lugar.

Finanzas y el modulo de marketing tienen que contar una demo igual. Si cada uno
define lo suyo, el CRM muestra dos numeros distintos para la misma pregunta en
dos paneles y nadie sabe a cual creerle.
"""

from services.embudo import (FUNNEL, EXCLUIDOS_DEL_FUNNEL, alcanzo, costo,
                             dividir, normalizar_estado)


def test_el_orden_del_embudo_no_cambia():
    assert FUNNEL[0] == "sin_contactar"
    assert FUNNEL[-1] == "finalizado"
    assert FUNNEL.index("demo_agendada") < FUNNEL.index("demo_1")
    assert FUNNEL.index("demo_1") < FUNNEL.index("presupuesto_enviado")
    assert FUNNEL.index("presupuesto_enviado") < FUNNEL.index("cerrado")


def test_en_espera_y_rechazo_no_son_avances():
    assert set(EXCLUIDOS_DEL_FUNNEL) == {"en_espera", "rechazo"}
    assert "en_espera" not in FUNNEL
    assert "rechazo" not in FUNNEL


def test_normalizar_traduce_el_vocabulario_viejo():
    assert normalizar_estado("reunion_agendada") == "demo_agendada"
    assert normalizar_estado("reunion_hecha") == "demo_1"
    assert normalizar_estado("cliente_cerrado") == "cerrado"
    assert normalizar_estado("agendo") == "demo_agendada"
    assert normalizar_estado("firmo") == "cerrado"


def test_normalizar_deja_pasar_lo_que_ya_es_nuevo():
    assert normalizar_estado("demo_1") == "demo_1"
    assert normalizar_estado("cualquier_cosa") == "cualquier_cosa"


def test_alcanzo_cuenta_una_etapa_posterior():
    """Llegar a cerrado implica haber alcanzado demo_1."""
    assert alcanzo({"cerrado"}, "demo_1") is True


def test_alcanzo_no_cuenta_una_etapa_anterior():
    assert alcanzo({"interesado"}, "demo_1") is False


def test_alcanzo_mezcla_las_dos_epocas():
    """lead_events trae nombres viejos y nuevos mezclados en la misma base."""
    assert alcanzo({"reunion_hecha"}, "demo_1") is True


def test_alcanzo_cuenta_al_lead_que_despues_se_cayo():
    """El que llego a demo y hoy figura en no_interesa hizo la demo igual."""
    assert alcanzo({"demo_1", "no_interesa"}, "demo_1") is True


def test_alcanzo_sin_eventos():
    assert alcanzo(set(), "demo_1") is False


def test_dividir_sin_denominador_es_none():
    assert dividir(10, 0) is None
    assert dividir(10, 4) == 2.5


def test_costo_sin_cantidad_es_none():
    """Un mes sin ventas no tiene costo por venta de cero: no tiene."""
    assert costo(300.0, 0) is None


def test_costo_sin_inversion_es_none():
    """Sin campana que costear tampoco es un costo de cero."""
    assert costo(0.0, 5) is None


def test_costo_normal():
    assert costo(300.0, 4) == 75.0
