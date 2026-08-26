"""Marca de ultima corrida de los jobs diarios.

Los hilos de las campanas arrancan N segundos despues de CADA boot, y Fly
reinicia la maquina en cada deploy. El 26/8/2026 hubo cinco releases en 42
minutos y la tanda de discovery salio dos veces el mismo dia (6 envios y 24).

El tope rodante de 24 horas hizo bien su trabajo —la segunda tanda descontó lo
ya enviado— pero era lo UNICO que separaba un deploy de una tanda repetida.
Para algo que le manda correo a gente real, un solo guard es poco. Esto es el
segundo, independiente del primero: si uno falla, el otro tapa.

Con esto el envio queda atado a un reloj y no a los deploys.
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)

# 20 y no 24 a proposito: con 24, si la tanda de hoy salio 18:11 y manana la
# maquina arranca a las 17:00, el job no corre y se pierde el dia entero. Con
# 20 el margen alcanza para una corrida por dia real sin acumular atraso.
_CADA_HORAS = 20


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
