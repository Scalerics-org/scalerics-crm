"""Conversión de moneda y aritmética de períodos.

Nada de esto toca la base ni Flask: es la parte que, si da un número mal, se
cree. Un total de egresos equivocado en un panel financiero es peor que un
bug de UI.
"""

import sqlite3
from datetime import date

import pytest

import database
from database import (actualizar_movimiento, borrar_recurrente, crear_movimiento,
                      crear_recurrente, init_db, listar_movimientos)
from services.finanzas import (CATEGORIAS, a_usd, materializar_recurrentes,
                               meses_entre, periodo_anterior, periodo_de, resumen)

_HOY = date(2026, 9, 8)


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


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "f.db")
    init_db(ruta)
    return ruta


def _fijo(db, **extra):
    campos = dict(tipo="egreso", concepto="Fly", categoria="infraestructura",
                  monto=4.18, moneda="USD", dia_del_mes=20, desde="2026-07")
    campos.update(extra)
    return crear_recurrente(db, **campos)


def test_materializa_un_movimiento_por_mes_desde_el_inicio(db):
    _fijo(db)  # desde julio, hoy es septiembre
    assert materializar_recurrentes(db, hoy=_HOY) == 3
    periodos = sorted(m["periodo"] for m in listar_movimientos(db))
    assert periodos == ["2026-07", "2026-08", "2026-09"]


def test_el_mes_en_curso_se_genera_aunque_el_dia_no_haya_llegado(db):
    # Hoy es 8 de septiembre y el fijo cae el 20: se genera igual, para que el
    # mes muestre su costo fijo completo.
    _fijo(db, desde="2026-09")
    materializar_recurrentes(db, hoy=_HOY)
    assert listar_movimientos(db)[0]["fecha"] == "2026-09-20"


def test_correrlo_dos_veces_no_duplica(db):
    """El caso del deploy. Cada deploy reinicia la máquina."""
    _fijo(db)
    materializar_recurrentes(db, hoy=_HOY)
    assert materializar_recurrentes(db, hoy=_HOY) == 0
    assert len(listar_movimientos(db)) == 3


def test_no_genera_periodos_futuros(db):
    _fijo(db, desde="2026-07", hasta="2027-12")
    materializar_recurrentes(db, hoy=_HOY)
    assert max(m["periodo"] for m in listar_movimientos(db)) == "2026-09"


def test_respeta_hasta(db):
    _fijo(db, desde="2026-07", hasta="2026-08")
    assert materializar_recurrentes(db, hoy=_HOY) == 2


def test_un_fijo_apagado_no_genera_nada(db):
    _fijo(db, activo=0)
    assert materializar_recurrentes(db, hoy=_HOY) == 0


def test_un_fijo_en_pesos_usa_su_tipo_de_cambio(db):
    _fijo(db, desde="2026-09", monto=40000, moneda="UYU", tipo_cambio=40.0)
    materializar_recurrentes(db, hoy=_HOY)
    assert listar_movimientos(db)[0]["monto_usd"] == 1000.0


def test_un_fijo_en_pesos_sin_tipo_de_cambio_se_saltea_sin_romper(db):
    """Un fijo mal cargado no puede tumbar la materialización de los demás."""
    _fijo(db, concepto="Roto", desde="2026-09", moneda="UYU", tipo_cambio=None)
    _fijo(db, concepto="Fly", desde="2026-09")
    assert materializar_recurrentes(db, hoy=_HOY) == 1
    assert listar_movimientos(db)[0]["concepto"] == "Fly"


def test_editar_un_movimiento_generado_sobrevive_a_rematerializar(db):
    _fijo(db, desde="2026-09")
    materializar_recurrentes(db, hoy=_HOY)
    mid = listar_movimientos(db)[0]["id"]
    actualizar_movimiento(db, mid, monto=9.99, monto_usd=9.99)
    materializar_recurrentes(db, hoy=_HOY)
    assert listar_movimientos(db)[0]["monto_usd"] == 9.99


def test_borrar_un_fijo_deja_vivos_los_movimientos_que_ya_genero(db):
    """Son plata que se gastó. Borrar la definición no borra la historia."""
    rid = _fijo(db)  # desde julio
    materializar_recurrentes(db, hoy=_HOY)
    assert len(listar_movimientos(db)) == 3
    borrar_recurrente(db, rid)
    assert len(listar_movimientos(db)) == 3


def test_un_movimiento_generado_y_anulado_no_reaparece(db):
    """Si se borrara de verdad, el fijo lo regeneraría y el gasto volvería solo."""
    _fijo(db, desde="2026-09")
    materializar_recurrentes(db, hoy=_HOY)
    mid = listar_movimientos(db)[0]["id"]
    actualizar_movimiento(db, mid, anulado=1)
    assert materializar_recurrentes(db, hoy=_HOY) == 0
    assert listar_movimientos(db) == []


def test_un_integrity_error_que_no_es_duplicado_se_propaga(db, monkeypatch):
    """Si `crear_movimiento` falla por otro motivo (NOT NULL, CHECK...), tiene
    que hacer ruido: no es el duplicado esperado del índice único y no se
    puede tragar en silencio."""
    def _explota(*args, **kwargs):
        raise sqlite3.IntegrityError(
            "NOT NULL constraint failed: finanzas_movimientos.concepto")

    monkeypatch.setattr(database, "crear_movimiento", _explota)
    _fijo(db, desde="2026-09")
    with pytest.raises(sqlite3.IntegrityError):
        materializar_recurrentes(db, hoy=_HOY)


def test_un_fijo_con_hasta_anterior_a_desde_no_genera_nada(db):
    _fijo(db, desde="2026-09", hasta="2026-07")
    assert materializar_recurrentes(db, hoy=_HOY) == 0
    assert listar_movimientos(db) == []


def test_dia_del_mes_se_clampea_entre_1_y_28(db, monkeypatch):
    """None o 0 caen en el día 1; cualquier valor mayor a 28 cae en 28.

    No hay forma de guardar `dia_del_mes` NULL o 0 pasando por
    `crear_recurrente` (la columna tiene NOT NULL DEFAULT 1 y 0 no es un caso
    de negocio real), así que se arman los fijos a mano y se parchea
    `listar_recurrentes` para devolverlos, sin tocar el esquema.
    """
    base = dict(tipo="egreso", categoria="infraestructura", monto=1.0,
               moneda="USD", tipo_cambio=None, desde="2026-09", hasta=None,
               client_id=None, facturado=0)
    fijos = [
        dict(base, id=1, concepto="Sin dia", dia_del_mes=None),
        dict(base, id=2, concepto="Dia cero", dia_del_mes=0),
        dict(base, id=3, concepto="Dia treinta y uno", dia_del_mes=31),
    ]
    monkeypatch.setattr(database, "listar_recurrentes", lambda *a, **k: fijos)
    materializar_recurrentes(db, hoy=_HOY)
    fechas = {m["concepto"]: m["fecha"] for m in listar_movimientos(db)}
    assert fechas["Sin dia"] == "2026-09-01"
    assert fechas["Dia cero"] == "2026-09-01"
    assert fechas["Dia treinta y uno"] == "2026-09-28"


def _cargar(db, tipo, periodo, monto_usd, categoria="otros", client_id=None):
    return crear_movimiento(db, tipo=tipo, fecha=f"{periodo}-15", periodo=periodo,
                            concepto="x", categoria=categoria, monto=monto_usd,
                            moneda="USD", monto_usd=monto_usd, client_id=client_id)


def test_los_kpis_suman_el_periodo_pedido(db):
    _cargar(db, "ingreso", "2026-09", 800)
    _cargar(db, "egreso", "2026-09", 100)
    _cargar(db, "ingreso", "2026-06", 5000)  # fuera del rango
    r = resumen(db, "2026-09", "2026-09")
    assert r["kpis"]["ingresos_usd"] == 800
    assert r["kpis"]["egresos_usd"] == 100
    assert r["kpis"]["neto_usd"] == 700


def test_los_kpis_traen_el_periodo_anterior_para_la_variacion(db):
    _cargar(db, "ingreso", "2026-09", 800)
    _cargar(db, "ingreso", "2026-08", 500)
    r = resumen(db, "2026-09", "2026-09")
    assert r["kpis"]["ingresos_previos_usd"] == 500


def test_mezcla_monedas_convirtiendo_a_dolares(db):
    crear_movimiento(db, tipo="ingreso", fecha="2026-09-01", periodo="2026-09",
                     concepto="cobro en pesos", categoria="desarrollo_web",
                     monto=40000, moneda="UYU", tipo_cambio=40.0, monto_usd=1000.0)
    crear_movimiento(db, tipo="ingreso", fecha="2026-09-02", periodo="2026-09",
                     concepto="cobro en dolares", categoria="desarrollo_web",
                     monto=500, moneda="USD", monto_usd=500.0)
    assert resumen(db, "2026-09", "2026-09")["kpis"]["ingresos_usd"] == 1500.0


def test_la_serie_trae_todos_los_meses_incluso_los_vacios(db):
    _cargar(db, "egreso", "2026-09", 100)
    serie = resumen(db, "2026-07", "2026-09")["serie"]
    assert [p["periodo"] for p in serie] == ["2026-07", "2026-08", "2026-09"]
    assert serie[0]["egresos_usd"] == 0.0


def test_el_desglose_por_categoria_agrupa(db):
    _cargar(db, "egreso", "2026-09", 10, categoria="infraestructura")
    _cargar(db, "egreso", "2026-09", 5, categoria="infraestructura")
    _cargar(db, "egreso", "2026-09", 20, categoria="herramientas")
    por_cat = resumen(db, "2026-09", "2026-09")["por_categoria"]
    infra = [c for c in por_cat if c["categoria"] == "infraestructura"][0]
    assert infra["total_usd"] == 15


def test_los_anulados_no_cuentan_en_ningun_agregado(db):
    mid = _cargar(db, "egreso", "2026-09", 100)
    actualizar_movimiento(db, mid, anulado=1)
    r = resumen(db, "2026-09", "2026-09")
    assert r["kpis"]["egresos_usd"] == 0
    assert r["por_categoria"] == []
