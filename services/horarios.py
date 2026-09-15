"""Horarios de trabajo de cada programador (Recursos Humanos > Horarios).

Un horario es la semana de una persona: para cada día (0 = lunes ... 6 =
domingo) cero o más tramos con hora desde / hasta en 'HH:MM', 24 h. Cero
tramos es "no trabaja". Pedido de Juan, 15/9.

Reglas que valida el servidor:
- formato HH:MM estricto (00:00 a 23:59);
- desde < hasta (un tramo no cruza la medianoche);
- los tramos de un mismo día no se superponen (12:00-14:00 y 14:00-18:00 se
  tocan pero no se pisan: vale).

Esto NO cambia cómo Ausencias calcula horas: `horas_por_dia` sigue siendo el
de Equipo.
"""

from __future__ import annotations

import re

from database import listar_personas_equipo, listar_tramos_horario

DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
TRAMOS_MAX = 6
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def minutos(valor) -> int | None:
    """'HH:MM' a minutos desde las 00:00, o None si no tiene ese formato."""
    if not isinstance(valor, str):
        return None
    m = _HHMM.match(valor)
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def duracion_texto(total: int) -> str:
    """1210 -> '20 h 10 min', 240 -> '4 h', 45 -> '45 min', 0 -> '0 h'."""
    horas, resto = divmod(int(total), 60)
    if not resto:
        return f"{horas} h"
    return f"{horas} h {resto} min" if horas else f"{resto} min"


def validar_semana(datos) -> tuple[list[tuple[int, str, str]] | None, str | None]:
    """{'dias': [7 listas de {desde, hasta}]} -> ([(día, desde, hasta)], None)
    o (None, error). El error nombra el día para que se entienda en pantalla."""
    if not isinstance(datos, dict) or not isinstance(datos.get("dias"), list):
        return None, "faltan los días del horario"
    dias = datos["dias"]
    if len(dias) != 7:
        return None, "el horario tiene que traer los 7 días, de lunes a domingo"

    salida: list[tuple[int, str, str]] = []
    for d, tramos in enumerate(dias):
        nombre = DIAS[d]
        if tramos is None:
            tramos = []
        if not isinstance(tramos, list):
            return None, f"{nombre}: los tramos vienen mal armados"
        if len(tramos) > TRAMOS_MAX:
            return None, f"{nombre}: hasta {TRAMOS_MAX} tramos por día"
        del_dia = []
        for i, t in enumerate(tramos, start=1):
            if not isinstance(t, dict):
                return None, f"{nombre}, tramo {i}: vienen mal armados"
            desde, hasta = t.get("desde"), t.get("hasta")
            m_desde, m_hasta = minutos(desde), minutos(hasta)
            if m_desde is None:
                return None, f"{nombre}, tramo {i}: la hora desde tiene que ser HH:MM"
            if m_hasta is None:
                return None, f"{nombre}, tramo {i}: la hora hasta tiene que ser HH:MM"
            if m_desde >= m_hasta:
                return None, f"{nombre}, tramo {i}: desde tiene que ser antes que hasta"
            del_dia.append((m_desde, m_hasta, desde, hasta))
        del_dia.sort()
        for anterior, siguiente in zip(del_dia, del_dia[1:]):
            if siguiente[0] < anterior[1]:
                return None, (f"{nombre}: los tramos {anterior[2]}–{anterior[3]} y "
                              f"{siguiente[2]}–{siguiente[3]} se superponen")
        salida.extend((d, desde, hasta) for _md, _mh, desde, hasta in del_dia)
    return salida, None


def _nombres_cortos(personas: list[dict]) -> dict[int, str]:
    """En pantalla va el primer nombre; si dos lo comparten, el nombre entero."""
    primero = {p["id"]: (p["nombre"].split() or [p["nombre"]])[0] for p in personas}
    repetidos = [n.lower() for n in primero.values()]
    return {pid: (n if repetidos.count(n.lower()) == 1 else
                  next(p["nombre"] for p in personas if p["id"] == pid))
            for pid, n in primero.items()}


def personas_con_horario(db_path: str) -> list[dict]:
    """Las que aparecen en Horarios: las activas de Equipo que llevan horas."""
    return [p for p in listar_personas_equipo(db_path) if p.get("lleva_horas")]


def estado(db_path: str) -> dict:
    """Todo lo que pinta la pantalla Horarios, en un solo pedido."""
    personas = personas_con_horario(db_path)
    cortos = _nombres_cortos(personas)
    por_dia: dict[tuple[int, int], list[dict]] = {}
    for t in listar_tramos_horario(db_path):
        por_dia.setdefault((t["persona_id"], t["dia"]), []).append(t)

    filas = []
    for p in personas:
        dias, semana = [], 0
        for d in range(7):
            tramos = sorted(por_dia.get((p["id"], d), []), key=lambda t: minutos(t["desde"]) or 0)
            total = sum((minutos(t["hasta"]) or 0) - (minutos(t["desde"]) or 0) for t in tramos)
            semana += total
            dias.append({"dia": d, "nombre": DIAS[d],
                         "tramos": [{"desde": t["desde"], "hasta": t["hasta"]} for t in tramos],
                         "minutos": total, "texto": duracion_texto(total) if tramos else ""})
        filas.append({"id": p["id"], "nombre": p["nombre"], "nombre_corto": cortos[p["id"]],
                      "dias": dias, "minutos_semana": semana,
                      "texto_semana": duracion_texto(semana)})

    # Sábado y domingo solo si alguien trabaja ese día.
    visibles = [d for d in range(7)
                if d < 5 or any(f["dias"][d]["tramos"] for f in filas)]
    return {"dias": [{"dia": d, "nombre": DIAS[d]} for d in range(7)],
            "visibles": visibles, "personas": filas}
