"""Endpoints de Inteligencia financiera.

Finos, como los de Finanzas y el Simulador: validan, llaman a
`services/inteligencia_fin.py` y serializan. Nada de esto escribe en Finanzas
ni en Notion.

Permisos:
- La pantalla, tomar/descartar, el origen de las ventas y el canal de los fijos
  piden el panel `inteligencia_fin` (Ruling R20: no se reparte solo a los roles).
- "Recalcular ahora" y los supuestos, además, admin.
- El motivo de pérdida se elige desde la tarjeta de Proceso de venta y la fila
  de Demos, así que alcanza con `notion_clients` o `inteligencia_fin`.
- El esfuerzo se carga desde la ficha del proyecto: `projects` o
  `inteligencia_fin`.
"""

from flask import Blueprint, current_app, jsonify, request, session

from database import log_activity
from services import intel_objetivo as obj
from services import inteligencia_fin as ifn
from services.auth import is_admin, tiene_panel

inteligencia_fin_bp = Blueprint("inteligencia_fin", __name__)

PANEL = "inteligencia_fin"


def _db() -> str:
    return current_app.config["DB_PATH"]


def _quien() -> tuple[int | None, str]:
    return session.get("user_id"), session.get("user_name", "sistema")


def _no_autorizado():
    return jsonify({"ok": False, "error": "No autorizado"}), 403


def _puede(*paneles) -> bool:
    uid = session.get("user_id")
    return any(tiene_panel(_db(), uid, p) for p in paneles)


def _error(mensaje, codigo=400):
    return jsonify({"ok": False, "error": mensaje}), codigo


@inteligencia_fin_bp.route("/api/inteligencia-fin")
def api_estado():
    if not _puede(PANEL):
        return _no_autorizado()
    return jsonify(ifn.estado_pantalla(_db(), es_admin=is_admin(_db(), session.get("user_id"))))


@inteligencia_fin_bp.route("/api/inteligencia-fin/recalcular", methods=["POST"])
def api_recalcular():
    if not _puede(PANEL) or not is_admin(_db(), session.get("user_id")):
        return _no_autorizado()
    resultado = ifn.corrida_diaria(_db(), forzar=True) or {}
    uid, quien = _quien()
    log_activity(_db(), quien, "inteligencia_fin_recalculada", "inteligencia_fin",
                 resultado.get("calculo_id"), "Recalcular ahora", "", user_id=uid)
    return jsonify({"ok": True, **resultado})


@inteligencia_fin_bp.route("/api/inteligencia-fin/recomendaciones/<int:rec_id>/tomar", methods=["POST"])
def api_tomar(rec_id):
    if not _puede(PANEL):
        return _no_autorizado()
    uid, quien = _quien()
    ok, error = ifn.tomar(_db(), rec_id, quien)
    if not ok:
        return _error(error[1], error[0])
    log_activity(_db(), quien, "inteligencia_fin_tomada", "inteligencia_fin", rec_id,
                 "Lo voy a hacer", "", user_id=uid)
    return jsonify({"ok": True})


@inteligencia_fin_bp.route("/api/inteligencia-fin/recomendaciones/<int:rec_id>/descartar", methods=["POST"])
def api_descartar(rec_id):
    if not _puede(PANEL):
        return _no_autorizado()
    ok, error = ifn.descartar(_db(), rec_id)
    if not ok:
        return _error(error[1], error[0])
    return jsonify({"ok": True})


@inteligencia_fin_bp.route("/api/inteligencia-fin/supuestos", methods=["PUT"])
def api_supuestos():
    if not _puede(PANEL) or not is_admin(_db(), session.get("user_id")):
        return _no_autorizado()
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict) or not datos:
        return _error("faltan los supuestos")
    _, quien = _quien()
    for clave, valor in datos.items():
        error = ifn.guardar_supuesto(_db(), clave, valor, quien)
        if error:
            return _error(error)
    return jsonify({"ok": True, "supuestos": ifn.leer_supuestos(_db())})


@inteligencia_fin_bp.route("/api/inteligencia-fin/fijos/<int:rec_id>/canal", methods=["PUT"])
def api_canal_fijo(rec_id):
    if not _puede(PANEL):
        return _no_autorizado()
    datos = request.get_json(silent=True) or {}
    error = ifn.guardar_canal_fijo(_db(), rec_id, datos.get("canal") if isinstance(datos, dict) else None)
    if error:
        return _error(error, 404 if "no existe" in error else 400)
    return jsonify({"ok": True})


@inteligencia_fin_bp.route("/api/perdidas/<entidad>/<int:entidad_id>/motivo", methods=["PUT"])
def api_motivo(entidad, entidad_id):
    if not _puede(PANEL, "notion_clients"):
        return _no_autorizado()
    datos = request.get_json(silent=True)
    motivo = datos.get("motivo") if isinstance(datos, dict) else None
    uid, quien = _quien()
    error = ifn.guardar_motivo(_db(), entidad, entidad_id, motivo, quien)
    if error:
        return _error(error, 404 if "no está marcado" in error else 400)
    log_activity(_db(), quien, "motivo_perdida", entidad, entidad_id,
                 ifn.MOTIVOS[motivo], "", user_id=uid)
    return jsonify({"ok": True, "motivo": motivo})


@inteligencia_fin_bp.route("/api/proyectos/<int:project_id>/esfuerzo", methods=["PUT"])
def api_esfuerzo(project_id):
    if not _puede(PANEL, "projects"):
        return _no_autorizado()
    uid, quien = _quien()
    guardado, error = ifn.guardar_esfuerzo(_db(), project_id, request.get_json(silent=True), quien)
    if error:
        return _error(error, 404 if "no existe" in error else 400)
    log_activity(_db(), quien, "esfuerzo_proyecto", "project", project_id,
                 f"{guardado['horas']} h", "", user_id=uid)
    return jsonify({"ok": True, **guardado})


def _mes():
    return obj.periodo_de(obj.hoy_mvd())


@inteligencia_fin_bp.route("/api/inteligencia-fin/objetivo", methods=["PUT"])
def api_objetivo():
    """Las tres partes del objetivo del mes. Cualquiera con el panel las edita:
    es la decisión de Juan sobre su propio mes, no un dato del sistema."""
    if not _puede(PANEL):
        return _no_autorizado()
    _, quien = _quien()
    error = obj.guardar(_db(), _mes(), request.get_json(silent=True), quien)
    if error:
        return _error(error)
    return jsonify({"ok": True, "objetivo": obj.objetivo(_db(), _mes())})


@inteligencia_fin_bp.route("/api/inteligencia-fin/fijos/<int:rec_id>/clase", methods=["PUT"])
def api_clase_fijo(rec_id):
    """Mover un gasto fijo entre estructura y costo de un cliente.

    El sistema propone (mirando si el concepto nombra a un cliente), pero la
    última palabra es de Juan: sin esto, la clasificación sería una adivinanza
    que él no puede corregir.
    """
    if not _puede(PANEL):
        return _no_autorizado()
    datos = request.get_json(silent=True) or {}
    if not isinstance(datos, dict):
        return _error("faltan los datos")
    _, quien = _quien()
    error = obj.marcar_fijo(_db(), rec_id, datos.get("clase"), datos.get("client_id"), quien)
    if error:
        return _error(error, 404 if "no existe" in error else 400)
    return jsonify({"ok": True, "objetivo": obj.objetivo(_db(), _mes())})


@inteligencia_fin_bp.route("/api/inteligencia-fin/gastos-esperados", methods=["POST"])
def api_crear_gasto_esperado():
    if not _puede(PANEL):
        return _no_autorizado()
    uid, quien = _quien()
    gasto, error = obj.crear_esperado(_db(), _mes(), request.get_json(silent=True), quien)
    if error:
        return _error(error)
    log_activity(_db(), quien, "gasto_esperado_cargado", "inteligencia_fin", gasto["id"],
                 gasto["concepto"], f"USD {gasto['monto_usd']}", user_id=uid)
    return jsonify({"ok": True, "gasto": gasto, "objetivo": obj.objetivo(_db(), _mes())}), 201


@inteligencia_fin_bp.route("/api/inteligencia-fin/gastos-esperados/<int:gasto_id>",
                           methods=["DELETE"])
def api_borrar_gasto_esperado(gasto_id):
    if not _puede(PANEL):
        return _no_autorizado()
    error = obj.borrar_esperado(_db(), gasto_id)
    if error:
        return _error(error, 404)
    return jsonify({"ok": True, "objetivo": obj.objetivo(_db(), _mes())})


@inteligencia_fin_bp.route("/api/inteligencia-fin/gastos-esperados/<int:gasto_id>/emparejar",
                           methods=["POST"])
def api_emparejar_gasto(gasto_id):
    """Juan confirma que ese gasto esperado es ese movimiento real."""
    if not _puede(PANEL):
        return _no_autorizado()
    datos = request.get_json(silent=True) or {}
    movimiento_id = datos.get("movimiento_id") if isinstance(datos, dict) else None
    if not isinstance(movimiento_id, int):
        return _error("falta el movimiento con el que emparejarlo")
    error = obj.confirmar_emparejado(_db(), gasto_id, movimiento_id)
    if error:
        return _error(error, 404 if "no existe" in error else 400)
    return jsonify({"ok": True, "objetivo": obj.objetivo(_db(), _mes())})


@inteligencia_fin_bp.route("/api/inteligencia-fin/gastos-esperados/<int:gasto_id>/descartar",
                           methods=["POST"])
def api_descartar_gasto(gasto_id):
    """El gasto que al final no vino: sale del objetivo sin borrarse."""
    if not _puede(PANEL):
        return _no_autorizado()
    error = obj.descartar_esperado(_db(), gasto_id)
    if error:
        return _error(error, 404)
    return jsonify({"ok": True, "objetivo": obj.objetivo(_db(), _mes())})


@inteligencia_fin_bp.route("/api/ventas/<entidad>/<int:entidad_id>/origen", methods=["PUT"])
def api_origen(entidad, entidad_id):
    if not _puede(PANEL):
        return _no_autorizado()
    datos = request.get_json(silent=True)
    canal = datos.get("canal") if isinstance(datos, dict) else None
    uid, quien = _quien()
    error = ifn.guardar_origen(_db(), entidad, entidad_id, canal, quien)
    if error:
        return _error(error, 404 if "no existe" in error else 400)
    log_activity(_db(), quien, "origen_venta", entidad, entidad_id, ifn.CANALES[canal], "", user_id=uid)
    return jsonify({"ok": True, "canal": canal})
