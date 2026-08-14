import html
import logging
import os

import requests

logger = logging.getLogger(__name__)

_LOGO = "https://raw.githubusercontent.com/juantomasetti1/scalerics-assets/main/logo_full_alt.png"
_CRM_URL = os.environ.get("CRM_URL", "https://scalerics-crm.fly.dev")


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


def send_new_meta_lead_notification(to_email: str, lead_name: str, phone: str, campaign: str, city: str, lead_id: int) -> bool:
    # Todo esto sale del formulario de Meta, que llena cualquiera en internet:
    # sin escapar, un `<a href="https://phishing/">` en el campo nombre le
    # inyecta un link al mail que reciben los admins.
    nombre_esc   = html.escape(lead_name or "")
    telefono_esc = html.escape(phone or "")
    ciudad_esc   = html.escape(city or "")
    campana_esc  = html.escape(campaign or "")

    rows = [("Nombre", nombre_esc)]
    if telefono_esc:
        rows.append(("Teléfono", telefono_esc))
    if ciudad_esc:
        rows.append(("Ciudad", ciudad_esc))
    if campana_esc:
        rows.append(("Campaña", campana_esc))
    body = (
        _muted("Llegó un nuevo lead de Meta Ads al CRM.")
        + _info_card(rows)
    )
    cuerpo_html = _layout(
        badge="Nuevo lead Meta Ads",
        title=f"Nuevo lead: {nombre_esc}",
        body=body,
        cta_url=f"{_CRM_URL}/?highlight={html.escape(str(lead_id))}",
        cta_label="Ver en CRM →",
    )
    # El asunto es texto plano, no HTML: va el nombre tal cual, sin saltos de línea.
    asunto = f"Nuevo lead Meta: {(lead_name or '').replace(chr(10), ' ').replace(chr(13), ' ')} — Scalerics CRM"
    return _send(to_email, asunto, cuerpo_html)


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


def send_meeting_reminder(to_email: str, nombre_cliente: str, titulo: str,
                          cuando: str, meet_link: str = "",
                          horas_antes: int = 24) -> bool:
    """Recordatorio de reunion al cliente.

    El CRM no enviaba ningun aviso previo: los no-shows salian caros y no habia
    forma de reducirlos. El link de Meet va adentro para que no tengan que buscarlo.
    """
    from markupsafe import escape

    saludo = f"Hola{' ' + str(escape(nombre_cliente)) if nombre_cliente else ''},"
    cuerpo_texto = (
        "te recordamos que mañana tenemos nuestra reunión."
        if horas_antes >= 12 else
        "te recordamos que en un rato tenemos nuestra reunión."
    )
    filas = [("Reunión", str(escape(titulo))), ("Cuándo", str(escape(cuando)))]
    body = (
        _muted(f"{saludo} {cuerpo_texto}")
        + _info_card(filas)
        + (_muted("Si no podés en ese horario, respondé este mail y lo reprogramamos.")
           if horas_antes >= 12 else
           _muted("Te esperamos."))
    )
    html = _layout(
        badge="Recordatorio",
        title="Tu reunión con Scalerics",
        body=body,
        cta_url=meet_link or "",
        cta_label="Entrar a la reunión →" if meet_link else "",
    )
    asunto = ("Recordatorio: reunión mañana con Scalerics" if horas_antes >= 12
              else "Tu reunión con Scalerics es en 1 hora")
    return _send(to_email, asunto, html)


def send_meta_token_alert(to_email: str, error_detail: str) -> bool:
    body = (
        _muted(
            "El token de Meta (PAGE_TOKEN) ya no es válido. "
            "Los leads nuevos de Meta Ads <b>no se están guardando en el CRM</b> hasta que se renueve."
        )
        + _info_card([("Error", error_detail)])
        + _muted(
            "Para renovarlo: entrá al Graph Explorer de Meta, generá un nuevo User Token "
            "con permisos <code>leads_retrieval</code> y <code>pages_read_engagement</code>, "
            "canjealo por un Page Token de larga duración, y cargalo en Fly:<br>"
            "<code>flyctl secrets set META_PAGE_TOKEN=&lt;nuevo&gt; -a scalerics-crm</code><br>"
            "Ese comando reinicia la app con el token nuevo. No alcanza con escribir un "
            "<code>.env</code>: el filesystem del contenedor es efímero fuera de <code>/data</code>."
        )
    )
    html = _layout(
        badge="Alerta de integración",
        title="Token de Meta Ads vencido o revocado",
        body=body,
        cta_url=f"{_CRM_URL}",
        cta_label="Ir al CRM →",
    )
    return _send(to_email, "ALERTA: Token Meta Ads inválido — Scalerics CRM", html)


def send_meta_lead_failure_alert(email: str, lead_id: str, error: str) -> None:
    """Avisa que un lead de Meta llegó pero no se pudo guardar."""
    # `lead_id` viene del payload del webhook y `error` puede arrastrar texto
    # de la respuesta de Graph: ninguno de los dos es HTML de confianza.
    lead_id_esc = html.escape(str(lead_id))
    error_esc   = html.escape(str(error))
    asunto = f"[CRM] No se pudo guardar un lead de Meta ({lead_id})"
    cuerpo = (
        f"<p>Llegó un lead de Meta y el CRM no lo pudo guardar.</p>"
        f"<p><b>leadgen_id:</b> {lead_id_esc}</p>"
        f"<p><b>Error:</b> {error_esc}</p>"
        f"<p>Se puede recuperar a mano desde el panel de formularios de Meta "
        f"buscando ese id.</p>"
    )
    _send(email, asunto, cuerpo)
