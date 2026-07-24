// crm-metrics.js — panel 'metrics' extraido de crm.js (SRP). Carga despues de crm.js.


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
  const stateLabels = {sin_contactar:'Sin contactar',interesado:'Interesado',contactado:'Interesado',reunion_agendada:'Reunión agendada',reunion_hecha:'Reunión hecha',presupuesto_enviado:'Presupuesto enviado',negociacion:'Negociación',cliente_cerrado:'Cliente cerrado',en_desarrollo:'En desarrollo',finalizado:'Finalizado'};
  const stateColors = {sin_contactar:'#334155',interesado:'#10b981',contactado:'#10b981',reunion_agendada:'#f59e0b',reunion_hecha:'#f97316',presupuesto_enviado:'#eab308',negociacion:'#f97316',cliente_cerrado:'#22c55e',en_desarrollo:'#10b981',finalizado:'#4ade80'};
  const el = id => document.getElementById(id);

  try {
    const fetches = [fetch('/api/metrics')];
    if (window._isAdmin) fetches.push(fetch('/api/metrics/meta'));
    const results = await Promise.all(fetches);
    const m = await results[0].json();

    if (el('m-total'))        el('m-total').textContent        = m.total;
    if (el('m-contacted'))    el('m-contacted').textContent    = m.contacted;
    if (el('m-meetings'))     el('m-meetings').textContent     = m.meetings;
    if (el('m-closed'))       el('m-closed').textContent       = m.closed;
    if (el('m-contact-rate')) el('m-contact-rate').textContent = m.contact_rate + '%';
    if (el('m-meeting-rate')) el('m-meeting-rate').textContent = m.meeting_rate + '%';
    if (el('m-conv'))         el('m-conv').textContent         = m.conversion + '%';

    if (el('m-funnel')) el('m-funnel').innerHTML = _funnelBars(m.funnel, stateLabels, stateColors);

    if (el('m-calls')) {
      const cs = m.call_stats || {};
      const callItems = [
        {name:'Contestó',      count: cs['contestó']      || 0, color:'#22c55e'},
        {name:'No contestó',   count: cs['no_contestó']   || 0, color:'#f87171'},
        {name:'Buzón',         count: cs['buzón']          || 0, color:'#64748b'},
        {name:'Llamar después',count: cs['llamar_despues'] || 0, color:'#f59e0b'},
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

    if (el('m-months')) el('m-months').innerHTML = _monthBars(m.by_month);
    if (el('m-rubros')) el('m-rubros').innerHTML = _barList(m.top_rubros);
    if (el('m-cities')) el('m-cities').innerHTML = _barList(m.top_cities);

    if (window._isAdmin && results[1]) {
      document.getElementById('tab-meta-btn').style.display = '';
      const mm = await results[1].json();

      if (el('mm-total'))       el('mm-total').textContent       = mm.total;
      if (el('mm-month'))       el('mm-month').textContent       = mm.this_month;
      if (el('mm-week'))        el('mm-week').textContent        = mm.this_week;
      if (el('mm-conv'))        el('mm-conv').textContent        = mm.conversion + '%';

      if (el('mm-campaigns'))   el('mm-campaigns').innerHTML     = _barList(mm.by_campaign);
      if (el('mm-months'))      el('mm-months').innerHTML        = _monthBars(mm.by_month);
      if (el('mm-funnel'))      el('mm-funnel').innerHTML        = _funnelBars(mm.funnel, stateLabels, stateColors);
      if (el('mm-busca'))       el('mm-busca').innerHTML         = _barList(mm.que_busca);
      if (el('mm-presupuesto')) el('mm-presupuesto').innerHTML   = _barList(mm.presupuesto);
      if (el('mm-cities'))      el('mm-cities').innerHTML        = _barList(mm.top_cities);
    }

    if (el('metrics-date')) el('metrics-date').textContent = 'Actualizado: ' + new Date().toLocaleString('es-UY');
  } catch(e) {
    const p = document.getElementById('metrics-panel');
    if (p) p.insertAdjacentHTML('afterbegin','<p style="color:#f87171;margin-bottom:16px">Error cargando métricas.</p>');
  }
}
registerPanel('metrics', loadMetrics);