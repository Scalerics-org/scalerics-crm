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
    update_business(db_path, business_id, notes="llamar esta semana", status="contactado")
    results = get_businesses_by_status(db_path, "contactado")
    assert len(results) == 1
    assert results[0]["notes"] == "llamar esta semana"

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


def test_insert_business_guarda_los_campos_basicos(tmp_path):
    """Un insert con los campos minimos tiene que quedar consultable."""
    from database import init_db, insert_business, get_business

    db = str(tmp_path / "t.db")
    init_db(db)
    biz_id = insert_business(db, {
        "name": "Negocio Test",
        "phone": "+59899000000",
        "city": "Montevideo",
        "category": "Test",
        "status": "scraped",
        "source": "scraped",
    })
    assert biz_id
    assert get_business(db, biz_id)["name"] == "Negocio Test"


def test_base_nueva_tiene_las_columnas_de_permisos(tmp_path):
    """init_db agregaba las columnas de users antes de crear la tabla: en una
    base nueva el ALTER fallaba en silencio y quedaban sin existir."""
    import sqlite3

    from database import init_db

    db = str(tmp_path / "nueva.db")
    init_db(db)

    cols = [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(users)")]
    assert "panel_access" in cols
    assert "role_id" in cols


def test_get_all_users_anda_en_base_nueva(tmp_path):
    from database import get_all_users, init_db

    db = str(tmp_path / "nueva.db")
    init_db(db)

    assert get_all_users(db) == []
