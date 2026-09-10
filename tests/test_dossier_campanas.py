"""El dossier por campana: gasto de Meta cruzado con el destino del lead.

Las etapas se cuentan con `alcanzo` sobre lead_events, igual que Finanzas. Si
este modulo contara desde crm_status, el CRM mostraria dos numeros distintos
para la misma pregunta en dos paneles.
"""

import pytest

from database import _connect, init_db
from services.dossier import por_campana


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def _lead(db, nombre, campana, entro="2026-03-05", eventos=()):
    conn = _connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO businesses (name, phone, source, scraped_at, "
            "meta_campaign_name, meta_campaign_id) VALUES (?,?,?,?,?,?)",
            (nombre, nombre, "meta", entro, campana, "120"))
        lead_id = cur.lastrowid
        for estado in eventos:
            conn.execute("INSERT INTO lead_events (lead_id, new_status) VALUES (?,?)",
                         (lead_id, estado))
        conn.commit()
        return lead_id
    finally:
        conn.close()


def _gasto(db, fecha, spend, leads=0, impresiones=0, clicks=0,
           campana="Leads - UY - 2026"):
    conn = _connect(db)
    try:
        conn.execute(
            "INSERT INTO meta_insights (date, campaign_id, campaign_name, spend, "
            "currency, impressions, clicks, leads) VALUES (?,?,?,?,?,?,?,?)",
            (fecha, "120", campana, spend, "UYU", impresiones, clicks, leads))
        conn.commit()
    finally:
        conn.close()


def _buscar(entrada, sufijo):
    for m in entrada["metricas"]:
        if m["id"].endswith(sufijo):
            return m
    raise AssertionError(f"no hay metrica que termine en {sufijo!r}: "
                         f"{[m['id'] for m in entrada['metricas']]}")


def _de(bloques, campana):
    return [b for b in bloques if b["campana"] == campana][0]


def test_cuenta_los_leads_de_la_campana(db):
    _lead(db, "a", "Leads - UY - 2026")
    _lead(db, "b", "Leads - UY - 2026")
    entrada = por_campana(db, "2026-03-01", "2026-03-31")[0]
    assert _buscar(entrada, ".leads_crm")["valor"] == 2


def test_una_demo_se_cuenta_aunque_el_lead_hoy_este_caido(db):
    """Llego a demo y despues se cayo a no_interesa. La demo paso igual."""
    _lead(db, "a", "Leads - UY - 2026", eventos=("demo_1", "no_interesa"))
    entrada = por_campana(db, "2026-03-01", "2026-03-31")[0]
    assert _buscar(entrada, ".demos")["valor"] == 1


def test_el_vocabulario_viejo_de_lead_events_se_cuenta_igual(db):
    """lead_events mezcla las dos epocas: reunion_hecha es demo_1."""
    _lead(db, "a", "Leads - UY - 2026", eventos=("reunion_hecha",))
    entrada = por_campana(db, "2026-03-01", "2026-03-31")[0]
    assert _buscar(entrada, ".demos")["valor"] == 1


def test_llegar_a_cerrado_implica_haber_pasado_por_demo(db):
    _lead(db, "a", "Leads - UY - 2026", eventos=("cerrado",))
    entrada = por_campana(db, "2026-03-01", "2026-03-31")[0]
    assert _buscar(entrada, ".demos")["valor"] == 1
    assert _buscar(entrada, ".cierres")["valor"] == 1


def test_el_cpl_sale_del_gasto_sobre_los_leads(db):
    _lead(db, "a", "Leads - UY - 2026")
    _lead(db, "b", "Leads - UY - 2026")
    _gasto(db, "2026-03-05", 100.0)
    entrada = por_campana(db, "2026-03-01", "2026-03-31")[0]
    assert _buscar(entrada, ".cpl")["valor"] == 50.0


def test_sin_gasto_el_cpl_es_none_no_cero(db):
    """Sin campana que costear no es un costo de cero."""
    _lead(db, "a", "Leads - UY - 2026")
    entrada = por_campana(db, "2026-03-01", "2026-03-31")[0]
    assert _buscar(entrada, ".cpl")["valor"] is None


def test_sin_demos_el_costo_por_demo_es_none(db):
    """Un periodo sin demos no tiene un costo por demo de cero: no tiene."""
    _lead(db, "a", "Leads - UY - 2026")
    _gasto(db, "2026-03-05", 100.0)
    entrada = por_campana(db, "2026-03-01", "2026-03-31")[0]
    assert _buscar(entrada, ".costo_demo")["valor"] is None


def test_la_tasa_de_demo_trae_intervalo_y_marca_de_muestra(db):
    for i in range(10):
        _lead(db, f"l{i}", "Leads - UY - 2026",
              eventos=("demo_1",) if i < 2 else ())
    m = _buscar(por_campana(db, "2026-03-01", "2026-03-31")[0], ".tasa_demo")
    assert m["valor"] == 0.2
    assert m["n"] == 10
    assert m["muestra_chica"] is True
    assert m["ic95"][0] < 0.2 < m["ic95"][1]


def test_los_leads_sin_campana_van_a_su_propio_bucket(db):
    """Trece leads no traen campana. No se reparten ni se esconden."""
    _lead(db, "a", None)
    campanas = {e["campana"] for e in por_campana(db, "2026-03-01", "2026-03-31")}
    assert "(sin campaña)" in campanas


def test_hay_una_entrada_total(db):
    _lead(db, "a", "Leads - UY - 2026")
    _lead(db, "b", "Leads - ARG - 2026")
    entrada = _de(por_campana(db, "2026-03-01", "2026-03-31"), "todas")
    assert _buscar(entrada, ".leads_crm")["valor"] == 2


def test_el_total_suma_el_gasto_de_todas_las_campanas(db):
    _lead(db, "a", "Leads - UY - 2026")
    _gasto(db, "2026-03-05", 100.0, campana="Leads - UY - 2026")
    _gasto(db, "2026-03-06", 40.0, campana="Leads - ARG - 2026")
    entrada = _de(por_campana(db, "2026-03-01", "2026-03-31"), "todas")
    assert _buscar(entrada, ".gasto")["valor"] == 140.0


def test_el_periodo_recorta(db):
    """Un lead de febrero no cuenta en el dossier de marzo."""
    _lead(db, "a", "Leads - UY - 2026", entro="2026-02-15")
    _lead(db, "b", "Leads - UY - 2026", entro="2026-03-15")
    entrada = _de(por_campana(db, "2026-03-01", "2026-03-31"), "todas")
    assert _buscar(entrada, ".leads_crm")["valor"] == 1


def test_la_discrepancia_entre_meta_y_el_crm_es_una_metrica(db):
    """Si Meta dice 5 leads y el CRM tiene 2, algo se pierde en la ingesta."""
    _lead(db, "a", "Leads - UY - 2026")
    _lead(db, "b", "Leads - UY - 2026")
    _gasto(db, "2026-03-05", 100.0, leads=5)
    entrada = por_campana(db, "2026-03-01", "2026-03-31")[0]
    assert _buscar(entrada, ".leads_meta")["valor"] == 5
    assert _buscar(entrada, ".discrepancia_leads")["valor"] == 3


def test_los_ids_de_metrica_son_unicos(db):
    """Dos metricas con el mismo id romperian la citacion del informe."""
    _lead(db, "a", "Leads - UY - 2026")
    _lead(db, "b", None)
    ids = [m["id"] for b in por_campana(db, "2026-03-01", "2026-03-31")
           for m in b["metricas"]]
    repetidos = {i for i in ids if ids.count(i) > 1}
    assert not repetidos, repetidos


def test_sin_leads_devuelve_solo_el_total(db):
    bloques = por_campana(db, "2026-03-01", "2026-03-31")
    assert [b["campana"] for b in bloques] == ["todas"]


def test_sin_clics_sincronizados_el_ctr_no_dice_cero(db):
    """Cero clics sobre 200.000 impresiones no es un CTR de 0%: es que los
    clics no se sincronizaron. Mostrar 0,0% inventa una precision que no hay."""
    _lead(db, "a", "Leads - UY - 2026")
    _gasto(db, "2026-03-05", 100.0, impresiones=200000, clicks=0)
    entrada = por_campana(db, "2026-03-01", "2026-03-31")[0]
    ctr = _buscar(entrada, ".ctr")
    assert ctr["valor"] is None


def test_con_clics_el_ctr_se_calcula(db):
    _lead(db, "a", "Leads - UY - 2026")
    _gasto(db, "2026-03-05", 100.0, impresiones=1000, clicks=15)
    ctr = _buscar(por_campana(db, "2026-03-01", "2026-03-31")[0], ".ctr")
    assert ctr["valor"] == 0.015


def test_sin_impresiones_el_ctr_tampoco_es_cero(db):
    _lead(db, "a", "Leads - UY - 2026")
    entrada = por_campana(db, "2026-03-01", "2026-03-31")[0]
    assert _buscar(entrada, ".ctr")["valor"] is None
