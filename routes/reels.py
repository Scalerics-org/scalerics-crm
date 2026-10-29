"""Panel Reels (MARKETING): revisar los reels que hace la rutina antes de que
entren a la cola de publicación (12, 18 y 21, Instagram y TikTok).

No guarda nada acá: los reels, su estado y los videos viven en el panel de
reels.scalerics.com (Vercel + GitHub + el bucket de estado), y este módulo le
pregunta servidor a servidor con CRM_REELS_TOKEN. El video lo baja el navegador
directo de GitHub (se redirige al link temporal), así que no pasa por Fly.
"""

import os
import re
import time

import requests
from flask import Blueprint, current_app, jsonify, redirect, request, session

from services.auth import require_panel

reels_bp = Blueprint("reels", __name__)

REELS_URL = os.environ.get("REELS_PANEL_URL", "https://reels.scalerics.com")
ESTADOS = ("por_revisar", "en_cola", "descartado")
_SLUG = re.compile(r"^[a-z0-9-]{1,80}$")
# El link temporal de cada video, por 2 minutos: volver a una página o repintar la cola
# no le vuelve a preguntar al panel (con 4 hilos en Fly, muchas consultas juntas trancan el CRM).
_VIDEO_TTL = 120
_videos: dict[str, tuple[float, str]] = {}


def _db() -> str:
    return current_app.config["DB_PATH"]


def _headers() -> dict:
    return {"x-crm-token": os.environ.get("CRM_REELS_TOKEN", "")}


@reels_bp.before_request
def _candado():
    return require_panel(_db(), "reels")


def _sin_conexion():
    return jsonify({"ok": False, "error": "No se pudo hablar con el panel de reels. Probá de nuevo."}), 502


@reels_bp.route("/api/reels")
def api_listar():
    if not os.environ.get("CRM_REELS_TOKEN"):
        return jsonify({"ok": False, "error": "Falta CRM_REELS_TOKEN en el servidor."}), 503
    try:
        r = requests.get(f"{REELS_URL}/api/reels", params={"todos": "1"}, headers=_headers(), timeout=25)
        r.raise_for_status()
        reels = r.json().get("reels", [])
    except (requests.RequestException, ValueError):
        current_app.logger.exception("reels: no se pudo leer la lista")
        return _sin_conexion()
    return jsonify({"ok": True, "reels": reels})


@reels_bp.route("/api/reels/<slug>/estado", methods=["POST"])
def api_estado(slug):
    estado = (request.get_json(silent=True) or {}).get("estado")
    if not _SLUG.match(slug) or estado not in ESTADOS:
        return jsonify({"ok": False, "error": "Reel o estado inválido"}), 400
    quien = str(session.get("user_name") or session.get("user_email") or "")
    try:
        r = requests.post(f"{REELS_URL}/api/revision", headers=_headers(), timeout=20,
                          json={"slug": slug, "estado": estado, "quien": quien})
        r.raise_for_status()
    except requests.RequestException:
        current_app.logger.exception("reels: no se pudo cambiar el estado de %s", slug)
        return _sin_conexion()
    return jsonify({"ok": True, "slug": slug, "estado": estado})


@reels_bp.route("/api/reels/<slug>/video")
def api_video(slug):
    """Redirige al link temporal de GitHub: el mp4 no pasa por este servidor."""
    if not _SLUG.match(slug):
        return jsonify({"ok": False, "error": "Reel inválido"}), 400
    guardado = _videos.get(slug)
    if guardado and guardado[0] > time.monotonic():
        return _al_video(guardado[1])
    try:
        r = requests.get(f"{REELS_URL}/api/video", params={"slug": slug}, headers=_headers(),
                         timeout=20, allow_redirects=False)
    except requests.RequestException:
        return _sin_conexion()
    destino = r.headers.get("Location", "")
    if r.status_code not in (301, 302, 307) or not destino.startswith("https://"):
        return jsonify({"ok": False, "error": "No se encontró el video"}), 404
    if len(_videos) > 500:
        _videos.clear()
    _videos[slug] = (time.monotonic() + _VIDEO_TTL, destino)
    return _al_video(destino)


def _al_video(destino: str):
    resp = redirect(destino, 302)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@reels_bp.route("/api/reels/horarios", methods=["GET", "PUT"])
def api_horarios():
    """A qué horas de Uruguay sale un reel en cada red (lo lee el workflow cada hora)."""
    try:
        if request.method == "GET":
            r = requests.get(f"{REELS_URL}/api/horarios", timeout=15)
            r.raise_for_status()
            d = r.json()
            return jsonify({"ok": True, "instagram": d.get("instagram", []), "tiktok": d.get("tiktok", [])})
        data = request.get_json(silent=True) or {}
        horas = {}
        for red in ("instagram", "tiktok"):
            lista = data.get(red)
            if (not isinstance(lista, list) or len(lista) > 12
                    or not all(isinstance(h, int) and not isinstance(h, bool) and 0 <= h <= 23 for h in lista)
                    or len(set(lista)) != len(lista)):
                return jsonify({"ok": False, "error": "Las horas van de 0 a 23, sin repetir, hasta 12 por red."}), 400
            horas[red] = sorted(lista)
        quien = str(session.get("user_name") or session.get("user_email") or "")
        r = requests.post(f"{REELS_URL}/api/horarios", headers=_headers(), timeout=15, json={**horas, "quien": quien})
        r.raise_for_status()
    except (requests.RequestException, ValueError):
        current_app.logger.exception("reels: horarios")
        return _sin_conexion()
    return jsonify({"ok": True, **horas})


@reels_bp.route("/api/reels/orden", methods=["PUT"])
def api_orden():
    """El orden de la cola: la lista de slugs en cola, el primero sale primero."""
    orden = (request.get_json(silent=True) or {}).get("orden")
    if not isinstance(orden, list) or len(orden) > 300 or not all(isinstance(s, str) and _SLUG.match(s) for s in orden):
        return jsonify({"ok": False, "error": "Orden inválido"}), 400
    try:
        r = requests.post(f"{REELS_URL}/api/revision", headers=_headers(), timeout=20, json={"orden": orden})
        r.raise_for_status()
    except requests.RequestException:
        current_app.logger.exception("reels: no se pudo guardar el orden")
        return _sin_conexion()
    return jsonify({"ok": True})
