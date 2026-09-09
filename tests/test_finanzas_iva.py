"""IVA: cuánto de lo que entra y sale es impuesto, y cuánto es plata de verdad.

Hasta ahora un movimiento de USD 500 contaba como USD 500 de ingreso, cuando en
realidad son USD 410 propios y USD 90 que hay que devolverle a la DGI. Eso ya
generó errores de caja reales.

Dos decisiones que estos tests fijan:

**El monto que se carga es el TOTAL, con IVA adentro.** Es como llega la
factura y como se cobra. Neto e IVA se derivan hacia atrás: 500 → 410 + 90, no
500 → 500 + 110.

**El IVA se GUARDA, no se recalcula al leer.** Si algún día cambia la tasa, los
movimientos viejos tienen que seguir mostrando el impuesto que de verdad se
facturó. Un `monto * 0.22` al vuelo reescribiría la historia.

El saldo a favor se arrastra al mes siguiente porque en Uruguay no vence. El
saldo a pagar no se arrastra: se paga y queda en cero.
"""

import pytest

from database import (crear_movimiento, get_movimiento, init_db)
from services.finanzas import IVA_TASA, desglosar_iva, resumen_iva


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "f.db")
    init_db(ruta)
    return ruta


def _mov(db, tipo, concepto, monto, periodo="2026-09", facturado=True):
    neto, iva = desglosar_iva(monto) if facturado else (monto, 0.0)
    return crear_movimiento(
        db, tipo=tipo, fecha=f"{periodo}-15", periodo=periodo, concepto=concepto,
        categoria="servicios", monto=monto, moneda="USD", monto_usd=monto,
        facturado=1 if facturado else 0, iva_usd=iva)


# ── el desglose ──────────────────────────────────────────────────────────────

def test_el_monto_cargado_es_el_total_con_iva_adentro(db):
    """500 son 410 propios + 90 de impuesto, no 500 + 110."""
    neto, iva = desglosar_iva(500)

    assert round(neto + iva, 2) == 500.0
    assert round(neto, 2) == 409.84
    assert round(iva, 2) == 90.16


def test_la_tasa_es_la_basica_de_uruguay(db):
    assert IVA_TASA == 0.22


@pytest.mark.parametrize("total", [0, 1, 123.45, 999999.99])
def test_neto_mas_iva_siempre_da_el_total(db, total):
    neto, iva = desglosar_iva(total)
    assert round(neto + iva, 2) == round(total, 2)


def test_un_movimiento_sin_factura_no_tiene_iva(db):
    """Un gasto que pagó alguien del equipo de su bolsillo no descuenta IVA."""
    mid = _mov(db, "egreso", "Almuerzo del equipo", 100, facturado=False)

    mov = get_movimiento(db, mid)
    assert mov["facturado"] == 0
    assert mov["iva_usd"] == 0


# ── el iva se guarda, no se recalcula ────────────────────────────────────────

def test_el_iva_queda_guardado_en_la_fila(db):
    """Para que un cambio de tasa no reescriba lo ya facturado."""
    mid = _mov(db, "ingreso", "Cobro 50% La Vaca Encantada", 500)

    mov = get_movimiento(db, mid)
    assert round(mov["iva_usd"], 2) == 90.16
    assert mov["facturado"] == 1


# ── el saldo del mes ─────────────────────────────────────────────────────────

def test_el_saldo_es_lo_cobrado_menos_lo_pagado(db):
    _mov(db, "ingreso", "Cobro", 500)      # iva 90.16
    _mov(db, "egreso", "Hosting", 250)     # iva 45.08

    r = resumen_iva(db, "2026-09")

    assert round(r["iva_cobrado"], 2) == 90.16
    assert round(r["iva_pagado"], 2) == 45.08
    assert round(r["saldo"], 2) == 45.08


def test_los_no_facturados_no_entran_al_saldo(db):
    _mov(db, "ingreso", "Cobro con factura", 500)
    _mov(db, "ingreso", "Cobro sin factura", 500, facturado=False)
    _mov(db, "egreso", "Gasto sin factura", 300, facturado=False)

    r = resumen_iva(db, "2026-09")

    assert round(r["iva_cobrado"], 2) == 90.16
    assert r["iva_pagado"] == 0


def test_un_mes_sin_nada_da_cero_y_no_explota(db):
    r = resumen_iva(db, "2026-09")

    assert r["iva_cobrado"] == 0
    assert r["iva_pagado"] == 0
    assert r["saldo"] == 0
    assert r["movimientos"] == []


def test_lista_los_movimientos_facturados_con_su_desglose(db):
    """Es lo que dibuja la pestaña: Concepto / Neto / IVA / Total."""
    _mov(db, "ingreso", "Cobro 50% La Vaca Encantada", 500)
    _mov(db, "egreso", "Gastos SAS", 250)
    _mov(db, "egreso", "Sin factura", 999, facturado=False)

    filas = resumen_iva(db, "2026-09")["movimientos"]

    assert len(filas) == 2
    por_concepto = {f["concepto"]: f for f in filas}
    vaca = por_concepto["Cobro 50% La Vaca Encantada"]
    assert round(vaca["neto"], 2) == 409.84
    assert round(vaca["iva"], 2) == 90.16
    assert round(vaca["total"], 2) == 500.0
    assert vaca["tipo"] == "ingreso"


# ── el arrastre del saldo a favor ────────────────────────────────────────────

def test_un_saldo_a_favor_se_arrastra_al_mes_siguiente(db):
    """En Uruguay el crédito de IVA no vence."""
    _mov(db, "egreso", "Compra grande", 1000, periodo="2026-08")   # iva 180.33 pagado
    _mov(db, "ingreso", "Cobro chico", 200, periodo="2026-09")     # iva 36.07 cobrado

    agosto = resumen_iva(db, "2026-08")
    septiembre = resumen_iva(db, "2026-09")

    assert round(agosto["saldo"], 2) == -180.33, "agosto queda a favor"
    assert round(septiembre["arrastre"], 2) == -180.33
    assert round(septiembre["saldo"], 2) == -144.26, "36.07 - 180.33"


def test_un_saldo_a_pagar_no_se_arrastra(db):
    """Lo que se debe se paga: no queda colgando al mes siguiente."""
    _mov(db, "ingreso", "Cobro grande", 1000, periodo="2026-08")
    _mov(db, "ingreso", "Cobro chico", 200, periodo="2026-09")

    assert round(resumen_iva(db, "2026-08")["saldo"], 2) == 180.33
    septiembre = resumen_iva(db, "2026-09")
    assert septiembre["arrastre"] == 0
    assert round(septiembre["saldo"], 2) == 36.07


def test_el_arrastre_a_favor_se_acumula_por_varios_meses(db):
    _mov(db, "egreso", "Compra 1", 1000, periodo="2026-07")   # -180.33
    _mov(db, "egreso", "Compra 2", 1000, periodo="2026-08")   # -180.33 mas arrastre
    _mov(db, "ingreso", "Cobro", 100, periodo="2026-09")      # +18.03

    assert round(resumen_iva(db, "2026-07")["saldo"], 2) == -180.33
    assert round(resumen_iva(db, "2026-08")["saldo"], 2) == -360.66
    assert round(resumen_iva(db, "2026-09")["saldo"], 2) == -342.62


def test_un_saldo_a_favor_se_consume_y_no_queda_negativo_de_mas(db):
    """Si el crédito alcanza para tapar el mes, el saldo queda a favor por la
    diferencia; si sobra deuda, se paga."""
    _mov(db, "egreso", "Compra", 500, periodo="2026-08")      # -90.16 a favor
    _mov(db, "ingreso", "Cobro grande", 1000, periodo="2026-09")  # +180.33

    septiembre = resumen_iva(db, "2026-09")

    assert round(septiembre["arrastre"], 2) == -90.16
    assert round(septiembre["saldo"], 2) == 90.16, "180.328 - 90.164, queda a pagar"


def test_un_mes_sin_movimientos_en_el_medio_no_corta_el_arrastre(db):
    _mov(db, "egreso", "Compra", 1000, periodo="2026-07")
    # agosto vacio a proposito
    _mov(db, "ingreso", "Cobro", 100, periodo="2026-09")

    assert round(resumen_iva(db, "2026-08")["saldo"], 2) == -180.33
    assert round(resumen_iva(db, "2026-09")["arrastre"], 2) == -180.33


def test_los_movimientos_anulados_no_cuentan(db):
    from database import actualizar_movimiento
    mid = _mov(db, "ingreso", "Cobro anulado", 500)
    actualizar_movimiento(db, mid, anulado=1)
    _mov(db, "ingreso", "Cobro bueno", 200)

    r = resumen_iva(db, "2026-09")

    assert round(r["iva_cobrado"], 2) == 36.07
    assert len(r["movimientos"]) == 1
