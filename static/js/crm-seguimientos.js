// crm-seguimientos.js — panel 'seguimientos' extraido de crm.js (SRP). Carga despues de crm.js.


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
registerPanel('seguimientos', loadSeguimientos);