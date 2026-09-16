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
from database import (crear_escenario, crear_movimiento, crear_recurrente, init_db,
                      insert_business, update_business)
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


def _estado(tmp_path, largo: bool = True) -> dict:
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
    crear_escenario(db, nombre="1 programador y 900 de pauta",
                    datos=json.dumps({"equipo": {"cantidadProgramadores": 1},
                                      "ventas": {"pauta": 900}}),
                    created_by_id=1, created_by_name="Juan")
    ifn.corrida_diaria(db, AHORA)
    estado = json.loads(json.dumps(ifn.estado_pantalla(db, es_admin=True, ahora=AHORA),
                                   default=str))
    assert estado["recomendaciones"], "sin alternativas no hay nada que medir"
    if largo:
        # Texto sin un solo espacio donde partir, que es lo que cortaba
        # la pantalla en el celular.
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
            "<script>window.__pedidos = [];"
            "window.fetch = function (url, op) {"
            "  window.__pedidos.push([String(url), (op && op.method) || 'GET']);"
            "  return Promise.resolve({ok: true, status: 200,"
            "    json: function () { return Promise.resolve({ok: true, objetivo: ifnEstado.objetivo}); }});"
            "};</script>"
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
    for dato in ("objetivo del mes", "con lo seleccionado", "gastos del mes"):
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
    # Sin el texto monstruoso de los otros tests: aca se mide la pantalla
    # de verdad, con las notas que escribe el servidor.
    pagina = _abrir(navegador, _estado(tmp_path, largo=False), 1280)
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


# ── lo que Juan dijo que faltaba (16/9) ─────────────────────────────────────

def test_el_escenario_guardado_esta_arriba_de_todo(navegador, tmp_path):
    """Juan: "que saque los escenarios de los guardados en el simulador"."""
    pagina = _abrir(navegador, _estado(tmp_path), 1280)
    try:
        assert pagina.is_visible("#ifn-escenario")
        opciones = pagina.inner_text("#ifn-escenario")
        assert "1 programador y 900 de pauta" in opciones
        assert "datos reales de hoy" in opciones.lower()
        # Y esta arriba del objetivo, no escondido abajo.
        orden = pagina.evaluate("""() => {
          const p = document.getElementById('inteligencia_fin-panel');
          const hijos = [...p.querySelectorAll('section, details')].map(e => e.id || '');
          return hijos.filter(x => x);
        }""")
        assert orden.index("ifn-escenario") < orden.index("ifn-objetivo")
    finally:
        pagina.close()


def test_la_recomendacion_es_el_titular_y_se_toma_en_un_clic(navegador, tmp_path):
    """Juan: "Y que me de recomendaciones" / "tiene que estar clara la opcion
    de recortar". Un boton y queda elegido."""
    estado = _estado(tmp_path)
    pagina = _abrir(navegador, estado, 1280)
    try:
        assert pagina.is_visible("#ifn-recomendado")
        texto = pagina.inner_text("#ifn-recomendado")
        assert "lo que te conviene hacer" in texto.lower()
        assert pagina.is_hidden("#ifn-plan")

        pagina.click("#ifn-recomendado .btn-primary")

        assert pagina.is_visible("#ifn-plan"), "tomar la recomendacion arma el plan"
        assert "plan resultante" in pagina.inner_text("#ifn-plan").lower()
        # Las recomendadas no son necesariamente la primera tarjeta: se cuentan.
        elegidas = pagina.eval_on_selector_all(".ifn-alt.ifn-elegida", "els => els.length")
        assert elegidas == len(estado["recomendado"]["ids"]), elegidas
    finally:
        pagina.close()


def test_todos_los_costos_se_ven_con_su_interruptor(navegador, tmp_path):
    """"que los ponga todos como en el simulador", con on/off."""
    pagina = _abrir(navegador, _estado(tmp_path), 1280)
    try:
        pagina.evaluate("() => document.querySelectorAll('details').forEach(d => { d.open = true; })")
        grupos = pagina.eval_on_selector_all(
            "#ifn-gastos .ifn-grupo-cab span:first-child", "els => els.map(e => e.innerText)")
        interruptores = pagina.eval_on_selector_all(
            "#ifn-gastos .ifn-sw", "els => els.length")

        en_minuscula = [g.lower() for g in grupos]
        for grupo in ("sueldos", "honorarios", "fijos de estructura"):
            assert grupo in en_minuscula, (grupo, grupos)
        assert interruptores >= 3, "cada costo tiene su interruptor"
    finally:
        pagina.close()


def test_apagar_un_costo_avisa_al_servidor(navegador, tmp_path):
    pagina = _abrir(navegador, _estado(tmp_path), 1280)
    try:
        pagina.evaluate("() => document.querySelectorAll('details').forEach(d => { d.open = true; })")
        pagina.click("#ifn-gastos .ifn-sw >> nth=0")
        pedidos = pagina.evaluate("() => window.__pedidos")

        assert any("/api/inteligencia-fin/costos/" in u and m == "PUT" for u, m in pedidos), pedidos
    finally:
        pagina.close()


def test_cada_alternativa_dice_que_pasa(navegador, tmp_path):
    """Juan: "si clickeo una o varias de las alternativas me diga que pasa
    sino no sirve para nada me entendes"."""
    estado = _estado(tmp_path, largo=False)
    conse = [r for r in estado["recomendaciones"] if r.get("consecuencia")]
    assert conse, "el servidor tiene que mandar consecuencias"
    pagina = _abrir(navegador, estado, 1280)
    try:
        visibles = pagina.eval_on_selector_all(
            ".ifn-alt-que", "els => els.map(e => e.innerText.trim()).filter(Boolean)")
        assert visibles, "la consecuencia se tiene que ver en la tarjeta"
        assert any(c["consecuencia"][:24] in " ".join(visibles) for c in conse)
    finally:
        pagina.close()
