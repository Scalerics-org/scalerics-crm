import logging
import random
import re
import time
import unicodedata
import urllib.parse

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

# Domains that are NOT a real business website (directories, social, delivery, etc.)
DIRECTORY_DOMAINS = {
    "google.com", "maps.google.com", "facebook.com", "instagram.com",
    "twitter.com", "x.com", "tiktok.com", "youtube.com", "linkedin.com",
    "tripadvisor.com", "tripadvisor.com.ar", "tripadvisor.com.uy",
    "yelp.com", "foursquare.com", "cybo.com",
    "paginasamarillas.com.uy", "paginasamarillas.com",
    "infonegocios.com.uy", "movete.com.uy", "gimnasios.com.uy",
    "mercadofitness.com", "localgymsandfitness.com", "fitfit.fitness",
    "mercadolibre.com", "mercadolibre.com.uy",
    "carta.menu", "restorando.com", "pedidosya.com", "rappi.com", "glovo.com",
    "booking.com", "airbnb.com", "whatsapp.com",
    "dle.rae.es", "wordreference.com", "thefreedictionary.com",
    "definicion.de", "definiciones-de.com", "significadosweb.com",
}

def random_delay(min_s: float = 3.0, max_s: float = 8.0) -> None:
    time.sleep(random.uniform(min_s, max_s))

def extract_text(page, selector: str) -> str:
    try:
        el = page.query_selector(selector)
        return el.inner_text().strip() if el else ""
    except Exception:
        return ""

def _normalize(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()

def _domain_from_cite(cite_text: str) -> str:
    """Extract bare domain from a Bing cite element like 'https://www.example.com › page'."""
    part = cite_text.split("›")[0].strip()
    try:
        host = urllib.parse.urlparse(part).hostname or part
        return host.lower().removeprefix("www.")
    except Exception:
        return part.lower().removeprefix("www.")

def _name_in_domain(name: str, domain: str) -> bool:
    """True if any significant word from the business name appears in the domain."""
    norm_domain = _normalize(domain)
    words = [w for w in re.split(r"\W+", _normalize(name)) if len(w) > 3]
    return any(w in norm_domain for w in words)

def verify_no_website(name: str, city: str, page) -> bool:
    """
    Returns True if confident the business has no real website.
    Uses Bing cite elements + business name matching.
    """
    query = f"{name} {city} Uruguay"
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(query)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        random_delay(2, 4)

        cites = page.query_selector_all("cite")
        for cite in cites[:8]:
            raw = cite.inner_text().strip()
            domain = _domain_from_cite(raw)
            if not domain:
                continue
            # Skip known directories
            if any(d in domain for d in DIRECTORY_DOMAINS):
                continue
            # If the domain contains words from the business name → it's their site
            if _name_in_domain(name, domain):
                logger.debug(f"Web encontrada (nombre coincide) para {name}: {domain}")
                return False
            # Any non-directory .com.uy or .uy domain in top 3 results → likely their site
            if cites.index(cite) < 3 and (domain.endswith(".com.uy") or domain.endswith(".uy")):
                logger.debug(f"Web encontrada (.uy en top 3) para {name}: {domain}")
                return False

        return True
    except Exception as e:
        logger.debug(f"Error en verificación Bing para {name}: {e}")
        return True

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

    maps_website_url = None
    website_el = page.query_selector("[data-item-id='authority']")
    if website_el:
        maps_website_url = website_el.get_attribute("href") or None

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
        "maps_website_url": maps_website_url,
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

                for attempt in range(3):
                    try:
                        el = page.query_selector(f'.hfpxzc[href="{href}"]')
                        if not el:
                            break
                        el.click()
                        page.wait_for_selector("h1.DUwDvf", timeout=10000)
                        random_delay()

                        data = extract_business_data(page)

                        # Maps shows a website link → has web, skip
                        if data.get("maps_website_url"):
                            logger.info(f"Saltando (web en Maps): {data['name']}")
                            page.go_back(wait_until="domcontentloaded")
                            page.wait_for_selector(".hfpxzc", timeout=10000)
                            random_delay()
                            break

                        # No Maps link → verify with Bing
                        logger.info(f"Verificando con Bing: {data['name']}")
                        no_web = verify_no_website(data["name"], data["city"], page)

                        maps_list_url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"
                        if not no_web:
                            logger.info(f"Saltando (web encontrada en Bing): {data['name']}")
                            page.goto(maps_list_url, wait_until="domcontentloaded", timeout=30000)
                            page.wait_for_selector(".hfpxzc", timeout=10000)
                            random_delay(2, 4)
                            break

                        # Confirmed: no website → save
                        business_id = insert_business(db_path, data)
                        if business_id:
                            inserted += 1
                            made_progress = True
                            logger.info(f"[{inserted}/{max_results}] Guardado: {data['name']}")
                        else:
                            logger.info(f"Duplicado, ignorado: {data['name']}")

                        page.goto(maps_list_url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_selector(".hfpxzc", timeout=10000)
                        random_delay()
                        break

                    except Exception as e:
                        if attempt < 2:
                            logger.warning(f"Reintentando resultado (intento {attempt + 1}): {e}")
                            random_delay(2, 4)
                        else:
                            logger.error(f"Saltando resultado tras 3 intentos fallidos: {e}")
                            try:
                                maps_list_url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"
                                page.goto(maps_list_url, wait_until="domcontentloaded", timeout=30000)
                                page.wait_for_selector(".hfpxzc", timeout=10000)
                            except Exception:
                                pass
                            random_delay(2, 4)

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
