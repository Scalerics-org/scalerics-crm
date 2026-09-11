"""Cuándo llegan los leads: dia de la semana por franja horaria.

Existe porque el panel sabia cuantos leads entran y de donde, pero no CUANDO. Y
el cuando es accionable de una forma directa: si la mitad llega un sabado a la
noche y nadie contesta hasta el lunes, eso no se arregla con mas pauta.

El grano es de tres horas y no de una: con ~1,4 leads por dia, 24x7 celdas dan
168 casilleros casi todos en cero y ningun patron visible.
"""

import pytest

from database import _connect, init_db
from services.dossier import llegada_de_leads


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def _lead(db, cuando, nombre=None):
    conn = _connect(db)
    try:
        conn.execute(
            "INSERT INTO businesses (name, phone, source, scraped_at) "
            "VALUES (?,?,?,?)",
            (nombre or cuando, cuando, "meta", cuando))
        conn.commit()
    finally:
        conn.close()


def _celda(mapa, dia, franja):
    iguales = [c for c in mapa["celdas"] if c["dia"] == dia and c["franja"] == franja]
    assert iguales, f"no hay celda {dia}/{franja}"
    return iguales[0]


def test_cuenta_el_lead_en_su_dia_y_su_franja(db):
    # 2026-03-05 es jueves. 14:30 UTC son las 11:30 en Montevideo: franja de 9.
    _lead(db, "2026-03-05 14:30:00")
    mapa = llegada_de_leads(db, "2026-03-01", "2026-03-31")
    assert _celda(mapa, 3, 9)["n"] == 1


def test_la_hora_se_pasa_a_montevideo():
    """`scraped_at` guarda el created_time de Meta, que viene en UTC.

    Sin convertir, el mapa dice que el pico es de madrugada —que no es lo que
    hace la gente— y el consejo que sale de ahi sale movido tres horas.
    """
    from services.dossier import HORAS_UTC_A_MONTEVIDEO
    assert HORAS_UTC_A_MONTEVIDEO == -3


def test_una_llegada_de_madrugada_utc_cae_la_noche_anterior(db):
    """01:00 UTC del jueves son las 22:00 del MIERCOLES. Si solo se corrigiera
    la hora y no el dia, el lead quedaria en el dia equivocado."""
    _lead(db, "2026-03-05 01:00:00")        # jueves 01:00 UTC
    mapa = llegada_de_leads(db, "2026-03-01", "2026-03-31")
    assert _celda(mapa, 2, 21)["n"] == 1    # miercoles, franja de las 21


def test_el_lunes_es_el_dia_cero(db):
    """Que la semana arranque el lunes y no el domingo: es como se lee acá."""
    _lead(db, "2026-03-02 13:00:00")      # lunes 13:00 UTC = 10:00 local
    mapa = llegada_de_leads(db, "2026-03-01", "2026-03-31")
    assert _celda(mapa, 0, 9)["n"] == 1


def test_las_franjas_son_de_tres_horas(db):
    """Con 1,4 leads por dia, 24 columnas son 24 ceros y ningun patron."""
    # 12:10, 13:40 y 14:59 UTC son 9:10, 10:40 y 11:59 locales: misma franja.
    for hora in ("12:10:00", "13:40:00", "14:59:00"):
        _lead(db, f"2026-03-05 {hora}")
    assert _celda(llegada_de_leads(db, "2026-03-01", "2026-03-31"), 3, 9)["n"] == 3


def test_la_grilla_viene_completa_aunque_haya_ceros(db):
    """Un heatmap con huecos no es un heatmap: el cero es informacion."""
    _lead(db, "2026-03-05 14:30:00")
    mapa = llegada_de_leads(db, "2026-03-01", "2026-03-31")
    assert len(mapa["celdas"]) == 7 * 8


def test_trae_el_maximo_para_poder_escalar(db):
    _lead(db, "2026-03-05 14:00:00")
    _lead(db, "2026-03-05 14:30:00")
    _lead(db, "2026-03-06 12:00:00")
    assert llegada_de_leads(db, "2026-03-01", "2026-03-31")["maximo"] == 2


def test_un_periodo_sin_leads_no_tiene_maximo_cero_enganoso(db):
    """Sin leads no hay un maximo de cero: no hay maximo, y el panel tiene que
    poder distinguir 'ninguno llego' de 'todos en cero'."""
    mapa = llegada_de_leads(db, "2026-03-01", "2026-03-31")
    assert mapa["total"] == 0
    assert mapa["maximo"] is None


def test_el_periodo_recorta(db):
    _lead(db, "2026-02-20 10:00:00")
    _lead(db, "2026-03-05 10:00:00")
    assert llegada_de_leads(db, "2026-03-01", "2026-03-31")["total"] == 1


def test_solo_mira_los_leads_de_meta(db):
    conn = _connect(db)
    try:
        conn.execute("INSERT INTO businesses (name, phone, source, scraped_at) "
                     "VALUES ('x','x','discovery','2026-03-05 10:00:00')")
        conn.commit()
    finally:
        conn.close()
    assert llegada_de_leads(db, "2026-03-01", "2026-03-31")["total"] == 0


def test_un_scraped_at_sin_hora_no_rompe_ni_inventa(db):
    """Los leads viejos pueden tener solo la fecha. Contarlos a medianoche
    inventaria un pico a las 00:00 que no paso."""
    _lead(db, "2026-03-05")
    mapa = llegada_de_leads(db, "2026-03-01", "2026-03-31")
    assert mapa["total"] == 0
    assert mapa["sin_hora"] == 1
