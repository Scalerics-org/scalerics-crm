"""Modo sombra: que haria el agente con la pauta, sin tocar nada en Meta.

Etapa 3 del agente de marketing. Los lunes desde las 9 de Montevideo:

1. Evalua las recomendaciones de la semana anterior contra lo que paso de
   verdad: si el de marketing hizo algo equivalente (se compara el estado de
   anuncios y presupuestos) y como le fue a ese anuncio o campana despues.
2. Arma las recomendaciones nuevas con reglas fijas sobre los ultimos 7 dias,
   comparados con los 28 anteriores.
3. No manda mails: se ve en el panel `sombra` ("Recomendaciones de pauta").
   Las recomendaciones las ve marketing; la evaluacion, solo el administrador.

**Solo lectura.** Usa `ads_read`; no hay ninguna llamada que escriba en Meta.

**Los veredictos son prudentes.** Con un lead por dia, una semana es poca
muestra: cuando los numeros no alcanzan para decidir, el veredicto es
"sin_definir" y se dice.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from database import _connect
from services import alertas_meta as am
from services.corridas import marcar_corrida, puede_correr

logger = logging.getLogger(__name__)

_UY = am._UY
_JOB = "sombra_meta"
DIA, HORA = 0, 9            # lunes 9:00
MIN_GASTO = 20.0
MIN_LEADS_ESCALAR = 3
BARATO = 0.7                # CPL <= 70% del de referencia
CARO = 1.5
FRECUENCIA_ALTA = 3.0
CTR_BAJO = 0.5
MIN_IMPRESIONES_CTR = 3000
OBJETIVOS_DE_LEADS = ("OUTCOME_LEADS", "LEAD_GENERATION")
OBJETIVOS = {
    "OUTCOME_AWARENESS": "reconocimiento de marca",
    "OUTCOME_ENGAGEMENT": "interacción",
    "OUTCOME_TRAFFIC": "tráfico",
    "OUTCOME_SALES": "ventas",
    "OUTCOME_APP_PROMOTION": "promoción de app",
}

TIPOS = {
    "pausar": "Pausar anuncio",
    "renovar": "Renovar creatividad",
    "escalar": "Subir presupuesto",
    "bajar": "Bajar presupuesto",
    "confirmar": "Confirmar objetivo",
    "formulario": "Revisar el formulario",
}

# Formulario de leads: se recomienda revisarlo cuando llegan bastante menos leads
# que antes pero los que llegan siguen avanzando (o sea, no es un problema de calidad).
MIN_LEADS_FORMULARIO = 20
CAIDA_FORMULARIO = 0.7      # leads de los ultimos 28 dias <= 70% de los 28 anteriores
AVANCE_FORMULARIO = 0.4     # y al menos 40% de esos leads paso de "sin contactar"
ESTADOS_QUE_AVANZAN = ("interesado", "demo_1", "demo_agendada", "presupuesto_enviado",
                       "follow_up_1", "en_desarrollo", "finalizado")


def activo() -> bool:
    return os.environ.get("SOMBRA_META", "").strip().lower() != "off"


# ── datos ────────────────────────────────────────────────────────────────────

def _todo(ruta: str, **params) -> list:
    """GET paginado. `am._get` valida errores y pone el token."""
    salida = []
    d = am._get(ruta, **params)
    while True:
        salida.extend(d.get("data", []))
        siguiente = (d.get("paging") or {}).get("next")
        if not siguiente:
            return salida
        import requests
        r = requests.get(siguiente, timeout=60)
        d = r.json()
        if "error" in d:
            raise am.ErrorDeMeta(str(d["error"].get("message"))[:200])


def traer_datos(hoy) -> dict:
    cuenta = os.environ["META_AD_ACCOUNT_ID"]
    v = am.ventanas(hoy)
    rango = json.dumps({"since": v["reciente"][0].isoformat(), "until": v["reciente"][1].isoformat()})
    campos = "spend,impressions,inline_link_clicks,frequency,actions"
    return {
        "referencia": (am._insights("account", *v["referencia"], am._CAMPOS) or [{}])[0],
        "anuncios_7d": _todo(f"{cuenta}/insights", level="ad", time_range=rango, limit=500,
                             fields="ad_id,ad_name,adset_id,campaign_id,campaign_name," + campos),
        "campanas_7d": _todo(f"{cuenta}/insights", level="campaign", time_range=rango, limit=500,
                             fields="campaign_id,campaign_name," + campos),
        "anuncios": _todo(f"{cuenta}/ads", limit=500,
                          fields="id,name,effective_status,adset_id,campaign_id,created_time"),
        "campanas": _todo(f"{cuenta}/campaigns", limit=500,
                          fields="id,name,objective,effective_status,daily_budget,lifetime_budget"),
        "conjuntos": _todo(f"{cuenta}/adsets", limit=500,
                           fields="id,campaign_id,effective_status,daily_budget,lifetime_budget"),
    }


def presupuesto_diario(datos: dict, campana_id: str) -> float | None:
    """En USD. Presupuesto de campana o suma de conjuntos activos."""
    for c in datos.get("campanas", []):
        if c["id"] == campana_id and c.get("daily_budget"):
            return int(c["daily_budget"]) / 100
    total = sum(int(a.get("daily_budget") or 0) for a in datos.get("conjuntos", [])
                if a.get("campaign_id") == campana_id and a.get("effective_status") == "ACTIVE")
    return total / 100 if total else None


def _estado_anuncio(datos, ad_id):
    return next((a for a in datos.get("anuncios", []) if a["id"] == ad_id), None)


def _campana(datos, campana_id):
    return next((c for c in datos.get("campanas", []) if c["id"] == campana_id), None)


# ── recomendaciones ──────────────────────────────────────────────────────────

TOPE_CPL_INICIAL = 25.0   # fijado por Juan el 21/9/2026; se edita desde el panel


def tope_cpl(db_path: str) -> float | None:
    """El tope de costo por lead (USD): el que fijaron Juan o el de marketing, el inicial si
    nadie lo tocó, o None si lo vaciaron (se usa el promedio de la cuenta)."""
    conn = _connect(db_path)
    try:
        fila = conn.execute("SELECT valor FROM sombra_ajustes WHERE clave = 'cpl_tope'").fetchone()
    finally:
        conn.close()
    if fila is None:
        return TOPE_CPL_INICIAL        # nadie lo tocó todavía
    try:
        valor = float(fila["valor"]) if fila and fila["valor"] else None
    except ValueError:
        return None
    return valor if valor and valor > 0 else None


def fijar_tope_cpl(db_path: str, valor: float | None, usuario: str) -> None:
    """Guarda el tope; `None` lo borra y se vuelve al promedio de la cuenta."""
    conn = _connect(db_path)
    try:
        conn.execute("INSERT INTO sombra_ajustes (clave, valor, actualizado_por, actualizado_en) "
                     "VALUES ('cpl_tope', ?, ?, ?) ON CONFLICT(clave) DO UPDATE SET "
                     "valor = excluded.valor, actualizado_por = excluded.actualizado_por, "
                     "actualizado_en = excluded.actualizado_en",
                     (None if valor is None else str(valor), usuario,
                      datetime.now(_UY).strftime("%Y-%m-%d %H:%M")))
        conn.commit()
    finally:
        conn.close()


def recomendar(datos: dict, cpl_tope: float | None = None) -> list[dict]:
    """Con `cpl_tope` el costo por lead se compara contra ese tope; sin el, contra
    el promedio de los 28 dias anteriores de la cuenta."""
    ref = am.resumir(datos.get("referencia") or {})
    cpl_ref, ctr_ref = cpl_tope or ref["cpl"], ref["ctr"]
    del_ref = "del tope" if cpl_tope else "del promedio"
    m = ref["moneda"]
    recs = []
    activos = {a["id"] for a in datos.get("anuncios", []) if a.get("effective_status") == "ACTIVE"}
    objetivos = {c["id"]: c.get("objective") for c in datos.get("campanas", [])}

    for fila in datos.get("anuncios_7d", []):
        ad_id = fila.get("ad_id")
        if ad_id not in activos:
            continue
        a = am.resumir(fila)
        nombre = fila.get("ad_name") or ad_id
        de_leads = objetivos.get(fila.get("campaign_id")) in OBJETIVOS_DE_LEADS
        base = {"objeto_id": ad_id, "objeto_nombre": nombre, "campana_id": fila.get("campaign_id"),
                "campana_nombre": fila.get("campaign_name"),
                "antes": {"gasto": a["gasto"], "leads": a["leads"], "cpl": a["cpl"],
                          "cpl_ref": cpl_ref}}
        if de_leads and cpl_ref and a["leads"] == 0 and a["gasto"] >= max(CARO * cpl_ref, MIN_GASTO):
            recs.append({**base, "clave": f"pausar:{ad_id}", "tipo": "pausar",
                         "evidencia": f"Gastó {am._plata(a['gasto'], m)} en 7 días sin traer leads "
                                      f"(un lead cuesta {'como máximo' if cpl_tope else 'en promedio'} {am._plata(cpl_ref, m)})."})
            continue
        # Pocos clics es normal en una campana de alcance: el CTR solo cuenta en las de leads.
        if a["frecuencia"] > FRECUENCIA_ALTA or (
                de_leads and a["impresiones"] >= MIN_IMPRESIONES_CTR and ctr_ref
                and a["ctr"] is not None and a["ctr"] < CTR_BAJO * ctr_ref):
            motivo = (f"la misma persona lo vio {am._decimal(a['frecuencia'])} veces"
                      if a["frecuencia"] > FRECUENCIA_ALTA else
                      f"hacen clic {am._pct(a['ctr'])} de los que lo ven, contra "
                      f"{am._pct(ctr_ref)} de la cuenta")
            recs.append({**base, "clave": f"renovar:{ad_id}", "tipo": "renovar",
                         "evidencia": f"Anuncio gastado: {motivo}."})

    for fila in datos.get("campanas_7d", []):
        cid = fila.get("campaign_id")
        camp = _campana(datos, cid)
        if not camp or camp.get("effective_status") != "ACTIVE":
            continue
        c = am.resumir(fila)
        nombre = fila.get("campaign_name") or cid
        presupuesto = presupuesto_diario(datos, cid)
        base = {"objeto_id": cid, "objeto_nombre": nombre, "campana_id": cid,
                "campana_nombre": nombre,
                "antes": {"gasto": c["gasto"], "leads": c["leads"], "cpl": c["cpl"],
                          "cpl_ref": cpl_ref, "presupuesto": presupuesto}}
        if camp.get("objective") not in OBJETIVOS_DE_LEADS:
            if c["gasto"] >= MIN_GASTO:
                recs.append({**base, "clave": f"confirmar:{cid}", "tipo": "confirmar",
                             "evidencia": f"Gastó {am._plata(c['gasto'], m)} en 7 días con un objetivo "
                                          f"que no busca leads ({OBJETIVOS.get(camp.get('objective'), camp.get('objective'))}). "
                                          "Conviene confirmar que sea a propósito."})
            continue
        if not cpl_ref or presupuesto is None:
            continue
        if c["leads"] >= MIN_LEADS_ESCALAR and c["cpl"] <= BARATO * cpl_ref:
            recs.append({**base, "clave": f"escalar:{cid}", "tipo": "escalar",
                         "evidencia": f"Trajo {c['leads']} leads a {am._plata(c['cpl'], m)} cada uno, "
                                      f"por debajo {del_ref} ({am._plata(cpl_ref, m)}). Subir el "
                                      f"presupuesto un 20%: de {am._plata(presupuesto, m)} a "
                                      f"{am._plata(presupuesto * 1.2, m)} por día."})
        elif c["gasto"] >= MIN_GASTO and (c["leads"] == 0 or c["cpl"] >= CARO * cpl_ref):
            costo = am._plata(c["cpl"], m) if c["leads"] else "sin leads"
            recs.append({**base, "clave": f"bajar:{cid}", "tipo": "bajar",
                         "evidencia": f"Gastó {am._plata(c['gasto'], m)} en 7 días ({costo}), contra "
                                      f"{am._plata(cpl_ref, m)} {'de tope' if cpl_tope else 'de promedio'}. Bajar el presupuesto un 20% "
                                      "mientras se revisan los anuncios."})
    return recs


def recomendar_formulario(db_path: str, hoy) -> list[dict]:
    """Recomendacion de revisar el formulario de Meta, con los leads del CRM.

    Solo lectura y solo una sugerencia: no propone sacar preguntas. El CRM ve a
    los que terminaron el formulario; cuantos lo abandonan se ve en Meta.
    """
    reciente = (hoy - timedelta(days=28)).isoformat()
    anterior = (hoy - timedelta(days=56)).isoformat()
    marcas = ",".join("?" * len(ESTADOS_QUE_AVANZAN))
    conn = _connect(db_path)
    try:
        def contar(desde, hasta):
            fila = conn.execute(
                f"SELECT COUNT(*) n, COALESCE(SUM(crm_status IN ({marcas})), 0) a FROM businesses "
                "WHERE source = 'meta' AND form_data IS NOT NULL AND date(scraped_at) >= ? "
                "AND date(scraped_at) < ?", (*ESTADOS_QUE_AVANZAN, desde, hasta)).fetchone()
            return fila["n"], fila["a"]
        n_ahora, a_ahora = contar(reciente, (hoy + timedelta(days=1)).isoformat())
        n_antes, _ = contar(anterior, reciente)
    finally:
        conn.close()
    if n_antes < MIN_LEADS_FORMULARIO or n_ahora > CAIDA_FORMULARIO * n_antes:
        return []
    if not n_ahora or a_ahora / n_ahora < AVANCE_FORMULARIO:
        return []
    return [{"clave": "formulario", "tipo": "formulario", "objeto_id": "formulario",
             "objeto_nombre": "Formulario de leads de Meta", "campana_id": None,
             "campana_nombre": None,
             "antes": {"leads": n_ahora, "leads_antes": n_antes, "avanzan": a_ahora},
             "evidencia": (f"Llegaron {n_ahora} leads en 28 días contra {n_antes} en los 28 anteriores, "
                           f"pero {a_ahora} de los {n_ahora} avanzaron: la calidad se sostiene. "
                           "Conviene mirar en Meta cuántos abren el formulario y lo abandonan, y en qué "
                           "pregunta. No se sugiere cambiar preguntas todavía.")}]


# ── evaluacion ───────────────────────────────────────────────────────────────

def _metricas(datos, clave_tipo, objeto_id):
    lista, campo = (("anuncios_7d", "ad_id") if clave_tipo in ("pausar", "renovar")
                    else ("campanas_7d", "campaign_id"))
    fila = next((f for f in datos.get(lista, []) if f.get(campo) == objeto_id), None)
    return am.resumir(fila or {})


def evaluar(rec: dict, datos: dict) -> tuple[bool | None, str, str]:
    """(la siguio el de marketing, veredicto, detalle) una semana despues."""
    tipo, oid = rec["tipo"], rec["objeto_id"]
    antes = rec["antes"]
    if tipo == "formulario":
        return None, "sin_definir", "Es una revisión manual del formulario: no se compara contra Meta."
    ahora = _metricas(datos, tipo, oid)
    cpl_ref = antes.get("cpl_ref")
    m = "USD"

    if tipo == "pausar":
        ad = _estado_anuncio(datos, oid)
        seguida = not ad or ad.get("effective_status") != "ACTIVE" or ahora["gasto"] < MIN_GASTO / 4
        if seguida:
            return True, "coincidencia", "El anuncio se pausó o dejó de gastar."
        if ahora["leads"] == 0 and ahora["gasto"] >= MIN_GASTO / 2:
            return False, "agente", (f"Siguió activo, gastó otros {am._plata(ahora['gasto'], m)} "
                                     "y no trajo leads.")
        if ahora["leads"] and cpl_ref and ahora["cpl"] <= cpl_ref:
            return False, "marketing", (f"Siguió activo y trajo {ahora['leads']} leads a "
                                        f"{am._plata(ahora['cpl'], m)}: dejarlo fue buena decisión.")
        return False, "sin_definir", "Siguió activo; los números de la semana no alcanzan para decidir."

    if tipo == "renovar":
        ad = _estado_anuncio(datos, oid)
        desde = rec.get("creada_en", "")
        nuevos = [a for a in datos.get("anuncios", [])
                  if a.get("campaign_id") == rec.get("campana_id")
                  and (a.get("created_time") or "")[:10] >= desde[:10] and a["id"] != oid]
        seguida = bool(nuevos) or not ad or ad.get("effective_status") != "ACTIVE"
        return seguida, ("coincidencia" if seguida else "sin_definir"), (
            "Se cambió o se sumó una creatividad." if seguida else
            "El anuncio sigue igual. Se evalúa solo si se hizo algo, no el resultado.")

    presupuesto = presupuesto_diario(datos, oid)
    antes_p = antes.get("presupuesto")
    camp = _campana(datos, oid)
    pausada = not camp or camp.get("effective_status") != "ACTIVE"

    if tipo == "confirmar":
        seguida = pausada or ahora["gasto"] < MIN_GASTO / 2
        return seguida, ("coincidencia" if seguida else "sin_definir"), (
            "La campaña se pausó o dejó de gastar." if seguida else
            f"Siguió gastando ({am._plata(ahora['gasto'], m)}). Si es a propósito, está bien.")

    if tipo == "escalar":
        seguida = bool(presupuesto and antes_p and presupuesto > antes_p * 1.05)
        if seguida:
            return True, "coincidencia", (f"Se subió el presupuesto a {am._plata(presupuesto, m)} por día.")
        if ahora["leads"] and cpl_ref and ahora["cpl"] <= BARATO * cpl_ref:
            return False, "agente", (f"No se subió y siguió barata ({am._plata(ahora['cpl'], m)} por lead): "
                                     "había espacio para invertir más.")
        if ahora["leads"] and cpl_ref and ahora["cpl"] > cpl_ref:
            return False, "marketing", (f"No se subió y el costo subió a {am._plata(ahora['cpl'], m)}: "
                                        "no escalar fue prudente.")
        return False, "sin_definir", "No se subió; los números no alcanzan para decidir."

    # bajar
    seguida = pausada or bool(presupuesto and antes_p and presupuesto < antes_p * 0.95)
    if seguida:
        return True, "coincidencia", "Se bajó el presupuesto o se pausó la campaña."
    if ahora["gasto"] >= MIN_GASTO and cpl_ref and (not ahora["leads"] or ahora["cpl"] >= CARO * cpl_ref):
        costo = am._plata(ahora["cpl"], m) if ahora["leads"] else "sin leads"
        return False, "agente", f"No se bajó y siguió cara ({costo})."
    if ahora["leads"] and cpl_ref and ahora["cpl"] <= cpl_ref:
        return False, "marketing", (f"No se bajó y mejoró ({am._plata(ahora['cpl'], m)} por lead): "
                                    "sostenerla fue buena decisión.")
    return False, "sin_definir", "No se bajó; los números no alcanzan para decidir."


# ── base ─────────────────────────────────────────────────────────────────────

def _lunes(hoy):
    return hoy - timedelta(days=hoy.weekday())


def guardar(db_path: str, semana: str, recs: list[dict], ahora: datetime) -> int:
    conn = _connect(db_path)
    try:
        antes = conn.total_changes
        for r in recs:
            conn.execute(
                "INSERT OR IGNORE INTO sombra_recomendaciones (semana, clave, tipo, objeto_id, "
                "objeto_nombre, campana_id, campana_nombre, evidencia, antes_json, creada_en) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (semana, r["clave"], r["tipo"], r["objeto_id"], r["objeto_nombre"], r["campana_id"],
                 r.get("campana_nombre"), r["evidencia"], json.dumps(r["antes"]),
                 ahora.strftime("%Y-%m-%d %H:%M")))
        conn.commit()
        return conn.total_changes - antes
    finally:
        conn.close()


def evaluar_pendientes(db_path: str, semana: str, datos: dict, ahora: datetime) -> list[dict]:
    conn = _connect(db_path)
    try:
        filas = conn.execute("SELECT * FROM sombra_recomendaciones WHERE evaluada_en IS NULL "
                             "AND semana < ? ORDER BY id", (semana,)).fetchall()
        salida = []
        for f in filas:
            rec = dict(f)
            rec["antes"] = json.loads(rec.pop("antes_json"))
            seguida, veredicto, detalle = evaluar(rec, datos)
            conn.execute("UPDATE sombra_recomendaciones SET evaluada_en = ?, seguida = ?, "
                         "veredicto = ?, detalle = ? WHERE id = ?",
                         (ahora.strftime("%Y-%m-%d %H:%M"),
                          None if seguida is None else int(seguida), veredicto, detalle, rec["id"]))
            salida.append({**rec, "seguida": seguida, "veredicto": veredicto, "detalle": detalle})
        conn.commit()
        return salida
    finally:
        conn.close()


def marcador(db_path: str) -> dict:
    conn = _connect(db_path)
    try:
        filas = conn.execute("SELECT veredicto, COUNT(*) n FROM sombra_recomendaciones "
                             "WHERE veredicto IS NOT NULL GROUP BY veredicto").fetchall()
        return {f["veredicto"]: f["n"] for f in filas}
    finally:
        conn.close()


# ── corrida ──────────────────────────────────────────────────────────────────
_JOB_MANUAL = "sombra_meta_manual"


def corrida(db_path: str, ahora: datetime | None = None, traer=None, forzar: bool = False) -> dict:
    """Evalua lo pendiente y guarda las recomendaciones de la semana. Sin mails:
    se ven en el panel Modo sombra, que solo ve el administrador."""
    from services.meta_insights import hay_credenciales

    ahora = (ahora or datetime.now(timezone.utc)).astimezone(_UY)
    if not forzar:
        if ahora.weekday() != DIA or ahora.hour < HORA:
            return {"estado": "no_es_hora"}
        if not puede_correr(db_path, _JOB, 24 * 6):
            return {"estado": "ya_corrio"}
    if traer is None:
        if not hay_credenciales():
            return {"estado": "sin_credenciales"}
        traer = traer_datos
    hoy = ahora.date()
    semana = _lunes(hoy).isoformat()
    try:
        datos = traer(hoy)
    except Exception as e:
        logger.warning(f"Modo sombra: no se pudieron leer los datos ({e})")
        return {"estado": "error", "error": str(e)[:200]}
    if not forzar:
        marcar_corrida(db_path, _JOB)
    evaluadas = evaluar_pendientes(db_path, semana, datos, ahora)
    recs = recomendar(datos, tope_cpl(db_path))
    try:
        recs += recomendar_formulario(db_path, hoy)
    except Exception as e:
        logger.warning(f"Modo sombra: no se pudo revisar el formulario ({e})")
    nuevas = guardar(db_path, semana, recs, ahora)
    return {"estado": "ok", "semana": semana, "recomendaciones": len(recs),
            "nuevas": nuevas, "evaluadas": len(evaluadas)}


def calcular_ahora(db_path: str) -> dict:
    if not puede_correr(db_path, _JOB_MANUAL, 0.25):
        return {"estado": "esperar", "error": "Se calculó hace menos de 15 minutos."}
    marcar_corrida(db_path, _JOB_MANUAL)
    return corrida(db_path, forzar=True)


def semana_de(db_path: str, semana: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        filas = conn.execute("SELECT * FROM sombra_recomendaciones WHERE semana = ? "
                             "ORDER BY CASE tipo WHEN 'pausar' THEN 0 WHEN 'bajar' THEN 1 "
                             "WHEN 'escalar' THEN 2 WHEN 'renovar' THEN 3 ELSE 4 END, id",
                             (semana,)).fetchall()
    finally:
        conn.close()
    salida = []
    for f in filas:
        d = dict(f)
        d["antes"] = json.loads(d.pop("antes_json"))
        d["tipo_texto"] = TIPOS.get(d["tipo"], d["tipo"])
        salida.append(d)
    return salida


def start_sombra_meta(app) -> None:
    if not activo():
        logger.info("Modo sombra apagado por SOMBRA_META=off")
        return

    def _loop():
        time.sleep(360)
        while True:
            try:
                corrida(app.config["DB_PATH"])
            except Exception as e:
                logger.warning(f"Modo sombra: {e}")
            time.sleep(1800)

    threading.Thread(target=_loop, daemon=True, name="sombra-meta").start()
    logger.info("Modo sombra ACTIVO: recomendaciones de pauta los lunes desde las 9, sin tocar Meta")
