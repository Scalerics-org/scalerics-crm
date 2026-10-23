import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

# Sin esto el logger raiz queda en WARNING y todo lo que el CRM registra con
# `logger.info` se pierde: el sync de Notion, el de Calendly y el import de
# leads de Meta solo dejarian rastro cuando fallan. En Fly los logs son la
# unica superficie de diagnostico que hay, asi que un sync que funciona tiene
# que verse tanto como uno que se rompe.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)

# Vista previa de una PR en Vercel (.github/workflows/preview.yml): base de
# datos inventados en /tmp y sin procesos de fondo, así no manda mails ni toca
# nada real. En producción (Fly) CRM_PREVIEW no existe y esto no corre.
if os.environ.get("CRM_PREVIEW") == "1":
    os.environ["DB_PATH"] = "/tmp/preview.db"
    os.environ["CRM_SIN_PROCESOS_DE_FONDO"] = "true"
    os.environ.setdefault("PREVIEW_EMAIL", "preview@scalerics.com")
    os.environ["ADMIN_EMAIL"] = os.environ["PREVIEW_EMAIL"]
    from scripts.preview_seed import LARGO_MINIMO_CLAVE, sembrar
    if len(os.environ.get("PREVIEW_PASSWORD", "")) < LARGO_MINIMO_CLAVE:
        raise RuntimeError("PREVIEW_PASSWORD falta o es corta: la vista previa no arranca sin una clave propia")
    sembrar(os.environ["DB_PATH"], os.environ["PREVIEW_EMAIL"], os.environ["PREVIEW_PASSWORD"])

from database import (init_db, seed_linkedin_banco, seed_linkedin_temas,
                      seed_pitch_templates)
from dashboard import create_app

db_path = os.environ.get("DB_PATH", "leads.db")
init_db(db_path)
seed_pitch_templates(db_path)
seed_linkedin_temas(db_path)
seed_linkedin_banco(db_path)

app = create_app(db_path)

if __name__ == "__main__":
    from dashboard import run
    run(db_path)
