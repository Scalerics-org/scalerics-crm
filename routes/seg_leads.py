"""Endpoints de Seguimiento de leads.

Finos, como los de Equipo: validan, llaman a `database.py` y serializan con
`services/seg_leads.py`. Todo pide el panel `seg_leads`.

`lead_id` es `businesses.id`: ahí está el teléfono, y es la ficha que abre el
panel de cliente. Una ficha de Proceso de venta llega a su lead por
`notion_clients.business_id`.
"""

from flask import Blueprint, current_app, jsonify, request, session

from database import (get_business, seg_cerrar_con_llamado, seg_crear_recordatorio,
                      seg_get_recordatorio, seg_listar_pendientes,
                      seg_llamados_del_lead, seg_mover_fecha)
from services.auth import require_panel
from services.seg_leads import (ahora_mvd, armar_item, armar_llamado, armar_pantalla,
                                parse_fecha, posponer, validar_hecho,
                                validar_recordatorio)

seg_leads_bp = Blueprint("seg_leads", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


def _ahora():
    """La hora de Montevideo. Aparte para poder fijarla en los tests."""
    return ahora_mvd()


def _hoy():
    return _ahora().date()


def _error(mensaje: str, codigo: int):
    return jsonify({"ok": False, "error": mensaje}), codigo


@seg_leads_bp.before_request
def _candado():
    return require_panel(_db(), "seg_leads")


@seg_leads_bp.route("/api/seg-leads")
def api_pantalla():
    """Solo lo pendiente, agrupado. Un lead sin recordatorio pendiente no está."""
    return jsonify(armar_pantalla(seg_listar_pendientes(_db()), _hoy()))


@seg_leads_bp.route("/api/seg-leads/recordatorios", methods=["POST"])
def api_crear():
    """Crea un recordatorio. Si el lead ya tenía uno abierto, ese se cierra."""
    campos, error = validar_recordatorio(request.get_json(silent=True))
    if error:
        return _error(error, 400)
    db = _db()
    if not get_business(db, campos["lead_id"]):
        return _error("ese lead no existe en el CRM", 404)
    rid, cerrados = seg_crear_recordatorio(db, **campos)
    return jsonify({"ok": True, "id": rid, "cerrados": cerrados}), 201


def _pendiente(rid: int):
    r = seg_get_recordatorio(_db(), rid)
    if not r:
        return None, _error("el recordatorio no existe", 404)
    if r["estado"] != "pendiente":
        return None, _error("ese recordatorio ya está cerrado", 409)
    return r, None


@seg_leads_bp.route("/api/seg-leads/recordatorios/<int:rid>/posponer", methods=["POST"])
def api_posponer(rid):
    """+1 semana o +1 mes, sin formulario: body `{"cuanto": "semana"|"mes"}`."""
    r, respuesta = _pendiente(rid)
    if respuesta:
        return respuesta
    datos = request.get_json(silent=True)
    cuanto = datos.get("cuanto") if isinstance(datos, dict) else None
    hoy = _hoy()
    nueva = posponer(parse_fecha(r["fecha"]) or hoy, cuanto, hoy)
    if nueva is None:
        return _error("cuanto tiene que ser 'semana' o 'mes'", 400)
    if not seg_mover_fecha(_db(), rid, nueva.isoformat()):
        return _error("ese recordatorio ya está cerrado", 409)
    return jsonify({"ok": True, "id": rid, "fecha": nueva.isoformat()})


@seg_leads_bp.route("/api/seg-leads/recordatorios/<int:rid>/hecho", methods=["POST"])
def api_hecho(rid):
    """Cierra el recordatorio y deja el llamado en el historial del lead.

    Body: `resultado` (qué pasó) y una de dos: `proxima_fecha` (con
    `proxima_hora`, `proximo_motivo` y `proxima_nota` opcionales) o
    `sin_volver: true`.
    """
    r, respuesta = _pendiente(rid)
    if respuesta:
        return respuesta
    ahora = _ahora()
    campos, error = validar_hecho(request.get_json(silent=True), r, ahora.date())
    if error:
        return _error(error, 400)
    hecho = seg_cerrar_con_llamado(_db(), rid, ahora.strftime("%Y-%m-%d %H:%M"),
                                   campos["resultado"],
                                   creado_por=session.get("user_name"),
                                   proximo=campos["proximo"])
    if hecho is None:
        return _error("ese recordatorio ya está cerrado", 409)
    return jsonify({"ok": True, **hecho})


@seg_leads_bp.route("/api/seg-leads/lead/<int:lead_id>")
def api_del_lead(lead_id):
    """Lo que muestra la ficha del lead: su recordatorio abierto y su historial."""
    db = _db()
    negocio = get_business(db, lead_id)
    if not negocio:
        return _error("ese lead no existe en el CRM", 404)
    hoy = _hoy()
    pendientes = seg_listar_pendientes(db, lead_id=lead_id)
    return jsonify({
        "hoy": hoy.isoformat(),
        "lead": {"id": lead_id, "nombre": negocio.get("name") or ""},
        "pendiente": armar_item(pendientes[0], hoy) if pendientes else None,
        "llamados": [armar_llamado(fila) for fila in seg_llamados_del_lead(db, lead_id)],
    })
