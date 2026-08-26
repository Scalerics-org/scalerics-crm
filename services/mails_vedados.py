"""Direcciones a las que no se les escribe mas: rebotes duros y quejas de spam.

Un rebote duro es una direccion que no existe. Seguir mandandole es la forma
mas rapida que hay de quemar un dominio de envio —mas rapida que las quejas—,
y en un padron raspado de sitios web los rebotes son inevitables: la direccion
publicada puede tener anios.

La lista es UNA para las dos campanas a proposito. Las dos salen de la misma
cuenta de Resend, y una direccion que rebota escribiendole a un lead de Meta va
a rebotar igual en discovery. Tenerla por campana seria cometer el error dos
veces.
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)

# Una queja de spam le gana a un rebote: es la senal mas cara que existe y la
# que hay que poder contar despues para saber si la campana esta sana.
_PRIORIDAD = {"queja_spam": 2, "rebote_duro": 1}


def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _normalizar(email) -> str:
    """Las direcciones llegan de Resend, de sitios raspados y de formularios de
    Meta, y cada fuente las escribe distinto."""
    return (email or "").strip().lower()


def vedar(db_path: str, email, motivo: str, detalle: str = "") -> bool:
    """Agrega la direccion a la lista. Devuelve si quedo vedada.

    Es idempotente porque Resend reintenta los webhooks: el mismo rebote puede
    llegar varias veces y no puede fallar la segunda.
    """
    direccion = _normalizar(email)
    if not direccion:
        # Vedar "" haria que el LOWER(TRIM(email)) de cualquier lead sin mail
        # matchee contra la lista y se lleve puesta media cohorte.
        logger.warning(f"No se veda una direccion vacia (motivo {motivo!r})")
        return False

    conn = _conn(db_path)
    try:
        previo = conn.execute(
            "SELECT motivo FROM mails_vedados WHERE email = ?", (direccion,)
        ).fetchone()
        if previo and _PRIORIDAD.get(previo["motivo"], 0) >= _PRIORIDAD.get(motivo, 0):
            return True
        conn.execute(
            """
            INSERT INTO mails_vedados (email, motivo, detalle, creado_at)
                 VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(email) DO UPDATE SET motivo = excluded.motivo,
                                             detalle = excluded.detalle
            """,
            (direccion, motivo, (detalle or "")[:500]),
        )
        conn.commit()
    finally:
        conn.close()
    logger.warning(f"Direccion vedada ({motivo}): {direccion}")
    return True


def esta_vedado(db_path: str, email) -> bool:
    direccion = _normalizar(email)
    if not direccion:
        return False
    conn = _conn(db_path)
    try:
        return conn.execute(
            "SELECT 1 FROM mails_vedados WHERE email = ?", (direccion,)
        ).fetchone() is not None
    finally:
        conn.close()


def listar_vedados(db_path: str) -> list:
    conn = _conn(db_path)
    try:
        return [dict(f) for f in conn.execute(
            "SELECT email, motivo, detalle, creado_at FROM mails_vedados "
            "ORDER BY creado_at DESC, email"
        ).fetchall()]
    finally:
        conn.close()
