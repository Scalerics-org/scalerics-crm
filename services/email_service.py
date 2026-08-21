import html
import logging
import os

import requests

from services.secuencia_contactos import TOTAL_CONTACTOS

logger = logging.getLogger(__name__)

_LOGO = "https://raw.githubusercontent.com/juantomasetti1/scalerics-assets/main/logo_full_alt.png"
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


# ── Resend sender ───────────────────────────────────────────────────────────────

def _send_estado(to: str, subject: str, html: str, from_email: str | None = None,
                 headers: dict | None = None, text: str | None = None) -> str:
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
        return "desconocido"
    except Exception as e:
        logger.error(f"Failed to send '{subject}' to {to}, no sabemos si salio: {e}")
        return "desconocido"


def _send(to: str, subject: str, html: str, from_email: str | None = None, headers: dict | None = None) -> bool:
    return _send_estado(to, subject, html, from_email=from_email, headers=headers) == "ok"


# ── Public functions ────────────────────────────────────────────────────────────

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
    return _send(to_email, asunto, cuerpo_html)


_GOAL_TYPE_LABELS = {
    "reuniones_agendadas": "reuniones agendadas",
    "leads_contactados": "leads contactados",
    "reuniones_hechas": "reuniones hechas",
    "clientes_cerrados": "clientes cerrados",
    "llamadas_realizadas": "llamadas realizadas",
    "llamadas_contestadas": "llamadas contestadas",
}


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


# _LOGO es la version clara, pensada para el header navy de _layout. Sobre el
# fondo blanco de este mail se ve lavada y casi ilegible, asi que la firma usa
# la version oscura.
_LOGO_FIRMA = "https://raw.githubusercontent.com/juantomasetti1/scalerics-assets/main/logo_full.png"
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


# Un texto por contacto. Repetir el mismo parrafo comercial cada trimestre es
# exactamente lo que hace que alguien marque spam, asi que del 2 en adelante los
# mails son cortos y no vuelven a vender.
def _cuerpo_por_contacto(numero: int, apertura: str, valor: str, negocio: str) -> tuple[str, list[str]]:
    """Devuelve (asunto, [parrafos]) para el contacto `numero`.

    `apertura` y `valor` ya vienen escapados por el llamador.
    """
    donde = f" para {negocio}" if negocio else ""
    if numero <= 1:
        return (f"Sobre tu consulta{donde}" if negocio else "Sobre tu consulta a Scalerics", [
            "Hola,",
            apertura,
            valor,
            "Agendá 30 minutos y salís de la llamada con precio y plazo cerrados.",
        ])
    if numero == 2:
        return (f"Sobre tu consulta{donde}" if negocio else "Sobre tu consulta a Scalerics", [
            "Hola,",
            f"Te escribimos hace unos días{donde}. Te dejamos el link de vuelta por si "
            f"te quedó pendiente.",
            "Si preferís, respondé este mail y coordinamos por acá.",
        ])
    if numero == 3:
        return (f"Sobre tu consulta{donde}" if negocio else "Sobre tu consulta a Scalerics", [
            "Hola,",
            "No tuvimos novedades tuyas, así que por ahora lo dejamos acá.",
            f"Si más adelante retomás el tema{donde}, escribinos y lo vemos.",
        ])
    if numero == 4:
        # "lo para X" no cierra sintacticamente (el asunto queda como una
        # plantilla mal armada); "lo de X" si.
        return (f"¿Retomamos lo de {negocio}?" if negocio else "¿Retomamos tu consulta?", [
            "Hola,",
            f"Pasó un tiempo desde que nos dejaste tus datos{donde}.",
            "Si el tema volvió a estar sobre la mesa, en 30 minutos te decimos qué se "
            "puede hacer, cuánto sale y en cuánto tiempo.",
        ])
    if numero == 5:
        return (f"¿Sigue en pie lo de {negocio}?" if negocio else "¿Sigue en pie tu consulta?", [
            "Hola,",
            # El lead nunca hablo con nadie, dejo sus datos en un formulario, y
            # el contacto 3 ya le dijo "no tuvimos novedades tuyas": este parrafo
            # no puede afirmar una conversacion previa que no existio.
            f"Pasaron varios meses desde que nos dejaste tus datos{donde}.",
            "Si en algún momento retomás el tema, seguimos para ayudarte: "
            "respondé este mail o agendá acá abajo.",
        ])
    if numero >= _CONTACTO_FINAL:
        return (f"Último mail{donde}" if negocio else "Último mail de Scalerics", [
            "Hola,",
            "Este es el último mail que te mandamos: a partir de acá no te "
            "escribimos más.",
            f"Si en algún momento retomás el tema{donde}, el link para agendar "
            f"queda acá abajo y podés escribirnos cuando quieras.",
            "Gracias por el tiempo.",
        ])
    # El anteultimo de la vida del lead (numero == 6): mismo tono corto y sin
    # venta que 4 y 5, pero insinuando que despues de este queda uno solo.
    return (f"Nos queda un mail más{donde}" if negocio else "Nos queda un mail más", [
        "Hola,",
        f"Este es el anteúltimo mail que te mandamos{donde}: después de este "
        f"te queda uno solo.",
        "Si el tema sigue en pie, es buen momento para retomarlo antes de que "
        "dejemos de escribirte.",
    ])


def send_meta_lead_reminder(to_email: str, negocio: str, rubro: str,
                            unsub_url: str, numero: int = 1) -> str:
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
    negocio_txt = (negocio or "").strip()
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
