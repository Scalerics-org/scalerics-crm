"""El navegador de período del panel de Marketing, probado de verdad en node.

La aritmetica de fechas es donde se esconden los bugs que nadie ve: el mes que
tiene 28 dias, el mes en curso que no puede pedir hasta el 30 cuando estamos a
11, el "ultimos 90 dias" que en realidad pide 91. Nada de eso se nota mirando la
pantalla, asi que se prueba.

Mismo patron que test_charts_js.py: node desde pytest, y se saltea si node no
esta instalado para no romperle la suite a nadie.
"""

import json
import re
import shutil
import subprocess

import pytest

import dashboard

sin_node = pytest.mark.skipif(shutil.which("node") is None,
                              reason="node no esta instalado")

# Las funciones del periodo, extraidas del HTML. Se copian tal cual estan en
# produccion: si se testeara una copia, el test dejaria de hablar del codigo que
# corre de verdad.
_FUNCIONES = ("_mkISO", "_mkMesVisible", "_mkRango")


def _fuente() -> str:
    html = dashboard.DASHBOARD_HTML
    piezas = []
    for nombre in _FUNCIONES:
        m = re.search(r"^function " + nombre + r"\(.*?^\}", html, re.M | re.S)
        assert m, f"no encontre la funcion {nombre} en el dashboard"
        piezas.append(m.group(0))
    m = re.search(r"^var _MK_PISO = '([^']+)';", html, re.M)
    assert m, "no encontre _MK_PISO"
    return f"var _MK_PISO = '{m.group(1)}';\n" + "\n".join(piezas), m.group(1)


def _correr(rango, offset, hoy, tmp_path):
    """Corre `_mkRango()` con el reloj congelado en `hoy`."""
    fuente, _ = _fuente()
    archivo = tmp_path / "periodo.js"
    archivo.write_text(
        # Reloj congelado: si el test dependiera de la fecha real, pasaria hoy y
        # fallaria el mes que viene por motivos que no son el codigo.
        f"const FIJO = new Date('{hoy}T12:00:00');\n"
        "const RealDate = Date;\n"
        "Date = function (...a) { return a.length ? new RealDate(...a) "
        ": new RealDate(FIJO); };\n"
        "Date.prototype = RealDate.prototype;\n"
        f"var _mkMesOffset = {offset};\n"
        "var document = { getElementById: () => ({ value: "
        f"'{rango}'" " }) };\n"
        + fuente
        + "\nconsole.log(JSON.stringify(_mkRango()));\n",
        encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True,
                       text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@sin_node
def test_un_mes_entero_pasado_va_del_1_al_ultimo(tmp_path):
    """Agosto tiene 31. Pedirlo estando en setiembre trae el mes completo."""
    assert _correr("mes", -1, "2026-09-11", tmp_path) == ["2026-08-01", "2026-08-31"]


@sin_node
def test_el_mes_en_curso_se_corta_hoy(tmp_path):
    """Pedir hasta el 30 estando a 11 no agrega un solo dato y hace que el
    rotulo mienta sobre lo que se esta midiendo."""
    assert _correr("mes", 0, "2026-09-11", tmp_path) == ["2026-09-01", "2026-09-11"]


@sin_node
def test_febrero_no_se_calcula_a_mano(tmp_path):
    """El dia 0 del mes siguiente es el ultimo del actual: 28 o 29 sin saberlo."""
    assert _correr("mes", -1, "2026-03-05", tmp_path) == ["2026-02-01", "2026-02-28"]
    assert _correr("mes", -1, "2028-03-05", tmp_path) == ["2028-02-01", "2028-02-29"]


@sin_node
def test_retroceder_cruza_el_ano(tmp_path):
    assert _correr("mes", -2, "2027-01-15", tmp_path) == ["2026-11-01", "2026-11-30"]


@sin_node
def test_noventa_dias_son_noventa_contando_hoy(tmp_path):
    """El error de un dia clasico: restar 90 da 91 dias de ventana."""
    desde, hasta = _correr("90", 0, "2026-09-11", tmp_path)
    import datetime as dt
    dias = (dt.date.fromisoformat(hasta) - dt.date.fromisoformat(desde)).days + 1
    assert dias == 90, f"la ventana tiene {dias} dias"
    assert hasta == "2026-09-11"


@sin_node
def test_este_ano_arranca_el_primero_de_enero(tmp_path):
    assert _correr("anio", 0, "2026-09-11", tmp_path) == ["2026-01-01", "2026-09-11"]


@sin_node
def test_toda_la_historia_arranca_en_el_piso(tmp_path):
    _, piso = _fuente()
    assert _correr("todo", 0, "2026-09-11", tmp_path) == [piso, "2026-09-11"]


@sin_node
def test_personalizado_no_toca_las_fechas(tmp_path):
    """Devuelve null para que manden los campos Desde/Hasta del usuario."""
    assert _correr("libre", 0, "2026-09-11", tmp_path) is None


def test_el_piso_es_anterior_al_primer_dato():
    """Si algun dia hay datos mas viejos que el piso, "toda la historia" dejaria
    de mostrarlos en silencio. El primer lead de Meta es de marzo de 2026."""
    _, piso = _fuente()
    assert piso < "2026-03-01", piso


def test_el_navegador_de_mes_nace_oculto():
    """Nace oculto en el HTML y lo muestra `_mkPintarPeriodo` al cargar, segun el
    periodo elegido. Desde el 14/9 el default es "Un mes" (pedido de Juan: al
    entrar a Marketing lo primero abierto es un mes), asi que al cargar se ve."""
    html = dashboard.DASHBOARD_HTML
    m = re.search(r'id="mk-nav-mes"[^>]*', html)
    assert m and "display:none" in m.group(0), m.group(0) if m else "no esta"
    assert '<option value="mes" selected>' in html, "el default deberia ser un mes"
    assert '<option value="90" selected>' not in html
