"""El panel de Inteligencia financiera: registrado, con tokens, sin trampas de
Jinja, sin pedir datos, y pintado de verdad en node con lo que devuelve el servidor."""

import json
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import (create_user, crear_movimiento, crear_por_cobrar, init_db,
                      insert_business, update_business)
from services import inteligencia_fin as ifn

HTML = dashboard.DASHBOARD_HTML
RAIZ = Path(__file__).resolve().parents[1]
SRC = (RAIZ / "dashboard.py").read_text(encoding="utf-8")
AHORA = datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc)

sin_node = pytest.mark.skipif(shutil.which("node") is None, reason="node no esta instalado")


def _entre(texto: str, desde: str, hasta: str) -> str:
    i = texto.index(desde)
    return texto[i:texto.index(hasta, i + len(desde))]


JS = _entre(SRC, "// ========== Inteligencia financiera ==========",
            "// ========== FIN Inteligencia financiera ==========")
PANEL = _entre(SRC, "<!-- ======= INTELIGENCIA FINANCIERA PANEL ======= -->",
               "<!-- ======= FIN INTELIGENCIA FINANCIERA PANEL ======= -->")
CSS = _entre(SRC, "/* ── Inteligencia financiera", "/* ── Plantillas")
FUENTES = {"js": JS, "panel": PANEL, "css": CSS}

# Lo que la pantalla ya no puede decir: no pide datos.
PEDIDOS = ("Falta", "falta cargar", "Cargá", "cargalo", "Elegí el", "ifn-comision",
           "datos que hacen falta", "No se muestra hasta tener el dato")


# ── registrado en todos los lugares ──────────────────────────────────────────

def test_es_el_tercer_item_de_finanzas():
    grupo = _entre(HTML, '<div class="nav-section-label">FINANZAS</div>',
                   '<div class="nav-section-label">VENTAS</div>')
    assert re.findall(r'id="nav-(\w+)"', grupo) == ["finanzas", "simulador", "inteligencia_fin"]


def test_esta_en_los_dos_all_panels_y_en_el_editor_de_roles():
    listas = re.findall(r"const ALL_PANELS = \[([^\]]*)\]", SRC)
    assert len(listas) == 2 and all("'inteligencia_fin'" in lista for lista in listas)
    assert "inteligencia_fin:'Inteligencia financiera'" in re.search(r"const PANEL_LABELS = \{([^}]*)\}", SRC).group(1)
    assert "if (name === 'inteligencia_fin') ifnCargar();" in HTML


def test_el_orden_de_la_pantalla():
    """Primero la decisión, después el respaldo.

    Arriba el objetivo del mes y el menú de alternativas (con el plan que sale
    de lo elegido); el diagnóstico, el contraste y el seguimiento quedan abajo
    como contexto de apoyo, que es lo que son.
    """
    orden = [PANEL.index(f'id="{i}"') for i in ("ifn-objetivo", "ifn-lista", "ifn-plan",
                                                "ifn-gastos", "ifn-resumen", "ifn-diagnostico",
                                                "ifn-contraste", "ifn-seguimiento")]
    assert orden == sorted(orden)


def test_la_pantalla_no_pide_datos():
    for pedido in PEDIDOS:
        assert pedido not in PANEL and pedido not in JS, pedido
    for viejo in ("ifnDatosHtml", "ifnAvisoHtml", "ifnGuardarComision", "ifnGuardarOrigen", "ifnGuardarCanalFijo"):
        assert viejo not in HTML, viejo


def test_afinar_es_opcional_y_colapsado():
    assert "<details class=\"ifn-afinar\"" in JS and "open" not in re.findall(r"<details[^>]*>", JS)[0]
    assert JS.count("<summary>Afinar (opcional)") == 2


def test_get_raiz_da_200_con_el_panel(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    db = str(tmp_path / "render.db")
    init_db(db)
    app = dashboard.create_app(db)
    uid = create_user(db, name="Jefe", email="jefe@test.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    cli = app.test_client()
    with cli.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Jefe"
    r = cli.get("/")
    assert r.status_code == 200 and b'id="inteligencia_fin-panel"' in r.data


# ── las trampas de DASHBOARD_HTML ────────────────────────────────────────────

@pytest.mark.parametrize("nombre", sorted(FUENTES))
def test_nada_que_jinja_o_python_interpreten(nombre):
    texto = FUENTES[nombre]
    for trampa in ("{#", "{{", "{%"):
        assert trampa not in texto, f"{trampa!r} en el {nombre}"
    assert chr(92) not in texto, f"un backslash en el {nombre}"


def test_todo_lo_del_js_lleva_el_prefijo_ifn():
    nombres = re.findall(r"^(?:async )?function ([A-Za-z_$][\w$]*)\(", JS, re.M)
    nombres += re.findall(r"^(?:const|let) ([A-Za-z_$][\w$]*)", JS, re.M)
    assert len(nombres) > 15
    assert not [n for n in nombres if not (n.startswith("ifn") or n.startswith("IFN_"))]
    for nombre in nombres:
        assert len(re.findall(r"^(?:async )?(?:function|const|let) " + re.escape(nombre) + r"\b", HTML, re.M)) == 1, nombre


def test_el_css_usa_solo_tokens():
    reglas = re.findall(r"([^{}]+)\{([^{}]*)\}", CSS)
    assert len(reglas) > 40
    for selector, cuerpo in reglas:
        assert not re.search(r"#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])|rgba?\(", cuerpo), selector.strip()
    assert "body.light" not in CSS
    assert "style=" not in PANEL and "style=" not in JS


# ── se pinta de verdad ───────────────────────────────────────────────────────

_ARNES = r"""
const _els = {};
function _el(id) {
  if (!_els[id]) {
    _els[id] = { id, innerHTML: '', textContent: '', value: '', placeholder: '', type: '',
                 className: '', style: {}, dataset: {}, options: [], selectedIndex: 0, disabled: false,
                 classList: { add(){}, remove(){}, toggle(){}, contains: () => false },
                 querySelectorAll: () => [], querySelector: () => null, closest: () => null,
                 setAttribute(){}, getAttribute: () => null, focus(){},
                 addEventListener(){}, appendChild(){}, remove(){} };
  }
  return _els[id];
}
globalThis.document = {
  getElementById: _el, querySelectorAll: () => [], querySelector: () => null,
  body: { classList: { contains: () => false, add(){}, remove(){}, toggle(){} } },
  createElement: () => _el('tmp'), addEventListener(){},
};
globalThis.window = globalThis;
globalThis.localStorage = { getItem: () => null, setItem(){}, removeItem(){} };
globalThis.lucide = { createIcons(){} };
globalThis.alert = () => {};
globalThis.confirm = () => true;
globalThis.setInterval = () => 0;
const _RESPUESTAS = __RESPUESTAS__;
const _pedidos = [];
globalThis.fetch = (url, opciones) => {
  _pedidos.push([String(url), (opciones && opciones.method) || 'GET']);
  const r = _RESPUESTAS[String(url)];
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(r === undefined ? {} : r) });
};
"""

_IDS = ['ifn-objetivo', 'ifn-lista', 'ifn-plan', 'ifn-gastos', 'ifn-resumen', 'ifn-diagnostico',
        'ifn-contraste', 'ifn-seguimiento']

_PRUEBA = """
(async () => {
  activePanel = 'inteligencia_fin';
  await ifnCargar();
  const s = {sinElegir: _el('ifn-lista').innerHTML};
  // La tarjeta compacta es la que se elige; el detalle (la cuenta, los
  // supuestos y los botones) aparece al elegirla, como en el mockup. Se eligen
  // dos: la de los vencidos (que trae acciones de cobranza) y la del
  // mantenimiento (que es la que declara supuestos).
  ifnAlternar(__REC__);
  ifnAlternar(__REC2__);
  __IDS__.forEach(id => { s[id] = _el(id).innerHTML; });
  s.bajada = _el('ifn-bajada').textContent;
  s.elegidas = ifnElegidas.slice();
  s.tarjetaNotion = _notionClientCardHtml({id: 7, name: 'Bar', status: 'Perdido', notion_page_id: 'x', motivo_perdida: null}, false);
  s.esfuerzo = ifnEsfuerzoHtml({id: 3, esfuerzo_horas: 40, esfuerzo_valor: 5, esfuerzo_unidad: 'dias'});
  await ifnTomar(__REC__);
  s.pedidos = _pedidos;
  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
"""


@sin_node
def test_la_pantalla_se_pinta_sin_pedir_nada(tmp_path):
    db = str(tmp_path / "pinta.db")
    init_db(db)
    for p in ("2026-06", "2026-07", "2026-08"):
        crear_movimiento(db, tipo="egreso", fecha=f"{p}-10", periodo=p, concepto="Gasto", categoria="servicios",
                         monto=900, moneda="USD", monto_usd=900)
    bid = insert_business(db, {"name": "Bar <b>Tito</b>", "phone": "099 123 456", "scraped_at": "2026-07-01 10:00:00"})
    update_business(db, bid, crm_status="cerrado")
    crear_movimiento(db, tipo="ingreso", fecha="2026-07-05", periodo="2026-07", concepto="Web",
                     categoria="desarrollo_web", monto=3000, moneda="USD", monto_usd=3000, client_id=bid)
    for i in range(2):
        otro = insert_business(db, {"name": f"Cliente {i}"})
        update_business(db, otro, crm_status="finalizado")
    crear_por_cobrar(db, client_id=bid, concepto="50% final", monto_usd=800, vence="2026-09-01")
    ifn.corrida_diaria(db, AHORA)
    estado = ifn.estado_pantalla(db, es_admin=True, ahora=AHORA)
    rec = next(r for r in estado["recomendaciones"] if r["regla"] == "R4")
    rec1 = next(r for r in estado["recomendaciones"] if r["regla"] == "R1")

    respuestas = {"/api/inteligencia-fin": json.loads(json.dumps(estado, default=str))}
    bloques = re.findall(r"<script>(.*?)</script>", HTML, re.S)
    archivo = tmp_path / "ifn.js"
    archivo.write_text(_ARNES.replace("__RESPUESTAS__", json.dumps(respuestas, ensure_ascii=False))
                       + "\n".join(bloques) + "\n"
                       + _PRUEBA.replace("__REC2__", str(rec1["id"]))
                                .replace("__REC__", str(rec["id"]))
                                .replace("__IDS__", json.dumps(_IDS)),
                       encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, (r.stderr or "")[:2000]
    s = json.loads(r.stdout.strip().splitlines()[-1])

    # Lo primero que se ve: el objetivo y el menú de alternativas de colores.
    assert "Objetivo del mes" in s["ifn-objetivo"] and "Con lo seleccionado" in s["ifn-objetivo"]
    assert "Alternativas para llegar al objetivo" in s["ifn-lista"]
    assert "ifn-alt-" in s["sinElegir"], "las alternativas se ven sin elegir nada"
    assert sorted(s["elegidas"]) == sorted([rec["id"], rec1["id"]])
    assert "Plan resultante" in s["ifn-plan"]
    assert "Gastos del mes" in s["ifn-gastos"]

    assert "Diagnóstico del mes" in s["ifn-diagnostico"] and "Resultado del mes" in s["ifn-diagnostico"]
    assert "ifn-nivel-mal" in s["ifn-diagnostico"]
    assert "Resumen armado con los números de abajo" in s["ifn-resumen"]
    lista = s["ifn-lista"]
    assert "Cobrá los USD 800 vencidos" in lista and "Confianza alta" in lista
    assert "Supuestos: Comisión de cobro 5 %" in lista
    assert "Lo voy a hacer" in lista and "Descartar" in lista
    assert "https://wa.me/59899123456?text=" in lista
    assert "&lt;b&gt;Tito" in lista and "<b>Tito" not in lista
    todo = "".join(s[k] for k in _IDS)
    for pedido in PEDIDOS:
        assert pedido not in todo, pedido
    assert "Calculado el 15/09/2026" in s["bajada"]

    assert "<details" in s["tarjetaNotion"] and "Afinar (opcional)" in s["tarjetaNotion"]
    assert "Falta" not in s["tarjetaNotion"]
    assert "Afinar (opcional) · 5 días" in s["esfuerzo"]
    assert [f"/api/inteligencia-fin/recomendaciones/{rec['id']}/tomar", "POST"] in s["pedidos"]
