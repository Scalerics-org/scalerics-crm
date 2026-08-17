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

logger = logging.getLogger(__name__)

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
