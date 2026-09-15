"""El menu en el orden que definio Juan, con el Calendario primero (14/9).

Menu, por grupo: CALENDARIO (Calendario) · MARKETING (Meta Ads, Marketing) ·
FINANZAS (Finanzas, Simulador financiero) · VENTAS (Seguimiento de leads,
WhatsApp, Proceso de venta, Demos) · OPERACION (Clientes, Proyectos, Tareas,
Actividad, Equipo) · CAPTACION (Outbound, Inteligencia comercial, SDR). La barra
del celular sigue el mismo orden. Equipo se sumo a OPERACION despues de
Actividad el 14/9, y Seguimiento de leads (`seg_leads`) es el primer item de
VENTAS, arriba de WhatsApp (pedido de Juan, 14/9).

Al entrar se abre el Calendario (Juan lo pidio despues de haber pedido Meta
Ads: gana el ultimo pedido). Un rol que no tiene el calendario no puede quedar
mirando un panel que no le corresponde, ni uno que ya no existe.
"""

import re
import shutil
import subprocess

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db

HTML = dashboard.DASHBOARD_HTML

sin_node = pytest.mark.skipif(shutil.which("node") is None,
                              reason="node no esta instalado")

ORDEN = [
    ("CALENDARIO", ["cal"]),
    ("MARKETING", ["meta", "marketing"]),
    ("FINANZAS", ["finanzas", "simulador"]),
    ("VENTAS", ["seg_leads", "wa", "notion_clients", "demos", "plantillas"]),
    ("OPERACIÓN", ["clientes", "projects", "tasks", "activity"]),
    ("RECURSOS HUMANOS", ["equipo", "ausencias"]),
    ("CAPTACIÓN", ["cola", "metrics", "sdr"]),
]


def _menu():
    """[(grupo, [paneles])] tal como aparece en el sidebar."""
    bloque = HTML[HTML.index('<div class="nav-scroll">'):HTML.index('<div class="sidebar-bottom">')]
    grupos = []
    for m in re.finditer(r'nav-section-label">([^<]+)</div>|id="nav-([\w]+)"', bloque):
        if m.group(1):
            grupos.append((m.group(1), []))
        else:
            grupos[-1][1].append(m.group(2))
    return grupos


def test_el_menu_tiene_los_grupos_y_el_orden_de_juan():
    assert _menu() == ORDEN


def test_el_calendario_va_solo_en_su_grupo_arriba_de_todo():
    grupo, paneles = _menu()[0]
    assert grupo == "CALENDARIO" and paneles == ["cal"]


def test_la_barra_del_celular_sigue_el_mismo_orden():
    orden = re.findall(r"'(\w+)'", re.search(r"const NAV_PRIORITY = \[([^\]]*)\]", HTML).group(1))
    del_menu = [p for _, paneles in ORDEN for p in paneles]
    assert orden[0] == "cal"
    assert orden == [p for p in del_menu if p in orden], orden
    assert len(orden) >= 10


def test_el_calendario_arranca_marcado_y_los_demas_no():
    assert '<div class="nav-item active" id="nav-cal"' in HTML
    assert '<div class="nav-item" id="nav-meta"' in HTML
    assert '<div class="nav-item" id="nav-cola"' in HTML
    assert '<div id="cal-panel" class="panel active">' in HTML
    assert '<div id="meta-panel" class="panel">' in HTML
    assert '<div id="cola-panel" class="panel">' in HTML
    assert HTML.count('class="nav-item active"') == 1
    assert len(re.findall(r'id="[\w]+-panel" class="panel active"', HTML)) == 1
    assert "let activePanel = 'cal';" in HTML


def _carga_inicial() -> str:
    m = re.search(r"// Initial load.*?\n([^/\s].*?)\n", HTML, re.S)
    assert m, "no encontre la carga inicial"
    return m.group(1)


def test_al_entrar_se_dibuja_el_calendario():
    llamada = _carga_inicial()
    assert llamada == "calLoaded = true; renderCalendar();", llamada
    assert "loadCola" not in llamada and "loadMetaPanel" not in llamada


def test_la_carga_inicial_no_usa_showpanel():
    """La carga inicial corre antes de que se declaren NAV_LABELS y compania con
    const. showPanel -> _syncMobileNav las lee y tira un ReferenceError que
    corta el resto del <script> en el navegador: permisos, barra del celular,
    tema. Paso con esta misma rama antes de publicarse."""
    assert "showPanel" not in _carga_inicial()
    inicio = HTML.index("// Initial load")
    assert inicio < HTML.index("const NAV_LABELS")
    # Lo que renderCalendar usa antes de su primer await ya esta declarado.
    for decl in ("let calLoaded", "let calMonthOffset", "let calView"):
        assert HTML.index(decl) < inicio, decl


def _primer_panel(access, existentes):
    """Arma en node la eleccion real del primer panel para un rol sin calendario."""
    nav = re.search(r"const NAV_PRIORITY = \[[^\]]*\];", HTML).group(0)
    todos = re.search(r"const ALL_PANELS = \[[^\]]*\];", HTML).group(0)
    m = re.search(r"const first = (NAV_PRIORITY\.concat\(ALL_PANELS\)\.find\(.*?\));", HTML, re.S)
    assert m, "no encontre la eleccion del primer panel"
    return "\n".join([
        nav, todos,
        f"const access = {access!r};".replace("'", '"'),
        f"const existentes = {existentes!r};".replace("'", '"'),
        "const document = {getElementById: id => existentes.includes(id.replace('-panel','')) ? {} : null};",
        f"const first = {m.group(1)};",
        "console.log(first === undefined ? '' : first);",
    ])


@sin_node
@pytest.mark.parametrize("access,esperado", [
    (["seguimientos", "cola", "wa"], "wa"),              # 'seguimientos' ya no existe
    (["pipeline", "clientes", "meta"], "meta"),           # meta va antes que clientes
    (["cola", "tasks"], "tasks"),
    (["cola", "ausencias"], "ausencias"),                 # Recursos Humanos va antes que Captación
])
def test_un_rol_sin_calendario_arranca_en_un_panel_que_existe(tmp_path, access, esperado):
    existentes = ["meta", "cola", "cal", "tasks", "clientes", "wa", "metrics",
                  "activity", "sdr", "projects", "notion_clients", "finanzas", "simulador",
                  "equipo", "ausencias", "seg_leads"]
    archivo = tmp_path / "primero.js"
    archivo.write_text(_primer_panel(access, existentes), encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == esperado


def test_la_pagina_abre(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    db = str(tmp_path / "cal.db")
    init_db(db)
    app = dashboard.create_app(db)
    uid = create_user(db, name="Jefe", email="jefe@test.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    cli = app.test_client()
    with cli.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Jefe"
    assert cli.get("/").status_code == 200
