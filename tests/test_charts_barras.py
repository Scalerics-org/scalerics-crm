"""Las barras del panel, corridas de verdad en node.

`barrasAgrupadas` existe para el "mes a mes": leads, demos y ventas de cada mes
uno al lado del otro. Una linea serviria para la tendencia, pero con tres o
cuatro puntos una linea se lee como si faltara algo.

`barrasSimples` reemplaza a `barrasConIC` en el panel. El intervalo de confianza
era correcto y era ilegible —Juan, textual: "lo de los intervalos de confianza
no los estoy logrando interpretar"— y un grafico que no se entiende no informa.
La incertidumbre no se tira: se dice con palabras al lado del numero.
"""

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


_MESES = """
var periodos = [
  {clave:'2026-03', etiqueta:'Marzo'},
  {clave:'2026-04', etiqueta:'Abril'},
  {clave:'2026-05', etiqueta:'Mayo'}
];
var series = [
  {etiqueta:'Leads', color:'#60a5fa',
   valores:{'2026-03':59,'2026-04':12,'2026-05':30}},
  {etiqueta:'Demos', color:'#34d399',
   valores:{'2026-03':2,'2026-04':4,'2026-05':9}},
  {etiqueta:'Ventas', color:'#fbbf24',
   valores:{'2026-03':1,'2026-04':0,'2026-05':2}}
];
"""


# ── barrasAgrupadas ────────────────────────────────────────────────────────

@sin_node
def test_dibuja_una_barra_por_mes_y_por_serie(tmp_path):
    salida = _correr(_MESES + """
var h = SC.barrasAgrupadas(periodos, series, {etiqueta:'Mes a mes'}, 'oscuro');
console.log((h.match(/<rect /g) || []).length);
""", tmp_path)
    # 3 meses x 3 series. El cero de Ventas en abril tambien es una barra: es
    # de alto cero, pero el hueco en el grupo es justamente lo que se mira.
    assert salida.strip() == "9"


@sin_node
def test_cada_serie_mantiene_su_color_en_todos_los_meses(tmp_path):
    """El color sigue a la serie, no a su posicion dentro del grupo. Si
    cambiara de mes a mes la leyenda no serviria para nada."""
    salida = _correr(_MESES + """
var h = SC.barrasAgrupadas(periodos, series, {etiqueta:'x'}, 'oscuro');
console.log((h.match(/fill="#60a5fa"/g) || []).length);
""", tmp_path)
    assert salida.strip() == "3"


@sin_node
def test_hay_un_solo_eje_y(tmp_path):
    """Leads, demos y ventas son todas cuentas de personas: comparten escala.

    Si alguien mete el gasto en este grafico, el eje pasa a mezclar dolares con
    personas y las barras de demos y ventas desaparecen contra la de gasto.
    """
    salida = _correr(_MESES + """
var h = SC.barrasAgrupadas(periodos, series, {etiqueta:'x'}, 'oscuro');
console.log((h.match(/class="sc-eje-y"/g) || []).length);
""", tmp_path)
    assert salida.strip() == "1"


@sin_node
def test_cada_mes_lleva_su_nombre_abajo(tmp_path):
    salida = _correr(_MESES + """
var h = SC.barrasAgrupadas(periodos, series, {etiqueta:'x'}, 'oscuro');
console.log(['Marzo','Abril','Mayo'].every(m => h.indexOf('>' + m + '<') >= 0));
""", tmp_path)
    assert salida.strip() == "true"


@sin_node
def test_hay_leyenda_con_las_tres_series(tmp_path):
    """Tres series y ninguna etiquetada directamente: sin leyenda la identidad
    queda solo en el color."""
    salida = _correr(_MESES + """
var h = SC.barrasAgrupadas(periodos, series, {etiqueta:'x'}, 'oscuro');
console.log((h.match(/class="sc-leyenda-item"/g) || []).length);
""", tmp_path)
    assert salida.strip() == "3"


@sin_node
def test_una_sola_serie_no_lleva_leyenda(tmp_path):
    """El titulo ya la nombra. Un recuadro con un solo item repite el titulo y
    se lee como si faltaran las demas series."""
    salida = _correr(_MESES + """
var h = SC.barrasAgrupadas(periodos, [series[0]],
  {etiqueta:'Gasto por semana'}, 'oscuro');
console.log(h.indexOf('sc-leyenda') >= 0);
""", tmp_path)
    assert salida.strip() == "false"


@sin_node
def test_un_mes_sin_dato_no_dibuja_una_barra_de_cero(tmp_path):
    """Cero y "no hay dato" no son lo mismo. Una barra de cero dice "medimos y
    dio cero"; un hueco dice "no sabemos"."""
    salida = _correr("""
var h = SC.barrasAgrupadas(
  [{clave:'a',etiqueta:'A'},{clave:'b',etiqueta:'B'}],
  [{etiqueta:'Leads', color:'#60a5fa', valores:{a:10}}],
  {etiqueta:'x'}, 'oscuro');
console.log((h.match(/<rect /g) || []).length);
""", tmp_path)
    assert salida.strip() == "1"


@sin_node
def test_sin_periodos_avisa_en_vez_de_dibujar_un_eje_vacio(tmp_path):
    salida = _correr(_MESES + """
console.log(SC.barrasAgrupadas([], series, {etiqueta:'Mes a mes'}, 'oscuro'));
""", tmp_path)
    assert "sc-vacio" in salida and "Mes a mes" in salida


@sin_node
def test_el_tooltip_dice_mes_serie_y_valor(tmp_path):
    salida = _correr(_MESES + """
var h = SC.barrasAgrupadas(periodos, series, {etiqueta:'x'}, 'oscuro');
console.log(h.indexOf('<title>Marzo · Leads: 59</title>') >= 0);
""", tmp_path)
    assert salida.strip() == "true"


# ── barrasSimples ──────────────────────────────────────────────────────────

_FILAS = """
var filas = [
  {etiqueta:'ARG-CH', valor:0.12, nota:'sobre 8 · muestra chica'},
  {etiqueta:'UY',     valor:0.31, nota:'sobre 96'},
  {etiqueta:'Form',   valor:0.05, nota:'sobre 40'}
];
"""


@sin_node
def test_ordena_de_mayor_a_menor(tmp_path):
    salida = _correr(_FILAS + """
var h = SC.barrasSimples(filas, {etiqueta:'Tasa de demo'}, 'oscuro');
console.log((h.match(/class="sc-barra-nom">([^<]+)</g) || [])
  .map(s => s.replace(/.*>/, '').replace(/<$/, '')).join(','));
""", tmp_path)
    assert salida.strip() == "UY,ARG-CH,Form"


@sin_node
def test_la_incertidumbre_se_dice_con_palabras_no_con_un_bigote(tmp_path):
    """Es el reemplazo del intervalo de confianza. El dato no se pierde: pasa
    de un bigote que nadie sabe leer a un texto que dice sobre cuantos se
    calculo y si la muestra es chica."""
    salida = _correr(_FILAS + """
var h = SC.barrasSimples(filas, {etiqueta:'x'}, 'oscuro');
console.log(h.indexOf('muestra chica') >= 0, h.indexOf('sobre 96') >= 0);
""", tmp_path)
    assert salida.strip() == "true true"


@sin_node
def test_la_barra_mas_larga_llena_el_ancho(tmp_path):
    """El largo es proporcional al valor y la mayor marca el tope: con valores
    de 0,05 a 0,31 sobre una escala de 0 a 1 no se distinguiria nada."""
    salida = _correr(_FILAS + """
var h = SC.barrasSimples(filas, {etiqueta:'x'}, 'oscuro');
console.log(h.indexOf('width:100.0%') >= 0);
""", tmp_path)
    assert salida.strip() == "true"


@sin_node
def test_el_largo_es_proporcional_al_valor(tmp_path):
    salida = _correr("""
var h = SC.barrasSimples(
  [{etiqueta:'a', valor:10},{etiqueta:'b', valor:5}], {etiqueta:'x'}, 'oscuro');
console.log((h.match(/width:([\\d.]+)%/g) || []).join(','));
""", tmp_path)
    assert salida.strip() == "width:100.0%,width:50.0%"


@sin_node
def test_una_fila_sin_valor_no_se_dibuja_como_cero(tmp_path):
    salida = _correr("""
var h = SC.barrasSimples(
  [{etiqueta:'a', valor:10},{etiqueta:'b', valor:null}], {etiqueta:'x'}, 'oscuro');
console.log((h.match(/class="sc-barra-fila"/g) || []).length);
""", tmp_path)
    assert salida.strip() == "1"


@sin_node
def test_sin_ninguna_fila_con_valor_avisa(tmp_path):
    salida = _correr("""
console.log(SC.barrasSimples(
  [{etiqueta:'a', valor:null}], {etiqueta:'Tasa de demo'}, 'oscuro'));
""", tmp_path)
    assert "sc-vacio" in salida and "Tasa de demo" in salida


@sin_node
def test_el_color_lo_manda_la_fila_si_lo_trae(tmp_path):
    """El color sigue a la campana. Si lo eligiera la posicion, filtrar una
    campana repintaria a las que quedan y el panel contaria otra historia."""
    salida = _correr("""
var h = SC.barrasSimples(
  [{etiqueta:'UY', valor:3, color:'#60a5fa'}], {etiqueta:'x'}, 'oscuro');
console.log(h.indexOf('background:#60a5fa') >= 0);
""", tmp_path)
    assert salida.strip() == "true"


@sin_node
def test_la_ayuda_va_debajo_del_titulo(tmp_path):
    """Primero que grafico es, despues como leerlo. Al reves se lee la
    explicacion de algo que todavia no tiene nombre."""
    salida = _correr("""
var h = SC.barrasSimples([{etiqueta:'a', valor:1}],
  {etiqueta:'Tasa de demo', ayuda:'De cada 100 leads, cuantos llegaron.'},
  'oscuro');
console.log(h.indexOf('Tasa de demo') < h.indexOf('De cada 100 leads'));
""", tmp_path)
    assert salida.strip() == "true"


@sin_node
def test_sin_ayuda_no_deja_un_hueco(tmp_path):
    salida = _correr("""
var h = SC.barrasSimples([{etiqueta:'a', valor:1}], {etiqueta:'x'}, 'oscuro');
console.log(h.indexOf('sc-barras-ayuda') >= 0);
""", tmp_path)
    assert salida.strip() == "false"


@sin_node
def test_los_valores_salen_con_su_formato(tmp_path):
    salida = _correr("""
var h = SC.barrasSimples([{etiqueta:'a', valor:0.315}],
  {etiqueta:'x', formato:'porcentaje'}, 'oscuro');
console.log(h.indexOf('%') >= 0);
""", tmp_path)
    assert salida.strip() == "true"


@sin_node
def test_una_etiqueta_con_html_no_se_interpreta(tmp_path):
    salida = _correr("""
var h = SC.barrasSimples([{etiqueta:'<img onerror=x>', valor:1, nota:'<b>'}],
  {etiqueta:'x'}, 'oscuro');
console.log(h.indexOf('<img') >= 0, h.indexOf('<b>') >= 0);
""", tmp_path)
    assert salida.strip() == "false false"


# ── La linea del historico ─────────────────────────────────────────────────
#
# Juan: "quiero que las graficas se comparen con el historico, para saber si
# estamos mas arriba/abajo". Una barra sola no dice si 18 dolares por lead esta
# bien; contra un historico de 12 dice que empeoro.

@sin_node
def test_dibuja_la_linea_del_historico(tmp_path):
    salida = _correr(_MESES + """
var h = SC.barrasAgrupadas(periodos, [series[0]],
  {etiqueta:'Leads', referencia:{valor:20, etiqueta:'histórico'}}, 'oscuro');
console.log((h.match(/stroke-dasharray="6 4"/g) || []).length);
""", tmp_path)
    assert salida.strip() == "1"


@sin_node
def test_la_linea_dice_su_valor(tmp_path):
    """Una linea punteada sin numero obliga a estimarla contra la grilla, que
    es justo lo que se queria evitar."""
    salida = _correr(_MESES + """
var h = SC.barrasAgrupadas(periodos, [series[0]],
  {etiqueta:'Leads', referencia:{valor:20, etiqueta:'histórico'}}, 'oscuro');
console.log(h.indexOf('histórico 20') >= 0);
""", tmp_path)
    assert salida.strip() == "true"


@sin_node
def test_un_historico_mas_alto_que_todo_igual_se_ve(tmp_path):
    """El caso que rompe el grafico: si la referencia no entra al maximo, la
    linea se dibuja fuera del area visible y el grafico dice "estamos igual"
    justo cuando mas distinto esta.

    Se comprueba por la geometria, no por la presencia de la linea: la `y` de
    la referencia tiene que caer DENTRO del area de dibujo.
    """
    salida = _correr(r"""
var h = SC.barrasAgrupadas(
  [{clave:'a', etiqueta:'A'}],
  [{etiqueta:'Leads', color:'#60a5fa', valores:{a:5}}],
  {etiqueta:'Leads', alto:300, referencia:{valor:500, etiqueta:'histórico'}},
  'oscuro');
var m = h.match(/stroke-dasharray="6 4"[^>]*/);
var y = parseFloat(/y1="([\d.]+)"/.exec(
  /<line[^>]*stroke-dasharray="6 4"[^>]*>/.exec(h)[0])[1]);
console.log(y > 0 && y < 300);
""", tmp_path)
    assert salida.strip() == "true"


@sin_node
def test_sin_referencia_no_dibuja_ninguna_linea(tmp_path):
    """Mirando el primer mes con datos no hay historico. Una linea en cero
    parece un dato y no lo es."""
    salida = _correr(_MESES + """
var h = SC.barrasAgrupadas(periodos, [series[0]], {etiqueta:'Leads'}, 'oscuro');
console.log(h.indexOf('stroke-dasharray') >= 0);
""", tmp_path)
    assert salida.strip() == "false"


@sin_node
def test_una_referencia_nula_tampoco_dibuja(tmp_path):
    """Sin leads historicos no hay CPL historico: `valor` viene en null y no es
    un historico de cero."""
    salida = _correr(_MESES + """
var h = SC.barrasAgrupadas(periodos, [series[0]],
  {etiqueta:'x', referencia:{valor:null, etiqueta:'histórico'}}, 'oscuro');
console.log(h.indexOf('stroke-dasharray') >= 0);
""", tmp_path)
    assert salida.strip() == "false"


@sin_node
def test_la_linea_no_va_pintada_de_verde_ni_de_rojo(tmp_path):
    """Estar arriba es bueno en leads y malo en costo por lead, y el grafico no
    sabe cual de los dos esta dibujando. Un color mentiria en la mitad."""
    salida = _correr(_MESES + """
var h = SC.barrasAgrupadas(periodos, [series[0]],
  {etiqueta:'x', referencia:{valor:20}}, 'oscuro');
var linea = /<line[^>]*stroke-dasharray[^>]*>/.exec(h)[0];
console.log(linea.indexOf(SC.PALETA.mudo.oscuro) >= 0);
""", tmp_path)
    assert salida.strip() == "true"


@sin_node
def test_el_grafico_de_lineas_tambien_lleva_el_historico(tmp_path):
    """Los dos de campanas volvieron a ser lineas y necesitan la misma vara."""
    salida = _correr(r"""
SC._resetColores();
var h = SC.serieMulti(
  [{campana:'UY', puntos:[{x:'Semana 1',y:10},{x:'Semana 2',y:14}]}],
  {etiqueta:'Costo por demo', referencia:{valor:64, etiqueta:'histórico'}},
  'oscuro');
var y = parseFloat(/y1="([\d.]+)"/.exec(
  /<line[^>]*stroke-dasharray="6 4"[^>]*>/.exec(h)[0])[1]);
console.log(h.indexOf('histórico 64') >= 0, y > 0 && y < 300);
""", tmp_path)
    assert salida.strip() == "true true"
