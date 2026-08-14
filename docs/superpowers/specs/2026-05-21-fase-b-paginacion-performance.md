# Fase B: Paginación y Performance

**Fecha:** 2026-05-21  
**Estado:** Aprobado

## Objetivo

Reducir la carga del panel principal (actualmente descarga todos los leads de golpe), añadir índices en columnas calientes, y proteger el login contra CSRF.

## Mejoras incluidas

### Mejora 1 — Índices de base de datos (database.py)

Agregar en `init_db()` tres índices nuevos en columnas de uso frecuente:
- `idx_businesses_crm_status` — filtro principal de la tabla de leads
- `idx_businesses_score` — columna de ordenamiento
- `idx_businesses_category` — filtro de rubro

### Mejora 2 — Filtrado de crm_status en SQL (database.py + routes/leads.py)

`get_all_businesses()` actualmente ignora el `crm_status` y devuelve todo. Se le agrega un parámetro opcional `crm_status: str | None = None`. Cuando es `"sin_contactar"`, la cláusula WHERE es `(crm_status IS NULL OR crm_status = 'sin_contactar')`. Para cualquier otro valor: `WHERE crm_status = ?`.

`api_leads()` en `routes/leads.py` pasa el `crm_status` al nuevo parámetro, eliminando el filtro en Python para ese campo.

### Mejora 3 — Paginación server-side de /api/leads (routes/leads.py)

Cuando el cliente envía `?page=N`, la respuesta cambia de array raw a:
```json
{"items": [...], "total": 320, "pages": 7, "page": 1}
```

Sin el param `page`, sigue devolviendo array raw para mantener compatibilidad con las llamadas de Kanban y Tareas que usan `/api/leads` sin paginación.

Tamaño de página fijo: 50 leads. La paginación ocurre en Python después de todos los filtros (crm_status en SQL, category y search en Python).

### Mejora 4 — CSRF en login form (dashboard.py)

En GET `/login`: `secrets.token_hex(32)` se genera, se guarda en `session["csrf_token"]` y se inyecta en LOGIN_HTML como campo oculto `<input type="hidden" name="csrf_token" value="{{ csrf_token }}">`.

En POST `/login`: se extrae el token del form y se usa `session.pop("csrf_token", "")` para compararlo con `secrets.compare_digest`. Si no coincide, se rechaza con mensaje "Token inválido. Recargá la página." y se genera un token nuevo para el próximo intento.

### Mejora 5 — UI de paginación en pestaña de leads (dashboard.py)

- `<div id="leads-pagination">` se agrega después del `<div id="table-body">` en el HTML
- Variables nuevas: `let currentPage = 1; let totalPages = 1;`
- `loadLeads()` envía `page=currentPage` y maneja respuesta `{items, total, pages}`
- `_updatePagination()` renderiza botones Anterior/Siguiente + contador "Página N de M"
- `gotoPage(p)` actualiza `currentPage` y llama `loadLeads()`
- Cuando cambia cualquier filtro (crm, categoría, búsqueda), `currentPage` se resetea a 1

## Lo que NO incluye esta fase

- Paginación en Kanban (las cards no escalan igual)
- Full-text search en SQL (requiere FTS5, overkill para este volumen)
- Rate limiting (Fase C)
- Multi-usuario (Fase C)

## Criterio de éxito

- El panel de leads carga la primera página sin descargar todos los registros
- Navegar entre páginas funciona
- El login rechaza POSTs sin token CSRF válido
- `python server.py` arranca sin errores
- Deploy exitoso en Railway
