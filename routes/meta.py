"""Meta Lead Ads webhook — recibe leads de formularios de Facebook/Instagram."""

import hashlib
import hmac
import json
import logging
import os
import re
import threading

import requests
from flask import Blueprint, current_app, jsonify, request

import sqlite3 as _sq_meta

import time

from database import (
    buscar_ficha_meta_por_mail,
    envio_meta_registrado,
    insert_business,
    norm_created_time,
    registrar_envio_meta,
    update_business,
    get_business_by_phone,
    log_activity,
)
from services.email_service import send_new_meta_lead_notification, send_meta_token_alert
from meta_config import GRAPH_VERSION, GRAPH  # re-exportados: mismo nombre para no tocar los usos internos

logger = logging.getLogger(__name__)
meta_bp = Blueprint("meta", __name__)

VERIFY_TOKEN   = os.environ.get("META_VERIFY_TOKEN", "scalerics_meta_webhook_2026")
APP_SECRET     = os.environ.get("META_APP_SECRET", "")
PAGE_TOKEN     = os.environ.get("META_PAGE_TOKEN", "")
ALLOW_UNSIGNED = os.environ.get("META_ALLOW_UNSIGNED", "").lower() == "true"


def _db() -> str:
    return current_app.config["DB_PATH"]


def _get_admin_emails(db: str) -> list[str]:
    # Valvula para probar la integracion sin escribirle a todo el equipo:
    # si META_NOTIFY_OVERRIDE tiene una direccion, los avisos de Meta van solo
    # ahi. REEMPLAZA la lista, no la suma — ADMIN_EMAIL sumaba, y para esto
    # hace falta lo contrario. En produccion tiene que quedar vacia.
    override = os.environ.get("META_NOTIFY_OVERRIDE", "").strip().lower()
    if override:
        logger.warning(f"META_NOTIFY_OVERRIDE activo: los avisos de Meta van solo a {override}")
        return [override]

    emails = set()
    admin_env = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    try:
        conn = _sq_meta.connect(db); conn.row_factory = _sq_meta.Row
        rows = conn.execute("""
            SELECT u.email FROM users u
            LEFT JOIN roles r ON u.role_id = r.id
            WHERE LOWER(r.name) = 'admin'
        """).fetchall()
        for r in rows:
            if r["email"]:
                emails.add(r["email"].strip().lower())
        conn.close()
    except Exception as e:
        logger.warning(f"Could not query admin emails: {e}")
    if admin_env:
        emails.add(admin_env)
    return list(emails)


def _guardar_campana_del_lead(db: str, lead_id: int, lead_data: dict) -> None:
    """Escribe la campana y el anuncio del lead en sus columnas propias.

    Aditivo: `notes` se sigue escribiendo igual en el llamador. Esto existe
    para poder agrupar y para juntar el lead con el gasto de su campana, que
    con la campana metida adentro de un texto no se puede.

    **Un dato ausente no pisa uno conocido.** Meta no siempre manda los cinco
    campos, y la importacion diaria vuelve a pasar por leads que ya estaban:
    sin el COALESCE, una segunda pasada sin `campaign_id` le borraria al lead
    la campana que ya se sabia.
    """
    from database import _connect

    conn = _connect(db)
    try:
        conn.execute(
            "UPDATE businesses SET "
            "  meta_campaign_id   = COALESCE(?, meta_campaign_id), "
            "  meta_campaign_name = COALESCE(?, meta_campaign_name), "
            "  meta_adset_id      = COALESCE(?, meta_adset_id), "
            "  meta_ad_id         = COALESCE(?, meta_ad_id), "
            "  meta_ad_name       = COALESCE(?, meta_ad_name) "
            "WHERE id = ?",
            (lead_data.get("campaign_id") or None,
             lead_data.get("campaign_name") or None,
             lead_data.get("adset_id") or None,
             lead_data.get("ad_id") or None,
             lead_data.get("ad_name") or None,
             lead_id))
        conn.commit()
    finally:
        conn.close()


def _notify_new_meta_lead(db: str, lead_name: str, phone: str, campaign: str, city: str, lead_id: int):
    for email in _get_admin_emails(db):
        try:
            send_new_meta_lead_notification(email, lead_name, phone, campaign, city, lead_id)
        except Exception as e:
            logger.error(f"Failed to notify {email} of new Meta lead: {e}")
    # WhatsApp — configurar ADMIN_WA_PHONE en .env cuando esté listo
    # wa_phone = os.environ.get("ADMIN_WA_PHONE", "")
    # if wa_phone:
    #     _send_wa_notification(wa_phone, lead_name, phone, campaign)


def _registrar_envio(db: str, biz_id: int, meta_lead_id, created_time, fields: dict,
                     lead: dict) -> bool:
    """Guarda este formulario como un envio de la ficha `biz_id`.

    Cada envio cuenta en su mes, como lo cuenta Meta: quien vuelve a escribir
    aparece tambien en el mes de la vuelta (ver database.ENVIOS_META_SQL).
    Nunca tumba la ingesta: si falla, el lead ya quedo guardado igual.
    """
    try:
        return registrar_envio_meta(
            db, biz_id, meta_lead_id, created_time,
            form_data=json.dumps(fields, ensure_ascii=False),
            campaign_id=lead.get("campaign_id"), campaign_name=lead.get("campaign_name"),
            ad_id=lead.get("ad_id"), ad_name=lead.get("ad_name"))
    except Exception as e:
        logger.error(f"No se pudo registrar el envio de Meta {meta_lead_id}: {_redact_secrets(str(e))}")
        return False


def _envio_sobre_ficha_existente(db: str, phone: str, email: str):
    """La ficha a la que pertenece un formulario que el INSERT no creo.

    Por telefono (con sus variantes) y, si el formulario no trae telefono, por
    el mail de una ficha de Meta. None si no hay ninguna.
    """
    if phone:
        return get_business_by_phone(db, phone)
    return buscar_ficha_meta_por_mail(db, email)


def _ahora_utc() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _importar_formulario(db: str, lead: dict, fields: dict, ficha: dict):
    """El paso comun de los imports contra Graph: guarda un formulario.

    Devuelve el id de la ficha si es NUEVA, o None si el formulario ya estaba
    registrado o es de una ficha que ya existia (duplicado, como antes). En
    los dos casos de ficha existente se registra el envio: es lo que hace
    que el import diario rellene `meta_lead_envios` con todo lo que Graph
    devuelve (backfill) sin crear fichas ni avisar a nadie.
    """
    meta_id = lead.get("id")
    if envio_meta_registrado(db, meta_id):
        return None
    ficha["scraped_at"] = ficha.get("scraped_at") or _ahora_utc()
    phone, email = ficha.get("phone") or "", ficha.get("email") or ""
    # Primero la ficha de Meta que ya tiene ese telefono (con cualquier grafia:
    # "+598 99..." y "59899..." son la misma persona) o, sin telefono, ese mail.
    # Una ficha de otra cohorte no se toma: el INSERT de abajo decide como
    # siempre, y ese lead no puede quedar fuera de Meta Ads.
    existente = _envio_sobre_ficha_existente(db, phone, email)
    if existente and (existente.get("source") or "") == "meta":
        _registrar_envio(db, existente["id"], meta_id, ficha["scraped_at"], fields, lead)
        return None
    biz_id = insert_business(db, ficha)
    if biz_id:
        _registrar_envio(db, biz_id, meta_id, ficha["scraped_at"], fields, lead)
        return biz_id
    existente = existente or _envio_sobre_ficha_existente(db, phone, email)
    if existente:
        _registrar_envio(db, existente["id"], meta_id, ficha["scraped_at"], fields, lead)
    return None


def _page_token() -> str:
    """El token vigente.

    En Fly el token se renueva con `flyctl secrets set META_PAGE_TOKEN=...`,
    que reinicia el proceso con el entorno nuevo. Leer `os.environ` en cada
    llamada — y no la constante de módulo, que quedó congelada en el import —
    es lo que hace que el valor nuevo se use sin depender de un redeploy.
    """
    return os.environ.get("META_PAGE_TOKEN") or PAGE_TOKEN


def _redact_secrets(text: str) -> str:
    """Saca el access_token de un mensaje de error antes de que viaje por mail.

    requests.raise_for_status() incluye la URL completa en el mensaje de
    excepción, y esa URL lleva el PAGE_TOKEN en el query string. El log
    puede quedarse con el error crudo; el mail no.
    """
    return re.sub(r"access_token=[^&\s]+", "access_token=***", text)


def _verify_signature(payload: bytes, sig_header: str) -> bool:
    if not APP_SECRET:
        if ALLOW_UNSIGNED:
            logger.warning("META_APP_SECRET sin configurar y META_ALLOW_UNSIGNED=true — firma no verificada")
            return True
        logger.error("META_APP_SECRET sin configurar — se rechaza el webhook")
        return False
    if not sig_header:
        return False
    try:
        expected = "sha256=" + hmac.new(APP_SECRET.encode(), payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig_header)
    except Exception:
        return False


# ── Webhook verification (GET) ────────────────────────────────────────────────

@meta_bp.route("/api/meta/webhook", methods=["GET"])
def meta_webhook_verify():
    mode      = request.args.get("hub.mode")
    token     = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("Meta webhook verified OK")
        return challenge, 200
    return "Forbidden", 403


# ── Webhook events (POST) ─────────────────────────────────────────────────────

@meta_bp.route("/api/meta/webhook", methods=["POST"])
def meta_webhook_receive():
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(request.data, sig):
        logger.warning(f"Meta webhook: invalid signature (sig={sig[:20] if sig else 'empty'})")
        return "Invalid signature", 403

    data = request.get_json(silent=True) or {}
    if data.get("object") != "page":
        return "ok", 200

    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "leadgen":
                continue
            lead_id = change.get("value", {}).get("leadgen_id")
            form_id = change.get("value", {}).get("form_id")
            if lead_id:
                logger.info(f"Meta webhook received: leadgen_id={lead_id} form_id={form_id}")
                threading.Thread(
                    target=_fetch_and_store_lead,
                    args=(current_app._get_current_object(), lead_id, form_id),
                    daemon=True,
                ).start()

    return "ok", 200


def _merge_lead_into_existing(db: str, phone: str, email: str, fields: dict) -> int | None:
    """El INSERT no creó fila: ya hay un negocio con ese teléfono.

    `insert_business` usa `INSERT OR IGNORE` sobre `businesses.phone UNIQUE`,
    así que un negocio ya scrapeado de Google que después llena el formulario
    de Meta se descartaba entero. El lead es real y llegó: al negocio que ya
    estaba se le devuelve `source='meta'` y se le guarda el `form_data`.

    Devuelve el id del negocio existente, o None si no se pudo ubicar
    (sin teléfono no hay por dónde buscarlo).
    """
    if not phone:
        return None
    try:
        existente = get_business_by_phone(db, phone)
    except Exception as e:
        logger.error(f"No se pudo buscar el negocio existente por teléfono: {_redact_secrets(str(e))}")
        return None
    if not existente:
        return None
    biz_id = existente["id"]
    update_fields = {
        "source": "meta",
        "form_data": json.dumps(fields, ensure_ascii=False),
    }
    # El mail del formulario nunca pisa uno que ya estaba: mismo criterio que
    # scripts/backfill_meta_emails.py. Un negocio ya scrapeado puede tener un
    # mail cargado a mano o de otra fuente; un formulario de Meta posterior
    # no es más confiable que eso, así que solo completa el campo si estaba
    # vacío, nunca lo corrige.
    # La excepcion es la cohorte de discovery: ese mail lo saco un bot del
    # sitio web del comercio, y el que el dueno tipeo en el formulario de Meta
    # es de mejor procedencia. Si no se pisara, la secuencia de recordatorios
    # -correo real- terminaria escribiendole a una casilla que nadie tipeo.
    existente_source = (existente.get("source") or "").strip().lower()
    mail_previo = (existente.get("email") or "").strip()
    if email and (not mail_previo or existente_source == "discovery"):
        update_fields["email"] = email
    elif existente_source == "discovery" and mail_previo:
        # Por el pipeline de Meta solo 63 de 110 leads traen mail. Sin mail de
        # formulario no alcanza con no pisar el raspado: la fila igual pasa a
        # source='meta' con crm_status='sin_contactar', que es exactamente el
        # filtro de services/meta_reminders.py, y la secuencia le escribiria a
        # la casilla que saco el bot. Se limpia: sin mail tipeado, esta fila no
        # tiene direccion que darle a la secuencia.
        update_fields["email"] = None
    update_business(db, biz_id, **update_fields)
    return biz_id


def _fetch_and_store_lead(app, lead_id: str, form_id: str):
    with app.app_context():
        db = app.config["DB_PATH"]
        try:
            page_token = _page_token()
            if not page_token:
                logger.warning("META_PAGE_TOKEN not set — cannot fetch lead")
                return

            r = requests.get(
                f"{GRAPH}/{lead_id}",
                params={"access_token": page_token, "fields": "field_data,created_time,ad_name,ad_id,adset_id,campaign_name,campaign_id,form_id"},
                timeout=10,
            )
            r.raise_for_status()
            lead_data = r.json()

            fields = {f["name"].lower(): f["values"][0] if f.get("values") else ""
                      for f in lead_data.get("field_data", [])}

            name  = fields.get("full_name") or fields.get("nombre") or fields.get("name") or "Lead Meta"
            phone = (fields.get("phone_number") or fields.get("telefono") or
                     fields.get("phone") or fields.get("celular") or "")
            email = fields.get("email") or fields.get("correo") or ""
            city  = fields.get("city") or fields.get("ciudad") or ""

            ad_name       = lead_data.get("ad_name", "")
            campaign_name = lead_data.get("campaign_name", "")
            notes = f"Meta Lead Ad · {campaign_name or ad_name or form_id or ''}".strip(" ·")

            # Webhook que reintenta: este formulario ya se guardo. Sin esto, un
            # reintento de Meta repetia el aviso de "nuevo lead".
            if envio_meta_registrado(db, lead_id):
                logger.info(f"Meta lead {lead_id} ya registrado: se ignora el reintento")
                return

            # Sin created_time se usa ahora mismo, y el MISMO valor va a la ficha
            # y al envio: si no, el envio y el scraped_at quedarian a segundos de
            # distancia y el mismo formulario contaria dos veces.
            created_at = norm_created_time(lead_data.get("created_time", "")) or _ahora_utc()

            # Sin telefono el INSERT nunca choca (phone NULL no es UNIQUE): la
            # persona que vuelve a escribir con el mismo mail se busca a mano.
            ficha_por_mail = None if phone else buscar_ficha_meta_por_mail(db, email)
            if ficha_por_mail:
                update_business(db, ficha_por_mail["id"],
                                form_data=json.dumps(fields, ensure_ascii=False))
                _guardar_campana_del_lead(db, ficha_por_mail["id"], lead_data)
                _registrar_envio(db, ficha_por_mail["id"], lead_id, created_at, fields, lead_data)
                log_activity(db, "meta_webhook", "lead_updated", "lead", ficha_por_mail["id"], name,
                             f"Volvió a escribir por Meta · {campaign_name or ad_name}", user_id=None)
                threading.Thread(
                    target=_notify_new_meta_lead,
                    args=(db, name, phone, campaign_name or ad_name or "", city, ficha_por_mail["id"]),
                    daemon=True,
                ).start()
                return

            biz_id = insert_business(db, {
                "name":       name,
                "phone":      phone or None,
                "email":      email or None,
                "city":       city or None,
                "category":   "Meta Lead Ad",
                "status":     "scraped",
                "notes":      notes,
                "score":      70,
                "source":     "meta",
                "form_data":  json.dumps(fields, ensure_ascii=False),
                "scraped_at": created_at or None,
            })

            if biz_id:
                _guardar_campana_del_lead(db, biz_id, lead_data)
                _registrar_envio(db, biz_id, lead_id, created_at, fields, lead_data)
                log_activity(db, "meta_webhook", "lead_created", "lead", biz_id, name,
                             f"Fuente: Meta Lead Ad · {campaign_name or ad_name}", user_id=None)
                logger.info(f"Meta lead stored: {name} ({phone}) → id {biz_id}")
                threading.Thread(
                    target=_notify_new_meta_lead,
                    args=(db, name, phone, campaign_name or ad_name or "", city, biz_id),
                    daemon=True,
                ).start()
            else:
                existente_id = _merge_lead_into_existing(db, phone, email, fields)
                if existente_id:
                    _guardar_campana_del_lead(db, existente_id, lead_data)
                    _registrar_envio(db, existente_id, lead_id, created_at, fields, lead_data)
                    logger.warning(
                        f"Meta lead sobre un negocio que ya existia: {name} ({phone}) "
                        f"→ id {existente_id}; se le devolvio source='meta' y se guardo el form_data"
                    )
                    log_activity(db, "meta_webhook", "lead_updated", "lead", existente_id, name,
                                 f"Lead de Meta sobre un negocio ya existente · {campaign_name or ad_name}",
                                 user_id=None)
                    threading.Thread(
                        target=_notify_new_meta_lead,
                        args=(db, name, phone, campaign_name or ad_name or "", city, existente_id),
                        daemon=True,
                    ).start()
                else:
                    logger.warning(
                        f"Meta lead descartado por el INSERT y sin negocio existente que lo reciba: "
                        f"{name} ({phone}) — recuperar a mano desde el panel de formularios de Meta"
                    )

        except Exception as e:
            logger.error(f"Error processing Meta lead {lead_id}: {_redact_secrets(str(e))}")
            from services.email_service import send_meta_lead_failure_alert
            admins = _get_admin_emails(db)
            if not admins:
                logger.error(
                    f"Meta lead {lead_id} failed with no admin email configured — "
                    f"nobody was alerted, recover manually from the Meta forms panel"
                )
            for admin in admins:
                try:
                    send_meta_lead_failure_alert(admin, lead_id, _redact_secrets(str(e)))
                except Exception as mail_err:
                    logger.error(f"Tampoco se pudo avisar del fallo: {mail_err}")


# ── Trigger historical import from production server ─────────────────────────
#
# Acá vivía `POST /api/meta/reset-import`, que hacía
# `DELETE FROM businesses WHERE category='Meta Lead Ad'` y reimportaba desde
# Graph. Graph solo retiene ~90 días: una llamada borraba para siempre los
# leads viejos que esta rama restaura. Se eliminó a propósito — no volver a
# agregarlo.


@meta_bp.route("/api/meta/sync-planilla", methods=["POST"])
def meta_sync_planilla():
    """Recibe los colores de la planilla de semaforo y los vuelve estados.

    Lo postea un Apps Script pegado a la planilla (`scripts/planilla_semaforo.gs`),
    no lo tira el CRM: leer el color de una celda pide la API de Sheets y una
    credencial nueva, y del lado de Google `getBackgrounds()` ya lo da gratis.

    `?dry=1` calcula y no escribe. La logica vive en services/planilla_semaforo.py.

    POR QUE ACEPTA UN TOKEN PROPIO Y NO SOLO EL DE ADMIN
        El Apps Script que postea aca vive pegado a `Scalerics - Leads - 2026`,
        que **no es de Scalerics**: la planilla es de la agencia y a nosotros nos
        la compartieron. Un script pegado a un archivo ajeno es, a efectos
        practicos, de su dueno: cualquiera con permiso de edicion sobre la
        planilla puede abrir Apps Script y leer sus Propiedades del script.

        Guardar ahi el `ADMIN_TOKEN` significaba darle a un tercero la llave de
        **todo** el CRM para que pudiera mandar colores. `PLANILLA_TOKEN` existe
        para que el peor caso —que se filtre— sea que alguien puede postear
        colores a este endpoint y nada mas.

        `ADMIN_TOKEN` se sigue aceptando: lo usan las corridas a mano y los
        scripts internos, que no viven en un archivo ajeno.
    """
    from flask import session
    token = request.headers.get("x-admin-token", "")
    validos = [os.environ.get(n, "") for n in ("PLANILLA_TOKEN", "ADMIN_TOKEN")]
    # `secrets.compare_digest` en vez de `==`: comparar tokens con el operador
    # normal corta en el primer caracter distinto y filtra, por tiempo, cuanto
    # del token acerto quien prueba.
    autorizado = any(
        esperado and hmac.compare_digest(token, esperado) for esperado in validos)
    if not (session.get("user_id") or autorizado):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    filas = data.get("filas")
    if not isinstance(filas, list):
        return jsonify({"ok": False, "error": "Se espera {\"filas\": [...]}"}), 400
    # Un tope alto pero real: la planilla tiene ~230 filas y el dia que alguien
    # postee un millon, que sea un 400 y no una maquina de 256 MB muriendose.
    if len(filas) > 5000:
        return jsonify({"ok": False, "error": "Demasiadas filas (max 5000)"}), 400

    dry = request.args.get("dry") in ("1", "true", "yes")
    from services.planilla_semaforo import aplicar
    resumen = aplicar(_db(), filas, dry_run=dry)
    logger.info(
        f"Sync planilla{' (dry)' if dry else ''}: {resumen['actualizados']} actualizados, "
        f"{resumen['sin_cambio']} sin cambio, {resumen['sin_match']} sin match, "
        f"{resumen['no_retrocede']} no retroceden, {resumen['color_ignorado']} sin color util"
    )

    # El Registro de demos va DESPUES y aparte: si se cae, los estados ya
    # quedaron aplicados y la respuesta los tiene que seguir diciendo. El
    # detalle del error va al log y no a la respuesta, que la lee un script
    # pegado a una planilla ajena.
    try:
        from services.planilla_semaforo import sincronizar_demos
        demos = sincronizar_demos(_db(), filas, dry_run=dry)
        logger.info(
            f"Sync planilla demos{' (dry)' if dry else ''}: {demos['creadas']} creadas, "
            f"{demos['actualizadas']} actualizadas, {demos['borradas']} borradas, "
            f"{demos['sin_cambio']} sin cambio, {demos['sin_match']} sin match, "
            f"{demos['mes_ignorado']} con pestaña que no es mes, "
            f"{demos['con_presupuesto_no_se_borra']} con presupuesto no se borran"
        )
    except Exception as e:
        logger.error(f"Sync planilla demos fallo (los estados si se aplicaron): "
                     f"{_redact_secrets(str(e))}")
        demos = {"ok": False, "error": "No se pudo sincronizar el registro de demos"}
    return jsonify({"ok": True, "dry": dry, **resumen, "demos": demos})


@meta_bp.route("/api/meta/leads/<int:lead_id>/semaforo", methods=["POST"])
def meta_marcar_semaforo(lead_id):
    """Marca a mano el color del semaforo de un lead desde Meta Ads.

    `{"color": "celeste"}` (o "sin_color"), y opcional `"mes": "AAAA-MM"`: el
    mes de la lista desde donde se marco, para la demo del Registro. Pide el
    panel `meta`. Las reglas —donde se guarda, que pasa con la demo y como
    convive con la planilla— estan en services/planilla_semaforo.marcar_color.
    """
    from services.auth import require_panel
    bloqueo = require_panel(_db(), "meta")
    if bloqueo:
        return bloqueo

    from routes.leads import registrar_cambio_de_estado
    from services.planilla_semaforo import MarcaInvalida, marcar_color

    data = request.get_json(silent=True) or {}
    db = _db()
    try:
        resultado = marcar_color(
            db, lead_id, data.get("color"), mes=data.get("mes"),
            cambiar_estado=lambda estado, nota: registrar_cambio_de_estado(db, lead_id, estado, nota))
    except MarcaInvalida as e:
        return jsonify({"ok": False, "error": str(e)}), e.status
    return jsonify({"ok": True, **resultado})


@meta_bp.route("/api/meta/import-leads", methods=["POST"])
def meta_import_leads():
    from flask import session
    token = request.headers.get("x-admin-token", "")
    expected = os.environ.get("ADMIN_TOKEN", "")
    if not (session.get("user_id") or (expected and token == expected)):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    db = _db()
    pt = _page_token()
    page_id = os.environ.get("META_PAGE_ID", "")
    if not pt or not page_id:
        return jsonify({"ok": False, "error": "META_PAGE_TOKEN o META_PAGE_ID no configurado"}), 400

    def _run():
        new, dup = 0, 0
        try:
            def get_all(url, params):
                results = []
                while url:
                    r = requests.get(url, params=params, timeout=15)
                    if not r.ok:
                        break
                    d = r.json()
                    results.extend(d.get("data", []))
                    url = d.get("paging", {}).get("next")
                    params = {}
                return results

            forms = get_all(
                f"{GRAPH}/{page_id}/leadgen_forms",
                {"access_token": pt, "fields": "id,name,status"}
            )
            for form in forms:
                leads = get_all(
                    f"{GRAPH}/{form['id']}/leads",
                    {"access_token": pt, "fields": "id,created_time,field_data,ad_name,ad_id,adset_id,campaign_name,campaign_id"}
                )
                for lead in leads:
                    fields = {f["name"].lower(): f["values"][0] if f.get("values") else ""
                              for f in lead.get("field_data", [])}
                    name  = (fields.get("full_name") or fields.get("nombre") or
                             fields.get("name") or "Lead Meta")
                    phone = (fields.get("phone_number") or fields.get("telefono") or
                             fields.get("phone") or fields.get("celular") or "")
                    email = fields.get("email") or fields.get("correo") or ""
                    city  = fields.get("city") or fields.get("ciudad") or ""
                    campaign = lead.get("campaign_name") or lead.get("ad_name") or form.get("name", "")
                    notes = f"Meta Lead Ad · {campaign}".strip(" ·")
                    ct = lead.get("created_time","")
                    if ct:
                        try:
                            from datetime import datetime, timezone as tz
                            ct = datetime.fromisoformat(ct.replace("+0000","")).replace(tzinfo=tz.utc).strftime("%Y-%m-%d %H:%M:%S")
                        except Exception:
                            ct = ""
                    biz_id = _importar_formulario(db, lead, fields, {
                        "name":       name,
                        "phone":      phone or None,
                        "email":      email or None,
                        "city":       city or None,
                        "category":   "Meta Lead Ad",
                        "status":     "scraped",
                        "notes":      notes,
                        "score":      70,
                        "source":     "meta",
                        "form_data":  json.dumps(fields, ensure_ascii=False),
                        "scraped_at": ct or None,
                    })
                    if biz_id:
                        new += 1
                    else:
                        # Import masivo: acá el `else` se cuenta y nada más.
                        # A diferencia del webhook no se pisa el negocio que ya
                        # existe ni se notifica: esta importación repasa todos
                        # los formularios de la página, así que casi todos los
                        # leads ya están y notificarlos sería spam.
                        dup += 1
        except Exception as e:
            logger.error(f"Import error: {_redact_secrets(str(e))}")
        logger.info(f"Meta import done: {new} new, {dup} dup")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "Importación iniciada en background"})


# ── One-time setup: exchange user token → long-lived page token ───────────────

@meta_bp.route("/api/meta/setup-token", methods=["POST"])
def meta_setup_token():
    """Exchange a short-lived user token for a long-lived page token and save to env."""
    from flask import session
    if not session.get("user_id"):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    data        = request.get_json() or {}
    user_token  = data.get("user_token", "").strip()
    app_id      = os.environ.get("META_APP_ID", "")
    app_secret  = os.environ.get("META_APP_SECRET", "")

    if not user_token or not app_id or not app_secret:
        return jsonify({"ok": False, "error": "Faltan credenciales"}), 400

    try:
        # 1. Long-lived user token
        r = requests.get(f"{GRAPH}/oauth/access_token", params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id, "client_secret": app_secret,
            "fb_exchange_token": user_token,
        }, timeout=10)
        r.raise_for_status()
        ll_user_token = r.json()["access_token"]

        # 2. Page token for the configured page
        page_id = os.environ.get("META_PAGE_ID", "")
        r2 = requests.get(f"{GRAPH}/{page_id}", params={
            "fields": "access_token,name",
            "access_token": ll_user_token,
        }, timeout=10)
        r2.raise_for_status()
        page_data  = r2.json()
        page_token = page_data.get("access_token", "")
        page_name  = page_data.get("name", "")

        # 3. Save to .env
        _update_env("META_PAGE_TOKEN", page_token)

        return jsonify({"ok": True, "page": page_name, "token_preview": page_token[:20] + "..."})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@meta_bp.route("/api/meta/import-sync", methods=["POST"])
def meta_import_sync():
    token = request.headers.get("x-admin-token", "")
    expected = os.environ.get("ADMIN_TOKEN", "")
    if not (expected and token == expected):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    db = _db()
    pt = _page_token()
    page_id = os.environ.get("META_PAGE_ID", "")
    if not pt or not page_id:
        return jsonify({"ok": False, "error": f"Missing: page_token={bool(pt)} page_id={bool(page_id)}"}), 400
    new_c, dup, errors = 0, 0, []
    try:
        def _ga(url, params):
            results = []
            while url:
                r = requests.get(url, params=params, timeout=15)
                if not r.ok:
                    errors.append(f"HTTP {r.status_code}: {r.text[:100]}")
                    break
                d = r.json()
                if "error" in d:
                    errors.append(str(d["error"])[:200])
                    break
                results.extend(d.get("data", []))
                url = d.get("paging", {}).get("next")
                params = {}
            return results
        forms = _ga(f"{GRAPH}/{page_id}/leadgen_forms",
                    {"access_token": pt, "fields": "id,name,leads_count"})
        for form in forms:
            leads = _ga(f"{GRAPH}/{form['id']}/leads",
                        {"access_token": pt, "fields": "id,created_time,field_data,ad_name,ad_id,adset_id,campaign_name,campaign_id"})
            for lead in leads:
                fields = {f["name"].lower(): (f.get("values") or [""])[0] for f in lead.get("field_data", [])}
                name  = fields.get("full_name") or fields.get("nombre") or fields.get("name") or "Lead Meta"
                phone = fields.get("phone_number") or fields.get("telefono") or fields.get("phone") or ""
                email = fields.get("email") or fields.get("correo") or ""
                city  = fields.get("city") or fields.get("ciudad") or ""
                ct = lead.get("created_time", "")
                if ct:
                    try:
                        from datetime import datetime, timezone as tz
                        ct = datetime.fromisoformat(ct.replace("+0000","")).replace(tzinfo=tz.utc).strftime("%Y-%m-%d %H:%M:%S")
                    except Exception as e2:
                        errors.append(f"date: {e2}"); ct = ""
                biz_id = _importar_formulario(db, lead, fields, {
                    "name": name, "phone": phone or None, "email": email or None, "city": city or None,
                    "category": "Meta Lead Ad", "status": "scraped",
                    "notes": f"Meta Lead Ad · {lead.get('campaign_name') or form.get('name','')}".strip(" ·"),
                    "score": 70, "source": "meta",
                    "form_data": json.dumps(fields, ensure_ascii=False),
                    "scraped_at": ct or None,
                })
                if biz_id: new_c += 1
                else: dup += 1  # import masivo: solo se cuenta (ver meta_import_leads)
    except Exception as e:
        errors.append(_redact_secrets(str(e)))
    return jsonify({"ok": True, "new": new_c, "dup": dup, "forms": len(forms) if "forms" in dir() else 0, "errors": errors})


def _update_env(key: str, value: str):
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    try:
        lines = open(env_path).readlines()
        found = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}\n"
                found = True
                break
        if not found:
            lines.append(f"{key}={value}\n")
        with open(env_path, "w") as f:
            f.writelines(lines)
        os.environ[key] = value
    except Exception as e:
        logger.warning(f"Could not update .env: {e}")


# ── Background token health monitor ──────────────────────────────────────────

# Cada cuánto se revisa que el token de página siga vivo.
_CHECK_INTERVAL = 10 * 60  # 10 minutos

# Cada cuánto corre el import de respaldo contra Graph. Es el colchón por si
# el webhook se cae; no hace falta más seguido, y compartía constante con el
# monitor de token, así que el "import diario" corría cada 10 minutos.
_IMPORT_INTERVAL = 24 * 60 * 60  # 24 horas

# Cada cuánto se repite el mail de "token vencido" mientras el mismo
# incidente sigue vivo. Ni cada _CHECK_INTERVAL (spam: ~288 mails/día por
# admin, y de paso revientan la cuota de Resend que usa todo el CRM) ni una
# sola vez (un token vencido un fin de semana largo queda sin nadie avisado).
_TOKEN_ALERT_RESEND_INTERVAL = 6 * 60 * 60  # 6 horas → 4 mails/día por admin mientras dure


def _token_alert_key(err: dict) -> str:
    """Identifica el *tipo* de falla, no el mensaje textual completo.

    Dos chequeos seguidos del mismo token vencido traen el mismo
    `code`/`error_subcode` de Meta: eso es "lo mismo" y no debe repetir el
    mail. Un `code`/`error_subcode` distinto (por ejemplo revocado en vez de
    vencido) es un incidente nuevo y tiene que avisar aunque el anterior
    siga silenciado.
    """
    return f"{err.get('code', '')}:{err.get('error_subcode', '')}"


def _should_send_token_alert(db: str, alert_key: str, detail: str) -> bool:
    """True si hay que mandar el mail para `alert_key`: primera vez que se ve
    ese tipo de falla, o ya pasó `_TOKEN_ALERT_RESEND_INTERVAL` desde el
    último envío. Registra el intento en `meta_token_alerts` (misma base que
    `leads.db`, montada en /data en Fly), así el silencio sobrevive a un
    restart o redeploy en vez de vivir en memoria del proceso.

    Si la tabla no está disponible por lo que sea, se manda igual: ante la
    duda, un mail de más es preferible a un incidente real que quede mudo.
    """
    now = time.time()
    try:
        conn = _sq_meta.connect(db)
        row = conn.execute(
            "SELECT last_sent_at FROM meta_token_alerts WHERE alert_key = ?",
            (alert_key,),
        ).fetchone()
        if row and (now - row[0]) < _TOKEN_ALERT_RESEND_INTERVAL:
            conn.close()
            return False
        conn.execute(
            """
            INSERT INTO meta_token_alerts (alert_key, detail, last_sent_at)
            VALUES (?, ?, ?)
            ON CONFLICT(alert_key) DO UPDATE SET detail = excluded.detail, last_sent_at = excluded.last_sent_at
            """,
            (alert_key, detail, now),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.warning(f"No se pudo evaluar el estado de la alerta de token, se avisa igual: {e}")
        return True


def _check_token_once(db: str) -> None:
    token = _page_token()  # el mismo que usa el webhook: vigilar otro no sirve
    if not token:
        return
    try:
        r = requests.get(
            f"{GRAPH}/me",
            params={"fields": "name", "access_token": token},
            timeout=10,
        )
        data = r.json()
        if "error" in data:
            err = data["error"]
            detail = f"[{err.get('code')}] {err.get('message', '')}"
            logger.error(f"Meta token invalid: {detail}")
            alert_key = _token_alert_key(err)
            if _should_send_token_alert(db, alert_key, detail):
                for email in _get_admin_emails(db):
                    send_meta_token_alert(email, detail)
            else:
                logger.info(f"Meta token alert silenciada (mismo incidente hace menos de {_TOKEN_ALERT_RESEND_INTERVAL}s): {alert_key}")
        else:
            logger.debug(f"Meta token OK — page: {data.get('name')}")
    except Exception as e:
        logger.warning(f"Meta token check failed (network?): {_redact_secrets(str(e))}")


def start_meta_token_monitor(app) -> None:
    def _loop():
        time.sleep(60)  # esperar a que la app levante
        while True:
            try:
                with app.app_context():
                    _check_token_once(app.config["DB_PATH"])
            except Exception as e:
                logger.warning(f"Token monitor error: {e}")
            time.sleep(_CHECK_INTERVAL)

    t = threading.Thread(target=_loop, daemon=True, name="meta-token-monitor")
    t.start()
    logger.info("Meta token monitor started (checks every 10 min)")


# ── Daily import cron ─────────────────────────────────────────────────────────

def _run_import_sync(db: str) -> tuple[int, int]:
    pt = _page_token()
    page_id = os.environ.get("META_PAGE_ID", "")
    if not pt or not page_id:
        logger.warning("Meta daily import: PAGE_TOKEN or PAGE_ID not set")
        return 0, 0

    new_c, dup = 0, 0

    def _ga(url, params):
        results = []
        while url:
            r = requests.get(url, params=params, timeout=15)
            if not r.ok:
                break
            d = r.json()
            if "error" in d:
                logger.error(f"Meta API error during import: {d['error']}")
                break
            results.extend(d.get("data", []))
            url = d.get("paging", {}).get("next")
            params = {}
        return results

    forms = _ga(
        f"{GRAPH}/{page_id}/leadgen_forms",
        {"access_token": pt, "fields": "id,name,leads_count"},
    )
    for form in forms:
        leads = _ga(
            f"{GRAPH}/{form['id']}/leads",
            {"access_token": pt, "fields": "id,created_time,field_data,ad_name,ad_id,adset_id,campaign_name,campaign_id"},
        )
        for lead in leads:
            fields = {f["name"].lower(): (f.get("values") or [""])[0] for f in lead.get("field_data", [])}
            name  = fields.get("full_name") or fields.get("nombre") or fields.get("name") or "Lead Meta"
            phone = fields.get("phone_number") or fields.get("telefono") or fields.get("phone") or fields.get("celular") or ""
            email = fields.get("email") or fields.get("correo") or ""
            city  = fields.get("city") or fields.get("ciudad") or ""
            ct = lead.get("created_time", "")
            if ct:
                try:
                    from datetime import datetime, timezone as tz
                    ct = datetime.fromisoformat(ct.replace("+0000", "")).replace(tzinfo=tz.utc).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    ct = ""
            campaign = lead.get("campaign_name") or form.get("name", "")
            biz_id = _importar_formulario(db, lead, fields, {
                "name": name, "phone": phone or None, "email": email or None, "city": city or None,
                "category": "Meta Lead Ad", "status": "scraped",
                "notes": f"Meta Lead Ad · {campaign}".strip(" ·"),
                "score": 70, "source": "meta",
                "form_data": json.dumps(fields, ensure_ascii=False),
                "scraped_at": ct or None,
            })
            if biz_id:
                new_c += 1
                log_activity(db, "meta_daily_import", "lead_created", "lead", biz_id, name,
                             f"Fuente: Meta Lead Ad · {campaign}", user_id=None)
                threading.Thread(
                    target=_notify_new_meta_lead,
                    args=(db, name, phone, campaign, city, biz_id),
                    daemon=True,
                ).start()
            else:
                # Repaso diario de todos los formularios: la enorme mayoría de
                # estas filas ya está en la base. Se cuentan y nada más — pisar
                # el negocio existente o notificar acá repetiría el aviso una
                # vez por día, todos los días. El camino que sí lo hace, una
                # sola vez y cuando el lead llega, es el webhook.
                dup += 1

    logger.info(f"Meta daily import done: {new_c} new, {dup} dup")
    return new_c, dup


def start_meta_daily_import(app) -> None:
    def _loop():
        time.sleep(120)  # esperar a que la app levante
        while True:
            try:
                with app.app_context():
                    _run_import_sync(app.config["DB_PATH"])
            except Exception as e:
                logger.warning(f"Meta daily import error: {_redact_secrets(str(e))}")
            time.sleep(_IMPORT_INTERVAL)

    t = threading.Thread(target=_loop, daemon=True, name="meta-daily-import")
    t.start()
    logger.info("Meta daily import started (runs every 24h)")
