"""Agente de pauta: maneja la pauta de Meta dentro de un tope; el de marketing controla.

Pedido de Juan (30/9/2026): se dan vuelta los roles. El agente ejecuta (pausa lo
que no trae leads, baja presupuesto de lo caro, sube las piezas que le mandan) y
Andres controla desde el panel `pauta`: ve cada cambio con el motivo, lo puede
deshacer, puede frenar al agente y le sube contenido. **Nadie salvo un
administrador sube el gasto**: subir presupuesto queda pendiente del OK de Juan.

**Dos llaves, las dos apagadas por defecto** (Juan: "no lo largues hasta que yo
te de el okey, lo tengo que hablar con el de marketing"):

- `PAUTA_AGENTE=on` prende el hilo (supervisor cada hora, operador una vez por dia).
- `PAUTA_ESCRITURA=on` deja que escriba en Meta. Sin ella todo queda en
  **ensayo**: se registra lo que haria y en Meta no se toca nada.

Ademas el panel no se reparte a ningun rol hasta el lanzamiento (ver database.py).

Cada cambio guarda el antes y el despues de cada campo que toca
(`cambios_json`), asi deshacer es volver a escribir el "antes".

Reglas: las de `sombra_meta.recomendar` (las mismas que venian midiendo en modo
sombra), con dos frenos para no pelearse con una persona: un objeto que el
agente ya toco no se vuelve a tocar por 7 dias, y uno donde alguien deshizo lo
del agente, por 14.
"""

import json
import logging
import os
import threading
import time
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone

from database import _connect
from services import alertas_meta as am
from services import sombra_meta as sm
from services.corridas import marcar_corrida, puede_correr

logger = logging.getLogger(__name__)

_UY = am._UY
_JOB_OPERADOR = "pauta_operador"
_JOB_SUPERVISOR = "pauta_supervisor"
_JOB_MANUAL = "pauta_manual"
HORA_OPERADOR = 10
NO_REPETIR_DIAS = 7
RESPETAR_DESHECHO_DIAS = 14
BAJA = 0.8
SUBA = 1.2
PRESUPUESTO_MINIMO_CENTAVOS = 500      # USD 5 por dia: por debajo, mejor pausar
AVISO_TOPE = 0.8
MOVER_MAXIMO = 1.5                     # a la buena se le suma como mucho un 50% de lo que tenia

MAX_IMAGEN = 30 * 1024 * 1024
MAX_VIDEO = 100 * 1024 * 1024
EXTENSIONES = {".jpg": "imagen", ".jpeg": "imagen", ".png": "imagen",
               ".mp4": "video", ".mov": "video"}

TIPOS = {
    "pausar": "Pausar anuncio",
    "bajar": "Bajar presupuesto",
    "escalar": "Subir presupuesto",
    "mover": "Pasar plata a lo que anda bien",
    "pedir_pieza": "Hace falta una pieza nueva",
    "subir_pieza": "Subir pieza nueva",
    "tope": "Tope del mes alcanzado",
}
# Deshacer estas sube el gasto: solo un administrador.
SOLO_ADMIN_DESHACE = ("tope",)


class NoSePuede(ValueError):
    pass


def activo() -> bool:
    return os.environ.get("PAUTA_AGENTE", "").strip().lower() == "on"


def escritura() -> bool:
    return os.environ.get("PAUTA_ESCRITURA", "").strip().lower() == "on"


def _txt(dt: datetime) -> str:
    return dt.astimezone(_UY).strftime("%Y-%m-%d %H:%M")


def _ahora(ahora: datetime | None = None) -> datetime:
    return (ahora or datetime.now(timezone.utc)).astimezone(_UY)


# ── ajustes (viven en sombra_ajustes, con prefijo pauta_) ────────────────────

def _leer(db_path: str, clave: str) -> dict | None:
    conn = _connect(db_path)
    try:
        f = conn.execute("SELECT * FROM sombra_ajustes WHERE clave = ?", (clave,)).fetchone()
        return dict(f) if f else None
    finally:
        conn.close()


def _fijar(db_path: str, clave: str, valor: str | None, usuario: str,
           ahora: datetime | None = None) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("INSERT INTO sombra_ajustes (clave, valor, actualizado_por, actualizado_en) "
                     "VALUES (?, ?, ?, ?) ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor, "
                     "actualizado_por = excluded.actualizado_por, "
                     "actualizado_en = excluded.actualizado_en",
                     (clave, valor, usuario, _txt(_ahora(ahora))))
        conn.commit()
    finally:
        conn.close()


def tope_mes(db_path: str) -> float | None:
    f = _leer(db_path, "pauta_tope_mes")
    try:
        v = float(f["valor"]) if f and f["valor"] else None
    except ValueError:
        return None
    return v if v and v > 0 else None


def fijar_tope_mes(db_path: str, valor: float | None, usuario: str) -> None:
    _fijar(db_path, "pauta_tope_mes", None if valor is None else str(valor), usuario)


def estado(db_path: str) -> dict:
    f = _leer(db_path, "pauta_estado")
    if not f or f["valor"] != "frenado":
        return {"estado": "activo", "por": f and f["actualizado_por"], "en": f and f["actualizado_en"]}
    return {"estado": "frenado", "por": f["actualizado_por"], "en": f["actualizado_en"]}


def frenar(db_path: str, usuario: str) -> None:
    _fijar(db_path, "pauta_estado", "frenado", usuario)
    _avisar(f"{usuario} frenó al agente de pauta",
            f"{usuario} apretó «Frenar agente». El agente no toca nada en Meta hasta que "
            "alguien lo reactive desde el panel. Lo que ya hizo sigue como está.")


def reactivar(db_path: str, usuario: str) -> None:
    _fijar(db_path, "pauta_estado", "activo", usuario)


def resumen(db_path: str) -> dict:
    """Lo ultimo que leyo el supervisor (gasto del mes, campanas). Sin llamar a Meta."""
    f = _leer(db_path, "pauta_resumen")
    try:
        return json.loads(f["valor"]) if f and f["valor"] else {}
    except ValueError:
        return {}


# ── Meta ─────────────────────────────────────────────────────────────────────

def _post(ruta: str, archivos: dict | None = None, **params) -> dict:
    """Unica puerta de escritura a Meta. Se niega si PAUTA_ESCRITURA no esta en on."""
    if not escritura():
        raise RuntimeError("PAUTA_ESCRITURA apagada: el agente no escribe en Meta")
    import requests

    from meta_config import GRAPH

    params["access_token"] = os.environ["META_ADS_TOKEN"]
    r = requests.post(f"{GRAPH}/{ruta}", data=params, files=archivos, timeout=300)
    d = r.json() if r.content else {}
    if not r.ok or "error" in d:
        e = d.get("error") or {}
        raise am.ErrorDeMeta(f"code={e.get('code')} {e.get('message') or r.status_code}"[:300])
    return d


def _escribir(cambios: list[dict], cual: str, post=None) -> None:
    post = post or _post
    for c in cambios:
        post(c["id"], **{c["campo"]: c[cual]})


def traer_resumen(hoy: date) -> dict:
    cuenta = os.environ["META_AD_ACCOUNT_ID"]
    mes = am._insights("account", hoy.replace(day=1), hoy, "spend,actions,account_currency")
    campanas = sm._todo(f"{cuenta}/campaigns", limit=500,
                        fields="id,name,objective,effective_status")
    return {"mes": mes[0] if mes else {}, "campanas": campanas}


# ── planificar ───────────────────────────────────────────────────────────────

def _cambios_presupuesto(datos: dict, campana_id: str, factor: float) -> list[dict]:
    """Presupuesto de la campana o, si vive en los conjuntos, de cada conjunto activo."""
    camp = sm._campana(datos, campana_id) or {}
    if camp.get("daily_budget"):
        objetos = [(campana_id, int(camp["daily_budget"]))]
    else:
        objetos = [(a["id"], int(a["daily_budget"])) for a in datos.get("conjuntos", [])
                   if a.get("campaign_id") == campana_id and a.get("effective_status") == "ACTIVE"
                   and a.get("daily_budget")]
    cambios = []
    for oid, antes in objetos:
        despues = max(int(round(antes * factor)), PRESUPUESTO_MINIMO_CENTAVOS)
        if despues != antes:
            cambios.append({"id": oid, "campo": "daily_budget", "antes": str(antes),
                            "despues": str(despues)})
    return cambios


def _usd(centavos) -> str:
    return am._plata(int(centavos) / 100)


def _gasto_proyectado(gasto_mes: float, hoy: date, extra_diario: float) -> float:
    dias_mes = monthrange(hoy.year, hoy.month)[1]
    ritmo = gasto_mes / max(hoy.day, 1)
    return ritmo * dias_mes + extra_diario * (dias_mes - hoy.day)


def _sumar(datos: dict, campana_id: str, centavos: int) -> list[dict]:
    """Reparte `centavos` diarios de mas entre los presupuestos de la campana, en proporcion."""
    base = _cambios_presupuesto(datos, campana_id, 2.0)     # solo para saber los objetos y el antes
    total = sum(int(c["antes"]) for c in base)
    if not total:
        return []
    cambios, resto = [], centavos
    for i, c in enumerate(base):
        extra = resto if i == len(base) - 1 else int(round(centavos * int(c["antes"]) / total))
        resto -= extra
        cambios.append({**c, "despues": str(int(c["antes"]) + extra)})
    return cambios


def _total(cambios: list[dict], cual: str) -> int:
    return sum(int(c[cual]) for c in cambios)


def planificar(datos: dict, cpl_tope: float | None, hoy: date,
               tope: float | None = None, gasto_mes: float = 0.0) -> list[dict]:
    """Lo que haria el agente. Si una campana anda mal y otra anda bien, la plata que
    se le saca a la mala se le pone a la buena (pedido de Juan, 30/9): el total por dia
    no sube, asi que lo hace solo. Subir el total, en cambio, espera el OK de Juan."""
    salida, bajar, escalar = [], [], []
    for r in sm.recomendar(datos, cpl_tope):
        base = {"clave": r["clave"], "objeto_id": r["objeto_id"], "objeto_nombre": r["objeto_nombre"],
                "campana_nombre": r.get("campana_nombre"), "motivo": r["evidencia"]}
        if r["tipo"] == "pausar":
            salida.append({**base, "tipo": "pausar", "necesita_ok": False,
                           "descripcion": f"Pausar «{r['objeto_nombre']}»",
                           "cambios": [{"id": r["objeto_id"], "campo": "status",
                                        "antes": "ACTIVE", "despues": "PAUSED"}]})
        elif r["tipo"] == "bajar":
            cambios = _cambios_presupuesto(datos, r["objeto_id"], BAJA)
            if cambios:
                bajar.append((r, base, cambios))
        elif r["tipo"] == "escalar":
            cambios = _cambios_presupuesto(datos, r["objeto_id"], SUBA)
            if cambios:
                escalar.append((r, base, cambios))
        elif r["tipo"] == "renovar":
            salida.append({**base, "tipo": "pedir_pieza", "necesita_ok": False, "cambios": [],
                           "descripcion": f"«{r['objeto_nombre']}» está gastado: conviene subir "
                                          "una pieza nueva para esa campaña"})

    # Mover: la que mas plata libera va a la mas barata. Una accion con los dos lados,
    # asi deshacer vuelve las dos campanas a como estaban y el total nunca sube.
    bajar.sort(key=lambda x: _total(x[2], "antes") - _total(x[2], "despues"), reverse=True)
    escalar.sort(key=lambda x: x[0]["antes"].get("cpl") or 1e9)
    while bajar and escalar:
        rb, bb, cb = bajar.pop(0)
        re_, be, _ = escalar.pop(0)
        libera = _total(cb, "antes") - _total(cb, "despues")
        actual = _total(_cambios_presupuesto(datos, re_["objeto_id"], 2.0), "antes")
        mueve = min(libera, int(actual * (MOVER_MAXIMO - 1)))
        suma = _sumar(datos, re_["objeto_id"], mueve)
        if not suma:
            bajar.insert(0, (rb, bb, cb))
            continue
        salida.append({
            **be, "tipo": "mover", "necesita_ok": False, "cambios": cb + suma,
            "clave": f"mover:{rb['objeto_id']}:{re_['objeto_id']}",
            "descripcion": f"Pasar {_usd(mueve)} por día de «{rb['objeto_nombre']}» a "
                           f"«{re_['objeto_nombre']}»",
            "motivo": f"«{rb['objeto_nombre']}» anda mal: {rb['evidencia'].split('. Bajar')[0]}. "
                      f"«{re_['objeto_nombre']}» anda bien: {re_['evidencia'].split('. Subir')[0]}. "
                      "El gasto total por día no sube."})

    for r, base, cambios in bajar:
        salida.append({**base, "tipo": "bajar", "necesita_ok": False, "cambios": cambios,
                       "descripcion": f"Bajar el presupuesto de «{r['objeto_nombre']}» de "
                                      f"{_usd(_total(cambios, 'antes'))} a "
                                      f"{_usd(_total(cambios, 'despues'))} por día"})
    for r, base, cambios in escalar:
        extra = (_total(cambios, "despues") - _total(cambios, "antes")) / 100
        if tope and _gasto_proyectado(gasto_mes, hoy, extra) > tope:
            continue        # subir haria pasar el tope del mes: ni se propone
        salida.append({**base, "tipo": "escalar", "necesita_ok": True, "cambios": cambios,
                       "descripcion": f"Subir el presupuesto de «{r['objeto_nombre']}» de "
                                      f"{_usd(_total(cambios, 'antes'))} a "
                                      f"{_usd(_total(cambios, 'despues'))} por día"})
    return salida


# ── registro ─────────────────────────────────────────────────────────────────

def _fila(f) -> dict:
    d = dict(f)
    d["cambios"] = json.loads(d.pop("cambios_json") or "[]")
    d["tipo_texto"] = TIPOS.get(d["tipo"], d["tipo"])
    return d


def _tocado_hace_poco(conn, objeto_id: str, ahora: datetime) -> bool:
    desde = _txt(ahora - timedelta(days=NO_REPETIR_DIAS))
    desde_deshecho = _txt(ahora - timedelta(days=RESPETAR_DESHECHO_DIAS))
    return conn.execute(
        "SELECT 1 FROM pauta_acciones WHERE (objeto_id = ? OR cambios_json LIKE ?) AND "
        "((estado = 'deshecha' AND creada_en >= ?) OR (estado != 'deshecha' AND creada_en >= ?)) "
        "LIMIT 1", (objeto_id, f'%"id": "{objeto_id}"%', desde_deshecho, desde)).fetchone() is not None


def registrar(db_path: str, a: dict, ahora: datetime, post=None) -> dict | None:
    """Guarda la accion y, si corresponde, la aplica. None si se salteo."""
    conn = _connect(db_path)
    try:
        ids = {a["objeto_id"], *(c["id"] for c in a["cambios"])}
        if a["tipo"] != "tope" and any(_tocado_hace_poco(conn, i, ahora) for i in ids):
            return None
        if a["tipo"] == "pedir_pieza" or not a["cambios"]:
            est = "aviso"
        elif a.get("necesita_ok"):
            est = "pendiente_ok"
        elif not escritura():
            est = "ensayo"
        else:
            est = "aplicando"
        cur = conn.execute(
            "INSERT INTO pauta_acciones (creada_en, tipo, clave, objeto_id, objeto_nombre, "
            "campana_nombre, descripcion, motivo, cambios_json, estado) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (_txt(ahora), a["tipo"], a["clave"], a["objeto_id"], a.get("objeto_nombre"),
             a.get("campana_nombre"), a["descripcion"], a["motivo"], json.dumps(a["cambios"]), est))
        conn.commit()
        acc_id = cur.lastrowid
    finally:
        conn.close()
    if est == "aplicando":
        try:
            _aplicar(db_path, acc_id, "despues", "aplicada", post=post)
        except NoSePuede:
            pass        # queda en estado "error" con el detalle, a la vista en el panel
    elif est == "pendiente_ok":
        _avisar("El agente de pauta pide tu OK",
                f"{a['descripcion']}. Motivo: {a['motivo']} Entrá al panel Agente de pauta "
                "para aprobarlo o rechazarlo.")
    return obtener(db_path, acc_id)


def _aplicar(db_path: str, acc_id: int, cual: str, estado_ok: str, usuario: str = "",
             post=None) -> dict:
    acc = obtener(db_path, acc_id)
    error = None
    try:
        _escribir(acc["cambios"], cual, post)
    except Exception as e:
        error = str(e)[:300]
        logger.warning(f"Agente de pauta: no se pudo aplicar la acción {acc_id} ({e})")
    conn = _connect(db_path)
    try:
        if error:
            conn.execute("UPDATE pauta_acciones SET error = ?, estado = CASE WHEN estado = "
                         "'aplicando' THEN 'error' ELSE estado END WHERE id = ?", (error, acc_id))
        elif cual == "antes":
            conn.execute("UPDATE pauta_acciones SET estado = ?, resuelta_por = ?, resuelta_en = ?, "
                         "error = NULL WHERE id = ?", (estado_ok, usuario, _txt(_ahora()), acc_id))
        else:
            conn.execute("UPDATE pauta_acciones SET estado = ?, aplicada_en = ?, error = NULL, "
                         "resuelta_por = COALESCE(NULLIF(?, ''), resuelta_por) WHERE id = ?",
                         (estado_ok, _txt(_ahora()), usuario, acc_id))
        conn.commit()
    finally:
        conn.close()
    if error:
        raise NoSePuede(f"Meta no aceptó el cambio: {error}")
    return obtener(db_path, acc_id)


def obtener(db_path: str, acc_id: int) -> dict | None:
    conn = _connect(db_path)
    try:
        f = conn.execute("SELECT * FROM pauta_acciones WHERE id = ?", (acc_id,)).fetchone()
    finally:
        conn.close()
    return _fila(f) if f else None


def listar(db_path: str, limite: int = 40) -> list[dict]:
    conn = _connect(db_path)
    try:
        filas = conn.execute("SELECT * FROM pauta_acciones ORDER BY "
                             "CASE estado WHEN 'pendiente_ok' THEN 0 ELSE 1 END, id DESC LIMIT ?",
                             (limite,)).fetchall()
    finally:
        conn.close()
    return [_fila(f) for f in filas]


def deshacer(db_path: str, acc_id: int, usuario: str, es_admin: bool, post=None) -> dict:
    acc = obtener(db_path, acc_id)
    if not acc:
        raise NoSePuede("No existe ese cambio")
    if acc["tipo"] in SOLO_ADMIN_DESHACE and not es_admin:
        raise NoSePuede("Deshacer esto vuelve a prender gasto: lo tiene que hacer un administrador")
    if acc["estado"] == "ensayo":
        # En ensayo no se toco Meta: deshacer solo lo marca, y el agente lo respeta igual.
        conn = _connect(db_path)
        try:
            conn.execute("UPDATE pauta_acciones SET estado = 'deshecha', resuelta_por = ?, "
                         "resuelta_en = ? WHERE id = ?", (usuario, _txt(_ahora()), acc_id))
            conn.commit()
        finally:
            conn.close()
        return obtener(db_path, acc_id)
    if acc["estado"] != "aplicada":
        raise NoSePuede("Solo se puede deshacer un cambio que ya se hizo")
    return _aplicar(db_path, acc_id, "antes", "deshecha", usuario, post)


def aprobar(db_path: str, acc_id: int, usuario: str, post=None) -> dict:
    acc = obtener(db_path, acc_id)
    if not acc or acc["estado"] != "pendiente_ok":
        raise NoSePuede("Ese cambio no está esperando aprobación")
    if not escritura():
        conn = _connect(db_path)
        try:
            conn.execute("UPDATE pauta_acciones SET estado = 'ensayo', resuelta_por = ?, "
                         "aplicada_en = ? WHERE id = ?", (usuario, _txt(_ahora()), acc_id))
            conn.commit()
        finally:
            conn.close()
        return obtener(db_path, acc_id)
    return _aplicar(db_path, acc_id, "despues", "aplicada", usuario, post)


def rechazar(db_path: str, acc_id: int, usuario: str) -> dict:
    conn = _connect(db_path)
    try:
        n = conn.execute("UPDATE pauta_acciones SET estado = 'rechazada', resuelta_por = ?, "
                         "resuelta_en = ? WHERE id = ? AND estado = 'pendiente_ok'",
                         (usuario, _txt(_ahora()), acc_id)).rowcount
        conn.commit()
    finally:
        conn.close()
    if not n:
        raise NoSePuede("Ese cambio no está esperando aprobación")
    return obtener(db_path, acc_id)


# ── piezas que sube el de marketing ──────────────────────────────────────────

def _dir_piezas(db_path: str, pieza_id: int) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "pauta", str(pieza_id))


def ruta_pieza(db_path: str, pieza: dict) -> str:
    return os.path.join(_dir_piezas(db_path, pieza["id"]), "original" + pieza["extension"])


def guardar_pieza(db_path: str, nombre_archivo: str, datos: bytes, texto: str,
                  campana_id: str, usuario: str, ahora: datetime | None = None) -> int:
    ext = os.path.splitext(nombre_archivo or "")[1].lower()
    tipo = EXTENSIONES.get(ext)
    if not tipo:
        raise NoSePuede("Tiene que ser una imagen (JPG o PNG) o un video (MP4 o MOV).")
    maximo = MAX_IMAGEN if tipo == "imagen" else MAX_VIDEO
    if not datos:
        raise NoSePuede("El archivo está vacío.")
    if len(datos) > maximo:
        raise NoSePuede(f"El archivo pasa el máximo de {maximo // (1024 * 1024)} MB.")
    texto = (texto or "").strip()
    if not texto:
        raise NoSePuede("Falta el texto del anuncio.")
    if len(texto) > 2000:
        raise NoSePuede("El texto es muy largo (máximo 2000 caracteres).")
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO pauta_piezas (subida_por, subida_en, nombre_archivo, extension, tipo, "
            "texto, campana_id, estado) VALUES (?,?,?,?,?,?,?, 'recibida')",
            (usuario, _txt(_ahora(ahora)), nombre_archivo[:200], ext, tipo, texto,
             (campana_id or "").strip() or None))
        pieza_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    os.makedirs(_dir_piezas(db_path, pieza_id), exist_ok=True)
    with open(ruta_pieza(db_path, {"id": pieza_id, "extension": ext}), "wb") as fh:
        fh.write(datos)
    return pieza_id


def listar_piezas(db_path: str, limite: int = 30) -> list[dict]:
    conn = _connect(db_path)
    try:
        filas = conn.execute("SELECT * FROM pauta_piezas ORDER BY id DESC LIMIT ?",
                             (limite,)).fetchall()
    finally:
        conn.close()
    salida = []
    for f in filas:
        d = dict(f)
        gasto, leads = d.get("gasto") or 0, d.get("leads") or 0
        d["cpl"] = round(gasto / leads, 2) if leads else None
        salida.append(d)
    return salida


def obtener_pieza(db_path: str, pieza_id: int) -> dict | None:
    conn = _connect(db_path)
    try:
        f = conn.execute("SELECT * FROM pauta_piezas WHERE id = ?", (pieza_id,)).fetchone()
    finally:
        conn.close()
    return dict(f) if f else None


def _actualizar_pieza(db_path: str, pieza_id: int, **campos) -> None:
    conn = _connect(db_path)
    try:
        sets = ", ".join(f"{k} = ?" for k in campos)
        conn.execute(f"UPDATE pauta_piezas SET {sets} WHERE id = ?", (*campos.values(), pieza_id))
        conn.commit()
    finally:
        conn.close()


def _campana_destino(datos: dict, pieza: dict) -> str | None:
    """La que eligio el de marketing o, si no eligio, la de leads con mejor costo por lead."""
    activas = {c["id"] for c in datos.get("campanas", []) if c.get("effective_status") == "ACTIVE"
               and c.get("objective") in sm.OBJETIVOS_DE_LEADS}
    if pieza.get("campana_id"):
        return pieza["campana_id"] if pieza["campana_id"] in activas else None
    mejores = sorted((am.resumir(f)["cpl"] or 1e9, f["campaign_id"]) for f in datos.get("campanas_7d", [])
                     if f.get("campaign_id") in activas)
    return mejores[0][1] if mejores else (sorted(activas)[0] if activas else None)


def _anuncio_modelo(datos: dict, campana_id: str) -> str | None:
    activos = [a for a in datos.get("anuncios", [])
               if a.get("campaign_id") == campana_id and a.get("effective_status") == "ACTIVE"]
    if not activos:
        return None
    leads = {f.get("ad_id"): am.resumir(f)["leads"] for f in datos.get("anuncios_7d", [])}
    return max(activos, key=lambda a: leads.get(a["id"], 0))["id"]


def subir_a_meta(db_path: str, pieza: dict, campana_id: str, modelo_id: str,
                 get=None, post=None) -> str:
    """Crea el anuncio copiando el formato (formulario, boton, pagina) del mejor anuncio
    activo de la campana y cambiando la imagen y el texto. Devuelve el id del anuncio."""
    import copy

    get = get or am._get
    post = post or _post
    cuenta = os.environ["META_AD_ACCOUNT_ID"]
    modelo = get(modelo_id, fields="adset_id,creative{object_story_spec}")
    spec = copy.deepcopy(((modelo.get("creative") or {}).get("object_story_spec")) or {})
    link = spec.get("link_data")
    if not link or link.get("child_attachments"):
        raise NoSePuede("El anuncio modelo de esa campaña no es de una sola imagen: "
                        "esta pieza hay que subirla a mano.")
    with open(ruta_pieza(db_path, pieza), "rb") as fh:
        subida = post(f"{cuenta}/adimages", archivos={"filename": (pieza["nombre_archivo"], fh)})
    imagen = next(iter((subida.get("images") or {}).values()), {})
    if not imagen.get("hash"):
        raise NoSePuede("Meta no devolvió la imagen subida.")
    link.pop("picture", None)
    link["image_hash"] = imagen["hash"]
    link["message"] = pieza["texto"]
    nombre = f"Pieza CRM #{pieza['id']} - {pieza['nombre_archivo']}"[:100]
    creativo = post(f"{cuenta}/adcreatives", name=nombre, object_story_spec=json.dumps(spec))
    anuncio = post(f"{cuenta}/ads", name=nombre, adset_id=modelo["adset_id"],
                   creative=json.dumps({"creative_id": creativo["id"]}), status="ACTIVE")
    return anuncio["id"]


def procesar_piezas(db_path: str, datos: dict, ahora: datetime, get=None, post=None) -> int:
    """Sube las recibidas (o registra en ensayo que las subiria). Devuelve cuantas movio."""
    movidas = 0
    for pieza in [p for p in listar_piezas(db_path, 200) if p["estado"] == "recibida"]:
        if pieza["tipo"] == "video":
            _actualizar_pieza(db_path, pieza["id"], estado="manual",
                              error="Los videos por ahora se suben a mano en Meta.")
            continue
        campana = _campana_destino(datos, pieza)
        modelo = campana and _anuncio_modelo(datos, campana)
        if not modelo:
            _actualizar_pieza(db_path, pieza["id"], estado="manual",
                              error="No hay una campaña de leads activa con anuncios para copiarle el formato.")
            continue
        nombre_camp = (sm._campana(datos, campana) or {}).get("name") or campana
        accion = {"tipo": "subir_pieza", "clave": f"pieza:{pieza['id']}",
                  "objeto_id": f"pieza:{pieza['id']}", "objeto_nombre": pieza["nombre_archivo"],
                  "campana_nombre": nombre_camp, "necesita_ok": False, "cambios": [],
                  "descripcion": f"Subir la pieza «{pieza['nombre_archivo']}» de "
                                 f"{pieza['subida_por'] or 'marketing'} a «{nombre_camp}»",
                  "motivo": "Pieza nueva que mandó el de marketing; queda en prueba y se pausa "
                            "sola si en 7 días gasta sin traer leads."}
        if not escritura():
            accion["cambios"] = [{"id": "(anuncio nuevo)", "campo": "status",
                                  "antes": "PAUSED", "despues": "ACTIVE"}]
            registrar(db_path, accion, ahora)
            _actualizar_pieza(db_path, pieza["id"], estado="ensayo", campana_id=campana)
            movidas += 1
            continue
        try:
            ad_id = subir_a_meta(db_path, pieza, campana, modelo, get=get, post=post)
        except Exception as e:
            _actualizar_pieza(db_path, pieza["id"], estado="error", error=str(e)[:300])
            continue
        accion["cambios"] = [{"id": ad_id, "campo": "status", "antes": "PAUSED", "despues": "ACTIVE"}]
        conn = _connect(db_path)
        try:
            conn.execute(
                "INSERT INTO pauta_acciones (creada_en, tipo, clave, objeto_id, objeto_nombre, "
                "campana_nombre, descripcion, motivo, cambios_json, estado, aplicada_en) "
                "VALUES (?,?,?,?,?,?,?,?,?, 'aplicada', ?)",
                (_txt(ahora), "subir_pieza", accion["clave"], ad_id, pieza["nombre_archivo"],
                 nombre_camp, accion["descripcion"], accion["motivo"],
                 json.dumps(accion["cambios"]), _txt(ahora)))
            conn.commit()
        finally:
            conn.close()
        _actualizar_pieza(db_path, pieza["id"], estado="en_prueba", ad_id=ad_id,
                          campana_id=campana, en_meta_desde=_txt(ahora), error=None)
        movidas += 1
    return movidas


def actualizar_resultados(db_path: str, datos: dict, ahora: datetime) -> None:
    """Resultados de las piezas subidas, con los numeros de los ultimos 7 dias por anuncio."""
    por_ad = {f.get("ad_id"): am.resumir(f) for f in datos.get("anuncios_7d", [])}
    estados = {a["id"]: a.get("effective_status") for a in datos.get("anuncios", [])}
    for p in listar_piezas(db_path, 200):
        if not p.get("ad_id"):
            continue
        r = por_ad.get(p["ad_id"])
        campos = {"resultados_en": _txt(ahora)}
        if r:
            campos.update(gasto=round(r["gasto"], 2), leads=r["leads"])
        if estados.get(p["ad_id"]) not in (None, "ACTIVE") and p["estado"] == "en_prueba":
            campos["estado"] = "pausada"
        _actualizar_pieza(db_path, p["id"], **campos)


# ── avisos ───────────────────────────────────────────────────────────────────

def _avisar(titulo: str, texto: str) -> None:
    try:
        from services.email_service import send_pauta_aviso
        send_pauta_aviso(titulo, texto)
    except Exception as e:
        logger.warning(f"Agente de pauta: no se pudo avisar por mail ({e})")


# ── corridas ─────────────────────────────────────────────────────────────────

def supervisor(db_path: str, ahora: datetime | None = None, traer=None, forzar: bool = False,
               post=None) -> dict:
    """Cada hora: lee el gasto del mes, lo guarda para el panel y hace cumplir el tope."""
    from services.meta_insights import hay_credenciales

    ahora = _ahora(ahora)
    if not forzar and not puede_correr(db_path, _JOB_SUPERVISOR, 1):
        return {"estado": "ya_corrio"}
    if traer is None:
        if not hay_credenciales():
            return {"estado": "sin_credenciales"}
        traer = traer_resumen
    hoy = ahora.date()
    try:
        crudo = traer(hoy)
    except Exception as e:
        logger.warning(f"Agente de pauta: no se pudo leer el gasto ({e})")
        return {"estado": "error", "error": str(e)[:200]}
    if not forzar:
        marcar_corrida(db_path, _JOB_SUPERVISOR)
    mes = am.resumir(crudo.get("mes") or {})
    campanas = crudo.get("campanas") or []
    datos = {"mes": hoy.strftime("%Y-%m"), "actualizado": _txt(ahora),
             "gasto_mes": round(mes["gasto"], 2), "leads_mes": mes["leads"],
             "cpl_mes": round(mes["cpl"], 2) if mes["cpl"] else None,
             "campanas": [{"id": c["id"], "nombre": c.get("name") or c["id"]} for c in campanas
                          if c.get("effective_status") == "ACTIVE"
                          and c.get("objective") in sm.OBJETIVOS_DE_LEADS]}
    _fijar(db_path, "pauta_resumen", json.dumps(datos), "supervisor", ahora)

    tope = tope_mes(db_path)
    if not tope:
        return {"estado": "ok", **datos}
    marca = hoy.strftime("%Y-%m")
    if mes["gasto"] >= tope:
        activas = [c for c in campanas if c.get("effective_status") == "ACTIVE"]
        conn = _connect(db_path)
        try:
            ya = conn.execute("SELECT 1 FROM pauta_acciones WHERE clave = ?",
                              (f"tope:{marca}",)).fetchone()
        finally:
            conn.close()
        if activas and not ya:
            registrar(db_path, {
                "tipo": "tope", "clave": f"tope:{marca}", "objeto_id": "cuenta",
                "objeto_nombre": "Toda la cuenta", "campana_nombre": None, "necesita_ok": False,
                "descripcion": f"Pausar las {len(activas)} campañas activas",
                "motivo": f"El gasto del mes llegó a {am._plata(mes['gasto'])}, el tope es "
                          f"{am._plata(tope)}.",
                "cambios": [{"id": c["id"], "campo": "status", "antes": "ACTIVE", "despues": "PAUSED"}
                            for c in activas]}, ahora, post=post)
            _avisar("La pauta llegó al tope del mes",
                    f"Se gastaron {am._plata(mes['gasto'])} de {am._plata(tope)}. El agente pausó "
                    "las campañas activas. Para seguir, subí el tope y deshacé el cambio en el panel.")
    elif mes["gasto"] >= AVISO_TOPE * tope and puede_correr(db_path, f"pauta_aviso_{marca}", 24 * 40):
        marcar_corrida(db_path, f"pauta_aviso_{marca}")
        _avisar("La pauta va por el 80% del tope",
                f"Se gastaron {am._plata(mes['gasto'])} de {am._plata(tope)} este mes.")
    return {"estado": "ok", **datos}


def operador(db_path: str, ahora: datetime | None = None, traer=None, forzar: bool = False,
             post=None, get=None) -> dict:
    """Una vez por dia: aplica las reglas, sube piezas y actualiza sus resultados."""
    from services.meta_insights import hay_credenciales

    ahora = _ahora(ahora)
    if estado(db_path)["estado"] == "frenado":
        return {"estado": "frenado"}
    if not forzar:
        if ahora.hour < HORA_OPERADOR:
            return {"estado": "no_es_hora"}
        if not puede_correr(db_path, _JOB_OPERADOR, 20):
            return {"estado": "ya_corrio"}
    if traer is None:
        if not hay_credenciales():
            return {"estado": "sin_credenciales"}
        traer = sm.traer_datos
    hoy = ahora.date()
    try:
        datos = traer(hoy)
    except Exception as e:
        logger.warning(f"Agente de pauta: no se pudieron leer los datos ({e})")
        return {"estado": "error", "error": str(e)[:200]}
    if not forzar:
        marcar_corrida(db_path, _JOB_OPERADOR)
    gasto_mes = resumen(db_path).get("gasto_mes") or 0.0
    hechas = [a for a in (registrar(db_path, a, ahora, post=post)
                          for a in planificar(datos, sm.tope_cpl(db_path), hoy,
                                              tope_mes(db_path), gasto_mes)) if a]
    actualizar_resultados(db_path, datos, ahora)
    piezas = procesar_piezas(db_path, datos, ahora, get=get, post=post)
    return {"estado": "ok", "acciones": len(hechas), "piezas": piezas,
            "ensayo": not escritura()}


def correr_ahora(db_path: str) -> dict:
    if not puede_correr(db_path, _JOB_MANUAL, 0.25):
        return {"estado": "esperar", "error": "Se corrió hace menos de 15 minutos."}
    marcar_corrida(db_path, _JOB_MANUAL)
    s = supervisor(db_path, forzar=True)
    if s["estado"] not in ("ok",):
        return s
    return operador(db_path, forzar=True)


def start_pauta_agente(app) -> None:
    if not activo():
        logger.info("Agente de pauta apagado (PAUTA_AGENTE no está en on)")
        return

    def _loop():
        time.sleep(420)
        while True:
            db = app.config["DB_PATH"]
            try:
                supervisor(db)
                operador(db)
            except Exception as e:
                logger.warning(f"Agente de pauta: {e}")
            time.sleep(1800)

    threading.Thread(target=_loop, daemon=True, name="pauta-agente").start()
    logger.info("Agente de pauta ACTIVO (%s)", "escribe en Meta" if escritura() else "ensayo")
