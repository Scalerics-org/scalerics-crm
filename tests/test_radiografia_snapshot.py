"""El snapshot semanal y la comparacion contra el anterior.

En esta fase no hay IA: el snapshot se guarda con status 'sin_ia' y report_json
en NULL. El panel funciona igual, porque los graficos salen del dossier y no
del informe.
"""

import io

import pytest

from database import _connect, init_db
from services.radiografia import (aplicar_deltas, construir_dossier,
                                  guardar_snapshot, ultimo_snapshot)


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def test_el_dossier_trae_todos_los_bloques(db):
    d = construir_dossier(db, "2026-03-01", "2026-03-31")
    assert set(d) >= {"periodo", "campanas", "segmentos", "serie_semanal",
                      "conciliacion", "tiempos", "recordatorios"}
    assert d["periodo"] == {"desde": "2026-03-01", "hasta": "2026-03-31"}


def test_el_dossier_es_serializable(db):
    """Un sqlite3.Row o un Decimal adentro rompen el jsonify en produccion."""
    import json
    json.dumps(construir_dossier(db, "2026-03-01", "2026-03-31"),
               ensure_ascii=False)


def test_guardar_deja_status_sin_ia(db):
    """Esta fase no llama a la API. El snapshot se guarda igual."""
    guardar_snapshot(db, construir_dossier(db, "2026-03-01", "2026-03-31"))
    conn = _connect(db)
    try:
        fila = conn.execute("SELECT * FROM radiografias").fetchone()
    finally:
        conn.close()
    assert fila["status"] == "sin_ia"
    assert fila["report_json"] is None
    assert fila["dossier_json"]


def test_ultimo_snapshot_devuelve_el_mas_reciente(db):
    guardar_snapshot(db, construir_dossier(db, "2026-03-01", "2026-03-31"))
    guardar_snapshot(db, construir_dossier(db, "2026-04-01", "2026-04-30"))
    assert ultimo_snapshot(db)["periodo"]["desde"] == "2026-04-01"


def test_sin_snapshots_previos_devuelve_none(db):
    assert ultimo_snapshot(db) is None


def test_un_snapshot_ilegible_no_rompe(db):
    conn = _connect(db)
    try:
        conn.execute("INSERT INTO radiografias (period_start, period_end, "
                     "dossier_json) VALUES ('2026-03-01','2026-03-31','no json')")
        conn.commit()
    finally:
        conn.close()
    assert ultimo_snapshot(db) is None


def test_los_deltas_salen_del_snapshot_anterior(db):
    anterior = {"campanas": [{"campana": "x", "metricas": [
        {"id": "campana.x.gasto", "valor": 100.0}]}]}
    ahora = {"campanas": [{"campana": "x", "metricas": [
        {"id": "campana.x.gasto", "valor": 130.0,
         "delta_periodo_anterior": None}]}]}
    r = aplicar_deltas(ahora, anterior)
    assert r["campanas"][0]["metricas"][0]["delta_periodo_anterior"] == 30.0


def test_sin_anterior_el_delta_queda_en_none(db):
    """La primera corrida no tiene contra que comparar. Eso no es cero."""
    ahora = {"campanas": [{"campana": "x", "metricas": [
        {"id": "campana.x.gasto", "valor": 130.0,
         "delta_periodo_anterior": None}]}]}
    r = aplicar_deltas(ahora, None)
    assert r["campanas"][0]["metricas"][0]["delta_periodo_anterior"] is None


def test_una_metrica_nueva_no_tiene_delta(db):
    """Una campana que arranco esta semana no existia en el snapshot anterior."""
    anterior = {"campanas": [{"campana": "x", "metricas": [
        {"id": "campana.x.gasto", "valor": 100.0}]}]}
    ahora = {"campanas": [{"campana": "y", "metricas": [
        {"id": "campana.y.gasto", "valor": 50.0,
         "delta_periodo_anterior": None}]}]}
    r = aplicar_deltas(ahora, anterior)
    assert r["campanas"][0]["metricas"][0]["delta_periodo_anterior"] is None


def test_una_metrica_sin_valor_no_inventa_delta(db):
    anterior = {"campanas": [{"campana": "x", "metricas": [
        {"id": "campana.x.cpl", "valor": 20.0}]}]}
    ahora = {"campanas": [{"campana": "x", "metricas": [
        {"id": "campana.x.cpl", "valor": None,
         "delta_periodo_anterior": None}]}]}
    r = aplicar_deltas(ahora, anterior)
    assert r["campanas"][0]["metricas"][0]["delta_periodo_anterior"] is None


def test_los_deltas_alcanzan_a_los_segmentos(db):
    anterior = {"segmentos": [{"pregunta": "p", "valores": [
        {"valor_declarado": "v", "metricas": [
            {"id": "segmento.p.v.tasa_demo", "valor": 0.1}]}]}]}
    ahora = {"segmentos": [{"pregunta": "p", "valores": [
        {"valor_declarado": "v", "metricas": [
            {"id": "segmento.p.v.tasa_demo", "valor": 0.25,
             "delta_periodo_anterior": None}]}]}]}
    r = aplicar_deltas(ahora, anterior)
    assert (r["segmentos"][0]["valores"][0]["metricas"][0]
            ["delta_periodo_anterior"] == 0.15)


def test_los_deltas_alcanzan_a_tiempos_y_recordatorios(db):
    anterior = {"tiempos": [{"id": "tiempos.dias_a_demo", "valor": 10.0}]}
    ahora = {"tiempos": [{"id": "tiempos.dias_a_demo", "valor": 4.0,
                          "delta_periodo_anterior": None}]}
    r = aplicar_deltas(ahora, anterior)
    assert r["tiempos"][0]["delta_periodo_anterior"] == -6.0


def test_el_modulo_no_importa_anthropic():
    """La fase 1 no gasta un token. Se verifica sobre el fuente, igual que en
    tests/test_linkedin_sin_api.py."""
    fuente = io.open("services/radiografia.py", encoding="utf-8").read()
    assert "anthropic" not in fuente.lower()
