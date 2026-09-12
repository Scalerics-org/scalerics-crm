"""Los números crudos, agrupados.

Eran ~70 filas en una sola lista con la etiqueta "Gasto" repetida una vez por
campaña y nada que dijera a cuál pertenecía cada una. La densidad es a
propósito —es la tabla que permite auditar cualquier número del panel— pero la
densidad sin orden no se puede leer. Juan, textual: "me gusta que este lleno de
datos pero tiene que ser un poco mas claro, para mi divilos en categorias".

Se ejecuta el pintado de verdad y se mira el HTML que quedó, no el código: un
agrupado que se arma bien y se escribe en el id equivocado pasa cualquier test
estático.
"""

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import dashboard

RAIZ = Path(__file__).resolve().parent.parent

# El arnés del DOM falso y el dossier de prueba viven en el test del pintado;
# `tests/` no es un paquete importable, así que se carga por ruta.
_spec = importlib.util.spec_from_file_location(
    "_panel_se_pinta", Path(__file__).with_name("test_panel_se_pinta.py"))
_panel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_panel)
_ARNES, _dossier_de_prueba = _panel._ARNES, _panel._dossier_de_prueba

sin_node = pytest.mark.skipif(shutil.which("node") is None,
                              reason="node no esta instalado")


@pytest.fixture(scope="module")
def tabla(tmp_path_factory):
    """El innerHTML de `mk-tabla` después de pintar el panel entero."""
    if shutil.which("node") is None:
        pytest.skip("node no esta instalado")
    bloques = re.findall(r"<script>(.*?)</script>", dashboard.DASHBOARD_HTML, re.S)
    charts = (RAIZ / "static" / "charts.js").read_text(encoding="utf-8")
    dossier = json.dumps(_dossier_de_prueba(), ensure_ascii=False)

    archivo = tmp_path_factory.mktemp("crudos") / "pintar.js"
    archivo.write_text(
        _ARNES + charts + "\n" + "\n".join(bloques) + "\n"
        + f"_mkDossier = {dossier};\n_mkPintar();\n"
        + "console.log(JSON.stringify(_els['mk-tabla'].innerHTML));\n",
        encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True,
                       text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@sin_node
def test_hay_un_grupo_por_campana_por_segmento_y_por_bloque(tabla):
    """El dossier de prueba trae 3 campañas, 1 segmento, conciliación y
    tiempos. `recordatorios` viene vacío y no tiene que aparecer: un grupo sin
    filas es un título que ocupa lugar y no dice nada."""
    assert len(re.findall(r'class="sc-crudo-grupo"', tabla)) == 6
    assert "Recordatorios" not in tabla


@sin_node
def test_cada_grupo_dice_de_que_campana_es(tabla):
    """Es el problema original: "Gasto" aparecía tres veces y no se sabía de
    cuál campaña era ninguna de las tres."""
    for campana in ("UY", "ARG", "todas"):
        assert f'class="sc-crudo-tit">{campana}<' in tabla


@sin_node
def test_cada_grupo_dice_cuantas_metricas_tiene(tabla):
    assert "16 métricas" in tabla


@sin_node
def test_las_filas_traen_con_que_filtrar(tabla):
    """El buscador lee `data-busca` y no el texto de las celdas: recorrer
    celdas en cada tecla sobre 70 filas se nota al tipear."""
    assert tabla.count("data-busca=") == len(re.findall(r"<tr ", tabla))
    assert tabla.count("data-busca=") > 40


@sin_node
def test_sin_periodo_anterior_lo_dice_en_palabras(tabla):
    """Un guión ahí se leería como "no cambió", que es otra cosa."""
    assert "sin período anterior" in tabla


@sin_node
def test_el_delta_lleva_el_signo_adelante(tmp_path):
    """`fmtDelta` devuelve el valor absoluto y el sentido por separado. Sin el
    signo, "12" no dice si subió o bajó, que es lo único que se le pide a esa
    columna. El dossier de prueba no trae deltas, así que acá se inyecta uno.
    """
    d = _dossier_de_prueba()
    d["campanas"][0]["metricas"][0]["delta_periodo_anterior"] = 120.0
    d["campanas"][0]["metricas"][1]["delta_periodo_anterior"] = -37.0

    bloques = re.findall(r"<script>(.*?)</script>", dashboard.DASHBOARD_HTML, re.S)
    charts = (RAIZ / "static" / "charts.js").read_text(encoding="utf-8")
    archivo = tmp_path / "pintar.js"
    archivo.write_text(
        _ARNES + charts + "\n" + "\n".join(bloques) + "\n"
        + f"_mkDossier = {json.dumps(d, ensure_ascii=False)};\n_mkPintar();\n"
        + "console.log(JSON.stringify(_els['mk-tabla'].innerHTML));\n",
        encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True,
                       text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    html = json.loads(r.stdout.strip().splitlines()[-1])

    signos = [c[c.index(">") + 1:c.index(">") + 2]
              for c in re.findall(r'class="sc-crudo-delta" data-signo="sube"[^>]*>.',
                                  html)]
    assert signos and all(s == "+" for s in signos), signos
    assert re.search(r'data-signo="baja"[^>]*>−', html)


@sin_node
def test_el_delta_no_va_pintado_de_verde_por_subir(tabla):
    """Subir es bueno en `leads` y malo en `cpl`, y la métrica no trae para qué
    lado es mejor. Un color por dirección mentiría en media tabla."""
    celdas = re.findall(r'class="sc-crudo-delta"[^>]*', tabla)
    assert celdas, "no hay ninguna celda de delta"
    assert not any("verde" in c or "rojo" in c for c in celdas)


@sin_node
def test_la_muestra_chica_se_dice_con_palabras(tabla):
    """Antes era un "⚠" pegado al número. El CRM no usa emojis, y además un
    símbolo solo no dice qué problema tiene la fila."""
    assert "⚠" not in tabla


# ── El filtro ──────────────────────────────────────────────────────────────
#
# Se prueba contra elementos falsos y no contra un DOM de verdad: lo único que
# `_mkFiltrarCrudos` toca es `querySelectorAll`, `dataset.busca` y
# `style.display`. Armar eso a mano cuesta menos que traer un parser de HTML, y
# lo que se quiere proteger es la lógica, no el selector del navegador.
_FILTRO = r"""
function _fila(busca) { return { dataset: { busca }, style: {} }; }
function _grupo(filas) {
  return { _filas: filas, style: {},
           querySelectorAll: () => filas };
}
const g1 = _grupo([_fila('gasto campana.uy.gasto'),
                   _fila('costo por demo campana.uy.costo_demo')]);
const g2 = _grupo([_fila('gasto campana.arg.gasto')]);
const _caja = { value: '' };
const _nada = { style: {} };
const _raiz = { querySelectorAll: () => [g1, g2] };
globalThis.document = { getElementById: (id) =>
  id === 'mk-tabla-buscar' ? _caja :
  id === 'mk-tabla' ? _raiz :
  id === 'mk-tabla-nada' ? _nada : null };
"""


def _correr_filtro(js, tmp_path):
    bloques = re.findall(r"<script>(.*?)</script>", dashboard.DASHBOARD_HTML, re.S)
    # Solo la declaración de la función: correr el panel entero pediría el
    # dossier y todos los gráficos, que acá no hacen falta.
    fuente = "\n".join(bloques)
    i = fuente.index("function _mkFiltrarCrudos()")
    j = fuente.index("\n}", i) + 2
    archivo = tmp_path / "filtro.js"
    archivo.write_text(_FILTRO + fuente[i:j] + "\n" + js + "\n", encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True,
                       text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@sin_node
def test_el_filtro_esconde_las_filas_que_no_coinciden(tmp_path):
    salida = _correr_filtro("""
_caja.value = 'costo_demo';
_mkFiltrarCrudos();
console.log(JSON.stringify(
  [].concat(g1._filas, g2._filas).map(f => f.style.display)));
""", tmp_path)
    assert json.loads(salida) == ["none", "", "none"]


@sin_node
def test_un_grupo_sin_filas_vivas_desaparece(tmp_path):
    """El título solo no dice nada y empuja hacia abajo a los grupos que sí
    tienen algo."""
    salida = _correr_filtro("""
_caja.value = 'costo_demo';
_mkFiltrarCrudos();
console.log(JSON.stringify([g1.style.display, g2.style.display]));
""", tmp_path)
    assert json.loads(salida) == ["", "none"]


@sin_node
def test_al_vaciar_la_caja_vuelven_todas(tmp_path):
    """Esconde, no borra: si borrara habría que repintar el panel entero para
    volver atrás."""
    salida = _correr_filtro("""
_caja.value = 'costo_demo'; _mkFiltrarCrudos();
_caja.value = '';           _mkFiltrarCrudos();
console.log(JSON.stringify(
  [].concat(g1._filas, g2._filas).map(f => f.style.display)
    .concat([g1.style.display, g2.style.display])));
""", tmp_path)
    assert json.loads(salida) == ["", "", "", "", ""]


@sin_node
def test_si_no_coincide_nada_lo_dice(tmp_path):
    """Todo escondido y sin aviso se ve como un bloque que se rompió."""
    salida = _correr_filtro("""
_caja.value = 'zzzz'; _mkFiltrarCrudos();
const conNada = _nada.style.display;
_caja.value = '';     _mkFiltrarCrudos();
console.log(JSON.stringify([conNada, _nada.style.display]));
""", tmp_path)
    assert json.loads(salida) == ["", "none"]


@sin_node
def test_el_filtro_no_distingue_mayusculas(tmp_path):
    salida = _correr_filtro("""
_caja.value = '  COSTO_DEMO ';
_mkFiltrarCrudos();
console.log(g1._filas[1].style.display === '');
""", tmp_path)
    assert salida == "true"


@sin_node
def test_numerador_y_denominador_van_juntos(tabla):
    """Eran dos columnas y la tabla ya tenía siete. Juntos ocupan una y se leen
    igual: el numerador solo no significa nada sin su denominador."""
    assert "Numerador / denominador" in tabla
