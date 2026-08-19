import logging
import os
import random
import re
import time
import unicodedata
import urllib.parse

import requests as http_requests
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
_CATEGORY_BLOCKLIST_KEYWORDS = [
    "agregar ", "add website", "e-commerce", "centro comercial",
]

# Car brands that indicate an official franchise concession — already have a parent website
CAR_BRAND_PREFIXES = {
    "hyundai", "chevrolet", "toyota", "fiat", "peugeot", "renault", "volkswagen",
    "ford", "nissan", "byd", "suzuki", "citroen", "citroën", "kia", "mitsubishi",
    "honda", "mazda", "jeep", "dodge", "chery", "dfsk", "geely", "mg", "gwm",
    "haval", "changan", "jac", "seat", "skoda", "volvo", "bmw", "mercedes",
    "audi", "subaru", "ram", "isuzu", "ssangyong", "jetour", "omoda", "jaecoo",
}


def _is_brand_franchise(name: str) -> bool:
    """Returns True if the business name starts with a known car brand (official concession)."""
    first_word = _normalize(name).split()[0] if name.strip() else ""
    return first_word in CAR_BRAND_PREFIXES


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

def score_lead(data: dict) -> int:
    score = 0
    if data.get("instagram_url"):
        score += 35
    if data.get("facebook_url"):
        score += 15
    rating = data.get("rating") or 0
    if rating >= 4.0:
        score += 20
    elif rating >= 3.5:
        score += 10
    reviews = data.get("review_count") or 0
    if reviews >= 20:
        score += 15
    elif reviews >= 5:
        score += 8
    if data.get("hours"):
        score += 10
    if data.get("address"):
        score += 5
    return score


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

        # Sin resultados cargados no se puede concluir nada: si Bing tarda o no
        # devuelve cites, asumir "tiene web" y descartar el lead en vez de darlo
        # por bueno con informacion incompleta.
        try:
            page.wait_for_selector("cite", timeout=6000)
        except Exception:
            logger.debug(f"Bing sin elementos cite para {name} — conservador: asume web")
            return False

        cites = page.query_selector_all("cite")
        if not cites:
            logger.debug(f"Bing devolvió lista cite vacía para {name} — conservador: asume web")
            return False

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

def is_permanently_closed(page) -> bool:
    """Returns True if Google Maps shows the business as permanently closed."""
    content = (page.content() or "").lower()
    return "permanentemente cerrado" in content or "permanently closed" in content

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
        href = website_el.get_attribute("href") or ""
        # Only treat as a real website if it's NOT a social/directory domain
        if href and not any(d in href for d in (
            "facebook.com", "instagram.com", "twitter.com", "x.com",
            "tiktok.com", "youtube.com", "linkedin.com", "wa.me", "whatsapp.com",
        )):
            maps_website_url = href

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

def _remote_insert(data: dict) -> bool:
    """POST a business to the remote Railway CRM. Returns True if inserted, False if duplicate/error."""
    crm_url = os.environ.get("CRM_URL", "").rstrip("/")
    token = os.environ.get("ADMIN_TOKEN", "")
    if not crm_url or not token:
        return False
    try:
        resp = http_requests.post(
            f"{crm_url}/api/leads",
            json=data,
            headers={"x-admin-token": token},
            timeout=10,
        )
        body = resp.json()
        if resp.status_code not in (200, 201):
            logger.warning(f"CRM remoto respondió {resp.status_code}: {body}")
        return resp.status_code == 201 and body.get("ok", False)
    except Exception as e:
        logger.warning(f"Error al enviar a CRM remoto: {e}")
        return False


def _debe_guardar(data: dict, solo_con_web: bool, skip_branded: bool) -> tuple[bool, str]:
    """Decide si un negocio se guarda. Devuelve (guardar, motivo_del_descarte).

    Es una funcion aparte y no un if adentro del bucle porque el bucle corre
    contra Google Maps con Playwright y no se puede probar; esto si.

    `solo_con_web` invierte el criterio: el modo por defecto junta negocios SIN
    sitio web (a esos se les vende una pagina), y el modo discovery junta los
    que SI lo tienen (a esos se les vende automatizacion, y su sitio es de
    donde se saca la direccion de mail).
    """
    if not data.get("phone"):
        return False, "sin teléfono"

    tiene_web = bool(data.get("maps_website_url"))
    if solo_con_web and not tiene_web:
        return False, "sin web en Maps"
    if not solo_con_web and tiene_web:
        return False, "con web en Maps"

    if skip_branded and _is_brand_franchise(data.get("name", "")):
        return False, "franquicia de marca"

    return True, ""


def scrape_google_maps(query: str, max_results: int, db_path: str, verify_web: bool = False, default_category: str = "", skip_branded: bool = False, solo_con_web: bool = False) -> int:
    inserted = 0
    maps_list_url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"

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

        logger.info(f"Buscando: {maps_list_url}")
        page.goto(maps_list_url, wait_until="domcontentloaded", timeout=30000)
        random_delay(2, 4)

        # Handle Google consent / cookie dialog (common on cloud IPs)
        if "consent.google" in page.url or page.query_selector('form[action*="consent"]'):
            logger.info("Detectado dialog de consentimiento Google — aceptando...")
            for sel in ['button[aria-label*="Accept"]', 'button[jsname="b3VHJd"]',
                        'form:nth-of-type(2) button', 'button:has-text("Aceptar")']:
                try:
                    btn = page.query_selector(sel)
                    if btn:
                        btn.click()
                        page.wait_for_load_state("domcontentloaded", timeout=10000)
                        random_delay(1, 2)
                        break
                except Exception:
                    pass

        logger.info(f"URL actual: {page.url!r} | Título: {page.title()!r}")
        # Count total links to diagnose what loaded
        all_links = page.query_selector_all("a[href]")
        place_links = [el.get_attribute("href") for el in all_links
                       if "/maps/place/" in (el.get_attribute("href") or "")]
        logger.info(f"Total <a>: {len(all_links)} | Links a /maps/place/: {len(place_links)}")

        seen_urls: set[str] = set()
        prev_result_count = 0

        while inserted < max_results:
            hrefs = []
            for el in page.query_selector_all("a[href]"):
                href = el.get_attribute("href") or ""
                if "/maps/place/" in href and href not in seen_urls:
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
                        page.goto(href, wait_until="domcontentloaded", timeout=15000)
                        page.wait_for_selector("h1.DUwDvf", timeout=10000)
                        random_delay()

                        # Permanently closed → no point contacting them
                        if is_permanently_closed(page):
                            logger.info(f"Saltando (cerrado permanentemente): {extract_text(page, 'h1.DUwDvf')}")
                            page.goto(maps_list_url, wait_until="domcontentloaded", timeout=30000)
                            random_delay()
                            break

                        data = extract_business_data(page)

                        # default_category always wins — whatever Maps says gets replaced
                        if default_category:
                            data["category"] = default_category

                        data["score"] = score_lead(data)

                        guardar, motivo = _debe_guardar(data, solo_con_web, skip_branded)
                        if not guardar:
                            logger.info(f"Saltando ({motivo}): {data['name']}")
                            page.goto(maps_list_url, wait_until="domcontentloaded", timeout=30000)
                            random_delay()
                            break

                        # Discovery mode: the site itself is the source for the email address
                        if solo_con_web:
                            data["website"] = data.get("maps_website_url")
                            data["source"] = "discovery"

                        # Optional Bing double-check (slow, off by default); pointless in
                        # discovery mode, which wants businesses WITH a website
                        if verify_web and not solo_con_web:
                            logger.info(f"Verificando con Bing: {data['name']}")
                            no_web = verify_no_website(data["name"], data["city"], page)
                            if not no_web:
                                logger.info(f"Saltando (web encontrada en Bing): {data['name']}")
                                page.goto(maps_list_url, wait_until="domcontentloaded", timeout=30000)
                                page.wait_for_selector('a[href^="https://www.google.com/maps/place/"]', timeout=10000)
                                random_delay(2, 4)
                                break

                        # Insert business — remote Railway CRM or local SQLite
                        if os.environ.get("CRM_URL"):
                            saved = _remote_insert(data)
                        else:
                            saved = bool(insert_business(db_path, data))
                        if saved:
                            inserted += 1
                            made_progress = True
                            logger.info(f"[{inserted}/{max_results}] Guardado: {data['name']}")
                        else:
                            logger.warning(f"No guardado (duplicado o error CRM remoto): {data['name']}")

                        page.goto(maps_list_url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_selector('a[href^="https://www.google.com/maps/place/"]', timeout=10000)
                        random_delay()
                        break

                    except Exception as e:
                        if attempt < 2:
                            logger.warning(f"Reintentando resultado (intento {attempt + 1}): {e}")
                            random_delay(2, 4)
                        else:
                            logger.error(f"Saltando resultado tras 3 intentos fallidos: {e}")
                            try:
                                page.goto(maps_list_url, wait_until="domcontentloaded", timeout=30000)
                                page.wait_for_selector('a[href^="https://www.google.com/maps/place/"]', timeout=10000)
                            except Exception:
                                pass
                            random_delay(2, 4)

            scroll_container = page.query_selector('div[role="feed"]')
            if scroll_container:
                scroll_container.evaluate("el => el.scrollBy(0, 1000)")
            random_delay(2, 3)

            new_results = page.query_selector_all('a[href^="https://www.google.com/maps/place/"]')
            if len(new_results) <= prev_result_count and not made_progress:
                logger.info("No hay más resultados para cargar")
                break
            prev_result_count = len(new_results)

        browser.close()

    logger.info(f"Scraping completo. Guardados: {inserted} negocios")
    return inserted

def run(query: str, max_results: int, db_path: str, verify_web: bool = False, default_category: str = "", skip_branded: bool = False, solo_con_web: bool = False) -> int:
    init_db(db_path)
    return scrape_google_maps(query, max_results, db_path, verify_web=verify_web, default_category=default_category, skip_branded=skip_branded, solo_con_web=solo_con_web)
