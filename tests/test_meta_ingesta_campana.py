"""La ingesta de leads de Meta guarda la campana en columna, no solo en notes.

`notes` se sigue escribiendo igual que siempre: hay codigo y hay gente que lo
lee. Esto es aditivo.
"""

import io

import pytest

from database import _connect, init_db


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def test_los_fields_del_graph_piden_los_ids():
    """Sin pedirlos, la API no los manda y las columnas quedan vacias."""
    fuente = io.open("routes/meta.py", encoding="utf-8").read()
    assert fuente.count("campaign_id") >= 4, "falta campaign_id en algun fields"
    assert "adset_id" in fuente
    assert "ad_id" in fuente


def test_guardar_lead_escribe_las_columnas(db):
    from routes.meta import _guardar_campana_del_lead

    conn = _connect(db)
    try:
        cur = conn.execute("INSERT INTO businesses (name, phone, source) "
                           "VALUES ('Test', '+59899', 'meta')")
        conn.commit()
        lead_id = cur.lastrowid
    finally:
        conn.close()

    _guardar_campana_del_lead(db, lead_id, {
        "campaign_id": "120", "campaign_name": "Leads - UY - 2026",
        "adset_id": "121", "ad_id": "122", "ad_name": "Video corto",
    })

    conn = _connect(db)
    try:
        f = conn.execute("SELECT * FROM businesses WHERE id=?", (lead_id,)).fetchone()
    finally:
        conn.close()
    assert f["meta_campaign_id"] == "120"
    assert f["meta_campaign_name"] == "Leads - UY - 2026"
    assert f["meta_adset_id"] == "121"
    assert f["meta_ad_id"] == "122"
    assert f["meta_ad_name"] == "Video corto"


def test_un_lead_sin_campana_no_rompe(db):
    """Meta no siempre manda todo. Ausencia no es error."""
    from routes.meta import _guardar_campana_del_lead

    conn = _connect(db)
    try:
        cur = conn.execute("INSERT INTO businesses (name, phone, source) "
                           "VALUES ('Test', '+59898', 'meta')")
        conn.commit()
        lead_id = cur.lastrowid
    finally:
        conn.close()

    _guardar_campana_del_lead(db, lead_id, {})

    conn = _connect(db)
    try:
        f = conn.execute("SELECT meta_campaign_id FROM businesses WHERE id=?",
                         (lead_id,)).fetchone()
    finally:
        conn.close()
    assert f["meta_campaign_id"] is None


def test_no_pisa_una_campana_con_un_dato_vacio(db):
    """Una segunda pasada sin datos no puede borrar lo que ya se sabia."""
    from routes.meta import _guardar_campana_del_lead

    conn = _connect(db)
    try:
        cur = conn.execute("INSERT INTO businesses (name, phone, source) "
                           "VALUES ('Test', '+59897', 'meta')")
        conn.commit()
        lead_id = cur.lastrowid
    finally:
        conn.close()

    _guardar_campana_del_lead(db, lead_id, {"campaign_id": "120",
                                            "campaign_name": "Leads - UY - 2026"})
    _guardar_campana_del_lead(db, lead_id, {})

    conn = _connect(db)
    try:
        f = conn.execute("SELECT meta_campaign_id, meta_campaign_name "
                         "FROM businesses WHERE id=?", (lead_id,)).fetchone()
    finally:
        conn.close()
    assert f["meta_campaign_id"] == "120"
    assert f["meta_campaign_name"] == "Leads - UY - 2026"
