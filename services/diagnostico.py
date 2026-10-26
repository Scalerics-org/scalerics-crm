"""Diagnóstico web gratuito (26/10).

Un comercio pega la dirección de su sitio y en unos segundos recibe un informe
con puntaje y qué le hace perder clientes: si se ve en el celular, si tiene
WhatsApp a un toque, si Google lo entiende, si carga rápido, si dice "No
seguro". Es el "Website Grader" de HubSpot llevado a lo que le importa a un
comercio de acá. Cada diagnóstico queda en el panel de VENTAS con lo que hay
que hablar en la llamada.

Tres partes:

- `obtener(url)`: baja la página. Es lo único que toca la red. Un endpoint
  público que hace pedidos a la dirección que le escriben es la receta de un
  SSRF, así que: solo http/https en los puertos de siempre, sin usuario en la
  URL, el dominio se resuelve ANTES y todas sus IP tienen que ser públicas, y
  la conexión va a esa IP ya validada (no se vuelve a resolver: así no hay
  DNS rebinding). Cada redirección se valida igual. Tope de bytes y de tiempo.
- `analizar(pagina)`: puro, sin red. Lee el HTML y arma los hallazgos con su
  peso. Los tests y la vista previa lo usan con HTML inventado.
- La base: `diagnosticos`, una fila por pedido, con el resultado en JSON.

Las filas de la web entran como lead con `source='web_diagnostico'`: ninguna
campaña automática filtra por ese origen, así que nadie recibe un mail solo
por haber pedido el diagnóstico (lo mismo que `web_guia`, ver routes/web.py).
"""

from __future__ import annotations

import ipaddress
import json
import re
import secrets
import socket
import sqlite3
import ssl
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import urllib3

MVD = ZoneInfo("America/Montevideo")

UA = "Mozilla/5.0 (compatible; ScalericsDiagnostico/1.0; +https://scalerics.com)"
MAX_BYTES = 3_000_000
MAX_REDIRECCIONES = 5
TIMEOUT_PAGINA = urllib3.Timeout(connect=5, read=10)
TIMEOUT_EXTRA = urllib3.Timeout(connect=3, read=4)

# Topes de la página pública: por IP y por hora, y del día para todo el CRM.
TOPE_POR_IP = 4
TOPE_DIA = 150
# Un dominio que se diagnosticó hace menos de esto se responde con lo que ya
# había: nadie usa el CRM para martillar un sitio ajeno.
REUSO = timedelta(hours=1)

SEGUIMIENTO = ("nuevo", "hablado", "propuesta", "descartado")
# Un hallazgo "mal" con este peso o más es de los que hacen perder clientes solo.
CRITICO = 10

_MAIL = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")
_HOST = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$")

# A qué puerto se conecta cada esquema. Solo lo cambian los tests, para
# apuntar a un servidor local: normalizar_url no deja pasar otros puertos.
_PUERTOS = {"http": 80, "https": 443}

_lock = threading.Lock()
_IPS: dict[str, list[float]] = defaultdict(list)
_DIA = {"dia": None, "n": 0}


class NoSePuede(ValueError):
    """Un pedido que no se puede hacer. El mensaje va tal cual a la pantalla."""


# ── tiempo ───────────────────────────────────────────────────────────────────

def _ahora() -> datetime:
    """UTC sin tzinfo. Los tests la tapan."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _txt(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S")


def fecha_mvd(txt: str) -> str:
    d = datetime.strptime(txt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone(MVD)
    return d.strftime("%d/%m/%Y")


# ── la dirección ─────────────────────────────────────────────────────────────

def normalizar_url(texto) -> str:
    """'Barberia.com.uy/contacto' -> 'https://barberia.com.uy/contacto'.

    Rechaza lo que no sea un dominio público con http/https en el puerto de
    siempre. La query se tira: no hace falta y puede traer datos de alguien.
    """
    t = str(texto or "").strip()
    if not t:
        raise NoSePuede("Escribí la dirección del sitio.")
    if len(t) > 300 or any(c.isspace() for c in t):
        raise NoSePuede("Esa dirección no parece la de un sitio.")
    if not re.match(r"^[a-z][a-z0-9+.-]*://", t, re.I):
        t = "https://" + t
    try:
        p = urlsplit(t)
        puerto = p.port
    except ValueError:
        raise NoSePuede("Esa dirección no parece la de un sitio.")
    if p.scheme.lower() not in ("http", "https"):
        raise NoSePuede("La dirección tiene que empezar con http o https.")
    if p.username or p.password:
        raise NoSePuede("Esa dirección no parece la de un sitio.")
    if puerto not in (None, 80, 443):
        raise NoSePuede("Solo analizamos sitios en el puerto de siempre.")
    host = (p.hostname or "").rstrip(".").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise NoSePuede("Esa dirección no parece la de un sitio.")
    try:
        ipaddress.ip_address(host)
        es_ip = True
    except ValueError:
        es_ip = False
    if es_ip:
        raise NoSePuede("Escribí el dominio del sitio, no una IP.")
    if not _HOST.match(host) or host.endswith((".local", ".localhost", ".internal", ".lan", ".home", ".corp")):
        raise NoSePuede("Esa dirección no parece la de un sitio.")
    return f"{p.scheme.lower()}://{host}{p.path or '/'}"


def dominio_de(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


def _ip_publica(texto: str) -> bool:
    ip = ipaddress.ip_address(texto.split("%")[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def _resolver(host: str) -> str:
    """La IP a la que se va a conectar, ya validada. Todas tienen que ser públicas."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        raise NoSePuede("No encontramos ese dominio. ¿Está bien escrito?")
    ips = list(dict.fromkeys(i[4][0] for i in infos))
    if not ips or not all(_ip_publica(ip) for ip in ips):
        raise NoSePuede("Esa dirección no es la de un sitio público.")
    return next((ip for ip in ips if ":" not in ip), ips[0])


# ── la red ───────────────────────────────────────────────────────────────────

def _pedir(url: str, timeout=TIMEOUT_PAGINA, max_bytes: int | None = None, verificar: bool = True) -> dict:
    """Un pedido GET sin seguir redirecciones, conectando a la IP validada."""
    max_bytes = MAX_BYTES if max_bytes is None else max_bytes
    p = urlsplit(url)
    host = p.hostname
    ip = _resolver(host)
    https = p.scheme == "https"
    puerto = p.port or _PUERTOS[p.scheme]
    comun = dict(port=puerto, timeout=timeout, retries=False, maxsize=1)
    if https:
        pool = urllib3.HTTPSConnectionPool(
            ip, server_hostname=host, assert_hostname=host if verificar else False,
            cert_reqs=ssl.CERT_REQUIRED if verificar else ssl.CERT_NONE, **comun)
    else:
        pool = urllib3.HTTPConnectionPool(ip, **comun)
    ruta = p.path or "/"
    t0 = time.monotonic()
    try:
        r = pool.urlopen("GET", ruta, redirect=False, preload_content=False, assert_same_host=False, headers={
            "Host": host if puerto in (80, 443) else f"{host}:{puerto}", "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8", "Accept-Language": "es-UY,es;q=0.9",
            "Accept-Encoding": "gzip, deflate"})
        ms = int((time.monotonic() - t0) * 1000)
        cuerpo = r.read(max_bytes + 1, decode_content=True) if r.status < 300 or r.status >= 400 else b""
        r.release_conn()
    finally:
        pool.close()
    return {"url": url, "estado": r.status, "cabeceras": {k.lower(): v for k, v in r.headers.items()},
            "cuerpo": cuerpo[:max_bytes], "truncado": len(cuerpo) > max_bytes, "ms": ms,
            "ms_total": int((time.monotonic() - t0) * 1000)}


def _seguir(url: str, timeout=TIMEOUT_PAGINA, max_bytes: int | None = None) -> tuple[dict, list[str], bool]:
    """Sigue redirecciones validando cada salto. Devuelve (respuesta, saltos, ssl_invalido)."""
    saltos: list[str] = []
    ssl_invalido = False
    for _ in range(MAX_REDIRECCIONES + 1):
        try:
            r = _pedir(url, timeout, max_bytes)
        except urllib3.exceptions.SSLError:
            # Un certificado vencido o de otro dominio es justo lo que hay que
            # mostrar ("No seguro"), así que se mira igual sin verificar.
            ssl_invalido = True
            r = _pedir(url, timeout, max_bytes, verificar=False)
        loc = r["cabeceras"].get("location")
        if r["estado"] in (301, 302, 303, 307, 308) and loc:
            saltos.append(url)
            url = normalizar_url(urljoin(url, loc.strip()))
            continue
        return r, saltos, ssl_invalido
    raise NoSePuede("El sitio redirige demasiadas veces.")


def _texto(r: dict) -> str:
    tipo = r["cabeceras"].get("content-type", "")
    m = re.search(r"charset=([\w-]+)", tipo, re.I) or re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", r["cuerpo"][:4000], re.I)
    cod = m.group(1) if m else "utf-8"
    cod = cod.decode("ascii", "ignore") if isinstance(cod, bytes) else cod
    try:
        return r["cuerpo"].decode(cod, errors="replace")
    except LookupError:
        return r["cuerpo"].decode("utf-8", errors="replace")


def _extra(url: str) -> dict | None:
    try:
        r, _, _ = _seguir(url, TIMEOUT_EXTRA, 300_000)
        return r
    except (NoSePuede, urllib3.exceptions.HTTPError, OSError):
        return None


def _http_a_https(url: str) -> bool | None:
    """¿http:// manda a https://? None si no se pudo saber."""
    try:
        r = _pedir("http://" + urlsplit(url).hostname + "/", TIMEOUT_EXTRA, 1)
    except (NoSePuede, urllib3.exceptions.HTTPError, OSError):
        return None
    loc = r["cabeceras"].get("location", "")
    return r["estado"] in (301, 302, 307, 308) and loc.lower().startswith("https://")


def obtener(url: str) -> dict:
    """Baja la página y lo que hace falta alrededor. Lo único con red."""
    try:
        r, saltos, ssl_invalido = _seguir(url)
    except urllib3.exceptions.MaxRetryError as e:
        raise NoSePuede(_motivo(e.reason))
    except (urllib3.exceptions.HTTPError, OSError) as e:
        raise NoSePuede(_motivo(e))
    if r["estado"] >= 400:
        raise NoSePuede(f"El sitio respondió con un error ({r['estado']}): así lo ve también un cliente.")
    tipo = r["cabeceras"].get("content-type", "").lower()
    if tipo and "html" not in tipo:
        raise NoSePuede("Esa dirección no devuelve una página web.")
    final = r["url"]
    raiz = f"{urlsplit(final).scheme}://{urlsplit(final).hostname}"
    with ThreadPoolExecutor(3) as ex:
        f_robots = ex.submit(_extra, raiz + "/robots.txt")
        f_sitemap = ex.submit(_extra, raiz + "/sitemap.xml")
        f_http = ex.submit(_http_a_https, final) if final.startswith("https://") else None
        robots, sitemap = f_robots.result(), f_sitemap.result()
        http_ok = f_http.result() if f_http else False
    return {
        "url": url, "url_final": final, "html": _texto(r), "bytes": len(r["cuerpo"]), "truncado": r["truncado"],
        "ms": r["ms"], "cabeceras": r["cabeceras"], "saltos": len(saltos), "ssl_invalido": ssl_invalido,
        "http_a_https": http_ok,
        "robots": _texto(robots) if robots and robots["estado"] == 200 else None,
        "sitemap": bool(sitemap and sitemap["estado"] == 200 and b"<" in sitemap["cuerpo"][:500]
                        and b"<html" not in sitemap["cuerpo"][:500].lower()),
    }


def _motivo(e) -> str:
    texto = str(e).lower()
    if "timed out" in texto or "timeout" in texto:
        return "El sitio tardó demasiado en responder: un cliente se iría antes."
    if "refused" in texto:
        return "El sitio no acepta conexiones: está caído o mal configurado."
    return "No pudimos abrir el sitio. Probá de nuevo en un rato."


# ── leer el HTML ─────────────────────────────────────────────────────────────

class _Lector(HTMLParser):
    """Junta lo que mira el análisis. Tolerante: un HTML roto no lo frena."""

    VACIOS = {"script", "style", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.lang = ""
        self.metas: dict[str, str] = {}
        self.links: list[dict] = []
        self.rels: list[tuple[str, str]] = []
        self.scripts: list[str] = []
        self.inline = []
        self.jsonld: list[str] = []
        self.imgs: list[dict] = []
        self.iframes: list[str] = []
        self.forms: list[dict] = []
        self.h1: list[str] = []
        self.texto: list[str] = []
        self._en = []
        self._script_tipo = None
        self._a = None
        self._h1 = None
        self._largo = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "meta":
            clave = (a.get("name") or a.get("property") or a.get("http-equiv") or "").lower()
            if clave:
                self.metas.setdefault(clave, a.get("content", ""))
            if "charset" in a:
                self.metas.setdefault("charset", a["charset"])
        elif tag == "a":
            self._a = {"href": a.get("href", "").strip(), "texto": "", "attrs": a}
            self.links.append(self._a)
        elif tag == "link":
            self.rels.append((a.get("rel", "").lower(), a.get("href", "")))
        elif tag == "script":
            self._script_tipo = a.get("type", "").lower()
            if a.get("src"):
                self.scripts.append(a["src"])
        elif tag == "img":
            self.imgs.append(a)
        elif tag == "iframe":
            self.iframes.append(a.get("src", "") or a.get("data-src", ""))
        elif tag == "form":
            self.forms.append({"attrs": a, "campos": []})
        elif tag in ("input", "textarea", "select") and self.forms:
            self.forms[-1]["campos"].append("textarea" if tag == "textarea" else a.get("type", "text").lower())
        elif tag == "h1":
            self._h1 = []
        if tag in self.VACIOS or tag == "title":
            self._en.append(tag)

    def handle_endtag(self, tag):
        if self._en and self._en[-1] == tag:
            self._en.pop()
        if tag == "a":
            self._a = None
        elif tag == "h1" and self._h1 is not None:
            self.h1.append(" ".join("".join(self._h1).split()))
            self._h1 = None
        elif tag == "script":
            self._script_tipo = None

    def handle_data(self, data):
        if self._en and self._en[-1] == "title":
            self.title += data
            return
        if self._en and self._en[-1] == "script":
            if "ld+json" in (self._script_tipo or ""):
                self.jsonld.append(data)
            elif self._largo < 400_000:
                self.inline.append(data)
                self._largo += len(data)
            return
        if self._en:
            return
        if self._a is not None:
            self._a["texto"] += data
        if self._h1 is not None:
            self._h1.append(data)
        if self._largo < 400_000:
            self.texto.append(data)
            self._largo += len(data)


def leer(html: str) -> _Lector:
    lector = _Lector()
    try:
        lector.feed(html)
        lector.close()
    except Exception:   # noqa: BLE001 — un HTML raro no tira el diagnóstico
        pass
    return lector


# ── el análisis ──────────────────────────────────────────────────────────────

CATEGORIAS = [
    ("celular", "Celular", "Cómo se ve y se usa desde el teléfono, que es de donde entra casi todo el mundo."),
    ("contacto", "Contacto", "Qué tan fácil es escribirte o llamarte desde el sitio."),
    ("google", "Google", "Si Google entiende el sitio para mostrarlo cuando te buscan."),
    ("velocidad", "Velocidad", "Cuánto espera alguien antes de ver algo."),
    ("confianza", "Confianza", "Lo que hace que alguien se quede o desconfíe y se vaya."),
    ("medicion", "Medición", "Si sabés cuánta gente entra y qué hace."),
]

PLATAFORMAS = [
    ("Shopify", ("cdn.shopify.com", "shopify.theme")),
    ("Tiendanube", ("tiendanube", "nuvemshop", "d26lpennugtm8s.cloudfront")),
    ("Wix", ("static.wixstatic.com", "wix.com", "_wixcss")),
    ("Squarespace", ("squarespace.com", "static1.squarespace")),
    ("Webflow", ("webflow.com", "data-wf-page")),
    ("WordPress", ("wp-content/", "wp-includes/")),
    ("Blogger", ("blogger.com", "blogspot.com")),
    ("Google Sites", ("sites.google.com",)),
]

LOCALES = {"localbusiness", "organization", "store", "restaurant", "foodestablishment", "barberorhairsalon",
           "beautysalon", "healthandbeautybusiness", "medicalbusiness", "dentist", "physician", "medicalclinic",
           "veterinarycare", "autorepair", "autodealer", "professionalservice", "legalservice", "attorney",
           "realestateagent", "lodgingbusiness", "hotel", "sportsactivitylocation", "exercisegym",
           "homeandconstructionbusiness", "clothingstore", "hardwarestore", "petstore", "optician",
           "accountingservice", "educationalorganization", "onlinestore", "bakery", "cafeorcoffeeshop",
           "pharmacy", "electronicsstore", "furniturestore", "jewelrystore", "shoestore", "bookstore",
           "grocerystore", "travelagency", "insuranceagency", "financialservice", "automotivebusiness"}

RELLENO = ("lorem ipsum", "just another wordpress site", "otro sitio realizado con wordpress", "hello world!",
           "¡hola, mundo!", "sample page", "página de ejemplo", "add your heading text here",
           "agregá tu texto aquí", "añade tu texto aquí", "this is a heading", "edit this text")

TITULOS_VACIOS = {"inicio", "home", "mi sitio", "my site", "untitled", "sin título", "index", "página principal",
                  "my wordpress blog", "mi blog", "wordpress"}

_WA = re.compile(r"^(?:(?:https?:)?//)?(?:wa\.me|api\.whatsapp\.com|web\.whatsapp\.com|wa\.link|chat\.whatsapp\.com)\b|^whatsapp:", re.I)
_MAPS = re.compile(r"google\.[a-z.]+/maps|maps\.google\.|goo\.gl/maps|maps\.app\.goo\.gl|g\.page/", re.I)


def _h(clave, cat, estado, titulo, detalle, por_que, solucion, peso, servicio=""):
    return {"clave": clave, "cat": cat, "estado": estado, "titulo": titulo, "detalle": detalle,
            "por_que": por_que, "solucion": solucion, "peso": peso, "servicio": servicio}


def _tipos_jsonld(bloques: list[str]) -> set[str]:
    tipos: set[str] = set()

    def juntar(x):
        if isinstance(x, dict):
            t = x.get("@type")
            for v in (t if isinstance(t, list) else [t]):
                if isinstance(v, str):
                    tipos.add(v.lower())
            for v in x.values():
                juntar(v)
        elif isinstance(x, list):
            for v in x:
                juntar(v)

    for b in bloques:
        try:
            juntar(json.loads(b))
        except (ValueError, RecursionError):
            continue
    return tipos


def _version(texto: str) -> tuple[int, ...] | None:
    m = re.search(r"wordpress\s+(\d+)\.(\d+)", texto or "", re.I)
    return (int(m.group(1)), int(m.group(2))) if m else None


def plataforma(html_bajo: str, generador: str) -> str | None:
    gen = (generador or "").lower()
    for nombre, marcas in PLATAFORMAS:
        if nombre.lower() in gen or any(m in html_bajo for m in marcas):
            if nombre == "WordPress" and "woocommerce" in html_bajo:
                return "WordPress + WooCommerce"
            return nombre
    return None


def analizar(pagina: dict, anio: int | None = None) -> dict:
    """Los hallazgos, el puntaje por categoría y el total. Puro: sin red."""
    anio = anio or _ahora().year
    html = pagina.get("html") or ""
    bajo = html.lower()
    L = leer(html)
    url = pagina.get("url_final") or pagina.get("url") or ""
    https = url.startswith("https://")
    texto = " ".join(" ".join(L.texto).split())
    texto_bajo = texto.lower()
    hrefs = [l["href"] for l in L.links]
    scripts_bajo = " ".join(L.scripts).lower() + " " + "".join(L.inline).lower()
    hs: list[dict] = []

    # Celular
    vp = L.metas.get("viewport", "").lower()
    if "width=device-width" in vp:
        hs.append(_h("viewport", "celular", "ok", "Se adapta al celular",
                     "El sitio le dice al teléfono que se acomode al ancho de la pantalla.", "", "", 10))
    else:
        hs.append(_h("viewport", "celular", "mal", "No está hecho para el celular",
                     "Falta la etiqueta que hace que el sitio se acomode al teléfono: se ve chiquito y hay que hacer zoom.",
                     "Más de 7 de cada 10 visitas llegan desde el celular. Si hay que hacer zoom para leer, la mayoría se va.",
                     "Rehacer la plantilla para que se adapte a cualquier pantalla.", 10, "Sitio nuevo adaptado al celular"))
    if "user-scalable=no" in vp.replace(" ", "") or "maximum-scale=1" in vp.replace(" ", ""):
        hs.append(_h("zoom", "celular", "aviso", "No deja hacer zoom",
                     "El sitio bloquea el zoom con los dedos.",
                     "A quien ve poco le impide leer precios y datos de contacto.",
                     "Sacar el bloqueo de zoom.", 2, "Ajustes de accesibilidad"))
    imgs = [i for i in L.imgs if i.get("src") or i.get("data-src")]
    sin_medida = [i for i in imgs if not (i.get("width") and i.get("height"))]
    if len(imgs) >= 4 and len(sin_medida) / len(imgs) > .5:
        hs.append(_h("saltos", "celular", "aviso", "La página salta mientras carga",
                     f"{len(sin_medida)} de {len(imgs)} imágenes no dicen qué tamaño tienen.",
                     "El texto se mueve cuando aparecen las fotos y la gente toca lo que no quería.",
                     "Darle medidas a las imágenes.", 3, "Optimización del sitio"))
    else:
        hs.append(_h("saltos", "celular", "ok", "La página no salta al cargar",
                     "Las imágenes tienen su lugar reservado.", "", "", 3))

    # Contacto
    wa = [h for h in hrefs if _WA.search(h)] or [l for l in L.links if l["attrs"].get("data-scalerics") == "whatsapp"]
    wa_widget = any(x in bajo for x in ("joinchat", "wa.me/", "click-to-chat", "whatsapp-button", "wptwa", "ht-ctc"))
    if wa or wa_widget:
        hs.append(_h("whatsapp", "contacto", "ok", "WhatsApp a un toque",
                     "Hay un botón o enlace que abre WhatsApp directo.", "", "", 10))
    else:
        hs.append(_h("whatsapp", "contacto", "mal", "No tiene botón de WhatsApp",
                     "No encontramos un enlace que abra WhatsApp con tu número.",
                     "Acá casi todos los clientes escriben por WhatsApp. Si tienen que copiar el número, muchos no lo hacen.",
                     "Un botón fijo de WhatsApp con un mensaje ya escrito, y medir cuántos lo tocan.", 10,
                     "Botón de WhatsApp con medición"))
    if any(h.lower().startswith("tel:") for h in hrefs):
        hs.append(_h("telefono", "contacto", "ok", "Se puede llamar con un toque",
                     "El teléfono es un enlace que llama.", "", "", 4))
    else:
        hs.append(_h("telefono", "contacto", "aviso", "El teléfono no se puede tocar para llamar",
                     "No hay ningún enlace tel: en la página.",
                     "Desde el celular, quien quiere llamar tiene que copiar el número a mano.",
                     "Hacer que el teléfono sea un enlace que llama.", 4, "Ajustes de contacto"))
    forms = [f for f in L.forms
             if not ("search" in f["campos"] or f["attrs"].get("role", "") == "search"
                     or re.search(r"search|buscar|login|cart|carrito", " ".join(f["attrs"].values()), re.I))]
    con_forms = [f for f in forms if "textarea" in f["campos"] or "email" in f["campos"] or "tel" in f["campos"]]
    embebidos = any(x in bajo for x in ("wpcf7", "wpforms", "elementor-form", "gform_", "forms.gle", "docs.google.com/forms",
                                         "typeform", "jotform", "hsforms"))
    if con_forms or embebidos:
        hs.append(_h("formulario", "contacto", "ok", "Tiene formulario de contacto",
                     "Quien no quiere escribir por WhatsApp puede dejar sus datos.", "", "", 3))
    else:
        hs.append(_h("formulario", "contacto", "aviso", "No tiene formulario de contacto",
                     "No encontramos un formulario para dejar datos o hacer una consulta.",
                     "Hay gente que consulta de noche o desde la oficina y prefiere dejar sus datos.",
                     "Un formulario corto que llegue al mail y al CRM.", 3, "Formulario conectado"))
    if any(_MAPS.search(x or "") for x in L.iframes + hrefs):
        hs.append(_h("mapa", "contacto", "ok", "Muestra dónde está",
                     "Hay un mapa o un enlace a Google Maps.", "", "", 3))
    else:
        hs.append(_h("mapa", "contacto", "aviso", "No muestra cómo llegar",
                     "No hay mapa ni enlace a Google Maps.",
                     "Para un comercio con local, \"¿dónde queda?\" es la segunda pregunta después del precio.",
                     "Un mapa con el enlace a la ficha de Google.", 3, "Ajustes de contacto"))
    redes = [r for r, m in (("Instagram", "instagram.com"), ("Facebook", "facebook.com"), ("TikTok", "tiktok.com"))
             if any(m in h.lower() for h in hrefs)]
    hs.append(_h("redes", "contacto", "info", "Redes enlazadas" if redes else "Sin enlaces a redes",
                 ", ".join(redes) if redes else "No hay enlaces a Instagram, Facebook ni TikTok.", "", "", 0))

    # Google
    titulo = " ".join(L.title.split())
    robots_meta = (L.metas.get("robots", "") + " " + L.metas.get("googlebot", "")).lower()
    x_robots = (pagina.get("cabeceras") or {}).get("x-robots-tag", "").lower()
    if "noindex" in robots_meta or "noindex" in x_robots:
        hs.append(_h("noindex", "google", "mal", "Le pide a Google que no lo muestre",
                     "La página tiene la marca \"noindex\": Google la saca de los resultados.",
                     "Es el error más caro: el sitio existe pero nadie lo encuentra buscando. Suele quedar olvidado de cuando se armó.",
                     "Sacar la marca y pedirle a Google que lo vuelva a leer.", 14, "SEO local"))
    else:
        hs.append(_h("noindex", "google", "ok", "Google lo puede mostrar",
                     "Nada le impide a Google mostrar la página.", "", "", 14))
    if not titulo:
        hs.append(_h("titulo", "google", "mal", "La página no tiene título",
                     "Falta el título que Google muestra en azul en los resultados.",
                     "Sin título, Google inventa uno y casi nunca es el que te conviene.",
                     "Escribir un título con el rubro, el barrio y el nombre.", 6, "SEO local"))
    elif titulo.lower().strip(" -|") in TITULOS_VACIOS or len(titulo) < 15:
        hs.append(_h("titulo", "google", "aviso", "El título no dice qué hacés",
                     f"El título es \"{titulo[:80]}\".",
                     "Es lo primero que se lee en Google. Si no dice el rubro ni la zona, no compite con los de al lado.",
                     "Un título con el rubro, el barrio y el nombre.", 6, "SEO local"))
    elif len(titulo) > 70:
        hs.append(_h("titulo", "google", "aviso", "El título es demasiado largo",
                     f"Tiene {len(titulo)} letras: Google lo corta.",
                     "Lo importante queda en los puntos suspensivos.", "Dejarlo en menos de 60 letras.", 6, "SEO local"))
    else:
        hs.append(_h("titulo", "google", "ok", "Tiene un buen título", f"\"{titulo[:80]}\".", "", "", 6))
    desc = " ".join(L.metas.get("description", "").split())
    if not desc:
        hs.append(_h("descripcion", "google", "mal", "No tiene descripción para Google",
                     "Falta el texto que Google muestra debajo del título.",
                     "Google pone cualquier frase del sitio, y ese texto es el que decide si te hacen clic.",
                     "Escribir una descripción que invite a entrar.", 5, "SEO local"))
    elif len(desc) < 50 or len(desc) > 170:
        hs.append(_h("descripcion", "google", "aviso", "La descripción para Google se puede mejorar",
                     f"Tiene {len(desc)} letras (lo ideal es entre 120 y 160).",
                     "Muy corta no convence; muy larga, Google la corta.", "Reescribirla.", 5, "SEO local"))
    else:
        hs.append(_h("descripcion", "google", "ok", "Tiene descripción para Google", f"\"{desc[:120]}\".", "", "", 5))
    if not [h for h in L.h1 if h]:
        hs.append(_h("h1", "google", "aviso", "No tiene un título principal en la página",
                     "No hay ningún encabezado principal (H1).",
                     "Es lo que Google lee para entender de qué se trata la página.",
                     "Un encabezado claro arriba de todo.", 3, "SEO local"))
    else:
        hs.append(_h("h1", "google", "ok", "Tiene título principal", f"\"{L.h1[0][:80]}\".", "", "", 3))
    tipos = _tipos_jsonld(L.jsonld)
    if tipos & LOCALES:
        hs.append(_h("datos", "google", "ok", "Le da a Google sus datos de negocio",
                     "Tiene datos estructurados de comercio (dirección, horario, teléfono).", "", "", 4))
    else:
        hs.append(_h("datos", "google", "aviso", "Google no tiene sus datos de negocio en el sitio",
                     "No hay datos estructurados de comercio (LocalBusiness).",
                     "Con esos datos Google muestra horario, dirección y teléfono, y lo asocia a la ficha de Maps.",
                     "Agregar los datos del comercio en el formato que lee Google.", 4, "SEO local"))
    if pagina.get("sitemap"):
        hs.append(_h("sitemap", "google", "ok", "Tiene mapa del sitio para Google",
                     "Hay un sitemap.xml.", "", "", 2))
    else:
        hs.append(_h("sitemap", "google", "aviso", "No tiene mapa del sitio para Google",
                     "No encontramos /sitemap.xml.",
                     "Sin mapa, Google tarda más en encontrar las páginas nuevas.",
                     "Generar el mapa y darlo de alta en Search Console.", 2, "SEO local"))
    sin_alt = [i for i in imgs if not i.get("alt", "").strip()]
    if len(imgs) >= 3 and len(sin_alt) / len(imgs) > .4:
        hs.append(_h("alt", "google", "aviso", "Las fotos no tienen descripción",
                     f"{len(sin_alt)} de {len(imgs)} imágenes no tienen texto alternativo.",
                     "Google Imágenes no sabe qué muestran y quien usa lector de pantalla tampoco.",
                     "Describir las fotos de productos y del local.", 2, "SEO local"))
    else:
        hs.append(_h("alt", "google", "ok", "Las fotos están descritas", "La mayoría de las imágenes tienen texto alternativo.", "", "", 2))
    if L.metas.get("og:image"):
        hs.append(_h("og", "google", "ok", "Se ve bien al compartirlo",
                     "Cuando alguien pasa el link por WhatsApp, sale con foto y título.", "", "", 3))
    else:
        hs.append(_h("og", "google", "aviso", "Al compartirlo por WhatsApp sale sin foto",
                     "Faltan las etiquetas que arman la vista previa del link (Open Graph).",
                     "El link se comparte por WhatsApp e Instagram todo el tiempo. Sin foto, parece spam.",
                     "Configurar la imagen y el texto que salen al compartir.", 3, "SEO local"))

    # Velocidad
    ms = int(pagina.get("ms") or 0)
    seg = f"{ms / 1000:.1f}".replace(".", ",")
    if ms <= 800:
        hs.append(_h("respuesta", "velocidad", "ok", "El servidor responde rápido", f"Respondió en {seg} s.", "", "", 6))
    else:
        hs.append(_h("respuesta", "velocidad", "aviso" if ms <= 1800 else "mal", "El servidor tarda en responder",
                     f"Tardó {seg} s solo en empezar a mandar la página.",
                     "Antes de ver nada, el visitante ya esperó eso. Con más de 3 segundos, la mitad se va.",
                     "Mejor hosting, caché y una página más liviana.", 6, "Hosting rápido y mantenimiento"))
    kb = int(pagina.get("bytes") or len(html.encode("utf-8", "ignore"))) // 1024
    if kb > 600:
        hs.append(_h("peso", "velocidad", "aviso" if kb <= 1500 else "mal", "La página es pesada",
                     f"Solo el código de la página pesa {kb} KB, sin contar fotos.",
                     "Con datos móviles se nota: tarda en aparecer.", "Limpiar el código que sobra.", 3,
                     "Optimización del sitio"))
    else:
        hs.append(_h("peso", "velocidad", "ok", "El código de la página es liviano", f"Pesa {kb} KB.", "", "", 3))
    externos = len(L.scripts)
    if externos > 15:
        hs.append(_h("scripts", "velocidad", "aviso" if externos <= 30 else "mal", "Carga demasiados programas",
                     f"La página carga {externos} scripts.",
                     "Cada uno es una espera más, sobre todo en el celular.",
                     "Sacar los complementos que no se usan.", 3, "Optimización del sitio"))
    else:
        hs.append(_h("scripts", "velocidad", "ok", "Carga pocos programas", f"{externos} scripts.", "", "", 3))
    lazy = [i for i in imgs if i.get("loading", "").lower() == "lazy" or i.get("data-src") or i.get("data-lazy-src")]
    if len(imgs) >= 8 and len(lazy) / len(imgs) < .3:
        hs.append(_h("lazy", "velocidad", "aviso", "Baja todas las fotos de una",
                     f"{len(imgs)} imágenes y casi ninguna espera a que el visitante llegue a ellas.",
                     "Se gastan datos y tiempo en fotos que capaz nadie ve.",
                     "Carga diferida de imágenes y formato WebP.", 2, "Optimización del sitio"))
    else:
        hs.append(_h("lazy", "velocidad", "ok", "Las fotos se cargan de a poco", "", "", "", 2))
    enc = (pagina.get("cabeceras") or {}).get("content-encoding", "").lower()
    if any(x in enc for x in ("gzip", "br", "zstd", "deflate")):
        hs.append(_h("compresion", "velocidad", "ok", "La página viaja comprimida", "", "", "", 2))
    else:
        hs.append(_h("compresion", "velocidad", "aviso", "La página viaja sin comprimir",
                     "El servidor no comprime lo que manda.", "Tarda más de lo necesario en llegar.",
                     "Activar la compresión en el servidor.", 2, "Hosting rápido y mantenimiento"))

    # Confianza
    if not https:
        hs.append(_h("https", "confianza", "mal", "El navegador lo marca \"No seguro\"",
                     "El sitio no usa https.",
                     "Chrome le avisa al visitante que el sitio no es seguro. Nadie deja sus datos ahí.",
                     "Certificado gratis y redirección a https.", 12, "Plan de mantenimiento"))
    elif pagina.get("ssl_invalido"):
        hs.append(_h("https", "confianza", "mal", "El certificado de seguridad no es válido",
                     "El certificado venció o es de otro dominio.",
                     "El navegador muestra una pantalla roja de advertencia antes de dejar entrar.",
                     "Renovar el certificado y dejarlo con renovación automática.", 12, "Plan de mantenimiento"))
    else:
        hs.append(_h("https", "confianza", "ok", "Conexión segura", "Usa https con un certificado válido.", "", "", 12))
        if pagina.get("http_a_https") is False:
            hs.append(_h("redireccion", "confianza", "aviso", "La versión sin https no redirige",
                         "Si alguien escribe la dirección sin https, entra a la versión insegura.",
                         "Le aparece \"No seguro\" a quien llega por un link viejo.",
                         "Redirigir todo a https.", 3, "Plan de mantenimiento"))
    relleno = next((r for r in RELLENO if r in texto_bajo or r in titulo.lower()), None)
    if relleno:
        hs.append(_h("relleno", "confianza", "mal", "Quedó texto de relleno a la vista",
                     f"Aparece \"{relleno}\" en la página.",
                     "Es lo primero que hace pensar que el sitio está abandonado o a medio hacer.",
                     "Revisar el sitio entero y reemplazar los textos de ejemplo.", 6, "Plan de mantenimiento"))
    anios = [int(a) for a in re.findall(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?((?:19|20)\d{2})", texto + " " + html[-20000:], re.I)]
    if anios and max(anios) < anio - 1:
        hs.append(_h("anio", "confianza", "aviso", "Parece desactualizado",
                     f"Al pie dice © {max(anios)}.",
                     "Un año viejo al pie hace dudar de si el negocio sigue abierto.",
                     "Poner el año automático y revisar precios y horarios.", 3, "Plan de mantenimiento"))
    elif anios:
        hs.append(_h("anio", "confianza", "ok", "Se ve al día", f"Al pie dice © {max(anios)}.", "", "", 3))
    gen = L.metas.get("generator", "")
    ver = _version(gen)
    if ver and ver < (6, 4):
        hs.append(_h("version", "confianza", "aviso", "WordPress desactualizado",
                     f"El sitio dice que usa WordPress {ver[0]}.{ver[1]}.",
                     "Las versiones viejas tienen fallas conocidas: es la puerta más común para que hackeen un sitio.",
                     "Actualizar WordPress y los plugins, con copia de seguridad antes.", 4, "Plan de mantenimiento"))
    mixto = https and any(re.match(r"^http://", x or "", re.I) for x in L.scripts + L.iframes
                          + [i.get("src", "") for i in L.imgs])
    if mixto:
        hs.append(_h("mixto", "confianza", "aviso", "Mezcla contenido inseguro",
                     "Hay imágenes o scripts que se cargan por http dentro de una página https.",
                     "El navegador las bloquea o saca el candado.", "Pasar todo a https.", 2, "Plan de mantenimiento"))
    if any("icon" in rel for rel, _ in L.rels):
        hs.append(_h("favicon", "confianza", "ok", "Tiene ícono en la pestaña", "", "", "", 1))
    else:
        hs.append(_h("favicon", "confianza", "aviso", "No declara ícono para la pestaña",
                     "No encontramos el ícono que aparece en la pestaña y en los favoritos.",
                     "Es un detalle, pero suma a que parezca profesional.", "Agregar el ícono con el logo.", 1,
                     "Ajustes de marca"))

    # Medición
    if any(x in scripts_bajo for x in ("googletagmanager.com", "google-analytics.com", "gtag(", "plausible.io",
                                       "static.cloudflareinsights.com", "clarity.ms")):
        hs.append(_h("analitica", "medicion", "ok", "Mide las visitas", "Tiene una herramienta de analítica instalada.", "", "", 3))
    else:
        hs.append(_h("analitica", "medicion", "aviso", "No sabe cuánta gente entra",
                     "No encontramos Google Analytics ni otra herramienta de medición.",
                     "Sin números no se sabe si el sitio trae clientes ni qué mejorar.",
                     "Medición de visitas y de cuántos tocan WhatsApp o llaman, con un informe por mes.", 3,
                     "Analítica de contactos"))
    if any(x in scripts_bajo for x in ("connect.facebook.net", "fbq(")):
        hs.append(_h("pixel", "medicion", "ok", "Tiene el píxel de Meta", "Puede medir los anuncios de Instagram y Facebook.", "", "", 2))
    else:
        hs.append(_h("pixel", "medicion", "aviso", "No puede medir los anuncios de Instagram",
                     "No tiene el píxel de Meta.",
                     "Si hace anuncios en Instagram o Facebook, no sabe cuáles traen consultas.",
                     "Instalar el píxel y medir los contactos.", 2, "Analítica de contactos"))

    return resumir(hs, {
        "plataforma": plataforma(bajo, gen),
        "titulo": titulo[:200], "segundos": seg, "kb": kb, "imagenes": len(imgs), "scripts": externos,
        "url_final": url, "redes": redes,
    })


def resumir(hs: list[dict], medidas: dict) -> dict:
    valor = {"ok": 1.0, "aviso": .5, "mal": 0.0}
    cats = []
    for clave, nombre, ayuda in CATEGORIAS:
        de = [h for h in hs if h["cat"] == clave and h["estado"] in valor]
        total = sum(h["peso"] for h in de)
        puntaje = round(100 * sum(h["peso"] * valor[h["estado"]] for h in de) / total) if total else 100
        cats.append({"clave": clave, "nombre": nombre, "ayuda": ayuda, "puntaje": puntaje,
                     "problemas": sum(1 for h in de if h["estado"] != "ok")})
    # Las seis categorías pesan lo mismo: si no, los chequeos que pasan tapan
    # a los que importan. Y un crítico (no se ve en el celular, sin WhatsApp,
    # Google no lo muestra, "No seguro") pone techo, por más que lo demás esté bien.
    puntaje = round(sum(c["puntaje"] for c in cats) / len(cats))
    criticos = sum(1 for h in hs if h["estado"] == "mal" and h["peso"] >= CRITICO)
    if criticos:
        puntaje = min(puntaje, 64 if criticos == 1 else 49)
    problemas = [h for h in hs if h["estado"] in ("mal", "aviso")]
    problemas.sort(key=lambda h: (h["estado"] != "mal", -h["peso"]))
    return {"puntaje": puntaje, "nivel": nivel(puntaje), "categorias": cats, "hallazgos": hs,
            "prioridades": [h["clave"] for h in problemas[:3]], "medidas": medidas,
            "servicios": list(dict.fromkeys(h["servicio"] for h in problemas if h["servicio"]))}


def nivel(puntaje: int) -> dict:
    if puntaje >= 80:
        return {"clave": "bien", "texto": "Bien encaminado"}
    if puntaje >= 55:
        return {"clave": "mejorable", "texto": "Mejorable"}
    return {"clave": "mal", "texto": "Está perdiendo clientes"}


# ── base ─────────────────────────────────────────────────────────────────────

def init_diagnostico(conn: sqlite3.Connection) -> None:
    """La tabla. La llama `database.init_db` con su conexión."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS diagnosticos (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo         TEXT NOT NULL UNIQUE,         -- la llave del informe público
            url            TEXT NOT NULL,
            dominio        TEXT NOT NULL,
            nombre         TEXT,                         -- el comercio
            email          TEXT,
            telefono       TEXT,
            origen         TEXT NOT NULL,                -- web | equipo
            business_id    INTEGER,
            estado         TEXT NOT NULL,                -- ok | error
            error          TEXT,
            puntaje        INTEGER,
            resultado      TEXT,                         -- JSON de analizar()
            seguimiento    TEXT NOT NULL DEFAULT 'nuevo',
            nota           TEXT,
            aperturas      INTEGER NOT NULL DEFAULT 0,
            ultima_apertura TEXT,
            creado_por     TEXT,
            creado_en      TEXT NOT NULL,
            actualizado_en TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_diagnosticos_dominio ON diagnosticos (dominio, creado_en)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_diagnosticos_creado ON diagnosticos (creado_en)")


def _conn(db: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _fila(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    d["resultado"] = json.loads(d["resultado"]) if d.get("resultado") else None
    return d


def _reciente(db: str, dominio: str) -> dict | None:
    conn = _conn(db)
    try:
        r = conn.execute("SELECT * FROM diagnosticos WHERE dominio = ? AND estado = 'ok' AND creado_en >= ? "
                         "ORDER BY id DESC LIMIT 1", (dominio, _txt(_ahora() - REUSO))).fetchone()
    finally:
        conn.close()
    return _fila(r)


def _correr(db: str, url: str, reusar: bool) -> tuple[str, dict | None, str | None]:
    """(url, resultado, error). Con `reusar`, un dominio recién mirado no se vuelve a bajar."""
    if reusar:
        previo = _reciente(db, dominio_de(url))
        if previo:
            return previo["url"], previo["resultado"], None
    try:
        pagina = obtener(url)
    except NoSePuede as e:
        return url, None, str(e)
    return url, analizar(pagina), None


def _insertar(db: str, datos: dict) -> dict:
    ahora = _txt(_ahora())
    res = datos.get("resultado")
    conn = _conn(db)
    try:
        cur = conn.execute(
            "INSERT INTO diagnosticos (codigo, url, dominio, nombre, email, telefono, origen, business_id, estado, "
            "error, puntaje, resultado, creado_por, creado_en, actualizado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (secrets.token_urlsafe(12), datos["url"], dominio_de(datos["url"]), datos.get("nombre"),
             datos.get("email"), datos.get("telefono"), datos["origen"], datos.get("business_id"),
             "ok" if res else "error", datos.get("error"), res["puntaje"] if res else None,
             json.dumps(res, ensure_ascii=False) if res else None, datos.get("creado_por"), ahora, ahora))
        conn.commit()
        return _fila(conn.execute("SELECT * FROM diagnosticos WHERE id = ?", (cur.lastrowid,)).fetchone())
    finally:
        conn.close()


def _paso_tope(ip: str) -> bool:
    ahora = time.time()
    hoy = _ahora().date().isoformat()
    with _lock:
        if _DIA["dia"] != hoy:
            _DIA.update(dia=hoy, n=0)
        recientes = [t for t in _IPS[ip] if ahora - t < 3600]
        _IPS[ip] = recientes
        if len(recientes) >= TOPE_POR_IP or _DIA["n"] >= TOPE_DIA:
            return False
        recientes.append(ahora)
        _DIA["n"] += 1
        return True


def _telefono(texto) -> str | None:
    t = str(texto or "").strip()
    if not t:
        return None
    dig = re.sub(r"\D", "", t)
    if not 8 <= len(dig) <= 15:
        raise NoSePuede("El WhatsApp no parece un número de teléfono.")
    if len(dig) == 9 and dig.startswith("09"):
        return "+598" + dig[1:]
    if len(dig) == 8 and dig.startswith("9"):
        return "+598" + dig
    return "+" + dig


def validar_publico(datos: dict) -> dict:
    url = normalizar_url(datos.get("url"))
    nombre = " ".join(str(datos.get("nombre") or "").split())[:120] or None
    email = str(datos.get("email") or "").strip()[:200] or None
    if email and not _MAIL.match(email):
        raise NoSePuede("El mail no parece válido.")
    telefono = _telefono(str(datos.get("telefono") or "")[:40])
    if not email and not telefono:
        raise NoSePuede("Dejanos un WhatsApp o un mail para mandarte el informe.")
    return {"url": url, "nombre": nombre, "email": email, "telefono": telefono}


def crear_publico(db: str, datos: dict, ip: str) -> dict:
    """El pedido de la página pública. Valida, corre (o reusa) y lo deja como lead."""
    d = validar_publico(datos)
    if not _paso_tope(ip):
        raise NoSePuede("Hiciste muchos diagnósticos seguidos. Probá de nuevo en un rato.")
    url, res, error = _correr(db, d["url"], reusar=True)
    fila = _insertar(db, {**d, "url": url, "resultado": res, "error": error, "origen": "web", "creado_por": "web"})
    bid = _lead(db, fila)
    if bid:
        conn = _conn(db)
        try:
            conn.execute("UPDATE diagnosticos SET business_id = ? WHERE id = ?", (bid, fila["id"]))
            conn.commit()
        finally:
            conn.close()
        fila["business_id"] = bid
    return fila


def _lead(db: str, fila: dict) -> int | None:
    """El negocio del CRM de este pedido: el que ya estaba (por mail o teléfono) o uno nuevo."""
    from database import get_business_by_email, get_business_by_phone, insert_business, log_activity
    resumen = (f"Pidió el diagnóstico web de {fila['dominio']}: "
               + (f"{fila['puntaje']}/100" if fila["estado"] == "ok" else "el sitio no abrió"))
    existente = ((fila["email"] and get_business_by_email(db, fila["email"]))
                 or (fila["telefono"] and get_business_by_phone(db, fila["telefono"])) or None)
    if existente:
        log_activity(db, "web", "diagnostico_web", "lead", existente["id"], existente.get("name") or fila["dominio"],
                     resumen, user_id=None)
        return existente["id"]
    bid = insert_business(db, {
        "name": fila["nombre"] or fila["dominio"], "email": fila["email"], "phone": fila["telefono"],
        "website": fila["url"], "category": "Diagnóstico web", "notes": resumen, "source": "web_diagnostico",
        "score": 70, "has_whatsapp": 1 if fila["telefono"] else None,
        "form_data": json.dumps({"diagnostico": fila["codigo"], "puntaje": fila["puntaje"]}, ensure_ascii=False),
    })
    if bid:
        log_activity(db, "web", "lead_created", "lead", bid, fila["nombre"] or fila["dominio"], resumen, user_id=None)
    return bid


def crear_equipo(db: str, datos: dict, quien: str) -> dict:
    """Un diagnóstico que corre alguien del equipo, por ejemplo antes de llamar a un lead."""
    url = normalizar_url(datos.get("url"))
    bid = datos.get("business_id")
    nombre = " ".join(str(datos.get("nombre") or "").split())[:120] or None
    if bid not in (None, ""):
        try:
            bid = int(bid)
        except (TypeError, ValueError):
            raise NoSePuede("El lead no es válido.")
        conn = _conn(db)
        try:
            b = conn.execute("SELECT id, name FROM businesses WHERE id = ?", (bid,)).fetchone()
        finally:
            conn.close()
        if not b:
            raise NoSePuede("Ese lead no existe.")
        nombre = nombre or b["name"]
    else:
        bid = None
    url, res, error = _correr(db, url, reusar=False)
    return _insertar(db, {"url": url, "nombre": nombre, "business_id": bid, "resultado": res, "error": error,
                          "origen": "equipo", "creado_por": quien})


def repetir(db: str, did: int, quien: str) -> dict:
    d = ver(db, did)
    url, res, error = _correr(db, d["url"], reusar=False)
    conn = _conn(db)
    try:
        conn.execute("UPDATE diagnosticos SET estado = ?, error = ?, puntaje = ?, resultado = ?, actualizado_en = ? "
                     "WHERE id = ?", ("ok" if res else "error", error, res["puntaje"] if res else None,
                                      json.dumps(res, ensure_ascii=False) if res else None, _txt(_ahora()), did))
        conn.commit()
    finally:
        conn.close()
    return ver(db, did)


def ver(db: str, did: int) -> dict:
    conn = _conn(db)
    try:
        r = conn.execute("SELECT d.*, b.name AS lead FROM diagnosticos d LEFT JOIN businesses b ON b.id = d.business_id "
                         "WHERE d.id = ?", (did,)).fetchone()
    finally:
        conn.close()
    if not r:
        raise NoSePuede("Ese diagnóstico no existe.")
    return _fila(r)


def por_codigo(db: str, codigo: str) -> dict | None:
    if not codigo or len(codigo) > 64:
        return None
    conn = _conn(db)
    try:
        return _fila(conn.execute("SELECT * FROM diagnosticos WHERE codigo = ?", (codigo,)).fetchone())
    finally:
        conn.close()


def marcar_apertura(db: str, did: int) -> None:
    conn = _conn(db)
    try:
        conn.execute("UPDATE diagnosticos SET aperturas = aperturas + 1, ultima_apertura = ? WHERE id = ?",
                     (_txt(_ahora()), did))
        conn.commit()
    finally:
        conn.close()


def editar(db: str, did: int, datos: dict) -> dict:
    ver(db, did)
    cambios = {}
    if "seguimiento" in datos:
        if datos["seguimiento"] not in SEGUIMIENTO:
            raise NoSePuede("Estado de seguimiento no válido.")
        cambios["seguimiento"] = datos["seguimiento"]
    if "nota" in datos:
        cambios["nota"] = str(datos["nota"] or "").strip()[:2000] or None
    if not cambios:
        raise NoSePuede("No hay nada para cambiar.")
    conn = _conn(db)
    try:
        conn.execute(f"UPDATE diagnosticos SET {', '.join(k + ' = ?' for k in cambios)}, actualizado_en = ? "
                     "WHERE id = ?", (*cambios.values(), _txt(_ahora()), did))
        conn.commit()
    finally:
        conn.close()
    return ver(db, did)


def nuevo_codigo(db: str, did: int) -> dict:
    ver(db, did)
    conn = _conn(db)
    try:
        conn.execute("UPDATE diagnosticos SET codigo = ? WHERE id = ?", (secrets.token_urlsafe(12), did))
        conn.commit()
    finally:
        conn.close()
    return ver(db, did)


def borrar(db: str, did: int) -> None:
    ver(db, did)
    conn = _conn(db)
    try:
        conn.execute("DELETE FROM diagnosticos WHERE id = ?", (did,))
        conn.commit()
    finally:
        conn.close()


def _compacto(d: dict) -> dict:
    """Lo que pinta la lista: sin los hallazgos enteros, con los tres primeros problemas."""
    res = d.pop("resultado", None) or {}
    hs = {h["clave"]: h for h in res.get("hallazgos", [])}
    d["prioridades"] = [{"titulo": hs[k]["titulo"], "estado": hs[k]["estado"], "servicio": hs[k]["servicio"]}
                        for k in res.get("prioridades", []) if k in hs]
    d["categorias"] = [{"clave": c["clave"], "nombre": c["nombre"], "puntaje": c["puntaje"]}
                       for c in res.get("categorias", [])]
    d["nivel"] = res.get("nivel")
    d["plataforma"] = (res.get("medidas") or {}).get("plataforma")
    return d


def listar(db: str, filtro: str = "", limite: int = 200) -> dict:
    conn = _conn(db)
    try:
        filas = [dict(r) for r in conn.execute(
            "SELECT d.*, b.name AS lead FROM diagnosticos d LEFT JOIN businesses b ON b.id = d.business_id "
            "ORDER BY d.creado_en DESC, d.id DESC LIMIT ?", (limite,))]
    finally:
        conn.close()
    for f in filas:
        f["resultado"] = json.loads(f["resultado"]) if f.get("resultado") else None
    if filtro in ("web", "equipo"):
        filas = [f for f in filas if f["origen"] == filtro]
    elif filtro in SEGUIMIENTO:
        filas = [f for f in filas if f["seguimiento"] == filtro]
    hace30 = _txt(_ahora() - timedelta(days=30))
    mes = [f for f in filas if f["creado_en"] >= hace30]
    ok = [f for f in mes if f["estado"] == "ok"]
    resumen = {
        "mes": len(mes),
        "web": sum(1 for f in mes if f["origen"] == "web"),
        "promedio": round(sum(f["puntaje"] for f in ok) / len(ok)) if ok else None,
        "perdiendo": sum(1 for f in ok if f["puntaje"] < 55),
        "sin_whatsapp": sum(1 for f in ok if any(h["clave"] == "whatsapp" and h["estado"] == "mal"
                                                for h in f["resultado"]["hallazgos"])),
        "abiertos": sum(1 for f in mes if f["aperturas"]),
        "nuevos": sum(1 for f in filas if f["seguimiento"] == "nuevo" and f["origen"] == "web"),
    }
    return {"resumen": resumen, "diagnosticos": [_compacto(f) for f in filas]}


def leads_sugeridos(db: str, q: str) -> list[dict]:
    """Leads con sitio, para diagnosticar desde el panel."""
    q = " ".join(str(q or "").split())[:80]
    if len(q) < 2:
        return []
    conn = _conn(db)
    try:
        # LIKE de SQLite solo ignora mayúsculas en ASCII: "ópti" no encuentra "Óptica".
        conn.create_function("dg_plano", 1, _plano, deterministic=True)
        return [dict(r) for r in conn.execute(
            "SELECT id, name, website FROM businesses WHERE dg_plano(name) LIKE ? "
            "ORDER BY (website IS NULL OR website = ''), name LIMIT 8", (f"%{_plano(q)}%",))]
    finally:
        conn.close()


def _plano(texto) -> str:
    """'Óptica Ñandú' -> 'optica nandu'."""
    import unicodedata
    t = unicodedata.normalize("NFKD", str(texto or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))
