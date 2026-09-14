"""El panel del simulador financiero: registrado, sin números escondidos, con
tokens, y que se pinta de verdad contra un DOM falso.

Las trampas que esto ataja salieron de incidentes reales: un `{#` en el HTML
tumba la página entera por Jinja, un backslash lo come Python, y un panel que
falta en ALL_PANELS existe pero no se le puede asignar a nadie.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import dashboard

HTML = dashboard.DASHBOARD_HTML
RAIZ = Path(__file__).resolve().parents[1]
SRC = (RAIZ / "dashboard.py").read_text(encoding="utf-8")

sin_node = pytest.mark.skipif(shutil.which("node") is None,
                              reason="node no esta instalado")


def _entre(texto: str, desde: str, hasta: str) -> str:
    i = texto.index(desde)
    return texto[i:texto.index(hasta, i + len(desde))]


JS_FUENTE = _entre(SRC, "// ========== Simulador financiero ==========",
                   "// ========== Panel de Marketing ==========")
PANEL_FUENTE = _entre(SRC, "<!-- ======= SIMULADOR FINANCIERO PANEL ======= -->",
                      "<!-- ======= METRICS PANEL ======= -->")
CSS_FUENTE = _entre(SRC, "/* ── Simulador financiero", "</style>")


# ── registrado en todos los lugares ──────────────────────────────────────────

def test_el_item_del_menu_esta_bajo_gestion_al_lado_de_finanzas():
    gestion = _entre(HTML, '<div class="nav-section-label">GESTIÓN</div>', "</div>\n  </div>")
    finanzas = gestion.index('id="nav-finanzas"')
    simulador = gestion.index('id="nav-simulador"')
    assert simulador > finanzas
    assert "Simulador financiero</div>" in gestion[simulador:simulador + 200]
    assert "showPanel('simulador')" in HTML


def test_el_panel_existe_y_showpanel_lo_carga():
    assert 'id="simulador-panel"' in HTML
    assert "if (name === 'simulador') loadSimulador();" in HTML


def test_esta_en_los_dos_all_panels_y_en_el_editor_de_roles():
    listas = re.findall(r"const ALL_PANELS = \[([^\]]*)\]", SRC)
    assert len(listas) == 2
    for lista in listas:
        assert "'simulador'" in lista
    etiquetas = re.search(r"const PANEL_LABELS = \{([^}]*)\}", SRC).group(1)
    assert "simulador:'Simulador financiero'" in etiquetas


def test_esta_en_la_navegacion_mobile():
    assert "'simulador'" in re.search(r"const NAV_PRIORITY = \[([^\]]*)\]", HTML).group(1)
    assert "simulador:'calculator'" in re.search(r"const NAV_ICONS = \{(.*?)\n\}", HTML, re.S).group(1)
    assert "simulador:'Simulador'" in re.search(r"const NAV_LABELS = \{(.*?)\n\}", HTML, re.S).group(1)


def test_el_blueprint_esta_registrado():
    assert "from routes.simulador import simulador_bp" in SRC
    registro = re.search(r"for bp in \((.*?)\):", SRC, re.S).group(1)
    assert "simulador_bp" in registro


def test_no_se_le_da_el_panel_a_los_roles_existentes():
    """Ruling R20, igual que Finanzas: muestra sueldos y lo que se debe cobrar."""
    fuente = (RAIZ / "database.py").read_text(encoding="utf-8")
    assert '_grant_panel_to_existing_roles(conn, "simulador")' not in fuente


def test_la_pagina_principal_se_renderiza_con_el_panel(tmp_path, monkeypatch):
    from werkzeug.security import generate_password_hash
    from database import create_user, init_db

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

    assert r.status_code == 200
    assert b'id="simulador-panel"' in r.data
    assert b"function simCalcular(" in r.data


# ── las trampas de DASHBOARD_HTML ────────────────────────────────────────────

_FUENTES = {"js": JS_FUENTE, "panel": PANEL_FUENTE, "css": CSS_FUENTE}


@pytest.mark.parametrize("nombre", sorted(_FUENTES))
def test_nada_que_jinja_o_python_interpreten(nombre):
    texto = _FUENTES[nombre]
    for trampa in ("{#", "{{", "{%"):
        assert trampa not in texto, f"{trampa!r} en el {nombre} del simulador"
    assert chr(92) not in texto, f"un backslash en el {nombre}: Python se lo come"


def test_todo_lo_del_js_lleva_el_prefijo_sim():
    """En JS gana la última declaración con el mismo nombre."""
    nombres = re.findall(r"^(?:async )?function ([A-Za-z_$][\w$]*)\(", JS_FUENTE, re.M)
    nombres += re.findall(r"^(?:const|let) ([A-Za-z_$][\w$]*)", JS_FUENTE, re.M)
    assert len(nombres) > 30
    # `loadSimulador` sigue la convención de showPanel (loadFinanzas, loadMarketing).
    sueltos = [n for n in nombres
               if not (n.startswith("sim") or n.startswith("SIM_") or n == "loadSimulador")]
    assert not sueltos, f"sin prefijo: {sueltos}"
    for nombre in nombres:
        declaraciones = re.findall(r"^(?:async )?(?:function|const|let) " + re.escape(nombre) + r"\b",
                                   HTML, re.M)
        assert len(declaraciones) == 1, f"{nombre} está declarado {len(declaraciones)} veces"


def test_no_hay_emojis():
    for texto in (JS_FUENTE, PANEL_FUENTE, CSS_FUENTE):
        assert not re.search(r"[\U0001F300-\U0001FAFF]", texto)


# ── ningún número escondido ──────────────────────────────────────────────────

def _funcion(nombre: str) -> str:
    m = re.search(r"\nfunction " + nombre + r"\(.*?\n\}", HTML, re.S)
    assert m, f"no encontré {nombre}"
    return m.group(0)


@pytest.mark.parametrize("nombre", ["simCalcular", "simNormalizar", "simNumero"])
def test_criterio_8_la_cuenta_no_tiene_numeros_escritos(nombre):
    """Todo supuesto sale de un campo. Lo único escrito es aritmética: 0, 1 y
    el 100 que convierte un porcentaje entero en fracción."""
    cuerpo = _funcion(nombre)
    cuerpo = re.sub(r"//[^\n]*", "", cuerpo)
    cuerpo = re.sub(r"'[^'\n]*'", "''", cuerpo)
    numeros = set(re.findall(r"(?<![\w$.])\d+(?:\.\d+)?(?:e-?\d+)?(?![\w$])", cuerpo))
    assert numeros <= {"0", "1", "100"}, f"números escritos en {nombre}: {numeros}"


def test_cada_campo_de_la_cuenta_tiene_su_control_en_pantalla():
    campos = re.findall(r"\['([\w.]+)', '(?:entero|monto|porcentaje|factor|saldo)'", JS_FUENTE)
    assert len(campos) >= 20
    for ruta in campos:
        assert f'data-sim="{ruta}"' in PANEL_FUENTE, f"{ruta} no tiene control"
    usados = set(re.findall(r'data-sim="([\w.]+)"', PANEL_FUENTE))
    assert usados == set(campos), f"controles sin campo: {usados - set(campos)}"


def test_cada_palanca_tiene_su_boton_y_su_monto():
    palancas = re.search(r"palancas: \{(.*?)\}", JS_FUENTE, re.S).group(1)
    for nombre in re.findall(r"(\w+): false", palancas):
        assert f'id="sim-palanca-{nombre}"' in PANEL_FUENTE, nombre
        assert f"simPalanca('{nombre}')" in PANEL_FUENTE, nombre
    for monto in ("projectManager", "miSueldo", "subcontratoPorcentaje",
                  "matiasPorcentaje", "aporteJavier"):
        assert f'data-sim="montosPalancas.{monto}"' in PANEL_FUENTE, monto


def test_los_defaults_estan_en_un_solo_objeto():
    assert HTML.count("const SIM_DEFAULTS = {") == 1
    precarga = (RAIZ / "services" / "simulador.py").read_text(encoding="utf-8")
    for valor in ("700", "1100", "2000", "Agencia de marketing"):
        assert valor not in precarga, f"{valor} escrito en el servidor"


def test_los_resultados_van_en_el_orden_de_la_especificacion():
    cuerpo = _funcion("simPintarResultados")
    orden = [cuerpo.index(f"tarjeta('{t}'") for t in ("Facturás", "Cobrás", "Sale", "Caja del mes")]
    assert orden == sorted(orden)


# ── tokens ───────────────────────────────────────────────────────────────────

def test_el_css_usa_solo_tokens():
    reglas = re.findall(r"([^{}]+)\{([^{}]*)\}", CSS_FUENTE)
    assert len(reglas) > 40
    for selector, cuerpo in reglas:
        assert not re.search(r"#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])|rgba?\(", cuerpo), \
            f"{selector.strip()} tiene un color a mano"
    assert "body.light" not in CSS_FUENTE.split("*/", 1)[1]


def test_el_panel_no_pinta_colores_inline():
    for texto in (PANEL_FUENTE, JS_FUENTE):
        assert not re.search(r"style=", texto), "estilos inline en el simulador"


# ── se pinta de verdad ───────────────────────────────────────────────────────

_ARNES = r"""
const _els = {};
function _el(id) {
  if (!_els[id]) {
    _els[id] = { id, innerHTML: '', textContent: '', value: '', placeholder: '', type: '',
                 className: '', style: {}, dataset: {}, options: [], selectedIndex: 0,
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
const _PRECARGA = __PRECARGA__;
globalThis.fetch = (url) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(
  String(url) === '/api/simulador/precarga' ? _PRECARGA
  : String(url) === '/api/simulador/escenarios' ? [] : {}) });
"""

_PRUEBA = r"""
(async () => {
  await loadSimulador();
  const s = {};
  s.tarjetas = _el('sim-tarjetas').innerHTML;
  s.cierre = _el('sim-cierre').textContent;
  s.subGastos = _el('sim-sub-gastosFijos').textContent;
  s.listaMants = _el('sim-lista-mantenimientos').innerHTML;
  s.listaPends = _el('sim-lista-pendientes').innerHTML;
  s.capacidad = _el('sim-semaforo-capacidad').innerHTML;
  s.embudo = _el('sim-semaforo-embudo').innerHTML;
  s.meta = _el('sim-meta').textContent;
  s.arrastre = _el('sim-arrastre').textContent;
  s.mini = _el('sim-mini').innerHTML;
  s.origenGastos = _el('sim-origen-gastosFijos').textContent;
  s.origenCaja = _el('sim-origen-caja').textContent;

  simAlCambiar({target: {dataset: {sim: 'ventas.precioEcom'}, value: '1500'}});
  s.tarjetasDespues = _el('sim-tarjetas').innerHTML;

  const antes = simEstado.gastosFijos.length;
  _el('sim-nuevo-nombre-gastosFijos').value = '   ';
  simAgregarFila('gastosFijos');
  s.errorSinNombre = _el('sim-nuevo-error-gastosFijos').textContent;
  s.filasTrasError = simEstado.gastosFijos.length - antes;

  _el('sim-nuevo-nombre-gastosFijos').value = 'Figma';
  _el('sim-nuevo-monto-gastosFijos').value = '15';
  simAgregarFila('gastosFijos');
  s.filasTrasAgregar = simEstado.gastosFijos.length - antes;
  s.errorDespues = _el('sim-nuevo-error-gastosFijos').textContent;
  s.subGastosDespues = _el('sim-sub-gastosFijos').textContent;

  simAlCambiar({target: {dataset: {simLista: 'gastosFijos', i: '0', campo: 'activo'}, checked: false}});
  s.filasTrasApagar = simEstado.gastosFijos.length - antes;
  s.primeraActiva = simEstado.gastosFijos[0].activo;

  simPalanca('aporteJavier');
  s.tarjetasConJavier = _el('sim-tarjetas').innerHTML;

  simBorrarFila('gastosFijos', 0);
  s.filasTrasBorrar = simEstado.gastosFijos.length - antes;

  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
"""


@sin_node
def test_el_panel_se_pinta_y_responde(tmp_path):
    precarga = {"moneda": "USD", "cajaActual": 1200, "gastosFijos": [],
                "mantenimientos": [{"nombre": "Bar O'Higgins <b>", "activo": False, "nota": ""}],
                "pendientes": [{"nombre": "Cliente viejo", "monto": 900, "activo": False,
                                "nota": "Saldo"}]}
    bloques = re.findall(r"<script>(.*?)</script>", HTML, re.S)
    archivo = tmp_path / "sim_panel.js"
    archivo.write_text(_ARNES.replace("__PRECARGA__", json.dumps(precarga))
                       + "\n".join(bloques) + "\n" + _PRUEBA, encoding="utf-8")

    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, (r.stderr or "")[:2000]
    s = json.loads(r.stdout.strip().splitlines()[-1])

    for rotulo in ("Facturás", "Cobrás", "Sale", "Caja del mes"):
        assert rotulo in s["tarjetas"]
    assert "USD 270" in s["tarjetas"] and "sim-positivo" in s["tarjetas"]
    assert s["cierre"] == "Caja al cierre del mes: USD 1.470 = caja actual USD 1.200 + caja del mes USD 270."
    assert s["subGastos"] == "6 activos · USD 930", "sin fijos en Finanzas, el respaldo"
    assert "lista por defecto" in s["origenGastos"]
    assert "ingresos menos egresos" in s["origenCaja"]
    assert "&lt;b&gt;" in s["listaMants"] and "<b>" not in s["listaMants"], "el nombre va escapado"
    assert "sim-apagada" in s["listaMants"]
    assert "Cliente viejo" in s["listaPends"]
    assert "sim-rojo" in s["embudo"]
    assert "2 programadores aguantan 6" in s["capacidad"]
    assert "El equipo actual alcanza" in s["meta"]
    assert "USD 900 de pendientes viejos" in s["arrastre"]
    assert "Al cierre" in s["mini"]

    assert s["tarjetasDespues"] != s["tarjetas"]
    assert "USD 670" in s["tarjetasDespues"]

    assert s["errorSinNombre"] and s["filasTrasError"] == 0
    assert s["filasTrasAgregar"] == 1 and s["errorDespues"] == ""
    assert s["subGastosDespues"] == "7 activos · USD 945"
    assert s["filasTrasApagar"] == 1 and s["primeraActiva"] is False
    # 670 de antes, +300 de la agencia apagada, -15 de Figma, +1.000 de Javier.
    assert "USD 1.955" in s["tarjetasConJavier"]
    assert s["filasTrasBorrar"] == 0, "borrar es la única forma de sacar una fila"
