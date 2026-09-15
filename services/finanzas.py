"""Las cuentas de la sección financiera.

Todo lo que puede dar un número mal vive acá y se testea sin Flask ni base:
conversión de moneda, aritmética de períodos, materialización de los fijos y
los agregados del panel. Las rutas solo validan y serializan.
"""

import logging
import sqlite3
from datetime import date

from services.embudo import (EXCLUIDOS_DEL_FUNNEL, FUNNEL,  # noqa: F401
                             alcanzo)
# Alias privados: mantienen intactas las llamadas internas de este archivo
# y sus tests. El movimiento no cambia ningun comportamiento.
from services.embudo import costo as _costo
from services.embudo import dividir as _dividir
from services.embudo import normalizar_estado as _normalizar_estado  # noqa: F401

logger = logging.getLogger(__name__)

MONEDAS = ("USD", "UYU")

# Lista fija a propósito, no texto libre: con texto libre alcanza con escribir
# "Infra" una vez en lugar de "Infraestructura" para que el desglose por
# categoría se parta en dos sin que nadie lo note.
CATEGORIAS = {
    "egreso": ["infraestructura", "herramientas", "publicidad",
               "retiros", "impuestos", "servicios", "otros"],
    "ingreso": ["desarrollo_web", "software_medida", "mantenimiento",
                "marketing", "otros"],
}


# Tasa basica de Uruguay. Se guarda el IVA calculado en cada movimiento en vez
# de recalcularlo al leer: si esto alguna vez cambia, lo ya facturado tiene que
# seguir mostrando el impuesto que de verdad se cobro.
IVA_TASA = 0.22


def periodo_de(fecha: str) -> str:
    """'2026-09-20' -> '2026-09'."""
    return fecha[:7]


def iva_sobre(neto) -> float:
    """El IVA que se SUMA a ese monto. 100 -> 22, y el total es 122.

    El monto que se carga es el LIQUIDO, no el total: es como se acuerda un
    precio y como se cargan los gastos acá. La primera version hacia lo
    contrario —tomaba el monto como total y sacaba el impuesto de adentro, 100
    -> 81,97 + 18,03— y estaba mal: nadie escribe el numero con el IVA ya
    metido.

    Se llama `iva_sobre` y no `desglosar_iva` a proposito: la funcion vieja
    devolvia una tupla con el sentido invertido, y un renombre hace que
    cualquier llamador que haya quedado sin actualizar reviente en vez de
    seguir calculando mal en silencio.
    """
    return float(neto or 0) * IVA_TASA


def a_usd(monto: float, moneda: str, tipo_cambio: float | None) -> float:
    """Convierte a dólares. El resultado se congela en `monto_usd`.

    Un movimiento en pesos sin tipo de cambio es un error, no un cero: guardarlo
    con monto_usd = 0 lo haría desaparecer de los totales sin avisar.
    """
    if moneda not in MONEDAS:
        raise ValueError(f"moneda desconocida: {moneda!r}")
    if moneda == "USD":
        return round(float(monto), 2)
    if not tipo_cambio or float(tipo_cambio) <= 0:
        raise ValueError("un movimiento en UYU necesita un tipo de cambio > 0")
    return round(float(monto) / float(tipo_cambio), 2)


def _a_indice(periodo: str) -> int:
    anio, mes = periodo.split("-")
    return int(anio) * 12 + (int(mes) - 1)


def _a_periodo(indice: int) -> str:
    return f"{indice // 12:04d}-{indice % 12 + 1:02d}"


def meses_entre(desde: str, hasta: str) -> list[str]:
    """Los períodos de `desde` a `hasta`, ambos inclusive. Al revés, vacío."""
    return [_a_periodo(i) for i in range(_a_indice(desde), _a_indice(hasta) + 1)]


def periodo_anterior(desde: str, hasta: str) -> tuple[str, str]:
    """El bloque inmediatamente anterior, del mismo largo. Para la variación."""
    largo = _a_indice(hasta) - _a_indice(desde) + 1
    fin = _a_indice(desde) - 1
    return _a_periodo(fin - largo + 1), _a_periodo(fin)


def materializar_recurrentes(db_path: str, hoy: date | None = None) -> int:
    """Crea los movimientos que falten de cada fijo activo. Devuelve cuántos creó.

    Idempotente: se apoya en el índice único (recurrente_id, periodo), así que
    correrlo mil veces produce exactamente un movimiento por fijo y por mes.
    Esa es la guarda que exige la regla 3 de COORDINACION.md — cada deploy
    reinicia la máquina, y sin esto duplicaría los gastos del mes.

    Se llama perezosamente desde GET /api/finanzas/resumen. No hay hilo de
    arranque a propósito: los jobs de boot de este repo ya provocaron una tanda
    de mails reales, y un hilo más es una cosa más que puede fallar sin que
    nadie mire.
    """
    from database import crear_movimiento, listar_recurrentes

    hoy = hoy or date.today()
    mes_actual = f"{hoy.year:04d}-{hoy.month:02d}"
    creados = 0

    for fijo in listar_recurrentes(db_path, solo_activos=True):
        fin = min(fijo["hasta"], mes_actual) if fijo["hasta"] else mes_actual
        try:
            monto_usd = a_usd(fijo["monto"], fijo["moneda"], fijo["tipo_cambio"])
        except ValueError as e:
            # Un fijo mal cargado no puede tumbar la materialización del resto.
            logger.warning("fijo id=%s (%s) se saltea: %s",
                           fijo["id"], fijo["concepto"], e)
            continue

        dia = min(max(int(fijo["dia_del_mes"] or 1), 1), 28)
        # El IVA se calcula acá y no al leer: queda congelado en cada
        # movimiento, igual que los que se cargan a mano.
        #
        # Ojo con lo que esto NO hace: prenderle el IVA a un fijo ya
        # materializado no toca los meses generados, porque el INSERT de abajo
        # choca con el índice único y se descarta. Es la misma regla que ya
        # regía para el monto —cambiarle el precio a un fijo no reescribe el
        # pasado— y es deliberada: reescribir hacia atrás tocaría meses
        # cerrados, que es justo lo que el candado existe para impedir. El mes
        # en curso se corrige editando el movimiento, que está abierto.
        facturado = 1 if fijo["facturado"] else 0
        iva_usd = iva_sobre(monto_usd) if facturado else 0.0
        for periodo in meses_entre(fijo["desde"], fin):
            try:
                crear_movimiento(
                    db_path,
                    tipo=fijo["tipo"],
                    fecha=f"{periodo}-{dia:02d}",
                    periodo=periodo,
                    concepto=fijo["concepto"],
                    categoria=fijo["categoria"],
                    monto=fijo["monto"],
                    moneda=fijo["moneda"],
                    tipo_cambio=fijo["tipo_cambio"],
                    monto_usd=monto_usd,
                    client_id=fijo["client_id"],
                    recurrente_id=fijo["id"],
                    facturado=facturado,
                    iva_usd=iva_usd,
                    created_by_name="fijo",
                )
                creados += 1
            except sqlite3.IntegrityError as e:
                es_duplicado = (
                    getattr(e, "sqlite_errorname", "") == "SQLITE_CONSTRAINT_UNIQUE"
                    or "UNIQUE constraint failed" in str(e)
                )
                if not es_duplicado:
                    # Cualquier otra violación (NOT NULL, CHECK, ...) es un bug
                    # real: tiene que hacer ruido, no desaparecer como si fuera
                    # el duplicado esperado.
                    raise
                # Ya existía ese (recurrente_id, periodo). Es el camino normal:
                # todas las corridas después de la primera pasan por acá.

    return creados


def _totales(movimientos: list[dict]) -> tuple[float, float]:
    ingresos = sum(m["monto_usd"] for m in movimientos if m["tipo"] == "ingreso")
    egresos = sum(m["monto_usd"] for m in movimientos if m["tipo"] == "egreso")
    return round(ingresos, 2), round(egresos, 2)


def _totales_con_iva(movimientos: list[dict]) -> tuple[float, float]:
    """Lo mismo pero con el impuesto sumado: es la plata que se mueve de verdad.

    Los KPIs muestran el liquido en grande —que es lo que se escribe y lo que
    de verdad es tuyo— y esto abajo en chico, para poder cuadrar contra el
    banco sin tener que hacer la cuenta a mano.
    """
    def _con(tipo):
        return sum((m["monto_usd"] or 0) + (m["iva_usd"] or 0)
                   for m in movimientos if m["tipo"] == tipo)
    return round(_con("ingreso"), 2), round(_con("egreso"), 2)


def mes_editable(db_path: str, periodo, hoy: str | None = None) -> bool:
    """Si ese mes se puede tocar.

    El mes en curso y los futuros, siempre. Los pasados solo si alguien los
    reabrio a proposito: la idea es que cerrar un mes no requiera que nadie se
    acuerde de cerrarlo.

    Un periodo mal formado devuelve False. Ante la duda no se toca: un dato
    ilegible no puede abrir la puerta a editar cualquier cosa.
    """
    from database import listar_meses_abiertos

    texto = str(periodo or "")
    if len(texto) != 7 or texto[4] != "-":
        return False
    try:
        int(texto[:4]), int(texto[5:])
    except ValueError:
        return False

    hoy = hoy or date.today().isoformat()
    if texto >= hoy[:7]:
        return True
    return any(m["periodo"] == texto for m in listar_meses_abiertos(db_path))


def meses_con_datos(db_path: str) -> list[str]:
    """Los periodos que tienen algun movimiento, del mas viejo al mas nuevo.

    Es lo que el navegador usa para saber hasta donde puede ir para atras.
    """
    from database import listar_movimientos

    return sorted({m["periodo"] for m in listar_movimientos(db_path)
                   if m.get("periodo")})


def estado_de_cobro(vence, hoy: str | None = None) -> dict:
    """Si ese pendiente esta vencido y como se lee eso en la pantalla.

    `hoy` se puede pasar para poder testearlo sin congelar el reloj.

    Una fecha ilegible se trata como "sin fecha" en vez de reventar: esto
    dibuja una pestania entera y un dato mal cargado no puede dejarla en
    blanco.
    """
    hoy = hoy or date.today().isoformat()
    try:
        d_vence = date.fromisoformat(str(vence))
        d_hoy = date.fromisoformat(hoy)
    except (TypeError, ValueError):
        return {"vencido": False, "dias": None, "texto": "sin fecha"}

    dias = (d_hoy - d_vence).days
    if dias > 0:
        plural = "día" if dias == 1 else "días"
        return {"vencido": True, "dias": dias,
                "texto": f"vencido hace {dias} {plural}"}
    if dias == 0:
        return {"vencido": False, "dias": 0, "texto": "vence hoy"}
    return {"vencido": False, "dias": -dias,
            "texto": f"vence {d_vence.strftime('%d/%m')}"}


def saldar_por_cobrar(db_path: str, pc_id: int, fecha: str,
                      facturado: bool = False, categoria: str = "otros",
                      created_by_id=None, created_by_name=None) -> int:
    """Cobra el pendiente: crea el ingreso y lo deja marcado. Devuelve el id.

    Saldar tiene que mover la caja. Si solo marcara el pendiente, la pestania
    diria que cobraste y los KPIs que no, y ese desacuerdo se descubre cuando
    alguien cierra el mes.
    """
    from database import (actualizar_por_cobrar, crear_movimiento,
                          get_por_cobrar)

    pendiente = get_por_cobrar(db_path, pc_id)
    if not pendiente:
        raise ValueError("ese pendiente no existe")
    if pendiente["cobrado_movimiento_id"]:
        raise ValueError("ese pendiente ya se cobró")

    monto = pendiente["monto_usd"]
    iva = iva_sobre(monto) if facturado else 0.0
    mid = crear_movimiento(
        db_path, tipo="ingreso", fecha=fecha, periodo=periodo_de(fecha),
        concepto=pendiente["concepto"], categoria=categoria,
        monto=monto, moneda="USD", monto_usd=monto,
        client_id=pendiente["client_id"],
        facturado=1 if facturado else 0, iva_usd=iva,
        created_by_id=created_by_id, created_by_name=created_by_name)
    actualizar_por_cobrar(db_path, pc_id, cobrado_movimiento_id=mid)
    return mid


def resumen_iva(db_path: str, periodo: str) -> dict:
    """Lo facturado del mes, y cuánto IVA queda a pagar o a favor.

    El saldo a favor se arrastra al mes siguiente porque en Uruguay el crédito
    de IVA no vence. El saldo a pagar NO se arrastra: se paga y el mes queda en
    cero. Por eso el arrastre que entra es siempre <= 0.

    El arrastre obliga a recorrer los meses desde el primero con movimientos
    facturados: el saldo de septiembre depende de agosto, que depende de julio.
    Un mes vacío en el medio no corta la cadena, solo la deja pasar.
    """
    from database import listar_movimientos

    facturados = [m for m in listar_movimientos(db_path)
                  if m.get("facturado")]

    arrastre = 0.0
    if facturados:
        primero = min(m["periodo"] for m in facturados)
        for mes in meses_entre(primero, periodo):
            if mes == periodo:
                break
            cobrado, pagado = _iva_del_mes(facturados, mes)
            # Lo que se debe se paga; solo el crédito sigue viaje.
            arrastre = min(0.0, cobrado - pagado + arrastre)

    cobrado, pagado = _iva_del_mes(facturados, periodo)
    del_mes = [m for m in facturados if m["periodo"] == periodo]

    return {
        "periodo": periodo,
        "iva_cobrado": cobrado,
        "iva_pagado": pagado,
        "arrastre": arrastre,
        "saldo": cobrado - pagado + arrastre,
        # El monto cargado es el LIQUIDO: el total lo suma el impuesto, no lo
        # contiene. Al reves —que era como estaba— 100 daba 81,97 + 18,03.
        "movimientos": [{
            "id": m["id"],
            "concepto": m["concepto"],
            "tipo": m["tipo"],
            "neto": m["monto_usd"] or 0,
            "iva": m["iva_usd"] or 0,
            "total": (m["monto_usd"] or 0) + (m["iva_usd"] or 0),
        } for m in del_mes],
    }


def _iva_del_mes(facturados: list[dict], periodo: str) -> tuple[float, float]:
    """(iva cobrado, iva pagado) de ese mes."""
    cobrado = sum(m["iva_usd"] or 0 for m in facturados
                  if m["periodo"] == periodo and m["tipo"] == "ingreso")
    pagado = sum(m["iva_usd"] or 0 for m in facturados
                 if m["periodo"] == periodo and m["tipo"] == "egreso")
    return cobrado, pagado


def resumen(db_path: str, desde: str, hasta: str) -> dict:
    """KPIs, serie mensual y desgloses del período. `desde`/`hasta` inclusive.

    No materializa: eso lo hace la ruta antes de llamar acá, para que el
    servicio se pueda testear sin efectos.
    """
    from database import listar_movimientos

    movs = listar_movimientos(db_path, desde=desde, hasta=hasta)
    ingresos, egresos = _totales(movs)
    ingresos_con_iva, egresos_con_iva = _totales_con_iva(movs)

    prev_desde, prev_hasta = periodo_anterior(desde, hasta)
    prev = listar_movimientos(db_path, desde=prev_desde, hasta=prev_hasta)
    ingresos_prev, egresos_prev = _totales(prev)

    por_periodo: dict[str, list[dict]] = {p: [] for p in meses_entre(desde, hasta)}
    for m in movs:
        por_periodo.setdefault(m["periodo"], []).append(m)

    serie = []
    for periodo in meses_entre(desde, hasta):
        i, e = _totales(por_periodo.get(periodo, []))
        serie.append({"periodo": periodo, "ingresos_usd": i,
                      "egresos_usd": e, "neto_usd": round(i - e, 2)})

    cat: dict[tuple[str, str], float] = {}
    for m in movs:
        clave = (m["tipo"], m["categoria"])
        cat[clave] = cat.get(clave, 0.0) + m["monto_usd"]
    por_categoria = [{"tipo": t, "categoria": c, "total_usd": round(v, 2)}
                     for (t, c), v in cat.items()]
    por_categoria.sort(key=lambda x: x["total_usd"], reverse=True)

    por_cliente = _ingresos_por_cliente(db_path, movs)

    return {
        "desde": desde, "hasta": hasta,
        "kpis": {
            "ingresos_usd": ingresos,
            "egresos_usd": egresos,
            "neto_usd": round(ingresos - egresos, 2),
            "ingresos_previos_usd": ingresos_prev,
            "egresos_previos_usd": egresos_prev,
            "neto_previo_usd": round(ingresos_prev - egresos_prev, 2),
            # Con el IVA sumado: la plata que de verdad se movió, para cuadrar
            # contra el banco. Los movimientos sin factura suman igual acá,
            # porque su total ES su líquido.
            "ingresos_con_iva_usd": ingresos_con_iva,
            "egresos_con_iva_usd": egresos_con_iva,
            "neto_con_iva_usd": round(ingresos_con_iva - egresos_con_iva, 2),
        },
        "serie": serie,
        "por_categoria": por_categoria,
        "por_cliente": por_cliente,
    }


def _ingresos_por_cliente(db_path: str, movs: list[dict]) -> list[dict]:
    """Ingresos agrupados por cliente. Los sin atribuir no aparecen."""
    from database import _connect

    totales: dict[int, float] = {}
    for m in movs:
        if m["tipo"] != "ingreso" or not m["client_id"]:
            continue
        totales[m["client_id"]] = totales.get(m["client_id"], 0.0) + m["monto_usd"]
    if not totales:
        return []

    marcas = ", ".join("?" for _ in totales)
    conn = _connect(db_path)
    try:
        filas = conn.execute(
            f"SELECT id, name FROM businesses WHERE id IN ({marcas})",
            list(totales)).fetchall()
    finally:
        conn.close()
    nombres = {f["id"]: f["name"] for f in filas}

    salida = [{"client_id": cid, "nombre": nombres.get(cid, f"#{cid}"),
               "total_usd": round(v, 2)} for cid, v in totales.items()]
    salida.sort(key=lambda x: x["total_usd"], reverse=True)
    return salida


# ─── Balance ─────────────────────────────────────────────────────────────────

# Pedido de Juan (15/9): "generar balance hasta el momento", de dos maneras:
# "en blanco" (lo que se contabiliza, con impuestos) e "interno" (lo que está
# en blanco y lo que no).
#
# Qué es "en blanco" en los datos. No hay campo de comprobante, número de
# factura ni cuenta bancaria: la única marca es `facturado` (el "¿Lleva IVA
# (22%)?" del alta), que es la que ya decide qué entra en la pestaña IVA. Se
# suma UN caso: los egresos de la categoría `impuestos` (IRAE, BPS, IVA a
# DGI...). Un pago de impuestos no trae factura con IVA, así que sale con
# facturado = 0, pero es por definición contable: dejarlo afuera del balance
# en blanco mostraría una empresa que no paga impuestos.
BALANCE_TIPOS = {"blanco": "En blanco (contable)", "interno": "Interno (todo)"}


def es_en_blanco(m: dict) -> bool:
    """Si ese movimiento se contabiliza. Ver el comentario de arriba."""
    if m.get("facturado"):
        return True
    return m.get("tipo") == "egreso" and m.get("categoria") == "impuestos"


def fecha_valida(texto) -> bool:
    """'YYYY-MM-DD' que además sea un día que existe (no 2026-02-30)."""
    texto = str(texto or "")
    if len(texto) != 10 or texto[4] != "-" or texto[7] != "-":
        return False
    try:
        date.fromisoformat(texto)
    except ValueError:
        return False
    return True


def _r2(x: float) -> float:
    # `+ 0.0` evita un -0.0 que en pantalla se lee "USD -0,00".
    return round(x, 2) + 0.0


def _bloque_balance(movs: list[dict], tipo: str) -> dict:
    """Total, parte en blanco, parte no facturada y desglose por categoría."""
    cats: dict[str, dict] = {}
    total = blanco = 0.0
    for m in movs:
        if m["tipo"] != tipo:
            continue
        usd = float(m["monto_usd"])
        en_blanco = es_en_blanco(m)
        c = cats.setdefault(m["categoria"], {"total": 0.0, "blanco": 0.0})
        c["total"] += usd
        total += usd
        if en_blanco:
            c["blanco"] += usd
            blanco += usd
    por_categoria = [{"categoria": k, "total": _r2(v["total"]),
                      "blanco": _r2(v["blanco"]),
                      "no_facturado": _r2(v["total"] - v["blanco"])}
                     for k, v in cats.items()]
    por_categoria.sort(key=lambda x: (-x["total"], x["categoria"]))
    return {"total": _r2(total), "blanco": _r2(blanco),
            "no_facturado": _r2(total - blanco), "por_categoria": por_categoria}


def calcular_balance(movimientos: list[dict], tipo: str, desde: str, hasta: str,
                     generado_en: str = "") -> dict:
    """El balance de `desde` a `hasta` (fechas 'YYYY-MM-DD', ambas inclusive).

    Pura: recibe los movimientos ya leídos y no toca la base, para poder
    testear cada borde sin armar nada. Todo en USD con el `monto_usd`
    congelado de cada movimiento, igual que el resto de Finanzas.

    - Los fijos cuentan solo como los movimientos que ya materializaron: acá
      no se lee `finanzas_recurrentes`, así que no hay forma de contarlos dos
      veces. Los anulados (un fijo borrado) quedan afuera.
    - Se filtra por FECHA y no por período: "hasta el momento" es hasta hoy,
      y un fijo del día 20 todavía no pasó el día 15.
    - Un movimiento sin `monto_usd` no se puede sumar: queda afuera y se
      cuenta en `sin_cotizacion`, como hacen los totales de los fijos.
    """
    if tipo not in BALANCE_TIPOS:
        raise ValueError(f"tipo tiene que ser uno de {tuple(BALANCE_TIPOS)}")
    if not (fecha_valida(desde) and fecha_valida(hasta)):
        raise ValueError("desde y hasta tienen que ser 'YYYY-MM-DD'")
    if desde > hasta:
        raise ValueError("desde tiene que ser <= hasta")

    del_periodo = [m for m in movimientos
                   if not m.get("anulado")
                   and desde <= str(m.get("fecha") or "")[:10] <= hasta
                   and (tipo == "interno" or es_en_blanco(m))]
    sin_cotizacion = sum(1 for m in del_periodo if m.get("monto_usd") is None)
    movs = [m for m in del_periodo if m.get("monto_usd") is not None]

    ingresos = _bloque_balance(movs, "ingreso")
    egresos = _bloque_balance(movs, "egreso")

    iva_ventas = sum(float(m.get("iva_usd") or 0) for m in movs
                     if m["tipo"] == "ingreso")
    iva_compras = sum(float(m.get("iva_usd") or 0) for m in movs
                      if m["tipo"] == "egreso")
    saldo_iva = iva_ventas - iva_compras

    impuestos: dict[str, float] = {}
    for m in movs:
        if m["tipo"] == "egreso" and m["categoria"] == "impuestos":
            concepto = (m.get("concepto") or "").strip() or "Sin concepto"
            impuestos[concepto] = impuestos.get(concepto, 0.0) + float(m["monto_usd"])

    meses = {p: [0.0, 0.0] for p in meses_entre(desde[:7], hasta[:7])}
    for m in movs:
        fila = meses.setdefault(m["fecha"][:7], [0.0, 0.0])
        fila[0 if m["tipo"] == "ingreso" else 1] += float(m["monto_usd"])

    return {
        "tipo": tipo,
        "tipo_nombre": BALANCE_TIPOS[tipo],
        "desde": desde,
        "hasta": hasta,
        "generado_en": generado_en,
        "movimientos": len(movs),
        "sin_cotizacion": sin_cotizacion,
        "ingresos": ingresos,
        "egresos": egresos,
        "resultado": {
            "total": _r2(ingresos["total"] - egresos["total"]),
            "blanco": _r2(ingresos["blanco"] - egresos["blanco"]),
            "no_facturado": _r2(ingresos["no_facturado"] - egresos["no_facturado"]),
        },
        # Débito (lo cobrado en las ventas) menos crédito (lo pagado en las
        # compras). Positivo es a pagar, negativo a favor. Es el saldo del
        # período entero, sin el arrastre mes a mes de la pestaña IVA.
        "iva": {
            "ventas": _r2(iva_ventas),
            "compras": _r2(iva_compras),
            "saldo": _r2(saldo_iva),
        },
        "con_iva": {
            "ingresos": _r2(ingresos["total"] + iva_ventas),
            "egresos": _r2(egresos["total"] + iva_compras),
            "resultado": _r2(ingresos["total"] + iva_ventas
                             - egresos["total"] - iva_compras),
        },
        "impuestos": {
            "total": _r2(sum(impuestos.values())),
            "por_concepto": sorted(
                ({"concepto": k, "total": _r2(v)} for k, v in impuestos.items()),
                key=lambda x: (-x["total"], x["concepto"])),
        },
        "meses": [{"periodo": p, "ingresos": _r2(v[0]), "egresos": _r2(v[1]),
                   "resultado": _r2(v[0] - v[1])}
                  for p, v in sorted(meses.items())],
    }


def periodo_balance(desde: str | None, hasta: str | None, hoy: date,
                    primer_movimiento: str | None = None) -> tuple[str, str]:
    """Resuelve el período pedido. `hoy` es el día en Montevideo.

    - `hasta` vacío: hoy ("hasta el momento").
    - `desde` vacío: el 1 de enero del año en curso ("Este año").
    - `desde` = 'inicio': la fecha del primer movimiento (o el 1 de enero si
      todavía no hay ninguno).
    """
    hasta = (hasta or "").strip() or hoy.isoformat()
    desde = (desde or "").strip()
    if desde == "inicio":
        desde = (primer_movimiento or "")[:10] or f"{hoy.year:04d}-01-01"
        # Un primer movimiento con fecha futura no puede dejar el período al
        # revés: se toma el más chico de los dos.
        desde = min(desde, hasta)
    elif not desde:
        desde = f"{hoy.year:04d}-01-01"
    return desde, hasta


def balance(db_path: str, tipo: str, desde: str, hasta: str,
            generado_en: str = "") -> dict:
    """Lee los movimientos del período y arma el balance. No materializa."""
    from database import listar_movimientos

    movs = listar_movimientos(db_path, desde=desde[:7], hasta=hasta[:7])
    return calcular_balance(movs, tipo, desde, hasta, generado_en=generado_en)


def primer_movimiento(db_path: str) -> str | None:
    """La fecha del movimiento más viejo, o None si no hay ninguno."""
    from database import _connect

    conn = _connect(db_path)
    try:
        fila = conn.execute("SELECT MIN(fecha) FROM finanzas_movimientos "
                            "WHERE anulado = 0").fetchone()
    finally:
        conn.close()
    return fila[0] if fila and fila[0] else None


# ─── Rendimiento de la pauta ─────────────────────────────────────────────────

# El embudo (FUNNEL, alcanzo, _dividir, _costo) se mudo a
# services/embudo.py el 10/9/2026: el modulo de marketing cuenta las mismas
# etapas y dos definiciones darian dos numeros distintos para la misma
# pregunta en dos paneles del mismo CRM. Se importa arriba.


def _fila_pauta(periodo, inversion, leads, calificados, demos, ventas, ingresos):
    return {
        "periodo": periodo,
        "inversion_usd": round(inversion, 2),
        "leads": leads, "calificados": calificados,
        "demos": demos, "ventas": ventas,
        "ingresos_usd": round(ingresos, 2),
        "cpl": _costo(inversion, leads),
        "costo_calificado": _costo(inversion, calificados),
        "costo_demo": _costo(inversion, demos),
        "costo_venta": _costo(inversion, ventas),
        # El ROI es distinto: con inversión y sin ingresos, 0.0 es un
        # resultado medido (se gastó y no volvió nada todavía), no un dato
        # faltante. Solo sin inversión el ROI no está definido.
        "roi": _dividir(ingresos, inversion),
    }


def rendimiento_pauta(db_path: str, desde: str, hasta: str) -> dict:
    """Qué compró la plata de pauta, mes a mes.

    Reemplaza la hoja «Análisis» de `Scalerics - Leads - 2026.xlsx`, que se
    llevaba a mano. Los leads se cuentan por el mes en que entraron, no por el
    mes en que convirtieron: la pauta de marzo compró los leads de marzo, aunque
    uno cierre en julio. Por lo mismo, el ingreso se atribuye al lead que lo
    generó y no al mes del cobro.
    """
    from database import ENVIOS_META_SQL, _connect, listar_movimientos

    periodos = meses_entre(desde, hasta)
    if not periodos:
        return {"desde": desde, "hasta": hasta, "meses": [],
                "total": _fila_pauta("total", 0, 0, 0, 0, 0, 0)}

    inversion = {p: 0.0 for p in periodos}
    for m in listar_movimientos(db_path, desde=desde, hasta=hasta,
                                tipo="egreso", categoria="publicidad"):
        inversion[m["periodo"]] = inversion.get(m["periodo"], 0.0) + m["monto_usd"]

    conn = _connect(db_path)
    try:
        leads = conn.execute(
            "SELECT id, primero, substr(fecha_local, 1, 7) AS periodo "
            f"FROM ({ENVIOS_META_SQL}) WHERE source = 'meta'").fetchall()
        eventos_filas = conn.execute(
            "SELECT lead_id, new_status FROM lead_events").fetchall()
        ingresos_filas = conn.execute(
            "SELECT client_id, monto_usd FROM finanzas_movimientos "
            "WHERE tipo = 'ingreso' AND anulado = 0 AND client_id IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    eventos: dict = {}
    for fila in eventos_filas:
        eventos.setdefault(fila["lead_id"], set()).add(fila["new_status"])

    ingresos_por_lead: dict = {}
    for fila in ingresos_filas:
        ingresos_por_lead[fila["client_id"]] = (
            ingresos_por_lead.get(fila["client_id"], 0.0) + fila["monto_usd"])

    conteo = {p: {"leads": 0, "calificados": 0, "demos": 0, "ventas": 0,
                  "ingresos": 0.0} for p in periodos}
    for lead in leads:
        periodo = lead["periodo"]
        if periodo not in conteo:
            continue
        c = conteo[periodo]
        c["leads"] += 1
        # Los leads son envios de formulario, como los cuenta Meta: quien
        # vuelve a escribir cuenta en el mes de la vuelta. Sus etapas y su
        # ingreso quedan en el mes de su primer envio, una sola vez.
        if not lead["primero"]:
            continue
        suyos = eventos.get(lead["id"], set())
        c["ingresos"] += ingresos_por_lead.get(lead["id"], 0.0)
        if alcanzo(suyos, "demo_agendada"):
            c["calificados"] += 1
        if alcanzo(suyos, "demo_1"):
            c["demos"] += 1
        if alcanzo(suyos, "cerrado"):
            c["ventas"] += 1

    meses = [_fila_pauta(p, inversion.get(p, 0.0), conteo[p]["leads"],
                         conteo[p]["calificados"], conteo[p]["demos"],
                         conteo[p]["ventas"], conteo[p]["ingresos"])
             for p in periodos]

    total = _fila_pauta("total",
                        sum(inversion.get(p, 0.0) for p in periodos),
                        sum(c["leads"] for c in conteo.values()),
                        sum(c["calificados"] for c in conteo.values()),
                        sum(c["demos"] for c in conteo.values()),
                        sum(c["ventas"] for c in conteo.values()),
                        sum(c["ingresos"] for c in conteo.values()))

    return {"desde": desde, "hasta": hasta, "meses": meses, "total": total}
