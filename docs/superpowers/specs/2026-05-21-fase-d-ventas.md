# Fase D: Features de Ventas

**Fecha:** 2026-05-21  
**Estado:** Aprobado

## Objetivo

Agregar 3 features que mejoran el flujo de ventas diario: plantillas de WhatsApp, registro de llamadas y trazabilidad de quién hizo qué acción.

---

## Feature 1 — WA Templates

### Qué hace

Permite guardar mensajes de WhatsApp predefinidos y usarlos desde el panel del lead o desde el panel de WhatsApp del sidebar.

### Tabla

```sql
CREATE TABLE IF NOT EXISTS wa_templates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

### API

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/wa/templates` | Listar todas las plantillas |
| POST | `/api/wa/templates` | Crear plantilla `{name, body}` |
| DELETE | `/api/wa/templates/<int:id>` | Eliminar plantilla |

### UI — Panel WA (sidebar)

Debajo del área de mensajes, un botón "📋 Plantillas" que despliega un panel inline con:
- Lista de plantillas existentes (nombre + botón Usar + botón Eliminar)
- Formulario para crear: input nombre + textarea cuerpo + botón Guardar

Al hacer clic en "Usar", el texto de la plantilla se copia al campo `#wa-input` del chat activo.

### UI — Panel de cliente

En la sección de contacto del panel de cliente (tab Info), si el lead tiene teléfono: un select "Usar plantilla →" que al elegir una opción abre `https://wa.me/<phone>?text=<body>` en nueva pestaña.

---

## Feature 2 — Call Logging

### Qué hace

Registra las llamadas realizadas a cada lead (resultado y notas). Visible en una nueva pestaña "Llamadas" en el panel de cliente.

### Tabla

```sql
CREATE TABLE IF NOT EXISTS call_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER NOT NULL,
    called_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    outcome     TEXT NOT NULL,
    notes       TEXT,
    created_by  TEXT DEFAULT 'sistema'
)
```

`outcome` válidos: `contestó`, `no_contestó`, `buzón`

### API

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/leads/<id>/calls` | Registrar llamada `{outcome, notes}` |
| GET | `/api/leads/<id>/calls` | Listar llamadas del lead |

### UI — Panel de cliente

Nueva pestaña "📞 Llamadas" (7ma pestaña, después de Tareas). Contiene:
- Formulario: select outcome + input notas + botón "Registrar"
- Historial: lista de llamadas ordenadas por fecha descendente, mostrando fecha, outcome con color (verde/rojo/gris), notas y "por <nombre>"

---

## Feature 3 — Activity Trail (quién hizo qué)

### Qué hace

Muestra quién realizó cada acción sobre un lead. Implementado como campo `created_by` en las tablas de eventos, poblado con el nombre del usuario que inició sesión.

### Login con nombre

El formulario de login agrega un campo `nombre` (texto libre, opcional). Al iniciar sesión: `session["user_name"] = nombre.strip() or "sistema"`. Sin cambios en la contraseña — sigue siendo una sola para todos.

### Migración

Agregar columna `created_by TEXT DEFAULT 'sistema'` a la tabla `lead_events` (ya existente).

### Propagación

- `add_lead_event()` en `database.py`: agrega parámetro `created_by: str = "sistema"`
- `api_crm_status()` y `api_batch_status()` en `routes/leads.py`: pasan `session.get("user_name", "sistema")`
- `call_logs.created_by`: se puebla desde `session.get("user_name", "sistema")` al registrar

### UI — Timeline en panel de cliente

En el tab Info del panel de cliente, la timeline de eventos ya existente muestra cada evento con el nombre del autor si está disponible: `"→ Contactado por Juan"`.

---

## Lo que NO incluye esta fase

- Roles ni permisos (todos tienen el mismo acceso)
- Múltiples contraseñas
- WA templates con variables dinámicas (ej. `{{nombre}}`)
- Estadísticas de llamadas

## Criterio de éxito

- Login pide nombre + contraseña
- Se puede crear, listar, usar y eliminar plantillas WA
- Se puede registrar una llamada con outcome + notas, y ver el historial
- El timeline del lead muestra "por <nombre>" en los eventos post-feature
- Deploy exitoso en Railway
