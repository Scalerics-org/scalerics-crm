"""Tests de fusion de leads duplicados.

Fusionar un lead consigo mismo lo BORRABA: los UPDATE quedaban en no-op (movian
cada fila a donde ya estaba) y despues el DELETE se lo llevaba. Con las foreign
keys activas la cascada ademas arrastra reuniones, adjuntos, eventos y llamadas.
"""

import pytest

from database import (_connect, add_call_log, add_lead_event, create_meeting,
                      get_business, init_db, insert_business, merge_business)


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "m.db")
    init_db(p)
    return p


def _contar(db_path, tabla, col, valor):
    conn = _connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {tabla} WHERE {col}=?", (valor,)).fetchone()[0]
    finally:
        conn.close()


def test_fusionar_consigo_mismo_no_borra_el_lead(db):
    lead = insert_business(db, {"name": "Ana", "phone": "+59899000001"})
    add_lead_event(db, lead, "interesado", "")
    create_meeting(db, lead, title="R", start_at="2026-09-01T10:00:00",
                   end_at="2026-09-01T11:00:00")

    with pytest.raises(ValueError, match="consigo mismo"):
        merge_business(db, lead, lead)

    assert get_business(db, lead) is not None, "el lead se borro"
    assert _contar(db, "lead_events", "lead_id", lead) == 1
    assert _contar(db, "meetings", "client_id", lead) == 1


def test_fusion_normal_transfiere_todo_y_borra_el_duplicado(db):
    dup = insert_business(db, {"name": "Ana dup", "phone": "+59899000001"})
    real = insert_business(db, {"name": "Ana", "phone": "+59899000002"})
    add_lead_event(db, dup, "interesado", "")
    add_call_log(db, dup, "contestó", "")
    create_meeting(db, dup, title="R", start_at="2026-09-01T10:00:00",
                   end_at="2026-09-01T11:00:00")

    merge_business(db, dup, real)

    assert get_business(db, dup) is None, "el duplicado deberia desaparecer"
    assert get_business(db, real) is not None
    # todo lo del duplicado quedo colgando del lead real
    assert _contar(db, "lead_events", "lead_id", real) == 1
    assert _contar(db, "call_logs", "lead_id", real) == 1
    assert _contar(db, "meetings", "client_id", real) == 1
    # y nada quedo huerfano
    conn = _connect(db)
    try:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()
