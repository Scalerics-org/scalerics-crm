"""Alertas diarias de la pauta de Meta, por mail.

Etapa 1 del agente de marketing: mirar y avisar, sin tocar nada en Meta. Una vez
por dia, despues de las 8 de Montevideo, compara los ultimos 7 dias contra los
28 anteriores y manda un mail solo si algo se desvio. Los lunes y miercoles manda
el resumen aunque no haya alertas, y esos dias tambien a los externos.

**Semana contra mes, no ayer contra la semana.** La cuenta trae alrededor de un
lead por dia: un costo por lead diario es ruido puro. Siete dias es la ventana
mas corta donde la senal se distingue.

**Reglas fijas, sin modelo.** No gasta tokens. Cada regla pide una muestra
minima antes de disparar, para no alarmar por dos clics.

**No repite.** La misma alerta no vuelve a salir por 72 horas: una alerta que
llega todos los dias se deja de leer. La marca vive en `corridas`, igual que la
de la corrida diaria, asi que un deploy no dispara una tanda (regla 3 de
COORDINACION.md).
"""

import html
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from services.corridas import marcar_corrida, puede_correr

logger = logging.getLogger(__name__)

DESTINO_POR_DEFECTO = "contacto@scalerics.com"
# Externos: reciben solo el resumen. ALERTAS_META_RESUMEN los reemplaza ("" = nadie).
RESUMEN_POR_DEFECTO = ("andres@simondigitalgroup.com",)
DIAS_DE_RESUMEN = (0, 2)  # lunes y miercoles

# Uruguay no tiene horario de verano desde 2015: el offset fijo alcanza y evita
# depender de que la imagen traiga la base de zonas horarias.
_UY = timezone(timedelta(hours=-3))
HORA_DE_ENVIO = 8
_CADA_CUANTO_MIRAR_S = 30 * 60
_DEMORA_ARRANQUE_S = 240

DIAS_RECIENTES = 7
DIAS_REFERENCIA = 28
_NO_REPETIR_HORAS = 72

_JOB = "alertas_meta"
_JOB_MANUAL = "alertas_meta_manual"
_JOB_ERROR = "alertas_meta_error"

# Umbrales. Un "1.3" es "30% peor que el mes de referencia".
SUBA_COSTO_POR_LEAD = 1.3
CAIDA_CONVERSION = 0.6
CAIDA_CTR = 0.75
SUBA_CPM = 1.3
SUBA_GASTO = 1.3
FRECUENCIA_MAXIMA = 3.0
DIAS_SIN_PUBLICAR = 5

# Muestras minimas para que una regla pueda disparar.
MIN_LEADS_REFERENCIA = 5
MIN_CLICS = 50
MIN_IMPRESIONES = 5000
MIN_GASTO_ANUNCIO = 20.0

_ACCION_LEAD = "lead"


class ErrorDeMeta(RuntimeError):
    pass


@dataclass
class Alerta:
    clave: str
    titulo: str
    detalle: str
    que_hacer: str


def activo() -> bool:
    return os.environ.get("ALERTAS_META", "").strip().lower() != "off"


def destino() -> str:
    return os.environ.get("ALERTAS_META_EMAIL", "").strip() or DESTINO_POR_DEFECTO


def destinos_resumen() -> list[str]:
    """Quienes reciben solo el resumen de los lunes y miercoles, sin el boton al CRM."""
    crudo = os.environ.get("ALERTAS_META_RESUMEN")
    if crudo is None:
        return list(RESUMEN_POR_DEFECTO)
    return [m.strip() for m in crudo.split(",") if m.strip()]


# ── datos ────────────────────────────────────────────────────────────────────

def _get(ruta: str, **params) -> dict:
    import requests

    from meta_config import GRAPH

    params["access_token"] = os.environ["META_ADS_TOKEN"]
    r = requests.get(f"{GRAPH}/{ruta}", params=params, timeout=30)
    try:
        d = r.json()
    except ValueError:
        raise ErrorDeMeta(f"respuesta ilegible (HTTP {r.status_code})")
    if "error" in d or not r.ok:
        e = d.get("error") or {}
        raise ErrorDeMeta(f"code={e.get('code')} {e.get('message') or ''}"[:200])
    return d


def _insights(nivel: str, desde: date, hasta: date, campos: str) -> list:
    d = _get(f"{os.environ['META_AD_ACCOUNT_ID']}/insights",
             level=nivel, fields=campos, limit=500,
             time_range=json.dumps({"since": desde.isoformat(),
                                    "until": hasta.isoformat()}))
    return d.get("data", [])


def _ultimo_post() -> str | None:
    pagina = os.environ.get("META_PAGE_ID", "")
    if not pagina:
        return None
    d = _get(pagina, fields="instagram_business_account{media.limit(1){timestamp}}")
    media = ((d.get("instagram_business_account") or {}).get("media") or {}).get("data") or []
    return media[0].get("timestamp") if media else None


def ventanas(hoy: date) -> dict:
    fin_rec = hoy - timedelta(days=1)
    ini_rec = hoy - timedelta(days=DIAS_RECIENTES)
    fin_ref = ini_rec - timedelta(days=1)
    ini_ref = ini_rec - timedelta(days=DIAS_REFERENCIA)
    return {"reciente": (ini_rec, fin_rec), "referencia": (ini_ref, fin_ref)}


_CAMPOS = "spend,impressions,inline_link_clicks,frequency,actions,account_currency"


def traer_datos(hoy: date) -> dict:
    """Todo lo que miran las reglas, en cinco llamadas a Meta."""
    v = ventanas(hoy)
    rec = _insights("account", *v["reciente"], _CAMPOS)
    ref = _insights("account", *v["referencia"], _CAMPOS)
    anuncios = _insights("ad", *v["reciente"], "ad_id,ad_name,campaign_name," + _CAMPOS)
    try:
        ultimo = _ultimo_post()
    except ErrorDeMeta as e:
        # Sin Instagram las alertas de pauta valen igual.
        logger.warning(f"Alertas Meta: no se pudo leer Instagram ({e})")
        ultimo = None
    return {
        "reciente": rec[0] if rec else {},
        "referencia": ref[0] if ref else {},
        "anuncios": anuncios,
        "ultimo_post": ultimo,
    }


# ── metricas ─────────────────────────────────────────────────────────────────

def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _leads(fila: dict) -> int:
    for a in fila.get("actions") or []:
        if a.get("action_type") == _ACCION_LEAD:
            return int(_num(a.get("value")))
    return 0


def _div(a: float, b: float):
    return a / b if b else None


def resumir(fila: dict) -> dict:
    gasto = _num(fila.get("spend"))
    imp = _num(fila.get("impressions"))
    clics = _num(fila.get("inline_link_clicks"))
    leads = _leads(fila)
    return {
        "gasto": gasto,
        "impresiones": imp,
        "clics": clics,
        "leads": leads,
        "frecuencia": _num(fila.get("frequency")),
        "moneda": fila.get("account_currency") or "USD",
        "cpl": _div(gasto, leads),
        "cpm": _div(gasto * 1000, imp),
        "ctr": _div(clics, imp),
        "conversion": _div(leads, clics),
    }


def _plata(v, moneda="USD") -> str:
    if v is None:
        return "—"
    return f"{moneda} {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(v) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.1f}%".replace(".", ",")


def _decimal(v: float) -> str:
    return f"{v:.1f}".replace(".", ",")


def _veces(a, b) -> str:
    return _decimal(a / b) if a and b else "—"


# ── reglas ───────────────────────────────────────────────────────────────────

def evaluar(datos: dict, hoy: date) -> list[Alerta]:
    r = resumir(datos.get("reciente") or {})
    f = resumir(datos.get("referencia") or {})
    m = r["moneda"]
    alertas: list[Alerta] = []

    gasto_ref_semanal = f["gasto"] * DIAS_RECIENTES / DIAS_REFERENCIA
    ref_valida = f["leads"] >= MIN_LEADS_REFERENCIA and f["cpl"]

    if ref_valida and r["leads"] and r["cpl"] > SUBA_COSTO_POR_LEAD * f["cpl"]:
        alertas.append(Alerta(
            "costo_por_lead",
            "Subió el costo por lead",
            f"Esta semana cada lead costó <b>{_plata(r['cpl'], m)}</b> "
            f"({r['leads']} leads). El mes anterior costaba {_plata(f['cpl'], m)}: "
            f"{_veces(r['cpl'], f['cpl'])} veces más.",
            "Las alertas de abajo muestran en qué parte del camino se pierde.",
        ))
    if ref_valida and not r["leads"] and r["gasto"] >= 2 * f["cpl"]:
        alertas.append(Alerta(
            "sin_leads",
            "Una semana entera sin leads",
            f"Se gastaron <b>{_plata(r['gasto'], m)}</b> en 7 días y no entró ningún lead.",
            "Revisar si cambió algo en las campañas o en el formulario.",
        ))
    if (r["clics"] >= MIN_CLICS and f["conversion"]
            and r["conversion"] is not None
            and r["conversion"] < CAIDA_CONVERSION * f["conversion"]):
        alertas.append(Alerta(
            "conversion",
            "La gente hace clic pero no deja sus datos",
            f"De cada 100 que hacen clic, <b>{_pct(r['conversion'])}</b> completan el "
            f"formulario. El mes anterior eran {_pct(f['conversion'])}.",
            "El problema está después del clic: el formulario, la página o el público. "
            "Revisar si se cambió el formulario o el objetivo de las campañas.",
        ))
    if (r["impresiones"] >= MIN_IMPRESIONES and f["ctr"]
            and r["ctr"] is not None and r["ctr"] < CAIDA_CTR * f["ctr"]):
        alertas.append(Alerta(
            "ctr",
            "Los anuncios llaman menos la atención",
            f"De cada 100 personas que ven un anuncio, <b>{_pct(r['ctr'])}</b> hacen clic. "
            f"El mes anterior eran {_pct(f['ctr'])}.",
            "Hace falta renovar los anuncios: imágenes, videos o textos nuevos.",
        ))
    if (r["impresiones"] >= MIN_IMPRESIONES and f["cpm"]
            and r["cpm"] > SUBA_CPM * f["cpm"]):
        alertas.append(Alerta(
            "cpm",
            "Meta cobra más caro mostrar los anuncios",
            f"Cada 1.000 personas alcanzadas cuestan <b>{_plata(r['cpm'], m)}</b>. "
            f"El mes anterior, {_plata(f['cpm'], m)}.",
            "Puede ser competencia de temporada o un público demasiado chico.",
        ))
    if gasto_ref_semanal and r["gasto"] > SUBA_GASTO * gasto_ref_semanal:
        alertas.append(Alerta(
            "gasto",
            "Subió el gasto semanal",
            f"Esta semana se gastaron <b>{_plata(r['gasto'], m)}</b>. El promedio "
            f"semanal del mes anterior era {_plata(gasto_ref_semanal, m)}.",
            "Confirmar que el aumento de presupuesto fue a propósito.",
        ))
    if r["frecuencia"] > FRECUENCIA_MAXIMA:
        alertas.append(Alerta(
            "frecuencia",
            "El público ya vio demasiado los anuncios",
            f"Cada persona vio los anuncios <b>{_decimal(r['frecuencia'])}</b> veces "
            "en la semana.",
            "Anuncios gastados: hay que cambiarlos o ampliar el público.",
        ))

    tope_anuncio = max(1.5 * f["cpl"], MIN_GASTO_ANUNCIO) if ref_valida else None
    if tope_anuncio:
        for fila in datos.get("anuncios") or []:
            a = resumir(fila)
            if a["leads"] == 0 and a["gasto"] >= tope_anuncio:
                nombre = html.escape(fila.get("ad_name") or "sin nombre")
                campana = html.escape(fila.get("campaign_name") or "")
                alertas.append(Alerta(
                    f"anuncio:{fila.get('ad_id')}",
                    f"Anuncio que gasta sin traer leads: {nombre}",
                    f"Gastó <b>{_plata(a['gasto'], m)}</b> en 7 días con 0 leads "
                    f"(campaña {campana}).",
                    "Si no es un anuncio de marca o de alcance a propósito, conviene pausarlo.",
                ))

    ultimo = datos.get("ultimo_post")
    if ultimo:
        dias = (hoy - datetime.fromisoformat(ultimo.replace("+0000", "+00:00"))
                .astimezone(_UY).date()).days
        if dias >= DIAS_SIN_PUBLICAR:
            alertas.append(Alerta(
                "instagram",
                "Instagram sin publicaciones",
                f"La última publicación de @scalerics_ fue hace <b>{dias} días</b>.",
                "Hace falta contenido para esta semana.",
            ))
    return alertas


# ── mail ─────────────────────────────────────────────────────────────────────

def _tabla(datos: dict) -> str:
    r = resumir(datos.get("reciente") or {})
    f = resumir(datos.get("referencia") or {})
    m = r["moneda"]
    sem = DIAS_RECIENTES / DIAS_REFERENCIA
    filas = [
        ("Gasto", _plata(r["gasto"], m), _plata(f["gasto"] * sem, m)),
        ("Leads", str(r["leads"]), _decimal(f["leads"] * sem)),
        ("Costo por lead", _plata(r["cpl"], m), _plata(f["cpl"], m)),
        ("Hacen clic (de cada 100 que ven)", _pct(r["ctr"]), _pct(f["ctr"])),
        ("Dejan sus datos (de cada 100 clics)", _pct(r["conversion"]), _pct(f["conversion"])),
        ("Costo por 1.000 vistas", _plata(r["cpm"], m), _plata(f["cpm"], m)),
    ]
    td = 'style="padding:6px 8px;font-size:13px;color:#1c2b40;border-top:1px solid #e2e8f0"'
    th = ('style="padding:6px 8px;font-size:11px;color:#94a3b8;text-transform:uppercase;'
          'text-align:left"')
    cuerpo = "".join(
        f"<tr><td {td}>{a}</td><td {td}><b>{b}</b></td><td {td}>{c}</td></tr>"
        for a, b, c in filas
    )
    return (
        '<table width="100%" cellpadding="0" cellspacing="0" '
        'style="border:1px solid #e2e8f0;border-radius:8px;margin-bottom:24px">'
        f"<tr><th {th}></th><th {th}>Últimos 7 días</th>"
        f"<th {th}>Semana promedio del mes anterior</th></tr>"
        f"{cuerpo}</table>"
    )


def _bloque(a: Alerta) -> str:
    return (
        '<div style="border-left:4px solid #f59e0b;background:#fffbeb;padding:12px 16px;'
        'margin-bottom:14px;border-radius:4px">'
        f'<p style="margin:0 0 4px;font-size:15px;font-weight:700;color:#0f1f3d">{a.titulo}</p>'
        f'<p style="margin:0 0 6px;font-size:13.5px;color:#1c2b40;line-height:1.5">{a.detalle}</p>'
        f'<p style="margin:0;font-size:13px;color:#5a6a82"><b>Qué hacer:</b> {a.que_hacer}</p>'
        "</div>"
    )


def componer(datos: dict, alertas: list[Alerta]) -> tuple[str, str]:
    """(asunto, cuerpo_html) sin el layout."""
    if alertas:
        asunto = (f"Meta Ads: {len(alertas)} alerta" + ("s" if len(alertas) > 1 else "")
                  + f" — {alertas[0].titulo}")
        intro = "Esto se salió de lo normal en la última semana:"
        bloques = "".join(_bloque(a) for a in alertas)
    else:
        asunto = "Meta Ads: resumen, sin alertas"
        intro = "Nada se salió de lo normal esta semana."
        bloques = ""
    cuerpo = (
        f'<p style="margin:0 0 16px;font-size:14px;color:#1c2b40">{intro}</p>'
        + bloques
        + '<p style="margin:16px 0 8px;font-size:12px;font-weight:700;color:#94a3b8;'
          'text-transform:uppercase;letter-spacing:.06em">Los números</p>'
        + _tabla(datos)
        + '<p style="margin:0;font-size:12px;color:#94a3b8">Este mail solo lee datos de Meta: '
          "no cambia nada en las campañas. Una alerta no se repite antes de 3 días.</p>"
    )
    return asunto, cuerpo


# ── corrida ──────────────────────────────────────────────────────────────────

def corrida(db_path: str, ahora: datetime | None = None, traer=None,
            enviar=None, forzar: bool = False) -> dict:
    """Mira, decide y manda. `traer` y `enviar` existen para los tests."""
    from services.email_service import send_alertas_meta, send_alertas_meta_error
    from services.meta_insights import hay_credenciales

    ahora = (ahora or datetime.now(timezone.utc)).astimezone(_UY)
    hoy = ahora.date()

    if not forzar:
        if ahora.hour < HORA_DE_ENVIO:
            return {"estado": "temprano"}
        if not puede_correr(db_path, _JOB):
            return {"estado": "ya_corrio"}
    if traer is None:
        if not hay_credenciales():
            return {"estado": "sin_credenciales"}
        traer = traer_datos
    enviar = enviar or send_alertas_meta

    try:
        datos = traer(hoy)
    except Exception as e:
        logger.warning(f"Alertas Meta: no se pudieron leer los datos ({e})")
        if puede_correr(db_path, _JOB_ERROR, 24):
            send_alertas_meta_error(destino(), str(e))
            marcar_corrida(db_path, _JOB_ERROR)
        if not forzar:
            marcar_corrida(db_path, _JOB)
        return {"estado": "error", "error": str(e)}

    todas = evaluar(datos, hoy)
    es_resumen = hoy.weekday() in DIAS_DE_RESUMEN
    if forzar or es_resumen:
        nuevas = todas
    else:
        nuevas = [a for a in todas
                  if puede_correr(db_path, f"{_JOB}:{a.clave}", _NO_REPETIR_HORAS)]

    marcar_corrida(db_path, _JOB)
    if not nuevas and not (forzar or es_resumen):
        return {"estado": "sin_novedades", "alertas": len(todas)}

    asunto, cuerpo = componer(datos, nuevas)
    ok = enviar(destino(), asunto, cuerpo)
    if ok:
        for a in nuevas:
            marcar_corrida(db_path, f"{_JOB}:{a.clave}")

    externos = []
    if es_resumen and not forzar:
        for mail in destinos_resumen():
            if enviar(mail, asunto, cuerpo, con_boton=False):
                externos.append(mail)
    return {"estado": "enviado" if ok else "fallo_envio",
            "alertas": [a.clave for a in nuevas], "resumen_a": externos}


def enviar_ahora(db_path: str) -> dict:
    """La corrida a pedido, con su propia pausa para que no sea un botón de spam."""
    if not puede_correr(db_path, _JOB_MANUAL, 0.25):
        return {"estado": "esperar", "error": "Se mandó hace menos de 15 minutos."}
    marcar_corrida(db_path, _JOB_MANUAL)
    return corrida(db_path, forzar=True)


def start_alertas_meta(app) -> None:
    """Mira cada media hora; `corrida` decide si es hora y si ya se mandó hoy."""
    if not activo():
        logger.info("Alertas de Meta apagadas por ALERTAS_META=off")
        return

    def _loop():
        time.sleep(_DEMORA_ARRANQUE_S)
        while True:
            try:
                corrida(app.config["DB_PATH"])
            except Exception as e:
                logger.warning(f"Alertas Meta: {e}")
            time.sleep(_CADA_CUANTO_MIRAR_S)

    threading.Thread(target=_loop, daemon=True, name="alertas-meta").start()
    logger.info(f"Alertas de Meta ACTIVAS: una por dia desde las {HORA_DE_ENVIO} "
                f"de Montevideo, a {destino()}")
