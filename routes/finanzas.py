"""Endpoints de la sección financiera.

Las rutas son finas: validan la entrada, llaman a `services/finanzas.py` y
serializan. Ninguna cuenta se hace acá.
"""

from datetime import date, datetime, timezone

from flask import Blueprint, current_app, jsonify, request, session

from database import (actualizar_movimiento, actualizar_recurrente,
                      borrar_movimiento, borrar_por_cobrar, borrar_recurrente,
                      crear_movimiento, crear_por_cobrar, crear_recurrente,
                      get_movimiento, get_por_cobrar, get_recurrente,
                      listar_meses_abiertos, listar_movimientos,
                      listar_por_cobrar, listar_recurrentes, log_activity,
                      marcar_mes_abierto, marcar_mes_cerrado)
from services.auth import require_edicion, require_panel
from database import (actualizar_dato_balance, borrar_dato_balance,
                      crear_dato_balance, get_dato_balance,
                      listar_datos_balance)
from services.finanzas import BALANCE_CLASES, balance_general
from services.finanzas import (BALANCE_TIPOS, CATEGORIAS, MONEDAS, a_usd,
                               balance, desglosar_iva_incluido,
                               estado_de_cobro, fecha_valida,
                               iva_sobre, materializar_recurrentes,
                               mes_editable, meses_con_datos, periodo_balance,
                               periodo_de, primer_movimiento,
                               rendimiento_pauta, resumen, resumen_iva,
                               saldar_por_cobrar)

finanzas_bp = Blueprint("finanzas", __name__)


def _db() -> str:
    return current_app.config["DB_PATH"]


# Rutas que un rol con Finanzas en solo lectura puede usar aunque no sean GET.
# Hoy el Balance se genera con GET y ya pasa solo; queda anotado acá para que
# el día que generar o guardar un balance sea POST, el Contador lo siga
# pudiendo hacer (pedido de Juan: "salvo la parte de balances").
_PERMITIDAS_EN_SOLO_LECTURA = {"finanzas.api_balance", "finanzas.api_balance_general"}


@finanzas_bp.before_request
def _candado():
    """Cubre todo el blueprint, en dos pasos.

    1. Ver: sin el panel `finanzas`, nada (Ruling R20).
    2. Modificar: todo lo que no sea GET necesita además que el rol no tenga
       Finanzas en solo lectura (el Contador). Va acá y no ruta por ruta por el
       mismo motivo que el paso 1: un endpoint nuevo que escriba queda cubierto
       sin que nadie se acuerde.
    """
    bloqueo = require_panel(_db(), "finanzas")
    if bloqueo:
        return bloqueo
    if request.method in ("GET", "HEAD", "OPTIONS") \
            or request.endpoint in _PERMITIDAS_EN_SOLO_LECTURA:
        return None
    return require_edicion(_db(), "finanzas")


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

    # El IVA por default se SUMA al monto cargado: se escribe el líquido, no
    # el total. Con `iva_incluido` es al revés (pedido de Juan, 22/9: "a
    # veces me dan los precios con IVA"): el monto que se escribe YA es el
    # total, y de ahí se separan neto e IVA hacia atrás. Se calcula acá y se
    # guarda, no se deriva al leer: si la tasa cambia, lo ya facturado tiene
    # que seguir mostrando lo que se cobró. Sobre `monto_usd` porque todo el
    # módulo cuenta en dólares; `monto` (lo que se tipeó, en su moneda
    # original) no cambia en ningún caso, es la prueba de lo que se acordó.
    facturado = 1 if data.get("facturado") else 0
    iva_incluido = 1 if (facturado and data.get("iva_incluido")) else 0
    if iva_incluido:
        monto_usd, iva_usd = desglosar_iva_incluido(monto_usd)
    elif facturado:
        iva_usd = iva_sobre(monto_usd)
    else:
        iva_usd = 0.0

    campos = {
        "tipo": tipo, "fecha": fecha, "periodo": periodo_de(fecha),
        "concepto": concepto, "categoria": categoria, "monto": monto,
        "moneda": moneda, "tipo_cambio": tipo_cambio, "monto_usd": monto_usd,
        "facturado": facturado, "iva_usd": iva_usd, "iva_incluido": iva_incluido,
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

    `activo`, `facturado`, `hasta`, `client_id` y `notas` solo entran al
    resultado si la clave vino en el cuerpo. Importa sobre todo para `activo`:
    un PUT que solo cambia el monto y omite `activo` no puede reencender un
    fijo que estaba apagado a propósito — eso empezaría a generar plata sola
    en la próxima materialización perezosa de `GET /resumen`.
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
    if "facturado" in data:
        # Condicional por el mismo motivo que `activo`: un PUT parcial que
        # omite la clave no puede apagarle el IVA a un fijo facturado. El
        # error no se vería al guardar sino un mes después, cuando se
        # materializa sin impuesto.
        campos["facturado"] = 1 if data["facturado"] else 0
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


@finanzas_bp.route("/api/finanzas/por-cobrar", methods=["POST"])
def api_crear_pendiente():
    """Carga a mano algo que falta cobrar.

    El camino principal sigue siendo el cobro parcial, que lo genera solo. Esto
    es para lo que ya se acordó y todavía no tuvo ningún movimiento: sin esta
    puerta, un saldo que no nació de un cobro no se puede registrar en ningún
    lado.

    No lleva candado de mes cerrado: un pendiente no pertenece a un período,
    es algo que se debe hasta que se cobre. El candado está donde entra la
    plata, que es al cobrarlo.
    """
    data = request.get_json() or {}
    concepto = (data.get("concepto") or "").strip()
    if not concepto:
        return jsonify({"ok": False, "error": "concepto es obligatorio"}), 400
    try:
        monto = float(data.get("monto_usd"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "monto tiene que ser un número"}), 400
    if monto <= 0:
        return jsonify({"ok": False, "error": "monto tiene que ser mayor que cero"}), 400

    vence = (data.get("vence") or "").strip() or None
    if vence and (len(vence) != 10 or vence[4] != "-" or vence[7] != "-"):
        return jsonify({"ok": False, "error": "vence tiene que ser 'YYYY-MM-DD'"}), 400

    uid, nombre = _quien()
    db = _db()
    pid = crear_por_cobrar(db, concepto=concepto, monto_usd=monto, vence=vence,
                           client_id=data.get("client_id") or None)
    log_activity(db, nombre, "finanzas_pendiente_creado", "finanzas", pid,
                 concepto, f"USD {monto}", user_id=uid)
    return jsonify({"ok": True, "id": pid}), 201


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


@finanzas_bp.route("/api/finanzas/balance")
def api_balance():
    """El balance "hasta el momento": en blanco (contable) o interno (todo).

    `tipo` es obligatorio. `desde` vacío es el 1 de enero del año en curso,
    `desde=inicio` es el primer movimiento, y `hasta` vacío es hoy. Las
    fechas son de Montevideo: el servidor corre en UTC, y de 21 a 24 ya sería
    mañana.

    Materializa antes, igual que el resumen: un fijo del mes ya tiene que
    estar contado. Lo cubre el candado del blueprint (Ruling R20).
    """
    from services.daily import MONTEVIDEO, hoy_montevideo

    tipo = request.args.get("tipo") or ""
    if tipo not in BALANCE_TIPOS:
        return jsonify({"ok": False,
                        "error": "tipo tiene que ser 'blanco' o 'interno'"}), 400
    db = _db()
    hoy = hoy_montevideo()
    desde_pedido = (request.args.get("desde") or "").strip()
    hasta_pedido = (request.args.get("hasta") or "").strip()
    for nombre, valor in (("desde", desde_pedido), ("hasta", hasta_pedido)):
        if valor and not (nombre == "desde" and valor == "inicio") \
                and not fecha_valida(valor):
            return jsonify({"ok": False,
                            "error": f"{nombre} tiene que ser 'YYYY-MM-DD'"}), 400

    materializar_recurrentes(db, hoy=hoy)
    desde, hasta = periodo_balance(
        desde_pedido, hasta_pedido, hoy,
        primer_movimiento(db) if desde_pedido == "inicio" else None)
    if desde > hasta:
        return jsonify({"ok": False, "error": "desde tiene que ser <= hasta"}), 400

    generado = datetime.now(timezone.utc).astimezone(MONTEVIDEO)
    return jsonify(balance(db, tipo, desde, hasta,
                           generado_en=generado.strftime("%Y-%m-%d %H:%M")))


@finanzas_bp.route("/api/finanzas/balance-general")
def api_balance_general():
    """Balance General (Activo = Pasivo + Patrimonio) a una fecha de corte.

    `tipo` obligatorio ('blanco' | 'interno'); `fecha` vacía es hoy en
    Montevideo. Es GET: el Contador (Finanzas en solo lectura) lo genera
    igual. Materializa los fijos antes, como el resto.
    """
    from services.daily import MONTEVIDEO, hoy_montevideo

    tipo = request.args.get("tipo") or ""
    if tipo not in BALANCE_TIPOS:
        return jsonify({"ok": False,
                        "error": "tipo tiene que ser 'blanco' o 'interno'"}), 400
    hoy = hoy_montevideo()
    corte = (request.args.get("fecha") or "").strip() or hoy.isoformat()
    if not fecha_valida(corte):
        return jsonify({"ok": False, "error": "fecha tiene que ser 'YYYY-MM-DD'"}), 400
    db = _db()
    materializar_recurrentes(db, hoy=hoy)
    generado = datetime.now(timezone.utc).astimezone(MONTEVIDEO)
    return jsonify(balance_general(db, tipo, corte,
                                   generado_en=generado.strftime("%Y-%m-%d %H:%M")))


def _validar_dato_balance(data: dict):
    """(campos, None) o (None, error). Ver `finanzas_balance_datos`."""
    clase = data.get("clase")
    if clase not in BALANCE_CLASES:
        return None, "clase tiene que ser activo, pasivo, capital o caja_inicial"
    rubros = BALANCE_CLASES[clase]
    rubro = data.get("rubro") or (next(iter(rubros)) if len(rubros) == 1 else None)
    if rubro not in rubros:
        return None, f"rubro inválido para {clase}: {rubro!r}"
    try:
        monto = float(data.get("monto_usd"))
    except (TypeError, ValueError):
        return None, "monto_usd tiene que ser un número"
    if monto != monto or monto in (float("inf"), float("-inf")):
        return None, "monto_usd tiene que ser un número"
    if clase == "caja_inicial":
        # Un saldo inicial puede ser negativo (arrancar en rojo con el banco).
        if abs(monto) < 0.005:
            return None, "el saldo inicial no puede ser cero"
    elif monto <= 0:
        return None, "monto_usd tiene que ser mayor que cero"
    desde = (data.get("desde") or "").strip()
    if not fecha_valida(desde):
        return None, "desde tiene que ser 'YYYY-MM-DD'"
    hasta = (data.get("hasta") or "").strip() or None
    if hasta and not fecha_valida(hasta):
        return None, "hasta tiene que ser 'YYYY-MM-DD'"
    if hasta and hasta <= desde:
        return None, "hasta tiene que ser posterior a desde"
    nombre = (data.get("nombre") or "").strip() or rubros[rubro]
    campos = {"clase": clase, "rubro": rubro, "nombre": nombre[:120],
              "monto_usd": round(monto, 2), "desde": desde, "hasta": hasta,
              "en_blanco": 1 if data.get("en_blanco", True) else 0}
    if "notas" in data:
        campos["notas"] = (data.get("notas") or "").strip() or None
    return campos, None


@finanzas_bp.route("/api/finanzas/balance-datos", methods=["GET"])
def api_listar_datos_balance():
    return jsonify({"datos": listar_datos_balance(_db()), "clases": BALANCE_CLASES})


@finanzas_bp.route("/api/finanzas/balance-datos", methods=["POST"])
def api_crear_dato_balance():
    campos, error = _validar_dato_balance(request.get_json(silent=True) or {})
    if error:
        return jsonify({"ok": False, "error": error}), 400
    uid, nombre = _quien()
    db = _db()
    did = crear_dato_balance(db, created_by_name=nombre, **campos)
    log_activity(db, nombre, "finanzas_dato_balance_creado", "finanzas", did,
                 campos["nombre"], f"{campos['clase']} USD {campos['monto_usd']}",
                 user_id=uid)
    return jsonify({"ok": True, "id": did}), 201


@finanzas_bp.route("/api/finanzas/balance-datos/<int:dato_id>", methods=["PUT"])
def api_actualizar_dato_balance(dato_id):
    db = _db()
    if not get_dato_balance(db, dato_id):
        return jsonify({"ok": False, "error": "no existe"}), 404
    campos, error = _validar_dato_balance(request.get_json(silent=True) or {})
    if error:
        return jsonify({"ok": False, "error": error}), 400
    actualizar_dato_balance(db, dato_id, **campos)
    uid, nombre = _quien()
    log_activity(db, nombre, "finanzas_dato_balance_editado", "finanzas", dato_id,
                 campos["nombre"], "", user_id=uid)
    return jsonify({"ok": True})


@finanzas_bp.route("/api/finanzas/balance-datos/<int:dato_id>", methods=["DELETE"])
def api_borrar_dato_balance(dato_id):
    db = _db()
    dato = get_dato_balance(db, dato_id)
    if not dato:
        return jsonify({"ok": False, "error": "no existe"}), 404
    borrar_dato_balance(db, dato_id)
    uid, nombre = _quien()
    log_activity(db, nombre, "finanzas_dato_balance_borrado", "finanzas", dato_id,
                 dato["nombre"], "", user_id=uid)
    return jsonify({"ok": True})


@finanzas_bp.route("/api/finanzas/categorias")
def api_categorias():
    return jsonify(CATEGORIAS)
