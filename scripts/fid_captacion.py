"""La captación de Fidelidad que corre sola en GitHub Actions, todos los días.

Juan (30/9): los mails a restaurantes «no pueden parar». Si se acaban los
restaurantes con mail, hay que traer más. Fly no tiene navegador, así que esto
corre en GitHub Actions (.github/workflows/fidelidad-captacion.yml) y habla con
el CRM por la API, con CRM_URL y ADMIN_TOKEN:

1. Busca el mail en la web de los restaurantes que tienen web y no mail.
2. Si quedan menos de `DIAS_MINIMOS` días de envíos, trae restaurantes nuevos
   de Google Maps: `BUSQUEDAS_POR_CORRIDA` búsquedas (tipo de local × barrio)
   que rotan día a día por `fidelidad.busquedas_automaticas()`, así cada
   corrida mira barrios distintos y en unas semanas se da la vuelta entera.
3. Les busca el mail a los que acaba de traer.

Nunca en la notebook de Juan (se le traba la compu): solo en Actions.
"""

import argparse
import logging
import os
import sys
import tempfile
import time
from datetime import date

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.email_finder import abrir_con_playwright, buscar_mail_del_sitio  # noqa: E402
from services.fidelidad import busquedas_automaticas  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("fid_captacion")

DIAS_MINIMOS = 14
BUSQUEDAS_POR_CORRIDA = 10
MAX_POR_BUSQUEDA = 20
WEBS_POR_CORRIDA = 200
_POR_ENVIO = 20


def _api(metodo: str, ruta: str, **kw):
    url = os.environ["CRM_URL"].rstrip("/") + ruta
    headers = {"x-admin-token": os.environ["ADMIN_TOKEN"]}
    for intento in range(4):
        try:
            r = requests.request(metodo, url, headers=headers, timeout=30, **kw)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            logger.warning(f"{metodo} {ruta} falló ({e}), intento {intento + 1}")
            time.sleep(15)
    raise RuntimeError(f"El CRM no contesta {metodo} {ruta}")


def buscar_mails(limite: int) -> dict:
    """Paso 1 y 3: el mail de los que tienen web. Manda los resultados de a
    `_POR_ENVIO`, así un corte a la mitad no pierde todo lo buscado."""
    from playwright.sync_api import sync_playwright
    webs = _api("GET", f"/api/fidelidad/mails-auto/webs?limite={int(limite)}")
    logger.info(f"{len(webs)} restaurantes con web para buscarles el mail")
    total = {"encontrados": 0, "sin_mail": 0, "no_abrio": 0}
    if not webs:
        return total
    lote = []

    def mandar():
        if lote:
            r = _api("POST", "/api/fidelidad/mails-auto/encontrados", json={"resultados": list(lote)})
            for k in total:
                total[k] += r.get(k, 0)
            lote.clear()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        abrir = abrir_con_playwright(page)
        for w in webs:
            try:
                mail, abrio = buscar_mail_del_sitio(abrir, w["web"])
            except Exception as e:
                logger.warning(f"[{w['id']}] {w['web']}: {type(e).__name__}")
                mail, abrio = None, False
            lote.append({"id": w["id"], "email": mail, "abrio": abrio})
            if len(lote) >= _POR_ENVIO:
                mandar()
        mandar()
        browser.close()
    logger.info(f"Mails: {total}")
    return total


def traer_restaurantes(cuantas: int, dia: int) -> int:
    """Paso 2: búsquedas nuevas en Maps. El scraper, con CRM_URL y ADMIN_TOKEN,
    manda cada restaurante al CRM (que descarta los repetidos)."""
    from scraper import run
    todas = busquedas_automaticas()
    desde = (dia * cuantas) % len(todas)
    elegidas = [todas[(desde + i) % len(todas)] for i in range(cuantas)]
    base = os.path.join(tempfile.mkdtemp(), "local.db")
    vistos: set[str] = set()
    nuevos = 0
    for b in elegidas:
        consulta = f"{b['tipo']} en {b['barrio']}, {b['depto']}"
        try:
            n = run(consulta, MAX_POR_BUSQUEDA, base, ya_vistos=vistos,
                    fidelidad={"barrio": b["barrio"], "ciudad": b["ciudad"], "rubro": "restaurante"})
        except Exception as e:
            logger.warning(f"[{consulta}] falló: {type(e).__name__}: {e}")
            continue
        logger.info(f"[{consulta}] {n} nuevos")
        nuevos += n
    return nuevos


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forzar-busqueda", action="store_true",
                    help="Traer restaurantes nuevos aunque sobre cola")
    args = ap.parse_args()

    buscar_mails(WEBS_POR_CORRIDA)
    estado = _api("GET", "/api/fidelidad/mails-auto")
    logger.info(f"Cola: {estado.get('sin_contactar_con_mail')} con mail, "
                f"{estado.get('dias_de_cola')} días, {estado.get('webs_sin_buscar')} webs sin buscar")
    if args.forzar_busqueda or estado.get("dias_de_cola", 0) < DIAS_MINIMOS:
        nuevos = traer_restaurantes(BUSQUEDAS_POR_CORRIDA, date.today().toordinal())
        logger.info(f"Restaurantes nuevos: {nuevos}")
        buscar_mails(WEBS_POR_CORRIDA)
    else:
        logger.info("Sobra cola: hoy no se buscan restaurantes nuevos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
