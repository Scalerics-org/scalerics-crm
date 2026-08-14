# CRM States Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar el estado `contactado` con `interesado`, separar "Interesado" y "Llamar después" en el call modal, agregar filtros a Cola, renombrar Pipeline a "Proceso de venta" y fijar `no_interesa` para que funcione desde cualquier panel.

**Architecture:** Migración en `database.py` (corre al reiniciar), cambios backend en `routes/leads.py` (estados, outcomes, callback endpoint), cambios UI/JS en `dashboard.py` (HTML, CSS, JS). Secuencial: DB → backend → frontend.

**Tech Stack:** Python/Flask, SQLite, HTML/CSS/JS vanilla.

---

## File Map

| Archivo | Cambios |
|---|---|
| `database.py` | Migración `contactado` → `interesado` en `init_db` |
| `routes/leads.py` | `_VALID_CRM_STATES`, `_VALID_OUTCOMES`, `_STATUS_TO_GOAL`, `api_contact`, `api_set_callback`, `funnel_order` + `_legacy` |
| `dashboard.py` (HTML/CSS) | Filtros de Cola, nav rename, CSS `.row-interesado`, `crmLabels`, `stateLabels` |
| `dashboard.py` (JS) | `_callbackOutcome`, `setCallbackOutcome`, `confirmCallback`, `logCallOutcome`, `markContacted`, `loadSeguimientos`, `loadColaStats`, `loadCola` |

---

## Task 1: database.py — migración de datos

**Files:**
- Modify: `database.py`

- [ ] **Paso 1: Agregar migración en `init_db` justo antes del bloque de índices**

Buscar en `database.py` el bloque:
```python
        # Idempotent unique index — prevents duplicate leads from concurrent bot pushes
```
Insertar ANTES de ese comentario:
```python
        # Migrate contactado → interesado (idempotent)
        conn.execute("UPDATE businesses SET crm_status = 'interesado' WHERE crm_status = 'contactado'")
        conn.commit()

```

- [ ] **Paso 2: Verificar sintaxis**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "import ast; ast.parse(open('database.py', encoding='utf-8-sig').read()); print('OK')"
```
Esperado: `OK`

- [ ] **Paso 3: Commit**

```bash
git add database.py
git commit -m "feat: migrate crm_status contactado -> interesado on startup"
```

---

## Task 2: routes/leads.py — estados, outcomes, endpoints

**Files:**
- Modify: `routes/leads.py`

- [ ] **Paso 1: Agregar `interesado` a `_VALID_CRM_STATES` y `_VALID_OUTCOMES`**

Buscar exactamente:
```python
_VALID_CRM_STATES = {
    "sin_contactar", "contactado", "reunion_agendada",
    "reunion_hecha", "presupuesto_enviado", "negociacion",
    "cliente_cerrado", "en_desarrollo", "finalizado",
    "llamar_despues", "no_interesa",
    # legacy aliases kept for backwards compat
    "agendo", "firmo",
}
```
Reemplazar con:
```python
_VALID_CRM_STATES = {
    "sin_contactar", "interesado", "contactado", "reunion_agendada",
    "reunion_hecha", "presupuesto_enviado", "negociacion",
    "cliente_cerrado", "en_desarrollo", "finalizado",
    "llamar_despues", "no_interesa",
    # legacy aliases kept for backwards compat
    "agendo", "firmo",
}
```

Buscar exactamente:
```python
_VALID_OUTCOMES = {"contestó", "no_contestó", "buzón", "no_interesa", "llamar_despues"}
```
Reemplazar con:
```python
_VALID_OUTCOMES = {"contestó", "no_contestó", "buzón", "no_interesa", "llamar_despues", "interesado"}
```

- [ ] **Paso 2: Actualizar `_STATUS_TO_GOAL` en `api_crm_status`**

Buscar en `api_crm_status`:
```python
    _STATUS_TO_GOAL = {
        "contactado":      "leads_contactados",
        "reunion_hecha":   "reuniones_hechas",
        "cliente_cerrado": "clientes_cerrados",
    }
```
Reemplazar con:
```python
    _STATUS_TO_GOAL = {
        "interesado":      "leads_contactados",
        "reunion_hecha":   "reuniones_hechas",
        "cliente_cerrado": "clientes_cerrados",
    }
```

- [ ] **Paso 3: Actualizar `_STATUS_TO_GOAL_BATCH` en `api_batch_status`**

Buscar en `api_batch_status`:
```python
    _STATUS_TO_GOAL_BATCH = {
        "contactado":      "leads_contactados",
        "reunion_hecha":   "reuniones_hechas",
        "cliente_cerrado": "clientes_cerrados",
    }
```
Reemplazar con:
```python
    _STATUS_TO_GOAL_BATCH = {
        "interesado":      "leads_contactados",
        "reunion_hecha":   "reuniones_hechas",
        "cliente_cerrado": "clientes_cerrados",
    }
```

- [ ] **Paso 4: Actualizar `api_contact` — usar `interesado`**

Buscar exactamente en `api_contact`:
```python
    update_business(db, biz_id, status="contacted", notes=note, crm_status="contactado")
    add_lead_event(db, biz_id, "contactado", note=note, created_by=user_name)
    log_activity(db, user_name, "status_change", "lead", biz_id, biz.get("name", ""), "contactado",
                 user_id=session.get("user_id"))
```
Reemplazar con:
```python
    update_business(db, biz_id, status="contacted", notes=note, crm_status="interesado")
    add_lead_event(db, biz_id, "interesado", note=note, created_by=user_name)
    log_activity(db, user_name, "status_change", "lead", biz_id, biz.get("name", ""), "interesado",
                 user_id=session.get("user_id"))
```

- [ ] **Paso 5: Actualizar `api_set_callback` — aceptar `outcome` param**

Buscar exactamente:
```python
@leads_bp.route("/api/leads/<int:biz_id>/callback", methods=["POST"])
def api_set_callback(biz_id):
    data = request.get_json() or {}
    callback_date = (data.get("callback_date") or "").strip()
    notes = (data.get("notes") or "").strip()
    if not callback_date:
        return jsonify({"ok": False, "error": "callback_date requerida"}), 400
    db = _db()
    user_name = session.get("user_name", "sistema")
    biz = get_business(db, biz_id) or {}
    update_business(db, biz_id, crm_status="llamar_despues", callback_date=callback_date)
    if notes:
        update_business(db, biz_id, notes=notes)
    add_lead_event(db, biz_id, "llamar_despues", note=f"Callback: {callback_date}", created_by=user_name)
    add_call_log(db, biz_id, "llamar_despues", notes or f"Callback: {callback_date}", user_name)
    log_activity(db, user_name, "callback_set", "lead", biz_id, biz.get("name", ""), callback_date,
                 user_id=session.get("user_id"))
    return jsonify({"ok": True})
```
Reemplazar con:
```python
@leads_bp.route("/api/leads/<int:biz_id>/callback", methods=["POST"])
def api_set_callback(biz_id):
    data = request.get_json() or {}
    callback_date = (data.get("callback_date") or "").strip()
    notes = (data.get("notes") or "").strip()
    outcome = data.get("outcome", "llamar_despues")
    if outcome not in ("llamar_despues", "interesado"):
        outcome = "llamar_despues"
    if not callback_date:
        return jsonify({"ok": False, "error": "callback_date requerida"}), 400
    db = _db()
    user_name = session.get("user_name", "sistema")
    biz = get_business(db, biz_id) or {}
    update_business(db, biz_id, crm_status=outcome, callback_date=callback_date)
    if notes:
        update_business(db, biz_id, notes=notes)
    add_lead_event(db, biz_id, outcome, note=f"Callback: {callback_date}", created_by=user_name)
    add_call_log(db, biz_id, outcome, notes or f"Callback: {callback_date}", user_name)
    log_activity(db, user_name, "callback_set", "lead", biz_id, biz.get("name", ""), callback_date,
                 user_id=session.get("user_id"))
    if outcome == "interesado":
        uids = _contributors(db, biz_id, session.get("user_id"))
        increment_task_progress(db, uids, "leads_contactados",
                                lead_id=biz_id, lead_name=biz.get("name", ""))
    return jsonify({"ok": True})
```

- [ ] **Paso 6: Actualizar `funnel_order` y `_legacy` en `api_metrics`**

Buscar exactamente (en `api_metrics`):
```python
    funnel_order = [
        "sin_contactar", "contactado", "reunion_agendada",
        "reunion_hecha", "presupuesto_enviado", "negociacion",
        "cliente_cerrado", "en_desarrollo", "finalizado",
    ]
    _legacy = {"firmo": "cliente_cerrado", "agendo": "reunion_agendada"}
```
Reemplazar con:
```python
    funnel_order = [
        "sin_contactar", "interesado", "reunion_agendada",
        "reunion_hecha", "presupuesto_enviado", "negociacion",
        "cliente_cerrado", "en_desarrollo", "finalizado",
    ]
    _legacy = {"firmo": "cliente_cerrado", "agendo": "reunion_agendada", "contactado": "interesado"}
```

- [ ] **Paso 7: Actualizar `funnel_order` y `_legacy` en `api_metrics_meta`**

Buscar exactamente (en `api_metrics_meta`, segunda ocurrencia):
```python
    funnel_order = ["sin_contactar", "contactado", "reunion_agendada", "reunion_hecha",
                    "presupuesto_enviado", "negociacion", "cliente_cerrado",
                    "en_desarrollo", "finalizado"]
    crm_counts = Counter(_norm(b.get("crm_status")) for b in businesses)
```
Reemplazar solo la línea de `funnel_order`:
```python
    funnel_order = ["sin_contactar", "interesado", "reunion_agendada", "reunion_hecha",
                    "presupuesto_enviado", "negociacion", "cliente_cerrado",
                    "en_desarrollo", "finalizado"]
    crm_counts = Counter(_norm(b.get("crm_status")) for b in businesses)
```
Y buscar el `_legacy` de `api_metrics_meta`:
```python
    _legacy = {"firmo": "cliente_cerrado", "agendo": "reunion_agendada"}
```
Reemplazar con:
```python
    _legacy = {"firmo": "cliente_cerrado", "agendo": "reunion_agendada", "contactado": "interesado"}
```

- [ ] **Paso 8: Verificar sintaxis**

```bash
python -c "import ast; ast.parse(open('routes/leads.py', encoding='utf-8-sig').read()); print('OK')"
```
Esperado: `OK`

- [ ] **Paso 9: Commit**

```bash
git add routes/leads.py
git commit -m "feat: crm states — interesado replaces contactado, callback accepts outcome param, funnel updated"
```

---

## Task 3: dashboard.py — HTML, CSS, labels

**Files:**
- Modify: `dashboard.py`

- [ ] **Paso 1: Renombrar "Pipeline" → "Proceso de venta" en el nav**

Buscar exactamente:
```html
  <div class="nav-item" id="nav-pipeline" onclick="showPanel('pipeline')"><i data-lucide="trending-up" class="nav-icon"></i> Pipeline</div>
```
Reemplazar con:
```html
  <div class="nav-item" id="nav-pipeline" onclick="showPanel('pipeline')"><i data-lucide="trending-up" class="nav-icon"></i> Proceso de venta</div>
```

- [ ] **Paso 2: Renombrar el `<h1>` del panel Pipeline**

Buscar exactamente:
```html
        <h1>Pipeline</h1>
```
Reemplazar con:
```html
        <h1>Proceso de venta</h1>
```

- [ ] **Paso 3: Agregar CSS `.row-interesado`**

Buscar exactamente:
```css
.row-llamar_despues{border-left:3px solid #f59e0b;background:rgba(245,158,11,.05)}
```
Agregar DESPUÉS:
```css
.row-interesado{border-left:3px solid #10b981;background:rgba(16,185,129,.05)}
```

- [ ] **Paso 4: Agregar filtros de Cola al HTML — antes de `<div class="table-wrap">`**

Buscar exactamente:
```html
    <div class="table-wrap">
      <div class="table-header no-cb">
        <span>Negocio</span><span>Teléfono</span><span>Notas</span><span>Acciones</span>
      </div>
      <div id="cola-body"></div>
```
Reemplazar con:
```html
    <div style="display:flex;gap:8px;margin-bottom:12px">
      <button id="cola-filter-sin" onclick="setColaFilter('sin_contactar')" style="padding:5px 14px;border-radius:8px;border:1px solid #0088cc;background:#0088cc;color:#fff;font-size:.78rem;font-weight:600;cursor:pointer;font-family:'Inter',sans-serif">Sin contactar</button>
      <button id="cola-filter-no" onclick="setColaFilter('no_interesa')" style="padding:5px 14px;border-radius:8px;border:1px solid #1e293b;background:transparent;color:#64748b;font-size:.78rem;font-weight:600;cursor:pointer;font-family:'Inter',sans-serif">No interesa</button>
    </div>
    <div class="table-wrap">
      <div class="table-header no-cb">
        <span>Negocio</span><span>Teléfono</span><span>Notas</span><span>Acciones</span>
      </div>
      <div id="cola-body"></div>
```

- [ ] **Paso 5: Actualizar `crmLabels` en el panel Meta Ads — agregar `interesado`**

Buscar exactamente:
```javascript
  const crmLabels = {sin_contactar:'Sin contactar',contactado:'Contactado',reunion_agendada:'Reunión agendada',reunion_hecha:'Reunión hecha',presupuesto_enviado:'Ppto enviado',negociacion:'Negociación',cliente_cerrado:'Cerrado',en_desarrollo:'En desarrollo',finalizado:'Finalizado',llamar_despues:'Llamar después',no_interesa:'No le interesa'};
```
Reemplazar con:
```javascript
  const crmLabels = {sin_contactar:'Sin contactar',interesado:'Interesado',contactado:'Interesado',reunion_agendada:'Reunión agendada',reunion_hecha:'Reunión hecha',presupuesto_enviado:'Ppto enviado',negociacion:'Negociación',cliente_cerrado:'Cerrado',en_desarrollo:'En desarrollo',finalizado:'Finalizado',llamar_despues:'Llamar después',no_interesa:'No le interesa'};
```

- [ ] **Paso 6: Actualizar `stateLabels` en `loadMetrics` JS — agregar `interesado`**

Buscar exactamente:
```javascript
  const stateLabels = {sin_contactar:'Sin contactar',contactado:'Contactado',reunion_agendada:'Reunión agendada',reunion_hecha:'Reunión hecha',presupuesto_enviado:'Presupuesto enviado',negociacion:'Negociación',cliente_cerrado:'Cliente cerrado',en_desarrollo:'En desarrollo',finalizado:'Finalizado'};
  const stateColors = {sin_contactar:'#334155',contactado:'#3b82f6',reunion_agendada:'#f59e0b',reunion_hecha:'#f97316',presupuesto_enviado:'#eab308',negociacion:'#f97316',cliente_cerrado:'#22c55e',en_desarrollo:'#10b981',finalizado:'#4ade80'};
```
Reemplazar con:
```javascript
  const stateLabels = {sin_contactar:'Sin contactar',interesado:'Interesado',contactado:'Interesado',reunion_agendada:'Reunión agendada',reunion_hecha:'Reunión hecha',presupuesto_enviado:'Presupuesto enviado',negociacion:'Negociación',cliente_cerrado:'Cliente cerrado',en_desarrollo:'En desarrollo',finalizado:'Finalizado'};
  const stateColors = {sin_contactar:'#334155',interesado:'#10b981',contactado:'#10b981',reunion_agendada:'#f59e0b',reunion_hecha:'#f97316',presupuesto_enviado:'#eab308',negociacion:'#f97316',cliente_cerrado:'#22c55e',en_desarrollo:'#10b981',finalizado:'#4ade80'};
```

- [ ] **Paso 7: Verificar sintaxis**

```bash
python -c "import ast; ast.parse(open('dashboard.py', encoding='utf-8-sig').read()); print('OK')"
```
Esperado: `OK`

- [ ] **Paso 8: Commit**

```bash
git add dashboard.py
git commit -m "feat: pipeline rename to Proceso de venta, Cola filter buttons, CSS row-interesado, crmLabels updated"
```

---

## Task 4: dashboard.py — JS lógica

**Files:**
- Modify: `dashboard.py`

- [ ] **Paso 1: Agregar `_colaFilter` variable y función `setColaFilter`**

Buscar exactamente:
```javascript
let _colaLeads = [];
```
Reemplazar con:
```javascript
let _colaLeads = [];
let _colaFilter = 'sin_contactar';
function setColaFilter(f) {
  _colaFilter = f;
  const sinBtn = document.getElementById('cola-filter-sin');
  const noBtn  = document.getElementById('cola-filter-no');
  if (sinBtn) { sinBtn.style.background = f === 'sin_contactar' ? '#0088cc' : 'transparent'; sinBtn.style.borderColor = f === 'sin_contactar' ? '#0088cc' : '#1e293b'; sinBtn.style.color = f === 'sin_contactar' ? '#fff' : '#64748b'; }
  if (noBtn)  { noBtn.style.background  = f === 'no_interesa'   ? '#ef4444' : 'transparent'; noBtn.style.borderColor  = f === 'no_interesa'   ? '#ef4444' : '#1e293b'; noBtn.style.color  = f === 'no_interesa'   ? '#fff' : '#64748b'; }
  loadCola();
}
```

- [ ] **Paso 2: Actualizar `loadCola()` para usar `_colaFilter`**

Buscar exactamente:
```javascript
    const r = await fetch('/api/leads?crm_status=sin_contactar');
    const data = await r.json();
    _colaLeads = Array.isArray(data) ? data : (data.items || []);
    renderCola();
```
Reemplazar con:
```javascript
    const r = await fetch(`/api/leads?crm_status=${_colaFilter}`);
    const data = await r.json();
    _colaLeads = Array.isArray(data) ? data : (data.items || []);
    renderCola();
```

- [ ] **Paso 3: Actualizar `markContacted` — usar `interesado`**

Buscar exactamente:
```javascript
async function markContacted(id) {
  await fetch(`/api/leads/${id}/crm-status`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({crm_status:'contactado'})});
  loadStats(); loadLeads();
}
```
Reemplazar con:
```javascript
async function markContacted(id) {
  await fetch(`/api/leads/${id}/crm-status`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({crm_status:'interesado'})});
  loadStats(); loadLeads();
}
```

- [ ] **Paso 4: Actualizar `logCallOutcome` — fix `no_interesa` y limpiar `contestó`**

Buscar exactamente:
```javascript
async function logCallOutcome(outcome) {
  if (!_callLeadId) return;
  const notes = document.getElementById('call-notes-input').value;
  await fetch(`/api/leads/${_callLeadId}/calls`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({outcome, notes})});
  if (outcome === 'contestó') {
    await fetch(`/api/leads/${_callLeadId}/crm-status`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({crm_status:'contactado'})});
  } else if (outcome === 'no_interesa' && _callActivePanel === 'seguimientos') {
    await fetch(`/api/leads/${_callLeadId}/crm-status`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({crm_status:'sin_contactar'})});
  } else if (outcome === 'reunion') {
    await fetch(`/api/leads/${_callLeadId}/crm-status`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({crm_status:'reunion_agendada'})});
  }
  closeCallModal();
  _reloadActiveCallPanel();
}
```
Reemplazar con:
```javascript
async function logCallOutcome(outcome) {
  if (!_callLeadId) return;
  const notes = document.getElementById('call-notes-input').value;
  await fetch(`/api/leads/${_callLeadId}/calls`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({outcome, notes})});
  if (outcome === 'no_interesa') {
    await fetch(`/api/leads/${_callLeadId}/crm-status`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({crm_status:'no_interesa'})});
  } else if (outcome === 'reunion') {
    await fetch(`/api/leads/${_callLeadId}/crm-status`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({crm_status:'reunion_agendada'})});
  }
  closeCallModal();
  _reloadActiveCallPanel();
}
```

- [ ] **Paso 5: Agregar `_callbackOutcome` y función `setCallbackOutcome`**

Buscar exactamente:
```javascript
function toggleCallbackRow() {
  const row = document.getElementById('callback-row');
  row.style.display = row.style.display === 'none' ? 'block' : 'none';
}
```
Reemplazar con:
```javascript
let _callbackOutcome = 'llamar_despues';
function setCallbackOutcome(outcome) {
  _callbackOutcome = outcome;
  const row = document.getElementById('callback-row');
  row.style.display = 'block';
}
function toggleCallbackRow() {
  const row = document.getElementById('callback-row');
  row.style.display = row.style.display === 'none' ? 'block' : 'none';
}
```

- [ ] **Paso 6: Actualizar los botones del call modal — separar "Interesado" y "Llamar después"**

Buscar exactamente:
```html
      <button class="outcome-btn outcome-callback" onclick="toggleCallbackRow()"><i data-lucide="clock" class="outcome-icon"></i><span style="font-size:.75rem">Llamar después</span></button>
      <button class="outcome-btn outcome-interested" onclick="toggleCallbackRow()"><i data-lucide="star" class="outcome-icon"></i><span style="font-size:.75rem">Interesado</span></button>
```
Reemplazar con:
```html
      <button class="outcome-btn outcome-callback" onclick="setCallbackOutcome('llamar_despues')"><i data-lucide="clock" class="outcome-icon"></i><span style="font-size:.75rem">Llamar después</span></button>
      <button class="outcome-btn outcome-interested" onclick="setCallbackOutcome('interesado')"><i data-lucide="star" class="outcome-icon"></i><span style="font-size:.75rem">Interesado</span></button>
```

- [ ] **Paso 7: Actualizar `confirmCallback` — enviar `outcome`**

Buscar exactamente:
```javascript
async function confirmCallback() {
  if (!_callLeadId) return;
  const date = document.getElementById('callback-date-input').value;
  if (!date) { alert('Elegí una fecha'); return; }
  const notes = document.getElementById('call-notes-input').value;
  await fetch(`/api/leads/${_callLeadId}/callback`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({callback_date:date, notes})});
  closeCallModal();
  _reloadActiveCallPanel();
}
```
Reemplazar con:
```javascript
async function confirmCallback() {
  if (!_callLeadId) return;
  const date = document.getElementById('callback-date-input').value;
  if (!date) { alert('Elegí una fecha'); return; }
  const notes = document.getElementById('call-notes-input').value;
  await fetch(`/api/leads/${_callLeadId}/callback`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({callback_date:date, notes, outcome:_callbackOutcome})});
  closeCallModal();
  _reloadActiveCallPanel();
}
```

- [ ] **Paso 8: Actualizar `loadSeguimientos` — `contactado` → `interesado`**

Buscar exactamente:
```javascript
    const [r1, r2] = await Promise.all([
      fetch('/api/leads?crm_status=llamar_despues'),
      fetch('/api/leads?crm_status=contactado'),
    ]);
```
Reemplazar con:
```javascript
    const [r1, r2] = await Promise.all([
      fetch('/api/leads?crm_status=llamar_despues'),
      fetch('/api/leads?crm_status=interesado'),
    ]);
```

Buscar exactamente:
```javascript
      const isContactado = b.crm_status === 'contactado';
```
Reemplazar con:
```javascript
      const isContactado = b.crm_status === 'interesado';
```

Buscar exactamente:
```javascript
        pillClass = 'cb-date-future'; pillLabel = 'Contactado';
```
Reemplazar con:
```javascript
        pillClass = 'cb-date-future'; pillLabel = 'Interesado';
```

- [ ] **Paso 9: Actualizar `loadColaStats` — `contactado` → `interesado`**

Buscar exactamente:
```javascript
    const [segR, conR] = await Promise.all([
      fetch('/api/leads?crm_status=llamar_despues'),
      fetch('/api/leads?crm_status=contactado'),
    ]);
```
Reemplazar con:
```javascript
    const [segR, conR] = await Promise.all([
      fetch('/api/leads?crm_status=llamar_despues'),
      fetch('/api/leads?crm_status=interesado'),
    ]);
```

- [ ] **Paso 10: Verificar sintaxis**

```bash
python -c "import ast; ast.parse(open('dashboard.py', encoding='utf-8-sig').read()); print('OK')"
```
Esperado: `OK`

- [ ] **Paso 11: Commit**

```bash
git add dashboard.py
git commit -m "feat: call modal separates Interesado/Llamar-despues, Cola filter, no_interesa fixed, loadSeguimientos updated"
```

---

## Task 5: Push a Railway

- [ ] **Paso 1: Push**

```bash
git push origin main
```

- [ ] **Paso 2: Verificar en el browser tras ~1 min de deploy**

1. Nav muestra "Proceso de venta" en vez de "Pipeline"
2. Cola tiene botones "Sin contactar" / "No interesa" — al clickear "No interesa" se muestran leads con ese estado
3. "Contactar" en Cola → lead va a Seguimientos como "Interesado" (borde verde)
4. Modal de llamada: "Interesado" + fecha → lead en Seguimientos como "Interesado"
5. Modal de llamada: "Llamar después" + fecha → lead en Seguimientos con fecha
6. "No le interesa" en el modal desde Cola → lead pasa a filtro "No interesa" de Cola
7. "No le interesa" en el modal desde Seguimientos → lead también pasa a "No interesa"
8. Seguimientos ya no mezcla "Contactado" — solo "Interesado" + "Llamar después"
9. Métricas: funnel muestra "Interesado" en vez de "Contactado"
