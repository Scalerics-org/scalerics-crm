import base64
import datetime
import html
import logging
import os
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from database import get_businesses_by_status, update_business

load_dotenv()
logger = logging.getLogger(__name__)

def get_gmail_service(client_id: str, client_secret: str, refresh_token: str):
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/gmail.send"],
    )
    return build("gmail", "v1", credentials=creds)

def build_subject(business_name: str) -> str:
    return f"Creamos una demo gratuita para {business_name}"

def build_email_html(business: dict, factory: dict) -> str:
    name = html.escape(business.get("name", "") or "")
    demo_url = business.get("demo_url", "") or ""
    factory_name = html.escape(factory.get("name", "") or "")
    factory_email = factory.get("email", "") or ""
    factory_phone = factory.get("phone", "") or ""

    phone_line = f"📞 {factory_phone}" if factory_phone else ""

    return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#333;padding:20px">
  <div style="border-top:4px solid #7c3aed;padding-top:24px">
    <h2 style="color:#7c3aed;margin-bottom:4px">{factory_name}</h2>
    <p style="color:#666;margin-top:0;font-size:0.9em">Diseño web para pymes uruguayas</p>
  </div>
  <p style="margin-top:24px">Hola equipo de <strong>{name}</strong>,</p>
  <p>Notamos que todavía no tienen sitio web propio. Hoy en día, la mayoría de los clientes busca en Google antes de visitar un negocio — y sin web, no aparecen.</p>
  <p>Por eso <strong>creamos una demo gratuita</strong> para que vean cómo podría quedar la web de {name}:</p>
  <div style="text-align:center;margin:32px 0">
    <a href="{demo_url}"
       style="background:#7c3aed;color:white;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:1rem;display:inline-block">
      👉 Ver mi demo gratuita
    </a>
    <p style="margin-top:12px;font-size:0.85em;color:#666">{demo_url}</p>
  </div>
  <p>Si les interesa tener su propia web profesional, respondé este mail o escribinos. Los precios son accesibles y el proceso es simple — nos encargamos de todo.</p>
  <div style="margin-top:32px;padding:16px;background:#f5f3ff;border-radius:8px;font-size:0.9em">
    <strong>{factory_name}</strong><br>
    📧 <a href="mailto:{factory_email}" style="color:#7c3aed">{factory_email}</a><br>
    {phone_line}
  </div>
  <p style="font-size:0.75em;color:#999;margin-top:24px">
    Este mail fue enviado porque {name} no tiene sitio web y creemos que podemos ayudar.
    Si no querés recibir más mensajes, respondé con "no gracias".
  </p>
</body>
</html>"""

def send_email(service, sender: str, to: str, subject: str, html_body: str) -> dict:
    message = MIMEMultipart("alternative")
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message.attach(MIMEText(html_body, "html", "utf-8"))
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return service.users().messages().send(userId="me", body={"raw": raw}).execute()

def run(db_path: str, factory: dict, delay_between_emails: int = 15) -> None:
    client_id = os.environ["GMAIL_CLIENT_ID"]
    client_secret = os.environ["GMAIL_CLIENT_SECRET"]
    refresh_token = os.environ["GMAIL_REFRESH_TOKEN"]
    sender_email = factory["email"]

    service = get_gmail_service(client_id, client_secret, refresh_token)
    businesses = get_businesses_by_status(db_path, "deployed")
    logger.info(f"Enviando mails a {len(businesses)} negocios")

    for biz in businesses:
        if not biz.get("email"):
            logger.warning(f"Sin email: {biz['name']}, saltando")
            update_business(db_path, biz["id"], status="no_email")
            continue
        try:
            subject = build_subject(biz["name"])
            html_body = build_email_html(biz, factory)
            send_email(service, sender_email, biz["email"], subject, html_body)
            update_business(
                db_path, biz["id"],
                status="email_sent",
                email_sent_at=datetime.datetime.now().isoformat(),
            )
            logger.info(f"Mail enviado a {biz['name']} <{biz['email']}>")
            time.sleep(delay_between_emails)
        except HttpError as e:
            if e.status_code == 429:
                logger.error("Cuota de Gmail excedida. Esperando 60 segundos...")
                update_business(db_path, biz["id"], status="error", error_message="Gmail quota exceeded")
                time.sleep(60)
            else:
                logger.error(f"Error Gmail para {biz['name']}: {e}")
                update_business(db_path, biz["id"], status="error", error_message=str(e))
        except Exception as e:
            logger.error(f"Error enviando a {biz['name']}: {e}")
            update_business(db_path, biz["id"], status="error", error_message=str(e))
