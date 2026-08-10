"""
Run this script ONCE (or again when scopes change) to obtain the OAuth2 refresh token.
It opens a browser window for authentication and saves credentials to .env.

Steps:
1. Google Cloud Console → proyecto scrao-495517
2. APIs & Services → Library → habilitar:
   - "Google Calendar API"
   - "Google Drive API"
3. Run: python setup_calendar.py
"""
import json
import os
import subprocess
from datetime import datetime, timezone
from google_auth_oauthlib.flow import InstalledAppFlow
from dotenv import load_dotenv, set_key

# App de Fly.io donde corre el CRM (ver fly.toml).
FLY_APP = os.environ.get("FLY_APP", "scalerics-crm")

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive.readonly",
]
CREDENTIALS_FILE = "credentials.json"

def main():
    load_dotenv()
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(CREDENTIALS_FILE) as f:
        client_config = json.load(f)["installed"]

    renewed_at = datetime.now(timezone.utc).isoformat()

    set_key(".env", "GCAL_CLIENT_ID", client_config["client_id"])
    set_key(".env", "GCAL_CLIENT_SECRET", client_config["client_secret"])
    set_key(".env", "GCAL_REFRESH_TOKEN", creds.refresh_token)
    set_key(".env", "GCAL_TOKEN_RENEWED_AT", renewed_at)

    print("Credenciales de Calendar guardadas en .env")
    print(f"  CLIENT_ID:     {client_config['client_id'][:30]}...")
    print(f"  REFRESH_TOKEN: {creds.refresh_token[:30]}...")
    print(f"  RENEWED_AT:    {renewed_at}")

    print("Sincronizando con Fly.io...")
    try:
        subprocess.run(
            ["fly", "secrets", "set",
             f"GCAL_CLIENT_ID={client_config['client_id']}",
             f"GCAL_CLIENT_SECRET={client_config['client_secret']}",
             f"GCAL_REFRESH_TOKEN={creds.refresh_token}",
             f"GCAL_TOKEN_RENEWED_AT={renewed_at}",
             "--app", FLY_APP],
            check=True, capture_output=True, text=True
        )
        print("  Fly.io actualizado. El servidor va a reiniciar solo.")
    except Exception as e:
        print(f"  Fly.io no actualizado (hacelo manual): {e}")

if __name__ == "__main__":
    main()
