"""El panel de Inteligencia financiera: registrado, con tokens, sin trampas de
Jinja, y pintado de verdad en node con lo que devuelve el servidor."""

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
from database import (create_user, crear_por_cobrar, init_db, insert_business,
                      update_business)
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


# ── registrado en todos los lugares ──────────────────────────────────────────

def test_es_el_tercer_item_de_finanzas():
    grupo = _entre(HTML, '<div class="nav-section-label">FINANZAS</div>',
                   '<div class="nav-section-label">VENTAS</div>')
    assert re.findall(r'id="nav-(\w+)"', grupo) == ["finanzas", "simulador", "inteligencia_fin"]
    assert ('<div class="nav-item" id="nav-inteligencia_fin" onclick="showPanel(\'inteligencia_fin\')">'
            '<i data-lucide="lightbulb" class="nav-icon"></i> Inteligencia financiera</div>') in grupo


def test_esta_en_los_dos_all_panels_y_en_el_editor_de_roles():
    listas = re.findall(r"const ALL_PANELS = \[([^\]]*)\]", SRC)
    assert len(listas) == 2 and all("'inteligencia_fin'" in lista for lista in listas)
    etiquetas = re.search(r"const PANEL_LABELS = \{([^}]*)\}", SRC).group(1)
    assert "inteligencia_fin:'Inteligencia financiera'" in etiquetas


def test_navegacion_mobile_colores_y_carga():
    assert "'inteligencia_fin'" in re.search(r"const NAV_PRIORITY = \[([^\]]*)\]", HTML).group(1)
    assert "inteligencia_fin:'lightbulb'" in re.search(r"const NAV_ICONS = \{(.*?)\n\}", HTML, re.S).group(1)
    assert "inteligencia_fin:'Intel. financiera'" in re.search(r"const NAV_LABELS = \{(.*?)\n\}", HTML, re.S).group(1)
    for regla in ("#nav-inteligencia_fin .nav-icon{stroke:", "#nav-inteligencia_fin.active .nav-icon{stroke:",
                  "body.light #nav-inteligencia_fin .nav-icon{stroke:"):
        assert len(re.findall("^" + re.escape(regla), HTML, re.M)) == 1, regla
    assert "if (name === 'inteligencia_fin') ifnCargar();" in HTML
    assert PANEL.count('id="inteligencia_fin-panel" class="panel"') == 1


def test_el_blueprint_esta_registrado():
    assert "from routes.inteligencia_fin import inteligencia_fin_bp" in SRC
    assert "inteligencia_fin_bp" in re.search(r"for bp in \((.*?)\):", SRC, re.S).group(1)


def test_no_se_le_da_el_panel_a_los_roles_existentes():
    """Ruling R20, igual que Finanzas y el Simulador."""
    fuente = (RAIZ / "database.py").read_text(encoding="utf-8")
    assert '_grant_panel_to_existing_roles(conn, "inteligencia_fin")' not in fuente
    assert "inteligencia_fin" not in "".join(re.findall(r"_grant_panel_to_existing_roles\([^)]*\)", fuente))


def test_los_tres_datos_van_antes_que_las_recomendaciones():
    orden = [PANEL.index(f'id="{i}"') for i in ("ifn-datos", "ifn-contraste", "ifn-avisos",
                                                 "ifn-lista", "ifn-seguimiento")]
    assert orden == sorted(orden)


def _login(tmp_path, monkeypatch):
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
    return cli, db


def test_get_raiz_da_200_con_el_panel(tmp_path, monkeypatch):
    cli, _ = _login(tmp_path, monkeypatch)
    r = cli.get("/")
    assert r.status_code == 200
    assert b'id="inteligencia_fin-panel"' in r.data
    assert b"function ifnTarjetaHtml(" in r.data


# ── las trampas de DASHBOARD_HTML ────────────────────────────────────────────

@pytest.mark.parametrize("nombre", sorted(FUENTES))
def test_nada_que_jinja_o_python_interpreten(nombre):
    texto = FUENTES[nombre]
    for trampa in ("{#", "{{", "{%"):
        assert trampa not in texto, f"{trampa!r} en el {nombre}"
    assert chr(92) not in texto, f"un backslash en el {nombre}: Python se lo come"


def test_todo_lo_del_js_lleva_el_prefijo_ifn():
    nombres = re.findall(r"^(?:async )?function ([A-Za-z_$][\w$]*)\(", JS, re.M)
    nombres += re.findall(r"^(?:const|let) ([A-Za-z_$][\w$]*)", JS, re.M)
    assert len(nombres) > 20
    sueltos = [n for n in nombres if not (n.startswith("ifn") or n.startswith("IFN_"))]
    assert not sueltos, f"sin prefijo: {sueltos}"
    for nombre in nombres:
        declaraciones = re.findall(r"^(?:async )?(?:function|const|let) " + re.escape(nombre) + r"\b", HTML, re.M)
        assert len(declaraciones) == 1, f"{nombre} está declarado {len(declaraciones)} veces"


def test_sin_setinterval_ni_showpanel_en_la_carga():
    assert "setInterval" not in JS
    assert "showPanel(" not in JS


def test_el_css_usa_solo_tokens():
    reglas = re.findall(r"([^{}]+)\{([^{}]*)\}", CSS)
    assert len(reglas) > 40
    for selector, cuerpo in reglas:
        assert not re.search(r"#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])|rgba?\(", cuerpo), selector.strip()
    assert "body.light" not in CSS
    for nombre in ("panel", "js"):
        assert "style=" not in FUENTES[nombre], f"estilo inline en el {nombre}"


def test_los_bordes_por_tipo():
    assert ".ifn-rec-ingreso{border-left-color:var(--verde)}" in CSS
    assert ".ifn-rec-recorte{border-left-color:var(--ambar)}" in CSS
    assert ".ifn-rec-alerta{border-left-color:var(--rojo)}" in CSS


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
  if (r === 'falla') return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) });
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(r === undefined ? {} : r) });
};
"""

_PRUEBA = """
(async () => {
  activePanel = 'inteligencia_fin';
  await ifnCargar();
  const s = {};
  ['ifn-datos', 'ifn-contraste', 'ifn-avisos', 'ifn-lista', 'ifn-seguimiento'].forEach(id => { s[id] = _el(id).innerHTML; });
  s.bajada = _el('ifn-bajada').textContent;
  s.tarjetaNotion = _notionClientCardHtml({id: 7, name: 'Bar', status: 'Perdido', notion_page_id: 'x', motivo_perdida: null}, false);
  s.tarjetaViva = _notionClientCardHtml({id: 8, name: 'Viva', status: 'Demo Agendada', notion_page_id: 'y'}, false);
  s.esfuerzo = ifnEsfuerzoHtml({id: 3, esfuerzo_horas: 40, esfuerzo_valor: 5, esfuerzo_unidad: 'dias'});
  await ifnTomar(__REC__);
  await ifnGuardarMotivo('notion_client', 7, 'precio');
  s.pedidos = _pedidos;
  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
"""


@sin_node
def test_la_pantalla_se_pinta_con_lo_que_devuelve_el_servidor(tmp_path):
    db = str(tmp_path / "pinta.db")
    init_db(db)
    bid = insert_business(db, {"name": "Bar <b>Tito</b>", "phone": "099 123 456"})
    update_business(db, bid, crm_status="cerrado")
    crear_por_cobrar(db, client_id=bid, concepto="50% final", monto_usd=800, vence="2026-09-01")
    ifn.corrida_diaria(db, AHORA)
    estado = ifn.estado_pantalla(db, es_admin=True, ahora=AHORA)
    rec = next(r for r in estado["recomendaciones"] if r["regla"] == "R4")
    ifn.tomar(db, rec["id"], "Juan", AHORA)
    estado_con_seguimiento = ifn.estado_pantalla(db, es_admin=True, ahora=AHORA)
    estado_con_seguimiento["recomendaciones"] = estado["recomendaciones"]

    respuestas = {"/api/inteligencia-fin": json.loads(json.dumps(estado_con_seguimiento, default=str))}
    bloques = re.findall(r"<script>(.*?)</script>", HTML, re.S)
    archivo = tmp_path / "ifn.js"
    archivo.write_text(_ARNES.replace("__RESPUESTAS__", json.dumps(respuestas, ensure_ascii=False))
                       + "\n".join(bloques) + "\n" + _PRUEBA.replace("__REC__", str(rec["id"])),
                       encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, (r.stderr or "")[:2000]
    s = json.loads(r.stdout.strip().splitlines()[-1])

    lista = s["ifn-lista"]
    assert "Cobrá los USD 800 vencidos" in lista
    assert "+USD 800" in lista and "por única vez" in lista
    assert "Confianza alta" in lista
    assert "ifn-rec ifn-rec-ingreso" in lista
    assert '<pre class="ifn-calculo">' in lista and "Total vencido" in lista
    assert "Lo voy a hacer" in lista and "Descartar" in lista
    assert "https://wa.me/59899123456?text=" in lista and "Abrir mensaje de cobranza" in lista
    assert "&lt;b&gt;Tito" in lista and "<b>Tito" not in lista, "el nombre va escapado"

    datos = s["ifn-datos"]
    for rotulo in ("1. Motivo de pérdida", "2. Esfuerzo por proyecto", "3. Origen de la venta"):
        assert rotulo in datos
    assert 'id="ifn-comision"' in datos, "el admin carga la comisión"
    assert "Cómo cierra el mes hoy" in s["ifn-contraste"] and "Aplicando las tres primeras" in s["ifn-contraste"]
    assert "Falta el esfuerzo de los proyectos" in s["ifn-avisos"]
    assert "Midiendo" in s["ifn-seguimiento"] and "se mide el 15/10/2026" in s["ifn-seguimiento"]
    assert "Calculado el 15/09/2026" in s["bajada"] and "USD" in s["bajada"]

    assert "Falta el motivo de pérdida" in s["tarjetaNotion"] and "Se enfrió" in s["tarjetaNotion"]
    assert "ifn-motivo" not in s["tarjetaViva"]
    assert "5 días (40 h)" in s["esfuerzo"]

    pedidos = [tuple(p) for p in s["pedidos"]]
    assert (f"/api/inteligencia-fin/recomendaciones/{rec['id']}/tomar", "POST") in pedidos
    assert ("/api/perdidas/notion_client/7/motivo", "PUT") in pedidos
