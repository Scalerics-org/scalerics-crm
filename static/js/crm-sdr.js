// crm-sdr.js — panel 'sdr' extraido de crm.js (SRP). Carga despues de crm.js.



// ── Seguimientos (llamar_despues) ─────────────────────────────────────────────
function _sdrNameColor(name) {
  const colors = ['#0369a1','#7e22ce','#065f46','#9a3412','#be185d','#0f766e','#1d4ed8','#a16207'];
  let h = 0; for (let i = 0; i < (name||'').length; i++) h = (h * 31 + name.charCodeAt(i)) & 0xff;
  return colors[h % colors.length];
}

function _renderSdrStats(stats) {
  const bar = document.getElementById('sdr-stats-bar');
  if (!bar) return;
  if (!stats || !stats.length) { bar.innerHTML = '<div style="color:#334155;font-size:.75rem;padding:4px 0">Sin actividad registrada hoy</div>'; return; }
  bar.innerHTML = stats.map(s => {
    const color = _sdrNameColor(s.user);
    const initials = (s.user||'').split(' ').slice(0,2).map(w=>w[0]||'').join('').toUpperCase()||'?';
    const heat = s.count >= 15 ? '#10b981' : s.count >= 8 ? '#38bdf8' : s.count >= 3 ? '#fbbf24' : '#64748b';
    return `<div style="display:flex;align-items:center;gap:10px;background:#111827;border:1px solid #1e293b;border-radius:12px;padding:10px 14px;flex:0 0 auto">
      <div style="width:34px;height:34px;border-radius:50%;background:${color};display:flex;align-items:center;justify-content:center;font-size:.65rem;font-weight:800;color:#fff;flex-shrink:0">${initials}</div>
      <div>
        <div style="font-size:.82rem;font-weight:700;color:#f1f5f9">${esc(s.user)}</div>
        <div style="font-size:.68rem;color:#64748b">Hoy</div>
      </div>
      <div style="margin-left:8px;font-size:1.4rem;font-weight:800;color:${heat};min-width:28px;text-align:right">${s.count}</div>
    </div>`;
  }).join('');
}

// ── SDR panel ──────────────────────────────────────────────────────────────────
let _sdrPeriod = 'month';
function setSdrPeriod(p) {
  _sdrPeriod = p;
  ['week','month','year'].forEach(k => {
    const el = document.getElementById('sdr-pill-' + k);
    if (!el) return;
    if (k === p) { el.style.background='#0088cc'; el.style.color='#fff'; el.style.borderColor='#0088cc'; }
    else { el.style.background='transparent'; el.style.color='#64748b'; el.style.borderColor='#1e293b'; }
  });
  loadSdr();
}

async function loadSdr() {
  const wrap = document.getElementById('sdr-content');
  wrap.innerHTML = '<div style="color:#475569;padding:20px;font-size:.85rem">Cargando...</div>';
  const data = await fetch('/api/sdr-stats?period=' + _sdrPeriod).then(r => r.json()).catch(() => null);
  if (!data) { wrap.innerHTML = '<div style="color:#ef4444;padding:20px">Error al cargar datos.</div>'; return; }

  const days = [];
  const today = new Date(); today.setHours(0,0,0,0);
  for (let i = 13; i >= 0; i--) {
    const d = new Date(today); d.setDate(d.getDate() - i);
    days.push(d.toISOString().split('T')[0]);
  }
  const todayStr = days[days.length - 1];

  const byUser = {};
  for (const r of data.daily) {
    if (!byUser[r.user]) byUser[r.user] = {};
    byUser[r.user][r.day] = { calls: r.calls, leads: r.leads };
  }
  const periodCallsMap = {};
  for (const r of (data.period_calls || [])) periodCallsMap[r.user] = r.count;
  const periodReunionesMap = {};
  for (const r of (data.period_reuniones || [])) periodReunionesMap[r.user] = r.count;

  // llamar_despues neto por dia/user
  const llamarDespuesMap = {};
  for (const r of (data.llamar_despues || [])) {
    if (!llamarDespuesMap[r.user]) llamarDespuesMap[r.user] = {};
    llamarDespuesMap[r.user][r.day] = r.count;
  }
  // reuniones agendadas via calendario por dia/user
  const reunionesCalMap = {};
  for (const r of (data.reuniones_cal || [])) {
    if (!reunionesCalMap[r.user]) reunionesCalMap[r.user] = {};
    reunionesCalMap[r.user][r.day] = r.count;
  }
  const periodLabel = {week:'esta semana', month:'este mes', year:'este año'}[data.period || 'month'];

  // daily_outcomes: {user: {day: {outcome: count}}}
  const dailyOutMap = {};
  for (const o of (data.daily_outcomes || [])) {
    if (!dailyOutMap[o.user]) dailyOutMap[o.user] = {};
    if (!dailyOutMap[o.user][o.day]) dailyOutMap[o.user][o.day] = {};
    dailyOutMap[o.user][o.day][o.outcome] = (dailyOutMap[o.user][o.day][o.outcome] || 0) + o.count;
  }

  const users = (data.sdr_users && data.sdr_users.length ? data.sdr_users : Object.keys(byUser)).sort();
  if (!users.length) {
    wrap.innerHTML = '<div style="color:#475569;padding:20px">No hay llamadas registradas aun.</div>';
    return;
  }

  const cards = users.map(u => {
    const todayData = byUser[u] ? (byUser[u][todayStr] || { calls: 0 }) : { calls: 0 };
    const periodCalls = periodCallsMap[u] || 0;
    const color = _sdrNameColor(u);
    const initials = u.split(' ').slice(0,2).map(w=>w[0]||'').join('').toUpperCase()||'?';
    const heat = todayData.calls >= 30 ? '#10b981' : todayData.calls >= 15 ? '#38bdf8' : todayData.calls >= 5 ? '#fbbf24' : '#64748b';
    const todayOuts = (dailyOutMap[u] || {})[todayStr] || {};
    const reunion = todayOuts['reunion'] || 0;
    const interesado = todayOuts['interesado'] || 0;
    const noContesto = todayOuts['no_contestó'] || 0;
    const noInteresa = todayOuts['no_interesa'] || 0;
    const llamarDespuesHoy = (llamarDespuesMap[u] || {})[todayStr] || 0;
    const reunionesCalHoy  = (reunionesCalMap[u] || {})[todayStr] || 0;
    const periodReunionesCal = periodReunionesMap[u] || 0;
    return '<div style="background:#111827;border:1px solid #1e293b;border-radius:16px;padding:20px 24px;display:flex;flex-direction:column;gap:14px;min-width:220px;flex:1">'
      + '<div style="display:flex;align-items:center;gap:12px">'
      + '<div style="width:42px;height:42px;border-radius:50%;background:' + color + ';display:flex;align-items:center;justify-content:center;font-size:.75rem;font-weight:800;color:#fff;flex-shrink:0">' + initials + '</div>'
      + '<div><div style="font-size:.95rem;font-weight:700;color:#f1f5f9">' + esc(u) + '</div>'
      + '<div style="font-size:.7rem;color:#64748b">' + periodCalls + ' contactados · ' + periodReunionesCal + ' reuniones ' + periodLabel + '</div></div></div>'
      + '<div style="display:flex;align-items:flex-end;gap:8px">'
      + '<div data-u="' + esc(u) + '" data-d="' + todayStr + '" data-tp="calls" data-lb="hoy" onclick="if(Number(this.textContent)>0)openSdrDetailEl(this)" style="font-size:3rem;font-weight:800;color:' + heat + ';line-height:1' + (todayData.calls > 0 ? ';cursor:pointer' : '') + '">' + todayData.calls + '</div>'
      + '<div style="font-size:.8rem;color:#64748b;padding-bottom:6px">leads contactados hoy</div></div>'
      + '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;border-top:1px solid #1e293b;padding-top:12px">'
      + '<div style="text-align:center"><div style="font-size:1.1rem;font-weight:800;color:#3b82f6">' + reunionesCalHoy + '</div><div style="font-size:.62rem;color:#64748b">Reuniones agendadas</div></div>'
      + '<div data-u="' + esc(u) + '" data-d="' + todayStr + '" data-tp="interesado" data-lb="hoy" onclick="if(' + interesado + ')openSdrDetailEl(this)" style="text-align:center' + (interesado > 0 ? ';cursor:pointer' : '') + '"><div style="font-size:1.1rem;font-weight:800;color:#38bdf8">' + interesado + '</div><div style="font-size:.62rem;color:#64748b">Interesados hoy</div></div>'
      + '<div data-u="' + esc(u) + '" data-d="' + todayStr + '" data-tp="llamar_despues" data-lb="hoy" onclick="if(' + llamarDespuesHoy + ')openSdrDetailEl(this)" style="text-align:center' + (llamarDespuesHoy > 0 ? ';cursor:pointer' : '') + '"><div style="font-size:1.1rem;font-weight:800;color:#f59e0b">' + llamarDespuesHoy + '</div><div style="font-size:.62rem;color:#64748b">Llamar después</div></div>'
      + '<div data-u="' + esc(u) + '" data-d="' + todayStr + '" data-tp="no_contestó" data-lb="hoy" onclick="if(' + noContesto + ')openSdrDetailEl(this)" style="text-align:center' + (noContesto > 0 ? ';cursor:pointer' : '') + '"><div style="font-size:1.1rem;font-weight:800;color:#475569">' + noContesto + '</div><div style="font-size:.62rem;color:#64748b">No contestó hoy</div></div>'
      + '<div data-u="' + esc(u) + '" data-d="' + todayStr + '" data-tp="no_interesa" data-lb="hoy" onclick="if(' + noInteresa + ')openSdrDetailEl(this)" style="text-align:center' + (noInteresa > 0 ? ';cursor:pointer' : '') + '"><div style="font-size:1.1rem;font-weight:800;color:#ef4444">' + noInteresa + '</div><div style="font-size:.62rem;color:#64748b">No le interesa hoy</div></div>'
      + '<div data-u="' + esc(u) + '" data-d="' + todayStr + '" data-tp="reunion" data-lb="hoy" onclick="if(' + reunion + ')openSdrDetailEl(this)" style="text-align:center' + (reunion > 0 ? ';cursor:pointer' : '') + '"><div style="font-size:1.1rem;font-weight:800;color:#10b981">' + reunion + '</div><div style="font-size:.62rem;color:#64748b">Marcó reunión</div></div>'
      + '</div></div>';
  }).join('');

  const shortDay = d => { const dt = new Date(d+'T12:00:00'); return ['Dom','Lun','Mar','Mie','Jue','Vie','Sab'][dt.getDay()]+' '+dt.getDate(); };
  const maxCalls = Math.max(1, ...Object.values(byUser).flatMap(u => Object.values(u).map(d => d.calls)));

  const tableHead = '<tr>'
    + '<th style="text-align:left;padding:8px 12px;font-size:.72rem;color:#64748b;font-weight:600;white-space:nowrap" colspan="2">SDR</th>'
    + days.map(d => {
        const isToday = d === todayStr;
        return '<th style="padding:6px 4px;font-size:.62rem;color:' + (isToday?'#38bdf8':'#64748b') + ';font-weight:' + (isToday?700:500) + ';text-align:center;min-width:44px;white-space:nowrap' + (isToday?';border-bottom:2px solid #38bdf8':'') + '">' + shortDay(d) + '</th>';
      }).join('')
    + '<th style="padding:8px 12px;font-size:.72rem;color:#64748b;font-weight:600;text-align:center">Total</th></tr>';

  const outcomeRows = [
    { key: 'reunion',        label: 'Marcó reunión',     color: '#10b981', src: 'outcomes' },
    { key: 'reunion_cal',    label: 'Reuniones agendadas', color: '#3b82f6', src: 'cal' },
    { key: 'interesado',     label: 'Interesados',        color: '#38bdf8', src: 'outcomes' },
    { key: 'llamar_despues', label: 'Llamar después',     color: '#f59e0b', src: 'llamar' },
    { key: 'no_interesa',    label: 'No le interesa',     color: '#ef4444', src: 'outcomes' },
    { key: 'no_contestó',    label: 'No contestó',        color: '#475569', src: 'outcomes' },
  ];

  const tableRows = users.map(u => {
    const color = _sdrNameColor(u);
    const initials = u.split(' ').slice(0,2).map(w=>w[0]||'').join('').toUpperCase()||'?';
    const uDays = byUser[u] || {};
    const total = Object.values(uDays).reduce((s,d) => s + d.calls, 0);
    const callCells = days.map(d => {
      const v = (uDays[d] || {}).calls || 0;
      const isToday = d === todayStr;
      const intensity = v === 0 ? 0 : Math.min(1, v / (maxCalls * 0.7));
      const alpha = (0.15 + intensity * 0.75).toFixed(2);
      const bg = v === 0 ? (isToday ? '#0d1b2a' : 'transparent') : 'rgba(0,136,204,' + alpha + ')';
      const fw = v > 0 ? 700 : 400;
      const fc = v === 0 ? '#334155' : intensity > 0.5 ? '#fff' : '#93c5fd';
      const outline = isToday ? ';outline:1px solid #1e3a5f' : '';
      const cellEl = v > 0
        ? '<div data-u="' + esc(u) + '" data-d="' + d + '" data-tp="calls" data-lb="' + shortDay(d) + '" onclick="openSdrDetailEl(this)" style="display:inline-flex;align-items:center;justify-content:center;width:36px;height:26px;border-radius:6px;background:' + bg + ';font-size:.78rem;font-weight:' + fw + ';color:' + fc + outline + ';cursor:pointer">' + v + '</div>'
        : '<div style="display:inline-flex;align-items:center;justify-content:center;width:36px;height:26px;border-radius:6px;background:' + bg + ';font-size:.78rem;font-weight:' + fw + ';color:' + fc + outline + '">&middot;</div>';
      return '<td style="text-align:center;padding:4px 4px">' + cellEl + '</td>';
    }).join('');
    const nameCell = '<td style="padding:4px 12px;white-space:nowrap" rowspan="7"><div style="display:flex;align-items:center;gap:8px">'
      + '<div style="width:24px;height:24px;border-radius:50%;background:' + color + ';display:flex;align-items:center;justify-content:center;font-size:.55rem;font-weight:800;color:#fff;flex-shrink:0">' + initials + '</div>'
      + '<span style="font-size:.82rem;font-weight:600;color:#e2e8f0">' + esc(u.split(' ')[0]) + '</span>'
      + '</div></td>';
    const callRow = '<tr style="border-top:2px solid #1e293b">' + nameCell
      + '<td style="padding:4px 12px;font-size:.65rem;font-weight:700;color:#64748b;white-space:nowrap;text-align:right">Leads contactados</td>'
      + callCells
      + '<td style="text-align:center;padding:4px 12px;font-size:.88rem;font-weight:800;color:#f1f5f9">' + total + '</td></tr>';
    const outRows = outcomeRows.map(oc => {
      const cells = days.map(d => {
        const v = oc.src === 'cal'    ? ((reunionesCalMap[u]  || {})[d] || 0)
                : oc.src === 'llamar' ? ((llamarDespuesMap[u] || {})[d] || 0)
                : ((dailyOutMap[u] || {})[d] || {})[oc.key] || 0;
        const isToday = d === todayStr;
        const outline = isToday ? ';outline:1px solid #1e3a5f' : '';
        const ocEl = v > 0
          ? '<div data-u="' + esc(u) + '" data-d="' + d + '" data-tp="' + oc.key + '" data-lb="' + shortDay(d) + '" onclick="openSdrDetailEl(this)" style="display:inline-flex;align-items:center;justify-content:center;width:36px;height:20px;border-radius:4px;font-size:.7rem;font-weight:700;color:' + oc.color + outline + ';cursor:pointer">' + v + '</div>'
          : '<div style="display:inline-flex;align-items:center;justify-content:center;width:36px;height:20px;border-radius:4px;font-size:.7rem;font-weight:400;color:#1e293b' + outline + '">&middot;</div>';
        return '<td style="text-align:center;padding:2px 4px">' + ocEl + '</td>';
      }).join('');
      const ocTotal = days.reduce((s,d) => s + (
        oc.src === 'cal'    ? ((reunionesCalMap[u]  || {})[d] || 0)
        : oc.src === 'llamar' ? ((llamarDespuesMap[u] || {})[d] || 0)
        : ((dailyOutMap[u]||{})[d]||{})[oc.key]||0
      ), 0);
      return '<tr><td style="padding:2px 12px;font-size:.62rem;font-weight:600;color:' + oc.color + ';white-space:nowrap;text-align:right;opacity:.7">' + oc.label + '</td>'
        + cells
        + '<td style="text-align:center;padding:2px 12px;font-size:.75rem;font-weight:700;color:' + oc.color + '">' + (ocTotal||'&middot;') + '</td></tr>';
    }).join('');
    return callRow + outRows;
  }).join('');

  wrap.innerHTML = '<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:28px">' + cards + '</div>'
    + '<div style="background:#111827;border:1px solid #1e293b;border-radius:16px;padding:20px;overflow-x:auto">'
    + '<div style="font-size:.78rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:1px;margin-bottom:16px">Actividad por dia (ultimas 2 semanas)</div>'
    + '<table style="border-collapse:collapse;width:100%;min-width:600px"><thead>' + tableHead + '</thead><tbody>' + tableRows + '</tbody></table>'
    + '</div>';
}

// ── SDR detail modal ──────────────────────────────────────────────────────────
function closeSdrDetail() {
  document.getElementById('sdr-modal').style.display = 'none';
  document.getElementById('sdr-bd').style.display = 'none';
}
function openSdrDetailEl(el) {
  _openSdrDetail(el.getAttribute('data-u'), el.getAttribute('data-d'), el.getAttribute('data-tp'), el.getAttribute('data-lb'));
}
async function _openSdrDetail(user, day, type, label) {
  var modal = document.getElementById('sdr-modal');
  var bd    = document.getElementById('sdr-bd');
  var title = document.getElementById('sdr-modal-title');
  var body  = document.getElementById('sdr-modal-body');
  var tl    = {calls:'Llamadas',reunion:'Reuniones',interesado:'Interesados',no_interesa:'No le interesa','no_contestó':'No contestó'}[type] || type;
  title.textContent = user.split(' ')[0] + ' · ' + label + ' · ' + tl;
  body.innerHTML = '<div style="color:#475569;padding:12px 0;font-size:.82rem">Cargando...</div>';
  modal.style.display = 'block'; bd.style.display = 'block';
  var data = await fetch('/api/sdr-detail?user=' + encodeURIComponent(user) + '&day=' + day + '&type=' + encodeURIComponent(type)).then(function(r){return r.json();}).catch(function(){return null;});
  if (!data || !data.leads.length) { body.innerHTML = '<div style="color:#475569;padding:12px 0;font-size:.82rem">Sin registros.</div>'; return; }
  var ocColors  = {reunion:'#10b981',interesado:'#38bdf8',no_interesa:'#ef4444','no_contestó':'#64748b',llamar_despues:'#f59e0b'};
  var ocLabels  = {reunion:'Reunión',interesado:'Interesado',no_interesa:'No le interesa','no_contestó':'No contestó',llamar_despues:'Llamar después'};
  var totalCalls = data.leads.reduce(function(s,l){ return s + (l.call_count||1); }, 0);
  var totalLeads = data.leads.length;
  var summary = totalCalls !== totalLeads
    ? '<div style="font-size:.72rem;color:#475569;padding:4px 8px 10px;border-bottom:1px solid #1e293b;margin-bottom:6px">' + totalCalls + ' llamadas · ' + totalLeads + ' leads</div>'
    : '';
  body.innerHTML = summary + data.leads.map(function(l) {
    var time  = (l.last_time || '').split(' ')[1] || ''; time = time.slice(0,5);
    var ocCol = ocColors[l.last_outcome] || '#64748b';
    var ocLab = ocLabels[l.last_outcome] || l.last_outcome || '';
    var badge = l.call_count > 1 ? '<span style="font-size:.63rem;background:#1e293b;color:#64748b;padding:1px 6px;border-radius:99px;margin-left:4px">' + l.call_count + 'x</span>' : '';
    return '<div data-lid="' + Number(l.id) + '" onclick="closeSdrDetail();openClientPanel(Number(this.dataset.lid))" class="sdr-detail-row">'
      + '<div style="flex:1;min-width:0"><div style="font-size:.84rem;font-weight:600;color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + esc(l.name||'—') + badge + '</div>'
      + (ocLab ? '<div style="font-size:.7rem;color:' + ocCol + ';margin-top:1px">' + ocLab + '</div>' : '') + '</div>'
      + (time ? '<div style="font-size:.7rem;color:#475569;flex-shrink:0">' + time + '</div>' : '')
      + '</div>';
  }).join('');
}
registerPanel('sdr', loadSdr);
