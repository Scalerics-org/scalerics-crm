"""Los totales de la vista Fijos de Finanzas: egresos, ingresos y resultado.

Pedido de Juan (14/9): "el total de los egresos fijos, el total de los ingresos
fijos, y el resultado de los fijos". La cuenta vive en el navegador
(`_finTotalesFijos`) porque la lista ya llega entera con el `monto_usd` que
calculó el servidor; estos tests la ejecutan en node contra un DOM falso.

Reglas que se fijan acá:
- todo en USD con el `monto_usd` del servidor; un fijo en pesos sin tipo de
  cambio (monto_usd null) queda afuera y se avisa;
- los fijos son todos mensuales, así que la suma es "por mes";
- cuentan solo los activos que corren en el mes en curso (desde/hasta);
- resultado = ingresos - egresos, verde si es >= 0 y rojo si es negativo;
- al guardar o borrar un fijo se vuelve a llamar a loadFijos, que repinta.
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


JS_NUEVO = _entre(SRC, "function _finFijoVigente(", "async function borrarFijoUI(")


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
const _RESP = {};
const _pedidos = [];
globalThis.fetch = (url, opciones) => {
  _pedidos.push([String(url), (opciones && opciones.method) || 'GET']);
  const r = _RESP[String(url)];
  if (r === 'falla') return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) });
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(r === undefined ? {} : r) });
};
"""


def _correr(tmp_path, prueba):
    bloques = re.findall(r"<script>(.*?)</script>", HTML, re.S)
    archivo = tmp_path / "fijos.js"
    archivo.write_text(_ARNES + "\n".join(bloques) + "\n" + prueba, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, (r.stderr or "")[:2000]
    return json.loads(r.stdout.strip().splitlines()[-1])


def _fijo(fid, tipo, monto_usd, activo=1, moneda="USD", desde="2026-01",
          hasta=None, monto=None):
    return {"id": fid, "tipo": tipo, "concepto": f"Fijo {fid}",
            "categoria": "otros", "monto": monto if monto is not None else (monto_usd or 1000),
            "moneda": moneda, "tipo_cambio": None, "monto_usd": monto_usd,
            "dia_del_mes": 1, "desde": desde, "hasta": hasta, "activo": activo,
            "facturado": 0, "client_id": None, "notas": None}


def _tarjetas(html: str) -> dict:
    """{rótulo: (clases del valor, texto del valor)} de las tres tarjetas."""
    return {rotulo: (clases, valor) for rotulo, clases, valor in re.findall(
        r'<div class="fin-kpi-label">([^<]*)</div>\s*'
        r'<div class="fin-kpi-valor ([^"]*)">([^<]*)</div>', html)}


def _pintar(tmp_path, fijos, valores=(), mes="2026-09"):
    """Pinta la vista con esa lista y devuelve los contenedores y, por cada
    número de `valores`, cómo lo formatea `_finUsd` (así el test no depende
    del ICU de node)."""
    prueba = """
(async () => {
  _finMeses = {mes_actual: '__MES__', con_datos: [], abiertos: []};
  _RESP['/api/finanzas/recurrentes'] = __FIJOS__;
  await loadFijos();
  const usd = {};
  __VALORES__.forEach(v => { usd[String(v)] = _finUsd(v); });
  console.log(JSON.stringify({totales: _el('fin-fijos-totales').innerHTML,
                              cuerpo: _el('fin-fijos').innerHTML, usd}));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""".replace("__MES__", mes).replace("__FIJOS__", json.dumps(fijos)) \
       .replace("__VALORES__", json.dumps(list(valores)))
    return _correr(tmp_path, prueba)


# ── la cuenta ────────────────────────────────────────────────────────────────

@sin_node
def test_la_cuenta_suma_por_tipo_y_deja_afuera_lo_que_no_corre(tmp_path):
    fijos = [
        _fijo(1, "egreso", 100.5),
        _fijo(2, "egreso", 49.25),
        _fijo(3, "ingreso", 300),
        _fijo(4, "egreso", 999, activo=0),                       # apagado
        _fijo(5, "ingreso", 777, activo=0),                      # apagado
        _fijo(6, "egreso", None, moneda="UYU", monto=4000),      # sin tipo de cambio
        _fijo(7, "egreso", 25, moneda="UYU", monto=1000),        # pesos ya convertidos
        _fijo(8, "egreso", 500, hasta="2026-08"),                # ya terminó
        _fijo(9, "ingreso", 500, desde="2026-10"),               # todavía no empezó
        _fijo(10, "ingreso", 20, desde="2026-09", hasta="2026-09"),  # justo este mes
    ]
    prueba = ("console.log(JSON.stringify(_finTotalesFijos(%s, '2026-09')));"
              % json.dumps(fijos))
    t = _correr(tmp_path, prueba)

    assert t["egresos"] == 174.75          # 100.5 + 49.25 + 25
    assert t["ingresos"] == 320            # 300 + 20
    assert t["resultado"] == 145.25
    assert t["sinCotizar"] == 1
    assert t["contados"] == 5


@sin_node
def test_la_cuenta_sin_fijos_da_cero(tmp_path):
    t = _correr(tmp_path, "console.log(JSON.stringify([_finTotalesFijos([], '2026-09'),"
                          " _finTotalesFijos(null, '2026-09')]));")
    for totales in t:
        assert totales["egresos"] == 0 and totales["ingresos"] == 0
        assert totales["resultado"] == 0 and totales["sinCotizar"] == 0


@sin_node
def test_la_cuenta_redondea_a_centavos(tmp_path):
    fijos = [_fijo(1, "ingreso", 0.1), _fijo(2, "ingreso", 0.2), _fijo(3, "egreso", 0.3)]
    t = _correr(tmp_path, "console.log(JSON.stringify(_finTotalesFijos(%s, '2026-09')));"
                % json.dumps(fijos))
    assert t["ingresos"] == 0.3
    assert t["resultado"] == 0


# ── lo que se ve ─────────────────────────────────────────────────────────────

@sin_node
def test_se_ven_las_tres_tarjetas_con_resultado_positivo_en_verde(tmp_path):
    fijos = [_fijo(1, "egreso", 120), _fijo(2, "ingreso", 500),
             _fijo(3, "egreso", 80, activo=0)]
    s = _pintar(tmp_path, fijos, valores=(120, 500, 380))
    tarjetas = _tarjetas(s["totales"])

    assert list(tarjetas) == ["Total egresos fijos", "Total ingresos fijos",
                              "Resultado de los fijos"]
    assert tarjetas["Total egresos fijos"] == ("fin-rojo", s["usd"]["120"])
    assert tarjetas["Total ingresos fijos"] == ("fin-verde", s["usd"]["500"])
    assert tarjetas["Resultado de los fijos"] == ("fin-verde", s["usd"]["380"])
    assert "por mes" in s["totales"]
    # La lista de abajo se sigue pintando, con el apagado marcado.
    assert "Fijo 1" in s["cuerpo"] and "apagado" in s["cuerpo"]


@sin_node
def test_resultado_negativo_en_rojo(tmp_path):
    s = _pintar(tmp_path, [_fijo(1, "egreso", 700), _fijo(2, "ingreso", 200)],
                valores=(-500,))
    clases, valor = _tarjetas(s["totales"])["Resultado de los fijos"]
    assert clases == "fin-rojo"
    assert valor == s["usd"]["-500"]


@sin_node
def test_resultado_cero_en_verde(tmp_path):
    s = _pintar(tmp_path, [_fijo(1, "egreso", 200), _fijo(2, "ingreso", 200)])
    assert _tarjetas(s["totales"])["Resultado de los fijos"][0] == "fin-verde"


@sin_node
def test_sin_fijos_muestra_cero_sin_romper(tmp_path):
    s = _pintar(tmp_path, [], valores=(0,))
    tarjetas = _tarjetas(s["totales"])
    assert len(tarjetas) == 3
    assert {v for _, v in tarjetas.values()} == {s["usd"]["0"]}
    assert tarjetas["Resultado de los fijos"][0] == "fin-verde"
    assert "No hay fijos cargados" in s["cuerpo"]


@sin_node
def test_los_pesos_sin_tipo_de_cambio_quedan_afuera_y_se_avisa(tmp_path):
    fijos = [_fijo(1, "egreso", 100), _fijo(2, "egreso", None, moneda="UYU", monto=4000)]
    s = _pintar(tmp_path, fijos, valores=(100,))
    assert _tarjetas(s["totales"])["Total egresos fijos"][1] == s["usd"]["100"]
    assert "afuera de los totales" in s["cuerpo"]


@sin_node
def test_un_fijo_que_no_corre_este_mes_no_suma_y_se_marca(tmp_path):
    fijos = [_fijo(1, "egreso", 100), _fijo(2, "egreso", 900, hasta="2026-06")]
    s = _pintar(tmp_path, fijos, valores=(100,))
    assert _tarjetas(s["totales"])["Total egresos fijos"][1] == s["usd"]["100"]
    assert "no corre este mes" in s["cuerpo"]


@sin_node
def test_si_no_carga_no_quedan_totales_viejos(tmp_path):
    prueba = """
(async () => {
  _RESP['/api/finanzas/recurrentes'] = %s;
  await loadFijos();
  const antes = _el('fin-fijos-totales').innerHTML;
  _RESP['/api/finanzas/recurrentes'] = 'falla';
  await loadFijos();
  console.log(JSON.stringify({antes, despues: _el('fin-fijos-totales').innerHTML,
                              cuerpo: _el('fin-fijos').innerHTML}));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""" % json.dumps([_fijo(1, "egreso", 100)])
    s = _correr(tmp_path, prueba)
    assert "Total egresos fijos" in s["antes"]
    assert s["despues"] == ""
    assert "No se pudieron cargar los fijos" in s["cuerpo"]


@sin_node
def test_borrar_y_guardar_un_fijo_repintan_los_totales(tmp_path):
    """Los totales salen de loadFijos, y guardar y borrar la vuelven a llamar."""
    prueba = """
(async () => {
  _finMeses = {mes_actual: '2026-09', con_datos: [], abiertos: []};
  const url = '/api/finanzas/recurrentes';
  _RESP[url] = %s;
  await loadFijos();
  const inicial = _el('fin-fijos-totales').innerHTML;

  _RESP[url] = %s;
  await borrarFijoUI(2);
  await new Promise(r => setImmediate(r));
  const trasBorrar = _el('fin-fijos-totales').innerHTML;

  _RESP[url] = %s;
  _el('fin-fijo-id').value = '';
  _el('fin-fijo-moneda').value = 'USD';
  _el('fin-fijo-monto').value = '50';
  await guardarFijo();
  await new Promise(r => setImmediate(r));
  const trasGuardar = _el('fin-fijos-totales').innerHTML;

  console.log(JSON.stringify({inicial, trasBorrar, trasGuardar,
    usd: {'-100': _finUsd(-100), '0': _finUsd(0), '-300': _finUsd(-300), '150': _finUsd(150)},
    borro: _pedidos.some(p => p[0] === url + '/2' && p[1] === 'DELETE')}));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""" % (json.dumps([_fijo(1, "egreso", 300), _fijo(2, "ingreso", 200)]),
       json.dumps([_fijo(1, "egreso", 300)]),
       json.dumps([_fijo(1, "egreso", 300), _fijo(3, "ingreso", 450)]))
    s = _correr(tmp_path, prueba)

    assert s["borro"]
    assert _tarjetas(s["inicial"])["Resultado de los fijos"] == ("fin-rojo", s["usd"]["-100"])
    assert _tarjetas(s["trasBorrar"])["Total ingresos fijos"][1] == s["usd"]["0"]
    assert _tarjetas(s["trasBorrar"])["Resultado de los fijos"] == ("fin-rojo", s["usd"]["-300"])
    assert _tarjetas(s["trasGuardar"])["Resultado de los fijos"] == ("fin-verde", s["usd"]["150"])


# ── las trampas de DASHBOARD_HTML ────────────────────────────────────────────

def test_el_contenedor_esta_arriba_de_la_lista():
    vista = _entre(HTML, '<div id="fin-vista-fijos"', 'id="fin-fijos"')
    assert '<div class="fin-kpis" id="fin-fijos-totales"></div>' in vista


def test_el_js_nuevo_no_tiene_jinja_ni_barras_invertidas():
    for trampa in ("{#", "{{", "{%", chr(92)):
        assert trampa not in JS_NUEVO, repr(trampa)
    # Lo que llega al navegador es exactamente lo que está en el fuente.
    assert JS_NUEVO in HTML


def test_los_colores_salen_de_las_clases_con_tokens():
    assert re.search(r"^\.fin-verde\{color:var\(--verde\)\}", HTML, re.M)
    assert re.search(r"^\.fin-rojo\{color:var\(--rojo\)\}", HTML, re.M)
    cuenta = _entre(JS_NUEVO, "function _finKpisFijos(", "async function loadFijos(")
    assert not re.search("#[0-9a-fA-F]{3,6}(?![0-9a-zA-Z])", cuenta)


def test_la_pagina_principal_abre_con_los_totales(tmp_path, monkeypatch):
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
    assert b'id="fin-fijos-totales"' in r.data
    assert b"function _finTotalesFijos(" in r.data
