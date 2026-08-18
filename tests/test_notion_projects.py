"""Espejo de la database Projects de Notion."""

from unittest.mock import patch

import pytest

from database import (borrar_proyectos, create_task, get_projects,
                      get_task_by_id, init_db, update_task, upsert_project)
from services import notion_service as ns


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    return ruta


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
    monkeypatch.setenv("NOTION_PROJECTS_DATA_SOURCE_ID", "ds-projects")


def _proyecto(page_id, nombre, stage=None, inicio=None, fin=None, lead=None):
    return {"id": page_id, "properties": {
        "Name": {"type": "title", "title": [{"plain_text": nombre}]},
        "Stage": {"type": "select", "select": ({"name": stage} if stage else None)},
        "Timeline": {"type": "date",
                     "date": ({"start": inicio, "end": fin} if inicio else None)},
        "Lead": {"type": "people",
                 "people": ([{"name": lead}] if lead else [])},
    }}


def test_alta_de_un_proyecto(db):
    pid = upsert_project(db, "pagina-1", "Desarrollo", stage="In Progress",
                         timeline_start="2026-08-01", timeline_end="2026-09-01",
                         lead="juan Tomasetti")
    proyectos = get_projects(db)
    assert len(proyectos) == 1
    p = proyectos[0]
    assert p["id"] == pid
    assert p["name"] == "Desarrollo"
    assert p["stage"] == "In Progress"
    assert p["timeline_start"] == "2026-08-01"
    assert p["lead"] == "juan Tomasetti"


def test_upsert_no_duplica_y_actualiza_el_nombre(db):
    primero = upsert_project(db, "pagina-1", "Desarrollo")
    segundo = upsert_project(db, "pagina-1", "Desarrollo 2026", stage="Done")

    assert primero == segundo, "la misma pagina tiene que seguir siendo la misma fila"
    proyectos = get_projects(db)
    assert len(proyectos) == 1
    assert proyectos[0]["name"] == "Desarrollo 2026"
    assert proyectos[0]["stage"] == "Done"


def test_borrar_un_proyecto_deja_sus_tareas_sin_proyecto(db):
    upsert_project(db, "pagina-1", "Desarrollo")
    task_id = create_task(db, title="Una tarea")
    update_task(db, task_id, notion_project_page_id="pagina-1")

    assert borrar_proyectos(db, {"pagina-1"}) == 1

    assert get_projects(db) == []
    assert get_task_by_id(db, task_id)["notion_project_page_id"] is None


def test_borrar_no_toca_las_tareas_de_otros_proyectos(db):
    upsert_project(db, "pagina-1", "Desarrollo")
    upsert_project(db, "pagina-2", "Administracion")
    t1 = create_task(db, title="De desarrollo")
    t2 = create_task(db, title="De administracion")
    update_task(db, t1, notion_project_page_id="pagina-1")
    update_task(db, t2, notion_project_page_id="pagina-2")

    borrar_proyectos(db, {"pagina-1"})

    assert get_task_by_id(db, t1)["notion_project_page_id"] is None
    assert get_task_by_id(db, t2)["notion_project_page_id"] == "pagina-2"
    assert len(get_projects(db)) == 1


def test_borrar_sin_page_ids_no_hace_nada(db):
    upsert_project(db, "pagina-1", "Desarrollo")
    assert borrar_proyectos(db, set()) == 0
    assert len(get_projects(db)) == 1


def test_trae_los_proyectos_del_tablero(db, notion_env):
    payload = {"results": [
        _proyecto("p-1", "Desarrollo", stage="In Progress",
                  inicio="2026-08-01", fin="2026-09-01", lead="juan Tomasetti"),
        _proyecto("p-2", "Administracion"),
    ], "has_more": False}

    with patch("services.notion_service.requests.post",
               return_value=_Resp(200, payload)) as post:
        assert ns.traer_proyectos(db) == (2, None)

    assert "data_sources/ds-projects/query" in post.call_args.args[0]
    proyectos = {p["name"]: p for p in get_projects(db)}
    assert proyectos["Desarrollo"]["stage"] == "In Progress"
    assert proyectos["Desarrollo"]["timeline_end"] == "2026-09-01"
    assert proyectos["Desarrollo"]["lead"] == "juan Tomasetti"
    assert proyectos["Administracion"]["stage"] is None


def test_el_pull_de_proyectos_no_le_escribe_nada_a_notion(db, notion_env):
    payload = {"results": [_proyecto("p-1", "Desarrollo")], "has_more": False}
    with patch("services.notion_service.requests.post",
               return_value=_Resp(200, payload)), \
         patch("services.notion_service.requests.patch") as patch_req:
        ns.traer_proyectos(db)
    patch_req.assert_not_called()


def test_un_proyecto_que_desaparecio_se_borra(db, notion_env):
    upsert_project(db, "p-vieja", "Proyecto viejo")
    task_id = create_task(db, title="Huerfana")
    update_task(db, task_id, notion_project_page_id="p-vieja")

    payload = {"results": [_proyecto("p-1", "Desarrollo")], "has_more": False}
    with patch("services.notion_service.requests.post",
               return_value=_Resp(200, payload)):
        ns.traer_proyectos(db)

    nombres = [p["name"] for p in get_projects(db)]
    assert nombres == ["Desarrollo"]
    assert get_task_by_id(db, task_id)["notion_project_page_id"] is None


def test_si_la_consulta_falla_no_se_borra_nada(db, notion_env):
    upsert_project(db, "p-vieja", "Proyecto viejo")

    with patch("services.notion_service.requests.post", return_value=_Resp(500, {})):
        cambiados, error = ns.traer_proyectos(db)

    assert cambiados == 0
    assert error
    assert len(get_projects(db)) == 1


def test_sin_data_source_de_proyectos_es_no_op(db, monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "x")
    monkeypatch.delenv("NOTION_PROJECTS_DATA_SOURCE_ID", raising=False)
    with patch("services.notion_service.requests.post") as post:
        cambiados, error = ns.traer_proyectos(db)
    assert cambiados == 0
    assert error
    post.assert_not_called()


def test_sin_token_es_no_op(db, monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    with patch("services.notion_service.requests.post") as post:
        assert ns.traer_proyectos(db)[0] == 0
    post.assert_not_called()
