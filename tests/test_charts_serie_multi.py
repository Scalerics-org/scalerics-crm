"""El grafico de varias lineas, corrido de verdad en node.

Existe porque `SC.serie` dibuja una sola serie, y comparar campanas obligaba a
mirar graficos separados adivinando la escala de cada uno.

Las reglas que este archivo protege son las que se rompen sin que se note:
un solo eje Y, el color atado a la campana y no a su posicion, los huecos que
cortan la linea, y una leyenda que exista siempre que haya mas de una serie.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
CHARTS = RAIZ / "static" / "charts.js"

sin_node = pytest.mark.skipif(shutil.which("node") is None,
                              reason="node no esta instalado")


def _correr(js, tmp_path):
    archivo = tmp_path / "correr.js"
    archivo.write_text(
        "globalThis.window = globalThis;\n"
        + CHARTS.read_text(encoding="utf-8")
        + "\nconst SC = globalThis.SC;\n" + js + "\n", encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True,
                       text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout


_DOS = """
var series = [
  {campana: 'UY', puntos: [{x:'W10',y:10},{x:'W11',y:20},{x:'W12',y:15}]},
  {campana: 'ARG', puntos: [{x:'W10',y:30},{x:'W11',y:25},{x:'W12',y:40}]}
];
"""


@sin_node
def test_dibuja_una_linea_por_serie(tmp_path):
    salida = _correr(_DOS + """
SC._resetColores();
var h = SC.serieMulti(series, {etiqueta:'x'}, 'oscuro');
console.log((h.match(/class="sc-linea"/g) || []).length);
""", tmp_path)
    assert salida.strip() == "2"


@sin_node
def test_hay_un_solo_eje_y(tmp_path):
    """La regla mas importante de todas: dos escalas en un grafico hacen que
    cualquier par de curvas se cruce donde uno elija la escala."""
    salida = _correr(_DOS + """
SC._resetColores();
var h = SC.serieMulti(series, {etiqueta:'x'}, 'oscuro');
console.log((h.match(/class="sc-eje-y"/g) || []).length);
""", tmp_path)
    assert salida.strip() == "1"


@sin_node
def test_las_dos_series_comparten_la_escala(tmp_path):
    """Si cada una se escalara sola, dos curvas iguales en forma pero distintas
    en magnitud se verian identicas y la comparacion mentiria."""
    salida = _correr(_DOS + """
SC._resetColores();
var h = SC.serieMulti(series, {etiqueta:'x', formato:'numero'}, 'oscuro');
// El tope del eje tiene que cubrir el maximo de TODAS las series (40), no el
// de la primera (20).
console.log(h.indexOf('>40<') >= 0 || h.indexOf('>50<') >= 0);
""", tmp_path)
    assert salida.strip() == "true"


@sin_node
def test_el_color_sigue_a_la_campana_no_a_su_posicion(tmp_path):
    """Filtrar una campana no puede repintar a las que quedan."""
    salida = _correr("""
SC._resetColores();
var a = SC.colorDeCampana('UY', 0, 'oscuro');
var b = SC.colorDeCampana('ARG', 1, 'oscuro');
// Ahora 'ARG' pasa a estar primera: su color no puede cambiar.
var b2 = SC.colorDeCampana('ARG', 0, 'oscuro');
console.log(JSON.stringify({estable: b === b2, distintos: a !== b}));
""", tmp_path)
    d = json.loads(salida)
    assert d["estable"] and d["distintos"]


@sin_node
def test_un_hueco_corta_la_linea(tmp_path):
    """Unir por arriba de una semana sin dato inventa una tendencia."""
    salida = _correr("""
SC._resetColores();
var series = [{campana:'UY', puntos:[{x:'W10',y:10},{x:'W11',y:null},{x:'W12',y:15}]}];
var h = SC.serieMulti(series, {etiqueta:'x'}, 'oscuro');
console.log((h.match(/class="sc-linea"/g) || []).length);
""", tmp_path)
    # Dos tramos de un punto cada uno, no una linea sola cruzando el hueco.
    assert salida.strip() == "2"


@sin_node
def test_con_dos_series_hay_leyenda(tmp_path):
    """La identidad nunca puede depender solo del color."""
    salida = _correr(_DOS + """
SC._resetColores();
var h = SC.serieMulti(series, {etiqueta:'x'}, 'oscuro');
console.log(JSON.stringify({
  leyenda: h.indexOf('sc-leyenda') >= 0,
  uy: h.indexOf('UY') >= 0,
  arg: h.indexOf('ARG') >= 0
}));
""", tmp_path)
    d = json.loads(salida)
    assert d["leyenda"] and d["uy"] and d["arg"]


@sin_node
def test_una_serie_toda_vacia_no_ocupa_lugar(tmp_path):
    """Una campana sin un solo dato seria una linea invisible con su color
    reservado en la leyenda."""
    salida = _correr("""
SC._resetColores();
var series = [
  {campana:'UY', puntos:[{x:'W10',y:10}]},
  {campana:'VACIA', puntos:[{x:'W10',y:null}]}
];
var h = SC.serieMulti(series, {etiqueta:'x'}, 'oscuro');
console.log(h.indexOf('VACIA') >= 0);
""", tmp_path)
    assert salida.strip() == "false"


@sin_node
def test_sin_ninguna_serie_avisa_en_vez_de_dibujar_un_eje_vacio(tmp_path):
    salida = _correr("""
console.log(SC.serieMulti([], {etiqueta:'Costo por demo'}, 'oscuro'));
""", tmp_path)
    assert "sc-vacio" in salida and "Costo por demo" in salida


@sin_node
def test_el_eje_x_es_la_union_de_todas_las_semanas(tmp_path):
    """Dos campanas que arrancaron en semanas distintas tienen que quedar
    alineadas, o la comparacion visual no vale."""
    salida = _correr("""
SC._resetColores();
var series = [
  {campana:'VIEJA', puntos:[{x:'W10',y:1},{x:'W11',y:2}]},
  {campana:'NUEVA', puntos:[{x:'W11',y:3},{x:'W12',y:4}]}
];
var h = SC.serieMulti(series, {etiqueta:'x'}, 'oscuro');
console.log(JSON.stringify({
  w10: h.indexOf('>W10<') >= 0,
  w12: h.indexOf('>W12<') >= 0
}));
""", tmp_path)
    d = json.loads(salida)
    assert d["w10"] and d["w12"]
