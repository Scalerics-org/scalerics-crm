# Auth multi-usuario con código de invitación

## Objetivo

Reemplazar el login de contraseña única por un sistema de usuarios individuales con registro controlado por código de invitación.

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

CREATE TABLE invite_codes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL,
    used_by     INTEGER REFERENCES users(id),
    used_at     TEXT
);
```

Unicidad de cuenta garantizada por `UNIQUE` en `email`.

---

## Flujo de autenticación

**Login** (`GET/POST /login`):
- Formulario: email + contraseña
- Verifica email existe → compara hash → crea sesión con `user_id`, `user_name`
- Error genérico si falla ("Email o contraseña incorrectos") — no revela si el email existe

**Registro** (`GET/POST /register`):
- Formulario: código de invitación + nombre + email + teléfono + contraseña
- Validaciones en orden:
  1. Código existe y no fue usado
  2. Email no registrado ya
  3. Contraseña mínimo 8 caracteres
- Si todo ok: crea user, marca código como `used_by` + `used_at`, inicia sesión
- Redirige al dashboard

**Logout** (`GET /logout`):
- Limpia sesión, redirige a login

---

## Panel de admin — gestión de invitaciones

Nueva sección en el dashboard (visible solo para el usuario admin):
- Botón "Generar código" → crea un `invite_codes` nuevo con `secrets.token_urlsafe(12)`, lo muestra en pantalla para copiar
- Lista de usuarios registrados: nombre, email, teléfono, fecha de registro
- Lista de códigos: código, estado (disponible / usado por quién)

El usuario admin es el primero registrado (id=1), o se puede definir por email en `.env` (`ADMIN_EMAIL`).

---

## Migración

Al arrancar la app por primera vez con el nuevo código:
- Si la tabla `users` no existe, se crea
- Si la tabla `invite_codes` no existe, se crea
- Se genera automáticamente un código de invitación inicial y se loguea en consola para que el admin pueda registrarse

La variable `DASHBOARD_PASSWORD` queda deprecada y se ignora.

---

## Seguridad

- Passwords hasheados con `werkzeug.security.generate_password_hash` (pbkdf2:sha256)
- Sin CSRF token — el nuevo flujo usa sesión Flask estándar sin ese mecanismo
- `SECRET_KEY` en `.env` sigue siendo necesaria para firmar las sesiones
- Rate limiting: no se implementa (fuera de scope, Railway ya tiene protección básica)

---

## Archivos afectados

- `database.py` — agregar funciones: `create_user`, `get_user_by_email`, `get_all_users`, `create_invite_code`, `get_invite_code`, `use_invite_code`, `get_all_invite_codes`
- `dashboard.py` — reemplazar login HTML + rutas `/login`, `/logout`, agregar `/register`, panel de admin de invitaciones
- `.env` — agregar `ADMIN_EMAIL` opcional

---

## Fuera de scope

- Recuperación de contraseña (olvidé mi contraseña)
- Roles/permisos distintos entre usuarios
- 2FA
