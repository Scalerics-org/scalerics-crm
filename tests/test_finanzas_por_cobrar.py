"""Lo que falta cobrar: de dónde sale, cuándo vence y cuándo se salda.

El caso real es el 50% final de un desarrollo: se cobra la mitad al empezar y
la otra mitad queda pendiente, con una fecha estimada. Hasta ahora eso vivía en
la cabeza de alguien.

**El pendiente nace del cobro parcial, no se carga aparte.** Al registrar el
ingreso se indica el total acordado y cuándo se espera el resto; la diferencia
es el pendiente. Cargarlo en dos lugares distintos garantiza que se
desincronicen.

**Un pendiente se salda cobrándolo**, y eso crea el movimiento de ingreso: si
saldar no moviera la caja, el panel diría que cobraste y los KPIs que no.
"""

import pytest

from database import (crear_movimiento, crear_por_cobrar, get_movimiento,
                      get_por_cobrar, init_db, insert_business,
                      listar_por_cobrar)
from services.finanzas import estado_de_cobro, saldar_por_cobrar


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "c.db")
    init_db(ruta)
    return ruta


_tel = iter(range(600000, 699999))


def _cliente(db, nombre):
    return insert_business(db, {"name": nombre, "phone": f"+598{next(_tel)}"})


def _pendiente(db, nombre="La Vaca Encantada", monto=500, vence="2026-09-18"):
    cid = _cliente(db, nombre)
    pid = crear_por_cobrar(db, client_id=cid, concepto=f"50% final {nombre}",
                           monto_usd=monto, vence=vence)
    return cid, pid


# ── el pendiente ─────────────────────────────────────────────────────────────

def test_se_guarda_lo_que_falta_cobrar(db):
    cid, pid = _pendiente(db, monto=500)

    p = get_por_cobrar(db, pid)
    assert p["monto_usd"] == 500
    assert p["client_id"] == cid
    assert p["vence"] == "2026-09-18"
    assert p["cobrado_movimiento_id"] is None


def test_el_listado_trae_el_nombre_del_cliente(db):
    """La pestaña muestra el cliente, no su id."""
    _pendiente(db, nombre="La Vaca Encantada")

    filas = listar_por_cobrar(db)

    assert len(filas) == 1
    assert filas[0]["client_name"] == "La Vaca Encantada"


def test_los_saldados_no_aparecen_por_defecto(db):
    _, pid = _pendiente(db)
    _, otro = _pendiente(db, nombre="Bloquera")
    saldar_por_cobrar(db, pid, fecha="2026-09-20")

    pendientes = listar_por_cobrar(db)

    assert [p["id"] for p in pendientes] == [otro]
    assert len(listar_por_cobrar(db, incluir_cobrados=True)) == 2


def test_se_ordenan_por_vencimiento_lo_mas_urgente_primero(db):
    _pendiente(db, nombre="Tarde", vence="2026-12-01")
    _pendiente(db, nombre="Temprano", vence="2026-09-01")
    _pendiente(db, nombre="Medio", vence="2026-10-15")

    nombres = [p["client_name"] for p in listar_por_cobrar(db)]

    assert nombres == ["Temprano", "Medio", "Tarde"]


def test_uno_sin_fecha_no_se_pierde_al_final(db):
    """Sin vencimiento no significa "no urgente": significa que nadie lo puso."""
    _pendiente(db, nombre="Con fecha", vence="2026-09-01")
    _pendiente(db, nombre="Sin fecha", vence=None)

    nombres = [p["client_name"] for p in listar_por_cobrar(db)]

    assert set(nombres) == {"Con fecha", "Sin fecha"}
    assert len(nombres) == 2


# ── vencido o en plazo ───────────────────────────────────────────────────────

def test_uno_que_vence_mas_adelante_esta_en_plazo(db):
    e = estado_de_cobro("2026-09-20", hoy="2026-09-09")

    assert e["vencido"] is False
    assert e["dias"] == 11
    assert e["texto"] == "vence 20/09"


def test_uno_que_vencio_dice_hace_cuanto(db):
    e = estado_de_cobro("2026-09-06", hoy="2026-09-09")

    assert e["vencido"] is True
    assert e["dias"] == 3
    assert e["texto"] == "vencido hace 3 días"


def test_uno_que_vence_hoy_todavia_no_esta_vencido(db):
    e = estado_de_cobro("2026-09-09", hoy="2026-09-09")

    assert e["vencido"] is False
    assert e["texto"] == "vence hoy"


def test_uno_que_vencio_ayer_dice_un_dia_en_singular(db):
    assert estado_de_cobro("2026-09-08", hoy="2026-09-09")["texto"] == "vencido hace 1 día"


def test_sin_fecha_no_inventa_un_vencimiento(db):
    e = estado_de_cobro(None, hoy="2026-09-09")

    assert e["vencido"] is False
    assert e["texto"] == "sin fecha"


def test_una_fecha_ilegible_no_rompe_la_pestania(db):
    e = estado_de_cobro("cuando cobren", hoy="2026-09-09")

    assert e["vencido"] is False
    assert e["texto"] == "sin fecha"


# ── saldar ───────────────────────────────────────────────────────────────────

def test_saldar_crea_el_movimiento_de_ingreso(db):
    """Si saldar no moviera la caja, el panel diría que cobraste y los KPIs
    que no."""
    cid, pid = _pendiente(db, monto=500)

    mid = saldar_por_cobrar(db, pid, fecha="2026-09-20")

    mov = get_movimiento(db, mid)
    assert mov["tipo"] == "ingreso"
    assert mov["monto_usd"] == 500
    assert mov["client_id"] == cid
    assert mov["periodo"] == "2026-09"


def test_saldar_deja_el_pendiente_marcado(db):
    _, pid = _pendiente(db)

    mid = saldar_por_cobrar(db, pid, fecha="2026-09-20")

    assert get_por_cobrar(db, pid)["cobrado_movimiento_id"] == mid


def test_saldar_dos_veces_no_cobra_dos_veces(db):
    _, pid = _pendiente(db)
    saldar_por_cobrar(db, pid, fecha="2026-09-20")

    with pytest.raises(ValueError):
        saldar_por_cobrar(db, pid, fecha="2026-09-21")


def test_saldar_uno_que_no_existe_avisa(db):
    with pytest.raises(ValueError):
        saldar_por_cobrar(db, 9999, fecha="2026-09-20")


def test_saldar_facturado_guarda_el_iva(db):
    """Cobrar el 50% final se factura como cualquier otro ingreso."""
    _, pid = _pendiente(db, monto=1220)

    mid = saldar_por_cobrar(db, pid, fecha="2026-09-20", facturado=True)

    mov = get_movimiento(db, mid)
    assert mov["facturado"] == 1
    assert round(mov["iva_usd"], 2) == 220.0


# ── el total, que es lo que va en la card ────────────────────────────────────

def test_el_total_pendiente_suma_solo_lo_no_cobrado(db):
    _pendiente(db, nombre="Uno", monto=500)
    _, pid = _pendiente(db, nombre="Dos", monto=300)
    _pendiente(db, nombre="Tres", monto=200)
    saldar_por_cobrar(db, pid, fecha="2026-09-20")

    total = sum(p["monto_usd"] for p in listar_por_cobrar(db))

    assert total == 700
