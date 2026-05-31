# Métricas separadas SDR / Meta Ads — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separar el panel Métricas en dos tabs — SDR y Meta Ads — donde el tab Meta Ads es visible solo para admins, con KPIs y gráficos completos para cada fuente.

**Architecture:** Se modifican dos archivos: `routes/leads.py` (endpoint `/api/metrics` existente + nuevo `/api/metrics/meta`) y `dashboard.py` (HTML del panel metrics + JS `loadMetrics()`). No se crean archivos nuevos. El panel key `metrics` no cambia — sin impacto en el sistema de permisos.

**Tech Stack:** Python/Flask, SQLite, HTML/CSS/JS vanilla (sin frameworks de UI). El JS existente en `dashboard.py` usa funciones globales y `fetch()`.

---

## File Map

| Archivo | Cambio |
|---|---|
| `routes/leads.py` | Modificar `api_metrics()` (línea 243) + agregar `api_metrics_meta()` |
| `dashboard.py` | Reemplazar HTML del `#metrics-panel` (líneas 1086–1120) + reemplazar `loadMetrics()` JS (líneas 3581–3626) |

---

## Task 1: Modificar `/api/metrics` para filtrar solo leads SDR y agregar nuevos campos

**Files:**
- Modify: `routes/leads.py:243-294`

- [ ] **Paso 1: Reemplazar `api_metrics()` completo**

Reemplazar desde `@leads_bp.route("/api/metrics")` hasta el `return jsonify(...)` con:

```python
@leads_bp.route("/api/metrics")
def api_metrics():
    from collections import Counter, defaultdict
    import sqlite3 as _sq

    # Solo leads SDR (excluye Meta)
    all_biz = get_all_businesses(_db())
    businesses = [b for b in all_biz if (b.get("source") or "") != "meta"]

    funnel_order = [
        "sin_contactar", "contactado", "reunion_agendada",
        "reunion_hecha", "presupuesto_enviado", "negociacion",
        "cliente_cerrado", "en_desarrollo", "finalizado",
    ]
    _legacy = {"firmo": "cliente_cerrado", "agendo": "reunion_agendada"}
    def _norm(s): return _legacy.get(s or "sin_contactar", s or "sin_contactar")

    crm_counts = Counter(_norm(b.get("crm_status")) for b in businesses)
    funnel = [{"status": s, "count": crm_counts.get(s, 0)} for s in funnel_order]

    rubro_counts = Counter(
        _normalize_category(b.get("category") or "") for b in businesses
        if _normalize_category(b.get("category") or "")
    )
    top_rubros = [{"name": k, "count": v} for k, v in rubro_counts.most_common(10)]

    city_counts = Counter(
        (b.get("city") or "").strip() for b in businesses if (b.get("city") or "").strip()
    )
    top_cities = [{"name": k, "count": v} for k, v in city_counts.most_common(10)]

    month_counts: dict = defaultdict(int)
    for b in businesses:
        ts = b.get("scraped_at") or ""
        if ts and len(ts) >= 7:
            month_counts[ts[:7]] += 1
    by_month = [{"month": m, "count": c} for m, c in sorted(month_counts.items())[-12:]]

    total = len(businesses)
    _contacted_st = {"contactado", "reunion_agendada", "reunion_hecha",
                     "presupuesto_enviado", "negociacion",
                     "cliente_cerrado", "en_desarrollo", "finalizado"}
    _meeting_st   = {"reunion_agendada", "reunion_hecha", "presupuesto_enviado",
                     "negociacion", "cliente_cerrado", "en_desarrollo", "finalizado"}
    _closed_st    = {"cliente_cerrado", "en_desarrollo", "finalizado"}

    contacted = sum(1 for b in businesses if _norm(b.get("crm_status")) in _contacted_st)
    meetings  = sum(1 for b in businesses if _norm(b.get("crm_status")) in _meeting_st)
    closed    = sum(1 for b in businesses if _norm(b.get("crm_status")) in _closed_st)

    contact_rate = round(contacted / total * 100, 1) if total else 0
    meeting_rate = round(meetings / contacted * 100, 1) if contacted else 0
    conversion   = round(closed / total * 100, 1) if total else 0

    # Stats de llamadas para leads SDR desde call_logs
    conn3 = _sq.connect(_db()); conn3.row_factory = _sq.Row
    try:
        rows = conn3.execute("""
            SELECT cl.outcome, COUNT(*) as cnt
            FROM call_logs cl
            JOIN businesses b ON cl.lead_id = b.id
            WHERE (b.source IS NULL OR b.source != 'meta')
            GROUP BY cl.outcome
        """).fetchall()
    finally:
        conn3.close()
    call_stats = {r["outcome"]: r["cnt"] for r in rows}

    return jsonify({
        "total": total,
        "contacted": contacted,
        "meetings": meetings,
        "closed": closed,
        "contact_rate": contact_rate,
        "meeting_rate": meeting_rate,
        "conversion": conversion,
        "funnel": funnel,
        "top_rubros": top_rubros,
        "top_cities": top_cities,
        "by_month": by_month,
        "call_stats": call_stats,
    })
```

- [ ] **Paso 2: Verificar que no hay errores de sintaxis**

```bash
cd C:\Users\juant\lead-gen-uy
python -c "from routes.leads import leads_bp; print('OK')"
```

Esperado: `OK`

- [ ] **Paso 3: Commit**

```bash
git add routes/leads.py
git commit -m "feat: filter SDR metrics to exclude Meta leads, add call_stats and efficiency KPIs"
```

---

## Task 2: Agregar endpoint `GET /api/metrics/meta` (admin-only)

**Files:**
- Modify: `routes/leads.py` (agregar después del bloque `api_metrics()`)

- [ ] **Paso 1: Agregar el endpoint después de `api_metrics()`**

Insertar este bloque directamente después del cierre de `api_metrics()` (antes del comentario `# ─── Attachments`):

```python
@leads_bp.route("/api/metrics/meta")
def api_metrics_meta():
    from collections import Counter, defaultdict
    from datetime import datetime, timezone, timedelta
    import sqlite3 as _sq4
    import json as _j4
    import os as _os4

    # Admin check
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "No autorizado"}), 403
    conn4 = _sq4.connect(_db()); conn4.row_factory = _sq4.Row
    try:
        u = conn4.execute("SELECT id, email FROM users WHERE id=?", (uid,)).fetchone()
    finally:
        conn4.close()
    if not u:
        return jsonify({"error": "No autorizado"}), 403
    admin_email = _os4.environ.get("ADMIN_EMAIL", "")
    is_admin = bool(
        (admin_email and u["email"].lower() == admin_email.lower())
        or (not admin_email and u["id"] == 1)
    )
    if not is_admin:
        return jsonify({"error": "No autorizado"}), 403

    businesses = get_all_businesses(_db(), source="meta")

    _legacy = {"firmo": "cliente_cerrado", "agendo": "reunion_agendada"}
    def _norm(s): return _legacy.get(s or "sin_contactar", s or "sin_contactar")
    _closed_st = {"cliente_cerrado", "en_desarrollo", "finalizado"}

    total  = len(businesses)
    now    = datetime.now(timezone.utc)
    month_prefix = now.strftime("%Y-%m")
    week_start   = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")

    this_month = sum(1 for b in businesses
                     if (b.get("scraped_at") or "").startswith(month_prefix))
    this_week  = sum(1 for b in businesses
                     if (b.get("scraped_at") or "")[:10] >= week_start)
    closed     = sum(1 for b in businesses if _norm(b.get("crm_status")) in _closed_st)
    conversion = round(closed / total * 100, 1) if total else 0

    # Leads por campaña (desde notes: "Meta Lead Ad · {campaign}")
    campaign_counts: Counter = Counter()
    for b in businesses:
        parts = (b.get("notes") or "").split(" · ", 1)
        campaign = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "Sin campaña"
        campaign_counts[campaign] += 1
    by_campaign = [{"name": k, "count": v} for k, v in campaign_counts.most_common(10)]

    # Leads por mes
    month_counts: defaultdict = defaultdict(int)
    for b in businesses:
        ts = b.get("scraped_at") or ""
        if ts and len(ts) >= 7:
            month_counts[ts[:7]] += 1
    by_month = [{"month": m, "count": c} for m, c in sorted(month_counts.items())[-12:]]

    # Funnel CRM
    funnel_order = ["sin_contactar", "contactado", "reunion_agendada", "reunion_hecha",
                    "presupuesto_enviado", "negociacion", "cliente_cerrado",
                    "en_desarrollo", "finalizado"]
    crm_counts = Counter(_norm(b.get("crm_status")) for b in businesses)
    funnel = [{"status": s, "count": crm_counts.get(s, 0)} for s in funnel_order]

    # Qué buscan / presupuesto desde form_data JSON
    que_busca_counts: Counter = Counter()
    presupuesto_counts: Counter = Counter()
    for b in businesses:
        try:
            fd = _j4.loads(b.get("form_data") or "{}")
            qb = (fd.get("que_busca") or fd.get("que_buscas") or fd.get("servicio") or "").strip()
            if qb:
                que_busca_counts[qb] += 1
            pr = (fd.get("presupuesto") or fd.get("budget_range") or fd.get("budget") or "").strip()
            if pr:
                presupuesto_counts[pr] += 1
        except Exception:
            pass

    city_counts = Counter(
        (b.get("city") or "").strip() for b in businesses if (b.get("city") or "").strip()
    )

    return jsonify({
        "total":       total,
        "this_month":  this_month,
        "this_week":   this_week,
        "conversion":  conversion,
        "by_campaign": by_campaign,
        "by_month":    by_month,
        "funnel":      funnel,
        "que_busca":   [{"name": k, "count": v} for k, v in que_busca_counts.most_common(10)],
        "presupuesto": [{"name": k, "count": v} for k, v in presupuesto_counts.most_common(10)],
        "top_cities":  [{"name": k, "count": v} for k, v in city_counts.most_common(10)],
    })
```

- [ ] **Paso 2: Verificar sintaxis**

```bash
python -c "from routes.leads import leads_bp; print('OK')"
```

Esperado: `OK`

- [ ] **Paso 3: Commit**

```bash
git add routes/leads.py
git commit -m "feat: add /api/metrics/meta admin-only endpoint with full Meta KPIs"
```

---

## Task 3: Reemplazar HTML del panel Métricas con tabs SDR / Meta Ads

**Files:**
- Modify: `dashboard.py:1086-1120` (el bloque `<!-- ======= METRICS PANEL ======= -->`)

- [ ] **Paso 1: Reemplazar el bloque HTML del panel**

Buscar exactamente:
```
  <!-- ======= METRICS PANEL ======= -->
  <div id="metrics-panel" class="panel">
    <div class="page-header">
      <div>
        <h1>Métricas</h1>
        <div class="page-date" id="metrics-date"></div>
      </div>
      <button class="export-btn" onclick="loadMetrics()">↻ Actualizar</button>
    </div>
    <div class="metrics-grid" id="metrics-kpis">
      <div class="stat-card"><div class="stat-label">Total leads</div><div class="stat-val" id="m-total">—</div></div>
      <div class="stat-card"><div class="stat-label">Clientes cerrados</div><div class="stat-val green" id="m-closed">—</div></div>
      <div class="stat-card"><div class="stat-label">Tasa de conversión</div><div class="stat-val blue" id="m-conv">—</div></div>
    </div>
    <div class="metrics-grid-2">
      <div class="m-card">
        <div class="m-card-title">Funnel CRM</div>
        <div id="m-funnel"></div>
      </div>
      <div class="m-card">
        <div class="m-card-title">Top rubros</div>
        <div id="m-rubros"></div>
      </div>
    </div>
    <div class="metrics-grid-2">
      <div class="m-card">
        <div class="m-card-title">Leads por mes</div>
        <div id="m-months"></div>
      </div>
      <div class="m-card">
        <div class="m-card-title">Top ciudades</div>
        <div id="m-cities"></div>
      </div>
    </div>
  </div>
```

Reemplazar con:
```
  <!-- ======= METRICS PANEL ======= -->
  <div id="metrics-panel" class="panel">
    <div class="page-header">
      <div>
        <h1>Métricas</h1>
        <div class="page-date" id="metrics-date"></div>
      </div>
      <button class="export-btn" onclick="loadMetrics()">↻ Actualizar</button>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:24px">
      <button id="tab-sdr-btn" onclick="switchMetricsTab('sdr')" style="padding:6px 18px;border-radius:8px;border:1px solid #1e293b;background:#0088cc;color:#fff;font-size:.82rem;font-weight:700;cursor:pointer;font-family:'Inter',sans-serif">SDR</button>
      <button id="tab-meta-btn" onclick="switchMetricsTab('meta')" style="display:none;padding:6px 18px;border-radius:8px;border:1px solid #1e293b;background:transparent;color:#64748b;font-size:.82rem;font-weight:700;cursor:pointer;font-family:'Inter',sans-serif">Meta Ads</button>
    </div>
    <!-- Tab SDR -->
    <div id="metrics-sdr">
      <div class="metrics-grid" style="grid-template-columns:repeat(4,1fr)">
        <div class="stat-card"><div class="stat-label">Total leads SDR</div><div class="stat-val" id="m-total">—</div></div>
        <div class="stat-card"><div class="stat-label">Contactados</div><div class="stat-val blue" id="m-contacted">—</div></div>
        <div class="stat-card"><div class="stat-label">Reuniones agendadas</div><div class="stat-val" style="color:#f59e0b" id="m-meetings">—</div></div>
        <div class="stat-card"><div class="stat-label">Clientes cerrados</div><div class="stat-val green" id="m-closed">—</div></div>
      </div>
      <div class="metrics-grid" style="grid-template-columns:repeat(3,1fr)">
        <div class="stat-card"><div class="stat-label">Tasa de contacto</div><div class="stat-val blue" id="m-contact-rate">—</div></div>
        <div class="stat-card"><div class="stat-label">Tasa de reunión</div><div class="stat-val" style="color:#f59e0b" id="m-meeting-rate">—</div></div>
        <div class="stat-card"><div class="stat-label">Tasa de conversión</div><div class="stat-val green" id="m-conv">—</div></div>
      </div>
      <div class="metrics-grid-2">
        <div class="m-card"><div class="m-card-title">Funnel CRM</div><div id="m-funnel"></div></div>
        <div class="m-card"><div class="m-card-title">Llamadas</div><div id="m-calls"></div></div>
      </div>
      <div class="metrics-grid-2">
        <div class="m-card"><div class="m-card-title">Leads por mes</div><div id="m-months"></div></div>
        <div class="m-card"><div class="m-card-title">Top rubros</div><div id="m-rubros"></div></div>
      </div>
      <div class="metrics-grid-2">
        <div class="m-card"><div class="m-card-title">Top ciudades</div><div id="m-cities"></div></div>
      </div>
    </div>
    <!-- Tab Meta Ads (solo admin) -->
    <div id="metrics-meta" style="display:none">
      <div class="metrics-grid" style="grid-template-columns:repeat(4,1fr)">
        <div class="stat-card"><div class="stat-label">Total leads Meta</div><div class="stat-val" id="mm-total">—</div></div>
        <div class="stat-card"><div class="stat-label">Este mes</div><div class="stat-val blue" id="mm-month">—</div></div>
        <div class="stat-card"><div class="stat-label">Esta semana</div><div class="stat-val" style="color:#f59e0b" id="mm-week">—</div></div>
        <div class="stat-card"><div class="stat-label">Conversión Meta</div><div class="stat-val green" id="mm-conv">—</div></div>
      </div>
      <div class="metrics-grid-2">
        <div class="m-card"><div class="m-card-title">Leads por campaña</div><div id="mm-campaigns"></div></div>
        <div class="m-card"><div class="m-card-title">Leads por mes</div><div id="mm-months"></div></div>
      </div>
      <div class="metrics-grid-2">
        <div class="m-card"><div class="m-card-title">Funnel CRM Meta</div><div id="mm-funnel"></div></div>
        <div class="m-card"><div class="m-card-title">Qué buscan</div><div id="mm-busca"></div></div>
      </div>
      <div class="metrics-grid-2">
        <div class="m-card"><div class="m-card-title">Presupuesto declarado</div><div id="mm-presupuesto"></div></div>
        <div class="m-card"><div class="m-card-title">Top ciudades Meta</div><div id="mm-cities"></div></div>
      </div>
    </div>
  </div>
```

- [ ] **Paso 2: Verificar que el servidor arranca sin error de template**

```bash
python -c "from dashboard import create_app; app = create_app('leads.db'); print('OK')"
```

Esperado: `OK` (o que no haya SyntaxError/TemplateError)

- [ ] **Paso 3: Commit**

```bash
git add dashboard.py
git commit -m "feat: metrics panel HTML — tabs SDR/Meta Ads with full KPI cards and chart slots"
```

---

## Task 4: Reemplazar `loadMetrics()` JS con tabs, renderizado SDR y Meta

**Files:**
- Modify: `dashboard.py:3581-3626` (función `loadMetrics()`)

- [ ] **Paso 1: Reemplazar la función `loadMetrics()` completa**

Buscar exactamente:
```
async function loadMetrics() {
  try {
    const r = await fetch('/api/metrics');
```
(hasta el cierre `}` de la función, línea ~3626)

Reemplazar todo el bloque `async function loadMetrics() { ... }` con:

```javascript
let _metricsTab = 'sdr';

function switchMetricsTab(tab) {
  _metricsTab = tab;
  document.getElementById('metrics-sdr').style.display  = tab === 'sdr'  ? '' : 'none';
  document.getElementById('metrics-meta').style.display = tab === 'meta' ? '' : 'none';
  const sdrBtn  = document.getElementById('tab-sdr-btn');
  const metaBtn = document.getElementById('tab-meta-btn');
  if (sdrBtn)  { sdrBtn.style.background  = tab === 'sdr'  ? '#0088cc' : 'transparent'; sdrBtn.style.color  = tab === 'sdr'  ? '#fff' : '#64748b'; }
  if (metaBtn) { metaBtn.style.background = tab === 'meta' ? '#e1306c' : 'transparent'; metaBtn.style.color = tab === 'meta' ? '#fff' : '#64748b'; }
}

function _barList(items, maxVal) {
  if (!items || !items.length) return '<div style="color:#475569;font-size:.8rem">Sin datos</div>';
  const max = maxVal || Math.max(...items.map(i => i.count), 1);
  return items.map(i => {
    const pct = Math.round(i.count / max * 100);
    return `<div class="bar-row"><div class="bar-label">${esc(i.name)}</div><div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div><div class="bar-val">${i.count}</div></div>`;
  }).join('');
}

function _monthBars(items) {
  if (!items || !items.length) return '<div style="color:#475569;font-size:.8rem">Sin datos</div>';
  const max = Math.max(...items.map(b => b.count), 1);
  return '<div class="month-bars">' + items.map(b => {
    const h = Math.max(4, Math.round(b.count / max * 60));
    const short = b.month.length >= 7 ? b.month.slice(5) : b.month;
    return `<div class="month-col"><div style="font-size:.6rem;color:#64748b;line-height:1;margin-bottom:2px">${b.count}</div><div class="month-bar" style="height:${h}px"></div><div class="month-tick">${short}</div></div>`;
  }).join('') + '</div>';
}

function _funnelBars(items, stateLabels, stateColors) {
  if (!items || !items.length) return '<div style="color:#475569;font-size:.8rem">Sin datos</div>';
  const max = Math.max(...items.map(f => f.count), 1);
  return items.filter(f => f.count > 0).map(f => {
    const pct = Math.round(f.count / max * 100);
    const col = (stateColors && stateColors[f.status]) || '#64748b';
    return `<div class="funnel-row"><div class="funnel-label">${esc(stateLabels[f.status] || f.status)}</div><div class="bar-track" style="flex:1"><div class="bar-fill" style="width:${pct}%;background:${col}"></div></div><div class="bar-val">${f.count}</div></div>`;
  }).join('') || '<div style="color:#475569;font-size:.8rem">Sin datos</div>';
}

async function loadMetrics() {
  const stateLabels = {sin_contactar:'Sin contactar',contactado:'Contactado',reunion_agendada:'Reunión agendada',reunion_hecha:'Reunión hecha',presupuesto_enviado:'Presupuesto enviado',negociacion:'Negociación',cliente_cerrado:'Cliente cerrado',en_desarrollo:'En desarrollo',finalizado:'Finalizado'};
  const stateColors = {sin_contactar:'#334155',contactado:'#3b82f6',reunion_agendada:'#f59e0b',reunion_hecha:'#f97316',presupuesto_enviado:'#eab308',negociacion:'#f97316',cliente_cerrado:'#22c55e',en_desarrollo:'#10b981',finalizado:'#4ade80'};
  const el = id => document.getElementById(id);

  try {
    const fetches = [fetch('/api/metrics')];
    if (window._isAdmin) fetches.push(fetch('/api/metrics/meta'));
    const results = await Promise.all(fetches);
    const m = await results[0].json();

    // SDR KPIs fila 1
    if (el('m-total'))        el('m-total').textContent        = m.total;
    if (el('m-contacted'))    el('m-contacted').textContent    = m.contacted;
    if (el('m-meetings'))     el('m-meetings').textContent     = m.meetings;
    if (el('m-closed'))       el('m-closed').textContent       = m.closed;
    // SDR KPIs fila 2
    if (el('m-contact-rate')) el('m-contact-rate').textContent = m.contact_rate + '%';
    if (el('m-meeting-rate')) el('m-meeting-rate').textContent = m.meeting_rate + '%';
    if (el('m-conv'))         el('m-conv').textContent         = m.conversion + '%';

    // Funnel SDR
    if (el('m-funnel')) el('m-funnel').innerHTML = _funnelBars(m.funnel, stateLabels, stateColors);

    // Llamadas SDR
    if (el('m-calls')) {
      const cs = m.call_stats || {};
      const callItems = [
        {name:'Contestó',     count: cs['contestó']      || 0, color:'#22c55e'},
        {name:'No contestó',  count: cs['no_contestó']   || 0, color:'#f87171'},
        {name:'Buzón',        count: cs['buzón']          || 0, color:'#64748b'},
        {name:'Llamar después',count:cs['llamar_despues'] || 0, color:'#f59e0b'},
      ].filter(i => i.count > 0);
      if (callItems.length) {
        const maxC = Math.max(...callItems.map(i => i.count), 1);
        el('m-calls').innerHTML = callItems.map(i => {
          const pct = Math.round(i.count / maxC * 100);
          return `<div class="bar-row"><div class="bar-label">${i.name}</div><div class="bar-track"><div class="bar-fill" style="width:${pct}%;background:${i.color}"></div></div><div class="bar-val">${i.count}</div></div>`;
        }).join('');
      } else {
        el('m-calls').innerHTML = '<div style="color:#475569;font-size:.8rem">Sin llamadas registradas</div>';
      }
    }

    // Leads por mes SDR
    if (el('m-months')) el('m-months').innerHTML = _monthBars(m.by_month);
    // Top rubros
    if (el('m-rubros')) el('m-rubros').innerHTML = _barList(m.top_rubros);
    // Top ciudades SDR
    if (el('m-cities')) el('m-cities').innerHTML = _barList(m.top_cities);

    // Meta tab
    if (window._isAdmin && results[1]) {
      document.getElementById('tab-meta-btn').style.display = '';
      const mm = await results[1].json();

      if (el('mm-total'))  el('mm-total').textContent  = mm.total;
      if (el('mm-month'))  el('mm-month').textContent  = mm.this_month;
      if (el('mm-week'))   el('mm-week').textContent   = mm.this_week;
      if (el('mm-conv'))   el('mm-conv').textContent   = mm.conversion + '%';

      if (el('mm-campaigns')) el('mm-campaigns').innerHTML = _barList(mm.by_campaign);
      if (el('mm-months'))    el('mm-months').innerHTML    = _monthBars(mm.by_month);
      if (el('mm-funnel'))    el('mm-funnel').innerHTML    = _funnelBars(mm.funnel, stateLabels, stateColors);
      if (el('mm-busca'))     el('mm-busca').innerHTML     = _barList(mm.que_busca);
      if (el('mm-presupuesto')) el('mm-presupuesto').innerHTML = _barList(mm.presupuesto);
      if (el('mm-cities'))    el('mm-cities').innerHTML    = _barList(mm.top_cities);
    }

    if (el('metrics-date')) el('metrics-date').textContent = 'Actualizado: ' + new Date().toLocaleString('es-UY');
  } catch(e) {
    const p = document.getElementById('metrics-panel');
    if (p) p.insertAdjacentHTML('afterbegin','<p style="color:#f87171;margin-bottom:16px">Error cargando métricas.</p>');
  }
}
```

**Nota:** La variable `window._isAdmin` se asigna en el bloque JS que procesa `/api/me`. Verificar que esa asignación existe buscando `_isAdmin` en `dashboard.py`. Si no existe como `window._isAdmin`, agregar `window._isAdmin = m.is_admin;` donde se procesa la respuesta de `/api/me`.

- [ ] **Paso 2: Verificar que `window._isAdmin` se asigna desde `/api/me`**

Buscar en `dashboard.py`:
```bash
grep -n "_isAdmin" dashboard.py
```

Si no aparece `window._isAdmin = `, buscar dónde se procesa `is_admin` del response de `/api/me` y agregar `window._isAdmin = m.is_admin;` en ese bloque.

- [ ] **Paso 3: Commit**

```bash
git add dashboard.py
git commit -m "feat: metrics JS — tabs SDR/Meta, full rendering for both data sources"
```

---

## Task 5: Push y verificación en Railway

- [ ] **Paso 1: Push**

```bash
git push origin main
```

- [ ] **Paso 2: Esperar deploy (~1 min) y verificar**

En el browser:
1. Entrar al panel Métricas — debe mostrar solo tab "SDR" activo con los nuevos KPIs
2. Verificar que los números de SDR excluyen leads Meta (total debe ser menor que antes si hay leads Meta)
3. Verificar desglose de llamadas (contestó / no contestó / etc.)
4. Como admin: verificar que aparece el tab "Meta Ads"
5. Cambiar a tab Meta Ads: verificar KPIs (total, este mes, semana, conversión)
6. Verificar "Leads por campaña" muestra nombres reales (no IDs)
7. Verificar "Qué buscan" y "Presupuesto declarado" tienen datos
8. Como usuario no-admin: verificar que el tab Meta Ads NO aparece
9. Verificar que `GET /api/metrics/meta` retorna 403 para no-admins

- [ ] **Paso 3: Commit final si hubo ajustes**

```bash
git add -p
git commit -m "fix: metrics tab adjustments post-deploy"
git push origin main
```
