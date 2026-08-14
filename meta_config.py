"""Configuración compartida de la integración con Meta Graph API.

Módulo liviano, sin dependencias del proyecto (ni Flask, ni database, ni
services): routes/meta.py, import_meta_leads.py y setup_meta.py lo importan
por igual, para que la versión de Graph viva en un solo lugar sin arrastrar
Flask/DB/email a los dos scripts sueltos que solo necesitan un string.
"""

GRAPH_VERSION = "v26.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
