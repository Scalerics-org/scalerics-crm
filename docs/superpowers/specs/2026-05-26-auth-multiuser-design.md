# Auth multi-usuario con recuperación de contraseña

## Objetivo

Reemplazar el login de contraseña única por un sistema de usuarios individuales con registro abierto (controlado por unicidad de email) y recuperación de contraseña por email.

---

## Base de datos

Dos tablas nuevas en la SQLite existente (`leads.db`):

```sql
CREATE TABLE users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    phone       TEXT NOT NULL,
    password    TEXT NOT NULL,  -- werkzeug hash
    created_at  TEXT NOT NULL
);

CREATE TABLE password_reset_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    token       TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL,
    used_at     TEXT
);
```

Unicidad de cuenta garantizada por `UNIQUE` en `email`. Un email = una cuenta, sin excepción.

---

## Flujo de autenticación

**Login** (`GET/POST /login`):
- Formulario: email + contraseña
- Error genérico si falla ("Email o contraseña incorrectos") — no revela si el email existe
- Link "Olvidé mi contraseña" debajo del botón

**Registro** (`GET/POST /register`):
- Formulario: nombre + email + teléfono + contraseña
- Validaciones:
  1. Email no registrado ya (error: "Ya existe una cuenta con ese email")
  2. Contraseña mínimo 8 caracteres
- Si ok: crea usuario, inicia sesión, redirige al dashboard

**Logout** (`GET /logout`):
- Limpia sesión, redirige a login

---

## Flujo de recuperación de contraseña

**Paso 1 — Solicitud** (`GET/POST /forgot-password`):
- Formulario: solo email
- Si el email existe: genera token con `secrets.token_urlsafe(32)`, lo guarda en `password_reset_tokens` con TTL de 1 hora
- Envía email con link `https://<dominio>/reset-password/<token>` usando **Resend** (a configurar)
- Siempre muestra el mismo mensaje ("Si el email está registrado, recibirás un link") — no revela si existe

**Paso 2 — Reset** (`GET/POST /reset-password/<token>`):
- GET: valida token (existe, no usado, no expirado) → muestra formulario de nueva contraseña
- POST: valida token + nueva contraseña (mín. 8 chars) → actualiza hash → marca token como usado → redirige a login
- Token expirado o inválido: muestra error con link para solicitar uno nuevo

**Integración Resend** (stub por ahora):
- Función `send_reset_email(to_email, reset_url)` en `services/email_service.py`
- Por ahora loguea el link en consola en vez de enviarlo
- Cuando se configure Resend, solo se edita esa función

---

## Panel de admin — gestión de usuarios

Nueva sección en el dashboard visible para el usuario admin (id=1 o email en `ADMIN_EMAIL`):
- Lista de usuarios: nombre, email, teléfono, fecha de registro
- Botón "Resetear contraseña" por usuario → genera token y loguea el link (mismo mecanismo que forgot-password)
- Botón "Eliminar usuario"

---

## Migración

Al arrancar la app con el nuevo código:
- Si `users` no existe, se crea automáticamente
- Si `password_reset_tokens` no existe, se crea automáticamente
- Se loguea en consola un aviso para que el admin se registre primero

`DASHBOARD_PASSWORD` queda deprecada e ignorada.

---

## Archivos afectados

- `database.py` — agregar: `create_user`, `get_user_by_email`, `get_user_by_id`, `get_all_users`, `delete_user`, `create_reset_token`, `get_reset_token`, `use_reset_token`
- `services/email_service.py` — nuevo archivo, función `send_reset_email` (stub con log)
- `dashboard.py` — reemplazar login HTML + rutas `/login`, `/logout`, agregar `/register`, `/forgot-password`, `/reset-password/<token>`, panel admin de usuarios
- `.env` — agregar `ADMIN_EMAIL` opcional, `RESEND_API_KEY` (cuando se configure)

---

## Fuera de scope

- Roles/permisos distintos entre usuarios
- 2FA
- Bloqueo por intentos fallidos
