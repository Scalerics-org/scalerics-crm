# Fase D: Features de Ventas — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar plantillas de WhatsApp, registro de llamadas y trazabilidad de quién hizo qué acción.

**Architecture:** Cambios quirúrgicos en archivos existentes. Las 3 features son independientes. `database.py` concentra el schema y las funciones de DB. Las rutas se agregan a los blueprints existentes (`wa.py`, `leads.py`). La UI se agrega a `dashboard.py` sin tocar la lógica de otras secciones.

**Tech Stack:** Python/Flask, SQLite, vanilla JS, Flask session

---

## File Map

| Archivo | Tasks |
|---|---|
| `database.py` | D1: schema + funciones DB |
| `routes/wa.py` | D3: API de plantillas WA |
| `routes/leads.py` | D4: API de llamadas + propagar user_name |
| `dashboard.py` | D2: login nombre, D5: UI plantillas WA, D6: tab Llamadas + activity trail |

---

### Task D1: Schema de DB y funciones

**Files:**
- Modify: `database.py`

- [ ] **Step 1: Agregar tablas wa_templates y call_logs en init_db()**

En `database.py`, encontrar el bloque de `lead_events` (termina alrededor de la línea 198):
```python
        # ── lead_events ───────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lead_events (
                ...
            )
        """)
        _add_column(conn, "businesses", "last_event_at", "TIMESTAMP")
        _add_column(conn, "businesses", "score", "INTEGER")

        conn.commit()
```

Después de `_add_column(conn, "businesses", "score", "INTEGER")` y ANTES de `conn.commit()`, insertar:

```python
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wa_templates (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                body        TEXT NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS call_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id     INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                called_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                outcome     TEXT NOT NULL,
                notes       TEXT,
                created_by  TEXT DEFAULT 'sistema'
            )
        """)
        _add_column(conn, "lead_events", "created_by", "TEXT DEFAULT 'sistema'")
```

- [ ] **Step 2: Actualizar add_lead_event para incluir created_by**

Encontrar (línea ~843):
```python
def add_lead_event(db_path: str, lead_id: int, new_status: str, note: str = "") -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO lead_events (lead_id, new_status, note) VALUES (?, ?, ?)",
            (lead_id, new_status, note or ""),
        )
```

Reemplazar con:
```python
def add_lead_event(db_path: str, lead_id: int, new_status: str, note: str = "", created_by: str = "sistema") -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO lead_events (lead_id, new_status, note, created_by) VALUES (?, ?, ?, ?)",
            (lead_id, new_status, note or "", created_by),
        )
```

- [ ] **Step 3: Actualizar get_lead_events para incluir created_by en SELECT**

Encontrar (línea ~862):
```python
        cursor = conn.execute(
            "SELECT id, new_status, note, created_at FROM lead_events WHERE lead_id = ? ORDER BY created_at DESC",
            (lead_id,),
        )
```

Reemplazar con:
```python
        cursor = conn.execute(
            "SELECT id, new_status, note, created_at, created_by FROM lead_events WHERE lead_id = ? ORDER BY created_at DESC",
            (lead_id,),
        )
```

- [ ] **Step 4: Agregar funciones de DB al final del archivo**

Al final de `database.py`, agregar:

```python
# ─── WA Templates ─────────────────────────────────────────────────────────────

def get_wa_templates(db_path: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute("SELECT * FROM wa_templates ORDER BY created_at ASC")
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def create_wa_template(db_path: str, name: str, body: str) -> int:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO wa_templates (name, body) VALUES (?, ?)", (name, body)
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def delete_wa_template(db_path: str, template_id: int) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM wa_templates WHERE id = ?", (template_id,))
        conn.commit()
    finally:
        conn.close()


# ─── Call Logs ────────────────────────────────────────────────────────────────

def add_call_log(db_path: str, lead_id: int, outcome: str, notes: str = "", created_by: str = "sistema") -> int:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO call_logs (lead_id, outcome, notes, created_by) VALUES (?, ?, ?, ?)",
            (lead_id, outcome, notes or "", created_by),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_call_logs(db_path: str, lead_id: int) -> list[dict]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT * FROM call_logs WHERE lead_id = ? ORDER BY called_at DESC",
            (lead_id,),
        )
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()
```

- [ ] **Step 5: Verificar sintaxis**

```bash
python -c "from database import get_wa_templates, create_wa_template, delete_wa_template, add_call_log, get_call_logs, add_lead_event; print('OK')"
```

Esperado: `OK`

- [ ] **Step 6: Commit**

```bash
git add database.py
git commit -m "feat: wa_templates and call_logs tables + DB functions + created_by in lead_events"
```

---

### Task D2: Login con nombre de usuario

**Files:**
- Modify: `dashboard.py` — constante LOGIN_HTML + función login()

- [ ] **Step 1: Agregar campo nombre en LOGIN_HTML**

En `dashboard.py`, encontrar el form en `LOGIN_HTML` (después de la implementación del CSRF de Fase B):
```html
  <form method="POST">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <label>Contraseña</label>
    <input type="password" name="password" autofocus placeholder="Ingresá la contraseña del equipo">
    <button type="submit">Entrar</button>
  </form>
```

Reemplazar con:
```html
  <form method="POST">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <label>Tu nombre</label>
    <input type="text" name="nombre" placeholder="Ej: Juan" autocomplete="off">
    <label>Contraseña</label>
    <input type="password" name="password" autofocus placeholder="Ingresá la contraseña del equipo">
    <button type="submit">Entrar</button>
  </form>
```

- [ ] **Step 2: Guardar nombre en sesión al hacer login exitoso**

En `dashboard.py`, encontrar la función `login()`. Hay dos lugares donde se hace `session["logged_in"] = True` seguido de un `return redirect(...)`. En ambos, agregar `session["user_name"] = request.form.get("nombre", "").strip() or "sistema"` justo antes del redirect.

Encontrar el primer bloque (cuando no hay password configurado):
```python
                if not expected:
                    session["logged_in"] = True
                    return redirect(url_for("index"))
```

Reemplazar con:
```python
                if not expected:
                    session["logged_in"] = True
                    session["user_name"] = request.form.get("nombre", "").strip() or "sistema"
                    return redirect(url_for("index"))
```

Encontrar el segundo bloque (login exitoso normal):
```python
                if secrets.compare_digest(password, expected):
                    session["logged_in"] = True
                    return redirect(url_for("index"))
```

Reemplazar con:
```python
                if secrets.compare_digest(password, expected):
                    session["logged_in"] = True
                    session["user_name"] = request.form.get("nombre", "").strip() or "sistema"
                    return redirect(url_for("index"))
```

- [ ] **Step 3: Verificar**

```bash
python -c "import dashboard; print('OK')"
```

Esperado: `OK`

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "feat: add nombre field to login form, save as session user_name"
```

---

### Task D3: API de plantillas WA

**Files:**
- Modify: `routes/wa.py`

- [ ] **Step 1: Agregar las 3 rutas al final de routes/wa.py**

Al final del archivo `routes/wa.py`, agregar:

```python
@wa_bp.route("/api/wa/templates", methods=["GET"])
def api_wa_templates_list():
    from database import get_wa_templates
    return jsonify(get_wa_templates(current_app.config["DB_PATH"]))


@wa_bp.route("/api/wa/templates", methods=["POST"])
def api_wa_templates_create():
    from database import create_wa_template
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    body = (data.get("body") or "").strip()
    if not name or not body:
        return jsonify({"ok": False, "error": "name y body requeridos"}), 400
    tid = create_wa_template(current_app.config["DB_PATH"], name, body)
    return jsonify({"ok": True, "id": tid}), 201


@wa_bp.route("/api/wa/templates/<int:template_id>", methods=["DELETE"])
def api_wa_templates_delete(template_id):
    from database import delete_wa_template
    delete_wa_template(current_app.config["DB_PATH"], template_id)
    return jsonify({"ok": True})
```

- [ ] **Step 2: Verificar**

```bash
python -c "from routes.wa import wa_bp; print('OK')"
```

Esperado: `OK`

- [ ] **Step 3: Commit**

```bash
git add routes/wa.py
git commit -m "feat: WA templates CRUD API (GET/POST/DELETE /api/wa/templates)"
```

---

### Task D4: API de llamadas + propagar user_name a lead_events

**Files:**
- Modify: `routes/leads.py`

- [ ] **Step 1: Actualizar imports de Flask y database**

En `routes/leads.py`, encontrar:
```python
from flask import Blueprint, current_app, jsonify, request
```

Reemplazar con:
```python
from flask import Blueprint, current_app, jsonify, request, session
```

También actualizar el import de database. Encontrar:
```python
from database import (get_all_businesses, update_business, delete_business, get_business,
                      get_client_info, insert_business,
                      add_attachment, get_attachments, get_attachment_file, delete_attachment,
                      add_lead_event, get_lead_events)
```

Reemplazar con:
```python
from database import (get_all_businesses, update_business, delete_business, get_business,
                      get_client_info, insert_business,
                      add_attachment, get_attachments, get_attachment_file, delete_attachment,
                      add_lead_event, get_lead_events,
                      add_call_log, get_call_logs)
```

- [ ] **Step 2: Propagar user_name a los 3 llamados de add_lead_event**

En `routes/leads.py`, encontrar (línea ~129):
```python
    add_lead_event(db, biz_id, crm_status)
```
Reemplazar con:
```python
    add_lead_event(db, biz_id, crm_status, created_by=session.get("user_name", "sistema"))
```

Encontrar (línea ~144):
```python
        add_lead_event(db, biz_id, crm_status)
```
Reemplazar con:
```python
        add_lead_event(db, biz_id, crm_status, created_by=session.get("user_name", "sistema"))
```

Encontrar (línea ~165):
```python
    add_lead_event(db, biz_id, "contactado", note=note)
```
Reemplazar con:
```python
    add_lead_event(db, biz_id, "contactado", note=note, created_by=session.get("user_name", "sistema"))
```

- [ ] **Step 3: Agregar constante de outcomes válidos y las 2 rutas de llamadas**

En `routes/leads.py`, justo después de `_PER_PAGE = 50` (constante de paginación de Fase B), agregar:

```python
_VALID_OUTCOMES = {"contestó", "no_contestó", "buzón"}
```

Al final del archivo (o junto a las otras rutas de `/api/leads/<id>/...`), agregar:

```python
@leads_bp.route("/api/leads/<int:biz_id>/calls", methods=["POST"])
def api_add_call(biz_id):
    data = request.get_json() or {}
    outcome = (data.get("outcome") or "").strip()
    notes = (data.get("notes") or "").strip()
    if outcome not in _VALID_OUTCOMES:
        return jsonify({"ok": False, "error": f"Outcome inválido: {outcome}"}), 400
    created_by = session.get("user_name", "sistema")
    add_call_log(_db(), biz_id, outcome, notes, created_by)
    return jsonify({"ok": True}), 201


@leads_bp.route("/api/leads/<int:biz_id>/calls", methods=["GET"])
def api_get_calls(biz_id):
    return jsonify(get_call_logs(_db(), biz_id))
```

- [ ] **Step 4: Verificar**

```bash
python -c "from routes.leads import leads_bp; print('OK')"
```

Esperado: `OK`

- [ ] **Step 5: Commit**

```bash
git add routes/leads.py
git commit -m "feat: call logging API + propagate user_name to lead_events"
```

---

### Task D5: UI de plantillas WA en dashboard

**Files:**
- Modify: `dashboard.py`

- [ ] **Step 1: Agregar panel de plantillas al WA panel HTML**

En `dashboard.py`, encontrar el cierre del `wa-chat-content` div en el WA panel:
```html
          <div class="wa-input-row">
            <input class="wa-input" id="wa-input" placeholder="Escribir mensaje..." onkeydown="if(event.key==='Enter')sendWaMessage()">
            <button class="wa-send-btn" onclick="sendWaMessage()">Enviar</button>
          </div>
        </div>
      </div>
    </div>
  </div>
```

Reemplazar con:
```html
          <div class="wa-input-row">
            <input class="wa-input" id="wa-input" placeholder="Escribir mensaje..." onkeydown="if(event.key==='Enter')sendWaMessage()">
            <button class="wa-send-btn" onclick="sendWaMessage()">Enviar</button>
          </div>
          <div id="wa-templates-panel" style="border-top:1px solid #1e293b;padding:10px;background:#0d1525">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
              <span style="font-size:.75rem;font-weight:600;color:#64748b;text-transform:uppercase;letter-spacing:.5px">Plantillas</span>
              <button onclick="toggleWaTemplateForm()" style="font-size:.72rem;background:#1e293b;border:none;color:#94a3b8;padding:3px 8px;border-radius:4px;cursor:pointer">+ Nueva</button>
            </div>
            <div id="wa-template-form" style="display:none;margin-bottom:8px">
              <input id="wa-tmpl-name" placeholder="Nombre de la plantilla" style="width:100%;background:#111827;border:1px solid #1e293b;color:#e2e8f0;padding:5px 8px;border-radius:4px;font-size:.78rem;margin-bottom:4px;box-sizing:border-box">
              <textarea id="wa-tmpl-body" rows="2" placeholder="Texto del mensaje..." style="width:100%;background:#111827;border:1px solid #1e293b;color:#e2e8f0;padding:5px 8px;border-radius:4px;font-size:.78rem;resize:none;margin-bottom:4px;box-sizing:border-box"></textarea>
              <button onclick="saveWaTemplate()" style="font-size:.75rem;background:#0088cc;border:none;color:#fff;padding:4px 12px;border-radius:4px;cursor:pointer">Guardar</button>
            </div>
            <div id="wa-template-list" style="max-height:120px;overflow-y:auto"></div>
          </div>
        </div>
      </div>
    </div>
  </div>
```

- [ ] **Step 2: Agregar funciones JS de plantillas WA**

En `dashboard.py`, buscar el bloque `// ========== WhatsApp panel ==========` (alrededor de la línea 1148). Justo ANTES de ese bloque, insertar:

```javascript
// ========== WA Templates ==========
async function loadWaTemplates() {
  try {
    const r = await fetch('/api/wa/templates');
    const templates = await r.json();
    const list = document.getElementById('wa-template-list');
    if (!list) return;
    if (!templates.length) { list.innerHTML = '<div style="font-size:.72rem;color:#475569;padding:2px 0">Sin plantillas guardadas</div>'; return; }
    list.innerHTML = templates.map(t =>
      `<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid #1a2234">
        <span style="font-size:.78rem;color:#94a3b8;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-right:8px">${esc(t.name)}</span>
        <div style="display:flex;gap:4px;flex-shrink:0">
          <button onclick="useWaTemplate(${JSON.stringify(t.body)})" style="font-size:.7rem;background:#1e293b;border:none;color:#60a5fa;padding:2px 8px;border-radius:4px;cursor:pointer">Usar</button>
          <button onclick="deleteWaTemplate(${t.id})" style="font-size:.7rem;background:#1e293b;border:none;color:#f87171;padding:2px 8px;border-radius:4px;cursor:pointer">✕</button>
        </div>
      </div>`
    ).join('');
  } catch(e) { console.error('loadWaTemplates', e); }
}

function toggleWaTemplateForm() {
  const f = document.getElementById('wa-template-form');
  if (f) f.style.display = f.style.display === 'none' ? 'block' : 'none';
}

async function saveWaTemplate() {
  const name = (document.getElementById('wa-tmpl-name').value || '').trim();
  const body = (document.getElementById('wa-tmpl-body').value || '').trim();
  if (!name || !body) return;
  await fetch('/api/wa/templates', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name, body})});
  document.getElementById('wa-tmpl-name').value = '';
  document.getElementById('wa-tmpl-body').value = '';
  const f = document.getElementById('wa-template-form');
  if (f) f.style.display = 'none';
  loadWaTemplates();
}

function useWaTemplate(body) {
  const input = document.getElementById('wa-input');
  if (input) { input.value = body; input.focus(); }
}

async function deleteWaTemplate(id) {
  await fetch(`/api/wa/templates/${id}`, {method:'DELETE'});
  loadWaTemplates();
}

```

- [ ] **Step 3: Llamar loadWaTemplates cuando se abre el panel WA**

Encontrar:
```javascript
  if (name === 'wa' && !waLoaded) loadWaLeads();
```

Reemplazar con:
```javascript
  if (name === 'wa' && !waLoaded) loadWaLeads();
  if (name === 'wa') loadWaTemplates();
```

- [ ] **Step 4: Agregar selector de plantilla en el panel de cliente (tab Info)**

En `dashboard.py`, dentro de `_cpRenderInfo()`, encontrar el bloque del pitch (alrededor de la línea 1945):
```javascript
  ${l.pitch_text ? `<div class="cp-section">
    <div class="cp-section-title" style="display:flex;justify-content:space-between;align-items:center">
      <span>Pitch WhatsApp</span>
```

Justo ANTES de ese bloque (es decir, entre el bloque de datos del bot y el bloque de pitch), insertar:

```javascript
  ${l.phone ? `<div class="cp-section">
    <div class="cp-section-title">Enviar por WhatsApp</div>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <select id="cp-wa-tmpl-sel" style="background:#111827;border:1px solid #1e293b;color:#94a3b8;padding:5px 10px;border-radius:6px;font-size:.78rem;flex:1">
        <option value="">Elegir plantilla...</option>
      </select>
      <button onclick="_cpUseWaTemplate()" style="background:#0088cc;border:none;color:#fff;padding:5px 14px;border-radius:6px;font-size:.78rem;cursor:pointer;white-space:nowrap">Abrir WA →</button>
    </div>
  </div>` : ''}
```

- [ ] **Step 5: Agregar función _cpUseWaTemplate y carga de plantillas en el selector**

En `dashboard.py`, justo antes de `function _cpRenderInfo()`, insertar:

```javascript
async function _cpLoadWaTemplates() {
  const sel = document.getElementById('cp-wa-tmpl-sel');
  if (!sel) return;
  try {
    const r = await fetch('/api/wa/templates');
    const templates = await r.json();
    sel.innerHTML = '<option value="">Elegir plantilla...</option>' +
      templates.map(t => `<option value="${esc(t.body)}">${esc(t.name)}</option>`).join('');
  } catch {}
}

function _cpUseWaTemplate() {
  const sel = document.getElementById('cp-wa-tmpl-sel');
  const phone = (_cpData.lead || {}).phone || '';
  if (!sel || !sel.value || !phone) return;
  const digits = phone.replace(/\D/g, '');
  window.open(`https://wa.me/${digits}?text=${encodeURIComponent(sel.value)}`, '_blank');
}

```

Luego, en `_cpSwitchTab`, encontrar:
```javascript
  if (tab === 'info')      body.innerHTML = _cpRenderInfo();
```

Reemplazar con:
```javascript
  if (tab === 'info')      { body.innerHTML = _cpRenderInfo(); _cpLoadWaTemplates(); }
```

- [ ] **Step 6: Verificar**

```bash
python -c "import dashboard; print('OK')"
```

Esperado: `OK`

- [ ] **Step 7: Commit**

```bash
git add dashboard.py
git commit -m "feat: WA templates UI in WA panel and client panel"
```

---

### Task D6: Tab Llamadas + activity trail en historial

**Files:**
- Modify: `dashboard.py`

- [ ] **Step 1: Agregar tab Llamadas en el HTML del panel de cliente**

En `dashboard.py`, encontrar los tabs del panel de cliente:
```html
      <div class="cp-tab active" data-tab="info" onclick="_cpSwitchTab('info')">Info</div>
      <div class="cp-tab" data-tab="conv" onclick="_cpSwitchTab('conv')">Conversación</div>
      <div class="cp-tab" data-tab="meet" onclick="_cpSwitchTab('meet')">Reuniones</div>
      <div class="cp-tab" data-tab="budget" onclick="_cpSwitchTab('budget')">Presupuesto</div>
      <div class="cp-tab" data-tab="demo" onclick="_cpSwitchTab('demo')">Demo</div>
      <div class="cp-tab" data-tab="ctasks" onclick="_cpSwitchTab('ctasks')">Tareas</div>
```

Reemplazar con:
```html
      <div class="cp-tab active" data-tab="info" onclick="_cpSwitchTab('info')">Info</div>
      <div class="cp-tab" data-tab="conv" onclick="_cpSwitchTab('conv')">Conversación</div>
      <div class="cp-tab" data-tab="meet" onclick="_cpSwitchTab('meet')">Reuniones</div>
      <div class="cp-tab" data-tab="budget" onclick="_cpSwitchTab('budget')">Presupuesto</div>
      <div class="cp-tab" data-tab="demo" onclick="_cpSwitchTab('demo')">Demo</div>
      <div class="cp-tab" data-tab="ctasks" onclick="_cpSwitchTab('ctasks')">Tareas</div>
      <div class="cp-tab" data-tab="calls" onclick="_cpSwitchTab('calls')">📞 Llamadas</div>
```

- [ ] **Step 2: Agregar fetch de llamadas en _cpLoadAll**

En `dashboard.py`, encontrar `_cpLoadAll`. Encontrar la línea que lista los fetches en `Promise.allSettled`:
```javascript
  const [leadRes, meetRes, budgetRes, demoRes, attBudgetRes, attDemoRes, eventsRes] = await Promise.allSettled([
    fetch('/api/leads/' + _cpClientId).then(r => r.json()),
    fetch('/api/calendar/meetings/' + _cpClientId).then(r => r.json()),
    fetch('/api/leads/' + _cpClientId + '/budget').then(r => r.json()),
    fetch('/api/demo/status/' + _cpClientId).then(r => r.json()),
    fetch('/api/leads/' + _cpClientId + '/attachments?section=budget').then(r => r.json()),
    fetch('/api/leads/' + _cpClientId + '/attachments?section=demo').then(r => r.json()),
    fetch('/api/leads/' + _cpClientId + '/events').then(r => r.json()),
  ]);
  _cpData.lead    = leadRes.status === 'fulfilled' ? leadRes.value : {};
  _cpData.meetings = meetRes.status === 'fulfilled' && Array.isArray(meetRes.value) ? meetRes.value : [];
  _cpData.budget  = budgetRes.status === 'fulfilled' ? budgetRes.value : null;
  _cpData.demo    = demoRes.status === 'fulfilled' ? demoRes.value : null;
  _cpData.attBudget = attBudgetRes.status === 'fulfilled' && Array.isArray(attBudgetRes.value) ? attBudgetRes.value : [];
  _cpData.attDemo   = attDemoRes.status === 'fulfilled' && Array.isArray(attDemoRes.value) ? attDemoRes.value : [];
  _cpData.events    = eventsRes.status === 'fulfilled' && Array.isArray(eventsRes.value) ? eventsRes.value : [];
```

Reemplazar con:
```javascript
  const [leadRes, meetRes, budgetRes, demoRes, attBudgetRes, attDemoRes, eventsRes, callsRes] = await Promise.allSettled([
    fetch('/api/leads/' + _cpClientId).then(r => r.json()),
    fetch('/api/calendar/meetings/' + _cpClientId).then(r => r.json()),
    fetch('/api/leads/' + _cpClientId + '/budget').then(r => r.json()),
    fetch('/api/demo/status/' + _cpClientId).then(r => r.json()),
    fetch('/api/leads/' + _cpClientId + '/attachments?section=budget').then(r => r.json()),
    fetch('/api/leads/' + _cpClientId + '/attachments?section=demo').then(r => r.json()),
    fetch('/api/leads/' + _cpClientId + '/events').then(r => r.json()),
    fetch('/api/leads/' + _cpClientId + '/calls').then(r => r.json()),
  ]);
  _cpData.lead    = leadRes.status === 'fulfilled' ? leadRes.value : {};
  _cpData.meetings = meetRes.status === 'fulfilled' && Array.isArray(meetRes.value) ? meetRes.value : [];
  _cpData.budget  = budgetRes.status === 'fulfilled' ? budgetRes.value : null;
  _cpData.demo    = demoRes.status === 'fulfilled' ? demoRes.value : null;
  _cpData.attBudget = attBudgetRes.status === 'fulfilled' && Array.isArray(attBudgetRes.value) ? attBudgetRes.value : [];
  _cpData.attDemo   = attDemoRes.status === 'fulfilled' && Array.isArray(attDemoRes.value) ? attDemoRes.value : [];
  _cpData.events    = eventsRes.status === 'fulfilled' && Array.isArray(eventsRes.value) ? eventsRes.value : [];
  _cpData.calls     = callsRes.status === 'fulfilled' && Array.isArray(callsRes.value) ? callsRes.value : [];
```

- [ ] **Step 3: Agregar función _cpRenderCalls**

En `dashboard.py`, justo ANTES de `function _cpRenderHistory()`, insertar:

```javascript
function _cpRenderCalls() {
  const calls = _cpData.calls || [];
  const outcomeLabel = {'contestó':'Contestó','no_contestó':'No contestó','buzón':'Buzón'};
  const outcomeColor = {'contestó':'#4ade80','no_contestó':'#f87171','buzón':'#fbbf24'};
  const history = calls.length ? `<div class="cp-section">
    <div class="cp-section-title">Historial de llamadas</div>
    ${calls.map(c => `<div style="display:flex;gap:10px;align-items:flex-start;padding:7px 0;border-bottom:1px solid #1a2234">
      <div style="width:8px;height:8px;border-radius:50%;background:${outcomeColor[c.outcome]||'#475569'};margin-top:5px;flex-shrink:0"></div>
      <div style="flex:1">
        <span style="font-size:.8rem;color:#e2e8f0;font-weight:600">${outcomeLabel[c.outcome]||c.outcome}</span>
        <span style="font-size:.72rem;color:#475569;margin-left:8px">${timeAgo(c.called_at)}${c.created_by && c.created_by !== 'sistema' ? ' · por ' + esc(c.created_by) : ''}</span>
        ${c.notes ? `<div style="font-size:.72rem;color:#64748b;margin-top:2px">${esc(c.notes)}</div>` : ''}
      </div>
    </div>`).join('')}
  </div>` : '<div style="padding:12px 0;font-size:.82rem;color:#475569">Sin llamadas registradas</div>';
  return `<div class="cp-section">
    <div class="cp-section-title">Registrar llamada</div>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <select id="call-outcome" style="background:#111827;border:1px solid #1e293b;color:#e2e8f0;padding:6px 10px;border-radius:6px;font-size:.8rem">
        <option value="">Resultado...</option>
        <option value="contestó">Contestó</option>
        <option value="no_contestó">No contestó</option>
        <option value="buzón">Buzón</option>
      </select>
      <input id="call-notes" placeholder="Notas (opcional)" style="flex:1;min-width:120px;background:#111827;border:1px solid #1e293b;color:#e2e8f0;padding:6px 10px;border-radius:6px;font-size:.8rem">
      <button onclick="_cpLogCall()" style="background:#0088cc;border:none;color:#fff;padding:6px 14px;border-radius:6px;font-size:.8rem;cursor:pointer">Registrar</button>
    </div>
  </div>
  ${history}`;
}

async function _cpLogCall() {
  const outcome = (document.getElementById('call-outcome') || {}).value || '';
  const notes = (document.getElementById('call-notes') || {}).value || '';
  if (!outcome) { alert('Elegí un resultado'); return; }
  await fetch(`/api/leads/${_cpClientId}/calls`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({outcome, notes})});
  const r = await fetch(`/api/leads/${_cpClientId}/calls`);
  _cpData.calls = await r.json();
  _cpSwitchTab('calls');
}

```

- [ ] **Step 4: Agregar case 'calls' en _cpSwitchTab**

Encontrar:
```javascript
  else if (tab === 'ctasks') { body.innerHTML = _cpRenderTasks(); _cpBindTasks(); }
}
```

Reemplazar con:
```javascript
  else if (tab === 'ctasks') { body.innerHTML = _cpRenderTasks(); _cpBindTasks(); }
  else if (tab === 'calls')  body.innerHTML = _cpRenderCalls();
}
```

- [ ] **Step 5: Mostrar created_by en _cpRenderHistory**

En `dashboard.py`, dentro de `_cpRenderHistory()`, encontrar:
```javascript
    const label = crmLabels[e.new_status] || e.new_status;
    const when = timeAgo(e.created_at);
    const note = e.note ? `<div style="font-size:.72rem;color:#64748b;margin-top:2px">${esc(e.note)}</div>` : '';
    return `<div style="display:flex;gap:10px;align-items:flex-start;padding:7px 0;border-bottom:1px solid #1a2234">
      <div style="width:8px;height:8px;border-radius:50%;background:#0088cc;margin-top:5px;flex-shrink:0"></div>
      <div style="flex:1">
        <span style="font-size:.8rem;color:#e2e8f0;font-weight:600">${label}</span>
        <span style="font-size:.72rem;color:#475569;margin-left:8px">${when}</span>
        ${note}
      </div>
    </div>`;
```

Reemplazar con:
```javascript
    const label = crmLabels[e.new_status] || e.new_status;
    const when = timeAgo(e.created_at);
    const by = (e.created_by && e.created_by !== 'sistema') ? ` · por ${esc(e.created_by)}` : '';
    const note = e.note ? `<div style="font-size:.72rem;color:#64748b;margin-top:2px">${esc(e.note)}</div>` : '';
    return `<div style="display:flex;gap:10px;align-items:flex-start;padding:7px 0;border-bottom:1px solid #1a2234">
      <div style="width:8px;height:8px;border-radius:50%;background:#0088cc;margin-top:5px;flex-shrink:0"></div>
      <div style="flex:1">
        <span style="font-size:.8rem;color:#e2e8f0;font-weight:600">${label}</span>
        <span style="font-size:.72rem;color:#475569;margin-left:8px">${when}${by}</span>
        ${note}
      </div>
    </div>`;
```

- [ ] **Step 6: Verificar**

```bash
python -c "import dashboard; print('OK')"
```

Esperado: `OK`

- [ ] **Step 7: Commit**

```bash
git add dashboard.py
git commit -m "feat: Llamadas tab in client panel + activity trail (created_by) in event history"
```

---

### Task D7: Deploy a Railway

**Files:** ninguno

- [ ] **Step 1: Verificar startup**

```bash
python -c "import dashboard; print('OK')"
```

Esperado: `OK`

- [ ] **Step 2: Revisar commits**

```bash
git log --oneline -7
```

- [ ] **Step 3: Deploy**

```bash
railway up --service web --detach
```
