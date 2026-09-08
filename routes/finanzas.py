"""Endpoints de la sección financiera.

Las rutas son finas: validan la entrada, llaman a `services/finanzas.py` y
serializan. Ninguna cuenta se hace acá.
"""

from datetime import date

from flask import Blueprint, current_app, jsonify, request, session

from database import (actualizar_movimiento, actualizar_recurrente,
                      borrar_movimiento, borrar_recurrente, crear_movimiento,
                      crear_recurrente, get_movimiento, get_recurrente,
                      listar_movimientos, listar_recurrentes, log_activity)
from services.auth import require_panel
from services.finanzas import (CATEGORIAS, MONEDAS, a_usd,
                               materializar_recurrentes, periodo_de, resumen)

finanzas_bp = Blueprint("finanzas", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


@finanzas_bp.before_request
def _candado():
    """Una línea, cubre todo el blueprint.

    Con un decorador por ruta, agregar un endpoint el mes que viene y olvidarse
    del candado deja la plata abierta. Así no hay forma de olvidarse.
    """
    return require_panel(_db(), "finanzas")


def _quien() -> tuple[int | None, str]:
    return session.get("user_id"), session.get("user_name", "sistema")


def _validar_movimiento(data: dict) -> tuple[dict | None, str | None]:
    """Devuelve (campos listos para guardar, None) o (None, mensaje de error)."""
    tipo = data.get("tipo")
    if tipo not in CATEGORIAS:
        return None, "tipo tiene que ser 'ingreso' o 'egreso'"

    categoria = data.get("categoria")
    if categoria not in CATEGORIAS[tipo]:
        return None, f"categoría inválida para un {tipo}: {categoria!r}"

    fecha = (data.get("fecha") or "").strip()
    if len(fecha) != 10 or fecha[4] != "-" or fecha[7] != "-":
        return None, "fecha tiene que ser 'YYYY-MM-DD'"

    concepto = (data.get("concepto") or "").strip()
    if not concepto:
        return None, "concepto es obligatorio"

    moneda = data.get("moneda")
    if moneda not in MONEDAS:
        return None, f"moneda tiene que ser una de {MONEDAS}"

    try:
        monto = float(data.get("monto"))
    except (TypeError, ValueError):
        return None, "monto tiene que ser un número"
    if monto <= 0:
        return None, "monto tiene que ser mayor que cero"

    tipo_cambio = data.get("tipo_cambio")
    try:
        monto_usd = a_usd(monto, moneda, tipo_cambio)
    except ValueError as e:
        return None, str(e)

    return {
        "tipo": tipo, "fecha": fecha, "periodo": periodo_de(fecha),
        "concepto": concepto, "categoria": categoria, "monto": monto,
        "moneda": moneda,
        "tipo_cambio": float(tipo_cambio) if moneda == "UYU" else None,
        "monto_usd": monto_usd,
        "client_id": data.get("client_id") or None,
        "budget_id": data.get("budget_id") or None,
        "notas": (data.get("notas") or "").strip() or None,
    }, None


# ── movimientos ───────────────────────────────────────────────────────────────

@finanzas_bp.route("/api/finanzas/movimientos", methods=["GET"])
def api_listar_movimientos():
    return jsonify(listar_movimientos(
        _db(),
        desde=request.args.get("desde"),
        hasta=request.args.get("hasta"),
        tipo=request.args.get("tipo"),
        categoria=request.args.get("categoria"),
        client_id=request.args.get("client_id", type=int),
    ))


@finanzas_bp.route("/api/finanzas/movimientos", methods=["POST"])
def api_crear_movimiento():
    campos, error = _validar_movimiento(request.get_json() or {})
    if error:
        return jsonify({"ok": False, "error": error}), 400
    uid, nombre = _quien()
    campos["created_by_id"] = uid
    campos["created_by_name"] = nombre
    db = _db()
    mid = crear_movimiento(db, **campos)
    log_activity(db, nombre, "finanzas_movimiento_creado", "finanzas", mid,
                 campos["concepto"],
                 f"{campos['tipo']} {campos['moneda']} {campos['monto']}",
                 user_id=uid)
    return jsonify({"ok": True, "id": mid}), 201


@finanzas_bp.route("/api/finanzas/movimientos/<int:mov_id>", methods=["PUT"])
def api_actualizar_movimiento(mov_id):
    db = _db()
    if not get_movimiento(db, mov_id):
        return jsonify({"ok": False, "error": "no existe"}), 404
    campos, error = _validar_movimiento(request.get_json() or {})
    if error:
        return jsonify({"ok": False, "error": error}), 400
    actualizar_movimiento(db, mov_id, **campos)
    uid, nombre = _quien()
    log_activity(db, nombre, "finanzas_movimiento_editado", "finanzas", mov_id,
                 campos["concepto"], "", user_id=uid)
    return jsonify({"ok": True})


@finanzas_bp.route("/api/finanzas/movimientos/<int:mov_id>", methods=["DELETE"])
def api_borrar_movimiento(mov_id):
    db = _db()
    mov = get_movimiento(db, mov_id)
    if not mov:
        return jsonify({"ok": False, "error": "no existe"}), 404
    uid, nombre = _quien()
    if mov["recurrente_id"]:
        # Un DELETE liberaría el par (recurrente_id, periodo) y el fijo lo
        # regeneraría en la próxima materialización: el gasto volvería solo.
        actualizar_movimiento(db, mov_id, anulado=1)
    else:
        borrar_movimiento(db, mov_id)
    log_activity(db, nombre, "finanzas_movimiento_borrado", "finanzas", mov_id,
                 mov["concepto"], "", user_id=uid)
    return jsonify({"ok": True})


# ── fijos ─────────────────────────────────────────────────────────────────────

def _validar_recurrente(data: dict) -> tuple[dict | None, str | None]:
    tipo = data.get("tipo")
    if tipo not in CATEGORIAS:
        return None, "tipo tiene que ser 'ingreso' o 'egreso'"
    if data.get("categoria") not in CATEGORIAS[tipo]:
        return None, f"categoría inválida para un {tipo}"
    concepto = (data.get("concepto") or "").strip()
    if not concepto:
        return None, "concepto es obligatorio"
    moneda = data.get("moneda")
    if moneda not in MONEDAS:
        return None, f"moneda tiene que ser una de {MONEDAS}"
    try:
        monto = float(data.get("monto"))
    except (TypeError, ValueError):
        return None, "monto tiene que ser un número"
    if monto <= 0:
        return None, "monto tiene que ser mayor que cero"
    try:
        a_usd(monto, moneda, data.get("tipo_cambio"))
    except ValueError as e:
        return None, str(e)

    dia = data.get("dia_del_mes", 1)
    try:
        dia = int(dia)
    except (TypeError, ValueError):
        return None, "dia_del_mes tiene que ser un número"
    if not 1 <= dia <= 28:
        # Se topea en 28 a propósito: no hay 30 de febrero, y no hace falta
        # lógica de "último día del mes" para un caso que no existe.
        return None, "dia_del_mes tiene que estar entre 1 y 28"

    desde = (data.get("desde") or "").strip()
    if len(desde) != 7 or desde[4] != "-":
        return None, "desde tiene que ser 'YYYY-MM'"
    hasta = (data.get("hasta") or "").strip() or None
    if hasta and (len(hasta) != 7 or hasta[4] != "-"):
        return None, "hasta tiene que ser 'YYYY-MM'"

    return {
        "tipo": tipo, "concepto": concepto, "categoria": data["categoria"],
        "monto": monto, "moneda": moneda,
        "tipo_cambio": float(data["tipo_cambio"]) if moneda == "UYU" else None,
        "dia_del_mes": dia, "desde": desde, "hasta": hasta,
        "activo": 1 if data.get("activo", 1) else 0,
        "client_id": data.get("client_id") or None,
        "notas": (data.get("notas") or "").strip() or None,
    }, None


@finanzas_bp.route("/api/finanzas/recurrentes", methods=["GET"])
def api_listar_recurrentes():
    return jsonify(listar_recurrentes(_db()))


@finanzas_bp.route("/api/finanzas/recurrentes", methods=["POST"])
def api_crear_recurrente():
    campos, error = _validar_recurrente(request.get_json() or {})
    if error:
        return jsonify({"ok": False, "error": error}), 400
    db = _db()
    rid = crear_recurrente(db, **campos)
    uid, nombre = _quien()
    log_activity(db, nombre, "finanzas_fijo_creado", "finanzas", rid,
                 campos["concepto"], "", user_id=uid)
    return jsonify({"ok": True, "id": rid}), 201


@finanzas_bp.route("/api/finanzas/recurrentes/<int:rec_id>", methods=["PUT"])
def api_actualizar_recurrente(rec_id):
    db = _db()
    if not get_recurrente(db, rec_id):
        return jsonify({"ok": False, "error": "no existe"}), 404
    campos, error = _validar_recurrente(request.get_json() or {})
    if error:
        return jsonify({"ok": False, "error": error}), 400
    actualizar_recurrente(db, rec_id, **campos)
    uid, nombre = _quien()
    log_activity(db, nombre, "finanzas_fijo_editado", "finanzas", rec_id,
                 campos["concepto"], "", user_id=uid)
    return jsonify({"ok": True})


@finanzas_bp.route("/api/finanzas/recurrentes/<int:rec_id>", methods=["DELETE"])
def api_borrar_recurrente(rec_id):
    db = _db()
    rec = get_recurrente(db, rec_id)
    if not rec:
        return jsonify({"ok": False, "error": "no existe"}), 404
    borrar_recurrente(db, rec_id)
    uid, nombre = _quien()
    log_activity(db, nombre, "finanzas_fijo_borrado", "finanzas", rec_id,
                 rec["concepto"],
                 "los movimientos que ya generó quedan", user_id=uid)
    return jsonify({"ok": True})


# ── resumen y catálogos ───────────────────────────────────────────────────────

@finanzas_bp.route("/api/finanzas/resumen")
def api_resumen():
    """KPIs, serie y desgloses. Materializa los fijos antes de calcular.

    Acá es donde se materializa, perezosamente. No hay hilo de arranque: los
    jobs de boot de este repo ya provocaron una tanda de mails reales.
    """
    db = _db()
    hoy = date.today()
    mes_actual = f"{hoy.year:04d}-{hoy.month:02d}"
    desde = request.args.get("desde") or mes_actual
    hasta = request.args.get("hasta") or mes_actual
    if desde > hasta:
        return jsonify({"ok": False, "error": "desde tiene que ser <= hasta"}), 400
    materializar_recurrentes(db, hoy=hoy)
    return jsonify(resumen(db, desde, hasta))


@finanzas_bp.route("/api/finanzas/categorias")
def api_categorias():
    return jsonify(CATEGORIAS)
