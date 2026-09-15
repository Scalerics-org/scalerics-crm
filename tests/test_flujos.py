"""El bloque Flujos, al final de la pantalla Ausencias (PDF "Flujos - Scalerics").

Criterios de aceptación del PDF, cada uno con su test:
1 aparece al final de la pantalla, debajo de las ausencias · 2 ningún nombre
propio, solo roles · 3 cambio el título en la base y la pantalla lo refleja ·
4 reordeno y la numeración se corrige sola · 5 un paso con pantalla lleva a
esa sección · 6 los otros tres flujos existen y muestran su estado vacío.
Además: solo lectura para quien no es administrador, y más de un momento de
cobro por paso.
"""

import json
import re
import sqlite3

import pytest

import database
from database import init_db, listar_personas_equipo
from services.flujos import ROLES, estado_flujos
from tests.test_equipo import (AUSENCIAS, HTML, MODALES, SRC, _cli, _correr_js,  # noqa: F401
                               _entre, _rol, _usuario, app, cli, sin_node)

# Texto exacto del PDF, página 2.
PASOS_PDF = [
    ("Se genera el lead", "Marketing", "Meta Ads u Outbound. Cae en Proceso de venta."),
    ("Se atiende el lead", "Comercial", "Primer llamado. Se califica y se carga el seguimiento."),
    ("Se lleva a videollamada", "Comercial", "Plantilla de confirmación. Recordatorio automático el mismo día."),
    ("Se prepara la demo", "Project manager", "Se arma sobre el rubro y lo que pidió el lead."),
    ("Se hace la demo", "Project manager", "Queda registrada en Demos, con lo que pidió y lo que objetó."),
    ("Se presupuesta", "Comercial", "Dentro de 48 horas. Plantilla de resumen y presupuesto."),
    ("Se cobra", "Administración", "Al confirmar. Se dan de alta el cliente y el proyecto."),
    ("Se desarrolla", "Desarrollo", "El plazo corre desde que llega el material."),
    ("Se entrega", "Desarrollo", "Publicación y capacitación. Se ofrece el mantenimiento."),
    ("Se mantiene", "Soporte", "Cuota mensual. Es el ingreso que se acumula mes a mes."),
]
PANTALLAS = {1: "notion_clients", 5: "demos", 7: "clientes", 8: "projects"}
FLUJOS_PDF = ["De lead a cobro", "Arranque de proyecto", "Cobranza", "Alta de una persona"]

BLOQUE = _entre(AUSENCIAS, "<!-- Flujos:", "</section>")
MODAL_PASO = MODALES[MODALES.index('id="eq-modal-paso"'):]
JS_FLUJOS = _entre(SRC, "// ── flujos ──", "// ========== Simulador financiero ==========")


def _flujos(c):
    r = c.get("/api/flujos")
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def _lead_a_cobro(c):
    return next(f for f in _flujos(c)["flujos"] if f["nombre"] == "De lead a cobro")


def _paso(c, titulo):
    return next(p for p in _lead_a_cobro(c)["pasos"] if p["titulo"] == titulo)


def _db(c):
    return c.application.config["_DB"]


def _sql(db, sql, params=()):
    conn = sqlite3.connect(db)
    try:
        filas = conn.execute(sql, params).fetchall()
        conn.commit()
        return filas
    finally:
        conn.close()


# ── precarga ─────────────────────────────────────────────────────────────────

def test_la_precarga_trae_cuatro_flujos_y_los_diez_pasos_del_pdf(tmp_path):
    db = str(tmp_path / "f.db")
    for _ in range(3):
        init_db(db)
    flujos = estado_flujos(db)
    assert [f["nombre"] for f in flujos] == FLUJOS_PDF
    lead = flujos[0]
    assert [(p["titulo"], p["rol"], p["detalle"]) for p in lead["pasos"]] == PASOS_PDF
    assert [p["numero"] for p in lead["pasos"]] == list(range(1, 11))
    assert {p["numero"]: p["pantalla"] for p in lead["pasos"] if p["pantalla"]} == PANTALLAS
    assert [p["numero"] for p in lead["pasos"] if p["destacado"]] == [10]
    cobros = {p["numero"]: [(c["porcentaje"], c["descripcion"]) for c in p["cobros"]]
              for p in lead["pasos"] if p["cobros"]}
    assert cobros == {7: [(100, "al confirmar")]}
    assert all(f["pasos"] == [] for f in flujos[1:])
    assert all(p["rol"] in ROLES for p in lead["pasos"])
    assert _sql(db, "SELECT COUNT(*) FROM flujos")[0][0] == 4
    assert _sql(db, "SELECT COUNT(*) FROM flujo_pasos")[0][0] == 10
    assert _sql(db, "SELECT COUNT(*) FROM flujo_paso_cobros")[0][0] == 1


def test_la_precarga_no_pisa_lo_editado(tmp_path):
    db = str(tmp_path / "f.db")
    init_db(db)
    pasos = estado_flujos(db)[0]["pasos"]
    _sql(db, "UPDATE flujo_pasos SET titulo='Entra el lead' WHERE id=?", (pasos[0]["id"],))
    database.borrar_paso_flujo(db, pasos[5]["id"])
    database.mover_paso_flujo(db, pasos[9]["id"], 1)
    antes = estado_flujos(db)
    init_db(db)
    assert estado_flujos(db) == antes
    assert antes[0]["pasos"][0]["titulo"] == "Se mantiene" and len(antes[0]["pasos"]) == 9


def test_los_pasos_estan_en_la_base_y_no_en_la_pantalla():
    for titulo, _rol_, detalle in PASOS_PDF:
        assert titulo not in BLOQUE + JS_FLUJOS + MODAL_PASO
        assert detalle not in BLOQUE + JS_FLUJOS + MODAL_PASO


# ── criterio 1: al final de Ausencias ────────────────────────────────────────

def test_c1_flujos_va_al_final_de_ausencias_y_no_en_el_menu(cli):
    pagina = cli.get("/").get_data(as_text=True)
    assert pagina.count('id="eq-flujos-pasos"') == 1
    assert AUSENCIAS.index('id="eq-detalle"') < AUSENCIAS.index('id="eq-titulo-flujos"')
    despues = AUSENCIAS[AUSENCIAS.index('id="eq-flujos-pasos"'):]
    assert "eq-card" not in despues, "Flujos tiene que ser lo último de la pantalla"
    assert ">Flujos</div>" in BLOQUE and 'class="eq-flujos-bajada"' in BLOQUE
    menu = HTML[HTML.index('<div class="nav-scroll">'):HTML.index('<div class="sidebar-bottom">')]
    assert "flujo" not in menu.lower()
    assert "if (name === 'ausencias') eqCargarFlujos();" in pagina


# ── criterio 2: sin nombres propios ──────────────────────────────────────────

def _nombres(db):
    palabras = set()
    for p in listar_personas_equipo(db, incluir_inactivas=True):
        palabras.add(p["nombre"])
        palabras.update(w for w in p["nombre"].split() if len(w) >= 3)
    return palabras


def _con_nombres(texto, nombres):
    return sorted(n for n in nombres if re.search(r"\b" + re.escape(n) + r"\b", texto, re.I))


def test_c2_ningun_nombre_propio_en_el_bloque(cli):
    nombres = _nombres(_db(cli))
    assert "Gonzalo" in nombres and "Pereyra" in nombres
    precarga = json.dumps(database._FLUJOS_PRECARGA, ensure_ascii=False)
    api = json.dumps(_flujos(cli), ensure_ascii=False)
    for nombre, texto in (("pantalla", BLOQUE), ("modal", MODAL_PASO), ("js", JS_FLUJOS),
                          ("precarga", precarga), ("api", api)):
        assert not _con_nombres(texto, nombres), (nombre, _con_nombres(texto, nombres))


# ── criterio 3: la base manda ────────────────────────────────────────────────

def test_c3_cambiar_el_titulo_en_la_base_se_ve_en_la_api(cli):
    paso = _paso(cli, "Se presupuesta")
    _sql(_db(cli), "UPDATE flujo_pasos SET titulo='Se manda el presupuesto' WHERE id=?", (paso["id"],))
    titulos = [p["titulo"] for p in _lead_a_cobro(cli)["pasos"]]
    assert "Se manda el presupuesto" in titulos and "Se presupuesta" not in titulos


# ── criterio 4: renumerado ───────────────────────────────────────────────────

def _titulos_y_numeros(c):
    return [(p["numero"], p["titulo"]) for p in _lead_a_cobro(c)["pasos"]]


def test_c4_reordenar_renumera_solo(cli):
    cobra = _paso(cli, "Se cobra")
    r = cli.post(f"/api/flujos/pasos/{cobra['id']}/mover", json={"delta": -1})
    assert r.status_code == 200 and r.get_json()["numero"] == 6
    orden = _titulos_y_numeros(cli)
    assert [n for n, _ in orden] == list(range(1, 11))
    assert orden[5] == (6, "Se cobra") and orden[6] == (7, "Se presupuesta")

    mantiene = _paso(cli, "Se mantiene")
    assert cli.post(f"/api/flujos/pasos/{mantiene['id']}/mover", json={"posicion": 1}).get_json()["numero"] == 1
    assert _titulos_y_numeros(cli)[0] == (1, "Se mantiene")
    assert [n for n, _ in _titulos_y_numeros(cli)] == list(range(1, 11))
    # Arriba de todo no sube más, abajo de todo no baja más.
    assert cli.post(f"/api/flujos/pasos/{mantiene['id']}/mover", json={"delta": -1}).get_json()["numero"] == 1
    assert cli.post(f"/api/flujos/pasos/{mantiene['id']}/mover", json={"posicion": 99}).get_json()["numero"] == 10


def test_un_hueco_en_la_base_se_corrige_al_primer_cambio_y_borrar_renumera(cli):
    db = _db(cli)
    entrega = _paso(cli, "Se entrega")
    _sql(db, "UPDATE flujo_pasos SET numero=40 WHERE id=?", (entrega["id"],))
    assert [p["titulo"] for p in _lead_a_cobro(cli)["pasos"]][-1] == "Se entrega"
    assert cli.delete(f"/api/flujos/pasos/{_paso(cli, 'Se hace la demo')['id']}").status_code == 200
    assert [n for n, _ in _titulos_y_numeros(cli)] == list(range(1, 10))
    assert _titulos_y_numeros(cli)[-1] == (9, "Se entrega")
    assert cli.delete(f"/api/flujos/pasos/{entrega['id']}").status_code == 200
    assert cli.delete(f"/api/flujos/pasos/{entrega['id']}").status_code == 404


def test_agregar_y_editar_sin_tocar_codigo(cli):
    lead = _lead_a_cobro(cli)
    r = cli.post(f"/api/flujos/{lead['id']}/pasos",
                 json={"titulo": "Se pide la reseña", "rol": "Soporte", "detalle": "A los 30 días."})
    assert r.status_code == 201, r.get_json()
    nuevo = _paso(cli, "Se pide la reseña")
    assert nuevo["numero"] == 11 and nuevo["pantalla"] is None and nuevo["destacado"] is False

    r = cli.put(f"/api/flujos/pasos/{nuevo['id']}",
                json={"titulo": "Se pide una reseña", "rol": "Comercial", "detalle": "",
                      "pantalla": "clientes", "destacado": True})
    assert r.status_code == 200
    editado = _paso(cli, "Se pide una reseña")
    assert (editado["rol"], editado["pantalla"], editado["destacado"], editado["numero"]) == (
        "Comercial", "clientes", True, 11)


@pytest.mark.parametrize("cambio,parte_del_error", [
    ({"titulo": "  "}, "título"),
    ({"rol": "Juan"}, "rol"),
    ({"rol": "Gerente"}, "rol"),
    ({"pantalla": "../admin"}, "pantalla"),
    ({"detalle": "x" * 241}, "detalle"),
    ({"titulo": 7}, "título"),
    ({"cobros": [{"porcentaje": 60, "descripcion": "a"}, {"porcentaje": 50, "descripcion": "b"}]}, "100"),
    ({"cobros": [{"porcentaje": 0, "descripcion": "a"}]}, "porcentaje"),
    ({"cobros": [{"porcentaje": 50, "descripcion": ""}]}, "descripción"),
    ({"cobros": "todo"}, "lista"),
])
def test_el_paso_se_valida_en_el_servidor(cli, cambio, parte_del_error):
    lead = _lead_a_cobro(cli)
    datos = {"titulo": "Nuevo", "rol": "Comercial", **cambio}
    r = cli.post(f"/api/flujos/{lead['id']}/pasos", json=datos)
    assert r.status_code == 400
    assert parte_del_error in r.get_json()["error"]
    assert len(_lead_a_cobro(cli)["pasos"]) == 10


def test_flujo_o_paso_que_no_existe_da_404(cli):
    assert cli.post("/api/flujos/999/pasos", json={"titulo": "x", "rol": "Soporte"}).status_code == 404
    assert cli.put("/api/flujos/pasos/999", json={"titulo": "x", "rol": "Soporte"}).status_code == 404
    assert cli.post("/api/flujos/pasos/999/mover", json={"delta": 1}).status_code == 404
    paso = _paso(cli, "Se cobra")
    assert cli.post(f"/api/flujos/pasos/{paso['id']}/mover", json={"delta": 2}).status_code == 400


# ── solo lectura para quien no es administrador ──────────────────────────────

def test_quien_no_es_admin_lee_pero_recibe_403_al_editar(app):
    db = app.config["_DB"]
    c = _cli(app, _usuario(db, "ausencias@scalerics.com", _rol(db, "SoloAusencias", ["ausencias"])))
    d = _flujos(c)
    assert d["es_admin"] is False and len(d["flujos"]) == 4
    lead = next(f for f in d["flujos"] if f["nombre"] == "De lead a cobro")
    pid = lead["pasos"][0]["id"]
    assert c.post(f"/api/flujos/{lead['id']}/pasos", json={"titulo": "x", "rol": "Soporte"}).status_code == 403
    assert c.put(f"/api/flujos/pasos/{pid}", json={"titulo": "x", "rol": "Soporte"}).status_code == 403
    assert c.post(f"/api/flujos/pasos/{pid}/mover", json={"delta": 1}).status_code == 403
    assert c.delete(f"/api/flujos/pasos/{pid}").status_code == 403
    assert estado_flujos(db) == d["flujos"], "nada cambió"

    organigrama = _cli(app, _usuario(db, "org@scalerics.com", _rol(db, "SoloOrg", ["equipo"])))
    assert organigrama.get("/api/flujos").status_code == 200
    ajeno = _cli(app, _usuario(db, "cola@scalerics.com", _rol(db, "SoloCola", ["cola"])))
    assert ajeno.get("/api/flujos").status_code == 403


def test_el_admin_se_decide_igual_que_en_api_me(app):
    db = app.config["_DB"]
    por_rol = _cli(app, _usuario(db, "rol@scalerics.com", _rol(db, "admin", ["cola"])))
    assert por_rol.get("/api/me").get_json()["is_admin"] is True
    assert _flujos(por_rol)["es_admin"] is True
    lead = next(f for f in _flujos(por_rol)["flujos"] if f["nombre"] == "De lead a cobro")
    assert por_rol.post(f"/api/flujos/{lead['id']}/pasos",
                        json={"titulo": "x", "rol": "Soporte"}).status_code == 201


def test_sin_sesion_no_entra(app):
    assert app.test_client().get("/api/flujos").status_code in (401, 302)


# ── criterio 6: flujos vacíos ────────────────────────────────────────────────

def test_c6_los_otros_tres_flujos_existen_vacios(cli):
    flujos = _flujos(cli)["flujos"]
    assert [f["nombre"] for f in flujos] == FLUJOS_PDF
    assert [len(f["pasos"]) for f in flujos] == [10, 0, 0, 0]
    cobranza = next(f for f in flujos if f["nombre"] == "Cobranza")
    r = cli.post(f"/api/flujos/{cobranza['id']}/pasos", json={"titulo": "Se revisa lo pendiente", "rol": "Administración"})
    assert r.status_code == 201
    assert [p["numero"] for p in next(f for f in _flujos(cli)["flujos"] if f["nombre"] == "Cobranza")["pasos"]] == [1]


# ── nota del paso 07: más de un momento de cobro ─────────────────────────────

def test_un_paso_puede_tener_varios_momentos_de_cobro(cli):
    cobra = _paso(cli, "Se cobra")
    base = {"titulo": cobra["titulo"], "rol": cobra["rol"], "detalle": cobra["detalle"],
            "pantalla": cobra["pantalla"], "destacado": cobra["destacado"]}
    r = cli.put(f"/api/flujos/pasos/{cobra['id']}", json={**base, "cobros": [
        {"porcentaje": 50, "descripcion": "al inicio"}, {"porcentaje": "50", "descripcion": "contra entrega"}]})
    assert r.status_code == 200, r.get_json()
    assert [(c["porcentaje"], c["descripcion"]) for c in _paso(cli, "Se cobra")["cobros"]] == [
        (50, "al inicio"), (50, "contra entrega")]

    # Una edición que no manda cobros no los borra.
    assert cli.put(f"/api/flujos/pasos/{cobra['id']}", json={**base, "detalle": "Otro detalle."}).status_code == 200
    assert len(_paso(cli, "Se cobra")["cobros"]) == 2
    # Con lista vacía, sí.
    assert cli.put(f"/api/flujos/pasos/{cobra['id']}", json={**base, "cobros": []}).status_code == 200
    assert _paso(cli, "Se cobra")["cobros"] == []
    assert _sql(_db(cli), "SELECT COUNT(*) FROM flujo_paso_cobros")[0][0] == 0


def test_borrar_un_paso_se_lleva_sus_cobros(cli):
    cobra = _paso(cli, "Se cobra")
    assert cli.delete(f"/api/flujos/pasos/{cobra['id']}").status_code == 200
    assert _sql(_db(cli), "SELECT COUNT(*) FROM flujo_paso_cobros")[0][0] == 0


# ── criterio 5 y pintado: node con DOM falso ─────────────────────────────────

_PRUEBA_PINTADO = """
(async () => {
  const s = {};
  const llamados = [];
  showPanel('ausencias');
  await new Promise(r => setTimeout(r, 30));
  s.selector = _el('eq-flujos-selector').innerHTML;
  s.acciones = _el('eq-flujos-acciones').innerHTML;
  s.pasos = _el('eq-flujos-pasos').innerHTML;
  s.pedidos = _pedidos.map(p => p[0]);

  // Tocar la tarjeta: se ejecuta el onclick tal como esta en el html.
  showPanel = p => llamados.push(p);
  globalThis.eqFlujoIr = eqFlujoIr;
  const li = s.pasos.split('<li ').find(x => x.indexOf('data-pantalla="clientes"') >= 0) || '';
  const onclick = (li.match(/onclick="([^"]*)"/) || [])[1] || '';
  s.onclick = onclick;
  new Function(onclick).call({dataset: {pantalla: 'clientes'}});
  eqFlujoTecla({key: 'Enter', preventDefault() {}}, 'demos');
  eqFlujoTecla({key: 'a', preventDefault() {}}, 'projects');
  s.llamados = llamados.slice();

  // Un panel que no esta en la pagina, o que el rol no ve, no es clickeable.
  const original = document.getElementById;
  document.getElementById = id => id === 'demos-panel' ? null : original(id);
  window._panelAccess = ['ausencias'];
  eqPintarFlujos();
  s.restringido = _el('eq-flujos-pasos').innerHTML;
  eqFlujoIr('clientes');
  s.llamadosRestringido = llamados.length;
  document.getElementById = original;
  window._panelAccess = null;

  __ELEGIR_VACIO__
  s.vacio = _el('eq-flujos-pasos').innerHTML;
  s.selectorVacio = _el('eq-flujos-selector').innerHTML;

  eqFlujoElegir(eqFlujos[0].id);
  eqFlujosAlternarEdicion();
  s.editando = _el('eq-flujos-pasos').innerHTML;
  s.botonEdicion = _el('eq-flujos-acciones').innerHTML;

  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
"""


def _li(html, titulo):
    return next(x for x in html.split("<li ")[1:] if f'<span class="eq-paso-titulo">{titulo}</span>' in x)


@sin_node
def test_c5_la_pantalla_se_pinta_y_una_tarjeta_con_pantalla_llama_a_showpanel(cli, tmp_path):
    datos = _flujos(cli)
    vacio = next(f for f in datos["flujos"] if f["nombre"] == "Arranque de proyecto")
    prueba = _PRUEBA_PINTADO.replace("__ELEGIR_VACIO__", f"eqFlujoElegir({vacio['id']});")
    s = _correr_js(tmp_path, {"/api/flujos": datos, "/api/equipo": {}}, prueba)

    assert "/api/flujos" in s["pedidos"]
    assert s["selector"].count('class="eq-flujo-tab') == 4
    assert s["selector"].count("eq-activo") == 1
    assert 'eq-activo" aria-pressed="true"' in s["selector"].split("</button>")[0]
    for nombre in FLUJOS_PDF:
        assert f">{nombre}</button>" in s["selector"]

    pasos = s["pasos"]
    assert pasos.startswith('<ol class="eq-pasos">') and pasos.count("<li ") == 10
    for i, (titulo, rol, detalle) in enumerate(PASOS_PDF, start=1):
        li = _li(pasos, titulo)
        assert f'<span class="eq-paso-num">{i:02d}</span><span class="eq-paso-titulo">{titulo}</span>' \
               f'<span class="eq-paso-rol">{rol}</span>' in li
        assert f'<div class="eq-paso-detalle">{detalle}</div>' in li
        assert ("eq-paso-destacado" in li) is (i == 10)
        assert (f'data-pantalla="{PANTALLAS[i]}"' in li) if i in PANTALLAS else ("data-pantalla" not in li)
        assert ("eq-paso-link" in li) is (i in PANTALLAS)
    assert "Cobro: 100% al confirmar" in _li(pasos, "Se cobra")
    assert "Ir a Clientes" in _li(pasos, "Se cobra")

    assert s["onclick"] == "eqFlujoIr(this.dataset.pantalla)"
    assert s["llamados"] == ["clientes", "demos"]

    restringido = s["restringido"]
    assert "eq-paso-link" not in _li(restringido, "Se cobra"), "clientes está en ALL_PANELS y el rol no lo tiene"
    assert "eq-paso-link" not in _li(restringido, "Se hace la demo"), "demos no está en la página"
    assert s["llamadosRestringido"] == 2

    assert "todavía no se cargaron" in s["vacio"] and "Arranque de proyecto" in s["vacio"]
    assert "Cargar el primer paso" in s["vacio"], "el admin puede cargar el primero"
    assert s["selectorVacio"].count("eq-activo") == 1

    assert ">Editar</button>" in s["acciones"]
    assert "Subir</button>" in s["editando"] and "Bajar</button>" in s["editando"]
    assert "+ Agregar paso" in s["editando"] and "eq-paso-link" not in s["editando"]
    assert ">Listo</button>" in s["botonEdicion"]

    nombres = _nombres(_db(cli))
    todo = "".join(str(v) for v in s.values())
    assert not _con_nombres(todo, nombres)


@sin_node
def test_quien_no_es_admin_no_ve_editar_ni_cargar(cli, tmp_path):
    datos = {**_flujos(cli), "es_admin": False}
    vacio = next(f for f in datos["flujos"] if f["nombre"] == "Cobranza")
    prueba = """
(async () => {
  await eqCargarFlujos();
  const s = {acciones: _el('eq-flujos-acciones').innerHTML};
  eqFlujosAlternarEdicion();
  s.pasos = _el('eq-flujos-pasos').innerHTML;
  eqFlujoElegir(__VACIO__);
  s.vacio = _el('eq-flujos-pasos').innerHTML;
  eqPasoAbrir(__VACIO__, null);
  s.modal = _el('eq-paso-titulo-modal').textContent;
  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""".replace("__VACIO__", str(vacio["id"]))
    s = _correr_js(tmp_path, {"/api/flujos": datos}, prueba)
    assert s["acciones"] == ""
    assert "Subir" not in s["pasos"] and "Agregar paso" not in s["pasos"]
    assert "todavía no se cargaron" in s["vacio"] and "Cargar el primer paso" not in s["vacio"]
    assert s["modal"] == ""


@sin_node
def test_el_formulario_arma_los_momentos_de_cobro_y_valida(cli, tmp_path):
    datos = _flujos(cli)
    lead = next(f for f in datos["flujos"] if f["nombre"] == "De lead a cobro")
    cobra = next(p for p in lead["pasos"] if p["titulo"] == "Se cobra")
    prueba = """
(async () => {
  await eqCargarFlujos();
  const s = {};
  eqPasoAbrir(__FLUJO__, __PASO__);
  s.titulo = _el('eq-paso-titulo').value;
  s.rol = _el('eq-paso-rol').value;
  s.roles = _el('eq-paso-rol').innerHTML;
  s.pantallas = _el('eq-paso-pantalla').innerHTML;
  s.cobros = _el('eq-paso-cobros').innerHTML;
  eqPasoCobroPct(0, '50');
  eqPasoCobroAgregar();
  eqPasoCobroPct(1, '60');
  eqPasoCobroDesc(1, 'contra entrega');
  const antes = _pedidos.length;
  await eqPasoGuardar();
  s.errorSuma = _el('eq-paso-error').textContent;
  eqPasoCobroPct(1, '50');
  await eqPasoGuardar();
  s.enviado = _pedidos.slice(antes).filter(p => p[1] !== 'GET');
  s.cuerpo = globalThis._ultimoCuerpo;
  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""".replace("__FLUJO__", str(lead["id"])).replace("__PASO__", str(cobra["id"]))
    captura = ("const _fetchOriginal = globalThis.fetch;\n"
               "globalThis.fetch = (url, op) => { if (op && op.body) globalThis._ultimoCuerpo = JSON.parse(op.body);"
               " return _fetchOriginal(url, op); };\n")
    s = _correr_js(tmp_path, {"/api/flujos": datos}, captura + prueba)
    assert s["titulo"] == "Se cobra" and s["rol"] == "Administración"
    for rol in ROLES:
        assert f">{rol}</option>" in s["roles"]
    assert '<option value="clientes" selected>Clientes</option>' in s["pantallas"]
    assert s["cobros"].count('class="eq-cobro-fila"') == 1 and 'value="100"' in s["cobros"]
    assert "más del 100%" in s["errorSuma"]
    assert s["enviado"] == [[f"/api/flujos/pasos/{cobra['id']}", "PUT"]]
    assert s["cuerpo"]["cobros"] == [{"porcentaje": 50, "descripcion": "al confirmar"},
                                     {"porcentaje": 50, "descripcion": "contra entrega"}]
