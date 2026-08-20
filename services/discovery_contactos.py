"""La tabla de la secuencia de discovery.

Vive en un modulo aparte y sin dependencias, igual que
`services/secuencia_contactos.py` para la campana de Meta, porque la necesitan
los dos lados: el que decide a quien le toca y el que escribe el texto.

Son DOS contactos, no siete como en Meta, y la diferencia es deliberada: los
leads de Meta llenaron un formulario pidiendo que los contacten; estos comercios
no pidieron nada. Siete toques en frio es el perfil que se marca como spam, y
cada marca se paga con entrega en el mismo dominio que usa el correo con
clientes.
"""

# Dias desde el PRIMER envio de cada comercio.
DIAS_DE_CADA_CONTACTO = [0, 7]
TOTAL_CONTACTOS = len(DIAS_DE_CADA_CONTACTO)
