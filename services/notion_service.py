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

# Grupo del CRM al que pertenece cada estado de Notion. Verificado con
# scripts/notion_smoke.py; ver docs/puesta-en-produccion-notion.md.
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
    """
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


def _marcar(db_path: str, task_id: int, estado_notion: str, page_id: str | None = None) -> None:
    """Guarda en la tarea el resultado de una escritura exitosa a Notion."""
    campos = {
        "notion_status": estado_notion,
        "notion_synced_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if page_id:
        campos["notion_page_id"] = page_id
    update_task(db_path, task_id, **campos)


def crear_pagina(db_path: str, task_id: int) -> str | None:
    """Crea la tarjeta en Notion para una tarea del CRM.

    Idempotente por diseno: si la tarea ya tiene `notion_page_id` devuelve ese
    y no toca Notion. Es la primera de las defensas contra duplicados en un
    tablero que usa el equipo.
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


def vincular_pagina(db_path: str, task_id: int, url: str) -> str | None:
    """Pega una tarea del CRM a una tarjeta que ya existe en el tablero.

    Escribe solo `CRM ID` en la pagina; el `Status` de la tarjeta queda como
    esta y el CRM se alinea con el. La tarjeta ya vivia ahi, asi que su estado
    es el que manda.
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
    if tarea.get("notion_page_id") == page_id:
        # Ya esta vinculada a esta misma pagina: no hay nada que pegar de
        # nuevo. Re-vincular a una pagina DISTINTA si es valido, y sigue
        # abajo.
        return page_id

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


def _url_de_query(db_id: str) -> str:
    """URL para consultar la database Tasks, con el mismo criterio que `_parent`."""
    ds = os.environ.get("NOTION_DATA_SOURCE_ID", "")
    if ds:
        return f"{API}/data_sources/{ds}/query"
    return f"{API}/databases/{db_id}/query"


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
