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
