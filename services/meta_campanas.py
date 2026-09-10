"""La campana del lead, sacada de `notes` a su columna.

Hasta el 10/9/2026 la unica huella de que campana trajo a un lead era el texto
"Meta Lead Ad · <campana>" que `routes/meta.py` escribe en `notes`. Sirve para
que un humano lo lea, no para agrupar: un LIKE sobre notes no escala, y no hay
forma de juntar el lead con el gasto de su campana.

El backfill recupera lo que se pueda de los leads viejos. Los ids de campana no
estan en el texto y no hay de donde sacarlos: quedan en NULL y esos leads se
emparejan con los Insights por nombre, que es fragil si la campana se renombro
en Meta. Los leads nuevos si traen id.
"""

import logging
import re

from database import _connect

logger = logging.getLogger(__name__)

# El separador es un punto medio (·), no un guion. Lo escribe routes/meta.py.
_PATRON = re.compile(r"^Meta Lead Ad\s*·\s*(.+)$")


def parsear_campana(notes):
    """El nombre de la campana que hay en `notes`, o None.

    Solo la primera linea: el vendedor escribe sus notas debajo y no son parte
    del nombre.
    """
    if not notes:
        return None
    m = _PATRON.match(notes.split("\n")[0].strip())
    if not m:
        return None
    nombre = m.group(1).strip()
    return nombre or None


def backfill_campanas(db_path: str) -> dict:
    """Escribe `meta_campaign_name` en los leads de Meta que no lo tengan.

    Idempotente: solo toca filas con la columna vacia, asi que correrlo dos
    veces no escribe nada la segunda y nunca pisa lo que escribio la ingesta.
    """
    conn = _connect(db_path)
    try:
        filas = conn.execute(
            "SELECT id, notes FROM businesses "
            "WHERE source = 'meta' "
            "  AND (meta_campaign_name IS NULL OR meta_campaign_name = '')"
        ).fetchall()

        escritos, sin_campana = 0, 0
        for fila in filas:
            nombre = parsear_campana(fila["notes"])
            if not nombre:
                sin_campana += 1
                continue
            conn.execute("UPDATE businesses SET meta_campaign_name = ? WHERE id = ?",
                         (nombre, fila["id"]))
            escritos += 1
        conn.commit()
    finally:
        conn.close()

    logger.info(f"backfill de campanas: {escritos} escritos, "
                f"{sin_campana} sin campana, sobre {len(filas)} revisados")
    return {"revisados": len(filas), "escritos": escritos,
            "sin_campana": sin_campana}
