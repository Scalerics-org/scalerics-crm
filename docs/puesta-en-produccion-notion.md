# Puesta en producción — sync de tareas con Notion

Este runbook cubre la preparación manual en Notion y lo que confirma el smoke
test antes de escribir cualquier request real de sync. Las Tasks 3 y 5 leen la
sección de hallazgos para decidir la forma de esos requests.

---

## Pasos manuales previos

Los hace una persona, no el código.

1. Entrar a Notion con la cuenta **Contacto Scalerics** (owner del workspace).
   La cuenta de Juan es invitada y no puede crear integraciones.
2. Crear una integración interna en `notion.so/my-integrations`, con las **tres**
   capacidades de contenido: *Leer*, *Actualizar* e ***Insertar***. Las tres se
   usan: leer para el pull, actualizar para el estado y el `CRM ID`, e insertar
   para crear la tarjeta con el botón. Sin la de insertar, `crear_pagina`
   devuelve 403 y el resto anda igual, que es la falla más confusa de las tres.
   No hace falta ninguna capacidad de información de usuario: el sync no lee
   personas. Copiar el token (empieza con `ntn_`).
3. En la database **Tasks**, menú `···` → *Conexiones* → agregar la
   integración.
4. Agregar a la database una property **`CRM ID`** de tipo *Número*. Ocultarla
   en la vista "By status".
5. Guardar el token local en `.env` como `NOTION_TOKEN`, y en producción con
   `flyctl secrets set NOTION_TOKEN=<token> -a scalerics-crm`.

## Smoke test

`scripts/notion_smoke.py` prueba las dos versiones de API (`2025-09-03` y
`2022-06-28`, en ese orden) contra `GET /v1/databases/{id}` y se queda con la
primera que responde HTTP 200. No escribe nada.

```bash
NOTION_TOKEN=<token> python scripts/notion_smoke.py
```

Condiciones de corte:

- Si imprime `CRM ID presente: False`, volver al paso manual 4.
- Si las dos versiones dan 404, la integración no tiene la database conectada
  (paso manual 3).

Ejemplo de la **forma** en que el script imprime la tabla estado → grupo (no
es un resultado real, es solo para ilustrar el formato de salida — el
resultado real va en la sección de hallazgos):

```
  grupo To-do: ['Backlog', 'Up next']
  grupo In progress: ['In progress', 'On Hold']
  grupo Complete: ['Done']
```

---

## Hallazgos del smoke (PENDIENTE)

Todavía no se corrió el script contra la API real — el token solo lo puede
generar el owner del workspace (paso manual 1-2), y esa parte no la puede
hacer esta sesión. Falta completar, después de correrlo, estos cuatro datos:

Cada hallazgo dice **dónde va la respuesta**. Los tres primeros son
configuración; el cuarto es código.

1. **`Notion-Version` que funcionó**: PENDIENTE.

   Va en la variable de entorno **`NOTION_VERSION`**. El default que trae el
   código es `2025-09-03`.

   ```bash
   flyctl secrets set NOTION_VERSION=<la que respondió 200> -a scalerics-crm
   ```

2. **`data_source_id` de la database** (o la nota de que la versión que
   respondió 200 no usa `data_sources`, según lo que imprima el campo
   `data_sources` del script): PENDIENTE.

   Va en la variable de entorno **`NOTION_DATA_SOURCE_ID`**. Si la database
   **no** expone `data_sources`, se deja **sin definir** (no vacía con un valor
   raro: sin definir).

   ```bash
   flyctl secrets set NOTION_DATA_SOURCE_ID=<el id> -a scalerics-crm
   # o, si la database no usa data sources, no se define nada
   ```

3. **Forma del `parent` que acepta `POST /v1/pages`**: **se responde solo al
   contestar el hallazgo 2.** No hay nada que anotar ni configurar acá.

   `NOTION_DATA_SOURCE_ID` decide las dos cosas a la vez, en
   `services/notion_service.py`: `_parent` (la forma del parent al crear) y
   `_url_de_query` (el endpoint del pull). Con la variable definida:
   `POST /v1/data_sources/{id}/query` y parent
   `{"type": "data_source_id", "data_source_id": "..."}`. Sin definir:
   `POST /v1/databases/{id}/query` y parent `{"database_id": "..."}`.

4. **Tabla estado → grupo de la property `Status`**, tal como la imprime el
   script (no inventar valores — puede no coincidir con el ejemplo de formato
   de la sección "Smoke test" de arriba): PENDIENTE.

   Este es el **único de los cuatro que no es configuración**: se responde
   **editando código**, en el dict `GRUPOS` de `services/notion_service.py`.
   No hay variable de entorno para esto. Lo que está ahí hoy sale de mirar el
   tablero a ojo; si el smoke imprime otra cosa, hay que corregir el dict,
   commitear y deployar. Mientras siga mal, el CRM puede mover tarjetas del
   equipo creyendo que el grupo cambió cuando no cambió.

---

## Cómo saber que funcionó

Con el token puesto y el CRM arriba, desde una sesión con login (o con el
header `x-admin-token`):

```bash
curl -X POST https://scalerics-crm.fly.dev/api/notion/sync \
  -H "x-admin-token: $ADMIN_TOKEN"
```

Qué esperar:

- **Funcionó**: HTTP 200 y `{"ok": true, "cambiadas": N}`. `N` es cuántas
  tareas del CRM cambiaron de estado por lo que decía Notion; `0` con
  `"ok": true` significa que la consulta anduvo y no había nada desalineado.
  Queda además una línea en el log de actividad del CRM a nombre del usuario.
- **No funcionó**: HTTP 502 y `{"ok": false, "error": "...", "cambiadas": N}`.
  `cambiadas` es el contador real, no siempre `0`: si la consulta falló recién en
  una página posterior del cursor, lo que se aplicó antes del fallo ya está
  guardado y cuenta. El `error` dice qué pasó:
  - `falta NOTION_TOKEN: el sync con Notion esta apagado` → el secret no está
    puesto (paso manual 5).
  - `la consulta a Notion devolvio HTTP 404` → la integración no tiene la
    database conectada (paso manual 3), o el `NOTION_DATABASE_ID` está mal.
  - `la consulta a Notion devolvio HTTP 400` → lo más probable es el par
    versión/endpoint: la combinación por defecto (`2025-09-03` sin
    `NOTION_DATA_SOURCE_ID`) le pega a `POST /v1/databases/{id}/query`, que el
    split de data sources reemplazó. Son los hallazgos 1 y 2.

Un `POST /api/notion/sync` no escribe nada en Notion: solo lee el tablero y
aplica en el CRM. Es seguro repetirlo.

## Lo que hoy no se puede hacer desde la UI

**Desvincular una tarea de su tarjeta no existe en la interfaz.** Tampoco por
API: las rutas de tareas descartan cualquier campo `notion_*` que venga del
cliente, justamente para que el CRM no pueda apuntar una tarea a una página
arbitraria. Si alguien vincula la tarea equivocada, hay dos salidas:

```bash
# Directo a la base, en la máquina de Fly (DB_PATH=/data/leads.db).
flyctl ssh console -a scalerics-crm
sqlite3 /data/leads.db \
  "UPDATE tasks SET notion_page_id=NULL, notion_status=NULL, notion_synced_at=NULL WHERE id=<task_id>;"
```

O pegar la URL de la tarjeta **correcta** en el modal de edición de la tarea:
re-vincular está soportado y le saca el `CRM ID` a la tarjeta vieja antes de
reclamar la nueva, así que no quedan dos tarjetas peleando por la misma tarea.

Ojo: limpiar la columna por sqlite deja el `CRM ID` puesto en la tarjeta de
Notion. Si esa tarjeta queda ahí con el `CRM ID` de una tarea que ya no la
apunta, el pull la va a seguir matcheando contra esa tarea. Hay que borrar el
`CRM ID` a mano en Notion también.
