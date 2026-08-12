import json
import sqlite3

import pytest

from scripts.restore_meta_leads import restore

CLIENTES_VIVOS = [598, 626]


@pytest.fixture
def db(tmp_path):
    ruta = tmp_path / "leads.db"
    conn = sqlite3.connect(ruta)
    conn.execute("""
        CREATE TABLE businesses (
            id INTEGER PRIMARY KEY, name TEXT, phone TEXT, city TEXT,
            category TEXT, status TEXT, notes TEXT, score INTEGER,
            source TEXT, form_data TEXT, scraped_at TEXT
        )
    """)
    # Un cliente real que sobrevivió al borrado, con source en NULL
    conn.execute(
        "INSERT INTO businesses (id, name, source, form_data) VALUES (598, 'Plan Arq', NULL, NULL)"
    )
    conn.commit()
    conn.close()
    return str(ruta)


@pytest.fixture
def backup(tmp_path):
    ruta = tmp_path / "meta_leads.json"
    datos = {
        "businesses": [
            {"id": 598, "name": "Plan Arq", "source": "meta", "form_data": '{"negocio":"arq"}'},
            {"id": 9001, "name": "Lead Borrado", "source": "meta", "form_data": '{"negocio":"x"}'},
        ],
        "lead_events": [], "call_logs": [], "lead_attachments": [],
        "budgets": [], "demos": [], "meetings": [], "activity_log": [],
    }
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    return str(ruta)


def test_actualiza_clientes_vivos_sin_duplicarlos(db, backup):
    res = restore(db, backup)

    conn = sqlite3.connect(db)
    filas = conn.execute("SELECT COUNT(*) FROM businesses WHERE id = 598").fetchone()[0]
    origen = conn.execute("SELECT source FROM businesses WHERE id = 598").fetchone()[0]
    conn.close()

    assert filas == 1, "el cliente vivo no se puede duplicar"
    assert origen == "meta", "al cliente vivo hay que devolverle el source"
    assert res["updated"] == 1
    assert res["inserted"] == 1


def test_dry_run_no_toca_la_base(db, backup):
    res = restore(db, backup, dry_run=True)

    conn = sqlite3.connect(db)
    total = conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0]
    conn.close()

    assert total == 1, "dry-run no escribe"
    assert res["inserted"] == 1, "pero sí reporta lo que haría"
