"""Inteligencia financiera: qué hacer este mes para ganar plata, con la cuenta.

Lee todo el sistema y arma una lista corta de acciones (reglas R1 a R7 del
documento de Juan), ordenadas por cuánta plata mueven. Las recomendaciones se
recalculan UNA vez por día (`corrida_diaria`), no en cada carga de la pantalla.

Moneda: todo en dólares (USD), con la misma conversión que Finanzas
(`monto_usd` de los movimientos y `a_usd` con el tipo de cambio del fijo). El
documento dice "pesos", pero Finanzas trabaja en USD; el umbral de "impacto
menor a 100 al mes" es USD 100.

Criterio 8, nunca inventar números: cada valor sale de una tabla del sistema o
de una cuenta entre valores del sistema, y la caja del cálculo muestra la cuenta
entera. Los únicos números escritos acá son los del documento (umbral 100,
máximo 6, 3 meses, 30 días, 20 %, 40 %, 30 %) y `FUNCIONO_SI_LLEGA_A`, que es un
supuesto a confirmar. Lo que el sistema no tiene (la comisión de cobro) se
carga desde la pantalla en `if_supuestos`; sin eso la regla muestra el aviso de
dato faltante.

De dónde sale cada dato
-----------------------
Ventas            negocios en ETAPAS_CLIENTE (cerrado / en desarrollo /
                  finalizado). Fecha: el primer `lead_event` que los pasó a una
                  de esas etapas; si no hay, `scraped_at` (el alta). Más las
                  fichas de Proceso de venta en "Presupuesto Aceptado" que no
                  están conectadas a un negocio (ventas sin lead, sin fecha).
Canal de la venta `ventas_origen_manual` (elegido a mano) y si no,
                  `businesses.source` normalizado: meta -> Meta Ads; discovery o
                  vacío con `maps_url` (padrón de Google Maps) -> Outbound;
                  web_guia / calendly / calendly_gcal -> Otro. 'manual', vacío
                  sin padrón o un valor desconocido -> sin origen (se pide).
Precio cobrado    ingresos de Finanzas del cliente sin la categoría
                  mantenimiento; si no hay, `businesses.monto_pagado` en USD
                  (Clientes); si no, el último `budgets.total_amount`.
Tipo de proyecto  la categoría de ingreso de Finanzas con más plata
                  (desarrollo_web / software_medida / marketing); si no,
                  `client_info.rubro` (lo que pidió en el bot); si no,
                  `businesses.interest`.
Proyecto -> venta `projects.notion_page_id` = `notion_clients.notion_project_page_id`
                  y `notion_clients.business_id` = el negocio.
Esfuerzo          `proyectos_esfuerzo` (cargado en Proyectos al cerrar).
Costo por hora    egresos de Finanzas de los últimos 3 meses sin publicidad ÷
                  horas base del equipo en esos días (personas que llevan horas
                  × horas por día × días hábiles). Supuesto a confirmar: reparte
                  todo el gasto de la empresa sobre las horas del equipo.
Margen por venta  promedio de (precio − horas × costo por hora) de los proyectos
                  con esfuerzo y precio.
Capacidad del mes `capacidad()` de services/equipo.py semana por semana, en la
                  parte de cada semana que cae en el mes: horas base − ausencias
                  (los recuperos no suman). Menos las horas comprometidas en
                  proyectos en curso (su esfuerzo o el promedio, en la parte de
                  su timeline que cae en el mes). Capacidad en proyectos = horas
                  libres ÷ esfuerzo promedio, redondeado para abajo.
Pauta de Meta     `meta_insights.spend` de los últimos 3 meses (por campaña;
                  nunca sumado con `meta_ad_insights`, que contaría dos veces).
Pérdidas          fichas de Proceso de venta en Perdido / Presupuesto Rechazado,
                  demos de la planilla en "no cerró", leads en `rechazo`. Una
                  pérdida por negocio (la ficha, la demo y el lead del mismo
                  negocio son la misma pérdida). Motivo en `perdidas_motivo`.
Cobros vencidos   `finanzas_por_cobrar` sin cobrar con `vence` anterior a hoy.
Fijos por canal   `finanzas_recurrentes` (egresos activos) + `fijos_canal`.
Resultado del mes ingresos − egresos de Finanzas del mes en curso (`_totales`).

Cómo se mide cada regla a los 30 días (`medir`)
------------------------------------------------
R1  ingreso mensual por mantenimiento neto de comisión: hoy − al tomarla.
R2  (ventas de Meta Ads en los 30 días − ventas de Meta por mes al tomarla) ×
    (margen − costo por venta al tomarla).
R3  (ventas totales en los 30 días − ventas por mes al tomarla) × margen.
R4  suma de los saldos vencidos listados que ya figuran cobrados.
R5  suma de los fijos listados que ya están apagados o terminados.
R6  margen del tipo hoy − al tomarla (en puntos); funcionó si llega al 20 %.
R7  (pérdidas por ese motivo por mes al tomarla − pérdidas en los 30 días) ×
    margen por venta.
Funcionó: el impacto real llega a `FUNCIONO_SI_LLEGA_A` del esperado (R6: el
margen del tipo quedó en 20 % o más).
"""

from __future__ import annotations

import calendar
import json
import logging
import math
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from database import (ETAPAS_CLIENTE, _connect, get_recurrente,
                      listar_clientes_activos, listar_movimientos,
                      listar_personas_equipo, listar_por_cobrar,
                      listar_recurrentes)
from services.embudo import normalizar_estado
from services.equipo import capacidad, dias_habiles, lunes_de
from services.finanzas import _totales, a_usd

logger = logging.getLogger(__name__)

# ── lo que dice el documento ─────────────────────────────────────────────────
UMBRAL_IMPACTO_USD = 100        # regla transversal 5
MAX_RECOMENDACIONES = 6
VENTANA_MESES = 3               # ventana móvil
DIAS_SEGUIMIENTO = 30           # regla transversal 4
MARGEN_MINIMO = 0.20            # R6
CONCENTRACION_MOTIVO = 0.40     # R7
RECUPERO_MOTIVO = 0.30          # R7
# Supuesto a confirmar con Juan: "funcionó" si lo que pasó de verdad llega a la
# mitad de lo esperado. El documento pide comparar, no dice contra qué umbral.
FUNCIONO_SI_LLEGA_A = 0.5

CORRIDA = "inteligencia_fin"
LISTA_MAX = 50
ESFUERZO_MAX = 5000
_EPS = 1e-9
_MVD = timezone(timedelta(hours=-3))

REGLAS = {  # regla -> (confianza, tipo)
    "R1": ("alta", "ingreso"),
    "R2": ("media", "ingreso"),
    "R3": ("media", "ingreso"),
    "R4": ("alta", "ingreso"),
    "R5": ("media", "recorte"),
    "R6": ("alta", "alerta"),
    "R7": ("baja", "ingreso"),
}

MOTIVOS = {
    "precio": "Precio",
    "se_enfrio": "Se enfrió",
    "eligio_otro": "Eligió a otro",
    "no_era_momento": "No era el momento",
    "no_calificaba": "No calificaba",
}
CANALES = {"meta_ads": "Meta Ads", "outbound": "Outbound",
           "referido": "Referido", "otro": "Otro"}
SOURCE_A_CANAL = {"meta": "meta_ads", "discovery": "outbound",
                  "web_guia": "otro", "calendly": "otro", "calendly_gcal": "otro"}

ENTIDADES_PERDIDA = ("notion_client", "demo", "lead")
ESTADOS_PERDIDO_NOTION = ("Perdido", "Presupuesto Rechazado")
ESTADO_PERDIDO_DEMO = "no_cerro"
ESTADO_PERDIDO_LEAD = "rechazo"
ESTADO_ACEPTADO = "Presupuesto Aceptado"   # el mismo de services/notion_service.py

# Etapas de Notion Projects que ya no ocupan al equipo.
ETAPAS_TERMINADAS = {"done", "complete", "completed", "canceled", "cancelled",
                     "archived", "terminado", "finalizado", "entregado", "cancelado"}
CATEGORIAS_TIPO = ("desarrollo_web", "software_medida", "marketing")
ETIQUETA_TIPO = {"desarrollo_web": "Desarrollo web",
                 "software_medida": "Software a medida", "marketing": "Marketing"}

SUPUESTOS = {"comision_cobro_pct": "Comisión de cobro del mantenimiento (%)"}

ADVERTENCIA_R5 = ("Puede estar aportando algo que el sistema no mide (marca, "
                  "referidos que no quedaron registrados). Probá un mes sin ese "
                  "gasto antes de cortarlo.")
ADVERTENCIA_R6 = ("La recomendación es revisar el precio, nunca abandonar la línea: "
                  "un margen bajo puede convenir si abre mantenimiento recurrente.")
SUGERENCIA_R7 = {
    "precio": "Revisá cómo presentás el precio: alcance por etapas o en cuotas.",
    "se_enfrio": "Acortá el tiempo entre la demo y el presupuesto, y seguí cada caso en Seguimiento de leads.",
    "eligio_otro": "Averiguá contra quién perdés y qué ofrece que vos no.",
    "no_era_momento": "Agendales un seguimiento a futuro en Seguimiento de leads.",
    "no_calificaba": "Filtrá mejor antes de agendar la demo.",
}

MARGEN_REGLAS = ("R2", "R3", "R6", "R7")
AVISOS = {
    "motivo": ("Faltan motivos de pérdida",
               "Elegí el motivo de cada pérdida de la lista. Habilitaría ver dónde se pierde la plata (R7)."),
    "esfuerzo": ("Falta el esfuerzo de los proyectos",
                 "Cargá las horas o los días de cada proyecto al cerrarlo, en Proyectos. Habilitaría el "
                 "margen por venta y por tipo: escalar la pauta (R2), capacidad ociosa (R3), margen "
                 "bajo por tipo de proyecto (R6) y motivo de pérdida dominante (R7)."),
    "precio": ("Los proyectos con esfuerzo no llegan a un precio",
               "Conectá la ficha de Proceso de venta con su proyecto y con el cliente del CRM, y cargá "
               "lo cobrado en Finanzas. Sin precio no hay margen (R2, R3, R6, R7)."),
    "costo_hora": ("Falta el costo por hora del equipo",
                   "Sale de los egresos de Finanzas de los últimos 3 meses (sin pauta) ÷ las horas del "
                   "equipo cargado en Equipo. Habilitaría el margen (R2, R3, R6, R7)."),
    "equipo": ("Falta la capacidad del equipo",
               "Cargá en Equipo a las personas que llevan horas. Habilitaría escalar la pauta (R2) y "
               "la capacidad ociosa (R3)."),
    "origen": ("Hay ventas sin origen",
               "Elegí el canal de cada venta sin lead vinculado. Habilitaría el costo por venta por "
               "canal (R2, R3) y los gastos fijos sin retorno (R5)."),
    "moneda_pauta": ("La pauta de Meta no está en dólares",
                     "El gasto de Meta viene en otra moneda y el sistema no tiene con qué convertirlo. "
                     "Habilitaría R2 y R3."),
    "cuota": ("No hay ninguna cuota de mantenimiento en Finanzas",
              "Cargá al menos un ingreso fijo de categoría mantenimiento con su cliente. Habilitaría "
              "cobrar mantenimiento a los clientes que no lo pagan (R1)."),
    "comision": ("Falta la comisión de cobro",
                 "Cargala en Supuestos, abajo de los datos. Habilitaría cobrar mantenimiento a los "
                 "clientes que no lo pagan (R1)."),
}


# ── tiempo ───────────────────────────────────────────────────────────────────

def ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse_iso(txt) -> datetime | None:
    try:
        return datetime.strptime(str(txt)[:19].replace("T", " "),
                                 "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def hoy_de(ahora: datetime) -> date:
    return ahora.astimezone(_MVD).date()


def _fecha(valor) -> date | None:
    try:
        return date.fromisoformat(str(valor)[:10])
    except (TypeError, ValueError):
        return None


def _restar_meses(d: date, n: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 - n, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def ventana(hoy: date) -> tuple[date, date]:
    """Los últimos 3 meses, hasta hoy inclusive."""
    return _restar_meses(hoy, VENTANA_MESES), hoy


# ── formato ──────────────────────────────────────────────────────────────────

def usd(x) -> str:
    n = float(x or 0)
    signo = "−" if n < -0.004 else ""
    n = abs(n)
    if abs(n - round(n)) < 0.005:
        txt = f"{int(round(n)):,}".replace(",", ".")
    else:
        txt = f"{n:,.2f}".replace(",", "#").replace(".", ",").replace("#", ".")
    return f"{signo}USD {txt}"


def num(x, decimales: int = 1) -> str:
    n = round(float(x or 0), decimales)
    if abs(n - round(n)) < 10 ** -(decimales + 1):
        return f"{int(round(n)):,}".replace(",", ".")
    return f"{n:,.{decimales}f}".replace(",", "#").replace(".", ",").replace("#", ".")


def _dmy(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else "sin fecha"


def _plural(n, uno: str, varios: str) -> str:
    return uno if n == 1 else varios


def _q(db_path: str, sql: str, params=()) -> list[dict]:
    conn = _connect(db_path)
    try:
        return [dict(r) for r in conn.execute(sql, list(params)).fetchall()]
    finally:
        conn.close()


def _numero(valor) -> float | None:
    if isinstance(valor, bool) or valor is None:
        return None
    try:
        x = float(str(valor).strip().replace(",", "."))
    except ValueError:
        return None
    return x if math.isfinite(x) else None


# ── ventas y su origen ───────────────────────────────────────────────────────

def canal_de_source(source, maps_url=None) -> str | None:
    """`businesses.source` a los canales del documento, o None si no se sabe."""
    s = (source or "").strip().lower()
    if not s:
        # Sin source: los del padrón scrapeado de Google Maps son Outbound. Uno
        # cargado a mano sin source no dice de dónde vino.
        return "outbound" if (maps_url or "").strip() else None
    return SOURCE_A_CANAL.get(s)


def ventas(db_path: str) -> list[dict]:
    etapas = list(ETAPAS_CLIENTE)
    marcas = ", ".join("?" for _ in etapas)
    negocios = _q(db_path, f"SELECT id, name, source, maps_url, crm_status, scraped_at "
                           f"FROM businesses WHERE crm_status IN ({marcas})", etapas)
    eventos = _q(db_path, f"SELECT e.lead_id, e.new_status, e.created_at FROM lead_events e "
                          f"JOIN businesses b ON b.id = e.lead_id WHERE b.crm_status IN ({marcas}) "
                          f"ORDER BY e.created_at, e.id", etapas)
    primera: dict[int, date] = {}
    for e in eventos:
        if e["lead_id"] not in primera and normalizar_estado(e["new_status"]) in ETAPAS_CLIENTE:
            f = _fecha(e["created_at"])
            if f:
                primera[e["lead_id"]] = f
    manual = {(r["entidad"], r["entidad_id"]): r["canal"]
              for r in _q(db_path, "SELECT entidad, entidad_id, canal FROM ventas_origen_manual")}

    salida = []
    for n in negocios:
        canal, origen = manual.get(("business", n["id"])), "manual"
        if not canal:
            canal = canal_de_source(n["source"], n["maps_url"])
            origen = "lead" if canal else None
        salida.append({"entidad": "business", "id": n["id"], "business_id": n["id"],
                       "nombre": n["name"], "fecha": primera.get(n["id"]) or _fecha(n["scraped_at"]),
                       "canal": canal, "origen": origen, "source": n["source"] or ""})
    fichas = _q(db_path, "SELECT nc.id, nc.name FROM notion_clients nc "
                         "LEFT JOIN businesses b ON b.id = nc.business_id "
                         "WHERE nc.status = ? AND b.id IS NULL", (ESTADO_ACEPTADO,))
    for f in fichas:
        canal = manual.get(("notion_client", f["id"]))
        salida.append({"entidad": "notion_client", "id": f["id"], "business_id": None,
                       "nombre": f["name"], "fecha": None, "canal": canal,
                       "origen": "manual" if canal else None, "source": ""})
    return salida


def precios_y_tipos(db_path: str) -> dict[int, dict]:
    """Precio cobrado y tipo de proyecto por negocio (ver el docstring del módulo)."""
    info: dict[int, dict] = {}
    cats: dict[int, dict] = {}
    for f in _q(db_path, "SELECT client_id, categoria, SUM(monto_usd) AS total "
                         "FROM finanzas_movimientos WHERE tipo = 'ingreso' AND anulado = 0 "
                         "AND client_id IS NOT NULL GROUP BY client_id, categoria"):
        if f["categoria"] == "mantenimiento":
            continue
        cats.setdefault(f["client_id"], {})[f["categoria"]] = float(f["total"] or 0)
    for cid, por_cat in cats.items():
        total = round(sum(por_cat.values()), 2)
        if total <= 0:
            continue
        tipos = {c: v for c, v in por_cat.items() if c in CATEGORIAS_TIPO and v > 0}
        info[cid] = {"precio": total, "fuente_precio": "cobros en Finanzas",
                     "tipo": max(tipos, key=tipos.get) if tipos else None,
                     "fuente_tipo": "categoría del cobro en Finanzas" if tipos else ""}
    for f in _q(db_path, "SELECT id, monto_pagado, moneda_pagado FROM businesses "
                         "WHERE monto_pagado > 0 AND moneda_pagado = 'USD'"):
        if f["id"] not in info:
            info[f["id"]] = {"precio": round(float(f["monto_pagado"]), 2),
                             "fuente_precio": "monto pagado en Clientes", "tipo": None, "fuente_tipo": ""}
    for f in _q(db_path, "SELECT client_id, total_amount FROM budgets WHERE total_amount > 0 "
                         "ORDER BY created_at, id"):
        if f["client_id"] not in info or info[f["client_id"]]["fuente_precio"] == "último presupuesto":
            info[f["client_id"]] = {"precio": round(float(f["total_amount"]), 2),
                                    "fuente_precio": "último presupuesto", "tipo": None, "fuente_tipo": ""}
    for f in _q(db_path, "SELECT b.id, ci.rubro, b.interest FROM businesses b "
                         "LEFT JOIN client_info ci ON ci.client_id = b.id"):
        d = info.get(f["id"])
        if not d or d["tipo"]:
            continue
        if (f["rubro"] or "").strip():
            d["tipo"], d["fuente_tipo"] = f["rubro"].strip(), "lo que pidió en el bot"
        elif (f["interest"] or "").strip():
            d["tipo"], d["fuente_tipo"] = f["interest"].strip(), "servicio que pidió al agendar"
    return info


# ── proyectos, costo por hora, margen y capacidad ────────────────────────────

def proyectos(db_path: str) -> list[dict]:
    filas = _q(db_path, "SELECT p.id, p.notion_page_id, p.name, p.stage, p.timeline_start, "
                        "p.timeline_end, e.horas, e.valor_cargado, e.unidad_cargada "
                        "FROM projects p LEFT JOIN proyectos_esfuerzo e ON e.project_id = p.id "
                        "ORDER BY p.name COLLATE NOCASE, p.id")
    vinculo: dict[str, int] = {}
    for v in _q(db_path, "SELECT nc.notion_project_page_id AS pagina, nc.business_id "
                         "FROM notion_clients nc JOIN businesses b ON b.id = nc.business_id "
                         "WHERE nc.notion_project_page_id IS NOT NULL ORDER BY nc.id"):
        vinculo.setdefault(v["pagina"], v["business_id"])
    for p in filas:
        p["business_id"] = vinculo.get(p["notion_page_id"])
        p["terminado"] = (p["stage"] or "").strip().lower() in ETAPAS_TERMINADAS
        p["horas"] = float(p["horas"]) if p["horas"] else None
    return filas


def costo_hora(db_path: str, desde: date, hasta: date) -> dict:
    egresos = round(sum(float(m["monto_usd"] or 0)
                        for m in listar_movimientos(db_path, tipo="egreso")
                        if m["categoria"] != "publicidad"
                        and desde.isoformat() <= (m["fecha"] or "")[:10] <= hasta.isoformat()), 2)
    personas = [p for p in listar_personas_equipo(db_path) if p.get("lleva_horas")]
    dias = len(dias_habiles(desde, hasta))
    horas = round(sum(float(p["horas_por_dia"]) for p in personas) * dias, 2)
    valor = round(egresos / horas, 2) if egresos > 0 and horas > 0 else None
    return {"egresos": egresos, "horas": horas, "dias": dias, "personas": len(personas), "valor": valor}


def margenes(db_path: str, hoy: date) -> dict:
    desde, hasta = ventana(hoy)
    ch = costo_hora(db_path, desde, hasta)
    proys = proyectos(db_path)
    precios = precios_y_tipos(db_path)
    con_esfuerzo = [p for p in proys if p["horas"]]
    filas = []
    for p in con_esfuerzo:
        info = precios.get(p["business_id"]) if p["business_id"] else None
        if not info or not info.get("precio"):
            continue
        costo = round(p["horas"] * ch["valor"], 2) if ch["valor"] is not None else None
        filas.append({"project_id": p["id"], "nombre": p["name"], "horas": p["horas"],
                      "precio": info["precio"], "fuente_precio": info["fuente_precio"],
                      "tipo": info["tipo"], "fuente_tipo": info["fuente_tipo"], "costo": costo,
                      "margen": round(info["precio"] - costo, 2) if costo is not None else None})
    esfuerzo_prom = (round(sum(p["horas"] for p in con_esfuerzo) / len(con_esfuerzo), 2)
                     if con_esfuerzo else None)
    precio_prom = round(sum(f["precio"] for f in filas) / len(filas), 2) if filas else None
    esf_con_precio = round(sum(f["horas"] for f in filas) / len(filas), 2) if filas else None
    margen_venta = (round(sum(f["margen"] for f in filas) / len(filas), 2)
                    if filas and ch["valor"] is not None else None)
    return {"costo_hora": ch, "proyectos": proys, "con_esfuerzo": len(con_esfuerzo),
            "filas": filas, "esfuerzo_promedio": esfuerzo_prom, "precio_promedio": precio_prom,
            "esfuerzo_con_precio": esf_con_precio, "margen_venta": margen_venta}


def capacidad_mes(db_path: str, hoy: date, proys: list[dict], esfuerzo_prom) -> dict | None:
    if not [p for p in listar_personas_equipo(db_path) if p.get("lleva_horas")]:
        return None
    primero = hoy.replace(day=1)
    ultimo = hoy.replace(day=calendar.monthrange(hoy.year, hoy.month)[1])
    base = ausencia = 0.0
    lunes = lunes_de(primero)
    while lunes <= ultimo:
        c = capacidad(db_path, lunes)
        dentro = dias_habiles(max(lunes, primero), min(lunes + timedelta(days=4), ultimo))
        if c["dias_habiles"] and dentro:
            parte = len(dentro) / c["dias_habiles"]
            base += c["totales"]["horas_base"] * parte
            ausencia += c["totales"]["horas_ausencia"] * parte
        lunes += timedelta(days=7)
    netas = base - ausencia

    comprometidas, en_curso = 0.0, 0
    for p in proys:
        if p["terminado"]:
            continue
        inicio, fin = _fecha(p["timeline_start"]), _fecha(p["timeline_end"])
        if (fin and fin < primero) or (inicio and inicio > ultimo):
            continue
        horas = p["horas"] or esfuerzo_prom
        if not horas:
            continue
        parte = 1.0
        if inicio and fin:
            total = dias_habiles(inicio, fin)
            if total:
                parte = len(dias_habiles(max(inicio, primero), min(fin, ultimo))) / len(total)
        comprometidas += horas * parte
        en_curso += 1

    libres_h = max(netas - comprometidas, 0.0)
    slots = math.floor(libres_h / esfuerzo_prom + _EPS) if esfuerzo_prom else None
    return {"desde": primero, "hasta": ultimo, "base": round(base, 2),
            "ausencia": round(ausencia, 2), "netas": round(netas, 2),
            "comprometidas": round(comprometidas, 2), "en_curso": en_curso,
            "libres_horas": round(libres_h, 2), "slots": slots}


def pauta_meta(db_path: str, desde: date, hasta: date) -> dict:
    d, h = desde.isoformat(), hasta.isoformat()
    gasto = _q(db_path, "SELECT COALESCE(SUM(spend), 0) AS g FROM meta_insights "
                        "WHERE date BETWEEN ? AND ?", (d, h))[0]["g"]
    monedas = {(r["currency"] or "").upper()
               for r in _q(db_path, "SELECT DISTINCT currency FROM meta_insights "
                                    "WHERE date BETWEEN ? AND ? AND spend > 0", (d, h))}
    leads = _q(db_path, "SELECT COUNT(*) AS n FROM businesses WHERE source = 'meta' "
                        "AND substr(scraped_at, 1, 10) BETWEEN ? AND ?", (d, h))[0]["n"]
    return {"gasto": round(float(gasto or 0), 2), "leads": leads,
            "monedas_raras": sorted(monedas - {"", "USD"})}


# ── pérdidas ─────────────────────────────────────────────────────────────────

def perdidas(db_path: str) -> list[dict]:
    """Una pérdida por negocio, con su motivo (el cargado más reciente)."""
    registros = {(r["entidad"], r["entidad_id"]): r
                 for r in _q(db_path, "SELECT * FROM perdidas_motivo")}
    marcas = ", ".join("?" for _ in ESTADOS_PERDIDO_NOTION)
    items = []
    for f in _q(db_path, f"SELECT id, name, status, business_id FROM notion_clients "
                         f"WHERE status IN ({marcas}) ORDER BY id", ESTADOS_PERDIDO_NOTION):
        items.append(("notion_client", f["id"], f["business_id"], f["name"], f["status"], None))
    for f in _q(db_path, "SELECT d.id, d.client_id, d.fecha, b.name FROM demos_realizadas d "
                         "LEFT JOIN businesses b ON b.id = d.client_id "
                         "WHERE d.estado_planilla = ? ORDER BY d.id", (ESTADO_PERDIDO_DEMO,)):
        items.append(("demo", f["id"], f["client_id"], f["name"] or "Demo", "Demo no cerró",
                      _fecha(f["fecha"])))
    for f in _q(db_path, "SELECT b.id, b.name, (SELECT MAX(e.created_at) FROM lead_events e "
                         "WHERE e.lead_id = b.id AND e.new_status = ?) AS cuando "
                         "FROM businesses b WHERE b.crm_status = ? ORDER BY b.id",
                (ESTADO_PERDIDO_LEAD, ESTADO_PERDIDO_LEAD)):
        items.append(("lead", f["id"], f["id"], f["name"], "Lead en rechazo", _fecha(f["cuando"])))

    grupos: dict = {}
    for entidad, eid, bid, nombre, estado, fecha_item in items:
        r = registros.get((entidad, eid)) or {}
        clave = ("b", bid) if bid else (entidad, eid)
        g = grupos.setdefault(clave, {"entidad": entidad, "entidad_id": eid, "business_id": bid,
                                      "nombre": nombre or "", "estado": estado, "motivo": None,
                                      "motivo_en": None, "fecha": None, "items": []})
        g["items"].append({"entidad": entidad, "entidad_id": eid})
        if r.get("motivo") and (g["motivo_en"] is None or (r.get("motivo_en") or "") > g["motivo_en"]):
            g["motivo"], g["motivo_en"] = r["motivo"], r.get("motivo_en") or ""
        fecha = _fecha(r.get("perdida_en")) or _fecha(r.get("motivo_en")) or fecha_item
        if fecha and (g["fecha"] is None or fecha > g["fecha"]):
            g["fecha"] = fecha
    return list(grupos.values())


def motivos_por_entidad(db_path: str, entidad: str) -> dict[int, str | None]:
    """{id: motivo} de las cosas perdidas de esa entidad, para pintar el selector."""
    salida = {}
    for g in perdidas(db_path):
        for it in g["items"]:
            if it["entidad"] == entidad:
                salida[it["entidad_id"]] = g["motivo"]
    return salida


def guardar_motivo(db_path: str, entidad: str, entidad_id: int, motivo,
                   quien: str = "", ahora: datetime | None = None) -> str | None:
    """Devuelve None si guardó, o el mensaje de error."""
    if entidad not in ENTIDADES_PERDIDA:
        return "no se reconoce qué se perdió"
    if motivo not in MOTIVOS:
        return "elegí un motivo de la lista: " + " / ".join(MOTIVOS.values()).lower()
    item = next((it for g in perdidas(db_path) for it in g["items"]
                 if it["entidad"] == entidad and it["entidad_id"] == entidad_id), None)
    if not item:
        return "eso no está marcado como perdido"
    grupo = next(g for g in perdidas(db_path) if item in g["items"])
    ahora = ahora or ahora_utc()
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO perdidas_motivo (entidad, entidad_id, business_id, motivo, motivo_en, cargado_por) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(entidad, entidad_id) DO UPDATE SET "
            "motivo = excluded.motivo, motivo_en = excluded.motivo_en, "
            "cargado_por = excluded.cargado_por, "
            "business_id = COALESCE(excluded.business_id, perdidas_motivo.business_id)",
            (entidad, entidad_id, grupo["business_id"], motivo, _iso(ahora), quien))
        conn.commit()
    finally:
        conn.close()
    return None


def registrar_perdida_ficha(db_path: str, notion_page_id: str, ahora: datetime | None = None) -> None:
    """Anota el día en que una ficha pasó a perdida (lo llama `cliente_cambio_de_estado`).

    Sin motivo: queda como pérdida sin motivo hasta que alguien lo elija.
    """
    fichas = _q(db_path, "SELECT id, business_id FROM notion_clients WHERE notion_page_id = ?",
                (notion_page_id,))
    if not fichas:
        return
    hoy = hoy_de(ahora or ahora_utc()).isoformat()
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO perdidas_motivo (entidad, entidad_id, business_id, perdida_en) "
            "VALUES ('notion_client', ?, ?, ?) ON CONFLICT(entidad, entidad_id) DO UPDATE SET "
            "perdida_en = excluded.perdida_en, "
            "business_id = COALESCE(excluded.business_id, perdidas_motivo.business_id)",
            (fichas[0]["id"], fichas[0]["business_id"], hoy))
        conn.commit()
    finally:
        conn.close()


# ── esfuerzo, origen, fijos por canal y supuestos ────────────────────────────

def esfuerzos(db_path: str) -> dict[int, dict]:
    return {r["project_id"]: r for r in _q(db_path, "SELECT * FROM proyectos_esfuerzo")}


def guardar_esfuerzo(db_path: str, project_id: int, datos, quien: str = "",
                     ahora: datetime | None = None) -> tuple[dict | None, str | None]:
    if not isinstance(datos, dict):
        return None, "faltan los datos del esfuerzo"
    unidad = datos.get("unidad") or "horas"
    if unidad not in ("horas", "dias"):
        return None, "la unidad es horas o días"
    valor = _numero(datos.get("valor"))
    if valor is None or valor <= 0:
        return None, "el esfuerzo tiene que ser un número mayor que cero"
    if valor > ESFUERZO_MAX:
        return None, f"el esfuerzo no puede pasar de {ESFUERZO_MAX}"
    if not _q(db_path, "SELECT id FROM projects WHERE id = ?", (project_id,)):
        return None, "ese proyecto no existe"
    horas_por_dia = None
    if unidad == "dias":
        personas = [p for p in listar_personas_equipo(db_path) if p.get("lleva_horas")]
        if not personas:
            return None, ("para cargar días hace falta el equipo con horas por día en Equipo: "
                          "cargalo en horas")
        horas_por_dia = sum(float(p["horas_por_dia"]) for p in personas) / len(personas)
        horas = round(valor * horas_por_dia, 2)
    else:
        horas = round(valor, 2)
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO proyectos_esfuerzo (project_id, horas, valor_cargado, unidad_cargada, "
            "cargado_por, updated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(project_id) DO UPDATE SET "
            "horas = excluded.horas, valor_cargado = excluded.valor_cargado, "
            "unidad_cargada = excluded.unidad_cargada, cargado_por = excluded.cargado_por, "
            "updated_at = excluded.updated_at",
            (project_id, horas, valor, unidad, quien, _iso(ahora or ahora_utc())))
        conn.commit()
    finally:
        conn.close()
    return {"project_id": project_id, "horas": horas, "valor": valor, "unidad": unidad,
            "horas_por_dia": round(horas_por_dia, 2) if horas_por_dia else None}, None


def guardar_origen(db_path: str, entidad: str, entidad_id: int, canal, quien: str = "",
                   ahora: datetime | None = None) -> str | None:
    if entidad not in ("business", "notion_client"):
        return "no se reconoce la venta"
    if canal not in CANALES:
        return "elegí un canal: " + " / ".join(CANALES.values())
    if entidad == "business":
        marcas = ", ".join("?" for _ in ETAPAS_CLIENTE)
        existe = _q(db_path, f"SELECT id FROM businesses WHERE id = ? AND crm_status IN ({marcas})",
                    [entidad_id, *ETAPAS_CLIENTE])
    else:
        existe = _q(db_path, "SELECT id FROM notion_clients WHERE id = ?", (entidad_id,))
    if not existe:
        return "esa venta no existe"
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO ventas_origen_manual (entidad, entidad_id, canal, cargado_por, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(entidad, entidad_id) DO UPDATE SET "
            "canal = excluded.canal, cargado_por = excluded.cargado_por, updated_at = excluded.updated_at",
            (entidad, entidad_id, canal, quien, _iso(ahora or ahora_utc())))
        conn.commit()
    finally:
        conn.close()
    return None


def guardar_canal_fijo(db_path: str, recurrente_id: int, canal,
                       ahora: datetime | None = None) -> str | None:
    fijo = get_recurrente(db_path, recurrente_id)
    if not fijo or fijo["tipo"] != "egreso":
        return "ese gasto fijo no existe"
    if canal not in (None, "") and canal not in CANALES:
        return "elegí un canal: " + " / ".join(CANALES.values())
    conn = _connect(db_path)
    try:
        if not canal:
            conn.execute("DELETE FROM fijos_canal WHERE recurrente_id = ?", (recurrente_id,))
        else:
            conn.execute("INSERT INTO fijos_canal (recurrente_id, canal, updated_at) VALUES (?, ?, ?) "
                         "ON CONFLICT(recurrente_id) DO UPDATE SET canal = excluded.canal, "
                         "updated_at = excluded.updated_at",
                         (recurrente_id, canal, _iso(ahora or ahora_utc())))
        conn.commit()
    finally:
        conn.close()
    return None


def leer_supuestos(db_path: str) -> dict:
    return {r["clave"]: r["valor"] for r in _q(db_path, "SELECT clave, valor FROM if_supuestos")}


def guardar_supuesto(db_path: str, clave: str, valor, quien: str = "",
                     ahora: datetime | None = None) -> str | None:
    if clave not in SUPUESTOS:
        return "ese supuesto no existe"
    x = _numero(valor)
    if x is None or x < 0 or x >= 100:
        return "la comisión es un porcentaje entre 0 y 100"
    conn = _connect(db_path)
    try:
        conn.execute("INSERT INTO if_supuestos (clave, valor, updated_by, updated_at) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor, "
                     "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                     (clave, x, quien, _iso(ahora or ahora_utc())))
        conn.commit()
    finally:
        conn.close()
    return None


# ── mantenimiento (R1) ───────────────────────────────────────────────────────

def _fijos_mantenimiento(db_path: str, mes: str) -> list[tuple[dict, float]]:
    salida = []
    for r in listar_recurrentes(db_path, solo_activos=True):
        if r["tipo"] != "ingreso" or r["categoria"] != "mantenimiento":
            continue
        if (r["hasta"] and r["hasta"] < mes) or (r["desde"] and r["desde"] > mes):
            continue
        try:
            salida.append((r, a_usd(r["monto"], r["moneda"], r["tipo_cambio"])))
        except (TypeError, ValueError):
            continue
    return salida


def ingreso_mensual_mantenimiento(db_path: str, hoy: date, comision_pct) -> dict:
    """Los fijos de mantenimiento del mes, más los cobros sueltos de mantenimiento
    de los últimos 30 días que no vienen de un fijo."""
    fijos = _fijos_mantenimiento(db_path, f"{hoy:%Y-%m}")
    con_fijo = {r["client_id"] for r, _ in fijos if r["client_id"]}
    desde = (hoy - timedelta(days=DIAS_SEGUIMIENTO)).isoformat()
    sueltos = [m for m in listar_movimientos(db_path, tipo="ingreso", categoria="mantenimiento")
               if desde <= (m["fecha"] or "")[:10] <= hoy.isoformat() and not m["recurrente_id"]
               and (not m["client_id"] or m["client_id"] not in con_fijo)]
    bruto = round(sum(u for _, u in fijos) + sum(float(m["monto_usd"] or 0) for m in sueltos), 2)
    neto = round(bruto * (1 - float(comision_pct) / 100), 2) if comision_pct is not None else None
    return {"bruto": bruto, "neto": neto, "fijos": len(fijos), "sueltos": len(sueltos)}


# ── las reglas ───────────────────────────────────────────────────────────────

def _rec(regla, clave, titulo, detalle, lineas, impacto, metrica, acciones=None,
         advertencia="", unica_vez=False) -> dict:
    confianza, tipo = REGLAS[regla]
    return {"regla": regla, "clave": str(clave), "titulo": titulo, "detalle": detalle,
            "calculo": "\n".join(lineas),
            "impacto_mensual": round(impacto, 2) if impacto is not None else None,
            "unica_vez": bool(unica_vez), "confianza": confianza, "tipo": tipo,
            "advertencia": advertencia, "acciones": acciones or [], "metrica": metrica}


def _aviso(clave: str, reglas, cantidad=None) -> dict:
    titulo, detalle = AVISOS[clave]
    return {"clave": clave, "titulo": titulo, "detalle": detalle,
            "reglas": sorted(set(reglas)), "cantidad": cantidad}


def _unir_avisos(avisos: list[dict]) -> list[dict]:
    por_clave: dict[str, dict] = {}
    for a in avisos:
        if a["clave"] in por_clave:
            b = por_clave[a["clave"]]
            b["reglas"] = sorted(set(b["reglas"]) | set(a["reglas"]))
            if a["cantidad"] is not None:
                b["cantidad"] = max(b["cantidad"] or 0, a["cantidad"])
        else:
            por_clave[a["clave"]] = dict(a)
    return list(por_clave.values())


def _avisos_margen(ctx: dict, reglas) -> list[dict]:
    m = ctx["margen"]
    avisos = []
    if not m["con_esfuerzo"]:
        avisos.append(_aviso("esfuerzo", reglas, cantidad=len(m["proyectos"])))
    elif not m["filas"]:
        avisos.append(_aviso("precio", reglas, cantidad=m["con_esfuerzo"]))
    if m["costo_hora"]["valor"] is None:
        avisos.append(_aviso("costo_hora", reglas))
    return avisos


def _lineas_margen(ctx: dict) -> list[str]:
    m, ch = ctx["margen"], ctx["margen"]["costo_hora"]
    d, h = ctx["desde"], ctx["hasta"]
    return [
        f"Costo por hora = egresos de Finanzas sin pauta del {_dmy(d)} al {_dmy(h)} {usd(ch['egresos'])} "
        f"÷ {num(ch['horas'])} h del equipo ({ch['personas']} {_plural(ch['personas'], 'persona', 'personas')} "
        f"× {ch['dias']} días hábiles) = {usd(ch['valor'])}",
        f"Margen por venta = precio promedio {usd(m['precio_promedio'])} − esfuerzo promedio "
        f"{num(m['esfuerzo_con_precio'])} h × {usd(ch['valor'])} = {usd(m['margen_venta'])} "
        f"({len(m['filas'])} {_plural(len(m['filas']), 'proyecto', 'proyectos')} con esfuerzo y precio)",
    ]


def _lineas_capacidad(ctx: dict) -> list[str]:
    c, esf = ctx["capacidad"], ctx["margen"]["esfuerzo_promedio"]
    return [
        f"Capacidad del {_dmy(c['desde'])} al {_dmy(c['hasta'])}: {num(c['base'])} h − "
        f"{num(c['ausencia'])} h de ausencias = {num(c['netas'])} h netas (Equipo)",
        f"Comprometidas: {num(c['comprometidas'])} h en {c['en_curso']} "
        f"{_plural(c['en_curso'], 'proyecto', 'proyectos')} en curso → {num(c['libres_horas'])} h libres "
        f"÷ {num(esf)} h por proyecto (esfuerzo promedio) = {c['slots']} "
        f"{_plural(c['slots'], 'proyecto', 'proyectos')}",
    ]


def _r1(db_path: str, hoy: date, ctx: dict):
    desde, hasta = ctx["desde"], ctx["hasta"]
    clientes = listar_clientes_activos(db_path)
    if not clientes:
        return [], []
    fijos = _fijos_mantenimiento(db_path, f"{hoy:%Y-%m}")
    movs = [m for m in listar_movimientos(db_path, tipo="ingreso", categoria="mantenimiento")
            if desde.isoformat() <= (m["fecha"] or "")[:10] <= hasta.isoformat()]
    con_cuota = {r["client_id"] for r, _ in fijos if r["client_id"]} | \
                {m["client_id"] for m in movs if m["client_id"]}
    sin = [c for c in clientes if c["id"] not in con_cuota]
    if not sin:
        return [], []
    avisos = []
    if fijos:
        cuotas = [u for _, u in fijos]
        fuente = f"{len(cuotas)} {_plural(len(cuotas), 'ingreso fijo', 'ingresos fijos')} de mantenimiento en Finanzas"
    else:
        cuotas = [float(m["monto_usd"] or 0) for m in movs if m["client_id"]]
        fuente = (f"{len(cuotas)} {_plural(len(cuotas), 'cobro', 'cobros')} de mantenimiento en Finanzas "
                  f"del {_dmy(desde)} al {_dmy(hasta)}")
    if not cuotas:
        avisos.append(_aviso("cuota", ["R1"]))
    comision = ctx["supuestos"].get("comision_cobro_pct")
    if comision is None:
        avisos.append(_aviso("comision", ["R1"]))
    if avisos:
        return [], avisos

    n = len(sin)
    suma = round(sum(cuotas), 2)
    cuota = round(suma / len(cuotas), 2)
    impacto = n * cuota * (1 - comision / 100)
    nombres = ", ".join(c["name"] for c in sin[:6]) + (" y otros" if n > 6 else "")
    lineas = [
        f"Clientes activos (Clientes): {len(clientes)}; con cuota de mantenimiento: "
        f"{len(clientes) - n}; sin cuota: {n}",
        f"Cuota promedio = {usd(suma)} ÷ {len(cuotas)} = {usd(cuota)} ({fuente})",
        f"Comisión de cobro: {num(comision)} % (Supuestos)",
        f"Impacto = {n} × {usd(cuota)} × (1 − {num(comision)} %) = {usd(impacto)} por mes",
    ]
    base = ingreso_mensual_mantenimiento(db_path, hoy, comision)
    return [_rec(
        "R1", "mantenimiento",
        f"Cobrales mantenimiento a los {n} {_plural(n, 'cliente', 'clientes')} que ya tenés",
        f"De {len(clientes)} clientes activos, {n} no pagan cuota mensual: {nombres}. Ya confían en "
        f"vos, así que es ingreso recurrente sin salir a buscar clientes nuevos.",
        lineas, impacto,
        {"mensual": base["neto"], "comision_pct": comision},
    )], []


def _r2_r3(db_path: str, hoy: date, ctx: dict):
    reglas = ("R2", "R3")
    avisos = _avisos_margen(ctx, reglas)
    if ctx["capacidad"] is None:
        avisos.append(_aviso("equipo", reglas))
    if ctx["sin_origen_ventana"]:
        avisos.append(_aviso("origen", reglas, cantidad=len(ctx["sin_origen_ventana"])))
    if ctx["pauta"]["monedas_raras"]:
        avisos.append(_aviso("moneda_pauta", reglas))
    if avisos:
        return [], avisos

    margen = ctx["margen"]["margen_venta"]
    slots = ctx["capacidad"]["slots"]
    if slots is None:
        return [], []
    gasto = ctx["pauta"]["gasto"]
    x = gasto / VENTANA_MESES
    meta = [v for v in ctx["ventas_ventana"] if v["canal"] == "meta_ads"]
    cac = gasto / len(meta) if meta and gasto > 0 else None
    d, h = _dmy(ctx["desde"]), _dmy(ctx["hasta"])
    lineas_pauta = [f"Pauta de Meta del {d} al {h}: {usd(gasto)} ÷ {VENTANA_MESES} meses = {usd(x)} por mes "
                    f"({ctx['pauta']['leads']} leads de Meta)"]
    if cac is not None:
        lineas_pauta.append(f"Ventas de Meta Ads en ese período: {len(meta)} → costo por venta = "
                            f"{usd(gasto)} ÷ {len(meta)} = {usd(cac)}")
    else:
        lineas_pauta.append(f"Ventas de Meta Ads en ese período: {len(meta)}")

    if cac is not None and margen > cac:
        vp = x / cac
        libre = math.floor(slots - vp + _EPS)
        if libre < 1:
            return [], []
        y = x + libre * cac
        impacto = libre * (margen - cac)
        lineas = lineas_pauta + _lineas_margen(ctx) + _lineas_capacidad(ctx) + [
            f"La pauta actual ya trae {usd(x)} ÷ {usd(cac)} = {num(vp)} ventas por mes → capacidad libre = "
            f"{slots} − {num(vp)} = {libre} (redondeado para abajo)",
            f"Pauta recomendada = {usd(x)} + {libre} × {usd(cac)} = {usd(y)}",
            f"Impacto = {libre} × ({usd(margen)} − {usd(cac)}) = {usd(impacto)} por mes",
        ]
        return [_rec(
            "R2", "meta_ads", f"Subí la pauta de {usd(x)} a {usd(y)}, no más",
            f"Cada venta de Meta Ads deja {usd(margen)} de margen y conseguirla cuesta {usd(cac)}: "
            f"conviene invertir más. Pero el tope lo pone el equipo, no el presupuesto: este mes hay lugar "
            f"para {libre} {_plural(libre, 'venta más', 'ventas más')}.",
            lineas, impacto,
            {"canal": "meta_ads", "ventas_mes": len(meta) / VENTANA_MESES, "margen_menos_cac": margen - cac},
            advertencia=(f"No pases de {usd(y)}: cada {usd(cac)} de más trae una venta que el equipo no puede "
                         f"hacer este mes. Pasarte en una venta tira {usd(cac)}; en dos, tira {usd(2 * cac)}."),
        )], []

    vp = x / cac if cac else 0.0
    if slots < 2 or vp >= slots or margen <= 0:
        return [], []
    ociosa = math.floor(slots - vp + _EPS)
    if ociosa < 1:
        return [], []
    impacto = ociosa * margen
    por_que = ("la pauta de hoy no trajo ventas en 3 meses" if cac is None
               else f"cada venta de Meta cuesta {usd(cac)} y deja {usd(margen)}, así que subir la pauta no conviene")
    lineas = lineas_pauta + _lineas_margen(ctx) + _lineas_capacidad(ctx) + [
        f"Ventas por mes con la pauta actual: {num(vp)}" + (f" ({usd(x)} ÷ {usd(cac)})" if cac else ""),
        f"Capacidad ociosa = {slots} − {num(vp)} = {ociosa} (redondeado para abajo)",
        f"Impacto = {ociosa} × margen por venta {usd(margen)} = {usd(impacto)} por mes",
    ]
    return [_rec(
        "R3", "capacidad",
        f"Te {_plural(ociosa, 'sobra', 'sobran')} {ociosa} {_plural(ociosa, 'proyecto', 'proyectos')} "
        f"de capacidad y la pauta no alcanza",
        f"El equipo tiene lugar para {slots} proyectos este mes y {por_que}. Esa capacidad se paga igual: "
        f"conseguí ventas para llenarla (outbound, referidos o una pauta distinta).",
        lineas, impacto,
        {"ventas_mes": len(ctx["ventas_ventana"]) / VENTANA_MESES, "margen": margen},
    )], []


def _r4(db_path: str, hoy: date, ctx: dict):
    from services.plantillas import variables_del_lead
    from services.seg_leads import telefonos

    vencidos = [p for p in listar_por_cobrar(db_path)
                if _fecha(p["vence"]) and _fecha(p["vence"]) < hoy and float(p["monto_usd"] or 0) > 0]
    if not vencidos:
        return [], []
    grupos: dict = {}
    for p in vencidos:
        grupos.setdefault(p["client_id"] or f"sin-{p['id']}", []).append(p)
    total = round(sum(float(p["monto_usd"]) for p in vencidos), 2)
    lineas, acciones = [], []
    for clave, suyos in grupos.items():
        nombre = suyos[0].get("client_name") or suyos[0]["concepto"]
        for p in suyos:
            lineas.append(f"{nombre} · {p['concepto']}: {usd(p['monto_usd'])}, venció el {_dmy(_fecha(p['vence']))}")
        monto = sum(float(p["monto_usd"]) for p in suyos)
        vence = min(_fecha(p["vence"]) for p in suyos)
        wa, saludo = None, ""
        cid = suyos[0]["client_id"]
        if cid:
            negocio = _q(db_path, "SELECT phone FROM businesses WHERE id = ?", (cid,))
            wa = telefonos(negocio[0]["phone"])[1] if negocio else None
            variables = variables_del_lead(db_path, cid)
            saludo = (variables or {}).get("valores", {}).get("nombre", "") if variables else ""
        texto = (f"Hola{(' ' + saludo) if saludo else ''}, ¿cómo estás? Te escribimos de Scalerics por el "
                 f"saldo de {usd(monto)} que venció el {_dmy(vence)}. ¿Nos confirmás cuándo lo podés "
                 f"abonar? Gracias.")
        acciones.append({"cliente": nombre, "monto": round(monto, 2), "vence": vence.isoformat(),
                         "wa": wa, "texto": texto})
    lineas.append(f"Total vencido = {' + '.join(usd(p['monto_usd']) for p in vencidos)} = {usd(total)}, "
                  f"por única vez (Finanzas, Por cobrar)")
    n = len(grupos)
    return [_rec(
        "R4", "vencidos", f"Cobrá los {usd(total)} vencidos",
        f"{len(vencidos)} {_plural(len(vencidos), 'saldo ya pasó', 'saldos ya pasaron')} su fecha de cobro "
        f"({n} {_plural(n, 'cliente', 'clientes')}). Es plata que ya ganaste: mandá el mensaje de cobranza hoy.",
        lineas, total, {"ids": [p["id"] for p in vencidos], "monto": total},
        acciones=acciones, unica_vez=True,
    )], []


def _r5(db_path: str, hoy: date, ctx: dict):
    asignados = {r["recurrente_id"]: r["canal"] for r in _q(db_path, "SELECT * FROM fijos_canal")}
    if not asignados:
        return [], []
    mes = f"{hoy:%Y-%m}"
    fijos = [r for r in listar_recurrentes(db_path, solo_activos=True)
             if r["id"] in asignados and r["tipo"] == "egreso" and not (r["hasta"] and r["hasta"] < mes)]
    if not fijos:
        return [], []
    if ctx["sin_origen_ventana"]:
        return [], [_aviso("origen", ["R5"], cantidad=len(ctx["sin_origen_ventana"]))]
    por_canal: dict[str, list] = {}
    for r in fijos:
        por_canal.setdefault(asignados[r["id"]], []).append(r)
    recs = []
    for canal, suyos in por_canal.items():
        vendidas = [v for v in ctx["ventas_ventana"] if v["canal"] == canal]
        if vendidas:
            continue
        partes, ids, montos, sin_tc = [], [], {}, []
        for r in suyos:
            try:
                u = a_usd(r["monto"], r["moneda"], r["tipo_cambio"])
            except (TypeError, ValueError):
                sin_tc.append(r["concepto"])
                continue
            partes.append((r["concepto"], u))
            ids.append(r["id"])
            montos[str(r["id"])] = u
        if not partes:
            continue
        total = round(sum(u for _, u in partes), 2)
        lineas = [f"Fijos de {CANALES[canal]}: " + " + ".join(f"{c} {usd(u)}" for c, u in partes)
                  + f" = {usd(total)} por mes (Finanzas, Fijos)",
                  f"Ventas atribuidas a {CANALES[canal]} del {_dmy(ctx['desde'])} al {_dmy(ctx['hasta'])}: 0 "
                  f"(de {len(ctx['ventas_ventana'])} ventas con origen)"]
        if sin_tc:
            lineas.append("Sin sumar, en pesos sin tipo de cambio: " + ", ".join(sin_tc))
        recs.append(_rec(
            "R5", canal, f"Probá un mes sin los gastos fijos de {CANALES[canal]}",
            f"En los últimos 3 meses ninguna venta vino de {CANALES[canal]} y sus gastos fijos siguen "
            f"saliendo todos los meses.",
            lineas, total, {"ids": ids, "montos": montos, "monto": total},
            advertencia=ADVERTENCIA_R5,
        ))
    return recs, []


def _r6(db_path: str, hoy: date, ctx: dict):
    avisos = _avisos_margen(ctx, ["R6"])
    if avisos:
        return [], avisos
    m = ctx["margen"]
    ch = m["costo_hora"]["valor"]
    por_tipo: dict[str, list] = {}
    for f in m["filas"]:
        if f["tipo"]:
            por_tipo.setdefault(f["tipo"], []).append(f)
    recs = []
    for tipo, filas in por_tipo.items():
        precio = round(sum(f["precio"] for f in filas), 2)
        margen = round(sum(f["margen"] for f in filas), 2)
        pct = margen / precio if precio else None
        if pct is None or pct >= MARGEN_MINIMO:
            continue
        etiqueta = ETIQUETA_TIPO.get(tipo, tipo)
        lineas = [_lineas_margen(ctx)[0]] + [
            f"{f['nombre']}: {usd(f['precio'])} ({f['fuente_precio']}) − {num(f['horas'])} h × {usd(ch)} "
            f"= {usd(f['margen'])}" for f in filas
        ] + [f"Margen del tipo {etiqueta} = {usd(margen)} ÷ {usd(precio)} = {num(pct * 100)} % "
             f"(piso: {num(MARGEN_MINIMO * 100)} %; tipo según {filas[0]['fuente_tipo']})"]
        recs.append(_rec(
            "R6", tipo, f"Los proyectos de tipo {etiqueta} te dejan {num(pct * 100)} %",
            f"Revisá el precio de los proyectos de tipo {etiqueta} antes de vender el próximo. "
            f"No abandones la línea.",
            lineas, None, {"tipo": tipo, "margen_pct": round(pct * 100, 2)},
            advertencia=ADVERTENCIA_R6,
        ))
    return recs, []


def _r7(db_path: str, hoy: date, ctx: dict):
    desde, hasta = ctx["desde"], ctx["hasta"]
    en_ventana = [g for g in ctx["perdidas"] if g["fecha"] and desde <= g["fecha"] <= hasta]
    if not en_ventana:
        return [], []
    avisos = []
    sin = [g for g in en_ventana if not g["motivo"]]
    if sin:
        avisos.append(_aviso("motivo", ["R7"], cantidad=len(sin)))
    avisos += _avisos_margen(ctx, ["R7"])
    if avisos:
        return [], avisos
    cuenta = Counter(g["motivo"] for g in en_ventana)
    motivo, n = cuenta.most_common(1)[0]
    total = len(en_ventana)
    share = n / total
    if share <= CONCENTRACION_MOTIVO:
        return [], []
    margen = ctx["margen"]["margen_venta"]
    impacto = n / VENTANA_MESES * margen * RECUPERO_MOTIVO
    lineas = [
        f"Pérdidas del {_dmy(desde)} al {_dmy(hasta)}: {total}; por «{MOTIVOS[motivo]}»: {n} → "
        f"{n} ÷ {total} = {num(share * 100)} % (umbral: {num(CONCENTRACION_MOTIVO * 100)} %)",
        _lineas_margen(ctx)[1],
        f"Impacto = {n} pérdidas ÷ {VENTANA_MESES} meses × {usd(margen)} × "
        f"{num(RECUPERO_MOTIVO * 100)} % de recupero = {usd(impacto)} por mes",
    ]
    return [_rec(
        "R7", motivo, f"El {num(share * 100, 0)} % de lo que perdés es por {MOTIVOS[motivo].lower()}",
        f"{SUGERENCIA_R7[motivo]} Si recuperás 3 de cada 10 de esas ventas, suma lo de la derecha.",
        lineas, impacto,
        {"motivo": motivo, "perdidas_mes": n / VENTANA_MESES, "margen": margen},
    )], []


# ── cálculo completo ─────────────────────────────────────────────────────────

def _contexto(db_path: str, hoy: date) -> dict:
    desde, hasta = ventana(hoy)
    lista = ventas(db_path)
    en_ventana = [v for v in lista if v["fecha"] and desde <= v["fecha"] <= hasta]
    m = margenes(db_path, hoy)
    return {
        "hoy": hoy, "desde": desde, "hasta": hasta,
        "ventas": lista,
        "ventas_ventana": [v for v in en_ventana if v["canal"]],
        "sin_origen_ventana": [v for v in en_ventana if not v["canal"]],
        "margen": m,
        "capacidad": capacidad_mes(db_path, hoy, m["proyectos"], m["esfuerzo_promedio"]),
        "pauta": pauta_meta(db_path, desde, hasta),
        "perdidas": perdidas(db_path),
        "supuestos": leer_supuestos(db_path),
    }


def calcular(db_path: str, hoy: date) -> dict:
    """Todas las recomendaciones candidatas y los avisos, sin filtrar."""
    ctx = _contexto(db_path, hoy)
    recs, avisos = [], []
    for regla in (_r1, _r2_r3, _r4, _r5, _r6, _r7):
        r, a = regla(db_path, hoy, ctx)
        recs += r
        avisos += a
    return {"recomendaciones": recs, "avisos": _unir_avisos(avisos), "contexto": ctx}


def ordenar(recs: list[dict]) -> list[dict]:
    """Umbral de USD 100, de mayor a menor impacto, máximo 6. Lo que no se estima
    en plata (R6, "revisar") no es obvio: entra, al final."""
    visibles = [r for r in recs
                if r["impacto_mensual"] is None or r["impacto_mensual"] >= UMBRAL_IMPACTO_USD]
    visibles.sort(key=lambda r: (r["impacto_mensual"] is None, -(r["impacto_mensual"] or 0)))
    return visibles[:MAX_RECOMENDACIONES]


def encabezado(db_path: str, hoy: date, visibles: list[dict]) -> dict:
    mes = f"{hoy:%Y-%m}"
    ingresos, egresos = _totales(listar_movimientos(db_path, desde=mes, hasta=mes))
    neto = round(ingresos - egresos, 2)
    primeras = [r for r in visibles[:3] if r["impacto_mensual"] is not None]
    con = round(neto + sum(r["impacto_mensual"] for r in primeras), 2)
    calculo_hoy = f"Ingresos {usd(ingresos)} − egresos {usd(egresos)} = {usd(neto)} (Finanzas, mes en curso)"
    calculo_con = (f"{usd(neto)} + " + " + ".join(usd(r["impacto_mensual"]) for r in primeras) + f" = {usd(con)}"
                   if primeras else "Todavía no hay recomendaciones con impacto en plata.")
    return {"mes": mes, "ingresos": ingresos, "egresos": egresos, "hoy": neto, "con_tres": con,
            "calculo_hoy": calculo_hoy, "calculo_con_tres": calculo_con}


def _resumen_contexto(ctx: dict) -> dict:
    return {"desde": ctx["desde"].isoformat(), "hasta": ctx["hasta"].isoformat(),
            "ventas": len(ctx["ventas_ventana"]) + len(ctx["sin_origen_ventana"]),
            "pauta": ctx["pauta"]["gasto"], "proyectos_con_esfuerzo": ctx["margen"]["con_esfuerzo"]}


def _suprimidas(conn, hoy: date) -> set:
    """Lo que no se vuelve a sugerir: lo tomado que se está midiendo, y lo
    descartado este mes."""
    sup = {(f["regla"], f["clave"]) for f in conn.execute(
        "SELECT r.regla, r.clave FROM if_recomendaciones r JOIN if_recomendaciones_tomadas t "
        "ON t.recomendacion_id = r.id WHERE t.resultado = 'midiendo'")}
    for f in conn.execute("SELECT regla, clave, descartada_en FROM if_recomendaciones "
                          "WHERE estado = 'descartada'"):
        cuando = _parse_iso(f["descartada_en"])
        if cuando and f"{hoy_de(cuando):%Y-%m}" == f"{hoy:%Y-%m}":
            sup.add((f["regla"], f["clave"]))
    return sup


def recalcular(db_path: str, ahora: datetime | None = None) -> int:
    ahora = ahora or ahora_utc()
    hoy = hoy_de(ahora)
    calc = calcular(db_path, hoy)
    conn = _connect(db_path)
    try:
        sup = _suprimidas(conn, hoy)
    finally:
        conn.close()
    visibles = ordenar([r for r in calc["recomendaciones"] if (r["regla"], r["clave"]) not in sup])
    enc = encabezado(db_path, hoy, visibles)
    generada = _iso(ahora)
    conn = _connect(db_path)
    try:
        cid = conn.execute(
            "INSERT INTO if_calculos (generada_en, avisos, encabezado, contexto) VALUES (?, ?, ?, ?)",
            (generada, json.dumps(calc["avisos"], ensure_ascii=False),
             json.dumps(enc, ensure_ascii=False),
             json.dumps(_resumen_contexto(calc["contexto"]), ensure_ascii=False))).lastrowid
        for r in visibles:
            conn.execute(
                "INSERT INTO if_recomendaciones (calculo_id, regla, clave, titulo, detalle, calculo, "
                "impacto_mensual, unica_vez, confianza, tipo, advertencia, acciones, metrica, generada_en) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cid, r["regla"], r["clave"], r["titulo"], r["detalle"], r["calculo"],
                 r["impacto_mensual"], 1 if r["unica_vez"] else 0, r["confianza"], r["tipo"],
                 r["advertencia"], json.dumps(r["acciones"], ensure_ascii=False),
                 json.dumps(r["metrica"], ensure_ascii=False), generada))
        conn.commit()
    finally:
        conn.close()
    return cid


# ── tomar, descartar y medir a los 30 días ───────────────────────────────────

def _rec_por_id(db_path: str, rec_id: int) -> dict | None:
    filas = _q(db_path, "SELECT * FROM if_recomendaciones WHERE id = ?", (rec_id,))
    return filas[0] if filas else None


def tomar(db_path: str, rec_id: int, quien: str = "", ahora: datetime | None = None):
    """(True, None) o (False, (código HTTP, mensaje))."""
    rec = _rec_por_id(db_path, rec_id)
    if not rec:
        return False, (404, "esa recomendación no existe")
    if rec["estado"] != "nueva":
        return False, (409, "esa recomendación ya se tomó o se descartó")
    conn = _connect(db_path)
    try:
        conn.execute("UPDATE if_recomendaciones SET estado = 'tomada' WHERE id = ?", (rec_id,))
        conn.execute("INSERT INTO if_recomendaciones_tomadas (recomendacion_id, tomada_en, tomada_por, "
                     "impacto_esperado) VALUES (?, ?, ?, ?)",
                     (rec_id, _iso(ahora or ahora_utc()), quien, rec["impacto_mensual"]))
        conn.commit()
    finally:
        conn.close()
    return True, None


def descartar(db_path: str, rec_id: int, ahora: datetime | None = None):
    rec = _rec_por_id(db_path, rec_id)
    if not rec:
        return False, (404, "esa recomendación no existe")
    if rec["estado"] != "nueva":
        return False, (409, "esa recomendación ya se tomó o se descartó")
    conn = _connect(db_path)
    try:
        conn.execute("UPDATE if_recomendaciones SET estado = 'descartada', descartada_en = ? WHERE id = ?",
                     (_iso(ahora or ahora_utc()), rec_id))
        conn.commit()
    finally:
        conn.close()
    return True, None


def medir(db_path: str, regla: str, metrica: dict, desde: date, hasta: date) -> tuple[float, str]:
    """(impacto real, cómo se midió). La misma métrica que usó la regla."""
    if regla == "R1":
        actual = ingreso_mensual_mantenimiento(db_path, hasta, metrica.get("comision_pct") or 0)["neto"]
        base = metrica.get("mensual") or 0
        return actual - base, (f"Ingreso mensual por mantenimiento neto de comisión: {usd(base)} al tomarla "
                               f"→ {usd(actual)} el {_dmy(hasta)}")
    if regla in ("R2", "R3"):
        todas = [v for v in ventas(db_path) if v["fecha"] and desde <= v["fecha"] <= hasta]
        if regla == "R2":
            todas = [v for v in todas if v["canal"] == metrica.get("canal")]
            factor, rotulo = metrica.get("margen_menos_cac") or 0, "margen − costo por venta"
        else:
            factor, rotulo = metrica.get("margen") or 0, "margen por venta"
        base = metrica.get("ventas_mes") or 0
        real = (len(todas) - base) * factor
        return real, (f"Ventas del {_dmy(desde)} al {_dmy(hasta)}: {len(todas)} contra {num(base)} por mes al "
                      f"tomarla → ({len(todas)} − {num(base)}) × {rotulo} {usd(factor)} = {usd(real)}")
    if regla == "R4":
        ids = [int(i) for i in metrica.get("ids") or []]
        cobrados = _q(db_path, "SELECT monto_usd FROM finanzas_por_cobrar WHERE cobrado_movimiento_id "
                               f"IS NOT NULL AND id IN ({', '.join('?' for _ in ids) or 'NULL'})", ids)
        real = sum(float(c["monto_usd"] or 0) for c in cobrados)
        return real, (f"Cobrados {len(cobrados)} de {len(ids)} saldos vencidos: {usd(real)} de "
                      f"{usd(metrica.get('monto'))}")
    if regla == "R5":
        mes = f"{hasta:%Y-%m}"
        real, cortados = 0.0, 0
        for rid, monto in (metrica.get("montos") or {}).items():
            fijo = get_recurrente(db_path, int(rid))
            if not fijo or not fijo["activo"] or (fijo["hasta"] and fijo["hasta"] < mes):
                real += float(monto)
                cortados += 1
        return real, (f"Fijos apagados o terminados: {cortados} de {len(metrica.get('montos') or {})}, "
                      f"{usd(real)} por mes")
    if regla == "R6":
        tipo = metrica.get("tipo")
        filas = [f for f in margenes(db_path, hasta)["filas"] if f["tipo"] == tipo and f["margen"] is not None]
        precio = sum(f["precio"] for f in filas)
        actual = (sum(f["margen"] for f in filas) / precio * 100) if precio else 0.0
        base = metrica.get("margen_pct") or 0
        return actual - base, f"Margen del tipo: {num(base)} % al tomarla → {num(actual)} % el {_dmy(hasta)}"
    if regla == "R7":
        motivo = metrica.get("motivo")
        n = len([g for g in perdidas(db_path)
                 if g["motivo"] == motivo and g["fecha"] and desde <= g["fecha"] <= hasta])
        base = metrica.get("perdidas_mes") or 0
        margen = metrica.get("margen") or 0
        real = (base - n) * margen
        return real, (f"Pérdidas por «{MOTIVOS.get(motivo, motivo)}» del {_dmy(desde)} al {_dmy(hasta)}: {n} "
                      f"contra {num(base)} por mes al tomarla → ({num(base)} − {n}) × {usd(margen)} = {usd(real)}")
    return 0.0, ""


def evaluar_seguimiento(db_path: str, ahora: datetime | None = None) -> int:
    ahora = ahora or ahora_utc()
    limite = _iso(ahora - timedelta(days=DIAS_SEGUIMIENTO))
    filas = _q(db_path, "SELECT t.id, t.tomada_en, t.impacto_esperado, r.regla, r.metrica "
                        "FROM if_recomendaciones_tomadas t JOIN if_recomendaciones r "
                        "ON r.id = t.recomendacion_id WHERE t.resultado = 'midiendo' AND t.tomada_en <= ?",
               (limite,))
    for f in filas:
        tomada = _parse_iso(f["tomada_en"])
        desde = hoy_de(tomada)
        hasta = desde + timedelta(days=DIAS_SEGUIMIENTO)
        try:
            metrica = json.loads(f["metrica"] or "{}")
        except ValueError:
            metrica = {}
        real, detalle = medir(db_path, f["regla"], metrica, desde, hasta)
        if f["regla"] == "R6":
            funciono = (metrica.get("margen_pct") or 0) + real >= MARGEN_MINIMO * 100
        elif f["impacto_esperado"] and f["impacto_esperado"] > 0:
            funciono = real >= f["impacto_esperado"] * FUNCIONO_SI_LLEGA_A
        else:
            funciono = real > 0
        conn = _connect(db_path)
        try:
            conn.execute("UPDATE if_recomendaciones_tomadas SET impacto_real = ?, resultado = ?, "
                         "evaluada_en = ?, detalle_real = ? WHERE id = ?",
                         (round(real, 2), "funciono" if funciono else "no_funciono", _iso(ahora),
                          detalle, f["id"]))
            conn.commit()
        finally:
            conn.close()
    return len(filas)


# ── una vez por día ──────────────────────────────────────────────────────────

_LOCK = threading.Lock()


def ya_corrio_hoy(db_path: str, ahora: datetime | None = None) -> bool:
    """Si ya hubo un recálculo en el día de hoy (hora de Montevideo)."""
    ahora = ahora or ahora_utc()
    filas = _q(db_path, "SELECT ultima FROM corridas WHERE nombre = ?", (CORRIDA,))
    ultima = _parse_iso(filas[0]["ultima"]) if filas else None
    return ultima is not None and hoy_de(ultima) == hoy_de(ahora)


def _marcar(db_path: str, ahora: datetime) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("INSERT INTO corridas (nombre, ultima) VALUES (?, ?) "
                     "ON CONFLICT(nombre) DO UPDATE SET ultima = excluded.ultima", (CORRIDA, _iso(ahora)))
        conn.commit()
    finally:
        conn.close()


def corrida_diaria(db_path: str, ahora: datetime | None = None, forzar: bool = False) -> dict | None:
    """Mide lo tomado hace 30 días y recalcula. None si ya corrió hoy.

    No manda nada ni llama afuera: un arranque de más solo lee y escribe estas
    tablas, y la marca de "ya corrió hoy" evita repetirlo en cada deploy.
    `forzar` es el botón "Recalcular ahora" de un admin.
    """
    ahora = ahora or ahora_utc()
    with _LOCK:
        if not forzar and ya_corrio_hoy(db_path, ahora):
            return None
        evaluadas = evaluar_seguimiento(db_path, ahora)
        calculo_id = recalcular(db_path, ahora)
        _marcar(db_path, ahora)
    return {"calculo_id": calculo_id, "evaluadas": evaluadas}


def start_inteligencia_fin(app) -> None:
    """Hilo que revisa cada hora si hay que correr la tanda del día. No bloquea
    el arranque: espera dos minutos y todo lo que hace va dentro de un try."""
    def _loop():
        time.sleep(120)
        while True:
            try:
                corrida_diaria(app.config["DB_PATH"])
            except Exception:
                logger.warning("inteligencia financiera: falló la corrida diaria", exc_info=True)
            time.sleep(3600)

    threading.Thread(target=_loop, daemon=True, name="inteligencia-fin").start()


# ── lo que pinta la pantalla ─────────────────────────────────────────────────

def estado_datos(db_path: str, hoy: date) -> dict:
    """El estado de los tres datos previos, calculado en vivo (es barato)."""
    desde, hasta = ventana(hoy)
    grupos = perdidas(db_path)
    sin_motivo = [g for g in grupos if not g["motivo"]]
    sin_motivo.sort(key=lambda g: (g["fecha"] is None, -(g["fecha"].toordinal() if g["fecha"] else 0)))
    proys = proyectos(db_path)
    con_esfuerzo = [p for p in proys if p["horas"]]
    sin_esfuerzo = sorted([p for p in proys if not p["horas"]], key=lambda p: not p["terminado"])
    lista_ventas = ventas(db_path)
    sin_origen = [v for v in lista_ventas if not v["canal"]]
    cobros_sin_cliente = len([m for m in listar_movimientos(db_path, tipo="ingreso")
                              if not m["client_id"]
                              and desde.isoformat() <= (m["fecha"] or "")[:10] <= hasta.isoformat()])
    canal_de = {r["recurrente_id"]: r["canal"] for r in _q(db_path, "SELECT * FROM fijos_canal")}
    fijos = []
    for r in listar_recurrentes(db_path, solo_activos=True):
        if r["tipo"] != "egreso":
            continue
        try:
            monto = a_usd(r["monto"], r["moneda"], r["tipo_cambio"])
        except (TypeError, ValueError):
            monto = None
        fijos.append({"id": r["id"], "concepto": r["concepto"], "categoria": r["categoria"],
                      "monto_usd": monto, "canal": canal_de.get(r["id"], "")})
    supuestos = leer_supuestos(db_path)
    return {
        "motivo": {"completo": not sin_motivo, "perdidas": len(grupos), "sin_motivo": len(sin_motivo),
                   "pendientes": [{"entidad": g["entidad"], "entidad_id": g["entidad_id"],
                                   "nombre": g["nombre"], "estado": g["estado"],
                                   "fecha": g["fecha"].isoformat() if g["fecha"] else None}
                                  for g in sin_motivo[:LISTA_MAX]]},
        "esfuerzo": {"completo": bool(con_esfuerzo) and not [p for p in proys if p["terminado"] and not p["horas"]],
                     "proyectos": len(proys), "con_esfuerzo": len(con_esfuerzo),
                     "pendientes": [{"id": p["id"], "nombre": p["name"], "stage": p["stage"] or "",
                                     "terminado": p["terminado"]} for p in sin_esfuerzo[:LISTA_MAX]]},
        "origen": {"completo": not sin_origen, "ventas": len(lista_ventas), "sin_origen": len(sin_origen),
                   "cobros_sin_cliente": cobros_sin_cliente,
                   "pendientes": [{"entidad": v["entidad"], "id": v["id"], "nombre": v["nombre"],
                                   "source": v["source"],
                                   "sin_lead": v["entidad"] == "notion_client"}
                                  for v in sin_origen[:LISTA_MAX]]},
        "fijos": fijos,
        "supuestos": {c: {"etiqueta": e, "valor": supuestos.get(c)} for c, e in SUPUESTOS.items()},
    }


def _ultimo_calculo(db_path: str) -> dict | None:
    filas = _q(db_path, "SELECT * FROM if_calculos ORDER BY id DESC LIMIT 1")
    return filas[0] if filas else None


def _texto_momento(iso: str) -> str:
    dt = _parse_iso(iso)
    return dt.astimezone(_MVD).strftime("%d/%m/%Y %H:%M") if dt else ""


def estado_pantalla(db_path: str, es_admin: bool = False, ahora: datetime | None = None) -> dict:
    ahora = ahora or ahora_utc()
    hoy = hoy_de(ahora)
    if not ya_corrio_hoy(db_path, ahora):
        # La primera carga del día, si el hilo todavía no corrió (recién
        # deployado). Las demás cargas leen lo guardado.
        corrida_diaria(db_path, ahora)
    calc = _ultimo_calculo(db_path)
    recs, avisos, enc, ctx = [], [], {}, {}
    if calc:
        avisos = json.loads(calc["avisos"] or "[]")
        enc = json.loads(calc["encabezado"] or "{}")
        ctx = json.loads(calc["contexto"] or "{}")
        for r in _q(db_path, "SELECT * FROM if_recomendaciones WHERE calculo_id = ? AND estado = 'nueva'",
                    (calc["id"],)):
            r["acciones"] = json.loads(r["acciones"] or "[]")
            r["unica_vez"] = bool(r["unica_vez"])
            r.pop("metrica", None)
            recs.append(r)
        recs = ordenar(recs)
    seguimiento = []
    for t in _q(db_path, "SELECT t.*, r.titulo, r.regla, r.tipo, r.unica_vez FROM if_recomendaciones_tomadas t "
                         "JOIN if_recomendaciones r ON r.id = t.recomendacion_id ORDER BY t.tomada_en DESC, t.id DESC "
                         "LIMIT ?", (LISTA_MAX,)):
        tomada = _parse_iso(t["tomada_en"])
        t["tomada_el"] = _dmy(hoy_de(tomada)) if tomada else ""
        t["se_mide_el"] = _dmy(hoy_de(tomada) + timedelta(days=DIAS_SEGUIMIENTO)) if tomada else ""
        t["unica_vez"] = bool(t["unica_vez"])
        seguimiento.append(t)
    desde = _fecha(ctx.get("desde"))
    hasta = _fecha(ctx.get("hasta"))
    bajada = ""
    if calc:
        bajada = (f"Calculado el {_texto_momento(calc['generada_en'])} con los datos del {_dmy(desde)} al "
                  f"{_dmy(hasta)}: pauta de Meta, ventas y pérdidas (Proceso de venta, Demos, Clientes), "
                  f"esfuerzo de Proyectos, capacidad de Equipo, y gastos y cobros de Finanzas. Montos en "
                  f"dólares (USD), con la misma conversión que Finanzas. Se recalcula una vez por día.")
    return {
        "es_admin": bool(es_admin),
        "generada_en": calc["generada_en"] if calc else None,
        "bajada": bajada,
        "encabezado": enc,
        "avisos": avisos,
        "recomendaciones": recs,
        "seguimiento": seguimiento,
        "datos": estado_datos(db_path, hoy),
        "motivos": [{"clave": c, "etiqueta": e} for c, e in MOTIVOS.items()],
        "canales": [{"clave": c, "etiqueta": e} for c, e in CANALES.items()],
        "umbral_usd": UMBRAL_IMPACTO_USD,
    }
