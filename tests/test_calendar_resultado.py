"""Resultado de la reunion y responsable.

`status` solo tenia 'scheduled' y 'canceled': un planton se veia EXACTAMENTE igual
que una reunion exitosa. No habia forma de medir la tasa de asistencia, ni de saber
a quien reagendar, ni de distinguir "el cliente cancelo" (aviso) de "el cliente no
aparecio" (no aviso), que comercialmente no son lo mismo.

Y las reuniones no tenian responsable: la meta 'reuniones_hechas' se repartia entre
todos los contributors del lead, asi que si uno agendaba y otro atendia, el
progreso les sumaba a los dos.
"""

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import (create_meeting, create_task, create_user, get_business,
                      get_meeting, get_task_by_id, init_db, insert_business)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ALLOW_INSECURE_DEV_KEY", "1")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    db = str(tmp_path / "r.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


def _usuario(app, email):
    return create_user(app.config["_DB"], name=email.split("@")[0], email=email,
                       phone="099", password_hash=generate_password_hash("x" * 10))


def _cli(app, uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "test"
    return c


def _reunion(db, lead, owner=None):
    return create_meeting(db, lead, title="R", start_at="2026-01-10T10:00:00",
                          end_at="2026-01-10T11:00:00", status="scheduled",
                          owner_id=owner)


# ── resultado ────────────────────────────────────────────────────────────────

def test_marcar_realizada_avanza_el_lead(app):
    db = app.config["_DB"]
    uid = _usuario(app, "jefe@test.com")
    lead = insert_business(db, {"name": "Ana", "phone": "+59899000001"})
    mid = _reunion(db, lead, owner=uid)

    r = _cli(app, uid).post(f"/api/calendar/meetings/{mid}/outcome", json={"outcome": "realizada"})
    assert r.status_code == 200
    assert get_meeting(db, mid)["status"] == "realizada"
    assert get_business(db, lead)["crm_status"] == "reunion_hecha"


def test_no_asistio_queda_registrado_y_no_avanza_el_lead(app):
    """Un planton no puede verse igual que una reunion exitosa, ni mover el lead
    como si hubiera pasado algo."""
    db = app.config["_DB"]
    uid = _usuario(app, "jefe@test.com")
    lead = insert_business(db, {"name": "Ana", "phone": "+59899000002"})
    from database import update_business
    update_business(db, lead, crm_status="reunion_agendada")
    mid = _reunion(db, lead, owner=uid)

    _cli(app, uid).post(f"/api/calendar/meetings/{mid}/outcome", json={"outcome": "no_asistio"})
    assert get_meeting(db, mid)["status"] == "no_asistio"
    assert get_business(db, lead)["crm_status"] == "reunion_agendada"


def test_resultado_invalido_da_400(app):
    db = app.config["_DB"]
    uid = _usuario(app, "jefe@test.com")
    lead = insert_business(db, {"name": "Ana", "phone": "+59899000003"})
    mid = _reunion(db, lead, owner=uid)
    r = _cli(app, uid).post(f"/api/calendar/meetings/{mid}/outcome", json={"outcome": "inventado"})
    assert r.status_code == 400


def test_reunion_inexistente_da_404(app):
    uid = _usuario(app, "jefe@test.com")
    r = _cli(app, uid).post("/api/calendar/meetings/99999/outcome", json={"outcome": "realizada"})
    assert r.status_code == 404


def test_no_pisa_un_estado_mas_avanzado_del_lead(app):
    db = app.config["_DB"]
    uid = _usuario(app, "jefe@test.com")
    lead = insert_business(db, {"name": "Ana", "phone": "+59899000004"})
    from database import update_business
    update_business(db, lead, crm_status="cliente_cerrado")
    mid = _reunion(db, lead, owner=uid)

    _cli(app, uid).post(f"/api/calendar/meetings/{mid}/outcome", json={"outcome": "realizada"})
    assert get_business(db, lead)["crm_status"] == "cliente_cerrado"


# ── la meta va a quien atendio ───────────────────────────────────────────────

def test_la_meta_se_acredita_al_responsable_no_a_quien_marca(app):
    """Si Thomy atiende y el jefe cierra la reunion, el progreso es de Thomy."""
    db = app.config["_DB"]
    jefe = _usuario(app, "jefe@test.com")
    thomy = _usuario(app, "thomy@test.com")
    tarea_thomy = create_task(db, title="Meta Thomy", assignee_id=thomy,
                              created_by_id=thomy, goal=5, goal_type="reuniones_hechas")
    tarea_jefe = create_task(db, title="Meta jefe", assignee_id=jefe,
                             created_by_id=jefe, goal=5, goal_type="reuniones_hechas")
    lead = insert_business(db, {"name": "Ana", "phone": "+59899000005"})
    mid = _reunion(db, lead, owner=thomy)

    _cli(app, jefe).post(f"/api/calendar/meetings/{mid}/outcome", json={"outcome": "realizada"})

    assert get_task_by_id(db, tarea_thomy)["progress"] == 1
    assert (get_task_by_id(db, tarea_jefe)["progress"] or 0) == 0


def test_cerrar_dos_veces_no_suma_dos_veces(app):
    db = app.config["_DB"]
    uid = _usuario(app, "jefe@test.com")
    tid = create_task(db, title="Meta", assignee_id=uid, created_by_id=uid,
                      goal=5, goal_type="reuniones_hechas")
    lead = insert_business(db, {"name": "Ana", "phone": "+59899000006"})
    mid = _reunion(db, lead, owner=uid)

    c = _cli(app, uid)
    c.post(f"/api/calendar/meetings/{mid}/outcome", json={"outcome": "realizada"})
    c.post(f"/api/calendar/meetings/{mid}/outcome", json={"outcome": "realizada"})
    assert get_task_by_id(db, tid)["progress"] == 1


# ── responsable ──────────────────────────────────────────────────────────────

def test_quien_crea_la_reunion_queda_como_responsable(app):
    from unittest.mock import patch
    db = app.config["_DB"]
    uid = _usuario(app, "jefe@test.com")
    lead = insert_business(db, {"name": "Ana", "phone": "+59899000007", "email": "a@x.com"})
    with patch("routes.calendar._get_calendar_service", return_value=(None, "sin credenciales")):
        d = _cli(app, uid).post("/api/calendar/events", json={
            "title": "R", "date": "2026-09-10", "time": "10:00", "client_id": lead,
        }).get_json()
    assert get_meeting(db, d["meeting_id"])["owner_id"] == uid


def test_se_puede_reasignar_el_responsable(app):
    db = app.config["_DB"]
    jefe = _usuario(app, "jefe@test.com")
    otro = _usuario(app, "otro@test.com")
    lead = insert_business(db, {"name": "Ana", "phone": "+59899000008"})
    mid = _reunion(db, lead, owner=jefe)

    r = _cli(app, jefe).put(f"/api/calendar/meetings/{mid}/owner", json={"owner_id": otro})
    assert r.status_code == 200
    assert get_meeting(db, mid)["owner_id"] == otro


def test_no_se_puede_asignar_a_un_usuario_inexistente(app):
    db = app.config["_DB"]
    uid = _usuario(app, "jefe@test.com")
    lead = insert_business(db, {"name": "Ana", "phone": "+59899000009"})
    mid = _reunion(db, lead, owner=uid)
    r = _cli(app, uid).put(f"/api/calendar/meetings/{mid}/owner", json={"owner_id": 99999})
    assert r.status_code == 400


def test_el_listado_expone_estado_y_responsable(app):
    db = app.config["_DB"]
    uid = _usuario(app, "jefe@test.com")
    lead = insert_business(db, {"name": "Ana", "phone": "+59899000010"})
    _reunion(db, lead, owner=uid)

    from unittest.mock import patch
    with patch("routes.calendar._lanzar_sync_en_background"):
        ev = _cli(app, uid).get("/api/calendar/events?start=2026-01-01&end=2026-01-31").get_json()
    e = ev["events"][0]
    assert e["status"] == "scheduled"
    assert e["owner_id"] == uid
    assert e["owner_name"] == "jefe"
    # ya paso (enero 2026) y sigue agendada -> pendiente de cerrar
    assert e["pendiente_cierre"] is True
