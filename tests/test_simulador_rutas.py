"""El simulador financiero del lado del servidor: precarga, escenarios y candado.

La regla que más importa acá: el simulador LEE Finanzas y nunca escribe ahí.
"""

import json
import sqlite3
from datetime import date

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import (actualizar_por_cobrar, create_user, crear_movimiento,
                      crear_por_cobrar, crear_recurrente, get_escenario,
                      init_db, insert_business, listar_escenarios,
                      update_business)
from services.simulador import (NOTA_SIN_TIPO_DE_CAMBIO, TAMANIO_MAXIMO,
                                caja_actual, gastos_desde_fijos,
                                mantenimientos_de_clientes, pendientes_por_cobrar,
                                validar_escenario)

HOY = date(2026, 9, 14)


def _mes_actual() -> str:
    hoy = date.today()
    return f"{hoy.year:04d}-{hoy.month:02d}"


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "sim.db")
    init_db(ruta)
    return ruta


@pytest.fixture
def app(db, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "raiz@scalerics.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


def _cliente_http(app, email, paneles=None):
    db = app.config["_DB"]
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    if paneles is not None:
        conn = sqlite3.connect(db)
        rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?, ?)",
                           (f"rol-{email}", json.dumps(paneles))).lastrowid
        conn.execute("UPDATE users SET role_id=? WHERE id=?", (rid, uid))
        conn.commit()
        conn.close()
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = email.split("@")[0]
    return c


@pytest.fixture
def cli(app):
    return _cliente_http(app, "socio@scalerics.com", ["simulador"])


_tel = iter(range(810000, 899999))


def _negocio(db, nombre, estado):
    bid = insert_business(db, {"name": nombre, "phone": f"+598{next(_tel)}"})
    update_business(db, bid, crm_status=estado)
    return bid


def _fijo(db, **campos):
    datos = {"tipo": "egreso", "concepto": "Hosting", "categoria": "infraestructura",
             "monto": 90, "moneda": "USD", "desde": "2020-01"}
    datos.update(campos)
    return crear_recurrente(db, **datos)


def _foto_de_finanzas(db) -> dict:
    conn = sqlite3.connect(db)
    try:
        tablas = [f[0] for f in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'finanzas_%'")]
        return {t: conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall()
                for t in tablas}
    finally:
        conn.close()


# ── candado ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("metodo,ruta", [
    ("get", "/api/simulador/precarga"),
    ("get", "/api/simulador/escenarios"),
    ("post", "/api/simulador/escenarios"),
    ("get", "/api/simulador/escenarios/1"),
    ("put", "/api/simulador/escenarios/1"),
    ("delete", "/api/simulador/escenarios/1"),
])
def test_sin_el_panel_no_entra_aunque_tenga_finanzas(app, metodo, ruta):
    """Tener Finanzas no da el simulador: son dos paneles y se asignan aparte."""
    c = _cliente_http(app, "caller@scalerics.com", ["finanzas", "cola"])
    r = getattr(c, metodo)(ruta, json={"nombre": "x", "datos": {}})
    assert r.status_code == 403


def test_el_admin_entra_sin_que_se_lo_asignen(app):
    c = _cliente_http(app, "raiz@scalerics.com")
    assert c.get("/api/simulador/precarga").status_code == 200


def test_ningun_rol_existente_recibe_el_panel_solo(db):
    """Igual que Finanzas (Ruling R20): arranca sin nadie asignado."""
    conn = sqlite3.connect(db)
    try:
        paneles = [json.loads(p) for (p,) in conn.execute("SELECT panel_access FROM roles")]
    finally:
        conn.close()
    assert paneles, "la siembra de roles tendría que haber corrido"
    assert all("simulador" not in p for p in paneles)


# ── precarga ─────────────────────────────────────────────────────────────────

def test_los_gastos_salen_de_los_fijos_activos_en_dolares_y_prendidos(db):
    _fijo(db, concepto="Hosting", monto=90)
    _fijo(db, concepto="Contador", monto=2800, moneda="UYU", tipo_cambio=40)
    _fijo(db, concepto="Sin cambio", monto=1000, moneda="UYU", tipo_cambio=None)
    _fijo(db, concepto="Mantenimiento cobrado", tipo="ingreso",
          categoria="mantenimiento", monto=100)
    _fijo(db, concepto="Apagado", monto=50, activo=0)
    _fijo(db, concepto="Terminado", monto=70, hasta="2026-08")
    _fijo(db, concepto="Termina este mes", monto=30, hasta="2026-09")

    filas = {f["nombre"]: f for f in gastos_desde_fijos(db, hoy=HOY)}

    assert set(filas) == {"Hosting", "Contador", "Sin cambio", "Termina este mes"}
    assert filas["Hosting"]["monto"] == 90
    assert filas["Contador"]["monto"] == 70, "2800 pesos a 40 son 70 dólares"
    assert all(f["activo"] is True for f in filas.values())
    assert filas["Sin cambio"]["monto"] == 0
    assert filas["Sin cambio"]["nota"] == NOTA_SIN_TIPO_DE_CAMBIO
    assert filas["Hosting"]["nota"] == ""


def test_sin_fijos_la_precarga_de_gastos_viene_vacia(app, cli):
    """Vacía, no la lista de respaldo: el respaldo vive en SIM_DEFAULTS del
    panel, que es el único lugar con valores por defecto."""
    r = cli.get("/api/simulador/precarga")
    assert r.status_code == 200
    datos = r.get_json()
    assert datos["gastosFijos"] == []
    assert datos["moneda"] == "USD"
    assert datos["cajaActual"] == 0


def test_la_caja_actual_es_ingresos_menos_egresos_hasta_este_mes(db):
    mov = {"fecha": "2026-09-01", "periodo": "2026-09", "concepto": "x",
           "moneda": "USD"}
    crear_movimiento(db, tipo="ingreso", categoria="desarrollo_web", monto=1000,
                     monto_usd=1000, facturado=1, iva_usd=220, **mov)
    crear_movimiento(db, tipo="egreso", categoria="servicios", monto=300,
                     monto_usd=300, **mov)
    crear_movimiento(db, tipo="egreso", categoria="servicios", monto=50,
                     monto_usd=50, anulado=1, **mov)
    crear_movimiento(db, tipo="ingreso", categoria="desarrollo_web", monto=400,
                     monto_usd=400, fecha="2026-03-10", periodo="2026-03",
                     concepto="viejo", moneda="USD")
    crear_movimiento(db, tipo="ingreso", categoria="desarrollo_web", monto=500,
                     monto_usd=500, fecha="2026-10-01", periodo="2026-10",
                     concepto="futuro", moneda="USD")

    caja = caja_actual(db, hoy=HOY)

    assert caja["ingresos"] == 1400, "sin IVA y sin el del mes que viene"
    assert caja["egresos"] == 300, "sin el anulado"
    assert caja["saldo"] == 1100
    assert caja["hasta"] == "2026-09"


def test_los_pendientes_son_los_no_cobrados_y_arrancan_apagados(db):
    cid = _negocio(db, "La Vaca Encantada", "en_desarrollo")
    crear_por_cobrar(db, client_id=cid, concepto="Saldo de web", monto_usd=500,
                     vence="2026-10-18")
    crear_por_cobrar(db, concepto="Seña vieja", monto_usd=200)
    cobrado = crear_por_cobrar(db, concepto="Ya cobrado", monto_usd=999)
    mid = crear_movimiento(db, tipo="ingreso", fecha="2026-09-01", periodo="2026-09",
                           concepto="cobro", categoria="otros", monto=999,
                           moneda="USD", monto_usd=999)
    actualizar_por_cobrar(db, cobrado, cobrado_movimiento_id=mid)

    filas = pendientes_por_cobrar(db)

    assert [f["nombre"] for f in filas] == ["La Vaca Encantada", "Seña vieja"]
    assert filas[0] == {"nombre": "La Vaca Encantada", "monto": 500, "activo": False,
                        "nota": "Saldo de web · vence 2026-10-18"}
    assert filas[1]["nota"] == ""
    assert all(f["activo"] is False for f in filas)


def test_los_mantenimientos_son_los_clientes_reales_apagados(db):
    _negocio(db, "Zapatería Norte", "finalizado")
    _negocio(db, "Almacén Sur", "cerrado")
    _negocio(db, "Bar Centro", "en_desarrollo")
    _negocio(db, "Todavía no", "demo_1")
    _negocio(db, "Frío", "sin_contactar")

    filas = mantenimientos_de_clientes(db)

    assert [f["nombre"] for f in filas] == ["Almacén Sur", "Bar Centro", "Zapatería Norte"]
    assert all(f["activo"] is False for f in filas)
    assert all("monto" not in f for f in filas), "la cuota inicial es un default del panel"


def test_la_precarga_por_http_trae_las_tres_listas_y_la_caja(app, cli):
    db = app.config["_DB"]
    _fijo(db, concepto="Claude", monto=120)
    _negocio(db, "Cliente A", "cerrado")
    crear_por_cobrar(db, concepto="Resto", monto_usd=750)

    datos = cli.get("/api/simulador/precarga").get_json()

    assert [f["nombre"] for f in datos["gastosFijos"]] == ["Claude"]
    assert [f["nombre"] for f in datos["mantenimientos"]] == ["Cliente A"]
    assert [f["monto"] for f in datos["pendientes"]] == [750]
    assert datos["caja"]["hasta"] == _mes_actual()


def test_el_simulador_no_escribe_en_finanzas(app, cli):
    """Ni la precarga ni los escenarios tocan una sola fila de Finanzas. Tampoco
    materializan: un fijo activo desde 2020 no genera movimientos."""
    db = app.config["_DB"]
    _fijo(db, concepto="Hosting", monto=90, desde="2020-01")
    crear_por_cobrar(db, concepto="Resto", monto_usd=750)
    antes = _foto_de_finanzas(db)
    assert antes["finanzas_movimientos"] == []

    cli.get("/api/simulador/precarga")
    r = cli.post("/api/simulador/escenarios", json={"nombre": "A", "datos": {"version": 1}})
    eid = r.get_json()["id"]
    cli.put(f"/api/simulador/escenarios/{eid}", json={"nombre": "B", "datos": {"version": 1}})
    cli.get(f"/api/simulador/escenarios/{eid}")
    cli.delete(f"/api/simulador/escenarios/{eid}")

    assert _foto_de_finanzas(db) == antes


# ── escenarios ───────────────────────────────────────────────────────────────

def test_guardar_listar_abrir_actualizar_y_borrar(app, cli):
    datos = {"version": 1, "equipo": {"cantidadProgramadores": 3},
             "gastosFijos": [{"nombre": "Figma", "monto": 15, "activo": True}]}

    r = cli.post("/api/simulador/escenarios", json={"nombre": "  Con Figma  ", "datos": datos})
    assert r.status_code == 201
    eid = r.get_json()["id"]

    lista = cli.get("/api/simulador/escenarios").get_json()
    assert [(e["id"], e["nombre"]) for e in lista] == [(eid, "Con Figma")]
    assert "datos" not in lista[0], "la lista es para elegir, sin el escenario entero"

    abierto = cli.get(f"/api/simulador/escenarios/{eid}").get_json()
    assert abierto["datos"] == datos
    assert abierto["created_by_name"] == "socio"

    datos["equipo"]["cantidadProgramadores"] = 4
    r = cli.put(f"/api/simulador/escenarios/{eid}", json={"nombre": "Cuatro", "datos": datos})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True and r.get_json()["id"] == eid
    assert r.get_json()["nombre"] == "Cuatro" and r.get_json()["updated_at"]
    guardado = get_escenario(app.config["_DB"], eid)
    assert guardado["nombre"] == "Cuatro"
    assert json.loads(guardado["datos"])["equipo"]["cantidadProgramadores"] == 4

    assert cli.delete(f"/api/simulador/escenarios/{eid}").status_code == 200
    assert listar_escenarios(app.config["_DB"]) == []


@pytest.mark.parametrize("metodo", ["get", "put", "delete"])
def test_un_escenario_que_no_existe_da_404(cli, metodo):
    r = getattr(cli, metodo)("/api/simulador/escenarios/999",
                             json={"nombre": "x", "datos": {}})
    assert r.status_code == 404


@pytest.mark.parametrize("cuerpo,pedazo", [
    ({"nombre": "", "datos": {}}, "nombre"),
    ({"nombre": "   ", "datos": {}}, "nombre"),
    ({"datos": {}}, "nombre"),
    ({"nombre": 123, "datos": {}}, "nombre"),
    ({"nombre": "x" * 81, "datos": {}}, "80"),
    ({"nombre": "ok", "datos": [1, 2]}, "datos"),
    ({"nombre": "ok"}, "datos"),
    ({"nombre": "ok", "datos": {"relleno": "x" * TAMANIO_MAXIMO}}, "grande"),
])
def test_guardar_valida_nombre_y_datos(cli, cuerpo, pedazo):
    r = cli.post("/api/simulador/escenarios", json=cuerpo)
    assert r.status_code == 400
    assert pedazo in r.get_json()["error"]


def test_un_cuerpo_que_no_es_json_da_400(cli):
    r = cli.post("/api/simulador/escenarios", data="no es json",
                 content_type="text/plain")
    assert r.status_code == 400


def test_validar_escenario_devuelve_el_json_listo():
    (nombre, texto), error = validar_escenario({"nombre": " Ñandú ", "datos": {"a": "ñ"}})
    assert error is None
    assert nombre == "Ñandú"
    assert json.loads(texto) == {"a": "ñ"}
    assert validar_escenario(None) == (None, "el cuerpo tiene que ser un objeto JSON")


def test_la_tabla_se_crea_una_sola_vez(db):
    init_db(db)
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        cols = [c[1] for c in conn.execute("PRAGMA table_info(simulador_escenarios)")]
    finally:
        conn.close()
    assert cols == ["id", "nombre", "datos", "created_by_id", "created_by_name",
                    "created_at", "updated_at"]
