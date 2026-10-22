"""Finanzas → Ventas (Juan, 22/10).

"En Movimientos solo van a aparecer las cobranzas del mes": acá va lo vendido,
mes a mes. Los cierres salen solos (el primer paso a una etapa de cliente) y
se pueden cargar o corregir ventas a mano. Los mantenimientos no son venta.
"""

import json
import re
import shutil
import sqlite3
import subprocess

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services.finanzas import borrar_venta, guardar_venta, ventas_del_mes

HTML = dashboard.DASHBOARD_HTML


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "v.db")
    init_db(ruta)
    return ruta


def _sql(db, q, params=()):
    c = sqlite3.connect(db)
    try:
        cur = c.execute(q, params)
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def _negocio(db, nombre, estado):
    return _sql(db, "INSERT INTO businesses (name, crm_status) VALUES (?, ?)", (nombre, estado))


def _evento(db, bid, estado, cuando):
    _sql(db, "INSERT INTO lead_events (lead_id, new_status, note, created_at, created_by) "
             "VALUES (?, ?, '', ?, 'test')", (bid, estado, cuando))


def _ingreso(db, bid, monto, periodo="2026-09", categoria="desarrollo_web"):
    return _sql(db, "INSERT INTO finanzas_movimientos (tipo, fecha, periodo, concepto, categoria, "
                    "monto, moneda, monto_usd, client_id) VALUES ('ingreso', ?, ?, 'cobro', ?, ?, 'USD', ?, ?)",
                (periodo + "-10", periodo, categoria, monto, monto, bid))


def _pendiente(db, bid, monto, cobrado_mov=None):
    _sql(db, "INSERT INTO finanzas_por_cobrar (client_id, concepto, monto_usd, cobrado_movimiento_id) "
             "VALUES (?, 'saldo', ?, ?)", (bid, monto, cobrado_mov))


# ── el cálculo ───────────────────────────────────────────────────────────────

def test_un_cierre_cuenta_en_su_mes_con_lo_cobrado_mas_lo_pendiente(db):
    bid = _negocio(db, "Cliente A", "en_desarrollo")
    _evento(db, bid, "demo_1", "2026-08-20 15:00:00")
    _evento(db, bid, "cerrado", "2026-09-05 15:00:00")
    _evento(db, bid, "en_desarrollo", "2026-09-10 15:00:00")
    _ingreso(db, bid, 500)
    _pendiente(db, bid, 500)

    sep = ventas_del_mes(db, "2026-09")
    assert sep["cantidad"] == 1
    v = sep["ventas"][0]
    assert (v["origen"], v["cliente"], v["fecha"]) == ("cierre", "Cliente A", "2026-09-05")
    assert (v["monto_usd"], v["cobrado_usd"], v["pendiente_usd"]) == (1000, 500, 500)
    assert sep["total_usd"] == 1000
    # Ni el mes anterior ni el siguiente.
    assert ventas_del_mes(db, "2026-08")["cantidad"] == 0
    assert ventas_del_mes(db, "2026-10")["cantidad"] == 0


def test_volver_a_poner_cerrado_no_es_otra_venta(db):
    """Lo del 21/10: devolver a un cliente a su etapa deja un evento nuevo."""
    bid = _negocio(db, "Cliente A", "cerrado")
    _evento(db, bid, "cerrado", "2026-09-05 15:00:00")
    _evento(db, bid, "demo_agendada", "2026-10-21 16:00:00")
    _evento(db, bid, "cerrado", "2026-10-21 19:00:00")

    assert ventas_del_mes(db, "2026-09")["cantidad"] == 1
    assert ventas_del_mes(db, "2026-10")["cantidad"] == 0


def test_el_mes_es_el_de_montevideo_no_el_de_utc(db):
    bid = _negocio(db, "Cliente A", "cerrado")
    # 1/10 a la 1 de la mañana en UTC es el 30/9 a las 22 en Montevideo.
    _evento(db, bid, "cerrado", "2026-10-01 01:00:00")
    assert ventas_del_mes(db, "2026-09")["ventas"][0]["fecha"] == "2026-09-30"


def test_mantenimiento_aportes_y_lo_ya_saldado_no_suman(db):
    bid = _negocio(db, "Cliente A", "finalizado")
    _evento(db, bid, "cerrado", "2026-09-05 15:00:00")
    _ingreso(db, bid, 1000)
    _ingreso(db, bid, 50, categoria="mantenimiento")
    _ingreso(db, bid, 70, categoria="aporte")
    saldo = _ingreso(db, bid, 300)
    # Un pendiente ya cobrado está en el ingreso que lo saldó: no se cuenta dos veces.
    _pendiente(db, bid, 300, cobrado_mov=saldo)

    assert ventas_del_mes(db, "2026-09")["ventas"][0]["monto_usd"] == 1300


def test_un_lead_que_no_es_cliente_hoy_no_aparece(db):
    bid = _negocio(db, "Lead", "perdido")
    _evento(db, bid, "cerrado", "2026-09-05 15:00:00")
    assert ventas_del_mes(db, "2026-09")["cantidad"] == 0


def test_la_venta_a_mano_reemplaza_el_cierre_del_cliente(db):
    bid = _negocio(db, "Cliente A", "cerrado")
    _evento(db, bid, "cerrado", "2026-09-05 15:00:00")
    _ingreso(db, bid, 500)

    vid, error = guardar_venta(db, {"client_id": bid, "fecha": "2026-09-05",
                                    "monto": 2000, "moneda": "USD"}, "Juan")
    assert error is None
    sep = ventas_del_mes(db, "2026-09")
    assert sep["cantidad"] == 1
    v = sep["ventas"][0]
    assert (v["origen"], v["id"], v["monto_usd"], v["cobrado_usd"]) == ("manual", vid, 2000, 500)

    # Borrada, vuelve el cierre.
    assert borrar_venta(db, vid)
    assert ventas_del_mes(db, "2026-09")["ventas"][0]["origen"] == "cierre"


def test_venta_a_mano_sin_ficha_y_en_pesos(db):
    vid, error = guardar_venta(db, {"cliente": "Comercio sin ficha", "fecha": "2026-09-12",
                                    "monto": 40000, "moneda": "UYU", "tipo_cambio": 40,
                                    "concepto": "Página web"}, "Juan")
    assert error is None
    sep = ventas_del_mes(db, "2026-09")
    v = sep["ventas"][0]
    assert (v["cliente"], v["monto"], v["moneda"], v["monto_usd"]) == ("Comercio sin ficha", 40000, "UYU", 1000)
    assert v["cobrado_usd"] is None
    assert (sep["total_usd"], sep["total_uyu"]) == (1000, 40000)


@pytest.mark.parametrize("data, error", [
    ({"fecha": "2026-09-12", "monto": 10}, "elegí un cliente"),
    ({"cliente": "X", "fecha": "2026-02-30", "monto": 10}, "fecha"),
    ({"cliente": "X", "fecha": "2026-09-12", "monto": 0}, "mayor que cero"),
    ({"cliente": "X", "fecha": "2026-09-12", "monto": 10, "moneda": "UYU"}, "tipo"),
    ({"client_id": 99999, "fecha": "2026-09-12", "monto": 10}, "no existe"),
])
def test_validacion_de_la_venta_a_mano(db, data, error):
    vid, err = guardar_venta(db, data, "Juan")
    assert vid is None and error in err.lower()


# ── rutas y permisos ─────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "raiz@scalerics.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    ruta = str(tmp_path / "a.db")
    init_db(ruta)
    a = dashboard.create_app(ruta)
    a.config["_DB"] = ruta
    return a


def _cliente_http(app, email, paneles=None, solo_lectura=None):
    ruta = app.config["_DB"]
    uid = create_user(ruta, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    if paneles is not None:
        rid = _sql(ruta, "INSERT INTO roles (name, panel_access, paneles_solo_lectura) VALUES (?, ?, ?)",
                   ("Rol " + email, json.dumps(paneles), json.dumps(solo_lectura or [])))
        _sql(ruta, "UPDATE users SET role_id = ? WHERE id = ?", (rid, uid))
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "test"
    return c


def test_las_rutas_cargan_editan_y_borran(app):
    c = _cliente_http(app, "raiz@scalerics.com")
    r = c.post("/api/finanzas/ventas", json={"cliente": "Comercio", "fecha": "2026-09-12", "monto": 800})
    assert r.status_code == 201
    vid = r.get_json()["id"]

    d = c.get("/api/finanzas/ventas?periodo=2026-09").get_json()
    assert [v["monto_usd"] for v in d["ventas"]] == [800]

    assert c.put(f"/api/finanzas/ventas/{vid}",
                 json={"cliente": "Comercio", "fecha": "2026-10-01", "monto": 900}).status_code == 200
    assert c.get("/api/finanzas/ventas?periodo=2026-09").get_json()["cantidad"] == 0
    assert c.get("/api/finanzas/ventas?periodo=2026-10").get_json()["total_usd"] == 900

    assert c.delete(f"/api/finanzas/ventas/{vid}").status_code == 200
    assert c.delete(f"/api/finanzas/ventas/{vid}").status_code == 404
    assert c.get("/api/finanzas/ventas?periodo=2026-13").status_code == 400


def test_sin_finanzas_no_se_ven_las_ventas(app):
    c = _cliente_http(app, "caller@scalerics.com", paneles=["cola"])
    assert c.get("/api/finanzas/ventas?periodo=2026-09").status_code == 403


def test_en_solo_lectura_se_ven_pero_no_se_cargan(app):
    c = _cliente_http(app, "contador@scalerics.com", paneles=["finanzas"], solo_lectura=["finanzas"])
    assert c.get("/api/finanzas/ventas?periodo=2026-09").status_code == 200
    r = c.post("/api/finanzas/ventas", json={"cliente": "X", "fecha": "2026-09-12", "monto": 1})
    assert r.status_code == 403


# ── la pantalla ──────────────────────────────────────────────────────────────

def test_la_pestana_va_al_lado_de_movimientos_y_usa_las_flechas():
    assert HTML.index('id="fin-tab-movs"') < HTML.index('id="fin-tab-ventas"') < HTML.index('id="fin-tab-cobrar"')
    assert 'id="fin-vista-ventas"' in HTML and 'id="fin-venta-modal"' in HTML
    assert "const FIN_VISTAS = ['movimientos', 'ventas'," in HTML
    vista = re.search(r"function finVista\(cual\) \{.*?\n\}", HTML, re.S).group(0)
    assert "if (cual === 'ventas') loadVentas();" in vista
    # Cambiar de mes con las flechas recarga la pestaña.
    finanzas = re.search(r"async function loadFinanzas\(\) \{.*?\n\}", HTML, re.S).group(0)
    assert "if (_finVistaActual === 'ventas') loadVentas();" in finanzas
    # El navegador de meses se ve en Ventas aunque el rango diga "12 meses".
    nav = re.search(r"function _finPintarNavegador\(\) \{.*?\n\}", HTML, re.S).group(0)
    assert "_finVistaActual === 'ventas'" in nav
    nuevo = re.search(r"function _finBotonNuevo\(\) \{.*?\n\}", HTML, re.S).group(0)
    assert "abrirVenta()" in nuevo


@pytest.mark.skipif(shutil.which("node") is None, reason="node no esta instalado")
def test_dibuja_las_ventas_del_mes(tmp_path):
    """Corre el loadVentas real en node con un DOM y un fetch de mentira."""
    cuerpo = re.search(r"async function loadVentas\(\) \{.*?\n\}", HTML, re.S).group(0)
    monto = re.search(r"function _finMontoVenta\(v\) \{.*?\n\}", HTML, re.S).group(0)
    datos = {"periodo": "2026-09", "cantidad": 2, "total_usd": 1800, "promedio_usd": 900, "total_uyu": 0,
             "ventas": [
                 {"origen": "cierre", "id": None, "client_id": 1, "cliente": "Cliente A", "concepto": None,
                  "fecha": "2026-09-05", "monto": 1000, "moneda": "USD", "monto_usd": 1000,
                  "cobrado_usd": 500, "pendiente_usd": 500, "notas": None},
                 {"origen": "manual", "id": 7, "client_id": None, "cliente": "Comercio", "concepto": "Web",
                  "fecha": "2026-09-02", "monto": 800, "moneda": "USD", "monto_usd": 800,
                  "cobrado_usd": None, "pendiente_usd": None, "notas": None}]}
    script = """
const els = {};
const document = {getElementById: id => (els[id] = els[id] || {innerHTML: '', textContent: ''})};
const esc = s => String(s);
const _finUsd = n => 'US$ ' + n;
const _finSoloLectura = () => false;
const _finPeriodoVisible = () => '2026-09';
const _finNombreMes = p => 'setiembre 2026';
let _finVentas = [];
const fetch = async (url) => { console.error(url); return {ok: true, json: async () => (%s)}; };
%s
%s
loadVentas().then(() => console.log(JSON.stringify({
  tabla: els['fin-ventas'].innerHTML, tot: els['fin-ventas-totales'].innerHTML,
  titulo: els['fin-ventas-titulo'].textContent})));
""" % (json.dumps(datos), monto, cuerpo)
    archivo = tmp_path / "ventas.js"
    archivo.write_text(script, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert "/api/finanzas/ventas?periodo=2026-09" in r.stderr
    out = json.loads(r.stdout)
    assert out["titulo"] == "Ventas de setiembre 2026"
    assert "US$ 1800" in out["tot"] and ">2<" in out["tot"]
    assert "Cliente A" in out["tabla"] and "Comercio" in out["tabla"]
    # El cierre se corrige con una venta a mano; la a mano se edita o se borra.
    assert "abrirVenta(null, 0)" in out["tabla"]
    assert "abrirVenta(7)" in out["tabla"] and "borrarVenta(7)" in out["tabla"]
