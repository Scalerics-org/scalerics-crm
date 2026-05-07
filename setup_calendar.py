"""
Run this script ONCE to obtain the Google Calendar OAuth2 refresh token.
It opens a browser window for authentication and saves credentials to .env.

Steps:
1. Google Cloud Console → proyecto scrao-495517
2. APIs & Services → Library → buscar "Google Calendar API" → Enable
3. Run: python setup_calendar.py
"""
import json
from google_auth_oauthlib.flow import InstalledAppFlow
from dotenv import load_dotenv, set_key

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CREDENTIALS_FILE = "credentials.json"

def main():
    load_dotenv()
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(CREDENTIALS_FILE) as f:
        client_config = json.load(f)["installed"]

    set_key(".env", "GCAL_CLIENT_ID", client_config["client_id"])
    set_key(".env", "GCAL_CLIENT_SECRET", client_config["client_secret"])
    set_key(".env", "GCAL_REFRESH_TOKEN", creds.refresh_token)

    print("Credenciales de Calendar guardadas en .env")
    print(f"  CLIENT_ID:     {client_config['client_id'][:30]}...")
    print(f"  REFRESH_TOKEN: {creds.refresh_token[:30]}...")

if __name__ == "__main__":
    main()
