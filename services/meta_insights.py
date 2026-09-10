"""El gasto de Meta Ads, sincronizado a una tabla propia.

El CRM sabia que campana trajo cada lead pero no cuanto costo. Sin eso no hay
costo por lead ni costo por demo, que es la pregunta central de donde invertir.

**Credencial aparte.** El META_PAGE_TOKEN no sirve: los Insights de ads piden
`ads_read` sobre la cuenta publicitaria. Van META_ADS_TOKEN y META_AD_ACCOUNT_ID
en los secrets de Fly. Si falta alguna, el sync se saltea con un warning y el
resto del modulo funciona igual, sin las metricas de costo.

**No se convierte la moneda.** Meta devuelve el gasto en la moneda de la cuenta.
Convertir gasto de marzo con la cotizacion de hoy produce un numero que parece
preciso y no lo es. Se guarda `currency` y se muestra tal cual.

**Version de la Graph API:** la constante compartida de `meta_config`, nunca una
propia. En 2026 el webhook de leadgen quedo fijado en una version vieja, Meta
dejo de entregar en silencio y se busco el problema en el token durante horas.
`meta_config` existe justo para que la version viva en un solo lugar.
"""

import logging
import os

from database import _connect

logger = logging.getLogger(__name__)

# Meta ajusta las cifras de los ultimos dias hacia atras, asi que la ventana
# reciente se vuelve a pedir siempre en vez de darla por cerrada.
DIAS_A_RESINCRONIZAR = 7

# Los action_type con los que Meta reporta un lead de formulario. Son varios
# porque el nombre cambio entre versiones y conviven en cuentas viejas. Cual usa
# la cuenta de Scalerics NO esta verificado todavia: hace falta una corrida real
# con credenciales. Si el que aparece no esta en esta tupla, la columna `leads`
# queda en cero y todos los CPL dan None — no seria un bug del codigo, seria un
# nombre que falta aca.
_ACCIONES_DE_LEAD = ("lead", "leadgen_grouped", "onsite_conversion.lead_grouped")


def hay_credenciales() -> bool:
    return bool(os.environ.get("META_ADS_TOKEN")
                and os.environ.get("META_AD_ACCOUNT_ID"))


def _traer_de_la_api(desde: str, hasta: str) -> list:
    """Pide los Insights al Graph.

    Los imports van adentro: la maquina de Fly tiene 256 MB y este modulo se
    importa aunque no haya credenciales.
    """
    import json

    import requests

    from meta_config import GRAPH

    cuenta = os.environ["META_AD_ACCOUNT_ID"]
    params = {
        "access_token": os.environ["META_ADS_TOKEN"],
        "level": "campaign",
        "time_increment": 1,
        "fields": ("campaign_id,campaign_name,spend,account_currency,"
                   "impressions,clicks,reach,actions"),
        "time_range": json.dumps({"since": desde, "until": hasta}),
        "limit": 500,
    }

    filas, url = [], f"{GRAPH}/{cuenta}/insights"
    while url:
        r = requests.get(url, params=params, timeout=30)
        if not r.ok:
            logger.error(f"Insights: la API contesto {r.status_code}")
            break
        d = r.json()
        if "error" in d:
            logger.error(f"Insights: {d['error']}")
            break
        filas.extend(d.get("data", []))
        url = d.get("paging", {}).get("next")
        params = {}   # el `next` ya trae todo adentro
    return filas


def _leads_de(fila: dict) -> int:
    """Los leads no son un campo: hay que buscarlos entre las acciones."""
    for accion in fila.get("actions") or []:
        if accion.get("action_type") in _ACCIONES_DE_LEAD:
            try:
                return int(float(accion.get("value") or 0))
            except (TypeError, ValueError):
                return 0
    return 0


def _entero(valor) -> int:
    try:
        return int(float(valor or 0))
    except (TypeError, ValueError):
        return 0


def sincronizar(db_path: str, desde: str, hasta: str, fetch=None) -> dict:
    """Trae los Insights del periodo y los deja en `meta_insights`.

    Idempotente por (date, campaign_id): correrlo dos veces no duplica, y
    resincronizar un dia ya guardado lo actualiza — la ultima corrida manda,
    porque Meta corrige cifras hacia atras.

    `fetch` existe para los tests: recibe (desde, hasta) y devuelve la lista de
    filas crudas. En produccion se usa el default, que va a la API.
    """
    if fetch is None:
        if not hay_credenciales():
            logger.warning("Insights: sin META_ADS_TOKEN o META_AD_ACCOUNT_ID, "
                           "se saltea el sync")
            return {"filas": 0, "campanas": 0, "salteado": "sin_credenciales"}
        fetch = _traer_de_la_api

    crudas = fetch(desde, hasta)

    conn = _connect(db_path)
    try:
        campanas, guardadas = set(), 0
        for fila in crudas:
            campana = fila.get("campaign_id")
            if not campana:
                # Sin id no hay con que emparejarla ni contra que hacer upsert.
                logger.warning("Insights: fila sin campaign_id, se saltea")
                continue
            campanas.add(campana)
            guardadas += 1
            conn.execute("""
                INSERT INTO meta_insights
                    (date, campaign_id, campaign_name, spend, currency,
                     impressions, clicks, reach, leads, synced_at)
                VALUES (?,?,?,?,?,?,?,?,?, CURRENT_TIMESTAMP)
                ON CONFLICT(date, campaign_id) DO UPDATE SET
                    campaign_name = excluded.campaign_name,
                    spend         = excluded.spend,
                    currency      = excluded.currency,
                    impressions   = excluded.impressions,
                    clicks        = excluded.clicks,
                    reach         = excluded.reach,
                    leads         = excluded.leads,
                    synced_at     = CURRENT_TIMESTAMP
            """, (
                fila.get("date_start"),
                campana,
                fila.get("campaign_name"),
                float(fila.get("spend") or 0),
                fila.get("account_currency"),
                _entero(fila.get("impressions")),
                _entero(fila.get("clicks")),
                _entero(fila.get("reach")),
                _leads_de(fila),
            ))
        conn.commit()
    finally:
        conn.close()

    logger.info(f"Insights: {guardadas} filas, {len(campanas)} campanas, "
                f"{desde} a {hasta}")
    return {"filas": guardadas, "campanas": len(campanas), "salteado": None}
