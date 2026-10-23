"""Entrada del CRM en Vercel, solo para las vistas previas de las PRs.

Producción sigue en Fly (fly.toml, start.sh). Acá el CRM arranca con una base
de datos inventados en /tmp (scripts/preview_seed.py) y sin procesos de fondo:
no manda mails, no importa leads, no cobra nada.

Ojo: cada instancia de la función tiene su propio /tmp. Lo que se carga en la
vista previa puede no estar en la visita siguiente si Vercel la atiende otra
instancia. Para ver cómo se ve una PR alcanza; no es un entorno de pruebas
con datos que duren.
"""

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

os.environ["DB_PATH"] = "/tmp/preview.db"
os.environ["CRM_SIN_PROCESOS_DE_FONDO"] = "true"
os.environ.setdefault("PREVIEW_EMAIL", "preview@scalerics.com")
os.environ["ADMIN_EMAIL"] = os.environ["PREVIEW_EMAIL"]

from scripts.preview_seed import LARGO_MINIMO_CLAVE, sembrar  # noqa: E402

_clave = os.environ.get("PREVIEW_PASSWORD", "")
if len(_clave) < LARGO_MINIMO_CLAVE:
    # La vista previa es pública: sin una clave propia no arranca.
    raise RuntimeError("PREVIEW_PASSWORD falta o es corta: la vista previa no arranca sin una clave propia")
sembrar(os.environ["DB_PATH"], os.environ["PREVIEW_EMAIL"], _clave)

from server import app  # noqa: E402,F401  (Vercel busca `app`)
