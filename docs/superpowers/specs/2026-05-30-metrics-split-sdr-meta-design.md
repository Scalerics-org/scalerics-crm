# Spec: Métricas separadas SDR / Meta Ads

**Fecha:** 2026-05-30  
**Estado:** Aprobado

---

## Contexto

El panel "Métricas" actual muestra KPIs de todos los leads mezclados, sin distinguir origen. Los leads de Meta Ads (`source = 'meta'`) tienen su propio panel (tabla), pero ningún análisis. Los admins quieren ver métricas detalladas de ambas fuentes por separado.

Restricción de acceso: solo admins pueden ver las métricas de Meta Ads.

---

## Decisiones de diseño

- **Un solo panel `metrics`** con dos tabs: `SDR` y `Meta Ads`
- El tab Meta Ads solo se renderiza si `window._isAdmin === true` (dato ya disponible desde `/api/me`)
- Panel key en el sistema de permisos no cambia (`metrics` sigue siendo el mismo panel)
- Dos endpoints de API separados: `/api/metrics` (SDR, existente) y `/api/metrics/meta` (nuevo, admin-only)

---

## Tab SDR

**Fuente:** todos los leads con `source IS NULL OR source != 'meta'`

### KPIs — fila 1 (4 tarjetas)
| Métrica | Descripción |
|---|---|
| Total leads SDR | COUNT de todos los leads SDR |
| Contactados | COUNT donde `crm_status = 'contactado'` o avanzó más |
| Reuniones agendadas | COUNT donde crm_status ∈ {reunion_agendada, reunion_hecha, ...} |
| Clientes cerrados | COUNT donde crm_status ∈ {cliente_cerrado, en_desarrollo, finalizado} |

### KPIs — fila 2 (3 tarjetas)
| Métrica | Cálculo |
|---|---|
| Tasa de contacto | contactados / total × 100 |
| Tasa de reunión | reuniones / contactados × 100 |
| Tasa de conversión | cerrados / total × 100 |

### Gráficos (2×2 grid)
1. **Funnel CRM** — barras horizontales, todos los estados, colores actuales
2. **Llamadas** — desglose de outcomes desde `activity_log` WHERE `action = 'call_logged'` filtrado a leads SDR: contestó / no contestó / buzón / llamar después
3. **Leads por mes** — últimos 12 meses, barras verticales (igual al actual)
4. **Top rubros + Top ciudades** — listas con barras horizontales (igual al actual)

---

## Tab Meta Ads *(solo admins)*

**Fuente:** todos los leads con `source = 'meta'`

### KPIs — fila 1 (4 tarjetas)
| Métrica | Descripción |
|---|---|
| Total leads Meta | COUNT source='meta' |
| Leads este mes | COUNT scraped_at >= inicio del mes actual |
| Leads esta semana | COUNT scraped_at >= lunes de la semana actual |
| Conversión Meta | cerrados_meta / total_meta × 100 |

### Gráficos
1. **Leads por campaña** — barras horizontales, nombre extraído del campo `notes` (split por `·`, tomar la parte después)
2. **Leads por mes** — últimos 12 meses, barras verticales
3. **Funnel CRM Meta** — en qué estado CRM están los leads que entraron por Meta
4. **Qué buscan** — desglose del campo `que_busca` / `que_buscas` de `form_data` (JSON)
5. **Presupuesto declarado** — desglose del campo `presupuesto` / `budget_range` de `form_data`
6. **Top ciudades Meta** — lista con barras

---

## API

### `GET /api/metrics` (modificado)
- Agrega filtro `WHERE (source IS NULL OR source != 'meta')` a todas las queries
- Agrega campo `call_stats`: `{contesto, no_contesto, buzon, llamar_despues}` desde `activity_log JOIN businesses WHERE source != 'meta'`
- Agrega campos `contacted_count`, `meeting_count` para KPIs de eficiencia

### `GET /api/metrics/meta` (nuevo, admin-only)
- Verifica `is_admin` via session; retorna 403 si no
- Retorna: `{total, this_month, this_week, conversion, by_campaign, by_month, funnel, que_busca, presupuesto, top_cities}`

---

## Frontend

### Estructura de tabs
```html
<div class="metrics-tabs">
  <button class="metrics-tab active" onclick="switchMetricsTab('sdr')">SDR</button>
  <button class="metrics-tab" id="tab-meta-btn" onclick="switchMetricsTab('meta')" style="display:none">Meta Ads</button>
</div>
<div id="metrics-sdr">...</div>
<div id="metrics-meta" style="display:none">...</div>
```

- Al cargar: si `window._isAdmin`, mostrar botón tab Meta
- `loadMetrics()` carga SDR siempre; si admin, también `/api/metrics/meta` en paralelo con `Promise.all`
- `switchMetricsTab(tab)` alterna visibilidad + clase `active`

### Estilo visual
- Tabs usando las variables CSS existentes del proyecto (mismo estilo que otros controles)
- KPIs: mismas clases `.stat-card`, `.stat-label`, `.stat-val`
- Gráficos: mismas clases `.m-card`, `.bar-row`, `.funnel-row`

---

## Archivos a modificar

| Archivo | Cambios |
|---|---|
| `routes/leads.py` | Modificar `api_metrics()`: filtrar SDR, agregar call_stats y KPIs de eficiencia |
| `dashboard.py` | Nuevo endpoint `GET /api/metrics/meta`; reemplazar panel metrics con tabs + nuevos elementos HTML + JS |

---

## Criterios de aceptación

- [ ] El tab "Meta Ads" no aparece para usuarios no-admin
- [ ] Los KPIs SDR solo cuentan leads con `source != 'meta'`  
- [ ] Las llamadas en SDR muestran desglose de outcomes reales desde activity_log
- [ ] Los KPIs Meta solo cuentan leads con `source = 'meta'`
- [ ] "Leads por campaña" muestra nombres reales de campaña, no IDs
- [ ] `GET /api/metrics/meta` retorna 403 para no-admins
- [ ] El layout funciona en mobile (grid colapsa a 1 columna)
