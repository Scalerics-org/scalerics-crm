"""El dossier: metricas calculadas, cada una auditable.

Esto es lo unico que el motor de IA va a ver. Nunca ve filas, ni nombres, ni
telefonos. Dos consecuencias: no puede inventar un numero que no este aca, y no
salen datos personales de 238 personas hacia un servicio externo.

Cada metrica trae numerador, denominador, n e intervalo. Sin eso, un 5% sobre 20
leads y un 5% sobre 2000 se leen igual en un grafico, y no son lo mismo.
"""

import logging
import math

from database import _connect

logger = logging.getLogger(__name__)

# Debajo de esto, una diferencia entre dos grupos es ruido. Con 51 leads en la
# campana de ARG, cinco puntos contra la de UY no significan nada.
MUESTRA_CHICA = 30

# `inferencia` no esta a proposito: las inferencias son del modelo y viven en el
# informe, separadas de los hechos.
FUENTES = ("meta_insights", "crm", "derivada")


def wilson(exitos: int, total: int, z: float = 1.96):
    """Intervalo de confianza de Wilson para una proporcion.

    Wilson y no el normal: con muestras chicas o proporciones cerca de 0 o de 1
    —justo el caso aca— el intervalo normal se va abajo de cero o arriba de uno
    y deja de querer decir algo. Ademas no necesita ninguna dependencia nueva.
    """
    if total <= 0:
        return (0.0, 1.0)      # sin datos no se sabe nada
    p = exitos / total
    d = 1 + z * z / total
    centro = (p + z * z / (2 * total)) / d
    margen = z / d * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return (round(max(0.0, centro - margen), 4),
            round(min(1.0, centro + margen), 4))


def metrica(id: str, etiqueta: str, valor, fuente: str, formato: str = "numero",
            numerador=None, denominador=None, anterior=None) -> dict:
    """Una metrica del dossier, con todo lo que hace falta para auditarla."""
    if fuente not in FUENTES:
        raise ValueError(
            f"fuente invalida: {fuente!r}. Tiene que ser una de {FUENTES}. "
            "Las inferencias no son metricas: van en el informe.")
    delta = None
    if anterior is not None and valor is not None:
        delta = round(valor - anterior, 4)
    return {
        "id": id,
        "etiqueta": etiqueta,
        "valor": valor,
        "formato": formato,
        "numerador": numerador,
        "denominador": denominador,
        "n": denominador,
        "ic95": None,
        "delta_periodo_anterior": delta,
        "fuente": fuente,
        "muestra_chica": False,
    }


def proporcion(id: str, etiqueta: str, exitos: int, total: int, fuente: str,
               anterior=None) -> dict:
    """Una metrica que es una tasa, con su intervalo y su marca de muestra."""
    valor = round(exitos / total, 4) if total else None
    m = metrica(id, etiqueta, valor, fuente, formato="porcentaje",
                numerador=exitos, denominador=total, anterior=anterior)
    m["ic95"] = list(wilson(exitos, total))
    m["muestra_chica"] = total < MUESTRA_CHICA
    return m


SIN_CAMPANA = "(sin campaña)"

# Las etapas que se reportan por campana, en orden. `contactado` no entra: en la
# cohorte de Meta el contacto se registra pintando la planilla de semaforo, no
# como evento, asi que contarlo daria siempre cero y pareceria un problema del
# equipo comercial cuando es un problema de donde se anota.
_ETAPAS = [
    ("interesados",  "interesado",           "Interesados"),
    ("agendadas",    "demo_agendada",        "Demos agendadas"),
    ("demos",        "demo_1",               "Demos hechas"),
    ("presupuestos", "presupuesto_enviado",  "Presupuestos enviados"),
    ("cierres",      "cerrado",              "Cierres"),
]

# El sufijo del id de la tasa de cada etapa. Explicito y no derivado del plural
# con `rstrip('s')`: eso convertia "cierres" en "cierre" y "demos" en "demo" por
# accidente, y un id de metrica es un contrato — el informe los cita.
_SUFIJO_TASA = {
    "interesados": "tasa_interes",
    "agendadas": "tasa_agenda",
    "demos": "tasa_demo",
    "presupuestos": "tasa_presupuesto",
    "cierres": "tasa_cierre",
}


def _slug(texto: str) -> str:
    """Un id estable para meter adentro del id de una metrica."""
    import re
    import unicodedata

    limpio = unicodedata.normalize("NFKD", (texto or "").lower())
    limpio = "".join(c for c in limpio if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", limpio).strip("_") or "sin_campana"


def por_campana(db_path: str, desde: str, hasta: str) -> list:
    """Un bloque de metricas por campana, mas uno con el total.

    Las etapas se cuentan con `alcanzo` sobre lead_events —no desde
    `crm_status`— para dar el mismo numero que el panel de Finanzas. Un lead
    que llego a demo y hoy figura en `no_interesa` cuenta como demo, porque la
    demo paso.
    """
    from services.embudo import alcanzo, costo

    conn = _connect(db_path)
    try:
        leads = conn.execute(
            "SELECT id, meta_campaign_name, meta_campaign_id FROM businesses "
            "WHERE source = 'meta' AND substr(scraped_at, 1, 10) BETWEEN ? AND ?",
            (desde, hasta)).fetchall()
        eventos_filas = conn.execute(
            "SELECT lead_id, new_status FROM lead_events").fetchall()
        gasto_filas = conn.execute(
            "SELECT campaign_name, SUM(spend) AS spend, SUM(impressions) AS impr, "
            "       SUM(clicks) AS clicks, SUM(leads) AS leads, "
            "       MAX(currency) AS currency "
            "FROM meta_insights WHERE date BETWEEN ? AND ? GROUP BY campaign_name",
            (desde, hasta)).fetchall()
    finally:
        conn.close()

    eventos = {}
    for fila in eventos_filas:
        eventos.setdefault(fila["lead_id"], set()).add(fila["new_status"])

    gasto = {f["campaign_name"]: f for f in gasto_filas}

    grupos = {}
    for lead in leads:
        grupos.setdefault(lead["meta_campaign_name"] or SIN_CAMPANA, []).append(lead)

    bloques = []
    for campana in sorted(grupos) + ["todas"]:
        if campana == "todas":
            del_grupo = leads
            g = {k: sum(float(f[k] or 0) for f in gasto_filas)
                 for k in ("spend", "impr", "clicks", "leads")}
            moneda = gasto_filas[0]["currency"] if gasto_filas else None
            campaign_id = None
        else:
            del_grupo = grupos[campana]
            fila = gasto.get(campana)
            g = {"spend": float(fila["spend"] or 0) if fila else 0.0,
                 "impr": float(fila["impr"] or 0) if fila else 0.0,
                 "clicks": float(fila["clicks"] or 0) if fila else 0.0,
                 "leads": float(fila["leads"] or 0) if fila else 0.0}
            moneda = fila["currency"] if fila else None
            campaign_id = del_grupo[0]["meta_campaign_id"] if del_grupo else None

        pref = f"campana.{_slug(campana)}"
        n = len(del_grupo)
        conteo = {clave: sum(1 for l in del_grupo
                             if alcanzo(eventos.get(l["id"], set()), etapa))
                  for clave, etapa, _ in _ETAPAS}

        ms = [
            metrica(f"{pref}.gasto", f"Gasto — {campana}", round(g["spend"], 2),
                    "meta_insights", formato="moneda"),
            metrica(f"{pref}.impresiones", f"Impresiones — {campana}",
                    int(g["impr"]), "meta_insights"),
            metrica(f"{pref}.clics", f"Clics — {campana}", int(g["clicks"]),
                    "meta_insights"),
            metrica(f"{pref}.leads_meta", f"Leads según Meta — {campana}",
                    int(g["leads"]), "meta_insights"),
            metrica(f"{pref}.leads_crm", f"Leads en el CRM — {campana}", n, "crm"),
            # Si Meta dice 5 y el CRM tiene 2, algo se pierde en la ingesta.
            metrica(f"{pref}.discrepancia_leads",
                    f"Leads que Meta reporta y el CRM no tiene — {campana}",
                    int(g["leads"]) - n, "derivada"),
            metrica(f"{pref}.cpl", f"Costo por lead — {campana}",
                    costo(g["spend"], n), "derivada", formato="moneda"),
            metrica(f"{pref}.costo_demo", f"Costo por demo — {campana}",
                    costo(g["spend"], conteo["demos"]), "derivada",
                    formato="moneda"),
            metrica(f"{pref}.costo_presupuesto",
                    f"Costo por presupuesto — {campana}",
                    costo(g["spend"], conteo["presupuestos"]), "derivada",
                    formato="moneda"),
            proporcion(f"{pref}.ctr", f"CTR — {campana}",
                       int(g["clicks"]), int(g["impr"]), "derivada"),
        ]
        for clave, _etapa, etiqueta in _ETAPAS:
            ms.append(metrica(f"{pref}.{clave}", f"{etiqueta} — {campana}",
                              conteo[clave], "crm"))
            ms.append(proporcion(f"{pref}.{_SUFIJO_TASA[clave]}",
                                 f"Tasa de {etiqueta.lower()} — {campana}",
                                 conteo[clave], n, "crm"))

        bloques.append({"campana": campana, "campaign_id": campaign_id,
                        "moneda": moneda, "metricas": ms})
    return bloques


# Las preguntas del formulario de Meta que sirven para segmentar, con su clave
# ya normalizada. Verificadas contra los 238 leads de produccion el 10/9/2026:
# las 191 respuestas del formulario vigente y las 47 del anterior.
#
# Hay DOS versiones del formulario conviviendo. Las preguntas equivalentes se
# unifican; las que no tienen equivalente van por separado con su propio `n`.
# Dos preguntas distintas nunca van bajo la misma etiqueta: seria sumar peras
# con manzanas y no se notaria en el grafico.
#
# `full_name`, `phone_number`, `email` y el nombre del negocio quedan afuera a
# proposito: son datos personales y ademas tienen un valor distinto por lead,
# asi que no segmentan nada.
PREGUNTAS = {
    "que_busca":   ("que_es_lo_que_buscas_para_tu_negocio",
                    "Qué busca para su negocio"),
    "presupuesto": ("contas_con_un_presupuesto_para_este_proyecto",
                    "Presupuesto declarado"),
    "objetivo":    ("cual_es_tu_objetivo_para_este_ano",
                    "Objetivo del año"),
    "objetivo_v2": ("cual_es_el_objetivo_que_tenes_en_este_2026",
                    "Objetivo del año (formulario anterior)"),
    "establecido": ("tenes_una_marca_negocio_establecido_o_es_un_proyecto_a_lanzar",
                    "Negocio establecido o a lanzar"),
    "ciudad":      ("city", "Ciudad"),
}

_POR_CLAVE = {clave: pregunta for pregunta, (clave, _) in PREGUNTAS.items()}

# La herramienta de prueba de formularios de Meta manda respuestas con este
# prefijo. Hay varias en la base de produccion y aparecian como un valor
# declarado mas en cada segmento, con n=1 — ruido que ensucia el panel y que la
# IA podria levantar como si fuera un hallazgo.
_PREFIJO_LEAD_DE_PRUEBA = "<test lead:"


def normalizar_clave(clave: str) -> str:
    """Una clave de form_data comparable.

    Se baja a minusculas, se sacan los acentos y se colapsa todo lo que no sea
    alfanumerico. Asi `¿Qué es lo que buscás para tu negocio?` y cualquier
    variante de puntuacion caen en la misma clave.
    """
    import re
    import unicodedata

    limpio = unicodedata.normalize("NFKD", (clave or "").lower())
    limpio = "".join(c for c in limpio if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", limpio).strip("_")


def _leer_formularios(db_path: str, desde: str, hasta: str):
    """(lead_id, {clave normalizada: valor}) de cada lead del periodo."""
    import json as _json

    conn = _connect(db_path)
    try:
        filas = conn.execute(
            "SELECT id, form_data FROM businesses WHERE source = 'meta' "
            "AND substr(scraped_at, 1, 10) BETWEEN ? AND ? "
            "AND form_data IS NOT NULL AND form_data != ''",
            (desde, hasta)).fetchall()
    finally:
        conn.close()

    for fila in filas:
        try:
            datos = _json.loads(fila["form_data"])
        except (ValueError, TypeError):
            continue          # un form_data roto no puede tumbar el dossier
        if isinstance(datos, dict):
            yield fila["id"], {normalizar_clave(k): v for k, v in datos.items()}


def claves_no_mapeadas(db_path: str, desde: str, hasta: str) -> dict:
    """Claves del formulario que no estan en PREGUNTAS, con cuantas veces salen.

    Existe porque si Meta cambia la redaccion de una pregunta, su segmento se
    caeria a cero y el panel simplemente no lo mostraria: un dato que
    desaparece sin ruido es peor que un error. Esto deja registro.
    """
    _IGNORADAS = {"full_name", "phone_number", "email", "como_se_llama_tu_negocio",
                  "perfil_de_instagram_o_sitio_web_del_mismo_si_corresponde"}
    sueltas = {}
    for _lead_id, datos in _leer_formularios(db_path, desde, hasta):
        for clave in datos:
            if clave in _POR_CLAVE or clave in _IGNORADAS:
                continue
            sueltas[clave] = sueltas.get(clave, 0) + 1
    if sueltas:
        logger.warning(f"form_data: claves sin mapear en PREGUNTAS: {sueltas}")
    return sueltas


def por_segmento(db_path: str, desde: str, hasta: str) -> list:
    """Tasas de avance por lo que el lead declaro en el formulario.

    Contesta la pregunta que ningun reporte contesta hoy: los que declararon
    mas de USD 1.000, ¿avanzan mas que los que dijeron "aun no lo se"?
    """
    from services.embudo import alcanzo

    conn = _connect(db_path)
    try:
        eventos_filas = conn.execute(
            "SELECT lead_id, new_status FROM lead_events").fetchall()
    finally:
        conn.close()

    eventos = {}
    for fila in eventos_filas:
        eventos.setdefault(fila["lead_id"], set()).add(fila["new_status"])

    # {pregunta: {valor declarado: [lead_id, ...]}}
    grupos = {}
    for lead_id, datos in _leer_formularios(db_path, desde, hasta):
        for clave, valor in datos.items():
            pregunta = _POR_CLAVE.get(clave)
            if not pregunta:
                continue
            valor = str(valor).strip()
            if not valor or valor.startswith(_PREFIJO_LEAD_DE_PRUEBA):
                continue
            grupos.setdefault(pregunta, {}).setdefault(valor, []).append(lead_id)

    bloques = []
    for pregunta in sorted(grupos):
        valores = []
        for declarado, ids in sorted(grupos[pregunta].items(),
                                     key=lambda kv: (-len(kv[1]), kv[0])):
            n = len(ids)
            pref = f"segmento.{pregunta}.{_slug(declarado)}"
            ms = []
            for clave, etapa, etiqueta in _ETAPAS:
                exitos = sum(1 for i in ids if alcanzo(eventos.get(i, set()), etapa))
                ms.append(proporcion(
                    f"{pref}.{_SUFIJO_TASA[clave]}",
                    f"Tasa de {etiqueta.lower()} — {declarado}",
                    exitos, n, "crm"))
            valores.append({"valor_declarado": declarado, "n": n, "metricas": ms})
        bloques.append({
            "pregunta": pregunta,
            "etiqueta": PREGUNTAS[pregunta][1],
            "n": sum(v["n"] for v in valores),
            "valores": valores,
        })
    return bloques


def _lunes_de(iso_fecha: str) -> str:
    """El lunes de la semana a la que pertenece esa fecha."""
    from datetime import date, timedelta

    y, m, d = (int(x) for x in iso_fecha[:10].split("-"))
    dia = date(y, m, d)
    return (dia - timedelta(days=dia.weekday())).isoformat()


def serie_semanal(db_path: str, desde: str, hasta: str) -> list:
    """Gasto, alcance y leads por semana.

    Semanal y no diaria a proposito: 238 leads en 178 dias son 1,3 por dia. En
    grano diario el grafico son picos y ceros; la senal aparece por semana.
    """
    from datetime import date as _date

    from services.embudo import costo

    conn = _connect(db_path)
    try:
        gasto_filas = conn.execute(
            "SELECT date, spend, impressions, clicks, leads FROM meta_insights "
            "WHERE date BETWEEN ? AND ?", (desde, hasta)).fetchall()
        lead_filas = conn.execute(
            "SELECT scraped_at FROM businesses WHERE source = 'meta' "
            "AND substr(scraped_at, 1, 10) BETWEEN ? AND ?",
            (desde, hasta)).fetchall()
    finally:
        conn.close()

    semanas = {}

    def _slot(inicio):
        return semanas.setdefault(inicio, {
            "semana": None, "inicio": inicio, "gasto": 0.0, "impresiones": 0,
            "clics": 0, "leads_meta": 0, "leads_crm": 0, "cpl": None})

    for fila in gasto_filas:
        s = _slot(_lunes_de(fila["date"]))
        s["gasto"] += float(fila["spend"] or 0)
        s["impresiones"] += int(fila["impressions"] or 0)
        s["clics"] += int(fila["clicks"] or 0)
        s["leads_meta"] += int(fila["leads"] or 0)

    for fila in lead_filas:
        _slot(_lunes_de(fila["scraped_at"]))["leads_crm"] += 1

    salida = []
    for inicio in sorted(semanas):
        s = semanas[inicio]
        s["gasto"] = round(s["gasto"], 2)
        s["cpl"] = costo(s["gasto"], s["leads_crm"])
        y, m, d = (int(x) for x in inicio.split("-"))
        s["semana"] = "%d-W%02d" % _date(y, m, d).isocalendar()[:2]
        salida.append(s)
    return salida


def conciliacion(db_path: str, desde: str, hasta: str) -> list:
    """Lo que Meta cobro contra lo que se cargo a mano en Finanzas.

    No se unifican y no se pisan: cada uno guarda lo suyo y aca se muestra la
    brecha. Vale por si sola — dice si se esta registrando en la contabilidad
    todo lo que Meta efectivamente cobro.

    Este modulo nunca escribe en finanzas_movimientos.
    """
    import sqlite3

    conn = _connect(db_path)
    try:
        meta_filas = conn.execute(
            "SELECT substr(date, 1, 7) AS periodo, SUM(spend) AS total "
            "FROM meta_insights WHERE date BETWEEN ? AND ? GROUP BY periodo",
            (desde, hasta)).fetchall()
        try:
            # `periodo` es una columna propia de finanzas_movimientos, no hay
            # que recortarla de la fecha: Finanzas la escribe al crear el
            # movimiento y es la que usa para sus propios cortes.
            cargado_filas = conn.execute(
                "SELECT periodo, SUM(monto_usd) AS total "
                "FROM finanzas_movimientos WHERE tipo = 'egreso' "
                "AND categoria = 'publicidad' AND anulado = 0 "
                "AND fecha BETWEEN ? AND ? GROUP BY periodo",
                (desde, hasta)).fetchall()
        except sqlite3.Error:
            # Finanzas es de otra sesion. Si su tabla no esta, la conciliacion
            # se degrada a "todo es brecha" en vez de tumbar el dossier entero.
            logger.warning("conciliacion: no se pudo leer finanzas_movimientos")
            cargado_filas = []
    finally:
        conn.close()

    meta = {f["periodo"]: float(f["total"] or 0) for f in meta_filas}
    cargado = {f["periodo"]: float(f["total"] or 0) for f in cargado_filas}

    ms = []
    for periodo in sorted(set(meta) | set(cargado)):
        suf = periodo.replace("-", "_")
        m_val = round(meta.get(periodo, 0.0), 2)
        c_val = round(cargado.get(periodo, 0.0), 2)
        ms.append(metrica(f"conciliacion.gasto_meta.{suf}",
                          f"Gasto según Meta — {periodo}", m_val,
                          "meta_insights", formato="moneda"))
        ms.append(metrica(f"conciliacion.gasto_cargado.{suf}",
                          f"Gasto cargado en Finanzas — {periodo}", c_val,
                          "crm", formato="moneda"))
        ms.append(metrica(f"conciliacion.brecha.{suf}",
                          f"Gasto de Meta sin registrar — {periodo}",
                          round(m_val - c_val, 2), "derivada", formato="moneda"))
    return ms


# Cuantos dias despues de un recordatorio se le puede atribuir un cambio de
# estado. Mas alla de eso, el mail no fue lo que lo movio.
VENTANA_RECORDATORIO_DIAS = 7

# Eventos cuyo `created_at` es la fecha en que el CRM se entero, no la fecha en
# que la cosa paso. El 27/8/2026 se importaron de una sola vez 195 estados que
# los CEO venian pintando a mano en la planilla de semaforo: son 195 de los 242
# eventos de la cohorte de Meta, todos con la misma fecha.
#
# El ESTADO de esos eventos es real y se usa para contar el embudo. La FECHA no
# lo es. Sin excluirlos, la mediana de dias hasta el primer contacto daba 79
# —que es cuanto tardo el import, no cuanto tarda el equipo en llamar— y un
# informe habria escrito que el equipo tarda 79 dias en atender un lead.
_MARCA_IMPORT_MASIVO = "%planilla%"


def _dias_entre(desde_iso, hasta_iso):
    """Dias enteros entre dos timestamps de SQLite, o None si no se puede."""
    from datetime import datetime

    def _parse(v):
        v = (v or "").strip().replace("T", " ")
        if not v:
            return None
        for formato in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(v[:19] if len(v) >= 19 else v, formato)
            except ValueError:
                continue
        return None

    a, b = _parse(desde_iso), _parse(hasta_iso)
    if not a or not b:
        return None
    return (b - a).days


def _mediana(valores):
    if not valores:
        return None
    ordenados = sorted(valores)
    mitad = len(ordenados) // 2
    if len(ordenados) % 2:
        return float(ordenados[mitad])
    return round((ordenados[mitad - 1] + ordenados[mitad]) / 2, 2)


def tiempos(db_path: str, desde: str, hasta: str) -> list:
    """Cuanto se tarda en reaccionarle a un lead.

    La mediana y no el promedio: un lead contactado a los 60 dias arrastra el
    promedio y hace parecer lento a un equipo que contesta el mismo dia.

    Un lead sin ningun evento no entra en la cuenta. "Nunca contactado" no es
    "tardo mucho": es que el dato no esta, y meterlo como un numero grande
    seria inventarlo.

    Los eventos del import masivo de la planilla quedan afuera: ver
    `_MARCA_IMPORT_MASIVO`. Su fecha es la del import, no la del contacto.
    """
    from services.embudo import normalizar_estado

    conn = _connect(db_path)
    try:
        leads = conn.execute(
            "SELECT id, scraped_at FROM businesses WHERE source = 'meta' "
            "AND substr(scraped_at, 1, 10) BETWEEN ? AND ?",
            (desde, hasta)).fetchall()
        eventos = conn.execute(
            "SELECT lead_id, new_status, created_at FROM lead_events "
            "WHERE created_by IS NULL OR created_by NOT LIKE ? "
            "ORDER BY created_at", (_MARCA_IMPORT_MASIVO,)).fetchall()
    finally:
        conn.close()

    primero, primera_demo = {}, {}
    for e in eventos:
        primero.setdefault(e["lead_id"], e["created_at"])
        if normalizar_estado(e["new_status"]) == "demo_1":
            primera_demo.setdefault(e["lead_id"], e["created_at"])

    a_contacto, a_demo = [], []
    for lead in leads:
        d = _dias_entre(lead["scraped_at"], primero.get(lead["id"]))
        if d is not None and d >= 0:
            a_contacto.append(d)
        d = _dias_entre(lead["scraped_at"], primera_demo.get(lead["id"]))
        if d is not None and d >= 0:
            a_demo.append(d)

    return [
        metrica("tiempos.dias_a_primer_contacto",
                "Días hasta el primer contacto (mediana)",
                _mediana(a_contacto), "crm", denominador=len(a_contacto)),
        metrica("tiempos.dias_a_demo", "Días hasta la demo (mediana)",
                _mediana(a_demo), "crm", denominador=len(a_demo)),
    ]


def recordatorios(db_path: str, desde: str, hasta: str) -> list:
    """Si cada mail de la secuencia movio el estado del lead.

    Atribucion por ventana, no causalidad: se cuenta si hubo un cambio de
    estado dentro de los 7 dias siguientes al envio. Pudo haber pasado otra
    cosa en el medio —una llamada, por ejemplo— y el informe tiene prohibido
    decir que el mail fue la causa.

    **Mide movimiento REGISTRADO, no movimiento.** En la cohorte de Meta los
    contactos se anotan pintando la planilla de semaforo, no como evento del
    CRM: sacando el import masivo quedan 47 eventos genuinos en seis meses.
    Sobre los datos del 8/9/2026 esto da 0 de 231, o sea que ningun recordatorio
    fue seguido de un cambio registrado en una semana. La lectura correcta es
    "no hay evidencia de que muevan", no "esta probado que no sirven": lo
    primero que habria que arreglar es donde se anota el contacto.
    """
    conn = _connect(db_path)
    try:
        envios = conn.execute(
            "SELECT business_id, numero, sent_at FROM meta_reminders "
            "WHERE substr(sent_at, 1, 10) BETWEEN ? AND ?",
            (desde, hasta)).fetchall()
        eventos = conn.execute(
            "SELECT lead_id, created_at FROM lead_events "
            "WHERE created_by IS NULL OR created_by NOT LIKE ?",
            (_MARCA_IMPORT_MASIVO,)).fetchall()
    finally:
        conn.close()

    por_lead = {}
    for e in eventos:
        por_lead.setdefault(e["lead_id"], []).append(e["created_at"])

    conteo = {}
    for envio in envios:
        slot = conteo.setdefault(envio["numero"], {"enviados": 0, "movio": 0})
        slot["enviados"] += 1
        for cuando in por_lead.get(envio["business_id"], []):
            dias = _dias_entre(envio["sent_at"], cuando)
            if dias is not None and 0 <= dias <= VENTANA_RECORDATORIO_DIAS:
                slot["movio"] += 1
                break     # el lead se movio; no se cuenta dos veces

    return [
        proporcion(
            f"recordatorios.tasa_movio_{n}",
            f"Recordatorio {n}: movió el estado en {VENTANA_RECORDATORIO_DIAS} días",
            conteo[n]["movio"], conteo[n]["enviados"], "crm")
        for n in sorted(conteo)
    ]
