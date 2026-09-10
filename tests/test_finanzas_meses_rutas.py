"""Que un mes cerrado no se toque, y los endpoints para abrirlo y cerrarlo.

El candado es lo que le da sentido al "mes cerrado": si la pestaña dijera
"cerrado" pero el PUT pasara igual, sería una etiqueta, no una protección.
"""

import json
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import (create_user, crear_movimiento, get_movimiento, init_db,
                      listar_meses_abiertos)


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


# Un mes que ya pasó seguro, sin depender de cuándo se corran los tests.
VIEJO = "2020-03"


def _mov_viejo(db, concepto="Gasto viejo"):
    return crear_movimiento(db, tipo="egreso", fecha=f"{VIEJO}-10", periodo=VIEJO,
                            concepto=concepto, categoria="otros", monto=100,
                            moneda="USD", monto_usd=100)


def _cuerpo(periodo=VIEJO, concepto="Editado"):
    return {"tipo": "egreso", "fecha": f"{periodo}-10", "concepto": concepto,
            "categoria": "otros", "monto": 100, "moneda": "USD"}


# ── el candado ───────────────────────────────────────────────────────────────

def test_no_se_crea_un_movimiento_en_un_mes_cerrado(app, cli):
    r = cli.post("/api/finanzas/movimientos", json=_cuerpo())

    assert r.status_code == 400
    assert "cerrado" in r.get_json()["error"].lower()


def test_no_se_edita_un_movimiento_de_un_mes_cerrado(app, cli):
    db = app.config["_DB"]
    mid = _mov_viejo(db)

    r = cli.put(f"/api/finanzas/movimientos/{mid}", json=_cuerpo(concepto="Cambiado"))

    assert r.status_code == 400
    assert get_movimiento(db, mid)["concepto"] == "Gasto viejo"


def test_no_se_borra_un_movimiento_de_un_mes_cerrado(app, cli):
    db = app.config["_DB"]
    mid = _mov_viejo(db)

    r = cli.delete(f"/api/finanzas/movimientos/{mid}")

    assert r.status_code == 400
    assert get_movimiento(db, mid) is not None


def test_tampoco_se_puede_mover_un_movimiento_hacia_un_mes_cerrado(app, cli):
    """El destino cuenta igual que el origen: si no, se edita un mes cerrado
    entrandole por la puerta de atras."""
    db = app.config["_DB"]
    hoy = cli.get("/api/finanzas/meses").get_json()["mes_actual"]
    mid = crear_movimiento(db, tipo="egreso", fecha=f"{hoy}-10", periodo=hoy,
                           concepto="Del mes", categoria="otros", monto=100,
                           moneda="USD", monto_usd=100)

    r = cli.put(f"/api/finanzas/movimientos/{mid}", json=_cuerpo())

    assert r.status_code == 400
    assert get_movimiento(db, mid)["periodo"] == hoy


def test_en_el_mes_en_curso_se_edita_normal(app, cli):
    hoy = cli.get("/api/finanzas/meses").get_json()["mes_actual"]

    r = cli.post("/api/finanzas/movimientos", json=_cuerpo(periodo=hoy))

    assert r.status_code == 201


# ── reabrir y volver a cerrar ────────────────────────────────────────────────

def test_reabrir_deja_editar_ese_mes(app, cli):
    db = app.config["_DB"]

    assert cli.post(f"/api/finanzas/meses/{VIEJO}/reabrir").status_code == 200
    r = cli.post("/api/finanzas/movimientos", json=_cuerpo())

    assert r.status_code == 201
    assert listar_meses_abiertos(db)[0]["periodo"] == VIEJO


def test_cerrar_vuelve_a_trabar(app, cli):
    cli.post(f"/api/finanzas/meses/{VIEJO}/reabrir")
    assert cli.post(f"/api/finanzas/meses/{VIEJO}/cerrar").status_code == 200

    assert cli.post("/api/finanzas/movimientos", json=_cuerpo()).status_code == 400


def test_queda_registrado_quien_lo_reabrio(app, cli):
    db = app.config["_DB"]
    cli.post(f"/api/finanzas/meses/{VIEJO}/reabrir")

    assert listar_meses_abiertos(db)[0]["abierto_por"] == "socio"


@pytest.mark.parametrize("malo", ["2026", "septiembre", "2026-13-01"])
def test_un_periodo_con_formato_raro_da_400(app, cli, malo):
    assert cli.post(f"/api/finanzas/meses/{malo}/reabrir").status_code == 400


# ── el navegador ─────────────────────────────────────────────────────────────

def test_meses_dice_cual_es_el_actual_y_cuales_tienen_datos(app, cli):
    db = app.config["_DB"]
    _mov_viejo(db)

    d = cli.get("/api/finanzas/meses").get_json()

    assert len(d["mes_actual"]) == 7
    assert VIEJO in d["con_datos"]
    assert d["abiertos"] == []


def test_meses_lista_los_reabiertos(app, cli):
    cli.post(f"/api/finanzas/meses/{VIEJO}/reabrir")

    assert cli.get("/api/finanzas/meses").get_json()["abiertos"] == [VIEJO]


# ── permisos ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("metodo,ruta", [
    ("get", "/api/finanzas/meses"),
    ("post", "/api/finanzas/meses/2020-03/reabrir"),
    ("post", "/api/finanzas/meses/2020-03/cerrar"),
])
def test_todo_esta_detras_del_permiso_del_panel(app, metodo, ruta):
    c = _con_panel(app, "caller@scalerics.com", ["cola"])
    assert getattr(c, metodo)(ruta, json={}).status_code == 403


def test_no_se_cobra_un_pendiente_hacia_un_mes_cerrado(app, cli):
    """Cobrar crea un ingreso: no puede entrar por la puerta de atrás."""
    from database import crear_por_cobrar, listar_por_cobrar
    db = app.config["_DB"]
    crear_por_cobrar(db, concepto="Saldo", monto_usd=500)
    pid = listar_por_cobrar(db)[0]["id"]

    r = cli.post(f"/api/finanzas/por-cobrar/{pid}/cobrar",
                 json={"fecha": f"{VIEJO}-10"})

    assert r.status_code == 400
    assert "cerrado" in r.get_json()["error"].lower()
    assert listar_por_cobrar(db)[0]["cobrado_movimiento_id"] is None
