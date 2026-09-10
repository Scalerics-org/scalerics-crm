"""Clientes de Google construidos una sola vez por thread.

`googleapiclient.discovery.build()` deja ~0,45 MB que no vuelven al heap. Los
syncs de Calendly y de discovery lo llamaban en cada corrida —cada 10 minutos—
asi que el worker crecia ~65 MB por dia hasta que el OOM killer se lo llevaba.
El 9-9-2026 eso dejo el CRM aceptando TCP sin contestar nunca.

El objeto service es reusable: `google.auth` refresca solo el access token
cuando vence, asi que alcanza con construirlo la primera vez y guardarlo.

**Por que por thread y no uno solo para todos.** El service arrastra un
`httplib2.Http`, y la documentacion de Google dice textual que "The
httplib2.Http() objects are not thread-safe": tiene el pool de conexiones en
un dict comun. Hasta ahora cada llamada construia el suyo y quedaban
aislados; un unico cliente compartido entre las cuatro threads de gunicorn y
las de los jobs de fondo daria respuestas mezcladas y errores intermitentes.
Con `threading.local()` cada thread reusa el suyo y nadie comparte nada.

El costo queda acotado: son unas seis threads vivas (las cuatro de request
mas las de los jobs) por ~2,6 MB, o sea ~15 MB constantes, contra los 65 MB
por dia que crecia antes. Y lo que importa para la fuga se mantiene: el
thread del sync es largo y hace una corrida cada 10 minutos durante dias
reusando siempre el mismo cliente.

Se cachea por (api, version, cuenta). La cuenta importa porque el CRM se
autentica con dos juegos de credenciales distintos, GCAL_* y GMAIL_*, y
compartir el cliente entre las dos le daria a una los permisos de la otra.
"""

import threading

_local = threading.local()


def _cache() -> dict:
    cache = getattr(_local, "cache", None)
    if cache is None:
        cache = _local.cache = {}
    return cache


def _build(api: str, version: str, credentials):
    from googleapiclient.discovery import build

    # static_discovery usa el esquema que viene con la libreria en vez de
    # bajarlo por HTTP: ahorra 1,9 MB de heap y un GET a googleapis.com.
    return build(api, version, credentials=credentials, static_discovery=True)


def get_service(api: str, version: str, credentials, *, account: str):
    """Devuelve el cliente de `api`, construyendolo una vez por thread."""
    cache = _cache()
    key = (api, version, account)
    if key not in cache:
        cache[key] = _build(api, version, credentials)
    return cache[key]


def reset_cache() -> None:
    """Olvida los clientes de esta thread (rotacion de credenciales, tests)."""
    _local.cache = {}
