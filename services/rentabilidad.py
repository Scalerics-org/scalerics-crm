"""Rentabilidad de proyectos: horas reales contra lo presupuestado (25/10).

Hasta acá el CRM sabía cuánto se vendió (Finanzas) pero no cuánto costó
hacerlo. Esta sección junta las dos cosas por proyecto:

- el equipo carga horas (a mano o con el reloj) por proyecto y por fase;
- cada persona tiene un costo por hora, que se copia en cada carga, así un
  aumento no reescribe la historia (como los "cost rates" de Harvest);
- con el precio vendido y las horas estimadas sale el consumo del
  presupuesto, el margen, la tarifa efectiva por hora y un semáforo;
- por tipo de proyecto (las claves de services/tipos_proyecto.py) se ve qué
  deja plata y cuánto se desvían las estimaciones, y el estimador sugiere
  horas y precio para el próximo presupuesto con esa historia.

La plata va en pesos. Un proyecto vendido en dólares se pasa con el tipo de
cambio de los ajustes. El panel es `rentabilidad` y, como Finanzas (Ruling
R20), no se reparte desde el código: lo ve admin y Juan lo tilda por rol.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from statistics import median

from services.tipos_proyecto import TIPOS_PROYECTO

UY = timedelta(hours=-3)

FASES: dict[str, str] = {
    "diseno": "Diseño",
    "desarrollo": "Desarrollo",
    "contenido": "Contenido y carga",
    "reuniones": "Reuniones",
    "correcciones": "Correcciones del cliente",
    "soporte": "Entrega y soporte",
}

ESTADOS = ("activo", "pausado", "entregado")
MONEDAS = ("UYU", "USD")

# Una carga no puede pasar de un día largo; un reloj olvidado prendido se
# corta acá y la carga queda con una nota que lo dice.
MAX_MIN_CARGA = 12 * 60
# Umbrales del semáforo.
RIESGO_CONSUMO = 0.8          # 80 % de las horas estimadas usadas sin entregar
RIESGO_ADELANTO = 0.2         # el consumo va 20 puntos por delante del calendario


class NoSePuede(ValueError):
    """Lo que se pidió no se puede hacer; el mensaje se muestra tal cual."""


# ── tiempo ───────────────────────────────────────────────────────────────────

def ahora_uy() -> datetime:
    return (datetime.now(timezone.utc) + UY).replace(tzinfo=None, microsecond=0)


def hoy_uy() -> date:
    return ahora_uy().date()


def _fecha(valor, campo: str, obligatoria: bool = False) -> str | None:
    texto = str(valor or "").strip()
    if not texto:
        if obligatoria:
            raise NoSePuede(f"Falta la fecha de {campo}")
        return None
    try:
        return date.fromisoformat(texto).isoformat()
    except ValueError:
        raise NoSePuede(f"La fecha de {campo} no es válida") from None


# ── base ─────────────────────────────────────────────────────────────────────

def init_rentabilidad(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS rt_proyectos (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre           TEXT NOT NULL,
            cliente          TEXT,
            tipo             TEXT NOT NULL DEFAULT 'web',
            moneda           TEXT NOT NULL DEFAULT 'UYU',
            precio           REAL NOT NULL DEFAULT 0,
            horas_estimadas  REAL NOT NULL DEFAULT 0,
            inicio           TEXT,
            entrega          TEXT,
            estado           TEXT NOT NULL DEFAULT 'activo',
            notas            TEXT,
            creado_por       TEXT,
            creado_en        TEXT NOT NULL,
            cerrado_en       TEXT
        );
        CREATE TABLE IF NOT EXISTS rt_costos (
            persona_id  INTEGER PRIMARY KEY REFERENCES equipo_personas(id) ON DELETE CASCADE,
            costo_hora  REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rt_horas (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            proyecto_id  INTEGER NOT NULL REFERENCES rt_proyectos(id) ON DELETE CASCADE,
            persona_id   INTEGER NOT NULL REFERENCES equipo_personas(id) ON DELETE CASCADE,
            fecha        TEXT NOT NULL,
            minutos      INTEGER NOT NULL,
            fase         TEXT NOT NULL DEFAULT 'desarrollo',
            nota         TEXT,
            costo_hora   REAL,
            origen       TEXT NOT NULL DEFAULT 'manual',
            creado_por   TEXT,
            creado_en    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rt_horas_proyecto ON rt_horas(proyecto_id);
        CREATE INDEX IF NOT EXISTS idx_rt_horas_fecha ON rt_horas(fecha);
        CREATE TABLE IF NOT EXISTS rt_relojes (
            persona_id   INTEGER PRIMARY KEY REFERENCES equipo_personas(id) ON DELETE CASCADE,
            proyecto_id  INTEGER NOT NULL REFERENCES rt_proyectos(id) ON DELETE CASCADE,
            fase         TEXT NOT NULL,
            desde        TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rt_ajustes (
            id               INTEGER PRIMARY KEY CHECK (id = 1),
            tipo_cambio      REAL NOT NULL DEFAULT 40,
            margen_objetivo  REAL NOT NULL DEFAULT 40
        );
        INSERT OR IGNORE INTO rt_ajustes (id) VALUES (1);
    """)


def _conn(db: str) -> sqlite3.Connection:
    c = sqlite3.connect(db, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


# ── validación ───────────────────────────────────────────────────────────────

def _texto(valor, campo: str, largo: int, obligatorio: bool = False) -> str | None:
    texto = str(valor or "").strip()
    if not texto:
        if obligatorio:
            raise NoSePuede(f"Falta {campo}")
        return None
    return texto[:largo]


def _numero(valor, campo: str, minimo: float = 0, maximo: float = 1e9) -> float:
    texto = str(valor if valor is not None else "").strip().replace(" ", "")
    if not texto:
        return 0.0
    # 1.500,50 / 1500.5 / 1500
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        n = float(texto)
    except ValueError:
        raise NoSePuede(f"{campo} tiene que ser un número") from None
    if n != n or n < minimo or n > maximo:
        raise NoSePuede(f"{campo} está fuera de rango")
    return n


_DUR = re.compile(r"^(?:(\d{1,2})\s*h)?\s*(?:(\d{1,3})\s*m(?:in)?)?$")


def minutos_de(valor) -> int:
    """'1:30', '1,5', '1.5', '90m', '2h', '1h 15m' o un número de horas -> minutos."""
    texto = str(valor if valor is not None else "").strip().lower()
    if not texto:
        raise NoSePuede("Falta cuánto tiempo")
    m = re.fullmatch(r"(\d{1,2}):([0-5]\d)", texto)
    if m:
        total = int(m.group(1)) * 60 + int(m.group(2))
    elif re.fullmatch(r"\d{1,2}(?:[.,]\d{1,2})?", texto):
        total = round(float(texto.replace(",", ".")) * 60)
    else:
        m = _DUR.fullmatch(texto)
        if not m or not (m.group(1) or m.group(2)):
            raise NoSePuede("El tiempo se escribe 1:30, 1,5 o 90m")
        total = int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    if total <= 0:
        raise NoSePuede("El tiempo tiene que ser mayor a cero")
    if total > MAX_MIN_CARGA:
        raise NoSePuede("Una carga no puede pasar de 12 horas")
    return total


def _validar_proyecto(data: dict) -> dict:
    tipo = str(data.get("tipo") or "web").strip()
    if tipo not in TIPOS_PROYECTO:
        raise NoSePuede("Elegí de qué es el proyecto")
    moneda = str(data.get("moneda") or "UYU").strip().upper()
    if moneda not in MONEDAS:
        raise NoSePuede("La moneda es UYU o USD")
    estado = str(data.get("estado") or "activo").strip()
    if estado not in ESTADOS:
        raise NoSePuede("Estado no válido")
    inicio = _fecha(data.get("inicio"), "inicio")
    entrega = _fecha(data.get("entrega"), "entrega")
    if inicio and entrega and entrega < inicio:
        raise NoSePuede("La entrega no puede ser antes del inicio")
    return {
        "nombre": _texto(data.get("nombre"), "el nombre del proyecto", 120, obligatorio=True),
        "cliente": _texto(data.get("cliente"), "el cliente", 120),
        "tipo": tipo,
        "moneda": moneda,
        "precio": _numero(data.get("precio"), "El precio"),
        "horas_estimadas": _numero(data.get("horas_estimadas"), "Las horas estimadas", 0, 10000),
        "inicio": inicio,
        "entrega": entrega,
        "estado": estado,
        "notas": _texto(data.get("notas"), "las notas", 1000),
    }


# ── ajustes y costos ─────────────────────────────────────────────────────────

def ajustes(db: str) -> dict:
    c = _conn(db)
    try:
        f = c.execute("SELECT tipo_cambio, margen_objetivo FROM rt_ajustes WHERE id = 1").fetchone()
    finally:
        c.close()
    return dict(f) if f else {"tipo_cambio": 40.0, "margen_objetivo": 40.0}


def guardar_ajustes(db: str, data: dict) -> dict:
    tc = _numero(data.get("tipo_cambio"), "El tipo de cambio", 1, 1000)
    margen = _numero(data.get("margen_objetivo"), "El margen objetivo", 0, 90)
    if not tc:
        raise NoSePuede("Falta el tipo de cambio")
    c = _conn(db)
    try:
        c.execute("UPDATE rt_ajustes SET tipo_cambio = ?, margen_objetivo = ? WHERE id = 1", (tc, margen))
        c.commit()
    finally:
        c.close()
    return ajustes(db)


def personas(db: str) -> list[dict]:
    """Las que llevan horas en Equipo, con su costo y su reloj si está prendido."""
    c = _conn(db)
    try:
        filas = c.execute("""
            SELECT p.id, p.nombre, p.rol, p.horas_por_dia, k.costo_hora,
                   r.proyecto_id AS reloj_proyecto, r.fase AS reloj_fase, r.desde AS reloj_desde,
                   x.nombre AS reloj_proyecto_nombre
            FROM equipo_personas p
            LEFT JOIN rt_costos k ON k.persona_id = p.id
            LEFT JOIN rt_relojes r ON r.persona_id = p.id
            LEFT JOIN rt_proyectos x ON x.id = r.proyecto_id
            WHERE p.activo = 1 AND p.lleva_horas = 1
            ORDER BY p.id""").fetchall()
    finally:
        c.close()
    return [dict(f) for f in filas]


def _persona_activa(c: sqlite3.Connection, persona_id) -> sqlite3.Row:
    try:
        pid = int(persona_id)
    except (TypeError, ValueError):
        raise NoSePuede("Elegí quién trabajó") from None
    f = c.execute("SELECT id, nombre FROM equipo_personas WHERE id = ? AND activo = 1 AND lleva_horas = 1",
                  (pid,)).fetchone()
    if not f:
        raise NoSePuede("Esa persona no existe o no lleva horas en Equipo")
    return f


def guardar_costo(db: str, persona_id, costo) -> None:
    valor = _numero(costo, "El costo por hora", 0, 100000)
    c = _conn(db)
    try:
        _persona_activa(c, persona_id)
        if valor:
            c.execute("INSERT INTO rt_costos (persona_id, costo_hora) VALUES (?, ?) "
                      "ON CONFLICT(persona_id) DO UPDATE SET costo_hora = excluded.costo_hora",
                      (int(persona_id), valor))
        else:
            c.execute("DELETE FROM rt_costos WHERE persona_id = ?", (int(persona_id),))
        c.commit()
    finally:
        c.close()


# ── proyectos ────────────────────────────────────────────────────────────────

def crear_proyecto(db: str, data: dict, quien: str) -> int:
    p = _validar_proyecto(data)
    c = _conn(db)
    try:
        cur = c.execute(
            "INSERT INTO rt_proyectos (nombre, cliente, tipo, moneda, precio, horas_estimadas, inicio, entrega, "
            "estado, notas, creado_por, creado_en, cerrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (p["nombre"], p["cliente"], p["tipo"], p["moneda"], p["precio"], p["horas_estimadas"], p["inicio"],
             p["entrega"], p["estado"], p["notas"], quien, ahora_uy().isoformat(" "),
             hoy_uy().isoformat() if p["estado"] == "entregado" else None))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def actualizar_proyecto(db: str, pid: int, data: dict) -> None:
    p = _validar_proyecto(data)
    c = _conn(db)
    try:
        viejo = c.execute("SELECT estado, cerrado_en FROM rt_proyectos WHERE id = ?", (pid,)).fetchone()
        if not viejo:
            raise NoSePuede("Ese proyecto no existe")
        cerrado = viejo["cerrado_en"]
        if p["estado"] == "entregado" and viejo["estado"] != "entregado":
            cerrado = hoy_uy().isoformat()
        elif p["estado"] != "entregado":
            cerrado = None
        c.execute(
            "UPDATE rt_proyectos SET nombre=?, cliente=?, tipo=?, moneda=?, precio=?, horas_estimadas=?, inicio=?, "
            "entrega=?, estado=?, notas=?, cerrado_en=? WHERE id = ?",
            (p["nombre"], p["cliente"], p["tipo"], p["moneda"], p["precio"], p["horas_estimadas"], p["inicio"],
             p["entrega"], p["estado"], p["notas"], cerrado, pid))
        if p["estado"] == "entregado":
            # Un proyecto cerrado no sigue sumando horas por un reloj olvidado.
            c.execute("DELETE FROM rt_relojes WHERE proyecto_id = ?", (pid,))
        c.commit()
    finally:
        c.close()


def borrar_proyecto(db: str, pid: int) -> None:
    c = _conn(db)
    try:
        if not c.execute("DELETE FROM rt_proyectos WHERE id = ?", (pid,)).rowcount:
            raise NoSePuede("Ese proyecto no existe")
        c.commit()
    finally:
        c.close()


# ── horas ────────────────────────────────────────────────────────────────────

def _insertar_horas(c: sqlite3.Connection, proyecto_id: int, persona_id: int, fecha: str, minutos: int,
                    fase: str, nota: str | None, origen: str, quien: str) -> int:
    costo = c.execute("SELECT costo_hora FROM rt_costos WHERE persona_id = ?", (persona_id,)).fetchone()
    cur = c.execute(
        "INSERT INTO rt_horas (proyecto_id, persona_id, fecha, minutos, fase, nota, costo_hora, origen, creado_por, "
        "creado_en) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (proyecto_id, persona_id, fecha, minutos, fase, nota, costo["costo_hora"] if costo else None, origen, quien,
         ahora_uy().isoformat(" ")))
    return cur.lastrowid


def _proyecto_abierto(c: sqlite3.Connection, proyecto_id) -> int:
    try:
        pid = int(proyecto_id)
    except (TypeError, ValueError):
        raise NoSePuede("Elegí el proyecto") from None
    f = c.execute("SELECT estado FROM rt_proyectos WHERE id = ?", (pid,)).fetchone()
    if not f:
        raise NoSePuede("Ese proyecto no existe")
    if f["estado"] == "entregado":
        raise NoSePuede("El proyecto ya está entregado: reabrilo para cargarle horas")
    return pid


def _fase(valor) -> str:
    fase = str(valor or "desarrollo").strip()
    if fase not in FASES:
        raise NoSePuede("Elegí la fase")
    return fase


def cargar_horas(db: str, data: dict, quien: str) -> int:
    minutos = minutos_de(data.get("tiempo"))
    fase = _fase(data.get("fase"))
    fecha = _fecha(data.get("fecha") or hoy_uy().isoformat(), "la carga", obligatoria=True)
    hoy = hoy_uy()
    if fecha > hoy.isoformat():
        raise NoSePuede("No se cargan horas a futuro")
    if fecha < (hoy - timedelta(days=366)).isoformat():
        raise NoSePuede("Esa fecha es de hace más de un año")
    nota = _texto(data.get("nota"), "la nota", 300)
    c = _conn(db)
    try:
        c.execute("BEGIN IMMEDIATE")
        persona = _persona_activa(c, data.get("persona_id"))
        pid = _proyecto_abierto(c, data.get("proyecto_id"))
        nuevo = _insertar_horas(c, pid, persona["id"], fecha, minutos, fase, nota, "manual", quien)
        c.commit()
        return nuevo
    finally:
        c.close()


def borrar_horas(db: str, hid: int) -> None:
    c = _conn(db)
    try:
        if not c.execute("DELETE FROM rt_horas WHERE id = ?", (hid,)).rowcount:
            raise NoSePuede("Esa carga no existe")
        c.commit()
    finally:
        c.close()


def prender_reloj(db: str, data: dict) -> None:
    fase = _fase(data.get("fase"))
    c = _conn(db)
    try:
        c.execute("BEGIN IMMEDIATE")
        persona = _persona_activa(c, data.get("persona_id"))
        pid = _proyecto_abierto(c, data.get("proyecto_id"))
        if c.execute("SELECT 1 FROM rt_relojes WHERE persona_id = ?", (persona["id"],)).fetchone():
            raise NoSePuede(f"{persona['nombre']} ya tiene el reloj prendido: frenalo primero")
        c.execute("INSERT INTO rt_relojes (persona_id, proyecto_id, fase, desde) VALUES (?,?,?,?)",
                  (persona["id"], pid, fase, ahora_uy().isoformat(" ")))
        c.commit()
    finally:
        c.close()


def frenar_reloj(db: str, persona_id, quien: str, nota=None) -> dict:
    """Apaga el reloj y deja la carga. Menos de un minuto no se guarda."""
    c = _conn(db)
    try:
        c.execute("BEGIN IMMEDIATE")
        persona = _persona_activa(c, persona_id)
        r = c.execute("SELECT proyecto_id, fase, desde FROM rt_relojes WHERE persona_id = ?",
                      (persona["id"],)).fetchone()
        if not r:
            raise NoSePuede(f"{persona['nombre']} no tiene el reloj prendido")
        c.execute("DELETE FROM rt_relojes WHERE persona_id = ?", (persona["id"],))
        desde = datetime.fromisoformat(r["desde"])
        minutos = int((ahora_uy() - desde).total_seconds() // 60)
        texto = _texto(nota, "la nota", 300)
        if minutos > MAX_MIN_CARGA:
            minutos = MAX_MIN_CARGA
            texto = ((texto + " · ") if texto else "") + "Reloj olvidado prendido: se recortó a 12 h"
        hid = None
        if minutos >= 1:
            hid = _insertar_horas(c, r["proyecto_id"], persona["id"], desde.date().isoformat(), minutos, r["fase"],
                                  texto, "reloj", quien)
        c.commit()
        return {"id": hid, "minutos": max(minutos, 0)}
    finally:
        c.close()


# ── números ──────────────────────────────────────────────────────────────────

def _a_pesos(monto: float, moneda: str, tc: float) -> float:
    return float(monto or 0) * (tc if moneda == "USD" else 1)


def _costo_carga(h: dict, costos: dict) -> float | None:
    k = h["costo_hora"] if h["costo_hora"] is not None else costos.get(h["persona_id"])
    return None if k is None else h["minutos"] / 60 * k


def _redondo(n, d=1):
    return None if n is None else round(n, d)


def metricas(p: dict, horas: list[dict], costos: dict, aj: dict, hoy: date, costo_medio: float | None) -> dict:
    """Los números de un proyecto. `horas` son sus cargas."""
    tc = aj["tipo_cambio"]
    minutos = sum(h["minutos"] for h in horas)
    hs = minutos / 60
    costo = 0.0
    sin_costo = 0
    for h in horas:
        k = _costo_carga(h, costos)
        if k is None:
            sin_costo += h["minutos"]
        else:
            costo += k
    ingreso = _a_pesos(p["precio"], p["moneda"], tc)
    est = float(p["horas_estimadas"] or 0)
    consumo = hs / est if est else None

    avance = None
    if p["inicio"] and p["entrega"]:
        ini, fin = date.fromisoformat(p["inicio"]), date.fromisoformat(p["entrega"])
        total = (fin - ini).days
        avance = 1.0 if total <= 0 else min(1.0, max(0.0, (hoy - ini).days / total))

    abierto = p["estado"] != "entregado"
    # Proyección al cierre: un proyecto abierto va a usar al menos lo estimado.
    hs_final = max(hs, est) if abierto else hs
    k_medio = (costo / (hs - sin_costo / 60)) if hs - sin_costo / 60 > 0 else costo_medio
    costo_final = costo + (hs_final - hs) * (k_medio or 0) + (sin_costo / 60) * (k_medio or 0)
    margen_final = ingreso - costo_final if ingreso else None

    if not abierto:
        salud = "cerrado"
    elif consumo is not None and consumo > 1:
        salud = "pasado"
    elif p["entrega"] and p["entrega"] < hoy.isoformat():
        salud = "atrasado"
    elif consumo is not None and (consumo >= RIESGO_CONSUMO or
                                  (avance is not None and consumo - avance > RIESGO_ADELANTO)):
        salud = "riesgo"
    else:
        salud = "ok"

    return {
        "horas": _redondo(hs),
        "horas_estimadas": _redondo(est),
        "consumo_pct": _redondo(consumo * 100 if consumo is not None else None, 0),
        "avance_pct": _redondo(avance * 100 if avance is not None else None, 0),
        "ingreso": round(ingreso),
        "costo": round(costo),
        "horas_sin_costo": _redondo(sin_costo / 60),
        "margen": round(ingreso - costo) if ingreso else None,
        "margen_pct": _redondo((ingreso - costo) / ingreso * 100, 0) if ingreso else None,
        "tarifa_efectiva": round(ingreso / hs) if ingreso and hs else None,
        "tarifa_vendida": round(ingreso / est) if ingreso and est else None,
        "horas_final": _redondo(hs_final),
        "margen_final": round(margen_final) if margen_final is not None else None,
        "margen_final_pct": _redondo(margen_final / ingreso * 100, 0) if ingreso else None,
        "salud": salud,
    }


def _datos(db: str):
    c = _conn(db)
    try:
        proyectos = [dict(f) for f in c.execute("SELECT * FROM rt_proyectos ORDER BY id DESC")]
        horas = [dict(f) for f in c.execute(
            "SELECT h.*, p.nombre AS persona FROM rt_horas h JOIN equipo_personas p ON p.id = h.persona_id")]
        costos = {f["persona_id"]: f["costo_hora"] for f in c.execute("SELECT * FROM rt_costos")}
    finally:
        c.close()
    return proyectos, horas, costos


def _costo_medio(horas: list[dict], costos: dict, hoy: date) -> float | None:
    """Costo por hora promedio del equipo en los últimos 90 días, ponderado por horas."""
    desde = (hoy - timedelta(days=90)).isoformat()
    total = mins = 0.0
    for h in horas:
        k = _costo_carga(h, costos)
        if h["fecha"] >= desde and k is not None:
            total += k
            mins += h["minutos"]
    if mins:
        return total / (mins / 60)
    return (sum(costos.values()) / len(costos)) if costos else None


def _semanas(hoy: date, n: int = 4) -> list[date]:
    lunes = hoy - timedelta(days=hoy.weekday())
    return [lunes - timedelta(weeks=i) for i in range(n - 1, -1, -1)]


def tablero(db: str, hoy: date | None = None) -> dict:
    hoy = hoy or hoy_uy()
    aj = ajustes(db)
    proyectos, horas, costos = _datos(db)
    k_medio = _costo_medio(horas, costos, hoy)
    por_proy: dict[int, list] = {}
    for h in horas:
        por_proy.setdefault(h["proyecto_id"], []).append(h)

    filas = []
    for p in proyectos:
        m = metricas(p, por_proy.get(p["id"], []), costos, aj, hoy, k_medio)
        ultima = max((h["fecha"] for h in por_proy.get(p["id"], [])), default=None)
        filas.append({**p, "tipo_etiqueta": TIPOS_PROYECTO.get(p["tipo"], p["tipo"]), "metricas": m,
                      "ultima_carga": ultima})
    orden = {"pasado": 0, "atrasado": 1, "riesgo": 2, "ok": 3, "cerrado": 4}
    filas.sort(key=lambda f: (orden[f["metricas"]["salud"]], -(f["metricas"]["consumo_pct"] or 0)))

    mes = hoy.replace(day=1).isoformat()
    hace90 = (hoy - timedelta(days=90)).isoformat()
    min_mes = sum(h["minutos"] for h in horas if h["fecha"] >= mes)
    min_90 = sum(h["minutos"] for h in horas if h["fecha"] >= hace90)
    min_corr_90 = sum(h["minutos"] for h in horas if h["fecha"] >= hace90 and h["fase"] == "correcciones")
    cerrados_90 = [f for f in filas if f["estado"] == "entregado" and (f["cerrado_en"] or "") >= hace90
                   and f["metricas"]["ingreso"]]
    ingreso_c = sum(f["metricas"]["ingreso"] for f in cerrados_90)
    costo_c = sum(f["metricas"]["costo"] for f in cerrados_90)
    horas_c = sum(f["metricas"]["horas"] for f in cerrados_90)

    equipo = utilizacion(db, hoy, horas)
    cap = sum(e["capacidad"] for e in equipo)
    carg = sum(e["horas_4s"] for e in equipo)

    kpis = {
        "horas_mes": _redondo(min_mes / 60),
        "utilizacion_pct": _redondo(carg / cap * 100, 0) if cap else None,
        "margen_entregados_pct": _redondo((ingreso_c - costo_c) / ingreso_c * 100, 0) if ingreso_c else None,
        "entregados_90": len(cerrados_90),
        "tarifa_efectiva": round(ingreso_c / horas_c) if horas_c else None,
        "en_riesgo": sum(1 for f in filas if f["metricas"]["salud"] in ("riesgo", "pasado", "atrasado")),
        "activos": sum(1 for f in filas if f["estado"] != "entregado"),
        "correcciones_pct": _redondo(min_corr_90 / min_90 * 100, 0) if min_90 else None,
        "costo_medio_hora": round(k_medio) if k_medio else None,
        "margen_objetivo": aj["margen_objetivo"],
    }
    return {"kpis": kpis, "proyectos": filas, "por_tipo": por_tipo(filas), "equipo": equipo,
            "fases_90": _por_fase([h for h in horas if h["fecha"] >= hace90])}


def _por_fase(horas: list[dict]) -> list[dict]:
    total = sum(h["minutos"] for h in horas)
    salida = []
    for k, etiqueta in FASES.items():
        mins = sum(h["minutos"] for h in horas if h["fase"] == k)
        if mins:
            salida.append({"fase": k, "etiqueta": etiqueta, "horas": _redondo(mins / 60),
                           "pct": _redondo(mins / total * 100, 0)})
    return salida


def utilizacion(db: str, hoy: date, horas: list[dict] | None = None) -> list[dict]:
    """Horas cargadas por persona en las últimas 4 semanas contra su capacidad."""
    if horas is None:
        horas = _datos(db)[1]
    semanas = _semanas(hoy)
    desde = semanas[0].isoformat()
    salida = []
    for p in personas(db):
        suyas = [h for h in horas if h["persona_id"] == p["id"] and h["fecha"] >= desde]
        por_semana = []
        for s in semanas:
            fin = (s + timedelta(days=7)).isoformat()
            por_semana.append(_redondo(sum(h["minutos"] for h in suyas if s.isoformat() <= h["fecha"] < fin) / 60))
        semanal = float(p["horas_por_dia"] or 0) * 5
        # La semana en curso cuenta solo los días hábiles que ya pasaron.
        dias_hoy = min(hoy.weekday() + 1, 5)
        capacidad = semanal * (len(semanas) - 1) + float(p["horas_por_dia"] or 0) * dias_hoy
        total = sum(h["minutos"] for h in suyas) / 60
        salida.append({"id": p["id"], "nombre": p["nombre"], "rol": p["rol"], "costo_hora": p["costo_hora"],
                       "semanas": por_semana, "semana_desde": [s.isoformat() for s in semanas],
                       "capacidad_semanal": _redondo(semanal), "capacidad": _redondo(capacidad),
                       "horas_4s": _redondo(total),
                       "utilizacion_pct": _redondo(total / capacidad * 100, 0) if capacidad else None,
                       "reloj": {"proyecto_id": p["reloj_proyecto"], "proyecto": p["reloj_proyecto_nombre"],
                                 "fase": p["reloj_fase"], "desde": p["reloj_desde"]} if p["reloj_proyecto"] else None})
    return salida


def por_tipo(filas: list[dict]) -> list[dict]:
    """Lo que dejó cada tipo de proyecto, con los entregados."""
    salida = []
    for clave, etiqueta in TIPOS_PROYECTO.items():
        del_tipo = [f for f in filas if f["tipo"] == clave]
        cerrados = [f for f in del_tipo if f["estado"] == "entregado" and f["metricas"]["horas"]]
        if not del_tipo:
            continue
        desvios = [f["metricas"]["horas"] / f["metricas"]["horas_estimadas"] - 1
                   for f in cerrados if f["metricas"]["horas_estimadas"]]
        tarifas = [f["metricas"]["tarifa_efectiva"] for f in cerrados if f["metricas"]["tarifa_efectiva"]]
        margenes = [f["metricas"]["margen_pct"] for f in cerrados if f["metricas"]["margen_pct"] is not None]
        salida.append({
            "tipo": clave, "etiqueta": etiqueta,
            "activos": sum(1 for f in del_tipo if f["estado"] != "entregado"),
            "entregados": len(cerrados),
            "horas_mediana": _redondo(median(f["metricas"]["horas"] for f in cerrados)) if cerrados else None,
            "desvio_pct": _redondo(sum(desvios) / len(desvios) * 100, 0) if desvios else None,
            "tarifa_mediana": round(median(tarifas)) if tarifas else None,
            "margen_medio_pct": _redondo(sum(margenes) / len(margenes), 0) if margenes else None,
        })
    return salida


def detalle(db: str, pid: int, hoy: date | None = None) -> dict:
    hoy = hoy or hoy_uy()
    aj = ajustes(db)
    proyectos, horas, costos = _datos(db)
    p = next((x for x in proyectos if x["id"] == pid), None)
    if not p:
        raise NoSePuede("Ese proyecto no existe")
    suyas = sorted((h for h in horas if h["proyecto_id"] == pid), key=lambda h: (h["fecha"], h["id"]))
    m = metricas(p, suyas, costos, aj, hoy, _costo_medio(horas, costos, hoy))
    personas_p: dict[str, float] = {}
    for h in suyas:
        personas_p[h["persona"]] = personas_p.get(h["persona"], 0) + h["minutos"]
    # Consumo acumulado por semana contra lo estimado: el burn-up de Harvest.
    serie = []
    if suyas:
        primera = date.fromisoformat(min(h["fecha"] for h in suyas))
        lunes = primera - timedelta(days=primera.weekday())
        fin = hoy if p["estado"] != "entregado" else date.fromisoformat(max(h["fecha"] for h in suyas))
        acumulado = 0
        while lunes <= fin and len(serie) < 104:
            prox = lunes + timedelta(days=7)
            acumulado += sum(h["minutos"] for h in suyas if lunes.isoformat() <= h["fecha"] < prox.isoformat())
            serie.append({"semana": lunes.isoformat(), "horas": _redondo(acumulado / 60)})
            lunes = prox
    cargas = [{"id": h["id"], "fecha": h["fecha"], "persona": h["persona"], "horas": _redondo(h["minutos"] / 60, 2),
               "fase": h["fase"], "fase_etiqueta": FASES.get(h["fase"], h["fase"]), "nota": h["nota"],
               "origen": h["origen"], "creado_por": h["creado_por"]} for h in reversed(suyas[-300:])]
    return {"proyecto": {**p, "tipo_etiqueta": TIPOS_PROYECTO.get(p["tipo"], p["tipo"])}, "metricas": m,
            "fases": _por_fase(suyas),
            "personas": [{"nombre": k, "horas": _redondo(v / 60)} for k, v in
                         sorted(personas_p.items(), key=lambda kv: -kv[1])],
            "serie": serie, "cargas": cargas}


def _redondear_precio(n: float, moneda: str) -> int:
    paso = 10 if moneda == "USD" else 500
    return int(round(n / paso) * paso)


def estimar(db: str, tipo: str, horas_propuestas=None, hoy: date | None = None) -> dict:
    """Horas y precio sugeridos para un proyecto nuevo de ese tipo, con la historia."""
    if tipo not in TIPOS_PROYECTO:
        raise NoSePuede("Elegí de qué es el proyecto")
    hoy = hoy or hoy_uy()
    t = tablero(db, hoy)
    aj = ajustes(db)
    fila = next((x for x in t["por_tipo"] if x["tipo"] == tipo), None)
    propuestas = _numero(horas_propuestas, "Las horas", 0, 10000) if horas_propuestas not in (None, "") else None
    k = t["kpis"]["costo_medio_hora"]
    salida = {"tipo": tipo, "etiqueta": TIPOS_PROYECTO[tipo], "base": fila["entregados"] if fila else 0,
              "costo_medio_hora": k, "margen_objetivo": aj["margen_objetivo"], "tipo_cambio": aj["tipo_cambio"],
              "horas_sugeridas": None, "horas_corregidas": None, "precio_uyu": None, "precio_usd": None,
              "desvio_pct": fila["desvio_pct"] if fila else None,
              "tarifa_mediana": fila["tarifa_mediana"] if fila else None}
    if fila and fila["horas_mediana"]:
        salida["horas_sugeridas"] = fila["horas_mediana"]
    if propuestas:
        # Lo que la historia dice que termina llevando lo que vos estimás.
        desvio = (fila["desvio_pct"] or 0) / 100 if fila else 0
        salida["horas_corregidas"] = _redondo(propuestas * (1 + max(desvio, 0)))
    horas = salida["horas_corregidas"] or salida["horas_sugeridas"]
    if horas and k:
        margen = min(float(aj["margen_objetivo"]), 90) / 100
        uyu = horas * k / (1 - margen)
        salida["precio_uyu"] = _redondear_precio(uyu, "UYU")
        salida["precio_usd"] = _redondear_precio(uyu / aj["tipo_cambio"], "USD")
    return salida


def horas_csv(db: str, desde: str | None, hasta: str | None) -> str:
    """Las cargas del período en CSV, para el contador o para facturar aparte."""
    d = _fecha(desde, "desde") or "0000-01-01"
    h = _fecha(hasta, "hasta") or "9999-12-31"
    c = _conn(db)
    try:
        filas = c.execute("""
            SELECT h.fecha, p.nombre AS persona, x.nombre AS proyecto, x.cliente, h.fase, h.minutos, h.nota, h.origen
            FROM rt_horas h JOIN equipo_personas p ON p.id = h.persona_id JOIN rt_proyectos x ON x.id = h.proyecto_id
            WHERE h.fecha BETWEEN ? AND ? ORDER BY h.fecha, h.id""", (d, h)).fetchall()
    finally:
        c.close()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["fecha", "persona", "proyecto", "cliente", "fase", "horas", "nota", "origen"])
    for f in filas:
        w.writerow([f["fecha"], _csv_seguro(f["persona"]), _csv_seguro(f["proyecto"]), _csv_seguro(f["cliente"]),
                    FASES.get(f["fase"], f["fase"]), f"{f['minutos'] / 60:.2f}", _csv_seguro(f["nota"]),
                    f["origen"]])
    return buf.getvalue()


def _csv_seguro(valor) -> str:
    """Un texto que empieza con = + - @ se abre como fórmula en Excel: se neutraliza."""
    texto = "" if valor is None else str(valor)
    return "'" + texto if texto[:1] in ("=", "+", "-", "@", "\t", "\r") else texto
