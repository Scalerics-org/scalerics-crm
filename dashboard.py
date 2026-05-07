import base64
import datetime
import html as html_lib
import os
import subprocess
import sys
import threading
import webbrowser
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests as http_requests
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template_string, request, session, url_for

from database import get_all_businesses, update_business, delete_business

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or "scalerics-dev-key-change-in-prod"
_db_path: str = ""
_pipeline_status: dict = {"running": False, "log": [], "error": None}

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
</style>
</head>
<body>
<div class="sidebar-backdrop" id="sidebar-backdrop" onclick="closeSidebar()"></div>
<div class="sidebar" id="sidebar">
  <div class="sidebar-logo">
    <img src="/static/logo.png" alt="Scalerics">
  </div>
  <div class="nav-item active" id="nav-leads" onclick="showPanel('leads')">📋 Leads</div>
  <div class="nav-item" id="nav-wa" onclick="showPanel('wa')">💬 WhatsApp</div>
  <div class="nav-item" id="nav-cal" onclick="showPanel('cal')">📅 Calendario</div>
  <div class="sidebar-bottom">
    <button class="run-btn" onclick="openPipelineModal()">▶ Correr pipeline</button>
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
      <div class="stat-card"><div class="stat-label">Con email</div><div class="stat-val yellow" id="stat-email">—</div></div>
      <div class="stat-card"><div class="stat-label">Contactados</div><div class="stat-val blue" id="stat-contacted">—</div></div>
    </div>
    <div class="filters">
      <button class="filter-btn active" data-crm="">Todos</button>
      <button class="filter-btn" data-crm="sin_contactar">Sin contactar</button>
      <button class="filter-btn" data-crm="contactado">Contactado</button>
      <button class="filter-btn" data-crm="agendo">Agendó</button>
      <button class="filter-btn" data-crm="firmo">Firmó</button>
      <select class="filter-select" id="category-filter">
        <option value="">Todos los rubros</option>
      </select>
      <input class="search-box" id="search-input" placeholder="🔍 Buscar negocio...">
    </div>
    <div class="table-wrap">
      <div class="table-header">
        <span>Negocio</span><span>Teléfono</span><span>Email</span><span>Acciones</span>
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
      <div>
        <label class="modal-label">Email asistente</label>
        <input type="email" id="ev-attendee" placeholder="(opcional)">
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
      <div class="modal-btns">
        <button class="btn-cancel" onclick="closeDemoModal()">Cancelar</button>
        <button class="btn-confirm" id="demo-gen-btn" onclick="startDemoGeneration()">✨ Generar Demo</button>
      </div>
    </div>
    <div id="demo-loading-section" style="display:none;text-align:center;padding:36px 0">
      <div style="font-size:2.5rem;margin-bottom:16px">🤖</div>
      <div style="font-weight:700;font-size:1rem;margin-bottom:8px">Generando demo con Claude...</div>
      <div style="color:#64748b;font-size:.84rem;line-height:1.6">Esto tarda entre 30 y 60 segundos.<br>Por favor esperá sin cerrar la ventana.</div>
    </div>
    <div id="demo-result-section" style="display:none">
      <h3 style="margin-bottom:16px">✅ Demo lista</h3>
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
  document.getElementById('stat-email').textContent = d.with_email;
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
  const crmLabels = {sin_contactar:'Sin contactar',contactado:'Contactado',agendo:'Agendó',firmo:'Firmó'};
  body.innerHTML = leads.map(b => {
    const crm = b.crm_status || 'sin_contactar';
    return `
    <div class="table-row row-${crm}">
      <div>
        <div class="biz-name">${esc(b.name||'')}</div>
        <div class="biz-sub">${esc(b.category||'')}${b.city ? ' · '+esc(b.city) : ''}</div>
      </div>
      <div>${b.phone ? `<a class="phone-val" href="https://wa.me/${waNum(b.phone)}" target="_blank" title="Abrir WhatsApp">${esc(b.phone)}</a>` : '<span class="no-val">—</span>'}</div>
      <div>${b.email ? `<span class="email-val">${esc(b.email)}</span>` : '<span class="no-val">—</span>'}</div>
      <div class="actions">
        <select class="status-sel" onchange="setCrmStatus(${b.id},this.value)">
          ${['sin_contactar','contactado','agendo','firmo'].map(s=>`<option value="${s}"${crm===s?' selected':''}>${crmLabels[s]}</option>`).join('')}
        </select>
        ${b.pitch_text ? `<button class="pitch-btn" onclick="openPitchModal(${b.id},'${esc(b.name||'')}')">📋</button>` : ''}
        ${b.email && b.status !== 'email_sent' ? `<button class="mail-btn" onclick="sendMail(${b.id},this)">📧</button>` : ''}
        <button class="delete-btn" onclick="deleteLead(${b.id},'${esc(b.name||'')}')">🗑</button>
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

async function sendMail(id, btn) {
  btn.disabled = true; btn.textContent = '...';
  const r = await fetch(`/api/leads/${id}/send-email`, {method:'POST'});
  const d = await r.json();
  if (d.ok) { loadStats(); loadLeads(); }
  else { alert('Error enviando mail: ' + (d.error||'Error desconocido')); btn.disabled = false; btn.textContent = '📧 Enviar'; }
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
  const m = text.match(/\+?\d[\d\s\-]{7,14}\d/);
  return m ? m[0].replace(/[\s\-\+]/g,'') : null;
}

function extractNameFromTitle(title) {
  // Calendly format: "Firstname Lastname: Meeting Type"
  const m = title.match(/^([^:]+):/);
  return m ? m[1].trim() : null;
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

// Initial load
loadStats();
loadLeads();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.before_request
def require_login():
    if request.endpoint in ("login", "logout", "static"):
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


# ---------------------------------------------------------------------------
# Main routes (leads)
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/leads")
def api_leads():
    businesses = get_all_businesses(_db_path)
    crm_status = request.args.get("crm_status")
    category = request.args.get("category")
    search = (request.args.get("search") or "").lower()
    if crm_status:
        businesses = [b for b in businesses if (b.get("crm_status") or "sin_contactar") == crm_status]
    if category:
        businesses = [b for b in businesses if (b.get("category") or "").lower() == category.lower()]
    if search:
        businesses = [b for b in businesses if search in (b.get("name") or "").lower()]
    return jsonify(businesses)


@app.route("/api/leads/<int:biz_id>/crm-status", methods=["POST"])
def api_crm_status(biz_id):
    data = request.get_json() or {}
    crm_status = data.get("crm_status", "sin_contactar")
    if crm_status not in ("sin_contactar", "contactado", "agendo", "firmo"):
        return jsonify({"ok": False, "error": "Estado inválido"})
    update_business(_db_path, biz_id, crm_status=crm_status)
    return jsonify({"ok": True})


@app.route("/api/leads/<int:biz_id>", methods=["DELETE"])
def api_delete_lead(biz_id):
    delete_business(_db_path, biz_id)
    return jsonify({"ok": True})


@app.route("/api/leads/<int:biz_id>/contact", methods=["POST"])
def api_contact(biz_id):
    data = request.get_json() or {}
    note = data.get("note", "")
    update_business(_db_path, biz_id, status="contacted", notes=note)
    return jsonify({"ok": True})


@app.route("/api/leads/<int:biz_id>/send-email", methods=["POST"])
def api_send_email(biz_id):
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        return jsonify({"ok": False, "error": "google-api-python-client no instalado"})

    client_id = os.environ.get("GMAIL_CLIENT_ID")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET")
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN")
    sender_email = os.environ.get("FACTORY_EMAIL", "")

    if not all([client_id, client_secret, refresh_token, sender_email]):
        return jsonify({"ok": False, "error": "Faltan variables Gmail en .env"})

    businesses = get_all_businesses(_db_path)
    biz = next((b for b in businesses if b["id"] == biz_id), None)
    if not biz:
        return jsonify({"ok": False, "error": "Negocio no encontrado"})
    if not biz.get("email"):
        return jsonify({"ok": False, "error": "Este negocio no tiene email"})

    try:
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=["https://www.googleapis.com/auth/gmail.send"],
        )
        service = build("gmail", "v1", credentials=creds)
        factory_name = os.environ.get("FACTORY_NAME", "Scalerics")
        factory_phone = os.environ.get("FACTORY_PHONE", "")
        name_esc = html_lib.escape(biz.get("name", "") or "")
        phone_line = f"📞 {factory_phone}" if factory_phone else ""
        email_html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333;padding:20px">
  <div style="border-top:4px solid #7c3aed;padding-top:24px">
    <h2 style="color:#7c3aed;margin-bottom:4px">{html_lib.escape(factory_name)}</h2>
    <p style="color:#666;margin-top:0;font-size:0.9em">Software factory · www.scalerics.com</p>
  </div>
  <p style="margin-top:24px">Hola equipo de <strong>{name_esc}</strong>,</p>
  <p>Notamos que todavía no tienen página web propia. Hoy la mayoría de los clientes busca en Google antes de visitar un negocio — y sin web, no aparecen.</p>
  <p>En <strong>{html_lib.escape(factory_name)}</strong> desarrollamos sitios web para negocios locales uruguayos, rápido y a precios accesibles.</p>
  <p>¿Charlamos 15 minutos esta semana?</p>
  <div style="margin-top:32px;padding:16px;background:#f5f3ff;border-radius:8px;font-size:0.9em">
    <strong>{html_lib.escape(factory_name)}</strong><br>
    📧 <a href="mailto:{html_lib.escape(sender_email)}" style="color:#7c3aed">{html_lib.escape(sender_email)}</a><br>
    {phone_line}
  </div>
  <p style="font-size:0.75em;color:#999;margin-top:24px">Si no querés recibir más mensajes, respondé con "no gracias".</p>
</body></html>"""
        message = MIMEMultipart("alternative")
        message["From"] = sender_email
        message["To"] = biz["email"]
        message["Subject"] = f"¿Le puedo mostrar algo a {biz['name']}?"
        message.attach(MIMEText(email_html, "html", "utf-8"))
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        update_business(_db_path, biz_id, status="email_sent",
                        email_sent_at=datetime.datetime.now().isoformat())
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/stats")
def api_stats():
    businesses = get_all_businesses(_db_path)
    categories = sorted({b.get("category") or "" for b in businesses if b.get("category")})
    return jsonify({
        "total": len(businesses),
        "with_pitch": sum(1 for b in businesses if b.get("pitch_text")),
        "with_email": sum(1 for b in businesses if b.get("email")),
        "contacted": sum(1 for b in businesses if b.get("status") in ("contacted", "email_sent")),
        "categories": categories,
    })


@app.route("/api/run-pipeline", methods=["POST"])
def api_run_pipeline():
    global _pipeline_status
    if _pipeline_status["running"]:
        return jsonify({"ok": False, "error": "El pipeline ya está corriendo"})
    data = request.get_json() or {}
    query = (data.get("query") or "").strip()
    max_results = int(data.get("max") or 30)
    if not query:
        return jsonify({"ok": False, "error": "Escribí qué negocios buscar"})
    _pipeline_status = {"running": True, "log": [], "error": None}

    def _run():
        global _pipeline_status
        try:
            cmd = [sys.executable, "main.py", "run-all", "--query", query, "--max", str(max_results)]
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    _pipeline_status["log"].append(line)
            proc.wait()
            if proc.returncode != 0:
                _pipeline_status["error"] = f"El pipeline terminó con código {proc.returncode}"
        except Exception as e:
            _pipeline_status["error"] = str(e)
        finally:
            _pipeline_status["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/pipeline-status")
def api_pipeline_status():
    return jsonify(_pipeline_status)


# ---------------------------------------------------------------------------
# WhatsApp panel routes
# ---------------------------------------------------------------------------

def _bot_req(method, path, **kwargs):
    """Call the bot's admin API. Returns (response_dict, error_string)."""
    base = os.environ.get("BOT_API_URL", "").rstrip("/")
    token = os.environ.get("ADMIN_TOKEN", "")
    if not base:
        return None, "BOT_API_URL no configurada en .env"
    if not token:
        return None, "ADMIN_TOKEN no configurado en .env"
    headers = {"x-admin-token": token, "Content-Type": "application/json"}
    try:
        r = http_requests.request(
            method, f"{base}/api/{path.lstrip('/')}",
            headers=headers, timeout=12, **kwargs
        )
        data = r.json()
        if r.status_code >= 400:
            return None, data.get("error", r.text)
        return data, None
    except Exception as e:
        return None, str(e)


@app.route("/api/wa/leads")
def api_wa_leads():
    data, err = _bot_req("GET", "leads?limit=200")
    if err:
        return jsonify({"error": err})
    return jsonify(data.get("leads", []))


@app.route("/api/wa/leads/<path:phone>/messages")
def api_wa_messages(phone):
    data, err = _bot_req("GET", f"leads/phone/{phone}")
    if err:
        return jsonify({"error": err})
    return jsonify(data.get("messages", []))


@app.route("/api/wa/leads/<path:phone>/release", methods=["POST"])
def api_wa_release(phone):
    data, err = _bot_req("POST", f"leads/phone/{phone}/release")
    if err:
        return jsonify({"ok": False, "error": err})
    return jsonify({"ok": True})


@app.route("/api/wa/send", methods=["POST"])
def api_wa_send():
    body = request.get_json() or {}
    phone = body.get("phone", "").strip()
    text = body.get("text", "").strip()
    if not phone or not text:
        return jsonify({"ok": False, "error": "phone y text requeridos"})
    data, err = _bot_req("POST", "send", json={"phone": phone, "text": text})
    if err:
        return jsonify({"ok": False, "error": err})
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Calendar panel routes
# ---------------------------------------------------------------------------

def _get_calendar_service():
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        return None, "google-api-python-client no instalado"
    client_id = os.environ.get("GCAL_CLIENT_ID", "")
    client_secret = os.environ.get("GCAL_CLIENT_SECRET", "")
    refresh_token = os.environ.get("GCAL_REFRESH_TOKEN", "")
    if not all([client_id, client_secret, refresh_token]):
        return None, "Faltan GCAL_CLIENT_ID, GCAL_CLIENT_SECRET o GCAL_REFRESH_TOKEN en .env. Ejecutá setup_calendar.py primero."
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    service = build("calendar", "v3", credentials=creds)
    return service, None


@app.route("/api/calendar/events", methods=["GET", "POST"])
def api_calendar_events():
    if request.method == "GET":
        service, err = _get_calendar_service()
        if err:
            return jsonify({"error": err})
        start = request.args.get("start")
        end = request.args.get("end")
        if not start or not end:
            return jsonify({"error": "Parámetros start y end requeridos"})
        try:
            time_min = start + "T00:00:00Z"
            time_max = end + "T23:59:59Z"
            result = service.events().list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=100,
            ).execute()
            events = []
            for item in result.get("items", []):
                start_data = item.get("start", {})
                date_str = start_data.get("dateTime", start_data.get("date", ""))
                time_str = ""
                day_str = ""
                if "T" in date_str:
                    montevideo = datetime.timezone(datetime.timedelta(hours=-3))
                    dt = datetime.datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    dt_local = dt.astimezone(montevideo)
                    day_str = dt_local.strftime("%Y-%m-%d")
                    time_str = dt_local.strftime("%H:%M")
                else:
                    day_str = date_str
                meeting_url = item.get("hangoutLink") or ""
                if not meeting_url:
                    for ep in (item.get("conferenceData") or {}).get("entryPoints", []):
                        if ep.get("entryPointType") == "video":
                            meeting_url = ep.get("uri", "")
                            break
                # Calendly puts the join URL in the location field
                if not meeting_url:
                    loc = item.get("location", "")
                    if loc.startswith("http"):
                        meeting_url = loc
                events.append({
                    "id": item.get("id"),
                    "title": item.get("summary", ""),
                    "description": item.get("description", ""),
                    "date": day_str,
                    "time": time_str,
                    "meeting_url": meeting_url,
                })
            return jsonify({"events": events})
        except Exception as e:
            return jsonify({"error": str(e)})

    # POST — create event
    service, err = _get_calendar_service()
    if err:
        return jsonify({"ok": False, "error": err})
    data = request.get_json() or {}
    title = data.get("title", "").strip()
    date = data.get("date", "")
    time = data.get("time", "")
    duration_min = int(data.get("duration_min") or 60)
    attendee_email = data.get("attendee_email", "").strip()
    description = data.get("description", "").strip()
    if not title or not date or not time:
        return jsonify({"ok": False, "error": "title, date y time requeridos"})
    try:
        start_dt = datetime.datetime.fromisoformat(f"{date}T{time}:00")
        end_dt = start_dt + datetime.timedelta(minutes=duration_min)
        import uuid
        event_body = {
            "summary": title,
            "description": description,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/Montevideo"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "America/Montevideo"},
            "conferenceData": {
                "createRequest": {
                    "requestId": str(uuid.uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
        }
        if attendee_email:
            event_body["attendees"] = [{"email": attendee_email}]
        created = service.events().insert(
            calendarId="primary",
            body=event_body,
            conferenceDataVersion=1,
            sendUpdates="all" if attendee_email else "none",
        ).execute()
        meet_url = created.get("hangoutLink", "")
        return jsonify({"ok": True, "meet_url": meet_url})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# Demo generation routes
# ---------------------------------------------------------------------------

_BTYPE = {1:"Agencia o consultora",2:"E-commerce / tienda online",3:"Servicios profesionales",4:"SaaS o software",5:"Otro"}

def _phone_variants(phone: str) -> list:
    """Return all plausible formats for a Uruguayan phone number."""
    import re as _re
    digits = _re.sub(r"[^\d]", "", phone)
    variants = set()
    variants.add(digits)
    if digits.startswith("00"):
        digits = digits[2:]
    # local 09XXXXXXX (9 digits) → 598XXXXXXXX
    if len(digits) == 9 and digits.startswith("0"):
        intl = "598" + digits[1:]
        variants.update([digits, intl, "+" + intl])
    # 8 bare digits → 598XXXXXXXX
    elif len(digits) == 8:
        intl = "598" + digits
        variants.update([digits, intl, "+" + intl])
    # already 598XXXXXXXXXX (11 digits)
    elif len(digits) == 11 and digits.startswith("598"):
        variants.update([digits, "+" + digits, "0" + digits[3:]])
    # add + prefix for everything
    for v in list(variants):
        if not v.startswith("+"):
            variants.add("+" + v)
    return list(variants)


@app.route("/api/wa/lead-by-name/<path:name>")
def api_lead_by_name(name):
    data, err = _bot_req("GET", f"leads/search?name={name}")
    if err:
        return jsonify({"error": err}), 404
    lead = data.get("lead", {})
    bt = lead.get("business_type")
    lead["rubro_hint"] = _BTYPE.get(bt, "") if bt else ""
    return jsonify({"lead": lead, "messages": data.get("messages", [])})


@app.route("/api/wa/lead-by-phone/<path:phone>")
def api_lead_by_phone(phone):
    data, err = _bot_req("GET", f"leads/phone/{phone}")
    if err:
        return jsonify({"error": err}), 404
    lead = data.get("lead", {})
    bt = lead.get("business_type")
    lead["rubro_hint"] = _BTYPE.get(bt, "") if bt else ""
    return jsonify({"lead": lead, "messages": data.get("messages", [])})


@app.route("/api/demo/generate", methods=["POST"])
def api_demo_generate():
    data = request.get_json() or {}
    business_name = data.get("business_name", "").strip()
    rubro = data.get("rubro", "").strip()
    if not business_name or not rubro:
        return jsonify({"ok": False, "error": "Nombre del negocio y rubro son obligatorios"})
    try:
        import demo_ai
        result = demo_ai.generate_and_deploy(
            phone=data.get("phone", ""),
            business_name=business_name,
            rubro=rubro,
            city=data.get("city", ""),
            client_color=data.get("client_color", ""),
            lead_name=data.get("lead_name", ""),
            messages=data.get("messages", []),
        )
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# Server startup
# ---------------------------------------------------------------------------

def run(db_path: str) -> None:
    global _db_path
    _db_path = db_path
    threading.Timer(1.2, lambda: webbrowser.open("http://localhost:5000")).start()
    app.run(port=5000, debug=False, use_reloader=False)
