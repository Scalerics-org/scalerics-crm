"""Arrastrar fichas del Pipeline Notion y que el cambio se escriba en Notion.

Tres capas, igual que el arrastre de Tareas:

- `mover_cliente` (servicio): GET de la pagina para ubicar la property de
  estado, PATCH solo si hace falta, y la fila local solo si Notion acepto.
- `POST /api/notion-clients/<id>/estado` (ruta): valida, pide el panel y
  contesta 502 si Notion no acepto.
- El JS del panel: la ficha se ve movida mientras Notion contesta y vuelve a
  su columna si rechaza. Se corre con node contra un DOM de mentira.

Nunca se llama a la API real de Notion: todo `requests` va mockeado.
"""

import json
import re
import shutil
import subprocess
from unittest.mock import patch

import pytest

import dashboard
from database import (_connect, create_user, get_notion_client_by_id,
                      get_notion_clients, init_db, upsert_notion_client)
from services import notion_service as ns

HTML = dashboard.DASHBOARD_HTML

node = pytest.mark.skipif(shutil.which("node") is None,
                          reason="node no esta instalado")


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    return ruta


@pytest.fixture
def notion_env(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "secret-de-prueba")
    monkeypatch.setenv("NOTION_CLIENTS_DATA_SOURCE_ID", "ds-clientes")


def _pagina(status="Demo Agendada", prop_id="Qx%3Ab", con_estado=True):
    """Una pagina de Clientes como la devuelve `GET /v1/pages/{id}`.

    La property de estado no tiene nombre (clave vacia), igual que en el
    fixture del sync en test_notion_clientes.py.
    """
    props = {"Name": {"id": "title", "type": "title",
                      "title": [{"plain_text": "Milky"}]}}
    if con_estado:
        estado = {"type": "status", "status": {"name": status} if status else None}
        if prop_id:
            estado["id"] = prop_id
        props[""] = estado
    return {"id": "c-1", "properties": props}


def _ficha(db, status="Demo Agendada"):
    return upsert_notion_client(db, "c-1", "Milky", status=status)


# ── servicio ─────────────────────────────────────────────────────────────────


def test_mover_escribe_la_property_sin_nombre_por_su_id(db, notion_env):
    cid = _ficha(db)
    with patch("services.notion_service.requests.get",
               return_value=_Resp(200, _pagina())) as get, \
         patch("services.notion_service.requests.patch",
               return_value=_Resp(200, {"id": "c-1"})) as patch_req:
        assert ns.mover_cliente(db, cid, "Hay que hacer Presupuesto") == (True, None)

    assert get.call_args.args[0].endswith("/pages/c-1")
    assert patch_req.call_args.args[0].endswith("/pages/c-1")
    assert patch_req.call_args.kwargs["json"] == {
        "properties": {"Qx%3Ab": {"status": {"name": "Hay que hacer Presupuesto"}}}}
    # La fila local queda igual que Notion: el tablero no rebota hasta el sync.
    assert get_notion_client_by_id(db, cid)["status"] == "Hay que hacer Presupuesto"


def test_sin_id_de_property_usa_su_nombre_aunque_sea_vacio(db, notion_env):
    cid = _ficha(db)
    with patch("services.notion_service.requests.get",
               return_value=_Resp(200, _pagina(prop_id=None))), \
         patch("services.notion_service.requests.patch",
               return_value=_Resp(200, {})) as patch_req:
        assert ns.mover_cliente(db, cid, "Perdido") == (True, None)
    assert patch_req.call_args.kwargs["json"] == {
        "properties": {"": {"status": {"name": "Perdido"}}}}


def test_no_escribe_status_ni_select_a_ciegas(db, notion_env):
    """La property se escribe como tipo `status`, que es el tipo que lee el
    sync (`_estado_de`), y nunca con la clave "Status" de la base de Tareas."""
    cid = _ficha(db)
    with patch("services.notion_service.requests.get",
               return_value=_Resp(200, _pagina())), \
         patch("services.notion_service.requests.patch",
               return_value=_Resp(200, {})) as patch_req:
        ns.mover_cliente(db, cid, "Perdido")
    props = patch_req.call_args.kwargs["json"]["properties"]
    assert "Status" not in props
    assert list(props.values()) == [{"status": {"name": "Perdido"}}]


def test_mover_a_la_columna_donde_ya_estaba_no_habla_con_notion(db, notion_env):
    cid = _ficha(db, status="Perdido")
    with patch("services.notion_service.requests.get") as get, \
         patch("services.notion_service.requests.patch") as patch_req:
        assert ns.mover_cliente(db, cid, "Perdido") == (True, None)
    get.assert_not_called()
    patch_req.assert_not_called()


def test_si_en_notion_ya_estaba_ahi_no_manda_patch_pero_pone_al_dia_el_espejo(db, notion_env):
    cid = _ficha(db, status="Demo Agendada")
    with patch("services.notion_service.requests.get",
               return_value=_Resp(200, _pagina(status="Perdido"))), \
         patch("services.notion_service.requests.patch") as patch_req:
        assert ns.mover_cliente(db, cid, "Perdido") == (True, None)
    patch_req.assert_not_called()
    assert get_notion_client_by_id(db, cid)["status"] == "Perdido"


@pytest.mark.parametrize("respuesta", [_Resp(400, {"message": "invalid"}),
                                       _Resp(429, {}), ConnectionError("sin red")])
def test_si_notion_rechaza_el_patch_la_ficha_no_se_mueve(db, notion_env, respuesta):
    cid = _ficha(db)
    kwargs = ({"side_effect": respuesta} if isinstance(respuesta, Exception)
              else {"return_value": respuesta})
    with patch("services.notion_service.requests.get",
               return_value=_Resp(200, _pagina())), \
         patch("services.notion_service.requests.patch", **kwargs), \
         patch("services.notion_service.cliente_cambio_de_estado") as aviso:
        ok, error = ns.mover_cliente(db, cid, "Presupuesto Aceptado")
    assert ok is False
    assert error
    assert get_notion_client_by_id(db, cid)["status"] == "Demo Agendada"
    aviso.assert_not_called()


@pytest.mark.parametrize("respuesta", [_Resp(404, {}), ConnectionError("sin red")])
def test_si_no_se_puede_leer_la_ficha_no_se_escribe(db, notion_env, respuesta):
    cid = _ficha(db)
    kwargs = ({"side_effect": respuesta} if isinstance(respuesta, Exception)
              else {"return_value": respuesta})
    with patch("services.notion_service.requests.get", **kwargs), \
         patch("services.notion_service.requests.patch") as patch_req:
        ok, error = ns.mover_cliente(db, cid, "Perdido")
    assert (ok, bool(error)) == (False, True)
    patch_req.assert_not_called()
    assert get_notion_client_by_id(db, cid)["status"] == "Demo Agendada"


def test_una_ficha_sin_property_de_estado_no_se_escribe(db, notion_env):
    cid = _ficha(db)
    with patch("services.notion_service.requests.get",
               return_value=_Resp(200, _pagina(con_estado=False))), \
         patch("services.notion_service.requests.patch") as patch_req:
        ok, error = ns.mover_cliente(db, cid, "Perdido")
    assert ok is False and error
    patch_req.assert_not_called()


def test_un_estado_que_no_es_del_tablero_no_sale_a_la_red(db, notion_env):
    cid = _ficha(db)
    with patch("services.notion_service.requests.get") as get, \
         patch("services.notion_service.requests.patch") as patch_req:
        ok, error = ns.mover_cliente(db, cid, "Backlog")  # es de Tareas, no de Clientes
    assert ok is False and error
    get.assert_not_called()
    patch_req.assert_not_called()


def test_sin_token_o_sin_ficha_no_se_mueve_nada(db, monkeypatch):
    cid = _ficha(db)
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    ok, error = ns.mover_cliente(db, cid, "Perdido")
    assert ok is False and "NOTION_TOKEN" in error

    monkeypatch.setenv("NOTION_TOKEN", "x")
    ok, error = ns.mover_cliente(db, 9999, "Perdido")
    assert ok is False and error


# ── el punto unico de "Notion ya tiene el cambio" ────────────────────────────


def test_mover_avisa_el_cambio_confirmado(db, notion_env):
    cid = _ficha(db)
    with patch("services.notion_service.requests.get",
               return_value=_Resp(200, _pagina())), \
         patch("services.notion_service.requests.patch", return_value=_Resp(200, {})), \
         patch("services.notion_service.cliente_cambio_de_estado",
               wraps=ns.cliente_cambio_de_estado) as aviso:
        ns.mover_cliente(db, cid, "Presupuesto Aceptado")
    aviso.assert_called_once_with(db, "c-1", "Demo Agendada", "Presupuesto Aceptado")


def test_el_sync_avisa_solo_las_fichas_que_se_movieron_en_notion(db, notion_env):
    upsert_notion_client(db, "c-1", "Milky", status="Demo Agendada")
    upsert_notion_client(db, "c-2", "Garrido", status="Perdido")

    def _c(pid, nombre, status):
        return {"id": pid, "properties": {
            "Name": {"type": "title", "title": [{"plain_text": nombre}]},
            "": {"type": "status", "status": {"name": status}}}}

    payload = {"results": [_c("c-1", "Milky", "Presupuesto Aceptado"),
                           _c("c-2", "Garrido", "Perdido"),
                           _c("c-3", "Nueva", "Demo Agendada")],
               "has_more": False}
    with patch("services.notion_service.requests.post",
               return_value=_Resp(200, payload)), \
         patch("services.notion_service.cliente_cambio_de_estado") as aviso:
        assert ns.traer_clientes(db) == (3, None)

    aviso.assert_called_once_with(db, "c-1", "Demo Agendada", "Presupuesto Aceptado")
    assert {c["name"]: c["status"] for c in get_notion_clients(db)} == {
        "Milky": "Presupuesto Aceptado", "Garrido": "Perdido", "Nueva": "Demo Agendada"}


# ── ruta ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "token-de-test")
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    app = dashboard.create_app(ruta)
    app.config["TESTING"] = True
    return app


_AUTH = {"x-admin-token": "token-de-test"}


def test_la_ruta_mueve_y_contesta_el_grupo(app):
    db = app.config["DB_PATH"]
    cid = _ficha(db)
    with patch("routes.notion_clients.mover_cliente", return_value=(True, None)) as mover:
        r = app.test_client().post(f"/api/notion-clients/{cid}/estado",
                                   json={"estado": "Hay que hacer Presupuesto"},
                                   headers=_AUTH)
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "estado": "Hay que hacer Presupuesto",
                            "grupo": "in_progress",
                            # Sin conectar y a una columna que no es la de
                            # aceptado: ni pasa a Clientes ni hay nada que avisar.
                            "paso_a_clientes": False, "sin_conectar": False}
    mover.assert_called_once_with(db, cid, "Hay que hacer Presupuesto")


def test_la_ruta_da_502_si_notion_no_acepta(app):
    cid = _ficha(app.config["DB_PATH"])
    with patch("routes.notion_clients.mover_cliente",
               return_value=(False, "Notion devolvio HTTP 400")):
        r = app.test_client().post(f"/api/notion-clients/{cid}/estado",
                                   json={"estado": "Perdido"}, headers=_AUTH)
    assert r.status_code == 502
    assert r.get_json() == {"ok": False, "error": "Notion devolvio HTTP 400"}


def test_la_ruta_valida_ficha_y_columna(app):
    cid = _ficha(app.config["DB_PATH"])
    cli = app.test_client()
    with patch("routes.notion_clients.mover_cliente") as mover:
        assert cli.post("/api/notion-clients/9999/estado", json={"estado": "Perdido"},
                        headers=_AUTH).status_code == 404
        assert cli.post(f"/api/notion-clients/{cid}/estado", json={"estado": "Inventada"},
                        headers=_AUTH).status_code == 400
        assert cli.post(f"/api/notion-clients/{cid}/estado", data="no es json",
                        headers=_AUTH).status_code == 400
    mover.assert_not_called()


def _usuario_con_paneles(db, paneles, email):
    uid = create_user(db, name="Alguien", email=email, phone="099", password_hash="x")
    conn = _connect(db)
    try:
        rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?, ?)",
                           (f"rol-{email}", json.dumps(paneles))).lastrowid
        conn.execute("UPDATE users SET role_id = ? WHERE id = ?", (rid, uid))
        conn.commit()
    finally:
        conn.close()
    return uid


@pytest.mark.parametrize("paneles,esperado", [(["cola"], 403), (["notion_clients"], 200)])
def test_mover_pide_el_panel_del_pipeline(app, monkeypatch, paneles, esperado):
    """Le escribe a una database del equipo: quien no ve el tablero no puede
    moverlo con un fetch directo."""
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@otro.com")
    db = app.config["DB_PATH"]
    create_user(db, name="Jefe", email="jefe@otro.com", phone="0", password_hash="x")
    uid = _usuario_con_paneles(db, paneles, "caller@test.com")
    cid = _ficha(db)
    cli = app.test_client()
    with cli.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Alguien"
    with patch("routes.notion_clients.mover_cliente", return_value=(True, None)) as mover:
        r = cli.post(f"/api/notion-clients/{cid}/estado", json={"estado": "Perdido"})
    assert r.status_code == esperado
    assert mover.called is (esperado == 200)


# ── JS: el tablero viejo no pisa los arrastres ───────────────────────────────


def _script_principal() -> str:
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", HTML, re.S)
    return max(scripts, key=len)


def test_cada_funcion_de_arrastre_se_declara_una_sola_vez():
    """El bug del tablero de Tareas: el tablero de leads muerto volvia a
    declarar `_kanbanDragStart` y `_kanbanDrop` mas abajo, gana la ultima
    declaracion, y arrastrar una tarea no hacia nada. Si vuelve una segunda
    declaracion de cualquiera de estas, se ve aca."""
    js = _script_principal()
    for nombre in ("_kanbanDragStart", "_kanbanOver", "_kanbanLeave", "_kanbanDrop",
                   "_ncDragStart", "_ncDragEnd", "_ncOver", "_ncLeave", "_ncDrop",
                   "_ncMover", "_ncDibujar", "loadNotionClients",
                   "_notionClientColHtml", "_notionClientCardHtml"):
        n = len(re.findall(r"(?:^|\s)function " + nombre + r"\(", js))
        assert n == 1, f"{nombre} declarada {n} veces"


def test_ninguna_funcion_del_script_principal_se_declara_dos_veces():
    """La version general del test de arriba. Al 14/9, borrado el tablero de
    leads, no queda ninguna repetida: una nueva pisa a la anterior sin error y
    sin aviso, que es como el arrastre de Tareas estuvo roto en produccion."""
    nombres = re.findall(r"^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(",
                         _script_principal(), re.M)
    repetidas = sorted({n for n in nombres if nombres.count(n) > 1})
    assert len(nombres) > 100, f"el parser vio pocas funciones: {len(nombres)}"
    assert not repetidas, f"funciones declaradas mas de una vez: {repetidas}"


def test_el_tablero_de_leads_muerto_no_volvio():
    for resto in ("loadKanban", "renderKanban", "KANBAN_COLS", "_kanbanDragging",
                  "kanban-board"):
        assert resto not in HTML, resto


def test_el_arrastre_de_tareas_es_el_que_escribe_en_notion():
    js = _script_principal()
    drop = re.search(r"async function _kanbanDrop\(.*?\n\}", js, re.S).group(0)
    assert "/notion/estado" in drop


# ── JS del panel, corrido con node ───────────────────────────────────────────


def _funcion(nombre: str) -> str:
    m = re.search(r"\n(?:async )?function " + nombre + r"\(.*?\n\}", HTML, re.S)
    assert m, f"no encontre la funcion {nombre} en el dashboard"
    return m.group(0)


def _correr(cuerpo: str, tmp_path) -> str:
    colores = re.search(r"^const _COLOR_GRUPO_CLIENTE = .*$", HTML, re.M).group(0)
    fuente = "\n".join([
        dashboard.ESC_JS, colores,
        *(_funcion(n) for n in ("_ncPuedeArrastrar", "_ncDibujar", "_notionClientColHtml",
                                "_notionClientCardHtml", "_ncVinculoHtml",
                                # El boton "Recordatorio de llamado" de Seguimiento de leads.
                                "slBotonNotionHtml", "_ncDragStart",
                                "_ncOver", "_ncDrop", "_ncMover")),
        """
        function assert(cond, msg) { if (!cond) { throw new Error(msg); } }
        let _ncArrastrando = null, _ncGuardando = false;
        let _ncColumnas = [
          {estado: 'Demo Agendada', grupo: 'todo'},
          {estado: 'Hay que hacer Presupuesto', grupo: 'in_progress'},
        ];
        let _ncClientes = [
          {id: 1, name: 'Milky', status: 'Demo Agendada', grupo: 'todo', notion_page_id: 'c-1'},
        ];
        let fino = true;
        const window = {matchMedia: q => ({matches: fino})};
        const board = {innerHTML: ''};
        const document = {getElementById: id => id === 'notion-clients-board' ? board : null,
                          querySelectorAll: () => []};
        const alertas = [];
        function alert(m) { alertas.push(m); }
        const pedidos = [];
        let responder = null;
        function fetch(url, opts) {
          pedidos.push({url, body: JSON.parse(opts.body)});
          return new Promise((ok, mal) => { responder = {ok, mal}; });
        }
        function respuesta(status, body) {
          return {ok: status < 300, json: async () => body};
        }
        // En que columna esta dibujada una ficha.
        function columnaDe(nombre) {
          const cols = board.innerHTML.split('class="kanban-col"').slice(1);
          const i = cols.findIndex(c => c.includes('>' + nombre + '<'));
          return i === -1 ? null : _ncColumnas.concat([{estado: 'Sin clasificar'}])[i].estado;
        }
        function soltarEn(estado) {
          _ncArrastrando = 1;
          return _ncDrop({preventDefault() {},
                          currentTarget: {dataset: {estado}, classList: {remove() {}}}});
        }
        const tick = () => new Promise(r => setTimeout(r, 0));
        """,
        "(async () => {", cuerpo, "})().catch(e => { console.error(e); process.exit(1); });",
    ])
    archivo = tmp_path / "pipeline.js"
    archivo.write_text(fuente, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True,
                       text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout


@node
def test_si_notion_rechaza_la_ficha_vuelve_a_su_columna_y_avisa(tmp_path):
    _correr("""
      _ncDibujar();
      assert(columnaDe('Milky') === 'Demo Agendada', 'arranca en Demo');
      const hecho = soltarEn('Hay que hacer Presupuesto');
      await tick();
      // Mientras Notion contesta: en la columna nueva, atenuada.
      assert(columnaDe('Milky') === 'Hay que hacer Presupuesto', 'se ve movida');
      assert(board.innerHTML.includes('nc-guardando'), 'se ve esperando');
      assert(pedidos[0].url === '/api/notion-clients/1/estado', pedidos[0].url);
      assert(pedidos[0].body.estado === 'Hay que hacer Presupuesto', 'manda el estado');
      responder.ok(respuesta(502, {ok: false, error: 'Notion devolvio HTTP 400'}));
      await hecho;
      assert(columnaDe('Milky') === 'Demo Agendada', 'volvio: ' + columnaDe('Milky'));
      assert(!board.innerHTML.includes('nc-guardando'), 'ya no espera');
      assert(alertas.length === 1 && alertas[0].includes('HTTP 400'), alertas.join('|'));
      assert(_ncClientes[0].status === 'Demo Agendada', 'el dato tambien volvio');
    """, tmp_path)


@node
def test_si_se_corta_la_red_tambien_vuelve(tmp_path):
    _correr("""
      _ncDibujar();
      const hecho = soltarEn('Hay que hacer Presupuesto');
      await tick();
      responder.mal(new TypeError('Failed to fetch'));
      await hecho;
      assert(columnaDe('Milky') === 'Demo Agendada', 'volvio');
      assert(alertas.length === 1, 'avisa');
    """, tmp_path)


@node
def test_si_notion_acepta_queda_en_la_columna_nueva(tmp_path):
    _correr("""
      _ncDibujar();
      const hecho = soltarEn('Hay que hacer Presupuesto');
      await tick();
      responder.ok(respuesta(200, {ok: true, estado: 'Hay que hacer Presupuesto',
                                   grupo: 'in_progress'}));
      await hecho;
      assert(columnaDe('Milky') === 'Hay que hacer Presupuesto', 'quedo movida');
      assert(_ncClientes[0].grupo === 'in_progress', 'grupo al dia');
      assert(alertas.length === 0, 'sin avisos');
      assert(!_ncGuardando, 'libera el candado');
    """, tmp_path)


@node
def test_soltar_en_la_misma_columna_no_pide_nada(tmp_path):
    _correr("""
      _ncDibujar();
      await soltarEn('Demo Agendada');
      assert(pedidos.length === 0, 'no hay fetch');
    """, tmp_path)


@node
def test_en_touch_no_se_dibuja_arrastrable(tmp_path):
    """El drag de HTML5 no dispara en touch: en vez de dejarlo roto y callado,
    la ficha no se ofrece como arrastrable (y el CSS muestra la ayuda)."""
    _correr("""
      fino = false;
      _ncDibujar();
      assert(!board.innerHTML.includes('draggable="true"'), 'no arrastrable');
      assert(!board.innerHTML.includes('ondrop'), 'columnas sin destino');
      assert(board.innerHTML.includes('nc-fija'), 'marcada fija');
      fino = true;
      _ncDibujar();
      assert(board.innerHTML.includes('draggable="true"'), 'con mouse si');
      assert(board.innerHTML.includes('data-estado="Hay que hacer Presupuesto"'), 'destino');
    """, tmp_path)


@node
def test_sin_clasificar_no_recibe_fichas(tmp_path):
    _correr("""
      _ncClientes.push({id: 2, name: 'Rara', status: 'Estado Nuevo', grupo: 'otros'});
      _ncDibujar();
      assert(columnaDe('Rara') === 'Sin clasificar', 'va aparte');
      const sueltas = board.innerHTML.split('class="kanban-col"').pop();
      assert(!sueltas.startsWith('" data-estado'), 'Sin clasificar no es destino');
      assert(sueltas.includes('data-nc-id="2"'), 'pero su ficha se puede sacar de ahi');
    """, tmp_path)


def test_la_ayuda_del_panel_cambia_segun_mouse_o_touch():
    assert 'class="nc-ayuda-mouse"' in HTML and 'class="nc-ayuda-touch"' in HTML
    assert ".nc-ayuda-touch{display:none}" in HTML
    assert "@media (hover:none),(pointer:coarse){ .nc-ayuda-mouse{display:none}" in HTML
    assert "Para moverlas, abrilas allá" not in HTML
