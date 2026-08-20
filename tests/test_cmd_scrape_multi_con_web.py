"""scrape-multi tiene que pasarle la bandera de discovery a los 19 departamentos.

Es el camino al volumen: `scrape` corre una consulta por vez, `scrape-multi` las
corre en paralelo sobre todo el pais. Si la bandera se pierde en el medio, la
corrida junta el padron equivocado —los comercios SIN web— y no da error: solo
devuelve negocios que no sirven para la campana de discovery.
"""

import argparse
import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from main import _DEPARTAMENTOS, cmd_scrape_multi


def _args(**extra):
    base = dict(query="inmobiliaria", max_per_dept=1, workers=1)
    base.update(extra)
    return argparse.Namespace(**base)


def _fake_run(registro):
    def run(query, max_results, db_path, verify_web=False,
            default_category="", skip_branded=False, solo_con_web=False):
        registro.append(solo_con_web)
        return 0
    return run


def test_con_web_llega_a_todos_los_departamentos(monkeypatch):
    visto = []
    monkeypatch.setattr("scraper.run", _fake_run(visto))

    cmd_scrape_multi(_args(con_web=True))

    assert len(visto) == len(_DEPARTAMENTOS), "tiene que correr sobre los 19 departamentos"
    assert all(visto), "la bandera se perdio en el camino a algun departamento"


def test_sin_la_bandera_el_modo_por_defecto_no_cambia(monkeypatch):
    visto = []
    monkeypatch.setattr("scraper.run", _fake_run(visto))

    cmd_scrape_multi(_args(con_web=False))

    assert visto and not any(visto)


def test_no_explota_si_el_atributo_no_esta(monkeypatch):
    """Mismo cuidado que en cmd_scrape: el atributo puede no venir en el Namespace."""
    visto = []
    monkeypatch.setattr("scraper.run", _fake_run(visto))

    cmd_scrape_multi(_args())

    assert visto and not any(visto)
