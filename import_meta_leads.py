"""
One-time import of historical Meta Lead Ads submissions.
Fetches all lead forms from the page and imports all leads.
"""
import os, requests, json
from dotenv import load_dotenv
load_dotenv()

PAGE_ID    = os.environ["META_PAGE_ID"]
PAGE_TOKEN = os.environ["META_PAGE_TOKEN"]
DB_PATH    = os.environ.get("DB_PATH", "leads.db")

import sys
sys.path.insert(0, os.path.dirname(__file__))
from database import init_db, insert_business, update_business, get_business, log_activity
from meta_config import GRAPH

init_db(DB_PATH)

def get_all(url, params):
    results = []
    while url:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        d = r.json()
        results.extend(d.get("data", []))
        url = d.get("paging", {}).get("next")
        params = {}  # next URL already has params
    return results

# 1. Get all lead forms for the page
print(f"[1] Fetching lead forms for page {PAGE_ID}...")
forms = get_all(
    f"{GRAPH}/{PAGE_ID}/leadgen_forms",
    {"access_token": PAGE_TOKEN, "fields": "id,name,status,leads_count"}
)
print(f"    Found {len(forms)} form(s)")
for f in forms:
    print(f"    - {f.get('name','?')} (id:{f['id']}) leads:{f.get('leads_count','?')} status:{f.get('status','?')}")

if not forms:
    print("No forms found. Make sure the page token has leads_retrieval permission.")
    exit(1)

# 2. Fetch leads from each form
total_new = 0
total_dup = 0

for form in forms:
    form_id   = form["id"]
    form_name = form.get("name", form_id)
    print(f"\n[2] Importing leads from form: {form_name}...")

    leads = get_all(
        f"{GRAPH}/{form_id}/leads",
        {
            "access_token": PAGE_TOKEN,
            "fields": "id,created_time,field_data,ad_name,campaign_name",
        }
    )
    print(f"    {len(leads)} lead(s) found")

    for lead in leads:
        fields = {
            f["name"].lower(): f["values"][0] if f.get("values") else ""
            for f in lead.get("field_data", [])
        }

        name  = (fields.get("full_name") or fields.get("nombre") or
                 fields.get("name") or fields.get("nombre_completo") or "Lead Meta")
        phone = (fields.get("phone_number") or fields.get("telefono") or
                 fields.get("phone") or fields.get("celular") or "")
        email = fields.get("email") or fields.get("correo") or ""
        city  = fields.get("city") or fields.get("ciudad") or ""

        ad_name       = lead.get("ad_name", "")
        campaign_name = lead.get("campaign_name", "")
        notes = f"Meta Lead Ad · {campaign_name or ad_name or form_name}".strip(" ·")

        ct = lead.get("created_time","")
        if ct:
            try:
                from datetime import datetime, timezone
                ct = datetime.fromisoformat(ct.replace("+0000","")).replace(tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ct = ""
        biz_id = insert_business(DB_PATH, {
            "name":       name,
            "phone":      phone or None,
            "city":       city or None,
            "category":   "Meta Lead Ad",
            "status":     "scraped",
            "notes":      notes,
            "score":      70,
            "source":     "meta",
            "form_data":  json.dumps(fields, ensure_ascii=False),
            "scraped_at": ct or None,
        })

        if biz_id:
            log_activity(DB_PATH, "meta_import", "lead_created", "lead", biz_id, name,
                         f"Importado: {campaign_name or ad_name or form_name}", user_id=None)
            total_new += 1
            print(f"    + {name} ({phone or 'sin tel'})")
        else:
            total_dup += 1

print(f"\n✓ Importación completa: {total_new} nuevos, {total_dup} duplicados ignorados")
