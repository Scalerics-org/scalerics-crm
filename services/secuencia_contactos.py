"""Las tablas de las secuencias de mails a leads de Meta, una por estado del CRM.

Vive en un modulo aparte, sin importar nada, porque la necesitan los dos lados
de la secuencia: `meta_reminders`, que decide a quien le toca hoy, y
`email_service`, que escribe el texto de cada contacto. Si viviera en
`meta_reminders`, `email_service` tendria que importarlo de ahi y se cerraria un
ciclo, porque `meta_reminders` ya importa `email_service`.

Un solo lugar de verdad: agregar un dia a una lista corre el tope de esa
secuencia Y mueve su ultimo contacto, sin que haya que acordarse de tocar los
dos archivos.

**El estado manda, no el lead.** Cada estado del CRM lleva su propio contador de
contactos. Un lead que pasa de `llamar_despues` a `interesado` arranca la
secuencia de `interesado` en el contacto 1, no sigue numerando desde donde iba:
si no, recibiria el contacto 2 de una secuencia que nunca empezo. El estado se
lee en el momento de mandar, asi que alguien que compro o se cayo el dia
anterior simplemente no aparece en la tanda de hoy.
"""

# Dias desde el PRIMER envio de cada lead DENTRO DE ESE ESTADO. El ancla es el
# primer contacto y no el anterior a proposito: asi el atraso de una tanda no se
# acumula sobre los que siguen.
#
# Ojo: estos umbrales no son toda la regla. Ademas tienen que haber pasado
# `_PISO_ENTRE_CONTACTOS_DIAS` (en meta_reminders) desde el ULTIMO envio a ese
# lead —contado sobre todos sus estados, no solo el actual—, o un lead atrasado
# pasaria todos estos umbrales de golpe, y uno que cambia de estado recibiria
# dos mails el mismo dia.

# El lead que nadie toco. Es la secuencia larga de siempre: son 7 y el septimo
# es el ultimo de la vida de ese lead en este estado.
DIAS_DE_CADA_CONTACTO = [0, 10, 25, 115, 205, 295, 365]
TOTAL_CONTACTOS = len(DIAS_DE_CADA_CONTACTO)

# Las secuencias cortas son cortas a proposito: no son para despertar a un
# desconocido, son para retomar una conversacion que ya existio. Con dos mails
# la persona ya entendio; el tercero solo compra una baja.
SECUENCIAS_POR_ESTADO = {
    # Nadie lo contacto todavia. Secuencia larga de nutricion.
    "sin_contactar": DIAS_DE_CADA_CONTACTO,
    # Vio la demo, recibio precio y desaparecio. El mas caliente de todos: ya
    # sabe quien sos, cuanto sale y que le vas a entregar.
    "presupuesto_enviado": [0, 7],
    # Hubo demo y no siguio. Falta el numero.
    "reunion_hecha": [0, 7],
    # Atendio el telefono pero no se llego a agendar nada.
    "interesado": [0],
    # No atiende el telefono y se le dejo mensaje. El mail es el canal alterno.
    "llamar_despues": [0],
}

# Los estados que NO estan en el dict no reciben nada, y es deliberado:
# `no_interesa` (ya dijo que no), `reunion_agendada` (tiene reunion coordinada y
# un mail automatico solo puede confundirlo), y `cliente_cerrado`,
# `en_desarrollo` y `finalizado`, que ya son clientes.
ESTADOS_CON_SECUENCIA = tuple(SECUENCIAS_POR_ESTADO)


def dias_de(estado: str) -> list:
    """Los umbrales en dias de la secuencia de `estado`, o [] si no tiene."""
    return SECUENCIAS_POR_ESTADO.get(estado, [])


def total_de(estado: str) -> int:
    """Cuantos contactos tiene la secuencia de `estado`. 0 si no recibe nada."""
    return len(dias_de(estado))


def es_ultimo(estado: str, numero: int) -> bool:
    """Si `numero` es el ultimo contacto de la secuencia de `estado`.

    Lo usa el texto del mail para poner el "no te escribimos mas". Un estado sin
    secuencia devuelve False: no hay contacto que pueda ser el ultimo.
    """
    total = total_de(estado)
    return total > 0 and int(numero) >= total
