from unittest.mock import patch

import services.email_service as es


BORRADOR = {
    "id": 1,
    "tipo": "trabajo",
    "texto": "Esta semana armamos la tienda de una bloquera de Durazno.",
    "fuente_desc": "Demo de Bloquera La Cadena (https://lacadena.vercel.app)",
    "aviso": "Confirma con el cliente antes de publicar.",
    "marcar_token": "tok-1",
    "png_b64": "iVBORw0KGgo=",
}


def _capturar():
    return patch.object(es, "_send", return_value=True)


def test_manda_un_mail_con_el_texto_del_borrador():
    with _capturar() as send:
        ok = es.send_linkedin_drafts("a@b.com", [BORRADOR], "https://crm.test")
    assert ok is True
    html = send.call_args.kwargs.get("html") or send.call_args.args[2]
    assert "bloquera de Durazno" in html


def test_el_asunto_dice_cuantos_borradores_van():
    with _capturar() as send:
        es.send_linkedin_drafts("a@b.com", [BORRADOR], "https://crm.test")
    asunto = send.call_args.kwargs.get("subject") or send.call_args.args[1]
    assert "1 borrador" in asunto

    with _capturar() as send:
        es.send_linkedin_drafts("a@b.com", [BORRADOR, dict(BORRADOR, id=2)], "https://crm.test")
    asunto = send.call_args.kwargs.get("subject") or send.call_args.args[1]
    assert "2 borradores" in asunto


def test_muestra_el_aviso_de_autorizacion():
    with _capturar() as send:
        es.send_linkedin_drafts("a@b.com", [BORRADOR], "https://crm.test")
    html = send.call_args.kwargs.get("html") or send.call_args.args[2]
    assert "Confirma con el cliente" in html


def test_sin_aviso_no_aparece_el_bloque():
    sin_aviso = dict(BORRADOR, aviso="")
    with _capturar() as send:
        es.send_linkedin_drafts("a@b.com", [sin_aviso], "https://crm.test")
    html = send.call_args.kwargs.get("html") or send.call_args.args[2]
    assert "Confirma con el cliente" not in html


def test_adjunta_el_png_cuando_hay():
    with _capturar() as send:
        es.send_linkedin_drafts("a@b.com", [BORRADOR], "https://crm.test")
    adjuntos = send.call_args.kwargs["attachments"]
    assert len(adjuntos) == 1
    assert adjuntos[0]["filename"].endswith(".png")
    assert adjuntos[0]["content"] == "iVBORw0KGgo="


def test_sin_png_no_adjunta_nada():
    sin_png = dict(BORRADOR, png_b64="")
    with _capturar() as send:
        es.send_linkedin_drafts("a@b.com", [sin_png], "https://crm.test")
    assert send.call_args.kwargs["attachments"] == []


def test_el_link_de_marcar_lleva_el_token():
    with _capturar() as send:
        es.send_linkedin_drafts("a@b.com", [BORRADOR], "https://crm.test")
    html = send.call_args.kwargs.get("html") or send.call_args.args[2]
    assert "https://crm.test/api/linkedin/marcar?token=tok-1" in html


def test_aviso_de_cooldown_aparece_solo_si_se_pide():
    with _capturar() as send:
        es.send_linkedin_drafts("a@b.com", [BORRADOR], "https://crm.test", aviso_cooldown=True)
    html = send.call_args.kwargs.get("html") or send.call_args.args[2]
    assert "temas" in html.lower()

    with _capturar() as send:
        es.send_linkedin_drafts("a@b.com", [BORRADOR], "https://crm.test")
    html = send.call_args.kwargs.get("html") or send.call_args.args[2]
    assert "180" not in html


def test_mail_de_fallo():
    with _capturar() as send:
        ok = es.send_linkedin_failure("a@b.com", "los dos borradores fallaron la validacion")
    assert ok is True
    html = send.call_args.kwargs.get("html") or send.call_args.args[2]
    assert "fallaron la validacion" in html


def test_send_sin_attachments_no_manda_la_clave():
    with patch.dict("os.environ", {"RESEND_API_KEY": "k"}), \
         patch("services.email_service.requests.post") as post:
        post.return_value.status_code = 200
        es._send("a@b.com", "asunto", "<p>hola</p>")
    cuerpo = post.call_args.kwargs["json"]
    assert "attachments" not in cuerpo


def test_send_con_attachments_los_pasa():
    with patch.dict("os.environ", {"RESEND_API_KEY": "k"}), \
         patch("services.email_service.requests.post") as post:
        post.return_value.status_code = 200
        es._send("a@b.com", "asunto", "<p>hola</p>",
                 attachments=[{"filename": "x.png", "content": "AAA"}])
    cuerpo = post.call_args.kwargs["json"]
    assert cuerpo["attachments"][0]["filename"] == "x.png"
