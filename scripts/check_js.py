"""Valida el JavaScript embebido en dashboard.py.

Todo el frontend vive dentro de strings de Python (DASHBOARD_HTML, ADMIN_PAGE),
asi que ni py_compile ni pytest lo miran: se puede romper el JS y tener el CI
entero en verde con el panel muerto.

Paso real que motivo este script: jsStr() emitia '\\x' en el JS renderizado, un
escape hexadecimal incompleto. El navegador tiraba SyntaxError y no cargaba el
bloque <script> completo. Python compilaba y los 70 tests pasaban.

La trampa de fondo es que los strings NO son raw, asi que un backslash hay que
duplicarlo para Python y volver a duplicarlo para JavaScript.

Uso:
    python scripts/check_js.py
"""

import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SECRET_KEY", "check-js")
os.environ.setdefault("ANTHROPIC_API_KEY", "check-js")

import dashboard  # noqa: E402


def _bloques_js(html: str) -> list:
    return [b for b in re.findall(r"<script>(.*?)</script>", html, re.S) if b.strip()]


def _validar(nombre: str, js: str) -> bool:
    ruta = os.path.join(tempfile.mkdtemp(), "bloque.js")
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(js)
    r = subprocess.run(["node", "--check", ruta], capture_output=True, text=True)
    if r.returncode == 0:
        print(f"  OK    {nombre} ({len(js):,} chars)")
        return True
    print(f"  ERROR {nombre}:\n{r.stderr[:1200]}")
    return False


def main() -> int:
    try:
        subprocess.run(["node", "--version"], capture_output=True, check=True)
    except Exception:
        print("node no esta disponible: no se puede validar el JS")
        return 1

    fallos = 0

    for i, js in enumerate(_bloques_js(dashboard.DASHBOARD_HTML)):
        if not _validar(f"DASHBOARD_HTML bloque {i}", js):
            fallos += 1

    # ADMIN_PAGE lleva Jinja ({{ all_panels | tojson }}), asi que hay que
    # renderizarla antes de que sea JavaScript valido.
    from flask import Flask
    app = Flask(__name__)
    with app.app_context():
        from flask import render_template_string
        try:
            admin = re.search(r'ADMIN_PAGE = """(.*?)"""', open(
                os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "dashboard.py"), encoding="utf-8").read(), re.S)
            if admin:
                with app.test_request_context("/"):
                    render = render_template_string(
                        admin.group(1), users=[], all_panels=["cola"])
                for i, js in enumerate(_bloques_js(render)):
                    if not _validar(f"ADMIN_PAGE bloque {i}", js):
                        fallos += 1
        except Exception as e:
            print(f"  AVISO no se pudo validar ADMIN_PAGE: {e}")

    # Un backslash-x suelto (no duplicado) es un escape invalido en JavaScript.
    bs = chr(92)
    crudo = dashboard.DASHBOARD_HTML.replace(bs + bs, "")
    if re.search(re.escape(bs) + r"x(?![0-9a-fA-F]{2})", crudo):
        print("  ERROR queda un escape hexadecimal incompleto en el JS")
        fallos += 1
    else:
        print("  OK    sin escapes hexadecimales incompletos")

    print("\n" + ("JS OK" if not fallos else f"{fallos} bloque(s) con problemas"))
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
