"""Busca la direccion de contacto de un comercio en su propio sitio web.

El nucleo es puro a proposito -HTML entra, direcciones salen- porque la parte
que decide si una direccion sirve es la que puede hacer dano: mandarle a una
direccion de ejemplo que quedo en la plantilla de la web es reputacion
quemada sin ninguna chance de venta.
"""

import logging
import random
import re
import time
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

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
# La comilla es opcional a proposito: `href=mailto:info@x.uy` sin comillas es
# HTML valido y hoy se perdia la prioridad del mailto sobre el texto suelto.
_RE_MAILTO = re.compile(r'href\s*=\s*["\']?mailto:([^"\'?>]+)', re.I)

# Extensiones de archivo que ningun TLD real usa. El markup de cualquier tema
# de WordPress o Wix produce nombres con arroba -"hero@3x.avif", "icons@2.woff"-
# porque el convenio retina @2x choca con la sintaxis de mail.
_EXT_NO_TLD = {
    "png", "jpg", "jpeg", "gif", "webp", "avif", "svg", "ico", "bmp", "tif",
    "tiff", "woff", "woff2", "ttf", "otf", "eot", "css", "js", "mjs", "json",
    "map", "mp4", "webm", "mp3", "pdf", "zip", "min", "html", "htm", "php",
}

# Primera etiqueta del dominio cuando en realidad es un sufijo de asset:
# "hero@3x.avif" -> "3x", "app@2.min.css" -> "2". La lista de extensiones no
# puede ser exhaustiva (jxl, heic, woff3...), asi que este es el corte general.
_RE_SUFIJO_ASSET = re.compile(r"\d+(\.\d+)*x?\Z")


def es_mail_basura(mail: str) -> bool:
    """True si la direccion no sirve para escribirle a un comercio."""
    mail = (mail or "").strip().lower()
    if not mail or "@" not in mail:
        return True
    local, _, dominio = mail.partition("@")
    if local in _LOCALES_BASURA:
        return True

    # Un nombre de archivo, no una direccion: el "TLD" es una extension.
    if dominio.rsplit(".", 1)[-1] in _EXT_NO_TLD:
        return True
    if _RE_SUFIJO_ASSET.match(dominio.split(".", 1)[0]):
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
    # Lo capturado en un mailto es texto crudo: puede traer varios destinatarios
    # separados por coma y la arroba escrita como %40. Se normaliza y se valida
    # contra _RE_MAIL en vez de guardarse tal cual.
    candidatas: list[str] = []
    for bruto in _RE_MAILTO.findall(html):
        for parte in re.split(r"[,;]", unquote(bruto)):
            candidatas.extend(_RE_MAIL.findall(parte.strip()))
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

# La marca que deja una fila que no abrio. Se busca por texto en la base para
# saber cuantos se estan perdiendo, y es lo que manda el reintento al fondo.
_MARCA_NO_ABRIO = "no abrio: ninguna pagina del sitio respondio"

# Cuantos sitios seguidos sin abrir hacen falta para dar por muerto el browser.
_MAX_SIN_ABRIR_SEGUIDOS = 10

# En que paginas buscar, en orden. La home primero porque muchos comercios
# chicos ponen el mail en el pie de todas las paginas.
RUTAS_CONTACTO = ["", "/contacto", "/contacto.html", "/contactenos", "/contact",
                  "/contactanos", "/es/contacto", "/nosotros", "/quienes-somos"]


def buscar_mail_del_sitio(abrir, website: str) -> tuple[str | None, bool]:
    """Devuelve (primera direccion buena del sitio o None, abrio alguna pagina).

    El segundo valor es lo que separa "el sitio no publica direccion" de "no se
    pudo mirar": un sitio que no abrio no dio evidencia de nada, y marcarlo como
    resuelto lo quema para siempre. Quien llama decide que hacer con eso.

    `abrir` es una funcion (url) -> html | None. Se pasa por parametro para que
    esto se pueda probar sin browser: en produccion la arma
    `abrir_con_playwright`, en los tests es un diccionario.
    """
    if not website:
        return None, False
    base = website.strip()
    if not re.match(r"^https?://", base, re.I):
        base = "https://" + base
    partes = urlsplit(base)
    esquema = partes.scheme.lower()
    # La home se visita tal cual vino, con su query: el campo "sitio web" de
    # una ficha de Google My Business muy seguido trae ?utm_source=gmb porque
    # el dueno lo pego asi. Las rutas de contacto cuelgan del origen, no de esa
    # URL: concatenarlas daba .../?utm_source=gmb/contacto, que es un 404.
    home = urlunsplit((esquema, partes.netloc, partes.path, partes.query, ""))
    origen = urlunsplit((esquema, partes.netloc, "", "", ""))

    abrio_alguna = False
    for ruta in RUTAS_CONTACTO:
        html = abrir(home if ruta == "" else urljoin(origen + "/", ruta.lstrip("/")))
        if not html:
            continue
        abrio_alguna = True
        mails = extraer_mails(html)
        if mails:
            return mails[0], True
    return None, abrio_alguna


def procesar_pendientes(db_path: str, abrir, limite: int = 50) -> dict:
    """Busca el mail de los negocios de discovery que todavia no lo tienen.

    Un sitio que ya se visito queda marcado (`email_found` o `no_email`) y no
    se vuelve a visitar: si no publica direccion, insistir cuesta minutos y no
    cambia el resultado. Solo mira `source = 'discovery'`: el padron sin web
    (`source` NULL) y los leads de Meta (`source = 'meta'`) no son asunto de
    este job.

    El que no abrio si se reintenta, pero al fondo de la cola: entra en la
    tanda despues de las filas que nunca se miraron. Sin eso, un par de
    dominios caidos con id bajo se come el LIMIT de todas las corridas y las
    filas nuevas no se miran nunca.

    Entre los reintentos el turno lo da la fecha del ultimo intento, no el id:
    round-robin. Si fuera un booleano, una tanda entera fallida -browser o red
    caidos desde el primer sitio- marcaria todo por igual y el orden degeneraria
    otra vez a `id`, con las caidas de id bajo comiendose el LIMIT para siempre.
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
             ORDER BY CASE WHEN COALESCE(TRIM(error_message), '') = ''
                           THEN 0 ELSE 1 END, error_message, id
             LIMIT ?
            """,
            # En SQLite LIMIT -1 significa SIN limite: un `--limite -1` de dedo
            # gordo procesaria la cohorte entera.
            (max(0, int(limite)),),
        ).fetchall()
    finally:
        conn.close()

    res = {"revisados": 0, "con_mail": 0, "sin_mail": 0, "no_abrio": 0}
    sin_abrir_seguidos = 0
    alguna_abrio = False
    for fila in filas:
        res["revisados"] += 1
        error = ""
        try:
            mail, abrio = buscar_mail_del_sitio(abrir, fila["website"])
        except Exception as e:
            # Un padron raspado tiene sitios que hacen cosas raras (redirects
            # rotos, certificados vencidos, etc). Que uno se caiga no puede
            # cortar la tanda entera, y tampoco cuenta como "no publica mail".
            error = f"{type(e).__name__}: {e}"
            logger.warning(f"[{fila['id']}] {fila['website']} -> excepcion: {error}")
            mail, abrio = None, False

        if mail:
            update_business(db_path, fila["id"], email=mail, status="email_found")
            res["con_mail"] += 1
            sin_abrir_seguidos = 0
            alguna_abrio = True
            logger.info(f"[{fila['id']}] {fila['website']} -> {mail}")
        elif abrio:
            update_business(db_path, fila["id"], status="no_email")
            res["sin_mail"] += 1
            sin_abrir_seguidos = 0
            alguna_abrio = True
            logger.info(f"[{fila['id']}] {fila['website']} -> sin mail")
        else:
            # No abrio ninguna pagina: el sitio puede estar caido un rato o se
            # puede haber muerto el browser de la tanda. No se marca el status,
            # asi la fila vuelve a salir en la proxima corrida.
            #
            # La marca en `error_message` va SIEMPRE, no solo en el camino de
            # excepcion: `abrir_con_playwright` se traga todo y devuelve None,
            # asi que en produccion la excepcion casi nunca llega hasta aca. Sin
            # la marca la perdida es invisible y, peor, el ORDER BY de la query
            # no puede mandar el reintento al fondo de la cola.
            # El sello de fecha adelante es lo que convierte la marca en una
            # recencia en vez de un booleano: es la clave por la que ordena el
            # ORDER BY de arriba. Sin el, una tanda entera fallida marca todo y
            # la cola vuelve a ordenarse por id, que es de donde no se sale.
            sello = time.strftime("%Y-%m-%d %H:%M:%S")
            update_business(db_path, fila["id"],
                            error_message=f"{sello} {(error or _MARCA_NO_ABRIO)}"[:500])
            res["no_abrio"] += 1
            sin_abrir_seguidos += 1
            logger.warning(f"[{fila['id']}] {fila['website']} -> no abrio, se reintenta")
            if sin_abrir_seguidos >= _MAX_SIN_ABRIR_SEGUIDOS and alguna_abrio:
                # Diez seguidos sin abrir no es Uruguay sin internet: es la page
                # compartida o el browser que se murieron. Seguir solo gasta la
                # tanda entera contra un browser muerto.
                #
                # `alguna_abrio` es lo que separa las dos lecturas: si en esta
                # tanda todavia no abrio ningun sitio, no hay evidencia de que el
                # browser haya estado vivo, y lo mas probable es que sean diez
                # dominios caidos seguidos -que es justo lo que queda pendiente
                # despues de la primera pasada-. Cortar ahi trababa el job para
                # siempre y encima logueaba una alarma falsa.
                logger.error(
                    f"{sin_abrir_seguidos} sitios seguidos sin abrir despues de uno "
                    "que si abrio: se corta la tanda (browser caido?). Las filas "
                    "que faltan quedan sin tocar."
                )
                break

    logger.info(f"Busqueda de mails: {res}")
    return res


def abrir_con_playwright(page):
    """Arma el `abrir` que usa produccion, contra una page de Playwright."""
    def abrir(url):
        # Una pausa corta antes de cada goto: un sitio chico puede recibir hasta
        # nueve pedidos seguidos del mismo cliente, y el scraper ya se toma este
        # respiro contra Maps.
        time.sleep(random.uniform(1.0, 2.5))
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            return page.content()
        except Exception as e:
            logger.debug(f"No se pudo abrir {url}: {type(e).__name__}")
            return None
    return abrir
