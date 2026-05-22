# Fase B: Paginación y Performance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar índices de DB, paginación server-side en /api/leads, CSRF en el login y UI de paginación en el panel de leads.

**Architecture:** Fixes quirúrgicos en archivos existentes. La paginación es backwards-compatible: sin `?page`, la API sigue devolviendo array raw (usado por Kanban y Tareas). Con `?page=N`, devuelve `{items, total, pages, page}`. El filtro de crm_status se mueve de Python a SQL. Category y search siguen en Python.

**Tech Stack:** Python/Flask, SQLite, secrets (stdlib), vanilla JS

---

## File Map

| Archivo | Cambios |
|---|---|
| `database.py` | Task 1: índices; Task 2: param crm_status en get_all_businesses |
| `routes/leads.py` | Task 2: pasar crm_status a la función; Task 3: paginación |
| `dashboard.py` | Task 4: CSRF en login; Task 5: UI paginación |

---

### Task 1: Índices de base de datos

**Files:**
- Modify: `database.py` — función `init_db()`

- [ ] **Step 1: Localizar el bloque de índices existente**

En `database.py`, buscar el bloque que dice:
```python
        # Idempotent unique index — prevents duplicate leads from concurrent bot pushes
        try:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_businesses_phone ON businesses(phone)")
            conn.commit()
        except Exception:
            pass
```
Está justo antes del `finally: conn.close()` en `init_db()`.

- [ ] **Step 2: Agregar los tres índices nuevos**

Reemplazar ese bloque con:
```python
        # Idempotent unique index — prevents duplicate leads from concurrent bot pushes
        try:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_businesses_phone ON businesses(phone)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_businesses_crm_status ON businesses(crm_status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_businesses_score ON businesses(score)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_businesses_category ON businesses(category)")
            conn.commit()
        except Exception:
            pass
```

- [ ] **Step 3: Verificar que los índices se crean**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "
from database import init_db
init_db('test_idx.db')
import sqlite3
con = sqlite3.connect('test_idx.db')
rows = con.execute(\"SELECT name FROM sqlite_master WHERE type='index'\").fetchall()
for r in rows: print(r[0])
con.close()
import os; os.remove('test_idx.db')
"
```

Esperado (entre otros):
```
idx_businesses_phone
idx_businesses_crm_status
idx_businesses_score
idx_businesses_category
```

- [ ] **Step 4: Commit**

```bash
git add database.py
git commit -m "perf: add crm_status, score, category indexes to businesses table"
```

---

### Task 2: Filtrado de crm_status en SQL

**Files:**
- Modify: `database.py` — función `get_all_businesses` (línea ~287)
- Modify: `routes/leads.py` — función `api_leads` (línea ~86)

- [ ] **Step 1: Actualizar get_all_businesses en database.py**

Encontrar:
```python
def get_all_businesses(db_path: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute("SELECT * FROM businesses ORDER BY CASE WHEN score IS NULL THEN 1 ELSE 0 END, score DESC, scraped_at DESC")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()
```

Reemplazar con:
```python
def get_all_businesses(db_path: str, crm_status: str | None = None) -> list[dict]:
    conn = _connect(db_path)
    try:
        if crm_status == "sin_contactar":
            where = "WHERE (crm_status IS NULL OR crm_status = 'sin_contactar')"
            params: list = []
        elif crm_status:
            where = "WHERE crm_status = ?"
            params = [crm_status]
        else:
            where = ""
            params = []
        cursor = conn.execute(
            f"SELECT * FROM businesses {where} ORDER BY CASE WHEN score IS NULL THEN 1 ELSE 0 END, score DESC, scraped_at DESC",
            params,
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()
```

- [ ] **Step 2: Actualizar api_leads en routes/leads.py**

Encontrar:
```python
@leads_bp.route("/api/leads")
def api_leads():
    businesses = get_all_businesses(_db())
    crm_status = request.args.get("crm_status")
    category = request.args.get("category")
    search = (request.args.get("search") or "").lower()
    if crm_status:
        businesses = [b for b in businesses if (b.get("crm_status") or "sin_contactar") == crm_status]
    if category:
        businesses = [b for b in businesses if _normalize_category(b.get("category") or "") == category]
    if search:
        businesses = [b for b in businesses if search in (b.get("name") or "").lower()]
    return jsonify(businesses)
```

Reemplazar con:
```python
@leads_bp.route("/api/leads")
def api_leads():
    crm_status = request.args.get("crm_status")
    category = request.args.get("category")
    search = (request.args.get("search") or "").lower()
    businesses = get_all_businesses(_db(), crm_status=crm_status)
    if category:
        businesses = [b for b in businesses if _normalize_category(b.get("category") or "") == category]
    if search:
        businesses = [b for b in businesses if search in (b.get("name") or "").lower()]
    return jsonify(businesses)
```

- [ ] **Step 3: Verificar sintaxis**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "from database import get_all_businesses; from routes.leads import leads_bp; print('OK')"
```

Esperado: `OK`

- [ ] **Step 4: Commit**

```bash
git add database.py routes/leads.py
git commit -m "perf: move crm_status filter to SQL in get_all_businesses"
```

---

### Task 3: Paginación server-side en /api/leads

**Files:**
- Modify: `routes/leads.py` — función `api_leads`

- [ ] **Step 1: Agregar paginación en Python en api_leads**

Tomar la función que quedó en Task 2 y reemplazarla con:
```python
_PER_PAGE = 50

@leads_bp.route("/api/leads")
def api_leads():
    crm_status = request.args.get("crm_status")
    category = request.args.get("category")
    search = (request.args.get("search") or "").lower()
    page_str = request.args.get("page")
    businesses = get_all_businesses(_db(), crm_status=crm_status)
    if category:
        businesses = [b for b in businesses if _normalize_category(b.get("category") or "") == category]
    if search:
        businesses = [b for b in businesses if search in (b.get("name") or "").lower()]
    if page_str is not None:
        try:
            page = max(1, int(page_str))
        except ValueError:
            page = 1
        total = len(businesses)
        pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
        page = min(page, pages)
        offset = (page - 1) * _PER_PAGE
        return jsonify({"items": businesses[offset:offset + _PER_PAGE], "total": total, "pages": pages, "page": page})
    return jsonify(businesses)
```

Nota: `_PER_PAGE = 50` va como constante al nivel del módulo, justo antes o después de `_VALID_CRM_STATES`.

- [ ] **Step 2: Verificar sintaxis**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "from routes.leads import leads_bp; print('OK')"
```

Esperado: `OK`

- [ ] **Step 3: Commit**

```bash
git add routes/leads.py
git commit -m "feat: server-side pagination in /api/leads (page param → {items, total, pages})"
```

---

### Task 4: CSRF en login form

**Files:**
- Modify: `dashboard.py` — constante `LOGIN_HTML` y función `login()`

- [ ] **Step 1: Agregar campo oculto en LOGIN_HTML**

En `dashboard.py`, encontrar el form en `LOGIN_HTML`:
```html
  <form method="POST">
    <label>Contraseña</label>
    <input type="password" name="password" autofocus placeholder="Ingresá la contraseña del equipo">
    <button type="submit">Entrar</button>
  </form>
```

Reemplazar con:
```html
  <form method="POST">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <label>Contraseña</label>
    <input type="password" name="password" autofocus placeholder="Ingresá la contraseña del equipo">
    <button type="submit">Entrar</button>
  </form>
```

- [ ] **Step 2: Actualizar la función login() para generar y validar el token**

Encontrar la función `login()` en `dashboard.py` (alrededor de la línea 2415):
```python
    def login():
        error = None
        if request.method == "POST":
            password = request.form.get("password", "")
            expected = os.environ.get("DASHBOARD_PASSWORD", "")
            if not expected:
                session["logged_in"] = True
                return redirect(url_for("index"))
            if expected and secrets.compare_digest(password, expected):
                session["logged_in"] = True
                return redirect(url_for("index"))
            error = "Contraseña incorrecta"
        return render_template_string(LOGIN_HTML, error=error)
```

Reemplazar con:
```python
    def login():
        error = None
        if request.method == "POST":
            form_csrf = request.form.get("csrf_token", "")
            expected_csrf = session.pop("csrf_token", "")
            if not expected_csrf or not secrets.compare_digest(form_csrf, expected_csrf):
                error = "Token inválido. Recargá la página."
            else:
                password = request.form.get("password", "")
                expected = os.environ.get("DASHBOARD_PASSWORD", "")
                if not expected:
                    session["logged_in"] = True
                    return redirect(url_for("index"))
                if secrets.compare_digest(password, expected):
                    session["logged_in"] = True
                    return redirect(url_for("index"))
                error = "Contraseña incorrecta"
        csrf_token = secrets.token_hex(32)
        session["csrf_token"] = csrf_token
        return render_template_string(LOGIN_HTML, error=error, csrf_token=csrf_token)
```

- [ ] **Step 3: Verificar sintaxis**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "import dashboard; print('OK')"
```

Esperado: `OK`

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "fix: add CSRF token to login form"
```

---

### Task 5: UI de paginación en pestaña de leads

**Files:**
- Modify: `dashboard.py` — HTML (después de `table-body`), JS variables y funciones

- [ ] **Step 1: Agregar div de paginación en el HTML**

En `dashboard.py`, encontrar:
```html
      <div id="table-body"></div>
    </div>
  </div>
```

Reemplazar con:
```html
      <div id="table-body"></div>
      <div id="leads-pagination" style="display:none;justify-content:center;align-items:center;gap:12px;padding:16px 0;font-size:.85rem;color:#94a3b8"></div>
    </div>
  </div>
```

- [ ] **Step 2: Agregar variables de paginación**

En `dashboard.py`, encontrar:
```javascript
let currentCrm = '';
let currentCategory = '';
let currentSearch = '';
```

Reemplazar con:
```javascript
let currentCrm = '';
let currentCategory = '';
let currentSearch = '';
let currentPage = 1;
let totalPages = 1;
```

- [ ] **Step 3: Agregar funciones _updatePagination y gotoPage**

En `dashboard.py`, justo ANTES de `async function loadLeads() {`, insertar:

```javascript
function _updatePagination() {
  const el = document.getElementById('leads-pagination');
  if (!el) return;
  if (totalPages <= 1) { el.style.display = 'none'; return; }
  el.style.display = 'flex';
  el.innerHTML =
    `<button onclick="gotoPage(${currentPage - 1})" ${currentPage <= 1 ? 'disabled' : ''} style="background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:6px 14px;border-radius:6px;cursor:pointer">← Anterior</button>` +
    `<span>Página ${currentPage} de ${totalPages}</span>` +
    `<button onclick="gotoPage(${currentPage + 1})" ${currentPage >= totalPages ? 'disabled' : ''} style="background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:6px 14px;border-radius:6px;cursor:pointer">Siguiente →</button>`;
}

function gotoPage(p) {
  if (p < 1 || p > totalPages) return;
  currentPage = p;
  loadLeads();
}

```

- [ ] **Step 4: Actualizar loadLeads() para usar paginación**

Encontrar el cuerpo de `loadLeads()`:
```javascript
async function loadLeads() {
  const body2 = document.getElementById('table-body');
  try {
  const params = new URLSearchParams();
  if (currentCrm) params.set('crm_status', currentCrm);
  if (currentCategory) params.set('category', currentCategory);
  if (currentSearch) params.set('search', currentSearch);
  const r = await fetch('/api/leads?' + params);
  if (!r.ok) { body2.innerHTML = `<div style="color:#f87171;padding:16px">API error ${r.status}</div>`; return; }
  const leads = await r.json();
  if (!Array.isArray(leads)) { body2.innerHTML = `<div style="color:#f87171;padding:16px">Respuesta inesperada: ${JSON.stringify(leads).slice(0,100)}</div>`; return; }
  _allLeads = leads;
```

Reemplazar con:
```javascript
async function loadLeads() {
  const body2 = document.getElementById('table-body');
  try {
  const params = new URLSearchParams();
  if (currentCrm) params.set('crm_status', currentCrm);
  if (currentCategory) params.set('category', currentCategory);
  if (currentSearch) params.set('search', currentSearch);
  params.set('page', currentPage);
  const r = await fetch('/api/leads?' + params);
  if (!r.ok) { body2.innerHTML = `<div style="color:#f87171;padding:16px">API error ${r.status}</div>`; return; }
  const data = await r.json();
  const leads = Array.isArray(data) ? data : (data.items || []);
  if (data.pages !== undefined) { totalPages = data.pages; currentPage = data.page || currentPage; }
  if (!Array.isArray(leads)) { body2.innerHTML = `<div style="color:#f87171;padding:16px">Respuesta inesperada: ${JSON.stringify(data).slice(0,100)}</div>`; return; }
  _allLeads = leads;
```

- [ ] **Step 5: Llamar _updatePagination al final de loadLeads()**

Encontrar el cierre de la función loadLeads, justo ANTES del `} catch(e)`:
```javascript
  body.innerHTML = leads.map(b => {
    ...
  }).join('');
  } catch(e) { document.getElementById('table-body').innerHTML = ...
```

Agregar `_updatePagination();` justo después del `.join('');`:
```javascript
  body.innerHTML = leads.map(b => {
    ...
  }).join('');
  _updatePagination();
  } catch(e) { document.getElementById('table-body').innerHTML = ...
```

- [ ] **Step 6: Resetear currentPage a 1 cuando cambian los filtros**

Encontrar:
```javascript
document.querySelectorAll('.filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentCrm = btn.dataset.crm;
    loadLeads();
  });
});
document.getElementById('category-filter').addEventListener('change', e => { currentCategory = e.target.value; loadLeads(); });
let searchTimeout;
document.getElementById('search-input').addEventListener('input', e => {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(() => { currentSearch = e.target.value; loadLeads(); }, 300);
});
```

Reemplazar con:
```javascript
document.querySelectorAll('.filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentCrm = btn.dataset.crm;
    currentPage = 1;
    loadLeads();
  });
});
document.getElementById('category-filter').addEventListener('change', e => { currentCategory = e.target.value; currentPage = 1; loadLeads(); });
let searchTimeout;
document.getElementById('search-input').addEventListener('input', e => {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(() => { currentSearch = e.target.value; currentPage = 1; loadLeads(); }, 300);
});
```

- [ ] **Step 7: Verificar que el servidor arranca**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "import dashboard; print('OK')"
```

Esperado: `OK`

- [ ] **Step 8: Commit**

```bash
git add dashboard.py
git commit -m "feat: pagination UI in leads tab (prev/next, page N of M)"
```

---

### Task 6: Deploy a Railway

**Files:** ninguno (solo deploy)

- [ ] **Step 1: Verificar startup limpio**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "import dashboard; print('OK')"
```

Esperado: `OK`

- [ ] **Step 2: Revisar git log**

```bash
git log --oneline -6
```

Esperado: los 4 commits de Fase B más recientes.

- [ ] **Step 3: Deploy**

```bash
railway up --service web --detach
```

Esperado: comando aceptado con exit code 0.
