"""Seguimiento de leads: la agenda de llamados (pedido de Juan, 14/9).

Lógica pura, sin Flask ni base: qué día es hoy en Montevideo, a qué grupo va
cada recordatorio, cuánto se corre al posponer, qué se valida al crear uno y al
marcarlo hecho, y cómo se arma el número para `tel:` y `wa.me`.

El servidor corre en UTC. De las 21 a las 24 de Montevideo en UTC ya es el día
siguiente: si "hoy" saliera de `date.today()`, un recordatorio de hoy pasaría a
Vencidos tres horas antes de tiempo. Por eso todo sale de `hoy_mvd()`.
"""

import calendar
from datetime import date, datetime, timedelta

import pytz

MVD = pytz.timezone("America/Montevideo")

# En este orden se muestran. Las claves son las de la API.
GRUPOS = ("vencidos", "hoy", "semana", "despues")

_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_DIAS_CORTOS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")

MAX_MOTIVO = 200
MAX_NOTA = 300
MAX_RESULTADO = 2000


# ── fechas ───────────────────────────────────────────────────────────────────

def ahora_mvd(ahora_utc: datetime | None = None) -> datetime:
    """La hora de Montevideo. `ahora_utc` es para los tests."""
    base = ahora_utc or datetime.now(pytz.utc)
    if base.tzinfo is None:
        base = pytz.utc.localize(base)
    return base.astimezone(MVD)


def hoy_mvd(ahora_utc: datetime | None = None) -> date:
    return ahora_mvd(ahora_utc).date()


def parse_fecha(valor) -> date | None:
    """AAAA-MM-DD estricto, o None."""
    if not isinstance(valor, str):
        return None
    v = valor.strip()
    if len(v) != 10 or v[4] != "-" or v[7] != "-":
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def parse_hora(valor) -> tuple[str | None, bool]:
    """(hora 'HH:MM' o None, es_valida). Vacía o ausente es válida: la hora es
    opcional."""
    if valor is None:
        return None, True
    if not isinstance(valor, str):
        return None, False
    v = valor.strip()
    if not v:
        return None, True
    partes = v.split(":")
    if len(partes) not in (2, 3) or not all(p.isascii() and p.isdigit() for p in partes):
        return None, False
    if len(partes[0]) not in (1, 2) or not all(len(p) == 2 for p in partes[1:]):
        return None, False
    h, m = int(partes[0]), int(partes[1])
    if h > 23 or m > 59:
        return None, False
    return f"{h:02d}:{m:02d}", True


def fecha_corta(d: date, hoy: date | None = None) -> str:
    """'jue 17/09'; con el año si no es el de hoy."""
    texto = f"{_DIAS_CORTOS[d.weekday()]} {d.day:02d}/{d.month:02d}"
    if hoy is not None and d.year != hoy.year:
        texto += f"/{d.year}"
    return texto


def texto_de_hoy(hoy: date) -> str:
    return f"{_DIAS[hoy.weekday()]} {hoy.day:02d}/{hoy.month:02d}"


def grupo_de(fecha: date, hoy: date) -> str:
    """Vencidos, hoy, esta semana (hasta el domingo) o más adelante.

    Un recordatorio viejo pasa solo a Vencidos: no se borra ni se archiva.
    """
    if fecha < hoy:
        return "vencidos"
    if fecha == hoy:
        return "hoy"
    if fecha <= hoy + timedelta(days=6 - hoy.weekday()):
        return "semana"
    return "despues"


def sumar_mes(d: date) -> date:
    """El mismo día del mes siguiente; si no existe, el último (31/1 -> 28/2)."""
    anio, mes = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return date(anio, mes, min(d.day, calendar.monthrange(anio, mes)[1]))


def posponer(fecha: date, cuanto, hoy: date) -> date | None:
    """+1 semana o +1 mes, contado desde la fecha del recordatorio o desde hoy,
    la que sea más tarde. Contar desde un vencido de hace dos semanas dejaría el
    llamado vencido igual, y el botón no serviría para nada."""
    base = max(fecha, hoy)
    if cuanto == "semana":
        return base + timedelta(days=7)
    if cuanto == "mes":
        return sumar_mes(base)
    return None


# ── teléfono ─────────────────────────────────────────────────────────────────

def telefonos(phone) -> tuple[str | None, str | None]:
    """(para `tel:`, para `wa.me`), o (None, None) si no hay número usable.

    Los números del CRM vienen como los cargó cada fuente: "099 123 456",
    "+598 99 123 456", "59899123456". wa.me pide el número internacional sin
    signos. Un celular uruguayo sin prefijo (099... o 99...) se completa con 598.
    """
    if not isinstance(phone, str):
        return None, None
    digitos = "".join(c for c in phone if c.isascii() and c.isdigit())
    if digitos.startswith("00"):
        digitos = digitos[2:]
    if len(digitos) < 7:
        return None, None
    if digitos.startswith("598"):
        internacional = digitos
    elif digitos.startswith("0") and len(digitos) == 9:
        internacional = "598" + digitos[1:]
    elif len(digitos) == 8:
        internacional = "598" + digitos
    else:
        internacional = digitos
    return "+" + internacional, internacional


# ── validación ───────────────────────────────────────────────────────────────

def _una_linea(valor: str) -> str:
    return " ".join(valor.split())


def _nota(valor) -> tuple[str | None, str | None]:
    if valor is None:
        return None, None
    if not isinstance(valor, str):
        return None, "la nota tiene que ser texto"
    nota = _una_linea(valor) or None
    if nota and len(nota) > MAX_NOTA:
        return None, f"la nota es un dato corto (hasta {MAX_NOTA} letras)"
    return nota, None


def _motivo(valor) -> tuple[str | None, str | None]:
    if not isinstance(valor, str) or not valor.strip():
        return None, "falta el motivo: qué tenés que hacer o preguntar"
    motivo = _una_linea(valor)
    if len(motivo) > MAX_MOTIVO:
        return None, f"el motivo es de una línea (hasta {MAX_MOTIVO} letras)"
    return motivo, None


def validar_recordatorio(datos) -> tuple[dict | None, str | None]:
    """(campos, None) o (None, error) para crear un recordatorio.

    Se acepta una fecha pasada a propósito: cargar "tenía que llamarlo ayer" es
    un caso real, y ese recordatorio va directo a Vencidos.
    """
    if not isinstance(datos, dict):
        return None, "faltan los datos del recordatorio"
    lead_id = datos.get("lead_id")
    if isinstance(lead_id, bool) or not isinstance(lead_id, int) or lead_id <= 0:
        return None, "lead_id tiene que ser el id de un lead del CRM"
    fecha = parse_fecha(datos.get("fecha"))
    if fecha is None:
        return None, "la fecha tiene que ser AAAA-MM-DD"
    hora, ok = parse_hora(datos.get("hora"))
    if not ok:
        return None, "la hora tiene que ser HH:MM"
    motivo, error = _motivo(datos.get("motivo"))
    if error:
        return None, error
    nota, error = _nota(datos.get("nota"))
    if error:
        return None, error
    return {"lead_id": lead_id, "fecha": fecha.isoformat(), "hora": hora,
            "motivo": motivo, "nota": nota}, None


def validar_hecho(datos, recordatorio: dict, hoy: date) -> tuple[dict | None, str | None]:
    """(campos, None) o (None, error) para marcar hecho un recordatorio.

    La regla que hace que la pantalla sirva: nunca se cierra un llamado sin
    decir qué pasó y sin elegir UNA de dos cosas, la próxima fecha o que no hace
    falta volver a llamar. Las dos juntas tampoco: sería ambiguo.

    `campos["proximo"]` es None si no hay que volver a llamar. Si no se manda
    motivo, el próximo hereda el del recordatorio que se cierra; si no se manda
    la clave `proxima_nota`, hereda la nota ("llamar después de las 18" sigue
    valiendo).
    """
    if not isinstance(datos, dict):
        return None, "faltan los datos del llamado"
    resultado = datos.get("resultado")
    if not isinstance(resultado, str) or not resultado.strip():
        return None, "contá qué pasó en la llamada"
    resultado = resultado.strip()
    if len(resultado) > MAX_RESULTADO:
        return None, f"el resultado es muy largo (hasta {MAX_RESULTADO} letras)"

    sin_volver = datos.get("sin_volver", False)
    if not isinstance(sin_volver, bool):
        return None, "sin_volver tiene que ser true o false"
    crudo = datos.get("proxima_fecha")
    tiene_fecha = not (crudo is None or (isinstance(crudo, str) and not crudo.strip()))
    if sin_volver and tiene_fecha:
        return None, ("elegí una sola cosa: la próxima fecha o que no hace falta "
                      "volver a llamar")
    if not sin_volver and not tiene_fecha:
        return None, ("elegí cuándo volvés a llamar, o marcá que no hace falta "
                      "volver a llamar")

    campos = {"resultado": resultado, "proximo": None}
    if sin_volver:
        return campos, None

    fecha = parse_fecha(crudo)
    if fecha is None:
        return None, "la próxima fecha tiene que ser AAAA-MM-DD"
    if fecha < hoy:
        return None, "la próxima fecha no puede ser anterior a hoy"
    hora, ok = parse_hora(datos.get("proxima_hora"))
    if not ok:
        return None, "la hora tiene que ser HH:MM"
    motivo_crudo = datos.get("proximo_motivo")
    if motivo_crudo is None or (isinstance(motivo_crudo, str) and not motivo_crudo.strip()):
        motivo_crudo = recordatorio.get("motivo")
    motivo, error = _motivo(motivo_crudo)
    if error:
        return None, error
    if "proxima_nota" in datos:
        nota, error = _nota(datos.get("proxima_nota"))
        if error:
            return None, error
    else:
        nota = recordatorio.get("nota") or None
    campos["proximo"] = {"fecha": fecha.isoformat(), "hora": hora,
                         "motivo": motivo, "nota": nota}
    return campos, None


# ── lo que ve la pantalla ────────────────────────────────────────────────────

def _empresa(fila: dict, nombre: str) -> str:
    """Empresa o rubro, lo primero que haya y no repita el nombre."""
    for candidato in (fila.get("empresa"), fila.get("ci_rubro"), fila.get("rubro")):
        c = (candidato or "").strip()
        if c and c.casefold() != nombre.casefold():
            return c
    return ""


def armar_item(fila: dict, hoy: date) -> dict:
    """Un recordatorio pendiente con todo lo que la tarjeta muestra ya resuelto."""
    fecha = parse_fecha(fila.get("fecha")) or hoy
    hora = fila.get("hora") or None
    grupo = grupo_de(fecha, hoy)
    dias = (hoy - fecha).days
    if grupo == "vencidos":
        vence = "ayer" if dias == 1 else f"hace {dias} días"
    elif grupo == "hoy":
        vence = hora or "sin hora"
    else:
        vence = fecha_corta(fecha, hoy) + (f" · {hora}" if hora else "")
    tel, wa = telefonos(fila.get("telefono"))
    nombre = (fila.get("nombre") or "").strip()
    return {
        "id": fila["id"],
        "lead_id": fila["lead_id"],
        "nombre": nombre,
        "empresa": _empresa(fila, nombre),
        "telefono": fila.get("telefono") or "",
        "tel": tel,
        "wa": wa,
        "fecha": fecha.isoformat(),
        "fecha_texto": fecha_corta(fecha, hoy),
        "hora": hora,
        "motivo": fila.get("motivo") or "",
        "nota": fila.get("nota") or "",
        "grupo": grupo,
        "vencido": grupo == "vencidos",
        "dias_vencido": max(dias, 0),
        "vence_texto": vence,
        "ultimo_resultado": fila.get("ultimo_resultado") or "",
    }


def armar_pantalla(filas: list[dict], hoy: date) -> dict:
    grupos = {g: [] for g in GRUPOS}
    for fila in filas:
        item = armar_item(fila, hoy)
        grupos[item["grupo"]].append(item)
    for items in grupos.values():
        # Primero los que tienen hora, en orden; los "sin hora" al final del día.
        items.sort(key=lambda i: (i["fecha"], i["hora"] is None, i["hora"] or "", i["id"]))
    return {
        "hoy": hoy.isoformat(),
        "hoy_texto": texto_de_hoy(hoy),
        "pendientes": sum(len(v) for v in grupos.values()),
        "vencidos": len(grupos["vencidos"]),
        "contadores": {g: len(v) for g, v in grupos.items()},
        "grupos": grupos,
    }


def armar_llamado(fila: dict) -> dict:
    crudo = fila.get("fecha") or ""
    d = parse_fecha(crudo[:10])
    hora = crudo[11:16] if len(crudo) >= 16 else ""
    texto = (f"{_DIAS_CORTOS[d.weekday()]} {d.day:02d}/{d.month:02d}/{d.year}" if d else crudo)
    if hora:
        texto += f" · {hora}"
    return {
        "id": fila["id"],
        "fecha": crudo,
        "fecha_texto": texto,
        "resultado": fila.get("resultado") or "",
        "motivo": fila.get("motivo") or "",
        "recordatorio_id": fila.get("recordatorio_id"),
    }
