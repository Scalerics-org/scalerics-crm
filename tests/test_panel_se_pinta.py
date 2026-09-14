"""Ejecuta el pintado del panel de verdad, contra un DOM falso.

Existe por un bug que ya pasó dos veces y que `node --check` NO ve: usar un
`const` antes de su declaración. Es sintaxis válida —compila— y revienta recién
en ejecución con un ReferenceError. Y como el pintado es una función sola, la
excepción corta el panel entero a partir de ahí: quedan los títulos, que son
HTML estático, y ningún gráfico. Se ve como "el panel está raro", no como un
error.

La primera vez fue `campanas` en el bloque del ranking. La segunda,
`nombreSemana` en las series. No hay tercera.

Esto no reemplaza a los tests de cada gráfico: verifica que la orquestación
corra de punta a punta y que cada contenedor termine con algo adentro.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import dashboard

RAIZ = Path(__file__).resolve().parent.parent

sin_node = pytest.mark.skipif(shutil.which("node") is None,
                              reason="node no esta instalado")

# Los contenedores que el pintado tiene que llenar. Si alguno queda vacío, o
# reventó antes de llegar o se lo está dibujando en un id que no existe.
_CONTENEDORES = ["mk-tiles", "mk-embudo", "mk-series", "mk-embudos",
                 "mk-evolucion", "mk-acumulado", "mk-ranking", "mk-campanas",
                 "mk-segmentos", "mk-tabla"]


def _dossier_de_prueba():
    """Un dossier con la forma real, chico pero con todos los bloques llenos.

    Con bloques vacíos el pintado toma los caminos de "sin datos" y no ejercita
    nada: el test pasaría con el panel roto.
    """
    def m(mid, valor, formato="numero"):
        return {"id": mid, "etiqueta": mid, "valor": valor, "formato": formato,
                "n": 20, "ic95": None, "muestra_chica": False, "fuente": "crm"}

    def campana(nombre, slug):
        return {"campana": nombre, "moneda": "USD", "metricas": [
            m(f"campana.{slug}.gasto", 500.0, "moneda"),
            m(f"campana.{slug}.impresiones", 10000),
            m(f"campana.{slug}.clics", 300),
            m(f"campana.{slug}.ctr", 0.03, "porcentaje"),
            m(f"campana.{slug}.leads_crm", 40),
            m(f"campana.{slug}.leads_meta", 42),
            m(f"campana.{slug}.cpl", 12.5, "moneda"),
            m(f"campana.{slug}.interesados", 20),
            m(f"campana.{slug}.agendadas", 12),
            m(f"campana.{slug}.demos", 8),
            m(f"campana.{slug}.presupuestos", 3),
            m(f"campana.{slug}.cierres", 1),
            m(f"campana.{slug}.costo_demo", 62.5, "moneda"),
            m(f"campana.{slug}.costo_presupuesto", 166.0, "moneda"),
            m(f"campana.{slug}.tasa_demo", 0.2, "porcentaje"),
            m(f"campana.{slug}.tasa_interes", 0.5, "porcentaje"),
        ]}

    def etapas():
        pares = [("leads", 40), ("interesados", 20), ("agendadas", 12),
                 ("demos", 8), ("presupuestos", 3), ("cierres", 1)]
        salida, previo = [], None
        for clave, n in pares:
            salida.append({"clave": clave, "etiqueta": clave, "n": n,
                           "tasa": (n / previo) if previo else None})
            previo = n
        return salida

    semanas = ["2026-06-15", "2026-06-22", "2026-06-29"]
    return {
        "periodo": {"desde": "2026-06-13", "hasta": "2026-09-11"},
        "campanas": [campana("UY", "uy"), campana("ARG", "arg"),
                     campana("todas", "todas")],
        "embudo_campanas": [{"campana": "UY", "etapas": etapas()},
                            {"campana": "ARG", "etapas": etapas()}],
        "serie_campanas": [{"campana": "UY", "puntos": [
            {"inicio": s, "semana": f"2026-W2{i}", "gasto": 100.0 + i,
             "leads": 5, "demos": 2, "cpl": 20.0, "costo_demo": 50.0,
             "gasto_acum": 100.0 * (i + 1), "leads_acum": 5 * (i + 1)}
            for i, s in enumerate(semanas)]}],
        "serie_semanal": [
            {"inicio": s, "semana": f"2026-W2{i}", "gasto": 100.0,
             "impresiones": 5000, "clics": 150, "leads_crm": 5,
             "leads_meta": 5, "cpl": 20.0}
            for i, s in enumerate(semanas)],
        "segmentos": [{"pregunta": "presupuesto", "etiqueta": "Presupuesto",
                       "n": 40, "valores_distintos": 2, "valores": [
                           {"valor_declarado": "menos_de_500", "n": 25,
                            "metricas": [m("seg.presupuesto.menos.tasa_demo",
                                           0.2, "porcentaje")]},
                           {"valor_declarado": "mas_de_1000", "n": 15,
                            "metricas": [m("seg.presupuesto.mas.tasa_demo",
                                           0.1, "porcentaje")]}]}],
        "conciliacion": [m("conciliacion.gasto_meta.2026_06", 600.0, "moneda"),
                         m("conciliacion.gasto_cargado.2026_06", 0.0, "moneda"),
                         m("conciliacion.brecha.2026_06", 600.0, "moneda")],
        "tiempos": [m("tiempos.dias_hasta_demo", 3.5)],
        "recordatorios": [],
        "hallazgos": [{"tipo": "sin_cierres", "severidad": "alta",
                       "titulo": "t", "cuerpo": "c",
                       "metricas_citadas": ["campana.uy.gasto"]}],
    }


_ARNES = r"""
// Un DOM mínimo: lo único que el pintado necesita es getElementById y poder
// escribirle innerHTML/textContent/value/style a lo que devuelve.
const _els = {};
function _el(id) {
  if (!_els[id]) {
    _els[id] = { id, innerHTML: '', textContent: '', value: '',
                 style: {}, dataset: {},
                 classList: { add(){}, remove(){}, toggle(){} },
                 querySelectorAll: () => [], querySelector: () => null,
                 addEventListener(){}, appendChild(){}, remove(){} };
  }
  return _els[id];
}
globalThis.document = {
  getElementById: _el,
  querySelectorAll: () => [],
  querySelector: () => null,
  body: { classList: { contains: () => false, add(){}, remove(){} } },
  createElement: () => _el('tmp'),
  addEventListener(){},
};
globalThis.window = globalThis;
globalThis.localStorage = { getItem: () => null, setItem(){}, removeItem(){} };
globalThis.fetch = () => Promise.resolve({ ok: true, json: () => ({}) });
globalThis.lucide = { createIcons(){} };
globalThis.alert = () => {};
globalThis.confirm = () => true;
"""


@sin_node
def test_el_panel_se_pinta_entero_sin_reventar(tmp_path):
    """El test que `node --check` no puede hacer: ejecutar.

    Si el pintado tira una excepción, los contenedores de después quedan vacíos
    y el panel se ve "raro" en vez de roto. Acá eso es un fallo con nombre.
    """
    bloques = re.findall(r"<script>(.*?)</script>", dashboard.DASHBOARD_HTML, re.S)
    charts = (RAIZ / "static" / "charts.js").read_text(encoding="utf-8")
    dossier = json.dumps(_dossier_de_prueba(), ensure_ascii=False)

    archivo = tmp_path / "pintar.js"
    archivo.write_text(
        _ARNES
        + charts + "\n"
        + "\n".join(bloques) + "\n"
        + f"_mkDossier = {dossier};\n"
        # El selector de campaña y el de tema existen como elementos del arnés y
        # devuelven '' , que es "todas" y tema oscuro: el camino por defecto.
        + "_mkPintar();\n"
        + "console.log(JSON.stringify(Object.fromEntries("
          "Object.entries(_els).map(([k, v]) => [k, (v.innerHTML || '').length]))));\n",
        encoding="utf-8")

    r = subprocess.run(["node", str(archivo)], capture_output=True,
                       text=True, encoding="utf-8")
    assert r.returncode == 0, (
        "el pintado del panel reventó:\n" + (r.stderr or "")[:2000])

    largos = json.loads(r.stdout.strip().splitlines()[-1])
    vacios = [c for c in _CONTENEDORES if largos.get(c, 0) == 0]
    assert not vacios, (
        f"estos contenedores quedaron vacíos: {vacios}. O el pintado cortó "
        "antes de llegar, o se está escribiendo en un id que no existe.")


# Hubo aquí un segundo test que buscaba el mismo bug leyendo el texto: por cada
# `const`, mirar si el nombre aparecía en una línea anterior. Se sacó porque
# daba falsos positivos y no había forma barata de arreglarlos: confundía los
# parámetros de las funciones flecha —`p`, `d`, `x`, `valor`— con variables del
# ámbito de afuera, y esos viven en su propio scope.
#
# Un test que grita sin motivo se termina ignorando, y ahí deja de proteger. El
# de arriba hace el mismo trabajo ejecutando, que además es la única forma
# honesta de saber que el panel se pinta.
