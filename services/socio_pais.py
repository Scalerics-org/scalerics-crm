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

Federico es el socio de Chile (Juan, 3/11: "igual al de Ciro pero solo para
Chile"): mismo rol con CL, y sus mails van en SOCIOS_CHILE. Ese mismo día Juan
pidió que Federico también pueda cargar: leads de Meta y movimientos de
Finanzas, solo de Chile (`_ESCRITURAS`). Después, lo mismo para Ciro con
Argentina. Así que el "solo GET" de arriba vale para lo que no está en
`_ESCRITURAS`.

- Lo que crea queda de su país: el lead de Meta toma el país fijo en la ruta,
  y lo de Finanzas lo marcan los triggers del modo "escribir" (database.aplicar_pais).
- Lo que toca por id tiene que ser de su país (`_es_de_su_pais`). En modo
  "escribir" no hay vistas, así que sin ese chequeo un PUT o DELETE con el id
  de una fila de Uruguay pasaría.
"""

import json
import re
import sqlite3

ROLES = {"Socio Argentina": "AR", "Socio Chile": "CL"}
PANELES = ["cal", "meta", "marketing", "finanzas"]
ENV_EMAILS = {"Socio Argentina": "SOCIOS_ARGENTINA", "Socio Chile": "SOCIOS_CHILE"}
# Los países cuyo socio además carga y edita en sus secciones (Juan, 3/11).
PAISES_QUE_ESCRIBEN = {"CL", "AR"}

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

# Lo que puede modificar el socio de un país de PAISES_QUE_ESCRIBEN:
# (método, patrón, dueño). El dueño dice dónde mirar que el id del patrón sea
# de su país: "lead" (un lead de Meta) o la tabla de Finanzas. None: crea algo
# nuevo, que queda de su país solo.
_ESCRITURAS = [(m, re.compile(p), d) for m, p, d in (
    # Calendario (Juan, 3/11): agendar. Un asunto, o una reunión con un lead de
    # su país (ver `_agenda_permitida`). Mover o borrar reuniones, no.
    ("POST", r"/api/calendar/events", "agenda"),
    # Meta Ads: cargar un lead a mano, el semáforo y la ficha.
    ("POST", r"/api/meta/leads", None),
    ("POST", r"/api/meta/leads/(\d+)/semaforo", "lead"),
    ("GET", r"/api/leads/(\d+)", "lead"),
    ("POST", r"/api/leads/(\d+)/(crm-status|contact|calls|callback)", "lead"),
    ("PUT", r"/api/leads/(\d+)/notes", "lead"),
    # Finanzas, lo de su país. Cerrar meses, IVA, tarjeta y cobros no: son de Uruguay.
    ("POST", r"/api/finanzas/(movimientos|recurrentes|por-cobrar|balance-datos|esenciales|ventas)", None),
    ("PUT", r"/api/finanzas/movimientos/(\d+)", "finanzas_movimientos"),
    ("DELETE", r"/api/finanzas/movimientos/(\d+)", "finanzas_movimientos"),
    ("POST", r"/api/finanzas/movimientos/(\d+)/hecho", "finanzas_movimientos"),
    ("PUT", r"/api/finanzas/recurrentes/(\d+)", "finanzas_recurrentes"),
    ("DELETE", r"/api/finanzas/recurrentes/(\d+)", "finanzas_recurrentes"),
    ("POST", r"/api/finanzas/por-cobrar/(\d+)/cobrar", "finanzas_por_cobrar"),
    ("DELETE", r"/api/finanzas/por-cobrar/(\d+)", "finanzas_por_cobrar"),
    ("PUT", r"/api/finanzas/balance-datos/(\d+)", "finanzas_balance_datos"),
    ("DELETE", r"/api/finanzas/balance-datos/(\d+)", "finanzas_balance_datos"),
    ("PUT", r"/api/finanzas/esenciales/(\d+)", "finanzas_esenciales"),
    ("DELETE", r"/api/finanzas/esenciales/(\d+)", "finanzas_esenciales"),
    ("PUT", r"/api/finanzas/ventas/(\d+)", "finanzas_ventas"),
    ("DELETE", r"/api/finanzas/ventas/(\d+)", "finanzas_ventas"),
)]
# Las páginas (no /api/): el CRM y salir. /admin/users y el resto, no.
_PAGINAS = {"/", "/logout"}


def init_socio_pais(conn: sqlite3.Connection) -> None:
    """Los roles, una vez. Finanzas va en solo lectura también en el dato,
    para que el panel no muestre los botones de cargar; salvo para el país que
    escribe. Eso se pisa en cada arranque: lo manda el código, no el editor."""
    for nombre, pais in ROLES.items():
        solo_lectura = json.dumps([] if pais in PAISES_QUE_ESCRIBEN else ["finanzas"])
        conn.execute("INSERT OR IGNORE INTO roles (name, panel_access, paneles_solo_lectura) "
                     "VALUES (?, ?, ?)", (nombre, json.dumps(PANELES), solo_lectura))
        conn.execute("UPDATE roles SET paneles_solo_lectura = ? WHERE name = ?", (solo_lectura, nombre))


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


def mails_del_pais(pais, environ) -> list:
    """Los mails del socio de ese país, para avisarle de sus leads nuevos
    (routes/meta._notify_new_meta_lead). Sin socio: lista vacía."""
    for nombre, codigo in ROLES.items():
        if codigo == pais:
            return [e.strip().lower() for e in (environ.get(ENV_EMAILS[nombre]) or "").split(",") if e.strip()]
    return []


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


def permitido(metodo: str, ruta: str, args, pais=None, db=None, cuerpo=None) -> bool:
    if not ruta.startswith("/api/"):
        return ruta in _PAGINAS or ruta.startswith("/static/")
    if ruta == "/api/leads" and args.get("crm_group") != "meta":
        return False
    if any(metodo == m and p.fullmatch(ruta) for m, p in _PERMITIDAS):
        return True
    if pais not in PAISES_QUE_ESCRIBEN:
        return False
    for m, p, dueno in _ESCRITURAS:
        hecho = p.fullmatch(ruta) if metodo == m else None
        if hecho:
            if dueno == "agenda":
                return _agenda_permitida(db, pais, cuerpo)
            return dueno is None or _es_de_su_pais(db, pais, dueno, int(hecho.group(1)))
    return False


def _agenda_permitida(db: str, pais: str, cuerpo) -> bool:
    """Una reunión nueva: un asunto, o con un lead de Meta de su país. Un
    cliente de Uruguay no: la reunión le cambiaría el estado."""
    cuerpo = cuerpo if isinstance(cuerpo, dict) else {}
    if (cuerpo.get("tipo") or "cliente") == "asunto":
        return True
    try:
        return _es_de_su_pais(db, pais, "lead", int(cuerpo.get("client_id")))
    except (TypeError, ValueError):
        return False


def _es_de_su_pais(db: str, pais: str, dueno: str, fila_id: int) -> bool:
    """Si esa fila es de su país. Contra main, sin vistas: corre antes de que
    la request ponga el país."""
    from services.paises import de_lead
    c = sqlite3.connect(db, timeout=10)
    try:
        if dueno == "lead":
            f = c.execute("SELECT source, pais, meta_campaign_name, phone FROM businesses WHERE id = ?",
                          (fila_id,)).fetchone()
            return bool(f) and f[0] == "meta" and de_lead(f[1], f[2], f[3]) == pais
        f = c.execute(f"SELECT pais FROM {dueno} WHERE id = ?", (fila_id,)).fetchone()
        return bool(f) and f[0] == pais
    finally:
        c.close()
