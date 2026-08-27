import sqlite3
from datetime import datetime

import pytest

from database import init_db, marcar_tema_usado, seed_linkedin_temas
from services.linkedin_posts import elegir_tema


AHORA = datetime(2026, 8, 26, 9, 0, 0)


@pytest.fixture
def db_path(tmp_path):
    p = str(tmp_path / "test.db")
    init_db(p)
    return p


def test_elegir_tema_devuelve_uno_libre(db_path):
    seed_linkedin_temas(db_path)
    tema, en_cooldown = elegir_tema(db_path, AHORA, excluir_ids=set())
    assert tema["titulo"]
    assert en_cooldown is False


def test_elegir_tema_excluye_los_del_mismo_mail(db_path):
    seed_linkedin_temas(db_path)
    primero, _ = elegir_tema(db_path, AHORA, excluir_ids=set())
    segundo, _ = elegir_tema(db_path, AHORA, excluir_ids={primero["id"]})
    assert segundo["id"] != primero["id"]


def test_elegir_tema_reusa_el_mas_viejo_si_no_queda_ninguno(db_path):
    seed_linkedin_temas(db_path)
    conn = sqlite3.connect(db_path)
    ids = [r[0] for r in conn.execute("SELECT id FROM linkedin_temas")]
    conn.close()
    for i in ids:
        marcar_tema_usado(db_path, i, "2026-08-20T00:00:00")

    tema, en_cooldown = elegir_tema(db_path, AHORA, excluir_ids=set())

    assert tema is not None
    assert en_cooldown is True


def test_sin_temas_sembrados_explota_con_un_mensaje_claro(db_path):
    with pytest.raises(RuntimeError, match="temas"):
        elegir_tema(db_path, AHORA, excluir_ids=set())
