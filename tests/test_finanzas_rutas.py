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
from database import create_user, crear_recurrente, init_db


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


def test_un_tipo_de_cambio_que_no_es_un_numero_da_400_en_movimiento(cli):
    r = cli.post("/api/finanzas/movimientos", json={
        "tipo": "ingreso", "fecha": "2026-09-01", "concepto": "Cobro",
        "categoria": "desarrollo_web", "monto": 40000, "moneda": "UYU",
        "tipo_cambio": [40, 41]})
    assert r.status_code == 400


def test_un_tipo_de_cambio_que_no_es_un_numero_da_400_en_fijo(cli):
    r = cli.post("/api/finanzas/recurrentes", json={
        "tipo": "egreso", "concepto": "x", "categoria": "servicios",
        "monto": 10, "moneda": "UYU", "tipo_cambio": [40, 41],
        "dia_del_mes": 1, "desde": "2026-09"})
    assert r.status_code == 400


def test_editar_un_fijo_apagado_sin_tocar_activo_lo_deja_apagado(cli):
    r = cli.post("/api/finanzas/recurrentes", json={
        "tipo": "egreso", "concepto": "Fly", "categoria": "infraestructura",
        "monto": 4.18, "moneda": "USD", "dia_del_mes": 1, "desde": "2026-09",
        "activo": False})
    rid = r.get_json()["id"]
    cli.put(f"/api/finanzas/recurrentes/{rid}", json={
        "tipo": "egreso", "concepto": "Fly", "categoria": "infraestructura",
        "monto": 5.0, "moneda": "USD", "dia_del_mes": 1, "desde": "2026-09"})
    fijo = [f for f in cli.get("/api/finanzas/recurrentes").get_json()
            if f["id"] == rid][0]
    assert fijo["activo"] == 0
    assert fijo["monto"] == 5.0


def test_editar_un_fijo_con_activo_true_explicito_lo_enciende(cli):
    r = cli.post("/api/finanzas/recurrentes", json={
        "tipo": "egreso", "concepto": "Fly", "categoria": "infraestructura",
        "monto": 4.18, "moneda": "USD", "dia_del_mes": 1, "desde": "2026-09",
        "activo": False})
    rid = r.get_json()["id"]
    cli.put(f"/api/finanzas/recurrentes/{rid}", json={
        "tipo": "egreso", "concepto": "Fly", "categoria": "infraestructura",
        "monto": 4.18, "moneda": "USD", "dia_del_mes": 1, "desde": "2026-09",
        "activo": True})
    fijo = [f for f in cli.get("/api/finanzas/recurrentes").get_json()
            if f["id"] == rid][0]
    assert fijo["activo"] == 1


def test_editar_un_movimiento_sin_mandar_client_id_conserva_el_que_tenia(cli):
    r = cli.post("/api/finanzas/movimientos", json={
        "tipo": "ingreso", "fecha": "2026-09-01", "concepto": "Cobro",
        "categoria": "desarrollo_web", "monto": 100, "moneda": "USD",
        "client_id": 7})
    mid = r.get_json()["id"]
    cli.put(f"/api/finanzas/movimientos/{mid}", json={
        "tipo": "ingreso", "fecha": "2026-09-01", "concepto": "Cobro editado",
        "categoria": "desarrollo_web", "monto": 100, "moneda": "USD"})
    mov = cli.get("/api/finanzas/movimientos").get_json()[0]
    assert mov["client_id"] == 7
    assert mov["concepto"] == "Cobro editado"


def test_editar_un_movimiento_con_client_id_null_lo_desatribuye(cli):
    r = cli.post("/api/finanzas/movimientos", json={
        "tipo": "ingreso", "fecha": "2026-09-01", "concepto": "Cobro",
        "categoria": "desarrollo_web", "monto": 100, "moneda": "USD",
        "client_id": 7})
    mid = r.get_json()["id"]
    cli.put(f"/api/finanzas/movimientos/{mid}", json={
        "tipo": "ingreso", "fecha": "2026-09-01", "concepto": "Cobro",
        "categoria": "desarrollo_web", "monto": 100, "moneda": "USD",
        "client_id": None})
    mov = cli.get("/api/finanzas/movimientos").get_json()[0]
    assert mov["client_id"] is None


def test_mover_de_mes_un_movimiento_generado_por_un_fijo_da_400(cli):
    """Mover la fecha de un movimiento de fijo a otro mes o revienta el
    índice único (si el mes destino ya tiene su fila) o libera el par
    (recurrente_id, periodo) del mes de origen -la próxima materialización
    lo regenera ahí y el gasto queda contado dos veces sin que nada falle."""
    cli.post("/api/finanzas/recurrentes", json={
        "tipo": "egreso", "concepto": "Fly", "categoria": "infraestructura",
        "monto": 4.18, "moneda": "USD", "dia_del_mes": 1, "desde": "2026-09"})
    cli.get("/api/finanzas/resumen")  # materializa el mes actual
    mov = cli.get("/api/finanzas/movimientos", query_string={
        "desde": "2026-01", "hasta": "2026-12"}).get_json()[0]
    assert mov["recurrente_id"] is not None

    r = cli.put(f"/api/finanzas/movimientos/{mov['id']}", json={
        "tipo": mov["tipo"], "fecha": "2026-10-15", "concepto": mov["concepto"],
        "categoria": mov["categoria"], "monto": mov["monto"], "moneda": mov["moneda"]})
    assert r.status_code == 400
    assert "otro mes" in r.get_json()["error"]

    # No se movió ni se tocó.
    lista = cli.get("/api/finanzas/movimientos", query_string={
        "desde": "2026-01", "hasta": "2026-12"}).get_json()
    intacto = [m for m in lista if m["id"] == mov["id"]][0]
    assert intacto["periodo"] == mov["periodo"]
    assert intacto["fecha"] == mov["fecha"]


def test_editar_el_monto_de_un_movimiento_de_fijo_sin_cambiar_el_mes_funciona(cli):
    cli.post("/api/finanzas/recurrentes", json={
        "tipo": "egreso", "concepto": "Fly", "categoria": "infraestructura",
        "monto": 4.18, "moneda": "USD", "dia_del_mes": 1, "desde": "2026-09"})
    cli.get("/api/finanzas/resumen")
    mov = cli.get("/api/finanzas/movimientos", query_string={
        "desde": "2026-01", "hasta": "2026-12"}).get_json()[0]

    r = cli.put(f"/api/finanzas/movimientos/{mov['id']}", json={
        "tipo": mov["tipo"], "fecha": mov["fecha"], "concepto": mov["concepto"],
        "categoria": mov["categoria"], "monto": 9.99, "moneda": mov["moneda"]})
    assert r.status_code == 200

    editado = [m for m in cli.get("/api/finanzas/movimientos", query_string={
        "desde": "2026-01", "hasta": "2026-12"}).get_json() if m["id"] == mov["id"]][0]
    assert editado["monto"] == 9.99
    assert editado["periodo"] == mov["periodo"]


def test_un_fijo_en_pesos_sin_tipo_de_cambio_viene_con_monto_usd_null(cli, app):
    """El panel no convierte: sin un tipo de cambio usable, se marca, no se
    inventa una cuenta a 1 peso por dólar."""
    db = app.config["_DB"]
    crear_recurrente(db, tipo="egreso", concepto="Alquiler",
                      categoria="servicios", monto=40000, moneda="UYU",
                      tipo_cambio=None, dia_del_mes=1, desde="2026-09")
    fijo = cli.get("/api/finanzas/recurrentes").get_json()[0]
    assert fijo["monto_usd"] is None


def test_un_fijo_en_pesos_con_tipo_de_cambio_trae_el_monto_usd_correcto(cli, app):
    db = app.config["_DB"]
    crear_recurrente(db, tipo="egreso", concepto="Alquiler",
                      categoria="servicios", monto=40000, moneda="UYU",
                      tipo_cambio=40, dia_del_mes=1, desde="2026-09")
    fijo = cli.get("/api/finanzas/recurrentes").get_json()[0]
    assert fijo["monto_usd"] == 1000.0
