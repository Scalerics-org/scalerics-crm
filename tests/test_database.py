import pytest
from database import init_db, insert_business, update_business, get_businesses_by_status

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
