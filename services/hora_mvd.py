"""La hora y el día de Montevideo.

El servidor corre en UTC. De las 21 a las 24 de Montevideo en UTC ya es el día
siguiente, así que "hoy" nunca es `date.today()`.

Vivían en services/daily.py y services/seg_leads.py; esas secciones se sacaron
del CRM el 29/10 (pedido de Juan) y lo que otras usaban quedó acá.
"""

from datetime import date, datetime, timedelta, timezone

import pytz

# Uruguay está en UTC-3 fijo (sin horario de verano desde 2015).
MONTEVIDEO = timezone(timedelta(hours=-3))
MVD = pytz.timezone("America/Montevideo")


def hoy_montevideo(ahora: datetime | None = None) -> date:
    """El día en Montevideo. Un datetime sin zona se toma como UTC, que es la
    hora del servidor."""
    ahora = ahora or datetime.now(timezone.utc)
    if ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=timezone.utc)
    return ahora.astimezone(MONTEVIDEO).date()


def ahora_mvd(ahora_utc: datetime | None = None) -> datetime:
    """La hora de Montevideo. `ahora_utc` es para los tests."""
    base = ahora_utc or datetime.now(pytz.utc)
    if base.tzinfo is None:
        base = pytz.utc.localize(base)
    return base.astimezone(MVD)
