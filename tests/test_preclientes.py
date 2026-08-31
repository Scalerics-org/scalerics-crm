"""Pre-clientes, clientes activos y registro de demos."""

import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import (ETAPAS_CLIENTE, ETAPAS_PRECLIENTE, create_user, get_business,
                      init_db, insert_business, listar_demos_realizadas)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    db = str(tmp_path / "p.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


def _usuario(db, email):
    return create_user(db, name=email.split("@")[0], email=email, phone="099",
                       password_hash=generate_password_hash("x" * 10))


@pytest.fixture
def cli(app):
    uid = _usuario(app.config["_DB"], "jefe@test.com")
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Jefe"
    c._uid = uid
    return c


_tel = iter(range(500000, 599999))


def _lead(db, nombre, etapa=None):
    lid = insert_business(db, {"name": nombre, "phone": f"+598{next(_tel)}"})
    if etapa:
        from database import update_business
        update_business(db, lid, crm_status=etapa)
    return lid


# ── etapas ───────────────────────────────────────────────────────────────────

def test_las_etapas_se_sirven_desde_el_backend(app, cli):
    """El frontend las pide en vez de hardcodearlas: asi el tablero y los
    selectores no pueden divergir."""
    d = cli.get("/api/preclientes/etapas").get_json()
    assert [e["key"] for e in d["preclientes"]] == list(ETAPAS_PRECLIENTE)
    assert [e["key"] for e in d["clientes"]] == list(ETAPAS_CLIENTE)
    assert d["preclientes"][0]["label"] == "Demo agendada"


def test_las_etapas_cubren_el_ciclo_de_venta(app):
    for esperada in ("demo_agendada", "demo_1", "demo_2", "demo_3",
                     "presupuesto_enviado", "follow_up_1", "follow_up_2",
                     "acepto", "en_espera", "rechazo"):
        assert esperada in ETAPAS_PRECLIENTE


# ── tablero de pre-clientes ──────────────────────────────────────────────────

def test_el_tablero_agrupa_por_etapa(app, cli):
    db = app.config["_DB"]
    _lead(db, "Ferretería", "demo_1")
    _lead(db, "Panadería", "demo_1")
    _lead(db, "Bloquera", "presupuesto_enviado")

    d = cli.get("/api/preclientes").get_json()
    por_key = {e["key"]: e for e in d["etapas"]}
    assert por_key["demo_1"]["total"] == 2
    assert por_key["presupuesto_enviado"]["total"] == 1
    assert por_key["demo_3"]["total"] == 0
    assert d["total"] == 3


def test_el_tablero_devuelve_todas_las_columnas_aunque_esten_vacias(app, cli):
    """Una columna vacia tiene que dibujarse igual, si no el tablero cambia de
    forma segun los datos."""
    d = cli.get("/api/preclientes").get_json()
    assert len(d["etapas"]) == len(ETAPAS_PRECLIENTE)


def test_el_tablero_no_incluye_leads_de_la_cola(app, cli):
    db = app.config["_DB"]
    _lead(db, "Sin contactar")           # queda en sin_contactar
    _lead(db, "Interesado", "interesado")
    assert cli.get("/api/preclientes").get_json()["total"] == 0


def test_el_tablero_no_incluye_clientes_cerrados(app, cli):
    db = app.config["_DB"]
    _lead(db, "Ya cliente", "cerrado")
    assert cli.get("/api/preclientes").get_json()["total"] == 0


def test_el_tablero_muestra_quien_dio_la_ultima_demo(app, cli):
    db = app.config["_DB"]
    lid = _lead(db, "Ferretería", "demo_2")
    cli.post("/api/demos-realizadas", json={"client_id": lid, "actualizacion": "primera"})

    d = cli.get("/api/preclientes").get_json()
    lead = [x for e in d["etapas"] for x in e["leads"]][0]
    assert lead["demos_dadas"] == 1
    assert lead["ultima_demo_por"] == "jefe"


# ── clientes activos ─────────────────────────────────────────────────────────

def test_lista_solo_los_clientes_cerrados(app, cli):
    db = app.config["_DB"]
    _lead(db, "Cliente A", "cerrado")
    _lead(db, "Cliente B", "en_desarrollo")
    _lead(db, "Todavia vendiendo", "demo_1")

    d = cli.get("/api/clientes-activos").get_json()
    assert d["total"] == 2
    assert {c["name"] for c in d["clientes"]} == {"Cliente A", "Cliente B"}


def test_asignar_los_tres_responsables(app, cli):
    db = app.config["_DB"]
    lid = _lead(db, "Cliente", "cerrado")
    otro = _usuario(db, "otro@test.com")

    r = cli.put(f"/api/clientes-activos/{lid}/responsables", json={
        "encargado_id": cli._uid,
        "mantenimiento_id": otro,
        "cobros_id": cli._uid,
    })
    assert r.status_code == 200

    c = cli.get("/api/clientes-activos").get_json()["clientes"][0]
    assert c["encargado_nombre"] == "jefe"
    assert c["mantenimiento_nombre"] == "otro"
    assert c["cobros_nombre"] == "jefe"


def test_un_puesto_se_puede_dejar_vacante(app, cli):
    db = app.config["_DB"]
    lid = _lead(db, "Cliente", "cerrado")
    cli.put(f"/api/clientes-activos/{lid}/responsables", json={"encargado_id": cli._uid})
    cli.put(f"/api/clientes-activos/{lid}/responsables", json={"encargado_id": None})

    assert get_business(db, lid)["encargado_id"] is None


def test_no_se_puede_asignar_un_usuario_inexistente(app, cli):
    db = app.config["_DB"]
    lid = _lead(db, "Cliente", "cerrado")
    r = cli.put(f"/api/clientes-activos/{lid}/responsables", json={"encargado_id": 99999})
    assert r.status_code == 400
    assert get_business(db, lid)["encargado_id"] is None


def test_responsables_de_un_cliente_inexistente_da_404(app, cli):
    r = cli.put("/api/clientes-activos/99999/responsables", json={"encargado_id": cli._uid})
    assert r.status_code == 404


# ── registro de demos ────────────────────────────────────────────────────────

def test_registrar_una_demo(app, cli):
    db = app.config["_DB"]
    lid = _lead(db, "Ferretería", "demo_1")
    r = cli.post("/api/demos-realizadas", json={
        "client_id": lid, "actualizacion": "Le gustó, pidió ver la tienda online",
    })
    assert r.status_code == 201

    demos = listar_demos_realizadas(db, lid)
    assert len(demos) == 1
    assert demos[0]["numero"] == 1
    assert demos[0]["realizada_por_nombre"] == "jefe"
    assert "tienda online" in demos[0]["actualizacion"]


def test_las_demos_se_numeran_solas(app, cli):
    """El equipo no tiene que llevar la cuenta de si va por la 2 o la 3."""
    db = app.config["_DB"]
    lid = _lead(db, "Ferretería", "demo_1")
    for _ in range(3):
        cli.post("/api/demos-realizadas", json={"client_id": lid, "actualizacion": "x"})
    assert [d["numero"] for d in listar_demos_realizadas(db, lid)] == [3, 2, 1]


def test_la_numeracion_es_por_cliente(app, cli):
    db = app.config["_DB"]
    a = _lead(db, "A", "demo_1")
    b = _lead(db, "B", "demo_1")
    cli.post("/api/demos-realizadas", json={"client_id": a, "actualizacion": "x"})
    cli.post("/api/demos-realizadas", json={"client_id": b, "actualizacion": "x"})
    assert listar_demos_realizadas(db, b)[0]["numero"] == 1


def test_se_puede_registrar_a_nombre_de_otro(app, cli):
    """Quien carga la demo no siempre es quien la dio."""
    db = app.config["_DB"]
    lid = _lead(db, "Ferretería", "demo_1")
    otro = _usuario(db, "thomy@test.com")
    cli.post("/api/demos-realizadas", json={
        "client_id": lid, "realizada_por": otro, "actualizacion": "la dio Thomy",
    })
    assert listar_demos_realizadas(db, lid)[0]["realizada_por_nombre"] == "thomy"


def test_el_historial_trae_el_nombre_del_cliente(app, cli):
    """La vista global de demos las muestra de todos los clientes juntas."""
    db = app.config["_DB"]
    lid = _lead(db, "Ferretería El Sol", "demo_1")
    cli.post("/api/demos-realizadas", json={"client_id": lid, "actualizacion": "x"})
    d = cli.get("/api/demos-realizadas").get_json()["demos"][0]
    assert d["cliente_nombre"] == "Ferretería El Sol"


def test_se_puede_editar_la_actualizacion(app, cli):
    db = app.config["_DB"]
    lid = _lead(db, "Ferretería", "demo_1")
    did = cli.post("/api/demos-realizadas",
                   json={"client_id": lid, "actualizacion": "primera nota"}).get_json()["id"]
    cli.put(f"/api/demos-realizadas/{did}", json={"actualizacion": "corregida"})
    assert listar_demos_realizadas(db, lid)[0]["actualizacion"] == "corregida"


def test_se_puede_borrar_una_demo(app, cli):
    db = app.config["_DB"]
    lid = _lead(db, "Ferretería", "demo_1")
    did = cli.post("/api/demos-realizadas",
                   json={"client_id": lid, "actualizacion": "x"}).get_json()["id"]
    assert cli.delete(f"/api/demos-realizadas/{did}").status_code == 200
    assert listar_demos_realizadas(db, lid) == []


def test_registrar_para_un_lead_inexistente_da_404(app, cli):
    r = cli.post("/api/demos-realizadas", json={"client_id": 99999, "actualizacion": "x"})
    assert r.status_code == 404


def test_sin_client_id_da_400(app, cli):
    assert cli.post("/api/demos-realizadas", json={"actualizacion": "x"}).status_code == 400


def test_borrar_el_lead_se_lleva_sus_demos(app, cli):
    """La cascada tiene que funcionar: si no, quedan demos huerfanas con notas
    comerciales de un cliente que ya no existe."""
    db = app.config["_DB"]
    lid = _lead(db, "Ferretería", "demo_1")
    cli.post("/api/demos-realizadas", json={"client_id": lid, "actualizacion": "x"})
    from database import delete_business
    delete_business(db, lid)
    assert listar_demos_realizadas(db, lid) == []


# ── migracion de los estados viejos ──────────────────────────────────────────

def test_la_migracion_traduce_los_estados_del_pipeline(tmp_path):
    db = str(tmp_path / "m.db")
    init_db(db)
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO businesses (name, phone, crm_status) VALUES (?,?,?)",
        [("A", "+5981", "reunion_agendada"), ("B", "+5982", "reunion_hecha"),
         ("C", "+5983", "negociacion"), ("D", "+5984", "cliente_cerrado")],
    )
    conn.commit()
    conn.close()

    init_db(db)  # segunda corrida: dispara la migracion

    conn = sqlite3.connect(db)
    estados = dict(conn.execute("SELECT name, crm_status FROM businesses").fetchall())
    conn.close()
    assert estados == {"A": "demo_agendada", "B": "demo_1",
                       "C": "follow_up_1", "D": "cerrado"}


def test_la_migracion_no_toca_los_estados_de_la_cola(tmp_path):
    """sin_contactar, interesado y no_interesa son de otra etapa del embudo."""
    db = str(tmp_path / "m2.db")
    init_db(db)
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO businesses (name, phone, crm_status) VALUES (?,?,?)",
        [("A", "+5981", "sin_contactar"), ("B", "+5982", "interesado"),
         ("C", "+5983", "no_interesa"), ("D", "+5984", "llamar_despues")],
    )
    conn.commit()
    conn.close()

    init_db(db)

    conn = sqlite3.connect(db)
    estados = dict(conn.execute("SELECT name, crm_status FROM businesses").fetchall())
    conn.close()
    assert estados == {"A": "sin_contactar", "B": "interesado",
                       "C": "no_interesa", "D": "llamar_despues"}


def test_la_migracion_es_idempotente(tmp_path):
    db = str(tmp_path / "m3.db")
    init_db(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO businesses (name, phone, crm_status) VALUES (?,?,?)",
                 ("A", "+5981", "reunion_agendada"))
    conn.commit()
    conn.close()

    for _ in range(3):
        init_db(db)

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT crm_status FROM businesses").fetchone()[0] == "demo_agendada"
    conn.close()


def test_las_etapas_nuevas_son_estados_validos(app, cli):
    """Mover un lead a una etapa nueva no puede dar 'Estado invalido'."""
    db = app.config["_DB"]
    lid = _lead(db, "Ferretería")
    for etapa in ETAPAS_PRECLIENTE:
        r = cli.post(f"/api/leads/{lid}/crm-status", json={"crm_status": etapa})
        assert r.status_code == 200, f"{etapa}: {r.get_json()}"
        assert get_business(db, lid)["crm_status"] == etapa
