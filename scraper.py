import logging
import random
import time

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from database import init_db, insert_business

load_dotenv()
logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

def random_delay(min_s: float = 3.0, max_s: float = 8.0) -> None:
    time.sleep(random.uniform(min_s, max_s))

def extract_text(page, selector: str) -> str:
    try:
        el = page.query_selector(selector)
        return el.inner_text().strip() if el else ""
    except Exception:
        return ""

def extract_business_data(page) -> dict:
    name = extract_text(page, "h1.DUwDvf")
    category = extract_text(page, ".DkEaL")
    address = extract_text(page, "[data-item-id='address'] .Io6YTe")

    phone_el = page.query_selector("[data-item-id^='phone:tel']")
    phone = ""
    if phone_el:
        raw = phone_el.get_attribute("data-item-id") or ""
        phone = raw.replace("phone:tel:", "").strip()

    rating_text = extract_text(page, ".MW4etd")
    review_text = extract_text(page, ".UY7F9")
    hours = extract_text(page, ".ZDu9vd")

    try:
        rating = float(rating_text.replace(",", ".")) if rating_text else None
    except ValueError:
        rating = None

    try:
        review_count = int(
            review_text.replace(".", "").replace(",", "").strip("()").replace("\xa0", "")
        ) if review_text else None
    except ValueError:
        review_count = None

    website_el = page.query_selector("[data-item-id='authority']")
    has_website = website_el is not None

    facebook_url = None
    instagram_url = None
    for link in page.query_selector_all("a[href]"):
        href = link.get_attribute("href") or ""
        if "facebook.com" in href and not facebook_url:
            facebook_url = href
        if "instagram.com" in href and not instagram_url:
            instagram_url = href

    city = ""
    if address:
        parts = address.split(",")
        city = parts[-1].strip() if len(parts) > 1 else parts[0].strip()

    return {
        "name": name,
        "category": category,
        "address": address,
        "city": city,
        "phone": phone,
        "rating": rating,
        "review_count": review_count,
        "hours": hours,
        "maps_url": page.url,
        "facebook_url": facebook_url,
        "instagram_url": instagram_url,
        "has_website": has_website,
    }

def scrape_google_maps(query: str, max_results: int, db_path: str) -> int:
    inserted = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={
                "width": random.randint(1280, 1920),
                "height": random.randint(800, 1080),
            },
        )
        page = context.new_page()
        Stealth().apply_stealth_sync(page)

        search_url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"
        logger.info(f"Buscando: {search_url}")
        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        random_delay(2, 4)

        seen_urls: set[str] = set()
        prev_result_count = 0

        while inserted < max_results:
            hrefs = []
            for el in page.query_selector_all(".hfpxzc"):
                href = el.get_attribute("href") or ""
                if href and href not in seen_urls:
                    hrefs.append(href)

            if not hrefs:
                logger.warning("No se encontraron resultados en la página")
                break

            made_progress = False
            for href in hrefs:
                if inserted >= max_results:
                    break
                if href in seen_urls:
                    continue
                seen_urls.add(href)
                try:
                    el = page.query_selector(f'.hfpxzc[href="{href}"]')
                    if not el:
                        continue
                    el.click()
                    page.wait_for_selector("h1.DUwDvf", timeout=10000)
                    random_delay()

                    data = extract_business_data(page)

                    if data["has_website"]:
                        logger.info(f"Saltando (tiene web): {data['name']}")
                    else:
                        business_id = insert_business(db_path, data)
                        if business_id:
                            inserted += 1
                            made_progress = True
                            logger.info(f"[{inserted}/{max_results}] Guardado: {data['name']}")
                        else:
                            logger.info(f"Duplicado, ignorado: {data['name']}")

                    page.go_back(wait_until="domcontentloaded")
                    page.wait_for_selector(".hfpxzc", timeout=10000)
                    random_delay()

                except Exception as e:
                    logger.error(f"Error procesando resultado: {e}")
                    try:
                        page.go_back(wait_until="domcontentloaded")
                        page.wait_for_selector(".hfpxzc", timeout=10000)
                    except Exception:
                        pass
                    random_delay(2, 4)

            # Scroll para cargar más resultados
            scroll_container = page.query_selector(".m6QErb[aria-label]")
            if scroll_container:
                scroll_container.evaluate("el => el.scrollBy(0, 1000)")
            random_delay(2, 3)

            new_results = page.query_selector_all(".hfpxzc")
            if len(new_results) <= prev_result_count and not made_progress:
                logger.info("No hay más resultados para cargar")
                break
            prev_result_count = len(new_results)

        browser.close()

    logger.info(f"Scraping completo. Guardados: {inserted} negocios")
    return inserted

def run(query: str, max_results: int, db_path: str) -> int:
    init_db(db_path)
    return scrape_google_maps(query, max_results, db_path)
