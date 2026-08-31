"""Chequea que el JavaScript embebido en dashboard.py parsee.

Existe porque `python -m py_compile dashboard.py` pasa aunque el JS este roto:
el frontend vive dentro de strings de Python normales (no raw), asi que un
backslash mal puesto lo consume Python como escape y al navegador le llega
codigo invalido. Eso ya tiro abajo un panel entero sin que fallara ni un test.

Uso:  python scripts/check_js.py
Sale con codigo 1 si algun bloque <script> no parsea.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Bloques <script> propios (sin src=). Los externos no los tenemos para chequear.
_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)


def _constantes_str(archivo: Path) -> dict[str, str]:
    """Las constantes de modulo que son strings literales, sin importar el modulo.

    Se parsea el AST en vez de importar: importar dashboard.py levanta Flask,
    pide variables de entorno y toca la base.
    """
    arbol = ast.parse(archivo.read_text(encoding="utf-8-sig"), filename=str(archivo))
    encontradas: dict[str, str] = {}
    for nodo in arbol.body:
        if not isinstance(nodo, ast.Assign):
            continue
        try:
            valor = ast.literal_eval(nodo.value)
        except (ValueError, SyntaxError):
            continue
        if not isinstance(valor, str):
            continue
        for destino in nodo.targets:
            if isinstance(destino, ast.Name):
                encontradas[destino.id] = valor
    return encontradas


def _revisar(nombre: str, js: str, tmp: Path) -> str | None:
    """Devuelve el error de node, o None si parsea."""
    f = tmp / f"{nombre}.js"
    f.write_text(js, encoding="utf-8")
    r = subprocess.run(["node", "--check", str(f)],
                       capture_output=True, text=True)
    return None if r.returncode == 0 else (r.stderr or r.stdout).strip()


def main() -> int:
    if not shutil.which("node"):
        print("check_js: node no esta instalado, no se puede chequear el JS")
        return 1

    errores = 0
    bloques = 0
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        for archivo in sorted(RAIZ.glob("*.py")) + sorted(RAIZ.glob("routes/*.py")):
            for const, texto in _constantes_str(archivo).items():
                if "<script" not in texto:
                    continue
                for n, js in enumerate(_SCRIPT.findall(texto)):
                    if not js.strip():
                        continue
                    bloques += 1
                    etiqueta = f"{archivo.name}:{const}:{n}"
                    error = _revisar(etiqueta.replace(":", "_"), js, tmp)
                    if error:
                        errores += 1
                        print(f"FALLA  {etiqueta}")
                        print("       " + error.replace("\n", "\n       "))

    if errores:
        print(f"\ncheck_js: {errores} de {bloques} bloques no parsean")
        return 1
    print(f"check_js: {bloques} bloques <script> OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
