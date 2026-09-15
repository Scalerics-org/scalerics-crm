"""La dona de "Por lo que el lead declaró", corrida de verdad en node.

Pedido de Juan (14/9): "para estos 4 hacer gráficos circulares y más grandes".
La dona reparte los leads entre las respuestas; el % que llegó a reunión va en
la leyenda, con la muestra chica avisada, y ningún número se dibuja encima de
un color (blanco sobre la paleta no llega a 4,5:1).
"""

import re

import pytest

from tests.test_charts_etiquetas import _fuera_del_viewbox, _svg, sin_node


def _grados(html):
    return [float(g) for g in re.findall(r'data-grados="([\d.]+)"', html)]


@sin_node
def test_las_porciones_suman_360_grados(tmp_path):
    html = _svg("""SC.dona([
      {etiqueta: 'Montevideo', n: 5, tasa: 0.2, muestraChica: true},
      {etiqueta: 'Interior', n: 3, tasa: 0, muestraChica: true},
      {etiqueta: 'Aún no lo sé', n: 4, tasa: null}
    ], {etiqueta: 'Ciudad'}, 'oscuro')""", tmp_path)
    grados = _grados(html)
    assert len(grados) == 3
    assert sum(grados) == pytest.approx(360, abs=0.01)
    assert grados[0] == pytest.approx(150)
    assert "NaN" not in html and "undefined" not in html
    assert _fuera_del_viewbox(html) == []


@sin_node
def test_la_leyenda_dice_cantidad_y_reunion_con_la_muestra_chica(tmp_path):
    html = _svg("""SC.dona([
      {etiqueta: 'Montevideo', n: 5, tasa: 0.2, muestraChica: true},
      {etiqueta: 'Interior', n: 40, tasa: 0.125, muestraChica: false},
      {etiqueta: 'Sin tasa', n: 1, tasa: null}
    ], {etiqueta: 'Ciudad', ayuda: 'a'}, 'claro')""", tmp_path)
    assert "<b>Montevideo</b> · 5 leads" in html
    assert "20,0% llegó a reunión · sobre 5 · muestra chica" in html
    assert "12,5% llegó a reunión</span>" in html
    assert "<b>Sin tasa</b> · 1 lead ·" in html and "sin datos de reunión" in html
    # El total en el centro, y ningún texto encima de las porciones.
    assert re.search(r">46</text>", html)
    assert html.count("<text") == 2


@sin_node
def test_los_colores_son_de_la_paleta_y_otros_va_en_gris(tmp_path):
    r = _svg("""(function () {
      const p = ['a','b','c','d','e','f'].map((e, i) => ({etiqueta: e, n: 10 - i}));
      p.push({etiqueta: 'otros (7 respuestas distintas)', n: 2});
      return {html: SC.dona(p, {etiqueta: 't'}, 'oscuro'), paleta: SC.PALETA};
    })()""", tmp_path)
    colores = re.findall(r'<path d="[^"]*" fill="([^"]+)"', r["html"])
    gris = r["paleta"]["neutro"]["oscuro"]
    assert colores[:5] == r["paleta"]["oscuro"]
    assert colores[5:] == [gris, gris]


@sin_node
def test_una_sola_porcion_es_un_anillo_entero_sin_nan(tmp_path):
    html = _svg("""SC.dona([{etiqueta: 'Montevideo', n: 7, tasa: 1, muestraChica: true}],
      {etiqueta: 'Ciudad'}, 'oscuro')""", tmp_path)
    assert _grados(html) == [360.0]
    assert "NaN" not in html
    assert "100,0% del total" in html
    assert _fuera_del_viewbox(html) == []


@sin_node
def test_sin_leads_no_dibuja_ni_da_nan(tmp_path):
    r = _svg("""[SC.dona([], {etiqueta: 'Ciudad'}, 'oscuro'),
      SC.dona([{etiqueta: 'x', n: 0}, {etiqueta: 'y', n: null}], {etiqueta: 'Ciudad'}, 'oscuro'),
      SC.dona([{etiqueta: 'x', n: 0}, {etiqueta: 'y', n: 3}], {etiqueta: 'Ciudad'}, 'oscuro')]""",
             tmp_path)
    assert "Sin datos" in r[0] and "<svg" not in r[0]
    assert "Sin datos" in r[1] and "NaN" not in r[1]
    # La porción en cero no ocupa lugar ni sale en la leyenda.
    assert _grados(r[2]) == [360.0] and "<b>x</b>" not in r[2]


@sin_node
def test_las_etiquetas_se_escapan(tmp_path):
    html = _svg("""SC.dona([{etiqueta: '<b>x</b>', n: 1}, {etiqueta: 'y', n: 1}],
      {etiqueta: 'Q & A'}, 'oscuro')""", tmp_path)
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "Q &amp; A" in html
