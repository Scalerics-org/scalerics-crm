"""Lo primero del CRM es Meta Ads, no la Cola (pedido de Juan, 14/9).

Primero en el menu de la izquierda, primero en la barra del celular, y el panel
que se abre al entrar. Un rol que no tiene Meta no puede quedar mirando un panel
que no le corresponde, ni uno que ya no existe.
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


def test_meta_ads_es_el_primer_item_del_menu():
    items = re.findall(r'<div class="nav-item[^"]*" id="nav-([\w]+)"', HTML)
    assert items, "no encontre items de menu"
    assert items[0] == "meta", items[:3]
    assert items.index("meta") < items.index("cola")


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
    inicio = HTML.index("// Initial load")
    assert inicio < HTML.index("const NAV_LABELS"), (
        "si la carga inicial pasa abajo de NAV_LABELS este test ya no hace falta, "
        "pero entonces revisá que no quede antes de ALL_PANELS")


def test_en_el_celular_meta_va_primero():
    orden = re.search(r"const NAV_PRIORITY = \[([^\]]*)\]", HTML).group(1)
    assert orden.split(",")[0].strip() == "'meta'", orden


def _primer_panel(access, existentes):
    """Corre en node la eleccion real del primer panel para un rol sin Meta."""
    nav = re.search(r"const NAV_PRIORITY = \[[^\]]*\];", HTML).group(0)
    todos = re.search(r"const ALL_PANELS = \[[^\]]*\];", HTML).group(0)
    m = re.search(r"const first = (NAV_PRIORITY\.concat\(ALL_PANELS\)\.find\(.*?\));", HTML, re.S)
    assert m, "no encontre la eleccion del primer panel"
    fuente = "\n".join([
        nav, todos,
        f"const access = {access!r};".replace("'", '"'),
        f"const existentes = {existentes!r};".replace("'", '"'),
        "const document = {getElementById: id => existentes.includes(id.replace('-panel','')) ? {} : null};",
        f"const first = {m.group(1)};",
        "console.log(first === undefined ? '' : first);",
    ])
    return fuente


@sin_node
@pytest.mark.parametrize("access,esperado", [
    (["seguimientos", "cola", "wa"], "cola"),          # el primero guardado ya no existe
    (["pipeline", "clientes", "cal"], "cal"),           # cal va antes que clientes en el menu
    (["wa", "cola"], "cola"),
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
