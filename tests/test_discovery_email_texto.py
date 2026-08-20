"""Los dos textos del correo en frio y sus cabeceras.

El remitente es la guarda que mas importa: si `DISCOVERY_FROM_EMAIL` no esta,
la funcion NO manda. Una configuracion a medias no puede terminar mandando en
frio desde scalerics.com, que es el dominio con el que se le escribe a los
clientes y por el que salen los recordatorios de Meta.
"""

import os
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from services.email_service import send_discovery_email

_REMITENTE = "Scalerics <hola@novedades.scalerics.com>"


@contextmanager
def _capturar():
    anterior = os.environ.get("DISCOVERY_FROM_EMAIL")
    os.environ["DISCOVERY_FROM_EMAIL"] = _REMITENTE
    try:
        with patch("services.email_service._send_estado", return_value="ok") as enviar:
            yield enviar
    finally:
        if anterior is None:
            os.environ.pop("DISCOVERY_FROM_EMAIL", None)
        else:
            os.environ["DISCOVERY_FROM_EMAIL"] = anterior


def test_sin_remitente_configurado_no_manda(monkeypatch):
    monkeypatch.delenv("DISCOVERY_FROM_EMAIL", raising=False)
    with patch("services.email_service._send_estado") as enviar:
        estado = send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t")
        enviar.assert_not_called()
    assert estado == "fallo"


def test_las_respuestas_van_a_contacto():
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t")
    assert enviar.call_args.kwargs["headers"]["Reply-To"] == "contacto@scalerics.com"


@pytest.mark.parametrize("numero,esperado", [(1, "eso se automatiza"), (2, "una sola vez")])
def test_cada_contacto_dice_algo_distinto(numero, esperado):
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t", numero)
    assert esperado in enviar.call_args.args[2]


def test_el_segundo_avisa_que_es_el_ultimo():
    """Tiene que cumplir lo que dice: despues de ese, el comercio no vuelve a
    entrar nunca."""
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t", 2)
    html = enviar.call_args.args[2]
    assert "no te escribimos m" in html.lower()


def test_el_primero_no_afirma_que_el_comercio_no_tiene_web():
    """Estos comercios YA tienen sitio: es el criterio con el que se los eligio,
    asi que el mail no puede decir lo contrario.

    OJO con el borde de este test: la version anterior tambien prohibia mencionar
    paginas web, y eso estaba mal — Scalerics SI vende paginas y tiendas online.
    Negarlo cerraba una puerta por la que entra plata. Lo unico prohibido es
    afirmar que este comercio no tiene sitio.

    Se compara sobre el texto plano y no sobre el HTML: el HTML escapa las
    tildes a entidades, asi que buscar "pagina" con tilde ahi nunca puede dar
    positivo y el test pasaria con cualquier texto.
    """
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t", 1)
    texto = enviar.call_args.kwargs["text"].lower()
    for prohibido in ("no tenés página", "no tenes pagina", "no tiene página",
                      "sin página web", "sin sitio web"):
        assert prohibido not in texto, f"el mail afirma algo falso: {prohibido!r}"


def test_el_primero_si_puede_ofrecer_paginas():
    """Scalerics vende paginas y tiendas online: el mail no tiene por que negarlo."""
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t", 1)
    texto = enviar.call_args.kwargs["text"].lower()
    assert "no te venimos a ofrecer" not in texto


def test_los_dos_llevan_baja_y_cabecera_de_baja():
    for numero in (1, 2):
        with _capturar() as enviar:
            send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/tok", numero)
        html = enviar.call_args.args[2]
        texto = enviar.call_args.kwargs["text"]
        assert "https://c/baja/tok" in html, f"contacto {numero} sin link de baja"
        assert "https://c/baja/tok" in texto, f"contacto {numero} sin baja en el texto"
        assert enviar.call_args.kwargs["headers"]["List-Unsubscribe"] == "<https://c/baja/tok>"
        assert enviar.call_args.kwargs["headers"]["List-Unsubscribe-Post"] == \
            "List-Unsubscribe=One-Click"


def test_el_nombre_del_comercio_se_escapa():
    """`name` sale de Google Maps: lo escribe cualquiera."""
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", "Inmo <script>alert(1)</script>", "Inmobiliaria",
                             "https://c/baja/t")
    assert "<script>" not in enviar.call_args.args[2]


def test_los_dos_cuerpos_son_distintos():
    cuerpos = []
    for numero in (1, 2):
        with _capturar() as enviar:
            send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t", numero)
        cuerpos.append(enviar.call_args.args[2])
    assert cuerpos[0] != cuerpos[1]


def test_el_texto_plano_no_lleva_entidades_html():
    """El asunto y la parte en texto van sin escapar: si no, el lector ve
    &aacute; en vez de una tilde."""
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria", "https://c/baja/t", 2)
    assert "&" not in enviar.call_args.args[1], "entidad HTML en el asunto"
    assert "&aacute;" not in enviar.call_args.kwargs["text"]


def test_numero_none_no_revienta():
    with _capturar() as enviar:
        assert send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria",
                                    "https://c/baja/t", None) == "ok"


# ─── Lo que la revision encontro abierto ─────────────────────────────────────

@pytest.mark.parametrize("negocio", ["Inmobiliaria Sur", "ACSA", "Mas Aguada"])
def test_el_asunto_del_primero_esta_bien_escrito(negocio):
    """El asunto es la linea mas visible de todo mail en frio. La version vieja
    armaba "Una idea para de Inmobiliaria Sur", pegando dos preposiciones."""
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", negocio, "Inmobiliaria", "https://c/baja/t", 1)
    asunto = enviar.call_args.args[1]
    assert asunto == f"Una idea para {negocio}"
    assert " para de " not in asunto
    assert "  " not in asunto


def test_el_asunto_del_segundo_esta_bien_escrito():
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", "Inmobiliaria Sur", "Inmobiliaria",
                             "https://c/baja/t", 2)
    assert enviar.call_args.args[1] == "Último mail de Inmobiliaria Sur"


def test_sin_negocio_los_asuntos_siguen_teniendo_sentido():
    for numero, esperado in ((1, "Una idea para tu negocio"), (2, "Último mail de Scalerics")):
        with _capturar() as enviar:
            send_discovery_email("x@y.uy", "", "Inmobiliaria", "https://c/baja/t", numero)
        assert enviar.call_args.args[1] == esperado


def test_no_manda_desde_el_dominio_principal(monkeypatch):
    """Un dedo torcido en el `flyctl secrets set` pondria el correo en frio en
    el mismo dominio con el que se le escribe a los clientes."""
    for remitente in ("contacto@scalerics.com",
                      "Scalerics <crm@scalerics.com>",
                      "hola@SCALERICS.COM"):
        monkeypatch.setenv("DISCOVERY_FROM_EMAIL", remitente)
        with patch("services.email_service._send_estado") as enviar:
            assert send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria",
                                        "https://c/baja/t") == "fallo"
            enviar.assert_not_called()


def test_un_subdominio_si_puede_mandar(monkeypatch):
    """La guarda es sobre el dominio exacto, no sobre la cadena: un subdominio
    de scalerics.com es justamente lo que la campana tiene que usar."""
    monkeypatch.setenv("DISCOVERY_FROM_EMAIL", "Scalerics <hola@novedades.scalerics.com>")
    with patch("services.email_service._send_estado", return_value="ok") as enviar:
        assert send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria",
                                    "https://c/baja/t") == "ok"
        enviar.assert_called_once()


def test_un_remitente_sin_arroba_no_manda(monkeypatch):
    monkeypatch.setenv("DISCOVERY_FROM_EMAIL", "basura sin arroba")
    with patch("services.email_service._send_estado") as enviar:
        assert send_discovery_email("x@y.uy", "Inmo", "Inmobiliaria",
                                    "https://c/baja/t") == "fallo"
        enviar.assert_not_called()


# ─── La linea de apertura por rubro ─────────────────────────────────────────

def _apertura(rubro, numero=1):
    with _capturar() as enviar:
        send_discovery_email("x@y.uy", "Comercio X", rubro, "https://c/baja/t", numero)
    # parrafos: [0] saludo, [1] la linea del rubro
    return enviar.call_args.kwargs["text"].split(chr(10) + chr(10))[1]


@pytest.mark.parametrize("rubro,marca", [
    ("Inmobiliaria", "propiedades"),
    ("Peluqueria", "turnos se agendan por WhatsApp"),
    ("Gimnasio", "cuotas"),
    ("Veterinaria", "vacunas"),
    ("Odontología", "turnos se agendan por teléfono"),
    ("Repuestos", "compatibilidad"),
    ("Automotora", "financiación"),
    ("Ferretería", "lista de precios"),
])
def test_cada_rubro_tiene_su_linea(rubro, marca):
    assert marca in _apertura(rubro)


@pytest.mark.parametrize("alias,equivalente", [
    ("Hair salon", "Peluqueria"),
    ("Hairdresser", "Peluqueria"),
    ("Concesionaria", "Automotora"),
    ("Compraventa", "Automotora"),
    ("Dentista", "Odontología"),
    ("Tienda de herramientas", "Ferretería"),
])
def test_el_mismo_rubro_escrito_distinto_cae_en_la_misma_linea(alias, equivalente):
    """Sin esto, dos rubros iguales escritos distinto reciben textos distintos."""
    assert _apertura(alias) == _apertura(equivalente)


@pytest.mark.parametrize("rubro", ["Odontología", "odontologia", "ODONTOLOGÍA",
                                   "  Odontología  "])
def test_las_tildes_y_las_mayusculas_no_rompen_el_rubro(rubro):
    assert "turnos se agendan por teléfono" in _apertura(rubro)


@pytest.mark.parametrize("rubro", ["Bloquera", "Comercio", "Agregar sitio web",
                                   "Rubro Que No Existe", "", None])
def test_un_rubro_desconocido_cae_en_la_generica(rubro):
    """De 44 rubros distintos en la base, ocho concentran el volumen: la
    generica se usa tanto como las especificas."""
    assert "algo de la operativa que hoy se hace a mano" in _apertura(rubro)


def test_la_apertura_nunca_afirma_nada_del_comercio():
    """No miramos su sitio: cualquier frase que suene a insight es mentira.
    Todas las lineas hablan del rubro y arrancan en condicional."""
    from services.email_service import _LINEAS_POR_RUBRO, _LINEA_GENERICA
    for linea in list(_LINEAS_POR_RUBRO.values()) + [_LINEA_GENERICA]:
        assert linea.startswith("Si "), f"no es condicional: {linea!r}"
        assert "vimos" not in linea.lower()
        assert "tu sitio" not in linea.lower()


def test_todas_las_lineas_son_distintas():
    from services.email_service import _LINEAS_POR_RUBRO, _LINEA_GENERICA
    todas = list(_LINEAS_POR_RUBRO.values()) + [_LINEA_GENERICA]
    assert len(set(todas)) == len(todas)


def test_los_alias_apuntan_a_rubros_que_existen():
    """Un alias mal escrito manda el rubro a la generica sin que nadie lo note."""
    from services.email_service import _ALIAS_RUBRO, _LINEAS_POR_RUBRO
    for alias, destino in _ALIAS_RUBRO.items():
        assert destino in _LINEAS_POR_RUBRO, f"{alias!r} apunta a {destino!r}, que no existe"
