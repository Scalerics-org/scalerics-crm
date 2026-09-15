"""El boton "Cargar" al final del simulador.

Pedido de Juan despues de probarlo: completar los datos y tocar "Cargar" para
ver el calculo. El recalculo en vivo sigue; el boton recalcula con la misma
funcion y lleva la vista a las tarjetas, que en el celular quedan abajo de todo.
"""

import re
import shutil
import subprocess

import pytest

import dashboard

HTML = dashboard.DASHBOARD_HTML

sin_node = pytest.mark.skipif(shutil.which("node") is None,
                              reason="node no esta instalado")


def _funcion(nombre: str) -> str:
    m = re.search(r"\nfunction " + nombre + r"\(.*?\n\}", HTML, re.S)
    assert m, f"no encontre la funcion {nombre}"
    return m.group(0)


def test_el_boton_esta_al_final_de_los_datos_y_antes_de_los_resultados():
    panel = HTML[HTML.index('id="simulador-panel"'):]
    boton = panel.index('id="sim-cargar"')
    assert panel.index("Supuestos del embudo") < boton < panel.index('class="sim-resultados"')
    assert re.search(r'id="sim-cargar"[^>]*onclick="simCargar\(\)"[^>]*>Cargar</button>', panel)


def test_cargar_usa_el_mismo_calculo_que_el_vivo():
    """Un segundo camino de calculo podria dar otro numero que el de las tarjetas."""
    js = _funcion("simCargar")
    assert "simRecalcular()" in js
    assert "simCalcular(" not in js


def _correr(cuerpo: str, tmp_path) -> None:
    fuente = "\n".join([
        _funcion("simCargar"),
        """
        function assert(c, m) { if (!c) throw new Error(m); }
        let resultado = {cajaDelMes: 120};
        let recalculos = 0;
        function simRecalcular() { recalculos++; return resultado; }
        const nota = {textContent: ''};
        const tarjetas = {vistas: 0, scrollIntoView() { this.vistas++; }};
        const document = {getElementById: id =>
          id === 'sim-cargar-nota' ? nota : id === 'sim-tarjetas' ? tarjetas : null};
        """,
        cuerpo,
    ])
    archivo = tmp_path / "cargar.js"
    archivo.write_text(fuente, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True,
                       encoding="utf-8")
    assert r.returncode == 0, r.stderr


@sin_node
def test_cargar_recalcula_y_lleva_a_los_resultados(tmp_path):
    _correr("""
      const r = simCargar();
      assert(recalculos === 1, 'no recalculo');
      assert(r && r.cajaDelMes === 120, 'no devolvio el resultado');
      assert(tarjetas.vistas === 1, 'no llevo a las tarjetas');
      assert(nota.textContent.includes('Listo'), nota.textContent);
    """, tmp_path)


@sin_node
def test_si_todavia_no_cargo_finanzas_avisa_y_no_se_mueve(tmp_path):
    _correr("""
      resultado = null;
      assert(simCargar() === null, 'devolvio algo sin datos');
      assert(tarjetas.vistas === 0, 'se movio sin resultados');
      assert(nota.textContent.includes('Finanzas'), nota.textContent);
    """, tmp_path)
