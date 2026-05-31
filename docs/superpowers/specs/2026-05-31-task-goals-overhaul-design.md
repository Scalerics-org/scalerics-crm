# Spec: Metas automáticas de tareas — mejora completa

**Fecha:** 2026-05-31
**Estado:** Aprobado

---

## Contexto

El sistema de metas automáticas en tareas solo tiene 2 `goal_type` (`leads_contactados`, `reuniones_agendadas`), no registra quién contribuyó a cada incremento, tiene un bug donde el batch update no dispara el progreso, y no cubre los eventos más importantes del funnel de ventas.

---

## Objetivo

Cubrir el funnel completo, compartir crédito entre todos los que tocaron un lead, y mostrar el historial de qué disparó cada incremento.

---

## 1. Nuevos goal_types

| goal_type | Label UI | Se dispara cuando... |
|---|---|---|
| `leads_contactados` | Leads contactados | CRM → `"contactado"` (individual y batch) ✅ existe, fix batch |
| `llamadas_realizadas` | Llamadas realizadas | Se loguea cualquier llamada (cualquier outcome) |
| `llamadas_contestadas` | Llamadas contestadas | Se loguea llamada con outcome `"contestó"` |
| `reuniones_agendadas` | Reuniones agendadas | Se crea una reunión en calendario ✅ existe |
| `reuniones_hechas` | Reuniones hechas | CRM → `"reunion_hecha"` (individual y batch) |
| `presupuestos_enviados` | Presupuestos enviados | Se marca un presupuesto como enviado |
| `clientes_cerrados` | Clientes cerrados | CRM → `"cliente_cerrado"` (individual y batch) |

---

## 2. Crédito compartido

Cuando se dispara cualquier goal_type para un lead, el incremento se aplica a **todos los usuarios que contribuyeron a ese lead**, no solo al usuario actual.

### Regla
Antes de incrementar, consultar `activity_log` para ese `lead_id` y recolectar todos los `user_id` únicos donde `user_id IS NOT NULL`. El usuario actual siempre está incluido aunque no tenga entrada previa en activity_log.

### Ejemplo
- Juan (SDR) contactó el lead → activity_log: user_id=1
- María agendó reunión → activity_log: user_id=2
- Pedro cierra el trato → user_id actual=3, contributors=[1,2,3]
- Los tres suman +1 en sus tareas de `clientes_cerrados`

### Implicancia para `leads_contactados`
Si el lead no tiene activity_log previo, solo suma el usuario actual. Si alguien llamó antes (y quedó en activity_log), también recibe crédito.

---

## 3. Historial por tarea

### Nueva tabla `task_progress_events`

```sql
CREATE TABLE IF NOT EXISTS task_progress_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id      INTEGER,
    lead_id      INTEGER REFERENCES businesses(id) ON DELETE SET NULL,
    lead_name    TEXT,
    event_type   TEXT NOT NULL,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

Cada vez que `increment_task_progress` suma +1 a una tarea, inserta una fila con el contexto del evento.

### UI — historial desplegable en la card de tarea

La barra de progreso existente pasa a ser clickeable. Al hacer click muestra una lista expandida debajo:

```
▸ 7/20 llamadas  [click para expandir]

  +1 · Empresa ABC           hoy 10:30
  +1 · La Huertita           ayer
  +1 · Taller Méndez         hace 2 días
  ...
```

Máximo 50 entradas mostradas. Nuevo endpoint `GET /api/tasks/:id/progress-history`.

---

## 4. Bug fix: batch update

`api_batch_status()` en `routes/leads.py` actualmente no llama `increment_task_progress`. Se agrega el loop necesario para los estados que tienen un `goal_type` mapeado:

```python
_STATUS_TO_GOAL = {
    "contactado":     "leads_contactados",
    "reunion_hecha":  "reuniones_hechas",
    "cliente_cerrado":"clientes_cerrados",
}
```

---

## 5. Cambios por archivo

### `database.py`
- Agregar `task_progress_events` en `init_db`
- Modificar `increment_task_progress(db_path, user_ids: list[int], goal_type, lead_id, lead_name)` — acepta lista de user_ids, inserta en `task_progress_events` por cada tarea completada/actualizada
- Agregar `get_lead_contributor_ids(db_path, lead_id) -> list[int]` — retorna user_ids únicos desde activity_log para un lead
- Agregar `get_task_progress_history(db_path, task_id, limit=50) -> list[dict]`

### `routes/leads.py`
- `api_set_crm_status()` — agregar calls para `reuniones_hechas` y `clientes_cerrados`, pasar contributors
- `api_batch_status()` — agregar loop de `increment_task_progress` para los 3 estados mapeados
- `api_log_call()` — agregar `llamadas_realizadas` siempre, `llamadas_contestadas` si outcome == `"contestó"`
- `api_mark_contacted()` — ya tiene `leads_contactados`, agregar contributors

### `routes/budgets.py`
- `api_mark_budget_sent()` — agregar `increment_task_progress` para `presupuestos_enviados` con contributors del lead

### `routes/calendar.py`
- `api_create_meeting()` — ya tiene `reuniones_agendadas`, agregar contributors

### `dashboard.py`
- Modal de nueva tarea: ampliar `<select>` con los 7 goal_types
- JS `goalTypeLabel`: agregar las 5 entradas nuevas
- Barra de progreso: hacer clickeable, agregar panel desplegable con historial
- Nuevo fetch a `GET /api/tasks/:id/progress-history` al expandir

---

## 6. Criterios de aceptación

- [ ] Los 7 goal_types aparecen en el modal de tarea
- [ ] Batch update de "contactado" incrementa progreso de `leads_contactados`
- [ ] Cuando se cierra un cliente, todos los usuarios en activity_log de ese lead reciben crédito en `clientes_cerrados`
- [ ] Cualquier llamada logueada suma en `llamadas_realizadas`; solo "contestó" suma en `llamadas_contestadas`
- [ ] Al expandir la barra de progreso se ve la lista de qué leads dispararon cada incremento
- [ ] `GET /api/tasks/:id/progress-history` retorna 403 si el usuario no es el asignado ni admin
