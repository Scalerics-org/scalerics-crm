"""Rutas de Notion y el autosync al abrir el CRM."""

from unittest.mock import patch

import pytest

import dashboard
from database import create_task, init_db


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "token-de-test")
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    app = dashboard.create_app(ruta)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def cliente(app):
    return app.test_client()


_AUTH = {"x-admin-token": "token-de-test"}


def test_crear_la_tarjeta_desde_el_crm(app, cliente):
    task_id = create_task(app.config["DB_PATH"], title="Nueva")
    with patch("routes.notion.crear_pagina", return_value="pagina-1") as crear:
        r = cliente.post(f"/api/tasks/{task_id}/notion", json={}, headers=_AUTH)
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "notion_page_id": "pagina-1"}
    crear.assert_called_once()


def test_vincular_cuando_viene_una_url(app, cliente):
    task_id = create_task(app.config["DB_PATH"], title="Existente")
    with patch("routes.notion.vincular_pagina", return_value="pagina-9") as vincular, \
         patch("routes.notion.crear_pagina") as crear:
        r = cliente.post(f"/api/tasks/{task_id}/notion",
                         json={"url": "https://www.notion.so/abc"}, headers=_AUTH)
    assert r.get_json()["notion_page_id"] == "pagina-9"
    vincular.assert_called_once()
    crear.assert_not_called()


def test_si_notion_falla_la_ruta_devuelve_502(app, cliente):
    task_id = create_task(app.config["DB_PATH"], title="Falla")
    with patch("routes.notion.crear_pagina", return_value=None):
        r = cliente.post(f"/api/tasks/{task_id}/notion", json={}, headers=_AUTH)
    assert r.status_code == 502
    assert r.get_json()["ok"] is False


def test_una_tarea_que_no_existe_da_404(app, cliente):
    r = cliente.post("/api/tasks/12345/notion", json={}, headers=_AUTH)
    assert r.status_code == 404


def test_el_sync_manual_devuelve_cuantas_cambiaron(app, cliente):
    with patch("routes.notion.traer_y_aplicar", return_value=3):
        r = cliente.post("/api/notion/sync", headers=_AUTH)
    assert r.get_json() == {"ok": True, "cambiadas": 3}


@pytest.fixture
def hilo_sincronico(monkeypatch):
    """Corre el target del Thread en el momento, para poder assertear la llamada."""
    monkeypatch.setattr(
        "routes.tasks.threading.Thread",
        lambda target=None, args=(), daemon=None:
        type("T", (), {"start": lambda s: target(*args)})(),
    )


def test_cambiar_el_estado_de_una_tarea_empuja_a_notion(app, cliente, hilo_sincronico):
    task_id = create_task(app.config["DB_PATH"], title="Se mueve")
    with patch("routes.tasks.empujar_estado") as empujar:
        r = cliente.put(f"/api/tasks/{task_id}", json={"status": "done"}, headers=_AUTH)
    assert r.status_code == 200
    empujar.assert_called_once_with(app.config["DB_PATH"], task_id)


def test_editar_otra_cosa_no_pega_a_notion(app, cliente, hilo_sincronico):
    task_id = create_task(app.config["DB_PATH"], title="Solo titulo")
    with patch("routes.tasks.empujar_estado") as empujar:
        cliente.put(f"/api/tasks/{task_id}", json={"title": "Otro titulo"}, headers=_AUTH)
    empujar.assert_not_called()


def test_el_autosync_no_dispara_sin_token(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    dashboard._notion_sync_state["at"] = 0.0
    dashboard._maybe_sync_notion("x.db")
    assert dashboard._notion_sync_state["at"] == 0.0


def test_el_throttle_del_autosync_evita_pegarle_en_cada_request(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "x")
    llamadas = []
    monkeypatch.setattr(dashboard.threading, "Thread",
                        lambda **kw: type("T", (), {"start": lambda s: llamadas.append(1)})())
    dashboard._notion_sync_state["at"] = 0.0

    dashboard._maybe_sync_notion("x.db")
    dashboard._maybe_sync_notion("x.db")
    dashboard._maybe_sync_notion("x.db")

    assert len(llamadas) == 1
