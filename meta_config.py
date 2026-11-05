"""Configuración compartida de la integración con Meta Graph API.

Módulo liviano, sin dependencias del proyecto (ni Flask, ni database, ni
services): routes/meta.py, import_meta_leads.py y setup_meta.py lo importan
por igual, para que la versión de Graph viva en un solo lugar sin arrastrar
Flask/DB/email a los dos scripts sueltos que solo necesitan un string.
"""

GRAPH_VERSION = "v26.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"

# Las cuentas publicitarias que lee el CRM. La principal sale del secret
# META_AD_ACCOUNT_ID; las demás van acá porque el ID no es secreto (el token
# sí). "Scalerics Argentina" (3/11/2026): la paga Ciro con su tarjeta, y sus
# campañas llevan "AR" en el nombre como las de la cuenta principal, así que
# el país sale igual que siempre (services/paises.py).
CUENTAS_EXTRA = ("act_2220090788887344",)


def cuentas_publicitarias() -> list:
    """La principal primero y después las extra, sin repetir. La principal
    puede venir sola o con otras separadas por coma."""
    import os

    salida = []
    crudas = (os.environ.get("META_AD_ACCOUNT_ID") or "").split(",") + list(CUENTAS_EXTRA)
    for c in crudas:
        c = c.strip()
        if not c:
            continue
        c = c if c.startswith("act_") else f"act_{c}"
        if c not in salida:
            salida.append(c)
    return salida
