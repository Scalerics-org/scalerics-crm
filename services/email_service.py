import contextvars
import functools
import html
import logging
import os
from contextlib import contextmanager

import requests

from services.secuencia_contactos import TOTAL_CONTACTOS

logger = logging.getLogger(__name__)

_LOGO = "https://raw.githubusercontent.com/Scalerics-org/scalerics-assets/main/logo_full_alt.png"
_CRM_URL = os.environ.get("CRM_URL", "https://scalerics-crm.fly.dev")


# ── Shared layout ──────────────────────────────────────────────────────────────

_PIE_DE_SISTEMA = "Scalerics CRM &middot; Notificación automática &middot; No responder este mail"


def _layout(badge: str, title: str, body: str, cta_url: str = "", cta_label: str = "",
            footer: str | None = None, mostrar_header: bool = True) -> str:
    """Arma el mail. Por defecto es el de siempre: header con logo y pie de
    notificacion automatica.

    `footer=""` saca el pie, y `mostrar_header=False` saca la barra con el
    logo. Los dos existen para los mails que van a un lead y no a nosotros: ahi
    "no responder este mail" es exactamente lo contrario de lo que se busca.
    """
    pie = _PIE_DE_SISTEMA if footer is None else footer
    cta = (
        f'<a href="{cta_url}" style="display:inline-block;background:#0088cc;color:#ffffff;'
        f'font-size:14px;font-weight:600;padding:12px 28px;border-radius:6px;text-decoration:none">'
        f'{cta_label}</a>'
    ) if cta_url else ""
    return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:'Segoe UI',Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:32px 0">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border-radius:10px;overflow:hidden;box-shadow:0 2px 16px rgba(15,31,61,.10)">

        <!-- Header -->
        {f'''<tr>
          <td style="background:#0f1f3d;padding:28px 40px">
            <img src="{_LOGO}" alt="Scalerics" height="32" style="display:block">
          </td>
        </tr>''' if mostrar_header else ""}

        <!-- Body -->
        <tr>
          <td style="padding:36px 40px 32px">
            <p style="margin:0 0 6px;font-size:12px;font-weight:700;letter-spacing:.08em;
                      text-transform:uppercase;color:#0088cc">{badge}</p>
            <h1 style="margin:0 0 24px;font-size:22px;font-weight:700;color:#0f1f3d;line-height:1.3">
              {title}
            </h1>
            {body}
            {f'<div style="margin-top:24px">{cta}</div>' if cta else ""}
          </td>
        </tr>

        <!-- Footer -->
        {f'''<tr>
          <td style="background:#f8fafc;border-top:1px solid #e2e8f0;padding:16px 40px">
            <p style="margin:0;font-size:11.5px;color:#94a3b8">
              {pie}
            </p>
          </td>
        </tr>''' if pie else ""}

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _info_card(rows: list[tuple[str, str]]) -> str:
    """Renders a key-value card. rows = [(label, value), ...]"""
    trs = "".join(
        f'<tr>'
        f'<td style="padding:5px 16px 5px 0;font-size:11px;font-weight:700;color:#94a3b8;'
        f'text-transform:uppercase;letter-spacing:.06em;white-space:nowrap">{label}</td>'
        f'<td style="padding:5px 0;font-size:14px;color:#1c2b40;font-weight:500">{value or "—"}</td>'
        f'</tr>'
        for label, value in rows
    )
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;margin-bottom:24px">'
        f'<tr><td style="padding:20px 24px"><table cellpadding="0" cellspacing="0">{trs}</table></td></tr>'
        f'</table>'
    )


def _muted(text: str) -> str:
    return f'<p style="margin:0 0 20px;font-size:13.5px;color:#5a6a82;line-height:1.6">{text}</p>'


# ── Registro de envios (panel Email marketing) ─────────────────────────────────
# Cada mail que Resend acepta queda en `emails_enviados` con su id de Resend,
# para que el webhook le cuelgue despues entregado / abierto / clic / rebote.
# Que es cada mail (tipo) y a que negocio va lo pone quien llama, con
# `contexto_envio`; asi `_send_estado` no cambia de firma para nadie.

_CONTEXTO_ENVIO = contextvars.ContextVar("contexto_envio", default={})
# La base a la que se registra fuera de un request (los hilos de las campanas).
# La fija `create_app`. Sin base configurada no se registra nada: un script
# suelto o un test no escriben en un leads.db cualquiera.
_DB_REGISTRO: str | None = None


def configurar_registro(db_path: str | None) -> None:
    global _DB_REGISTRO
    _DB_REGISTRO = db_path


@contextmanager
def contexto_envio(**datos):
    """Marca los envios de adentro con tipo, business_id y/o numero."""
    nuevos = {k: v for k, v in datos.items() if v is not None}
    token = _CONTEXTO_ENVIO.set({**_CONTEXTO_ENVIO.get(), **nuevos})
    try:
        yield
    finally:
        _CONTEXTO_ENVIO.reset(token)


def _tipo_envio(tipo: str):
    """Decorador: todo lo que mande la funcion se registra con ese tipo."""
    def decorar(fn):
        @functools.wraps(fn)
        def envuelta(*args, **kwargs):
            with contexto_envio(tipo=tipo):
                return fn(*args, **kwargs)
        return envuelta
    return decorar


def _db_registro() -> str | None:
    try:
        from flask import current_app, has_app_context
        if has_app_context():
            ruta = current_app.config.get("DB_PATH")
            if ruta:
                return ruta
    except Exception:
        pass
    return _DB_REGISTRO


def _registrar_envio(respuesta, to: str, subject: str, text: str | None, estado_envio: str) -> None:
    """Registra el envio. NUNCA levanta: el mail ya salio (o pudo salir)."""
    try:
        db = _db_registro()
        if not db:
            return
        resend_id = None
        if respuesta is not None:
            try:
                cuerpo = respuesta.json()
            except Exception:
                cuerpo = None
            if isinstance(cuerpo, dict) and isinstance(cuerpo.get("id"), str):
                resend_id = cuerpo["id"]
        ctx = _CONTEXTO_ENVIO.get()
        from services.email_marketing import registrar_envio
        registrar_envio(db, resend_id=resend_id, tipo=ctx.get("tipo", "otro"),
                        destinatario=to, asunto=subject, texto=text,
                        business_id=ctx.get("business_id"), numero=ctx.get("numero"),
                        estado_envio=estado_envio)
    except Exception as e:
        # Sin la direccion en el log: solo que fallo y por que clase de error.
        logger.warning(f"No se pudo registrar el envio en emails_enviados ({type(e).__name__})")


# ── Resend sender ───────────────────────────────────────────────────────────────

def _send_estado(to: str, subject: str, html: str, from_email: str | None = None,
                 headers: dict | None = None, text: str | None = None,
                 attachments: list | None = None) -> str:
    """Manda el mail y devuelve un tri-estado: "ok" / "fallo" / "desconocido".

    La diferencia entre "fallo" y "desconocido" importa: "fallo" es *sabemos que
    no salio* (Resend contesto un 4xx/5xx, o no llegamos a conectarnos), y por
    lo tanto se puede reintentar sin riesgo. "desconocido" es un timeout o
    cualquier otra excepcion: la peticion pudo haber llegado y el mail pudo
    haber salido igual, asi que reintentar significa mandar dos veces.
    """
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        logger.info(f"[EMAIL STUB] {subject} → {to}")
        return "ok"
    try:
        cuerpo = {
            "from": from_email or os.environ.get("RESEND_FROM_EMAIL", "Scalerics CRM <crm@noreply.scalerics.com>"),
            "to": [to],
            "subject": subject,
            "html": html,
        }
        if headers:
            cuerpo["headers"] = headers
        if attachments:
            # Formato de Resend: [{"filename": ..., "content": <base64>}].
            # Solo se agrega si viene, asi ningun llamador previo cambia.
            cuerpo["attachments"] = attachments
        if text:
            # La version en texto plano no es un adorno: un mail que solo trae
            # HTML es una de las senales que empujan a Promociones y a spam.
            cuerpo["text"] = text
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=cuerpo,
            timeout=10,
        )
        r.raise_for_status()
        _registrar_envio(r, to, subject, text, "enviado")
        return "ok"
    except requests.exceptions.HTTPError as e:
        # Resend contesto, y contesto que no. El mail no salio.
        logger.error(f"Failed to send '{subject}' to {to}: {e}")
        return "fallo"
    except requests.exceptions.ConnectionError as e:
        # Ni siquiera se establecio la conexion (ConnectTimeout cae aca, y esta
        # bien: si el timeout fue al conectar, la peticion nunca se mando).
        logger.error(f"Failed to send '{subject}' to {to}: {e}")
        return "fallo"
    except requests.exceptions.Timeout as e:
        # Se mando y no volvio la respuesta a tiempo. Puede haber salido.
        logger.error(f"Timeout sending '{subject}' to {to}, no sabemos si salio: {e}")
        _registrar_envio(None, to, subject, text, "incierto")
        return "desconocido"
    except Exception as e:
        logger.error(f"Failed to send '{subject}' to {to}, no sabemos si salio: {e}")
        return "desconocido"


def _send(to: str, subject: str, html: str, from_email: str | None = None, headers: dict | None = None,
          attachments: list | None = None) -> bool:
    return _send_estado(to, subject, html, from_email=from_email, headers=headers,
                        attachments=attachments) == "ok"


# ── Public functions ────────────────────────────────────────────────────────────

@_tipo_envio("reset_password")
def send_reset_email(to_email: str, reset_url: str) -> bool:
    body = (
        _muted("Recibimos una solicitud para resetear la contraseña de tu cuenta en Scalerics CRM. "
               "El link expira en 1 hora.")
        + _info_card([("Link de reseteo", f'<a href="{reset_url}" style="color:#0088cc">{reset_url}</a>')])
        + _muted("Si no lo pediste vos, podés ignorar este mail.")
    )
    html = _layout(
        badge="Seguridad de cuenta",
        title="Resetear contraseña",
        body=body,
        cta_url=reset_url,
        cta_label="Resetear contraseña →",
    )
    return _send(to_email, "Resetear contraseña — Scalerics CRM", html)


@_tipo_envio("aviso_equipo")
def send_new_user_notification(new_name: str, new_email: str, new_phone: str, admin_email: str) -> bool:
    body = (
        _info_card([
            ("Nombre", new_name),
            ("Email", new_email),
            ("Teléfono", new_phone),
        ])
        + _muted("El nuevo usuario ya tiene acceso al panel. "
                 "Podés asignarle un rol desde la sección de administración.")
    )
    html = _layout(
        badge="Nuevo usuario registrado",
        title=f"{new_name} se registró en el CRM",
        body=body,
        cta_url=f"{_CRM_URL}/admin/users",
        cta_label="Ver usuarios →",
    )
    return _send(admin_email, f"Nuevo usuario: {new_name} — Scalerics CRM", html)


@_tipo_envio("aviso_equipo")
def send_new_meta_lead_notification(to_email: str, lead_name: str, phone: str, campaign: str, city: str, lead_id: int) -> bool:
    # Todo esto sale del formulario de Meta, que llena cualquiera en internet:
    # sin escapar, un `<a href="https://phishing/">` en el campo nombre le
    # inyecta un link al mail que reciben los admins.
    nombre_esc   = html.escape(lead_name or "")
    telefono_esc = html.escape(phone or "")
    ciudad_esc   = html.escape(city or "")
    campana_esc  = html.escape(campaign or "")

    rows = [("Nombre", nombre_esc)]
    if telefono_esc:
        rows.append(("Teléfono", telefono_esc))
    if ciudad_esc:
        rows.append(("Ciudad", ciudad_esc))
    if campana_esc:
        rows.append(("Campaña", campana_esc))
    body = (
        _muted("Llegó un nuevo lead de Meta Ads al CRM.")
        + _info_card(rows)
    )
    cuerpo_html = _layout(
        badge="Nuevo lead Meta Ads",
        title=f"Nuevo lead: {nombre_esc}",
        body=body,
        cta_url=f"{_CRM_URL}/?highlight={html.escape(str(lead_id))}",
        cta_label="Ver en CRM →",
    )
    # El asunto es texto plano, no HTML: va el nombre tal cual, sin saltos de línea.
    asunto = f"Nuevo lead Meta: {(lead_name or '').replace(chr(10), ' ').replace(chr(13), ' ')} — Scalerics CRM"
    try:
        lead = int(lead_id)
    except (TypeError, ValueError):
        lead = None
    with contexto_envio(business_id=lead):
        return _send(to_email, asunto, cuerpo_html)


_GOAL_TYPE_LABELS = {
    "reuniones_agendadas": "reuniones agendadas",
    "leads_contactados": "leads contactados",
    "reuniones_hechas": "reuniones hechas",
    "clientes_cerrados": "clientes cerrados",
    "llamadas_realizadas": "llamadas realizadas",
    "llamadas_contestadas": "llamadas contestadas",
}


@_tipo_envio("tarea")
def send_task_assignment_email(
    to_email: str,
    assignee_name: str,
    task_title: str,
    task_description: str,
    goal: int | None,
    goal_type: str | None,
    deadline: str | None,
    assigned_by: str,
    crm_url: str = "",
) -> bool:
    rows = []
    if task_description:
        rows.append(("Descripción", task_description))
    if goal and goal_type:
        label = _GOAL_TYPE_LABELS.get(goal_type, goal_type)
        rows.append(("Meta", f"{goal} {label}"))
    if deadline:
        rows.append(("Vencimiento", deadline))
    rows.append(("Asignada por", assigned_by))

    body = (
        _muted(f"Hola {assignee_name}, <b>{assigned_by}</b> te asignó una nueva tarea en Scalerics CRM.")
        + _info_card(rows)
    )
    html = _layout(
        badge="Nueva tarea asignada",
        title=task_title,
        body=body,
        cta_url=crm_url or _CRM_URL,
        cta_label="Abrir CRM →",
    )
    return _send(to_email, f"Nueva tarea: {task_title} — Scalerics CRM", html)


@_tipo_envio("alerta")
def send_meta_token_alert(to_email: str, error_detail: str) -> bool:
    body = (
        _muted(
            "El token de Meta (PAGE_TOKEN) ya no es válido. "
            "Los leads nuevos de Meta Ads <b>no se están guardando en el CRM</b> hasta que se renueve."
        )
        + _info_card([("Error", error_detail)])
        + _muted(
            "Para renovarlo: entrá al Graph Explorer de Meta, generá un nuevo User Token "
            "con permisos <code>leads_retrieval</code> y <code>pages_read_engagement</code>, "
            "canjealo por un Page Token de larga duración, y cargalo en Fly:<br>"
            "<code>flyctl secrets set META_PAGE_TOKEN=&lt;nuevo&gt; -a scalerics-crm</code><br>"
            "Ese comando reinicia la app con el token nuevo. No alcanza con escribir un "
            "<code>.env</code>: el filesystem del contenedor es efímero fuera de <code>/data</code>."
        )
    )
    html = _layout(
        badge="Alerta de integración",
        title="Token de Meta Ads vencido o revocado",
        body=body,
        cta_url=f"{_CRM_URL}",
        cta_label="Ir al CRM →",
    )
    return _send(to_email, "ALERTA: Token Meta Ads inválido — Scalerics CRM", html)


@_tipo_envio("alerta")
def send_backup_alert(to_email: str, error_detail: str) -> bool:
    """El backup diario de la base fallo (integridad, subida a R2 o excepcion).

    Lo manda `services/backup_db.py`, como mucho uno por dia.
    """
    body = (
        _muted(
            "El backup diario de la base del CRM <b>no se completó</b>. "
            "Mientras no se arregle, la única copia fuera del volumen puede estar quedando vieja."
        )
        + _info_card([("Error", html.escape(error_detail or ""))])
        + _muted(
            "Para revisarlo: mirá el log de la app (<code>flyctl logs -a scalerics-crm</code>, "
            "buscar <code>backup</code>) y probá una corrida a mano desde Administración "
            "o con <code>POST /api/admin/backup-ahora</code>. "
            "Pasos y restauración en <code>docs/BACKUPS.md</code>."
        )
    )
    cuerpo_html = _layout(
        badge="Alerta de backup",
        title="Falló el backup diario de la base",
        body=body,
        cta_url=f"{_CRM_URL}/admin/users",
        cta_label="Ir a Administración →",
    )
    return _send(to_email, "ALERTA: falló el backup de la base — Scalerics CRM", cuerpo_html)


@_tipo_envio("alerta")
def send_discovery_queue_alert(to_email: str, dias: int, pendientes: int,
                               tope: int) -> bool:
    """Avisa que la campana de discovery se esta quedando sin a quien escribirle.

    El hueco de agosto de 2026 duro tres dias y se descubrio de casualidad
    mirando el panel de Resend: el enviador hacia lo correcto (una tanda por
    dia, cero candidatos) y no habia forma de enterarse. Esto no evita el
    hueco; hace que se sepa antes de que pase.
    """
    cuerpo = (
        _muted(
            f"La campana de discovery tiene <b>{pendientes} comercios</b> sin contactar "
            f"en la cola. A {tope} mails por dia eso es <b>{dias} "
            f"{'dia' if dias == 1 else 'dias'}</b> de autonomia."
            if pendientes else
            "La campana de discovery <b>se quedo sin comercios para contactar</b>. "
            "La tanda diaria esta corriendo y encontrando cero candidatos."
        )
        + _info_card([
            ("Sin contactar", str(pendientes)),
            ("Tope diario", str(tope)),
            ("Autonomia", f"{dias} dias"),
        ])
        + _muted(
            "Para recargar la cola hay dos pasos, en este orden: scrapear mas rubros "
            "y despues correrles el buscador de mails. Un comercio sin direccion no "
            "entra en la cola, asi que scrapear solo no alcanza."
        )
    )
    html = _layout(
        badge="Campana de discovery",
        title=("Sin comercios para contactar" if not pendientes
               else f"Quedan {dias} dias de cola"),
        body=cuerpo,
        cta_url=_CRM_URL,
        cta_label="Ir al CRM →",
    )
    asunto = ("Discovery: la cola se quedo vacia — Scalerics CRM" if not pendientes
              else f"Discovery: quedan {dias} dias de cola — Scalerics CRM")
    return _send(to_email, asunto, html)


@_tipo_envio("alerta")
def send_meta_lead_failure_alert(email: str, lead_id: str, error: str) -> None:
    """Avisa que un lead de Meta llegó pero no se pudo guardar."""
    # `lead_id` viene del payload del webhook y `error` puede arrastrar texto
    # de la respuesta de Graph: ninguno de los dos es HTML de confianza.
    lead_id_esc = html.escape(str(lead_id))
    error_esc   = html.escape(str(error))
    asunto = f"[CRM] No se pudo guardar un lead de Meta ({lead_id})"
    cuerpo = (
        f"<p>Llegó un lead de Meta y el CRM no lo pudo guardar.</p>"
        f"<p><b>leadgen_id:</b> {lead_id_esc}</p>"
        f"<p><b>Error:</b> {error_esc}</p>"
        f"<p>Se puede recuperar a mano desde el panel de formularios de Meta "
        f"buscando ese id.</p>"
    )
    _send(email, asunto, cuerpo)


@_tipo_envio("aviso_equipo")
def send_wa_message_notification(to_email: str, nombre: str, telefono: str,
                                 texto: str, hora: str) -> bool:
    """Avisa que alguien escribio al WhatsApp de Scalerics.

    Cuando avisar y cuando no lo decide `services/wa_aviso_mail.py`; aca solo
    se arma el mail. Nombre, telefono y texto los escribe cualquiera que le
    mande un WhatsApp a la empresa: van escapados, igual que los datos del
    formulario de Meta.
    """
    from urllib.parse import quote

    nombre_esc = html.escape(nombre or "")
    telefono_esc = html.escape(telefono or "")
    texto_esc = html.escape(texto or "").replace("\n", "<br>")
    quien_esc = nombre_esc or telefono_esc or "Alguien"
    digitos = "".join(c for c in (telefono or "") if c.isdigit())
    link = f"{_CRM_URL}/?panel=wa" + (f"&chat={quote(digitos)}" if digitos else "")

    filas = []
    if nombre_esc:
        filas.append(("Contacto", nombre_esc))
    filas.append(("Teléfono", telefono_esc))
    filas.append(("Hora", f"{html.escape(hora or '')} (Montevideo)"))
    sin_texto = "<i>(mensaje sin texto: una nota de voz, una foto o un archivo)</i>"
    mensaje = (
        '<div style="background:#f0fdf4;border-left:3px solid #10b981;border-radius:6px;'
        'padding:14px 16px;margin:0 0 20px;font-size:14px;line-height:1.6;color:#1c2b40">'
        f'{texto_esc or sin_texto}</div>'
    )
    cuerpo = (
        _info_card(filas)
        + mensaje
        + _muted("Se avisa con el primer mensaje de cada conversación. Lo que ese "
                 "contacto escriba en los 30 minutos siguientes no genera otro mail.")
    )
    cuerpo_html = _layout(
        badge="WhatsApp",
        title=f"{quien_esc} escribió al WhatsApp",
        body=cuerpo,
        cta_url=html.escape(link),
        cta_label="Abrir la conversación →",
    )
    # El asunto es texto plano: sin escapar, pero sin saltos de linea.
    quien = " ".join((nombre or telefono or "mensaje nuevo").split())[:80]
    asunto = f"WhatsApp: {quien} escribió — Scalerics CRM"
    texto_plano = (
        f"{quien} escribió al WhatsApp.\n"
        f"Teléfono: {telefono or '-'}\n"
        f"Hora: {hora or '-'} (Montevideo)\n\n"
        f"{texto or '(mensaje sin texto)'}\n\n"
        f"Abrir la conversación: {link}\n"
    )
    return _send_estado(to_email, asunto, cuerpo_html, text=texto_plano) == "ok"


# _LOGO es la version clara, pensada para el header navy de _layout. Sobre el
# fondo blanco de este mail se ve lavada y casi ilegible, asi que la firma usa
# la version oscura.
_LOGO_FIRMA = "https://raw.githubusercontent.com/Scalerics-org/scalerics-assets/main/logo_full.png"
_REMITENTE_LEADS = "Scalerics <contacto@scalerics.com>"
_CALENDLY = "https://calendly.com/scalerics/consultoriagratuita"
_TELEFONO = "+598 97 250 713"

# El ultimo contacto de la vida del lead es el ultimo de la tabla de la
# secuencia, no un 7 escrito aparte: si se agrega un dia a la tabla, el mail que
# dice "no te escribimos mas" se corre solo al contacto nuevo. Con dos numeros
# desacoplados, el 7 seguiria despidiendose y despues saldrian dos mails mas.
_CONTACTO_FINAL = TOTAL_CONTACTOS

# Los cuatro valores que ofrece el formulario de Meta. Vienen como
# 'crear_mi_ecommerce', y a veces ya con los guiones bajos cambiados por
# espacios (leads_a_recordar los limpia): se normaliza para aceptar los dos.
# Sin esto, el mas frecuente de la base arma "buscabas crear mi ecommerce para
# Zsoul", con un "mi" en primera persona dentro de una frase dirigida al lector.
_FRASES_RUBRO = {
    "crear_mi_ecommerce":   "querías crear tu ecommerce",
    "automatizaciones":     "buscabas automatizaciones",
    "una_nueva_página_web": "buscabas una nueva página web",
    "un_software_a_medida": "buscabas un software a medida",
}


def _clave_rubro(rubro: str) -> str:
    return " ".join(rubro.lower().split()).replace(" ", "_")


def _frase_rubro(rubro: str) -> str:
    """Frase redactada para los rubros conocidos; el texto crudo para el resto."""
    conocida = _FRASES_RUBRO.get(_clave_rubro(rubro))
    if conocida:
        return conocida
    return f"buscabas {html.escape(rubro.strip())}"


# Que dice el mail que hacemos. Va uno por rubro a proposito: mandarle el parrafo
# de tiendas online a alguien que pidio automatizaciones delata el envio masivo,
# que es exactamente lo que este mail intenta no parecer.
_PARRAFOS_VALOR = {
    "crear_mi_ecommerce":
        "Hacemos tiendas online que venden de verdad: catálogo propio, cobros con "
        "Mercado Pago, envíos configurados y un panel para que cargues productos "
        "vos, sin depender de nadie. En semanas, no en meses.",
    "automatizaciones":
        "Automatizamos lo que hoy te come el día: pedidos, stock, respuestas a "
        "clientes, reportes que armás a mano. Se conecta con lo que ya usás y "
        "queda andando solo. En semanas, no en meses.",
    "una_nueva_página_web":
        "Hacemos sitios que traen consultas, no folletos: rápidos, que se ven bien "
        "en el celular, que aparecen en Google, y con un panel para que los edites "
        "vos. En semanas, no en meses.",
    "un_software_a_medida":
        "Hacemos software a medida para lo que ningún sistema de estante resuelve: "
        "tu operativa, tus reglas, tu gente. Arrancamos por lo que más te frena y "
        "lo ponemos a andar en semanas, no en meses.",
}

_PARRAFO_VALOR_GENERICO = (
    "Hacemos páginas web, tiendas online, automatizaciones y software a medida. "
    "Arrancamos por lo que más te esté frenando y lo ponemos a andar en semanas, "
    "no en meses."
)


def _parrafo_valor(rubro: str) -> str:
    return _PARRAFOS_VALOR.get(_clave_rubro(rubro), _PARRAFO_VALOR_GENERICO)


# _FRASES_RUBRO trae la frase con verbo ("buscabas un software a medida"), que
# sirve para "Dejaste tus datos porque {frase}". Donde la oracion ya tiene su
# propio verbo hace falta el sustantivo solo, o sale "Te llamamos por lo que
# buscabas un software a medida", que es un trabalenguas.
_SUSTANTIVOS_RUBRO = {
    "crear_mi_ecommerce":   "una tienda online",
    "automatizaciones":     "automatizaciones",
    "una_nueva_página_web": "una nueva página web",
    "un_software_a_medida": "un software a medida",
}


def _sustantivo_rubro(rubro: str) -> str:
    return _SUSTANTIVOS_RUBRO.get(_clave_rubro(rubro), "")


# Como trabajamos. Va SOLO donde el lector todavia no lo vivio: el primer
# contacto de la secuencia fria, y los de 'interesado' y 'llamar_despues'.
# A alguien que ya vio la demo y ya recibio un precio, contarle que "te
# mostramos una demo y recien ahi hablamos de numeros" lo trata de desconocido.
_COMO_TRABAJAMOS = (
    "Trabajamos con comercios y empresas en Uruguay: relevamos el proceso, te "
    "mostramos una demo funcionando, y recién ahí hablamos de números."
)

# Los dos caminos. Nadie tiene que aceptar una reunion para avanzar: quien no
# quiere hablar por telefono responde el mail y sigue igual.
_DOS_CAMINOS = (
    "Agendá 30 minutos acá abajo, o respondé este mail y te enviamos una "
    "propuesta por escrito."
)


# Un texto por contacto. Repetir el mismo parrafo comercial cada trimestre es
# exactamente lo que hace que alguien marque spam, asi que del 2 en adelante los
# mails son cortos y no vuelven a vender.
def _cuerpo_por_contacto(numero: int, apertura: str, valor: str, negocio: str) -> tuple[str, list[str]]:
    """Devuelve (asunto, [parrafos]) para el contacto `numero` de un lead frio.

    `apertura` y `valor` ya vienen escapados por el llamador.

    Del 2 en adelante los mails son cortos y no vuelven a vender: repetir el
    parrafo comercial cada trimestre es lo que hace que alguien marque spam.
    """
    donde = f" para {negocio}" if negocio else ""
    de = f" de {negocio}" if negocio else ""
    por_lo_de = f" por lo de {negocio}" if negocio else ""
    if numero <= 1:
        return (f"{negocio} — cómo lo resolveríamos" if negocio
                else "Cómo resolveríamos tu consulta", [
            "Hola,",
            apertura,
            # Un solo bloque de "por que nosotros" por mail. El parrafo por
            # rubro es mas especifico que _COMO_TRABAJAMOS, asi que en la
            # secuencia fria gana el, y la linea de proceso queda para los
            # estados que no tienen parrafo propio.
            valor,
            _DOS_CAMINOS,
        ])
    if numero == 2:
        return (f"Sobre tu consulta{donde}" if negocio else "Sobre tu consulta a Scalerics", [
            "Hola,",
            f"Te escribimos hace unos días{por_lo_de}. Te dejamos el link de vuelta por si "
            f"te quedó pendiente.",
            "Si preferís, respondé este mail y coordinamos por acá.",
        ])
    if numero == 3:
        return (f"¿Dejamos lo de {negocio} para más adelante?" if negocio
                else "¿Lo dejamos para más adelante?", [
            "Hola,",
            "No tuvimos novedades tuyas, así que por ahora lo dejamos acá.",
            f"Si más adelante retomás el tema{donde}, escribinos y lo vemos.",
        ])
    if numero == 4:
        # "lo para X" no cierra sintacticamente; "lo de X" si.
        return (f"¿Retomamos lo de {negocio}?" if negocio else "¿Retomamos tu consulta?", [
            "Hola,",
            f"Pasó un tiempo desde que nos dejaste tus datos{donde}.",
            "Si el tema volvió a estar sobre la mesa, en 30 minutos te decimos qué se "
            "puede hacer, cuánto sale y en cuánto tiempo.",
            _DOS_CAMINOS,
        ])
    if numero == 5:
        return (f"¿Sigue en pie lo de {negocio}?" if negocio else "¿Sigue en pie tu consulta?", [
            "Hola,",
            # El lead nunca hablo con nadie, dejo sus datos en un formulario, y
            # el contacto 3 ya le dijo "no tuvimos novedades tuyas": este parrafo
            # no puede afirmar una conversacion previa que no existio.
            f"Pasaron varios meses desde que nos dejaste tus datos{donde}.",
            "Si en algún momento retomás el tema, seguimos disponibles: respondé "
            "este mail o agendá acá abajo.",
        ])
    if numero >= _CONTACTO_FINAL:
        return (f"Cerramos lo de {negocio}" if negocio else "Cerramos tu consulta", [
            "Hola,",
            "Este es el último mail que te enviamos: a partir de acá no te "
            "escribimos más.",
            f"Si en algún momento retomás el tema{donde}, el link para agendar "
            f"queda acá abajo y podés escribirnos cuando quieras.",
            "Gracias por el tiempo.",
        ])
    # El anteultimo (numero == 6): mismo tono corto y sin venta que 4 y 5, pero
    # avisando que despues de este queda uno solo.
    return (f"¿Cerramos lo de {negocio}?" if negocio else "¿Cerramos tu consulta?", [
        "Hola,",
        f"Volvemos una vez más por lo{de}: si el tema sigue en pie, es buen "
        f"momento para retomarlo.",
        "Si no tenemos novedades, después de este te escribimos una sola vez "
        "más y cerramos.",
    ])


# El campo "¿cómo se llama tu negocio?" es texto libre de un formulario de Meta
# y lo llena cualquiera. En la base real hay direcciones de mail
# ("Fullprinturuguay@gmail.com"), teléfonos, URLs, respuestas de una letra ("h",
# "."), y párrafos enteros ("Tengo empresa de logística y necesito un software
# para registrar el ingreso, la salida, etc de la mercadería").
#
# Cualquiera de esos metido en "para {negocio}" convierte el mail en algo que
# ninguna empresa mandaría. Es el mismo motivo por el que `name` no se usa para
# saludar, unas líneas más arriba: si el dato no sirve, la frase se arma sin él.
_LARGO_MAXIMO_NEGOCIO = 40


def _negocio_usable(texto: str) -> str:
    """El nombre del negocio si se puede escribir en una frase, o "" si no."""
    t = " ".join((texto or "").split())
    if not t or len(t) < 3 or len(t) > _LARGO_MAXIMO_NEGOCIO:
        return ""
    bajo = t.lower()
    if "@" in t or bajo.startswith(("http://", "https://", "www.")):
        return ""
    letras = sum(c.isalpha() for c in t)
    if letras < sum(c.isdigit() for c in t) or letras < 3:
        return ""
    # Mucha gente contesta otra cosa en ese campo. Dos formas frecuentes:
    # el no-nombre ("No lo se aun...", "hola", ".") y la respuesta en primera
    # persona a una pregunta que nadie hizo ("Soy arquitecta", "Tengo local").
    # Ninguna de las dos se puede escribir despues de "para".
    plano = t.rstrip(".!? ").lower()
    for a, b in (("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ñ","n")):
        plano = plano.replace(a, b)
    if plano in {
        "no lo se aun", "no lo se", "no se", "aun no lo se", "aun no", "todavia no",
        "ninguno", "ninguna", "nada", "hola", "si", "no", "sin nombre", "sinnombre",
        "prueba", "test", "mi negocio", "negocio", "empresa", "a", "x",
    }:
        return ""
    # El verbo en primera persona puede no estar al principio: en la base hay un
    # "Gracias , necesito ayuda" que como prefijo no se detecta. Se busca la
    # palabra entera en cualquier posicion. Un nombre de negocio con "tengo" o
    # "quiero" adentro existe, pero es mucho mas raro que esto, y equivocarse
    # rechazando solo cuesta una frase sin nombre.
    verbos = {"soy", "tengo", "necesito", "quiero", "busco", "dedico",
              "trabajo", "estoy", "hago", "ayuda"}
    if verbos & set(plano.replace(",", " ").split()):
        return ""
    return t


# ── Copy por estado del CRM ──────────────────────────────────────────────────
# Estos cuatro estados no son leads frios: son conversaciones que ya existieron
# y se cortaron en un punto distinto. El mail que sirve para despertar a un
# desconocido es exactamente el que ofende a alguien que ya vio un presupuesto,
# asi que cada estado tiene su propio texto. Las secuencias viven en
# services/secuencia_contactos.py; aca solo se escribe lo que dicen.
def _cuerpo_por_estado(estado: str, numero: int, negocio: str,
                       rubro_txt: str) -> tuple:
    """Devuelve (asunto, [parrafos]) para el contacto `numero` de `estado`.

    Mismo registro que `_cuerpo_por_contacto`: es la misma empresa escribiendo.
    Plural siempre, "Hola," de apertura, y los dos caminos al cerrar.

    `_COMO_TRABAJAMOS` va solo en 'interesado' y 'llamar_despues': son los dos
    estados donde el lead todavia no vio una demo. A los de 'demo_1' y
    'presupuesto_enviado' ya se la mostramos y ya recibieron numeros.

    `negocio` llega escapado o crudo segun para que version se lo pida, y ya
    filtrado por `_negocio_usable`.
    """
    de = f" de {negocio}" if negocio else ""
    para = f" para {negocio}" if negocio else ""
    rubro = _frase_rubro(rubro_txt) if rubro_txt else ""
    sustantivo = _sustantivo_rubro(rubro_txt) if rubro_txt else ""

    if estado == "presupuesto_enviado":
        if numero <= 1:
            return (f"{negocio} — ¿qué te frenó?" if negocio
                    else "¿Qué te frenó del presupuesto?", [
                "Hola,",
                "Te pasamos el presupuesto y no tuvimos respuesta. Suele ser una de "
                "tres: el precio, el momento, o que lo resolviste por otro lado.",
                "Las tres son respuestas válidas y no insistimos con ninguna. Si es "
                "el precio, casi siempre hay una versión más acotada que resuelve lo "
                "mismo para arrancar y se amplía después.",
                "Respondenos con cuál de las tres es y seguimos desde ahí.",
            ])
        return (f"¿Damos por cerrado lo de {negocio}?" if negocio else "¿Lo damos por cerrado?", [
            "Hola,",
            "Última por el presupuesto. Si la respuesta es que no, decínoslo y lo "
            "cerramos: no hace falta explicar nada.",
            "Y si era el momento y no el proyecto, avisanos cuando sea y lo "
            "retomamos donde quedó.",
        ])

    if estado == "demo_1":
        if numero <= 1:
            arranque = ("Hicimos la demo y no llegamos a seguir."
                        if not rubro else
                        f"Hicimos la demo de lo que buscabas{para} y no llegamos a seguir.")
            return (f"{negocio} — quedó pendiente el presupuesto" if negocio
                    else "Quedó pendiente tu presupuesto", [
                "Hola,",
                arranque,
                "El paso que falta es el presupuesto: alcance, precio y plazo. Te lo "
                "enviamos por mail, no hace falta otra reunión.",
                "Respondé este mail y te lo preparamos, o agendá 30 minutos si "
                "preferís repasarlo en vivo.",
            ])
        return (f"{negocio} — el presupuesto" if negocio else "El presupuesto de tu proyecto", [
            "Hola,",
            "Te escribimos por última vez por la demo que hicimos.",
            "Si querés el presupuesto, respondenos y te lo enviamos hoy mismo. Si no, "
            "lo dejamos acá y no te molestamos más.",
        ])

    if estado == "interesado":
        arranque = ("Hablamos hace un tiempo y no llegamos a agendar una reunión."
                    if not sustantivo else
                    f"Cuando hablamos, buscabas {sustantivo}{para}, y no llegamos a "
                    f"agendar una reunión.")
        return (f"{negocio} — cómo lo resolveríamos" if negocio
                else "Retomamos tu consulta", [
            "Hola,",
            arranque,
            _COMO_TRABAJAMOS,
            _DOS_CAMINOS,
        ])

    if estado == "llamar_despues":
        arranque = ("Te llamamos por teléfono y no logramos ubicarte."
                    if not sustantivo else
                    f"Te llamamos porque buscabas {sustantivo}{para}, y no logramos "
                    f"ubicarte.")
        return (f"{negocio} — intentamos comunicarnos" if negocio
                else "Intentamos comunicarnos con vos", [
            "Hola,",
            arranque,
            _COMO_TRABAJAMOS,
            "Si te queda más cómodo por escrito, respondé este mail y seguimos por "
            "acá. Y si preferís hablar, agendá 30 minutos cuando te sirva.",
        ])

    # Estado sin copy propia: no deberia llegar (el filtro de meta_reminders
    # solo deja pasar los que tienen secuencia), pero si llega, que caiga en el
    # texto del lead frio y no que reviente el envio.
    return None


@_tipo_envio("recordatorio_meta")
def send_meta_lead_reminder(to_email: str, negocio: str, rubro: str,
                            unsub_url: str, numero: int = 1,
                            estado: str = "sin_contactar") -> str:
    """Invita al lead a agendar una llamada. Sale de contacto@, no de crm@.

    Devuelve el tri-estado de `_send_estado` ("ok"/"fallo"/"desconocido"), no un
    bool: quien lo llama tiene que poder distinguir "no salio" de "no se", que
    es lo que decide si se reintenta manana o no. Ojo con evaluarlo por
    verdad — "fallo" es un string y es truthy.
    """
    # Negocio y rubro salen del formulario de Meta: los llena cualquiera.
    #
    # El campo `name` NO se usa para saludar. En la base real trae el nombre del
    # negocio o directamente basura ("Petshop | Peluqueria canina | Pet Friendly",
    # "Ji lo lo iwwii8i lo lo lo es bj thjue"), asi que saludar con eso queda
    # peor que no saludar con nada. El negocio, que si viene limpio, se usa en el
    # cuerpo, que es donde suena natural.
    # Filtrado antes de tocar nada: la misma basura arruinaba las dos secuencias
    # (la vieja arma "Sobre tu consulta para Fullprinturuguay@gmail.com" igual de
    # mal que la nueva). Si no sirve queda "", y todas las frases tienen su
    # version sin negocio.
    negocio_txt = _negocio_usable(negocio)
    negocio_esc = html.escape(negocio_txt)
    rubro_txt   = (rubro or "").strip()
    # `numero` sale de la columna `numero` de meta_reminders, que es nullable:
    # un None revienta con TypeError en la primera comparacion (`numero <= 1`) y
    # el mail no sale. Hoy ningun camino lo manda vacio; si alguno lo hiciera,
    # que se trate como el contacto 1 y no que se caiga el envio.
    numero = int(numero or 1)

    if rubro_txt and negocio_txt:
        apertura_txt = (f"Dejaste tus datos porque {_frase_rubro(rubro_txt)} "
                        f"para {negocio_txt}, y todavía estamos a tiempo de tomarlo.")
        apertura_esc = (f"Dejaste tus datos porque {_frase_rubro(rubro_txt)} "
                        f"para {negocio_esc}, y todavía estamos a tiempo de tomarlo.")
    elif rubro_txt:
        apertura_txt = apertura_esc = (
            f"Dejaste tus datos porque {_frase_rubro(rubro_txt)}, y todavía "
            f"estamos a tiempo de tomarlo.")
    else:
        apertura_txt = apertura_esc = ("Dejaste tus datos para que hablemos de tu "
                                       "proyecto, y todavía estamos a tiempo de tomarlo.")

    # Que hacemos, dicho para el rubro que pidio ESTE lead. Es fijo (no viene
    # del formulario), asi que no hace falta escaparlo por separado para cada
    # version: sirve igual para el HTML y para el texto plano.
    valor = _parrafo_valor(rubro_txt)

    # _cuerpo_por_contacto se llama dos veces: una con los valores escapados
    # (para el HTML) y otra con los crudos (para el texto plano). El asunto
    # real del mail sale de la version cruda: escapar negocio_esc metería
    # entidades como &amp; en el subject, que ahi no tienen sentido.
    por_estado_html = _cuerpo_por_estado(estado, numero, negocio_esc, rubro_txt)
    if por_estado_html is not None:
        _, parrafos_html = por_estado_html
        asunto, parrafos_texto = _cuerpo_por_estado(estado, numero, negocio_txt, rubro_txt)
    else:
        _, parrafos_html = _cuerpo_por_contacto(numero, apertura_esc, valor, negocio_esc)
        asunto, parrafos_texto = _cuerpo_por_contacto(numero, apertura_txt, valor, negocio_txt)
    asunto = asunto.replace(chr(10), " ").replace(chr(13), " ")

    # Membrete arriba y firma en texto abajo: el mail tiene que verse de la
    # empresa sin caer en la tarjeta con boton de color, que es el molde de una
    # campana. Un solo logo, arriba: con el de la firma tambien, el nombre y el
    # telefono se pisaban cuando el cliente no cargaba las imagenes.
    estilo_p = "margin:0 0 16px;font-size:15px;line-height:1.6;color:#1a1a1a"
    parrafos_render = "\n".join(
        f'    <p style="{estilo_p}">{p}</p>' for p in parrafos_html
    )
    cuerpo_html = f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:28px;background:#ffffff">
  <div style="max-width:520px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif">
    <img src="{_LOGO_FIRMA}" alt="Scalerics" width="130"
         style="display:block;width:130px;height:auto;margin-bottom:24px">
{parrafos_render}
    <p style="{estilo_p}">
      <a href="{_CALENDLY}" style="color:#0069a3">Agendar una llamada</a>
    </p>
    <p style="margin:28px 0 0;padding-top:16px;border-top:1px solid #e6e6e6;
              font-size:14px;line-height:1.7;color:#1a1a1a">
      <strong>Scalerics</strong><br>
      {_TELEFONO}<br>
      <a href="https://scalerics.com" style="color:#0069a3">scalerics.com</a>
    </p>
    <p style="margin:20px 0 0;font-size:12px;line-height:1.5;color:#8a8a8a">
      <a href="{unsub_url}" style="color:#8a8a8a">No quiero recibir más estos mails</a>
    </p>
  </div>
</body>
</html>"""

    cuerpo_texto = (
        "\n\n".join(parrafos_texto) + f"\n{_CALENDLY}\n\n"
             + f"\n\nScalerics · {_TELEFONO}\nhttps://scalerics.com"
        f"No quiero recibir más estos mails: {unsub_url}\n"
    )

    return _send_estado(
        to_email, asunto, cuerpo_html,
        from_email=_REMITENTE_LEADS,
        headers={
            "List-Unsubscribe": f"<{unsub_url}>",
            # El par que pide RFC 8058: sin el segundo, Gmail no muestra su
            # boton nativo de baja. La URL tiene que aceptar POST.
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
        text=cuerpo_texto,
    )


# ─── Discovery: correo en frio a comercios con sitio web ────────────────────

# El correo en frio NO puede salir de aca: es el dominio de los clientes.
_DOMINIO_PRINCIPAL = "scalerics.com"


# ─── Discovery: la linea de apertura, una por rubro ─────────────────────────

# La apertura NO puede afirmar nada del negocio del comercio: no miramos su
# sitio, solo sabemos que existe. Cualquier frase que suene a insight es mentira
# y se nota. Lo que si sabemos es el RUBRO, asi que cada linea habla de como
# trabaja ese rubro —siempre en condicional, "si hacés tal cosa"— y nunca de
# ellos.
_LINEAS_POR_RUBRO = {
    "peluqueria":
        "Si los turnos se agendan por WhatsApp y los recordatorios los manda "
        "alguien a mano, eso se automatiza.",
    "gimnasio":
        "Si las cuotas y los vencimientos los controlás a mano, y los avisos de "
        "pago salen uno por uno, eso se automatiza.",
    "veterinaria":
        "Si los recordatorios de vacunas y los turnos los llevás a mano, y la "
        "historia de cada paciente está en papel, eso se automatiza.",
    "odontologia":
        "Si los turnos se agendan por teléfono y los recordatorios los manda "
        "alguien a mano, eso se automatiza.",
    "inmobiliaria":
        "Si cargás las propiedades a mano en el sitio y en los portales, o "
        "contestás las mismas consultas todos los días, eso se automatiza.",
    "repuestos":
        "Si el catálogo y los precios los actualizás a mano, y las consultas de "
        "compatibilidad las contestás una por una, eso se automatiza.",
    "automotora":
        "Si el stock lo actualizás a mano en el sitio y en los portales, o las "
        "consultas de financiación las contestás una por una, eso se automatiza.",
    "ferreteria":
        "Si la lista de precios la mandás por WhatsApp y los pedidos los pasás a "
        "mano al sistema, eso se automatiza.",
}

# El mismo rubro escrito distinto tiene que caer en la misma linea. Solo se
# mapea lo inequivoco: "Venta" y "Agencia" salen de las queries de autos pero
# podrian ser cualquier cosa manana, asi que van al generico a proposito.
_ALIAS_RUBRO = {
    "hair salon": "peluqueria",
    "hairdresser": "peluqueria",
    "barberia": "peluqueria",
    "concesionaria": "automotora",
    "compraventa": "automotora",
    "odontologo": "odontologia",
    "dentista": "odontologia",
    "clinica dental": "odontologia",
    "veterinario": "veterinaria",
    "tienda de herramientas": "ferreteria",
    "tienda de materiales para la construccion": "ferreteria",
    "proveedor de materiales de construccion": "ferreteria",
    "corralon": "ferreteria",
    "mayorista": "ferreteria",
    "proveedor mayorista de alimentos": "ferreteria",
    "distribuidor de comestibles": "ferreteria",
}

# El generico se usa tanto como los especificos: de 44 rubros distintos en la
# base, ocho concentran el volumen y el resto son puñados.
_LINEA_GENERICA = (
    "Si hay algo de la operativa que hoy se hace a mano —pedidos, turnos, "
    "precios, respuestas que se repiten—, eso se automatiza."
)


def _sin_tildes(t: str) -> str:
    return (t.replace("á", "a").replace("é", "e").replace("í", "i")
             .replace("ó", "o").replace("ú", "u").replace("ñ", "n"))


# Con 48 rubros no se pueden escribir 48 textos sin que se noten hechos a
# desgano, y la generica no dice nada. La familia es el punto medio: agrupa por
# QUE SE HACE A MANO ahi, no por industria. A una odontologia y a una
# veterinaria les duele lo mismo (la agenda); a una ferreteria y a una casa de
# repuestos tambien (la lista de precios). Ver services/rubros.py.
_LINEAS_POR_FAMILIA = {
    "turnos":
        "Si los turnos se agendan por tel&eacute;fono o WhatsApp y los "
        "recordatorios los manda alguien a mano, eso se automatiza.",
    "stock_precios":
        "Si el cat&aacute;logo y la lista de precios los actualiz&aacute;s a "
        "mano, y las consultas de stock las contest&aacute;s una por una, eso "
        "se automatiza.",
    "pedidos":
        "Si los pedidos entran por WhatsApp y despu&eacute;s alguien los pasa a "
        "mano al sistema, eso se automatiza.",
    "expedientes":
        "Si cada caso es una carpeta y para saber en qu&eacute; qued&oacute; hay "
        "que preguntarle a alguien, eso se automatiza.",
    "reservas":
        "Si la disponibilidad la llev&aacute;s en una planilla y cada consulta "
        "se contesta a mano, eso se automatiza.",
    "cuotas":
        "Si las cuotas y los vencimientos los control&aacute;s a mano, y los "
        "avisos de pago salen uno por uno, eso se automatiza.",
    "presupuestos":
        "Si cada presupuesto se arma de cero en una planilla y despu&eacute;s "
        "nadie sabe en qu&eacute; qued&oacute;, eso se automatiza.",
}


def _familia_del_rubro(clave: str) -> str:
    """La familia de copy del rubro, segun services/rubros.py."""
    # Import local: rubros.py no depende de nada, pero este modulo se importa
    # desde media aplicacion y no vale la pena arrastrarlo en cada arranque.
    from services.rubros import RUBROS
    for r in RUBROS:
        if r.clave == clave:
            return r.familia
    return ""


def _linea_de_rubro(rubro: str) -> str:
    """La linea de apertura del rubro: la propia, la de su familia, o la generica.

    En ese orden a proposito: la propia es mas especifica que la de la familia,
    y la generica es solo una red para un rubro que no este en la lista.

    El texto que llega puede ser la clave canonica (lo que pone el scraper) o lo
    que dijo Google ("Agencia inmobiliaria", "Agentes inmobiliarios"), asi que
    primero se normaliza con rubro_de_texto. Sin ese paso, las filas viejas
    caian todas en la generica: los primeros 30 mails salieron asi.
    """
    from services.rubros import rubro_de_texto

    crudo = _sin_tildes(" ".join((rubro or "").lower().split()))
    clave = _ALIAS_RUBRO.get(crudo, crudo)
    if clave not in _LINEAS_POR_RUBRO:
        clave = rubro_de_texto(clave) or clave
    if clave in _LINEAS_POR_RUBRO:
        return _LINEAS_POR_RUBRO[clave]
    return _LINEAS_POR_FAMILIA.get(_familia_del_rubro(clave), _LINEA_GENERICA)

def _cuerpo_discovery(numero: int, negocio: str, rubro: str) -> tuple[str, list[str]]:
    """Devuelve (asunto, [parrafos]) para el contacto `numero`.

    `negocio` y `rubro` ya vienen escapados por el llamador.

    Son dos y el segundo cierra. En frio, el tercer toque rinde poco y cuesta
    reputacion: cada marca de spam se paga con entrega, y la entrega es la
    misma que usa el correo con clientes.
    """
    # "de {negocio}" leia como si el mail fuera DEL comercio; va "para".
    para = f" para {negocio}" if negocio else ""
    if numero <= 1:
        return (f"Una idea para {negocio}" if negocio else "Una idea para tu negocio", [
            "Hola,",
            # La apertura habla del RUBRO, no del comercio: es lo unico concreto
            # que sabemos sin mentir. Ver _linea_de_rubro.
            _linea_de_rubro(rubro),
            "Somos Scalerics, una software factory uruguaya. Hacemos p&aacute;ginas y "
            "tiendas online, y tambi&eacute;n automatizamos lo que hoy se hace a mano: "
            "pedidos, seguimientos, reportes que se arman de a uno. Y software a medida "
            "para lo que ning&uacute;n sistema de estante resuelve.",
            (f"Si algo de eso te sirve para {negocio}, respond&eacute; este mail y lo "
             f"charlamos en 20 minutos." if negocio else
             "Si algo de eso te sirve, respond&eacute; este mail y lo charlamos en "
             "20 minutos."),
        ])
    return (f"&Uacute;ltimo mail{para}" if negocio else "&Uacute;ltimo mail de Scalerics", [
        "Hola,",
        "Te escribimos hace una semana y no queremos insistir m&aacute;s de la cuenta, "
        "as&iacute; que este es el &uacute;ltimo: no te escribimos m&aacute;s.",
        "Si en alg&uacute;n momento te sirve automatizar algo de la operativa, "
        "respondenos y lo vemos. Preguntamos una sola vez.",
        "Gracias por el tiempo.",
    ])


@_tipo_envio("discovery")
def send_discovery_email(to_email: str, negocio: str, rubro: str,
                         unsub_url: str, numero: int = 1) -> str:
    """Mail en frio a un comercio de la cohorte de discovery.

    Sale del subdominio de `DISCOVERY_FROM_EMAIL`, NO del dominio principal: el
    correo en frio genera quejas por bien hecho que este, y esas quejas no
    pueden degradar la entrega de los recordatorios de Meta ni la del correo con
    clientes. Si la variable no esta, no manda y devuelve "fallo": una
    configuracion a medias no puede terminar mandando en frio desde
    scalerics.com.
    """
    remitente = os.environ.get("DISCOVERY_FROM_EMAIL", "").strip()
    if not remitente:
        logger.warning("DISCOVERY_FROM_EMAIL sin configurar: no se manda nada")
        return "fallo"

    # El dominio principal esta vedado a proposito. Un dedo torcido en el
    # `flyctl secrets set` pondria el correo en frio en el mismo dominio con el
    # que se le escribe a los clientes y por el que salen los recordatorios de
    # Meta, que es exactamente lo que este modulo existe para evitar.
    dominio = remitente.rsplit("@", 1)[-1].strip(" <>").lower()
    if not dominio or "." not in dominio:
        logger.error(f"DISCOVERY_FROM_EMAIL sin dominio valido ({remitente!r}): no se manda nada")
        return "fallo"
    if dominio == _DOMINIO_PRINCIPAL:
        logger.error(
            f"DISCOVERY_FROM_EMAIL apunta al dominio principal ({dominio}): no se manda "
            f"nada. El correo en frio tiene que salir de un subdominio propio."
        )
        return "fallo"

    numero = int(numero or 1)
    # name y category salen de Google Maps: los escribe cualquiera.
    negocio_txt = (negocio or "").strip()
    rubro_txt = (rubro or "").strip()

    asunto, parrafos = _cuerpo_discovery(numero, html.escape(negocio_txt),
                                         html.escape(rubro_txt))
    asunto_txt, parrafos_txt = _cuerpo_discovery(numero, negocio_txt, rubro_txt)
    asunto_txt = html.unescape(asunto_txt)
    parrafos_txt = [html.unescape(p) for p in parrafos_txt]

    estilo_p = "margin:0 0 14px;font-size:15px;line-height:1.6;color:#1c2b40"
    cuerpo_html = "".join(f'<p style="{estilo_p}">{p}</p>' for p in parrafos)
    # El <meta charset> no es decorativo: las lineas por rubro traen tildes de
    # verdad (no entidades) y sin declararlo un cliente de correo lee los bytes
    # como latin-1 y muestra "cargAs" en vez de "cargas".
    html_mail = f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:24px;background:#f1f5f9">
  <div style="max-width:520px;margin:0 auto;background:#fff;border-radius:10px;padding:32px">
    <img src="{_LOGO_FIRMA}" alt="Scalerics" style="height:24px;margin-bottom:20px">
    {cuerpo_html}
    <p style="{estilo_p};margin-top:24px"><strong>Scalerics</strong><br>{_TELEFONO}<br>
      <a href="https://scalerics.com" style="color:#0069a3">scalerics.com</a></p>
    <p style="font-size:12px;color:#94a3b8;margin:24px 0 0">
      Si no quer&eacute;s recibir m&aacute;s, <a href="{unsub_url}" style="color:#94a3b8">dale de baja ac&aacute;</a>.
    </p>
  </div>
</body></html>"""

    texto = ("\n\n".join(parrafos_txt)
             + f"\n\nScalerics · {_TELEFONO}\nhttps://scalerics.com"
             + f"\n\nSi no querés recibir más: {unsub_url}")

    return _send_estado(
        to_email, asunto_txt, html_mail,
        from_email=remitente,
        headers={
            "Reply-To": "contacto@scalerics.com",
            "List-Unsubscribe": f"<{unsub_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
        text=texto,
    )


# ── LinkedIn ────────────────────────────────────────────────────────────────────

_DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


def _bloque_borrador(b: dict, base_url: str) -> str:
    aviso = ""
    if b.get("aviso"):
        aviso = (
            '<div style="background:#fff7ed;border-left:3px solid #f97316;'
            'padding:10px 14px;margin:0 0 14px;font-size:13px;color:#7c2d12">'
            f'{b["aviso"]}</div>'
        )

    texto_html = (b.get("texto") or "").replace("\n", "<br>")
    marcar = f'{base_url}/api/linkedin/marcar?token={b["marcar_token"]}'
    etiqueta = "Trabajo propio" if b.get("tipo") == "trabajo" else "Educativo"

    return f"""
    <div style="border:1px solid #e2e8f0;border-radius:8px;padding:18px;margin:0 0 22px">
      <div style="font-size:11px;letter-spacing:.08em;text-transform:uppercase;
                  color:#64748b;margin:0 0 12px">{etiqueta}</div>
      {aviso}
      <div style="background:#f8fafc;border-radius:6px;padding:16px;font-family:
                  ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;
                  line-height:1.7;color:#0f172a;white-space:pre-wrap">{texto_html}</div>
      <div style="font-size:12px;color:#64748b;margin:14px 0 0">
        Datos usados: {b.get('fuente_desc', '')}
      </div>
      <div style="margin:14px 0 0">
        <a href="{marcar}" style="font-size:13px;color:#0088cc;text-decoration:none">
          Ya publiqué este</a>
        <span style="font-size:12px;color:#94a3b8"> &middot; no publica nada, solo lo marca</span>
      </div>
    </div>"""


@_tipo_envio("linkedin")
def send_linkedin_drafts(to: str, borradores: list, base_url: str,
                         aviso_cooldown: bool = False) -> bool:
    from datetime import datetime

    hoy = datetime.now()
    dia = _DIAS[hoy.weekday()]
    cuantos = len(borradores)
    palabra = "borrador" if cuantos == 1 else "borradores"
    subject = f"{cuantos} {palabra} para LinkedIn - {dia} {hoy.day}/{hoy.month}"

    cuerpo = "".join(_bloque_borrador(b, base_url) for b in borradores)
    if aviso_cooldown:
        cuerpo += _muted(
            "Se reuso un tema educativo antes de cumplir los 180 dias porque no "
            "quedaban temas libres. Conviene sembrar temas nuevos en linkedin_temas."
        )

    attachments = [
        {"filename": f"linkedin-{b['id']}.png", "content": b["png_b64"]}
        for b in borradores if b.get("png_b64")
    ]

    html = _layout(
        badge="LinkedIn",
        title=f"{cuantos} {palabra} listos",
        body=cuerpo,
    )
    return _send(to, subject, html, attachments=attachments)


@_tipo_envio("linkedin")
def send_linkedin_failure(to: str, motivo: str) -> bool:
    html = _layout(
        badge="LinkedIn",
        title="No salieron los borradores",
        body=(
            '<p style="font-size:14px;color:#334155;line-height:1.6">'
            f"No se pudo generar ningun borrador esta vez. Motivo: {motivo}.</p>"
        ),
    )
    return _send(to, "No salieron los borradores de LinkedIn", html)
