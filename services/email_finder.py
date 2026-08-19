"""Busca la direccion de contacto de un comercio en su propio sitio web.

El nucleo es puro a proposito -HTML entra, direcciones salen- porque la parte
que decide si una direccion sirve es la que puede hacer dano: mandarle a una
direccion de ejemplo que quedo en la plantilla de la web es reputacion
quemada sin ninguna chance de venta.
"""

import logging
import re

from database import _connect, update_business

logger = logging.getLogger(__name__)

# Local part que delata una direccion que nadie lee.
_LOCALES_BASURA = {
    "noreply", "no-reply", "donotreply", "usuario", "tuemail", "tucorreo",
    "ejemplo", "example",
}

# Dominios de plantilla y de proveedores, no del comercio.
_DOMINIOS_BASURA = {
    "dominio.com", "tudominio.com", "midominio.com", "sitio.com", "tusitio.com",
    "example.com", "example.org", "example.net", "dominio.com.uy",
    "sentry.io", "sentry.wixpress.com", "wix.com", "squarespace.com",
    "godaddy.com", "wordpress.com",
}

_RE_MAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RE_MAILTO = re.compile(r'href\s*=\s*["\']mailto:([^"\'?]+)', re.I)
_RE_IMAGEN = re.compile(r"\.(png|jpe?g|gif|webp|svg)$", re.I)


def es_mail_basura(mail: str) -> bool:
    """True si la direccion no sirve para escribirle a un comercio."""
    mail = (mail or "").strip().lower()
    if not mail or "@" not in mail:
        return True
    if _RE_IMAGEN.search(mail):
        return True

    local, _, dominio = mail.partition("@")
    if local in _LOCALES_BASURA:
        return True
    if dominio in _DOMINIOS_BASURA:
        return True
    if any(dominio.endswith("." + d) for d in _DOMINIOS_BASURA):
        return True
    return False


def extraer_mails(html: str) -> list[str]:
    """Direcciones buenas del HTML, las de `mailto:` primero y sin repetir.

    El orden importa: una direccion en un `mailto:` la puso el dueno del sitio
    para que le escriban. Una suelta en el texto puede ser la del contador, la
    del que hizo la web, o cualquier cosa.
    """
    html = html or ""
    candidatas = [m.strip() for m in _RE_MAILTO.findall(html)]
    candidatas += _RE_MAIL.findall(html)

    salida: list[str] = []
    vistas: set[str] = set()
    for mail in candidatas:
        clave = mail.lower()
        if clave in vistas or es_mail_basura(mail):
            continue
        vistas.add(clave)
        salida.append(mail)
    return salida


# ---------------------------------------------------------------------------
# A partir de aca: lo que toca la base y el browser. El nucleo de arriba
# (extraer_mails, es_mail_basura) no sabe que existe una base de datos ni un
# browser; esto es lo que los conecta.
# ---------------------------------------------------------------------------

# En que paginas buscar, en orden. La home primero porque muchos comercios
# chicos ponen el mail en el pie de todas las paginas.
RUTAS_CONTACTO = ["", "/contacto", "/contact", "/contactanos", "/nosotros"]


def buscar_mail_del_sitio(abrir, website: str) -> str | None:
    """Primera direccion buena del sitio, o None.

    `abrir` es una funcion (url) -> html | None. Se pasa por parametro para que
    esto se pueda probar sin browser: en produccion la arma
    `abrir_con_playwright`, en los tests es un diccionario.
    """
    if not website:
        return None
    base = website.strip()
    if not base.startswith("http"):
        base = "https://" + base
    base = base.rstrip("/")

    for ruta in RUTAS_CONTACTO:
        html = abrir(base + ruta)
        if not html:
            continue
        mails = extraer_mails(html)
        if mails:
            return mails[0]
    return None


def procesar_pendientes(db_path: str, abrir, limite: int = 50) -> dict:
    """Busca el mail de los negocios de discovery que todavia no lo tienen.

    Un sitio que ya se visito queda marcado (`email_found` o `no_email`) y no
    se vuelve a visitar: si no publica direccion, insistir cuesta minutos y no
    cambia el resultado. Solo mira `source = 'discovery'`: el padron sin web
    (`source` NULL) y los leads de Meta (`source = 'meta'`) no son asunto de
    este job.
    """
    conn = _connect(db_path)
    try:
        filas = conn.execute(
            """
            SELECT id, website FROM businesses
             WHERE source = 'discovery'
               AND website IS NOT NULL AND TRIM(website) <> ''
               AND (email IS NULL OR TRIM(email) = '')
               AND COALESCE(status, '') NOT IN ('email_found', 'no_email')
             ORDER BY id
             LIMIT ?
            """,
            (int(limite),),
        ).fetchall()
    finally:
        conn.close()

    res = {"revisados": 0, "con_mail": 0, "sin_mail": 0}
    for fila in filas:
        res["revisados"] += 1
        try:
            mail = buscar_mail_del_sitio(abrir, fila["website"])
        except Exception as e:
            # Un padron raspado tiene sitios que hacen cosas raras (redirects
            # rotos, certificados vencidos, etc). Que uno se caiga no puede
            # cortar la tanda entera.
            logger.warning(f"[{fila['id']}] {fila['website']} -> excepcion: {type(e).__name__}: {e}")
            mail = None
        if mail:
            update_business(db_path, fila["id"], email=mail, status="email_found")
            res["con_mail"] += 1
            logger.info(f"[{fila['id']}] {fila['website']} -> {mail}")
        else:
            update_business(db_path, fila["id"], status="no_email")
            res["sin_mail"] += 1
            logger.info(f"[{fila['id']}] {fila['website']} -> sin mail")

    logger.info(f"Busqueda de mails: {res}")
    return res


def abrir_con_playwright(page):
    """Arma el `abrir` que usa produccion, contra una page de Playwright."""
    def abrir(url):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            return page.content()
        except Exception as e:
            logger.debug(f"No se pudo abrir {url}: {type(e).__name__}")
            return None
    return abrir
