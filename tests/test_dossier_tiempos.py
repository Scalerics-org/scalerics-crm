"""Cuanto se tarda en reaccionar, y si los recordatorios mueven algo.

La mediana y no el promedio: un lead contactado a los 60 dias arrastra el
promedio y hace parecer lento a un equipo que contesta el mismo dia.
"""

import pytest

from database import _connect, init_db
from services.dossier import recordatorios, tiempos


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def _lead(db, nombre, entro="2026-03-01 09:00:00", eventos=()):
    conn = _connect(db)
    try:
        cur = conn.execute("INSERT INTO businesses (name, phone, source, scraped_at) "
                           "VALUES (?,?,?,?)", (nombre, nombre, "meta", entro))
        lead_id = cur.lastrowid
        for estado, cuando in eventos:
            conn.execute("INSERT INTO lead_events (lead_id, new_status, created_at) "
                         "VALUES (?,?,?)", (lead_id, estado, cuando))
        conn.commit()
        return lead_id
    finally:
        conn.close()


def _recordatorio(db, lead_id, numero, cuando):
    conn = _connect(db)
    try:
        conn.execute("INSERT INTO meta_reminders (business_id, estado, numero, "
                     "token, sent_at) VALUES (?,?,?,?,?)",
                     (lead_id, "sin_contactar", numero,
                      f"tok{lead_id}-{numero}", cuando))
        conn.commit()
    finally:
        conn.close()


def _buscar(metricas, sufijo):
    for m in metricas:
        if m["id"].endswith(sufijo):
            return m
    raise AssertionError(f"no hay metrica que termine en {sufijo!r}: "
                         f"{[m['id'] for m in metricas]}")


def test_mediana_de_dias_hasta_el_primer_evento(db):
    _lead(db, "a", eventos=[("interesado", "2026-03-03 09:00:00")])   # 2 dias
    _lead(db, "b", eventos=[("interesado", "2026-03-05 09:00:00")])   # 4 dias
    _lead(db, "c", eventos=[("interesado", "2026-03-07 09:00:00")])   # 6 dias
    m = _buscar(tiempos(db, "2026-03-01", "2026-03-31"), ".dias_a_primer_contacto")
    assert m["valor"] == 4.0


def test_la_mediana_aguanta_un_valor_extremo(db):
    """Con promedio, el lead contactado a los 60 dias arruina el numero."""
    _lead(db, "a", eventos=[("interesado", "2026-03-02 09:00:00")])
    _lead(db, "b", eventos=[("interesado", "2026-03-02 09:00:00")])
    _lead(db, "c", eventos=[("interesado", "2026-04-30 09:00:00")])
    m = _buscar(tiempos(db, "2026-03-01", "2026-05-31"), ".dias_a_primer_contacto")
    assert m["valor"] == 1.0


def test_mediana_de_dias_hasta_la_demo(db):
    _lead(db, "a", eventos=[("demo_1", "2026-03-11 09:00:00")])   # 10 dias
    m = _buscar(tiempos(db, "2026-03-01", "2026-03-31"), ".dias_a_demo")
    assert m["valor"] == 10.0


def test_la_demo_se_reconoce_con_el_vocabulario_viejo(db):
    """lead_events mezcla las dos epocas: reunion_hecha es demo_1."""
    _lead(db, "a", eventos=[("reunion_hecha", "2026-03-11 09:00:00")])
    m = _buscar(tiempos(db, "2026-03-01", "2026-03-31"), ".dias_a_demo")
    assert m["valor"] == 10.0


def test_un_lead_sin_eventos_no_cuenta_en_la_mediana(db):
    """Nunca contactado no es "tardo mucho": es que no esta el dato."""
    _lead(db, "a", eventos=[("interesado", "2026-03-03 09:00:00")])
    _lead(db, "sin_tocar")
    m = _buscar(tiempos(db, "2026-03-01", "2026-03-31"), ".dias_a_primer_contacto")
    assert m["n"] == 1


def test_sin_ningun_lead_con_eventos_el_tiempo_es_none(db):
    _lead(db, "a")
    m = _buscar(tiempos(db, "2026-03-01", "2026-03-31"), ".dias_a_primer_contacto")
    assert m["valor"] is None


def test_una_fecha_ilegible_no_rompe(db):
    _lead(db, "a", eventos=[("interesado", "no es una fecha")])
    m = _buscar(tiempos(db, "2026-03-01", "2026-03-31"), ".dias_a_primer_contacto")
    assert m["valor"] is None


def test_un_recordatorio_que_movio_el_estado_cuenta(db):
    lead = _lead(db, "a", eventos=[("interesado", "2026-03-10 09:00:00")])
    _recordatorio(db, lead, 1, "2026-03-08 09:00:00")     # 2 dias antes
    m = _buscar(recordatorios(db, "2026-03-01", "2026-03-31"), ".tasa_movio_1")
    assert m["numerador"] == 1
    assert m["denominador"] == 1


def test_un_evento_muy_posterior_no_se_le_atribuye_al_recordatorio(db):
    """La ventana son 7 dias. Un cambio 20 dias despues no fue por el mail."""
    lead = _lead(db, "a", eventos=[("interesado", "2026-03-28 09:00:00")])
    _recordatorio(db, lead, 1, "2026-03-08 09:00:00")
    m = _buscar(recordatorios(db, "2026-03-01", "2026-03-31"), ".tasa_movio_1")
    assert m["numerador"] == 0


def test_un_evento_anterior_al_recordatorio_tampoco_cuenta(db):
    lead = _lead(db, "a", eventos=[("interesado", "2026-03-02 09:00:00")])
    _recordatorio(db, lead, 1, "2026-03-08 09:00:00")
    m = _buscar(recordatorios(db, "2026-03-01", "2026-03-31"), ".tasa_movio_1")
    assert m["numerador"] == 0


def test_un_lead_con_varios_eventos_cuenta_una_sola_vez(db):
    lead = _lead(db, "a", eventos=[("interesado", "2026-03-09 09:00:00"),
                                   ("demo_agendada", "2026-03-10 09:00:00")])
    _recordatorio(db, lead, 1, "2026-03-08 09:00:00")
    m = _buscar(recordatorios(db, "2026-03-01", "2026-03-31"), ".tasa_movio_1")
    assert m["numerador"] == 1


def test_cada_numero_de_la_secuencia_se_mide_aparte(db):
    a = _lead(db, "a", eventos=[("interesado", "2026-03-10 09:00:00")])
    _recordatorio(db, a, 1, "2026-03-08 09:00:00")
    b = _lead(db, "b")
    _recordatorio(db, b, 3, "2026-03-08 09:00:00")
    ids = {m["id"] for m in recordatorios(db, "2026-03-01", "2026-03-31")}
    assert any(i.endswith(".tasa_movio_1") for i in ids)
    assert any(i.endswith(".tasa_movio_3") for i in ids)


def test_sin_recordatorios_no_hay_metricas(db):
    assert recordatorios(db, "2026-03-01", "2026-03-31") == []


def _evento_importado(db, lead_id, estado, cuando):
    """Un evento del import masivo de la planilla: el estado es real, la fecha
    es la del import."""
    conn = _connect(db)
    try:
        conn.execute("INSERT INTO lead_events (lead_id, new_status, created_at, "
                     "created_by) VALUES (?,?,?,'sistema (import planilla)')",
                     (lead_id, estado, cuando))
        conn.commit()
    finally:
        conn.close()


def test_el_import_masivo_de_la_planilla_no_cuenta_para_los_tiempos(db):
    """195 de los 242 eventos de Meta se escribieron el mismo dia, en agosto,
    para leads que entraron en marzo. Su fecha es la del import y arrastraba la
    mediana a 79 dias, que no es lo que tarda el equipo en llamar."""
    lead = _lead(db, "a", eventos=[("interesado", "2026-03-03 09:00:00")])
    otro = _lead(db, "b")
    _evento_importado(db, otro, "interesado", "2026-08-27 09:00:00")

    m = _buscar(tiempos(db, "2026-03-01", "2026-09-30"), ".dias_a_primer_contacto")
    assert m["n"] == 1
    assert m["valor"] == 2.0


def test_un_recordatorio_no_se_lleva_el_credito_de_un_evento_importado(db):
    lead = _lead(db, "a")
    _recordatorio(db, lead, 1, "2026-08-25 09:00:00")
    _evento_importado(db, lead, "interesado", "2026-08-27 09:00:00")
    m = _buscar(recordatorios(db, "2026-08-01", "2026-08-31"), ".tasa_movio_1")
    assert m["numerador"] == 0
