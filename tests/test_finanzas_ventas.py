"""Finanzas → Ventas (Juan, 22/10).

"En Movimientos solo van a aparecer las cobranzas del mes": acá va lo vendido,
mes a mes. Los cierres salen solos (el primer paso a una etapa de cliente), sin
monto; se cargan, corrigen y eliminan ventas a mano. Es solo para analizar: no
se vincula con movimientos, por cobrar, fijos ni esenciales.
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
from services.finanzas import (borrar_cierre, borrar_venta, guardar_venta,
                               resumen, ventas_del_mes)

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

def test_un_cierre_cuenta_en_su_mes_sin_monto(db):
    bid = _negocio(db, "Cliente A", "en_desarrollo")
    _evento(db, bid, "demo_1", "2026-08-20 15:00:00")
    _evento(db, bid, "cerrado", "2026-09-05 15:00:00")
    _evento(db, bid, "en_desarrollo", "2026-09-10 15:00:00")

    sep = ventas_del_mes(db, "2026-09")
    assert (sep["cantidad"], sep["sin_monto"], sep["total_usd"]) == (1, 1, 0)
    assert sep["promedio_usd"] is None, "un cierre sin monto no vale cero"
    v = sep["ventas"][0]
    assert (v["origen"], v["cliente"], v["fecha"], v["monto_usd"]) == ("cierre", "Cliente A", "2026-09-05", None)
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


def test_ventas_no_lee_nada_de_finanzas(db):
    """Juan (22/10): "no se vincule con movimientos de finanzas ni fijos ni
    esenciales". Cobros y pendientes del cliente no cambian la venta."""
    bid = _negocio(db, "Cliente A", "finalizado")
    _evento(db, bid, "cerrado", "2026-09-05 15:00:00")
    _ingreso(db, bid, 1000)
    _pendiente(db, bid, 300)
    v = ventas_del_mes(db, "2026-09")["ventas"][0]
    assert v["monto_usd"] is None
    assert "cobrado_usd" not in v and "pendiente_usd" not in v


def test_ventas_no_escribe_nada_en_finanzas(db):
    """Y al revés: cargar una venta con IVA no aparece en Movimientos ni en el IVA."""
    antes = resumen(db, "2026-09", "2026-09")
    guardar_venta(db, {"cliente": "X", "fecha": "2026-09-12", "monto": 1000,
                       "facturado": True}, "Juan")
    despues = resumen(db, "2026-09", "2026-09")
    assert antes["kpis"] == despues["kpis"]
    c = sqlite3.connect(db)
    try:
        for tabla in ("finanzas_movimientos", "finanzas_por_cobrar", "finanzas_recurrentes",
                      "finanzas_esenciales"):
            assert c.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0] == 0, tabla
    finally:
        c.close()


@pytest.mark.parametrize("iva, neto, impuesto", [("no", 1000, 0), ("mas", 1000, 220),
                                                 ("incluido", 819.67, 180.33)])
def test_iva_de_la_venta(db, iva, neto, impuesto):
    guardar_venta(db, {"cliente": "X", "fecha": "2026-09-12", "monto": 1000,
                       "facturado": iva != "no", "iva_incluido": iva == "incluido"}, "Juan")
    sep = ventas_del_mes(db, "2026-09")
    v = sep["ventas"][0]
    assert (v["monto"], v["monto_usd"], v["iva_usd"]) == (1000, neto, impuesto)
    assert (sep["total_usd"], sep["iva_usd"], sep["total_con_iva_usd"]) == (neto, impuesto, round(neto + impuesto, 2))


def test_un_lead_que_no_es_cliente_hoy_no_aparece(db):
    bid = _negocio(db, "Lead", "perdido")
    _evento(db, bid, "cerrado", "2026-09-05 15:00:00")
    assert ventas_del_mes(db, "2026-09")["cantidad"] == 0


def test_la_venta_a_mano_reemplaza_el_cierre_del_cliente(db):
    bid = _negocio(db, "Cliente A", "cerrado")
    _evento(db, bid, "cerrado", "2026-09-05 15:00:00")

    vid, error = guardar_venta(db, {"client_id": bid, "fecha": "2026-09-05",
                                    "monto": 2000, "moneda": "USD"}, "Juan")
    assert error is None
    sep = ventas_del_mes(db, "2026-09")
    assert sep["cantidad"] == 1
    v = sep["ventas"][0]
    assert (v["origen"], v["id"], v["monto_usd"]) == ("manual", vid, 2000)

    # Eliminada, el cierre no vuelve: eliminar es eliminar.
    assert borrar_venta(db, vid)
    assert ventas_del_mes(db, "2026-09")["cantidad"] == 0


def test_eliminar_un_cierre_lo_saca_de_ventas_y_no_toca_al_cliente(db):
    bid = _negocio(db, "Cliente A", "cerrado")
    _evento(db, bid, "cerrado", "2026-09-05 15:00:00")
    otro = _negocio(db, "Cliente B", "cerrado")
    _evento(db, otro, "cerrado", "2026-09-06 15:00:00")

    assert borrar_cierre(db, bid, "Juan")
    assert [v["cliente"] for v in ventas_del_mes(db, "2026-09")["ventas"]] == ["Cliente B"]
    c = sqlite3.connect(db)
    try:
        assert c.execute("SELECT crm_status FROM businesses WHERE id = ?", (bid,)).fetchone()[0] == "cerrado"
    finally:
        c.close()
    # Un negocio sin cierre no tiene nada que eliminar.
    assert not borrar_cierre(db, _negocio(db, "Lead", "contactado"), "Juan")


def test_venta_a_mano_sin_ficha_y_en_pesos(db):
    vid, error = guardar_venta(db, {"cliente": "Comercio sin ficha", "fecha": "2026-09-12",
                                    "monto": 40000, "moneda": "UYU", "tipo_cambio": 40,
                                    "concepto": "Página web"}, "Juan")
    assert error is None
    sep = ventas_del_mes(db, "2026-09")
    v = sep["ventas"][0]
    assert (v["cliente"], v["monto"], v["moneda"], v["monto_usd"]) == ("Comercio sin ficha", 40000, "UYU", 1000)
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


def test_la_ruta_elimina_un_cierre(app):
    ruta = app.config["_DB"]
    bid = _negocio(ruta, "Cliente A", "cerrado")
    _evento(ruta, bid, "cerrado", "2026-09-05 15:00:00")
    c = _cliente_http(app, "raiz@scalerics.com")
    assert c.get("/api/finanzas/ventas?periodo=2026-09").get_json()["cantidad"] == 1
    assert c.delete(f"/api/finanzas/ventas/cierre/{bid}").status_code == 200
    assert c.get("/api/finanzas/ventas?periodo=2026-09").get_json()["cantidad"] == 0
    assert c.delete("/api/finanzas/ventas/cierre/99999").status_code == 404


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
    datos = {"periodo": "2026-09", "cantidad": 2, "sin_monto": 1, "total_usd": 800, "iva_usd": 176,
             "total_con_iva_usd": 976, "promedio_usd": 800, "total_uyu": 0,
             "ventas": [
                 {"origen": "cierre", "id": None, "client_id": 1, "cliente": "Cliente A", "concepto": None,
                  "fecha": "2026-09-05", "monto": None, "moneda": "USD", "monto_usd": None,
                  "facturado": 0, "iva_incluido": 0, "iva_usd": 0, "notas": None},
                 {"origen": "manual", "id": 7, "client_id": None, "cliente": "Comercio", "concepto": "Web",
                  "fecha": "2026-09-02", "monto": 800, "moneda": "USD", "monto_usd": 800,
                  "facturado": 1, "iva_incluido": 0, "iva_usd": 176, "notas": None}]}
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
    assert "US$ 800" in out["tot"] and "con IVA US$ 976" in out["tot"] and ">2<" in out["tot"]
    assert "1 sin monto" in out["tot"]
    assert "Cliente A" in out["tabla"] and "Comercio" in out["tabla"]
    assert "Sin monto" in out["tabla"] and "Más IVA" in out["tabla"]
    # Nada de Finanzas en la tabla.
    assert "Cobrado" not in out["tabla"] and "Falta cobrar" not in out["tabla"]
    # Todas se pueden eliminar: el cierre por su fila, la a mano por su id.
    assert "abrirVenta(null, 0)" in out["tabla"] and "borrarVenta(null, 0)" in out["tabla"]
    assert "abrirVenta(7)" in out["tabla"] and "borrarVenta(7)" in out["tabla"]
