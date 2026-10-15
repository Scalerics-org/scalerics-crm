"""Las herramientas del conector MCP y el endpoint /mcp.

La base se siembra a mano con leads en distintas etapas. Los tests de las
herramientas llaman a `services.mcp_tools` directo; los del transporte pasan
por el test_client de Flask con un Bearer real.
"""

import json
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services import mcp_tools as mt
from services import oauth_tokens as ot


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_URL", "https://crm.test")
    monkeypatch.setenv("ADMIN_EMAIL", "nadie@scalerics.invalid")
    r = str(tmp_path / "leads.db")
    init_db(r)
    return r


@pytest.fixture
def ana(db):
    return create_user(db, name="Ana", email="ana@scalerics.com", phone="099",
                       password_hash=generate_password_hash("x" * 10))


@pytest.fixture
def juan(db):
    return create_user(db, name="Juan", email="juan@scalerics.com", phone="098",
                       password_hash=generate_password_hash("x" * 10))


@pytest.fixture
def sembrada(db, ana, juan):
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO businesses (id, name, phone, email, city, category, crm_status, source, "
        "encargado_id, last_event_at, archivado_en, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (1, "Panadería La Espiga", "099111222", "espiga@mail.com", "Montevideo", "panaderia",
             "demo_1", "meta", ana, "2026-10-01 10:00:00", None, "Quiere web con pedidos"),
            (2, "Ferretería Norte", "099333444", None, "Salto", "ferreteria",
             "presupuesto_enviado", None, juan, "2026-09-20 10:00:00", None, None),
            (3, "Bar Espiga", "099555666", "bar@mail.com", "Montevideo", "bar",
             "sin_contactar", None, None, None, None, None),
            (4, "Archivado Viejo", "099777888", "viejo@mail.com", "Rivera", "bar",
             "demo_1", None, None, None, "2026-09-23", None),
            (5, "Cliente Cerrado", "099999000", "c@mail.com", "Rivera", "bar",
             "cerrado", None, ana, "2026-08-01 10:00:00", None, None),
        ])
    conn.execute("INSERT INTO client_info (client_id, lead_name, rubro, needs) VALUES (1,'Lucía','panadería','pedidos online')")
    # created_at explícito: get_lead_events ordena por esa columna y dos
    # inserts en el mismo segundo dejarían el orden librado a SQLite.
    conn.executemany("INSERT INTO lead_events (lead_id, new_status, note, created_by, created_at) VALUES (?,?,?,?,?)",
                     [(1, "demo_agendada", "agendada para el martes", "Ana", "2026-09-25 10:00:00"),
                      (1, "demo_1", "demo dada", "Ana", "2026-10-01 10:00:00")])
    conn.execute("INSERT INTO call_logs (lead_id, outcome, notes, created_by) VALUES (1,'contestó','habló Lucía','Ana')")
    conn.commit()
    conn.close()
    return db


# ── registro de herramientas ─────────────────────────────────────────────────

def test_init_db_crea_mcp_llamadas(db):
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='mcp_llamadas'").fetchone()
    conn.close()


def test_listar_herramientas_tiene_las_tres_con_schema():
    hs = {h["name"]: h for h in mt.listar_herramientas()}
    assert set(hs) == {"buscar_leads", "ver_lead", "pipeline"}
    for h in hs.values():
        assert h["description"]
        assert h["inputSchema"]["type"] == "object"
        assert h["inputSchema"]["additionalProperties"] is False
        assert "fn" not in h
    assert hs["buscar_leads"]["inputSchema"]["required"] == ["texto"]
    assert hs["ver_lead"]["inputSchema"]["required"] == ["lead_id"]
    assert "demo_agendada" in hs["pipeline"]["description"]


@pytest.mark.parametrize("args", [
    {},                                  # falta texto
    {"texto": "a"},                      # muy corto
    {"texto": "ab", "limite": 0},        # fuera de rango
    {"texto": "ab", "limite": 51},
    {"texto": "ab", "limite": "5"},      # tipo
    {"texto": 5},
    {"texto": "ab", "otro": 1},          # additionalProperties
])
def test_validar_argumentos_rechaza(args):
    schema = {h["name"]: h for h in mt.listar_herramientas()}["buscar_leads"]["inputSchema"]
    with pytest.raises(mt.ArgumentosInvalidos):
        mt.validar_argumentos(schema, args)


def test_validar_argumentos_completa_defaults():
    schema = {h["name"]: h for h in mt.listar_herramientas()}["buscar_leads"]["inputSchema"]
    assert mt.validar_argumentos(schema, {"texto": "espiga"}) == {"texto": "espiga", "limite": 10}


def test_validar_enum_de_etapa():
    schema = {h["name"]: h for h in mt.listar_herramientas()}["pipeline"]["inputSchema"]
    with pytest.raises(mt.ArgumentosInvalidos):
        mt.validar_argumentos(schema, {"etapa": "inventada"})


def test_ejecutar_herramienta_desconocida(sembrada, ana):
    with pytest.raises(mt.ArgumentosInvalidos):
        mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "borrar_todo", {})


def test_ejecutar_registra_la_llamada(sembrada, ana):
    r = mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "buscar_leads", {"texto": "espiga"})
    assert r["isError"] is False
    conn = sqlite3.connect(sembrada)
    fila = conn.execute("SELECT user_id, user_name, herramienta, argumentos, ok, ms FROM mcp_llamadas").fetchone()
    conn.close()
    assert fila[0] == ana and fila[1] == "Ana" and fila[2] == "buscar_leads"
    assert json.loads(fila[3]) == {"texto": "espiga", "limite": 10}
    assert fila[4] == 1 and fila[5] >= 0
