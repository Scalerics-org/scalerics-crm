"""El toggle de IVA en el alta y la edición de gastos fijos.

Lo que se prueba acá y no en `test_finanzas_fijos_iva.py`: que la ruta acepte
`facturado`, y —lo importante— que **omitirlo no lo apague**. Es la misma
regla que ya protege a `activo`: un PUT que solo cambia el monto no puede
tocar campos que nadie mencionó. Si lo apagara, editarle el precio a un fijo
facturado le sacaría el IVA en silencio a partir del mes siguiente.
"""

import json
import sqlite3
from datetime import date

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, get_recurrente, init_db


def _mes(offset: int = 0) -> str:
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


def _cuerpo(**extra):
    base = {"tipo": "egreso", "concepto": "Hosting", "categoria": "infraestructura",
            "monto": 100, "moneda": "USD", "dia_del_mes": 1, "desde": _mes()}
    base.update(extra)
    return base


def _crear(cli, **extra):
    return cli.post("/api/finanzas/recurrentes", json=_cuerpo(**extra))


# ── alta ─────────────────────────────────────────────────────────────────────

def test_crear_un_fijo_con_iva(app, cli):
    r = _crear(cli, facturado=True)

    assert r.status_code == 201
    assert get_recurrente(app.config["_DB"], r.get_json()["id"])["facturado"] == 1


def test_crear_un_fijo_sin_marcar_nada_lo_deja_sin_iva(app, cli):
    """El default es "no lleva": es lo que no inventa impuesto."""
    r = _crear(cli)

    assert get_recurrente(app.config["_DB"], r.get_json()["id"])["facturado"] == 0


def test_crear_un_fijo_con_el_toggle_apagado(app, cli):
    r = _crear(cli, facturado=False)

    assert get_recurrente(app.config["_DB"], r.get_json()["id"])["facturado"] == 0


# ── edición ──────────────────────────────────────────────────────────────────

def test_prenderle_el_iva_a_un_fijo_que_ya_existia(app, cli):
    db = app.config["_DB"]
    rid = _crear(cli).get_json()["id"]

    r = cli.put(f"/api/finanzas/recurrentes/{rid}", json=_cuerpo(facturado=True))

    assert r.status_code == 200
    assert get_recurrente(db, rid)["facturado"] == 1


def test_apagarle_el_iva(app, cli):
    db = app.config["_DB"]
    rid = _crear(cli, facturado=True).get_json()["id"]

    cli.put(f"/api/finanzas/recurrentes/{rid}", json=_cuerpo(facturado=False))

    assert get_recurrente(db, rid)["facturado"] == 0


def test_editar_el_monto_sin_mandar_facturado_no_le_saca_el_iva(app, cli):
    """El motivo por el que este archivo existe.

    Mismo criterio que `activo`: si omitir la clave la apagara, cualquier PUT
    parcial le sacaría el IVA a un fijo facturado sin que nadie lo pida, y el
    error recién se vería el mes siguiente, en la materialización.
    """
    db = app.config["_DB"]
    rid = _crear(cli, facturado=True).get_json()["id"]

    r = cli.put(f"/api/finanzas/recurrentes/{rid}", json=_cuerpo(monto=120))

    assert r.status_code == 200
    fijo = get_recurrente(db, rid)
    assert fijo["monto"] == 120, "el monto sí cambió"
    assert fijo["facturado"] == 1, "y el IVA quedó como estaba"


# ── el fijo se lista con su marca ────────────────────────────────────────────

def test_el_listado_dice_cuales_llevan_iva(app, cli):
    _crear(cli, concepto="Hosting", facturado=True)
    _crear(cli, concepto="Netflix", facturado=False)

    fijos = {f["concepto"]: f for f in cli.get("/api/finanzas/recurrentes").get_json()}

    assert fijos["Hosting"]["facturado"] == 1
    assert fijos["Netflix"]["facturado"] == 0
