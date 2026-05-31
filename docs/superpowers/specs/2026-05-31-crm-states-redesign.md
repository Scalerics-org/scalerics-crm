# Spec: Reestructuración de estados CRM

**Fecha:** 2026-05-31
**Estado:** Aprobado

---

## Contexto

El estado `contactado` es ambiguo y no refleja el resultado real de una llamada. Además, "Interesado" y "Llamar después" en el modal de llamadas producen el mismo estado (`llamar_despues`), y "No le interesa" solo cambia el estado si el lead viene de Seguimientos. El nombre "Pipeline" no es claro para el equipo.

---

## Cambios de estados

### Estado eliminado
- `contactado` → reemplazado por `interesado`. Migrado en DB al reiniciar.

### Estado nuevo
- `interesado` — el lead respondió y está interesado. Aparece en Seguimientos.

### Estado redefinido
- `no_interesa` — el lead no está interesado. Se establece desde **cualquier panel** al seleccionar "No le interesa" en el modal. El lead **permanece visible en Cola** bajo el filtro "No interesa" (no desaparece del sistema).

### Estados sin cambios
`sin_contactar`, `llamar_despues`, `reunion_agendada`, `reunion_hecha`, `presupuesto_enviado`, `negociacion`, `cliente_cerrado`, `en_desarrollo`, `finalizado`

---

## Paneles

### Cola
- Filtro por defecto: `sin_contactar`
- Filtro alternativo: `no_interesa`
- UI: dos botones de filtro arriba de la tabla — **[ Sin contactar ]** y **[ No interesa ]** — el activo resaltado en azul
- La tabla, acciones y comportamiento son idénticos para ambos filtros

### Seguimientos
- Carga `interesado` + `llamar_despues` (antes: `llamar_despues` + `contactado`)
- Sin cambios de UI

### Proceso de venta (antes: Pipeline)
- Solo renombre en nav y header — `id="pipeline-panel"` no cambia
- Carga los mismos estados: `reunion_agendada`, `reunion_hecha`, `presupuesto_enviado`, `negociacion`

---

## Call modal — outcomes y estados resultantes

| Botón | Call log outcome | crm_status resultante | Desde cualquier panel |
|---|---|---|---|
| No contestó | `no_contestó` | sin cambio | ✅ |
| No le interesa | `no_interesa` | `no_interesa` | ✅ |
| Llamar después + fecha | `llamar_despues` | `llamar_despues` | ✅ |
| Interesado + fecha | `interesado` | `interesado` | ✅ |
| Agendó reunión | `reunion` | `reunion_agendada` | ✅ |

### Implementación JS — `_callbackOutcome`
Agregar variable `let _callbackOutcome = 'llamar_despues'` (default).
- Botón "Llamar después" → `_callbackOutcome = 'llamar_despues'` → llama `toggleCallbackRow()`
- Botón "Interesado" → `_callbackOutcome = 'interesado'` → llama `toggleCallbackRow()`
- `confirmCallback()` envía `outcome: _callbackOutcome` al endpoint `/api/leads/:id/callback`

### Endpoint `/api/leads/:id/callback`
Acepta param `outcome` (default `llamar_despues` para backward compat).
- Setea `crm_status` = `outcome` (puede ser `llamar_despues` o `interesado`)
- Loguea call con ese outcome
- Agrega `interesado` a `_VALID_OUTCOMES`

### Botón "Contactar" en Cola
- Sigue existiendo
- Ahora setea `crm_status = 'interesado'` en vez de `contactado`
- `leads_contactados` goal type se dispara igual (el trigger cambia de `contactado` → `interesado`)

---

## Migración de datos

En `database.py`, `init_db()`, agregar antes de crear índices:

```sql
UPDATE businesses SET crm_status = 'interesado' WHERE crm_status = 'contactado'
```

Idempotente: si se corre dos veces no hace nada la segunda vez.

---

## Cambios en `routes/leads.py`

### `_VALID_CRM_STATES`
Agregar `interesado`. Mantener `contactado` como alias legacy (para que PUT existentes no fallen).

### `_VALID_OUTCOMES`
Agregar `interesado`.

### `_STATUS_TO_GOAL` (en `api_crm_status` y `api_batch_status`)
```python
_STATUS_TO_GOAL = {
    "interesado":      "leads_contactados",  # era "contactado"
    "reunion_hecha":   "reuniones_hechas",
    "cliente_cerrado": "clientes_cerrados",
}
```
Remover `"contactado"` del mapa.

### `api_contact` (botón "Contactar" de Cola)
Cambiar `crm_status="contactado"` → `crm_status="interesado"`.

### `api_callback`
Leer `outcome` del body JSON (default `"llamar_despues"`).
Setear `crm_status = outcome` (puede ser `llamar_despues` o `interesado`).
Loguear call con ese outcome.

---

## Cambios en `dashboard.py`

### Nav sidebar
`Pipeline` → `Proceso de venta` (solo texto, el `id="nav-pipeline"` y `showPanel('pipeline')` no cambian)

### Header del panel
`<h1>Pipeline</h1>` → `<h1>Proceso de venta</h1>`

### `crmLabels` (todos los objetos en el JS)
Agregar: `interesado: 'Interesado'`
Actualizar: `contactado: 'Contactado'` → puede quedar como fallback o removerse

### CSS
Agregar: `.row-interesado{border-left:3px solid #10b981;background:rgba(16,185,129,.05)}`

### Cola — filtros
Agregar dos botones de filtro sobre la tabla:
```html
<button id="cola-filter-sin" onclick="setColaFilter('sin_contactar')" class="cola-filter-btn active">Sin contactar</button>
<button id="cola-filter-no" onclick="setColaFilter('no_interesa')" class="cola-filter-btn">No interesa</button>
```
Variable JS `_colaFilter = 'sin_contactar'` (default).
`setColaFilter(f)` → actualiza `_colaFilter`, resalta botón activo, recarga Cola.
`loadCola()` usa `_colaFilter` para la query: `/api/leads?crm_status=${_colaFilter}`.

### `loadSeguimientos()`
Cambiar fetch de `crm_status=contactado` → `crm_status=interesado`.
Cambiar el texto del pill "Contactado" → "Interesado" donde aparezca.

### Stats bar
`stat-seguimientos` fetcha `interesado` + `llamar_despues` (antes: `contactado` + `llamar_despues`).

### `logCallOutcome` JS
Remover la rama `if (outcome === 'contestó') { crm_status = 'contactado' }` (ya no tiene efecto, pero limpiar).
Agregar: `if (outcome === 'no_interesa') { crm_status = 'no_interesa' }` para que funcione desde Cola también.

### `_callbackOutcome` variable y `confirmCallback()`
Agregar `let _callbackOutcome = 'llamar_despues'`.
Botones "Llamar después" e "Interesado" setean esta variable antes de llamar `toggleCallbackRow()`.
`confirmCallback()` envía `{callback_date, notes, outcome: _callbackOutcome}`.

### `stateLabels` en métricas
Agregar `interesado: 'Interesado'`.

### `funnel_order` en `api_metrics` (routes/leads.py)
Reemplazar `"contactado"` → `"interesado"` en el array.
Agregar `_legacy` alias: `"contactado" → "interesado"` en la función `_norm`.

---

## Criterios de aceptación

- [ ] Al reiniciar, todos los leads con `crm_status='contactado'` pasan a `interesado`
- [ ] El botón "Contactar" en Cola setea el lead como `interesado` (va a Seguimientos)
- [ ] "Interesado" + fecha en el modal setea `interesado` (va a Seguimientos como "Interesado")
- [ ] "Llamar después" + fecha en el modal setea `llamar_despues` (va a Seguimientos)
- [ ] "No le interesa" en el modal setea `no_interesa` desde cualquier panel
- [ ] Cola muestra `sin_contactar` por default y `no_interesa` al filtrar
- [ ] Seguimientos carga leads `interesado` + `llamar_despues`
- [ ] "Pipeline" en el nav y header dice "Proceso de venta"
- [ ] `leads_contactados` goal type dispara cuando status → `interesado`
- [ ] El funnel en métricas muestra "Interesado" en vez de "Contactado"
