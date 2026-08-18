"""Rutas de Notion y el autosync al abrir el CRM."""

from unittest.mock import patch

import pytest

import dashboard
from database import create_task, get_task_by_id, init_db, update_task


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
    # notion_status viene de la tarea (aca crear_pagina esta mockeado y no la
    # escribio, asi que es None); el front lo usa para el title del badge.
    assert r.get_json() == {"ok": True, "notion_page_id": "pagina-1",
                            "notion_status": None}
    crear.assert_called_once()


def test_la_ruta_devuelve_el_estado_que_quedo_guardado(app, cliente):
    """El front pinta el title del badge con esto, sin recargar la pagina."""
    db = app.config["DB_PATH"]
    task_id = create_task(db, title="Vinculada a una tarjeta de Up next")

    def _falso_vincular(_db, _task_id, _url):
        update_task(db, task_id, notion_page_id="pagina-9", notion_status="Up next")
        return "pagina-9"

    with patch("routes.notion.vincular_pagina", side_effect=_falso_vincular):
        r = cliente.post(f"/api/tasks/{task_id}/notion",
                         json={"url": "https://www.notion.so/abc"}, headers=_AUTH)
    assert r.get_json() == {"ok": True, "notion_page_id": "pagina-9",
                            "notion_status": "Up next"}


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
    with patch("routes.notion.traer_y_aplicar", return_value=(3, None)):
        r = cliente.post("/api/notion/sync", headers=_AUTH)
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "cambiadas": 3}


def test_el_sync_manual_que_anduvo_sin_cambios_dice_ok(app, cliente):
    with patch("routes.notion.traer_y_aplicar", return_value=(0, None)):
        r = cliente.post("/api/notion/sync", headers=_AUTH)
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "cambiadas": 0}


def test_el_sync_manual_no_dice_ok_si_la_consulta_fallo(app, cliente):
    """Un cero de "fallo todo" no puede verse igual que un cero limpio.

    Esta ruta es el instrumento con el que una persona prueba la configuracion:
    el default que se shippea manda el pull a un endpoint que puede no existir.
    """
    with patch("routes.notion.traer_y_aplicar",
               return_value=(0, "la consulta a Notion devolvio HTTP 400")):
        r = cliente.post("/api/notion/sync", headers=_AUTH)
    assert r.status_code == 502
    d = r.get_json()
    assert d["ok"] is False
    assert "400" in d["error"]


def test_el_sync_manual_queda_en_el_log_de_actividad(app, cliente):
    import sqlite3
    with patch("routes.notion.traer_y_aplicar", return_value=(2, None)):
        cliente.post("/api/notion/sync", headers=_AUTH)
    conn = sqlite3.connect(app.config["DB_PATH"])
    filas = conn.execute(
        "SELECT user_name, action, detail FROM activity_log WHERE action='notion_sync'"
    ).fetchall()
    conn.close()
    assert len(filas) == 1
    assert "2" in filas[0][2]


def test_un_sync_que_fallo_no_deja_actividad(app, cliente):
    import sqlite3
    with patch("routes.notion.traer_y_aplicar", return_value=(0, "fallo")):
        cliente.post("/api/notion/sync", headers=_AUTH)
    conn = sqlite3.connect(app.config["DB_PATH"])
    filas = conn.execute("SELECT id FROM activity_log WHERE action='notion_sync'").fetchall()
    conn.close()
    assert filas == []


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


def test_un_put_no_puede_setear_el_pareo_con_notion(app, cliente, hilo_sincronico):
    """El pareo lo escribe solo el service.

    Si el cliente pudiera setear `notion_page_id`, un PUT de `status` despues
    dejaria al CRM escribiendo Status en una pagina del tablero del equipo que
    nadie pareo.
    """
    db = app.config["DB_PATH"]
    task_id = create_task(db, title="Ajena")
    with patch("routes.tasks.empujar_estado"):
        r = cliente.put(f"/api/tasks/{task_id}",
                        json={"title": "Ajena", "notion_page_id": "pagina-de-otra-gente",
                              "notion_status": "Done"},
                        headers=_AUTH)
    assert r.status_code == 200
    t = get_task_by_id(db, task_id)
    assert t["notion_page_id"] is None
    assert t["notion_status"] is None
    # Lo que si es del cliente se guardo igual.
    assert t["title"] == "Ajena"


def test_un_post_no_puede_setear_el_pareo_con_notion(app, cliente):
    r = cliente.post("/api/tasks",
                     json={"title": "Nueva", "notion_page_id": "pagina-de-otra-gente"},
                     headers=_AUTH)
    assert r.status_code == 201
    task_id = r.get_json()["id"]
    assert get_task_by_id(app.config["DB_PATH"], task_id)["notion_page_id"] is None


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


def test_el_autosync_loguea_el_error_de_la_consulta(monkeypatch, caplog):
    """El autosync tambien tiene que entender el (cambiadas, error) del service."""
    monkeypatch.setenv("NOTION_TOKEN", "x")
    monkeypatch.setattr(
        dashboard.threading, "Thread",
        lambda target=None, daemon=None: type("T", (), {"start": lambda s: target()})())
    dashboard._notion_sync_state["at"] = 0.0

    with patch("services.notion_service.traer_y_aplicar",
               return_value=(0, "la consulta a Notion devolvio HTTP 400")), \
            caplog.at_level("WARNING"):
        dashboard._maybe_sync_notion("x.db")

    assert "HTTP 400" in caplog.text


def test_el_panel_de_tareas_tiene_el_boton_y_el_badge_de_notion():
    """Regresión: que nadie borre el front de Notion sin darse cuenta."""
    html = dashboard.DASHBOARD_HTML
    assert "task-notion-url" in html
    assert "task-notion-badge" in html
    assert "_enviarTareaANotion" in html


def _fuente_de(nombre):
    """El cuerpo de una funcion del JS embebido, para assertear sobre el front.

    La suite de Python no ejecuta el JS; esto es lo unico que evita que el fix
    del hallazgo 5 se pierda en un refactor del panel.
    """
    js = dashboard.DASHBOARD_HTML.split(f"async function {nombre}")[1]
    return js.split("\nasync function")[0]


def test_el_boton_de_notion_anda_desde_el_panel_de_cliente():
    """El boton `→ N` sale tambien en las filas del panel de cliente.

    Ahi `_allTasks` puede estar vacio (el panel de Tareas nunca se abrio) y hay
    que repintar el panel de cliente, no el de tareas.
    """
    fuente = _fuente_de("_enviarTareaANotion")
    assert "_cpData.tasks" in fuente
    assert "_cpSwitchTab('ctasks')" in fuente
    # Y un 500 de la ruta devuelve HTML: r.json() no puede tirar sin aviso.
    assert "try { d = await r.json(); }" in fuente
    assert "alert(" in fuente
