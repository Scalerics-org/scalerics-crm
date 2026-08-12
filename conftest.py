"""Config global de pytest.

Los tests necesitan que ciertas variables existan antes de importar los modulos
de la app: dashboard.create_app() ahora falla ruidoso sin SECRET_KEY (antes caia
en un fallback hardcodeado que estaba en el repo), y main.py exige
ANTHROPIC_API_KEY para el CLI.
"""

import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("SECRET_KEY", "test-secret-key-solo-para-tests")
os.environ.setdefault("ALLOW_INSECURE_DEV_KEY", "1")
