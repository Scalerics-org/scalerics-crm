"""Instagram: del banco de ideas a @scalerics_, con aprobacion de Juan.

Etapa 2 del agente de marketing. Gratis: no llama a ningun modelo.

1. Los jueves se arma la semana siguiente: 3 piezas de feed (lunes, miercoles y
   viernes 19:00) y 2 historias (martes y jueves 19:00), tomadas de `ig_banco`
   (semilla en `services/ig_banco_semilla.py`). Llega un mail a contacto@.
2. En el panel Instagram se revisan, se editan, se cambian por otra idea, se
   descartan o se aprueban.
3. Un hilo publica lo aprobado cuando llega su hora.

**Nada sale sin aprobacion.** Es pedido explicito de Juan. Solo se publica una
fila en `aprobada`, y ese estado solo lo pone una persona desde el panel.
Editar una aprobada la devuelve a borrador: lo que se aprobo es lo que sale.

**Las imagenes tienen que estar en una URL publica** para que Meta las baje. Se
sirven en `/pub/ig/<token>/<n>.jpg`, con un token al azar por publicacion, y
solo mientras la publicacion esta aprobada o publicandose.
"""

import json
import logging
import os
import secrets
import shutil
import threading
import time
from datetime import date, datetime, timedelta, timezone

from database import _connect
from services import ig_render
from services.corridas import marcar_corrida, puede_correr

logger = logging.getLogger(__name__)

_UY = timezone(timedelta(hours=-3))
HORA = 19
# (slot, dia de la semana desde el lunes, tipo)
SLOTS = (
    ("feed_lun", 0, "feed"),
    ("historia_mar", 1, "historia"),
    ("feed_mie", 2, "feed"),
    ("historia_jue", 3, "historia"),
    ("feed_vie", 4, "feed"),
)
# El primer feed de la semana es carrusel y el segundo imagen: la grilla alterna.
FORMATO_PREFERIDO = {"feed_lun": "carrusel", "feed_mie": "imagen", "feed_vie": None}

DIA_DE_ARMADO = 3          # jueves
HORA_DE_ARMADO = 10
NO_REPETIR_DIAS = 120
VENCE_A_LAS_HORAS = 6      # una aprobada que no salio a tiempo no sale tarde
TOPE_POR_VUELTA = 3
MINIMO_EN_BANCO = 10

ESTADOS_EDITABLES = ("borrador", "aprobada", "error", "vencida")
_JOB_SEMANA = "ig_semana"
_JOB_AVISO_BANCO = "ig_banco_bajo"


class NoSePuede(ValueError):
    """Error para mostrarle a la persona tal cual."""


def activo() -> bool:
    return os.environ.get("INSTAGRAM_AGENTE", "").strip().lower() != "off"


def ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


def _txt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _dt(txt: str) -> datetime:
    return datetime.strptime(txt, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)


def lunes_de(d: date) -> date:
    return d - timedelta(days=d.weekday())


def semana_actual(ahora: datetime) -> date:
    return lunes_de(ahora.astimezone(_UY).date())


def _dir_imagenes(db_path: str, pub_id: int) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "instagram", str(pub_id))


def ruta_imagen(db_path: str, pub: dict, n: int) -> str:
    return os.path.join(_dir_imagenes(db_path, pub["id"]), f"{pub['version']}-{n}.jpg")


# ── banco ────────────────────────────────────────────────────────────────────

def sembrar_banco(db_path: str) -> int:
    from services.ig_banco_semilla import BANCO

    conn = _connect(db_path)
    try:
        antes = conn.total_changes
        for item in BANCO:
            conn.execute(
                "INSERT OR IGNORE INTO ig_banco (clave, formato, pilar, slides_json, caption) "
                "VALUES (?,?,?,?,?)",
                (item["clave"], item["formato"], item["pilar"],
                 json.dumps(item["slides"], ensure_ascii=False), item["caption"]))
        conn.commit()
        return conn.total_changes - antes
    finally:
        conn.close()


def disponibles(conn, ahora: datetime) -> dict:
    corte = _txt(ahora - timedelta(days=NO_REPETIR_DIAS))
    filas = conn.execute(
        "SELECT formato, COUNT(*) n FROM ig_banco WHERE usado_en IS NULL OR usado_en < ? "
        "GROUP BY formato", (corte,)).fetchall()
    d = {r["formato"]: r["n"] for r in filas}
    return {"feed": d.get("imagen", 0) + d.get("carrusel", 0), "historia": d.get("historia", 0)}


def _elegir(conn, tipo: str, preferido: str | None, ahora: datetime, excluir: set):
    formatos = ("historia",) if tipo == "historia" else ("imagen", "carrusel")
    corte = _txt(ahora - timedelta(days=NO_REPETIR_DIAS))
    marcas = ",".join("?" * len(formatos))
    filas = conn.execute(
        f"SELECT * FROM ig_banco WHERE formato IN ({marcas}) "
        "AND (usado_en IS NULL OR usado_en < ?) ORDER BY usado_en IS NOT NULL, usado_en, id",
        (*formatos, corte)).fetchall()
    filas = [f for f in filas if f["clave"] not in excluir]
    if not filas:
        return None
    # Variedad: evitar el pilar de la ultima pieza del mismo tipo.
    ultimo = conn.execute(
        "SELECT pilar FROM ig_publicaciones WHERE formato IN (" + marcas + ") "
        "ORDER BY programada_para DESC LIMIT 1", formatos).fetchone()
    candidatas = [f for f in filas if not preferido or f["formato"] == preferido] or filas
    distintas = [f for f in candidatas if not ultimo or f["pilar"] != ultimo["pilar"]]
    return (distintas or candidatas)[0]


def _usar(conn, fila, ahora):
    conn.execute("UPDATE ig_banco SET usado_en = ? WHERE id = ?", (_txt(ahora), fila["id"]))


# ── publicaciones ────────────────────────────────────────────────────────────

def _fila_a_dict(fila) -> dict:
    d = dict(fila)
    d["slides"] = json.loads(d.pop("slides_json"))
    return d


def obtener(db_path: str, pub_id: int) -> dict | None:
    conn = _connect(db_path)
    try:
        fila = conn.execute("SELECT * FROM ig_publicaciones WHERE id = ?", (pub_id,)).fetchone()
        return _fila_a_dict(fila) if fila else None
    finally:
        conn.close()


def renderizar(db_path: str, pub_id: int) -> dict:
    """Dibuja las imagenes de la version nueva y borra las viejas."""
    pub = obtener(db_path, pub_id)
    estilos = ig_render.estilos_para(pub["formato"])
    estilo = pub["estilo"] if pub["estilo"] in estilos else estilos[0]
    imagenes = ig_render.dibujar_publicacion(pub["formato"], pub["slides"], estilo)
    version = pub["version"] + 1
    carpeta = _dir_imagenes(db_path, pub_id)
    if os.path.isdir(carpeta):
        shutil.rmtree(carpeta, ignore_errors=True)
    os.makedirs(carpeta, exist_ok=True)
    for n, contenido in enumerate(imagenes, 1):
        with open(os.path.join(carpeta, f"{version}-{n}.jpg"), "wb") as f:
            f.write(contenido)
    conn = _connect(db_path)
    try:
        conn.execute("UPDATE ig_publicaciones SET version = ?, imagenes = ?, estilo = ?, "
                     "actualizado_en = CURRENT_TIMESTAMP WHERE id = ?",
                     (version, len(imagenes), estilo, pub_id))
        conn.commit()
    finally:
        conn.close()
    return obtener(db_path, pub_id)


# ── plan de fondos ───────────────────────────────────────────────────────────
# Juan (17/9): "todo del mismo fondo es monotono; arma cada mes una
# planificacion de fondos y colores y variala". Cada mes toma una secuencia y
# la recorre pieza por pieza. Son de 5 y el feed sale de a 3 por semana, asi que
# el patron no arma columnas del mismo color en la grilla.

# Cada plan lleva un blanco: rompe la serie de oscuros sin salirse de la marca.
PLANES_FEED = (
    ("marco", "blanco", "azul_marco", "verde", "bruma_azul"),
    ("azul", "grafito_marco", "blanco_marco", "verde", "bruma"),
    ("grafito", "marco", "blanco", "azul", "bruma"),
    ("verde", "azul_marco", "grafito", "blanco_marco", "bruma_azul"),
    ("bruma", "blanco", "azul", "grafito_marco", "marco"),
    ("azul_marco", "verde", "bruma_azul", "blanco_marco", "grafito"),
)
PLANES_HISTORIA = (
    ("degradado", "bruma", "blanco"),
    ("verde", "degradado", "blanco"),
    ("degradado", "azul", "blanco"),
    ("grafito", "blanco", "degradado"),
)
MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre")


def plan_del_mes(anio: int, mes: int, tipo: str = "feed") -> tuple:
    planes = PLANES_HISTORIA if tipo == "historia" else PLANES_FEED
    return planes[(anio * 12 + mes) % len(planes)]


def _estilo_planificado(conn, tipo: str, cuando: datetime) -> str:
    local = cuando.astimezone(_UY)
    inicio = local.replace(day=1, hour=0, minute=0)
    fin = (inicio + timedelta(days=32)).replace(day=1)
    igual = "=" if tipo == "historia" else "!="
    n = conn.execute(
        f"SELECT COUNT(*) FROM ig_publicaciones WHERE formato {igual} 'historia' "
        "AND estado != 'descartada' AND programada_para >= ? AND programada_para < ?",
        (_txt(inicio), _txt(cuando))).fetchone()[0]
    plan = plan_del_mes(local.year, local.month, tipo)
    return plan[n % len(plan)]


def plan_para_panel(lunes: date) -> dict:
    return {
        "mes": MESES[lunes.month - 1],
        "feed": [ig_render.nombre_estilo(e) for e in plan_del_mes(lunes.year, lunes.month)],
        "historias": [ig_render.nombre_estilo(e)
                      for e in plan_del_mes(lunes.year, lunes.month, "historia")],
    }


_JOB_REPLAN = "ig_replan_fondos_v2"


def replanificar(db_path: str, ahora: datetime | None = None) -> int:
    """Aplica el plan de fondos a lo que todavia no salio y lo redibuja.

    Una aprobada vuelve a borrador: cambio como se ve y la aprobacion era por
    la version anterior.
    """
    ahora = ahora or ahora_utc()
    cambiadas = []
    conn = _connect(db_path)
    try:
        filas = conn.execute(
            "SELECT id, formato, estilo, programada_para FROM ig_publicaciones "
            "WHERE programada_para > ? AND estado IN ('borrador','aprobada','error','vencida') "
            "ORDER BY programada_para", (_txt(ahora),)).fetchall()
        for f in filas:
            tipo = "historia" if f["formato"] == "historia" else "feed"
            estilo = _estilo_planificado(conn, tipo, _dt(f["programada_para"]))
            if estilo == f["estilo"]:
                continue
            conn.execute(
                "UPDATE ig_publicaciones SET estilo = ?, estado = CASE WHEN estado = 'aprobada' "
                "THEN 'borrador' ELSE estado END, aprobada_por = NULL, aprobada_en = NULL, "
                "actualizado_en = CURRENT_TIMESTAMP WHERE id = ?", (estilo, f["id"]))
            cambiadas.append(f["id"])
        conn.commit()
    finally:
        conn.close()
    for pub_id in cambiadas:
        renderizar(db_path, pub_id)
    return len(cambiadas)


def armar_semana(db_path: str, lunes: date, ahora: datetime | None = None) -> list[int]:
    """Llena los slots vacios de esa semana. Idempotente."""
    ahora = ahora or ahora_utc()
    creadas = []
    conn = _connect(db_path)
    try:
        ocupados = {r["slot"] for r in conn.execute(
            "SELECT slot FROM ig_publicaciones WHERE semana = ?", (lunes.isoformat(),))}
        usadas = set()
        for slot, dia, tipo in SLOTS:
            if slot in ocupados:
                continue
            cuando = datetime.combine(lunes + timedelta(days=dia),
                                      datetime.min.time().replace(hour=HORA), _UY)
            if cuando <= ahora:
                continue
            fila = _elegir(conn, tipo, FORMATO_PREFERIDO.get(slot), ahora, usadas)
            if fila is None:
                logger.warning(f"Instagram: el banco no tiene ideas de {tipo}")
                continue
            usadas.add(fila["clave"])
            _usar(conn, fila, ahora)
            estilo = _estilo_planificado(conn, tipo, cuando)
            cur = conn.execute(
                "INSERT INTO ig_publicaciones (semana, slot, clave_banco, formato, pilar, "
                "slides_json, caption, estilo, programada_para, img_token) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (lunes.isoformat(), slot, fila["clave"], fila["formato"], fila["pilar"],
                 fila["slides_json"], fila["caption"], estilo, _txt(cuando),
                 secrets.token_hex(16)))
            creadas.append(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()
    for pub_id in creadas:
        renderizar(db_path, pub_id)
    return creadas


def listar_semana(db_path: str, lunes: date) -> list[dict]:
    conn = _connect(db_path)
    try:
        filas = conn.execute("SELECT * FROM ig_publicaciones WHERE semana = ? "
                             "ORDER BY programada_para", (lunes.isoformat(),)).fetchall()
        return [_fila_a_dict(f) for f in filas]
    finally:
        conn.close()


def _validar_slides(formato: str, slides) -> list[dict]:
    if not isinstance(slides, list) or not slides:
        raise NoSePuede("Falta el contenido de las imágenes")
    maximo = 10 if formato == "carrusel" else 1
    if len(slides) > maximo or (formato == "carrusel" and len(slides) < 2):
        raise NoSePuede("Un carrusel lleva de 2 a 10 imágenes" if formato == "carrusel"
                        else "Esta publicación lleva una sola imagen")
    limpias = []
    for s in slides:
        if not isinstance(s, dict) or not str(s.get("titulo") or "").strip():
            raise NoSePuede("Cada imagen necesita un título")
        limpia = {}
        for campo, tope in (("etiqueta", 40), ("titulo", 120), ("texto", 320), ("cta", 40)):
            valor = str(s.get(campo) or "").strip()
            if len(valor) > tope:
                raise NoSePuede(f"El campo {campo} es muy largo (máximo {tope} caracteres)")
            if valor:
                limpia[campo] = valor
        limpias.append(limpia)
    return limpias


def editar(db_path: str, pub_id: int, cambios: dict, usuario: str,
           ahora: datetime | None = None) -> dict:
    ahora = ahora or ahora_utc()
    pub = obtener(db_path, pub_id)
    if not pub:
        raise NoSePuede("No existe esa publicación")
    if pub["estado"] not in ESTADOS_EDITABLES:
        raise NoSePuede("Esta publicación ya no se puede editar")
    caption = cambios.get("caption", pub["caption"])
    if not isinstance(caption, str) or not caption.strip():
        raise NoSePuede("El texto de la publicación no puede quedar vacío")
    if len(caption) > 2200:
        raise NoSePuede("Instagram acepta hasta 2.200 caracteres en el texto")
    if caption.count("#") > 30:
        raise NoSePuede("Instagram acepta hasta 30 hashtags")
    slides = _validar_slides(pub["formato"], cambios.get("slides", pub["slides"]))
    estilo = cambios.get("estilo") or pub["estilo"]
    if estilo not in ig_render.estilos_para(pub["formato"]):
        raise NoSePuede("Ese estilo no está disponible para este formato")
    programada = pub["programada_para"]
    if cambios.get("fecha") or cambios.get("hora"):
        try:
            local = datetime.strptime(
                f"{cambios.get('fecha') or _dt(programada).astimezone(_UY).date().isoformat()} "
                f"{cambios.get('hora') or _dt(programada).astimezone(_UY).strftime('%H:%M')}",
                "%Y-%m-%d %H:%M").replace(tzinfo=_UY)
        except ValueError:
            raise NoSePuede("Fecha u hora inválida")
        if local <= ahora:
            raise NoSePuede("La fecha tiene que ser en el futuro")
        programada = _txt(local)

    cambio_contenido = (caption != pub["caption"] or slides != pub["slides"]
                        or estilo != pub["estilo"])
    cambio_algo = cambio_contenido or programada != pub["programada_para"]
    if not cambio_algo:
        return pub
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE ig_publicaciones SET caption = ?, slides_json = ?, estilo = ?, "
            "programada_para = ?, estado = 'borrador', error = NULL, aprobada_por = NULL, "
            "aprobada_en = NULL, editada_por = ?, actualizado_en = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (caption.strip(), json.dumps(slides, ensure_ascii=False), estilo, programada,
             usuario, pub_id))
        conn.commit()
    finally:
        conn.close()
    return renderizar(db_path, pub_id) if cambio_contenido else obtener(db_path, pub_id)


def aprobar(db_path: str, pub_id: int, usuario: str, ahora: datetime | None = None) -> dict:
    ahora = ahora or ahora_utc()
    pub = obtener(db_path, pub_id)
    if not pub or pub["estado"] not in ("borrador", "error", "vencida"):
        raise NoSePuede("Esta publicación no se puede aprobar")
    if _dt(pub["programada_para"]) <= ahora + timedelta(minutes=5):
        raise NoSePuede("La fecha de publicación ya pasó: elegí otra antes de aprobar")
    if not pub["imagenes"]:
        raise NoSePuede("La publicación todavía no tiene imágenes")
    conn = _connect(db_path)
    try:
        conn.execute("UPDATE ig_publicaciones SET estado = 'aprobada', error = NULL, "
                     "aprobada_por = ?, aprobada_en = ?, actualizado_en = CURRENT_TIMESTAMP "
                     "WHERE id = ? AND estado IN ('borrador','error','vencida')",
                     (usuario, _txt(ahora), pub_id))
        conn.commit()
    finally:
        conn.close()
    return obtener(db_path, pub_id)


def _cambiar_estado(db_path, pub_id, desde: tuple, hacia: str, error: str) -> dict:
    conn = _connect(db_path)
    try:
        marcas = ",".join("?" * len(desde))
        cur = conn.execute(
            f"UPDATE ig_publicaciones SET estado = ?, aprobada_por = NULL, aprobada_en = NULL, "
            f"actualizado_en = CURRENT_TIMESTAMP WHERE id = ? AND estado IN ({marcas})",
            (hacia, pub_id, *desde))
        conn.commit()
        if not cur.rowcount:
            raise NoSePuede(error)
    finally:
        conn.close()
    return obtener(db_path, pub_id)


def desaprobar(db_path: str, pub_id: int) -> dict:
    return _cambiar_estado(db_path, pub_id, ("aprobada",), "borrador",
                           "Esta publicación no está aprobada")


def descartar(db_path: str, pub_id: int) -> dict:
    return _cambiar_estado(db_path, pub_id, ESTADOS_EDITABLES, "descartada",
                           "Esta publicación ya no se puede descartar")


def restaurar(db_path: str, pub_id: int) -> dict:
    return _cambiar_estado(db_path, pub_id, ("descartada",), "borrador",
                           "Esta publicación no está descartada")


def otra_idea(db_path: str, pub_id: int, ahora: datetime | None = None) -> dict:
    """Cambia el contenido por otra idea del banco del mismo tipo."""
    ahora = ahora or ahora_utc()
    pub = obtener(db_path, pub_id)
    if not pub or pub["estado"] not in ESTADOS_EDITABLES + ("descartada",):
        raise NoSePuede("Esta publicación ya no se puede cambiar")
    tipo = "historia" if pub["formato"] == "historia" else "feed"
    conn = _connect(db_path)
    try:
        en_semana = {r["clave_banco"] for r in conn.execute(
            "SELECT clave_banco FROM ig_publicaciones WHERE semana = ?", (pub["semana"],))}
        fila = _elegir(conn, tipo, None, ahora, en_semana)
        if fila is None:
            raise NoSePuede("No quedan ideas nuevas en el banco. Pedile a Claude que escriba más.")
        _usar(conn, fila, ahora)
        conn.execute(
            "UPDATE ig_publicaciones SET clave_banco = ?, formato = ?, pilar = ?, "
            "slides_json = ?, caption = ?, estado = 'borrador', error = NULL, "
            "aprobada_por = NULL, aprobada_en = NULL, actualizado_en = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (fila["clave"], fila["formato"], fila["pilar"], fila["slides_json"],
             fila["caption"], pub_id))
        conn.commit()
    finally:
        conn.close()
    return renderizar(db_path, pub_id)


# ── pedidos de correccion ────────────────────────────────────────────────────
# Juan escribe en texto libre ("cambia el boton por Agenda tu demo"). No hay
# modelo pago: los resuelve una sesion de Claude Code programada, que lee los
# pendientes y guarda la version corregida por /api/instagram-bot/. El
# resultado queda en borrador: hay que volver a aprobarlo.

MAX_PEDIDO = 1000
AUTOR_BOT = "Claude"


def pedir_correccion(db_path: str, pub_id: int, pedido: str, usuario: str) -> dict:
    pedido = (pedido or "").strip()
    if not pedido:
        raise NoSePuede("Escribí qué querés cambiar")
    if len(pedido) > MAX_PEDIDO:
        raise NoSePuede(f"El pedido es muy largo (máximo {MAX_PEDIDO} caracteres)")
    pub = obtener(db_path, pub_id)
    if not pub or pub["estado"] not in ESTADOS_EDITABLES:
        raise NoSePuede("Esta publicación ya no se puede corregir")
    conn = _connect(db_path)
    try:
        abierta = conn.execute("SELECT 1 FROM ig_correcciones WHERE publicacion_id = ? "
                               "AND estado = 'pendiente'", (pub_id,)).fetchone()
        if abierta:
            raise NoSePuede("Ya hay un pedido en espera para esta publicación")
        conn.execute("INSERT INTO ig_correcciones (publicacion_id, pedido, pedido_por) "
                     "VALUES (?,?,?)", (pub_id, pedido, usuario))
        conn.commit()
    finally:
        conn.close()
    return pub


def correcciones_de(db_path: str, pub_ids: list[int]) -> dict:
    """{pub_id: [pedidos, el mas nuevo primero]} para el panel."""
    if not pub_ids:
        return {}
    conn = _connect(db_path)
    try:
        marcas = ",".join("?" * len(pub_ids))
        filas = conn.execute(
            f"SELECT id, publicacion_id, pedido, estado, respuesta, creado_en "
            f"FROM ig_correcciones WHERE publicacion_id IN ({marcas}) ORDER BY id DESC",
            pub_ids).fetchall()
    finally:
        conn.close()
    salida = {}
    for f in filas:
        salida.setdefault(f["publicacion_id"], []).append(dict(f))
    return salida


def pendientes(db_path: str) -> list[dict]:
    """Lo que tiene que resolver la sesion de Claude, con todo el contexto."""
    conn = _connect(db_path)
    try:
        filas = conn.execute(
            "SELECT c.id, c.pedido, c.creado_en, p.id AS publicacion_id, p.formato, p.estado, "
            "p.slides_json, p.caption, p.estilo FROM ig_correcciones c "
            "JOIN ig_publicaciones p ON p.id = c.publicacion_id "
            "WHERE c.estado = 'pendiente' ORDER BY c.id").fetchall()
    finally:
        conn.close()
    salida = []
    for f in filas:
        d = dict(f)
        d["slides"] = json.loads(d.pop("slides_json"))
        d["estilos"] = list(ig_render.estilos_para(d["formato"]))
        salida.append(d)
    return salida


def _cerrar_pedido(db_path, corr_id, estado, respuesta):
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE ig_correcciones SET estado = ?, respuesta = ?, resuelto_en = CURRENT_TIMESTAMP "
            "WHERE id = ? AND estado = 'pendiente'", (estado, (respuesta or "")[:500], corr_id))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def _pedido(db_path, corr_id):
    conn = _connect(db_path)
    try:
        return conn.execute("SELECT * FROM ig_correcciones WHERE id = ?", (corr_id,)).fetchone()
    finally:
        conn.close()


def resolver_correccion(db_path: str, corr_id: int, cambios: dict, respuesta: str) -> dict:
    fila = _pedido(db_path, corr_id)
    if not fila or fila["estado"] != "pendiente":
        raise NoSePuede("Ese pedido ya no está pendiente")
    permitidos = {k: cambios[k] for k in ("caption", "slides", "estilo") if k in cambios}
    if not permitidos:
        raise NoSePuede("No vino ningún cambio")
    pub = editar(db_path, fila["publicacion_id"], permitidos, AUTOR_BOT)
    _cerrar_pedido(db_path, corr_id, "hecha", respuesta or "Listo.")
    return pub


def rechazar_correccion(db_path: str, corr_id: int, respuesta: str) -> None:
    if not (respuesta or "").strip():
        raise NoSePuede("Hay que explicar por qué no se pudo")
    if not _cerrar_pedido(db_path, corr_id, "no_se_pudo", respuesta.strip()):
        raise NoSePuede("Ese pedido ya no está pendiente")


# ── publicar ─────────────────────────────────────────────────────────────────

def _post(ruta: str, **params) -> dict:
    import requests

    from meta_config import GRAPH

    params["access_token"] = os.environ["META_ADS_TOKEN"]
    r = requests.post(f"{GRAPH}/{ruta}", data=params, timeout=60)
    d = r.json() if r.content else {}
    if not r.ok or "error" in d:
        e = d.get("error") or {}
        raise RuntimeError(f"code={e.get('code')} {e.get('message') or r.status_code}"[:300])
    return d


def _get(ruta: str, **params) -> dict:
    import requests

    from meta_config import GRAPH

    params["access_token"] = os.environ["META_ADS_TOKEN"]
    r = requests.get(f"{GRAPH}/{ruta}", params=params, timeout=30)
    d = r.json()
    if not r.ok or "error" in d:
        e = d.get("error") or {}
        raise RuntimeError(f"code={e.get('code')} {e.get('message') or r.status_code}"[:300])
    return d


def _cuenta_ig() -> str:
    fija = os.environ.get("INSTAGRAM_USER_ID", "").strip()
    if fija:
        return fija
    d = _get(os.environ["META_PAGE_ID"], fields="instagram_business_account")
    return d["instagram_business_account"]["id"]


REINTENTOS_PUBLICAR = 6


def _esperar_contenedor(cid: str, espera=time.sleep):
    for _ in range(20):
        estado = _get(cid, fields="status_code").get("status_code")
        if estado == "FINISHED":
            return
        if estado in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Meta rechazó la imagen ({estado})")
        espera(3)
    raise RuntimeError("Meta tardó demasiado en procesar la imagen")


def publicar_en_meta(formato: str, caption: str, urls: list[str], espera=time.sleep) -> dict:
    """Crea el contenedor, espera a que Meta baje las imagenes y publica."""
    ig = _cuenta_ig()
    if formato == "carrusel":
        hijos = []
        for url in urls:
            hijos.append(_post(f"{ig}/media", image_url=url, is_carousel_item="true")["id"])
        for h in hijos:
            _esperar_contenedor(h, espera)
        cid = _post(f"{ig}/media", media_type="CAROUSEL", children=",".join(hijos),
                    caption=caption)["id"]
    elif formato == "historia":
        cid = _post(f"{ig}/media", media_type="STORIES", image_url=urls[0])["id"]
    else:
        cid = _post(f"{ig}/media", image_url=urls[0], caption=caption)["id"]
    _esperar_contenedor(cid, espera)
    # Meta a veces dice FINISHED y aun asi rechaza publicar con 9007 ("Media ID is not
    # available"): el 21/9/2026 dejo sin salir el carrusel de las 19 h. Se reintenta.
    for intento in range(REINTENTOS_PUBLICAR):
        try:
            media_id = _post(f"{ig}/media_publish", creation_id=cid)["id"]
            break
        except RuntimeError as e:
            if "code=9007" not in str(e) or intento == REINTENTOS_PUBLICAR - 1:
                raise
            espera(10)
    try:
        permalink = _get(media_id, fields="permalink").get("permalink")
    except RuntimeError:
        permalink = None
    return {"id": media_id, "permalink": permalink}


def urls_publicas(pub: dict, base: str) -> list[str]:
    return [f"{base.rstrip('/')}/pub/ig/{pub['img_token']}/{n}.jpg?v={pub['version']}"
            for n in range(1, pub["imagenes"] + 1)]


def publicar_pendientes(db_path: str, ahora: datetime | None = None, publicar=None,
                        base: str | None = None, avisar=None) -> list[dict]:
    """Publica lo aprobado cuya hora llego. Devuelve que paso con cada una."""
    from services.email_service import send_instagram_error

    from services.meta_insights import hay_credenciales

    ahora = ahora or ahora_utc()
    if publicar is None:
        if not (hay_credenciales() and os.environ.get("META_PAGE_ID")):
            return []
        publicar = publicar_en_meta
    avisar = avisar or send_instagram_error
    base = base or os.environ.get("CRM_URL", "https://scalerics-crm.fly.dev")
    resultados = []
    conn = _connect(db_path)
    try:
        vencidas = conn.execute(
            "UPDATE ig_publicaciones SET estado = 'vencida', actualizado_en = CURRENT_TIMESTAMP "
            "WHERE estado = 'aprobada' AND programada_para < ?",
            (_txt(ahora - timedelta(hours=VENCE_A_LAS_HORAS)),)).rowcount
        conn.commit()
        if vencidas:
            logger.warning(f"Instagram: {vencidas} publicaciones aprobadas vencieron sin salir")
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM ig_publicaciones WHERE estado = 'aprobada' AND programada_para <= ? "
            "ORDER BY programada_para LIMIT ?", (_txt(ahora), TOPE_POR_VUELTA))]
    finally:
        conn.close()

    for pub_id in ids:
        conn = _connect(db_path)
        try:
            # Se toma la fila antes de llamar a Meta: dos hilos no publican dos veces.
            tomada = conn.execute(
                "UPDATE ig_publicaciones SET estado = 'publicando' "
                "WHERE id = ? AND estado = 'aprobada'", (pub_id,)).rowcount
            conn.commit()
        finally:
            conn.close()
        if not tomada:
            continue
        pub = obtener(db_path, pub_id)
        try:
            r = publicar(pub["formato"], pub["caption"], urls_publicas(pub, base))
            estado, campos = "publicada", (r["id"], r.get("permalink"), None, _txt(ahora))
        except Exception as e:
            estado, campos = "error", (None, None, str(e)[:300], None)
            logger.error(f"Instagram: no se pudo publicar {pub_id}: {e}")
            try:
                avisar(pub_id, str(e)[:300])
            except Exception:
                pass
        conn = _connect(db_path)
        try:
            conn.execute(
                "UPDATE ig_publicaciones SET estado = ?, ig_media_id = ?, permalink = ?, "
                "error = ?, publicada_en = ?, actualizado_en = CURRENT_TIMESTAMP WHERE id = ?",
                (estado, *campos, pub_id))
            conn.commit()
        finally:
            conn.close()
        resultados.append({"id": pub_id, "estado": estado})
    return resultados


def imagen_publica(db_path: str, token: str, n: int) -> str | None:
    """La ruta del archivo si el token existe y la publicacion esta por salir."""
    if not token or len(token) != 32:
        return None
    conn = _connect(db_path)
    try:
        fila = conn.execute(
            "SELECT * FROM ig_publicaciones WHERE img_token = ? "
            "AND estado IN ('aprobada','publicando')", (token,)).fetchone()
    finally:
        conn.close()
    if not fila or not 1 <= n <= fila["imagenes"]:
        return None
    ruta = ruta_imagen(db_path, dict(fila), n)
    return ruta if os.path.isfile(ruta) else None


# ── vista del perfil ─────────────────────────────────────────────────────────
# Como quedaria la grilla de @scalerics_ al final de una semana: lo nuevo arriba
# y lo ya publicado abajo. Las ultimas publicaciones se piden a Meta y se
# guardan media hora en memoria: el panel se abre seguido y no hace falta
# pedirlas cada vez.

GRILLA = 9
_CACHE_FEED = {"cuando": 0.0, "items": None}
_CACHE_SEGUNDOS = 1800


def feed_publicado(cantidad: int = GRILLA, traer=None) -> list[dict]:
    ahora = time.time()
    if traer is None and _CACHE_FEED["items"] is not None \
            and ahora - _CACHE_FEED["cuando"] < _CACHE_SEGUNDOS:
        return _CACHE_FEED["items"][:cantidad]
    if traer is None:
        def traer():
            d = _get(f"{_cuenta_ig()}/media", limit=GRILLA + 3,
                     fields="id,timestamp,media_type,media_url,thumbnail_url,permalink")
            return d.get("data", [])
    items = [{"id": m.get("id"), "fecha": (m.get("timestamp") or "")[:10],
              "imagen": m.get("thumbnail_url") or m.get("media_url"),
              "permalink": m.get("permalink"), "video": m.get("media_type") == "VIDEO"}
             for m in traer() if m.get("thumbnail_url") or m.get("media_url")]
    _CACHE_FEED.update(cuando=ahora, items=items)
    return items[:cantidad]


def grilla(db_path: str, lunes: date, ahora: datetime | None = None, publicadas=None) -> dict:
    """{'nuevas': [...], 'publicadas': [...], 'error': str|None}, en orden de perfil."""
    ahora = ahora or ahora_utc()
    fin = datetime.combine(lunes + timedelta(days=7), datetime.min.time(), _UY)
    conn = _connect(db_path)
    try:
        filas = conn.execute(
            "SELECT * FROM ig_publicaciones WHERE formato != 'historia' "
            "AND estado IN ('borrador','aprobada','publicando','error','vencida') "
            "AND programada_para >= ? AND programada_para < ? AND imagenes > 0 "
            "ORDER BY programada_para DESC",
            (_txt(ahora), _txt(fin))).fetchall()
    finally:
        conn.close()
    nuevas = []
    for f in filas[:GRILLA]:
        p = dict(f)
        local = _dt(p["programada_para"]).astimezone(_UY)
        nuevas.append({"id": p["id"], "estado": p["estado"], "formato": p["formato"],
                       "fecha": local.date().isoformat(), "hora": local.strftime("%H:%M"),
                       "version": p["version"]})
    error = None
    viejas = []
    faltan = GRILLA - len(nuevas)
    if faltan > 0:
        try:
            viejas = (publicadas if publicadas is not None else feed_publicado())[:faltan]
        except Exception as e:
            error = "No se pudieron traer las publicaciones de Instagram"
            logger.warning(f"Instagram: grilla sin publicadas ({e})")
    return {"nuevas": nuevas, "publicadas": viejas, "error": error}


# ── rutina ───────────────────────────────────────────────────────────────────

def rutina(db_path: str, ahora: datetime | None = None, avisar_semana=None) -> dict:
    """Lo que hace el hilo en cada vuelta."""
    from services.email_service import send_instagram_banco_bajo, send_instagram_semana

    ahora = ahora or ahora_utc()
    avisar_semana = avisar_semana or send_instagram_semana
    local = ahora.astimezone(_UY)
    salida = {"publicadas": publicar_pendientes(db_path, ahora)}
    from services import ig_comentarios
    salida["comentarios"] = ig_comentarios.corrida(db_path, ahora)

    if (local.weekday() == DIA_DE_ARMADO and local.hour >= HORA_DE_ARMADO
            and puede_correr(db_path, _JOB_SEMANA)):
        marcar_corrida(db_path, _JOB_SEMANA)
        proxima = semana_actual(ahora) + timedelta(days=7)
        creadas = armar_semana(db_path, proxima, ahora)
        salida["creadas"] = creadas
        if creadas:
            avisar_semana(len(creadas), proxima)
        conn = _connect(db_path)
        try:
            quedan = disponibles(conn, ahora)
        finally:
            conn.close()
        if (min(quedan["feed"], quedan["historia"] * 3 // 2) < MINIMO_EN_BANCO
                and puede_correr(db_path, _JOB_AVISO_BANCO, 24 * 6)):
            marcar_corrida(db_path, _JOB_AVISO_BANCO)
            send_instagram_banco_bajo(quedan["feed"], quedan["historia"])
    return salida


def start_instagram(app) -> None:
    if not activo():
        logger.info("Instagram apagado por INSTAGRAM_AGENTE=off")
        return
    db_path = app.config["DB_PATH"]
    try:
        n = sembrar_banco(db_path)
        if n:
            logger.info(f"Instagram: {n} ideas nuevas en el banco")
    except Exception as e:
        logger.warning(f"Instagram: no se pudo sembrar el banco ({e})")
    # Una sola vez: las piezas armadas antes del plan de fondos se ajustan a el.
    if puede_correr(db_path, _JOB_REPLAN, 24 * 3650):
        try:
            marcar_corrida(db_path, _JOB_REPLAN)
            logger.info(f"Instagram: {replanificar(db_path)} piezas ajustadas al plan de fondos")
        except Exception as e:
            logger.warning(f"Instagram: no se pudo ajustar al plan de fondos ({e})")

    def _loop():
        time.sleep(300)
        while True:
            try:
                rutina(db_path)
            except Exception as e:
                logger.warning(f"Instagram: {e}")
            time.sleep(600)

    threading.Thread(target=_loop, daemon=True, name="instagram").start()
    logger.info("Instagram ACTIVO: publica solo lo aprobado; arma la semana los jueves")
