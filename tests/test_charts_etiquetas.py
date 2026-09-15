"""Ningun rotulo de un grafico se sale del viewBox. Corrido de verdad en node.

Existe por lo que vio Juan el 14/9 en "Mes a mes: que parte del gasto no esta
registrada": se leia "eptiembre 2026". `SC.barrasDivergentes` tenia 92px fijos
de margen izquierdo y "Septiembre 2026" mide unos 100 a letra 11.

La cuenta del ancho es independiente de la del codigo a proposito: 0,6 del
tamano de letra por caracter, el promedio real de Inter. Si el test usara la
misma funcion que el grafico, no probaria nada.

El nombre de mes mas largo que puede llegar es "Septiembre 2026" (15
caracteres): el panel de Finanzas escribe "Septiembre" y el de Marketing
"Setiembre".
"""

import html
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
CHARTS = RAIZ / "static" / "charts.js"

sin_node = pytest.mark.skipif(shutil.which("node") is None,
                              reason="node no esta instalado")

MES_LARGO = "Septiembre 2026"
_POR_CARACTER = 0.6


def _svg(js, tmp_path):
    archivo = tmp_path / "etiquetas.js"
    archivo.write_text(
        "globalThis.window = globalThis;\n" + CHARTS.read_text(encoding="utf-8")
        + "\nconst SC = globalThis.SC;\nSC._resetColores && SC._resetColores();\n"
        + "console.log(JSON.stringify(" + js + "));\n", encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True,
                       encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _fuera_del_viewbox(svg):
    """Los <text> que, con la estimacion de ancho, se salen del viewBox."""
    ancho = float(re.search(r'viewBox="0 0 ([\d.]+) ', svg).group(1))
    fuera = []
    for attrs, texto in re.findall(r"<text ([^>]*)>([^<]*)</text>", svg):
        a = dict(re.findall(r'([\w-]+)="([^"]*)"', attrs))
        x = float(a["x"])
        w = len(html.unescape(texto)) * float(a.get("font-size", 10)) * _POR_CARACTER
        ancla = a.get("text-anchor", "start")
        izq = {"start": x, "middle": x - w / 2, "end": x - w}[ancla]
        if izq < 0 or izq + w > ancho:
            fuera.append((html.unescape(texto), round(izq, 1), round(izq + w, 1), ancho))
    return fuera


@sin_node
def test_septiembre_entra_en_las_barras_divergentes(tmp_path):
    svg = _svg("""SC.barrasDivergentes([
      {etiqueta: 'Agosto 2026', valor: -12345.67},
      {etiqueta: '""" + MES_LARGO + """', valor: 1234.56}
    ], {etiqueta: 't', formato: 'moneda', cero: 'coinciden'}, 'oscuro')""", tmp_path)
    assert MES_LARGO in svg
    assert _fuera_del_viewbox(svg) == []


@sin_node
def test_con_nombres_cortos_las_divergentes_quedan_como_estaban(tmp_path):
    """El margen de antes es el piso: con meses cortos nada se mueve."""
    svg = _svg("""SC.barrasDivergentes([{etiqueta: 'Mayo 2026', valor: 10}],
      {etiqueta: 't', formato: 'moneda'}, 'oscuro')""", tmp_path)
    assert re.search(r'<text x="82" [^>]*text-anchor="end"[^>]*>Mayo 2026<', svg)


@sin_node
def test_septiembre_entra_en_el_eje_x_de_las_barras_agrupadas(tmp_path):
    svg = _svg("""SC.barrasAgrupadas(
      ['Marzo 2026','Abril 2026','Mayo 2026','Junio 2026','Julio 2026',
       'Agosto 2026','""" + MES_LARGO + """'].map((e, i) => ({clave: 'p' + i, etiqueta: e})),
      [{etiqueta: 'Gasto', color: '#0088CC',
        valores: {p0: 10, p1: 20, p2: 30, p3: 40, p4: 50, p5: 60, p6: 12345.67}}],
      {etiqueta: 't', formato: 'moneda', numeros: false, ancho: 520}, 'oscuro')""",
               tmp_path)
    assert MES_LARGO in svg
    assert _fuera_del_viewbox(svg) == []


@sin_node
def test_la_serie_no_corta_ni_la_plata_ni_el_mes(tmp_path):
    """Con 52px fijos, "25.000,00" se salia por la izquierda."""
    svg = _svg("""SC.serie([{x: 'Agosto 2026', y: 12345.67},
                            {x: '""" + MES_LARGO + """', y: 23456.78}],
      {etiqueta: 't', formato: 'moneda'}, 'oscuro')""", tmp_path)
    assert MES_LARGO in svg
    assert _fuera_del_viewbox(svg) == []


@sin_node
def test_la_serie_multiple_no_corta_ni_la_plata_ni_el_mes(tmp_path):
    svg = _svg("""SC.serieMulti([{campana: 'Pauta', puntos: [
        {x: 'Agosto 2026', y: 12345.67}, {x: '""" + MES_LARGO + """', y: 23456.78}]}],
      {etiqueta: 't', formato: 'moneda',
       referencia: {valor: 20000, etiqueta: 'histórico'}}, 'oscuro')""", tmp_path)
    assert MES_LARGO in svg
    assert _fuera_del_viewbox(svg) == []


@sin_node
def test_un_rotulo_de_fila_largo_entra_en_la_matriz(tmp_path):
    svg = _svg("""SC.matriz([{fila: 0, columna: 0, n: 1, titulo: 'x'}],
      {etiqueta: 't', maximo: 1, filas: [{clave: 0, etiqueta: '""" + MES_LARGO + """'}],
       columnas: [{clave: 0, etiqueta: '00h'}]}, 'oscuro')""", tmp_path)
    assert MES_LARGO in svg
    assert _fuera_del_viewbox(svg) == []
