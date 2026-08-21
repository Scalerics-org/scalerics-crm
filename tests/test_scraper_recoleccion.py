"""La fase de recoleccion de fichas: scrollear el listado y juntar las URLs.

Existe porque el bucle viejo recargaba el listado despues de cada ficha, y
recargarlo lo devuelve al principio con solo la primera pantalla cargada. El
resultado era que el scraper nunca pasaba de las ~10 primeras de las ~100 que
Google ofrece, sin importar que `max_results` se le pidiera.

Aca se prueba la parte que no necesita navegador: cuando dejar de scrollear y
que URLs quedan.
"""

import pytest

from scraper import _scroll_agotado, recolectar_fichas


# ─── Cuando parar de scrollear ────────────────────────────────────────────────

def test_mientras_siga_creciendo_no_para():
    assert _scroll_agotado([10, 22, 35], quietas=3) is False


def test_una_sola_medicion_repetida_no_alcanza():
    """Google carga de a tandas y a veces tarda: una pausa no es el final."""
    assert _scroll_agotado([10, 22, 22], quietas=3) is False


def test_dos_repetidas_tampoco():
    assert _scroll_agotado([10, 22, 22, 22], quietas=3) is False


def test_tres_repetidas_es_el_final():
    assert _scroll_agotado([10, 22, 22, 22, 22], quietas=3) is True


def test_si_vuelve_a_crecer_se_reinicia_la_cuenta():
    """Lo que corta es el estancamiento al final, no uno del medio."""
    assert _scroll_agotado([10, 10, 10, 25], quietas=3) is False


def test_historial_corto_nunca_corta():
    assert _scroll_agotado([], quietas=3) is False
    assert _scroll_agotado([7], quietas=3) is False


# ─── La recoleccion ───────────────────────────────────────────────────────────

class _PaginaFalsa:
    """Simula el feed de Maps: cada scroll agrega una tanda de links.

    Devuelve links repetidos a proposito, porque Maps los repite: la misma
    ficha aparece como <a> mas de una vez y sin deduplicar se visitaria dos
    veces, que es lo caro.
    """

    def __init__(self, tandas):
        self._tandas = list(tandas)
        self._visibles = list(self._tandas.pop(0)) if self._tandas else []
        self.scrolls = 0

    def query_selector_all(self, _sel):
        return [_Link(h) for h in self._visibles]

    def query_selector(self, _sel):
        return self          # el contenedor del feed es la propia pagina falsa

    def evaluate(self, _js):
        self.scrolls += 1
        if self._tandas:
            self._visibles = self._visibles + list(self._tandas.pop(0))

    def wait_for_timeout(self, _ms):
        pass


class _Link:
    def __init__(self, href):
        self._href = href

    def get_attribute(self, _n):
        return self._href


def test_junta_las_fichas_de_todas_las_tandas():
    pagina = _PaginaFalsa([
        ["https://www.google.com/maps/place/A", "https://www.google.com/maps/place/B"],
        ["https://www.google.com/maps/place/C"],
        ["https://www.google.com/maps/place/D"],
    ])
    fichas = recolectar_fichas(pagina, tope=50)
    assert fichas == ["https://www.google.com/maps/place/A",
                      "https://www.google.com/maps/place/B",
                      "https://www.google.com/maps/place/C",
                      "https://www.google.com/maps/place/D"]


def test_no_repite_una_ficha_que_aparece_dos_veces():
    pagina = _PaginaFalsa([
        ["https://www.google.com/maps/place/A", "https://www.google.com/maps/place/A"],
        ["https://www.google.com/maps/place/A", "https://www.google.com/maps/place/B"],
    ])
    assert recolectar_fichas(pagina, tope=50) == [
        "https://www.google.com/maps/place/A",
        "https://www.google.com/maps/place/B",
    ]


def test_descarta_lo_que_no_sea_una_ficha():
    pagina = _PaginaFalsa([[
        "https://www.google.com/maps/place/A",
        "https://www.google.com/maps/dir/algo",
        "https://support.google.com/maps",
        "",
    ]])
    assert recolectar_fichas(pagina, tope=50) == ["https://www.google.com/maps/place/A"]


def test_respeta_el_tope():
    pagina = _PaginaFalsa([[f"https://www.google.com/maps/place/{i}" for i in range(40)]])
    assert len(recolectar_fichas(pagina, tope=12)) == 12


def test_deja_de_scrollear_cuando_el_listado_se_agota():
    """Sin esto scrollearia para siempre en una busqueda de pocos resultados."""
    pagina = _PaginaFalsa([["https://www.google.com/maps/place/A"]])
    recolectar_fichas(pagina, tope=500)
    assert pagina.scrolls < 12, f"scrolleo de mas: {pagina.scrolls}"


def test_sin_feed_no_explota():
    class SinFeed(_PaginaFalsa):
        def query_selector(self, _sel):
            return None

    pagina = SinFeed([["https://www.google.com/maps/place/A"]])
    assert recolectar_fichas(pagina, tope=50) == ["https://www.google.com/maps/place/A"]
