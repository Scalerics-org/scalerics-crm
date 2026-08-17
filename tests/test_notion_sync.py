"""Sync de estado de tareas entre el CRM y la database Tasks de Notion."""

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


def test_las_columnas_de_notion_se_pueden_guardar(db):
    task_id = create_task(db, title="Probar Notion")
    update_task(db, task_id, notion_page_id="abc123", notion_status="Up next")
    t = get_task_by_id(db, task_id)
    assert t["notion_page_id"] == "abc123"
    assert t["notion_status"] == "Up next"
