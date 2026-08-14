# Mobile Responsive Design — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hacer que el CRM se sienta como app nativa en celular (≤768px) y como app de escritorio en computadora (>768px), sin romper nada del comportamiento actual.

**Architecture:** Todos los cambios en `dashboard.py`. Mobile usa media queries `@media(max-width:768px)` para CSS y un bloque JS que corre en el IIFE de panel access control. Desktop queda intacto — ningún estilo o JS nuevo afecta viewports >768px.

**Tech Stack:** Vanilla JS, CSS, HTML — todo en `dashboard.py`. Sin dependencias nuevas.

**Constraint crítica:** Desktop queda EXACTAMENTE igual. Toda nueva lógica JS usa `window.innerWidth <= 768` o media queries CSS como guardia. Nunca modificar estilos sin media query scope.

---

## Archivos modificados

- `dashboard.py` — único archivo. Cambios en zonas:
  1. CSS `@media(max-width:768px)` (expandir bloque existente ~línea 252)
  2. HTML — agregar `<nav id="mobile-bottom-nav">`, sheet "Más", FAB
  3. JS — ampliar IIFE de panel access + actualizar `showPanel()`

---

## Task 0: CSS — Estilos globales mobile

**Files:**
- Modify: `dashboard.py` (~línea 252, bloque `@media(max-width:768px)`)

- [ ] **Step 1: Expandir el bloque media query existente con estilos globales**

Encontrar el bloque existente que termina con:
```css
  .max-input{width:100%}
}
```

Reemplazar con (agregar después de `.max-input{width:100%}` antes del `}`):
```css
  .max-input{width:100%}
  /* ── Global mobile ── */
  .main{padding:14px 14px 90px!important}
  .page-header{margin-bottom:16px}
  .page-header h1{font-size:1.1rem}
  .nav-section-label{display:none}
  /* Modales como bottom sheets */
  .modal-overlay{align-items:flex-end!important}
  .modal{border-radius:20px 20px 0 0!important;width:100%!important;max-width:100%!important;max-height:90vh;overflow-y:auto}
  .modal-row{grid-template-columns:1fr!important}
  /* Touch targets mínimo 44px */
  .filter-btn,.btn-cancel,.btn-confirm,.run-btn,.contact-btn,.pitch-btn,.mail-btn{min-height:44px}
  .cp-tab{padding:12px 14px;font-size:.78rem}
  /* Kanban: scroll táctil */
  .kanban-board{-webkit-overflow-scrolling:touch;scroll-snap-type:x mandatory;padding-bottom:24px}
  .kanban-col{scroll-snap-align:start}
}
```

- [ ] **Step 2: Agregar light mode para bottom nav (al final del bloque light mode, ~línea 777)**

Encontrar:
```css
body.light .task-status-badge.in_progress{background:#dbeafe;color:#1d4ed8;border-color:#93c5fd}
body.light .task-status-badge.done{background:#dcfce7;color:#16a34a;border-color:#86efac}
body.light .tasks-summary{color:#94a3b8}
```

Reemplazar con:
```css
body.light .task-status-badge.in_progress{background:#dbeafe;color:#1d4ed8;border-color:#93c5fd}
body.light .task-status-badge.done{background:#dcfce7;color:#16a34a;border-color:#86efac}
body.light .tasks-summary{color:#94a3b8}
body.light .mobile-bottom-nav{background:rgba(255,255,255,.92);border-color:rgba(0,0,0,.1)}
body.light .mbn-icon{stroke:#94a3b8}
body.light .mbn-label{color:#94a3b8}
body.light .mbn-item.active{background:rgba(0,136,204,.12)}
body.light .mbn-item.active .mbn-icon{stroke:#0088cc}
body.light .mbn-item.active .mbn-label{color:#0088cc}
body.light .mas-sheet{background:#fff;border-top-color:#e2e8f0}
body.light .mas-sheet-handle{background:#e2e8f0}
body.light .mas-sheet-title{color:#94a3b8}
body.light .mas-sheet-item{background:#f8fafc;border-color:#e2e8f0}
body.light .mas-sheet-icon{stroke:#475569}
body.light .mas-sheet-label{color:#0f172a}
body.light .mas-sheet-backdrop{background:rgba(0,0,0,.3)}
```

- [ ] **Step 3: Verificar en browser (desktop)**

Abrir el CRM en desktop — todo debe verse exactamente igual que antes. Ningún elemento debe cambiar. Si algo cambió, el media query no está correctamente scoped.

- [ ] **Step 4: Commit**

```bash
cd C:/Users/juant/lead-gen-uy && git add dashboard.py && git commit -m "style: estilos globales mobile — modales bottom sheet, touch targets, padding"
```

---

## Task 1: CSS — Bottom navigation bar

**Files:**
- Modify: `dashboard.py` (CSS, agregar estilos para `.mobile-bottom-nav` y elementos)

- [ ] **Step 1: Agregar CSS del bottom nav después del bloque `#add-task-modal .modal{width:440px}`**

Encontrar:
```css
/* Add-task modal */
#add-task-modal .modal{width:440px}
/* Custom user picker */
```

Reemplazar con:
```css
/* Add-task modal */
#add-task-modal .modal{width:440px}
/* Mobile bottom navigation */
.mobile-bottom-nav{display:none}
.mbn-item{display:flex;flex-direction:column;align-items:center;gap:3px;padding:6px 10px;border-radius:12px;cursor:pointer;min-width:44px;min-height:44px;justify-content:center;transition:background .15s}
.mbn-item.active{background:rgba(0,136,204,.2)}
.mbn-icon{width:20px;height:20px;stroke:#64748b;stroke-width:1.8;fill:none;transition:stroke .15s;flex-shrink:0}
.mbn-item.active .mbn-icon{stroke:#38bdf8}
.mbn-label{font-size:.42rem;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.05em;line-height:1}
.mbn-item.active .mbn-label{color:#38bdf8}
/* Más sheet */
.mas-sheet-backdrop{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:350}
.mas-sheet-backdrop.open{display:block}
.mas-sheet{position:fixed;bottom:0;left:0;right:0;background:#111827;border-radius:20px 20px 0 0;border-top:1px solid #1e293b;padding:12px 20px 40px;z-index:351;transform:translateY(100%);transition:transform .3s cubic-bezier(.32,.72,0,1)}
.mas-sheet.open{transform:translateY(0)}
.mas-sheet-handle{width:40px;height:4px;background:#334155;border-radius:4px;margin:0 auto 16px}
.mas-sheet-title{font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#475569;margin-bottom:14px}
.mas-sheet-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.mas-sheet-item{display:flex;align-items:center;gap:12px;padding:14px 16px;background:#1e293b;border-radius:12px;cursor:pointer;border:1px solid #334155;min-height:44px;transition:background .12s}
.mas-sheet-item:active{background:#0c1a2e}
.mas-sheet-icon{width:22px;height:22px;stroke:#64748b;stroke-width:1.8;fill:none;flex-shrink:0}
.mas-sheet-label{font-size:.82rem;font-weight:600;color:#e2e8f0}
/* Mobile FAB */
.mobile-fab{display:none;position:fixed;bottom:88px;right:20px;width:52px;height:52px;border-radius:50%;background:#0088cc;border:none;color:#fff;align-items:center;justify-content:center;box-shadow:0 4px 16px rgba(0,136,204,.4);cursor:pointer;z-index:250;font-size:1.4rem;font-weight:300;line-height:1}
@media(max-width:768px){
  .mobile-bottom-nav{display:flex;position:fixed;bottom:16px;left:16px;right:16px;background:rgba(17,24,39,.92);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,.08);border-radius:20px;padding:8px 6px;z-index:300;justify-content:space-around;box-shadow:0 8px 32px rgba(0,0,0,.5)}
  .mobile-fab{display:flex}
  /* Client panel como bottom sheet */
  .client-panel{width:100%!important;height:92vh;top:auto!important;border-radius:20px 20px 0 0;border-left:none!important;border-top:1px solid #1e293b;transform:translateY(100%)!important;transition:transform .3s cubic-bezier(.32,.72,0,1)!important}
  .client-panel.open{transform:translateY(0)!important}
  .cp-header::before{content:'';display:block;width:40px;height:4px;background:#334155;border-radius:4px;margin:0 auto 12px}
}
/* Custom user picker */
```

- [ ] **Step 2: Verificar desktop**

En desktop: `.mobile-bottom-nav` tiene `display:none` fuera del media query — no debe verse. El CSS del media query no afecta viewports grandes.

- [ ] **Step 3: Commit**

```bash
cd C:/Users/juant/lead-gen-uy && git add dashboard.py && git commit -m "style: CSS bottom nav, mas-sheet, FAB, client panel bottom sheet mobile"
```

---

## Task 2: HTML — Bottom nav, sheet Más, FAB

**Files:**
- Modify: `dashboard.py` (HTML, agregar elementos antes de `<div class="main">` y al final del body)

- [ ] **Step 1: Agregar bottom nav y sheet Más después del sidebar-backdrop**

Encontrar:
```html
<div class="sidebar-backdrop" id="sidebar-backdrop" onclick="closeSidebar()"></div>
```

Reemplazar con:
```html
<div class="sidebar-backdrop" id="sidebar-backdrop" onclick="closeSidebar()"></div>
<nav class="mobile-bottom-nav" id="mobile-bottom-nav"></nav>
<div class="mas-sheet-backdrop" id="mas-sheet-backdrop" onclick="closeMasSheet()"></div>
<div class="mas-sheet" id="mas-sheet">
  <div class="mas-sheet-handle"></div>
  <div class="mas-sheet-title">Más secciones</div>
  <div class="mas-sheet-grid" id="mas-sheet-grid"></div>
</div>
```

- [ ] **Step 2: Agregar FAB dentro del panel de Tareas**

Encontrar dentro del tasks-panel:
```html
    <div id="tasks-summary" class="tasks-summary"></div>

    <div id="tasks-list"></div>
  </div>
```

Reemplazar con:
```html
    <div id="tasks-summary" class="tasks-summary"></div>
    <button class="mobile-fab" id="mobile-fab-task" onclick="openAddTaskModal()" aria-label="Nueva tarea">+</button>
    <div id="tasks-list"></div>
  </div>
```

- [ ] **Step 3: Verificar que el HTML carga sin errores**

Abrir el CRM — no debe haber errores en consola. En desktop: nav, sheet y FAB no visibles. El sidebar sigue funcionando.

- [ ] **Step 4: Commit**

```bash
cd C:/Users/juant/lead-gen-uy && git add dashboard.py && git commit -m "feat: HTML bottom nav, mas-sheet, FAB mobile"
```

---

## Task 3: JS — Build mobile nav + sync

**Files:**
- Modify: `dashboard.py` (JS IIFE de panel access ~línea 3322, función `showPanel` ~línea 1495)

- [ ] **Step 1: Agregar constantes y funciones del mobile nav ANTES del IIFE de panel access**

Encontrar:
```javascript
// ── Panel access control ──────────────────────────────────────────────────────
const ALL_PANELS = ['cola','seguimientos','meta','pipeline','clientes','tasks','wa','cal','metrics','activity'];
```

Reemplazar con:
```javascript
// ── Mobile navigation ─────────────────────────────────────────────────────────
const NAV_PRIORITY = ['cola','seguimientos','meta','cal','tasks','pipeline','clientes','wa','metrics','activity'];
const NAV_ICONS = {
  cola:'inbox',seguimientos:'bookmark',meta:'instagram',cal:'calendar',
  tasks:'check-square',pipeline:'trending-up',clientes:'users',
  wa:'message-circle',metrics:'bar-chart-2',activity:'clock'
};
const NAV_LABELS = {
  cola:'Cola',seguimientos:'Seguim.',meta:'Meta',cal:'Agenda',
  tasks:'Tareas',pipeline:'Pipeline',clientes:'Clientes',
  wa:'WA',metrics:'Métricas',activity:'Actividad'
};
let _mobileNavOverflow = [];

function _buildMobileNav(allowedPanels) {
  const nav = document.getElementById('mobile-bottom-nav');
  if (!nav) return;
  const ordered = NAV_PRIORITY.filter(p => allowedPanels.includes(p));
  const visible = ordered.slice(0, 5);
  _mobileNavOverflow = ordered.slice(5);
  nav.innerHTML = visible.map(p => `
    <div class="mbn-item" id="mbn-${p}" onclick="showPanel('${p}')">
      <svg class="mbn-icon" viewBox="0 0 24 24"><use href="#icon-${NAV_ICONS[p]}"></use></svg>
      <span class="mbn-label">${NAV_LABELS[p]}</span>
    </div>
  `).join('') + (_mobileNavOverflow.length ? `
    <div class="mbn-item" id="mbn-mas" onclick="openMasSheet()">
      <svg class="mbn-icon" viewBox="0 0 24 24"><use href="#icon-more-horizontal"></use></svg>
      <span class="mbn-label">Más</span>
    </div>
  ` : '');
  if (window.lucide) lucide.createIcons({nodes: [nav]});
}

function _syncMobileNav(panelName) {
  document.querySelectorAll('.mbn-item').forEach(i => i.classList.remove('active'));
  const item = document.getElementById('mbn-' + panelName);
  if (item) item.classList.add('active');
  else { const mas = document.getElementById('mbn-mas'); if (mas) mas.classList.add('active'); }
  // Show/hide FAB
  const fab = document.getElementById('mobile-fab-task');
  if (fab) fab.style.display = (panelName === 'tasks' && window.innerWidth <= 768) ? 'flex' : 'none';
}

function openMasSheet() {
  const grid = document.getElementById('mas-sheet-grid');
  if (grid) {
    grid.innerHTML = _mobileNavOverflow.map(p => `
      <div class="mas-sheet-item" onclick="closeMasSheet();showPanel('${p}')">
        <i data-lucide="${NAV_ICONS[p]}" class="mas-sheet-icon"></i>
        <span class="mas-sheet-label">${NAV_LABELS[p]}</span>
      </div>
    `).join('');
    if (window.lucide) lucide.createIcons({nodes: [grid]});
  }
  document.getElementById('mas-sheet-backdrop').classList.add('open');
  document.getElementById('mas-sheet').classList.add('open');
}

function closeMasSheet() {
  document.getElementById('mas-sheet-backdrop').classList.remove('open');
  document.getElementById('mas-sheet').classList.remove('open');
}

// ── Panel access control ──────────────────────────────────────────────────────
const ALL_PANELS = ['cola','seguimientos','meta','pipeline','clientes','tasks','wa','cal','metrics','activity'];
```

- [ ] **Step 2: Integrar `_buildMobileNav` en el IIFE de panel access**

Encontrar dentro del IIFE (después de la restricción de paneles):
```javascript
    if (access && !m.is_admin) {
      ALL_PANELS.forEach(p => {
        if (!access.includes(p)) {
          const nav = document.getElementById('nav-' + p);
          if (nav) nav.style.display = 'none';
        }
      });
      // If current panel not allowed, redirect to first allowed
      if (!access.includes(activePanel)) {
        const first = access[0];
        if (first) showPanel(first);
      }
    }
  } catch(e) {}
})();
```

Reemplazar con:
```javascript
    const allowedPanels = (access && !m.is_admin) ? access : ALL_PANELS;
    if (access && !m.is_admin) {
      ALL_PANELS.forEach(p => {
        if (!access.includes(p)) {
          const nav = document.getElementById('nav-' + p);
          if (nav) nav.style.display = 'none';
        }
      });
      if (!access.includes(activePanel)) {
        const first = access[0];
        if (first) showPanel(first);
      }
    }
    _buildMobileNav(allowedPanels);
    _syncMobileNav(activePanel);
  } catch(e) {}
})();
```

- [ ] **Step 3: Agregar `_syncMobileNav(name)` al inicio de `showPanel()`**

Encontrar:
```javascript
function showPanel(name) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById(name + '-panel').classList.add('active');
  document.getElementById('nav-' + name).classList.add('active');
  activePanel = name;
  closeSidebar();
```

Reemplazar con:
```javascript
function showPanel(name) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById(name + '-panel').classList.add('active');
  const sideNav = document.getElementById('nav-' + name);
  if (sideNav) sideNav.classList.add('active');
  activePanel = name;
  _syncMobileNav(name);
  closeSidebar();
```

- [ ] **Step 4: Verificar en mobile (DevTools, 390px)**

Abrir DevTools → Toggle device toolbar → iPhone. La barra debe aparecer con los paneles del usuario. Tocar cada ítem navega al panel correcto. El activo se ilumina azul. Tocar "···" abre el sheet "Más". En desktop (>768px): sidebar funciona igual, bottom nav no visible.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/juant/lead-gen-uy && git add dashboard.py && git commit -m "feat: JS bottom nav, syncMobileNav, más sheet, FAB visibility"
```

---

## Task 4: CSS — Lead cards en mobile

Transformar las filas de tabla en cards táctiles para Cola, Seguimientos, Meta y Clientes.

**Files:**
- Modify: `dashboard.py` (CSS, dentro del bloque `@media(max-width:768px)` ~línea 252)

- [ ] **Step 1: Agregar estilos de card dentro del bloque media query existente**

Encontrar dentro de `@media(max-width:768px)`:
```css
  .cal-event-chip{font-size:.55rem}
  .modal{width:95vw!important;max-width:95vw!important}
```

Insertar ANTES de `.modal{width:95vw...}`:
```css
  /* ── Lead cards ── */
  .table-wrap{background:transparent!important;border:none!important;border-radius:0!important;overflow:visible!important}
  .table-header{display:none!important}
  .table-row,.table-row.no-cb{
    display:flex!important;flex-direction:column!important;gap:6px;
    padding:14px 16px!important;background:#111827!important;
    border:1px solid #1e293b!important;border-radius:14px!important;
    margin-bottom:10px;grid-template-columns:none!important;
    align-items:stretch!important;
  }
  .table-row:last-child{border-bottom:1px solid #1e293b!important}
  .table-row:hover{background:#141d2e!important}
  /* Col 1 en no-cb = nombre+teléfono — siempre visible */
  .table-row.no-cb>div:nth-child(1){order:1;font-size:.9rem!important}
  /* Col 2 en no-cb = fuente/rubro — visible pequeño */
  .table-row.no-cb>div:nth-child(2){order:3;font-size:.72rem!important;color:#64748b!important}
  /* Col 3 en no-cb = estado — visible */
  .table-row.no-cb>div:nth-child(3){order:2;display:flex!important;align-items:center;gap:8px}
  /* Col 4+ en no-cb = otras — ocultar las que sean puras metadata */
  .table-row.no-cb>div:nth-child(4){display:none!important}
  /* Acciones (último div) — siempre visible, al final */
  .table-row.no-cb>div:last-child{order:10;display:flex!important;gap:8px;flex-wrap:wrap;margin-top:4px}
  .table-row.no-cb>div:last-child button,.table-row.no-cb>div:last-child a{min-height:40px!important;flex:1}
  /* Cola tiene checkbox en col 1 */
  .table-row:not(.no-cb)>div:nth-child(1){display:none!important}
  .table-row:not(.no-cb)>div:nth-child(2){order:1;font-size:.9rem!important}
  .table-row:not(.no-cb)>div:nth-child(3){display:none!important}
  .table-row:not(.no-cb)>div:nth-child(4){order:2}
  .table-row:not(.no-cb)>div:last-child{order:10;display:flex!important;gap:8px;flex-wrap:wrap;margin-top:4px}
  /* Meta tiene 7 columnas — ocultar las menos críticas */
  #meta-panel .table-row.no-cb>div:nth-child(5),
  #meta-panel .table-row.no-cb>div:nth-child(6){display:none!important}
```

- [ ] **Step 2: Verificar en mobile (DevTools 390px)**

Ir a Cola, Seguimientos, Meta y Clientes en mobile. Las filas deben verse como cards con nombre arriba, estado en segunda línea, acciones abajo. No debe haber overflow horizontal.

- [ ] **Step 3: Verificar en desktop**

En desktop todas las tablas deben verse exactamente igual que antes — grilla con columnas. Si algo cambió, el media query no está bien scoped.

- [ ] **Step 4: Commit**

```bash
cd C:/Users/juant/lead-gen-uy && git add dashboard.py && git commit -m "style: lead cards mobile — table rows como cards táctiles"
```

---

## Task 5: CSS — Tareas mobile + Calendario compacto

**Files:**
- Modify: `dashboard.py` (CSS dentro del `@media(max-width:768px)`)

- [ ] **Step 1: Agregar CSS de tareas dentro del media query**

Dentro de `@media(max-width:768px)`, agregar:
```css
  /* ── Tareas mobile ── */
  .filter-row-1{flex-direction:column!important}
  .search-input,.upick-wrap,.upick-trigger{width:100%!important}
  .filter-row-2{gap:5px}
  .pill{font-size:.68rem;padding:5px 10px}
  .task-edit-btn,.task-del-btn{min-height:36px;padding:6px 10px}
  .task-status-badge{padding:5px 12px;font-size:.74rem}
  .task-row{padding:12px 14px;border-radius:12px}
  /* ── Calendario compacto ── */
  .cal-cell{min-height:44px!important;padding:3px 2px!important}
  .cal-event-chip{font-size:0!important;width:7px!important;height:7px!important;border-radius:50%!important;padding:0!important;min-width:0!important;display:inline-block!important;margin:1px!important}
  #cal-day-events-mobile{display:block}
```

- [ ] **Step 2: Agregar HTML para lista de eventos del día en calendario**

Encontrar (línea ~1158):
```html
    <div id="cal-days" class="cal-days"><div class="cal-loading">Cargando calendario...</div></div>
```

Reemplazar con:
```html
    <div id="cal-days" class="cal-days"><div class="cal-loading">Cargando calendario...</div></div>
    <div id="cal-day-events-mobile" style="display:none;margin-top:12px;padding:0 4px"></div>
```

- [ ] **Step 3: Agregar JS para mostrar eventos del día en mobile**

La función `renderCalendar()` ya construye `eventMap` (objeto `{dateString: [events]}`) y genera celdas sin onclick. Hay que:
1. Guardar `eventMap` en `window._calEventMap` para accederlo desde el click handler
2. Agregar `data-date="${c.ds}"` a cada celda no-vacía
3. Agregar listener delegado en `daysEl` para mostrar eventos en mobile

**Sub-paso A:** En `renderCalendar()`, después de la línea:
```javascript
  (d.events || []).forEach(ev => {
    if (!eventMap[ev.date]) eventMap[ev.date] = [];
    eventMap[ev.date].push(ev);
  });
```

Agregar:
```javascript
  window._calEventMap = eventMap;
```

**Sub-paso B:** En la línea que genera las celdas no-vacías:
```javascript
      : `<div class="cal-cell${c.isToday?' today':''}">
```

Reemplazar con:
```javascript
      : `<div class="cal-cell${c.isToday?' today':''}" data-date="${c.ds}" onclick="_calCellClick(this,'${c.ds}')">
```

**Sub-paso C:** Agregar función `_calCellClick` DESPUÉS de `renderCalendar()` (alrededor de línea 2453):
```javascript
function _calCellClick(cell, dateStr) {
  if (window.innerWidth > 768) return;
  const mobileList = document.getElementById('cal-day-events-mobile');
  if (!mobileList) return;
  // Highlight selected cell
  document.querySelectorAll('.cal-cell').forEach(c => c.style.outline = '');
  cell.style.outline = '2px solid #0088cc';
  const events = (window._calEventMap || {})[dateStr] || [];
  if (!events.length) {
    mobileList.innerHTML = '<div style="color:#475569;font-size:.78rem;padding:8px 0">Sin eventos este día.</div>';
  } else {
    mobileList.innerHTML = events.map(ev => `
      <div style="background:#111827;border:1px solid #1e293b;border-radius:10px;padding:12px;margin-bottom:8px">
        <div style="font-size:.82rem;font-weight:600;color:#f1f5f9">${esc(ev.title||'')}</div>
        ${ev.time ? `<div style="font-size:.72rem;color:#0088cc;margin-top:3px">🕐 ${esc(ev.time)}</div>` : ''}
        ${ev.meeting_url ? `<a href="${esc(ev.meeting_url)}" target="_blank" style="display:inline-flex;align-items:center;gap:4px;margin-top:6px;font-size:.72rem;color:#4ade80;text-decoration:none">▶ Unirse a reunión</a>` : ''}
      </div>
    `).join('');
  }
  mobileList.scrollIntoView({behavior:'smooth',block:'nearest'});
}

- [ ] **Step 4: Verificar en mobile**

En mobile: panel Tareas con filtros apilados. FAB "+" visible. Panel Calendario con celdas pequeñas, chips como puntos. Tocar una celda muestra lista de eventos debajo.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/juant/lead-gen-uy && git add dashboard.py && git commit -m "style: tareas mobile (filtros apilados), calendario compacto con lista de eventos"
```

---

## Verificación final

- [ ] **Desktop** (>768px): Sidebar visible, tablas con columnas, modales centrados, client panel desde derecha — todo igual que antes. Abrir DevTools Network tab y verificar que no hay requests extra.
- [ ] **Mobile** (390px en DevTools): Bottom nav visible con íconos correctos para el usuario logueado.
- [ ] Navegar entre todos los paneles desde la bottom nav — cada uno carga correctamente.
- [ ] Tocar "···" abre el sheet "Más" con los paneles restantes.
- [ ] Panel Cola: leads como cards con nombre, estado y botones de acción.
- [ ] Tocar un lead abre el client panel subiendo desde abajo (92vh).
- [ ] Panel Tareas: filtros apilados, FAB "+" visible, tocar "+" abre el modal desde abajo.
- [ ] Panel Calendario: celdas compactas, tocar un día muestra eventos debajo.
- [ ] Light mode en mobile: bottom nav con fondo blanco translúcido.
- [ ] Hacer push a Railway y probar en un celular real.
