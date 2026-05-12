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

    subparsers.add_parser("generate-pitches", help="Generar texto de pitch WhatsApp por negocio")
    subparsers.add_parser("generate-demos", help="Generar páginas demo con IA")
    subparsers.add_parser("deploy", help="Subir demos a Vercel")

    run_all_p = subparsers.add_parser("run-all", help="Ejecutar el pipeline completo")
    run_all_p.add_argument("--query", required=True, help='Ej: "restaurante Montevideo"')
    run_all_p.add_argument("--max", type=int, default=100)
    run_all_p.add_argument("--verify-web", action="store_true", help="Verificar con Bing si el negocio tiene web (lento)")

    subparsers.add_parser("dashboard", help="Abrir panel de leads en el browser")

    return parser

def cmd_scrape(args):
    from scraper import run
    return run(args.query, args.max, DB_PATH, verify_web=not getattr(args, 'no_verify_web', False))

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
        "generate-pitches": cmd_generate_pitches,
        "generate-demos": cmd_generate_demos,
        "deploy": cmd_deploy,
        "run-all": cmd_run_all,
        "dashboard": cmd_dashboard,
    }
    commands[args.command](args)

if __name__ == "__main__":
    main()
