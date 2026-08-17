# Puesta en producción — sync de tareas con Notion

Este runbook cubre la preparación manual en Notion y lo que confirma el smoke
test antes de escribir cualquier request real de sync. Las Tasks 3 y 5 leen la
sección de hallazgos para decidir la forma de esos requests.

---

## Pasos manuales previos

Los hace una persona, no el código.

1. Entrar a Notion con la cuenta **Contacto Scalerics** (owner del workspace).
   La cuenta de Juan es invitada y no puede crear integraciones.
2. Crear una integración interna en `notion.so/my-integrations`, con
   capacidades de lectura y actualización de contenido. Copiar el token.
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

1. **`Notion-Version` que funcionó**: PENDIENTE.
2. **`data_source_id` de la database** (o la nota de que la versión que
   respondió 200 no usa `data_sources`, según lo que imprima el campo
   `data_sources` del script): PENDIENTE.
3. **Forma del `parent` que acepta `POST /v1/pages`**: PENDIENTE. Depende del
   dato anterior:
   - Con `data_sources`: `POST /v1/data_sources/{data_source_id}/query` y
     parent de creación `{"type": "data_source_id", "data_source_id": "..."}`.
   - Sin ellos: `POST /v1/databases/{id}/query` y parent
     `{"database_id": "..."}`.
4. **Tabla estado → grupo de la property `Status`**, tal como la imprime el
   script (no inventar valores — puede no coincidir con el ejemplo de formato
   de la sección "Smoke test" de arriba): PENDIENTE.
