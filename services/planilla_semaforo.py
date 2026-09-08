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
