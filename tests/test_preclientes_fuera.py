"""Pre-clientes sale de la vista y Pipeline Notion decide quien es cliente.

Hasta el 14/9 el tablero de Pre-clientes era el unico lugar de la interfaz
donde un negocio cambiaba a `cerrado`, y Clientes solo muestra negocios en
`ETAPAS_CLIENTE`. Sacar el tablero sin reemplazo dejaba Clientes congelado.

Juan eligio el reemplazo: una ficha de Pipeline Notion se conecta una vez con
su negocio del CRM, y cuando llega a "Presupuesto Aceptado" (arrastrada desde
el CRM o movida en Notion y traida por el sync) el negocio pasa a Clientes.

Nunca se llama a la API real de Notion: todo `requests` va mockeado.
"""

import re
from unittest.mock import patch

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import (_connect, create_user, get_business, get_notion_client_by_id,
                      get_notion_clients, init_db, insert_business, update_business,
                      upsert_notion_client, vincular_notion_client)
from services import notion_service as ns

HTML = dashboard.DASHBOARD_HTML
ACEPTADO = ns.ESTADO_ACEPTADO
ESPERANDO = "Esperando Confirmación Presupuesto"
TOKEN = {"x-admin-token": "token-de-prueba"}


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


@pytest.fixture
def cli(db, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    monkeypatch.setenv("ADMIN_TOKEN", "token-de-prueba")
    app = dashboard.create_app(db)
    uid = create_user(db, name="Jefe", email="jefe@test.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Jefe"
    return c


_tel = iter(range(810000, 899999))


def _negocio(db, nombre="Milky", estado="demo_1"):
    bid = insert_business(db, {"name": nombre, "phone": f"+598{next(_tel)}"})
    update_business(db, bid, crm_status=estado)
    return bid


def _ficha(db, estado=ESPERANDO, page="c-1", nombre="Milky"):
    return upsert_notion_client(db, page, nombre, status=estado)


def _props(estado, nombre="Milky"):
    return {"Name": {"id": "title", "type": "title", "title": [{"plain_text": nombre}]},
            "": {"id": "st%3Ax", "type": "status", "status": {"name": estado}}}


def _eventos(db, bid):
    conn = _connect(db)
    try:
        return [r[0] for r in conn.execute(
            "SELECT new_status FROM lead_events WHERE lead_id = ?", (bid,))]
    finally:
        conn.close()


# ─── la vista ya no esta ──────────────────────────────────────────────────────

def test_el_tablero_de_preclientes_ya_no_esta_en_la_pantalla():
    assert 'id="pipeline-panel"' not in HTML
    assert 'id="nav-pipeline"' not in HTML
    assert "showPanel('pipeline')" not in HTML
    assert "cargarPreclientes" not in HTML
    assert ".pre-board" not in HTML


def test_pipeline_no_figura_en_las_listas_de_paneles():
    """Un panel en ALL_PANELS o NAV_PRIORITY sin HTML deja un boton muerto en
    la barra del celular o un permiso que no abre nada."""
    listas = re.findall(r"const (?:ALL_PANELS|NAV_PRIORITY) = \[([^\]]*)\]", HTML)
    assert listas, "no encontre las listas de paneles"
    for lista in listas:
        assert "'pipeline'" not in lista, lista


def test_los_selectores_de_usuarios_siguen_vivos():
    """`_usuarios` vivia al lado del tablero, pero la usan Clientes y Demos."""
    assert "async function _usuarios()" in HTML
    assert HTML.count("await _usuarios()") + HTML.count("_usuarios()]") >= 2


def test_la_api_de_preclientes_y_la_de_clientes_siguen(cli):
    """Se saca la vista, no el modulo: /api/clientes-activos y los tests del
    calendario siguen usando routes/preclientes.py."""
    assert cli.get("/api/preclientes").status_code == 200
    assert cli.get("/api/clientes-activos").status_code == 200


def test_la_pagina_principal_se_renderiza_sin_el_tablero(cli):
    r = cli.get("/")
    assert r.status_code == 200
    assert b"pipeline-panel" not in r.data
    assert b'id="nc-vinculo-modal"' in r.data


# ─── pasar a Clientes ─────────────────────────────────────────────────────────

def test_llegar_a_aceptado_pasa_al_negocio_conectado_a_clientes(db):
    bid = _negocio(db)
    fid = _ficha(db)
    vincular_notion_client(db, fid, bid)

    ns.cliente_cambio_de_estado(db, "c-1", ESPERANDO, ACEPTADO)

    assert get_business(db, bid)["crm_status"] == "cerrado"
    assert "cerrado" in _eventos(db, bid), "falta el evento en el historial"


def test_una_ficha_sin_conectar_no_toca_ningun_negocio(db):
    bid = _negocio(db)
    _ficha(db)

    ns.cliente_cambio_de_estado(db, "c-1", ESPERANDO, ACEPTADO)

    assert get_business(db, bid)["crm_status"] == "demo_1"


def test_otra_columna_no_pasa_a_clientes(db):
    bid = _negocio(db)
    fid = _ficha(db)
    vincular_notion_client(db, fid, bid)

    for estado in ("Perdido", "Presupuesto Rechazado", "Hay que hacer Presupuesto"):
        ns.cliente_cambio_de_estado(db, "c-1", ESPERANDO, estado)

    assert get_business(db, bid)["crm_status"] == "demo_1"


def test_un_cliente_mas_avanzado_no_vuelve_a_cerrado(db):
    """Reacomodar el tablero no puede devolver a `cerrado` a quien ya esta en
    desarrollo o finalizado."""
    for estado in ("en_desarrollo", "finalizado"):
        bid = _negocio(db, f"Negocio {estado}", estado)
        page = f"c-{estado}"
        fid = _ficha(db, page=page)
        vincular_notion_client(db, fid, bid)

        ns.cliente_cambio_de_estado(db, page, ESPERANDO, ACEPTADO)

        assert get_business(db, bid)["crm_status"] == estado
        assert "cerrado" not in _eventos(db, bid)


def test_un_error_al_pasar_a_clientes_no_corta_el_cambio_de_estado(db, monkeypatch):
    """Notion ya tiene el cambio: el espejo se actualiza igual."""
    bid = _negocio(db)
    fid = _ficha(db)
    vincular_notion_client(db, fid, bid)

    def _explota(*a, **kw):
        raise RuntimeError("base ocupada")
    monkeypatch.setattr(ns, "update_business", _explota)

    ns.cliente_cambio_de_estado(db, "c-1", ESPERANDO, ACEPTADO)

    assert get_notion_client_by_id(db, fid)["status"] == ACEPTADO


def test_el_sync_no_borra_la_conexion(db):
    bid = _negocio(db)
    fid = _ficha(db)
    vincular_notion_client(db, fid, bid)

    upsert_notion_client(db, "c-1", "Milky renombrada", status=ESPERANDO)

    assert get_notion_client_by_id(db, fid)["business_id"] == bid
    assert get_notion_clients(db)[0]["business_name"] == "Milky"


def test_moverla_en_notion_y_que_el_sync_la_traiga_tambien_pasa_a_clientes(db, notion_env):
    bid = _negocio(db)
    fid = _ficha(db)
    vincular_notion_client(db, fid, bid)
    respuesta = {"results": [{"id": "c-1", "properties": _props(ACEPTADO)}],
                 "has_more": False}

    with patch("services.notion_service.requests.post", return_value=_Resp(200, respuesta)):
        _, error = ns.traer_clientes(db)

    assert error is None
    assert get_business(db, bid)["crm_status"] == "cerrado"


# ─── la ruta de arrastre avisa ────────────────────────────────────────────────

def test_arrastrar_a_aceptado_pasa_a_clientes_y_lo_avisa(db, cli, notion_env):
    bid = _negocio(db)
    fid = _ficha(db)
    vincular_notion_client(db, fid, bid)

    with patch("services.notion_service.requests.get",
               return_value=_Resp(200, {"id": "c-1", "properties": _props(ESPERANDO)})), \
         patch("services.notion_service.requests.patch", return_value=_Resp(200, {})):
        r = cli.post(f"/api/notion-clients/{fid}/estado", json={"estado": ACEPTADO},
                     headers=TOKEN)

    d = r.get_json()
    assert d["ok"] is True
    assert d["paso_a_clientes"] is True
    assert d["sin_conectar"] is False
    assert get_business(db, bid)["crm_status"] == "cerrado"
    assert b"Milky" in cli.get("/api/clientes-activos").data


def test_arrastrar_a_aceptado_sin_conectar_lo_avisa(db, cli, notion_env):
    fid = _ficha(db)

    with patch("services.notion_service.requests.get",
               return_value=_Resp(200, {"id": "c-1", "properties": _props(ESPERANDO)})), \
         patch("services.notion_service.requests.patch", return_value=_Resp(200, {})):
        d = cli.post(f"/api/notion-clients/{fid}/estado", json={"estado": ACEPTADO},
                     headers=TOKEN).get_json()

    assert d["ok"] is True
    assert d["paso_a_clientes"] is False
    assert d["sin_conectar"] is True


def test_si_notion_rechaza_no_pasa_a_clientes(db, cli, notion_env):
    """Nunca quede cliente solo en el CRM con la ficha sin mover en Notion."""
    bid = _negocio(db)
    fid = _ficha(db)
    vincular_notion_client(db, fid, bid)

    with patch("services.notion_service.requests.get",
               return_value=_Resp(200, {"id": "c-1", "properties": _props(ESPERANDO)})), \
         patch("services.notion_service.requests.patch", return_value=_Resp(400, {})):
        r = cli.post(f"/api/notion-clients/{fid}/estado", json={"estado": ACEPTADO},
                     headers=TOKEN)

    assert r.status_code == 502
    assert get_business(db, bid)["crm_status"] == "demo_1"


# ─── conectar una ficha ───────────────────────────────────────────────────────

def test_conectar_guarda_la_conexion_y_la_devuelve_en_el_tablero(db, cli):
    bid = _negocio(db)
    fid = _ficha(db)

    d = cli.put(f"/api/notion-clients/{fid}/cliente-crm", json={"business_id": bid},
                headers=TOKEN).get_json()

    assert d == {"ok": True, "business_id": bid, "business_name": "Milky",
                 "paso_a_clientes": False}
    fichas = cli.get("/api/notion-clients").get_json()["clientes"]
    assert fichas[0]["business_id"] == bid
    assert fichas[0]["business_name"] == "Milky"
    assert get_business(db, bid)["crm_status"] == "demo_1"


def test_conectar_una_ficha_ya_aceptada_pasa_a_clientes_en_el_momento(db, cli):
    bid = _negocio(db)
    fid = _ficha(db, estado=ACEPTADO)

    d = cli.put(f"/api/notion-clients/{fid}/cliente-crm", json={"business_id": bid},
                headers=TOKEN).get_json()

    assert d["paso_a_clientes"] is True
    assert get_business(db, bid)["crm_status"] == "cerrado"


def test_desconectar(db, cli):
    bid = _negocio(db)
    fid = _ficha(db)
    vincular_notion_client(db, fid, bid)

    d = cli.put(f"/api/notion-clients/{fid}/cliente-crm", json={"business_id": None},
                headers=TOKEN).get_json()

    assert d["ok"] is True and d["business_id"] is None
    assert get_notion_client_by_id(db, fid)["business_id"] is None


@pytest.mark.parametrize("cuerpo", [{}, {"business_id": "7"}, {"business_id": True},
                                    {"business_id": 0}, {"business_id": -3},
                                    {"business_id": 1.5}, ["business_id"]])
def test_conectar_valida_lo_que_llega(db, cli, cuerpo):
    fid = _ficha(db)

    r = cli.put(f"/api/notion-clients/{fid}/cliente-crm", json=cuerpo, headers=TOKEN)

    assert r.status_code == 400
    assert get_notion_client_by_id(db, fid)["business_id"] is None


def test_conectar_con_alguien_que_no_existe(db, cli):
    fid = _ficha(db)
    r = cli.put(f"/api/notion-clients/{fid}/cliente-crm", json={"business_id": 99999},
                headers=TOKEN)
    assert r.status_code == 404


def test_conectar_una_ficha_que_no_existe(db, cli):
    bid = _negocio(db)
    r = cli.put("/api/notion-clients/99999/cliente-crm", json={"business_id": bid},
                headers=TOKEN)
    assert r.status_code == 404


# ─── el front ─────────────────────────────────────────────────────────────────

def test_cada_ficha_muestra_su_conexion():
    m = re.search(r"\nfunction _notionClientCardHtml\(.*?\n\}", HTML, re.S)
    assert m and "_ncVinculoHtml(c)" in m.group(0)


def test_el_buscador_no_trae_todos_los_leads_a_memoria():
    """Sin `page`, /api/leads carga todos los negocios en Python: el 502 del 28/8."""
    m = re.search(r"\nasync function _ncBuscarPersona\(.*?\n\}", HTML, re.S)
    assert m, "falta el buscador"
    assert "/api/leads?page=1&search=" in m.group(0)
