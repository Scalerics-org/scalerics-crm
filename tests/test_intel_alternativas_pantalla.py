"""El menú de alternativas pintado de verdad, en el celular y en la computadora.

Juan usa el celular y ya se le cortó una vez la pantalla de Inteligencia
financiera. El panel se rehízo entero (objetivo arriba, tarjetas de colores,
plan resultante, gastos esperados), así que se vuelve a medir lo mismo que
entonces: a 360, 375 y 414 px no se sale nada de costado, todo va en una
columna y lo que se toca entra en el dedo.

Y además lo nuevo: elegir una tarjeta tiene que actualizar EN VIVO el número de
arriba y hacer aparecer el plan. Eso se prueba con un clic de verdad, no
leyendo el HTML.

Se pinta con el CSS entero del dashboard y con lo que devuelve el servidor, sin
inventar datos en el navegador.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import dashboard
from database import crear_movimiento, crear_recurrente, init_db, insert_business, update_business
from services import inteligencia_fin as ifn
from services import intel_objetivo as obj

HTML = dashboard.DASHBOARD_HTML
SRC = (Path(__file__).resolve().parents[1] / "dashboard.py").read_text(encoding="utf-8")
AHORA = datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc)
MES = "2026-09"
# Lo peor que puede llegar: texto sin un solo espacio donde partir.
LARGO = ("Mantenimiento_mensual_de_servidores_y_herramientas_"
         "https://ejemplo.com/factura/2026/09/abcdef123456")
ANCHOS = [360, 375, 414]


def _entre(texto: str, desde: str, hasta: str) -> str:
    i = texto.index(desde)
    return texto[i:texto.index(hasta, i + len(desde))]


PANEL = _entre(SRC, "<!-- ======= INTELIGENCIA FINANCIERA PANEL ======= -->",
               "<!-- ======= FIN INTELIGENCIA FINANCIERA PANEL ======= -->")
JS = _entre(SRC, "// ========== Inteligencia financiera ==========",
            "// ========== FIN Inteligencia financiera ==========")


def _estado(tmp_path) -> dict:
    db = str(tmp_path / "pantalla.db")
    init_db(db)
    for p in ("2026-06", "2026-07", "2026-08"):
        crear_movimiento(db, tipo="egreso", fecha=f"{p}-10", periodo=p, concepto="Servidores",
                         categoria="servicios", monto=400, moneda="USD", monto_usd=400)
        bid = insert_business(db, {"name": f"Cliente {p}", "source": "meta",
                                   "scraped_at": f"{p}-01 09:00:00"})
        update_business(db, bid, crm_status="cerrado")
        crear_movimiento(db, tipo="ingreso", fecha=f"{p}-05", periodo=p, concepto="Web",
                         categoria="desarrollo_web", monto=4000, moneda="USD", monto_usd=4000,
                         client_id=bid)
    crear_recurrente(db, tipo="egreso", concepto="Hosting", categoria="infraestructura",
                     monto=170, moneda="USD", desde="2026-01")
    obj.guardar(db, MES, {"aportes_usd": 1100, "sueldo_usd": 2000})
    obj.crear_esperado(db, MES, {"concepto": LARGO, "monto_usd": 300, "categoria": "fijo"})
    ifn.corrida_diaria(db, AHORA)
    estado = json.loads(json.dumps(ifn.estado_pantalla(db, es_admin=True, ahora=AHORA),
                                   default=str))
    assert estado["recomendaciones"], "sin alternativas no hay nada que medir"
    # Y encima, todo lo largo que puede venir.
    estado["recomendaciones"][0]["titulo"] += " " + LARGO
    estado["recomendaciones"][0]["nota"] = LARGO
    estado["recomendaciones"][0]["calculo"] += "\n" + LARGO
    estado["recomendaciones"][0]["impacto_mensual"] = 1234567
    return estado


def _pagina(estado: dict, tema: str = "") -> str:
    estilos = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", HTML, re.S))
    panel = PANEL.replace('class="panel ifn-panel"', 'class="panel ifn-panel active"')
    return ("<!doctype html><html><head>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<style>{estilos}</style></head><body class='{tema}'><div class='main'>{panel}</div>"
            f"<script>{dashboard.ESC_JS}\nlet activePanel = 'inteligencia_fin';\n{JS}\n"
            f"ifnEstado = {json.dumps(estado, ensure_ascii=False)};\nifnPintar();</script>"
            "</body></html>")


_MEDIR = """() => {
  const p = document.getElementById('inteligencia_fin-panel');
  const W = window.innerWidth;
  const salidos = [];
  p.querySelectorAll('*').forEach(el => {
    const r = el.getBoundingClientRect();
    const desborda = el.scrollWidth > el.clientWidth + 1
      && getComputedStyle(el).overflowX !== 'visible';
    if (r.width && (r.right > W + 0.5 || r.left < -0.5 || desborda)) {
      salidos.push(el.tagName + '.' + el.className);
    }
  });
  const tarjetas = [...p.querySelectorAll('.ifn-alt')];
  return {
    W, pagina: document.documentElement.scrollWidth, salidos,
    alternativas: tarjetas.length,
    objetivoFila: getComputedStyle(p.querySelector('.ifn-obj-fila')).flexDirection,
    partes: getComputedStyle(p.querySelector('.ifn-obj-partes'))
      .gridTemplateColumns.split(' ').length,
    altDir: getComputedStyle(tarjetas[0]).flexDirection,
    toques: tarjetas.map(b => b.getBoundingClientRect().height),
    inputs: [...p.querySelectorAll('.ifn-obj-input')]
      .map(i => i.getBoundingClientRect().height),
    texto: p.innerText,
  };
}"""


@pytest.fixture(scope="module")
def navegador():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        nav, errores = None, []
        # El chromium de Playwright si esta bajado; si no, el Edge o el Chrome
        # que ya tiene la maquina. No se baja nada desde los tests.
        for canal in (None, "msedge", "chrome"):
            try:
                nav = p.chromium.launch(channel=canal) if canal else p.chromium.launch()
                break
            except Exception as e:
                errores.append(f"{canal or 'chromium'}: {str(e).splitlines()[0]}")
        if nav is None:
            pytest.skip("ningun navegador arranca: " + " | ".join(errores))
        yield nav
        nav.close()


def _abrir(navegador, estado, ancho, alto=900, tema=""):
    pagina = navegador.new_page(viewport={"width": ancho, "height": alto})
    pagina.set_content(_pagina(estado, tema), wait_until="domcontentloaded")
    return pagina


# ── el celular ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ancho", ANCHOS)
def test_en_el_celular_no_se_corta_nada(navegador, tmp_path, ancho):
    pagina = _abrir(navegador, _estado(tmp_path), ancho)
    try:
        m = pagina.evaluate(_MEDIR)
    finally:
        pagina.close()

    assert m["W"] == ancho
    assert m["pagina"] <= ancho, f"la pagina se va de costado: {m['pagina']}px"
    assert m["salidos"] == [], m["salidos"]
    # Una sola columna: el objetivo, sus tres partes y las tarjetas.
    assert m["objetivoFila"] == "column"
    assert m["partes"] == 1
    assert m["altDir"] == "column"
    # Lo que se toca entra en el dedo.
    assert m["toques"] and min(m["toques"]) >= 44, m["toques"]
    assert m["inputs"] and min(m["inputs"]) >= 44, m["inputs"]
    # En minúsculas: varios rótulos van en versalitas por CSS, así que el
    # innerText los devuelve en mayúsculas.
    texto = m["texto"].lower()
    for dato in ("objetivo del mes", "con lo seleccionado",
                 "alternativas para llegar al objetivo", "gastos del mes"):
        assert dato in texto, dato


def test_en_la_computadora_el_objetivo_va_en_una_fila(navegador, tmp_path):
    pagina = _abrir(navegador, _estado(tmp_path), 1280)
    try:
        m = pagina.evaluate(_MEDIR)
    finally:
        pagina.close()

    assert m["objetivoFila"] == "row"
    assert m["partes"] > 1
    assert m["altDir"] == "row"
    assert m["salidos"] == []


# ── elegir actualiza en vivo ─────────────────────────────────────────────────

def _num(pagina, selector):
    return pagina.inner_text(selector).strip()


@pytest.mark.parametrize("ancho", [375, 1280])
def test_elegir_una_alternativa_actualiza_el_total_y_arma_el_plan(navegador, tmp_path, ancho):
    estado = _estado(tmp_path)
    pagina = _abrir(navegador, estado, ancho)
    try:
        assert pagina.is_hidden("#ifn-plan"), "sin elegir nada no hay plan"
        antes = _num(pagina, ".ifn-obj-der .ifn-obj-num")
        assert antes == "USD 0"

        pagina.click(".ifn-alt")

        despues = _num(pagina, ".ifn-obj-der .ifn-obj-num")
        assert despues != antes, "el numero de arriba tiene que moverse al elegir"
        assert pagina.is_visible("#ifn-plan")
        # En minúsculas: el título del plan va en versalitas por CSS, así que
        # `inner_text` lo devuelve en mayúsculas.
        plan = pagina.inner_text("#ifn-plan").lower()
        assert "plan resultante" in plan and "1 alternativa seleccionada" in plan
        # La tarjeta queda marcada como elegida, y se nota.
        assert pagina.get_attribute(".ifn-alt", "aria-pressed") == "true"
        assert "ifn-elegida" in (pagina.get_attribute(".ifn-alt", "class") or "")

        # Y se puede desmarcar.
        pagina.click(".ifn-alt")
        assert pagina.is_hidden("#ifn-plan")
        assert _num(pagina, ".ifn-obj-der .ifn-obj-num") == antes
    finally:
        pagina.close()


def test_la_cuenta_viene_plegada_y_se_abre_a_pedido(navegador, tmp_path):
    """Juan: "mucho texto". La cuenta, los supuestos y los botones existen, pero
    no ocupan la pantalla hasta que se piden."""
    pagina = _abrir(navegador, _estado(tmp_path), 1280)
    try:
        # Está en el DOM, pero plegado: no se ve.
        assert pagina.query_selector(".ifn-rec") is not None
        assert not pagina.is_visible(".ifn-rec")
        assert pagina.evaluate("() => document.querySelectorAll('.ifn-detalle[open]').length") == 0

        pagina.click(".ifn-ver")

        assert pagina.is_visible(".ifn-rec")
        detalle = pagina.inner_text(".ifn-rec")
        assert "Lo voy a hacer" in detalle and "Descartar" in detalle
    finally:
        pagina.close()


def test_la_pantalla_arranca_sin_parrafos(navegador, tmp_path):
    """Se tiene que leer de un vistazo: números y títulos, no prosa."""
    pagina = _abrir(navegador, _estado(tmp_path), 1280)
    try:
        largos = pagina.evaluate("""() => {
          const p = document.getElementById('inteligencia_fin-panel');
          const malos = [];
          p.querySelectorAll('p, .ifn-alt-nota, .ifn-obj-pie').forEach(el => {
            const r = el.getBoundingClientRect();
            const texto = (el.innerText || '').trim();
            if (r.width && r.height && texto.length > 90) malos.push(texto.slice(0, 80));
          });
          return malos;
        }""")
    finally:
        pagina.close()

    assert largos == [], largos


# ── los colores, en los dos temas ────────────────────────────────────────────

@pytest.mark.parametrize("tema", ["", "light"])
def test_las_tarjetas_no_quedan_en_blanco_y_negro(navegador, tmp_path, tema):
    """Pedido de Juan: "que no quede blanco y negro". Cada palanca tiene que
    pintar distinto, y en los dos temas."""
    pagina = _abrir(navegador, _estado(tmp_path), 1280, tema=tema)
    try:
        colores = pagina.evaluate("""() => {
          const salida = {};
          document.querySelectorAll('.ifn-alt').forEach(el => {
            const c = getComputedStyle(el);
            const palanca = [...el.classList].find(x => x.indexOf('ifn-alt-') === 0) || '';
            salida[palanca] = c.borderLeftColor + ' | ' + c.backgroundColor;
          });
          return salida;
        }""")
    finally:
        pagina.close()

    assert len(colores) >= 2, colores
    # Ningun borde gris de fabrica y ningun par de palancas con el mismo color.
    assert len(set(colores.values())) == len(colores), colores
    for palanca, color in colores.items():
        assert "rgb(0, 0, 0)" not in color, (palanca, color)
