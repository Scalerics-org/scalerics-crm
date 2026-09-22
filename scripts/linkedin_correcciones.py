"""Cliente de los pedidos de correccion de LinkedIn (lo usa Claude Code).

La sesion programada lee los pendientes, arma la version corregida y la
guarda. El token sale de LINKEDIN_BOT_TOKEN o de
%APPDATA%/Scalerics/linkedin_bot_token.txt (lo crea "Activar correcciones
LinkedIn.bat") y nunca se imprime.

Uso:
    python scripts/linkedin_correcciones.py pendientes
    python scripts/linkedin_correcciones.py resolver <id> <archivo.json>
        el JSON: {"texto": "...", "frase": "...", "respuesta": "..."}
        "frase" es opcional: si no viene, se saca de la primera oracion del
        texto corregido. La tarjeta se dibuja en la proxima corrida del cron
        (o un workflow_dispatch a mano de .github/workflows/linkedin.yml),
        no al toque: no hay Chromium en Fly.
    python scripts/linkedin_correcciones.py rechazar <id> "motivo"
"""

import json
import os
import sys

import requests

BASE = os.environ.get("CRM_URL", "https://scalerics-crm.fly.dev").rstrip("/")


def _token() -> str:
    token = os.environ.get("LINKEDIN_BOT_TOKEN", "").strip()
    if not token:
        ruta = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                            "Scalerics", "linkedin_bot_token.txt")
        if os.path.isfile(ruta):
            token = open(ruta, encoding="utf-8").read().strip()
    if not token:
        sys.exit("Falta el token: correr 'Activar correcciones LinkedIn.bat' del Escritorio.")
    return token


def _pedir(metodo, ruta, **kw):
    r = requests.request(metodo, BASE + ruta, headers={"x-linkedin-token": _token()}, timeout=60, **kw)
    datos = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not r.ok:
        sys.exit(f"Error {r.status_code}: {datos.get('error')}")
    return datos


def main(args):
    if not args:
        sys.exit(__doc__)
    cmd = args[0]
    if cmd == "pendientes":
        print(json.dumps(_pedir("GET", "/api/linkedin-bot/pendientes")["pendientes"],
                         ensure_ascii=False, indent=2))
    elif cmd == "resolver" and len(args) == 3:
        cuerpo = json.load(open(args[2], encoding="utf-8"))
        print(json.dumps(_pedir("POST", f"/api/linkedin-bot/correcciones/{int(args[1])}/resolver",
                                json=cuerpo), ensure_ascii=False))
    elif cmd == "rechazar" and len(args) == 3:
        _pedir("POST", f"/api/linkedin-bot/correcciones/{int(args[1])}/rechazar",
               json={"respuesta": args[2]})
        print("ok")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
