"""
Run once to:
1. Exchange short-lived user token → long-lived page token
2. Subscribe the webhook to the Facebook page
"""
import os, requests
from dotenv import load_dotenv
load_dotenv()

from meta_config import GRAPH

APP_ID       = os.environ["META_APP_ID"]
APP_SECRET   = os.environ["META_APP_SECRET"]
PAGE_ID      = os.environ["META_PAGE_ID"]
VERIFY_TOKEN = os.environ["META_VERIFY_TOKEN"]
CRM_URL      = os.environ.get("CRM_URL", "https://scalerics-crm.fly.dev")
WEBHOOK_URL  = f"{CRM_URL}/api/meta/webhook"

USER_TOKEN = input("Pegá el user access token del Graph API Explorer: ").strip()

# ── 1. Long-lived user token ──────────────────────────────────────────────────
print("\n[1] Convirtiendo a long-lived user token...")
r = requests.get(f"{GRAPH}/oauth/access_token", params={
    "grant_type": "fb_exchange_token",
    "client_id": APP_ID, "client_secret": APP_SECRET,
    "fb_exchange_token": USER_TOKEN,
})
r.raise_for_status()
ll_user = r.json()["access_token"]
print("    OK — long-lived user token obtenido")

# ── 2. Page access token ──────────────────────────────────────────────────────
print(f"[2] Obteniendo page token para página {PAGE_ID}...")
r = requests.get(f"{GRAPH}/{PAGE_ID}", params={
    "fields": "access_token,name", "access_token": ll_user,
})
r.raise_for_status()
d = r.json()
page_token = d["access_token"]
page_name  = d.get("name", PAGE_ID)
print(f"    OK — página: {page_name}")

# ── 3. Save to .env ───────────────────────────────────────────────────────────
lines = open(".env").readlines()
found = False
for i, line in enumerate(lines):
    if line.startswith("META_PAGE_TOKEN="):
        lines[i] = f"META_PAGE_TOKEN={page_token}\n"; found = True; break
if not found:
    lines.append(f"META_PAGE_TOKEN={page_token}\n")
open(".env", "w").writelines(lines)
print("    Guardado en .env")

# ── 4. Subscribe webhook to page ─────────────────────────────────────────────
print(f"[3] Suscribiendo webhook {WEBHOOK_URL} a la página...")
r = requests.post(f"{GRAPH}/{PAGE_ID}/subscribed_apps", params={
    "access_token": page_token,
    "subscribed_fields": "leadgen",
})
print("    Respuesta:", r.json())

# ── 5. Register webhook on the app ───────────────────────────────────────────
print("[4] Registrando webhook en la app de Meta...")
r = requests.post(f"{GRAPH}/{APP_ID}/subscriptions", params={
    "access_token": f"{APP_ID}|{APP_SECRET}",
    "object": "page",
    "callback_url": WEBHOOK_URL,
    "verify_token": VERIFY_TOKEN,
    "fields": "leadgen",
})
print("    Respuesta:", r.json())

print("\n✓ Setup completo. El CRM ya recibirá leads de Meta Lead Ads.")
print(f"  Webhook URL: {WEBHOOK_URL}")
print(f"  Verify token: {VERIFY_TOKEN}")
