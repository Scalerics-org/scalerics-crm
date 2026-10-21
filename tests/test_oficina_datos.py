"""Los agentes de la Oficina leen el CRM, sin poder escribir ni ver secretos."""

import sqlite3

import pytest

from services import oficina_datos as datos


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "datos.db")
    conn = sqlite3.connect(ruta)
    conn.executescript("""
        CREATE TABLE leads (id INTEGER PRIMARY KEY, nombre TEXT, etapa TEXT);
        INSERT INTO leads (nombre, etapa) VALUES ('Trouville', 'cliente'), ('TNT Burger', 'demo');
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, password_hash TEXT);
        INSERT INTO users (email, password_hash) VALUES ('juan@x.com', 'pbkdf2:secreto');
        CREATE TABLE oauth_tokens (id INTEGER PRIMARY KEY, valor TEXT);
        INSERT INTO oauth_tokens (valor) VALUES ('abc');
        CREATE TABLE credenciales (id INTEGER PRIMARY KEY, servicio TEXT);
        CREATE TABLE numeros (n INTEGER);
    """)
    conn.executemany("INSERT INTO numeros VALUES (?)", [(i,) for i in range(datos.TOPE_FILAS + 20)])
    conn.commit()
    conn.close()
    return ruta


def test_esquema_sin_tablas_ni_columnas_secretas(db):
    tablas = {t["tabla"]: t for t in datos.esquema(db)}
    assert set(tablas) == {"leads", "users", "numeros"}
    assert tablas["users"]["columnas"] == ["id", "email"]
    assert tablas["leads"]["filas"] == 2


def test_lee_y_agrupa(db):
    r = datos.consultar(db, "SELECT etapa, COUNT(*) AS n FROM leads GROUP BY etapa ORDER BY etapa")
    assert r["columnas"] == ["etapa", "n"] and r["filas"] == [["cliente", 1], ["demo", 1]]


def test_las_columnas_secretas_salen_vacias(db):
    r = datos.consultar(db, "SELECT * FROM users")
    assert r["filas"] == [[1, "juan@x.com", None]]


@pytest.mark.parametrize("sql", [
    "SELECT * FROM oauth_tokens",
    "SELECT servicio FROM credenciales",
    "SELECT l.nombre FROM leads l JOIN oauth_tokens t ON t.id = l.id",
    "DELETE FROM leads",
    "UPDATE leads SET etapa = 'x'",
    "INSERT INTO leads (nombre) VALUES ('x')",
    "DROP TABLE leads",
    "PRAGMA table_info(users)",
    "ATTACH DATABASE 'otra.db' AS otra",
    "SELECT 1; DELETE FROM leads",
    "",
])
def test_no_puede_escribir_ni_ver_secretos(db, sql):
    with pytest.raises(datos.ConsultaInvalida):
        datos.consultar(db, sql)
    assert datos.consultar(db, "SELECT COUNT(*) FROM leads")["filas"] == [[2]]


def test_tope_de_filas(db):
    r = datos.consultar(db, "SELECT n FROM numeros")
    assert len(r["filas"]) == datos.TOPE_FILAS and r["recortado"] is True


def test_corta_las_consultas_eternas(db, monkeypatch):
    monkeypatch.setattr(datos, "TOPE_SEGUNDOS", 0.2)
    with pytest.raises(datos.ConsultaInvalida):
        datos.consultar(db, "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT MAX(x) FROM c")


def test_por_la_api_solo_con_el_token(tmp_path, monkeypatch):
    import dashboard
    from database import init_db
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("OFICINA_BOT_TOKEN", "t" * 40)
    ruta = str(tmp_path / "api.db")
    init_db(ruta)
    app = dashboard.create_app(ruta)
    c = app.test_client()
    h = {"x-oficina-token": "t" * 40}
    nombres = [t["tabla"] for t in c.get("/api/oficina-bot/esquema", headers=h).get_json()["tablas"]]
    assert "leads" in nombres or "businesses" in nombres
    assert not [n for n in nombres if datos.TABLAS_SECRETAS.search(n)]
    assert c.post("/api/oficina-bot/consulta", headers=h, json={"sql": "SELECT 1 AS uno"}).get_json()["filas"] == [[1]]
    assert c.post("/api/oficina-bot/consulta", headers=h, json={"sql": "DELETE FROM users"}).status_code == 400
    assert c.post("/api/oficina-bot/consulta", json={"sql": "SELECT 1"}).status_code == 403
