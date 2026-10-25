"""Control de calidad antes de entregar un sitio (panel `qa`).

Lo que se le escapa a cualquiera al entregar un WordPress o una tienda: el
"noindex" de cuando estaba en prueba, un "Lorem ipsum" o un "Haz clic en el
botón Editar" de Elementor en una página interna, el botón de WhatsApp con el
número escrito "099 123 456" (wa.me no lo entiende), links que siguen
apuntando al sitio de prueba, el © del año pasado, una foto de 4 MB en la home.
Todo eso lo encuentra una revisión automática en 20 segundos; el resto (la
compra de prueba, el formulario que llega al cliente) va en un checklist por
tipo de proyecto, con quién lo tildó y cuándo.

Tres piezas:

- `auditar(url)` recorre el sitio (misma dirección, hasta MAX_PAGINAS, con un
  tope de tiempo) y devuelve las páginas y los hallazgos. Solo hace GET y HEAD,
  y valida en cada redirección que no apunte a la red interna: la URL la carga
  alguien del equipo, pero un link o una redirección del sitio no.
- El catálogo `CATALOGO`: cada hallazgo tiene nivel (bloqueante, aviso, info)
  y el "cómo se arregla", escrito para lo que usamos (WordPress, Elementor,
  WooCommerce).
- El estado de entrega: un proyecto está listo cuando la última revisión no
  tiene bloqueantes y el checklist tiene todos los obligatorios tildados. Un
  hallazgo que no aplica (el cliente no quiere analítica) se marca "no aplica"
  con el motivo y deja de contar.

Todas las fechas en UTC, texto 'YYYY-MM-DD HH:MM:SS'.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import sqlite3
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Callable, Optional
from urllib.parse import parse_qs, unquote, urldefrag, urljoin, urlsplit

from database import _connect
from services.tipos_proyecto import TIPOS_PROYECTO

FORMATO = "%Y-%m-%d %H:%M:%S"
MAX_PAGINAS = 20
MAX_EXTERNOS = 25
MAX_IMAGENES = 30
# El tope de la vista previa en Vercel es 30 s por pedido, y en Fly no conviene
# tener un worker de gunicorn colgado más que eso: la revisión corta y avisa.
TIEMPO_MAX = 20.0
TIMEOUT = 8
MAX_BYTES = 2_000_000
MAX_REDIRECCIONES = 5
HILOS = 6
AGENTE = "ScalericsQA/1.0 (+https://scalerics.com)"
LARGO_NOMBRE = 120
LARGO_URL = 500
LARGO_NOTA = 500
TOPE_DONDE = 15
TOPE_REVISIONES = 30  # por proyecto; las más viejas se borran

# Lo que pesa cada nivel en el puntaje (100 = nada que arreglar).
PESOS = {"bloqueante": 12, "aviso": 4, "info": 1}
NIVELES = tuple(PESOS)
PAGINA_LENTA_MS = 3000
IMAGEN_PESADA = 400_000
SSL_DIAS_AVISO = 21


class NoSePuede(Exception):
    pass


def _ahora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _txt(d: datetime) -> str:
    return d.strftime(FORMATO)


# ── catálogo ─────────────────────────────────────────────────────────────────
# clave -> (nivel, título, cómo se arregla). El título es lo que se ve en la
# lista; el "cómo" se abre debajo. La clave se guarda (no aplica, historial):
# no se renombra.

CATALOGO: dict[str, tuple[str, str, str]] = {
    "inaccesible": ("bloqueante", "El sitio no abre",
                    "Revisá que el dominio apunte al hosting (DNS) y que el sitio no esté en modo mantenimiento."),
    "ssl_invalido": ("bloqueante", "El certificado SSL es inválido o venció",
                     "En hPanel: SSL › instalar o renovar el certificado gratuito. Esperá unos minutos y volvé a revisar."),
    "sin_https": ("bloqueante", "El sitio no abre con https",
                  "Instalá el SSL y en WordPress cambiá Ajustes › Generales a https (o usá Really Simple SSL)."),
    "noindex": ("bloqueante", "Google tiene prohibido indexar el sitio (noindex)",
                "Quedó de cuando estaba en prueba. WordPress: Ajustes › Lectura › destildar \"Disuadir a los motores "
                "de búsqueda\". Si es Yoast o Rank Math, revisá también la página."),
    "robots_bloquea": ("bloqueante", "robots.txt bloquea todo el sitio",
                       "Sacá el \"Disallow: /\" del robots.txt (o el ajuste de WordPress que lo genera)."),
    "relleno": ("bloqueante", "Quedó texto de relleno o de la plantilla",
                "Reemplazá cada texto por el del cliente. En Elementor, buscalo en la página y editá el widget."),
    "error_php": ("bloqueante", "Se ven errores de PHP en la página",
                  "Desactivá WP_DEBUG_DISPLAY en wp-config.php y arreglá el plugin o tema que tira el error."),
    "pagina_rota": ("bloqueante", "Hay links internos a páginas que no existen",
                    "Corregí el link en el menú o la página, o redirigí la dirección vieja (plugin Redirection)."),
    "whatsapp_mal": ("bloqueante", "El botón de WhatsApp está mal armado",
                     "El formato es https://wa.me/598XXXXXXXX: código de país, sin el 0, sin +, espacios ni guiones."),
    "links_a_prueba": ("bloqueante", "Hay links que apuntan al sitio de prueba",
                       "Pasá Better Search Replace de la dirección de prueba a la definitiva (con backup antes)."),
    "sin_viewport": ("bloqueante", "No está preparado para celular (falta viewport)",
                     "El tema tiene que llevar <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">."),
    "link_externo_roto": ("aviso", "Hay links a otros sitios que no funcionan",
                          "Actualizá o sacá el link. Pasa mucho con redes sociales del cliente que cambiaron de nombre."),
    "canonical_otro": ("aviso", "La etiqueta canonical apunta a otra dirección",
                       "Revisá el canonical en Yoast o Rank Math: tiene que ser la dirección definitiva del sitio."),
    "http_sin_redirigir": ("aviso", "La versión http no redirige a https",
                           "Forzá https desde hPanel o con una regla en .htaccess."),
    "ssl_por_vencer": ("aviso", "El certificado SSL vence pronto",
                       "Revisá que la renovación automática esté prendida en el hosting."),
    "telefono_mal": ("aviso", "Un link de teléfono está mal armado",
                     "El formato es tel:+598XXXXXXXX, con el número completo."),
    "contenido_mixto": ("aviso", "La página carga cosas por http dentro de https",
                        "Pasá Better Search Replace de http:// a https:// en la base y regenerá el CSS de Elementor."),
    "sin_titulo": ("aviso", "Páginas sin título",
                   "Poné un título en Yoast o Rank Math: es lo que sale en Google y en la pestaña."),
    "titulo_generico": ("aviso", "Títulos genéricos (Inicio, Home, Mi sitio)",
                        "Que diga qué es y dónde: \"Ferretería Norte · Herramientas en Salto\"."),
    "titulo_repetido": ("aviso", "Varias páginas con el mismo título",
                        "Cada página necesita su título; Google las confunde si se repite."),
    "sin_descripcion": ("aviso", "Páginas sin meta descripción",
                        "Escribí 140 a 160 caracteres en Yoast o Rank Math: es el texto debajo del título en Google."),
    "sin_h1": ("aviso", "Páginas sin título principal (H1)",
               "Cada página tiene que tener un único encabezado H1 con el tema de la página."),
    "img_sin_alt": ("aviso", "Imágenes sin texto alternativo",
                    "Cargá el texto alternativo en la biblioteca de medios: ayuda en Google y a quien no ve."),
    "img_pesada": ("aviso", "Imágenes muy pesadas",
                   "Pasalas a WebP y achicalas (Imagify, ShortPixel o Squoosh). Más de 400 KB frena el celular."),
    "pagina_lenta": ("aviso", "Páginas que tardan más de 3 segundos",
                     "Prendé la caché (LiteSpeed Cache en Hostinger), optimizá imágenes y sacá plugins que no se usan."),
    "sin_favicon": ("aviso", "No tiene ícono en la pestaña (favicon)",
                    "Apariencia › Personalizar › Identidad del sitio › Ícono del sitio, con el logo del cliente."),
    "sin_og": ("aviso", "Al compartir el link no sale foto",
               "Cargá la imagen para redes en Yoast o Rank Math (og:image). Es lo que se ve al mandar el link por WhatsApp."),
    "sin_sitemap": ("aviso", "No tiene sitemap",
                    "Yoast y Rank Math lo generan solos. Después cargalo en Search Console."),
    "sin_analitica": ("aviso", "No mide visitas (sin Google Analytics, Tag Manager ni Pixel)",
                      "Instalá GA4 con Site Kit o Tag Manager, con la cuenta del cliente y acceso nuestro."),
    "sin_contacto": ("aviso", "No hay forma de contactar",
                     "Sumá el botón de WhatsApp, un formulario o al menos el teléfono con link."),
    "copyright_viejo": ("aviso", "El © del pie es de un año viejo",
                        "Poné el año actual o, mejor, que lo calcule solo (shortcode o función del tema)."),
    "titulo_largo": ("info", "Títulos de más de 65 caracteres",
                     "Google corta los títulos largos: lo importante va adelante."),
    "varios_h1": ("info", "Páginas con más de un H1",
                  "No es grave, pero conviene un solo H1 por página."),
    "sin_idioma": ("info", "Falta el idioma de la página",
                   "El tema tiene que poner lang=\"es\" en la etiqueta <html> (Ajustes › Generales › Idioma)."),
    "revision_cortada": ("info", "La revisión no llegó a recorrer todo",
                         "Se revisaron las primeras páginas por el tope de tiempo o cantidad. Lo que se ve es real, "
                         "pero puede haber más."),
}

# Lo que sí o sí se prueba a mano: la máquina no puede comprar ni ver si el
# mail le llegó al cliente. clave -> (texto, obligatorio). La clave se guarda.
_COMUNES_WEB = [
    ("form_probado", "Formulario de contacto probado: el mail le llega al cliente (y no a spam)", True),
    ("wa_probado", "Botón de WhatsApp probado desde un celular: abre el chat correcto", True),
    ("revisado_celular", "Revisado en iPhone, Android y escritorio", True),
    ("textos_ok", "El cliente aprobó textos, precios, horarios y dirección", True),
    ("accesos", "Dominio y hosting a nombre del cliente; accesos guardados en Credenciales", True),
    ("analitica_cuenta", "Analytics y Search Console con la cuenta del cliente y acceso nuestro", False),
    ("google_maps", "Ficha de Google (Business Profile) apunta al sitio nuevo", False),
    ("backup", "Backup completo hecho después del último cambio", True),
    ("capacitacion", "Capacitación al cliente y guía de cómo editar entregada", False),
]

CHECKLISTS: dict[str, list[tuple[str, str, bool]]] = {
    "web": _COMUNES_WEB,
    "ecommerce": _COMUNES_WEB + [
        ("compra_prueba", "Compra de prueba de punta a punta con cada medio de pago, y reembolsada", True),
        ("mails_pedido", "Mails de pedido (recibido, en proceso, completado) con la marca del cliente", True),
        ("envios", "Costos y zonas de envío probados (Montevideo e interior)", True),
        ("precios_stock", "Precios, IVA y stock de los productos revisados con el cliente", True),
        ("politicas", "Políticas de cambio, envío y privacidad publicadas", True),
    ],
    "automatizacion": [
        ("punta_a_punta", "Probado con datos reales del cliente de punta a punta", True),
        ("si_falla", "Si falla, avisa a alguien y se puede reintentar", True),
        ("credenciales", "Tokens y claves del cliente en Credenciales, no en el código", True),
        ("doc_uso", "Guía de uso entregada al cliente", True),
        ("capacitacion", "Capacitación hecha con quien lo va a usar", False),
    ],
    "aMedida": [
        ("tests", "Tests en verde y deploy reproducible", True),
        ("usuarios", "Usuarios y permisos del cliente creados", True),
        ("backup_restore", "Backup automático y restauración probada", True),
        ("credenciales", "Tokens y claves en variables de entorno y en Credenciales", True),
        ("doc_uso", "Documentación de uso y de cómo se deploya", True),
        ("revisado_celular", "Revisado en celular y escritorio", False),
    ],
    "otro": [
        ("textos_ok", "El cliente aprobó lo entregado", True),
        ("accesos", "Accesos entregados y guardados en Credenciales", True),
        ("backup", "Backup hecho", False),
    ],
}


def checklist_de(tipo: str) -> list[dict]:
    return [{"clave": c, "texto": t, "obligatorio": o} for c, t, o in CHECKLISTS.get(tipo, CHECKLISTS["otro"])]


# ── URLs y red ───────────────────────────────────────────────────────────────

def _ip_publica(ip: str) -> bool:
    try:
        d = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return d.is_global and not d.is_multicast


def _resolver(host: str) -> list:
    return [info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)]


def normalizar_url(url: str, obligatoria: bool = True) -> str:
    """https:// si falta, sin espacios ni #. '' si no hay y no es obligatoria."""
    url = (url or "").strip()
    if not url:
        if obligatoria:
            raise NoSePuede("Falta la dirección del sitio")
        return ""
    if "://" not in url:
        url = "https://" + url
    if len(url) > LARGO_URL:
        raise NoSePuede("La dirección es demasiado larga")
    partes = urlsplit(url)
    if partes.scheme not in ("http", "https") or not partes.hostname:
        raise NoSePuede("La dirección tiene que ser http o https")
    if partes.username or partes.password:
        raise NoSePuede("La dirección no puede llevar usuario ni contraseña")
    if not _host_permitido(url):
        raise NoSePuede("La dirección apunta a una red interna")
    return urldefrag(url)[0]


def _host_permitido(url: str, resolver: Optional[Callable] = None) -> bool:
    """Que la URL no apunte a la red interna (la de Fly incluida)."""
    resolver = resolver or _resolver
    partes = urlsplit(url)
    host = (partes.hostname or "").lower()
    if not host or host == "localhost" or host.endswith((".internal", ".local", ".localhost")):
        return False
    try:
        if partes.port not in (None, 80, 443, 8080, 8443):
            return False
    except ValueError:
        return False
    try:
        ipaddress.ip_address(host)
        ips = [host]
    except ValueError:
        try:
            ips = resolver(host)
        except OSError:
            return True  # que falle el pedido con "no resuelve", no con "red interna"
    return bool(ips) and all(_ip_publica(ip) for ip in ips)


def _pedir(url: str, metodo: str = "GET") -> dict:
    """GET o HEAD siguiendo redirecciones a mano, validando cada salto.

    Devuelve {codigo, url, texto, ms, bytes, headers} o {error, url}. `headers`
    va con las claves en minúscula. El texto solo en GET y si es HTML.
    """
    import requests

    inicio = time.monotonic()
    actual = url
    for _ in range(MAX_REDIRECCIONES + 1):
        if not _host_permitido(actual):
            return {"error": "La dirección apunta a una red interna", "url": actual}
        try:
            r = requests.request(metodo, actual, timeout=TIMEOUT, allow_redirects=False, stream=True,
                                 headers={"User-Agent": AGENTE, "Accept": "text/html,*/*;q=0.8",
                                          "Accept-Language": "es-UY,es;q=0.9"})
        except requests.exceptions.SSLError:
            return {"error": "ssl", "url": actual}
        except requests.exceptions.Timeout:
            return {"error": f"No contestó en {TIMEOUT} s", "url": actual}
        except requests.exceptions.ConnectionError as e:
            texto = str(e).lower()
            if "name or service not known" in texto or "nodename nor servname" in texto or "getaddrinfo" in texto:
                return {"error": "El dominio no existe", "url": actual}
            return {"error": "No se pudo conectar", "url": actual}
        except requests.exceptions.RequestException:
            return {"error": "No se pudo conectar", "url": actual}
        try:
            headers = {k.lower(): v for k, v in r.headers.items()}
            if r.is_redirect and headers.get("location"):
                actual = urljoin(actual, headers["location"])
                continue
            texto, leidos = "", 0
            tipo = headers.get("content-type", "")
            if metodo == "GET" and ("html" in tipo or "xml" in tipo or "text/plain" in tipo or not tipo):
                trozos = []
                for trozo in r.iter_content(65536):
                    trozos.append(trozo)
                    leidos += len(trozo)
                    if leidos >= MAX_BYTES:
                        break
                texto = b"".join(trozos).decode(r.encoding or "utf-8", errors="replace")
            return {"codigo": r.status_code, "url": actual, "texto": texto, "headers": headers,
                    "ms": int((time.monotonic() - inicio) * 1000), "bytes": leidos}
        finally:
            r.close()
    return {"error": "Demasiadas redirecciones", "url": actual}


def _vence_ssl(host: str, puerto: int = 443) -> Optional[datetime]:
    if not _host_permitido("https://" + host):
        return None
    ctx = ssl.create_default_context()
    with socket.create_connection((host, puerto), timeout=TIMEOUT) as s:
        with ctx.wrap_socket(s, server_hostname=host) as t:
            cert = t.getpeercert()
    if not cert or "notAfter" not in cert:
        return None
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), timezone.utc).replace(tzinfo=None)


def _base_host(host: str) -> str:
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def mismo_sitio(a: str, b: str) -> bool:
    return _base_host(urlsplit(a).hostname or "") == _base_host(urlsplit(b).hostname or "")


# Lo que no es una página: no se recorre (sí se revisa que exista si es imagen).
_NO_PAGINA = re.compile(r"\.(jpe?g|png|gif|webp|avif|svg|ico|pdf|zip|rar|mp4|mp3|webm|mov|docx?|xlsx?|pptx?|"
                        r"css|js|json|xml|txt|woff2?|ttf|eot)$", re.I)
# WordPress y WooCommerce: direcciones que no son contenido o que cambian el carrito.
_NO_RECORRER = re.compile(r"/(wp-admin|wp-login\.php|wp-json|xmlrpc\.php|feed|comments/feed|cart|carrito|checkout|"
                          r"finalizar-compra|my-account|mi-cuenta)(/|$)|[?&](add-to-cart|replytocom|s|orderby|"
                          r"filter_[a-z_]+|min_price|max_price)=", re.I)


def _recorrible(url: str) -> bool:
    p = urlsplit(url)
    return p.scheme in ("http", "https") and not _NO_PAGINA.search(p.path) and not _NO_RECORRER.search(url)


# ── lectura de una página ────────────────────────────────────────────────────

class _Lector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.titulo, self._en_titulo = None, False
        self.descripcion = None
        self.robots = ""
        self.viewport = False
        self.lang = ""
        self.h1 = 0
        self.imgs: list[tuple[str, bool]] = []  # (src, tiene alt)
        self.links: list[str] = []
        self.icono = False
        self.og: dict[str, str] = {}
        self.canonical = None
        self.recursos: list[str] = []  # src/href que carga la página
        self.forms = 0
        self._ignorar = 0
        self.texto: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "").strip()
        elif tag == "title":
            self._en_titulo = True
            self.titulo = self.titulo or ""
        elif tag == "meta":
            nombre = (a.get("name") or a.get("property") or "").lower()
            if nombre == "description":
                self.descripcion = a.get("content", "").strip()
            elif nombre in ("robots", "googlebot"):
                self.robots += " " + a.get("content", "").lower()
            elif nombre == "viewport":
                self.viewport = "width" in a.get("content", "").lower()
            elif nombre.startswith("og:"):
                self.og[nombre] = a.get("content", "").strip()
        elif tag == "link":
            rel = a.get("rel", "").lower().split()
            if "icon" in rel or "apple-touch-icon" in rel:
                self.icono = True
            if "canonical" in rel:
                self.canonical = a.get("href", "").strip()
            if "stylesheet" in rel and a.get("href"):
                self.recursos.append(a["href"])
        elif tag == "h1":
            self.h1 += 1
        elif tag == "img":
            src = a.get("src") or a.get("data-src") or ""
            if src and not src.startswith("data:"):
                self.imgs.append((src, "alt" in a))
                self.recursos.append(src)
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"].strip())
        elif tag in ("script", "iframe", "source", "video", "audio") and a.get("src"):
            self.recursos.append(a["src"])
        elif tag == "form":
            self.forms += 1
        if tag in ("script", "style", "noscript", "template"):
            self._ignorar += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._en_titulo = False
        if tag in ("script", "style", "noscript", "template") and self._ignorar:
            self._ignorar -= 1

    def handle_data(self, data):
        if self._en_titulo:
            self.titulo += data
        elif not self._ignorar and data.strip():
            self.texto.append(data.strip())


# En minúscula. Lo que dejan WordPress, Elementor, WooCommerce y las plantillas.
RELLENO = (
    "lorem ipsum", "dolor sit amet", "hello world!", "¡hola, mundo!", "hola mundo!", "sample page",
    "página de ejemplo", "just another wordpress site", "otro sitio realizado con wordpress",
    "producto de ejemplo", "sample product", "add your heading text here", "agrega tu texto de encabezado aquí",
    "añade tu texto de título aquí", "click edit button to change this text", "haz clic en el botón editar",
    "haga clic en el botón editar", "texto de ejemplo", "aquí va el texto", "acá va el texto", "your text here",
    "tu texto aquí", "tu@email.com", "tuemail@", "email@example.com", "correo@ejemplo", "ejemplo@ejemplo",
    "+598 99 999 999", "099 999 999", "123 456 7890", "1234 calle", "123 main street",
)
_ERROR_PHP = re.compile(r"<b>\s*(warning|notice|deprecated|fatal error|parse error)\s*</b>\s*:"
                        r"|there has been a critical error on (this|your) website"
                        r"|ha habido un error crítico en (esta|tu) web"
                        r"|error establishing a database connection"
                        r"|error al establecer una conexión con la base de datos", re.I)
_COPYRIGHT = re.compile(r"(?:©|\(c\)|copyright)\s*(?:\d{4}\s*[-–]\s*)?(\d{4})", re.I)
_ANALITICA = ("googletagmanager.com", "google-analytics.com", "gtag(", "fbq(", "connect.facebook.net",
              "plausible.io", "clarity.ms", "static.hotjar.com")
_HOSTS_PRUEBA = (".hostingersite.com", ".temp.domains", ".local", ".test", ".localhost", "localhost",
                 "127.0.0.1", "example.com", ".ngrok.io", ".ngrok-free.app", ".wpcomstaging.com", ".instawp.xyz")


def es_host_de_prueba(host: str, url_prueba: str = "") -> bool:
    host = (host or "").lower()
    if url_prueba and _base_host(host) == _base_host(urlsplit(url_prueba).hostname or ""):
        return True
    if host.startswith(("staging.", "stage.", "dev.", "test.", "prueba.", "demo.")):
        return True
    return any(host == h.lstrip(".") or host.endswith(h) for h in _HOSTS_PRUEBA)


def revisar_whatsapp(href: str) -> Optional[str]:
    """Por qué un link de WhatsApp está mal, o None si está bien."""
    p = urlsplit(href)
    host = (p.hostname or "").lower()
    if p.scheme == "whatsapp":
        crudo = (parse_qs(p.query).get("phone") or [""])[0]
    elif host in ("wa.me", "www.wa.me"):
        partes = [x for x in p.path.split("/") if x]
        if partes and partes[0] in ("message", "c", "qr", "catalog"):
            return None  # link corto de WhatsApp Business: no lleva número
        crudo = unquote(partes[0]) if partes else ""
    elif host in ("api.whatsapp.com", "web.whatsapp.com", "whatsapp.com", "www.whatsapp.com"):
        if "send" not in p.path:
            return None  # el canal, una comunidad
        crudo = (parse_qs(p.query, keep_blank_values=True).get("phone") or [""])[0]
    else:
        return None
    crudo = crudo.strip()
    if not crudo:
        return "No tiene número"
    if not crudo.isdigit():
        return f"\"{crudo}\" tiene símbolos o espacios: van solo números"
    if crudo.startswith("0"):
        return f"\"{crudo}\" no tiene código de país (Uruguay: 598 y el número sin el 0)"
    if crudo.startswith("598") and len(crudo) != 11:
        return f"\"{crudo}\" no es un número uruguayo completo (598 y 8 cifras)"
    if not 10 <= len(crudo) <= 15:
        return f"\"{crudo}\" no tiene el largo de un número con código de país"
    return None


def revisar_telefono(href: str) -> Optional[str]:
    numero = unquote(href.split(":", 1)[1] if ":" in href else "")
    cifras = re.sub(r"\D", "", numero)
    if len(cifras) < 8:
        return f"\"{numero or 'vacío'}\" no es un número completo"
    return None


def leer_pagina(url: str, respuesta: dict, url_prueba: str = "") -> dict:
    """Lo que interesa de una página ya bajada. Pura: los tests le pasan HTML."""
    html = respuesta.get("texto") or ""
    final = respuesta.get("url") or url
    lector = _Lector()
    try:
        lector.feed(html)
        lector.close()
    except Exception:  # HTML roto: lo que se haya leído alcanza
        pass
    texto = re.sub(r"\s+", " ", " ".join(lector.texto))
    texto_min = texto.lower()
    links_int, links_ext, wa, tel, prueba, mail = set(), set(), [], [], set(), False
    for href in lector.links:
        h = href.lower()
        if h.startswith("mailto:"):
            mail = True
            continue
        if h.startswith("tel:"):
            tel.append(href)
            continue
        if h.startswith(("javascript:", "#", "data:", "sms:")):
            continue
        absoluto = urldefrag(urljoin(final, href))[0]
        p = urlsplit(absoluto)
        if p.scheme == "whatsapp" or (p.hostname or "").lower() in (
                "wa.me", "www.wa.me", "api.whatsapp.com", "web.whatsapp.com", "whatsapp.com", "www.whatsapp.com"):
            wa.append(href)
            continue
        if p.scheme not in ("http", "https"):
            continue
        if es_host_de_prueba(p.hostname or "", url_prueba) and not mismo_sitio(absoluto, final):
            prueba.add(absoluto)
        elif mismo_sitio(absoluto, final):
            links_int.add(absoluto)
        else:
            links_ext.add(absoluto)
    https = urlsplit(final).scheme == "https"
    mixtos = sorted({urljoin(final, r) for r in lector.recursos if r.lower().startswith("http://")}) if https else []
    for r in lector.recursos:
        absoluto = urljoin(final, r)
        host = urlsplit(absoluto).hostname or ""
        if es_host_de_prueba(host, url_prueba) and not mismo_sitio(absoluto, final):
            prueba.add(absoluto)
    anio = None
    for m in _COPYRIGHT.finditer(texto):
        anio = max(anio or 0, int(m.group(1)))
    headers = respuesta.get("headers") or {}
    return {
        "url": final,
        "codigo": respuesta.get("codigo"),
        "ms": respuesta.get("ms"),
        "bytes": respuesta.get("bytes") or len(html),
        "titulo": re.sub(r"\s+", " ", lector.titulo).strip() if lector.titulo is not None else None,
        "descripcion": lector.descripcion,
        "noindex": "noindex" in lector.robots or "noindex" in headers.get("x-robots-tag", "").lower(),
        "viewport": lector.viewport,
        "lang": lector.lang,
        "h1": lector.h1,
        "imgs": sorted({urljoin(final, s) for s, _ in lector.imgs}),
        "imgs_sin_alt": sorted({urljoin(final, s) for s, alt in lector.imgs if not alt}),
        "links_internos": sorted(links_int),
        "links_externos": sorted(links_ext),
        "whatsapp": wa,
        "telefonos": tel,
        "mail": mail,
        "forms": lector.forms,
        "icono": lector.icono,
        "og_imagen": bool(lector.og.get("og:image")),
        "canonical": urljoin(final, lector.canonical) if lector.canonical else None,
        "mixtos": mixtos,
        "links_a_prueba": sorted(prueba),
        "relleno": [f for f in RELLENO if f in texto_min],
        "error_php": bool(_ERROR_PHP.search(html)),
        "analitica": any(a in html for a in _ANALITICA),
        "copyright": anio,
    }


# ── la revisión ──────────────────────────────────────────────────────────────

def _robots_bloquea(texto: str) -> bool:
    """Si el grupo de `User-agent: *` tiene un `Disallow: /` pelado."""
    agentes, en_reglas, bloquea = [], False, False
    for linea in texto.splitlines():
        linea = linea.split("#", 1)[0].strip()
        if ":" not in linea:
            continue
        campo, valor = (x.strip() for x in linea.split(":", 1))
        campo = campo.lower()
        if campo == "user-agent":
            if en_reglas:  # empieza otro grupo
                agentes, en_reglas = [], False
            agentes.append(valor)
        elif campo in ("disallow", "allow"):
            en_reglas = True
            if campo == "disallow" and valor == "/" and "*" in agentes:
                bloquea = True
    return bloquea


def _hallazgo(clave: str, donde: list, detalle: str = "") -> dict:
    nivel, titulo, como = CATALOGO[clave]
    vistos, lista = set(), []
    for d in donde:
        d = d if isinstance(d, dict) else {"url": d}
        k = (d.get("url"), d.get("extra"))
        if k not in vistos:
            vistos.add(k)
            lista.append(d)
    return {"clave": clave, "nivel": nivel, "titulo": titulo, "como": como, "detalle": detalle,
            "n": len(lista), "donde": lista[:TOPE_DONDE]}


def auditar(url: str, url_prueba: str = "", obtener: Optional[Callable] = None,
            vence_ssl: Optional[Callable] = None, ahora: Optional[datetime] = None,
            tiempo_max: float = TIEMPO_MAX) -> dict:
    """Recorre el sitio y devuelve {url, paginas, hallazgos, ssl_vence, cortada, segundos}.

    `obtener(url, metodo)` y `vence_ssl(host)` se inyectan en los tests; por
    defecto salen a internet.
    """
    crudo = obtener or _pedir

    def obtener(u, metodo="GET"):
        # Un link raro del sitio (una URL mal escrita, un puerto inválido) no
        # puede tirar abajo la revisión entera.
        try:
            return crudo(u, metodo)
        except Exception:
            return {"error": "No se pudo conectar", "url": u}

    vence_ssl = vence_ssl or _vence_ssl
    ahora = ahora or _ahora()
    inicio = time.monotonic()

    def queda() -> bool:
        return time.monotonic() - inicio < tiempo_max

    hallazgos: list[dict] = []
    portada = obtener(url, "GET")
    if portada.get("error") == "ssl":
        return {"url": url, "paginas": [], "hallazgos": [_hallazgo("ssl_invalido", [url])], "ssl_vence": None,
                "cortada": False, "segundos": round(time.monotonic() - inicio, 1)}
    if portada.get("error") or (portada.get("codigo") or 0) >= 400:
        motivo = portada.get("error") or f"Contestó {portada.get('codigo')}"
        return {"url": url, "paginas": [], "hallazgos": [_hallazgo("inaccesible", [{"url": url, "extra": motivo}],
                                                                    motivo)],
                "ssl_vence": None, "cortada": False, "segundos": round(time.monotonic() - inicio, 1)}

    paginas = [leer_pagina(url, portada, url_prueba)]
    base = paginas[0]["url"]
    vistos = {url, base}
    cola = [u for u in paginas[0]["links_internos"] if _recorrible(u)]
    rotas: list[dict] = []
    cortada = False
    with ThreadPoolExecutor(max_workers=HILOS) as ex:
        while cola and len(paginas) < MAX_PAGINAS:
            if not queda():
                cortada = True
                break
            # De a HILOS por vuelta: así el tope de tiempo se mira seguido.
            tanda, resto = [], []
            for u in cola:
                if u in vistos:
                    continue
                if len(tanda) < HILOS and len(tanda) + len(paginas) < MAX_PAGINAS:
                    vistos.add(u)
                    tanda.append(u)
                else:
                    resto.append(u)
            cola = resto
            if not tanda:
                break
            for u, r in zip(tanda, ex.map(lambda x: obtener(x, "GET"), tanda)):
                if r.get("error") or (r.get("codigo") or 0) >= 400:
                    rotas.append({"url": u, "extra": r.get("error") or f"Contestó {r.get('codigo')}"})
                    continue
                if not mismo_sitio(r.get("url") or u, base):
                    continue  # redirige afuera (un link corto, una red social)
                pag = leer_pagina(u, r, url_prueba)
                if pag["url"] in vistos and pag["url"] != u:
                    continue  # ya la teníamos con otra dirección
                vistos.add(pag["url"])
                paginas.append(pag)
                cola.extend(x for x in pag["links_internos"] if x not in vistos and _recorrible(x))
        if any(u not in vistos for u in cola):
            cortada = True

        # Lo del sitio entero: robots, sitemap, favicon, https.
        raiz = f"{urlsplit(base).scheme}://{urlsplit(base).netloc}"
        robots = obtener(raiz + "/robots.txt", "GET") if queda() else {}
        robots_txt = robots.get("texto", "") if robots.get("codigo") == 200 else ""
        if robots_txt and _robots_bloquea(robots_txt):
            hallazgos.append(_hallazgo("robots_bloquea", [raiz + "/robots.txt"]))
        hay_sitemap = "sitemap:" in robots_txt.lower()
        for camino in ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml"):
            if hay_sitemap or not queda():
                break
            r = obtener(raiz + camino, "GET")
            hay_sitemap = r.get("codigo") == 200 and "<" in (r.get("texto") or "")
        if not hay_sitemap and queda():
            hallazgos.append(_hallazgo("sin_sitemap", [raiz]))
        if not paginas[0]["icono"] and queda():
            if obtener(raiz + "/favicon.ico", "HEAD").get("codigo") != 200:
                hallazgos.append(_hallazgo("sin_favicon", [base]))
        if urlsplit(base).scheme == "http":
            hallazgos.append(_hallazgo("sin_https", [base]))
        elif queda():
            r = obtener("http://" + urlsplit(base).netloc + "/", "HEAD")
            if r.get("codigo") and r["codigo"] < 400 and urlsplit(r.get("url") or "").scheme == "http":
                hallazgos.append(_hallazgo("http_sin_redirigir", ["http://" + urlsplit(base).netloc + "/"]))

        # Links a otros sitios e imágenes: HEAD en paralelo, con tope.
        externos: dict[str, str] = {}
        for p in paginas:
            for e in p["links_externos"]:
                externos.setdefault(e, p["url"])
        externos = dict(list(externos.items())[:MAX_EXTERNOS])
        rotos = []
        if externos and queda():
            for (e, desde), r in zip(externos.items(), ex.map(lambda x: _revisar_externo(obtener, x), externos)):
                if r:
                    rotos.append({"url": e, "extra": f"{r} · en {desde}"})
        if rotos:
            hallazgos.append(_hallazgo("link_externo_roto", rotos))
        imagenes = sorted({i for p in paginas for i in p["imgs"]})[:MAX_IMAGENES]
        pesadas = []
        if imagenes and queda():
            for i, r in zip(imagenes, ex.map(lambda x: obtener(x, "HEAD"), imagenes)):
                largo = (r.get("headers") or {}).get("content-length", "")
                if largo.isdigit() and int(largo) > IMAGEN_PESADA:
                    pesadas.append({"url": i, "extra": f"{int(largo) / 1_000_000:.1f} MB".replace(".", ",")})
        if pesadas:
            hallazgos.append(_hallazgo("img_pesada", sorted(pesadas, key=lambda d: d["extra"], reverse=True)))

    ssl_vence = None
    if urlsplit(base).scheme == "https" and queda():
        try:
            ssl_vence = vence_ssl(urlsplit(base).hostname)
        except (OSError, ssl.SSLError, ValueError):
            ssl_vence = None
        if ssl_vence and (ssl_vence - ahora).days < SSL_DIAS_AVISO:
            hallazgos.append(_hallazgo("ssl_por_vencer", [base], f"Vence el {ssl_vence.date().isoformat()}"))

    if not queda():
        cortada = True
    hallazgos.extend(_hallazgos_de_paginas(paginas, rotas, base, url_prueba, ahora))
    if cortada:
        hallazgos.append(_hallazgo("revision_cortada", [base], f"Se revisaron {len(paginas)} páginas"))
    orden = {n: i for i, n in enumerate(NIVELES)}
    hallazgos.sort(key=lambda h: (orden[h["nivel"]], list(CATALOGO).index(h["clave"])))
    return {"url": base, "paginas": [_resumen_pagina(p) for p in paginas], "hallazgos": hallazgos,
            "ssl_vence": _txt(ssl_vence) if ssl_vence else None, "cortada": cortada,
            "segundos": round(time.monotonic() - inicio, 1)}


# Redes que contestan 999, 403 o 429 a cualquier robot: no se dan por rotas.
_NO_SE_PUEDE_SABER = (401, 403, 405, 429, 999)


def _revisar_externo(obtener: Callable, url: str) -> Optional[str]:
    r = obtener(url, "HEAD")
    if r.get("codigo") in (405, 501) or (r.get("error") and r["error"] not in ("El dominio no existe", "ssl")):
        r = obtener(url, "GET")
    if r.get("error") == "El dominio no existe":
        return "El dominio no existe"
    if r.get("error"):
        return None  # un corte o un timeout no alcanza para decir que está roto
    codigo = r.get("codigo") or 0
    if codigo >= 400 and codigo not in _NO_SE_PUEDE_SABER:
        return f"Contesta {codigo}"
    return None


def _hallazgos_de_paginas(paginas: list, rotas: list, base: str, url_prueba: str, ahora: datetime) -> list:
    h = []
    portada = paginas[0]

    def donde(cond, extra=None):
        return [{"url": p["url"], **({"extra": extra(p)} if extra else {})} for p in paginas if cond(p)]

    if any(p["noindex"] for p in paginas):
        h.append(_hallazgo("noindex", donde(lambda p: p["noindex"])))
    rell = donde(lambda p: p["relleno"], lambda p: ", ".join(f"\"{f}\"" for f in p["relleno"][:3]))
    if rell:
        h.append(_hallazgo("relleno", rell))
    if any(p["error_php"] for p in paginas):
        h.append(_hallazgo("error_php", donde(lambda p: p["error_php"])))
    if rotas:
        h.append(_hallazgo("pagina_rota", rotas))
    wa_mal = [{"url": p["url"], "extra": m} for p in paginas for w in p["whatsapp"]
              for m in [revisar_whatsapp(w)] if m]
    if wa_mal:
        h.append(_hallazgo("whatsapp_mal", wa_mal))
    # Un canonical al sitio de prueba es lo mismo que un link al sitio de prueba.
    canon = [{"url": p["url"], "extra": p["canonical"]} for p in paginas
             if p["canonical"] and not mismo_sitio(p["canonical"], base)]
    canon_prueba = [c for c in canon if es_host_de_prueba(urlsplit(c["extra"]).hostname or "", url_prueba)]
    prueba = [{"url": p["url"], "extra": x} for p in paginas for x in p["links_a_prueba"]] + canon_prueba
    if prueba:
        h.append(_hallazgo("links_a_prueba", prueba))
    if not portada["viewport"]:
        h.append(_hallazgo("sin_viewport", [portada["url"]]))
    canon_otro = [c for c in canon if c not in canon_prueba]
    if canon_otro:
        h.append(_hallazgo("canonical_otro", canon_otro))
    tel_mal = [{"url": p["url"], "extra": m} for p in paginas for t in p["telefonos"]
               for m in [revisar_telefono(t)] if m]
    if tel_mal:
        h.append(_hallazgo("telefono_mal", tel_mal))
    mixtos = [{"url": p["url"], "extra": m} for p in paginas for m in p["mixtos"]]
    if mixtos:
        h.append(_hallazgo("contenido_mixto", mixtos))
    if any(p["titulo"] in (None, "") for p in paginas):
        h.append(_hallazgo("sin_titulo", donde(lambda p: not p["titulo"])))
    genericos = {"inicio", "home", "mi sitio", "mi blog", "sin título", "untitled", "document", "página",
                 "just another wordpress site", "otro sitio realizado con wordpress"}
    gen = donde(lambda p: (p["titulo"] or "").strip(" -|·").lower() in genericos, lambda p: p["titulo"])
    if gen:
        h.append(_hallazgo("titulo_generico", gen))
    por_titulo: dict[str, list] = {}
    for p in paginas:
        if p["titulo"]:
            por_titulo.setdefault(p["titulo"], []).append(p["url"])
    rep = [{"url": u, "extra": t} for t, us in por_titulo.items() if len(us) > 1 for u in us]
    if rep:
        h.append(_hallazgo("titulo_repetido", rep))
    sin_desc = donde(lambda p: not p["descripcion"])
    if sin_desc:
        h.append(_hallazgo("sin_descripcion", sin_desc))
    sin_h1 = donde(lambda p: p["h1"] == 0)
    if sin_h1:
        h.append(_hallazgo("sin_h1", sin_h1))
    alt = [{"url": i, "extra": f"en {p['url']}"} for p in paginas for i in p["imgs_sin_alt"]]
    if alt:
        h.append(_hallazgo("img_sin_alt", alt, f"{len({a['url'] for a in alt})} imágenes"))
    lentas = donde(lambda p: (p["ms"] or 0) > PAGINA_LENTA_MS,
                   lambda p: f"{p['ms'] / 1000:.1f} s".replace(".", ","))
    if lentas:
        h.append(_hallazgo("pagina_lenta", lentas))
    if not portada["og_imagen"]:
        h.append(_hallazgo("sin_og", [portada["url"]]))
    if not any(p["analitica"] for p in paginas):
        h.append(_hallazgo("sin_analitica", [portada["url"]]))
    if not any(p["whatsapp"] or p["forms"] or p["telefonos"] or p["mail"] for p in paginas):
        h.append(_hallazgo("sin_contacto", [portada["url"]]))
    anios = [p["copyright"] for p in paginas if p["copyright"]]
    if anios and max(anios) < ahora.year:
        h.append(_hallazgo("copyright_viejo", [portada["url"]], f"Dice {max(anios)}"))
    largos = donde(lambda p: len(p["titulo"] or "") > 65, lambda p: f"{len(p['titulo'])} caracteres")
    if largos:
        h.append(_hallazgo("titulo_largo", largos))
    varios = donde(lambda p: p["h1"] > 1, lambda p: f"{p['h1']} H1")
    if varios:
        h.append(_hallazgo("varios_h1", varios))
    if not portada["lang"]:
        h.append(_hallazgo("sin_idioma", [portada["url"]]))
    return h


def _resumen_pagina(p: dict) -> dict:
    return {k: p[k] for k in ("url", "codigo", "ms", "bytes", "titulo", "h1")} | {
        "imgs": len(p["imgs"]), "links": len(p["links_internos"]) + len(p["links_externos"])}


# ── puntaje y estado ─────────────────────────────────────────────────────────

def puntaje(hallazgos: list, no_aplica: set) -> dict:
    vivos = [h for h in hallazgos if h["clave"] not in no_aplica]
    cuenta = {n: sum(1 for h in vivos if h["nivel"] == n) for n in NIVELES}
    return {"puntaje": max(0, 100 - sum(PESOS[n] * c for n, c in cuenta.items())), **cuenta}


def comparar(antes: Optional[list], ahora: list) -> dict:
    """Qué apareció y qué se arregló desde la revisión anterior (por clave)."""
    if antes is None:
        return {"hay_anterior": False, "nuevos": [], "resueltos": [], "titulos": {}}
    a = {h["clave"] for h in antes if h["clave"] != "revision_cortada"}
    b = {h["clave"] for h in ahora if h["clave"] != "revision_cortada"}
    nuevos = [k for k in CATALOGO if k in b - a]
    resueltos = [k for k in CATALOGO if k in a - b]
    return {"hay_anterior": True, "nuevos": nuevos, "resueltos": resueltos,
            "titulos": {k: CATALOGO[k][1] for k in nuevos + resueltos}}


def estado(proyecto: dict, ultima: Optional[dict], checklist: list) -> str:
    """listo · falta_checklist · bloqueado · sin_revisar · entregado."""
    if proyecto.get("entregado_en"):
        return "entregado"
    if proyecto.get("url") and not ultima:
        return "sin_revisar"
    if ultima and ultima["bloqueante"]:
        return "bloqueado"
    if any(i["obligatorio"] and not i["hecho"] for i in checklist):
        return "falta_checklist"
    return "listo"


# ── base de datos ────────────────────────────────────────────────────────────

def init_qa(conn: sqlite3.Connection) -> None:
    """Tablas del panel. La llama `database.init_db` con su conexión."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS qa_proyectos (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre        TEXT NOT NULL,
            url           TEXT NOT NULL DEFAULT '',
            url_prueba    TEXT NOT NULL DEFAULT '',
            tipo          TEXT NOT NULL DEFAULT 'web',
            business_id   INTEGER,
            creado_por    TEXT,
            creado_en     TEXT NOT NULL,
            entregado_en  TEXT,
            entregado_por TEXT
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS qa_revisiones (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            proyecto_id  INTEGER NOT NULL,
            url          TEXT NOT NULL,
            hecha_en     TEXT NOT NULL,
            hecha_por    TEXT,
            segundos     REAL,
            paginas      INTEGER NOT NULL DEFAULT 0,
            resultado    TEXT NOT NULL
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_qa_rev ON qa_revisiones (proyecto_id, id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS qa_checklist (
            proyecto_id  INTEGER NOT NULL,
            clave        TEXT NOT NULL,
            por          TEXT,
            en           TEXT NOT NULL,
            PRIMARY KEY (proyecto_id, clave)
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS qa_no_aplica (
            proyecto_id  INTEGER NOT NULL,
            clave        TEXT NOT NULL,
            motivo       TEXT NOT NULL,
            por          TEXT,
            en           TEXT NOT NULL,
            PRIMARY KEY (proyecto_id, clave)
        )""")


def _limpiar(datos: dict) -> dict:
    nombre = (datos.get("nombre") or "").strip()
    if not nombre:
        raise NoSePuede("Falta el nombre del proyecto")
    if len(nombre) > LARGO_NOMBRE:
        raise NoSePuede("El nombre es demasiado largo")
    tipo = datos.get("tipo") or "web"
    if tipo not in TIPOS_PROYECTO:
        raise NoSePuede("Ese tipo de proyecto no existe")
    con_sitio = tipo in ("web", "ecommerce")
    url = normalizar_url(datos.get("url"), obligatoria=con_sitio)
    url_prueba = normalizar_url(datos.get("url_prueba"), obligatoria=False) if datos.get("url_prueba") else ""
    if url and url_prueba and mismo_sitio(url, url_prueba):
        raise NoSePuede("La dirección de prueba tiene que ser distinta de la definitiva")
    bid = datos.get("business_id")
    try:
        bid = int(bid) if bid not in (None, "") else None
    except (TypeError, ValueError):
        raise NoSePuede("El cliente no es válido")
    return {"nombre": nombre, "url": url, "url_prueba": url_prueba, "tipo": tipo, "business_id": bid}


def crear(db: str, datos: dict, quien: str) -> dict:
    d = _limpiar(datos)
    conn = _connect(db)
    try:
        if d["business_id"] and not conn.execute("SELECT 1 FROM businesses WHERE id = ?",
                                                 (d["business_id"],)).fetchone():
            raise NoSePuede("Ese cliente no existe")
        pid = conn.execute(
            "INSERT INTO qa_proyectos (nombre, url, url_prueba, tipo, business_id, creado_por, creado_en) "
            "VALUES (?,?,?,?,?,?,?)",
            (d["nombre"], d["url"], d["url_prueba"], d["tipo"], d["business_id"], quien, _txt(_ahora()))).lastrowid
        conn.commit()
    finally:
        conn.close()
    return detalle(db, pid)


def editar(db: str, pid: int, datos: dict) -> dict:
    actual = _fila(db, pid)
    d = _limpiar({**actual, **datos})
    conn = _connect(db)
    try:
        conn.execute("UPDATE qa_proyectos SET nombre=?, url=?, url_prueba=?, tipo=?, business_id=? WHERE id=?",
                     (d["nombre"], d["url"], d["url_prueba"], d["tipo"], d["business_id"], pid))
        conn.commit()
    finally:
        conn.close()
    return detalle(db, pid)


def borrar(db: str, pid: int) -> None:
    _fila(db, pid)
    conn = _connect(db)
    try:
        for t in ("qa_revisiones", "qa_checklist", "qa_no_aplica"):
            conn.execute(f"DELETE FROM {t} WHERE proyecto_id = ?", (pid,))
        conn.execute("DELETE FROM qa_proyectos WHERE id = ?", (pid,))
        conn.commit()
    finally:
        conn.close()


def _fila(db: str, pid: int) -> dict:
    conn = _connect(db)
    try:
        f = conn.execute("SELECT * FROM qa_proyectos WHERE id = ?", (pid,)).fetchone()
    finally:
        conn.close()
    if not f:
        raise NoSePuede("Ese proyecto no existe")
    return dict(f)


def guardar_revision(db: str, pid: int, resultado: dict, quien: str, en: Optional[datetime] = None) -> int:
    conn = _connect(db)
    try:
        rid = conn.execute(
            "INSERT INTO qa_revisiones (proyecto_id, url, hecha_en, hecha_por, segundos, paginas, resultado) "
            "VALUES (?,?,?,?,?,?,?)",
            (pid, resultado["url"], _txt(en or _ahora()), quien, resultado.get("segundos"),
             len(resultado.get("paginas") or []), json.dumps(resultado, ensure_ascii=False))).lastrowid
        conn.execute("DELETE FROM qa_revisiones WHERE proyecto_id = ? AND id NOT IN "
                     "(SELECT id FROM qa_revisiones WHERE proyecto_id = ? ORDER BY id DESC LIMIT ?)",
                     (pid, pid, TOPE_REVISIONES))
        conn.commit()
    finally:
        conn.close()
    return rid


def revisar(db: str, pid: int, quien: str, **kw) -> dict:
    p = _fila(db, pid)
    if not p["url"]:
        raise NoSePuede("Este proyecto no tiene sitio para revisar: es solo checklist")
    resultado = auditar(p["url"], p["url_prueba"], **kw)
    guardar_revision(db, pid, resultado, quien)
    return detalle(db, pid)


def marcar(db: str, pid: int, clave: str, hecho: bool, quien: str) -> dict:
    p = _fila(db, pid)
    if clave not in {i["clave"] for i in checklist_de(p["tipo"])}:
        raise NoSePuede("Ese punto no está en el checklist")
    conn = _connect(db)
    try:
        if hecho:
            conn.execute("INSERT OR REPLACE INTO qa_checklist (proyecto_id, clave, por, en) VALUES (?,?,?,?)",
                         (pid, clave, quien, _txt(_ahora())))
        else:
            conn.execute("DELETE FROM qa_checklist WHERE proyecto_id = ? AND clave = ?", (pid, clave))
        conn.commit()
    finally:
        conn.close()
    return detalle(db, pid)


def no_aplica(db: str, pid: int, clave: str, motivo: Optional[str], quien: str) -> dict:
    """Con motivo marca el hallazgo como "no aplica"; sin motivo lo vuelve a contar."""
    _fila(db, pid)
    if clave not in CATALOGO:
        raise NoSePuede("Ese hallazgo no existe")
    conn = _connect(db)
    try:
        if motivo is None:
            conn.execute("DELETE FROM qa_no_aplica WHERE proyecto_id = ? AND clave = ?", (pid, clave))
        else:
            motivo = motivo.strip()
            if not motivo:
                raise NoSePuede("Escribí por qué no aplica: queda para el que revise después")
            if len(motivo) > LARGO_NOTA:
                raise NoSePuede("El motivo es demasiado largo")
            conn.execute("INSERT OR REPLACE INTO qa_no_aplica (proyecto_id, clave, motivo, por, en) "
                         "VALUES (?,?,?,?,?)", (pid, clave, motivo, quien, _txt(_ahora())))
        conn.commit()
    finally:
        conn.close()
    return detalle(db, pid)


def entregar(db: str, pid: int, quien: str, deshacer: bool = False) -> dict:
    p = _fila(db, pid)
    if not deshacer:
        d = detalle(db, pid)
        if d["estado"] not in ("listo",):
            raise NoSePuede("Todavía no está listo: faltan bloqueantes o puntos obligatorios del checklist")
    conn = _connect(db)
    try:
        conn.execute("UPDATE qa_proyectos SET entregado_en = ?, entregado_por = ? WHERE id = ?",
                     (None, None, pid) if deshacer else (_txt(_ahora()), quien, p["id"]))
        conn.commit()
    finally:
        conn.close()
    return detalle(db, pid)


def _armar(conn, p: dict, con_historial: bool) -> dict:
    pid = p["id"]
    revs = conn.execute("SELECT * FROM qa_revisiones WHERE proyecto_id = ? ORDER BY id DESC LIMIT ?",
                        (pid, TOPE_REVISIONES if con_historial else 2)).fetchall()
    na = {f["clave"]: dict(f) for f in conn.execute("SELECT clave, motivo, por, en FROM qa_no_aplica "
                                                    "WHERE proyecto_id = ?", (pid,))}
    hechos = {f["clave"]: dict(f) for f in conn.execute("SELECT clave, por, en FROM qa_checklist "
                                                        "WHERE proyecto_id = ?", (pid,))}
    checklist = [{**i, "hecho": i["clave"] in hechos, **({k: hechos[i["clave"]][k] for k in ("por", "en")}
                                                         if i["clave"] in hechos else {})}
                 for i in checklist_de(p["tipo"])]
    ultima = anterior = None
    if revs:
        r0 = json.loads(revs[0]["resultado"])
        ultima = {"id": revs[0]["id"], "hecha_en": revs[0]["hecha_en"], "hecha_por": revs[0]["hecha_por"],
                  "segundos": revs[0]["segundos"], "url": revs[0]["url"], **puntaje(r0["hallazgos"], set(na))}
        if con_historial:
            ultima.update({"hallazgos": [{**h, "no_aplica": na.get(h["clave"])} for h in r0["hallazgos"]],
                           "paginas": r0["paginas"], "ssl_vence": r0.get("ssl_vence"), "cortada": r0.get("cortada")})
        if len(revs) > 1:
            anterior = json.loads(revs[1]["resultado"])["hallazgos"]
        ultima["cambios"] = comparar(anterior, r0["hallazgos"])
    obligatorios = [i for i in checklist if i["obligatorio"]]
    out = {**p, "tipo_nombre": TIPOS_PROYECTO.get(p["tipo"], p["tipo"]), "ultima": ultima,
           "checklist_hechos": sum(1 for i in checklist if i["hecho"]), "checklist_total": len(checklist),
           "obligatorios_pendientes": sum(1 for i in obligatorios if not i["hecho"])}
    out["estado"] = estado(p, ultima, checklist)
    if con_historial:
        out["checklist"] = checklist
        out["no_aplica"] = na
        out["historial"] = [{"id": r["id"], "hecha_en": r["hecha_en"], "hecha_por": r["hecha_por"],
                             "paginas": r["paginas"],
                             **puntaje(json.loads(r["resultado"])["hallazgos"], set(na))} for r in revs]
    return out


def detalle(db: str, pid: int) -> dict:
    p = _fila(db, pid)
    conn = _connect(db)
    try:
        if p["business_id"]:
            f = conn.execute("SELECT name FROM businesses WHERE id = ?", (p["business_id"],)).fetchone()
            p["cliente"] = f["name"] if f else None
        return _armar(conn, p, True)
    finally:
        conn.close()


def listar(db: str) -> list:
    conn = _connect(db)
    try:
        filas = conn.execute("SELECT q.*, b.name AS cliente FROM qa_proyectos q "
                             "LEFT JOIN businesses b ON b.id = q.business_id ORDER BY q.id DESC").fetchall()
        return [_armar(conn, dict(f), False) for f in filas]
    finally:
        conn.close()


ORDEN_ESTADOS = ("bloqueado", "sin_revisar", "falta_checklist", "listo", "entregado")


def resumen(proyectos: list) -> dict:
    activos = [p for p in proyectos if p["estado"] != "entregado"]
    con_rev = [p["ultima"]["puntaje"] for p in activos if p["ultima"]]
    return {"activos": len(activos),
            "por_estado": {e: sum(1 for p in proyectos if p["estado"] == e) for e in ORDEN_ESTADOS},
            "bloqueantes": sum(p["ultima"]["bloqueante"] for p in activos if p["ultima"]),
            "puntaje_medio": round(sum(con_rev) / len(con_rev)) if con_rev else None}


def clientes(db: str) -> list:
    conn = _connect(db)
    try:
        return [dict(f) for f in conn.execute(
            "SELECT id, name FROM businesses WHERE crm_status IN ('cerrado','en_desarrollo','finalizado','acepto') "
            "ORDER BY name COLLATE NOCASE LIMIT 300")]
    finally:
        conn.close()


def texto_resumen(d: dict) -> str:
    """El resumen para pegar en Notion o mandarle al equipo. Texto plano."""
    lineas = [f"Control de calidad · {d['nombre']}"]
    if d.get("url"):
        lineas.append(d["url"])
    u = d.get("ultima")
    if u:
        lineas.append(f"Revisión del {u['hecha_en'][:16]} UTC: puntaje {u['puntaje']}/100, "
                      f"{u['bloqueante']} bloqueantes, {u['aviso']} avisos.")
        for h in u.get("hallazgos") or []:
            if h.get("no_aplica"):
                continue
            lineas.append(f"- [{h['nivel']}] {h['titulo']}" + (f" ({h['n']})" if h["n"] > 1 else ""))
    pend = [i["texto"] for i in d.get("checklist") or [] if not i["hecho"]]
    if pend:
        lineas.append("Falta probar a mano:")
        lineas.extend(f"- {t}" for t in pend)
    return "\n".join(lineas)
