"""Endpoints de la sección financiera.

Las rutas son finas: validan la entrada, llaman a `services/finanzas.py` y
serializan. Ninguna cuenta se hace acá.
"""

from datetime import date

from flask import Blueprint, current_app, jsonify, request, session

from database import (actualizar_movimiento, actualizar_recurrente,
                      borrar_movimiento, borrar_por_cobrar, borrar_recurrente,
                      crear_movimiento, crear_por_cobrar, crear_recurrente,
                      get_movimiento, get_por_cobrar, get_recurrente,
                      listar_meses_abiertos, listar_movimientos,
                      listar_por_cobrar, listar_recurrentes, log_activity,
                      marcar_mes_abierto, marcar_mes_cerrado)
from services.auth import require_panel
from services.finanzas import (CATEGORIAS, MONEDAS, a_usd, desglosar_iva,
                               estado_de_cobro, materializar_recurrentes,
                               mes_editable, meses_con_datos, periodo_de,
                               rendimiento_pauta, resumen, resumen_iva,
                               saldar_por_cobrar)

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


def _validar_comunes(data: dict):
    """Tipo, categoría, moneda y monto: los cuatro campos que movimientos y
    fijos validan igual. Devuelve ((tipo, categoria, moneda, monto,
    tipo_cambio, monto_usd), None) o (None, mensaje de error).

    `monto_usd` sale ya congelado con `a_usd`; quien no lo necesite (los
    fijos no lo guardan, se recalcula en cada materialización) lo descarta.
    """
    tipo = data.get("tipo")
    if tipo not in CATEGORIAS:
        return None, "tipo tiene que ser 'ingreso' o 'egreso'"

    categoria = data.get("categoria")
    if categoria not in CATEGORIAS[tipo]:
        return None, f"categoría inválida para un {tipo}: {categoria!r}"

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
    except (TypeError, ValueError) as e:
        # TypeError además de ValueError: un tipo_cambio que llega como lista
        # u objeto rompe el `float(tipo_cambio)` de adentro con TypeError, no
        # con ValueError, y sin este catch escapaba como 500.
        return None, "tipo_cambio inválido" if isinstance(e, TypeError) else str(e)

    tipo_cambio = float(tipo_cambio) if moneda == "UYU" else None
    return (tipo, categoria, moneda, monto, tipo_cambio, monto_usd), None


def _validar_movimiento(data: dict) -> tuple[dict | None, str | None]:
    """Devuelve (campos listos para guardar, None) o (None, mensaje de error).

    Los campos opcionales (`client_id`, `budget_id`, `notas`) solo entran al
    resultado si la clave vino en el cuerpo: en un PUT, omitirla deja la
    columna como estaba, y mandarla en `null` la limpia a propósito.
    """
    comunes, error = _validar_comunes(data)
    if error:
        return None, error
    tipo, categoria, moneda, monto, tipo_cambio, monto_usd = comunes

    fecha = (data.get("fecha") or "").strip()
    if len(fecha) != 10 or fecha[4] != "-" or fecha[7] != "-":
        return None, "fecha tiene que ser 'YYYY-MM-DD'"

    concepto = (data.get("concepto") or "").strip()
    if not concepto:
        return None, "concepto es obligatorio"

    # El IVA se calcula acá y se guarda, no se deriva al leer: si la tasa
    # cambia, lo ya facturado tiene que seguir mostrando lo que se cobró.
    # Se calcula sobre `monto_usd` porque todo el módulo cuenta en dólares.
    facturado = 1 if data.get("facturado") else 0
    iva_usd = desglosar_iva(monto_usd)[1] if facturado else 0.0

    campos = {
        "tipo": tipo, "fecha": fecha, "periodo": periodo_de(fecha),
        "concepto": concepto, "categoria": categoria, "monto": monto,
        "moneda": moneda, "tipo_cambio": tipo_cambio, "monto_usd": monto_usd,
        "facturado": facturado, "iva_usd": iva_usd,
    }
    for campo in ("client_id", "budget_id"):
        if campo in data:
            campos[campo] = data[campo] or None
    if "notas" in data:
        campos["notas"] = (data["notas"] or "").strip() or None
    return campos, None


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


def _validar_cobro_parcial(data: dict, campos: dict):
    """Cuánto queda por cobrar después de este movimiento, o None si nada.

    Devuelve (pendiente_usd, None) o (None, error). El pendiente sale del
    TOTAL ACORDADO menos lo que se está cobrando: se pide el total y no el
    resto porque el total es el número que está en el presupuesto, y restar
    es más difícil de equivocar que acordarse de cuánto se cobró antes.
    """
    if data.get("total_acordado") in (None, ""):
        return None, None
    # Un egreso no deja nada por cobrar: lo que se paga, se pagó.
    if campos["tipo"] != "ingreso":
        return None, None
    try:
        total = float(data["total_acordado"])
    except (TypeError, ValueError):
        return None, "total_acordado tiene que ser un número"
    if total <= 0:
        return None, "total_acordado tiene que ser mayor que cero"

    pendiente = total - campos["monto_usd"]
    if pendiente < -0.005:
        return None, ("el total acordado no puede ser menor que lo que se "
                      "está cobrando")
    # Cobrar el total entero es válido: simplemente no deja pendiente.
    return (pendiente if pendiente > 0.005 else None), None


@finanzas_bp.route("/api/finanzas/movimientos", methods=["POST"])
def api_crear_movimiento():
    data = request.get_json() or {}
    campos, error = _validar_movimiento(data)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    pendiente, error = _validar_cobro_parcial(data, campos)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    trabado = _mes_trabado(campos["periodo"])
    if trabado:
        return jsonify({"ok": False, "error": trabado}), 400
    uid, nombre = _quien()
    campos["created_by_id"] = uid
    campos["created_by_name"] = nombre
    db = _db()
    mid = crear_movimiento(db, **campos)
    if pendiente:
        # "Saldo de ..." y no el concepto tal cual: el pendiente no es el cobro
        # que se acaba de hacer, es lo que falta. Heredarlo verbatim dejaba dos
        # movimientos llamados "50% inicial" cuando el segundo era el resto.
        crear_por_cobrar(db, client_id=campos.get("client_id"),
                         concepto=f"Saldo de {campos['concepto']}",
                         monto_usd=pendiente,
                         vence=(data.get("vence_resto") or "").strip() or None,
                         origen_movimiento_id=mid)
    log_activity(db, nombre, "finanzas_movimiento_creado", "finanzas", mid,
                 campos["concepto"],
                 f"{campos['tipo']} {campos['moneda']} {campos['monto']}",
                 user_id=uid)
    return jsonify({"ok": True, "id": mid}), 201


@finanzas_bp.route("/api/finanzas/movimientos/<int:mov_id>", methods=["PUT"])
def api_actualizar_movimiento(mov_id):
    db = _db()
    mov = get_movimiento(db, mov_id)
    if not mov:
        return jsonify({"ok": False, "error": "no existe"}), 404
    campos, error = _validar_movimiento(request.get_json() or {})
    if error:
        return jsonify({"ok": False, "error": error}), 400
    for periodo in {mov["periodo"], campos["periodo"]}:
        trabado = _mes_trabado(periodo)
        if trabado:
            return jsonify({"ok": False, "error": trabado}), 400
    if mov["recurrente_id"] and campos["periodo"] != mov["periodo"]:
        # Mover la fecha a otro mes de un movimiento generado por un fijo:
        # si el mes destino ya tiene la fila de ese fijo, el UPDATE viola
        # idx_finanzas_recurrente_periodo y esto 500ea sin este chequeo; si
        # el mes destino está libre, el UPDATE pasa pero libera el par
        # (recurrente_id, periodo) del mes de origen, y la próxima
        # materialización lo regenera ahí: el gasto queda contado dos veces
        # sin que nada falle. El pencil se ve igual para un fijo que para
        # uno a mano, así que esto es uso normal, no un caso raro.
        return jsonify({"ok": False, "error":
                        "un movimiento generado por un gasto fijo no se puede "
                        "mover a otro mes"}), 400
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
    trabado = _mes_trabado(mov["periodo"])
    if trabado:
        return jsonify({"ok": False, "error": trabado}), 400
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
    """Devuelve (campos listos para guardar, None) o (None, mensaje de error).

    `activo`, `hasta`, `client_id` y `notas` solo entran al resultado si la
    clave vino en el cuerpo. Importa sobre todo para `activo`: un PUT que
    solo cambia el monto y omite `activo` no puede reencender un fijo que
    estaba apagado a propósito — eso empezaría a generar plata sola en la
    próxima materialización perezosa de `GET /resumen`.
    """
    comunes, error = _validar_comunes(data)
    if error:
        return None, error
    tipo, categoria, moneda, monto, tipo_cambio, _monto_usd = comunes
    # Los fijos no guardan monto_usd: se recalcula en cada materialización
    # porque el tipo de cambio del mes puede ser otro.

    concepto = (data.get("concepto") or "").strip()
    if not concepto:
        return None, "concepto es obligatorio"

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

    campos = {
        "tipo": tipo, "concepto": concepto, "categoria": categoria,
        "monto": monto, "moneda": moneda, "tipo_cambio": tipo_cambio,
        "dia_del_mes": dia, "desde": desde,
    }
    if "hasta" in data:
        hasta = (data["hasta"] or "").strip() or None
        if hasta and (len(hasta) != 7 or hasta[4] != "-"):
            return None, "hasta tiene que ser 'YYYY-MM'"
        campos["hasta"] = hasta
    if "activo" in data:
        campos["activo"] = 1 if data["activo"] else 0
    if "client_id" in data:
        campos["client_id"] = data["client_id"] or None
    if "notas" in data:
        campos["notas"] = (data["notas"] or "").strip() or None
    return campos, None


@finanzas_bp.route("/api/finanzas/recurrentes", methods=["GET"])
def api_listar_recurrentes():
    """Los fijos, cada uno con su equivalente en USD ya calculado.

    El panel no convierte monedas. Si un fijo esta en pesos sin un tipo de
    cambio usable viene con `monto_usd = None`, y el panel lo deja afuera del
    total y lo marca, en vez de mostrarlo como si fuera un peso por dolar.
    """
    salida = []
    for r in listar_recurrentes(_db()):
        try:
            usd = a_usd(r["monto"], r["moneda"], r["tipo_cambio"])
        except (TypeError, ValueError):
            usd = None
        salida.append({**r, "monto_usd": usd})
    return jsonify(salida)


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


@finanzas_bp.route("/api/finanzas/pauta")
def api_pauta():
    """Rendimiento de la pauta: qué compró cada dólar invertido.

    Materializa antes de calcular, igual que `api_resumen`: hoy la pauta se
    carga mes a mes con el monto real, pero nada impide que mañana alguien
    cargue un gasto de publicidad como fijo, y ahí esta vista y el resumen
    tienen que coincidir. Materializar es idempotente, así que llamarlo de
    más no cuesta nada (Ruling R19).
    """
    db = _db()
    hoy = date.today()
    mes_actual = f"{hoy.year:04d}-{hoy.month:02d}"
    desde = request.args.get("desde") or mes_actual
    hasta = request.args.get("hasta") or mes_actual
    if desde > hasta:
        return jsonify({"ok": False, "error": "desde tiene que ser <= hasta"}), 400
    materializar_recurrentes(db, hoy=hoy)
    return jsonify(rendimiento_pauta(db, desde, hasta))


def _mes_trabado(periodo) -> str | None:
    """El mensaje de error si ese mes está cerrado, o None si se puede tocar."""
    if mes_editable(_db(), periodo):
        return None
    return (f"{periodo} es un mes cerrado. Reabrilo desde el navegador de meses "
            f"si de verdad hay que corregirlo.")


@finanzas_bp.route("/api/finanzas/meses")
def api_meses():
    """Qué mes es hoy, cuáles tienen datos y cuáles están reabiertos.

    Es lo que el navegador necesita para saber hasta dónde ir para atrás y
    cuándo mostrar el cartel de "mes cerrado".
    """
    hoy = date.today()
    return jsonify({
        "mes_actual": f"{hoy.year:04d}-{hoy.month:02d}",
        "con_datos": meses_con_datos(_db()),
        "abiertos": [m["periodo"] for m in listar_meses_abiertos(_db())],
    })


def _periodo_valido(periodo: str) -> bool:
    if len(periodo) != 7 or periodo[4] != "-":
        return False
    try:
        anio, mes = int(periodo[:4]), int(periodo[5:])
    except ValueError:
        return False
    return 1 <= mes <= 12 and anio > 1900


@finanzas_bp.route("/api/finanzas/meses/<periodo>/reabrir", methods=["POST"])
def api_reabrir_mes(periodo):
    if not _periodo_valido(periodo):
        return jsonify({"ok": False, "error": "periodo tiene que ser 'YYYY-MM'"}), 400
    uid, nombre = _quien()
    marcar_mes_abierto(_db(), periodo, quien=nombre)
    log_activity(_db(), nombre, "finanzas_mes_reabierto", "finanzas", None,
                 periodo, "", user_id=uid)
    return jsonify({"ok": True})


@finanzas_bp.route("/api/finanzas/meses/<periodo>/cerrar", methods=["POST"])
def api_cerrar_mes(periodo):
    if not _periodo_valido(periodo):
        return jsonify({"ok": False, "error": "periodo tiene que ser 'YYYY-MM'"}), 400
    uid, nombre = _quien()
    marcar_mes_cerrado(_db(), periodo)
    log_activity(_db(), nombre, "finanzas_mes_cerrado", "finanzas", None,
                 periodo, "", user_id=uid)
    return jsonify({"ok": True})


@finanzas_bp.route("/api/finanzas/por-cobrar")
def api_por_cobrar():
    """Lo que falta cobrar, con el estado de vencimiento ya resuelto.

    El estado se calcula acá y no en el navegador: "vencido hace 3 días"
    depende de qué día es hoy, y el reloj del servidor es el mismo para todos.
    """
    pendientes = []
    for p in listar_por_cobrar(_db()):
        estado = estado_de_cobro(p.get("vence"))
        pendientes.append({**p, **estado})
    return jsonify({
        "pendientes": pendientes,
        "total_usd": sum(p["monto_usd"] or 0 for p in pendientes),
    })


@finanzas_bp.route("/api/finanzas/por-cobrar/<int:pc_id>/cobrar", methods=["POST"])
def api_cobrar_pendiente(pc_id):
    """Cobra el pendiente: crea el ingreso y lo saca del listado."""
    data = request.get_json() or {}
    fecha = (data.get("fecha") or "").strip()
    if len(fecha) != 10 or fecha[4] != "-" or fecha[7] != "-":
        return jsonify({"ok": False, "error": "fecha tiene que ser 'YYYY-MM-DD'"}), 400
    # Cobrar crea un ingreso: si el mes esta cerrado, no entra por esta puerta
    # tampoco.
    trabado = _mes_trabado(periodo_de(fecha))
    if trabado:
        return jsonify({"ok": False, "error": trabado}), 400
    uid, nombre = _quien()
    db = _db()
    try:
        mid = saldar_por_cobrar(db, pc_id, fecha=fecha,
                                facturado=bool(data.get("facturado")),
                                created_by_id=uid, created_by_name=nombre)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    log_activity(db, nombre, "finanzas_cobro_registrado", "finanzas", mid,
                 "", "", user_id=uid)
    return jsonify({"ok": True, "movimiento_id": mid}), 201


@finanzas_bp.route("/api/finanzas/por-cobrar/<int:pc_id>", methods=["DELETE"])
def api_borrar_pendiente(pc_id):
    """Saca un pendiente mal cargado. No toca el movimiento que lo generó: esa
    plata entró de verdad."""
    db = _db()
    if not get_por_cobrar(db, pc_id):
        return jsonify({"ok": False, "error": "no existe"}), 404
    borrar_por_cobrar(db, pc_id)
    uid, nombre = _quien()
    log_activity(db, nombre, "finanzas_pendiente_borrado", "finanzas", pc_id,
                 "", "", user_id=uid)
    return jsonify({"ok": True})


@finanzas_bp.route("/api/finanzas/iva")
def api_iva():
    """Lo facturado del mes y el saldo de IVA, con el arrastre a favor.

    Es de UN mes, no de un rango: el IVA se liquida mensualmente y un saldo de
    "los últimos 12 meses" no significa nada. Materializa antes, igual que el
    resumen, para que un fijo facturado del mes ya esté contado.
    """
    db = _db()
    hoy = date.today()
    periodo = request.args.get("periodo") or f"{hoy.year:04d}-{hoy.month:02d}"
    if len(periodo) != 7 or periodo[4] != "-":
        return jsonify({"ok": False, "error": "periodo tiene que ser 'YYYY-MM'"}), 400
    materializar_recurrentes(db, hoy=hoy)
    return jsonify(resumen_iva(db, periodo))


@finanzas_bp.route("/api/finanzas/categorias")
def api_categorias():
    return jsonify(CATEGORIAS)
