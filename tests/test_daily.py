"""Daily Programador: el día de cada programador.

Pedido de Juan (15/9): una sección "Daily Programador" donde se puedan poner
actividades y recordatorios de lo que hay que hacer cada día, para no
olvidarse. Después lo ajustó: en el menú, debajo del ítem, una opción por
persona (Juan y Gonzalo), y cualquiera con el panel entra a cualquiera. Las
personas salen de Recursos Humanos: `equipo_personas.programador`.

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
from services.daily import hoy_montevideo, le_toca

HTML = dashboard.DASHBOARD_HTML
RAIZ = Path(__file__).resolve().parents[1]
SRC = (RAIZ / "dashboard.py").read_text(encoding="utf-8")

sin_node = pytest.mark.skipif(shutil.which("node") is None,
                              reason="node no esta instalado")

AHORA = datetime(2026, 9, 15, 13, 0, tzinfo=timezone.utc)  # martes 10:00 en Montevideo
LUNES, MARTES, MIERCOLES = "2026-09-14", "2026-09-15", "2026-09-16"
SABADO, DOMINGO, PROX_LUNES = "2026-09-19", "2026-09-20", "2026-09-21"


def _entre(texto: str, desde: str, hasta: str) -> str:
    i = texto.index(desde)
    return texto[i:texto.index(hasta, i + len(desde))]


PANEL = _entre(SRC, "<!-- ======= DAILY PROGRAMADOR PANEL ======= -->",
               "<!-- ======= RECURSOS HUMANOS PANELES ======= -->")
JS = _entre(SRC, "// ========== Daily Programador ==========", "// ========== Equipo ==========")
# Termina donde empieza Seguimiento de leads, que va pegado abajo.
CSS = _entre(SRC, "/* ── Daily Programador", "/* ── Seguimiento de leads")
FUENTES = {"panel": PANEL, "js": JS, "css": CSS}


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


def _act(c, persona_id, texto, fecha=MARTES):
    r = c.post("/api/daily/actividades", json={"persona_id": persona_id, "texto": texto, "fecha": fecha})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _rec(c, persona_id, texto, frecuencia="diario", dias=None):
    datos = {"persona_id": persona_id, "texto": texto, "frecuencia": frecuencia}
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
        "pasada_de": None, "creada_por": "test"}]
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
])
def test_la_actividad_se_valida_en_el_servidor(cli, datos, parte):
    r = cli.post("/api/daily/actividades", json={"persona_id": _juan(cli), **datos})
    assert r.status_code == 400 and parte in r.get_json()["error"], r.get_json()


def test_editar_o_pasar_una_actividad_se_valida(cli):
    aid = _act(cli, _juan(cli), "Algo")
    for cuerpo in ({}, {"hecha": "si"}, {"hecha": 1}, {"texto": ""}):
        assert cli.patch(f"/api/daily/actividades/{aid}", json=cuerpo).status_code == 400, cuerpo
    assert cli.post(f"/api/daily/actividades/{aid}/pasar", json={"fecha": "mañana"}).status_code == 400


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
        # Una base de antes de Daily: el panel todavía no se repartió nunca.
        conn.execute("DELETE FROM panel_grants_aplicados WHERE panel='daily'")
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
    assert [r["cuando"] for r in todos] == ["Todos los días", "Días hábiles (lunes a viernes)", "Lun, Mié"]
    assert todos[2]["dias"] == [0, 2] and todos[0]["dias"] == []


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
])
def test_el_recordatorio_se_valida_en_el_servidor(cli, datos, parte):
    r = cli.post("/api/daily/recordatorios", json={"persona_id": _juan(cli), **datos})
    assert r.status_code == 400 and parte in r.get_json()["error"], r.get_json()


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


def test_la_pagina_abre_con_el_panel(cli):
    r = cli.get("/")
    assert r.status_code == 200
    pagina = r.get_data(as_text=True)
    assert pagina.count('id="daily-panel"') == 1 and pagina.count('id="nav-daily"') == 1
    assert pagina.count('id="dy-nav-personas"') == 1


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
    assert "style=" not in PANEL and "style=" not in JS
    assert "setInterval" not in JS and "showPanel" not in re.search(
        r"// Initial load.*?\n([^/\s].*?)\n", HTML, re.S).group(1)


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
  _pedidos.push([String(url), (opciones && opciones.method) || 'GET']);
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
  s.actividadesGonzalo = _el('dy-actividades').innerHTML;
  s.menuConGonzalo = _el('dy-nav-personas').innerHTML;
  s.pidioGonzalo = _pedidos.some(p => p[0].indexOf('/api/daily?persona_id=__GONZALO__') === 0);

  dyElegirPersona(__JUAN__);
  await dyCargarDia();
  s.fecha = _el('dy-fecha').innerHTML;
  s.actividades = _el('dy-actividades').innerHTML;
  s.pendientes = _el('dy-pendientes').innerHTML;
  s.recordatoriosHoy = _el('dy-recordatorios-hoy').innerHTML;
  s.recordatorios = _el('dy-recordatorios').innerHTML;

  const antes = _pedidos.length;
  _el('dy-nueva').value = '   ';
  await dyAgregar();
  s.errorVacio = _el('dy-error').textContent;
  s.postsVacio = _pedidos.slice(antes).filter(p => p[1] === 'POST').length;
  _el('dy-nueva').value = 'Llamar a Tito';
  await dyAgregar();
  s.postsAgregar = _pedidos.slice(antes).filter(p => p[0] === '/api/daily/actividades' && p[1] === 'POST').length;
  s.nuevaVacia = _el('dy-nueva').value;

  await dyPasar(__PENDIENTE__);
  s.pasar = _pedidos.some(p => p[0] === '/api/daily/actividades/__PENDIENTE__/pasar' && p[1] === 'POST');
  await dyMarcarRecordatorio(__DIARIO__, true);
  s.marca = _pedidos.some(p => p[0] === '/api/daily/recordatorios/__DIARIO__/marca' && p[1] === 'PUT');
  await dyMarcarActividad(__HECHA__, false);
  s.patch = _pedidos.some(p => p[0] === '/api/daily/actividades/__HECHA__' && p[1] === 'PATCH');

  _el('dy-rec-texto').value = 'Subir backup';
  _el('dy-rec-frecuencia').value = 'dias';
  const antesRec = _pedidos.length;
  await dyGuardarRecordatorio();
  s.errorDias = _el('dy-rec-error').textContent;
  s.postsRecSinDias = _pedidos.slice(antesRec).filter(p => p[1] === 'POST').length;
  _el('dy-rec-dia-0').checked = true;
  _el('dy-rec-dia-3').checked = true;
  await dyGuardarRecordatorio();
  s.postRec = _pedidos.some(p => p[0] === '/api/daily/recordatorios' && p[1] === 'POST');
  s.textoTrasCrear = _el('dy-rec-texto').value;

  dyEditarRecordatorio(__DIARIO__);
  s.editTexto = _el('dy-rec-texto').value;
  s.botonEdit = _el('dy-rec-guardar').textContent;
  await dyGuardarRecordatorio();
  s.put = _pedidos.some(p => p[0] === '/api/daily/recordatorios/__DIARIO__' && p[1] === 'PUT');
  s.botonTras = _el('dy-rec-guardar').textContent;

  await dyPausarRecordatorio(__DIARIO__);
  s.puts = _pedidos.filter(p => p[0] === '/api/daily/recordatorios/__DIARIO__' && p[1] === 'PUT').length;
  await dyBorrarRecordatorio(__PAUSADO__);
  s.borrar = _pedidos.some(p => p[0] === '/api/daily/recordatorios/__PAUSADO__' && p[1] === 'DELETE');
  await dyBorrarActividad(__HECHA__);
  s.borrarAct = _pedidos.some(p => p[0] === '/api/daily/actividades/__HECHA__' && p[1] === 'DELETE');

  dyMoverDia(1);
  await dyCargarDia();
  s.fechaSiguiente = dyFecha;
  s.fechaSiguienteTexto = _el('dy-fecha').innerHTML;
  s.pendientesManiana = _el('dy-pendientes').innerHTML;
  dyIrHoy();
  await dyCargarDia();
  s.fechaHoy = dyFecha;

  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
"""


@sin_node
def test_la_pantalla_se_pinta_y_cada_persona_del_menu_abre_su_dia(cli, tmp_path):
    juan, gonzalo = _juan(cli), _gonzalo(cli)
    pendiente = _act(cli, juan, "Terminar landing", LUNES)
    hecha = _act(cli, juan, "Deploy <b>Bar Tito</b>")
    cli.patch(f"/api/daily/actividades/{hecha}", json={"hecha": True})
    _act(cli, juan, "Revisar PR")
    diario = _rec(cli, juan, "Revisar mails de clientes")
    pausado = _rec(cli, juan, "Subir backup", "habiles")
    cli.put(f"/api/daily/recordatorios/{pausado}", json={"activo": False})
    _act(cli, gonzalo, "Reunión con Matías")

    respuestas = {
        "/api/daily/personas": cli.get("/api/daily/personas").get_json(),
        f"/api/daily?persona_id={juan}": _dia(cli, juan),
        f"/api/daily?persona_id={juan}&fecha={MARTES}": _dia(cli, juan, MARTES),
        f"/api/daily?persona_id={juan}&fecha={MIERCOLES}": _dia(cli, juan, MIERCOLES),
        f"/api/daily?persona_id={gonzalo}": _dia(cli, gonzalo),
        f"/api/daily?persona_id={gonzalo}&fecha={MARTES}": _dia(cli, gonzalo, MARTES),
    }
    prueba = _PRUEBA
    for clave, valor in {"__JUAN__": juan, "__GONZALO__": gonzalo, "__PENDIENTE__": pendiente,
                         "__HECHA__": hecha, "__DIARIO__": diario, "__PAUSADO__": pausado}.items():
        prueba = prueba.replace(clave, str(valor))
    s = _correr_js(tmp_path, respuestas, prueba)

    # Las dos opciones del menú, en orden, y cada una abre su día.
    assert f"dyAbrirPersona({juan})" in s["menu"] and f"dyAbrirPersona({gonzalo})" in s["menu"]
    assert s["menu"].index(">Juan</div>") < s["menu"].index(">Gonzalo</div>")
    assert "dy-activa" not in s["menu"], "fuera del panel no se marca ninguna"
    assert 'aria-pressed="true"' in s["selector"] and ">Juan</button>" in s["selector"]
    assert ">Gonzalo</button>" in s["selector"], "en el celular se elige arriba del panel"
    assert s["primera"] == juan
    assert s["panel"] == "daily" and s["pidioGonzalo"]
    assert "Reunión con Matías" in s["actividadesGonzalo"] and "Revisar PR" not in s["actividadesGonzalo"]
    assert f'class="dy-nav-sub dy-activa" id="dy-nav-persona-{gonzalo}"' in s["menuConGonzalo"]

    # El día de Juan.
    assert s["fecha"] == 'martes 15 de septiembre<span class="dy-fecha-hoy">Hoy</span>'
    assert "Revisar PR" in s["actividades"]
    assert "&lt;b&gt;Bar Tito&lt;/b&gt;" in s["actividades"] and "<b>" not in s["actividades"]
    assert s["actividades"].count("dy-hecha") == 1 and s["actividades"].count(" checked") == 1
    assert "Pendiente de ayer" in s["pendientes"] and "Terminar landing" in s["pendientes"]
    assert "Pasar a hoy" in s["pendientes"] and f"dyPasar({pendiente})" in s["pendientes"]
    assert "Revisar mails de clientes" in s["recordatoriosHoy"] and "Todos los días" in s["recordatoriosHoy"]
    assert "Subir backup" not in s["recordatoriosHoy"], "pausado no aparece en el día"
    assert "Pausado" in s["recordatorios"] and "Subir backup" in s["recordatorios"]
    assert f"dyBorrarRecordatorio({pausado})" in s["recordatorios"]

    # Lo que manda cada botón.
    assert s["errorVacio"] and s["postsVacio"] == 0
    assert s["postsAgregar"] == 1 and s["nuevaVacia"] == ""
    assert s["pasar"] and s["marca"] and s["patch"]
    assert "al menos un día" in s["errorDias"] and s["postsRecSinDias"] == 0
    assert s["postRec"] and s["textoTrasCrear"] == ""
    assert s["editTexto"] == "Revisar mails de clientes" and s["botonEdit"] == "Guardar cambios"
    assert s["put"] and s["botonTras"] == "Crear recordatorio"
    assert s["puts"] == 2, "editar y pausar"
    assert s["borrar"] and s["borrarAct"]

    # Las flechas y "Hoy".
    assert s["fechaSiguiente"] == MIERCOLES and s["fechaSiguienteTexto"] == "miércoles 16 de septiembre"
    assert "Revisar PR" in s["pendientesManiana"] and "Pasar a este día" in s["pendientesManiana"]
    assert s["fechaHoy"] == MARTES
