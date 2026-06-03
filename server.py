import os

from dotenv import load_dotenv

load_dotenv()

from database import init_db, seed_pitch_templates
from dashboard import create_app

db_path = os.environ.get("DB_PATH", "leads.db")
init_db(db_path)
seed_pitch_templates(db_path)

app = create_app(db_path)

if __name__ == "__main__":
    from dashboard import run
    run(db_path)
