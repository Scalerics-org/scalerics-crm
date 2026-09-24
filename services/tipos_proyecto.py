"""De que es el proyecto: el vocabulario, en un solo lugar (Juan, 16/9).

"Que aparte me deje poner la opcion si es automatizacion, pagina web,
e commerce o desarrollo a medida u otro".

Por que un modulo y no una lista suelta en el calendario: los tipos ya existian
escritos dos veces, con claves distintas, y ninguna de las dos se guardaba.

  simulador   `SIM_TIPOS` en dashboard.py usa 'web', 'ecommerce' y 'aMedida'
              para repartir ventas y cobros. Es lo unico que hoy reporta por
              tipo, asi que las claves de aca son ESAS, tal cual, para que un
              reporte pueda cruzar las dos cosas sin una tabla de traduccion.
  resumen IA  el prompt de `/summarize` pedia 'web|ecommerce|app|automatizacion
              |otro'. Tenia 'app', que no es ninguno de los de Juan, y el
              resultado se tiraba. Ahora pide estas mismas claves.

'automatizacion' es el unico agregado: ya estaba en el prompt de la IA, no en
el simulador. Si alguien suma un tipo, va aca y aparece en los dos lados.

La etiqueta es lo que ve Juan; la clave es lo que se guarda en la base
(`meetings.tipo_proyecto`). Con 'otro' se guarda ademas el texto que escriba en
`tipo_otro`, igual que "Otro asunto" deja escribir el titulo.
"""

from __future__ import annotations

# clave -> etiqueta. El orden es el que se dibuja en el selector.
TIPOS_PROYECTO: dict[str, str] = {
    "automatizacion": "Automatización",
    "web": "Página web",
    "ecommerce": "E-commerce",
    "aMedida": "Desarrollo a medida",
    "otro": "Otro",
}

CLAVES = tuple(TIPOS_PROYECTO)

# El unico que deja escribir texto libre.
LIBRE = "otro"

# Cuanto se le acepta al texto de "Otro": entra en el titulo del evento de
# Google, asi que es corto a proposito.
MAX_OTRO = 60


def validar(clave, texto=None) -> tuple[str | None, str | None, str | None]:
    """(clave, texto, error). Sin tipo elegido devuelve (None, None, None).

    'otro' sin texto no es un error: queda como "Otro" a secas. El texto se
    recorta a MAX_OTRO y solo se guarda para 'otro'.
    """
    clave = str(clave or "").strip()
    if not clave:
        return None, None, None
    if clave not in TIPOS_PROYECTO:
        return None, None, "Elegí de qué es la reunión"
    if clave != LIBRE:
        return clave, None, None
    texto = str(texto or "").strip()[:MAX_OTRO]
    return clave, texto or None, None


def etiqueta(clave, texto=None) -> str:
    """Como se dice en pantalla y en la invitacion. '' si no hay tipo."""
    clave = str(clave or "").strip()
    if clave not in TIPOS_PROYECTO:
        return ""
    if clave == LIBRE:
        texto = str(texto or "").strip()
        return texto or TIPOS_PROYECTO[LIBRE]
    return TIPOS_PROYECTO[clave]
