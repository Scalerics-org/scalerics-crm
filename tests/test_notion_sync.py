"""Sync de estado de tareas entre el CRM y la database Tasks de Notion."""

from unittest.mock import patch

import pytest

from database import create_task, get_task_by_id, init_db, update_task
from services import notion_service as ns


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    return ruta


@pytest.mark.parametrize("estado_notion,grupo", [
    (None, "todo"),
    ("", "todo"),
    ("Backlog", "todo"),
    ("Up next", "todo"),
    ("In progress", "in_progress"),
    ("On Hold", "in_progress"),
    ("Done", "done"),
])
def test_cada_estado_de_notion_cae_en_un_grupo_del_crm(estado_notion, grupo):
    assert ns.grupo_de(estado_notion) == grupo


def test_un_estado_desconocido_de_notion_no_mueve_la_tarea():
    # Si el equipo agrega una columna nueva al tablero, el CRM la trata como
    # pendiente en vez de explotar.
    assert ns.grupo_de("Esperando al cliente") == "todo"


@pytest.mark.parametrize("estado_crm,esperado", [
    ("todo", "Backlog"),
    ("in_progress", "In progress"),
    ("done", "Done"),
])
def test_mapeo_del_crm_a_notion(estado_crm, esperado):
    assert ns.estado_notion_para(estado_crm) == esperado


def test_no_se_escribe_si_notion_ya_esta_en_el_mismo_grupo():
    # La regla que impide que el CRM aplaste los estados finos del tablero.
    assert ns.hay_que_escribir("todo", "Up next") is False
    assert ns.hay_que_escribir("in_progress", "On Hold") is False
    assert ns.hay_que_escribir("done", "Done") is False


def test_se_escribe_cuando_el_grupo_cambio_de_verdad():
    assert ns.hay_que_escribir("done", "Up next") is True
    assert ns.hay_que_escribir("todo", "In progress") is True


def test_sin_estado_conocido_en_notion_se_escribe():
    assert ns.hay_que_escribir("todo", None) is True


def test_string_vacio_no_es_lo_mismo_que_nunca_sincronizado():
    # "" es la tarjeta real en la columna "Sin Status" (grupo "todo"): si el
    # CRM tambien esta en "todo" no hay nada que escribir. None es "todavia
    # no sincronizamos esta tarea", que siempre escribe. Si alguien "simplifica"
    # el `estado_notion or ""` de grupo_de, este test tiene que romperse.
    assert ns.hay_que_escribir("todo", "") is False
    assert ns.hay_que_escribir("todo", None) is True


def test_las_columnas_de_notion_se_pueden_guardar(db):
    task_id = create_task(db, title="Probar Notion")
    update_task(db, task_id, notion_page_id="abc123", notion_status="Up next")
    t = get_task_by_id(db, task_id)
    assert t["notion_page_id"] == "abc123"
    assert t["notion_status"] == "Up next"


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
    monkeypatch.setenv("NOTION_DATABASE_ID", "db-1")


def test_sin_token_no_se_intenta_nada(db, monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    task_id = create_task(db, title="Sin token")
    with patch("services.notion_service.requests") as req:
        assert ns.crear_pagina(db, task_id) is None
        assert ns.empujar_estado(db, task_id) is False
    req.post.assert_not_called()
    req.patch.assert_not_called()


def test_crear_pagina_guarda_el_id_y_el_estado(db, notion_env):
    task_id = create_task(db, title="Armar el modulo de pagos", status="in_progress")
    with patch("services.notion_service.requests.post",
               return_value=_Resp(200, {"id": "pagina-1"})) as post:
        assert ns.crear_pagina(db, task_id) == "pagina-1"

    cuerpo = post.call_args.kwargs["json"]
    assert cuerpo["properties"]["Status"]["status"]["name"] == "In progress"
    assert cuerpo["properties"]["CRM ID"]["number"] == task_id
    assert cuerpo["properties"]["Name"]["title"][0]["text"]["content"] == "Armar el modulo de pagos"

    t = get_task_by_id(db, task_id)
    assert t["notion_page_id"] == "pagina-1"
    assert t["notion_status"] == "In progress"
    assert t["notion_synced_at"]


def test_no_se_crea_una_segunda_pagina_para_la_misma_tarea(db, notion_env):
    task_id = create_task(db, title="Ya vinculada")
    update_task(db, task_id, notion_page_id="pagina-vieja")
    with patch("services.notion_service.requests.post") as post:
        assert ns.crear_pagina(db, task_id) == "pagina-vieja"
    post.assert_not_called()


def test_empujar_no_escribe_si_el_grupo_no_cambio(db, notion_env):
    task_id = create_task(db, title="Pendiente", status="todo")
    update_task(db, task_id, notion_page_id="pagina-1", notion_status="Up next")
    with patch("services.notion_service.requests.patch") as patch_req:
        assert ns.empujar_estado(db, task_id) is False
    patch_req.assert_not_called()


def test_empujar_escribe_cuando_el_grupo_cambio(db, notion_env):
    task_id = create_task(db, title="Se termino", status="done")
    update_task(db, task_id, notion_page_id="pagina-1", notion_status="Up next")
    with patch("services.notion_service.requests.patch",
               return_value=_Resp(200, {"id": "pagina-1"})) as patch_req:
        assert ns.empujar_estado(db, task_id) is True

    cuerpo = patch_req.call_args.kwargs["json"]
    assert cuerpo["properties"]["Status"]["status"]["name"] == "Done"
    assert get_task_by_id(db, task_id)["notion_status"] == "Done"


def test_una_tarea_sin_pagina_no_se_empuja(db, notion_env):
    task_id = create_task(db, title="Nunca vinculada", status="done")
    with patch("services.notion_service.requests.patch") as patch_req:
        assert ns.empujar_estado(db, task_id) is False
    patch_req.assert_not_called()


def test_notion_caido_no_propaga_la_excepcion(db, notion_env):
    task_id = create_task(db, title="Con error", status="done")
    update_task(db, task_id, notion_page_id="pagina-1", notion_status="Backlog")
    with patch("services.notion_service.requests.patch",
               side_effect=RuntimeError("timeout")):
        assert ns.empujar_estado(db, task_id) is False
    # El estado guardado no se toca si la escritura fallo.
    assert get_task_by_id(db, task_id)["notion_status"] == "Backlog"


def test_un_500_de_notion_no_marca_el_estado_como_sincronizado(db, notion_env):
    task_id = create_task(db, title="Con 500", status="done")
    update_task(db, task_id, notion_page_id="pagina-1", notion_status="Backlog")
    with patch("services.notion_service.requests.patch", return_value=_Resp(500, {})):
        assert ns.empujar_estado(db, task_id) is False
    assert get_task_by_id(db, task_id)["notion_status"] == "Backlog"


@pytest.mark.parametrize("url,esperado", [
    ("https://app.notion.com/p/Implementar-notificaciones-3b365d94deec80189624d8f91c06cf0c",
     "3b365d94-deec-8018-9624-d8f91c06cf0c"),
    ("https://www.notion.so/3b365d94deec80189624d8f91c06cf0c?pvs=5",
     "3b365d94-deec-8018-9624-d8f91c06cf0c"),
    ("3b365d94-deec-8018-9624-d8f91c06cf0c",
     "3b365d94-deec-8018-9624-d8f91c06cf0c"),
])
def test_se_saca_el_page_id_de_cualquier_forma_de_url(url, esperado):
    assert ns.page_id_de_url(url) == esperado


@pytest.mark.parametrize("basura", ["", "https://app.notion.com/p/sin-id", "cualquier cosa"])
def test_una_url_sin_page_id_devuelve_none(basura):
    assert ns.page_id_de_url(basura) is None


def test_vincular_escribe_el_crm_id_y_lee_el_estado_actual(db, notion_env):
    task_id = create_task(db, title="Ya existe en el tablero", status="todo")
    respuesta = {"id": "3b365d94-deec-8018-9624-d8f91c06cf0c",
                 "properties": {"Status": {"status": {"name": "On Hold"}}}}
    with patch("services.notion_service.requests.patch",
               return_value=_Resp(200, respuesta)) as patch_req:
        page_id = ns.vincular_pagina(
            db, task_id, "https://www.notion.so/3b365d94deec80189624d8f91c06cf0c")

    assert page_id == "3b365d94-deec-8018-9624-d8f91c06cf0c"
    cuerpo = patch_req.call_args.kwargs["json"]
    # Vincular escribe SOLO el CRM ID. El Status de la tarjeta no se toca:
    # la tarjeta ya vivia en el tablero y su estado es el que manda.
    assert cuerpo["properties"] == {"CRM ID": {"number": task_id}}

    t = get_task_by_id(db, task_id)
    assert t["notion_page_id"] == "3b365d94-deec-8018-9624-d8f91c06cf0c"
    assert t["notion_status"] == "On Hold"
    # Y el estado del CRM se alinea con lo que decia Notion.
    assert t["status"] == "in_progress"


def test_vincular_con_una_url_invalida_no_toca_nada(db, notion_env):
    task_id = create_task(db, title="Con URL mala")
    with patch("services.notion_service.requests.patch") as patch_req:
        assert ns.vincular_pagina(db, task_id, "no-es-una-url") is None
    patch_req.assert_not_called()
    assert get_task_by_id(db, task_id)["notion_page_id"] is None
