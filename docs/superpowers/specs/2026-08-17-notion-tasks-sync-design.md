# Sincronizar tareas del CRM con la database Tasks de Notion

**Fecha:** 2026-08-17 (ampliado el 18-8-2026)
**Estado:** implementado, mergeado a `main` y verificado contra la API real
el 18-8-2026: creación, pull, push y la regla de no reescribir un valor igual.

## Contexto

El Notion de Scalerics tiene una database **Tasks** con vista kanban "By status",
seis columnas y ~15 tarjetas de proyecto ("Realizar modulo pagos Plexo", "Agente
de facturación y cobranzas", "Mejorar CRM. Hacer uno nuevo?"). La usan varias
personas del equipo y la descripción real de cada tarea vive en los **comentarios**
de la página, no en la property `Descripcion`, que está vacía en las que se
revisaron.

| | |
|---|---|
| Database id (de la URL, confirmar por API) | `3ae65d94-deec-8093-8c48-cfbe77e202d5` |
| Estados | Sin Status, Backlog, Up next, On Hold, In progress, Done |
| Properties | `Name` (title), `Assignee` (people), `Descripcion` (texto), `Due date` (date), `Project` (relación), `Status` (status), `Tiempo Estimado` (número) |

El CRM tiene su propio kanban de tareas (tabla `tasks`, estados `todo` /
`in_progress` / `done`, con `assignee`, `deadline`, `client_id`, `goal` e
historial en `task_progress_events`).

Lo que se quiere: poder mover una tarea de estado desde cualquiera de los dos
lados y que el otro se entere. La restricción que manda sobre todo el diseño es
que **el tablero de Notion no puede empeorar** — es de un equipo, no del CRM.

## Dos bloqueos que había que resolver antes (los dos resueltos el 18-8-2026)

1. **El token.** La cuenta con la que se entra a Notion desde esta máquina es
   *invitada* al workspace ("Contacto Scalerics's…", badge Invitado; la bandeja
   dice "Contacto Scalerics te ha invitado a Tasks"). Las integraciones internas
   las crea un owner del workspace, así que hay que entrar con la cuenta de
   Contacto Scalerics, crear la integración, compartirle la database Tasks y
   guardar el token como secret en Fly (`NOTION_TOKEN`). No hay ningún token de
   Notion en la máquina hoy.
2. **La property `CRM ID`** (tipo número) hay que agregarla a la database. Es el
   único cambio que este diseño le pide a Notion. Sin ella el pareo depende de
   matchear por título, que es exactamente cómo aparecen los duplicados. Se puede
   ocultar en la vista kanban.

Antes de escribir lógica, un smoke test contra la API real con el token: fijar el
header `Notion-Version` explícitamente y resolver el `data_source_id` de la
database (`GET /v1/databases/{id}`), porque el endpoint de query se mueve entre
versiones. La lección del webhook de Meta aplica igual acá: la versión de la API
se verifica primero, no cuando algo dejó de andar en silencio.

## Alcance

- Estado bidireccional entre una tarea del CRM y su tarjeta.
- CRM → Notion inmediato (en hilo aparte). Notion → CRM cuando alguien abre el CRM.
- Vinculación manual desde el CRM: crear una tarjeta nueva en Notion, o vincular
  una tarjeta que ya existe pegando su URL.

### Ampliación del 18-8-2026: el tablero manda sobre el trabajo de proyecto

El diseño original era opt-in puro y las tareas nacían siempre en el CRM. Se
amplió para que el panel de tareas del CRM refleje también el tablero, sin dejar
de tener sus tareas operativas propias (las que tienen cliente, meta y
responsable, que en Notion no tienen equivalente).

- El pull **deja de filtrar**: pide todas las páginas del data source.
- Una tarjeta que el CRM nunca vio **se convierte en tarea**, con su título y su
  estado. El import inicial no es un script aparte: es la primera corrida.
- Una tarjeta que **desaparece del tablero** (borrada o archivada) marca su tarea
  como `done` y la despareja. No se borra: un borrado en Notion no debería
  llevarse puesto el historial de progreso del CRM.
- **El pareo vive solo del lado del CRM**, en `notion_page_id`. A las tarjetas
  nacidas en Notion no se les escribe **nada**, ni siquiera `CRM ID`. La
  restricción que manda es que quien trabaja solo en Notion no note ninguna
  diferencia: ni una columna que se llena sola, ni ediciones de la conexión
  pisando quién tocó cada tarjeta por última vez.
- `CRM ID` queda solo para las tarjetas que nacen en el CRM, donde sigue siendo
  la defensa contra crear duplicados si se pierde la respuesta de un POST.

### Fuera de alcance

`Assignee` (los usuarios de Notion son del workspace, los del CRM son otra tabla),
`Descripcion`, los comentarios, `Project`, `Tiempo Estimado`, el webhook de
Notion, y cualquier borrado o archivado **en** Notion.

## Arquitectura

- **`services/notion_service.py`** — el único módulo que habla HTTP con Notion.
  `push_status(db, task)`, `create_page(db, task)`, `link_page(db, task, url)`,
  `pull_and_apply(db)`, `_group_of(notion_status)`. Sin Flask adentro, para
  testear con `requests` mockeado como en `test_calendly_gcal.py`.
- **`routes/notion.py`** — `POST /api/tasks/<id>/notion` (crear o vincular, según
  reciba `url` o no) y `POST /api/notion/sync` para el botón manual.
- **`_maybe_sync_notion(db_path)` en `dashboard.py`** — calcado de
  `_maybe_sync_calendly` (`dashboard.py:5078`): hilo daemon para no demorar la
  carga, throttle por `NOTION_SYNC_EVERY` (default 600s), y `return` inmediato si
  no hay `NOTION_TOKEN`. La máquina de Fly se duerme sin tráfico, así que un cron
  interno no correría.
- **Columnas nuevas en `tasks`**, con `_add_column` como las de `businesses`:
  `notion_page_id TEXT`, `notion_status TEXT`, `notion_synced_at TIMESTAMP`.
  Hay que agregarlas también a `_TASK_COLUMNS` (`database.py:918`), que es una
  whitelist a mano: `update_task` levanta `ValueError` con cualquier columna que
  no esté ahí. `get_tasks` hace `SELECT *`, así que las columnas nuevas llegan
  solas al front y el badge de Notion no necesita tocar la query.

## El modelo: reconciliación, no eventos

En cada sync el CRM pide a Notion **todas** las páginas del data source — un
request, con paginación por cursor si algún día pasan de 100 — compara el
`Status` que ve contra el `notion_status` que guardó la última vez, y donde
difieren **gana Notion**. Las que no reconoce las adopta como tareas nuevas; las
pareadas que no volvieron en el listado las da por desaparecidas, pero solo si el
listado vino entero (ver la ampliación del 18-8).

El push CRM → Notion es inmediato y actualiza `notion_status` en el mismo
momento, así que ese valor guardado es siempre "lo último que los dos lados
acordaron". De ahí sale la propiedad que importa: **no existe el cambio
perdido**. Si un sync falla, si la máquina se duerme, si Notion está caído, el
siguiente sync ve exactamente el mismo desajuste y lo resuelve igual. No hay
timestamps que llevar ni cola de eventos que se pueda desincronizar.

## Mapeo de estados

| CRM | → Notion | Notion | → CRM |
|---|---|---|---|
| `todo` | Backlog | Sin Status, Backlog, Up next | `todo` |
| `in_progress` | In progress | In progress, On Hold, Waiting To Accept | `in_progress` |
| `done` | Done | Done | `done` |

La columna de la izquierda **solo se aplica si cambió el grupo**. Si el CRM pasa
a `todo` y la tarjeta ya está en *Up next*, no sale ningún `PATCH`: el grupo de
*Up next* ya es `todo`. Esa regla es lo único que impide que el CRM aplaste los
estados finos del tablero.

Confirmado por el smoke el 18-8-2026: *On Hold* cae en el grupo "In progress" de
Notion, como se había asumido. **"Waiting To Accept"** es un estado que el equipo
agregó ese mismo día; Notion lo agrupa bajo *Complete*, pero acá se mapea a
`in_progress` a propósito: una tarea esperando aceptación todavía ocupa a alguien,
y mandarla a `done` la haría desaparecer de los pendientes del CRM.

Un estado que el equipo agregue y que no esté en `GRUPOS` cae en `todo` por
default: no rompe nada, pero conviene revisarlo.

## Garantías de no-daño en Notion

| Riesgo | Regla |
|---|---|
| Tarjetas duplicadas | Solo se crea página si `notion_page_id` está vacío. Antes de crear, se busca por `CRM ID`. |
| Pisar Up next / On Hold / Backlog | Solo se escribe `Status` si cambió el grupo. |
| Ensuciar el historial y notificar al vacío | El `PATCH` sale solo si el valor nuevo difiere del actual. Cero escrituras idempotentes. |
| Tocar tarjetas ajenas | Solo se escriben páginas pareadas. A las nacidas en Notion no se les escribe nada, ni siquiera `CRM ID`: el pareo vive del lado del CRM. |
| Borrar o archivar | El CRM nunca borra ni archiva en Notion. Si se borra la tarea en el CRM, la tarjeta queda y se despareja. |
| Inundar el tablero | Nada sale automático. El push existe solo para tareas que se vincularon a mano. |

## Errores

Notion caído, token vencido, property renombrada o `CRM ID` inexistente: se
loguea y el sync termina. Nunca rompe el request del dashboard — el CRM tiene que
funcionar igual sin Notion, como funciona hoy sin `GMAIL_REFRESH_TOKEN`.

Cada cambio traído de Notion se anota en `activity_log` con usuario `notion`
(igual que `calendly` hoy), así aparece en el panel de actividad y se puede
auditar qué movió el sync y cuándo.

Rate limit de Notion ~3 req/s: el pull es 1 request, el push es 1 por tarea
vinculada. No hace falta throttling propio con estos volúmenes.

## Verificación

`tests/test_notion_sync.py`, con `requests` mockeado:

- `_group_of` para los seis estados de Notion.
- Mapeo CRM → Notion en los tres estados.
- **No-pisado**: tarea en `todo` + `notion_status` = *Up next* → ningún `PATCH`.
- Push que sí escribe: `todo` → `done` con `notion_status` = *Up next*.
- Pull que actualiza una tarea pareada y **no** toca páginas sin `CRM ID`.
- `NOTION_TOKEN` ausente → `_maybe_sync_notion` es no-op y no importa `requests`.
- `link_page` parseando el page id de una URL de Notion (con y sin guiones).
- Notion devuelve 500 → el sync no propaga la excepción.

Smoke manual, en este orden: crear la property, correr el smoke de versión y
`data_source_id`, vincular **una** tarea de prueba, moverla en Notion, abrir el
CRM y confirmar que llegó; moverla en el CRM y confirmar que llegó a Notion;
después mirar el historial de la tarjeta en Notion para verificar que no quedaron
ediciones de más.

## Riesgos

- **El pareo se corta si alguien borra la property `CRM ID`** o la renombra. El
  sync loguea y no hace nada; se recupera reponiendo la property.
- **Dos personas mueven la misma tarjeta en los dos lados entre syncs.** Gana
  Notion, por diseño. Con una sola persona vinculando tareas a mano el escenario
  es raro, pero está aceptado explícitamente, no ignorado.
- **El token es de un workspace que no controlás.** Si Contacto Scalerics revoca
  la integración, el sync se apaga en silencio (loguea). Vale mirar el log si el
  estado deja de viajar.
