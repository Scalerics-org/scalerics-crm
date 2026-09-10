"""Sacar la campana de adentro de `notes` a su columna.

Los 238 leads historicos la tienen como texto: "Meta Lead Ad · <campana>". Los
nombres reales medidos en la base son "Leads - Form - 2026", "Leads - UY - 2026",
"Leads - ARG - CH - 2026", "Leads - ARG - 2026" y "Leads - Abril 2026". Trece
leads no traen campana ninguna.
"""

import pytest

from database import _connect, init_db
from services.meta_campanas import backfill_campanas, parsear_campana


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def _lead(db, nombre, notes, source="meta", telefono=None):
    conn = _connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO businesses (name, notes, source, phone) VALUES (?,?,?,?)",
            (nombre, notes, source, telefono or nombre))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _campana_de(db, lead_id):
    conn = _connect(db)
    try:
        fila = conn.execute("SELECT meta_campaign_name FROM businesses WHERE id=?",
                            (lead_id,)).fetchone()
        return fila["meta_campaign_name"]
    finally:
        conn.close()


@pytest.mark.parametrize("notes,esperado", [
    ("Meta Lead Ad · Leads - UY - 2026", "Leads - UY - 2026"),
    ("Meta Lead Ad · Leads - ARG - CH - 2026", "Leads - ARG - CH - 2026"),
    ("Meta Lead Ad · Leads - Abril 2026", "Leads - Abril 2026"),
])
def test_parsea_los_nombres_reales(notes, esperado):
    assert parsear_campana(notes) == esperado


def test_solo_mira_la_primera_linea():
    """El vendedor escribe notas debajo; no son parte del nombre."""
    notes = "Meta Lead Ad · Leads - UY - 2026\nLlame el martes, no atendio."
    assert parsear_campana(notes) == "Leads - UY - 2026"


def test_sin_campana_devuelve_none():
    assert parsear_campana("Meta Lead Ad") is None
    assert parsear_campana("Meta Lead Ad · ") is None
    assert parsear_campana("") is None
    assert parsear_campana(None) is None


def test_una_nota_que_no_es_de_meta_devuelve_none():
    assert parsear_campana("Lo llamo Juan, pidio presupuesto") is None


def test_backfill_escribe_la_columna(db):
    lead = _lead(db, "Peluqueria Ana", "Meta Lead Ad · Leads - UY - 2026")
    r = backfill_campanas(db)
    assert r["escritos"] == 1
    assert _campana_de(db, lead) == "Leads - UY - 2026"


def test_backfill_cuenta_los_que_no_tienen_campana(db):
    _lead(db, "Sin campana", "Meta Lead Ad")
    r = backfill_campanas(db)
    assert r["sin_campana"] == 1
    assert r["escritos"] == 0


def test_backfill_no_toca_leads_que_no_son_de_meta(db):
    """Discovery y el padron tienen sus propias notas; no son campanas."""
    lead = _lead(db, "Ferreteria", "Meta Lead Ad · Leads - UY - 2026",
                 source="discovery")
    backfill_campanas(db)
    assert _campana_de(db, lead) is None


def test_backfill_es_idempotente(db):
    """Correrlo dos veces no reescribe nada la segunda."""
    _lead(db, "Peluqueria Ana", "Meta Lead Ad · Leads - UY - 2026")
    backfill_campanas(db)
    r = backfill_campanas(db)
    assert r["escritos"] == 0


def test_backfill_no_pisa_una_campana_ya_cargada(db):
    """La ingesta nueva escribe el id y el nombre; el backfill no los toca."""
    lead = _lead(db, "Nuevo", "Meta Lead Ad · Leads - UY - 2026")
    conn = _connect(db)
    try:
        conn.execute("UPDATE businesses SET meta_campaign_name=?, meta_campaign_id=? "
                     "WHERE id=?", ("Nombre de la API", "12345", lead))
        conn.commit()
    finally:
        conn.close()
    backfill_campanas(db)
    assert _campana_de(db, lead) == "Nombre de la API"
