"""Marca de ultima corrida de los jobs diarios.

Los hilos de las campanas arrancan N segundos despues de CADA boot, y Fly
reinicia la maquina en cada deploy. El 26/8/2026 hubo cinco releases en 42
minutos y la tanda de discovery salio dos veces el mismo dia (6 envios y 24).

El tope rodante de 24 horas hizo bien su trabajo —la segunda tanda descontó lo
ya enviado— pero era lo UNICO que separaba un deploy de una tanda repetida.
Para algo que le manda correo a gente real, un solo guard es poco. Esto es el
segundo, independiente del primero: si uno falla, el otro tapa.

Con esto el envio queda atado a un reloj y no a los deploys.

Desde el 15/9/2026 los hilos no duermen 24 horas entre intentos: revisan cada
hora (ver `REVISAR_CADA_S`) y son esta marca y el tope rodante los que deciden
si la revision manda. Antes, un deploy que caia entre la hora 20 y la 24 desde
la ultima tanda pasaba la marca, se encontraba el cupo lleno, no mandaba nada y
el hilo se dormia un dia: discovery perdio 7 dias entre el 28/8 y el 15/9, y
Meta casi los mismos.
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)

# 20 y no 24 a proposito. Es el piso entre dos tandas marcadas, no el ritmo: el
# ritmo lo pone el tope rodante de 24 horas, que es el que deja la revision
# vacia hasta que la tanda de ayer sale de la ventana. Con la revision horaria
# la marca queda como segundo guard contra una tanda repetida (un deploy justo
# despues de mandar). No puede ser mayor que la ventana del cupo: si lo fuera,
# le sumaria su propio atraso a la tanda de manana.
_CADA_HORAS = 20

# Cada cuanto se fija cada hilo de campana si le toca mandar. Una hora, y no un
# dia, porque el cupo se libera 24 horas despues de la tanda anterior y no en
# el momento del deploy: con 24 horas de siesta, una revision que llegaba antes
# de que se liberara perdia el dia entero.
#
# Los 10 segundos de mas son a proposito. La tanda sale unos segundos DESPUES
# de la revision (Gmail y las consultas van primero), asi que con 3600 exactos
# la revision 24 horas mas tarde cae justo antes de que esos mails salgan de la
# ventana: el cupo da 0 y la tanda se corre una hora, todos los dias. Una hora
# por dia es un dia perdido cada 25. Con 3610, veinticuatro revisiones son
# 24 h 4 min y la tanda de ayer —que dura alrededor de un minuto— ya salio.
#
# Es UNA constante para las dos campanas a proposito: con la misma grilla, el
# desfase de sus arranques (180 s Meta, 600 s discovery) se mantiene para
# siempre, y no mandan a la vez contra el limite de 2 por segundo de Resend.
REVISAR_CADA_S = 60 * 60 + 10


def siguiente_revision(anterior: float, ahora: float, cada: float = REVISAR_CADA_S) -> float:
    """El proximo turno de la grilla que empieza en la primera revision.

    Se cuenta desde el turno anterior y no desde que termino la vuelta: si lo
    que tarda cada tanda corriera la grilla, el desfase entre Meta y discovery
    se iria comiendo solo. Si una vuelta tardo mas que un turno, los turnos
    perdidos no se recuperan de golpe: se salta al siguiente que todavia no
    paso.
    """
    siguiente = anterior + cada
    while siguiente <= ahora:
        siguiente += cada
    return siguiente


def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ultima_corrida(db_path: str, nombre: str):
    conn = _conn(db_path)
    try:
        fila = conn.execute(
            "SELECT ultima FROM corridas WHERE nombre = ?", (nombre,)
        ).fetchone()
        return fila["ultima"] if fila else None
    finally:
        conn.close()


def puede_correr(db_path: str, nombre: str, cada_horas: float = _CADA_HORAS) -> bool:
    """Paso suficiente tiempo desde la ultima corrida de este job?

    Ante un error de la tabla devuelve True: el tope rodante de 24 horas sigue
    estando detras y es el que evita el dano de verdad. Fallar cerrado aca
    dejaria la campana muda por un problema de la tabla de marcas, que es peor
    que correr de mas una vez.
    """
    try:
        conn = _conn(db_path)
    except Exception as e:
        logger.warning(f"corridas: no se pudo abrir la base ({e}), se deja correr")
        return True
    try:
        fila = conn.execute(
            "SELECT ultima FROM corridas WHERE nombre = ? "
            "  AND ultima > datetime('now', ?)",
            (nombre, f"-{float(cada_horas)} hours"),
        ).fetchone()
        return fila is None
    except Exception as e:
        logger.warning(f"corridas: no se pudo leer la marca de {nombre!r} ({e}), se deja correr")
        return True
    finally:
        conn.close()


def marcar_corrida(db_path: str, nombre: str) -> None:
    try:
        conn = _conn(db_path)
    except Exception as e:
        logger.warning(f"corridas: no se pudo marcar {nombre!r}: {e}")
        return
    try:
        conn.execute(
            "INSERT INTO corridas (nombre, ultima) VALUES (?, datetime('now')) "
            "ON CONFLICT(nombre) DO UPDATE SET ultima = excluded.ultima",
            (nombre,),
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"corridas: no se pudo marcar {nombre!r}: {e}")
    finally:
        conn.close()
