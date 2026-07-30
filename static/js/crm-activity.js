// crm-activity.js — panel 'activity' extraido de crm.js (SRP). Carga despues de crm.js.


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
registerPanel('activity', loadActivity);
