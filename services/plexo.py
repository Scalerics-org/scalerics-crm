"""Cobro automático con tarjeta a través de Plexo (pedido de Juan, 24/9).

Plexo guarda la tarjeta del cliente y cobra cuando se lo pedimos, pero su API
pública NO tiene un "cobrá solo todos los meses": el calendario lo lleva el
CRM. El circuito:

1. Un ingreso fijo que se cobra con tarjeta pide la tarjeta: se crea el
   cliente en Plexo y una sesión de tokenización, y el link se le manda al
   cliente. La tarjeta la carga él en la página de Plexo; nunca pasa por acá
   (sin certificación PCI, Plexo rechaza una tarjeta enviada desde el
   servidor: `pci-certification-required`).
2. Plexo avisa al callback. El aviso NO viene firmado, así que no se le cree:
   se vuelve a preguntar a Plexo por las tarjetas de ese cliente.
3. Cada día, a partir del día de cobro del fijo, `cobrar_pendientes` le cobra
   el mes a la tarjeta guardada. Recién cuando Plexo lo aprueba se registra en
   Finanzas (ingreso, comisión, Plexo y depósito: lo mismo que el botón
   "Registrar cobro"). Un fijo en automático NO se materializa suponiendo que
   pagó (`es_automatico`, lo mira services/finanzas.py).

Guardas contra cobrar dos veces:
- una fila en `plexo_cobros` por (fijo, mes, intento) con índice único, que se
  inserta ANTES de llamar a Plexo;
- un intento que quedó "enviando" (la máquina se cayó en el medio) no se
  reintenta solo: se consulta a Plexo por su referencia;
- el ingreso del mes lleva (recurrente_id, periodo), el mismo índice único que
  usan los fijos de siempre.

Credenciales: PLEXO_CLIENT_ID, PLEXO_MERCHANT_ID y PLEXO_ENV (testing o
produccion) son datos, no secretos. La key sale de PLEXO_API_KEY o, si no
está, del panel Contraseñas ("Plexo TESTING…" / "Plexo PRODUCCIÓN…"), así
nadie tiene que pegarla en ningún lado.
"""

import base64
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

URLS = {"testing": "https://api.testing.plexo.com.uy", "produccion": "https://api.plexo.com.uy"}
REINTENTOS = 3            # intentos por mes antes de avisar que la tarjeta no pasa
DIAS_ENTRE_INTENTOS = 3
_TIMEOUT = 30


class PlexoError(RuntimeError):
    """Plexo respondió con error o no respondió. El mensaje va a la pantalla."""


# ── configuración ────────────────────────────────────────────────────────────

def entorno() -> str:
    e = (os.environ.get("PLEXO_ENV") or "testing").strip().lower()
    return "produccion" if e in ("produccion", "producción", "production", "prod") else "testing"


def _key_de_contrasenas(db: str, env: str) -> str | None:
    patron = "%plexo%test%" if env == "testing" else "%plexo%produc%"
    try:
        c = sqlite3.connect(db)
        try:
            fila = c.execute("SELECT clave_cifrada FROM credenciales WHERE lower(servicio) LIKE ? "
                             "ORDER BY id DESC LIMIT 1", (patron,)).fetchone()
            if not fila:
                # Si hay UNA sola de Plexo, con cualquier nombre, es esa. Con
                # dos no se adivina cuál es la de producción.
                filas = c.execute("SELECT clave_cifrada FROM credenciales "
                                  "WHERE lower(servicio) LIKE '%plexo%'").fetchall()
                fila = filas[0] if len(filas) == 1 else None
        finally:
            c.close()
    except sqlite3.Error:
        return None
    if not fila:
        return None
    try:
        from services.credenciales import descifrar
        key = descifrar(fila[0]).strip()
    except Exception as e:  # sin CREDENCIALES_KEY, clave rotada
        logger.warning("Plexo: no pude leer la key de Contraseñas (%s)", e)
        return None
    return key if key and not key.startswith("(no se pudo") else None


def configuracion(db: str) -> dict:
    env = entorno()
    cfg = {"env": env, "base_url": URLS[env],
           "client_id": (os.environ.get("PLEXO_CLIENT_ID") or "").strip(),
           "merchant_id": (os.environ.get("PLEXO_MERCHANT_ID") or "").strip(),
           "api_key": (os.environ.get("PLEXO_API_KEY") or "").strip() or None}
    if not cfg["api_key"]:
        cfg["api_key"] = _key_de_contrasenas(db, env)
    faltan = [k for k in ("client_id", "merchant_id", "api_key") if not cfg[k]]
    cfg["faltan"] = faltan
    cfg["listo"] = not faltan
    return cfg


def activo() -> bool:
    """PLEXO_COBROS=off apaga los cobros automáticos (la pantalla sigue)."""
    return (os.environ.get("PLEXO_COBROS") or "on").strip().lower() != "off"


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _http(cfg: dict, metodo: str, ruta: str, cuerpo: dict | None = None):
    """(status, json). Aparte para poder reemplazarlo en los tests."""
    token = base64.b64encode(f"{cfg['client_id']}:{cfg['api_key']}".encode()).decode()
    req = urllib.request.Request(
        cfg["base_url"] + ruta, method=metodo,
        data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        headers={"Authorization": "Basic " + token, "Content-Type": "application/json",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            texto = r.read()
            return r.status, (json.loads(texto) if texto else {})
    except urllib.error.HTTPError as e:
        texto = e.read()
        try:
            return e.code, json.loads(texto)
        except ValueError:
            return e.code, {"message": texto[:300].decode("utf-8", "replace")}


def _pedir(cfg: dict, metodo: str, ruta: str, cuerpo: dict | None = None) -> dict | list:
    if not cfg.get("listo"):
        raise PlexoError("Plexo no está configurado: falta " + ", ".join(cfg.get("faltan") or []))
    try:
        status, datos = _http(cfg, metodo, ruta, cuerpo)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise PlexoError(f"Plexo no respondió: {e}") from e
    if status >= 400:
        msg = (datos or {}).get("message") if isinstance(datos, dict) else None
        codigo = (datos or {}).get("code") if isinstance(datos, dict) else None
        raise PlexoError(f"Plexo respondió {status}: {msg or codigo or 'sin detalle'}")
    return datos


# Plexo exige un email válido para crear el cliente (24/9: "Customer profile
# validation failed" con un fijo sin email). Si el cliente no tiene, va el
# nuestro: los avisos de Plexo le llegan a Juan.
EMAIL_POR_DEFECTO = "contacto@scalerics.com"
_EMAIL_OK = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def crear_cliente(cfg: dict, referencia: str, email: str | None, nombre: str) -> str:
    partes = (nombre or "Cliente").strip().split(" ", 1)
    email = (email or "").strip()
    if not _EMAIL_OK.match(email):
        email = EMAIL_POR_DEFECTO
    datos = _pedir(cfg, "POST", "/v1/customers", {
        "referenceId": referencia, "email": email,
        "firstName": partes[0][:60], "lastName": (partes[1] if len(partes) > 1 else partes[0])[:60]})
    cid = datos.get("id") if isinstance(datos, dict) else None
    if not cid:
        raise PlexoError("Plexo no devolvió el id del cliente")
    return cid


def crear_sesion_tarjeta(cfg: dict, customer_id: str, referencia: str,
                         callback_url: str | None, volver_url: str) -> dict:
    """El link de la página de Plexo donde el cliente carga la tarjeta."""
    settings = {"paymentMethodTypes": ["card"],
                "tokenization": {"type": "store", "features": ["create", "select"]},
                "redirects": {"defaultUrl": volver_url, "cancelUrl": volver_url}}
    if callback_url:
        settings["callbacks"] = {"tokenizationCallbackUrl": callback_url}
    datos = _pedir(cfg, "POST", "/v1/sessions", {
        "merchantId": int(cfg["merchant_id"]), "referenceId": referencia,
        "type": "tokenization", "customerId": customer_id, "settings": settings})
    link = next((a.get("href") for a in (datos.get("actions") or []) if a.get("rel") == "REDIRECT"), None)
    if not link:
        raise PlexoError("Plexo no devolvió el link para cargar la tarjeta")
    return {"link": link, "expira": datos.get("expiresAt")}


def tarjetas_del_cliente(cfg: dict, customer_id: str) -> list[dict]:
    datos = _pedir(cfg, "GET", f"/v1/customers/{customer_id}/payment-instruments")
    if isinstance(datos, dict):
        datos = datos.get("items") or datos.get("data") or []
    return [t for t in datos if (t.get("status") or "active") == "active"]


def cobrar(cfg: dict, customer_id: str, token: str, total: float, moneda: str,
           referencia: str, metadata: dict) -> dict:
    return _pedir(cfg, "POST", "/v1/payments", {
        "merchantId": int(cfg["merchant_id"]), "referenceId": referencia, "invoiceNumber": referencia,
        "customerId": customer_id, "amount": {"total": round(float(total), 2), "currency": moneda},
        "paymentMethod": {"source": "token", "paymentInstrument": {"token": token}},
        "capture": {"method": "automatic"}, "metadata": metadata})


def buscar_pago(cfg: dict, referencia: str) -> dict | None:
    """El pago con esa referencia, si Plexo lo tiene (para un intento que quedó a medias)."""
    datos = _pedir(cfg, "GET", f"/v1/payments/query?merchantId={int(cfg['merchant_id'])}"
                               f"&referenceId={urllib.request.quote(referencia)}&size=5")
    lista = datos if isinstance(datos, list) else (
        datos.get("items") or datos.get("data") or datos.get("results") or [])
    return next((p for p in lista if p.get("referenceId") == referencia), None)


# ── marca de la tarjeta ──────────────────────────────────────────────────────

def tarjeta_de(instrumento: dict) -> str | None:
    """La clave de services/cobro_tarjeta.TARJETAS para esa tarjeta, por el BIN.

    Importa porque la comisión cambia: Visa débito 1,05% y Master crédito
    3,35%. Si no se reconoce, None: se queda la que eligió Juan en el fijo.
    """
    bin_ = re.sub(r"\D", "", str(instrumento.get("bin") or instrumento.get("cardName") or ""))[:6]
    tipo = (instrumento.get("cardType") or "").lower()
    if not bin_ or tipo not in ("credit", "debit"):
        return None
    if bin_.startswith("589657") or bin_.startswith("542991"):
        marca = "oca"
    elif bin_.startswith("4"):
        marca = "visa"
    elif bin_[:2] in ("51", "52", "53", "54", "55") or 2221 <= int(bin_[:4]) <= 2720:
        marca = "master"
    else:
        return None
    return f"{marca}_{'credito' if tipo == 'credit' else 'debito'}"


# ── base ─────────────────────────────────────────────────────────────────────

def init_plexo(conn: sqlite3.Connection) -> None:
    """Tablas del cobro automático. La llama `database.init_db`."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plexo_suscripciones (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            recurrente_id   INTEGER NOT NULL UNIQUE REFERENCES finanzas_recurrentes(id) ON DELETE CASCADE,
            env             TEXT NOT NULL,
            customer_id     TEXT,
            token           TEXT,
            tarjeta_texto   TEXT,
            vence           TEXT,
            estado          TEXT NOT NULL DEFAULT 'sin_tarjeta',
            link            TEXT,
            link_expira     TEXT,
            link_publico    TEXT UNIQUE,
            ultimo_error    TEXT,
            activada_en     TEXT,
            creado_en       TEXT NOT NULL,
            actualizado_en  TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plexo_cobros (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            recurrente_id   INTEGER NOT NULL,
            periodo         TEXT NOT NULL,
            intento         INTEGER NOT NULL,
            referencia      TEXT NOT NULL UNIQUE,
            env             TEXT NOT NULL,
            estado          TEXT NOT NULL,
            total           REAL,
            moneda          TEXT,
            payment_id      TEXT,
            detalle         TEXT,
            cobro_tarjeta_id INTEGER,
            creado_en       TEXT NOT NULL,
            actualizado_en  TEXT,
            UNIQUE (recurrente_id, periodo, intento)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_plexo_cobros_fijo ON plexo_cobros(recurrente_id, periodo)")


def _conn(db: str) -> sqlite3.Connection:
    c = sqlite3.connect(db, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _ahora() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def suscripcion(db: str, recurrente_id: int) -> dict | None:
    c = _conn(db)
    try:
        f = c.execute("SELECT * FROM plexo_suscripciones WHERE recurrente_id = ?", (recurrente_id,)).fetchone()
        return dict(f) if f else None
    finally:
        c.close()


def es_automatico(db: str, recurrente_id: int) -> bool:
    """Si ese fijo lo cobra Plexo (tarjeta guardada): entonces Finanzas no lo
    anota suponiendo que pagó; lo anota el cobro aprobado."""
    try:
        s = suscripcion(db, recurrente_id)
    except sqlite3.Error:
        return False
    return bool(s and s.get("token") and s["estado"] in ("activa", "rechazada"))


def _guardar(db: str, recurrente_id: int, **campos) -> None:
    campos["actualizado_en"] = _ahora()
    c = _conn(db)
    try:
        existe = c.execute("SELECT 1 FROM plexo_suscripciones WHERE recurrente_id = ?", (recurrente_id,)).fetchone()
        if existe:
            c.execute(f"UPDATE plexo_suscripciones SET {', '.join(k + ' = ?' for k in campos)} WHERE recurrente_id = ?",
                      list(campos.values()) + [recurrente_id])
        else:
            campos.setdefault("env", entorno())
            campos["recurrente_id"] = recurrente_id
            campos["creado_en"] = _ahora()
            c.execute(f"INSERT INTO plexo_suscripciones ({', '.join(campos)}) VALUES ({', '.join('?' * len(campos))})",
                      list(campos.values()))
        c.commit()
    finally:
        c.close()


def cobros_del_fijo(db: str, recurrente_id: int, limite: int = 12) -> list[dict]:
    c = _conn(db)
    try:
        return [dict(f) for f in c.execute(
            "SELECT * FROM plexo_cobros WHERE recurrente_id = ? ORDER BY creado_en DESC, id DESC LIMIT ?",
            (recurrente_id, limite))]
    finally:
        c.close()


# ── pedir la tarjeta ─────────────────────────────────────────────────────────

def pedir_tarjeta(db: str, fijo: dict, cliente: dict | None, base_url: str, email: str | None = None) -> dict:
    """Deja listo el link que se le manda al cliente para cargar la tarjeta.

    Es un link del CRM (/plexo/tarjeta/<código>) y no el de Plexo, porque la
    página de Plexo vence a la media hora y el cliente puede abrir el mensaje
    días después. Cada vez que lo abre, `abrir_link` pide una página nueva.
    """
    cfg = configuracion(db)
    s = suscripcion(db, fijo["id"]) or {}
    nombre = (cliente or {}).get("name") or fijo["concepto"]
    email = (email or (cliente or {}).get("email") or "").strip() or None
    customer_id = s.get("customer_id") if s.get("env") == cfg["env"] else None
    if not customer_id:
        customer_id = crear_cliente(cfg, f"fijo-{fijo['id']}-{cfg['env']}-{int(time.time())}", email, nombre)
    codigo = s.get("link_publico") if s.get("env") == cfg["env"] and s.get("link_publico") else secrets.token_urlsafe(18)
    estado = s.get("estado") if s.get("token") and s.get("env") == cfg["env"] else "esperando_tarjeta"
    _guardar(db, fijo["id"], env=cfg["env"], customer_id=customer_id, link_publico=codigo,
             estado=estado, ultimo_error=None)
    return {"link": f"{base_url.rstrip('/')}/plexo/tarjeta/{codigo}", "cliente": nombre}


def abrir_link(db: str, codigo: str, base_url: str) -> str | None:
    """La página de Plexo para ese link del CRM, recién creada. None si el código no existe."""
    if not codigo or len(codigo) < 16:
        return None
    c = _conn(db)
    try:
        f = c.execute("SELECT * FROM plexo_suscripciones WHERE link_publico = ?", (codigo,)).fetchone()
    finally:
        c.close()
    if not f or f["estado"] == "pausada" or not f["customer_id"]:
        return None
    s = dict(f)
    cfg = configuracion(db)
    if s["env"] != cfg["env"]:
        return None
    base = base_url.rstrip("/")
    ses = crear_sesion_tarjeta(cfg, s["customer_id"], f"tarjeta-fijo-{s['recurrente_id']}-{int(time.time())}",
                               callback_url=f"{base}/api/plexo/callback/tarjeta" if base.startswith("https://") else None,
                               volver_url=f"{base}/plexo/listo/{codigo}")
    _guardar(db, s["recurrente_id"], link=ses["link"], link_expira=ses["expira"])
    return ses["link"]


def por_link(db: str, codigo: str) -> dict | None:
    c = _conn(db)
    try:
        f = c.execute("SELECT * FROM plexo_suscripciones WHERE link_publico = ?", (codigo,)).fetchone()
        return dict(f) if f else None
    finally:
        c.close()


def verificar_tarjeta(db: str, recurrente_id: int) -> dict:
    """Pregunta a Plexo si el cliente ya cargó la tarjeta y, si sí, la activa.

    Es lo que hace el callback, y también el botón "Ya la cargó": el aviso de
    Plexo no viene firmado, así que la fuente de verdad es esta consulta.
    """
    from database import actualizar_recurrente, get_recurrente
    s = suscripcion(db, recurrente_id)
    if not s or not s.get("customer_id"):
        return {"estado": "sin_tarjeta"}
    cfg = configuracion(db)
    tarjetas = tarjetas_del_cliente(cfg, s["customer_id"])
    if not tarjetas:
        return {"estado": s["estado"], "mensaje": "El cliente todavía no cargó la tarjeta."}
    t = sorted(tarjetas, key=lambda x: x.get("createdAt") or "", reverse=True)[0]
    texto = f"{t.get('cardName') or ''}".replace("X", "•")
    vence = f"{t.get('expMonth') or ''}/{str(t.get('expYear') or '')[-2:]}" if t.get("expMonth") else None
    _guardar(db, recurrente_id, token=t.get("token"), tarjeta_texto=texto[-9:] if texto else None, vence=vence,
             estado="activa", ultimo_error=None, activada_en=s.get("activada_en") or _ahora())
    clave = tarjeta_de(t)
    fijo = get_recurrente(db, recurrente_id)
    if clave and fijo and fijo.get("tarjeta") != clave:
        actualizar_recurrente(db, recurrente_id, tarjeta=clave)
    return {"estado": "activa", "tarjeta": texto, "vence": vence, "tarjeta_tipo": clave}


def por_customer(db: str, customer_id: str) -> dict | None:
    c = _conn(db)
    try:
        f = c.execute("SELECT * FROM plexo_suscripciones WHERE customer_id = ?", (customer_id,)).fetchone()
        return dict(f) if f else None
    finally:
        c.close()


def pausar(db: str, recurrente_id: int, pausada: bool) -> None:
    s = suscripcion(db, recurrente_id)
    if not s:
        return
    if pausada:
        _guardar(db, recurrente_id, estado="pausada")
    else:
        _guardar(db, recurrente_id, estado="activa" if s.get("token") else "esperando_tarjeta")


# ── cobrar ───────────────────────────────────────────────────────────────────

def _periodo(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _monto_a_cobrar(db: str, fijo: dict) -> dict:
    """El desglose del cobro del mes: lo mismo que registra Finanzas."""
    from database import get_ajuste
    from services.cobro_tarjeta import desglosar, unir_ajustes
    ajustes = unir_ajustes(get_ajuste(db, "cobro_tarjeta"))
    return desglosar("precio", fijo["monto"], fijo["tarjeta"], ajustes, moneda=fijo["moneda"],
                     tipo_cambio=fijo["tipo_cambio"], incluir_fijo=False), ajustes


def _registrar_en_finanzas(db: str, fijo: dict, d: dict, ajustes: dict, fecha: str) -> int | None:
    from database import CobroDuplicado, crear_cobro_tarjeta, get_business
    from services.cobro_tarjeta import armar_cobro
    negocio = get_business(db, fijo["client_id"]) if fijo.get("client_id") else None
    cobro, ingreso, comision, plexo = armar_cobro(
        d, fecha=fecha, concepto=fijo["concepto"], categoria=fijo["categoria"],
        client_id=fijo.get("client_id"), cliente=(negocio or {}).get("name"),
        plexo_por_cobro_uyu=ajustes["plexo_por_cobro_uyu"], created_by_name="plexo",
        recurrente_id=fijo["id"])
    try:
        return crear_cobro_tarjeta(db, cobro, ingreso, comision, plexo)
    except CobroDuplicado:
        return None
    except sqlite3.IntegrityError as e:
        if "UNIQUE" in str(e):
            return None  # ese mes ya estaba anotado
        raise


def _intentos(db: str, recurrente_id: int, periodo: str) -> list[dict]:
    c = _conn(db)
    try:
        return [dict(f) for f in c.execute(
            "SELECT * FROM plexo_cobros WHERE recurrente_id = ? AND periodo = ? ORDER BY intento",
            (recurrente_id, periodo))]
    finally:
        c.close()


def _mes_anotado(db: str, recurrente_id: int, periodo: str) -> bool:
    c = _conn(db)
    try:
        return c.execute("SELECT 1 FROM finanzas_movimientos WHERE recurrente_id = ? AND periodo = ? LIMIT 1",
                         (recurrente_id, periodo)).fetchone() is not None
    finally:
        c.close()


def _actualizar_cobro(db: str, cobro_id: int, **campos) -> None:
    campos["actualizado_en"] = _ahora()
    c = _conn(db)
    try:
        c.execute(f"UPDATE plexo_cobros SET {', '.join(k + ' = ?' for k in campos)} WHERE id = ?",
                  list(campos.values()) + [cobro_id])
        c.commit()
    finally:
        c.close()


def cobrar_mes(db: str, fijo: dict, hoy: date, manual: bool = False) -> dict:
    """Intenta cobrar el mes de `hoy` de ese fijo. Devuelve qué pasó.

    `manual` (el botón "Cobrar ahora") salta la espera del día de cobro y de
    los días entre reintentos, pero nunca cobra un mes ya aprobado.
    """
    s = suscripcion(db, fijo["id"])
    if not s or s["estado"] not in ("activa", "rechazada") or not s.get("token"):
        return {"resultado": "sin_tarjeta"}
    cfg = configuracion(db)
    if s.get("env") != cfg["env"]:
        return {"resultado": "otro_entorno"}
    periodo = _periodo(hoy)
    if fijo["desde"] > periodo or (fijo.get("hasta") and fijo["hasta"] < periodo) or not fijo.get("activo", 1):
        return {"resultado": "fuera_de_vigencia"}
    dia = min(max(int(fijo.get("dia_del_mes") or 1), 1), 28)
    if not manual and hoy.day < dia:
        return {"resultado": "todavia_no"}
    # No se cobran meses anteriores a la activación: lo de antes se arregla a mano.
    if s.get("activada_en") and s["activada_en"][:7] > periodo:
        return {"resultado": "antes_de_activar"}

    intentos = _intentos(db, fijo["id"], periodo)
    if any(i["estado"] == "aprobado" for i in intentos):
        return {"resultado": "ya_cobrado"}
    # Si el mes ya está anotado en Finanzas (lo pagó por otro lado, o se
    # materializó antes de guardar la tarjeta), no se le cobra a la tarjeta:
    # Finanzas no lo anotaría de nuevo y el cliente pagaría dos veces. Para
    # cobrarlo igual, primero se borra ese ingreso.
    if not any(i["estado"] == "enviando" for i in intentos) and _mes_anotado(db, fijo["id"], periodo):
        return {"resultado": "ya_anotado"}
    if any(i["estado"] == "enviando" for i in intentos):
        return _reconciliar(db, fijo, [i for i in intentos if i["estado"] == "enviando"][-1], cfg)
    if len(intentos) >= REINTENTOS and not manual:
        return {"resultado": "sin_reintentos"}
    if intentos and not manual:
        ultimo = datetime.strptime(intentos[-1]["creado_en"][:10], "%Y-%m-%d").date()
        if (hoy - ultimo).days < DIAS_ENTRE_INTENTOS:
            return {"resultado": "esperando_reintento"}

    try:
        d, ajustes = _monto_a_cobrar(db, fijo)
    except ValueError as e:
        _guardar(db, fijo["id"], ultimo_error=str(e))
        return {"resultado": "error", "detalle": str(e)}

    intento = len(intentos) + 1
    referencia = f"scalerics-fijo{fijo['id']}-{periodo}-{intento}-{cfg['env'][:4]}"
    c = _conn(db)
    try:
        cur = c.execute("INSERT INTO plexo_cobros (recurrente_id, periodo, intento, referencia, env, estado, total, "
                        "moneda, creado_en) VALUES (?,?,?,?,?,?,?,?,?)",
                        (fijo["id"], periodo, intento, referencia, cfg["env"], "enviando", d["total"],
                         fijo["moneda"], _ahora()))
        c.commit()
        cobro_id = cur.lastrowid
    except sqlite3.IntegrityError:
        return {"resultado": "en_curso"}  # otro proceso lo está cobrando
    finally:
        c.close()

    try:
        pago = cobrar(cfg, s["customer_id"], s["token"], d["total"], fijo["moneda"], referencia,
                      {"fijo": str(fijo["id"]), "periodo": periodo, "concepto": fijo["concepto"][:60]})
    except PlexoError as e:
        # Un 4xx es un rechazo claro de Plexo: no hubo cobro. Uno de red puede
        # haber cobrado igual: queda "enviando" y se reconcilia después.
        if "respondió 4" in str(e):
            _actualizar_cobro(db, cobro_id, estado="error", detalle=str(e)[:300])
            _guardar(db, fijo["id"], estado="rechazada", ultimo_error=str(e)[:300])
            return {"resultado": "error", "detalle": str(e)}
        _actualizar_cobro(db, cobro_id, detalle=str(e)[:300])
        return {"resultado": "sin_respuesta", "detalle": str(e)}
    return _cerrar(db, fijo, cobro_id, pago, d, ajustes, hoy)


def _estado_pago(pago: dict) -> str:
    st = (pago.get("status") or "").lower()
    if st in ("approved", "captured", "paid", "succeeded", "authorized"):
        return "aprobado"
    if st in ("pending", "processing", "created", "in_progress"):
        return "pendiente"
    return "rechazado"


def _detalle(pago: dict) -> str:
    tr = (pago.get("transactions") or [{}])[-1]
    return (tr.get("resultMessage") or pago.get("status") or "")[:200]


def _cerrar(db: str, fijo: dict, cobro_id: int, pago: dict, d: dict, ajustes: dict, hoy: date) -> dict:
    estado = _estado_pago(pago)
    if estado == "pendiente":
        _actualizar_cobro(db, cobro_id, payment_id=pago.get("id"), detalle=_detalle(pago))
        return {"resultado": "pendiente"}
    if estado == "aprobado":
        ct_id = _registrar_en_finanzas(db, fijo, d, ajustes, hoy.isoformat())
        _actualizar_cobro(db, cobro_id, estado="aprobado", payment_id=pago.get("id"), detalle=_detalle(pago),
                          cobro_tarjeta_id=ct_id)
        _guardar(db, fijo["id"], estado="activa", ultimo_error=None)
        return {"resultado": "aprobado", "total": d["total"], "moneda": fijo["moneda"]}
    _actualizar_cobro(db, cobro_id, estado="rechazado", payment_id=pago.get("id"), detalle=_detalle(pago))
    _guardar(db, fijo["id"], estado="rechazada", ultimo_error=_detalle(pago) or "rechazado")
    return {"resultado": "rechazado", "detalle": _detalle(pago)}


def _reconciliar(db: str, fijo: dict, intento: dict, cfg: dict) -> dict:
    """Un intento quedó sin respuesta: se busca en Plexo por la referencia."""
    try:
        pago = buscar_pago(cfg, intento["referencia"])
    except PlexoError as e:
        return {"resultado": "sin_respuesta", "detalle": str(e)}
    if not pago:
        # Plexo no lo registró: no hubo cobro. Se cierra y se puede reintentar.
        _actualizar_cobro(db, intento["id"], estado="error", detalle="Plexo no registró el intento")
        return {"resultado": "reintentable"}
    d, ajustes = _monto_a_cobrar(db, fijo)
    return _cerrar(db, fijo, intento["id"], pago, d, ajustes,
                   datetime.strptime(intento["creado_en"][:10], "%Y-%m-%d").date())


def cobrar_pendientes(db: str, hoy: date | None = None) -> list[dict]:
    """Recorre los fijos con tarjeta guardada y cobra lo que toca. Para el hilo diario."""
    from database import listar_recurrentes
    hoy = hoy or date.today()
    if not configuracion(db)["listo"]:
        return []
    hechos = []
    for fijo in listar_recurrentes(db, solo_activos=True):
        if fijo["tipo"] != "ingreso" or not fijo.get("tarjeta"):
            continue
        try:
            r = cobrar_mes(db, fijo, hoy)
        except Exception as e:  # un fijo no puede frenar a los demás
            logger.warning("Plexo: el fijo %s falló: %s", fijo["id"], e)
            r = {"resultado": "error", "detalle": str(e)}
        if r["resultado"] not in ("sin_tarjeta", "todavia_no", "ya_cobrado", "ya_anotado", "fuera_de_vigencia",
                                  "esperando_reintento", "sin_reintentos", "antes_de_activar"):
            hechos.append({"fijo": fijo["id"], "concepto": fijo["concepto"], **r})
    return hechos


def resumen(db: str) -> dict:
    """Lo que pinta la pantalla: los fijos con tarjeta y en qué está cada uno."""
    from database import get_business, listar_recurrentes
    cfg = configuracion(db)
    hoy = date.today()
    filas = []
    for fijo in listar_recurrentes(db):
        if fijo["tipo"] != "ingreso" or not fijo.get("tarjeta"):
            continue
        s = suscripcion(db, fijo["id"]) or {}
        negocio = get_business(db, fijo["client_id"]) if fijo.get("client_id") else None
        cobros = cobros_del_fijo(db, fijo["id"], 6)
        filas.append({
            "fijo_id": fijo["id"], "concepto": fijo["concepto"], "cliente": (negocio or {}).get("name"),
            "email": (negocio or {}).get("email"), "telefono": (negocio or {}).get("phone"),
            "monto": fijo["monto"], "moneda": fijo["moneda"], "tarjeta_tipo": fijo["tarjeta"],
            "dia": fijo["dia_del_mes"], "activo": fijo["activo"],
            "estado": s.get("estado") or "sin_tarjeta", "env": s.get("env"),
            "tarjeta": s.get("tarjeta_texto"), "vence": s.get("vence"), "codigo": s.get("link_publico"),
            "link_expira": s.get("link_expira"), "ultimo_error": s.get("ultimo_error"),
            "cobrado_este_mes": any(c["estado"] == "aprobado" and c["periodo"] == _periodo(hoy) for c in cobros),
            "cobros": cobros,
        })
    return {"env": cfg["env"], "listo": cfg["listo"], "faltan": cfg["faltan"],
            "automatico": activo(), "merchant_id": cfg["merchant_id"], "fijos": filas}


# ── hilo diario ──────────────────────────────────────────────────────────────

_DEMORA_ARRANQUE_S = 180
_CADA_S = 3600


def start_plexo_cobros(app) -> None:
    """Cada hora mira si hay cobros para hacer. La guarda contra cobrar dos
    veces es la tabla, no el reloj: correrlo de más no cobra de más."""
    if not activo():
        logger.info("Cobros automáticos con Plexo apagados por PLEXO_COBROS=off")
        return

    def _loop():
        time.sleep(_DEMORA_ARRANQUE_S)
        while True:
            try:
                hechos = cobrar_pendientes(app.config["DB_PATH"])
                for h in hechos:
                    logger.info("Plexo: %s", h)
            except Exception as e:
                logger.warning("Plexo: la corrida falló: %s", e)
            time.sleep(_CADA_S)

    threading.Thread(target=_loop, daemon=True, name="plexo-cobros").start()
    logger.info("Cobros automáticos con Plexo ACTIVOS (%s)", entorno())
