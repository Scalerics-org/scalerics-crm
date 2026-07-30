import base64
import logging
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from database import get_businesses_by_status, update_business

load_dotenv()
logger = logging.getLogger(__name__)

def build_deployment_payload(html_content: str, deployment_name: str, project_name: str) -> dict:
    encoded = base64.b64encode(html_content.encode("utf-8")).decode("ascii")
    return {
        "name": project_name,
        "files": [
            {
                "file": "index.html",
                "data": encoded,
                "encoding": "base64",
            }
        ],
        "projectSettings": {
            "framework": None,
            "devCommand": None,
            "buildCommand": None,
            "outputDirectory": None,
        },
        "target": "production",
    }

def extract_url_from_response(data: dict) -> str:
    return f"https://{data['url']}"

def deploy_html_to_vercel(
    html_path: str,
    business_id: int,
    vercel_token: str,
    project_name: str,
    max_retries: int = 3,
) -> str:
    html_content = Path(html_path).read_text(encoding="utf-8")
    deployment_name = f"{project_name}-{int(business_id)}"
    payload = build_deployment_payload(html_content, deployment_name, project_name)

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(
                "https://api.vercel.com/v13/deployments",
                headers={
                    "Authorization": f"Bearer {vercel_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            return extract_url_from_response(response.json())
        except Exception as e:
            logger.warning(f"Intento {attempt}/{max_retries} fallido para business {business_id}: {e}")
            if attempt < max_retries:
                time.sleep(2 ** attempt)
            else:
                raise

def run(db_path: str, vercel_token: str, project_name: str) -> None:
    businesses = get_businesses_by_status(db_path, "demo_generated")
    logger.info(f"Desplegando {len(businesses)} demos a Vercel")

    for biz in businesses:
        try:
            logger.info(f"Deployando: {biz['name']}")
            url = deploy_html_to_vercel(
                biz["demo_html_path"],
                biz["id"],
                vercel_token,
                project_name,
            )
            update_business(db_path, biz["id"], demo_url=url, status="demo_deployed")
            logger.info(f"Deploy exitoso: {url}")
            time.sleep(2)
        except Exception as e:
            logger.error(f"Error deployando {biz['name']}: {e}")
            update_business(db_path, biz["id"], status="error", error_message=str(e))
