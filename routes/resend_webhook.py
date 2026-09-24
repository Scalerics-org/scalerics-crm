"""Webhook de Resend: rebotes duros y quejas de spam a la lista de vedados.

Un rebote duro es una direccion que no existe, y seguir mandandole es la forma
mas rapida que hay de quemar un dominio de envio. En un padron raspado de
sitios web los rebotes son inevitables: la direccion publicada puede tener
anios.

La verificacion de firma no es ceremonia. El endpoint tiene que ser publico
—Resend lo llama sin credenciales nuestras— asi que sin firma cualquiera que
sepa la URL puede vedar direcciones, o vedarlas todas, y dejar las dos campanas
mudas sin que nadie entienda por que.

Resend firma con Svix: HMAC-SHA256 sobre "{svix-id}.{svix-timestamp}.{cuerpo}"
usando el secreto en base64 que va despues del prefijo "whsec_".
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import time

from flask import Blueprint, current_app, jsonify, request

from services.email_marketing import registrar_evento
from services.mails_vedados import vedar

logger = logging.getLogger(__name__)

resend_bp = Blueprint("resend", __name__)

# Ventana de tolerancia del timestamp. Sin ella, una peticion capturada una vez
# sirve para siempre.
_TOLERANCIA_SEGUNDOS = 5 * 60


def _db() -> str:
    return current_app.config["DB_PATH"]


def _firma_valida(cuerpo: bytes, cabeceras) -> bool:
    secreto_crudo = os.environ.get("RESEND_WEBHOOK_SECRET", "").strip()
    if not secreto_crudo:
        # Una config a medias no puede dejar el endpoint abierto.
        logger.error("RESEND_WEBHOOK_SECRET sin configurar: se rechaza el webhook")
        return False

    msg_id = cabeceras.get("svix-id", "")
    ts = cabeceras.get("svix-timestamp", "")
    firmas = cabeceras.get("svix-signature", "")
    if not (msg_id and ts and firmas):
        return False

    try:
        if abs(time.time() - int(ts)) > _TOLERANCIA_SEGUNDOS:
            logger.warning("Webhook de Resend con timestamp fuera de ventana")
            return False
    except ValueError:
        return False

    try:
        secreto = base64.b64decode(secreto_crudo.split("_", 1)[-1])
    except Exception:
        logger.error("RESEND_WEBHOOK_SECRET no es base64 valido")
        return False

    esperada = hmac.new(secreto, f"{msg_id}.{ts}.".encode() + cuerpo,
                        hashlib.sha256).digest()
    # La cabecera puede traer varias firmas separadas por espacio, cada una como
    # "v1,<base64>". Alcanza con que una coincida.
    for parte in firmas.split():
        _, _, valor = parte.partition(",")
        try:
            recibida = base64.b64decode(valor)
        except Exception:
            continue
        # compare_digest y no ==: comparar byte a byte filtra el secreto por el
        # tiempo que tarda en fallar.
        if hmac.compare_digest(esperada, recibida):
            return True
    return False


def _motivo_del_evento(tipo: str, datos: dict) -> str:
    """Que veda este evento, o "" si no veda nada.

    Un rebote blando —casilla llena, servidor caido un rato— NO veda: es un
    problema temporal y tirar el contacto seria perder uno bueno. Resend marca
    los permanentes con bounce.type = "Permanent"; ante la duda se veda, porque
    un rebote duro que se sigue intentando cuesta mucho mas que un contacto
    perdido.
    """
    if tipo == "email.complained":
        return "queja_spam"
    if tipo == "email.bounced":
        clase = str((datos.get("bounce") or {}).get("type") or "").lower()
        if clase in ("transient", "soft", "undetermined"):
            return ""
        return "rebote_duro"
    return ""


@resend_bp.route("/api/resend/webhook", methods=["POST"])
def webhook_resend():
    cuerpo = request.get_data() or b""
    if not _firma_valida(cuerpo, request.headers):
        return jsonify({"error": "firma invalida"}), 401

    # De aca en adelante se contesta 200 siempre: un 500 hace que Resend
    # reintente en loop un evento que nunca vamos a poder procesar.
    try:
        evento = json.loads(cuerpo.decode("utf-8"))
    except Exception:
        logger.warning("Webhook de Resend con cuerpo que no es JSON")
        return jsonify({"ok": True, "vedados": 0}), 200

    tipo = str(evento.get("type") or "")
    datos = evento.get("data") or {}
    if not isinstance(datos, dict):
        datos = {}

    # Email marketing: entregado, abierto, clic, rebote o spam sobre la fila del
    # envio. Va antes del vedado y aparte: si falla, vedar igual tiene que andar.
    try:
        registrar_evento(_db(), tipo, datos, evento.get("created_at"))
    except Exception as e:
        logger.error(f"Webhook de Resend: no se pudo registrar el evento {tipo} ({type(e).__name__})")

    motivo = _motivo_del_evento(tipo, datos)
    if not motivo:
        return jsonify({"ok": True, "vedados": 0}), 200

    destinos = datos.get("to") or []
    if isinstance(destinos, str):
        destinos = [destinos]
    detalle = f"{tipo} · {json.dumps(datos.get('bounce') or {}, ensure_ascii=False)}"

    vedados = 0
    for direccion in destinos:
        try:
            if vedar(_db(), direccion, motivo, detalle):
                vedados += 1
        except Exception as e:
            logger.error(f"Webhook de Resend: no se pudo vedar {direccion!r}: {e}")
    return jsonify({"ok": True, "vedados": vedados}), 200
