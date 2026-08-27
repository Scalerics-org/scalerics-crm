import json
import sqlite3
from unittest.mock import patch

import pytest

import dashboard
from database import (
    create_linkedin_post,
    get_linkedin_post_by_token,
    init_db,
    seed_linkedin_temas,
)


@pytest.fixture
def app_y_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secreto")
    monkeypatch.setenv("LINKEDIN_MAIL_TO", "destino@test.com")
    db = str(tmp_path / "t.db")
    init_db(db)
    seed_linkedin_temas(db)
    app = dashboard.create_app(db)
    app.config["TESTING"] = True
    # `conftest` apaga los procesos de fondo en toda la suite, asi que
    # `create_app` no arranca el worker y `get_worker()` devuelve None. El
    # endpoint contesta 503 en ese caso, y esa guarda es correcta en
    # produccion: no queremos encolar un job que nadie va a levantar. Lo que
    # se prueba aca es que el job se cree bien, no el worker, asi que se
    # simula uno presente.
    monkeypatch.setattr("routes.linkedin.get_worker", lambda: object())
    return app, db


def test_generar_sin_token_devuelve_401(app_y_db):
    app, _ = app_y_db
    r = app.test_client().post("/api/linkedin/generar")
    assert r.status_code == 401


def test_generar_encola_un_job_con_lote(app_y_db):
    app, db = app_y_db
    # El worker arranca junto con create_app y podria levantar el job durante
    # el test; con redactar mockeado no sale ninguna llamada a la API.
    with patch("services.linkedin_posts.redactar", return_value="t" * 400):
        r = app.test_client().post(
            "/api/linkedin/generar", headers={"x-admin-token": "secreto"}
        )
        assert r.status_code == 202
        cuerpo = r.get_json()

    assert cuerpo["lote"]

    conn = sqlite3.connect(db)
    fila = conn.execute(
        "SELECT type, payload FROM jobs WHERE id = ?", (cuerpo["job_id"],)
    ).fetchone()
    conn.close()

    assert fila[0] == "linkedin"
    # El lote viaja en el payload desde el INSERT, no se agrega despues.
    assert json.loads(fila[1])["lote"] == cuerpo["lote"]


def test_enviar_manda_el_mail_y_marca_enviados(app_y_db):
    app, db = app_y_db
    pid = create_linkedin_post(
        db, job_id=99, lote="lote-x", tipo="educativo", texto="t" * 300,
        angulo="concreto", fuente_tipo="tema", fuente_id=1,
        imagen_tipo="tarjeta", imagen_spec=json.dumps({"frase": "hola"}),
        fuente_desc="Tema educativo: Que es un CRM", aviso="",
        marcar_token="tok-x",
    )

    with patch("routes.linkedin.send_linkedin_drafts", return_value=True) as mail:
        r = app.test_client().post(
            "/api/linkedin/enviar",
            headers={"x-admin-token": "secreto"},
            json={"lote": "lote-x", "imagenes": [{"id": pid, "png_b64": "AAA"}]},
        )

    assert r.status_code == 200
    assert mail.call_args.args[0] == "destino@test.com"
    assert mail.call_args.args[1][0]["png_b64"] == "AAA"
    assert mail.call_args.args[1][0]["fuente_desc"] == "Tema educativo: Que es un CRM"

    conn = sqlite3.connect(db)
    estado = conn.execute(
        "SELECT estado FROM linkedin_posts WHERE id = ?", (pid,)
    ).fetchone()[0]
    conn.close()
    assert estado == "enviado"


def test_enviar_sin_borradores_devuelve_404(app_y_db):
    app, _ = app_y_db
    r = app.test_client().post(
        "/api/linkedin/enviar",
        headers={"x-admin-token": "secreto"},
        json={"lote": "no-existe", "imagenes": []},
    )
    assert r.status_code == 404


def test_enviar_sin_lote_devuelve_400(app_y_db):
    app, _ = app_y_db
    r = app.test_client().post(
        "/api/linkedin/enviar",
        headers={"x-admin-token": "secreto"},
        json={"imagenes": []},
    )
    assert r.status_code == 400


def test_marcar_publica_y_quema_el_token(app_y_db):
    app, db = app_y_db
    pid = create_linkedin_post(
        db, job_id=1, lote="lote-m", tipo="educativo", texto="t" * 300,
        angulo="concreto", fuente_tipo="tema", fuente_id=1,
        imagen_tipo="ninguna", imagen_spec="{}", marcar_token="tok-m",
    )

    r = app.test_client().get("/api/linkedin/marcar?token=tok-m")
    assert r.status_code == 200

    conn = sqlite3.connect(db)
    fila = conn.execute(
        "SELECT estado, publicado_en FROM linkedin_posts WHERE id = ?", (pid,)
    ).fetchone()
    conn.close()
    assert fila[0] == "publicado"
    assert fila[1] is not None
    assert get_linkedin_post_by_token(db, "tok-m") is None


def test_marcar_con_token_quemado_devuelve_410(app_y_db):
    app, _ = app_y_db
    r = app.test_client().get("/api/linkedin/marcar?token=no-existe")
    assert r.status_code == 410


def test_generar_acepta_un_contexto_manual(app_y_db):
    """El post sobre trabajo real se pide a mano: el CRM no guarda nada de los
    proyectos entregados, asi que esa info la escribe Juan."""
    app, db = app_y_db
    with patch("services.linkedin_posts.redactar", return_value="t" * 400):
        r = app.test_client().post(
            "/api/linkedin/generar",
            headers={"x-admin-token": "secreto"},
            json={"contexto_manual": "Salio la tienda de Biciconde.",
                  "imagen_url": "https://biciconde.uy"},
        )
    assert r.status_code == 202
    payload = json.loads(sqlite3.connect(db).execute(
        "SELECT payload FROM jobs WHERE id = ?", (r.get_json()["job_id"],)
    ).fetchone()[0])
    assert payload["contexto_manual"] == "Salio la tienda de Biciconde."
    assert payload["imagen_url"] == "https://biciconde.uy"


def test_generar_sin_cuerpo_no_pone_contexto_manual(app_y_db):
    app, db = app_y_db
    with patch("services.linkedin_posts.redactar", return_value="t" * 400):
        r = app.test_client().post(
            "/api/linkedin/generar", headers={"x-admin-token": "secreto"})
    payload = json.loads(sqlite3.connect(db).execute(
        "SELECT payload FROM jobs WHERE id = ?", (r.get_json()["job_id"],)
    ).fetchone()[0])
    assert "contexto_manual" not in payload


def test_fallo_manda_el_aviso_a_la_casilla_de_los_borradores(app_y_db):
    app, _ = app_y_db
    with patch("routes.linkedin.send_linkedin_failure", return_value=True) as aviso:
        r = app.test_client().post(
            "/api/linkedin/fallo",
            headers={"x-admin-token": "secreto"},
            json={"motivo": "el job no termino en 900s"},
        )
    assert r.status_code == 200
    aviso.assert_called_once_with("destino@test.com", "el job no termino en 900s")


def test_fallo_sin_motivo_no_manda_un_mail_vacio(app_y_db):
    app, _ = app_y_db
    with patch("routes.linkedin.send_linkedin_failure", return_value=True) as aviso:
        app.test_client().post("/api/linkedin/fallo",
                               headers={"x-admin-token": "secreto"})
    assert aviso.call_args[0][1] == "el cron no dejo detalle"


def test_fallo_sin_token_devuelve_401(app_y_db):
    app, _ = app_y_db
    assert app.test_client().post("/api/linkedin/fallo").status_code == 401
