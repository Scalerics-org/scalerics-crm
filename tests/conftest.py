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

# Ninguna clave de correo real durante los tests. Se borra ANTES de importar
# nada: `_send_estado` decide si manda o no leyendo RESEND_API_KEY al momento
# de mandar, asi que sin clave se queda en modo stub y no toca la red.
#
# No es teorico. El 26/8/2026 la suite corrio en una maquina que si tenia las
# credenciales de produccion y `test_registro.py` —que no mockea el envio—
# mando notificaciones de "nuevo usuario registrado" de verdad, varias veces,
# a la casilla de Juan. Los tests no pueden depender de que el .env de quien
# los corre este vacio.
for _clave in ("RESEND_API_KEY", "RESEND_FROM_EMAIL", "DISCOVERY_FROM_EMAIL",
               "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN",
               "META_PAGE_TOKEN", "NOTION_TOKEN", "ANTHROPIC_API_KEY"):
    os.environ.pop(_clave, None)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _sin_correo_real(monkeypatch):
    """Corta la salida a Resend en TODA la suite, no solo donde alguien se acordo.

    La guarda de arriba alcanza para el modo stub, pero esto es el cinturon: si
    un test setea la clave a proposito (los de discovery lo hacen para probar
    las cabeceras), igual no sale nada a la red.
    """
    def _no_manda(*a, **k):
        raise AssertionError(
            "Un test intento hacer una peticion HTTP a Resend. Los tests no "
            "mandan correo: mockea _send_estado o la funcion que lo llama."
        )

    import services.email_service as es
    monkeypatch.setattr(es.requests, "post", _no_manda)
