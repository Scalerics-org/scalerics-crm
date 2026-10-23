"""La vista previa de una PR en Vercel (scripts/preview_seed.py, server.py con CRM_PREVIEW,
.github/workflows/preview.yml): datos inventados, sin clave por defecto y sin
nada de producción."""

import sqlite3
import sys
from pathlib import Path

import pytest

from scripts import preview_seed as ps
from services import agentes as ag

RAIZ = Path(__file__).resolve().parent.parent


def test_sin_clave_no_siembra(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("PREVIEW_PASSWORD", raising=False)
    monkeypatch.setattr(sys, "argv", ["x", str(tmp_path / "p.db")])
    assert ps.main() == 1
    assert not (tmp_path / "p.db").exists()


def test_clave_corta_no_siembra(tmp_path, monkeypatch):
    monkeypatch.setenv("PREVIEW_PASSWORD", "corta")
    monkeypatch.setattr(sys, "argv", ["x", str(tmp_path / "p.db")])
    assert ps.main() == 1


def test_siembra_y_es_idempotente(tmp_path, monkeypatch):
    db = str(tmp_path / "p.db")
    monkeypatch.setenv("PREVIEW_PASSWORD", "una-clave-larga-1")
    monkeypatch.setattr(sys, "argv", ["x", db])
    assert ps.main() == 0
    assert ps.main() == 0
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0] == len(ps.LEADS)
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert conn.execute("SELECT email FROM users").fetchone()[0] == "preview@scalerics.com"
    conn.close()
    assert len(ag.listar(db)) == 1


def test_el_modo_vista_previa_no_usa_la_base_ni_los_procesos_de_produccion():
    srv = (RAIZ / "server.py").read_text(encoding="utf-8")
    bloque = srv[srv.index('if os.environ.get("CRM_PREVIEW") == "1":'):srv.index("from database import")]
    assert 'os.environ["DB_PATH"] = "/tmp/preview.db"' in bloque
    assert 'os.environ["CRM_SIN_PROCESOS_DE_FONDO"] = "true"' in bloque
    assert "PREVIEW_PASSWORD" in bloque
    # La vista previa no puede subir secretos ni la base.
    ignorado = (RAIZ / ".vercelignore").read_text(encoding="utf-8").split()
    for algo in (".env", "credentials.json", "*.db", "backups/"):
        assert algo in ignorado


def test_las_dependencias_de_la_vista_previa_alcanzan_para_arrancar():
    req = [l.strip() for l in (RAIZ / "requirements-preview.txt").read_text(encoding="utf-8").splitlines()
           if l.strip() and not l.startswith("#")]
    for paquete in ("flask", "requests", "pytz", "python-dotenv"):
        assert paquete in req
    assert "playwright" not in req


def test_el_workflow_corre_la_version_de_main():
    wf = (RAIZ / ".github" / "workflows" / "preview.yml").read_text(encoding="utf-8")
    assert "pull_request_target:" in wf
    assert "\n  pull_request:" not in wf
    assert "head.repo.full_name == github.repository" in wf
    assert "persist-credentials: false" in wf
    assert "CRM_PREVIEW=1" in wf
    assert "scalerics-crm-pr-${{ github.event.number }}.vercel.app" in wf
