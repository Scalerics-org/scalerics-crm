"""El endpoint de IVA y el toggle de facturación en el alta de movimientos."""

import json
import sqlite3
from datetime import date

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, get_movimiento, init_db


def _mes(offset: int = 0) -> str:
    """El período actual desplazado `offset` meses, como 'YYYY-MM'.

    Estas fechas estaban fijas en 2026-09. Desde que un mes pasado queda
    cerrado, una fecha fija pasa mientras el calendario no la deje atrás y
    empieza a fallar el día 1 del mes siguiente. Calcularla al vuelo hace que
    el test siga probando lo mismo en cualquier momento del año.
    """
    hoy = date.today()
    m = hoy.month + offset
    return f"{hoy.year + (m - 1) // 12:04d}-{(m - 1) % 12 + 1:02d}"


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
    """Crea el rol, o le fija los paneles si init_db ya lo sembró."""
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


@pytest.fixture
def cli(app):
    db = app.config["_DB"]
    uid = create_user(db, name="socio", email="socio@scalerics.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    conn = sqlite3.connect(db)
    conn.execute("UPDATE users SET role_id=? WHERE id=?",
                 (_rol(db, "Socio", ["finanzas"]), uid))
    conn.commit()
    conn.close()
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "socio"
    return c


def _crear(cli, concepto, monto, tipo="ingreso", facturado=None, fecha=None):
    fecha = fecha or f"{_mes()}-15"
    cuerpo = {"tipo": tipo, "fecha": fecha, "concepto": concepto,
              "categoria": "otros" if tipo == "egreso" else "otros",
              "monto": monto, "moneda": "USD"}
    if facturado is not None:
        cuerpo["facturado"] = facturado
    return cli.post("/api/finanzas/movimientos", json=cuerpo)


# ── el toggle al crear ───────────────────────────────────────────────────────

def test_marcar_que_se_factura_guarda_el_iva(app, cli):
    r = _crear(cli, "Cobro 50%", 500, facturado=True)

    assert r.status_code == 201
    mov = get_movimiento(app.config["_DB"], r.get_json()["id"])
    assert mov["facturado"] == 1
    assert round(mov["iva_usd"], 2) == 110.0


def test_sin_factura_no_guarda_iva(app, cli):
    r = _crear(cli, "Almuerzo del equipo", 100, tipo="egreso", facturado=False)

    mov = get_movimiento(app.config["_DB"], r.get_json()["id"])
    assert mov["facturado"] == 0
    assert mov["iva_usd"] == 0


def test_si_no_viene_el_campo_no_se_factura(app, cli):
    """Los movimientos que ya existían arrancan sin factura: suponer que sí
    inventaría un IVA que nunca se cobró."""
    r = _crear(cli, "Cargado a la vieja usanza", 500)

    mov = get_movimiento(app.config["_DB"], r.get_json()["id"])
    assert mov["facturado"] == 0
    assert mov["iva_usd"] == 0


def test_editar_puede_apagar_la_factura(app, cli):
    r = _crear(cli, "Cobro", 500, facturado=True)
    mid = r.get_json()["id"]

    cli.put(f"/api/finanzas/movimientos/{mid}",
            json={"tipo": "ingreso", "fecha": f"{_mes()}-15", "concepto": "Cobro",
                  "categoria": "otros", "monto": 500, "moneda": "USD",
                  "facturado": False})

    mov = get_movimiento(app.config["_DB"], mid)
    assert mov["facturado"] == 0
    assert mov["iva_usd"] == 0


def test_editar_el_monto_recalcula_el_iva(app, cli):
    r = _crear(cli, "Cobro", 500, facturado=True)
    mid = r.get_json()["id"]

    cli.put(f"/api/finanzas/movimientos/{mid}",
            json={"tipo": "ingreso", "fecha": f"{_mes()}-15", "concepto": "Cobro",
                  "categoria": "otros", "monto": 1000, "moneda": "USD",
                  "facturado": True})

    assert round(get_movimiento(app.config["_DB"], mid)["iva_usd"], 2) == 220.0


# ── el endpoint ──────────────────────────────────────────────────────────────

def test_devuelve_el_saldo_del_mes(app, cli):
    _crear(cli, "Cobro", 500, facturado=True)
    _crear(cli, "Hosting", 250, tipo="egreso", facturado=True)

    d = cli.get(f"/api/finanzas/iva?periodo={_mes()}").get_json()

    assert round(d["iva_cobrado"], 2) == 110.0
    assert round(d["iva_pagado"], 2) == 55.0
    assert round(d["saldo"], 2) == 55.0
    assert len(d["movimientos"]) == 2


def test_sin_periodo_usa_el_mes_actual(app, cli):
    d = cli.get("/api/finanzas/iva").get_json()

    assert len(d["periodo"]) == 7 and d["periodo"][4] == "-"


@pytest.mark.parametrize("malo", ["2026", "septiembre", "2026/09", ""])
def test_un_periodo_con_formato_raro_da_400(app, cli, malo):
    r = cli.get(f"/api/finanzas/iva?periodo={malo}")
    if malo == "":
        assert r.status_code == 200      # vacio = mes actual
    else:
        assert r.status_code == 400


def test_el_iva_esta_detras_del_permiso_del_panel(app):
    """La plata no se mira sin el panel, igual que el resto del módulo."""
    db = app.config["_DB"]
    uid = create_user(db, name="caller", email="caller@scalerics.com", phone="098",
                      password_hash=generate_password_hash("x" * 10))
    conn = sqlite3.connect(db)
    conn.execute("UPDATE users SET role_id=? WHERE id=?",
                 (_rol(db, "Caller", ["cola"]), uid))
    conn.commit()
    conn.close()
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "caller"

    assert c.get("/api/finanzas/iva").status_code == 403


def test_sin_sesion_no_entra(app):
    assert app.test_client().get("/api/finanzas/iva").status_code in (401, 403, 302)
