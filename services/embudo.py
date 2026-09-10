"""Las etapas del embudo, definidas una sola vez.

Esto vivia dentro de `services/finanzas.py`. Se saco a un modulo propio el
10/9/2026, cuando el modulo de marketing necesito contar las mismas etapas: dos
definiciones del embudo son dos numeros distintos para la misma pregunta en dos
paneles del mismo CRM, y eso es peor que no tener el segundo panel.

Se movio sin tocar el comportamiento. Los tests de finanzas pasan identicos.
"""

# El embudo en orden, con el vocabulario nuevo de ETAPAS_PRECLIENTE /
# ETAPAS_CLIENTE (database.py). `no_interesa`, `en_espera` y `rechazo` NO
# estan: son salidas o pausas, no etapas.
#
# `no_interesa` es "nunca engancho". `en_espera` es "frenado por el cliente,
# sin cerrar" y `rechazo` es "dijo que no despues de haber avanzado" — ninguno
# de los dos dice hasta donde llego el lead, eso ya lo dicen sus eventos
# anteriores. Meterlos en la lista ordenada haria que un lead rechazado
# figurara mas avanzado que uno en `presupuesto_enviado`, que es al reves de
# lo que paso: un lead que se cayo ahi igual paso por lo que haya pasado antes.
FUNNEL = ["sin_contactar", "interesado", "contactado",
          "demo_agendada", "demo_1", "demo_2", "demo_3",
          "presupuesto_enviado", "follow_up_1", "follow_up_2", "acepto",
          "cerrado", "en_desarrollo", "finalizado"]

# Exclusiones explicitas y documentadas: por que cada una no entra a FUNNEL
# aunque forme parte de ETAPAS_PRECLIENTE / ETAPAS_CLIENTE.
EXCLUIDOS_DEL_FUNNEL = {
    "en_espera": "pausa del cliente, no un avance",
    "rechazo": "salida tras haber avanzado, no una etapa",
}


def normalizar_estado(estado: str) -> str:
    """Traduce un `crm_status` viejo al vocabulario nuevo, si corresponde.

    `lead_events` mezcla las dos epocas: la migracion de `database.py`
    reescribe `businesses.crm_status` pero no toca el historial de eventos, asi
    que todo lo de antes de la migracion quedo con los nombres viejos
    (`reunion_agendada`, `reunion_hecha`, `negociacion`, `cliente_cerrado`, y
    los alias `agendo`/`firmo`) y todo lo de despues ya nace con los nuevos.
    Sin esto, `alcanzo` dejaria de contar cualquier lead viejo.
    """
    from database import _MAPA_ESTADOS_VIEJOS

    return _MAPA_ESTADOS_VIEJOS.get(estado, estado)


def alcanzo(eventos: set, etapa: str) -> bool:
    """Si el lead paso por `etapa` o por cualquiera posterior, alguna vez.

    Se mira contra el historial de `lead_events`, no contra el `crm_status` de
    hoy: un lead que llego a demo y despues se cayo a `no_interesa` figura hoy
    como `no_interesa`, y contarlo por el estado actual lo perderia.

    Los eventos se normalizan antes de comparar (ver `normalizar_estado`),
    porque el historial trae nombres viejos y nuevos mezclados.
    """
    objetivo = FUNNEL.index(etapa)
    normalizados = {normalizar_estado(e) for e in eventos}
    return any(e in FUNNEL and FUNNEL.index(e) >= objetivo for e in normalizados)


def dividir(numerador: float, denominador: float):
    """Division simple, o None si el denominador es cero."""
    if not denominador:
        return None
    return round(numerador / denominador, 2)


def costo(inversion: float, cantidad: float):
    """Costo unitario, o None si no hay de que dividir.

    Sin `cantidad` la division no esta definida: un mes sin ventas no tiene un
    costo por venta de cero, no tiene costo por venta — la planilla mostraba
    #DIV/0! y esa era la lectura correcta. Sin `inversion` tampoco: no hubo
    campana que costear, asi que no es un costo de cero.
    """
    if not inversion:
        return None
    return dividir(inversion, cantidad)
