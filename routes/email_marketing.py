"""Endpoints del panel Email marketing (CAPTACIÓN).

Finos: validan, llaman a `services/email_marketing.py` y serializan. Todo pide
el panel `email_mkt`, porque devuelve las direcciones de los destinatarios.
"Actualizar estados" además pide admin: consulta la API real de Resend.
"""

import os

import requests
from flask import Blueprint, current_app, jsonify, request, session

from services.auth import is_admin, require_admin, require_panel
from services.email_marketing import (ESTADOS, TIPOS, TOPE_CONSULTAS,
                                      actualizar_estados, mes_actual, parse_mes,
                                      resumen)

email_mkt_bp = Blueprint("email_mkt", __name__)

_POR_PAGINA = 25


def _db() -> str:
    return current_app.config["DB_PATH"]


def _cliente_http():
    """Aparte para poder cambiarlo por uno falso en los tests."""
    return requests


def _api_key() -> str:
    return os.environ.get("RESEND_API_KEY", "").strip()


@email_mkt_bp.before_request
def _candado():
    return require_panel(_db(), "email_mkt")


@email_mkt_bp.route("/api/email-marketing")
def api_resumen():
    crudo = request.args.get("mes")
    mes = parse_mes(crudo) if crudo else mes_actual()
    if mes is None:
        return jsonify({"ok": False, "error": "mes tiene que ser AAAA-MM"}), 400
    tipo = (request.args.get("tipo") or "").strip() or None
    if tipo and tipo not in TIPOS:
        return jsonify({"ok": False, "error": "tipo desconocido"}), 400
    estado = (request.args.get("estado") or "").strip() or None
    if estado and estado not in ESTADOS:
        return jsonify({"ok": False, "error": "estado desconocido"}), 400
    try:
        pagina = int(request.args.get("pagina") or 1)
    except ValueError:
        return jsonify({"ok": False, "error": "pagina tiene que ser un número"}), 400
    q = (request.args.get("q") or "").strip()[:100] or None

    datos = resumen(_db(), mes, tipo=tipo, q=q, estado=estado, pagina=pagina,
                    por_pagina=_POR_PAGINA)
    datos["es_admin"] = is_admin(_db(), session.get("user_id"))
    datos["hay_api_key"] = bool(_api_key())
    return jsonify(datos)


@email_mkt_bp.route("/api/email-marketing/actualizar-estados", methods=["POST"])
def api_actualizar_estados():
    err = require_admin(_db())
    if err:
        return err
    clave = _api_key()
    if not clave:
        return jsonify({"ok": False, "error": "No hay clave de Resend (RESEND_API_KEY) "
                                              "configurada en el servidor."}), 400
    cuerpo = request.get_json(silent=True) or {}
    try:
        limite = max(1, min(int(cuerpo.get("limite") or 20), TOPE_CONSULTAS))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "limite tiene que ser un número"}), 400
    res = actualizar_estados(_db(), clave, limite=limite, http=_cliente_http())
    return jsonify({"ok": True, **res})
