"""Cobro con tarjeta (Plexo): la calculadora, los ajustes y el registro.

Pedido de Juan (23/9): ver cuánto cobrar para llevarse tanto, y que el IVA
compras y ventas se cargue solo. Los aranceles son los del Plan Clásico que
pasó OCA: Visa crédito 3,30%, Master crédito 3,35%, débito 1,05% / 1,15%.
"""

import json
import sqlite3
from datetime import date

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import (create_user, init_db, insert_business,
                      listar_movimientos)
from services.cobro_tarjeta import (AJUSTES_POR_DEFECTO, desglosar,
                                    sumar_dias_habiles, unir_ajustes,
                                    validar_ajustes)
from services.finanzas import resumen_iva

AJ = unir_ajustes(None)


def _hoy() -> str:
    return date.today().isoformat()


# ── la calculadora ───────────────────────────────────────────────────────────

def test_cobrar_un_precio_desglosa_todo():
    # 300 + IVA con Visa crédito, sin repartir el fijo de Plexo.
    d = desglosar("precio", 300, "visa_credito", AJ, incluir_fijo=False)
    assert d["iva_venta"] == 66.0
    assert d["total"] == 366.0
    assert d["comision"] == 12.08            # 3,30% de 366
    assert d["comision_iva"] == 2.66
    assert d["deposito"] == 351.26           # 366 - 12,08 - 2,66
    assert d["plexo"] == 0.24                # $9,49 a 40
    assert d["plexo_iva"] == 0.05
    assert d["iva_dgi"] == 63.29             # 66 - 2,66 - 0,05
    assert d["te_queda"] == 287.68           # 300 - 12,08 - 0,24
    assert d["dias_habiles"] == 15


def test_lo_que_entra_al_banco_menos_costos_e_iva_es_lo_que_queda():
    """La cuenta tiene que cerrar al centavo: si no, el panel miente."""
    for modo, monto in (("precio", 300), ("total", 1234.56), ("quiero_llevarme", 777)):
        for tarjeta in ("visa_credito", "master_credito", "visa_debito"):
            for moneda in ("USD", "UYU"):
                d = desglosar(modo, monto, tarjeta, AJ, moneda=moneda)
                caja = (d["deposito"] - d["plexo"] - d["plexo_iva"]
                        - d["fijo"] - d["fijo_iva"] - d["iva_dgi"])
                assert round(caja, 2) == d["te_queda"], (modo, tarjeta, moneda)


def test_quiero_llevarme_300_da_el_precio_a_cobrar():
    d = desglosar("quiero_llevarme", 300, "visa_credito", AJ, incluir_fijo=False)
    # (300 + 0,24) / (1 - 0,033 * 1,22) = 312,83
    assert d["precio"] == 312.83
    assert abs(d["te_queda"] - 300) <= 0.02


def test_el_fijo_de_plexo_se_reparte_entre_los_clientes():
    sin = desglosar("quiero_llevarme", 300, "visa_credito", AJ, incluir_fijo=False)
    con = desglosar("quiero_llevarme", 300, "visa_credito", AJ, clientes=5)
    assert con["fijo"] == pytest.approx(20.095, abs=0.01)   # $4.019 / 40 / 5
    assert con["precio"] > sin["precio"] + 20
    assert abs(con["te_queda"] - 300) <= 0.02


def test_total_con_iva_se_desarma_hacia_atras():
    d = desglosar("total", 366, "master_credito", AJ, incluir_fijo=False)
    assert d["precio"] == 300.0
    assert d["comision"] == 12.26            # 3,35% de 366


def test_en_pesos_plexo_no_se_convierte():
    d = desglosar("precio", 10000, "visa_debito", AJ, moneda="UYU",
                  incluir_fijo=False)
    assert d["plexo"] == 9.49
    assert d["comision"] == 128.1            # 1,05% de 12.200
    assert d["dias_habiles"] == 1


def test_sin_comision_cargada_avisa_en_vez_de_suponer_cero():
    with pytest.raises(ValueError, match="falta cargar la comisión"):
        desglosar("precio", 300, "oca_credito", AJ)


def test_monto_invalido():
    with pytest.raises(ValueError):
        desglosar("precio", 0, "visa_credito", AJ)
    with pytest.raises(ValueError):
        desglosar("precio", "abc", "visa_credito", AJ)


def test_dias_habiles_saltean_el_fin_de_semana():
    viernes = date(2026, 9, 25)
    assert sumar_dias_habiles(viernes, 1) == date(2026, 9, 28)   # lunes
    assert sumar_dias_habiles(viernes, 15) == date(2026, 10, 16)


def test_ajustes_guardados_pisan_los_de_fabrica_sin_perder_claves_nuevas():
    aj = unir_ajustes({"comisiones": {"oca_credito": 3.5}, "tipo_cambio": 41})
    assert aj["comisiones"]["oca_credito"] == 3.5
    assert aj["comisiones"]["visa_credito"] == 3.30
    assert aj["tipo_cambio"] == 41
    assert aj["plexo_fijo_uyu"] == AJUSTES_POR_DEFECTO["plexo_fijo_uyu"]


def test_validar_ajustes_rechaza_cosas_raras():
    assert validar_ajustes({"comisiones": {"visa_credito": "x"}})[1]
    assert validar_ajustes({"comisiones": {"visa_credito": 80}})[1]
    assert validar_ajustes({"tipo_cambio": 0})[1]
    ok, error = validar_ajustes({"comisiones": {"oca_credito": "3,5".replace(",", ".")}})
    assert error is None and ok["comisiones"]["oca_credito"] == 3.5


# ── las rutas ────────────────────────────────────────────────────────────────

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


def _cliente_http(app, email, paneles):
    db = app.config["_DB"]
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    conn = sqlite3.connect(db)
    rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)",
                       (email, json.dumps(paneles))).lastrowid
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
    return _cliente_http(app, "socio@scalerics.com", ["finanzas"])


def test_la_calculadora_responde(cli):
    r = cli.get("/api/finanzas/tarjeta/desglose?modo=precio&monto=300"
                "&tarjeta=visa_credito&incluir_fijo=0")
    assert r.status_code == 200
    assert r.get_json()["te_queda"] == 287.68


def test_la_calculadora_avisa_el_error(cli):
    r = cli.get("/api/finanzas/tarjeta/desglose?modo=precio&monto=300&tarjeta=oca_credito")
    assert r.status_code == 400
    assert "comisión" in r.get_json()["error"]


def test_guardar_ajustes_cambia_la_cuenta(cli):
    r = cli.put("/api/finanzas/tarjeta/ajustes", json={
        **AJUSTES_POR_DEFECTO,
        "comisiones": {**AJUSTES_POR_DEFECTO["comisiones"], "oca_credito": 3.5}})
    assert r.status_code == 200
    aj = cli.get("/api/finanzas/tarjeta/ajustes").get_json()["ajustes"]
    assert aj["comisiones"]["oca_credito"] == 3.5
    r = cli.get("/api/finanzas/tarjeta/desglose?modo=precio&monto=300&tarjeta=oca_credito")
    assert r.status_code == 200


def test_registrar_un_cobro_carga_ingreso_comision_plexo_e_iva(app, cli):
    db = app.config["_DB"]
    cid = insert_business(db, {"name": "Panadería López", "phone": "+598700001"})
    r = cli.post("/api/finanzas/cobros-tarjeta", json={
        "modo": "precio", "monto": 300, "tarjeta": "visa_credito",
        "fecha": _hoy(), "concepto": "Mantenimiento", "client_id": cid,
        # Aunque la pantalla lo mande prendido, el fijo no va en el cobro.
        "incluir_fijo": True,
    })
    assert r.status_code == 201, r.get_json()

    movs = sorted(listar_movimientos(db), key=lambda m: m["id"])
    assert len(movs) == 3
    ingreso, comision, plexo = movs
    assert (ingreso["tipo"], ingreso["monto_usd"], ingreso["iva_usd"]) == ("ingreso", 300, 66)
    assert ingreso["client_id"] == cid and ingreso["facturado"] == 1
    assert (comision["tipo"], comision["categoria"], comision["monto_usd"]) == \
        ("egreso", "comisiones", 12.08)
    assert plexo["moneda"] == "UYU" and plexo["monto"] == 9.49 and plexo["facturado"] == 1

    iva = resumen_iva(db, _hoy()[:7])
    assert round(iva["iva_cobrado"], 2) == 66.0
    assert round(iva["iva_pagado"], 2) == round(comision["iva_usd"] + plexo["iva_usd"], 2)

    cobros = cli.get("/api/finanzas/cobros-tarjeta").get_json()
    assert len(cobros) == 1
    assert cobros[0]["deposito"] == 351.26
    assert cobros[0]["client_name"] == "Panadería López"
    assert cobros[0]["acreditado_fecha"] is None


def test_marcar_llegada_y_borrar_el_cobro(app, cli):
    db = app.config["_DB"]
    r = cli.post("/api/finanzas/cobros-tarjeta", json={
        "modo": "precio", "monto": 100, "tarjeta": "master_debito",
        "fecha": _hoy(), "concepto": "Mantenimiento"})
    cid = r.get_json()["id"]

    r = cli.put(f"/api/finanzas/cobros-tarjeta/{cid}/acreditado", json={"llego": True})
    assert r.get_json()["acreditado_fecha"] == _hoy()
    r = cli.put(f"/api/finanzas/cobros-tarjeta/{cid}/acreditado", json={"llego": False})
    assert r.get_json()["acreditado_fecha"] is None

    assert cli.delete(f"/api/finanzas/cobros-tarjeta/{cid}").status_code == 200
    assert listar_movimientos(db) == []
    assert cli.get("/api/finanzas/cobros-tarjeta").get_json() == []


def test_no_se_registra_en_un_mes_cerrado(cli):
    r = cli.post("/api/finanzas/cobros-tarjeta", json={
        "modo": "precio", "monto": 100, "tarjeta": "visa_credito",
        "fecha": "2020-01-15", "concepto": "Viejo"})
    assert r.status_code == 400
    assert "cerrado" in r.get_json()["error"]


def test_sin_concepto_no_se_registra(cli):
    r = cli.post("/api/finanzas/cobros-tarjeta", json={
        "modo": "precio", "monto": 100, "tarjeta": "visa_credito", "fecha": _hoy()})
    assert r.status_code == 400


def test_sin_el_panel_de_finanzas_no_se_ve(app):
    c = _cliente_http(app, "caller@scalerics.com", ["leads"])
    assert c.get("/api/finanzas/tarjeta/desglose?modo=precio&monto=1"
                 "&tarjeta=visa_credito").status_code in (302, 401, 403)
    assert c.post("/api/finanzas/cobros-tarjeta", json={}).status_code in (302, 401, 403)
