import logging
import os

from dotenv import load_dotenv

load_dotenv()

# Sin esto, los logger.info de routes/ y services/ se descartaban: el lastResort
# de Python es WARNING, asi que no habia forma de saber si un webhook llego o si
# un lead se guardo o se descarto por duplicado. Tambien habilita que el
# errorhandler de dashboard.py deje rastro de las excepciones.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from database import init_db, seed_pitch_templates
from dashboard import create_app

db_path = os.environ.get("DB_PATH", "leads.db")
init_db(db_path)
seed_pitch_templates(db_path)

app = create_app(db_path)

if __name__ == "__main__":
    from dashboard import run
    run(db_path)
