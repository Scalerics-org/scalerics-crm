"""Carga el token de Insights de Meta en Fly, sin que el token quede escrito.

Existe por un motivo concreto: pegar un token sin vencimiento en una terminal
lo deja en el historial del shell, y pegarlo en un chat lo deja en un
transcript. Este script lo pide con `getpass` —no se ve mientras lo escribis, no
va al historial— lo valida, lo manda a Fly y despues verifica que funcione.

Uso:

    python scripts/cargar_token_meta.py

Requiere `flyctl` en el PATH y estar logueado. El id de la cuenta publicitaria
viene puesto; se puede pisar con --cuenta si algun dia cambia.

Los pasos previos —crear el usuario del sistema, asignarle la cuenta y la app,
agregarle el producto Marketing API— estan en
docs/puesta-en-produccion-marketing-meta.md y ya se hicieron el 10/9/2026.
"""

import argparse
import getpass
import re
import shutil
import subprocess
import sys

APP_FLY = "scalerics-crm"

# La cuenta publicitaria del portafolio Scalerics. Es un id, no un secreto.
CUENTA_POR_DEFECTO = "act_1165635198430883"

# Un token de usuario del sistema es largo y no tiene espacios. Este chequeo no
# valida que sea valido —eso lo dice la API— pero atrapa el error mas comun:
# pegar de menos, o pegar con un salto de linea adentro.
_LARGO_MINIMO = 60
_FORMA = re.compile(r"^[A-Za-z0-9_\-.]+$")


def _flyctl():
    ruta = shutil.which("flyctl") or shutil.which("fly")
    if not ruta:
        print("No encontre flyctl en el PATH.")
        print("Instalarlo o correr el comando a mano:")
        print(f'  flyctl secrets set META_ADS_TOKEN="..." '
              f'META_AD_ACCOUNT_ID="{CUENTA_POR_DEFECTO}" -a {APP_FLY}')
        return None
    return ruta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cuenta", default=CUENTA_POR_DEFECTO,
                    help=f"id de la cuenta publicitaria (default {CUENTA_POR_DEFECTO})")
    ap.add_argument("--app", default=APP_FLY, help=f"app de Fly (default {APP_FLY})")
    args = ap.parse_args()

    fly = _flyctl()
    if not fly:
        return 1

    print("Pega el token del usuario del sistema `crm-insights`.")
    print("No se va a ver mientras lo pegas, y no queda en el historial.\n")

    token = getpass.getpass("META_ADS_TOKEN: ").strip()

    if not token:
        print("\nNo pegaste nada.")
        return 1
    if len(token) < _LARGO_MINIMO:
        print(f"\nEse token tiene {len(token)} caracteres y deberia tener mas de "
              f"{_LARGO_MINIMO}. Puede que se haya cortado al copiar.")
        return 1
    if not _FORMA.match(token):
        print("\nEse texto tiene caracteres raros —espacios, comillas o un salto "
              "de linea—. Copialo de nuevo, entero y sin comillas.")
        return 1

    print(f"\nToken de {len(token)} caracteres. Cargandolo en `{args.app}`...")
    print("Ojo: esto reinicia la maquina (regla 3 de COORDINACION.md).\n")

    r = subprocess.run(
        [fly, "secrets", "set",
         f"META_ADS_TOKEN={token}",
         f"META_AD_ACCOUNT_ID={args.cuenta}",
         "-a", args.app],
        text=True)
    if r.returncode != 0:
        print("\nflyctl fallo. El token no se cargo.")
        return 1

    print("\nSecrets cargados.\n")
    print("Ahora, para saber con que action_type reporta los leads esta cuenta:")
    print("  python scripts/verificar_meta_insights.py")
    print()
    print("Y para traer la historia desde marzo de una sola vez:")
    print(f'  curl -X POST -H "x-admin-token: $ADMIN_TOKEN" \\')
    print(f'    "https://{args.app}.fly.dev/api/marketing/sync-insights?dias=200"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
