"""Mes a mes: leads, demos y ventas, para verlos uno al lado del otro.

La serie semanal contesta "como viene esta semana". Con un periodo corto son dos
o tres puntos y una linea con tres puntos no se lee: parece que falta algo. Y la
pregunta que se hace mirando el panel casi siempre es mensual —"¿como venimos
contra el mes pasado?"— no semanal.

`ventas` son los cierres. Se llaman asi porque es como los llama el equipo y
como figuran en la planilla, y el panel tiene que hablar el idioma de quien lo
mira.
"""

import pytest

from database import _connect, init_db
from services.dossier import serie_mensual


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def _lead(db, cuando, eventos=()):
    conn = _connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO businesses (name, phone, source, scraped_at) "
            "VALUES (?,?,?,?)", (cuando, cuando, "meta", cuando))
        for estado in eventos:
            conn.execute("INSERT INTO lead_events (lead_id, new_status) VALUES (?,?)",
                         (cur.lastrowid, estado))
        conn.commit()
    finally:
        conn.close()


def _gasto(db, fecha, spend, campana="UY"):
    conn = _connect(db)
    try:
        conn.execute(
            "INSERT INTO meta_insights (date, campaign_id, campaign_name, spend, "
            "currency, impressions, clicks, leads) VALUES (?,?,?,?,?,?,?,?)",
            (fecha, str(abs(hash(campana)) % 10**9), campana, spend,
             "USD", 0, 0, 0))
        conn.commit()
    finally:
        conn.close()


def _de(serie, periodo):
    iguales = [p for p in serie if p["periodo"] == periodo]
    assert iguales, f"no hay mes {periodo}: {[p['periodo'] for p in serie]}"
    return iguales[0]


def test_cuenta_leads_demos_y_ventas_del_mes(db):
    _lead(db, "2026-03-05 10:00:00", eventos=("demo_1",))
    _lead(db, "2026-03-20 10:00:00", eventos=("cerrado",))
    _lead(db, "2026-03-25 10:00:00")
    m = _de(serie_mensual(db, "2026-03-01", "2026-03-31"), "2026-03")
    assert m["leads"] == 3
    # `cerrado` implica haber pasado por demo: son 2 demos, no 1.
    assert m["demos"] == 2
    assert m["ventas"] == 1


def test_trae_el_gasto_del_mes(db):
    _gasto(db, "2026-03-05", 100.0)
    _gasto(db, "2026-03-20", 50.0)
    assert _de(serie_mensual(db, "2026-03-01", "2026-03-31"), "2026-03")["gasto"] == 150.0


def test_los_meses_vienen_en_orden(db):
    for mes in ("2026-05", "2026-03", "2026-04"):
        _lead(db, f"{mes}-10 10:00:00")
    periodos = [p["periodo"] for p in serie_mensual(db, "2026-03-01", "2026-05-31")]
    assert periodos == sorted(periodos)


def test_un_mes_del_medio_sin_nada_igual_aparece(db):
    """El hueco es informacion: un mes sin leads entre dos con leads es
    justamente lo que hay que ver. Si se saltea, el grafico miente sobre el
    ritmo."""
    _lead(db, "2026-03-10 10:00:00")
    _lead(db, "2026-05-10 10:00:00")
    serie = serie_mensual(db, "2026-03-01", "2026-05-31")
    assert [p["periodo"] for p in serie] == ["2026-03", "2026-04", "2026-05"]
    assert _de(serie, "2026-04")["leads"] == 0


def test_el_mes_solo_con_gasto_tambien_aparece(db):
    """Se gasto y no entro nadie: es la fila mas importante del cuadro."""
    _gasto(db, "2026-04-10", 80.0)
    m = _de(serie_mensual(db, "2026-03-01", "2026-05-31"), "2026-04")
    assert m["gasto"] == 80.0 and m["leads"] == 0


def test_el_costo_por_venta_es_none_sin_ventas(db):
    """Sin ventas no es un costo de cero: no hay costo por venta."""
    _lead(db, "2026-03-10 10:00:00")
    _gasto(db, "2026-03-10", 100.0)
    assert _de(serie_mensual(db, "2026-03-01", "2026-03-31"), "2026-03")["costo_venta"] is None


def test_el_periodo_recorta(db):
    _lead(db, "2026-02-25 10:00:00")
    _lead(db, "2026-03-10 10:00:00")
    serie = serie_mensual(db, "2026-03-01", "2026-03-31")
    assert [p["periodo"] for p in serie] == ["2026-03"]
    assert serie[0]["leads"] == 1


def test_cada_mes_trae_su_nombre_legible(db):
    """"2026-03" es un id; "Marzo" es lo que va abajo de la barra."""
    _lead(db, "2026-03-10 10:00:00")
    assert _de(serie_mensual(db, "2026-03-01", "2026-03-31"), "2026-03")["nombre"] == "Marzo"


def test_un_periodo_vacio_no_devuelve_nada(db):
    assert serie_mensual(db, "2026-03-01", "2026-03-31") == []
