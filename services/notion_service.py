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

from database import get_task_by_id, log_activity, update_task

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
    "Done": "done",
}

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
    anterior llego a Notion y la respuesta se perdio.
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
        # Su Status manda, igual que en vincular_pagina: la tarjeta ya vive en
        # el tablero.
        props = ya.get("properties") or {}
        estado_previo = ((props.get("Status") or {}).get("status") or {}).get("name") or ""
        logger.warning("notion: la tarea %s ya tenia la pagina %s con su CRM ID; "
                       "la pareo en vez de crear otra", task_id, ya["id"])
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
    token, version, db_id = cfg

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


def vincular_pagina(db_path: str, task_id: int, url: str) -> str | None:
    """Pega una tarea del CRM a una tarjeta que ya existe en el tablero.

    Escribe solo `CRM ID` en la pagina; el `Status` de la tarjeta queda como
    esta y el CRM se alinea con el. La tarjeta ya vivia ahi, asi que su estado
    es el que manda.

    Re-apuntar una tarea a otra tarjeta es valido, pero antes hay que soltar la
    tarjeta vieja: dos paginas con el mismo `CRM ID` matchean las dos contra la
    misma tarea en el pull, y cada sync le invierte el estado. Si soltarla
    falla, no se re-vincula.
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
            return None
        estado = (r.json().get("properties", {})
                  .get("Status", {}).get("status") or {}).get("name") or ""
    except Exception:
        logger.warning("notion: vincular fallo", exc_info=True)
        return None

    update_task(db_path, task_id, status=grupo_de(estado))
    _marcar(db_path, task_id, estado, page_id=page_id)
    return page_id


def traer_y_aplicar(db_path: str) -> int:
    """Reconcilia: trae las paginas pareadas y aplica lo que Notion diga.

    Un solo request (mas los que haga falta por cursor). No lleva timestamps:
    compara el Status de Notion contra `notion_status`, que es el ultimo valor
    que los dos lados acordaron. Si un sync se pierde, el siguiente ve el mismo
    desajuste y lo arregla igual.

    Escribe con update_task directo, nunca por el endpoint del CRM: por eso un
    cambio traido de Notion no rebota de vuelta.
    """
    cfg = _config()
    if not cfg:
        return 0
    token, version, db_id = cfg

    cuerpo = {
        "filter": {"property": "CRM ID", "number": {"is_not_empty": True}},
        "page_size": 100,
    }
    cambiadas = 0
    url = _url_de_query(db_id)

    while True:
        try:
            r = requests.post(url, headers=_headers(token, version),
                              json=cuerpo, timeout=TIMEOUT)
            if r.status_code >= 300:
                logger.warning("notion: query fallo con %s: %s", r.status_code, r.text[:300])
                return cambiadas
            data = r.json()
        except Exception:
            logger.warning("notion: query fallo", exc_info=True)
            return cambiadas

        for pagina in data.get("results", []):
            props = pagina.get("properties", {})
            task_id = props.get("CRM ID", {}).get("number")
            if not task_id:
                continue
            tarea = get_task_by_id(db_path, int(task_id))
            if not tarea:
                continue  # la tarea se borro en el CRM; la tarjeta queda en paz

            estado_notion = (props.get("Status", {}).get("status") or {}).get("name") or ""
            if estado_notion == (tarea.get("notion_status") or ""):
                continue  # nada cambio alla

            nuevo_grupo = grupo_de(estado_notion)
            if nuevo_grupo != (tarea.get("status") or "todo"):
                update_task(db_path, int(task_id), status=nuevo_grupo)
                log_activity(db_path, "notion", "task_updated", "task", int(task_id),
                             tarea.get("title", ""), f"estado: {nuevo_grupo} (desde Notion)")
                cambiadas += 1
            # El estado fino se guarda igual, aunque el grupo no haya cambiado.
            _marcar(db_path, int(task_id), estado_notion)

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        if not cursor:
            logger.warning("notion: query dijo has_more sin next_cursor, corto la paginacion")
            break
        cuerpo["start_cursor"] = cursor

    return cambiadas
