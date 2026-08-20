"""Espejo de la database Clientes de Notion."""

import re
from unittest.mock import patch

import pytest

import dashboard
from database import (borrar_notion_clients, get_notion_clients, init_db,
                      upsert_notion_client, upsert_project)
from services import notion_service as ns


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    return ruta


def test_alta_de_un_cliente(db):
    cid = upsert_notion_client(db, "pagina-1", "Milky", status="Demo Agendada",
                               descripcion="viene por Instagram",
                               due_date="2026-09-01", tiempo_estimado=3,
                               notion_project_page_id="proj-1")
    clientes = get_notion_clients(db)
    assert len(clientes) == 1
    c = clientes[0]
    assert c["id"] == cid
    assert c["name"] == "Milky"
    assert c["status"] == "Demo Agendada"
    assert c["descripcion"] == "viene por Instagram"
    assert c["due_date"] == "2026-09-01"
    assert c["tiempo_estimado"] == 3
    assert c["notion_project_page_id"] == "proj-1"


def test_upsert_no_duplica_y_actualiza_el_estado(db):
    primero = upsert_notion_client(db, "pagina-1", "Milky", status="Demo Agendada")
    segundo = upsert_notion_client(db, "pagina-1", "Milky SA",
                                   status="Presupuesto Aceptado")

    assert primero == segundo, "la misma pagina tiene que seguir siendo la misma fila"
    clientes = get_notion_clients(db)
    assert len(clientes) == 1
    assert clientes[0]["name"] == "Milky SA"
    assert clientes[0]["status"] == "Presupuesto Aceptado"


def test_borrar_saca_del_espejo_solo_a_los_pedidos(db):
    upsert_notion_client(db, "pagina-1", "Milky")
    upsert_notion_client(db, "pagina-2", "Garrido")

    assert borrar_notion_clients(db, {"pagina-1"}) == 1

    assert [c["name"] for c in get_notion_clients(db)] == ["Garrido"]


def test_borrar_sin_page_ids_no_hace_nada(db):
    upsert_notion_client(db, "pagina-1", "Milky")
    assert borrar_notion_clients(db, set()) == 0
    assert len(get_notion_clients(db)) == 1


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


@pytest.fixture
def notion_env(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "secret-de-prueba")
    monkeypatch.setenv("NOTION_CLIENTS_DATA_SOURCE_ID", "ds-clientes")


def _cliente(page_id, nombre, status=None, descripcion=None, due=None,
             tiempo=None, proyecto=None):
    """Una pagina como la devuelve Notion.

    Ojo con la property de estado: en esta database **no tiene nombre**, por
    eso la clave es la cadena vacia. El codigo la tiene que ubicar por tipo.
    """
    return {"id": page_id, "properties": {
        "Name": {"type": "title", "title": [{"plain_text": nombre}]},
        "": {"type": "status", "status": ({"name": status} if status else None)},
        "Descripcion": {"type": "rich_text",
                        "rich_text": ([{"plain_text": descripcion}] if descripcion else [])},
        "Due date": {"type": "date", "date": ({"start": due} if due else None)},
        "Tiempo Estimado": {"type": "number", "number": tiempo},
        "Project": {"type": "relation",
                    "relation": ([{"id": proyecto}] if proyecto else [])},
    }}


def test_el_pull_trae_los_clientes_con_su_estado_sin_nombre(db, notion_env):
    payload = {"results": [
        _cliente("c-1", "Milky", status="Demo Agendada", descripcion="por Instagram",
                 due="2026-09-01", tiempo=2, proyecto="proj-1"),
        _cliente("c-2", "Garrido"),
    ], "has_more": False}

    with patch("services.notion_service.requests.post",
               return_value=_Resp(200, payload)) as post:
        assert ns.traer_clientes(db) == (2, None)

    assert "data_sources/ds-clientes/query" in post.call_args.args[0]
    clientes = {c["name"]: c for c in get_notion_clients(db)}
    assert clientes["Milky"]["status"] == "Demo Agendada"
    assert clientes["Milky"]["descripcion"] == "por Instagram"
    assert clientes["Milky"]["due_date"] == "2026-09-01"
    assert clientes["Milky"]["tiempo_estimado"] == 2
    assert clientes["Milky"]["notion_project_page_id"] == "proj-1"
    assert clientes["Garrido"]["status"] is None


def test_el_pull_de_clientes_no_le_escribe_nada_a_notion(db, notion_env):
    payload = {"results": [_cliente("c-1", "Milky")], "has_more": False}
    with patch("services.notion_service.requests.post",
               return_value=_Resp(200, payload)), \
         patch("services.notion_service.requests.patch") as patch_req:
        ns.traer_clientes(db)
    patch_req.assert_not_called()


def test_un_cliente_que_desaparecio_se_borra(db, notion_env):
    upsert_notion_client(db, "c-vieja", "Cliente viejo")
    payload = {"results": [_cliente("c-1", "Milky")], "has_more": False}

    with patch("services.notion_service.requests.post",
               return_value=_Resp(200, payload)):
        ns.traer_clientes(db)

    assert [c["name"] for c in get_notion_clients(db)] == ["Milky"]


def test_si_la_consulta_de_clientes_falla_no_se_borra_nada(db, notion_env):
    upsert_notion_client(db, "c-vieja", "Cliente viejo")

    with patch("services.notion_service.requests.post", return_value=_Resp(500, {})):
        cambiados, error = ns.traer_clientes(db)

    assert cambiados == 0
    assert error
    assert len(get_notion_clients(db)) == 1


def test_si_la_paginacion_de_clientes_se_corta_no_se_borra_nada(db, notion_env):
    upsert_notion_client(db, "c-vieja", "Cliente viejo")
    payload = {"results": [_cliente("c-1", "Milky")], "has_more": True}

    with patch("services.notion_service.requests.post",
               return_value=_Resp(200, payload)):
        ns.traer_clientes(db)

    assert len(get_notion_clients(db)) == 2


def test_sin_data_source_de_clientes_el_pull_es_no_op(db, monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "secret-de-prueba")
    monkeypatch.delenv("NOTION_CLIENTS_DATA_SOURCE_ID", raising=False)

    with patch("services.notion_service.requests.post") as post:
        cambiados, error = ns.traer_clientes(db)

    post.assert_not_called()
    assert cambiados == 0
    assert error


# ── ruta, autosync y permisos ────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "token-de-test")
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    app = dashboard.create_app(ruta)
    app.config["TESTING"] = True
    return app


_AUTH = {"x-admin-token": "token-de-test"}


def test_la_ruta_devuelve_los_clientes_con_el_nombre_de_su_proyecto(app):
    ruta = app.config["DB_PATH"]
    upsert_project(ruta, "proj-1", "Desarrollo")
    upsert_notion_client(ruta, "c-1", "Milky", status="Demo Agendada",
                         notion_project_page_id="proj-1")
    upsert_notion_client(ruta, "c-2", "Garrido", status="Perdido")

    r = app.test_client().get("/api/notion-clients", headers=_AUTH)

    assert r.status_code == 200
    por_nombre = {c["name"]: c for c in r.get_json()["clientes"]}
    assert por_nombre["Milky"]["project_name"] == "Desarrollo"
    assert por_nombre["Garrido"]["project_name"] is None
    assert por_nombre["Garrido"]["status"] == "Perdido"


def test_no_hay_ruta_para_crear_ni_editar_un_cliente_de_notion(app):
    """El espejo es de solo lectura: si alguna vez aparece un POST o un PUT
    aca, el CRM puede escribirle a una database que el equipo maneja a mano."""
    cliente = app.test_client()
    assert cliente.post("/api/notion-clients", json={"name": "X"},
                        headers=_AUTH).status_code == 405
    assert cliente.put("/api/notion-clients/1", json={"name": "X"},
                       headers=_AUTH).status_code in (404, 405)


def test_el_autosync_sigue_trayendo_tareas_si_los_clientes_explotan(monkeypatch):
    """Misma garantia que ya tienen los proyectos: el pull de clientes es el
    menos importante de los tres, asi que ni un error devuelto ni una excepcion
    pueden frenar el de tareas."""
    monkeypatch.setenv("NOTION_TOKEN", "x")
    monkeypatch.setattr(
        dashboard.threading, "Thread",
        lambda target=None, daemon=None: type("T", (), {"start": lambda s: target()})())
    dashboard._notion_sync_state["at"] = 0.0

    with patch("services.notion_service.traer_clientes",
               side_effect=RuntimeError("database is locked")) as clientes, \
         patch("services.notion_service.traer_proyectos", return_value=(1, None)), \
         patch("services.notion_service.traer_y_aplicar",
               return_value=(1, None)) as tareas:
        dashboard._maybe_sync_notion("x.db")

    clientes.assert_called_once()
    tareas.assert_called_once()


def test_el_sync_manual_tambien_refresca_los_clientes(app):
    with patch("routes.notion.traer_clientes", return_value=(2, None)) as clientes, \
         patch("routes.notion.traer_proyectos", return_value=(5, None)), \
         patch("routes.notion.traer_y_aplicar", return_value=(0, None)):
        r = app.test_client().post("/api/notion/sync", headers=_AUTH)

    clientes.assert_called_once()
    assert r.get_json()["clientes"] == 2


def test_el_panel_de_clientes_de_notion_esta_en_el_sistema_de_permisos():
    """Sin 'notion_clients' en las dos `ALL_PANELS`, el panel no se le puede
    ocultar a nadie ni concederse desde el editor de roles -- y muestra el
    pipeline comercial entero, que es justo lo que un rol como `Caller` no
    tiene por que ver."""
    html = dashboard.DASHBOARD_HTML

    all_panels = re.search(r"const ALL_PANELS = (\[.*?\]);", html).group(1)
    assert "'notion_clients'" in all_panels

    nav_priority = re.search(r"const NAV_PRIORITY = (\[.*?\]);", html).group(1)
    assert "'notion_clients'" in nav_priority

    for mapa in ("NAV_ICONS", "NAV_LABELS"):
        cuerpo = re.search(r"const %s = \{(.*?)\};" % mapa, html, re.S).group(1)
        assert "notion_clients:" in cuerpo


def test_la_ruta_agrupa_los_clientes_como_el_tablero(app):
    ruta = app.config["DB_PATH"]
    upsert_notion_client(ruta, "c-1", "Milky", status="Demo Agendada")
    upsert_notion_client(ruta, "c-2", "Easy Rider",
                         status="Esperando Confirmación Presupuesto")
    upsert_notion_client(ruta, "c-3", "Garrido", status="Perdido")

    por_nombre = {c["name"]: c for c in
                  app.test_client().get("/api/notion-clients",
                                        headers=_AUTH).get_json()["clientes"]}

    assert por_nombre["Milky"]["grupo"] == "todo"
    assert por_nombre["Easy Rider"]["grupo"] == "in_progress"
    assert por_nombre["Garrido"]["grupo"] == "done"


def test_un_estado_nuevo_del_tablero_no_se_disfraza_de_pendiente(app):
    """El equipo agrega estados sin avisar -- en Tasks paso con "Waiting To
    Accept". Un estado que no conocemos tiene que caer en su propio grupo y no
    mezclarse con los pendientes, para que se vea que falta mapearlo."""
    ruta = app.config["DB_PATH"]
    upsert_notion_client(ruta, "c-1", "Nuevo", status="Estado Inventado")
    upsert_notion_client(ruta, "c-2", "Sin estado")

    por_nombre = {c["name"]: c for c in
                  app.test_client().get("/api/notion-clients",
                                        headers=_AUTH).get_json()["clientes"]}

    assert por_nombre["Nuevo"]["grupo"] == "otros"
    assert por_nombre["Sin estado"]["grupo"] == "otros"


def test_la_ruta_manda_una_columna_por_estado_del_tablero(app):
    """El kanban tiene que mostrar los estados de verdad, no los tres grupos.

    Las columnas salen de la ruta y no del JS porque una columna existe aunque
    no tenga ninguna ficha: "Presupuesto Aceptado" con cero clientes se tiene
    que ver igual, y eso no se puede deducir mirando las fichas que llegaron.
    """
    ruta = app.config["DB_PATH"]
    upsert_notion_client(ruta, "c-1", "Milky", status="Demo Agendada")

    datos = app.test_client().get("/api/notion-clients", headers=_AUTH).get_json()

    # El orden es el de las opciones de la property Status del tablero.
    assert [c["estado"] for c in datos["columnas"]] == [
        "Demo Agendada",
        "Hay que hacer Presupuesto",
        "Esperando Confirmación Presupuesto",
        "Perdido",
        "Presupuesto Rechazado",
        "Presupuesto Aceptado",
    ]
    assert [c["grupo"] for c in datos["columnas"]] == [
        "todo", "in_progress", "in_progress", "done", "done", "done"]


def test_el_kanban_no_dibuja_las_columnas_por_grupo(app):
    """Guard del bug que motivo el cambio: el tablero mostraba tres columnas
    (Pendientes / En progreso / Cerrados) y los seis estados del equipo
    quedaban invisibles. Si vuelve a aparecer una lista de columnas fija en el
    JS, esto lo agarra."""
    html = dashboard.DASHBOARD_HTML

    assert "_COLUMNAS_CLIENTES" not in html
    assert "loadNotionClients" in html
