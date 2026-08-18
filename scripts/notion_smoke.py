"""Smoke test de la API de Notion. No escribe nada, solo lee.

Confirma cuatro cosas antes de que exista una linea de logica de sync:
la version de API que el token acepta, el data_source_id de la database,
que la property CRM ID exista, y a que grupo pertenece cada estado.

Uso:
    python scripts/notion_smoke.py

Lee `NOTION_TOKEN` del entorno o del `.env` del proyecto (que esta en
.gitignore). Asi el token se pega una sola vez en un archivo y no queda en el
historial del shell ni en una linea de comando.
"""

import json
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

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
        print("Falta NOTION_TOKEN: ponelo en el .env del proyecto o exportalo.")
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

        # Desde 2025-09-03 el esquema no vive en la database sino en el data
        # source: `GET /v1/databases/{id}` devuelve properties vacio y hay que
        # preguntarle a `GET /v1/data_sources/{id}`.
        props = data.get("properties", {})
        if not props and fuentes:
            ds_id = fuentes[0]["id"]
            rds = requests.get(f"https://api.notion.com/v1/data_sources/{ds_id}",
                               headers=_headers(version), timeout=20)
            print(f"data source {ds_id} -> HTTP {rds.status_code}")
            if rds.status_code != 200:
                print(rds.text[:400])
                return 1
            props = rds.json().get("properties", {})

        print("properties:", sorted(props))
        print("CRM ID presente:", "CRM ID" in props)
        print("CRM ID tipo:", props.get("CRM ID", {}).get("type"))

        status = props.get("Status", {}).get("status", {})
        print("estados:", [o["name"] for o in status.get("options", [])])
        for grupo in status.get("groups", []):
            ids = set(grupo.get("option_ids", []))
            nombres = [o["name"] for o in status.get("options", []) if o["id"] in ids]
            print(f"  grupo {grupo['name']}: {nombres}")

        return 0

    print("\nNinguna version funciono. Revisar el token y la conexion de la database.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
