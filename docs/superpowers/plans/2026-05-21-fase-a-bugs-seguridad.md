# Fase A: Bugs Críticos y Seguridad — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corregir 8 bugs críticos y vulnerabilidades de seguridad en el CRM Scalerics sin romper funcionalidad existente.

**Architecture:** Fixes quirúrgicos en archivos existentes. Sin nuevas abstracciones. Sin refactors. Cada fix es independiente y se puede aplicar y revertir por separado.

**Tech Stack:** Python/Flask, SQLite, werkzeug, secrets (stdlib)

---

## File Map

| Archivo | Fixes |
|---|---|
| `database.py` | Fix 1: SQL NULLS LAST |
| `routes/leads.py` | Fix 2: path traversal en uploads |
| `dashboard.py` | Fix 3: timing attack password, Fix 6: parseInt NaN |
| `routes/budgets.py` | Fix 4: error interno expuesto, Fix 8: JSON silent error |
| `routes/wa.py` | Fix 5: leads duplicados del bot |
| `routes/calendar.py` | Fix 7: estado del lead al borrar reunión |

---

### Task 1: Fix SQL NULLS LAST (database.py:283)

SQLite no soporta `NULLS LAST`. Causa: leads sin score aparecen primero en lugar de al final.

**Files:**
- Modify: `database.py:283`

- [ ] **Step 1: Localizar la línea exacta**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "
data = open('database.py').read()
print('NULLS LAST' in data)
"
```
Esperado: `True`

- [ ] **Step 2: Aplicar el fix**

En `database.py`, encontrar:
```python
cursor = conn.execute("SELECT * FROM businesses ORDER BY score DESC NULLS LAST, scraped_at DESC")
```
Reemplazar con:
```python
cursor = conn.execute("SELECT * FROM businesses ORDER BY CASE WHEN score IS NULL THEN 1 ELSE 0 END, score DESC, scraped_at DESC")
```

- [ ] **Step 3: Verificar sintaxis y que la query corre**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "
import sqlite3, os
from dotenv import load_dotenv
load_dotenv()
db = os.environ.get('DB_PATH', 'leads.db')
if not os.path.exists(db):
    print('DB not found, creating temp...')
    con = sqlite3.connect(':memory:')
    con.execute('CREATE TABLE businesses (id INTEGER PRIMARY KEY, score REAL, scraped_at TEXT)')
    con.execute('INSERT INTO businesses VALUES (1, 5.0, \"2026-01-01\")')
    con.execute('INSERT INTO businesses VALUES (2, NULL, \"2026-01-02\")')
    cur = con.execute('SELECT * FROM businesses ORDER BY CASE WHEN score IS NULL THEN 1 ELSE 0 END, score DESC, scraped_at DESC')
    print([dict(zip([c[0] for c in cur.description], row)) for row in cur.fetchall()])
else:
    from database import get_all_businesses
    leads = get_all_businesses(db)
    print(f'OK - {len(leads)} leads loaded')
"
```
Esperado: sin error SQLite, leads con score primero.

- [ ] **Step 4: Commit**

```bash
git add database.py
git commit -m "fix: replace NULLS LAST with SQLite-compatible ORDER BY"
```

---

### Task 2: Fix path traversal en file upload (routes/leads.py:273)

El filename del archivo viene del cliente sin sanitizar. Un atacante podría subir `../../app.py`.

**Files:**
- Modify: `routes/leads.py:273`

- [ ] **Step 1: Verificar que werkzeug está disponible**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "from werkzeug.utils import secure_filename; print('OK')"
```
Esperado: `OK` (werkzeug es dependencia de Flask, siempre disponible)

- [ ] **Step 2: Agregar el import en routes/leads.py**

Al inicio de `routes/leads.py`, en el bloque de imports, agregar:
```python
from werkzeug.utils import secure_filename
```

- [ ] **Step 3: Sanitizar el filename**

En `routes/leads.py`, encontrar:
```python
    name = f.filename or "archivo"
```
Reemplazar con:
```python
    name = secure_filename(f.filename) or "archivo"
```

- [ ] **Step 4: Verificar manualmente**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "
from werkzeug.utils import secure_filename
casos = ['../../etc/passwd', 'mi archivo.pdf', '../secret.txt', 'normal.jpg', '']
for c in casos:
    result = secure_filename(c) or 'archivo'
    print(f'{repr(c):30} -> {repr(result)}')
"
```
Esperado: paths con `..` quedan como nombre de archivo plano, sin slashes.

- [ ] **Step 5: Commit**

```bash
git add routes/leads.py
git commit -m "fix: sanitize upload filename with secure_filename to prevent path traversal"
```

---

### Task 3: Fix timing attack en comparación de contraseña (dashboard.py:2421)

`password == expected` revela información de tiempo. Reemplazar con comparación de tiempo constante.

**Files:**
- Modify: `dashboard.py:2421`

- [ ] **Step 1: Agregar import de secrets**

En `dashboard.py`, buscar el bloque de imports al inicio del archivo. Agregar:
```python
import secrets
```
(Ya hay `import os` — poner `import secrets` junto a los imports de stdlib)

- [ ] **Step 2: Reemplazar la comparación**

Encontrar en `dashboard.py`:
```python
            if password == expected:
```
Reemplazar con:
```python
            if expected and secrets.compare_digest(password, expected):
```

Nota: el check `if expected and` es necesario para mantener el comportamiento existente: si DASHBOARD_PASSWORD no está seteada, el login es libre (líneas anteriores ya lo manejan, pero es buena práctica ser explícito).

- [ ] **Step 3: Verificar que el login sigue funcionando**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "
import secrets
pw = 'scalerics'
expected = 'scalerics'
wrong = 'incorrecto'
print('Correcto:', expected and secrets.compare_digest(pw, expected))
print('Incorrecto:', expected and secrets.compare_digest(wrong, expected))
"
```
Esperado: `True` / `False`

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "fix: use secrets.compare_digest for constant-time password comparison"
```

---

### Task 4: Fix errores internos expuestos al cliente (routes/budgets.py:199)

`str(e)` en la respuesta JSON expone stack traces y detalles internos.

**Files:**
- Modify: `routes/budgets.py:199`

- [ ] **Step 1: Localizar todas las ocurrencias de str(e) en respuestas JSON**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "
import re
content = open('routes/budgets.py').read()
lines = content.split('\n')
for i, line in enumerate(lines, 1):
    if 'str(e)' in line or 'str(err)' in line:
        print(f'Línea {i}: {line.strip()}')
"
```

- [ ] **Step 2: Agregar import de logging al inicio de routes/budgets.py**

Al inicio de `routes/budgets.py`, agregar:
```python
import logging
logger = logging.getLogger(__name__)
```

- [ ] **Step 3: Reemplazar la línea 199**

Encontrar:
```python
        return jsonify({"ok": False, "error": f"Error generando presupuesto: {e}"}), 500
```
Reemplazar con:
```python
        logger.error("Error generando presupuesto para cliente %s: %s", client_id, e)
        return jsonify({"ok": False, "error": "Error generando presupuesto. Intentá de nuevo."}), 500
```

- [ ] **Step 4: Buscar y corregir cualquier otro str(e) en routes/**

```bash
cd C:\Users\juant\lead-gen-uy
grep -rn "str(e)" routes/ --include="*.py"
```

Para cada ocurrencia en una respuesta JSON, aplicar el mismo patrón: loguear con `logger.error(...)` y devolver mensaje genérico.

- [ ] **Step 5: Commit**

```bash
git add routes/budgets.py routes/
git commit -m "fix: replace str(e) in JSON responses with generic messages, log internally"
```

---

### Task 5: Fix leads duplicados desde bot webhook (routes/wa.py)

Aunque `get_business_by_phone` verifica múltiples formatos, un push concurrente puede crear dos registros. Agregar constraint UNIQUE en la columna phone.

**Files:**
- Modify: `database.py` (migration inline)
- Modify: `routes/wa.py` (INSERT con ON CONFLICT)

- [ ] **Step 1: Agregar UNIQUE constraint si no existe**

En `database.py`, buscar la función `init_db` o similar donde se crea la tabla `businesses`. Verificar si `phone` ya tiene UNIQUE:

```bash
cd C:\Users\juant\lead-gen-uy
python -c "
from dotenv import load_dotenv; import os, sqlite3
load_dotenv()
db = os.environ.get('DB_PATH', 'leads.db')
if os.path.exists(db):
    con = sqlite3.connect(db)
    info = con.execute(\"SELECT sql FROM sqlite_master WHERE name='businesses'\").fetchone()
    print(info[0] if info else 'tabla no encontrada')
"
```

Si `phone` no tiene UNIQUE, crear índice único:

```bash
cd C:\Users\juant\lead-gen-uy
python -c "
from dotenv import load_dotenv; import os, sqlite3
load_dotenv()
db = os.environ.get('DB_PATH', 'leads.db')
if os.path.exists(db):
    con = sqlite3.connect(db)
    try:
        con.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_businesses_phone ON businesses(phone)')
        con.commit()
        print('Índice creado')
    except Exception as e:
        print(f'Error (puede ya existir): {e}')
"
```

- [ ] **Step 2: Agregar migration en database.py para aplicar automáticamente en Railway**

En la función `init_db` de `database.py`, después de la creación de tablas, agregar:
```python
    # Ensure unique phone index (idempotent)
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_businesses_phone ON businesses(phone)")
        conn.commit()
    except Exception:
        pass
```

- [ ] **Step 3: Verificar que insert_business maneja conflictos**

En `database.py`, encontrar la función `insert_business`. Verificar cómo hace el INSERT. Si usa INSERT sin ON CONFLICT, el índice único hará que falle con IntegrityError en concurrent push.

```bash
cd C:\Users\juant\lead-gen-uy
python -c "
import re
content = open('database.py').read()
# Find insert_business function
idx = content.find('def insert_business')
print(content[idx:idx+400])
"
```

Si el INSERT no tiene `OR IGNORE`, en `routes/wa.py` el código ya envuelve el insert en un bloque que primero busca por teléfono. La race condition es mínima. El índice único es suficiente protección: el segundo insert fallará con IntegrityError y el handler devolverá 500, que el bot reintentará.

- [ ] **Step 4: Commit**

```bash
git add database.py
git commit -m "fix: add unique index on businesses.phone to prevent duplicate leads"
```

---

### Task 6: Fix parseInt NaN en selección de leads (dashboard.py:901)

`parseInt(cb.dataset.id)` devuelve NaN si dataset.id está vacío, enviando IDs inválidos a la API.

**Files:**
- Modify: `dashboard.py` (JS, línea ~901)

- [ ] **Step 1: Localizar el contexto completo**

En `dashboard.py`, buscar la función `toggleSelectAll`. La línea es:
```javascript
    const id = parseInt(cb.dataset.id);
```

- [ ] **Step 2: Agregar guard**

Reemplazar:
```javascript
function toggleSelectAll(checked) {
  document.querySelectorAll('.row-cb').forEach(cb => {
    cb.checked = checked;
    const id = parseInt(cb.dataset.id);
    checked ? selectedIds.add(id) : selectedIds.delete(id);
  });
  updateBatchBar();
}
```
Con:
```javascript
function toggleSelectAll(checked) {
  document.querySelectorAll('.row-cb').forEach(cb => {
    cb.checked = checked;
    const id = parseInt(cb.dataset.id);
    if (isNaN(id)) return;
    checked ? selectedIds.add(id) : selectedIds.delete(id);
  });
  updateBatchBar();
}
```

- [ ] **Step 3: Buscar otros parseInt sin guard en el mismo archivo**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "
content = open('dashboard.py').read()
lines = content.split('\n')
for i, line in enumerate(lines, 1):
    if 'parseInt(' in line and 'isNaN' not in lines[i] if i < len(lines) else True:
        print(f'{i}: {line.strip()}')
" 2>/dev/null | head -20
```

Para cada `parseInt` que procese IDs de usuario, agregar `if (isNaN(id)) return;` o `if (isNaN(id)) continue;` en el bloque siguiente.

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "fix: guard parseInt with isNaN check to prevent NaN IDs in batch operations"
```

---

### Task 7: Fix estado del lead al borrar reunión (routes/calendar.py)

Al borrar una reunión, el lead queda con estado `reunion_agendada` si no tiene más reuniones. Debe retroceder a `contactado`.

**Files:**
- Modify: `routes/calendar.py` — ambos endpoints DELETE

- [ ] **Step 1: Agregar import faltante en calendar.py**

Al inicio de `routes/calendar.py`, verificar que `get_meetings_for_client` está importado:
```python
from database import (
    create_meeting,
    delete_meeting,
    get_meeting,
    get_meetings_for_client,
    update_meeting,
)
```
Si falta `get_meetings_for_client`, agregarlo.

- [ ] **Step 2: Agregar helper function para revertir estado**

En `routes/calendar.py`, después de los imports y antes de la primera ruta, agregar:

```python
def _maybe_revert_lead_status(db_path: str, client_id: int) -> None:
    """If client has no remaining scheduled meetings, revert CRM status to 'contactado'."""
    if not client_id:
        return
    from database import get_business, update_business
    biz = get_business(db_path, client_id)
    if not biz:
        return
    if biz.get("crm_status") not in ("reunion_agendada",):
        return
    remaining = get_meetings_for_client(db_path, client_id)
    # Filter out the meeting being deleted (already removed from DB at call time)
    if not remaining:
        update_business(db_path, client_id, crm_status="contactado")
```

- [ ] **Step 3: Llamar al helper en api_delete_meeting**

En `api_delete_meeting`, después de `delete_meeting(_db(), meeting_id)`, agregar:
```python
    client_id = meeting.get("client_id")
    if client_id:
        _maybe_revert_lead_status(_db(), client_id)
```

El código completo del handler queda:
```python
@calendar_bp.route("/api/calendar/meetings/<int:meeting_id>", methods=["DELETE"])
def api_delete_meeting(meeting_id):
    meeting = get_meeting(_db(), meeting_id)
    if not meeting:
        return jsonify({"ok": False, "error": "Reunión no encontrada"}), 404

    cal_event_id = meeting.get("calendar_event_id")
    if cal_event_id:
        service, err = _get_calendar_service()
        if service:
            try:
                service.events().delete(
                    calendarId="primary",
                    eventId=cal_event_id,
                    sendUpdates="all",
                ).execute()
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"No se pudo cancelar evento en Calendar: {e}")

    delete_meeting(_db(), meeting_id)

    client_id = meeting.get("client_id")
    if client_id:
        _maybe_revert_lead_status(_db(), client_id)

    return jsonify({"ok": True})
```

- [ ] **Step 4: Llamar al helper en api_delete_cal_event**

En `api_delete_cal_event`, después del bloque que hace `DELETE FROM meetings`, recuperar el client_id y llamar al helper. El código completo del handler queda:

```python
@calendar_bp.route("/api/calendar/events/<string:cal_event_id>", methods=["DELETE"])
def api_delete_cal_event(cal_event_id):
    service, err = _get_calendar_service()
    if service:
        try:
            service.events().delete(
                calendarId="primary",
                eventId=cal_event_id,
                sendUpdates="all",
            ).execute()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"No se pudo borrar evento {cal_event_id} en Calendar: {e}")

    import sqlite3
    db = _db()
    client_id = None
    try:
        con = sqlite3.connect(db)
        row = con.execute("SELECT client_id FROM meetings WHERE calendar_event_id = ?", (cal_event_id,)).fetchone()
        client_id = row[0] if row else None
        con.execute("DELETE FROM meetings WHERE calendar_event_id = ?", (cal_event_id,))
        con.commit()
        con.close()
    except Exception:
        pass

    if client_id:
        _maybe_revert_lead_status(db, client_id)

    return jsonify({"ok": True})
```

- [ ] **Step 5: Verificar importaciones y sintaxis**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "from routes.calendar import calendar_bp; print('OK')"
```
Esperado: `OK`

- [ ] **Step 6: Commit**

```bash
git add routes/calendar.py
git commit -m "fix: revert lead status to contactado when last meeting is deleted"
```

---

### Task 8: Fix budget JSON silent error (routes/budgets.py)

Cuando el JSON de un presupuesto falla, el handler devuelve objeto vacío silenciosamente. Retornar error claro.

**Files:**
- Modify: `routes/budgets.py` (~línea 156)

- [ ] **Step 1: Localizar el bloque problemático**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "
content = open('routes/budgets.py').read()
lines = content.split('\n')
for i, line in enumerate(lines, 1):
    if 'json.loads' in line or ('except' in line and i > 140 and i < 200):
        print(f'{i}: {line}')
"
```

- [ ] **Step 2: Identificar el bloque y reemplazar**

Buscar el patrón donde el JSON parse falla silenciosamente. Típicamente:
```python
    try:
        budget_data = json.loads(...)
    except Exception:
        budget_data = {}
```

Reemplazar con:
```python
    try:
        budget_data = json.loads(...)
    except Exception as e:
        logger.error("Error parseando JSON de presupuesto: %s", e)
        return jsonify({"ok": False, "error": "Error leyendo datos del presupuesto"}), 400
```

- [ ] **Step 3: Verificar sintaxis**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "from routes.budgets import budgets_bp; print('OK')"
```
Esperado: `OK`

- [ ] **Step 4: Commit**

```bash
git add routes/budgets.py
git commit -m "fix: return 400 on budget JSON parse error instead of silent empty object"
```

---

### Task 9: Deploy a Railway

- [ ] **Step 1: Verificar que el servidor arranca limpio localmente**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "from dashboard import create_app; print('OK')"
```
Esperado: `OK` sin errores de importación.

- [ ] **Step 2: Deploy**

```bash
cd C:\Users\juant\lead-gen-uy
railway up --service web --detach
```

- [ ] **Step 3: Verificar en Railway logs**

```bash
railway logs --service web 2>&1 | head -30
```
Esperado: sin errores de startup, servidor corriendo.
