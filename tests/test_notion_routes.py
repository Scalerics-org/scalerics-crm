"""Rutas de Notion y el autosync al abrir el CRM."""

import re
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
    with patch("routes.notion.traer_proyectos", return_value=(5, None)), \
         patch("routes.notion.traer_y_aplicar", return_value=(3, None)):
        r = cliente.post("/api/notion/sync", headers=_AUTH)
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "cambiadas": 3, "proyectos": 5, "proyectos_error": None}


def test_el_sync_manual_que_anduvo_sin_cambios_dice_ok(app, cliente):
    with patch("routes.notion.traer_proyectos", return_value=(0, None)), \
         patch("routes.notion.traer_y_aplicar", return_value=(0, None)):
        r = cliente.post("/api/notion/sync", headers=_AUTH)
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "cambiadas": 0, "proyectos": 0, "proyectos_error": None}


def test_el_sync_manual_no_dice_ok_si_la_consulta_fallo(app, cliente):
    """Un cero de "fallo todo" no puede verse igual que un cero limpio.

    Esta ruta es el instrumento con el que una persona prueba la configuracion:
    el default que se shippea manda el pull a un endpoint que puede no existir.
    """
    with patch("routes.notion.traer_proyectos", return_value=(0, None)), \
         patch("routes.notion.traer_y_aplicar",
               return_value=(0, "la consulta a Notion devolvio HTTP 400")):
        r = cliente.post("/api/notion/sync", headers=_AUTH)
    assert r.status_code == 502
    d = r.get_json()
    assert d["ok"] is False
    assert "400" in d["error"]


def test_el_sync_manual_tambien_refresca_proyectos(app, cliente):
    """`/api/notion/sync` es el instrumento con el que una persona prueba a mano
    que la configuracion de Notion quedo bien, y la verificacion manual del
    plan arranca confirmando que aparecen los cinco proyectos. Hoy solo
    refresca tareas: un fallo del lado de proyectos no se puede reintentar a
    mano, hay que esperar el throttle de 600s del autosync."""
    with patch("routes.notion.traer_proyectos", return_value=(5, None)) as proyectos, \
         patch("routes.notion.traer_y_aplicar", return_value=(3, None)):
        r = cliente.post("/api/notion/sync", headers=_AUTH)
    proyectos.assert_called_once()
    assert r.status_code == 200
    assert r.get_json()["proyectos"] == 5


def test_una_excepcion_en_proyectos_no_le_pega_al_sync_manual_de_tareas(app, cliente):
    """Mismo constraint que el hallazgo 2, pero en el sync manual: si
    `traer_proyectos` explota, `traer_y_aplicar` tiene que seguir corriendo y
    la ruta tiene que seguir contestando 200 con las tareas que sí cambiaron."""
    with patch("routes.notion.traer_proyectos", side_effect=RuntimeError("database is locked")), \
         patch("routes.notion.traer_y_aplicar", return_value=(3, None)) as tareas:
        r = cliente.post("/api/notion/sync", headers=_AUTH)
    tareas.assert_called_once()
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    assert d["cambiadas"] == 3
    assert d["proyectos"] == 0


def test_el_sync_manual_queda_en_el_log_de_actividad(app, cliente):
    import sqlite3
    with patch("routes.notion.traer_proyectos", return_value=(1, None)), \
         patch("routes.notion.traer_y_aplicar", return_value=(2, None)):
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
    with patch("routes.notion.traer_proyectos", return_value=(0, None)), \
         patch("routes.notion.traer_y_aplicar", return_value=(0, "fallo")):
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


def test_el_autosync_sigue_trayendo_tareas_si_fallan_los_proyectos(monkeypatch):
    """`_maybe_sync_notion` trae primero los proyectos y despues las tareas; un
    fallo en el pull de proyectos no puede frenar la sync de tareas, que es lo
    que mas importa mantener al dia. Regresion: que nadie reordene las llamadas
    ni agregue un `return` temprano entre las dos."""
    monkeypatch.setenv("NOTION_TOKEN", "x")
    monkeypatch.setattr(
        dashboard.threading, "Thread",
        lambda target=None, daemon=None: type("T", (), {"start": lambda s: target()})())
    dashboard._notion_sync_state["at"] = 0.0

    with patch("services.notion_service.traer_proyectos",
               return_value=(0, "fallo a proposito")) as proyectos, \
         patch("services.notion_service.traer_y_aplicar",
               return_value=(1, None)) as tareas:
        dashboard._maybe_sync_notion("x.db")

    proyectos.assert_called_once()
    tareas.assert_called_once()


def test_el_autosync_sigue_trayendo_tareas_si_los_proyectos_explotan(monkeypatch):
    """Mismo constraint que el test de arriba, pero por el otro camino: el de
    arriba parchea un `(cambiadas, error)` de retorno, que es lo unico que el
    `try/except` de `traer_proyectos` sabe convertir en ese formato. Una
    excepcion real -- `sqlite3.OperationalError` por "database is locked"
    corriendo en un hilo daemon contra el mismo WAL que los requests vivos, o
    un payload malformado -- hoy escapa `traer_proyectos` sin que nada la
    atrape antes de `traer_y_aplicar`. Este test tiene que fallar contra el
    codigo actual: si pasa igual no esta ejercitando la excepcion."""
    monkeypatch.setenv("NOTION_TOKEN", "x")
    monkeypatch.setattr(
        dashboard.threading, "Thread",
        lambda target=None, daemon=None: type("T", (), {"start": lambda s: target()})())
    dashboard._notion_sync_state["at"] = 0.0

    with patch("services.notion_service.traer_proyectos",
               side_effect=RuntimeError("database is locked")) as proyectos, \
         patch("services.notion_service.traer_y_aplicar",
               return_value=(1, None)) as tareas:
        dashboard._maybe_sync_notion("x.db")

    proyectos.assert_called_once()
    tareas.assert_called_once()


def test_el_panel_de_proyectos_esta_en_el_sistema_de_permisos():
    """`ALL_PANELS` maneja el loop que oculta paneles segun el `panel_access`
    del usuario logueado. Si 'projects' no esta ahi, `nav-projects` nunca se
    oculta para nadie: un rol como el `Caller` sembrado (sin 'tasks' ni
    'projects') tiene Tareas oculto pero ve Proyectos, que lista los mismos
    titulos y estados de tareas agrupados por proyecto -- exactamente el dato
    que la restriccion de 'tasks' existe para no mostrarle.

    Tambien tiene que estar en los mapas de la nav movil (`NAV_PRIORITY`,
    `NAV_ICONS`, `NAV_LABELS`), o el panel solo se alcanza por el sidebar y el
    header movil queda sin titulo mientras esta activo."""
    html = dashboard.DASHBOARD_HTML

    all_panels = re.search(r"const ALL_PANELS = (\[.*?\]);", html).group(1)
    assert "'projects'" in all_panels

    nav_priority = re.search(r"const NAV_PRIORITY = (\[.*?\]);", html).group(1)
    assert "'projects'" in nav_priority

    nav_icons = re.search(r"const NAV_ICONS = \{(.*?)\};", html, re.S).group(1)
    assert "projects:" in nav_icons

    nav_labels = re.search(r"const NAV_LABELS = \{(.*?)\};", html, re.S).group(1)
    assert "projects:" in nav_labels


def test_el_editor_de_roles_puede_conceder_proyectos():
    """La segunda `ALL_PANELS` -- la del editor de roles en /admin/users -- arma
    los checkboxes de paneles desde esa lista. Si no tiene 'projects', un admin
    no puede conceder ni quitar ese panel a ningun rol. Vive en un template
    local a la funcion (`ADMIN_PAGE`), no en `DASHBOARD_HTML`, asi que esto lee
    la fuente del modulo en vez de la constante."""
    fuente = open(dashboard.__file__, encoding="utf-8").read()
    ocurrencias = re.findall(r"const ALL_PANELS = (\[.*?\]);", fuente)
    assert len(ocurrencias) == 2, "se esperaban las dos ALL_PANELS conocidas del panel access"
    for lista in ocurrencias:
        assert "'projects'" in lista

    panel_labels = re.search(r"const PANEL_LABELS = (\{.*?\});", fuente).group(1)
    assert "projects:" in panel_labels


def test_el_panel_de_tareas_tiene_el_boton_y_el_badge_de_notion():
    """Regresión: que nadie borre el front de Notion sin darse cuenta."""
    html = dashboard.DASHBOARD_HTML
    assert "task-notion-url" in html
    assert "task-notion-badge" in html
    assert "_enviarTareaANotion" in html


def test_el_badge_de_proyecto_en_tareas_no_depende_del_panel_de_proyectos():
    """Chequeo de presencia sobre el JS embebido, no de comportamiento (no hay
    infraestructura para ejecutar el front en esta suite):

    1. `loadTasks` tiene que llamar a `_asegurarMapaDeProyectos` -- si no, el
       badge de proyecto vuelve a depender de haber abierto el panel de
       Proyectos primero en esa carga de página.
    2. El guard de `_taskCardHtml` tiene que evaluar
       `_proyectosPorPagina[t.notion_project_page_id]` como condición del
       ternario, no solo la existencia del mapa -- si no, un proyecto sin
       nombre para esa tarea (mapa vacío, fetch fallido, proyecto borrado)
       deja un `<span class="proj-stage">` vacío en la tarjeta, que es
       exactamente lo que Task 4 declaró inaceptable.
    """
    html = dashboard.DASHBOARD_HTML

    load_tasks = re.search(r"async function loadTasks\(\) \{.*?\n\}", html, re.S).group(0)
    assert "_asegurarMapaDeProyectos" in load_tasks

    task_card = re.search(r"function _taskCardHtml\(t\) \{.*?\n\}", html, re.S).group(0)
    assert "_proyectosPorPagina[t.notion_project_page_id] ?" in task_card


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


# ── Arrastrar en el kanban ───────────────────────────────────────────────────


def test_mover_a_una_columna_devuelve_ok(app, cliente):
    task_id = create_task(app.config["DB_PATH"], title="Arrastrada")
    with patch("routes.notion.empujar_estado_exacto", return_value=(True, None)) as mover:
        r = cliente.post(f"/api/tasks/{task_id}/notion/estado",
                         json={"estado": "Up next"}, headers=_AUTH)
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    mover.assert_called_once_with(app.config["DB_PATH"], task_id, "Up next")


def test_mover_una_tarea_que_no_existe_da_404(app, cliente):
    r = cliente.post("/api/tasks/12345/notion/estado",
                     json={"estado": "Up next"}, headers=_AUTH)
    assert r.status_code == 404


def test_mover_a_una_columna_que_no_existe_da_400(app, cliente):
    task_id = create_task(app.config["DB_PATH"], title="Columna rara")
    with patch("routes.notion.empujar_estado_exacto") as mover:
        r = cliente.post(f"/api/tasks/{task_id}/notion/estado",
                         json={"estado": "Inventada"}, headers=_AUTH)
    assert r.status_code == 400
    mover.assert_not_called()


def test_si_notion_rechaza_el_movimiento_la_ruta_da_502(app, cliente):
    task_id = create_task(app.config["DB_PATH"], title="Rechazada")
    with patch("routes.notion.empujar_estado_exacto",
               return_value=(False, "Notion devolvio HTTP 400")):
        r = cliente.post(f"/api/tasks/{task_id}/notion/estado",
                         json={"estado": "Done"}, headers=_AUTH)
    assert r.status_code == 502
    assert r.get_json()["ok"] is False
    assert "400" in r.get_json()["error"]


def test_las_columnas_del_kanban_coinciden_con_grupos():
    """El front duplica los estados del tablero; este test es el que avisa.

    `_COLUMNAS_NOTION` vive en el JS embebido de dashboard.py y `GRUPOS` en el
    servicio. Si el equipo agrega un estado y solo se actualiza uno de los dos,
    las tareas caerian en una columna que no existe o se escribiria un estado
    que el tablero no tiene.
    """
    import re
    import dashboard
    from services.notion_service import GRUPOS

    bloque = re.search(r"const _COLUMNAS_NOTION = \[(.*?)\];",
                       dashboard.DASHBOARD_HTML, re.S)
    assert bloque, "no encontre _COLUMNAS_NOTION en el dashboard"
    enel_front = dict(re.findall(r"estado:'([^']+)',\s*grupo:'([^']+)'", bloque.group(1)))

    assert enel_front == GRUPOS, (
        "las columnas del kanban y GRUPOS se separaron: "
        f"front={enel_front} servicio={GRUPOS}")
