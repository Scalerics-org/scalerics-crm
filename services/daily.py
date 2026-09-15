"""Daily Programador: el día de cada programador.

Dos cosas por persona del equipo (las marcadas `programador` en
`equipo_personas`):

- actividades de un día, con check de hecha;
- recordatorios que se repiten (todos los días, días hábiles o días elegidos
  de la semana), que aparecen solos en los días que les tocan y se marcan por
  día.

No es Tareas. Tareas es el tablero del equipo: tarjetas con cliente,
responsable, prioridad, fecha límite, estado y avance, que se sincronizan con
Notion y avisan por mail al asignar. Esto es la lista personal del día, sin
estados ni responsables, para no olvidarse de lo que hay que hacer hoy.

Fechas en hora de Montevideo: Uruguay está en UTC-3 fijo (sin horario de
verano desde 2015) y el servidor corre en UTC, así que "hoy" nunca es
`date.today()`.
"""

from datetime import date, datetime, timedelta, timezone

from database import (listar_actividades_daily, listar_marcas_daily,
                      listar_recordatorios_daily)
from services.equipo import parse_fecha

MONTEVIDEO = timezone(timedelta(hours=-3))
LARGO_TEXTO = 200
FRECUENCIAS = ("diario", "habiles", "dias")
DIAS_CORTOS = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")


def hoy_montevideo(ahora: datetime | None = None) -> date:
    """El día en Montevideo. Un datetime sin zona se toma como UTC, que es la
    hora del servidor."""
    ahora = ahora or datetime.now(timezone.utc)
    if ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=timezone.utc)
    return ahora.astimezone(MONTEVIDEO).date()


def primer_nombre(nombre: str) -> str:
    partes = (nombre or "").split()
    return partes[0] if partes else ""


def validar_texto(valor) -> tuple[str | None, str | None]:
    if not isinstance(valor, str) or not valor.strip():
        return None, "el texto no puede estar vacío"
    texto = " ".join(valor.split())
    if len(texto) > LARGO_TEXTO:
        return None, f"el texto no puede pasar de {LARGO_TEXTO} caracteres"
    return texto, None


def dias_de_texto(texto) -> list[int]:
    """'0,2,4' -> [0, 2, 4]. Lo que no es un día válido se ignora."""
    dias = set()
    for parte in str(texto or "").split(","):
        parte = parte.strip()
        if parte.isdigit() and int(parte) <= 6:
            dias.add(int(parte))
    return sorted(dias)


def validar_recordatorio(data: dict) -> tuple[dict | None, str | None]:
    """{texto, frecuencia, dias (lista, 0 = lunes), activo} -> campos para la base."""
    texto, error = validar_texto(data.get("texto"))
    if error:
        return None, error
    frecuencia = data.get("frecuencia", "diario")
    if frecuencia not in FRECUENCIAS:
        return None, "frecuencia tiene que ser diario, habiles o dias"
    dias: list[int] = []
    if frecuencia == "dias":
        crudos = data.get("dias")
        if not isinstance(crudos, list) or not all(
                isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in crudos):
            return None, "dias tiene que ser una lista de números del 0 (lunes) al 6 (domingo)"
        dias = sorted(set(crudos))
        if not dias:
            return None, "elegí al menos un día de la semana"
    activo = data.get("activo", True)
    if not isinstance(activo, bool):
        return None, "activo tiene que ser true o false"
    return {"texto": texto, "frecuencia": frecuencia,
            "dias": ",".join(str(d) for d in dias), "activo": 1 if activo else 0}, None


def le_toca(recordatorio: dict, dia: date) -> bool:
    """Si el recordatorio aparece ese día: activo, ya creado, y la frecuencia."""
    if not recordatorio["activo"]:
        return False
    desde = parse_fecha(recordatorio["desde"])
    if desde and dia < desde:
        return False
    if recordatorio["frecuencia"] == "habiles":
        return dia.weekday() < 5
    if recordatorio["frecuencia"] == "dias":
        return dia.weekday() in dias_de_texto(recordatorio["dias"])
    return True


def cuando(recordatorio: dict) -> str:
    if recordatorio["frecuencia"] == "habiles":
        return "Días hábiles (lunes a viernes)"
    if recordatorio["frecuencia"] == "dias":
        return ", ".join(DIAS_CORTOS[d] for d in dias_de_texto(recordatorio["dias"]))
    return "Todos los días"


def _actividad(a: dict) -> dict:
    return {"id": a["id"], "texto": a["texto"], "hecha": bool(a["hecha"]),
            "fecha": a["fecha"], "pasada_de": a["pasada_de"],
            "creada_por": a["created_by_name"]}


def _recordatorio(r: dict) -> dict:
    return {"id": r["id"], "texto": r["texto"], "frecuencia": r["frecuencia"],
            "dias": dias_de_texto(r["dias"]), "activo": bool(r["activo"]),
            "desde": r["desde"], "cuando": cuando(r)}


def dia(db_path: str, persona: dict, fecha: date, hoy: date) -> dict:
    """Todo lo que muestra la pantalla para una persona y un día."""
    ayer = fecha - timedelta(days=1)
    marcados = listar_marcas_daily(db_path, persona["id"], fecha.isoformat())
    todos = listar_recordatorios_daily(db_path, persona["id"])
    # Aparece si le toca ese día, o si ese día ya estaba marcado: pausar un
    # recordatorio no borra lo que se hizo.
    del_dia = [dict(_recordatorio(r), hecha=r["id"] in marcados)
               for r in todos if le_toca(r, fecha) or r["id"] in marcados]
    pendientes = [_actividad(a) for a in
                  listar_actividades_daily(db_path, persona["id"], ayer.isoformat())
                  if not a["hecha"]]
    return {
        "persona": {"id": persona["id"], "nombre": persona["nombre"],
                    "primer_nombre": primer_nombre(persona["nombre"])},
        "fecha": fecha.isoformat(),
        "hoy": hoy.isoformat(),
        "ayer": ayer.isoformat(),
        "es_hoy": fecha == hoy,
        "actividades": [_actividad(a) for a in
                        listar_actividades_daily(db_path, persona["id"], fecha.isoformat())],
        "recordatorios": del_dia,
        "pendientes_ayer": pendientes,
        "recordatorios_todos": [_recordatorio(r) for r in todos],
    }
