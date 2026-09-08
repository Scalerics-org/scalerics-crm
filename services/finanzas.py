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
            except sqlite3.IntegrityError:
                # Ya existía ese (recurrente_id, periodo). Es el camino normal:
                # todas las corridas después de la primera pasan por acá.
                pass

    return creados
