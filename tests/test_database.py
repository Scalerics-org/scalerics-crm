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


def test_migracion_de_meta_reminders_conserva_las_filas_viejas(tmp_path):
    """La tabla vieja tenia UNIQUE(business_id) y una fila por lead. Las 15
    filas de produccion tienen tokens publicados dentro de mails ya enviados:
    si se pierden, el link de baja de esos mails deja de funcionar."""
    import sqlite3
    from database import init_db

    ruta = str(tmp_path / "viejo.db")
    conn = sqlite3.connect(ruta)
    conn.execute("""
        CREATE TABLE meta_reminders (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id     INTEGER NOT NULL UNIQUE,
            token           TEXT NOT NULL UNIQUE,
            sent_at         TEXT NOT NULL,
            unsubscribed_at TEXT
        )""")
    conn.execute(
        "INSERT INTO meta_reminders (business_id, token, sent_at, unsubscribed_at) "
        "VALUES (?,?,?,?)", (650, "tok-uno", "2026-08-18 14:41:18", None))
    conn.execute(
        "INSERT INTO meta_reminders (business_id, token, sent_at, unsubscribed_at) "
        "VALUES (?,?,?,?)", (651, "tok-dos", "2026-08-18 14:41:19", "2026-08-18 15:00:00"))
    conn.commit()
    conn.close()

    init_db(ruta)

    conn = sqlite3.connect(ruta)
    try:
        filas = conn.execute(
            "SELECT business_id, numero, token, sent_at, unsubscribed_at "
            "FROM meta_reminders ORDER BY business_id").fetchall()
        cols = [c[1] for c in conn.execute("PRAGMA table_info(meta_reminders)")]
    finally:
        conn.close()

    assert filas == [
        (650, 1, "tok-uno", "2026-08-18 14:41:18", None),
        (651, 1, "tok-dos", "2026-08-18 14:41:19", "2026-08-18 15:00:00"),
    ], "las filas viejas son el contacto 1 y conservan su token"
    assert "numero" in cols


def test_el_mismo_lead_puede_tener_varios_contactos(tmp_path):
    import sqlite3
    import pytest
    from database import init_db

    ruta = str(tmp_path / "nuevo.db")
    init_db(ruta)

    conn = sqlite3.connect(ruta)
    try:
        conn.execute("INSERT INTO meta_reminders (business_id, numero, token, sent_at) "
                     "VALUES (?,?,?,?)", (10, 1, "t1", "2026-08-01 10:00:00"))
        conn.execute("INSERT INTO meta_reminders (business_id, numero, token, sent_at) "
                     "VALUES (?,?,?,?)", (10, 2, "t2", "2026-08-11 10:00:00"))
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO meta_reminders (business_id, numero, token, sent_at) "
                         "VALUES (?,?,?,?)", (10, 2, "t3", "2026-08-12 10:00:00"))
    finally:
        conn.close()


def test_migracion_tolera_un_reintento_despues_de_un_crash_a_mitad_de_camino(tmp_path):
    """`CREATE TABLE meta_reminders_nueva` no esta dentro de una transaccion
    (es DDL y todavia no hay ningun INSERT/UPDATE/DELETE que la abra), asi que
    SQLite la autocommitea sola en el momento. Si el proceso muere justo
    despues del DROP TABLE meta_reminders y antes del commit final, ese DROP
    se revierte (la tabla vieja sobrevive con sus tokens) pero la
    `meta_reminders_nueva` ya creada queda huerfana y persistida, vacia. Un
    reintento de init_db() no puede reventar contra esa huerfana."""
    import sqlite3
    from database import init_db

    ruta = str(tmp_path / "crasheada.db")
    conn = sqlite3.connect(ruta)
    conn.execute("""
        CREATE TABLE meta_reminders (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id     INTEGER NOT NULL UNIQUE,
            token           TEXT NOT NULL UNIQUE,
            sent_at         TEXT NOT NULL,
            unsubscribed_at TEXT
        )""")
    conn.execute(
        "INSERT INTO meta_reminders (business_id, token, sent_at, unsubscribed_at) "
        "VALUES (?,?,?,?)", (650, "tok-uno", "2026-08-18 14:41:18", None))
    # Lo que deja un crash a mitad de la migracion: la huerfana ya creada,
    # con el esquema nuevo pero sin filas (el INSERT...SELECT que la iba a
    # llenar nunca llego a comittear).
    conn.execute("""
        CREATE TABLE meta_reminders_nueva (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id     INTEGER NOT NULL,
            numero          INTEGER NOT NULL DEFAULT 1,
            token           TEXT NOT NULL UNIQUE,
            sent_at         TEXT NOT NULL,
            unsubscribed_at TEXT,
            UNIQUE (business_id, numero)
        )
    """)
    conn.commit()
    conn.close()

    init_db(ruta)  # no puede explotar con "table meta_reminders_nueva already exists"

    conn = sqlite3.connect(ruta)
    try:
        filas = conn.execute(
            "SELECT business_id, numero, token, sent_at, unsubscribed_at "
            "FROM meta_reminders ORDER BY business_id").fetchall()
        huerfana = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='meta_reminders_nueva'"
        ).fetchone()
    finally:
        conn.close()

    assert filas == [(650, 1, "tok-uno", "2026-08-18 14:41:18", None)], (
        "la fila vieja tiene que terminar migrada con su token intacto"
    )
    assert huerfana is None, "la huerfana no puede quedar dando vueltas despues de migrar"


def test_insert_business_guarda_website(db_path):
    """El sitio web tiene que sobrevivir al INSERT.

    insert_business arma la lista de columnas a mano: una columna que existe
    en la tabla pero no en esa lista se pierde sin dar error. Ya paso con
    `email` y los leads de Meta.
    """
    from database import insert_business, get_all_businesses

    insert_business(db_path, {
        "name": "Inmobiliaria Ejemplo",
        "phone": "+598 2900 1111",
        "maps_url": "https://maps.google.com/?cid=1",
        "website": "https://inmobiliariaejemplo.com.uy",
        "source": "discovery",
    })

    negocio = get_all_businesses(db_path)[0]
    assert negocio["website"] == "https://inmobiliariaejemplo.com.uy"
    assert negocio["source"] == "discovery"

def test_update_business_puede_setear_website(db_path):
    """`website` tiene que estar en ALLOWED_COLUMNS o update_business la rechaza."""
    from database import insert_business, update_business, get_all_businesses

    bid = insert_business(db_path, {
        "name": "Sin Sitio",
        "phone": "+598 2900 2222",
        "maps_url": "https://maps.google.com/?cid=2",
    })

    update_business(db_path, bid, website="https://aparecio.com.uy")

    assert get_all_businesses(db_path)[0]["website"] == "https://aparecio.com.uy"

def test_website_es_nulo_cuando_no_se_manda(db_path):
    from database import insert_business, get_all_businesses

    insert_business(db_path, {
        "name": "Sin Web",
        "phone": "+598 2900 3333",
        "maps_url": "https://maps.google.com/?cid=3",
    })

    assert get_all_businesses(db_path)[0]["website"] is None


# ── Filtro de cohorte ────────────────────────────────────────────────────────
# La cohorte se compone con crm_status en vez de reemplazarlo. Importa porque
# despues de importar los estados reales de la planilla (27-8-2026), la vista de
# seguimientos paso de ~35 a 128 leads y tres cuartos eran de Meta: sin poder
# separar cohortes, la lista de a quien llamar deja de ser usable.

def _biz(conn, bid, crm_status, source):
    conn.execute(
        "INSERT INTO businesses (id, name, crm_status, source) VALUES (?,?,?,?)",
        (bid, f"Negocio {bid}", crm_status, source),
    )


def test_la_cohorte_se_compone_con_el_estado(db_path):
    import sqlite3
    from database import get_all_businesses, COHORTE_SIN_WEB

    conn = sqlite3.connect(db_path)
    _biz(conn, 1, "interesado", "meta")
    _biz(conn, 2, "interesado", None)            # padron sin web
    _biz(conn, 3, "interesado", "discovery")
    _biz(conn, 4, "llamar_despues", "meta")      # otro estado, misma cohorte
    conn.commit()
    conn.close()

    todos = get_all_businesses(db_path, crm_status="interesado")
    solo_meta = get_all_businesses(db_path, crm_status="interesado", cohorte="meta")
    sin_web = get_all_businesses(db_path, crm_status="interesado", cohorte=COHORTE_SIN_WEB)
    disc = get_all_businesses(db_path, crm_status="interesado", cohorte="discovery")

    assert sorted(b["id"] for b in todos) == [1, 2, 3]
    assert [b["id"] for b in solo_meta] == [1]
    assert [b["id"] for b in sin_web] == [2], "sin_web es source IS NULL"
    assert [b["id"] for b in disc] == [3]


def test_sin_cohorte_no_cambia_nada(db_path):
    import sqlite3
    from database import get_all_businesses

    conn = sqlite3.connect(db_path)
    _biz(conn, 1, "interesado", "meta")
    _biz(conn, 2, "interesado", None)
    conn.commit()
    conn.close()

    assert len(get_all_businesses(db_path, crm_status="interesado")) == 2
    assert len(get_all_businesses(db_path, crm_status="interesado", cohorte=None)) == 2


def test_la_cola_fria_sigue_sin_traer_leads_de_meta(db_path):
    """La exclusion de Meta en sin_contactar es deliberada y no la toca la cohorte."""
    import sqlite3
    from database import get_all_businesses

    conn = sqlite3.connect(db_path)
    _biz(conn, 1, "sin_contactar", "meta")
    _biz(conn, 2, "sin_contactar", None)
    conn.commit()
    conn.close()

    assert [b["id"] for b in get_all_businesses(db_path, crm_status="sin_contactar")] == [2]
    assert get_all_businesses(db_path, crm_status="sin_contactar", cohorte="meta") == []


def test_llamar_despues_conserva_su_orden_de_agenda(db_path):
    """El refactor saco el return anticipado; el orden por callback tiene que quedar."""
    import sqlite3
    from database import get_all_businesses

    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO businesses (id,name,crm_status,source,callback_date) VALUES (1,'sin fecha','llamar_despues','meta',NULL)")
    conn.execute("INSERT INTO businesses (id,name,crm_status,source,callback_date) VALUES (2,'tarde','llamar_despues','meta','2026-09-10')")
    conn.execute("INSERT INTO businesses (id,name,crm_status,source,callback_date) VALUES (3,'temprano','llamar_despues','meta','2026-09-01')")
    conn.commit()
    conn.close()

    assert [b["id"] for b in get_all_businesses(db_path, crm_status="llamar_despues")] == [3, 2, 1]


# ── La cola paginada en SQL ──────────────────────────────────────────────────
# El 28-8-2026 el CRM empezo a devolver 502 al pasar los 6.000 leads:
# get_all_businesses traia las 6.200 filas de la cola con las 32 columnas y
# recien despues filtraba y paginaba en Python, asi que paginar no ahorraba
# nada del lado del servidor. 7,32 MB de JSON por request, 0,67 s, y el worker
# muerto por memoria. Paginado en SQL son 26 KB y 1 ms.

def _muchos(db_path, n=120, source=None, estado="sin_contactar"):
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.executemany(
        "INSERT INTO businesses (id,name,phone,category,crm_status,source) VALUES (?,?,?,?,?,?)",
        [(i, f"Negocio {i:03d}", f"+5989{i:07d}", "ferreteria", estado, source)
         for i in range(1, n + 1)])
    conn.commit()
    conn.close()


def test_pagina_y_dice_cuantas_hay(db_path):
    from database import listar_leads
    _muchos(db_path, 120)
    r = listar_leads(db_path, crm_status="sin_contactar", page=1, por_pagina=50)
    assert r["total"] == 120 and r["pages"] == 3 and len(r["items"]) == 50
    assert listar_leads(db_path, crm_status="sin_contactar", page=3,
                        por_pagina=50)["items"].__len__() == 20


def test_solo_trae_las_columnas_de_la_lista(db_path):
    """Traer b.* eran 28 MB de objetos por request."""
    from database import listar_leads, COLUMNAS_LISTA
    _muchos(db_path, 3)
    item = listar_leads(db_path, crm_status="sin_contactar")["items"][0]
    assert set(item) == set(COLUMNAS_LISTA) | {"no_contesto_count", "no_interesa_count"}
    assert "form_data" not in item and "pitch_text" in item


def test_una_pagina_fuera_de_rango_no_revienta(db_path):
    from database import listar_leads
    _muchos(db_path, 10)
    r = listar_leads(db_path, crm_status="sin_contactar", page=99, por_pagina=50)
    assert r["page"] == 1 and len(r["items"]) == 10


def test_la_base_vacia_no_revienta(db_path):
    from database import listar_leads
    r = listar_leads(db_path, crm_status="sin_contactar")
    assert r == {"items": [], "total": 0, "pages": 1, "page": 1}


def test_filtra_por_busqueda_rubro_y_cohorte_en_sql(db_path):
    from database import listar_leads, COHORTE_SIN_WEB
    import sqlite3
    _muchos(db_path, 5)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE businesses SET source='discovery' WHERE id <= 2")
    conn.execute("UPDATE businesses SET category='panaderia' WHERE id = 5")
    conn.commit(); conn.close()

    assert listar_leads(db_path, crm_status="sin_contactar", search="cio 003")["total"] == 1
    assert listar_leads(db_path, crm_status="sin_contactar", category="panaderia")["total"] == 1
    assert listar_leads(db_path, crm_status="sin_contactar", cohorte="discovery")["total"] == 2
    assert listar_leads(db_path, crm_status="sin_contactar",
                        cohorte=COHORTE_SIN_WEB)["total"] == 3


def test_la_cola_fria_sigue_sin_traer_meta(db_path):
    from database import listar_leads
    _muchos(db_path, 4)
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE businesses SET source='meta' WHERE id <= 2")
    conn.commit(); conn.close()
    assert listar_leads(db_path, crm_status="sin_contactar")["total"] == 2


def test_llamar_despues_conserva_el_orden_de_agenda_tambien_paginado(db_path):
    import sqlite3
    from database import listar_leads
    conn = sqlite3.connect(db_path)
    for i, fecha in [(1, None), (2, "2026-09-10"), (3, "2026-09-01")]:
        conn.execute("INSERT INTO businesses (id,name,crm_status,callback_date) "
                     "VALUES (?,?,'llamar_despues',?)", (i, f"N{i}", fecha))
    conn.commit(); conn.close()
    r = listar_leads(db_path, crm_status="llamar_despues")
    assert [i["id"] for i in r["items"]] == [3, 2, 1]
