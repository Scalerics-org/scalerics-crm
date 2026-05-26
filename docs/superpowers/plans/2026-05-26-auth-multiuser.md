# Auth Multi-usuario Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar el login de contraseña única por usuarios individuales con email + password, registro abierto con unicidad de email, y recuperación de contraseña por email (Resend, stub por ahora).

**Architecture:** Dos tablas nuevas en SQLite (`users`, `password_reset_tokens`), funciones en `database.py` siguiendo el patrón existente, rutas de auth en `dashboard.py`, servicio de email stub en `services/email_service.py`. El panel de admin dentro del dashboard existente gestiona usuarios.

**Tech Stack:** Flask, SQLite, werkzeug.security (ya incluida con Flask), secrets (stdlib), pytest

---

## File Map

| Archivo | Acción | Responsabilidad |
|---------|--------|-----------------|
| `database.py` | Modify | Agregar tablas users/reset_tokens en `init_db()` + funciones CRUD |
| `services/email_service.py` | Create | Stub `send_reset_email()` — loguea a consola, ready para Resend |
| `dashboard.py` | Modify | Reemplazar LOGIN_HTML, agregar HTML de register/forgot/reset, nuevas rutas, panel admin |
| `tests/test_auth_db.py` | Create | Tests de las funciones de DB de usuarios y tokens |
| `.env` | Modify | Agregar `ADMIN_EMAIL` |

---

## Task 1: Funciones de base de datos — usuarios y tokens de reset

**Files:**
- Modify: `database.py`
- Create: `tests/test_auth_db.py`

- [ ] **Step 1: Escribir tests que fallan**

Crear `tests/test_auth_db.py`:

```python
import pytest
from database import (
    init_db,
    create_user,
    get_user_by_email,
    get_user_by_id,
    get_all_users,
    delete_user,
    create_reset_token,
    get_reset_token,
    use_reset_token,
)


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    return path


def test_tables_created(db):
    import sqlite3
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "users" in tables
    assert "password_reset_tokens" in tables
    conn.close()


def test_create_and_get_user(db):
    uid = create_user(db, name="Juan", email="juan@test.com", phone="099123456", password_hash="hash123")
    assert isinstance(uid, int)
    user = get_user_by_email(db, "juan@test.com")
    assert user is not None
    assert user["name"] == "Juan"
    assert user["phone"] == "099123456"


def test_email_uniqueness(db):
    create_user(db, name="Juan", email="juan@test.com", phone="099111111", password_hash="hash1")
    result = create_user(db, name="Otro", email="juan@test.com", phone="099222222", password_hash="hash2")
    assert result is None


def test_get_user_by_id(db):
    uid = create_user(db, name="Ana", email="ana@test.com", phone="099333333", password_hash="hash")
    user = get_user_by_id(db, uid)
    assert user["email"] == "ana@test.com"


def test_get_user_by_email_not_found(db):
    assert get_user_by_email(db, "noexiste@test.com") is None


def test_get_all_users(db):
    create_user(db, name="A", email="a@test.com", phone="1", password_hash="h")
    create_user(db, name="B", email="b@test.com", phone="2", password_hash="h")
    users = get_all_users(db)
    assert len(users) == 2


def test_delete_user(db):
    uid = create_user(db, name="Del", email="del@test.com", phone="9", password_hash="h")
    delete_user(db, uid)
    assert get_user_by_id(db, uid) is None


def test_create_and_get_reset_token(db):
    uid = create_user(db, name="X", email="x@test.com", phone="0", password_hash="h")
    token_str = "abc123token"
    create_reset_token(db, user_id=uid, token=token_str)
    record = get_reset_token(db, token_str)
    assert record is not None
    assert record["user_id"] == uid
    assert record["used_at"] is None


def test_use_reset_token(db):
    uid = create_user(db, name="Y", email="y@test.com", phone="0", password_hash="h")
    create_reset_token(db, user_id=uid, token="tok456")
    use_reset_token(db, "tok456")
    record = get_reset_token(db, "tok456")
    assert record["used_at"] is not None


def test_get_nonexistent_token(db):
    assert get_reset_token(db, "noexiste") is None
```

- [ ] **Step 2: Correr tests para verificar que fallan**

```
cd lead-gen-uy && python -m pytest tests/test_auth_db.py -v
```

Esperado: FAIL — `ImportError` en `create_user` etc.

- [ ] **Step 3: Agregar tablas y funciones a `database.py`**

En `init_db()`, después del bloque de `wa_templates`/`call_logs`, agregar antes del `conn.commit()` final:

```python
        # ── users ─────────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                email       TEXT NOT NULL UNIQUE,
                phone       TEXT NOT NULL,
                password    TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
        """)

        # ── password_reset_tokens ─────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token       TEXT NOT NULL UNIQUE,
                created_at  TEXT NOT NULL,
                used_at     TEXT
            )
        """)
```

Al final del archivo `database.py`, agregar las funciones:

```python
# ─── Users ────────────────────────────────────────────────────────────────────

def create_user(db_path: str, name: str, email: str, phone: str, password_hash: str) -> Optional[int]:
    from datetime import datetime, timezone
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO users (name, email, phone, password, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, email, phone, password_hash, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cursor.lastrowid if cursor.rowcount > 0 else None
    finally:
        conn.close()


def get_user_by_email(db_path: str, email: str) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(db_path: str, user_id: int) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_users(db_path: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        cursor = conn.execute("SELECT id, name, email, phone, created_at FROM users ORDER BY created_at ASC")
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def delete_user(db_path: str, user_id: int) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def update_user_password(db_path: str, user_id: int, password_hash: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("UPDATE users SET password = ? WHERE id = ?", (password_hash, user_id))
        conn.commit()
    finally:
        conn.close()


# ─── Password reset tokens ────────────────────────────────────────────────────

def create_reset_token(db_path: str, user_id: int, token: str) -> None:
    from datetime import datetime, timezone
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO password_reset_tokens (user_id, token, created_at) VALUES (?, ?, ?)",
            (user_id, token, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_reset_token(db_path: str, token: str) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM password_reset_tokens WHERE token = ?", (token,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def use_reset_token(db_path: str, token: str) -> None:
    from datetime import datetime, timezone
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE password_reset_tokens SET used_at = ? WHERE token = ?",
            (datetime.now(timezone.utc).isoformat(), token),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Correr tests para verificar que pasan**

```
python -m pytest tests/test_auth_db.py -v
```

Esperado: todos PASS.

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_auth_db.py
git commit -m "feat: add users and password_reset_tokens tables with CRUD functions"
```

---

## Task 2: Email service stub

**Files:**
- Create: `services/email_service.py`

- [ ] **Step 1: Crear `services/email_service.py`**

```python
import logging
import os

logger = logging.getLogger(__name__)


def send_reset_email(to_email: str, reset_url: str) -> bool:
    """Send password reset email. Currently stubs to console log.
    Replace body with Resend API call when RESEND_API_KEY is configured.
    """
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        logger.info(f"[EMAIL STUB] Reset link para {to_email}: {reset_url}")
        return True

    try:
        import requests
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": os.environ.get("FACTORY_EMAIL", "noreply@scalerics.com"),
                "to": [to_email],
                "subject": "Resetear contraseña — Scalerics CRM",
                "html": f"""
                <p>Hola,</p>
                <p>Hacé clic en el link para resetear tu contraseña. Expira en 1 hora.</p>
                <p><a href="{reset_url}">{reset_url}</a></p>
                <p>Si no lo pediste vos, ignorá este mail.</p>
                """,
            },
            timeout=10,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send reset email to {to_email}: {e}")
        return False
```

- [ ] **Step 2: Commit**

```bash
git add services/email_service.py
git commit -m "feat: email service stub for password reset (Resend-ready)"
```

---

## Task 3: Reemplazar rutas de auth en dashboard.py

**Files:**
- Modify: `dashboard.py`

Las siguientes sub-tareas modifican `dashboard.py`. Cada una es un cambio específico y acotado.

### 3a — Reemplazar LOGIN_HTML

- [ ] **Step 1: Reemplazar el string `LOGIN_HTML` completo**

Encontrar la línea `LOGIN_HTML = """<!DOCTYPE html>` (línea ~30) y reemplazar todo el bloque hasta el `"""` de cierre con:

```python
LOGIN_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Scalerics — Acceso</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:#0a0f1a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#111827;border:1px solid #1e293b;border-radius:16px;padding:40px 36px;width:360px;max-width:92vw}
.logo-wrap{margin-bottom:28px;display:flex;flex-direction:column;align-items:flex-start;gap:10px}
.logo-wrap img{height:44px;object-fit:contain;filter:drop-shadow(0 0 8px rgba(0,136,204,.25))}
.logo-sub{font-size:.75rem;color:#475569}
label{font-size:.72rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.8px;display:block;margin-bottom:7px}
input{width:100%;background:#0a0f1a;border:1px solid #1e293b;border-radius:8px;padding:11px 14px;font-size:.92rem;color:#e2e8f0;font-family:'Inter',sans-serif;outline:none;margin-bottom:16px}
input:focus{border-color:#0088cc}
button{width:100%;background:linear-gradient(135deg,#0088cc,#3db648);color:#fff;font-size:.88rem;font-weight:700;padding:12px;border-radius:8px;border:none;cursor:pointer;font-family:'Inter',sans-serif}
button:hover{opacity:.9}
.error{background:#2a1515;border:1px solid #7f1d1d;border-radius:8px;padding:10px 14px;font-size:.8rem;color:#f87171;margin-bottom:16px}
.links{margin-top:18px;display:flex;flex-direction:column;gap:8px;align-items:center}
.links a{font-size:.78rem;color:#64748b;text-decoration:none}
.links a:hover{color:#0088cc}
</style>
</head>
<body>
<div class="card">
  <div class="logo-wrap">
    <img src="https://raw.githubusercontent.com/juantomasetti1/scalerics-assets/main/logo_full.png" alt="Scalerics">
    <span class="logo-sub">CRM interno</span>
  </div>
  {% if error %}<div class="error">{{ error }}</div>{% endif %}
  <form method="POST">
    <label>Email</label>
    <input type="email" name="email" autocomplete="email" required autofocus>
    <label>Contraseña</label>
    <input type="password" name="password" autocomplete="current-password" required>
    <button type="submit">Ingresar</button>
  </form>
  <div class="links">
    <a href="/forgot-password">Olvidé mi contraseña</a>
    <a href="/register">Crear cuenta</a>
  </div>
</div>
</body>
</html>"""
```

### 3b — Agregar REGISTER_HTML, FORGOT_HTML, RESET_HTML

- [ ] **Step 2: Agregar los 3 nuevos strings HTML justo después del cierre de LOGIN_HTML**

```python
REGISTER_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Scalerics — Crear cuenta</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:#0a0f1a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#111827;border:1px solid #1e293b;border-radius:16px;padding:40px 36px;width:380px;max-width:92vw}
.logo-wrap{margin-bottom:24px;display:flex;flex-direction:column;align-items:flex-start;gap:8px}
.logo-wrap img{height:38px;object-fit:contain}
.logo-sub{font-size:.75rem;color:#475569}
label{font-size:.72rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.8px;display:block;margin-bottom:7px}
input{width:100%;background:#0a0f1a;border:1px solid #1e293b;border-radius:8px;padding:11px 14px;font-size:.92rem;color:#e2e8f0;font-family:'Inter',sans-serif;outline:none;margin-bottom:16px}
input:focus{border-color:#0088cc}
button{width:100%;background:linear-gradient(135deg,#0088cc,#3db648);color:#fff;font-size:.88rem;font-weight:700;padding:12px;border-radius:8px;border:none;cursor:pointer;font-family:'Inter',sans-serif}
button:hover{opacity:.9}
.error{background:#2a1515;border:1px solid #7f1d1d;border-radius:8px;padding:10px 14px;font-size:.8rem;color:#f87171;margin-bottom:16px}
.back{margin-top:16px;text-align:center}<br>.back a{font-size:.78rem;color:#64748b;text-decoration:none}
.back a:hover{color:#0088cc}
</style>
</head>
<body>
<div class="card">
  <div class="logo-wrap">
    <img src="https://raw.githubusercontent.com/juantomasetti1/scalerics-assets/main/logo_full.png" alt="Scalerics">
    <span class="logo-sub">Crear cuenta</span>
  </div>
  {% if error %}<div class="error">{{ error }}</div>{% endif %}
  <form method="POST">
    <label>Nombre</label>
    <input type="text" name="name" required autofocus>
    <label>Email</label>
    <input type="email" name="email" autocomplete="email" required>
    <label>Teléfono</label>
    <input type="tel" name="phone" required>
    <label>Contraseña</label>
    <input type="password" name="password" autocomplete="new-password" required>
    <button type="submit">Crear cuenta</button>
  </form>
  <div class="back"><a href="/login">← Volver al login</a></div>
</div>
</body>
</html>"""

FORGOT_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Scalerics — Recuperar contraseña</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:#0a0f1a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#111827;border:1px solid #1e293b;border-radius:16px;padding:40px 36px;width:360px;max-width:92vw}
.logo-wrap{margin-bottom:24px}
.logo-wrap img{height:38px;object-fit:contain}
label{font-size:.72rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.8px;display:block;margin-bottom:7px}
input{width:100%;background:#0a0f1a;border:1px solid #1e293b;border-radius:8px;padding:11px 14px;font-size:.92rem;color:#e2e8f0;font-family:'Inter',sans-serif;outline:none;margin-bottom:16px}
input:focus{border-color:#0088cc}
button{width:100%;background:linear-gradient(135deg,#0088cc,#3db648);color:#fff;font-size:.88rem;font-weight:700;padding:12px;border-radius:8px;border:none;cursor:pointer;font-family:'Inter',sans-serif}
button:hover{opacity:.9}
.msg{background:#0f2a1a;border:1px solid #166534;border-radius:8px;padding:10px 14px;font-size:.82rem;color:#4ade80;margin-bottom:16px}
.back{margin-top:16px;text-align:center}
.back a{font-size:.78rem;color:#64748b;text-decoration:none}
.back a:hover{color:#0088cc}
</style>
</head>
<body>
<div class="card">
  <div class="logo-wrap">
    <img src="https://raw.githubusercontent.com/juantomasetti1/scalerics-assets/main/logo_full.png" alt="Scalerics">
  </div>
  {% if message %}<div class="msg">{{ message }}</div>{% endif %}
  {% if not message %}
  <form method="POST">
    <label>Email de tu cuenta</label>
    <input type="email" name="email" required autofocus>
    <button type="submit">Enviar link de recuperación</button>
  </form>
  {% endif %}
  <div class="back"><a href="/login">← Volver al login</a></div>
</div>
</body>
</html>"""

RESET_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Scalerics — Nueva contraseña</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:#0a0f1a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#111827;border:1px solid #1e293b;border-radius:16px;padding:40px 36px;width:360px;max-width:92vw}
.logo-wrap{margin-bottom:24px}
.logo-wrap img{height:38px;object-fit:contain}
label{font-size:.72rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.8px;display:block;margin-bottom:7px}
input{width:100%;background:#0a0f1a;border:1px solid #1e293b;border-radius:8px;padding:11px 14px;font-size:.92rem;color:#e2e8f0;font-family:'Inter',sans-serif;outline:none;margin-bottom:16px}
input:focus{border-color:#0088cc}
button{width:100%;background:linear-gradient(135deg,#0088cc,#3db648);color:#fff;font-size:.88rem;font-weight:700;padding:12px;border-radius:8px;border:none;cursor:pointer;font-family:'Inter',sans-serif}
button:hover{opacity:.9}
.error{background:#2a1515;border:1px solid #7f1d1d;border-radius:8px;padding:10px 14px;font-size:.8rem;color:#f87171;margin-bottom:16px}
.back{margin-top:16px;text-align:center}
.back a{font-size:.78rem;color:#64748b;text-decoration:none}
</style>
</head>
<body>
<div class="card">
  <div class="logo-wrap">
    <img src="https://raw.githubusercontent.com/juantomasetti1/scalerics-assets/main/logo_full.png" alt="Scalerics">
  </div>
  {% if error %}<div class="error">{{ error }}</div>{% endif %}
  {% if valid %}
  <form method="POST">
    <label>Nueva contraseña</label>
    <input type="password" name="password" autocomplete="new-password" required autofocus>
    <label>Repetir contraseña</label>
    <input type="password" name="password2" autocomplete="new-password" required>
    <button type="submit">Guardar contraseña</button>
  </form>
  {% else %}
  <p style="font-size:.85rem;color:#94a3b8;margin-bottom:16px">{{ error }}</p>
  {% endif %}
  <div class="back"><a href="/forgot-password">Pedir nuevo link</a></div>
</div>
</body>
</html>"""
```

### 3c — Reemplazar rutas de auth en create_app()

- [ ] **Step 3: Actualizar `require_login` para incluir los nuevos endpoints**

Encontrar en `create_app()`:
```python
        if request.endpoint in ("login", "logout", "static"):
```

Reemplazar con:
```python
        if request.endpoint in ("login", "logout", "register", "forgot_password", "reset_password", "static"):
```

- [ ] **Step 4: Reemplazar la ruta `/login` completa**

Encontrar y reemplazar el bloque de la ruta `/login` (desde `@app.route("/login"` hasta el final de la función `login()`):

```python
    @app.route("/login", methods=["GET", "POST"])
    def login():
        from werkzeug.security import check_password_hash
        from database import get_user_by_email
        error = None
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = get_user_by_email(db_path, email)
            if user and check_password_hash(user["password"], password):
                session["logged_in"] = True
                session["user_id"] = user["id"]
                session["user_name"] = user["name"]
                return redirect(url_for("index"))
            error = "Email o contraseña incorrectos"
        return render_template_string(LOGIN_HTML, error=error)
```

- [ ] **Step 5: Agregar ruta `/register`**

Agregar después de la ruta `/login`:

```python
    @app.route("/register", methods=["GET", "POST"])
    def register():
        from werkzeug.security import generate_password_hash
        from database import create_user, get_user_by_email
        error = None
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            phone = request.form.get("phone", "").strip()
            password = request.form.get("password", "")
            if not all([name, email, phone, password]):
                error = "Todos los campos son requeridos"
            elif len(password) < 8:
                error = "La contraseña debe tener al menos 8 caracteres"
            elif get_user_by_email(db_path, email):
                error = "Ya existe una cuenta con ese email"
            else:
                uid = create_user(db_path, name=name, email=email, phone=phone,
                                  password_hash=generate_password_hash(password))
                if uid:
                    session["logged_in"] = True
                    session["user_id"] = uid
                    session["user_name"] = name
                    return redirect(url_for("index"))
                error = "Error al crear la cuenta"
        return render_template_string(REGISTER_HTML, error=error)
```

- [ ] **Step 6: Agregar ruta `/forgot-password`**

Agregar después de `/register`:

```python
    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        import secrets as _secrets
        from database import get_user_by_email, create_reset_token
        from services.email_service import send_reset_email
        message = None
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            user = get_user_by_email(db_path, email)
            if user:
                token = _secrets.token_urlsafe(32)
                create_reset_token(db_path, user_id=user["id"], token=token)
                base_url = request.host_url.rstrip("/")
                reset_url = f"{base_url}/reset-password/{token}"
                send_reset_email(email, reset_url)
            message = "Si el email está registrado, recibirás un link en los próximos minutos."
        return render_template_string(FORGOT_HTML, message=message)
```

- [ ] **Step 7: Agregar ruta `/reset-password/<token>`**

Agregar después de `/forgot-password`:

```python
    @app.route("/reset-password/<token>", methods=["GET", "POST"])
    def reset_password(token):
        from datetime import datetime, timezone
        from werkzeug.security import generate_password_hash
        from database import get_reset_token, use_reset_token, update_user_password

        record = get_reset_token(db_path, token)
        error = None
        valid = False

        if not record:
            error = "Link inválido o ya utilizado."
        elif record["used_at"] is not None:
            error = "Este link ya fue utilizado."
        else:
            created = datetime.fromisoformat(record["created_at"])
            age_hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600
            if age_hours > 1:
                error = "Este link expiró. Pedí uno nuevo."
            else:
                valid = True

        if valid and request.method == "POST":
            password = request.form.get("password", "")
            password2 = request.form.get("password2", "")
            if len(password) < 8:
                error = "La contraseña debe tener al menos 8 caracteres"
                valid = True
            elif password != password2:
                error = "Las contraseñas no coinciden"
                valid = True
            else:
                use_reset_token(db_path, token)
                update_user_password(db_path, record["user_id"],
                                     generate_password_hash(password))
                return redirect(url_for("login"))

        return render_template_string(RESET_HTML, error=error, valid=valid)
```

- [ ] **Step 8: Commit**

```bash
git add dashboard.py
git commit -m "feat: multi-user auth — login, register, forgot/reset password routes"
```

---

## Task 4: Panel de admin — gestión de usuarios

**Files:**
- Modify: `dashboard.py`

- [ ] **Step 1: Agregar rutas de admin para usuarios**

Agregar en `create_app()` después de la ruta `/reset-password`:

```python
    @app.route("/api/admin/users", methods=["GET"])
    def admin_list_users():
        admin_email = os.environ.get("ADMIN_EMAIL", "")
        current_user_id = session.get("user_id")
        from database import get_user_by_id, get_all_users
        current = get_user_by_id(db_path, current_user_id) if current_user_id else None
        if not current:
            return jsonify({"error": "No autorizado"}), 403
        if admin_email and current["email"].lower() != admin_email.lower():
            return jsonify({"error": "No autorizado"}), 403
        if not admin_email and current["id"] != 1:
            return jsonify({"error": "No autorizado"}), 403
        return jsonify(get_all_users(db_path))

    @app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
    def admin_delete_user(user_id):
        admin_email = os.environ.get("ADMIN_EMAIL", "")
        current_user_id = session.get("user_id")
        from database import get_user_by_id, delete_user
        current = get_user_by_id(db_path, current_user_id) if current_user_id else None
        if not current:
            return jsonify({"error": "No autorizado"}), 403
        if admin_email and current["email"].lower() != admin_email.lower():
            return jsonify({"error": "No autorizado"}), 403
        if not admin_email and current["id"] != 1:
            return jsonify({"error": "No autorizado"}), 403
        if user_id == current_user_id:
            return jsonify({"error": "No podés eliminar tu propia cuenta"}), 400
        delete_user(db_path, user_id)
        return jsonify({"ok": True})

    @app.route("/api/admin/users/<int:user_id>/reset-password", methods=["POST"])
    def admin_reset_user_password(user_id):
        import secrets as _secrets
        admin_email = os.environ.get("ADMIN_EMAIL", "")
        current_user_id = session.get("user_id")
        from database import get_user_by_id, create_reset_token
        from services.email_service import send_reset_email
        current = get_user_by_id(db_path, current_user_id) if current_user_id else None
        if not current:
            return jsonify({"error": "No autorizado"}), 403
        if admin_email and current["email"].lower() != admin_email.lower():
            return jsonify({"error": "No autorizado"}), 403
        if not admin_email and current["id"] != 1:
            return jsonify({"error": "No autorizado"}), 403
        target = get_user_by_id(db_path, user_id)
        if not target:
            return jsonify({"error": "Usuario no encontrado"}), 404
        token = _secrets.token_urlsafe(32)
        create_reset_token(db_path, user_id=user_id, token=token)
        base_url = request.host_url.rstrip("/")
        reset_url = f"{base_url}/reset-password/{token}"
        send_reset_email(target["email"], reset_url)
        return jsonify({"ok": True, "reset_url": reset_url})
```

- [ ] **Step 2: Agregar sección de admin en el dashboard HTML**

En `DASHBOARD_HTML`, localizar la sección del nav o del sidebar donde hay otros botones de configuración. Agregar un botón "Usuarios" que solo aparezca para el admin, y un modal de usuarios. El botón y modal van pegados al final del body, antes del cierre `</body>`:

Buscar la línea que contiene `</body>\n</html>"""` al final de `DASHBOARD_HTML` y agregar antes de ella:

```html
<!-- Admin users panel -->
<div id="admin-panel" style="display:none;position:fixed;top:0;right:0;bottom:0;width:380px;background:#111827;border-left:1px solid #1e293b;z-index:200;flex-direction:column;overflow:hidden">
  <div style="padding:20px 20px 0;display:flex;align-items:center;justify-content:space-between">
    <span style="font-size:.85rem;font-weight:700;color:#e2e8f0">Usuarios</span>
    <button onclick="closeAdminPanel()" style="background:none;border:none;color:#64748b;cursor:pointer;font-size:1.2rem">&times;</button>
  </div>
  <div id="admin-users-list" style="padding:16px;overflow-y:auto;flex:1;display:flex;flex-direction:column;gap:10px"></div>
</div>
<script>
async function openAdminPanel(){
  document.getElementById('admin-panel').style.display='flex';
  const res=await fetch('/api/admin/users');
  if(!res.ok){alert('No autorizado');closeAdminPanel();return;}
  const users=await res.json();
  document.getElementById('admin-users-list').innerHTML=users.map(u=>`
    <div style="background:#0a0f1a;border:1px solid #1e293b;border-radius:10px;padding:14px 16px">
      <div style="font-size:.88rem;font-weight:600;color:#e2e8f0">${u.name}</div>
      <div style="font-size:.75rem;color:#64748b;margin:2px 0">${u.email} · ${u.phone}</div>
      <div style="font-size:.7rem;color:#475569;margin-bottom:10px">Desde ${u.created_at.slice(0,10)}</div>
      <div style="display:flex;gap:8px">
        <button onclick="adminResetPwd(${u.id})" style="flex:1;background:#1e293b;border:none;border-radius:6px;padding:7px;font-size:.72rem;color:#94a3b8;cursor:pointer">Resetear contraseña</button>
        <button onclick="adminDeleteUser(${u.id})" style="background:#2a1515;border:1px solid #7f1d1d;border-radius:6px;padding:7px 10px;font-size:.72rem;color:#f87171;cursor:pointer">Eliminar</button>
      </div>
    </div>
  `).join('');
}
function closeAdminPanel(){document.getElementById('admin-panel').style.display='none';}
async function adminDeleteUser(id){
  if(!confirm('¿Eliminar este usuario?'))return;
  const r=await fetch('/api/admin/users/'+id,{method:'DELETE'});
  if((await r.json()).ok)openAdminPanel();
}
async function adminResetPwd(id){
  const r=await fetch('/api/admin/users/'+id+'/reset-password',{method:'POST'});
  const d=await r.json();
  if(d.ok)alert('Link de reset (ver consola si no hay email configurado):\n'+d.reset_url);
}
</script>
```

- [ ] **Step 3: Agregar botón "Usuarios" visible en el header del dashboard**

En `DASHBOARD_HTML`, buscar el área del header/nav donde están los botones de acción (buscar texto como `id="header"` o el área del top bar). Agregar un botón que llame a `openAdminPanel()`:

```html
<button onclick="openAdminPanel()" style="background:none;border:1px solid #1e293b;border-radius:8px;padding:6px 12px;font-size:.75rem;color:#64748b;cursor:pointer">⚙ Usuarios</button>
```

Nota: el lugar exacto depende del HTML del dashboard. Buscá el bloque del header y agregalo junto a otros botones de configuración.

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "feat: admin users panel in dashboard"
```

---

## Task 5: Primer arranque y limpieza de DASHBOARD_PASSWORD

**Files:**
- Modify: `dashboard.py`
- Modify: `.env`

- [ ] **Step 1: Agregar log de primer arranque en `create_app()`**

Al final de `create_app()`, antes del `return app`, agregar:

```python
    # Log a reminder if no users exist yet
    import logging as _logging
    _log = _logging.getLogger(__name__)
    try:
        from database import get_all_users
        if not get_all_users(db_path):
            _log.warning("No hay usuarios registrados. Entrá a /register para crear el primer usuario.")
    except Exception:
        pass
```

- [ ] **Step 2: Agregar `ADMIN_EMAIL` al `.env`**

En `.env`, agregar después de `FACTORY_EMAIL`:

```
ADMIN_EMAIL=scalerics@gmail.com
```

- [ ] **Step 3: Commit**

```bash
git add dashboard.py .env
git commit -m "feat: first-boot reminder, add ADMIN_EMAIL to .env"
```

---

## Task 6: Verificación final

- [ ] **Step 1: Correr todos los tests**

```
python -m pytest tests/ -v
```

Esperado: todos PASS.

- [ ] **Step 2: Correr la app localmente y verificar flujo completo**

```
python -m flask --app dashboard run --debug
```

Verificar:
1. `/login` muestra email + contraseña + links a register y forgot-password
2. `/register` crea cuenta y redirige al dashboard
3. Con el mismo email, `/register` da error "Ya existe una cuenta"
4. `/forgot-password` muestra mensaje genérico y loguea el reset URL en consola
5. El link de reset lleva a `/reset-password/<token>` con formulario de nueva contraseña
6. Token expirado o ya usado muestra error claro
7. Panel de usuarios (botón ⚙) lista, elimina y resetea contraseñas

- [ ] **Step 3: Commit final si todo OK**

```bash
git add -A
git commit -m "chore: verify multi-user auth implementation complete"
```
