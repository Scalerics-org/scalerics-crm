"""Carga una clave de Anthropic en las apps de Fly, sin que la clave quede escrita.

Mismo motivo que `cargar_token_meta.py`: pegar una clave en una terminal la deja
en el historial del shell, y pegarla en un chat la deja en un transcript. Esta la
pide con `getpass` —no se ve, no va al historial— valida la forma y la manda.

Uso:

    python scripts/cargar_clave_anthropic.py                 # las dos apps
    python scripts/cargar_clave_anthropic.py --app scalerics-wa

POR QUE EXISTE
    Hasta el 11/9/2026 habia UNA sola clave de Anthropic repartida entre
    `crm-limpio`, `crm-jose` (la bloquera, que es de un cliente) y
    `respaldo-lead-gen-uy`, mas las dos apps de Fly. Con una clave compartida el
    gasto del panel de la consola es un solo numero y **no se puede saber quien
    gasto que**. Una clave por proyecto —idealmente cada una en su propio
    workspace, con limite de gasto— convierte ese numero en una respuesta.
"""

import argparse
import getpass
import re
import shutil
import subprocess

APPS_POR_DEFECTO = ("scalerics-crm", "scalerics-wa")

# Las claves de Anthropic arrancan con `sk-ant-` y son largas. Esto no valida que
# sirva —eso lo dice la API— pero atrapa el error mas comun: pegar de menos, o
# pegar con un salto de linea adentro.
_PREFIJO = "sk-ant-"
_LARGO_MINIMO = 40
_FORMA = re.compile(r"^[A-Za-z0-9_\-]+$")


def _flyctl():
    ruta = shutil.which("flyctl") or shutil.which("fly")
    if not ruta:
        print("No encontre flyctl en el PATH.")
        print("Instalarlo, o correr a mano:")
        print('  flyctl secrets set ANTHROPIC_API_KEY="..." -a <app>')
        return None
    return ruta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--app", action="append", dest="apps",
                    help="app de Fly (repetible). Por defecto: "
                         + ", ".join(APPS_POR_DEFECTO))
    args = ap.parse_args()
    apps = args.apps or list(APPS_POR_DEFECTO)

    fly = _flyctl()
    if not fly:
        return 1

    print("Pega la clave de Anthropic para: " + ", ".join(apps))
    print("No se va a ver mientras la pegas, y no queda en el historial.\n")

    clave = getpass.getpass("ANTHROPIC_API_KEY: ").strip()

    if not clave:
        print("\nNo pegaste nada.")
        return 1
    if not clave.startswith(_PREFIJO):
        print(f"\nEso no arranca con {_PREFIJO!r}. Copiala de nuevo, entera.")
        return 1
    if len(clave) < _LARGO_MINIMO:
        print(f"\nEsa clave tiene {len(clave)} caracteres y deberia tener mas de "
              f"{_LARGO_MINIMO}. Puede que se haya cortado al copiar.")
        return 1
    if not _FORMA.match(clave):
        print("\nEsa clave tiene caracteres raros —espacios, comillas o un salto "
              "de linea—. Copiala de nuevo, entera y sin comillas.")
        return 1

    print(f"\nClave de {len(clave)} caracteres, termina en ...{clave[-4:]}")
    print("Ojo: cargarla reinicia cada maquina (regla 3 de COORDINACION.md).\n")

    for app in apps:
        print(f"-> {app}")
        r = subprocess.run([fly, "secrets", "set", f"ANTHROPIC_API_KEY={clave}",
                            "-a", app], text=True)
        if r.returncode != 0:
            print(f"\nflyctl fallo en {app}. Las anteriores si se cargaron.")
            return 1

    print("\nListo.")
    print("Cuando las dos apps esten andando con la clave nueva, revoca la vieja")
    print("en console.anthropic.com. Mientras no la revoques, sigue habiendo un")
    print("numero de gasto que no se puede atribuir a nadie.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
