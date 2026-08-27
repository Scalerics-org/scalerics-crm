import pytest

from database import (
    create_linkedin_post,
    get_fuentes_usadas,
    get_linkedin_post_by_token,
    get_linkedin_posts_by_job,
    get_linkedin_posts_by_lote,
    get_temas_disponibles,
    init_db,
    marcar_tema_usado,
    seed_linkedin_temas,
    update_linkedin_post,
)


@pytest.fixture
def db_path(tmp_path):
    p = str(tmp_path / "test.db")
    init_db(p)
    return p


def test_init_crea_las_tablas_de_linkedin(db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    nombres = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    conn.close()
    assert "linkedin_posts" in nombres
    assert "linkedin_temas" in nombres


def test_businesses_tiene_linkedin_ok_en_cero(db_path):
    import sqlite3
    from database import insert_business
    bid = insert_business(db_path, {"name": "Panadería La Nueva", "phone": "+598 99 111 222"})
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT linkedin_ok FROM businesses WHERE id = ?", (bid,)).fetchone()
    conn.close()
    assert row["linkedin_ok"] == 0


def test_seed_temas_es_idempotente(db_path):
    import sqlite3
    seed_linkedin_temas(db_path)
    seed_linkedin_temas(db_path)
    conn = sqlite3.connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM linkedin_temas").fetchone()[0]
    conn.close()
    assert total >= 40


def test_create_y_get_por_job(db_path):
    pid = create_linkedin_post(
        db_path,
        job_id=7,
        lote="lote-7",
        tipo="educativo",
        texto="Un texto cualquiera.",
        angulo="concreto",
        fuente_tipo="tema",
        fuente_id=3,
        imagen_tipo="tarjeta",
        imagen_spec='{"frase": "hola"}',
        marcar_token="tok-abc",
    )
    posts = get_linkedin_posts_by_job(db_path, 7)
    assert len(posts) == 1
    assert posts[0]["id"] == pid
    assert posts[0]["estado"] == "generado"
    assert posts[0]["texto"] == "Un texto cualquiera."


def test_get_por_lote(db_path):
    create_linkedin_post(
        db_path, job_id=1, lote="lote-a", tipo="educativo", texto="a" * 300,
        angulo="concreto", fuente_tipo="tema", fuente_id=1,
        imagen_tipo="ninguna", imagen_spec="{}", marcar_token="tok-a",
    )
    create_linkedin_post(
        db_path, job_id=2, lote="lote-b", tipo="educativo", texto="b" * 300,
        angulo="concreto", fuente_tipo="tema", fuente_id=2,
        imagen_tipo="ninguna", imagen_spec="{}", marcar_token="tok-b",
    )

    assert len(get_linkedin_posts_by_lote(db_path, "lote-a")) == 1
    assert get_linkedin_posts_by_lote(db_path, "lote-a")[0]["texto"] == "a" * 300
    assert get_linkedin_posts_by_lote(db_path, "inexistente") == []


def test_get_por_token_y_update(db_path):
    pid = create_linkedin_post(
        db_path, job_id=1, lote="l1", tipo="educativo", texto="x" * 300,
        angulo="a", fuente_tipo="tema", fuente_id=1, imagen_tipo="ninguna",
        imagen_spec="{}", marcar_token="tok-1",
    )
    assert get_linkedin_post_by_token(db_path, "tok-1")["id"] == pid
    update_linkedin_post(db_path, pid, estado="publicado", marcar_token=None)
    assert get_linkedin_post_by_token(db_path, "tok-1") is None


def test_update_rechaza_columna_inventada(db_path):
    pid = create_linkedin_post(
        db_path, job_id=1, lote="l2", tipo="educativo", texto="x" * 300,
        angulo="a", fuente_tipo="tema", fuente_id=1, imagen_tipo="ninguna",
        imagen_spec="{}", marcar_token="tok-2",
    )
    with pytest.raises(ValueError):
        update_linkedin_post(db_path, pid, columna_que_no_existe="x")


def test_fuentes_usadas_solo_cuenta_enviados_y_publicados(db_path):
    create_linkedin_post(
        db_path, job_id=1, lote="l3", tipo="trabajo", texto="a" * 300,
        angulo="a", fuente_tipo="demo", fuente_id=10, imagen_tipo="ninguna",
        imagen_spec="{}", marcar_token="t1",
    )
    descartado = create_linkedin_post(
        db_path, job_id=1, lote="l3", tipo="trabajo", texto="b" * 300,
        angulo="b", fuente_tipo="demo", fuente_id=11, imagen_tipo="ninguna",
        imagen_spec="{}", marcar_token="t2",
    )
    update_linkedin_post(db_path, descartado, estado="descartado")
    enviado = create_linkedin_post(
        db_path, job_id=1, lote="l3", tipo="trabajo", texto="c" * 300,
        angulo="c", fuente_tipo="demo", fuente_id=12, imagen_tipo="ninguna",
        imagen_spec="{}", marcar_token="t3",
    )
    update_linkedin_post(db_path, enviado, estado="enviado")

    usadas = get_fuentes_usadas(db_path)
    assert ("demo", 12) in usadas
    assert ("demo", 10) not in usadas   # sigue en 'generado'
    assert ("demo", 11) not in usadas   # descartado


def test_temas_disponibles_respeta_el_cooldown(db_path):
    seed_linkedin_temas(db_path)
    todos = get_temas_disponibles(db_path, "2026-03-01T00:00:00")
    primero = todos[0]["id"]

    marcar_tema_usado(db_path, primero, "2026-08-01T00:00:00")
    libres = get_temas_disponibles(db_path, "2026-03-01T00:00:00")
    assert primero not in {t["id"] for t in libres}

    # con un límite posterior al uso, el tema vuelve a estar disponible
    vueltos = get_temas_disponibles(db_path, "2026-09-01T00:00:00")
    assert primero in {t["id"] for t in vueltos}
