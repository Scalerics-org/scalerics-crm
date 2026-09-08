"""Permisos y endpoints de la sección financiera.

El `panel_access` del CRM solo escondía el ítem del menú: nada frenaba un
fetch de un usuario logueado sin ese panel. Para los leads es tolerable; para
la plata no.
"""

import json
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db


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


def _usuario(db, email, role_id):
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    conn = sqlite3.connect(db)
    conn.execute("UPDATE users SET role_id=? WHERE id=?", (role_id, uid))
    conn.commit()
    conn.close()
    return uid


def _cli(app, uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "test"
    return c


def test_sin_el_panel_finanzas_da_403(app):
    db = app.config["_DB"]
    uid = _usuario(db, "caller@scalerics.com", _rol(db, "Caller", ["cola"]))
    assert _cli(app, uid).get("/api/finanzas/resumen").status_code == 403


def test_con_el_panel_finanzas_entra(app):
    db = app.config["_DB"]
    uid = _usuario(db, "socio@scalerics.com",
                   _rol(db, "Socio", ["cola", "finanzas"]))
    assert _cli(app, uid).get("/api/finanzas/resumen").status_code == 200


def test_un_admin_entra_aunque_su_rol_no_lo_liste(app):
    db = app.config["_DB"]
    uid = _usuario(db, "raiz@scalerics.com", _rol(db, "Caller", ["cola"]))
    assert _cli(app, uid).get("/api/finanzas/resumen").status_code == 200


def test_sin_sesion_no_entra(app):
    r = app.test_client().get("/api/finanzas/resumen")
    assert r.status_code in (401, 302)


@pytest.fixture
def cli(app):
    db = app.config["_DB"]
    uid = _usuario(db, "socio@scalerics.com",
                   _rol(db, "Socio", ["cola", "finanzas"]))
    return _cli(app, uid)


def test_crear_un_movimiento_en_dolares(cli):
    r = cli.post("/api/finanzas/movimientos", json={
        "tipo": "egreso", "fecha": "2026-09-20", "concepto": "Fly",
        "categoria": "infraestructura", "monto": 4.18, "moneda": "USD"})
    assert r.status_code == 201
    assert r.get_json()["ok"] is True

    lista = cli.get("/api/finanzas/movimientos").get_json()
    assert lista[0]["monto_usd"] == 4.18
    assert lista[0]["periodo"] == "2026-09"


def test_crear_en_pesos_congela_el_monto_en_dolares(cli):
    cli.post("/api/finanzas/movimientos", json={
        "tipo": "ingreso", "fecha": "2026-09-01", "concepto": "Cobro",
        "categoria": "desarrollo_web", "monto": 40000, "moneda": "UYU",
        "tipo_cambio": 40})
    assert cli.get("/api/finanzas/movimientos").get_json()[0]["monto_usd"] == 1000.0


def test_pesos_sin_tipo_de_cambio_da_400(cli):
    r = cli.post("/api/finanzas/movimientos", json={
        "tipo": "ingreso", "fecha": "2026-09-01", "concepto": "Cobro",
        "categoria": "desarrollo_web", "monto": 40000, "moneda": "UYU"})
    assert r.status_code == 400


def test_una_categoria_que_no_existe_da_400(cli):
    r = cli.post("/api/finanzas/movimientos", json={
        "tipo": "egreso", "fecha": "2026-09-01", "concepto": "x",
        "categoria": "inventada", "monto": 10, "moneda": "USD"})
    assert r.status_code == 400


def test_una_categoria_de_ingreso_en_un_egreso_da_400(cli):
    r = cli.post("/api/finanzas/movimientos", json={
        "tipo": "egreso", "fecha": "2026-09-01", "concepto": "x",
        "categoria": "desarrollo_web", "monto": 10, "moneda": "USD"})
    assert r.status_code == 400


def test_borrar_un_movimiento_a_mano_lo_borra(cli):
    cli.post("/api/finanzas/movimientos", json={
        "tipo": "egreso", "fecha": "2026-09-20", "concepto": "Dominio",
        "categoria": "servicios", "monto": 15, "moneda": "USD"})
    mid = cli.get("/api/finanzas/movimientos").get_json()[0]["id"]
    assert cli.delete(f"/api/finanzas/movimientos/{mid}").status_code == 200
    assert cli.get("/api/finanzas/movimientos").get_json() == []


def test_borrar_un_movimiento_de_un_fijo_lo_anula_y_no_reaparece(cli):
    cli.post("/api/finanzas/recurrentes", json={
        "tipo": "egreso", "concepto": "Fly", "categoria": "infraestructura",
        "monto": 4.18, "moneda": "USD", "dia_del_mes": 20, "desde": "2026-09"})
    cli.get("/api/finanzas/resumen")  # materializa
    mid = cli.get("/api/finanzas/movimientos").get_json()[0]["id"]
    cli.delete(f"/api/finanzas/movimientos/{mid}")
    cli.get("/api/finanzas/resumen")  # vuelve a materializar
    assert cli.get("/api/finanzas/movimientos").get_json() == []


def test_el_resumen_materializa_los_fijos(cli):
    cli.post("/api/finanzas/recurrentes", json={
        "tipo": "egreso", "concepto": "Fly", "categoria": "infraestructura",
        "monto": 4.18, "moneda": "USD", "dia_del_mes": 1, "desde": "2026-09"})
    assert cli.get("/api/finanzas/resumen").status_code == 200
    assert cli.get("/api/finanzas/movimientos").get_json() != []


def test_las_categorias_se_sirven_al_front(cli):
    cats = cli.get("/api/finanzas/categorias").get_json()
    assert "infraestructura" in cats["egreso"]
    assert "desarrollo_web" in cats["ingreso"]


def test_un_dia_del_mes_mayor_a_28_da_400(cli):
    r = cli.post("/api/finanzas/recurrentes", json={
        "tipo": "egreso", "concepto": "x", "categoria": "servicios",
        "monto": 10, "moneda": "USD", "dia_del_mes": 31, "desde": "2026-09"})
    assert r.status_code == 400


def test_el_resumen_rechaza_el_rango_al_reves(cli):
    r = cli.get("/api/finanzas/resumen?desde=2026-09&hasta=2026-08")
    assert r.status_code == 400
