from unittest.mock import MagicMock, patch

import pytest
import requests

from services.email_service import _send, _send_estado, send_meta_lead_reminder


def _capturar():
    return patch("services.email_service._send_estado", return_value="ok")


# ── Tri-estado del envio ───────────────────────────────────────────────────────

def _respuesta(status=200):
    r = MagicMock()
    if status >= 400:
        r.raise_for_status.side_effect = requests.exceptions.HTTPError(f"{status}")
    return r


@pytest.mark.parametrize("efecto, esperado", [
    (None, "ok"),
    (requests.exceptions.HTTPError("422"), "fallo"),
    (requests.exceptions.ConnectionError("no hay red"), "fallo"),
    (requests.exceptions.Timeout("tardo mas de 10s"), "desconocido"),
    (ValueError("cualquier otra cosa"), "desconocido"),
])
def test_el_envio_distingue_fallo_de_no_se(monkeypatch, efecto, esperado):
    """Un timeout NO es un fallo: Resend pudo haber aceptado el mail igual."""
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    if isinstance(efecto, requests.exceptions.HTTPError):
        post = MagicMock(return_value=_respuesta(422))
    elif efecto is None:
        post = MagicMock(return_value=_respuesta(200))
    else:
        post = MagicMock(side_effect=efecto)

    with patch("services.email_service.requests.post", post):
        assert _send_estado("a@b.com", "asunto", "<p>x</p>") == esperado


@pytest.mark.parametrize("efecto, esperado", [
    (None, True),
    (requests.exceptions.HTTPError("422"), False),
    (requests.exceptions.Timeout("tardo"), False),
])
def test_send_sigue_devolviendo_bool_para_las_llamadas_de_siempre(monkeypatch, efecto, esperado):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    if efecto is None:
        post = MagicMock(return_value=_respuesta(200))
    elif isinstance(efecto, requests.exceptions.HTTPError):
        post = MagicMock(return_value=_respuesta(422))
    else:
        post = MagicMock(side_effect=efecto)

    with patch("services.email_service.requests.post", post):
        r = _send("a@b.com", "asunto", "<p>x</p>")

    assert r is esperado, "las 6 llamadas existentes esperan un bool, no el tri-estado"


def test_el_mail_sale_de_contacto_y_lleva_baja():
    with _capturar() as enviar:
        send_meta_lead_reminder(
            "lead@ejemplo.com", "Romyna", "RP Estudio Juridico",
            "una nueva pagina web", "https://crm/baja/abc123",
        )

    assert enviar.called
    kwargs = enviar.call_args.kwargs
    assert kwargs["from_email"] == "Scalerics <contacto@scalerics.com>"
    assert kwargs["headers"]["List-Unsubscribe"] == "<https://crm/baja/abc123>"

    html = enviar.call_args.args[2]
    assert "https://crm/baja/abc123" in html, "el link de baja va visible en el cuerpo"
    assert "calendly.com/scalerics/consultoriagratuita" in html
    assert "+598 97 250 713" in html


def test_personaliza_con_lo_que_pidio_el_lead():
    with _capturar() as enviar:
        send_meta_lead_reminder(
            "lead@ejemplo.com", "Romyna", "RP Estudio Juridico",
            "una nueva pagina web", "https://crm/baja/x",
        )

    html = enviar.call_args.args[2]
    assert "RP Estudio Juridico" in html
    assert "una nueva pagina web" in html


def _html_del_recordatorio(**kw):
    datos = dict(to_email="lead@ejemplo.com", lead_name="Romyna",
                 negocio="Zsoul", rubro="una nueva pagina web",
                 unsub_url="https://crm/baja/x")
    datos.update(kw)
    with _capturar() as enviar:
        send_meta_lead_reminder(**datos)
    return enviar.call_args


# ── I5: no es una notificacion de sistema ──────────────────────────────────────

def test_el_recordatorio_no_se_presenta_como_notificacion_automatica():
    """Es un mail cuyo unico objetivo es que la persona conteste o agende."""
    html = _html_del_recordatorio().args[2]

    assert "No responder" not in html
    assert "Notificación automática" not in html


def test_las_otras_plantillas_conservan_el_pie_de_sistema():
    from services.email_service import send_reset_email

    with patch("services.email_service._send_estado", return_value="ok") as enviar:
        send_reset_email("admin@ejemplo.com", "https://crm/reset/abc")

    html = enviar.call_args.args[2]
    assert "No responder este mail" in html
    assert "Notificación automática" in html


def test_la_firma_usa_el_logo_oscuro_y_no_el_claro():
    """_LOGO es la version clara, para el header navy de _layout. Sobre el fondo
    blanco de este mail se ve lavada: en Gmail parecia un logo roto."""
    from services.email_service import _LOGO, _LOGO_FIRMA

    html = _html_del_recordatorio().args[2]

    assert html.count(_LOGO_FIRMA) == 1
    assert _LOGO not in html


# ── I6: el par de cabeceras que Gmail necesita ─────────────────────────────────

def test_lleva_el_par_de_cabeceras_de_baja_en_un_click():
    kwargs = _html_del_recordatorio().kwargs

    assert kwargs["headers"]["List-Unsubscribe"] == "<https://crm/baja/x>"
    assert kwargs["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click", (
        "sin esta, Gmail no muestra su boton nativo de baja"
    )


# ── Texto que ve el lector ─────────────────────────────────────────────────────

@pytest.mark.parametrize("rubro, esperado, prohibido", [
    ("crear_mi_ecommerce", "querías crear tu ecommerce", "mi ecommerce"),
    ("crear mi ecommerce", "querías crear tu ecommerce", "mi ecommerce"),
    ("automatizaciones", "buscabas automatizaciones", None),
    ("una_nueva_página_web", "buscabas una nueva página web", None),
    ("una nueva página web", "buscabas una nueva página web", None),
    ("un_software_a_medida", "buscabas un software a medida", None),
    ("un software a medida", "buscabas un software a medida", None),
])
def test_cada_rubro_conocido_tiene_su_frase(rubro, esperado, prohibido):
    html = _html_del_recordatorio(rubro=rubro).args[2]

    assert esperado in html
    if prohibido:
        assert prohibido not in html, "quedaba un 'mi' en primera persona"


def test_un_rubro_desconocido_cae_en_el_texto_generico():
    html = _html_del_recordatorio(rubro="algo que puso el lead a mano").args[2]

    assert "buscabas algo que puso el lead a mano" in html


def test_el_texto_que_ve_el_lector_lleva_tildes():
    html = _html_del_recordatorio().args[2]

    assert "cómodo" in html
    assert "respondé" in html
    assert "más adelante" in html
    assert "No quiero recibir más" in html
    assert "comodo" not in html.replace("cómodo", "")


def test_el_nombre_del_formulario_no_se_usa_en_ningun_lado():
    """En la base real ese campo trae el nombre del negocio o basura
    ('Petshop | Peluqueria canina | Mascotas', 'Ji lo lo iwwii8i lo lo lo').
    Saludar con eso queda peor que no saludar: el asunto y el cuerpo salen del
    negocio, que si viene limpio."""
    llamada = _html_del_recordatorio(
        lead_name="Petshop | Peluquería canina | Pet Friendly | Mascotas",
        negocio="Animal Petshop")

    assert llamada.args[1] == "Sobre tu consulta para Animal Petshop"
    assert "Peluquería canina" not in llamada.args[2]
    assert "Petshop |" not in llamada.args[2]


def test_sin_negocio_el_asunto_no_queda_colgado():
    llamada = _html_del_recordatorio(lead_name="Lo que sea", negocio="")

    assert llamada.args[1] == "Sobre tu consulta a Scalerics"


def test_escapa_la_entrada_del_formulario():
    with _capturar() as enviar:
        send_meta_lead_reminder(
            "lead@ejemplo.com", "Un Nombre", "<script>alert(1)</script>Neg<ocio>",
            "web", "https://crm/baja/x",
        )

    html = enviar.call_args.args[2]
    assert "<script>" not in html, "el negocio lo llena cualquiera en internet"
    assert "&lt;ocio&gt;" in html, "el negocio va escapado en el cuerpo"


def test_el_recordatorio_lleva_version_en_texto_plano():
    """Un mail que va solo en HTML es una de las senales que empujan a
    Promociones. El primer envio de prueba cayo justo ahi."""
    with _capturar() as enviar:
        send_meta_lead_reminder(
            "lead@ejemplo.com", "Un Nombre", "RP Estudio",
            "una_nueva_página_web", "https://crm/baja/tok",
        )

    texto = enviar.call_args.kwargs["text"]

    assert texto, "tiene que ir la parte de texto plano"
    assert "<" not in texto, "la parte de texto no lleva HTML"
    assert "RP Estudio" in texto
    assert "https://calendly.com/scalerics/consultoriagratuita" in texto
    assert "https://crm/baja/tok" in texto, "la baja tambien tiene que estar en el texto"


def test_las_otras_plantillas_siguen_sin_texto_plano():
    """_send sigue llamando sin `text`: las 6 llamadas de siempre no cambian."""
    import services.email_service as e
    from unittest.mock import patch

    with patch.object(e, "_send_estado", return_value="ok") as enviar:
        e.send_reset_email("a@b.com", "https://crm/reset")

    assert "text" not in enviar.call_args.kwargs
