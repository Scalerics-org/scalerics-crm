"""Como se dibuja en el panel lo que el lead manda y no es texto.

El JS del dashboard vive dentro de un string de Python, asi que no habia forma
de probarlo. Aca se saca la parte que dibuja los medios a una constante y se la
corre con node de verdad: lo que se mide es el HTML que sale, no que el codigo
fuente contenga tal palabra.

Hasta ahora el panel solo sabia dibujar un <audio> y descartaba todo lo demas
(`if (a.tipo !== 'audio') return ''`). Una foto llegaba al CRM y no se veia.
"""

import json
import shutil
import subprocess

import pytest

import dashboard

node = pytest.mark.skipif(shutil.which("node") is None, reason="no hay node")


def render(mensaje):
    """Corre el render real del panel sobre un mensaje y devuelve el HTML."""
    guion = (
        dashboard.ESC_JS
        + "\n"
        + dashboard.WA_MEDIOS_JS
        + "\nprocess.stdout.write(mediosDeMensaje("
        + json.dumps(mensaje)
        + "));"
    )
    r = subprocess.run(
        ["node", "-e", guion], capture_output=True, text=True, encoding="utf-8"
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def medio(**campos):
    """Un mensaje con un medio, EXACTAMENTE como lo entrega la API del bot.

    Ojo con esto: la API manda `{tipo, segundos, nombre, url}` y NO manda
    `archivo` —el nombre en disco es interno y no sale—. La primera versión de
    estos tests inventaba un `archivo` que no existe, los tests pasaban, y en
    producción no se veía ninguna foto. Si cambia el shape, el que manda es
    `mediosAFormatoBot` de wa-service/src/http/routes/crm.js.
    """
    return {"id": 42, "media": [{"url": "/api/messages/42/media/0", **campos}]}


# ── lo que antes no se dibujaba ─────────────────────────────────────────────


@node
def test_una_foto_se_ve_en_el_panel():
    html = render(medio(tipo="imagen"))

    assert "<img" in html
    # La URL del bot se traduce a la del CRM: el bot no tiene IP publica, asi
    # que el navegador no puede pedirle el archivo directo.
    assert "/api/wa/media/42/0" in html


@node
def test_un_sticker_tambien_se_dibuja():
    html = render(medio(tipo="sticker"))
    assert "<img" in html


@node
def test_un_video_sale_con_reproductor():
    html = render(medio(tipo="video"))
    assert "<video" in html
    assert "/api/wa/media/42/0" in html


@node
def test_un_documento_sale_como_link_con_su_nombre():
    html = render(
        medio(tipo="documento", nombre="presupuesto obra.pdf")
    )
    assert "presupuesto obra.pdf" in html
    assert "/api/wa/media/42/0" in html


# ── lo que ya andaba y no se puede romper ───────────────────────────────────


@node
def test_la_nota_de_voz_sigue_saliendo_con_su_duracion():
    html = render(medio(tipo="audio", segundos=7))
    assert "<audio" in html
    assert "7s" in html


@node
def test_un_mensaje_sin_medios_no_dibuja_nada():
    assert render({"id": 1, "media": []}) == ""
    assert render({"id": 1}) == ""


# ── los bordes ──────────────────────────────────────────────────────────────


@node
def test_sin_archivo_dice_que_mandaron_y_no_finge_un_reproductor():
    """El archivo no esta: era muy grande, fallo la descarga, o ya se borro.

    Dibujar un <img> vacio deja un roto en la pantalla sin explicar nada. Lo
    honesto es decir que mandaron una foto y que no la tenemos.
    """
    html = render({"id": 42, "media": [{"tipo": "imagen", "url": None}]})

    assert "foto" in html.lower()
    assert "<img" not in html
    assert "<audio" not in html


@node
def test_el_nombre_del_archivo_va_escapado():
    """El nombre lo elige el lead: es texto de afuera entrando al panel."""
    html = render(
        medio(
            tipo="documento",
            nombre='<img src=x onerror="alert(1)">.pdf',
        )
    )

    assert "onerror" not in html or "&quot;" in html
    assert "<img src=x" not in html


@node
def test_un_tipo_que_no_conocemos_no_rompe_la_conversacion():
    html = render(medio(tipo="ubicacion"))
    assert "<script" not in html


# ── que el pegado no se rompa ───────────────────────────────────────────────


def test_el_js_queda_pegado_en_el_html_del_panel():
    """Si el marcador no se sustituye, el panel entero muere.

    El JS del dashboard es un solo <script>: una llamada a una funcion que no
    existe tira ReferenceError y de ahi para abajo no corre nada. La pantalla
    queda en blanco y no hay ningun error en el servidor que lo delate.
    """
    html = dashboard.DASHBOARD_HTML

    assert "/*WA_MEDIOS_JS*/" not in html, "el marcador quedo sin sustituir"
    assert "/*ESC_JS*/" not in html
    assert "function mediosDeMensaje(" in html
    assert "function esc(" in html
