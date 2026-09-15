"""El Balance de Finanzas: en blanco (contable) e interno (todo).

Pedido de Juan (15/9): "que toques generar balance hasta el momento y se genere
un balance ... una el supuesto balance en blanco osea con impuestos lo que se
contabiliza y otro que sea para nosotros con lo que esta en blanco y lo que no".

"En blanco" = `facturado` (el "¿Lleva IVA?" del alta) más los egresos de la
categoría `impuestos`, que no traen factura con IVA pero son contables por
definición. Ver el comentario de `BALANCE_TIPOS` en services/finanzas.py.
"""

import json
import re
import shutil
import sqlite3
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, crear_movimiento, crear_recurrente, init_db
from services.daily import hoy_montevideo
from services.finanzas import (balance, calcular_balance, es_en_blanco,
                               fecha_valida, materializar_recurrentes,
                               periodo_balance)

HTML = dashboard.DASHBOARD_HTML
SRC = (Path(__file__).resolve().parents[1] / "dashboard.py").read_text(encoding="utf-8")

sin_node = pytest.mark.skipif(shutil.which("node") is None,
                              reason="node no esta instalado")


def _m(mid, tipo, fecha, usd, categoria="otros", facturado=0, iva=None,
       concepto=None, anulado=0, recurrente_id=None):
    return {"id": mid, "tipo": tipo, "fecha": fecha, "periodo": fecha[:7],
            "concepto": concepto or f"Mov {mid}", "categoria": categoria,
            "monto": usd, "moneda": "USD", "tipo_cambio": None, "monto_usd": usd,
            "facturado": facturado,
            "iva_usd": (iva if iva is not None
                        else (round((usd or 0) * 0.22, 2) if facturado else 0)),
            "anulado": anulado, "recurrente_id": recurrente_id, "client_id": None}


def _mezcla():
    return [
        _m(1, "ingreso", "2026-03-10", 1000, "desarrollo_web", facturado=1),   # iva 220
        _m(2, "ingreso", "2026-03-12", 500, "mantenimiento"),                  # no facturado
        _m(3, "egreso", "2026-04-02", 200, "infraestructura", facturado=1),    # iva 44
        _m(4, "egreso", "2026-04-05", 100, "herramientas"),                    # no facturado
        _m(5, "egreso", "2026-04-20", 50, "impuestos", concepto="IRAE"),       # contable
    ]


# ── qué es en blanco ─────────────────────────────────────────────────────────

def test_en_blanco_es_lo_facturado_y_los_impuestos():
    assert es_en_blanco({"tipo": "ingreso", "categoria": "otros", "facturado": 1})
    assert not es_en_blanco({"tipo": "ingreso", "categoria": "otros", "facturado": 0})
    assert es_en_blanco({"tipo": "egreso", "categoria": "impuestos", "facturado": 0})
    assert not es_en_blanco({"tipo": "egreso", "categoria": "retiros", "facturado": 0})


# ── la cuenta ────────────────────────────────────────────────────────────────

def test_en_blanco_solo_cuenta_lo_contable_con_su_iva():
    b = calcular_balance(_mezcla(), "blanco", "2026-01-01", "2026-12-31")

    assert b["tipo_nombre"] == "En blanco (contable)"
    assert b["ingresos"]["total"] == 1000
    assert b["egresos"]["total"] == 250                  # 200 facturado + 50 impuestos
    assert b["resultado"]["total"] == 750
    assert b["iva"] == {"ventas": 220, "compras": 44, "saldo": 176}
    assert b["con_iva"] == {"ingresos": 1220, "egresos": 294, "resultado": 926}
    assert b["impuestos"] == {"total": 50, "por_concepto": [{"concepto": "IRAE", "total": 50}]}
    assert [c["categoria"] for c in b["ingresos"]["por_categoria"]] == ["desarrollo_web"]
    assert b["ingresos"]["no_facturado"] == 0 and b["egresos"]["no_facturado"] == 0
    assert b["movimientos"] == 3


def test_interno_cuenta_todo_con_el_desglose_blanco_y_no():
    b = calcular_balance(_mezcla(), "interno", "2026-01-01", "2026-12-31")

    assert (b["ingresos"]["total"], b["ingresos"]["blanco"], b["ingresos"]["no_facturado"]) \
        == (1500, 1000, 500)
    assert (b["egresos"]["total"], b["egresos"]["blanco"], b["egresos"]["no_facturado"]) \
        == (350, 250, 100)
    assert b["resultado"] == {"total": 1150, "blanco": 750, "no_facturado": 400}
    # El IVA es el mismo: solo lo facturado lo lleva.
    assert b["iva"] == {"ventas": 220, "compras": 44, "saldo": 176}
    assert b["con_iva"]["ingresos"] == 1720 and b["con_iva"]["egresos"] == 394
    cats = {c["categoria"]: c for c in b["egresos"]["por_categoria"]}
    assert cats["herramientas"] == {"categoria": "herramientas", "total": 100,
                                    "blanco": 0, "no_facturado": 100}
    assert cats["impuestos"]["blanco"] == 50
    assert b["movimientos"] == 5


def test_saldo_de_iva_a_favor_es_negativo():
    movs = [_m(1, "ingreso", "2026-05-01", 100, facturado=1),
            _m(2, "egreso", "2026-05-02", 1000, facturado=1)]
    b = calcular_balance(movs, "blanco", "2026-01-01", "2026-12-31")
    assert b["iva"]["saldo"] == -198
    assert b["resultado"]["total"] == -900


def test_los_anulados_no_cuentan():
    movs = [_m(1, "egreso", "2026-05-01", 100), _m(2, "egreso", "2026-05-01", 900, anulado=1)]
    assert calcular_balance(movs, "interno", "2026-01-01", "2026-12-31")["egresos"]["total"] == 100


def test_los_sin_cotizacion_quedan_afuera_y_se_cuentan():
    movs = [_m(1, "ingreso", "2026-05-01", 100, facturado=1),
            _m(2, "egreso", "2026-05-01", None, facturado=1),
            _m(3, "egreso", "2026-05-01", None)]                  # no facturado
    blanco = calcular_balance(movs, "blanco", "2026-01-01", "2026-12-31")
    interno = calcular_balance(movs, "interno", "2026-01-01", "2026-12-31")

    assert blanco["sin_cotizacion"] == 1      # el no facturado no es de este balance
    assert interno["sin_cotizacion"] == 2
    assert interno["egresos"]["total"] == 0 and interno["movimientos"] == 1


def test_bordes_de_fecha_inclusivos():
    movs = [_m(1, "ingreso", "2026-02-28", 1), _m(2, "ingreso", "2026-03-01", 10),
            _m(3, "ingreso", "2026-03-31", 100), _m(4, "ingreso", "2026-04-01", 1000)]
    b = calcular_balance(movs, "interno", "2026-03-01", "2026-03-31")
    assert b["ingresos"]["total"] == 110


def test_mes_a_mes_con_los_meses_vacios_en_cero():
    movs = [_m(1, "ingreso", "2026-01-20", 300), _m(2, "egreso", "2026-01-25", 100),
            _m(3, "egreso", "2026-03-05", 400), _m(4, "ingreso", "2026-03-15", 999)]
    b = calcular_balance(movs, "interno", "2026-01-15", "2026-03-10")

    assert b["meses"] == [
        {"periodo": "2026-01", "ingresos": 300, "egresos": 100, "resultado": 200},
        {"periodo": "2026-02", "ingresos": 0, "egresos": 0, "resultado": 0},
        {"periodo": "2026-03", "ingresos": 0, "egresos": 400, "resultado": -400},
    ]


def test_redondea_a_centavos():
    movs = [_m(i, "ingreso", "2026-05-01", 0.1) for i in range(3)]
    b = calcular_balance(movs, "interno", "2026-01-01", "2026-12-31")
    assert b["ingresos"]["total"] == 0.3
    assert str(b["egresos"]["total"]) == "0.0"


@pytest.mark.parametrize("tipo,desde,hasta", [
    ("otro", "2026-01-01", "2026-12-31"),
    ("blanco", "2026-13-01", "2026-12-31"),
    ("blanco", "2026-02-30", "2026-12-31"),
    ("blanco", "2026-12-31", "2026-01-01"),
])
def test_entradas_invalidas_revientan(tipo, desde, hasta):
    with pytest.raises(ValueError):
        calcular_balance([], tipo, desde, hasta)


def test_fecha_valida():
    assert fecha_valida("2026-09-15")
    for mala in ("2026-9-15", "15/09/2026", "2026-02-30", "", None, "inicio"):
        assert not fecha_valida(mala)


# ── el período y Montevideo ──────────────────────────────────────────────────

def test_periodo_por_defecto_es_este_anio_hasta_hoy():
    assert periodo_balance("", "", date(2026, 9, 15)) == ("2026-01-01", "2026-09-15")


def test_desde_el_inicio_toma_el_primer_movimiento():
    assert periodo_balance("inicio", "", date(2026, 9, 15), "2025-11-03") \
        == ("2025-11-03", "2026-09-15")
    # Sin movimientos, el 1 de enero.
    assert periodo_balance("inicio", "", date(2026, 9, 15), None) == ("2026-01-01", "2026-09-15")


def test_personalizado_respeta_las_fechas():
    assert periodo_balance("2026-02-01", "2026-02-28", date(2026, 9, 15)) \
        == ("2026-02-01", "2026-02-28")


def test_a_las_23_de_montevideo_del_31_de_diciembre_sigue_siendo_ese_anio():
    """El servidor corre en UTC: a las 2 de la mañana UTC del 1 de enero, en
    Montevideo son las 23 del 31 de diciembre. El balance "de este año" tiene
    que ser el que termina, no uno vacío del año nuevo."""
    ahora = datetime(2027, 1, 1, 2, 0, tzinfo=timezone.utc)
    hoy = hoy_montevideo(ahora)
    assert hoy == date(2026, 12, 31)
    assert periodo_balance("", "", hoy) == ("2026-01-01", "2026-12-31")
    # Tres horas después ya es el año nuevo también en Montevideo.
    assert periodo_balance("", "", hoy_montevideo(datetime(2027, 1, 1, 3, 0, tzinfo=timezone.utc))) \
        == ("2027-01-01", "2027-01-01")


# ── con base: fijos sin doble conteo ─────────────────────────────────────────

def test_los_fijos_materializados_cuentan_una_sola_vez(tmp_path):
    db = str(tmp_path / "b.db")
    init_db(db)
    crear_recurrente(db, tipo="egreso", concepto="Fly.io", categoria="infraestructura",
                     monto=30, moneda="USD", desde="2026-01", dia_del_mes=5, facturado=1)
    hoy = date(2026, 3, 20)
    materializar_recurrentes(db, hoy=hoy)
    materializar_recurrentes(db, hoy=hoy)          # idempotente
    crear_movimiento(db, tipo="ingreso", fecha="2026-02-10", periodo="2026-02",
                     concepto="Web", categoria="desarrollo_web", monto=500,
                     moneda="USD", monto_usd=500)

    b = balance(db, "interno", "2026-01-01", "2026-03-20")

    assert b["egresos"]["total"] == 90             # 3 meses x 30, no 180
    assert b["egresos"]["blanco"] == 90
    assert b["iva"]["compras"] == pytest.approx(19.8)
    assert b["ingresos"]["no_facturado"] == 500
    assert [m["egresos"] for m in b["meses"]] == [30, 30, 30]


def test_un_fijo_de_despues_de_hoy_no_entra_hasta_el_momento(tmp_path):
    db = str(tmp_path / "b.db")
    init_db(db)
    crear_recurrente(db, tipo="egreso", concepto="Zoho", categoria="herramientas",
                     monto=10, moneda="USD", desde="2026-03", dia_del_mes=25)
    materializar_recurrentes(db, hoy=date(2026, 3, 20))
    assert balance(db, "interno", "2026-01-01", "2026-03-20")["egresos"]["total"] == 0
    assert balance(db, "interno", "2026-01-01", "2026-03-31")["egresos"]["total"] == 10


# ── la ruta ──────────────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "raiz@scalerics.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    db = str(tmp_path / "a.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


def _rol(db, nombre, paneles):
    conn = sqlite3.connect(db)
    try:
        fila = conn.execute("SELECT id FROM roles WHERE name=?", (nombre,)).fetchone()
        if fila:
            conn.execute("UPDATE roles SET panel_access=? WHERE id=?",
                         (json.dumps(paneles), fila[0]))
            conn.commit()
            return fila[0]
        rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)",
                           (nombre, json.dumps(paneles))).lastrowid
        conn.commit()
        return rid
    finally:
        conn.close()


def _cliente(app, nombre, paneles):
    db = app.config["_DB"]
    uid = create_user(db, name=nombre, email=f"{nombre}@scalerics.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    conn = sqlite3.connect(db)
    conn.execute("UPDATE users SET role_id=? WHERE id=?",
                 (_rol(db, nombre.capitalize(), paneles), uid))
    conn.commit()
    conn.close()
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = nombre
    return c


@pytest.fixture
def cli(app):
    return _cliente(app, "socio", ["finanzas"])


def _hoy():
    return hoy_montevideo()


def test_la_ruta_devuelve_el_balance_de_este_anio(app, cli):
    db = app.config["_DB"]
    hoy = _hoy()
    crear_movimiento(db, tipo="ingreso", fecha=hoy.isoformat(), periodo=hoy.isoformat()[:7],
                     concepto="Cobro", categoria="desarrollo_web", monto=1000,
                     moneda="USD", monto_usd=1000, facturado=1, iva_usd=220)
    crear_movimiento(db, tipo="ingreso", fecha=hoy.isoformat(), periodo=hoy.isoformat()[:7],
                     concepto="Cobro en negro", categoria="otros", monto=300,
                     moneda="USD", monto_usd=300)
    crear_movimiento(db, tipo="ingreso", fecha=f"{hoy.year - 1}-12-31",
                     periodo=f"{hoy.year - 1}-12", concepto="Año pasado",
                     categoria="otros", monto=5, moneda="USD", monto_usd=5)

    r = cli.get("/api/finanzas/balance?tipo=blanco")
    d = r.get_json()
    assert r.status_code == 200
    assert (d["desde"], d["hasta"]) == (f"{hoy.year}-01-01", hoy.isoformat())
    assert d["ingresos"]["total"] == 1000 and d["iva"]["ventas"] == 220
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", d["generado_en"])

    d = cli.get("/api/finanzas/balance?tipo=interno").get_json()
    assert d["ingresos"] == {**d["ingresos"], "total": 1300, "blanco": 1000, "no_facturado": 300}

    d = cli.get("/api/finanzas/balance?tipo=interno&desde=inicio").get_json()
    assert d["desde"] == f"{hoy.year - 1}-12-31" and d["ingresos"]["total"] == 1305

    d = cli.get(f"/api/finanzas/balance?tipo=interno&desde={hoy.year - 1}-12-01"
                f"&hasta={hoy.year - 1}-12-31").get_json()
    assert d["ingresos"]["total"] == 5


def test_la_ruta_usa_el_dia_de_montevideo(app, cli, monkeypatch):
    import services.daily
    monkeypatch.setattr(services.daily, "hoy_montevideo", lambda: date(2026, 12, 31))
    d = cli.get("/api/finanzas/balance?tipo=blanco").get_json()
    assert (d["desde"], d["hasta"]) == ("2026-01-01", "2026-12-31")


@pytest.mark.parametrize("query", [
    "", "tipo=", "tipo=negro", "tipo=blanco&desde=2026-13-01",
    "tipo=blanco&hasta=hoy", "tipo=blanco&desde=2026/01/01",
    "tipo=blanco&desde=2026-05-01&hasta=2026-04-01", "tipo=blanco&hasta=inicio",
])
def test_la_ruta_valida(cli, query):
    r = cli.get("/api/finanzas/balance?" + query)
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_la_ruta_esta_detras_del_panel_finanzas(app):
    caller = _cliente(app, "caller", ["cola"])
    assert caller.get("/api/finanzas/balance?tipo=blanco").status_code == 403


def test_sin_sesion_no_entra(app):
    assert app.test_client().get("/api/finanzas/balance?tipo=blanco").status_code in (401, 403, 302)


def test_la_pagina_principal_abre_con_el_balance(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    db = str(tmp_path / "render.db")
    init_db(db)
    app = dashboard.create_app(db)
    uid = create_user(db, name="Jefe", email="jefe@test.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Jefe"

    r = c.get("/")

    assert r.status_code == 200
    assert b'id="fin-tab-balance"' in r.data
    assert b'id="fin-vista-balance"' in r.data
    assert "Generar balance hasta el momento".encode() in r.data
    assert b"function _finBalPintar(" in r.data
    assert b"@media print{" in r.data


# ── las trampas de DASHBOARD_HTML ────────────────────────────────────────────

def _entre(texto, desde, hasta):
    i = texto.index(desde)
    return texto[i:texto.index(hasta, i + len(desde))]


JS_BALANCE = _entre(SRC, "// ========== Finanzas: Balance ==========", "function _finMesActual(")
CSS_BALANCE = _entre(SRC, "/* ── Finanzas: Balance", "</style>")


def test_el_js_y_el_css_nuevos_no_tienen_jinja_ni_barras_invertidas():
    for bloque in (JS_BALANCE, CSS_BALANCE[:CSS_BALANCE.index("body.fb-imprimiendo .fb-seccion")]):
        for trampa in ("{#", "{{", "{%", chr(92)):
            assert trampa not in bloque, repr(trampa)
    assert JS_BALANCE in HTML


def test_el_css_del_balance_usa_tokens():
    reglas = re.findall(r"^\.fb-[^{\n]*\{[^}]*\}", HTML, re.M)
    assert len(reglas) >= 10
    for regla in reglas:
        assert not re.search("#[0-9a-fA-F]{3,6}(?![0-9a-zA-Z])", regla), regla


def test_el_contenedor_de_impresion_es_hijo_del_body():
    assert '<body>\n<div class="fb-print" id="fb-print"></div>' in HTML


# ── lo que se ve (node) ──────────────────────────────────────────────────────

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
const _clasesBody = new Set();
globalThis.document = {
  getElementById: _el, querySelectorAll: () => [], querySelector: () => null,
  body: { classList: { contains: c => _clasesBody.has(c), add: c => _clasesBody.add(c),
                       remove: c => _clasesBody.delete(c), toggle(){} } },
  createElement: () => _el('tmp'), addEventListener(){},
};
globalThis.window = globalThis;
const _oyentes = {};
globalThis.addEventListener = (ev, fn) => { _oyentes[ev] = fn; };
globalThis.removeEventListener = (ev) => { delete _oyentes[ev]; };
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
  if (r && r._status) return Promise.resolve({ ok: false, status: r._status, json: () => Promise.resolve(r) });
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(r === undefined ? {} : r) });
};
"""


def _correr(tmp_path, prueba):
    bloques = re.findall(r"<script>(.*?)</script>", HTML, re.S)
    archivo = tmp_path / "balance.js"
    archivo.write_text(_ARNES + "\n".join(bloques) + "\n" + prueba, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, (r.stderr or "")[:2000]
    return json.loads(r.stdout.strip().splitlines()[-1])


def _pintar(tmp_path, datos, valores=()):
    prueba = """
const usd = {};
__VALORES__.forEach(v => { usd[String(v)] = _finUsd(v); });
console.log(JSON.stringify({html: _finBalPintar(__DATOS__), usd}));
""".replace("__DATOS__", json.dumps(datos)).replace("__VALORES__", json.dumps(list(valores)))
    return _correr(tmp_path, prueba)


def _kpis(html):
    return {rotulo: (clases, valor) for rotulo, clases, valor in re.findall(
        r'<div class="fin-kpi-label">([^<]*)</div>'
        r'<div class="fin-kpi-valor ([^"]*)">([^<]*)</div>', html)}


@sin_node
def test_pinta_el_balance_en_blanco(tmp_path):
    d = calcular_balance(_mezcla(), "blanco", "2026-01-01", "2026-09-15",
                         generado_en="2026-09-15 10:32")
    s = _pintar(tmp_path, d, valores=(1000, 250, 750, 220, 44, 176, 50))
    html = s["html"]

    assert "Balance · En blanco (contable)" in html
    assert "del 01/01/2026 al 15/09/2026" in html
    assert "Generado el 15/09/2026 a las 10:32" in html
    k = _kpis(html)
    assert k["Ingresos"] == ("fin-verde", s["usd"]["1000"])
    assert k["Egresos"] == ("fin-rojo", s["usd"]["250"])
    assert k["Resultado"] == ("fin-verde", s["usd"]["750"])
    for bloque in ("Ingresos por categoría", "Egresos por categoría",
                   "IVA ventas (débito)", "IVA compras (crédito)",
                   "Saldo de IVA (a pagar)", "Impuestos", "IRAE", "Evolución mes a mes",
                   "Desarrollo web"):
        assert bloque in html, bloque
    assert s["usd"]["176"] in html
    # En el de blanco no se desglosa ni hay columna de no facturado.
    assert "No facturado</th>" not in html
    assert "en blanco +" not in html
    assert "sin tipo de cambio" not in html


@sin_node
def test_pinta_el_interno_con_cuanto_es_en_blanco_y_cuanto_no(tmp_path):
    d = calcular_balance(_mezcla(), "interno", "2026-01-01", "2026-09-15")
    s = _pintar(tmp_path, d, valores=(1500, 1000, 500))
    html = s["html"]

    assert "Balance · Interno (todo)" in html
    assert (s["usd"]["1500"] + " = " + s["usd"]["1000"] + " en blanco + "
            + s["usd"]["500"] + " no facturado") in html
    assert "<th class=\"fb-num\">En blanco</th><th class=\"fb-num\">No facturado</th>" in html


@sin_node
def test_resultado_negativo_en_rojo_y_meses(tmp_path):
    movs = [_m(1, "egreso", "2026-02-01", 700), _m(2, "ingreso", "2026-03-01", 200)]
    d = calcular_balance(movs, "interno", "2026-02-01", "2026-03-31")
    s = _pintar(tmp_path, d, valores=(-500, -700, 200))
    html = s["html"]

    assert _kpis(html)["Resultado"] == ("fin-rojo", s["usd"]["-500"])
    assert "Febrero 2026" in html and "Marzo 2026" in html
    assert ('<td class="fb-num fin-rojo">' + s["usd"]["-700"] + "</td>") in html
    assert ('<td class="fb-num fin-verde">' + s["usd"]["200"] + "</td>") in html
    assert "No hay egresos en la categoría Impuestos" in html


@sin_node
def test_avisa_los_movimientos_sin_tipo_de_cambio(tmp_path):
    movs = [_m(1, "egreso", "2026-02-01", None), _m(2, "egreso", "2026-02-01", None)]
    d = calcular_balance(movs, "interno", "2026-01-01", "2026-03-31")
    assert "2 movimientos sin tipo de cambio no se incluyen" in _pintar(tmp_path, d)["html"]


@sin_node
def test_generar_pide_la_ruta_y_pinta(tmp_path):
    d = calcular_balance(_mezcla(), "interno", "2026-02-01", "2026-04-30")
    prueba = """
(async () => {
  _el('fb-tipo').value = 'interno';
  _el('fb-preset').value = 'personalizado';
  _el('fb-desde').value = '2026-02-01';
  _el('fb-hasta').value = '2026-04-30';
  _RESP['/api/finanzas/balance?tipo=interno&desde=2026-02-01&hasta=2026-04-30'] = %s;
  await finBalGenerar();
  const personalizado = {html: _el('fin-balance').innerHTML, imprimir: _el('fb-imprimir').style.display};

  _el('fb-tipo').value = 'blanco';
  _el('fb-preset').value = 'inicio';
  _RESP['/api/finanzas/balance?tipo=blanco&desde=inicio'] = {_status: 400, error: 'desde tiene que ser <= hasta'};
  await finBalGenerar();
  const error = {html: _el('fin-balance').innerHTML, imprimir: _el('fb-imprimir').style.display};

  _el('fb-preset').value = 'anio';
  await finBalGenerar();   // respuesta vacía: muestra error, no revienta
  const rara = _el('fin-balance').innerHTML;
  console.log(JSON.stringify({personalizado, error, rara, pedidos: _pedidos.map(p => p[0])}));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""" % json.dumps(d)
    s = _correr(tmp_path, prueba)

    assert "Balance · Interno (todo)" in s["personalizado"]["html"]
    assert s["personalizado"]["imprimir"] == ""
    assert "desde tiene que ser &lt;= hasta" in s["error"]["html"]
    assert s["error"]["imprimir"] == "none"
    assert s["pedidos"][-1] == "/api/finanzas/balance?tipo=blanco"
    assert "Error" in s["rara"]


@sin_node
def test_el_preset_personalizado_muestra_las_fechas_con_valores(tmp_path):
    prueba = """
_el('fb-preset').value = 'personalizado';
finBalPreset();
const antes = {display: _el('fb-fechas').style.display, desde: _el('fb-desde').value, hasta: _el('fb-hasta').value};
_el('fb-preset').value = 'anio';
finBalPreset();
console.log(JSON.stringify({antes, despues: _el('fb-fechas').style.display, hoy: _finBalHoy()}));
"""
    s = _correr(tmp_path, prueba)
    assert s["antes"]["display"] == "" and s["despues"] == "none"
    assert s["antes"]["hasta"] == s["hoy"]
    assert s["antes"]["desde"] == s["hoy"][:4] + "-01-01"


@sin_node
def test_imprimir_copia_el_balance_y_lo_pone_en_claro(tmp_path):
    d = calcular_balance(_mezcla(), "blanco", "2026-01-01", "2026-09-15")
    prueba = """
_finBalUltimo = %s;
let durante = null;
globalThis.print = () => {
  durante = {clases: Array.from(_clasesBody).sort(), hoja: _el('fb-print').innerHTML.length};
};
finBalImprimir();
_oyentes.afterprint();
console.log(JSON.stringify({durante, despues: Array.from(_clasesBody), hoja: _el('fb-print').innerHTML}));
""" % json.dumps(d)
    s = _correr(tmp_path, prueba)
    assert s["durante"]["clases"] == ["fb-imprimiendo", "light"]
    assert s["durante"]["hoja"] > 500
    assert s["despues"] == [] and s["hoja"] == ""


@sin_node
def test_la_pestania_balance_se_muestra_y_esconde_el_rango(tmp_path):
    prueba = """
finVista('balance');
console.log(JSON.stringify({balance: _el('fin-vista-balance').style.display,
  movs: _el('fin-vista-movimientos').style.display, rango: _el('fin-rango').style.display}));
"""
    s = _correr(tmp_path, prueba)
    assert s == {"balance": "", "movs": "none", "rango": "none"}
