"""El menu en el orden que definio Juan, y Meta Ads como panel de entrada (14/9).

Menu, por grupo: CALENDARIO (Calendario) · MARKETING (Meta Ads, Marketing) ·
FINANZAS (Finanzas, Simulador financiero) · VENTAS (WhatsApp, Proceso de venta,
Demos, SDR) · OPERACION (Clientes, Proyectos, Tareas, Actividad) · CAPTACION
(Outbound, Cola). La barra del celular sigue el mismo orden.

Aunque Calendario va arriba en el menu, al entrar se abre Meta Ads, que es donde
entran los leads que se trabajan. Un rol que no tiene Meta no puede quedar
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
    ("VENTAS", ["wa", "notion_clients", "demos", "sdr"]),
    ("OPERACIÓN", ["clientes", "projects", "tasks", "activity"]),
    ("CAPTACIÓN", ["metrics", "cola"]),
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


def test_la_barra_del_celular_sigue_el_mismo_orden():
    orden = re.findall(r"'(\w+)'", re.search(r"const NAV_PRIORITY = \[([^\]]*)\]", HTML).group(1))
    del_menu = [p for _, paneles in ORDEN for p in paneles]
    # La barra solo lista los paneles que ya tenian icono y nombre corto; los
    # que lista van en el mismo orden relativo que el menu.
    assert orden == [p for p in del_menu if p in orden], orden
    assert len(orden) >= 10


def test_meta_arranca_marcado_y_la_cola_no():
    assert '<div class="nav-item active" id="nav-meta"' in HTML
    assert '<div class="nav-item" id="nav-cola"' in HTML
    assert '<div id="meta-panel" class="panel active">' in HTML
    assert '<div id="cola-panel" class="panel">' in HTML
    assert "let activePanel = 'meta';" in HTML


def _carga_inicial() -> str:
    m = re.search(r"// Initial load.*?\n([^/\s].*?)\n", HTML, re.S)
    assert m, "no encontre la carga inicial"
    return m.group(1)


def test_al_entrar_se_carga_meta_y_no_la_cola():
    llamada = _carga_inicial()
    assert llamada == "loadMetaPanel();", llamada
    assert "loadCola" not in llamada


def test_la_carga_inicial_no_usa_showpanel():
    """La carga inicial corre antes de que se declaren NAV_LABELS y compania con
    const. showPanel -> _syncMobileNav las lee y tira un ReferenceError que
    corta el resto del <script> en el navegador: permisos, barra del celular,
    tema. Paso con esta misma rama antes de publicarse."""
    assert "showPanel" not in _carga_inicial()
    assert HTML.index("// Initial load") < HTML.index("const NAV_LABELS")


def _primer_panel(access, existentes):
    """Arma en node la eleccion real del primer panel para un rol sin Meta."""
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
    (["pipeline", "clientes", "cal"], "cal"),             # cal va primero en el menu
    (["cola", "tasks"], "tasks"),
])
def test_un_rol_sin_meta_arranca_en_un_panel_que_existe(tmp_path, access, esperado):
    existentes = ["meta", "cola", "cal", "tasks", "clientes", "wa", "metrics",
                  "activity", "sdr", "projects", "notion_clients", "finanzas", "simulador"]
    archivo = tmp_path / "primero.js"
    archivo.write_text(_primer_panel(access, existentes), encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == esperado


def test_la_pagina_abre(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    db = str(tmp_path / "meta.db")
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
