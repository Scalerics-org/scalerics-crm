"""Roles Contador y Marketing (pedido de Juan, 15/9).

Hoy en producción hay Admin, SDR y Programador; se suman Contador y Marketing.
Se crean solos al arrancar, una sola vez, y sin paneles con plata: por el
Ruling R20 Finanzas y Simulador no se asignan desde el código, Juan los tilda
en el editor de roles.
"""

import json
import sqlite3

from database import init_db

PLATA = {"finanzas", "simulador"}


def _roles(db):
    conn = sqlite3.connect(db)
    try:
        return {n: json.loads(p) for n, p in conn.execute("SELECT name, panel_access FROM roles")}
    finally:
        conn.close()


def _cuantos(db, nombre):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT COUNT(1) FROM roles WHERE name = ?", (nombre,)).fetchone()[0]
    finally:
        conn.close()


def test_una_base_nueva_trae_contador_y_marketing(tmp_path):
    db = str(tmp_path / "nueva.db")
    init_db(db)
    roles = _roles(db)
    assert "cal" in roles["Contador"]
    assert {"cal", "meta", "marketing"} <= set(roles["Marketing"])


def test_ninguno_arranca_con_paneles_de_plata(tmp_path):
    db = str(tmp_path / "plata.db")
    init_db(db)
    roles = _roles(db)
    assert not PLATA & set(roles["Contador"])
    assert not PLATA & set(roles["Marketing"])


def test_se_suman_a_una_base_con_los_roles_de_produccion(tmp_path):
    """Producción ya tiene roles (Admin, SDR, Programador): el seed inicial no
    corre ahí, así que los nuevos tienen que crearse igual y sin tocar a los otros."""
    db = str(tmp_path / "prod.db")
    init_db(db)
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM roles WHERE name IN ('Contador', 'Marketing')")
    conn.execute("DELETE FROM roles")
    conn.executemany("INSERT INTO roles (name, panel_access) VALUES (?, ?)", [
        ("Admin", json.dumps(["cal", "finanzas"])),
        ("SDR", json.dumps(["cola", "cal"])),
        ("Programador", json.dumps(["tasks", "cal"])),
    ])
    conn.commit()
    conn.close()

    init_db(db)
    roles = _roles(db)
    assert {"Admin", "SDR", "Programador", "Contador", "Marketing"} <= set(roles)
    assert "finanzas" in roles["Admin"], "los roles que ya existían no se tocan"
    assert "cola" in roles["SDR"] and "tasks" in roles["Programador"]


def test_arrancar_varias_veces_no_duplica(tmp_path):
    db = str(tmp_path / "dos.db")
    init_db(db)
    init_db(db)
    init_db(db)
    assert _cuantos(db, "Contador") == 1
    assert _cuantos(db, "Marketing") == 1


def test_lo_que_juan_tilda_en_el_editor_no_se_pisa(tmp_path):
    db = str(tmp_path / "editado.db")
    init_db(db)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE roles SET panel_access = ? WHERE name = 'Contador'",
                 (json.dumps(["cal", "finanzas", "simulador"]),))
    conn.commit()
    conn.close()

    init_db(db)
    assert {"finanzas", "simulador"} <= set(_roles(db)["Contador"])
