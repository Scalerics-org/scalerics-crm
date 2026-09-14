"""Sync del estado de las tareas con la database Tasks de Notion.

El modelo es reconciliacion, no eventos: el CRM guarda en `notion_status` el
estado exacto que vio en Notion la ultima vez, y en cada sync compara lo que
Notion dice hoy contra ese valor. Donde difiere, gana Notion. Si un sync falla
no se pierde nada, porque el siguiente ve el mismo desajuste.

Notion tiene mas estados que el CRM (Backlog, Up next, On Hold...), asi que el
mapeo no es 1:1. La regla de `hay_que_escribir` es la que impide que el CRM
aplaste un "Up next" del equipo con un "Backlog" que no aporta nada.
"""

import logging
import os
import re
from datetime import datetime

import requests

from database import (ETAPAS_CLIENTE, add_lead_event, get_business,
                      get_notion_client_by_page, log_activity, update_business)
from database import (borrar_notion_clients, borrar_proyectos, create_task,
                      get_notion_client_by_id, get_notion_clients, get_projects,
                      get_task_by_id, get_tasks_notion, log_activity,
                      set_notion_client_status, update_task,
                      upsert_notion_client, upsert_project)

logger = logging.getLogger(__name__)

API = "https://api.notion.com/v1"
TIMEOUT = 20

_UUID_SUELTO = re.compile(r"(?<![0-9a-f])([0-9a-f]{32})(?![0-9a-f])", re.I)
_UUID_CON_GUIONES = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)

# Grupo del CRM al que pertenece cada estado de Notion.
#
# PENDIENTE DE CONFIRMAR: esta tabla sale de mirar el tablero, no de la API.
# Nadie corrio todavia scripts/notion_smoke.py con un token real, que es lo que
# imprime los grupos de verdad de la property Status.
#
# Si la salida del smoke difiere de esto, se arregla EDITANDO ESTE DICT: es el
# unico de los cuatro pendientes del runbook que no se responde por variable de
# entorno (ver docs/puesta-en-produccion-notion.md, hallazgo 4).
#
# Por que importa: si un estado cae en el grupo equivocado, el CRM cree que el
# grupo cambio cuando no cambio y le baja la tarjeta al equipo. Con "On Hold"
# mal clasificado en "todo", una tarea del CRM en `todo` pareada a una tarjeta
# en *On Hold* la manda a *Backlog* en el proximo push. No hay test ni log que
# distinga eso de un comportamiento correcto.
GRUPOS = {
    "Backlog": "todo",
    "Up next": "todo",
    "In progress": "in_progress",
    "On Hold": "in_progress",
    # El equipo lo agrego el 18-8-2026. Va a in_progress y no a done porque
    # la tarea sigue ocupando a alguien: el trabajo esta hecho pero todavia
    # no lo acepto nadie, asi que en el CRM no puede figurar como cerrada.
    "Waiting To Accept": "in_progress",
    "Done": "done",
}

# Los estados de la database Clientes, con el grupo en el que Notion los pone.
#
# Confirmado contra la API el 20-8-2026 (GET /v1/data_sources/{id}): los seis
# estados, sus grupos y **este orden** son los de la property Status del
# tablero. La property no tiene nombre, ver `_estado_de`.
#
# El orden importa: el kanban del CRM dibuja una columna por estado siguiendo
# esta lista, asi que reordenar el dict reordena el tablero. Si el equipo
# agrega un estado en Notion hay que sumarlo aca a mano, en la posicion que
# ocupa alla; mientras tanto sus fichas caen en la columna "Sin clasificar".
GRUPOS_CLIENTES = {
    "Demo Agendada": "todo",
    "Hay que hacer Presupuesto": "in_progress",
    "Esperando Confirmación Presupuesto": "in_progress",
    "Perdido": "done",
    "Presupuesto Rechazado": "done",
    "Presupuesto Aceptado": "done",
}

# La columna que convierte a un negocio en cliente. Desde el 14/9 no hay otro
# camino en la interfaz: el tablero de Pre-clientes se saco de la vista.
ESTADO_ACEPTADO = "Presupuesto Aceptado"


def estados_de_clientes() -> list[dict]:
    """Las columnas del kanban de clientes, en el orden del tablero.

    Van por la ruta y no hardcodeadas en el JS porque una columna existe aunque
    este vacia: el equipo tiene que ver que "Presupuesto Aceptado" es un estado
    posible aunque hoy no haya ninguna ficha ahi, y eso no se puede deducir
    mirando las fichas que llegaron.
    """
    return [{"estado": estado, "grupo": grupo}
            for estado, grupo in GRUPOS_CLIENTES.items()]


def grupo_de_cliente(estado_notion: str | None) -> str:
    """Grupo de una ficha de Clientes.

    A diferencia de `grupo_de` para tareas, un estado desconocido NO cae en
    `todo`: va a `otros`. Un estado nuevo mezclado entre los pendientes se ve
    igual que los demas y nadie se entera de que falta mapearlo; en una columna
    aparte salta a la vista.
    """
    return GRUPOS_CLIENTES.get(estado_notion or "", "otros")


# A donde manda el CRM cada estado propio cuando el grupo cambio de verdad.
DESTINOS = {
    "todo": "Backlog",
    "in_progress": "In progress",
    "done": "Done",
}


def grupo_de(estado_notion: str | None) -> str:
    """Grupo del CRM para un estado de Notion.

    Un estado vacio (la columna "Sin Status") o uno que no conocemos cuentan
    como pendiente: si el equipo agrega una columna nueva al tablero, el sync
    la trata como to-do en vez de romperse.
    """
    return GRUPOS.get(estado_notion or "", "todo")


def estado_notion_para(estado_crm: str) -> str:
    return DESTINOS.get(estado_crm, "Backlog")


def hay_que_escribir(estado_crm: str, notion_status: str | None) -> bool:
    """True si hay que escribir el estado del CRM en Notion.

    Sin `notion_status` (la tarea todavia no se sincronizo nunca) siempre hay
    que escribir, para dejar sentada la primera posicion en el tablero.

    Con un `notion_status` conocido, solo escribe si el grupo cambio: con el
    CRM en `todo` y la tarjeta en *Up next* devuelve False, porque el grupo de
    *Up next* ya es `todo`, y escribir "Backlog" seria pisarle el orden al
    equipo y ensuciar el historial de la pagina por nada.

    Con un `estado_crm` que no es ninguno de los tres de `DESTINOS` devuelve
    **False**: `grupo_de` nunca puede devolver algo fuera de esos tres, asi que
    la comparacion daria True siempre y cada push reenviaria "Backlog" a la
    tarjeta, que es justo la escritura idempotente que el spec prohibe. Ante un
    estado que no sabemos mapear, no escribir es lo unico que no hace dano.
    """
    if estado_crm not in DESTINOS:
        logger.warning("notion: estado del CRM desconocido (%r), no escribo en el tablero",
                       estado_crm)
        return False
    if notion_status is None:
        return True
    return grupo_de(notion_status) != estado_crm


def _config() -> tuple[str, str, str] | None:
    """(token, version, database_id) o None si no hay token.

    Sin NOTION_TOKEN todo el modulo es no-op: el CRM tiene que funcionar igual
    sin Notion, como funciona hoy sin GMAIL_REFRESH_TOKEN.
    """
    token = os.environ.get("NOTION_TOKEN", "")
    if not token:
        return None
    version = os.environ.get("NOTION_VERSION", "2025-09-03")
    db_id = os.environ.get("NOTION_DATABASE_ID", "3ae65d94-deec-8093-8c48-cfbe77e202d5")
    return token, version, db_id


def _headers(token: str, version: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": version,
        "Content-Type": "application/json",
    }


def _parent(db_id: str) -> dict:
    """Forma del `parent` para crear una pagina.

    `docs/puesta-en-produccion-notion.md` (Task 1) dejo esto PENDIENTE: falta
    correr scripts/notion_smoke.py con un token real para saber si la
    database expone `data_sources`. Hasta entonces el default es
    `{"database_id": ...}`, que es la forma vieja de la API. Si el smoke test
    confirma `data_source_id`, se define NOTION_DATA_SOURCE_ID por env y esta
    funcion cambia sola, sin tocar codigo.
    """
    ds = os.environ.get("NOTION_DATA_SOURCE_ID", "")
    if ds:
        return {"type": "data_source_id", "data_source_id": ds}
    return {"database_id": db_id}


def _url_de_query(db_id: str) -> str:
    """URL para consultar la database Tasks, con el mismo criterio que `_parent`."""
    ds = os.environ.get("NOTION_DATA_SOURCE_ID", "")
    if ds:
        return f"{API}/data_sources/{ds}/query"
    return f"{API}/databases/{db_id}/query"


def _marcar(db_path: str, task_id: int, estado_notion: str, page_id: str | None = None) -> None:
    """Guarda en la tarea el resultado de una escritura exitosa a Notion."""
    campos = {
        "notion_status": estado_notion,
        "notion_synced_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if page_id:
        campos["notion_page_id"] = page_id
    update_task(db_path, task_id, **campos)


def _buscar_pagina_por_crm_id(token: str, version: str, db_id: str,
                              task_id: int) -> dict | None:
    """Busca en el tablero una pagina que ya tenga este `CRM ID`.

    Es la segunda defensa contra duplicados: si el POST de una creacion
    anterior se perdio en el camino (timeout de lectura, conexion cortada)
    despues de que Notion ya creo la pagina, la tarea del CRM quedo sin
    `notion_page_id` y el proximo click en `-> N` crearia una segunda tarjeta
    en un tablero que usa el equipo.

    Devuelve None tanto cuando no hay ninguna como cuando la busqueda fallo:
    el llamador trata los dos casos igual y sigue con la creacion, que era el
    comportamiento anterior. "No sabemos" no habilita a inventar otra cosa,
    pero el fallo queda logueado.
    """
    try:
        r = requests.post(
            _url_de_query(db_id),
            headers=_headers(token, version),
            json={"filter": {"property": "CRM ID", "number": {"equals": task_id}},
                  "page_size": 1},
            timeout=TIMEOUT,
        )
        if r.status_code >= 300:
            logger.warning("notion: buscar por CRM ID fallo con %s: %s",
                           r.status_code, r.text[:300])
            return None
        resultados = r.json().get("results") or []
    except Exception:
        logger.warning("notion: buscar por CRM ID fallo", exc_info=True)
        return None
    return resultados[0] if resultados else None


def crear_pagina(db_path: str, task_id: int) -> str | None:
    """Crea la tarjeta en Notion para una tarea del CRM.

    Dos defensas contra duplicados en un tablero que usa el equipo: si la
    tarea ya tiene `notion_page_id` devuelve ese y no toca Notion, y si no lo
    tiene igual busca por `CRM ID` antes de crear, por si una creacion
    anterior llego a Notion y la respuesta se perdio. Cuando la encuentra, el
    CRM se alinea con el estado de esa tarjeta, como en `vincular_pagina`.
    """
    cfg = _config()
    if not cfg:
        return None
    token, version, db_id = cfg

    tarea = get_task_by_id(db_path, task_id)
    if not tarea:
        return None
    if tarea.get("notion_page_id"):
        return tarea["notion_page_id"]

    ya = _buscar_pagina_por_crm_id(token, version, db_id, task_id)
    if ya and ya.get("id"):
        # La pagina ya existe con este CRM ID: la pareamos en vez de crear otra.
        # Su Status manda, igual que en vincular_pagina: la tarjeta ya vive en el
        # tablero, asi que el CRM se alinea con ella. Sin alinear el status, el
        # pull saltearia esa fila para siempre (estado_notion == notion_status) y
        # el proximo cambio de estado del CRM le empujaria su grupo viejo.
        props = ya.get("properties") or {}
        estado_previo = ((props.get("Status") or {}).get("status") or {}).get("name") or ""
        logger.warning("notion: la tarea %s ya tenia la pagina %s con su CRM ID; "
                       "la pareo en vez de crear otra", task_id, ya["id"])
        update_task(db_path, task_id, status=grupo_de(estado_previo))
        _marcar(db_path, task_id, estado_previo, page_id=ya["id"])
        return ya["id"]

    estado = estado_notion_para(tarea.get("status") or "todo")
    cuerpo = {
        "parent": _parent(db_id),
        "properties": {
            "Name": {"title": [{"text": {"content": tarea.get("title") or "(sin titulo)"}}]},
            "Status": {"status": {"name": estado}},
            "CRM ID": {"number": task_id},
        },
    }
    try:
        r = requests.post(f"{API}/pages", headers=_headers(token, version),
                          json=cuerpo, timeout=TIMEOUT)
        if r.status_code >= 300:
            logger.warning("notion: crear pagina fallo con %s: %s", r.status_code, r.text[:300])
            return None
        page_id = r.json().get("id")
    except Exception:
        logger.warning("notion: crear pagina fallo", exc_info=True)
        return None

    if not page_id:
        return None
    _marcar(db_path, task_id, estado, page_id=page_id)
    return page_id


def empujar_estado(db_path: str, task_id: int) -> bool:
    """Lleva el estado del CRM a la tarjeta, solo si cambio el grupo."""
    cfg = _config()
    if not cfg:
        return False
    token, version, _ = cfg

    tarea = get_task_by_id(db_path, task_id)
    if not tarea or not tarea.get("notion_page_id"):
        return False

    estado_crm = tarea.get("status") or "todo"
    if not hay_que_escribir(estado_crm, tarea.get("notion_status")):
        return False

    destino = estado_notion_para(estado_crm)
    try:
        r = requests.patch(
            f"{API}/pages/{tarea['notion_page_id']}",
            headers=_headers(token, version),
            json={"properties": {"Status": {"status": {"name": destino}}}},
            timeout=TIMEOUT,
        )
        if r.status_code >= 300:
            logger.warning("notion: patch fallo con %s: %s", r.status_code, r.text[:300])
            return False
    except Exception:
        logger.warning("notion: patch fallo", exc_info=True)
        return False

    _marcar(db_path, task_id, destino)
    return True


def page_id_de_url(url: str) -> str | None:
    """Saca el uuid de una URL de Notion y lo devuelve con guiones.

    Las URLs vienen en dos formas: con el id pegado al final del slug del
    titulo, o suelto. La API acepta las dos, pero normalizamos para que el
    pareo contra `notion_page_id` sea siempre el mismo string.
    """
    if not url:
        return None
    con_guiones = _UUID_CON_GUIONES.search(url)
    if con_guiones:
        return con_guiones.group(1).lower()
    # No se sacan los guiones de la URL antes de buscar: en el formato real
    # de Notion (Title-Slug-<32hex>) el guion que separa el slug del id es lo
    # que evita que una palabra del slug que termina en hex (p.ej. "facade")
    # se pegue al id y corra la ventana de 32 caracteres.
    suelto = _UUID_SUELTO.search(url)
    if not suelto:
        return None
    h = suelto.group(1).lower()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _limpiar_crm_id(token: str, version: str, page_id: str) -> bool:
    """Saca el `CRM ID` de la pagina que el CRM deja de tener pareada.

    Es una escritura sobre una pagina que **si** tiene `CRM ID` (justamente la
    que estaba pareada), asi que no viola la restriccion de no tocar paginas
    que el CRM no puso ahi.
    """
    try:
        r = requests.patch(
            f"{API}/pages/{page_id}",
            headers=_headers(token, version),
            json={"properties": {"CRM ID": {"number": None}}},
            timeout=TIMEOUT,
        )
        if r.status_code >= 300:
            logger.warning("notion: soltar el CRM ID de %s fallo con %s: %s",
                           page_id, r.status_code, r.text[:300])
            return False
    except Exception:
        logger.warning("notion: soltar el CRM ID de %s fallo", page_id, exc_info=True)
        return False
    return True


def _despareja(db_path: str, task_id: int, anterior: str | None) -> None:
    """Deja la tarea sin pareo cuando se solto la vieja y no se pudo reclamar la nueva.

    Si nos quedaramos con `anterior` en la base, el CRM apuntaria a una pagina a
    la que le acabamos de sacar el `CRM ID`: el pull dejaria de matchearla y
    `empujar_estado` seguiria escribiendo `Status` en una pagina sin `CRM ID`,
    que es justo la invariante que sostiene toda esta rama. Sin pareo, la tarea
    vuelve a un estado legitimo y la persona puede reintentar con la URL buena.

    Devuelve None siempre, para poder escribir `return _despareja(...)`.
    """
    if not anterior:
        return None
    update_task(db_path, task_id, notion_page_id=None, notion_status=None,
                notion_synced_at=None)
    logger.warning("notion: la tarea %s quedo sin pareo: solte la pagina %s y no "
                   "pude reclamar la nueva", task_id, anterior)
    return None


def vincular_pagina(db_path: str, task_id: int, url: str) -> str | None:
    """Pega una tarea del CRM a una tarjeta que ya existe en el tablero.

    Escribe solo `CRM ID` en la pagina; el `Status` de la tarjeta queda como
    esta y el CRM se alinea con el. La tarjeta ya vivia ahi, asi que su estado
    es el que manda.

    Re-apuntar una tarea a otra tarjeta es valido, pero antes hay que soltar la
    tarjeta vieja: dos paginas con el mismo `CRM ID` matchean las dos contra la
    misma tarea en el pull, y cada sync le invierte el estado. Si soltarla
    falla, no se re-vincula.

    Y si la vieja se solto pero la nueva no se pudo reclamar, la tarea queda
    **sin pareo** (ver `_despareja`): nunca apuntando a una pagina a la que le
    sacamos el `CRM ID`.
    """
    cfg = _config()
    if not cfg:
        return None
    token, version, _ = cfg

    page_id = page_id_de_url(url)
    if not page_id:
        return None
    tarea = get_task_by_id(db_path, task_id)
    if not tarea:
        return None
    anterior = tarea.get("notion_page_id")
    if anterior == page_id:
        # Ya esta vinculada a esta misma pagina: no hay nada que pegar de
        # nuevo. Re-vincular a una pagina DISTINTA si es valido, y sigue
        # abajo.
        return page_id

    if anterior and not _limpiar_crm_id(token, version, anterior):
        # Mejor no re-vincular que dejar dos paginas peleando por el mismo id:
        # con las dos pareadas, cada pull invierte el estado de la tarea y no
        # converge nunca.
        logger.warning("notion: no re-vinculo la tarea %s porque no pude soltar "
                       "la pagina %s", task_id, anterior)
        return None

    try:
        r = requests.patch(
            f"{API}/pages/{page_id}",
            headers=_headers(token, version),
            json={"properties": {"CRM ID": {"number": task_id}}},
            timeout=TIMEOUT,
        )
        if r.status_code >= 300:
            logger.warning("notion: vincular fallo con %s: %s", r.status_code, r.text[:300])
            return _despareja(db_path, task_id, anterior)
        estado = (r.json().get("properties", {})
                  .get("Status", {}).get("status") or {}).get("name") or ""
    except Exception:
        logger.warning("notion: vincular fallo", exc_info=True)
        return _despareja(db_path, task_id, anterior)

    update_task(db_path, task_id, status=grupo_de(estado))
    _marcar(db_path, task_id, estado, page_id=page_id)
    return page_id


def empujar_estado_exacto(db_path: str, task_id: int,
                          estado_notion: str) -> tuple[bool, str | None]:
    """Escribe en la tarjeta el estado exacto que se pidio, no el del grupo.

    Es lo que usa el kanban cuando alguien arrastra una tarjeta. La diferencia
    con `empujar_estado` es deliberada: aquel solo escribe si cambio el grupo,
    porque el CRM adivina el destino a partir de sus tres estados y adivinar
    aplastaria el estado fino del equipo. Un arrastre no es una adivinanza —
    si soltaste en *Up next*, queres *Up next*.

    Es sincrono a proposito, tambien al reves que el push de fondo: quien
    arrastra espera respuesta, y si Notion rechaza la tarjeta tiene que volver
    a su columna en vez de quedar movida solo en el CRM.

    Devuelve `(ok, error)`.
    """
    if estado_notion not in GRUPOS:
        return False, f"'{estado_notion}' no es un estado del tablero"

    cfg = _config()
    if not cfg:
        return False, "falta NOTION_TOKEN: el sync con Notion esta apagado"
    token, version, _ = cfg

    tarea = get_task_by_id(db_path, task_id)
    if not tarea:
        return False, "la tarea no existe"
    if not tarea.get("notion_page_id"):
        return False, "la tarea no esta vinculada a ninguna tarjeta"

    if (tarea.get("notion_status") or "") == estado_notion:
        return True, None  # ya estaba ahi: no se manda un PATCH al vacio

    try:
        r = requests.patch(
            f"{API}/pages/{tarea['notion_page_id']}",
            headers=_headers(token, version),
            json={"properties": {"Status": {"status": {"name": estado_notion}}}},
            timeout=TIMEOUT,
        )
        if r.status_code >= 300:
            logger.warning("notion: mover a %s fallo con %s: %s", estado_notion,
                           r.status_code, r.text[:300])
            return False, f"Notion devolvio HTTP {r.status_code}"
    except Exception as e:
        logger.warning("notion: mover a %s fallo", estado_notion, exc_info=True)
        return False, f"no se pudo hablar con Notion: {type(e).__name__}"

    update_task(db_path, task_id, status=grupo_de(estado_notion))
    _marcar(db_path, task_id, estado_notion)
    return True, None


def _titulo_de(props: dict) -> str:
    """El titulo de una tarjeta. Notion lo parte en varios fragmentos."""
    partes = (props.get("Name") or {}).get("title") or []
    titulo = "".join(p.get("plain_text", "") for p in partes).strip()
    return titulo or "(sin titulo)"


def _proyecto_de(props: dict) -> str | None:
    """El page id del proyecto de una tarjeta, o None si no tiene."""
    relacion = (props.get("Project") or {}).get("relation") or []
    return relacion[0].get("id") if relacion else None


def _adoptar(db_path: str, page_id: str, props: dict, estado_notion: str) -> int:
    """Crea en el CRM la tarea de una tarjeta que el tablero ya tenia.

    El pareo se guarda solo de este lado, en `notion_page_id`. A la tarjeta no
    se le escribe nada: quien trabaja solo en Notion no tiene que notar que el
    CRM la esta mirando.
    """
    titulo = _titulo_de(props)
    task_id = create_task(db_path, title=titulo, status=grupo_de(estado_notion),
                          notion_project_page_id=_proyecto_de(props))
    _marcar(db_path, task_id, estado_notion, page_id=page_id)
    log_activity(db_path, "notion", "task_created", "task", task_id, titulo,
                 "creada desde el tablero de Notion")
    return 1


def _dar_por_desaparecida(db_path: str, tarea: dict) -> int:
    """La tarjeta ya no esta en el tablero: la tarea se cierra y se despareja.

    Se marca hecha en vez de borrarla porque un borrado en Notion no deberia
    llevarse puesto el historial de progreso del CRM, y en vez de dejarla como
    pendiente porque si no se acumulan fantasmas de trabajo que alla ya no
    existe.
    """
    update_task(db_path, tarea["id"], status="done", notion_page_id=None,
                notion_status=None, notion_synced_at=None)
    log_activity(db_path, "notion", "task_updated", "task", tarea["id"],
                 tarea.get("title", ""),
                 "la tarjeta ya no esta en el tablero: se marco hecha")
    return 1


def traer_y_aplicar(db_path: str) -> tuple[int, str | None]:
    """Reconcilia: trae las paginas pareadas y aplica lo que Notion diga.

    Un solo request (mas los que haga falta por cursor). No lleva timestamps:
    compara el Status de Notion contra `notion_status`, que es el ultimo valor
    que los dos lados acordaron. Si un sync se pierde, el siguiente ve el mismo
    desajuste y lo arregla igual.

    Escribe con update_task directo, nunca por el endpoint del CRM: por eso un
    cambio traido de Notion no rebota de vuelta.

    Devuelve `(cambiadas, error)`. `error` es None cuando la consulta funciono,
    y un mensaje corto cuando fallo o cuando falta configuracion. Los dos datos
    hacen falta: "cero limpio" y "los cuatro requests dieron 400" no pueden
    verse iguales desde afuera, porque `POST /api/notion/sync` es el instrumento
    con el que una persona prueba que la configuracion quedo bien. Si falla en
    la segunda pagina del cursor, `cambiadas` conserva lo que ya se aplico.
    """
    cfg = _config()
    if not cfg:
        return 0, "falta NOTION_TOKEN: el sync con Notion esta apagado"
    token, version, db_id = cfg

    # Sin filtro a proposito: el tablero entero es la fuente de verdad del
    # trabajo de proyecto. Filtrar por `CRM ID` obligaria a escribirlo en cada
    # tarjeta del equipo, y eso se ve en su vista de tabla y en el historial de
    # cada pagina.
    cuerpo = {"page_size": 100}
    cambiadas = 0
    url = _url_de_query(db_id)
    por_page = {t["notion_page_id"]: t for t in get_tasks_notion(db_path)}
    vistas = set()
    completo = True

    while True:
        try:
            r = requests.post(url, headers=_headers(token, version),
                              json=cuerpo, timeout=TIMEOUT)
            if r.status_code >= 300:
                logger.warning("notion: query fallo con %s: %s", r.status_code, r.text[:300])
                return cambiadas, f"la consulta a Notion devolvio HTTP {r.status_code}"
            data = r.json()
        except Exception as e:
            logger.warning("notion: query fallo", exc_info=True)
            return cambiadas, f"la consulta a Notion fallo: {type(e).__name__}"

        for pagina in data.get("results", []):
            page_id = pagina.get("id")
            if not page_id:
                continue
            vistas.add(page_id)
            props = pagina.get("properties", {})
            estado_notion = (props.get("Status", {}).get("status") or {}).get("name") or ""

            tarea = por_page.get(page_id)
            if not tarea:
                cambiadas += _adoptar(db_path, page_id, props, estado_notion)
                continue

            task_id = tarea["id"]

            # El proyecto es un dato independiente del Status: se reconcilia
            # siempre que la tarea este pareada, sin depender de que el
            # estado tambien haya cambiado (si no, el "continue" de abajo lo
            # dejaria sin actualizar cuando alguien solo reasigna el proyecto).
            proyecto = _proyecto_de(props)
            if proyecto != tarea.get("notion_project_page_id"):
                update_task(db_path, task_id, notion_project_page_id=proyecto)

            if estado_notion == (tarea.get("notion_status") or ""):
                continue  # nada cambio alla

            nuevo_grupo = grupo_de(estado_notion)
            if nuevo_grupo != (tarea.get("status") or "todo"):
                update_task(db_path, task_id, status=nuevo_grupo)
                log_activity(db_path, "notion", "task_updated", "task", task_id,
                             tarea.get("title", ""), f"estado: {nuevo_grupo} (desde Notion)")
                cambiadas += 1
            # El estado fino se guarda igual, aunque el grupo no haya cambiado.
            _marcar(db_path, task_id, estado_notion)

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        if not cursor:
            logger.warning("notion: query dijo has_more sin next_cursor, corto la paginacion")
            completo = False
            break
        cuerpo["start_cursor"] = cursor

    # Barrido de las que ya no estan en el tablero. Solo si el listado vino
    # entero: si la consulta fallo o la paginacion se corto, las que faltan no
    # estan borradas, simplemente no las vimos — y marcarlas hechas de una
    # pasada seria el peor error posible de este sync.
    if completo:
        for page_id, tarea in por_page.items():
            if page_id not in vistas:
                cambiadas += _dar_por_desaparecida(db_path, tarea)

    return cambiadas, None


def _texto_de_titulo(prop: dict) -> str:
    """Concatena los fragmentos de una property de tipo `title`."""
    partes = (prop or {}).get("title") or []
    return "".join(p.get("plain_text", "") for p in partes).strip()


def traer_proyectos(db_path: str) -> tuple[int, str | None]:
    """Espeja la database Projects en la tabla `projects` del CRM.

    Solo lectura: no se le escribe nada al tablero. Un proyecto que ya no
    vuelve en el listado se borra y sus tareas quedan sin proyecto, pero el
    barrido corre unicamente si el listado vino entero — si la consulta fallo,
    los que faltan no estan borrados, no los vimos.
    """
    cfg = _config()
    if not cfg:
        return 0, "falta NOTION_TOKEN: el sync con Notion esta apagado"
    token, version, _ = cfg

    ds = os.environ.get("NOTION_PROJECTS_DATA_SOURCE_ID", "")
    if not ds:
        return 0, "falta NOTION_PROJECTS_DATA_SOURCE_ID"

    url = f"{API}/data_sources/{ds}/query"
    cuerpo = {"page_size": 100}
    vistos = set()
    cambiados = 0
    completo = True

    while True:
        try:
            r = requests.post(url, headers=_headers(token, version),
                              json=cuerpo, timeout=TIMEOUT)
            if r.status_code >= 300:
                logger.warning("notion: query de proyectos fallo con %s: %s",
                               r.status_code, r.text[:300])
                return cambiados, f"la consulta de proyectos devolvio HTTP {r.status_code}"
            data = r.json()
        except Exception as e:
            logger.warning("notion: query de proyectos fallo", exc_info=True)
            return cambiados, f"la consulta de proyectos fallo: {type(e).__name__}"

        for pagina in data.get("results", []):
            page_id = pagina.get("id")
            if not page_id:
                continue
            props = pagina.get("properties", {})
            nombre = _texto_de_titulo(props.get("Name")) or "(sin nombre)"
            stage = ((props.get("Stage") or {}).get("select") or {}).get("name")
            fechas = (props.get("Timeline") or {}).get("date") or {}
            gente = (props.get("Lead") or {}).get("people") or []
            lead = ", ".join(p.get("name", "") for p in gente) or None

            upsert_project(db_path, page_id, nombre, stage=stage,
                           timeline_start=fechas.get("start"),
                           timeline_end=fechas.get("end"), lead=lead)
            vistos.add(page_id)
            cambiados += 1

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        if not cursor:
            logger.warning("notion: proyectos dijo has_more sin next_cursor, corto")
            completo = False
            break
        cuerpo["start_cursor"] = cursor

    if completo:
        conocidos = {p["notion_page_id"] for p in get_projects(db_path)}
        borrados = borrar_proyectos(db_path, conocidos - vistos)
        if borrados:
            logger.info("notion: %s proyecto(s) borrados del espejo", borrados)

    return cambiados, None


def _estado_de(props: dict) -> str | None:
    """El estado de una ficha de Clientes.

    Esa property **no tiene nombre** en el tablero (por eso la columna sale sin
    encabezado), asi que se la ubica por tipo. Buscarla por "Status" devuelve
    None y dejaria a todos los clientes sin estado.
    """
    for prop in (props or {}).values():
        if (prop or {}).get("type") == "status":
            return ((prop.get("status") or {}) or {}).get("name")
    return None


def _clave_de_estado(props: dict) -> str | None:
    """Con que clave se le escribe a la property de estado de una ficha.

    La misma property que lee `_estado_de` (la primera de tipo `status`), pero
    para escribir no alcanza con ubicarla: en el PATCH hay que nombrarla, y su
    nombre es la cadena vacia. La API acepta como clave el nombre **o el id**
    de la property; se usa el id, que no depende de que alguien le ponga o le
    cambie el nombre en el tablero. Sin id (no deberia pasar) queda el nombre.
    """
    for nombre, prop in (props or {}).items():
        if (prop or {}).get("type") == "status":
            return prop.get("id") or nombre
    return None


def cliente_cambio_de_estado(db_path: str, notion_page_id: str,
                             anterior: str | None, nuevo: str | None) -> None:
    """El unico lugar donde el CRM registra que una ficha de Clientes cambio de
    estado **y Notion ya lo tiene**.

    Lo llaman los dos caminos por los que eso pasa:

    - `mover_cliente`, despues de que Notion acepto el PATCH (o cuando la ficha
      ya estaba en ese estado alla y solo el espejo iba atrasado);
    - `traer_clientes`, cuando el sync ve que una ficha que ya estaba en el
      espejo volvio con otro estado (alguien la movio en Notion). Las fichas
      nuevas no pasan por aca: aparecer no es moverse.

    Si algo del CRM tiene que reaccionar a un cambio de columna, se cuelga de
    esta funcion y no de cada camino por separado.

    Escribe el estado en `notion_clients`. Desde el sync es redundante (el
    upsert ya lo escribio) y es a proposito: asi la fila queda igual venga el
    cambio de donde venga.
    """
    set_notion_client_status(db_path, notion_page_id, nuevo)
    logger.info("notion: la ficha %s paso de %r a %r", notion_page_id, anterior, nuevo)
    if nuevo == ESTADO_ACEPTADO:
        # Un error aca no puede cortar el sync ni devolver la ficha a su
        # columna: Notion ya tiene el cambio. Se loguea y sigue.
        try:
            pasar_a_cliente(db_path, get_notion_client_by_page(db_path, notion_page_id))
        except Exception:
            logger.warning("notion: no se pudo pasar a Clientes el negocio de la ficha %s",
                           notion_page_id, exc_info=True)


def pasar_a_cliente(db_path: str, ficha: dict | None,
                    quien: str = "Proceso venta") -> bool:
    """Pasa a Clientes al negocio conectado a una ficha en "Presupuesto Aceptado".

    Devuelve True solo si lo movio. No hace nada si la ficha no esta conectada,
    si no esta en esa columna, o si el negocio ya es cliente: un negocio en
    `en_desarrollo` o `finalizado` no vuelve a `cerrado` porque alguien
    reacomodo el tablero.

    Deja el mismo rastro que el cambio de estado a mano
    (`POST /api/leads/<id>/crm-status`): el estado, el evento del historial y la
    actividad.
    """
    if not ficha or ficha.get("status") != ESTADO_ACEPTADO:
        return False
    business_id = ficha.get("business_id")
    if not business_id:
        return False
    negocio = get_business(db_path, business_id)
    if not negocio or (negocio.get("crm_status") or "") in ETAPAS_CLIENTE:
        return False
    update_business(db_path, business_id, crm_status="cerrado")
    add_lead_event(db_path, business_id, "cerrado",
                   note="Presupuesto Aceptado en Proceso venta", created_by=quien)
    log_activity(db_path, quien, "status_change", "lead", business_id,
                 negocio.get("name", ""), "cerrado")
    logger.info("notion: %s paso a Clientes por la ficha %s",
                negocio.get("name"), ficha.get("notion_page_id"))
    return True


def mover_cliente(db_path: str, cliente_id: int,
                  estado_notion: str) -> tuple[bool, str | None]:
    """Mueve una ficha de Clientes a otra columna del tablero de Notion.

    Es `empujar_estado_exacto` para Clientes, con los mismos cuidados: estado
    exacto, sincrono (quien arrastra espera la respuesta y la ficha vuelve a
    su columna si Notion no acepta), sin PATCH si el estado no cambio, y
    `(ok, error)`.

    La diferencia es que antes del PATCH hay un GET de la pagina. En Tasks la
    property se llama `Status` y se escribe a ciegas; en Clientes no tiene
    nombre (ver `_estado_de`), asi que se lee la pagina para sacar su id. De
    paso, si en Notion ya estaba en ese estado, tampoco se escribe.

    Solo si Notion acepta se actualiza la fila local de `notion_clients`: el
    sync de fondo la pisaria igual con lo que diga Notion, asi que dejarla
    distinta de Notion no tiene ningun sentido.
    """
    if estado_notion not in GRUPOS_CLIENTES:
        return False, f"'{estado_notion}' no es un estado del tablero de Clientes"

    cfg = _config()
    if not cfg:
        return False, "falta NOTION_TOKEN: el sync con Notion esta apagado"
    token, version, _ = cfg

    cliente = get_notion_client_by_id(db_path, cliente_id)
    if not cliente:
        return False, "la ficha no existe"
    page_id = cliente.get("notion_page_id")
    if not page_id:
        return False, "la ficha no tiene pagina en Notion"

    if (cliente.get("status") or "") == estado_notion:
        return True, None  # ya estaba ahi: no se manda un PATCH al vacio

    headers = _headers(token, version)
    try:
        r = requests.get(f"{API}/pages/{page_id}", headers=headers, timeout=TIMEOUT)
        if r.status_code >= 300:
            logger.warning("notion: leer la ficha %s fallo con %s: %s", page_id,
                           r.status_code, r.text[:300])
            return False, f"Notion devolvio HTTP {r.status_code} al leer la ficha"
        props = (r.json() or {}).get("properties") or {}
    except Exception as e:
        logger.warning("notion: leer la ficha %s fallo", page_id, exc_info=True)
        return False, f"no se pudo hablar con Notion: {type(e).__name__}"

    clave = _clave_de_estado(props)
    if clave is None:
        return False, "la ficha no tiene una property de estado en Notion"

    if _estado_de(props) != estado_notion:
        try:
            r = requests.patch(
                f"{API}/pages/{page_id}",
                headers=headers,
                json={"properties": {clave: {"status": {"name": estado_notion}}}},
                timeout=TIMEOUT,
            )
            if r.status_code >= 300:
                logger.warning("notion: mover la ficha a %s fallo con %s: %s",
                               estado_notion, r.status_code, r.text[:300])
                return False, f"Notion devolvio HTTP {r.status_code}"
        except Exception as e:
            logger.warning("notion: mover la ficha a %s fallo", estado_notion,
                           exc_info=True)
            return False, f"no se pudo hablar con Notion: {type(e).__name__}"

    cliente_cambio_de_estado(db_path, page_id, cliente.get("status"), estado_notion)
    return True, None


def _texto_de(prop: dict) -> str | None:
    """Concatena los fragmentos de una property de tipo `rich_text`."""
    partes = (prop or {}).get("rich_text") or []
    texto = "".join(p.get("plain_text", "") for p in partes).strip()
    return texto or None


def traer_clientes(db_path: str) -> tuple[int, str | None]:
    """Espeja la database Clientes en la tabla `notion_clients` del CRM.

    Mismo trato que Projects: solo lectura, y el barrido de los que ya no
    vuelven corre unicamente si el listado vino entero. No toca `businesses`
    -- los leads del CRM son otra cosa y no se mezclan con estas fichas.
    """
    cfg = _config()
    if not cfg:
        return 0, "falta NOTION_TOKEN: el sync con Notion esta apagado"
    token, version, _ = cfg

    ds = os.environ.get("NOTION_CLIENTS_DATA_SOURCE_ID", "")
    if not ds:
        return 0, "falta NOTION_CLIENTS_DATA_SOURCE_ID"

    url = f"{API}/data_sources/{ds}/query"
    cuerpo = {"page_size": 100}
    vistos = set()
    cambiados = 0
    completo = True
    # El estado que tenia cada ficha antes de este sync, para avisarle a
    # `cliente_cambio_de_estado` solo de las que se movieron de verdad.
    estados_previos = {c["notion_page_id"]: c.get("status")
                       for c in get_notion_clients(db_path)}

    while True:
        try:
            r = requests.post(url, headers=_headers(token, version),
                              json=cuerpo, timeout=TIMEOUT)
            if r.status_code >= 300:
                logger.warning("notion: query de clientes fallo con %s: %s",
                               r.status_code, r.text[:300])
                return cambiados, f"la consulta de clientes devolvio HTTP {r.status_code}"
            data = r.json()
        except Exception as e:
            logger.warning("notion: query de clientes fallo", exc_info=True)
            return cambiados, f"la consulta de clientes fallo: {type(e).__name__}"

        for pagina in data.get("results", []):
            page_id = pagina.get("id")
            if not page_id:
                continue
            props = pagina.get("properties", {})
            fecha = (props.get("Due date") or {}).get("date") or {}
            estado = _estado_de(props)
            upsert_notion_client(
                db_path, page_id,
                _texto_de_titulo(props.get("Name")) or "(sin nombre)",
                status=estado,
                descripcion=_texto_de(props.get("Descripcion")),
                due_date=fecha.get("start"),
                tiempo_estimado=(props.get("Tiempo Estimado") or {}).get("number"),
                notion_project_page_id=_proyecto_de(props),
            )
            if page_id in estados_previos and estados_previos[page_id] != estado:
                cliente_cambio_de_estado(db_path, page_id,
                                         estados_previos[page_id], estado)
            vistos.add(page_id)
            cambiados += 1

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        if not cursor:
            logger.warning("notion: clientes dijo has_more sin next_cursor, corto")
            completo = False
            break
        cuerpo["start_cursor"] = cursor

    if completo:
        conocidos = {c["notion_page_id"] for c in get_notion_clients(db_path)}
        borrados = borrar_notion_clients(db_path, conocidos - vistos)
        if borrados:
            logger.info("notion: %s cliente(s) borrados del espejo", borrados)

    return cambiados, None
