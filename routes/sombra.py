"""Panel Modo sombra: recomendaciones de pauta y como resultaron.

Pide el panel `sombra` ("Recomendaciones de pauta"), que tiene quien ve
Marketing. La evaluacion (quien tenia razon) y el marcador son del
administrador: a los demas se les sacan antes de responder.
"""

from datetime import date

from flask import Blueprint, current_app, jsonify, request, session

from services import sombra_meta as sm
from services.auth import is_admin, require_panel

sombra_bp = Blueprint("sombra", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


@sombra_bp.before_request
def _candado():
    return require_panel(_db(), "sombra")


@sombra_bp.route("/api/sombra/semana")
def api_semana():
    hoy = date.today()
    crudo = request.args.get("semana")
    try:
        lunes = sm._lunes(date.fromisoformat(crudo)) if crudo else sm._lunes(hoy)
    except ValueError:
        return jsonify({"ok": False, "error": "semana tiene que ser AAAA-MM-DD"}), 400
    admin = is_admin(_db(), session.get("user_id"))
    recs = sm.semana_de(_db(), lunes.isoformat())
    if not admin:
        for r in recs:
            for campo in ("seguida", "veredicto", "detalle", "evaluada_en"):
                r.pop(campo, None)
    return jsonify({
        "ok": True,
        "semana": lunes.isoformat(),
        "semana_actual": sm._lunes(hoy).isoformat(),
        "recomendaciones": recs,
        "marcador": sm.marcador(_db()) if admin else {},
        "es_admin": admin,
        "puede_calcular": admin,
        "tope_cpl": sm.tope_cpl(_db()),
    })


@sombra_bp.route("/api/sombra/tope", methods=["POST"])
def api_tope():
    """Fija o borra el tope de costo por lead. Lo puede cambiar quien ve el panel."""
    crudo = (request.get_json(silent=True) or {}).get("valor")
    if crudo in (None, ""):
        valor = None
    else:
        try:
            valor = round(float(str(crudo).replace(",", ".")), 2)
        except ValueError:
            return jsonify({"ok": False, "error": "El tope tiene que ser un número."}), 400
        if not 1 <= valor <= 500:
            return jsonify({"ok": False, "error": "El tope tiene que estar entre 1 y 500 USD."}), 400
    sm.fijar_tope_cpl(_db(), valor, str(session.get("user_name") or session.get("user_id") or ""))
    return jsonify({"ok": True, "tope_cpl": valor})


@sombra_bp.route("/api/sombra/calcular", methods=["POST"])
def api_calcular():
    if not is_admin(_db(), session.get("user_id")):
        return jsonify({"ok": False, "error": "Solo un administrador puede calcular"}), 403
    r = sm.calcular_ahora(_db())
    codigo = 429 if r["estado"] == "esperar" else (502 if r["estado"] == "error" else 200)
    return jsonify({"ok": r["estado"] == "ok", **r}), codigo
