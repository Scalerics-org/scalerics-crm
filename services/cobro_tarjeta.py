"""Las cuentas de cobrar con tarjeta a través de Plexo.

Pedido de Juan (23/9): "poder ver cuánto tendría que cobrar si quiero
llevarme tanto, y que me cargue el IVA compras y ventas automático". Cobrar
con tarjeta abre tres números que no coinciden y que a fin de mes se mezclan:

- lo que se le cobra a la tarjeta (precio + IVA),
- lo que deposita la tarjeta (eso menos la comisión y el IVA de la comisión),
- lo que es de Scalerics (el precio menos la comisión y lo que cobra Plexo).

La diferencia entre lo que entra al banco y lo que es tuyo es el IVA que hay
que pagarle a DGI: el de la venta, menos el de las facturas de la tarjeta y de
Plexo, que son IVA compras.

Todo acá es puro (sin Flask ni base) para poder testear cada centavo. La
cuenta se hace en la moneda del cobro: si se cobra en pesos, los costos de
Plexo (que son en pesos) no se convierten; si se cobra en dólares, sí.
"""

from datetime import date, timedelta

from services.finanzas import IVA_TASA, MONEDAS, a_usd, iva_sobre, periodo_de

# Los aranceles del Plan Clásico que pasó OCA el 23/9 (foto de la tabla
# "Aranceles y Comisiones"). OCA es el adquirente de Visa y Master acá; la
# tarjeta OCA propia no venía en la tabla, por eso arranca sin número: sin
# comisión la calculadora avisa en vez de suponer un 0% que haría parecer
# gratis el cobro.
TARJETAS = {
    "visa_credito": "Visa crédito (1 pago)",
    "master_credito": "Master crédito (1 pago)",
    "oca_credito": "OCA crédito (1 pago)",
    "visa_debito": "Visa débito",
    "master_debito": "Master débito",
    "oca_debito": "OCA débito",
}

AJUSTES_POR_DEFECTO = {
    "comisiones": {
        "visa_credito": 3.30,
        "master_credito": 3.35,
        "oca_credito": None,
        "visa_debito": 1.05,
        "master_debito": 1.15,
        "oca_debito": None,
    },
    # Días HÁBILES hasta que la tarjeta deposita. Crédito en 1 pago: 15 días
    # hábiles. Débito: 24 horas, que es el día hábil siguiente.
    "dias_credito": 15,
    "dias_debito": 1,
    # Contrato con Plexo, en pesos y sin IVA.
    "plexo_por_cobro_uyu": 9.49,
    "plexo_fijo_uyu": 4019.0,
    "tipo_cambio": 40.0,
    # Entre cuántos clientes se reparte el fijo de Plexo en la calculadora.
    "clientes_tarjeta": 5,
}

MODOS = {
    "quiero_llevarme": "Quiero que me quede",
    "precio": "Le cobro (sin IVA)",
    "total": "Le cobro a la tarjeta (con IVA)",
}


def _r2(x: float) -> float:
    # `+ 0.0` evita un -0.0 que en pantalla se lee "-0,00".
    return round(x, 2) + 0.0


def es_debito(tarjeta: str) -> bool:
    return tarjeta.endswith("_debito")


def unir_ajustes(guardados: dict | None) -> dict:
    """Los ajustes guardados encima de los de fábrica.

    Una clave que falta en lo guardado toma el default: así, sumar un ajuste
    nuevo no obliga a migrar lo que ya estaba guardado.
    """
    guardados = guardados or {}
    salida = {k: v for k, v in AJUSTES_POR_DEFECTO.items() if k != "comisiones"}
    salida["comisiones"] = dict(AJUSTES_POR_DEFECTO["comisiones"])
    for clave, valor in (guardados.get("comisiones") or {}).items():
        if clave in TARJETAS:
            salida["comisiones"][clave] = valor
    for clave in salida:
        if clave != "comisiones" and clave in guardados:
            salida[clave] = guardados[clave]
    return salida


def validar_ajustes(data: dict) -> tuple[dict | None, str | None]:
    """Devuelve (ajustes listos para guardar, None) o (None, error)."""
    comisiones = {}
    for clave in TARJETAS:
        valor = (data.get("comisiones") or {}).get(clave)
        if valor in (None, ""):
            comisiones[clave] = None
            continue
        try:
            valor = float(valor)
        except (TypeError, ValueError):
            return None, f"la comisión de {TARJETAS[clave]} tiene que ser un número"
        if not 0 <= valor < 50:
            return None, f"la comisión de {TARJETAS[clave]} tiene que estar entre 0 y 50%"
        comisiones[clave] = valor

    salida = {"comisiones": comisiones}
    enteros = {"dias_credito": (0, 120), "dias_debito": (0, 120),
               "clientes_tarjeta": (1, 10000)}
    for clave, (minimo, maximo) in enteros.items():
        try:
            valor = int(data.get(clave, AJUSTES_POR_DEFECTO[clave]))
        except (TypeError, ValueError):
            return None, f"{clave} tiene que ser un número entero"
        if not minimo <= valor <= maximo:
            return None, f"{clave} tiene que estar entre {minimo} y {maximo}"
        salida[clave] = valor
    for clave in ("plexo_por_cobro_uyu", "plexo_fijo_uyu"):
        try:
            valor = float(data.get(clave, AJUSTES_POR_DEFECTO[clave]))
        except (TypeError, ValueError):
            return None, f"{clave} tiene que ser un número"
        if valor < 0:
            return None, f"{clave} no puede ser negativo"
        salida[clave] = valor
    try:
        tc = float(data.get("tipo_cambio", AJUSTES_POR_DEFECTO["tipo_cambio"]))
    except (TypeError, ValueError):
        return None, "el tipo de cambio tiene que ser un número"
    if tc <= 0:
        return None, "el tipo de cambio tiene que ser mayor que cero"
    salida["tipo_cambio"] = tc
    return salida, None


def sumar_dias_habiles(desde: date, dias: int) -> date:
    """`desde` + `dias` días hábiles, salteando sábados y domingos.

    Los feriados no se saltean: no hay un calendario de feriados en el CRM, y
    un depósito que llega un día después de lo esperado se nota solo en la
    lista de "por llegar".
    """
    d = desde
    while dias > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            dias -= 1
    return d


def desglosar(modo: str, monto: float, tarjeta: str, ajustes: dict,
              moneda: str = "USD", tipo_cambio: float | None = None,
              incluir_fijo: bool = True, clientes: int | None = None) -> dict:
    """El desglose completo de UN cobro con tarjeta.

    - `modo` "quiero_llevarme": `monto` es lo que tiene que quedarte limpio
      (después de comisión, Plexo e IVA). Se despeja el precio.
    - `modo` "precio": `monto` es el precio sin IVA, como se acuerda.
    - `modo` "total": `monto` es lo que se le pasa a la tarjeta, IVA incluido.

    La comisión de la tarjeta se cobra sobre el TOTAL (con IVA), y la tarjeta
    le suma IVA a su comisión. Ese IVA, igual que el de Plexo, es IVA compras:
    se descuenta del IVA que se le paga a DGI. Por eso el costo real de la
    comisión es la comisión sin su IVA.

    Despejando: te queda = P - c·1,22·P - plexo - parte del fijo, así que
    P = (lo que querés + plexo + parte del fijo) / (1 - c·1,22).

    Levanta ValueError con un mensaje para la pantalla si falta un dato.
    """
    if modo not in MODOS:
        raise ValueError(f"modo tiene que ser uno de {tuple(MODOS)}")
    if tarjeta not in TARJETAS:
        raise ValueError("tarjeta desconocida")
    if moneda not in MONEDAS:
        raise ValueError(f"moneda tiene que ser una de {MONEDAS}")
    try:
        monto = float(monto)
    except (TypeError, ValueError):
        raise ValueError("el monto tiene que ser un número")
    if monto <= 0:
        raise ValueError("el monto tiene que ser mayor que cero")

    pct = ajustes["comisiones"].get(tarjeta)
    if pct is None:
        raise ValueError(f"falta cargar la comisión de {TARJETAS[tarjeta]} en Ajustes")
    c = float(pct) / 100

    tc = float(tipo_cambio or ajustes["tipo_cambio"] or 0)
    if tc <= 0:
        raise ValueError("el tipo de cambio tiene que ser mayor que cero")
    # Plexo cobra en pesos: en un cobro en dólares hay que pasarlo a dólares.
    en_moneda = (lambda uyu: uyu) if moneda == "UYU" else (lambda uyu: uyu / tc)

    plexo = en_moneda(float(ajustes["plexo_por_cobro_uyu"]))
    clientes = max(int(clientes or ajustes["clientes_tarjeta"] or 1), 1)
    fijo = en_moneda(float(ajustes["plexo_fijo_uyu"])) / clientes if incluir_fijo else 0.0

    if modo == "quiero_llevarme":
        precio = (monto + plexo + fijo) / (1 - c * (1 + IVA_TASA))
    elif modo == "precio":
        precio = monto
    else:
        precio = monto / (1 + IVA_TASA)
    precio = _r2(precio)

    iva_venta = _r2(precio * IVA_TASA)
    total = _r2(precio + iva_venta)
    comision = _r2(total * c)
    comision_iva = _r2(comision * IVA_TASA)
    deposito = _r2(total - comision - comision_iva)
    plexo = _r2(plexo)
    plexo_iva = _r2(plexo * IVA_TASA)
    fijo = _r2(fijo)
    fijo_iva = _r2(fijo * IVA_TASA)
    iva_dgi = _r2(iva_venta - comision_iva - plexo_iva - fijo_iva)
    te_queda = _r2(precio - comision - plexo - fijo)

    dias = int(ajustes["dias_debito"] if es_debito(tarjeta) else ajustes["dias_credito"])

    return {
        "modo": modo, "monto": _r2(monto), "tarjeta": tarjeta,
        "tarjeta_nombre": TARJETAS[tarjeta], "comision_pct": float(pct),
        "moneda": moneda, "tipo_cambio": tc,
        "incluir_fijo": bool(incluir_fijo), "clientes": clientes,
        "dias_habiles": dias,
        "precio": precio,
        "iva_venta": iva_venta,
        "total": total,
        "comision": comision,
        "comision_iva": comision_iva,
        "deposito": deposito,
        "plexo": plexo,
        "plexo_iva": plexo_iva,
        "fijo": fijo,
        "fijo_iva": fijo_iva,
        "iva_compras": _r2(comision_iva + plexo_iva + fijo_iva),
        "iva_dgi": iva_dgi,
        "te_queda": te_queda,
        # Lo que se come la tarjeta + Plexo, sobre el precio. Para comparar
        # tarjetas de un vistazo.
        "costo_pct": _r2((precio - te_queda) / precio * 100) if precio else 0.0,
    }


def quien_paga(concepto: str, cliente: str | None) -> str:
    """El texto que identifica el cobro en la lista de movimientos.

    Pedido de Juan (24/9): "dice Plexo por cobro · Mantenimiento mensual y no
    el nombre del cliente, no se entiende". Con cliente va primero su nombre;
    si el concepto ya lo nombra, no se repite.
    """
    cliente = (cliente or "").strip()
    if not cliente or cliente.lower() in concepto.lower():
        return concepto
    return f"{cliente} · {concepto}"


def armar_cobro(d: dict, *, fecha: str, concepto: str, categoria: str,
                client_id, cliente: str | None, plexo_por_cobro_uyu: float,
                created_by_id=None, created_by_name=None,
                recurrente_id=None) -> tuple[dict, dict, dict, dict | None]:
    """Los movimientos de UN cobro con tarjeta, listos para
    `database.crear_cobro_tarjeta`: (cobro, ingreso, comision, plexo).

    Lo usan el botón "Registrar cobro" y los ingresos fijos que se cobran con
    tarjeta, para que los dos carguen exactamente lo mismo. `d` es el
    resultado de `desglosar` SIN el fijo de Plexo (ese va como gasto fijo).

    `recurrente_id` va solo en el ingreso: el índice único
    (recurrente_id, periodo) es el que impide que el fijo cobre dos veces el
    mismo mes, y la comisión y Plexo del mismo cobro lo violarían.
    """
    periodo = periodo_de(fecha)
    moneda, tc = d["moneda"], d["tipo_cambio"]
    tc_mov = tc if moneda == "UYU" else None
    comunes = {"fecha": fecha, "periodo": periodo, "facturado": 1,
               "created_by_id": created_by_id, "created_by_name": created_by_name,
               "client_id": client_id}

    def _mov(tipo, cat, texto, monto, mon, tipo_cambio, **extra):
        usd = a_usd(monto, mon, tipo_cambio)
        return {**comunes, "tipo": tipo, "categoria": cat, "concepto": texto,
                "monto": monto, "moneda": mon, "tipo_cambio": tipo_cambio,
                "monto_usd": usd, "iva_usd": iva_sobre(usd), **extra}

    texto = quien_paga(concepto, cliente)
    pct = f"{d['comision_pct']:g}".replace(".", ",")
    ingreso = _mov("ingreso", categoria, texto, d["precio"], moneda, tc_mov,
                   notas=f"Cobro con tarjeta: {d['tarjeta_nombre']}")
    if recurrente_id:
        ingreso["recurrente_id"] = recurrente_id
    comision = _mov("egreso", "comisiones",
                    f"Comisión {d['tarjeta_nombre']} {pct}% · {texto}",
                    d["comision"], moneda, tc_mov)
    # Plexo cobra en pesos: su movimiento va siempre en pesos, con el tipo de
    # cambio del cobro, aunque el cobro sea en dólares.
    plexo = (_mov("egreso", "comisiones", f"Plexo por cobro · {texto}",
                  float(plexo_por_cobro_uyu), "UYU", tc)
             if float(plexo_por_cobro_uyu) > 0 else None)

    from datetime import date as _date
    esperada = sumar_dias_habiles(_date.fromisoformat(fecha), d["dias_habiles"])
    cobro = {
        "fecha": fecha, "client_id": client_id, "concepto": texto,
        "tarjeta": d["tarjeta"], "comision_pct": d["comision_pct"],
        "moneda": moneda, "tipo_cambio": tc, "precio": d["precio"],
        "total": d["total"], "deposito": d["deposito"],
        "acreditacion_esperada": esperada.isoformat(),
        "created_by_name": created_by_name,
    }
    return cobro, ingreso, comision, plexo

