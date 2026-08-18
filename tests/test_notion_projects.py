"""Espejo de la database Projects de Notion."""

import pytest

from database import (borrar_proyectos, create_task, get_projects,
                      get_task_by_id, init_db, update_task, upsert_project)


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    return ruta


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
