# Espejo de Projects de Notion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el CRM muestre un panel de Proyectos que refleja la database Projects del Notion de Scalerics, y que cada tarea sepa a qué proyecto pertenece.

**Architecture:** Mismo patrón que el espejo de tareas, que ya está en producción: una función de pull en `services/notion_service.py` que consulta el data source, hace upsert por `notion_page_id` y borra lo que ya no vuelve — con la guarda de que el barrido solo corre si el listado vino entero. Solo lectura: el CRM nunca escribe en Projects.

**Tech Stack:** Python 3, Flask, SQLite, `requests`, pytest + `unittest.mock.patch`. API REST de Notion, versión `2025-09-03` (data sources).

**Spec:** `docs/superpowers/specs/2026-08-18-notion-projects-mirror-design.md`

## Global Constraints

- **Solo lectura.** El CRM no escribe nada en la database Projects: ni `Stage`, ni `Lead`, ni proyectos nuevos. Ningún `POST /v1/pages` ni `PATCH` contra ese data source.
- **Sin `NOTION_TOKEN` o sin `NOTION_PROJECTS_DATA_SOURCE_ID`, todo es no-op**, y el CRM funciona igual.
- **El sync de proyectos no puede tumbar al de tareas.** Si falla, se loguea y el de tareas corre igual.
- **El barrido de borrados corre solo si el listado vino entero.** Si la consulta falló o la paginación se cortó, no se borra nada.
- `NOTION_PROJECTS_DATA_SOURCE_ID = "3ae65d94-deec-803d-8ea4-000b48070a4e"`.
- Nombres de funciones y docstrings en español, como `services/meta_reminders.py`. Tests en español.
- Los tests corren con `ANTHROPIC_API_KEY=dummy-para-tests python -m pytest -q`. Baseline actual: 285 passed.
- En el menú, los íconos son de Lucide (`<i data-lucide="...">`), no emojis.

---

### Task 1: Esquema y funciones de base

**Files:**
- Modify: `database.py` (bloque de `CREATE TABLE` en `init_db`, `_TASK_COLUMNS`)
- Test: `tests/test_notion_projects.py`

**Interfaces:**
- Produces:
  - Tabla `projects` con `notion_page_id` único.
  - Columna `notion_project_page_id TEXT` en `tasks`, aceptada por `create_task` y `update_task`.
  - `upsert_project(db_path, notion_page_id, name, stage=None, timeline_start=None, timeline_end=None, lead=None) -> int` (devuelve el id del CRM).
  - `get_projects(db_path) -> list[dict]` ordenados por nombre.
  - `borrar_proyectos(db_path, page_ids: set[str]) -> int` (borra y deja sin proyecto a sus tareas).

- [ ] **Step 1: Escribir los tests que fallan**

```python
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
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `ANTHROPIC_API_KEY=dummy-para-tests python -m pytest tests/test_notion_projects.py -v`
Expected: FAIL con `ImportError: cannot import name 'upsert_project' from 'database'`.

- [ ] **Step 3: Crear la tabla y la columna**

En `database.py`, dentro de `init_db`, junto a los otros `CREATE TABLE`:

```python
        conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                notion_page_id    TEXT UNIQUE,
                name              TEXT NOT NULL,
                stage             TEXT,
                timeline_start    TEXT,
                timeline_end      TEXT,
                lead              TEXT,
                notion_synced_at  TIMESTAMP
            )
        """)
```

Y con las otras migraciones aditivas de `tasks`, al lado de `notion_page_id`:

```python
        # A que proyecto de Notion pertenece la tarea. Se guarda el page id y no
        # el nombre: si el equipo renombra un proyecto, el pareo sobrevive.
        _add_column(conn, "tasks", "notion_project_page_id", "TEXT")
```

Agregar `"notion_project_page_id"` a `_TASK_COLUMNS`, que es la whitelist a mano
contra la que `update_task` valida.

- [ ] **Step 4: Escribir las funciones**

En `database.py`, junto a `get_tasks_notion`:

```python
def upsert_project(db_path: str, notion_page_id: str, name: str,
                   stage: str | None = None, timeline_start: str | None = None,
                   timeline_end: str | None = None, lead: str | None = None) -> int:
    """Da de alta o actualiza un proyecto espejado de Notion.

    La identidad es `notion_page_id`, no el nombre: renombrar un proyecto alla
    tiene que actualizar la fila, no crear una nueva.
    """
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _connect(db_path)
    try:
        conn.execute(
            """INSERT INTO projects
                   (notion_page_id, name, stage, timeline_start, timeline_end,
                    lead, notion_synced_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(notion_page_id) DO UPDATE SET
                   name = excluded.name,
                   stage = excluded.stage,
                   timeline_start = excluded.timeline_start,
                   timeline_end = excluded.timeline_end,
                   lead = excluded.lead,
                   notion_synced_at = excluded.notion_synced_at""",
            (notion_page_id, name, stage, timeline_start, timeline_end, lead, ahora),
        )
        conn.commit()
        fila = conn.execute("SELECT id FROM projects WHERE notion_page_id = ?",
                            (notion_page_id,)).fetchone()
        return fila["id"]
    finally:
        conn.close()


def get_projects(db_path: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute("SELECT * FROM projects ORDER BY name COLLATE NOCASE")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def borrar_proyectos(db_path: str, page_ids: set) -> int:
    """Borra proyectos que ya no estan en el tablero y despareja sus tareas.

    Es un espejo: no hay historial propio que perder. Lo que si importa es no
    dejar tareas apuntando a un proyecto que ya no existe.
    """
    if not page_ids:
        return 0
    marcas = ",".join("?" for _ in page_ids)
    valores = list(page_ids)
    conn = _connect(db_path)
    try:
        conn.execute(
            f"UPDATE tasks SET notion_project_page_id = NULL "
            f"WHERE notion_project_page_id IN ({marcas})", valores)
        cursor = conn.execute(
            f"DELETE FROM projects WHERE notion_page_id IN ({marcas})", valores)
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
```

`datetime` ya está importado en `database.py`; si no lo estuviera, agregar
`from datetime import datetime` arriba.

- [ ] **Step 5: Correr los tests**

Run: `ANTHROPIC_API_KEY=dummy-para-tests python -m pytest tests/test_notion_projects.py -v`
Expected: PASS.

- [ ] **Step 6: Correr la suite entera**

Run: `ANTHROPIC_API_KEY=dummy-para-tests python -m pytest -q`
Expected: PASS. `get_tasks` hace `SELECT *`, así que la columna nueva llega sola al front sin romper nada.

- [ ] **Step 7: Commit**

```bash
git add database.py tests/test_notion_projects.py
git commit -m "feat(notion): tabla projects y pareo de tareas con su proyecto"
```

---

### Task 2: Traer los proyectos desde Notion

**Files:**
- Modify: `services/notion_service.py`
- Test: `tests/test_notion_projects.py`

**Interfaces:**
- Consumes: `upsert_project`, `get_projects`, `borrar_proyectos` de la Task 1; `_config`, `_headers`, `TIMEOUT`, `API` del módulo.
- Produces: `traer_proyectos(db_path: str) -> tuple[int, str | None]` — `(cambiados, error)`, con `error=None` cuando la consulta funcionó.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_notion_projects.py`:

```python
from unittest.mock import patch

from services import notion_service as ns


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
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `ANTHROPIC_API_KEY=dummy-para-tests python -m pytest tests/test_notion_projects.py -v -k proyecto`
Expected: FAIL con `AttributeError: module 'services.notion_service' has no attribute 'traer_proyectos'`.

- [ ] **Step 3: Implementar**

En `services/notion_service.py`, con el resto de las funciones de pull:

```python
def _texto_de_titulo(prop: dict) -> str:
    partes = (prop or {}).get("title") or []
    return "".join(p.get("plain_text", "") for p in partes).strip()


def traer_proyectos(db_path: str) -> tuple[int, str | None]:
    """Espeja la database Projects en la tabla `projects` del CRM.

    Solo lectura: no se le escribe nada al tablero. Un proyecto que ya no
    vuelve en el listado se borra y sus tareas quedan sin proyecto, pero el
    barrido corre unicamente si el listado vino entero — si la consulta fallo,
    los que faltan no estan borrados, no los vimos.
    """
    cfg = _config()
    if not cfg:
        return 0, "falta NOTION_TOKEN: el sync con Notion esta apagado"
    token, version, _ = cfg

    ds = os.environ.get("NOTION_PROJECTS_DATA_SOURCE_ID", "")
    if not ds:
        return 0, "falta NOTION_PROJECTS_DATA_SOURCE_ID"

    url = f"{API}/data_sources/{ds}/query"
    cuerpo = {"page_size": 100}
    vistos = set()
    cambiados = 0
    completo = True

    while True:
        try:
            r = requests.post(url, headers=_headers(token, version),
                              json=cuerpo, timeout=TIMEOUT)
            if r.status_code >= 300:
                logger.warning("notion: query de proyectos fallo con %s: %s",
                               r.status_code, r.text[:300])
                return cambiados, f"la consulta de proyectos devolvio HTTP {r.status_code}"
            data = r.json()
        except Exception as e:
            logger.warning("notion: query de proyectos fallo", exc_info=True)
            return cambiados, f"la consulta de proyectos fallo: {type(e).__name__}"

        for pagina in data.get("results", []):
            page_id = pagina.get("id")
            if not page_id:
                continue
            props = pagina.get("properties", {})
            nombre = _texto_de_titulo(props.get("Name")) or "(sin nombre)"
            stage = ((props.get("Stage") or {}).get("select") or {}).get("name")
            fechas = (props.get("Timeline") or {}).get("date") or {}
            gente = (props.get("Lead") or {}).get("people") or []
            lead = ", ".join(p.get("name", "") for p in gente) or None

            upsert_project(db_path, page_id, nombre, stage=stage,
                           timeline_start=fechas.get("start"),
                           timeline_end=fechas.get("end"), lead=lead)
            vistos.add(page_id)
            cambiados += 1

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        if not cursor:
            logger.warning("notion: proyectos dijo has_more sin next_cursor, corto")
            completo = False
            break
        cuerpo["start_cursor"] = cursor

    if completo:
        conocidos = {p["notion_page_id"] for p in get_projects(db_path)}
        borrar_proyectos(db_path, conocidos - vistos)

    return cambiados, None
```

Agregar `get_projects`, `upsert_project` y `borrar_proyectos` al `from database import (...)` que ya existe arriba del módulo, sin abrir un segundo import.

- [ ] **Step 4: Correr los tests**

Run: `ANTHROPIC_API_KEY=dummy-para-tests python -m pytest tests/test_notion_projects.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/notion_service.py tests/test_notion_projects.py
git commit -m "feat(notion): traer los proyectos del tablero al CRM"
```

---

### Task 3: Enganchar el proyecto a la tarea y al autosync

**Files:**
- Modify: `services/notion_service.py` (`_adoptar` y el loop de `traer_y_aplicar`)
- Modify: `dashboard.py` (`_maybe_sync_notion`)
- Modify: `fly.toml`, `.env.example`
- Test: `tests/test_notion_sync.py`

**Interfaces:**
- Consumes: `traer_proyectos` de la Task 2.
- Produces: las tareas guardan `notion_project_page_id`; `_maybe_sync_notion` sincroniza proyectos antes que tareas.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_notion_sync.py`:

```python
def test_el_pull_guarda_a_que_proyecto_pertenece_la_tarea(db, notion_env):
    pagina = _pagina("pagina-nueva", None, "Backlog", titulo="Con proyecto")
    pagina["properties"]["Project"] = {"type": "relation",
                                       "relation": [{"id": "proyecto-1"}]}
    payload = {"results": [pagina], "has_more": False}

    with patch("services.notion_service.requests.post", return_value=_Resp(200, payload)):
        ns.traer_y_aplicar(db)

    t = get_tasks(db)[0]
    assert t["notion_project_page_id"] == "proyecto-1"


def test_una_tarjeta_sin_proyecto_no_inventa_uno(db, notion_env):
    payload = {"results": [_pagina("pagina-nueva", None, "Backlog")], "has_more": False}
    with patch("services.notion_service.requests.post", return_value=_Resp(200, payload)):
        ns.traer_y_aplicar(db)
    assert get_tasks(db)[0]["notion_project_page_id"] is None
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `ANTHROPIC_API_KEY=dummy-para-tests python -m pytest tests/test_notion_sync.py -v -k proyecto`
Expected: FAIL — `notion_project_page_id` viene `None` en el primero.

- [ ] **Step 3: Resolver la relación al adoptar y al reconciliar**

En `services/notion_service.py`, agregar el helper:

```python
def _proyecto_de(props: dict) -> str | None:
    """El page id del proyecto de una tarjeta, o None si no tiene."""
    relacion = (props.get("Project") or {}).get("relation") or []
    return relacion[0].get("id") if relacion else None
```

En `_adoptar`, pasar el proyecto al crear la tarea:

```python
    task_id = create_task(db_path, title=titulo, status=grupo_de(estado_notion),
                          notion_project_page_id=_proyecto_de(props))
```

Y en el loop de `traer_y_aplicar`, para las tareas ya pareadas, mantenerlo al día
justo antes del `_marcar` final del bloque:

```python
            proyecto = _proyecto_de(props)
            if proyecto != tarea.get("notion_project_page_id"):
                update_task(db_path, task_id, notion_project_page_id=proyecto)
```

- [ ] **Step 4: Sincronizar proyectos antes que tareas**

En `dashboard.py`, dentro de `_run()` de `_maybe_sync_notion`, antes de la llamada
a `traer_y_aplicar`:

```python
            from services.notion_service import traer_proyectos
            np, error_p = traer_proyectos(db_path)
            if error_p:
                logging.getLogger(__name__).warning("notion proyectos: %s", error_p)
            elif np:
                logging.getLogger(__name__).info("notion sync: %s proyectos", np)
```

Va en el mismo `try`, pero el error de proyectos no corta: `traer_y_aplicar` se
llama igual abajo, como hoy.

- [ ] **Step 5: Configuración**

En `fly.toml`, en el bloque `[env]`, junto al data source de tareas:

```toml
  # Data source de la database Projects. Espejo de solo lectura.
  NOTION_PROJECTS_DATA_SOURCE_ID = "3ae65d94-deec-803d-8ea4-000b48070a4e"
```

Y la misma línea en `.env.example`, bajo el bloque de Notion, con el comentario
`# Data source de Projects. Sin esto, el panel de proyectos queda vacio.`

- [ ] **Step 6: Correr la suite entera**

Run: `ANTHROPIC_API_KEY=dummy-para-tests python -m pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/notion_service.py dashboard.py fly.toml .env.example tests/test_notion_sync.py
git commit -m "feat(notion): cada tarea sabe a que proyecto pertenece"
```

---

### Task 4: El panel de Proyectos

**Files:**
- Create: `routes/projects.py`
- Modify: `dashboard.py` (nav, panel, JS, CSS, registro del blueprint)
- Test: `tests/test_notion_projects.py`

**Interfaces:**
- Consumes: `get_projects` de la Task 1, `notion_project_page_id` de la Task 3.
- Produces: `GET /api/projects` y el panel `projects` en el dashboard.

- [ ] **Step 1: Escribir el test de la ruta**

Agregar a `tests/test_notion_projects.py`:

```python
def test_la_ruta_devuelve_los_proyectos_con_sus_tareas(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "token-de-test")
    import dashboard
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    upsert_project(ruta, "p-1", "Desarrollo", stage="In Progress")
    task_id = create_task(ruta, title="Una tarea", status="in_progress")
    update_task(ruta, task_id, notion_project_page_id="p-1")

    app = dashboard.create_app(ruta)
    app.config["TESTING"] = True
    r = app.test_client().get("/api/projects", headers={"x-admin-token": "token-de-test"})

    assert r.status_code == 200
    datos = r.get_json()
    assert len(datos) == 1
    assert datos[0]["name"] == "Desarrollo"
    assert datos[0]["stage"] == "In Progress"
    assert [t["title"] for t in datos[0]["tasks"]] == ["Una tarea"]
    assert datos[0]["conteo"] == {"todo": 0, "in_progress": 1, "done": 0}
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `ANTHROPIC_API_KEY=dummy-para-tests python -m pytest tests/test_notion_projects.py -v -k ruta`
Expected: FAIL con 404.

- [ ] **Step 3: Escribir el blueprint**

Crear `routes/projects.py`:

```python
"""Panel de proyectos: espejo de solo lectura de la database Projects."""

from flask import Blueprint, current_app, jsonify

from database import get_projects, get_tasks

projects_bp = Blueprint("projects", __name__)


@projects_bp.route("/api/projects")
def api_projects():
    """Los proyectos con sus tareas y el conteo por estado.

    Solo lectura: no hay ruta para crear ni editar. El panel linkea a Notion,
    que es donde el equipo los maneja.
    """
    db = current_app.config["DB_PATH"]
    tareas = get_tasks(db)
    salida = []
    for p in get_projects(db):
        suyas = [t for t in tareas if t.get("notion_project_page_id") == p["notion_page_id"]]
        salida.append({
            **p,
            "tasks": suyas,
            "conteo": {
                "todo": sum(1 for t in suyas if t.get("status") == "todo"),
                "in_progress": sum(1 for t in suyas if t.get("status") == "in_progress"),
                "done": sum(1 for t in suyas if t.get("status") == "done"),
            },
        })
    return jsonify(salida)
```

Registrarlo en `dashboard.py`: `from routes.projects import projects_bp` con los
otros imports de blueprints, y sumarlo a la tupla del `for bp in (...)`.

- [ ] **Step 4: Correr el test**

Run: `ANTHROPIC_API_KEY=dummy-para-tests python -m pytest tests/test_notion_projects.py -v -k ruta`
Expected: PASS.

- [ ] **Step 5: El panel en el dashboard**

En el menú, después del ítem de Tareas (buscar `id="nav-tasks"`):

```html
  <div class="nav-item" id="nav-projects" onclick="showPanel('projects')"><i data-lucide="target" class="nav-icon"></i> Proyectos</div>
```

El panel, junto a los otros `<div class="panel">`:

```html
  <div id="projects-panel" class="panel">
    <div class="panel-head">
      <h1>Proyectos</h1>
      <p class="panel-sub">Espejo del tablero de Notion. Para editarlos, abrilos allá.</p>
    </div>
    <div id="projects-list"></div>
  </div>
```

En `showPanel`, junto a las otras cargas: `if (name === 'projects') loadProjects();`

Y el render, junto a las funciones de tareas:

```javascript
async function loadProjects() {
  const cont = document.getElementById('projects-list');
  if (!cont) return;
  cont.innerHTML = '<div class="tasks-empty">Cargando...</div>';
  const r = await fetch('/api/projects');
  const proyectos = await r.json();
  if (!proyectos.length) {
    cont.innerHTML = '<div class="tasks-empty">No hay proyectos en el tablero.</div>';
    return;
  }
  cont.innerHTML = proyectos.map(p => {
    const url = 'https://www.notion.so/' + (p.notion_page_id||'').replace(/-/g,'');
    const rango = p.timeline_start
      ? p.timeline_start + (p.timeline_end ? ' → ' + p.timeline_end : '') : '';
    return `<div class="proj-card">
      <div class="proj-head">
        <a class="proj-name" href="${url}" target="_blank" rel="noopener">${esc(p.name)}</a>
        ${p.stage ? `<span class="proj-stage">${esc(p.stage)}</span>` : ''}
        <span class="proj-counts">${p.conteo.todo} pendientes · ${p.conteo.in_progress} en progreso · ${p.conteo.done} hechas</span>
      </div>
      ${p.lead || rango ? `<div class="proj-meta">${p.lead ? esc(p.lead) : ''}${p.lead && rango ? ' · ' : ''}${rango}</div>` : ''}
      <div class="proj-tasks">${
        p.tasks.length
          ? p.tasks.map(t => `<div class="proj-task"><span class="task-status-badge ${t.status||'todo'}">${
              ({todo:'Pendiente', in_progress:'En progreso', done:'Hecha'})[t.status] || 'Pendiente'
            }</span> ${esc(t.title)}</div>`).join('')
          : '<div class="proj-task proj-vacio">Sin tareas</div>'
      }</div>
    </div>`;
  }).join('');
}
```

El CSS, junto a `.task-notion-badge` (tema oscuro) y su bloque `body.light`:

```css
.proj-card{background:#0d1420;border:1px solid #1e293b;border-radius:10px;padding:12px 14px;margin-bottom:10px}
.proj-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.proj-name{font-size:.95rem;font-weight:600;color:#e2e8f0;text-decoration:none}
.proj-name:hover{color:#0088cc}
.proj-stage{font-size:.72rem;color:#94a3b8;background:#1a2234;padding:2px 7px;border-radius:10px}
.proj-counts{font-size:.72rem;color:#64748b;margin-left:auto}
.proj-meta{font-size:.75rem;color:#64748b;margin-top:4px}
.proj-tasks{margin-top:8px;display:flex;flex-direction:column;gap:4px}
.proj-task{font-size:.8rem;color:#cbd5e1;display:flex;align-items:center;gap:7px}
.proj-vacio{color:#475569;font-style:italic}
```

```css
body.light .proj-card{background:#f8fafc;border-color:#e2e8f0}
body.light .proj-name{color:#0f172a}
body.light .proj-stage{background:#f1f5f9;color:#64748b}
body.light .proj-task{color:#334155}
```

En el kanban de tareas, dentro de `_taskCardHtml`, agregar el nombre del proyecto
después del badge de Notion:

```javascript
      ${t.notion_project_page_id && window._proyectosPorPagina ? `<span class="proj-stage">${esc(window._proyectosPorPagina[t.notion_project_page_id]||'')}</span>` : ''}
```

y en `loadProjects`, poblar el mapa para que el kanban lo pueda usar:

```javascript
  window._proyectosPorPagina = Object.fromEntries(proyectos.map(p => [p.notion_page_id, p.name]));
```

- [ ] **Step 6: Verificar el JS y la suite**

Run: `ANTHROPIC_API_KEY=dummy-para-tests python -m pytest -q`
Expected: PASS, incluido `tests/test_dashboard_js.py`, que corre `node --check` sobre
el JS embebido. Si ese test falla, casi siempre es un `\n` escrito con una barra
sola dentro de un string de Python: tiene que ir `\\n`.

- [ ] **Step 7: Commit**

```bash
git add routes/projects.py dashboard.py tests/test_notion_projects.py
git commit -m "feat(tareas): panel de proyectos espejado de Notion"
```

---

## Verificación manual (después de deployar)

1. Abrir el CRM → **Proyectos**: tienen que aparecer los cinco (Administracion, Desarrollo, General, Seguimiento Clientes, TSM), con TSM en *In Progress*.
2. Administracion y Desarrollo muestran dos tareas cada uno; los otros tres dicen "Sin tareas".
3. Clic en el nombre de un proyecto abre su página en Notion.
4. En **Tareas**, las tarjetas de esas cuatro tareas muestran el nombre de su proyecto.
5. En Notion, abrir el historial de cualquier proyecto: **no tiene que haber ninguna edición de la conexión CRM Scalerics**. Es el chequeo de que el espejo es de solo lectura.
