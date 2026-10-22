"""Cliente de los recordatorios de Bruno (lo usa la tarea programada de Claude Code).

La tarea lee los chats nuevos del WhatsApp de Juan, carga los recordatorios y
arma los mensajes que Juan pidió con "Armame el mensaje". El token sale
de OFICINA_BOT_TOKEN o de %APPDATA%/Scalerics/oficina_bot_token.txt (lo crea
"Activar Oficina.bat") y nunca se imprime.

Uso:
    python scripts/oficina_responder.py recordatorios
        los abiertos y los que esperan que Bruno arme el mensaje
    python scripts/oficina_responder.py crear-recordatorio <archivo.json>
        el JSON: {"que": "...", "cuando": "AAAA-MM-DD", "contacto": "...",
                  "cita": "frase del chat", "clave": "algo-unico-para-no-repetir"}
    python scripts/oficina_responder.py borrador <id> <archivo.json>
        el JSON: {"texto": "el mensaje listo para que Juan lo mande"}
    python scripts/oficina_responder.py hecho <id>
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
    if cmd == "recordatorios":
        datos = _pedir("GET", "/api/oficina-bot/recordatorios")
        datos.pop("ok", None)
        print(json.dumps(datos, ensure_ascii=False, indent=2))
    elif cmd == "crear-recordatorio" and len(args) == 2:
        cuerpo = json.load(open(args[1], encoding="utf-8"))
        print(json.dumps(_pedir("POST", "/api/oficina-bot/recordatorios", json=cuerpo), ensure_ascii=False))
    elif cmd == "borrador" and len(args) == 3:
        cuerpo = json.load(open(args[2], encoding="utf-8"))
        _pedir("POST", f"/api/oficina-bot/recordatorios/{int(args[1])}/borrador", json=cuerpo)
        print("ok")
    elif cmd == "hecho" and len(args) == 2:
        _pedir("POST", f"/api/oficina-bot/recordatorios/{int(args[1])}/hecho")
        print("ok")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
