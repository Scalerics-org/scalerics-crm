"""La tabla de la secuencia de recordatorios a leads de Meta.

Vive en un modulo aparte, sin importar nada, porque la necesitan los dos lados
de la secuencia: `meta_reminders`, que decide a quien le toca hoy, y
`email_service`, que escribe el texto de cada contacto. Si viviera en
`meta_reminders`, `email_service` tendria que importarlo de ahi y se cerraria un
ciclo, porque `meta_reminders` ya importa `email_service`.

Un solo lugar de verdad: agregar un dia a la lista corre el tope de la secuencia
Y mueve el "no te escribimos mas" al contacto nuevo, sin que haya que acordarse
de tocar los dos archivos.
"""

# Dias desde el PRIMER envio de cada lead. El ancla es el primer contacto y no
# el anterior a proposito: asi el atraso de una tanda no se acumula sobre los
# que siguen. Son 7 y el septimo es el ultimo de la vida de ese lead.
#
# Ojo: estos umbrales no son toda la regla. Ademas tienen que haber pasado
# `_PISO_ENTRE_CONTACTOS_DIAS` (en meta_reminders) desde el ULTIMO envio, o un
# lead atrasado pasaria todos estos umbrales de golpe y recibiria los contactos
# 2 a 7 uno atras de otro.
DIAS_DE_CADA_CONTACTO = [0, 10, 25, 115, 205, 295, 365]
TOTAL_CONTACTOS = len(DIAS_DE_CADA_CONTACTO)
