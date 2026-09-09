"""Las cuentas de la sección financiera.

Todo lo que puede dar un número mal vive acá y se testea sin Flask ni base:
conversión de moneda, aritmética de períodos, materialización de los fijos y
los agregados del panel. Las rutas solo validan y serializan.
"""

import logging
import sqlite3
from datetime import date

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


def desglosar_iva(total: float) -> tuple[float, float]:
    """(neto, iva) a partir del TOTAL, con el impuesto ya adentro.

    El monto que se carga es el de la factura: 500 son 409,84 propios mas 90,16
    de impuesto. Al reves —500 mas 22%— daria 610 y ese numero no existe en
    ningun papel.
    """
    total = float(total or 0)
    neto = total / (1 + IVA_TASA)
    return neto, total - neto


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
    iva = desglosar_iva(monto)[1] if facturado else 0.0
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
        "movimientos": [{
            "id": m["id"],
            "concepto": m["concepto"],
            "tipo": m["tipo"],
            "neto": (m["monto_usd"] or 0) - (m["iva_usd"] or 0),
            "iva": m["iva_usd"] or 0,
            "total": m["monto_usd"] or 0,
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


# ─── Rendimiento de la pauta ─────────────────────────────────────────────────

# El embudo en orden, con el vocabulario nuevo de ETAPAS_PRECLIENTE /
# ETAPAS_CLIENTE (database.py). `no_interesa`, `en_espera` y `rechazo` NO
# están: son salidas o pausas, no etapas.
#
# `no_interesa` es "nunca enganchó". `en_espera` es "frenado por el cliente,
# sin cerrar" y `rechazo` es "dijo que no después de haber avanzado" — ninguno
# de los dos dice hasta dónde llegó el lead, eso ya lo dicen sus eventos
# anteriores. Meterlos en la lista ordenada haría que un lead rechazado
# figurara más avanzado que uno en `presupuesto_enviado`, que es al revés de
# lo que pasó: un lead que se cayó ahí igual pasó por lo que haya pasado antes.
FUNNEL = ["sin_contactar", "interesado", "contactado",
          "demo_agendada", "demo_1", "demo_2", "demo_3",
          "presupuesto_enviado", "follow_up_1", "follow_up_2", "acepto",
          "cerrado", "en_desarrollo", "finalizado"]

# Exclusiones explícitas y documentadas: por qué cada una no entra a FUNNEL
# aunque forme parte de ETAPAS_PRECLIENTE / ETAPAS_CLIENTE.
EXCLUIDOS_DEL_FUNNEL = {
    "en_espera": "pausa del cliente, no un avance",
    "rechazo": "salida tras haber avanzado, no una etapa",
}


def _normalizar_estado(estado: str) -> str:
    """Traduce un `crm_status` viejo al vocabulario nuevo, si corresponde.

    `lead_events` mezcla las dos épocas: la migración de `database.py`
    reescribe `businesses.crm_status` pero no toca el historial de eventos,
    así que todo lo de antes de la migración quedó con los nombres viejos
    (`reunion_agendada`, `reunion_hecha`, `negociacion`, `cliente_cerrado`, y
    los alias `agendo`/`firmo`) y todo lo de después ya nace con los nuevos.
    Sin esto, `alcanzo` dejaría de contar cualquier lead viejo.
    """
    from database import _MAPA_ESTADOS_VIEJOS

    return _MAPA_ESTADOS_VIEJOS.get(estado, estado)


def alcanzo(eventos: set, etapa: str) -> bool:
    """Si el lead pasó por `etapa` o por cualquiera posterior, alguna vez.

    Se mira contra el historial de `lead_events`, no contra el `crm_status` de
    hoy: un lead que llegó a demo y después se cayó a `no_interesa` figura hoy
    como `no_interesa`, y contarlo por el estado actual lo perdería.

    Los eventos se normalizan antes de comparar (ver `_normalizar_estado`),
    porque el historial trae nombres viejos y nuevos mezclados.
    """
    objetivo = FUNNEL.index(etapa)
    normalizados = {_normalizar_estado(e) for e in eventos}
    return any(e in FUNNEL and FUNNEL.index(e) >= objetivo for e in normalizados)


def _dividir(numerador: float, denominador: float):
    """División simple, o None si el denominador es cero."""
    if not denominador:
        return None
    return round(numerador / denominador, 2)


def _costo(inversion: float, cantidad: float):
    """Costo unitario, o None si no hay de qué dividir.

    Sin `cantidad` la división no está definida: un mes sin ventas no tiene un
    costo por venta de cero, no tiene costo por venta — la planilla mostraba
    #DIV/0! y esa era la lectura correcta. Sin `inversión` tampoco: no hubo
    campaña que costear, así que no es un costo de cero.
    """
    if not inversion:
        return None
    return _dividir(inversion, cantidad)


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
    from database import _connect, listar_movimientos

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
            "SELECT id, substr(scraped_at, 1, 7) AS periodo "
            "FROM businesses WHERE source = 'meta'").fetchall()
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
        suyos = eventos.get(lead["id"], set())
        c = conteo[periodo]
        c["leads"] += 1
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
