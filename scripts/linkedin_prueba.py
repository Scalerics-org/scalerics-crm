"""Prueba a mano de la API de LinkedIn, desde la notebook.

    python scripts/linkedin_prueba.py chequear
        Dice si el token anda y en que paginas sos admin. No publica nada.
    python scripts/linkedin_prueba.py mostrar N [carpeta]
        Guarda la tarjeta del post N del banco y muestra el texto. No publica.
    python scripts/linkedin_prueba.py publicar N
        Publica el post N del banco, con su tarjeta, en la pagina de Scalerics.

El token lo carga "Cargar token LinkedIn.bat" en %APPDATA%\\Scalerics\\linkedin_token.txt.
Nunca se imprime.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.linkedin_api import Cliente, url_del_post  # noqa: E402
from services.linkedin_banco_semilla import LINKEDIN_BANCO_SEMILLA  # noqa: E402

TOKEN = Path(os.environ.get("APPDATA", "")) / "Scalerics" / "linkedin_token.txt"


def _cliente() -> Cliente:
    if not TOKEN.exists():
        sys.exit(f"No esta el token en {TOKEN}. Correr 'Cargar token LinkedIn.bat'.")
    return Cliente(TOKEN.read_text(encoding="utf-8-sig").strip())


def _pagina(cli: Cliente) -> str:
    paginas = cli.paginas_admin()
    if not paginas:
        sys.exit("El token anda pero no figura como admin de ninguna pagina.")
    if os.environ.get("LINKEDIN_ORG"):
        return os.environ["LINKEDIN_ORG"]
    if len(paginas) > 1:
        sys.exit(f"Admin de varias paginas: {paginas}. Elegir con LINKEDIN_ORG.")
    return paginas[0]


def main() -> None:
    accion = sys.argv[1] if len(sys.argv) > 1 else "chequear"
    if accion == "chequear":
        cli = _cliente()
        for urn in cli.paginas_admin():
            print(f"admin de: {cli.nombre_pagina(urn)} ({urn})")
        print(f"version de la API: {cli.version}")
        return

    n = int(sys.argv[2])
    tema, _angulo, texto, frase = LINKEDIN_BANCO_SEMILLA[n]
    from services.linkedin_render import dibujar
    png = dibujar(frase)

    if accion == "mostrar":
        carpeta = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(".")
        destino = carpeta / f"linkedin_prueba_{n}.png"
        destino.write_bytes(png)
        print(f"tema: {tema}\nimagen: {destino}\n\n{texto}")
        return

    if accion == "publicar":
        cli = _cliente()
        org = _pagina(cli)
        imagen = cli.subir_imagen(org, png)
        post = cli.publicar(org, texto, imagen, alt=frase)
        print(f"publicado: {url_del_post(post)}")
        return

    sys.exit(__doc__)


if __name__ == "__main__":
    main()
