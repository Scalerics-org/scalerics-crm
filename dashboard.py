import hmac
import logging
import os
import secrets
import threading
import time
import webbrowser
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, g, jsonify, redirect, render_template_string, request, session, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from database import _connect, init_db, seed_pitch_templates
from database import connect as _db_connect
from routes.leads import leads_bp
from routes.demos import demos_bp
from routes.calendar import calendar_bp
from routes.wa import wa_bp
from routes.pipeline import pipeline_bp
from routes.tasks import tasks_bp
from routes.budgets import budgets_bp
from routes.tokens import tokens_bp
from routes.meta import meta_bp, start_meta_token_monitor, start_meta_daily_import
from routes.calendly import calendly_bp
from services.auth import ALL_PANELS, enforce_panel_access, is_admin, require_admin
from services.demo_service import demo_job_handler
from services.job_service import init_worker
from services.avisos_reunion import iniciar_scheduler

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
<link rel="icon" type="image/png" href="/static/logo_icon.png">
<title>Scalerics — Acceso</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:#0a0f1a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#111827;border:1px solid #1e293b;border-radius:16px;padding:40px 36px;width:360px;max-width:92vw}
.logo-wrap{margin-bottom:28px;display:flex;flex-direction:column;align-items:flex-start;gap:10px}
.logo-wrap img{height:44px;object-fit:contain;filter:drop-shadow(0 0 8px rgba(0,136,204,.25))}
.logo-sub{font-size:.75rem;color:#475569}
label{font-size:.72rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.8px;display:block;margin-bottom:7px}
input{width:100%;background:#0a0f1a;border:1px solid #1e293b;border-radius:8px;padding:11px 14px;font-size:.92rem;color:#e2e8f0;font-family:'Inter',sans-serif;outline:none;margin-bottom:16px}
input:focus{border-color:#0088cc}
button{width:100%;background:linear-gradient(135deg,#0088cc,#3db648);color:#fff;font-size:.88rem;font-weight:700;padding:12px;border-radius:8px;border:none;cursor:pointer;font-family:'Inter',sans-serif}
button:hover{opacity:.9}
.error{background:#2a1515;border:1px solid #7f1d1d;border-radius:8px;padding:10px 14px;font-size:.8rem;color:#f87171;margin-bottom:16px}
.links{margin-top:18px;display:flex;flex-direction:column;gap:8px;align-items:center}
.links a{font-size:.78rem;color:#64748b;text-decoration:none}
.links a:hover{color:#0088cc}
.pw-wrap{position:relative;margin-bottom:16px}
.pw-wrap input{margin-bottom:0}
.pw-toggle{position:absolute;right:12px;top:50%;transform:translateY(-50%);background:none;border:none;color:#475569;cursor:pointer;padding:0;width:auto;font-size:.8rem;font-family:'Inter',sans-serif}
.pw-toggle:hover{color:#94a3b8;opacity:1}
</style>
</head>
<body>
<div class="card">
  <div class="logo-wrap">
    <img src="https://raw.githubusercontent.com/juantomasetti1/scalerics-assets/main/logo_full_alt.png" alt="Scalerics">
    <span class="logo-sub">CRM interno</span>
  </div>
  {% if error %}<div class="error">{{ error }}</div>{% endif %}
  <form method="POST">
    <label>Email</label>
    <input type="email" name="email" autocomplete="email" required autofocus>
    <label>Contraseña</label>
    <div class="pw-wrap">
      <input type="password" name="password" id="pw" autocomplete="current-password" required>
      <button type="button" class="pw-toggle" onclick="var i=document.getElementById('pw');i.type=i.type==='password'?'text':'password';this.textContent=i.type==='password'?'Ver':'Ocultar'">Ver</button>
    </div>
    <button type="submit">Ingresar</button>
  </form>
  <div class="links">
    <a href="/forgot-password">Olvidé mi contraseña</a>
    <a href="/register">Crear cuenta</a>
  </div>
</div>
</body>
</html>"""

REGISTER_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<link rel="icon" type="image/png" href="/static/logo_icon.png">
<title>Scalerics — Crear cuenta</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:#0a0f1a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#111827;border:1px solid #1e293b;border-radius:16px;padding:40px 36px;width:380px;max-width:92vw}
.logo-wrap{margin-bottom:24px;display:flex;flex-direction:column;align-items:flex-start;gap:8px}
.logo-wrap img{height:38px;object-fit:contain}
.logo-sub{font-size:.75rem;color:#475569}
label{font-size:.72rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.8px;display:block;margin-bottom:7px}
input{width:100%;background:#0a0f1a;border:1px solid #1e293b;border-radius:8px;padding:11px 14px;font-size:.92rem;color:#e2e8f0;font-family:'Inter',sans-serif;outline:none;margin-bottom:16px}
input:focus{border-color:#0088cc}
button{width:100%;background:linear-gradient(135deg,#0088cc,#3db648);color:#fff;font-size:.88rem;font-weight:700;padding:12px;border-radius:8px;border:none;cursor:pointer;font-family:'Inter',sans-serif}
button:hover{opacity:.9}
.error{background:#2a1515;border:1px solid #7f1d1d;border-radius:8px;padding:10px 14px;font-size:.8rem;color:#f87171;margin-bottom:16px}
.back{margin-top:16px;text-align:center}
.back a{font-size:.78rem;color:#64748b;text-decoration:none}
.back a:hover{color:#0088cc}
</style>
</head>
<body>
<div class="card">
  <div class="logo-wrap">
    <img src="https://raw.githubusercontent.com/juantomasetti1/scalerics-assets/main/logo_full.png" alt="Scalerics">
    <span class="logo-sub">Crear cuenta</span>
  </div>
  {% if error %}<div class="error">{{ error }}</div>{% endif %}
  <form method="POST">
    <label>Nombre</label>
    <input type="text" name="name" required autofocus>
    <label>Email</label>
    <input type="email" name="email" autocomplete="email" required>
    <label>Teléfono</label>
    <input type="tel" name="phone" required>
    <label>Contraseña</label>
    <input type="password" name="password" autocomplete="new-password" required>
    <label>Código de invitación</label>
    <input type="text" name="code" required>
    <button type="submit">Crear cuenta</button>
  </form>
  <div class="back"><a href="/login">&#8592; Volver al login</a></div>
</div>
</body>
</html>"""

FORGOT_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<link rel="icon" type="image/png" href="/static/logo_icon.png">
<title>Scalerics — Recuperar contraseña</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:#0a0f1a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#111827;border:1px solid #1e293b;border-radius:16px;padding:40px 36px;width:360px;max-width:92vw}
.logo-wrap{margin-bottom:24px}
.logo-wrap img{height:38px;object-fit:contain}
label{font-size:.72rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.8px;display:block;margin-bottom:7px}
input{width:100%;background:#0a0f1a;border:1px solid #1e293b;border-radius:8px;padding:11px 14px;font-size:.92rem;color:#e2e8f0;font-family:'Inter',sans-serif;outline:none;margin-bottom:16px}
input:focus{border-color:#0088cc}
button{width:100%;background:linear-gradient(135deg,#0088cc,#3db648);color:#fff;font-size:.88rem;font-weight:700;padding:12px;border-radius:8px;border:none;cursor:pointer;font-family:'Inter',sans-serif}
button:hover{opacity:.9}
.msg{background:#0f2a1a;border:1px solid #166534;border-radius:8px;padding:10px 14px;font-size:.82rem;color:#4ade80;margin-bottom:16px}
.back{margin-top:16px;text-align:center}
.back a{font-size:.78rem;color:#64748b;text-decoration:none}
.back a:hover{color:#0088cc}
</style>
</head>
<body>
<div class="card">
  <div class="logo-wrap">
    <img src="https://raw.githubusercontent.com/juantomasetti1/scalerics-assets/main/logo_full.png" alt="Scalerics">
  </div>
  {% if message %}<div class="msg">{{ message }}</div>{% endif %}
  {% if not message %}
  <form method="POST">
    <label>Email de tu cuenta</label>
    <input type="email" name="email" required autofocus>
    <button type="submit">Enviar link de recuperación</button>
  </form>
  {% endif %}
  <div class="back"><a href="/login">&#8592; Volver al login</a></div>
</div>
</body>
</html>"""

RESET_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<link rel="icon" type="image/png" href="/static/logo_icon.png">
<title>Scalerics — Nueva contraseña</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:#0a0f1a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#111827;border:1px solid #1e293b;border-radius:16px;padding:40px 36px;width:360px;max-width:92vw}
.logo-wrap{margin-bottom:24px}
.logo-wrap img{height:38px;object-fit:contain}
label{font-size:.72rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.8px;display:block;margin-bottom:7px}
input{width:100%;background:#0a0f1a;border:1px solid #1e293b;border-radius:8px;padding:11px 14px;font-size:.92rem;color:#e2e8f0;font-family:'Inter',sans-serif;outline:none;margin-bottom:16px}
input:focus{border-color:#0088cc}
button{width:100%;background:linear-gradient(135deg,#0088cc,#3db648);color:#fff;font-size:.88rem;font-weight:700;padding:12px;border-radius:8px;border:none;cursor:pointer;font-family:'Inter',sans-serif}
button:hover{opacity:.9}
.error{background:#2a1515;border:1px solid #7f1d1d;border-radius:8px;padding:10px 14px;font-size:.8rem;color:#f87171;margin-bottom:16px}
.back{margin-top:16px;text-align:center}
.back a{font-size:.78rem;color:#64748b;text-decoration:none}
</style>
</head>
<body>
<div class="card">
  <div class="logo-wrap">
    <img src="https://raw.githubusercontent.com/juantomasetti1/scalerics-assets/main/logo_full.png" alt="Scalerics">
  </div>
  {% if error %}<div class="error">{{ error }}</div>{% endif %}
  {% if valid %}
  <form method="POST">
    <label>Nueva contraseña</label>
    <input type="password" name="password" autocomplete="new-password" required autofocus>
    <label>Repetir contraseña</label>
    <input type="password" name="password2" autocomplete="new-password" required>
    <button type="submit">Guardar contraseña</button>
  </form>
  {% else %}
  <p style="font-size:.85rem;color:#94a3b8;margin-bottom:16px">{{ error }}</p>
  {% endif %}
  <div class="back"><a href="/forgot-password">Pedir nuevo link</a></div>
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
<link rel="icon" type="image/png" href="/static/logo_icon.png">
<title>Scalerics — CRM</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://unpkg.com/lucide@0.511.0/dist/umd/lucide.min.js"></script>
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:#0a0f1a;color:#e2e8f0;min-height:100vh;display:flex}
.sidebar{width:228px;min-height:100vh;background:#111827;border-right:1px solid #1a2d3d;display:flex;flex-direction:column;padding:20px 0;flex-shrink:0;position:fixed;top:0;bottom:0;left:0;z-index:200;transition:transform .25s ease}
.sidebar-logo{padding:16px 20px 20px;border-bottom:1px solid #1a2d3d;margin-bottom:14px}
.sidebar-logo img{height:38px;object-fit:contain;max-width:170px;filter:drop-shadow(0 0 6px rgba(0,136,204,.18))}
.nav-item{display:flex;align-items:center;gap:10px;padding:10px 20px;font-size:.85rem;font-weight:500;color:#64748b;cursor:pointer;border-left:3px solid transparent;transition:all .15s}
.nav-item:hover{color:#e2e8f0;background:#1a2d3d}
.nav-item.active{color:#fff;background:linear-gradient(90deg,rgba(0,136,204,.12),rgba(0,136,204,.03));border-left-color:#0088cc}
.nav-scroll{flex:1;overflow-y:auto;min-height:0}
.sidebar-bottom{margin-top:auto;padding:16px 20px;border-top:1px solid #1a2d3d;display:flex;flex-direction:column;gap:8px}
.run-btn{width:100%;background:linear-gradient(135deg,#0088cc,#3db648);color:#fff;font-size:.82rem;font-weight:700;padding:10px;border-radius:8px;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px}
.logout-btn{width:100%;background:transparent;border:1px solid #1a2d3d;color:#475569;font-size:.78rem;font-weight:500;padding:8px;border-radius:8px;cursor:pointer;font-family:'Inter',sans-serif}
.logout-btn:hover{color:#e2e8f0;border-color:#334155}
.sidebar-backdrop{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:199}
.main{margin-left:228px;padding:28px 32px;flex:1;min-width:0}
.panel{display:none}
.panel.active{display:block}
@media(max-width:768px){
  .sidebar{transform:translateX(-100%)}
  .sidebar.open{transform:translateX(0)}
  .sidebar-backdrop.open{display:block}
  .mobile-header{display:flex}
  .main{margin-left:0;padding:64px 14px 90px}
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
  /* ── Tareas mobile ── */
  .filter-row-1{flex-direction:column!important}
  .search-input,.upick-wrap,.upick-trigger{width:100%!important}
  .filter-row-2{gap:5px}
  .pill{font-size:.68rem;padding:5px 10px}
  .task-edit-btn,.task-del-btn{min-height:36px;padding:6px 10px}
  .task-status-badge{padding:5px 12px;font-size:.74rem}
  .task-row{padding:12px 14px;border-radius:12px}
  /* ── Calendario compacto ── */
  .cal-cell{min-height:44px!important;padding:3px 2px!important}
  .cal-event-chip{font-size:0!important;width:7px!important;height:7px!important;border-radius:50%!important;padding:0!important;min-width:0!important;display:inline-block!important;margin:1px!important}
  #cal-day-events-mobile{display:block}
  /* ── Lead cards ── */
  .table-wrap{background:transparent!important;border:none!important;border-radius:0!important;overflow:visible!important}
  .table-header{display:none!important}
  .table-row,.table-row.no-cb{
    display:flex!important;flex-direction:column!important;gap:6px;
    padding:14px 16px!important;background:#111827!important;
    border:1px solid #1e293b!important;border-radius:14px!important;
    margin-bottom:10px;grid-template-columns:none!important;
    align-items:stretch!important;
  }
  .table-row:last-child{border-bottom:1px solid #1e293b!important}
  .table-row:hover{background:#141d2e!important}
  /* no-cb: col1=nombre, col2=fuente, col3=estado, col4+=ocultar, last=acciones */
  .table-row.no-cb>div:nth-child(1){order:1}
  .table-row.no-cb>div:nth-child(2){order:3;font-size:.72rem!important;color:#64748b!important}
  .table-row.no-cb>div:nth-child(3){order:2;display:flex!important;align-items:center;gap:8px;flex-wrap:wrap}
  .table-row.no-cb>div:nth-child(4){display:none!important}
  .table-row.no-cb>div:last-child:not(:nth-child(1)):not(:nth-child(2)):not(:nth-child(3)){order:10;display:flex!important;gap:8px;flex-wrap:wrap;margin-top:4px}
  .table-row.no-cb>div:last-child button,.table-row.no-cb>div:last-child a{min-height:40px!important;flex:1}
  /* con checkbox: col1=checkbox(ocultar), col2=nombre, col3=fuente(ocultar), col4=estado, last=acciones */
  .table-row:not(.no-cb)>div:nth-child(1){display:none!important}
  .table-row:not(.no-cb)>div:nth-child(2){order:1}
  .table-row:not(.no-cb)>div:nth-child(3){display:none!important}
  .table-row:not(.no-cb)>div:nth-child(4){order:2;display:flex!important;align-items:center;gap:8px;flex-wrap:wrap}
  .table-row:not(.no-cb)>div:last-child{order:10;display:flex!important;gap:8px;flex-wrap:wrap;margin-top:4px}
  .table-row:not(.no-cb)>div:last-child button{min-height:40px!important;flex:1}
  /* Meta panel: 7 cols — ocultar 5 y 6 que son metadata extra */
  #meta-panel .table-row.no-cb>div:nth-child(5),
  #meta-panel .table-row.no-cb>div:nth-child(6){display:none!important}
  .modal{width:95vw!important;max-width:95vw!important}
  .modal-row{grid-template-columns:1fr}
  .pipeline-input-row{flex-direction:column}
  .max-input{width:100%}
  /* ── Global mobile ── */
  .main{padding:14px 14px 90px!important}
  .page-header{margin-bottom:16px}
  .page-header h1{font-size:1.1rem}
  .nav-section-label{display:none}
  /* Modales como bottom sheets */
  .modal-overlay{align-items:flex-end!important}
  .modal{border-radius:20px 20px 0 0!important;width:100%!important;max-width:100%!important;max-height:90vh;overflow-y:auto}
  .modal-row{grid-template-columns:1fr!important}
  /* Touch targets mínimo 44px */
  .filter-btn,.btn-cancel,.btn-confirm,.run-btn,.contact-btn,.pitch-btn,.mail-btn{min-height:44px}
  .cp-tabs{overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none;flex-shrink:0}
  .cp-tabs::-webkit-scrollbar{display:none}
  .cp-tab{padding:10px 14px;font-size:.76rem;white-space:nowrap;flex-shrink:0}
  /* Kanban: scroll táctil */
  .kanban-board{-webkit-overflow-scrolling:touch;scroll-snap-type:x mandatory;padding-bottom:24px}
  .kanban-col{scroll-snap-align:start}
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
.stat-card{background:#161b27;border:1px solid #1e293b;border-top:2px solid #1a2d3d;border-radius:12px;padding:18px 20px;transition:border-top-color .2s}
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

.table-header span{font-size:.65rem;font-weight:700;color:#334155;text-transform:uppercase;letter-spacing:1px}

.table-row:hover{background:#1a2234}
.table-row:last-child{border-bottom:none}
.biz-name{font-weight:600;font-size:.88rem;color:#e2e8f0;display:flex;align-items:center;gap:7px}
.biz-sub{font-size:.7rem;color:#475569;margin-top:2px}
.score-badge{display:inline-flex;align-items:center;gap:3px;font-size:.62rem;font-weight:700;padding:1px 6px;border-radius:4px;letter-spacing:.02em;flex-shrink:0}
.score-hot{background:rgba(16,185,129,.15);color:#34d399;border:1px solid rgba(16,185,129,.25)}
.score-mid{background:rgba(0,136,204,.12);color:#60a5fa;border:1px solid rgba(0,136,204,.22)}
.score-low{background:rgba(100,116,139,.1);color:#64748b;border:1px solid rgba(100,116,139,.18)}
.phone-val{font-size:.8rem;color:#4ade80;font-family:monospace;text-decoration:none}
.phone-val:hover{color:#86efac;text-decoration:underline}
.phone-plain{font-size:.8rem;color:#64748b;font-family:monospace}
.call-btn{border:1px solid #1e293b;background:none;color:#64748b;padding:2px 7px;border-radius:5px;font-size:.72rem;cursor:pointer;text-decoration:none;font-family:'Inter',sans-serif;transition:all .15s}.call-btn:hover{border-color:#334155;color:#94a3b8}
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
.export-btn{background:#1a2d1e;border:1px solid #166534;color:#4ade80;padding:7px 14px;border-radius:8px;font-size:.78rem;font-weight:600;cursor:pointer;font-family:'Inter',sans-serif}
.export-btn:hover{background:#166534;color:#fff}
.cb-col{width:36px;display:flex;align-items:center;justify-content:center}
.cb{width:15px;height:15px;accent-color:#0088cc;cursor:pointer}
.table-header{display:grid;grid-template-columns:36px 2fr 1.2fr 1.8fr 1.5fr;padding:12px 20px;background:#0f1117;border-bottom:1px solid #1e293b}
.table-row{display:grid;grid-template-columns:36px 2fr 1.2fr 1.8fr 1.5fr;padding:13px 20px;border-bottom:1px solid #1a2234;align-items:center;transition:background .1s}
.table-header.no-cb{grid-template-columns:2fr 1.1fr 1fr 1.8fr 1.2fr}
.table-row.no-cb{grid-template-columns:2fr 1.1fr 1fr 1.8fr 1.2fr}
.batch-bar{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1e293b;border:1px solid #334155;border-radius:12px;padding:10px 18px;display:none;align-items:center;gap:12px;z-index:500;box-shadow:0 4px 24px rgba(0,0,0,.5)}
.batch-bar.open{display:flex}
.batch-count{font-size:.82rem;color:#94a3b8;white-space:nowrap}
.batch-sel{background:#0f1117;border:1px solid #1e293b;border-radius:8px;padding:6px 10px;font-size:.78rem;color:#e2e8f0;font-family:'Inter',sans-serif;outline:none;cursor:pointer}
.batch-apply{background:linear-gradient(135deg,#0088cc,#3db648);color:#fff;border:none;padding:7px 16px;border-radius:8px;font-size:.78rem;font-weight:700;cursor:pointer;font-family:'Inter',sans-serif}
.batch-cancel{background:transparent;border:none;color:#475569;font-size:.78rem;cursor:pointer;font-family:'Inter',sans-serif}
.metrics-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:24px}
.metrics-grid-2{display:grid;grid-template-columns:repeat(2,1fr);gap:16px;margin-bottom:24px}
@media(max-width:768px){.metrics-grid{grid-template-columns:1fr 1fr}.metrics-grid-2{grid-template-columns:1fr}}
@media(max-width:480px){.metrics-grid{grid-template-columns:1fr}}
.m-card{background:#161b27;border:1px solid #1e293b;border-radius:14px;padding:20px 22px}
.m-card-title{font-size:.7rem;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:1px;margin-bottom:16px}
.bar-row{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.bar-label{font-size:.75rem;color:#94a3b8;width:140px;flex-shrink:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar-track{flex:1;background:#1e293b;border-radius:4px;height:8px;overflow:hidden}
.bar-fill{height:100%;border-radius:4px;background:linear-gradient(90deg,#0088cc,#3db648);transition:width .4s}
.bar-val{font-size:.72rem;color:#64748b;width:28px;text-align:right;flex-shrink:0}
.funnel-row{display:flex;align-items:center;gap:10px;margin-bottom:6px}
.funnel-label{font-size:.75rem;color:#94a3b8;width:150px;flex-shrink:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.month-bars{display:flex;align-items:flex-end;gap:4px;height:80px;margin-top:8px}
.month-col{display:flex;flex-direction:column;align-items:center;flex:1;gap:3px}
.month-bar{width:100%;border-radius:3px 3px 0 0;background:linear-gradient(180deg,#0088cc,#3db648);min-height:2px}
.month-tick{font-size:.55rem;color:#334155;white-space:nowrap}
.conv-big{font-size:2.6rem;font-weight:800;color:#4ade80;line-height:1}
.conv-sub{font-size:.75rem;color:#475569;margin-top:6px}
.delete-btn:hover{background:#7f1d1d;color:#fff}
.copy-pitch-btn{background:none;border:none;cursor:pointer;font-size:.85rem;padding:2px 4px;border-radius:4px;opacity:.6;transition:opacity .15s}.copy-pitch-btn:hover{opacity:1}
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
.cal-del-btn{display:block;width:100%;text-align:left;background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.2);color:#f87171;border-radius:3px;padding:1px 5px;font-size:.5rem;font-weight:700;letter-spacing:.03em;cursor:pointer;margin-top:2px;line-height:1.6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cal-del-btn:hover{background:rgba(239,68,68,.25)}
.cal-join-btn{display:block;width:100%;text-align:left;background:rgba(74,222,128,.1);border:1px solid rgba(74,222,128,.25);color:#4ade80;border-radius:3px;padding:1px 5px;font-size:.5rem;font-weight:700;letter-spacing:.03em;cursor:pointer;margin-top:2px;line-height:1.6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-decoration:none}
.cal-join-btn:hover{background:rgba(74,222,128,.25)}
.cal-event-chip .cal-demo-btn,.cal-event-chip .cal-del-btn,.cal-event-chip .cal-join-btn{display:none}
.cal-event-chip:hover .cal-demo-btn,.cal-event-chip:hover .cal-del-btn,.cal-event-chip:hover .cal-join-btn{display:block}
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
.modal input[type=text],.modal input[type=date],.modal input[type=time],.modal input[type=number],.modal input[type=email],.modal input[type=datetime-local],.modal-input{width:100%;background:#0f1117;border:1px solid #1e293b;border-radius:8px;padding:10px 14px;font-size:.85rem;color:#e2e8f0;font-family:'Inter',sans-serif;outline:none;margin-bottom:12px;box-sizing:border-box}
.modal select,.modal-input select{width:100%;background:#0f1117;border:1px solid #1e293b;border-radius:8px;padding:10px 14px;font-size:.85rem;color:#e2e8f0;font-family:'Inter',sans-serif;outline:none;margin-bottom:12px;appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%2364748b' stroke-width='2'%3E%3Cpolyline points='6 9 12 15 18 9'%3E%3C/polyline%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 12px center;padding-right:36px;cursor:pointer}
.modal input[type=datetime-local]::-webkit-calendar-picker-indicator{filter:invert(1);opacity:.4;cursor:pointer}
.modal input::placeholder{color:#334155}
.modal input:focus,.modal textarea:focus,.modal select:focus{border-color:#0088cc;outline:none}
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
.attach-drop{border:1.5px dashed #1e293b;border-radius:8px;padding:18px;text-align:center;color:#475569;font-size:.8rem;cursor:pointer;transition:border-color .15s,background .15s;margin-bottom:8px}
.attach-drop.dragover{border-color:#0088cc;background:#0a1628}
.attach-drop:hover{border-color:#334155}
.attach-list{display:flex;flex-direction:column;gap:6px;margin-top:8px}
.attach-item{display:flex;align-items:center;gap:8px;background:#0a0f1a;border:1px solid #1e293b;border-radius:6px;padding:7px 10px;font-size:.8rem}
.attach-item-name{flex:1;color:#e2e8f0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.attach-item-name a{color:#3b82f6;text-decoration:none}
.attach-item-name a:hover{text-decoration:underline}
.attach-del{background:none;border:none;color:#475569;cursor:pointer;font-size:.85rem;padding:0 2px;flex-shrink:0}
.attach-del:hover{color:#f87171}

.cp-event-row{display:flex;gap:10px;align-items:flex-start;padding:7px 0;border-bottom:1px solid #1a2234}
.cp-event-dot{width:8px;height:8px;border-radius:50%;margin-top:5px;flex-shrink:0}
.cp-event-body{flex:1;min-width:0}
.cp-event-label{font-size:.8rem;color:#e2e8f0;font-weight:600}
.cp-event-meta{font-size:.72rem;color:#475569;margin-left:8px}
.cp-event-note{font-size:.72rem;color:#64748b;margin-top:2px}
body.light .cp-event-row{border-bottom-color:#f1f5f9}
body.light .cp-event-label{color:#0f172a !important}
body.light .cp-event-meta{color:#94a3b8 !important}
body.light .cp-event-note{color:#64748b !important}
.act-row{display:flex;gap:14px;align-items:flex-start;padding:13px 0;border-bottom:1px solid #1e293b}
.act-avatar{width:34px;height:34px;border-radius:50%;background:#1a2234;display:flex;align-items:center;justify-content:center;font-size:.95rem;flex-shrink:0}
.act-body{flex:1;min-width:0}
.act-user{font-weight:600;color:#e2e8f0}
.act-sep{color:#64748b}
.act-desc{color:#94a3b8;font-size:.85rem}
.act-when{font-size:.72rem;color:#475569;margin-top:3px}
.act-entity-link{cursor:pointer;color:#38bdf8;text-decoration:underline;text-decoration-color:rgba(56,189,248,.35)}
.sdr-detail-row{display:flex;align-items:center;gap:10px;padding:9px 8px;border-radius:8px;cursor:pointer;transition:background .12s}
.sdr-detail-row:hover{background:#1e293b}
.act-entity-link:hover{color:#7dd3fc}
.no-answer-badge{display:inline-flex;align-items:center;gap:3px;background:rgba(239,68,68,.15);color:#f87171;font-size:.65rem;font-weight:700;padding:2px 6px;border-radius:99px;border:1px solid rgba(239,68,68,.3)}
.no-interest-badge{display:inline-flex;align-items:center;gap:3px;background:rgba(245,158,11,.15);color:#fbbf24;font-size:.65rem;font-weight:700;padding:2px 6px;border-radius:99px;border:1px solid rgba(245,158,11,.3)}
body.light .no-answer-badge{background:rgba(239,68,68,.1);color:#dc2626;border-color:rgba(239,68,68,.25)}
body.light .no-interest-badge{background:rgba(245,158,11,.1);color:#b45309;border-color:rgba(245,158,11,.25)}
/* ── Row status colors ────────────────────────────────────────────────────── */
.row-sin_contactar{border-left:3px solid transparent}
.row-no_interesa{border-left:3px solid #ef4444;background:rgba(239,68,68,.05)}
.row-llamar_despues{border-left:3px solid #f59e0b;background:rgba(245,158,11,.05)}
.row-interesado{border-left:3px solid #10b981;background:rgba(16,185,129,.05)}
.row-contactado{border-left:3px solid #60a5fa;background:rgba(96,165,250,.04)}
.row-reunion_agendada{border-left:3px solid #3b82f6;background:rgba(59,130,246,.06)}
.row-demo_generada{border-left:3px solid #a78bfa;background:rgba(167,139,250,.06)}
.row-reunion_hecha{border-left:3px solid #14b8a6;background:rgba(20,184,166,.06)}
.row-presupuesto_enviado{border-left:3px solid #f97316;background:rgba(249,115,22,.06)}
.row-negociacion{border-left:3px solid #fbbf24;background:rgba(251,191,36,.06)}
.row-cliente_cerrado{border-left:3px solid #10b981;background:rgba(16,185,129,.07)}
.row-en_desarrollo{border-left:3px solid #0088cc;background:rgba(0,136,204,.06)}
.row-finalizado{border-left:3px solid #6ee7b7;background:rgba(110,231,183,.06)}
/* light mode row colors */
body.light .row-no_interesa{background:rgba(239,68,68,.07)}
body.light .row-llamar_despues{background:rgba(245,158,11,.07)}
body.light .row-contactado{background:rgba(96,165,250,.07)}
body.light .row-reunion_agendada{background:rgba(59,130,246,.08)}
body.light .row-demo_generada{background:rgba(167,139,250,.08)}
body.light .row-reunion_hecha{background:rgba(20,184,166,.08)}
body.light .row-presupuesto_enviado{background:rgba(249,115,22,.08)}
body.light .row-negociacion{background:rgba(251,191,36,.08)}
body.light .row-cliente_cerrado{background:rgba(16,185,129,.1)}
body.light .row-en_desarrollo{background:rgba(0,136,204,.08)}
body.light .row-finalizado{background:rgba(110,231,183,.1)}
/* ── Calendar light mode ──────────────────────────────────────────────────── */
body.light .cal-header h1{color:#0f172a !important}
body.light .cal-nav-btn{background:#f1f5f9;border:1px solid #e2e8f0;color:#475569}
body.light .cal-nav-btn:hover{background:#e2e8f0;color:#0f172a}
body.light .cal-grid{background:#e2e8f0}
body.light .cal-grid-header{background:#f8fafc;color:#94a3b8}
body.light .cal-cell{background:#fff}
body.light .cal-cell.other-month{background:#f8fafc}
body.light .cal-cell.today{background:#eff6ff}
body.light .cal-cell-day{color:#64748b}
body.light .cal-cell.today .cal-cell-day{color:#fff;background:#0088cc}
body.light .cal-event-chip.regular{background:#dbeafe;color:#1d4ed8}
body.light .cal-event-chip.meet{background:#dcfce7;color:#15803d}
body.light .cal-demo-btn{background:rgba(6,182,212,.08);color:#0e7490;border-color:rgba(6,182,212,.2)}
body.light .cal-del-btn{background:rgba(239,68,68,.07);color:#dc2626;border-color:rgba(239,68,68,.18)}
body.light .cal-join-btn{background:rgba(22,163,74,.08);color:#15803d;border-color:rgba(22,163,74,.2)}
body.light .cal-loading{color:#94a3b8}
/* ── Metrics light mode ───────────────────────────────────────────────────── */
body.light .metrics-card{background:#fff;border-color:#e2e8f0}
body.light .metrics-card-title{color:#64748b !important}
body.light .metrics-card-val{color:#0f172a !important}
body.light .bar-label{color:#475569 !important}
body.light .bar-val{color:#475569 !important}
body.light .bar-track{background:#f1f5f9}
body.light .funnel-label{color:#475569 !important}
body.light .funnel-val,.bar-val{color:#475569}
body.light .metrics-section-title{color:#64748b !important}
#nav-meta .nav-icon{stroke:#e1306c}
body.light #nav-meta .nav-icon{stroke:#c13584}
/* ── Nav icon colors ──────────────────────────────────────────────────────── */
#nav-cola .nav-icon{stroke:#60a5fa}
#nav-seguimientos .nav-icon{stroke:#f59e0b}
#nav-pipeline .nav-icon{stroke:#10b981}
#nav-clientes .nav-icon{stroke:#a78bfa}
#nav-tasks .nav-icon{stroke:#14b8a6}
#nav-wa .nav-icon{stroke:#25d366}
#nav-cal .nav-icon{stroke:#3b82f6}
#nav-metrics .nav-icon{stroke:#6366f1}
#nav-activity .nav-icon{stroke:#64748b}
.nav-item.active #nav-cola .nav-icon,
.nav-item.active .nav-icon{opacity:1}
/* active item keeps its color but brighter */
#nav-cola.active .nav-icon{stroke:#93c5fd}
#nav-seguimientos.active .nav-icon{stroke:#fcd34d}
#nav-pipeline.active .nav-icon{stroke:#34d399}
#nav-clientes.active .nav-icon{stroke:#c4b5fd}
#nav-tasks.active .nav-icon{stroke:#2dd4bf}
#nav-wa.active .nav-icon{stroke:#4ade80}
#nav-cal.active .nav-icon{stroke:#60a5fa}
#nav-metrics.active .nav-icon{stroke:#818cf8}
#nav-activity.active .nav-icon{stroke:#94a3b8}
/* light mode — slightly darker tones */
body.light #nav-cola .nav-icon{stroke:#2563eb}
body.light #nav-seguimientos .nav-icon{stroke:#d97706}
body.light #nav-pipeline .nav-icon{stroke:#059669}
body.light #nav-clientes .nav-icon{stroke:#7c3aed}
body.light #nav-tasks .nav-icon{stroke:#0d9488}
body.light #nav-wa .nav-icon{stroke:#16a34a}
body.light #nav-cal .nav-icon{stroke:#2563eb}
body.light #nav-metrics .nav-icon{stroke:#4f46e5}
body.light #nav-activity .nav-icon{stroke:#475569}
/* ── Lucide icons ─────────────────────────────────────────────────────────── */
.nav-icon{width:15px;height:15px;stroke-width:2;flex-shrink:0}
.btn-icon{width:14px;height:14px;stroke-width:2;vertical-align:middle}
.outcome-icon{width:22px;height:22px;stroke-width:1.8;display:block;margin:0 auto 4px}
/* ── Theme toggle ─────────────────────────────────────────────────────────── */
.theme-btn{width:100%;background:transparent;border:1px solid #1a2d3d;color:#475569;font-size:.78rem;font-weight:500;padding:8px 12px;border-radius:8px;cursor:pointer;font-family:'Inter',sans-serif;display:flex;align-items:center;gap:8px;transition:all .15s}
.theme-btn:hover{color:#e2e8f0;border-color:#334155}
/* ── Light mode ────────────────────────────────────────────────────────────── */
body.light{background:#f1f5f9;color:#0f172a}
body.light .sidebar{background:#fff;border-right-color:#e2e8f0}
body.light .sidebar-logo{border-bottom-color:#e2e8f0}
body.light .nav-section-label{color:#94a3b8}
body.light .nav-item{color:#64748b}
body.light .nav-item:hover{color:#0f172a;background:#f1f5f9}
body.light .nav-item.active{color:#0f172a;background:linear-gradient(90deg,rgba(0,136,204,.1),rgba(0,136,204,.02))}
body.light .sidebar-bottom{border-top-color:#e2e8f0}
body.light .theme-btn{border-color:#e2e8f0;color:#64748b}
body.light .theme-btn:hover{color:#0f172a;border-color:#94a3b8}
body.light .logout-btn{border-color:#e2e8f0;color:#64748b}
body.light .logout-btn:hover{color:#0f172a;border-color:#94a3b8}
body.light .page-header h1{color:#0f172a}
body.light .page-date{color:#64748b}
body.light .stat-card{background:#fff;border-color:#e2e8f0}
body.light .stat-label{color:#64748b}
body.light .stat-val{color:#0f172a}
body.light .stat-val.blue{color:#0088cc}
body.light .stat-val.green{color:#16a34a}
body.light .filters{background:transparent}
body.light .filter-btn{background:#fff;border-color:#e2e8f0;color:#475569}
body.light .filter-btn:hover,body.light .filter-btn.active{background:#0088cc;color:#fff;border-color:#0088cc}
body.light .filter-select{background:#fff;border-color:#e2e8f0;color:#0f172a}
body.light .filter-select option{background:#fff;color:#0f172a}
body.light .search-box{background:#fff;border-color:#e2e8f0;color:#0f172a}
body.light .search-box::placeholder{color:#94a3b8}
body.light .table-wrap{background:#fff;border-color:#e2e8f0}
body.light .table-header{background:#f8fafc;border-bottom-color:#e2e8f0}
body.light .table-header span{color:#94a3b8}
body.light .table-row{border-bottom-color:#f1f5f9}
body.light .table-row:hover{background:#f8fafc}
body.light .biz-name,.biz-name{font-weight:600}
body.light .biz-name a,.biz-name a{color:#0f172a}
body.light .biz-sub{color:#64748b}
body.light .phone-val{color:#0088cc}
body.light .phone-plain{color:#475569}
body.light .no-val{color:#94a3b8}
body.light .notes-inline{color:#64748b}
body.light .notes-inline:hover{background:#f1f5f9;border-bottom-color:#94a3b8}
body.light .notes-inline:focus{background:#f1f5f9;border-bottom-color:#0088cc;color:#0f172a}
body.light .notes-inline::placeholder{color:#cbd5e1}
body.light .score-badge{filter:brightness(.9)}
body.light .pitch-btn{background:#e0f2fe;border-color:#0088cc;color:#0369a1}
body.light .pitch-btn:hover{background:#0088cc;color:#fff}
body.light .delete-btn{color:#94a3b8}
body.light .delete-btn:hover{color:#ef4444}
body.light .export-btn{background:#f1f5f9;border-color:#e2e8f0;color:#475569}
body.light .modal-overlay .modal{background:#fff;border-color:#e2e8f0;color:#0f172a}
body.light .modal h3,.modal-title{color:#0f172a}
body.light .modal label{color:#64748b}
body.light .modal input,body.light .modal textarea,body.light .modal select,body.light .modal-input{background:#f8fafc;border-color:#e2e8f0;color:#0f172a}
body.light .modal input[type=datetime-local]::-webkit-calendar-picker-indicator{filter:none;opacity:.6}
body.light .outcome-btn{background:#f8fafc;border-color:#e2e8f0;color:#0f172a}
body.light .outcome-btn:hover{background:#f1f5f9}
body.light .client-panel{background:#fff;border-left-color:#e2e8f0}
body.light .cp-tab{color:#64748b;border-bottom-color:transparent}
body.light .cp-tab.active{color:#0088cc;border-bottom-color:#0088cc}
body.light .cb-overdue{background:#fef2f2;border-color:#fca5a5 !important}
body.light .cb-today{background:#fefce8;border-color:#fde047 !important}
body.light ::-webkit-scrollbar-thumb{background:#e2e8f0}
body.light ::-webkit-scrollbar-thumb:hover{background:#94a3b8}
body.light .page-header h1{color:#0f172a !important}
body.light .stat-val{color:#0f172a !important}
body.light .stat-val.green{color:#16a34a !important}
body.light .stat-val.blue{color:#0088cc !important}
body.light .stat-val.yellow{color:#b45309 !important}
body.light .biz-name{color:#0f172a !important}
body.light .biz-sub{color:#64748b !important}
body.light .score-badge{color:#475569 !important}
body.light .phone-val{color:#0088cc !important}
body.light .phone-plain{color:#475569 !important}
body.light .no-val{color:#94a3b8 !important}
body.light .actions .pitch-btn{background:#e0f2fe;color:#0369a1;border-color:#bae6fd}
body.light .actions .pitch-btn:hover{background:#0088cc;color:#fff}
body.light .cp-title{color:#0f172a !important}
body.light .cp-field-label{color:#64748b !important}
body.light .cp-field-val{color:#0f172a !important}
body.light .cp-meeting-title{color:#0f172a !important}
body.light .cp-close{color:#94a3b8}
body.light .cp-close:hover{color:#0f172a}
body.light .cp-tab{color:#64748b}
body.light .cp-tab.active{color:#0088cc;border-bottom-color:#0088cc}
body.light .budget-table td{color:#0f172a !important}
body.light .budget-total-row td{color:#0f172a !important}
body.light .budget-table{border-color:#e2e8f0}
body.light .budget-table th{color:#64748b !important;background:#f8fafc}
body.light .attach-item{background:#f8fafc;border-color:#e2e8f0}
body.light .attach-item-name{color:#0f172a !important}
body.light .batch-count{color:#0f172a}
body.light .empty-state{color:#94a3b8}
body.light .bar-label,.body.light .funnel-label{color:#475569}
body.light .wa-lead-name{color:#0f172a !important}
body.light .wa-lead-phone{color:#64748b}
body.light .wa-chat-name{color:#0f172a}
body.light .wa-chat-phone{color:#64748b}
body.light .wa-list{background:#f8fafc;border-right-color:#e2e8f0}
body.light .wa-list-header{color:#64748b;border-bottom-color:#e2e8f0}
body.light .wa-lead-item{border-bottom-color:#f1f5f9}
body.light .wa-lead-item:hover,.body.light .wa-lead-item.active{background:#f1f5f9}
body.light .wa-chat{background:#fff}
body.light .task-row{background:#fff;border-color:#e2e8f0}
body.light .task-row:hover{border-color:#94a3b8}
body.light .task-title{color:#0f172a !important}
body.light .task-meta{color:#64748b}
body.light .task-check{border-color:#94a3b8}
body.light .search-input{background:#fff;border-color:#e2e8f0;color:#0f172a}
body.light .search-input::placeholder{color:#94a3b8}
body.light .user-select{background:#fff;border-color:#e2e8f0;color:#0f172a}
body.light .pill{background:#f8fafc;border-color:#e2e8f0;color:#475569}
body.light .pill:hover{color:#0f172a}
body.light .pill.active{background:#dbeafe;color:#1d4ed8;border-color:#93c5fd}
body.light .task-status-badge.todo{background:#f1f5f9;color:#64748b}
body.light .task-status-badge.in_progress{background:#dbeafe;color:#1d4ed8;border-color:#93c5fd}
body.light .task-status-badge.done{background:#dcfce7;color:#16a34a;border-color:#86efac}
body.light .tasks-summary{color:#94a3b8}
body.light .mobile-bottom-nav{background:rgba(255,255,255,.92);border-color:rgba(0,0,0,.1)}
body.light .mbn-icon{stroke:#94a3b8}
body.light .mbn-label{color:#94a3b8}
body.light .mbn-item.active{background:rgba(0,136,204,.12)}
body.light .mbn-item.active .mbn-icon{stroke:#0088cc}
body.light .mbn-item.active .mbn-label{color:#0088cc}
body.light .mas-sheet{background:#fff;border-top-color:#e2e8f0}
body.light .mas-sheet-handle{background:#e2e8f0}
body.light .mas-sheet-title{color:#94a3b8}
body.light .mas-sheet-item{background:#f8fafc;border-color:#e2e8f0}
body.light .mas-sheet-icon{stroke:#475569}
body.light .mas-sheet-label{color:#0f172a}
body.light .mas-sheet-backdrop{background:rgba(0,0,0,.3)}
body.light .modal h3,body.light .modal-title{color:#0f172a !important}
body.light .modal label{color:#475569}
body.light .modal p{color:#475569}
body.light .modal input,body.light .modal textarea,body.light .modal select{background:#f8fafc;border-color:#e2e8f0;color:#0f172a}
body.light .token-card{background:#f8fafc;border-color:#e2e8f0}
body.light .token-card-name{color:#64748b !important}
body.light .cp-event-row{display:flex;gap:10px;align-items:flex-start;padding:7px 0;border-bottom:1px solid #1a2234}
.cp-event-dot{width:8px;height:8px;border-radius:50%;margin-top:5px;flex-shrink:0}
.cp-event-body{flex:1;min-width:0}
.cp-event-label{font-size:.8rem;color:#e2e8f0;font-weight:600}
.cp-event-meta{font-size:.72rem;color:#475569;margin-left:8px}
.cp-event-note{font-size:.72rem;color:#64748b;margin-top:2px}
body.light .cp-event-row{border-bottom-color:#f1f5f9}
body.light .cp-event-label{color:#0f172a !important}
body.light .cp-event-meta{color:#94a3b8 !important}
body.light .cp-event-note{color:#64748b !important}
.act-row{border-bottom-color:#f1f5f9}
body.light .act-avatar{background:#f1f5f9;color:#475569}
body.light .act-user{color:#0f172a !important}
body.light .act-sep{color:#94a3b8}
body.light .act-desc{color:#475569 !important}
body.light .act-when{color:#94a3b8 !important}
body.light .nav-icon{stroke:#64748b}
body.light .nav-item.active .nav-icon{stroke:#0088cc}
body.light .btn-icon{stroke:currentColor}
/* ── Scrollbars ───────────────────────────────────────────────────────────── */
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:#1e293b;border-radius:99px}
::-webkit-scrollbar-thumb:hover{background:#334155}
/* ── Nav section labels ───────────────────────────────────────────────────── */
.nav-section-label{padding:14px 20px 4px;font-size:.65rem;font-weight:700;color:#334155;text-transform:uppercase;letter-spacing:1px}
/* ── Notes inline ─────────────────────────────────────────────────────────── */
.notes-inline{width:100%;background:transparent;border:none;border-bottom:1px solid transparent;color:#94a3b8;font-size:.78rem;font-family:'Inter',sans-serif;resize:none;outline:none;min-height:22px;line-height:1.4;padding:2px 4px;border-radius:4px;transition:all .15s}
.notes-inline:hover{background:#0d1525;border-bottom-color:#1e293b}
.notes-inline:focus{background:#0d1525;border-bottom-color:#0088cc;color:#e2e8f0}
.notes-inline::placeholder{color:#334155}
/* ── Call outcome buttons ─────────────────────────────────────────────────── */
.outcome-btn{padding:12px 8px;border:1px solid #1e293b;border-radius:10px;background:#111827;color:#e2e8f0;font-size:.82rem;font-weight:600;cursor:pointer;font-family:'Inter',sans-serif;transition:all .15s;text-align:center;line-height:1.4}
.outcome-btn:hover{transform:translateY(-1px);box-shadow:0 4px 12px rgba(0,0,0,.3)}
.outcome-no-answer:hover{background:#1e293b;border-color:#475569}
.outcome-not-interested:hover{background:#2a1515;border-color:#f87171;color:#f87171}
.outcome-callback:hover{background:#1a2d3d;border-color:#60a5fa;color:#60a5fa}
.outcome-interested:hover{background:#0f2a1a;border-color:#4ade80;color:#4ade80}
.outcome-meeting:hover{background:#0d1f35;border-color:#3b82f6;color:#60a5fa}
.outcome-contacted:hover{background:#1a2d3d;border-color:#0088cc;color:#0088cc}
/* ── Callback urgency ─────────────────────────────────────────────────────── */
.cb-overdue{background:#2a1515;border-color:#7f1d1d !important}
.cb-today{background:#1a1a0f;border-color:#ca8a04 !important}
.cb-date-pill{display:inline-block;padding:2px 8px;border-radius:99px;font-size:.72rem;font-weight:600}
.cb-date-overdue{background:#2a1515;color:#f87171}
.cb-date-today{background:#1a1a0f;color:#fbbf24}
.cb-date-future{background:#0f1f35;color:#60a5fa}
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

/* ── Token health panel ───────────────────────────────────────────────────── */
.token-health{margin-bottom:20px}
.token-health-title{font-size:.7rem;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.7px;margin-bottom:10px}
.token-cards{display:flex;gap:10px;flex-wrap:wrap}
.token-card{background:#111827;border:1px solid #1e293b;border-radius:10px;padding:12px 16px;min-width:140px;flex:1}
.token-card-name{font-size:.75rem;font-weight:600;color:#94a3b8;margin-bottom:4px}
.token-card-label{font-size:.85rem;font-weight:700}
.token-card.ok .token-card-label{color:#4ade80}
.token-card.warning .token-card-label{color:#fbbf24}
.token-card.danger .token-card-label{color:#f87171}
.token-card.permanent .token-card-label{color:#60a5fa}
.token-card.unknown .token-card-label{color:#475569}
/* ── Tasks panel ──────────────────────────────────────────────────────────── */
.filter-bar{display:flex;flex-direction:column;gap:10px;margin-bottom:14px}
.filter-row-1{display:flex;gap:8px}
.filter-row-2{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.search-input{flex:1;background:#111827;border:1px solid #1e293b;color:#e2e8f0;border-radius:8px;padding:7px 12px;font-size:.82rem}
.search-input::placeholder{color:#334155}
.user-select{background:#111827;border:1px solid #1e293b;color:#e2e8f0;border-radius:8px;padding:7px 12px;font-size:.82rem;min-width:150px}
.pill{padding:4px 12px;border-radius:99px;font-size:.72rem;font-weight:600;cursor:pointer;border:1px solid #1e293b;background:#111827;color:#64748b;transition:all .15s;white-space:nowrap}
.pill:hover{color:#e2e8f0}
.pill.active{background:#0088cc22;color:#38bdf8;border-color:#0088cc44}
.pill.warn{border-color:#450a0a}
.pill.warn.active{background:#450a0a22;color:#f87171;border-color:#450a0a}
.pill.orange{border-color:#431407}
.pill.orange.active{background:#431407;color:#fb923c;border-color:#9a3412}
.pill-count{font-weight:400;color:#334155;margin-left:3px;font-size:.68rem}
.pill.active .pill-count{color:#0088cc99}
.tasks-summary{font-size:.75rem;color:#475569;margin-bottom:10px}
.task-status-badge{padding:3px 9px;border-radius:99px;font-size:.68rem;font-weight:700;cursor:pointer;transition:all .15s;border:1px solid transparent;user-select:none}
.task-status-badge.todo{background:#1e293b;color:#64748b}
.task-status-badge.in_progress{background:#0c1f2e;color:#38bdf8;border-color:#0369a133}
.task-status-badge.done{background:#052e16;color:#4ade80;border-color:#16a34a33}
.task-row.in-progress{border-left:3px solid #0369a1}
.task-row.overdue{border-left:3px solid #f87171}
.task-edit-btn{background:none;border:1px solid #1e293b;color:#64748b;cursor:pointer;font-size:.78rem;padding:3px 7px;border-radius:6px;transition:all .15s}
.task-edit-btn:hover{border-color:#334155;color:#94a3b8}
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
/* Mobile header */
.mobile-header{display:none;position:fixed;top:0;left:0;right:0;height:52px;background:rgba(17,24,39,.95);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border-bottom:1px solid rgba(255,255,255,.07);z-index:250;align-items:center;padding:0 16px;gap:12px}
.mobile-header img{height:24px;object-fit:contain}
.mobile-header-title{flex:1;font-size:.88rem;font-weight:700;color:#f1f5f9}
body.light .mobile-header{background:rgba(255,255,255,.95);border-bottom-color:#e2e8f0}
body.light .mobile-header-title{color:#0f172a}
/* Add-task modal */
#add-task-modal .modal{width:440px}
/* Mobile bottom navigation */
.mobile-bottom-nav{display:none}
.mbn-item{display:flex;flex-direction:column;align-items:center;gap:3px;padding:6px 10px;border-radius:12px;cursor:pointer;min-width:44px;min-height:44px;justify-content:center;transition:background .15s}
.mbn-item.active{background:rgba(0,136,204,.2)}
.mbn-icon{width:20px;height:20px;stroke:#64748b;stroke-width:1.8;fill:none;transition:stroke .15s;flex-shrink:0}
.mbn-item.active .mbn-icon{stroke:#38bdf8}
.mbn-label{font-size:.42rem;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.05em;line-height:1}
.mbn-item.active .mbn-label{color:#38bdf8}
/* Más sheet */
.mas-sheet-backdrop{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:350}
.mas-sheet-backdrop.open{display:block}
.mas-sheet{position:fixed;bottom:0;left:0;right:0;background:#111827;border-radius:20px 20px 0 0;border-top:1px solid #1e293b;padding:12px 20px 40px;z-index:351;transform:translateY(100%);transition:transform .3s cubic-bezier(.32,.72,0,1)}
.mas-sheet.open{transform:translateY(0)}
.mas-sheet-handle{width:40px;height:4px;background:#334155;border-radius:4px;margin:0 auto 16px}
.mas-sheet-title{font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#475569;margin-bottom:14px}
.mas-sheet-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.mas-sheet-item{display:flex;align-items:center;gap:12px;padding:14px 16px;background:#1e293b;border-radius:12px;cursor:pointer;border:1px solid #334155;min-height:44px;transition:background .12s}
.mas-sheet-item:active{background:#0c1a2e}
.mas-sheet-icon{width:22px;height:22px;stroke:#64748b;stroke-width:1.8;fill:none;flex-shrink:0}
.mas-sheet-label{font-size:.82rem;font-weight:600;color:#e2e8f0}
/* Mobile FAB */
.mobile-fab{display:none;position:fixed;bottom:88px;right:20px;width:52px;height:52px;border-radius:50%;background:#0088cc;border:none;color:#fff;align-items:center;justify-content:center;box-shadow:0 4px 16px rgba(0,136,204,.4);cursor:pointer;z-index:250;font-size:1.4rem;font-weight:300;line-height:1}
@media(max-width:768px){
  .mobile-bottom-nav{display:flex;position:fixed;bottom:16px;left:16px;right:16px;background:rgba(17,24,39,.92);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,.08);border-radius:20px;padding:8px 6px;z-index:300;justify-content:space-around;box-shadow:0 8px 32px rgba(0,0,0,.5)}
  .mobile-fab{display:flex}
  /* Client panel como bottom sheet */
  .client-panel{width:100%!important;height:92vh;top:auto!important;border-radius:20px 20px 0 0;border-left:none!important;border-top:1px solid #1e293b;transform:translateY(100%)!important;transition:transform .3s cubic-bezier(.32,.72,0,1)!important}
  .client-panel.open{transform:translateY(0)!important}
  .cp-header::before{content:'';display:block;width:40px;height:4px;background:#334155;border-radius:4px;margin:0 auto 12px}
}
/* Custom user picker */
.upick-wrap{position:relative}
.upick-trigger{display:flex;align-items:center;gap:8px;background:#0a0f1a;border:1px solid #334155;border-radius:8px;padding:8px 12px;cursor:pointer;transition:border-color .15s;user-select:none}
.upick-trigger:hover{border-color:#0088cc55}
.upick-trigger.open{border-color:#0088cc}
.upick-label{flex:1;font-size:.82rem;color:#e2e8f0}
.upick-chevron{color:#475569;font-size:.7rem;transition:transform .15s}
.upick-trigger.open .upick-chevron{transform:rotate(180deg)}
.upick-dropdown{position:absolute;top:calc(100% + 6px);left:0;right:0;background:#111827;border:1px solid #334155;border-radius:10px;overflow:hidden;z-index:200;box-shadow:0 8px 24px rgba(0,0,0,.4)}
.upick-option{display:flex;align-items:center;gap:10px;padding:9px 12px;cursor:pointer;transition:background .12s}
.upick-option:hover{background:#1a2234}
.upick-option.upick-sel{background:#0c1a2e}
.upick-av{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:.65rem;font-weight:800;flex-shrink:0;color:#fff}
.upick-name{font-size:.82rem;color:#e2e8f0;font-weight:500;flex:1}
.upick-check{color:#0088cc;font-size:.8rem;font-weight:700}
body.light .upick-trigger{background:#fff;border-color:#e2e8f0}
body.light .upick-dropdown{background:#fff;border-color:#e2e8f0;box-shadow:0 8px 24px rgba(0,0,0,.12)}
body.light .upick-option:hover{background:#f8fafc}
body.light .upick-option.upick-sel{background:#eff6ff}
body.light .upick-label{color:#0f172a}
body.light .upick-name{color:#0f172a}

/* Avisos de error de la API. Antes un fallo de red o un 500 no mostraba nada:
   el usuario se quedaba con un spinner colgado sin saber que habia pasado. */
#avisos{position:fixed;top:16px;right:16px;z-index:9999;display:flex;flex-direction:column;
        gap:8px;max-width:min(380px,calc(100vw - 32px));pointer-events:none}
.aviso{pointer-events:auto;background:#1e293b;border:1px solid #334155;border-left:3px solid #64748b;
       border-radius:8px;padding:11px 14px;font-size:.82rem;color:#e2e8f0;line-height:1.45;
       box-shadow:0 8px 24px rgba(0,0,0,.35);animation:avisoIn .18s ease-out}
.aviso-error{border-left-color:#ef4444}
.aviso-warn{border-left-color:#f59e0b}
.aviso-titulo{font-weight:700;margin-bottom:2px}
.aviso-cerrar{float:right;background:none;border:none;color:#64748b;cursor:pointer;
              font-size:1rem;line-height:1;padding:0 0 0 10px;font-family:inherit}
.aviso-cerrar:hover{color:#e2e8f0}
@keyframes avisoIn{from{opacity:0;transform:translateX(12px)}to{opacity:1;transform:none}}
body.light .aviso{background:#fff;border-color:#e2e8f0;color:#0f172a}

/* Cortina de sesion vencida */
#sesion-vencida{display:none;position:fixed;inset:0;z-index:10000;background:rgba(2,6,23,.82);
                backdrop-filter:blur(3px);align-items:center;justify-content:center}
#sesion-vencida .caja{background:#0f172a;border:1px solid #1e293b;border-radius:12px;
                      padding:28px 32px;max-width:380px;text-align:center;color:#e2e8f0}
#sesion-vencida h3{margin:0 0 8px;font-size:1.05rem}
#sesion-vencida p{margin:0 0 18px;font-size:.85rem;color:#94a3b8;line-height:1.5}
#sesion-vencida a{display:inline-block;background:#0088cc;color:#fff;text-decoration:none;
                  padding:8px 20px;border-radius:8px;font-size:.85rem;font-weight:600}
</style>
</head>
<body>
<div class="sidebar-backdrop" id="sidebar-backdrop" onclick="closeSidebar()"></div>
<header class="mobile-header" id="mobile-header">
  <img id="mobile-header-logo" src="https://raw.githubusercontent.com/juantomasetti1/scalerics-assets/main/logo_full_alt.png" alt="Scalerics">
  <span class="mobile-header-title" id="mobile-header-title"></span>
</header>
<nav class="mobile-bottom-nav" id="mobile-bottom-nav"></nav>
<div class="mas-sheet-backdrop" id="mas-sheet-backdrop" onclick="closeMasSheet()"></div>
<div class="mas-sheet" id="mas-sheet">
  <div class="mas-sheet-handle"></div>
  <div class="mas-sheet-title">Más secciones</div>
  <div class="mas-sheet-grid" id="mas-sheet-grid"></div>
</div>
<div class="sidebar" id="sidebar">
  <div class="sidebar-logo">
    <img id="sidebar-logo" src="https://raw.githubusercontent.com/juantomasetti1/scalerics-assets/main/logo_full_alt.png" alt="Scalerics">
  </div>
  <div class="nav-scroll">
  <div class="nav-section-label">LLAMADAS</div>
  <div class="nav-item active" id="nav-cola" onclick="showPanel('cola')"><i data-lucide="inbox" class="nav-icon"></i> Cola</div>
  <div class="nav-item" id="nav-seguimientos" onclick="showPanel('seguimientos')"><i data-lucide="bookmark" class="nav-icon"></i> Seguimientos</div>
  <div class="nav-item" id="nav-meta" onclick="showPanel('meta');clearMetaBadge()"><i data-lucide="instagram" class="nav-icon"></i> Meta Ads <span id="meta-badge" style="display:none;background:#e1306c;color:#fff;font-size:.65rem;font-weight:700;padding:1px 6px;border-radius:10px;margin-left:4px">NEW</span></div>
  <div class="nav-section-label">VENTAS</div>
  <div class="nav-item" id="nav-pipeline" onclick="showPanel('pipeline')"><i data-lucide="trending-up" class="nav-icon"></i> Proceso de venta</div>
  <div class="nav-item" id="nav-clientes" onclick="showPanel('clientes')"><i data-lucide="users" class="nav-icon"></i> Clientes</div>
  <div class="nav-section-label">GESTIÓN</div>
  <div class="nav-item" id="nav-tasks" onclick="showPanel('tasks')"><i data-lucide="check-square" class="nav-icon"></i> Tareas</div>
  <div class="nav-item" id="nav-wa" onclick="showPanel('wa')"><i data-lucide="message-circle" class="nav-icon"></i> WhatsApp</div>
  <div class="nav-item" id="nav-cal" onclick="showPanel('cal')"><i data-lucide="calendar" class="nav-icon"></i> Calendario</div>
  <div class="nav-item" id="nav-metrics" onclick="showPanel('metrics')"><i data-lucide="bar-chart-2" class="nav-icon"></i> Métricas</div>
  <div class="nav-item" id="nav-activity" onclick="showPanel('activity')"><i data-lucide="clock" class="nav-icon"></i> Actividad</div>
  <div class="nav-item" id="nav-sdr" onclick="showPanel('sdr')"><i data-lucide="phone-call" class="nav-icon"></i> SDR</div>
  </div>
  <div class="sidebar-bottom">
    <a id="admin-link" href="/admin/users" style="display:none;background:none;border:1px solid #1e293b;border-radius:8px;padding:6px 12px;font-size:.75rem;color:#64748b;cursor:pointer;width:100%;text-align:left;text-decoration:none;box-sizing:border-box">&#9881; Usuarios</a>
    <a href="/profile" style="background:none;border:1px solid #1e293b;border-radius:8px;padding:6px 12px;font-size:.75rem;color:#64748b;cursor:pointer;width:100%;text-align:left;text-decoration:none;box-sizing:border-box;display:block">&#128100; Mi perfil</a>
    <button class="theme-btn" id="theme-btn" onclick="toggleTheme()"><i data-lucide="sun" class="btn-icon" id="theme-icon"></i> <span id="theme-label">Modo claro</span></button>
    <button class="logout-btn" onclick="window.location.href='/logout'"><i data-lucide="log-out" class="btn-icon"></i> Cerrar sesión</button>
  </div>
</div>


<!-- Contenedor de avisos y cortina de sesion vencida. Antes, cuando la sesion
     caducaba, los fetch recibian un 401 que nadie miraba: la pantalla quedaba
     vacia o girando para siempre, sin decir que habia que volver a entrar. -->
<div id="avisos"></div>
<div id="sesion-vencida">
  <div class="caja">
    <h3>Tu sesión venció</h3>
    <p>Por seguridad cerramos la sesión después de un rato de inactividad. Volvé a entrar para seguir trabajando.</p>
    <a href="/login">Iniciar sesión</a>
  </div>
</div>

<div class="main">
  <!-- ======= COLA PANEL ======= -->
  <div id="cola-panel" class="panel active">
    <div class="page-header">
      <div>
        <h1>Cola de llamadas</h1>
        <div class="page-date" id="cola-date"></div>
      </div>
      <button class="export-btn" onclick="exportCSV()"><i data-lucide="download" class="btn-icon"></i> Exportar CSV</button>
    </div>
    <div class="stats">
      <div class="stat-card"><div class="stat-label">Sin contactar</div><div class="stat-val" id="stat-cola">—</div></div>
      <div class="stat-card"><div class="stat-label">Seguimientos</div><div class="stat-val blue" id="stat-seguimientos">—</div></div>
      <div class="stat-card"><div class="stat-label">No le interesa</div><div class="stat-val" id="stat-no-interesa">—</div></div>
    </div>
    <div class="filters">
      <select class="filter-select" id="cola-category-filter">
        <option value="">Todos los rubros</option>
      </select>
      <input class="search-box" id="cola-search-input" placeholder="🔍 Buscar negocio..." oninput="colaSearch(this.value)">
    </div>
    <div style="display:flex;gap:8px;margin-bottom:12px">
      <button id="cola-filter-sin" onclick="setColaFilter('sin_contactar')" style="padding:5px 14px;border-radius:8px;border:1px solid #0088cc;background:#0088cc;color:#fff;font-size:.78rem;font-weight:600;cursor:pointer;font-family:'Inter',sans-serif">Sin contactar</button>
      <button id="cola-filter-no" onclick="setColaFilter('no_interesa')" style="padding:5px 14px;border-radius:8px;border:1px solid #1e293b;background:transparent;color:#64748b;font-size:.78rem;font-weight:600;cursor:pointer;font-family:'Inter',sans-serif">No interesa</button>
    </div>
    <div class="table-wrap">
      <div class="table-header no-cb">
        <span>Negocio</span><span>Teléfono</span><span>Notas</span><span>Acciones</span>
      </div>
      <div id="cola-body"></div>
      <div id="cola-pagination" style="display:none;justify-content:center;align-items:center;gap:12px;padding:16px 0;font-size:.85rem;color:#94a3b8"></div>
    </div>
  </div>

  <!-- ======= SEGUIMIENTOS PANEL ======= -->
  <div id="seguimientos-panel" class="panel">
    <div class="page-header">
      <div>
        <h1>Seguimientos</h1>
        <div class="page-date">Leads que pidieron que los llamen después</div>
      </div>
    </div>
    <div class="table-wrap">
      <div class="table-header no-cb">
        <span>Negocio</span><span>Teléfono</span><span>Callback</span><span>Notas</span><span>Acciones</span>
      </div>
      <div id="seguimientos-body"></div>
    </div>
  </div>

  <!-- ======= META ADS PANEL ======= -->
  <div id="meta-panel" class="panel">
    <div class="page-header">
      <div>
        <h1>Meta Ads</h1>
        <div class="page-date">Leads de formularios de Facebook e Instagram</div>
      </div>
    </div>
    <div class="filters">
      <input class="search-box" id="meta-search-input" placeholder="🔍 Buscar..." oninput="metaSearch(this.value)">
    </div>
    <div class="table-wrap">
      <div class="table-header no-cb" style="grid-template-columns:1.8fr 1fr 1.2fr 1.2fr 0.9fr 0.8fr 1.1fr">
        <span>Nombre / Negocio</span><span>Teléfono</span><span>Qué busca</span><span>Presupuesto</span><span>Ciudad</span><span style="cursor:pointer" onclick="toggleMetaSort()">Fecha <span id="meta-sort-icon">↓</span></span><span>Acciones</span>
      </div>
      <div id="meta-body"></div>
    </div>
  </div>

  <!-- ======= PIPELINE PANEL ======= -->
  <div id="pipeline-panel" class="panel">
    <div class="page-header">
      <div>
        <h1>Proceso de venta</h1>
        <div class="page-date">Leads en proceso de venta activo</div>
      </div>
    </div>
    <div class="filters">
      <input class="search-box" id="pipeline-search-input" placeholder="🔍 Buscar..." oninput="pipelineSearch(this.value)">
    </div>
    <div class="table-wrap">
      <div class="table-header no-cb">
        <span>Negocio</span><span>Estado</span><span>Teléfono</span><span>Notas</span><span>Acciones</span>
      </div>
      <div id="pipeline-body"></div>
    </div>
  </div>

  <!-- ======= CLIENTES PANEL ======= -->
  <div id="clientes-panel" class="panel">
    <div class="page-header">
      <div>
        <h1>Clientes</h1>
        <div class="page-date">Deals cerrados y proyectos en curso</div>
      </div>
    </div>
    <div class="table-wrap">
      <div class="table-header no-cb">
        <span>Negocio</span><span>Estado</span><span>Teléfono</span><span>Notas</span><span>Acciones</span>
      </div>
      <div id="clientes-body"></div>
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
          <div id="wa-templates-panel" style="border-top:1px solid #1e293b;padding:10px;background:#0d1525">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
              <span style="font-size:.75rem;font-weight:600;color:#64748b;text-transform:uppercase;letter-spacing:.5px">Plantillas</span>
              <button onclick="toggleWaTemplateForm()" style="font-size:.72rem;background:#1e293b;border:none;color:#94a3b8;padding:3px 8px;border-radius:4px;cursor:pointer">+ Nueva</button>
            </div>
            <div id="wa-template-form" style="display:none;margin-bottom:8px">
              <input id="wa-tmpl-name" placeholder="Nombre de la plantilla" style="width:100%;background:#111827;border:1px solid #1e293b;color:#e2e8f0;padding:5px 8px;border-radius:4px;font-size:.78rem;margin-bottom:4px;box-sizing:border-box">
              <textarea id="wa-tmpl-body" rows="2" placeholder="Texto del mensaje..." style="width:100%;background:#111827;border:1px solid #1e293b;color:#e2e8f0;padding:5px 8px;border-radius:4px;font-size:.78rem;resize:none;margin-bottom:4px;box-sizing:border-box"></textarea>
              <button onclick="saveWaTemplate()" style="font-size:.75rem;background:#0088cc;border:none;color:#fff;padding:4px 12px;border-radius:4px;cursor:pointer">Guardar</button>
            </div>
            <div id="wa-template-list" style="max-height:120px;overflow-y:auto"></div>
          </div>
        </div>
      </div>
    </div>
  </div>


  <!-- ======= TASKS PANEL ======= -->
  <div id="tasks-panel" class="panel">
    <div class="page-header">
      <div><h1>Tareas</h1><div class="page-date">Tareas y seguimientos del equipo</div></div>
      <button class="run-btn" style="width:auto;padding:8px 16px" onclick="openAddTaskModal()">+ Nueva tarea</button>
    </div>
    <div class="filter-bar">
      <div class="filter-row-1">
        <input type="text" id="task-search" class="search-input" placeholder="🔍 Buscar tarea..." oninput="_onTaskSearch(this.value)">
        <div class="upick-wrap">
          <div class="upick-trigger" id="upick-filter-trigger" onclick="_upickToggle('filter')">
            <div class="upick-av" id="upick-filter-av" style="background:#1e293b;color:#475569;font-size:.8rem">👤</div>
            <span class="upick-label" id="upick-filter-label">Todos los usuarios</span>
            <span class="upick-chevron">▾</span>
          </div>
          <div class="upick-dropdown" id="upick-filter-dropdown" style="display:none"></div>
        </div>
      </div>
      <div class="filter-row-2">
        <button class="pill active" id="pill-all" onclick="filterTasks('all',this)">Todas <span class="pill-count" id="pill-count-all">0</span></button>
        <button class="pill" id="pill-todo" onclick="filterTasks('todo',this)">Pendientes <span class="pill-count" id="pill-count-todo">0</span></button>
        <button class="pill" id="pill-inprogress" onclick="filterTasks('in_progress',this)">En progreso <span class="pill-count" id="pill-count-inprogress">0</span></button>
        <button class="pill" id="pill-done" onclick="filterTasks('done',this)">Hechas <span class="pill-count" id="pill-count-done">0</span></button>
        <div style="width:1px;height:20px;background:#1e293b;margin:0 2px;flex-shrink:0"></div>
        <button class="pill warn" id="pill-high" onclick="filterTasksQuick('high',this)">⚠ Alta prioridad <span class="pill-count" id="pill-count-high">0</span></button>
        <button class="pill orange" id="pill-overdue" onclick="filterTasksQuick('overdue',this)">🕐 Vencidas <span class="pill-count" id="pill-count-overdue">0</span></button>
      </div>
    </div>
    <div id="tasks-summary" class="tasks-summary"></div>
    <button class="mobile-fab" id="mobile-fab-task" onclick="openAddTaskModal()" aria-label="Nueva tarea">+</button>
    <div id="tasks-list"></div>
  </div>

  <!-- ======= CALENDAR PANEL ======= -->
  <div id="cal-panel" class="panel">
    <div class="cal-header">
      <h1 id="cal-week-label">Calendario</h1>
      <div style="display:flex;gap:8px;align-items:center;flex-shrink:0">
        <button class="cal-nav-btn" onclick="calChangeMonth(-1)">←</button>
        <button class="cal-nav-btn" onclick="calChangeMonth(1)">→</button>
        <a href="https://calendly.com/scalerics/consultoriagratuita" target="_blank" class="cal-new-btn" style="background:#0f2a1a;border:1px solid #10b981;color:#10b981;text-decoration:none">+ Calendly</a>
      </div>
    </div>
    <div id="cal-error" class="cal-error" style="display:none"></div>
    <div id="cal-days" class="cal-days"><div class="cal-loading">Cargando calendario...</div></div>
    <div id="cal-day-events-mobile" style="display:none;margin-top:12px;padding:0 4px"></div>
  </div>

  <!-- ======= METRICS PANEL ======= -->
  <div id="metrics-panel" class="panel">
    <div class="page-header">
      <div>
        <h1>Métricas</h1>
        <div class="page-date" id="metrics-date"></div>
      </div>
      <button class="export-btn" onclick="loadMetrics()">↻ Actualizar</button>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:24px">
      <button id="tab-sdr-btn" onclick="switchMetricsTab('sdr')" style="padding:6px 18px;border-radius:8px;border:1px solid #1e293b;background:#0088cc;color:#fff;font-size:.82rem;font-weight:700;cursor:pointer;font-family:'Inter',sans-serif">SDR</button>
      <button id="tab-meta-btn" onclick="switchMetricsTab('meta')" style="display:none;padding:6px 18px;border-radius:8px;border:1px solid #1e293b;background:transparent;color:#64748b;font-size:.82rem;font-weight:700;cursor:pointer;font-family:'Inter',sans-serif">Meta Ads</button>
    </div>
    <!-- Tab SDR -->
    <div id="metrics-sdr">
      <div class="metrics-grid" style="grid-template-columns:repeat(4,1fr)">
        <div class="stat-card"><div class="stat-label">Total leads SDR</div><div class="stat-val" id="m-total">—</div></div>
        <div class="stat-card"><div class="stat-label">Contactados</div><div class="stat-val blue" id="m-contacted">—</div></div>
        <div class="stat-card"><div class="stat-label">Reuniones agendadas</div><div class="stat-val" style="color:#f59e0b" id="m-meetings">—</div></div>
        <div class="stat-card"><div class="stat-label">Clientes cerrados</div><div class="stat-val green" id="m-closed">—</div></div>
      </div>
      <div class="metrics-grid" style="grid-template-columns:repeat(3,1fr)">
        <div class="stat-card"><div class="stat-label">Tasa de contacto</div><div class="stat-val blue" id="m-contact-rate">—</div></div>
        <div class="stat-card"><div class="stat-label">Tasa de reunión</div><div class="stat-val" style="color:#f59e0b" id="m-meeting-rate">—</div></div>
        <div class="stat-card"><div class="stat-label">Tasa de conversión</div><div class="stat-val green" id="m-conv">—</div></div>
      </div>
      <div class="metrics-grid-2">
        <div class="m-card"><div class="m-card-title">Funnel CRM</div><div id="m-funnel"></div></div>
        <div class="m-card"><div class="m-card-title">Llamadas</div><div id="m-calls"></div></div>
      </div>
      <div class="metrics-grid-2">
        <div class="m-card"><div class="m-card-title">Leads por mes</div><div id="m-months"></div></div>
        <div class="m-card"><div class="m-card-title">Top rubros</div><div id="m-rubros"></div></div>
      </div>
      <div class="metrics-grid-2">
        <div class="m-card"><div class="m-card-title">Top ciudades</div><div id="m-cities"></div></div>
      </div>
    </div>
    <!-- Tab Meta Ads (solo admin) -->
    <div id="metrics-meta" style="display:none">
      <div class="metrics-grid" style="grid-template-columns:repeat(4,1fr)">
        <div class="stat-card"><div class="stat-label">Total leads Meta</div><div class="stat-val" id="mm-total">—</div></div>
        <div class="stat-card"><div class="stat-label">Este mes</div><div class="stat-val blue" id="mm-month">—</div></div>
        <div class="stat-card"><div class="stat-label">Esta semana</div><div class="stat-val" style="color:#f59e0b" id="mm-week">—</div></div>
        <div class="stat-card"><div class="stat-label">Conversión Meta</div><div class="stat-val green" id="mm-conv">—</div></div>
      </div>
      <div class="metrics-grid-2">
        <div class="m-card"><div class="m-card-title">Leads por campaña</div><div id="mm-campaigns"></div></div>
        <div class="m-card"><div class="m-card-title">Leads por mes</div><div id="mm-months"></div></div>
      </div>
      <div class="metrics-grid-2">
        <div class="m-card"><div class="m-card-title">Funnel CRM Meta</div><div id="mm-funnel"></div></div>
        <div class="m-card"><div class="m-card-title">Qué buscan</div><div id="mm-busca"></div></div>
      </div>
      <div class="metrics-grid-2">
        <div class="m-card"><div class="m-card-title">Presupuesto declarado</div><div id="mm-presupuesto"></div></div>
        <div class="m-card"><div class="m-card-title">Top ciudades Meta</div><div id="mm-cities"></div></div>
      </div>
    </div>
  </div>
  <div id="sdr-panel" class="panel">
    <div class="page-header">
      <div>
        <h1>SDR</h1>
        <div class="page-date">Rendimiento por vendedor</div>
      </div>
      <div style="display:flex;gap:6px;align-items:center">
        <button id="sdr-pill-week"  onclick="setSdrPeriod('week')"  style="padding:5px 14px;border-radius:99px;border:1px solid #1e293b;background:transparent;color:#64748b;font-size:.78rem;font-weight:600;cursor:pointer;font-family:'Inter',sans-serif">Semana</button>
        <button id="sdr-pill-month" onclick="setSdrPeriod('month')" style="padding:5px 14px;border-radius:99px;border:1px solid #0088cc;background:#0088cc;color:#fff;font-size:.78rem;font-weight:600;cursor:pointer;font-family:'Inter',sans-serif">Mes</button>
        <button id="sdr-pill-year"  onclick="setSdrPeriod('year')"  style="padding:5px 14px;border-radius:99px;border:1px solid #1e293b;background:transparent;color:#64748b;font-size:.78rem;font-weight:600;cursor:pointer;font-family:'Inter',sans-serif">Año</button>
        <button class="export-btn"  onclick="loadSdr()">↻ Actualizar</button>
      </div>
    </div>
    <div id="sdr-content"></div>
  </div>

  <div id="activity-panel" class="panel">
    <div class="page-header">
      <div>
        <h1>Actividad reciente</h1>
        <div class="page-date" id="activity-date"></div>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <select class="filter-select" id="activity-user-filter" onchange="loadActivity()" style="font-size:.8rem">
          <option value="">Todos los usuarios</option>
        </select>
        <button class="export-btn" onclick="loadActivity()">↻</button>
      </div>
    </div>
    <div id="activity-list" style="max-width:760px"></div>
  </div>
</div>

<!-- Modal: Contactar -->
<div class="modal-overlay" id="call-modal" onclick="if(event.target===this)closeCallModal()">
  <div class="modal" style="width:400px;max-width:95vw">
    <h3 id="call-modal-name" style="margin-bottom:4px;font-size:1rem"></h3>
    <p id="call-modal-phone" style="font-size:.8rem;color:#64748b;margin-bottom:20px"></p>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px">
      <button class="outcome-btn outcome-no-answer" onclick="logCallOutcome('no_contestó')"><i data-lucide="phone-missed" class="outcome-icon"></i><span style="font-size:.75rem">No contestó</span></button>
      <button class="outcome-btn outcome-not-interested" onclick="logCallOutcome('no_interesa')"><i data-lucide="x-circle" class="outcome-icon"></i><span style="font-size:.75rem">No le interesa</span></button>
      <button class="outcome-btn outcome-callback" onclick="setCallbackOutcome('llamar_despues')"><i data-lucide="clock" class="outcome-icon"></i><span style="font-size:.75rem">Llamar después</span></button>
      <button class="outcome-btn outcome-interested" onclick="setCallbackOutcome('interesado')"><i data-lucide="star" class="outcome-icon"></i><span style="font-size:.75rem">Interesado</span></button>
      <button class="outcome-btn outcome-meeting" onclick="logCallOutcome('reunion')" style="grid-column:span 2"><i data-lucide="calendar-check" class="outcome-icon" style="display:inline-block;vertical-align:middle;margin-right:6px"></i><span style="font-size:.75rem">Agendó reunión</span></button>
    </div>
    <div id="callback-row" style="display:none;background:#0d1525;border:1px solid #1e293b;border-radius:8px;padding:12px;margin-bottom:12px">
      <label style="font-size:.72rem;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.8px;display:block;margin-bottom:8px">Fecha para llamar</label>
      <input type="datetime-local" id="callback-date-input" style="width:100%;background:#0a0f1a;border:1px solid #1e293b;border-radius:6px;color:#e2e8f0;padding:8px 10px;font-size:.88rem;font-family:'Inter',sans-serif;margin-bottom:8px">
      <button onclick="confirmCallback()" style="width:100%;background:#0088cc;border:none;color:#fff;font-size:.82rem;font-weight:700;padding:8px;border-radius:6px;cursor:pointer;font-family:'Inter',sans-serif">Confirmar fecha</button>
    </div>
    <textarea id="call-notes-input" placeholder="Notas opcionales..." rows="2" style="width:100%;background:#0a0f1a;border:1px solid #1e293b;border-radius:8px;color:#e2e8f0;padding:8px 10px;font-size:.82rem;font-family:'Inter',sans-serif;resize:vertical;margin-bottom:12px;box-sizing:border-box"></textarea>
    <button onclick="closeCallModal()" style="width:100%;background:transparent;border:1px solid #1e293b;color:#64748b;font-size:.82rem;padding:8px;border-radius:6px;cursor:pointer;font-family:'Inter',sans-serif">Cancelar</button>
  </div>
</div>

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
  <div class="modal" style="width:480px">
    <h3>Nueva tarea</h3>
    <div style="margin-top:14px">
      <label class="modal-label">Título</label>
      <input type="text" id="task-title-input" class="modal-input" placeholder="Ej: Agendar 10 reuniones, Llamar el martes...">
    </div>
    <div style="margin-top:10px">
      <label class="modal-label">Descripción (opcional)</label>
      <input type="text" id="task-desc-input" class="modal-input" placeholder="Detalle de la tarea...">
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
        <label class="modal-label">Vencimiento y hora</label>
        <input type="datetime-local" id="task-deadline-input" class="modal-input">
      </div>
    </div>
    <div style="margin-top:10px">
      <label class="modal-label">Asignar a</label>
      <div class="upick-wrap">
        <div class="upick-trigger" id="upick-modal-trigger" onclick="_upickToggle('modal')">
          <div class="upick-av" id="upick-modal-av" style="background:#1e293b;color:#475569;font-size:.9rem">—</div>
          <span class="upick-label" id="upick-modal-label">— Sin asignar —</span>
          <span class="upick-chevron">▾</span>
        </div>
        <div class="upick-dropdown" id="upick-modal-dropdown" style="display:none"></div>
      </div>
      <input type="hidden" id="task-assignee-id">
      <input type="hidden" id="task-assignee-email">
    </div>
    <div style="margin-top:10px">
      <label class="modal-label">Meta (opcional)</label>
      <div style="display:flex;gap:8px;align-items:center">
        <select id="task-goal-type-input" class="modal-input" style="flex:2" onchange="_onTaskGoalTypeChange()">
          <option value="">Sin meta automática</option>
          <option value="leads_contactados">Leads contactados</option>
          <option value="llamadas_realizadas">Llamadas realizadas</option>
          <option value="llamadas_contestadas">Llamadas contestadas</option>
          <option value="reuniones_agendadas">Reuniones agendadas</option>
          <option value="reuniones_hechas">Reuniones hechas</option>
          <option value="presupuestos_enviados">Presupuestos enviados</option>
          <option value="clientes_cerrados">Clientes cerrados</option>
        </select>
        <input type="number" id="task-goal-input" class="modal-input" style="flex:1;display:none" placeholder="Cantidad" min="1">
      </div>
    </div>
    <div style="margin-top:10px">
      <label class="modal-label">Cliente (opcional)</label>
      <input type="text" id="task-client-search" class="modal-input" placeholder="Buscar negocio..." oninput="_taskClientSearch(this.value)">
      <div id="task-client-results" style="background:#0a0f1a;border:1px solid #1e293b;border-radius:6px;margin-top:4px;display:none;max-height:140px;overflow-y:auto"></div>
      <input type="hidden" id="task-client-id">
      <div id="task-client-chosen" style="font-size:.78rem;color:#0088cc;margin-top:4px"></div>
    </div>
    <div style="margin-top:10px">
      <label class="modal-label">Estado</label>
      <select id="task-status-input" class="modal-input">
        <option value="todo">● Pendiente</option>
        <option value="in_progress">⚡ En progreso</option>
        <option value="done">✓ Hecha</option>
      </select>
    </div>
    <input type="hidden" id="task-edit-id">
    <div class="modal-btns" style="margin-top:16px">
      <button class="btn-cancel" onclick="document.getElementById('add-task-modal').classList.remove('open')">Cancelar</button>
      <button class="btn-confirm" id="task-submit-btn" onclick="submitAddTask()">+ Crear tarea</button>
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
    <label class="modal-label">Link de reunión</label>
    <input type="url" id="ev-email" placeholder="(opcional) https://meet.google.com/..." style="margin-bottom:12px">
    <label class="modal-label">Descripción</label>
    <textarea id="ev-desc" placeholder="(opcional)" style="min-height:60px"></textarea>
    <input type="hidden" id="ev-client-id" value="">
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
window._isAdmin = false; // default until /api/me resolves

// ========== Manejo central de errores de la API ==========
// De los 101 fetch() del panel, solo 4 miraban el 401. Cuando la sesion vencia,
// el resto recibia un JSON de error donde esperaba datos: la vista quedaba vacia
// o con un spinner eterno, y un SDR podia perder media jornada de trabajo sin
// enterarse de que ya no estaba logueado.
//
// En vez de tocar los 101 call sites (y arriesgar romper alguno), se envuelve
// window.fetch: todas las llamadas existentes y futuras quedan cubiertas, y la
// Response se devuelve intacta para no cambiar el comportamiento de nadie.

function mostrarAviso(mensaje, titulo, tipo) {
  let cont = document.getElementById('avisos');
  if (!cont) {
    cont = document.createElement('div');
    cont.id = 'avisos';
    document.body.appendChild(cont);
  }
  const el = document.createElement('div');
  el.className = 'aviso aviso-' + (tipo || 'error');
  const btn = document.createElement('button');
  btn.className = 'aviso-cerrar';
  btn.textContent = '×';
  btn.onclick = () => el.remove();
  const cuerpo = document.createElement('div');
  if (titulo) {
    const h = document.createElement('div');
    h.className = 'aviso-titulo';
    h.textContent = titulo;      // textContent, no innerHTML: el detalle viene del server
    cuerpo.appendChild(h);
  }
  cuerpo.appendChild(document.createTextNode(mensaje));
  el.appendChild(btn);
  el.appendChild(cuerpo);
  cont.appendChild(el);
  setTimeout(() => el.remove(), 7000);
}

let _sesionYaVencida = false;
function _avisarSesionVencida() {
  if (_sesionYaVencida) return;   // no apilar una cortina por cada fetch en vuelo
  _sesionYaVencida = true;
  const cortina = document.getElementById('sesion-vencida');
  if (cortina) cortina.style.display = 'flex';
  else window.location.href = '/login';
}

(function envolverFetch() {
  const original = window.fetch.bind(window);
  const esApi = (u) => {
    try {
      return new URL(u, window.location.origin).pathname.startsWith('/api/');
    } catch (e) { return false; }
  };

  window.fetch = async function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    let resp;
    try {
      resp = await original(input, init);
    } catch (e) {
      if (esApi(url)) {
        mostrarAviso('No se pudo contactar al servidor. Revisá tu conexión.',
                     'Sin conexión', 'error');
      }
      throw e;   // se relanza: el codigo que ya tenia catch sigue funcionando igual
    }

    if (!resp.ok && esApi(url)) {
      if (resp.status === 401) {
        _avisarSesionVencida();
      } else {
        // clone() para no consumir el body: quien llamo sigue pudiendo leerlo.
        resp.clone().json().then(d => {
          const detalle = (d && (d.detail || d.error)) || '';
          if (resp.status === 403) {
            mostrarAviso(detalle || 'No tenés permiso para esta acción.',
                         'Acceso denegado', 'warn');
          } else if (resp.status === 429) {
            mostrarAviso(detalle || 'Demasiados intentos, esperá un momento.',
                         'Frenando un poco', 'warn');
          } else if (resp.status >= 500) {
            mostrarAviso(detalle || 'Error interno del servidor.',
                         'Algo falló', 'error');
          }
        }).catch(() => {
          if (resp.status >= 500) {
            mostrarAviso('El servidor respondió con un error inesperado.',
                         'Algo falló', 'error');
          }
        });
      }
    }
    return resp;
  };
})();

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
let activePanel = 'cola';
function showPanel(name) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById(name + '-panel').classList.add('active');
  const sideNav = document.getElementById('nav-' + name);
  if (sideNav) sideNav.classList.add('active');
  activePanel = name;
  _syncMobileNav(name);
  closeSidebar();
  if (name === 'cola') loadCola();
  if (name === 'seguimientos') loadSeguimientos();
  if (name === 'pipeline') loadPipelinePanel();
  if (name === 'clientes') loadClientesPanel();
  if (name === 'meta') loadMetaPanel();
  if (name === 'wa' && !waLoaded) loadWaLeads();
  if (name === 'wa') loadWaTemplates();
  if (name === 'cal' && !calLoaded) { calLoaded = true; renderCalendar(); }
  if (name === 'tasks') loadTasks();
  if (name === 'metrics') loadMetrics();
  if (name === 'activity') loadActivity();
  if (name === 'sdr') loadSdr();
}

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
        <button class="delete-btn" onclick="deleteLead(${b.id},${escJs(b.name||'')},loadMetaPanel)" title="Borrar"><i data-lucide=\"trash-2\" class=\"btn-icon\"></i></button>
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
        <button class="pitch-btn" onclick="openCallModal(${b.id},${escJs(b.name||'')},${escJs(b.phone||'')},'cola')" style="background:#1e293b"><i data-lucide=\"clipboard-list\" class=\"btn-icon\"></i> Resultado</button>
        <button class="delete-btn" onclick="deleteLead(${b.id},${escJs(b.name||'')})" title="Borrar"><i data-lucide=\"trash-2\" class=\"btn-icon\"></i></button>
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
          <button class="pitch-btn" onclick="openCallModal(${b.id},${escJs(b.name||'')},${escJs(b.phone||'')},'seguimientos')" style="background:#1e293b"><i data-lucide=\"clipboard-list\" class=\"btn-icon\"></i> Resultado</button>
          <button class="delete-btn" onclick="deleteLead(${b.id},${escJs(b.name||'')})" title="Borrar"><i data-lucide=\"trash-2\" class=\"btn-icon\"></i></button>
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
        <button class="delete-btn" onclick="deleteLead(${b.id},${escJs(b.name||'')})" title="Borrar lead">🗑</button>
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
  // Antes se armaba el CSV en el browser desde _allLeads, que segun el panel que
  // hubiera cargado ultimo tenia la pagina actual (50 filas) o la lista entera: el
  // archivo salia incompleto sin ningun aviso. Ahora lo genera el servidor con los
  // MISMOS filtros que se ven en pantalla, y exporta todo lo que matchea.
  // Mismas variables que usa loadLeads(), para que el archivo contenga exactamente
  // lo que el usuario esta viendo filtrado.
  const params = new URLSearchParams();
  if (currentCrm) params.set('crm_status', currentCrm);
  if (currentCategory) params.set('category', currentCategory);
  if (currentSearch) params.set('search', currentSearch);
  window.location.href = '/api/leads/export.csv?' + params;
}

function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
// Literal de JS seguro para meter dentro de un onclick="..." inline.
// esc() sola no alcanza ahi: el navegador decodifica las entidades HTML
// (&#39; -> ') ANTES de parsear el atributo como JS, asi que una comilla
// escapada igual rompe un literal '...' armado a mano, para cualquier nombre
// con apostrofe. JSON.stringify produce un string de JS bien citado y
// escapado; despues se escapa el HTML para que sobreviva dentro del
// atributo onclick="" con comillas dobles.
function escJs(s) { return JSON.stringify(String(s == null ? '' : s)).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

// Para href/src: bloquea javascript:, data:, vbscript: y demas esquemas ejecutables.
// Devuelve '#' si la URL no es http(s), y escapa el resto para el atributo.
function safeUrl(u) {
  const raw = String(u == null ? '' : u).trim();
  return /^https?:\/\//i.test(raw) ? esc(raw) : '#';
}
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
    <div class="wa-lead-item" id="wa-lead-${esc(lead.phone)}" onclick="selectWaLead(${escJs(lead.phone)},${escJs(lead.name||lead.phone)})">
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
              ${ev.meeting_url?`<a class="cal-join-btn" href="${safeUrl(ev.meeting_url)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">▶ Unirse</a>`:''}
              <button class="cal-demo-btn" onclick="event.stopPropagation();openDemoModal(${escJs(ph||'')},${escJs(ev.title||'')},${escJs(nm||'')})">📊 Generar Demo</button>
              <button class="cal-del-btn" onclick="event.stopPropagation();deleteCalEvent(${escJs(ev.id)},${escJs(ev.title||'')})">🗑 Borrar</button>
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
  // canceled_locally: no se pudo confirmar el borrado en Google (o es una reunión
  // de Calendly, cuyo id no es un eventId de Google), asi que quedo CANCELADA en
  // el CRM. Hay que refrescar igual — si no, sigue visible como si no hubiera
  // pasado nada — y explicar por que no desaparecio de la agenda de Google.
  if (d.ok || d.canceled_locally) {
    renderCalendar();
    if (!d.ok) mostrarAviso(d.error || 'Quedó cancelada en el CRM.', 'Ojo', 'warn');
  } else {
    mostrarAviso(d.error || 'No se pudo borrar la reunión.', 'Error al borrar', 'error');
  }
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
  if (!d.ok) { mostrarAviso(d.error || 'No se pudo crear la reunión.', 'Error', 'error'); return; }
  closeNewEventModal();
  renderCalendar();
  // La reunion se crea en el CRM y en Google Calendar. Si Google fallo, o el
  // cliente no tiene email y no se le pudo mandar invitacion, hay que decirlo: si
  // no, el usuario se queda pensando que ya esta invitado y nadie aparece.
  if (d.google_ok === false) {
    mostrarAviso(d.aviso || 'Quedó en el CRM pero no se creó en Google Calendar.',
                 'Revisá Google Calendar', 'warn');
  } else if (d.aviso) {
    mostrarAviso(d.aviso, 'Ojo', 'warn');
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
  res.innerHTML = matches.map(l => `<div style="padding:8px 12px;cursor:pointer;font-size:.82rem;color:#e2e8f0;border-bottom:1px solid #1e293b" onmousedown="_pickTaskClient(${l.id},${escJs(l.name||'')})">${esc(l.name||'')}</div>`).join('');
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
      <button class="cp-btn cp-btn-ghost" onclick="openAddTaskModal(${_cpClientId},${escJs((_cpData.lead||{}).name||'')})">+ Nueva</button>
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
  const meta = [l.interest, l.category, l.city].filter(Boolean).join(' · ');
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
// Fallback nada mas: la lista real la manda /api/me (services/auth.ALL_PANELS).
// Estaba hardcodeada aca y en la pagina de admin, y a las dos les faltaba 'sdr',
// asi que guardar cualquier rol borraba ese permiso.
let ALL_PANELS = ['cola','seguimientos','meta','pipeline','clientes','tasks','wa','cal','metrics','sdr','activity'];
(async () => {
  try {
    const r = await fetch('/api/me');
    if (!r.ok) return;
    const m = await r.json();
    if (Array.isArray(m.all_panels) && m.all_panels.length) ALL_PANELS = m.all_panels;
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
    ${l.phone ? `<div class="cp-field"><span class="cp-field-label">Teléfono</span><span class="cp-field-val">${esc(l.phone)}</span></div>` : ''}
    ${l.city ? `<div class="cp-field"><span class="cp-field-label">Ciudad</span><span class="cp-field-val">${esc(l.city)}</span></div>` : ''}
    ${l.category ? `<div class="cp-field"><span class="cp-field-label">Rubro</span><span class="cp-field-val">${esc(l.category)}</span></div>` : ''}
    ${l.address ? `<div class="cp-field"><span class="cp-field-label">Dirección</span><span class="cp-field-val">${esc(l.address)}</span></div>` : ''}
    ${l.maps_url ? `<div class="cp-field"><span class="cp-field-label">Google Maps</span><span class="cp-field-val"><a href="${safeUrl(l.maps_url)}" target="_blank" rel="noopener noreferrer" style="color:#3b82f6">Ver en Maps →</a></span></div>` : ''}
    ${stars ? `<div class="cp-field"><span class="cp-field-label">Rating</span><span class="cp-field-val">${stars}</span></div>` : ''}
    ${l.instagram_url ? `<div class="cp-field"><span class="cp-field-label">Instagram</span><span class="cp-field-val"><a href="${safeUrl(l.instagram_url)}" rel="noopener noreferrer" target="_blank" style="display:inline-flex;align-items:center;gap:7px;color:#e2e8f0;text-decoration:none"><span style="display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:6px;background:linear-gradient(135deg,#f09433,#dc2743,#bc1888);color:#fff;font-size:.6rem;font-weight:800;flex-shrink:0">IG</span>Ver perfil →</a></span></div>` : ''}
    ${l.facebook_url ? `<div class="cp-field"><span class="cp-field-label">Facebook</span><span class="cp-field-val"><a href="${safeUrl(l.facebook_url)}" rel="noopener noreferrer" target="_blank" style="display:inline-flex;align-items:center;gap:7px;color:#e2e8f0;text-decoration:none"><span style="display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:6px;background:#1877f2;color:#fff;font-size:.6rem;font-weight:800;flex-shrink:0">FB</span>Ver perfil →</a></span></div>` : ''}
  </div>
  ${hasBotData ? `<div class="cp-section">
    <div class="cp-section-title">Datos del bot <span style="font-size:.7rem;color:#475569;font-weight:400">(calificación WA)</span></div>
    ${ci.lead_name ? `<div class="cp-field"><span class="cp-field-label">Contacto</span><span class="cp-field-val">${esc(ci.lead_name)}</span></div>` : ''}
    ${ci.budget_range ? `<div class="cp-field"><span class="cp-field-label">Presupuesto</span><span class="cp-field-val">${esc(ci.budget_range)}</span></div>` : ''}
    ${ci.colors ? `<div class="cp-field"><span class="cp-field-label">Colores de marca</span><span class="cp-field-val">${esc(ci.colors)}</span></div>` : ''}
    ${ci.instagram ? `<div class="cp-field"><span class="cp-field-label">Instagram / web</span><span class="cp-field-val">${esc(ci.instagram)}</span></div>` : ''}
    ${ci.needs ? `<div class="cp-field"><span class="cp-field-label">Necesidades</span><span class="cp-field-val">${esc(ci.needs)}</span></div>` : ''}
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
    // El bot guarda 'in'/'out' (bot/src/db/schema.sql:49, CHECK direction IN ('in','out')).
    // Antes se comparaba contra 'outbound', que nunca existe: todos los mensajes
    // salientes se pintaban como entrantes.
    const dir = (m.direction === 'out' || m.direction === 'outbound') ? 'out' : 'in';
    // esc() obligatorio: el contenido lo escribe cualquier numero que le mande un
    // mensaje al bot de WhatsApp. Sin escapar era un XSS almacenado sin autenticar.
    return `<div class="cp-wa-msg ${dir}">${esc(m.content || m.body || '')}</div>`;
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
        <div class="cp-meeting-title">${esc(m.title || 'Reunión')}</div>
        <button class="cp-btn cp-btn-ghost" style="color:#ef4444;font-size:.8rem;padding:2px 8px" onclick="_cpDeleteMeeting(${m.id})">Borrar</button>
      </div>
      <div class="cp-meeting-meta">${m.start_at ? new Date(m.start_at).toLocaleString('es-UY',{timeZone:'America/Montevideo',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'}) : ''} · <span style="color:${(_MEET_ESTADOS[m.status]||{}).color||'#94a3b8'};font-weight:600">${_cpMeetStatus(m.status)}</span>${m.owner_name ? ' · ' + esc(m.owner_name) : ''}</div>
      ${m.meet_link ? `<div style="margin-bottom:8px"><a class="cp-meeting-link" href="${safeUrl(m.meet_link)}" target="_blank" rel="noopener noreferrer" style="margin:0">▶ Unirse a la reunión</a></div>` : ''}
      ${m.calendar_event_id ? `<div style="margin-bottom:8px">
        <button class="cp-btn cp-btn-ghost" style="font-size:.75rem;padding:2px 8px" onclick="_cpToggleAddEmail(${m.id})">+ Agregar email</button>
        <div id="add-email-form-${m.id}" style="display:none;margin-top:6px;gap:6px;align-items:center;flex-wrap:wrap">
          <input type="email" id="add-email-input-${m.id}" placeholder="email@ejemplo.com" style="font-size:.8rem;padding:4px 8px;background:#0f172a;border:1px solid #1e293b;border-radius:6px;color:#f1f5f9;width:220px">
          <button class="cp-btn cp-btn-primary" style="font-size:.75rem;padding:4px 10px;margin-top:4px" onclick="_cpAddEmailToMeeting(${m.id},${escJs(m.calendar_event_id)})">Agregar</button>
        </div>
      </div>` : ''}
      ${_cpRenderCierre(m)}
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

// El mapa anterior usaba 'completed' y 'cancelled' (doble L), valores que la base
// nunca guardo: el estado real se mostraba crudo o vacio.
const _MEET_ESTADOS = {
  scheduled:  {texto:'agendada',   color:'#94a3b8'},
  realizada:  {texto:'realizada',  color:'#34d399'},
  no_asistio: {texto:'no asistió', color:'#f87171'},
  reagendada: {texto:'reagendada', color:'#fbbf24'},
  canceled:   {texto:'cancelada',  color:'#64748b'},
};

function _cpMeetStatus(s) {
  return (_MEET_ESTADOS[s] || {}).texto || s || '';
}

// Botonera de cierre. Solo aparece cuando la reunion ya paso y sigue en
// 'agendada': antes una reunion terminaba y el CRM no pedia nada, asi que un
// planton quedaba registrado igual que una reunion exitosa.
function _cpRenderCierre(m) {
  const paso = m.start_at && new Date(m.start_at) < new Date();
  if (!paso || (m.status && m.status !== 'scheduled')) return '';
  return `<div style="margin-top:10px;padding-top:10px;border-top:1px solid #1e293b">
    <div style="font-size:.72rem;color:#64748b;margin-bottom:6px">¿Qué pasó con esta reunión?</div>
    <div style="display:flex;gap:6px;flex-wrap:wrap">
      <button class="cp-btn cp-btn-ghost" style="font-size:.75rem;padding:3px 10px;color:#34d399;border-color:#34d399" onclick="_cpCerrarReunion(${m.id},'realizada')">✓ Se hizo</button>
      <button class="cp-btn cp-btn-ghost" style="font-size:.75rem;padding:3px 10px;color:#f87171;border-color:#f87171" onclick="_cpCerrarReunion(${m.id},'no_asistio')">✕ No vino</button>
      <button class="cp-btn cp-btn-ghost" style="font-size:.75rem;padding:3px 10px;color:#fbbf24;border-color:#fbbf24" onclick="_cpCerrarReunion(${m.id},'reagendada')">↻ Se reagendó</button>
    </div>
  </div>`;
}

async function _cpCerrarReunion(meetingId, resultado) {
  const r = await fetch('/api/calendar/meetings/' + meetingId + '/outcome', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({outcome: resultado})
  });
  const d = await r.json().catch(() => ({}));
  if (!d.ok) { mostrarAviso(d.error || 'No se pudo registrar el resultado.', 'Error', 'error'); return; }
  const m = (_cpData.meetings || []).find(x => x.id === meetingId);
  if (m) m.status = d.status;
  // El backend tambien mueve el lead: se refleja para que la ficha no quede
  // mostrando un estado viejo.
  if (d.crm_status && _cpData.lead) _cpData.lead = {..._cpData.lead, crm_status: d.crm_status};
  document.getElementById('cp-tab-meetings').innerHTML = _cpRenderMeetings();
  mostrarAviso(
    resultado === 'realizada'  ? 'Reunión marcada como realizada. El lead avanzó.' :
    resultado === 'no_asistio' ? 'Registrado: el cliente no asistió.' :
                                 'Registrado: la reunión se reagendó.',
    'Listo', 'warn');
}

function _cpBindMeetings() {}

async function _cpDeleteMeeting(meetingId) {
  if (!confirm('¿Borrar esta reunión? También se cancela el evento en Google Calendar.')) return;
  const r = await fetch('/api/calendar/meetings/' + meetingId, { method: 'DELETE' });
  const data = await r.json();
  // Igual que en el calendario: canceled_locally significa que la reunion quedo
  // cancelada en el CRM aunque Google no lo haya confirmado. Se saca de la lista
  // y se explica el motivo, en vez de dejarla ahi como si el borrado no hubiera
  // ocurrido.
  if (data.ok || data.canceled_locally) {
    _cpData.meetings = (_cpData.meetings || []).filter(m => m.id !== meetingId);
    document.getElementById('cp-tab-meetings').innerHTML = _cpRenderMeetings();
    if (!data.ok) mostrarAviso(data.error || 'Quedó cancelada en el CRM.', 'Ojo', 'warn');
  } else {
    mostrarAviso(data.error || 'No se pudo borrar la reunión.', 'Error al borrar', 'error');
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

// Cabecera con el dato ESTRUCTURADO del presupuesto: monto, estado y el boton de
// marcar enviado. La pestaña solo listaba los adjuntos HTML, asi que el monto y el
// estado —lo unico que permite medir cuanto se presupuesto y cuanto se cerro— no
// se veian en ningun lado, y _cpMarkBudgetSent() estaba definida pero sin ningun
// boton que la llamara.
function _cpRenderBudgetResumen() {
  const b = _cpData.budget;
  if (!b || !b.id) return '';
  const enviado = b.status === 'sent';
  const monto = Number(b.total_amount || 0).toLocaleString('es-UY');
  const chip = enviado
    ? '<span style="background:rgba(16,185,129,.15);color:#34d399;border:1px solid rgba(16,185,129,.3);border-radius:999px;padding:2px 10px;font-size:.72rem;font-weight:700">Enviado</span>'
    : '<span style="background:rgba(148,163,184,.12);color:#94a3b8;border:1px solid #334155;border-radius:999px;padding:2px 10px;font-size:.72rem;font-weight:700">Borrador</span>';
  return `<div class="cp-section" style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
    <div>
      <div style="font-size:.72rem;color:#64748b;text-transform:uppercase;letter-spacing:.04em">Monto presupuestado</div>
      <div style="font-size:1.45rem;font-weight:800;color:#e2e8f0;line-height:1.2">USD ${esc(monto)}</div>
    </div>
    <div style="display:flex;align-items:center;gap:10px">
      ${chip}
      <a class="cp-btn cp-btn-ghost" style="font-size:.75rem;padding:4px 12px;text-decoration:none" href="/api/leads/${_cpClientId}/budget/preview" target="_blank">👁️ Vista previa</a>
      ${enviado ? '' : `<button class="cp-btn cp-btn-primary" style="font-size:.75rem;padding:4px 12px" onclick="_cpMarkBudgetSent()">✓ Marcar enviado</button>`}
    </div>
  </div>`;
}

function _cpRenderBudget() {
  const items = (_cpData.attBudget || []).filter(a => a.mime_type === 'text/html');
  const hasBudget = items.length > 0;

  if (!hasBudget) {
    return `${_cpRenderBudgetResumen()}
    <div class="cp-section">
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
        <a class="cp-btn cp-btn-ghost" style="font-size:.75rem;padding:3px 10px;text-decoration:none" href="/api/attachments/${a.id}/print" target="_blank">🖨️ PDF</a>
        <button class="cp-btn cp-btn-ghost" style="font-size:.75rem;padding:3px 10px" onclick="_cpOpenAiEditModal(${a.id},${escJs(a.name)})">✏️ Editar con IA</button>
        <button class="attach-del" title="Eliminar" onclick="_cpDeleteAttach(${a.id},'budget')">✕</button>
      </div>
    </div>`).join('');

  return `${_cpRenderBudgetResumen()}
  <div class="cp-section">
    <div class="cp-section-title">Presupuesto</div>
    ${listHtml}
  </div>
  ${_cpRenderAttachBox('budget')}`;
}

async function _cpSaveBudget() {
  // No-op: budget editing is done via the full preview/PDF page
}

async function _cpMarkBudgetSent() {
  if (!_cpData.budget || !_cpData.budget.id) return;
  const r = await fetch('/api/budgets/' + _cpData.budget.id + '/mark-sent', {method:'POST'});
  const d = await r.json().catch(() => ({}));
  if (!d.ok) { mostrarAviso(d.error || 'No se pudo marcar como enviado.', 'Error', 'error'); return; }
  _cpData.budget.status = 'sent';
  // El backend tambien mueve el lead en el pipeline: se refleja aca para que la
  // ficha no quede mostrando un estado viejo hasta que se recargue.
  if (d.crm_status && _cpData.lead) _cpData.lead = {..._cpData.lead, crm_status: d.crm_status};
  _cpSwitchTab('budget');
  mostrarAviso(d.ya_estaba ? 'Ya estaba marcado como enviado.'
                           : 'Presupuesto marcado como enviado. El lead pasó a "presupuesto enviado".',
               'Listo', 'warn');
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
</script>

<div id="sdr-bd" onclick="closeSdrDetail()" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:1100"></div>
<div id="sdr-modal" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);background:#0f172a;border:1px solid #1e293b;border-radius:16px;z-index:1101;width:420px;max-width:95vw;max-height:75vh;overflow:hidden;box-shadow:0 24px 48px rgba(0,0,0,.5)">
  <div style="display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid #1e293b">
    <div id="sdr-modal-title" style="font-size:.88rem;font-weight:700;color:#f1f5f9"></div>
    <button onclick="closeSdrDetail()" style="background:none;border:none;color:#475569;font-size:1.3rem;cursor:pointer;line-height:1;padding:2px 8px">×</button>
  </div>
  <div id="sdr-modal-body" style="overflow-y:auto;max-height:calc(75vh - 52px);padding:6px 10px"></div>
</div>

<div class="cp-backdrop" id="cp-backdrop" onclick="closeClientPanel()"></div>
<div class="client-panel" id="client-panel">
  <button class="cp-close" onclick="closeClientPanel()">×</button>
  <div class="cp-header">
    <div class="cp-title" id="cp-title">Cliente</div>
    <div class="cp-sub">
      <span id="cp-phone"></span>
      <select class="cp-status-sel" id="cp-status-sel" onchange="_cpChangeStatus(this.value)">
        <option value="sin_contactar">Sin contactar</option>
        <option value="interesado">Interesado</option>
        <option value="llamar_despues">Llamar después</option>
        <option value="no_interesa">No le interesa</option>
        <option value="reunion_agendada">Reunión agendada</option>
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
      <div class="cp-tab" data-tab="calls" onclick="_cpSwitchTab('calls')">📞 Llamadas</div>
    </div>
  </div>
  <div class="cp-body" id="cp-body">
    <div style="color:#475569">Cargando...</div>
  </div>
</div>

<div class="batch-bar" id="batch-bar">
  <span class="batch-count" id="batch-count">0 seleccionados</span>
  <select class="batch-sel" id="batch-status">
    <option value="">— Cambiar estado —</option>
    <option value="sin_contactar">Sin contactar</option>
    <option value="interesado">Interesado</option>
    <option value="llamar_despues">Llamar después</option>
    <option value="no_interesa">No le interesa</option>
    <option value="reunion_agendada">Reunión agendada</option>
    <option value="reunion_hecha">Reunión hecha</option>
    <option value="presupuesto_enviado">Presupuesto enviado</option>
    <option value="negociacion">Negociación</option>
    <option value="cliente_cerrado">Cliente cerrado</option>
    <option value="en_desarrollo">En desarrollo</option>
    <option value="finalizado">Finalizado</option>
  </select>
  <button class="batch-apply" onclick="applyBatch()">Aplicar</button>
  <button class="batch-cancel" onclick="clearSelection()">Cancelar</button>
</div>
</body>
</html>"""


_calendly_sync_state = {"at": 0.0}
_calendly_sync_lock = threading.Lock()
CALENDLY_SYNC_EVERY = int(os.environ.get("CALENDLY_SYNC_EVERY", "600"))


def _maybe_sync_calendly(db_path: str) -> None:
    """Trae los leads de Calendly cuando alguien abre el CRM.

    La máquina de Fly se duerme sin tráfico, así que un cron interno no
    correría. Va en un hilo aparte para no demorar la carga de la página, y
    con throttle para no pegarle a Google en cada request.
    """
    if not os.environ.get("GMAIL_REFRESH_TOKEN"):
        return
    now = time.time()
    with _calendly_sync_lock:
        if now - _calendly_sync_state["at"] < CALENDLY_SYNC_EVERY:
            return
        _calendly_sync_state["at"] = now

    def _run():
        try:
            # Gmail primero: aporta el mail del invitado y el calendario, que
            # es el único que ve las cancelaciones, pasa después.
            from services.calendly_gcal import fetch_and_sync
            from services.calendly_gmail import fetch_and_sync_gmail
            g = fetch_and_sync_gmail(db_path)
            c = fetch_and_sync(db_path)
            if g["created"] or c["created"] or c["canceled"]:
                logging.getLogger(__name__).info(
                    "calendly sync: %s nuevas por mail, %s por calendario, %s canceladas",
                    g["created"], c["created"], c["canceled"])
        except Exception:
            logging.getLogger(__name__).warning("calendly sync falló", exc_info=True)

    threading.Thread(target=_run, daemon=True).start()


def create_app(db_path: str) -> Flask:
    app = Flask(__name__)
    # SECRET_KEY firma la cookie de sesion. El fallback hardcodeado que habia aca
    # estaba en el repo: cualquiera que lo leyera podia forjarse una sesion de
    # admin. Falla ruidoso en vez de degradar en silencio. Para correr local sin
    # configurar nada, exportar ALLOW_INSECURE_DEV_KEY=1.
    _secret = os.environ.get("SECRET_KEY", "").strip()
    if not _secret:
        if os.environ.get("ALLOW_INSECURE_DEV_KEY") == "1":
            _secret = secrets.token_urlsafe(48)
            logging.getLogger(__name__).warning(
                "SECRET_KEY sin configurar: se genera una efimera. Las sesiones no "
                "sobreviven al reinicio. NO usar asi en produccion."
            )
        else:
            raise RuntimeError(
                "SECRET_KEY no configurada. El CRM no arranca sin firma de sesion. "
                "Genera una con: python -c \"import secrets;print(secrets.token_urlsafe(48))\" "
                "(o expor ALLOW_INSECURE_DEV_KEY=1 para desarrollo local)."
            )
    app.secret_key = _secret
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # force_https ya esta activo en fly.toml; sin esto la cookie viaja en claro
        # si alguna vez se sirve por HTTP.
        SESSION_COOKIE_SECURE=os.environ.get("ALLOW_INSECURE_DEV_KEY") != "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    )
    app.config["DB_PATH"] = db_path
    app.config["PIPELINE_STATUS"] = _pipeline_status
    app.config["PIPELINE_LOCK"] = _pipeline_lock

    for bp in (leads_bp, demos_bp, calendar_bp, wa_bp, pipeline_bp, tasks_bp, budgets_bp, tokens_bp, meta_bp, calendly_bp):
        app.register_blueprint(bp)

    # Rate limiting. No habia ninguno: /login aceptaba intentos ilimitados (fuerza
    # bruta contra 6 cuentas), /forgot-password permitia enumerar usuarios y gastar
    # cuota de Resend, y los dos webhooks publicos podian inundar la base.
    # Storage en memoria: alcanza porque se corre con --workers 1 (ver Procfile y
    # railway.toml). Si algun dia se escala a mas de un worker hay que mover esto
    # a Redis o el limite pasa a ser por-worker.
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=[],
        storage_uri="memory://",
        strategy="fixed-window",
    )
    app.config["LIMITER"] = limiter

    # Los dos webhooks estan exentos de login: son la unica superficie de escritura
    # sin autenticar. Aunque ahora validan firma, un atacante sin el secreto igual
    # puede forzar el calculo del HMAC en loop. El limite acota ese costo y evita
    # que un Meta/Calendly con hipo inunde la base.
    limiter.limit("60/minute")(meta_bp)
    limiter.limit("60/minute")(calendly_bp)

    @app.errorhandler(429)
    def _rate_limit_excedido(e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "rate_limited",
                            "detail": "Demasiados intentos, probá de nuevo en un rato"}), 429
        return render_template_string(
            LOGIN_HTML, error="Demasiados intentos. Esperá unos minutos."), 429

    @app.errorhandler(Exception)
    def _error_no_manejado(e):
        """Sin esto, cualquier excepcion en /api/ devolvia una pagina HTML de error
        a un fetch() que esperaba JSON: el frontend explotaba con 'Unexpected token
        <' y el usuario se quedaba con un spinner colgado y cero informacion."""
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            if request.path.startswith("/api/"):
                return jsonify({"error": e.name, "status": e.code}), e.code
            return e
        app.logger.exception("Error no manejado en %s %s", request.method, request.path)
        if request.path.startswith("/api/"):
            return jsonify({"error": "internal_error",
                            "detail": "Error interno. Quedo registrado en el log."}), 500
        return "Error interno del servidor", 500

    @app.after_request
    def _security_headers(resp):
        """Contencion global. La app no emitia ningun header de seguridad, asi que
        cualquier XSS almacenado corria sin limites y el CRM era embebible en un
        iframe ajeno. 'unsafe-inline' en script-src sigue siendo necesario mientras
        el JS viva dentro de DASHBOARD_HTML; se saca al extraerlo a static/."""
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            # unpkg sirve la libreria de iconos (lucide). Sin esto el navegador la
            # bloquea y TODOS los iconos del panel desaparecen con
            # "lucide is not defined". Conviene auto-hospedarla al extraer el JS a
            # static/, y ahi sacar este origen.
            "script-src 'self' 'unsafe-inline' https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "img-src 'self' data: https:; "
            "font-src 'self' https://fonts.gstatic.com; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "object-src 'none'; "
            "base-uri 'self'",
        )
        return resp

    @app.before_request
    def require_login():
        if request.endpoint in ("login", "logout", "register", "forgot_password",
                                "reset_password", "static", "privacidad",
                                "baja_recordatorios", "health"):
            return
        if request.path.startswith("/api/meta/webhook"):
            return
        if request.path.startswith("/api/calendly/webhook"):
            return
        # Any /api/ request with valid x-admin-token bypasses session auth
        if request.path.startswith("/api/"):
            token = request.headers.get("x-admin-token", "")
            expected = os.environ.get("ADMIN_TOKEN", "")
            # compare_digest para no filtrar el token por diferencia de tiempos
            if expected and hmac.compare_digest(token, expected):
                g.admin_token_auth = True
                return
        # Invalidate pre-multiuser sessions that lack user_id
        if session.get("logged_in") and not session.get("user_id"):
            session.clear()
            if request.path.startswith("/api/"):
                return jsonify({"error": "session_expired"}), 401
            return redirect(url_for("login"))
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "session_expired"}), 401
            return redirect(url_for("login"))

        # Sesión válida: aprovechamos la visita para traer lo de Calendly.
        if not request.path.startswith(("/api/", "/static/")):
            _maybe_sync_calendly(app.config["DB_PATH"])

    @app.before_request
    def require_panel():
        """Aplica los permisos de panel del lado del servidor.

        Corre DESPUES de require_login (Flask respeta el orden de registro), asi
        que aca el usuario ya esta autenticado. Hasta ahora panel_access solo
        escondia items del nav en JavaScript.
        """
        return enforce_panel_access(db_path)

    @app.route("/health")
    def health():
        """Healthcheck para Fly. Toca la base a proposito: un proceso que responde
        pero no puede leer SQLite (volumen no montado, base corrupta, disco lleno)
        esta caido a los efectos practicos, y antes se veia sano.

        Exento de login — si no, el chequeo recibiria un redirect a /login y daria
        por sana una app que no puede consultar nada.
        """
        try:
            conn = _connect(db_path)
            try:
                conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
            finally:
                conn.close()
        except Exception as e:
            app.logger.error("Healthcheck fallo al consultar la base: %s", e)
            return jsonify({"status": "error", "db": "unreachable"}), 503
        return jsonify({"status": "ok", "db": "ok"}), 200
    @app.route("/privacidad")
    def privacidad():
        return render_template_string("""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Política de Privacidad — Scalerics</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Segoe UI',Arial,sans-serif;background:#f1f5f9;color:#1e293b;line-height:1.7}
  header{background:#0f1f3d;padding:20px 0;text-align:center}
  header img{height:32px}
  main{max-width:760px;margin:48px auto;background:#fff;border-radius:10px;
       box-shadow:0 2px 16px rgba(15,31,61,.08);padding:48px 56px}
  h1{font-size:26px;font-weight:700;color:#0f1f3d;margin-bottom:8px}
  .updated{font-size:13px;color:#64748b;margin-bottom:36px}
  h2{font-size:16px;font-weight:700;color:#0f1f3d;margin:32px 0 10px}
  p,li{font-size:15px;color:#334155}
  ul{padding-left:20px;margin-top:6px}
  li{margin-bottom:4px}
  a{color:#0088cc;text-decoration:none}
  footer{text-align:center;padding:24px;font-size:13px;color:#94a3b8}
</style>
</head>
<body>
<header>
  <img src="https://raw.githubusercontent.com/juantomasetti1/scalerics-assets/main/logo_full_alt.png" alt="Scalerics">
</header>
<main>
  <h1>Política de Privacidad</h1>
  <p class="updated">Última actualización: junio de 2026</p>

  <h2>1. Quiénes somos</h2>
  <p>Scalerics es una agencia de software y marketing digital. Esta política describe cómo tratamos los datos personales que recopilamos a través de nuestros formularios de Meta Lead Ads y herramientas internas.</p>

  <h2>2. Datos que recopilamos</h2>
  <p>A través de formularios de anuncios en Facebook e Instagram podemos recopilar:</p>
  <ul>
    <li>Nombre completo</li>
    <li>Número de teléfono</li>
    <li>Correo electrónico</li>
    <li>Ciudad o ubicación</li>
  </ul>

  <h2>3. Cómo usamos los datos</h2>
  <p>Los datos recopilados se utilizan exclusivamente para:</p>
  <ul>
    <li>Contactar al interesado en respuesta a su consulta</li>
    <li>Presentar propuestas de servicios de Scalerics</li>
    <li>Gestionar el seguimiento comercial interno</li>
  </ul>
  <p>No compartimos los datos con terceros ni los utilizamos con fines publicitarios propios.</p>

  <h2>4. Almacenamiento y seguridad</h2>
  <p>Los datos se almacenan en una base de datos segura con acceso restringido al equipo interno de Scalerics. Se aplican medidas técnicas para proteger la información contra accesos no autorizados.</p>

  <h2>5. Plazo de conservación</h2>
  <p>Los datos se conservan mientras exista una relación comercial activa o potencial. Podés solicitar la eliminación de tus datos en cualquier momento.</p>

  <h2>6. Tus derechos</h2>
  <p>Tenés derecho a acceder, rectificar o eliminar tus datos personales. Para ejercerlos, contactanos en:</p>
  <p><a href="mailto:juantomasetti240@gmail.com">juantomasetti240@gmail.com</a></p>

  <h2>7. Contacto</h2>
  <p>Ante cualquier consulta sobre esta política podés escribirnos a <a href="mailto:juantomasetti240@gmail.com">juantomasetti240@gmail.com</a>.</p>
</main>
<footer>© 2026 Scalerics · Todos los derechos reservados</footer>
</body>
</html>""")

    @app.route("/baja/<token>", methods=["GET", "POST"])
    def baja_recordatorios(token):
        from services.meta_reminders import dar_de_baja
        dar_de_baja(app.config["DB_PATH"], token)
        # Se responde lo mismo exista o no el token: no tiene sentido decirle a
        # quien se da de baja que su token no servia, y evita sondear tokens.
        return render_template_string("""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Baja confirmada — Scalerics</title></head>
<body style="margin:0;font-family:'Segoe UI',Arial,sans-serif;background:#f1f5f9;color:#1c2b40">
  <div style="max-width:520px;margin:80px auto;background:#fff;border-radius:10px;padding:40px;text-align:center">
    <img src="https://raw.githubusercontent.com/juantomasetti1/scalerics-assets/main/logo_full_alt.png"
         alt="Scalerics" style="height:28px;margin-bottom:24px">
    <h1 style="font-size:20px;margin:0 0 12px">Listo, no te escribimos más</h1>
    <p style="font-size:15px;color:#64748b;margin:0">
      Te sacamos de la lista de recordatorios. Si algun dia queres retomar, escribinos a
      <a href="mailto:contacto@scalerics.com" style="color:#0088cc">contacto@scalerics.com</a>.
    </p>
  </div>
</body>
</html>""")

    @app.route("/login", methods=["GET", "POST"])
    @limiter.limit("10/minute;40/hour", methods=["POST"])
    def login():
        from werkzeug.security import check_password_hash
        from database import get_user_by_email
        error = None
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = get_user_by_email(db_path, email)
            if user and check_password_hash(user["password"], password):
                session["logged_in"] = True
                session["user_id"] = user["id"]
                session["user_name"] = user["name"]
                return redirect(url_for("index"))
            error = "Email o contraseña incorrectos"
        return render_template_string(LOGIN_HTML, error=error)

    @app.route("/register", methods=["GET", "POST"])
    @limiter.limit("5/minute;20/hour", methods=["POST"])
    def register():
        import hmac as _hmac

        from werkzeug.security import generate_password_hash
        from database import create_user, get_user_by_email

        # El alta exige un codigo de invitacion. Sin REGISTER_CODE configurado el
        # registro queda cerrado: falla cerrado a proposito, porque este endpoint
        # esta exento del login y sin codigo cualquiera se creaba una cuenta.
        codigo_ok = os.environ.get("REGISTER_CODE", "").strip()
        if not codigo_ok:
            return render_template_string(
                REGISTER_HTML,
                error="El registro está cerrado. Pedile la cuenta a un administrador.",
            ), 403

        error = None
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            phone = request.form.get("phone", "").strip()
            password = request.form.get("password", "")
            codigo = request.form.get("code", "").strip()
            if not _hmac.compare_digest(codigo, codigo_ok):
                error = "Código de invitación inválido"
            elif not all([name, email, phone, password]):
                error = "Todos los campos son requeridos"
            elif len(password) < 8:
                error = "La contraseña debe tener al menos 8 caracteres"
            elif get_user_by_email(db_path, email):
                error = "Ya existe una cuenta con ese email"
            else:
                uid = create_user(db_path, name=name, email=email, phone=phone,
                                  password_hash=generate_password_hash(password))
                if uid:
                    session["logged_in"] = True
                    session["user_id"] = uid
                    session["user_name"] = name
                    import threading
                    from services.email_service import send_new_user_notification
                    admin_email = os.environ.get("ADMIN_EMAIL", "")
                    if admin_email:
                        threading.Thread(
                            target=send_new_user_notification,
                            args=(name, email, phone, admin_email),
                            daemon=True,
                        ).start()
                    return redirect(url_for("index"))
                error = "Error al crear la cuenta"
        return render_template_string(REGISTER_HTML, error=error)

    @app.route("/forgot-password", methods=["GET", "POST"])
    @limiter.limit("5/minute;20/hour", methods=["POST"])
    def forgot_password():
        import secrets as _secrets
        from database import get_user_by_email, create_reset_token
        from services.email_service import send_reset_email
        message = None
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            user = get_user_by_email(db_path, email)
            if user:
                token = _secrets.token_urlsafe(32)
                create_reset_token(db_path, user_id=user["id"], token=token)
                base_url = request.host_url.rstrip("/")
                reset_url = f"{base_url}/reset-password/{token}"
                send_reset_email(email, reset_url)
            message = "Si el email está registrado, recibirás un link en los próximos minutos."
        return render_template_string(FORGOT_HTML, message=message)

    @app.route("/reset-password/<token>", methods=["GET", "POST"])
    @limiter.limit("10/minute;40/hour", methods=["POST"])
    def reset_password(token):
        from datetime import datetime, timezone
        from werkzeug.security import generate_password_hash
        from database import get_reset_token, use_reset_token, update_user_password

        record = get_reset_token(db_path, token)
        error = None
        valid = False

        if not record:
            error = "Link inválido o ya utilizado."
        elif record["used_at"] is not None:
            error = "Este link ya fue utilizado."
        else:
            created = datetime.fromisoformat(record["created_at"])
            age_hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600
            if age_hours > 1:
                error = "Este link expiró. Pedí uno nuevo."
            else:
                valid = True

        if valid and request.method == "POST":
            password = request.form.get("password", "")
            password2 = request.form.get("password2", "")
            if len(password) < 8:
                error = "La contraseña debe tener al menos 8 caracteres"
                valid = True
            elif password != password2:
                error = "Las contraseñas no coinciden"
                valid = True
            else:
                use_reset_token(db_path, token)
                update_user_password(db_path, record["user_id"],
                                     generate_password_hash(password))
                return redirect(url_for("login"))

        return render_template_string(RESET_HTML, error=error, valid=valid)


    @app.route("/api/users", methods=["GET"])
    def api_users():
        from database import get_all_users
        admin_email = os.environ.get("ADMIN_EMAIL", "").lower()
        users = get_all_users(db_path)
        filtered = [
            u for u in users
            if not (admin_email and u["email"].lower() == admin_email)
            and not (not admin_email and u["id"] == 1)
        ]
        return jsonify(filtered)

    @app.route("/api/activity", methods=["GET"])
    def api_activity():
        import sqlite3 as _sqa
        user_filter = request.args.get("user", "").strip()
        conn_a = _db_connect(db_path); conn_a.row_factory = _sqa.Row
        try:
            # Distinct users for filter dropdown
            users = [r["user_name"] for r in conn_a.execute(
                "SELECT DISTINCT user_name FROM activity_log WHERE user_name NOT IN ('','sistema','sistema-auto','meta_import','calendly','calendly-import') ORDER BY user_name"
            ).fetchall()]
            if user_filter:
                rows = conn_a.execute(
                    "SELECT id,user_name,action,entity_type,entity_id,entity_name,detail,created_at FROM activity_log WHERE user_name=? ORDER BY created_at DESC LIMIT 120",
                    (user_filter,)
                ).fetchall()
            else:
                rows = conn_a.execute(
                    "SELECT id,user_name,action,entity_type,entity_id,entity_name,detail,created_at FROM activity_log ORDER BY created_at DESC LIMIT 120"
                ).fetchall()
            return jsonify({"items": [dict(r) for r in rows], "users": users})
        finally:
            conn_a.close()

    @app.route("/api/sdr-activity", methods=["GET"])
    def api_sdr_activity():
        import sqlite3 as _sq2, datetime as _dt2
        today = _dt2.date.today().isoformat()
        conn2 = _db_connect(db_path); conn2.row_factory = _sq2.Row
        try:
            # Unique leads touched per user today (exclude sistema/automatico)
            today_rows = conn2.execute("""
                SELECT user_name, COUNT(DISTINCT entity_id) as count
                FROM activity_log
                WHERE DATE(created_at) = ? AND entity_type = 'lead'
                  AND user_name NOT IN ('sistema','sistema-auto','')
                GROUP BY user_name ORDER BY count DESC
            """, (today,)).fetchall()
            # Last actor + timestamp per lead
            actor_rows = conn2.execute("""
                SELECT entity_id, user_name, MAX(created_at) as last_at
                FROM activity_log
                WHERE entity_type = 'lead'
                  AND user_name NOT IN ('sistema','sistema-auto','')
                GROUP BY entity_id
            """).fetchall()
            return jsonify({
                "today": [{"user": r["user_name"], "count": r["count"]} for r in today_rows],
                "last_actor": {r["entity_id"]: {"user": r["user_name"], "at": r["last_at"]} for r in actor_rows}
            })
        finally:
            conn2.close()

    @app.route("/api/sdr-stats", methods=["GET"])
    def api_sdr_stats():
        import sqlite3 as _sq3
        from datetime import date, timedelta
        conn3 = _db_connect(db_path); conn3.row_factory = _sq3.Row
        try:
            period = request.args.get('period', 'month')
            today = date.today()
            if period == 'week':
                date_from = (today - timedelta(days=today.weekday())).isoformat()
            elif period == 'year':
                date_from = today.replace(month=1, day=1).isoformat()
            else:
                date_from = today.replace(day=1).isoformat()
                period = 'month'
            # Usuarios que hacen llamadas (rol 'SDR' o 'Caller', el default sembrado)
            sdr_names = [r["name"] for r in conn3.execute("""
                SELECT u.name FROM users u
                JOIN roles r ON u.role_id = r.id
                WHERE r.name IN ('SDR', 'Caller')
            """).fetchall()]
            if not sdr_names:
                return jsonify({"daily": [], "outcomes": [], "period_calls": [], "sdr_users": [], "period": period})
            placeholders = ','.join('?' * len(sdr_names))
            rows = conn3.execute(f"""
                SELECT user_name, DATE(created_at) as day,
                  COUNT(DISTINCT CASE WHEN action='call_logged'
                    OR action='meeting_scheduled'
                    OR action='callback_set'
                    OR (action='status_change' AND detail='reunion_agendada')
                    THEN entity_id END) as calls,
                  COUNT(DISTINCT entity_id) as leads_touched
                FROM activity_log
                WHERE created_at >= date('now','-13 days')
                  AND user_name IN ({placeholders})
                  AND entity_type = 'lead'
                GROUP BY user_name, day
                ORDER BY day ASC
            """, sdr_names).fetchall()
            # Ultimo outcome por lead por dia (si se equivoca y corrige, cuenta el ultimo)
            daily_outcomes = conn3.execute(f"""
                SELECT user_name, day, outcome, COUNT(*) as c
                FROM (
                    SELECT user_name, DATE(created_at) as day, detail as outcome, entity_id,
                           ROW_NUMBER() OVER (
                               PARTITION BY user_name, entity_id, DATE(created_at)
                               ORDER BY created_at DESC
                           ) as rn
                    FROM activity_log
                    WHERE action='call_logged'
                      AND user_name IN ({placeholders})
                      AND created_at >= date('now','-13 days')
                )
                WHERE rn = 1
                GROUP BY user_name, day, outcome
            """, sdr_names).fetchall()
            period_calls = conn3.execute(f"""
                SELECT user_name, COUNT(*) as c
                FROM activity_log
                WHERE action='call_logged'
                  AND user_name IN ({placeholders})
                  AND DATE(created_at) >= ?
                GROUP BY user_name
            """, sdr_names + [date_from]).fetchall()

            # llamar_despues neto: call_logged detail=llamar_despues O callback_set,
            # excluyendo leads donde hubo otra llamada posterior el mismo dia
            llamar_despues = conn3.execute(f"""
                SELECT al.user_name, DATE(al.created_at) as day, COUNT(DISTINCT al.entity_id) as c
                FROM activity_log al
                WHERE (
                    (al.action='call_logged' AND al.detail='llamar_despues')
                    OR al.action='callback_set'
                )
                  AND al.user_name IN ({placeholders})
                  AND al.created_at >= date('now','-13 days')
                  AND NOT EXISTS (
                    SELECT 1 FROM activity_log later
                    WHERE later.action='call_logged'
                      AND later.entity_id=al.entity_id
                      AND later.user_name=al.user_name
                      AND DATE(later.created_at)=DATE(al.created_at)
                      AND later.created_at > al.created_at
                  )
                GROUP BY al.user_name, day
            """, sdr_names).fetchall()

            # Reuniones: meeting_scheduled siempre cuenta; status_change solo si fue el ultimo del dia
            reuniones_cal = conn3.execute(f"""
                SELECT user_name, DATE(created_at) as day, COUNT(DISTINCT entity_id) as c
                FROM (
                    SELECT user_name, entity_id, created_at
                    FROM activity_log
                    WHERE action='meeting_scheduled'
                      AND user_name IN ({placeholders})
                      AND created_at >= date('now','-13 days')
                    UNION ALL
                    SELECT user_name, entity_id, created_at
                    FROM (
                        SELECT user_name, entity_id, created_at, detail,
                               ROW_NUMBER() OVER (
                                   PARTITION BY user_name, entity_id, DATE(created_at)
                                   ORDER BY created_at DESC
                               ) as rn
                        FROM activity_log
                        WHERE (action='status_change' OR (action='call_logged' AND detail='reunion'))
                          AND user_name IN ({placeholders})
                          AND created_at >= date('now','-13 days')
                    )
                    WHERE rn = 1 AND detail='reunion_agendada'
                )
                GROUP BY user_name, DATE(created_at)
            """, sdr_names + sdr_names).fetchall()

            period_reuniones = conn3.execute(f"""
                SELECT user_name, COUNT(DISTINCT entity_id) as c
                FROM (
                    SELECT user_name, entity_id
                    FROM activity_log
                    WHERE action='meeting_scheduled'
                      AND user_name IN ({placeholders})
                      AND DATE(created_at) >= ?
                    UNION ALL
                    SELECT user_name, entity_id
                    FROM (
                        SELECT user_name, entity_id, detail,
                               ROW_NUMBER() OVER (
                                   PARTITION BY user_name, entity_id, DATE(created_at)
                                   ORDER BY created_at DESC
                               ) as rn
                        FROM activity_log
                        WHERE (action='status_change' OR (action='call_logged' AND detail='reunion'))
                          AND user_name IN ({placeholders})
                          AND DATE(created_at) >= ?
                    )
                    WHERE rn = 1 AND detail='reunion_agendada'
                )
                GROUP BY user_name
            """, sdr_names + [date_from] + sdr_names + [date_from]).fetchall()

            return jsonify({
                "daily": [{"user": r["user_name"], "day": r["day"], "calls": r["calls"], "leads": r["leads_touched"]} for r in rows],
                "daily_outcomes": [{"user": r["user_name"], "day": r["day"], "outcome": r["outcome"], "count": r["c"]} for r in daily_outcomes],
                "period_calls": [{"user": r["user_name"], "count": r["c"]} for r in period_calls],
                "llamar_despues": [{"user": r["user_name"], "day": r["day"], "count": r["c"]} for r in llamar_despues],
                "reuniones_cal": [{"user": r["user_name"], "day": r["day"], "count": r["c"]} for r in reuniones_cal],
                "period_reuniones": [{"user": r["user_name"], "count": r["c"]} for r in period_reuniones],
                "sdr_users": sdr_names,
                "period": period,
            })
        finally:
            conn3.close()

    @app.route("/api/sdr-detail", methods=["GET"])
    def api_sdr_detail():
        import sqlite3 as _sq5
        from collections import OrderedDict
        user  = request.args.get('user', '').strip()
        day   = request.args.get('day', '').strip()
        type_ = request.args.get('type', 'calls').strip()
        if not user or not day:
            return jsonify({"leads": []})
        conn5 = _db_connect(db_path); conn5.row_factory = _sq5.Row
        try:
            if type_ == 'calls':
                rows = conn5.execute(
                    "SELECT entity_id, entity_name, detail, created_at FROM activity_log "
                    "WHERE action='call_logged' AND user_name=? AND DATE(created_at)=? ORDER BY created_at",
                    (user, day)
                ).fetchall()
            elif type_ == 'reunion_cal':
                rows = conn5.execute(
                    "SELECT entity_id, entity_name, detail, created_at FROM activity_log "
                    "WHERE (action='meeting_scheduled' OR (action='call_logged' AND detail='reunion') "
                    "       OR (action='status_change' AND detail='reunion_agendada')) "
                    "AND user_name=? AND DATE(created_at)=? ORDER BY created_at",
                    (user, day)
                ).fetchall()
            elif type_ == 'llamar_despues':
                rows = conn5.execute(
                    "SELECT al.entity_id, al.entity_name, al.detail, al.created_at FROM activity_log al "
                    "WHERE ((al.action='call_logged' AND al.detail='llamar_despues') OR al.action='callback_set') "
                    "AND al.user_name=? AND DATE(al.created_at)=? "
                    "AND NOT EXISTS ("
                    "  SELECT 1 FROM activity_log later WHERE later.action='call_logged' "
                    "  AND later.entity_id=al.entity_id AND later.user_name=al.user_name "
                    "  AND DATE(later.created_at)=DATE(al.created_at) AND later.created_at > al.created_at"
                    ") ORDER BY al.created_at",
                    (user, day)
                ).fetchall()
            else:
                rows = conn5.execute(
                    "SELECT entity_id, entity_name, detail, created_at FROM activity_log "
                    "WHERE action='call_logged' AND user_name=? AND DATE(created_at)=? AND detail=? ORDER BY created_at",
                    (user, day, type_)
                ).fetchall()
            leads = OrderedDict()
            for r in rows:
                eid = r['entity_id']
                if eid not in leads:
                    leads[eid] = {'id': eid, 'name': r['entity_name'], 'call_count': 0,
                                  'last_outcome': r['detail'], 'last_time': r['created_at']}
                leads[eid]['call_count'] += 1
                leads[eid]['last_outcome'] = r['detail']
                leads[eid]['last_time']    = r['created_at']
            return jsonify({"leads": list(leads.values())})
        finally:
            conn5.close()

    @app.route("/api/me", methods=["GET"])
    def api_me():
        from database import get_user_by_id
        user_id = session.get("user_id")
        user = get_user_by_id(db_path, user_id) if user_id else None
        if not user:
            return jsonify({"error": "not_logged_in"}), 401
        # Misma funcion que usan los endpoints: si el backend te deja entrar, el
        # nav tiene que mostrarte el link. Cuando esto se calculaba aparte, el rol
        # "Admin" no contaba y la seccion quedaba invisible para quien si podia.
        es_admin = is_admin(db_path, user_id)
        # Role-based access: role takes priority over direct panel_access
        import sqlite3 as _sq2, json as _j2
        panel_access = None
        if not es_admin:
            role_id = user.get("role_id")
            if role_id:
                conn3 = _db_connect(db_path); conn3.row_factory = _sq2.Row
                try:
                    role = conn3.execute("SELECT name, panel_access FROM roles WHERE id=?", (role_id,)).fetchone()
                    if role:
                        if (role["name"] or "").lower() == "admin":
                            es_admin = True
                        else:
                            panel_access = role["panel_access"]
                finally: conn3.close()
            else:
                panel_access = "[]"  # sin rol = sin acceso
        return jsonify({
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "phone": user["phone"],
            "is_admin": es_admin,
            "panel_access": panel_access,
            "role_id": user.get("role_id"),
            # El frontend tenia esta lista hardcodeada en dos constantes distintas,
            # y a las dos les faltaba 'sdr'. Ahora viene del backend.
            "all_panels": list(ALL_PANELS),
        })

    @app.route("/api/me", methods=["PUT"])
    def api_me_update():
        from werkzeug.security import generate_password_hash
        from database import get_user_by_id, get_user_by_email, update_user_password, update_user_profile
        user_id = session.get("user_id")
        user = get_user_by_id(db_path, user_id) if user_id else None
        if not user:
            return jsonify({"error": "No autorizado"}), 403
        data = request.get_json(force=True) or {}
        name = (data.get("name") or "").strip()
        email = (data.get("email") or "").strip().lower()
        phone = (data.get("phone") or "").strip()
        password = data.get("password", "")
        if not name or not email or not phone:
            return jsonify({"error": "Nombre, email y tel\u00e9fono son requeridos"}), 400
        if password and len(password) < 8:
            return jsonify({"error": "La contrase\u00f1a debe tener al menos 8 caracteres"}), 400
        if email != user["email"]:
            existing = get_user_by_email(db_path, email)
            if existing and existing["id"] != user_id:
                return jsonify({"error": "Ya existe una cuenta con ese email"}), 409
        update_user_profile(db_path, user_id, name=name, email=email, phone=phone)
        if password:
            update_user_password(db_path, user_id, generate_password_hash(password))
        session["user_name"] = name
        return jsonify({"ok": True})

    @app.route("/api/admin/users/<int:uid>/panel-access", methods=["PUT"])
    def admin_set_panel_access(uid):
        import json as _json
        from database import get_user_by_id
        current = get_user_by_id(db_path, session.get("user_id")) or {}
        if not is_admin(db_path, session.get("user_id")):
            return jsonify({"ok": False, "error": "No autorizado"}), 403
        data = request.get_json() or {}
        panels = data.get("panels")  # None = all access, list = specific panels
        val = _json.dumps(panels) if panels is not None else None
        conn2 = _db_connect(db_path)
        try:
            conn2.execute("UPDATE users SET panel_access=? WHERE id=?", (val, uid))
            conn2.commit()
        finally:
            conn2.close()
        return jsonify({"ok": True})

    @app.route("/api/admin/roles", methods=["GET"])
    def admin_list_roles():
        import sqlite3 as _sq
        err = require_admin(db_path)
        if err:
            return err
        conn2 = _db_connect(db_path); conn2.row_factory = _sq.Row
        try:
            rows = conn2.execute("SELECT * FROM roles ORDER BY id").fetchall()
            return jsonify([dict(r) for r in rows])
        finally: conn2.close()

    @app.route("/api/admin/roles", methods=["POST"])
    def admin_create_role():
        import sqlite3 as _sq, json as _j
        err = require_admin(db_path)
        if err:
            return err
        data = request.get_json() or {}
        name = (data.get("name") or "").strip()
        panels = data.get("panels", [])
        if not name: return jsonify({"ok": False, "error": "Nombre requerido"}), 400
        conn2 = _db_connect(db_path)
        try:
            conn2.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)", (name, _j.dumps(panels)))
            conn2.commit()
            rid = conn2.execute("SELECT last_insert_rowid()").fetchone()[0]
            return jsonify({"ok": True, "id": rid})
        except _sq.IntegrityError: return jsonify({"ok": False, "error": "Nombre ya existe"}), 409
        finally: conn2.close()

    @app.route("/api/admin/roles/<int:rid>", methods=["PUT"])
    def admin_update_role(rid):
        import sqlite3 as _sq, json as _j
        err = require_admin(db_path)
        if err:
            return err
        data = request.get_json() or {}
        name = (data.get("name") or "").strip()
        panels = data.get("panels")
        conn2 = _db_connect(db_path)
        try:
            if name: conn2.execute("UPDATE roles SET name=? WHERE id=?", (name, rid))
            if panels is not None: conn2.execute("UPDATE roles SET panel_access=? WHERE id=?", (_j.dumps(panels), rid))
            conn2.commit()
            return jsonify({"ok": True})
        finally: conn2.close()

    @app.route("/api/admin/roles/<int:rid>", methods=["DELETE"])
    def admin_delete_role(rid):
        import sqlite3 as _sq
        err = require_admin(db_path)
        if err:
            return err
        conn2 = _db_connect(db_path)
        try:
            conn2.execute("UPDATE users SET role_id=NULL WHERE role_id=?", (rid,))
            conn2.execute("DELETE FROM roles WHERE id=?", (rid,))
            conn2.commit()
            return jsonify({"ok": True})
        finally: conn2.close()

    @app.route("/api/admin/users/<int:uid>/role", methods=["PUT"])
    def admin_set_user_role(uid):
        import sqlite3 as _sq
        err = require_admin(db_path)
        if err:
            return err
        data = request.get_json() or {}
        role_id = data.get("role_id")  # None = no role (full access for admins)
        conn2 = _db_connect(db_path)
        try:
            conn2.execute("UPDATE users SET role_id=? WHERE id=?", (role_id, uid))
            conn2.commit()
            return jsonify({"ok": True})
        finally: conn2.close()

    @app.route("/api/admin/users-data", methods=["GET"])
    def admin_users_data():
        from database import get_user_by_id, get_all_users
        current_user_id = session.get("user_id")
        if not is_admin(db_path, current_user_id):
            return jsonify({"error": "No autorizado"}), 403
        current = get_user_by_id(db_path, current_user_id) if current_user_id else None
        users = get_all_users(db_path)
        return jsonify([dict(u) for u in users])

    @app.route("/api/admin/users", methods=["GET"])
    def admin_list_users():
        from database import get_user_by_id, get_all_users
        current_user_id = session.get("user_id")
        if not is_admin(db_path, current_user_id):
            return jsonify({"error": "No autorizado"}), 403
        current = get_user_by_id(db_path, current_user_id) if current_user_id else None
        return jsonify(get_all_users(db_path))

    @app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
    def admin_delete_user(user_id):
        from database import get_user_by_id, delete_user
        current_user_id = session.get("user_id")
        if not is_admin(db_path, current_user_id):
            return jsonify({"error": "No autorizado"}), 403
        current = get_user_by_id(db_path, current_user_id) if current_user_id else None
        if user_id == current_user_id:
            return jsonify({"error": "No podés eliminar tu propia cuenta"}), 400
        delete_user(db_path, user_id)
        return jsonify({"ok": True})

    @app.route("/api/admin/users/<int:user_id>/reset-password", methods=["POST"])
    def admin_reset_user_password(user_id):
        import secrets as _secrets
        from database import get_user_by_id, create_reset_token
        from services.email_service import send_reset_email
        current_user_id = session.get("user_id")
        if not is_admin(db_path, current_user_id):
            return jsonify({"error": "No autorizado"}), 403
        current = get_user_by_id(db_path, current_user_id) if current_user_id else None
        target = get_user_by_id(db_path, user_id)
        if not target:
            return jsonify({"error": "Usuario no encontrado"}), 404
        token = _secrets.token_urlsafe(32)
        create_reset_token(db_path, user_id=user_id, token=token)
        base_url = request.host_url.rstrip("/")
        reset_url = f"{base_url}/reset-password/{token}"
        send_reset_email(target["email"], reset_url)
        return jsonify({"ok": True, "reset_url": reset_url})

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/profile", methods=["GET", "POST"])
    def profile():
        from werkzeug.security import generate_password_hash
        from database import get_user_by_id, get_user_by_email, update_user_password, update_user_profile
        user_id = session.get("user_id")
        user = get_user_by_id(db_path, user_id) if user_id else None
        if not user:
            return redirect(url_for("login"))
        error = None
        success = None
        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            email = (request.form.get("email") or "").strip().lower()
            phone = (request.form.get("phone") or "").strip()
            password = request.form.get("password", "")
            password2 = request.form.get("password2", "")
            if not name or not email or not phone:
                error = "Nombre, email y teléfono son requeridos"
            elif password and len(password) < 8:
                error = "La contraseña debe tener al menos 8 caracteres"
            elif password and password != password2:
                error = "Las contraseñas no coinciden"
            else:
                if email != user["email"]:
                    existing = get_user_by_email(db_path, email)
                    if existing and existing["id"] != user_id:
                        error = "Ya existe una cuenta con ese email"
                if not error:
                    update_user_profile(db_path, user_id, name=name, email=email, phone=phone)
                    if password:
                        update_user_password(db_path, user_id, generate_password_hash(password))
                    session["user_name"] = name
                    user = get_user_by_id(db_path, user_id)
                    success = "Cambios guardados"
        PROFILE_PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mi perfil — Scalerics</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:#0a0f1a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}
.card{background:#111827;border:1px solid #1e293b;border-radius:14px;padding:32px;width:100%;max-width:440px}
.back{display:inline-flex;align-items:center;gap:6px;color:#64748b;font-size:.82rem;text-decoration:none;margin-bottom:20px}
.back:hover{color:#e2e8f0}
h2{font-size:1.1rem;font-weight:700;margin-bottom:24px}
label{display:block;font-size:.72rem;color:#64748b;margin-bottom:4px}
input{width:100%;background:#0a0f1a;border:1px solid #1e293b;border-radius:6px;padding:9px 12px;color:#e2e8f0;font-size:.85rem;font-family:inherit;outline:none;margin-bottom:14px}
input:focus{border-color:#0088CC}
.sep{border-top:1px solid #1e293b;margin:18px 0;font-size:.75rem;color:#475569;padding-top:14px}
.btn{width:100%;background:#0088CC;border:none;border-radius:8px;padding:11px;color:#fff;font-size:.88rem;font-weight:600;cursor:pointer;font-family:inherit;margin-top:4px}
.btn:hover{background:#0077bb}
.msg-ok{background:rgba(16,185,129,.12);color:#10B981;border-radius:6px;padding:8px 12px;font-size:.8rem;margin-bottom:14px}
.msg-err{background:rgba(239,68,68,.1);color:#f87171;border-radius:6px;padding:8px 12px;font-size:.8rem;margin-bottom:14px}
</style>
</head>
<body>
<div class="card">
  <a class="back" href="/">&#8592; Volver al dashboard</a>
  <h2>Mi perfil</h2>
  {% if success %}<div class="msg-ok">{{ success }}</div>{% endif %}
  {% if error %}<div class="msg-err">{{ error }}</div>{% endif %}
  <form method="POST">
    <label>Nombre</label>
    <input type="text" name="name" value="{{ user.name }}" required>
    <label>Email</label>
    <input type="email" name="email" value="{{ user.email }}" required>
    <label>Teléfono</label>
    <input type="text" name="phone" value="{{ user.phone }}" required>
    <div class="sep">Cambiar contraseña (dejá vacío para no cambiar)</div>
    <label>Nueva contraseña</label>
    <input type="password" name="password" autocomplete="new-password">
    <label>Confirmar contraseña</label>
    <input type="password" name="password2" autocomplete="new-password">
    <button class="btn" type="submit">Guardar cambios</button>
  </form>
</div>
</body>
</html>"""
        return render_template_string(PROFILE_PAGE, user=user, error=error, success=success)

    @app.route("/admin/users", methods=["GET"])
    def admin_users_page():
        from database import get_user_by_id, get_all_users, delete_user
        user_id = session.get("user_id")
        current = get_user_by_id(db_path, user_id) if user_id else None
        if not current:
            return redirect(url_for("login"))
        es_admin = is_admin(db_path, user_id)
        if not es_admin:
            return redirect(url_for("index"))
        users = get_all_users(db_path)
        ADMIN_PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gestión — Scalerics</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:#0a0f1a;color:#e2e8f0;min-height:100vh;padding:32px;max-width:780px}
.back{display:inline-flex;align-items:center;gap:6px;color:#64748b;font-size:.82rem;text-decoration:none;margin-bottom:24px}
.back:hover{color:#e2e8f0}
h2{font-size:1rem;font-weight:700;margin-bottom:6px;color:#f1f5f9}
.section{margin-bottom:36px}
.section-title{font-size:.7rem;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.9px;margin-bottom:14px;padding-bottom:8px;border-bottom:1px solid #1e293b}
.card{background:#111827;border:1px solid #1e293b;border-radius:10px;padding:16px;margin-bottom:10px}
.row{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.name{font-size:.88rem;font-weight:600;flex:1}
.sub{font-size:.72rem;color:#64748b}
.badge{font-size:.7rem;font-weight:600;background:#1e293b;color:#94a3b8;padding:2px 8px;border-radius:99px}
.badge.has-role{background:rgba(0,136,204,.15);color:#60a5fa}
input[type=text]{background:#0a0f1a;border:1px solid #1e293b;border-radius:6px;color:#e2e8f0;padding:7px 10px;font-size:.82rem;font-family:inherit;outline:none;width:180px}
input[type=text]:focus{border-color:#0088cc}
select{background:#0a0f1a;border:1px solid #1e293b;border-radius:6px;color:#e2e8f0;padding:6px 10px;font-size:.82rem;font-family:inherit;outline:none;cursor:pointer}
select:focus{border-color:#0088cc}
.btn{border:none;border-radius:6px;padding:6px 14px;font-size:.78rem;font-weight:600;cursor:pointer;font-family:inherit;transition:opacity .15s}
.btn:hover{opacity:.85}
.btn-primary{background:#0088cc;color:#fff}
.btn-ghost{background:#1e293b;color:#94a3b8}
.btn-ghost:hover{color:#e2e8f0}
.btn-danger{background:#2a1515;border:1px solid #7f1d1d;color:#f87171}
.panels-wrap{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.chip{display:inline-flex;align-items:center;gap:4px;background:#1e293b;border:1px solid #334155;border-radius:5px;padding:3px 9px;font-size:.72rem;cursor:pointer;user-select:none;transition:all .15s}
.chip input{accent-color:#0088cc;cursor:pointer;width:12px;height:12px}
.chip.on{border-color:#0088cc;background:rgba(0,136,204,.12);color:#60a5fa}
.chip.meta-on{border-color:#c084fc;background:rgba(192,132,252,.1);color:#c084fc}
.toast{display:none;font-size:.75rem;color:#4ade80;margin-left:8px}
.msg-ok{background:rgba(16,185,129,.1);color:#4ade80;border-radius:6px;padding:8px 12px;font-size:.8rem;margin-bottom:14px}
.divider{height:1px;background:#1e293b;margin:10px 0}
</style>
</head>
<body>
<a class="back" href="/">&#8592; Dashboard</a>

<div class="section">
  <div class="section-title">Roles y permisos</div>
  <div id="roles-list"></div>
  <div class="card" id="new-role-form">
    <div class="row">
      <input type="text" id="new-role-name" placeholder="Nombre del rol" maxlength="40">
      <button class="btn btn-primary" onclick="createRole()">+ Crear rol</button>
    </div>
    <div class="panels-wrap" id="new-role-panels"></div>
  </div>
</div>

<div class="section">
  <div class="section-title">Usuarios</div>
  {% if request.args.get('deleted') %}<div class="msg-ok">Usuario eliminado</div>{% endif %}
  <div id="users-list"></div>
</div>

<script>
// Esta pagina se arma con innerHTML igual que el dashboard, pero no tenia ningun
// helper de escapado. Como cualquier usuario puede cambiarse el nombre desde su
// perfil, un nombre con HTML se ejecutaba en la sesion del admin al abrir
// /admin/users: escalada desde el rol mas bajo. Mismos helpers que DASHBOARD_HTML.
function esc(s) { return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }

// Viene del backend (services/auth.ALL_PANELS) para que no vuelva a divergir con
// la del dashboard: a ambas les faltaba 'sdr', y como getChecked() itera esta
// lista, guardar un rol borraba ese permiso de la base sin avisar.
const ALL_PANELS = {{ all_panels | tojson }};
const PANEL_LABELS = {cola:'Cola',seguimientos:'Seguimientos',meta:'Meta Ads',pipeline:'Pipeline',clientes:'Clientes',tasks:'Tareas',wa:'WhatsApp',cal:'Calendario',metrics:'Métricas',sdr:'SDR',activity:'Actividad'};
let _roles = [];

function makeChips(containerId, checkedArr, prefix) {
  const el = document.getElementById(containerId);
  el.innerHTML = ALL_PANELS.map(p => {
    const on = checkedArr ? checkedArr.includes(p) : true;
    const isMeta = p === 'meta';
    return `<label class="chip ${on?(isMeta?'meta-on':'on'):''}" id="${prefix}-chip-${p}">
      <input type="checkbox" id="${prefix}-cb-${p}" ${on?'checked':''} onchange="toggleChip('${prefix}','${p}',this.checked)">
      ${PANEL_LABELS[p]}
    </label>`;
  }).join('');
}
function toggleChip(prefix, p, on) {
  const chip = document.getElementById(`${prefix}-chip-${p}`);
  chip.classList.toggle('on', on && p !== 'meta');
  chip.classList.toggle('meta-on', on && p === 'meta');
}
function getChecked(prefix) {
  return ALL_PANELS.filter(p => document.getElementById(`${prefix}-cb-${p}`)?.checked);
}

async function loadRoles() {
  const r = await fetch('/api/admin/roles');
  _roles = await r.json();
  renderRoles();
  renderUsers();
}

function renderRoles() {
  const el = document.getElementById('roles-list');
  el.innerHTML = _roles.map(role => {
    const panels = JSON.parse(role.panel_access || '[]');
    return `<div class="card" id="role-card-${role.id}">
      <div class="row">
        <input type="text" value="${role.name}" id="role-name-${role.id}" style="flex:1;max-width:200px">
        <button class="btn btn-primary" onclick="saveRole(${role.id})">Guardar</button>
        <button class="btn btn-danger" onclick="deleteRole(${role.id},'${role.name}')">Borrar</button>
        <span class="toast" id="role-toast-${role.id}">✓</span>
      </div>
      <div class="panels-wrap" id="role-panels-${role.id}"></div>
    </div>`;
  }).join('');
  _roles.forEach(role => {
    const panels = JSON.parse(role.panel_access || '[]');
    makeChips(`role-panels-${role.id}`, panels, `r${role.id}`);
  });
}

async function saveRole(id) {
  const name = document.getElementById(`role-name-${id}`).value.trim();
  const panels = ALL_PANELS.filter(p => document.getElementById(`r${id}-cb-${p}`)?.checked);
  const r = await fetch(`/api/admin/roles/${id}`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name, panels})});
  if (r.ok) {
    const t = document.getElementById(`role-toast-${id}`);
    t.style.display='inline'; setTimeout(()=>{t.style.display='none'},2000);
    await loadRoles();
  }
}

async function deleteRole(id, name) {
  if (!confirm(`Borrar el rol "${name}"? Los usuarios con este rol quedarán sin rol.`)) return;
  await fetch(`/api/admin/roles/${id}`, {method:'DELETE'});
  await loadRoles();
}

// New role form
makeChips('new-role-panels', [], 'new');
async function createRole() {
  const name = document.getElementById('new-role-name').value.trim();
  if (!name) { document.getElementById('new-role-name').focus(); return; }
  const panels = ALL_PANELS.filter(p => document.getElementById(`new-cb-${p}`)?.checked);
  const r = await fetch('/api/admin/roles', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name, panels})});
  const d = await r.json();
  if (d.ok) { document.getElementById('new-role-name').value=''; await loadRoles(); }
  else alert(d.error);
}

let _users = {{ users | tojson }};
function renderUsers() {
  const el = document.getElementById('users-list');
  const users = _users;
  el.innerHTML = users.map(u => {
    const opts = `<option value="">Sin rol (sin acceso)</option>` +
      _roles.map(r => `<option value="${r.id}" ${u.role_id==r.id?'selected':''}>${esc(r.name)}</option>`).join('');
    const roleName = u.role_name || 'Sin rol';
    return `<div class="card">
      <div class="row">
        <div style="flex:1">
          <div class="name">${esc(u.name)}</div>
          <div class="sub">${esc(u.email)} · ${esc(u.phone)}</div>
        </div>
        <span class="badge ${u.role_id?'has-role':''}">${esc(roleName)}</span>
        <select onchange="setRole(${u.id}, this.value)">${opts}</select>
        <form method="POST" action="/admin/users/${u.id}/delete" onsubmit="return confirm('Eliminar a ' + ${escJs(u.name)} + '?')" style="display:inline">
          <button class="btn btn-danger" type="submit">Borrar</button>
        </form>
      </div>
    </div>`;
  }).join('');
}

async function setRole(uid, roleId) {
  await fetch(`/api/admin/users/${uid}/role`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({role_id: roleId ? parseInt(roleId) : null})});
  await loadAll();
}

async function loadAll() {
  const [rolesRes, usersRes] = await Promise.all([fetch('/api/admin/roles'), fetch('/api/admin/users-data')]);
  _roles = await rolesRes.json();
  _users = await usersRes.json();
  renderRoles();
  renderUsers();
}

loadAll();
</script>
</body>
</html>"""
        return render_template_string(ADMIN_PAGE, users=users, all_panels=list(ALL_PANELS))

    @app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
    def admin_delete_user_page(user_id):
        from database import get_user_by_id, delete_user
        current_uid = session.get("user_id")
        current = get_user_by_id(db_path, current_uid) if current_uid else None
        if not current:
            return redirect(url_for("login"))
        es_admin = is_admin(db_path, current_uid)
        if not es_admin or user_id == current_uid:
            return redirect(url_for("index"))
        delete_user(db_path, user_id)
        return redirect(url_for("admin_users_page") + "?deleted=1")

    @app.route("/")
    def index():
        from flask import make_response
        resp = make_response(render_template_string(DASHBOARD_HTML))
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        return resp

    # Los procesos de fondo se saltean con CRM_SIN_PROCESOS_DE_FONDO=true.
    # En los tests cada create_app dejaba tres threads vivos que seguian
    # tocando SQLite durante el test siguiente: de ahi el "database is locked"
    # intermitente y el ruido de "no such table: jobs".
    if os.environ.get("CRM_SIN_PROCESOS_DE_FONDO", "").lower() != "true":
        worker = init_worker(db_path)
        worker.register("demo", demo_job_handler)
        worker.start()

        start_meta_token_monitor(app)
        start_meta_daily_import(app)
        iniciar_scheduler(app)

        from services.meta_reminders import start_meta_reminders
        start_meta_reminders(app)

    try:
        from database import get_all_users
        if not get_all_users(db_path):
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "No hay usuarios registrados. Entrá a /register para crear el primer usuario."
            )
    except Exception:
        pass

    return app


def run(db_path: str) -> None:
    init_db(db_path)
    seed_pitch_templates(db_path)
    app = create_app(db_path)
    threading.Timer(1.2, lambda: webbrowser.open("http://localhost:5000")).start()
    app.run(port=5000, debug=False, use_reloader=False)
