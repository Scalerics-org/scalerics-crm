"""Los titulos de grupo del menu no se ven si el usuario no tiene nada debajo.

Pedido de Juan (15/9): siendo programador por un rato vio el titulo FINANZAS
solo, sin secciones, y se dio cuenta de que habia algo oculto. A quien no es
admin no le tiene que figurar ni el titulo; a los administradores si.
"""

import json
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


def _funcion() -> str:
    m = re.search(r"function _ocultarGruposVacios\(\) \{.*?\n\}", HTML, re.S)
    assert m, "no esta _ocultarGruposVacios"
    return m.group(0)


def _bloque_permisos() -> str:
    inicio = HTML.index("const allowedPanels = ")
    return HTML[inicio:HTML.index("_buildMobileNav(allowedPanels);", inicio)]


def test_se_llama_solo_para_quien_no_es_admin():
    bloque = _bloque_permisos()
    no_admin = bloque[bloque.index("if (access && !m.is_admin) {"):]
    assert "_ocultarGruposVacios();" in no_admin
    # Despues de ocultar los items, que es lo que la funcion mira.
    assert no_admin.index("nav.style.display = 'none'") < no_admin.index("_ocultarGruposVacios();")
    assert HTML.count("_ocultarGruposVacios();") == 1


_ARNES = """
function _el(cls, visible) {
  return { classList: { contains: c => c === cls }, style: { display: visible ? '' : 'none' },
           nextElementSibling: null };
}
// __MENU__ es [["label", nombre] | ["item", visible]] en orden, hermanos planos.
const _menu = __MENU__;
const _nodos = _menu.map(([tipo, v]) => tipo === 'label'
  ? Object.assign(_el('nav-section-label', true), { nombre: v })
  : _el('nav-item', v));
_nodos.forEach((n, i) => { n.nextElementSibling = _nodos[i + 1] || null; });
globalThis.document = {
  querySelectorAll: sel => sel === '.nav-scroll .nav-section-label'
    ? _nodos.filter(n => n.classList.contains('nav-section-label')) : [],
};
__FUNCION__
_ocultarGruposVacios();
console.log(JSON.stringify(Object.fromEntries(
  _nodos.filter(n => n.nombre).map(n => [n.nombre, n.style.display]))));
"""


def _correr(tmp_path, menu):
    js = _ARNES.replace("__MENU__", json.dumps(menu)).replace("__FUNCION__", _funcion())
    archivo = tmp_path / "menu.js"
    archivo.write_text(js, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@sin_node
def test_un_programador_sin_finanzas_no_ve_el_titulo(tmp_path):
    menu = [["label", "CALENDARIO"], ["item", True],
            ["label", "FINANZAS"], ["item", False], ["item", False],
            ["label", "VENTAS"], ["item", True], ["item", False], ["item", False],
            ["label", "CAPTACIÓN"], ["item", False], ["item", False], ["item", True]]
    assert _correr(tmp_path, menu) == {
        "CALENDARIO": "", "FINANZAS": "none", "VENTAS": "", "CAPTACIÓN": ""}


@sin_node
def test_el_ultimo_grupo_vacio_y_un_grupo_sin_items_tambien_se_ocultan(tmp_path):
    menu = [["label", "VACIO"],
            ["label", "MARKETING"], ["item", True],
            ["label", "CAPTACIÓN"], ["item", False], ["item", False]]
    assert _correr(tmp_path, menu) == {"VACIO": "none", "MARKETING": "", "CAPTACIÓN": "none"}


@sin_node
def test_con_todo_visible_no_se_oculta_ningun_titulo(tmp_path):
    grupos = re.findall(r'nav-section-label">([^<]+)</div>', HTML)
    assert "FINANZAS" in grupos
    menu = []
    for g in grupos:
        menu += [["label", g], ["item", True]]
    assert set(_correr(tmp_path, menu).values()) == {""}


def test_la_funcion_no_rompe_jinja_ni_usa_backslashes():
    f = _funcion()
    for trampa in ("{#", "{{", "{%"):
        assert trampa not in f
    assert chr(92) not in f


def test_la_pagina_abre(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    db = str(tmp_path / "menu.db")
    init_db(db)
    app = dashboard.create_app(db)
    uid = create_user(db, name="Jefe", email="jefe@test.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    cli = app.test_client()
    with cli.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Jefe"
    r = cli.get("/")
    assert r.status_code == 200
    assert b"_ocultarGruposVacios" in r.data
