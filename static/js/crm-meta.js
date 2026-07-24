// crm-meta.js — panel 'meta' extraido de crm.js (SRP). Carga despues de crm.js.


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
registerPanel('meta', loadMetaPanel);