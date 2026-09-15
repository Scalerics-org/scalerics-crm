"""De que anuncio vino cada lead, recuperado de la API.

De 245 leads de Meta, solo 3 tenian guardado el `ad_id`. No porque Meta no lo
mande —de los 109 leads que la API todavia conserva, 108 lo traen— sino porque
la importacion diaria pedia el campo y despues no lo guardaba: el webhook
llamaba a `_guardar_campana_del_lead` y el import no. El dato llegaba y se
tiraba.

Sin eso el panel puede decir cuanto costo cada lead y nada sobre cual sirvio,
que es justo lo que hay que saber para decidir la proxima campana.

**Meta guarda los leads 90 dias.** Lo de antes se perdio y no hay de donde
sacarlo: este backfill recupera lo que este adentro de la ventana. Por eso el
arreglo de verdad es el del camino de entrada, en `routes/meta.py`; esto es el
rescate de una sola vez.
"""

import json
import logging
import os
import re

from database import _connect

logger = logging.getLogger(__name__)

# Los campos que hacen falta para atribuir. `is_organic` esta porque un lead
# que llego al formulario sin anuncio no es un lead al que le falte el dato:
# es otra cosa, y mezclarlo con la pauta ensucia el costo por lead.
_CAMPOS = ("id,created_time,is_organic,ad_id,ad_name,adset_id,"
           "campaign_id,campaign_name,field_data")


def hay_credenciales() -> bool:
    return bool(os.environ.get("META_PAGE_TOKEN")
                and os.environ.get("META_PAGE_ID"))


def _solo_digitos(texto) -> str:
    return re.sub(r"[^\d]", "", str(texto or ""))


def _cola(telefono) -> str:
    """Los ultimos 8 digitos.

    El CRM guarda "099123456" y Meta manda "+59899123456": mismo telefono,
    distinta escritura. Comparar los ultimos 8 digitos los junta sin inventar
    reglas de prefijo por pais, que es donde se rompe en cuanto entra un lead
    de Argentina.
    """
    d = _solo_digitos(telefono)
    return d[-8:] if len(d) >= 8 else ""


def _traer_de_la_api() -> list:
    import requests

    from meta_config import GRAPH

    pt = os.environ["META_PAGE_TOKEN"]
    page = os.environ["META_PAGE_ID"]

    r = requests.get(f"{GRAPH}/{page}/leadgen_forms",
                     params={"access_token": pt, "fields": "id,name"},
                     timeout=30)
    if not r.ok:
        logger.error(f"Atribucion: los formularios contestaron {r.status_code}")
        return []

    leads = []
    for form in r.json().get("data", []):
        url = f"{GRAPH}/{form['id']}/leads"
        params = {"access_token": pt, "fields": _CAMPOS, "limit": 200}
        while url:
            rr = requests.get(url, params=params, timeout=40)
            if not rr.ok:
                logger.error(f"Atribucion: el formulario {form['id']} contesto "
                             f"{rr.status_code}")
                break
            d = rr.json()
            leads.extend(d.get("data", []))
            url = d.get("paging", {}).get("next")
            params = {}
    return leads


def _campo(lead: dict, *nombres):
    for f in lead.get("field_data") or []:
        if str(f.get("name", "")).lower() in nombres:
            vs = f.get("values") or []
            return vs[0] if vs else None
    return None


def backfill_atribucion(db_path: str, traer=None) -> dict:
    """Empareja los leads de la API con los del CRM y les guarda el anuncio.

    Empareja por, en este orden: el id de Meta si ya estaba guardado, los
    ultimos 8 digitos del telefono, y el mail. Solo toca leads con
    `source='meta'`: un negocio de Google con el mismo telefono no es el mismo
    lead.

    **Un dato ausente nunca pisa uno conocido.** Es como se perdieron los
    primeros: una segunda pasada sin `campaign_id` le borraba al lead la
    campana que ya se sabia.
    """
    if traer is None:
        if not hay_credenciales():
            logger.warning("Atribucion: sin META_PAGE_TOKEN o META_PAGE_ID")
            return {"emparejados": 0, "con_anuncio": 0, "sin_emparejar": 0,
                    "salteado": "sin_credenciales"}
        traer = _traer_de_la_api

    crudos = traer()

    conn = _connect(db_path)
    try:
        filas = conn.execute(
            "SELECT id, phone, email, meta_lead_id FROM businesses "
            "WHERE source = 'meta'").fetchall()

        por_id, por_tel, por_mail = {}, {}, {}
        for f in filas:
            if f["meta_lead_id"]:
                por_id[str(f["meta_lead_id"])] = f["id"]
            cola = _cola(f["phone"])
            if cola:
                por_tel.setdefault(cola, f["id"])
            if f["email"]:
                por_mail.setdefault(str(f["email"]).strip().lower(), f["id"])

        emparejados, con_anuncio, sin_emparejar = 0, 0, 0
        for lead in crudos:
            lid = str(lead.get("id") or "")
            tel = _campo(lead, "phone_number", "telefono", "phone", "celular")
            mail = _campo(lead, "email", "correo")

            bid = por_id.get(lid)
            if bid is None and _cola(tel):
                bid = por_tel.get(_cola(tel))
            if bid is None and mail:
                bid = por_mail.get(str(mail).strip().lower())
            if bid is None:
                sin_emparejar += 1
                continue

            organico = 1 if lead.get("is_organic") else 0
            conn.execute(
                "UPDATE businesses SET "
                "  meta_lead_id       = COALESCE(?, meta_lead_id), "
                "  meta_campaign_id   = COALESCE(?, meta_campaign_id), "
                "  meta_campaign_name = COALESCE(?, meta_campaign_name), "
                "  meta_adset_id      = COALESCE(?, meta_adset_id), "
                "  meta_ad_id         = COALESCE(?, meta_ad_id), "
                "  meta_ad_name       = COALESCE(?, meta_ad_name), "
                "  meta_organico      = ? "
                "WHERE id = ?",
                (lid or None,
                 lead.get("campaign_id") or None,
                 lead.get("campaign_name") or None,
                 lead.get("adset_id") or None,
                 lead.get("ad_id") or None,
                 lead.get("ad_name") or None,
                 organico, bid))
            emparejados += 1
            if lead.get("ad_id"):
                con_anuncio += 1
            por_id[lid] = bid

        conn.commit()
    finally:
        conn.close()

    logger.info(f"Atribucion: {emparejados} emparejados, {con_anuncio} con "
                f"anuncio, {sin_emparejar} sin emparejar")
    return {"emparejados": emparejados, "con_anuncio": con_anuncio,
            "sin_emparejar": sin_emparejar, "leidos": len(crudos)}
