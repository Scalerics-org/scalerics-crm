"""Buscar un negocio por su mail, en SQL y no recorriendo la tabla entera.

`routes/web.py` y `routes/calendly.py` hacían lo mismo: traían los 8.358 leads a
memoria y los recorrían en Python buscando un mail. Son ~38 MB de objetos por
request, y en `web.py` eso cuelga de `/api/web/lead`, que es público. La máquina
tenía 12 MB libres y el OOM killer se llevó un worker el 9-9-2026.

Estos tests fijan lo que la búsqueda tiene que seguir haciendo igual que antes,
porque el reemplazo no puede cambiar a QUIÉN encuentra:

  mayúsculas     hay 64 mails guardados con mayúsculas en producción
  espacios       hoy no hay ninguno, pero el código viejo hacía `.strip()`
  duplicados     hay 6 mails repetidos, uno hasta 14 veces: cuál gana importa
"""

import sqlite3

import pytest

from database import get_business_by_email, init_db, insert_business, update_business


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "b.db")
    init_db(ruta)
    return ruta


_tel = iter(range(900000, 999999))


def _negocio(db, nombre, mail=None, score=None, scraped_at=None):
    bid = insert_business(db, {"name": nombre, "phone": f"+598{next(_tel)}",
                               "email": mail, "score": score,
                               "scraped_at": scraped_at})
    return bid


# ── lo básico ────────────────────────────────────────────────────────────────

def test_encuentra_por_mail_exacto(db):
    bid = _negocio(db, "Ferretería El Sol", "juan@ferreteria.com.uy")

    hallado = get_business_by_email(db, "juan@ferreteria.com.uy")

    assert hallado is not None
    assert hallado["id"] == bid


def test_no_encuentra_lo_que_no_esta(db):
    _negocio(db, "Ferretería El Sol", "juan@ferreteria.com.uy")

    assert get_business_by_email(db, "otro@cosa.com") is None


def test_una_base_sin_nadie_no_explota(db):
    assert get_business_by_email(db, "juan@ferreteria.com.uy") is None


# ── mayúsculas y espacios ────────────────────────────────────────────────────

@pytest.mark.parametrize("guardado,buscado", [
    ("Juan@Ferreteria.com.uy", "juan@ferreteria.com.uy"),
    ("juan@ferreteria.com.uy", "JUAN@FERRETERIA.COM.UY"),
    ("JUAN@FERRETERIA.COM.UY", "Juan@Ferreteria.Com.Uy"),
])
def test_no_distingue_mayusculas(db, guardado, buscado):
    """En producción hay 64 mails guardados con mayúsculas."""
    bid = _negocio(db, "Ferretería El Sol", guardado)

    hallado = get_business_by_email(db, buscado)

    assert hallado is not None and hallado["id"] == bid


def test_ignora_espacios_de_los_bordes(db):
    """El código viejo hacía `.strip()` de los dos lados."""
    bid = _negocio(db, "Ferretería El Sol", "  juan@ferreteria.com.uy  ")

    assert get_business_by_email(db, "juan@ferreteria.com.uy")["id"] == bid
    assert get_business_by_email(db, "   juan@ferreteria.com.uy ")["id"] == bid


# ── los casos que devuelven nada ─────────────────────────────────────────────

@pytest.mark.parametrize("vacio", ["", "   ", None])
def test_buscar_un_mail_vacio_no_devuelve_cualquier_cosa(db, vacio):
    """Con `email` NULL en la tabla, un buscado vacío no puede parear."""
    _negocio(db, "Sin mail", None)
    _negocio(db, "Con mail", "juan@ferreteria.com.uy")

    assert get_business_by_email(db, vacio) is None


def test_no_pareo_contra_un_mail_nulo(db):
    _negocio(db, "Sin mail", None)

    assert get_business_by_email(db, "juan@ferreteria.com.uy") is None


def test_no_pareo_contra_un_mail_vacio_guardado(db):
    bid = _negocio(db, "Mail vacio", "")

    assert get_business_by_email(db, "") is None
    assert get_business_by_email(db, "juan@ferreteria.com.uy") is None
    assert bid is not None   # el negocio existe, simplemente no se encuentra así


# ── duplicados: cuál gana ────────────────────────────────────────────────────

def test_entre_duplicados_gana_el_que_tiene_score(db):
    """Replica el orden de `get_all_businesses`, que es por donde se buscaba
    antes: primero los que tienen score, después score DESC, después
    scraped_at DESC. En producción hay 6 mails repetidos, uno 14 veces."""
    sin_score = _negocio(db, "Sin score", "repe@cosa.com", score=None,
                         scraped_at="2026-01-01T00:00:00")
    con_score = _negocio(db, "Con score", "repe@cosa.com", score=10,
                         scraped_at="2020-01-01T00:00:00")

    hallado = get_business_by_email(db, "repe@cosa.com")

    assert hallado["id"] == con_score, "el que tiene score va primero"
    assert hallado["id"] != sin_score


def test_entre_dos_con_score_gana_el_mas_alto(db):
    bajo = _negocio(db, "Score bajo", "repe@cosa.com", score=3)
    alto = _negocio(db, "Score alto", "repe@cosa.com", score=90)

    assert get_business_by_email(db, "repe@cosa.com")["id"] == alto
    assert bajo is not None


def test_entre_dos_sin_score_gana_el_mas_reciente(db):
    viejo = _negocio(db, "Viejo", "repe@cosa.com", scraped_at="2020-01-01T00:00:00")
    nuevo = _negocio(db, "Nuevo", "repe@cosa.com", scraped_at="2026-09-09T00:00:00")

    assert get_business_by_email(db, "repe@cosa.com")["id"] == nuevo
    assert viejo is not None


# ── que devuelva el negocio entero, no media fila ────────────────────────────

def test_devuelve_las_columnas_que_usa_quien_llama(db):
    """`routes/web.py` lee `id` y `name` de lo que sale de acá."""
    _negocio(db, "Ferretería El Sol", "juan@ferreteria.com.uy")
    hallado = get_business_by_email(db, "juan@ferreteria.com.uy")

    assert hallado["name"] == "Ferretería El Sol"
    assert "crm_status" in hallado
    assert "phone" in hallado


# ── que de verdad no recorra la tabla ────────────────────────────────────────

def test_resuelve_en_sql_y_no_trayendo_todo_a_memoria(db, monkeypatch):
    """El punto entero del cambio.

    Si alguien vuelve a implementarlo recorriendo `get_all_businesses`, este
    test lo agarra: no es una cuestión de estilo, es 38 MB por request en un
    endpoint público.
    """
    import database

    def _prohibido(*a, **k):
        raise AssertionError("buscar por mail no puede traer la tabla entera")

    _negocio(db, "Ferretería El Sol", "juan@ferreteria.com.uy")
    monkeypatch.setattr(database, "get_all_businesses", _prohibido)

    assert get_business_by_email(db, "juan@ferreteria.com.uy") is not None


def test_la_consulta_real_usa_el_indice(db):
    """Sin índice el SELECT sigue siendo un scan: más barato en memoria que
    traerlo a Python, pero igual O(n) en disco.

    Se explica la constante que ejecuta `get_business_by_email`, no una copia
    escrita acá: el índice es por EXPRESIÓN y por condición parcial, así que
    cualquier cambio en el `WHERE` lo deja de usar en silencio.
    """
    from database import _SQL_NEGOCIO_POR_MAIL

    conn = sqlite3.connect(db)
    try:
        plan = conn.execute("EXPLAIN QUERY PLAN " + _SQL_NEGOCIO_POR_MAIL,
                            ("x@y.com",)).fetchall()
    finally:
        conn.close()

    texto = " ".join(str(f) for f in plan).lower()
    assert "idx_businesses_email" in texto, f"no usa el indice: {plan}"
    assert "scan businesses" not in texto, f"sigue escaneando: {plan}"
