"""El lead de Meta arranca la conversacion por WhatsApp.

De los 219 leads de Meta acumulados, 206 quedaron en "sin_contactar". Este
enganche existe para que los nuevos no terminen igual.
"""
import os
from unittest.mock import patch, MagicMock

from routes.meta import _arrancar_conversacion_wa


def _entorno(**extra):
    base = {"WA_SERVICE_URL": "http://bot.internal:8080", "WA_API_KEY": "clave", "META_WA_AUTO": "true"}
    base.update(extra)
    return patch.dict(os.environ, base, clear=False)


def test_le_pasa_el_lead_al_bot():
    with _entorno(), patch("routes.meta.requests.post") as post:
        post.return_value = MagicMock(status_code=200, json=lambda: {"status": "queued"})
        _arrancar_conversacion_wa("Ana Torres", "099123456",
                                  {"full_name": "Ana Torres", "phone_number": "099123456",
                                   "que_necesitas": "una pagina web para mi panaderia"}, 42)

        cuerpo = post.call_args.kwargs["json"]
        assert cuerpo["telefono"] == "099123456"
        assert cuerpo["nombre"] == "Ana Torres"
        assert cuerpo["origen"] == "meta"
        # Lo que escribio en el formulario viaja: es lo que evita que el primer
        # mensaje arranque de cero.
        assert "panaderia" in cuerpo["necesidad"]
        # El nombre y el telefono no se repiten adentro de la necesidad.
        assert "Ana Torres" not in cuerpo["necesidad"]
        # Con el mismo external_id, un reintento del webhook no duplica el lead.
        assert cuerpo["external_id"] == "meta-42"


def test_sin_telefono_no_intenta_escribir():
    with _entorno(), patch("routes.meta.requests.post") as post:
        _arrancar_conversacion_wa("Ana", "", {}, 42)
        post.assert_not_called()


def test_sin_configurar_no_hace_nada():
    # Sin WA_SERVICE_URL el enganche queda inerte: es como estaba antes.
    with patch.dict(os.environ, {"WA_SERVICE_URL": "", "WA_API_KEY": ""}, clear=False), \
         patch("routes.meta.requests.post") as post:
        _arrancar_conversacion_wa("Ana", "099123456", {}, 42)
        post.assert_not_called()


def test_si_el_bot_esta_caido_no_rompe_el_webhook():
    # El lead ya quedo guardado en el CRM, que es lo que no se puede perder.
    with _entorno(), patch("routes.meta.requests.post", side_effect=Exception("connection refused")):
        _arrancar_conversacion_wa("Ana", "099123456", {}, 42)  # no debe levantar


def test_apagado_por_defecto_no_le_escribe_a_nadie():
    """Sin META_WA_AUTO no sale ningun mensaje, aunque el bot este configurado.

    Decision del negocio: el bot atiende al que escribe, no sale a buscar. Y la
    llave es propia porque WA_SERVICE_URL y WA_API_KEY ya estan puestas para el
    panel y para Calendly: sin esta bandera, un deploy cualquiera prenderia el
    outbound sin que nadie lo hubiera decidido.
    """
    with patch.dict(os.environ,
                    {"WA_SERVICE_URL": "http://bot.internal:8080",
                     "WA_API_KEY": "clave", "META_WA_AUTO": ""}, clear=False),          patch("routes.meta.requests.post") as post:
        _arrancar_conversacion_wa("Ana", "099123456", {"que_necesitas": "una web"}, 42)
        post.assert_not_called()
