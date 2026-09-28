"""La tarjeta de LinkedIn se dibuja en el CRM al abrir la semana.

Pedido de Juan (28/9): cada borrador de la semana con la foto abajo del texto,
sin esperar a la corrida del cron de los martes y viernes.
"""

from datetime import datetime, timezone

import pytest

import services.linkedin_borradores as lb
from database import init_db
from services.linkedin_render import dibujar

AHORA = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)
PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "li.db")
    init_db(ruta)
    return ruta


def _borrador(db, texto, frase=None, estado="borrador"):
    conn = lb._conn(db)
    conn.execute(
        "INSERT INTO linkedin_borradores (post_id, semana, orden, tema, texto, texto_original, hashtags, "
        "estado, created_at, frase) VALUES (NULL, ?, (SELECT COALESCE(MAX(orden),0)+1 FROM linkedin_borradores), "
        "'t', ?, ?, '', ?, '2026-09-28 15:00:00', ?)",
        (lb.semana_de(AHORA), texto, texto, estado, frase))
    conn.commit()
    conn.close()


def test_dibujar_da_un_png_del_tamano_de_linkedin():
    from PIL import Image
    import io
    png = dibujar("Cada campo que se suma a un formulario baja la cantidad de respuestas")
    assert png.startswith(PNG)
    assert Image.open(io.BytesIO(png)).size == (1200, 627)


def test_la_misma_frase_da_la_misma_tarjeta():
    assert dibujar("Una frase") == dibujar("Una frase")


def test_dibuja_los_que_faltan_con_frase_o_primera_oracion(db):
    _borrador(db, "Texto con frase propia.", frase="La frase propia")
    _borrador(db, "Cada campo que se suma a un formulario baja las respuestas. Y sigue.")
    _borrador(db, "Uno descartado.", estado="descartado")

    assert lb.dibujar_faltantes(db, lb.semana_de(AHORA)) == 2
    filas = {b["texto"][:4]: b for b in lb.listar_semana(db, lb.semana_de(AHORA))}
    assert filas["Text"]["tiene_imagen"] and filas["Cada"]["tiene_imagen"]
    assert not filas["Uno "]["tiene_imagen"]
    # Una segunda pasada no vuelve a dibujar lo que ya tiene foto.
    assert lb.dibujar_faltantes(db, lb.semana_de(AHORA)) == 0


def test_otra_semana_no_se_toca(db):
    _borrador(db, "Algo.")
    assert lb.dibujar_faltantes(db, "2026-09-14") == 0


def test_texto_vacio_no_rompe(db):
    _borrador(db, "   ")
    assert lb.dibujar_faltantes(db, lb.semana_de(AHORA)) == 0
