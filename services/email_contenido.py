"""Ver el mail que se le mandó a cada uno (panel Email marketing).

Pedido de Juan (15/9): "Hay alguna forma de en email marketing, ver el mail
que se le manda a cada uno?"

El cuerpo del mail NO se guarda en la base. Sale, en este orden, de:

1. Resend: `GET /emails/{id}` en el momento, para los envíos con id de Resend.
   Con timeout, una pausa mínima entre pedidos y un cache en memoria de cinco
   minutos para que abrir y cerrar el mismo mail no repita el pedido.
2. La misma función que lo mandó (`send_discovery_email` o
   `send_meta_lead_reminder`), corrida dentro de `capturar_envio`: arma el mail
   con los datos guardados del negocio y del contacto sin mandarlo. Sirve para
   los históricos y para cuando Resend ya no lo tiene. Va con aviso, porque la
   plantilla o los datos del negocio pueden haber cambiado desde el envío.
3. Si no hay ninguna de las dos, se dice que no está disponible, y por qué.

Todo HTML que sale de acá pasa por `sanitizar_html`. La pantalla además lo
muestra en un iframe con sandbox vacío: son dos capas a propósito.
"""

import html as _html
import json
import os
import re
import sqlite3
import threading
import time
from html.parser import HTMLParser

import requests

from services.email_marketing import TIPOS, a_montevideo

AVISO_RECONSTRUIDO = ("Reconstruido con la plantilla actual: puede diferir un poco "
                      "del mail original.")
NO_DISPONIBLE = "El contenido de este mail no está disponible."

_URL_EMAIL = "https://api.resend.com/emails/"
_TIMEOUT = 8.0
_TTL_CACHE = 300.0
_TOPE_CACHE = 100
# Resend permite 10 pedidos por segundo por equipo, compartidos con las
# campañas que están mandando: entre dos consultas se deja un margen.
_PAUSA_MINIMA = 0.2
_TOPE_HTML = 1_000_000
_ID_RESEND = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
_RECONSTRUIBLES = ("discovery", "recordatorio_meta")

_cache: dict[str, tuple[float, dict]] = {}
_candado = threading.Lock()
_ultimo_pedido = [float("-inf")]


def limpiar_cache() -> None:
    with _candado:
        _cache.clear()
        _ultimo_pedido[0] = float("-inf")


# ── sanitización ─────────────────────────────────────────────────────────────

# Se van con todo lo que tengan adentro.
_FUERA_CON_CONTENIDO = {"script", "iframe", "object", "embed", "applet", "noscript",
                        "template", "frame", "frameset"}
# Se va la etiqueta; el texto de adentro, si hay, se queda.
_FUERA = {"base", "link", "form", "input", "button", "textarea", "select", "option",
          "portal", "meta"}
_ATRIBUTOS_URL = {"href", "src", "action", "formaction", "background", "poster",
                  "xlink:href", "srcset", "lowsrc", "dynsrc", "data", "longdesc"}
_ESQUEMAS_PELIGROSOS = ("javascript:", "vbscript:", "livescript:", "data:text/html")
_ATRIBUTOS_FUERA = {"target", "ping", "srcdoc", "formaction", "action", "http-equiv"}
_TOKEN_BAJA = re.compile(r"/baja/[A-Za-z0-9_-]+")


def ocultar_token_baja(texto: str | None) -> str:
    """El link de baja lleva el token del lead: en la vista previa no hace falta."""
    return _TOKEN_BAJA.sub("/baja/(token)", texto or "")


def _plano(valor: str) -> str:
    """Sin espacios ni caracteres de control y en minúscula: 'Java\\tScript:' -> 'javascript:'."""
    return re.sub(r"[\x00-\x20]+", "", _html.unescape(valor or "")).lower()


def _es_pixel(attrs: list) -> bool:
    medidas = {(n or "").lower(): (v or "").strip().lower() for n, v in attrs}
    chico = ("0", "1", "0px", "1px")
    return medidas.get("width") in chico or medidas.get("height") in chico


class _Limpiador(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.salida: list[str] = []
        self._saltando = 0
        self._en_style = False

    def _abrir(self, tag: str, attrs: list, cerrado: bool) -> None:
        tag = tag.lower()
        if self._saltando:
            if tag in _FUERA_CON_CONTENIDO and not cerrado:
                self._saltando += 1
            return
        if tag in _FUERA_CON_CONTENIDO:
            if not cerrado:
                self._saltando = 1
            return
        if tag == "meta" and any((n or "").lower() == "charset" for n, _ in attrs) \
                and not any((n or "").lower() == "http-equiv" for n, _ in attrs):
            self.salida.append('<meta charset="utf-8">')
            return
        if tag in _FUERA or (tag == "img" and _es_pixel(attrs)):
            # El pixel de apertura: cargarlo marcaría el mail como abierto.
            return

        limpios, link = [], None
        for nombre, valor in attrs:
            n = (nombre or "").lower()
            v = valor if valor is not None else ""
            if n.startswith("on") or n in _ATRIBUTOS_FUERA:
                continue
            plano = _plano(v)
            if n in _ATRIBUTOS_URL and plano.startswith(_ESQUEMAS_PELIGROSOS):
                continue
            if n == "style" and any(p in plano for p in ("javascript:", "expression(", "behavior:",
                                                          "-moz-binding")):
                continue
            if n == "href":
                # La vista previa no navega ni dispara el conteo de clics: el
                # link queda como texto y la dirección, en el título.
                link = v
                continue
            if tag == "a" and n == "title":
                continue
            limpios.append((n, v))
        if tag == "a" and link:
            limpios.append(("title", "Link: " + ocultar_token_baja(link)))

        partes = "".join(f' {n}="{_html.escape(v, quote=True)}"' for n, v in limpios)
        self.salida.append(f"<{tag}{partes}{' /' if cerrado else ''}>")
        if tag == "style" and not cerrado:
            self._en_style = True

    def handle_starttag(self, tag, attrs):
        self._abrir(tag, attrs, cerrado=False)

    def handle_startendtag(self, tag, attrs):
        self._abrir(tag, attrs, cerrado=True)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._saltando:
            if tag in _FUERA_CON_CONTENIDO:
                self._saltando -= 1
            return
        if tag in _FUERA_CON_CONTENIDO or tag in _FUERA or tag == "img":
            return
        if tag == "style":
            self._en_style = False
        self.salida.append(f"</{tag}>")

    def handle_data(self, data):
        if self._saltando:
            return
        if self._en_style:
            data = re.sub(r"(?i)javascript:|expression\(|behavior:|-moz-binding", "", data)
        self.salida.append(data)

    def handle_entityref(self, name):
        if not self._saltando:
            self.salida.append(f"&{name};")

    def handle_charref(self, name):
        if not self._saltando:
            self.salida.append(f"&#{name};")

    def handle_decl(self, decl):
        if not self._saltando and decl.lower().startswith("doctype"):
            self.salida.append(f"<!{decl}>")

    # Comentarios (los condicionales de Outlook pueden traer cualquier cosa),
    # instrucciones de proceso y CDATA: afuera.
    def handle_comment(self, data):
        pass

    def handle_pi(self, data):
        pass

    def unknown_decl(self, data):
        pass


def sanitizar_html(texto: str | None) -> str:
    """Quita scripts, handlers `on*`, esquemas `javascript:` y la navegación.

    No es la única defensa: la pantalla lo carga en un iframe con sandbox vacío
    (sin scripts ni mismo origen). Esto es para que un HTML raro no dependa
    solo del navegador.
    """
    if not texto:
        return ""
    limpiador = _Limpiador()
    limpiador.feed(str(texto)[:_TOPE_HTML])
    limpiador.close()
    return ocultar_token_baja("".join(limpiador.salida))


# ── Resend ───────────────────────────────────────────────────────────────────

def _pedir_a_resend(resend_id: str, api_key: str, http, reloj, dormir) -> tuple[dict | None, str | None]:
    """(datos, motivo). Solo se cachea lo que salió bien."""
    ahora = reloj()
    with _candado:
        guardado = _cache.get(resend_id)
        if guardado and guardado[0] > ahora:
            return guardado[1], None
        espera = _PAUSA_MINIMA - (ahora - _ultimo_pedido[0])
    if espera > 0:
        dormir(espera)
    with _candado:
        _ultimo_pedido[0] = reloj()
    try:
        r = http.get(_URL_EMAIL + resend_id, headers={"Authorization": f"Bearer {api_key}"},
                     timeout=_TIMEOUT)
    except requests.RequestException:
        return None, "No se pudo consultar a Resend en este momento."
    if r.status_code == 404:
        return None, "Resend ya no tiene el contenido de este mail."
    if r.status_code == 429:
        return None, "Resend pidió esperar un momento antes de volver a consultar."
    if r.status_code != 200:
        return None, f"Resend contestó con un error ({r.status_code})."
    try:
        cuerpo = r.json()
    except ValueError:
        cuerpo = None
    if not isinstance(cuerpo, dict):
        return None, "Resend devolvió una respuesta que no se pudo leer."
    datos = {
        "html": cuerpo.get("html") if isinstance(cuerpo.get("html"), str) else None,
        "text": cuerpo.get("text") if isinstance(cuerpo.get("text"), str) else None,
        "from": str(cuerpo.get("from") or ""),
        "subject": str(cuerpo.get("subject") or ""),
    }
    if not datos["html"] and not datos["text"]:
        return None, "Resend no devolvió el contenido de este mail."
    with _candado:
        if len(_cache) >= _TOPE_CACHE:
            del _cache[min(_cache, key=lambda k: _cache[k][0])]
        _cache[resend_id] = (reloj() + _TTL_CACHE, datos)
    return datos, None


# ── reconstrucción ───────────────────────────────────────────────────────────

def _reconstruir(db_path: str, fila: sqlite3.Row) -> tuple[dict | None, str | None]:
    """(mail capturado, motivo si no se pudo). (None, None) si el tipo no se reconstruye."""
    tipo = fila["tipo"]
    if tipo not in _RECONSTRUIBLES:
        return None, None
    bid, numero = fila["business_id"], fila["numero"]
    if not bid:
        return None, "Falta el negocio de este envío: no se puede reconstruir el mail."
    if not numero:
        return None, "Falta el número de contacto de este envío: no se puede reconstruir el mail."

    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        negocio = conn.execute("SELECT id, name, category, email, form_data FROM businesses WHERE id = ?",
                               (bid,)).fetchone()
        if tipo == "discovery":
            registro = conn.execute(
                "SELECT token, NULL AS estado FROM discovery_reminders "
                "WHERE business_id = ? AND numero = ? ORDER BY id LIMIT 1", (bid, numero)).fetchone()
        else:
            # Un lead puede tener el contacto N de dos estados distintos: se
            # toma el que salió más cerca de la fecha de este envío.
            registro = conn.execute(
                "SELECT token, estado FROM meta_reminders WHERE business_id = ? AND numero = ? "
                "ORDER BY ABS(julianday(sent_at) - julianday(?)), id LIMIT 1",
                (bid, numero, fila["enviado_at"])).fetchone()
    finally:
        conn.close()
    if not negocio:
        return None, "El negocio de este envío ya no está en el CRM: no se puede reconstruir el mail."
    if not registro:
        return None, (f"No está el registro del contacto {numero} de este negocio: "
                      f"no se puede reconstruir el mail.")

    from services.email_service import capturar_envio, send_discovery_email, send_meta_lead_reminder

    destino = fila["destinatario"] or negocio["email"] or ""
    baja = os.environ.get("CRM_URL", "https://scalerics-crm.fly.dev").rstrip("/") + "/baja/" + registro["token"]
    with capturar_envio() as capturados:
        if tipo == "discovery":
            send_discovery_email(destino, negocio["name"] or "", negocio["category"] or "", baja, int(numero))
        else:
            from services.meta_reminders import CLAVE_NEGOCIO, CLAVE_RUBRO, _texto
            try:
                campos = json.loads(negocio["form_data"] or "{}")
            except (ValueError, TypeError):
                campos = {}
            if not isinstance(campos, dict):
                campos = {}
            send_meta_lead_reminder(destino, _texto(campos, CLAVE_NEGOCIO), _texto(campos, CLAVE_RUBRO),
                                    baja, int(numero), registro["estado"] or "sin_contactar")
    if not capturados:
        if tipo == "discovery":
            return None, ("Falta DISCOVERY_FROM_EMAIL en el servidor: no se puede reconstruir "
                          "el mail de discovery.")
        return None, "No se pudo reconstruir el mail."
    return capturados[-1], None


# ── lo que pide la pantalla ──────────────────────────────────────────────────

def contenido_de_envio(db_path: str, envio_id: int, api_key: str, http=None,
                       reloj=time.monotonic, dormir=time.sleep) -> dict | None:
    """El mail de la fila `envio_id` de emails_enviados, o None si no existe."""
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        fila = conn.execute(
            "SELECT id, resend_id, tipo, destinatario, asunto, business_id, numero, enviado_at, "
            "estado, origen FROM emails_enviados WHERE id = ?", (envio_id,)).fetchone()
    finally:
        conn.close()
    if not fila:
        return None

    local = a_montevideo(fila["enviado_at"])
    res = {
        "id": fila["id"], "asunto": fila["asunto"] or "", "remitente": "",
        "destinatario": fila["destinatario"] or "",
        "fecha_local": local.strftime("%d/%m/%Y %H:%M") if local else "",
        "tipo": fila["tipo"], "tipo_etiqueta": TIPOS.get(fila["tipo"], fila["tipo"]),
        "estado": fila["estado"], "origen": fila["origen"],
        "fuente": "no_disponible", "avisos": [], "html": None, "text": None,
        "motivo": NO_DISPONIBLE,
    }

    resend_id = fila["resend_id"]
    if resend_id and _ID_RESEND.match(resend_id):
        if not api_key:
            res["avisos"].append("No hay clave de Resend (RESEND_API_KEY) en el servidor para traer "
                                 "el mail original.")
        else:
            datos, motivo = _pedir_a_resend(resend_id, api_key, http or requests, reloj, dormir)
            if datos:
                res.update(fuente="resend", motivo=None, remitente=datos["from"],
                           html=sanitizar_html(datos["html"]) if datos["html"] else None,
                           text=ocultar_token_baja(datos["text"]) if datos["text"] else None)
                if datos["subject"]:
                    res["asunto"] = datos["subject"]
                return res
            res["avisos"].append(motivo)

    capturado, motivo = _reconstruir(db_path, fila)
    if capturado:
        res.update(fuente="reconstruido", motivo=None, remitente=capturado.get("from") or "",
                   html=sanitizar_html(capturado.get("html")) if capturado.get("html") else None,
                   text=ocultar_token_baja(capturado.get("text")) if capturado.get("text") else None)
        if capturado.get("subject"):
            res["asunto"] = capturado["subject"]
        res["avisos"].append(AVISO_RECONSTRUIDO)
        return res
    if motivo:
        res["motivo"] = motivo
    return res
