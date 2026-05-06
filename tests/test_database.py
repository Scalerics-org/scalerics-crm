import pytest
from database import init_db, insert_business, update_business, get_businesses_by_status, get_all_businesses

@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    return path

def test_init_creates_table(db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='businesses'")
    assert cursor.fetchone() is not None
    conn.close()

def test_insert_business(db_path):
    data = {
        "name": "Pizzería Don Pepe",
        "category": "Pizza",
        "address": "Av. 18 de Julio 100",
        "city": "Montevideo",
        "phone": "+598 2900 0000",
        "rating": 4.5,
        "review_count": 120,
        "maps_url": "https://maps.google.com/?cid=123",
    }
    business_id = insert_business(db_path, data)
    assert business_id is not None
    assert isinstance(business_id, int)

def test_insert_duplicate_maps_url_is_ignored(db_path):
    data = {"name": "Test", "maps_url": "https://maps.google.com/?cid=999"}
    id1 = insert_business(db_path, data)
    id2 = insert_business(db_path, data)
    assert id1 is not None
    assert id2 is None  # duplicado ignorado

def test_update_business(db_path):
    data = {"name": "Peluquería Ana", "maps_url": "https://maps.google.com/?cid=456"}
    business_id = insert_business(db_path, data)
    update_business(db_path, business_id, email="ana@gmail.com", status="email_found")
    results = get_businesses_by_status(db_path, "email_found")
    assert len(results) == 1
    assert results[0]["email"] == "ana@gmail.com"

def test_get_businesses_by_status_empty(db_path):
    results = get_businesses_by_status(db_path, "email_found")
    assert results == []

def test_get_all_businesses_returns_all(db_path):
    insert_business(db_path, {"name": "A", "maps_url": "http://a.com"})
    insert_business(db_path, {"name": "B", "maps_url": "http://b.com"})
    result = get_all_businesses(db_path)
    assert len(result) == 2

def test_notes_column_exists_after_init(db_path):
    rows = get_all_businesses(db_path)
    insert_business(db_path, {"name": "C", "maps_url": "http://c.com"})
    rows = get_all_businesses(db_path)
    assert "notes" in rows[0]

def test_update_notes(db_path):
    bid = insert_business(db_path, {"name": "D", "maps_url": "http://d.com"})
    update_business(db_path, bid, notes="llamar mañana")
    rows = get_all_businesses(db_path)
    assert rows[0]["notes"] == "llamar mañana"
