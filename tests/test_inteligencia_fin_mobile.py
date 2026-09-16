"""Inteligencia financiera en el celular.

Juan (15/9): "desde el celular inteligencia financiera queda cortado". Con
datos cortos entraba justo; lo que la cortaba era texto sin espacios (un
nombre de cliente largo, un concepto, una linea de la cuenta): no tenia donde
partir y empujaba la pagina de costado, asi que a la derecha quedaba todo
cortado. Se arreglo solo con CSS, adentro de un @media de 768px (la misma
frontera en la que aparece el menu de abajo): arriba de eso la pantalla es la
de siempre.

Los tests de Playwright pintan el panel de verdad, con el CSS entero del
dashboard y lo que devuelve el servidor, a ancho de celular y de escritorio.

16/9: el panel se rehizo (arriba el objetivo del mes y el menu de alternativas;
el diagnostico, el contraste y el seguimiento pasaron a un `<details>` plegado,
igual que la cuenta de cada alternativa). Lo que este archivo cuida no cambio
-que a 360, 375 y 414 px no se salga nada de costado, que todo vaya en una
columna y que lo que se toca entre en el dedo-, asi que se sigue midiendo lo
mismo: se abren los `<details>` antes de medir, porque lo plegado no se pinta y
un elemento que no se pinta pasaria estas pruebas sin querer.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import dashboard
from database import crear_movimiento, crear_por_cobrar, init_db, insert_business, update_business
from services import inteligencia_fin as ifn

HTML = dashboard.DASHBOARD_HTML
SRC = (Path(__file__).resolve().parents[1] / "dashboard.py").read_text(encoding="utf-8")
AHORA = datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc)
LARGO = "Mantenimiento_mensual_de_servidores_y_herramientas_https://ejemplo.com/factura/2026/09/abcdef123456"


def _entre(texto: str, desde: str, hasta: str) -> str:
    i = texto.index(desde)
    return texto[i:texto.index(hasta, i + len(desde))]


CSS = _entre(SRC, "/* ── Inteligencia financiera", "/* ── Plantillas")
PANEL = _entre(SRC, "<!-- ======= INTELIGENCIA FINANCIERA PANEL ======= -->",
               "<!-- ======= FIN INTELIGENCIA FINANCIERA PANEL ======= -->")
JS = _entre(SRC, "// ========== Inteligencia financiera ==========",
            "// ========== FIN Inteligencia financiera ==========")
MOVIL = "@media (max-width:768px){"


def _bloque_movil() -> str:
    i = CSS.index(MOVIL)
    j = CSS.index("\n}", i)
    return CSS[i:j + 2]


# ── el CSS: todo lo del celular adentro del @media ──────────────────────────

def test_un_solo_media_y_es_el_del_celular():
    assert re.findall(r"@media[^{]*\{", CSS) == [MOVIL]
    assert "max-width:640px" not in CSS


def test_la_clase_del_panel_solo_se_usa_en_el_celular():
    assert '<div id="inteligencia_fin-panel" class="panel ifn-panel">' in PANEL
    afuera = CSS.replace(_bloque_movil(), "")
    assert ".ifn-panel" not in afuera


def test_las_reglas_de_escritorio_siguen_iguales():
    """Lo de arriba de 768px no se toco: estas son las de antes, letra por letra."""
    afuera = CSS.replace(_bloque_movil(), "")
    for regla in (
        ".ifn-diag-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}",
        ".ifn-contraste{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin-bottom:16px}",
        ".ifn-rec-cab{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}",
        ".ifn-impacto{font-size:1.15rem;font-weight:700;text-align:right;white-space:nowrap}",
        ".ifn-rec-pie{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:10px}",
        ".ifn-calculo{background:var(--relleno);color:var(--texto);border-radius:6px;padding:10px 12px;font-size:.76rem;line-height:1.5;white-space:pre-wrap;overflow-x:auto;margin:8px 0}",
        ".ifn-accion{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;font-size:.8rem;color:var(--texto)}",
    ):
        assert regla in afuera, regla


def test_el_celular_parte_palabras_y_agranda_los_botones():
    movil = _bloque_movil()
    assert ".ifn-panel,.ifn-afinar{overflow-wrap:anywhere" in movil
    assert ".ifn-diag-grid,.ifn-contraste{grid-template-columns:minmax(0,1fr)}" in movil
    assert "min-height:44px" in movil
    assert "!important" not in movil


def test_las_clases_del_celular_existen_en_el_js_o_en_el_panel():
    """Un selector que no pinta nada no arregla nada."""
    for clase in set(re.findall(r"\.(ifn-[a-z-]+)", _bloque_movil())):
        assert clase in JS or clase in PANEL, clase


# ── pintado de verdad ────────────────────────────────────────────────────────

def _estado(tmp_path) -> dict:
    db = str(tmp_path / "movil.db")
    init_db(db)
    for p in ("2026-06", "2026-07", "2026-08", "2026-09"):
        crear_movimiento(db, tipo="egreso", fecha=f"{p}-10", periodo=p, concepto="Servidores",
                         categoria="servicios", monto=2900, moneda="USD", monto_usd=2900)
    bid = insert_business(db, {"name": LARGO, "phone": "099 123 456", "scraped_at": "2026-07-01 10:00:00"})
    update_business(db, bid, crm_status="cerrado")
    crear_movimiento(db, tipo="ingreso", fecha="2026-07-05", periodo="2026-07", concepto="Web",
                     categoria="desarrollo_web", monto=3000, moneda="USD", monto_usd=3000, client_id=bid)
    for i in range(3):
        otro = insert_business(db, {"name": f"Cliente {i}", "phone": f"09{i} 555 111"})
        update_business(db, otro, crm_status="finalizado")
        crear_por_cobrar(db, client_id=otro, concepto="Cuota", monto_usd=350, vence="2026-08-20")
    crear_por_cobrar(db, client_id=bid, concepto="50% final", monto_usd=12800, vence="2026-09-01")
    ifn.corrida_diaria(db, AHORA)
    estado = json.loads(json.dumps(ifn.estado_pantalla(db, es_admin=True, ahora=AHORA), default=str))
    assert estado["recomendaciones"] and estado["diagnostico"]
    # Lo peor que puede llegar: todo con una palabra sin espacios.
    estado["resumen"] = {"texto": "El mes viene en rojo. " + LARGO, "origen": "reglas"}
    estado["diagnostico"][0]["titulo"] += " " + LARGO
    estado["diagnostico"][0]["calculo"] += "\n" + LARGO
    estado["encabezado"]["calculo_hoy"] = (estado["encabezado"].get("calculo_hoy") or "") + " " + LARGO
    rec = estado["recomendaciones"][0]
    rec["titulo"] += " " + LARGO
    rec["calculo"] += "\n" + LARGO
    rec["supuestos"] = [LARGO]
    rec["impacto_mensual"] = 1234567
    estado["seguimiento"] = [{"regla": "R4", "titulo": LARGO, "resultado": "midiendo", "tomada_el": "01/09/2026",
                              "se_mide_el": "01/10/2026", "impacto_esperado": 1050, "impacto_real": 0,
                              "detalle_real": LARGO}]
    return estado


def _pagina(estado: dict) -> str:
    estilos = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", HTML, re.S))
    panel = PANEL.replace('class="panel ifn-panel"', 'class="panel ifn-panel active"')
    return ("<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<style>{estilos}</style></head><body><div class='main'>{panel}</div>"
            f"<script>{dashboard.ESC_JS}\nlet activePanel = 'inteligencia_fin';\n{JS}\n"
            f"ifnEstado = {json.dumps(estado, ensure_ascii=False)};\nifnPintar();\n"
            "document.querySelectorAll('details').forEach(d => { d.open = true; });"
            "</script></body></html>")


_MEDIR = """() => {
  const p = document.getElementById('inteligencia_fin-panel');
  const W = window.innerWidth;
  const salidos = [];
  p.querySelectorAll('*').forEach(el => {
    const r = el.getBoundingClientRect();
    const desborda = el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflowX !== 'visible';
    if (r.width && (r.right > W + 0.5 || r.left < -0.5 || desborda)) salidos.push(el.tagName + '.' + el.className);
  });
  const columnas = sel => getComputedStyle(p.querySelector(sel)).gridTemplateColumns.split(' ').length;
  const pie = p.querySelector('.ifn-rec-pie');
  return {
    W, pagina: document.documentElement.scrollWidth, salidos,
    diag: columnas('.ifn-diag-grid'), contraste: columnas('.ifn-contraste'),
    pie: getComputedStyle(pie).display,
    cab: getComputedStyle(p.querySelector('.ifn-rec-cab')).flexDirection,
    botones: [...pie.querySelectorAll('button')].map(b => {
      const r = b.getBoundingClientRect();
      return {texto: b.textContent, izq: r.left, der: r.right, alto: r.height};
    }),
    cobranza: [...p.querySelectorAll('.ifn-accion .btn-primary')].map(b => b.getBoundingClientRect().height),
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


def _medir(navegador, estado, ancho, alto=800):
    pagina = navegador.new_page(viewport={"width": ancho, "height": alto})
    try:
        pagina.set_content(_pagina(estado), wait_until="domcontentloaded")
        return pagina.evaluate(_MEDIR)
    finally:
        pagina.close()


@pytest.mark.parametrize("ancho", [360, 375, 414])
def test_en_el_celular_no_se_corta_nada(navegador, tmp_path, ancho):
    m = _medir(navegador, _estado(tmp_path), ancho)
    assert m["W"] == ancho
    assert m["pagina"] <= ancho, f"la pagina se va de costado: {m['pagina']}px"
    assert m["salidos"] == [], m["salidos"]
    assert m["diag"] == 1 and m["contraste"] == 1
    assert m["cab"] == "column"
    assert [b["texto"] for b in m["botones"]] == ["Lo voy a hacer", "Descartar"]
    for b in m["botones"]:
        assert b["alto"] >= 44 and b["izq"] >= 0 and b["der"] <= ancho, b
    assert m["cobranza"] and min(m["cobranza"]) >= 44
    for dato in ("Diagnóstico del mes", "Seguimiento de lo que tomaste",
                 "Confianza", "USD 1.234.567"):
        assert dato in m["texto"], dato
    # El titulo de seccion de las alternativas se fue (Juan: "mucho texto"):
    # ahora lo que encabeza la pantalla es el objetivo del mes.
    assert "Objetivo del mes" in m["texto"].replace("OBJETIVO DEL MES", "Objetivo del mes")


def test_en_la_computadora_queda_como_estaba(navegador, tmp_path):
    m = _medir(navegador, _estado(tmp_path), 1280)
    assert m["diag"] > 1 and m["contraste"] > 1, m
    assert m["pie"] == "flex" and m["cab"] == "row"
    assert all(b["alto"] < 44 for b in m["botones"]), m["botones"]
