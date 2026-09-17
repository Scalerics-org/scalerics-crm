"""Cliente de los pedidos de correccion de Instagram (lo usa Claude Code).

La sesion programada lee los pendientes, arma la version corregida y la guarda.
El token sale de IG_BOT_TOKEN o de %APPDATA%/Scalerics/ig_bot_token.txt (lo
crea "Activar correcciones Instagram.bat") y nunca se imprime.

Uso:
    python scripts/ig_correcciones.py pendientes
    python scripts/ig_correcciones.py resolver <id> <archivo.json>
        el JSON: {"cambios": {"slides": [...], "caption": "..."}, "respuesta": "..."}
    python scripts/ig_correcciones.py rechazar <id> "motivo"
    python scripts/ig_correcciones.py imagen <publicacion_id> <n> <destino.jpg>
"""

import json
import os
import sys

import requests

BASE = os.environ.get("CRM_URL", "https://scalerics-crm.fly.dev").rstrip("/")


def _token() -> str:
    token = os.environ.get("IG_BOT_TOKEN", "").strip()
    if not token:
        ruta = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                            "Scalerics", "ig_bot_token.txt")
        if os.path.isfile(ruta):
            token = open(ruta, encoding="utf-8").read().strip()
    if not token:
        sys.exit("Falta el token: correr 'Activar correcciones Instagram.bat' del Escritorio.")
    return token


def _pedir(metodo, ruta, **kw):
    r = requests.request(metodo, BASE + ruta, headers={"x-ig-token": _token()}, timeout=60, **kw)
    if r.headers.get("content-type", "").startswith("application/json"):
        datos = r.json()
        if not r.ok:
            sys.exit(f"Error {r.status_code}: {datos.get('error')}")
        return datos
    if not r.ok:
        sys.exit(f"Error {r.status_code}")
    return r


def main(args):
    if not args:
        sys.exit(__doc__)
    cmd = args[0]
    if cmd == "pendientes":
        print(json.dumps(_pedir("GET", "/api/instagram-bot/pendientes")["pendientes"],
                         ensure_ascii=False, indent=2))
    elif cmd == "resolver" and len(args) == 3:
        cuerpo = json.load(open(args[2], encoding="utf-8"))
        print(json.dumps(_pedir("POST", f"/api/instagram-bot/correcciones/{int(args[1])}/resolver",
                                json=cuerpo), ensure_ascii=False))
    elif cmd == "rechazar" and len(args) == 3:
        _pedir("POST", f"/api/instagram-bot/correcciones/{int(args[1])}/rechazar",
               json={"respuesta": args[2]})
        print("ok")
    elif cmd == "imagen" and len(args) == 4:
        r = _pedir("GET", f"/api/instagram-bot/publicaciones/{int(args[1])}/imagen/{int(args[2])}")
        with open(args[3], "wb") as f:
            f.write(r.content)
        print(args[3])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
