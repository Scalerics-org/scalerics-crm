"""La lógica de la sección Equipo: organigrama, ausencias, recuperos y capacidad.

Todo en horas. Esta sección no toca plata: no hay cálculo de descuento ni de
liquidación en ningún lado, y no lo tiene que haber.

Reglas:
- Días hábiles = lunes a viernes. **Sin feriados** en esta versión.
- Saldo de una persona = suma(horas de sus ausencias) − suma(horas de sus
  recuperos). Cero o menos es "al día". Un recupero con fecha futura ya cuenta:
  es un compromiso con fecha.
- Capacidad neta de una semana = horas base − horas de ausencia de esa semana.
  Los recuperos NO suman capacidad libre: esas horas ya están ocupadas.
"""

from __future__ import annotations

import math
import re
from datetime import date, timedelta

from database import (entregas_de_proyectos, listar_ausencias_equipo,
                      listar_personas_equipo, listar_recuperos_equipo)

MOTIVO_MAX = 160
HORAS_MAX = 1000
RANGO_MAX_DIAS = 366
_EPS = 1e-9


# ── fechas ───────────────────────────────────────────────────────────────────

def parse_fecha(valor) -> date | None:
    """'AAAA-MM-DD' estricto a date, o None."""
    if not isinstance(valor, str) or len(valor) != 10:
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


def lunes_de(d: date) -> date:
    return d - timedelta(days=d.weekday())


def dias_habiles(desde: date, hasta: date) -> list[date]:
    """Lunes a viernes entre las dos fechas, inclusive. Sin feriados."""
    if hasta < desde:
        return []
    return [desde + timedelta(days=i) for i in range((hasta - desde).days + 1)
            if (desde + timedelta(days=i)).weekday() < 5]


def _horas(valor) -> float | None:
    if isinstance(valor, bool) or valor is None:
        return None
    try:
        x = float(str(valor).strip().replace(",", "."))
    except ValueError:
        return None
    if not math.isfinite(x) or x <= 0 or x > HORAS_MAX:
        return None
    return round(x, 2)


def _redondear(x: float) -> float:
    return round(x + 0.0, 2)


# ── validación ───────────────────────────────────────────────────────────────

def validar_ausencia(datos, personas: dict[int, dict]) -> tuple[dict | None, str | None]:
    """Devuelve (campos listos para guardar, None) o (None, error)."""
    if not isinstance(datos, dict):
        return None, "faltan los datos de la ausencia"
    pid = datos.get("persona_id")
    try:
        pid = int(pid) if not isinstance(pid, bool) else None
    except (TypeError, ValueError):
        pid = None
    persona = personas.get(pid) if pid is not None else None
    if not persona or not persona.get("activo"):
        return None, "elegí una persona del equipo"
    if not persona.get("lleva_horas"):
        return None, "a esa persona no se le llevan horas"

    desde = parse_fecha(datos.get("fecha_desde"))
    hasta = parse_fecha(datos.get("fecha_hasta"))
    if desde is None:
        return None, "la fecha desde no es válida"
    if hasta is None:
        return None, "la fecha hasta no es válida"
    if hasta < desde:
        return None, "hasta no puede ser anterior a desde"
    if (hasta - desde).days >= RANGO_MAX_DIAS:
        return None, "el rango de fechas es de más de un año"

    motivo = " ".join(str(datos.get("motivo") or "").split())
    if not motivo:
        return None, "falta el motivo"
    if len(motivo) > MOTIVO_MAX:
        return None, f"el motivo va en una línea, hasta {MOTIVO_MAX} caracteres"

    crudo = datos.get("horas_totales")
    if crudo is None or (isinstance(crudo, str) and not crudo.strip()):
        horas = _redondear(len(dias_habiles(desde, hasta)) * float(persona["horas_por_dia"]))
        if horas <= 0:
            return None, "entre esas fechas no hay días hábiles: cargá las horas a mano"
    else:
        horas = _horas(crudo)
        if horas is None:
            return None, "las horas tienen que ser un número mayor que cero"

    return {"persona_id": pid, "fecha_desde": desde.isoformat(),
            "fecha_hasta": hasta.isoformat(), "motivo": motivo,
            "horas_totales": horas}, None


def validar_recupero(datos) -> tuple[dict | None, str | None]:
    if not isinstance(datos, dict):
        return None, "faltan los datos del recupero"
    fecha = parse_fecha(datos.get("fecha"))
    if fecha is None:
        return None, "la fecha del recupero no es válida"
    horas = _horas(datos.get("horas"))
    if horas is None:
        return None, "las horas tienen que ser un número mayor que cero"
    return {"fecha": fecha.isoformat(), "horas": horas}, None


# ── organigrama ──────────────────────────────────────────────────────────────

_CTO = re.compile(r"\bCTO\b")


def organigrama(personas: list[dict]) -> list[dict]:
    """Las personas activas con `reporta_a` normalizado para dibujar.

    Queda en None (fila de arriba) si no reporta a nadie, si su jefe no existe
    o está inactivo, o si la cadena hacia arriba forma un ciclo: sin esto, un
    error de carga dejaba a esa gente fuera del dibujo sin avisar.
    """
    por_id = {p["id"]: p for p in personas}
    jefe = {p["id"]: (p["reporta_a"] if p.get("reporta_a") in por_id
                      and p.get("reporta_a") != p["id"] else None)
            for p in personas}
    for pid in list(jefe):
        vistos, actual = {pid}, jefe[pid]
        while actual is not None:
            if actual in vistos:
                jefe[pid] = None
                break
            vistos.add(actual)
            actual = jefe[actual]
    return [{"id": p["id"], "nombre": p["nombre"], "rol": p.get("rol") or "",
             "reporta_a": jefe[p["id"]], "lleva_horas": bool(p.get("lleva_horas")),
             "destacado": bool(_CTO.search(p.get("rol") or ""))}
            for p in personas]


# ── estado de la pantalla ────────────────────────────────────────────────────

def _entrega(valor) -> date | None:
    """Notion puede mandar '2026-09-15' o '2026-09-15T10:00:00.000-03:00'."""
    return parse_fecha(valor[:10]) if isinstance(valor, str) else None


def estado(db_path: str, hoy: date | None = None, desde: date | None = None) -> dict:
    """Todo lo que pinta la pantalla Equipo, en un solo pedido.

    La grilla son dos semanas: la de hoy y la que viene, o las que arrancan en
    la semana de `desde` para ir hacia adelante o atrás. Sábado y domingo solo
    aparecen si alguien recupera ese día: un recupero agendado tiene que verse
    en verde (Juan, 14/9), y dos columnas vacías por semana no suman nada.
    """
    hoy = hoy or date.today()
    lunes_hoy = lunes_de(hoy)
    lunes = lunes_de(desde) if desde else lunes_hoy

    personas = listar_personas_equipo(db_path)
    con_horas = {p["id"]: p for p in personas if p.get("lleva_horas")}
    ausencias = [a for a in listar_ausencias_equipo(db_path) if a["persona_id"] in con_horas]
    ids_aus = {a["id"] for a in ausencias}
    recuperos = [r for r in listar_recuperos_equipo(db_path) if r["ausencia_id"] in ids_aus]
    rec_por_aus: dict[int, list[dict]] = {}
    for r in recuperos:
        rec_por_aus.setdefault(r["ausencia_id"], []).append(r)

    fechas_rec = {r["fecha"] for r in recuperos}
    semanas = [[d for d in (lunes + timedelta(days=7 * s + i) for i in range(7))
                if d.weekday() < 5 or d.isoformat() in fechas_rec] for s in range(2)]
    primer_dia, ultimo_dia = lunes, lunes + timedelta(days=13)

    detalle = []
    for a in ausencias:
        suyos = rec_por_aus.get(a["id"], [])
        recuperadas = _redondear(sum(r["horas"] for r in suyos))
        pendientes = _redondear(max(a["horas_totales"] - recuperadas, 0))
        detalle.append({
            "id": a["id"], "persona_id": a["persona_id"],
            "persona": con_horas[a["persona_id"]]["nombre"],
            "motivo": a["motivo"], "fecha_desde": a["fecha_desde"],
            "fecha_hasta": a["fecha_hasta"],
            "dias_habiles": len(dias_habiles(parse_fecha(a["fecha_desde"]),
                                             parse_fecha(a["fecha_hasta"]))),
            "horas_totales": _redondear(a["horas_totales"]),
            "horas_recuperadas": recuperadas, "horas_pendientes": pendientes,
            "recuperado": pendientes <= _EPS,
            "recuperos": [{"id": r["id"], "fecha": r["fecha"], "horas": _redondear(r["horas"])}
                          for r in suyos],
        })

    filas = []
    for p in con_horas.values():
        suyas = [d for d in detalle if d["persona_id"] == p["id"]]
        h_aus = _redondear(sum(d["horas_totales"] for d in suyas))
        h_rec = _redondear(sum(d["horas_recuperadas"] for d in suyas))
        saldo = _redondear(h_aus - h_rec)
        dias = {}
        for d in semanas[0] + semanas[1]:
            iso = d.isoformat()
            falta = any(x["fecha_desde"] <= iso <= x["fecha_hasta"] for x in suyas)
            rec = _redondear(sum(r["horas"] for x in suyas for r in x["recuperos"]
                                 if r["fecha"] == iso))
            dias[iso] = {"falta": falta, "recupero": rec}
        filas.append({"id": p["id"], "nombre": p["nombre"],
                      "horas_por_dia": p["horas_por_dia"],
                      "horas_ausencia": h_aus, "horas_recupero": h_rec,
                      "saldo": saldo, "al_dia": saldo <= _EPS, "dias": dias})

    # Cruce con Proyectos. Solo ausencias que no terminaron antes de esta
    # semana: un aviso de algo que ya pasó no le sirve a nadie.
    avisos = []
    entregas = [(e, _entrega(e["timeline_end"])) for e in entregas_de_proyectos(db_path)]
    for a in detalle:
        if a["fecha_hasta"] < lunes_hoy.isoformat():
            continue
        for e, fecha in entregas:
            if fecha and a["fecha_desde"] <= fecha.isoformat() <= a["fecha_hasta"]:
                avisos.append({"proyecto": e["name"], "fecha_entrega": fecha.isoformat(),
                               "persona": a["persona"], "ausencia_id": a["id"]})
    avisos.sort(key=lambda x: (x["fecha_entrega"], x["proyecto"]))

    return {
        "hoy": hoy.isoformat(),
        "esta_semana": lunes_hoy.isoformat(),
        "desde": primer_dia.isoformat(), "hasta": ultimo_dia.isoformat(),
        "semanas": [[d.isoformat() for d in s] for s in semanas],
        "organigrama": organigrama(personas),
        "personas": [{"id": p["id"], "nombre": p["nombre"],
                      "horas_por_dia": p["horas_por_dia"]} for p in con_horas.values()],
        "calendario": filas,
        "ausencias": detalle,
        "avisos": avisos,
    }


# ── capacidad para el simulador ──────────────────────────────────────────────

def capacidad(db_path: str, semana: date | None = None) -> dict:
    """Capacidad neta de una semana (lunes a viernes) de quienes llevan horas.

    Las horas de una ausencia se reparten en partes iguales entre sus días
    hábiles (así una corrección a mano de `horas_totales` se respeta), y a la
    semana le tocan las de sus días. Los recuperos con fecha en la semana se
    informan aparte y no suman capacidad: esas horas ya están comprometidas.
    """
    lunes = lunes_de(semana or date.today())
    viernes, domingo = lunes + timedelta(days=4), lunes + timedelta(days=6)
    habiles_semana = dias_habiles(lunes, viernes)

    personas = [p for p in listar_personas_equipo(db_path) if p.get("lleva_horas")]
    ids = {p["id"] for p in personas}
    ausencias = [a for a in listar_ausencias_equipo(db_path) if a["persona_id"] in ids]
    persona_de_aus = {a["id"]: a["persona_id"] for a in ausencias}
    recuperos = [r for r in listar_recuperos_equipo(db_path) if r["ausencia_id"] in persona_de_aus]

    salida = []
    for p in personas:
        base = len(habiles_semana) * float(p["horas_por_dia"])
        ausente = 0.0
        for a in ausencias:
            if a["persona_id"] != p["id"]:
                continue
            dias = dias_habiles(parse_fecha(a["fecha_desde"]), parse_fecha(a["fecha_hasta"]))
            if not dias:
                continue
            en_semana = sum(1 for d in dias if lunes <= d <= viernes)
            ausente += a["horas_totales"] * en_semana / len(dias)
        ausente = min(ausente, base)
        recupero = sum(r["horas"] for r in recuperos
                       if persona_de_aus[r["ausencia_id"]] == p["id"]
                       and lunes.isoformat() <= r["fecha"] <= domingo.isoformat())
        salida.append({"id": p["id"], "nombre": p["nombre"],
                       "horas_base": _redondear(base),
                       "horas_ausencia": _redondear(ausente),
                       "horas_recupero": _redondear(recupero),
                       "capacidad_neta": _redondear(base - ausente)})

    def total(clave):
        return _redondear(sum(x[clave] for x in salida))

    return {
        "semana": lunes.isoformat(), "hasta": viernes.isoformat(),
        "dias_habiles": len(habiles_semana),
        "personas": salida,
        "totales": {c: total(c) for c in ("horas_base", "horas_ausencia",
                                          "horas_recupero", "capacidad_neta")},
        "nota": ("Capacidad neta = horas base − horas de ausencia. Los recuperos "
                 "no suman capacidad libre. Días hábiles de lunes a viernes, sin feriados."),
    }
