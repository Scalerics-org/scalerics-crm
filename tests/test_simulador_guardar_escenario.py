"""Abrir un escenario guardado, editarlo y guardarlo de nuevo lo corrige.

Pedido de Juan (15/9): "si lo editas y lo guardas de nuevo, se guarde el cambio
y quede corregido con la ultima modificacion".

La causa: el panel decidia entre actualizar y crear comparando el texto del
nombre con el del escenario abierto. Si no era identico (le cambiaste el
nombre, lo elegiste en la lista sin tocar "Abrir", recargaste la pagina) creaba
OTRO escenario en silencio, con un aviso casi igual, y el original quedaba con
los valores viejos. Ahora manda el id del escenario abierto: "Guardar" siempre
actualiza ese, y "Guardar como nuevo" es el unico camino que crea otro.
"""

import json
import re
import shutil
import sqlite3
import subprocess

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, get_escenario, init_db, listar_escenarios

HTML = dashboard.DASHBOARD_HTML

sin_node = pytest.mark.skipif(shutil.which("node") is None,
                              reason="node no esta instalado")

DATOS = {"version": 1, "ventas": {"webs": 2},
         "cobros": {"mesesEntrega": 1, "formas": {"web": "todo", "ecommerce": "mitad",
                                                  "aMedida": "mitad"}}}


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "sim_guardar.db")
    init_db(ruta)
    return ruta


@pytest.fixture
def app(db, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "raiz@scalerics.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    return dashboard.create_app(db)


def _cliente(app, db, email, paneles=None, rol=None):
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    conn = sqlite3.connect(db)
    try:
        if rol:
            rid = conn.execute("SELECT id FROM roles WHERE name=?", (rol,)).fetchone()[0]
            if paneles is not None:
                conn.execute("UPDATE roles SET panel_access=? WHERE id=?",
                             (json.dumps(paneles), rid))
        elif paneles is not None:
            rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?, ?)",
                               (f"rol-{email}", json.dumps(paneles))).lastrowid
        else:
            rid = None
        if rid is not None:
            conn.execute("UPDATE users SET role_id=? WHERE id=?", (rid, uid))
        conn.commit()
    finally:
        conn.close()
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = email.split("@")[0]
    return c


@pytest.fixture
def cli(app, db):
    return _cliente(app, db, "socio@scalerics.com", ["simulador"])


# ── servidor ─────────────────────────────────────────────────────────────────

def test_actualizar_cambia_los_valores_y_no_crea_otro(cli, db):
    eid = cli.post("/api/simulador/escenarios", json={"nombre": "Plan", "datos": DATOS}).get_json()["id"]

    nuevos = json.loads(json.dumps(DATOS))
    nuevos["ventas"]["webs"] = 9
    nuevos["cobros"]["mesesEntrega"] = 3
    nuevos["cobros"]["formas"]["ecommerce"] = "todo"
    r = cli.put(f"/api/simulador/escenarios/{eid}", json={"nombre": "Plan", "datos": nuevos})

    assert r.status_code == 200
    assert r.get_json()["id"] == eid and r.get_json()["nombre"] == "Plan"
    assert [e["id"] for e in listar_escenarios(db)] == [eid], "no tiene que crear otro"
    abierto = cli.get(f"/api/simulador/escenarios/{eid}").get_json()
    assert abierto["datos"] == nuevos


def test_guardar_como_nuevo_crea_otro_sin_tocar_el_original(cli, db):
    original = cli.post("/api/simulador/escenarios", json={"nombre": "Plan", "datos": DATOS}).get_json()["id"]
    copia = json.loads(json.dumps(DATOS))
    copia["ventas"]["webs"] = 5

    r = cli.post("/api/simulador/escenarios", json={"nombre": "Plan (copia)", "datos": copia})

    assert r.status_code == 201
    nuevo = r.get_json()["id"]
    assert nuevo != original
    assert r.get_json()["nombre"] == "Plan (copia)" and r.get_json()["updated_at"]
    assert json.loads(get_escenario(db, original)["datos"]) == DATOS
    assert get_escenario(db, original)["nombre"] == "Plan"
    assert len(listar_escenarios(db)) == 2


def test_actualizar_uno_que_no_existe_da_404_y_no_crea_nada(cli, db):
    r = cli.put("/api/simulador/escenarios/4242", json={"nombre": "Borrado", "datos": DATOS})
    assert r.status_code == 404
    assert listar_escenarios(db) == []


def test_actualizar_guarda_la_fecha_de_modificacion_y_la_lista_la_trae(cli, db):
    eid = cli.post("/api/simulador/escenarios", json={"nombre": "Plan", "datos": DATOS}).get_json()["id"]
    conn = sqlite3.connect(db)
    conn.execute("UPDATE simulador_escenarios SET updated_at='2020-01-01 00:00:00' WHERE id=?", (eid,))
    conn.commit()
    conn.close()

    r = cli.put(f"/api/simulador/escenarios/{eid}", json={"nombre": "Plan", "datos": DATOS})

    fecha = r.get_json()["updated_at"]
    assert fecha and fecha > "2020-01-01 00:00:00"
    assert cli.get("/api/simulador/escenarios").get_json()[0]["updated_at"] == fecha


def test_sin_el_panel_no_actualiza(app, db, cli):
    eid = cli.post("/api/simulador/escenarios", json={"nombre": "Plan", "datos": DATOS}).get_json()["id"]
    otro = _cliente(app, db, "caller@scalerics.com", ["finanzas", "cola"])

    assert otro.put(f"/api/simulador/escenarios/{eid}",
                    json={"nombre": "Pisado", "datos": {}}).status_code == 403
    assert get_escenario(db, eid)["nombre"] == "Plan"


def test_el_contador_abre_edita_y_guarda_escenarios(app, db):
    """Finanzas en solo lectura no alcanza al simulador: es un panel aparte."""
    contador = _cliente(app, db, "contador@scalerics.com",
                        ["cal", "finanzas", "simulador"], rol="Contador")
    eid = contador.post("/api/simulador/escenarios", json={"nombre": "Plan", "datos": DATOS}).get_json()["id"]
    r = contador.put(f"/api/simulador/escenarios/{eid}",
                     json={"nombre": "Plan", "datos": {"version": 1, "ventas": {"webs": 7}}})
    assert r.status_code == 200
    assert json.loads(get_escenario(db, eid)["datos"])["ventas"]["webs"] == 7


def test_la_pagina_principal_carga_con_los_botones_nuevos(app, db):
    jefe = _cliente(app, db, "raiz@scalerics.com")
    r = jefe.get("/")
    assert r.status_code == 200
    assert b'id="sim-editando"' in r.data
    assert b'onclick="simGuardarComoNuevo()"' in r.data
    assert b">Nuevo escenario</button>" in r.data


# ── panel, contra un DOM falso ───────────────────────────────────────────────

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
let _confirmar = true;
const _confirms = [];
globalThis.confirm = (t) => { _confirms.push(t); return _confirmar; };
let _respuestaPrompt = null;
const _prompts = [];
globalThis.prompt = (t, d) => { _prompts.push([t, d]); return _respuestaPrompt; };
globalThis.setInterval = () => 0;

// Un servidor en memoria con las mismas respuestas que routes/simulador.py.
const _db = {};
let _proximo = 1;
let _reloj = 10;
const _pedidos = [];
function _ahora() { _reloj += 1; return '2026-09-15 17:' + _reloj + ':00'; }
globalThis.fetch = (url, opt) => {
  const metodo = (opt && opt.method) || 'GET';
  url = String(url);
  const resp = (status, cuerpo) => Promise.resolve({ok: status < 300, status,
                                                    json: () => Promise.resolve(cuerpo)});
  if (url.indexOf('/api/simulador/') === 0) _pedidos.push(metodo + ' ' + url);
  if (url === '/api/simulador/precarga')
    return resp(200, {moneda: 'USD', cajaActual: 0, gastosFijos: [], mantenimientos: [], pendientes: []});
  if (url === '/api/simulador/escenarios' && metodo === 'GET')
    return resp(200, Object.values(_db).map(e => ({id: e.id, nombre: e.nombre, updated_at: e.updated_at})));
  if (url === '/api/simulador/escenarios' && metodo === 'POST') {
    const b = JSON.parse(opt.body);
    const id = _proximo++;
    _db[id] = {id, nombre: b.nombre, datos: JSON.stringify(b.datos), updated_at: _ahora()};
    return resp(201, {ok: true, id, nombre: b.nombre, updated_at: _db[id].updated_at});
  }
  const partes = url.split('/');
  if (url.indexOf('/api/simulador/escenarios/') === 0) {
    const id = Number(partes[partes.length - 1]);
    const e = _db[id];
    if (!e) return resp(404, {ok: false, error: 'no existe'});
    if (metodo === 'GET') return resp(200, {id, nombre: e.nombre, updated_at: e.updated_at, datos: JSON.parse(e.datos)});
    if (metodo === 'PUT') {
      const b = JSON.parse(opt.body);
      e.nombre = b.nombre; e.datos = JSON.stringify(b.datos); e.updated_at = _ahora();
      return resp(200, {ok: true, id, nombre: e.nombre, updated_at: e.updated_at});
    }
    if (metodo === 'DELETE') { delete _db[id]; return resp(200, {ok: true}); }
  }
  return resp(200, {});
};
async function _recargarPagina() {
  simIniciado = false;
  await loadSimulador();
  await new Promise(r => setTimeout(r, 0));
}
async function _abrir(id) {
  _el('sim-escenarios').value = String(id);
  await simAbrir();
}
function assert(c, m) { if (!c) throw new Error(m); }
"""


def _correr(tmp_path, prueba: str) -> dict:
    bloques = re.findall(r"<script>(.*?)</script>", HTML, re.S)
    archivo = tmp_path / "sim_guardar.js"
    archivo.write_text(_ARNES + "\n".join(bloques) + "\n(async () => {\n" + prueba
                       + "\n  console.log(JSON.stringify(s));\n  process.exit(0);\n"
                       "})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });\n",
                       encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, (r.stderr or "")[:2000]
    return json.loads(r.stdout.strip().splitlines()[-1])


@sin_node
def test_abrir_editar_guardar_y_reabrir_muestra_la_ultima_version(tmp_path):
    s = _correr(tmp_path, """
  const s = {};
  await loadSimulador();
  s.editandoAlArrancar = _el('sim-editando').textContent;
  _el('sim-nombre').value = 'Plan A';
  await simGuardar();
  s.avisoCrear = _el('sim-guardado').textContent;

  await _recargarPagina();
  s.editandoTrasRecargar = _el('sim-editando').textContent;
  await _abrir(1);
  s.editandoAbierto = _el('sim-editando').textContent;

  simAlCambiar({target: {dataset: {sim: 'ventas.webs'}, value: '9'}});
  simAlCambiar({target: {dataset: {sim: 'cobros.mesesEntrega'}, value: '3'}});
  simAlCambiar({target: {dataset: {simForma: 'ecommerce'}, value: 'todo'}});
  simAlCambiar({target: {dataset: {simForma: 'web'}, value: 'mitad'}});
  _pedidos.length = 0;
  await simGuardar();
  s.pedidosGuardar = _pedidos.filter(p => p.indexOf('GET') !== 0);
  s.avisoGuardar = _el('sim-guardado').textContent;
  s.cuantos = Object.keys(_db).length;

  await _recargarPagina();
  s.formaEcomTrasRecargar = _el('sim-forma-ecommerce').value;
  await _abrir(1);
  s.webs = simEstado.ventas.webs;
  s.mesesEntrega = simEstado.cobros.mesesEntrega;
  s.formaEcom = _el('sim-forma-ecommerce').value;
  s.formaWeb = _el('sim-forma-web').value;
  s.editandoReabierto = _el('sim-editando').textContent;
  s.lista = _el('sim-escenarios').innerHTML;
""")
    assert "sin guardar" in s["editandoAlArrancar"]
    assert "Plan A" in s["avisoCrear"]
    assert "sin guardar" in s["editandoTrasRecargar"]
    assert s["editandoAbierto"].startswith("Editando: Plan A")

    assert s["pedidosGuardar"] == ["PUT /api/simulador/escenarios/1"]
    assert s["avisoGuardar"] == "Cambios guardados en Plan A"
    assert s["cuantos"] == 1, "guardar lo abierto no crea otro"

    assert s["formaEcomTrasRecargar"] == "mitad", "la recarga arranca de los defaults"
    assert s["webs"] == "9" and s["mesesEntrega"] == "3"
    assert s["formaEcom"] == "todo" and s["formaWeb"] == "mitad"
    assert s["editandoReabierto"].startswith("Editando: Plan A")
    assert "Plan A" in s["lista"] and "modificado" in s["lista"] and "2026" in s["lista"]


@sin_node
def test_cambiarle_el_nombre_al_abierto_lo_corrige_y_no_duplica(tmp_path):
    s = _correr(tmp_path, """
  const s = {};
  await loadSimulador();
  _el('sim-nombre').value = 'Plan A';
  await simGuardar();
  await _recargarPagina();
  await _abrir(1);
  simAlCambiar({target: {dataset: {sim: 'ventas.webs'}, value: '4'}});
  _el('sim-nombre').value = 'Plan A corregido';
  await simGuardar();
  s.cuantos = Object.keys(_db).length;
  s.nombre = _db[1].nombre;
  s.webs = JSON.parse(_db[1].datos).ventas.webs;
  s.editando = _el('sim-editando').textContent;
  s.aviso = _el('sim-guardado').textContent;
""")
    assert s["cuantos"] == 1
    assert s["nombre"] == "Plan A corregido" and s["webs"] == "4"
    assert s["editando"].startswith("Editando: Plan A corregido")
    assert s["aviso"] == "Cambios guardados en Plan A corregido"


@sin_node
def test_guardar_como_nuevo_pide_nombre_y_no_toca_el_original(tmp_path):
    s = _correr(tmp_path, """
  const s = {};
  await loadSimulador();
  _el('sim-nombre').value = 'Plan A';
  await simGuardar();
  const original = _db[1].datos;
  simAlCambiar({target: {dataset: {sim: 'ventas.webs'}, value: '6'}});

  _respuestaPrompt = null;
  await simGuardarComoNuevo();
  s.cancelado = Object.keys(_db).length;
  s.sugerido = _prompts[0][1];

  _respuestaPrompt = '  Plan B  ';
  await simGuardarComoNuevo();
  s.cuantos = Object.keys(_db).length;
  s.originalIntacto = _db[1].datos === original;
  s.webNuevo = JSON.parse(_db[2].datos).ventas.webs;
  s.nombreNuevo = _db[2].nombre;
  s.editando = _el('sim-editando').textContent;
  s.nombreEnPantalla = _el('sim-nombre').value;

  simAlCambiar({target: {dataset: {sim: 'ventas.webs'}, value: '8'}});
  await simGuardar();
  s.guardaEnElNuevo = JSON.parse(_db[2].datos).ventas.webs;
  s.originalSigueIntacto = _db[1].datos === original;
""")
    assert s["cancelado"] == 1, "cancelar el nombre no guarda"
    assert s["sugerido"] == "Plan A (copia)"
    assert s["cuantos"] == 2 and s["originalIntacto"]
    assert s["nombreNuevo"] == "Plan B" and s["webNuevo"] == "6"
    assert s["editando"].startswith("Editando: Plan B") and s["nombreEnPantalla"] == "Plan B"
    assert s["guardaEnElNuevo"] == "8" and s["originalSigueIntacto"]


@sin_node
def test_nuevo_escenario_deja_de_editar(tmp_path):
    s = _correr(tmp_path, """
  const s = {};
  await loadSimulador();
  _el('sim-nombre').value = 'Plan A';
  await simGuardar();
  await simRestablecer();
  s.editando = _el('sim-editando').textContent;
  s.nombre = _el('sim-nombre').value;
  _el('sim-nombre').value = 'Otro';
  await simGuardar();
  s.cuantos = Object.keys(_db).length;
  s.primero = _db[1].nombre;
""")
    assert "sin guardar" in s["editando"] and s["nombre"] == ""
    assert s["cuantos"] == 2 and s["primero"] == "Plan A"


@sin_node
def test_si_lo_borraron_mientras_estaba_abierto_avisa_y_ofrece_guardarlo_como_nuevo(tmp_path):
    s = _correr(tmp_path, """
  const s = {};
  await loadSimulador();
  _el('sim-nombre').value = 'Plan A';
  await simGuardar();
  simAlCambiar({target: {dataset: {sim: 'ventas.webs'}, value: '5'}});

  // Otro usuario lo borra.
  delete _db[1];
  _confirmar = false;
  await simGuardar();
  s.avisoNo = _el('sim-guardado').textContent;
  s.confirmNo = _confirms[_confirms.length - 1];
  s.cuantosNo = Object.keys(_db).length;
  s.editandoNo = _el('sim-editando').textContent;

  // Vuelve a abrir el mismo y lo borran de nuevo; esta vez acepta.
  _el('sim-nombre').value = 'Plan C';
  await simGuardar();
  delete _db[2];
  _confirmar = true;
  _respuestaPrompt = 'Plan C';
  await simGuardar();
  s.cuantosSi = Object.keys(_db).length;
  s.webs = JSON.parse(_db[3].datos).ventas.webs;
  s.editandoSi = _el('sim-editando').textContent;
""")
    assert "ya no existe" in s["avisoNo"] and "Plan A" in s["avisoNo"]
    assert "como un escenario nuevo" in s["confirmNo"]
    assert s["cuantosNo"] == 0, "sin aceptar no se crea nada"
    assert "sin guardar" in s["editandoNo"]
    assert s["cuantosSi"] == 1 and s["webs"] == "5"
    assert s["editandoSi"].startswith("Editando: Plan C")
