import logging
import os

import requests

logger = logging.getLogger(__name__)

_LOGO = "https://raw.githubusercontent.com/juantomasetti1/scalerics-assets/main/logo_full_alt.png"
_CRM_URL = os.environ.get("CRM_URL", "https://web-production-cb6e.up.railway.app")


# ── Shared layout ──────────────────────────────────────────────────────────────

def _layout(badge: str, title: str, body: str, cta_url: str = "", cta_label: str = "") -> str:
    cta = (
        f'<a href="{cta_url}" style="display:inline-block;background:#0088cc;color:#ffffff;'
        f'font-size:14px;font-weight:600;padding:12px 28px;border-radius:6px;text-decoration:none">'
        f'{cta_label}</a>'
    ) if cta_url else ""
    return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:'Segoe UI',Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:32px 0">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border-radius:10px;overflow:hidden;box-shadow:0 2px 16px rgba(15,31,61,.10)">

        <!-- Header -->
        <tr>
          <td style="background:#0f1f3d;padding:28px 40px">
            <img src="{_LOGO}" alt="Scalerics" height="32" style="display:block">
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:36px 40px 32px">
            <p style="margin:0 0 6px;font-size:12px;font-weight:700;letter-spacing:.08em;
                      text-transform:uppercase;color:#0088cc">{badge}</p>
            <h1 style="margin:0 0 24px;font-size:22px;font-weight:700;color:#0f1f3d;line-height:1.3">
              {title}
            </h1>
            {body}
            {f'<div style="margin-top:24px">{cta}</div>' if cta else ""}
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#f8fafc;border-top:1px solid #e2e8f0;padding:16px 40px">
            <p style="margin:0;font-size:11.5px;color:#94a3b8">
              Scalerics CRM &middot; Notificación automática &middot; No responder este mail
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _info_card(rows: list[tuple[str, str]]) -> str:
    """Renders a key-value card. rows = [(label, value), ...]"""
    trs = "".join(
        f'<tr>'
        f'<td style="padding:5px 16px 5px 0;font-size:11px;font-weight:700;color:#94a3b8;'
        f'text-transform:uppercase;letter-spacing:.06em;white-space:nowrap">{label}</td>'
        f'<td style="padding:5px 0;font-size:14px;color:#1c2b40;font-weight:500">{value or "—"}</td>'
        f'</tr>'
        for label, value in rows
    )
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;margin-bottom:24px">'
        f'<tr><td style="padding:20px 24px"><table cellpadding="0" cellspacing="0">{trs}</table></td></tr>'
        f'</table>'
    )


def _muted(text: str) -> str:
    return f'<p style="margin:0 0 20px;font-size:13.5px;color:#5a6a82;line-height:1.6">{text}</p>'


# ── Resend sender ───────────────────────────────────────────────────────────────

def _send(to: str, subject: str, html: str) -> bool:
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        logger.info(f"[EMAIL STUB] {subject} → {to}")
        return True
    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": os.environ.get("RESEND_FROM_EMAIL", "Scalerics CRM <crm@noreply.scalerics.com>"),
                "to": [to],
                "subject": subject,
                "html": html,
            },
            timeout=10,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send '{subject}' to {to}: {e}")
        return False


# ── Public functions ────────────────────────────────────────────────────────────

def send_reset_email(to_email: str, reset_url: str) -> bool:
    body = (
        _muted("Recibimos una solicitud para resetear la contraseña de tu cuenta en Scalerics CRM. "
               "El link expira en 1 hora.")
        + _info_card([("Link de reseteo", f'<a href="{reset_url}" style="color:#0088cc">{reset_url}</a>')])
        + _muted("Si no lo pediste vos, podés ignorar este mail.")
    )
    html = _layout(
        badge="Seguridad de cuenta",
        title="Resetear contraseña",
        body=body,
        cta_url=reset_url,
        cta_label="Resetear contraseña →",
    )
    return _send(to_email, "Resetear contraseña — Scalerics CRM", html)


def send_new_user_notification(new_name: str, new_email: str, new_phone: str, admin_email: str) -> bool:
    body = (
        _info_card([
            ("Nombre", new_name),
            ("Email", new_email),
            ("Teléfono", new_phone),
        ])
        + _muted("El nuevo usuario ya tiene acceso al panel. "
                 "Podés asignarle un rol desde la sección de administración.")
    )
    html = _layout(
        badge="Nuevo usuario registrado",
        title=f"{new_name} se registró en el CRM",
        body=body,
        cta_url=f"{_CRM_URL}/admin/users",
        cta_label="Ver usuarios →",
    )
    return _send(admin_email, f"Nuevo usuario: {new_name} — Scalerics CRM", html)


_GOAL_TYPE_LABELS = {
    "reuniones_agendadas": "reuniones agendadas",
    "leads_contactados": "leads contactados",
    "reuniones_hechas": "reuniones hechas",
    "clientes_cerrados": "clientes cerrados",
    "llamadas_realizadas": "llamadas realizadas",
    "llamadas_contestadas": "llamadas contestadas",
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
    rows = []
    if task_description:
        rows.append(("Descripción", task_description))
    if goal and goal_type:
        label = _GOAL_TYPE_LABELS.get(goal_type, goal_type)
        rows.append(("Meta", f"{goal} {label}"))
    if deadline:
        rows.append(("Vencimiento", deadline))
    rows.append(("Asignada por", assigned_by))

    body = (
        _muted(f"Hola {assignee_name}, <b>{assigned_by}</b> te asignó una nueva tarea en Scalerics CRM.")
        + _info_card(rows)
    )
    html = _layout(
        badge="Nueva tarea asignada",
        title=task_title,
        body=body,
        cta_url=crm_url or _CRM_URL,
        cta_label="Abrir CRM →",
    )
    return _send(to_email, f"Nueva tarea: {task_title} — Scalerics CRM", html)
