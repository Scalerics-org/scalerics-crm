# Meta Ads: el panel — Plan de implementación (Fase 2 de 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un panel `Marketing` en el CRM que muestre lo que ya devuelve
`/api/marketing/dossier` con el nivel de un dashboard de ads comercial, y que
además muestre lo que ninguno de esos puede: qué pasó con cada lead después del
clic.

**Architecture:** Un módulo SVG propio en `static/charts.js`, sin dependencias.
Funciones puras que reciben datos y devuelven una cadena SVG, más un renderer
que las inyecta. El panel vive en `dashboard.py` como los demás y consume los
endpoints de la Fase 1. Ningún gráfico calcula nada: todo viene del dossier.

**Tech Stack:** JavaScript sin build ni librerías, SVG inline, CSS variables
para los dos temas. Verificación con `node --check` desde pytest, aserciones
sobre las funciones puras corriendo en node, y revisión visual en un navegador
real.

**Spec:** `docs/superpowers/specs/2026-09-10-inteligencia-comercial-meta-design.md` (§11)

**Fase 1** (datos y dossier) está hecha y mergeando en el PR #22. **Fase 3** (el
motor de IA) tiene su propio plan y espera a que se pueda volver a gastar en la
API.

## Global Constraints

- **Ningún gráfico de doble eje.** Superponer dos escalas deja elegir dónde se
  cruzan las líneas, o sea que se puede fabricar cualquier correlación moviendo
  un eje. Dos medidas de escalas distintas van como dos gráficos apilados que
  comparten el eje de tiempo.
- **Grano semanal** en toda serie temporal. 238 leads en 178 días son 1,3 por
  día; en grano diario el gráfico son picos y ceros.
- **La incertidumbre se dibuja.** Toda barra de tasa con `muestra_chica` lleva su
  intervalo visible. Con 51 leads en una campaña, cinco puntos de diferencia
  contra otra no significan nada, y el gráfico tiene que decirlo.
- **Un valor `null` no se dibuja como cero.** `costo()` devuelve `null` cuando no
  hay de qué dividir; en el gráfico eso es un hueco con la leyenda «sin datos»,
  nunca una barra de altura cero.
- **Color por entidad, nunca por ranking.** Una campaña conserva su color aunque
  cambie de posición al filtrar.
- **Paleta fija y ya validada** (ver abajo). No se generan hues nuevos: una
  séptima campaña cae en el gris de «otros».
- **Los dos temas, ambos explícitos.** El CRM tiene `body.light`. Nada de
  invertir colores automáticamente.
- **Leyenda siempre que haya dos o más series**, y etiquetas directas cuando son
  cuatro o menos. La identidad nunca depende solo del color.
- **Texto con tokens de texto, nunca con el color de la serie.** El valor y la
  etiqueta van en gris de texto; el color lo lleva la marca al lado.
- **Nada de emojis.** Iconos SVG inline, como el resto del CRM.
- **Números en formato es-UY**: separador de miles con punto, decimal con coma.

### La paleta, validada con `scripts/validate_palette.js`

No se cambia sin volver a correr el validador contra las dos superficies.

| Rol | Claro (superficie `#f8fafc`) | Oscuro (superficie `#111827`) |
|---|---|---|
| Campaña 1 | `#0088CC` | `#0088CC` |
| Campaña 2 | `#A855F7` | `#A855F7` |
| Campaña 3 | `#0D9488` | `#0D9488` |
| Campaña 4 | `#EA580C` | `#EA580C` |
| Campaña 5 | `#4F46E5` | `#6366F1` |
| (sin campaña) y «otros» | `#94A3B8` | `#64748B` |

Dos cosas que salieron de la validación y no son negociables:

1. **El índigo cambia de paso entre temas.** `#4F46E5` sobre `#111827` da 2,82:1
   de contraste, por debajo del piso de 3:1. En oscuro va `#6366F1`.
2. **Queda un aviso de daltonismo entre el azul y el violeta** (ΔE 6,7 deutan,
   en la banda 6-8). Eso es legal **solo** con codificación secundaria: por eso
   las etiquetas directas y la leyenda no son opcionales en este panel.

El gris de «(sin campaña)» es deliberado: ese bucket no es una categoría más,
es un desconocido, y además concentra 8 de los 9 cierres históricos.

---

### Task 1: Las primitivas de `static/charts.js`

**Files:**
- Create: `static/charts.js`
- Create: `tests/test_charts_js.py`

**Interfaces:**
- Produces, todo bajo el global `SC` (por Scalerics, para no chocar con nada):
  - `SC.PALETA` — `{claro: [...], oscuro: [...], neutro: {...}}`
  - `SC.colorDeCampana(nombre, indice, tema) -> string`
  - `SC.escalaLineal(dominio, rango) -> f(valor) -> number`
  - `SC.ticks(min, max, cantidad) -> number[]` — cortes redondos
  - `SC.fmt(valor, formato) -> string` — `numero` | `moneda` | `porcentaje`, es-UY
  - `SC.fmtDelta(valor, formato) -> {texto, signo}` 
  - `SC.esc(texto) -> string` — escape de XML para meter texto en SVG

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_charts_js.py`:

```python
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
    """Carga charts.js en node y corre `js`, que debe imprimir JSON."""
    archivo = tmp_path / "correr.mjs"
    archivo.write_text(
        f"const SC = {{}};\n"
        f"globalThis.SC = SC;\n"
        f"{CHARTS.read_text(encoding='utf-8')}\n"
        f"{js}\n", encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@sin_node
def test_el_modulo_compila():
    r = subprocess.run(["node", "--check", str(CHARTS)],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"charts.js no compila:\\n{r.stderr}"


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
        "console.log(JSON.stringify(["
        "  SC.colorDeCampana('(sin campaña)', 0, 'oscuro'),"
        "  SC.colorDeCampana('Leads - UY - 2026', 0, 'oscuro')]));", tmp_path)
    assert r[0] == "#64748B"
    assert r[1] != r[0]


@sin_node
def test_el_color_sigue_a_la_campana_no_a_la_posicion(tmp_path):
    """Filtrar no puede repintar a las que quedan."""
    r = _correr(
        "console.log(JSON.stringify(["
        "  SC.colorDeCampana('Leads - UY - 2026', 2, 'claro'),"
        "  SC.colorDeCampana('Leads - UY - 2026', 0, 'claro')]));", tmp_path)
    assert r[0] == r[1]


@sin_node
def test_una_septima_campana_cae_en_el_gris(tmp_path):
    """No se generan hues nuevos."""
    r = _correr(
        "const nombres = ['a','b','c','d','e','f','g'];"
        "console.log(JSON.stringify(nombres.map((n,i) => "
        "  SC.colorDeCampana(n, i, 'claro'))));", tmp_path)
    assert r[5] == "#94A3B8"
    assert r[6] == "#94A3B8"


@sin_node
def test_los_numeros_van_en_formato_uruguayo(tmp_path):
    r = _correr(
        "console.log(JSON.stringify(["
        "  SC.fmt(1234.5, 'moneda'),"
        "  SC.fmt(0.1084, 'porcentaje'),"
        "  SC.fmt(4500, 'numero'),"
        "  SC.fmt(null, 'moneda')]));", tmp_path)
    assert r[0] == "1.234,50"
    assert r[1] == "10,8%"
    assert r[2] == "4.500"
    assert r[3] == "sin datos"


@sin_node
def test_un_null_dice_sin_datos_y_no_cero(tmp_path):
    """costo() devuelve null cuando no hay de que dividir. Dibujarlo como cero
    diria que el costo por demo fue cero, que es una afirmacion distinta."""
    r = _correr("console.log(JSON.stringify(SC.fmt(null, 'numero')));", tmp_path)
    assert r == "sin datos"


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
    assert all(isinstance(x, (int, float)) for x in r)


@sin_node
def test_el_texto_se_escapa_antes_de_entrar_al_svg(tmp_path):
    r = _correr(
        "console.log(JSON.stringify(SC.esc('Leads & <UY> \\\"2026\\\"')));",
        tmp_path)
    assert "<" not in r and "&l" in r


@sin_node
def test_el_delta_trae_su_signo(tmp_path):
    r = _correr(
        "console.log(JSON.stringify(["
        "  SC.fmtDelta(12.5, 'moneda'), SC.fmtDelta(-3, 'moneda'),"
        "  SC.fmtDelta(null, 'moneda')]));", tmp_path)
    assert r[0]["signo"] == "sube"
    assert r[1]["signo"] == "baja"
    assert r[2]["signo"] == "sin_comparacion"
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `python -m pytest tests/test_charts_js.py -q`
Expected: FAIL — `charts.js` no existe.

- [ ] **Step 3: Escribir `static/charts.js`**

El archivo arranca con este encabezado y las primitivas. Todo cuelga de `SC`
para no chocar con los globals que ya tiene `dashboard.py`.

```javascript
/* Graficos del panel de Marketing.
 *
 * SVG a mano, sin librerias. Se eligio asi sobre Chart.js o ECharts por tres
 * motivos: el CRM no tiene ninguna dependencia de frontend y no se le queria
 * sumar una; los dos temas y la paleta de Scalerics hay que respetarlos
 * exactamente y a las librerias hay que pelearles los defaults; y el embudo de
 * nueve pasos, que es el grafico que importa, igual habria que dibujarlo a mano.
 *
 * Las funciones de dibujo son puras: reciben datos, devuelven un string de SVG.
 * Ninguna calcula nada — todo numero sale del dossier, que ya trae numerador,
 * denominador, n e intervalo.
 */

(function (SC) {
  'use strict';

  // Validada con scripts/validate_palette.js de la skill de dataviz contra las
  // dos superficies del CRM (#f8fafc y #111827). No cambiar un color sin volver
  // a correrlo: el indigo, por ejemplo, tiene un paso distinto por tema porque
  // #4F46E5 sobre #111827 da 2,82:1, abajo del piso de 3:1.
  SC.PALETA = {
    claro:  ['#0088CC', '#A855F7', '#0D9488', '#EA580C', '#4F46E5'],
    oscuro: ['#0088CC', '#A855F7', '#0D9488', '#EA580C', '#6366F1'],
    neutro: { claro: '#94A3B8', oscuro: '#64748B' },
    // Texto y ejes: recesivos a proposito. El color lo lleva la marca.
    tinta:  { claro: '#0f172a', oscuro: '#e2e8f0' },
    mudo:   { claro: '#64748b', oscuro: '#64748b' },
    grilla: { claro: '#e2e8f0', oscuro: '#1e293b' },
    fondo:  { claro: '#ffffff', oscuro: '#111827' }
  };

  SC.SIN_CAMPANA = '(sin campaña)';

  // El color sigue a la campana, no a su posicion: filtrar no puede repintar a
  // las que quedan. El indice es solo el desempate cuando no hay historia.
  var _asignados = {};

  SC.colorDeCampana = function (nombre, indice, tema) {
    var hues = SC.PALETA[tema] || SC.PALETA.oscuro;
    if (nombre === SC.SIN_CAMPANA || nombre === 'otros') {
      return SC.PALETA.neutro[tema] || SC.PALETA.neutro.oscuro;
    }
    if (!(nombre in _asignados)) {
      var usados = Object.keys(_asignados).length;
      // Una septima campana no genera un hue nuevo: cae en el gris.
      _asignados[nombre] = usados < hues.length ? usados : -1;
    }
    var slot = _asignados[nombre];
    return slot < 0 ? (SC.PALETA.neutro[tema] || SC.PALETA.neutro.oscuro)
                    : hues[slot];
  };

  SC._resetColores = function () { _asignados = {}; };   // para los tests

  SC.escalaLineal = function (dominio, rango) {
    var d0 = dominio[0], d1 = dominio[1], r0 = rango[0], r1 = rango[1];
    var ancho = d1 - d0;
    return function (v) {
      if (!ancho) return r0;          // dominio degenerado: no dividir por cero
      return r0 + (v - d0) / ancho * (r1 - r0);
    };
  };

  SC.ticks = function (min, max, cantidad) {
    if (max <= min) return [min];
    var crudo = (max - min) / (cantidad || 5);
    var mag = Math.pow(10, Math.floor(Math.log10(crudo)));
    var norm = crudo / mag;
    var paso = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
    var salida = [], v = Math.floor(min / paso) * paso;
    while (v <= max + paso * 0.001) { salida.push(Math.round(v * 1e6) / 1e6); v += paso; }
    return salida;
  };

  // es-UY: miles con punto, decimal con coma.
  SC.fmt = function (valor, formato) {
    if (valor === null || valor === undefined) return 'sin datos';
    if (formato === 'porcentaje') {
      return (valor * 100).toFixed(1).replace('.', ',') + '%';
    }
    if (formato === 'moneda') {
      return valor.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, '.')
                  .replace(/\.(\d\d)$/, ',$1');
    }
    return String(Math.round(valor)).replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  };

  SC.fmtDelta = function (valor, formato) {
    if (valor === null || valor === undefined) {
      return { texto: 'sin período anterior', signo: 'sin_comparacion' };
    }
    var signo = valor > 0 ? 'sube' : valor < 0 ? 'baja' : 'igual';
    var prefijo = valor > 0 ? '+' : valor < 0 ? '−' : '';
    return { texto: prefijo + SC.fmt(Math.abs(valor), formato), signo: signo };
  };

  SC.esc = function (t) {
    return String(t === null || t === undefined ? '' : t)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  };

}(typeof SC !== 'undefined' ? SC : (window.SC = window.SC || {})));
```

**Ojo con el formateador de moneda:** el reemplazo de miles corre sobre el string
con dos decimales, así que el punto decimal tiene que convertirse a coma
**después**. Si se hace al revés, el separador de miles pisa la coma.

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_charts_js.py -q`
Expected: PASS, 12 tests

- [ ] **Step 5: Servir el archivo**

`dashboard.py` ya sirve `/static/` (el favicon sale de ahí). Verificar que el
archivo se sirve:

Run: `python -c "from dashboard import create_app; c = create_app(':memory:').test_client(); print(c.get('/static/charts.js').status_code)"`
Expected: `200`

- [ ] **Step 6: Commit**

```bash
git add static/charts.js tests/test_charts_js.py
git commit -m "feat(marketing): primitivas SVG del panel

Paleta validada con el script de la skill de dataviz contra las dos
superficies del CRM. El indigo lleva un paso distinto por tema porque
#4F46E5 sobre #111827 da 2,82:1, abajo del piso de 3:1.

El color sigue a la campana y no a su posicion: filtrar no repinta a las
que quedan. Una septima campana no genera un hue nuevo, cae en el gris,
igual que (sin campana) —que no es una categoria mas, es un desconocido.

Un null se dibuja como 'sin datos' y nunca como cero: costo() devuelve
null cuando no hay de que dividir, y una barra en cero diria que el costo
por demo fue cero, que es otra afirmacion."
```

---

### Task 2: Los 8 KPIs con delta

Stat tiles, sin gráfico. Es la fila de arriba del panel.

**Files:**
- Modify: `static/charts.js`
- Modify: `tests/test_charts_js.py`

**Interfaces:**
- Produces: `SC.tiles(metricas, tema) -> string` (HTML, no SVG)

Los ocho: gasto, CPM, CPC, CTR, leads, CPL, **costo por demo** y **costo por
presupuesto**. Los dos últimos reemplazan a los tiles de «valor del lead» de los
dashboards comerciales: ese número necesita plata asignada por lead.

- [ ] **Step 1: Tests**

Agregar a `tests/test_charts_js.py`:

```python
@sin_node
def test_un_tile_sin_periodo_anterior_no_finge_un_delta(tmp_path):
    """La primera corrida no tiene contra que comparar. Eso no es 0%."""
    r = _correr(
        "const html = SC.tiles([{id:'x', etiqueta:'Gasto', valor:100,"
        " formato:'moneda', delta_periodo_anterior:null}], 'oscuro');"
        "console.log(JSON.stringify(html));", tmp_path)
    assert "sin período anterior" in r
    assert "0%" not in r


@sin_node
def test_un_tile_sin_valor_dice_sin_datos(tmp_path):
    r = _correr(
        "const html = SC.tiles([{id:'x', etiqueta:'CPL', valor:null,"
        " formato:'moneda', delta_periodo_anterior:null}], 'oscuro');"
        "console.log(JSON.stringify(html));", tmp_path)
    assert "sin datos" in r


@sin_node
def test_el_tile_escapa_la_etiqueta(tmp_path):
    r = _correr(
        "const html = SC.tiles([{id:'x', etiqueta:'<script>', valor:1,"
        " formato:'numero', delta_periodo_anterior:null}], 'oscuro');"
        "console.log(JSON.stringify(html));", tmp_path)
    assert "<script>" not in r
```

- [ ] **Step 2: Correr y ver fallar**

Run: `python -m pytest tests/test_charts_js.py -q`
Expected: FAIL — `SC.tiles is not a function`

- [ ] **Step 3: Implementar `SC.tiles`**

Reglas de la marca, tomadas de la guía de visualización:

- El número grande va en tinta, no en el color de la serie.
- El delta lleva icono SVG de flecha además del color: el signo no puede
  depender solo del color.
- «sube» no es siempre bueno: en `cpl`, `costo_demo` y `costo_presupuesto` subir
  es malo. El tile recibe el sentido en `mejor: 'alto' | 'bajo'` y colorea con
  los tokens de estado, no con la paleta categórica.

- [ ] **Step 4: Correr los tests**

Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 3: El embudo de nueve pasos

El gráfico que justifica el módulo entero. Las tres primeras etapas las tiene
cualquier reporte de ads; las seis siguientes solo las tiene el CRM.

**Files:**
- Modify: `static/charts.js`
- Modify: `tests/test_charts_js.py`

**Interfaces:**
- Produces: `SC.embudo(etapas, tema) -> string` — cada etapa
  `{clave, etiqueta, valor, fuente}`; devuelve SVG

- [ ] **Step 1: Tests**

```python
@sin_node
def test_el_embudo_dibuja_una_fila_por_etapa(tmp_path):
    r = _correr(
        "const svg = SC.embudo([{clave:'impresiones', etiqueta:'Impresiones',"
        " valor:44900, fuente:'meta_insights'},{clave:'clics', etiqueta:'Clics',"
        " valor:2000, fuente:'meta_insights'},{clave:'leads', etiqueta:'Leads',"
        " valor:338, fuente:'crm'}], 'oscuro');"
        "console.log(JSON.stringify(svg));", tmp_path)
    assert r.count("<rect") >= 3
    assert "44.900" in r and "2.000" in r and "338" in r


@sin_node
def test_el_embudo_muestra_la_caida_entre_etapas(tmp_path):
    r = _correr(
        "const svg = SC.embudo([{clave:'a', etiqueta:'A', valor:100,"
        " fuente:'crm'},{clave:'b', etiqueta:'B', valor:25, fuente:'crm'}],"
        " 'oscuro');console.log(JSON.stringify(svg));", tmp_path)
    assert "25,0%" in r


@sin_node
def test_el_embudo_distingue_lo_que_viene_de_meta_de_lo_que_viene_del_crm(tmp_path):
    """Las tres primeras etapas las tiene cualquier reporte de ads. Las seis
    siguientes son lo que solo tenemos nosotros, y eso se ve."""
    r = _correr(
        "const svg = SC.embudo([{clave:'a', etiqueta:'A', valor:10,"
        " fuente:'meta_insights'},{clave:'b', etiqueta:'B', valor:5,"
        " fuente:'crm'}], 'oscuro');console.log(JSON.stringify(svg));", tmp_path)
    assert "data-fuente=\"meta_insights\"" in r
    assert "data-fuente=\"crm\"" in r


@sin_node
def test_una_etapa_en_cero_no_rompe_el_embudo(tmp_path):
    r = _correr(
        "const svg = SC.embudo([{clave:'a', etiqueta:'A', valor:0,"
        " fuente:'crm'},{clave:'b', etiqueta:'B', valor:0, fuente:'crm'}],"
        " 'oscuro');console.log(JSON.stringify(svg));", tmp_path)
    assert "NaN" not in r and "Infinity" not in r
```

- [ ] **Step 2: Correr y ver fallar**
- [ ] **Step 3: Implementar `SC.embudo`**

Especificación de la marca:

- Barras horizontales apiladas verticalmente, ancho proporcional al valor sobre
  el máximo, esquinas de 4px del lado del dato.
- **2px de separación** entre barras (el spacer que pide la guía).
- Entre etapa y etapa, el porcentaje de caída en gris mudo, a la derecha.
- Las etapas de `meta_insights` y las de `crm` se separan con una línea sutil y
  un rótulo: hasta acá llega cualquier reporte de ads.
- Etiqueta y valor en tinta, siempre visibles: nunca dentro de la barra, donde
  una barra corta los taparía.

- [ ] **Step 4: Correr los tests**
- [ ] **Step 5: Commit**

---

### Task 4: Series temporales apiladas

**Files:**
- Modify: `static/charts.js`
- Modify: `tests/test_charts_js.py`

**Interfaces:**
- Produces: `SC.serie(puntos, opciones, tema) -> string` — un solo eje Y
- Produces: `SC.parApilado(serieA, serieB, tema) -> string` — dos `SC.serie`
  con el mismo eje X, apilados

- [ ] **Step 1: Tests**

```python
@sin_node
def test_la_serie_dibuja_un_solo_eje_y(tmp_path):
    """Nunca doble eje: con dos escalas se puede fabricar cualquier
    correlacion moviendo un eje."""
    r = _correr(
        "const svg = SC.serie([{x:'2026-W10', y:100},{x:'2026-W11', y:150}],"
        " {etiqueta:'Gasto', formato:'moneda'}, 'oscuro');"
        "console.log(JSON.stringify(svg));", tmp_path)
    assert r.count('class="eje-y"') == 1


@sin_node
def test_un_hueco_en_la_serie_corta_la_linea(tmp_path):
    """Una semana sin CPL no se une con una recta a la siguiente: eso
    inventaria datos que no hay."""
    r = _correr(
        "const svg = SC.serie([{x:'a', y:1},{x:'b', y:null},{x:'c', y:3}],"
        " {etiqueta:'CPL', formato:'moneda'}, 'oscuro');"
        "console.log(JSON.stringify(svg));", tmp_path)
    assert r.count("<path") >= 2


@sin_node
def test_el_par_apilado_comparte_el_eje_de_tiempo(tmp_path):
    r = _correr(
        "const svg = SC.parApilado("
        "  {puntos:[{x:'a',y:1},{x:'b',y:2}], etiqueta:'Gasto', formato:'moneda'},"
        "  {puntos:[{x:'a',y:3},{x:'b',y:4}], etiqueta:'CPL', formato:'moneda'},"
        "  'oscuro');console.log(JSON.stringify(svg));", tmp_path)
    assert r.count('class="panel-serie"') == 2
    # Un solo eje de tiempo compartido, no uno por panel.
    assert r.count('class="eje-x"') == 1
```

- [ ] **Step 2: Correr y ver fallar**
- [ ] **Step 3: Implementar**

Especificación:

- Línea de 2px, marcadores de 8px, grilla recesiva.
- Hover: crosshair vertical + tooltip con el valor de la semana. El área de
  captura es toda la franja vertical, no el marcador.
- Un `null` corta el path: dos `<path>` en vez de uno.
- El eje X muestra la semana ISO cada N puntos para que no se amontonen.

- [ ] **Step 4: Correr los tests**
- [ ] **Step 5: Commit**

---

### Task 5: Barras horizontales con intervalo de confianza

El gráfico que impide decidir sobre ruido.

**Files:**
- Modify: `static/charts.js`
- Modify: `tests/test_charts_js.py`

**Interfaces:**
- Produces: `SC.barrasConIC(filas, opciones, tema) -> string` — cada fila
  `{etiqueta, metrica}` donde `metrica` es una del dossier

- [ ] **Step 1: Tests**

```python
@sin_node
def test_una_muestra_chica_dibuja_su_intervalo(tmp_path):
    """Con 51 leads, cinco puntos de diferencia contra otra campana no
    significan nada, y el grafico tiene que decirlo."""
    r = _correr(
        "const svg = SC.barrasConIC([{etiqueta:'ARG', metrica:{valor:0.2,"
        " n:10, ic95:[0.05,0.5], muestra_chica:true, formato:'porcentaje'}}],"
        " {}, 'claro');console.log(JSON.stringify(svg));", tmp_path)
    assert 'class="ic"' in r
    assert "n=10" in r


@sin_node
def test_una_muestra_grande_no_necesita_la_advertencia(tmp_path):
    r = _correr(
        "const svg = SC.barrasConIC([{etiqueta:'UY', metrica:{valor:0.2,"
        " n:400, ic95:[0.18,0.22], muestra_chica:false,"
        " formato:'porcentaje'}}], {}, 'claro');"
        "console.log(JSON.stringify(svg));", tmp_path)
    assert "muestra chica" not in r


@sin_node
def test_una_metrica_sin_valor_no_dibuja_barra(tmp_path):
    r = _correr(
        "const svg = SC.barrasConIC([{etiqueta:'X', metrica:{valor:null,"
        " n:0, ic95:[0,1], muestra_chica:true, formato:'moneda'}}], {},"
        " 'claro');console.log(JSON.stringify(svg));", tmp_path)
    assert "sin datos" in r
```

- [ ] **Step 2: Correr y ver fallar**
- [ ] **Step 3: Implementar**

Especificación:

- Barra de 4px de radio del lado del dato, anclada al cero.
- El intervalo va como una línea fina con topes, superpuesta a la barra, con un
  anillo de 2px del color de la superficie para que se lea sobre la barra.
- `n=<numero>` como etiqueta directa al final de cada fila, siempre.
- `muestra_chica` agrega el rótulo «muestra chica» en gris mudo.
- Orden: descendente por valor, pero **el color sale de la campaña**, no de la
  posición.

- [ ] **Step 4: Correr los tests**
- [ ] **Step 5: Commit**

---

### Task 6: El panel en `dashboard.py`

**Files:**
- Modify: `dashboard.py` — nav, panel, CSS, carga
- Create: `tests/test_marketing_panel.py`

- [ ] **Step 1: Tests**

```python
"""El panel de Marketing existe, esta en el nav y su JS compila."""

import re
import shutil
import subprocess

import pytest

import dashboard


def test_el_panel_esta_en_la_navegacion():
    assert 'showPanel(\\'marketing\\')' in dashboard.DASHBOARD_HTML
    assert 'id="nav-marketing"' in dashboard.DASHBOARD_HTML


def test_el_panel_existe():
    assert 'id="marketing-panel"' in dashboard.DASHBOARD_HTML


def test_el_panel_carga_charts_js():
    assert '/static/charts.js' in dashboard.DASHBOARD_HTML


def test_no_hay_emojis_en_el_panel():
    """El CRM usa iconos SVG, no emojis."""
    bloque = dashboard.DASHBOARD_HTML.split('id="marketing-panel"')[1][:8000]
    assert not re.search(r'[\\U0001F300-\\U0001FAFF]', bloque)


@pytest.mark.skipif(shutil.which("node") is None, reason="node no esta instalado")
def test_el_javascript_del_dashboard_sigue_compilando(tmp_path):
    bloques = re.findall(r"<script>(.*?)</script>", dashboard.DASHBOARD_HTML, re.S)
    archivo = tmp_path / "d.js"
    archivo.write_text("\\n".join(bloques), encoding="utf-8")
    r = subprocess.run(["node", "--check", str(archivo)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
```

- [ ] **Step 2: Correr y ver fallar**
- [ ] **Step 3: Implementar el panel**

Estructura, de arriba hacia abajo:

1. **Fila de filtros**, una sola: rango de fechas y campaña. Los filtros van
   arriba de los gráficos, en una fila.
2. **Los 8 KPIs.**
3. **El embudo de 9 pasos**, ancho completo. Es el gráfico principal.
4. **Gasto y CPL por semana**, par apilado.
5. **Impresiones/CPM y Clics/CPC**, pares apilados.
6. **CPL, tasa de demo y costo por demo por campaña**, barras con IC.
7. **Calidad por segmento declarado** — qué busca, presupuesto, objetivo, ciudad.
8. **Secuencia de recordatorios.**
9. **Vista de tabla**, colapsada: el dossier crudo. La guía la exige como salida
   accesible, y además es la que permite auditar cualquier número del panel.

Reglas del panel:

- **Un aviso fijo arriba del bloque por campaña** cuando el bucket
  `(sin campaña)` concentra la mayoría de los cierres: sin eso, la tabla se lee
  como que una sola campaña cerró algo, y es falso.
- El estado de carga y el de error son explícitos, no una pantalla en blanco.
- Sin datos de gasto (que es el estado de hoy, sin credenciales de Insights) los
  bloques de costo muestran «sin datos de gasto — falta configurar Meta Ads», no
  ceros.

- [ ] **Step 4: Correr los tests**

Run: `python -m pytest tests/test_marketing_panel.py tests/ -k js -q`
Expected: PASS

- [ ] **Step 5: `check_js.py`**

Run: `python scripts/check_js.py`
Expected: sin errores

- [ ] **Step 6: Commit**

---

### Task 7: Verlo, auditarlo y arreglar lo que se vea mal

Un panel que compila no es un panel que se ve bien. La guía de visualización lo
dice explícitamente: el validador chequea color, no geometría — hay que abrirlo
y mirarlo.

**Files:**
- Modify: lo que haga falta según lo que se vea

- [ ] **Step 1: Levantar el CRM con datos reales**

Sobre una **copia** del backup, nunca contra producción:

```bash
SCRATCH="C:/Users/juant/AppData/Local/Temp"
cp backups/leads_pre_preclientes_8sep.db "$SCRATCH/panel.db"
python -c "
from database import init_db
from services.meta_campanas import backfill_campanas
db = r'$SCRATCH/panel.db'
init_db(db); backfill_campanas(db)
"
DB_PATH="$SCRATCH/panel.db" python server.py
```

- [ ] **Step 2: Abrirlo en el navegador y sacar capturas de los dos temas**

Con las herramientas de Chrome: entrar, abrir el panel Marketing, capturar en
oscuro y en claro.

- [ ] **Step 3: Mirar la lista de lo que suele salir mal**

Contra `references/anti-patterns.md` de la skill de dataviz, y a ojo:
etiquetas encimadas, barras que se salen del área, ejes con demasiados
decimales, tooltips cortados por el borde, texto ilegible en un tema.

- [ ] **Step 4: Auditar la interfaz**

Correr la skill `web-design-guidelines` sobre el panel. Encontró bugs reales en
demos anteriores, así que no es un trámite.

- [ ] **Step 5: Arreglar lo que aparezca y volver al Step 2**

Hasta que las dos capturas estén bien.

- [ ] **Step 6: Commit final y anotar en `COORDINACION.md`**

---

## Cuando termine esta fase

1. **Anotar en `COORDINACION.md`**: panel nuevo en `dashboard.py`, archivo nuevo
   en `static/`.
2. **Deployar sigue bloqueado por lo mismo que la Fase 1**: faltan
   `META_ADS_TOKEN` y `META_AD_ACCOUNT_ID`, y verificar el `action_type`. El
   panel funciona sin eso, pero con los bloques de costo vacíos.
3. **Fase 3**: el motor de IA, cuando se pueda volver a gastar en la API.
