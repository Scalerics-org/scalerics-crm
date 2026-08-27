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

from database import init_db, seed_linkedin_temas, seed_pitch_templates
from dashboard import create_app

db_path = os.environ.get("DB_PATH", "leads.db")
init_db(db_path)
seed_pitch_templates(db_path)
seed_linkedin_temas(db_path)

app = create_app(db_path)

if __name__ == "__main__":
    from dashboard import run
    run(db_path)
