"""cmd_run_all reusa el Namespace de argparse de run-all para llamar a
cmd_scrape, y ese subparser no define --con-web (esa bandera solo existe en
el subparser de scrape). cmd_scrape no puede asumir que el atributo esta."""

import argparse
import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from main import cmd_scrape


def test_cmd_scrape_no_explota_sin_atributo_con_web(monkeypatch):
    captured = {}

    def fake_run(query, max_results, db_path, verify_web=False,
                 default_category="", skip_branded=False, solo_con_web=False):
        captured["solo_con_web"] = solo_con_web
        return 0

    monkeypatch.setattr("scraper.run", fake_run)

    # Namespace tal cual lo arma el subparser run-all: sin con_web.
    args = argparse.Namespace(query="restaurante Montevideo", max=50)

    cmd_scrape(args)

    assert captured["solo_con_web"] is False
