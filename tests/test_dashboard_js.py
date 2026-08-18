"""El JS embebido en dashboard.py tiene que compilar.

`dashboard.py` guarda el HTML, el CSS y el JS del CRM como strings de Python.
Eso tiene una trampa que ya mordio dos veces: un `\n` escrito con una sola
barra dentro de un string de Python se convierte en un salto de linea de verdad
antes de llegar al navegador, y parte en dos un string de JavaScript. La suite
de Python no ve nada: el modulo importa perfecto y el error aparece recien
cuando alguien abre el panel.

Este test extrae el JS y lo pasa por `node --check`. Si node no esta instalado
se saltea, para no romperle la suite a nadie por una dependencia que el
proyecto no tiene.
"""

import re
import shutil
import subprocess

import pytest

import dashboard


@pytest.mark.skipif(shutil.which("node") is None, reason="node no esta instalado")
def test_el_javascript_del_dashboard_compila(tmp_path):
    bloques = re.findall(r"<script>(.*?)</script>", dashboard.DASHBOARD_HTML, re.S)
    assert bloques, "no encontre ningun bloque <script> en el dashboard"

    archivo = tmp_path / "dashboard.js"
    archivo.write_text("\n".join(bloques), encoding="utf-8")

    r = subprocess.run(["node", "--check", str(archivo)],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"el JS del dashboard no compila:\n{r.stderr}"
