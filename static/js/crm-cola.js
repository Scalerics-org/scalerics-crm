// crm-cola.js — panel 'cola' extraido de crm.js (SRP). Carga despues de crm.js.


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
        <button class="pitch-btn" onclick="openCallModal(${b.id},${escJs(b.name||'')},${escJs(b.phone||'')},'cola')" style="background:#1e293b"><i data-lucide=\"clipboard-list\" class=\"btn-icon\"></i> Resultado</button>
        <button class="delete-btn" onclick="deleteLead(${b.id},${escJs(b.name||'')})" title="Borrar"><i data-lucide=\"trash-2\" class=\"btn-icon\"></i></button>
      </div>
    </div>`).join('');
  _populateNotes(body);
}

// Initial load
loadCola();

// -- Registro de paneles (OCP): cada uno se despacha via showPanel --
registerPanel('cola', loadCola);
