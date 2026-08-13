"""Tests de tareas y metas.

Dos defectos que hacian inutilizable el modulo como herramienta de gestion:
PUT y DELETE no chequeaban propiedad (cualquiera reasignaba o borraba la tarea de
otro), y el progreso no era idempotente (mover un lead de ida y vuelta inflaba el
contador y daba la meta por cumplida sin cumplirse).
"""

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import (connect, create_task, create_user, get_task_by_id,
                      increment_task_progress, init_db, insert_business)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ALLOW_INSECURE_DEV_KEY", "1")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    db = str(tmp_path / "t.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


def _usuario(db, email):
    """Crea el usuario CON el panel 'tasks' habilitado.

    Sin rol, el enforcement por panel devuelve 403 antes de llegar al chequeo de
    propiedad, y los tests de propiedad pasarian por el motivo equivocado.
    """
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    conn = connect(db)
    try:
        rid = conn.execute("SELECT id FROM roles WHERE name='Admin'").fetchone()[0]
        conn.execute("UPDATE users SET role_id=? WHERE id=?", (rid, uid))
        conn.commit()
    finally:
        conn.close()
    return uid


def _cliente(app, uid, nombre="u"):
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = nombre
    return c


# ── propiedad ────────────────────────────────────────────────────────────────

def test_no_puedo_editar_la_tarea_de_otro(app):
    db = app.config["_DB"]
    duenio = _usuario(db, "duenio@test.com")
    ajeno = _usuario(db, "ajeno@test.com")
    tid = create_task(db, title="Mia", assignee_id=duenio, created_by_id=duenio)

    r = _cliente(app, ajeno).put(f"/api/tasks/{tid}", json={"title": "Secuestrada"})
    assert r.status_code == 403
    assert get_task_by_id(db, tid)["title"] == "Mia"


def test_no_puedo_borrar_la_tarea_de_otro(app):
    db = app.config["_DB"]
    duenio = _usuario(db, "duenio@test.com")
    ajeno = _usuario(db, "ajeno@test.com")
    tid = create_task(db, title="Mia", assignee_id=duenio, created_by_id=duenio)

    assert _cliente(app, ajeno).delete(f"/api/tasks/{tid}").status_code == 403
    assert get_task_by_id(db, tid) is not None


def test_el_asignado_si_puede_editarla(app):
    db = app.config["_DB"]
    duenio = _usuario(db, "duenio@test.com")
    tid = create_task(db, title="Mia", assignee_id=duenio, created_by_id=duenio)
    r = _cliente(app, duenio).put(f"/api/tasks/{tid}", json={"status": "done"})
    assert r.status_code == 200
    assert get_task_by_id(db, tid)["status"] == "done"


def test_quien_la_creo_puede_editarla_aunque_este_asignada_a_otro(app):
    db = app.config["_DB"]
    creador = _usuario(db, "creador@test.com")
    asignado = _usuario(db, "asignado@test.com")
    tid = create_task(db, title="T", assignee_id=asignado, created_by_id=creador)
    assert _cliente(app, creador).put(f"/api/tasks/{tid}", json={"status": "done"}).status_code == 200


def test_el_admin_puede_con_cualquiera(app):
    db = app.config["_DB"]
    otro = _usuario(db, "otro@test.com")
    admin = _usuario(db, "jefe@test.com")
    tid = create_task(db, title="T", assignee_id=otro, created_by_id=otro)
    assert _cliente(app, admin).put(f"/api/tasks/{tid}", json={"status": "done"}).status_code == 200


def test_tarea_inexistente_da_404_no_ok(app):
    db = app.config["_DB"]
    c = _cliente(app, _usuario(db, "u@test.com"))
    assert c.put("/api/tasks/99999", json={"status": "done"}).status_code == 404
    # Antes el DELETE devolvia ok:true y el frontend daba por borrado algo inexistente
    assert c.delete("/api/tasks/99999").status_code == 404


def test_columna_invalida_da_400_no_500(app):
    db = app.config["_DB"]
    uid = _usuario(db, "u@test.com")
    tid = create_task(db, title="T", assignee_id=uid, created_by_id=uid)
    r = _cliente(app, uid).put(f"/api/tasks/{tid}", json={"columna_que_no_existe": 1})
    assert r.status_code == 400


# ── idempotencia del progreso ────────────────────────────────────────────────

def test_el_mismo_lead_no_suma_dos_veces(app):
    """Mover un lead a 'reunion_hecha', volverlo atras y adelantarlo de nuevo
    sumaba dos veces por la MISMA reunion."""
    db = app.config["_DB"]
    uid = _usuario(db, "sdr@test.com")
    tid = create_task(db, title="Meta", assignee_id=uid, created_by_id=uid,
                      goal=5, goal_type="reuniones_hechas")
    lead = insert_business(db, {"name": "Ana", "phone": "+59899000001"})

    increment_task_progress(db, [uid], "reuniones_hechas", lead_id=lead, lead_name="Ana")
    increment_task_progress(db, [uid], "reuniones_hechas", lead_id=lead, lead_name="Ana")
    increment_task_progress(db, [uid], "reuniones_hechas", lead_id=lead, lead_name="Ana")

    assert get_task_by_id(db, tid)["progress"] == 1


def test_leads_distintos_si_suman_por_separado(app):
    db = app.config["_DB"]
    uid = _usuario(db, "sdr@test.com")
    tid = create_task(db, title="Meta", assignee_id=uid, created_by_id=uid,
                      goal=5, goal_type="reuniones_hechas")
    for n in range(3):
        lead = insert_business(db, {"name": f"L{n}", "phone": f"+5989900010{n}"})
        increment_task_progress(db, [uid], "reuniones_hechas", lead_id=lead, lead_name=f"L{n}")

    assert get_task_by_id(db, tid)["progress"] == 3


def test_sin_lead_id_se_cuenta_cada_vez(app):
    """Sin lead concreto no hay con que deduplicar: se mantiene el conteo simple."""
    db = app.config["_DB"]
    uid = _usuario(db, "sdr@test.com")
    tid = create_task(db, title="Meta", assignee_id=uid, created_by_id=uid,
                      goal=5, goal_type="llamadas")
    increment_task_progress(db, [uid], "llamadas")
    increment_task_progress(db, [uid], "llamadas")
    assert get_task_by_id(db, tid)["progress"] == 2


def test_la_meta_se_completa_al_llegar_al_objetivo(app):
    db = app.config["_DB"]
    uid = _usuario(db, "sdr@test.com")
    tid = create_task(db, title="Meta", assignee_id=uid, created_by_id=uid,
                      goal=2, goal_type="reuniones_hechas")
    for n in range(2):
        lead = insert_business(db, {"name": f"L{n}", "phone": f"+5989900020{n}"})
        increment_task_progress(db, [uid], "reuniones_hechas", lead_id=lead)

    t = get_task_by_id(db, tid)
    assert t["progress"] == 2
    assert t["status"] == "done"
