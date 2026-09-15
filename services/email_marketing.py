"""Email marketing: lo que sale por Resend y lo que Resend cuenta despues.

Tres fuentes, en este orden:

1. `email_service._send_estado` registra cada envio que Resend acepta, con el
   id que devuelve Resend, el tipo (discovery, recordatorio_meta, aviso...) y
   el negocio si lo hay. Registrar nunca rompe el envio.
2. El webhook de Resend (`routes/resend_webhook.py`) marca entregado, abierto,
   clic, rebote y spam sobre esa fila. Si llega un evento de un mail que el CRM
   no registro (mandado a mano desde Resend, por ejemplo), se crea la fila.
3. "Actualizar estados" (solo admin) le pregunta a la API de Resend el
   `last_event` de los ultimos envios que todavia no tienen ningun evento.

Los historicos de las dos campanas los copia `database._backfill_emails_enviados`
una sola vez.

Nada de esto escribe direcciones de mail en el log.
"""

import logging
import math
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytz
import requests

logger = logging.getLogger(__name__)

MVD = pytz.timezone("America/Montevideo")
_FORMATO = "%Y-%m-%d %H:%M:%S"

# Orden = orden del filtro en pantalla.
TIPOS = {
    "discovery": "Discovery en frío",
    "recordatorio_meta": "Recordatorios a leads de Meta",
    "aviso_equipo": "Avisos al equipo",
    "tarea": "Tareas asignadas",
    "alerta": "Alertas del sistema",
    "linkedin": "Borradores de LinkedIn",
    "reset_password": "Cambio de contraseña",
    "otro": "Otros (fuera del CRM)",
}

# Solo las campanas guardan extracto: en los avisos internos el texto puede
# traer lo que un tercero escribio por WhatsApp o un link de reseteo.
_CON_EXTRACTO = ("discovery", "recordatorio_meta")
_LARGO_EXTRACTO = 140

# Evento del webhook -> columna. Un rebote blando tambien cuenta como rebote
# aca: para la pantalla es "no llego". Quien decide si se veda es el webhook.
EVENTOS = {
    "email.delivered": "entregado_at",
    "email.delivery_delayed": "demorado_at",
    "email.opened": "abierto_at",
    "email.clicked": "clic_at",
    "email.bounced": "rebotado_at",
    "email.complained": "spam_at",
    "email.failed": "fallido_at",
    "email.suppressed": "fallido_at",
}

# Los eventos llegan desordenados (Resend lo avisa). Abrir implica que se
# entrego, y un clic implica las dos: si el "opened" llega antes que el
# "delivered", la fila igual cuenta como entregada.
_IMPLICA = {
    "abierto_at": ("entregado_at",),
    "clic_at": ("abierto_at", "entregado_at"),
    "spam_at": ("entregado_at",),
}

# `last_event` de GET /emails/{id} -> evento equivalente del webhook.
_LAST_EVENT = {
    "delivered": "email.delivered",
    "delivery_delayed": "email.delivery_delayed",
    "opened": "email.opened",
    "clicked": "email.clicked",
    "bounced": "email.bounced",
    "complained": "email.complained",
    "failed": "email.failed",
    "suppressed": "email.suppressed",
    "canceled": "email.failed",
}

ESTADOS = ("enviado", "entregado", "abierto", "clic", "rebotado", "spam",
           "demorado", "fallido", "incierto")

# El mas grave gana: un mail marcado como spam se entrego y a lo mejor se
# abrio, pero lo que importa es la queja.
_ESTADO_SQL = """CASE
    WHEN spam_at IS NOT NULL THEN 'spam'
    WHEN rebotado_at IS NOT NULL THEN 'rebotado'
    WHEN fallido_at IS NOT NULL THEN 'fallido'
    WHEN clic_at IS NOT NULL THEN 'clic'
    WHEN abierto_at IS NOT NULL THEN 'abierto'
    WHEN entregado_at IS NOT NULL THEN 'entregado'
    WHEN demorado_at IS NOT NULL THEN 'demorado'
    ELSE estado_envio END"""

_SIN_EVENTOS_SQL = ("entregado_at IS NULL AND demorado_at IS NULL AND abierto_at IS NULL "
                    "AND clic_at IS NULL AND rebotado_at IS NULL AND spam_at IS NULL "
                    "AND fallido_at IS NULL")

_ID_RESEND = re.compile(r"^[A-Za-z0-9_-]{8,80}$")


def _conn(db_path: str) -> sqlite3.Connection:
    # Timeout corto: esto corre en el camino de un envio y no puede trabarlo.
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def ahora_utc() -> str:
    return datetime.now(timezone.utc).strftime(_FORMATO)


def utc_texto(valor) -> str | None:
    """Una fecha ISO de Resend ('...Z', con offset o sin nada) en UTC de la casa."""
    if not valor or not isinstance(valor, str):
        return None
    try:
        d = datetime.fromisoformat(valor.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is not None:
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    return d.strftime(_FORMATO)


# ── registro ─────────────────────────────────────────────────────────────────

def registrar_envio(db_path: str, *, resend_id: str | None, tipo: str,
                    destinatario: str, asunto: str, texto: str | None = None,
                    business_id: int | None = None, numero: int | None = None,
                    estado_envio: str = "enviado") -> None:
    """Deja la fila del envio. Si el webhook se adelanto, completa la suya."""
    tipo = tipo if tipo in TIPOS else "otro"
    extracto = None
    if texto and tipo in _CON_EXTRACTO:
        extracto = " ".join(str(texto).split())[:_LARGO_EXTRACTO] or None
    fila = (resend_id or None, tipo, (destinatario or "")[:320], (asunto or "")[:300],
            extracto, business_id, numero, ahora_utc(), estado_envio)
    conn = _conn(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO emails_enviados (resend_id, tipo, destinatario, asunto, extracto, "
            "business_id, numero, enviado_at, origen, estado_envio) "
            "VALUES (?,?,?,?,?,?,?,?,'crm',?) "
            "ON CONFLICT(resend_id) DO UPDATE SET tipo=excluded.tipo, "
            "destinatario=excluded.destinatario, asunto=excluded.asunto, "
            "extracto=excluded.extracto, business_id=excluded.business_id, "
            "numero=excluded.numero, origen='crm', estado_envio=excluded.estado_envio",
            fila)
        if resend_id:
            conn.execute(f"UPDATE emails_enviados SET estado = {_ESTADO_SQL} WHERE resend_id = ?",
                         (resend_id,))
        else:
            conn.execute(f"UPDATE emails_enviados SET estado = {_ESTADO_SQL} WHERE id = ?",
                         (cur.lastrowid,))
        conn.commit()
    finally:
        conn.close()


def registrar_evento(db_path: str, tipo_evento: str, datos, creado=None) -> bool:
    """Aplica un evento de Resend a su fila. Devuelve si lo uso."""
    if not isinstance(datos, dict):
        return False
    email_id = str(datos.get("email_id") or "").strip()
    if not email_id or (tipo_evento not in EVENTOS and tipo_evento != "email.sent"):
        return False
    cuando = utc_texto(creado) or ahora_utc()
    conn = _conn(db_path)
    try:
        if not conn.execute("SELECT 1 FROM emails_enviados WHERE resend_id = ?", (email_id,)).fetchone():
            destinos = datos.get("to") or []
            if isinstance(destinos, str):
                destinos = [destinos]
            primero = str(destinos[0]) if destinos else ""
            conn.execute(
                "INSERT INTO emails_enviados (resend_id, tipo, destinatario, asunto, enviado_at, "
                "origen, estado_envio) VALUES (?, 'otro', ?, ?, ?, 'webhook', 'enviado') "
                "ON CONFLICT(resend_id) DO NOTHING",
                (email_id, primero[:320], str(datos.get("subject") or "")[:300],
                 utc_texto(datos.get("created_at")) or cuando))
        columna = EVENTOS.get(tipo_evento)
        if columna:
            # Las columnas salen de un diccionario fijo, no del pedido.
            for col in (columna,) + _IMPLICA.get(columna, ()):
                conn.execute(f"UPDATE emails_enviados SET {col} = COALESCE({col}, ?) WHERE resend_id = ?",
                             (cuando, email_id))
        conn.execute(f"UPDATE emails_enviados SET estado = {_ESTADO_SQL}, actualizado_at = ? "
                     f"WHERE resend_id = ?", (ahora_utc(), email_id))
        conn.commit()
        return True
    finally:
        conn.close()


# ── periodo ──────────────────────────────────────────────────────────────────

def mes_actual(ahora: datetime | None = None) -> str:
    ahora = ahora or datetime.now(timezone.utc)
    return ahora.astimezone(MVD).strftime("%Y-%m")


def parse_mes(texto) -> str | None:
    m = re.fullmatch(r"(\d{4})-(\d{2})", str(texto or ""))
    if not m or not (2000 <= int(m.group(1)) <= 2100 and 1 <= int(m.group(2)) <= 12):
        return None
    return m.group(0)


def _mes_siguiente(mes: str) -> str:
    anio, m = int(mes[:4]), int(mes[5:7])
    return f"{anio + (m == 12)}-{1 if m == 12 else m + 1:02d}"


def rango_mes(mes: str) -> tuple[str, str]:
    """[desde, hasta) del mes de Montevideo, en UTC de la casa."""
    def _utc(clave):
        local = MVD.localize(datetime(int(clave[:4]), int(clave[5:7]), 1))
        return local.astimezone(timezone.utc).strftime(_FORMATO)
    return _utc(mes), _utc(_mes_siguiente(mes))


def a_montevideo(utc: str) -> datetime | None:
    try:
        d = datetime.strptime(utc, _FORMATO)
    except (TypeError, ValueError):
        return None
    return d.replace(tzinfo=timezone.utc).astimezone(MVD)


# ── consulta ─────────────────────────────────────────────────────────────────

def _tasa(parte: int, total: int) -> float | None:
    return round(100.0 * parte / total, 1) if total else None


def resumen(db_path: str, mes: str, tipo: str | None = None, q: str | None = None,
            estado: str | None = None, pagina: int = 1, por_pagina: int = 25) -> dict:
    desde, hasta = rango_mes(mes)
    filtro = ["e.enviado_at >= ?", "e.enviado_at < ?"]
    params: list = [desde, hasta]
    if tipo:
        filtro.append("e.tipo = ?")
        params.append(tipo)

    conn = _conn(db_path)
    try:
        where = " AND ".join(filtro)
        c = conn.execute(f"""
            SELECT COUNT(*) AS enviados,
              COALESCE(SUM(CASE WHEN entregado_at IS NOT NULL OR abierto_at IS NOT NULL
                                  OR clic_at IS NOT NULL OR spam_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS entregados,
              COALESCE(SUM(CASE WHEN abierto_at IS NOT NULL OR clic_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS abiertos,
              COALESCE(SUM(CASE WHEN clic_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS clics,
              COALESCE(SUM(CASE WHEN rebotado_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS rebotados,
              COALESCE(SUM(CASE WHEN spam_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS spam,
              COALESCE(SUM(CASE WHEN {_SIN_EVENTOS_SQL} THEN 1 ELSE 0 END), 0) AS sin_eventos
            FROM emails_enviados e WHERE {where}""", params).fetchone()
        contadores = dict(c)
        total = contadores["enviados"]
        tasas = {k: _tasa(contadores[k], total)
                 for k in ("entregados", "abiertos", "clics", "rebotados", "spam")}

        # Por dia, en hora de Montevideo: un mail de las 23:30 del 31 es del 31.
        por_dia_n: dict[str, int] = {}
        for (utc,) in conn.execute(f"SELECT e.enviado_at FROM emails_enviados e WHERE {where}", params):
            local = a_montevideo(utc)
            if local:
                clave = local.strftime("%Y-%m-%d")
                por_dia_n[clave] = por_dia_n.get(clave, 0) + 1
        dia = datetime(int(mes[:4]), int(mes[5:7]), 1)
        por_dia = []
        while dia.strftime("%Y-%m") == mes:
            clave = dia.strftime("%Y-%m-%d")
            por_dia.append({"dia": clave, "n": por_dia_n.get(clave, 0)})
            dia += timedelta(days=1)

        tabla, tparams = list(filtro), list(params)
        if q:
            patron = "%" + q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            tabla.append("(lower(COALESCE(e.destinatario,'')) LIKE ? ESCAPE '\\' "
                         "OR lower(COALESCE(e.asunto,'')) LIKE ? ESCAPE '\\')")
            tparams += [patron, patron]
        if estado:
            tabla.append("e.estado = ?")
            tparams.append(estado)
        twhere = " AND ".join(tabla)
        encontrados = conn.execute(f"SELECT COUNT(*) FROM emails_enviados e WHERE {twhere}",
                                   tparams).fetchone()[0]
        paginas = max(1, math.ceil(encontrados / por_pagina))
        pagina = min(max(1, pagina), paginas)
        filas = conn.execute(f"""
            SELECT e.id, e.tipo, e.destinatario, e.asunto, e.extracto, e.enviado_at, e.estado,
                   e.origen, b.id AS business_id, b.name AS negocio
            FROM emails_enviados e LEFT JOIN businesses b ON b.id = e.business_id
            WHERE {twhere}
            ORDER BY e.enviado_at DESC, e.id DESC LIMIT ? OFFSET ?""",
            tparams + [por_pagina, (pagina - 1) * por_pagina]).fetchall()

        presentes = {r[0] for r in conn.execute("SELECT DISTINCT tipo FROM emails_enviados")}
    finally:
        conn.close()

    envios = []
    for f in filas:
        local = a_montevideo(f["enviado_at"])
        envios.append({
            "id": f["id"], "tipo": f["tipo"], "tipo_etiqueta": TIPOS.get(f["tipo"], f["tipo"]),
            "destinatario": f["destinatario"] or "", "asunto": f["asunto"] or "",
            "extracto": f["extracto"] or "", "estado": f["estado"], "origen": f["origen"],
            "fecha_local": local.strftime("%d/%m/%Y %H:%M") if local else "",
            "business_id": f["business_id"], "negocio": f["negocio"] or "",
        })

    return {
        "mes": mes, "mes_actual": mes_actual(),
        "tipos": [{"clave": k, "etiqueta": v} for k, v in TIPOS.items() if k in presentes or k == tipo],
        "contadores": contadores, "tasas": tasas, "por_dia": por_dia,
        "envios": envios, "pagina": pagina, "paginas": paginas, "encontrados": encontrados,
        "por_pagina": por_pagina,
    }


# ── Actualizar estados (API de Resend) ───────────────────────────────────────

# Resend permite 10 pedidos por segundo por equipo, y ese cupo lo comparten
# las campanas que estan mandando: se va a menos de la mitad.
_PAUSA_SEGUNDOS = 0.25
TOPE_CONSULTAS = 50
_URL_EMAIL = "https://api.resend.com/emails/"


def pendientes_sin_evento(db_path: str, limite: int) -> list[str]:
    conn = _conn(db_path)
    try:
        return [r[0] for r in conn.execute(f"""
            SELECT resend_id FROM emails_enviados
            WHERE resend_id IS NOT NULL AND ({_SIN_EVENTOS_SQL})
              AND enviado_at >= datetime('now', '-30 days')
            ORDER BY enviado_at DESC, id DESC LIMIT ?""", (limite,))]
    finally:
        conn.close()


def actualizar_estados(db_path: str, api_key: str, limite: int = 20, http=None,
                       dormir=time.sleep, timeout: float = 8.0,
                       presupuesto_segundos: float = 25.0) -> dict:
    """Pide el `last_event` de los ultimos envios sin eventos y lo aplica.

    Una consulta por mail (la API no trae eventos en lote), con pausa entre
    una y otra, timeout por pedido y un presupuesto total para que el pedido
    HTTP del navegador no quede colgado. Un 429 corta la tanda.
    De la respuesta solo se lee `last_event`: el cuerpo del mail no se guarda.
    """
    if not api_key:
        raise ValueError("falta RESEND_API_KEY")
    http = http or requests
    limite = max(1, min(int(limite), TOPE_CONSULTAS))
    res = {"pendientes": 0, "consultados": 0, "actualizados": 0, "errores": 0,
           "cortado_por_limite": False, "cortado_por_tiempo": False}
    ids = [i for i in pendientes_sin_evento(db_path, limite) if _ID_RESEND.match(i)]
    res["pendientes"] = len(ids)
    inicio = time.monotonic()
    for n, rid in enumerate(ids):
        if n:
            if time.monotonic() - inicio > presupuesto_segundos:
                res["cortado_por_tiempo"] = True
                break
            dormir(_PAUSA_SEGUNDOS)
        try:
            r = http.get(_URL_EMAIL + rid, headers={"Authorization": f"Bearer {api_key}"},
                         timeout=timeout)
        except requests.RequestException as e:
            logger.warning(f"Email marketing: no se pudo consultar un envio a Resend ({type(e).__name__})")
            res["errores"] += 1
            continue
        res["consultados"] += 1
        if r.status_code == 429:
            res["cortado_por_limite"] = True
            break
        if r.status_code != 200:
            res["errores"] += 1
            continue
        try:
            cuerpo = r.json()
        except ValueError:
            res["errores"] += 1
            continue
        evento = _LAST_EVENT.get(str((cuerpo or {}).get("last_event") or ""))
        if evento and registrar_evento(db_path, evento, {"email_id": rid}):
            res["actualizados"] += 1
    return res
