# Extracción del frontend de dashboard.py — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Regla de oro: cero cambio de comportamiento visible.** Si algo se ve o funciona distinto después de un paso, es un bug del refactor — revertir y corregir.

**Goal:** Sacar el HTML/CSS/JS embebido en `dashboard.py` a archivos propios (`static/css/`, `static/js/`, `templates/crm/`), sin cambiar el comportamiento ni el aspecto del CRM. Dejar `dashboard.py` como backend + render de template.

**Architecture:** Extracción mecánica en fases, verificable y reversible. El JS se sirve como `<script src>` clásicos (no ES modules) para preservar funciones globales; el orden de carga se mantiene (`crm-core.js` primero). Los valores que hoy inyecta Jinja en el `<script>` se mueven a un objeto `window.CRM` definido en un `<script>` inline del template (patrón bootstrap). Ver spec: `docs/superpowers/specs/2026-07-24-frontend-extraction-design.md`.

**SOLID:** el resultado cumple SOLID por su espíritu (ver sección SOLID del spec). SRP guía toda la extracción (Tasks 1–6). OCP vía **registro de paneles** (Task 6, Step 2). DIP vía **interfaces de proveedor** para deploy e IA (Task 7, companion). No inventar abstracciones para casos con una sola implementación.

**Clean Code:** todo código que se toque en este plan sigue **`docs/clean-code.md`** (convenciones adaptadas a Python/JS). Los quick wins de clean code (enums de estado, batch commits, objetos de opciones) van en la Task 8 (companion). Al mover cada pieza en el refactor, respetar esas convenciones — pero **sin reescribir todo de golpe**.

**Tech Stack:** Python/Flask, Jinja2, vanilla JS, CSS. Sin dependencias nuevas.

---

## File Map

| Archivo | Cambios |
|---|---|
| `dashboard.py` | Tasks 1–5: extraer CSS, JS, HTML; render_template; slim down |
| `static/css/crm.css` | Task 2 (nuevo): CSS extraído |
| `static/js/crm.js` | Task 3 (nuevo): JS extraído (después se parte en Task 6) |
| `templates/crm/index.html` | Task 4 (nuevo): shell HTML |
| `static/js/crm-*.js` | Task 6 (nuevos): JS por panel |

---

## Task 0: Red de seguridad

**Files:** ninguno (setup)

- [ ] **Step 1: Rama nueva**
```bash
git checkout -b refactor/frontend-extraction
```

- [ ] **Step 2: Tests verdes de base**
```bash
python -m pytest -q
```
Anotar el resultado. Debe seguir igual al final de cada task.

- [ ] **Step 3: Baseline visual**
Levantar el CRM local (`python main.py dashboard` o como se corra) y sacar una captura de cada panel: Cola, Seguimientos, Meta Ads, Proceso de venta, Clientes, ficha de cliente (abrir un lead), Tareas, WhatsApp, Calendario, Métricas, Actividad, SDR — en modo claro y oscuro. Guardarlas en `docs/refactor-baseline/`. Son la referencia de "no rompí nada".

---

## Task 1: Bootstrap de config (`window.CRM`)

**Files:** Modify: `dashboard.py`

Este es el paso más importante y hay que hacerlo ANTES de mover el JS.

- [ ] **Step 1: Encontrar el/los `<script>` del CRM en `dashboard.py`**
Localizar el string HTML grande que sirve la página principal del CRM (la ruta que renderiza el dashboard, probablemente `/` o `/dashboard`). Dentro, ubicar el bloque `<script> ... </script>` con la app JS.

- [ ] **Step 2: Listar TODAS las interpolaciones Jinja/Python dentro del `<script>`**
Buscar dentro de ese `<script>` cualquier `{{ ... }}`, `{% ... %}`, o f-string de Python (`{var}`) que inyecte datos del servidor (usuario, rol, flags, IDs, URLs, etc.). Hacer una lista.

- [ ] **Step 3: Definir `window.CRM` en un `<script>` inline**
Justo antes del `<script>` de la app, agregar un `<script>` inline que arme `window.CRM` con esos valores. Ej:
```html
<script>
  window.CRM = { user: {{ user|tojson }}, isAdmin: {{ is_admin|tojson }} /* ...etc */ };
</script>
```
Usar `|tojson` (Jinja) o `json.dumps` (f-string) para serializar bien.

- [ ] **Step 4: Reemplazar las interpolaciones dentro del JS por `window.CRM.x`**
En el cuerpo del `<script>` de la app, cambiar cada `{{ algo }}` por `window.CRM.algo`. Al terminar, el `<script>` de la app **no debe tener ninguna interpolación**.

- [ ] **Step 5: Verificar**
Levantar el CRM. Debe verse y funcionar idéntico. Revisar consola: sin errores. Probar login, que cargue la Cola, abrir una ficha de cliente.

- [ ] **Step 6: Commit**
```bash
git add dashboard.py && git commit -m "refactor(front): bootstrap window.CRM, quitar interpolacion del script principal"
```

---

## Task 2: Extraer CSS → `static/css/crm.css`

**Files:** Create: `static/css/crm.css` · Modify: `dashboard.py`

- [ ] **Step 1: Localizar el `<style> ... </style>` del CRM**
En el HTML de la página principal, ubicar el bloque `<style>` grande (el del CRM, no el de las demos).

- [ ] **Step 2: Mover el contenido a `static/css/crm.css`**
Copiar TODO lo de adentro del `<style>` a `static/css/crm.css` (sin las tags `<style>`).

- [ ] **Step 3: Reemplazar por un link**
En `dashboard.py`, reemplazar el bloque `<style>...</style>` por:
```html
<link rel="stylesheet" href="/static/css/crm.css">
```
Confirmar que Flask sirve `/static` (por defecto sí, si la carpeta es `static/`).

- [ ] **Step 4: Verificar paridad visual**
Levantar el CRM. Comparar cada panel contra las capturas de `docs/refactor-baseline/`. Deben ser idénticos (claro y oscuro). Si algo cambió, es que quedó CSS afuera — corregir.

- [ ] **Step 5: Commit**
```bash
git add static/css/crm.css dashboard.py && git commit -m "refactor(front): extraer CSS del CRM a static/css/crm.css"
```

---

## Task 3: Extraer JS → `static/js/crm.js`

**Files:** Create: `static/js/crm.js` · Modify: `dashboard.py`

- [ ] **Step 1: Mover el `<script>` de la app a `static/js/crm.js`**
Copiar todo el cuerpo del `<script>` de la app (el que ya quedó sin interpolación en Task 1) a `static/js/crm.js`.

- [ ] **Step 2: Reemplazar por un `<script src>`**
Dejar el `<script>` inline de `window.CRM` (Task 1) en su lugar, y **después** de él:
```html
<script src="/static/js/crm.js" defer></script>
```
Ojo con el orden: `window.CRM` se define primero, `crm.js` se carga después.

- [ ] **Step 3: Revisar dependencias de carga**
Si el JS usa librerías externas (Lucide, etc.) o corre algo en `DOMContentLoaded`, confirmar que `defer` no rompe el orden. Si había un `lucide.createIcons()` al final del inline, mantenerlo funcionando (puede quedar en `crm.js` o en un pequeño init).

- [ ] **Step 4: Verificar TODO**
Probar panel por panel: Cola, Seguimientos, Meta, Pipeline, Clientes, ficha de cliente (abrir, tabs, "Nueva reunión"), Tareas, WhatsApp, Calendario, Métricas, Actividad, SDR. Modo claro/oscuro. Sidebar. Sin errores en consola.

- [ ] **Step 5: Commit**
```bash
git add static/js/crm.js dashboard.py && git commit -m "refactor(front): extraer JS del CRM a static/js/crm.js"
```

---

## Task 4: Extraer el shell HTML → `templates/crm/index.html`

**Files:** Create: `templates/crm/index.html` · Modify: `dashboard.py`

- [ ] **Step 1: Mover el HTML de la página a un template**
Copiar el HTML del dashboard (el `<!DOCTYPE html>...</html>` con sidebar, contenedores de panel, modales, el link al CSS, el `<script>` de `window.CRM` y el `<script src>` de crm.js) a `templates/crm/index.html`.

- [ ] **Step 2: Convertir interpolaciones a variables de template**
Los pocos valores que se inyectan en el HTML (fuera del JS) quedan como `{{ var }}` de Jinja normales, pasados desde la vista.

- [ ] **Step 3: Renderizar con `render_template`**
En `dashboard.py`, la ruta del dashboard pasa a:
```python
return render_template('crm/index.html', user=..., is_admin=..., ...)
```
Confirmar que Flask tiene configurada la carpeta `templates/` (por defecto sí).

- [ ] **Step 4: Verificar**
El CRM se ve y funciona idéntico. Comparar contra baseline.

- [ ] **Step 5: Commit**
```bash
git add templates/crm/index.html dashboard.py && git commit -m "refactor(front): mover shell HTML a templates/crm/index.html"
```

---

## Task 5: Slim down de `dashboard.py`

**Files:** Modify: `dashboard.py`

- [ ] **Step 1: Limpiar**
Borrar de `dashboard.py` cualquier string HTML/CSS/JS muerto que haya quedado tras las extracciones. `dashboard.py` debería quedar solo con: setup de Flask, auth, registro de blueprints, y las vistas que renderean templates.

- [ ] **Step 2: Verificar tamaño y tests**
```bash
python -m pytest -q
```
Confirmar que el archivo bajó drásticamente de tamaño y que los tests pasan.

- [ ] **Step 3: Commit**
```bash
git add dashboard.py && git commit -m "refactor(front): limpiar dashboard.py post-extraccion"
```

---

## Task 6: Partir `crm.js` por panel (incremental)

**Files:** Create: `static/js/crm-core.js`, `static/js/crm-cola.js`, ... · Modify: `templates/crm/index.html`

Hacer **un panel por vez**, verificando entre cada uno. Empezar por lo compartido y por Cola (el más usado).

- [ ] **Step 1: `crm-core.js`**
Mover a `crm-core.js` los helpers compartidos: `showPanel`, helpers de `fetch`, `esc`, theme (`toggleTheme`), sidebar (`toggleSidebar`/`closeSidebar`), bootstrap/init, y cualquier utilidad usada por varios paneles. En `index.html`, cargar `crm-core.js` antes que el resto. Verificar que todo sigue andando (todas las funciones globales siguen existiendo). **Mantener `crm-core.js` chico** (SRP/ISP): solo plumbing compartido, no lógica de paneles.

- [ ] **Step 2: Registro de paneles (OCP)**
En `crm-core.js`, introducir un registro: `const PANELS = {}` y una función `registerPanel(id, {label, icon, init})`. Reescribir `showPanel(id)` para que use el registro (mostrar/ocultar contenedores, marcar el nav activo, llamar `init` la primera vez) en vez de un switch hardcodeado. Cada `crm-<panel>.js` (Steps 3+) se auto-registra con `registerPanel('cola', {...})`. **Comportamiento idéntico**: `showPanel('cola')` hace exactamente lo mismo que hoy; solo cambia cómo está cableado. Así, agregar un panel nuevo = un archivo que se registra, sin tocar el core.

- [ ] **Step 3: `crm-cola.js`**
Mover las funciones del panel Cola (render de la tabla, `_scoreBadge`, `_socialIcons`, acciones de llamada, etc.) a `crm-cola.js`. Cargar después de core. Verificar la Cola.

- [ ] **Step 4: `crm-client-panel.js`**
Mover la ficha del cliente (`openClientPanel`, `closeClientPanel`, tabs `_cpSwitchTab`, `_cp*`, incluido el `_cpOpenNewMeeting` ya arreglado). Verificar abrir ficha, tabs, "Nueva reunión".

- [ ] **Step 5: Un archivo por panel restante**
Repetir para: `crm-seguimientos.js`, `crm-meta.js`, `crm-pipeline.js`, `crm-clientes.js`, `crm-tasks.js`, `crm-wa.js`, `crm-calendar.js`, `crm-metrics.js`, `crm-activity.js`, `crm-sdr.js`. Cada uno se auto-registra con `registerPanel(...)`. **Un commit por panel**, verificando ese panel antes de seguir.

- [ ] **Step 6: Verificación final**
Smoke test completo de todos los paneles + modo claro/oscuro + `pytest`. Comparar contra baseline.

---

## Task 7 (companion, backend — DIP): interfaces de proveedor

**Files:** Create: `services/providers/` · Modify: `deployer.py`, `demo_ai.py`, `budget_ai.py`

> Esta task es **independiente del frontend** y puede ejecutarse antes, después o en paralelo. Se incluye porque el objetivo es cumplir SOLID; si preferís, se puede sacar a su propio plan.

- [ ] **Step 1: Interfaz `DemoHost` (deploy)**
Definir una interfaz (ABC o `typing.Protocol`) `DemoHost` con `deploy(files: dict, name: str) -> str` (devuelve la URL). Implementar `VercelHost` (lo que hoy hace `deployer.py`) y dejar preparado `CloudflareHost`. `deployer.py`/los callers dependen de `DemoHost`, no de Vercel directo. Elegir la impl por env (`DEMO_HOST=vercel|cloudflare`).

- [ ] **Step 2: Interfaz `LLMClient` (IA)**
Definir `LLMClient` con `complete(system: str, user: str) -> str`. Implementar `AnthropicClient` (lo que hoy usan `demo_ai.py` y `budget_ai.py`). Los módulos de IA reciben un `LLMClient` (inyección) en vez de instanciar el SDK adentro. Cambiar de modelo/proveedor = nueva impl.

- [ ] **Step 3: LSP — mismos contratos**
Test simple: todas las impls de `DemoHost` devuelven una URL string válida; todas las de `LLMClient` devuelven texto no vacío. Verificar que los callers no distinguen la impl.

- [ ] **Step 4: Commit**
```bash
git add services/providers deployer.py demo_ai.py budget_ai.py && git commit -m "refactor(solid): DIP - interfaces DemoHost y LLMClient con impls intercambiables"
```

> **Fuera de alcance (plan futuro):** partir `database.py` en repositorios por entidad (leads/users/tasks) — es el mayor win de SRP en el backend, pero es grande y merece su propio plan.

---

## Task 8 (companion, Clean Code — quick wins)

**Files:** varios (dirigido) · Guía: `docs/clean-code.md`

> Acotada e independiente. NO reescribir todo — solo los ítems de mayor valor y menor riesgo. Un commit por ítem.

- [ ] **Step 1: Enums de estado (matar strings mágicos)**
Buscar comparaciones con strings de estado repetidas: `crm_status`, task status (`'todo'/'in_progress'/'done'`), roles, `source`. Definir enums/constantes (Python `Enum`, JS `Object.freeze`) y reemplazar los literales por la constante. Verificar que el comportamiento no cambia.

- [ ] **Step 2: `commit` fuera de loops**
Grep `commit()` dentro de `for`/`while` en `database.py`, `scraper.py`, `import_meta_leads.py`. Mover el commit afuera del loop (acumular, commitear una vez). Correr los tests de DB.

- [ ] **Step 3: Objetos de opciones en funciones JS con muchos argumentos**
Identificar funciones JS con 4+ argumentos posicionales (ej: `openAddTaskModal`, handlers de modales). Refactorizar a un objeto de opciones. Actualizar los call sites. Verificar el panel afectado.

- [ ] **Step 4: Guard clauses en el código tocado**
En las funciones que se muevan/toquen, aplicar fail-fast (validar y `return`/`raise` temprano) para reducir anidamiento. Sin cambiar comportamiento.

- [ ] **Step 5: Commit por ítem**
```bash
git commit -m "refactor(clean): <item> segun docs/clean-code.md"
```

---

## Notas para quien ejecute

- Si un paso rompe algo y no es obvio por qué, **revertir ese commit** y hacerlo en pedazos más chicos.
- No mezclar mejoras de UX ni fixes de features en este refactor. Si aparece un bug, anotarlo aparte.
- Cache del browser: si tras extraer CSS/JS el navegador sirve una versión vieja, hard-refresh (Ctrl+Shift+R) o agregar `?v=<hash>` al `href`/`src`.
- La Task 7 del spec (mover rutas inline a blueprints) NO está en este plan — va en uno aparte cuando este termine.
