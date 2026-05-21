"""
Run this script ONCE to obtain the Gmail OAuth2 refresh token.
It opens a browser window for authentication and saves credentials to .env.
"""
import json
import subprocess
from datetime import datetime, timezone
from google_auth_oauthlib.flow import InstalledAppFlow
from dotenv import load_dotenv, set_key

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

def main():
    load_dotenv()
    flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
    creds = flow.run_local_server(port=0)

    with open("credentials.json") as f:
        client_config = json.load(f)["installed"]

    renewed_at = datetime.now(timezone.utc).isoformat()

    set_key(".env", "GMAIL_CLIENT_ID", client_config["client_id"])
    set_key(".env", "GMAIL_CLIENT_SECRET", client_config["client_secret"])
    set_key(".env", "GMAIL_REFRESH_TOKEN", creds.refresh_token)
    set_key(".env", "GMAIL_TOKEN_RENEWED_AT", renewed_at)

    print("Credenciales guardadas en .env")
    print(f"  CLIENT_ID:     {client_config['client_id'][:30]}...")
    print(f"  REFRESH_TOKEN: {creds.refresh_token[:30]}...")
    print(f"  RENEWED_AT:    {renewed_at}")

    print("Sincronizando con Railway...")
    try:
        subprocess.run(
            ["railway", "variables", "set",
             f"GMAIL_REFRESH_TOKEN={creds.refresh_token}",
             f"GMAIL_TOKEN_RENEWED_AT={renewed_at}",
             "--service", "web"],
            check=True, capture_output=True, text=True
        )
        print("  Railway actualizado.")
    except Exception as e:
        print(f"  Railway no actualizado (hacelo manual): {e}")

if __name__ == "__main__":
    main()
