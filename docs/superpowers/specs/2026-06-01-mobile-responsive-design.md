# Spec: CRM Mobile-First Responsive Design

**Fecha:** 2026-06-01  
**Estado:** Aprobado

---

## Objetivo

Hacer que el CRM se sienta como una app nativa en celular (≤768px) y como una app de escritorio en computadora (>768px). No es un ajuste de layout — es dos experiencias distintas en el mismo codebase.

**Principio:** Desktop queda exactamente igual. Mobile es una experiencia nueva.

---

## Stack técnico

Todos los cambios en `dashboard.py`:
- CSS en el `<style>` block (media queries `@media(max-width:768px)`)
- HTML: agregar bottom nav, sheet "Más", modificar client panel
- JS mínimo: toggle sheet "Más", cambiar animación client panel en mobile

---

## 1. Navegación — Bottom Nav

### Desktop (sin cambios)
Sidebar izquierdo de 228px, exactamente como está.

### Mobile
- Sidebar oculto (ya implementado)
- **Bottom nav flotante** fijo en la parte inferior, estilo pill con blur (Opción A del brainstorm)
- Hasta **5 ítems** visibles, definidos por una lista de prioridad fija
- **6to ícono "···"** abre el panel "Más"

### Lista de prioridad para el bottom nav
```
Cola → Seguimientos → Meta Ads → Calendario → Tareas → Pipeline → Clientes → WhatsApp → Métricas → Actividad
```
Se muestran los primeros 5 paneles a los que el usuario tiene acceso. El resto va en "Más".

### Estructura HTML del bottom nav
```html
<nav class="mobile-bottom-nav" id="mobile-bottom-nav">
  <!-- generado por JS según pages del usuario -->
</nav>
```

### CSS
```css
.mobile-bottom-nav {
  display: none; /* oculto por defecto (desktop) */
}
@media(max-width:768px) {
  .mobile-bottom-nav {
    display: flex;
    position: fixed;
    bottom: 16px;
    left: 16px;
    right: 16px;
    background: rgba(17,24,39,0.92);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 20px;
    padding: 8px 6px;
    z-index: 300;
    justify-content: space-around;
    box-shadow: 0 8px 32px rgba(0,0,0,0.5);
  }
}
```

### Ítem del nav
Cada ítem: ícono Lucide + label debajo. El activo tiene fondo `rgba(0,136,204,0.2)` y label azul.

```css
.mbn-item { display:flex;flex-direction:column;align-items:center;gap:3px;padding:6px 12px;border-radius:12px;cursor:pointer;min-width:48px;min-height:44px;justify-content:center; }
.mbn-item.active { background:rgba(0,136,204,0.2); }
.mbn-icon { width:20px;height:20px;stroke:#64748b;transition:stroke .15s; }
.mbn-item.active .mbn-icon { stroke:#38bdf8; }
.mbn-label { font-size:.45rem;font-weight:600;color:#475569;text-transform:uppercase;letter-spacing:.05em; }
.mbn-item.active .mbn-label { color:#38bdf8; }
```

### JS — Generar bottom nav
En la función que procesa `/api/me`, después de aplicar las restricciones de paneles, se genera el bottom nav:

```javascript
const NAV_PRIORITY = ['cola','seguimientos','meta','cal','tasks','pipeline','clientes','wa','metrics','activity'];
const NAV_ICONS = {
  cola:'inbox', seguimientos:'bookmark', meta:'instagram', cal:'calendar',
  tasks:'check-square', pipeline:'trending-up', clientes:'users',
  wa:'message-circle', metrics:'bar-chart-2', activity:'clock'
};
const NAV_LABELS = {
  cola:'Cola', seguimientos:'Seguim.', meta:'Meta', cal:'Agenda',
  tasks:'Tareas', pipeline:'Pipeline', clientes:'Clientes',
  wa:'WhatsApp', metrics:'Métricas', activity:'Actividad'
};

function _buildMobileNav(allowedPanels) {
  const nav = document.getElementById('mobile-bottom-nav');
  if (!nav) return;
  const priority = NAV_PRIORITY.filter(p => allowedPanels.includes(p));
  const visible = priority.slice(0, 5);
  const overflow = priority.slice(5);
  
  nav.innerHTML = visible.map(p => `
    <div class="mbn-item" id="mbn-${p}" onclick="showPanel('${p}')">
      <i data-lucide="${NAV_ICONS[p]}" class="mbn-icon"></i>
      <span class="mbn-label">${NAV_LABELS[p]}</span>
    </div>
  `).join('') + (overflow.length ? `
    <div class="mbn-item" id="mbn-mas" onclick="openMasSheet()">
      <i data-lucide="more-horizontal" class="mbn-icon"></i>
      <span class="mbn-label">Más</span>
    </div>
  ` : '');
  
  if (window.lucide) lucide.createIcons();
  _mobileNavOverflow = overflow;
}

// Sincronizar estado activo del bottom nav con el panel visible
function _syncMobileNav(panelName) {
  document.querySelectorAll('.mbn-item').forEach(i => i.classList.remove('active'));
  const item = document.getElementById('mbn-' + panelName);
  if (item) item.classList.add('active');
  else { const mas = document.getElementById('mbn-mas'); if (mas) mas.classList.add('active'); }
}
```

Llamar `_syncMobileNav(name)` al inicio de `showPanel(name)`.

---

## 2. Panel "Más"

### Descripción
Bottom sheet que sube desde abajo al tocar "···". Muestra los paneles que no cupieron en la bottom nav, en grilla de 2 columnas.

### HTML
```html
<div class="mas-sheet-backdrop" id="mas-sheet-backdrop" onclick="closeMasSheet()"></div>
<div class="mas-sheet" id="mas-sheet">
  <div class="mas-sheet-handle"></div>
  <div class="mas-sheet-title">Más secciones</div>
  <div class="mas-sheet-grid" id="mas-sheet-grid"></div>
</div>
```

### CSS
```css
.mas-sheet-backdrop { display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:350; }
.mas-sheet-backdrop.open { display:block; }
.mas-sheet { position:fixed;bottom:0;left:0;right:0;background:#111827;border-radius:20px 20px 0 0;border-top:1px solid #1e293b;padding:12px 20px 32px;z-index:351;transform:translateY(100%);transition:transform .3s cubic-bezier(.32,.72,0,1); }
.mas-sheet.open { transform:translateY(0); }
.mas-sheet-handle { width:40px;height:4px;background:#334155;border-radius:4px;margin:0 auto 16px; }
.mas-sheet-title { font-size:.75rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#475569;margin-bottom:16px; }
.mas-sheet-grid { display:grid;grid-template-columns:1fr 1fr;gap:10px; }
.mas-sheet-item { display:flex;align-items:center;gap:12px;padding:14px 16px;background:#1e293b;border-radius:12px;cursor:pointer;border:1px solid #334155; }
.mas-sheet-item:active { background:#0c1a2e; }
.mas-sheet-icon { width:22px;height:22px;stroke:#64748b; }
.mas-sheet-label { font-size:.82rem;font-weight:600;color:#e2e8f0; }
```

### JS
```javascript
let _mobileNavOverflow = [];

function openMasSheet() {
  const grid = document.getElementById('mas-sheet-grid');
  if (grid) {
    grid.innerHTML = _mobileNavOverflow.map(p => `
      <div class="mas-sheet-item" onclick="closeMasSheet();showPanel('${p}')">
        <i data-lucide="${NAV_ICONS[p]}" class="mas-sheet-icon"></i>
        <span class="mas-sheet-label">${NAV_LABELS[p]}</span>
      </div>
    `).join('');
    if (window.lucide) lucide.createIcons();
  }
  document.getElementById('mas-sheet-backdrop').classList.add('open');
  document.getElementById('mas-sheet').classList.add('open');
}

function closeMasSheet() {
  document.getElementById('mas-sheet-backdrop').classList.remove('open');
  document.getElementById('mas-sheet').classList.remove('open');
}
```

---

## 3. Paneles de leads — Cards en mobile

Los paneles Cola, Seguimientos, Meta Ads y Clientes usan `.table-row` con grid de columnas fijas. En mobile se transforman en cards con flexbox.

### CSS — Transformar filas en cards
```css
@media(max-width:768px) {
  .table-wrap { background:transparent;border:none;border-radius:0; }
  .table-header { display:none; }
  .table-row {
    display:flex;flex-direction:column;gap:0;padding:14px 16px;
    background:#111827;border:1px solid #1e293b;border-radius:14px;
    margin-bottom:10px;
  }
  /* Fila 1: nombre + status badge */
  .table-row > div:nth-child(1) { order:1;margin-bottom:6px; }
  /* Ocultar columnas menos relevantes */
  .table-row > div:nth-child(3),
  .table-row > div:nth-child(4) { display:none; }
  /* Checkbox */
  .table-row > div:nth-child(1).cb-col { display:none; }
}
```

**Nota de implementación:** Cada panel tiene columnas distintas. El implementador debe tratar cada panel por separado — Cola, Seguimientos, Meta y Clientes tienen estructuras de grid diferentes. La estrategia es: convertir `.table-row` de grid a flex-column, mostrar nombre+estado+teléfono+acciones, ocultar el resto. Los selectores `nth-child` son frágiles; preferir clases específicas por panel (`.no-cb`, etc.) que ya existen en el código.

### Botones de acción en mobile
```css
@media(max-width:768px) {
  .contact-btn,.pitch-btn,.mail-btn,.run-btn {
    min-height:44px;padding:10px 16px;font-size:.82rem;
  }
}
```

---

## 4. Panel de cliente — Bottom Sheet en mobile

### Desktop (sin cambios)
Desliza desde la derecha (580px de ancho), igual que ahora.

### Mobile
Sube desde abajo, pantalla completa. Cambiar la animación via CSS override:

```css
@media(max-width:768px) {
  .client-panel {
    width:100% !important;
    height:92vh;
    top:auto;
    border-radius:20px 20px 0 0;
    border-left:none;
    border-top:1px solid #1e293b;
    transform:translateY(100%) !important;
    transition:transform .3s cubic-bezier(.32,.72,0,1) !important;
  }
  .client-panel.open {
    transform:translateY(0) !important;
  }
  /* Handle para deslizar hacia abajo */
  .cp-header::before {
    content:'';display:block;width:40px;height:4px;
    background:#334155;border-radius:4px;margin:0 auto 12px;
  }
}
```

El backdrop que ya existe (`#cp-backdrop`) cubre el fondo igual que en desktop.

---

## 5. Calendario — Compacto en mobile

### Cambios CSS
```css
@media(max-width:768px) {
  /* Celdas más pequeñas */
  .cal-cell { min-height:44px;padding:4px 2px; }
  .cal-grid-header { font-size:.6rem;padding:6px 2px; }
  /* Ocultar texto de eventos en celdas — solo punto de color */
  .cal-event-chip { font-size:0;width:8px;height:8px;border-radius:50%;padding:0;min-width:0; }
  /* Lista de eventos del día seleccionado debajo de la grilla */
  #cal-day-events { display:block; } /* elemento a agregar */
}
```

### HTML — Lista de eventos del día
Agregar debajo del `#cal-grid`:
```html
<div id="cal-day-events" style="display:none;margin-top:16px"></div>
```

### JS — Al hacer click en una celda en mobile
En la función que maneja click de celda, en mobile mostrar los eventos del día en `#cal-day-events` como lista. En desktop comportamiento actual.

---

## 6. Tareas — Mobile

```css
@media(max-width:768px) {
  /* Filter bar apila en columna */
  .filter-row-1 { flex-direction:column; }
  .search-input { width:100%; }
  .upick-wrap { width:100%; }
  .upick-trigger { width:100%; }
  /* Padding de página reducido (ya cubierto por .main) */
  /* Task cards: más espacio para touch */
  .task-edit-btn,.task-del-btn { min-height:36px;padding:6px 10px; }
  .task-status-badge { padding:5px 12px;font-size:.75rem; }
}
```

**FAB para nueva tarea en mobile:**
```html
<button class="mobile-fab" id="mobile-fab-task" onclick="openAddTaskModal()" style="display:none">
  <i data-lucide="plus"></i>
</button>
```
Solo visible en mobile cuando el panel Tareas está activo.

```css
.mobile-fab { position:fixed;bottom:88px;right:20px;width:52px;height:52px;border-radius:50%;background:#0088cc;border:none;color:#fff;display:flex;align-items:center;justify-content:center;box-shadow:0 4px 16px rgba(0,136,204,.4);cursor:pointer;z-index:200; }
```

---

## 7. Estilos globales mobile

### Padding y espacio
```css
@media(max-width:768px) {
  .main { padding:14px 14px 90px; } /* 90px de padding-bottom para no quedar bajo el bottom nav */
  .page-header { margin-bottom:16px; }
  .page-header h1 { font-size:1.1rem; }
}
```

### Modales → Bottom sheets
Los modales existentes ya van a 95vw en mobile. Se agrega:
```css
@media(max-width:768px) {
  .modal-overlay { align-items:flex-end; }
  .modal {
    border-radius:20px 20px 0 0 !important;
    width:100% !important;
    max-width:100% !important;
    max-height:92vh;
    overflow-y:auto;
  }
}
```

### Touch targets
```css
@media(max-width:768px) {
  .filter-btn,.btn-cancel,.btn-confirm { min-height:44px; }
  .nav-item { display:none; } /* sidebar nav oculto en mobile */
}
```

### Scroll suave en kanban y listas
```css
@media(max-width:768px) {
  .kanban-board { -webkit-overflow-scrolling:touch;scroll-snap-type:x mandatory; }
  .kanban-col { scroll-snap-align:start; }
}
```

---

## 8. Cambios por zona en `dashboard.py`

| Zona | Cambio |
|---|---|
| CSS `@media(max-width:768px)` | Expandir con todos los estilos mobile |
| HTML `<nav class="mobile-bottom-nav">` | Agregar antes del `<div class="main">` |
| HTML `<div class="mas-sheet-*">` | Agregar al final del body |
| HTML `<button class="mobile-fab">` | Agregar en la sección de tareas |
| HTML `<div id="cal-day-events">` | Agregar debajo del calendario |
| JS `showPanel()` | Agregar `_syncMobileNav(name)` |
| JS `api/me` handler | Agregar `_buildMobileNav(allowedPanels)` |
| JS nuevas funciones | `_buildMobileNav`, `_syncMobileNav`, `openMasSheet`, `closeMasSheet` |

---

## 9. Criterios de aceptación

### Mobile (≤768px)
- [ ] Bottom nav flotante visible con íconos y labels
- [ ] Solo aparecen los paneles accesibles para el usuario (hasta 5)
- [ ] Panel "Más" sube como sheet al tocar "···"
- [ ] Filas de leads se muestran como cards táctiles
- [ ] Panel de cliente sube desde abajo (bottom sheet), 92vh
- [ ] Modales abren como bottom sheets desde abajo
- [ ] Calendario muestra celdas compactas con puntos de color
- [ ] Al tocar día del calendario aparece lista de eventos debajo
- [ ] Barra de filtros de tareas se apila verticalmente
- [ ] Botón "+" flotante visible en panel Tareas
- [ ] Todos los botones interactivos tienen mínimo 44px de altura
- [ ] `padding-bottom` del main evita que el contenido quede bajo la barra
- [ ] Kanban tiene scroll táctil fluido con snap

### Desktop (>768px)
- [ ] Sidebar izquierdo visible, sin cambios
- [ ] Bottom nav NO visible
- [ ] Tablas con grillas de columnas, sin cambios
- [ ] Panel de cliente desliza desde la derecha, sin cambios
- [ ] Modales centrados, sin cambios
- [ ] Todo el comportamiento actual intacto
