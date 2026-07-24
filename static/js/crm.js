// ========== Leads / Cola panel ==========
let currentCrm = '';
let currentCategory = '';
let currentSearch = '';
let currentPage = 1;
let totalPages = 1;
let contactingId = null;
let pipelinePolling = null;
let pitchMap = {};

// ── call modal state ──────────────────────────────────────────────────────────
let _callLeadId = null;
let _callActivePanel = 'cola';

function openCallModal(id, name, phone, panelName) {
  _callLeadId = id;
  _callActivePanel = panelName || 'cola';
  document.getElementById('call-modal-name').textContent = name;
  document.getElementById('call-modal-phone').textContent = phone || '';
  document.getElementById('call-notes-input').value = '';
  document.getElementById('callback-row').style.display = 'none';
  // Pre-fill datetime to +3h rounded to nearest 30min
  const d = new Date(Date.now() + 3 * 3600 * 1000);
  d.setMinutes(d.getMinutes() < 30 ? 0 : 30, 0, 0);
  const pad = n => String(n).padStart(2,'0');
  const defaultDt = `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const now = new Date(); const nowPad = n => String(n).padStart(2,'0');
  const minDt = `${now.getFullYear()}-${nowPad(now.getMonth()+1)}-${nowPad(now.getDate())}T${nowPad(now.getHours())}:${nowPad(now.getMinutes())}`;
  const inp = document.getElementById('callback-date-input');
  inp.min = minDt;
  inp.value = defaultDt;
  document.getElementById('call-modal').classList.add('open');
}
function closeCallModal() {
  document.getElementById('call-modal').classList.remove('open');
  _callLeadId = null;
}
let _callbackOutcome = 'llamar_despues';
function setCallbackOutcome(outcome) {
  _callbackOutcome = outcome;
  const row = document.getElementById('callback-row');
  row.style.display = 'block';
}
function toggleCallbackRow() {
  const row = document.getElementById('callback-row');
  row.style.display = row.style.display === 'none' ? 'block' : 'none';
}
async function logCallOutcome(outcome) {
  if (!_callLeadId) return;
  const notes = document.getElementById('call-notes-input').value;
  await fetch(`/api/leads/${_callLeadId}/calls`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({outcome, notes})});
  if (outcome === 'no_interesa') {
    await fetch(`/api/leads/${_callLeadId}/crm-status`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({crm_status:'no_interesa'})});
  } else if (outcome === 'reunion') {
    await fetch(`/api/leads/${_callLeadId}/crm-status`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({crm_status:'reunion_agendada'})});
  }
  closeCallModal();
  if (_callActivePanel === 'cola' && (outcome === 'no_contestó' || outcome === 'no_interesa')) {
    if (outcome === 'no_contestó') {
      const lead = _colaLeads.find(b => b.id === _callLeadId);
      if (lead) lead.no_contesto_count = (lead.no_contesto_count || 0) + 1;
    } else {
      _colaLeads = _colaLeads.filter(b => b.id !== _callLeadId);
    }
    const scrollY = window.scrollY;
    renderCola();
    window.scrollTo(0, scrollY);
    loadColaStats();
  } else {
    _reloadActiveCallPanel();
  }
}
async function confirmCallback() {
  if (!_callLeadId) return;
  const date = document.getElementById('callback-date-input').value;
  if (!date) { alert('Elegí una fecha'); return; }
  const notes = document.getElementById('call-notes-input').value;
  await fetch(`/api/leads/${_callLeadId}/callback`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({callback_date:date, notes, outcome:_callbackOutcome})});
  closeCallModal();
  _reloadActiveCallPanel();
}
function _reloadActiveCallPanel() {
  if (_callActivePanel === 'cola') loadCola();
  else if (_callActivePanel === 'seguimientos') loadSeguimientos();
  loadColaStats();
}

// ── Meta Ads panel ───────────────────────────────────────────────────────────
let _metaSearch = '';
let _metaLeads = [];
let _metaSortDesc = true;
let _metaKnownIds = new Set();
let _metaPollTimer = null;

function clearMetaBadge() {
  document.getElementById('meta-badge').style.display = 'none';
}

function _startMetaPoll() {
  if (_metaPollTimer) return;
  _metaPollTimer = setInterval(async () => {
    try {
      const r = await fetch('/api/leads?crm_group=meta');
      const data = await r.json();
      const leads = Array.isArray(data) ? data : (data.items || []);
      const newOnes = leads.filter(l => !_metaKnownIds.has(l.id));
      if (newOnes.length && _metaKnownIds.size > 0) {
        const badge = document.getElementById('meta-badge');
        badge.textContent = newOnes.length === 1 ? 'NEW' : `+${newOnes.length}`;
        badge.style.display = '';
        _metaLeads = leads;
        const activePanel = document.querySelector('.panel.active');
        if (activePanel && activePanel.id === 'meta-panel') {
          renderMetaTable();
          clearMetaBadge();
        }
      }
      leads.forEach(l => _metaKnownIds.add(l.id));
    } catch(e) {}
  }, 60000);
}
function metaSearch(v) { _metaSearch = v.toLowerCase(); renderMetaTable(); }
function toggleMetaSort() { _metaSortDesc = !_metaSortDesc; document.getElementById('meta-sort-icon').textContent = _metaSortDesc ? '↓' : '↑'; renderMetaTable(); }

async function loadMetaPanel() {
  const body = document.getElementById('meta-body');
  body.innerHTML = '<div style="color:#475569;padding:16px;font-size:.85rem">Cargando...</div>';
  try {
    const r = await fetch('/api/leads?crm_group=meta');
    const data = await r.json();
    _metaLeads = Array.isArray(data) ? data : (data.items || []);
    _metaLeads.forEach(l => _metaKnownIds.add(l.id));
    renderMetaTable();
    _startMetaPoll();
  } catch(e) { body.innerHTML = `<div style="color:#f87171;padding:16px">Error: ${e.message}</div>`; }
}

function renderMetaTable() {
  const body = document.getElementById('meta-body');
  let leads = _metaLeads;
  if (_metaSearch) leads = leads.filter(b => (b.name||'').toLowerCase().includes(_metaSearch) || (b.notes||'').toLowerCase().includes(_metaSearch));
  if (!leads.length) { body.innerHTML = '<div class="empty-state">No hay leads de Meta Ads todavía</div>'; return; }
  const crmLabels = {sin_contactar:'Sin contactar',interesado:'Interesado',contactado:'Interesado',reunion_agendada:'Reunión agendada',reunion_hecha:'Reunión hecha',presupuesto_enviado:'Ppto enviado',negociacion:'Negociación',cliente_cerrado:'Cerrado',en_desarrollo:'En desarrollo',finalizado:'Finalizado',llamar_despues:'Llamar después',no_interesa:'No le interesa'};
  const crmColor = {sin_contactar:'#475569',interesado:'#10b981',contactado:'#10b981',reunion_agendada:'#3b82f6',reunion_hecha:'#14b8a6',presupuesto_enviado:'#f97316',negociacion:'#fbbf24',cliente_cerrado:'#10b981',en_desarrollo:'#0088cc',finalizado:'#6ee7b7',llamar_despues:'#f59e0b',no_interesa:'#ef4444'};
  leads.sort((a,b) => {
    const da = new Date(a.scraped_at||0), db2 = new Date(b.scraped_at||0);
    return _metaSortDesc ? db2-da : da-db2;
  });
  const buscarLabels = {
    'una_nueva_p\u00e1gina_web':'Nueva web',
    'una_nueva_pagina_web':'Nueva web',
    'crear_mi_ecommerce':'E-commerce',
    'una_tienda_online':'E-commerce',
    'redise\u00f1ar_mi_p\u00e1gina':'Redise\u00f1o',
    'redisenar_mi_pagina':'Redise\u00f1o',
    'una_app_a_medida':'App/Software',
    'un_software_a_medida':'App/Software',
    'software_a_medida':'App/Software',
    'automatizaciones':'Automatizaciones',
    'otro':'Otro',
  };
  const presupLabels = {
    'menos_de_usd_500':'< USD 500',
    'entre_usd_500_y_usd_1.000':'USD 500-1K',
    'entre_usd_1.000_y_usd_3.000':'USD 1K-3K',
    'm\u00e1s_de_usd_3.000':'> USD 3K',
    'mas_de_usd_3000':'> USD 3K',
    'mas_de_usd_1.000':'> USD 1K',
    'm\u00e1s_de_usd_1.000':'> USD 1K',
    'a\u00fan_no_lo_se':'No sabe',
    'aun_no_lo_se':'No sabe',
  };
  body.innerHTML = leads.map(b => {
    const crm = b.crm_status || 'sin_contactar';
    const color = crmColor[crm] || '#475569';
    let fd = {};
    try { fd = JSON.parse(b.form_data || '{}'); } catch(e) {}
    const negocio = fd['\u00bfc\u00f3mo_se_llama_tu_negocio?'] || fd['como_se_llama_tu_negocio'] || fd['nombre_del_negocio'] || '';
    const buscaRaw = fd['\u00bfque_es_lo_que_busc\u00e1s_para_tu_negocio?'] || fd['que_buscas'] || fd['que_busca'] || '';
    const busca = buscarLabels[buscaRaw] || buscaRaw.replace(/_/g,' ') || '—';
    const presupRaw = fd['\u00bfcont\u00e1s_con_un_presupuesto_para_este_proyecto?'] || fd['presupuesto'] || '';
    const presup = presupLabels[presupRaw] || presupRaw.replace(/_/g,' ') || '—';
    return `
    <div class="table-row no-cb row-${crm}" style="grid-template-columns:1.8fr 1fr 1.2fr 1.2fr 0.9fr 0.8fr 1.1fr">
      <div>
        <div class="biz-name"><span style="cursor:pointer;text-decoration:underline;text-decoration-color:#334155" onclick="openClientPanel(${b.id})">${esc(b.name||'')}</span>
        <span style="font-size:.65rem;background:linear-gradient(135deg,#833ab4,#fd1d1d,#fcb045);color:#fff;padding:1px 6px;border-radius:99px;font-weight:700;margin-left:4px">IG/FB</span></div>
        <div class="biz-sub">${negocio ? esc(negocio) : (esc(b.city||'') || '—')}</div>
      </div>
      <div>${b.phone ? (hasWhatsApp(b.phone) ? `<a class="phone-val" href="https://wa.me/${waNum(b.phone)}${b.pitch_text ? '?text='+encodeURIComponent(b.pitch_text) : ''}" target="_blank" title="Abrir WhatsApp">${esc(b.phone)}</a>` : `<span class="phone-plain">${esc(b.phone)}</span>`) : '<span class="no-val">—</span>'}</div>
      <div style="font-size:.78rem;color:#94a3b8">${esc(busca)}</div>
      <div style="font-size:.78rem;color:#94a3b8">${esc(presup)}</div>
      <div style="font-size:.78rem;color:#64748b">${esc(b.city||'—')}</div>
      <div style="font-size:.72rem;color:#475569">${b.scraped_at ? new Date(b.scraped_at+'Z').toLocaleString('es-UY',{day:'2-digit',month:'2-digit',year:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—'}</div>
      <div class="actions">
        <span style="font-size:.68rem;font-weight:600;color:${color};background:${color}18;padding:2px 6px;border-radius:99px">${crmLabels[crm]||crm}</span>
        <button class="pitch-btn" onclick="openClientPanel(${b.id})">Ver ficha</button>
      </div>
    </div>`;
  }).join('');
  _populateNotes(body);
}

// ── Cola stats ────────────────────────────────────────────────────────────────
async function loadColaStats() {
  try {
    const r = await fetch('/api/stats');
    if (!r.ok) return;
    const d = await r.json();
    const cola = await fetch('/api/leads?crm_status=sin_contactar');
    const colaData = await cola.json();
    const [segR, conR] = await Promise.all([
      fetch('/api/leads?crm_status=llamar_despues'),
      fetch('/api/leads?crm_status=interesado'),
    ]);
    const [segData, conData] = await Promise.all([segR.json(), conR.json()]);
    const segTotal = (Array.isArray(segData) ? segData.length : (segData.total||0)) +
                     (Array.isArray(conData) ? conData.length : (conData.total||0));
    const noInt = await fetch('/api/leads?crm_status=no_interesa');
    const noIntData = await noInt.json();
    document.getElementById('stat-cola').textContent = Array.isArray(colaData) ? colaData.length : (colaData.total || 0);
    document.getElementById('stat-seguimientos').textContent = segTotal;
    document.getElementById('stat-no-interesa').textContent = Array.isArray(noIntData) ? noIntData.length : (noIntData.total || 0);
    const sel = document.getElementById('cola-category-filter');
    const prev = sel.value;
    while (sel.options.length > 1) sel.remove(1);
    (d.categories || []).forEach(c => { sel.add(new Option(c, c)); });
    if (prev) sel.value = prev;
    document.getElementById('cola-date').textContent = 'Actualizado: ' + new Date().toLocaleString('es-UY');
  } catch(e) {}
}

// ── Cola (sin_contactar) ──────────────────────────────────────────────────────
let _colaSearch = '';
let _colaCategory = '';
let _colaLeads = [];
let _colaFilter = 'sin_contactar';
function setColaFilter(f) {
  _colaFilter = f;
  const sinBtn = document.getElementById('cola-filter-sin');
  const noBtn  = document.getElementById('cola-filter-no');
  if (sinBtn) { sinBtn.style.background = f === 'sin_contactar' ? '#0088cc' : 'transparent'; sinBtn.style.borderColor = f === 'sin_contactar' ? '#0088cc' : '#1e293b'; sinBtn.style.color = f === 'sin_contactar' ? '#fff' : '#64748b'; }
  if (noBtn)  { noBtn.style.background  = f === 'no_interesa'   ? '#ef4444' : 'transparent'; noBtn.style.borderColor  = f === 'no_interesa'   ? '#ef4444' : '#1e293b'; noBtn.style.color  = f === 'no_interesa'   ? '#fff' : '#64748b'; }
  loadCola();
}

function colaSearch(v) { _colaSearch = v.toLowerCase(); renderCola(); }

document.addEventListener('DOMContentLoaded', () => {
  const catSel = document.getElementById('cola-category-filter');
  if (catSel) catSel.addEventListener('change', () => { _colaCategory = catSel.value; renderCola(); });
});

async function loadCola() {
  const body = document.getElementById('cola-body');
  body.innerHTML = '<div style="color:#475569;padding:16px;font-size:.85rem">Cargando...</div>';
  try {
    const r = await fetch(`/api/leads?crm_status=${_colaFilter}`);
    const data = await r.json();
    _colaLeads = Array.isArray(data) ? data : (data.items || []);
    renderCola();
    loadColaStats();
  } catch(e) { body.innerHTML = `<div style="color:#f87171;padding:16px">Error: ${e.message}</div>`; }
}

function renderCola() {
  const body = document.getElementById('cola-body');
  let leads = _colaLeads;
  if (_colaCategory) leads = leads.filter(b => (b.category||'') === _colaCategory);
  if (_colaSearch) leads = leads.filter(b => (b.name||'').toLowerCase().includes(_colaSearch));
  pitchMap = {};
  leads.forEach(b => { if (b.pitch_text) pitchMap[b.id] = b.pitch_text; });
  if (!leads.length) { body.innerHTML = '<div class="empty-state">No hay leads en la cola</div>'; return; }
  body.innerHTML = leads.map(b => `
    <div class="table-row no-cb row-${b.crm_status||'sin_contactar'}">
      <div>
        <div class="biz-name"><span style="cursor:pointer;text-decoration:underline;text-decoration-color:#334155" onclick="openClientPanel(${b.id})">${esc(b.name||'')}</span>${_scoreBadge(b)}${_socialIcons(b)}${_calendlyBadge(b)}</div>
        <div class="biz-sub">${esc(b.category||'')}${b.city ? ' · '+esc(b.city) : ''}</div>
      </div>
      <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">${b.phone ? (hasWhatsApp(b.phone) ? `<a class="phone-val" href="https://wa.me/${waNum(b.phone)}${b.pitch_text ? '?text='+encodeURIComponent(b.pitch_text) : ''}" target="_blank" title="Abrir WhatsApp">${esc(b.phone)}</a>` : `<span class="phone-plain">${esc(b.phone)}</span>`) : '<span class="no-val">—</span>'}${b.no_contesto_count ? `<span class="no-answer-badge" title="${b.no_contesto_count} veces sin contestar">✗ ${b.no_contesto_count}</span>` : ''}${b.no_interesa_count ? `<span class="no-interest-badge" title="Dijo que no le interesa ${b.no_interesa_count} vez/veces">✕ NI</span>` : ''}</div>
      <div><textarea class="notes-inline" data-id="${b.id}" data-notes="${esc(b.notes||'')}" placeholder="Agregar nota..." rows="1" oninput="this.style.height='auto';this.style.height=this.scrollHeight+'px'"></textarea></div>
      <div class="actions">
        <a class="pitch-btn" href="tel:${b.phone||''}" style="text-decoration:none"><i data-lucide=\"phone\" class=\"btn-icon\"></i> Llamar</a>
        <button class="pitch-btn" onclick="openCallModal(${b.id},'${esc(b.name||'')}','${esc(b.phone||'')}','cola')" style="background:#1e293b"><i data-lucide=\"clipboard-list\" class=\"btn-icon\"></i> Resultado</button>
        <button class="delete-btn" onclick="deleteLead(${b.id},'${esc(b.name||'')}')" title="Borrar"><i data-lucide=\"trash-2\" class=\"btn-icon\"></i></button>
      </div>
    </div>`).join('');
  _populateNotes(body);
}


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

let _sdrLastActor = {};

async function loadSeguimientos() {
  const body = document.getElementById('seguimientos-body');
  body.innerHTML = '<div style="color:#475569;padding:16px;font-size:.85rem">Cargando...</div>';
  try {
    const [r1, r2] = await Promise.all([
      fetch('/api/leads?crm_status=llamar_despues'),
      fetch('/api/leads?crm_status=interesado'),
    ]);
    const [d1, d2] = await Promise.all([r1.json(), r2.json()]);
    const leads = [
      ...(Array.isArray(d1) ? d1 : (d1.items || [])),
      ...(Array.isArray(d2) ? d2 : (d2.items || [])),
    ].sort((a,b) => {
      // llamar_despues with date first, then contactado
      if (a.callback_date && !b.callback_date) return -1;
      if (!a.callback_date && b.callback_date) return 1;
      if (a.callback_date && b.callback_date) return a.callback_date.localeCompare(b.callback_date);
      return 0;
    });
    if (!leads.length) { body.innerHTML = '<div class="empty-state">No hay seguimientos pendientes</div>'; return; }
    const today = new Date().toISOString().split('T')[0];
    body.innerHTML = leads.map(b => {
      const cd = b.callback_date || '';
      const isContactado = b.crm_status === 'interesado';
      let urgencyClass = '', pillClass = 'cb-date-future', pillLabel = 'Sin fecha';
      if (isContactado && !cd) {
        pillClass = 'cb-date-future'; pillLabel = 'Interesado';
      } else if (cd) {
        const cdDate = cd.split('T')[0];
        pillLabel = cd.replace('T',' ').replace(/:\d{2}$/,'');
        if (cdDate < today) { urgencyClass = 'cb-overdue'; pillClass = 'cb-date-overdue'; pillLabel = '⚠ ' + pillLabel; }
        else if (cdDate === today) { urgencyClass = 'cb-today'; pillClass = 'cb-date-today'; pillLabel = '📅 Hoy ' + cd.split('T')[1]?.replace(/:\d{2}$/,''); }
      }
      return `
      <div class="table-row no-cb row-llamar_despues ${urgencyClass}">
        <div>
          <div class="biz-name"><span style="cursor:pointer;text-decoration:underline;text-decoration-color:#334155" onclick="openClientPanel(${b.id})">${esc(b.name||'')}</span>${_calendlyBadge(b)}</div>
          <div class="biz-sub">${esc(b.category||'')}${b.city ? ' · '+esc(b.city) : ''}</div>
        </div>
        <div style="display:flex;align-items:center;gap:6px">${b.phone ? (hasWhatsApp(b.phone) ? `<a class="phone-val" href="https://wa.me/${waNum(b.phone)}${b.pitch_text ? '?text='+encodeURIComponent(b.pitch_text) : ''}" target="_blank" title="Abrir WhatsApp">${esc(b.phone)}</a>` : `<span class="phone-plain">${esc(b.phone)}</span>`) : '<span class="no-val">—</span>'}</div>
        <div><span class="cb-date-pill ${pillClass}">${pillLabel}</span></div>
        <div><textarea class="notes-inline" data-id="${b.id}" data-notes="${esc(b.notes||'')}" placeholder="Agregar nota..." rows="1" oninput="this.style.height='auto';this.style.height=this.scrollHeight+'px'"></textarea></div>
        <div class="actions">
          <a class="pitch-btn" href="tel:${b.phone||''}" style="text-decoration:none"><i data-lucide=\"phone\" class=\"btn-icon\"></i> Llamar</a>
          <button class="pitch-btn" onclick="openCallModal(${b.id},'${esc(b.name||'')}','${esc(b.phone||'')}','seguimientos')" style="background:#1e293b"><i data-lucide=\"clipboard-list\" class=\"btn-icon\"></i> Resultado</button>
          <button class="delete-btn" onclick="deleteLead(${b.id},'${esc(b.name||'')}')" title="Borrar"><i data-lucide=\"trash-2\" class=\"btn-icon\"></i></button>
        </div>
      </div>`; }).join('');
    _populateNotes(body);
  } catch(e) { body.innerHTML = `<div style="color:#f87171;padding:16px">Error: ${e.message}</div>`; }
}

// ── Pipeline ──────────────────────────────────────────────────────────────────
let _pipelineSearch = '';
let _pipelineLeads = [];
function pipelineSearch(v) { _pipelineSearch = v.toLowerCase(); renderPipelineTable(); }

async function loadPipelinePanel() {
  const body = document.getElementById('pipeline-body');
  body.innerHTML = '<div style="color:#475569;padding:16px;font-size:.85rem">Cargando...</div>';
  try {
    const r = await fetch('/api/leads?crm_group=pipeline');
    const data = await r.json();
    _pipelineLeads = Array.isArray(data) ? data : (data.items || []);
    renderPipelineTable();
  } catch(e) { body.innerHTML = `<div style="color:#f87171;padding:16px">Error: ${e.message}</div>`; }
}

function renderPipelineTable() {
  const body = document.getElementById('pipeline-body');
  const crmLabels = {reunion_agendada:'Reunión agendada',reunion_hecha:'Reunión hecha',presupuesto_enviado:'Presupuesto enviado',negociacion:'Negociación'};
  const crmColor = {reunion_agendada:'#60a5fa',reunion_hecha:'#34d399',presupuesto_enviado:'#fbbf24',negociacion:'#fb923c'};
  let leads = _pipelineLeads;
  if (_pipelineSearch) leads = leads.filter(b => (b.name||'').toLowerCase().includes(_pipelineSearch));
  if (!leads.length) { body.innerHTML = '<div class="empty-state">No hay leads en el pipeline</div>'; return; }
  body.innerHTML = leads.map(b => {
    const crm = b.crm_status || '';
    const color = crmColor[crm] || '#475569';
    return `
    <div class="table-row no-cb row-${crm}">
      <div>
        <div class="biz-name"><span style="cursor:pointer;text-decoration:underline;text-decoration-color:#334155" onclick="openClientPanel(${b.id})">${esc(b.name||'')}</span>${_calendlyBadge(b)}</div>
        <div class="biz-sub">${esc(b.category||'')}${b.city ? ' · '+esc(b.city) : ''}</div>
      </div>
      <div><span style="font-size:.72rem;font-weight:600;color:${color};background:${color}18;padding:3px 8px;border-radius:99px">${crmLabels[crm]||crm}</span></div>
      <div>${b.phone ? (hasWhatsApp(b.phone) ? `<a class="phone-val" href="https://wa.me/${waNum(b.phone)}${b.pitch_text ? '?text='+encodeURIComponent(b.pitch_text) : ''}" target="_blank" title="Abrir WhatsApp">${esc(b.phone)}</a>` : `<span class="phone-plain">${esc(b.phone)}</span>`) : '<span class="no-val">—</span>'}</div>
      <div><textarea class="notes-inline" data-id="${b.id}" data-notes="${esc(b.notes||'')}" placeholder="Agregar nota..." rows="1" oninput="this.style.height='auto';this.style.height=this.scrollHeight+'px'"></textarea></div>
      <div class="actions">
        <button class="pitch-btn" onclick="openClientPanel(${b.id})">Ver ficha</button>
      </div>
    </div>`; }).join('');
  _populateNotes(body);
}

// ── Clientes ──────────────────────────────────────────────────────────────────
async function loadClientesPanel() {
  const body = document.getElementById('clientes-body');
  body.innerHTML = '<div style="color:#475569;padding:16px;font-size:.85rem">Cargando...</div>';
  try {
    const r = await fetch('/api/leads?crm_group=clientes');
    const data = await r.json();
    const leads = Array.isArray(data) ? data : (data.items || []);
    if (!leads.length) { body.innerHTML = '<div class="empty-state">No hay clientes todavía</div>'; return; }
    const crmLabels = {cliente_cerrado:'Cliente cerrado',en_desarrollo:'En desarrollo',finalizado:'Finalizado'};
    const crmColor = {cliente_cerrado:'#4ade80',en_desarrollo:'#0088cc',finalizado:'#a78bfa'};
    body.innerHTML = leads.map(b => {
      const crm = b.crm_status || '';
      const color = crmColor[crm] || '#475569';
      return `
      <div class="table-row no-cb row-${crm}">
        <div>
          <div class="biz-name"><span style="cursor:pointer;text-decoration:underline;text-decoration-color:#334155" onclick="openClientPanel(${b.id})">${esc(b.name||'')}</span>${_calendlyBadge(b)}</div>
          <div class="biz-sub">${esc(b.category||'')}${b.city ? ' · '+esc(b.city) : ''}</div>
        </div>
        <div><span style="font-size:.72rem;font-weight:600;color:${color};background:${color}18;padding:3px 8px;border-radius:99px">${crmLabels[crm]||crm}</span></div>
        <div>${b.phone ? (hasWhatsApp(b.phone) ? `<a class="phone-val" href="https://wa.me/${waNum(b.phone)}${b.pitch_text ? '?text='+encodeURIComponent(b.pitch_text) : ''}" target="_blank" title="Abrir WhatsApp">${esc(b.phone)}</a>` : `<span class="phone-plain">${esc(b.phone)}</span>`) : '<span class="no-val">—</span>'}</div>
        <div><textarea class="notes-inline" data-id="${b.id}" data-notes="${esc(b.notes||'')}" placeholder="Agregar nota..." rows="1" oninput="this.style.height='auto';this.style.height=this.scrollHeight+'px'"></textarea></div>
        <div class="actions">
          <button class="pitch-btn" onclick="openClientPanel(${b.id})">Ver ficha</button>
        </div>
      </div>`; }).join('');
    _populateNotes(body);
  } catch(e) { body.innerHTML = `<div style="color:#f87171;padding:16px">Error: ${e.message}</div>`; }
}

// ── Save notes inline ─────────────────────────────────────────────────────────
async function saveNote(id, notes) {
  await fetch(`/api/leads/${id}/notes`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({notes})});
}
document.addEventListener('focusout', async e => {
  if (!e.target.classList.contains('notes-inline')) return;
  const id = e.target.dataset.id;
  if (!id) return;
  await saveNote(id, e.target.value);
});
document.addEventListener('keydown', async e => {
  if (!e.target.classList.contains('notes-inline')) return;
  if (e.key !== 'Enter' || e.shiftKey) return;
  e.preventDefault();
  const id = e.target.dataset.id;
  if (!id) return;
  await saveNote(id, e.target.value);
  e.target.blur();
});
function _populateNotes(container) {
  container.querySelectorAll('.notes-inline[data-notes]').forEach(ta => {
    ta.value = ta.dataset.notes || '';
    if (ta.value) { ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 'px'; }
  });
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

async function loadStats() {
  try {
  const r = await fetch('/api/stats');
  if (!r.ok) { document.getElementById('stat-total').textContent = 'ERR '+r.status; return; }
  const d = await r.json();
  document.getElementById('stat-total').textContent = d.total;
  document.getElementById('stat-pitch').textContent = d.with_pitch;
  document.getElementById('stat-contacted').textContent = d.contacted;
  const sel = document.getElementById('category-filter');
  const prev = sel ? sel.value : '';
  if (sel) { while (sel.options.length > 1) sel.remove(1); (d.categories || []).forEach(c => { sel.add(new Option(c, c)); }); if (prev) sel.value = prev; }
  const pd = document.getElementById('page-date');
  if (pd) pd.textContent = 'Actualizado: ' + new Date().toLocaleString('es-UY');
  } catch(e) {}
}

function _updatePagination() {
  const el = document.getElementById('leads-pagination');
  if (!el) return;
  if (totalPages <= 1) { el.style.display = 'none'; return; }
  el.style.display = 'flex';
  el.innerHTML =
    `<button onclick="gotoPage(${currentPage - 1})" ${currentPage <= 1 ? 'disabled' : ''} style="background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:6px 14px;border-radius:6px;cursor:pointer">← Anterior</button>` +
    `<span>Página ${currentPage} de ${totalPages}</span>` +
    `<button onclick="gotoPage(${currentPage + 1})" ${currentPage >= totalPages ? 'disabled' : ''} style="background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:6px 14px;border-radius:6px;cursor:pointer">Siguiente →</button>`;
}

function gotoPage(p) {
  if (p < 1 || p > totalPages) return;
  currentPage = p;
  loadLeads();
}

async function loadLeads() {
  const body2 = document.getElementById('table-body');
  try {
  const params = new URLSearchParams();
  if (currentCrm) params.set('crm_status', currentCrm);
  if (currentCategory) params.set('category', currentCategory);
  if (currentSearch) params.set('search', currentSearch);
  params.set('page', currentPage);
  const r = await fetch('/api/leads?' + params);
  if (!r.ok) { body2.innerHTML = `<div style="color:#f87171;padding:16px">API error ${r.status}</div>`; return; }
  const data = await r.json();
  const leads = Array.isArray(data) ? data : (data.items || []);
  if (data.pages !== undefined) { totalPages = data.pages; currentPage = data.page || currentPage; }
  _allLeads = leads;
  pitchMap = {};
  leads.forEach(b => { if (b.pitch_text) pitchMap[b.id] = b.pitch_text; });
  const body = body2;
  if (!leads.length) { body.innerHTML = '<div class="empty-state">No hay leads con estos filtros</div>'; return; }
  const crmLabels = {sin_contactar:'Sin contactar',contactado:'Contactado',reunion_agendada:'Reunión agendada',reunion_hecha:'Reunión hecha',presupuesto_enviado:'Presupuesto enviado',negociacion:'Negociación',cliente_cerrado:'Cliente cerrado',en_desarrollo:'En desarrollo',finalizado:'Finalizado',agendo:'Agendó',firmo:'Firmó'};
  body.innerHTML = leads.map(b => {
    const crm = b.crm_status || 'sin_contactar';
    return `
    <div class="table-row row-${crm}">
      <div class="cb-col"><input type="checkbox" class="cb row-cb" data-id="${b.id}" onchange="toggleSelect(${b.id},this.checked)"></div>
      <div>
        <div class="biz-name"><span style="cursor:pointer;text-decoration:underline;text-decoration-color:#334155" onclick="openClientPanel(${b.id})">${esc(b.name||'')}</span>${_scoreBadge(b)}${_socialIcons(b)}${_calendlyBadge(b)}</div>
        <div class="biz-sub">${esc(b.category||'')}${b.city ? ' · '+esc(b.city) : ''}${b.last_event_at ? ' · <span style="color:#60a5fa">'+timeAgo(b.last_event_at)+'</span>' : ''}</div>
      </div>
      <div style="display:flex;align-items:center;gap:6px">${b.phone ? (hasWhatsApp(b.phone) ? `<a class="phone-val" href="https://wa.me/${waNum(b.phone)}${b.pitch_text ? '?text='+encodeURIComponent(b.pitch_text) : ''}" target="_blank" title="Abrir WhatsApp">${esc(b.phone)}</a>` : `<span class="phone-plain">${esc(b.phone)}</span>`) : '<span class="no-val">—</span>'}${b.phone ? `<a class="call-btn" href="tel:${esc(b.phone)}" title="Llamar">📞</a>` : ''}${b.pitch_text ? `<button class="copy-pitch-btn" onclick="copyPitch(${b.id},event)" title="Copiar pitch">📋</button>` : ''}</div>
      <div style="font-size:.75rem;color:#94a3b8">${(() => {
        const raw = b.callback_date || b.last_event_at;
        if (!raw) return '<span style="color:#334155">—</span>';
        const d = new Date(raw);
        const now = new Date();
        const isCallback = !!b.callback_date;
        const isPast = isCallback && d < now;
        const dateStr = d.toLocaleDateString('es-UY',{day:'2-digit',month:'2-digit',year:'2-digit'});
        if (isCallback) return `<span style="color:${isPast?'#f87171':'#fbbf24'};font-weight:600">📅 ${dateStr}</span>`;
        return `<span style="color:#64748b">${dateStr}</span>`;
      })()}</div>
      <div class="actions">
        ${(!crm || crm === 'sin_contactar') ? `<button class="pitch-btn" onclick="markContacted(${b.id})">Contactar</button>` : `<span style="color:#3db648;font-size:.75rem">✓ ${crmLabels[crm]||crm}</span>`}
        <button class="delete-btn" onclick="deleteLead(${b.id},event)" title="Borrar lead">🗑</button>
      </div>
    </div>`}).join('');
  _updatePagination();
  } catch(e) { document.getElementById('table-body').innerHTML = `<div style="color:#f87171;padding:16px">Error JS: ${e.message}</div>`; }
}

// ── Batch selection ─────────────────────────────────────────────────────────
let selectedIds = new Set();

function toggleSelect(id, checked) {
  checked ? selectedIds.add(id) : selectedIds.delete(id);
  updateBatchBar();
}

function toggleSelectAll(checked) {
  document.querySelectorAll('.row-cb').forEach(cb => {
    cb.checked = checked;
    const id = parseInt(cb.dataset.id);
    if (isNaN(id)) return;
    checked ? selectedIds.add(id) : selectedIds.delete(id);
  });
  updateBatchBar();
}

function updateBatchBar() {
  const bar = document.getElementById('batch-bar');
  const count = document.getElementById('batch-count');
  const n = selectedIds.size;
  if (n > 0) {
    bar.classList.add('open');
    count.textContent = n + (n === 1 ? ' seleccionado' : ' seleccionados');
  } else {
    bar.classList.remove('open');
    document.getElementById('cb-all').checked = false;
  }
}

function clearSelection() {
  selectedIds.clear();
  document.querySelectorAll('.row-cb').forEach(cb => cb.checked = false);
  document.getElementById('cb-all').checked = false;
  updateBatchBar();
}

async function applyBatch() {
  const status = document.getElementById('batch-status').value;
  if (!status) { alert('Elegí un estado'); return; }
  if (!selectedIds.size) return;
  const res = await fetch('/api/leads/batch-status', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ids: [...selectedIds], crm_status: status})
  });
  const data = await res.json();
  clearSelection();
  document.getElementById('batch-status').value = '';
  loadLeads();
}

// ── CSV Export ───────────────────────────────────────────────────────────────
function exportCSV() {
  const cols = ['id','name','phone','category','city','crm_status','rating','address','scraped_at'];
  const headers = ['ID','Nombre','Teléfono','Rubro','Ciudad','Estado CRM','Rating','Dirección','Fecha scrape'];
  const rows = [headers.join(',')];
  for (const b of _allLeads) {
    const row = cols.map(k => {
      const v = b[k] == null ? '' : String(b[k]);
      return '"' + v.replace(/"/g, '""') + '"';
    });
    rows.push(row.join(','));
  }
  const blob = new Blob([rows.join('\\n')], {type: 'text/csv;charset=utf-8;'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'leads_scalerics.csv'; a.click();
  URL.revokeObjectURL(url);
}

function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
function timeAgo(ts) {
  if (!ts) return '';
  const diff = Math.floor((Date.now() - new Date(ts + 'Z').getTime()) / 1000);
  if (diff < 60) return 'hace un momento';
  if (diff < 3600) return 'hace ' + Math.floor(diff/60) + 'm';
  if (diff < 86400) return 'hace ' + Math.floor(diff/3600) + 'h';
  const d = Math.floor(diff/86400);
  return 'hace ' + d + (d===1?' día':' días');
}
function waNum(phone) {
  const raw = String(phone).trim();
  let n = raw.replace(/[^0-9]/g,'');
  if (raw.startsWith('+')) return n;  // already has country code (+54, +598, etc.)
  if (n.startsWith('598')) return n;
  if (n.startsWith('0')) n = n.slice(1);
  return '598' + n;
}
function hasWhatsApp(phone) {
  // International numbers (+ prefix) → assume WA
  if (String(phone).trim().startsWith('+')) return true;
  const n = String(phone).replace(/[^0-9]/g,'');
  // Uruguay mobile: starts with 09 (raw) or 9 after stripping leading 0
  const local = n.startsWith('598') ? n.slice(3) : (n.startsWith('0') ? n.slice(1) : n);
  return local.startsWith('9');
}

async function copyPitch(id, ev) {
  const btn = ev.currentTarget;
  try {
    const r = await fetch('/api/leads/'+id);
    const b = await r.json();
    await navigator.clipboard.writeText(b.pitch_text||'');
    btn.textContent = '✅';
    setTimeout(()=>{ btn.textContent = '📋'; }, 1500);
  } catch(e) { btn.textContent = '❌'; setTimeout(()=>{ btn.textContent = '📋'; }, 1500); }
}

function openPitchModal(id, name) {
  document.getElementById('pitch-modal-title').textContent = name;
  document.getElementById('pitch-modal-text').value = pitchMap[id] || '';
  document.getElementById('pitch-modal').classList.add('open');
}
function closePitchModal() { document.getElementById('pitch-modal').classList.remove('open'); }
function copyPitchText() {
  navigator.clipboard.writeText(document.getElementById('pitch-modal-text').value).then(() => {
    const btn = document.querySelector('#pitch-modal .btn-confirm');
    btn.textContent = '✓ Copiado';
    setTimeout(() => { btn.textContent = '📋 Copiar'; }, 1800);
  });
}

function openContact(id, name) {
  contactingId = id;
  document.getElementById('modal-title').textContent = `Contactar: ${name}`;
  document.getElementById('modal-note').value = '';
  document.getElementById('contact-modal').classList.add('open');
}
function closeContactModal() { document.getElementById('contact-modal').classList.remove('open'); contactingId = null; }
async function confirmContact() {
  if (!contactingId) return;
  const note = document.getElementById('modal-note').value;
  await fetch(`/api/leads/${contactingId}/contact`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({note})});
  closeContactModal(); loadStats(); loadLeads();
}

async function setCrmStatus(id, status) {
  await fetch(`/api/leads/${id}/crm-status`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({crm_status:status})});
  loadStats();
}

async function markContacted(id) {
  await fetch(`/api/leads/${id}/crm-status`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({crm_status:'interesado'})});
  loadStats(); loadLeads();
}

function _refreshActivePanel() {
  if (activePanel === 'cola') loadCola();
  else if (activePanel === 'seguimientos') loadSeguimientos();
  else if (activePanel === 'clientes') loadClientesPanel();
}

async function deleteLead(id, name) {
  if (!confirm(`¿Eliminar "${name}"? Esta acción no se puede deshacer.`)) return;
  await fetch(`/api/leads/${id}`, {method:'DELETE'});
  _refreshActivePanel();
}

function openPipelineModal() { document.getElementById('pipeline-modal').classList.add('open'); }
function closePipelineModal() { if (pipelinePolling) return; document.getElementById('pipeline-modal').classList.remove('open'); }

async function runPipeline() {
  const query = document.getElementById('pipeline-query').value.trim();
  const max = parseInt(document.getElementById('pipeline-max').value) || 30;
  if (!query) { document.getElementById('pipeline-query').focus(); return; }
  const btn = document.getElementById('pipeline-run-btn');
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Corriendo...';
  setPipelinePill('running');
  document.getElementById('pipeline-log').innerHTML = '';
  const res = await fetch('/api/run-pipeline', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({query, max})});
  const d = await res.json();
  if (!d.ok) { appendLog('ERROR: '+(d.error||'Error desconocido'),'err'); resetPipelineBtn(); setPipelinePill('error'); return; }
  pipelinePolling = setInterval(pollPipeline, 1500);
}

async function pollPipeline() {
  const r = await fetch('/api/pipeline-status');
  const d = await r.json();
  const logEl = document.getElementById('pipeline-log');
  if (d.log && d.log.length) {
    logEl.innerHTML = d.log.map(l => {
      const low = l.toLowerCase();
      const cls = (low.includes('error')||low.includes('traceback')) ? 'err' : (low.includes('warn')||low.includes('skip')) ? 'warn' : 'ok';
      return `<div class="log-line ${cls}">${esc(l)}</div>`;
    }).join('');
    logEl.scrollTop = logEl.scrollHeight;
  }
  if (!d.running) {
    clearInterval(pipelinePolling); pipelinePolling = null; resetPipelineBtn();
    if (d.error) { appendLog('✕ '+d.error,'err'); setPipelinePill('error'); }
    else { appendLog('✓ Pipeline completado','ok'); setPipelinePill('done'); loadStats(); loadLeads(); }
  }
}

function appendLog(msg, cls) {
  const logEl = document.getElementById('pipeline-log');
  const div = document.createElement('div');
  div.className = 'log-line '+(cls||''); div.textContent = msg;
  logEl.appendChild(div); logEl.scrollTop = logEl.scrollHeight;
}
function resetPipelineBtn() {
  const btn = document.getElementById('pipeline-run-btn');
  btn.disabled = false; btn.innerHTML = '▶ Buscar leads y generar pitches';
}
function setPipelinePill(state) {
  const pill = document.getElementById('pipeline-pill');
  pill.className = 'status-pill '+state;
  if (state==='running') pill.innerHTML = '<span class="spinner"></span> Corriendo...';
  else if (state==='done') pill.textContent = '✓ Completado';
  else if (state==='error') pill.textContent = '✕ Error';
  else pill.textContent = '● Listo';
}

document.querySelectorAll('.filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentCrm = btn.dataset.crm;
    currentPage = 1;
    loadLeads();
  });
});
let searchTimeout;
document.getElementById('contact-modal').addEventListener('click', e => { if(e.target===e.currentTarget) closeContactModal(); });
document.getElementById('pitch-modal').addEventListener('click', e => { if(e.target===e.currentTarget) closePitchModal(); });
document.getElementById('pipeline-modal').addEventListener('click', e => { if(e.target===e.currentTarget) closePipelineModal(); });

// ========== WA Templates ==========
async function loadWaTemplates() {
  try {
    const r = await fetch('/api/wa/templates');
    const templates = await r.json();
    const list = document.getElementById('wa-template-list');
    if (!list) return;
    if (!templates.length) { list.innerHTML = '<div style="font-size:.72rem;color:#475569;padding:2px 0">Sin plantillas guardadas</div>'; return; }
    list.innerHTML = templates.map(t =>
      `<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid #1a2234">
        <span style="font-size:.78rem;color:#94a3b8;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-right:8px">${esc(t.name)}</span>
        <div style="display:flex;gap:4px;flex-shrink:0">
          <button onclick="useWaTemplate(${JSON.stringify(t.body)})" style="font-size:.7rem;background:#1e293b;border:none;color:#60a5fa;padding:2px 8px;border-radius:4px;cursor:pointer">Usar</button>
          <button onclick="deleteWaTemplate(${t.id})" style="font-size:.7rem;background:#1e293b;border:none;color:#f87171;padding:2px 8px;border-radius:4px;cursor:pointer">✕</button>
        </div>
      </div>`
    ).join('');
  } catch(e) { console.error('loadWaTemplates', e); }
}

function toggleWaTemplateForm() {
  const f = document.getElementById('wa-template-form');
  if (f) f.style.display = f.style.display === 'none' ? 'block' : 'none';
}

async function saveWaTemplate() {
  const name = (document.getElementById('wa-tmpl-name').value || '').trim();
  const body = (document.getElementById('wa-tmpl-body').value || '').trim();
  if (!name || !body) return;
  await fetch('/api/wa/templates', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name, body})});
  document.getElementById('wa-tmpl-name').value = '';
  document.getElementById('wa-tmpl-body').value = '';
  const f = document.getElementById('wa-template-form');
  if (f) f.style.display = 'none';
  loadWaTemplates();
}

function useWaTemplate(body) {
  const input = document.getElementById('wa-input');
  if (input) { input.value = body; input.focus(); }
}

async function deleteWaTemplate(id) {
  await fetch(`/api/wa/templates/${id}`, {method:'DELETE'});
  loadWaTemplates();
}

// ========== WhatsApp panel ==========
let waLoaded = false;
let waLeads = [];
let selectedPhone = null;
let waPolling = null;

function waStateBadgeClass(state) {
  if (!state) return 'wa-state-NEW';
  if (state === 'SCHEDULED') return 'wa-state-SCHEDULED';
  if (state === 'SCORED') return 'wa-state-SCORED';
  if (state === 'NURTURE') return 'wa-state-NURTURE';
  if (state === 'DISQUALIFIED') return 'wa-state-DISQUALIFIED';
  if (state === 'HUMAN_QUEUED') return 'wa-state-NURTURE';
  if (state.startsWith('QUAL')) return 'wa-state-QUAL';
  return 'wa-state-NEW';
}

function fmtWaTime(ts) {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    return d.toLocaleString('es-UY', {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
  } catch(e) { return ts; }
}

async function loadWaLeads() {
  waLoaded = true;
  const listEl = document.getElementById('wa-lead-list');
  const r = await fetch('/api/wa/leads');
  const d = await r.json();
  if (d.error) {
    listEl.innerHTML = `<div class="wa-error-banner">${esc(d.error)}</div>`;
    return;
  }
  waLeads = d;
  if (!d.length) { listEl.innerHTML = '<div class="wa-no-leads">No hay leads en el bot</div>'; return; }
  listEl.innerHTML = d.map(lead => `
    <div class="wa-lead-item" id="wa-lead-${esc(lead.phone)}" onclick="selectWaLead('${esc(lead.phone)}','${esc(lead.name||lead.phone)}')">
      <div class="wa-lead-name">${esc(lead.name || lead.phone)}</div>
      <div class="wa-lead-meta">
        <span class="wa-state-badge ${waStateBadgeClass(lead.state)}">${esc(lead.state||'NEW')}</span>
        <span class="wa-lead-time">${fmtWaTime(lead.last_activity)}</span>
      </div>
    </div>`).join('');
}

async function selectWaLead(phone, name) {
  selectedPhone = phone;
  document.querySelectorAll('.wa-lead-item').forEach(el => el.classList.remove('selected'));
  const item = document.getElementById('wa-lead-' + phone);
  if (item) item.classList.add('selected');

  document.getElementById('wa-empty-state').style.display = 'none';
  const content = document.getElementById('wa-chat-content');
  content.style.display = 'flex';
  document.getElementById('wa-chat-name').textContent = name;
  document.getElementById('wa-chat-phone').textContent = phone;
  document.getElementById('wa-messages').innerHTML = '<div style="color:#334155;text-align:center;padding:20px">Cargando...</div>';

  const lead = waLeads.find(l => l.phone === phone);
  const isHuman = lead && (lead.state === 'HUMAN_QUEUED');
  document.getElementById('wa-human-badge').style.display = isHuman ? 'inline-flex' : 'none';
  document.getElementById('wa-release-btn').style.display = isHuman ? 'inline-flex' : 'none';

  await loadWaMessages(phone);
  if (waPolling) clearInterval(waPolling);
  waPolling = setInterval(() => { if (selectedPhone === phone) loadWaMessages(phone); }, 5000);
}

async function releaseToBot() {
  if (!selectedPhone) return;
  const r = await fetch('/api/wa/leads/' + encodeURIComponent(selectedPhone) + '/release', {method:'POST'});
  const d = await r.json();
  if (d.ok) {
    document.getElementById('wa-human-badge').style.display = 'none';
    document.getElementById('wa-release-btn').style.display = 'none';
    await loadWaLeads();
  }
}

async function loadWaMessages(phone) {
  const r = await fetch('/api/wa/leads/' + encodeURIComponent(phone) + '/messages');
  const d = await r.json();
  if (d.error) {
    document.getElementById('wa-messages').innerHTML = `<div style="color:#f87171;padding:16px">${esc(d.error)}</div>`;
    return;
  }
  const el = document.getElementById('wa-messages');
  if (!d.length) { el.innerHTML = '<div style="color:#334155;text-align:center;padding:20px">Sin mensajes</div>'; return; }
  el.innerHTML = d.map(m => `
    <div style="display:flex;flex-direction:column;align-items:${m.direction==='out'?'flex-end':'flex-start'}">
      <div class="wa-bubble ${m.direction==='out'?'wa-bubble-out':'wa-bubble-in'}">${esc(m.content||'')}</div>
      <div class="wa-bubble-time">${fmtWaTime(m.created_at)}</div>
    </div>`).join('');
  setTimeout(() => { el.scrollTop = el.scrollHeight; }, 50);
}

async function sendWaMessage() {
  if (!selectedPhone) return;
  const input = document.getElementById('wa-input');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  const r = await fetch('/api/wa/send', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({phone:selectedPhone, text})});
  const d = await r.json();
  if (!d.ok) { alert('Error enviando mensaje: '+(d.error||'Error desconocido')); input.value = text; return; }
  await loadWaMessages(selectedPhone);
}

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
              <button class="cal-demo-btn" onclick="event.stopPropagation();openDemoModal('${ph||''}','${esc(ev.title||'')}','${nm||''}')">📊 Generar Demo</button>
              <button class="cal-del-btn" onclick="event.stopPropagation();deleteCalEvent('${ev.id}','${esc(ev.title||'')}')">🗑 Borrar</button>
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
  document.getElementById('event-modal').classList.add('open');
}
function closeNewEventModal() { document.getElementById('event-modal').classList.remove('open'); }
document.getElementById('event-modal').addEventListener('click', e => { if(e.target===e.currentTarget) closeNewEventModal(); });

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
  const btn = document.getElementById('ev-save-btn');
  btn.disabled = true; btn.textContent = '...';
  const r = await fetch('/api/calendar/events', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({title,date,time,duration_min:duration,description:desc,meet_link,client_id:clientId})});
  const d = await r.json();
  btn.disabled = false; btn.textContent = '📅 Crear reunión';
  if (!d.ok) { alert('Error: '+(d.error||'Error desconocido')); return; }
  closeNewEventModal();
  renderCalendar();
}

// ========== Demo generation ==========
let _demoMessages = [];
let _demoPhone = '';

function extractPhoneFromText(text) {
  const m = text.match(/[+]?\\d[\\d\\s\\-]{7,14}\\d/);
  return m ? m[0].replace(/[\\s\\-\\+]/g,'') : null;
}

function extractNameFromTitle(title) {
  // Calendly format: "Firstname Lastname: Meeting Type"
  const m = title.match(/^([^:]+):/);
  return m ? m[1].trim() : null;
}

function openDemoModalFromCRM(b) {
  _demoPhone = b.phone || '';
  _demoMessages = [];
  document.getElementById('demo-modal').classList.add('open');
  document.getElementById('demo-form-section').style.display = '';
  document.getElementById('demo-loading-section').style.display = 'none';
  document.getElementById('demo-result-section').style.display = 'none';
  document.getElementById('demo-chat-section').style.display = 'none';
  document.getElementById('demo-phone').value = b.phone || '';
  document.getElementById('demo-biz').value = b.name || '';
  document.getElementById('demo-rubro').value = b.category || '';
  document.getElementById('demo-city').value = b.city || '';
  document.getElementById('demo-color').value = '';
  document.getElementById('demo-conv-info').style.display = 'none';
  document.getElementById('demo-lead-hint').textContent = b.name ? 'Lead: ' + b.name + (b.city ? ' · ' + b.city : '') : '';
}

function openDemoModal(phone, eventTitle, leadName) {
  _demoPhone = phone || '';
  _demoMessages = [];
  document.getElementById('demo-modal').classList.add('open');
  document.getElementById('demo-form-section').style.display = '';
  document.getElementById('demo-loading-section').style.display = 'none';
  document.getElementById('demo-result-section').style.display = 'none';
  document.getElementById('demo-biz').value = '';
  document.getElementById('demo-rubro').value = '';
  document.getElementById('demo-city').value = '';
  document.getElementById('demo-color').value = '';
  document.getElementById('demo-conv-info').style.display = 'none';
  document.getElementById('demo-phone').value = phone || '';
  if (phone) {
    document.getElementById('demo-lead-hint').textContent = 'Cargando datos del lead...';
    fetchLeadForDemo();
  } else if (leadName) {
    document.getElementById('demo-lead-hint').textContent = 'Buscando por nombre: ' + leadName + '...';
    fetchLeadByName(leadName);
  } else {
    document.getElementById('demo-lead-hint').textContent = 'Ingresá el teléfono del lead o completá los campos manualmente.';
  }
}

async function fetchLeadForDemo() {
  const phone = document.getElementById('demo-phone').value.trim();
  if (!phone) { alert('Ingresá un número de teléfono.'); return; }
  _demoPhone = phone;
  document.getElementById('demo-lead-hint').textContent = 'Buscando lead ' + phone + '...';
  try {
    const r = await fetch('/api/wa/lead-by-phone/' + encodeURIComponent(phone));
    if (r.status === 401) { document.getElementById('demo-lead-hint').textContent = 'Sesión expirada — recargá la página y volvé a entrar.'; return; }
    const d = await r.json();
    if (d.lead) {
      _applyLeadToModal(d, phone);
    } else {
      const msg = d.error || 'no encontrado';
      document.getElementById('demo-lead-hint').textContent = phone + ' — ' + msg + '. Podés completar los campos manualmente.';
    }
  } catch(e) {
    document.getElementById('demo-lead-hint').textContent = 'Error buscando lead: ' + e.message;
  }
}

function _applyLeadToModal(d, label) {
  const l = d.lead;
  _demoPhone = l.phone || _demoPhone;
  document.getElementById('demo-phone').value = _demoPhone;
  document.getElementById('demo-lead-hint').textContent = 'Lead: ' + (l.name||label) + ' · Estado: ' + (l.state||'?');
  if (l.business_name) document.getElementById('demo-biz').value = l.business_name;
  if (l.city) document.getElementById('demo-city').value = l.city;
  if (l.rubro_hint && !document.getElementById('demo-rubro').value)
    document.getElementById('demo-rubro').value = l.rubro_hint;
  _demoMessages = d.messages || [];
  if (_demoMessages.length) {
    const infoEl = document.getElementById('demo-conv-info');
    infoEl.textContent = '✅ ' + _demoMessages.length + ' mensajes de WhatsApp cargados.';
    infoEl.style.display = '';
  }
}

async function fetchLeadByName(name) {
  try {
    const r = await fetch('/api/wa/lead-by-name/' + encodeURIComponent(name));
    if (r.status === 401) { document.getElementById('demo-lead-hint').textContent = 'Sesión expirada — recargá la página.'; return; }
    const d = await r.json();
    if (d.lead) {
      _applyLeadToModal(d, name);
    } else {
      document.getElementById('demo-lead-hint').textContent = name + ' — ' + (d.error||'no encontrado') + '. Completá los campos manualmente.';
    }
  } catch(e) {
    document.getElementById('demo-lead-hint').textContent = 'Error buscando lead: ' + e.message;
  }
}

function closeDemoModal() {
  document.getElementById('demo-modal').classList.remove('open');
  document.getElementById('demo-form-section').style.display = '';
  document.getElementById('demo-loading-section').style.display = 'none';
  document.getElementById('demo-result-section').style.display = 'none';
  document.getElementById('demo-chat-section').style.display = 'none';
}

async function startDemoGeneration() {
  const biz = document.getElementById('demo-biz').value.trim();
  const rubro = document.getElementById('demo-rubro').value.trim();
  if (!biz || !rubro) { alert('El nombre del negocio y el rubro son obligatorios.'); return; }
  const city = document.getElementById('demo-city').value.trim();
  const color = document.getElementById('demo-color').value.trim();
  const leadHint = document.getElementById('demo-lead-hint').textContent;
  const leadName = leadHint.includes('Lead:') ? leadHint.split('Lead:')[1].split('·')[0].trim() : '';

  document.getElementById('demo-form-section').style.display = 'none';
  document.getElementById('demo-loading-section').style.display = '';

  try {
    const r = await fetch('/api/demo/generate', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({phone: _demoPhone, business_name: biz, rubro, city, client_color: color, lead_name: leadName, messages: _demoMessages})
    });
    const d = await r.json();
    document.getElementById('demo-loading-section').style.display = 'none';
    if (!d.ok) { document.getElementById('demo-form-section').style.display = ''; alert('Error: '+(d.error||'Error desconocido')); return; }
    document.getElementById('demo-result-section').style.display = '';
    const urlEl = document.getElementById('demo-url-link');
    urlEl.href = d.url; urlEl.textContent = d.url;
    const cachedBadge = document.getElementById('demo-cached-badge');
    if (cachedBadge) cachedBadge.style.display = d.cached ? '' : 'none';
    if (d.questions && d.questions.length) {
      const qs = document.getElementById('demo-q-section');
      qs.style.display = '';
      document.getElementById('demo-q-list').innerHTML = d.questions.map(q=>`<li>${q}</li>`).join('');
    }
  } catch(e) {
    document.getElementById('demo-loading-section').style.display = 'none';
    document.getElementById('demo-form-section').style.display = '';
    alert('Error generando demo: ' + e.message);
  }
}

function copyDemoUrl() {
  const url = document.getElementById('demo-url-link').href;
  navigator.clipboard.writeText(url).then(() => alert('URL copiada ✅')).catch(() => alert(url));
}

let _chatPromptText = '';

async function startDemoChat() {
  const biz = document.getElementById('demo-biz').value.trim();
  const rubro = document.getElementById('demo-rubro').value.trim();
  if (!biz || !rubro) { alert('El nombre del negocio y el rubro son obligatorios.'); return; }
  const city = document.getElementById('demo-city').value.trim();
  const color = document.getElementById('demo-color').value.trim();
  const leadHint = document.getElementById('demo-lead-hint').textContent;
  const leadName = leadHint.includes('Lead:') ? leadHint.split('Lead:')[1].split('·')[0].trim() : '';

  const btn = document.getElementById('demo-chat-btn');
  btn.disabled = true; btn.textContent = 'Generando prompt...';

  try {
    const r = await fetch('/api/demo/prompt', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({phone: _demoPhone, business_name: biz, rubro, city, client_color: color, lead_name: leadName, messages: _demoMessages})
    });
    const d = await r.json();
    if (!d.ok) { alert('Error: ' + (d.error || 'Error desconocido')); return; }
    _chatPromptText = d.prompt;
    document.getElementById('demo-form-section').style.display = 'none';
    document.getElementById('demo-chat-section').style.display = '';
    document.getElementById('demo-chat-copied').style.display = 'none';
  } catch(e) {
    alert('Error: ' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = '💬 Claude Chat';
  }
}

function copyAndOpenClaude() {
  navigator.clipboard.writeText(_chatPromptText).catch(() => {});
  document.getElementById('demo-chat-copied').style.display = '';
  window.open('https://claude.ai/new', '_blank');
}

// ── Tasks (global panel) ──────────────────────────────────────────────────────

let _allTasks = [];
let _allLeads = [];
let _taskStatusFilter = 'all';
let _taskUserFilter = '';
let _taskSearchQuery = '';
let _taskQuickFilter = '';
let _editingTaskId = null;
let _taskSearchTimer = null;

function _getFilteredTasks() {
  let tasks = _allTasks;
  if (_taskUserFilter) tasks = tasks.filter(t => String(t.assignee_id) === String(_taskUserFilter));
  if (_taskQuickFilter === 'high') {
    tasks = tasks.filter(t => t.priority === 'high');
  } else if (_taskQuickFilter === 'overdue') {
    const now = new Date();
    tasks = tasks.filter(t => t.deadline && new Date(t.deadline) < now && t.status !== 'done');
  } else if (_taskStatusFilter !== 'all') {
    tasks = tasks.filter(t => t.status === _taskStatusFilter);
  }
  if (_taskSearchQuery) {
    const q = _taskSearchQuery.toLowerCase();
    tasks = tasks.filter(t => (t.title||'').toLowerCase().includes(q) || (t.description||'').toLowerCase().includes(q));
  }
  return tasks;
}

function _updateFilterCounts() {
  let base = _allTasks;
  if (_taskUserFilter) base = base.filter(t => String(t.assignee_id) === String(_taskUserFilter));
  const now = new Date();
  const counts = {
    all: base.length,
    todo: base.filter(t => t.status === 'todo').length,
    inprogress: base.filter(t => t.status === 'in_progress').length,
    done: base.filter(t => t.status === 'done').length,
    high: base.filter(t => t.priority === 'high').length,
    overdue: base.filter(t => t.deadline && new Date(t.deadline) < now && t.status !== 'done').length,
  };
  ['all','todo','inprogress','done','high','overdue'].forEach(k => {
    const el = document.getElementById('pill-count-' + k);
    if (el) el.textContent = counts[k];
  });
}

async function loadTasks() {
  try {
    const [tr, lr] = await Promise.all([
      fetch('/api/tasks').then(r => r.json()),
      fetch('/api/leads').then(r => r.json()),
    ]);
    _allTasks = Array.isArray(tr) ? tr : [];
    _allLeads = Array.isArray(lr) ? lr : [];
  } catch { _allTasks = []; }
  await _loadUsersForTask();
  _populateUserFilter();
  _updateFilterCounts();
  renderTasksList();
}

function _populateUserFilter() {
  if (_taskUserFilter) {
    const u = _allUsers.find(u => String(u.id) === String(_taskUserFilter));
    if (u) {
      const av = document.getElementById('upick-filter-av');
      const lbl = document.getElementById('upick-filter-label');
      if (av) { av.style.cssText = `background:${_upickColor(u.id)};font-size:.65rem`; av.textContent = _upickInitials(u.name); }
      if (lbl) lbl.textContent = u.name;
    }
  }
}

function filterTasks(status, btn) {
  _taskStatusFilter = status;
  _taskQuickFilter = '';
  document.querySelectorAll('.filter-row-2 .pill').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  renderTasksList();
}

function filterTasksQuick(type, btn) {
  _taskQuickFilter = _taskQuickFilter === type ? '' : type;
  _taskStatusFilter = 'all';
  document.querySelectorAll('.filter-row-2 .pill').forEach(b => b.classList.remove('active'));
  if (_taskQuickFilter && btn) {
    btn.classList.add('active');
  } else {
    const pillAll = document.getElementById('pill-all');
    if (pillAll) pillAll.classList.add('active');
  }
  renderTasksList();
}

function _onTaskUserFilterChange(val) {
  _taskUserFilter = val;
  _updateFilterCounts();
  renderTasksList();
}

function _onTaskSearch(val) {
  clearTimeout(_taskSearchTimer);
  _taskSearchTimer = setTimeout(() => {
    _taskSearchQuery = val.trim();
    renderTasksList();
  }, 200);
}

function renderTasksList() {
  const container = document.getElementById('tasks-list');
  if (!container) return;
  let tasks = _getFilteredTasks();
  tasks = [...tasks].sort((a, b) => {
    const prio = {high:0,medium:1,low:2};
    return (prio[a.priority]||1) - (prio[b.priority]||1);
  });
  const summary = document.getElementById('tasks-summary');
  if (summary) {
    const userLabel = _taskUserFilter
      ? '👤 ' + ((_allUsers.find(u => String(u.id) === String(_taskUserFilter)) || {}).name || '')
      : 'todos los usuarios';
    const filterLabel = _taskQuickFilter === 'high' ? 'Alta prioridad'
      : _taskQuickFilter === 'overdue' ? 'Vencidas'
      : _taskStatusFilter === 'all' ? 'Todas'
      : _taskStatusFilter === 'todo' ? 'Pendientes'
      : _taskStatusFilter === 'in_progress' ? 'En progreso'
      : 'Hechas';
    summary.textContent = `${tasks.length} tarea${tasks.length !== 1 ? 's' : ''} · ${userLabel} · ${filterLabel}`;
  }
  if (!tasks.length) { container.innerHTML = '<div class="tasks-empty">Sin tareas para este filtro.</div>'; return; }
  container.innerHTML = tasks.map(t => _taskRowHtml(t)).join('');
}

function _taskRowHtml(t) {
  const done = t.status === 'done';
  const inProgress = t.status === 'in_progress';
  const lead = t.client_id ? _allLeads.find(l => l.id === t.client_id) : null;
  const now = new Date(); const dl = t.deadline ? new Date(t.deadline) : null;
  const overdue = dl && dl < now && !done;
  const hasTime = dl && (dl.getHours() !== 0 || dl.getMinutes() !== 0);
  const dlStr = dl ? dl.toLocaleDateString('es-UY',{day:'2-digit',month:'2-digit'})
    + (hasTime ? ' ' + dl.toLocaleTimeString('es-UY',{hour:'2-digit',minute:'2-digit'}) : '') : '';
  const prioLabel = ({'high':'Alta','medium':'Media','low':'Baja'})[t.priority] || t.priority;
  const statusLabel = {todo:'● Pendiente', in_progress:'⚡ En progreso', done:'✓ Hecha'}[t.status] || '● Pendiente';
  const statusClass = t.status || 'todo';
  const goalTypeLabel = {
    'leads_contactados':    'leads contactados',
    'llamadas_realizadas':  'llamadas realizadas',
    'llamadas_contestadas': 'llamadas contestadas',
    'reuniones_agendadas':  'reuniones agendadas',
    'reuniones_hechas':     'reuniones hechas',
    'presupuestos_enviados':'presupuestos enviados',
    'clientes_cerrados':    'clientes cerrados',
  };
  const progress = t.goal ? Math.min(t.progress || 0, t.goal) : 0;
  const pct = t.goal ? Math.round(progress / t.goal * 100) : 0;
  const progressBar = t.goal ? `
    <div style="margin-top:6px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:3px;cursor:pointer" onclick="_toggleTaskHistory(${t.id})">
        <span style="font-size:.72rem;color:#64748b">${goalTypeLabel[t.goal_type]||t.goal_type}: </span>
        <span style="font-size:.72rem;font-weight:600;color:${done||pct>=100?'#10b981':'#e2e8f0'}">${progress}/${t.goal}</span>
        ${pct >= 100 ? '<span style="font-size:.68rem;color:#10b981">✓ Meta alcanzada</span>' : ''}
        <span style="font-size:.68rem;color:#334155">▾ historial</span>
      </div>
      <div style="height:4px;background:#1e293b;border-radius:2px;overflow:hidden;max-width:240px">
        <div style="height:100%;width:${pct}%;background:${pct>=100?'#10b981':'#0088cc'};transition:width .3s"></div>
      </div>
      <div id="task-history-${t.id}" style="display:none;margin-top:6px;padding:6px 0;border-top:1px solid #1e293b"></div>
    </div>` : '';
  const assigneeBadge = t.assignee_name ? `<span style="font-size:.72rem;color:#64748b;background:#1a2234;padding:2px 7px;border-radius:10px">→ ${esc(t.assignee_name)}</span>` : '';
  const createdByBadge = t.created_by_name && t.assignee_name ? `<span style="font-size:.72rem;color:#334155">de ${esc(t.created_by_name)}</span>` : '';
  const rowExtra = inProgress ? ' in-progress' : overdue ? ' overdue' : '';
  return `<div class="task-row${rowExtra}" id="task-row-${t.id}">
    <div class="task-body" style="flex:1;min-width:0">
      <div class="task-title ${done ? 'done-text' : ''}">${esc(t.title)}</div>
      ${t.description ? `<div style="font-size:.75rem;color:#64748b;margin-bottom:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(t.description)}</div>` : ''}
      <div class="task-meta">
        <span class="task-status-badge ${statusClass}" onclick="_setTaskStatus(${t.id})" title="Click para cambiar estado">${statusLabel}</span>
        ${t.priority ? `<span class="task-priority ${t.priority}">${prioLabel}</span>` : ''}
        ${lead ? `<span class="task-client-link" onclick="openClientPanel(${lead.id})">${esc(lead.name||'')}</span>` : ''}
        ${dlStr ? `<span class="task-deadline ${overdue ? 'overdue' : ''}">📅 ${dlStr}${overdue?' (vencida)':''}</span>` : ''}
        ${assigneeBadge}${createdByBadge}
      </div>
      ${progressBar}
    </div>
    <div class="task-actions">
      <button class="task-edit-btn" onclick="openEditTaskModal(${t.id})" title="Editar">✏️</button>
      <button class="task-del-btn" onclick="_deleteTask(${t.id})" title="Eliminar">🗑</button>
    </div>
  </div>`;
}

async function _toggleTask(id, wasDone) {
  const newStatus = wasDone ? 'todo' : 'done';
  await fetch('/api/tasks/' + id, {
    method:'PUT', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({status: newStatus})
  });
  const t = _allTasks.find(t => t.id === id);
  if (t) t.status = newStatus;
  _updateFilterCounts();
  renderTasksList();
}

async function _setTaskStatus(id) {
  const t = _allTasks.find(t => t.id === id);
  if (!t) return;
  const cycle = {todo: 'in_progress', in_progress: 'done', done: 'todo'};
  const newStatus = cycle[t.status] || 'in_progress';
  await fetch('/api/tasks/' + id, {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({status: newStatus})
  });
  t.status = newStatus;
  _updateFilterCounts();
  renderTasksList();
  if (_cpClientId) {
    const ct = (_cpData.tasks||[]).find(ct => ct.id === id);
    if (ct) { ct.status = newStatus; _cpSwitchTab('ctasks'); }
  }
}

async function _deleteTask(id) {
  await fetch('/api/tasks/' + id, {method:'DELETE'});
  _allTasks = _allTasks.filter(t => t.id !== id);
  _updateFilterCounts();
  renderTasksList();
  if (_cpClientId) { _cpData.tasks = (_cpData.tasks||[]).filter(t => t.id !== id); _cpSwitchTab('ctasks'); }
}

let _allUsers = [];
async function _loadUsersForTask() {
  if (_allUsers.length) return;
  try {
    const r = await fetch('/api/users');
    _allUsers = await r.json();
  } catch { _allUsers = []; }
}

function _onTaskAssigneeChange(sel) {
  const opt = sel.options[sel.selectedIndex];
  document.getElementById('task-assignee-id').value = opt.dataset.uid || '';
  document.getElementById('task-assignee-email').value = opt.dataset.email || '';
}

async function _toggleTaskHistory(taskId) {
  const el = document.getElementById(`task-history-${taskId}`);
  if (!el) return;
  if (el.style.display !== 'none') { el.style.display = 'none'; return; }
  el.innerHTML = '<div style="font-size:.72rem;color:#475569;padding:2px 0">Cargando...</div>';
  el.style.display = '';
  try {
    const r = await fetch(`/api/tasks/${taskId}/progress-history`);
    const items = await r.json();
    if (!Array.isArray(items) || !items.length) {
      el.innerHTML = '<div style="font-size:.72rem;color:#475569;padding:2px 0">Sin historial aún</div>';
      return;
    }
    el.innerHTML = items.slice(0, 50).map(i => {
      const d = new Date(i.created_at);
      const dStr = d.toLocaleDateString('es-UY',{day:'2-digit',month:'2-digit'})
                 + ' ' + d.toLocaleTimeString('es-UY',{hour:'2-digit',minute:'2-digit'});
      return `<div style="display:flex;gap:8px;align-items:baseline;padding:2px 0;font-size:.72rem">
        <span style="color:#10b981;font-weight:700;min-width:20px">+1</span>
        <span style="color:#94a3b8;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${i.lead_name ? esc(i.lead_name) : '—'}</span>
        <span style="color:#475569;white-space:nowrap">${dStr}</span>
      </div>`;
    }).join('');
  } catch(e) {
    el.innerHTML = '<div style="font-size:.72rem;color:#f87171;padding:2px 0">Error cargando historial</div>';
  }
}

function _onTaskGoalTypeChange() {
  const goalType = document.getElementById('task-goal-type-input').value;
  document.getElementById('task-goal-input').style.display = goalType ? '' : 'none';
}

async function openAddTaskModal(clientId, clientName) {
  _editingTaskId = null;
  document.getElementById('task-title-input').value = '';
  document.getElementById('task-desc-input').value = '';
  document.getElementById('task-priority-input').value = 'medium';
  document.getElementById('task-deadline-input').value = new Date().toISOString().slice(0,16);
  document.getElementById('task-goal-type-input').value = '';
  document.getElementById('task-goal-input').value = '';
  document.getElementById('task-goal-input').style.display = 'none';
  document.getElementById('task-client-search').value = clientName || '';
  document.getElementById('task-client-id').value = clientId || '';
  document.getElementById('task-client-chosen').textContent = clientName ? 'Cliente: ' + clientName : '';
  document.getElementById('task-client-results').style.display = 'none';
  document.getElementById('task-status-input').value = 'todo';
  await _loadUsersForTask();
  _upickSelect('modal', '', '', '', '— Sin asignar —');
  const h3 = document.getElementById('add-task-modal').querySelector('h3');
  if (h3) h3.textContent = 'Nueva tarea';
  const submitBtn = document.getElementById('task-submit-btn');
  if (submitBtn) submitBtn.textContent = '+ Crear tarea';
  document.getElementById('add-task-modal').classList.add('open');
  setTimeout(() => document.getElementById('task-title-input').focus(), 50);
}

async function openEditTaskModal(taskId) {
  const t = _allTasks.find(t => t.id === taskId);
  if (!t) return;
  _editingTaskId = taskId;
  document.getElementById('task-title-input').value = t.title || '';
  document.getElementById('task-desc-input').value = t.description || '';
  document.getElementById('task-priority-input').value = t.priority || 'medium';
  document.getElementById('task-deadline-input').value = t.deadline ? t.deadline.slice(0,16).replace(' ','T') : '';
  document.getElementById('task-goal-type-input').value = t.goal_type || '';
  document.getElementById('task-goal-input').value = t.goal || '';
  document.getElementById('task-goal-input').style.display = t.goal_type ? '' : 'none';
  document.getElementById('task-status-input').value = t.status || 'todo';
  const clientLead = t.client_id ? _allLeads.find(l => l.id === t.client_id) : null;
  document.getElementById('task-client-search').value = clientLead ? (clientLead.name||'') : '';
  document.getElementById('task-client-id').value = t.client_id || '';
  document.getElementById('task-client-chosen').textContent = clientLead ? 'Cliente: ' + (clientLead.name||'') : '';
  document.getElementById('task-client-results').style.display = 'none';
  await _loadUsersForTask();
  _upickSelect('modal', t.assignee_id||'', t.assignee_name||'', t.assignee_email||'', t.assignee_name||'— Sin asignar —');
  const h3 = document.getElementById('add-task-modal').querySelector('h3');
  if (h3) h3.textContent = 'Editar tarea';
  const submitBtn = document.getElementById('task-submit-btn');
  if (submitBtn) submitBtn.textContent = 'Guardar cambios';
  document.getElementById('add-task-modal').classList.add('open');
  setTimeout(() => document.getElementById('task-title-input').focus(), 50);
}

function _taskClientSearch(q) {
  const res = document.getElementById('task-client-results');
  if (!q.trim()) { res.style.display = 'none'; return; }
  const matches = _allLeads.filter(l => l.name && l.name.toLowerCase().includes(q.toLowerCase())).slice(0,6);
  if (!matches.length) { res.style.display = 'none'; return; }
  res.style.display = '';
  res.innerHTML = matches.map(l => `<div style="padding:8px 12px;cursor:pointer;font-size:.82rem;color:#e2e8f0;border-bottom:1px solid #1e293b" onmousedown="_pickTaskClient(${l.id},'${esc(l.name||'')}')">${esc(l.name||'')}</div>`).join('');
}

function _pickTaskClient(id, name) {
  document.getElementById('task-client-id').value = id;
  document.getElementById('task-client-search').value = name;
  document.getElementById('task-client-chosen').textContent = 'Cliente: ' + name;
  document.getElementById('task-client-results').style.display = 'none';
}

let _taskSubmitting = false;
async function submitAddTask() {
  if (_taskSubmitting) return;
  const title = document.getElementById('task-title-input').value.trim();
  if (!title) { document.getElementById('task-title-input').focus(); return; }
  _taskSubmitting = true;
  const body = {
    title,
    description: document.getElementById('task-desc-input').value.trim() || null,
    priority: document.getElementById('task-priority-input').value,
    deadline: document.getElementById('task-deadline-input').value || null,
    status: document.getElementById('task-status-input').value || 'todo',
  };
  const clientId = document.getElementById('task-client-id').value;
  if (clientId) body.client_id = parseInt(clientId);
  const assigneeId = document.getElementById('task-assignee-id').value;
  if (assigneeId) {
    body.assignee_id = parseInt(assigneeId);
    const assigneeUser = _allUsers.find(u => String(u.id) === String(assigneeId));
    body.assignee_name = assigneeUser ? assigneeUser.name : '';
    body.assignee_email = document.getElementById('task-assignee-email').value;
    body.assignee = body.assignee_name;
  }
  const goalType = document.getElementById('task-goal-type-input').value;
  const goalVal = parseInt(document.getElementById('task-goal-input').value);
  if (goalType && goalVal > 0) {
    body.goal_type = goalType;
    body.goal = goalVal;
    if (!_editingTaskId) body.progress = 0;
  }
  try {
    if (_editingTaskId) {
      await fetch('/api/tasks/' + _editingTaskId, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
      const idx = _allTasks.findIndex(t => t.id === _editingTaskId);
      if (idx !== -1) _allTasks[idx] = {..._allTasks[idx], ...body};
      document.getElementById('add-task-modal').classList.remove('open');
      _updateFilterCounts();
      renderTasksList();
      if (_cpClientId) {
        const cpIdx = (_cpData.tasks||[]).findIndex(t => t.id === _editingTaskId);
        if (cpIdx !== -1) _cpData.tasks[cpIdx] = {..._cpData.tasks[cpIdx], ...body};
        _cpSwitchTab('ctasks');
      }
    } else {
      const r = await fetch('/api/tasks', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
      const d = await r.json();
      document.getElementById('add-task-modal').classList.remove('open');
      const newTask = {id: d.id, ...body};
      _allTasks.unshift(newTask);
      _updateFilterCounts();
      renderTasksList();
      if (_cpClientId && body.client_id === _cpClientId) {
        _cpData.tasks = [newTask, ...(_cpData.tasks||[])];
        _cpSwitchTab('ctasks');
      }
    }
  } finally {
    _taskSubmitting = false;
  }
}

// ── Custom user picker ────────────────────────────────────────────────────────

const _upickColors = ['#0369a1','#7e22ce','#065f46','#9a3412','#be185d','#0f766e','#1d4ed8','#a16207'];
function _upickColor(id) { return _upickColors[Number(id||0) % _upickColors.length]; }
function _upickInitials(name) { return (name||'').split(' ').slice(0,2).map(w=>w[0]||'').join('').toUpperCase()||'?'; }

function _upickToggle(id) {
  const trigger = document.getElementById('upick-'+id+'-trigger');
  const dd = document.getElementById('upick-'+id+'-dropdown');
  if (!dd) return;
  const isOpen = dd.style.display !== 'none';
  // close all pickers
  ['filter','modal'].forEach(k => {
    const d = document.getElementById('upick-'+k+'-dropdown');
    const t = document.getElementById('upick-'+k+'-trigger');
    if (d) d.style.display = 'none';
    if (t) t.classList.remove('open');
  });
  if (!isOpen) {
    _upickRenderDropdown(id);
    dd.style.display = '';
    if (trigger) trigger.classList.add('open');
  }
}

function _upickRenderDropdown(id) {
  const dd = document.getElementById('upick-'+id+'-dropdown');
  if (!dd) return;
  const selectedId = id === 'filter' ? _taskUserFilter
    : (document.getElementById('task-assignee-id')||{}).value || '';
  const isFilter = id === 'filter';
  const noneLabel = isFilter ? 'Todos los usuarios' : '— Sin asignar —';
  const noneAv = isFilter ? '👤' : '—';
  const noneAvStyle = isFilter
    ? 'background:#1e293b;color:#475569;font-size:.8rem'
    : 'background:#1e293b;color:#475569;font-size:.9rem';
  const noneSel = !selectedId;
  let html = `<div class="upick-option ${noneSel?'upick-sel':''}" data-uid="" data-name="" data-email="" data-label="${noneLabel}">
    <div class="upick-av" style="${noneAvStyle}">${noneAv}</div>
    <span class="upick-name" style="color:#64748b">${noneLabel}</span>
    ${noneSel?'<span class="upick-check">✓</span>':''}
  </div>`;
  html += _allUsers.map(u => {
    const sel = String(u.id) === String(selectedId);
    return `<div class="upick-option ${sel?'upick-sel':''}" data-uid="${u.id}" data-name="${esc(u.name||'')}" data-email="${esc(u.email||'')}" data-label="${esc(u.name||'')}">
      <div class="upick-av" style="background:${_upickColor(u.id)}">${_upickInitials(u.name)}</div>
      <span class="upick-name">${esc(u.name)}</span>
      ${sel?'<span class="upick-check">✓</span>':''}
    </div>`;
  }).join('');
  dd.innerHTML = html;
  // delegated click — reads data attrs, safe for any name/email content
  dd.onclick = e => {
    const opt = e.target.closest('.upick-option');
    if (!opt) return;
    _upickSelect(id, opt.dataset.uid, opt.dataset.name, opt.dataset.email, opt.dataset.label);
  };
}

function _upickSelect(id, userId, userName, userEmail, label) {
  const trigger = document.getElementById('upick-'+id+'-trigger');
  const av = document.getElementById('upick-'+id+'-av');
  const lbl = document.getElementById('upick-'+id+'-label');
  if (userId) {
    if (av) { av.style.cssText = `background:${_upickColor(userId)};font-size:.65rem`; av.textContent = _upickInitials(userName); }
    if (lbl) lbl.textContent = userName;
  } else {
    const isFilter = id === 'filter';
    if (av) { av.style.cssText = `background:#1e293b;color:#475569;font-size:${isFilter?'.8rem':'.9rem'}`; av.textContent = isFilter ? '👤' : '—'; }
    if (lbl) lbl.textContent = label;
  }
  const dd = document.getElementById('upick-'+id+'-dropdown');
  if (dd) dd.style.display = 'none';
  if (trigger) trigger.classList.remove('open');
  if (id === 'modal') {
    const aid = document.getElementById('task-assignee-id');
    const aem = document.getElementById('task-assignee-email');
    if (aid) aid.value = userId || '';
    if (aem) aem.value = userEmail || '';
  }
  if (id === 'filter') {
    _taskUserFilter = String(userId);
    _updateFilterCounts();
    renderTasksList();
  }
}

// close picker on outside click
document.addEventListener('click', e => {
  if (!e.target.closest('.upick-wrap')) {
    ['filter','modal'].forEach(k => {
      const d = document.getElementById('upick-'+k+'-dropdown');
      const t = document.getElementById('upick-'+k+'-trigger');
      if (d) d.style.display = 'none';
      if (t) t.classList.remove('open');
    });
  }
}, true);

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

// ── Kanban ────────────────────────────────────────────────────────────────────

const KANBAN_COLS = [
  {key:'contactado',     label:'Contactado'},
  {key:'reunion_agendada', label:'Reunión agendada'},
  {key:'reunion_hecha',  label:'Reunión hecha'},
  {key:'presupuesto_enviado', label:'Presupuesto enviado'},
  {key:'cliente_cerrado',label:'Cliente cerrado'},
];

let _kanbanLeads = [];
let _kanbanDragging = null;

async function loadKanban() {
  const board = document.getElementById('kanban-board');
  board.innerHTML = '<div style="color:#475569;font-size:.85rem">Cargando...</div>';
  try {
    const r = await fetch('/api/leads');
    _kanbanLeads = await r.json();
  } catch { board.innerHTML = '<div style="color:#f87171">Error cargando leads</div>'; return; }
  renderKanban();
}

function renderKanban() {
  const board = document.getElementById('kanban-board');
  const grouped = {};
  KANBAN_COLS.forEach(c => grouped[c.key] = []);
  _kanbanLeads.forEach(l => {
    const k = l.crm_status || 'sin_contactar';
    if (grouped[k]) grouped[k].push(l);
    else grouped['sin_contactar'] && grouped['sin_contactar'].push({...l, crm_status:'sin_contactar'});
  });
  board.innerHTML = KANBAN_COLS.map(col => `
    <div class="kanban-col" data-col="${col.key}"
         ondragover="event.preventDefault();this.classList.add('drag-over')"
         ondragleave="this.classList.remove('drag-over')"
         ondrop="_kanbanDrop(event,'${col.key}')">
      <div class="kanban-col-header">
        <span class="kanban-col-title">${col.label}</span>
        <span class="kanban-count">${grouped[col.key].length}</span>
      </div>
      <div class="kanban-cards">
        ${grouped[col.key].length === 0
          ? '<div class="kanban-empty">Sin leads</div>'
          : grouped[col.key].map(l => _kanbanCard(l)).join('')}
      </div>
    </div>`).join('');
}

function _kanbanCard(l) {
  const meta = [l.category, l.city].filter(Boolean).join(' · ');
  return `<div class="kanban-card" draggable="true" data-id="${l.id}"
    ondragstart="_kanbanDragStart(event,${l.id})"
    ondragend="_kanbanDragEnd(event)"
    onclick="openClientPanel(${l.id})">
    <div class="kanban-card-name">${esc(l.name||'')}</div>
    ${meta ? `<div class="kanban-card-meta">${esc(meta)}</div>` : ''}
    ${l.phone ? `<div class="kanban-card-phone">${esc(l.phone)}</div>` : ''}
  </div>`;
}

function _kanbanDragStart(e, id) {
  _kanbanDragging = id;
  e.currentTarget.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
}

function _kanbanDragEnd(e) {
  e.currentTarget.classList.remove('dragging');
  document.querySelectorAll('.kanban-col').forEach(c => c.classList.remove('drag-over'));
}

async function _kanbanDrop(e, newStatus) {
  e.currentTarget.classList.remove('drag-over');
  if (!_kanbanDragging) return;
  const id = _kanbanDragging;
  _kanbanDragging = null;
  const lead = _kanbanLeads.find(l => l.id === id);
  if (!lead || lead.crm_status === newStatus) return;
  lead.crm_status = newStatus;
  renderKanban();
  await fetch(`/api/leads/${id}/crm-status`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({crm_status: newStatus})
  });
}

// Initial load
loadCola();

// ── Score badge + social icons ────────────────────────────────────────────────

function _scoreBadge(b) {
  if (b.score == null) return '';
  const cls = b.score >= 60 ? 'score-hot' : b.score >= 30 ? 'score-mid' : 'score-low';
  return `<span class="score-badge ${cls}" style="cursor:pointer"
    data-ig="${b.instagram_url?1:0}" data-fb="${b.facebook_url?1:0}"
    data-rating="${b.rating||0}" data-reviews="${b.review_count||0}"
    data-hours="${b.hours?1:0}" data-address="${b.address?1:0}"
    onclick="_showScoreBreakdown(event,this)">⚡${b.score}</span>`;
}

function _calendlyBadge(b) {
  if (b.source !== 'calendly_unmatched') return '';
  return '<span style="font-size:.62rem;font-weight:700;background:rgba(251,146,60,.15);color:#fb923c;border:1px solid rgba(251,146,60,.3);padding:1px 6px;border-radius:99px;margin-left:4px" title="Vino de Calendly — verificar si ya es un cliente existente">Sin verificar</span>';
}

function _socialIcons(b) {
  let s = '';
  if (b.instagram_url) s += `<a href="${esc(b.instagram_url)}" target="_blank" title="Instagram" style="display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:4px;background:linear-gradient(135deg,#f09433,#dc2743,#bc1888);color:#fff;font-size:.52rem;font-weight:800;text-decoration:none;flex-shrink:0;line-height:1" onclick="event.stopPropagation()">IG</a>`;
  if (b.facebook_url) s += `<a href="${esc(b.facebook_url)}" target="_blank" title="Facebook" style="display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:4px;background:#1877f2;color:#fff;font-size:.52rem;font-weight:800;text-decoration:none;flex-shrink:0;line-height:1" onclick="event.stopPropagation()">FB</a>`;
  return s ? `<span style="display:inline-flex;gap:3px;align-items:center;margin-left:2px">${s}</span>` : '';
}

function _showScoreBreakdown(event, el) {
  event.stopPropagation();
  const existing = document.getElementById('score-tooltip');
  if (existing) { const same = existing._src === el; existing.remove(); if (same) return; }
  const d = el.dataset;
  const rating = parseFloat(d.rating || 0);
  const reviews = parseInt(d.reviews || 0);
  const rows = [
    {ok: d.ig==='1',    label: 'Instagram',                                   pts: 35},
    {ok: d.fb==='1',    label: 'Facebook',                                    pts: 15},
    {ok: rating>=4.0,   label: `Rating ${rating||'—'}`,                       pts: rating>=4.0?20:rating>=3.5?10:0, note: rating>=3.5&&rating<4.0?'+10':null},
    {ok: rating>=3.5&&rating<4.0, label: `Rating ${rating}`, pts:10, _skip: rating>=4.0||!rating},
    {ok: reviews>=20,   label: `${reviews||'0'} reseñas`,                    pts: reviews>=20?15:reviews>=5?8:0, note: reviews>=5&&reviews<20?'+8':null},
    {ok: reviews>=5&&reviews<20, label: `${reviews} reseñas`, pts:8, _skip: reviews>=20||!reviews},
    {ok: d.hours==='1', label: 'Horario publicado',                            pts: 10},
    {ok: d.address==='1',label:'Dirección',                                   pts: 5},
  ].filter(r => !r._skip);
  const tip = document.createElement('div');
  tip.id = 'score-tooltip';
  tip._src = el;
  tip.style.cssText = 'position:fixed;background:#1e293b;border:1px solid #334155;border-radius:10px;padding:10px 14px;z-index:2000;min-width:190px;box-shadow:0 8px 28px rgba(0,0,0,.5);font-size:.72rem;font-family:Inter,sans-serif';
  tip.innerHTML = `<div style="font-weight:700;color:#64748b;margin-bottom:8px;font-size:.62rem;text-transform:uppercase;letter-spacing:.06em">Desglose ⚡${el.textContent.replace('⚡','')}</div>`
    + rows.map(r => `<div style="display:flex;justify-content:space-between;gap:12px;padding:3px 0;color:${r.ok?'#e2e8f0':'#334155'}">
      <span>${r.ok?'✓':'—'} ${r.label}</span>
      <span style="font-weight:700;color:${r.ok?(r.pts>=20?'#10b981':r.pts>=10?'#38bdf8':'#94a3b8'):'#334155'}">${r.pts?'+'+r.pts:'—'}</span>
    </div>`).join('');
  document.body.appendChild(tip);
  const rect = el.getBoundingClientRect();
  let top = rect.bottom + 6, left = rect.left;
  if (left + 200 > window.innerWidth - 8) left = window.innerWidth - 208;
  if (top + 220 > window.innerHeight) top = rect.top - 226;
  tip.style.top = top + 'px'; tip.style.left = Math.max(8, left) + 'px';
  setTimeout(() => document.addEventListener('click', function _c() { const t = document.getElementById('score-tooltip'); if(t)t.remove(); document.removeEventListener('click',_c); }), 10);
}

// ── Mobile navigation ─────────────────────────────────────────────────────────
const NAV_PRIORITY = ['cola','seguimientos','meta','cal','tasks','pipeline','clientes','wa','metrics','activity'];
const NAV_ICONS = {
  cola:'inbox',seguimientos:'bookmark',meta:'instagram',cal:'calendar',
  tasks:'check-square',pipeline:'trending-up',clientes:'users',
  wa:'message-circle',metrics:'bar-chart-2',activity:'clock'
};
const NAV_LABELS = {
  cola:'Cola',seguimientos:'Seguim.',meta:'Meta',cal:'Agenda',
  tasks:'Tareas',pipeline:'Pipeline',clientes:'Clientes',
  wa:'WA',metrics:'Métricas',activity:'Actividad'
};
let _mobileNavOverflow = [];

function _buildMobileNav(allowedPanels) {
  const nav = document.getElementById('mobile-bottom-nav');
  if (!nav) return;
  const ordered = NAV_PRIORITY.filter(p => allowedPanels.includes(p));
  const visible = ordered.slice(0, 5);
  _mobileNavOverflow = ordered.slice(5);
  nav.innerHTML = visible.map(p => `
    <div class="mbn-item" id="mbn-${p}" onclick="showPanel('${p}')">
      <i data-lucide="${NAV_ICONS[p]}" class="mbn-icon"></i>
      <span class="mbn-label">${NAV_LABELS[p]}</span>
    </div>
  `).join('') + (_mobileNavOverflow.length ? `
    <div class="mbn-item" id="mbn-mas" onclick="openMasSheet()">
      <i data-lucide="more-horizontal" class="mbn-icon"></i>
      <span class="mbn-label">Más</span>
    </div>
  ` : '');
  if (window.lucide) lucide.createIcons({nodes: [nav]});
}

function _syncMobileNav(panelName) {
  document.querySelectorAll('.mbn-item').forEach(i => i.classList.remove('active'));
  const item = document.getElementById('mbn-' + panelName);
  if (item) item.classList.add('active');
  else { const mas = document.getElementById('mbn-mas'); if (mas) mas.classList.add('active'); }
  const fab = document.getElementById('mobile-fab-task');
  if (fab) fab.style.display = (panelName === 'tasks' && window.innerWidth <= 768) ? 'flex' : 'none';
  const title = document.getElementById('mobile-header-title');
  if (title) title.textContent = NAV_LABELS[panelName] || '';
}

function openMasSheet() {
  const grid = document.getElementById('mas-sheet-grid');
  if (grid) {
    const isLight = document.body.classList.contains('light');
    const panelItems = _mobileNavOverflow.map(p => `
      <div class="mas-sheet-item" onclick="closeMasSheet();showPanel('${p}')">
        <i data-lucide="${NAV_ICONS[p]}" class="mas-sheet-icon"></i>
        <span class="mas-sheet-label">${NAV_LABELS[p]}</span>
      </div>
    `).join('');
    const adminLink = document.getElementById('admin-link');
    const adminItem = adminLink && adminLink.style.display !== 'none'
      ? `<a class="mas-sheet-item" href="/admin/users" style="text-decoration:none">
           <i data-lucide="users" class="mas-sheet-icon"></i>
           <span class="mas-sheet-label">Usuarios</span>
         </a>` : '';
    const settingsItems = `
      <div style="grid-column:1/-1;height:1px;background:#1e293b;margin:4px 0"></div>
      ${adminItem}
      <a class="mas-sheet-item" href="/profile" style="text-decoration:none">
        <i data-lucide="user" class="mas-sheet-icon"></i>
        <span class="mas-sheet-label">Mi perfil</span>
      </a>
      <div class="mas-sheet-item" onclick="closeMasSheet();toggleTheme()">
        <i data-lucide="${isLight ? 'moon' : 'sun'}" class="mas-sheet-icon"></i>
        <span class="mas-sheet-label">Modo ${isLight ? 'oscuro' : 'claro'}</span>
      </div>
      <div class="mas-sheet-item" onclick="window.location.href='/logout'" style="grid-column:1/-1">
        <i data-lucide="log-out" class="mas-sheet-icon"></i>
        <span class="mas-sheet-label">Cerrar sesión</span>
      </div>
    `;
    grid.innerHTML = panelItems + settingsItems;
    if (window.lucide) lucide.createIcons({nodes: [grid]});
  }
  document.getElementById('mas-sheet-backdrop').classList.add('open');
  document.getElementById('mas-sheet').classList.add('open');
}

function closeMasSheet() {
  document.getElementById('mas-sheet-backdrop').classList.remove('open');
  document.getElementById('mas-sheet').classList.remove('open');
}

// ── Panel access control ──────────────────────────────────────────────────────
const ALL_PANELS = ['cola','seguimientos','meta','pipeline','clientes','tasks','wa','cal','metrics','activity'];
(async () => {
  try {
    const r = await fetch('/api/me');
    if (!r.ok) return;
    const m = await r.json();
    window._isAdmin = m.is_admin;
    if (m.is_admin) {
      const a = document.getElementById('admin-link');
      if (a) a.style.display = 'block';
    }
    const access = m.panel_access ? JSON.parse(m.panel_access) : null;
    const allowedPanels = (access && !m.is_admin) ? access : ALL_PANELS;
    if (access && !m.is_admin) {
      ALL_PANELS.forEach(p => {
        if (!access.includes(p)) {
          const nav = document.getElementById('nav-' + p);
          if (nav) nav.style.display = 'none';
        }
      });
      if (!access.includes(activePanel)) {
        const first = access[0];
        if (first) showPanel(first);
      }
    }
    _buildMobileNav(allowedPanels);
    _syncMobileNav(activePanel);
  } catch(e) {}
})();

// ── Theme toggle ──────────────────────────────────────────────────────────────
const LOGO_DARK  = 'https://raw.githubusercontent.com/juantomasetti1/scalerics-assets/main/logo_full_alt.png';
const LOGO_LIGHT = 'https://raw.githubusercontent.com/juantomasetti1/scalerics-assets/main/logo_full.png';

function toggleTheme() {
  const isLight = document.body.classList.toggle('light');
  localStorage.setItem('crm-theme', isLight ? 'light' : 'dark');
  _applyThemeUI(isLight);
}
function _applyThemeUI(isLight) {
  const logo = document.getElementById('sidebar-logo');
  if (logo) logo.src = isLight ? LOGO_LIGHT : LOGO_DARK;
  const mLogo = document.getElementById('mobile-header-logo');
  if (mLogo) mLogo.src = isLight ? LOGO_LIGHT : LOGO_DARK;
  const label = document.getElementById('theme-label');
  if (label) label.textContent = isLight ? 'Modo oscuro' : 'Modo claro';
  lucide.createIcons();
}
(function() {
  const saved = localStorage.getItem('crm-theme');
  if (saved === 'light') { document.body.classList.add('light'); _applyThemeUI(true); }
})();

// ── Lucide icons ──────────────────────────────────────────────────────────────
lucide.createIcons();
// Admin link visibility
(async()=>{try{const r=await fetch('/api/me');if(!r.ok)return;const m=await r.json();if(m.is_admin){const a=document.getElementById('admin-link');if(a)a.style.display='block';}}catch(e){}})();

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

// ========== Activity feed ==========
function _actEntityLink(i) {
  if (!i.entity_name) return '';
  if (i.entity_type === 'lead' && i.entity_id)
    return '<b><span class="act-entity-link" onclick="openClientPanel('+Number(i.entity_id)+')">'+esc(i.entity_name)+'</span></b>';
  return '<b>'+esc(i.entity_name)+'</b>';
}
const _actActionLabels = {
  status_change: (i) => `cambió estado${i.entity_name ? ' de '+_actEntityLink(i) : ''} a <b>${_actCrmLabel(i.detail)}</b>`,
  note_updated:  (i) => `actualizó notas${i.entity_name ? ' de '+_actEntityLink(i) : ''}`,
  attachment_added: (i) => `adjuntó archivo${i.entity_name ? ' a '+_actEntityLink(i) : ''}${i.detail ? ': '+esc(i.detail) : ''}`,
  call_logged:   (i) => `registró llamada${i.entity_name ? ' a '+_actEntityLink(i) : ''}: <b>${_actCallLabel(i.detail)}</b>`,
  budget_generated: (i) => `generó presupuesto${i.entity_name ? ' para '+_actEntityLink(i) : ''}`,
  budget_sent:   (i) => `marcó presupuesto como enviado${i.entity_name ? ' para '+_actEntityLink(i) : ''}`,
  task_created:  (i) => `creó tarea: <b>${esc(i.detail || i.entity_name)}</b>`,
  task_updated:  (i) => `actualizó tarea: <b>${esc(i.entity_name)}</b>${i.detail ? ' ('+esc(i.detail)+')' : ''}`,
  task_deleted:  (i) => `eliminó tarea: <b>${esc(i.entity_name)}</b>`,
  meeting_scheduled: (i) => `agendó reunión${i.entity_name ? ' con '+_actEntityLink(i) : ''}${i.detail ? ': '+esc(i.detail) : ''}`,
  lead_deleted:  (i) => `eliminó lead: <b>${esc(i.entity_name)}</b>`,
  batch_status:  (i) => i.detail || 'actualizó múltiples leads',
};
const _actCrmMap = {sin_contactar:'Sin contactar',contactado:'Contactado',reunion_agendada:'Reunión agendada',reunion_hecha:'Reunión hecha',presupuesto_enviado:'Presupuesto enviado',negociacion:'Negociación',cliente_cerrado:'Cliente cerrado',en_desarrollo:'En desarrollo',finalizado:'Finalizado'};
function _actCrmLabel(s) { return _actCrmMap[s] || s || ''; }
function _actCallLabel(s) { return {contestó:'Contestó',no_contestó:'No contestó',buzón:'Buzón'}[s] || s || ''; }
const _actIcons = {status_change:'🔄',note_updated:'📝',attachment_added:'📎',call_logged:'📞',budget_generated:'💰',budget_sent:'📨',task_created:'✅',task_updated:'✏️',task_deleted:'🗑️',meeting_scheduled:'📅',lead_deleted:'🗑️',batch_status:'🔄'};

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

async function loadActivity() {
  const list = document.getElementById('activity-list');
  if (!list) return;
  list.innerHTML = '<div style="color:#475569;padding:16px 0">Cargando...</div>';
  const sel = document.getElementById('activity-user-filter');
  const user = sel ? sel.value : '';
  try {
    const r = await fetch('/api/activity' + (user ? '?user=' + encodeURIComponent(user) : ''));
    if (!r.ok) { list.innerHTML = '<div style="color:#f87171">Error cargando actividad</div>'; return; }
    const data = await r.json();
    const items = data.items || data;
    // Populate user filter dropdown (preserve selected)
    if (sel && data.users) {
      const cur = sel.value;
      sel.innerHTML = '<option value="">Todos los usuarios</option>'
        + data.users.map(u => '<option value="' + esc(u) + '"' + (u === cur ? ' selected' : '') + '>' + esc(u) + '</option>').join('');
    }
    if (!items.length) { list.innerHTML = '<div style="color:#475569;padding:16px 0">Sin actividad registrada.</div>'; return; }
    list.innerHTML = items.map(i => {
      const fn = _actActionLabels[i.action];
      const desc = fn ? fn(i) : esc(i.action);
      const icon = _actIcons[i.action] || '·';
      const when = timeAgo(i.created_at);
      return '<div class="act-row">'
        + '<div class="act-avatar">' + icon + '</div>'
        + '<div class="act-body">'
        + '<span class="act-user">' + esc(i.user_name) + '</span>'
        + '<span class="act-sep"> · </span>'
        + '<span class="act-desc">' + desc + '</span>'
        + '<div class="act-when">' + when + '</div>'
        + '</div></div>';
    }).join('');
    const d = document.getElementById('activity-date');
    if (d) d.textContent = 'Actualizado: ' + new Date().toLocaleString('es-UY');
  } catch(e) {
    list.innerHTML = '<div style="color:#f87171">Error cargando actividad</div>';
  }
}

// -- Registro de paneles (OCP): cada uno se despacha via showPanel --
registerPanel('cola', loadCola);
registerPanel('seguimientos', loadSeguimientos);
registerPanel('pipeline', loadPipelinePanel);
registerPanel('clientes', loadClientesPanel);
registerPanel('meta', loadMetaPanel);
registerPanel('wa', () => { if (!waLoaded) loadWaLeads(); loadWaTemplates(); });
registerPanel('cal', () => { if (!calLoaded) { calLoaded = true; renderCalendar(); } });
registerPanel('tasks', loadTasks);
registerPanel('metrics', loadMetrics);
registerPanel('activity', loadActivity);
registerPanel('sdr', loadSdr);
