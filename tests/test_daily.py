"""Daily Programador: el día de cada programador.

Pedido de Juan (15/9): una sección "Daily Programador" donde se puedan poner
actividades y recordatorios de lo que hay que hacer cada día, para no
olvidarse. Después lo ajustó: en el menú, debajo del ítem, una opción por
persona (Juan y Gonzalo), y cualquiera con el panel entra a cualquiera. Las
personas salen de Recursos Humanos: `equipo_personas.programador`.

16/9: cada persona con su ícono en el menú, y el día pintado como Seguimiento
de leads (contadores, grupos y tarjetas), con hora y nota opcionales.

El reloj está fijo en el martes 15/9/2026, 10:00 de Montevideo (13:00 UTC).
"""

import json
import re
import shutil
import sqlite3
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import dashboard
import database
import routes.daily as rutas_daily
from database import create_user, get_actividad_daily, init_db, listar_personas_equipo
from services.daily import cuando, hoy_montevideo, le_toca

HTML = dashboard.DASHBOARD_HTML
RAIZ = Path(__file__).resolve().parents[1]
SRC = (RAIZ / "dashboard.py").read_text(encoding="utf-8")

sin_node = pytest.mark.skipif(shutil.which("node") is None,
                              reason="node no esta instalado")

AHORA = datetime(2026, 9, 15, 13, 0, tzinfo=timezone.utc)  # martes 10:00 en Montevideo
LUNES, MARTES, MIERCOLES = "2026-09-14", "2026-09-15", "2026-09-16"
JUEVES, SABADO, DOMINGO, PROX_LUNES = "2026-09-17", "2026-09-19", "2026-09-20", "2026-09-21"


def _entre(texto: str, desde: str, hasta: str) -> str:
    i = texto.index(desde)
    return texto[i:texto.index(hasta, i + len(desde))]


PANEL = _entre(SRC, "<!-- ======= DAILY PROGRAMADOR PANEL ======= -->",
               "<!-- ======= RECURSOS HUMANOS PANELES ======= -->")
MODALES = _entre(SRC, "<!-- ======= DAILY MODALES ======= -->", "<!-- ======= FIN DAILY MODALES ======= -->")
JS = _entre(SRC, "// ========== Daily Programador ==========", "// ========== Equipo ==========")
# Termina donde empieza Seguimiento de leads, que va pegado abajo.
CSS = _entre(SRC, "/* ── Daily Programador", "/* ── Seguimiento de leads")
FUENTES = {"panel": PANEL, "modales": MODALES, "js": JS, "css": CSS}


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(rutas_daily, "_ahora", lambda: AHORA)
    db = str(tmp_path / "daily.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


def _rol(db, nombre, paneles):
    conn = sqlite3.connect(db)
    try:
        rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)",
                           (nombre, json.dumps(paneles))).lastrowid
        conn.commit()
        return rid
    finally:
        conn.close()


def _usuario(db, email, role_id=None):
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    if role_id is not None:
        _set(db, "UPDATE users SET role_id=? WHERE id=?", (role_id, uid))
    return uid


def _cli(app, uid, nombre="test"):
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = nombre
    return c


@pytest.fixture
def cli(app):
    return _cli(app, _usuario(app.config["_DB"], "jefe@scalerics.com"))


def _set(db, sql, params=()):
    conn = sqlite3.connect(db)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def _db(c):
    return c.application.config["_DB"]


def _pid(db, nombre):
    return next(p["id"] for p in listar_personas_equipo(db) if p["nombre"] == nombre)


def _juan(c):
    return _pid(_db(c), "Juan Tomasetti")


def _gonzalo(c):
    return _pid(_db(c), "Gonzalo Siuciak")


def _dia(c, persona_id, fecha=None):
    url = f"/api/daily?persona_id={persona_id}" + (f"&fecha={fecha}" if fecha else "")
    r = c.get(url)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def _act(c, persona_id, texto, fecha=MARTES, **extra):
    r = c.post("/api/daily/actividades",
               json={"persona_id": persona_id, "texto": texto, "fecha": fecha, **extra})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _rec(c, persona_id, texto, frecuencia="diario", dias=None, **extra):
    datos = {"persona_id": persona_id, "texto": texto, "frecuencia": frecuencia, **extra}
    if dias is not None:
        datos["dias"] = dias
    r = c.post("/api/daily/recordatorios", json=datos)
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _textos(lista):
    return [x["texto"] for x in lista]


def _marcas(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM daily_marcas").fetchone()[0]
    finally:
        conn.close()


def _columnas(db, tabla):
    conn = sqlite3.connect(db)
    try:
        return {fila[1] for fila in conn.execute(f"PRAGMA table_info({tabla})")}
    finally:
        conn.close()


# ── fecha de Montevideo ──────────────────────────────────────────────────────

def test_hoy_es_el_dia_de_montevideo_y_no_el_del_servidor():
    assert hoy_montevideo(datetime(2026, 9, 16, 2, 59, tzinfo=timezone.utc)) == date(2026, 9, 15)
    assert hoy_montevideo(datetime(2026, 9, 16, 3, 0, tzinfo=timezone.utc)) == date(2026, 9, 16)
    assert hoy_montevideo(datetime(2026, 9, 16, 1, 0)) == date(2026, 9, 15), "sin zona se toma como UTC"
    assert isinstance(hoy_montevideo(), date)


def test_el_reloj_del_servidor_viene_con_zona():
    """Sin la fixture `app` no hay monkeypatch: es el reloj de verdad."""
    assert rutas_daily._ahora().tzinfo is not None


def test_a_la_una_y_media_utc_todavia_es_ayer_en_montevideo(cli, monkeypatch):
    monkeypatch.setattr(rutas_daily, "_ahora", lambda: datetime(2026, 9, 16, 1, 30, tzinfo=timezone.utc))
    juan = _juan(cli)
    dia = _dia(cli, juan)
    assert dia["fecha"] == MARTES and dia["hoy"] == MARTES and dia["es_hoy"] is True
    assert dia["ayer"] == LUNES
    r = cli.post("/api/daily/actividades", json={"persona_id": juan, "texto": "Sin fecha"})
    assert get_actividad_daily(_db(cli), r.get_json()["id"])["fecha"] == MARTES
    assert cli.get("/api/daily/personas").get_json()["hoy"] == MARTES
    assert _dia(cli, juan, MIERCOLES)["es_hoy"] is False


def test_una_fecha_mal_escrita_da_400(cli):
    assert cli.get(f"/api/daily?persona_id={_juan(cli)}&fecha=15-09-2026").status_code == 400


# ── personas ─────────────────────────────────────────────────────────────────

def test_las_personas_son_los_programadores_de_recursos_humanos(cli):
    r = cli.get("/api/daily/personas")
    assert r.status_code == 200
    personas = r.get_json()["personas"]
    assert [(p["nombre"], p["primer_nombre"]) for p in personas] == [
        ("Juan Tomasetti", "Juan"), ("Gonzalo Siuciak", "Gonzalo")]
    db = _db(cli)
    _set(db, "UPDATE equipo_personas SET programador = 1 WHERE nombre = ?", ("Matías Domínguez",))
    nombres = [p["primer_nombre"] for p in cli.get("/api/daily/personas").get_json()["personas"]]
    assert nombres == ["Matías", "Juan", "Gonzalo"], "sumar a alguien es prender la marca, sin tocar código"
    _set(db, "UPDATE equipo_personas SET activo = 0 WHERE nombre = ?", ("Matías Domínguez",))
    nombres = [p["primer_nombre"] for p in cli.get("/api/daily/personas").get_json()["personas"]]
    assert nombres == ["Juan", "Gonzalo"]


def test_la_precarga_marca_a_juan_y_gonzalo_y_no_pisa_un_cambio_a_mano(tmp_path):
    db = str(tmp_path / "p.db")
    init_db(db)
    assert {p["nombre"] for p in listar_personas_equipo(db) if p["programador"]} == {
        "Juan Tomasetti", "Gonzalo Siuciak"}
    _set(db, "UPDATE equipo_personas SET programador = 0 WHERE nombre = ?", ("Juan Tomasetti",))
    init_db(db)
    assert {p["nombre"] for p in listar_personas_equipo(db) if p["programador"]} == {"Gonzalo Siuciak"}


def test_una_persona_sin_daily_da_404(cli):
    matias = _pid(_db(cli), "Matías Domínguez")
    assert cli.get(f"/api/daily?persona_id={matias}").status_code == 404
    assert cli.get("/api/daily?persona_id=99999").status_code == 404
    assert cli.get("/api/daily?persona_id=x").status_code == 400
    assert cli.get("/api/daily").status_code == 400
    assert cli.post("/api/daily/actividades", json={"persona_id": matias, "texto": "x"}).status_code == 404
    assert cli.post("/api/daily/recordatorios", json={"persona_id": matias, "texto": "x"}).status_code == 404


# ── actividades ──────────────────────────────────────────────────────────────

def test_crud_de_actividades(cli):
    juan = _juan(cli)
    aid = _act(cli, juan, "  Terminar   la landing  ")
    assert _dia(cli, juan, MARTES)["actividades"] == [{
        "id": aid, "texto": "Terminar la landing", "hecha": False, "fecha": MARTES,
        "pasada_de": None, "hora": None, "nota": None, "creada_por": "test"}]
    assert cli.patch(f"/api/daily/actividades/{aid}", json={"hecha": True}).status_code == 200
    assert _dia(cli, juan, MARTES)["actividades"][0]["hecha"] is True
    assert cli.patch(f"/api/daily/actividades/{aid}", json={"hecha": False}).status_code == 200
    assert _dia(cli, juan, MARTES)["actividades"][0]["hecha"] is False
    assert cli.patch(f"/api/daily/actividades/{aid}", json={"texto": "Terminar la landing de Tito"}).status_code == 200
    assert _textos(_dia(cli, juan, MARTES)["actividades"]) == ["Terminar la landing de Tito"]
    assert _dia(cli, juan, MIERCOLES)["actividades"] == [], "cada actividad es de su día"
    assert cli.delete(f"/api/daily/actividades/{aid}").status_code == 200
    assert _dia(cli, juan, MARTES)["actividades"] == []
    assert cli.delete(f"/api/daily/actividades/{aid}").status_code == 404
    assert cli.patch(f"/api/daily/actividades/{aid}", json={"hecha": True}).status_code == 404


@pytest.mark.parametrize("datos,parte", [
    ({"texto": ""}, "vacío"),
    ({"texto": "   "}, "vacío"),
    ({"texto": 5}, "vacío"),
    ({"texto": "x" * 201}, "200"),
    ({"texto": "ok", "fecha": "15/09/2026"}, "AAAA-MM-DD"),
    ({"texto": "ok", "hora": "25:00"}, "HH:MM"),
    ({"texto": "ok", "hora": "9:30"}, "HH:MM"),
    ({"texto": "ok", "hora": 930}, "HH:MM"),
    ({"texto": "ok", "nota": "x" * 301}, "300"),
    ({"texto": "ok", "nota": 5}, "texto"),
])
def test_la_actividad_se_valida_en_el_servidor(cli, datos, parte):
    r = cli.post("/api/daily/actividades", json={"persona_id": _juan(cli), **datos})
    assert r.status_code == 400 and parte in r.get_json()["error"], r.get_json()


def test_editar_o_pasar_una_actividad_se_valida(cli):
    aid = _act(cli, _juan(cli), "Algo")
    for cuerpo in ({}, {"hecha": "si"}, {"hecha": 1}, {"texto": ""}, {"hora": "24:00"},
                   {"nota": ["x"]}, {"fecha": "mañana"}):
        assert cli.patch(f"/api/daily/actividades/{aid}", json=cuerpo).status_code == 400, cuerpo
    assert cli.post(f"/api/daily/actividades/{aid}/pasar", json={"fecha": "mañana"}).status_code == 400


def test_hora_y_nota_de_una_actividad_y_el_orden_por_hora(cli):
    juan = _juan(cli)
    sin_hora = _act(cli, juan, "Sin hora")
    tarde = _act(cli, juan, "A la tarde", hora="15:00")
    temprano = _act(cli, juan, "Temprano", hora="09:30", nota="  Pedir acceso al hosting  ")
    dia = _dia(cli, juan, MARTES)
    assert _textos(dia["actividades"]) == ["Temprano", "A la tarde", "Sin hora"], "con hora primero, en orden"
    assert dia["actividades"][0]["hora"] == "09:30"
    assert dia["actividades"][0]["nota"] == "Pedir acceso al hosting"

    # Editar: sacar la hora, cambiar la nota y moverla de día.
    assert cli.patch(f"/api/daily/actividades/{tarde}", json={"hora": "", "nota": "Después del almuerzo"}).status_code == 200
    fila = get_actividad_daily(_db(cli), tarde)
    assert fila["hora"] is None and fila["nota"] == "Después del almuerzo"
    assert cli.patch(f"/api/daily/actividades/{temprano}", json={"nota": ""}).status_code == 200
    assert get_actividad_daily(_db(cli), temprano)["nota"] is None, "una nota vacía es sin nota"
    assert cli.patch(f"/api/daily/actividades/{sin_hora}", json={"fecha": JUEVES}).status_code == 200
    assert _textos(_dia(cli, juan, JUEVES)["actividades"]) == ["Sin hora"]


def test_pasar_a_maniana_y_hecho(cli):
    """Los dos botones de cada tarjeta del día. "Pasar a mañana" es el mismo
    pasar de siempre con el día siguiente; "Hecho" es marcarla."""
    juan = _juan(cli)
    aid = _act(cli, juan, "Revisar PR", hora="11:00")
    r = cli.post(f"/api/daily/actividades/{aid}/pasar", json={"fecha": MIERCOLES})
    assert r.status_code == 200 and r.get_json()["fecha"] == MIERCOLES
    assert _dia(cli, juan, MARTES)["actividades"] == []
    [miercoles] = _dia(cli, juan, MIERCOLES)["actividades"]
    assert miercoles["pasada_de"] == MARTES and miercoles["hora"] == "11:00" and miercoles["hecha"] is False

    assert cli.patch(f"/api/daily/actividades/{aid}", json={"hecha": True}).status_code == 200
    assert _dia(cli, juan, MIERCOLES)["actividades"][0]["hecha"] is True
    assert cli.patch(f"/api/daily/actividades/{aid}", json={"hecha": False}).status_code == 200, "Deshacer"
    assert _dia(cli, juan, MIERCOLES)["actividades"][0]["hecha"] is False


def test_lo_de_juan_no_aparece_en_el_dia_de_gonzalo(cli):
    juan, gonzalo = _juan(cli), _gonzalo(cli)
    _act(cli, juan, "Lo de Juan")
    _rec(cli, juan, "Recordatorio de Juan")
    _act(cli, gonzalo, "Lo de Gonzalo")
    dj, dg = _dia(cli, juan, MARTES), _dia(cli, gonzalo, MARTES)
    assert _textos(dj["actividades"]) == ["Lo de Juan"]
    assert _textos(dg["actividades"]) == ["Lo de Gonzalo"]
    assert _textos(dj["recordatorios"]) == ["Recordatorio de Juan"]
    assert dg["recordatorios"] == [] and dg["recordatorios_todos"] == []
    assert dj["persona"]["primer_nombre"] == "Juan" and dg["persona"]["primer_nombre"] == "Gonzalo"


# ── migración ────────────────────────────────────────────────────────────────

def test_la_migracion_agrega_hora_y_nota_sin_romper_lo_cargado(tmp_path):
    """Una base con las tablas del Daily como quedaron publicadas (v226), sin
    hora ni nota y con datos: al arrancar se suman las columnas y lo cargado
    sigue igual."""
    db = str(tmp_path / "vieja.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE daily_actividades (
        id INTEGER PRIMARY KEY AUTOINCREMENT, persona_id INTEGER NOT NULL, fecha TEXT NOT NULL,
        texto TEXT NOT NULL, hecha INTEGER NOT NULL DEFAULT 0, pasada_de TEXT,
        created_by_id INTEGER, created_by_name TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE TABLE daily_recordatorios (
        id INTEGER PRIMARY KEY AUTOINCREMENT, persona_id INTEGER NOT NULL, texto TEXT NOT NULL,
        frecuencia TEXT NOT NULL DEFAULT 'diario', dias TEXT NOT NULL DEFAULT '',
        activo INTEGER NOT NULL DEFAULT 1, desde TEXT NOT NULL, created_by_id INTEGER,
        created_by_name TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("INSERT INTO daily_actividades (persona_id, fecha, texto, hecha, pasada_de) "
                 "VALUES (6, ?, 'Cargada antes', 1, ?)", (MARTES, LUNES))
    conn.execute("INSERT INTO daily_recordatorios (persona_id, texto, frecuencia, dias, desde) "
                 "VALUES (6, 'Revisar mails', 'dias', '1,3', ?)", (LUNES,))
    conn.commit()
    conn.close()
    assert "hora" not in _columnas(db, "daily_actividades")

    init_db(db)
    init_db(db)  # dos arranques: la migración no falla la segunda vez
    for tabla in ("daily_actividades", "daily_recordatorios"):
        assert {"hora", "nota"} <= _columnas(db, tabla), tabla

    [a] = database.listar_actividades_daily(db, 6, MARTES)
    assert (a["texto"], a["hecha"], a["pasada_de"], a["hora"], a["nota"]) == (
        "Cargada antes", 1, LUNES, None, None)
    [r] = database.listar_recordatorios_daily(db, 6)
    assert (r["texto"], r["dias"], r["desde"], r["hora"], r["nota"]) == (
        "Revisar mails", "1,3", LUNES, None, None)
    assert cuando(r) == "Mar y Jue"


# ── permisos ─────────────────────────────────────────────────────────────────

def test_sin_el_panel_daily_da_403(app):
    db = app.config["_DB"]
    c = _cli(app, _usuario(db, "tareas@scalerics.com", _rol(db, "Solo tareas", ["tasks"])))
    juan = _pid(db, "Juan Tomasetti")
    assert c.get("/api/daily/personas").status_code == 403
    assert c.get(f"/api/daily?persona_id={juan}").status_code == 403
    assert c.post("/api/daily/actividades", json={"persona_id": juan, "texto": "x"}).status_code == 403


def test_con_el_panel_cualquiera_entra_al_dia_de_cualquiera_y_queda_quien_lo_cargo(app):
    db = app.config["_DB"]
    uid = _usuario(db, "gonza@scalerics.com", _rol(db, "Solo daily", ["daily"]))
    c = _cli(app, uid, "Gonzalo")
    for persona in (_pid(db, "Juan Tomasetti"), _pid(db, "Gonzalo Siuciak")):
        aid = _act(c, persona, "Cargado por Gonzalo")
        fila = get_actividad_daily(db, aid)
        assert fila["created_by_id"] == uid and fila["created_by_name"] == "Gonzalo"
        assert c.patch(f"/api/daily/actividades/{aid}", json={"hecha": True}).status_code == 200
        assert _dia(c, persona, MARTES)["actividades"][0]["hecha"] is True
    rid = _rec(c, _pid(db, "Juan Tomasetti"), "Subir backup")
    assert c.put(f"/api/daily/recordatorios/{rid}/marca", json={"fecha": MARTES, "hecha": True}).status_code == 200
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT created_by_id FROM daily_recordatorios WHERE id=?", (rid,)).fetchone()[0] == uid
        assert conn.execute("SELECT created_by_id FROM daily_marcas WHERE recordatorio_id=?", (rid,)).fetchone()[0] == uid
    finally:
        conn.close()


def test_sin_sesion_no_entra(app):
    assert app.test_client().get("/api/daily/personas").status_code in (302, 401, 403)


def test_los_roles_con_tareas_reciben_el_daily(tmp_path):
    db = str(tmp_path / "r.db")
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        acceso = {n: json.loads(p) for n, p in conn.execute("SELECT name, panel_access FROM roles")}
        assert "daily" in acceso["Admin"], "Admin tiene Tareas"
        assert "daily" not in acceso["Caller"] and "daily" not in acceso["Ventas"]
        conn.execute("UPDATE roles SET panel_access=? WHERE name='Ventas'", (json.dumps(["meta", "tasks"]),))
        conn.commit()
        assert database._grant_panel_to_existing_roles(conn, "daily", solo_si_tiene="tasks") == 1
        ventas = json.loads(conn.execute("SELECT panel_access FROM roles WHERE name='Ventas'").fetchone()[0])
        assert ventas == ["meta", "tasks", "daily"]
        assert database._grant_panel_to_existing_roles(conn, "daily", solo_si_tiene="tasks") == 0
    finally:
        conn.close()


# ── recordatorios que se repiten ─────────────────────────────────────────────

def test_los_recordatorios_aparecen_en_los_dias_que_les_tocan(cli):
    juan = _juan(cli)
    _rec(cli, juan, "Revisar mails", "diario")
    _rec(cli, juan, "Subir backup", "habiles")
    _rec(cli, juan, "Reunión de equipo", "dias", [2, 0, 2])  # lunes y miércoles
    esperado = {
        LUNES: [],  # se crearon el martes: no aparecen antes
        MARTES: ["Revisar mails", "Subir backup"],
        MIERCOLES: ["Revisar mails", "Subir backup", "Reunión de equipo"],
        SABADO: ["Revisar mails"],
        DOMINGO: ["Revisar mails"],
        PROX_LUNES: ["Revisar mails", "Subir backup", "Reunión de equipo"],
    }
    for fecha, textos in esperado.items():
        assert _textos(_dia(cli, juan, fecha)["recordatorios"]) == textos, fecha
    todos = _dia(cli, juan, LUNES)["recordatorios_todos"]
    assert [r["cuando"] for r in todos] == ["Todos los días", "Lun a Vie", "Lun y Mié"]
    assert todos[2]["dias"] == [0, 2] and todos[0]["dias"] == []


def test_la_etiqueta_de_frecuencia():
    base = {"activo": 1, "desde": LUNES}
    assert cuando(dict(base, frecuencia="diario", dias="")) == "Todos los días"
    assert cuando(dict(base, frecuencia="habiles", dias="")) == "Lun a Vie"
    assert cuando(dict(base, frecuencia="dias", dias="1,3")) == "Mar y Jue"
    assert cuando(dict(base, frecuencia="dias", dias="0,2,4")) == "Lun, Mié y Vie"
    assert cuando(dict(base, frecuencia="dias", dias="6")) == "Dom"


def test_le_toca_sin_base():
    rec = {"activo": 1, "desde": "2026-09-01", "frecuencia": "dias", "dias": "4,9,x"}
    assert le_toca(rec, date(2026, 9, 18)), "viernes"
    assert not le_toca(rec, date(2026, 9, 17)), "jueves"
    assert not le_toca(dict(rec, activo=0), date(2026, 9, 18))
    assert not le_toca(dict(rec, frecuencia="diario"), date(2026, 8, 31)), "antes de desde no"


@pytest.mark.parametrize("datos,parte", [
    ({"texto": ""}, "vacío"),
    ({"texto": "x", "frecuencia": "semanal"}, "frecuencia"),
    ({"texto": "x", "frecuencia": "dias", "dias": []}, "al menos un día"),
    ({"texto": "x", "frecuencia": "dias"}, "0 (lunes)"),
    ({"texto": "x", "frecuencia": "dias", "dias": [7]}, "0 (lunes)"),
    ({"texto": "x", "frecuencia": "dias", "dias": [True]}, "0 (lunes)"),
    ({"texto": "x", "frecuencia": "dias", "dias": "lunes"}, "0 (lunes)"),
    ({"texto": "x", "activo": "no"}, "activo"),
    ({"texto": "x", "hora": "7 de la mañana"}, "HH:MM"),
    ({"texto": "x", "nota": "x" * 301}, "300"),
])
def test_el_recordatorio_se_valida_en_el_servidor(cli, datos, parte):
    r = cli.post("/api/daily/recordatorios", json={"persona_id": _juan(cli), **datos})
    assert r.status_code == 400 and parte in r.get_json()["error"], r.get_json()


def test_hora_y_nota_de_un_recordatorio_y_el_orden_por_hora(cli):
    juan = _juan(cli)
    sin_hora = _rec(cli, juan, "Sin hora")
    rid = _rec(cli, juan, "Revisar mails", hora="10:00", nota="Los de clientes primero")
    _rec(cli, juan, "Subir backup", hora="08:15")
    assert _textos(_dia(cli, juan, MARTES)["recordatorios"]) == ["Subir backup", "Revisar mails", "Sin hora"]
    todos = {r["id"]: r for r in _dia(cli, juan, MARTES)["recordatorios_todos"]}
    assert todos[rid]["hora"] == "10:00" and todos[rid]["nota"] == "Los de clientes primero"
    assert todos[sin_hora]["hora"] is None

    # Mandar solo la hora no pisa la nota (ni al revés).
    assert cli.put(f"/api/daily/recordatorios/{rid}", json={"hora": "07:00"}).status_code == 200
    r = next(x for x in _dia(cli, juan, MARTES)["recordatorios_todos"] if x["id"] == rid)
    assert r["hora"] == "07:00" and r["nota"] == "Los de clientes primero"
    assert cli.put(f"/api/daily/recordatorios/{rid}", json={"hora": "", "nota": ""}).status_code == 200
    r = next(x for x in _dia(cli, juan, MARTES)["recordatorios_todos"] if x["id"] == rid)
    assert r["hora"] is None and r["nota"] is None
    assert cli.put(f"/api/daily/recordatorios/{rid}", json={"hora": "99:99"}).status_code == 400


def test_pausar_editar_y_borrar_un_recordatorio(cli):
    juan = _juan(cli)
    rid = _rec(cli, juan, "Subir backup", "habiles")
    assert cli.put(f"/api/daily/recordatorios/{rid}", json={"activo": False}).status_code == 200
    assert _dia(cli, juan, MIERCOLES)["recordatorios"] == []
    assert _dia(cli, juan, MIERCOLES)["recordatorios_todos"][0]["activo"] is False
    assert cli.put(f"/api/daily/recordatorios/{rid}", json={"activo": True}).status_code == 200
    assert _textos(_dia(cli, juan, MIERCOLES)["recordatorios"]) == ["Subir backup"]

    r = cli.put(f"/api/daily/recordatorios/{rid}",
                json={"texto": "Subir backup de la base", "frecuencia": "dias", "dias": [5]})
    assert r.status_code == 200
    assert _dia(cli, juan, MIERCOLES)["recordatorios"] == []
    assert _textos(_dia(cli, juan, SABADO)["recordatorios"]) == ["Subir backup de la base"]
    assert cli.put(f"/api/daily/recordatorios/{rid}", json={"dias": []}).status_code == 400
    assert cli.put(f"/api/daily/recordatorios/{rid}", json={"frecuencia": "diario"}).status_code == 200
    assert _dia(cli, juan, MIERCOLES)["recordatorios_todos"][0]["dias"] == []

    cli.put(f"/api/daily/recordatorios/{rid}/marca", json={"fecha": SABADO, "hecha": True})
    assert _marcas(_db(cli)) == 1
    assert cli.delete(f"/api/daily/recordatorios/{rid}").status_code == 200
    sabado = _dia(cli, juan, SABADO)
    assert sabado["recordatorios"] == [] and sabado["recordatorios_todos"] == []
    assert _marcas(_db(cli)) == 0, "se lleva sus marcas"
    assert cli.delete(f"/api/daily/recordatorios/{rid}").status_code == 404
    assert cli.put(f"/api/daily/recordatorios/{rid}", json={"activo": True}).status_code == 404


def test_marcar_un_recordatorio_hecho_no_toca_los_otros_dias(cli):
    juan = _juan(cli)
    rid = _rec(cli, juan, "Revisar mails")
    assert cli.put(f"/api/daily/recordatorios/{rid}/marca", json={"fecha": MARTES, "hecha": True}).status_code == 200
    assert _dia(cli, juan, MARTES)["recordatorios"][0]["hecha"] is True
    assert _dia(cli, juan, MIERCOLES)["recordatorios"][0]["hecha"] is False
    cli.put(f"/api/daily/recordatorios/{rid}/marca", json={"fecha": MARTES, "hecha": True})
    assert _marcas(_db(cli)) == 1, "marcar dos veces no duplica"
    cli.put(f"/api/daily/recordatorios/{rid}/marca", json={"fecha": MIERCOLES, "hecha": True})
    cli.put(f"/api/daily/recordatorios/{rid}/marca", json={"fecha": MARTES, "hecha": False})
    assert _dia(cli, juan, MARTES)["recordatorios"][0]["hecha"] is False
    assert _dia(cli, juan, MIERCOLES)["recordatorios"][0]["hecha"] is True, "desmarcar un día no toca otro"
    assert cli.put(f"/api/daily/recordatorios/{rid}/marca", json={"fecha": "hoy", "hecha": True}).status_code == 400
    assert cli.put(f"/api/daily/recordatorios/{rid}/marca", json={"fecha": MARTES, "hecha": 1}).status_code == 400
    assert cli.put("/api/daily/recordatorios/9999/marca", json={"fecha": MARTES, "hecha": True}).status_code == 404


def test_pausar_no_borra_lo_que_ya_se_marco(cli):
    juan = _juan(cli)
    rid = _rec(cli, juan, "Revisar mails")
    cli.put(f"/api/daily/recordatorios/{rid}/marca", json={"fecha": MARTES, "hecha": True})
    cli.put(f"/api/daily/recordatorios/{rid}", json={"activo": False})
    martes = _dia(cli, juan, MARTES)["recordatorios"]
    assert _textos(martes) == ["Revisar mails"] and martes[0]["hecha"] is True
    assert _dia(cli, juan, MIERCOLES)["recordatorios"] == []


# ── pendiente de ayer ────────────────────────────────────────────────────────

def test_lo_que_quedo_sin_hacer_ayer_se_ve_y_se_pasa_a_hoy(cli):
    juan = _juan(cli)
    sin_hacer = _act(cli, juan, "Terminar landing", LUNES)
    hecha = _act(cli, juan, "Mandar presupuesto", LUNES)
    cli.patch(f"/api/daily/actividades/{hecha}", json={"hecha": True})
    _act(cli, _gonzalo(cli), "De Gonzalo", LUNES)

    martes = _dia(cli, juan)
    assert martes["fecha"] == MARTES
    assert _textos(martes["pendientes_ayer"]) == ["Terminar landing"]
    assert _dia(cli, juan, MIERCOLES)["pendientes_ayer"] == [], "solo mira el día anterior"

    r = cli.post(f"/api/daily/actividades/{sin_hacer}/pasar", json={})
    assert r.status_code == 200 and r.get_json()["fecha"] == MARTES
    martes = _dia(cli, juan)
    assert martes["pendientes_ayer"] == []
    assert martes["actividades"][0]["texto"] == "Terminar landing"
    assert martes["actividades"][0]["pasada_de"] == LUNES and martes["actividades"][0]["hecha"] is False
    assert _textos(_dia(cli, juan, LUNES)["actividades"]) == ["Mandar presupuesto"], "se mueve, no se copia"

    # Pasarla de nuevo recuerda el día en que se cargó; al mismo día no cambia nada.
    cli.post(f"/api/daily/actividades/{sin_hacer}/pasar", json={"fecha": MIERCOLES})
    assert _dia(cli, juan, MIERCOLES)["actividades"][0]["pasada_de"] == LUNES
    assert cli.post(f"/api/daily/actividades/{sin_hacer}/pasar", json={"fecha": MIERCOLES}).status_code == 200
    assert _dia(cli, juan, MIERCOLES)["actividades"][0]["pasada_de"] == LUNES
    assert cli.post("/api/daily/actividades/9999/pasar", json={}).status_code == 404


def test_un_dia_futuro_no_tiene_pendiente_de_ayer_y_uno_pasado_si(cli, monkeypatch):
    juan = _juan(cli)
    _act(cli, juan, "Quedó sin hacer el martes", MARTES)
    assert _dia(cli, juan, MIERCOLES)["pendientes_ayer"] == [], "el miércoles todavía no llegó"
    monkeypatch.setattr(rutas_daily, "_ahora", lambda: datetime(2026, 9, 17, 13, 0, tzinfo=timezone.utc))
    assert _textos(_dia(cli, juan, MIERCOLES)["pendientes_ayer"]) == ["Quedó sin hacer el martes"], \
        "mirando un día pasado se ve lo que quedó"


# ── registrado en todos lados ────────────────────────────────────────────────

def test_daily_va_en_operacion_despues_de_tareas_con_lugar_para_las_personas():
    menu = HTML[HTML.index('<div class="nav-scroll">'):HTML.index('<div class="sidebar-bottom">')]
    operacion = _entre(menu, '<div class="nav-section-label">OPERACIÓN</div>',
                       '<div class="nav-section-label">RECURSOS HUMANOS</div>')
    assert re.findall(r'id="nav-(\w+)"', operacion) == ["clientes", "projects", "tasks", "daily", "activity"]
    assert ("<div class=\"nav-item\" id=\"nav-daily\" onclick=\"showPanel('daily')\">"
            "<i data-lucide=\"clipboard-list\" class=\"nav-icon\"></i> Daily Programador</div>\n"
            "  <div class=\"dy-nav-personas\" id=\"dy-nav-personas\"></div>") in operacion


def test_esta_registrado_en_todos_lados():
    prioridad = re.findall(r"'(\w+)'", re.search(r"const NAV_PRIORITY = \[([^\]]*)\]", HTML).group(1))
    assert prioridad.index("daily") == prioridad.index("tasks") + 1
    assert "daily:'clipboard-list'" in re.search(r"const NAV_ICONS = \{(.*?)\n\}", HTML, re.S).group(1)
    assert "daily:'Daily'" in re.search(r"const NAV_LABELS = \{(.*?)\n\}", HTML, re.S).group(1)
    listas = re.findall(r"const ALL_PANELS = \[([^\]]*)\]", SRC)
    assert len(listas) == 2 and all("'daily'" in lista for lista in listas)
    assert "daily:'Daily Programador'" in re.search(r"const PANEL_LABELS = \{([^}]*)\}", SRC).group(1)
    assert "if (name === 'daily') loadDaily();" in HTML
    assert re.search(r"\n#nav-daily \.nav-icon\{stroke:#[0-9a-f]{6}\}", HTML)
    assert re.search(r"\nbody\.light #nav-daily \.nav-icon\{stroke:#[0-9a-f]{6}\}", HTML)
    assert "from routes.daily import daily_bp" in SRC
    assert "daily_bp" in re.search(r"for bp in \((.*?)\):", SRC, re.S).group(1)
    fuente_db = (RAIZ / "database.py").read_text(encoding="utf-8")
    assert '_grant_panel_to_existing_roles(conn, "daily", solo_si_tiene="tasks")' in fuente_db


def test_cada_persona_tiene_su_color_en_los_dos_temas():
    """El ícono y el borde de los recordatorios usan tokens, que ya cambian con
    el tema: no hace falta una regla clara aparte."""
    for i in range(4):
        assert re.search(r"\.dy-nav-icono\.dy-color-" + str(i) + r"\{stroke:var\(--[\w-]+\)\}", CSS), i
        assert re.search(r"\.dy-borde-" + str(i) + r"\{border-left:3px solid var\(--[\w-]+\)\}", CSS), i
    assert "const DY_COLORES = 4;" in JS


def test_el_dia_reusa_las_piezas_de_seguimiento_de_leads():
    assert 'class="page-header sl-cabecera"' in PANEL and 'class="sl-contadores" id="dy-contadores"' in PANEL
    assert ">Nueva actividad</button>" in PANEL
    for clase in ("sl-grupo", "sl-grupo-titulo-vencidos", "sl-tarjeta", "sl-vencida", "sl-acciones",
                  "sl-btn-hecho", "sl-linea", "sl-contador-vencidos", "sl-nota", "sl-cuando"):
        assert clase in JS, clase
        assert re.search(r"\." + clase + r"\{", SRC), f"{clase} ya no existe en el CSS de Seguimiento"


def test_la_pagina_abre_con_el_panel(cli):
    r = cli.get("/")
    assert r.status_code == 200
    pagina = r.get_data(as_text=True)
    assert pagina.count('id="daily-panel"') == 1 and pagina.count('id="nav-daily"') == 1
    assert pagina.count('id="dy-nav-personas"') == 1
    assert pagina.count('id="dy-modal-actividad"') == 1 and pagina.count('id="dy-contadores"') == 1


# ── las trampas de DASHBOARD_HTML ────────────────────────────────────────────

@pytest.mark.parametrize("nombre", sorted(FUENTES))
def test_nada_que_jinja_o_python_interpreten(nombre):
    texto = FUENTES[nombre]
    for trampa in ("{#", "{{", "{%"):
        assert trampa not in texto, f"{trampa!r} en el {nombre} del Daily"
    assert chr(92) not in texto, f"un backslash en el {nombre}: Python se lo come"


def test_el_css_usa_solo_tokens_y_nada_inline():
    reglas = re.findall(r"([^{}]+)\{([^{}]*)\}", CSS)
    assert len(reglas) > 25
    for selector, cuerpo in reglas:
        assert not re.search(r"#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])|rgba?\(", cuerpo), selector.strip()
    assert "body.light" not in CSS.split("*/", 1)[1]
    for nombre in ("panel", "modales", "js"):
        assert "style=" not in FUENTES[nombre], nombre
    assert "setInterval" not in JS and "showPanel" not in re.search(
        r"// Initial load.*?\n([^/\s].*?)\n", HTML, re.S).group(1)


def test_en_el_celular_los_botones_tienen_al_menos_40px():
    movil = CSS[CSS.index("@media(max-width:600px){"):]
    assert "#daily-panel .sl-btn" in movil and "min-height:40px" in movil


def test_todo_lo_del_js_lleva_el_prefijo_dy():
    nombres = re.findall(r"^(?:async )?function ([A-Za-z_$][\w$]*)\(", JS, re.M)
    nombres += re.findall(r"^(?:const|let) ([A-Za-z_$][\w$]*)", JS, re.M)
    assert len(nombres) > 20
    sueltos = [n for n in nombres if not (n.startswith("dy") or n.startswith("DY_") or n == "loadDaily")]
    assert not sueltos, f"sin prefijo: {sueltos}"
    for nombre in nombres:
        declaraciones = re.findall(r"^(?:async )?(?:function|const|let) " + re.escape(nombre) + r"\b",
                                   HTML, re.M)
        assert len(declaraciones) == 1, f"{nombre} está declarado {len(declaraciones)} veces"


# ── se pinta de verdad ───────────────────────────────────────────────────────
# El mismo arnés de tests/test_equipo.py: todos los <script> del dashboard en
# node, con un DOM falso y un fetch que responde lo que devolvió el servidor.

_ARNES = r"""
const _els = {};
function _el(id) {
  if (!_els[id]) {
    _els[id] = { id, innerHTML: '', textContent: '', value: '', placeholder: '', type: '',
                 className: '', style: {}, dataset: {}, options: [], selectedIndex: 0,
                 classList: { add(){}, remove(){}, toggle(){}, contains: () => false },
                 querySelectorAll: () => [], querySelector: () => null, closest: () => null,
                 setAttribute(){}, getAttribute: () => null, focus(){},
                 addEventListener(){}, appendChild(){}, remove(){} };
  }
  return _els[id];
}
globalThis.document = {
  getElementById: _el, querySelectorAll: () => [], querySelector: () => null,
  body: { classList: { contains: () => false, add(){}, remove(){}, toggle(){} } },
  createElement: () => _el('tmp'), addEventListener(){},
};
globalThis.window = globalThis;
globalThis.localStorage = { getItem: () => null, setItem(){}, removeItem(){} };
globalThis.lucide = { createIcons(){} };
globalThis.alert = () => {};
globalThis.confirm = () => true;
globalThis.setInterval = () => 0;
const _RESPUESTAS = __RESPUESTAS__;
const _pedidos = [];
globalThis.fetch = (url, opciones) => {
  _pedidos.push([String(url), (opciones && opciones.method) || 'GET', (opciones && opciones.body) || null]);
  const r = _RESPUESTAS[String(url)];
  if (r === 'falla') return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) });
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(r === undefined ? {} : r) });
};
"""


def _correr_js(tmp_path, respuestas, prueba):
    bloques = re.findall(r"<script>(.*?)</script>", HTML, re.S)
    archivo = tmp_path / "daily.js"
    archivo.write_text(_ARNES.replace("__RESPUESTAS__", json.dumps(respuestas, ensure_ascii=False))
                       + "\n".join(bloques) + "\n" + prueba, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, (r.stderr or "")[:2000]
    return json.loads(r.stdout.strip().splitlines()[-1])


_PRUEBA = """
(async () => {
  const s = {};
  await dyCargarPersonas();
  s.menu = _el('dy-nav-personas').innerHTML;
  s.selector = _el('dy-personas').innerHTML;
  s.primera = dyPersonaId;

  dyAbrirPersona(__GONZALO__);
  s.panel = activePanel;
  await loadDaily();
  s.tituloGonzalo = _el('dy-titulo').textContent;
  s.listaGonzalo = _el('dy-lista').innerHTML;
  s.menuConGonzalo = _el('dy-nav-personas').innerHTML;
  dyMoverDia(1);
  await dyCargarDia();
  s.vacioFuturo = _el('dy-lista').innerHTML;
  s.contadoresVacio = _el('dy-contadores').innerHTML;

  dyElegirPersona(__JUAN__);
  dyIrHoy();
  await dyCargarDia();
  s.titulo = _el('dy-titulo').textContent;
  s.resumen = _el('dy-resumen').textContent;
  s.fecha = _el('dy-fecha').innerHTML;
  s.contadores = _el('dy-contadores').innerHTML;
  s.lista = _el('dy-lista').innerHTML;
  s.recordatorios = _el('dy-recordatorios').innerHTML;

  const antes = _pedidos.length;
  await dyActividadHecha(__TEMPRANO__);
  await dyPasarAManiana(__SIN_HORA__);
  await dyPasar(__PENDIENTE__);
  await dyRecordatorioHecho(__DIARIO__);
  await dyDeshacerActividad(__HECHA__);
  await dyDeshacerRecordatorio(__REC_HECHO__);

  _el('dy-nueva').value = '   ';
  await dyAgregar();
  s.errorVacio = _el('dy-error').textContent;
  _el('dy-nueva').value = 'Llamar a Tito';
  await dyAgregar();
  s.nuevaVacia = _el('dy-nueva').value;

  dyAbrirActividad();
  s.modalTitulo = _el('dy-act-titulo').textContent;
  s.modalContexto = _el('dy-act-contexto').textContent;
  s.modalFecha = _el('dy-act-fecha').value;
  _el('dy-act-texto').value = '  ';
  await dyGuardarActividad();
  s.modalError = _el('dy-act-error').textContent;
  _el('dy-act-texto').value = 'Preparar demo';
  _el('dy-act-hora').value = '16:00';
  _el('dy-act-nota').value = 'Con datos reales';
  await dyGuardarActividad();

  dyAbrirActividad(__TEMPRANO__);
  s.editTitulo = _el('dy-act-titulo').textContent;
  s.editTexto = _el('dy-act-texto').value;
  s.editHora = _el('dy-act-hora').value;
  s.editNota = _el('dy-act-nota').value;
  _el('dy-act-hora').value = '11:00';
  await dyGuardarActividad();

  dyAbrirActividad(__SIN_HORA__);
  await dyBorrarDesdeModal();

  _el('dy-rec-texto').value = 'Daily con el equipo';
  _el('dy-rec-frecuencia').value = 'dias';
  await dyGuardarRecordatorio();
  s.errorDias = _el('dy-rec-error').textContent;
  _el('dy-rec-dia-1').checked = true;
  _el('dy-rec-hora').value = '18:00';
  _el('dy-rec-nota').value = 'Antes de irse';
  await dyGuardarRecordatorio();
  s.textoTrasCrear = _el('dy-rec-texto').value;
  dyEditarRecordatorio(__DIARIO__);
  s.editRecHora = _el('dy-rec-hora').value;
  s.editRecNota = _el('dy-rec-nota').value;
  s.botonEdit = _el('dy-rec-guardar').textContent;
  s.pedidos = _pedidos.slice(antes);

  dyMoverDia(-1);
  await dyCargarDia();
  s.fechaLunes = dyFecha;
  s.listaLunes = _el('dy-lista').innerHTML;

  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
"""


def _grupo(lista, clave):
    """El HTML de un grupo de la lista, hasta el grupo siguiente."""
    inicio = lista.index(f'id="dy-grupo-{clave}"')
    siguientes = [lista.index(m, inicio + 1) for m in ("<section", "<details", '<div class="dy-vacio-grande"')
                  if m in lista[inicio + 1:]]
    return lista[inicio:min(siguientes)] if siguientes else lista[inicio:]


def _hubo(pedidos, url, metodo, cuerpo=None):
    return any(p[0] == url and p[1] == metodo and (cuerpo is None or json.loads(p[2] or "null") == cuerpo)
               for p in pedidos)


@sin_node
def test_la_pantalla_se_pinta_como_seguimiento_y_cada_persona_del_menu_abre_su_dia(cli, tmp_path):
    juan, gonzalo = _juan(cli), _gonzalo(cli)
    pendiente = _act(cli, juan, "Terminar landing", LUNES, hora="08:00")
    sin_hora = _act(cli, juan, "Revisar PR")
    temprano = _act(cli, juan, "Deploy <b>Bar Tito</b>", hora="09:30", nota="Pedir acceso al hosting")
    hecha = _act(cli, juan, "Mandar presupuesto")
    cli.patch(f"/api/daily/actividades/{hecha}", json={"hecha": True})
    diario = _rec(cli, juan, "Revisar mails de clientes", hora="10:00", nota="Los urgentes primero")
    _rec(cli, juan, "Daily con Matías", "dias", [1, 3])
    rec_hecho = _rec(cli, juan, "Subir backup", "habiles")
    cli.put(f"/api/daily/recordatorios/{rec_hecho}/marca", json={"fecha": MARTES, "hecha": True})
    pausado = _rec(cli, juan, "Llamar al contador", "habiles")
    cli.put(f"/api/daily/recordatorios/{pausado}", json={"activo": False})
    _act(cli, gonzalo, "Reunión con Matías")

    respuestas = {
        "/api/daily/personas": cli.get("/api/daily/personas").get_json(),
        f"/api/daily?persona_id={juan}": _dia(cli, juan),
        f"/api/daily?persona_id={juan}&fecha={MARTES}": _dia(cli, juan, MARTES),
        f"/api/daily?persona_id={juan}&fecha={LUNES}": _dia(cli, juan, LUNES),
        f"/api/daily?persona_id={juan}&fecha={MIERCOLES}": _dia(cli, juan, MIERCOLES),
        f"/api/daily?persona_id={gonzalo}": _dia(cli, gonzalo),
        f"/api/daily?persona_id={gonzalo}&fecha={MARTES}": _dia(cli, gonzalo, MARTES),
        f"/api/daily?persona_id={gonzalo}&fecha={MIERCOLES}": _dia(cli, gonzalo, MIERCOLES),
    }
    prueba = _PRUEBA
    for clave, valor in {"__JUAN__": juan, "__GONZALO__": gonzalo, "__PENDIENTE__": pendiente,
                         "__SIN_HORA__": sin_hora, "__TEMPRANO__": temprano, "__HECHA__": hecha,
                         "__DIARIO__": diario, "__REC_HECHO__": rec_hecho}.items():
        prueba = prueba.replace(clave, str(valor))
    s = _correr_js(tmp_path, respuestas, prueba)

    # ── menú: cada persona con su ícono a la izquierda, en su color ──
    icono_juan = '<i data-lucide="user-round" class="dy-nav-icono dy-color-0" aria-hidden="true"></i><span>Juan</span>'
    icono_gonzalo = '<i data-lucide="user-round" class="dy-nav-icono dy-color-1" aria-hidden="true"></i><span>Gonzalo</span>'
    assert icono_juan in s["menu"] and icono_gonzalo in s["menu"]
    assert s["menu"].index(icono_juan) < s["menu"].index(icono_gonzalo)
    assert f"dyAbrirPersona({juan})" in s["menu"] and f"dyAbrirPersona({gonzalo})" in s["menu"]
    assert "dy-activa" not in s["menu"], "fuera del panel no se marca ninguna"
    assert icono_juan + "</button>" in s["selector"] and icono_gonzalo + "</button>" in s["selector"], \
        "en el celular, el mismo ícono en el selector"
    assert 'aria-pressed="true" onclick="dyElegirPersona(%d)"' % juan in s["selector"]
    assert s["primera"] == juan

    # ── Gonzalo desde el menú ──
    assert s["panel"] == "daily" and s["tituloGonzalo"] == "Daily de Gonzalo"
    assert f'class="dy-nav-sub dy-activa" id="dy-nav-persona-{gonzalo}"' in s["menuConGonzalo"]
    assert "Reunión con Matías" in s["listaGonzalo"] and "Revisar PR" not in s["listaGonzalo"]
    assert "Nada pendiente para este día." in s["vacioFuturo"] and 'onclick="dyAbrirActividad()"' in s["vacioFuturo"]
    assert "sl-contador-vencidos" not in s["contadoresVacio"], "sin nada de ayer, sin rojo"

    # ── encabezado y contadores del día de Juan ──
    assert s["titulo"] == "Daily de Juan"
    assert s["resumen"] == "4 pendientes · 2 hechas · 1 de ayer"
    assert s["fecha"] == 'martes 15 de septiembre<span class="dy-fecha-hoy">Hoy</span>'
    contadores = re.findall(r'data-grupo="(\w+)".*?sl-contador-num">(\d+)</span><span class="sl-contador-rot">([^<]+)<',
                            s["contadores"])
    assert contadores == [("ayer", "1", "Pendiente de ayer"), ("hoy", "2", "Hoy"),
                          ("recordatorios", "2", "Recordatorios"), ("hechas", "2", "Hechas")]
    assert 'class="sl-contador sl-contador-vencidos" data-grupo="ayer"' in s["contadores"]
    assert 'onclick="dyIrAGrupo(this.dataset.grupo)"' in s["contadores"]

    # ── grupos en orden, con su título ──
    lista = s["lista"]
    orden = [lista.index(f'id="dy-grupo-{g}"') for g in ("ayer", "hoy", "recordatorios", "hechas")]
    assert orden == sorted(orden)
    assert '<h2 class="sl-grupo-titulo sl-grupo-titulo-vencidos">PENDIENTE DE AYER</h2>' in lista
    assert '<h2 class="sl-grupo-titulo">HOY · MARTES 15 DE SEPTIEMBRE</h2>' in lista
    assert '<h2 class="sl-grupo-titulo">RECORDATORIOS DE HOY</h2>' in lista
    assert '<summary class="sl-grupo-titulo">HECHAS (2)</summary>' in lista
    assert '<details class="sl-grupo dy-hechas" id="dy-grupo-hechas" ontoggle' in lista, "colapsado por defecto"
    assert "Nada pendiente" not in lista

    # Pendiente de ayer: borde rojo, hora en rojo, "Pasar a hoy".
    ayer = _grupo(lista, "ayer")
    assert ayer.count("<article") == 1
    assert '<article class="sl-tarjeta dy-tarjeta sl-vencida"' in ayer and "Terminar landing" in ayer
    assert 'class="sl-cuando sl-cuando-vencido">08:00' in ayer
    botones = re.findall(r'<button type="button" class="sl-btn[^"]*"[^>]*>([^<]+)</button>', ayer)
    assert botones == ["Pasar a hoy", "Hecho", "Editar"]

    # Hoy: tarjetas completas en orden de hora, con nota y los tres botones.
    hoy = _grupo(lista, "hoy")
    assert hoy.count("<article") == 2 and "sl-vencida" not in hoy
    assert hoy.index("Deploy") < hoy.index("Revisar PR")
    assert "&lt;b&gt;Bar Tito&lt;/b&gt;" in hoy and "<b>" not in hoy
    assert '<div class="sl-cuando">09:30</div>' in hoy and '<div class="sl-nota">Pedir acceso al hosting</div>' in hoy
    botones = re.findall(r'<button type="button" class="sl-btn[^"]*"[^>]*>([^<]+)</button>', hoy)
    assert botones == ["Hecho", "Pasar a mañana", "Editar"] * 2
    assert 'class="sl-btn sl-btn-hecho" data-id="%d" onclick="dyActividadHecha(' % temprano in hoy

    # Recordatorios de hoy: borde del color de la persona y etiqueta de frecuencia.
    recs = _grupo(lista, "recordatorios")
    assert recs.count('<article class="sl-tarjeta dy-tarjeta dy-borde-0"') == 2
    assert recs.index("Revisar mails de clientes") < recs.index("Daily con Matías"), "con hora primero"
    assert '<span class="dy-etiqueta">Todos los días</span>' in recs and '<span class="dy-etiqueta">Mar y Jue</span>' in recs
    assert re.findall(r'class="sl-btn[^"]*"[^>]*>([^<]+)</button>', recs) == ["Hecho", "Editar"] * 2
    assert "Llamar al contador" not in lista, "pausado no aparece en el día"

    # Hechas: una línea tachada con Deshacer.
    hechas = _grupo(lista, "hechas")
    assert hechas.count('class="sl-linea dy-linea-hecha"') == 2 and hechas.count(">Deshacer</button>") == 2
    assert '<div class="dy-tachado">Mandar presupuesto</div>' in hechas and "Subir backup" in hechas
    assert f'onclick="dyDeshacerActividad(Number(this.dataset.id))"' in hechas
    assert "dyDeshacerRecordatorio(" in hechas
    assert "Mandar presupuesto" not in hoy

    assert "Llamar al contador" in s["recordatorios"] and "Pausado" in s["recordatorios"]
    assert "Todos los días · 10:00" in s["recordatorios"]

    # ── lo que manda cada botón ──
    p = s["pedidos"]
    assert _hubo(p, f"/api/daily/actividades/{temprano}", "PATCH", {"hecha": True}), "Hecho"
    assert _hubo(p, f"/api/daily/actividades/{sin_hora}/pasar", "POST", {"fecha": MIERCOLES}), "Pasar a mañana"
    assert _hubo(p, f"/api/daily/actividades/{pendiente}/pasar", "POST", {"fecha": MARTES}), "Pasar a hoy"
    assert _hubo(p, f"/api/daily/recordatorios/{diario}/marca", "PUT", {"fecha": MARTES, "hecha": True})
    assert _hubo(p, f"/api/daily/actividades/{hecha}", "PATCH", {"hecha": False}), "Deshacer"
    assert _hubo(p, f"/api/daily/recordatorios/{rec_hecho}/marca", "PUT", {"fecha": MARTES, "hecha": False})

    assert s["errorVacio"] and s["nuevaVacia"] == ""
    assert _hubo(p, "/api/daily/actividades", "POST", {"persona_id": juan, "fecha": MARTES, "texto": "Llamar a Tito"})
    altas = [x for x in p if x[0] == "/api/daily/actividades" and x[1] == "POST"]
    assert len(altas) == 2, "el vacío y el modal sin texto no mandan nada"

    assert s["modalTitulo"] == "Nueva actividad" and s["modalContexto"] == "Daily de Juan"
    assert s["modalFecha"] == MARTES and "Escribí qué hay que hacer" in s["modalError"]
    assert _hubo(p, "/api/daily/actividades", "POST", {"persona_id": juan, "texto": "Preparar demo",
                                                       "fecha": MARTES, "hora": "16:00", "nota": "Con datos reales"})
    assert s["editTitulo"] == "Editar actividad" and s["editTexto"] == "Deploy <b>Bar Tito</b>"
    assert s["editHora"] == "09:30" and s["editNota"] == "Pedir acceso al hosting"
    assert _hubo(p, f"/api/daily/actividades/{temprano}", "PATCH", {"texto": "Deploy <b>Bar Tito</b>", "fecha": MARTES,
                                                                    "hora": "11:00", "nota": "Pedir acceso al hosting"})
    assert _hubo(p, f"/api/daily/actividades/{sin_hora}", "DELETE")

    assert "al menos un día" in s["errorDias"]
    assert _hubo(p, "/api/daily/recordatorios", "POST", {"persona_id": juan, "texto": "Daily con el equipo",
                                                         "frecuencia": "dias", "dias": [1], "hora": "18:00",
                                                         "nota": "Antes de irse"})
    assert s["textoTrasCrear"] == ""
    assert s["editRecHora"] == "10:00" and s["editRecNota"] == "Los urgentes primero"
    assert s["botonEdit"] == "Guardar cambios"

    # ── un día pasado ──
    assert s["fechaLunes"] == LUNES
    assert "Terminar landing" in s["listaLunes"] and "dy-grupo-ayer" not in s["listaLunes"]
    assert '<h2 class="sl-grupo-titulo">LUNES 14 DE SEPTIEMBRE</h2>' in s["listaLunes"]
