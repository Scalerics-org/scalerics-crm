# Sync de estado de tareas CRM ↔ Notion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que una tarea del CRM vinculada a mano con una tarjeta de la database Tasks de Notion mantenga el estado sincronizado en los dos sentidos, sin ensuciar el tablero del equipo.

**Architecture:** Reconciliación, no eventos. El CRM empuja el estado a Notion en el momento en que cambia (hilo aparte) y guarda en `notion_status` el estado que dejó. Para el sentido inverso, cuando alguien abre el CRM se pide a Notion todas las páginas con `CRM ID` cargado en un solo request, se compara el `Status` que ve contra `notion_status`, y donde difiere gana Notion. Al no llevar timestamps ni cola de eventos, un sync que falla no pierde nada: el siguiente ve el mismo desajuste.

**Tech Stack:** Python 3, Flask, SQLite, `requests`, pytest + `unittest.mock.patch`. API REST de Notion.

**Spec:** `docs/superpowers/specs/2026-08-17-notion-tasks-sync-design.md`

## Global Constraints

- **La database de Notion es de un equipo.** El CRM nunca borra ni archiva páginas, nunca toca páginas sin `CRM ID`, y nunca manda un `PATCH` cuyo valor sea igual al que ya está.
- **Solo viaja el estado.** `Assignee`, `Descripcion`, comentarios, `Project` y `Tiempo Estimado` quedan afuera en las dos direcciones.
- **Nada sale automático a Notion.** Una tarea llega al tablero solo si alguien la vinculó desde el CRM.
- **Sin `NOTION_TOKEN`, todo es no-op.** El CRM tiene que arrancar y funcionar igual, como funciona hoy sin `GMAIL_REFRESH_TOKEN`.
- **Notion caído nunca rompe un request del CRM.** Se loguea con `logging.getLogger(__name__)` y se sigue.
- Nombres de funciones y docstrings en español, como `services/meta_reminders.py`. Nombres de test en español, como `tests/test_calendly_autosync.py`.
- El grupo al que pertenece cada estado de Notion se **lee de la API** en la Task 1 y se anota en el runbook. No se asume.

---

### Task 1: Preparación en Notion y smoke test de la API

Esto va primero porque desbloquea todo lo demás y porque la versión de la API decide la forma de dos requests. Es la misma clase de trampa que dejó el webhook de Meta entregando en silencio: la versión se verifica antes, no cuando algo dejó de andar.

**Files:**
- Create: `scripts/notion_smoke.py`
- Create: `docs/puesta-en-produccion-notion.md`

**Interfaces:**
- Consumes: nada.
- Produces: el runbook con cuatro datos que las tareas siguientes leen — la `Notion-Version` que funcionó, el `data_source_id`, la forma del `parent` que acepta `POST /v1/pages`, y la tabla de estado → grupo de la property `Status`.

**Pasos manuales previos** (los hace una persona, no el código; van anotados en el runbook):

1. Entrar a Notion con la cuenta **Contacto Scalerics** (owner del workspace). La cuenta de Juan es invitada y no puede crear integraciones.
2. Crear una integración interna en `notion.so/my-integrations`, con capacidades de lectura y actualización de contenido. Copiar el token.
3. En la database **Tasks**, menú `···` → *Conexiones* → agregar la integración.
4. Agregar a la database una property **`CRM ID`** de tipo *Número*. Ocultarla en la vista "By status".
5. Guardar el token local en `.env` como `NOTION_TOKEN`, y en producción con
   `flyctl secrets set NOTION_TOKEN=<token> -a scalerics-crm`.

- [ ] **Step 1: Escribir el script de smoke**

```python
"""Smoke test de la API de Notion. No escribe nada, solo lee.

Confirma cuatro cosas antes de que exista una linea de logica de sync:
la version de API que el token acepta, el data_source_id de la database,
que la property CRM ID exista, y a que grupo pertenece cada estado.

Uso:
    NOTION_TOKEN=... NOTION_DATABASE_ID=... python scripts/notion_smoke.py
"""

import json
import os
import sys

import requests

VERSIONES = ["2025-09-03", "2022-06-28"]
DB_ID = os.environ.get("NOTION_DATABASE_ID", "3ae65d94-deec-8093-8c48-cfbe77e202d5")


def _headers(version: str) -> dict:
    return {
        "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
        "Notion-Version": version,
        "Content-Type": "application/json",
    }


def main() -> int:
    if not os.environ.get("NOTION_TOKEN"):
        print("Falta NOTION_TOKEN")
        return 1

    for version in VERSIONES:
        r = requests.get(f"https://api.notion.com/v1/databases/{DB_ID}",
                         headers=_headers(version), timeout=20)
        print(f"\n=== Notion-Version {version} -> HTTP {r.status_code}")
        if r.status_code != 200:
            print(r.text[:400])
            continue

        data = r.json()
        print("title:", "".join(t.get("plain_text", "") for t in data.get("title", [])))

        # En 2025-09-03 la database expone data_sources; en 2022-06-28 no existen
        # y las queries van contra /v1/databases/{id}/query.
        fuentes = data.get("data_sources") or []
        print("data_sources:", json.dumps(fuentes))

        props = data.get("properties", {})
        print("properties:", sorted(props))
        print("CRM ID presente:", "CRM ID" in props)

        status = props.get("Status", {}).get("status", {})
        for grupo in status.get("groups", []):
            ids = set(grupo.get("option_ids", []))
            nombres = [o["name"] for o in status.get("options", []) if o["id"] in ids]
            print(f"  grupo {grupo['name']}: {nombres}")

        return 0

    print("\nNinguna version funciono. Revisar el token y la conexion de la database.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Correr el smoke con el token real**

Run: `NOTION_TOKEN=<token> python scripts/notion_smoke.py`
Expected: HTTP 200 en alguna versión, `CRM ID presente: True`, y los grupos impresos con sus estados (esperado: algo tipo `To-do: ['Backlog', 'Up next']`, `In progress: ['In progress', 'On Hold']`, `Complete: ['Done']` — pero vale lo que imprima, no lo que dice acá).

Si `CRM ID presente: False`, volver al paso manual 4. Si los dos intentos dan 404, la integración no tiene la database conectada (paso manual 3).

- [ ] **Step 3: Anotar los hallazgos en el runbook**

Crear `docs/puesta-en-produccion-notion.md` con: los cinco pasos manuales de arriba, la versión que respondió 200, el `data_source_id` (o la nota de que esa versión no los usa), la tabla estado → grupo tal como la imprimió el script, y qué endpoint de query corresponde:

- Con `data_sources`: `POST /v1/data_sources/{data_source_id}/query` y parent de creación `{"type": "data_source_id", "data_source_id": "..."}`.
- Sin ellos: `POST /v1/databases/{id}/query` y parent `{"database_id": "..."}`.

Las Tasks 3 y 5 leen esta sección antes de escribir los requests.

- [ ] **Step 4: Commit**

```bash
git add scripts/notion_smoke.py docs/puesta-en-produccion-notion.md
git commit -m "chore(notion): smoke test de la API y runbook de puesta en produccion"
```

---

### Task 2: Columnas nuevas y mapeo de estados

**Files:**
- Modify: `database.py:83-116` (bloque de migraciones aditivas), `database.py:918-923` (`_TASK_COLUMNS`)
- Create: `services/notion_service.py`
- Test: `tests/test_notion_sync.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - Columnas `notion_page_id TEXT`, `notion_status TEXT`, `notion_synced_at TIMESTAMP` en `tasks`, aceptadas por `create_task` y `update_task`.
  - `services.notion_service.grupo_de(estado_notion: str | None) -> str` → devuelve `"todo"`, `"in_progress"` o `"done"`.
  - `services.notion_service.estado_notion_para(estado_crm: str) -> str` → `"Backlog"` / `"In progress"` / `"Done"`.
  - `services.notion_service.hay_que_escribir(estado_crm: str, notion_status: str | None) -> bool`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
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
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `python -m pytest tests/test_notion_sync.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.notion_service'`, y el último test con `ValueError: Invalid task columns`.

- [ ] **Step 3: Agregar las columnas**

En `database.py`, junto a las otras migraciones aditivas de `tasks` (buscar el bloque de `_add_column(conn, "businesses", ...)` y agregar debajo del `CREATE TABLE tasks`):

```python
        # Pareo con la database Tasks de Notion. `notion_status` guarda el
        # estado exacto que vimos alla la ultima vez (Backlog, Up next, On
        # Hold...), que es mas fino que los tres del CRM: es lo que nos permite
        # no pisarlo cuando el grupo no cambio.
        _add_column(conn, "tasks", "notion_page_id", "TEXT")
        _add_column(conn, "tasks", "notion_status", "TEXT")
        _add_column(conn, "tasks", "notion_synced_at", "TIMESTAMP")
```

Y en `_TASK_COLUMNS` (`database.py:918`) agregar la línea:

```python
    "notion_page_id", "notion_status", "notion_synced_at",
```

- [ ] **Step 4: Escribir el mapeo**

Crear `services/notion_service.py`:

```python
"""Sync del estado de las tareas con la database Tasks de Notion.

El modelo es reconciliacion, no eventos: el CRM guarda en `notion_status` el
estado exacto que vio en Notion la ultima vez, y en cada sync compara lo que
Notion dice hoy contra ese valor. Donde difiere, gana Notion. Si un sync falla
no se pierde nada, porque el siguiente ve el mismo desajuste.

Notion tiene mas estados que el CRM (Backlog, Up next, On Hold...), asi que el
mapeo no es 1:1. La regla de `hay_que_escribir` es la que impide que el CRM
aplaste un "Up next" del equipo con un "Backlog" que no aporta nada.
"""

import logging

logger = logging.getLogger(__name__)

# Grupo del CRM al que pertenece cada estado de Notion. Verificado con
# scripts/notion_smoke.py; ver docs/puesta-en-produccion-notion.md.
GRUPOS = {
    "Backlog": "todo",
    "Up next": "todo",
    "In progress": "in_progress",
    "On Hold": "in_progress",
    "Done": "done",
}

# A donde manda el CRM cada estado propio cuando el grupo cambio de verdad.
DESTINOS = {
    "todo": "Backlog",
    "in_progress": "In progress",
    "done": "Done",
}


def grupo_de(estado_notion: str | None) -> str:
    """Grupo del CRM para un estado de Notion.

    Un estado vacio (la columna "Sin Status") o uno que no conocemos cuentan
    como pendiente: si el equipo agrega una columna nueva al tablero, el sync
    la trata como to-do en vez de romperse.
    """
    return GRUPOS.get(estado_notion or "", "todo")


def estado_notion_para(estado_crm: str) -> str:
    return DESTINOS.get(estado_crm, "Backlog")


def hay_que_escribir(estado_crm: str, notion_status: str | None) -> bool:
    """True solo si el grupo cambio.

    Con el CRM en `todo` y la tarjeta en *Up next* devuelve False: el grupo de
    *Up next* ya es `todo`, y escribir "Backlog" seria pisarle el orden al
    equipo y ensuciar el historial de la pagina por nada.

    `None` no es lo mismo que `""`: None significa que nunca sincronizamos y no
    sabemos que hay en Notion, y ahi lo seguro es escribir. `""` es la columna
    "Sin Status", cuyo grupo es `todo` como cualquier otra columna pendiente.
    """
    if notion_status is None:
        return True
    return grupo_de(notion_status) != estado_crm
```

- [ ] **Step 5: Correr los tests**

Run: `python -m pytest tests/test_notion_sync.py -v`
Expected: PASS.

- [ ] **Step 6: Correr la suite entera**

Run: `python -m pytest -q`
Expected: PASS. Las columnas nuevas entran por `SELECT *` en `get_tasks`, así que nada existente debería romperse.

- [ ] **Step 7: Commit**

```bash
git add database.py services/notion_service.py tests/test_notion_sync.py
git commit -m "feat(notion): columnas de pareo y mapeo de estados CRM <-> Notion"
```

---

### Task 3: Push — crear la tarjeta y actualizar el estado

**Files:**
- Modify: `services/notion_service.py`
- Modify: `.env.example`
- Test: `tests/test_notion_sync.py`

**Interfaces:**
- Consumes: `grupo_de`, `estado_notion_para`, `hay_que_escribir` de la Task 2.
- Produces:
  - `crear_pagina(db_path: str, task_id: int) -> str | None` → devuelve el `notion_page_id` creado, o `None` si no hay token / falló.
  - `empujar_estado(db_path: str, task_id: int) -> bool` → `True` si escribió en Notion.
  - `_config() -> tuple[str, str, str] | None` → `(token, version, db_id)` o `None` si falta el token.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_notion_sync.py`:

```python
from unittest.mock import patch

from database import create_task, get_task_by_id, update_task


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
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `python -m pytest tests/test_notion_sync.py -v -k "crear or empujar or token or caido or 500"`
Expected: FAIL — `AttributeError: module 'services.notion_service' has no attribute 'crear_pagina'`.

- [ ] **Step 3: Implementar el push**

Antes de escribir esto, leer la sección de endpoints de `docs/puesta-en-produccion-notion.md` (Task 1) y usar la forma de `parent` que quedó anotada ahí. El código de abajo usa `{"database_id": ...}`; si el runbook dice `data_source_id`, cambiar `_parent()` y nada más — los tests no dependen de la forma.

Agregar a `services/notion_service.py`:

```python
import os
from datetime import datetime

import requests

from database import get_task_by_id, update_task

API = "https://api.notion.com/v1"
TIMEOUT = 20


def _config() -> tuple[str, str, str] | None:
    """(token, version, database_id) o None si no hay token.

    Sin NOTION_TOKEN todo el modulo es no-op: el CRM tiene que funcionar igual
    sin Notion, como funciona hoy sin GMAIL_REFRESH_TOKEN.
    """
    token = os.environ.get("NOTION_TOKEN", "")
    if not token:
        return None
    version = os.environ.get("NOTION_VERSION", "2025-09-03")
    db_id = os.environ.get("NOTION_DATABASE_ID", "3ae65d94-deec-8093-8c48-cfbe77e202d5")
    return token, version, db_id


def _headers(token: str, version: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": version,
        "Content-Type": "application/json",
    }


def _parent(db_id: str) -> dict:
    ds = os.environ.get("NOTION_DATA_SOURCE_ID", "")
    if ds:
        return {"type": "data_source_id", "data_source_id": ds}
    return {"database_id": db_id}


def _marcar(db_path: str, task_id: int, estado_notion: str, page_id: str | None = None) -> None:
    campos = {
        "notion_status": estado_notion,
        "notion_synced_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if page_id:
        campos["notion_page_id"] = page_id
    update_task(db_path, task_id, **campos)


def crear_pagina(db_path: str, task_id: int) -> str | None:
    """Crea la tarjeta en Notion para una tarea del CRM.

    Idempotente por diseno: si la tarea ya tiene `notion_page_id` devuelve ese
    y no toca Notion. Es la primera de las defensas contra duplicados en un
    tablero que usa el equipo.
    """
    cfg = _config()
    if not cfg:
        return None
    token, version, db_id = cfg

    tarea = get_task_by_id(db_path, task_id)
    if not tarea:
        return None
    if tarea.get("notion_page_id"):
        return tarea["notion_page_id"]

    estado = estado_notion_para(tarea.get("status") or "todo")
    cuerpo = {
        "parent": _parent(db_id),
        "properties": {
            "Name": {"title": [{"text": {"content": tarea.get("title") or "(sin titulo)"}}]},
            "Status": {"status": {"name": estado}},
            "CRM ID": {"number": task_id},
        },
    }
    try:
        r = requests.post(f"{API}/pages", headers=_headers(token, version),
                          json=cuerpo, timeout=TIMEOUT)
        if r.status_code >= 300:
            logger.warning("notion: crear pagina fallo con %s: %s", r.status_code, r.text[:300])
            return None
        page_id = r.json().get("id")
    except Exception:
        logger.warning("notion: crear pagina fallo", exc_info=True)
        return None

    if not page_id:
        return None
    _marcar(db_path, task_id, estado, page_id=page_id)
    return page_id


def empujar_estado(db_path: str, task_id: int) -> bool:
    """Lleva el estado del CRM a la tarjeta, solo si cambio el grupo."""
    cfg = _config()
    if not cfg:
        return False
    token, version, db_id = cfg

    tarea = get_task_by_id(db_path, task_id)
    if not tarea or not tarea.get("notion_page_id"):
        return False

    estado_crm = tarea.get("status") or "todo"
    if not hay_que_escribir(estado_crm, tarea.get("notion_status")):
        return False

    destino = estado_notion_para(estado_crm)
    try:
        r = requests.patch(
            f"{API}/pages/{tarea['notion_page_id']}",
            headers=_headers(token, version),
            json={"properties": {"Status": {"status": {"name": destino}}}},
            timeout=TIMEOUT,
        )
        if r.status_code >= 300:
            logger.warning("notion: patch fallo con %s: %s", r.status_code, r.text[:300])
            return False
    except Exception:
        logger.warning("notion: patch fallo", exc_info=True)
        return False

    _marcar(db_path, task_id, destino)
    return True
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_notion_sync.py -v`
Expected: PASS.

- [ ] **Step 5: Documentar las variables de entorno**

En `.env.example`, después del bloque de Meta:

```
# ── Notion (sync de estado de tareas) ────────────────────────────────────────
# Token de la integracion interna. Sin el, el sync entero es no-op.
# La integracion la crea un owner del workspace (Contacto Scalerics) y hay que
# conectarle la database Tasks. Ver docs/puesta-en-produccion-notion.md.
NOTION_TOKEN=
# Database Tasks. Ya tiene default en el codigo; se pone aca solo para cambiarla.
NOTION_DATABASE_ID=
# Version de la API que confirmo scripts/notion_smoke.py.
NOTION_VERSION=2025-09-03
# Solo si el smoke test mostro data_sources.
NOTION_DATA_SOURCE_ID=
# Cada cuantos segundos, como maximo, se consulta Notion al abrir el CRM.
NOTION_SYNC_EVERY=600
```

- [ ] **Step 6: Commit**

```bash
git add services/notion_service.py tests/test_notion_sync.py .env.example
git commit -m "feat(notion): crear la tarjeta y empujar el estado del CRM a Notion"
```

---

### Task 4: Vincular una tarjeta que ya existe

Sin esto, la primera tarea que se cargue en el CRM y que ya exista en el tablero ("Realizar modulo pagos Plexo", "Mejorar CRM. Hacer uno nuevo?") genera el duplicado que todo el diseño intenta evitar.

**Files:**
- Modify: `services/notion_service.py`
- Test: `tests/test_notion_sync.py`

**Interfaces:**
- Consumes: `_config`, `_headers`, `_marcar` de la Task 3.
- Produces:
  - `page_id_de_url(url: str) -> str | None` → el uuid con guiones, o `None`.
  - `vincular_pagina(db_path: str, task_id: int, url: str) -> str | None` → el page_id vinculado, o `None`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
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
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `python -m pytest tests/test_notion_sync.py -v -k "url or vincular"`
Expected: FAIL — `AttributeError: ... has no attribute 'page_id_de_url'`.

- [ ] **Step 3: Implementar la vinculación**

```python
import re

_UUID_SUELTO = re.compile(r"([0-9a-f]{32})", re.I)
_UUID_CON_GUIONES = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)


def page_id_de_url(url: str) -> str | None:
    """Saca el uuid de una URL de Notion y lo devuelve con guiones.

    Las URLs vienen en dos formas: con el id pegado al final del slug del
    titulo, o suelto. La API acepta las dos, pero normalizamos para que el
    pareo contra `notion_page_id` sea siempre el mismo string.
    """
    if not url:
        return None
    con_guiones = _UUID_CON_GUIONES.search(url)
    if con_guiones:
        return con_guiones.group(1).lower()
    suelto = _UUID_SUELTO.search(url.replace("-", ""))
    if not suelto:
        return None
    h = suelto.group(1).lower()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def vincular_pagina(db_path: str, task_id: int, url: str) -> str | None:
    """Pega una tarea del CRM a una tarjeta que ya existe en el tablero.

    Escribe solo `CRM ID` en la pagina; el `Status` de la tarjeta queda como
    esta y el CRM se alinea con el. La tarjeta ya vivia ahi, asi que su estado
    es el que manda.
    """
    cfg = _config()
    if not cfg:
        return None
    token, version, _ = cfg

    page_id = page_id_de_url(url)
    if not page_id:
        return None
    if not get_task_by_id(db_path, task_id):
        return None

    try:
        r = requests.patch(
            f"{API}/pages/{page_id}",
            headers=_headers(token, version),
            json={"properties": {"CRM ID": {"number": task_id}}},
            timeout=TIMEOUT,
        )
        if r.status_code >= 300:
            logger.warning("notion: vincular fallo con %s: %s", r.status_code, r.text[:300])
            return None
        estado = (r.json().get("properties", {})
                  .get("Status", {}).get("status") or {}).get("name") or ""
    except Exception:
        logger.warning("notion: vincular fallo", exc_info=True)
        return None

    update_task(db_path, task_id, status=grupo_de(estado))
    _marcar(db_path, task_id, estado, page_id=page_id)
    return page_id
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_notion_sync.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/notion_service.py tests/test_notion_sync.py
git commit -m "feat(notion): vincular una tarea del CRM a una tarjeta existente"
```

---

### Task 5: Pull — reconciliar el tablero contra el CRM

**Files:**
- Modify: `services/notion_service.py`
- Test: `tests/test_notion_sync.py`

**Interfaces:**
- Consumes: `_config`, `_headers`, `grupo_de` de las tasks anteriores.
- Produces: `traer_y_aplicar(db_path: str) -> int` → cuántas tareas del CRM cambió.

Invariante que hay que preservar: `traer_y_aplicar` escribe con `update_task` directo, **nunca** llamando al endpoint HTTP del CRM ni a `empujar_estado`. Es lo que garantiza que un cambio traído de Notion no rebote de vuelta.

- [ ] **Step 1: Escribir los tests que fallan**

```python
def _pagina(page_id, crm_id, estado):
    return {"id": page_id,
            "properties": {"CRM ID": {"number": crm_id},
                           "Status": {"status": {"name": estado}}}}


def test_el_pull_aplica_el_estado_de_notion(db, notion_env):
    task_id = create_task(db, title="Movida en Notion", status="todo")
    update_task(db, task_id, notion_page_id="pagina-1", notion_status="Backlog")

    payload = {"results": [_pagina("pagina-1", task_id, "In progress")], "has_more": False}
    with patch("services.notion_service.requests.post", return_value=_Resp(200, payload)):
        assert ns.traer_y_aplicar(db) == 1

    t = get_task_by_id(db, task_id)
    assert t["status"] == "in_progress"
    assert t["notion_status"] == "In progress"


def test_un_movimiento_dentro_del_mismo_grupo_no_cambia_el_estado_del_crm(db, notion_env):
    task_id = create_task(db, title="De Backlog a Up next", status="todo")
    update_task(db, task_id, notion_page_id="pagina-1", notion_status="Backlog")

    payload = {"results": [_pagina("pagina-1", task_id, "Up next")], "has_more": False}
    with patch("services.notion_service.requests.post", return_value=_Resp(200, payload)):
        ns.traer_y_aplicar(db)

    t = get_task_by_id(db, task_id)
    assert t["status"] == "todo"
    # Pero si guardamos el estado fino: sin esto el proximo push le pisaria el
    # "Up next" con un "Backlog".
    assert t["notion_status"] == "Up next"


def test_el_pull_ignora_paginas_sin_tarea_en_el_crm(db, notion_env):
    payload = {"results": [_pagina("pagina-huerfana", 9999, "Done")], "has_more": False}
    with patch("services.notion_service.requests.post", return_value=_Resp(200, payload)):
        assert ns.traer_y_aplicar(db) == 0


def test_el_pull_pagina_con_cursor(db, notion_env):
    t1 = create_task(db, title="Una", status="todo")
    t2 = create_task(db, title="Otra", status="todo")
    update_task(db, t1, notion_page_id="p1", notion_status="Backlog")
    update_task(db, t2, notion_page_id="p2", notion_status="Backlog")

    respuestas = [
        _Resp(200, {"results": [_pagina("p1", t1, "Done")],
                    "has_more": True, "next_cursor": "cursor-2"}),
        _Resp(200, {"results": [_pagina("p2", t2, "Done")], "has_more": False}),
    ]
    with patch("services.notion_service.requests.post", side_effect=respuestas) as post:
        assert ns.traer_y_aplicar(db) == 2

    assert post.call_count == 2
    assert post.call_args_list[1].kwargs["json"]["start_cursor"] == "cursor-2"
    assert get_task_by_id(db, t1)["status"] == "done"
    assert get_task_by_id(db, t2)["status"] == "done"


def test_el_pull_deja_actividad_a_nombre_de_notion(db, notion_env):
    import sqlite3
    task_id = create_task(db, title="Auditable", status="todo")
    update_task(db, task_id, notion_page_id="pagina-1", notion_status="Backlog")

    payload = {"results": [_pagina("pagina-1", task_id, "Done")], "has_more": False}
    with patch("services.notion_service.requests.post", return_value=_Resp(200, payload)):
        ns.traer_y_aplicar(db)

    conn = sqlite3.connect(db)
    filas = conn.execute(
        "SELECT user_name, action, detail FROM activity_log WHERE entity_type='task'"
    ).fetchall()
    conn.close()
    assert filas, "el cambio traido de Notion tiene que quedar en el log"
    assert filas[0][0] == "notion"
    assert "done" in filas[0][2]


def test_el_pull_sin_token_no_pega_a_notion(db, monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    with patch("services.notion_service.requests.post") as post:
        assert ns.traer_y_aplicar(db) == 0
    post.assert_not_called()


def test_notion_caido_en_el_pull_no_propaga(db, notion_env):
    with patch("services.notion_service.requests.post", side_effect=RuntimeError("boom")):
        assert ns.traer_y_aplicar(db) == 0
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `python -m pytest tests/test_notion_sync.py -v -k "pull or pagina_con_cursor"`
Expected: FAIL — `AttributeError: ... has no attribute 'traer_y_aplicar'`.

- [ ] **Step 3: Implementar el pull**

El endpoint de query sale del runbook de la Task 1: con `NOTION_DATA_SOURCE_ID` va `/v1/data_sources/{ds}/query`, si no `/v1/databases/{id}/query`.

```python
from database import log_activity


def _url_de_query(db_id: str) -> str:
    ds = os.environ.get("NOTION_DATA_SOURCE_ID", "")
    if ds:
        return f"{API}/data_sources/{ds}/query"
    return f"{API}/databases/{db_id}/query"


def traer_y_aplicar(db_path: str) -> int:
    """Reconcilia: trae las paginas pareadas y aplica lo que Notion diga.

    Un solo request (mas los que haga falta por cursor). No lleva timestamps:
    compara el Status de Notion contra `notion_status`, que es el ultimo valor
    que los dos lados acordaron. Si un sync se pierde, el siguiente ve el mismo
    desajuste y lo arregla igual.

    Escribe con update_task directo, nunca por el endpoint del CRM: por eso un
    cambio traido de Notion no rebota de vuelta.
    """
    cfg = _config()
    if not cfg:
        return 0
    token, version, db_id = cfg

    cuerpo = {
        "filter": {"property": "CRM ID", "number": {"is_not_empty": True}},
        "page_size": 100,
    }
    cambiadas = 0
    url = _url_de_query(db_id)

    while True:
        try:
            r = requests.post(url, headers=_headers(token, version),
                              json=cuerpo, timeout=TIMEOUT)
            if r.status_code >= 300:
                logger.warning("notion: query fallo con %s: %s", r.status_code, r.text[:300])
                return cambiadas
            data = r.json()
        except Exception:
            logger.warning("notion: query fallo", exc_info=True)
            return cambiadas

        for pagina in data.get("results", []):
            props = pagina.get("properties", {})
            task_id = props.get("CRM ID", {}).get("number")
            if not task_id:
                continue
            tarea = get_task_by_id(db_path, int(task_id))
            if not tarea:
                continue  # la tarea se borro en el CRM; la tarjeta queda en paz

            estado_notion = (props.get("Status", {}).get("status") or {}).get("name") or ""
            if estado_notion == (tarea.get("notion_status") or ""):
                continue  # nada cambio alla

            nuevo_grupo = grupo_de(estado_notion)
            if nuevo_grupo != (tarea.get("status") or "todo"):
                update_task(db_path, int(task_id), status=nuevo_grupo)
                log_activity(db_path, "notion", "task_updated", "task", int(task_id),
                             tarea.get("title", ""), f"estado: {nuevo_grupo} (desde Notion)")
                cambiadas += 1
            # El estado fino se guarda igual, aunque el grupo no haya cambiado.
            _marcar(db_path, int(task_id), estado_notion)

        if not data.get("has_more"):
            break
        cuerpo["start_cursor"] = data.get("next_cursor")

    return cambiadas
```

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_notion_sync.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/notion_service.py tests/test_notion_sync.py
git commit -m "feat(notion): reconciliar el estado del tablero contra el CRM"
```

---

### Task 6: Rutas, hook en el update de tareas y autosync

**Files:**
- Create: `routes/notion.py`
- Modify: `routes/tasks.py:61-72` (`api_update_task`)
- Modify: `dashboard.py:5073-5109` (bloque de `_maybe_sync_calendly`), `dashboard.py:5118-5119` (blueprints), `dashboard.py:5148` (call site)
- Test: `tests/test_notion_routes.py`

**Interfaces:**
- Consumes: `crear_pagina`, `vincular_pagina`, `empujar_estado`, `traer_y_aplicar`.
- Produces:
  - `POST /api/tasks/<id>/notion` — con `{"url": "..."}` vincula; sin body crea la tarjeta. Devuelve `{"ok": true, "notion_page_id": "..."}`.
  - `POST /api/notion/sync` — devuelve `{"ok": true, "cambiadas": N}`.
  - `dashboard._maybe_sync_notion(db_path)` y `dashboard._notion_sync_state`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
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
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `python -m pytest tests/test_notion_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'routes.notion'`.

- [ ] **Step 3: Escribir el blueprint**

Crear `routes/notion.py`:

```python
"""Rutas del sync con Notion."""

from flask import Blueprint, current_app, jsonify, request, session

from database import get_task_by_id, log_activity
from services.notion_service import crear_pagina, traer_y_aplicar, vincular_pagina

notion_bp = Blueprint("notion", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


@notion_bp.route("/api/tasks/<int:task_id>/notion", methods=["POST"])
def api_vincular_tarea(task_id):
    """Manda la tarea al tablero, o la pega a una tarjeta que ya existe.

    Nada de esto pasa solo: el tablero de Notion es del equipo y se llena a
    mano, tarea por tarea.
    """
    db = _db()
    tarea = get_task_by_id(db, task_id)
    if not tarea:
        return jsonify({"ok": False, "error": "la tarea no existe"}), 404

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    page_id = vincular_pagina(db, task_id, url) if url else crear_pagina(db, task_id)
    if not page_id:
        return jsonify({"ok": False, "error": "Notion no aceptó la operación"}), 502

    log_activity(db, session.get("user_name", "sistema"), "task_updated", "task",
                 task_id, tarea.get("title", ""),
                 "vinculada a Notion" if url else "enviada a Notion",
                 user_id=session.get("user_id"))
    return jsonify({"ok": True, "notion_page_id": page_id})


@notion_bp.route("/api/notion/sync", methods=["POST"])
def api_sync():
    return jsonify({"ok": True, "cambiadas": traer_y_aplicar(_db())})
```

- [ ] **Step 4: Enganchar el push en el update de tareas**

En `routes/tasks.py` hay dos cambios de imports. Hoy `threading` se importa **adentro** de `api_create_task`; hay que subirlo al tope del módulo, porque el fixture `hilo_sincronico` parchea `routes.tasks.threading.Thread` y necesita que el atributo exista a nivel de módulo. Sacar el `import threading` local de `api_create_task` (el `import os` local de la misma función queda como está, ya hay uno arriba).

```python
import os
import threading

from database import (create_task, delete_task, get_task_by_id, get_tasks,
                      log_activity, update_task, get_task_progress_history)
from services.email_service import send_task_assignment_email
from services.notion_service import empujar_estado
```

Y en `api_update_task`, después del `log_activity` y antes del `return`:

```python
    # Si la tarea esta vinculada a Notion, el cambio de estado viaja para alla.
    # En un hilo aparte para no demorar la respuesta, igual que el mail de
    # asignacion. empujar_estado no hace nada si la tarea no tiene pagina.
    if "status" in data:
        threading.Thread(target=empujar_estado, args=(db, task_id), daemon=True).start()
```

- [ ] **Step 5: Registrar el blueprint y el autosync**

En `dashboard.py`, junto a los imports de blueprints, agregar `from routes.notion import notion_bp`, y sumarlo a la tupla de `register_blueprint` (`dashboard.py:5118`):

```python
    for bp in (leads_bp, demos_bp, calendar_bp, wa_bp, pipeline_bp, tasks_bp, budgets_bp, tokens_bp, meta_bp, calendly_bp, notion_bp):
```

Después del bloque de `_maybe_sync_calendly` (que termina en `dashboard.py:5109`), agregar:

```python
_notion_sync_state = {"at": 0.0}
_notion_sync_lock = threading.Lock()
NOTION_SYNC_EVERY = int(os.environ.get("NOTION_SYNC_EVERY", "600"))


def _maybe_sync_notion(db_path: str) -> None:
    """Trae los cambios de estado del tablero de Notion cuando alguien abre el CRM.

    Mismo motivo que en Calendly: la maquina de Fly se duerme sin trafico, asi
    que un cron interno no correria. Hilo aparte para no demorar la carga, y
    throttle para no consultar Notion en cada request.
    """
    if not os.environ.get("NOTION_TOKEN"):
        return
    now = time.time()
    with _notion_sync_lock:
        if now - _notion_sync_state["at"] < NOTION_SYNC_EVERY:
            return
        _notion_sync_state["at"] = now

    def _run():
        try:
            from services.notion_service import traer_y_aplicar
            n = traer_y_aplicar(db_path)
            if n:
                logging.getLogger(__name__).info("notion sync: %s tareas actualizadas", n)
        except Exception:
            logging.getLogger(__name__).warning("notion sync falló", exc_info=True)

    threading.Thread(target=_run, daemon=True).start()
```

Y en el call site donde hoy se llama a `_maybe_sync_calendly` (`dashboard.py:5148`), agregar la línea de al lado:

```python
            _maybe_sync_notion(app.config["DB_PATH"])
```

- [ ] **Step 6: Correr los tests**

Run: `python -m pytest tests/test_notion_routes.py tests/test_notion_sync.py -v`
Expected: PASS.

- [ ] **Step 7: Correr la suite entera**

Run: `python -m pytest -q`
Expected: PASS. Si `test_main.py` chequea la lista de blueprints o rutas registradas, actualizarlo.

- [ ] **Step 8: Commit**

```bash
git add routes/notion.py routes/tasks.py dashboard.py tests/test_notion_routes.py
git commit -m "feat(notion): rutas de vinculacion, push en el cambio de estado y autosync"
```

---

### Task 7: Front — badge y botón en la lista de tareas

**Files:**
- Modify: `dashboard.py:1485-1492` (modal de tarea), `dashboard.py:3100-3156` (`_taskRowHtml`), `dashboard.py:3269-3294` (`openEditTaskModal`), `dashboard.py:3313-3360` (`submitAddTask`)

**Interfaces:**
- Consumes: `POST /api/tasks/<id>/notion` de la Task 6. Los campos `notion_page_id` y `notion_status` ya llegan al front porque `get_tasks` hace `SELECT *`.
- Produces: nada que consuman otras tasks.

- [ ] **Step 1: Agregar el campo de URL al modal**

En el modal `add-task-modal`, después del bloque de "Estado" y antes de `<input type="hidden" id="task-edit-id">`:

```html
    <div style="margin-top:10px" id="task-notion-block">
      <label class="modal-label">Notion (opcional)</label>
      <input type="text" id="task-notion-url" class="modal-input"
             placeholder="Pegá la URL de la tarjeta para vincularla">
      <div id="task-notion-linked" style="font-size:.78rem;color:#0088cc;margin-top:4px"></div>
    </div>
```

- [ ] **Step 2: Mostrar el estado de vinculación al abrir el modal**

En `openEditTaskModal`, después de la línea de `task-status-input`:

```javascript
  const notionUrlInput = document.getElementById('task-notion-url');
  const notionLinked = document.getElementById('task-notion-linked');
  notionUrlInput.value = '';
  if (t.notion_page_id) {
    notionUrlInput.style.display = 'none';
    notionLinked.innerHTML = 'Vinculada a Notion' +
      (t.notion_status ? ' (' + esc(t.notion_status) + ')' : '') +
      ' · <a href="https://www.notion.so/' + t.notion_page_id.replace(/-/g,'') +
      '" target="_blank" rel="noopener" style="color:#0088cc">abrir</a>';
  } else {
    notionUrlInput.style.display = '';
    notionLinked.textContent = '';
  }
```

- [ ] **Step 3: Vincular al guardar**

En `submitAddTask`, dentro de la rama `if (_editingTaskId)`, después del `await fetch('/api/tasks/' + ...)` y antes de cerrar el modal:

```javascript
      const notionUrl = (document.getElementById('task-notion-url').value || '').trim();
      if (notionUrl) {
        const rn = await fetch('/api/tasks/' + _editingTaskId + '/notion', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({url: notionUrl})
        });
        const dn = await rn.json();
        if (dn.ok) {
          const i = _allTasks.findIndex(t => t.id === _editingTaskId);
          if (i !== -1) _allTasks[i].notion_page_id = dn.notion_page_id;
        }
      }
```

- [ ] **Step 4: Badge y botón en la fila de la tarea**

En `_taskRowHtml`, agregar antes del `return`:

```javascript
  const notionBadge = t.notion_page_id
    ? `<a href="https://www.notion.so/${t.notion_page_id.replace(/-/g,'')}" target="_blank" rel="noopener"
          class="task-notion-badge" title="${esc(t.notion_status||'')}">Notion</a>`
    : '';
```

Sumar `${notionBadge}` dentro de `<div class="task-meta">`, después de `${assigneeBadge}${createdByBadge}`. Y en `.task-actions`, antes del botón de editar:

```javascript
      ${t.notion_page_id ? '' : `<button class="task-edit-btn" onclick="_enviarTareaANotion(${t.id})" title="Mandar a Notion">→ N</button>`}
```

El CSS del badge va junto a `.task-status-badge` (`dashboard.py:963` para el tema oscuro):

```css
.task-notion-badge{font-size:.72rem;color:#94a3b8;background:#1a2234;padding:2px 7px;border-radius:10px;text-decoration:none;border:1px solid #23304a}
.task-notion-badge:hover{color:#e2e8f0}
```

- [ ] **Step 5: La función que manda la tarea al tablero**

Junto a `_setTaskStatus` (`dashboard.py:3171`):

```javascript
async function _enviarTareaANotion(id) {
  const t = _allTasks.find(t => t.id === id);
  if (!t || t.notion_page_id) return;
  const r = await fetch('/api/tasks/' + id + '/notion', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'
  });
  const d = await r.json();
  if (!d.ok) { alert('Notion no aceptó la operación. Mirá los logs del CRM.'); return; }
  t.notion_page_id = d.notion_page_id;
  renderTasksList();
}
```

- [ ] **Step 6: Verificar a mano**

Run: `python main.py` (o como se levante local) y abrir el panel de tareas.
Expected: en una tarea sin vincular aparece el botón `→ N`; al tocarlo (con `NOTION_TOKEN` en `.env`) aparece el badge "Notion" y la tarjeta existe en el tablero. Sin `NOTION_TOKEN`, el botón devuelve el alert de error y no rompe la página.

- [ ] **Step 7: Correr la suite entera**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add dashboard.py
git commit -m "feat(notion): badge y boton de vinculacion en el panel de tareas"
```

---

## Verificación final (a mano, con el token real)

En este orden, y con **una sola** tarea de prueba antes de tocar tareas reales:

1. Crear una tarea en el CRM, tocar `→ N`, confirmar que la tarjeta aparece en *Backlog* con `CRM ID` cargado.
2. Moverla en Notion a *Up next*. Abrir el CRM (o `POST /api/notion/sync`): la tarea sigue en Pendiente, y en el modal el badge dice "Up next". Ese es el caso que prueba que el CRM no pisa el estado fino.
3. Moverla en Notion a *Done*. Sincronizar: la tarea queda Hecha en el CRM y aparece en actividad a nombre de `notion`.
4. Moverla en el CRM a Pendiente: la tarjeta vuelve a *Backlog*.
5. Abrir el historial de la tarjeta en Notion y contar las ediciones. Tienen que ser exactamente las de los pasos 1 y 4 — ninguna de más.
6. Borrar la tarea de prueba en el CRM y sincronizar: la tarjeta sigue ahí, intacta, y el sync no tira error.
7. Vincular una tarea a una tarjeta que ya existe pegando la URL, y confirmar que **no** se creó una tarjeta nueva.

## Notas de despliegue

`flyctl secrets set NOTION_TOKEN=<token> -a scalerics-crm` fuerza un redeploy solo.
Recordar que el deploy a Fly es una carrera sin lock: comparar la imagen del log
propio contra `flyctl status` antes de dar por buena la versión que quedó arriba.
