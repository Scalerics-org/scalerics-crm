"""Inteligencia financiera: diagnóstico del mes y sugerencias para ganar plata.

No pide datos. Lee lo que ya hay en el sistema (Finanzas, la pauta de Meta, el
semáforo de leads, Demos, Clientes, Proyectos y Equipo), saca conclusiones y
sugiere qué hacer, cada cosa con su número y la cuenta a la vista (pedido de
Juan, 15/9: "no quiero que me pida datos"). Si alguien cargó a mano un motivo
de pérdida, el esfuerzo de un proyecto o el origen de una venta, se aprovecha;
si no, se usa el mejor dato que exista. Si para una regla no hay ningún dato,
esa regla no aparece, sin aviso.

Moneda: USD, con la misma conversión que Finanzas (`monto_usd` de los
movimientos y `a_usd` con el tipo de cambio del fijo). Ventana: los 3 meses
completos anteriores más el mes en curso.

Se recalcula UNA vez por día (`corrida_diaria`), no en cada carga.

De dónde sale cada dato
-----------------------
Ingresos/egresos  `finanzas_movimientos` (sin anulados), por período.
Fijos             `finanzas_recurrentes` activos y vigentes este mes. Variables
                  = egresos sin `recurrente_id`, promedio de los 3 meses previos.
Caja              la de Balance General (`balance_general`, tipo interno, al día
                  de hoy): la misma que ve Finanzas.
Mantenimiento     ingresos fijos y cobros de la categoría mantenimiento.
Ventas            negocios en cerrado / en desarrollo / finalizado, fechados por
                  su primer evento de cierre (o el alta). Canal: el elegido a mano
                  si existe, si no `businesses.source` (meta = Meta Ads).
Ticket promedio   precio de las ventas de la ventana (cobros en Finanzas sin
                  mantenimiento, monto pagado en Clientes o último presupuesto);
                  si ninguna tiene precio, todas las ventas con precio.
Margen %          (ingresos − egresos) ÷ ingresos de la ventana.
Margen por venta  ticket promedio × margen %.
Pauta y leads     `meta_insights.spend` y leads con `source='meta'` por fecha de
                  alta. Costo por venta = pauta ÷ ventas de Meta.
Embudo            leads de Meta → demo agendada → demo realizada → venta, con el
                  historial de `lead_events` y el `crm_status` (semáforo), más
                  las demos "no cerró" de Demos.
Capacidad         `capacidad()` de Equipo, horas libres ÷ horas por proyecto.
Horas/proyecto    el esfuerzo cargado si hay; si no, la duración promedio de los
                  timelines de Proyectos × horas por día del equipo (supuesto).
Costo por hora    egresos de los 3 meses previos sin pauta ÷ horas del equipo.

Supuestos que se muestran en la tarjeta: la comisión de cobro y la cuota de
mantenimiento cuando salen del Simulador o de presupuestos, y las horas por
proyecto cuando son estimadas. Los umbrales (USD 100, 6 sugerencias, 3 meses de
caja, 20 % de margen, 30 % de recupero) son los del pedido; los demás están
nombrados abajo como constantes.
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
from services.embudo import alcanzo, normalizar_estado
from services.equipo import capacidad, dias_habiles, lunes_de
from services.finanzas import _totales, a_usd

logger = logging.getLogger(__name__)

# ── umbrales ─────────────────────────────────────────────────────────────────
UMBRAL_IMPACTO_USD = 100
MAX_RECOMENDACIONES = 6
MAX_DIAGNOSTICO = 6
VENTANA_MESES = 3
DIAS_SEGUIMIENTO = 30
MARGEN_MINIMO = 0.20          # R6
RECUPERO_PERDIDAS = 0.30      # R7
CAJA_MESES_MINIMO = 3         # alerta de caja y R8
CAJA_MESES_ATENCION = 6       # supuesto: debajo de 6 meses, "atención"
CONCENTRACION_ALTA = 0.40     # supuesto: más del 40 % en un cliente es riesgo (R9)
CONCENTRACION_ATENCION = 0.25
DEMOS_REALIZADAS_MINIMO = 0.60  # supuesto: si se hacen menos de 6 de cada 10 demos (R10)
FIJO_PESA = 0.10              # supuesto: un fijo que es el 10 % de los ingresos pesa (R5)
PESO_FIJOS_ALTO = 0.70
PESO_FIJOS_ATENCION = 0.40
FUNCIONO_SI_LLEGA_A = 0.5     # supuesto: funcionó si lo real llega a la mitad de lo esperado

# Los mismos valores por defecto que el Simulador (`SIM_DEFAULTS` en
# dashboard.py: mantenimiento.comisionCobro y mantenimiento.cuotaAltaNueva).
# Hay un test que verifica que coincidan.
COMISION_SIMULADOR_PCT = 5
CUOTA_SIMULADOR_USD = 100

CORRIDA = "inteligencia_fin"
LISTA_MAX = 50
ESFUERZO_MAX = 5000
_EPS = 1e-9
_MVD = timezone(timedelta(hours=-3))

REGLAS = {  # regla -> (confianza por defecto, tipo)
    "R1": ("alta", "ingreso"),
    "R2": ("media", "ingreso"),
    "R3": ("media", "recorte"),
    "R4": ("alta", "ingreso"),
    "R5": ("media", "recorte"),
    "R6": ("alta", "alerta"),
    "R7": ("baja", "ingreso"),
    "R8": ("alta", "alerta"),
    "R9": ("alta", "alerta"),
    "R10": ("media", "ingreso"),
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
# "Hubo demo y no cerró" en el semáforo (violeta).
ESTADOS_DEMO_SIN_CIERRE = {"presupuesto_enviado", "follow_up_1", "follow_up_2",
                           "en_espera", "rechazo"}

ETAPAS_TERMINADAS = {"done", "complete", "completed", "canceled", "cancelled",
                     "archived", "terminado", "finalizado", "entregado", "cancelado"}
CATEGORIAS_TIPO = ("desarrollo_web", "software_medida", "marketing")
ETIQUETA_TIPO = {"desarrollo_web": "Desarrollo web",
                 "software_medida": "Software a medida", "marketing": "Marketing"}
ETIQUETA_CATEGORIA = {"infraestructura": "infraestructura", "herramientas": "herramientas",
                      "publicidad": "publicidad", "servicios": "servicios", "otros": "otros gastos"}
# Lo que no se sugiere recortar: no es un gasto que se pueda probar un mes sin.
CATEGORIAS_NO_RECORTABLES = ("impuestos", "retiros")

SUPUESTOS = {"comision_cobro_pct": "Comisión de cobro del mantenimiento (%)"}

ADVERTENCIA_R5 = ("Puede estar aportando algo que el sistema no mide. Probá un mes sin "
                  "ese gasto antes de cortarlo.")
ADVERTENCIA_R6 = ("La recomendación es revisar el precio, nunca abandonar la línea: "
                  "un margen bajo puede convenir si abre mantenimiento recurrente.")
ADVERTENCIA_R3 = ("Mirá qué campaña trae las pocas ventas antes de pausar todo: bajar "
                  "la que no rinde puede alcanzar.")

NIVELES = ("mal", "atencion", "bien")


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


def _periodo(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _mover_periodo(periodo: str, n: int) -> str:
    y, m = divmod(int(periodo[:4]) * 12 + int(periodo[5:7]) - 1 + n, 12)
    return f"{y:04d}-{m + 1:02d}"


def _primer_dia(periodo: str) -> date:
    return date(int(periodo[:4]), int(periodo[5:7]), 1)


def _ultimo_dia(periodo: str) -> date:
    y, m = int(periodo[:4]), int(periodo[5:7])
    return date(y, m, calendar.monthrange(y, m)[1])


def periodos(hoy: date) -> tuple[list[str], str]:
    """(los 3 meses completos anteriores, el mes en curso)."""
    mes = _periodo(hoy)
    return [_mover_periodo(mes, -i) for i in range(VENTANA_MESES, 0, -1)], mes


def ventana(hoy: date) -> tuple[date, date]:
    """Del primer día de los 3 meses anteriores hasta hoy."""
    previos, _ = periodos(hoy)
    return _primer_dia(previos[0]), hoy


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


def pct(fraccion, decimales: int = 0) -> str:
    return num(float(fraccion or 0) * 100, decimales)


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


def _promedio(valores) -> float | None:
    valores = list(valores)
    return sum(valores) / len(valores) if valores else None


# ── ventas y su origen ───────────────────────────────────────────────────────

def canal_de_source(source, maps_url=None) -> str | None:
    """`businesses.source` a los canales, o None si no se sabe."""
    s = (source or "").strip().lower()
    if not s:
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
        canal = manual.get(("business", n["id"])) or canal_de_source(n["source"], n["maps_url"])
        salida.append({"entidad": "business", "id": n["id"], "business_id": n["id"],
                       "nombre": n["name"], "fecha": primera.get(n["id"]) or _fecha(n["scraped_at"]),
                       "canal": canal, "source": n["source"] or ""})
    fichas = _q(db_path, "SELECT nc.id, nc.name FROM notion_clients nc "
                         "LEFT JOIN businesses b ON b.id = nc.business_id "
                         "WHERE nc.status = ? AND b.id IS NULL", (ESTADO_ACEPTADO,))
    for f in fichas:
        salida.append({"entidad": "notion_client", "id": f["id"], "business_id": None,
                       "nombre": f["name"], "fecha": None,
                       "canal": manual.get(("notion_client", f["id"])), "source": ""})
    return salida


def precios_y_tipos(db_path: str) -> dict[int, dict]:
    """Precio cobrado y tipo de proyecto por negocio."""
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
    for f in _q(db_path, "SELECT id, monto_pagado FROM businesses "
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


# ── proyectos, horas y capacidad ─────────────────────────────────────────────

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


def _personas_con_horas(db_path: str) -> list[dict]:
    return [p for p in listar_personas_equipo(db_path) if p.get("lleva_horas")]


def horas_por_proyecto(db_path: str, proys: list[dict]) -> dict | None:
    """Las horas que lleva un proyecto: las cargadas o, si no hay, una estimación."""
    cargadas = [p["horas"] for p in proys if p["horas"]]
    if cargadas:
        return {"horas": round(sum(cargadas) / len(cargadas), 2), "estimado": False,
                "fuente": f"esfuerzo cargado en {len(cargadas)} "
                          f"{_plural(len(cargadas), 'proyecto', 'proyectos')}"}
    personas = _personas_con_horas(db_path)
    duraciones = []
    for p in proys:
        inicio, fin = _fecha(p["timeline_start"]), _fecha(p["timeline_end"])
        if inicio and fin and fin >= inicio:
            dias = len(dias_habiles(inicio, fin))
            if dias:
                duraciones.append(dias)
    if not duraciones or not personas:
        return None
    dias = sum(duraciones) / len(duraciones)
    por_dia = sum(float(p["horas_por_dia"]) for p in personas) / len(personas)
    return {"horas": round(dias * por_dia, 2), "estimado": True,
            "fuente": (f"estimado: duración promedio de {len(duraciones)} "
                       f"{_plural(len(duraciones), 'timeline', 'timelines')} de Proyectos "
                       f"({num(dias)} días hábiles) × {num(por_dia)} h por día del equipo")}


def costo_hora(db_path: str, desde: date, hasta: date) -> dict:
    egresos = round(sum(float(m["monto_usd"] or 0)
                        for m in listar_movimientos(db_path, tipo="egreso")
                        if m["categoria"] != "publicidad"
                        and desde.isoformat() <= (m["fecha"] or "")[:10] <= hasta.isoformat()), 2)
    personas = _personas_con_horas(db_path)
    dias = len(dias_habiles(desde, hasta))
    horas = round(sum(float(p["horas_por_dia"]) for p in personas) * dias, 2)
    valor = round(egresos / horas, 2) if egresos > 0 and horas > 0 else None
    return {"egresos": egresos, "horas": horas, "dias": dias, "personas": len(personas), "valor": valor}


def capacidad_mes(db_path: str, hoy: date, proys: list[dict], horas_proyecto) -> dict | None:
    if not _personas_con_horas(db_path):
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
        horas = p["horas"] or horas_proyecto
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
    slots = math.floor(libres_h / horas_proyecto + _EPS) if horas_proyecto else None
    return {"desde": primero, "hasta": ultimo, "base": round(base, 2),
            "ausencia": round(ausencia, 2), "netas": round(netas, 2),
            "comprometidas": round(comprometidas, 2), "en_curso": en_curso,
            "libres_horas": round(libres_h, 2), "slots": slots}


# ── pauta, embudo, caja ──────────────────────────────────────────────────────

def pauta_meta(db_path: str, desde: date, hasta: date) -> dict:
    d, h = desde.isoformat(), hasta.isoformat()
    gasto = _q(db_path, "SELECT COALESCE(SUM(spend), 0) AS g FROM meta_insights "
                        "WHERE date BETWEEN ? AND ?", (d, h))[0]["g"]
    monedas = {(r["currency"] or "").upper()
               for r in _q(db_path, "SELECT DISTINCT currency FROM meta_insights "
                                    "WHERE date BETWEEN ? AND ? AND spend > 0", (d, h))}
    return {"gasto": round(float(gasto or 0), 2), "monedas_raras": sorted(monedas - {"", "USD"})}


def embudo(db_path: str, desde: date, hasta: date) -> dict:
    """Leads de Meta que entraron en esas fechas y hasta dónde llegó cada uno."""
    leads = _q(db_path, "SELECT id, crm_status FROM businesses WHERE source = 'meta' "
                        "AND substr(replace(scraped_at, 'T', ' '), 1, 10) BETWEEN ? AND ?",
               (desde.isoformat(), hasta.isoformat()))
    eventos: dict[int, set] = {}
    ids = [l["id"] for l in leads]
    for i in range(0, len(ids), 500):
        tramo = ids[i:i + 500]
        for e in _q(db_path, f"SELECT lead_id, new_status FROM lead_events "
                             f"WHERE lead_id IN ({', '.join('?' for _ in tramo)})", tramo):
            eventos.setdefault(e["lead_id"], set()).add(e["new_status"])

    c = {"leads": len(leads), "no_atiende": 0, "no_interesa": 0, "atendieron": 0,
         "agendadas": 0, "realizadas": 0, "ventas": 0}
    sin_cierre: set = set()
    for l in leads:
        estado = normalizar_estado(l["crm_status"] or "")
        estados = eventos.get(l["id"], set()) | {estado}
        if estado == "llamar_despues":
            c["no_atiende"] += 1
        if estado == "no_interesa":
            c["no_interesa"] += 1
        if estado == "no_interesa" or alcanzo(estados, "interesado"):
            c["atendieron"] += 1
        if alcanzo(estados, "demo_agendada"):
            c["agendadas"] += 1
        if alcanzo(estados, "demo_1"):
            c["realizadas"] += 1
        if alcanzo(estados, "cerrado"):
            c["ventas"] += 1
        elif estado in ESTADOS_DEMO_SIN_CIERRE:
            sin_cierre.add(l["id"])
    # Las demos que la planilla marca "no cerró" en esos meses, sin repetir negocio.
    for d in _q(db_path, "SELECT client_id FROM demos_realizadas WHERE estado_planilla = ? "
                         "AND COALESCE(NULLIF(mes_planilla, ''), substr(fecha, 1, 7)) BETWEEN ? AND ?",
                (ESTADO_PERDIDO_DEMO, _periodo(desde), _periodo(hasta))):
        sin_cierre.add(d["client_id"])
    c["demo_no_cerro"] = len(sin_cierre)
    c["etapas"] = [
        {"clave": "agendar", "nombre": "de lead a demo agendada", "base": c["leads"], "pasan": c["agendadas"]},
        {"clave": "hacer", "nombre": "de demo agendada a demo realizada", "base": c["agendadas"], "pasan": c["realizadas"]},
        {"clave": "cerrar", "nombre": "de demo realizada a venta", "base": c["realizadas"], "pasan": c["ventas"]},
    ]
    for e in c["etapas"]:
        e["tasa"] = e["pasan"] / e["base"] if e["base"] else None
    return c


def caja_hoy(db_path: str, hoy: date) -> float | None:
    """La caja de Balance General (la misma que Finanzas). None sin movimientos."""
    if not _q(db_path, "SELECT 1 AS x FROM finanzas_movimientos WHERE anulado = 0 LIMIT 1"):
        return None
    try:
        from services.finanzas import balance_general
        bg = balance_general(db_path, "interno", hoy.isoformat())
        fila = next(f for f in bg["activo"]["filas"] if f["clave"] == "caja")
        return round(float(fila["monto"]), 2)
    except Exception:
        logger.warning("inteligencia financiera: no se pudo leer la caja", exc_info=True)
        return None


# ── pérdidas cargadas a mano (si hay) ────────────────────────────────────────

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
    salida = {}
    for g in perdidas(db_path):
        for it in g["items"]:
            if it["entidad"] == entidad:
                salida[it["entidad_id"]] = g["motivo"]
    return salida


def guardar_motivo(db_path: str, entidad: str, entidad_id: int, motivo,
                   quien: str = "", ahora: datetime | None = None) -> str | None:
    if entidad not in ENTIDADES_PERDIDA:
        return "no se reconoce qué se perdió"
    if motivo not in MOTIVOS:
        return "elegí un motivo de la lista: " + " / ".join(MOTIVOS.values()).lower()
    grupos = perdidas(db_path)
    grupo = next((g for g in grupos for it in g["items"]
                  if it["entidad"] == entidad and it["entidad_id"] == entidad_id), None)
    if not grupo:
        return "eso no está marcado como perdido"
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO perdidas_motivo (entidad, entidad_id, business_id, motivo, motivo_en, cargado_por) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(entidad, entidad_id) DO UPDATE SET "
            "motivo = excluded.motivo, motivo_en = excluded.motivo_en, "
            "cargado_por = excluded.cargado_por, "
            "business_id = COALESCE(excluded.business_id, perdidas_motivo.business_id)",
            (entidad, entidad_id, grupo["business_id"], motivo, _iso(ahora or ahora_utc()), quien))
        conn.commit()
    finally:
        conn.close()
    return None


def registrar_perdida_ficha(db_path: str, notion_page_id: str, ahora: datetime | None = None) -> None:
    """Anota el día en que una ficha pasó a perdida (lo llama `cliente_cambio_de_estado`)."""
    fichas = _q(db_path, "SELECT id, business_id FROM notion_clients WHERE notion_page_id = ?",
                (notion_page_id,))
    if not fichas:
        return
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO perdidas_motivo (entidad, entidad_id, business_id, perdida_en) "
            "VALUES ('notion_client', ?, ?, ?) ON CONFLICT(entidad, entidad_id) DO UPDATE SET "
            "perdida_en = excluded.perdida_en, "
            "business_id = COALESCE(excluded.business_id, perdidas_motivo.business_id)",
            (fichas[0]["id"], fichas[0]["business_id"], hoy_de(ahora or ahora_utc()).isoformat()))
        conn.commit()
    finally:
        conn.close()


# ── lo opcional que se puede afinar a mano ───────────────────────────────────

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
        personas = _personas_con_horas(db_path)
        if not personas:
            return None, "para cargar días hace falta el equipo con horas por día: cargalo en horas"
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


# ── mantenimiento ────────────────────────────────────────────────────────────

def _fijos_vigentes(db_path: str, mes: str, tipo: str, categoria: str | None = None) -> list[tuple[dict, float]]:
    salida = []
    for r in listar_recurrentes(db_path, solo_activos=True):
        if r["tipo"] != tipo or (categoria and r["categoria"] != categoria):
            continue
        if (r["hasta"] and r["hasta"] < mes) or (r["desde"] and r["desde"] > mes):
            continue
        try:
            salida.append((r, a_usd(r["monto"], r["moneda"], r["tipo_cambio"])))
        except (TypeError, ValueError):
            continue
    return salida


def _fijos_mantenimiento(db_path: str, mes: str) -> list[tuple[dict, float]]:
    return _fijos_vigentes(db_path, mes, "ingreso", "mantenimiento")


def ingreso_mensual_mantenimiento(db_path: str, hoy: date, comision_pct) -> dict:
    fijos = _fijos_mantenimiento(db_path, _periodo(hoy))
    con_fijo = {r["client_id"] for r, _ in fijos if r["client_id"]}
    desde = (hoy - timedelta(days=DIAS_SEGUIMIENTO)).isoformat()
    sueltos = [m for m in listar_movimientos(db_path, tipo="ingreso", categoria="mantenimiento")
               if desde <= (m["fecha"] or "")[:10] <= hoy.isoformat() and not m["recurrente_id"]
               and (not m["client_id"] or m["client_id"] not in con_fijo)]
    bruto = round(sum(u for _, u in fijos) + sum(float(m["monto_usd"] or 0) for m in sueltos), 2)
    neto = round(bruto * (1 - float(comision_pct) / 100), 2) if comision_pct is not None else None
    return {"bruto": bruto, "neto": neto, "fijos": len(fijos), "sueltos": len(sueltos)}


def _ultimo_escenario(db_path: str) -> dict:
    try:
        filas = _q(db_path, "SELECT datos FROM simulador_escenarios "
                            "ORDER BY COALESCE(updated_at, created_at) DESC, id DESC LIMIT 1")
        return json.loads(filas[0]["datos"]) if filas else {}
    except Exception:
        return {}


def comision_cobro(db_path: str) -> dict:
    """La comisión de cobro: la cargada, la del último escenario o la del Simulador."""
    cargada = leer_supuestos(db_path).get("comision_cobro_pct")
    if cargada is not None:
        return {"valor": float(cargada), "fuente": "cargada en Inteligencia financiera"}
    valor = _numero(((_ultimo_escenario(db_path).get("mantenimiento") or {}).get("comisionCobro")))
    if valor is not None and 0 <= valor < 100:
        return {"valor": valor, "fuente": "la del último escenario guardado del Simulador"}
    return {"valor": float(COMISION_SIMULADOR_PCT), "fuente": "la que usa el Simulador"}


def cuota_mantenimiento(db_path: str, fijos: list, movs: list) -> dict | None:
    if fijos:
        valores = [u for _, u in fijos]
        return {"valor": sum(valores) / len(valores), "suma": sum(valores), "n": len(valores),
                "supuesto": False, "fuente": f"promedio de {len(valores)} "
                f"{_plural(len(valores), 'ingreso fijo', 'ingresos fijos')} de mantenimiento en Finanzas"}
    valores = [float(m["monto_usd"] or 0) for m in movs if m["client_id"] and m["monto_usd"]]
    if valores:
        return {"valor": sum(valores) / len(valores), "suma": sum(valores), "n": len(valores),
                "supuesto": False, "fuente": f"promedio de {len(valores)} "
                f"{_plural(len(valores), 'cobro', 'cobros')} de mantenimiento en Finanzas"}
    presupuestos = []
    for f in _q(db_path, "SELECT notes FROM budgets WHERE notes IS NOT NULL AND notes != ''"):
        try:
            v = _numero((json.loads(f["notes"]) or {}).get("monthly_price"))
        except (ValueError, TypeError, AttributeError):
            v = None
        if v and v > 0:
            presupuestos.append(v)
    if presupuestos:
        return {"valor": sum(presupuestos) / len(presupuestos), "suma": sum(presupuestos),
                "n": len(presupuestos), "supuesto": True,
                "fuente": f"promedio del mantenimiento mensual de {len(presupuestos)} "
                          f"{_plural(len(presupuestos), 'presupuesto', 'presupuestos')}"}
    v = _numero(((_ultimo_escenario(db_path).get("mantenimiento") or {}).get("cuotaAltaNueva")))
    if v and v > 0:
        return {"valor": v, "suma": v, "n": 1, "supuesto": True,
                "fuente": "la cuota del último escenario guardado del Simulador"}
    return {"valor": float(CUOTA_SIMULADOR_USD), "suma": float(CUOTA_SIMULADOR_USD), "n": 1,
            "supuesto": True, "fuente": "la cuota por defecto del Simulador"}


# ── el contexto: todo lo que se lee una vez ──────────────────────────────────

def _contexto(db_path: str, hoy: date) -> dict:
    previos, mes = periodos(hoy)
    desde, hasta = ventana(hoy)
    desde_prev, hasta_prev = _primer_dia(previos[0]), _ultimo_dia(previos[-1])
    movs = listar_movimientos(db_path, desde=previos[0], hasta=mes)
    por_periodo = {p: _totales([m for m in movs if m["periodo"] == p]) for p in previos + [mes]}
    ing_v = round(sum(i for i, _ in por_periodo.values()), 2)
    egr_v = round(sum(e for _, e in por_periodo.values()), 2)
    ing_prom = sum(por_periodo[p][0] for p in previos) / VENTANA_MESES
    egr_prom = sum(por_periodo[p][1] for p in previos) / VENTANA_MESES
    variables_prev = sum(float(m["monto_usd"] or 0) for m in movs
                         if m["tipo"] == "egreso" and not m["recurrente_id"] and m["periodo"] in previos)
    variables_v = sum(float(m["monto_usd"] or 0) for m in movs
                      if m["tipo"] == "egreso" and not m["recurrente_id"])
    fijos = _fijos_vigentes(db_path, mes, "egreso")

    lista = ventas(db_path)
    ventas_v = [v for v in lista if v["fecha"] and desde <= v["fecha"] <= hasta]
    ventas_prev = [v for v in lista if v["fecha"] and desde_prev <= v["fecha"] <= hasta_prev]
    precios = precios_y_tipos(db_path)
    con_precio = [precios[v["business_id"]]["precio"] for v in ventas_v if v["business_id"] in precios]
    alcance = "de las ventas de los últimos 3 meses"
    if not con_precio:
        con_precio = [precios[v["business_id"]]["precio"] for v in lista if v["business_id"] in precios]
        alcance = "de todas las ventas con precio"
    ticket = ({"valor": sum(con_precio) / len(con_precio), "n": len(con_precio),
               "total": sum(con_precio), "alcance": alcance} if con_precio else None)
    margen_pct = (ing_v - egr_v) / ing_v if ing_v > 0 else None
    margen_venta = ticket["valor"] * margen_pct if ticket and margen_pct is not None else None

    proys = proyectos(db_path)
    horas = horas_por_proyecto(db_path, proys)
    return {
        "db_path": db_path,
        "hoy": hoy, "mes": mes, "previos": previos, "desde": desde, "hasta": hasta,
        "desde_prev": desde_prev, "hasta_prev": hasta_prev,
        "movs": movs, "por_periodo": por_periodo, "ingresos_ventana": ing_v, "egresos_ventana": egr_v,
        "ingresos_promedio": ing_prom, "egresos_promedio": egr_prom,
        "variables_promedio": variables_prev / VENTANA_MESES, "variables_ventana": variables_v,
        "fijos": fijos, "fijos_total": round(sum(u for _, u in fijos), 2),
        "caja": caja_hoy(db_path, hoy),
        "ventas": lista, "ventas_ventana": ventas_v, "ventas_prev": ventas_prev,
        "precios": precios, "ticket": ticket, "margen_pct": margen_pct, "margen_venta": margen_venta,
        "pauta": pauta_meta(db_path, desde, hasta), "pauta_prev": pauta_meta(db_path, desde_prev, hasta_prev),
        "embudo": embudo(db_path, desde, hasta), "embudo_prev": embudo(db_path, desde_prev, hasta_prev),
        "vencidos": [p for p in listar_por_cobrar(db_path)
                     if _fecha(p["vence"]) and _fecha(p["vence"]) < hoy and float(p["monto_usd"] or 0) > 0],
        "mantenimiento": ingreso_mensual_mantenimiento(db_path, hoy, None)["bruto"],
        "proyectos": proys, "horas_proyecto": horas,
        "capacidad": capacidad_mes(db_path, hoy, proys, horas["horas"] if horas else None),
        "costo_hora": costo_hora(db_path, desde_prev, hasta_prev),
    }


def _concentracion(ctx: dict) -> dict | None:
    por_cliente: dict[int, float] = {}
    for m in ctx["movs"]:
        if m["tipo"] == "ingreso" and m["client_id"]:
            por_cliente[m["client_id"]] = por_cliente.get(m["client_id"], 0.0) + float(m["monto_usd"] or 0)
    total = ctx["ingresos_ventana"]
    if not por_cliente or total <= 0:
        return None
    cid = max(por_cliente, key=por_cliente.get)
    nombre = (_q_nombre(ctx, cid))
    return {"client_id": cid, "nombre": nombre, "monto": round(por_cliente[cid], 2),
            "total": total, "share": por_cliente[cid] / total}


def _q_nombre(ctx: dict, cid: int) -> str:
    filas = _q(ctx["db_path"], "SELECT name FROM businesses WHERE id = ?", (cid,))
    return (filas[0]["name"] if filas and filas[0]["name"] else f"el cliente #{cid}")


def _fijos_crecidos(ctx: dict) -> list[dict]:
    """Los fijos que hoy cuestan más que al principio de la ventana, o que son nuevos."""
    salida = []
    for rec, actual in ctx["fijos"]:
        suyos = sorted((m for m in ctx["movs"] if m["recurrente_id"] == rec["id"]),
                       key=lambda m: (m["periodo"], m["id"]))
        if suyos and actual > float(suyos[0]["monto_usd"] or 0) + 0.5:
            salida.append({"id": rec["id"], "concepto": rec["concepto"], "categoria": rec["categoria"],
                           "antes": round(float(suyos[0]["monto_usd"]), 2), "ahora": actual,
                           "desde": suyos[0]["periodo"], "nuevo": False})
        elif not suyos and rec["desde"] and rec["desde"] >= ctx["previos"][0]:
            salida.append({"id": rec["id"], "concepto": rec["concepto"], "categoria": rec["categoria"],
                           "antes": 0.0, "ahora": actual, "desde": rec["desde"], "nuevo": True})
        elif suyos and rec["desde"] and rec["desde"] >= ctx["previos"][0] and suyos[0]["periodo"] >= ctx["previos"][0]:
            salida.append({"id": rec["id"], "concepto": rec["concepto"], "categoria": rec["categoria"],
                           "antes": 0.0, "ahora": actual, "desde": rec["desde"], "nuevo": True})
    return salida


# ── diagnóstico ──────────────────────────────────────────────────────────────

def _diag(clave, titulo, valor, texto, lineas, nivel) -> dict:
    return {"clave": clave, "titulo": titulo, "valor": valor, "texto": texto,
            "calculo": "\n".join(lineas), "nivel": nivel}


def diagnostico(ctx: dict) -> list[dict]:
    """Entre 4 y 6 conclusiones, las alertas primero. Solo las que tienen datos."""
    salida = []
    mes, previos = ctx["mes"], ctx["previos"]

    # Resultado y margen
    if ctx["movs"]:
        i, e = ctx["por_periodo"][mes]
        neto = i - e
        prom = sum(ctx["por_periodo"][p][0] - ctx["por_periodo"][p][1] for p in previos) / VENTANA_MESES
        margen = neto / i if i > 0 else None
        if neto < 0:
            frase = f"salieron {usd(-neto)} más de lo que entró"
        else:
            frase = f"quedan {usd(neto)}" + (f" ({pct(margen)} % de margen)" if margen is not None else "")
        comparacion = "por debajo" if neto < prom else "por encima"
        salida.append(_diag(
            "resultado", "Resultado del mes", usd(neto),
            f"Este mes, hasta hoy, entraron {usd(i)} y salieron {usd(e)}: {frase}. "
            f"Los 3 meses anteriores dejaron {usd(prom)} por mes: vas {comparacion}.",
            [f"Mes en curso: ingresos {usd(i)} − egresos {usd(e)} = {usd(neto)}"
             + (f" → margen {usd(neto)} ÷ {usd(i)} = {pct(margen, 1)} %" if margen is not None else ""),
             "Meses anteriores: " + " · ".join(
                 f"{p} {usd(ctx['por_periodo'][p][0] - ctx['por_periodo'][p][1])}" for p in previos)
             + f" → promedio {usd(prom)}"],
            "mal" if neto < 0 else ("atencion" if neto < prom else "bien")))

    # Caja y meses de supervivencia
    gasto = ctx["fijos_total"] + ctx["variables_promedio"]
    if ctx["caja"] is not None and gasto > 0:
        meses = max(ctx["caja"], 0) / gasto
        salida.append(_diag(
            "caja", "Caja", f"{num(meses)} meses",
            f"Con la caja de hoy ({usd(ctx['caja'])}) cubrís {num(meses)} meses de gastos"
            + (". Es poco: por debajo de 3 meses cualquier atraso de un cobro duele." if meses < CAJA_MESES_MINIMO
               else "."),
            [f"Caja (Balance General) {usd(ctx['caja'])} ÷ (fijos {usd(ctx['fijos_total'])} + variables "
             f"promedio {usd(ctx['variables_promedio'])}) = {num(meses)} meses"],
            "mal" if meses < CAJA_MESES_MINIMO else ("atencion" if meses < CAJA_MESES_ATENCION else "bien")))

    # Punto de equilibrio
    if ctx["fijos_total"] > 0 and ctx["ingresos_ventana"] > 0:
        proporcion = ctx["variables_ventana"] / ctx["ingresos_ventana"]
        if proporcion < 1:
            equilibrio = ctx["fijos_total"] / (1 - proporcion)
            cubre = ctx["mantenimiento"] / equilibrio if equilibrio else 0
            salida.append(_diag(
                "equilibrio", "Punto de equilibrio", usd(equilibrio),
                f"Para cubrir los fijos tenés que facturar {usd(equilibrio)} por mes. El mantenimiento "
                f"recurrente ({usd(ctx['mantenimiento'])}) ya cubre el {pct(cubre)} %.",
                [f"Gastos variables ÷ ingresos de la ventana = {usd(ctx['variables_ventana'])} ÷ "
                 f"{usd(ctx['ingresos_ventana'])} = {pct(proporcion, 1)} %",
                 f"Equilibrio = fijos {usd(ctx['fijos_total'])} ÷ (1 − {pct(proporcion, 1)} %) = {usd(equilibrio)}",
                 f"Mantenimiento {usd(ctx['mantenimiento'])} ÷ {usd(equilibrio)} = {pct(cubre, 1)} %"],
                "bien" if ctx["ingresos_promedio"] >= equilibrio else "mal"))

    # Costo por lead y por venta
    pauta, emb = ctx["pauta"]["gasto"], ctx["embudo"]
    if pauta > 0 and not ctx["pauta"]["monedas_raras"]:
        meta = [v for v in ctx["ventas_ventana"] if v["canal"] == "meta_ads"]
        lineas = [f"Pauta de Meta del {_dmy(ctx['desde'])} al {_dmy(ctx['hasta'])}: {usd(pauta)}"]
        partes = []
        if emb["leads"]:
            cpl = pauta / emb["leads"]
            lineas.append(f"Costo por lead = {usd(pauta)} ÷ {emb['leads']} leads = {usd(cpl)}")
            partes.append(f"cada lead cuesta {usd(cpl)}")
        if meta:
            cpv = pauta / len(meta)
            lineas.append(f"Costo por venta = {usd(pauta)} ÷ {len(meta)} ventas de Meta = {usd(cpv)}")
            partes.append(f"cada venta cuesta {usd(cpv)}")
        if ctx["ticket"]:
            lineas.append(f"Ticket promedio = {usd(ctx['ticket']['total'])} ÷ {ctx['ticket']['n']} = "
                          f"{usd(ctx['ticket']['valor'])} ({ctx['ticket']['alcance']})")
        if meta:
            cpv = pauta / len(meta)
            rinde = ctx["margen_venta"] is not None and ctx["margen_venta"] > cpv
            if ctx["margen_venta"] is not None:
                lineas.append(f"Margen por venta = {usd(ctx['ticket']['valor'])} × {pct(ctx['margen_pct'], 1)} % "
                              f"= {usd(ctx['margen_venta'])}")
            texto = (f"En pauta, {', '.join(partes)}"
                     + (f", contra un ticket promedio de {usd(ctx['ticket']['valor'])}." if ctx["ticket"] else ".")
                     + (" La venta deja más de lo que cuesta conseguirla." if rinde
                        else " Conseguir la venta cuesta más de lo que deja." if ctx["margen_venta"] is not None else ""))
            salida.append(_diag("pauta", "Costo por venta", usd(cpv), texto, lineas,
                                "bien" if rinde else ("mal" if ctx["margen_venta"] is not None else "atencion")))
        else:
            salida.append(_diag("pauta", "Costo por venta", "sin ventas",
                                f"Se invirtieron {usd(pauta)} en Meta" + (f" ({', '.join(partes)})" if partes else "")
                                + " y ninguna venta vino de ahí en la ventana.", lineas, "mal"))

    # Embudo
    if emb["leads"]:
        con_tasa = [e for e in emb["etapas"] if e["tasa"] is not None]
        peor = min(con_tasa, key=lambda e: e["tasa"]) if con_tasa else None
        texto = (f"De {emb['leads']} leads de Meta, {emb['agendadas']} agendaron demo, "
                 f"{emb['realizadas']} la hicieron y {emb['ventas']} compraron.")
        if peor:
            texto += f" La caída más grande es {peor['nombre']}: pasa el {pct(peor['tasa'])} %."
        salida.append(_diag(
            "embudo", "Embudo", f"{emb['ventas']} de {emb['leads']}", texto,
            [f"{e['nombre'].capitalize()}: {e['pasan']} ÷ {e['base']} = {pct(e['tasa'], 1)} %"
             for e in con_tasa]
            + [f"No atiende: {emb['no_atiende']} · no le interesa: {emb['no_interesa']} · "
               f"hubo demo y no cerró: {emb['demo_no_cerro']}"],
            "mal" if peor and peor["tasa"] < 0.2 else "atencion"))

    # Concentración
    conc = _concentracion(ctx)
    if conc:
        salida.append(_diag(
            "concentracion", "Concentración", f"{pct(conc['share'])} %",
            f"El {pct(conc['share'])} % de los ingresos de la ventana viene de {conc['nombre']}.",
            [f"{conc['nombre']}: {usd(conc['monto'])} ÷ ingresos {usd(conc['total'])} = {pct(conc['share'], 1)} %"],
            "mal" if conc["share"] >= CONCENTRACION_ALTA else
            ("atencion" if conc["share"] >= CONCENTRACION_ATENCION else "bien")))

    # Fijos
    if ctx["fijos_total"] > 0 and ctx["ingresos_promedio"] > 0:
        peso = ctx["fijos_total"] / ctx["ingresos_promedio"]
        crecidos = _fijos_crecidos(ctx)
        texto = f"Los fijos ({usd(ctx['fijos_total'])} por mes) son el {pct(peso)} % de lo que entra por mes."
        if crecidos:
            texto += " Crecieron: " + ", ".join(
                f"{c['concepto']} ({'nuevo' if c['nuevo'] else usd(c['antes']) + ' → ' + usd(c['ahora'])})"
                for c in crecidos[:3]) + "."
        salida.append(_diag(
            "fijos", "Gastos fijos", f"{pct(peso)} %", texto,
            [f"Fijos {usd(ctx['fijos_total'])} ÷ ingresos promedio de los 3 meses anteriores "
             f"{usd(ctx['ingresos_promedio'])} = {pct(peso, 1)} %"]
            + [f"{c['concepto']}: {usd(c['antes'])} en {c['desde']} → {usd(c['ahora'])} hoy" for c in crecidos],
            "mal" if peso >= PESO_FIJOS_ALTO else ("atencion" if peso >= PESO_FIJOS_ATENCION or crecidos else "bien")))

    # Cobros vencidos
    if ctx["vencidos"]:
        total = sum(float(p["monto_usd"]) for p in ctx["vencidos"])
        n = len(ctx["vencidos"])
        salida.append(_diag(
            "vencidos", "Cobros vencidos", usd(total),
            f"Hay {n} {_plural(n, 'cobro vencido', 'cobros vencidos')} por {usd(total)}.",
            [" + ".join(usd(p["monto_usd"]) for p in ctx["vencidos"]) + f" = {usd(total)} (Finanzas, Por cobrar)"],
            "mal"))

    orden = {n: i for i, n in enumerate(NIVELES)}
    salida.sort(key=lambda d: orden[d["nivel"]])
    return salida[:MAX_DIAGNOSTICO]


# ── sugerencias ──────────────────────────────────────────────────────────────

def _rec(regla, clave, titulo, detalle, lineas, impacto, metrica, acciones=None,
         advertencia="", unica_vez=False, confianza=None, supuestos=None) -> dict:
    confianza_regla, tipo = REGLAS[regla]
    return {"regla": regla, "clave": str(clave), "titulo": titulo, "detalle": detalle,
            "calculo": "\n".join(lineas),
            "impacto_mensual": round(impacto, 2) if impacto is not None else None,
            "unica_vez": bool(unica_vez), "confianza": confianza or confianza_regla, "tipo": tipo,
            "advertencia": advertencia, "acciones": acciones or [], "metrica": metrica,
            "supuestos": list(supuestos or [])}


def _lineas_margen(ctx: dict) -> list[str]:
    t = ctx["ticket"]
    return [f"Ticket promedio = {usd(t['total'])} ÷ {t['n']} {_plural(t['n'], 'venta', 'ventas')} = "
            f"{usd(t['valor'])} ({t['alcance']})",
            f"Margen real = (ingresos {usd(ctx['ingresos_ventana'])} − egresos {usd(ctx['egresos_ventana'])}) ÷ "
            f"{usd(ctx['ingresos_ventana'])} = {pct(ctx['margen_pct'], 1)} %",
            f"Margen por venta = {usd(t['valor'])} × {pct(ctx['margen_pct'], 1)} % = {usd(ctx['margen_venta'])}"]


def _r1(db_path: str, hoy: date, ctx: dict) -> list[dict]:
    clientes = listar_clientes_activos(db_path)
    if not clientes:
        return []
    fijos = _fijos_mantenimiento(db_path, ctx["mes"])
    movs = [m for m in ctx["movs"] if m["tipo"] == "ingreso" and m["categoria"] == "mantenimiento"]
    con_cuota = ({r["client_id"] for r, _ in fijos if r["client_id"]}
                 | {m["client_id"] for m in movs if m["client_id"]})
    sin = [c for c in clientes if c["id"] not in con_cuota]
    if not sin:
        return []
    cuota = cuota_mantenimiento(db_path, fijos, movs)
    comision = comision_cobro(db_path)
    n = len(sin)
    impacto = n * cuota["valor"] * (1 - comision["valor"] / 100)
    supuestos = [f"Comisión de cobro {num(comision['valor'])} %: {comision['fuente']}"]
    if cuota["supuesto"]:
        supuestos.append(f"Cuota de {usd(cuota['valor'])}: {cuota['fuente']}")
    nombres = ", ".join(c["name"] for c in sin[:6]) + (" y otros" if n > 6 else "")
    return [_rec(
        "R1", "mantenimiento",
        f"Cobrales mantenimiento a los {n} {_plural(n, 'cliente', 'clientes')} que ya tenés",
        f"De {len(clientes)} clientes activos, {n} no pagan cuota mensual: {nombres}. Ya confían en "
        f"vos, así que es ingreso recurrente sin salir a buscar clientes nuevos.",
        [f"Clientes activos: {len(clientes)}; con ingreso de mantenimiento en Finanzas: "
         f"{len(clientes) - n}; sin: {n}",
         f"Cuota = {usd(cuota['suma'])} ÷ {cuota['n']} = {usd(cuota['valor'])} ({cuota['fuente']})",
         f"Impacto = {n} × {usd(cuota['valor'])} × (1 − {num(comision['valor'])} %) = {usd(impacto)} por mes"],
        impacto, {"mensual": ingreso_mensual_mantenimiento(db_path, hoy, comision["valor"])["neto"],
                  "comision_pct": comision["valor"]},
        confianza="alta" if not cuota["supuesto"] else "media", supuestos=supuestos)]


def _r2_r3(db_path: str, hoy: date, ctx: dict) -> list[dict]:
    if ctx["pauta"]["monedas_raras"]:
        return []
    gasto_prev = ctx["pauta_prev"]["gasto"]
    x = gasto_prev / VENTANA_MESES
    meta = [v for v in ctx["ventas_ventana"] if v["canal"] == "meta_ads"]
    meta_prev = [v for v in ctx["ventas_prev"] if v["canal"] == "meta_ads"]
    pauta = ctx["pauta"]["gasto"]
    if pauta <= 0 or x <= 0:
        return []
    cpv = pauta / len(meta) if meta else None
    margen = ctx["margen_venta"]
    d, h = _dmy(ctx["desde"]), _dmy(ctx["hasta"])
    lineas_pauta = [f"Pauta de Meta: {usd(gasto_prev)} en los 3 meses anteriores ÷ 3 = {usd(x)} por mes",
                    f"Ventas de Meta del {d} al {h}: {len(meta)}"
                    + (f" → costo por venta = {usd(pauta)} ÷ {len(meta)} = {usd(cpv)}" if cpv else "")]

    if cpv is not None and margen is not None and margen > cpv:
        horas, cap = ctx["horas_proyecto"], ctx["capacidad"]
        if not horas or not cap or cap["slots"] is None:
            return []
        vp = x / cpv
        libre = math.floor(cap["slots"] - vp + _EPS)
        if libre < 1:
            return []
        y = x + libre * cpv
        impacto = libre * (margen - cpv)
        supuestos = [f"Horas por proyecto: {num(horas['horas'])} h ({horas['fuente']})"] if horas["estimado"] else []
        lineas = lineas_pauta + _lineas_margen(ctx) + [
            f"Capacidad del mes: {num(cap['netas'])} h netas (Equipo) − {num(cap['comprometidas'])} h en "
            f"{cap['en_curso']} proyectos en curso = {num(cap['libres_horas'])} h ÷ {num(horas['horas'])} h "
            f"por proyecto = {cap['slots']} proyectos",
            f"La pauta actual ya trae {usd(x)} ÷ {usd(cpv)} = {num(vp)} ventas por mes → capacidad libre = "
            f"{cap['slots']} − {num(vp)} = {libre}",
            f"Pauta recomendada = {usd(x)} + {libre} × {usd(cpv)} = {usd(y)}",
            f"Impacto = {libre} × ({usd(margen)} − {usd(cpv)}) = {usd(impacto)} por mes"]
        return [_rec(
            "R2", "meta_ads", f"Subí la pauta de {usd(x)} a {usd(y)}, no más",
            f"Cada venta de Meta deja {usd(margen)} y cuesta {usd(cpv)} conseguirla. El tope lo pone el "
            f"equipo: este mes hay lugar para {libre} {_plural(libre, 'venta más', 'ventas más')}.",
            lineas, impacto,
            {"canal": "meta_ads", "ventas_mes": len(meta_prev) / VENTANA_MESES, "margen_menos_cac": margen - cpv},
            advertencia=f"Pasarte de {usd(y)} tira {usd(cpv)} por cada venta que el equipo no puede hacer.",
            confianza="baja" if horas["estimado"] else "media", supuestos=supuestos)]

    # La pauta no rinde: lo que vuelve por mes no cubre lo que se gasta.
    ventas_mes = len(meta_prev) / VENTANA_MESES
    if meta_prev and margen is None:
        return []
    retorno = ventas_mes * margen if meta_prev else 0.0
    perdida = x - retorno
    if perdida <= 0:
        return []
    lineas = list(lineas_pauta)
    if meta_prev:
        lineas += _lineas_margen(ctx) + [
            f"Vuelve por mes: {num(ventas_mes)} ventas de Meta × {usd(margen)} = {usd(retorno)}"]
        por_que = (f"cada venta de Meta cuesta {usd(cpv)} y deja {usd(margen)}" if cpv
                   else f"lo que vuelve ({usd(retorno)} por mes) no cubre la pauta")
    else:
        lineas.append("Ventas de Meta en los 3 meses anteriores: 0 → vuelve USD 0 por mes")
        por_que = "en los 3 meses anteriores ninguna venta vino de Meta"
    lineas.append(f"Se pierde = pauta {usd(x)} − retorno {usd(retorno)} = {usd(perdida)} por mes")
    return [_rec(
        "R3", "pauta_no_rinde", "Bajá o pausá la pauta de Meta",
        f"La pauta no se paga: {por_que}. Por mes se pierden {usd(perdida)}.",
        lineas, perdida, {"pauta_mes": x}, advertencia=ADVERTENCIA_R3)]


def _r4(db_path: str, hoy: date, ctx: dict) -> list[dict]:
    from services.plantillas import variables_del_lead
    from services.seg_leads import telefonos

    vencidos = ctx["vencidos"]
    if not vencidos:
        return []
    grupos: dict = {}
    for p in vencidos:
        grupos.setdefault(p["client_id"] or f"sin-{p['id']}", []).append(p)
    total = round(sum(float(p["monto_usd"]) for p in vencidos), 2)
    lineas, acciones = [], []
    for suyos in grupos.values():
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
        acciones=acciones, unica_vez=True)]


def _r5(db_path: str, hoy: date, ctx: dict) -> list[dict]:
    if not ctx["fijos"]:
        return []
    crecidos = {c["id"]: c for c in _fijos_crecidos(ctx)}
    ing = ctx["ingresos_promedio"]
    por_cat: dict[str, list] = {}
    for rec, monto in ctx["fijos"]:
        if rec["categoria"] in CATEGORIAS_NO_RECORTABLES:
            continue
        motivos = []
        if rec["id"] in crecidos:
            c = crecidos[rec["id"]]
            motivos.append("es nuevo" if c["nuevo"] else f"pasó de {usd(c['antes'])} a {usd(monto)}")
        if ing > 0 and monto >= FIJO_PESA * ing:
            motivos.append(f"es el {pct(monto / ing)} % de lo que entra por mes")
        if motivos:
            por_cat.setdefault(rec["categoria"], []).append((rec, monto, motivos))
    recs = []
    for cat, filas in por_cat.items():
        total = round(sum(m for _, m, _ in filas), 2)
        etiqueta = ETIQUETA_CATEGORIA.get(cat, cat)
        conceptos = ", ".join(r["concepto"] for r, _, _ in filas)
        lineas = [f"{r['concepto']} ({etiqueta}): {usd(m)} por mes, {' y '.join(mot)}" for r, m, mot in filas]
        if ing > 0:
            lineas.append(f"Ingresos promedio de los 3 meses anteriores: {usd(ing)} por mes")
        lineas.append(f"Impacto = " + " + ".join(usd(m) for _, m, _ in filas) + f" = {usd(total)} por mes")
        recs.append(_rec(
            "R5", cat, f"Probá un mes sin {conceptos}",
            f"Son gastos fijos de {etiqueta} que crecieron o pesan mucho sobre los ingresos, y no están "
            f"atados a ninguna venta.",
            lineas, total, {"ids": [r["id"] for r, _, _ in filas],
                            "montos": {str(r["id"]): m for r, m, _ in filas}, "monto": total},
            advertencia=ADVERTENCIA_R5))
    return recs


def _margen_por_tipo(ctx: dict) -> dict[str, dict]:
    """Por tipo de venta: ticket promedio, horas por proyecto y margen con el costo por hora."""
    ch = ctx["costo_hora"]["valor"]
    general = ctx["horas_proyecto"]
    if ch is None or not general:
        return {}
    horas_tipo: dict[str, list] = {}
    for p in ctx["proyectos"]:
        info = ctx["precios"].get(p["business_id"]) if p["business_id"] else None
        if p["horas"] and info and info["tipo"]:
            horas_tipo.setdefault(info["tipo"], []).append(p["horas"])
    precios_tipo: dict[str, list] = {}
    for v in ctx["ventas"]:
        info = ctx["precios"].get(v["business_id"]) if v["business_id"] else None
        if info and info["tipo"]:
            precios_tipo.setdefault(info["tipo"], []).append((v["nombre"], info))
    salida = {}
    for tipo, filas in precios_tipo.items():
        ticket = sum(i["precio"] for _, i in filas) / len(filas)
        if horas_tipo.get(tipo):
            horas = sum(horas_tipo[tipo]) / len(horas_tipo[tipo])
            fuente, estimado = f"esfuerzo cargado en {len(horas_tipo[tipo])} proyectos de ese tipo", False
        else:
            horas, fuente, estimado = general["horas"], general["fuente"], general["estimado"]
        costo = horas * ch
        salida[tipo] = {"ticket": ticket, "n": len(filas), "horas": horas, "fuente": fuente,
                        "estimado": estimado, "costo": costo, "margen": (ticket - costo) / ticket,
                        "fuente_tipo": filas[0][1]["fuente_tipo"]}
    return salida


def _r6(db_path: str, hoy: date, ctx: dict) -> list[dict]:
    recs = []
    ch = ctx["costo_hora"]
    for tipo, t in _margen_por_tipo(ctx).items():
        if t["margen"] >= MARGEN_MINIMO:
            continue
        etiqueta = ETIQUETA_TIPO.get(tipo, tipo)
        supuestos = [f"Horas por proyecto: {num(t['horas'])} h ({t['fuente']})"] if t["estimado"] else []
        recs.append(_rec(
            "R6", tipo, f"Revisá el precio de {etiqueta}",
            f"Los proyectos de {etiqueta} se venden a {usd(t['ticket'])} y cuestan {usd(t['costo'])} en "
            f"horas del equipo: dejan {pct(t['margen'])} %. No abandones la línea.",
            [f"Costo por hora = egresos sin pauta de los 3 meses anteriores {usd(ch['egresos'])} ÷ "
             f"{num(ch['horas'])} h del equipo = {usd(ch['valor'])}",
             f"Ticket de {etiqueta}: {usd(t['ticket'])} ({t['n']} {_plural(t['n'], 'venta', 'ventas')}, tipo según "
             f"{t['fuente_tipo']})",
             f"Costo = {num(t['horas'])} h × {usd(ch['valor'])} = {usd(t['costo'])}",
             f"Margen = ({usd(t['ticket'])} − {usd(t['costo'])}) ÷ {usd(t['ticket'])} = {pct(t['margen'], 1)} % "
             f"(piso: {pct(MARGEN_MINIMO)} %)"],
            None, {"tipo": tipo, "margen_pct": round(t["margen"] * 100, 2)},
            advertencia=ADVERTENCIA_R6, confianza="media" if t["estimado"] else "alta", supuestos=supuestos))
    return recs


def _r7(db_path: str, hoy: date, ctx: dict) -> list[dict]:
    emb, margen = ctx["embudo_prev"], ctx["margen_venta"]
    if not emb["leads"] or margen is None or margen <= 0:
        return []
    tasa_atendidos = emb["ventas"] / emb["atendieron"] if emb["atendieron"] else 0.0
    etapas = [
        ("no_atiende", "no atienden", emb["no_atiende"], tasa_atendidos,
         f"de los que atienden compra el {pct(tasa_atendidos, 1)} % ({emb['ventas']} ÷ {emb['atendieron']})"),
        ("no_interesa", "dijeron que no les interesa", emb["no_interesa"], tasa_atendidos,
         f"de los que atienden compra el {pct(tasa_atendidos, 1)} % ({emb['ventas']} ÷ {emb['atendieron']})"),
        ("demo_no_cerro", "tuvieron demo y no cerraron", emb["demo_no_cerro"], 1.0,
         "cada uno es una venta que se escapó"),
    ]
    candidatas = []
    for clave, nombre, n, tasa, explicacion in etapas:
        perdidas_ = n * tasa
        if n and perdidas_ > 0:
            candidatas.append((perdidas_ / VENTANA_MESES * margen * RECUPERO_PERDIDAS,
                               clave, nombre, n, tasa, explicacion, perdidas_))
    if not candidatas:
        return []
    impacto, clave, nombre, n, tasa, explicacion, perdidas_ = max(candidatas)
    lineas = [f"Leads de Meta del {_dmy(ctx['desde_prev'])} al {_dmy(ctx['hasta_prev'])}: {emb['leads']}; "
              f"{n} {nombre}",
              f"Ventas perdidas = {n} × {pct(tasa, 1)} % = {num(perdidas_)} ({explicacion})"] + \
        _lineas_margen(ctx) + [
            f"Impacto = {num(perdidas_)} ÷ {VENTANA_MESES} meses × {usd(margen)} × "
            f"{pct(RECUPERO_PERDIDAS)} % de recupero = {usd(impacto)} por mes"]
    motivos = Counter(g["motivo"] for g in perdidas(db_path) if g["motivo"])
    if motivos:
        lineas.append("Motivos cargados a mano: " + ", ".join(
            f"{MOTIVOS[m].lower()} {c}" for m, c in motivos.most_common()))
    return [_rec(
        "R7", clave, f"Recuperá a los {n} que {nombre}",
        f"Es donde más plata se pierde del embudo. Si recuperás 3 de cada 10, suma lo de la derecha.",
        lineas, impacto, {"etapa": clave, "base_mes": n / VENTANA_MESES,
                          "valor_unidad": tasa * margen * RECUPERO_PERDIDAS})]


def _r8(db_path: str, hoy: date, ctx: dict) -> list[dict]:
    gasto = ctx["fijos_total"] + ctx["variables_promedio"]
    if ctx["caja"] is None or gasto <= 0:
        return []
    meses = max(ctx["caja"], 0) / gasto
    if meses >= CAJA_MESES_MINIMO:
        return []
    recortables = sorted((f for f in ctx["fijos"] if f[0]["categoria"] not in CATEGORIAS_NO_RECORTABLES),
                         key=lambda f: -f[1])
    vencido = round(sum(float(p["monto_usd"]) for p in ctx["vencidos"]), 2)
    if not recortables and not vencido:
        return []
    partes, lineas = [], [f"Caja {usd(ctx['caja'])} ÷ gastos del mes {usd(gasto)} = {num(meses)} meses"]
    impacto, unica = None, False
    if recortables:
        rec, monto = recortables[0]
        partes.append(f"frená {rec['concepto']} ({usd(monto)} por mes)")
        lineas.append(f"Fijo más grande que se puede frenar: {rec['concepto']}, {usd(monto)} por mes")
        impacto = monto
    if vencido:
        partes.append(f"cobrá los {usd(vencido)} vencidos")
        lineas.append(f"Cobros vencidos: {usd(vencido)}")
        if impacto is None:
            impacto, unica = vencido, True
    return [_rec(
        "R8", "caja", f"Tenés {num(meses)} meses de caja: " + " o ".join(partes),
        "Con menos de 3 meses de caja, un cobro que se atrasa o un mes flojo te deja sin margen.",
        lineas, impacto, {"caja": ctx["caja"], "meses": meses}, unica_vez=unica)]


def _r9(db_path: str, hoy: date, ctx: dict) -> list[dict]:
    conc = _concentracion(ctx)
    if not conc or conc["share"] < CONCENTRACION_ALTA:
        return []
    return [_rec(
        "R9", "concentracion", f"Sumá clientes: el {pct(conc['share'])} % de lo que entra viene de {conc['nombre']}",
        f"Si {conc['nombre']} se va o se atrasa, se lleva casi la mitad de los ingresos. Priorizá ventas nuevas "
        f"y mantenimiento de otros clientes.",
        [f"{conc['nombre']}: {usd(conc['monto'])} ÷ ingresos de la ventana {usd(conc['total'])} = "
         f"{pct(conc['share'], 1)} % (riesgo desde {pct(CONCENTRACION_ALTA)} %)"],
        None, {"client_id": conc["client_id"], "share": conc["share"]})]


def _r10(db_path: str, hoy: date, ctx: dict) -> list[dict]:
    emb, margen = ctx["embudo_prev"], ctx["margen_venta"]
    if not emb["agendadas"] or not emb["realizadas"] or margen is None or margen <= 0:
        return []
    tasa = emb["realizadas"] / emb["agendadas"]
    if tasa >= DEMOS_REALIZADAS_MINIMO:
        return []
    caidas = emb["agendadas"] - emb["realizadas"]
    cierre = emb["ventas"] / emb["realizadas"]
    impacto = caidas / VENTANA_MESES * cierre * margen
    lineas = [f"Demos agendadas {emb['agendadas']} → realizadas {emb['realizadas']} = {pct(tasa, 1)} % "
              f"(se caen {caidas})",
              f"Cierre después de la demo = {emb['ventas']} ÷ {emb['realizadas']} = {pct(cierre, 1)} %"]
    gasto_prev = ctx["pauta_prev"]["gasto"]
    if gasto_prev > 0:
        costo_demo = gasto_prev / emb["agendadas"]
        lineas.append(f"Cada demo agendada costó {usd(gasto_prev)} ÷ {emb['agendadas']} = {usd(costo_demo)} de "
                      f"pauta → las que se cayeron: {usd(costo_demo * caidas)}")
    lineas += _lineas_margen(ctx) + [
        f"Impacto = {caidas} ÷ {VENTANA_MESES} meses × {pct(cierre, 1)} % × {usd(margen)} = {usd(impacto)} por mes"]
    return [_rec(
        "R10", "demos", f"Confirmá las demos: se caen {caidas} de {emb['agendadas']}",
        "Una demo agendada que no se hace ya costó el lead. Un recordatorio el día anterior y confirmar "
        "por WhatsApp suele alcanzar.",
        lineas, impacto, {"caidas_mes": caidas / VENTANA_MESES, "valor_unidad": cierre * margen})]


REGLAS_EN_ORDEN = (_r1, _r2_r3, _r4, _r5, _r6, _r7, _r8, _r9, _r10)


def calcular(db_path: str, hoy: date) -> dict:
    ctx = _contexto(db_path, hoy)
    recs = []
    for regla in REGLAS_EN_ORDEN:
        try:
            recs += regla(db_path, hoy, ctx)
        except Exception:
            # Una regla que falla no puede dejar sin pantalla al resto.
            logger.warning("inteligencia financiera: falló %s", regla.__name__, exc_info=True)
    return {"recomendaciones": recs, "diagnostico": diagnostico(ctx), "contexto": ctx}


def ordenar(recs: list[dict]) -> list[dict]:
    """Umbral de USD 100, de mayor a menor impacto, máximo 6. Lo que no se mide
    en plata (revisar un precio, la concentración) va al final."""
    visibles = [r for r in recs
                if r["impacto_mensual"] is None or r["impacto_mensual"] >= UMBRAL_IMPACTO_USD]
    visibles.sort(key=lambda r: (r["impacto_mensual"] is None, -(r["impacto_mensual"] or 0)))
    return visibles[:MAX_RECOMENDACIONES]


def encabezado(db_path: str, hoy: date, visibles: list[dict]) -> dict:
    mes = _periodo(hoy)
    ingresos, egresos = _totales(listar_movimientos(db_path, desde=mes, hasta=mes))
    neto = round(ingresos - egresos, 2)
    primeras = [r for r in visibles[:3] if r["impacto_mensual"] is not None]
    con = round(neto + sum(r["impacto_mensual"] for r in primeras), 2)
    return {"mes": mes, "ingresos": ingresos, "egresos": egresos, "hoy": neto, "con_tres": con,
            "calculo_hoy": f"Ingresos {usd(ingresos)} − egresos {usd(egresos)} = {usd(neto)} (Finanzas, mes en curso)",
            "calculo_con_tres": (f"{usd(neto)} + " + " + ".join(usd(r["impacto_mensual"]) for r in primeras)
                                 + f" = {usd(con)}") if primeras else "Sin sugerencias con impacto en plata."}


def _suprimidas(conn, hoy: date) -> set:
    sup = {(f["regla"], f["clave"]) for f in conn.execute(
        "SELECT r.regla, r.clave FROM if_recomendaciones r JOIN if_recomendaciones_tomadas t "
        "ON t.recomendacion_id = r.id WHERE t.resultado = 'midiendo'")}
    for f in conn.execute("SELECT regla, clave, descartada_en FROM if_recomendaciones WHERE estado = 'descartada'"):
        cuando = _parse_iso(f["descartada_en"])
        if cuando and _periodo(hoy_de(cuando)) == _periodo(hoy):
            sup.add((f["regla"], f["clave"]))
    return sup


def recalcular(db_path: str, ahora: datetime | None = None, redactar=None) -> int:
    from services import inteligencia_fin_ia

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
    resumen, origen = inteligencia_fin_ia.redactar(calc["diagnostico"], visibles, llamar=redactar)
    ctx = calc["contexto"]
    generada = _iso(ahora)
    conn = _connect(db_path)
    try:
        cid = conn.execute(
            "INSERT INTO if_calculos (generada_en, avisos, encabezado, contexto, diagnostico, resumen, "
            "resumen_origen) VALUES (?, '[]', ?, ?, ?, ?, ?)",
            (generada, json.dumps(enc, ensure_ascii=False),
             json.dumps({"desde": ctx["desde"].isoformat(), "hasta": ctx["hasta"].isoformat()}),
             json.dumps(calc["diagnostico"], ensure_ascii=False), resumen, origen)).lastrowid
        for r in visibles:
            conn.execute(
                "INSERT INTO if_recomendaciones (calculo_id, regla, clave, titulo, detalle, calculo, "
                "impacto_mensual, unica_vez, confianza, tipo, advertencia, acciones, metrica, generada_en, "
                "supuestos) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cid, r["regla"], r["clave"], r["titulo"], r["detalle"], r["calculo"],
                 r["impacto_mensual"], 1 if r["unica_vez"] else 0, r["confianza"], r["tipo"],
                 r["advertencia"], json.dumps(r["acciones"], ensure_ascii=False),
                 json.dumps(r["metrica"], ensure_ascii=False), generada,
                 json.dumps(r["supuestos"], ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()
    return cid


# ── tomar, descartar y medir a los 30 días ───────────────────────────────────

def _rec_por_id(db_path: str, rec_id: int) -> dict | None:
    filas = _q(db_path, "SELECT * FROM if_recomendaciones WHERE id = ?", (rec_id,))
    return filas[0] if filas else None


def tomar(db_path: str, rec_id: int, quien: str = "", ahora: datetime | None = None):
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
    """(impacto real, cómo se midió), con la misma métrica que usó la regla."""
    if regla == "R1":
        actual = ingreso_mensual_mantenimiento(db_path, hasta, metrica.get("comision_pct") or 0)["neto"]
        base = metrica.get("mensual") or 0
        return actual - base, f"Ingreso mensual por mantenimiento: {usd(base)} al tomarla → {usd(actual)}"
    if regla == "R2":
        n = len([v for v in ventas(db_path) if v["fecha"] and desde <= v["fecha"] <= hasta
                 and v["canal"] == metrica.get("canal")])
        base, factor = metrica.get("ventas_mes") or 0, metrica.get("margen_menos_cac") or 0
        real = (n - base) * factor
        return real, f"Ventas de Meta en 30 días: {n} contra {num(base)} por mes → ({n} − {num(base)}) × {usd(factor)} = {usd(real)}"
    if regla == "R3":
        gasto = pauta_meta(db_path, desde, hasta)["gasto"]
        base = metrica.get("pauta_mes") or 0
        real = base - gasto
        return real, f"Pauta en 30 días: {usd(gasto)} contra {usd(base)} por mes al tomarla → ahorro {usd(real)}"
    if regla == "R4":
        ids = [int(i) for i in metrica.get("ids") or []]
        cobrados = _q(db_path, "SELECT monto_usd FROM finanzas_por_cobrar WHERE cobrado_movimiento_id "
                               f"IS NOT NULL AND id IN ({', '.join('?' for _ in ids) or 'NULL'})", ids)
        real = sum(float(c["monto_usd"] or 0) for c in cobrados)
        return real, f"Cobrados {len(cobrados)} de {len(ids)} saldos vencidos: {usd(real)} de {usd(metrica.get('monto'))}"
    if regla == "R5":
        mes = _periodo(hasta)
        real, cortados = 0.0, 0
        for rid, monto in (metrica.get("montos") or {}).items():
            fijo = get_recurrente(db_path, int(rid))
            if not fijo or not fijo["activo"] or (fijo["hasta"] and fijo["hasta"] < mes):
                real += float(monto)
                cortados += 1
        return real, f"Fijos apagados: {cortados} de {len(metrica.get('montos') or {})}, {usd(real)} por mes"
    if regla == "R6":
        ctx = _contexto(db_path, hasta)
        t = _margen_por_tipo(ctx).get(metrica.get("tipo"))
        actual = t["margen"] * 100 if t else 0.0
        base = metrica.get("margen_pct") or 0
        return actual - base, f"Margen del tipo: {num(base)} % al tomarla → {num(actual)} %"
    if regla == "R7":
        emb = embudo(db_path, desde, hasta)
        n = emb.get(metrica.get("etapa"), 0)
        base, valor = metrica.get("base_mes") or 0, metrica.get("valor_unidad") or 0
        real = (base - n) * valor
        return real, f"Leads en esa etapa en 30 días: {n} contra {num(base)} por mes → {usd(real)}"
    if regla == "R8":
        actual = caja_hoy(db_path, hasta) or 0.0
        base = metrica.get("caja") or 0
        return actual - base, f"Caja: {usd(base)} al tomarla → {usd(actual)}"
    if regla == "R9":
        conc = _concentracion(_contexto(db_path, hasta))
        actual = conc["share"] if conc else 0.0
        base = metrica.get("share") or 0
        return (base - actual) * 100, f"Concentración: {pct(base, 1)} % al tomarla → {pct(actual, 1)} %"
    if regla == "R10":
        emb = embudo(db_path, desde, hasta)
        caidas = emb["agendadas"] - emb["realizadas"]
        base, valor = metrica.get("caidas_mes") or 0, metrica.get("valor_unidad") or 0
        real = (base - caidas) * valor
        return real, f"Demos caídas en 30 días: {caidas} contra {num(base)} por mes → {usd(real)}"
    return 0.0, ""


def evaluar_seguimiento(db_path: str, ahora: datetime | None = None) -> int:
    ahora = ahora or ahora_utc()
    filas = _q(db_path, "SELECT t.id, t.tomada_en, t.impacto_esperado, r.regla, r.metrica "
                        "FROM if_recomendaciones_tomadas t JOIN if_recomendaciones r "
                        "ON r.id = t.recomendacion_id WHERE t.resultado = 'midiendo' AND t.tomada_en <= ?",
               (_iso(ahora - timedelta(days=DIAS_SEGUIMIENTO)),))
    for f in filas:
        desde = hoy_de(_parse_iso(f["tomada_en"]))
        hasta = desde + timedelta(days=DIAS_SEGUIMIENTO)
        try:
            metrica = json.loads(f["metrica"] or "{}")
        except ValueError:
            metrica = {}
        real, detalle = medir(db_path, f["regla"], metrica, desde, hasta)
        if f["regla"] == "R6":
            funciono = (metrica.get("margen_pct") or 0) + real >= MARGEN_MINIMO * 100
        elif f["regla"] == "R9":
            funciono = (metrica.get("share") or 0) - real / 100 < CONCENTRACION_ALTA
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


def corrida_diaria(db_path: str, ahora: datetime | None = None, forzar: bool = False,
                   redactar=None) -> dict | None:
    """Mide lo tomado hace 30 días y recalcula. None si ya corrió hoy.

    El único llamado afuera posible es el resumen con IA, y solo si
    RADIOGRAFIA_IA_ACTIVA está prendida y hay clave: por defecto no sale nada.
    """
    ahora = ahora or ahora_utc()
    with _LOCK:
        if not forzar and ya_corrio_hoy(db_path, ahora):
            return None
        evaluadas = evaluar_seguimiento(db_path, ahora)
        calculo_id = recalcular(db_path, ahora, redactar=redactar)
        _marcar(db_path, ahora)
    return {"calculo_id": calculo_id, "evaluadas": evaluadas}


def start_inteligencia_fin(app) -> None:
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

def _texto_momento(iso: str) -> str:
    dt = _parse_iso(iso)
    return dt.astimezone(_MVD).strftime("%d/%m/%Y %H:%M") if dt else ""


def estado_pantalla(db_path: str, es_admin: bool = False, ahora: datetime | None = None) -> dict:
    ahora = ahora or ahora_utc()
    if not ya_corrio_hoy(db_path, ahora):
        corrida_diaria(db_path, ahora)
    filas = _q(db_path, "SELECT * FROM if_calculos ORDER BY id DESC LIMIT 1")
    calc = filas[0] if filas else None
    recs, diag, enc, ctx = [], [], {}, {}
    if calc:
        diag = json.loads(calc.get("diagnostico") or "[]")
        enc = json.loads(calc["encabezado"] or "{}")
        ctx = json.loads(calc["contexto"] or "{}")
        for r in _q(db_path, "SELECT * FROM if_recomendaciones WHERE calculo_id = ? AND estado = 'nueva'",
                    (calc["id"],)):
            r["acciones"] = json.loads(r["acciones"] or "[]")
            r["supuestos"] = json.loads(r.get("supuestos") or "[]")
            r["unica_vez"] = bool(r["unica_vez"])
            r.pop("metrica", None)
            recs.append(r)
        recs = ordenar(recs)
    seguimiento = []
    for t in _q(db_path, "SELECT t.*, r.titulo, r.regla, r.tipo, r.unica_vez FROM if_recomendaciones_tomadas t "
                         "JOIN if_recomendaciones r ON r.id = t.recomendacion_id "
                         "ORDER BY t.tomada_en DESC, t.id DESC LIMIT ?", (LISTA_MAX,)):
        tomada = _parse_iso(t["tomada_en"])
        t["tomada_el"] = _dmy(hoy_de(tomada)) if tomada else ""
        t["se_mide_el"] = _dmy(hoy_de(tomada) + timedelta(days=DIAS_SEGUIMIENTO)) if tomada else ""
        t["unica_vez"] = bool(t["unica_vez"])
        seguimiento.append(t)
    bajada = ""
    if calc:
        bajada = (f"Calculado el {_texto_momento(calc['generada_en'])} con los datos del "
                  f"{_dmy(_fecha(ctx.get('desde')))} al {_dmy(_fecha(ctx.get('hasta')))} (los 3 meses "
                  f"anteriores y el mes en curso): Finanzas, pauta de Meta, leads, Demos, Clientes, "
                  f"Proyectos y Equipo. Montos en dólares (USD), con la conversión de Finanzas. "
                  f"Se recalcula una vez por día.")
    return {
        "es_admin": bool(es_admin),
        "generada_en": calc["generada_en"] if calc else None,
        "bajada": bajada,
        "resumen": {"texto": (calc or {}).get("resumen") or "", "origen": (calc or {}).get("resumen_origen") or ""},
        "diagnostico": diag,
        "encabezado": enc,
        "recomendaciones": recs,
        "seguimiento": seguimiento,
        "umbral_usd": UMBRAL_IMPACTO_USD,
    }
