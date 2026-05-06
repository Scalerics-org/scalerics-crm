"""
Run this script ONCE to obtain the Gmail OAuth2 refresh token.
It opens a browser window for authentication and saves credentials to .env.
"""
import json
from google_auth_oauthlib.flow import InstalledAppFlow
from dotenv import load_dotenv, set_key

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

def main():
    load_dotenv()
    flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
    creds = flow.run_local_server(port=0)

    with open("credentials.json") as f:
        client_config = json.load(f)["installed"]

    set_key(".env", "GMAIL_CLIENT_ID", client_config["client_id"])
    set_key(".env", "GMAIL_CLIENT_SECRET", client_config["client_secret"])
    set_key(".env", "GMAIL_REFRESH_TOKEN", creds.refresh_token)

    print("Credenciales guardadas en .env")
    print(f"  CLIENT_ID:     {client_config['client_id'][:30]}...")
    print(f"  REFRESH_TOKEN: {creds.refresh_token[:30]}...")

if __name__ == "__main__":
    main()
