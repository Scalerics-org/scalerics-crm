"""El socio de un país: ve el calendario y su país, nada más (Juan, 30/10).

Ciro es el socio de Scalerics Argentina. Entra al CRM para ver todas las
reuniones de Scalerics (el calendario, que es uno solo) y la parte de
Argentina de las tres secciones con banderitas: Meta Ads, Inteligencia
marketing y Finanzas. Nada más, y sin modificar nada.

Va igual que el vendedor de Fidelidad (services/fidelidad.py): el menú le
esconde el resto, pero el candado es `candado()`, que corre en
dashboard.require_login antes que cualquier ruta. Hace falta porque las APIs
del CRM no piden panel: sin esto, un fetch a mano le mostraba los leads, los
clientes o la plata de Uruguay.

- El país no lo elige él: la cookie de las banderitas no cuenta. Toda request
  suya corre con su país puesto (database.poner_pais), así las vistas TEMP de
  Meta y Finanzas le dejan pasar solo lo de Argentina.
- Solo GET (y su propio perfil). Escribir con el país puesto en modo "leer"
  falla igual, pero mejor un 403 claro que un error de SQLite.
- Las rutas permitidas están acá, escritas a mano, y no salen de
  `panel_access`: un panel nuevo que se reparta a los roles existentes, o un
  tilde de más en el editor de roles, no le abre nada.

Los mails de SOCIOS_ARGENTINA (separados por coma) entran con el rol en el
alta, sin esperar a Juan, igual que VENDEDORES_FIDELIDAD.
"""

import json
import re
import sqlite3

ROLES = {"Socio Argentina": "AR"}
PANELES = ["cal", "meta", "marketing", "finanzas"]
ENV_EMAILS = {"Socio Argentina": "SOCIOS_ARGENTINA"}

# (método, patrón de la ruta). Lo que no está acá da 403.
_PERMITIDAS = [(m, re.compile(p)) for m, p in (
    ("GET", r"/api/me"),
    ("PUT", r"/api/me"),                                    # su nombre y su contraseña
    # Calendario: las reuniones de todo Scalerics. Las transcripciones no.
    ("GET", r"/api/calendar/events"),
    ("GET", r"/api/calendar/meetings/\d+"),
    ("GET", r"/api/calendar/asuntos/\d+"),
    ("GET", r"/api/calendar/clients/\d+/meetings"),
    # Meta Ads: la lista de leads de Meta (ver `candado`, que pide crm_group=meta).
    ("GET", r"/api/leads"),
    # Inteligencia marketing. La radiografía no: es un informe de toda la cuenta.
    ("GET", r"/api/marketing/(dossier|piezas|leads-semana|version)"),
    ("GET", r"/api/marketing/creativo/\d+"),
    # Finanzas, menos lo que es solo de Uruguay (IVA, tarjeta, cobros, aportes).
    ("GET", r"/api/finanzas/(movimientos|recurrentes|resumen|pauta|meses|por-cobrar"
            r"|balance|balance-general|balance-datos|categorias|esenciales|ventas)"),
)]
# Las páginas (no /api/): el CRM y salir. /admin/users y el resto, no.
_PAGINAS = {"/", "/logout"}


def init_socio_pais(conn: sqlite3.Connection) -> None:
    """Los roles, una vez. Finanzas va en solo lectura también en el dato,
    para que el panel no muestre los botones de cargar."""
    for nombre in ROLES:
        conn.execute("INSERT OR IGNORE INTO roles (name, panel_access, paneles_solo_lectura) "
                     "VALUES (?, ?, ?)", (nombre, json.dumps(PANELES), json.dumps(["finanzas"])))


def asignar_socios(conn: sqlite3.Connection, environ) -> int:
    """Pone el rol a los usuarios de esos mails que no tengan rol. Corre en
    cada arranque y en cada alta; el que ya tiene un rol lo conserva."""
    total = 0
    for nombre, variable in ENV_EMAILS.items():
        emails = [e.strip().lower() for e in (environ.get(variable) or "").split(",") if e.strip()]
        fila = conn.execute("SELECT id FROM roles WHERE name = ?", (nombre,)).fetchone()
        if not emails or not fila:
            continue
        cur = conn.execute(
            f"UPDATE users SET role_id = ? WHERE role_id IS NULL AND lower(email) IN ({', '.join('?' * len(emails))})",
            [fila[0]] + emails)
        total += cur.rowcount
    conn.commit()
    return total


def pais_fijo(db: str, user_id) -> str | None:
    """El país del socio, o None si el usuario no es socio de un país."""
    if not user_id:
        return None
    c = sqlite3.connect(db, timeout=10)
    try:
        f = c.execute("SELECT r.name FROM users u JOIN roles r ON u.role_id = r.id WHERE u.id = ?",
                      (user_id,)).fetchone()
    finally:
        c.close()
    return ROLES.get(f[0]) if f else None


def permitido(metodo: str, ruta: str, args) -> bool:
    if not ruta.startswith("/api/"):
        return ruta in _PAGINAS or ruta.startswith("/static/")
    if ruta == "/api/leads" and args.get("crm_group") != "meta":
        return False
    return any(metodo == m and p.fullmatch(ruta) for m, p in _PERMITIDAS)
