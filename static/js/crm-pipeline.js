// crm-pipeline.js — panel 'pipeline' extraido de crm.js (SRP). Carga despues de crm.js.


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
        <button class="delete-btn" onclick="deleteLead(${b.id},${escJs(b.name||'')})" title="Borrar"><i data-lucide=\"trash-2\" class=\"btn-icon\"></i></button>
      </div>
    </div>`; }).join('');
  _populateNotes(body);
}
registerPanel('pipeline', loadPipelinePanel);
