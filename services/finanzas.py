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


def periodo_de(fecha: str) -> str:
    """'2026-09-20' -> '2026-09'."""
    return fecha[:7]


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

# El embudo en orden, tal como lo define routes/leads.py. `no_interesa` NO está:
# es una salida, no una etapa, y un lead que se cayó ahí igual pasó por lo que
# haya pasado antes.
FUNNEL = ["sin_contactar", "interesado", "contactado", "reunion_agendada",
          "reunion_hecha", "presupuesto_enviado", "negociacion",
          "cliente_cerrado", "en_desarrollo", "finalizado"]


def alcanzo(eventos: set, etapa: str) -> bool:
    """Si el lead pasó por `etapa` o por cualquiera posterior, alguna vez.

    Se mira contra el historial de `lead_events`, no contra el `crm_status` de
    hoy: un lead que llegó a reunión y después se cayó a `no_interesa` figura
    hoy como `no_interesa`, y contarlo por el estado actual lo perdería.
    """
    objetivo = FUNNEL.index(etapa)
    return any(e in FUNNEL and FUNNEL.index(e) >= objetivo for e in eventos)


def _dividir(numerador: float, denominador: float):
    """El costo (o ROI) unitario, o None si no hay de qué dividir.

    Ninguno de los dos lados vale como "no hay dato" si es cero: sin
    denominador la división no está definida (un mes sin ventas no tiene un
    costo por venta de cero, no tiene costo por venta — la planilla mostraba
    #DIV/0! y esa era la lectura correcta), y sin numerador tampoco hay nada
    que repartir (sin inversión no hay "costo por lead $0", no hubo campaña
    que costear; sin ingresos atribuidos el ROI no es 0, todavía no se sabe).
    """
    if not numerador or not denominador:
        return None
    return round(numerador / denominador, 2)


def _fila_pauta(periodo, inversion, leads, calificados, demos, ventas, ingresos):
    return {
        "periodo": periodo,
        "inversion_usd": round(inversion, 2),
        "leads": leads, "calificados": calificados,
        "demos": demos, "ventas": ventas,
        "ingresos_usd": round(ingresos, 2),
        "cpl": _dividir(inversion, leads),
        "costo_calificado": _dividir(inversion, calificados),
        "costo_demo": _dividir(inversion, demos),
        "costo_venta": _dividir(inversion, ventas),
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
        if alcanzo(suyos, "reunion_agendada"):
            c["calificados"] += 1
        if alcanzo(suyos, "reunion_hecha"):
            c["demos"] += 1
        if alcanzo(suyos, "cliente_cerrado"):
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
