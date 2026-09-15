"""Daily Programador y Daily Admin: el día de cada persona.

Pedido de Juan (15/9): una sección "Daily Programador" donde se puedan poner
actividades y recordatorios de lo que hay que hacer cada día, para no
olvidarse, con una opción por persona en el menú. 16/9:

- cada persona con su ícono en el menú, y el día pintado como Seguimiento de
  leads (contadores, grupos y tarjetas), con hora y nota opcionales;
- Daily Admin, la misma pantalla para Juanchi (Juan Pereyra) y Javier, y
  Matías se suma a Daily Programador (Juan Tomasetti, Gonzalo y Matías);
- todo lo cargado se edita desde su tarjeta.

Las personas salen de Recursos Humanos: `equipo_personas.programador` y
`admin_daily`. El reloj está fijo en el martes 15/9/2026, 10:00 de Montevideo
(13:00 UTC).
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
from services.daily import cuando, hoy_montevideo, le_toca, nombre_para_mostrar

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


def _set(db, sql, params=()):
    conn = sqlite3.connect(db)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


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


def _cli_con_paneles(app, email, paneles):
    db = app.config["_DB"]
    return _cli(app, _usuario(db, email, _rol(db, f"rol-{email}", paneles)))


@pytest.fixture
def cli(app):
    return _cli(app, _usuario(app.config["_DB"], "jefe@scalerics.com"))


def _db(c):
    return c.application.config["_DB"]


def _pid(db, nombre):
    return next(p["id"] for p in listar_personas_equipo(db, incluir_inactivas=True) if p["nombre"] == nombre)


def _juan(c):
    """El Juan de Daily Programador."""
    return _pid(_db(c), "Juan Tomasetti")


def _gonzalo(c):
    return _pid(_db(c), "Gonzalo Siuciak")


def _matias(c):
    return _pid(_db(c), "Matías Domínguez")


def _juanchi(c):
    """El de Daily Admin."""
    return _pid(_db(c), "Juan Pereyra")


def _javier(c):
    return _pid(_db(c), "Javier Tomasetti")


def _url(seccion, persona_id, fecha=None):
    """La misma URL que arma el JS, para las respuestas del pintado en node."""
    return f"/api/daily?seccion={seccion}&persona_id={persona_id}" + (f"&fecha={fecha}" if fecha else "")


def _dia(c, persona_id, fecha=None, seccion="programador"):
    r = c.get(_url(seccion, persona_id, fecha))
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def _act(c, persona_id, texto, fecha=MARTES, seccion="programador", **extra):
    r = c.post("/api/daily/actividades",
               json={"seccion": seccion, "persona_id": persona_id, "texto": texto, "fecha": fecha, **extra})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _rec(c, persona_id, texto, frecuencia="diario", dias=None, seccion="programador", **extra):
    datos = {"seccion": seccion, "persona_id": persona_id, "texto": texto, "frecuencia": frecuencia, **extra}
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


def _marcados(db, campo):
    conn = sqlite3.connect(db)
    try:
        return {n for (n,) in conn.execute(f"SELECT nombre FROM equipo_personas WHERE {campo} = 1")}
    finally:
        conn.close()


def _apodos(db):
    conn = sqlite3.connect(db)
    try:
        return dict(conn.execute("SELECT nombre, apodo FROM equipo_personas WHERE apodo IS NOT NULL"))
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


# ── personas de cada Daily ───────────────────────────────────────────────────

def test_las_personas_de_cada_daily(cli):
    """Daily Programador: Juan (Tomasetti), Gonzalo y Matías. Daily Admin:
    Juanchi (Juan Pereyra) y Javier. En orden de alta en Recursos Humanos."""
    r = cli.get("/api/daily/personas?seccion=programador")
    assert r.status_code == 200
    assert [(p["nombre"], p["mostrar"]) for p in r.get_json()["personas"]] == [
        ("Matías Domínguez", "Matías"), ("Juan Tomasetti", "Juan"), ("Gonzalo Siuciak", "Gonzalo")]
    assert cli.get("/api/daily/personas").get_json()["personas"] == r.get_json()["personas"], \
        "sin seccion es Daily Programador"
    admin = cli.get("/api/daily/personas?seccion=admin").get_json()["personas"]
    assert [(p["nombre"], p["primer_nombre"], p["mostrar"]) for p in admin] == [
        ("Juan Pereyra", "Juan", "Juanchi"), ("Javier Tomasetti", "Javier", "Javier")]
    assert cli.get("/api/daily/personas?seccion=ventas").status_code == 400


def test_sumar_a_alguien_es_prender_la_marca(cli):
    db = _db(cli)
    _set(db, "UPDATE equipo_personas SET programador = 1 WHERE nombre = ?", ("Guillermo Paredes",))
    _set(db, "UPDATE equipo_personas SET admin_daily = 1 WHERE nombre = ?", ("Andrés Rosi",))
    programadores = [p["mostrar"] for p in cli.get("/api/daily/personas").get_json()["personas"]]
    admins = [p["mostrar"] for p in cli.get("/api/daily/personas?seccion=admin").get_json()["personas"]]
    assert "Guillermo" in programadores and "Andrés" in admins, "sin tocar código"
    _set(db, "UPDATE equipo_personas SET activo = 0 WHERE nombre = ?", ("Guillermo Paredes",))
    assert "Guillermo" not in [p["mostrar"] for p in cli.get("/api/daily/personas").get_json()["personas"]]


def test_el_nombre_para_mostrar():
    assert nombre_para_mostrar({"nombre": "Juan Pereyra", "apodo": "Juanchi"}) == "Juanchi"
    assert nombre_para_mostrar({"nombre": "Juan Pereyra", "apodo": "   "}) == "Juan"
    assert nombre_para_mostrar({"nombre": "Javier Tomasetti", "apodo": None}) == "Javier"
    assert nombre_para_mostrar({"nombre": "Matías Domínguez"}) == "Matías"


def test_la_precarga_de_personas_y_el_apodo_no_pisan_un_cambio_a_mano(tmp_path):
    db = str(tmp_path / "p.db")
    init_db(db)
    assert _marcados(db, "programador") == {"Juan Tomasetti", "Gonzalo Siuciak", "Matías Domínguez"}
    assert _marcados(db, "admin_daily") == {"Juan Pereyra", "Javier Tomasetti"}
    assert _apodos(db) == {"Juan Pereyra": "Juanchi"}, "el apodo va en Juan Pereyra, no en Juan Tomasetti"
    _set(db, "UPDATE equipo_personas SET programador = 0 WHERE nombre = ?", ("Matías Domínguez",))
    _set(db, "UPDATE equipo_personas SET admin_daily = 0, apodo = 'Juan P.' WHERE nombre = ?", ("Juan Pereyra",))
    init_db(db)
    assert _marcados(db, "programador") == {"Juan Tomasetti", "Gonzalo Siuciak"}
    assert _marcados(db, "admin_daily") == {"Javier Tomasetti"}
    assert _apodos(db) == {"Juan Pereyra": "Juan P."}


def test_la_migracion_desde_la_version_publicada(tmp_path):
    """Una base como quedó con v226: `programador` ya existe (Juan Tomasetti y
    Gonzalo marcados, y alguien le sacó la marca a Gonzalo a mano), sin
    `admin_daily` ni `apodo`, y con actividades y recordatorios cargados sin
    hora, nota ni sección. Al arrancar: Matías se suma, Juanchi y Javier van a
    Daily Admin, lo de Gonzalo no se pisa y lo cargado sigue siendo de quien era."""
    db = str(tmp_path / "v226.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE equipo_personas (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, rol TEXT NOT NULL DEFAULT '',
        reporta_a INTEGER, lleva_horas INTEGER NOT NULL DEFAULT 0, horas_por_dia REAL NOT NULL DEFAULT 4,
        activo INTEGER NOT NULL DEFAULT 1, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        programador INTEGER NOT NULL DEFAULT 0)""")
    for nombre, marca in (("Juan Pereyra", 0), ("Javier Tomasetti", 0), ("Andrés Rosi", 0),
                          ("Matías Domínguez", 0), ("Guillermo Paredes", 0), ("Juan Tomasetti", 1),
                          ("Gonzalo Siuciak", 0)):
        conn.execute("INSERT INTO equipo_personas (nombre, programador) VALUES (?, ?)", (nombre, marca))
    tomasetti = conn.execute("SELECT id FROM equipo_personas WHERE nombre = 'Juan Tomasetti'").fetchone()[0]
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
                 "VALUES (?, ?, 'Cargada antes', 1, ?)", (tomasetti, MARTES, LUNES))
    conn.execute("INSERT INTO daily_recordatorios (persona_id, texto, frecuencia, dias, desde) "
                 "VALUES (?, 'Revisar mails', 'dias', '1,3', ?)", (tomasetti, LUNES))
    conn.commit()
    conn.close()

    init_db(db)
    init_db(db)  # dos arranques: la migración no falla ni se repite la segunda vez
    assert _marcados(db, "programador") == {"Juan Tomasetti", "Matías Domínguez"}, \
        "Matías se suma y Gonzalo sigue como lo dejaron a mano"
    assert _marcados(db, "admin_daily") == {"Juan Pereyra", "Javier Tomasetti"}
    assert _apodos(db) == {"Juan Pereyra": "Juanchi"}
    for tabla in ("daily_actividades", "daily_recordatorios"):
        assert {"hora", "nota", "seccion"} <= _columnas(db, tabla), tabla

    [a] = database.listar_actividades_daily(db, tomasetti, MARTES)
    assert (a["texto"], a["hecha"], a["pasada_de"], a["hora"], a["nota"], a["seccion"]) == (
        "Cargada antes", 1, LUNES, None, None, "programador"), "sigue siendo de Juan Tomasetti"
    [r] = database.listar_recordatorios_daily(db, tomasetti)
    assert (r["texto"], r["dias"], r["desde"], r["hora"], r["seccion"]) == (
        "Revisar mails", "1,3", LUNES, None, "programador")
    assert cuando(r) == "Mar y Jue"

    # Lo que se cambie a mano después tampoco se pisa.
    _set(db, "UPDATE equipo_personas SET programador = 0 WHERE nombre = ?", ("Matías Domínguez",))
    init_db(db)
    assert _marcados(db, "programador") == {"Juan Tomasetti"}


def test_una_persona_que_no_esta_en_ese_daily_da_404(cli):
    andres, juanchi, juan = _pid(_db(cli), "Andrés Rosi"), _juanchi(cli), _juan(cli)
    assert cli.get(_url("programador", andres)).status_code == 404
    assert cli.get(_url("programador", juanchi)).status_code == 404, "Juanchi es de Daily Admin"
    assert cli.get(_url("admin", juan)).status_code == 404, "Juan Tomasetti es de Daily Programador"
    assert cli.get("/api/daily?persona_id=99999").status_code == 404
    assert cli.get("/api/daily?persona_id=x").status_code == 400
    assert cli.get("/api/daily").status_code == 400
    assert cli.get(f"/api/daily?seccion=otra&persona_id={juan}").status_code == 400
    assert cli.post("/api/daily/actividades", json={"persona_id": andres, "texto": "x"}).status_code == 404
    assert cli.post("/api/daily/recordatorios",
                    json={"seccion": "admin", "persona_id": juan, "texto": "x"}).status_code == 404


# ── actividades ──────────────────────────────────────────────────────────────

def test_crud_de_actividades(cli):
    juan = _juan(cli)
    aid = _act(cli, juan, "  Terminar   la landing  ")
    assert _dia(cli, juan, MARTES)["actividades"] == [{
        "id": aid, "texto": "Terminar la landing", "hecha": False, "fecha": MARTES,
        "pasada_de": None, "hora": None, "nota": None, "seccion": "programador", "creada_por": "test"}]
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
    ({"texto": "ok", "seccion": "ventas"}, "seccion"),
])
def test_la_actividad_se_valida_en_el_servidor(cli, datos, parte):
    r = cli.post("/api/daily/actividades", json={"persona_id": _juan(cli), **datos})
    assert r.status_code == 400 and parte in r.get_json()["error"], r.get_json()


def test_editar_una_actividad_se_valida_como_el_alta(cli):
    aid = _act(cli, _juan(cli), "Algo")
    for cuerpo, parte in (({}, "nada para cambiar"), ({"hecha": "si"}, "hecha"), ({"hecha": 1}, "hecha"),
                          ({"texto": ""}, "vacío"), ({"texto": "x" * 201}, "200"), ({"hora": "24:00"}, "HH:MM"),
                          ({"nota": ["x"]}, "texto"), ({"nota": "x" * 301}, "300"), ({"fecha": "mañana"}, "AAAA-MM-DD")):
        r = cli.patch(f"/api/daily/actividades/{aid}", json=cuerpo)
        assert r.status_code == 400 and parte in r.get_json()["error"], (cuerpo, r.get_json())
    assert get_actividad_daily(_db(cli), aid)["texto"] == "Algo", "con datos inválidos no se toca nada"
    assert cli.post(f"/api/daily/actividades/{aid}/pasar", json={"fecha": "mañana"}).status_code == 400


def test_editar_una_actividad_la_cambia_de_grupo(cli):
    """Editar desde la tarjeta: texto, hora, nota y fecha. Si cambia la fecha,
    sale del día; lo de ayer editado a hoy deja de estar pendiente; y una hecha
    se edita sin dejar de estar hecha."""
    juan = _juan(cli)
    aid = _act(cli, juan, "Deploy", hora="09:00")
    r = cli.patch(f"/api/daily/actividades/{aid}",
                  json={"texto": "Deploy de Bar Tito", "hora": "10:30", "nota": "Con Gonzalo", "fecha": MIERCOLES})
    assert r.status_code == 200
    assert _dia(cli, juan, MARTES)["actividades"] == [], "cambió de fecha: sale de hoy"
    [miercoles] = _dia(cli, juan, MIERCOLES)["actividades"]
    assert (miercoles["texto"], miercoles["hora"], miercoles["nota"]) == ("Deploy de Bar Tito", "10:30", "Con Gonzalo")

    de_ayer = _act(cli, juan, "Quedó del lunes", LUNES)
    assert _textos(_dia(cli, juan, MARTES)["pendientes_ayer"]) == ["Quedó del lunes"]
    cli.patch(f"/api/daily/actividades/{de_ayer}", json={"fecha": MARTES, "texto": "Quedó del lunes (hoy sí)"})
    martes = _dia(cli, juan, MARTES)
    assert martes["pendientes_ayer"] == [] and _textos(martes["actividades"]) == ["Quedó del lunes (hoy sí)"]

    hecha = _act(cli, juan, "Mandar presupuesto")
    cli.patch(f"/api/daily/actividades/{hecha}", json={"hecha": True})
    cli.patch(f"/api/daily/actividades/{hecha}", json={"nota": "Por mail"})
    fila = next(a for a in _dia(cli, juan, MARTES)["actividades"] if a["id"] == hecha)
    assert fila["hecha"] is True and fila["nota"] == "Por mail"


def test_hora_y_nota_de_una_actividad_y_el_orden_por_hora(cli):
    juan = _juan(cli)
    sin_hora = _act(cli, juan, "Sin hora")
    tarde = _act(cli, juan, "A la tarde", hora="15:00")
    temprano = _act(cli, juan, "Temprano", hora="09:30", nota="  Pedir acceso al hosting  ")
    dia = _dia(cli, juan, MARTES)
    assert _textos(dia["actividades"]) == ["Temprano", "A la tarde", "Sin hora"], "con hora primero, en orden"
    assert dia["actividades"][0]["hora"] == "09:30"
    assert dia["actividades"][0]["nota"] == "Pedir acceso al hosting"
    assert cli.patch(f"/api/daily/actividades/{tarde}", json={"hora": "", "nota": "Después del almuerzo"}).status_code == 200
    fila = get_actividad_daily(_db(cli), tarde)
    assert fila["hora"] is None and fila["nota"] == "Después del almuerzo"
    assert cli.patch(f"/api/daily/actividades/{temprano}", json={"nota": ""}).status_code == 200
    assert get_actividad_daily(_db(cli), temprano)["nota"] is None, "una nota vacía es sin nota"
    assert cli.patch(f"/api/daily/actividades/{sin_hora}", json={"fecha": JUEVES}).status_code == 200
    assert _textos(_dia(cli, juan, JUEVES)["actividades"]) == ["Sin hora"]


def test_pasar_a_maniana_y_hecho(cli):
    """Los botones de cada tarjeta del día. "Pasar a mañana" es el mismo pasar
    de siempre con el día siguiente; "Hecho" es marcarla; "Deshacer", desmarcarla."""
    juan = _juan(cli)
    aid = _act(cli, juan, "Revisar PR", hora="11:00")
    r = cli.post(f"/api/daily/actividades/{aid}/pasar", json={"fecha": MIERCOLES})
    assert r.status_code == 200 and r.get_json()["fecha"] == MIERCOLES
    assert _dia(cli, juan, MARTES)["actividades"] == []
    [miercoles] = _dia(cli, juan, MIERCOLES)["actividades"]
    assert miercoles["pasada_de"] == MARTES and miercoles["hora"] == "11:00" and miercoles["hecha"] is False
    assert cli.patch(f"/api/daily/actividades/{aid}", json={"hecha": True}).status_code == 200
    assert _dia(cli, juan, MIERCOLES)["actividades"][0]["hecha"] is True
    assert cli.patch(f"/api/daily/actividades/{aid}", json={"hecha": False}).status_code == 200
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
    assert dj["persona"]["mostrar"] == "Juan" and dg["persona"]["mostrar"] == "Gonzalo"


# ── dos Daily, listas separadas ──────────────────────────────────────────────

def test_las_listas_de_cada_daily_estan_separadas(cli):
    """Si una persona está en los dos Daily, lo de uno no aparece en el otro:
    actividades, pendientes de ayer, recordatorios y sus marcas."""
    juanchi = _juanchi(cli)
    _set(_db(cli), "UPDATE equipo_personas SET programador = 1 WHERE id = ?", (juanchi,))
    _act(cli, juanchi, "Programar la integración", seccion="programador")
    _act(cli, juanchi, "Firmar el contrato", seccion="admin")
    _act(cli, juanchi, "Quedó en programador", LUNES, seccion="programador")
    _act(cli, juanchi, "Quedó en admin", LUNES, seccion="admin")
    _rec(cli, juanchi, "Subir backup", seccion="programador")
    caja = _rec(cli, juanchi, "Mirar la caja", seccion="admin")
    cli.put(f"/api/daily/recordatorios/{caja}/marca", json={"fecha": MARTES, "hecha": True})

    programador = _dia(cli, juanchi, MARTES, "programador")
    admin = _dia(cli, juanchi, MARTES, "admin")
    assert programador["seccion"] == "programador" and admin["seccion"] == "admin"
    assert _textos(programador["actividades"]) == ["Programar la integración"]
    assert _textos(admin["actividades"]) == ["Firmar el contrato"]
    assert _textos(programador["pendientes_ayer"]) == ["Quedó en programador"]
    assert _textos(admin["pendientes_ayer"]) == ["Quedó en admin"]
    assert _textos(programador["recordatorios_todos"]) == ["Subir backup"]
    assert [(r["texto"], r["hecha"]) for r in admin["recordatorios"]] == [("Mirar la caja", True)]
    assert [(r["texto"], r["hecha"]) for r in programador["recordatorios"]] == [("Subir backup", False)]
    assert admin["actividades"][0]["seccion"] == "admin"


# ── permisos ─────────────────────────────────────────────────────────────────

def test_sin_ninguno_de_los_dos_paneles_da_403(app):
    c = _cli_con_paneles(app, "tareas@scalerics.com", ["tasks"])
    juan = _pid(app.config["_DB"], "Juan Tomasetti")
    assert c.get("/api/daily/personas").status_code == 403
    assert c.get("/api/daily/personas?seccion=admin").status_code == 403
    assert c.get(_url("programador", juan)).status_code == 403
    assert c.post("/api/daily/actividades", json={"persona_id": juan, "texto": "x"}).status_code == 403


def test_con_daily_no_se_entra_a_daily_admin(app, cli):
    """Ni a la lista ni a un ítem ya cargado en Daily Admin (la sección sale de
    la fila, no de lo que mande el pedido)."""
    db = app.config["_DB"]
    juanchi, juan = _pid(db, "Juan Pereyra"), _pid(db, "Juan Tomasetti")
    act_admin = _act(cli, juanchi, "Firmar el contrato", seccion="admin")
    rec_admin = _rec(cli, juanchi, "Mirar la caja", seccion="admin")

    c = _cli_con_paneles(app, "programador@scalerics.com", ["daily"])
    assert c.get("/api/daily/personas").status_code == 200
    assert c.get(_url("programador", juan)).status_code == 200
    assert c.get("/api/daily/personas?seccion=admin").status_code == 403
    assert c.get(_url("admin", juanchi)).status_code == 403
    assert c.post("/api/daily/actividades", json={"seccion": "admin", "persona_id": juanchi, "texto": "x"}).status_code == 403
    assert c.post("/api/daily/recordatorios", json={"seccion": "admin", "persona_id": juanchi, "texto": "x"}).status_code == 403
    assert c.patch(f"/api/daily/actividades/{act_admin}", json={"texto": "Otro"}).status_code == 403
    assert c.post(f"/api/daily/actividades/{act_admin}/pasar", json={}).status_code == 403
    assert c.delete(f"/api/daily/actividades/{act_admin}").status_code == 403
    assert c.put(f"/api/daily/recordatorios/{rec_admin}", json={"texto": "Otro"}).status_code == 403
    assert c.put(f"/api/daily/recordatorios/{rec_admin}/marca", json={"fecha": MARTES, "hecha": True}).status_code == 403
    assert c.delete(f"/api/daily/recordatorios/{rec_admin}").status_code == 403
    assert get_actividad_daily(db, act_admin)["texto"] == "Firmar el contrato"


def test_con_daily_admin_no_se_entra_a_daily_programador(app, cli):
    db = app.config["_DB"]
    juan, juanchi = _pid(db, "Juan Tomasetti"), _pid(db, "Juan Pereyra")
    act_programador = _act(cli, juan, "Revisar PR")
    c = _cli_con_paneles(app, "admin-daily@scalerics.com", ["daily_admin"])
    assert c.get("/api/daily/personas?seccion=admin").status_code == 200
    aid = _act(c, juanchi, "Firmar el contrato", seccion="admin")
    assert c.patch(f"/api/daily/actividades/{aid}", json={"hora": "11:00"}).status_code == 200
    assert c.get("/api/daily/personas").status_code == 403, "sin seccion es Daily Programador"
    assert c.get(_url("programador", juan)).status_code == 403
    assert c.patch(f"/api/daily/actividades/{act_programador}", json={"texto": "x"}).status_code == 403


def test_un_admin_ve_daily_admin_sin_que_la_migracion_se_lo_de_a_los_roles(app, tmp_path):
    db = app.config["_DB"]
    conn = sqlite3.connect(db)
    acceso = {n: json.loads(p) for n, p in conn.execute("SELECT name, panel_access FROM roles")}
    admin_rol = conn.execute("SELECT id FROM roles WHERE name = 'Admin'").fetchone()[0]
    conn.close()
    assert all("daily_admin" not in paneles for paneles in acceso.values()), \
        "la migración no le da Daily Admin a ningún rol"
    c = _cli(app, _usuario(db, "otro-admin@scalerics.com", admin_rol))
    assert c.get("/api/daily/personas?seccion=admin").status_code == 200, "el rol Admin lo ve igual"
    assert c.get(_url("admin", _pid(db, "Javier Tomasetti"))).status_code == 200


def test_con_el_panel_cualquiera_entra_al_dia_de_cualquiera_y_queda_quien_lo_cargo(app):
    db = app.config["_DB"]
    uid = _usuario(db, "gonza@scalerics.com", _rol(db, "Solo daily", ["daily"]))
    c = _cli(app, uid, "Gonzalo")
    for persona in (_pid(db, "Juan Tomasetti"), _pid(db, "Gonzalo Siuciak"), _pid(db, "Matías Domínguez")):
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


def test_los_roles_con_tareas_reciben_el_daily_y_no_el_admin(tmp_path):
    db = str(tmp_path / "r.db")
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        acceso = {n: json.loads(p) for n, p in conn.execute("SELECT name, panel_access FROM roles")}
        assert "daily" in acceso["Admin"], "Admin tiene Tareas"
        assert "daily" not in acceso["Caller"] and "daily" not in acceso["Ventas"]
        assert all("daily_admin" not in paneles for paneles in acceso.values())
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
    assert cli.put(f"/api/daily/recordatorios/{rid}", json={"hora": "07:00"}).status_code == 200
    r = next(x for x in _dia(cli, juan, MARTES)["recordatorios_todos"] if x["id"] == rid)
    assert r["hora"] == "07:00" and r["nota"] == "Los de clientes primero", "mandar solo la hora no pisa la nota"
    assert cli.put(f"/api/daily/recordatorios/{rid}", json={"hora": "", "nota": ""}).status_code == 200
    r = next(x for x in _dia(cli, juan, MARTES)["recordatorios_todos"] if x["id"] == rid)
    assert r["hora"] is None and r["nota"] is None
    assert cli.put(f"/api/daily/recordatorios/{rid}", json={"hora": "99:99"}).status_code == 400


def test_editar_un_recordatorio_no_pierde_las_marcas_de_otros_dias(cli):
    juan = _juan(cli)
    rid = _rec(cli, juan, "Revisar mails")
    for fecha in (MARTES, MIERCOLES):
        cli.put(f"/api/daily/recordatorios/{rid}/marca", json={"fecha": fecha, "hecha": True})
    r = cli.put(f"/api/daily/recordatorios/{rid}", json={"texto": "Revisar mails de clientes", "frecuencia": "dias",
                                                         "dias": [3], "hora": "09:00", "nota": "Los urgentes"})
    assert r.status_code == 200
    assert _marcas(_db(cli)) == 2, "editar no borra marcas"
    for fecha in (MARTES, MIERCOLES):
        [rec] = _dia(cli, juan, fecha)["recordatorios"]
        assert (rec["texto"], rec["hecha"], rec["hora"], rec["cuando"]) == (
            "Revisar mails de clientes", True, "09:00", "Jue"), fecha
    [jueves] = _dia(cli, juan, JUEVES)["recordatorios"]
    assert jueves["hecha"] is False
    assert _dia(cli, juan, SABADO)["recordatorios"] == []


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
    assert _marcas(_db(cli)) == 0, "borrar se lleva sus marcas"
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

def test_los_dos_daily_van_en_operacion_despues_de_tareas_con_lugar_para_las_personas():
    menu = HTML[HTML.index('<div class="nav-scroll">'):HTML.index('<div class="sidebar-bottom">')]
    operacion = _entre(menu, '<div class="nav-section-label">OPERACIÓN</div>',
                       '<div class="nav-section-label">RECURSOS HUMANOS</div>')
    assert re.findall(r'id="nav-(\w+)"', operacion) == ["clientes", "projects", "tasks", "daily", "daily_admin",
                                                        "activity"]
    assert ("<div class=\"nav-item\" id=\"nav-daily\" onclick=\"showPanel('daily')\">"
            "<i data-lucide=\"clipboard-list\" class=\"nav-icon\"></i> Daily Programador</div>\n"
            "  <div class=\"dy-nav-personas\" id=\"dy-nav-personas\"></div>\n"
            "  <div class=\"nav-item\" id=\"nav-daily_admin\" onclick=\"showPanel('daily_admin')\">"
            "<i data-lucide=\"clipboard-check\" class=\"nav-icon\"></i> Daily Admin</div>\n"
            "  <div class=\"dy-nav-personas\" id=\"dya-nav-personas\"></div>") in operacion


def test_estan_registrados_en_todos_lados():
    prioridad = re.findall(r"'(\w+)'", re.search(r"const NAV_PRIORITY = \[([^\]]*)\]", HTML).group(1))
    assert prioridad.index("daily") == prioridad.index("tasks") + 1
    assert prioridad.index("daily_admin") == prioridad.index("daily") + 1
    iconos = re.search(r"const NAV_ICONS = \{(.*?)\n\}", HTML, re.S).group(1)
    assert "daily:'clipboard-list'" in iconos and "daily_admin:'clipboard-check'" in iconos
    etiquetas = re.search(r"const NAV_LABELS = \{(.*?)\n\}", HTML, re.S).group(1)
    assert "daily:'Daily'" in etiquetas and "daily_admin:'Daily Admin'" in etiquetas
    listas = re.findall(r"const ALL_PANELS = \[([^\]]*)\]", SRC)
    assert len(listas) == 2 and all("'daily'" in lista and "'daily_admin'" in lista for lista in listas)
    rotulos = re.search(r"const PANEL_LABELS = \{([^}]*)\}", SRC).group(1)
    assert "daily:'Daily Programador'" in rotulos and "daily_admin:'Daily Admin'" in rotulos
    assert "if (name === 'daily') loadDaily('programador');" in HTML
    assert "if (name === 'daily_admin') loadDaily('admin');" in HTML
    assert "if (allowedPanels.includes('daily')) dyCargarPersonas('programador');" in HTML
    assert "if (allowedPanels.includes('daily_admin')) dyCargarPersonas('admin');" in HTML
    for panel in ("daily", "daily_admin"):
        assert re.search(r"\n#nav-" + panel + r" \.nav-icon\{stroke:#[0-9a-f]{6}\}", HTML), panel
        assert re.search(r"\n#nav-" + panel + r"\.active \.nav-icon\{stroke:#[0-9a-f]{6}\}", HTML), panel
        assert re.search(r"\nbody\.light #nav-" + panel + r" \.nav-icon\{stroke:#[0-9a-f]{6}\}", HTML), panel
    assert "from routes.daily import daily_bp" in SRC
    assert "daily_bp" in re.search(r"for bp in \((.*?)\):", SRC, re.S).group(1)
    fuente_db = (RAIZ / "database.py").read_text(encoding="utf-8")
    assert '_grant_panel_to_existing_roles(conn, "daily", solo_si_tiene="tasks")' in fuente_db
    assert '"daily_admin"' not in fuente_db, "Daily Admin no se le da a ningún rol en la migración"


def test_cada_persona_tiene_su_color_en_los_dos_temas():
    """El ícono y el borde de los recordatorios usan tokens, que ya cambian con
    el tema: no hace falta una regla clara aparte."""
    for i in range(4):
        assert re.search(r"\.dy-nav-icono\.dy-color-" + str(i) + r"\{stroke:var\(--[\w-]+\)\}", CSS), i
        assert re.search(r"\.dy-borde-" + str(i) + r"\{border-left:3px solid var\(--[\w-]+\)\}", CSS), i
    assert "const DY_COLORES = 4;" in JS


def test_los_dos_paneles_son_la_misma_pantalla_con_las_piezas_de_seguimiento():
    assert '<div id="daily-panel" class="panel dy-panel"></div>' in PANEL
    assert '<div id="daily_admin-panel" class="panel dy-panel"></div>' in PANEL
    for pieza in ("page-header sl-cabecera", "sl-contadores", ">Nueva actividad</button>", "Nuevo recordatorio",
                  "sl-grupo", "sl-grupo-titulo-vencidos", "sl-tarjeta", "sl-vencida", "sl-acciones",
                  "sl-btn-hecho", "sl-linea", "sl-contador-vencidos", "sl-nota", "sl-cuando"):
        assert pieza in JS, pieza
    for clase in ("sl-cabecera", "sl-contadores", "sl-grupo", "sl-tarjeta", "sl-vencida", "sl-acciones",
                  "sl-btn-hecho", "sl-linea", "sl-contador-vencidos", "sl-nota", "sl-cuando"):
        assert re.search(r"\." + clase + r"\{", SRC), f"{clase} ya no existe en el CSS de Seguimiento"


def test_la_pagina_abre_con_los_dos_paneles(cli):
    r = cli.get("/")
    assert r.status_code == 200
    pagina = r.get_data(as_text=True)
    for id_ in ("daily-panel", "daily_admin-panel", "nav-daily", "nav-daily_admin", "dy-nav-personas",
                "dya-nav-personas", "dy-modal-actividad", "dy-modal-recordatorio"):
        assert pagina.count(f'id="{id_}"') == 1, id_


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
    assert ".dy-panel .sl-btn" in movil and "min-height:40px" in movil


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


def _reemplazar(prueba, valores):
    for clave, valor in valores.items():
        prueba = prueba.replace(clave, str(valor))
    return prueba


def _grupo(lista, prefijo, clave):
    """El HTML de un grupo de la lista, hasta el grupo siguiente."""
    inicio = lista.index(f'id="{prefijo}-grupo-{clave}"')
    siguientes = [lista.index(m, inicio + 1) for m in ("<section", "<details", '<div class="dy-vacio-grande"')
                  if m in lista[inicio + 1:]]
    return lista[inicio:min(siguientes)] if siguientes else lista[inicio:]


def _hubo(pedidos, url, metodo, cuerpo=None):
    return any(p[0] == url and p[1] == metodo and (cuerpo is None or json.loads(p[2] or "null") == cuerpo)
               for p in pedidos)


def _botones(html):
    return re.findall(r'<button type="button" class="sl-btn[^"]*"[^>]*>([^<]+)</button>', html)


_PRUEBA_PROGRAMADOR = """
(async () => {
  const s = {};
  const P = 'programador';
  await dyCargarPersonas(P);
  s.menu = _el('dy-nav-personas').innerHTML;
  s.selector = _el('dy-personas').innerHTML;
  s.primera = DY_EST.programador.personaId;

  dyAbrirPersona(P, __GONZALO__);
  s.panel = activePanel;
  await loadDaily(P);
  s.armado = _el('daily-panel').innerHTML;
  s.tituloGonzalo = _el('dy-titulo').textContent;
  s.listaGonzalo = _el('dy-lista').innerHTML;
  s.menuConGonzalo = _el('dy-nav-personas').innerHTML;
  dyMoverDia(P, 1);
  await dyCargarDia(P);
  s.vacioFuturo = _el('dy-lista').innerHTML;
  s.contadoresVacio = _el('dy-contadores').innerHTML;

  dyElegirPersona(P, __JUAN__);
  dyIrHoy(P);
  await dyCargarDia(P);
  s.titulo = _el('dy-titulo').textContent;
  s.resumen = _el('dy-resumen').textContent;
  s.fecha = _el('dy-fecha').innerHTML;
  s.contadores = _el('dy-contadores').innerHTML;
  s.lista = _el('dy-lista').innerHTML;
  s.recordatorios = _el('dy-recordatorios').innerHTML;
  s.admin = DY_EST.admin.datos === null;

  const antes = _pedidos.length;
  // Editar desde cada tarjeta abre el modal con lo que ya está cargado.
  dyAbrirActividad(P, __PENDIENTE__);
  s.editAyer = [_el('dy-act-titulo').textContent, _el('dy-act-texto').value, _el('dy-act-fecha').value,
                _el('dy-act-hora').value];
  dyCerrarModal();
  dyAbrirActividad(P, __HECHA__);
  s.editHecha = [_el('dy-act-titulo').textContent, _el('dy-act-texto').value, _el('dy-act-fecha').value];
  _el('dy-act-fecha').value = '__MIERCOLES__';
  await dyGuardarActividad();
  dyEditarRecordatorio(P, __CON_MATIAS__);
  s.editRec = {titulo: _el('dy-recm-titulo').textContent, texto: _el('dy-recm-texto').value,
               frecuencia: _el('dy-recm-frecuencia').value, lunes: _el('dy-recm-dia-0').checked,
               martes: _el('dy-recm-dia-1').checked, jueves: _el('dy-recm-dia-3').checked,
               hora: _el('dy-recm-hora').value, nota: _el('dy-recm-nota').value};
  _el('dy-recm-hora').value = '12:00';
  await dyGuardarRecordatorio();
  dyEditarRecordatorio(P, __DIARIO__);
  s.editDiario = [_el('dy-recm-texto').value, _el('dy-recm-hora').value, _el('dy-recm-nota').value];
  dyCerrarRecordatorio();
  dyEditarRecordatorio(P, __REC_HECHO__);
  s.editRecHecho = _el('dy-recm-texto').value;
  dyCerrarRecordatorio();

  await dyActividadHecha(P, __TEMPRANO__);
  await dyPasarAManiana(P, __SIN_HORA__);
  await dyPasar(P, __PENDIENTE__);
  await dyRecordatorioHecho(P, __DIARIO__);
  await dyDeshacerActividad(P, __HECHA__);
  await dyDeshacerRecordatorio(P, __REC_HECHO__);

  _el('dy-nueva').value = '   ';
  await dyAgregar(P);
  s.errorVacio = _el('dy-error').textContent;
  _el('dy-nueva').value = 'Llamar a Tito';
  await dyAgregar(P);
  s.nuevaVacia = _el('dy-nueva').value;

  dyAbrirActividad(P);
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

  dyAbrirActividad(P, __TEMPRANO__);
  s.editTitulo = _el('dy-act-titulo').textContent;
  s.editTexto = _el('dy-act-texto').value;
  s.editHora = _el('dy-act-hora').value;
  s.editNota = _el('dy-act-nota').value;
  _el('dy-act-hora').value = '11:00';
  await dyGuardarActividad();
  dyAbrirActividad(P, __SIN_HORA__);
  await dyBorrarDesdeModal();

  dyAbrirRecordatorio(P);
  s.recNuevoTitulo = _el('dy-recm-titulo').textContent;
  _el('dy-recm-texto').value = 'Daily con el equipo';
  _el('dy-recm-frecuencia').value = 'dias';
  await dyGuardarRecordatorio();
  s.errorDias = _el('dy-recm-error').textContent;
  _el('dy-recm-dia-1').checked = true;
  _el('dy-recm-hora').value = '18:00';
  _el('dy-recm-nota').value = 'Antes de irse';
  await dyGuardarRecordatorio();
  await dyPausarRecordatorio(P, __DIARIO__);
  await dyBorrarRecordatorio(P, __PAUSADO__);
  s.pedidos = _pedidos.slice(antes);

  dyMoverDia(P, -1);
  await dyCargarDia(P);
  s.fechaLunes = DY_EST.programador.fecha;
  s.listaLunes = _el('dy-lista').innerHTML;

  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
"""


@sin_node
def test_daily_programador_se_pinta_como_seguimiento_y_todo_se_edita_desde_su_tarjeta(cli, tmp_path):
    juan, gonzalo, matias = _juan(cli), _gonzalo(cli), _matias(cli)
    pendiente = _act(cli, juan, "Terminar landing", LUNES, hora="08:00")
    sin_hora = _act(cli, juan, "Revisar PR")
    temprano = _act(cli, juan, "Deploy <b>Bar Tito</b>", hora="09:30", nota="Pedir acceso al hosting")
    hecha = _act(cli, juan, "Mandar presupuesto")
    cli.patch(f"/api/daily/actividades/{hecha}", json={"hecha": True})
    diario = _rec(cli, juan, "Revisar mails de clientes", hora="10:00", nota="Los urgentes primero")
    con_matias = _rec(cli, juan, "Daily con Matías", "dias", [1, 3])
    rec_hecho = _rec(cli, juan, "Subir backup", "habiles")
    cli.put(f"/api/daily/recordatorios/{rec_hecho}/marca", json={"fecha": MARTES, "hecha": True})
    pausado = _rec(cli, juan, "Llamar al contador", "habiles")
    cli.put(f"/api/daily/recordatorios/{pausado}", json={"activo": False})
    _act(cli, gonzalo, "Reunión con Matías")

    respuestas = {
        "/api/daily/personas?seccion=programador": cli.get("/api/daily/personas?seccion=programador").get_json(),
        "/api/daily/personas?seccion=admin": cli.get("/api/daily/personas?seccion=admin").get_json(),
        _url("programador", juan): _dia(cli, juan),
        _url("programador", juan, MARTES): _dia(cli, juan, MARTES),
        _url("programador", juan, LUNES): _dia(cli, juan, LUNES),
        _url("programador", juan, MIERCOLES): _dia(cli, juan, MIERCOLES),
        _url("programador", gonzalo): _dia(cli, gonzalo),
        _url("programador", gonzalo, MARTES): _dia(cli, gonzalo, MARTES),
        _url("programador", gonzalo, MIERCOLES): _dia(cli, gonzalo, MIERCOLES),
    }
    prueba = _reemplazar(_PRUEBA_PROGRAMADOR, {
        "__JUAN__": juan, "__GONZALO__": gonzalo, "__PENDIENTE__": pendiente, "__SIN_HORA__": sin_hora,
        "__TEMPRANO__": temprano, "__HECHA__": hecha, "__DIARIO__": diario, "__CON_MATIAS__": con_matias,
        "__REC_HECHO__": rec_hecho, "__PAUSADO__": pausado, "__MIERCOLES__": MIERCOLES})
    s = _correr_js(tmp_path, respuestas, prueba)

    # ── menú: Matías, Juan y Gonzalo, cada uno con su ícono a la izquierda y su color ──
    iconos = [f'<i data-lucide="user-round" class="dy-nav-icono dy-color-{i}" aria-hidden="true"></i><span>{n}</span>'
              for i, n in enumerate(("Matías", "Juan", "Gonzalo"))]
    posiciones = [s["menu"].index(i) for i in iconos]
    assert posiciones == sorted(posiciones)
    for persona in (matias, juan, gonzalo):
        assert f'id="dy-nav-persona-{persona}" role="button" tabindex="0" data-seccion="programador" ' \
               f'data-persona="{persona}"' in s["menu"]
    assert 'onclick="dyAbrirPersona(this.dataset.seccion, Number(this.dataset.persona))"' in s["menu"]
    assert "dy-activa" not in s["menu"], "fuera del panel no se marca ninguna"
    assert all(i + "</button>" in s["selector"] for i in iconos), "en el celular, el mismo ícono en el selector"
    assert s["primera"] == matias

    # ── Gonzalo desde el menú: el panel se arma y abre su día ──
    assert s["panel"] == "daily" and s["tituloGonzalo"] == "Daily de Gonzalo"
    for pieza in ('class="page-header sl-cabecera"', 'id="dy-contadores"', 'id="dy-lista"', 'id="dy-recordatorios"',
                  '>Nueva actividad</button>', '>Nuevo recordatorio</button>', 'data-seccion="programador"'):
        assert pieza in s["armado"], pieza
    assert "dya-" not in s["armado"]
    assert f'class="dy-nav-sub dy-activa" id="dy-nav-persona-{gonzalo}"' in s["menuConGonzalo"]
    assert "Reunión con Matías" in s["listaGonzalo"] and "Revisar PR" not in s["listaGonzalo"]
    assert "Nada pendiente para este día." in s["vacioFuturo"]
    assert 'onclick="dyAbrirActividad(this.dataset.seccion)">Agregar actividad</button>' in s["vacioFuturo"]
    assert "sl-contador-vencidos" not in s["contadoresVacio"], "sin nada de ayer, sin rojo"

    # ── encabezado y contadores del día de Juan ──
    assert s["titulo"] == "Daily de Juan" and s["admin"], "Daily Admin no se tocó"
    assert s["resumen"] == "4 pendientes · 2 hechas · 1 de ayer"
    assert s["fecha"] == 'martes 15 de septiembre<span class="dy-fecha-hoy">Hoy</span>'
    contadores = re.findall(r'data-grupo="(\w+)".*?sl-contador-num">(\d+)</span><span class="sl-contador-rot">([^<]+)<',
                            s["contadores"])
    assert contadores == [("ayer", "1", "Pendiente de ayer"), ("hoy", "2", "Hoy"),
                          ("recordatorios", "2", "Recordatorios"), ("hechas", "2", "Hechas")]
    assert 'class="sl-contador sl-contador-vencidos" data-grupo="ayer"' in s["contadores"]

    # ── grupos en orden, con su título ──
    lista = s["lista"]
    orden = [lista.index(f'id="dy-grupo-{g}"') for g in ("ayer", "hoy", "recordatorios", "hechas")]
    assert orden == sorted(orden)
    assert '<h2 class="sl-grupo-titulo sl-grupo-titulo-vencidos">PENDIENTE DE AYER</h2>' in lista
    assert '<h2 class="sl-grupo-titulo">HOY · MARTES 15 DE SEPTIEMBRE</h2>' in lista
    assert '<h2 class="sl-grupo-titulo">RECORDATORIOS DE HOY</h2>' in lista
    assert '<summary class="sl-grupo-titulo">HECHAS (2)</summary>' in lista
    assert 'id="dy-grupo-hechas" data-seccion="programador" ontoggle' in lista, "colapsado por defecto"

    ayer = _grupo(lista, "dy", "ayer")
    assert ayer.count("<article") == 1 and '<article class="sl-tarjeta dy-tarjeta sl-vencida"' in ayer
    assert 'class="sl-cuando sl-cuando-vencido">08:00' in ayer
    assert _botones(ayer) == ["Pasar a hoy", "Hecho", "Editar"]

    hoy = _grupo(lista, "dy", "hoy")
    assert hoy.count("<article") == 2 and "sl-vencida" not in hoy
    assert hoy.index("Deploy") < hoy.index("Revisar PR")
    assert "&lt;b&gt;Bar Tito&lt;/b&gt;" in hoy and "<b>" not in hoy
    assert '<div class="sl-cuando">09:30</div>' in hoy and '<div class="sl-nota">Pedir acceso al hosting</div>' in hoy
    assert _botones(hoy) == ["Hecho", "Pasar a mañana", "Editar"] * 2

    recs = _grupo(lista, "dy", "recordatorios")
    assert recs.count('<article class="sl-tarjeta dy-tarjeta dy-borde-1"') == 2, "el color de Juan"
    assert recs.index("Revisar mails de clientes") < recs.index("Daily con Matías")
    assert '<span class="dy-etiqueta">Todos los días</span>' in recs and '<span class="dy-etiqueta">Mar y Jue</span>' in recs
    assert _botones(recs) == ["Hecho", "Editar"] * 2
    assert "Llamar al contador" not in lista, "pausado no aparece en el día"

    hechas = _grupo(lista, "dy", "hechas")
    assert hechas.count('class="sl-linea dy-linea-hecha"') == 2
    assert _botones(hechas) == ["Editar", "Deshacer"] * 2, "las hechas también se editan"
    assert f'data-id="{hecha}" onclick="dyAbrirActividad(this.dataset.seccion, Number(this.dataset.id))">Editar' in hechas
    assert f'data-id="{rec_hecho}" onclick="dyEditarRecordatorio(this.dataset.seccion, Number(this.dataset.id))">Editar' in hechas
    assert '<div class="dy-tachado">Mandar presupuesto</div>' in hechas
    assert "Llamar al contador" in s["recordatorios"] and "Pausado" in s["recordatorios"]
    assert "Todos los días · 10:00" in s["recordatorios"]

    # ── Editar abre el modal con los valores cargados ──
    assert s["editAyer"] == ["Editar actividad", "Terminar landing", LUNES, "08:00"]
    assert s["editHecha"] == ["Editar actividad", "Mandar presupuesto", MARTES]
    assert s["editRec"] == {"titulo": "Editar recordatorio", "texto": "Daily con Matías", "frecuencia": "dias",
                            "lunes": False, "martes": True, "jueves": True, "hora": "", "nota": ""}
    assert s["editDiario"] == ["Revisar mails de clientes", "10:00", "Los urgentes primero"]
    assert s["editRecHecho"] == "Subir backup"

    # ── lo que manda cada botón ──
    p = s["pedidos"]
    assert _hubo(p, f"/api/daily/actividades/{hecha}", "PATCH",
                 {"texto": "Mandar presupuesto", "fecha": MIERCOLES, "hora": "", "nota": ""}), "editar la fecha"
    assert _hubo(p, f"/api/daily/recordatorios/{con_matias}", "PUT",
                 {"texto": "Daily con Matías", "frecuencia": "dias", "dias": [1, 3], "hora": "12:00", "nota": ""})
    assert _hubo(p, f"/api/daily/actividades/{temprano}", "PATCH", {"hecha": True}), "Hecho"
    assert _hubo(p, f"/api/daily/actividades/{sin_hora}/pasar", "POST", {"fecha": MIERCOLES}), "Pasar a mañana"
    assert _hubo(p, f"/api/daily/actividades/{pendiente}/pasar", "POST", {"fecha": MARTES}), "Pasar a hoy"
    assert _hubo(p, f"/api/daily/recordatorios/{diario}/marca", "PUT", {"fecha": MARTES, "hecha": True})
    assert _hubo(p, f"/api/daily/actividades/{hecha}", "PATCH", {"hecha": False}), "Deshacer"
    assert _hubo(p, f"/api/daily/recordatorios/{rec_hecho}/marca", "PUT", {"fecha": MARTES, "hecha": False})
    assert s["errorVacio"] and s["nuevaVacia"] == ""
    assert _hubo(p, "/api/daily/actividades", "POST",
                 {"seccion": "programador", "persona_id": juan, "fecha": MARTES, "texto": "Llamar a Tito"})
    assert len([x for x in p if x[0] == "/api/daily/actividades" and x[1] == "POST"]) == 2, \
        "el vacío y el modal sin texto no mandan nada"
    assert s["modalTitulo"] == "Nueva actividad" and s["modalContexto"] == "Daily de Juan"
    assert s["modalFecha"] == MARTES and "Escribí qué hay que hacer" in s["modalError"]
    assert _hubo(p, "/api/daily/actividades", "POST", {"seccion": "programador", "persona_id": juan,
                                                       "texto": "Preparar demo", "fecha": MARTES, "hora": "16:00",
                                                       "nota": "Con datos reales"})
    assert s["editTitulo"] == "Editar actividad" and s["editTexto"] == "Deploy <b>Bar Tito</b>"
    assert s["editHora"] == "09:30" and s["editNota"] == "Pedir acceso al hosting"
    assert _hubo(p, f"/api/daily/actividades/{temprano}", "PATCH", {"texto": "Deploy <b>Bar Tito</b>", "fecha": MARTES,
                                                                    "hora": "11:00", "nota": "Pedir acceso al hosting"})
    assert _hubo(p, f"/api/daily/actividades/{sin_hora}", "DELETE")
    assert s["recNuevoTitulo"] == "Nuevo recordatorio" and "al menos un día" in s["errorDias"]
    assert _hubo(p, "/api/daily/recordatorios", "POST", {"seccion": "programador", "persona_id": juan,
                                                         "texto": "Daily con el equipo", "frecuencia": "dias",
                                                         "dias": [1], "hora": "18:00", "nota": "Antes de irse"})
    assert len([x for x in p if x[0] == "/api/daily/recordatorios" and x[1] == "POST"]) == 1
    assert _hubo(p, f"/api/daily/recordatorios/{diario}", "PUT", {"activo": False}), "Pausar"
    assert _hubo(p, f"/api/daily/recordatorios/{pausado}", "DELETE")

    # ── un día pasado ──
    assert s["fechaLunes"] == LUNES
    assert "Terminar landing" in s["listaLunes"] and "dy-grupo-ayer" not in s["listaLunes"]
    assert '<h2 class="sl-grupo-titulo">LUNES 14 DE SEPTIEMBRE</h2>' in s["listaLunes"]


_PRUEBA_ADMIN = """
(async () => {
  const s = {};
  const A = 'admin';
  await dyCargarPersonas(A);
  s.menu = _el('dya-nav-personas').innerHTML;
  s.selector = _el('dya-personas').innerHTML;
  s.primera = DY_EST.admin.personaId;

  dyAbrirPersona(A, __JAVIER__);
  s.panel = activePanel;
  await loadDaily(A);
  s.armado = _el('daily_admin-panel').innerHTML;
  s.titulo = _el('dya-titulo').textContent;
  s.resumen = _el('dya-resumen').textContent;
  s.lista = _el('dya-lista').innerHTML;
  s.menuActivo = _el('dya-nav-personas').innerHTML;
  s.programadorSinTocar = DY_EST.programador.datos === null && _el('dy-lista').innerHTML === '';

  const antes = _pedidos.length;
  _el('dya-nueva').value = 'Firmar el contrato';
  await dyAgregar(A);
  dyAbrirActividad(A, __CONTRATO__);
  s.modal = [_el('dy-act-titulo').textContent, _el('dy-act-texto').value, _el('dy-act-hora').value,
             _el('dy-act-contexto').textContent];
  _el('dy-act-fecha').value = '__MIERCOLES__';
  await dyGuardarActividad();
  dyEditarRecordatorio(A, __CAJA__);
  s.modalRec = [_el('dy-recm-titulo').textContent, _el('dy-recm-texto').value, _el('dy-recm-nota').value];
  dyCerrarRecordatorio();
  dyAbrirRecordatorio(A);
  _el('dy-recm-texto').value = 'Pagar sueldos';
  await dyGuardarRecordatorio();
  s.pedidos = _pedidos.slice(antes);

  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
"""


@sin_node
def test_daily_admin_es_la_misma_pantalla_para_juanchi_y_javier(cli, tmp_path):
    juanchi, javier, juan = _juanchi(cli), _javier(cli), _juan(cli)
    contrato = _act(cli, javier, "Revisar contrato de Tito", seccion="admin", hora="11:00")
    caja = _rec(cli, javier, "Mirar la caja", seccion="admin", nota="Antes del mediodía")
    _act(cli, juan, "Revisar PR")  # de Daily Programador: no tiene que aparecer

    respuestas = {
        "/api/daily/personas?seccion=programador": cli.get("/api/daily/personas?seccion=programador").get_json(),
        "/api/daily/personas?seccion=admin": cli.get("/api/daily/personas?seccion=admin").get_json(),
        _url("admin", javier): _dia(cli, javier, seccion="admin"),
        _url("admin", javier, MARTES): _dia(cli, javier, MARTES, "admin"),
    }
    prueba = _reemplazar(_PRUEBA_ADMIN, {"__JAVIER__": javier, "__CONTRATO__": contrato, "__CAJA__": caja,
                                         "__MIERCOLES__": MIERCOLES})
    s = _correr_js(tmp_path, respuestas, prueba)

    juanchi_icono = '<i data-lucide="user-round" class="dy-nav-icono dy-color-0" aria-hidden="true"></i><span>Juanchi</span>'
    javier_icono = '<i data-lucide="user-round" class="dy-nav-icono dy-color-1" aria-hidden="true"></i><span>Javier</span>'
    assert s["menu"].index(juanchi_icono) < s["menu"].index(javier_icono)
    assert f'id="dya-nav-persona-{juanchi}" role="button" tabindex="0" data-seccion="admin"' in s["menu"]
    assert juanchi_icono + "</button>" in s["selector"] and javier_icono + "</button>" in s["selector"]
    assert s["primera"] == juanchi

    assert s["panel"] == "daily_admin" and s["titulo"] == "Daily de Javier"
    for pieza in ('class="page-header sl-cabecera"', 'id="dya-contadores"', 'id="dya-lista"',
                  'id="dya-recordatorios"', 'data-seccion="admin"', '>Daily Admin</h1>'):
        assert pieza in s["armado"], pieza
    assert 'id="dy-lista"' not in s["armado"]
    assert f'class="dy-nav-sub dy-activa" id="dya-nav-persona-{javier}"' in s["menuActivo"]
    assert s["resumen"] == "2 pendientes · 0 hechas"
    assert "Revisar contrato de Tito" in s["lista"] and "Revisar PR" not in s["lista"]
    assert 'id="dya-grupo-hoy"' in s["lista"] and 'data-seccion="admin"' in s["lista"]
    assert _botones(_grupo(s["lista"], "dya", "hoy")) == ["Hecho", "Pasar a mañana", "Editar"]
    assert s["programadorSinTocar"]

    assert s["modal"] == ["Editar actividad", "Revisar contrato de Tito", "11:00", "Daily de Javier"]
    assert s["modalRec"] == ["Editar recordatorio", "Mirar la caja", "Antes del mediodía"]
    p = s["pedidos"]
    assert _hubo(p, "/api/daily/actividades", "POST",
                 {"seccion": "admin", "persona_id": javier, "fecha": MARTES, "texto": "Firmar el contrato"})
    assert _hubo(p, f"/api/daily/actividades/{contrato}", "PATCH",
                 {"texto": "Revisar contrato de Tito", "fecha": MIERCOLES, "hora": "11:00", "nota": ""})
    assert _hubo(p, "/api/daily/recordatorios", "POST", {"seccion": "admin", "persona_id": javier,
                                                         "texto": "Pagar sueldos", "frecuencia": "diario",
                                                         "dias": [], "hora": "", "nota": ""})
