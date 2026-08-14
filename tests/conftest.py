"""Configuracion comun de los tests.

Apaga los procesos de fondo que `create_app` lanza (worker de demos, monitor
del token de Meta e import diario). Sin esto, cada test que construye una app
deja tres threads vivos que siguen escribiendo en SQLite mientras corre el
test siguiente, y aparecen fallos intermitentes de "database is locked" que
no tienen nada que ver con el codigo bajo prueba.

Se setea antes de que se importe dashboard, asi que va a nivel de modulo.
"""

import os

os.environ.setdefault("CRM_SIN_PROCESOS_DE_FONDO", "true")
