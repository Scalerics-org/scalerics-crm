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

15/10: la cola se vaciaba igual (traía 5 a 20 mails por día contra 25+ que se
mandan). Ahora el workflow corre dos veces por día y en `PARTES` máquinas a la
vez: cada una hace sus búsquedas (`--parte`) y busca el mail solo de su parte
de la lista, así no abren dos la misma web.
"""

import argparse
import logging
import os
import sys
import tempfile
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.email_finder import abrir_con_playwright, buscar_mail_del_sitio  # noqa: E402
from services.fidelidad import busquedas_automaticas  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("fid_captacion")

DIAS_MINIMOS = 14
BUSQUEDAS_POR_CORRIDA = 8  # por parte: 3 partes × 2 corridas = 48 búsquedas por día
MAX_POR_BUSQUEDA = 30  # 20 traía pocos locales nuevos en Montevideo (15/10)
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


def buscar_mails(limite: int, parte: int = 0, partes: int = 1) -> dict:
    """Paso 1 y 3: el mail de los que tienen web. Manda los resultados de a
    `_POR_ENVIO`, así un corte a la mitad no pierde todo lo buscado."""
    from playwright.sync_api import sync_playwright
    webs = _api("GET", f"/api/fidelidad/mails-auto/webs?limite={int(limite)}&parte={parte}&partes={partes}")
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


def turno_actual() -> int:
    """Un número distinto por corrida, así dos corridas no repiten búsquedas.
    En Actions es el número de corrida y no la hora: GitHub atrasa los cron
    hasta 5 horas (el de las 7:30 UTC salió a las 12:43 el 15/10)."""
    numero = os.environ.get("GITHUB_RUN_NUMBER", "")
    return int(numero) if numero.isdigit() else int(time.time() // (12 * 3600))


def traer_restaurantes(cuantas: int, turno: int, parte: int = 0, partes: int = 1) -> int:
    """Paso 2: búsquedas nuevas en Maps. El scraper, con CRM_URL y ADMIN_TOKEN,
    manda cada restaurante al CRM (que descarta los repetidos). Cada parte del
    mismo turno toma su propio tramo de la lista."""
    from scraper import run
    todas = busquedas_automaticas()
    desde = ((turno * partes + parte) * cuantas) % len(todas)
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
    ap.add_argument("--parte", type=int, default=0, help="Qué parte es esta máquina (desde 0)")
    ap.add_argument("--partes", type=int, default=1, help="Cuántas máquinas corren a la vez")
    args = ap.parse_args()
    parte, partes = args.parte, max(1, args.partes)

    buscar_mails(WEBS_POR_CORRIDA, parte, partes)
    estado = _api("GET", "/api/fidelidad/mails-auto")
    logger.info(f"Cola: {estado.get('sin_contactar_con_mail')} con mail, "
                f"{estado.get('dias_de_cola')} días, {estado.get('webs_sin_buscar')} webs sin buscar")
    if args.forzar_busqueda or estado.get("dias_de_cola", 0) < DIAS_MINIMOS:
        nuevos = traer_restaurantes(BUSQUEDAS_POR_CORRIDA, turno_actual(), parte, partes)
        logger.info(f"Restaurantes nuevos: {nuevos}")
        buscar_mails(WEBS_POR_CORRIDA, parte, partes)
    else:
        logger.info("Sobra cola: hoy no se buscan restaurantes nuevos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
