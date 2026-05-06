import argparse
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()
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

    subparsers.add_parser("find-emails", help="Buscar emails de los negocios scraped")
    subparsers.add_parser("generate-demos", help="Generar páginas demo con IA")
    subparsers.add_parser("deploy", help="Subir demos a Vercel")
    subparsers.add_parser("send-emails", help="Enviar mails a los negocios")

    run_all_p = subparsers.add_parser("run-all", help="Ejecutar el pipeline completo")
    run_all_p.add_argument("--query", required=True, help='Ej: "restaurante Montevideo"')
    run_all_p.add_argument("--max", type=int, default=100)

    subparsers.add_parser("dashboard", help="Abrir panel de leads en el browser")

    return parser

def cmd_scrape(args):
    from scraper import run
    return run(args.query, args.max, DB_PATH)

def cmd_find_emails(args):
    from email_finder import run
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

def cmd_send_emails(args):
    from email_sender import run
    run(DB_PATH, get_factory_config())

def cmd_dashboard(args):
    from dashboard import run
    run(DB_PATH)

def cmd_run_all(args):
    count = cmd_scrape(args)
    if not count:
        logging.getLogger(__name__).warning("Scraping no encontró negocios sin web. Abortando pipeline.")
        return
    cmd_find_emails(args)
    cmd_generate_demos(args)
    cmd_deploy(args)
    cmd_send_emails(args)

def main():
    parser = create_parser()
    args = parser.parse_args()
    commands = {
        "scrape": cmd_scrape,
        "find-emails": cmd_find_emails,
        "generate-demos": cmd_generate_demos,
        "deploy": cmd_deploy,
        "send-emails": cmd_send_emails,
        "run-all": cmd_run_all,
        "dashboard": cmd_dashboard,
    }
    commands[args.command](args)

if __name__ == "__main__":
    main()
