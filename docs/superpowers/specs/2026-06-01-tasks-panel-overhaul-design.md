# Spec: Panel de Tareas — Mejora completa

**Fecha:** 2026-06-01  
**Estado:** Aprobado

---

## Contexto

El panel de Tareas del CRM tiene filtros de estado básicos (todas/pendientes/en progreso/hechas) pero carece de filtro por usuario, buscador, edición de tareas y un manejo funcional del estado "En progreso". El único control de estado es un checkbox todo/done y no hay forma de marcar una tarea como "En progreso" manualmente.

---

## Objetivo

Hacer el panel de tareas completamente funcional para un equipo que usa el CRM diariamente: filtrar por usuario, buscar, editar tareas, y manejar los 3 estados (todo/in_progress/done) de forma intuitiva.

---

## 1. Barra de filtros

### Fila 1 — Búsqueda y usuario
- **Buscador** (input): filtra en tiempo real por título y descripción de tarea (cliente-side, sin request al servidor)
- **Dropdown de usuario**: lista todos los usuarios del sistema (`GET /api/users`). Opciones: "Todos los usuarios" (default) + uno por cada usuario. Al seleccionar filtra `_allTasks` por `assignee_id`

### Fila 2 — Pills de estado y filtros rápidos
- **Pills de estado** con conteo dinámico: Todas (N) · Pendientes (N) · En progreso (N) · Hechas (N)
  - El conteo refleja las tareas después de aplicar el filtro de usuario activo (si filtrás por Juan, los conteos son de Juan)
  - El pill activo tiene highlight azul
- **Separador visual** (1px)
- **⚠ Alta prioridad (N)**: muestra solo tareas con `priority === 'high'`, independiente del filtro de estado
- **🕐 Vencidas (N)**: muestra tareas con `deadline < hoy` y `status !== 'done'`
- Los filtros rápidos se combinan con el filtro de usuario (AND)

### Texto resumen
Debajo de la barra: `"Mostrando X tareas de [Usuario] · [Filtro activo]"` — actualiza en cada cambio de filtro.

---

## 2. Cards de tarea — cambios

### Status badge clickeable
Cada card muestra un badge de estado que rota al hacer click:
- `todo` → `in_progress` → `done` → `todo`
- Estilos: `● Pendiente` (gris) · `⚡ En progreso` (azul) · `✓ Hecha` (verde)
- Al cambiar llama `PUT /api/tasks/:id` con `{status: newStatus}`
- El checkbox actual **se elimina** y reemplaza por el badge

### Indicadores visuales en la card
- Borde izquierdo azul (`border-left: 3px solid #0369a1`) cuando `status === 'in_progress'`
- Borde izquierdo rojo (`border-left: 3px solid #f87171`) cuando la tarea está vencida y no está hecha
- Título con `line-through` + color muted cuando `status === 'done'`

### Botón Editar
Botón `✏️ Editar` en las acciones de cada card. Abre el modal de edición con los datos actuales de la tarea.

---

## 3. Modal de edición (nuevo)

Reutiliza la estructura del modal de creación existente (`#add-task-modal`) pero en modo edición:
- Título (text input)
- Descripción (text input)
- Prioridad (select: Alta/Media/Baja)
- Vencimiento (date input)
- Asignado a (select de usuarios)
- Estado (select: Pendiente/En progreso/Hecha) — campo nuevo en el modal
- Cliente (búsqueda existente)
- Meta/goal (existente)

El modal puede ser el mismo con un flag `_editingTaskId`. Si `_editingTaskId` es null → crear; si tiene valor → editar.

Al guardar: `PUT /api/tasks/:id` con todos los campos modificados, luego actualiza `_allTasks` en memoria y re-renderiza.

---

## 4. Lógica de filtrado combinada

Los filtros se aplican en este orden:
1. Filtro de usuario (`assignee_id === selectedUserId || selectedUserId === ''`)
2. Filtro de estado (pill activo: `status === filter || filter === 'all'`)
3. Filtro rápido (`priority === 'high'` o `deadline < hoy && status !== 'done'`)
4. Búsqueda (título o descripción contiene el texto buscado, case-insensitive)

Los filtros rápidos (Alta prioridad, Vencidas) reemplazan el filtro de estado cuando están activos (no se acumulan con él).

---

## 5. Cambios por archivo

### `dashboard.py`

**HTML — `#tasks-panel`:**
- Reemplazar `<div class="tasks-filters">` con la nueva barra de dos filas (search + user select + pills + quick filters)
- Agregar elemento `<div id="tasks-summary">` debajo de la barra
- Quitar el checkbox de `_taskRowHtml`, agregar status badge y botón Editar
- Modificar `#add-task-modal` para soportar modo edición: agregar campo Estado, agregar input hidden `task-edit-id`

**CSS:**
- `.filter-bar`, `.filter-row-1`, `.filter-row-2`: layout de la barra
- `.search-input`: input de búsqueda
- `.user-select`: dropdown de usuario
- `.pill` actualizado: agregar `.pill-count` span, quitar `onclick` inline, manejar con JS
- `.status-badge`: estilos para `.todo`, `.in_progress`, `.done`
- Actualizar `.task-row`: agregar variantes `.in-progress` y `.overdue` para borde izquierdo
- Agregar `.tasks-summary`: texto de resumen debajo de filtros
- Agregar `.btn-edit`: botón editar en task-actions

**JS:**
- Variables nuevas: `_taskUserFilter = ''`, `_taskSearchQuery = ''`, `_taskQuickFilter = ''`, `_editingTaskId = null`
- `loadTasks()`: sin cambios en el fetch; actualizar para llamar `_updateFilterCounts()` después
- `filterTasks(status, btn)`: actualizar para limpiar `_taskQuickFilter` cuando se activa un pill de estado
- `filterTasksQuick(type)`: nueva función para "alta_prioridad" y "vencidas"
- `_getFilteredTasks()`: nueva función centralizada que aplica los 4 filtros en orden; usada por `renderTasksList()`
- `renderTasksList()`: delegar filtrado a `_getFilteredTasks()`, actualizar texto resumen
- `_updateFilterCounts()`: recalcula conteos de cada pill con `_allTasks` completo (sin filtro de usuario)
- `_taskRowHtml(t)`: reemplazar checkbox por status badge, agregar botón Editar
- `_setTaskStatus(id, newStatus)`: nueva función — cicla el estado y hace PUT
- `openAddTaskModal(clientId, clientName)`: limpiar `_editingTaskId = null`, resetear campo Estado
- `openEditTaskModal(taskId)`: nueva función — carga datos de la tarea en el modal, setea `_editingTaskId`
- `submitAddTask()`: chequear `_editingTaskId`; si existe, hacer PUT en lugar de POST, actualizar `_allTasks` en memoria
- `_onTaskUserFilterChange(val)`: nueva función — setea `_taskUserFilter`, re-renderiza
- `_onTaskSearch(q)`: nueva función — setea `_taskSearchQuery`, re-renderiza (debounce 200ms)

---

## 6. Criterios de aceptación

- [ ] Dropdown de usuario filtra tareas por `assignee_id`; "Todos" muestra todo
- [ ] Buscador filtra en tiempo real por título y descripción
- [ ] Pills muestran conteo correcto y se actualizan al agregar/editar/borrar tareas
- [ ] Pill "Alta prioridad" muestra solo tareas con `priority === 'high'`
- [ ] Pill "Vencidas" muestra tareas con deadline pasado y no completadas
- [ ] Badge de estado en cada card rota al hacer click: todo → in_progress → done → todo
- [ ] Tarea en in_progress tiene borde izquierdo azul
- [ ] Tarea vencida tiene borde izquierdo rojo
- [ ] Botón ✏️ Editar abre el modal con datos pre-cargados de la tarea
- [ ] Guardar en modo edición actualiza la tarea (PUT) sin recargar la página
- [ ] Texto resumen debajo de filtros refleja usuario y filtro activos
- [ ] Los filtros de usuario, estado, rápido y búsqueda se combinan correctamente
