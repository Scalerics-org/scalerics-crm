"""Devoluciones de aportes (Juan, 3/11).

Un aporte es plata que pone un socio; una devolución es parte de esa plata
que se le devuelve. Se carga como un egreso con la categoría
"devolucion_aporte": sale de la caja, pero no es gasto del negocio, y en
Aportes se resta para ver lo que los socios todavía no recuperaron.
"""

from tests.test_finanzas_rutas import _cli, _mes, _rol, _usuario, app  # noqa: F401
from tests.test_finanzas_service import db  # noqa: F401

import dashboard
from database import crear_movimiento, listar_movimientos
from services.finanzas import (CATEGORIAS, aportado_hasta_la_fecha, calcular_balance,
                               calcular_balance_general, resumen)


def _mov(db, tipo, cat, monto, fecha, concepto="x"):
    return crear_movimiento(db, tipo=tipo, fecha=fecha, periodo=fecha[:7], concepto=concepto,
                            categoria=cat, monto=monto, moneda="USD", monto_usd=monto)


def _con_devoluciones(db):
    _mov(db, "ingreso", "desarrollo_web", 1000, "2026-09-05")
    _mov(db, "ingreso", "aporte", 500, "2026-09-10", "Aporte Javier")
    _mov(db, "ingreso", "aporte", 300, "2026-08-10", "Aporte padre Juan")
    _mov(db, "egreso", "infraestructura", 1200, "2026-09-20")
    _mov(db, "egreso", "devolucion_aporte", 200, "2026-09-25", "Devolución aporte Javier")
    _mov(db, "egreso", "devolucion_aporte", 50, "2026-08-28", "Devolución a padre Juan (transferencia)")


def test_la_devolucion_es_una_categoria_de_egreso():
    assert "devolucion_aporte" in CATEGORIAS["egreso"]
    assert "devolucion_aporte" not in CATEGORIAS["ingreso"]


def test_aportes_resta_lo_devuelto_y_muestra_lo_que_falta_recuperar(db):
    _con_devoluciones(db)
    d = aportado_hasta_la_fecha(db)
    assert d["total_usd"] == 800
    assert d["devuelto_usd"] == 250
    assert d["por_recuperar_usd"] == 550
    assert d["cantidad"] == 2 and d["cantidad_devoluciones"] == 2
    assert d["por_persona"] == [
        {"persona": "Javier", "total_usd": 500, "devuelto_usd": 200, "por_recuperar_usd": 300},
        {"persona": "Padre Juan", "total_usd": 300, "devuelto_usd": 50, "por_recuperar_usd": 250},
    ]
    assert [m["persona"] for m in d["devoluciones"]] == ["Javier", "Padre Juan"]
    # La lista de aportes y los meses siguen siendo solo de aportes.
    assert [m["concepto"] for m in d["movimientos"]] == ["Aporte Javier", "Aporte padre Juan"]
    assert sum(m["total_usd"] for m in d["por_mes"]) == 800


def test_una_devolucion_anulada_no_cuenta(db):
    from database import actualizar_movimiento
    _mov(db, "ingreso", "aporte", 500, "2026-09-10", "Aporte Javier")
    mid = _mov(db, "egreso", "devolucion_aporte", 200, "2026-09-25", "Devolución Javier")
    actualizar_movimiento(db, mid, anulado=1)
    d = aportado_hasta_la_fecha(db)
    assert d["devuelto_usd"] == 0 and d["por_recuperar_usd"] == 500
    assert d["cantidad_devoluciones"] == 0


def test_sin_devoluciones_lo_que_falta_es_todo_el_aporte(db):
    _mov(db, "ingreso", "aporte", 500, "2026-09-10", "Aporte Javier")
    d = aportado_hasta_la_fecha(db)
    assert d["devuelto_usd"] == 0 and d["por_recuperar_usd"] == 500
    assert d["devoluciones"] == []


def test_la_devolucion_sale_de_caja_pero_no_es_perdida(db):
    _con_devoluciones(db)
    k = resumen(db, "2026-09", "2026-09")["kpis"]
    assert k["egresos_usd"] == 1400                  # sigue siendo egreso: 1200 + 200
    assert k["devoluciones_usd"] == 200 and k["devoluciones_previas_usd"] == 50
    assert k["neto_usd"] == 100                      # 1500 - 1400
    assert k["resultado_real_usd"] == -200           # sin aportes ni devoluciones: 1000 - 1200


def test_en_el_balance_la_devolucion_no_es_gasto_y_achica_los_aportes(db):
    _con_devoluciones(db)
    movs = listar_movimientos(db)
    b = calcular_balance(movs, "interno", "2026-09-01", "2026-09-30")
    assert b["egresos"]["total"] == 1200
    assert "devolucion_aporte" not in [c["categoria"] for c in b["egresos"]["por_categoria"]]
    assert b["aportes"] == {"total": 500, "devoluciones": 200, "neto": 300}
    assert b["resultado"]["total"] == -200
    g = calcular_balance_general(movs, [], [], "interno", "2026-09-30")
    filas = {f["clave"]: f["monto"] for f in g["patrimonio"]["filas"]}
    assert filas["aportes"] == 550                   # 800 aportado - 250 devuelto
    assert filas["utilidad"] == -200
    assert g["activo"]["filas"][0]["monto"] == 350   # caja: 1000 + 800 - 1200 - 250
    assert g["total_pasivo_patrimonio"] == g["activo"]["total"]


def test_la_ruta_de_aportes_trae_las_devoluciones(app):  # noqa: F811
    db = app.config["_DB"]
    uid = _usuario(db, "socio@scalerics.com", _rol(db, "Socio", ["cola", "finanzas"]))
    cli = _cli(app, uid)
    mes = _mes()
    assert cli.post("/api/finanzas/movimientos", json={
        "tipo": "ingreso", "fecha": f"{mes}-01", "concepto": "Aporte Javier",
        "categoria": "aporte", "monto": 1000, "moneda": "USD"}).status_code == 201
    assert cli.post("/api/finanzas/movimientos", json={
        "tipo": "egreso", "fecha": f"{mes}-02", "concepto": "Devolución aporte Javier",
        "categoria": "devolucion_aporte", "monto": 400, "moneda": "USD"}).status_code == 201
    d = cli.get("/api/finanzas/aportes").get_json()
    assert d["total_usd"] == 1000 and d["devuelto_usd"] == 400 and d["por_recuperar_usd"] == 600


def test_el_panel_tiene_las_tres_tarjetas_y_el_boton_de_devolucion():
    html = dashboard.DASHBOARD_HTML
    assert 'id="fin-tipo-devolucion"' in html and "finSetTipo('devolucion')" in html
    assert "sel.value = aporte ? 'aporte' : 'devolucion_aporte'" in html
    for texto in ("Aporte total", "Devolución", "Lo que no ha recuperado aún"):
        assert texto in html
    for id_ in ("fin-aporte-total", "fin-aporte-devuelto", "fin-aporte-falta"):
        assert f'id="{id_}"' in html
