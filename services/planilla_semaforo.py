"""Los estados reales de los leads de Meta viven en una planilla de Google.

Los CEO llaman a los leads y marcan el resultado pintando la fila de un color;
la leyenda esta en la pestana `Semaforo` de `Scalerics - Leads - 2026`. El CRM
nunca se entero de eso: el 27-8-2026 se importaron 195 estados a mano y hasta
ese dia el CRM decia "Sin contactar" sobre 216 leads que si habian sido
contactados. Este modulo existe para que ese import no vuelva a ser manual.

**El color se lee del lado de Google, no de aca.** Un Apps Script pegado a la
planilla corre `getBackgrounds()` —que es la unica forma barata de leer el
color, porque ningun export de texto lo trae— y postea las filas a
`POST /api/meta/sync-planilla`. Asi no hace falta ninguna credencial nueva de
Google en el CRM: el script corre como el dueno de la planilla. El .gs esta
versionado en `scripts/planilla_semaforo.gs`.

Reglas que no se negocian, todas probadas:

- **Solo toca leads con `source='meta'`.** La planilla no puede mover un lead
  de discovery ni del padron sin web aunque coincida el telefono. Las tres
  cohortes tienen publicos y automatizaciones distintas y esa separacion es lo
  caro de romper.
- **Nunca retrocede, con una excepcion.** Si produccion ya dice `finalizado` y
  la planilla dice `cerrado`, gana produccion: la planilla la mantiene
  gente a mano y va atrasada respecto del trabajo real. La excepcion es
  `no_interesa`, que la pinta alguien que hablo con el lead y siempre gana —
  salvo sobre un cliente, donde hay plata y una celda mal pintada no puede
  borrarla.
- **Idempotente.** Aplicarla dos veces no escribe nada la segunda.
- **El blanco no es un estado.** Una celda sin pintar vuelve como `#ffffff`
  desde Apps Script; significa "nadie la marco", no "sin contactar".
"""

import json
import re
import sqlite3
import unicodedata
from datetime import date, datetime, timedelta, timezone

from database import ETAPAS_CLIENTE, add_lead_event, get_business, update_business

# La leyenda de la pestana `Semaforo`, verificada contra la planilla y
# confirmada con Juan el 27-8-2026. Los dos verdes oscuros son los dos tonos
# que aparecen en la planilla real: los dos significan venta.
COLOR_A_ESTADO = {
    "000000": "no_interesa",          # atendio y no le interesa
    "ff0000": "llamar_despues",       # no atiende, se dejo mensaje
    "f4cccc": "llamar_despues",       # el mismo rojo desteñido
    "ffff00": "interesado",           # atendio, sin reunion agendada
    "fff2cc": "interesado",           # el mismo amarillo desteñido
    "00ff00": "demo_agendada",        # atendio y se agendo demo
    "00ffff": "demo_1",               # demo realizada
    "ff00ff": "presupuesto_enviado",  # hubo demo y no cerro
    "274e13": "cerrado",              # venta concretada
    "38761d": "cerrado",              # venta concretada (el otro tono)
}

# Cuan avanzado esta cada estado. Decide dos cosas: cual gana cuando un mismo
# telefono aparece en varios meses, y si lo que dice la planilla es un avance
# respecto de lo que ya sabe produccion.
#
# Tiene que conocer TODAS las etapas, viejas y nuevas. Un estado que no esta
# entra como `RANK.get(actual, 0)` = 0, o sea al fondo, y entonces cualquier
# color de la planilla cuenta como avance: un lead en 'demo_1' se lo lleva
# puesto un amarillo y vuelve a 'interesado'. Eso paso de verdad cuando se
# renombraron las etapas y este mapa quedo con los nombres viejos.
RANK = {
    "sin_contactar": 0,
    "no_interesa": 1,
    "llamar_despues": 2,
    "contactado": 3,
    "interesado": 3,
    "demo_agendada": 4,
    "demo_1": 5,
    "demo_2": 5,
    "demo_3": 5,
    "presupuesto_enviado": 6,
    "follow_up_1": 7,
    "follow_up_2": 7,
    "en_espera": 7,
    "rechazo": 7,
    "acepto": 8,
    "cerrado": 8,
    "en_desarrollo": 9,
    "finalizado": 10,
    # Los nombres viejos siguen mapeados: quedan leads con estos estados en
    # cualquier base que no haya arrancado con la migracion todavia.
    "reunion_agendada": 4,
    "reunion_hecha": 5,
    "negociacion": 7,
    "cliente_cerrado": 8,
}

NOTA_EVENTO = "sync planilla semaforo"

# ── El semaforo como lo ve la gente ──────────────────────────────────────────
# Los siete colores de la leyenda, con el nombre que se muestra en el CRM (los
# cuatro de demo, iguales que en el Registro de demos) y el estado del CRM que
# significan. `hex` es el tono puro de la planilla: el mismo que los tokens
# --semaforo-* del panel. El orden es el del embudo, que es como se lee.
SEMAFORO = (
    # clave       etiqueta                 estado del CRM          hex
    ("rojo",      "No atiende",            "llamar_despues",       "ff0000"),
    ("amarillo",  "Interesado",            "interesado",           "ffff00"),
    ("verde",     "Demo agendada",         "demo_agendada",        "00ff00"),
    ("celeste",   "Demo realizada",        "demo_1",               "00ffff"),
    ("violeta",   "Hubo demo y no cerró",  "presupuesto_enviado",  "ff00ff"),
    ("venta",     "Venta concretada",      "cerrado",              "38761d"),
    ("negro",     "No le interesa",        "no_interesa",          "000000"),
)
COLORES = {clave: (etiqueta, estado) for clave, etiqueta, estado, _hex in SEMAFORO}
SIN_COLOR = "sin_color"

# Que color le toca a un lead segun su estado. El color NO se guarda aparte: sale
# de `crm_status`, que es donde lo escribe la planilla y donde lo leen el
# Proceso de venta, Clientes y las secuencias de mail. Asi no hay dos verdades.
#
# Los estados que la planilla no pinta caen en el color de su etapa: una demo 2
# sigue siendo "demo realizada", un follow up o un rechazo despues de la demo es
# "hubo demo y no cerro", y aceptar, estar en desarrollo o finalizado es venta.
# 'sin_contactar' no tiene color: nadie lo marco.
ESTADO_A_COLOR = {
    "no_interesa": "negro",
    "llamar_despues": "rojo",
    "interesado": "amarillo", "contactado": "amarillo",
    "demo_agendada": "verde", "reunion_agendada": "verde", "agendo": "verde",
    "demo_1": "celeste", "demo_2": "celeste", "demo_3": "celeste",
    "reunion_hecha": "celeste",
    "presupuesto_enviado": "violeta", "follow_up_1": "violeta",
    "follow_up_2": "violeta", "en_espera": "violeta", "rechazo": "violeta",
    "negociacion": "violeta",
    "acepto": "venta", "cerrado": "venta", "en_desarrollo": "venta",
    "finalizado": "venta", "cliente_cerrado": "venta", "firmo": "venta",
}

# Un cliente no se baja con un toque: ahi hay plata. Misma idea que la
# excepcion del negro en `aplicar`.
ESTADOS_DE_CLIENTE = set(ETAPAS_CLIENTE) | {"cliente_cerrado", "firmo"}

ORIGEN_CRM = "crm"


def color_de_estado(estado) -> str:
    """La clave del color del semaforo para ese estado, o "" si no tiene."""
    return ESTADO_A_COLOR.get(estado or "sin_contactar", "")


def normalizar_color(valor: str) -> str:
    """'#FFFF00', 'FFFFFF00' y 'ffff00' son el mismo amarillo.

    Apps Script devuelve '#rrggbb'; openpyxl, 'AARRGGBB'. Se descarta el alfa y
    se baja a minusculas para que las dos fuentes entren por la misma puerta.
    """
    v = re.sub(r"[^0-9a-fA-F]", "", str(valor or ""))
    if len(v) == 8:
        v = v[2:]
    return v.lower() if len(v) == 6 else ""


def estado_de_color(valor: str) -> str:
    """El estado que corresponde a ese color, o "" si el color no dice nada."""
    return COLOR_A_ESTADO.get(normalizar_color(valor), "")


def normalizar_telefono(valor: str) -> str:
    """Solo digitos, sin ceros a la izquierda.

    La planilla y el CRM los escriben distinto (con +, con espacios, con
    parentesis) y no hay forma de pedirle a quien la llena que sea consistente.

    **La parte decimal se corta antes de sacar los simbolos.** Google guarda un
    telefono sin + como numero, y sale como "59895720157.0"; sacar los no-digitos
    a lo bruto lo convierte en "598957201570", con un cero de mas al final, y ese
    lead deja de parear en silencio. Paso con dos filas reales.
    """
    t = str(valor or "").strip()
    if re.fullmatch(r"[\d\s+().-]*\.\d+", t):
        t = t.split(".")[0]
    return re.sub(r"\D", "", t).lstrip("0")


def _indice(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """Telefono -> lead y mail -> lead, SOLO de los leads de Meta."""
    por_tel: dict = {}
    por_mail: dict = {}
    for bid, phone, email, estado, origen, visto in conn.execute(
        "SELECT id, phone, email, crm_status, semaforo_origen, semaforo_planilla "
        "FROM businesses WHERE source = 'meta'"
    ):
        ficha = {"id": bid, "estado": estado or "sin_contactar",
                 "origen": origen, "visto": visto}
        tel = normalizar_telefono(phone)
        if tel:
            por_tel.setdefault(tel, ficha)
            # Los ultimos 8 digitos alcanzan para casar un numero escrito con y
            # sin codigo de pais. Se guarda como respaldo, nunca pisando una
            # coincidencia exacta.
            if len(tel) > 8:
                por_tel.setdefault(tel[-8:], ficha)
        mail = (email or "").strip().lower()
        if mail:
            por_mail.setdefault(mail, ficha)
    return por_tel, por_mail


def _buscar(fila: dict, por_tel: dict, por_mail: dict):
    tel = normalizar_telefono(fila.get("tel"))
    if tel and tel in por_tel:
        return por_tel[tel]
    if tel and len(tel) > 8 and tel[-8:] in por_tel:
        return por_tel[tel[-8:]]
    mail = (fila.get("mail") or "").strip().lower()
    if mail and mail in por_mail:
        return por_mail[mail]
    return None


def aplicar(db_path: str, filas: list, dry_run: bool = False) -> dict:
    """Lleva los colores de la planilla a los estados del CRM.

    `filas` es lo que postea el Apps Script: dicts con `tel`, `mail` y `color`.
    Devuelve el resumen de lo que hizo, que es lo que el llamador loguea.
    """
    resumen = {
        "recibidas": len(filas),
        "actualizados": 0,
        "sin_cambio": 0,
        "sin_match": 0,
        "color_ignorado": 0,
        "no_retrocede": 0,
        "marcado_a_mano": 0,
        "cambios": [],
    }
    conn = sqlite3.connect(db_path)
    try:
        por_tel, por_mail = _indice(conn)
    finally:
        conn.close()

    # Un mismo telefono puede figurar en varios meses con colores distintos:
    # se resuelve antes de escribir, quedandose con el estado mas avanzado, y
    # no fila por fila. Si no, el orden de las filas decidiria el resultado.
    deseado: dict = {}
    for fila in filas:
        estado = estado_de_color(fila.get("color"))
        if not estado:
            resumen["color_ignorado"] += 1
            continue
        ficha = _buscar(fila, por_tel, por_mail)
        if not ficha:
            resumen["sin_match"] += 1
            continue
        previo = deseado.get(ficha["id"])
        if previo is None or RANK[estado] > RANK[previo[0]]:
            deseado[ficha["id"]] = (estado, ficha)

    vistos = []   # (estado que trajo la planilla, id): se anota aunque no cambie nada
    for bid, (estado, ficha) in deseado.items():
        actual = ficha["estado"]
        if estado != ficha["visto"]:
            vistos.append((estado, bid))
        # Marcado a mano en el CRM (Meta Ads). La planilla la llevan otros y va
        # atrasada: si sigue diciendo lo MISMO que la ultima vez que se la leyo,
        # no trae nada nuevo y la marca a mano se queda. Si se repinto despues
        # (trae otro color), es informacion nueva y pasa por las reglas de
        # siempre: avanza, o el negro que siempre gana salvo sobre un cliente.
        # Sin lectura previa no se sabe si se repinto: solo puede avanzar.
        a_mano = ficha["origen"] == ORIGEN_CRM
        if a_mano and ficha["visto"] is not None and estado == ficha["visto"]:
            resumen["marcado_a_mano"] += 1
            continue
        if estado == actual:
            resumen["sin_cambio"] += 1
            continue
        avanza = RANK[estado] > RANK.get(actual, 0)
        # 'no_interesa' es la UNICA marca de la planilla que puede ir para
        # atras. La pinta una persona que hablo con el lead, y eso es mejor
        # informacion que cualquier estado que haya puesto una automatizacion.
        # Sin esta excepcion la planilla no podia decir nunca "este murio":
        # 'no_interesa' es rango 1, asi que solo se aplicaba a quien estaba en
        # 'sin_contactar', y alguien que dijo que no despues de avanzar se
        # quedaba en el pipeline recibiendo mails.
        #
        # Los clientes quedan afuera: ahi hay plata, y una celda mal pintada no
        # puede borrarla.
        baja_deliberada = (estado == "no_interesa"
                           and RANK.get(actual, 0) < RANK["cerrado"]
                           and not (a_mano and ficha["visto"] is None))
        if not (avanza or baja_deliberada):
            resumen["no_retrocede"] += 1
            continue
        resumen["cambios"].append({"id": bid, "de": actual, "a": estado})
        if not dry_run:
            update_business(db_path, bid, crm_status=estado)
            add_lead_event(db_path, bid, estado, note=NOTA_EVENTO,
                           created_by="sistema (planilla)")
            _anotar_origen(db_path, bid, ORIGEN_PLANILLA)
        resumen["actualizados"] += 1

    # Lo ultimo que dijo la planilla de cada lead. Solo se escribe lo que cambio,
    # asi la segunda corrida sigue sin escribir nada.
    if vistos and not dry_run:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            with conn:
                conn.executemany(
                    "UPDATE businesses SET semaforo_planilla = ? WHERE id = ?", vistos)
        finally:
            conn.close()
    return resumen


def _anotar_origen(db_path: str, bid: int, origen: str, ahora: str | None = None) -> None:
    """Quien puso el color por ultima vez y cuando (UTC, formato de la casa)."""
    ahora = ahora or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        with conn:
            conn.execute("UPDATE businesses SET semaforo_origen = ?, semaforo_at = ? "
                         "WHERE id = ?", (origen, ahora, bid))
    finally:
        conn.close()


# ── Registro de demos desde la planilla ──────────────────────────────────────
# Pedido de Juan (14/9): que el Registro de demos se llene solo con los colores
# del semaforo, mes por mes. Las reglas, todas probadas en
# tests/test_demos_planilla.py:
#
# - Entran los cuatro colores de demo. Negro, rojos y amarillos no son demos.
# - El mes de la demo es la PESTANA de la planilla: la planilla no tiene la
#   fecha de la demo, solo el mes en que entro el lead.
# - Una demo de planilla por cliente y mes (indice unico parcial en la base).
#   Si el color cambia, se actualiza la misma fila; no se duplica.
# - La demo refleja la planilla tal cual, sin la regla de "no retroceder" de
#   `aplicar`: aca no hay automatizaciones que proteger, y si alguien corrige
#   un violeta a celeste, el registro tiene que decir celeste.
# - Si la fila pasa a un color que no es demo, la demo de planilla se borra,
#   salvo que ya tenga un presupuesto adjunto: ese archivo lo subio una persona.
#   Una fila que deja de venir (pintada de blanco, borrada, una pestana que no
#   se pudo leer) NO borra nada: la ausencia no es una marca.
# - Las demos cargadas a mano (`origen` NULL) no se tocan nunca.

ORIGEN_PLANILLA = "planilla"

# Estado del CRM (el que ya sale de COLOR_A_ESTADO) -> clave estable de la demo.
ESTADO_A_DEMO = {
    "demo_agendada": "agendada",
    "demo_1": "realizada",
    "presupuesto_enviado": "no_cerro",
    "cerrado": "venta",
}

_MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "setiembre": 9, "septiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}


def _hoy_uruguay() -> date:
    # Uruguay no tiene horario de verano desde 2015: UTC-3 fijo. La maquina
    # corre en UTC, y el 1 del mes a las 22 h de aca alla ya es el mes siguiente.
    return (datetime.now(timezone.utc) - timedelta(hours=3)).date()


def mes_de_pestana(nombre, hoy: date | None = None) -> str | None:
    """'Agosto' -> '2026-08'. None si la pestana no es un mes.

    La planilla es del ano en curso y no dice el ano en el nombre: un mes
    posterior al actual solo puede ser del ano anterior (en febrero, la
    pestana 'Diciembre' es la del diciembre pasado). Acepta 'Setiembre' y
    'Septiembre', con o sin tilde y en cualquier mayuscula, y un ano explicito
    ('Agosto 2026') si algun dia lo agregan.
    """
    texto = unicodedata.normalize("NFKD", str(nombre or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    partes = texto.lower().split()
    if not partes or partes[0] not in _MESES or len(partes) > 2:
        return None
    mes = _MESES[partes[0]]
    if len(partes) == 2:
        if not re.fullmatch(r"\d{4}", partes[1]):
            return None
        anio = int(partes[1])
    else:
        hoy = hoy or _hoy_uruguay()
        anio = hoy.year if mes <= hoy.month else hoy.year - 1
    return f"{anio:04d}-{mes:02d}"


def sincronizar_demos(db_path: str, filas: list, dry_run: bool = False,
                      hoy: date | None = None) -> dict:
    """Lleva los colores de demo de la planilla al Registro de demos.

    Corre despues de `aplicar` y no depende de lo que `aplicar` haya escrito:
    solo usa el mismo match de leads. `filas` son los dicts del Apps Script con
    `tel`, `mail`, `color` y `mes` (el nombre de la pestana).
    """
    resumen = {
        "creadas": 0,
        "actualizadas": 0,
        "borradas": 0,
        "sin_cambio": 0,
        "sin_match": 0,
        "mes_ignorado": 0,
        "con_presupuesto_no_se_borra": 0,
    }
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        por_tel, por_mail = _indice(conn)

        # Primero se resuelve cada (lead, mes) con el color mas avanzado, igual
        # que en `aplicar`: si el mismo telefono esta dos veces en la pestana,
        # el orden de las filas no puede decidir.
        ganador: dict = {}
        for fila in filas:
            if not isinstance(fila, dict):
                continue
            estado = estado_de_color(fila.get("color"))
            if not estado:
                continue
            mes = mes_de_pestana(fila.get("mes"), hoy)
            if not mes:
                if estado in ESTADO_A_DEMO:
                    resumen["mes_ignorado"] += 1
                continue
            ficha = _buscar(fila, por_tel, por_mail)
            if not ficha:
                if estado in ESTADO_A_DEMO:
                    resumen["sin_match"] += 1
                continue
            clave = (ficha["id"], mes)
            previo = ganador.get(clave)
            if previo is None or RANK[estado] > RANK[previo]:
                ganador[clave] = estado

        # Un lead marcado a mano en Meta Ads no lo toca la planilla mientras siga
        # diciendo lo de antes: `aplicar`, que corre primero, le devuelve el
        # origen 'planilla' cuando la planilla trae algo nuevo. Hasta entonces
        # su demo es la que dejo la marca a mano.
        resumen["marcado_a_mano"] = 0
        manuales = {bid for (bid,) in conn.execute(
            "SELECT id FROM businesses WHERE source = 'meta' AND semaforo_origen = ?",
            (ORIGEN_CRM,))}
        for clave in [c for c in ganador if c[0] in manuales]:
            del ganador[clave]
            resumen["marcado_a_mano"] += 1
        return _escribir_demos(conn, ganador, resumen, dry_run)
    finally:
        conn.close()


def _escribir_demos(conn: sqlite3.Connection, ganador: dict, resumen: dict,
                    dry_run: bool = False) -> dict:
    """Lleva {(lead, 'AAAA-MM'): estado del CRM} al Registro de demos.

    La usan el sync de la planilla y la marca a mano de Meta Ads, asi las dos
    crean, actualizan y borran la demo del mes con las mismas reglas. Un estado
    que no es de demo borra la demo de planilla de ese mes, salvo que tenga un
    presupuesto adjunto.
    """
    # Solo las de planilla. El EXISTS usa el indice de demo_id y no lee el
    # BLOB del presupuesto.
    existentes = {
        (cid, mes): (did, est, bool(presu))
        for did, cid, mes, est, presu in conn.execute(
            "SELECT d.id, d.client_id, d.mes_planilla, d.estado_planilla, "
            "       EXISTS (SELECT 1 FROM lead_attachments a WHERE a.demo_id = d.id) "
            "FROM demos_realizadas d WHERE d.origen = ?", (ORIGEN_PLANILLA,))
    }

    crear, actualizar, borrar = [], [], []
    # Ordenado por cliente y mes para que el numero de demo siga el orden
    # de los meses cuando un cliente entra con varias de una vez.
    for (cid, mes), estado in sorted(ganador.items()):
        demo = ESTADO_A_DEMO.get(estado)
        previa = existentes.get((cid, mes))
        if demo:
            if previa is None:
                crear.append((cid, mes, demo))
            elif previa[1] != demo:
                actualizar.append((previa[0], demo))
            else:
                resumen["sin_cambio"] += 1
        elif previa is not None:
            if previa[2]:
                resumen["con_presupuesto_no_se_borra"] += 1
            else:
                borrar.append(previa[0])

    resumen["creadas"] = len(crear)
    resumen["actualizadas"] = len(actualizar)
    resumen["borradas"] = len(borrar)
    if dry_run or not (crear or actualizar or borrar):
        return resumen

    with conn:  # una sola transaccion: o entra todo o nada
        for cid, mes, demo in crear:
            # OR IGNORE: si otra corrida la creo recien, el indice unico
            # la frena y no se duplica.
            conn.execute(
                "INSERT OR IGNORE INTO demos_realizadas "
                "  (client_id, numero, fecha, origen, estado_planilla, mes_planilla) "
                "SELECT ?, COALESCE(MAX(numero), 0) + 1, ?, ?, ?, ? "
                "FROM demos_realizadas WHERE client_id = ?",
                (cid, f"{mes}-01", ORIGEN_PLANILLA, demo, mes, cid))
        for did, demo in actualizar:
            conn.execute(
                "UPDATE demos_realizadas SET estado_planilla = ? "
                "WHERE id = ? AND origen = ?", (demo, did, ORIGEN_PLANILLA))
        for did in borrar:
            # Las dos guardas otra vez en el SQL, por si algo cambio entre
            # la lectura y la escritura: nunca una a mano, nunca una con
            # presupuesto.
            conn.execute(
                "DELETE FROM demos_realizadas WHERE id = ? AND origen = ? "
                "AND NOT EXISTS (SELECT 1 FROM lead_attachments a WHERE a.demo_id = ?)",
                (did, ORIGEN_PLANILLA, did))
    return resumen


# ── El semaforo marcado a mano desde Meta Ads ────────────────────────────────
# Pedido de Juan (15/9): marcar el color desde el CRM, tocando el color. Reglas,
# todas probadas en tests/test_meta_ads_por_mes.py:
#
# - Se guarda donde lo guarda la planilla: `crm_status` (el color sale de ahi,
#   ver ESTADO_A_COLOR) y la demo de planilla del mes en el Registro de demos,
#   con la misma logica que el sync (`_escribir_demos`). No hay una columna de
#   color aparte que pueda discrepar.
# - La marca a mano SI puede ir para atras: la elige una persona mirando el lead.
# - Un cliente no se baja con un toque (409), igual que el negro de la planilla.
# - Deja `semaforo_origen='crm'` y la hora. Mientras la planilla siga diciendo
#   lo mismo que antes de la marca, el sync no la pisa (ver `aplicar`).
# - Queda en los eventos del lead y en la actividad, como cualquier cambio de
#   estado hecho por una persona, y mueve las mismas metas.

class MarcaInvalida(ValueError):
    def __init__(self, mensaje: str, status: int = 400):
        super().__init__(mensaje)
        self.status = status


_FECHA_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?")


def mes_montevideo(fecha) -> str | None:
    """'AAAA-MM' de una fecha UTC de la base, en hora de Montevideo (UTC-3).

    El 1 del mes a las 01:00 UTC todavia es el mes anterior aca. Una fecha sin
    hora queda en su mes: correrla tres horas la mandaria al dia antes.
    """
    m = _FECHA_RE.match(str(fecha or "").strip())
    if not m:
        return None
    try:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if m.group(4) is None:
            date(y, mo, d)
            return f"{y:04d}-{mo:02d}"
        local = datetime(y, mo, d, int(m.group(4)), int(m.group(5)),
                         int(m.group(6) or 0)) - timedelta(hours=3)
    except ValueError:
        return None
    return f"{local.year:04d}-{local.month:02d}"


def _resumen_demos() -> dict:
    return {"creadas": 0, "actualizadas": 0, "borradas": 0, "sin_cambio": 0,
            "con_presupuesto_no_se_borra": 0}


def marcar_color(db_path: str, lead_id: int, clave, mes=None,
                 cambiar_estado=None, ahora: str | None = None) -> dict:
    """Pone a mano el color `clave` (o SIN_COLOR) a un lead de Meta.

    `cambiar_estado(estado, nota)` hace el cambio de estado con el rastro de una
    persona (la ruta pasa el de routes/leads.py). `mes` es el mes de la lista
    desde donde se marco: si la persona escribio ese mes, la demo va ahi; si no,
    al mes de su primer formulario.

    Lanza MarcaInvalida con el status HTTP que corresponde.
    """
    from database import ENVIOS_META_SQL

    if not isinstance(clave, str) or (clave != SIN_COLOR and clave not in COLORES):
        raise MarcaInvalida("Color inválido", 400)

    conn = sqlite3.connect(db_path, timeout=10)
    try:
        fila = conn.execute(
            "SELECT source, crm_status, semaforo_origen FROM businesses WHERE id = ?",
            (lead_id,)).fetchone()
        if not fila:
            raise MarcaInvalida("Lead no encontrado", 404)
        source, actual, origen = fila
        if (source or "") != "meta":
            raise MarcaInvalida("El semáforo se marca solo en leads de Meta Ads", 400)
        actual = actual or "sin_contactar"
        destino = "" if clave == SIN_COLOR else clave
        if destino == color_de_estado(actual):
            return {"cambio": False, "crm_status": actual, "semaforo": destino,
                    "semaforo_origen": origen, "demos": None}
        if actual in ESTADOS_DE_CLIENTE:
            raise MarcaInvalida("Es un cliente: su estado se cambia desde Clientes, "
                                "no con el semáforo", 409)

        nuevo = COLORES[destino][1] if destino else "sin_contactar"
        etiqueta = COLORES[destino][0] if destino else "Sin color"
        nota = f"semaforo marcado en el CRM: {etiqueta}"
        if cambiar_estado:
            cambiar_estado(nuevo, nota)
        else:
            update_business(db_path, lead_id, crm_status=nuevo)
            add_lead_event(db_path, lead_id, nuevo, note=nota, created_by="CRM")
        _anotar_origen(db_path, lead_id, ORIGEN_CRM, ahora)

        meses = {mes_montevideo(f) for (f,) in conn.execute(
            f"SELECT scraped_at FROM ({ENVIOS_META_SQL}) WHERE id = ?", (lead_id,))}
        meses.discard(None)
        mes_demo = mes if mes in meses else (min(meses) if meses else None)
        demos = _resumen_demos()
        if mes_demo:
            _escribir_demos(conn, {(lead_id, mes_demo): nuevo}, demos)
        return {"cambio": True, "crm_status": nuevo, "semaforo": destino,
                "semaforo_origen": ORIGEN_CRM, "mes_demo": mes_demo, "demos": demos}
    finally:
        conn.close()
