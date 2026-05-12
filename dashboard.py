import os
import threading
import webbrowser

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template_string, request, session, url_for

from database import init_db, seed_pitch_templates
from routes.leads import leads_bp
from routes.demos import demos_bp
from routes.calendar import calendar_bp
from routes.wa import wa_bp
from routes.pipeline import pipeline_bp
from routes.tasks import tasks_bp
from routes.budgets import budgets_bp
from services.demo_service import demo_job_handler
from services.job_service import init_worker

load_dotenv()

_pipeline_status: dict = {"running": False, "log": [], "error": None}
_pipeline_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Login page
# ---------------------------------------------------------------------------

LOGIN_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Scalerics — Acceso</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:#0a0f1a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#111827;border:1px solid #1e293b;border-radius:16px;padding:40px 36px;width:360px;max-width:92vw}
.logo-wrap{margin-bottom:28px;display:flex;flex-direction:column;align-items:flex-start;gap:10px}
.logo-wrap img{height:32px;object-fit:contain}
.logo-sub{font-size:.75rem;color:#475569}
label{font-size:.72rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.8px;display:block;margin-bottom:7px}
input[type=password]{width:100%;background:#0a0f1a;border:1px solid #1e293b;border-radius:8px;padding:11px 14px;font-size:.92rem;color:#e2e8f0;font-family:'Inter',sans-serif;outline:none;margin-bottom:16px}
input[type=password]:focus{border-color:#0088cc}
button{width:100%;background:linear-gradient(135deg,#0088cc,#3db648);color:#fff;font-size:.88rem;font-weight:700;padding:12px;border-radius:8px;border:none;cursor:pointer;font-family:'Inter',sans-serif}
button:hover{opacity:.9}
.error{background:#2a1515;border:1px solid #7f1d1d;border-radius:8px;padding:10px 14px;font-size:.8rem;color:#f87171;margin-bottom:16px}
</style>
</head>
<body>
<div class="card">
  <div class="logo-wrap">
    <img src="/static/logo.png" alt="Scalerics">
    <span class="logo-sub">CRM · Panel interno</span>
  </div>
  {% if error %}
  <div class="error">{{ error }}</div>
  {% endif %}
  <form method="POST">
    <label>Contraseña</label>
    <input type="password" name="password" autofocus placeholder="Ingresá la contraseña del equipo">
    <button type="submit">Entrar</button>
  </form>
</div>
</body>
</html>"""

# ---------------------------------------------------------------------------
# Main dashboard HTML
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Scalerics — CRM</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:#0a0f1a;color:#e2e8f0;min-height:100vh;display:flex}
.sidebar{width:220px;min-height:100vh;background:#111827;border-right:1px solid #1a2d3d;display:flex;flex-direction:column;padding:20px 0;flex-shrink:0;position:fixed;top:0;bottom:0;left:0;z-index:200;transition:transform .25s ease}
.sidebar-logo{padding:12px 20px 18px;border-bottom:1px solid #1a2d3d;margin-bottom:14px}
.sidebar-logo img{height:28px;object-fit:contain;max-width:160px}
.nav-item{display:flex;align-items:center;gap:10px;padding:10px 20px;font-size:.85rem;font-weight:500;color:#64748b;cursor:pointer;border-left:3px solid transparent;transition:all .15s}
.nav-item:hover{color:#e2e8f0;background:#1a2d3d}
.nav-item.active{color:#fff;background:#1a2d3d;border-left-color:#0088cc}
.sidebar-bottom{margin-top:auto;padding:16px 20px;border-top:1px solid #1a2d3d;display:flex;flex-direction:column;gap:8px}
.run-btn{width:100%;background:linear-gradient(135deg,#0088cc,#3db648);color:#fff;font-size:.82rem;font-weight:700;padding:10px;border-radius:8px;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px}
.logout-btn{width:100%;background:transparent;border:1px solid #1a2d3d;color:#475569;font-size:.78rem;font-weight:500;padding:8px;border-radius:8px;cursor:pointer;font-family:'Inter',sans-serif}
.logout-btn:hover{color:#e2e8f0;border-color:#334155}
.sidebar-backdrop{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:199}
.topbar{display:none;align-items:center;gap:12px;padding:12px 16px;background:#111827;border-bottom:1px solid #1a2d3d;position:sticky;top:0;z-index:100}
.topbar img{height:24px;object-fit:contain}
.hamburger{background:none;border:none;color:#94a3b8;font-size:1.3rem;cursor:pointer;padding:4px 6px;line-height:1;flex-shrink:0}
.main{margin-left:220px;padding:28px 32px;flex:1;min-width:0}
.panel{display:none}
.panel.active{display:block}
@media(max-width:768px){
  .topbar{position:fixed;top:0;left:0;right:0;display:flex}
  .sidebar{transform:translateX(-220px)}
  .sidebar.open{transform:translateX(0)}
  .sidebar-backdrop.open{display:block}
  .main{margin-left:0;padding:14px;padding-top:62px}
  .page-header h1{font-size:1.1rem}
  .stats{grid-template-columns:1fr 1fr}
  .filters{gap:6px}
  .search-box{width:100%;margin-left:0}
  .table-wrap{overflow-x:auto}
  .table-header span:nth-child(3),.table-row>div:nth-child(3){display:none}
  .table-header,.table-row{grid-template-columns:2fr 1.1fr 1.4fr}
  .wa-container{grid-template-columns:1fr;height:auto}
  .wa-list{max-height:240px;border-right:none;border-bottom:1px solid #1e293b}
  .wa-chat{height:calc(100vh - 380px);min-height:320px}
  .cal-header{flex-wrap:wrap;gap:8px}
  .cal-header h1{flex:1;font-size:1.1rem}
  .cal-grid-header{font-size:.55rem;padding:6px 2px}
  .cal-cell{min-height:60px;padding:4px}
  .cal-event-chip{font-size:.55rem}
  .modal{width:95vw!important;max-width:95vw!important}
  .modal-row{grid-template-columns:1fr}
  .pipeline-input-row{flex-direction:column}
  .max-input{width:100%}
}
@media(max-width:480px){
  .stats{grid-template-columns:1fr}
  .pitch-btn,.mail-btn,.contact-btn{padding:4px 7px;font-size:.67rem}
}

/* ---- Leads panel ---- */
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
.filter-btn.active{background:#0088cc;border-color:#0088cc;color:#fff}
.filter-select{background:#161b27;border:1px solid #1e293b;border-radius:8px;padding:6px 12px;font-size:.78rem;color:#94a3b8;font-family:'Inter',sans-serif;cursor:pointer;outline:none}
.filter-select option{background:#161b27}
.search-box{margin-left:auto;background:#161b27;border:1px solid #1e293b;border-radius:8px;padding:7px 14px;font-size:.82rem;color:#e2e8f0;width:200px;outline:none;font-family:'Inter',sans-serif}
.search-box::placeholder{color:#334155}
.table-wrap{background:#161b27;border:1px solid #1e293b;border-radius:14px;overflow:hidden}
.table-header{display:grid;grid-template-columns:2fr 1.2fr 1.8fr 1.5fr;padding:12px 20px;background:#0f1117;border-bottom:1px solid #1e293b}
.table-header span{font-size:.65rem;font-weight:700;color:#334155;text-transform:uppercase;letter-spacing:1px}
.table-row{display:grid;grid-template-columns:2fr 1.2fr 1.8fr 1.5fr;padding:13px 20px;border-bottom:1px solid #1a2234;align-items:center;transition:background .1s}
.table-row:hover{background:#1a2234}
.table-row:last-child{border-bottom:none}
.biz-name{font-weight:600;font-size:.88rem;color:#e2e8f0}
.biz-sub{font-size:.7rem;color:#475569;margin-top:2px}
.phone-val{font-size:.8rem;color:#4ade80;font-family:monospace;text-decoration:none}
.phone-val:hover{color:#86efac;text-decoration:underline}
.no-val{font-size:.75rem;color:#1e293b}
.email-val{font-size:.76rem;color:#94a3b8;word-break:break-all}
.dot{width:5px;height:5px;border-radius:50%;display:inline-block}
.dot.green{background:#4ade80}.dot.orange{background:#fbbf24}.dot.gray{background:#334155}.dot.purple{background:#818cf8}.dot.red{background:#f87171}
.actions{display:flex;gap:5px;align-items:center;flex-wrap:wrap}
.pitch-btn{background:#1a2e1e;border:none;color:#4ade80;padding:5px 9px;border-radius:6px;font-size:.7rem;cursor:pointer;font-weight:600;font-family:'Inter',sans-serif;white-space:nowrap}
.pitch-btn:hover{background:#14532d}
.mail-btn{background:#1e1b4b;border:none;color:#a5b4fc;padding:5px 9px;border-radius:6px;font-size:.7rem;cursor:pointer;font-weight:600;font-family:'Inter',sans-serif;white-space:nowrap}
.mail-btn:hover{background:#312e81}
.contact-btn{background:#1e293b;border:none;color:#64748b;padding:5px 9px;border-radius:6px;font-size:.7rem;cursor:pointer;font-weight:600;font-family:'Inter',sans-serif;white-space:nowrap}
.contact-btn:hover{background:#6366f1;color:#fff}
.contacted-tag{font-size:.72rem;color:#a5b4fc;font-weight:600}
.no-pitch{font-size:.72rem;color:#1e293b}
.empty-state{padding:40px;text-align:center;color:#334155;font-size:.9rem}
.row-sin-contactar{background:#161b27}
.row-contactado{background:#0d1f33}
.row-agendo{background:#1f1a0d}
.row-firmo{background:#0d1f12}
.row-sin-contactar:hover{background:#1a2234}
.row-contactado:hover{background:#112438}
.row-agendo:hover{background:#251f0f}
.row-firmo:hover{background:#0f2416}
.status-sel{background:#0f1117;border:1px solid #1e293b;border-radius:6px;padding:4px 6px;font-size:.7rem;color:#94a3b8;font-family:'Inter',sans-serif;cursor:pointer;outline:none;max-width:110px}
.status-sel:focus{border-color:#0088cc}
.delete-btn{background:#2a1515;border:none;color:#f87171;padding:5px 8px;border-radius:6px;font-size:.7rem;cursor:pointer;font-family:'Inter',sans-serif}
.delete-btn:hover{background:#7f1d1d;color:#fff}
.pipeline-input-row{display:flex;gap:10px;margin-bottom:12px}
.pipeline-input{flex:1;background:#0f1117;border:1px solid #1e293b;border-radius:8px;padding:10px 14px;font-size:.88rem;color:#e2e8f0;font-family:'Inter',sans-serif;outline:none}
.pipeline-input::placeholder{color:#334155}
.pipeline-input:focus{border-color:#0088cc}
.max-input{width:76px;background:#0f1117;border:1px solid #1e293b;border-radius:8px;padding:10px 10px;font-size:.88rem;color:#e2e8f0;font-family:'Inter',sans-serif;outline:none;text-align:center}
.pipeline-log{background:#0a0e18;border:1px solid #1e293b;border-radius:8px;padding:12px 14px;font-family:monospace;font-size:.76rem;height:210px;overflow-y:auto;white-space:pre-wrap;word-break:break-all;margin-bottom:14px}
.log-line{color:#64748b;line-height:1.6}
.log-line.ok{color:#4ade80}.log-line.err{color:#f87171}.log-line.warn{color:#fbbf24}
.log-empty{color:#1e293b;text-align:center;padding-top:80px;font-family:'Inter',sans-serif}
.status-pill{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;font-size:.72rem;font-weight:700}
.status-pill.idle{background:#1e293b;color:#64748b}
.status-pill.running{background:#1a2e1e;color:#4ade80}
.status-pill.done{background:#1a2e1e;color:#4ade80}
.status-pill.error{background:#2a1515;color:#f87171}
.spinner{width:8px;height:8px;border:2px solid #4ade80;border-top-color:transparent;border-radius:50%;animation:spin .6s linear infinite;display:inline-block}
@keyframes spin{to{transform:rotate(360deg)}}

/* ---- WhatsApp panel ---- */
.wa-container{display:grid;grid-template-columns:280px 1fr;gap:0;background:#161b27;border:1px solid #1e293b;border-radius:14px;overflow:hidden;height:calc(100vh - 120px);min-height:500px}
.wa-list{border-right:1px solid #1e293b;overflow-y:auto;display:flex;flex-direction:column}
.wa-list-header{padding:14px 18px;font-size:.78rem;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.8px;border-bottom:1px solid #1e293b;flex-shrink:0}
.wa-lead-item{padding:12px 18px;border-bottom:1px solid #1a2234;cursor:pointer;transition:background .1s;flex-shrink:0}
.wa-lead-item:hover{background:#1a2234}
.wa-lead-item.selected{background:#1e293b;border-left:3px solid #6366f1}
.wa-lead-name{font-size:.85rem;font-weight:600;color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wa-lead-meta{display:flex;align-items:center;gap:7px;margin-top:4px}
.wa-state-badge{font-size:.62rem;font-weight:700;padding:2px 7px;border-radius:999px}
.wa-state-NEW{background:#1e293b;color:#94a3b8}
.wa-state-QUAL{background:#292116;color:#fbbf24}
.wa-state-SCORED{background:#172036;color:#60a5fa}
.wa-state-SCHEDULED{background:#1a2e1e;color:#4ade80}
.wa-state-NURTURE{background:#1e1b4b;color:#a78bfa}
.wa-state-DISQUALIFIED{background:#2a1515;color:#f87171}
.wa-lead-time{font-size:.65rem;color:#334155}
.wa-chat{display:flex;flex-direction:column;background:#0f1117;overflow:hidden;min-height:0}
.wa-chat-header{padding:10px 20px;border-bottom:1px solid #1e293b;flex-shrink:0;background:#161b27;display:flex;align-items:center;justify-content:space-between;gap:12px}
.wa-chat-info{flex:1;min-width:0}
.wa-chat-name{font-size:.9rem;font-weight:700;color:#fff}
.wa-chat-phone{font-size:.72rem;color:#475569;margin-top:2px}
.wa-release-btn{background:#1a2e1e;border:none;color:#4ade80;font-size:.72rem;font-weight:700;padding:5px 10px;border-radius:6px;cursor:pointer;font-family:'Inter',sans-serif;white-space:nowrap;flex-shrink:0}
.wa-release-btn:hover{background:#14532d}
.wa-human-badge{font-size:.68rem;font-weight:700;color:#fbbf24;background:#292116;padding:3px 8px;border-radius:999px;flex-shrink:0}
.wa-messages{flex:1;overflow-y:auto;padding:16px 20px;display:flex;flex-direction:column;gap:8px;min-height:0}
.wa-bubble{max-width:68%;padding:9px 13px;border-radius:12px;font-size:.84rem;line-height:1.5;white-space:pre-wrap;word-break:break-word}
.wa-bubble-in{background:#1e293b;color:#e2e8f0;align-self:flex-start;border-bottom-left-radius:3px}
.wa-bubble-out{background:#0a3a5c;color:#e2e8f0;align-self:flex-end;border-bottom-right-radius:3px}
.wa-bubble-time{font-size:.62rem;color:#475569;margin-top:4px}
.wa-input-row{padding:12px 16px;border-top:1px solid #1e293b;display:flex;gap:10px;align-items:center;flex-shrink:0;background:#161b27}
.wa-input{flex:1;background:#0f1117;border:1px solid #1e293b;border-radius:8px;padding:10px 14px;font-size:.85rem;color:#e2e8f0;font-family:'Inter',sans-serif;outline:none}
.wa-input:focus{border-color:#6366f1}
.wa-send-btn{background:#0088cc;border:none;color:#fff;padding:10px 18px;border-radius:8px;font-size:.82rem;font-weight:700;cursor:pointer;font-family:'Inter',sans-serif;white-space:nowrap}
.wa-send-btn:hover{background:#0077b3}
.wa-empty{flex:1;display:flex;align-items:center;justify-content:center;color:#334155;font-size:.88rem}
.wa-no-leads{padding:32px;text-align:center;color:#334155;font-size:.85rem}
.wa-error-banner{padding:12px 18px;background:#2a1515;border:1px solid #7f1d1d;border-radius:8px;color:#f87171;font-size:.82rem;margin:16px}

/* ---- Calendar panel ---- */
.cal-header{display:flex;align-items:center;gap:12px;margin-bottom:20px}
.cal-header h1{font-size:1.4rem;font-weight:800;color:#fff;flex:1}
.cal-nav-btn{background:#1e293b;border:none;color:#94a3b8;padding:8px 14px;border-radius:8px;cursor:pointer;font-size:.85rem;font-family:'Inter',sans-serif}
.cal-nav-btn:hover{background:#334155;color:#fff}
.cal-new-btn{background:linear-gradient(135deg,#0088cc,#3db648);border:none;color:#fff;padding:9px 16px;border-radius:8px;cursor:pointer;font-size:.82rem;font-weight:700;font-family:'Inter',sans-serif}
.cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:1px;background:#1e293b;border-radius:12px;overflow:hidden}
.cal-grid-header{background:#0f1117;padding:8px 4px;text-align:center;font-size:.62rem;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.5px}
.cal-cell{background:#161b27;min-height:88px;padding:6px 8px}
.cal-cell.other-month{background:#0d1117}
.cal-cell.today{background:#0d1f33}
.cal-cell-day{font-size:.72rem;font-weight:700;color:#475569;margin-bottom:4px;width:20px;height:20px;display:flex;align-items:center;justify-content:center;border-radius:50%}
.cal-cell.today .cal-cell-day{color:#fff;background:#0088cc}
.cal-event-chip{font-size:.62rem;padding:2px 5px;border-radius:3px;margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.5;cursor:default}
.cal-event-chip.regular{background:#172036;color:#60a5fa}
.cal-event-chip.meet{background:#1a2e1e;color:#4ade80}
.cal-demo-btn{display:block;width:100%;text-align:left;background:rgba(6,182,212,.12);border:1px solid rgba(6,182,212,.25);color:#06B6D4;border-radius:3px;padding:1px 5px;font-size:.5rem;font-weight:700;letter-spacing:.03em;cursor:pointer;margin-top:2px;line-height:1.6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cal-demo-btn:hover{background:rgba(6,182,212,.25)}
.cal-loading{padding:40px;text-align:center;color:#334155;font-size:.9rem}
.cal-error{padding:16px;background:#2a1515;border:1px solid #7f1d1d;border-radius:8px;color:#f87171;font-size:.82rem;margin-bottom:16px}

/* ---- Shared modals ---- */
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:1000;align-items:center;justify-content:center}
.modal-overlay.open{display:flex}
.modal{background:#161b27;border:1px solid #1e293b;border-radius:16px;padding:28px;width:420px;max-width:90vw}
.modal h3{font-size:1rem;font-weight:700;color:#fff;margin-bottom:6px}
.modal p{font-size:.82rem;color:#64748b;margin-bottom:18px}
.modal textarea{width:100%;background:#0f1117;border:1px solid #1e293b;border-radius:8px;padding:10px 14px;font-size:.85rem;color:#e2e8f0;font-family:'Inter',sans-serif;resize:vertical;min-height:80px;outline:none;margin-bottom:16px}
.modal textarea::placeholder{color:#334155}
.modal input[type=text],.modal input[type=date],.modal input[type=time],.modal input[type=number],.modal input[type=email]{width:100%;background:#0f1117;border:1px solid #1e293b;border-radius:8px;padding:10px 14px;font-size:.85rem;color:#e2e8f0;font-family:'Inter',sans-serif;outline:none;margin-bottom:12px}
.modal input::placeholder{color:#334155}
.modal input:focus,.modal textarea:focus{border-color:#6366f1}
.modal-label{font-size:.7rem;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.7px;display:block;margin-bottom:5px}
.modal-row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.modal-btns{display:flex;gap:10px;justify-content:flex-end}
.btn-cancel{background:#1e293b;border:none;color:#64748b;padding:9px 18px;border-radius:8px;cursor:pointer;font-size:.82rem;font-weight:600;font-family:'Inter',sans-serif}
.btn-confirm{background:#0088cc;border:none;color:#fff;padding:9px 18px;border-radius:8px;cursor:pointer;font-size:.82rem;font-weight:700;font-family:'Inter',sans-serif;display:inline-flex;align-items:center;gap:6px}
.btn-confirm:disabled{opacity:.5;cursor:not-allowed}

/* ── Client Panel ─────────────────────────────────────────────────────────── */
.cp-backdrop{display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:400}
.cp-backdrop.open{display:block}
.client-panel{position:fixed;top:0;right:0;bottom:0;width:580px;max-width:100vw;background:#111827;border-left:1px solid #1e293b;z-index:401;display:flex;flex-direction:column;transform:translateX(100%);transition:transform .25s ease}
.client-panel.open{transform:translateX(0)}
.cp-header{padding:18px 20px 0;border-bottom:1px solid #1e293b;flex-shrink:0}
.cp-title{font-size:1.1rem;font-weight:700;color:#f1f5f9;margin:0 0 4px}
.cp-sub{font-size:.78rem;color:#475569;margin-bottom:12px;display:flex;align-items:center;gap:8px}
.cp-status-sel{background:#0a0f1a;border:1px solid #1e293b;color:#94a3b8;border-radius:6px;padding:3px 8px;font-size:.75rem;font-family:'Inter',sans-serif;cursor:pointer}
.cp-tabs{display:flex;gap:0;border-bottom:1px solid #1e293b;margin-top:4px}
.cp-tab{padding:10px 16px;font-size:.8rem;font-weight:600;color:#475569;cursor:pointer;border-bottom:2px solid transparent;transition:all .15s;white-space:nowrap}
.cp-tab.active{color:#fff;border-bottom-color:#0088cc}
.cp-tab:hover:not(.active){color:#94a3b8}
.cp-body{flex:1;overflow-y:auto;padding:20px}
.cp-close{position:absolute;top:14px;right:16px;background:none;border:none;color:#475569;font-size:1.3rem;cursor:pointer;line-height:1;padding:4px 6px}
.cp-close:hover{color:#e2e8f0}
.cp-section{margin-bottom:22px}
.cp-section-title{font-size:.7rem;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.7px;margin-bottom:10px}
.cp-field{display:flex;align-items:flex-start;gap:8px;margin-bottom:8px;font-size:.85rem}
.cp-field-label{color:#64748b;min-width:80px;flex-shrink:0}
.cp-field-val{color:#e2e8f0;word-break:break-word}
.cp-meeting-card{background:#0a0f1a;border:1px solid #1e293b;border-radius:10px;padding:14px;margin-bottom:10px}
.cp-meeting-title{font-size:.88rem;font-weight:600;color:#f1f5f9;margin-bottom:4px}
.cp-meeting-meta{font-size:.75rem;color:#475569;margin-bottom:10px}
.cp-meeting-link{font-size:.75rem;color:#0088cc;text-decoration:none;display:inline-block;margin-bottom:10px}
.cp-meeting-link:hover{text-decoration:underline}
.cp-summary-box{background:#1e293b;border-radius:8px;padding:12px;margin-top:10px;font-size:.8rem;color:#94a3b8;line-height:1.6;white-space:pre-wrap}
.cp-summary-label{font-size:.68rem;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.6px;margin-bottom:4px}
.cp-transcript-area{width:100%;background:#0a0f1a;border:1px solid #1e293b;border-radius:8px;color:#e2e8f0;font-size:.8rem;padding:10px;font-family:'Inter',sans-serif;resize:vertical;min-height:100px;margin-bottom:8px}
.cp-transcript-area:focus{outline:none;border-color:#0088cc}
.budget-table{width:100%;border-collapse:collapse;font-size:.82rem;margin:12px 0}
.budget-table th{color:#475569;font-size:.68rem;text-transform:uppercase;letter-spacing:.6px;padding:6px 8px;text-align:left;border-bottom:1px solid #1e293b}
.budget-table td{padding:8px;border-bottom:1px solid #1e293b15;color:#e2e8f0;vertical-align:top}
.budget-table td input{background:transparent;border:none;color:#e2e8f0;font-family:'Inter',sans-serif;font-size:.82rem;width:100%;outline:none}
.budget-table td input:focus{background:#0a0f1a;border-radius:4px;padding:2px 4px}
.budget-total-row td{font-weight:700;color:#f1f5f9;border-top:1px solid #1e293b;border-bottom:none;padding-top:12px}
.cp-btn{display:inline-flex;align-items:center;gap:6px;padding:8px 14px;border-radius:8px;border:none;font-size:.8rem;font-weight:600;cursor:pointer;font-family:'Inter',sans-serif;transition:opacity .15s}
.cp-btn:hover{opacity:.85}
.cp-btn:disabled{opacity:.4;cursor:not-allowed}
.cp-btn-primary{background:#0088cc;color:#fff}
.cp-btn-success{background:#16a34a;color:#fff}
.cp-btn-ghost{background:#1e293b;color:#94a3b8}
.cp-badge{display:inline-block;padding:2px 8px;border-radius:99px;font-size:.7rem;font-weight:600}
.cp-badge-draft{background:#1e293b;color:#94a3b8}
.cp-badge-sent{background:#064e3b;color:#34d399}
.cp-badge-pending{background:#1e3a5f;color:#60a5fa}
.cp-badge-completed{background:#14532d;color:#4ade80}
.cp-badge-generating{background:#451a03;color:#fb923c}
.cp-badge-failed{background:#450a0a;color:#f87171}
.cp-wa-msg{padding:8px 12px;border-radius:10px;font-size:.8rem;margin-bottom:6px;max-width:88%;line-height:1.5}
.cp-wa-msg.out{background:#1e3a5f;color:#bfdbfe;align-self:flex-end;margin-left:auto}
.cp-wa-msg.in{background:#1e293b;color:#e2e8f0}
.cp-wa-msgs{display:flex;flex-direction:column;gap:2px;max-height:260px;overflow-y:auto;padding:8px;background:#0a0f1a;border-radius:8px;border:1px solid #1e293b}
.cp-spinner{display:inline-block;width:14px;height:14px;border:2px solid #334155;border-top-color:#0088cc;border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.cp-req-area{width:100%;background:#0a0f1a;border:1px solid #1e293b;border-radius:8px;color:#e2e8f0;font-size:.82rem;padding:10px;font-family:'Inter',sans-serif;resize:vertical;min-height:70px;margin-bottom:8px}
.cp-req-area:focus{outline:none;border-color:#0088cc}

/* ── Scrollbars ───────────────────────────────────────────────────────────── */
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:#1e293b;border-radius:99px}
::-webkit-scrollbar-thumb:hover{background:#334155}
/* ── Kanban ───────────────────────────────────────────────────────────────── */
.kanban-board{display:flex;gap:14px;overflow-x:auto;padding-bottom:20px;align-items:flex-start;min-height:calc(100vh - 180px)}
.kanban-col{background:#111827;border:1px solid #1e293b;border-radius:12px;min-width:220px;width:220px;flex-shrink:0;display:flex;flex-direction:column;max-height:calc(100vh - 200px)}
.kanban-col-header{padding:12px 14px 10px;border-bottom:1px solid #1e293b;display:flex;align-items:center;justify-content:space-between;flex-shrink:0}
.kanban-col-title{font-size:.78rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.6px}
.kanban-count{background:#1e293b;color:#475569;font-size:.68rem;font-weight:700;padding:2px 7px;border-radius:99px}
.kanban-cards{padding:8px;overflow-y:auto;flex:1;display:flex;flex-direction:column;gap:8px}
.kanban-col.drag-over{background:#1a2d3d;border-color:#0088cc}
.kanban-card{background:#0a0f1a;border:1px solid #1e293b;border-radius:10px;padding:12px;cursor:pointer;transition:border-color .15s,transform .1s}
.kanban-card:hover{border-color:#334155;transform:translateY(-1px)}
.kanban-card.dragging{opacity:.4;transform:rotate(1deg)}
.kanban-card-name{font-size:.85rem;font-weight:600;color:#f1f5f9;margin-bottom:4px}
.kanban-card-meta{font-size:.72rem;color:#475569;margin-bottom:6px}
.kanban-card-phone{font-size:.72rem;color:#0088cc}
.kanban-card-rating{font-size:.68rem;color:#fbbf24}
.kanban-empty{color:#334155;font-size:.78rem;text-align:center;padding:20px 10px}

/* ── Tasks panel ──────────────────────────────────────────────────────────── */
.tasks-filters{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
.task-row{background:#111827;border:1px solid #1e293b;border-radius:10px;padding:14px 16px;margin-bottom:8px;display:flex;align-items:flex-start;gap:12px;transition:border-color .15s}
.task-row:hover{border-color:#334155}
.task-check{width:18px;height:18px;border:2px solid #334155;border-radius:4px;cursor:pointer;flex-shrink:0;margin-top:2px;display:flex;align-items:center;justify-content:center;transition:all .15s}
.task-check.done{background:#16a34a;border-color:#16a34a;color:#fff;font-size:.7rem}
.task-check:hover:not(.done){border-color:#0088cc}
.task-body{flex:1;min-width:0}
.task-title{font-size:.88rem;font-weight:600;color:#f1f5f9;margin-bottom:3px}
.task-title.done-text{text-decoration:line-through;color:#475569}
.task-meta{font-size:.72rem;color:#475569;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.task-client-link{color:#0088cc;cursor:pointer}
.task-client-link:hover{text-decoration:underline}
.task-priority{padding:2px 7px;border-radius:99px;font-size:.65rem;font-weight:700}
.task-priority.high{background:#450a0a;color:#f87171}
.task-priority.medium{background:#1c1917;color:#fb923c}
.task-priority.low{background:#0c1a0c;color:#86efac}
.task-deadline{color:#fbbf24}
.task-deadline.overdue{color:#f87171}
.task-actions{display:flex;gap:6px;flex-shrink:0}
.task-del-btn{background:none;border:none;color:#334155;cursor:pointer;font-size:.9rem;padding:2px 4px}
.task-del-btn:hover{color:#f87171}
.tasks-empty{text-align:center;color:#334155;padding:40px;font-size:.88rem}
/* Add-task modal */
#add-task-modal .modal{width:440px}
</style>
</head>
<body>
<div class="sidebar-backdrop" id="sidebar-backdrop" onclick="closeSidebar()"></div>
<div class="sidebar" id="sidebar">
  <div class="sidebar-logo">
    <img src="/static/logo.png" alt="Scalerics">
  </div>
  <div class="nav-item active" id="nav-leads" onclick="showPanel('leads')">📋 Leads</div>
  <div class="nav-item" id="nav-kanban" onclick="showPanel('kanban')">🗂 Kanban</div>
  <div class="nav-item" id="nav-tasks" onclick="showPanel('tasks')">✅ Tareas</div>
  <div class="nav-item" id="nav-wa" onclick="showPanel('wa')">💬 WhatsApp</div>
  <div class="nav-item" id="nav-cal" onclick="showPanel('cal')">📅 Calendario</div>
  <div class="sidebar-bottom">
    <button class="logout-btn" onclick="window.location.href='/logout'">Cerrar sesión</button>
  </div>
</div>

<div class="topbar">
  <button class="hamburger" onclick="toggleSidebar()">☰</button>
  <img src="/static/logo.png" alt="Scalerics" style="height:22px">
</div>

<div class="main">
  <!-- ======= LEADS PANEL ======= -->
  <div id="leads-panel" class="panel active">
    <div class="page-header">
      <div>
        <h1>Leads</h1>
        <div class="page-date" id="page-date"></div>
      </div>
    </div>
    <div class="stats">
      <div class="stat-card"><div class="stat-label">Total leads</div><div class="stat-val" id="stat-total">—</div></div>
      <div class="stat-card"><div class="stat-label">Con pitch</div><div class="stat-val green" id="stat-pitch">—</div></div>
      <div class="stat-card"><div class="stat-label">Contactados</div><div class="stat-val blue" id="stat-contacted">—</div></div>
    </div>
    <div class="filters">
      <button class="filter-btn active" data-crm="">Todos</button>
      <button class="filter-btn" data-crm="sin_contactar">Sin contactar</button>
      <button class="filter-btn" data-crm="contactado">Contactado</button>
      <button class="filter-btn" data-crm="reunion_agendada">Reunión agendada</button>
      <button class="filter-btn" data-crm="demo_generada">Demo generada</button>
      <button class="filter-btn" data-crm="reunion_hecha">Reunión hecha</button>
      <button class="filter-btn" data-crm="presupuesto_enviado">Presupuesto enviado</button>
      <button class="filter-btn" data-crm="cliente_cerrado">Cerrado</button>
      <button class="filter-btn" data-crm="en_desarrollo">En desarrollo</button>
      <button class="filter-btn" data-crm="finalizado">Finalizado</button>
      <select class="filter-select" id="category-filter">
        <option value="">Todos los rubros</option>
      </select>
      <input class="search-box" id="search-input" placeholder="🔍 Buscar negocio...">
    </div>
    <div class="table-wrap">
      <div class="table-header">
        <span>Negocio</span><span>Teléfono</span><span>Acciones</span>
      </div>
      <div id="table-body"></div>
    </div>
  </div>

  <!-- ======= WHATSAPP PANEL ======= -->
  <div id="wa-panel" class="panel">
    <div class="page-header">
      <div>
        <h1>WhatsApp</h1>
        <div class="page-date">Conversaciones del bot</div>
      </div>
    </div>
    <div class="wa-container">
      <div class="wa-list">
        <div class="wa-list-header">Leads</div>
        <div id="wa-lead-list"><div class="wa-no-leads">Cargando...</div></div>
      </div>
      <div class="wa-chat" id="wa-chat-area">
        <div class="wa-empty" id="wa-empty-state">← Seleccioná un lead para ver la conversación</div>
        <div id="wa-chat-content" style="display:none;flex:1;flex-direction:column;min-height:0;overflow:hidden">
          <div class="wa-chat-header">
            <div class="wa-chat-info">
              <div class="wa-chat-name" id="wa-chat-name"></div>
              <div class="wa-chat-phone" id="wa-chat-phone"></div>
            </div>
            <span class="wa-human-badge" id="wa-human-badge" style="display:none">👤 Humano activo</span>
            <button class="wa-release-btn" id="wa-release-btn" style="display:none" onclick="releaseToBot()">🤖 Devolver al bot</button>
          </div>
          <div class="wa-messages" id="wa-messages"></div>
          <div class="wa-input-row">
            <input class="wa-input" id="wa-input" placeholder="Escribir mensaje..." onkeydown="if(event.key==='Enter')sendWaMessage()">
            <button class="wa-send-btn" onclick="sendWaMessage()">Enviar</button>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ======= KANBAN PANEL ======= -->
  <div id="kanban-panel" class="panel">
    <div class="page-header">
      <div><h1>Kanban</h1><div class="page-date">Pipeline de ventas</div></div>
      <button class="run-btn" style="width:auto;padding:8px 16px" onclick="loadKanban()">↺ Actualizar</button>
    </div>
    <div class="kanban-board" id="kanban-board">
      <div style="color:#475569;font-size:.85rem">Cargando...</div>
    </div>
  </div>

  <!-- ======= TASKS PANEL ======= -->
  <div id="tasks-panel" class="panel">
    <div class="page-header">
      <div><h1>Tareas</h1><div class="page-date">Tareas y seguimientos del equipo</div></div>
      <button class="run-btn" style="width:auto;padding:8px 16px" onclick="openAddTaskModal()">+ Nueva tarea</button>
    </div>
    <div class="tasks-filters">
      <button class="filter-btn active" data-tfilter="all" onclick="filterTasks('all',this)">Todas</button>
      <button class="filter-btn" data-tfilter="todo" onclick="filterTasks('todo',this)">Pendientes</button>
      <button class="filter-btn" data-tfilter="in_progress" onclick="filterTasks('in_progress',this)">En progreso</button>
      <button class="filter-btn" data-tfilter="done" onclick="filterTasks('done',this)">Hechas</button>
    </div>
    <div id="tasks-list"></div>
  </div>

  <!-- ======= CALENDAR PANEL ======= -->
  <div id="cal-panel" class="panel">
    <div class="cal-header">
      <h1 id="cal-week-label">Calendario</h1>
      <div style="display:flex;gap:8px;align-items:center;flex-shrink:0">
        <button class="cal-nav-btn" onclick="calChangeMonth(-1)">←</button>
        <button class="cal-nav-btn" onclick="calChangeMonth(1)">→</button>
        <button class="cal-new-btn" onclick="openNewEventModal()">+ Nueva reunión</button>
      </div>
    </div>
    <div id="cal-error" class="cal-error" style="display:none"></div>
    <div id="cal-days" class="cal-days"><div class="cal-loading">Cargando calendario...</div></div>
  </div>
</div>

<!-- Modal: Contactar -->
<div class="modal-overlay" id="contact-modal">
  <div class="modal">
    <h3 id="modal-title">Marcar como contactado</h3>
    <p id="modal-sub">Podés agregar una nota opcional sobre el contacto</p>
    <textarea id="modal-note" placeholder="Ej: Llamé el martes, quedó en pensar..."></textarea>
    <div class="modal-btns">
      <button class="btn-cancel" onclick="closeContactModal()">Cancelar</button>
      <button class="btn-confirm" onclick="confirmContact()">✓ Confirmar</button>
    </div>
  </div>
</div>

<!-- Modal: Add Task -->
<div class="modal-overlay" id="add-task-modal">
  <div class="modal" style="width:440px">
    <h3>Nueva tarea</h3>
    <div style="margin-top:14px">
      <label class="modal-label">Título</label>
      <input type="text" id="task-title-input" class="modal-input" placeholder="Ej: Enviar presupuesto, Llamar el martes...">
    </div>
    <div class="modal-row" style="margin-top:10px">
      <div>
        <label class="modal-label">Prioridad</label>
        <select id="task-priority-input" class="modal-input">
          <option value="medium">Media</option>
          <option value="high">Alta</option>
          <option value="low">Baja</option>
        </select>
      </div>
      <div>
        <label class="modal-label">Vencimiento</label>
        <input type="date" id="task-deadline-input" class="modal-input">
      </div>
    </div>
    <div style="margin-top:10px">
      <label class="modal-label">Cliente (opcional)</label>
      <input type="text" id="task-client-search" class="modal-input" placeholder="Buscar negocio..." oninput="_taskClientSearch(this.value)">
      <div id="task-client-results" style="background:#0a0f1a;border:1px solid #1e293b;border-radius:6px;margin-top:4px;display:none;max-height:140px;overflow-y:auto"></div>
      <input type="hidden" id="task-client-id">
      <div id="task-client-chosen" style="font-size:.78rem;color:#0088cc;margin-top:4px"></div>
    </div>
    <div class="modal-btns" style="margin-top:16px">
      <button class="btn-cancel" onclick="document.getElementById('add-task-modal').classList.remove('open')">Cancelar</button>
      <button class="btn-confirm" onclick="submitAddTask()">+ Crear tarea</button>
    </div>
  </div>
</div>

<!-- Modal: Pitch -->
<div class="modal-overlay" id="pitch-modal">
  <div class="modal" style="width:480px;max-width:95vw">
    <h3 id="pitch-modal-title">Pitch WhatsApp</h3>
    <p style="margin-bottom:12px"></p>
    <textarea id="pitch-modal-text" style="min-height:120px" readonly></textarea>
    <div class="modal-btns">
      <button class="btn-cancel" onclick="closePitchModal()">Cerrar</button>
      <button class="btn-confirm" onclick="copyPitchText()">📋 Copiar</button>
    </div>
  </div>
</div>

<!-- Modal: Pipeline -->
<div class="modal-overlay" id="pipeline-modal">
  <div class="modal" style="width:540px;max-width:95vw">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:18px">
      <h3 style="margin:0">Buscar negocios</h3>
      <span class="status-pill idle" id="pipeline-pill">● Listo</span>
    </div>
    <div class="pipeline-input-row">
      <input class="pipeline-input" id="pipeline-query" placeholder='Ej: "bar Montevideo", "gym Pocitos"' onkeydown="if(event.key==='Enter')runPipeline()">
      <input class="max-input" id="pipeline-max" type="number" value="30" min="1" max="200" title="Máximo de resultados">
    </div>
    <button class="btn-confirm" id="pipeline-run-btn" onclick="runPipeline()" style="width:100%;justify-content:center;margin-bottom:14px">▶ Buscar leads y generar pitches</button>
    <div class="pipeline-log" id="pipeline-log"><div class="log-empty">Los logs aparecen acá cuando corrés el pipeline</div></div>
    <div class="modal-btns">
      <button class="btn-cancel" onclick="closePipelineModal()">Cerrar</button>
    </div>
  </div>
</div>

<!-- Modal: Nueva reunión -->
<div class="modal-overlay" id="event-modal">
  <div class="modal" style="width:460px;max-width:95vw">
    <h3>Nueva reunión</h3>
    <p style="margin-bottom:16px"></p>
    <label class="modal-label">Título</label>
    <input type="text" id="ev-title" placeholder="Ej: Reunión con El Fogón">
    <div class="modal-row">
      <div>
        <label class="modal-label">Fecha</label>
        <input type="date" id="ev-date">
      </div>
      <div>
        <label class="modal-label">Hora</label>
        <input type="time" id="ev-time" value="10:00">
      </div>
    </div>
    <div class="modal-row">
      <div>
        <label class="modal-label">Duración (min)</label>
        <input type="number" id="ev-duration" value="60" min="15" max="480">
      </div>
    </div>
    <label class="modal-label">Descripción</label>
    <textarea id="ev-desc" placeholder="(opcional)" style="min-height:60px"></textarea>
    <div class="modal-btns">
      <button class="btn-cancel" onclick="closeNewEventModal()">Cancelar</button>
      <button class="btn-confirm" id="ev-save-btn" onclick="saveEvent()">📅 Crear reunión</button>
    </div>
  </div>
</div>

<!-- Modal: Generar Demo -->
<div class="modal-overlay" id="demo-modal">
  <div class="modal" style="width:520px;max-width:95vw">
    <div id="demo-form-section">
      <h3>📊 Generar Demo</h3>
      <p id="demo-lead-hint" style="margin-bottom:16px"></p>
      <label class="modal-label">Teléfono WhatsApp del lead</label>
      <div style="display:flex;gap:8px;margin-bottom:12px">
        <input type="text" id="demo-phone" placeholder="59899123456" style="margin-bottom:0;flex:1">
        <button onclick="fetchLeadForDemo()" style="background:#1e3a5f;border:1px solid #2d5a8f;border-radius:8px;padding:0 14px;color:#60a5fa;font-size:.8rem;cursor:pointer;white-space:nowrap">Buscar lead</button>
      </div>
      <label class="modal-label">Nombre del negocio *</label>
      <input type="text" id="demo-biz" placeholder="Ej: Bicicletería El Rayo">
      <label class="modal-label">Rubro / qué venden *</label>
      <input type="text" id="demo-rubro" placeholder="Ej: Bicicletería e-commerce, Restaurante, Clínica dental">
      <div class="modal-row">
        <div>
          <label class="modal-label">Ciudad</label>
          <input type="text" id="demo-city" placeholder="Montevideo">
        </div>
        <div>
          <label class="modal-label">Color principal (hex, opcional)</label>
          <input type="text" id="demo-color" placeholder="#2563EB">
        </div>
      </div>
      <div id="demo-conv-info" style="display:none;background:#0a1628;border:1px solid #1e3a5f;border-radius:8px;padding:10px 14px;margin-bottom:14px;font-size:.8rem;color:#94a3b8"></div>
      <div style="display:flex;gap:8px;margin-bottom:6px">
        <button class="btn-cancel" onclick="closeDemoModal()" style="flex:0 0 auto">Cancelar</button>
        <button id="demo-gen-btn" onclick="startDemoGeneration()" style="flex:1;background:linear-gradient(135deg,#0088cc,#3db648);color:#fff;font-size:.82rem;font-weight:700;padding:10px 12px;border-radius:8px;border:none;cursor:pointer">✨ Generar con API</button>
        <button id="demo-chat-btn" onclick="startDemoChat()" style="flex:1;background:#1e1b4b;border:1px solid #4f46e5;color:#a5b4fc;font-size:.82rem;font-weight:700;padding:10px 12px;border-radius:8px;cursor:pointer">💬 Claude Chat</button>
      </div>
      <div style="font-size:.7rem;color:#475569;text-align:center">API → deploy automático en Vercel &nbsp;|&nbsp; Chat → gratis, copiás el HTML vos</div>
    </div>
    <div id="demo-loading-section" style="display:none;text-align:center;padding:36px 0">
      <div style="font-size:2.5rem;margin-bottom:16px">🤖</div>
      <div style="font-weight:700;font-size:1rem;margin-bottom:8px">Generando demo con Claude...</div>
      <div style="color:#64748b;font-size:.84rem;line-height:1.6">Esto tarda entre 30 y 60 segundos.<br>Por favor esperá sin cerrar la ventana.</div>
    </div>
    <div id="demo-chat-section" style="display:none">
      <h3 style="margin-bottom:6px">💬 Demo vía Claude Chat</h3>
      <p style="font-size:.8rem;color:#64748b;margin-bottom:14px">Claude.ai se abre con el prompt listo. Claude genera el HTML completo — copialo y guardalo como <strong style="color:#a5b4fc">demo.html</strong> para abrirlo en el browser.</p>
      <div id="demo-chat-copied" style="display:none;background:#1a2e1a;border:1px solid #2d5a2d;border-radius:6px;padding:8px 12px;font-size:.78rem;color:#4ade80;margin-bottom:12px">✅ Prompt copiado al portapapeles.</div>
      <div class="modal-btns">
        <button class="btn-cancel" onclick="closeDemoModal()">Cerrar</button>
        <button onclick="copyAndOpenClaude()" style="background:#4f46e5;border:none;color:#fff;font-size:.82rem;font-weight:700;padding:10px 18px;border-radius:8px;cursor:pointer">📋 Copiar prompt y abrir Claude.ai</button>
      </div>
    </div>
    <div id="demo-result-section" style="display:none">
      <h3 style="margin-bottom:16px">✅ Demo lista</h3>
      <div id="demo-cached-badge" style="display:none;background:#1a2e1a;border:1px solid #2d5a2d;border-radius:6px;padding:8px 12px;font-size:.78rem;color:#4ade80;margin-bottom:12px">♻️ Esta demo ya fue generada antes — se reutilizó la existente.</div>
      <div style="background:#0a1628;border:1px solid #1e3a5f;border-radius:8px;padding:14px;margin-bottom:16px">
        <div style="font-size:.7rem;color:#475569;text-transform:uppercase;letter-spacing:.8px;margin-bottom:7px">URL pública</div>
        <div style="display:flex;align-items:center;gap:8px">
          <a id="demo-url-link" href="#" target="_blank" style="color:#06B6D4;font-size:.85rem;word-break:break-all;flex:1"></a>
          <button onclick="copyDemoUrl()" style="background:#1e293b;border:1px solid #334155;border-radius:6px;padding:5px 10px;font-size:.72rem;color:#94a3b8;cursor:pointer;flex-shrink:0">Copiar</button>
        </div>
      </div>
      <div id="demo-q-section" style="display:none">
        <div style="font-size:.7rem;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.8px;margin-bottom:10px">Preguntas si acepta el presupuesto</div>
        <ol id="demo-q-list" style="padding-left:18px;font-size:.82rem;color:#94a3b8;line-height:1.9"></ol>
      </div>
      <div class="modal-btns" style="margin-top:20px">
        <button class="btn-cancel" onclick="closeDemoModal()">Cerrar</button>
        <button class="btn-confirm" onclick="window.open(document.getElementById('demo-url-link').href,'_blank')">🔗 Abrir Demo</button>
      </div>
    </div>
  </div>
</div>

<script>
// ========== Sidebar mobile ==========
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('sidebar-backdrop').classList.toggle('open');
}
function closeSidebar() {
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('sidebar-backdrop').classList.remove('open');
}

// ========== Panel switching ==========
let activePanel = 'leads';
function showPanel(name) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById(name + '-panel').classList.add('active');
  document.getElementById('nav-' + name).classList.add('active');
  activePanel = name;
  closeSidebar();
  if (name === 'wa' && !waLoaded) loadWaLeads();
  if (name === 'cal' && !calLoaded) { calLoaded = true; renderCalendar(); }
  if (name === 'kanban') loadKanban();
  if (name === 'tasks') loadTasks();
}

// ========== Leads panel ==========
let currentCrm = '';
let currentCategory = '';
let currentSearch = '';
let contactingId = null;
let pipelinePolling = null;
let pitchMap = {};

async function loadStats() {
  const r = await fetch('/api/stats');
  const d = await r.json();
  document.getElementById('stat-total').textContent = d.total;
  document.getElementById('stat-pitch').textContent = d.with_pitch;
  document.getElementById('stat-contacted').textContent = d.contacted;
  const sel = document.getElementById('category-filter');
  const prev = sel.value;
  while (sel.options.length > 1) sel.remove(1);
  (d.categories || []).forEach(c => { sel.add(new Option(c, c)); });
  if (prev) sel.value = prev;
  document.getElementById('page-date').textContent = 'Actualizado: ' + new Date().toLocaleString('es-UY');
}

async function loadLeads() {
  const params = new URLSearchParams();
  if (currentCrm) params.set('crm_status', currentCrm);
  if (currentCategory) params.set('category', currentCategory);
  if (currentSearch) params.set('search', currentSearch);
  const r = await fetch('/api/leads?' + params);
  const leads = await r.json();
  pitchMap = {};
  leads.forEach(b => { if (b.pitch_text) pitchMap[b.id] = b.pitch_text; });
  const body = document.getElementById('table-body');
  if (!leads.length) { body.innerHTML = '<div class="empty-state">No hay leads con estos filtros</div>'; return; }
  const crmLabels = {sin_contactar:'Sin contactar',contactado:'Contactado',reunion_agendada:'Reunión agendada',reunion_hecha:'Reunión hecha',presupuesto_enviado:'Presupuesto enviado',negociacion:'Negociación',cliente_cerrado:'Cliente cerrado',en_desarrollo:'En desarrollo',finalizado:'Finalizado',agendo:'Agendó',firmo:'Firmó'};
  body.innerHTML = leads.map(b => {
    const crm = b.crm_status || 'sin_contactar';
    return `
    <div class="table-row row-${crm}">
      <div>
        <div class="biz-name" style="cursor:pointer;text-decoration:underline;text-decoration-color:#334155" onclick="openClientPanel(${b.id})">${esc(b.name||'')}</div>
        <div class="biz-sub">${esc(b.category||'')}${b.city ? ' · '+esc(b.city) : ''}</div>
      </div>
      <div>${b.phone ? (hasWhatsApp(b.phone) ? `<a class="phone-val" href="https://wa.me/${waNum(b.phone)}${b.pitch_text ? '?text='+encodeURIComponent(b.pitch_text) : ''}" target="_blank" title="Abrir WhatsApp con pitch">${esc(b.phone)}</a>` : `<span class="phone-val">${esc(b.phone)}</span>`) : '<span class="no-val">—</span>'}</div>
      <div class="actions">
        <button class="pitch-btn" onclick="openContact(${b.id},'${esc(b.name||'')}')">Contactar</button>
        ${crm !== 'sin_contactar' && crm ? `<span style="color:#3db648;font-size:.75rem;margin-left:4px">✓ ${crmLabels[crm]||crm}</span>` : ''}
      </div>
    </div>`}).join('');
}

function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
function waNum(phone) {
  let n = String(phone).replace(/[^0-9]/g,'');
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

async function deleteLead(id, name) {
  if (!confirm(`¿Eliminar "${name}"? Esta acción no se puede deshacer.`)) return;
  await fetch(`/api/leads/${id}`, {method:'DELETE'});
  loadStats(); loadLeads();
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
    loadLeads();
  });
});
document.getElementById('category-filter').addEventListener('change', e => { currentCategory = e.target.value; loadLeads(); });
let searchTimeout;
document.getElementById('search-input').addEventListener('input', e => {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(() => { currentSearch = e.target.value; loadLeads(); }, 300);
});
document.getElementById('contact-modal').addEventListener('click', e => { if(e.target===e.currentTarget) closeContactModal(); });
document.getElementById('pitch-modal').addEventListener('click', e => { if(e.target===e.currentTarget) closePitchModal(); });
document.getElementById('pipeline-modal').addEventListener('click', e => { if(e.target===e.currentTarget) closePipelineModal(); });

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
      : `<div class="cal-cell${c.isToday?' today':''}">
          <div class="cal-cell-day">${c.dayNum}</div>
          ${c.events.map(ev => {
            const ph = extractPhoneFromText((ev.title||'')+' '+(ev.description||''));
            const nm = extractNameFromTitle(ev.title||'');
            return `<div class="cal-event-chip ${ev.meeting_url?'meet':'regular'}" title="${esc((ev.time?ev.time+' ':'')+ev.title)}">
              ${ev.time?esc(ev.time)+' ':''}${ev.meeting_url?'🎥 ':''}${esc(ev.title||'')}
              <button class="cal-demo-btn" onclick="event.stopPropagation();openDemoModal('${ph||''}','${esc(ev.title||'')}','${nm||''}')">📊 Generar Demo</button>
            </div>`;
          }).join('')}
        </div>`
    ).join('')}
  </div>`;
}

function openNewEventModal() {
  const today = isoDate(new Date());
  document.getElementById('ev-title').value = '';
  document.getElementById('ev-date').value = today;
  document.getElementById('ev-time').value = '10:00';
  document.getElementById('ev-duration').value = '60';
  document.getElementById('ev-attendee').value = '';
  document.getElementById('ev-desc').value = '';
  document.getElementById('event-modal').classList.add('open');
}
function closeNewEventModal() { document.getElementById('event-modal').classList.remove('open'); }
document.getElementById('event-modal').addEventListener('click', e => { if(e.target===e.currentTarget) closeNewEventModal(); });

async function saveEvent() {
  const title = document.getElementById('ev-title').value.trim();
  const date = document.getElementById('ev-date').value;
  const time = document.getElementById('ev-time').value;
  const duration = parseInt(document.getElementById('ev-duration').value) || 60;
  const attendee = document.getElementById('ev-attendee').value.trim();
  const desc = document.getElementById('ev-desc').value.trim();
  if (!title || !date || !time) { alert('Completá el título, fecha y hora'); return; }
  const btn = document.getElementById('ev-save-btn');
  btn.disabled = true; btn.textContent = '...';
  const r = await fetch('/api/calendar/events', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({title,date,time,duration_min:duration,attendee_email:attendee,description:desc})});
  const d = await r.json();
  btn.disabled = false; btn.textContent = '📅 Crear reunión';
  if (!d.ok) { alert('Error: '+(d.error||'Error desconocido')); return; }
  closeNewEventModal();
  renderCalendar();
  if (d.meet_url) {
    if (confirm('Reunión creada. ¿Abrir Google Meet ahora?')) window.open(d.meet_url, '_blank');
  }
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

async function loadTasks() {
  try {
    const [tr, lr] = await Promise.all([
      fetch('/api/tasks').then(r => r.json()),
      fetch('/api/leads').then(r => r.json()),
    ]);
    _allTasks = Array.isArray(tr) ? tr : [];
    _allLeads = Array.isArray(lr) ? lr : [];
  } catch { _allTasks = []; }
  renderTasksList();
}

function filterTasks(status, btn) {
  _taskStatusFilter = status;
  document.querySelectorAll('.tasks-filters .filter-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  renderTasksList();
}

function renderTasksList() {
  const container = document.getElementById('tasks-list');
  if (!container) return;
  let tasks = _taskStatusFilter === 'all'
    ? _allTasks
    : _allTasks.filter(t => t.status === _taskStatusFilter);
  tasks = [...tasks].sort((a, b) => {
    const prio = {high:0,medium:1,low:2};
    return (prio[a.priority]||1) - (prio[b.priority]||1);
  });
  if (!tasks.length) { container.innerHTML = '<div class="tasks-empty">Sin tareas. Agregá una con el botón de arriba.</div>'; return; }
  container.innerHTML = tasks.map(t => _taskRowHtml(t)).join('');
}

function _taskRowHtml(t) {
  const done = t.status === 'done';
  const lead = t.client_id ? _allLeads.find(l => l.id === t.client_id) : null;
  const now = new Date(); const dl = t.deadline ? new Date(t.deadline) : null;
  const overdue = dl && dl < now && !done;
  const dlStr = dl ? dl.toLocaleDateString('es-UY',{day:'2-digit',month:'2-digit'}) : '';
  const prioLabel = ({'high':'Alta','medium':'Media','low':'Baja'})[t.priority] || t.priority;
  return `<div class="task-row" id="task-row-${t.id}">
    <div class="task-check ${done ? 'done' : ''}" onclick="_toggleTask(${t.id},${done})">${done ? '✓' : ''}</div>
    <div class="task-body">
      <div class="task-title ${done ? 'done-text' : ''}">${esc(t.title)}</div>
      <div class="task-meta">
        ${t.priority ? `<span class="task-priority ${t.priority}">${prioLabel}</span>` : ''}
        ${lead ? `<span class="task-client-link" onclick="openClientPanel(${lead.id})">${esc(lead.name||'')}</span>` : ''}
        ${dlStr ? `<span class="task-deadline ${overdue ? 'overdue' : ''}">📅 ${dlStr}${overdue?' (vencida)':''}</span>` : ''}
      </div>
    </div>
    <div class="task-actions">
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
  renderTasksList();
}

async function _deleteTask(id) {
  await fetch('/api/tasks/' + id, {method:'DELETE'});
  _allTasks = _allTasks.filter(t => t.id !== id);
  renderTasksList();
  if (_cpClientId) { _cpData.tasks = (_cpData.tasks||[]).filter(t => t.id !== id); _cpSwitchTab('ctasks'); }
}

function openAddTaskModal(clientId, clientName) {
  document.getElementById('task-title-input').value = '';
  document.getElementById('task-priority-input').value = 'medium';
  document.getElementById('task-deadline-input').value = '';
  document.getElementById('task-client-search').value = clientName || '';
  document.getElementById('task-client-id').value = clientId || '';
  document.getElementById('task-client-chosen').textContent = clientName ? 'Cliente: ' + clientName : '';
  document.getElementById('task-client-results').style.display = 'none';
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

async function submitAddTask() {
  const title = document.getElementById('task-title-input').value.trim();
  if (!title) { document.getElementById('task-title-input').focus(); return; }
  const body = {
    title,
    priority: document.getElementById('task-priority-input').value,
    deadline: document.getElementById('task-deadline-input').value || null,
    status: 'todo',
  };
  const clientId = document.getElementById('task-client-id').value;
  if (clientId) body.client_id = parseInt(clientId);
  const r = await fetch('/api/tasks', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const d = await r.json();
  document.getElementById('add-task-modal').classList.remove('open');
  const newTask = {id: d.id, ...body};
  _allTasks.unshift(newTask);
  renderTasksList();
  if (_cpClientId && body.client_id === _cpClientId) {
    _cpData.tasks = [newTask, ...(_cpData.tasks||[])];
    _cpSwitchTab('ctasks');
  }
}

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
loadStats();
loadLeads();

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
  const [leadRes, meetRes, budgetRes, demoRes, waRes] = await Promise.allSettled([
    fetch('/api/leads/' + _cpClientId).then(r => r.json()),
    fetch('/api/calendar/meetings/' + _cpClientId).then(r => r.json()),
    fetch('/api/leads/' + _cpClientId + '/budget').then(r => r.json()),
    fetch('/api/demo/status/' + _cpClientId).then(r => r.json()),
    fetch('/api/wa/lead-by-phone/').then(() => null).catch(() => null),
  ]);
  _cpData.lead    = leadRes.status === 'fulfilled' ? leadRes.value : {};
  _cpData.meetings = meetRes.status === 'fulfilled' && Array.isArray(meetRes.value) ? meetRes.value : [];
  _cpData.budget  = budgetRes.status === 'fulfilled' ? budgetRes.value : null;
  _cpData.demo    = demoRes.status === 'fulfilled' ? demoRes.value : null;

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
  if (tab === 'info')      body.innerHTML = _cpRenderInfo();
  else if (tab === 'conv') body.innerHTML = _cpRenderConv();
  else if (tab === 'meet') { body.innerHTML = _cpRenderMeetings(); _cpBindMeetings(); }
  else if (tab === 'budget') { body.innerHTML = _cpRenderBudget(); _cpBindBudget(); }
  else if (tab === 'demo') body.innerHTML = _cpRenderDemo();
  else if (tab === 'ctasks') { body.innerHTML = _cpRenderTasks(); _cpBindTasks(); }
}

function _cpRenderInfo() {
  const l = _cpData.lead || {};
  const ci = l.client_info || {};
  const stars = l.rating ? '⭐ ' + l.rating + (l.review_count ? ' (' + l.review_count + ' reseñas)' : '') : '';
  const hasBotData = ci.lead_name || ci.budget_range || ci.colors || ci.instagram || ci.needs;
  return `<div class="cp-section">
    <div class="cp-section-title">Información del negocio</div>
    ${l.phone ? `<div class="cp-field"><span class="cp-field-label">Teléfono</span><span class="cp-field-val">${l.phone}</span></div>` : ''}
    ${l.city ? `<div class="cp-field"><span class="cp-field-label">Ciudad</span><span class="cp-field-val">${l.city}</span></div>` : ''}
    ${l.category ? `<div class="cp-field"><span class="cp-field-label">Rubro</span><span class="cp-field-val">${l.category}</span></div>` : ''}
    ${l.address ? `<div class="cp-field"><span class="cp-field-label">Dirección</span><span class="cp-field-val">${l.address}</span></div>` : ''}
    ${stars ? `<div class="cp-field"><span class="cp-field-label">Rating</span><span class="cp-field-val">${stars}</span></div>` : ''}
  </div>
  ${hasBotData ? `<div class="cp-section">
    <div class="cp-section-title">Datos del bot <span style="font-size:.7rem;color:#475569;font-weight:400">(calificación WA)</span></div>
    ${ci.lead_name ? `<div class="cp-field"><span class="cp-field-label">Contacto</span><span class="cp-field-val">${ci.lead_name}</span></div>` : ''}
    ${ci.budget_range ? `<div class="cp-field"><span class="cp-field-label">Presupuesto</span><span class="cp-field-val">${ci.budget_range}</span></div>` : ''}
    ${ci.colors ? `<div class="cp-field"><span class="cp-field-label">Colores de marca</span><span class="cp-field-val">${ci.colors}</span></div>` : ''}
    ${ci.instagram ? `<div class="cp-field"><span class="cp-field-label">Instagram / web</span><span class="cp-field-val">${ci.instagram}</span></div>` : ''}
    ${ci.needs ? `<div class="cp-field"><span class="cp-field-label">Necesidades</span><span class="cp-field-val">${ci.needs}</span></div>` : ''}
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
    <button class="cp-btn cp-btn-ghost" onclick="_cpOpenNewMeeting()">+ Nueva reunión</button>
  </div>`;
  if (!meets.length) html += `<div style="color:#475569;font-size:.85rem">No hay reuniones registradas.</div>`;
  meets.forEach(m => {
    const hasSummary = m.summary || m.requirements;
    html += `<div class="cp-meeting-card" id="meet-card-${m.id}">
      <div class="cp-meeting-title">${m.title || 'Reunión'}</div>
      <div class="cp-meeting-meta">${m.start_at ? m.start_at.substring(0,16).replace('T',' ') : ''} · ${_cpMeetStatus(m.status)}</div>
      ${m.meet_link ? `<a class="cp-meeting-link" href="${m.meet_link}" target="_blank">🔗 ${m.meet_link}</a>` : ''}
      ${hasSummary ? `
        <div class="cp-summary-label">Resumen</div>
        <div class="cp-summary-box">${m.summary || ''}</div>
        ${m.requirements ? `<div class="cp-summary-label" style="margin-top:10px">Requerimientos</div>
        <div class="cp-summary-box">${m.requirements}</div>` : ''}
        <button class="cp-btn cp-btn-primary" style="margin-top:10px" onclick="_cpGenerateBudgetFromMeeting(${m.id})">⚡ Generar presupuesto</button>
      ` : ''}
      <div style="margin-top:10px">
        <div class="cp-summary-label">Transcripción de la reunión</div>
        <textarea class="cp-transcript-area" id="transcript-${m.id}" placeholder="Pegá la transcripción de Google Meet acá...">${m.transcript || ''}</textarea>
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
  // Reuse existing calendar new-event form by switching to calendar panel and pre-filling client
  document.querySelector('.nav-item[data-panel="calendar"]').click();
  closeClientPanel();
}

function _cpGenerateBudgetFromMeeting(meetingId) {
  const meet = _cpData.meetings.find(m => m.id === meetingId);
  _cpSwitchTab('budget');
  if (meet && meet.requirements) {
    document.getElementById('cp-extra-req') && (document.getElementById('cp-extra-req').value = meet.requirements);
  }
}

function _cpRenderBudget() {
  const b = _cpData.budget;
  let budgetHtml = '';
  if (b && b.items) {
    const meta = typeof b.notes === 'object' ? b.notes : {};
    const devPrice = b.total_amount || meta.dev_price || 0;
    const monthlyPrice = meta.monthly_price || 0;
    const sections = Array.isArray(b.items) ? b.items : [];
    const sectionsPreview = sections.slice(0, 2).map(s =>
      `<div style="margin-bottom:6px"><span style="color:#0088cc;font-size:.78rem;font-weight:600">${s.title || ''}</span>` +
      (s.subsections ? ` <span style="color:#475569;font-size:.75rem">(${s.subsections.length} módulos)</span>` :
       s.items ? ` <span style="color:#475569;font-size:.75rem">(${s.items.length} ítems)</span>` : '') +
      `</div>`
    ).join('');
    budgetHtml = `
    <div style="background:#0a0f1a;border-radius:8px;padding:14px;margin-bottom:14px">
      <div style="font-size:.72rem;color:#475569;text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px">Resumen del presupuesto</div>
      ${meta.hero_title ? `<div style="color:#e2e8f0;font-size:.9rem;font-weight:600;margin-bottom:8px">${meta.hero_title}</div>` : ''}
      ${sectionsPreview}
      ${sections.length > 2 ? `<div style="color:#475569;font-size:.75rem">+${sections.length-2} secciones más...</div>` : ''}
      <div style="display:flex;gap:20px;margin-top:12px;padding-top:12px;border-top:1px solid #1e293b">
        <div>
          <div style="font-size:.68rem;color:#475569;text-transform:uppercase;letter-spacing:.5px">Desarrollo</div>
          <div style="font-size:1.1rem;font-weight:700;color:#4ade80">USD ${devPrice.toLocaleString()}</div>
        </div>
        ${monthlyPrice ? `<div>
          <div style="font-size:.68rem;color:#475569;text-transform:uppercase;letter-spacing:.5px">Mensual</div>
          <div style="font-size:1.1rem;font-weight:700;color:#94a3b8">USD ${monthlyPrice.toLocaleString()}/mes</div>
        </div>` : ''}
      </div>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <a class="cp-btn cp-btn-primary" href="/api/leads/${_cpClientId}/budget/preview" target="_blank">🖨 Ver / imprimir PDF</a>
      ${b.status !== 'sent' ? `<button class="cp-btn cp-btn-success" onclick="_cpMarkBudgetSent()">✅ Marcar enviado</button>` : `<span class="cp-badge cp-badge-sent">Enviado</span>`}
      <button class="cp-btn cp-btn-ghost" onclick="_cpRegeneraBudget()">⚡ Regenerar</button>
    </div>`;
  } else {
    budgetHtml = `<div style="color:#475569;font-size:.85rem;margin-bottom:14px">Sin presupuesto generado aún.</div>`;
  }
  return `<div class="cp-section">
    <div class="cp-section-title">Requerimientos adicionales</div>
    <textarea class="cp-req-area" id="cp-extra-req" placeholder="Describí qué necesita el cliente (opcional, se suman a los de la reunión)..."></textarea>
    <button class="cp-btn cp-btn-primary" id="cp-gen-btn" onclick="_cpRegeneraBudget()">
      <span id="budget-spin" style="display:none" class="cp-spinner"></span>
      ⚡ Generar presupuesto con IA
    </button>
  </div>
  <div class="cp-section">
    <div class="cp-section-title">Presupuesto ${b && b.status === 'sent' ? '<span class=\\"cp-badge cp-badge-sent\\">Enviado</span>' : b ? '<span class=\\"cp-badge cp-badge-draft\\">Borrador</span>' : ''}</div>
    ${budgetHtml}
  </div>`;
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

function _cpRenderDemo() {
  const d = _cpData.demo;
  const l = _cpData.lead || {};
  const isDone = d && d.status === 'completed' && d.url;
  const isGenerating = d && ['generating', 'pending'].includes(d.status);
  const demoPayload = JSON.stringify({id:l.id,name:l.name||'',category:l.category||'',city:l.city||'',phone:l.phone||''});
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
    </div>`;
  }
  if (isGenerating) {
    return `<div class="cp-section">
      <div class="cp-section-title">Demo <span class="cp-badge cp-badge-generating">Generando...</span></div>
      <div style="display:flex;align-items:center;gap:8px;color:#94a3b8;font-size:.85rem">
        <span class="cp-spinner"></span> Generando demo con IA...
      </div>
      <div style="font-size:.75rem;color:#475569;margin-top:6px">Puede tomar 1-2 minutos. Actualizá la página para ver el estado.</div>
    </div>`;
  }
  return `<div class="cp-section">
    <div class="cp-section-title">Demo</div>
    ${d && d.error_message ? `<div style="color:#f87171;font-size:.8rem;margin-bottom:8px;background:#0a0f1a;padding:8px;border-radius:6px">Error anterior: ${d.error_message}</div>` : ''}
    <div style="color:#475569;font-size:.85rem;margin-bottom:12px">Sin demo generada para este cliente.</div>
    <button class="cp-btn cp-btn-primary" onclick="closeClientPanel();openDemoModalFromCRM(${demoPayload})">
      📊 Generar demo
    </button>
  </div>`;
}

function _cpChangeStatus(val) {
  fetch('/api/leads/' + _cpClientId + '/crm-status', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({crm_status: val})
  }).then(() => { if (_cpData.lead) _cpData.lead.crm_status = val; });
}
</script>

<div class="cp-backdrop" id="cp-backdrop" onclick="closeClientPanel()"></div>
<div class="client-panel" id="client-panel">
  <button class="cp-close" onclick="closeClientPanel()">×</button>
  <div class="cp-header">
    <div class="cp-title" id="cp-title">Cliente</div>
    <div class="cp-sub">
      <span id="cp-phone"></span>
      <select class="cp-status-sel" id="cp-status-sel" onchange="_cpChangeStatus(this.value)">
        <option value="sin_contactar">Sin contactar</option>
        <option value="contactado">Contactado</option>
        <option value="reunion_agendada">Reunión agendada</option>
        <option value="demo_generada">Demo generada</option>
        <option value="reunion_hecha">Reunión hecha</option>
        <option value="presupuesto_enviado">Presupuesto enviado</option>
        <option value="negociacion">Negociación</option>
        <option value="cliente_cerrado">Cliente cerrado</option>
        <option value="en_desarrollo">En desarrollo</option>
        <option value="finalizado">Finalizado</option>
      </select>
    </div>
    <div class="cp-tabs">
      <div class="cp-tab active" data-tab="info" onclick="_cpSwitchTab('info')">Info</div>
      <div class="cp-tab" data-tab="conv" onclick="_cpSwitchTab('conv')">Conversación</div>
      <div class="cp-tab" data-tab="meet" onclick="_cpSwitchTab('meet')">Reuniones</div>
      <div class="cp-tab" data-tab="budget" onclick="_cpSwitchTab('budget')">Presupuesto</div>
      <div class="cp-tab" data-tab="demo" onclick="_cpSwitchTab('demo')">Demo</div>
      <div class="cp-tab" data-tab="ctasks" onclick="_cpSwitchTab('ctasks')">Tareas</div>
    </div>
  </div>
  <div class="cp-body" id="cp-body">
    <div style="color:#475569">Cargando...</div>
  </div>
</div>
</body>
</html>"""


def create_app(db_path: str) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY") or "scalerics-dev-key-change-in-prod"
    app.config["DB_PATH"] = db_path
    app.config["PIPELINE_STATUS"] = _pipeline_status
    app.config["PIPELINE_LOCK"] = _pipeline_lock

    for bp in (leads_bp, demos_bp, calendar_bp, wa_bp, pipeline_bp, tasks_bp, budgets_bp):
        app.register_blueprint(bp)

    @app.before_request
    def require_login():
        if request.endpoint in ("login", "logout", "static"):
            return
        # Any /api/ request with valid x-admin-token bypasses session auth
        if request.path.startswith("/api/"):
            token = request.headers.get("x-admin-token", "")
            expected = os.environ.get("ADMIN_TOKEN", "")
            if expected and token == expected:
                return
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "session_expired"}), 401
            return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            password = request.form.get("password", "")
            expected = os.environ.get("DASHBOARD_PASSWORD", "")
            if not expected:
                session["logged_in"] = True
                return redirect(url_for("index"))
            if password == expected:
                session["logged_in"] = True
                return redirect(url_for("index"))
            error = "Contraseña incorrecta"
        return render_template_string(LOGIN_HTML, error=error)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    def index():
        return render_template_string(DASHBOARD_HTML)

    worker = init_worker(db_path)
    worker.register("demo", demo_job_handler)
    worker.start()

    return app


def run(db_path: str) -> None:
    init_db(db_path)
    seed_pitch_templates(db_path)
    app = create_app(db_path)
    threading.Timer(1.2, lambda: webbrowser.open("http://localhost:5000")).start()
    app.run(port=5000, debug=False, use_reloader=False)
