"""Cliente de las preguntas de la Oficina (lo usa Claude Code).

La sesión programada lee las preguntas que Juan les hizo a los agentes, con las
indicaciones y aprendizajes de cada uno, y guarda la respuesta. El token sale
de OFICINA_BOT_TOKEN o de %APPDATA%/Scalerics/oficina_bot_token.txt (lo crea
"Activar Oficina.bat") y nunca se imprime.

Uso:
    python scripts/oficina_responder.py pendientes
    python scripts/oficina_responder.py responder <id> <archivo.json>
        el JSON: {"texto": "...", "fuente": "de dónde salió el dato"}
"""

import json
import os
import sys

import requests

BASE = os.environ.get("CRM_URL", "https://scalerics-crm.fly.dev").rstrip("/")


def _token() -> str:
    token = os.environ.get("OFICINA_BOT_TOKEN", "").strip()
    if not token:
        ruta = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                            "Scalerics", "oficina_bot_token.txt")
        if os.path.isfile(ruta):
            token = open(ruta, encoding="utf-8").read().strip()
    if not token:
        sys.exit("Falta el token: correr 'Activar Oficina.bat' del Escritorio.")
    return token


def _pedir(metodo, ruta, **kw):
    r = requests.request(metodo, BASE + ruta, headers={"x-oficina-token": _token()}, timeout=60, **kw)
    datos = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not r.ok:
        sys.exit(f"Error {r.status_code}: {datos.get('error')}")
    return datos


def main(args):
    if not args:
        sys.exit(__doc__)
    cmd = args[0]
    if cmd == "pendientes":
        print(json.dumps(_pedir("GET", "/api/oficina-bot/pendientes")["pendientes"],
                         ensure_ascii=False, indent=2))
    elif cmd == "responder" and len(args) == 3:
        cuerpo = json.load(open(args[2], encoding="utf-8"))
        print(json.dumps(_pedir("POST", f"/api/oficina-bot/mensajes/{int(args[1])}/responder",
                                json=cuerpo), ensure_ascii=False))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
