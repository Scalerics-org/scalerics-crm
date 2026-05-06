import logging
import random
import re

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from database import get_businesses_by_status, update_business
from scraper import USER_AGENTS, random_delay

load_dotenv()
logger = logging.getLogger(__name__)

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

def extract_email_from_text(text: str) -> str | None:
    match = EMAIL_REGEX.search(text)
    return match.group() if match else None

def find_email_on_page(page) -> str | None:
    try:
        text = page.inner_text("body")
        return extract_email_from_text(text)
    except Exception:
        return None

def check_gmaps_page(maps_url: str, page) -> str | None:
    try:
        page.goto(maps_url, wait_until="networkidle", timeout=20000)
        random_delay(2, 4)
        return find_email_on_page(page)
    except Exception as e:
        logger.debug(f"Error en GMaps email check: {e}")
        return None

def check_facebook_page(facebook_url: str, page) -> str | None:
    try:
        page.goto(facebook_url, wait_until="networkidle", timeout=20000)
        random_delay(3, 5)
        close_btn = page.query_selector("[aria-label='Cerrar']")
        if close_btn:
            close_btn.click()
            random_delay(1, 2)
        return find_email_on_page(page)
    except Exception as e:
        logger.debug(f"Error en Facebook email check: {e}")
        return None

def check_instagram_page(instagram_url: str, page) -> str | None:
    try:
        page.goto(instagram_url, wait_until="networkidle", timeout=20000)
        random_delay(3, 5)
        bio_el = page.query_selector("._aacl._aaco._aacu._aacx._aad7._aade")
        if bio_el:
            return extract_email_from_text(bio_el.inner_text())
        return find_email_on_page(page)
    except Exception as e:
        logger.debug(f"Error en Instagram email check: {e}")
        return None

def run(db_path: str) -> None:
    businesses = get_businesses_by_status(db_path, "scraped")
    logger.info(f"Buscando emails para {len(businesses)} negocios")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        Stealth().apply_stealth_sync(page)

        for biz in businesses:
            email = None
            logger.info(f"Buscando email: {biz['name']}")

            if biz.get("maps_url"):
                email = check_gmaps_page(biz["maps_url"], page)

            if not email and biz.get("facebook_url"):
                email = check_facebook_page(biz["facebook_url"], page)

            if not email and biz.get("instagram_url"):
                email = check_instagram_page(biz["instagram_url"], page)

            if email:
                logger.info(f"Email encontrado: {email}")
                update_business(db_path, biz["id"], email=email, status="email_found")
            else:
                logger.info(f"Sin email: {biz['name']}")
                update_business(db_path, biz["id"], status="no_email")

            random_delay(2, 4)

        browser.close()
