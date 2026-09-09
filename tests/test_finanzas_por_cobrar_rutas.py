"""Endpoints de "Por cobrar" y el pendiente que nace de un cobro parcial."""

import json
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import (create_user, get_movimiento, init_db, insert_business,
                      listar_por_cobrar)


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


def _con_panel(app, email, paneles):
    db = app.config["_DB"]
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    conn = sqlite3.connect(db)
    conn.execute("UPDATE users SET role_id=? WHERE id=?",
                 (_rol(db, "Socio" if "finanzas" in paneles else "Caller", paneles), uid))
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
    return _con_panel(app, "socio@scalerics.com", ["finanzas"])


_tel = iter(range(700000, 799999))


def _cliente(db, nombre):
    return insert_business(db, {"name": nombre, "phone": f"+598{next(_tel)}"})


# ── el pendiente nace del cobro parcial ──────────────────────────────────────

def test_un_cobro_parcial_genera_el_pendiente(app, cli):
    db = app.config["_DB"]
    cid = _cliente(db, "La Vaca Encantada")

    r = cli.post("/api/finanzas/movimientos", json={
        "tipo": "ingreso", "fecha": "2026-09-09", "concepto": "50% inicial",
        "categoria": "desarrollo_web", "monto": 500, "moneda": "USD",
        "client_id": cid,
        "total_acordado": 1000, "vence_resto": "2026-10-18",
    })

    assert r.status_code == 201
    pendientes = listar_por_cobrar(db)
    assert len(pendientes) == 1
    p = pendientes[0]
    assert p["monto_usd"] == 500, "1000 acordados menos 500 cobrados"
    assert p["vence"] == "2026-10-18"
    # No hereda el concepto tal cual: el pendiente es el RESTO, no el cobro que
    # se acaba de hacer. Heredarlo dejaba dos movimientos "50% inicial".
    assert p["concepto"] == "Saldo de 50% inicial"
    assert p["client_id"] == cid
    assert p["origen_movimiento_id"] == r.get_json()["id"]


def test_sin_total_acordado_no_genera_nada(app, cli):
    """Un cobro normal no deja un pendiente fantasma."""
    db = app.config["_DB"]
    cli.post("/api/finanzas/movimientos", json={
        "tipo": "ingreso", "fecha": "2026-09-09", "concepto": "Cobro entero",
        "categoria": "otros", "monto": 500, "moneda": "USD"})

    assert listar_por_cobrar(db) == []


def test_si_el_total_es_igual_a_lo_cobrado_no_queda_pendiente(app, cli):
    db = app.config["_DB"]
    cli.post("/api/finanzas/movimientos", json={
        "tipo": "ingreso", "fecha": "2026-09-09", "concepto": "Cobro entero",
        "categoria": "otros", "monto": 1000, "moneda": "USD",
        "total_acordado": 1000, "vence_resto": "2026-10-18"})

    assert listar_por_cobrar(db) == []


def test_un_total_menor_a_lo_cobrado_es_un_error(app, cli):
    """Cobrar 500 de un total de 300 no tiene sentido: es un dato mal puesto."""
    db = app.config["_DB"]
    r = cli.post("/api/finanzas/movimientos", json={
        "tipo": "ingreso", "fecha": "2026-09-09", "concepto": "Cobro",
        "categoria": "otros", "monto": 500, "moneda": "USD",
        "total_acordado": 300, "vence_resto": "2026-10-18"})

    assert r.status_code == 400
    assert listar_por_cobrar(db) == []


def test_un_egreso_no_genera_un_pendiente_de_cobro(app, cli):
    """Lo que se paga no queda por cobrar."""
    db = app.config["_DB"]
    cli.post("/api/finanzas/movimientos", json={
        "tipo": "egreso", "fecha": "2026-09-09", "concepto": "Hosting",
        "categoria": "infraestructura", "monto": 100, "moneda": "USD",
        "total_acordado": 500, "vence_resto": "2026-10-18"})

    assert listar_por_cobrar(db) == []


# ── el listado ───────────────────────────────────────────────────────────────

def test_el_listado_trae_el_estado_de_vencimiento(app, cli):
    db = app.config["_DB"]
    cid = _cliente(db, "La Vaca Encantada")
    cli.post("/api/finanzas/movimientos", json={
        "tipo": "ingreso", "fecha": "2026-09-09", "concepto": "50% inicial",
        "categoria": "otros", "monto": 500, "moneda": "USD", "client_id": cid,
        "total_acordado": 1000, "vence_resto": "2030-01-15"})

    d = cli.get("/api/finanzas/por-cobrar").get_json()

    assert d["total_usd"] == 500
    fila = d["pendientes"][0]
    assert fila["client_name"] == "La Vaca Encantada"
    assert fila["vencido"] is False
    assert "vence" in fila["texto"]


def test_el_total_es_lo_que_va_en_la_card(app, cli):
    db = app.config["_DB"]
    for monto in (500, 300):
        cid = _cliente(db, f"Cliente {monto}")
        cli.post("/api/finanzas/movimientos", json={
            "tipo": "ingreso", "fecha": "2026-09-09", "concepto": "50%",
            "categoria": "otros", "monto": monto, "moneda": "USD",
            "client_id": cid, "total_acordado": monto * 2,
            "vence_resto": "2030-01-15"})

    assert cli.get("/api/finanzas/por-cobrar").get_json()["total_usd"] == 800


# ── cobrar ───────────────────────────────────────────────────────────────────

def _un_pendiente(app, cli, monto=500):
    db = app.config["_DB"]
    cid = _cliente(db, "La Vaca Encantada")
    cli.post("/api/finanzas/movimientos", json={
        "tipo": "ingreso", "fecha": "2026-09-09", "concepto": "50% inicial",
        "categoria": "otros", "monto": monto, "moneda": "USD", "client_id": cid,
        "total_acordado": monto * 2, "vence_resto": "2030-01-15"})
    return listar_por_cobrar(db)[0]["id"]


def test_cobrar_crea_el_ingreso_y_lo_saca_del_listado(app, cli):
    db = app.config["_DB"]
    pid = _un_pendiente(app, cli, monto=500)

    r = cli.post(f"/api/finanzas/por-cobrar/{pid}/cobrar",
                 json={"fecha": "2026-09-20"})

    assert r.status_code == 201
    mov = get_movimiento(db, r.get_json()["movimiento_id"])
    assert mov["tipo"] == "ingreso" and mov["monto_usd"] == 500
    assert listar_por_cobrar(db) == []


def test_cobrar_facturado_guarda_el_iva(app, cli):
    db = app.config["_DB"]
    pid = _un_pendiente(app, cli, monto=1220)

    r = cli.post(f"/api/finanzas/por-cobrar/{pid}/cobrar",
                 json={"fecha": "2026-09-20", "facturado": True})

    mov = get_movimiento(db, r.get_json()["movimiento_id"])
    assert round(mov["iva_usd"], 2) == 220.0


def test_cobrar_dos_veces_da_400(app, cli):
    pid = _un_pendiente(app, cli)
    cli.post(f"/api/finanzas/por-cobrar/{pid}/cobrar", json={"fecha": "2026-09-20"})

    r = cli.post(f"/api/finanzas/por-cobrar/{pid}/cobrar", json={"fecha": "2026-09-21"})

    assert r.status_code == 400


def test_cobrar_uno_que_no_existe_da_400(app, cli):
    r = cli.post("/api/finanzas/por-cobrar/9999/cobrar", json={"fecha": "2026-09-20"})
    assert r.status_code == 400


def test_una_fecha_invalida_al_cobrar_no_crea_nada(app, cli):
    db = app.config["_DB"]
    pid = _un_pendiente(app, cli)

    r = cli.post(f"/api/finanzas/por-cobrar/{pid}/cobrar", json={"fecha": "ayer"})

    assert r.status_code == 400
    assert len(listar_por_cobrar(db)) == 1


def test_borrar_un_pendiente_mal_cargado(app, cli):
    db = app.config["_DB"]
    pid = _un_pendiente(app, cli)

    assert cli.delete(f"/api/finanzas/por-cobrar/{pid}").status_code == 200
    assert listar_por_cobrar(db) == []


# ── permisos ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("metodo,ruta", [
    ("get", "/api/finanzas/por-cobrar"),
    ("post", "/api/finanzas/por-cobrar/1/cobrar"),
    ("delete", "/api/finanzas/por-cobrar/1"),
])
def test_todo_esta_detras_del_permiso_del_panel(app, metodo, ruta):
    """La plata no se mira ni se mueve sin el panel."""
    c = _con_panel(app, "caller@scalerics.com", ["cola"])
    assert getattr(c, metodo)(ruta, json={}).status_code == 403
