import argparse
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

if not os.environ.get("ANTHROPIC_API_KEY"):
    print("❌ No tenés acceso para ejecutar el pipeline.")
    print("   El único que puede ejecutarlo es Juan, contactate con él.")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("pipeline.log", encoding="utf-8"),
    ],
)

DB_PATH = os.environ.get("DB_PATH", "leads.db")

def get_factory_config() -> dict:
    return {
        "name": os.environ.get("FACTORY_NAME", "Scalerics"),
        "email": os.environ.get("FACTORY_EMAIL", ""),
        "phone": os.environ.get("FACTORY_PHONE", ""),
    }

def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scalerics Lead Generation Pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scrape_p = subparsers.add_parser("scrape", help="Raspar Google Maps")
    scrape_p.add_argument("--query", required=True, help='Ej: "restaurante Montevideo"')
    scrape_p.add_argument("--max", type=int, default=100, help="Máximo de resultados")
    scrape_p.add_argument("--no-verify-web", action="store_true", help="Desactivar verificación Bing (más rápido, menos preciso)")
    scrape_p.add_argument("--con-web", action="store_true",
                          help="Modo discovery: junta los negocios que SI tienen sitio web")

    subparsers.add_parser("generate-pitches", help="Generar texto de pitch WhatsApp por negocio")
    subparsers.add_parser("generate-demos", help="Generar páginas demo con IA")
    subparsers.add_parser("deploy", help="Subir demos a Vercel")

    run_all_p = subparsers.add_parser("run-all", help="Ejecutar el pipeline completo")
    run_all_p.add_argument("--query", required=True, help='Ej: "restaurante Montevideo"')
    run_all_p.add_argument("--max", type=int, default=100)
    run_all_p.add_argument("--verify-web", action="store_true", help="Verificar con Bing si el negocio tiene web (lento)")

    multi_p = subparsers.add_parser("scrape-multi", help="Scrape en paralelo para los 19 departamentos de Uruguay")
    multi_p.add_argument("--query", required=True, help='Ej: "bloquera" — se agrega el departamento automáticamente')
    multi_p.add_argument("--max-per-dept", type=int, default=20, help="Máximo de leads por departamento")
    multi_p.add_argument("--workers", type=int, default=3, help="Threads paralelos (default 3, máx recomendado 4)")
    multi_p.add_argument("--verify-web", action="store_true", help="Verificar con Bing si el negocio tiene web (lento pero más preciso)")
    multi_p.add_argument("--category", default="", help="Rubro forzado para todos los leads (si no se pone, se deriva de la query)")
    multi_p.add_argument("--skip-branded", action="store_true", help="Saltear concesionarias oficiales de marcas conocidas (Hyundai, Toyota, etc.)")
    multi_p.add_argument("--con-web", action="store_true", help="Modo discovery: junta los negocios que SI tienen sitio web")

    fid_p = subparsers.add_parser("scrape-fidelidad",
                                  help="Restaurantes y peluquerías para Scalerics Fidelidad")
    fid_p.add_argument("--rubro", choices=["restaurante", "peluqueria"], default="restaurante")
    fid_p.add_argument("--ciudad", choices=["Montevideo", "Buenos Aires"], default="Montevideo")
    fid_p.add_argument("--max-por-barrio", type=int, default=20, help="Máximo por búsqueda (tipo de local y barrio)")
    fid_p.add_argument("--zona", choices=["Municipio CH", "Carrasco"], default=None,
                       help="En Montevideo, solo una de las dos zonas")

    subparsers.add_parser("dashboard", help="Abrir panel de leads en el browser")

    buscar_p = subparsers.add_parser("buscar-mails",
                                     help="Buscar el mail de los comercios de discovery en su sitio")
    buscar_p.add_argument("--limite", type=int, default=50)

    return parser

def cmd_scrape(args):
    from scraper import run
    default_cat = args.query.split()[0].capitalize()
    return run(args.query, args.max, DB_PATH,
               verify_web=not getattr(args, 'no_verify_web', False),
               default_category=default_cat, solo_con_web=getattr(args, 'con_web', False))

def cmd_generate_pitches(args):
    from pitch_generator import run
    run(DB_PATH)

def cmd_generate_demos(args):
    from demo_generator import run
    api_key = os.environ["ANTHROPIC_API_KEY"]
    run(DB_PATH, api_key)

def cmd_deploy(args):
    from deployer import run
    token = os.environ["VERCEL_TOKEN"]
    project = os.environ.get("VERCEL_PROJECT_NAME", "scalerics-demos")
    run(DB_PATH, token, project)

def cmd_dashboard(args):
    from dashboard import run
    run(DB_PATH)

def cmd_buscar_mails(args):
    from playwright.sync_api import sync_playwright
    from services.email_finder import abrir_con_playwright, procesar_pendientes

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(20000)
        try:
            res = procesar_pendientes(DB_PATH, abrir_con_playwright(page), limite=args.limite)
        finally:
            browser.close()
    print(res)

_DEPARTAMENTOS = [
    "Montevideo", "Canelones", "Maldonado", "Colonia", "San José",
    "Soriano", "Río Negro", "Paysandú", "Salto", "Artigas",
    "Rivera", "Tacuarembó", "Cerro Largo", "Treinta y Tres",
    "Rocha", "Lavalleja", "Florida", "Flores", "Durazno",
]


def cmd_scrape_multi(args):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from scraper import run

    logger = logging.getLogger(__name__)
    base_query = args.query.strip()
    max_per = args.max_per_dept
    workers = min(args.workers, len(_DEPARTAMENTOS))
    verify_web = getattr(args, "verify_web", False)
    default_cat = getattr(args, "category", "").strip() or base_query.split()[0].capitalize()
    skip_branded = getattr(args, "skip_branded", False)
    solo_con_web = getattr(args, "con_web", False)

    logger.info(f"scrape-multi: '{base_query}' × {len(_DEPARTAMENTOS)} depts | {workers} workers | máx {max_per}/dept | verify_web={verify_web} | con_web={solo_con_web}")

    results: dict[str, int] = {}
    errors: dict[str, str] = {}

    def _scrape_dept(dept: str) -> tuple[str, int]:
        query = f"{base_query} {dept} Uruguay"
        count = run(query, max_per, DB_PATH, verify_web=verify_web,
                    default_category=default_cat, skip_branded=skip_branded,
                    solo_con_web=solo_con_web)
        return dept, count

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_scrape_dept, d): d for d in _DEPARTAMENTOS}
        for future in as_completed(futures):
            dept = futures[future]
            try:
                _, count = future.result()
                results[dept] = count
                logger.info(f"[{dept}] OK {count} leads guardados")
            except Exception as exc:
                errors[dept] = str(exc)
                logger.error(f"[{dept}] ERROR {exc}")

    total = sum(results.values())
    logger.info(f"scrape-multi completo. Total: {total} leads en {len(results)} departamentos")
    if errors:
        logger.warning(f"Errores en: {', '.join(errors)}")
    return total


def cmd_scrape_fidelidad(args):
    """Barrio por barrio, tipo de local por tipo de local, y de a uno: Google
    corta rapido a quien le pega en paralelo. Los prospectos van a las tablas de
    Fidelidad (con CRM_URL, al CRM de produccion), no al padron."""
    from scraper import run
    from services.fidelidad import BARRIOS, BARRIOS_BSAS, BUSQUEDAS
    logger = logging.getLogger(__name__)
    if args.ciudad == "Buenos Aires":
        barrios = [(b, "Buenos Aires") for b in BARRIOS_BSAS]
    else:
        barrios = [(b, "Canelones" if b == "Barra de Carrasco" else "Montevideo")
                   for zona, lista in BARRIOS.items() if not args.zona or zona == args.zona for b in lista]
    vistos: set[str] = set()
    total = 0
    for barrio, depto in barrios:
        for tipo in BUSQUEDAS[args.rubro][args.ciudad]:
            n = run(f"{tipo} en {barrio}, {depto}", args.max_por_barrio, DB_PATH, ya_vistos=vistos,
                    fidelidad={"barrio": barrio, "ciudad": args.ciudad, "rubro": args.rubro})
            logger.info(f"[{tipo} · {barrio}] {n} nuevos")
            total += n
    logger.info(f"scrape-fidelidad completo: {total} comercios nuevos")
    return total


def cmd_run_all(args):
    count = cmd_scrape(args)
    if not count:
        logging.getLogger(__name__).warning("Scraping no encontró negocios. Abortando pipeline.")
        return
    cmd_generate_pitches(args)

def main():
    parser = create_parser()
    args = parser.parse_args()
    commands = {
        "scrape": cmd_scrape,
        "scrape-multi": cmd_scrape_multi,
        "generate-pitches": cmd_generate_pitches,
        "generate-demos": cmd_generate_demos,
        "deploy": cmd_deploy,
        "run-all": cmd_run_all,
        "dashboard": cmd_dashboard,
        "buscar-mails": cmd_buscar_mails,
        "scrape-fidelidad": cmd_scrape_fidelidad,
    }
    commands[args.command](args)

if __name__ == "__main__":
    main()
