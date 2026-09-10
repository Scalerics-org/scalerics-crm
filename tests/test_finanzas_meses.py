"""Navegar a un mes anterior, y que un mes cerrado no se toque sin querer.

El spec pide que los meses pasados esten "por default no editables, para evitar
cambios accidentales", con un boton para reabrirlos si hace falta. O sea que lo
que se persiste NO son los meses cerrados sino los REABIERTOS: cerrar es la
regla y abrir es la excepcion. Si fuera al reves habria que acordarse de cerrar
cada mes, y el mes que nadie cierre queda editable para siempre.
"""

import pytest

from database import (crear_movimiento, init_db, listar_meses_abiertos,
                      marcar_mes_abierto, marcar_mes_cerrado)
from services.finanzas import mes_editable


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "m.db")
    init_db(ruta)
    return ruta


HOY = "2026-09-15"


# ── que mes se puede editar ──────────────────────────────────────────────────

def test_el_mes_en_curso_se_edita(db):
    assert mes_editable(db, "2026-09", hoy=HOY) is True


def test_un_mes_futuro_se_edita(db):
    """Cargar algo con fecha adelantada es raro pero no es un error de este
    modulo: lo que se protege es lo ya cerrado."""
    assert mes_editable(db, "2026-10", hoy=HOY) is True


def test_un_mes_pasado_esta_cerrado(db):
    assert mes_editable(db, "2026-08", hoy=HOY) is False


def test_un_mes_pasado_reabierto_se_edita(db):
    marcar_mes_abierto(db, "2026-08", quien="Gonza")

    assert mes_editable(db, "2026-08", hoy=HOY) is True


def test_reabrir_y_volver_a_cerrar(db):
    marcar_mes_abierto(db, "2026-08", quien="Gonza")
    marcar_mes_cerrado(db, "2026-08")

    assert mes_editable(db, "2026-08", hoy=HOY) is False


def test_reabrir_dos_veces_no_duplica(db):
    marcar_mes_abierto(db, "2026-08", quien="Gonza")
    marcar_mes_abierto(db, "2026-08", quien="Juan")

    assert len(listar_meses_abiertos(db)) == 1


def test_reabrir_un_mes_no_abre_los_demas(db):
    marcar_mes_abierto(db, "2026-08", quien="Gonza")

    assert mes_editable(db, "2026-07", hoy=HOY) is False
    assert mes_editable(db, "2026-08", hoy=HOY) is True


def test_cerrar_uno_que_nunca_se_abrio_no_rompe(db):
    marcar_mes_cerrado(db, "2026-08")

    assert mes_editable(db, "2026-08", hoy=HOY) is False


@pytest.mark.parametrize("periodo", ["", None, "2026", "septiembre"])
def test_un_periodo_ilegible_no_se_considera_editable(db, periodo):
    """Ante la duda, no se toca: un periodo mal formado no puede abrir la
    puerta a editar lo que sea."""
    assert mes_editable(db, periodo, hoy=HOY) is False


# ── el listado de reabiertos ─────────────────────────────────────────────────

def test_el_listado_dice_quien_lo_reabrio(db):
    marcar_mes_abierto(db, "2026-08", quien="Gonza")

    filas = listar_meses_abiertos(db)

    assert filas[0]["periodo"] == "2026-08"
    assert filas[0]["abierto_por"] == "Gonza"


def test_sin_reabiertos_el_listado_esta_vacio(db):
    assert listar_meses_abiertos(db) == []


# ── que meses tienen datos, que es lo que dibuja el navegador ────────────────

def test_los_meses_con_movimientos_salen_ordenados(db):
    from services.finanzas import meses_con_datos

    for periodo in ("2026-09", "2026-07", "2026-09"):
        crear_movimiento(db, tipo="egreso", fecha=f"{periodo}-10", periodo=periodo,
                         concepto="x", categoria="otros", monto=10, moneda="USD",
                         monto_usd=10)

    assert meses_con_datos(db) == ["2026-07", "2026-09"]


def test_sin_movimientos_no_hay_meses(db):
    from services.finanzas import meses_con_datos

    assert meses_con_datos(db) == []
