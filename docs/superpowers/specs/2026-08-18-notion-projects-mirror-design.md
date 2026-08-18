# Espejo de la database Projects de Notion en el CRM

**Fecha:** 2026-08-18
**Estado:** diseño aprobado, sin implementar

## Contexto

El CRM ya espeja la database **Tasks** del Notion de Scalerics: el pull lee todas
las páginas, adopta las que no conocía y reconcilia el estado, sin escribirle nada
a las tarjetas nacidas allá. Ver
`docs/superpowers/specs/2026-08-17-notion-tasks-sync-design.md`.

El pedido es que el CRM refleje también **Projects**. Lo que hay del otro lado,
leído por API el 18-8-2026:

| | |
|---|---|
| Data source | `3ae65d94-deec-803d-8ea4-000b48070a4e` |
| Properties | `Name` (title), `Stage` (select), `Timeline` (date), `Lead` (people), `Tasks` (relation) |
| Filas | TSM (Stage: In Progress), Seguimiento Clientes, Administracion, Desarrollo, General |

**La base está casi vacía**: de cinco proyectos, uno tiene Stage y ninguno tiene
Timeline ni Lead. Solo Administracion y Desarrollo tienen tareas (dos cada uno).

Eso se le planteó a Juan — un panel de proyectos muestra hoy cinco nombres y poco
más, y el valor real de esa base es la relación con las tareas — y decidió el panel
igual. Queda anotado para que dentro de tres meses no parezca una decisión
irreflexiva: **el panel se construyó sabiendo que la base está vacía**, apostando a
que el equipo la llene.

## Alcance

- **Espejo de solo lectura.** El CRM nunca escribe en Projects: ni Stage, ni Lead,
  ni proyectos nuevos. Para editar, el panel linkea a la página de Notion.
- Panel nuevo "Proyectos" en la sección GESTIÓN del menú.
- Cada tarea del CRM sabe a qué proyecto pertenece, y el kanban lo muestra.

### Fuera de alcance

Crear o editar proyectos desde el CRM, mapear `Lead` contra los usuarios del CRM
(son personas del workspace de Notion, otra tabla), y las relaciones de Projects
que no sean `Tasks`.

## Datos

Tabla nueva:

```sql
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
```

Y una columna más en `tasks`, con `_add_column` y agregada a `_TASK_COLUMNS`:
`notion_project_page_id TEXT`.

Se guarda el page id y no el nombre: si el equipo renombra un proyecto, el pareo
sobrevive y el nombre se actualiza solo.

## Sync

`traer_proyectos(db_path) -> tuple[int, str | None]` en `services/notion_service.py`,
con la misma forma que `traer_y_aplicar`: devuelve `(cambiados, error)`.

- Consulta el data source de Projects (`NOTION_PROJECTS_DATA_SOURCE_ID`), con la
  misma paginación por cursor y la misma guarda de `has_more` sin `next_cursor`.
- Upsert por `notion_page_id`: alta si no existe, y si existe actualiza nombre,
  stage, timeline y lead.
- Un proyecto que ya no vuelve en el listado **se borra** del CRM, y las tareas que
  lo referenciaban quedan con `notion_project_page_id = NULL`. Es un espejo: no hay
  historial propio que perder. Igual que en tareas, el barrido corre **solo si el
  listado vino entero**.

`_maybe_sync_notion` en `dashboard.py` llama primero a `traer_proyectos` y después a
`traer_y_aplicar`, para que al adoptar una tarea su proyecto ya exista. Si el de
proyectos falla, el de tareas corre igual: uno no puede tumbar al otro.

El pull de tareas lee la relación `Project` de cada página y guarda el primer id en
`notion_project_page_id`. Si la relación está vacía, queda NULL.

## Panel

Sección "Proyectos" en GESTIÓN, arriba de Tareas. Un bloque por proyecto:

- Nombre, que linkea a la página de Notion (`https://www.notion.so/<page_id>`).
- Badge de Stage cuando lo tiene.
- Lead y Timeline cuando los tiene; si no, no se muestra el campo vacío.
- Sus tareas listadas con el estado de cada una, y el conteo por estado.

Sin controles de edición. Los proyectos sin tareas se muestran igual: que el panel
diga la verdad sobre lo que hay en el tablero es parte del punto.

En el kanban de tareas, la tarjeta muestra el nombre de su proyecto junto al badge
de Notion.

## Errores

Como el resto del módulo: se loguea con `logger.warning` y se devuelve el error en
la tupla. Sin `NOTION_TOKEN` o sin `NOTION_PROJECTS_DATA_SOURCE_ID`, la función es
no-op y el CRM funciona igual.

## Configuración

`NOTION_PROJECTS_DATA_SOURCE_ID = "3ae65d94-deec-803d-8ea4-000b48070a4e"` en el
bloque `[env]` de `fly.toml`, junto al de tareas. No es un secreto, es un id, y así
viaja versionado con el deploy.

La conexión `CRM Scalerics` ya tiene acceso a la database Projects (agregada el
18-8-2026).

## Verificación

Tests con `requests` mockeado, como el resto:

- Alta de un proyecto que el CRM no conocía.
- Renombre: cambia el nombre, no se duplica la fila.
- Desaparición: se borra y las tareas quedan sin proyecto.
- La consulta falla → no se borra nada y se devuelve el error.
- Sin token → no-op y ningún request.
- El pull de tareas resuelve la relación `Project` y guarda el page id.

Del front, los dos que ya existen: el de presencia del markup y el `node --check`
sobre el JS embebido.

Manual, contra el tablero real: correr el sync y confirmar que aparecen los cinco
proyectos con TSM en In Progress, que Administracion y Desarrollo muestran sus dos
tareas cada uno, y que el historial de las páginas de Notion no registra ninguna
edición de la conexión.
