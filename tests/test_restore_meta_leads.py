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


# --- Fix 2: las tablas relacionadas no usan "business_id" -------------------
#
# lead_events/call_logs/lead_attachments usan lead_id, budgets/demos/meetings
# usan client_id, y activity_log usa entity_type + entity_id. Si el filtro
# vuelve a buscar "business_id" (que no existe en ninguna tabla real), este
# test tiene que fallar: se comprobó revirtiendo a esa lógica vieja.

@pytest.fixture
def db_con_lead_events(tmp_path):
    ruta = tmp_path / "leads.db"
    conn = sqlite3.connect(ruta)
    conn.execute("""
        CREATE TABLE businesses (
            id INTEGER PRIMARY KEY, name TEXT, source TEXT, form_data TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE lead_events (
            id INTEGER PRIMARY KEY, lead_id INTEGER, new_status TEXT, note TEXT
        )
    """)
    # Cliente real, nunca se borró
    conn.execute(
        "INSERT INTO businesses (id, name, source, form_data) VALUES (598, 'Plan Arq', NULL, NULL)"
    )
    conn.commit()
    conn.close()
    return str(ruta)


@pytest.fixture
def backup_con_lead_events(tmp_path):
    ruta = tmp_path / "meta_leads.json"
    datos = {
        "businesses": [
            {"id": 598, "name": "Plan Arq", "source": "meta", "form_data": '{"negocio":"arq"}'},
            {"id": 9001, "name": "Lead Borrado", "source": "meta", "form_data": '{"negocio":"x"}'},
        ],
        "lead_events": [
            {"id": 1, "lead_id": 9001, "new_status": "contactado", "note": "evento del lead borrado"},
            {"id": 2, "lead_id": 598, "new_status": "contactado", "note": "evento del cliente vivo"},
        ],
        "call_logs": [], "lead_attachments": [],
        "budgets": [], "demos": [], "meetings": [], "activity_log": [],
    }
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    return str(ruta)


def test_restaura_lead_events_del_borrado_y_excluye_al_vivo(db_con_lead_events, backup_con_lead_events):
    restore(db_con_lead_events, backup_con_lead_events)

    conn = sqlite3.connect(db_con_lead_events)
    lead_ids = {fila[0] for fila in conn.execute("SELECT lead_id FROM lead_events")}
    conn.close()

    assert 9001 in lead_ids, "el evento del lead borrado tiene que volver"
    assert 598 not in lead_ids, "el evento del cliente vivo no se toca: ya está intacto en producción"


# --- Fix 1: los contadores tienen que reflejar lo que se escribió de verdad -

def test_correr_dos_veces_no_duplica_y_el_contador_no_miente(db, backup):
    restore(db, backup)
    res2 = restore(db, backup)

    conn = sqlite3.connect(db)
    total = conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0]
    conn.close()

    assert total == 2, "la segunda corrida no duplica nada"
    assert res2["inserted"] == 0, "el lead 9001 ya estaba: la segunda vez no se inserta de nuevo"
    assert res2["insert_conflicts"] == 1, "el intento de reinsertar 9001 tiene que quedar visible, no oculto"
    assert res2["updated"] == 1, "el cliente vivo se sigue actualizando (rowcount real, no un conteo ciego)"
