import json
import sqlite3

import pytest

from scripts.backfill_meta_emails import backfill


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    conn = sqlite3.connect(ruta)
    conn.execute("""
        CREATE TABLE businesses (
            id INTEGER PRIMARY KEY, name TEXT, email TEXT,
            source TEXT, form_data TEXT
        )
    """)
    filas = [
        (1, "Con mail", None, "meta", json.dumps({"email": "uno@ejemplo.com", "full_name": "Con mail"})),
        (2, "Sin mail", None, "meta", json.dumps({"full_name": "Sin mail"})),
        (3, "Ya tenia", "viejo@ejemplo.com", "meta", json.dumps({"email": "nuevo@ejemplo.com"})),
        (4, "No es de meta", None, "google", json.dumps({"email": "ajeno@ejemplo.com"})),
    ]
    conn.executemany("INSERT INTO businesses VALUES (?,?,?,?,?)", filas)
    conn.commit()
    conn.close()
    return ruta


def test_sube_el_mail_desde_form_data(db):
    res = backfill(db)

    conn = sqlite3.connect(db)
    por_id = dict(conn.execute("SELECT id, email FROM businesses").fetchall())
    conn.close()

    assert por_id[1] == "uno@ejemplo.com"
    assert por_id[2] is None, "sin email en form_data no se inventa nada"
    assert por_id[3] == "viejo@ejemplo.com", "no pisa un mail que ya estaba"
    assert por_id[4] is None, "no toca leads de otra fuente"
    assert res == {"actualizados": 1, "sin_email": 1, "ya_tenian": 1}


def test_dry_run_no_escribe(db):
    res = backfill(db, dry_run=True)

    conn = sqlite3.connect(db)
    email = conn.execute("SELECT email FROM businesses WHERE id=1").fetchone()[0]
    conn.close()

    assert email is None, "dry-run no escribe"
    assert res["actualizados"] == 1, "pero si reporta lo que haria"
