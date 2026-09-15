"""Balance General de Finanzas: Activo = Pasivo + Patrimonio a una fecha.

Juan rechazó el estado de resultados como "balance" (15/9): "Un balance desde
que tengo uso de la razon es asi", con la imagen de un Balance General clásico.

Automático: Caja y Bancos (movimientos CON IVA + saldo inicial cargado),
Cuentas por cobrar (interno), IVA crédito / IVA a pagar, Utilidad del
ejercicio, Resultados de ejercicios anteriores. Manual: bienes, deudas,
capital y saldo inicial de caja (`finanzas_balance_datos`). Si no cuadra, NO se
fuerza: aparece "Diferencia a revisar (patrimonio no explicado)".
"""

import json
import re
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import (create_user, crear_dato_balance, crear_movimiento,
                      crear_por_cobrar, get_dato_balance, init_db)
from services.daily import hoy_montevideo
from services.finanzas import (BALANCE_CLASES, balance_general,
                               calcular_balance_general, dato_vigente,
                               empresa_nombre, fecha_mvd_de_timestamp,
                               pendientes_para_balance, saldar_por_cobrar)
from tests.test_finanzas_balance import _correr, _m, sin_node

HTML = dashboard.DASHBOARD_HTML
SRC = (Path(__file__).resolve().parents[1] / "dashboard.py").read_text(encoding="utf-8")


def _d(clase, rubro, monto, desde="2026-01-01", hasta=None, en_blanco=1, nombre=None):
    return {"clase": clase, "rubro": rubro, "nombre": nombre or rubro, "monto_usd": monto,
            "desde": desde, "hasta": hasta, "en_blanco": en_blanco}


def _movs():
    return [
        _m(1, "ingreso", "2026-03-10", 1000, "desarrollo_web", facturado=1),   # +1220 caja, iva 220
        _m(2, "egreso", "2026-04-02", 200, "infraestructura", facturado=1),    # -244 caja, iva 44
        _m(3, "egreso", "2026-04-05", 100, "herramientas"),                    # -100, no facturado
    ]


def _claves(seccion):
    return [f["clave"] for f in seccion["filas"]]


def _monto(seccion, clave):
    return next(f["monto"] for f in seccion["filas"] if f["clave"] == clave)


# ── la ecuación contable ─────────────────────────────────────────────────────

def test_cuadra_exacto_con_saldo_inicial_bienes_deudas_y_capital():
    datos = [_d("caja_inicial", "caja_inicial", 300), _d("activo", "maquinarias", 500),
             _d("pasivo", "prestamos", 300), _d("capital", "capital", 500)]
    bg = calcular_balance_general(_movs(), [], datos, "interno", "2026-09-15")

    # Caja = 300 + 1220 - 244 - 100 = 1176 ; IVA a pagar = 220 - 44 = 176
    # Utilidad = 1000 - 200 - 100 = 700
    assert _claves(bg["activo"]) == ["caja", "activo:maquinarias"]
    assert _monto(bg["activo"], "caja") == 1176
    assert bg["activo"]["total"] == 1676
    assert _claves(bg["pasivo"]) == ["iva_a_pagar", "pasivo:prestamos"]
    assert bg["pasivo"]["total"] == 476
    assert _claves(bg["patrimonio"]) == ["capital", "utilidad"]
    assert _monto(bg["patrimonio"], "utilidad") == 700
    assert bg["patrimonio"]["total"] == 1200
    assert bg["total_pasivo_patrimonio"] == bg["activo"]["total"] == 1676
    assert bg["cuadra"] is True and bg["diferencia"] == 0


def test_sin_capital_aparece_la_diferencia_con_el_monto_correcto():
    datos = [_d("caja_inicial", "caja_inicial", 300), _d("activo", "maquinarias", 500),
             _d("pasivo", "prestamos", 300)]
    bg = calcular_balance_general(_movs(), [], datos, "interno", "2026-09-15")

    assert bg["cuadra"] is False
    assert bg["diferencia"] == 500
    ultima = bg["patrimonio"]["filas"][-1]
    assert ultima["clave"] == "diferencia" and ultima["alerta"] is True
    assert ultima["nombre"] == "Diferencia a revisar (patrimonio no explicado)"
    assert ultima["monto"] == 500
    # La ecuación se ve igual: la diferencia es parte del patrimonio, marcada.
    assert bg["total_pasivo_patrimonio"] == bg["activo"]["total"] == 1676


def test_una_deuda_sin_contrapartida_da_diferencia_negativa():
    bg = calcular_balance_general(_movs(), [], [_d("pasivo", "sueldos", 80)], "interno", "2026-09-15")
    assert bg["diferencia"] == -80
    assert bg["total_pasivo_patrimonio"] == bg["activo"]["total"]


@pytest.mark.parametrize("tipo", ["blanco", "interno"])
def test_solo_con_movimientos_cuadra_siempre(tipo):
    movs = _movs() + [
        _m(10, "ingreso", "2025-06-01", 700, facturado=1),
        _m(11, "egreso", "2025-07-01", 3000, facturado=1),               # IVA a favor
        _m(12, "egreso", "2026-05-01", 50, "impuestos", concepto="BPS"),
        _m(13, "ingreso", "2026-08-01", 0.1),
        _m(14, "ingreso", "2026-08-02", 0.2),
    ]
    pendientes = [{"monto_usd": 333.33, "desde": "2026-02-01", "cobrado_fecha": None}]
    bg = calcular_balance_general(movs, pendientes, [], tipo, "2026-09-15")
    assert bg["cuadra"], bg
    assert bg["activo"]["total"] == bg["total_pasivo_patrimonio"]


# ── rubros automáticos ───────────────────────────────────────────────────────

def test_la_caja_llega_hasta_la_fecha_de_corte():
    movs = [_m(1, "ingreso", "2026-06-30", 100), _m(2, "ingreso", "2026-07-01", 999)]
    en_el_dia = calcular_balance_general(movs, [], [], "interno", "2026-06-30")
    despues = calcular_balance_general(movs, [], [], "interno", "2026-07-01")
    assert _monto(en_el_dia["activo"], "caja") == 100
    assert _monto(despues["activo"], "caja") == 1099


def test_los_anulados_no_mueven_la_caja():
    movs = [_m(1, "ingreso", "2026-06-01", 100), _m(2, "egreso", "2026-06-01", 50, anulado=1)]
    assert _monto(calcular_balance_general(movs, [], [], "interno", "2026-09-15")["activo"], "caja") == 100


def test_cuentas_por_cobrar_a_la_fecha():
    pendientes = [
        {"monto_usd": 400, "desde": "2026-05-01", "cobrado_fecha": None},
        {"monto_usd": 250, "desde": "2026-05-01", "cobrado_fecha": "2026-06-01"},
        {"monto_usd": 100, "desde": "2026-07-01", "cobrado_fecha": None},
    ]
    junio = calcular_balance_general([], pendientes, [], "interno", "2026-06-15")
    mayo = calcular_balance_general([], pendientes, [], "interno", "2026-05-20")
    antes = calcular_balance_general([], pendientes, [], "interno", "2026-04-30")

    assert _monto(junio["activo"], "por_cobrar") == 400
    assert _monto(mayo["activo"], "por_cobrar") == 650
    assert "por_cobrar" not in _claves(antes["activo"])
    # Su contrapartida: la venta acordada que todavía no es ingreso.
    assert _monto(junio["patrimonio"], "ventas_por_cobrar") == 400
    assert junio["cuadra"]


def test_en_blanco_no_lleva_cuentas_por_cobrar():
    """La factura se decide al cobrar: un pendiente todavía no está facturado."""
    pendientes = [{"monto_usd": 400, "desde": "2026-05-01", "cobrado_fecha": None}]
    bg = calcular_balance_general([], pendientes, [], "blanco", "2026-06-15")
    assert "por_cobrar" not in _claves(bg["activo"])
    assert "ventas_por_cobrar" not in _claves(bg["patrimonio"])


def test_iva_a_pagar_va_al_pasivo():
    bg = calcular_balance_general([_m(1, "ingreso", "2026-05-01", 1000, facturado=1),
                                   _m(2, "egreso", "2026-05-02", 100, facturado=1)],
                                  [], [], "blanco", "2026-09-15")
    assert _monto(bg["pasivo"], "iva_a_pagar") == 198
    assert "iva_credito" not in _claves(bg["activo"])
    assert bg["cuadra"]


def test_iva_a_favor_va_al_activo_como_credito_fiscal():
    bg = calcular_balance_general([_m(1, "ingreso", "2026-05-01", 100, facturado=1),
                                   _m(2, "egreso", "2026-05-02", 1000, facturado=1)],
                                  [], [], "blanco", "2026-09-15")
    assert _monto(bg["activo"], "iva_credito") == 198
    assert "iva_a_pagar" not in _claves(bg["pasivo"])
    assert _monto(bg["patrimonio"], "utilidad") == -900
    assert bg["patrimonio"]["filas"][-1]["nombre"] == "Pérdida del ejercicio"
    assert bg["cuadra"]


def test_utilidad_del_ejercicio_y_resultados_anteriores_en_el_borde_del_anio():
    movs = [_m(1, "ingreso", "2025-12-31", 100), _m(2, "ingreso", "2026-01-01", 50)]
    uno_de_enero = calcular_balance_general(movs, [], [], "interno", "2026-01-01")
    fin_de_anio = calcular_balance_general(movs, [], [], "interno", "2025-12-31")

    assert uno_de_enero["ejercicio_desde"] == "2026-01-01"
    assert _monto(uno_de_enero["patrimonio"], "acumulados") == 100
    assert _monto(uno_de_enero["patrimonio"], "utilidad") == 50
    assert fin_de_anio["ejercicio_desde"] == "2025-01-01"
    assert "acumulados" not in _claves(fin_de_anio["patrimonio"])
    assert _monto(fin_de_anio["patrimonio"], "utilidad") == 100


def test_el_dia_de_alta_de_un_pendiente_es_el_de_montevideo():
    """SQLite guarda created_at en UTC: las 2 de la mañana del 1/1 UTC son las
    23 del 31/12 en Montevideo."""
    assert fecha_mvd_de_timestamp("2026-01-01 02:00:00") == "2025-12-31"
    assert fecha_mvd_de_timestamp("2026-01-01 03:00:00") == "2026-01-01"
    pendiente = [{"monto_usd": 70, "desde": fecha_mvd_de_timestamp("2026-01-01 02:00:00"),
                  "cobrado_fecha": None}]
    bg = calcular_balance_general([], pendiente, [], "interno", "2025-12-31")
    assert _monto(bg["activo"], "por_cobrar") == 70


# ── datos manuales ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("corte,esta", [
    ("2026-02-28", False), ("2026-03-01", True), ("2026-05-31", True), ("2026-06-01", False),
])
def test_un_bien_existe_desde_su_fecha_y_hasta_el_dia_que_se_vende(corte, esta):
    datos = [_d("activo", "rodados", 4000, desde="2026-03-01", hasta="2026-06-01")]
    assert dato_vigente(datos[0], corte) is esta
    bg = calcular_balance_general([], [], datos, "interno", corte)
    assert ("activo:rodados" in _claves(bg["activo"])) is esta


def test_los_bienes_se_agrupan_por_rubro_en_el_orden_del_balance():
    datos = [_d("activo", "otros", 10), _d("activo", "inmuebles", 90000),
             _d("activo", "maquinarias", 700), _d("activo", "maquinarias", 300),
             _d("pasivo", "fiscales", 40), _d("pasivo", "sueldos", 1200)]
    bg = calcular_balance_general([], [], datos, "interno", "2026-09-15")
    assert _claves(bg["activo"]) == ["caja", "activo:maquinarias", "activo:inmuebles", "activo:otros"]
    assert _monto(bg["activo"], "activo:maquinarias") == 1000
    assert [f["nombre"] for f in bg["pasivo"]["filas"]] == ["Sueldos por pagar", "Deudas fiscales / BPS"]


def test_en_blanco_vs_interno():
    movs = [_m(1, "ingreso", "2026-05-01", 1000, facturado=1), _m(2, "ingreso", "2026-05-02", 400)]
    datos = [_d("activo", "mercaderia", 250, en_blanco=0), _d("capital", "capital", 100)]
    blanco = calcular_balance_general(movs, [], datos, "blanco", "2026-09-15")
    interno = calcular_balance_general(movs, [], datos, "interno", "2026-09-15")

    assert _monto(blanco["activo"], "caja") == 1220
    assert "activo:mercaderia" not in _claves(blanco["activo"])
    assert _monto(blanco["patrimonio"], "utilidad") == 1000
    assert _monto(interno["activo"], "caja") == 1620
    assert _monto(interno["activo"], "activo:mercaderia") == 250
    assert _monto(interno["patrimonio"], "utilidad") == 1400
    assert blanco["tipo_nombre"] == "En blanco (contable)"


@pytest.mark.parametrize("tipo,corte", [("otro", "2026-09-15"), ("blanco", "2026-02-30"),
                                        ("interno", "hoy")])
def test_entradas_invalidas_revientan(tipo, corte):
    with pytest.raises(ValueError):
        calcular_balance_general([], [], [], tipo, corte)


def test_encabezado_y_empresa(monkeypatch):
    monkeypatch.delenv("EMPRESA_NOMBRE", raising=False)
    assert empresa_nombre() == "Scalerics"
    monkeypatch.setenv("EMPRESA_NOMBRE", "  Scalerics SAS ")
    assert empresa_nombre() == "Scalerics SAS"
    bg = calcular_balance_general([], [], [], "blanco", "2026-09-15")
    assert bg["expresado_en"] == "dólares estadounidenses" and bg["moneda"] == "USD"


# ── con base ─────────────────────────────────────────────────────────────────

def test_pendientes_para_balance_trae_la_fecha_del_cobro(tmp_path):
    db = str(tmp_path / "p.db")
    init_db(db)
    a = crear_por_cobrar(db, concepto="Saldo A", monto_usd=300)
    crear_por_cobrar(db, concepto="Saldo B", monto_usd=200)
    saldar_por_cobrar(db, a, fecha="2026-08-10")
    filas = {p["concepto"]: p for p in pendientes_para_balance(db)}
    assert filas["Saldo A"]["cobrado_fecha"] == "2026-08-10"
    assert filas["Saldo B"]["cobrado_fecha"] is None
    assert len(filas["Saldo B"]["desde"]) == 10


def test_balance_general_con_base_cuelga_el_estado_de_resultados(tmp_path):
    db = str(tmp_path / "b.db")
    init_db(db)
    crear_movimiento(db, tipo="ingreso", fecha="2025-11-01", periodo="2025-11", concepto="Viejo",
                     categoria="otros", monto=60, moneda="USD", monto_usd=60)
    crear_movimiento(db, tipo="ingreso", fecha="2026-02-01", periodo="2026-02", concepto="Web",
                     categoria="desarrollo_web", monto=900, moneda="USD", monto_usd=900,
                     facturado=1, iva_usd=198)
    crear_movimiento(db, tipo="egreso", fecha="2026-10-01", periodo="2026-10", concepto="Futuro",
                     categoria="otros", monto=5000, moneda="USD", monto_usd=5000)
    crear_dato_balance(db, clase="capital", rubro="capital", nombre="Aportes",
                       monto_usd=100, desde="2025-01-01")

    bg = balance_general(db, "interno", "2026-09-15")

    er = bg["estado_resultados"]
    assert (er["desde"], er["hasta"]) == ("2026-01-01", "2026-09-15")
    assert _monto(bg["patrimonio"], "utilidad") == er["resultado"]["total"] == 900
    assert _monto(bg["patrimonio"], "acumulados") == 60
    assert _monto(bg["activo"], "caja") == 1158          # 60 + 900 + 198, sin el de octubre
    assert bg["diferencia"] == -100                        # capital sin plata que lo respalde


# ── rutas ────────────────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "raiz@scalerics.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("EMPRESA_NOMBRE", raising=False)
    db = str(tmp_path / "a.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


@pytest.fixture
def cli(app):
    uid = create_user(app.config["_DB"], name="Raiz", email="raiz@scalerics.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Raiz"
    return c


def _caller(app):
    db = app.config["_DB"]
    uid = create_user(db, name="caller", email="caller@scalerics.com", phone="098",
                      password_hash=generate_password_hash("x" * 10))
    conn = sqlite3.connect(db)
    rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES ('Solo cola', ?)",
                       (json.dumps(["cola"]),)).lastrowid
    conn.execute("UPDATE users SET role_id=? WHERE id=?", (rid, uid))
    conn.commit()
    conn.close()
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "caller"
    return c


def test_la_ruta_genera_el_balance_general_a_hoy(app, cli):
    db = app.config["_DB"]
    hoy = hoy_montevideo().isoformat()
    crear_movimiento(db, tipo="ingreso", fecha=hoy, periodo=hoy[:7], concepto="Cobro",
                     categoria="otros", monto=500, moneda="USD", monto_usd=500)
    r = cli.get("/api/finanzas/balance-general?tipo=interno")
    d = r.get_json()
    assert r.status_code == 200
    assert d["corte"] == hoy and d["empresa"] == "Scalerics"
    assert _monto(d["activo"], "caja") == 500
    assert d["activo"]["total"] == d["total_pasivo_patrimonio"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", d["generado_en"])
    assert "estado_resultados" in d


def test_la_ruta_usa_el_dia_de_montevideo(cli, monkeypatch):
    import services.daily
    monkeypatch.setattr(services.daily, "hoy_montevideo", lambda: date(2026, 12, 31))
    d = cli.get("/api/finanzas/balance-general?tipo=blanco").get_json()
    assert d["corte"] == "2026-12-31" and d["ejercicio_desde"] == "2026-01-01"


@pytest.mark.parametrize("query", ["", "tipo=negro", "tipo=blanco&fecha=2026-13-01",
                                   "tipo=blanco&fecha=15/09/2026"])
def test_la_ruta_valida(cli, query):
    r = cli.get("/api/finanzas/balance-general?" + query)
    assert r.status_code == 400 and r.get_json()["ok"] is False


def test_sin_panel_finanzas_no_hay_balance_ni_datos(app):
    c = _caller(app)
    assert c.get("/api/finanzas/balance-general?tipo=blanco").status_code == 403
    assert c.get("/api/finanzas/balance-datos").status_code == 403
    assert c.post("/api/finanzas/balance-datos", json={}).status_code == 403


def test_alta_edicion_y_borrado_de_datos(app, cli):
    r = cli.post("/api/finanzas/balance-datos",
                 json={"clase": "activo", "rubro": "maquinarias", "nombre": " Notebooks ",
                       "monto_usd": 1500, "desde": "2026-02-01"})
    assert r.status_code == 201
    did = r.get_json()["id"]
    dato = get_dato_balance(app.config["_DB"], did)
    assert dato["nombre"] == "Notebooks" and dato["en_blanco"] == 1 and dato["hasta"] is None

    listado = cli.get("/api/finanzas/balance-datos").get_json()
    assert [x["id"] for x in listado["datos"]] == [did]
    assert listado["clases"] == BALANCE_CLASES

    r = cli.put(f"/api/finanzas/balance-datos/{did}",
                json={"clase": "activo", "rubro": "maquinarias", "nombre": "Notebooks",
                      "monto_usd": 1200, "desde": "2026-02-01", "hasta": "2026-08-01",
                      "en_blanco": False})
    assert r.status_code == 200
    dato = get_dato_balance(app.config["_DB"], did)
    assert (dato["monto_usd"], dato["hasta"], dato["en_blanco"]) == (1200, "2026-08-01", 0)

    assert cli.delete(f"/api/finanzas/balance-datos/{did}").status_code == 200
    assert get_dato_balance(app.config["_DB"], did) is None
    assert cli.put(f"/api/finanzas/balance-datos/{did}", json={}).status_code == 404
    assert cli.delete(f"/api/finanzas/balance-datos/{did}").status_code == 404


def test_capital_y_saldo_inicial_toman_su_rubro_solos(app, cli):
    for cuerpo in ({"clase": "capital", "monto_usd": 5000, "desde": "2025-01-01"},
                   {"clase": "caja_inicial", "monto_usd": -250, "desde": "2025-01-01"}):
        assert cli.post("/api/finanzas/balance-datos", json=cuerpo).status_code == 201
    datos = cli.get("/api/finanzas/balance-datos").get_json()["datos"]
    assert {(x["clase"], x["rubro"], x["nombre"]) for x in datos} == {
        ("capital", "capital", "Capital"), ("caja_inicial", "caja_inicial", "Saldo inicial de caja")}
    d = cli.get("/api/finanzas/balance-general?tipo=blanco&fecha=2026-01-01").get_json()
    assert _monto(d["activo"], "caja") == -250
    assert _monto(d["patrimonio"], "capital") == 5000


@pytest.mark.parametrize("cuerpo", [
    {"clase": "otra", "monto_usd": 1, "desde": "2026-01-01"},
    {"clase": "activo", "rubro": "sueldos", "monto_usd": 1, "desde": "2026-01-01"},
    {"clase": "activo", "monto_usd": 1, "desde": "2026-01-01"},
    {"clase": "pasivo", "rubro": "prestamos", "monto_usd": 0, "desde": "2026-01-01"},
    {"clase": "pasivo", "rubro": "prestamos", "monto_usd": -5, "desde": "2026-01-01"},
    {"clase": "pasivo", "rubro": "prestamos", "monto_usd": "mucho", "desde": "2026-01-01"},
    {"clase": "caja_inicial", "monto_usd": 0, "desde": "2026-01-01"},
    {"clase": "capital", "monto_usd": 10, "desde": "ayer"},
    {"clase": "capital", "monto_usd": 10, "desde": "2026-05-01", "hasta": "2026-05-01"},
    {"clase": "capital", "monto_usd": 10, "desde": "2026-05-01", "hasta": "2026-02-30"},
])
def test_datos_invalidos_dan_400(cli, cuerpo):
    r = cli.post("/api/finanzas/balance-datos", json=cuerpo)
    assert r.status_code == 400 and r.get_json()["ok"] is False


def test_la_pagina_principal_abre_con_el_balance_general(tmp_path, monkeypatch):
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
    for pedazo in (b'id="fb-corte"', b'id="fbd-card"', b"Datos para el balance",
                   b"function _finBalGeneralPintar(", b"Generar balance hasta el momento"):
        assert pedazo in r.data, pedazo


# ── las trampas de DASHBOARD_HTML ────────────────────────────────────────────

def _entre(texto, desde, hasta):
    i = texto.index(desde)
    return texto[i:texto.index(hasta, i + len(desde))]


def test_el_js_nuevo_no_tiene_jinja_ni_barras_invertidas():
    js = _entre(SRC, "// ---- Balance General (Activo = Pasivo + Patrimonio) ----",
                "function _finMesActual(")
    for trampa in ("{#", "{{", "{%", chr(92)):
        assert trampa not in js, repr(trampa)
    assert js in HTML


def test_el_css_del_balance_general_usa_tokens():
    reglas = re.findall(r"^\.fb[gd]-[^{\n]*\{[^}]*\}", HTML, re.M)
    assert len(reglas) >= 15
    for regla in reglas:
        assert not re.search("#[0-9a-fA-F]{3,6}(?![0-9a-zA-Z])", regla), regla
        assert "{#" not in regla


def test_en_el_celular_una_columna_y_al_imprimir_dos():
    assert re.search(r"@media \(max-width:760px\)\{[^@]*\.fbg-cols\{grid-template-columns:1fr\}", HTML)
    assert "body.fb-imprimiendo .fbg-cols{grid-template-columns:1fr 1fr" in HTML


# ── lo que se ve (node) ──────────────────────────────────────────────────────

def _pintar(tmp_path, bg, extra=""):
    prueba = """
console.log(JSON.stringify({pantalla: _finBalGeneralPintar(__BG__),
                            impresion: _finBalGeneralPintar(__BG__, true) __EXTRA__}));
""".replace("__BG__", json.dumps(bg)).replace("__EXTRA__", extra)
    return _correr(tmp_path, prueba)


def _valor(html, rotulo):
    m = re.search(r'<span>' + re.escape(rotulo) + r'</span><span class="fbg-num">([^<]*)</span>', html)
    assert m, rotulo
    return m.group(1)


def _bg_que_cuadra():
    datos = [_d("caja_inicial", "caja_inicial", 300), _d("activo", "maquinarias", 500),
             _d("pasivo", "prestamos", 300), _d("capital", "capital", 500)]
    bg = calcular_balance_general(_movs(), [], datos, "interno", "2026-09-15",
                                  generado_en="2026-09-15 10:32")
    bg["estado_resultados"] = {"tipo": "interno", "tipo_nombre": "Interno (todo)",
                               "desde": "2026-01-01", "hasta": "2026-09-15", "generado_en": "",
                               "movimientos": 3, "sin_cotizacion": 0,
                               "ingresos": {"total": 1000, "blanco": 1000, "no_facturado": 0,
                                            "por_categoria": []},
                               "egresos": {"total": 300, "blanco": 200, "no_facturado": 100,
                                           "por_categoria": []},
                               "resultado": {"total": 700, "blanco": 800, "no_facturado": -100},
                               "iva": {"ventas": 220, "compras": 44, "saldo": 176},
                               "con_iva": {"ingresos": 1220, "egresos": 344, "resultado": 876},
                               "impuestos": {"total": 0, "por_concepto": []}, "meses": []}
    return bg


@sin_node
def test_pinta_el_formato_clasico_con_los_dos_totales_iguales(tmp_path):
    s = _pintar(tmp_path, _bg_que_cuadra(), extra=", monto: _finBalMonto(1676), negativo: _finBalMonto(-1234.5)")
    html = s["pantalla"]

    for linea in ("BALANCE GENERAL", '<div class="fbg-empresa">Scalerics</div>',
                  "<div>AL 15 DE SEPTIEMBRE DE 2026</div>",
                  "<div>(expresado en dólares estadounidenses)</div>"):
        assert linea in html, linea
    # Dos columnas: Activo a la izquierda, Pasivo y Patrimonio a la derecha.
    izquierda = _entre(html, '<div class="fbg-col fbg-activo">', '<div class="fbg-col fbg-pasivo">')
    derecha = html[html.index('<div class="fbg-col fbg-pasivo">'):]
    assert "ACTIVO" in izquierda and "Caja y Bancos" in izquierda and "Maquinarias y equipos" in izquierda
    assert derecha.index(">PASIVO<") < derecha.index(">PATRIMONIO<")
    assert "IVA a pagar" in derecha and "Préstamos por pagar" in derecha and "Capital" in derecha
    assert "Utilidad del ejercicio" in derecha

    assert _valor(html, "TOTAL ACTIVO") == _valor(html, "TOTAL PASIVO Y PATRIMONIO") == s["monto"]
    assert '<div class="fbg-fila fbg-total"><span>TOTAL ACTIVO</span>' in html
    assert '<div class="fbg-fila fbg-total"><span>TOTAL PASIVO Y PATRIMONIO</span>' in html
    assert '<div class="fbg-fila fbg-sub"><span>TOTAL PASIVO</span>' in html
    assert '<div class="fbg-fila fbg-sub"><span>TOTAL PATRIMONIO</span>' in html
    assert "fbg-dif" not in html and "no cuadra" not in html
    # El estado de resultados queda abajo, colapsado, en pantalla; no se imprime.
    assert '<details class="fbg-er"><summary>Estado de resultados del período</summary>' in html
    assert "fbg-er" not in s["impresion"] and "TOTAL ACTIVO" in s["impresion"]
    assert s["negativo"].startswith("(") and s["negativo"].endswith(")")


@sin_node
def test_si_no_cuadra_se_ve_la_diferencia_en_ambar_y_el_aviso(tmp_path):
    datos = [_d("caja_inicial", "caja_inicial", 300), _d("activo", "maquinarias", 500),
             _d("pasivo", "prestamos", 300)]
    bg = calcular_balance_general(_movs(), [], datos, "interno", "2026-09-15")
    s = _pintar(tmp_path, bg, extra=", usd: _finUsd(500), monto: _finBalMonto(500)")
    html = s["pantalla"]

    assert ('<div class="fbg-fila fbg-dif"><span>Diferencia a revisar (patrimonio no explicado)</span>'
            '<span class="fbg-num">' + s["monto"] + "</span></div>") in html
    assert "El balance no cuadra: hay " + s["usd"] + " de activo" in html
    assert "saldo inicial de caja" in html and "capital" in html
    assert _valor(html, "TOTAL ACTIVO") == _valor(html, "TOTAL PASIVO Y PATRIMONIO")


@sin_node
def test_los_rubros_del_formulario_son_los_del_servidor(tmp_path):
    s = _correr(tmp_path, "console.log(JSON.stringify(FB_RUBROS));")
    assert s == BALANCE_CLASES


@sin_node
def test_datos_para_el_balance_normal_y_en_solo_lectura(tmp_path):
    datos = [{"id": 4, "clase": "pasivo", "rubro": "prestamos", "nombre": "BROU",
              "monto_usd": 2000, "desde": "2026-01-10", "hasta": None, "en_blanco": 0}]
    prueba = """
(async () => {
  await new Promise(r => setImmediate(r));
  _RESP['/api/finanzas/balance-datos'] = {datos: %s};
  const cuerpos = [];
  const fetchOriginal = globalThis.fetch;
  globalThis.fetch = (url, o) => {
    if (o && o.body) cuerpos.push([String(url), o.method, JSON.parse(o.body)]);
    return fetchOriginal(url, o);
  };

  await loadBalanceDatos();
  const normal = {lista: _el('fbd-lista').innerHTML, form: _el('fbd-form').style.display,
                  rubros: _el('fbd-rubro').innerHTML, desde: _el('fbd-desde').value};

  finBalDatoEditar(4);
  const editando = {clase: _el('fbd-clase').value, nombre: _el('fbd-nombre').value,
                    blanco: _el('fbd-blanco').checked, cancelar: _el('fbd-cancelar').style.display};
  _el('fbd-monto').value = '2500';
  _el('fbd-rubro').value = 'prestamos';
  await finBalDatoGuardar();

  _el('fbd-id').value = '9';
  _el('fbd-clase').value = 'capital';
  _el('fbd-rubro').value = 'capital';
  _RESP['/api/finanzas/balance-datos/9'] = {_status: 400, error: 'monto mal'};
  await finBalDatoGuardar();
  const error = {texto: _el('fbd-error').textContent, display: _el('fbd-error').style.display};

  window._panelesSoloLectura = ['finanzas'];
  await loadBalanceDatos();
  const solo = {lista: _el('fbd-lista').innerHTML, form: _el('fbd-form').style.display};
  console.log(JSON.stringify({normal, editando, cuerpos, error, solo, hoy: _finBalHoy()}));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""" % json.dumps(datos)
    s = _correr(tmp_path, prueba)

    assert s["normal"]["form"] == ""
    assert "BROU" in s["normal"]["lista"] and "Pasivo · Préstamos por pagar" in s["normal"]["lista"]
    assert "finBalDatoEditar(4)" in s["normal"]["lista"] and "finBalDatoBorrar(4)" in s["normal"]["lista"]
    assert "Mercadería" in s["normal"]["rubros"]
    assert s["normal"]["desde"] == s["hoy"]

    assert s["editando"] == {"clase": "pasivo", "nombre": "BROU", "blanco": False, "cancelar": ""}
    url, metodo, cuerpo = s["cuerpos"][0]
    assert (url, metodo) == ("/api/finanzas/balance-datos/4", "PUT")
    assert cuerpo == {"clase": "pasivo", "rubro": "prestamos", "nombre": "BROU", "monto_usd": 2500,
                      "desde": "2026-01-10", "hasta": None, "en_blanco": False}
    assert s["error"] == {"texto": "monto mal", "display": ""}

    assert s["solo"]["form"] == "none"
    assert "BROU" in s["solo"]["lista"]
    assert "finBalDatoEditar(" not in s["solo"]["lista"] and "finBalDatoBorrar(" not in s["solo"]["lista"]
