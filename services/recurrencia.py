"""Reuniones que se repiten, e invitados (pedido de Juan, 15/9).

"Agrega entonces tambien una opcion de reunion en el calendario que no sea solo
para clientes sino otro asunto que se pueda escribir, y la opcion de agregar
reuniones que se repitan".

Donde viven: en la base del CRM, no en Google Calendar. Crear una reunion desde
el CRM no crea evento en Google desde el 1/6/2026 (commit 81f19fb), asi que la
regla de repeticion se guarda en la fila y las ocurrencias se calculan al pedir
el rango que se mira (mes o semana). Nada de esto manda mails.

Todo es hora de pared de Montevideo, sin zona: '2026-09-18' + '19:00' se
expande sumando dias a la FECHA, nunca pasando por UTC. El servidor corre en
UTC y no importa: ninguna cuenta de aca mira el reloj ni convierte husos, asi
que un viernes 19:00 sigue siendo viernes 19:00 en todas las ocurrencias.

La regla (JSON en la columna `repeticion`):

    {"freq": "semanal", "dias": [4], "fin": "nunca"}
    {"freq": "diaria", "fin": "veces", "veces": 10}
    {"freq": "quincenal", "dias": [0, 2], "fin": "fecha", "hasta": "2026-12-31"}
    {"freq": "mensual", "fin": "nunca"}          # mismo dia del mes

`dias`: 0 = lunes ... 6 = domingo, solo en semanal y quincenal. Mensual en un
dia 31 se saltea los meses que no lo tienen (como una RRULE BYMONTHDAY).

Las excepciones (JSON en `excepciones`) van por la fecha ORIGINAL de la
ocurrencia: `null` = borrada ("solo esta"), y un objeto = movida o renombrada
("solo esta"). "Esta y las siguientes" corta la serie el dia anterior y abre
una serie nueva desde ahi. Como en una RRULE con COUNT, "despues de N veces"
cuenta las ocurrencias generadas: borrar una no agrega otra al final.
"""

from __future__ import annotations

import datetime as dt
import json
import re

FRECUENCIAS = ("diaria", "semanal", "quincenal", "mensual")
FINES = ("nunca", "fecha", "veces")
ALCANCES = ("esta", "siguientes", "todas")
MAX_VECES = 500
MAX_INVITADOS = 50

_MAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>.]{2,}$")


# ── invitados ────────────────────────────────────────────────────────────────

def validar_invitados(valor) -> tuple[list[str], str | None]:
    """Lista de mails limpia, o un error que dice cuales no son validos.

    Acepta una lista o un texto separado por comas, punto y coma o espacios.
    Repetidos (sin importar mayusculas) quedan una sola vez.
    """
    if valor in (None, ""):
        return [], None
    if isinstance(valor, str):
        crudos = re.split(r"[,;\s]+", valor)
    elif isinstance(valor, (list, tuple)):
        crudos = []
        for v in valor:
            crudos.extend(re.split(r"[,;\s]+", str(v or "")))
    else:
        return [], "Los invitados tienen que ser una lista de mails"

    limpios, vistos, malos = [], set(), []
    for c in crudos:
        mail = c.strip()
        if not mail:
            continue
        if not _MAIL.match(mail):
            malos.append(mail)
            continue
        if mail.lower() in vistos:
            continue
        vistos.add(mail.lower())
        limpios.append(mail)
    if malos:
        return [], "Estos mails no son válidos: " + ", ".join(malos)
    if len(limpios) > MAX_INVITADOS:
        return [], f"Como mucho {MAX_INVITADOS} invitados"
    return limpios, None


# ── la regla ─────────────────────────────────────────────────────────────────

def _fecha(valor) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(valor or "")[:10])
    except ValueError:
        return None


def validar_regla(valor, inicio: dt.date) -> tuple[dict | None, str | None]:
    """La regla normalizada, None si no se repite, o un error para mostrar."""
    if not valor:
        return None, None
    if not isinstance(valor, dict):
        return None, "La repetición no es válida"
    freq = valor.get("freq") or "no"
    if freq == "no":
        return None, None
    if freq not in FRECUENCIAS:
        return None, "Elegí cada cuánto se repite"

    regla: dict = {"freq": freq}
    if freq in ("semanal", "quincenal"):
        dias = valor.get("dias")
        if dias in (None, []):
            dias = [inicio.weekday()]
        try:
            dias = sorted({int(d) for d in dias})
        except (TypeError, ValueError):
            return None, "Los días de la semana no son válidos"
        if not dias or any(d < 0 or d > 6 for d in dias):
            return None, "Los días de la semana no son válidos"
        regla["dias"] = dias

    fin = valor.get("fin") or "nunca"
    if fin not in FINES:
        return None, "Elegí cuándo termina la repetición"
    regla["fin"] = fin
    if fin == "fecha":
        hasta = _fecha(valor.get("hasta"))
        if not hasta:
            return None, "Poné hasta qué fecha se repite"
        if hasta < inicio:
            return None, "La fecha de fin no puede ser antes de la primera reunión"
        regla["hasta"] = hasta.isoformat()
    elif fin == "veces":
        try:
            veces = int(valor.get("veces"))
        except (TypeError, ValueError):
            return None, "Poné cuántas veces se repite"
        if veces < 1 or veces > MAX_VECES:
            return None, f"Las veces tienen que ir de 1 a {MAX_VECES}"
        regla["veces"] = veces
    return regla, None


def _cargar(texto, defecto):
    if not texto:
        return defecto
    if isinstance(texto, (dict, list)):
        return texto
    try:
        return json.loads(texto)
    except (TypeError, ValueError):
        return defecto


def regla_de(fila: dict) -> dict | None:
    regla = _cargar(fila.get("repeticion"), None)
    return regla if isinstance(regla, dict) and regla.get("freq") in FRECUENCIAS else None


def excepciones_de(fila: dict) -> dict:
    exc = _cargar(fila.get("excepciones"), {})
    return exc if isinstance(exc, dict) else {}


# ── expandir ─────────────────────────────────────────────────────────────────

def _originales(regla: dict, inicio: dt.date, tope: dt.date):
    """Las fechas que genera la regla, en orden, desde `inicio` hasta `tope`.

    Respeta el fin de la regla. Con "veces" cuenta desde la primera, que es lo
    que hace falta para que la cantidad sea la misma mire el mes que mire.
    """
    if regla.get("fin") == "fecha":
        hasta = _fecha(regla.get("hasta"))
        if hasta and hasta < tope:
            tope = hasta
    veces = regla.get("veces") if regla.get("fin") == "veces" else None
    n = 0
    freq = regla["freq"]

    if freq == "diaria":
        d = inicio
        while d <= tope:
            n += 1
            if veces and n > veces:
                return
            yield d
            d += dt.timedelta(days=1)
        return

    if freq in ("semanal", "quincenal"):
        paso = 7 if freq == "semanal" else 14
        lunes = inicio - dt.timedelta(days=inicio.weekday())
        dias = regla.get("dias") or [inicio.weekday()]
        while lunes <= tope:
            for dia in dias:
                d = lunes + dt.timedelta(days=dia)
                if d < inicio:
                    continue
                if d > tope:
                    return
                n += 1
                if veces and n > veces:
                    return
                yield d
            lunes += dt.timedelta(days=paso)
        return

    # mensual: el mismo numero de dia
    k = 0
    while True:
        anio, mes = divmod(inicio.month - 1 + k, 12)
        anio += inicio.year
        mes += 1
        if dt.date(anio, mes, 1) > tope:
            return
        k += 1
        try:
            d = dt.date(anio, mes, inicio.day)
        except ValueError:
            continue          # un 31 en un mes de 30: ese mes no hay
        if d > tope:
            return
        n += 1
        if veces and n > veces:
            return
        yield d


def _inicio(fila: dict) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(fila.get("start_at") or "").replace(" ", "T")[:19])
    except ValueError:
        return None


def duracion_min(fila: dict) -> int:
    ini = _inicio(fila)
    try:
        fin = dt.datetime.fromisoformat(str(fila.get("end_at") or "").replace(" ", "T")[:19])
    except ValueError:
        fin = None
    if not ini or not fin or fin <= ini:
        return 60
    return int((fin - ini).total_seconds() // 60)


def es_ocurrencia(fila: dict, fecha: str) -> bool:
    regla, ini, d = regla_de(fila), _inicio(fila), _fecha(fecha)
    if not regla or not ini or not d:
        return False
    return d in set(_originales(regla, ini.date(), d))


def ocurrencias(fila: dict, desde: str, hasta: str) -> list[dict]:
    """Lo que hay que dibujar de la serie entre `desde` y `hasta` (inclusive).

    Cada una: ocurrencia (la fecha original), date, time, title, duration_min
    y `movida` si tiene una excepcion que la cambio.
    """
    regla, ini = regla_de(fila), _inicio(fila)
    d_desde, d_hasta = _fecha(desde), _fecha(hasta)
    if not regla or not ini or not d_desde or not d_hasta:
        return []
    exc = excepciones_de(fila)
    claves = [k for k in (_fecha(x) for x in exc) if k]
    tope = max([d_hasta] + claves)
    originales = list(_originales(regla, ini.date(), tope))
    validas = set(originales)
    hora = ini.strftime("%H:%M")
    titulo = fila.get("title") or ""
    minutos = duracion_min(fila)

    salida = []
    for d in originales:
        iso = d.isoformat()
        if iso in exc:
            continue
        if d_desde <= d <= d_hasta:
            salida.append({"ocurrencia": iso, "date": iso, "time": hora,
                           "title": titulo, "duration_min": minutos, "movida": False})
    for clave, cambio in exc.items():
        if not isinstance(cambio, dict) or _fecha(clave) not in validas:
            continue
        fecha = str(cambio.get("date") or clave)[:10]
        if not (desde <= fecha <= hasta):
            continue
        salida.append({"ocurrencia": clave, "date": fecha,
                       "time": cambio.get("time") or hora,
                       "title": cambio.get("title") or titulo,
                       "duration_min": int(cambio.get("duration_min") or minutos),
                       "movida": True})
    salida.sort(key=lambda o: (o["date"], o["time"]))
    return salida


def _sin_ocurrencias(regla: dict, inicio: dt.date, exc: dict) -> bool:
    """Una serie con fin cuyas ocurrencias estan todas borradas."""
    if regla.get("fin") == "nunca":
        return False
    for d in _originales(regla, inicio, dt.date(9999, 12, 31)):
        if d.isoformat() not in exc or isinstance(exc[d.isoformat()], dict):
            return False
    return True


# ── editar y borrar ──────────────────────────────────────────────────────────

def _corrida(regla: dict, delta: int) -> dict:
    """La regla con la serie corrida `delta` dias: los dias de la semana y la
    fecha de fin se mueven con ella, asi la cantidad de reuniones no cambia."""
    nueva = dict(regla)
    if delta and nueva.get("dias") is not None:
        nueva["dias"] = sorted({(d + delta) % 7 for d in nueva["dias"]})
    if delta and nueva.get("fin") == "fecha" and nueva.get("hasta"):
        nueva["hasta"] = (_fecha(nueva["hasta"]) + dt.timedelta(days=delta)).isoformat()
    return nueva


def _cortada(regla: dict, ocurrencia: dt.date, cuantas_antes: int) -> dict:
    """La regla que termina justo antes de `ocurrencia`."""
    nueva = dict(regla)
    if nueva.get("fin") == "veces":
        nueva["veces"] = cuantas_antes
    else:
        nueva["fin"] = "fecha"
        nueva["hasta"] = (ocurrencia - dt.timedelta(days=1)).isoformat()
        nueva.pop("veces", None)
    return nueva


def _campos(inicio: dt.datetime, minutos: int) -> dict:
    return {"start_at": inicio.isoformat(),
            "end_at": (inicio + dt.timedelta(minutes=minutos)).isoformat()}


def editar(fila: dict, ocurrencia: str, alcance: str, cambios: dict) -> dict:
    """Plan para editar una ocurrencia de la serie.

    `cambios`: date, time ('HH:MM'), title, duration_min, ya validados.
    Devuelve {"actualizar": campos de la fila, "nueva": campos de una serie
    nueva o None}. Levanta ValueError si la fecha no es de la serie.
    """
    regla, ini = regla_de(fila), _inicio(fila)
    oc = _fecha(ocurrencia)
    if alcance not in ALCANCES:
        raise ValueError("Elegí si el cambio es para esta, las siguientes o todas")
    if not regla or not ini or not oc or not es_ocurrencia(fila, ocurrencia):
        raise ValueError("Esa fecha no es una de las reuniones de la serie")

    exc = excepciones_de(fila)
    nueva_fecha = _fecha(cambios["date"])
    hora = dt.time.fromisoformat(cambios["time"])
    titulo = cambios.get("title") or fila.get("title") or ""
    minutos = int(cambios.get("duration_min") or duracion_min(fila))
    delta = (nueva_fecha - oc).days

    if alcance == "esta":
        exc[oc.isoformat()] = {"date": nueva_fecha.isoformat(), "time": cambios["time"],
                               "title": titulo, "duration_min": minutos}
        return {"actualizar": {"excepciones": json.dumps(exc)}, "nueva": None}

    antes = [d for d in _originales(regla, ini.date(), oc - dt.timedelta(days=1))]
    if alcance == "todas" or not antes:
        inicio = dt.datetime.combine(ini.date() + dt.timedelta(days=delta), hora)
        exc_corridas = {(_fecha(k) + dt.timedelta(days=delta)).isoformat(): v
                        for k, v in exc.items() if _fecha(k)}
        actualizar = _campos(inicio, minutos)
        actualizar.update(title=titulo, repeticion=json.dumps(_corrida(regla, delta)),
                          excepciones=json.dumps(exc_corridas) if exc_corridas else None)
        return {"actualizar": actualizar, "nueva": None}

    # esta y las siguientes: la serie vieja termina el dia anterior...
    vieja = {k: v for k, v in exc.items() if _fecha(k) and _fecha(k) < oc}
    actualizar = {"repeticion": json.dumps(_cortada(regla, oc, len(antes))),
                  "excepciones": json.dumps(vieja) if vieja else None}
    # ...y desde aca sigue una nueva con los cambios.
    regla_nueva = _corrida(regla, delta)
    if regla_nueva.get("fin") == "veces":
        regla_nueva["veces"] = regla["veces"] - len(antes)
    siguientes = {(_fecha(k) + dt.timedelta(days=delta)).isoformat(): v
                  for k, v in exc.items() if _fecha(k) and _fecha(k) >= oc}
    nueva = _campos(dt.datetime.combine(nueva_fecha, hora), minutos)
    nueva.update(title=titulo, repeticion=json.dumps(regla_nueva),
                 excepciones=json.dumps(siguientes) if siguientes else None)
    return {"actualizar": actualizar, "nueva": nueva}


def borrar(fila: dict, ocurrencia: str, alcance: str) -> dict:
    """Plan para borrar: {"actualizar": campos o None, "borrar": bool}."""
    regla, ini = regla_de(fila), _inicio(fila)
    oc = _fecha(ocurrencia)
    if alcance not in ALCANCES:
        raise ValueError("Elegí si se borra esta, las siguientes o todas")
    if alcance == "todas":
        return {"actualizar": None, "borrar": True}
    if not regla or not ini or not oc or not es_ocurrencia(fila, ocurrencia):
        raise ValueError("Esa fecha no es una de las reuniones de la serie")

    exc = excepciones_de(fila)
    if alcance == "esta":
        exc[oc.isoformat()] = None
        if _sin_ocurrencias(regla, ini.date(), exc):
            return {"actualizar": None, "borrar": True}
        return {"actualizar": {"excepciones": json.dumps(exc)}, "borrar": False}

    antes = list(_originales(regla, ini.date(), oc - dt.timedelta(days=1)))
    if not antes:
        return {"actualizar": None, "borrar": True}
    vieja = {k: v for k, v in exc.items() if _fecha(k) and _fecha(k) < oc}
    regla_cortada = _cortada(regla, oc, len(antes))
    if _sin_ocurrencias(regla_cortada, ini.date(), vieja):
        return {"actualizar": None, "borrar": True}
    return {"actualizar": {"repeticion": json.dumps(regla_cortada),
                           "excepciones": json.dumps(vieja) if vieja else None},
            "borrar": False}
