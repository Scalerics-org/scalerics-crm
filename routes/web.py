"""Lo que el sitio publico le manda al CRM.

Hoy es una sola cosa: las descargas de la guia de precios de
`scalerics.com/cuanto-cuesta-una-pagina-web/`. Antes de esto la descarga
terminaba en un mail a `contacto@` y no quedaba en ningun lado.

**Es la unica ruta del CRM que puede llamar un navegador cualquiera.** El
`POST /api/leads` que ya existe pide `x-admin-token`, y ese token no puede
viajar en el JavaScript de una pagina publica. El camino limpio hubiera sido
que Web3Forms reenviara el envio servidor a servidor, pero sus webhooks son
funcion del plan PRO.

Asi que se sigue el patron que ya usan `/api/meta/webhook` y
`/api/resend/webhook`: exenta del `before_request` de `dashboard.py` y
validandose sola. Lo que la protege:

- el `Origin` (o el `Referer`) tiene que ser uno de los dominios del sitio;
- un tope de envios por IP y por hora, en memoria del proceso;
- los campos se validan y se recortan antes de tocar la base.

Nada de eso vuelve el endpoint infalsificable —el `Origin` lo pone el
navegador, y un script lo inventa— pero no hay secreto que proteger: lo unico
que se puede hacer es crear leads basura. El tope es lo que acota el dano.

**Las filas entran con `source='web_guia'` a proposito.** Las dos campanas
automaticas filtran por origen (`meta_reminders` por `'meta'`,
`discovery_emails` por `'discovery'`), asi que estas no reciben correo solo.
La pagina promete "un mail con la guia y nada mas" y esto es lo que lo
sostiene: si algun dia se le quiere escribir a esta gente, tiene que ser una
decision explicita, no el efecto de haber creado la fila.
"""

import json
import logging
import os
import re
import time
from collections import defaultdict

from flask import Blueprint, make_response, request, jsonify

from database import get_business_by_email, insert_business, log_activity

logger = logging.getLogger(__name__)

web_bp = Blueprint("web", __name__)

ORIGENES_POR_DEFECTO = ("https://scalerics.com", "https://www.scalerics.com")

# Tope por IP y ventana. Una persona baja la guia una vez; cinco por hora ya es
# holgado para un mismo hogar u oficina detras de un NAT.
_TOPE_POR_IP = 5
_VENTANA_SEG = 3600
_VISITAS: dict[str, list[float]] = defaultdict(list)

_MAIL = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

_LARGO_NOMBRE = 200
_LARGO_MAIL = 200
_LARGO_PROYECTO = 60


def _origenes() -> tuple[str, ...]:
    crudo = os.environ.get("WEB_LEAD_ORIGINS", "")
    if crudo.strip():
        return tuple(o.strip().rstrip("/") for o in crudo.split(",") if o.strip())
    return ORIGENES_POR_DEFECTO


def _origen_valido() -> str | None:
    """El origen permitido de este pedido, o None.

    Se mira `Origin` y, si no viene, `Referer`. Un navegador manda `Origin` en
    todo POST entre origenes; el `Referer` es la red de seguridad para el caso
    de una politica de referrer restrictiva.
    """
    permitidos = _origenes()
    origen = (request.headers.get("Origin") or "").strip().rstrip("/")
    if origen:
        return origen if origen in permitidos else None
    referer = (request.headers.get("Referer") or "").strip()
    for permitido in permitidos:
        if referer.startswith(permitido + "/") or referer == permitido:
            return permitido
    return None


def _cors(respuesta, origen: str):
    respuesta.headers["Access-Control-Allow-Origin"] = origen
    respuesta.headers["Vary"] = "Origin"
    return respuesta


def _ip() -> str:
    reenviada = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return reenviada or (request.remote_addr or "desconocida")


def _paso_el_tope(ip: str) -> bool:
    ahora = time.time()
    recientes = [t for t in _VISITAS[ip] if ahora - t < _VENTANA_SEG]
    _VISITAS[ip] = recientes
    if len(recientes) >= _TOPE_POR_IP:
        return False
    recientes.append(ahora)
    return True


def _buscar_por_mail(db_path: str, mail: str) -> dict | None:
    """El negocio que ya tenga ese mail, sin distinguir mayusculas.

    Antes recorria la tabla entera con `get_all_businesses`: 8.358 negocios,
    ~38 MB de objetos por request, colgando de un endpoint publico. Ahora lo
    resuelve `database.get_business_by_email` en SQL, con indice. Elige el mismo
    negocio que antes cuando el mail esta repetido.
    """
    return get_business_by_email(db_path, mail)


@web_bp.route("/api/web/lead", methods=["OPTIONS"])
def web_lead_preflight():
    origen = _origen_valido()
    if not origen:
        # Sin cabeceras CORS: el navegador corta el POST antes de mandarlo.
        return "", 204
    salida = make_response("", 204)
    salida.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    salida.headers["Access-Control-Allow-Headers"] = "Content-Type"
    salida.headers["Access-Control-Max-Age"] = "86400"
    return _cors(salida, origen)


@web_bp.route("/api/web/lead", methods=["POST"])
def web_lead():
    origen = _origen_valido()
    if not origen:
        logger.warning("Descarga rechazada por origen: %r", request.headers.get("Origin"))
        return jsonify({"error": "origen_no_permitido"}), 403

    if not _paso_el_tope(_ip()):
        logger.warning("Descarga rechazada por tope de IP")
        return _cors(jsonify({"error": "demasiados_envios"}), origen), 429

    datos = request.get_json(silent=True) or {}
    nombre = str(datos.get("name") or "").strip()[:_LARGO_NOMBRE]
    mail = str(datos.get("email") or "").strip()[:_LARGO_MAIL]
    proyecto = str(datos.get("project") or "").strip()[:_LARGO_PROYECTO]

    if not nombre or not _MAIL.match(mail):
        return _cors(jsonify({"error": "datos_invalidos"}), origen), 400

    db = os.environ.get("DB_PATH", "leads.db")

    existente = _buscar_por_mail(db, mail)
    if existente:
        # Bajar un PDF no reescribe el origen de un negocio que ya estaba. Que
        # un cliente o un lead de otra campana se descargue la guia es un dato
        # util, pero es un evento, no un cambio de estado: va al historial y la
        # fila queda como estaba.
        log_activity(db, "web", "guia_descargada", "lead", existente["id"],
                     existente.get("name") or nombre,
                     f"Descargo la guia de precios · proyecto: {proyecto or 'sin indicar'}",
                     user_id=None)
        logger.info("Descarga de la guia sobre un negocio existente: id %s", existente["id"])
        return _cors(jsonify({"ok": True, "id": existente["id"], "nuevo": False}), origen), 200

    biz_id = insert_business(db, {
        "name": nombre,
        "email": mail,
        "category": "Guía de precios",
        "notes": "Descargó la guía de precios de la web",
        "source": "web_guia",
        "score": 60,
        "form_data": json.dumps({"project": proyecto}, ensure_ascii=False),
    })

    if not biz_id:
        # `insert_business` usa INSERT OR IGNORE sobre `phone` UNIQUE y aca no
        # mandamos telefono, asi que no deberia pasar. Si pasa, no se pierde el
        # dato en silencio.
        logger.error("La descarga de la guia no creo fila ni encontro existente: %s", mail)
        return _cors(jsonify({"ok": False, "reason": "no_insertado"}), origen), 200

    log_activity(db, "web", "lead_created", "lead", biz_id, nombre,
                 f"Descargó la guía de precios · proyecto: {proyecto or 'sin indicar'}",
                 user_id=None)
    logger.info("Lead nuevo desde la guia de precios: %s → id %s", mail, biz_id)
    return _cors(jsonify({"ok": True, "id": biz_id, "nuevo": True}), origen), 201
