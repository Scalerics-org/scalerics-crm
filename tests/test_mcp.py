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


# ── buscar_leads ─────────────────────────────────────────────────────────────

def _texto(r):
    return json.loads(r["content"][0]["text"])


def test_buscar_por_nombre_sin_archivados(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "buscar_leads", {"texto": "espiga"}))
    assert [l["id"] for l in r["leads"]] == [1, 3]
    assert r["leads"][0] == {"id": 1, "nombre": "Panadería La Espiga", "etapa": "demo_1",
                             "telefono": "099111222", "email": "espiga@mail.com",
                             "ciudad": "Montevideo", "rubro": "panaderia", "origen": "meta",
                             "ultimo_evento_at": "2026-10-01 10:00:00"}


def test_buscar_por_mail_y_telefono(sembrada, ana):
    u = {"id": ana, "name": "Ana"}
    assert [l["id"] for l in _texto(mt.ejecutar(sembrada, u, "buscar_leads", {"texto": "BAR@mail"}))["leads"]] == [3]
    assert [l["id"] for l in _texto(mt.ejecutar(sembrada, u, "buscar_leads", {"texto": "099 333-444"}))["leads"]] == [2]


def test_buscar_respeta_limite_y_lo_dice(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "buscar_leads", {"texto": "espiga", "limite": 1}))
    assert len(r["leads"]) == 1 and r["hay_mas"] is True


def test_buscar_sin_resultados(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "buscar_leads", {"texto": "zzz"}))
    assert r == {"leads": [], "hay_mas": False}


# ── ver_lead ─────────────────────────────────────────────────────────────────

def test_ver_lead_trae_ficha_completa(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "ver_lead", {"lead_id": 1}))
    assert r["id"] == 1 and r["nombre"] == "Panadería La Espiga" and r["etapa"] == "demo_1"
    assert r["notas"] == "Quiere web con pedidos" and r["origen"] == "meta"
    assert r["responsables"] == {"encargado": "Ana", "mantenimiento": None, "cobros": None}
    assert r["relevamiento"]["lead_name"] == "Lucía" and r["relevamiento"]["needs"] == "pedidos online"
    assert [e["new_status"] for e in r["eventos"]] == ["demo_1", "demo_agendada"]
    assert r["llamadas"][0]["outcome"] == "contestó"
    # Nada interno.
    for interno in ("form_data", "meta_lead_id", "semaforo_origen", "demo_html_path", "encargado_id"):
        assert interno not in r


def test_ver_lead_sin_relevamiento(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "ver_lead", {"lead_id": 2}))
    assert r["relevamiento"] == {} and r["eventos"] == [] and r["llamadas"] == []
    assert r["responsables"]["encargado"] == "Juan"


def test_ver_lead_inexistente_es_error_de_herramienta(sembrada, ana):
    r = mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "ver_lead", {"lead_id": 999})
    assert r["isError"] is True
    assert "999" in r["content"][0]["text"]
    conn = sqlite3.connect(sembrada)
    assert conn.execute("SELECT ok, error FROM mcp_llamadas ORDER BY id DESC LIMIT 1").fetchone() == (0, "No existe el lead 999")
    conn.close()


# ── pipeline ─────────────────────────────────────────────────────────────────

def test_pipeline_agrupa_en_orden_y_excluye_archivados_y_clientes(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "pipeline", {}))
    assert [e["etapa"] for e in r["etapas"]] == ["demo_1", "presupuesto_enviado"]
    assert r["etapas"][0]["total"] == 1
    lead = r["etapas"][0]["leads"][0]
    assert lead["id"] == 1 and lead["encargado"] == "Ana" and lead["nombre"] == "Panadería La Espiga"
    assert isinstance(lead["dias_sin_movimiento"], int)
    assert r["total"] == 2 and r["truncado"] is False


def test_pipeline_filtra_por_etapa(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "pipeline", {"etapa": "presupuesto_enviado"}))
    assert [e["etapa"] for e in r["etapas"]] == ["presupuesto_enviado"]
    assert r["etapas"][0]["leads"][0]["id"] == 2


def test_pipeline_filtra_por_responsable(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "pipeline", {"responsable": "jua"}))
    assert r["total"] == 1 and r["etapas"][0]["leads"][0]["encargado"] == "Juan"


def test_pipeline_incluye_clientes(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "pipeline", {"incluir_clientes": True}))
    assert [e["etapa"] for e in r["etapas"]] == ["demo_1", "presupuesto_enviado", "cerrado"]


def test_pipeline_etapa_de_cliente_sin_incluir_clientes_igual_la_trae(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "pipeline", {"etapa": "cerrado"}))
    assert r["total"] == 1


def test_pipeline_vacio(sembrada, ana):
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "pipeline", {"etapa": "rechazo"}))
    assert r == {"etapas": [], "total": 0, "truncado": False}


def test_pipeline_trunca_a_200(sembrada, ana):
    conn = sqlite3.connect(sembrada)
    conn.executemany("INSERT INTO businesses (name, phone, crm_status) VALUES (?,?,?)",
                     [(f"L{i}", f"09{i:07d}", "follow_up_1") for i in range(205)])
    conn.commit()
    conn.close()
    r = _texto(mt.ejecutar(sembrada, {"id": ana, "name": "Ana"}, "pipeline", {"etapa": "follow_up_1"}))
    assert r["truncado"] is True and r["total"] == 200
    assert "Filtr" in r["aviso"]
