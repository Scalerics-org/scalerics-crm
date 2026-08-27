import base64

from scripts.render_linkedin import armar_tarjeta, renderizar


PLANTILLA = '<html><body><h1>{{FRASE}}</h1></body></html>'


class PageFalsa:
    """Doble de una pagina de Playwright: registra que se le pidio."""

    def __init__(self, falla_en=()):
        self.navegaciones = []
        self.contenidos = []
        self.falla_en = falla_en

    def goto(self, url, wait_until=None, timeout=None):
        if url in self.falla_en:
            raise RuntimeError("no cargo")
        self.navegaciones.append(url)

    def set_content(self, html, wait_until=None):
        self.contenidos.append(html)

    def screenshot(self):
        return b"PNGFALSO"


def test_armar_tarjeta_sustituye_la_frase():
    html = armar_tarjeta(PLANTILLA, "Que es un CRM")
    assert "Que es un CRM" in html
    assert "{{FRASE}}" not in html


def test_armar_tarjeta_escapa_el_html():
    html = armar_tarjeta(PLANTILLA, "Precios <script>alert(1)</script>")
    assert "<script>" not in html


def test_renderiza_una_tarjeta():
    page = PageFalsa()
    borradores = [{"id": 1, "imagen_tipo": "tarjeta", "imagen_spec": {"frase": "Hola"}}]

    resultado = renderizar(borradores, PLANTILLA, page)

    assert resultado == [{"id": 1, "png_b64": base64.b64encode(b"PNGFALSO").decode()}]
    assert "Hola" in page.contenidos[0]


def test_renderiza_un_screenshot():
    page = PageFalsa()
    borradores = [{
        "id": 2, "imagen_tipo": "screenshot",
        "imagen_spec": {"url": "https://demo.test"},
    }]

    resultado = renderizar(borradores, PLANTILLA, page)

    assert page.navegaciones == ["https://demo.test"]
    assert resultado[0]["id"] == 2


def test_imagen_ninguna_no_produce_png():
    page = PageFalsa()
    borradores = [{"id": 3, "imagen_tipo": "ninguna", "imagen_spec": {}}]
    assert renderizar(borradores, PLANTILLA, page) == []


def test_una_falla_de_render_no_tumba_al_resto():
    page = PageFalsa(falla_en={"https://rota.test"})
    borradores = [
        {"id": 4, "imagen_tipo": "screenshot", "imagen_spec": {"url": "https://rota.test"}},
        {"id": 5, "imagen_tipo": "tarjeta", "imagen_spec": {"frase": "Sigue"}},
    ]

    resultado = renderizar(borradores, PLANTILLA, page)

    assert [r["id"] for r in resultado] == [5]


# ── La tarjeta tiene que seguir la marca ─────────────────────────────────────

def _plantilla():
    import os
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "templates", "linkedin_card.html")
    return open(ruta, encoding="utf-8").read()


def test_la_tarjeta_usa_los_colores_de_marca():
    """Navy #0f2430, azul #1796d2 y verde #7fcc2a salen de los assets originales
    (logo_full_alt.png, logo_redes.png). La paleta #09090f / #0088CC es la de
    las demos de clientes y no va en material de Scalerics."""
    html = _plantilla().lower()
    for color in ("#0f2430", "#1796d2", "#7fcc2a"):
        assert color in html, color
    for ajeno in ("#09090f", "#0088cc", "#10b981"):
        assert ajeno not in html, f"{ajeno} es de la paleta de demos, no de la marca"


def test_la_tarjeta_lleva_el_logo_embebido():
    """Embebido y no por URL: el render corre en el runner de Actions y no
    puede depender de que GitHub sirva la imagen."""
    html = _plantilla()
    assert "data:image/png;base64," in html
    assert "http://" not in html.replace("https://fonts.googleapis.com", "")


def test_el_logo_no_se_estira():
    """Es hijo de un flex column: sin align-self lo estira a todo el ancho y
    el alto queda ignorado."""
    html = _plantilla()
    bloque = html[html.index(".logo {"):html.index("}", html.index(".logo {"))]
    assert "align-self" in bloque
