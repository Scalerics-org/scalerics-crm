import os
import secrets
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
from routes.tokens import tokens_bp
from routes.meta import meta_bp, start_meta_token_monitor, start_meta_daily_import
from routes.calendly import calendly_bp
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
.topbar{display:none;align-items:center;gap:12px;padding:12px 16px;background:#111827;border-bottom:1px solid #1a2d3d;position:sticky;top:0;z-index:100}
.topbar img{height:24px;object-fit:contain}
.hamburger{background:none;border:none;color:#94a3b8;font-size:1.3rem;cursor:pointer;padding:4px 6px;line-height:1;flex-shrink:0}
.main{margin-left:228px;padding:28px 32px;flex:1;min-width:0}
.panel{display:none}
.panel.active{display:block}
@media(max-width:768px){
  .topbar{display:none!important}
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
body.light .topbar{background:#fff;border-bottom-color:#e2e8f0}
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

<div class="topbar">
  <button class="hamburger" onclick="toggleSidebar()">☰</button>
  <img src="data:image/png;base64,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" alt="Scalerics" style="height:28px">
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
        <button class="pitch-btn" onclick="openCallModal(${b.id},'${esc(b.name||'')}','${esc(b.phone||'')}','cola')" style="background:#1e293b"><i data-lucide=\"clipboard-list\" class=\"btn-icon\"></i> Resultado</button>
        <button class="delete-btn" onclick="deleteLead(${b.id},'${esc(b.name||'')}')" title="Borrar"><i data-lucide=\"trash-2\" class=\"btn-icon\"></i></button>
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
          <button class="pitch-btn" onclick="openCallModal(${b.id},'${esc(b.name||'')}','${esc(b.phone||'')}','seguimientos')" style="background:#1e293b"><i data-lucide=\"clipboard-list\" class=\"btn-icon\"></i> Resultado</button>
          <button class="delete-btn" onclick="deleteLead(${b.id},'${esc(b.name||'')}')" title="Borrar"><i data-lucide=\"trash-2\" class=\"btn-icon\"></i></button>
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
        <button class="delete-btn" onclick="deleteLead(${b.id},event)" title="Borrar lead">🗑</button>
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
  const cols = ['id','name','phone','category','city','crm_status','rating','address','scraped_at'];
  const headers = ['ID','Nombre','Teléfono','Rubro','Ciudad','Estado CRM','Rating','Dirección','Fecha scrape'];
  const rows = [headers.join(',')];
  for (const b of _allLeads) {
    const row = cols.map(k => {
      const v = b[k] == null ? '' : String(b[k]);
      return '"' + v.replace(/"/g, '""') + '"';
    });
    rows.push(row.join(','));
  }
  const blob = new Blob([rows.join('\\n')], {type: 'text/csv;charset=utf-8;'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'leads_scalerics.csv'; a.click();
  URL.revokeObjectURL(url);
}

function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
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
              ${ev.meeting_url?`<a class="cal-join-btn" href="${esc(ev.meeting_url)}" target="_blank" onclick="event.stopPropagation()">▶ Unirse</a>`:''}
              <button class="cal-demo-btn" onclick="event.stopPropagation();openDemoModal('${ph||''}','${esc(ev.title||'')}','${nm||''}')">📊 Generar Demo</button>
              <button class="cal-del-btn" onclick="event.stopPropagation();deleteCalEvent('${ev.id}','${esc(ev.title||'')}')">🗑 Borrar</button>
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
  if (d.ok) { renderCalendar(); }
  else { alert('Error al borrar: ' + (d.error || 'desconocido')); }
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
  if (!d.ok) { alert('Error: '+(d.error||'Error desconocido')); return; }
  closeNewEventModal();
  renderCalendar();
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
const ALL_PANELS = ['cola','seguimientos','meta','pipeline','clientes','tasks','wa','cal','metrics','activity'];
(async () => {
  try {
    const r = await fetch('/api/me');
    if (!r.ok) return;
    const m = await r.json();
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
    ${l.phone ? `<div class="cp-field"><span class="cp-field-label">Teléfono</span><span class="cp-field-val">${l.phone}</span></div>` : ''}
    ${l.city ? `<div class="cp-field"><span class="cp-field-label">Ciudad</span><span class="cp-field-val">${l.city}</span></div>` : ''}
    ${l.category ? `<div class="cp-field"><span class="cp-field-label">Rubro</span><span class="cp-field-val">${l.category}</span></div>` : ''}
    ${l.address ? `<div class="cp-field"><span class="cp-field-label">Dirección</span><span class="cp-field-val">${l.address}</span></div>` : ''}
    ${l.maps_url ? `<div class="cp-field"><span class="cp-field-label">Google Maps</span><span class="cp-field-val"><a href="${l.maps_url}" target="_blank" style="color:#3b82f6">Ver en Maps →</a></span></div>` : ''}
    ${stars ? `<div class="cp-field"><span class="cp-field-label">Rating</span><span class="cp-field-val">${stars}</span></div>` : ''}
    ${l.instagram_url ? `<div class="cp-field"><span class="cp-field-label">Instagram</span><span class="cp-field-val"><a href="${esc(l.instagram_url)}" target="_blank" style="display:inline-flex;align-items:center;gap:7px;color:#e2e8f0;text-decoration:none"><span style="display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:6px;background:linear-gradient(135deg,#f09433,#dc2743,#bc1888);color:#fff;font-size:.6rem;font-weight:800;flex-shrink:0">IG</span>Ver perfil →</a></span></div>` : ''}
    ${l.facebook_url ? `<div class="cp-field"><span class="cp-field-label">Facebook</span><span class="cp-field-val"><a href="${esc(l.facebook_url)}" target="_blank" style="display:inline-flex;align-items:center;gap:7px;color:#e2e8f0;text-decoration:none"><span style="display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:6px;background:#1877f2;color:#fff;font-size:.6rem;font-weight:800;flex-shrink:0">FB</span>Ver perfil →</a></span></div>` : ''}
  </div>
  ${hasBotData ? `<div class="cp-section">
    <div class="cp-section-title">Datos del bot <span style="font-size:.7rem;color:#475569;font-weight:400">(calificación WA)</span></div>
    ${ci.lead_name ? `<div class="cp-field"><span class="cp-field-label">Contacto</span><span class="cp-field-val">${ci.lead_name}</span></div>` : ''}
    ${ci.budget_range ? `<div class="cp-field"><span class="cp-field-label">Presupuesto</span><span class="cp-field-val">${ci.budget_range}</span></div>` : ''}
    ${ci.colors ? `<div class="cp-field"><span class="cp-field-label">Colores de marca</span><span class="cp-field-val">${ci.colors}</span></div>` : ''}
    ${ci.instagram ? `<div class="cp-field"><span class="cp-field-label">Instagram / web</span><span class="cp-field-val">${ci.instagram}</span></div>` : ''}
    ${ci.needs ? `<div class="cp-field"><span class="cp-field-label">Necesidades</span><span class="cp-field-val">${ci.needs}</span></div>` : ''}
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
    const dir = m.direction === 'outbound' ? 'out' : 'in';
    return `<div class="cp-wa-msg ${dir}">${m.content || m.body || ''}</div>`;
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
        <div class="cp-meeting-title">${m.title || 'Reunión'}</div>
        <button class="cp-btn cp-btn-ghost" style="color:#ef4444;font-size:.8rem;padding:2px 8px" onclick="_cpDeleteMeeting(${m.id})">Borrar</button>
      </div>
      <div class="cp-meeting-meta">${m.start_at ? new Date(m.start_at).toLocaleString('es-UY',{timeZone:'America/Montevideo',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'}) : ''} · ${_cpMeetStatus(m.status)}</div>
      ${m.meet_link ? `<div style="margin-bottom:8px"><a class="cp-meeting-link" href="${m.meet_link}" target="_blank" style="margin:0">▶ Unirse a la reunión</a></div>` : ''}
      ${m.calendar_event_id ? `<div style="margin-bottom:8px">
        <button class="cp-btn cp-btn-ghost" style="font-size:.75rem;padding:2px 8px" onclick="_cpToggleAddEmail(${m.id})">+ Agregar email</button>
        <div id="add-email-form-${m.id}" style="display:none;margin-top:6px;gap:6px;align-items:center;flex-wrap:wrap">
          <input type="email" id="add-email-input-${m.id}" placeholder="email@ejemplo.com" style="font-size:.8rem;padding:4px 8px;background:#0f172a;border:1px solid #1e293b;border-radius:6px;color:#f1f5f9;width:220px">
          <button class="cp-btn cp-btn-primary" style="font-size:.75rem;padding:4px 10px;margin-top:4px" onclick="_cpAddEmailToMeeting(${m.id},'${m.calendar_event_id}')">Agregar</button>
        </div>
      </div>` : ''}
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

function _cpMeetStatus(s) {
  const map = {scheduled:'agendada',completed:'realizada',cancelled:'cancelada'};
  return map[s] || s || '';
}

function _cpBindMeetings() {}

async function _cpDeleteMeeting(meetingId) {
  if (!confirm('¿Borrar esta reunión? También se cancela el evento en Google Calendar.')) return;
  const r = await fetch('/api/calendar/meetings/' + meetingId, { method: 'DELETE' });
  const data = await r.json();
  if (data.ok) {
    _cpData.meetings = (_cpData.meetings || []).filter(m => m.id !== meetingId);
    document.getElementById('cp-tab-meetings').innerHTML = _cpRenderMeetings();
  } else {
    alert('Error al borrar: ' + (data.error || 'desconocido'));
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

function _cpRenderBudget() {
  const items = (_cpData.attBudget || []).filter(a => a.mime_type === 'text/html');
  const hasBudget = items.length > 0;

  if (!hasBudget) {
    return `<div class="cp-section">
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
        <a class="cp-btn cp-btn-ghost" style="font-size:.75rem;padding:3px 10px;text-decoration:none" href="/api/attachments/${a.id}/pdf">⬇️ PDF</a>
        <button class="cp-btn cp-btn-ghost" style="font-size:.75rem;padding:3px 10px" onclick="_cpOpenAiEditModal(${a.id},'${esc(a.name)}')">✏️ Editar con IA</button>
        <button class="attach-del" title="Eliminar" onclick="_cpDeleteAttach(${a.id},'budget')">✕</button>
      </div>
    </div>`).join('');

  return `<div class="cp-section">
    <div class="cp-section-title">Presupuesto</div>
    ${listHtml}
  </div>
  ${_cpRenderAttachBox('budget')}`;
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


def create_app(db_path: str) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY") or "scalerics-dev-key-change-in-prod"
    app.config["DB_PATH"] = db_path
    app.config["PIPELINE_STATUS"] = _pipeline_status
    app.config["PIPELINE_LOCK"] = _pipeline_lock

    for bp in (leads_bp, demos_bp, calendar_bp, wa_bp, pipeline_bp, tasks_bp, budgets_bp, tokens_bp, meta_bp, calendly_bp):
        app.register_blueprint(bp)

    @app.before_request
    def require_login():
        if request.endpoint in ("login", "logout", "register", "forgot_password", "reset_password", "static", "privacidad"):
            return
        if request.path.startswith("/api/meta/webhook"):
            return
        if request.path.startswith("/api/calendly/webhook"):
            return
        # Any /api/ request with valid x-admin-token bypasses session auth
        if request.path.startswith("/api/"):
            token = request.headers.get("x-admin-token", "")
            expected = os.environ.get("ADMIN_TOKEN", "")
            if expected and token == expected:
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

    @app.route("/login", methods=["GET", "POST"])
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
    def register():
        from werkzeug.security import generate_password_hash
        from database import create_user, get_user_by_email
        error = None
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            phone = request.form.get("phone", "").strip()
            password = request.form.get("password", "")
            if not all([name, email, phone, password]):
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
        conn_a = _sqa.connect(db_path); conn_a.row_factory = _sqa.Row
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
        conn2 = _sq2.connect(db_path); conn2.row_factory = _sq2.Row
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
        conn3 = _sq3.connect(db_path); conn3.row_factory = _sq3.Row
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
            # SDR users only (role name = 'SDR')
            sdr_names = [r["name"] for r in conn3.execute("""
                SELECT u.name FROM users u
                JOIN roles r ON u.role_id = r.id
                WHERE r.name = 'SDR'
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
        conn5 = _sq5.connect(db_path); conn5.row_factory = _sq5.Row
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
        admin_email = os.environ.get("ADMIN_EMAIL", "")
        is_admin = bool(
            (admin_email and user["email"].lower() == admin_email.lower())
            or (not admin_email and user["id"] == 1)
        )
        # Role-based access: role takes priority over direct panel_access
        import sqlite3 as _sq2, json as _j2
        panel_access = None
        if not is_admin:
            role_id = user.get("role_id")
            if role_id:
                conn3 = _sq2.connect(db_path); conn3.row_factory = _sq2.Row
                try:
                    role = conn3.execute("SELECT name, panel_access FROM roles WHERE id=?", (role_id,)).fetchone()
                    if role:
                        if (role["name"] or "").lower() == "admin":
                            is_admin = True
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
            "is_admin": is_admin,
            "panel_access": panel_access,
            "role_id": user.get("role_id"),
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
        admin_email = os.environ.get("ADMIN_EMAIL", "")
        is_admin = bool((admin_email and current.get("email","").lower() == admin_email.lower()) or (not admin_email and current.get("id") == 1))
        if not is_admin:
            return jsonify({"ok": False, "error": "No autorizado"}), 403
        data = request.get_json() or {}
        panels = data.get("panels")  # None = all access, list = specific panels
        val = _json.dumps(panels) if panels is not None else None
        conn2 = __import__("sqlite3").connect(db_path)
        try:
            conn2.execute("UPDATE users SET panel_access=? WHERE id=?", (val, uid))
            conn2.commit()
        finally:
            conn2.close()
        return jsonify({"ok": True})

    @app.route("/api/admin/roles", methods=["GET"])
    def admin_list_roles():
        import sqlite3 as _sq
        conn2 = _sq.connect(db_path); conn2.row_factory = _sq.Row
        try:
            rows = conn2.execute("SELECT * FROM roles ORDER BY id").fetchall()
            return jsonify([dict(r) for r in rows])
        finally: conn2.close()

    @app.route("/api/admin/roles", methods=["POST"])
    def admin_create_role():
        import sqlite3 as _sq, json as _j
        data = request.get_json() or {}
        name = (data.get("name") or "").strip()
        panels = data.get("panels", [])
        if not name: return jsonify({"ok": False, "error": "Nombre requerido"}), 400
        conn2 = _sq.connect(db_path)
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
        data = request.get_json() or {}
        name = (data.get("name") or "").strip()
        panels = data.get("panels")
        conn2 = _sq.connect(db_path)
        try:
            if name: conn2.execute("UPDATE roles SET name=? WHERE id=?", (name, rid))
            if panels is not None: conn2.execute("UPDATE roles SET panel_access=? WHERE id=?", (_j.dumps(panels), rid))
            conn2.commit()
            return jsonify({"ok": True})
        finally: conn2.close()

    @app.route("/api/admin/roles/<int:rid>", methods=["DELETE"])
    def admin_delete_role(rid):
        import sqlite3 as _sq
        conn2 = _sq.connect(db_path)
        try:
            conn2.execute("UPDATE users SET role_id=NULL WHERE role_id=?", (rid,))
            conn2.execute("DELETE FROM roles WHERE id=?", (rid,))
            conn2.commit()
            return jsonify({"ok": True})
        finally: conn2.close()

    @app.route("/api/admin/users/<int:uid>/role", methods=["PUT"])
    def admin_set_user_role(uid):
        import sqlite3 as _sq
        data = request.get_json() or {}
        role_id = data.get("role_id")  # None = no role (full access for admins)
        conn2 = _sq.connect(db_path)
        try:
            conn2.execute("UPDATE users SET role_id=? WHERE id=?", (role_id, uid))
            conn2.commit()
            return jsonify({"ok": True})
        finally: conn2.close()

    @app.route("/api/admin/users-data", methods=["GET"])
    def admin_users_data():
        admin_email = os.environ.get("ADMIN_EMAIL", "")
        current_user_id = session.get("user_id")
        from database import get_user_by_id, get_all_users
        current = get_user_by_id(db_path, current_user_id) if current_user_id else None
        if not current:
            return jsonify({"error": "No autorizado"}), 403
        if admin_email and current["email"].lower() != admin_email.lower():
            return jsonify({"error": "No autorizado"}), 403
        if not admin_email and current["id"] != 1:
            return jsonify({"error": "No autorizado"}), 403
        users = get_all_users(db_path)
        return jsonify([dict(u) for u in users])

    @app.route("/api/admin/users", methods=["GET"])
    def admin_list_users():
        admin_email = os.environ.get("ADMIN_EMAIL", "")
        current_user_id = session.get("user_id")
        from database import get_user_by_id, get_all_users
        current = get_user_by_id(db_path, current_user_id) if current_user_id else None
        if not current:
            return jsonify({"error": "No autorizado"}), 403
        if admin_email and current["email"].lower() != admin_email.lower():
            return jsonify({"error": "No autorizado"}), 403
        if not admin_email and current["id"] != 1:
            return jsonify({"error": "No autorizado"}), 403
        return jsonify(get_all_users(db_path))

    @app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
    def admin_delete_user(user_id):
        admin_email = os.environ.get("ADMIN_EMAIL", "")
        current_user_id = session.get("user_id")
        from database import get_user_by_id, delete_user
        current = get_user_by_id(db_path, current_user_id) if current_user_id else None
        if not current:
            return jsonify({"error": "No autorizado"}), 403
        if admin_email and current["email"].lower() != admin_email.lower():
            return jsonify({"error": "No autorizado"}), 403
        if not admin_email and current["id"] != 1:
            return jsonify({"error": "No autorizado"}), 403
        if user_id == current_user_id:
            return jsonify({"error": "No podés eliminar tu propia cuenta"}), 400
        delete_user(db_path, user_id)
        return jsonify({"ok": True})

    @app.route("/api/admin/users/<int:user_id>/reset-password", methods=["POST"])
    def admin_reset_user_password(user_id):
        import secrets as _secrets
        admin_email = os.environ.get("ADMIN_EMAIL", "")
        current_user_id = session.get("user_id")
        from database import get_user_by_id, create_reset_token
        from services.email_service import send_reset_email
        current = get_user_by_id(db_path, current_user_id) if current_user_id else None
        if not current:
            return jsonify({"error": "No autorizado"}), 403
        if admin_email and current["email"].lower() != admin_email.lower():
            return jsonify({"error": "No autorizado"}), 403
        if not admin_email and current["id"] != 1:
            return jsonify({"error": "No autorizado"}), 403
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
        admin_email = os.environ.get("ADMIN_EMAIL", "")
        user_id = session.get("user_id")
        from database import get_user_by_id, get_all_users, delete_user
        current = get_user_by_id(db_path, user_id) if user_id else None
        if not current:
            return redirect(url_for("login"))
        is_admin = (admin_email and current["email"].lower() == admin_email.lower()) or (not admin_email and current["id"] == 1)
        if not is_admin:
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
const ALL_PANELS = ['cola','seguimientos','meta','pipeline','clientes','tasks','wa','cal','metrics','activity'];
const PANEL_LABELS = {cola:'Cola',seguimientos:'Seguimientos',meta:'Meta Ads',pipeline:'Pipeline',clientes:'Clientes',tasks:'Tareas',wa:'WhatsApp',cal:'Calendario',metrics:'Métricas',activity:'Actividad'};
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
      _roles.map(r => `<option value="${r.id}" ${u.role_id==r.id?'selected':''}>${r.name}</option>`).join('');
    const roleName = u.role_name || 'Sin rol';
    return `<div class="card">
      <div class="row">
        <div style="flex:1">
          <div class="name">${u.name}</div>
          <div class="sub">${u.email} · ${u.phone}</div>
        </div>
        <span class="badge ${u.role_id?'has-role':''}">${roleName}</span>
        <select onchange="setRole(${u.id}, this.value)">${opts}</select>
        <form method="POST" action="/admin/users/${u.id}/delete" onsubmit="return confirm('Eliminar a ${u.name}?')" style="display:inline">
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
        return render_template_string(ADMIN_PAGE, users=users)

    @app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
    def admin_delete_user_page(user_id):
        admin_email = os.environ.get("ADMIN_EMAIL", "")
        current_uid = session.get("user_id")
        from database import get_user_by_id, delete_user
        current = get_user_by_id(db_path, current_uid) if current_uid else None
        if not current:
            return redirect(url_for("login"))
        is_admin = (admin_email and current["email"].lower() == admin_email.lower()) or (not admin_email and current["id"] == 1)
        if not is_admin or user_id == current_uid:
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

    worker = init_worker(db_path)
    worker.register("demo", demo_job_handler)
    worker.start()

    start_meta_token_monitor(app)
    start_meta_daily_import(app)

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
