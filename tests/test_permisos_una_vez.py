"""Los paneles que se reparten solos a los roles se reparten UNA sola vez.

Pedido de Juan (15/9). Organigrama, Ausencias, Daily, Plantillas, etc. se les
sumaban a los roles en cada arranque, o sea en cada deploy. Si Juan le sacaba
Ausencias al SDR desde el editor de roles, el próximo deploy se la devolvía.
Ahora el reparto queda anotado y desde ahí manda lo que Juan tilde.
"""

import json
import sqlite3

from database import _grant_panel_to_existing_roles, init_db


def _sql(db, sql, params=()):
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.fetchall()
    finally:
        conn.close()


def _paneles(db, rol):
    return json.loads(_sql(db, "SELECT panel_access FROM roles WHERE name = ?", (rol,))[0][0])


def _poner(db, rol, paneles):
    _sql(db, "UPDATE roles SET panel_access = ? WHERE name = ?", (json.dumps(paneles), rol))


def test_lo_que_juan_saca_no_vuelve_con_los_deploys(tmp_path):
    db = str(tmp_path / "saca.db")
    init_db(db)
    admin = _paneles(db, "Admin")
    assert {"equipo", "ausencias"} <= set(admin), "el primer arranque reparte como antes"

    _poner(db, "Admin", [p for p in admin if p not in ("equipo", "ausencias", "marketing")])
    for _ in range(3):  # tres deploys
        init_db(db)

    quedo = set(_paneles(db, "Admin"))
    assert not {"equipo", "ausencias", "marketing"} & quedo


def test_el_reparto_queda_anotado(tmp_path):
    db = str(tmp_path / "anotado.db")
    init_db(db)
    anotados = {fila[0] for fila in _sql(db, "SELECT panel FROM panel_grants_aplicados")}
    assert {"meta", "marketing", "equipo", "ausencias"} <= anotados


def test_un_panel_nuevo_le_llega_a_los_roles_que_ya_existian(tmp_path):
    """Lo que pasa en producción cuando entra un panel nuevo: los roles ya
    están y el panel todavía no se repartió nunca."""
    db = str(tmp_path / "nuevo.db")
    init_db(db)
    _sql(db, "INSERT INTO roles (name, panel_access) VALUES ('SDR', ?)", (json.dumps(["cola", "cal"]),))
    conn = sqlite3.connect(db)
    try:
        assert _grant_panel_to_existing_roles(conn, "panel_de_prueba") >= 1
        assert "panel_de_prueba" in json.loads(conn.execute(
            "SELECT panel_access FROM roles WHERE name='SDR'").fetchone()[0])
        # La segunda vez ya no reparte, aunque se lo hayan sacado.
        conn.execute("UPDATE roles SET panel_access = ? WHERE name='SDR'", (json.dumps(["cola", "cal"]),))
        conn.commit()
        assert _grant_panel_to_existing_roles(conn, "panel_de_prueba") == 0
        assert "panel_de_prueba" not in json.loads(conn.execute(
            "SELECT panel_access FROM roles WHERE name='SDR'").fetchone()[0])
    finally:
        conn.close()


def test_un_rol_creado_despues_no_recibe_paneles_solos(tmp_path):
    db = str(tmp_path / "despues.db")
    init_db(db)
    _sql(db, "INSERT INTO roles (name, panel_access) VALUES ('Nuevo', ?)", (json.dumps(["cal"]),))
    init_db(db)
    assert _paneles(db, "Nuevo") == ["cal"], "lo que tenga lo tilda Juan en el editor"


def test_finanzas_y_simulador_siguen_sin_repartirse(tmp_path):
    db = str(tmp_path / "plata.db")
    init_db(db)
    init_db(db)
    for (crudo,) in _sql(db, "SELECT panel_access FROM roles"):
        assert not {"finanzas", "simulador"} & set(json.loads(crudo))
