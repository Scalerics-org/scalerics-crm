# Tasks Panel Overhaul — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar filtro por usuario, buscador, pills con conteo, filtros rápidos (Alta prioridad/Vencidas), status badge clickeable 3-estados, edición de tareas, y hora en el vencimiento al panel de Tareas del CRM.

**Architecture:** Todos los cambios están en `dashboard.py` (Flask + Jinja2 + vanilla JS en un solo archivo). El filtrado es client-side: se carga todo con `loadTasks()` y se filtra en `_getFilteredTasks()`. El modal de creación se reutiliza para edición con un flag `_editingTaskId`.

**Tech Stack:** Python/Flask, SQLite, vanilla JS (ES2017+), CSS inline en el mismo archivo.

**Constraint crítico:** No modificar `.filter-btn` (clase global usada en otros paneles). Los nuevos pills de tareas usan `.pill` (clase nueva).

---

## Archivos modificados

- `dashboard.py` — único archivo. Cambios en 4 zonas:
  1. CSS (líneas ~848–871): agregar estilos nuevos
  2. HTML panel de tareas (líneas ~1058–1071): nueva barra de filtros
  3. HTML modal `#add-task-modal` (líneas ~1194–1255): agregar Estado + IDs
  4. JS sección tasks (líneas ~2606–2871): nueva lógica de filtrado y edición

---

## Task 0: CSS + JS — Custom User Picker

Dropdown personalizado con avatar (inicial coloreada + nombre) usado en dos lugares:
- Filtro de usuario en la barra de filtros (`upick-filter`)
- Campo "Asignar a" en el modal de tarea (`upick-modal`)

Reemplaza los `<select>` nativos que se veían inconsistentes. Los hidden inputs `task-assignee-id` y `task-assignee-email` se mantienen para no cambiar `submitAddTask()`.

**Files:**
- Modify: `dashboard.py` (CSS ~línea 870, JS al final de la sección tasks)

- [ ] **Step 1: Agregar CSS del custom picker**

Encontrar:
```css
/* Add-task modal */
#add-task-modal .modal{width:440px}
```

Reemplazar con:
```css
/* Add-task modal */
#add-task-modal .modal{width:440px}
/* Custom user picker */
.upick-wrap{position:relative}
.upick-trigger{display:flex;align-items:center;gap:8px;background:#0a0f1a;border:1px solid #334155;border-radius:8px;padding:8px 12px;cursor:pointer;transition:border-color .15s;user-select:none}
.upick-trigger:hover{border-color:#0088cc55}
.upick-trigger.open{border-color:#0088cc}
.upick-label{flex:1;font-size:.82rem;color:#e2e8f0}
.upick-chevron{color:#475569;font-size:.7rem;transition:transform .15s}
.upick-trigger.open .upick-chevron{transform:rotate(180deg)}
.upick-dropdown{position:absolute;top:calc(100% + 6px);left:0;right:0;background:#111827;border:1px solid #334155;border-radius:10px;overflow:hidden;z-index:200;box-shadow:0 8px 24px rgba(0,0,0,.4)}
.upick-option{display:flex;align-items:center;gap:10px;padding:9px 12px;cursor:pointer;transition:background .12s}
.upick-option:hover{background:#1a2234}
.upick-option.upick-sel{background:#0c1a2e}
.upick-av{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:.65rem;font-weight:800;flex-shrink:0;color:#fff}
.upick-name{font-size:.82rem;color:#e2e8f0;font-weight:500;flex:1}
.upick-check{color:#0088cc;font-size:.8rem;font-weight:700}
body.light .upick-trigger{background:#fff;border-color:#e2e8f0}
body.light .upick-dropdown{background:#fff;border-color:#e2e8f0;box-shadow:0 8px 24px rgba(0,0,0,.12)}
body.light .upick-option:hover{background:#f8fafc}
body.light .upick-option.upick-sel{background:#eff6ff}
body.light .upick-label{color:#0f172a}
body.light .upick-name{color:#0f172a}
```

- [ ] **Step 2: Agregar JS del custom picker (al final del bloque JS de tasks, antes del cierre `// ── Client panel`)**

Encontrar:
```javascript
// ── Client panel: Tasks tab ───────────────────────────────────────────────────
```

Insertar antes de esa línea:
```javascript
// ── Custom user picker ────────────────────────────────────────────────────────

const _upickColors = ['#0369a1','#7e22ce','#065f46','#9a3412','#be185d','#0f766e','#1d4ed8','#a16207'];
function _upickColor(id) { return _upickColors[Number(id||0) % _upickColors.length]; }
function _upickInitials(name) { return (name||'').split(' ').slice(0,2).map(w=>w[0]||'').join('').toUpperCase()||'?'; }

function _upickToggle(id) {
  const trigger = document.getElementById('upick-'+id+'-trigger');
  const dd = document.getElementById('upick-'+id+'-dropdown');
  if (!dd) return;
  const isOpen = dd.style.display !== 'none';
  // close all pickers
  ['filter','modal'].forEach(k => {
    const d = document.getElementById('upick-'+k+'-dropdown');
    const t = document.getElementById('upick-'+k+'-trigger');
    if (d) d.style.display = 'none';
    if (t) t.classList.remove('open');
  });
  if (!isOpen) {
    _upickRenderDropdown(id);
    dd.style.display = '';
    if (trigger) trigger.classList.add('open');
  }
}

function _upickRenderDropdown(id) {
  const dd = document.getElementById('upick-'+id+'-dropdown');
  if (!dd) return;
  const selectedId = id === 'filter' ? _taskUserFilter
    : (document.getElementById('task-assignee-id')||{}).value || '';
  const isFilter = id === 'filter';
  const noneLabel = isFilter ? 'Todos los usuarios' : '— Sin asignar —';
  const noneAv = isFilter ? '👤' : '—';
  const noneAvStyle = isFilter
    ? 'background:#1e293b;color:#475569;font-size:.8rem'
    : 'background:#1e293b;color:#475569;font-size:.9rem';
  const noneSel = !selectedId;
  let html = `<div class="upick-option ${noneSel?'upick-sel':''}" onclick="_upickSelect('${id}','','','','${noneLabel}')">
    <div class="upick-av" style="${noneAvStyle}">${noneAv}</div>
    <span class="upick-name" style="color:#64748b">${noneLabel}</span>
    ${noneSel?'<span class="upick-check">✓</span>':''}
  </div>`;
  html += _allUsers.map(u => {
    const sel = String(u.id) === String(selectedId);
    return `<div class="upick-option ${sel?'upick-sel':''}" onclick="_upickSelect('${id}',${u.id},'${esc(u.name||'')}','${esc(u.email||'')}','${esc(u.name||'')}')">
      <div class="upick-av" style="background:${_upickColor(u.id)}">${_upickInitials(u.name)}</div>
      <span class="upick-name">${esc(u.name)}</span>
      ${sel?'<span class="upick-check">✓</span>':''}
    </div>`;
  }).join('');
  dd.innerHTML = html;
}

function _upickSelect(id, userId, userName, userEmail, label) {
  const trigger = document.getElementById('upick-'+id+'-trigger');
  const av = document.getElementById('upick-'+id+'-av');
  const lbl = document.getElementById('upick-'+id+'-label');
  if (userId) {
    if (av) { av.style.cssText = `background:${_upickColor(userId)};font-size:.65rem`; av.textContent = _upickInitials(userName); }
    if (lbl) lbl.textContent = userName;
  } else {
    const isFilter = id === 'filter';
    if (av) { av.style.cssText = 'background:#1e293b;color:#475569'; av.style.fontSize = isFilter ? '.8rem' : '.9rem'; av.textContent = isFilter ? '👤' : '—'; }
    if (lbl) lbl.textContent = label;
  }
  const dd = document.getElementById('upick-'+id+'-dropdown');
  if (dd) dd.style.display = 'none';
  if (trigger) trigger.classList.remove('open');
  if (id === 'modal') {
    const aid = document.getElementById('task-assignee-id');
    const aem = document.getElementById('task-assignee-email');
    if (aid) aid.value = userId || '';
    if (aem) aem.value = userEmail || '';
  }
  if (id === 'filter') {
    _taskUserFilter = String(userId);
    _updateFilterCounts();
    renderTasksList();
  }
}

// close picker on outside click
document.addEventListener('click', e => {
  if (!e.target.closest('.upick-wrap')) {
    ['filter','modal'].forEach(k => {
      const d = document.getElementById('upick-'+k+'-dropdown');
      const t = document.getElementById('upick-'+k+'-trigger');
      if (d) d.style.display = 'none';
      if (t) t.classList.remove('open');
    });
  }
}, true);

```

- [ ] **Step 3: Verificar que la página carga sin errores**

Las funciones están definidas pero no se usan aún (el HTML todavía tiene los `<select>` viejos). No debe haber errores en consola.

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "feat: custom user picker CSS y JS"
```

---

## Task 1: CSS — Nuevos estilos

**Files:**
- Modify: `dashboard.py` (zona CSS, ~línea 848)

- [ ] **Step 1: Agregar estilos después del bloque CSS de tasks existente**

Encontrar la línea exacta:
```
.tasks-filters{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
```

Reemplazar con:
```css
.filter-bar{display:flex;flex-direction:column;gap:10px;margin-bottom:14px}
.filter-row-1{display:flex;gap:8px}
.filter-row-2{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.search-input{flex:1;background:#111827;border:1px solid #1e293b;color:#e2e8f0;border-radius:8px;padding:7px 12px;font-size:.82rem}
.search-input::placeholder{color:#334155}
.user-select{background:#111827;border:1px solid #1e293b;color:#e2e8f0;border-radius:8px;padding:7px 12px;font-size:.82rem;min-width:150px}
.pill{padding:4px 12px;border-radius:99px;font-size:.72rem;font-weight:600;cursor:pointer;border:1px solid #1e293b;background:#111827;color:#64748b;transition:all .15s;white-space:nowrap}
.pill:hover{color:#e2e8f0}
.pill.active{background:#0088cc22;color:#38bdf8;border-color:#0088cc44}
.pill.warn{border-color:#450a0a}
.pill.warn.active{background:#450a0a22;color:#f87171;border-color:#450a0a}
.pill.orange{border-color:#431407}
.pill.orange.active{background:#431407;color:#fb923c;border-color:#9a3412}
.pill-count{font-weight:400;color:#334155;margin-left:3px;font-size:.68rem}
.pill.active .pill-count{color:#0088cc99}
.tasks-summary{font-size:.75rem;color:#475569;margin-bottom:10px}
.task-status-badge{padding:3px 9px;border-radius:99px;font-size:.68rem;font-weight:700;cursor:pointer;transition:all .15s;border:1px solid transparent;user-select:none}
.task-status-badge.todo{background:#1e293b;color:#64748b}
.task-status-badge.in_progress{background:#0c1f2e;color:#38bdf8;border-color:#0369a133}
.task-status-badge.done{background:#052e16;color:#4ade80;border-color:#16a34a33}
.task-row.in-progress{border-left:3px solid #0369a1}
.task-row.overdue{border-left:3px solid #f87171}
.task-edit-btn{background:none;border:1px solid #1e293b;color:#64748b;cursor:pointer;font-size:.78rem;padding:3px 7px;border-radius:6px;transition:all .15s}
.task-edit-btn:hover{border-color:#334155;color:#94a3b8}
```

- [ ] **Step 2: Agregar soporte light mode para nuevos elementos**

Encontrar:
```
body.light .task-check{border-color:#94a3b8}
```

Reemplazar con:
```css
body.light .task-check{border-color:#94a3b8}
body.light .search-input{background:#fff;border-color:#e2e8f0;color:#0f172a}
body.light .search-input::placeholder{color:#94a3b8}
body.light .user-select{background:#fff;border-color:#e2e8f0;color:#0f172a}
body.light .pill{background:#f8fafc;border-color:#e2e8f0;color:#475569}
body.light .pill:hover{color:#0f172a}
body.light .pill.active{background:#dbeafe;color:#1d4ed8;border-color:#93c5fd}
body.light .task-status-badge.todo{background:#f1f5f9;color:#64748b}
body.light .task-status-badge.in_progress{background:#dbeafe;color:#1d4ed8;border-color:#93c5fd}
body.light .task-status-badge.done{background:#dcfce7;color:#16a34a;border-color:#86efac}
body.light .tasks-summary{color:#94a3b8}
```

- [ ] **Step 3: Verificar visualmente (abrir CRM en browser, ir a Tareas)**

El panel de tareas debe verse igual que antes (los nuevos estilos no se aplican hasta que cambie el HTML).

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "style: estilos nuevos para panel de tareas — filter bar, status badge, pills"
```

---

## Task 2: HTML — Nueva barra de filtros

**Files:**
- Modify: `dashboard.py` (zona HTML tasks panel, ~línea 1063)

- [ ] **Step 1: Reemplazar el bloque tasks-filters con la nueva barra**

Encontrar (bloque exacto):
```html
    <div class="tasks-filters">
      <button class="filter-btn active" data-tfilter="all" onclick="filterTasks('all',this)">Todas</button>
      <button class="filter-btn" data-tfilter="todo" onclick="filterTasks('todo',this)">Pendientes</button>
      <button class="filter-btn" data-tfilter="in_progress" onclick="filterTasks('in_progress',this)">En progreso</button>
      <button class="filter-btn" data-tfilter="done" onclick="filterTasks('done',this)">Hechas</button>
    </div>

    <div id="tasks-list"></div>
```

Reemplazar con:
```html
    <div class="filter-bar">
      <div class="filter-row-1">
        <input type="text" id="task-search" class="search-input" placeholder="🔍 Buscar tarea..." oninput="_onTaskSearch(this.value)">
        <div class="upick-wrap">
          <div class="upick-trigger" id="upick-filter-trigger" onclick="_upickToggle('filter')">
            <div class="upick-av" id="upick-filter-av" style="background:#1e293b;color:#475569;font-size:.8rem">👤</div>
            <span class="upick-label" id="upick-filter-label">Todos los usuarios</span>
            <span class="upick-chevron">▾</span>
          </div>
          <div class="upick-dropdown" id="upick-filter-dropdown" style="display:none"></div>
        </div>
      </div>
      <div class="filter-row-2">
        <button class="pill active" id="pill-all" onclick="filterTasks('all',this)">Todas <span class="pill-count" id="pill-count-all">0</span></button>
        <button class="pill" id="pill-todo" onclick="filterTasks('todo',this)">Pendientes <span class="pill-count" id="pill-count-todo">0</span></button>
        <button class="pill" id="pill-inprogress" onclick="filterTasks('in_progress',this)">En progreso <span class="pill-count" id="pill-count-inprogress">0</span></button>
        <button class="pill" id="pill-done" onclick="filterTasks('done',this)">Hechas <span class="pill-count" id="pill-count-done">0</span></button>
        <div style="width:1px;height:20px;background:#1e293b;margin:0 2px;flex-shrink:0"></div>
        <button class="pill warn" id="pill-high" onclick="filterTasksQuick('high',this)">⚠ Alta prioridad <span class="pill-count" id="pill-count-high">0</span></button>
        <button class="pill orange" id="pill-overdue" onclick="filterTasksQuick('overdue',this)">🕐 Vencidas <span class="pill-count" id="pill-count-overdue">0</span></button>
      </div>
    </div>
    <div id="tasks-summary" class="tasks-summary"></div>

    <div id="tasks-list"></div>
```

- [ ] **Step 2: Verificar en browser**

Ir a Tareas: debe verse el buscador + dropdown de usuarios (vacío por ahora) + pills con "0" + div resumen vacío.

- [ ] **Step 3: Commit**

```bash
git add dashboard.py
git commit -m "feat: nueva barra de filtros en panel de tareas (HTML)"
```

---

## Task 3: HTML — Agregar Estado e IDs al modal

**Files:**
- Modify: `dashboard.py` (zona HTML `#add-task-modal`, ~línea 1194)

- [ ] **Step 1: Agregar campo Estado al modal y IDs necesarios**

Encontrar:
```html
    <div class="modal-btns" style="margin-top:16px">
      <button class="btn-cancel" onclick="document.getElementById('add-task-modal').classList.remove('open')">Cancelar</button>
      <button class="btn-confirm" onclick="submitAddTask()">+ Crear tarea</button>
    </div>
```

Reemplazar con:
```html
    <div style="margin-top:10px">
      <label class="modal-label">Estado</label>
      <select id="task-status-input" class="modal-input">
        <option value="todo">● Pendiente</option>
        <option value="in_progress">⚡ En progreso</option>
        <option value="done">✓ Hecha</option>
      </select>
    </div>
    <input type="hidden" id="task-edit-id">
    <div class="modal-btns" style="margin-top:16px">
      <button class="btn-cancel" onclick="document.getElementById('add-task-modal').classList.remove('open')">Cancelar</button>
      <button class="btn-confirm" id="task-submit-btn" onclick="submitAddTask()">+ Crear tarea</button>
    </div>
```

- [ ] **Step 2: Reemplazar `<select>` de "Asignar a" por el custom picker**

Encontrar en el modal:
```html
    <div style="margin-top:10px">
      <label class="modal-label">Asignar a</label>
      <select id="task-assignee-input" class="modal-input" onchange="_onTaskAssigneeChange(this)">
        <option value="">— Sin asignar —</option>
      </select>
      <input type="hidden" id="task-assignee-id">
      <input type="hidden" id="task-assignee-email">
    </div>
```

Reemplazar con:
```html
    <div style="margin-top:10px">
      <label class="modal-label">Asignar a</label>
      <div class="upick-wrap">
        <div class="upick-trigger" id="upick-modal-trigger" onclick="_upickToggle('modal')">
          <div class="upick-av" id="upick-modal-av" style="background:#1e293b;color:#475569;font-size:.9rem">—</div>
          <span class="upick-label" id="upick-modal-label">— Sin asignar —</span>
          <span class="upick-chevron">▾</span>
        </div>
        <div class="upick-dropdown" id="upick-modal-dropdown" style="display:none"></div>
      </div>
      <input type="hidden" id="task-assignee-id">
      <input type="hidden" id="task-assignee-email">
    </div>
```

- [ ] **Step 3: Cambiar el input de vencimiento de `date` a `datetime-local`**

Encontrar en el modal:
```html
        <label class="modal-label">Vencimiento</label>
        <input type="date" id="task-deadline-input" class="modal-input">
```

Reemplazar con:
```html
        <label class="modal-label">Vencimiento y hora</label>
        <input type="datetime-local" id="task-deadline-input" class="modal-input">
```

- [ ] **Step 4: Verificar en browser**

Abrir modal con "+ Nueva tarea": el campo "Asignar a" debe ser el custom picker (trigger con "—" y dropdown al hacer click), el campo Estado con "● Pendiente", y el vencimiento con date + time picker.

- [ ] **Step 5: Commit**

```bash
git add dashboard.py
git commit -m "feat: custom picker en modal, campo Estado, hora en vencimiento"
```

---

## Task 4: JS — Variables de estado + `_getFilteredTasks()` + `_updateFilterCounts()`

**Files:**
- Modify: `dashboard.py` (zona JS tasks, ~línea 2606)

- [ ] **Step 1: Agregar variables nuevas y funciones de filtrado centralizadas**

Encontrar:
```javascript
let _allTasks = [];
let _allLeads = [];
let _taskStatusFilter = 'all';
```

Reemplazar con:
```javascript
let _allTasks = [];
let _allLeads = [];
let _taskStatusFilter = 'all';
let _taskUserFilter = '';
let _taskSearchQuery = '';
let _taskQuickFilter = '';
let _editingTaskId = null;
let _taskSearchTimer = null;

function _getFilteredTasks() {
  let tasks = _allTasks;
  if (_taskUserFilter) tasks = tasks.filter(t => String(t.assignee_id) === String(_taskUserFilter));
  if (_taskQuickFilter === 'high') {
    tasks = tasks.filter(t => t.priority === 'high');
  } else if (_taskQuickFilter === 'overdue') {
    const now = new Date();
    tasks = tasks.filter(t => t.deadline && new Date(t.deadline) < now && t.status !== 'done');
  } else if (_taskStatusFilter !== 'all') {
    tasks = tasks.filter(t => t.status === _taskStatusFilter);
  }
  if (_taskSearchQuery) {
    const q = _taskSearchQuery.toLowerCase();
    tasks = tasks.filter(t => (t.title||'').toLowerCase().includes(q) || (t.description||'').toLowerCase().includes(q));
  }
  return tasks;
}

function _updateFilterCounts() {
  let base = _allTasks;
  if (_taskUserFilter) base = base.filter(t => String(t.assignee_id) === String(_taskUserFilter));
  const now = new Date();
  const counts = {
    all: base.length,
    todo: base.filter(t => t.status === 'todo').length,
    inprogress: base.filter(t => t.status === 'in_progress').length,
    done: base.filter(t => t.status === 'done').length,
    high: base.filter(t => t.priority === 'high').length,
    overdue: base.filter(t => t.deadline && new Date(t.deadline) < now && t.status !== 'done').length,
  };
  ['all','todo','inprogress','done','high','overdue'].forEach(k => {
    const el = document.getElementById('pill-count-' + k);
    if (el) el.textContent = counts[k];
  });
}
```

- [ ] **Step 2: Verificar que la página carga sin errores JS**

Abrir consola del browser — no debe haber errores. El panel de tareas sigue funcionando igual.

- [ ] **Step 3: Commit**

```bash
git add dashboard.py
git commit -m "feat: variables de estado y funciones _getFilteredTasks/_updateFilterCounts"
```

---

## Task 5: JS — Handlers de filtro + `renderTasksList()` actualizado

**Files:**
- Modify: `dashboard.py` (zona JS, ~línea 2625)

- [ ] **Step 1: Actualizar `filterTasks()` y agregar `filterTasksQuick()`, `_onTaskUserFilterChange()`, `_onTaskSearch()`**

Encontrar:
```javascript
function filterTasks(status, btn) {
  _taskStatusFilter = status;
  document.querySelectorAll('.tasks-filters .filter-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  renderTasksList();
}
```

Reemplazar con:
```javascript
function filterTasks(status, btn) {
  _taskStatusFilter = status;
  _taskQuickFilter = '';
  document.querySelectorAll('.filter-row-2 .pill').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  renderTasksList();
}

function filterTasksQuick(type, btn) {
  _taskQuickFilter = _taskQuickFilter === type ? '' : type;
  _taskStatusFilter = 'all';
  document.querySelectorAll('.filter-row-2 .pill').forEach(b => b.classList.remove('active'));
  if (_taskQuickFilter && btn) {
    btn.classList.add('active');
  } else {
    const pillAll = document.getElementById('pill-all');
    if (pillAll) pillAll.classList.add('active');
  }
  renderTasksList();
}

function _onTaskUserFilterChange(val) {
  _taskUserFilter = val;
  _updateFilterCounts();
  renderTasksList();
}

function _onTaskSearch(val) {
  clearTimeout(_taskSearchTimer);
  _taskSearchTimer = setTimeout(() => {
    _taskSearchQuery = val.trim();
    renderTasksList();
  }, 200);
}
```

- [ ] **Step 2: Actualizar `renderTasksList()` para usar `_getFilteredTasks()` y actualizar resumen**

Encontrar:
```javascript
function renderTasksList() {
  const container = document.getElementById('tasks-list');
  if (!container) return;
  let tasks = _taskStatusFilter === 'all'
    ? _allTasks
    : _allTasks.filter(t => t.status === _taskStatusFilter);
  tasks = [...tasks].sort((a, b) => {
    const prio = {high:0,medium:1,low:2};
    return (prio[a.priority]||1) - (prio[b.priority]||1);
  });
  if (!tasks.length) { container.innerHTML = '<div class="tasks-empty">Sin tareas. Agregá una con el botón de arriba.</div>'; return; }
  container.innerHTML = tasks.map(t => _taskRowHtml(t)).join('');
}
```

Reemplazar con:
```javascript
function renderTasksList() {
  const container = document.getElementById('tasks-list');
  if (!container) return;
  let tasks = _getFilteredTasks();
  tasks = [...tasks].sort((a, b) => {
    const prio = {high:0,medium:1,low:2};
    return (prio[a.priority]||1) - (prio[b.priority]||1);
  });
  const summary = document.getElementById('tasks-summary');
  if (summary) {
    const userLabel = _taskUserFilter
      ? '👤 ' + ((_allUsers.find(u => String(u.id) === String(_taskUserFilter)) || {}).name || '')
      : 'todos los usuarios';
    const filterLabel = _taskQuickFilter === 'high' ? 'Alta prioridad'
      : _taskQuickFilter === 'overdue' ? 'Vencidas'
      : _taskStatusFilter === 'all' ? 'Todas'
      : _taskStatusFilter === 'todo' ? 'Pendientes'
      : _taskStatusFilter === 'in_progress' ? 'En progreso'
      : 'Hechas';
    summary.textContent = `${tasks.length} tarea${tasks.length !== 1 ? 's' : ''} · ${userLabel} · ${filterLabel}`;
  }
  if (!tasks.length) { container.innerHTML = '<div class="tasks-empty">Sin tareas para este filtro.</div>'; return; }
  container.innerHTML = tasks.map(t => _taskRowHtml(t)).join('');
}
```

- [ ] **Step 3: Verificar en browser**

Ir a Tareas: las pills de estado siguen funcionando. El texto resumen aparece debajo de la barra ("X tareas · todos los usuarios · Todas"). Los filtros rápidos (Alta prioridad, Vencidas) funcionan al hacer click.

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "feat: handlers de filtro y renderTasksList actualizado con resumen"
```

---

## Task 6: JS — `loadTasks()` + `_populateUserFilter()`

**Files:**
- Modify: `dashboard.py` (~línea 2613)

- [ ] **Step 1: Actualizar `loadTasks()` para poblar dropdown de usuarios y conteos**

Encontrar:
```javascript
async function loadTasks() {
  try {
    const [tr, lr] = await Promise.all([
      fetch('/api/tasks').then(r => r.json()),
      fetch('/api/leads').then(r => r.json()),
    ]);
    _allTasks = Array.isArray(tr) ? tr : [];
    _allLeads = Array.isArray(lr) ? lr : [];
  } catch { _allTasks = []; }
  renderTasksList();
}
```

Reemplazar con:
```javascript
async function loadTasks() {
  try {
    const [tr, lr] = await Promise.all([
      fetch('/api/tasks').then(r => r.json()),
      fetch('/api/leads').then(r => r.json()),
    ]);
    _allTasks = Array.isArray(tr) ? tr : [];
    _allLeads = Array.isArray(lr) ? lr : [];
  } catch { _allTasks = []; }
  await _loadUsersForTask();
  _populateUserFilter();
  _updateFilterCounts();
  renderTasksList();
}

function _populateUserFilter() {
  // Restore filter picker display after reload (preserves selected user if any)
  if (_taskUserFilter) {
    const u = _allUsers.find(u => String(u.id) === String(_taskUserFilter));
    if (u) {
      const av = document.getElementById('upick-filter-av');
      const lbl = document.getElementById('upick-filter-label');
      if (av) { av.style.cssText = `background:${_upickColor(u.id)};font-size:.65rem`; av.textContent = _upickInitials(u.name); }
      if (lbl) lbl.textContent = u.name;
    }
  }
}
```

- [ ] **Step 2: Verificar en browser**

Ir a Tareas: el dropdown "Todos los usuarios" ahora muestra los usuarios del sistema. Las pills muestran conteos reales. Seleccionar un usuario filtra la lista.

- [ ] **Step 3: Commit**

```bash
git add dashboard.py
git commit -m "feat: loadTasks pobla dropdown de usuarios y conteos de pills"
```

---

## Task 7: JS — `_taskRowHtml()` con status badge y botón editar

**Files:**
- Modify: `dashboard.py` (~línea 2646)

- [ ] **Step 1: Reemplazar `_taskRowHtml()` completo**

Encontrar el inicio de la función:
```javascript
function _taskRowHtml(t) {
  const done = t.status === 'done';
  const lead = t.client_id ? _allLeads.find(l => l.id === t.client_id) : null;
```

Y el final (hasta el cierre de la función):
```javascript
  return `<div class="task-row" id="task-row-${t.id}">
    <div class="task-check ${done ? 'done' : ''}" onclick="_toggleTask(${t.id},${done})">${done ? '✓' : ''}</div>
    <div class="task-body" style="flex:1;min-width:0">
      <div class="task-title ${done ? 'done-text' : ''}">${esc(t.title)}</div>
      <div class="task-meta">
        ${t.priority ? `<span class="task-priority ${t.priority}">${prioLabel}</span>` : ''}
        ${lead ? `<span class="task-client-link" onclick="openClientPanel(${lead.id})">${esc(lead.name||'')}</span>` : ''}
        ${dlStr ? `<span class="task-deadline ${overdue ? 'overdue' : ''}">📅 ${dlStr}${overdue?' (vencida)':''}</span>` : ''}
        ${assigneeBadge}${createdByBadge}
      </div>
      ${progressBar}
    </div>
    <div class="task-actions">
      <button class="task-del-btn" onclick="_deleteTask(${t.id})" title="Eliminar">🗑</button>
    </div>
  </div>`;
}
```

Reemplazar toda la función con:
```javascript
function _taskRowHtml(t) {
  const done = t.status === 'done';
  const inProgress = t.status === 'in_progress';
  const lead = t.client_id ? _allLeads.find(l => l.id === t.client_id) : null;
  const now = new Date(); const dl = t.deadline ? new Date(t.deadline) : null;
  const overdue = dl && dl < now && !done;
  const hasTime = dl && (dl.getHours() !== 0 || dl.getMinutes() !== 0);
  const dlStr = dl ? dl.toLocaleDateString('es-UY',{day:'2-digit',month:'2-digit'})
    + (hasTime ? ' ' + dl.toLocaleTimeString('es-UY',{hour:'2-digit',minute:'2-digit'}) : '') : '';
  const prioLabel = ({'high':'Alta','medium':'Media','low':'Baja'})[t.priority] || t.priority;
  const statusLabel = {todo:'● Pendiente', in_progress:'⚡ En progreso', done:'✓ Hecha'}[t.status] || '● Pendiente';
  const statusClass = t.status || 'todo';
  const goalTypeLabel = {
    'leads_contactados':    'leads contactados',
    'llamadas_realizadas':  'llamadas realizadas',
    'llamadas_contestadas': 'llamadas contestadas',
    'reuniones_agendadas':  'reuniones agendadas',
    'reuniones_hechas':     'reuniones hechas',
    'presupuestos_enviados':'presupuestos enviados',
    'clientes_cerrados':    'clientes cerrados',
  };
  const progress = t.goal ? Math.min(t.progress || 0, t.goal) : 0;
  const pct = t.goal ? Math.round(progress / t.goal * 100) : 0;
  const progressBar = t.goal ? `
    <div style="margin-top:6px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:3px;cursor:pointer" onclick="_toggleTaskHistory(${t.id})">
        <span style="font-size:.72rem;color:#64748b">${goalTypeLabel[t.goal_type]||t.goal_type}: </span>
        <span style="font-size:.72rem;font-weight:600;color:${done||pct>=100?'#10b981':'#e2e8f0'}">${progress}/${t.goal}</span>
        ${pct >= 100 ? '<span style="font-size:.68rem;color:#10b981">✓ Meta alcanzada</span>' : ''}
        <span style="font-size:.68rem;color:#334155">▾ historial</span>
      </div>
      <div style="height:4px;background:#1e293b;border-radius:2px;overflow:hidden;max-width:240px">
        <div style="height:100%;width:${pct}%;background:${pct>=100?'#10b981':'#0088cc'};transition:width .3s"></div>
      </div>
      <div id="task-history-${t.id}" style="display:none;margin-top:6px;padding:6px 0;border-top:1px solid #1e293b"></div>
    </div>` : '';
  const assigneeBadge = t.assignee_name ? `<span style="font-size:.72rem;color:#64748b;background:#1a2234;padding:2px 7px;border-radius:10px">→ ${esc(t.assignee_name)}</span>` : '';
  const createdByBadge = t.created_by_name && t.assignee_name ? `<span style="font-size:.72rem;color:#334155">de ${esc(t.created_by_name)}</span>` : '';
  const rowExtra = inProgress ? ' in-progress' : overdue ? ' overdue' : '';
  return `<div class="task-row${rowExtra}" id="task-row-${t.id}">
    <div class="task-body" style="flex:1;min-width:0">
      <div class="task-title ${done ? 'done-text' : ''}">${esc(t.title)}</div>
      ${t.description ? `<div style="font-size:.75rem;color:#64748b;margin-bottom:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(t.description)}</div>` : ''}
      <div class="task-meta">
        <span class="task-status-badge ${statusClass}" onclick="_setTaskStatus(${t.id})" title="Click para cambiar estado">${statusLabel}</span>
        ${t.priority ? `<span class="task-priority ${t.priority}">${prioLabel}</span>` : ''}
        ${lead ? `<span class="task-client-link" onclick="openClientPanel(${lead.id})">${esc(lead.name||'')}</span>` : ''}
        ${dlStr ? `<span class="task-deadline ${overdue ? 'overdue' : ''}">📅 ${dlStr}${overdue?' (vencida)':''}</span>` : ''}
        ${assigneeBadge}${createdByBadge}
      </div>
      ${progressBar}
    </div>
    <div class="task-actions">
      <button class="task-edit-btn" onclick="openEditTaskModal(${t.id})" title="Editar">✏️</button>
      <button class="task-del-btn" onclick="_deleteTask(${t.id})" title="Eliminar">🗑</button>
    </div>
  </div>`;
}
```

- [ ] **Step 2: Verificar en browser**

Ir a Tareas: cada tarea muestra el badge de estado (● Pendiente / ⚡ En progreso / ✓ Hecha) en lugar del checkbox. El botón ✏️ aparece en cada card. Las tareas en progreso tienen borde azul. Las vencidas tienen borde rojo.

- [ ] **Step 3: Verificar panel de cliente**

Abrir el panel lateral de cualquier cliente → pestaña Tareas: las cards se ven igual (también usan `_taskRowHtml`). El badge y el botón editar funcionan ahí también.

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "feat: _taskRowHtml con status badge y botón editar"
```

---

## Task 8: JS — `_setTaskStatus()` + actualizar `_deleteTask()`

**Files:**
- Modify: `dashboard.py` (~línea 2697)

- [ ] **Step 1: Agregar `_setTaskStatus()` después de `_toggleTask()`**

Encontrar:
```javascript
async function _toggleTask(id, wasDone) {
  const newStatus = wasDone ? 'todo' : 'done';
  await fetch('/api/tasks/' + id, {
    method:'PUT', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({status: newStatus})
  });
  const t = _allTasks.find(t => t.id === id);
  if (t) t.status = newStatus;
  renderTasksList();
}
```

Reemplazar con:
```javascript
async function _toggleTask(id, wasDone) {
  const newStatus = wasDone ? 'todo' : 'done';
  await fetch('/api/tasks/' + id, {
    method:'PUT', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({status: newStatus})
  });
  const t = _allTasks.find(t => t.id === id);
  if (t) t.status = newStatus;
  renderTasksList();
}

async function _setTaskStatus(id) {
  const t = _allTasks.find(t => t.id === id);
  if (!t) return;
  const cycle = {todo: 'in_progress', in_progress: 'done', done: 'todo'};
  const newStatus = cycle[t.status] || 'in_progress';
  await fetch('/api/tasks/' + id, {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({status: newStatus})
  });
  t.status = newStatus;
  _updateFilterCounts();
  renderTasksList();
  if (_cpClientId) {
    const ct = (_cpData.tasks||[]).find(ct => ct.id === id);
    if (ct) { ct.status = newStatus; _cpSwitchTab('ctasks'); }
  }
}
```

- [ ] **Step 2: Actualizar `_deleteTask()` para llamar `_updateFilterCounts()`**

Encontrar:
```javascript
async function _deleteTask(id) {
  await fetch('/api/tasks/' + id, {method:'DELETE'});
  _allTasks = _allTasks.filter(t => t.id !== id);
  renderTasksList();
  if (_cpClientId) { _cpData.tasks = (_cpData.tasks||[]).filter(t => t.id !== id); _cpSwitchTab('ctasks'); }
}
```

Reemplazar con:
```javascript
async function _deleteTask(id) {
  await fetch('/api/tasks/' + id, {method:'DELETE'});
  _allTasks = _allTasks.filter(t => t.id !== id);
  _updateFilterCounts();
  renderTasksList();
  if (_cpClientId) { _cpData.tasks = (_cpData.tasks||[]).filter(t => t.id !== id); _cpSwitchTab('ctasks'); }
}
```

- [ ] **Step 3: Verificar en browser**

Ir a Tareas y hacer click en el badge de una tarea: debe ciclar Pendiente → En progreso → Hecha → Pendiente. Verificar que el borde izquierdo azul aparece/desaparece. Verificar que las pills actualizan su conteo. Eliminar una tarea y verificar que los conteos actualizan.

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "feat: _setTaskStatus (ciclo 3 estados) y _deleteTask actualiza conteos"
```

---

## Task 9: JS — Modal de edición

**Files:**
- Modify: `dashboard.py` (~línea 2763)

- [ ] **Step 1: Agregar `openEditTaskModal()` después de `openAddTaskModal()`**

Encontrar la función completa de `openAddTaskModal`:
```javascript
async function openAddTaskModal(clientId, clientName) {
  document.getElementById('task-title-input').value = '';
  document.getElementById('task-desc-input').value = '';
  document.getElementById('task-priority-input').value = 'medium';
  document.getElementById('task-deadline-input').value = new Date().toISOString().slice(0,16);
  document.getElementById('task-goal-type-input').value = '';
  document.getElementById('task-goal-input').value = '';
  document.getElementById('task-goal-input').style.display = 'none';
  document.getElementById('task-client-search').value = clientName || '';
  document.getElementById('task-client-id').value = clientId || '';
  document.getElementById('task-client-chosen').textContent = clientName ? 'Cliente: ' + clientName : '';
  document.getElementById('task-client-results').style.display = 'none';
  await _loadUsersForTask();
  const sel = document.getElementById('task-assignee-input');
  sel.innerHTML = '<option value="">— Sin asignar —</option>';
  _allUsers.forEach(u => {
    const opt = new Option(u.name, u.id);
    opt.dataset.uid = u.id;
    opt.dataset.email = u.email;
    sel.appendChild(opt);
  });
  document.getElementById('task-assignee-id').value = '';
  document.getElementById('task-assignee-email').value = '';
  document.getElementById('add-task-modal').classList.add('open');
  setTimeout(() => document.getElementById('task-title-input').focus(), 50);
}
```

Reemplazar con:
```javascript
async function openAddTaskModal(clientId, clientName) {
  _editingTaskId = null;
  document.getElementById('task-title-input').value = '';
  document.getElementById('task-desc-input').value = '';
  document.getElementById('task-priority-input').value = 'medium';
  document.getElementById('task-deadline-input').value = new Date().toISOString().slice(0,16);
  document.getElementById('task-goal-type-input').value = '';
  document.getElementById('task-goal-input').value = '';
  document.getElementById('task-goal-input').style.display = 'none';
  document.getElementById('task-client-search').value = clientName || '';
  document.getElementById('task-client-id').value = clientId || '';
  document.getElementById('task-client-chosen').textContent = clientName ? 'Cliente: ' + clientName : '';
  document.getElementById('task-client-results').style.display = 'none';
  document.getElementById('task-status-input').value = 'todo';
  await _loadUsersForTask();
  _upickSelect('modal', '', '', '', '— Sin asignar —');
  const h3 = document.getElementById('add-task-modal').querySelector('h3');
  if (h3) h3.textContent = 'Nueva tarea';
  const submitBtn = document.getElementById('task-submit-btn');
  if (submitBtn) submitBtn.textContent = '+ Crear tarea';
  document.getElementById('add-task-modal').classList.add('open');
  setTimeout(() => document.getElementById('task-title-input').focus(), 50);
}

async function openEditTaskModal(taskId) {
  const t = _allTasks.find(t => t.id === taskId);
  if (!t) return;
  _editingTaskId = taskId;
  document.getElementById('task-title-input').value = t.title || '';
  document.getElementById('task-desc-input').value = t.description || '';
  document.getElementById('task-priority-input').value = t.priority || 'medium';
  document.getElementById('task-deadline-input').value = t.deadline ? t.deadline.slice(0,16).replace(' ','T') : '';
  document.getElementById('task-goal-type-input').value = t.goal_type || '';
  document.getElementById('task-goal-input').value = t.goal || '';
  document.getElementById('task-goal-input').style.display = t.goal_type ? '' : 'none';
  document.getElementById('task-status-input').value = t.status || 'todo';
  const clientLead = t.client_id ? _allLeads.find(l => l.id === t.client_id) : null;
  document.getElementById('task-client-search').value = clientLead ? (clientLead.name||'') : '';
  document.getElementById('task-client-id').value = t.client_id || '';
  document.getElementById('task-client-chosen').textContent = clientLead ? 'Cliente: ' + (clientLead.name||'') : '';
  document.getElementById('task-client-results').style.display = 'none';
  await _loadUsersForTask();
  _upickSelect('modal', t.assignee_id||'', t.assignee_name||'', t.assignee_email||'', t.assignee_name||'— Sin asignar —');
  const h3 = document.getElementById('add-task-modal').querySelector('h3');
  if (h3) h3.textContent = 'Editar tarea';
  const submitBtn = document.getElementById('task-submit-btn');
  if (submitBtn) submitBtn.textContent = 'Guardar cambios';
  document.getElementById('add-task-modal').classList.add('open');
  setTimeout(() => document.getElementById('task-title-input').focus(), 50);
}
```

- [ ] **Step 2: Actualizar `submitAddTask()` para manejar modo edición**

Encontrar:
```javascript
  try {
    const r = await fetch('/api/tasks', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    const d = await r.json();
    document.getElementById('add-task-modal').classList.remove('open');
    const newTask = {id: d.id, ...body};
    _allTasks.unshift(newTask);
    renderTasksList();
    if (_cpClientId && body.client_id === _cpClientId) {
      _cpData.tasks = [newTask, ...(_cpData.tasks||[])];
      _cpSwitchTab('ctasks');
    }
  } finally {
    _taskSubmitting = false;
  }
```

Reemplazar con:
```javascript
  try {
    if (_editingTaskId) {
      await fetch('/api/tasks/' + _editingTaskId, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
      const idx = _allTasks.findIndex(t => t.id === _editingTaskId);
      if (idx !== -1) _allTasks[idx] = {..._allTasks[idx], ...body};
      document.getElementById('add-task-modal').classList.remove('open');
      _updateFilterCounts();
      renderTasksList();
      if (_cpClientId) _cpSwitchTab('ctasks');
    } else {
      const r = await fetch('/api/tasks', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
      const d = await r.json();
      document.getElementById('add-task-modal').classList.remove('open');
      const newTask = {id: d.id, ...body};
      _allTasks.unshift(newTask);
      _updateFilterCounts();
      renderTasksList();
      if (_cpClientId && body.client_id === _cpClientId) {
        _cpData.tasks = [newTask, ...(_cpData.tasks||[])];
        _cpSwitchTab('ctasks');
      }
    }
  } finally {
    _taskSubmitting = false;
  }
```

- [ ] **Step 3: Actualizar `submitAddTask()` para leer Estado del select**

Encontrar dentro de `submitAddTask()`:
```javascript
    status: 'todo',
```

Reemplazar con:
```javascript
    status: document.getElementById('task-status-input').value || 'todo',
```

- [ ] **Step 4: Verificar flujo completo en browser**

1. Click ✏️ en una tarea → modal se abre con datos pre-cargados, título dice "Editar tarea", botón dice "Guardar cambios"
2. Cambiar prioridad/título/estado → Guardar → la card se actualiza sin recargar
3. Click "+ Nueva tarea" → modal limpio, título "Nueva tarea", botón "+ Crear tarea"
4. Crear tarea → aparece en la lista, conteos actualizan
5. Filtrar por usuario → solo aparecen tareas de ese usuario, conteos de pills cambian
6. Buscar texto → lista se filtra en tiempo real

- [ ] **Step 5: Commit**

```bash
git add dashboard.py
git commit -m "feat: modal de edición de tareas, openEditTaskModal, submitAddTask actualizado"
```

---

## Verificación final

- [ ] Abrir el CRM en producción (Railway) después de hacer push
- [ ] Ir a Tareas: barra de filtros visible, pills con conteos, buscador y dropdown de usuarios
- [ ] Filtrar por cada usuario del equipo: lista actualiza correctamente
- [ ] Buscar texto: filtra en tiempo real
- [ ] Click en badge de estado de una tarea: cicla Pendiente → En progreso → Hecha
- [ ] Click ✏️: modal de edición con datos cargados, guardar actualiza la card
- [ ] "+ Nueva tarea": crea normalmente, aparece en lista
- [ ] Eliminar tarea: desaparece, conteos actualizan
- [ ] Pills "Alta prioridad" y "Vencidas" filtran correctamente
- [ ] Abrir panel lateral de cliente → pestaña Tareas: funciona igual que antes
- [ ] Modo light (si aplica): estilos correctos
