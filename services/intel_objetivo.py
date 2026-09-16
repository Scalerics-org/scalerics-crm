"""El objetivo real del mes y los gastos que ya se sabe que van a caer.

Dos cosas que Inteligencia financiera necesita y que no salen solas de los
datos, porque son decisiones de Juan, no hechos del sistema:

**El objetivo real del mes** no es "cubrir los fijos". Son tres partes, todas
editables (spec del 15/9):

1. Costos fijos del mes. Por defecto, los fijos confirmados de Finanzas
   (`finanzas_recurrentes` vigentes) MAS los gastos esperados de abajo. Se puede
   pisar a mano. Se guarda NULL y no el numero copiado: guardar la copia
   congelaria el objetivo el dia que cambie un fijo.
2. Monto a reemplazar de aportes externos (el aporte mensual de un socio).
3. Sueldo objetivo que Juan quiere poder pagarse.

**Los gastos esperados** se cargan cuando se sabe, no al cierre del mes, y
suman al objetivo en vivo. Cuando el gasto se carga de verdad como movimiento
en Finanzas hay que emparejarlo y sacarlo de la lista, o el mes lo cuenta dos
veces: una como esperado y otra como real.

El emparejado no adivina en silencio. Se empareja solo cuando hay UN candidato
y coinciden las dos cosas (monto dentro de la tolerancia y concepto parecido);
si hay dudas -coincide el monto pero no el texto, o hay dos esperados que
podrian ser- la pantalla se lo pregunta a Juan y el confirma. Entre equivocarse
callado y preguntar, se pregunta.

Este modulo es la capa de abajo: no importa `services/inteligencia_fin.py`
(que si lo importa a el), asi `routes/finanzas.py` puede emparejar sin
arrastrar todo el motor de reglas.
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

import json

from database import (_connect, get_escenario, listar_escenarios,
                      listar_movimientos, listar_recurrentes)
from services.finanzas import a_usd

logger = logging.getLogger(__name__)

CATEGORIAS_ESPERADO = ("fijo", "variable")
MONTO_MAX = 1_000_000
CONCEPTO_MAX = 120

# Lo que NO es gasto fijo de estructura, aunque salga todos los meses:
# la pauta es de las palancas de Meta, los impuestos son grandes y desparejos
# (no un mensual parejo) y los retiros son justamente lo que el objetivo quiere
# poder pagar. Se muestran aparte, no se esconden.
CATEGORIAS_FUERA_DE_ESTRUCTURA = ("publicidad", "impuestos", "retiros")
ETIQUETA_FUERA = {"publicidad": "es la pauta: va en las alternativas de Meta",
                  "impuestos": "cae de a saltos, no todos los meses igual",
                  "retiros": "es lo que el objetivo quiere poder pagarte"}

# Los sueldos no estan en la pestana de Fijos: se cargan como movimientos
# sueltos, mes a mes, con el nombre de la persona en el concepto. Son el gasto
# fijo mas grande que hay, asi que el objetivo tiene que contarlos.
PALABRAS_EQUIPO = {"sueldo", "sueldos", "salario", "salarios", "honorario",
                   "honorarios", "nomina", "jornal", "jornales"}
MESES_EQUIPO = 3
# Las dos formas de cobrar que hay en el equipo: sueldo fijo por mes, o
# comision sobre el desarrollo (Matias: 50 %, y solo si se le da un proyecto).
TIPOS_EQUIPO = ("fijo", "comision")
CANTIDAD_MAX = 100
# Los tres grupos que pidio Juan (16/9): "por un lado sueldos. Por el otro
# honorarios, por el otro los fijos".
MESES_ES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
            "agosto", "setiembre", "octubre", "noviembre", "diciembre")
GRUPOS_COSTO = ("sueldos", "honorarios", "fijos")
ETIQUETA_GRUPO = {"sueldos": "Sueldos", "honorarios": "Honorarios",
                  "fijos": "Fijos de estructura"}
# Un pago que aparecio un solo mes no es un sueldo: es un pago suelto.
MESES_PARA_SER_RECURRENTE = 2

# Palabras de infraestructura y de servicios. Nunca identifican a un cliente,
# asi que no se usan para emparejar: sin esto, "Pasarela de Pagos" se cruzaria
# con cualquier negocio que tenga "pagos" en el nombre.
PALABRAS_TECNICAS = {"servidor", "servidores", "server", "hosting", "vps", "dominio", "dominios",
                     "pasarela", "pagos", "pago", "sistema", "sistemas", "facturacion", "factura",
                     "backup", "backups", "correo", "mail", "mails", "software", "licencia",
                     "licencias", "plan", "planes", "suscripcion", "api", "base", "datos",
                     "almacenamiento", "nube", "cloud", "web", "sitio", "app", "aplicacion",
                     "herramienta", "herramientas", "infraestructura", "mantenimiento"}
# Un token mas corto que esto no distingue a nadie.
LARGO_MINIMO_NOMBRE = 4

# Tolerancia del emparejado: el 10 % del monto esperado, con un piso de USD 5
# para que un gasto chico no pida coincidencia exacta al centavo.
TOL_PCT = 0.10
TOL_USD = 5.0
# Cuanto se tienen que parecer los conceptos para emparejar solo. 0,6 deja
# pasar "Hosting" contra "Hosting AWS" y frena "Hosting" contra "Contador".
PARECIDO_AUTO = 0.6

_MVD = timezone(timedelta(hours=-3))

# Palabras que no distinguen nada: estan en medio gasto del mes.
_VACIAS = {"de", "del", "la", "el", "los", "las", "y", "a", "en", "para", "por",
           "mes", "mensual", "pago", "cuota", "factura", "servicio", "usd"}


# ── tiempo ───────────────────────────────────────────────────────────────────

def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def hoy_mvd(ahora: datetime | None = None) -> date:
    return (ahora or _ahora()).astimezone(_MVD).date()


def periodo_de(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _q(db_path: str, sql: str, params=()) -> list[dict]:
    conn = _connect(db_path)
    try:
        return [dict(r) for r in conn.execute(sql, list(params)).fetchall()]
    finally:
        conn.close()


def _ejecutar(db_path: str, sql: str, params=()):
    conn = _connect(db_path)
    try:
        cur = conn.execute(sql, list(params))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _numero(valor) -> float | None:
    if isinstance(valor, bool) or valor is None:
        return None
    try:
        x = float(str(valor).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


# ── fijos confirmados de Finanzas ────────────────────────────────────────────

def _meses_previos(mes: str, cuantos: int = MESES_EQUIPO) -> list[str]:
    y, m = int(mes[:4]), int(mes[5:7])
    salida = []
    for i in range(cuantos, 0, -1):
        indice = y * 12 + (m - 1) - i
        salida.append(f"{indice // 12:04d}-{indice % 12 + 1:02d}")
    return salida


def _vigencia(r: dict, mes: str) -> str:
    """Si ese fijo ya corre, arranca más adelante o ya terminó."""
    if r["hasta"] and r["hasta"] < mes:
        return "terminado"
    if r["desde"] and r["desde"] > mes:
        return "futuro"
    return "vigente"


def _desde_en_palabras(periodo) -> str:
    try:
        return f"desde {MESES_ES[int(str(periodo)[5:7]) - 1]}"
    except (TypeError, ValueError, IndexError):
        return "todavía no arrancó"


def _recurrentes_egreso(db_path: str, mes: str) -> list[tuple[dict, float, str]]:
    """TODOS los gastos fijos de la pestaña Fijos, sin filtrar por fecha.

    Juan (16/9): "anda a fijos y agarra todos incluso los que se van a pagar
    cobrar el mes que viene y ahi los tenes". Así que los que arrancan el mes
    que viene ENTRAN —él planifica con ellos— y se marcan para que se vea por
    qué el número es más grande que lo que pagó este mes. Los que ya
    terminaron se listan igual, apagados, con el motivo.
    """
    salida = []
    for r in listar_recurrentes(db_path, solo_activos=True):
        if r["tipo"] != "egreso":
            continue
        try:
            monto = round(a_usd(r["monto"], r["moneda"], r["tipo_cambio"]), 2)
        except (TypeError, ValueError):
            # Un fijo en pesos sin tipo de cambio es un dato roto de Finanzas,
            # no un cero: se saltea y se sigue, no tira abajo el objetivo.
            logger.warning("objetivo: fijo %s sin tipo de cambio usable", r["id"])
            continue
        salida.append((r, monto, _vigencia(r, mes)))
    return salida


def ingresos_recurrentes(db_path: str, mes: str) -> dict:
    """Los ingresos fijos (los mantenimientos), futuros incluidos.

    Los de Juan arrancan todos en octubre. Si no se contaran, el panel le
    mostraría un agujero que en realidad no tiene.
    """
    filas, total = [], 0.0
    for r in listar_recurrentes(db_path, solo_activos=True):
        if r["tipo"] != "ingreso":
            continue
        vigencia = _vigencia(r, mes)
        if vigencia == "terminado":
            continue
        try:
            monto = round(a_usd(r["monto"], r["moneda"], r["tipo_cambio"]), 2)
        except (TypeError, ValueError):
            continue
        filas.append({"id": r["id"], "nombre": r["concepto"], "monto_usd": monto,
                      "vigencia": vigencia,
                      "nota": _desde_en_palabras(r["desde"]) if vigencia == "futuro" else ""})
        total += monto
    filas.sort(key=lambda f: -f["monto_usd"])
    return {"total": round(total, 2), "filas": filas}


def _tokens_de_clientes(db_path: str) -> dict:
    """Palabras que identifican a un cliente, sacadas de sus nombres.

    Salen de los negocios y de los ingresos de mantenimiento ("Mantenimiento
    Rodrigo" ⇒ rodrigo). Se descartan las palabras técnicas y las cortas, que
    no identifican a nadie.
    """
    tokens: dict = {}
    for f in _q(db_path, "SELECT id, name FROM businesses "
                         "WHERE name IS NOT NULL AND TRIM(name) != ''"):
        for p in _palabras(f["name"]):
            if len(p) >= LARGO_MINIMO_NOMBRE and p not in PALABRAS_TECNICAS:
                tokens.setdefault(p, f["id"])
    for r in listar_recurrentes(db_path, solo_activos=True):
        if r["tipo"] != "ingreso":
            continue
        for p in _palabras(r["concepto"]):
            if len(p) >= LARGO_MINIMO_NOMBRE and p not in PALABRAS_TECNICAS:
                tokens.setdefault(p, r["client_id"])
    return tokens


def clasificar_fijos(db_path: str, mes: str) -> dict:
    """Cada gasto recurrente, en estructura o en costo de un cliente, y por qué.

    Un gasto fijo de estructura hay que cubrirlo sí o sí. El costo indirecto de
    un cliente de mantenimiento (los servidores de Fulano, USD 25) existe solo
    mientras exista ese cliente y se descuenta de lo que ese cliente deja, así
    que no va al objetivo del mes.

    En los datos de verdad, esa diferencia no está marcada: los costos de
    cliente están cargados como infraestructura común, sin cliente. Así que se
    PROPONE mirando si el concepto nombra a un cliente, cada línea dice por qué
    quedó donde quedó, y Juan puede moverla de grupo. El orden manda: lo que él
    marcó, después el cliente que tenga el fijo en Finanzas, y recién ahí la
    propuesta.
    """
    overrides = {r["recurrente_id"]: r for r in _q(db_path, "SELECT * FROM if_fijo_clasificacion")}
    tokens = _tokens_de_clientes(db_path)
    estructura, cliente = [], []
    for r, monto, vigencia in _recurrentes_egreso(db_path, mes):
        fila = {"id": r["id"], "concepto": r["concepto"], "categoria": r["categoria"],
                "monto_usd": monto, "estado": "confirmado", "sugerido": False,
                "vigencia": vigencia,
                "nota": _desde_en_palabras(r["desde"]) if vigencia == "futuro"
                        else ("ya terminó" if vigencia == "terminado" else "")}
        ov = overrides.get(r["id"])
        if ov:
            fila["motivo"] = "lo marcaste vos"
            (cliente if ov["clase"] == "cliente" else estructura).append(fila)
            continue
        if r["client_id"]:
            fila["motivo"] = "tiene un cliente puesto en Finanzas"
            cliente.append(fila)
            continue
        nombres = (_palabras(r["concepto"]) - PALABRAS_TECNICAS) & set(tokens)
        nombres = {n for n in nombres if len(n) >= LARGO_MINIMO_NOMBRE}
        if nombres:
            fila["motivo"] = "el concepto nombra a un cliente: " + ", ".join(sorted(nombres))
            fila["sugerido"] = True
            cliente.append(fila)
            continue
        fila["motivo"] = ""
        estructura.append(fila)
    estructura.sort(key=lambda f: -f["monto_usd"])
    cliente.sort(key=lambda f: -f["monto_usd"])
    return {"estructura": estructura, "cliente": cliente}


def marcar_fijo(db_path: str, recurrente_id: int, clase, client_id=None, quien: str = "",
                ahora: datetime | None = None) -> str | None:
    """Juan mueve un gasto fijo entre estructura y costo de cliente."""
    if clase not in ("estructura", "cliente"):
        return "el gasto fijo va en estructura o en costo de un cliente"
    if not _q(db_path, "SELECT id FROM finanzas_recurrentes WHERE id = ?", (recurrente_id,)):
        return "ese gasto fijo no existe"
    _ejecutar(db_path,
              "INSERT INTO if_fijo_clasificacion (recurrente_id, clase, client_id, updated_by, "
              "updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(recurrente_id) DO UPDATE SET "
              "clase = excluded.clase, client_id = excluded.client_id, "
              "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
              (recurrente_id, clase, client_id, quien, _iso(ahora or _ahora())))
    return None


def listar_equipo(db_path: str) -> list[dict]:
    """Las líneas de costo del equipo, como las escribió Juan."""
    return _q(db_path, "SELECT * FROM if_equipo_costos ORDER BY orden, id")


def equipo_comision(db_path: str) -> list[dict]:
    """Los que no tienen costo fijo y cobran un % del desarrollo.

    Matías Domínguez es el caso: si no se le da un proyecto no cuesta nada, y
    si se le da cobra la mitad de ese desarrollo. No va al objetivo del mes:
    entra en el margen del proyecto que hace.
    """
    return [f for f in listar_equipo(db_path) if f["tipo"] == "comision" and f["pct"] > 0]


def validar_linea_equipo(datos) -> tuple[dict | None, str | None]:
    if not isinstance(datos, dict):
        return None, "faltan los datos de la persona"
    nombre = str(datos.get("nombre") or "").strip()
    if not nombre:
        return None, "poné el nombre o el rol"
    if len(nombre) > CONCEPTO_MAX:
        return None, f"el nombre no puede pasar de {CONCEPTO_MAX} caracteres"
    tipo = datos.get("tipo") or "fijo"
    if tipo not in TIPOS_EQUIPO:
        return None, "el tipo es fijo o comisión"
    cantidad = _numero(datos.get("cantidad", 1))
    if cantidad is None or cantidad < 0 or cantidad != int(cantidad):
        return None, "la cantidad tiene que ser un número entero de cero para arriba"
    if cantidad > CANTIDAD_MAX:
        return None, f"la cantidad no puede pasar de {CANTIDAD_MAX}"
    if tipo == "comision":
        pct = _numero(datos.get("pct"))
        if pct is None or pct <= 0 or pct > 100:
            return None, "la comisión es un porcentaje entre 0 y 100"
        return {"nombre": nombre, "monto_usd": 0.0, "cantidad": int(cantidad),
                "tipo": tipo, "pct": round(pct, 2)}, None
    monto = _numero(datos.get("monto_usd") if "monto_usd" in datos else datos.get("monto"))
    if monto is None or monto < 0:
        return None, "el monto tiene que ser un número de cero para arriba"
    if monto > MONTO_MAX:
        return None, "ese monto es demasiado grande"
    return {"nombre": nombre, "monto_usd": round(monto, 2), "cantidad": int(cantidad),
            "tipo": tipo, "pct": 0.0}, None


def guardar_linea_equipo(db_path: str, linea_id, datos, quien: str = "",
                         ahora: datetime | None = None) -> tuple[dict | None, str | None]:
    """Crea una línea (sin id) o edita la que se le pase."""
    campos, error = validar_linea_equipo(datos)
    if error:
        return None, error
    momento = _iso(ahora or _ahora())
    if linea_id:
        if not _q(db_path, "SELECT id FROM if_equipo_costos WHERE id = ?", (linea_id,)):
            return None, "esa persona no está en la lista"
        _ejecutar(db_path,
                  "UPDATE if_equipo_costos SET nombre = ?, monto_usd = ?, cantidad = ?, tipo = ?, "
                  "pct = ?, updated_by = ?, updated_at = ? WHERE id = ?",
                  (campos["nombre"], campos["monto_usd"], campos["cantidad"], campos["tipo"],
                   campos["pct"], quien, momento, linea_id))
    else:
        orden = (_q(db_path, "SELECT COALESCE(MAX(orden), 0) + 1 AS o FROM if_equipo_costos")
                 or [{"o": 0}])[0]["o"]
        linea_id = _ejecutar(db_path,
                             "INSERT INTO if_equipo_costos (nombre, monto_usd, cantidad, tipo, "
                             "pct, orden, updated_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                             (campos["nombre"], campos["monto_usd"], campos["cantidad"],
                              campos["tipo"], campos["pct"], orden, quien, momento))
    return {"id": linea_id, **campos}, None


def borrar_linea_equipo(db_path: str, linea_id: int) -> str | None:
    if not _q(db_path, "SELECT id FROM if_equipo_costos WHERE id = ?", (linea_id,)):
        return "esa persona no está en la lista"
    _ejecutar(db_path, "DELETE FROM if_equipo_costos WHERE id = ?", (linea_id,))
    return None


def costo_equipo(db_path: str, mes: str) -> dict:
    """El costo del equipo por mes: la lista que escribió Juan.

    Hasta el 16/9 esto era el promedio de los 3 meses anteriores de sueldos y
    honorarios. Daba unas 4 veces de más, porque metía en la bolsa pagos que no
    son sueldo mensual. Ahora la fuente es la lista de `if_equipo_costos`, una
    línea por rol o persona, con su monto y cuántos son (2 programadores a 50
    son 100). Juan la edita, agrega y saca gente desde la pantalla.

    Los de comisión (Matías: 50 % del desarrollo) NO suman acá: no cuestan nada
    si no se les da un proyecto, y cuando se les da, el costo sale del margen de
    ese proyecto. Se devuelven aparte para que la pantalla los muestre igual.

    El promedio sigue existiendo, pero solo de respaldo para una base sin lista,
    y en ese caso `fuente` lo dice para que la pantalla lo avise.
    """
    lineas = listar_equipo(db_path)
    comision = [f for f in lineas if f["tipo"] == "comision" and f["pct"] > 0]
    fijas = [f for f in lineas if f["tipo"] == "fijo"]
    if fijas:
        filas, total = [], 0.0
        for f in fijas:
            subtotal = round(float(f["monto_usd"]) * int(f["cantidad"]), 2)
            filas.append({"id": f["id"], "concepto": f["nombre"], "monto_usd": subtotal,
                          "unitario": round(float(f["monto_usd"]), 2),
                          "cantidad": int(f["cantidad"]), "tipo": "fijo", "pct": 0.0,
                          "estado": "equipo"})
            total += subtotal
        return {"total": round(total, 2), "filas": filas, "sueltos": [], "comision": comision,
                "fuente": "lista", "es_promedio": False}
    return _costo_equipo_promedio(db_path, mes, comision)


def _costo_equipo_promedio(db_path: str, mes: str, comision: list) -> dict:
    """El respaldo de antes: promedio de los 3 meses previos de los movimientos.

    Solo se usa con la lista vacía. Cuenta lo que se repite (aparece en 2 de
    esos 3 meses); un pago que salió una sola vez se informa aparte y no suma.
    """
    previos = _meses_previos(mes)
    movs = [m for m in listar_movimientos(db_path, desde=previos[0], hasta=previos[-1],
                                          tipo="egreso")
            if _palabras(m["concepto"]) & PALABRAS_EQUIPO]
    grupos: dict = {}
    for m in movs:
        # La clave es la persona, no el texto exacto: los conceptos vienen con
        # erratas y cambian de mes a mes.
        clave = " ".join(sorted(_palabras(m["concepto"]) - PALABRAS_EQUIPO)) or "equipo"
        g = grupos.setdefault(clave, {"concepto": m["concepto"], "meses": set(), "total": 0.0})
        g["meses"].add(m["periodo"])
        g["total"] += float(m["monto_usd"] or 0)
    filas, sueltos, total = [], [], 0.0
    for g in grupos.values():
        mensual = round(g["total"] / len(previos), 2)
        fila = {"concepto": g["concepto"], "monto_usd": mensual, "meses": len(g["meses"]),
                "estado": "equipo"}
        if len(g["meses"]) >= MESES_PARA_SER_RECURRENTE:
            filas.append(fila)
            total += mensual
        else:
            sueltos.append(fila)
    filas.sort(key=lambda f: -f["monto_usd"])
    sueltos.sort(key=lambda f: -f["monto_usd"])
    return {"total": round(total, 2), "filas": filas, "sueltos": sueltos,
            "comision": comision, "fuente": "promedio", "es_promedio": True,
            "meses": len(previos), "desde": previos[0], "hasta": previos[-1]}


def escenarios_guardados(db_path: str) -> list[dict]:
    """Los escenarios que Juan guardó en el Simulador. Solo de lectura.

    Juan (16/9): "de ultima voy creo un escenario y me da una recomendacion".
    El panel los lee para poder calcular contra uno de ellos, pero nunca los
    toca: el Simulador es el dueño.
    """
    try:
        return [{"id": e["id"], "nombre": e["nombre"], "actualizado": e["updated_at"]}
                for e in listar_escenarios(db_path)]
    except Exception:
        logger.warning("objetivo: no se pudieron leer los escenarios", exc_info=True)
        return []


def escenario_para_panel(db_path: str, escenario_id) -> dict | None:
    """Los supuestos de un escenario guardado, en los nombres del panel.

    Del JSON del Simulador solo se toma lo que el panel usa: cuántos
    programadores, qué se paga de pauta, el costo por lead y las dos
    conversiones del embudo. El resto del escenario no se toca.
    """
    try:
        fila = get_escenario(db_path, int(escenario_id))
    except (TypeError, ValueError):
        return None
    if not fila:
        return None
    try:
        datos = json.loads(fila["datos"] or "{}")
    except ValueError:
        datos = {}
    if not isinstance(datos, dict):
        datos = {}

    def leer(grupo, clave):
        valor = _numero((datos.get(grupo) or {}).get(clave))
        return valor if valor is not None and valor >= 0 else None

    return {
        "id": fila["id"], "nombre": fila["nombre"], "actualizado": fila["updated_at"],
        "supuestos": {
            "programadores": leer("equipo", "cantidadProgramadores"),
            "sueldo_programador": leer("equipo", "sueldoPorProgramador"),
            "pauta": leer("ventas", "pauta"),
            "costo_por_lead": leer("embudo", "costoPorLead"),
            "conv_lead_demo": leer("embudo", "conversionLeadDemo"),
            "conv_demo_venta": leer("embudo", "conversionDemoVenta"),
            "sueldo_objetivo": leer("meta", "sueldoObjetivo"),
        },
    }


def apagados(db_path: str) -> set:
    """Las claves de costo que Juan apagó. Ausente es prendido."""
    return {f["clave"] for f in _q(db_path, "SELECT clave FROM if_costos_activos WHERE activo = 0")}


def marcar_costo(db_path: str, clave: str, activo, quien: str = "",
                 ahora: datetime | None = None) -> str | None:
    """Prende o apaga un costo. Apagado sale del objetivo, pero se sigue viendo."""
    clave = str(clave or "").strip()
    if not clave or len(clave) > CONCEPTO_MAX:
        return "no se reconoce ese costo"
    _ejecutar(db_path,
              "INSERT INTO if_costos_activos (clave, activo, updated_by, updated_at) "
              "VALUES (?, ?, ?, ?) ON CONFLICT(clave) DO UPDATE SET activo = excluded.activo, "
              "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
              (clave, 1 if activo else 0, quien, _iso(ahora or _ahora())))
    return None


def movimientos_que_se_repiten(db_path: str, mes: str, ya_estan: set) -> list[dict]:
    """Gastos que solo existen como movimientos y vuelven todos los meses.

    En Finanzas hay costos que nunca se cargaron como "fijo": se anotan mes a
    mes como movimiento suelto. Si el panel mira solo la pestaña de Fijos, esos
    no aparecen, y Juan pidió que aparezcan TODOS. Se toman los que se repiten
    (2 de los 3 meses anteriores) y no los generó un fijo, y se promedia.

    `ya_estan` son los nombres que ya salen de la lista del equipo, para no
    contar dos veces a la misma persona.
    """
    previos = _meses_previos(mes)
    movs = [m for m in listar_movimientos(db_path, desde=previos[0], hasta=previos[-1],
                                          tipo="egreso")
            if not m["recurrente_id"] and m["categoria"] not in CATEGORIAS_FUERA_DE_ESTRUCTURA]
    grupos: dict = {}
    for m in movs:
        clave = " ".join(sorted(_palabras(m["concepto"]))) or "gasto"
        g = grupos.setdefault(clave, {"concepto": m["concepto"], "meses": set(), "total": 0.0,
                                      "categoria": m["categoria"]})
        g["meses"].add(m["periodo"])
        g["total"] += float(m["monto_usd"] or 0)
    salida = []
    for clave, g in grupos.items():
        if len(g["meses"]) < MESES_PARA_SER_RECURRENTE:
            continue
        if _raices(g["concepto"]) & ya_estan:
            continue
        salida.append({"clave": "mov:" + clave, "nombre": g["concepto"],
                       "monto_usd": round(g["total"] / len(previos), 2),
                       "grupo": _grupo_de_concepto(g["concepto"]),
                       "origen": "se repite en Finanzas, promedio de 3 meses"})
    return [f for f in salida if f["monto_usd"] > 0]


def _raiz(palabra: str) -> str:
    """La palabra sin el plural, para comparar nombres.

    "Sueldo Programadores" y la línea "Programador" de la lista son la misma
    persona. Comparando palabra por palabra no coincidían y el costo se contaba
    DOS veces: una en la lista y otra como movimiento que se repite.
    """
    for fin in ("es", "s"):
        if len(palabra) > 4 and palabra.endswith(fin):
            return palabra[:-len(fin)]
    return palabra


def _raices(texto) -> set:
    return {_raiz(p) for p in _palabras(texto)}


def _grupo_de_concepto(concepto) -> str:
    p = _palabras(concepto)
    if p & {"sueldo", "sueldos", "salario", "salarios", "jornal", "jornales"}:
        return "sueldos"
    if p & {"honorario", "honorarios", "comision", "comisiones"}:
        return "honorarios"
    return "fijos"


def costos_del_mes(db_path: str, mes: str) -> dict:
    """TODOS los costos del mes, en los tres grupos que pidió Juan.

    "Lo que hay que entender es que se deben poner todos los gastos, por un
    lado sueldos. Por el otro honorarios, por el otro los fijos."

    Cada línea trae su `clave` y si está prendida. Apagar una la saca del
    objetivo pero NO la esconde: se sigue viendo, tachada, como en el
    Simulador. El total son solo las prendidas.

    Los costos de un cliente de mantenimiento van aparte y no entran al
    objetivo, igual que antes; la pauta, los impuestos y los retiros también.
    """
    off = apagados(db_path)
    grupos = {g: [] for g in GRUPOS_COSTO}

    nombres_equipo = set()
    for f in listar_equipo(db_path):
        if f["tipo"] != "fijo":
            continue
        nombres_equipo |= _raices(f["nombre"])
        clave = f"equipo:{f['id']}"
        grupos[f["grupo"] if f["grupo"] in grupos else "sueldos"].append({
            "clave": clave, "id": f["id"], "nombre": f["nombre"],
            "unitario": round(float(f["monto_usd"]), 2), "cantidad": int(f["cantidad"]),
            "monto_usd": round(float(f["monto_usd"]) * int(f["cantidad"]), 2),
            "activo": clave not in off, "editable": True,
            "origen": "lo que cobra, escrito acá"})

    clases = clasificar_fijos(db_path, mes)
    for f in clases["estructura"]:
        if f["categoria"] in CATEGORIAS_FUERA_DE_ESTRUCTURA:
            continue
        clave = f"fijo:{f['id']}"
        # Un fijo que ya terminó se sigue viendo, pero arranca apagado: no se
        # paga más. Uno que arranca el mes que viene entra prendido, porque
        # Juan planifica con él, y dice desde cuándo.
        terminado = f.get("vigencia") == "terminado"
        grupos["fijos"].append({
            "clave": clave, "id": f["id"], "nombre": f["concepto"],
            "unitario": f["monto_usd"], "cantidad": 1, "monto_usd": f["monto_usd"],
            "activo": (clave not in off) and not terminado, "editable": False,
            "vigencia": f.get("vigencia") or "vigente",
            "origen": f.get("nota") or "gasto fijo de Finanzas"})

    for f in movimientos_que_se_repiten(db_path, mes, nombres_equipo):
        grupos[f["grupo"]].append({
            "clave": f["clave"], "id": None, "nombre": f["nombre"],
            "unitario": f["monto_usd"], "cantidad": 1, "monto_usd": f["monto_usd"],
            "activo": f["clave"] not in off, "editable": False, "origen": f["origen"]})

    salida, total = [], 0.0
    for clave in GRUPOS_COSTO:
        filas = sorted(grupos[clave], key=lambda f: -f["monto_usd"])
        prendido = round(sum(f["monto_usd"] for f in filas if f["activo"]), 2)
        total += prendido
        salida.append({"clave": clave, "rotulo": ETIQUETA_GRUPO[clave], "filas": filas,
                       "total": prendido,
                       "total_todo": round(sum(f["monto_usd"] for f in filas), 2)})
    return {"grupos": salida, "total": round(total, 2),
            "ingresos": ingresos_recurrentes(db_path, mes),
            "por_cliente": clases["cliente"],
            "por_cliente_total": round(sum(f["monto_usd"] for f in clases["cliente"]), 2),
            "fuera": [dict(f, motivo=ETIQUETA_FUERA.get(f["categoria"], ""))
                      for f in clases["estructura"]
                      if f["categoria"] in CATEGORIAS_FUERA_DE_ESTRUCTURA],
            "comision": equipo_comision(db_path)}


def base_estructural(db_path: str, mes: str, equipo_usd=None) -> dict:
    """Lo que hay que cubrir sí o sí este mes: estructura + equipo.

    No entran ni los costos de clientes de mantenimiento, ni la pauta, ni los
    impuestos, ni los retiros. Cada grupo se devuelve aparte para que se vea
    de dónde sale el número y se pueda discutir.
    """
    costos = costos_del_mes(db_path, mes)
    por_grupo = {g["clave"]: g for g in costos["grupos"]}
    # El equipo son sueldos + honorarios; la estructura, los fijos. Los tres
    # grupos ya cuentan SOLO lo prendido: apagar un costo mueve el objetivo.
    equipo_filas = por_grupo["sueldos"]["filas"] + por_grupo["honorarios"]["filas"]
    equipo_total = round(por_grupo["sueldos"]["total"] + por_grupo["honorarios"]["total"], 2)
    hay_lista = any(f.get("editable") for f in equipo_filas)
    equipo = {"total": equipo_total, "filas": equipo_filas, "sueltos": [],
              "comision": costos["comision"], "editado": False,
              "fuente": "lista" if hay_lista else "promedio",
              "es_promedio": not hay_lista}
    # Juan sabe el número exacto: si lo escribió a mano, vale el suyo.
    if equipo_usd is not None:
        equipo = dict(equipo, total=round(float(equipo_usd), 2), editado=True)
    estructura = por_grupo["fijos"]["filas"]
    recurrentes_total = por_grupo["fijos"]["total"]
    return {"recurrentes": estructura, "recurrentes_total": recurrentes_total,
            "equipo": equipo, "por_cliente": costos["por_cliente"],
            "por_cliente_total": costos["por_cliente_total"],
            "fuera": costos["fuera"], "costos": costos,
            "total": round(recurrentes_total + equipo["total"], 2)}


def fijos_confirmados(db_path: str, mes: str) -> dict:
    """Compatibilidad: el total de estructura (sin costos de cliente) y su detalle."""
    base = base_estructural(db_path, mes)
    return {"total": base["total"], "filas": base["recurrentes"] + base["equipo"]["filas"],
            "base": base}


# ── gastos esperados ─────────────────────────────────────────────────────────

def listar_esperados(db_path: str, mes: str, incluir_cerrados: bool = False) -> list[dict]:
    sql = "SELECT * FROM if_gastos_esperados WHERE periodo = ?"
    if not incluir_cerrados:
        sql += " AND estado = 'esperado'"
    return _q(db_path, sql + " ORDER BY monto_usd DESC, id", (mes,))


def validar_esperado(datos) -> tuple[dict | None, str | None]:
    if not isinstance(datos, dict):
        return None, "faltan los datos del gasto"
    concepto = str(datos.get("concepto") or "").strip()
    if not concepto:
        return None, "poné un concepto"
    if len(concepto) > CONCEPTO_MAX:
        return None, f"el concepto no puede pasar de {CONCEPTO_MAX} caracteres"
    monto = _numero(datos.get("monto_usd") if "monto_usd" in datos else datos.get("monto"))
    if monto is None or monto <= 0:
        return None, "el monto tiene que ser un número mayor que cero"
    if monto > MONTO_MAX:
        return None, "ese monto es demasiado grande"
    categoria = datos.get("categoria") or "variable"
    if categoria not in CATEGORIAS_ESPERADO:
        return None, "la categoría es fijo o variable"
    return {"concepto": concepto, "monto_usd": round(monto, 2), "categoria": categoria}, None


def crear_esperado(db_path: str, mes: str, datos, quien: str = "",
                   ahora: datetime | None = None) -> tuple[dict | None, str | None]:
    campos, error = validar_esperado(datos)
    if error:
        return None, error
    momento = _iso(ahora or _ahora())
    gid = _ejecutar(db_path,
                    "INSERT INTO if_gastos_esperados (periodo, concepto, monto_usd, categoria, "
                    "cargado_por, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (mes, campos["concepto"], campos["monto_usd"], campos["categoria"],
                     quien, momento))
    return {"id": gid, "periodo": mes, "estado": "esperado", **campos}, None


def borrar_esperado(db_path: str, gasto_id: int) -> str | None:
    filas = _q(db_path, "SELECT id FROM if_gastos_esperados WHERE id = ?", (gasto_id,))
    if not filas:
        return "ese gasto esperado no existe"
    _ejecutar(db_path, "DELETE FROM if_gastos_esperados WHERE id = ?", (gasto_id,))
    return None


def total_esperado(db_path: str, mes: str) -> float:
    return round(sum(float(g["monto_usd"] or 0) for g in listar_esperados(db_path, mes)), 2)


# ── emparejado con el movimiento real ────────────────────────────────────────

def _normalizar(texto) -> str:
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", str(texto or ""))
                         if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", sin_tildes.lower())


def _palabras(texto) -> set:
    return {p for p in _normalizar(texto).split() if p and p not in _VACIAS}


def parecido(a, b) -> float:
    """Cuánto se parecen dos conceptos, de 0 a 1 (palabras en común)."""
    pa, pb = _palabras(a), _palabras(b)
    if not pa or not pb:
        return 0.0
    return len(pa & pb) / min(len(pa), len(pb))


def _monto_entra(esperado: float, real: float) -> bool:
    return abs(float(esperado) - float(real)) <= max(float(esperado) * TOL_PCT, TOL_USD)


def candidatos_de(db_path: str, movimiento: dict) -> list[dict]:
    """Los gastos esperados que podrían ser ese movimiento, mejor primero.

    Solo egresos del mismo mes que todavía están esperando. Cada candidato dice
    por qué entró, para que la pantalla pueda mostrarlo tal cual.
    """
    if (movimiento.get("tipo") or "") != "egreso" or movimiento.get("anulado"):
        return []
    mes = (movimiento.get("periodo") or "")[:7]
    real = float(movimiento.get("monto_usd") or 0)
    if not mes or real <= 0:
        return []
    salida = []
    for g in listar_esperados(db_path, mes):
        monto_ok = _monto_entra(float(g["monto_usd"]), real)
        score = parecido(g["concepto"], movimiento.get("concepto"))
        if not monto_ok and score < PARECIDO_AUTO:
            continue
        salida.append({"esperado": g, "monto_coincide": monto_ok, "parecido": round(score, 2),
                       "seguro": bool(monto_ok and score >= PARECIDO_AUTO)})
    salida.sort(key=lambda c: (not c["seguro"], not c["monto_coincide"], -c["parecido"]))
    return salida


def confirmar_emparejado(db_path: str, gasto_id: int, movimiento_id: int,
                         ahora: datetime | None = None) -> str | None:
    filas = _q(db_path, "SELECT * FROM if_gastos_esperados WHERE id = ?", (gasto_id,))
    if not filas:
        return "ese gasto esperado no existe"
    if filas[0]["estado"] != "esperado":
        return "ese gasto esperado ya se cerró"
    if not _q(db_path, "SELECT id FROM finanzas_movimientos WHERE id = ?", (movimiento_id,)):
        return "ese movimiento no existe"
    _ejecutar(db_path,
              "UPDATE if_gastos_esperados SET estado = 'confirmado', movimiento_id = ?, "
              "emparejado_en = ? WHERE id = ?",
              (movimiento_id, _iso(ahora or _ahora()), gasto_id))
    return None


def descartar_esperado(db_path: str, gasto_id: int, ahora: datetime | None = None) -> str | None:
    """El gasto que al final no vino: sale del objetivo sin borrarse."""
    filas = _q(db_path, "SELECT * FROM if_gastos_esperados WHERE id = ?", (gasto_id,))
    if not filas:
        return "ese gasto esperado no existe"
    _ejecutar(db_path, "UPDATE if_gastos_esperados SET estado = 'descartado', emparejado_en = ? "
                       "WHERE id = ?", (_iso(ahora or _ahora()), gasto_id))
    return None


def emparejar_movimiento(db_path: str, movimiento_id: int,
                         ahora: datetime | None = None) -> dict:
    """Se llama cuando entra un egreso real. Empareja solo si no hay dudas.

    Devuelve `{"emparejado": id | None, "dudas": [...]}`. Con un solo candidato
    seguro (monto dentro de la tolerancia Y concepto parecido) se cierra el
    esperado. Con dos candidatos seguros, o con uno que coincide a medias,
    no se toca nada: queda en `dudas` y la pantalla se lo pregunta a Juan.
    """
    filas = _q(db_path, "SELECT * FROM finanzas_movimientos WHERE id = ?", (movimiento_id,))
    if not filas:
        return {"emparejado": None, "dudas": []}
    candidatos = candidatos_de(db_path, filas[0])
    seguros = [c for c in candidatos if c["seguro"]]
    if len(seguros) == 1:
        error = confirmar_emparejado(db_path, seguros[0]["esperado"]["id"], movimiento_id, ahora)
        if not error:
            return {"emparejado": seguros[0]["esperado"]["id"], "dudas": []}
    return {"emparejado": None,
            "dudas": [{"movimiento_id": movimiento_id,
                       "movimiento_concepto": filas[0]["concepto"],
                       "movimiento_monto_usd": round(float(filas[0]["monto_usd"] or 0), 2),
                       "gasto_id": c["esperado"]["id"],
                       "gasto_concepto": c["esperado"]["concepto"],
                       "gasto_monto_usd": c["esperado"]["monto_usd"],
                       "monto_coincide": c["monto_coincide"], "parecido": c["parecido"]}
                      for c in candidatos]}


def emparejar_pendientes(db_path: str, mes: str, ahora: datetime | None = None) -> int:
    """Cierra todos los esperados que tengan UN movimiento real sin dudas.

    Corre al abrir la pantalla, y no solo cuando alguien carga un gasto a mano,
    porque un movimiento puede aparecer por otros caminos (la materialización de
    un fijo, el cobro de un pendiente). Lo dudoso no se toca: queda para
    `dudas_del_mes`. Devuelve cuántos se emparejaron.
    """
    usados = {g["movimiento_id"] for g in listar_esperados(db_path, mes, incluir_cerrados=True)
              if g["movimiento_id"]}
    cerrados = 0
    for m in listar_movimientos(db_path, desde=mes, hasta=mes, tipo="egreso"):
        if m["id"] in usados:
            continue
        seguros = [c for c in candidatos_de(db_path, m) if c["seguro"]]
        if len(seguros) != 1:
            continue
        if not confirmar_emparejado(db_path, seguros[0]["esperado"]["id"], m["id"], ahora):
            usados.add(m["id"])
            cerrados += 1
    return cerrados


def dudas_del_mes(db_path: str, mes: str) -> list[dict]:
    """Emparejados posibles que quedaron sin resolver, para preguntar en la pantalla.

    Se recalcula al leer en vez de guardarse: un esperado que se borra o un
    movimiento que se edita cambian la respuesta, y una tabla de pendientes
    quedaria mintiendo.
    """
    if not listar_esperados(db_path, mes):
        return []
    usados = {g["movimiento_id"] for g in listar_esperados(db_path, mes, incluir_cerrados=True)
              if g["movimiento_id"]}
    dudas = []
    for m in listar_movimientos(db_path, desde=mes, hasta=mes, tipo="egreso"):
        if m["id"] in usados:
            continue
        for c in candidatos_de(db_path, m):
            dudas.append({"movimiento_id": m["id"], "movimiento_concepto": m["concepto"],
                          "movimiento_monto_usd": round(float(m["monto_usd"] or 0), 2),
                          "gasto_id": c["esperado"]["id"],
                          "gasto_concepto": c["esperado"]["concepto"],
                          "gasto_monto_usd": c["esperado"]["monto_usd"],
                          "monto_coincide": c["monto_coincide"], "parecido": c["parecido"]})
    return dudas


# ── el objetivo ──────────────────────────────────────────────────────────────

def leer_fila(db_path: str, mes: str) -> dict:
    filas = _q(db_path, "SELECT * FROM if_objetivo WHERE periodo = ?", (mes,))
    return filas[0] if filas else {"periodo": mes, "fijos_usd": None, "equipo_usd": None,
                                   "aportes_usd": 0.0, "sueldo_usd": 0.0}


def guardar(db_path: str, mes: str, datos, quien: str = "",
            ahora: datetime | None = None) -> str | None:
    """Guarda las partes que vinieron.

    `fijos_usd` y `equipo_usd` en null vuelven a calcularse solos: el primero
    desde Finanzas, el segundo desde el promedio de los movimientos.
    """
    if not isinstance(datos, dict) or not datos:
        return "faltan los datos del objetivo"
    fila = leer_fila(db_path, mes)
    valores = {"fijos_usd": fila["fijos_usd"], "equipo_usd": fila.get("equipo_usd"),
               "aportes_usd": fila["aportes_usd"] or 0.0,
               "sueldo_usd": fila["sueldo_usd"] or 0.0}
    etiquetas = {"fijos_usd": "los costos fijos", "equipo_usd": "el costo del equipo",
                 "aportes_usd": "los aportes externos", "sueldo_usd": "el sueldo objetivo"}
    automaticos = ("fijos_usd", "equipo_usd")
    for clave in valores:
        if clave not in datos:
            continue
        crudo = datos[clave]
        if clave in automaticos and crudo in (None, ""):
            valores[clave] = None      # que lo vuelva a calcular el sistema
            continue
        x = _numero(crudo)
        if x is None or x < 0:
            return f"{etiquetas[clave]} tiene que ser un número de cero para arriba"
        if x > MONTO_MAX:
            return f"{etiquetas[clave]}: ese monto es demasiado grande"
        valores[clave] = round(x, 2)
    _ejecutar(db_path,
              "INSERT INTO if_objetivo (periodo, fijos_usd, equipo_usd, aportes_usd, sueldo_usd, "
              "updated_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
              "ON CONFLICT(periodo) DO UPDATE SET "
              "fijos_usd = excluded.fijos_usd, equipo_usd = excluded.equipo_usd, "
              "aportes_usd = excluded.aportes_usd, sueldo_usd = excluded.sueldo_usd, "
              "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
              (mes, valores["fijos_usd"], valores["equipo_usd"], valores["aportes_usd"],
               valores["sueldo_usd"], quien, _iso(ahora or _ahora())))
    return None


def objetivo(db_path: str, mes: str) -> dict:
    """Las tres partes y el total, con de dónde sale cada una.

    Nunca falla ni queda vacío: sin nada cargado, los fijos son los de Finanzas
    (o cero) y las otras dos partes son cero. El total es un número siempre.
    """
    # Antes de sumar: cerrar los esperados que ya cayeron como movimiento real,
    # o el mes los cuenta dos veces.
    try:
        emparejar_pendientes(db_path, mes)
    except Exception:
        logger.warning("objetivo: falló el emparejado automático", exc_info=True)
    fila = leer_fila(db_path, mes)
    base = base_estructural(db_path, mes, fila.get("equipo_usd"))
    esperados = listar_esperados(db_path, mes)
    suma_esperados = round(sum(float(g["monto_usd"] or 0) for g in esperados), 2)
    automatico = round(base["total"] + suma_esperados, 2)

    editado = fila["fijos_usd"] is not None
    fijos = round(float(fila["fijos_usd"]), 2) if editado else automatico
    if editado:
        origen = "puesto a mano"
    else:
        partes_origen = []
        if base["equipo"]["total"]:
            partes_origen.append("equipo")
        if base["recurrentes_total"]:
            partes_origen.append("fijos")
        if suma_esperados:
            partes_origen.append("esperados")
        origen = " + ".join(partes_origen) or "editable acá"

    aportes = round(float(fila["aportes_usd"] or 0), 2)
    sueldo = round(float(fila["sueldo_usd"] or 0), 2)
    return {
        "periodo": mes,
        "partes": [
            {"clave": "fijos", "rotulo": "Fijos del mes", "monto": fijos,
             "origen": origen, "editado": editado, "automatico": automatico,
             "equipo": base["equipo"]["total"], "estructura": base["recurrentes_total"],
             "equipo_editado": bool(base["equipo"].get("editado")),
             "equipo_promedio": bool(base["equipo"].get("es_promedio")),
             "esperados": suma_esperados},
            {"clave": "aportes", "rotulo": "Aportes a reemplazar", "monto": aportes,
             "origen": "", "editado": aportes > 0},
            {"clave": "sueldo", "rotulo": "Tu sueldo", "monto": sueldo,
             "origen": "", "editado": sueldo > 0},
        ],
        "total": round(fijos + aportes + sueldo, 2),
        "base": base,
        "fijos_confirmados": base["recurrentes"] + base["equipo"]["filas"],
        "por_cliente": base["por_cliente"],
        "fuera": base["fuera"],
        "esperados": esperados,
        "dudas": dudas_del_mes(db_path, mes),
    }
