"""IVA: cuánto se le suma a lo que se carga, y cuánto queda a pagar o a favor.

**El monto que se escribe es el LÍQUIDO, no el total.** Es como se acuerda un
precio y como llegan los gastos acá: se escribe 100 y el IVA son 22 aparte, con
un total de 122.

La primera versión hacía lo contrario —tomaba el monto como total y sacaba el
impuesto de adentro, 100 → 81,97 + 18,03— y estaba mal. Salió probándolo con un
gasto real: nadie escribe el número con el IVA ya metido. Estos tests fijan el
sentido correcto para que no se vuelva a dar vuelta.

**El IVA se GUARDA, no se recalcula al leer.** Si algún día cambia la tasa, los
movimientos viejos tienen que seguir mostrando el impuesto que de verdad se
facturó. Un `monto * 0.22` al vuelo reescribiría la historia.

El saldo a favor se arrastra al mes siguiente porque en Uruguay no vence. El
saldo a pagar no se arrastra: se paga y queda en cero.
"""

import pytest

from database import crear_movimiento, get_movimiento, init_db
from services.finanzas import IVA_TASA, desglosar_iva_incluido, iva_sobre, resumen_iva


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "f.db")
    init_db(ruta)
    return ruta


def _mov(db, tipo, concepto, monto, periodo="2026-09", facturado=True):
    return crear_movimiento(
        db, tipo=tipo, fecha=f"{periodo}-15", periodo=periodo, concepto=concepto,
        categoria="servicios", monto=monto, moneda="USD", monto_usd=monto,
        facturado=1 if facturado else 0,
        iva_usd=iva_sobre(monto) if facturado else 0.0)


# ── el impuesto se suma, no se saca de adentro ───────────────────────────────

def test_el_iva_se_suma_al_monto_cargado(db):
    """100 son 100 propios más 22 de impuesto: total 122, no 81,97 + 18,03."""
    assert iva_sobre(100) == 22.0


@pytest.mark.parametrize("liquido,iva", [
    (100, 22.0), (250, 55.0), (500, 110.0), (1000, 220.0),
])
def test_los_numeros_dan_redondos(db, liquido, iva):
    """Que den redondo no es casualidad: es la señal de que el monto que se
    escribe es el que la gente tiene en la cabeza."""
    assert round(iva_sobre(liquido), 2) == iva


def test_la_tasa_es_la_basica_de_uruguay(db):
    assert IVA_TASA == 0.22


def test_un_monto_vacio_no_explota(db):
    assert iva_sobre(0) == 0
    assert iva_sobre(None) == 0


# ── cuando el precio ya viene con el IVA adentro (pedido de Juan, 22/9) ─────

def test_desglosar_iva_incluido_separa_neto_e_iva_de_un_total(db):
    """122 con IVA incluido son 100 de neto más 22 de IVA, no al revés."""
    assert desglosar_iva_incluido(122) == (100.0, 22.0)


@pytest.mark.parametrize("total,neto,iva", [
    (122, 100.0, 22.0), (610, 500.0, 110.0), (1220, 1000.0, 220.0),
])
def test_desglosar_iva_incluido_reconstruye_el_total_exacto(db, total, neto, iva):
    """Que neto + iva vuelva a dar el total es lo que garantiza que el saldo
    de IVA no se corra ni un centavo por redondeo."""
    n, i = desglosar_iva_incluido(total)
    assert (n, i) == (neto, iva)
    assert round(n + i, 2) == total


def test_desglosar_iva_incluido_con_vacio_no_explota(db):
    assert desglosar_iva_incluido(0) == (0.0, 0.0)
    assert desglosar_iva_incluido(None) == (0.0, 0.0)


def test_un_movimiento_con_iva_incluido_guarda_el_neto_separado(db):
    """El monto que se tipea (122, "con IVA") no cambia: lo que se separa es
    monto_usd (neto) e iva_usd, para que sigan sumando lo mismo que antes."""
    mid = crear_movimiento(
        db, tipo="ingreso", fecha="2026-09-15", periodo="2026-09",
        concepto="Cobro con IVA incluido", categoria="desarrollo_web",
        monto=122, moneda="USD", monto_usd=100.0, facturado=1, iva_usd=22.0,
        iva_incluido=1)

    mov = get_movimiento(db, mid)
    assert mov["monto"] == 122, "lo que se tipeó no se toca"
    assert mov["monto_usd"] == 100.0 and mov["iva_usd"] == 22.0
    assert mov["iva_incluido"] == 1
    assert mov["monto_usd"] + mov["iva_usd"] == 122.0


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
    assert round(mov["iva_usd"], 2) == 110.0
    assert mov["facturado"] == 1


# ── lo que dibuja la pestaña ─────────────────────────────────────────────────

def test_el_desglose_va_liquido_iva_y_total(db):
    """Concepto / Neto / IVA / Total, con el total sumando los dos."""
    _mov(db, "ingreso", "Cobro 50% La Vaca Encantada", 500)

    fila = resumen_iva(db, "2026-09")["movimientos"][0]

    assert fila["neto"] == 500
    assert round(fila["iva"], 2) == 110.0
    assert round(fila["total"], 2) == 610.0


def test_un_movimiento_sin_factura_no_aparece(db):
    _mov(db, "ingreso", "Con factura", 500)
    _mov(db, "egreso", "Sin factura", 999, facturado=False)

    filas = resumen_iva(db, "2026-09")["movimientos"]

    assert [f["concepto"] for f in filas] == ["Con factura"]


def test_un_mes_sin_nada_da_cero_y_no_explota(db):
    r = resumen_iva(db, "2026-09")

    assert r["iva_cobrado"] == 0
    assert r["iva_pagado"] == 0
    assert r["saldo"] == 0
    assert r["movimientos"] == []


# ── el saldo del mes ─────────────────────────────────────────────────────────

def test_el_saldo_es_lo_cobrado_menos_lo_pagado(db):
    _mov(db, "ingreso", "Cobro", 500)      # iva 110
    _mov(db, "egreso", "Hosting", 250)     # iva 55

    r = resumen_iva(db, "2026-09")

    assert round(r["iva_cobrado"], 2) == 110.0
    assert round(r["iva_pagado"], 2) == 55.0
    assert round(r["saldo"], 2) == 55.0


def test_los_no_facturados_no_entran_al_saldo(db):
    _mov(db, "ingreso", "Cobro con factura", 500)
    _mov(db, "ingreso", "Cobro sin factura", 500, facturado=False)
    _mov(db, "egreso", "Gasto sin factura", 300, facturado=False)

    r = resumen_iva(db, "2026-09")

    assert round(r["iva_cobrado"], 2) == 110.0
    assert r["iva_pagado"] == 0


def test_los_movimientos_anulados_no_cuentan(db):
    from database import actualizar_movimiento
    mid = _mov(db, "ingreso", "Cobro anulado", 500)
    actualizar_movimiento(db, mid, anulado=1)
    _mov(db, "ingreso", "Cobro bueno", 200)

    r = resumen_iva(db, "2026-09")

    assert round(r["iva_cobrado"], 2) == 44.0
    assert len(r["movimientos"]) == 1


# ── el arrastre del saldo a favor ────────────────────────────────────────────

def test_un_saldo_a_favor_se_arrastra_al_mes_siguiente(db):
    """En Uruguay el crédito de IVA no vence."""
    _mov(db, "egreso", "Compra grande", 1000, periodo="2026-08")   # paga 220
    _mov(db, "ingreso", "Cobro chico", 200, periodo="2026-09")     # cobra 44

    assert round(resumen_iva(db, "2026-08")["saldo"], 2) == -220.0
    septiembre = resumen_iva(db, "2026-09")
    assert round(septiembre["arrastre"], 2) == -220.0
    assert round(septiembre["saldo"], 2) == -176.0, "44 - 220"


def test_un_saldo_a_pagar_no_se_arrastra(db):
    """Lo que se debe se paga: no queda colgando al mes siguiente."""
    _mov(db, "ingreso", "Cobro grande", 1000, periodo="2026-08")
    _mov(db, "ingreso", "Cobro chico", 200, periodo="2026-09")

    assert round(resumen_iva(db, "2026-08")["saldo"], 2) == 220.0
    septiembre = resumen_iva(db, "2026-09")
    assert septiembre["arrastre"] == 0
    assert round(septiembre["saldo"], 2) == 44.0


def test_el_arrastre_a_favor_se_acumula_por_varios_meses(db):
    _mov(db, "egreso", "Compra 1", 1000, periodo="2026-07")   # -220
    _mov(db, "egreso", "Compra 2", 1000, periodo="2026-08")   # -220 mas arrastre
    _mov(db, "ingreso", "Cobro", 100, periodo="2026-09")      # +22

    assert round(resumen_iva(db, "2026-07")["saldo"], 2) == -220.0
    assert round(resumen_iva(db, "2026-08")["saldo"], 2) == -440.0
    assert round(resumen_iva(db, "2026-09")["saldo"], 2) == -418.0


def test_un_saldo_a_favor_se_consume_y_deja_lo_que_sobra_a_pagar(db):
    _mov(db, "egreso", "Compra", 500, periodo="2026-08")           # -110 a favor
    _mov(db, "ingreso", "Cobro grande", 1000, periodo="2026-09")   # +220

    septiembre = resumen_iva(db, "2026-09")

    assert round(septiembre["arrastre"], 2) == -110.0
    assert round(septiembre["saldo"], 2) == 110.0, "220 - 110, queda a pagar"


def test_un_mes_sin_movimientos_en_el_medio_no_corta_el_arrastre(db):
    _mov(db, "egreso", "Compra", 1000, periodo="2026-07")
    # agosto vacio a proposito
    _mov(db, "ingreso", "Cobro", 100, periodo="2026-09")

    assert round(resumen_iva(db, "2026-08")["saldo"], 2) == -220.0
    assert round(resumen_iva(db, "2026-09")["arrastre"], 2) == -220.0
