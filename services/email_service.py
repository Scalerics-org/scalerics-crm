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
                "from": os.environ.get("FACTORY_EMAIL", "noreply@scalerics.com"),
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
