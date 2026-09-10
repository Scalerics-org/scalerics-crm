"""La serie semanal y la conciliacion del gasto.

Semanal y no diaria: 238 leads en 178 dias son 1,3 por dia. En dias solo se ve
ruido; la senal aparece recien por semana.
"""

import pytest

from database import _connect, init_db
from services.dossier import conciliacion, serie_semanal


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def _gasto(db, fecha, spend, leads=0, impresiones=1000, clicks=40):
    conn = _connect(db)
    try:
        conn.execute(
            "INSERT INTO meta_insights (date, campaign_id, campaign_name, spend, "
            "currency, impressions, clicks, reach, leads) VALUES (?,?,?,?,?,?,?,?,?)",
            (fecha, "120", "Leads - UY - 2026", spend, "UYU", impresiones,
             clicks, 900, leads))
        conn.commit()
    finally:
        conn.close()


def _lead(db, nombre, entro):
    conn = _connect(db)
    try:
        conn.execute("INSERT INTO businesses (name, phone, source, scraped_at) "
                     "VALUES (?,?,?,?)", (nombre, nombre, "meta", entro))
        conn.commit()
    finally:
        conn.close()


def _movimiento(db, fecha, monto):
    """Un egreso de publicidad cargado a mano en Finanzas.

    `periodo` y `concepto` son NOT NULL en esa tabla: omitirlos da IntegrityError.
    """
    conn = _connect(db)
    try:
        conn.execute(
            "INSERT INTO finanzas_movimientos (tipo, fecha, periodo, concepto, "
            "categoria, monto, moneda, monto_usd, anulado) "
            "VALUES ('egreso',?,?,'Pauta Meta','publicidad',?,'USD',?,0)",
            (fecha, fecha[:7], monto, monto))
        conn.commit()
    finally:
        conn.close()


def _por_inicio(db, desde="2026-03-01", hasta="2026-03-31"):
    return {s["inicio"]: s for s in serie_semanal(db, desde, hasta)}


def test_agrupa_los_dias_de_la_misma_semana(db):
    _gasto(db, "2026-03-02", 10.0)   # lunes
    _gasto(db, "2026-03-04", 15.0)   # miercoles
    assert _por_inicio(db)["2026-03-02"]["gasto"] == 25.0


def test_separa_semanas_distintas(db):
    _gasto(db, "2026-03-02", 10.0)
    _gasto(db, "2026-03-09", 20.0)
    assert {"2026-03-02", "2026-03-09"} <= set(_por_inicio(db))


def test_la_semana_arranca_en_lunes(db):
    """Un domingo cae en la semana que empezo el lunes anterior."""
    _gasto(db, "2026-03-08", 10.0)   # domingo
    semanas = serie_semanal(db, "2026-03-01", "2026-03-31")
    assert [s for s in semanas if s["gasto"] == 10.0][0]["inicio"] == "2026-03-02"


def test_cuenta_los_leads_del_crm_en_su_semana(db):
    _lead(db, "a", "2026-03-03 10:00:00")
    _lead(db, "b", "2026-03-05 10:00:00")
    _lead(db, "c", "2026-03-10 10:00:00")
    s = _por_inicio(db)
    assert s["2026-03-02"]["leads_crm"] == 2
    assert s["2026-03-09"]["leads_crm"] == 1


def test_el_cpl_semanal_sale_del_gasto_sobre_los_leads_del_crm(db):
    _gasto(db, "2026-03-02", 100.0)
    _lead(db, "a", "2026-03-03 10:00:00")
    _lead(db, "b", "2026-03-04 10:00:00")
    assert _por_inicio(db)["2026-03-02"]["cpl"] == 50.0


def test_una_semana_con_gasto_y_sin_leads_no_tiene_cpl(db):
    _gasto(db, "2026-03-02", 100.0)
    assert _por_inicio(db)["2026-03-02"]["cpl"] is None


def test_la_semana_trae_su_etiqueta_iso(db):
    _gasto(db, "2026-03-02", 10.0)
    assert _por_inicio(db)["2026-03-02"]["semana"].startswith("2026-W")


def test_las_semanas_salen_ordenadas(db):
    _gasto(db, "2026-03-16", 10.0)
    _gasto(db, "2026-03-02", 10.0)
    inicios = [s["inicio"] for s in serie_semanal(db, "2026-03-01", "2026-03-31")]
    assert inicios == sorted(inicios)


def test_sin_datos_la_serie_es_vacia(db):
    assert serie_semanal(db, "2026-03-01", "2026-03-31") == []


def test_la_conciliacion_compara_meta_contra_lo_cargado_a_mano(db):
    _gasto(db, "2026-03-05", 300.0)
    _movimiento(db, "2026-03-31", 250.0)
    m = {x["id"]: x for x in conciliacion(db, "2026-03-01", "2026-03-31")}
    assert m["conciliacion.gasto_meta.2026_03"]["valor"] == 300.0
    assert m["conciliacion.gasto_cargado.2026_03"]["valor"] == 250.0
    assert m["conciliacion.brecha.2026_03"]["valor"] == 50.0


def test_la_conciliacion_sin_gasto_cargado_marca_todo_como_brecha(db):
    """Si Meta cobro y nadie lo registro, la brecha es el gasto entero."""
    _gasto(db, "2026-03-05", 300.0)
    m = {x["id"]: x for x in conciliacion(db, "2026-03-01", "2026-03-31")}
    assert m["conciliacion.brecha.2026_03"]["valor"] == 300.0


def test_la_conciliacion_ignora_los_movimientos_anulados(db):
    _gasto(db, "2026-03-05", 300.0)
    _movimiento(db, "2026-03-31", 250.0)
    conn = _connect(db)
    try:
        conn.execute("UPDATE finanzas_movimientos SET anulado = 1")
        conn.commit()
    finally:
        conn.close()
    m = {x["id"]: x for x in conciliacion(db, "2026-03-01", "2026-03-31")}
    assert m["conciliacion.gasto_cargado.2026_03"]["valor"] == 0.0


def test_la_conciliacion_solo_mira_la_categoria_publicidad(db):
    """Un egreso de infraestructura no es pauta."""
    _gasto(db, "2026-03-05", 300.0)
    conn = _connect(db)
    try:
        conn.execute(
            "INSERT INTO finanzas_movimientos (tipo, fecha, periodo, concepto, "
            "categoria, monto, moneda, monto_usd, anulado) VALUES "
            "('egreso','2026-03-10','2026-03','Fly','infraestructura',9,'USD',9,0)")
        conn.commit()
    finally:
        conn.close()
    m = {x["id"]: x for x in conciliacion(db, "2026-03-01", "2026-03-31")}
    assert m["conciliacion.gasto_cargado.2026_03"]["valor"] == 0.0


def test_sin_datos_la_conciliacion_es_vacia(db):
    assert conciliacion(db, "2026-03-01", "2026-03-31") == []
