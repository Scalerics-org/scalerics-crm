// crm-calendar.js — panel 'calendar' extraido de crm.js (SRP). Carga despues de crm.js.


// ========== Calendar panel ==========
let calLoaded = false;
let calMonthOffset = 0;

function calChangeMonth(delta) {
  calMonthOffset += delta;
  renderCalendar();
}

function isoDate(d) {
  return d.toISOString().split('T')[0];
}

async function renderCalendar() {
  const now = new Date();
  const target = new Date(now.getFullYear(), now.getMonth() + calMonthOffset, 1);
  const year = target.getFullYear();
  const month = target.getMonth();
  const monthStart = new Date(year, month, 1);
  const monthEnd = new Date(year, month + 1, 0);

  const monthNames = ['Enero','Febrero','Marzo','Abril','Mayo','Junio','Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre'];
  document.getElementById('cal-week-label').textContent = monthNames[month] + ' ' + year;

  const daysEl = document.getElementById('cal-days');
  daysEl.innerHTML = '<div class="cal-loading">Cargando...</div>';
  document.getElementById('cal-error').style.display = 'none';

  const r = await fetch('/api/calendar/events?start='+isoDate(monthStart)+'&end='+isoDate(monthEnd));
  const d = await r.json();

  if (d.error) {
    document.getElementById('cal-error').textContent = d.error;
    document.getElementById('cal-error').style.display = 'block';
    daysEl.innerHTML = '';
    return;
  }

  const todayStr = isoDate(new Date());
  const dayNames = ['Lun','Mar','Mié','Jue','Vie','Sáb','Dom'];

  const eventMap = {};
  (d.events || []).forEach(ev => {
    if (!eventMap[ev.date]) eventMap[ev.date] = [];
    eventMap[ev.date].push(ev);
  });
  window._calEventMap = eventMap;

  let firstWeekday = monthStart.getDay() - 1;
  if (firstWeekday < 0) firstWeekday = 6;
  const daysInMonth = monthEnd.getDate();
  const totalCells = Math.ceil((firstWeekday + daysInMonth) / 7) * 7;

  const cells = [];
  for (let i = 0; i < totalCells; i++) {
    const dayNum = i - firstWeekday + 1;
    if (dayNum < 1 || dayNum > daysInMonth) {
      cells.push({ empty: true });
    } else {
      const ds = isoDate(new Date(year, month, dayNum));
      cells.push({ dayNum, ds, isToday: ds === todayStr, events: (eventMap[ds] || []).sort((a,b) => (a.time||'').localeCompare(b.time||'')) });
    }
  }

  daysEl.innerHTML = `<div class="cal-grid">
    ${dayNames.map(n => `<div class="cal-grid-header">${n}</div>`).join('')}
    ${cells.map(c => c.empty
      ? `<div class="cal-cell other-month"></div>`
      : `<div class="cal-cell${c.isToday?' today':''}" data-date="${c.ds}" onclick="_calCellClick(this,'${c.ds}')">
          <div class="cal-cell-day">${c.dayNum}</div>
          ${c.events.map(ev => {
            const ph = extractPhoneFromText((ev.title||'')+' '+(ev.description||''));
            const nm = extractNameFromTitle(ev.title||'');
            return `<div class="cal-event-chip ${ev.meeting_url?'meet':'regular'}" title="${esc((ev.time?ev.time+' ':'')+ev.title)}">
              ${ev.time?esc(ev.time)+' ':''}${ev.meeting_url?'🎥 ':''}${esc(ev.title||'')}
              ${ev.meeting_url?`<a class="cal-join-btn" href="${esc(ev.meeting_url)}" target="_blank" onclick="event.stopPropagation()">▶ Unirse</a>`:''}
              <button class="cal-demo-btn" onclick="event.stopPropagation();openDemoModal(${escJs(ph||'')},${escJs(ev.title||'')},${escJs(nm||'')})">📊 Generar Demo</button>
              <button class="cal-del-btn" onclick="event.stopPropagation();deleteCalEvent(${escJs(ev.id)},${escJs(ev.title||'')})">🗑 Borrar</button>
            </div>`;
          }).join('')}
        </div>`
    ).join('')}
  </div>`;
}

function _calCellClick(cell, dateStr) {
  if (window.innerWidth > 768) return;
  const mobileList = document.getElementById('cal-day-events-mobile');
  if (!mobileList) return;
  document.querySelectorAll('.cal-cell').forEach(c => c.style.outline = '');
  cell.style.outline = '2px solid #0088cc';
  const events = (window._calEventMap || {})[dateStr] || [];
  if (!events.length) {
    mobileList.innerHTML = '<div style="color:#475569;font-size:.78rem;padding:8px 0">Sin eventos este día.</div>';
  } else {
    mobileList.innerHTML = events.map(ev => `
      <div style="background:#111827;border:1px solid #1e293b;border-radius:10px;padding:12px;margin-bottom:8px">
        <div style="font-size:.82rem;font-weight:600;color:#f1f5f9">${esc(ev.title||'')}</div>
        ${ev.time ? `<div style="font-size:.72rem;color:#0088cc;margin-top:3px">🕐 ${esc(ev.time)}</div>` : ''}
        ${ev.meeting_url ? `<a href="${esc(ev.meeting_url)}" target="_blank" style="display:inline-flex;align-items:center;gap:4px;margin-top:6px;font-size:.72rem;color:#4ade80;text-decoration:none">▶ Unirse a reunión</a>` : ''}
      </div>
    `).join('');
  }
  mobileList.scrollIntoView({behavior:'smooth',block:'nearest'});
}

function openNewEventModal() {
  const today = isoDate(new Date());
  document.getElementById('ev-title').value = '';
  document.getElementById('ev-date').value = today;
  document.getElementById('ev-time').value = '10:00';
  document.getElementById('ev-duration').value = '60';
  document.getElementById('ev-desc').value = '';
  document.getElementById('ev-email').value = '';
  document.getElementById('ev-client-id').value = '';
  document.getElementById('ev-status').value = 'scheduled';
  document.getElementById('event-modal').classList.add('open');
}
function closeNewEventModal() { document.getElementById('event-modal').classList.remove('open'); }

async function deleteCalEvent(eventId, title) {
  if (!confirm('¿Borrar "' + title + '" del calendario?')) return;
  const r = await fetch('/api/calendar/meetings/' + eventId, { method: 'DELETE' });
  const d = await r.json();
  if (d.ok) { renderCalendar(); }
  else { alert('Error al borrar: ' + (d.error || 'desconocido')); }
}

async function saveEvent() {
  const title = document.getElementById('ev-title').value.trim();
  const date = document.getElementById('ev-date').value;
  const time = document.getElementById('ev-time').value;
  const duration = parseInt(document.getElementById('ev-duration').value) || 60;
  const desc = document.getElementById('ev-desc').value.trim();
  if (!title || !date || !time) { alert('Completá el título, fecha y hora'); return; }
  const meet_link = document.getElementById('ev-email').value.trim();
  const clientId = document.getElementById('ev-client-id').value.trim() || null;
  const meeting_status = document.getElementById('ev-status').value;
  const btn = document.getElementById('ev-save-btn');
  btn.disabled = true; btn.textContent = '...';
  const r = await fetch('/api/calendar/events', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({title,date,time,duration_min:duration,description:desc,meet_link,client_id:clientId,meeting_status})});
  const d = await r.json();
  btn.disabled = false; btn.textContent = '📅 Crear reunión';
  if (!d.ok) { alert('Error: '+(d.error||'Error desconocido')); return; }
  closeNewEventModal();
  renderCalendar();
}
registerPanel('cal', () => { if (!calLoaded) { calLoaded = true; renderCalendar(); } });
