# Token Health Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mostrar en la pestaña Tareas un panel "Estado del sistema" con el tiempo restante hasta el vencimiento de cada token crítico (Gmail, Google Calendar, WhatsApp, Vercel).

**Architecture:** Un nuevo blueprint `routes/tokens.py` expone `GET /api/tokens/status`. Los setup scripts guardan timestamps de renovación en `.env` y Railway. El dashboard inyecta un panel HTML sobre la lista de tareas que llama a ese endpoint al cargar.

**Tech Stack:** Python/Flask, dotenv, base64 (JWT decode), Railway CLI (subprocess en setup scripts), vanilla JS

---

## File Map

| Archivo | Acción | Qué hace |
|---|---|---|
| `setup_gmail.py` | Modificar | Guarda `GMAIL_TOKEN_RENEWED_AT` al `.env` + Railway |
| `setup_calendar.py` | Modificar | Guarda `GCAL_TOKEN_RENEWED_AT` al `.env` + Railway |
| `routes/tokens.py` | Crear | Blueprint con `GET /api/tokens/status` |
| `dashboard.py` | Modificar | Importa tokens_bp, agrega HTML del panel y JS |

---

### Task 1: Guardar timestamp en setup_gmail.py

**Files:**
- Modify: `setup_gmail.py`

- [ ] **Step 1: Agregar guardado de timestamp**

Reemplazar el contenido completo de `setup_gmail.py` con:

```python
"""
Run this script ONCE to obtain the Gmail OAuth2 refresh token.
It opens a browser window for authentication and saves credentials to .env.
"""
import json
import subprocess
from datetime import datetime, timezone
from google_auth_oauthlib.flow import InstalledAppFlow
from dotenv import load_dotenv, set_key

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

def main():
    load_dotenv()
    flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
    creds = flow.run_local_server(port=0)

    with open("credentials.json") as f:
        client_config = json.load(f)["installed"]

    renewed_at = datetime.now(timezone.utc).isoformat()

    set_key(".env", "GMAIL_CLIENT_ID", client_config["client_id"])
    set_key(".env", "GMAIL_CLIENT_SECRET", client_config["client_secret"])
    set_key(".env", "GMAIL_REFRESH_TOKEN", creds.refresh_token)
    set_key(".env", "GMAIL_TOKEN_RENEWED_AT", renewed_at)

    print("Credenciales guardadas en .env")
    print(f"  CLIENT_ID:     {client_config['client_id'][:30]}...")
    print(f"  REFRESH_TOKEN: {creds.refresh_token[:30]}...")
    print(f"  RENEWED_AT:    {renewed_at}")

    print("Sincronizando con Railway...")
    try:
        subprocess.run(
            ["railway", "variables", "set",
             f"GMAIL_REFRESH_TOKEN={creds.refresh_token}",
             f"GMAIL_TOKEN_RENEWED_AT={renewed_at}",
             "--service", "web"],
            check=True, capture_output=True, text=True
        )
        print("  Railway actualizado.")
    except Exception as e:
        print(f"  Railway no actualizado (hacelo manual): {e}")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verificar que escribe el timestamp**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print(os.environ.get('GMAIL_TOKEN_RENEWED_AT','NO EXISTE'))"
```

Esperado: fecha ISO o `NO EXISTE` si aún no se corrió el script. Si no existe, correr `python setup_gmail.py` y verificar de nuevo.

- [ ] **Step 3: Commit**

```bash
git add setup_gmail.py
git commit -m "feat: save GMAIL_TOKEN_RENEWED_AT on setup"
```

---

### Task 2: Guardar timestamp en setup_calendar.py

**Files:**
- Modify: `setup_calendar.py`

- [ ] **Step 1: Agregar guardado de timestamp**

Reemplazar el contenido completo de `setup_calendar.py` con:

```python
"""
Run this script ONCE to obtain the Google Calendar OAuth2 refresh token.
It opens a browser window for authentication and saves credentials to .env.
"""
import json
import subprocess
from datetime import datetime, timezone
from google_auth_oauthlib.flow import InstalledAppFlow
from dotenv import load_dotenv, set_key

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CREDENTIALS_FILE = "credentials.json"

def main():
    load_dotenv()
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(CREDENTIALS_FILE) as f:
        client_config = json.load(f)["installed"]

    renewed_at = datetime.now(timezone.utc).isoformat()

    set_key(".env", "GCAL_CLIENT_ID", client_config["client_id"])
    set_key(".env", "GCAL_CLIENT_SECRET", client_config["client_secret"])
    set_key(".env", "GCAL_REFRESH_TOKEN", creds.refresh_token)
    set_key(".env", "GCAL_TOKEN_RENEWED_AT", renewed_at)

    print("Credenciales de Calendar guardadas en .env")
    print(f"  CLIENT_ID:     {client_config['client_id'][:30]}...")
    print(f"  REFRESH_TOKEN: {creds.refresh_token[:30]}...")
    print(f"  RENEWED_AT:    {renewed_at}")

    print("Sincronizando con Railway...")
    try:
        subprocess.run(
            ["railway", "variables", "set",
             f"GCAL_REFRESH_TOKEN={creds.refresh_token}",
             f"GCAL_TOKEN_RENEWED_AT={renewed_at}",
             "--service", "web"],
            check=True, capture_output=True, text=True
        )
        print("  Railway actualizado.")
    except Exception as e:
        print(f"  Railway no actualizado (hacelo manual): {e}")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add setup_calendar.py
git commit -m "feat: save GCAL_TOKEN_RENEWED_AT on setup"
```

---

### Task 3: Crear routes/tokens.py

**Files:**
- Create: `routes/tokens.py`

- [ ] **Step 1: Crear el archivo**

```python
"""Token health status endpoint."""

import base64
import json
import os
from datetime import datetime, timezone, timedelta

from flask import Blueprint, jsonify

tokens_bp = Blueprint("tokens", __name__)

GOOGLE_TTL_DAYS = 7  # testing mode expiry


def _days_left(renewed_at_iso: str) -> float | None:
    """Return days remaining from a renewal ISO timestamp, or None if missing."""
    if not renewed_at_iso:
        return None
    try:
        renewed = datetime.fromisoformat(renewed_at_iso)
        if renewed.tzinfo is None:
            renewed = renewed.replace(tzinfo=timezone.utc)
        expires = renewed + timedelta(days=GOOGLE_TTL_DAYS)
        delta = expires - datetime.now(timezone.utc)
        return delta.total_seconds() / 86400
    except Exception:
        return None


def _format_label(days: float | None) -> str:
    if days is None:
        return "Sin datos"
    if days <= 0:
        return "Vencido"
    if days < 1:
        hours = int(days * 24)
        return f"Vence en {hours}h"
    return f"Vence en {int(days)}d"


def _status_from_days(days: float | None) -> str:
    if days is None:
        return "unknown"
    if days <= 0:
        return "danger"
    if days <= 1:
        return "danger"
    if days <= 3:
        return "warning"
    return "ok"


def _decode_jwt_exp(token: str) -> float | None:
    """Try to decode exp field from a JWT. Returns days left or None."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1] + "=="  # pad
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        if not exp:
            return None
        expires = datetime.fromtimestamp(exp, tz=timezone.utc)
        delta = expires - datetime.now(timezone.utc)
        return delta.total_seconds() / 86400
    except Exception:
        return None


@tokens_bp.route("/api/tokens/status")
def api_tokens_status():
    results = []

    # Gmail
    gmail_days = _days_left(os.environ.get("GMAIL_TOKEN_RENEWED_AT", ""))
    results.append({
        "name": "Gmail",
        "key": "GMAIL_REFRESH_TOKEN",
        "status": _status_from_days(gmail_days),
        "label": _format_label(gmail_days),
    })

    # Google Calendar
    gcal_days = _days_left(os.environ.get("GCAL_TOKEN_RENEWED_AT", ""))
    results.append({
        "name": "Google Calendar",
        "key": "GCAL_REFRESH_TOKEN",
        "status": _status_from_days(gcal_days),
        "label": _format_label(gcal_days),
    })

    # WhatsApp (try JWT decode)
    wa_token = os.environ.get("WA_ACCESS_TOKEN", "")
    wa_days = _decode_jwt_exp(wa_token) if wa_token else None
    results.append({
        "name": "WhatsApp",
        "key": "WA_ACCESS_TOKEN",
        "status": _status_from_days(wa_days) if wa_days is not None else "unknown",
        "label": _format_label(wa_days),
    })

    # Vercel — no expiry
    results.append({
        "name": "Vercel",
        "key": "VERCEL_TOKEN",
        "status": "permanent",
        "label": "Permanente",
    })

    return jsonify(results)
```

- [ ] **Step 2: Verificar que el archivo existe y tiene sintaxis correcta**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "from routes.tokens import tokens_bp; print('OK')"
```

Esperado: `OK`

- [ ] **Step 3: Commit**

```bash
git add routes/tokens.py
git commit -m "feat: token health status endpoint"
```

---

### Task 4: Integrar en dashboard.py

**Files:**
- Modify: `dashboard.py` (4 puntos: import, register, HTML, CSS+JS)

- [ ] **Step 1: Agregar import del blueprint**

En `dashboard.py` línea ~14, después de `from routes.budgets import budgets_bp`, agregar:

```python
from routes.tokens import tokens_bp
```

- [ ] **Step 2: Registrar el blueprint**

En la línea que dice:
```python
for bp in (leads_bp, demos_bp, calendar_bp, wa_bp, pipeline_bp, tasks_bp, budgets_bp):
```

Cambiarla a:
```python
for bp in (leads_bp, demos_bp, calendar_bp, wa_bp, pipeline_bp, tasks_bp, budgets_bp, tokens_bp):
```

- [ ] **Step 3: Agregar CSS del panel de tokens**

Buscar en `dashboard.py` la línea:
```
/* ── Tasks panel ──────────────────────────────────────────────────────────── */
```

Justo ANTES de esa línea, insertar:

```css
/* ── Token health panel ───────────────────────────────────────────────────── */
.token-health{margin-bottom:20px}
.token-health-title{font-size:.7rem;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.7px;margin-bottom:10px}
.token-cards{display:flex;gap:10px;flex-wrap:wrap}
.token-card{background:#111827;border:1px solid #1e293b;border-radius:10px;padding:12px 16px;min-width:140px;flex:1}
.token-card-name{font-size:.75rem;font-weight:600;color:#94a3b8;margin-bottom:4px}
.token-card-label{font-size:.85rem;font-weight:700}
.token-card.ok .token-card-label{color:#4ade80}
.token-card.warning .token-card-label{color:#fbbf24}
.token-card.danger .token-card-label{color:#f87171}
.token-card.permanent .token-card-label{color:#60a5fa}
.token-card.unknown .token-card-label{color:#475569}
```

- [ ] **Step 4: Agregar el div del panel en el HTML de Tareas**

Buscar en `dashboard.py`:
```html
    <div id="tasks-list"></div>
  </div>
```

Reemplazar con:
```html
    <div id="token-health" class="token-health" style="display:none">
      <div class="token-health-title">Estado del sistema</div>
      <div class="token-cards" id="token-cards"></div>
    </div>
    <div id="tasks-list"></div>
  </div>
```

- [ ] **Step 5: Agregar función JS loadTokenHealth y llamarla desde loadTasks**

Buscar en `dashboard.py`:
```javascript
async function loadTasks() {
  try {
    const [tr, lr] = await Promise.all([
      fetch('/api/tasks').then(r => r.json()),
```

Justo ANTES de `async function loadTasks()`, insertar:

```javascript
async function loadTokenHealth() {
  try {
    const tokens = await fetch('/api/tokens/status').then(r => r.json());
    const container = document.getElementById('token-cards');
    const panel = document.getElementById('token-health');
    if (!container || !panel) return;
    container.innerHTML = tokens.map(t =>
      `<div class="token-card ${t.status}">
        <div class="token-card-name">${t.name}</div>
        <div class="token-card-label">${t.label}</div>
      </div>`
    ).join('');
    panel.style.display = 'block';
  } catch { /* silencioso */ }
}
```

Luego buscar el cuerpo de `loadTasks`:
```javascript
  try {
    const [tr, lr] = await Promise.all([
      fetch('/api/tasks').then(r => r.json()),
```

Y agregar `loadTokenHealth();` como primera línea dentro del `try`:
```javascript
  try {
    loadTokenHealth();
    const [tr, lr] = await Promise.all([
      fetch('/api/tasks').then(r => r.json()),
```

- [ ] **Step 6: Verificar que el servidor arranca sin errores**

```bash
cd C:\Users\juant\lead-gen-uy
python server.py
```

Esperado: el servidor arranca sin errores de importación. Visitar `http://localhost:5000/api/tokens/status` y ver un JSON con 4 tokens.

- [ ] **Step 7: Commit**

```bash
git add dashboard.py
git commit -m "feat: token health panel in tasks tab"
```

---

### Task 5: Sincronizar timestamps actuales con Railway

Los tokens ya fueron renovados hoy (2026-05-21) pero sin guardar el timestamp. Hay que setearlo manualmente esta vez.

- [ ] **Step 1: Guardar timestamps de hoy en .env**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "
from dotenv import set_key
ts = '2026-05-21T17:00:00+00:00'
set_key('.env', 'GMAIL_TOKEN_RENEWED_AT', ts)
set_key('.env', 'GCAL_TOKEN_RENEWED_AT', ts)
print('OK')
"
```

- [ ] **Step 2: Sincronizar con Railway**

```bash
cd C:\Users\juant\lead-gen-uy
railway variables set GMAIL_TOKEN_RENEWED_AT="2026-05-21T17:00:00+00:00" GCAL_TOKEN_RENEWED_AT="2026-05-21T17:00:00+00:00" --service web
```

- [ ] **Step 3: Deploy a Railway**

```bash
cd C:\Users\juant\lead-gen-uy
railway up --service web --detach
```

Esperado: deploy exitoso. Visitar el dashboard en Railway y abrir la pestaña Tareas — debe aparecer el panel con los 4 tokens.
