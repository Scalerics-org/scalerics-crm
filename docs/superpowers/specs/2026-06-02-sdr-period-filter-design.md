# Spec: Filtro de periodo en panel SDR

**Fecha:** 2026-06-02
**Estado:** Aprobado

---

## Contexto

El panel SDR muestra métricas por vendedor (llamadas, reuniones, interesados, no contestó). Actualmente los outcomes no tienen filtro de fecha — muestran acumulado histórico — lo que genera confusión (ej. Franco Ghizzo muestra 10 "No contestó" que el equipo no puede relacionar con ningún periodo concreto). Se agrega un selector de periodo para que las cards reflejen datos del rango elegido.

---

## Alcance

**Cambia:** las cards de cada SDR (total de llamadas del periodo, reuniones, interesados, no contestó).

**No cambia:** el heatmap de llamadas por día (sigue mostrando los últimos 14 días fijos), el número grande de "llamadas hoy".

---

## Definición de periodos

| Filtro | Desde | Hasta |
|--------|-------|-------|
| Semana | Lunes de la semana actual | Hoy |
| Mes | 1ro del mes actual | Hoy |
| Año | 1ro de enero del año actual | Hoy |

Default al cargar el panel: **Mes**.

---

## Backend

### `GET /api/sdr-stats?period=week|month|year`

**Parámetro:** `period` (opcional, default `month`)

**Cálculo de `date_from` en Python:**

```python
from datetime import date, timedelta

period = request.args.get('period', 'month')
today = date.today()

if period == 'week':
    date_from = today - timedelta(days=today.weekday())  # lunes
elif period == 'year':
    date_from = today.replace(month=1, day=1)
else:  # month (default)
    date_from = today.replace(day=1)
```

**Queries adicionales (además de las ya existentes):**

```sql
-- period_calls: total de llamadas en el periodo por usuario
SELECT user_name, COUNT(*) as c
FROM activity_log
WHERE action = 'call_logged'
  AND user_name IN (...)
  AND DATE(created_at) >= ?
GROUP BY user_name

-- outcomes filtrados por periodo (reemplaza la query actual de outcomes)
SELECT user_name, detail as outcome, COUNT(*) as c
FROM activity_log
WHERE action = 'call_logged'
  AND user_name IN (...)
  AND DATE(created_at) >= ?
GROUP BY user_name, detail
```

**Respuesta JSON:**

```json
{
  "daily": [...],           // sin cambio — últimos 14 días para heatmap
  "outcomes": [...],        // ahora filtrados por date_from
  "period_calls": [         // nuevo campo
    {"user": "Franco Ghizzo", "count": 19}
  ],
  "sdr_users": [...],
  "period": "month"         // periodo efectivo usado
}
```

---

## Frontend

### UI — selector de periodo

Ubicación: en el `page-header` del panel SDR, entre el subtítulo y el botón actualizar.

```html
<div style="display:flex;gap:6px;align-items:center">
  <button onclick="setSdrPeriod('week')"  id="sdr-pill-week">Semana</button>
  <button onclick="setSdrPeriod('month')" id="sdr-pill-month">Mes</button>
  <button onclick="setSdrPeriod('year')"  id="sdr-pill-year">Año</button>
</div>
```

Estilos (igual que los tab-buttons del panel Métricas):
- Activo: `background:#0088cc; color:#fff; border-color:#0088cc`
- Inactivo: `background:transparent; color:#64748b; border:1px solid #1e293b`

### Estado

```js
let _sdrPeriod = 'month';

function setSdrPeriod(p) {
  _sdrPeriod = p;
  // actualizar estilos de pills
  loadSdr();
}
```

`loadSdr()` usa `_sdrPeriod` para construir la URL: `/api/sdr-stats?period=${_sdrPeriod}`

### Cards — cambios

- **Subtítulo bajo el nombre:** `"X llamadas esta semana"` / `"X llamadas este mes"` / `"X llamadas este año"` (usa `period_calls[user]` en lugar de sumar daily)
- **Reuniones / Interesados / No contestó:** usan `outcomes` del nuevo JSON filtrado (sin cambio en cómo se leen, solo los datos cambian)
- **Número grande "llamadas hoy":** sigue igual — lee `byUser[u][todayStr]` de `daily`

Texto del subtítulo según periodo:
```js
const periodLabel = { week: 'esta semana', month: 'este mes', year: 'este año' }[period];
// "19 llamadas este mes"
```

---

## Archivos a modificar

| Archivo | Cambios |
|---------|---------|
| `dashboard.py` | Función `api_sdr_stats()`: agregar param `period`, calcular `date_from`, agregar query `period_calls`, filtrar `outcomes` por `date_from`. HTML: agregar pills de periodo. JS: `_sdrPeriod`, `setSdrPeriod()`, actualizar `loadSdr()` y `loadSdr()` card render. |

Solo un archivo.

---

## Criterios de aceptación

- [ ] Los pills Semana / Mes / Año aparecen en el header del panel SDR
- [ ] Al cambiar el pill, las cards actualizan sus números
- [ ] El pill activo está resaltado en azul
- [ ] Default al cargar: Mes
- [ ] El subtítulo de la card refleja el periodo activo ("X llamadas este mes")
- [ ] Reuniones / Interesados / No contestó corresponden al periodo, no al historial completo
- [ ] El heatmap de 14 días no cambia
- [ ] El número grande "llamadas hoy" no cambia
