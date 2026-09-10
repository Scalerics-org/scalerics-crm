"""Las rutas del modulo de inteligencia comercial sobre Meta Ads.

Las rutas son finas: validan la entrada, llaman a services/ y serializan.
Ninguna cuenta se hace aca.

Los GET piden el panel `marketing`: muestran cuanto se gasta en publicidad y no
todo el equipo tiene por que verlo. Los POST los llama el cron de GitHub
Actions, que no tiene con que loguearse, asi que pasan con x-admin-token.
"""

import logging
import os
import re
from datetime import date, timedelta

from flask import Blueprint, current_app, jsonify, request

from services.auth import require_panel

logger = logging.getLogger(__name__)

marketing_bp = Blueprint("marketing", __name__)

_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Sin parametros, la ventana por defecto. Tres meses dan varias campanas y
# muestras usables sin que el dossier se vaya de tamano.
DIAS_POR_DEFECTO = 90


def _db() -> str:
    return current_app.config["DB_PATH"]


def _token_admin_ok() -> bool:
    esperado = os.environ.get("ADMIN_TOKEN", "")
    return bool(esperado) and request.headers.get("x-admin-token", "") == esperado


@marketing_bp.before_request
def _candado():
    """Una linea, cubre todo el blueprint.

    Con un decorador por ruta, agregar un endpoint el mes que viene y olvidarse
    del candado deja el gasto publicitario abierto a cualquier usuario del CRM.

    El token pasa sin sesion porque los POST los llama el cron. El
    `require_login` global de `create_app` ya valido ese mismo token antes de
    llegar aca; esto agrega el permiso de panel para las personas.
    """
    if _token_admin_ok():
        return None
    return require_panel(_db(), "marketing")


def _periodo():
    """(desde, hasta) validados, o (None, None) si vinieron mal."""
    hasta = request.args.get("hasta") or date.today().isoformat()
    desde = (request.args.get("desde")
             or (date.today() - timedelta(days=DIAS_POR_DEFECTO)).isoformat())
    if not _FECHA.match(desde) or not _FECHA.match(hasta):
        return None, None
    if desde > hasta:
        return None, None
    return desde, hasta


_ERROR_FECHAS = {"error": "fechas invalidas: se espera YYYY-MM-DD y desde <= hasta"}


@marketing_bp.route("/api/marketing/dossier")
def api_dossier():
    """El dossier del periodo, comparado contra el ultimo snapshot."""
    from services.radiografia import (aplicar_deltas, construir_dossier,
                                      ultimo_snapshot)

    desde, hasta = _periodo()
    if not desde:
        return jsonify(_ERROR_FECHAS), 400

    return jsonify(aplicar_deltas(construir_dossier(_db(), desde, hasta),
                                  ultimo_snapshot(_db())))


@marketing_bp.route("/api/marketing/generar", methods=["POST"])
def api_generar():
    """Calcula el dossier del periodo y lo guarda como snapshot.

    En esta fase termina aca: no hay informe. Cuando exista el motor de IA se
    encadena despues de guardar.
    """
    from services.radiografia import (aplicar_deltas, construir_dossier,
                                      guardar_snapshot, ultimo_snapshot)

    desde, hasta = _periodo()
    if not desde:
        return jsonify(_ERROR_FECHAS), 400

    dossier = aplicar_deltas(construir_dossier(_db(), desde, hasta),
                             ultimo_snapshot(_db()))
    return jsonify({"id": guardar_snapshot(_db(), dossier), "status": "sin_ia"})


@marketing_bp.route("/api/marketing/sync-insights", methods=["POST"])
def api_sync_insights():
    """Trae el gasto de Meta. Sin credenciales contesta 200 y avisa."""
    from services.meta_insights import DIAS_A_RESINCRONIZAR, sincronizar

    try:
        dias = int(request.args.get("dias") or DIAS_A_RESINCRONIZAR)
    except ValueError:
        return jsonify({"error": "dias tiene que ser un numero"}), 400
    if not 1 <= dias <= 365:
        return jsonify({"error": "dias tiene que estar entre 1 y 365"}), 400

    hasta = date.today()
    desde = hasta - timedelta(days=dias)
    return jsonify(sincronizar(_db(), desde.isoformat(), hasta.isoformat()))


@marketing_bp.route("/api/marketing/backfill-campanas", methods=["POST"])
def api_backfill_campanas():
    """Saca la campana de adentro de `notes` a su columna. Idempotente."""
    from services.meta_campanas import backfill_campanas

    return jsonify(backfill_campanas(_db()))
