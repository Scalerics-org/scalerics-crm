# Clean Code — Convenciones (adaptadas a Python + JS)

Basado en la guía de Clean Code (curso DA2, originalmente **C#/.NET**). Este repo es **Python/Flask + JS vanilla**, así que acá está traducido al stack real. Los tips que son puramente de C#/.NET se marcan y se adaptan al principio equivalente. Aplicar al **código que se toca o se agrega** — no reescribir todo de golpe.

---

## Legibilidad

**Condiciones complejas → variable/función con nombre descriptivo** (tip 1)
```python
# ✗  if b.score >= 60 and not b.website and b.review_count > 10:
es_lead_caliente = b.score >= 60 and not b.website and b.review_count > 10
if es_lead_caliente:
```
```js
const isHotLead = b.score >= 60 && !b.website && b.reviewCount > 10;
if (isHotLead) { ... }
```

**Agrupar ifs anidados en una sola condición** (tip 2). Menos caminos lógicos, más fácil de leer.

**Guard clauses / fail-fast** (tip 6). Validar y `return`/`raise` temprano; reduce anidamiento.
```python
def procesar(lead):
    if lead is None:
        raise ValueError("lead requerido")
    if not lead.phone:
        return None
    # ...camino feliz sin anidar
```

**Evitar negaciones poco claras** (tip 16). Nombrar la condición o comparar explícito: `if x is None:` mejor que `if not x.has_value`.

**Cadenas de métodos en vertical, no horizontal** (tip 7b). Una operación por línea.
```python
resultados = (
    leads
    .filter(sin_web)
    .sorted(key=lambda l: l.score, reverse=True)
)
```

---

## Funciones

**Máximo ~2–3 argumentos; agrupar el resto en un objeto** (tip 5).
```python
# ✗  def crear_tarea(titulo, desc, prioridad, deadline, assignee, cliente): ...
@dataclass
class NuevaTarea:
    titulo: str
    descripcion: str
    prioridad: str
    deadline: date | None
    assignee_id: int | None
def crear_tarea(t: NuevaTarea): ...
```
```js
// ✗  openAddTaskModal(clientId, clientName)
// ✓  objeto de opciones
openAddTaskModal({ clientId, clientName });
```

**Encapsular el mapeo Request→entidad y entidad→response** en una función/método dedicado, no en el route/controller (tips 4, 14). Routes finos.
```python
def lead_to_response(b) -> dict:
    return {"id": b.id, "name": b.name, "score": b.score}
```

**Mapear con comprehensions/`map`, no loops manuales** (tip 12).
```python
responses = [lead_to_response(b) for b in leads]   # no for + append
```
```js
const rows = leads.map(leadToRow);
```

---

## Tipos y datos (menos "stringly typed")

**Nada de strings mágicos para estados/roles — usar enums/constantes** (tip 13). *El más relevante en este repo* (hoy hay `crm_status`, task status como `'todo'/'in_progress'/'done'`, roles, etc. comparados como strings sueltos).
```python
from enum import Enum
class CrmStatus(str, Enum):
    SIN_CONTACTAR = "sin_contactar"
    SEGUIMIENTO   = "seguimiento"
    NO_INTERESA   = "no_interesa"
# uso: if b.crm_status == CrmStatus.SIN_CONTACTAR:
```
```js
const TASK_STATUS = Object.freeze({ TODO: 'todo', IN_PROGRESS: 'in_progress', DONE: 'done' });
```

**Enum→string sin literales mágicos** (tip 3 — `nameof` es de C#). Equivalente: usar `MiEnum.VALOR.value`/`.name`, nunca re-escribir el string a mano.

**Parseo de fechas explícito** con formato + timezone (tip 12b). Nunca `parse` ambiguo dependiente del SO.
```python
from datetime import datetime, timezone
d = datetime.strptime(s, "%d-%m-%Y").replace(tzinfo=timezone.utc)
```

**Constantes inmutables** (tip 17 — `readonly`/`const` de C#). Python: constantes `UPPER_CASE` a nivel módulo. JS: `const`.

**Identidad de entidades: PK `id` consistente** (tip 18). Las entidades con identidad usan `id` como primary key.

---

## Base de datos

**No hacer `commit` dentro de loops** (tip 21 — `SaveChanges` de EF Core). Acumular y commitear una sola vez fuera del loop.
```python
for lead in leads:
    cur.execute("INSERT ...", lead)
conn.commit()   # una sola vez, no adentro del for
```
> Buscar este anti-patrón en `database.py`, `scraper.py`, `deployer.py`.

---

## Nombres — convenciones por lenguaje (tips 8, 9, 10, 11 son de C#, se adaptan)

- **Python:** `snake_case` para funciones/variables/módulos, `PascalCase` para clases, `_prefijo` para privado, `UPPER_CASE` para constantes.
- **JS:** `camelCase` para variables/funciones, `PascalCase` para clases, `_prefijo` para "privado".
- **Clases `PascalCase`** en ambos (tip 10, universal).
- (tip 11, nombres de proyecto `Contexto.Tipo` de C#: no aplica a módulos Python/JS.)

---

## Tests

**Orden de setup de mocks = orden de ejecución en la lógica** (tip 19). En pytest, ordenar los `mock.patch`/fixtures como se llaman en el código bajo prueba. Facilita encontrar errores.

---

## Organización

**Organizar por negocio/feature, no por tipo técnico** (tip 20). *Matiz honesto:* tu `routes/` y `services/` ya están bastante orientados a feature (leads, budgets, calendar, tasks) — eso está bien. No forzar un rework de carpetas ahora; tenerlo como norte al agregar features nuevas.

**Versionar la API ante breaking changes** (tip 15 — ASP.NET). Principio: no romper endpoints `/api/` existentes; si un cambio es incompatible, versionar (`/api/v2/...`) en vez de cambiar el comportamiento en el lugar.

---

## Regla anti-sobre-ingeniería

Estas prácticas se aplican al **código que se toca o se agrega**. No reescribir 10.000 líneas de una. Durante el refactor de extracción, seguir estas convenciones al mover cada pieza; los "quick wins" grandes (enums de estado, batch commits) van como tarea separada y acotada.
