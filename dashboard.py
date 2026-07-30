import os
import secrets
import threading
import webbrowser

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, render_template_string, request, session, url_for

from database import init_db, seed_pitch_templates
from routes.leads import leads_bp
from routes.demos import demos_bp
from routes.calendar import calendar_bp
from routes.wa import wa_bp
from routes.pipeline import pipeline_bp
from routes.tasks import tasks_bp
from routes.budgets import budgets_bp
from routes.tokens import tokens_bp
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


def create_app(db_path: str) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY") or "scalerics-dev-key-change-in-prod"
    app.config["DB_PATH"] = db_path
    app.config["PIPELINE_STATUS"] = _pipeline_status
    app.config["PIPELINE_LOCK"] = _pipeline_lock

    for bp in (leads_bp, demos_bp, calendar_bp, wa_bp, pipeline_bp, tasks_bp, budgets_bp, tokens_bp, calendly_bp):
        app.register_blueprint(bp)

    @app.before_request
    def require_login():
        if request.endpoint in ("login", "logout", "register", "forgot_password", "reset_password", "static", "privacidad"):
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
  <p>Scalerics es una agencia de software y marketing digital. Esta política describe cómo tratamos los datos personales que recopilamos a través de nuestros formularios de captación y herramientas internas.</p>

  <h2>2. Datos que recopilamos</h2>
  <p>A través de nuestros formularios de captación en redes sociales podemos recopilar:</p>
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
                "SELECT DISTINCT user_name FROM activity_log WHERE user_name NOT IN ('','sistema','sistema-auto','calendly','calendly-import') ORDER BY user_name"
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
const ALL_PANELS = ['cola','seguimientos','pipeline','clientes','tasks','wa','cal','metrics','activity','sdr'];
const PANEL_LABELS = {cola:'Cola',seguimientos:'Seguimientos',pipeline:'Pipeline',clientes:'Clientes',tasks:'Tareas',wa:'WhatsApp',cal:'Calendario',metrics:'Métricas',activity:'Actividad'};
let _roles = [];

function makeChips(containerId, checkedArr, prefix) {
  const el = document.getElementById(containerId);
  el.innerHTML = ALL_PANELS.map(p => {
    const on = checkedArr ? checkedArr.includes(p) : true;
    return `<label class="chip ${on?'on':''}" id="${prefix}-chip-${p}">
      <input type="checkbox" id="${prefix}-cb-${p}" ${on?'checked':''} onchange="toggleChip('${prefix}','${p}',this.checked)">
      ${PANEL_LABELS[p]}
    </label>`;
  }).join('');
}
function toggleChip(prefix, p, on) {
  const chip = document.getElementById(`${prefix}-chip-${p}`);
  chip.classList.toggle('on', on);
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
        resp = make_response(render_template("crm/index.html"))
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        return resp

    worker = init_worker(db_path)
    worker.register("demo", demo_job_handler)
    worker.start()


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
