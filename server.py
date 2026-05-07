import os
from dotenv import load_dotenv
load_dotenv()

import dashboard
from database import init_db

db_path = os.environ.get("DB_PATH", "leads.db")
init_db(db_path)
dashboard._db_path = db_path
app = dashboard.app
