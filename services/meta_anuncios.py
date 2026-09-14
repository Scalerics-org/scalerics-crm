"""Los anuncios de Meta y sus fotos, sincronizados a tablas propias.

Hermano de `meta_insights.py`, que hace lo mismo al grano de campana. Existe
porque la campana no es la unidad sobre la que se decide: adentro de una campana
conviven varios anuncios y uno se puede estar llevando la mitad de la plata sin
traer a nadie.

**Las URLs de imagen de Meta caducan.** Vienen firmadas, con el permiso adentro
de la query. Guardarlas en la base y servirlas desde el panel funcionaria hoy y
daria imagenes rotas en unos dias, sin que nada avise. Por eso el archivo se
baja una vez al volumen y de ahi en mas se sirve el nuestro.

**Los videos no tienen foto.** Con los permisos que tiene la app hoy, pedir el
still de un video contesta "Application does not have permission" (probado
contra la cuenta real el 14/9/2026). El anuncio se guarda igual y sin imagen: el
gasto existe y hay que poder verlo. La miniatura de 64x64 que Meta si deja bajar
no sirve para mostrar.

**Credencial aparte.** Las mismas que el sync de campanas: META_ADS_TOKEN y
META_AD_ACCOUNT_ID. Sin ellas se saltea con un warning y el resto del modulo
sigue andando.
"""

import logging
import os

from database import _connect
from services.meta_insights import _ACCIONES_DE_LEAD, _entero, hay_credenciales

logger = logging.getLogger(__name__)

# Lo que se le pide a la API por cada anuncio. `effective_status` y no `status`:
# `status` dice si el anuncio esta prendido, `effective_status` dice si de
# verdad esta corriendo — un anuncio prendido adentro de una campana apagada no
# corre, y es la diferencia entre "esto se esta pautando" y no.
_CAMPOS_AD = (
    "id,name,effective_status,campaign{id,name},adset{name},"
    "creative{id,object_type,title,body,image_url,video_id}"
)

_CAMPOS_INSIGHT = (
    "ad_id,ad_name,spend,account_currency,impressions,clicks,reach,actions"
)


class _ErrorDeMeta(RuntimeError):
    """La API contesto mal. Existe para que el sync no confunda "fallo" con
    "no habia nada": las dos cosas devolvian cero filas y la segunda es
    normal."""


def _motivo(respuesta) -> str:
    """Lo que dijo Meta, recortado. Un 403 puede ser falta de permiso o exceso
    de llamadas, y sin el mensaje hay que salir a averiguar cual."""
    try:
        e = respuesta.json().get("error") or {}
        return f"code={e.get('code')} {e.get('message') or ''}"[:200]
    except Exception:
        return (respuesta.text or "")[:200]


def _dir_creativos(db_path: str) -> str:
    """Al lado de la base: en produccion es el volumen, en los tests el tmp."""
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "creativos")


def _traer_ads() -> list:
    import requests

    from meta_config import GRAPH

    cuenta = os.environ["META_AD_ACCOUNT_ID"]
    params = {"access_token": os.environ["META_ADS_TOKEN"],
              "fields": _CAMPOS_AD, "limit": 200}
    filas, url = [], f"{GRAPH}/{cuenta}/ads"
    while url:
        r = requests.get(url, params=params, timeout=40)
        if not r.ok:
            # El cuerpo va al log: un 403 puede ser "no tenes permiso" o "te
            # pasaste de llamadas", y son dos problemas completamente
            # distintos. Con el numero solo hay que salir a averiguarlo.
            logger.error(f"Anuncios: la API contesto {r.status_code}: "
                         f"{_motivo(r)}")
            raise _ErrorDeMeta(f"ads {r.status_code}: {_motivo(r)}")
        d = r.json()
        if "error" in d:
            logger.error(f"Anuncios: {d['error']}")
            raise _ErrorDeMeta(f"ads: {d['error']}")
        filas.extend(d.get("data", []))
        url = d.get("paging", {}).get("next")
        params = {}
    return filas


def _traer_insights(desde: str, hasta: str) -> list:
    import json

    import requests

    from meta_config import GRAPH

    cuenta = os.environ["META_AD_ACCOUNT_ID"]
    params = {
        "access_token": os.environ["META_ADS_TOKEN"],
        "level": "ad",
        "time_increment": 1,
        "fields": _CAMPOS_INSIGHT,
        "time_range": json.dumps({"since": desde, "until": hasta}),
        "limit": 500,
    }
    filas, url = [], f"{GRAPH}/{cuenta}/insights"
    while url:
        r = requests.get(url, params=params, timeout=60)
        if not r.ok:
            logger.error(f"Insights por anuncio: la API contesto "
                         f"{r.status_code}: {_motivo(r)}")
            raise _ErrorDeMeta(f"insights {r.status_code}: {_motivo(r)}")
        d = r.json()
        if "error" in d:
            logger.error(f"Insights por anuncio: {d['error']}")
            raise _ErrorDeMeta(f"insights: {d['error']}")
        filas.extend(d.get("data", []))
        url = d.get("paging", {}).get("next")
        params = {}
    return filas


def _bajar(url: str, destino: str) -> bool:
    import requests

    r = requests.get(url, timeout=40, stream=True)
    if not r.ok:
        logger.warning(f"Creativo: no pude bajar la imagen ({r.status_code})")
        return False
    with open(destino, "wb") as f:
        for trozo in r.iter_content(64 * 1024):
            f.write(trozo)
    return True


def _leads_de(fila: dict) -> int:
    for accion in fila.get("actions") or []:
        if accion.get("action_type") in _ACCIONES_DE_LEAD:
            try:
                return int(float(accion.get("value") or 0))
            except (TypeError, ValueError):
                return 0
    return 0


def sincronizar_anuncios(db_path: str, desde: str, hasta: str,
                         traer_ads=None, traer_insights=None, bajar=None) -> dict:
    """Trae anuncios, fotos e insights por anuncio. Idempotente.

    Las tres funciones que hablan con afuera se pueden inyectar: los tests
    prueban el guardado, que es donde estan los errores, sin salir a la red.
    """
    if traer_ads is None or traer_insights is None:
        if not hay_credenciales():
            logger.warning("Anuncios: sin META_ADS_TOKEN o META_AD_ACCOUNT_ID, "
                           "se saltea el sync")
            return {"anuncios": 0, "filas": 0, "imagenes": 0,
                    "salteado": "sin_credenciales"}
        traer_ads = traer_ads or _traer_ads
        traer_insights = traer_insights or _traer_insights
    bajar = bajar or _bajar

    carpeta = _dir_creativos(db_path)
    os.makedirs(carpeta, exist_ok=True)

    conn = _connect(db_path)
    try:
        anuncios, imagenes = 0, 0
        for a in traer_ads():
            ad_id = a.get("id")
            if not ad_id:
                logger.warning("Anuncios: fila sin id, se saltea")
                continue
            c = a.get("creative") or {}
            url = c.get("image_url")

            # El archivo se baja una sola vez. Si ya esta, la URL nueva de Meta
            # se guarda igual (sirve para volver a bajarla si se borrara) pero
            # no se vuelve a pedir el archivo.
            previo = conn.execute(
                "SELECT imagen_archivo FROM meta_ads WHERE ad_id = ?",
                (ad_id,)).fetchone()
            archivo = previo["imagen_archivo"] if previo else None
            if archivo and not os.path.exists(archivo):
                archivo = None
            if url and not archivo:
                destino = os.path.join(carpeta, f"{ad_id}.jpg")
                try:
                    if bajar(url, destino):
                        archivo = destino
                        imagenes += 1
                except Exception as e:
                    # Una imagen que no baja no puede tumbar el gasto de toda
                    # la cuenta.
                    logger.warning(f"Creativo {ad_id}: {e}")

            conn.execute("""
                INSERT INTO meta_ads
                    (ad_id, ad_name, campaign_id, campaign_name, adset_name,
                     effective_status, creative_id, object_type, titulo, cuerpo,
                     imagen_url, imagen_archivo, synced_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?, CURRENT_TIMESTAMP)
                ON CONFLICT(ad_id) DO UPDATE SET
                    ad_name          = excluded.ad_name,
                    campaign_id      = excluded.campaign_id,
                    campaign_name    = excluded.campaign_name,
                    adset_name       = excluded.adset_name,
                    effective_status = excluded.effective_status,
                    creative_id      = excluded.creative_id,
                    object_type      = excluded.object_type,
                    titulo           = excluded.titulo,
                    cuerpo           = excluded.cuerpo,
                    imagen_url       = excluded.imagen_url,
                    imagen_archivo   = excluded.imagen_archivo,
                    synced_at        = CURRENT_TIMESTAMP
            """, (
                ad_id, a.get("name"),
                (a.get("campaign") or {}).get("id"),
                (a.get("campaign") or {}).get("name"),
                (a.get("adset") or {}).get("name"),
                a.get("effective_status"),
                c.get("id"), c.get("object_type"),
                c.get("title") or None, c.get("body") or None,
                url, archivo,
            ))
            anuncios += 1

        filas, fallo = 0, None
        try:
            crudas = traer_insights(desde, hasta)
        except _ErrorDeMeta as e:
            # Los anuncios ya se guardaron y eso no se tira. Pero el resultado
            # tiene que decir que el gasto NO se trajo: "filas: 0" a secas se
            # lee como "no hubo gasto", que es lo contrario de lo que paso.
            logger.error(f"Anuncios: el gasto no se pudo traer: {e}")
            crudas, fallo = [], str(e)

        for f in crudas:
            ad_id = f.get("ad_id")
            if not ad_id:
                continue
            filas += 1
            conn.execute("""
                INSERT INTO meta_ad_insights
                    (date, ad_id, spend, currency, impressions, clicks, reach,
                     leads, synced_at)
                VALUES (?,?,?,?,?,?,?,?, CURRENT_TIMESTAMP)
                ON CONFLICT(date, ad_id) DO UPDATE SET
                    spend       = excluded.spend,
                    currency    = excluded.currency,
                    impressions = excluded.impressions,
                    clicks      = excluded.clicks,
                    reach       = excluded.reach,
                    leads       = excluded.leads,
                    synced_at   = CURRENT_TIMESTAMP
            """, (
                f.get("date_start"), ad_id,
                float(f.get("spend") or 0),
                f.get("account_currency"),
                _entero(f.get("impressions")),
                _entero(f.get("clicks")),
                _entero(f.get("reach")),
                _leads_de(f),
            ))

        conn.commit()
    finally:
        conn.close()

    logger.info(f"Anuncios: {anuncios} anuncios, {filas} filas de gasto, "
                f"{imagenes} imagenes nuevas")
    salida = {"anuncios": anuncios, "filas": filas, "imagenes": imagenes}
    if fallo:
        salida["error_gasto"] = fallo
    return salida
