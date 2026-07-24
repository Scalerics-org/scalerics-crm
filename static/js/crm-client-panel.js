// crm-client-panel.js — panel 'client-panel' extraido de crm.js (SRP). Carga despues de crm.js.


// ── Client panel: Tasks tab ───────────────────────────────────────────────────

function _cpRenderTasks() {
  const tasks = _cpData.tasks || [];
  const pending = tasks.filter(t => t.status !== 'done');
  const done = tasks.filter(t => t.status === 'done');
  const renderList = (list) => list.length
    ? list.map(t => _taskRowHtml(t)).join('')
    : '<div style="color:#334155;font-size:.78rem">Sin tareas.</div>';
  return `<div class="cp-section">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <div class="cp-section-title" style="margin:0">Pendientes</div>
      <button class="cp-btn cp-btn-ghost" onclick="openAddTaskModal(${_cpClientId},'${esc((_cpData.lead||{}).name||'')}')">+ Nueva</button>
    </div>
    ${renderList(pending)}
  </div>
  ${done.length ? `<div class="cp-section">
    <div class="cp-section-title">Completadas</div>
    ${renderList(done)}
  </div>` : ''}`;
}

async function _cpBindTasks() {
  if (!_cpData.tasks) {
    try {
      const r = await fetch('/api/tasks?client_id=' + _cpClientId);
      _cpData.tasks = await r.json();
    } catch { _cpData.tasks = []; }
    _cpSwitchTab('ctasks');
  }
}

// ── Client Panel ─────────────────────────────────────────────────────────────

let _cpClientId = null;
let _cpTab = 'info';
let _cpData = {};

function openClientPanel(id) {
  _cpClientId = id;
  _cpTab = 'info';
  document.getElementById('cp-backdrop').classList.add('open');
  document.getElementById('client-panel').classList.add('open');
  _cpLoadAll();
}

function closeClientPanel() {
  document.getElementById('cp-backdrop').classList.remove('open');
  document.getElementById('client-panel').classList.remove('open');
  _cpClientId = null;
  _cpData = {};
}

async function _cpLoadAll() {
  if (!_cpClientId) return;
  const [leadRes, meetRes, budgetRes, demoRes, attBudgetRes, attDemoRes, eventsRes, callsRes] = await Promise.allSettled([
    fetch('/api/leads/' + _cpClientId).then(r => r.json()),
    fetch('/api/calendar/clients/' + _cpClientId + '/meetings').then(r => r.json()),
    fetch('/api/leads/' + _cpClientId + '/budget').then(r => r.json()),
    fetch('/api/demo/status/' + _cpClientId).then(r => r.json()),
    fetch('/api/leads/' + _cpClientId + '/attachments?section=budget').then(r => r.json()),
    fetch('/api/leads/' + _cpClientId + '/attachments?section=demo').then(r => r.json()),
    fetch('/api/leads/' + _cpClientId + '/events').then(r => r.json()),
    fetch('/api/leads/' + _cpClientId + '/calls').then(r => r.json()),
  ]);
  _cpData.lead    = leadRes.status === 'fulfilled' ? leadRes.value : {};
  _cpData.meetings = meetRes.status === 'fulfilled' && Array.isArray(meetRes.value) ? meetRes.value : [];
  _cpData.budget  = budgetRes.status === 'fulfilled' ? budgetRes.value : null;
  _cpData.demo    = demoRes.status === 'fulfilled' ? demoRes.value : null;
  _cpData.attBudget = attBudgetRes.status === 'fulfilled' && Array.isArray(attBudgetRes.value) ? attBudgetRes.value : [];
  _cpData.attDemo   = attDemoRes.status === 'fulfilled' && Array.isArray(attDemoRes.value) ? attDemoRes.value : [];
  _cpData.events    = eventsRes.status === 'fulfilled' && Array.isArray(eventsRes.value) ? eventsRes.value : [];
  _cpData.calls     = callsRes.status === 'fulfilled' && Array.isArray(callsRes.value) ? callsRes.value : [];

  if (_cpData.lead && _cpData.lead.phone) {
    try {
      const wr = await fetch('/api/wa/lead-by-phone/' + encodeURIComponent(_cpData.lead.phone));
      const wj = await wr.json();
      _cpData.waMessages = wj.messages || [];
    } catch { _cpData.waMessages = []; }
  } else {
    _cpData.waMessages = [];
  }

  _cpRenderHeader();
  _cpSwitchTab(_cpTab);
}

function _cpRenderHeader() {
  const l = _cpData.lead || {};
  document.getElementById('cp-title').textContent = l.name || 'Cliente';
  document.getElementById('cp-phone').textContent = l.phone || '';
  const sel = document.getElementById('cp-status-sel');
  sel.value = l.crm_status || 'sin_contactar';
}

function _cpSwitchTab(tab) {
  _cpTab = tab;
  document.querySelectorAll('.cp-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  const body = document.getElementById('cp-body');
  if (tab === 'info')      { body.innerHTML = _cpRenderInfo(); _cpLoadWaTemplates(); }
  else if (tab === 'conv') body.innerHTML = _cpRenderConv();
  else if (tab === 'meet') { body.innerHTML = _cpRenderMeetings(); _cpBindMeetings(); }
  else if (tab === 'budget') { body.innerHTML = _cpRenderBudget(); _cpBindBudget(); }
  else if (tab === 'demo') body.innerHTML = _cpRenderDemo();
  else if (tab === 'ctasks') { body.innerHTML = _cpRenderTasks(); _cpBindTasks(); }
  else if (tab === 'calls')  body.innerHTML = _cpRenderCalls();
}

async function _cpLoadWaTemplates() {
  const sel = document.getElementById('cp-wa-tmpl-sel');
  if (!sel) return;
  try {
    const r = await fetch('/api/wa/templates');
    const templates = await r.json();
    sel.innerHTML = '<option value="">Elegir plantilla...</option>' +
      templates.map(t => `<option value="${esc(t.body)}">${esc(t.name)}</option>`).join('');
  } catch {}
}

function _cpUseWaTemplate() {
  const sel = document.getElementById('cp-wa-tmpl-sel');
  const phone = (_cpData.lead || {}).phone || '';
  if (!sel || !sel.value || !phone) return;
  const digits = phone.replace(/\\D/g, '');
  window.open(`https://wa.me/${digits}?text=${encodeURIComponent(sel.value)}`, '_blank');
}

function _cpRenderInfo() {
  const l = _cpData.lead || {};
  const ci = l.client_info || {};
  const stars = l.rating ? '⭐ ' + l.rating + (l.review_count ? ' (' + l.review_count + ' reseñas)' : '') : '';
  const hasBotData = ci.lead_name || ci.budget_range || ci.colors || ci.instagram || ci.needs;

  const unmatchedBanner = l.source === 'calendly_unmatched' ? `
    <div id="unmatched-banner" style="background:rgba(251,146,60,.1);border:1px solid rgba(251,146,60,.35);border-radius:10px;padding:14px 16px;margin-bottom:18px">
      <div style="font-size:.78rem;font-weight:700;color:#fb923c;margin-bottom:6px">⚠️ Agendó por Calendly — sin match automático</div>
      <div style="font-size:.75rem;color:#94a3b8;margin-bottom:12px">Este cliente puede ya estar en el CRM. Buscalo abajo para fusionar, o confirmá que es nuevo.</div>
      <input id="banner-merge-search" type="text" placeholder="Buscar cliente existente..."
        style="width:100%;background:#0a0f1a;border:1px solid #1e293b;border-radius:6px;padding:8px 10px;font-size:.8rem;color:#e2e8f0;font-family:inherit;outline:none;margin-bottom:8px"
        oninput="_mergeSearch(this.value, 'banner-merge-results')">
      <div id="banner-merge-results" style="margin-bottom:10px"></div>
      <button onclick="_confirmNewLead()" style="background:#1e293b;border:1px solid #334155;color:#94a3b8;font-size:.75rem;font-weight:600;padding:6px 12px;border-radius:6px;cursor:pointer;font-family:inherit">
        ✓ Es un cliente nuevo
      </button>
    </div>` : '';

  return unmatchedBanner + `<div class="cp-section">
    <div class="cp-section-title">Información del negocio</div>
    ${l.phone ? `<div class="cp-field"><span class="cp-field-label">Teléfono</span><span class="cp-field-val">${l.phone}</span></div>` : ''}
    ${l.city ? `<div class="cp-field"><span class="cp-field-label">Ciudad</span><span class="cp-field-val">${l.city}</span></div>` : ''}
    ${l.category ? `<div class="cp-field"><span class="cp-field-label">Rubro</span><span class="cp-field-val">${l.category}</span></div>` : ''}
    ${l.address ? `<div class="cp-field"><span class="cp-field-label">Dirección</span><span class="cp-field-val">${l.address}</span></div>` : ''}
    ${l.maps_url ? `<div class="cp-field"><span class="cp-field-label">Google Maps</span><span class="cp-field-val"><a href="${l.maps_url}" target="_blank" style="color:#3b82f6">Ver en Maps →</a></span></div>` : ''}
    ${stars ? `<div class="cp-field"><span class="cp-field-label">Rating</span><span class="cp-field-val">${stars}</span></div>` : ''}
    ${l.instagram_url ? `<div class="cp-field"><span class="cp-field-label">Instagram</span><span class="cp-field-val"><a href="${esc(l.instagram_url)}" target="_blank" style="display:inline-flex;align-items:center;gap:7px;color:#e2e8f0;text-decoration:none"><span style="display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:6px;background:linear-gradient(135deg,#f09433,#dc2743,#bc1888);color:#fff;font-size:.6rem;font-weight:800;flex-shrink:0">IG</span>Ver perfil →</a></span></div>` : ''}
    ${l.facebook_url ? `<div class="cp-field"><span class="cp-field-label">Facebook</span><span class="cp-field-val"><a href="${esc(l.facebook_url)}" target="_blank" style="display:inline-flex;align-items:center;gap:7px;color:#e2e8f0;text-decoration:none"><span style="display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:6px;background:#1877f2;color:#fff;font-size:.6rem;font-weight:800;flex-shrink:0">FB</span>Ver perfil →</a></span></div>` : ''}
  </div>
  ${hasBotData ? `<div class="cp-section">
    <div class="cp-section-title">Datos del bot <span style="font-size:.7rem;color:#475569;font-weight:400">(calificación WA)</span></div>
    ${ci.lead_name ? `<div class="cp-field"><span class="cp-field-label">Contacto</span><span class="cp-field-val">${ci.lead_name}</span></div>` : ''}
    ${ci.budget_range ? `<div class="cp-field"><span class="cp-field-label">Presupuesto</span><span class="cp-field-val">${ci.budget_range}</span></div>` : ''}
    ${ci.colors ? `<div class="cp-field"><span class="cp-field-label">Colores de marca</span><span class="cp-field-val">${ci.colors}</span></div>` : ''}
    ${ci.instagram ? `<div class="cp-field"><span class="cp-field-label">Instagram / web</span><span class="cp-field-val">${ci.instagram}</span></div>` : ''}
    ${ci.needs ? `<div class="cp-field"><span class="cp-field-label">Necesidades</span><span class="cp-field-val">${ci.needs}</span></div>` : ''}
  </div>` : ''}
  ${l.phone ? `<div class="cp-section">
    <div class="cp-section-title">Enviar por WhatsApp</div>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <select id="cp-wa-tmpl-sel" style="background:#111827;border:1px solid #1e293b;color:#94a3b8;padding:5px 10px;border-radius:6px;font-size:.78rem;flex:1">
        <option value="">Elegir plantilla...</option>
      </select>
      <button onclick="_cpUseWaTemplate()" style="background:#0088cc;border:none;color:#fff;padding:5px 14px;border-radius:6px;font-size:.78rem;cursor:pointer;white-space:nowrap">Abrir WA →</button>
    </div>
  </div>` : ''}
  ${l.pitch_text ? `<div class="cp-section">
    <div class="cp-section-title" style="display:flex;justify-content:space-between;align-items:center">
      <span>Pitch WhatsApp</span>
      <button class="cp-btn cp-btn-ghost" style="padding:3px 10px;font-size:.72rem" onclick="navigator.clipboard.writeText(_cpData.lead.pitch_text||'');this.textContent='✓ Copiado';setTimeout(()=>this.textContent='Copiar',1500)">Copiar</button>
    </div>
    <div style="font-size:.82rem;color:#94a3b8;white-space:pre-wrap;line-height:1.5;background:#0a0f1a;border:1px solid #1e293b;border-radius:8px;padding:12px">${esc(l.pitch_text||'')}</div>
  </div>` : ''}
  <div class="cp-section">
    <div class="cp-section-title">Notas internas</div>
    <textarea class="cp-req-area" id="cp-notes-area" placeholder="Agregar notas sobre este lead...">${l.notes || ''}</textarea>
    <button class="cp-btn cp-btn-ghost" onclick="_cpSaveNotes()">Guardar notas</button>
  </div>
  <div class="cp-section">
    <div class="cp-section-title" style="display:flex;align-items:center;justify-content:space-between">
      <span>Fusionar con otro cliente</span>
      <button onclick="_toggleMergeSection(this)" style="background:none;border:none;color:#475569;font-size:.72rem;cursor:pointer;font-family:inherit">Mostrar</button>
    </div>
    <div id="merge-section" style="display:none;margin-top:8px">
      <input id="merge-search" type="text" placeholder="Buscar cliente existente..."
        style="width:100%;background:#0a0f1a;border:1px solid #1e293b;border-radius:6px;padding:8px 10px;font-size:.8rem;color:#e2e8f0;font-family:inherit;outline:none;margin-bottom:8px"
        oninput="_mergeSearch(this.value)">
      <div id="merge-results"></div>
      <div style="font-size:.72rem;color:#475569;margin-top:6px">⚠️ Esto transfiere las reuniones, adjuntos y eventos al cliente destino y elimina este registro.</div>
    </div>
  </div>
  ${_cpRenderHistory()}`;
}

function _toggleMergeSection(btn) {
  const sec = document.getElementById('merge-section');
  if (!sec) return;
  const open = sec.style.display !== 'none';
  sec.style.display = open ? 'none' : '';
  btn.textContent = open ? 'Mostrar' : 'Ocultar';
  if (!open) sec.querySelector('input').focus();
}

let _mergeSearchTimeout = null;

async function _mergeSearch(query, resultsId) {
  clearTimeout(_mergeSearchTimeout);
  const res = document.getElementById(resultsId || 'merge-results');
  if (!res) return;
  if (!query || query.length < 2) { res.innerHTML = ''; return; }
  _mergeSearchTimeout = setTimeout(async () => {
    try {
      const r = await fetch('/api/leads?search=' + encodeURIComponent(query));
      const data = await r.json();
      const leads = Array.isArray(data) ? data : (data.items || []);
      const filtered = leads.filter(l => l.id !== _cpClientId).slice(0, 5);
      if (!filtered.length) {
        res.innerHTML = '<div style="font-size:.75rem;color:#475569;padding:4px 0">Sin resultados</div>';
        return;
      }
      res.innerHTML = filtered.map(l => `
        <div style="display:flex;align-items:center;justify-content:space-between;padding:6px 8px;border-radius:6px;background:#111827;margin-bottom:4px">
          <div>
            <div style="font-size:.8rem;font-weight:600;color:#e2e8f0">${esc(l.name||'')}</div>
            <div style="font-size:.7rem;color:#475569">${esc(l.phone||'')} · ${esc(l.crm_status||'')}</div>
          </div>
          <button data-tid="${l.id}" data-tname="${esc(l.name||'')}" onclick="_mergeLead(+this.dataset.tid, this.dataset.tname)"
            style="background:#0088cc22;border:1px solid #0088cc55;color:#60a5fa;font-size:.72rem;font-weight:600;padding:4px 10px;border-radius:6px;cursor:pointer;font-family:inherit;flex-shrink:0">
            Fusionar →
          </button>
        </div>`).join('');
    } catch { res.innerHTML = ''; }
  }, 300);
}

async function _mergeLead(targetId, targetName) {
  if (!_cpClientId) return;
  if (!confirm(`¿Fusionar con "${targetName}"? Los datos del cliente actual se transferirán y éste se eliminará.`)) return;
  try {
    const r = await fetch('/api/leads/' + _cpClientId + '/merge', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({into_id: targetId}),
    });
    const d = await r.json();
    if (d.ok) {
      closeClientPanel();
      await loadLeads();
      openClientPanel(targetId);
    } else {
      alert('Error: ' + (d.error || 'desconocido'));
    }
  } catch (e) {
    alert('Error de red: ' + e.message);
  }
}

async function _confirmNewLead() {
  if (!_cpClientId) return;
  try {
    const r = await fetch('/api/leads/' + _cpClientId + '/confirm-new', {method: 'POST'});
    const d = await r.json();
    if (d.ok) {
      _cpData.lead.source = 'calendly';
      const banner = document.getElementById('unmatched-banner');
      if (banner) banner.remove();
      await loadLeads();
    }
  } catch {}
}

function _cpRenderCalls() {
  const calls = _cpData.calls || [];
  const outcomeLabel = {'contestó':'Contestó','no_contestó':'No contestó','buzón':'Buzón'};
  const outcomeColor = {'contestó':'#4ade80','no_contestó':'#f87171','buzón':'#fbbf24'};
  const history = calls.length ? `<div class="cp-section">
    <div class="cp-section-title">Historial de llamadas</div>
    ${calls.map(c => `<div style="display:flex;gap:10px;align-items:flex-start;padding:7px 0;border-bottom:1px solid #1a2234">
      <div style="width:8px;height:8px;border-radius:50%;background:${outcomeColor[c.outcome]||'#475569'};margin-top:5px;flex-shrink:0"></div>
      <div style="flex:1">
        <span style="font-size:.8rem;color:#e2e8f0;font-weight:600">${outcomeLabel[c.outcome]||c.outcome}</span>
        <span style="font-size:.72rem;color:#475569;margin-left:8px">${timeAgo(c.called_at)}${c.created_by && c.created_by !== 'sistema' ? ' · por ' + esc(c.created_by) : ''}</span>
        ${c.notes ? `<div style="font-size:.72rem;color:#64748b;margin-top:2px">${esc(c.notes)}</div>` : ''}
      </div>
    </div>`).join('')}
  </div>` : '<div style="padding:12px 0;font-size:.82rem;color:#475569">Sin llamadas registradas</div>';
  return `<div class="cp-section">
    <div class="cp-section-title">Registrar llamada</div>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <select id="call-outcome" style="background:#111827;border:1px solid #1e293b;color:#e2e8f0;padding:6px 10px;border-radius:6px;font-size:.8rem">
        <option value="">Resultado...</option>
        <option value="contestó">Contestó</option>
        <option value="no_contestó">No contestó</option>
        <option value="buzón">Buzón</option>
      </select>
      <input id="call-notes" placeholder="Notas (opcional)" style="flex:1;min-width:120px;background:#111827;border:1px solid #1e293b;color:#e2e8f0;padding:6px 10px;border-radius:6px;font-size:.8rem">
      <button onclick="_cpLogCall()" style="background:#0088cc;border:none;color:#fff;padding:6px 14px;border-radius:6px;font-size:.8rem;cursor:pointer">Registrar</button>
    </div>
  </div>
  ${history}`;
}

async function _cpLogCall() {
  const outcome = (document.getElementById('call-outcome') || {}).value || '';
  const notes = (document.getElementById('call-notes') || {}).value || '';
  if (!outcome) { alert('Elegí un resultado'); return; }
  await fetch(`/api/leads/${_cpClientId}/calls`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({outcome, notes})});
  const r = await fetch(`/api/leads/${_cpClientId}/calls`);
  _cpData.calls = await r.json();
  _cpSwitchTab('calls');
}

function _cpRenderHistory() {
  const events = _cpData.events || [];
  if (!events.length) return '';
  const crmLabels = {sin_contactar:'Sin contactar',contactado:'Contactado',reunion_agendada:'Reunión agendada',reunion_hecha:'Reunión hecha',presupuesto_enviado:'Presupuesto enviado',negociacion:'Negociación',cliente_cerrado:'Cliente cerrado',en_desarrollo:'En desarrollo',finalizado:'Finalizado',llamar_despues:'Llamar después',no_interesa:'No le interesa',agendo:'Agendó',firmo:'Firmó',nota_actualizada:'Nota actualizada',adjunto_agregado:'Adjunto agregado'};
  const crmDot = {sin_contactar:'#475569',contactado:'#60a5fa',reunion_agendada:'#3b82f6',reunion_hecha:'#14b8a6',presupuesto_enviado:'#f97316',negociacion:'#fbbf24',cliente_cerrado:'#10b981',en_desarrollo:'#0088cc',finalizado:'#6ee7b7',llamar_despues:'#f59e0b',no_interesa:'#ef4444',nota_actualizada:'#64748b',adjunto_agregado:'#64748b'};
  const items = events.map(e => {
    const label = crmLabels[e.new_status] || e.new_status;
    const dot = crmDot[e.new_status] || '#0088cc';
    const when = timeAgo(e.created_at);
    const by = (e.created_by && e.created_by !== 'sistema') ? ` · por ${esc(e.created_by)}` : '';
    const rawNote = e.note || '';
    const cleanNote = rawNote.replace(/^Callback:\s*/i,'').replace('T',' ').replace(/:\d{2}$/,'');
    const note = rawNote ? `<div class="cp-event-note">${esc(cleanNote)}</div>` : '';
    return `<div class="cp-event-row">
      <div class="cp-event-dot" style="background:${dot}"></div>
      <div class="cp-event-body">
        <span class="cp-event-label">${label}</span>
        <span class="cp-event-meta">${when}${by}</span>
        ${note}
      </div>
    </div>`;
  }).join('');
  return `<div class="cp-section">
    <div class="cp-section-title">Historial de estados</div>
    ${items}
  </div>`;
}

async function _cpSaveNotes() {
  const notes = document.getElementById('cp-notes-area').value;
  await fetch('/api/leads/' + _cpClientId + '/notes', {
    method: 'PUT', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({notes})
  });
  _cpData.lead = {..._cpData.lead, notes};
}

function _cpRenderConv() {
  const msgs = _cpData.waMessages || [];
  if (!msgs.length) return `<div style="color:#475569;font-size:.85rem;padding:20px 0">Sin conversación registrada en el bot de WhatsApp.</div>`;
  return `<div class="cp-wa-msgs">` + msgs.map(m => {
    const dir = m.direction === 'outbound' ? 'out' : 'in';
    return `<div class="cp-wa-msg ${dir}">${m.content || m.body || ''}</div>`;
  }).join('') + `</div>`;
}

function _cpRenderMeetings() {
  const meets = _cpData.meetings || [];
  let html = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
    <div class="cp-section-title" style="margin:0">Reuniones</div>
    <div style="display:flex;gap:8px">
      <a href="https://calendly.com/scalerics/consultoriagratuita" target="_blank" class="cp-btn cp-btn-ghost" style="color:#10b981;border-color:#10b981;text-decoration:none;font-size:.75rem" title="Abrir Calendly">Calendly</a>
      <button class="cp-btn cp-btn-ghost" onclick="_cpOpenNewMeeting()">+ Nueva reunión</button>
    </div>
  </div>`;
  if (!meets.length) html += `<div style="color:#475569;font-size:.85rem">No hay reuniones registradas.</div>`;
  meets.forEach(m => {
    const hasSummary = m.summary || m.requirements;
    html += `<div class="cp-meeting-card" id="meet-card-${m.id}">
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <div class="cp-meeting-title">${m.title || 'Reunión'}</div>
        <button class="cp-btn cp-btn-ghost" style="color:#ef4444;font-size:.8rem;padding:2px 8px" onclick="_cpDeleteMeeting(${m.id})">Borrar</button>
      </div>
      <div class="cp-meeting-meta">${m.start_at ? new Date(m.start_at).toLocaleString('es-UY',{timeZone:'America/Montevideo',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'}) : ''} · ${_cpMeetStatus(m.status)}</div>
      ${m.meet_link ? `<div style="margin-bottom:8px"><a class="cp-meeting-link" href="${m.meet_link}" target="_blank" style="margin:0">▶ Unirse a la reunión</a></div>` : ''}
      ${m.calendar_event_id ? `<div style="margin-bottom:8px">
        <button class="cp-btn cp-btn-ghost" style="font-size:.75rem;padding:2px 8px" onclick="_cpToggleAddEmail(${m.id})">+ Agregar email</button>
        <div id="add-email-form-${m.id}" style="display:none;margin-top:6px;gap:6px;align-items:center;flex-wrap:wrap">
          <input type="email" id="add-email-input-${m.id}" placeholder="email@ejemplo.com" style="font-size:.8rem;padding:4px 8px;background:#0f172a;border:1px solid #1e293b;border-radius:6px;color:#f1f5f9;width:220px">
          <button class="cp-btn cp-btn-primary" style="font-size:.75rem;padding:4px 10px;margin-top:4px" onclick="_cpAddEmailToMeeting(${m.id},'${m.calendar_event_id}')">Agregar</button>
        </div>
      </div>` : ''}
      ${hasSummary ? `
        <div class="cp-summary-label">Resumen</div>
        <div class="cp-summary-box">${m.summary || ''}</div>
        ${m.requirements ? `<div class="cp-summary-label" style="margin-top:10px">Requerimientos</div>
        <div class="cp-summary-box">${m.requirements}</div>` : ''}
        <button class="cp-btn cp-btn-primary" style="margin-top:10px" onclick="_cpGenerateBudgetFromMeeting(${m.id})">⚡ Generar presupuesto</button>
      ` : ''}
      <div style="margin-top:10px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
          <div class="cp-summary-label" style="margin:0">Transcripción de la reunión</div>
          <button class="cp-btn cp-btn-ghost" style="font-size:.75rem;padding:2px 8px" onclick="_cpFetchTranscript(${m.id})" id="fetch-tr-btn-${m.id}">
            <span id="fetch-tr-spin-${m.id}" style="display:none" class="cp-spinner"></span>
            ⬇ Obtener transcripción
          </button>
        </div>
        <textarea class="cp-transcript-area" id="transcript-${m.id}" placeholder="Pegá la transcripción de Google Meet acá, o usá ⬇ Obtener de Drive...">${m.transcript || ''}</textarea>
        <button class="cp-btn cp-btn-primary" onclick="_cpSummarize(${m.id})">
          <span id="sum-spin-${m.id}" style="display:none" class="cp-spinner"></span>
          Resumir con IA
        </button>
      </div>
    </div>`;
  });
  return html;
}

function _cpMeetStatus(s) {
  const map = {scheduled:'agendada',completed:'realizada',cancelled:'cancelada'};
  return map[s] || s || '';
}

function _cpBindMeetings() {}

async function _cpDeleteMeeting(meetingId) {
  if (!confirm('¿Borrar esta reunión? También se cancela el evento en Google Calendar.')) return;
  const r = await fetch('/api/calendar/meetings/' + meetingId, { method: 'DELETE' });
  const data = await r.json();
  if (data.ok) {
    _cpData.meetings = (_cpData.meetings || []).filter(m => m.id !== meetingId);
    document.getElementById('cp-tab-meetings').innerHTML = _cpRenderMeetings();
  } else {
    alert('Error al borrar: ' + (data.error || 'desconocido'));
  }
}

function _cpToggleAddEmail(meetId) {
  const form = document.getElementById('add-email-form-' + meetId);
  if (!form) return;
  const visible = form.style.display === 'flex';
  form.style.display = visible ? 'none' : 'flex';
}

async function _cpAddEmailToMeeting(meetId, calEventId) {
  const input = document.getElementById('add-email-input-' + meetId);
  const email = (input ? input.value : '').trim();
  if (!email) { alert('Ingresá un email'); return; }
  const r = await fetch('/api/calendar/events/' + encodeURIComponent(calEventId) + '/attendees', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({email})
  });
  const d = await r.json();
  if (d.ok) {
    if (input) input.value = '';
    document.getElementById('add-email-form-' + meetId).style.display = 'none';
    alert('Email agregado al evento.');
  } else {
    alert('Error: ' + (d.error || 'desconocido'));
  }
}

async function _cpFetchTranscript(meetingId) {
  const btn = document.getElementById('fetch-tr-btn-' + meetingId);
  const spin = document.getElementById('fetch-tr-spin-' + meetingId);
  if (spin) spin.style.display = 'inline-block';
  if (btn) btn.disabled = true;
  try {
    // Try Recall first, fall back to Drive
    let r = await fetch('/api/calendar/meetings/' + meetingId + '/recall-transcript');
    let d = await r.json();
    if (!d.ok) {
      r = await fetch('/api/calendar/meetings/' + meetingId + '/fetch-transcript');
      d = await r.json();
    }
    if (d.ok) {
      const ta = document.getElementById('transcript-' + meetingId);
      if (ta) ta.value = d.transcript;
    } else {
      alert('No se pudo obtener la transcripción: ' + (d.error || 'error desconocido'));
    }
  } finally {
    if (spin) spin.style.display = 'none';
    if (btn) btn.disabled = false;
  }
}

async function _cpSummarize(meetingId) {
  const textarea = document.getElementById('transcript-' + meetingId);
  const transcript = (textarea ? textarea.value : '').trim();
  if (!transcript) { alert('Pegá la transcripción primero.'); return; }
  const spin = document.getElementById('sum-spin-' + meetingId);
  if (spin) spin.style.display = '';
  try {
    const r = await fetch('/api/calendar/meetings/' + meetingId + '/summarize', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({transcript})
    });
    const d = await r.json();
    if (!d.ok) { alert('Error: ' + d.error); return; }
    const meet = _cpData.meetings.find(m => m.id === meetingId);
    if (meet) {
      meet.transcript = transcript;
      meet.summary = d.summary.summary || '';
      meet.requirements = d.summary.requirements || '';
    }
    if (d.budget_generated) {
      const budgetRes = await fetch('/api/leads/' + _cpClientId + '/budget');
      _cpData.budget = await budgetRes.json();
      const budgetTab = document.querySelector('[data-tab="budget"]');
      if (budgetTab && !budgetTab.querySelector('.budget-new-badge')) {
        budgetTab.insertAdjacentHTML('beforeend', '<span class="budget-new-badge" style="background:#4ade80;color:#000;font-size:.65rem;padding:1px 5px;border-radius:4px;margin-left:4px">Nuevo</span>');
      }
    }
    _cpSwitchTab('meet');
  } catch(e) { alert('Error: ' + e); }
  finally { if (spin) spin.style.display = 'none'; }
}

function _cpOpenNewMeeting() {
  showPanel('cal');
  closeClientPanel();
  openNewEventModal();
  if (_cpClientId) {
    document.getElementById('ev-client-id').value = _cpClientId;
    const clientName = _cpData.info && _cpData.info.name ? _cpData.info.name : '';
    if (clientName) document.getElementById('ev-title').value = 'Reunión con ' + clientName;
  }
}

function _cpGenerateBudgetFromMeeting(meetingId) {
  const meet = _cpData.meetings.find(m => m.id === meetingId);
  _cpSwitchTab('budget');
  if (meet && meet.requirements) {
    document.getElementById('cp-extra-req') && (document.getElementById('cp-extra-req').value = meet.requirements);
  }
}

function _cpRenderAttachBox(section) {
  const items = section === 'budget' ? (_cpData.attBudget||[]) : (_cpData.attDemo||[]);
  const listHtml = items.length ? `<div class="attach-list">` + items.map(a => {
    const href = a.has_file ? `/api/attachments/${a.id}/file` : (a.url||'#');
    return `<div class="attach-item">
      <span class="attach-item-name"><a href="${href}" target="_blank">${esc(a.name)}</a></span>
      <button class="attach-del" title="Eliminar" onclick="_cpDeleteAttach(${a.id},'${section}')">✕</button>
    </div>`;
  }).join('') + `</div>` : '';
  return `<div class="cp-section">
    <div class="cp-section-title">Archivos y links</div>
    <div class="attach-drop" id="attach-drop-${section}"
         onclick="document.getElementById('attach-file-${section}').click()"
         ondragover="event.preventDefault();this.classList.add('dragover')"
         ondragleave="this.classList.remove('dragover')"
         ondrop="_cpDropFile(event,'${section}')">
      Arrastrá un archivo acá o hacé click para subir
    </div>
    <input type="file" id="attach-file-${section}" style="display:none" onchange="_cpUploadFile(this,'${section}')">
    <div style="display:flex;gap:6px;margin-bottom:4px">
      <input type="text" id="attach-link-${section}" placeholder="Pegar link (GitHub, Google Drive, etc.)"
             style="flex:1;background:#0a0f1a;border:1px solid #1e293b;border-radius:6px;color:#e2e8f0;font-size:.8rem;padding:6px 10px">
      <button class="cp-btn cp-btn-ghost" style="white-space:nowrap" onclick="_cpSaveLink('${section}')">Guardar link</button>
    </div>
    ${listHtml}
  </div>`;
}

async function _cpUploadFile(input, section) {
  const file = input.files[0]; if (!file) return;
  if (file.size > 10*1024*1024) { alert('Archivo demasiado grande (máx 10 MB)'); input.value=''; return; }
  const fd = new FormData(); fd.append('file', file);
  const r = await fetch(`/api/leads/${_cpClientId}/attachments?section=${section}`, {method:'POST', body:fd});
  const d = await r.json();
  if (d.ok) { await _cpReloadAttach(section); _cpSwitchTab(_cpTab); } else alert(d.error||'Error');
  input.value = '';
}

async function _cpDropFile(e, section) {
  e.preventDefault();
  document.getElementById('attach-drop-'+section).classList.remove('dragover');
  const file = e.dataTransfer.files[0]; if (!file) return;
  if (file.size > 10*1024*1024) { alert('Archivo demasiado grande (máx 10 MB)'); return; }
  const fd = new FormData(); fd.append('file', file);
  const r = await fetch(`/api/leads/${_cpClientId}/attachments?section=${section}`, {method:'POST', body:fd});
  const d = await r.json();
  if (d.ok) { await _cpReloadAttach(section); _cpSwitchTab(_cpTab); } else alert(d.error||'Error');
}

async function _cpSaveLink(section) {
  const inp = document.getElementById('attach-link-'+section);
  const url = (inp.value||'').trim(); if (!url) return;
  const name = url.replace(/^https?:\/\//,'').split('/')[0];
  const r = await fetch(`/api/leads/${_cpClientId}/attachments?section=${section}`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url, name})
  });
  if ((await r.json()).ok) { inp.value=''; await _cpReloadAttach(section); _cpSwitchTab(_cpTab); }
}

async function _cpDeleteAttach(id, section) {
  await fetch(`/api/attachments/${id}`, {method:'DELETE'});
  await _cpReloadAttach(section);
  _cpSwitchTab(_cpTab);
}

async function _cpReloadAttach(section) {
  const r = await fetch(`/api/leads/${_cpClientId}/attachments?section=${section}`);
  const data = await r.json();
  if (section === 'budget') _cpData.attBudget = Array.isArray(data) ? data : [];
  else _cpData.attDemo = Array.isArray(data) ? data : [];
}

function _cpRenderBudget() {
  const items = (_cpData.attBudget || []).filter(a => a.mime_type === 'text/html');
  const hasBudget = items.length > 0;

  if (!hasBudget) {
    return `<div class="cp-section">
      <div class="cp-section-title">Presupuesto</div>
      <div style="color:#475569;font-size:.85rem;margin-bottom:14px">No hay presupuesto para este cliente.</div>
      <button class="cp-btn cp-btn-primary" onclick="_cpOpenGenBudgetModal()">
        ⚡ Generar con IA
      </button>
    </div>
    ${_cpRenderAttachBox('budget')}`;
  }

  const listHtml = items.map(a => `
    <div class="attach-item" style="display:flex;align-items:center;justify-content:space-between;padding:8px 10px;background:#0a0f1a;border-radius:6px;margin-bottom:6px">
      <a href="/api/attachments/${a.id}/file" target="_blank" style="color:#33aadd;font-size:.85rem;text-decoration:none">📄 ${esc(a.name)}</a>
      <div style="display:flex;gap:6px">
        <a class="cp-btn cp-btn-ghost" style="font-size:.75rem;padding:3px 10px;text-decoration:none" href="/api/attachments/${a.id}/pdf">⬇️ PDF</a>
        <button class="cp-btn cp-btn-ghost" style="font-size:.75rem;padding:3px 10px" onclick="_cpOpenAiEditModal(${a.id},'${esc(a.name)}')">✏️ Editar con IA</button>
        <button class="attach-del" title="Eliminar" onclick="_cpDeleteAttach(${a.id},'budget')">✕</button>
      </div>
    </div>`).join('');

  return `<div class="cp-section">
    <div class="cp-section-title">Presupuesto</div>
    ${listHtml}
  </div>
  ${_cpRenderAttachBox('budget')}`;
}

async function _cpSaveBudget() {
  // No-op: budget editing is done via the full preview/PDF page
}

async function _cpMarkBudgetSent() {
  if (!_cpData.budget) return;
  await fetch('/api/budgets/' + _cpData.budget.id + '/mark-sent', {method:'POST'});
  _cpData.budget.status = 'sent';
  _cpSwitchTab('budget');
}

async function _cpRegeneraBudget() {
  const req = document.getElementById('cp-extra-req');
  const requirements = req ? req.value.trim() : '';
  const spin = document.getElementById('budget-spin');
  const btn = document.getElementById('cp-gen-btn');
  if (spin) spin.style.display = '';
  if (btn) btn.disabled = true;
  try {
    const r = await fetch('/api/leads/' + _cpClientId + '/budget/generate', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({requirements})
    });
    const d = await r.json();
    if (!d.ok) { alert('Error: ' + d.error); return; }
    const budgetRes = await fetch('/api/leads/' + _cpClientId + '/budget');
    _cpData.budget = await budgetRes.json();
    _cpSwitchTab('budget');
  } catch(e) { alert('Error: ' + e); }
  finally { if (spin) spin.style.display = 'none'; if (btn) btn.disabled = false; }
}

function _cpBindBudget() {}

function _cpOpenAiEditModal(attachId, attachName) {
  const existing = document.getElementById('ai-edit-modal');
  if (existing) existing.remove();
  const modal = document.createElement('div');
  modal.id = 'ai-edit-modal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:9999;display:flex;align-items:center;justify-content:center';
  modal.innerHTML = `
    <div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:24px;width:min(680px,95vw);max-height:90vh;overflow:auto">
      <div style="font-family:Sora,sans-serif;font-size:1rem;font-weight:700;color:#e2e8f0;margin-bottom:16px">✏️ Editar con IA — ${esc(attachName)}</div>
      <textarea id="ai-edit-instr" placeholder="Ej: cambia el precio a $500 USD, agrega mantenimiento mensual de $30..."
        style="width:100%;height:80px;background:#0a0f1a;border:1px solid #1e293b;border-radius:6px;color:#e2e8f0;font-size:.85rem;padding:10px;resize:vertical;box-sizing:border-box"></textarea>
      <div style="display:flex;gap:8px;margin-top:10px">
        <button class="cp-btn cp-btn-primary" id="ai-edit-preview-btn" onclick="_cpAiEditPreview(${attachId})">
          <span id="ai-edit-spin" class="cp-spinner" style="display:none"></span>
          Generar preview
        </button>
        <button class="cp-btn cp-btn-ghost" onclick="document.getElementById('ai-edit-modal').remove()">Cancelar</button>
      </div>
      <div id="ai-edit-preview-wrap" style="display:none;margin-top:16px">
        <div style="font-size:.75rem;color:#475569;margin-bottom:6px">Preview:</div>
        <iframe id="ai-edit-iframe" style="width:100%;height:400px;border:1px solid #1e293b;border-radius:6px;background:#fff"></iframe>
        <div style="display:flex;gap:8px;margin-top:10px">
          <button class="cp-btn cp-btn-success" id="ai-edit-save-btn" onclick="_cpAiEditSave(${attachId})">✅ Guardar</button>
          <button class="cp-btn cp-btn-ghost" onclick="document.getElementById('ai-edit-modal').remove()">Cancelar</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(modal);
}

let _aiEditPendingHtml = '';

async function _cpAiEditPreview(attachId) {
  const instr = (document.getElementById('ai-edit-instr').value || '').trim();
  if (!instr) { alert('Escribí las instrucciones primero'); return; }
  const btn = document.getElementById('ai-edit-preview-btn');
  const spin = document.getElementById('ai-edit-spin');
  const textarea = document.getElementById('ai-edit-instr');
  btn.disabled = true; spin.style.display = 'inline-block'; textarea.disabled = true;
  try {
    const r = await fetch(`/api/attachments/${attachId}/ai-edit`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({instructions: instr})
    });
    const d = await r.json();
    if (!d.ok) { alert(d.error || 'Error generando preview'); return; }
    _aiEditPendingHtml = d.html;
    const iframe = document.getElementById('ai-edit-iframe');
    iframe.srcdoc = d.html;
    document.getElementById('ai-edit-preview-wrap').style.display = '';
  } catch(e) { alert('Error: ' + e); }
  finally { btn.disabled = false; spin.style.display = 'none'; textarea.disabled = false; }
}

async function _cpAiEditSave(attachId) {
  if (!_aiEditPendingHtml) return;
  const btn = document.getElementById('ai-edit-save-btn');
  btn.disabled = true;
  try {
    const r = await fetch(`/api/attachments/${attachId}/ai-apply`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({html: _aiEditPendingHtml})
    });
    const d = await r.json();
    if (d.ok) {
      document.getElementById('ai-edit-modal').remove();
      await _cpReloadAttach('budget');
      _cpSwitchTab('budget');
    } else { alert(d.error || 'Error guardando'); }
  } catch(e) { alert('Error: ' + e); }
  finally { btn.disabled = false; }
}

function _cpOpenGenBudgetModal() {
  const existing = document.getElementById('gen-budget-modal');
  if (existing) existing.remove();
  const modal = document.createElement('div');
  modal.id = 'gen-budget-modal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:9999;display:flex;align-items:center;justify-content:center';
  modal.innerHTML = `
    <div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:24px;width:min(480px,95vw)">
      <div style="font-family:Sora,sans-serif;font-size:1rem;font-weight:700;color:#e2e8f0;margin-bottom:16px">⚡ Generar presupuesto con IA</div>
      <textarea id="gen-budget-instr" placeholder="Instrucciones adicionales (opcional). Ej: sitio web para arquitecta, precio $370 USD, mantenimiento $25/mes..."
        style="width:100%;height:80px;background:#0a0f1a;border:1px solid #1e293b;border-radius:6px;color:#e2e8f0;font-size:.85rem;padding:10px;resize:vertical;box-sizing:border-box"></textarea>
      <div style="display:flex;gap:8px;margin-top:12px">
        <button class="cp-btn cp-btn-primary" id="gen-budget-btn" onclick="_cpGenBudget()">
          <span id="gen-budget-spin" class="cp-spinner" style="display:none"></span>
          Generar
        </button>
        <button class="cp-btn cp-btn-ghost" onclick="document.getElementById('gen-budget-modal').remove()">Cancelar</button>
      </div>
    </div>`;
  document.body.appendChild(modal);
}

async function _cpGenBudget() {
  const instr = (document.getElementById('gen-budget-instr').value || '').trim();
  const btn = document.getElementById('gen-budget-btn');
  const spin = document.getElementById('gen-budget-spin');
  btn.disabled = true; spin.style.display = 'inline-block';
  try {
    const r = await fetch(`/api/leads/${_cpClientId}/budget/generate`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({instructions: instr})
    });
    const d = await r.json();
    if (d.ok) {
      document.getElementById('gen-budget-modal').remove();
      await _cpReloadAttach('budget');
      _cpSwitchTab('budget');
    } else { alert(d.error || 'Error generando presupuesto'); }
  } catch(e) { alert('Error: ' + e); }
  finally { btn.disabled = false; spin.style.display = 'none'; }
}

function _cpOpenDemoModal() {
  const l = _cpData.lead || {};
  const b = {id: l.id, name: l.name||'', category: l.category||'', city: l.city||'', phone: l.phone||''};
  closeClientPanel();
  openDemoModalFromCRM(b);
}

function _cpRenderDemo() {
  const d = _cpData.demo;
  const l = _cpData.lead || {};
  const isDone = d && d.status === 'completed' && d.url;
  const isGenerating = d && ['generating', 'pending'].includes(d.status);
  if (isDone) {
    return `<div class="cp-section">
      <div class="cp-section-title">Demo <span class="cp-badge cp-badge-completed">Lista</span></div>
      <div class="cp-field"><span class="cp-field-label">URL de la demo</span>
        <a href="${d.url}" target="_blank" class="cp-meeting-link" style="display:block;margin-top:4px;word-break:break-all">${d.url}</a>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
        <a class="cp-btn cp-btn-primary" href="${d.url}" target="_blank">🔗 Ver demo</a>
        <button class="cp-btn cp-btn-ghost" onclick="navigator.clipboard.writeText('${d.url}').then(()=>alert('Link copiado'))">📋 Copiar link</button>
      </div>
      <div style="margin-top:10px;font-size:.75rem;color:#475569">La demo ya fue generada. Para regenerar contactá al administrador.</div>
    </div>
    ${_cpRenderAttachBox('demo')}`;
  }
  if (isGenerating) {
    return `<div class="cp-section">
      <div class="cp-section-title">Demo <span class="cp-badge cp-badge-generating">Generando...</span></div>
      <div style="display:flex;align-items:center;gap:8px;color:#94a3b8;font-size:.85rem">
        <span class="cp-spinner"></span> Generando demo con IA...
      </div>
      <div style="font-size:.75rem;color:#475569;margin-top:6px">Puede tomar 1-2 minutos. Actualizá la página para ver el estado.</div>
    </div>
    ${_cpRenderAttachBox('demo')}`;
  }
  return `<div class="cp-section">
    <div class="cp-section-title">Demo</div>
    ${d && d.error_message ? `<div style="color:#f87171;font-size:.8rem;margin-bottom:8px;background:#0a0f1a;padding:8px;border-radius:6px">Error anterior: ${d.error_message}</div>` : ''}
    <div style="color:#475569;font-size:.85rem;margin-bottom:12px">Sin demo generada para este cliente.</div>
    <button class="cp-btn cp-btn-primary" onclick="_cpOpenDemoModal()">
      📊 Generar demo
    </button>
  </div>
  ${_cpRenderAttachBox('demo')}`;
}

function _cpChangeStatus(val) {
  fetch('/api/leads/' + _cpClientId + '/crm-status', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({crm_status: val})
  }).then(() => { if (_cpData.lead) _cpData.lead.crm_status = val; });
}