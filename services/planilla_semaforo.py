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

from database import add_lead_event, get_business, update_business

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
    for bid, phone, email, estado in conn.execute(
        "SELECT id, phone, email, crm_status FROM businesses WHERE source = 'meta'"
    ):
        ficha = {"id": bid, "estado": estado or "sin_contactar"}
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
            deseado[ficha["id"]] = (estado, ficha["estado"])

    for bid, (estado, actual) in deseado.items():
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
                           and RANK.get(actual, 0) < RANK["cerrado"])
        if not (avanza or baja_deliberada):
            resumen["no_retrocede"] += 1
            continue
        resumen["cambios"].append({"id": bid, "de": actual, "a": estado})
        if not dry_run:
            update_business(db_path, bid, crm_status=estado)
            add_lead_event(db_path, bid, estado, note=NOTA_EVENTO,
                           created_by="sistema (planilla)")
        resumen["actualizados"] += 1
    return resumen


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
    finally:
        conn.close()
