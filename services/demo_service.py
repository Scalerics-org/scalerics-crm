"""Demo generation service — idempotent, async via job queue."""

import json
import logging
import sqlite3
from typing import Optional

from database import (
    create_demo, get_demo_for_client, update_demo,
    create_job, get_business, update_business,
)

logger = logging.getLogger(__name__)


class DemoAlreadyExists(Exception):
    """Raised when a completed demo already exists for this client."""
    def __init__(self, url: str):
        self.url = url
        super().__init__(f"Demo already exists: {url}")


class DemoCurrentlyGenerating(Exception):
    """Raised when a demo is already being generated for this client."""


def get_existing_demo(db_path: str, client_id: int) -> Optional[dict]:
    return get_demo_for_client(db_path, client_id)


def request_demo(db_path: str, client_id: int, params: dict) -> dict:
    """
    Idempotent demo generation request.

    - If a completed demo exists: raises DemoAlreadyExists.
    - If generation is in progress: raises DemoCurrentlyGenerating.
    - If previous attempt failed: retries (resets to pending).
    - Otherwise: creates demo record + dispatches background job.

    Returns: {demo_id, job_id, status: 'generating'}
    """
    demo = get_demo_for_client(db_path, client_id)

    if demo:
        if demo["status"] == "completed":
            raise DemoAlreadyExists(demo["url"] or "")
        if demo["status"] == "generating":
            raise DemoCurrentlyGenerating()
        # status == 'failed' or 'pending': allow retry
        demo_id = demo["id"]
        update_demo(db_path, demo_id, status="pending", error_message=None)
    else:
        try:
            demo_id = create_demo(db_path, client_id)
        except sqlite3.IntegrityError:
            # Race condition: another request created the demo simultaneously
            raise DemoCurrentlyGenerating()

    job_payload = json.dumps({
        "client_id": client_id,
        "demo_id": demo_id,
        "db_path": db_path,
        **{k: v for k, v in params.items() if v is not None},
    })
    job_id = create_job(db_path, "demo", job_payload)
    update_demo(db_path, demo_id, status="generating")

    logger.info(f"Demo requested for client {client_id} — job {job_id}")
    return {"demo_id": demo_id, "job_id": job_id, "status": "generating"}


def demo_job_handler(payload: dict) -> dict:
    """
    Background job handler. Called by JobWorker.
    Generates and deploys the demo, then updates DB.
    """
    import demo_ai

    client_id: int = payload["client_id"]
    demo_id: int = payload["demo_id"]
    db_path: str = payload["db_path"]

    client = get_business(db_path, client_id)
    if not client:
        raise ValueError(f"Client {client_id} not found")

    try:
        result = demo_ai.generate_and_deploy(
            phone=client.get("phone", ""),
            business_name=payload.get("business_name") or client.get("name", ""),
            rubro=payload.get("rubro") or client.get("category", ""),
            city=payload.get("city") or client.get("city", ""),
            client_color=payload.get("client_color", ""),
            lead_name=payload.get("lead_name", ""),
            messages=payload.get("messages", []),
        )
        url: str = result.get("url", "")
        update_demo(db_path, demo_id, status="completed", url=url)
        update_business(db_path, client_id, demo_url=url, status="demo_generated")
        logger.info(f"Demo completed for client {client_id}: {url}")
        return {"url": url, "questions": result.get("questions", [])}
    except Exception as e:
        update_demo(db_path, demo_id, status="failed", error_message=str(e))
        logger.error(f"Demo failed for client {client_id}: {e}")
        raise
