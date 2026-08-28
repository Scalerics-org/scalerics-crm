"""El banco de posts ya escritos que reemplaza a la generacion por API.

Antes el cron tomaba un titulo de `linkedin_temas` y le pedia a Claude que
escribiera el post. Ahora los posts ya estan escritos y guardados: el cron
solo elige cual de los que quedan libres le toca a esta corrida.
"""

import sqlite3

import pytest

from database import (
    get_banco_disponible,
    init_db,
    marcar_banco_usado,
    seed_linkedin_banco,
)


@pytest.fixture
def db_path(tmp_path):
    p = str(tmp_path / "test.db")
    init_db(p)
    return p


def _insertar(db_path, tema, angulo, texto="Un texto cualquiera.", frase="Una frase"):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO linkedin_banco (tema, angulo, texto, frase) VALUES (?, ?, ?, ?)",
        (tema, angulo, texto, frase),
    )
    conn.commit()
    conn.close()


def test_init_crea_la_tabla_del_banco(db_path):
    conn = sqlite3.connect(db_path)
    nombres = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    conn.close()
    assert "linkedin_banco" in nombres


def test_el_banco_devuelve_los_no_usados(db_path):
    _insertar(db_path, "Tema A", "concreto")
    _insertar(db_path, "Tema B", "implicancia")

    libres = get_banco_disponible(db_path, "2026-01-01T00:00:00")

    assert [f["tema"] for f in libres] == ["Tema A", "Tema B"]


def test_el_banco_esconde_lo_usado_hace_poco(db_path):
    _insertar(db_path, "Tema A", "concreto")
    _insertar(db_path, "Tema B", "implicancia")
    marcar_banco_usado(db_path, 1, "2026-08-20T00:00:00")

    libres = get_banco_disponible(db_path, "2026-03-01T00:00:00")

    assert [f["tema"] for f in libres] == ["Tema B"]


def test_el_banco_devuelve_lo_usado_hace_mucho(db_path):
    _insertar(db_path, "Tema A", "concreto")
    marcar_banco_usado(db_path, 1, "2025-01-01T00:00:00")

    libres = get_banco_disponible(db_path, "2026-03-01T00:00:00")

    assert [f["tema"] for f in libres] == ["Tema A"]


def test_los_nunca_usados_van_antes_que_los_reciclados(db_path):
    _insertar(db_path, "Reciclado", "concreto")
    _insertar(db_path, "Virgen", "implicancia")
    marcar_banco_usado(db_path, 1, "2025-01-01T00:00:00")

    libres = get_banco_disponible(db_path, "2026-03-01T00:00:00")

    assert [f["tema"] for f in libres] == ["Virgen", "Reciclado"]


def test_el_banco_guarda_texto_y_frase(db_path):
    _insertar(db_path, "Tema A", "concreto",
              texto="El post entero, ya escrito.", frase="La frase de la tarjeta")

    fila = get_banco_disponible(db_path, "2026-01-01T00:00:00")[0]

    assert fila["texto"] == "El post entero, ya escrito."
    assert fila["frase"] == "La frase de la tarjeta"


def test_sembrar_el_banco_dos_veces_no_duplica(db_path):
    seed_linkedin_banco(db_path)
    seed_linkedin_banco(db_path)

    conn = sqlite3.connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM linkedin_banco").fetchone()[0]
    distintos = conn.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT tema, angulo FROM linkedin_banco)"
    ).fetchone()[0]
    conn.close()

    assert total == distintos
    assert total > 0
