"""Smoke test de la API de Notion. No escribe nada, solo lee.

Confirma cuatro cosas antes de que exista una linea de logica de sync:
la version de API que el token acepta, el data_source_id de la database,
que la property CRM ID exista, y a que grupo pertenece cada estado.

Uso:
    NOTION_TOKEN=... NOTION_DATABASE_ID=... python scripts/notion_smoke.py
"""

import json
import os
import sys

import requests

VERSIONES = ["2025-09-03", "2022-06-28"]
DB_ID = os.environ.get("NOTION_DATABASE_ID", "3ae65d94-deec-8093-8c48-cfbe77e202d5")


def _headers(version: str) -> dict:
    """Arma las cabeceras de autenticacion para una version de API dada."""
    return {
        "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
        "Notion-Version": version,
        "Content-Type": "application/json",
    }


def main() -> int:
    """Prueba cada version de API contra la database y reporta lo que encuentra."""
    if not os.environ.get("NOTION_TOKEN"):
        print("Falta NOTION_TOKEN")
        return 1

    for version in VERSIONES:
        r = requests.get(f"https://api.notion.com/v1/databases/{DB_ID}",
                         headers=_headers(version), timeout=20)
        print(f"\n=== Notion-Version {version} -> HTTP {r.status_code}")
        if r.status_code != 200:
            print(r.text[:400])
            continue

        data = r.json()
        print("title:", "".join(t.get("plain_text", "") for t in data.get("title", [])))

        # En 2025-09-03 la database expone data_sources; en 2022-06-28 no existen
        # y las queries van contra /v1/databases/{id}/query.
        fuentes = data.get("data_sources") or []
        print("data_sources:", json.dumps(fuentes))

        props = data.get("properties", {})
        print("properties:", sorted(props))
        print("CRM ID presente:", "CRM ID" in props)

        status = props.get("Status", {}).get("status", {})
        for grupo in status.get("groups", []):
            ids = set(grupo.get("option_ids", []))
            nombres = [o["name"] for o in status.get("options", []) if o["id"] in ids]
            print(f"  grupo {grupo['name']}: {nombres}")

        return 0

    print("\nNinguna version funciono. Revisar el token y la conexion de la database.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
