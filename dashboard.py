import threading
import webbrowser
from flask import Flask, jsonify, request, render_template_string

from database import get_all_businesses, update_business

app = Flask(__name__)
_db_path: str = ""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Scalerics — Panel de leads</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:#0f1117;color:#e2e8f0;min-height:100vh;display:flex}
.sidebar{width:220px;min-height:100vh;background:#161b27;border-right:1px solid #1e293b;display:flex;flex-direction:column;padding:20px 0;flex-shrink:0;position:fixed;top:0;bottom:0;left:0}
.sidebar-logo{padding:0 20px 22px;border-bottom:1px solid #1e293b;margin-bottom:14px}
.brand{font-size:1.1rem;font-weight:800;color:#fff}
.brand-sub{font-size:.7rem;color:#475569;margin-top:2px}
.nav-item{display:flex;align-items:center;gap:10px;padding:10px 20px;font-size:.85rem;font-weight:500;color:#64748b;cursor:pointer;border-left:3px solid transparent;transition:all .15s}
.nav-item:hover{color:#e2e8f0;background:#1e293b}
.nav-item.active{color:#fff;background:#1e293b;border-left-color:#6366f1}
.sidebar-bottom{margin-top:auto;padding:16px 20px;border-top:1px solid #1e293b}
.run-btn{width:100%;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;font-size:.82rem;font-weight:700;padding:10px;border-radius:8px;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px}
.main{margin-left:220px;padding:28px 32px;flex:1}
.page-header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:24px}
.page-header h1{font-size:1.4rem;font-weight:800;color:#fff}
.page-date{font-size:.78rem;color:#475569;margin-top:3px}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:24px}
.stat-card{background:#161b27;border:1px solid #1e293b;border-radius:12px;padding:18px 20px}
.stat-label{font-size:.68rem;color:#475569;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px}
.stat-val{font-size:1.8rem;font-weight:800;color:#fff}
.stat-val.green{color:#4ade80}
.stat-val.yellow{color:#fbbf24}
.stat-val.blue{color:#60a5fa}
.filters{display:flex;gap:8px;margin-bottom:16px;align-items:center;flex-wrap:wrap}
.filter-btn{padding:6px 14px;border-radius:8px;font-size:.78rem;font-weight:600;border:1px solid #1e293b;background:#161b27;color:#64748b;cursor:pointer;transition:all .15s}
.filter-btn:hover{color:#e2e8f0}
.filter-btn.active{background:#6366f1;border-color:#6366f1;color:#fff}
.filter-select{background:#161b27;border:1px solid #1e293b;border-radius:8px;padding:6px 12px;font-size:.78rem;color:#94a3b8;font-family:'Inter',sans-serif;cursor:pointer;outline:none}
.filter-select option{background:#161b27}
.search-box{margin-left:auto;background:#161b27;border:1px solid #1e293b;border-radius:8px;padding:7px 14px;font-size:.82rem;color:#e2e8f0;width:200px;outline:none;font-family:'Inter',sans-serif}
.search-box::placeholder{color:#334155}
.table-wrap{background:#161b27;border:1px solid #1e293b;border-radius:14px;overflow:hidden}
.table-header{display:grid;grid-template-columns:2fr 1.2fr 1fr .8fr 1.3fr 1.1fr;padding:12px 20px;background:#0f1117;border-bottom:1px solid #1e293b}
.table-header span{font-size:.65rem;font-weight:700;color:#334155;text-transform:uppercase;letter-spacing:1px}
.table-row{display:grid;grid-template-columns:2fr 1.2fr 1fr .8fr 1.3fr 1.1fr;padding:13px 20px;border-bottom:1px solid #1a2234;align-items:center;transition:background .1s}
.table-row:hover{background:#1a2234}
.table-row:last-child{border-bottom:none}
.biz-name{font-weight:600;font-size:.88rem;color:#e2e8f0}
.biz-cat{font-size:.7rem;color:#475569;margin-top:2px}
.phone-val{font-size:.8rem;color:#94a3b8;font-family:monospace}
.no-phone{font-size:.78rem;color:#1e293b}
.city-val{font-size:.82rem;color:#94a3b8}
.rating-val{font-size:.82rem;color:#fbbf24;font-weight:700}
.status-badge{display:inline-flex;align-items:center;gap:5px;padding:3px 9px;border-radius:999px;font-size:.68rem;font-weight:700}
.status-badge.scraped{background:#1e293b;color:#64748b}
.status-badge.no_email{background:#292524;color:#a16207}
.status-badge.demo_generated,.status-badge.demo_deployed{background:#1a2e1e;color:#4ade80}
.status-badge.contacted{background:#1e1b4b;color:#a5b4fc}
.status-badge.error{background:#2a1515;color:#f87171}
.dot{width:5px;height:5px;border-radius:50%;display:inline-block}
.dot.green{background:#4ade80}
.dot.orange{background:#fbbf24}
.dot.gray{background:#334155}
.dot.purple{background:#818cf8}
.dot.red{background:#f87171}
.actions{display:flex;gap:6px;align-items:center}
.demo-link{color:#6366f1;font-size:.78rem;text-decoration:none;font-weight:600;white-space:nowrap}
.demo-link:hover{color:#818cf8}
.no-demo{color:#1e293b;font-size:.75rem}
.contact-btn{background:#1e293b;border:none;color:#64748b;padding:5px 10px;border-radius:6px;font-size:.72rem;cursor:pointer;font-weight:600;font-family:'Inter',sans-serif;white-space:nowrap}
.contact-btn:hover{background:#6366f1;color:#fff}
.contacted-tag{font-size:.72rem;color:#a5b4fc;font-weight:600}
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:1000;align-items:center;justify-content:center}
.modal-overlay.open{display:flex}
.modal{background:#161b27;border:1px solid #1e293b;border-radius:16px;padding:28px;width:420px;max-width:90vw}
.modal h3{font-size:1rem;font-weight:700;color:#fff;margin-bottom:6px}
.modal p{font-size:.82rem;color:#64748b;margin-bottom:18px}
.modal textarea{width:100%;background:#0f1117;border:1px solid #1e293b;border-radius:8px;padding:10px 14px;font-size:.85rem;color:#e2e8f0;font-family:'Inter',sans-serif;resize:vertical;min-height:80px;outline:none;margin-bottom:16px}
.modal textarea::placeholder{color:#334155}
.modal-btns{display:flex;gap:10px;justify-content:flex-end}
.btn-cancel{background:#1e293b;border:none;color:#64748b;padding:9px 18px;border-radius:8px;cursor:pointer;font-size:.82rem;font-weight:600;font-family:'Inter',sans-serif}
.btn-confirm{background:#6366f1;border:none;color:#fff;padding:9px 18px;border-radius:8px;cursor:pointer;font-size:.82rem;font-weight:700;font-family:'Inter',sans-serif}
.cmd-box{background:#0f1117;border:1px solid #1e293b;border-radius:8px;padding:12px 16px;font-family:monospace;font-size:.82rem;color:#4ade80;margin-bottom:16px;word-break:break-all}
.empty-state{padding:40px;text-align:center;color:#334155;font-size:.9rem}
</style>
</head>
<body>
<div class="sidebar">
  <div class="sidebar-logo">
    <div class="brand">⚡ Scalerics</div>
    <div class="brand-sub">Panel de leads</div>
  </div>
  <div class="nav-item active">📋 Leads</div>
  <div class="sidebar-bottom">
    <button class="run-btn" onclick="openPipelineModal()">▶ Correr pipeline</button>
  </div>
</div>

<div class="main">
  <div class="page-header">
    <div>
      <h1 id="page-title">Leads</h1>
      <div class="page-date" id="page-date"></div>
    </div>
  </div>

  <div class="stats">
    <div class="stat-card"><div class="stat-label">Total leads</div><div class="stat-val" id="stat-total">—</div></div>
    <div class="stat-card"><div class="stat-label">Demos listas</div><div class="stat-val green" id="stat-demos">—</div></div>
    <div class="stat-card"><div class="stat-label">Con teléfono</div><div class="stat-val yellow" id="stat-phones">—</div></div>
    <div class="stat-card"><div class="stat-label">Contactados</div><div class="stat-val blue" id="stat-contacted">—</div></div>
  </div>

  <div class="filters">
    <button class="filter-btn active" data-status="">Todos</button>
    <button class="filter-btn" data-status="demo_generated">Con demo</button>
    <button class="filter-btn" data-status="demo_deployed">Deployados</button>
    <button class="filter-btn" data-status="contacted">Contactados</button>
    <select class="filter-select" id="category-filter">
      <option value="">Todos los rubros</option>
    </select>
    <input class="search-box" id="search-input" placeholder="🔍 Buscar negocio...">
  </div>

  <div class="table-wrap">
    <div class="table-header">
      <span>Negocio</span><span>Teléfono</span><span>Ciudad</span><span>Rating</span><span>Estado</span><span>Acciones</span>
    </div>
    <div id="table-body"></div>
  </div>
</div>

<!-- Modal contactar -->
<div class="modal-overlay" id="contact-modal">
  <div class="modal">
    <h3 id="modal-title">Marcar como contactado</h3>
    <p id="modal-sub">Podés agregar una nota opcional sobre el contacto</p>
    <textarea id="modal-note" placeholder="Ej: Llamé el martes, quedó en pensar, volver a llamar en una semana..."></textarea>
    <div class="modal-btns">
      <button class="btn-cancel" onclick="closeModal()">Cancelar</button>
      <button class="btn-confirm" onclick="confirmContact()">✓ Confirmar contacto</button>
    </div>
  </div>
</div>

<!-- Modal pipeline -->
<div class="modal-overlay" id="pipeline-modal">
  <div class="modal">
    <h3>Correr el pipeline</h3>
    <p>Abrí una terminal en la carpeta del proyecto y ejecutá:</p>
    <div class="cmd-box">python main.py run-all --query "negocio ciudad" --max 50</div>
    <p style="margin-bottom:12px">O por pasos:</p>
    <div class="cmd-box" style="margin-bottom:8px">python main.py scrape --query "restaurante Montevideo" --max 50</div>
    <div class="cmd-box" style="margin-bottom:8px">python main.py find-emails</div>
    <div class="cmd-box" style="margin-bottom:8px">python main.py generate-demos</div>
    <div class="cmd-box" style="margin-bottom:8px">python main.py deploy</div>
    <div class="cmd-box">python main.py send-emails</div>
    <div class="modal-btns" style="margin-top:16px">
      <button class="btn-confirm" onclick="document.getElementById('pipeline-modal').classList.remove('open')">Cerrar</button>
    </div>
  </div>
</div>

<script>
let currentStatus = '';
let currentCategory = '';
let currentSearch = '';
let contactingId = null;

function statusDot(s) {
  if (s === 'contacted') return '<span class="dot purple"></span>';
  if (s === 'demo_generated' || s === 'demo_deployed') return '<span class="dot green"></span>';
  if (s === 'error') return '<span class="dot red"></span>';
  if (s === 'no_email') return '<span class="dot orange"></span>';
  return '<span class="dot gray"></span>';
}
function statusLabel(s) {
  const map = {scraped:'Scraped',no_email:'Sin email',email_found:'Email encontrado',
    demo_generated:'Demo lista',demo_deployed:'Deployado',contacted:'Contactado',error:'Error'};
  return map[s] || s;
}

async function loadStats() {
  const r = await fetch('/api/stats');
  const d = await r.json();
  document.getElementById('stat-total').textContent = d.total;
  document.getElementById('stat-demos').textContent = d.demo_ready;
  document.getElementById('stat-phones').textContent = d.with_phone;
  document.getElementById('stat-contacted').textContent = d.contacted;
  const sel = document.getElementById('category-filter');
  const prev = sel.value;
  while (sel.options.length > 1) sel.remove(1);
  (d.categories || []).forEach(c => {
    const o = new Option(c, c);
    sel.add(o);
  });
  if (prev) sel.value = prev;
  document.getElementById('page-date').textContent = 'Actualizado: ' + new Date().toLocaleString('es-UY');
}

async function loadLeads() {
  const params = new URLSearchParams();
  if (currentStatus) params.set('status', currentStatus);
  if (currentCategory) params.set('category', currentCategory);
  if (currentSearch) params.set('search', currentSearch);
  const r = await fetch('/api/leads?' + params);
  const leads = await r.json();
  const body = document.getElementById('table-body');
  if (!leads.length) { body.innerHTML = '<div class="empty-state">No hay leads con estos filtros</div>'; return; }
  body.innerHTML = leads.map(b => `
    <div class="table-row">
      <div><div class="biz-name">${esc(b.name||'')}</div><div class="biz-cat">${esc(b.category||'')}</div></div>
      <div>${b.phone ? `<span class="phone-val">${esc(b.phone)}</span>` : '<span class="no-phone">— sin teléfono</span>'}</div>
      <div class="city-val">${esc(b.city||'')}</div>
      <div>${b.rating ? `<span class="rating-val">★ ${b.rating}</span>` : '<span style="color:#1e293b">—</span>'}</div>
      <div><span class="status-badge ${b.status||''}">${statusDot(b.status)} ${statusLabel(b.status||'')}</span></div>
      <div class="actions">
        ${b.demo_url ? `<a class="demo-link" href="${b.demo_url}" target="_blank">Ver demo ↗</a>` : '<span class="no-demo">Sin demo</span>'}
        ${b.status !== 'contacted' ? `<button class="contact-btn" onclick="openContact(${b.id}, '${esc(b.name||'')}')">✓ Contactar</button>` : '<span class="contacted-tag">✓ Listo</span>'}
      </div>
    </div>`).join('');
}

function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

function openContact(id, name) {
  contactingId = id;
  document.getElementById('modal-title').textContent = `Contactar: ${name}`;
  document.getElementById('modal-note').value = '';
  document.getElementById('contact-modal').classList.add('open');
}
function closeModal() { document.getElementById('contact-modal').classList.remove('open'); contactingId = null; }
async function confirmContact() {
  if (!contactingId) return;
  const note = document.getElementById('modal-note').value;
  await fetch(`/api/leads/${contactingId}/contact`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({note})});
  closeModal();
  loadStats();
  loadLeads();
}
function openPipelineModal() { document.getElementById('pipeline-modal').classList.add('open'); }

document.querySelectorAll('.filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentStatus = btn.dataset.status;
    loadLeads();
  });
});
document.getElementById('category-filter').addEventListener('change', e => { currentCategory = e.target.value; loadLeads(); });
let searchTimeout;
document.getElementById('search-input').addEventListener('input', e => {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(() => { currentSearch = e.target.value; loadLeads(); }, 300);
});
document.getElementById('contact-modal').addEventListener('click', e => { if (e.target === e.currentTarget) closeModal(); });
document.getElementById('pipeline-modal').addEventListener('click', e => { if (e.target === e.currentTarget) e.currentTarget.classList.remove('open'); });

loadStats();
loadLeads();
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/leads")
def api_leads():
    businesses = get_all_businesses(_db_path)
    status = request.args.get("status")
    category = request.args.get("category")
    search = (request.args.get("search") or "").lower()
    if status:
        businesses = [b for b in businesses if b.get("status") == status]
    if category:
        businesses = [b for b in businesses if (b.get("category") or "").lower() == category.lower()]
    if search:
        businesses = [b for b in businesses if search in (b.get("name") or "").lower()]
    return jsonify(businesses)


@app.route("/api/leads/<int:biz_id>/contact", methods=["POST"])
def api_contact(biz_id):
    data = request.get_json() or {}
    note = data.get("note", "")
    update_business(_db_path, biz_id, status="contacted", notes=note)
    return jsonify({"ok": True})


@app.route("/api/stats")
def api_stats():
    businesses = get_all_businesses(_db_path)
    categories = sorted({b.get("category") or "" for b in businesses if b.get("category")})
    return jsonify({
        "total": len(businesses),
        "demo_ready": sum(1 for b in businesses if b.get("status") in ("demo_generated", "demo_deployed")),
        "with_phone": sum(1 for b in businesses if b.get("phone")),
        "contacted": sum(1 for b in businesses if b.get("status") == "contacted"),
        "categories": categories,
    })


def run(db_path: str) -> None:
    global _db_path
    _db_path = db_path
    threading.Timer(1.2, lambda: webbrowser.open("http://localhost:5000")).start()
    app.run(port=5000, debug=False, use_reloader=False)
