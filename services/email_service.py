import logging
import os

logger = logging.getLogger(__name__)


def send_reset_email(to_email: str, reset_url: str) -> bool:
    """Send password reset email. Currently stubs to console log.
    Replace body with Resend API call when RESEND_API_KEY is configured.
    """
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        logger.info(f"[EMAIL STUB] Reset link para {to_email}: {reset_url}")
        return True

    try:
        import requests
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": f"Scalerics CRM <{os.environ.get('RESEND_FROM_EMAIL', 'crm@noreply.scalerics.com')}>",
                "to": [to_email],
                "subject": "Resetear contraseña — Scalerics CRM",
                "html": f"""
                <p>Hola,</p>
                <p>Hacé clic en el link para resetear tu contraseña. Expira en 1 hora.</p>
                <p><a href="{reset_url}">{reset_url}</a></p>
                <p>Si no lo pediste vos, ignorá este mail.</p>
                """,
            },
            timeout=10,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send reset email to {to_email}: {e}")
        return False


def send_new_user_notification(new_name: str, new_email: str, new_phone: str, admin_email: str) -> bool:
    """Notify admin when a new user registers."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    html = f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:'Segoe UI',Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:32px 0">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:10px;overflow:hidden;box-shadow:0 2px 16px rgba(15,31,61,.10)">

        <!-- Header -->
        <tr>
          <td style="background:#0f1f3d;padding:28px 40px">
            <img src="https://raw.githubusercontent.com/juantomasetti1/scalerics-assets/main/logo_full_alt.png"
                 alt="Scalerics" height="32" style="display:block">
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:36px 40px 24px">
            <p style="margin:0 0 6px;font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#0088cc">Nuevo usuario registrado</p>
            <h1 style="margin:0 0 24px;font-size:22px;font-weight:700;color:#0f1f3d;line-height:1.3">
              {new_name} se registró en el CRM
            </h1>

            <!-- User card -->
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;margin-bottom:24px">
              <tr>
                <td style="padding:20px 24px">
                  <table cellpadding="0" cellspacing="0">
                    <tr>
                      <td style="padding:5px 16px 5px 0;font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.06em;white-space:nowrap">Nombre</td>
                      <td style="padding:5px 0;font-size:14px;color:#1c2b40;font-weight:500">{new_name}</td>
                    </tr>
                    <tr>
                      <td style="padding:5px 16px 5px 0;font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.06em;white-space:nowrap">Email</td>
                      <td style="padding:5px 0;font-size:14px;color:#1c2b40;font-weight:500">{new_email}</td>
                    </tr>
                    <tr>
                      <td style="padding:5px 16px 5px 0;font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.06em;white-space:nowrap">Teléfono</td>
                      <td style="padding:5px 0;font-size:14px;color:#1c2b40;font-weight:500">{new_phone or '—'}</td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>

            <p style="margin:0 0 24px;font-size:13.5px;color:#5a6a82;line-height:1.6">
              El nuevo usuario ya tiene acceso al panel. Podés asignarle un rol desde la sección de administración.
            </p>

            <a href="{os.environ.get('CRM_URL', 'https://web-production-cb6e.up.railway.app')}/admin/users"
               style="display:inline-block;background:#0088cc;color:#ffffff;font-size:14px;font-weight:600;
                      padding:12px 28px;border-radius:6px;text-decoration:none">
              Ver usuarios →
            </a>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#f8fafc;border-top:1px solid #e2e8f0;padding:16px 40px">
            <p style="margin:0;font-size:11.5px;color:#94a3b8">
              Scalerics CRM · Notificación automática · No responder este mail
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""

    if not api_key:
        logger.info(f"[EMAIL STUB] Nuevo usuario: {new_name} <{new_email}>")
        return True
    try:
        import requests
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": os.environ.get("RESEND_FROM_EMAIL", "Scalerics CRM <crm@noreply.scalerics.com>"),
                "to": [admin_email],
                "subject": f"Nuevo usuario: {new_name} — Scalerics CRM",
                "html": html,
            },
            timeout=10,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send new user notification: {e}")
        return False


_GOAL_TYPE_LABELS = {
    "reuniones_agendadas": "reuniones agendadas",
    "leads_contactados": "leads contactados",
}


def send_task_assignment_email(
    to_email: str,
    assignee_name: str,
    task_title: str,
    task_description: str,
    goal: int | None,
    goal_type: str | None,
    deadline: str | None,
    assigned_by: str,
    crm_url: str = "",
) -> bool:
    api_key = os.environ.get("RESEND_API_KEY", "")
    goal_line = ""
    if goal and goal_type:
        label = _GOAL_TYPE_LABELS.get(goal_type, goal_type)
        goal_line = f"<p><b>Meta:</b> {goal} {label}</p>"
    deadline_line = f"<p><b>Vencimiento:</b> {deadline}</p>" if deadline else ""
    desc_line = f"<p><b>Descripción:</b> {task_description}</p>" if task_description else ""
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto">
      <h2 style="color:#0088cc">Nueva tarea asignada</h2>
      <p>Hola {assignee_name}, <b>{assigned_by}</b> te asignó una tarea en el CRM de Scalerics.</p>
      <div style="background:#f8fafc;border-left:4px solid #0088cc;padding:16px 20px;border-radius:4px;margin:16px 0">
        <h3 style="margin:0 0 8px">{task_title}</h3>
        {desc_line}
        {goal_line}
        {deadline_line}
      </div>
      {f'<p><a href="{crm_url}" style="background:#0088cc;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none">Abrir CRM</a></p>' if crm_url else ''}
    </div>
    """
    if not api_key:
        logger.info(f"[EMAIL STUB] Tarea '{task_title}' asignada a {to_email} por {assigned_by}")
        return True
    try:
        import requests
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": f"Scalerics CRM <{os.environ.get('RESEND_FROM_EMAIL', 'crm@noreply.scalerics.com')}>",
                "to": [to_email],
                "subject": f"Nueva tarea: {task_title} — Scalerics CRM",
                "html": html,
            },
            timeout=10,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send task assignment email to {to_email}: {e}", exc_info=True)
        return False
