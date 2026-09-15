"""La sección Equipo: organigrama desde `reporta_a` y ausencias con recupero.

Criterios de aceptación de Juan (14/9), cada uno con su test `test_cN_...`:
1 una sola pantalla con las dos partes · 2 el organigrama refleja reporta_a ·
3 ausencia de dos días de Gonzalo -> saldo −8h · 4 dos recuperos de 4h -> al
día · 5 aviso ámbar si pisa una entrega · 6 Matías, Andrés y Guillermo en el
organigrama y no en las ausencias · 7 ningún monto, sueldo ni descuento.

El día está fijo en el lunes 14/9/2026 para que las semanas no se muevan.
"""

import json
import re
import shutil
import sqlite3
import subprocess
from datetime import date
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import dashboard
import database
import routes.equipo as rutas_equipo
from database import create_user, init_db, listar_personas_equipo, upsert_project
from services.equipo import dias_habiles, organigrama

HTML = dashboard.DASHBOARD_HTML
RAIZ = Path(__file__).resolve().parents[1]
SRC = (RAIZ / "dashboard.py").read_text(encoding="utf-8")

sin_node = pytest.mark.skipif(shutil.which("node") is None,
                              reason="node no esta instalado")

HOY = date(2026, 9, 14)  # lunes
LUNES, MARTES, MIERCOLES = "2026-09-14", "2026-09-15", "2026-09-16"
PROX_LUNES = "2026-09-21"


def _entre(texto: str, desde: str, hasta: str) -> str:
    i = texto.index(desde)
    return texto[i:texto.index(hasta, i + len(desde))]


PANEL = _entre(SRC, "<!-- ======= RECURSOS HUMANOS PANELES ======= -->",
               "<!-- ======= FIN RECURSOS HUMANOS PANELES ======= -->")
ORGANIGRAMA = _entre(PANEL, '<div id="equipo-panel" class="panel">', '<div id="ausencias-panel"')
AUSENCIAS = PANEL[PANEL.index('<div id="ausencias-panel" class="panel">'):]
MODALES = _entre(SRC, "<!-- ======= EQUIPO MODALES ======= -->", "<!-- ======= FIN EQUIPO MODALES ======= -->")
JS = _entre(SRC, "// ========== Equipo ==========", "// ========== Simulador financiero ==========")
CSS = _entre(SRC, "/* ── Equipo", "/* ── Simulador financiero")
FUENTES = {"panel": PANEL, "modales": MODALES, "js": JS, "css": CSS}

# Lo que Juan pidió que no aparezca, y parientes cercanos.
PROHIBIDAS = ("sueldo", "monto", "descuento", "usd", "$", "salario", "liquidaci", "pago")


def _sin_dinero(texto: str) -> list[str]:
    bajo = texto.lower()
    return [p for p in PROHIBIDAS if p in bajo]


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(rutas_equipo, "_hoy", lambda: HOY)
    db = str(tmp_path / "equipo.db")
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
        conn = sqlite3.connect(db)
        conn.execute("UPDATE users SET role_id=? WHERE id=?", (role_id, uid))
        conn.commit()
        conn.close()
    return uid


def _cli(app, uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "test"
    return c


@pytest.fixture
def cli(app):
    return _cli(app, _usuario(app.config["_DB"], "jefe@scalerics.com"))


def _db(cli_o_app):
    app = cli_o_app.application if hasattr(cli_o_app, "application") else cli_o_app
    return app.config["_DB"]


def _persona(db, nombre):
    return next(p for p in listar_personas_equipo(db) if p["nombre"] == nombre)


def _estado(cli):
    r = cli.get("/api/equipo")
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def _fila(estado, nombre):
    return next(f for f in estado["calendario"] if f["nombre"] == nombre)


def _ausencia(cli, nombre="Gonzalo Siuciak", desde=LUNES, hasta=MARTES, **extra):
    datos = {"persona_id": _persona(_db(cli), nombre)["id"], "fecha_desde": desde,
             "fecha_hasta": hasta, "motivo": "Trámite", **extra}
    return cli.post("/api/equipo/ausencias", json=datos)


def _set(db, sql, params):
    conn = sqlite3.connect(db)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


# ── criterio 7: sin dinero ───────────────────────────────────────────────────

def test_c7_ninguna_tabla_tiene_campos_de_dinero(tmp_path):
    db = str(tmp_path / "e.db")
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        for tabla in ("equipo_personas", "equipo_ausencias", "equipo_recuperos"):
            columnas = [c[1] for c in conn.execute(f"PRAGMA table_info({tabla})")]
            assert columnas, f"no existe {tabla}"
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (tabla,)).fetchone()[0]
            assert not _sin_dinero(" ".join(columnas) + sql), (tabla, columnas)
    finally:
        conn.close()


@pytest.mark.parametrize("nombre", sorted(FUENTES))
def test_c7_ni_la_pantalla_ni_su_codigo_hablan_de_dinero(nombre):
    assert not _sin_dinero(FUENTES[nombre]), f"dinero en el {nombre} de Equipo"


def test_c7_lo_que_devuelve_la_api_no_trae_dinero(cli):
    aid = _ausencia(cli).get_json()["id"]
    cli.post(f"/api/equipo/ausencias/{aid}/recuperos", json={"fecha": MIERCOLES, "horas": 4})
    texto = json.dumps(_estado(cli), ensure_ascii=False) + json.dumps(
        cli.get("/api/equipo/capacidad").get_json(), ensure_ascii=False)
    assert not _sin_dinero(texto)


# ── precarga ─────────────────────────────────────────────────────────────────

ESPERADO = {
    "Juan Pereyra": ("Comercial y administración", None, False),
    "Javier Tomasetti": ("Legal", None, False),
    "Andrés Rosi": ("Marketing digital", "Juan Pereyra", False),
    "Matías Domínguez": ("CTO", "Juan Pereyra", False),
    "Guillermo Paredes": ("Contador", "Juan Pereyra", False),
    "Juan Tomasetti": ("Programador", "Matías Domínguez", True),
    "Gonzalo Siuciak": ("Programador · project manager", "Matías Domínguez", True),
}


def test_la_precarga_trae_las_siete_personas_y_no_duplica(tmp_path):
    db = str(tmp_path / "e.db")
    init_db(db)
    init_db(db)
    init_db(db)
    personas = listar_personas_equipo(db, incluir_inactivas=True)
    assert len(personas) == 7
    por_id = {p["id"]: p["nombre"] for p in personas}
    for p in personas:
        rol, jefe, lleva = ESPERADO[p["nombre"]]
        assert p["rol"] == rol
        assert por_id.get(p["reporta_a"]) == jefe, p["nombre"]
        assert bool(p["lleva_horas"]) is lleva
        assert p["horas_por_dia"] == 4 and p["activo"] == 1


def test_la_precarga_no_pisa_un_cambio_hecho_a_mano(tmp_path):
    db = str(tmp_path / "e.db")
    init_db(db)
    javier = _persona(db, "Javier Tomasetti")["id"]
    _set(db, "UPDATE equipo_personas SET reporta_a=? WHERE nombre='Gonzalo Siuciak'", (javier,))
    init_db(db)
    assert _persona(db, "Gonzalo Siuciak")["reporta_a"] == javier


def test_los_roles_existentes_reciben_el_panel(tmp_path):
    """Como Marketing: acá no hay plata, así que no aplica el Ruling R20."""
    db = str(tmp_path / "e.db")
    init_db(db)
    conn = sqlite3.connect(db)
    filas = conn.execute("SELECT name, panel_access FROM roles").fetchall()
    conn.close()
    assert filas
    for nombre, acceso in filas:
        assert "equipo" in json.loads(acceso), nombre
        assert "ausencias" in json.loads(acceso), nombre


def test_la_migracion_le_da_ausencias_a_quien_tenia_equipo(tmp_path):
    """Recursos Humanos partió Equipo en Organigrama (`equipo`) y Ausencias
    (`ausencias`): quien veía Equipo veía las dos partes y las sigue viendo."""
    db = str(tmp_path / "e.db")
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE roles SET panel_access=? WHERE name='Caller'", (json.dumps(["cola", "equipo"]),))
        conn.execute("UPDATE roles SET panel_access=? WHERE name='Ventas'", (json.dumps(["meta"]),))
        conn.execute("UPDATE roles SET panel_access=? WHERE name='Admin'",
                     (json.dumps(["equipo", "ausencias", "cola"]),))
        conn.commit()
        assert database._grant_panel_to_existing_roles(conn, "ausencias", solo_si_tiene="equipo") == 1
        acceso = {n: json.loads(p) for n, p in conn.execute("SELECT name, panel_access FROM roles")}
        assert acceso["Caller"] == ["cola", "equipo", "ausencias"]
        assert acceso["Ventas"] == ["meta"], "sin Equipo no recibe Ausencias"
        assert acceso["Admin"] == ["equipo", "ausencias", "cola"], "no se duplica"
        assert database._grant_panel_to_existing_roles(conn, "ausencias", solo_si_tiene="equipo") == 0
    finally:
        conn.close()

    # En el arranque real: un rol de producción con Equipo y sin Ausencias.
    _set(db, "UPDATE roles SET panel_access=? WHERE name='Caller'", (json.dumps(["wa", "equipo"]),))
    init_db(db)
    conn = sqlite3.connect(db)
    caller = json.loads(conn.execute("SELECT panel_access FROM roles WHERE name='Caller'").fetchone()[0])
    conn.close()
    assert caller[:2] == ["wa", "equipo"] and caller.count("ausencias") == 1


# ── criterio 1 (revisado): Recursos Humanos, dos pantallas ───────────────────

def test_c1_recursos_humanos_son_dos_pantallas(cli):
    """Pedido de Juan: la sección se llama Recursos Humanos, no Equipo, y el
    organigrama y las ausencias van en dos partes separadas."""
    r = cli.get("/")
    assert r.status_code == 200
    pagina = r.get_data(as_text=True)
    for panel in ("equipo", "ausencias"):
        assert pagina.count(f'id="{panel}-panel"') == 1
        assert pagina.count(f'id="nav-{panel}"') == 1
    assert "if (name === 'equipo' || name === 'ausencias') loadEquipo();" in pagina
    assert PANEL.count('class="panel"') == 2

    assert "<h1>Organigrama</h1>" in ORGANIGRAMA and 'id="eq-organigrama"' in ORGANIGRAMA
    for id_ in ("eq-avisos", "eq-calendario", "eq-detalle", "eq-cal-rango"):
        assert f'id="{id_}"' not in ORGANIGRAMA, id_
        assert f'id="{id_}"' in AUSENCIAS, id_
    assert "<h1>Ausencias</h1>" in AUSENCIAS and 'id="eq-organigrama"' not in AUSENCIAS
    orden = [AUSENCIAS.index(i) for i in ('id="eq-avisos"', 'id="eq-calendario"', 'id="eq-detalle"')]
    assert orden == sorted(orden)
    assert "eqAbrirAusencia()\">Registrar</button>" in AUSENCIAS
    assert "eqSemanas(-1)" in AUSENCIAS and "eqSemanasHoy()" in AUSENCIAS
    # Ningún texto visible de estos paneles dice Equipo.
    visible = re.sub(r"<!--.*?-->|<[^>]+>", " ", PANEL, flags=re.S)
    assert "Equipo" not in visible
    assert "Recursos Humanos" in visible


def test_el_menu_tiene_recursos_humanos_con_organigrama_y_ausencias():
    menu = HTML[HTML.index('<div class="nav-scroll">'):HTML.index('<div class="sidebar-bottom">')]
    texto_del_menu = re.sub(r"<!--.*?-->|<[^>]+>", " ", menu, flags=re.S)
    assert "equipo" not in texto_del_menu.lower(), "la sección se llama Recursos Humanos"
    grupo = _entre(menu, '<div class="nav-section-label">RECURSOS HUMANOS</div>',
                   '<div class="nav-section-label">CAPTACIÓN</div>')
    items = re.findall(r'id="nav-(\w+)"[^>]*><i data-lucide="([\w-]+)" class="nav-icon"></i> ([^<]+)</div>', grupo)
    assert items == [("equipo", "network", "Organigrama"), ("ausencias", "calendar-clock", "Ausencias"),
                     ("horarios", "clock-4", "Horarios")]
    operacion = _entre(menu, '<div class="nav-section-label">OPERACIÓN</div>',
                       '<div class="nav-section-label">RECURSOS HUMANOS</div>')
    assert re.findall(r'id="nav-(\w+)"', operacion) == ["clientes", "projects", "tasks", "activity"]


def test_esta_registrado_en_todos_lados():
    prioridad = re.findall(r"'(\w+)'", re.search(r"const NAV_PRIORITY = \[([^\]]*)\]", HTML).group(1))
    assert prioridad.index("equipo") == prioridad.index("activity") + 1
    assert prioridad.index("ausencias") == prioridad.index("equipo") + 1
    iconos = re.search(r"const NAV_ICONS = \{(.*?)\n\}", HTML, re.S).group(1)
    assert "equipo:'network'" in iconos and "ausencias:'calendar-clock'" in iconos
    etiquetas = re.search(r"const NAV_LABELS = \{(.*?)\n\}", HTML, re.S).group(1)
    assert "equipo:'Organigrama'" in etiquetas and "ausencias:'Ausencias'" in etiquetas
    listas = re.findall(r"const ALL_PANELS = \[([^\]]*)\]", SRC)
    assert len(listas) == 2 and all("'equipo'" in l and "'ausencias'" in l for l in listas)
    rotulos = re.search(r"const PANEL_LABELS = \{([^}]*)\}", SRC).group(1)
    assert "equipo:'Organigrama'" in rotulos and "ausencias:'Ausencias'" in rotulos
    assert "from routes.equipo import equipo_bp" in SRC
    assert "equipo_bp" in re.search(r"for bp in \((.*?)\):", SRC, re.S).group(1)
    fuente_db = (RAIZ / "database.py").read_text(encoding="utf-8")
    assert '_grant_panel_to_existing_roles(conn, "equipo")' in fuente_db
    assert '_grant_panel_to_existing_roles(conn, "ausencias", solo_si_tiene="equipo")' in fuente_db


# ── criterio 2: el organigrama sale de reporta_a ─────────────────────────────

def _jefes(estado):
    por_id = {p["id"]: p["nombre"] for p in estado["organigrama"]}
    return {p["nombre"]: por_id.get(p["reporta_a"]) for p in estado["organigrama"]}


def test_c2_el_organigrama_refleja_reporta_a(cli):
    jefes = _jefes(_estado(cli))
    assert jefes == {n: j for n, (_r, j, _l) in ESPERADO.items()}

    db = _db(cli)
    javier = _persona(db, "Javier Tomasetti")["id"]
    _set(db, "UPDATE equipo_personas SET reporta_a=? WHERE nombre='Gonzalo Siuciak'", (javier,))
    assert _jefes(_estado(cli))["Gonzalo Siuciak"] == "Javier Tomasetti"


def test_el_cto_se_resalta_por_rol_y_no_por_nombre(cli):
    destacados = [p["nombre"] for p in _estado(cli)["organigrama"] if p["destacado"]]
    assert destacados == ["Matías Domínguez"]

    db = _db(cli)
    _set(db, "UPDATE equipo_personas SET rol='Director técnico' WHERE nombre='Matías Domínguez'", ())
    _set(db, "UPDATE equipo_personas SET rol='CTO interino' WHERE nombre='Andrés Rosi'", ())
    destacados = [p["nombre"] for p in _estado(cli)["organigrama"] if p["destacado"]]
    assert destacados == ["Andrés Rosi"], "'Director' no es CTO aunque contenga las letras"


def test_un_ciclo_o_un_jefe_inactivo_no_saca_a_nadie_del_dibujo():
    personas = [
        {"id": 1, "nombre": "A", "rol": "", "reporta_a": 2, "lleva_horas": 0},
        {"id": 2, "nombre": "B", "rol": "", "reporta_a": 1, "lleva_horas": 0},
        {"id": 3, "nombre": "C", "rol": "", "reporta_a": 99, "lleva_horas": 0},
    ]
    salida = {p["nombre"]: p["reporta_a"] for p in organigrama(personas)}
    assert salida["C"] is None
    assert None in (salida["A"], salida["B"]), "el ciclo tiene que cortarse"


# ── criterios 3 y 4: saldo ───────────────────────────────────────────────────

def test_c3_ausencia_de_dos_dias_de_gonzalo_deja_saldo_menos_8(cli):
    r = _ausencia(cli)
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["horas_totales"] == 8

    estado = _estado(cli)
    gonzalo = _fila(estado, "Gonzalo Siuciak")
    assert gonzalo["saldo"] == 8 and gonzalo["al_dia"] is False
    assert gonzalo["dias"][LUNES]["falta"] and gonzalo["dias"][MARTES]["falta"]
    assert not gonzalo["dias"][MIERCOLES]["falta"]
    assert _fila(estado, "Juan Tomasetti")["al_dia"] is True
    aus = estado["ausencias"][0]
    assert aus["recuperado"] is False and aus["recuperos"] == []


def test_c4_dos_recuperos_de_4h_lo_dejan_al_dia(cli):
    aid = _ausencia(cli).get_json()["id"]

    r = cli.post(f"/api/equipo/ausencias/{aid}/recuperos", json={"fecha": MIERCOLES, "horas": 4})
    assert r.status_code == 201
    estado = _estado(cli)
    assert _fila(estado, "Gonzalo Siuciak")["saldo"] == 4
    assert estado["ausencias"][0]["recuperado"] is False
    assert estado["ausencias"][0]["horas_pendientes"] == 4

    # El segundo es la semana que viene: un recupero futuro ya cuenta.
    r = cli.post(f"/api/equipo/ausencias/{aid}/recuperos", json={"fecha": PROX_LUNES, "horas": 4})
    assert r.status_code == 201
    estado = _estado(cli)
    gonzalo = _fila(estado, "Gonzalo Siuciak")
    assert gonzalo["saldo"] == 0 and gonzalo["al_dia"] is True
    assert gonzalo["dias"][MIERCOLES]["recupero"] == 4
    assert gonzalo["dias"][PROX_LUNES]["recupero"] == 4
    assert estado["ausencias"][0]["recuperado"] is True


def test_la_grilla_va_a_la_semana_pedida(cli):
    """Un recupero a tres semanas no entraba en la grilla de dos semanas: se
    agendaba y no se veia en verde en ningun lado (Juan, 14/9)."""
    aid = _ausencia(cli).get_json()["id"]
    cli.post(f"/api/equipo/ausencias/{aid}/recuperos", json={"fecha": "2026-10-07", "horas": 4})

    assert "2026-10-07" not in _fila(_estado(cli), "Gonzalo Siuciak")["dias"]

    r = cli.get("/api/equipo?desde=2026-10-07")
    assert r.status_code == 200
    estado = r.get_json()
    assert estado["semanas"][0][0] == "2026-10-05" and estado["desde"] == "2026-10-05"
    assert estado["esta_semana"] == LUNES
    assert _fila(estado, "Gonzalo Siuciak")["dias"]["2026-10-07"]["recupero"] == 4
    # La ausencia de esta semana sigue contando para el saldo aunque no se vea.
    assert _fila(estado, "Gonzalo Siuciak")["saldo"] == 4


def test_desde_invalido_da_400(cli):
    assert cli.get("/api/equipo?desde=ayer").status_code == 400


def test_un_recupero_en_sabado_agrega_la_columna(cli):
    aid = _ausencia(cli).get_json()["id"]
    cli.post(f"/api/equipo/ausencias/{aid}/recuperos", json={"fecha": "2026-09-19", "horas": 4})
    estado = _estado(cli)
    assert estado["semanas"][0] == [LUNES, MARTES, MIERCOLES, "2026-09-17", "2026-09-18", "2026-09-19"]
    assert len(estado["semanas"][1]) == 5, "sin recuperos el fin de semana no aparece"
    assert _fila(estado, "Gonzalo Siuciak")["dias"]["2026-09-19"]["recupero"] == 4


def test_las_horas_corregidas_a_mano_se_respetan(cli):
    r = _ausencia(cli, horas_totales=6)
    assert r.status_code == 201
    assert _fila(_estado(cli), "Gonzalo Siuciak")["saldo"] == 6


def test_un_fin_de_semana_sin_horas_pide_cargarlas_a_mano(cli):
    r = _ausencia(cli, desde="2026-09-19", hasta="2026-09-20")
    assert r.status_code == 400 and "a mano" in r.get_json()["error"]
    assert _ausencia(cli, desde="2026-09-19", hasta="2026-09-20", horas_totales=4).status_code == 201


def test_dias_habiles_de_lunes_a_viernes_sin_feriados():
    assert len(dias_habiles(date(2026, 9, 14), date(2026, 9, 20))) == 5
    assert len(dias_habiles(date(2026, 9, 18), date(2026, 9, 21))) == 2
    assert dias_habiles(date(2026, 9, 15), date(2026, 9, 14)) == []


# ── validación y borrado ─────────────────────────────────────────────────────

@pytest.mark.parametrize("cambio,parte_del_error", [
    ({"fecha_hasta": "2026-09-11"}, "anterior"),
    ({"fecha_desde": "2026-02-30"}, "desde"),
    ({"fecha_hasta": "15/09/2026"}, "hasta"),
    ({"horas_totales": 0}, "mayor que cero"),
    ({"horas_totales": -2}, "mayor que cero"),
    ({"horas_totales": "muchas"}, "mayor que cero"),
    ({"motivo": "   "}, "motivo"),
    ({"persona_id": 9999}, "persona"),
])
def test_la_ausencia_se_valida_en_el_servidor(cli, cambio, parte_del_error):
    datos = {"persona_id": _persona(_db(cli), "Gonzalo Siuciak")["id"],
             "fecha_desde": LUNES, "fecha_hasta": MARTES, "motivo": "Trámite", **cambio}
    r = cli.post("/api/equipo/ausencias", json=datos)
    assert r.status_code == 400
    assert parte_del_error in r.get_json()["error"]
    assert _estado(cli)["ausencias"] == []


@pytest.mark.parametrize("datos", [{"fecha": "", "horas": 4}, {"fecha": MIERCOLES, "horas": 0},
                                   {"fecha": "2026-13-01", "horas": 4}, {"fecha": MIERCOLES}])
def test_el_recupero_se_valida_en_el_servidor(cli, datos):
    aid = _ausencia(cli).get_json()["id"]
    assert cli.post(f"/api/equipo/ausencias/{aid}/recuperos", json=datos).status_code == 400
    assert cli.post("/api/equipo/ausencias/9999/recuperos",
                    json={"fecha": MIERCOLES, "horas": 4}).status_code == 404


def test_borrar_una_ausencia_se_lleva_sus_recuperos(cli):
    aid = _ausencia(cli).get_json()["id"]
    cli.post(f"/api/equipo/ausencias/{aid}/recuperos", json={"fecha": MIERCOLES, "horas": 4})
    assert cli.delete(f"/api/equipo/ausencias/{aid}").status_code == 200
    estado = _estado(cli)
    assert estado["ausencias"] == [] and _fila(estado, "Gonzalo Siuciak")["saldo"] == 0
    conn = sqlite3.connect(_db(cli))
    assert conn.execute("SELECT COUNT(*) FROM equipo_recuperos").fetchone()[0] == 0
    conn.close()
    assert cli.delete(f"/api/equipo/ausencias/{aid}").status_code == 404


def test_borrar_un_recupero_vuelve_a_dejar_la_deuda(cli):
    aid = _ausencia(cli).get_json()["id"]
    rid = cli.post(f"/api/equipo/ausencias/{aid}/recuperos",
                   json={"fecha": MIERCOLES, "horas": 8}).get_json()["id"]
    assert _fila(_estado(cli), "Gonzalo Siuciak")["al_dia"] is True
    assert cli.delete(f"/api/equipo/recuperos/{rid}").status_code == 200
    assert _fila(_estado(cli), "Gonzalo Siuciak")["saldo"] == 8
    assert cli.delete(f"/api/equipo/recuperos/{rid}").status_code == 404


# ── criterio 5: cruce con Proyectos ──────────────────────────────────────────

def test_c5_aviso_si_la_ausencia_pisa_una_entrega(cli):
    db = _db(cli)
    upsert_project(db, "p1", "Web Bar Tito", timeline_start="2026-09-01", timeline_end=MARTES)
    upsert_project(db, "p2", "Tienda Lejana", timeline_start="2026-09-01", timeline_end="2026-09-30")
    upsert_project(db, "p3", "Con hora", timeline_start="2026-09-01",
                   timeline_end="2026-09-14T10:00:00.000-03:00")
    upsert_project(db, "p4", "Sin final", timeline_start=MARTES)
    assert _estado(cli)["avisos"] == []

    _ausencia(cli)
    avisos = _estado(cli)["avisos"]
    assert [(a["proyecto"], a["fecha_entrega"]) for a in avisos] == [
        ("Con hora", LUNES), ("Web Bar Tito", MARTES)]
    assert all(a["persona"] == "Gonzalo Siuciak" for a in avisos)


def test_una_ausencia_que_ya_paso_no_avisa(cli):
    upsert_project(_db(cli), "p1", "Entregado", timeline_end="2026-09-02")
    _ausencia(cli, desde="2026-09-01", hasta="2026-09-02")
    assert _estado(cli)["avisos"] == []


# ── criterio 6: quienes no llevan horas ──────────────────────────────────────

def test_c6_matias_andres_y_guillermo_en_el_organigrama_y_no_en_ausencias(cli):
    estado = _estado(cli)
    en_org = {p["nombre"] for p in estado["organigrama"]}
    for nombre in ("Matías Domínguez", "Andrés Rosi", "Guillermo Paredes"):
        assert nombre in en_org
    assert {f["nombre"] for f in estado["calendario"]} == {"Juan Tomasetti", "Gonzalo Siuciak"}
    assert {p["nombre"] for p in estado["personas"]} == {"Juan Tomasetti", "Gonzalo Siuciak"}

    r = _ausencia(cli, nombre="Matías Domínguez")
    assert r.status_code == 400 and "no se le llevan horas" in r.get_json()["error"]


# ── permisos ─────────────────────────────────────────────────────────────────

def test_sin_el_panel_equipo_da_403(app):
    db = app.config["_DB"]
    c = _cli(app, _usuario(db, "caller@scalerics.com", _rol(db, "SoloCola", ["cola"])))
    assert c.get("/api/equipo").status_code == 403
    assert c.post("/api/equipo/ausencias", json={}).status_code == 403
    assert c.get("/api/equipo/capacidad").status_code == 403


@pytest.mark.parametrize("panel", ["ausencias", "equipo"])
def test_con_uno_solo_de_los_dos_paneles_se_usa_toda_la_api(app, panel):
    """Organigrama y Ausencias leen el mismo pedido: cualquiera de los dos
    abre las rutas de ausencias y recuperos."""
    db = app.config["_DB"]
    c = _cli(app, _usuario(db, f"{panel}@scalerics.com", _rol(db, f"Solo-{panel}", [panel])))
    assert c.get("/api/equipo").status_code == 200
    assert c.get("/api/equipo?desde=2026-09-21").status_code == 200
    assert c.get("/api/equipo/capacidad").status_code == 200
    r = _ausencia(c)
    assert r.status_code == 201, r.get_json()
    aid = r.get_json()["id"]
    r = c.post(f"/api/equipo/ausencias/{aid}/recuperos", json={"fecha": MIERCOLES, "horas": 4})
    assert r.status_code == 201
    assert c.delete(f"/api/equipo/recuperos/{r.get_json()['id']}").status_code == 200
    assert c.delete(f"/api/equipo/ausencias/{aid}").status_code == 200
    assert c.get("/").status_code == 200


def test_con_el_simulador_se_lee_solo_la_capacidad(app):
    db = app.config["_DB"]
    c = _cli(app, _usuario(db, "socio@scalerics.com", _rol(db, "SoloSim", ["simulador"])))
    assert c.get("/api/equipo/capacidad").status_code == 200
    assert c.get("/api/equipo").status_code == 403


def test_sin_sesion_no_entra(app):
    assert app.test_client().get("/api/equipo").status_code in (401, 302)


# ── regla 3: capacidad neta ──────────────────────────────────────────────────

def test_la_capacidad_neta_resta_ausencias_y_no_suma_recuperos(cli):
    aid = _ausencia(cli).get_json()["id"]
    cli.post(f"/api/equipo/ausencias/{aid}/recuperos", json={"fecha": MIERCOLES, "horas": 2})
    cli.post(f"/api/equipo/ausencias/{aid}/recuperos", json={"fecha": PROX_LUNES, "horas": 6})

    c = cli.get("/api/equipo/capacidad").get_json()
    assert c["semana"] == LUNES and c["dias_habiles"] == 5
    assert c["totales"] == {"horas_base": 40, "horas_ausencia": 8,
                            "horas_recupero": 2, "capacidad_neta": 32}
    gonzalo = next(p for p in c["personas"] if p["nombre"] == "Gonzalo Siuciak")
    assert gonzalo["capacidad_neta"] == 12 and gonzalo["horas_recupero"] == 2

    # Cualquier día de la semana que viene lleva a su lunes.
    c = cli.get("/api/equipo/capacidad?semana=2026-09-23").get_json()
    assert c["semana"] == PROX_LUNES
    assert c["totales"] == {"horas_base": 40, "horas_ausencia": 0,
                            "horas_recupero": 6, "capacidad_neta": 40}


def test_la_capacidad_reparte_horas_corregidas_entre_semanas(cli):
    _ausencia(cli, desde="2026-09-18", hasta=PROX_LUNES, horas_totales=6)
    assert cli.get("/api/equipo/capacidad").get_json()["totales"]["horas_ausencia"] == 3
    assert cli.get(f"/api/equipo/capacidad?semana={PROX_LUNES}").get_json()["totales"]["horas_ausencia"] == 3


def test_la_capacidad_con_semana_invalida_da_400(cli):
    assert cli.get("/api/equipo/capacidad?semana=mañana").status_code == 400


# ── las trampas de DASHBOARD_HTML ────────────────────────────────────────────

@pytest.mark.parametrize("nombre", sorted(FUENTES))
def test_nada_que_jinja_o_python_interpreten(nombre):
    texto = FUENTES[nombre]
    for trampa in ("{#", "{{", "{%"):
        assert trampa not in texto, f"{trampa!r} en el {nombre} de Equipo"
    assert chr(92) not in texto, f"un backslash en el {nombre}: Python se lo come"


def test_el_css_usa_solo_tokens():
    reglas = re.findall(r"([^{}]+)\{([^{}]*)\}", CSS)
    assert len(reglas) > 30
    for selector, cuerpo in reglas:
        assert not re.search(r"#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])|rgba?\(", cuerpo), selector.strip()
    assert "body.light" not in CSS.split("*/", 1)[1]
    for nombre in ("panel", "modales", "js"):
        assert "style=" not in FUENTES[nombre], f"estilo inline en el {nombre} de Equipo"


def test_todo_lo_del_js_lleva_el_prefijo_eq():
    nombres = re.findall(r"^(?:async )?function ([A-Za-z_$][\w$]*)\(", JS, re.M)
    nombres += re.findall(r"^(?:const|let) ([A-Za-z_$][\w$]*)", JS, re.M)
    assert len(nombres) > 20
    sueltos = [n for n in nombres if not (n.startswith("eq") or n.startswith("EQ_") or n == "loadEquipo")]
    assert not sueltos, f"sin prefijo: {sueltos}"
    for nombre in nombres:
        declaraciones = re.findall(r"^(?:async )?(?:function|const|let) " + re.escape(nombre) + r"\b",
                                   HTML, re.M)
        assert len(declaraciones) == 1, f"{nombre} está declarado {len(declaraciones)} veces"


# ── se pinta de verdad ───────────────────────────────────────────────────────

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
    archivo = tmp_path / "equipo.js"
    archivo.write_text(_ARNES.replace("__RESPUESTAS__", json.dumps(respuestas, ensure_ascii=False))
                       + "\n".join(bloques) + "\n" + prueba, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, (r.stderr or "")[:2000]
    return json.loads(r.stdout.strip().splitlines()[-1])


@sin_node
def test_la_pantalla_se_pinta_con_lo_que_devuelve_el_servidor(cli, tmp_path):
    upsert_project(_db(cli), "p1", "Web Bar Tito", timeline_end=MARTES)
    aid = _ausencia(cli).get_json()["id"]
    cli.post(f"/api/equipo/ausencias/{aid}/recuperos", json={"fecha": MIERCOLES, "horas": 4})
    estado = _estado(cli)
    gonzalo = _persona(_db(cli), "Gonzalo Siuciak")["id"]

    prueba = """
(async () => {
  await loadEquipo();
  const s = {};
  ['eq-organigrama', 'eq-avisos', 'eq-calendario', 'eq-detalle'].forEach(id => { s[id] = _el(id).innerHTML; });

  eqAbrirAusencia();
  s.opciones = _el('eq-aus-persona').innerHTML;
  _el('eq-aus-persona').value = '__GONZALO__';
  _el('eq-aus-desde').value = '2026-09-17';
  _el('eq-aus-hasta').value = '2026-09-18';
  eqSugerirHoras();
  s.horasSugeridas = _el('eq-aus-horas').value;
  s.calculo = _el('eq-aus-calculo').textContent;
  _el('eq-aus-horas').value = '5';
  eqTocarHoras();
  _el('eq-aus-hasta').value = '2026-09-21';
  eqSugerirHoras();
  s.horasTrasTocar = _el('eq-aus-horas').value;

  _el('eq-aus-hasta').value = '2026-09-10';
  _el('eq-aus-motivo').value = 'Médico';
  const antes = _pedidos.length;
  await eqGuardarAusencia();
  s.errorFechas = _el('eq-aus-error').textContent;
  _el('eq-aus-hasta').value = '2026-09-18';
  _el('eq-aus-motivo').value = '   ';
  await eqGuardarAusencia();
  s.errorMotivo = _el('eq-aus-error').textContent;
  _el('eq-aus-motivo').value = 'Médico';
  _el('eq-aus-horas').value = '0';
  await eqGuardarAusencia();
  s.errorHoras = _el('eq-aus-error').textContent;
  s.postsInvalidos = _pedidos.slice(antes).filter(p => p[1] === 'POST').length;

  eqAbrirRecupero(__AUSENCIA__);
  s.recContexto = _el('eq-rec-contexto').textContent;
  s.recHoras = _el('eq-rec-horas').value;
  _el('eq-rec-fecha').value = '';
  await eqGuardarRecupero();
  s.recError = _el('eq-rec-error').textContent;

  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""".replace("__GONZALO__", str(gonzalo)).replace("__AUSENCIA__", str(aid))
    s = _correr_js(tmp_path, {"/api/equipo": estado}, prueba)

    org = s["eq-organigrama"]
    assert org.startswith("<svg") and org.count('<g class="eq-nodo') == 7
    assert org.count("eq-destacado") == 1
    destacado = org[org.index("eq-destacado"):]
    assert destacado.index("Matías Domínguez") < destacado.index("</g>")
    assert org.count("<line") >= 10

    cal = s["eq-calendario"]
    assert "Gonzalo Siuciak" in cal and "Juan Tomasetti" in cal
    assert "Matías" not in cal and "Andrés" not in cal and "Guillermo" not in cal
    assert cal.count("eq-dia eq-falta") == 2
    assert '<td class="eq-dia eq-recupero">+4h</td>' in cal
    assert '<td class="eq-saldo eq-debe">−4h</td>' in cal
    assert '<td class="eq-saldo eq-al-dia">al día</td>' in cal
    assert cal.count('class="eq-cal-hueco"') == 1 + 1 + 2, "un hueco entre semanas por fila"
    assert cal.count("eq-dia") == 20

    det = s["eq-detalle"]
    assert "<b>Gonzalo Siuciak</b> · Trámite" in det
    assert "sin fecha · faltan 4h" in det and "Agendar recupero" in det
    assert "recupera mié 16/09 (4h)" in det and "Faltó lun 14/09 al mar 15/09 · 8h" in det

    assert "Web Bar Tito" in s["eq-avisos"] and "15/09/2026" in s["eq-avisos"]

    assert "Matías" not in s["opciones"] and "Gonzalo Siuciak" in s["opciones"]
    assert s["horasSugeridas"] == "8" and "2 días hábiles" in s["calculo"]
    assert s["horasTrasTocar"] == "5", "una corrección a mano no se pisa"
    assert "anterior" in s["errorFechas"]
    assert "motivo" in s["errorMotivo"]
    assert "mayor que cero" in s["errorHoras"]
    assert s["postsInvalidos"] == 0, "con datos inválidos no se manda nada"
    assert "Faltan 4h" in s["recContexto"] and s["recHoras"] == "4"
    assert "fecha" in s["recError"]

    todo = "".join(str(v) for v in s.values())
    assert not _sin_dinero(todo)


@sin_node
def test_al_agendar_un_recupero_lejos_la_grilla_salta_a_su_semana(cli, tmp_path):
    aid = _ausencia(cli).get_json()["id"]
    antes = _estado(cli)
    cli.post(f"/api/equipo/ausencias/{aid}/recuperos", json={"fecha": "2026-10-07", "horas": 2})
    octubre = cli.get("/api/equipo?desde=2026-10-07").get_json()
    cerca = _estado(cli)

    prueba = """
(async () => {
  const s = {};
  await loadEquipo();
  s.rangoHoy = _el('eq-cal-rango').textContent;

  eqAbrirRecupero(__AUSENCIA__);
  _el('eq-rec-fecha').value = '2026-10-07';
  _el('eq-rec-horas').value = '2';
  await eqGuardarRecupero();
  s.pedidoLejos = _pedidos.filter(p => p[1] === 'GET').pop()[0];
  s.calLejos = _el('eq-calendario').innerHTML;
  s.rangoLejos = _el('eq-cal-rango').textContent;

  eqSemanasHoy();
  await new Promise(r => setTimeout(r, 10));
  eqAbrirRecupero(__AUSENCIA__);
  _el('eq-rec-fecha').value = '2026-09-16';
  _el('eq-rec-horas').value = '2';
  await eqGuardarRecupero();
  s.pedidoCerca = _pedidos.filter(p => p[1] === 'GET').pop()[0];

  eqSemanas(1);
  await new Promise(r => setTimeout(r, 10));
  s.pedidoSiguiente = _pedidos.filter(p => p[1] === 'GET').pop()[0];

  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""".replace("__AUSENCIA__", str(aid))
    s = _correr_js(tmp_path, {"/api/equipo": antes, "/api/equipo?desde=2026-10-07": octubre,
                              "/api/equipo?desde=2026-09-21": cerca}, prueba)

    assert s["rangoHoy"] == "Del lun 14/09 al dom 27/09"
    assert s["pedidoLejos"] == "/api/equipo?desde=2026-10-07"
    assert '<td class="eq-dia eq-recupero">+2h</td>' in s["calLejos"]
    assert "Semana del lun 05/10" in s["calLejos"] and "Esta semana" not in s["calLejos"]
    assert s["rangoLejos"] == "Del lun 05/10 al dom 18/10"
    assert s["pedidoCerca"] == "/api/equipo", "si ya se ve, la grilla no se mueve"
    assert s["pedidoSiguiente"] == "/api/equipo?desde=2026-09-21"


_SOLO_UN_PANEL = {
    # Lo que no está en la página de un rol que ve solo uno de los dos paneles.
    "ausencias": ["equipo-panel", "nav-equipo", "eq-organigrama"],
    "equipo": ["ausencias-panel", "nav-ausencias", "eq-avisos", "eq-cal-rango",
               "eq-calendario", "eq-detalle"],
}


@sin_node
@pytest.mark.parametrize("panel", sorted(_SOLO_UN_PANEL))
@pytest.mark.parametrize("falla", [False, True])
def test_abrir_un_panel_sin_el_otro_no_revienta(cli, tmp_path, panel, falla):
    _ausencia(cli)
    faltan = _SOLO_UN_PANEL[panel]
    prueba = """
(async () => {
  const faltan = __FALTAN__;
  document.getElementById = id => faltan.includes(id) ? null : _el(id);
  showPanel('__PANEL__');
  await new Promise(r => setTimeout(r, 20));
  const s = {pedidos: _pedidos.map(p => p[0])};
  ['eq-organigrama', 'eq-calendario', 'eq-detalle'].forEach(id => { s[id] = _el(id).innerHTML; });
  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""".replace("__FALTAN__", json.dumps(faltan)).replace("__PANEL__", panel)
    s = _correr_js(tmp_path, {"/api/equipo": "falla" if falla else _estado(cli)}, prueba)
    assert "/api/equipo" in s["pedidos"]
    visibles = [i for i in ("eq-organigrama", "eq-calendario", "eq-detalle") if i not in faltan]
    for id_ in ("eq-organigrama", "eq-calendario", "eq-detalle"):
        if id_ in faltan:
            assert s[id_] == "", f"{id_} no está en la página y no se tendría que pintar"
    if falla:
        assert any("No se pudieron cargar" in s[i] for i in visibles)
    elif panel == "equipo":
        assert s["eq-organigrama"].startswith("<svg")
    else:
        assert "Gonzalo Siuciak" in s["eq-calendario"] and "Trámite" in s["eq-detalle"]


@sin_node
def test_el_organigrama_de_una_sola_raiz_tambien_se_dibuja(tmp_path):
    personas = [{"id": 1, "nombre": "Uno", "rol": "CTO", "reporta_a": None, "destacado": True},
                {"id": 2, "nombre": "Dos", "rol": "", "reporta_a": 1, "destacado": False},
                {"id": 3, "nombre": "Tres", "rol": "", "reporta_a": None, "destacado": False}]
    prueba = ("console.log(JSON.stringify({svg: eqOrganigramaSvg(" + json.dumps(personas)
              + "), solo: eqOrganigramaSvg([" + json.dumps(personas[0]) + "]), nada: eqOrganigramaSvg([])}));")
    s = _correr_js(tmp_path, {}, prueba)
    assert s["svg"].count('<g class="eq-nodo') == 3
    assert s["solo"].count('<g class="eq-nodo') == 1 and "<line" not in s["solo"]
    assert "No hay personas" in s["nada"]


_PRECARGA_SIM = {"moneda": "USD", "cajaActual": 1200, "gastosFijos": [],
                 "mantenimientos": [], "pendientes": []}

_PRUEBA_SIM = """
(async () => {
  await loadSimulador();
  await new Promise(ok => setTimeout(ok, 30));
  console.log(JSON.stringify({capacidad: _el('sim-capacidad-equipo').textContent,
                              tarjetas: _el('sim-tarjetas').innerHTML}));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
"""


@sin_node
def test_el_simulador_muestra_la_capacidad_leida_de_equipo(cli, tmp_path):
    _ausencia(cli)
    capacidad = cli.get("/api/equipo/capacidad").get_json()
    s = _correr_js(tmp_path, {"/api/simulador/precarga": _PRECARGA_SIM,
                              "/api/simulador/escenarios": [],
                              "/api/equipo/capacidad": capacidad}, _PRUEBA_SIM)
    assert "leída de Ausencias (no editable): 32 h = 40 h base − 8 h de ausencias" in s["capacidad"]
    assert "no suman capacidad libre" in s["capacidad"]
    assert "Caja del mes" in s["tarjetas"]


@sin_node
def test_si_equipo_falla_el_simulador_sigue_igual(tmp_path):
    s = _correr_js(tmp_path, {"/api/simulador/precarga": _PRECARGA_SIM,
                              "/api/simulador/escenarios": [],
                              "/api/equipo/capacidad": "falla"}, _PRUEBA_SIM)
    assert "No se pudo leer la capacidad" in s["capacidad"]
    assert "Caja del mes" in s["tarjetas"]


def test_la_capacidad_en_el_simulador_no_es_un_campo():
    """Principio del simulador: ningún número escondido ni reemplazado. La
    capacidad leída no tiene data-sim ni input: es texto al lado de los campos."""
    bloque = _entre(SRC, '<div class="fin-card-title">Equipo</div>', "</section>")
    for ruta in ("equipo.cantidadProgramadores", "equipo.sueldoPorProgramador",
                 "equipo.proyectosPorProgramador"):
        assert f'data-sim="{ruta}"' in bloque
    leido = re.search(r'<div class="sim-leido" id="sim-capacidad-equipo"[^>]*></div>', bloque)
    assert leido and "data-sim" not in leido.group(0)
