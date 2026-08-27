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


# ── Aguante frente a cortes ─────────────────────────────────────────────────────


class RespuestaFalsa:
    def __init__(self, cuerpo, codigo=200):
        self._cuerpo = cuerpo
        self.status_code = codigo

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            e = requests.exceptions.HTTPError(f"HTTP {self.status_code}")
            e.response = self
            raise e

    def json(self):
        return self._cuerpo


def test_esperar_job_sobrevive_a_un_corte_en_el_medio(monkeypatch):
    """Un 502 mientras el CRM reinicia no puede matar la corrida."""
    from scripts import render_linkedin as rl

    respuestas = [
        ConnectionError("cortado"),
        RespuestaFalsa({"status": "running"}),
        RespuestaFalsa({"status": "completed", "result": '{"lote": "abc"}'}),
    ]

    def falso_get(url, headers=None, timeout=None):
        r = respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(rl.requests, "get", falso_get)
    monkeypatch.setattr(rl.time, "sleep", lambda s: None)

    assert rl.esperar_job("http://crm", "t", 1, espera_s=0)["lote"] == "abc"


def test_esperar_job_no_espera_si_el_job_ya_fallo(monkeypatch):
    """Un job fallado no mejora esperando: eso si corta al toque."""
    import pytest
    from scripts import render_linkedin as rl

    monkeypatch.setattr(rl.requests, "get", lambda *a, **k: RespuestaFalsa(
        {"status": "failed", "error_message": "sin API key"}))
    monkeypatch.setattr(rl.time, "sleep", lambda s: None)

    with pytest.raises(RuntimeError, match="sin API key"):
        rl.esperar_job("http://crm", "t", 1, espera_s=0)


def test_postear_reintenta_los_5xx_y_termina_mandando(monkeypatch):
    from scripts import render_linkedin as rl

    intentos = []

    def falso_post(url, headers=None, json=None, timeout=None):
        intentos.append(1)
        return RespuestaFalsa({"ok": True}, 500 if len(intentos) < 3 else 200)

    monkeypatch.setattr(rl.requests, "post", falso_post)
    monkeypatch.setattr(rl.time, "sleep", lambda s: None)

    assert rl.postear_con_reintentos("http://crm/x", "t", {}).json() == {"ok": True}
    assert len(intentos) == 3


def test_postear_no_reintenta_un_400(monkeypatch):
    """Insistir con un cuerpo mal armado solo repite el mismo error mas tarde."""
    import pytest
    import requests
    from scripts import render_linkedin as rl

    intentos = []

    def falso_post(url, headers=None, json=None, timeout=None):
        intentos.append(1)
        return RespuestaFalsa({"error": "lote requerido"}, 400)

    monkeypatch.setattr(rl.requests, "post", falso_post)
    monkeypatch.setattr(rl.time, "sleep", lambda s: None)

    with pytest.raises(requests.exceptions.HTTPError):
        rl.postear_con_reintentos("http://crm/x", "t", {})
    assert len(intentos) == 1


def test_si_el_navegador_no_arranca_el_mail_sale_igual(monkeypatch):
    """Un mail sin foto sirve; uno que no llega porque fallo Playwright, no."""
    import sys
    from scripts import render_linkedin as rl

    monkeypatch.setattr(rl, "esperar_job", lambda *a, **k: {
        "lote": "abc",
        "borradores": [{"id": 1, "imagen_tipo": "tarjeta",
                        "imagen_spec": {"frase": "hola"}}],
    })
    # Que el `from playwright.sync_api import ...` de adentro de main() explote.
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    enviado = {}

    def falso_postear(url, token, cuerpo, intentos=4):
        enviado.update(cuerpo)
        return RespuestaFalsa({"ok": True})

    monkeypatch.setattr(rl, "postear_con_reintentos", falso_postear)
    monkeypatch.setattr(sys, "argv",
                        ["render_linkedin.py", "--crm", "http://crm",
                         "--token", "t", "--job-id", "1"])

    assert rl.main() == 0
    assert enviado["lote"] == "abc"
    assert enviado["imagenes"] == []
