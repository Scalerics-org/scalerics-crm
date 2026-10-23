"""Eliminar cliente (Juan, 23/10): lo saca de Clientes sin borrarlo.

Se archiva (`archivado_en`): desaparece de Clientes, de los selectores de
cliente y de los cierres de Ventas, y su historial y lo que tenga en Finanzas
quedan como estaban.
"""

import re
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, get_business, init_db, insert_business, update_business
from services.finanzas import ventas_del_mes


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    db = str(tmp_path / "e.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


@pytest.fixture
def cli(app):
    uid = create_user(app.config["_DB"], name="jefe", email="jefe@test.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Jefe"
    return c


_tel = iter(range(700000, 799999))


def _negocio(db, nombre, etapa):
    bid = insert_business(db, {"name": nombre, "phone": f"+598{next(_tel)}"})
    update_business(db, bid, crm_status=etapa)
    return bid


def _sql(db, q, params=()):
    c = sqlite3.connect(db)
    try:
        r = c.execute(q, params).fetchall()
        c.commit()
        return r
    finally:
        c.close()


def test_eliminar_lo_saca_de_clientes_y_conserva_todo(app, cli):
    db = app.config["_DB"]
    bid = _negocio(db, "Cliente A", "en_desarrollo")
    otro = _negocio(db, "Cliente B", "cerrado")
    _sql(db, "INSERT INTO finanzas_movimientos (tipo, fecha, periodo, concepto, categoria, monto, "
             "moneda, monto_usd, client_id) VALUES ('ingreso', '2026-10-01', '2026-10', 'cobro', "
             "'desarrollo_web', 500, 'USD', 500, ?)", (bid,))

    assert cli.delete(f"/api/clientes-activos/{bid}").status_code == 200

    nombres = [c["name"] for c in cli.get("/api/clientes-activos").get_json()["clientes"]]
    assert nombres == ["Cliente B"]
    # Tampoco aparece en los selectores de cliente (Finanzas, Ventas).
    leads = cli.get("/api/leads?crm_group=clientes").get_json()
    leads = leads if isinstance(leads, list) else leads.get("items", [])
    assert bid not in [b["id"] for b in leads] and otro in [b["id"] for b in leads]
    # No se borró nada: el negocio, su etapa y su plata siguen.
    biz = get_business(db, bid)
    assert biz["crm_status"] == "en_desarrollo" and biz["archivado_en"]
    assert _sql(db, "SELECT COUNT(*) FROM finanzas_movimientos WHERE client_id = ?", (bid,))[0][0] == 1
    # Y queda anotado quién lo sacó.
    assert _sql(db, "SELECT COUNT(*) FROM activity_log WHERE action = 'cliente_quitado' "
                    "AND entity_id = ?", (bid,))[0][0] == 1


def test_eliminar_dos_veces_o_algo_que_no_es_cliente_da_404(app, cli):
    db = app.config["_DB"]
    bid = _negocio(db, "Cliente A", "cerrado")
    lead = _negocio(db, "Lead", "demo_agendada")
    assert cli.delete(f"/api/clientes-activos/{bid}").status_code == 200
    assert cli.delete(f"/api/clientes-activos/{bid}").status_code == 404
    assert cli.delete(f"/api/clientes-activos/{lead}").status_code == 404
    assert cli.delete("/api/clientes-activos/99999").status_code == 404
    assert get_business(db, lead)["archivado_en"] is None


def test_un_cliente_eliminado_no_figura_como_cierre_en_ventas(app, cli):
    db = app.config["_DB"]
    bid = _negocio(db, "Cliente A", "cerrado")
    _sql(db, "INSERT INTO lead_events (lead_id, new_status, note, created_at, created_by) "
             "VALUES (?, 'cerrado', '', '2026-10-05 15:00:00', 'test')", (bid,))
    assert ventas_del_mes(db, "2026-10")["cantidad"] == 1
    cli.delete(f"/api/clientes-activos/{bid}")
    assert ventas_del_mes(db, "2026-10")["cantidad"] == 0


def test_sin_sesion_no_se_puede_eliminar(app):
    db = app.config["_DB"]
    bid = _negocio(db, "Cliente A", "cerrado")
    r = app.test_client().delete(f"/api/clientes-activos/{bid}")
    assert r.status_code in (302, 401, 403)
    assert get_business(db, bid)["archivado_en"] is None


def test_el_panel_tiene_el_boton_y_avisa_que_no_se_borra():
    html = dashboard.DASHBOARD_HTML
    panel = re.search(r"async function loadClientesPanel\(\) \{.*?\n\}", html, re.S).group(0)
    assert "cliEliminar(${b.id}" in panel
    eliminar = re.search(r"async function cliEliminar\(clientId, nombre\) \{.*?\n\}", html, re.S).group(0)
    assert "confirm(" in eliminar and "queda guardado" in eliminar
    assert "method: 'DELETE'" in eliminar
