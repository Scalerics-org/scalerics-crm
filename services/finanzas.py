"""Las cuentas de la sección financiera.

Todo lo que puede dar un número mal vive acá y se testea sin Flask ni base:
conversión de moneda, aritmética de períodos, materialización de los fijos y
los agregados del panel. Las rutas solo validan y serializan.
"""

import logging

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
