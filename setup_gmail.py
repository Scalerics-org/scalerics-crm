"""
Obtiene el refresh token de Gmail para LEER los mails de confirmación de
Calendly y cargar los leads al CRM.

Calendly Free no tiene webhooks, así que los datos del formulario de reserva
llegan por dos vías: el evento de Google Calendar (empresa, servicio,
teléfono) y el mail de confirmación (que además trae el mail del invitado).

Correr UNA vez, o de nuevo si cambian los scopes:

1. Google Cloud Console → proyecto scalerics-crm
2. APIs & Services → Library → habilitar "Gmail API"
3. Tener credentials.json en esta carpeta (es el mismo OAuth client que usa
   Calendar: 176581510793-...)
4. python setup_gmail.py
5. Elegir la cuenta scalerics@gmail.com, que es la que recibe las
   confirmaciones de Calendly

Ojo: gmail.readonly da lectura de toda la casilla. Google no ofrece un scope
más acotado por remitente. El CRM sólo consulta los mails de Calendly, pero el
permiso que se concede es el amplio.
"""
import json
import os
import subprocess
from datetime import datetime, timezone

from dotenv import load_dotenv, set_key
from google_auth_oauthlib.flow import InstalledAppFlow

# readonly, no send: este script existe para LEER los mails de confirmacion de
# Calendly (ver el docstring). Nadie usa gmail.send.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_FILE = "credentials.json"
# App de Fly.io donde corre el CRM (ver fly.toml).
FLY_APP = os.environ.get("FLY_APP", "scalerics-crm")

def main():
    load_dotenv()
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(CREDENTIALS_FILE) as f:
        client_config = json.load(f)["installed"]

    renewed_at = datetime.now(timezone.utc).isoformat()

    set_key(".env", "GMAIL_CLIENT_ID", client_config["client_id"])
    set_key(".env", "GMAIL_CLIENT_SECRET", client_config["client_secret"])
    set_key(".env", "GMAIL_REFRESH_TOKEN", creds.refresh_token)
    set_key(".env", "GMAIL_TOKEN_RENEWED_AT", renewed_at)

    print("Credenciales de Gmail guardadas en .env")
    print(f"  CLIENT_ID:     {client_config['client_id'][:30]}...")
    print(f"  REFRESH_TOKEN: {creds.refresh_token[:30]}...")
    print(f"  RENEWED_AT:    {renewed_at}")

    print(f"Sincronizando con Fly ({FLY_APP})...")
    try:
        subprocess.run(
            ["fly", "secrets", "set",
             f"GMAIL_CLIENT_ID={client_config['client_id']}",
             f"GMAIL_CLIENT_SECRET={client_config['client_secret']}",
             f"GMAIL_REFRESH_TOKEN={creds.refresh_token}",
             f"GMAIL_TOKEN_RENEWED_AT={renewed_at}",
             "--app", FLY_APP],
            check=True, capture_output=True, text=True,
        )
        print("  Fly actualizado. El servidor reinicia solo.")
    except Exception as e:
        print(f"  Fly NO actualizado, hacelo a mano: {e}")

if __name__ == "__main__":
    main()
