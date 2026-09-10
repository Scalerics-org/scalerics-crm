"""El dossier: metricas calculadas, cada una auditable.

Esto es lo unico que el motor de IA va a ver. Nunca ve filas, ni nombres, ni
telefonos. Dos consecuencias: no puede inventar un numero que no este aca, y no
salen datos personales de 238 personas hacia un servicio externo.

Cada metrica trae numerador, denominador, n e intervalo. Sin eso, un 5% sobre 20
leads y un 5% sobre 2000 se leen igual en un grafico, y no son lo mismo.
"""

import math

from database import _connect

# Debajo de esto, una diferencia entre dos grupos es ruido. Con 51 leads en la
# campana de ARG, cinco puntos contra la de UY no significan nada.
MUESTRA_CHICA = 30

# `inferencia` no esta a proposito: las inferencias son del modelo y viven en el
# informe, separadas de los hechos.
FUENTES = ("meta_insights", "crm", "derivada")


def wilson(exitos: int, total: int, z: float = 1.96):
    """Intervalo de confianza de Wilson para una proporcion.

    Wilson y no el normal: con muestras chicas o proporciones cerca de 0 o de 1
    —justo el caso aca— el intervalo normal se va abajo de cero o arriba de uno
    y deja de querer decir algo. Ademas no necesita ninguna dependencia nueva.
    """
    if total <= 0:
        return (0.0, 1.0)      # sin datos no se sabe nada
    p = exitos / total
    d = 1 + z * z / total
    centro = (p + z * z / (2 * total)) / d
    margen = z / d * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return (round(max(0.0, centro - margen), 4),
            round(min(1.0, centro + margen), 4))


def metrica(id: str, etiqueta: str, valor, fuente: str, formato: str = "numero",
            numerador=None, denominador=None, anterior=None) -> dict:
    """Una metrica del dossier, con todo lo que hace falta para auditarla."""
    if fuente not in FUENTES:
        raise ValueError(
            f"fuente invalida: {fuente!r}. Tiene que ser una de {FUENTES}. "
            "Las inferencias no son metricas: van en el informe.")
    delta = None
    if anterior is not None and valor is not None:
        delta = round(valor - anterior, 4)
    return {
        "id": id,
        "etiqueta": etiqueta,
        "valor": valor,
        "formato": formato,
        "numerador": numerador,
        "denominador": denominador,
        "n": denominador,
        "ic95": None,
        "delta_periodo_anterior": delta,
        "fuente": fuente,
        "muestra_chica": False,
    }


def proporcion(id: str, etiqueta: str, exitos: int, total: int, fuente: str,
               anterior=None) -> dict:
    """Una metrica que es una tasa, con su intervalo y su marca de muestra."""
    valor = round(exitos / total, 4) if total else None
    m = metrica(id, etiqueta, valor, fuente, formato="porcentaje",
                numerador=exitos, denominador=total, anterior=anterior)
    m["ic95"] = list(wilson(exitos, total))
    m["muestra_chica"] = total < MUESTRA_CHICA
    return m
