"""Colores en el organigrama (pedido de Juan, 16/9).

"Al organigrama ponele colores a cada uno, usá los mismos de los flujos y si
hay gente que no está en flujos ponele otro color."

Cada persona tiene `rol_flujo` (uno de los roles de Flujos, o NULL). El color
del nodo sale del MISMO mapa de `services/flujos.ROL_ESTILOS`; quien no
participa va en rosa, "Fuera de Flujos".
"""

import json
import re
import sqlite3

import pytest

import database
from database import init_db, listar_personas_equipo
from services.equipo import leyenda_organigrama, organigrama
from services.flujos import (FUERA_DE_FLUJOS, ROL_ESTILOS, ROLES, estilo_de_persona,
                             estilos_roles, validar_rol_flujo)
from tests.test_equipo import (HTML, SRC, _cli, _correr_js, _entre, _rol, _usuario,  # noqa: F401
                               app, cli, sin_node)
from tests.test_tokens_css import _contraste, _delta_e, _tokens

PRECARGA = {
    "Andrés Rosi": "Marketing",
    "Juan Pereyra": "Comercial",
    "Gonzalo Siuciak": "Project manager",
    "Juan Tomasetti": "Desarrollo",
    "Matías Domínguez": "Desarrollo",
    "Guillermo Paredes": "Administración",
    "Javier Tomasetti": None,
}
COLOR_ESPERADO = {"Andrés Rosi": "rojo", "Juan Pereyra": "verde", "Gonzalo Siuciak": "naranja",
                  "Juan Tomasetti": "azul", "Matías Domínguez": "azul",
                  "Guillermo Paredes": "violeta", "Javier Tomasetti": "rosa"}


def _roles_en_base(db):
    return {p["nombre"]: p["rol_flujo"] for p in listar_personas_equipo(db, incluir_inactivas=True)}


def _sql(db, sql, params=()):
    conn = sqlite3.connect(db)
    try:
        filas = conn.execute(sql, params).fetchall()
        conn.commit()
        return filas
    finally:
        conn.close()


def _id(db, nombre):
    return next(p["id"] for p in listar_personas_equipo(db, incluir_inactivas=True) if p["nombre"] == nombre)


# ── migración ────────────────────────────────────────────────────────────────

def test_la_precarga_pone_el_rol_de_cada_persona(tmp_path):
    db = str(tmp_path / "o.db")
    init_db(db)
    assert _roles_en_base(db) == PRECARGA
    assert all(r is None or r in ROLES for r in PRECARGA.values())


def test_la_precarga_es_idempotente_y_no_pisa_ediciones(tmp_path):
    db = str(tmp_path / "o.db")
    init_db(db)
    init_db(db)
    assert _roles_en_base(db) == PRECARGA

    _sql(db, "UPDATE equipo_personas SET rol_flujo='Comercial' WHERE nombre='Gonzalo Siuciak'")
    _sql(db, "UPDATE equipo_personas SET rol_flujo='Soporte' WHERE nombre='Javier Tomasetti'")
    _sql(db, "UPDATE equipo_personas SET rol_flujo=NULL WHERE nombre='Andrés Rosi'")
    init_db(db)
    init_db(db)
    roles = _roles_en_base(db)
    assert roles["Gonzalo Siuciak"] == "Comercial"
    assert roles["Javier Tomasetti"] == "Soporte"
    assert roles["Andrés Rosi"] is None, "sacarle el rol a mano tampoco se deshace"


def test_si_no_encuentra_a_alguien_lo_deja_sin_rol_y_avisa(tmp_path):
    db = str(tmp_path / "o.db")
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE equipo_personas SET nombre='Andres Rosi', rol_flujo=NULL WHERE nombre='Andrés Rosi'")
        conn.commit()
        assert database._precargar_rol_flujo(conn) == ["Andrés Rosi"]
        fila = conn.execute("SELECT rol_flujo FROM equipo_personas WHERE nombre='Andres Rosi'").fetchone()
        assert fila[0] is None, "no se adivina por parecido"
    finally:
        conn.close()


# ── el color sale del mapa de Flujos ─────────────────────────────────────────

def test_el_color_de_cada_nodo_es_el_de_su_rol_en_flujos(cli):
    nodos = {n["nombre"]: n for n in cli.get("/api/equipo").get_json()["organigrama"]}
    assert {n: x["color"] for n, x in nodos.items()} == COLOR_ESPERADO
    for nombre, rol in PRECARGA.items():
        nodo = nodos[nombre]
        if rol:
            assert (nodo["rol_flujo"], nodo["color"], nodo["etiqueta_flujo"]) == (
                rol, ROL_ESTILOS[rol]["color"], ROL_ESTILOS[rol]["etiqueta"])
        else:
            assert (nodo["rol_flujo"], nodo["color"], nodo["etiqueta_flujo"]) == (None, "rosa", "Fuera de Flujos")
    assert nodos["Andrés Rosi"]["etiqueta_flujo"] == "Líder marketing digital"


def test_un_rol_que_no_es_de_la_lista_cuenta_como_fuera_de_flujos():
    assert estilo_de_persona("Gerente") == {"rol": None, "color": "rosa", "etiqueta": "Fuera de Flujos"}
    assert estilo_de_persona(None)["color"] == "rosa"
    for rol in ROLES:
        assert estilo_de_persona(rol) == {"rol": rol, **ROL_ESTILOS[rol]}
    nodos = organigrama([{"id": 1, "nombre": "A", "rol": "", "reporta_a": None, "rol_flujo": "Soporte"}])
    assert nodos[0]["color"] == "teal"


# ── "Fuera de Flujos" ────────────────────────────────────────────────────────

def test_el_color_fuera_de_flujos_es_unico_y_tiene_sus_tokens():
    colores = [e["color"] for e in ROL_ESTILOS.values()]
    assert FUERA_DE_FLUJOS == {"color": "rosa", "etiqueta": "Fuera de Flujos"}
    assert FUERA_DE_FLUJOS["color"] not in colores
    assert ".eq-rol-rosa{--rol-c:var(--rol-rosa);--rol-t:var(--rol-rosa-tinte)}" in HTML
    for selector in (":root", "body.light"):
        t = _tokens(selector)
        assert "--rol-rosa" in t and "--rol-rosa-tinte" in t, selector


@pytest.mark.parametrize("selector,tema", [(":root", "oscuro"), ("body.light", "claro")])
def test_el_rosa_se_lee_y_no_se_confunde_con_los_roles(selector, tema):
    t = _tokens(selector)
    pleno, tinte = t["--rol-rosa"], t["--rol-rosa-tinte"]
    for texto, fondo, que in ((pleno, tinte, "el cargo sobre el tinte"),
                              (t["--texto-fuerte"], tinte, "el nombre sobre el tinte"),
                              (pleno, t["--superficie"], "el color sobre la superficie")):
        c = _contraste(texto, fondo)
        assert c >= 4.5, f"{que} en {tema}: {c:.2f}:1"
    assert _delta_e(tinte, t["--superficie"]) >= 4
    for e in ROL_ESTILOS.values():
        assert _delta_e(pleno, t[f"--rol-{e['color']}"]) >= 10, f"rosa vs {e['color']} en {tema}"
        assert _delta_e(tinte, t[f"--rol-{e['color']}-tinte"]) >= 4, f"tinte rosa vs {e['color']} en {tema}"


def test_los_nodos_toman_el_color_por_token():
    css = _entre(SRC, "/* ── Equipo", "/* ── Simulador financiero")
    rect = re.search(r"^\.eq-nodo rect\{([^}]*)\}", css, re.M).group(1)
    assert "fill:var(--rol-t," in rect and "stroke:var(--rol-c," in rect
    assert "fill:var(--rol-c," in re.search(r"^\.eq-nodo-rol\{([^}]*)\}", css, re.M).group(1)
    destacado = re.search(r"^\.eq-destacado rect\{([^}]*)\}", css, re.M).group(1)
    assert "stroke-width" in destacado and "var(--azul" not in destacado, "el CTO conserva su color de rol"


# ── leyenda ──────────────────────────────────────────────────────────────────

def test_la_leyenda_muestra_solo_los_colores_presentes(cli):
    d = cli.get("/api/equipo").get_json()
    assert d["leyenda_organigrama"] == [
        {"color": "rojo", "etiqueta": "Líder marketing digital"}, {"color": "verde", "etiqueta": "Comercial"},
        {"color": "naranja", "etiqueta": "Project manager"}, {"color": "azul", "etiqueta": "Desarrollo"},
        {"color": "violeta", "etiqueta": "Administración"}, {"color": "rosa", "etiqueta": "Fuera de Flujos"}]
    assert d["roles_flujo"] == estilos_roles() and d["fuera_de_flujos"] == FUERA_DE_FLUJOS

    javier = _id(cli.application.config["_DB"], "Javier Tomasetti")
    assert cli.put(f"/api/equipo/personas/{javier}/rol-flujo", json={"rol_flujo": "Soporte"}).status_code == 200
    colores = [e["color"] for e in cli.get("/api/equipo").get_json()["leyenda_organigrama"]]
    assert colores == ["rojo", "verde", "naranja", "azul", "violeta", "teal"], "sin nadie fuera, no hay rosa"
    assert leyenda_organigrama([]) == []


# ── edición ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cuerpo,parte", [
    ({"rol_flujo": "Gerente"}, "tiene que ser uno de"),
    ({"rol_flujo": "marketing"}, "tiene que ser uno de"),
    ({"rol_flujo": ["Soporte"]}, "tiene que ser uno de"),
    ({"rol_flujo": 3}, "tiene que ser uno de"),
    ({}, "falta"),
    (None, "falta"),
])
def test_el_rol_se_valida_en_el_servidor(cli, cuerpo, parte):
    db = cli.application.config["_DB"]
    gonzalo = _id(db, "Gonzalo Siuciak")
    url = f"/api/equipo/personas/{gonzalo}/rol-flujo"
    r = cli.put(url, json=cuerpo) if cuerpo is not None else cli.put(url, data="no es json")
    assert r.status_code == 400 and parte in r.get_json()["error"], r.get_json()
    assert _roles_en_base(db)["Gonzalo Siuciak"] == "Project manager"


def test_el_admin_cambia_el_rol_y_no_participa(cli):
    db = cli.application.config["_DB"]
    gonzalo = _id(db, "Gonzalo Siuciak")
    url = f"/api/equipo/personas/{gonzalo}/rol-flujo"
    r = cli.put(url, json={"rol_flujo": "Soporte"})
    assert r.status_code == 200 and r.get_json() == {"ok": True, "rol_flujo": "Soporte", "color": "teal",
                                                      "etiqueta": "Soporte"}
    assert _roles_en_base(db)["Gonzalo Siuciak"] == "Soporte"
    for vacio in (None, ""):
        r = cli.put(url, json={"rol_flujo": vacio})
        assert r.status_code == 200 and r.get_json()["color"] == "rosa"
        assert _roles_en_base(db)["Gonzalo Siuciak"] is None
    assert cli.put("/api/equipo/personas/9999/rol-flujo", json={"rol_flujo": "Soporte"}).status_code == 404
    accion = _sql(db, "SELECT action, entity_name FROM activity_log ORDER BY id DESC LIMIT 1")[0]
    assert accion == ("equipo_rol_flujo", "Gonzalo Siuciak")
    assert cli.get("/api/equipo").get_json()["es_admin"] is True


def test_solo_el_admin_cambia_el_rol(app):
    db = app.config["_DB"]
    gonzalo = _id(db, "Gonzalo Siuciak")
    url = f"/api/equipo/personas/{gonzalo}/rol-flujo"
    con_panel = _cli(app, _usuario(db, "org@scalerics.com", _rol(db, "SoloOrg", ["equipo"])))
    assert con_panel.get("/api/equipo").get_json()["es_admin"] is False
    assert con_panel.put(url, json={"rol_flujo": "Soporte"}).status_code == 403
    sin_panel = _cli(app, _usuario(db, "cola@scalerics.com", _rol(db, "SoloCola", ["cola"])))
    assert sin_panel.put(url, json={"rol_flujo": "Soporte"}).status_code == 403
    assert app.test_client().put(url, json={"rol_flujo": "Soporte"}).status_code in (401, 302)
    assert _roles_en_base(db)["Gonzalo Siuciak"] == "Project manager"


# ── se pinta ─────────────────────────────────────────────────────────────────

_CAPTURA = ("const _fetchOriginal = globalThis.fetch;\n"
            "globalThis.fetch = (url, op) => { if (op && op.body) globalThis._ultimoCuerpo = JSON.parse(op.body);"
            " return _fetchOriginal(url, op); };\n")


@sin_node
def test_el_organigrama_se_pinta_con_colores_leyenda_y_edicion(cli, tmp_path):
    db = cli.application.config["_DB"]
    datos = cli.get("/api/equipo").get_json()
    javier = _id(db, "Javier Tomasetti")
    url = f"/api/equipo/personas/{javier}/rol-flujo"
    prueba = """
(async () => {
  await loadEquipo();
  const s = {org: _el('eq-organigrama').innerHTML, leyenda: _el('eq-org-leyenda').innerHTML};
  eqOrgEditar(__JAVIER__);
  s.abierto = _el('eq-modal-rol').classList.contains ? true : true;
  s.persona = _el('eq-rol-persona').textContent;
  s.opciones = _el('eq-rol-select').innerHTML;
  s.muestra = _el('eq-rol-muestra').innerHTML;
  _el('eq-rol-select').value = 'Gerente';
  const antes = _pedidos.length;
  await eqOrgGuardarRol();
  s.errorInvalido = _el('eq-rol-error').textContent;
  s.putsInvalidos = _pedidos.slice(antes).filter(p => p[1] === 'PUT').length;
  _el('eq-rol-select').value = 'Soporte';
  eqOrgRolMuestra();
  s.muestraSoporte = _el('eq-rol-muestra').innerHTML;
  await eqOrgGuardarRol();
  s.put = _pedidos.filter(p => p[1] === 'PUT').pop();
  s.cuerpo = globalThis._ultimoCuerpo;
  s.ultimo = _pedidos[_pedidos.length - 1][0];
  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""".replace("__JAVIER__", str(javier))
    s = _correr_js(tmp_path, {"/api/equipo": datos, url: {"ok": True}}, _CAPTURA + prueba)

    org = s["org"]
    assert org.startswith("<svg") and org.count('<g class="eq-nodo eq-rol-') == 7

    def nodo(nombre):
        return next(g for g in org.split("<g ")[1:] if f">{nombre}</text>" in g)

    for nombre, color in COLOR_ESPERADO.items():
        assert nodo(nombre).startswith(f'class="eq-nodo eq-rol-{color}'), nombre
    assert nodo("Matías Domínguez").startswith('class="eq-nodo eq-rol-azul eq-destacado eq-nodo-editable"')
    assert org.count("eq-destacado") == 1
    assert "Líder marketing digital" in nodo("Andrés Rosi") and "Fuera de Flujos" in nodo("Javier Tomasetti")
    assert 'onclick="eqOrgEditar(Number(this.dataset.persona))"' in nodo("Juan Pereyra")

    ley = s["leyenda"]
    chips = re.findall(r'<span class="eq-rol-chip eq-rol-(\w+)"><i class="eq-rol-punto" aria-hidden="true"></i>'
                       r'([^<]+)</span>', ley)
    assert chips == [("rojo", "Líder marketing digital"), ("verde", "Comercial"), ("naranja", "Project manager"),
                     ("azul", "Desarrollo"), ("violeta", "Administración"), ("rosa", "Fuera de Flujos")]
    assert "Tocá a una persona para cambiar su rol en Flujos." in ley

    assert s["persona"].startswith("Javier Tomasetti")
    for e in estilos_roles():
        assert f'<option value="{e["rol"]}">{e["etiqueta"]}</option>' in s["opciones"]
    assert '<option value="" selected>No participa</option>' in s["opciones"]
    assert "eq-rol-rosa" in s["muestra"] and "Fuera de Flujos" in s["muestra"]
    assert "Elegí un rol" in s["errorInvalido"] and s["putsInvalidos"] == 0
    assert "eq-rol-teal" in s["muestraSoporte"]
    assert s["put"][:2] == [url, "PUT"] and s["cuerpo"] == {"rol_flujo": "Soporte"}
    assert s["ultimo"] == "/api/equipo", "guarda y recarga el organigrama"


@sin_node
def test_quien_no_es_admin_ve_los_colores_pero_no_edita(cli, tmp_path):
    datos = {**cli.get("/api/equipo").get_json(), "es_admin": False}
    prueba = """
(async () => {
  await loadEquipo();
  const s = {org: _el('eq-organigrama').innerHTML, leyenda: _el('eq-org-leyenda').innerHTML};
  eqOrgEditar((__DATOS__).organigrama[0].id);
  s.persona = _el('eq-rol-persona').textContent;
  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""".replace("__DATOS__", json.dumps(datos, ensure_ascii=False))
    s = _correr_js(tmp_path, {"/api/equipo": datos}, prueba)
    assert s["org"].count('<g class="eq-nodo eq-rol-') == 7
    assert "eq-nodo-editable" not in s["org"] and "onclick" not in s["org"]
    assert "eq-rol-chip" in s["leyenda"] and "Tocá a una persona" not in s["leyenda"]
    assert s["persona"] == "", "sin admin no se abre la edición"


def test_la_pagina_abre(cli):
    r = cli.get("/")
    assert r.status_code == 200
    pagina = r.get_data(as_text=True)
    assert pagina.count('id="eq-modal-rol"') == 1 and pagina.count('id="eq-org-leyenda"') == 1
