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

from database import _connect, listar_movimientos, listar_recurrentes
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


def _recurrentes_egreso(db_path: str, mes: str) -> list[tuple[dict, float]]:
    salida = []
    for r in listar_recurrentes(db_path, solo_activos=True):
        if r["tipo"] != "egreso":
            continue
        if (r["hasta"] and r["hasta"] < mes) or (r["desde"] and r["desde"] > mes):
            continue
        try:
            salida.append((r, round(a_usd(r["monto"], r["moneda"], r["tipo_cambio"]), 2)))
        except (TypeError, ValueError):
            # Un fijo en pesos sin tipo de cambio es un dato roto de Finanzas,
            # no un cero: se saltea y se sigue, no tira abajo el objetivo.
            logger.warning("objetivo: fijo %s sin tipo de cambio usable", r["id"])
    return salida


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
    for r, monto in _recurrentes_egreso(db_path, mes):
        fila = {"id": r["id"], "concepto": r["concepto"], "categoria": r["categoria"],
                "monto_usd": monto, "estado": "confirmado", "sugerido": False}
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


def costo_equipo(db_path: str, mes: str) -> dict:
    """El costo del equipo por mes, sacado de los movimientos reales.

    Los sueldos no viven en la pestaña de Fijos: se cargan mes a mes como
    movimientos sueltos, casi siempre en "servicios", con el nombre de la
    persona en el concepto ("Sueldo Juan programador", "Honorarios Mati"). Son
    el gasto fijo más grande que tiene la empresa: si el objetivo no los cuenta,
    queda por el piso.

    Se promedia sobre los 3 meses completos anteriores y se cuenta solo lo que
    se repite (aparece en 2 de esos 3 meses). Un pago que salió una sola vez se
    informa aparte y no suma: no es un sueldo.
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
            "meses": len(previos), "desde": previos[0], "hasta": previos[-1]}


def base_estructural(db_path: str, mes: str, equipo_usd=None) -> dict:
    """Lo que hay que cubrir sí o sí este mes: estructura + equipo.

    No entran ni los costos de clientes de mantenimiento, ni la pauta, ni los
    impuestos, ni los retiros. Cada grupo se devuelve aparte para que se vea
    de dónde sale el número y se pueda discutir.
    """
    clases = clasificar_fijos(db_path, mes)
    estructura = [f for f in clases["estructura"]
                  if f["categoria"] not in CATEGORIAS_FUERA_DE_ESTRUCTURA]
    fuera = [dict(f, motivo=ETIQUETA_FUERA.get(f["categoria"], ""))
             for f in clases["estructura"] if f["categoria"] in CATEGORIAS_FUERA_DE_ESTRUCTURA]
    equipo = costo_equipo(db_path, mes)
    # El promedio de los movimientos es una aproximación: Juan sabe el sueldo
    # exacto de cada uno. Si lo escribió, vale el suyo y no se recalcula.
    if equipo_usd is not None:
        equipo = dict(equipo, total=round(float(equipo_usd), 2), editado=True)
    else:
        equipo = dict(equipo, editado=False)
    recurrentes_total = round(sum(f["monto_usd"] for f in estructura), 2)
    return {"recurrentes": estructura, "recurrentes_total": recurrentes_total,
            "equipo": equipo, "por_cliente": clases["cliente"],
            "por_cliente_total": round(sum(f["monto_usd"] for f in clases["cliente"]), 2),
            "fuera": fuera, "total": round(recurrentes_total + equipo["total"], 2)}


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
