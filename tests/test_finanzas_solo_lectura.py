"""Finanzas en solo lectura por rol (el Contador).

Pedido de Juan (15/9): "El usuario que sea dado de alta como contador no va a
poder agregar movimientos o editar cosas solo a visualizar salvo la parte de
balances ahi si va a poder tocar, y tambien va a poder usar el simulador".

- `roles.paneles_solo_lectura` (JSON). El Contador arranca con ["finanzas"].
- `services.auth.puede_editar_panel` / `require_edicion`. Hoy solo Finanzas lo
  respeta en el servidor.
- Toda ruta de Finanzas que no sea GET da 403 para ese rol; el Balance (GET) y
  el Simulador (su propio panel) siguen andando.
"""

import json
import re
import sqlite3
from datetime import date

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import (create_user, crear_movimiento, crear_por_cobrar,
                      crear_recurrente, get_movimiento, init_db)
from services.auth import paneles_solo_lectura, puede_editar_panel
from tests.test_finanzas_balance import _correr, sin_node

HTML = dashboard.DASHBOARD_HTML
MENSAJE = "Tu rol puede ver Finanzas pero no modificarla"


def _solo_lectura_de(db, nombre):
    conn = sqlite3.connect(db)
    try:
        fila = conn.execute("SELECT paneles_solo_lectura FROM roles WHERE name = ?",
                            (nombre,)).fetchone()
    finally:
        conn.close()
    return None if fila is None else json.loads(fila[0])


def _sql(db, consulta, params=()):
    conn = sqlite3.connect(db)
    try:
        conn.execute(consulta, params)
        conn.commit()
    finally:
        conn.close()


# ── migración ────────────────────────────────────────────────────────────────

def test_una_base_nueva_trae_al_contador_con_finanzas_en_solo_lectura(tmp_path):
    db = str(tmp_path / "nueva.db")
    init_db(db)
    assert _solo_lectura_de(db, "Contador") == ["finanzas"]
    assert _solo_lectura_de(db, "Marketing") == []
    assert _solo_lectura_de(db, "Admin") == []


def test_arrancar_varias_veces_no_cambia_nada(tmp_path):
    db = str(tmp_path / "varias.db")
    for _ in range(3):
        init_db(db)
    assert _solo_lectura_de(db, "Contador") == ["finanzas"]
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(1) FROM roles WHERE name='Contador'").fetchone()[0] == 1
    finally:
        conn.close()


def test_lo_que_juan_destilda_no_se_pisa_en_el_proximo_arranque(tmp_path):
    db = str(tmp_path / "editada.db")
    init_db(db)
    _sql(db, "UPDATE roles SET paneles_solo_lectura='[]' WHERE name='Contador'")
    init_db(db)
    assert _solo_lectura_de(db, "Contador") == []


def test_una_base_de_antes_precarga_al_contador_que_ya_existia(tmp_path):
    """Producción: el Contador lo creó la rama de roles, antes de que existiera
    la columna. La primera vez que aparece la columna, queda en solo lectura."""
    db = str(tmp_path / "vieja.db")
    init_db(db)
    _sql(db, "ALTER TABLE roles DROP COLUMN paneles_solo_lectura")
    _sql(db, "UPDATE roles SET panel_access=? WHERE name='Contador'",
         (json.dumps(["cal", "finanzas", "simulador"]),))

    init_db(db)

    assert _solo_lectura_de(db, "Contador") == ["finanzas"]
    assert _solo_lectura_de(db, "Marketing") == []


def test_una_base_de_antes_sin_contador_lo_crea_en_solo_lectura(tmp_path):
    db = str(tmp_path / "sin_contador.db")
    init_db(db)
    _sql(db, "ALTER TABLE roles DROP COLUMN paneles_solo_lectura")
    _sql(db, "DELETE FROM roles WHERE name='Contador'")

    init_db(db)

    assert _solo_lectura_de(db, "Contador") == ["finanzas"]


def test_si_borran_el_contador_vuelve_en_solo_lectura(tmp_path):
    db = str(tmp_path / "borrado.db")
    init_db(db)
    _sql(db, "DELETE FROM roles WHERE name='Contador'")
    init_db(db)
    assert _solo_lectura_de(db, "Contador") == ["finanzas"]


# ── la app ───────────────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "raiz@scalerics.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    db = str(tmp_path / "a.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


def _rol(db, nombre, paneles, solo_lectura=None):
    conn = sqlite3.connect(db)
    try:
        fila = conn.execute("SELECT id FROM roles WHERE name=?", (nombre,)).fetchone()
        if fila:
            rid = fila[0]
            conn.execute("UPDATE roles SET panel_access=? WHERE id=?", (json.dumps(paneles), rid))
        else:
            rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)",
                               (nombre, json.dumps(paneles))).lastrowid
        if solo_lectura is not None:
            conn.execute("UPDATE roles SET paneles_solo_lectura=? WHERE id=?",
                         (json.dumps(solo_lectura), rid))
        conn.commit()
        return rid
    finally:
        conn.close()


def _usuario(app, nombre, email, rol=None, paneles=None, solo_lectura=None):
    db = app.config["_DB"]
    uid = create_user(db, name=nombre, email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    if rol:
        _sql(db, "UPDATE users SET role_id=? WHERE id=?",
             (_rol(db, rol, paneles, solo_lectura), uid))
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = nombre
    return c, uid


@pytest.fixture
def contador(app):
    # Juan le tilda Finanzas y Simulador en el editor; el solo lectura ya viene
    # de init_db.
    c, uid = _usuario(app, "guille", "contador@scalerics.com", rol="Contador",
                      paneles=["cal", "finanzas", "simulador"])
    return c, uid


@pytest.fixture
def admin(app):
    return _usuario(app, "raiz", "raiz@scalerics.com")


@pytest.fixture
def socio(app):
    return _usuario(app, "socio", "socio@scalerics.com", rol="Socio",
                    paneles=["finanzas", "simulador"])


@pytest.fixture
def datos(app):
    db = app.config["_DB"]
    hoy = date.today().isoformat()
    mov = crear_movimiento(db, tipo="ingreso", fecha=hoy, periodo=hoy[:7],
                           concepto="Cobro", categoria="otros", monto=100,
                           moneda="USD", monto_usd=100)
    fijo = crear_recurrente(db, tipo="egreso", concepto="Fly", categoria="infraestructura",
                            monto=30, moneda="USD", desde="2026-01")
    pc = crear_por_cobrar(db, concepto="Saldo", monto_usd=50)
    from database import crear_dato_balance
    dato = crear_dato_balance(db, clase="activo", rubro="maquinarias", nombre="Notebook",
                              monto_usd=900, desde="2026-01-01")
    return {"mov": mov, "fijo": fijo, "pc": pc, "dato": dato, "hoy": hoy}


def _mov(hoy):
    return {"tipo": "ingreso", "fecha": hoy, "concepto": "Nuevo", "categoria": "otros",
            "monto": 10, "moneda": "USD"}


FIJO = {"tipo": "egreso", "concepto": "Zoho", "categoria": "herramientas",
        "monto": 10, "moneda": "USD", "desde": "2026-01"}

# (método, regla de Flask, url, cuerpo). Cada ruta de Finanzas que no es GET.
ESCRITURAS = [
    ("POST", "/api/finanzas/movimientos", "/api/finanzas/movimientos", "mov"),
    ("PUT", "/api/finanzas/movimientos/<int:mov_id>", "/api/finanzas/movimientos/{mov}", "mov"),
    ("DELETE", "/api/finanzas/movimientos/<int:mov_id>", "/api/finanzas/movimientos/{mov}", None),
    ("POST", "/api/finanzas/recurrentes", "/api/finanzas/recurrentes", "fijo"),
    ("PUT", "/api/finanzas/recurrentes/<int:rec_id>", "/api/finanzas/recurrentes/{fijo}", "fijo"),
    ("DELETE", "/api/finanzas/recurrentes/<int:rec_id>", "/api/finanzas/recurrentes/{fijo}", None),
    ("POST", "/api/finanzas/meses/<periodo>/reabrir", "/api/finanzas/meses/2026-01/reabrir", None),
    ("POST", "/api/finanzas/meses/<periodo>/cerrar", "/api/finanzas/meses/2026-01/cerrar", None),
    ("POST", "/api/finanzas/por-cobrar", "/api/finanzas/por-cobrar", "pendiente"),
    ("POST", "/api/finanzas/por-cobrar/<int:pc_id>/cobrar", "/api/finanzas/por-cobrar/{pc}/cobrar", "cobro"),
    ("DELETE", "/api/finanzas/por-cobrar/<int:pc_id>", "/api/finanzas/por-cobrar/{pc}", None),
    # Datos para el Balance General (15/9): cargar bienes, deudas y capital es
    # modificar Finanzas. Generar el balance no (es GET, está en LECTURAS).
    ("POST", "/api/finanzas/balance-datos", "/api/finanzas/balance-datos", "dato"),
    ("PUT", "/api/finanzas/balance-datos/<int:dato_id>", "/api/finanzas/balance-datos/{dato}", "dato"),
    ("DELETE", "/api/finanzas/balance-datos/<int:dato_id>", "/api/finanzas/balance-datos/{dato}", None),
    # Cobro con tarjeta (23/9): registrar un cobro, marcar que llegó el
    # depósito o cambiar las comisiones es modificar Finanzas. La calculadora
    # no (es GET, está en LECTURAS). El id no importa: el candado corta antes.
    ("PUT", "/api/finanzas/tarjeta/ajustes", "/api/finanzas/tarjeta/ajustes", None),
    ("POST", "/api/finanzas/cobros-tarjeta", "/api/finanzas/cobros-tarjeta", None),
    ("PUT", "/api/finanzas/cobros-tarjeta/<int:cobro_id>/acreditado",
     "/api/finanzas/cobros-tarjeta/1/acreditado", None),
    ("DELETE", "/api/finanzas/cobros-tarjeta/<int:cobro_id>", "/api/finanzas/cobros-tarjeta/1", None),
]

LECTURAS = ["/api/finanzas/movimientos", "/api/finanzas/recurrentes", "/api/finanzas/resumen",
            "/api/finanzas/pauta", "/api/finanzas/meses", "/api/finanzas/por-cobrar",
            "/api/finanzas/iva", "/api/finanzas/categorias", "/api/finanzas/balance-datos",
            "/api/finanzas/balance?tipo=blanco", "/api/finanzas/balance?tipo=interno",
            "/api/finanzas/balance-general?tipo=blanco", "/api/finanzas/balance-general?tipo=interno",
            "/api/finanzas/tarjeta/ajustes", "/api/finanzas/cobros-tarjeta",
            "/api/finanzas/tarjeta/desglose?modo=precio&monto=300&tarjeta=visa_credito"]


def _pedir(cli, metodo, url, cuerpo, datos):
    json_cuerpo = {"mov": _mov(datos["hoy"]), "fijo": FIJO,
                   "pendiente": {"concepto": "Otro", "monto_usd": 20},
                   "cobro": {"fecha": datos["hoy"]},
                   "dato": {"clase": "pasivo", "rubro": "prestamos", "nombre": "BROU",
                            "monto_usd": 100, "desde": "2026-01-01"},
                   None: None}[cuerpo]
    return cli.open(url.format(**datos), method=metodo, json=json_cuerpo)


def test_el_contador_genera_el_balance_general_y_ve_los_datos_pero_no_los_carga(app, contador, datos):
    from database import get_dato_balance
    cli, _ = contador
    r = cli.get("/api/finanzas/balance-general?tipo=interno")
    assert r.status_code == 200
    assert r.get_json()["activo"]["total"] == r.get_json()["total_pasivo_patrimonio"]
    assert [x["nombre"] for x in cli.get("/api/finanzas/balance-datos").get_json()["datos"]] == ["Notebook"]

    for respuesta in (
            cli.post("/api/finanzas/balance-datos",
                     json={"clase": "capital", "monto_usd": 1, "desde": "2026-01-01"}),
            cli.put(f"/api/finanzas/balance-datos/{datos['dato']}",
                    json={"clase": "activo", "rubro": "otros", "monto_usd": 1, "desde": "2026-01-01"}),
            cli.delete(f"/api/finanzas/balance-datos/{datos['dato']}")):
        assert respuesta.status_code == 403
        assert respuesta.get_json()["error"] == MENSAJE
    assert get_dato_balance(app.config["_DB"], datos["dato"])["monto_usd"] == 900


def test_la_lista_de_escrituras_cubre_todas_las_rutas_que_no_son_get(app):
    """Si mañana se suma una ruta de Finanzas que escribe, este test obliga a
    decidir si el Contador la puede usar."""
    reales = set()
    for regla in app.url_map.iter_rules():
        if not regla.endpoint.startswith("finanzas."):
            continue
        for metodo in regla.methods - {"GET", "HEAD", "OPTIONS"}:
            reales.add((metodo, regla.rule))
    assert reales == {(m, r) for m, r, _, _ in ESCRITURAS}


@pytest.mark.parametrize("url", LECTURAS)
def test_el_contador_ve_finanzas(contador, url):
    cli, _ = contador
    assert cli.get(url).status_code == 200


@pytest.mark.parametrize("metodo,regla,url,cuerpo", ESCRITURAS)
def test_el_contador_no_modifica_finanzas(app, contador, datos, metodo, regla, url, cuerpo):
    cli, _ = contador
    r = _pedir(cli, metodo, url, cuerpo, datos)
    assert r.status_code == 403
    assert r.get_json() == {"ok": False, "solo_lectura": True, "error": MENSAJE}
    # Y no tocó nada.
    assert get_movimiento(app.config["_DB"], datos["mov"])["concepto"] == "Cobro"


def test_el_contador_genera_el_balance(contador, datos):
    cli, _ = contador
    d = cli.get("/api/finanzas/balance?tipo=interno&desde=inicio").get_json()
    assert d["ingresos"]["total"] == 100


def test_el_contador_usa_y_guarda_el_simulador(contador):
    cli, _ = contador
    assert cli.get("/api/simulador/precarga").status_code == 200
    r = cli.post("/api/simulador/escenarios", json={"nombre": "Plan", "datos": {"version": 1}})
    assert r.status_code in (200, 201)
    eid = r.get_json()["id"]
    assert cli.put(f"/api/simulador/escenarios/{eid}",
                   json={"nombre": "Plan B", "datos": {"version": 1}}).status_code == 200
    assert cli.get("/api/simulador/escenarios").status_code == 200
    assert cli.delete(f"/api/simulador/escenarios/{eid}").status_code == 200


@pytest.mark.parametrize("metodo,regla,url,cuerpo", ESCRITURAS)
def test_el_admin_puede_todo(admin, datos, metodo, regla, url, cuerpo):
    cli, _ = admin
    assert _pedir(cli, metodo, url, cuerpo, datos).status_code != 403


@pytest.mark.parametrize("metodo,regla,url,cuerpo", ESCRITURAS)
def test_un_rol_sin_solo_lectura_edita_como_siempre(socio, datos, metodo, regla, url, cuerpo):
    cli, _ = socio
    assert _pedir(cli, metodo, url, cuerpo, datos).status_code != 403


def test_crear_y_borrar_como_admin_y_como_socio(admin, socio, datos):
    for cli, _ in (admin, socio):
        r = cli.post("/api/finanzas/movimientos", json=_mov(datos["hoy"]))
        assert r.status_code == 201
        assert cli.delete(f"/api/finanzas/movimientos/{r.get_json()['id']}").status_code == 200


def test_sin_panel_finanzas_sigue_siendo_403_de_siempre(app):
    cli, _ = _usuario(app, "caller", "caller@scalerics.com", rol="Caller", paneles=["cola"])
    r = cli.post("/api/finanzas/movimientos", json={})
    assert r.status_code == 403
    assert r.get_json()["error"] == "No autorizado"


def test_puede_editar_panel(app, contador, admin, socio):
    db = app.config["_DB"]
    _, uid_contador = contador
    _, uid_admin = admin
    _, uid_socio = socio
    assert not puede_editar_panel(db, uid_contador, "finanzas")
    assert puede_editar_panel(db, uid_contador, "simulador")
    assert not puede_editar_panel(db, uid_contador, "cola")        # ni lo ve
    assert puede_editar_panel(db, uid_admin, "finanzas")
    assert puede_editar_panel(db, uid_socio, "finanzas")
    assert not puede_editar_panel(db, None, "finanzas")
    assert paneles_solo_lectura(db, uid_contador) == ["finanzas"]
    assert paneles_solo_lectura(db, uid_admin) == []


def test_un_admin_por_rol_no_queda_en_solo_lectura_aunque_el_rol_lo_diga(app):
    cli, uid = _usuario(app, "otro", "otro@scalerics.com", rol="Admin",
                        paneles=["finanzas"], solo_lectura=["finanzas"])
    assert puede_editar_panel(app.config["_DB"], uid, "finanzas")
    assert cli.get("/api/me").get_json()["paneles_solo_lectura"] == []


# ── /api/me ──────────────────────────────────────────────────────────────────

def test_api_me_trae_los_paneles_en_solo_lectura(contador, admin, socio):
    assert contador[0].get("/api/me").get_json()["paneles_solo_lectura"] == ["finanzas"]
    assert admin[0].get("/api/me").get_json()["paneles_solo_lectura"] == []
    assert socio[0].get("/api/me").get_json()["paneles_solo_lectura"] == []


# ── editor de roles ──────────────────────────────────────────────────────────

def _id_rol(db, nombre):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT id FROM roles WHERE name=?", (nombre,)).fetchone()[0]
    finally:
        conn.close()


def test_el_editor_guarda_el_solo_lectura(app, admin):
    cli, _ = admin
    db = app.config["_DB"]
    rid = _id_rol(db, "Contador")

    r = cli.put(f"/api/admin/roles/{rid}", json={"name": "Contador", "panels": ["cal", "finanzas"],
                                                "paneles_solo_lectura": []})
    assert r.status_code == 200 and _solo_lectura_de(db, "Contador") == []

    # Uno que el rol no ve se descarta.
    cli.put(f"/api/admin/roles/{rid}", json={"name": "Contador", "panels": ["cal", "finanzas"],
                                            "paneles_solo_lectura": ["finanzas", "cola"]})
    assert _solo_lectura_de(db, "Contador") == ["finanzas"]

    # Un PUT viejo, sin la clave, no lo toca.
    cli.put(f"/api/admin/roles/{rid}", json={"name": "Contador", "panels": ["cal", "finanzas"]})
    assert _solo_lectura_de(db, "Contador") == ["finanzas"]

    assert cli.put(f"/api/admin/roles/{rid}",
                   json={"paneles_solo_lectura": "finanzas"}).status_code == 400

    r = cli.post("/api/admin/roles", json={"name": "Auditor", "panels": ["finanzas"],
                                          "paneles_solo_lectura": ["finanzas"]})
    assert r.status_code == 200 and _solo_lectura_de(db, "Auditor") == ["finanzas"]
    listado = {x["name"]: x for x in cli.get("/api/admin/roles").get_json()}
    assert json.loads(listado["Auditor"]["paneles_solo_lectura"]) == ["finanzas"]


def test_solo_un_admin_toca_los_roles(app, contador):
    """Sin esto el Contador se sacaba la marca a sí mismo con un PUT."""
    cli, _ = contador
    rid = _id_rol(app.config["_DB"], "Contador")
    assert cli.get("/api/admin/roles").status_code == 403
    assert cli.put(f"/api/admin/roles/{rid}", json={"paneles_solo_lectura": []}).status_code == 403
    assert cli.post("/api/admin/roles", json={"name": "X", "panels": []}).status_code == 403
    assert cli.delete(f"/api/admin/roles/{rid}").status_code == 403
    assert _solo_lectura_de(app.config["_DB"], "Contador") == ["finanzas"]


_ARNES_ADMIN = r"""
const _els = {};
function _el(id) {
  if (!_els[id]) _els[id] = { id, innerHTML: '', value: '', style: {}, checked: undefined,
                              classList: { add(){}, remove(){}, toggle(){} }, focus(){} };
  return _els[id];
}
globalThis.document = { getElementById: _el };
globalThis.window = globalThis;
globalThis.alert = () => {};
globalThis.confirm = () => true;
const _pedidos = [];
const _RESP = { '/api/admin/roles': __ROLES__, '/api/admin/users-data': [] };
globalThis.fetch = (url, o) => {
  _pedidos.push([String(url), (o && o.method) || 'GET', o && o.body]);
  const r = _RESP[String(url)];
  return Promise.resolve({ ok: true, json: () => Promise.resolve(r === undefined ? {ok: true} : r) });
};
"""


@sin_node
def test_el_editor_muestra_el_check_solo_en_finanzas_y_lo_manda(app, admin, tmp_path):
    cli, _ = admin
    pagina = cli.get("/admin/users").get_data(as_text=True)
    script = re.findall(r"<script>(.*?)</script>", pagina, re.S)[-1]
    roles = [{"id": 7, "name": "Contador",
              "panel_access": json.dumps(["cal", "finanzas"]),
              "paneles_solo_lectura": json.dumps(["finanzas"])}]
    prueba = """
(async () => {
  await new Promise(r => setImmediate(r));
  const chips = _el('role-panels-7').innerHTML;
  _el('role-name-7').value = 'Contador';
  _el('r7-cb-cal').checked = true;
  _el('r7-cb-finanzas').checked = true;
  _el('r7-sl-finanzas').checked = true;
  await saveRole(7);
  _el('r7-sl-finanzas').checked = false;
  await saveRole(7);
  _el('r7-sl-finanzas').checked = true;
  _el('r7-cb-finanzas').checked = false;
  await saveRole(7);
  _el('new-role-name').value = 'Auditor';
  _el('new-cb-finanzas').checked = true;
  _el('new-sl-finanzas').checked = true;
  await createRole();
  const cuerpos = _pedidos.filter(p => p[1] !== 'GET').map(p => JSON.parse(p[2]));
  console.log(JSON.stringify({chips, cuerpos}));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
"""
    archivo = tmp_path / "admin.js"
    archivo.write_text(_ARNES_ADMIN.replace("__ROLES__", json.dumps(roles)) + script + prueba,
                       encoding="utf-8")
    import subprocess
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, (r.stderr or "")[:2000]
    s = json.loads(r.stdout.strip().splitlines()[-1])

    assert re.search(r'id="r7-sl-finanzas" checked', s["chips"])
    assert s["chips"].count("-sl-chip-") == 1, "el check va solo al lado de Finanzas"
    assert "Finanzas: solo lectura" in s["chips"]
    assert [c["paneles_solo_lectura"] for c in s["cuerpos"]] == [["finanzas"], [], [], ["finanzas"]]
    assert s["cuerpos"][-1]["name"] == "Auditor"


# ── Finanzas en solo lectura (node) ──────────────────────────────────────────

def _fijo(fid):
    return {"id": fid, "tipo": "egreso", "concepto": f"Fijo {fid}", "categoria": "otros",
            "monto": 30, "moneda": "USD", "tipo_cambio": None, "monto_usd": 30,
            "dia_del_mes": 1, "desde": "2026-01", "hasta": None, "activo": 1,
            "facturado": 0, "client_id": None, "notas": None}


@sin_node
def test_finanzas_en_solo_lectura_no_dibuja_botones_y_avisa(tmp_path):
    movs = [{"id": 1, "tipo": "ingreso", "fecha": "2026-09-01", "concepto": "Cobro",
             "categoria": "otros", "monto": 100, "moneda": "USD", "tipo_cambio": None,
             "monto_usd": 100, "recurrente_id": None}]
    pendientes = {"pendientes": [{"id": 3, "client_name": "X", "concepto": "Saldo",
                                  "vence": None, "vencido": False, "texto": "sin fecha",
                                  "monto_usd": 50}], "total_usd": 50}
    prueba = """
(async () => {
  await new Promise(r => setImmediate(r));   // deja terminar el /api/me del arranque
  _RESP['/api/finanzas/movimientos?desde=2026-09&hasta=2026-09'] = %s;
  _RESP['/api/finanzas/recurrentes'] = %s;
  _RESP['/api/finanzas/por-cobrar'] = %s;
  _finMeses = {mes_actual: '2026-09', con_datos: [], abiertos: []};
  const pintar = async () => {
    _finAplicarSoloLectura();
    await loadMovimientos('2026-09', '2026-09');
    await loadFijos();
    await loadPorCobrar();
    finVista('balance');
    return {movs: _el('fin-tabla').innerHTML, fijos: _el('fin-fijos').innerHTML,
            cobrar: _el('fin-cobrar').innerHTML,
            botones: ['fin-btn-movimiento', 'fin-btn-reabrir', 'fin-btn-pendiente']
              .map(id => _el(id).style.display),
            aviso: _el('fin-solo-lectura').style.display,
            balance: _el('fin-vista-balance').style.display,
            generar: _el('fb-generar').style.display || ''};
  };
  const normal = await pintar();
  window._panelesSoloLectura = ['finanzas'];
  const solo = await pintar();
  let alerta = '';
  globalThis.alert = t => { alerta = t; };
  await abrirMovimiento();
  console.log(JSON.stringify({normal, solo, alerta}));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""" % (json.dumps(movs), json.dumps([_fijo(1)]), json.dumps(pendientes))
    s = _correr(tmp_path, prueba)
    escrituras = ("abrirMovimiento(", "borrarMovimientoUI(", "abrirFijo(", "borrarFijoUI(",
                  "cobrarPendiente(", "borrarPendiente(")

    normal, solo = s["normal"], s["solo"]
    todo_normal = normal["movs"] + normal["fijos"] + normal["cobrar"]
    for f in escrituras:
        assert f in todo_normal, f
    assert normal["botones"] == ["", "", ""] and normal["aviso"] == "none"

    todo_solo = solo["movs"] + solo["fijos"] + solo["cobrar"]
    for f in escrituras:
        assert f not in todo_solo, f
    assert "Cobro" in solo["movs"] and "Fijo 1" in solo["fijos"] and "Saldo" in solo["cobrar"]
    assert solo["botones"] == ["none", "none", "none"]
    assert solo["aviso"] == ""
    assert solo["balance"] == "" and solo["generar"] == ""
    assert s["alerta"] == MENSAJE


def test_el_aviso_y_la_pestania_balance_estan_en_la_pagina():
    assert ('id="fin-solo-lectura" style="display:none">Modo solo lectura: '
            'podés ver y generar balances</div>') in HTML
    assert 'id="fin-tab-balance"' in HTML


def test_el_js_nuevo_no_tiene_jinja_ni_barras_invertidas():
    src = open(dashboard.__file__, encoding="utf-8").read()
    bloques = [
        src[src.index("// ── Finanzas en solo lectura (el Contador) ──"):
            src.index("function _finRangoCambio(")],
        src[src.index("const PANELES_CON_SOLO_LECTURA"):src.index("async function loadRoles(")],
    ]
    for bloque in bloques:
        for trampa in ("{#", "{{", "{%", chr(92)):
            assert trampa not in bloque, repr(trampa)
