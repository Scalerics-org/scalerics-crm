"""Clientes de Google construidos una sola vez.

`googleapiclient.discovery.build()` deja ~0,45 MB que no vuelven al heap. Los
syncs de Calendly y de discovery lo llamaban en cada corrida —cada 10 minutos—
asi que el worker crecia ~65 MB por dia hasta que el OOM killer se lo llevaba.
El 9-9-2026 eso dejo el CRM aceptando TCP sin contestar nunca.

El objeto service es reusable: `google.auth` refresca solo el access token
cuando vence, asi que alcanza con construirlo la primera vez y guardarlo.

Se cachea por (api, version, cuenta). La cuenta importa porque el CRM se
autentica con dos juegos de credenciales distintos, GCAL_* y GMAIL_*, y
compartir el cliente entre las dos le daria a una los permisos de la otra.
"""

_CACHE: dict[tuple[str, str, str], object] = {}


def _build(api: str, version: str, credentials):
    from googleapiclient.discovery import build

    # static_discovery usa el esquema que viene con la libreria en vez de
    # bajarlo por HTTP: ahorra 1,9 MB de heap y un GET a googleapis.com.
    return build(api, version, credentials=credentials, static_discovery=True)


def get_service(api: str, version: str, credentials, *, account: str):
    """Devuelve el cliente de `api`, construyendolo solo la primera vez."""
    key = (api, version, account)
    if key not in _CACHE:
        _CACHE[key] = _build(api, version, credentials)
    return _CACHE[key]


def reset_cache() -> None:
    """Olvida los clientes ya construidos (rotacion de credenciales, tests)."""
    _CACHE.clear()
