"""El esquema del modulo de marketing.

Todo aditivo: ninguna migracion altera una tabla, columna o consulta que ya
existia. La prueba de eso es que los leads siguen leyendose igual despues de
migrar.
"""

import json
import sqlite3

import pytest

from database import _connect, init_db


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def _columnas(db, tabla):
    conn = _connect(db)
    try:
        return {c[1] for c in conn.execute(f"PRAGMA table_info({tabla})")}
    finally:
        conn.close()


def test_businesses_tiene_las_columnas_de_meta(db):
    cols = _columnas(db, "businesses")
    assert {"meta_campaign_id", "meta_campaign_name", "meta_adset_id",
            "meta_ad_id", "meta_ad_name"} <= cols


def test_no_se_perdio_ninguna_columna_de_businesses(db):
    """La migracion es aditiva: lo que ya estaba sigue estando."""
    cols = _columnas(db, "businesses")
    assert {"id", "name", "crm_status", "source", "notes", "form_data",
            "scraped_at", "email", "website"} <= cols


def test_meta_insights_existe_con_su_unique(db):
    cols = _columnas(db, "meta_insights")
    assert {"date", "campaign_id", "campaign_name", "spend", "currency",
            "impressions", "clicks", "reach", "leads", "synced_at"} <= cols


def test_meta_insights_no_admite_dos_filas_del_mismo_dia_y_campana(db):
    conn = _connect(db)
    try:
        conn.execute("INSERT INTO meta_insights (date, campaign_id, spend) "
                     "VALUES ('2026-03-01', '123', 10.0)")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO meta_insights (date, campaign_id, spend) "
                         "VALUES ('2026-03-01', '123', 20.0)")
            conn.commit()
    finally:
        conn.close()


def test_radiografias_existe(db):
    cols = _columnas(db, "radiografias")
    assert {"generated_at", "period_start", "period_end", "dossier_json",
            "report_json", "model", "tokens_in", "tokens_out", "status",
            "error_message"} <= cols


def test_el_panel_marketing_le_llega_a_los_roles_que_ya_existian(db):
    """Un panel nuevo no le llega a nadie salvo admins si no se migra."""
    conn = _connect(db)
    try:
        # El vendedor de Fidelidad es de afuera: ningun reparto le suma paneles.
        filas = conn.execute("SELECT panel_access FROM roles "
                             "WHERE name != 'Vendedor Fidelidad'").fetchall()
    finally:
        conn.close()
    assert filas, "la siembra de roles no corrio"
    for fila in filas:
        assert "marketing" in json.loads(fila["panel_access"])


def test_init_db_es_idempotente(db):
    """Correrla dos veces no rompe ni duplica nada."""
    init_db(db)
    init_db(db)
    assert {"meta_campaign_id"} <= _columnas(db, "businesses")
