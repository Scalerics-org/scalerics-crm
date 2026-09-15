"""Plantillas de mensajes: validación, buscador de leads y variables.

Las plantillas llevan variables entre llaves ({nombre}, {monto}...). El
servidor solo dice cuánto vale cada una para un lead y de dónde salió; el
reemplazo lo hace la pantalla, porque las que no tienen dato se completan a
mano en la vista previa.

De dónde sale cada variable (la primera fuente con dato gana):

    nombre       client_info.lead_name (bot de WhatsApp) · businesses.name si
                 el lead vino de Meta (ahí el nombre es de la persona). Solo
                 el primer nombre: "¿Cómo estás Ana?".
    empresa      client_info.business_name · businesses.name si NO es de Meta
    servicio     businesses.interest (lo que pidió al agendar) ·
                 client_info.rubro (el bot guarda ahí el tipo de proyecto)
    monto        último presupuesto con total (budgets.total_amount, USD) ·
                 businesses.monto_pagado + moneda_pagado (Clientes)
    plazo        no está en el CRM: siempre a mano
    fecha, hora, link
                 la próxima reunión del lead en `meetings` (si no hay
                 próxima, la última) · client_info.meeting_time/meeting_url
    saldo, vencimiento
                 finanzas_por_cobrar sin cobrar. SOLO si el usuario tiene el
                 panel Finanzas: es plata de la empresa (Ruling R20).
"""

from datetime import date, datetime, timedelta, timezone

from database import _connect

VARIABLES = ("nombre", "empresa", "servicio", "monto", "plazo",
             "fecha", "hora", "link", "saldo", "vencimiento")

# Uruguay no tiene horario de verano desde 2015.
_MONTEVIDEO = timezone(timedelta(hours=-3))

_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

# campo -> (nombre para el mensaje de error, largo máximo)
_CAMPOS = {
    "momento": ("momento", 80),
    "titulo": ("título", 120),
    "canal": ("canal", 40),
    "cuerpo": ("texto", 4000),
    "nota": ("nota", 300),
    "explicacion": ("explicación", 1000),
}


def validar_plantilla(datos) -> tuple[dict | None, str | None]:
    if not isinstance(datos, dict):
        return None, "Faltan los datos de la plantilla."
    campos = {}
    for campo, (nombre, tope) in _CAMPOS.items():
        valor = datos.get(campo)
        if valor is None:
            valor = ""
        if not isinstance(valor, str):
            return None, f"El {nombre} tiene que ser texto."
        valor = valor.replace("\r\n", "\n").strip()
        if len(valor) > tope:
            return None, f"El {nombre} es demasiado largo (máximo {tope} caracteres)."
        campos[campo] = valor
    if not campos["titulo"]:
        return None, "Falta el título."
    if not campos["cuerpo"]:
        return None, "Falta el texto del mensaje."
    campos["automatica"] = 1 if datos.get("automatica") in (True, 1, "1", "true") else 0
    return campos, None


# ── buscador ─────────────────────────────────────────────────────────────────

_TEL_SOLO_DIGITOS = ("REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(b.phone, ''), "
                     "' ', ''), '-', ''), '+', ''), '(', ''), ')', '')")


def buscar_leads(db_path: str, q: str, limite: int = 15) -> list[dict]:
    """Leads del CRM por nombre (del negocio o del contacto) o por teléfono."""
    q = (q or "").strip()
    if len(q) < 2:
        return []
    parecido = f"%{q.lower()}%"
    cond = ["LOWER(b.name) LIKE ?", "LOWER(COALESCE(ci.lead_name, '')) LIKE ?",
            "LOWER(COALESCE(ci.business_name, '')) LIKE ?"]
    params: list = [parecido, parecido, parecido]
    digitos = "".join(ch for ch in q if ch.isdigit())
    if len(digitos) >= 3:
        # "099 123 456" tiene que encontrar "+598 99 123 456": sin el 0 de
        # adelante y comparando solo dígitos.
        cond.append(f"{_TEL_SOLO_DIGITOS} LIKE ?")
        params.append(f"%{digitos.lstrip('0') or digitos}%")
    conn = _connect(db_path)
    try:
        filas = conn.execute(
            "SELECT b.id, b.name, b.phone, b.crm_status, ci.lead_name "
            "FROM businesses b LEFT JOIN client_info ci ON ci.client_id = b.id "
            f"WHERE {' OR '.join(cond)} "
            "ORDER BY CASE WHEN LOWER(b.name) LIKE ? THEN 0 ELSE 1 END, "
            "COALESCE(b.last_event_at, b.scraped_at) DESC LIMIT ?",
            params + [f"{q.lower()}%", int(limite)]).fetchall()
    finally:
        conn.close()
    return [dict(f) for f in filas]


# ── variables ────────────────────────────────────────────────────────────────

def _primer_nombre(nombre) -> str:
    partes = str(nombre or "").split()
    if not partes:
        return ""
    p = partes[0]
    return p[:1].upper() + p[1:] if p.islower() else p


def _servicio(texto) -> str:
    """"Página web" -> "página web", para que lea bien en medio de la frase.
    "E-commerce" o "SaaS" quedan como están."""
    s = str(texto or "").strip()
    if len(s) > 1 and s[1].islower():
        return s[:1].lower() + s[1:]
    return s


def _plata(monto, moneda: str) -> str:
    try:
        n = float(monto)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    if abs(n - round(n)) < 0.005:
        texto = f"{int(round(n)):,}".replace(",", ".")
    else:
        texto = f"{n:,.2f}".replace(",", "#").replace(".", ",").replace("#", ".")
    return f"{(moneda or '').strip()} {texto}".strip()


def _partir(cuando) -> tuple[date | None, str]:
    """'2026-09-17T15:00:00-03:00' o '2026-09-17 15:00' -> (fecha, 'HH:MM').
    Toma la hora tal como está escrita, igual que el calendario."""
    s = str(cuando or "").strip().replace(" ", "T", 1)
    try:
        dia = date.fromisoformat(s[:10])
    except ValueError:
        return None, ""
    hora = s[11:16] if len(s) >= 16 and s[10] == "T" and s[13] == ":" else ""
    return dia, hora


def _fecha_larga(dia: date) -> str:
    return f"{_DIAS[dia.weekday()]} {dia:%d/%m}"


def variables_del_lead(db_path: str, lead_id: int, con_finanzas: bool = False,
                       ahora: datetime | None = None) -> dict | None:
    ahora = ahora or datetime.now(_MONTEVIDEO)
    conn = _connect(db_path)
    try:
        b = conn.execute("SELECT * FROM businesses WHERE id = ?", (lead_id,)).fetchone()
        if not b:
            return None
        b = dict(b)
        ci = conn.execute("SELECT * FROM client_info WHERE client_id = ?", (lead_id,)).fetchone()
        ci = dict(ci) if ci else {}
        reuniones = conn.execute(
            "SELECT start_at, meet_link FROM meetings WHERE client_id = ? "
            "AND COALESCE(status, '') != 'canceled' AND COALESCE(start_at, '') != '' "
            "ORDER BY start_at", (lead_id,)).fetchall()
        presupuesto = conn.execute(
            "SELECT total_amount FROM budgets WHERE client_id = ? AND total_amount > 0 "
            "ORDER BY created_at DESC, id DESC LIMIT 1", (lead_id,)).fetchone()
        cobro = None
        if con_finanzas:
            cobro = conn.execute(
                "SELECT SUM(monto_usd) AS saldo, MIN(NULLIF(vence, '')) AS vence "
                "FROM finanzas_por_cobrar WHERE client_id = ? "
                "AND cobrado_movimiento_id IS NULL", (lead_id,)).fetchone()
    finally:
        conn.close()

    valores = {v: "" for v in VARIABLES}
    fuentes = {v: "" for v in VARIABLES}

    def poner(var, valor, fuente):
        valor = str(valor or "").strip()
        if valor and not valores[var]:
            valores[var] = valor
            fuentes[var] = fuente

    de_meta = (b.get("source") or "") == "meta"
    poner("nombre", _primer_nombre(ci.get("lead_name")), "Contacto que dejó en el bot de WhatsApp")
    if de_meta:
        poner("nombre", _primer_nombre(b.get("name")), "Nombre del formulario de Meta")
    poner("empresa", ci.get("business_name"), "Negocio que dejó en el bot de WhatsApp")
    if not de_meta:
        poner("empresa", b.get("name"), "Nombre del negocio en el CRM")
    poner("servicio", _servicio(b.get("interest")), "Servicio que pidió al agendar")
    poner("servicio", _servicio(ci.get("rubro")), "Lo que pidió en el bot de WhatsApp")
    if presupuesto:
        poner("monto", _plata(presupuesto["total_amount"], "USD"), "Presupuesto del CRM")
    poner("monto", _plata(b.get("monto_pagado"), b.get("moneda_pagado") or ""),
          "Monto cargado en Clientes")

    # La próxima reunión; si ya pasaron todas, la última.
    ahora_txt = ahora.strftime("%Y-%m-%dT%H:%M")
    elegida = None
    for r in reuniones:
        elegida = r
        if str(r["start_at"]).replace(" ", "T", 1)[:16] >= ahora_txt:
            break
    if elegida:
        dia, hora = _partir(elegida["start_at"])
        if dia:
            cual = f"Reunión del calendario del CRM ({dia:%d/%m}{' ' + hora if hora else ''})"
            poner("fecha", _fecha_larga(dia), cual)
            poner("hora", hora, cual)
            poner("link", elegida["meet_link"], cual)
    dia, hora = _partir(ci.get("meeting_time"))
    if dia:
        poner("fecha", _fecha_larga(dia), "Reunión que agendó el bot de WhatsApp")
        poner("hora", hora, "Reunión que agendó el bot de WhatsApp")
    poner("link", ci.get("meeting_url"), "Link que mandó el bot de WhatsApp")

    if cobro and cobro["saldo"]:
        poner("saldo", _plata(cobro["saldo"], "USD"), "Por cobrar en Finanzas")
        vence, _ = _partir(cobro["vence"])
        if vence:
            poner("vencimiento", f"{vence:%d/%m/%Y}", "Primer vencimiento por cobrar en Finanzas")

    for var in VARIABLES:
        if not valores[var]:
            fuentes[var] = ("Solo se completa con acceso a Finanzas"
                            if var in ("saldo", "vencimiento") and not con_finanzas
                            else "Sin dato en el CRM: completalo a mano")

    return {
        "lead": {"id": b["id"], "nombre": b.get("name") or "", "telefono": b.get("phone") or ""},
        "valores": valores,
        "fuentes": fuentes,
        "faltan": [v for v in VARIABLES if not valores[v]],
    }
