"""Endpoints de los borradores de LinkedIn.

La autenticacion la resuelve el before_request de dashboard.py: cualquier
request a /api/ con el header x-admin-token correcto pasa sin sesion. La
excepcion es /marcar, que se abre desde un link de un mail y por eso lleva
su token de un solo uso en la query.
"""

import json
import os
import secrets
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from database import (
    create_job,
    get_linkedin_post_by_token,
    get_linkedin_posts_by_lote,
    update_linkedin_post,
)
from services.email_service import send_linkedin_drafts, send_linkedin_failure
from services.job_service import get_worker

linkedin_bp = Blueprint("linkedin", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


@linkedin_bp.route("/api/linkedin/generar", methods=["POST"])
def api_linkedin_generar():
    db_path = _db()
    worker = get_worker()
    if worker is None:
        return jsonify({"ok": False, "error": "worker no inicializado"}), 503

    # El lote se genera aca y viaja dentro del payload desde el principio. Si
    # se lo agregara despues con un UPDATE, el worker podria levantar el job
    # entre el INSERT y el UPDATE y quedarse sin el.
    lote = secrets.token_urlsafe(12)
    payload = {
        "db_path": db_path,
        "lote": lote,
        "ahora": datetime.now().isoformat(),
    }

    # Sin cuerpo, el job arma los dos educativos del mail programado. Con
    # `contexto_manual`, arma un solo post sobre trabajo real a partir de lo
    # que escriba Juan: el CRM no guarda nada de los proyectos entregados.
    data = request.get_json(silent=True) or {}
    manual = (data.get("contexto_manual") or "").strip()
    if manual:
        payload["contexto_manual"] = manual
        payload["imagen_url"] = (data.get("imagen_url") or "").strip()

    job_id = create_job(db_path, "linkedin", json.dumps(payload))

    return jsonify({"ok": True, "job_id": job_id, "lote": lote}), 202


@linkedin_bp.route("/api/linkedin/enviar", methods=["POST"])
def api_linkedin_enviar():
    data = request.get_json() or {}
    lote = data.get("lote")
    imagenes = {i["id"]: i.get("png_b64", "") for i in data.get("imagenes", [])}

    if not lote:
        return jsonify({"ok": False, "error": "lote requerido"}), 400

    posts = [p for p in get_linkedin_posts_by_lote(_db(), lote)
             if p["estado"] == "generado"]
    if not posts:
        return jsonify({"ok": False, "error": "no hay borradores para ese lote"}), 404

    borradores = [{
        "id": p["id"],
        "tipo": p["tipo"],
        "texto": p["texto"],
        "fuente_desc": p["fuente_desc"] or f"{p['fuente_tipo']} #{p['fuente_id']}",
        "aviso": p["aviso"] or "",
        "marcar_token": p["marcar_token"],
        "png_b64": imagenes.get(p["id"], ""),
    } for p in posts]

    destino = os.environ.get("LINKEDIN_MAIL_TO", "scalerics@gmail.com")
    base_url = os.environ.get("CRM_URL", "https://scalerics-crm.fly.dev")
    ok = send_linkedin_drafts(destino, borradores, base_url,
                              aviso_cooldown=bool(data.get("aviso_cooldown")))

    for p in posts:
        update_linkedin_post(_db(), p["id"], estado="enviado")

    return jsonify({"ok": ok, "enviados": len(borradores)}), 200


@linkedin_bp.route("/api/linkedin/fallo", methods=["POST"])
def api_linkedin_fallo():
    """Lo llama el cron cuando se dio por vencido.

    Sin esto, una corrida fallida solo deja el mail de "workflow failed" que
    manda GitHub, que se pierde entre el resto de las notificaciones del repo.
    Un fallo del cron tiene que llegar a la misma casilla que los borradores,
    porque es la que se mira los martes y viernes a la manana.
    """
    data = request.get_json(silent=True) or {}
    motivo = (data.get("motivo") or "").strip() or "el cron no dejo detalle"
    destino = os.environ.get("LINKEDIN_MAIL_TO", "scalerics@gmail.com")
    ok = send_linkedin_failure(destino, motivo)
    return jsonify({"ok": ok}), 200


@linkedin_bp.route("/api/linkedin/marcar", methods=["GET"])
def api_linkedin_marcar():
    token = request.args.get("token", "")
    post = get_linkedin_post_by_token(_db(), token)
    if post is None:
        return "Ese link ya se uso o no existe.", 410

    update_linkedin_post(
        _db(), post["id"],
        estado="publicado",
        publicado_en=datetime.now().isoformat(),
        marcar_token=None,
    )
    return ("Listo, lo marque como publicado. Esto no publica en LinkedIn: "
            "solo registra que ya lo subiste, para que ese tema no vuelva a salir.", 200)
