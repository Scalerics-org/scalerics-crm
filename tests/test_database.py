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


def test_insert_business_guarda_el_email(tmp_path):
    """insert_business arma el INSERT a mano y no mencionaba `email` en
    ningun lado: pasarlo en el dict se descartaba en silencio aunque la
    columna existiera y estuviera en ALLOWED_COLUMNS (esa constante solo la
    usa update_business, no este INSERT)."""
    from database import init_db, insert_business, get_business

    db = str(tmp_path / "t.db")
    init_db(db)
    biz_id = insert_business(db, {
        "name": "Negocio Con Mail",
        "phone": "+59899000001",
        "email": "prueba@ejemplo.com",
        "source": "meta",
    })
    assert biz_id
    assert get_business(db, biz_id)["email"] == "prueba@ejemplo.com"


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


# ── Migración: panel "meta" para los roles que ya existen ────────────────────

def _roles(db):
    import json
    import sqlite3

    conn = sqlite3.connect(db)
    filas = conn.execute("SELECT name, panel_access FROM roles").fetchall()
    conn.close()
    return {nombre: json.loads(acceso) for nombre, acceso in filas}


def test_roles_existentes_reciben_el_panel_meta(tmp_path):
    """El bloque que siembra roles solo corre con la tabla vacía. En producción
    los roles ya existen: sin migración ningún usuario no-admin ve el panel."""
    import json
    import sqlite3

    from database import init_db

    db = str(tmp_path / "prod.db")
    init_db(db)

    # Simular producción: roles ya creados, sin "meta" en el panel_access
    conn = sqlite3.connect(db)
    conn.execute("UPDATE roles SET panel_access = ?", (json.dumps(["cola", "seguimientos", "wa"]),))
    conn.commit()
    conn.close()

    init_db(db)  # el arranque siguiente

    for nombre, paneles in _roles(db).items():
        assert "meta" in paneles, f"el rol {nombre} tiene que ver el panel de Meta"
        assert "cola" in paneles and "wa" in paneles, "no se pisa el resto del array"


def test_migracion_de_panel_es_idempotente(tmp_path):
    """Corre en cada arranque: no puede acumular 'meta' ni reescribir de más."""
    import json
    import sqlite3

    from database import init_db

    db = str(tmp_path / "prod.db")
    init_db(db)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE roles SET panel_access = ?", (json.dumps(["cola"]),))
    conn.commit()
    conn.close()

    init_db(db)
    primera = _roles(db)
    init_db(db)
    init_db(db)
    tercera = _roles(db)

    assert primera == tercera, "arrancar de nuevo no cambia nada"
    for paneles in tercera.values():
        assert paneles.count("meta") == 1, "'meta' no se puede duplicar en el array"


def test_panel_access_ilegible_no_rompe_el_arranque(tmp_path):
    """Un panel_access vacío, con JSON inválido o que no es una lista se saltea
    sin pisarlo: init_db corre en cada arranque y no puede tirar la app."""
    import sqlite3

    from database import init_db

    db = str(tmp_path / "rara.db")
    init_db(db)

    conn = sqlite3.connect(db)
    conn.execute("UPDATE roles SET panel_access = 'no-es-json' WHERE name = 'Caller'")
    conn.execute("UPDATE roles SET panel_access = '' WHERE name = 'Ventas'")
    conn.execute("UPDATE roles SET panel_access = '{\"cola\": true}' WHERE name = 'Admin'")
    conn.commit()
    conn.close()

    init_db(db)  # no explota

    conn = sqlite3.connect(db)
    valores = dict(conn.execute("SELECT name, panel_access FROM roles").fetchall())
    conn.close()
    assert valores["Caller"] == "no-es-json", "lo que no se entiende no se pisa"
    assert valores["Ventas"] == ""
    assert valores["Admin"] == '{"cola": true}'


def test_panel_access_en_null_no_rompe(tmp_path):
    """panel_access es NOT NULL en el esquema, pero una base vieja puede traer
    NULL: la migración tiene que saltearlo igual, no reventar el arranque."""
    import sqlite3

    from database import init_db

    db = str(tmp_path / "null.db")
    init_db(db)

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA writable_schema = ON")
    conn.execute(
        "UPDATE sqlite_master SET sql = replace(sql, 'panel_access TEXT NOT NULL', 'panel_access TEXT') "
        "WHERE type = 'table' AND name = 'roles'"
    )
    conn.execute("PRAGMA writable_schema = OFF")
    conn.commit()
    conn.close()

    conn = sqlite3.connect(db)
    conn.execute("UPDATE roles SET panel_access = NULL WHERE name = 'Caller'")
    conn.commit()
    conn.close()

    init_db(db)  # no explota

    conn = sqlite3.connect(db)
    valor = conn.execute("SELECT panel_access FROM roles WHERE name = 'Caller'").fetchone()[0]
    conn.close()
    assert valor is None, "un NULL se saltea, no se inventa un array"
