# Task Goals Overhaul — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ampliar el sistema de metas automáticas de tareas a 7 goal_types, compartir crédito entre todos los contributors de un lead, arreglar el bug del batch update, y mostrar historial de qué disparó cada incremento.

**Architecture:** Se modifica `database.py` para cambiar la firma de `increment_task_progress` (pasa de 1 user_id a lista) y agregar tabla `task_progress_events`. Cada route que dispara un goal usa `get_lead_contributor_ids` para obtener todos los contribuyentes del lead. El historial se sirve desde un nuevo endpoint en `routes/tasks.py` y se muestra en la card de tarea en `dashboard.py`.

**Tech Stack:** Python/Flask, SQLite, HTML/CSS/JS vanilla.

---

## File Map

| Archivo | Cambios |
|---|---|
| `database.py` | Nueva tabla `task_progress_events`, nueva firma `increment_task_progress`, nuevas funciones `get_lead_contributor_ids` y `get_task_progress_history` |
| `routes/leads.py` | Actualizar `api_crm_status`, `api_batch_status`, `api_contact`, `api_add_call` |
| `routes/budgets.py` | Agregar `presupuestos_enviados` en `api_mark_budget_sent` |
| `routes/calendar.py` | Actualizar `reuniones_agendadas` con contributors |
| `routes/tasks.py` | Nuevo endpoint `GET /api/tasks/<id>/progress-history` |
| `dashboard.py` | Modal: 7 opciones en select; JS: `goalTypeLabel`, barra de progreso con historial desplegable |

---

## Task 1: database.py — nueva tabla, nuevas funciones, firma actualizada

**Files:**
- Modify: `database.py`

- [ ] **Paso 1: Agregar tabla `task_progress_events` en `init_db`**

Buscar en `database.py` el bloque `# ── tasks` (alrededor de línea 153) y agregar justo después del bloque `CREATE TABLE IF NOT EXISTS tasks`:

```python
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_progress_events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id      INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                user_id      INTEGER,
                lead_id      INTEGER REFERENCES businesses(id) ON DELETE SET NULL,
                lead_name    TEXT,
                event_type   TEXT NOT NULL,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
```

- [ ] **Paso 2: Agregar `get_lead_contributor_ids` justo antes de `increment_task_progress`**

```python
def get_lead_contributor_ids(db_path: str, lead_id: int) -> list[int]:
    """Return unique user_ids from activity_log for a given lead (excludes NULLs)."""
    if not lead_id:
        return []
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT user_id FROM activity_log "
            "WHERE entity_id = ? AND entity_type = 'lead' AND user_id IS NOT NULL",
            (lead_id,),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()
```

- [ ] **Paso 3: Reemplazar `increment_task_progress` completa**

Buscar la función existente (línea ~840) y reemplazarla con:

```python
def increment_task_progress(
    db_path: str,
    user_ids: list[int],
    goal_type: str,
    lead_id: int | None = None,
    lead_name: str = "",
) -> list[int]:
    """Increment progress for all user_ids on tasks matching goal_type.
    Logs each increment to task_progress_events. Returns newly completed task IDs."""
    unique_ids = list({uid for uid in (user_ids or []) if uid})
    if not unique_ids:
        return []
    conn = _connect(db_path)
    all_completed: list[int] = []
    try:
        for uid in unique_ids:
            conn.execute(
                "UPDATE tasks SET progress = COALESCE(progress, 0) + 1 "
                "WHERE assignee_id = ? AND goal_type = ? AND status != 'done'",
                (uid, goal_type),
            )
            # Log events for every task that was just incremented
            active = conn.execute(
                "SELECT id FROM tasks WHERE assignee_id = ? AND goal_type = ? AND status != 'done'",
                (uid, goal_type),
            ).fetchall()
            for row in active:
                conn.execute(
                    "INSERT INTO task_progress_events "
                    "(task_id, user_id, lead_id, lead_name, event_type) VALUES (?, ?, ?, ?, ?)",
                    (row[0], uid, lead_id, lead_name or "", goal_type),
                )
            # Auto-complete tasks that reached their goal
            completed = [
                r[0] for r in conn.execute(
                    "SELECT id FROM tasks WHERE assignee_id = ? AND goal_type = ? "
                    "AND goal IS NOT NULL AND COALESCE(progress, 0) >= goal AND status != 'done'",
                    (uid, goal_type),
                ).fetchall()
            ]
            if completed:
                conn.execute(
                    f"UPDATE tasks SET status = 'done' "
                    f"WHERE id IN ({','.join('?' * len(completed))})",
                    completed,
                )
            all_completed.extend(completed)
        conn.commit()
        return all_completed
    finally:
        conn.close()
```

- [ ] **Paso 4: Agregar `get_task_progress_history` al final del bloque de funciones de tasks**

Agregar justo después de `delete_task`:

```python
def get_task_progress_history(db_path: str, task_id: int, limit: int = 50) -> list[dict]:
    """Return the last `limit` progress events for a task, newest first."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM task_progress_events WHERE task_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
```

- [ ] **Paso 5: Verificar sintaxis**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "import ast; ast.parse(open('database.py', encoding='utf-8-sig').read()); print('OK')"
```

Esperado: `OK` (puede haber un SyntaxWarning de `\d`, es pre-existente e inofensivo)

- [ ] **Paso 6: Commit**

```bash
git add database.py
git commit -m "feat: task_progress_events table, get_lead_contributor_ids, updated increment_task_progress"
```

---

## Task 2: routes/leads.py — fix batch + nuevos goal_types en CRM y llamadas

**Files:**
- Modify: `routes/leads.py`

Contexto: `get_lead_contributor_ids` ya está disponible en `database.py`. Hay que importarla y usarla en cada endpoint que dispara progreso.

- [ ] **Paso 1: Agregar imports nuevos**

Buscar la línea de imports de database (línea ~9):
```python
from database import (get_all_businesses, update_business, delete_business, get_business,
                      get_client_info, insert_business,
                      add_attachment, get_attachments, get_attachment_file, delete_attachment,
                      add_lead_event, get_lead_events,
                      add_call_log, get_call_logs,
                      increment_task_progress, log_activity)
```

Reemplazar con:
```python
from database import (get_all_businesses, update_business, delete_business, get_business,
                      get_client_info, insert_business,
                      add_attachment, get_attachments, get_attachment_file, delete_attachment,
                      add_lead_event, get_lead_events,
                      add_call_log, get_call_logs,
                      increment_task_progress, get_lead_contributor_ids, log_activity)
```

- [ ] **Paso 2: Agregar helper `_contributors` local**

Agregar justo después de la definición de `leads_bp` (después de la línea `leads_bp = Blueprint("leads", __name__)`):

```python
def _contributors(db: str, lead_id: int, current_uid: int | None) -> list[int]:
    ids = set(get_lead_contributor_ids(db, lead_id))
    if current_uid:
        ids.add(current_uid)
    return list(ids)
```

- [ ] **Paso 3: Actualizar `api_crm_status` — agregar `reuniones_hechas` y `clientes_cerrados`**

Buscar:
```python
    if crm_status == "contactado":
        increment_task_progress(db, session.get("user_id"), "leads_contactados")
    return jsonify({"ok": True})
```

Reemplazar con:
```python
    _STATUS_TO_GOAL = {
        "contactado":      "leads_contactados",
        "reunion_hecha":   "reuniones_hechas",
        "cliente_cerrado": "clientes_cerrados",
    }
    if crm_status in _STATUS_TO_GOAL:
        uids = _contributors(db, biz_id, session.get("user_id"))
        increment_task_progress(db, uids, _STATUS_TO_GOAL[crm_status],
                                lead_id=biz_id, lead_name=biz.get("name", ""))
    return jsonify({"ok": True})
```

- [ ] **Paso 4: Arreglar `api_batch_status` — agregar progress tracking**

Buscar:
```python
    log_activity(db, user_name, "batch_status", "", None, "",
                 f"{len(ids)} leads → {crm_status}", user_id=session.get("user_id"))
    return jsonify({"ok": True, "updated": len(ids)})
```

Reemplazar con:
```python
    log_activity(db, user_name, "batch_status", "", None, "",
                 f"{len(ids)} leads → {crm_status}", user_id=session.get("user_id"))
    _STATUS_TO_GOAL_BATCH = {
        "contactado":      "leads_contactados",
        "reunion_hecha":   "reuniones_hechas",
        "cliente_cerrado": "clientes_cerrados",
    }
    if crm_status in _STATUS_TO_GOAL_BATCH:
        goal = _STATUS_TO_GOAL_BATCH[crm_status]
        for bid in ids:
            bid = int(bid)
            biz_name = (get_business(db, bid) or {}).get("name", "")
            uids = _contributors(db, bid, session.get("user_id"))
            increment_task_progress(db, uids, goal, lead_id=bid, lead_name=biz_name)
    return jsonify({"ok": True, "updated": len(ids)})
```

- [ ] **Paso 5: Actualizar `api_contact` — pasar contributors**

Buscar:
```python
    increment_task_progress(db, session.get("user_id"), "leads_contactados")
    return jsonify({"ok": True})
```

(La que está en `api_contact`, línea ~199)

Reemplazar con:
```python
    uids = _contributors(db, biz_id, session.get("user_id"))
    increment_task_progress(db, uids, "leads_contactados",
                            lead_id=biz_id, lead_name=biz.get("name", ""))
    return jsonify({"ok": True})
```

- [ ] **Paso 6: Actualizar `api_add_call` — agregar `llamadas_realizadas` y `llamadas_contestadas`**

Buscar en `api_add_call` (línea ~524):
```python
    add_call_log(db, biz_id, outcome, notes, created_by)
    log_activity(db, created_by, "call_logged", "lead", biz_id, biz.get("name", ""), outcome,
                 user_id=session.get("user_id"))
    return jsonify({"ok": True}), 201
```

Reemplazar con:
```python
    add_call_log(db, biz_id, outcome, notes, created_by)
    log_activity(db, created_by, "call_logged", "lead", biz_id, biz.get("name", ""), outcome,
                 user_id=session.get("user_id"))
    uids = _contributors(db, biz_id, session.get("user_id"))
    increment_task_progress(db, uids, "llamadas_realizadas",
                            lead_id=biz_id, lead_name=biz.get("name", ""))
    if outcome == "contestó":
        increment_task_progress(db, uids, "llamadas_contestadas",
                                lead_id=biz_id, lead_name=biz.get("name", ""))
    return jsonify({"ok": True}), 201
```

- [ ] **Paso 7: Verificar sintaxis**

```bash
python -c "import ast; ast.parse(open('routes/leads.py', encoding='utf-8-sig').read()); print('OK')"
```

Esperado: `OK`

- [ ] **Paso 8: Commit**

```bash
git add routes/leads.py
git commit -m "feat: batch fix + leads_contactados/reuniones_hechas/clientes_cerrados/llamadas with shared credit"
```

---

## Task 3: routes/budgets.py + routes/calendar.py — presupuestos y reuniones con contributors

**Files:**
- Modify: `routes/budgets.py`
- Modify: `routes/calendar.py`

- [ ] **Paso 1: Actualizar imports de `budgets.py`**

Buscar la sección `from database import (` en `routes/budgets.py` y agregar `increment_task_progress, get_lead_contributor_ids` a la lista. Quedaría algo como:

```python
from database import (
    create_budget,
    get_budget_by_id,
    get_budget_for_client,
    get_business,
    update_budget,
    log_activity,
    increment_task_progress,
    get_lead_contributor_ids,
)
```

(Mantener todos los imports actuales, solo agregar los dos nuevos al final de la lista)

- [ ] **Paso 2: Agregar helper `_contributors` en `budgets.py`**

Agregar justo después de `def _db() -> str:` en `routes/budgets.py`:

```python
def _contributors(db: str, lead_id: int, current_uid: int | None) -> list[int]:
    ids = set(get_lead_contributor_ids(db, lead_id))
    if current_uid:
        ids.add(current_uid)
    return list(ids)
```

- [ ] **Paso 3: Actualizar `api_mark_budget_sent` en `budgets.py`**

Buscar:
```python
    if budget:
        client = get_business(db, budget["client_id"]) or {}
        log_activity(db, session.get("user_name", "sistema"), "budget_sent",
                     "lead", budget["client_id"], client.get("name", ""), "",
                     user_id=session.get("user_id"))
    return jsonify({"ok": True})
```

Reemplazar con:
```python
    if budget:
        client = get_business(db, budget["client_id"]) or {}
        client_id = budget["client_id"]
        client_name = client.get("name", "")
        log_activity(db, session.get("user_name", "sistema"), "budget_sent",
                     "lead", client_id, client_name, "",
                     user_id=session.get("user_id"))
        uids = _contributors(db, client_id, session.get("user_id"))
        increment_task_progress(db, uids, "presupuestos_enviados",
                                lead_id=client_id, lead_name=client_name)
    return jsonify({"ok": True})
```

- [ ] **Paso 4: Actualizar imports de `calendar.py`**

Buscar la línea que importa `increment_task_progress` en `routes/calendar.py`:
```python
    increment_task_progress,
```
Agregar `get_lead_contributor_ids,` justo debajo.

- [ ] **Paso 5: Agregar helper `_contributors` en `calendar.py`**

Agregar justo después de `def _db() -> str:` en `routes/calendar.py`:

```python
def _contributors(db: str, lead_id: int, current_uid: int | None) -> list[int]:
    ids = set(get_lead_contributor_ids(db, lead_id))
    if current_uid:
        ids.add(current_uid)
    return list(ids)
```

- [ ] **Paso 6: Actualizar `increment_task_progress` en `calendar.py`**

Buscar en `routes/calendar.py`:
```python
            increment_task_progress(db, session.get("user_id"), "reuniones_agendadas")
```

Reemplazar con:
```python
            uids = _contributors(db, int(client_id), session.get("user_id"))
            increment_task_progress(db, uids, "reuniones_agendadas",
                                    lead_id=int(client_id), lead_name=client.get("name", ""))
```

- [ ] **Paso 7: Verificar sintaxis de ambos archivos**

```bash
python -c "import ast; ast.parse(open('routes/budgets.py', encoding='utf-8-sig').read()); print('budgets OK')"
python -c "import ast; ast.parse(open('routes/calendar.py', encoding='utf-8-sig').read()); print('calendar OK')"
```

Esperado: `budgets OK` y `calendar OK`

- [ ] **Paso 8: Commit**

```bash
git add routes/budgets.py routes/calendar.py
git commit -m "feat: presupuestos_enviados and reuniones_agendadas with shared contributor credit"
```

---

## Task 4: routes/tasks.py — endpoint GET /api/tasks/<id>/progress-history

**Files:**
- Modify: `routes/tasks.py`

- [ ] **Paso 1: Actualizar imports en `tasks.py`**

Buscar:
```python
from database import create_task, delete_task, get_task_by_id, get_tasks, log_activity, update_task
```

Reemplazar con:
```python
import os
from database import (create_task, delete_task, get_task_by_id, get_tasks,
                      log_activity, update_task, get_task_progress_history)
```

- [ ] **Paso 2: Agregar el endpoint al final de `tasks.py`**

```python
@tasks_bp.route("/api/tasks/<int:task_id>/progress-history")
def api_task_progress_history(task_id):
    db = _db()
    task = get_task_by_id(db, task_id)
    if not task:
        return jsonify({"error": "Not found"}), 404
    uid = session.get("user_id")
    # Allow assignee, creator, or admin
    admin_email = os.environ.get("ADMIN_EMAIL", "")
    import sqlite3 as _sq
    conn2 = _sq.connect(db); conn2.row_factory = _sq.Row
    try:
        u = conn2.execute("SELECT id, email FROM users WHERE id=?", (uid,)).fetchone()
    finally:
        conn2.close()
    is_admin = bool(
        u and (
            (admin_email and u["email"].lower() == admin_email.lower())
            or (not admin_email and u["id"] == 1)
        )
    )
    if not is_admin and task.get("assignee_id") != uid and task.get("created_by_id") != uid:
        return jsonify({"error": "No autorizado"}), 403
    return jsonify(get_task_progress_history(db, task_id))
```

- [ ] **Paso 3: Verificar sintaxis**

```bash
python -c "import ast; ast.parse(open('routes/tasks.py', encoding='utf-8-sig').read()); print('OK')"
```

Esperado: `OK`

- [ ] **Paso 4: Commit**

```bash
git add routes/tasks.py
git commit -m "feat: GET /api/tasks/<id>/progress-history endpoint"
```

---

## Task 5: dashboard.py — modal con 7 opciones + barra de progreso con historial

**Files:**
- Modify: `dashboard.py`

- [ ] **Paso 1: Actualizar el `<select>` de goal_type en el modal**

Buscar exactamente:
```html
        <select id="task-goal-type-input" class="modal-input" style="flex:2" onchange="_onTaskGoalTypeChange()">
          <option value="">Sin meta automática</option>
          <option value="reuniones_agendadas">Reuniones agendadas</option>
          <option value="leads_contactados">Leads contactados</option>
        </select>
```

Reemplazar con:
```html
        <select id="task-goal-type-input" class="modal-input" style="flex:2" onchange="_onTaskGoalTypeChange()">
          <option value="">Sin meta automática</option>
          <option value="leads_contactados">Leads contactados</option>
          <option value="llamadas_realizadas">Llamadas realizadas</option>
          <option value="llamadas_contestadas">Llamadas contestadas</option>
          <option value="reuniones_agendadas">Reuniones agendadas</option>
          <option value="reuniones_hechas">Reuniones hechas</option>
          <option value="presupuestos_enviados">Presupuestos enviados</option>
          <option value="clientes_cerrados">Clientes cerrados</option>
        </select>
```

- [ ] **Paso 2: Actualizar `goalTypeLabel` en `_taskRowHtml`**

Buscar exactamente:
```javascript
  const goalTypeLabel = {'reuniones_agendadas':'reuniones agendadas','leads_contactados':'leads contactados'};
```

Reemplazar con:
```javascript
  const goalTypeLabel = {
    'leads_contactados':    'leads contactados',
    'llamadas_realizadas':  'llamadas realizadas',
    'llamadas_contestadas': 'llamadas contestadas',
    'reuniones_agendadas':  'reuniones agendadas',
    'reuniones_hechas':     'reuniones hechas',
    'presupuestos_enviados':'presupuestos enviados',
    'clientes_cerrados':    'clientes cerrados',
  };
```

- [ ] **Paso 3: Reemplazar `progressBar` para que sea clickeable con historial desplegable**

Buscar exactamente:
```javascript
  const progressBar = t.goal ? `
    <div style="margin-top:6px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:3px">
        <span style="font-size:.72rem;color:#64748b">${goalTypeLabel[t.goal_type]||t.goal_type}: </span>
        <span style="font-size:.72rem;font-weight:600;color:${done||pct>=100?'#10b981':'#e2e8f0'}">${progress}/${t.goal}</span>
        ${pct >= 100 ? '<span style="font-size:.68rem;color:#10b981">✓ Meta alcanzada</span>' : ''}
      </div>
      <div style="height:4px;background:#1e293b;border-radius:2px;overflow:hidden;max-width:240px">
        <div style="height:100%;width:${pct}%;background:${pct>=100?'#10b981':'#0088cc'};transition:width .3s"></div>
      </div>
    </div>` : '';
```

Reemplazar con:
```javascript
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
```

- [ ] **Paso 4: Agregar función `_toggleTaskHistory` en el JS**

Buscar la función `_onTaskGoalTypeChange`:
```javascript
function _onTaskGoalTypeChange() {
```

Agregar ANTES de esa función:
```javascript
async function _toggleTaskHistory(taskId) {
  const el = document.getElementById(`task-history-${taskId}`);
  if (!el) return;
  if (el.style.display !== 'none') { el.style.display = 'none'; return; }
  el.innerHTML = '<div style="font-size:.72rem;color:#475569;padding:2px 0">Cargando...</div>';
  el.style.display = '';
  try {
    const r = await fetch(`/api/tasks/${taskId}/progress-history`);
    const items = await r.json();
    if (!Array.isArray(items) || !items.length) {
      el.innerHTML = '<div style="font-size:.72rem;color:#475569;padding:2px 0">Sin historial aún</div>';
      return;
    }
    el.innerHTML = items.slice(0, 50).map(i => {
      const d = new Date(i.created_at);
      const dStr = d.toLocaleDateString('es-UY',{day:'2-digit',month:'2-digit'})
                 + ' ' + d.toLocaleTimeString('es-UY',{hour:'2-digit',minute:'2-digit'});
      return `<div style="display:flex;gap:8px;align-items:baseline;padding:2px 0;font-size:.72rem">
        <span style="color:#10b981;font-weight:700;min-width:20px">+1</span>
        <span style="color:#94a3b8;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${i.lead_name ? esc(i.lead_name) : '—'}</span>
        <span style="color:#475569;white-space:nowrap">${dStr}</span>
      </div>`;
    }).join('');
  } catch(e) {
    el.innerHTML = '<div style="font-size:.72rem;color:#f87171;padding:2px 0">Error cargando historial</div>';
  }
}

```

- [ ] **Paso 5: Verificar sintaxis Python del archivo**

```bash
python -c "import ast; ast.parse(open('dashboard.py', encoding='utf-8-sig').read()); print('OK')"
```

Esperado: `OK` (puede haber SyntaxWarning de `\d`, ignorar)

- [ ] **Paso 6: Commit**

```bash
git add dashboard.py
git commit -m "feat: task modal with 7 goal_types, progress bar with clickable history panel"
```

---

## Task 6: Push a Railway

**Files:** ninguno

- [ ] **Paso 1: Push**

```bash
git push origin main
```

- [ ] **Paso 2: Verificar en el browser**

1. Crear una tarea con meta "Llamadas realizadas" = 5, asignada a un usuario
2. Loguear una llamada para cualquier lead → la tarea debe mostrar 1/5
3. Hacer click en "▾ historial" → debe aparecer el nombre del lead y la hora
4. Cambiar estado de un lead a "cliente_cerrado" en batch con 2 leads → ambos cuentan
5. Confirmar que un usuario que tocó el lead previamente también recibe crédito
6. Marcar un presupuesto como enviado → suma en tareas de `presupuestos_enviados`
