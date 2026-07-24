// crm-tasks.js — panel 'tasks' extraido de crm.js (SRP). Carga despues de crm.js.


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
registerPanel('tasks', loadTasks);