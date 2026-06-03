import os

from dotenv import load_dotenv

load_dotenv()

from database import init_db, seed_pitch_templates
from dashboard import create_app

db_path = os.environ.get("DB_PATH", "leads.db")
init_db(db_path)
seed_pitch_templates(db_path)

app = create_app(db_path)

@app.route('/admin/download-db')
def download_db():
    import os
    from flask import send_file, abort, request
    token = request.headers.get('x-admin-token', '')
    expected = os.environ.get('ADMIN_TOKEN', '')
    if not expected or token != expected:
        abort(403)
    return send_file(db_path, as_attachment=True, download_name='leads_prod.db')

if __name__ == "__main__":
    from dashboard import run
    run(db_path)
