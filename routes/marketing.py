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
    """Calcula el dossier del periodo, le pide el informe a la IA y guarda todo.

    Con `RADIOGRAFIA_IA_ACTIVA` apagada —que es como nace— `redactar` no llama a
    nadie y devuelve status 'sin_ia': el dossier se guarda igual y el panel
    funciona igual, porque los graficos siempre salieron del dossier.

    Un informe que no paso el validador NO se guarda. Queda el status del error
    y el motivo.
    """
    from services.radiografia import (aplicar_deltas, construir_dossier,
                                      guardar_snapshot, ultimo_snapshot)
    from services.radiografia_ia import MODELO, redactar

    desde, hasta = _periodo()
    if not desde:
        return jsonify(_ERROR_FECHAS), 400

    dossier = aplicar_deltas(construir_dossier(_db(), desde, hasta),
                             ultimo_snapshot(_db()))

    # Con la IA apagada esto no llama a nadie y devuelve status 'sin_ia'.
    resultado = redactar(dossier)
    if resultado.get("informe"):
        resultado["model"] = MODELO

    return jsonify({
        "id": guardar_snapshot(_db(), dossier, resultado),
        "status": resultado.get("status"),
        "error": resultado.get("error"),
        "avisos": resultado.get("avisos") or [],
        "tokens": {"entrada": resultado.get("tokens_in"),
                   "salida": resultado.get("tokens_out")},
    })


@marketing_bp.route("/api/marketing/radiografia")
def api_radiografia():
    """El informe de la ultima corrida, si lo hay.

    Contesta 200 con `informe: null` cuando todavia no corrio nunca, cuando la
    IA esta apagada, o cuando el informe no paso el validador. Ninguno de esos
    tres es un error: son estados que el panel tiene que poder pintar. Lo que
    los distingue es `status`.

    Un informe rechazado no se devuelve —nunca se publica algo sin validar—
    pero el motivo si, para poder mirar despues por que se rechazo.
    """
    import json as _json

    from database import _connect

    conn = _connect(_db())
    try:
        fila = conn.execute(
            "SELECT id, generated_at, period_start, period_end, report_json, "
            "       model, tokens_in, tokens_out, status, error_message "
            "FROM radiografias ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()

    if not fila:
        return jsonify({"id": None, "informe": None, "status": None,
                        "error": None, "generado": None, "periodo": None,
                        "modelo": None, "tokens": {"entrada": None,
                                                   "salida": None}})

    informe = None
    if fila["report_json"]:
        try:
            informe = _json.loads(fila["report_json"])
        except (ValueError, TypeError):
            logger.warning(f"radiografia {fila['id']}: report_json ilegible")

    return jsonify({
        "id": fila["id"],
        "generado": fila["generated_at"],
        "periodo": {"desde": fila["period_start"], "hasta": fila["period_end"]},
        "status": fila["status"],
        "error": fila["error_message"],
        "modelo": fila["model"],
        "tokens": {"entrada": fila["tokens_in"], "salida": fila["tokens_out"]},
        "informe": informe,
    })


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


@marketing_bp.route("/api/marketing/sync-anuncios", methods=["POST"])
def api_sync_anuncios():
    """Trae los anuncios, sus fotos y su gasto. Sin credenciales avisa y sale."""
    from services.meta_anuncios import sincronizar_anuncios
    from services.meta_insights import DIAS_A_RESINCRONIZAR

    try:
        dias = int(request.args.get("dias") or DIAS_A_RESINCRONIZAR)
    except ValueError:
        return jsonify({"error": "dias tiene que ser un numero"}), 400
    if not 1 <= dias <= 365:
        return jsonify({"error": "dias tiene que estar entre 1 y 365"}), 400

    hasta = date.today()
    desde = hasta - timedelta(days=dias)
    return jsonify(sincronizar_anuncios(_db(), desde.isoformat(),
                                        hasta.isoformat()))


_MES = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@marketing_bp.route("/api/marketing/rellenar-anuncios", methods=["POST"])
def api_rellenar_anuncios():
    """Trae los insights por pieza de UN mes viejo. Se corre a mano, una vez.

    El cron diario de `sync-anuncios` trae solo los ultimos 7 dias, asi que los
    meses anteriores al primer sync no tienen datos por pieza. Esto los rellena
    de a un mes por llamada, con su propio tope (ver `rellenar_mes`). Protegido
    igual que el sync: candado del blueprint, x-admin-token o panel marketing.

    429 si no paso la pausa minima desde la llamada anterior: no se reintenta
    solo, se espera.
    """
    from services.meta_anuncios import ReintentarMasTarde, rellenar_mes

    mes = request.args.get("mes") or ""
    if not _MES.match(mes):
        return jsonify({"error": "mes invalido: se espera YYYY-MM"}), 400
    try:
        return jsonify(rellenar_mes(_db(), mes))
    except ReintentarMasTarde as e:
        return jsonify({"error": str(e)}), 429
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@marketing_bp.route("/api/marketing/piezas")
def api_piezas():
    """Las piezas de la pauta de UN mes, partidas en activas hoy y ya no.

    Va aparte del dossier y no depende del periodo de arriba: la seccion tiene
    su propio navegador de mes, y cambiar de mes no tiene por que recalcular
    el panel entero. Sin token de Meta contesta igual, con las listas vacias.
    """
    from services.anuncios import piezas_del_mes

    mes = request.args.get("mes") or date.today().isoformat()[:7]
    if not _MES.match(mes):
        return jsonify({"error": "mes invalido: se espera YYYY-MM"}), 400
    return jsonify(piezas_del_mes(_db(), mes))


@marketing_bp.route("/api/marketing/leads-semana")
def api_leads_semana():
    """Cuando llegan los leads, de a UNA semana (lunes a domingo, Montevideo).

    Como las piezas, va aparte del dossier y no depende del periodo de arriba:
    el bloque tiene su propio navegador de semana. Sin `semana`, la actual.
    """
    from services.dossier import _hoy_en_montevideo, leads_de_la_semana

    semana = request.args.get("semana")
    if not semana:
        hoy = _hoy_en_montevideo()
        semana = (hoy - timedelta(days=hoy.weekday())).isoformat()
    if not _FECHA.match(semana):
        return jsonify({"error": "semana invalida: se espera el lunes, YYYY-MM-DD"}), 400
    try:
        return jsonify(leads_de_la_semana(_db(), semana))
    except ValueError:
        return jsonify({"error": "semana invalida: se espera el lunes, YYYY-MM-DD"}), 400


# Un id de anuncio de Meta es un numero largo. Se valida con esto y no con
# `secure_filename` porque lo que importa no es que el nombre sea prolijo sino
# que NO pueda salirse de la carpeta: sin esta guarda, un ad_id con `..`
# serviria cualquier archivo del disco.
_AD_ID = re.compile(r"^\d{1,32}$")


@marketing_bp.route("/api/marketing/creativo/<ad_id>")
def api_creativo(ad_id):
    """La foto del anuncio, servida desde el volumen.

    No se redirige a la URL de Meta: viene firmada, caduca en dias y despues
    deja imagenes rotas sin que nada avise. Por eso el archivo es nuestro.
    """
    import os.path

    from flask import send_file

    from database import _connect
    from services.meta_anuncios import _dir_creativos

    if not _AD_ID.match(ad_id or ""):
        return jsonify({"error": "id invalido"}), 400

    conn = _connect(_db())
    try:
        fila = conn.execute(
            "SELECT imagen_archivo FROM meta_ads WHERE ad_id = ?",
            (ad_id,)).fetchone()
    finally:
        conn.close()

    ruta = fila["imagen_archivo"] if fila else None
    # La ruta guardada tiene que caer adentro de la carpeta de creativos: si
    # alguna vez se guardara algo raro ahi, esto lo corta igual.
    carpeta = os.path.abspath(_dir_creativos(_db()))
    if not ruta or not os.path.abspath(ruta).startswith(carpeta + os.sep):
        return jsonify({"error": "sin imagen"}), 404
    if not os.path.exists(ruta):
        return jsonify({"error": "sin imagen"}), 404

    # Un mes de cache: el archivo no cambia nunca — si el anuncio cambia de
    # creativo, cambia el ad_id.
    return send_file(ruta, mimetype="image/jpeg", max_age=2592000)


@marketing_bp.route("/api/marketing/backfill-atribucion", methods=["POST"])
def api_backfill_atribucion():
    """Recupera de que anuncio vino cada lead, para los que Meta todavia tiene.

    Meta guarda los leads 90 dias: lo de antes se perdio. Esto es el rescate de
    una sola vez; el arreglo de verdad es que el import ya no tire el dato.
    """
    from services.meta_atribucion import backfill_atribucion

    return jsonify(backfill_atribucion(_db()))


@marketing_bp.route("/api/marketing/version")
def api_version():
    """Que imagen esta corriendo y desde cuando.

    Existe porque no habia forma de contestar "¿estoy viendo lo ultimo?" sin
    entrar por SSH. Un deploy en Fly es una carrera —la ultima imagen gana— y
    del lado del navegador no quedaba ningun rastro de cual quedo.

    `FLY_IMAGE_REF` trae el id del deploy. No es secreto: identifica la imagen,
    no da acceso a nada.
    """
    import time

    ref = os.environ.get("FLY_IMAGE_REF", "")
    return jsonify({
        "imagen": ref.rsplit(":", 1)[-1] if ref else None,
        "maquina": os.environ.get("FLY_MACHINE_ID") or None,
        "arrancado": getattr(current_app, "_arrancado", None),
        "ahora": int(time.time()),
    })
