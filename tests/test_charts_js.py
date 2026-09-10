"""El modulo de graficos tiene que parsear y sus funciones puras dar bien.

Sigue el patron de tests/test_dashboard_js.py: node --check desde pytest, y se
saltea si node no esta instalado para no romperle la suite a nadie por una
dependencia que el proyecto no tiene.
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


def _correr(js: str, tmp_path):
    """Carga charts.js en node y corre `js`, que tiene que imprimir JSON."""
    archivo = tmp_path / "correr.js"
    archivo.write_text(
        "globalThis.window = globalThis;\n"
        + CHARTS.read_text(encoding="utf-8")
        + "\nconst SC = globalThis.SC;\n"
        + js + "\n", encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@sin_node
def test_el_modulo_compila():
    r = subprocess.run(["node", "--check", str(CHARTS)],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"charts.js no compila:\n{r.stderr}"


@sin_node
def test_la_paleta_es_la_validada(tmp_path):
    """Si alguien cambia un color hay que volver a correr el validador de la
    skill de dataviz contra las dos superficies. El test existe para que ese
    cambio no pase inadvertido."""
    p = _correr("console.log(JSON.stringify(SC.PALETA));", tmp_path)
    assert p["claro"][:5] == ["#0088CC", "#A855F7", "#0D9488", "#EA580C", "#4F46E5"]
    assert p["oscuro"][:5] == ["#0088CC", "#A855F7", "#0D9488", "#EA580C", "#6366F1"]
    # El indigo cambia de paso: #4F46E5 sobre #111827 da 2,82:1, bajo el piso.
    assert p["claro"][4] != p["oscuro"][4]


@sin_node
def test_sin_campana_va_en_gris(tmp_path):
    """Ese bucket no es una categoria mas: es un desconocido."""
    r = _correr(
        "SC._resetColores();"
        "console.log(JSON.stringify(["
        "  SC.colorDeCampana('(sin campaña)', 0, 'oscuro'),"
        "  SC.colorDeCampana('Leads - UY - 2026', 0, 'oscuro')]));", tmp_path)
    assert r[0] == "#64748B"
    assert r[1] != r[0]


@sin_node
def test_el_color_sigue_a_la_campana_no_a_la_posicion(tmp_path):
    """Filtrar no puede repintar a las que quedan."""
    r = _correr(
        "SC._resetColores();"
        "console.log(JSON.stringify(["
        "  SC.colorDeCampana('Leads - UY - 2026', 2, 'claro'),"
        "  SC.colorDeCampana('otra', 0, 'claro'),"
        "  SC.colorDeCampana('Leads - UY - 2026', 0, 'claro')]));", tmp_path)
    assert r[0] == r[2]
    assert r[1] != r[0]


@sin_node
def test_una_septima_campana_cae_en_el_gris(tmp_path):
    """No se generan hues nuevos."""
    r = _correr(
        "SC._resetColores();"
        "const nombres = ['a','b','c','d','e','f','g'];"
        "console.log(JSON.stringify(nombres.map((n,i) => "
        "  SC.colorDeCampana(n, i, 'claro'))));", tmp_path)
    assert len(set(r[:5])) == 5
    assert r[5] == "#94A3B8"
    assert r[6] == "#94A3B8"


@sin_node
def test_los_numeros_van_en_formato_uruguayo(tmp_path):
    r = _correr(
        "console.log(JSON.stringify(["
        "  SC.fmt(1234.5, 'moneda'),"
        "  SC.fmt(0.1084, 'porcentaje'),"
        "  SC.fmt(4500, 'numero'),"
        "  SC.fmt(1234567, 'numero')]));", tmp_path)
    assert r[0] == "1.234,50"
    assert r[1] == "10,8%"
    assert r[2] == "4.500"
    assert r[3] == "1.234.567"


@sin_node
def test_un_null_dice_sin_datos_y_no_cero(tmp_path):
    """costo() devuelve null cuando no hay de que dividir. Dibujarlo como cero
    diria que el costo por demo fue cero, que es otra afirmacion."""
    r = _correr(
        "console.log(JSON.stringify([SC.fmt(null,'numero'),"
        " SC.fmt(undefined,'moneda'), SC.fmt(0,'moneda')]));", tmp_path)
    assert r[0] == "sin datos"
    assert r[1] == "sin datos"
    assert r[2] == "0,00", "un cero de verdad si se muestra"


@sin_node
def test_la_escala_mapea_los_extremos(tmp_path):
    r = _correr(
        "const e = SC.escalaLineal([0, 100], [0, 400]);"
        "console.log(JSON.stringify([e(0), e(50), e(100)]));", tmp_path)
    assert r == [0, 200, 400]


@sin_node
def test_una_escala_de_dominio_cero_no_divide_por_cero(tmp_path):
    r = _correr(
        "const e = SC.escalaLineal([5, 5], [0, 400]);"
        "console.log(JSON.stringify(e(5)));", tmp_path)
    assert r == 0


@sin_node
def test_los_ticks_son_numeros_redondos(tmp_path):
    r = _correr("console.log(JSON.stringify(SC.ticks(0, 97, 5)));", tmp_path)
    assert r[0] == 0
    assert r[-1] >= 97
    assert r == sorted(r)
    paso = r[1] - r[0]
    assert all(abs((b - a) - paso) < 1e-6 for a, b in zip(r, r[1:]))


@sin_node
def test_los_ticks_de_un_rango_plano_no_cuelgan(tmp_path):
    r = _correr("console.log(JSON.stringify(SC.ticks(7, 7, 5)));", tmp_path)
    assert r == [7]


@sin_node
def test_el_texto_se_escapa_antes_de_entrar_al_svg(tmp_path):
    r = _correr(
        "console.log(JSON.stringify(SC.esc('Leads & <UY>')));", tmp_path)
    assert "<" not in r
    assert "&amp;" in r and "&lt;" in r


@sin_node
def test_el_delta_trae_su_signo(tmp_path):
    r = _correr(
        "console.log(JSON.stringify(["
        "  SC.fmtDelta(12.5, 'moneda'), SC.fmtDelta(-3, 'moneda'),"
        "  SC.fmtDelta(0, 'moneda'), SC.fmtDelta(null, 'moneda')]));", tmp_path)
    assert r[0]["signo"] == "sube"
    assert r[1]["signo"] == "baja"
    assert r[2]["signo"] == "igual"
    assert r[3]["signo"] == "sin_comparacion"
    assert "-" not in r[1]["texto"], "el signo va aparte, no pegado al numero"
