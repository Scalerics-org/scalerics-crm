# Budget Tab — AI Edit Design

**Date:** 2026-05-31  
**Scope:** Simplificar la pestaña Presupuesto y agregar edición con IA sobre el archivo HTML subido.

---

## Problema actual

La pestaña Presupuesto tiene dos sistemas paralelos confusos:
1. Sistema AI estructurado (tabla `budgets`, endpoint `/budget/preview`) — genera un presupuesto como JSON con secciones
2. Sistema de adjuntos (tabla `lead_attachments`, sección `budget`) — sube archivos HTML manuales

El resultado es una UI con tres secciones ("Requerimientos adicionales", "Presupuesto", "Archivos y links") que no comunican entre sí y confunden al usuario.

---

## Diseño

### UI — Pestaña Presupuesto (estado-based)

**Estado A — Sin presupuesto (sin adjuntos HTML en sección budget):**
```
┌─────────────────────────────────────────┐
│ Presupuesto                             │
│                                         │
│ No hay presupuesto para este cliente.   │
│ [⚡ Generar con IA]                     │
└─────────────────────────────────────────┘
```

**Estado B — Con presupuesto (existe al menos un adjunto HTML en sección budget):**
```
┌─────────────────────────────────────────┐
│ Presupuesto                             │
│                                         │
│ 📄 plan-arq.html    [Ver] [Editar con IA] [🗑]  │
└─────────────────────────────────────────┘
```

- Se elimina la sección "Requerimientos adicionales" y el textarea.
- Se elimina la sección "Sin presupuesto generado aún" del sistema antiguo.
- El área de upload manual (arrastrar archivo / pegar link) se mantiene debajo en ambos estados, por si el usuario quiere subir un HTML propio.

---

### Flujo "Generar con IA" (Estado A)

1. Usuario hace click en "Generar con IA"
2. Modal con: textarea de instrucciones (opcional, ej: "sitio web para arquitecta, precio $370 USD")
3. Backend genera el HTML completo con Haiku usando el nombre/rubro/ciudad del lead como contexto base
4. El HTML generado se sube automáticamente como adjunto `section=budget`
5. La UI pasa al Estado B

---

### Flujo "Editar con IA" (Estado B)

1. Usuario hace click en "Editar con IA" sobre el adjunto HTML
2. Modal con:
   - Textarea de instrucciones (ej: "cambia el precio a $500 USD", "agrega mantenimiento mensual de $30")
   - Botón "Generar preview"
   - Spinner + bloqueo del textarea durante la generación
   - Iframe con el HTML modificado (oculto hasta que Haiku responde)
   - Botón "Guardar" (habilitado solo después del preview) + "Cancelar"
3. Al confirmar, el `file_data` del adjunto se sobreescribe con el nuevo HTML

---

### Backend — Nuevos endpoints

#### `POST /api/attachments/<id>/ai-edit`
- Auth: `x-admin-token` o sesión activa
- Body: `{ "instructions": "..." }`
- Lee `file_data` del adjunto (debe ser `mime_type=text/html`)
- Llama a Claude Haiku con el HTML + instrucciones
- Devuelve: `{ "ok": true, "html": "<html completo modificado>" }`
- **No guarda** — solo devuelve el preview

#### `POST /api/attachments/<id>/ai-apply`
- Auth: `x-admin-token` o sesión activa  
- Body: `{ "html": "<html completo>" }`
- Sobreescribe `file_data` en `lead_attachments` para ese `id`
- Devuelve: `{ "ok": true }`

#### `POST /api/leads/<biz_id>/budget/generate`
- Auth: sesión activa
- Body: `{ "instructions": "..." }` (opcional)
- Lee `name`, `category`, `city` del lead como contexto
- Llama a Haiku para generar un presupuesto HTML completo desde cero usando la plantilla base de `budgets/`
- Sube el resultado como adjunto `section=budget` via `add_attachment()`
- Devuelve: `{ "ok": true, "attachment_id": <id> }`

---

### Claude Haiku — Prompts

**Edición:**
```
Sistema: Sos un asistente que edita documentos HTML de presupuestos.
Tu tarea es aplicar los cambios solicitados al HTML preservando exactamente
la estructura, estilos CSS y formato del documento original.
Devolvé ÚNICAMENTE el HTML completo modificado, sin explicaciones ni markdown.

Usuario: <html original>

Instrucciones: <instrucciones del usuario>
```

**Generación:**
```
Sistema: Sos un asistente que genera presupuestos HTML profesionales para
una agencia de desarrollo web en Uruguay llamada Scalerics.
Devolvé ÚNICAMENTE el HTML completo listo para imprimir, sin explicaciones ni markdown.
Usá la paleta de colores Scalerics (navy #0f1f3d, accent #0088CC, green #0d9e6e).

Usuario: Generá un presupuesto para:
- Negocio: <name>
- Rubro: <category>  
- Ciudad: <city>
- Instrucciones adicionales: <instructions>
```

Modelo: `claude-haiku-4-5-20251001`

---

### Qué se elimina / oculta de la UI

- Sección "Requerimientos adicionales" (textarea + botón "Generar presupuesto con IA" del sistema viejo)
- Sección "Presupuesto" con el resumen JSON (cards de precio, botón "Ver/imprimir PDF", "Marcar enviado", "Regenerar")
- La función `_cpRenderBudget()` se reescribe completamente

El backend del sistema viejo (`/api/leads/<id>/budget`, tabla `budgets`) **no se toca** — solo se oculta de la UI, por si se reactiva en el futuro.

---

### Archivos modificados

| Archivo | Cambio |
|---------|--------|
| `dashboard.py` | Reescribir `_cpRenderBudget()` + nuevo modal "Editar con IA" + nuevo modal "Generar" |
| `routes/leads.py` | Agregar `POST /api/leads/<id>/budget/generate` |
| `routes/leads.py` | Agregar `POST /api/attachments/<id>/ai-edit` y `ai-apply` |

---

### Casos borde

- Si el adjunto no es HTML (`mime_type != text/html`), el botón "Editar con IA" no aparece
- Si Haiku devuelve un error, mostrar mensaje en el modal sin cerrar
- Si el HTML generado supera 10 MB (imposible en práctica), el `add_attachment` lo rechaza por límite existente
