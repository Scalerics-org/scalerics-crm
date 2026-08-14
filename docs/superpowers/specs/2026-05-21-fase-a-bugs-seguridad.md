# Fase A: Bugs Críticos y Seguridad

**Fecha:** 2026-05-21  
**Estado:** Aprobado

## Objetivo

Corregir los bugs críticos y vulnerabilidades de seguridad que representan riesgo real de pérdida de datos, acceso no autorizado o comportamiento incorrecto en producción.

## Fixes incluidos

### Fix 1 — SQL NULLS LAST (database.py)

SQLite no soporta `NULLS LAST`. Reemplazar con `CASE WHEN score IS NULL THEN 1 ELSE 0 END, score DESC`.

**Archivo:** `database.py` — función `get_all_businesses` o equivalente donde aparece `NULLS LAST`.

---

### Fix 2 — Path traversal en file upload (routes/leads.py)

El filename del archivo subido se almacena sin sanitizar. Usar `werkzeug.utils.secure_filename()` antes de guardarlo.

**Archivo:** `routes/leads.py` — handler de upload de adjuntos.

---

### Fix 3 — Comparación de contraseña vulnerable (dashboard.py)

`if password == expected` es vulnerable a timing attacks. Reemplazar con `secrets.compare_digest(password, expected)`.

**Archivo:** `dashboard.py` — función de login.

---

### Fix 4 — Errores internos expuestos al cliente (routes/budgets.py y otros)

Varios handlers devuelven `str(e)` directo al cliente, exponiendo rutas internas, nombres de variables y estructura de la app. Reemplazar con mensajes genéricos y loguear el error interno.

**Archivos:** `routes/budgets.py` (líneas ~199, ~243), cualquier otro que haga `jsonify({"error": str(e)})`.

---

### Fix 5 — Leads duplicados desde bot webhook (routes/wa.py)

El handler que recibe leads del bot no verifica si ya existe un lead con ese teléfono antes de crear uno nuevo. Agregar check: si existe, actualizar; si no, crear.

**Archivo:** `routes/wa.py` — handler POST de creación de lead.

---

### Fix 6 — parseInt sin validar en frontend (dashboard.py)

`parseInt(cb.dataset.id)` puede retornar NaN si el dataset está vacío o mal formado, enviando `NaN` a la API. Agregar guard: `if (isNaN(id)) return;`.

**Archivo:** `dashboard.py` — JS cerca de línea 901.

---

### Fix 7 — Estado del lead no se actualiza al borrar reunión

Cuando se borra una reunión (desde calendar chip o panel de cliente), el lead queda con estado `reunion_agendada` para siempre. Al borrar reunión, si el lead no tiene otras reuniones pendientes, retroceder el estado a `contactado`.

**Archivos:** `routes/calendar.py` — endpoints DELETE de meetings/events.

---

### Fix 8 — Budget JSON silent error

Cuando falla el parse de JSON de un presupuesto, el handler retorna objeto vacío sin avisar. Retornar error 400 con mensaje claro en lugar de `{}`.

**Archivo:** `routes/budgets.py` — handler de parse de budget items.

---

## Lo que NO incluye esta fase

- Refactor de dashboard.py (demasiado riesgo sin tests)
- Paginación (Fase B)
- CSRF completo (requiere flask-wtf, deja para Fase B)
- Multi-usuario (Fase C)

## Criterio de éxito

- Todos los fixes pasan revisión de código
- El servidor arranca sin errores
- Deploy exitoso en Railway
