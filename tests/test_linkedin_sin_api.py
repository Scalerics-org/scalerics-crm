"""El camino del cron no puede tocar la API de Anthropic.

Ese era todo el punto del banco de posts. Si alguien vuelve a meter una
llamada a un modelo en este flujo, estos tests lo frenan antes del deploy.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from database import init_db, seed_linkedin_banco
from services.linkedin_posts import (
    elegir_del_banco,
    linkedin_job_handler,
    validar_borrador,
)


@pytest.fixture
def db_path(tmp_path):
    p = str(tmp_path / "test.db")
    init_db(p)
    seed_linkedin_banco(p)
    return p


AHORA = datetime(2026, 8, 28, 8, 0, 0)


def test_el_modulo_no_importa_anthropic():
    fuente = Path("services/linkedin_posts.py").read_text(encoding="utf-8")
    assert "anthropic" not in fuente.lower()


def test_elegir_del_banco_trae_un_post_ya_escrito(db_path):
    fila, en_cooldown = elegir_del_banco(db_path, AHORA, set())

    assert fila["texto"].strip()
    assert fila["frase"].strip()
    assert en_cooldown is False


def test_elegir_del_banco_no_repite_tema_dentro_de_la_misma_corrida(db_path):
    primera, _ = elegir_del_banco(db_path, AHORA, set())
    segunda, _ = elegir_del_banco(db_path, AHORA, {primera["tema"]})

    assert primera["tema"] != segunda["tema"]


def test_elegir_del_banco_avisa_cuando_recicla(db_path):
    # Todos usados la semana pasada: dentro del cooldown de 180 dias, asi que
    # no queda ninguno libre y hay que reciclar.
    hace_una_semana = datetime(2026, 8, 21, 8, 0, 0).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE linkedin_banco SET usado_en = ?", (hace_una_semana,))
    conn.commit()
    conn.close()

    _, en_cooldown = elegir_del_banco(db_path, AHORA, set())

    assert en_cooldown is True


def test_el_job_arma_dos_borradores_con_texto_del_banco(db_path):
    resultado = linkedin_job_handler({
        "db_path": db_path,
        "lote": "lote-de-prueba",
        "job_id": None,
        "ahora": AHORA.isoformat(),
    })

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    textos_del_banco = {
        r["texto"] for r in conn.execute("SELECT texto FROM linkedin_banco")
    }
    conn.close()

    assert len(resultado["borradores"]) == 2
    for b in resultado["borradores"]:
        assert b["texto"] in textos_del_banco


def test_el_job_marca_usado_lo_que_consumio(db_path):
    linkedin_job_handler({
        "db_path": db_path,
        "lote": "lote-de-prueba",
        "job_id": None,
        "ahora": AHORA.isoformat(),
    })

    conn = sqlite3.connect(db_path)
    usados = conn.execute(
        "SELECT COUNT(*) FROM linkedin_banco WHERE usado_en IS NOT NULL"
    ).fetchone()[0]
    conn.close()

    assert usados == 2


def test_el_camino_manual_guarda_el_texto_tal_cual(db_path):
    texto = (
        "Esta semana entregamos el sistema de stock de una bloquera de Durazno. "
        "Dos semanas de trabajo y el mostrador dejó de anotar en un cuaderno. "
        "Lo que más costó no fue el código: fue acordar qué contaba como una "
        "unidad cuando el mismo bloque se vende suelto y por palet."
    )

    resultado = linkedin_job_handler({
        "db_path": db_path,
        "lote": "lote-manual",
        "job_id": None,
        "ahora": AHORA.isoformat(),
        "contexto_manual": texto,
    })

    assert len(resultado["borradores"]) == 1
    assert resultado["borradores"][0]["texto"] == texto


def test_todos_los_posts_de_la_semilla_pasan_el_validador():
    from services.linkedin_banco_semilla import LINKEDIN_BANCO_SEMILLA

    fallados = []
    for tema, angulo, texto, _frase in LINKEDIN_BANCO_SEMILLA:
        violaciones = validar_borrador(texto)
        if violaciones:
            fallados.append(f"{tema} [{angulo}]: {'; '.join(violaciones)}")

    assert not fallados, "\n".join(fallados)


def test_las_frases_de_tarjeta_entran_en_la_imagen():
    from services.linkedin_posts import MAX_FRASE
    from services.linkedin_banco_semilla import LINKEDIN_BANCO_SEMILLA

    largas = [
        f"{tema} [{angulo}]: {len(frase)} caracteres"
        for tema, angulo, _texto, frase in LINKEDIN_BANCO_SEMILLA
        if len(frase) > MAX_FRASE
    ]

    assert not largas, "\n".join(largas)


def test_cada_tema_del_banco_tiene_los_dos_angulos():
    from services.linkedin_banco_semilla import LINKEDIN_BANCO_SEMILLA

    por_tema = {}
    for tema, angulo, _texto, _frase in LINKEDIN_BANCO_SEMILLA:
        por_tema.setdefault(tema, set()).add(angulo)

    incompletos = [t for t, a in por_tema.items() if a != {"concreto", "implicancia"}]

    assert not incompletos, f"temas sin los dos angulos: {incompletos}"


# ── Cobertura heredada de test_linkedin_redaccion.py y test_linkedin_tarjeta.py
# Esos archivos probaban redactar() y frase_tarjeta(), que ya no existen. Lo
# que sigue abajo es lo que de ahi seguia teniendo sentido: el armado del
# borrador, que no cambio de intencion aunque el texto ahora venga del banco.


def test_el_manual_con_url_usa_screenshot(db_path):
    resultado = linkedin_job_handler({
        "db_path": db_path,
        "lote": "l-man",
        "job_id": None,
        "ahora": AHORA.isoformat(),
        "contexto_manual": "Salió la tienda de Biciconde y ya está vendiendo.",
        "imagen_url": "https://biciconde.uy",
    })

    b = resultado["borradores"][0]
    assert b["imagen_tipo"] == "screenshot"
    assert b["imagen_spec"]["url"] == "https://biciconde.uy"


def test_el_manual_sin_url_lleva_tarjeta(db_path):
    resultado = linkedin_job_handler({
        "db_path": db_path,
        "lote": "l-man",
        "job_id": None,
        "ahora": AHORA.isoformat(),
        "contexto_manual": "Entregamos el sistema de stock de una bloquera.",
        "frase": "El mostrador dejó el cuaderno",
    })

    b = resultado["borradores"][0]
    assert b["imagen_tipo"] == "tarjeta"
    assert b["imagen_spec"]["frase"] == "El mostrador dejó el cuaderno"


def test_el_manual_sin_frase_cae_a_la_primera_linea(db_path):
    resultado = linkedin_job_handler({
        "db_path": db_path,
        "lote": "l-man",
        "job_id": None,
        "ahora": AHORA.isoformat(),
        "contexto_manual": "El mostrador dejó el cuaderno.\n\nY lo que sigue.",
    })

    b = resultado["borradores"][0]
    assert b["imagen_tipo"] == "tarjeta"
    assert b["imagen_spec"]["frase"] == "El mostrador dejó el cuaderno"


def test_el_manual_no_consume_posts_del_banco(db_path):
    """Un post a mano no puede quemar uno del banco."""
    linkedin_job_handler({
        "db_path": db_path,
        "lote": "l-man",
        "job_id": None,
        "ahora": AHORA.isoformat(),
        "contexto_manual": "Algo real que pasó esta semana en un proyecto.",
    })

    conn = sqlite3.connect(db_path)
    usados = conn.execute(
        "SELECT COUNT(*) FROM linkedin_banco WHERE usado_en IS NOT NULL"
    ).fetchone()[0]
    conn.close()

    assert usados == 0


def test_un_manual_en_blanco_cae_al_camino_programado(db_path):
    """Un `contexto_manual` vacio no es un pedido a mano, es no haber mandado
    ninguno. En ese caso sale el par educativo de siempre."""
    resultado = linkedin_job_handler({
        "db_path": db_path,
        "lote": "l-man",
        "job_id": None,
        "ahora": AHORA.isoformat(),
        "contexto_manual": "   ",
    })

    assert [b["tipo"] for b in resultado["borradores"]] == ["educativo", "educativo"]


def test_el_banco_vacio_falla_fuerte(db_path):
    """Sin posts no hay nada que mandar, y hay que enterarse.

    El workflow de Actions tiene un paso `if: failure()` que avisa por mail, y
    antes de llegar a esto el `aviso_cooldown` ya viene avisando en cada mail
    que quedan pocos. Fallar es preferible a mandar un mail vacio.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM linkedin_banco")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="banco"):
        linkedin_job_handler({
            "db_path": db_path,
            "lote": "l-vacio",
            "job_id": None,
            "ahora": AHORA.isoformat(),
        })


def test_el_educativo_usa_la_frase_del_banco_en_la_tarjeta(db_path):
    resultado = linkedin_job_handler({
        "db_path": db_path,
        "lote": "l-edu",
        "job_id": None,
        "ahora": AHORA.isoformat(),
    })

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    frases = {r["frase"] for r in conn.execute("SELECT frase FROM linkedin_banco")}
    conn.close()

    for b in resultado["borradores"]:
        assert b["imagen_tipo"] == "tarjeta"
        assert b["imagen_spec"]["frase"] in frases


def test_las_reglas_de_voz_no_encasillan_por_tamano():
    """El encuadre de PyME salió de un supuesto escrito, no del modelo."""
    from services.linkedin_posts import REGLAS_DE_VOZ

    assert "digitaliza PyMEs" not in REGLAS_DE_VOZ
    assert "#PyMEs" in REGLAS_DE_VOZ          # aparece como prohibición
    assert "Encasillar al lector por tamaño" in REGLAS_DE_VOZ


def test_las_reglas_de_voz_estan_escritas_con_tildes():
    """Las lee quien escribe los posts a mano: un texto sin tildes enseña mal."""
    from services.linkedin_posts import REGLAS_DE_VOZ

    assert "Ortografía correcta" in REGLAS_DE_VOZ
    for palabra in ("página", "gestión", "español", "máximo"):
        assert palabra in REGLAS_DE_VOZ.lower(), palabra


def test_el_arranque_siembra_el_banco():
    """Sin esto la tabla queda vacia en produccion y el cron falla el martes."""
    fuente = Path("server.py").read_text(encoding="utf-8")

    assert "seed_linkedin_banco" in fuente


def test_el_endpoint_pasa_la_frase_del_post_manual(tmp_path, monkeypatch):
    import json as _json

    import dashboard
    from database import init_db as _init

    monkeypatch.setenv("ADMIN_TOKEN", "secreto")
    db = str(tmp_path / "t.db")
    _init(db)
    seed_linkedin_banco(db)
    app = dashboard.create_app(db)
    app.config["TESTING"] = True
    monkeypatch.setattr("routes.linkedin.get_worker", lambda: object())

    r = app.test_client().post(
        "/api/linkedin/generar",
        headers={"x-admin-token": "secreto"},
        json={"contexto_manual": "Entregamos el sistema de stock de una bloquera.",
              "frase": "El mostrador dejó el cuaderno"},
    )

    assert r.status_code == 202
    payload = _json.loads(sqlite3.connect(db).execute(
        "SELECT payload FROM jobs WHERE id = ?", (r.get_json()["job_id"],)
    ).fetchone()[0])
    assert payload["frase"] == "El mostrador dejó el cuaderno"


def test_el_par_del_mail_son_dos_temas_distintos(db_path):
    """Los dos borradores de una corrida no pueden ser el mismo tema.

    Cada tema tiene sus dos angulos en filas contiguas del banco. Tomando los
    dos mas viejos salen los dos angulos del mismo tema, y el mail queda con
    dos posts sobre lo mismo. Antes del banco esto no pasaba porque se elegian
    dos temas distintos, y tiene que seguir sin pasar.
    """
    resultado = linkedin_job_handler({
        "db_path": db_path,
        "lote": "lote-de-prueba",
        "job_id": None,
        "ahora": AHORA.isoformat(),
    })

    temas = [b["fuente_desc"] for b in resultado["borradores"]]

    assert temas[0] != temas[1], f"los dos posts son del mismo tema: {temas[0]}"


def test_un_tema_no_vuelve_hasta_agotar_los_demas(db_path):
    """Martes y viernes no pueden hablar del mismo tema.

    Los dos angulos de un tema son filas contiguas, asi que ordenando por
    antiguedad la corrida del viernes agarra el otro angulo de los mismos dos
    temas del martes. Para el que sigue la pagina, eso es la misma semana
    hablando dos veces de lo mismo.
    """
    from datetime import timedelta

    ahora = AHORA
    vistos = []
    for i in range(21):                 # media vuelta al banco: 42 posts
        r = linkedin_job_handler({
            "db_path": db_path, "lote": f"l{i}",
            "job_id": None, "ahora": ahora.isoformat(),
        })
        vistos += [b["fuente_desc"] for b in r["borradores"]]
        ahora += timedelta(days=3 if i % 2 == 0 else 4)

    repetidos = {t for t in vistos if vistos.count(t) > 1}

    assert not repetidos, f"temas repetidos en las primeras 21 corridas: {repetidos}"
