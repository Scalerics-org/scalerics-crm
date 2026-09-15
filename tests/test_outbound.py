"""Los nombres que definio Juan el 14/9 para tres paneles.

- Metricas: primero se llamo "Outbound" (sin la pestaña Meta Ads, que se mira en
  Marketing) y despues "Inteligencia comercial".
- Cola: pasa a llamarse "Outbound".
- Marketing: pasa a llamarse "Inteligencia marketing".

Por dentro los paneles siguen siendo `metrics`, `cola` y `marketing`: los
permisos de cada rol estan guardados con esos nombres, y renombrarlos les
sacaria el panel a todos. Solo cambia lo que se ve.
"""

import re
from pathlib import Path

from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db

HTML = dashboard.DASHBOARD_HTML
SRC = Path(dashboard.__file__).read_text(encoding="utf-8")


def _nav(panel: str) -> str:
    m = re.search(r'id="nav-' + panel + r'"[^>]*>.*?</i>\s*([^<]+?)\s*(?:<span|</div>)', HTML)
    assert m, f"no encontre el item de menu de {panel}"
    return m.group(1)


def test_metricas_se_llama_inteligencia_comercial():
    assert _nav("metrics") == "Inteligencia comercial"
    assert "<h1>Inteligencia comercial</h1>" in HTML
    assert "<h1>Métricas</h1>" not in HTML
    assert "metrics:'Intel. comercial'" in HTML            # barra del celular
    assert "metrics:'Inteligencia comercial'" in SRC       # permisos


def test_cola_se_llama_outbound():
    assert _nav("cola") == "Outbound"
    assert "<h1>Outbound</h1>" in HTML
    assert "<h1>Cola de llamadas</h1>" not in HTML
    assert "cola:'Outbound'" in HTML
    assert "{cola:'Outbound'," in SRC


def test_marketing_se_llama_inteligencia_marketing():
    assert _nav("marketing") == "Inteligencia marketing"
    assert "<h1>Inteligencia marketing</h1>" in HTML


def test_outbound_no_quedo_en_dos_paneles():
    """Al renombrar Metricas y Cola en la misma tanda, "Outbound" tiene que
    quedar solo en la Cola."""
    assert _nav("metrics") != "Outbound"
    assert HTML.count("<h1>Outbound</h1>") == 1
    assert "metrics:'Outbound'" not in SRC
    assert "metrics:'Métricas'" not in SRC


def test_no_queda_nada_de_la_pestaña_meta():
    for resto in ('id="metrics-meta"', 'id="tab-meta-btn"', 'id="tab-sdr-btn"',
                  "/api/metrics/meta", "switchMetricsTab", 'id="mm-'):
        assert resto not in HTML, resto


def test_los_paneles_siguen_con_su_nombre_por_dentro():
    """Los permisos guardados dicen 'metrics', 'cola' y 'marketing'."""
    for panel in ("metrics", "cola", "marketing"):
        assert f'id="{panel}-panel"' in HTML, panel
        assert f"showPanel('{panel}')" in HTML, panel
    assert "if (name === 'metrics') loadMetrics();" in HTML
    for lista in re.findall(r"const ALL_PANELS = \[([^\]]*)\]", SRC):
        assert "'metrics'" in lista and "'cola'" in lista


def test_lo_del_panel_sigue_y_la_pagina_abre(tmp_path, monkeypatch):
    for id_ in ("m-total", "m-contacted", "m-funnel", "m-calls", "m-months",
                "m-rubros", "m-cities"):
        assert f'id="{id_}"' in HTML, id_

    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    db = str(tmp_path / "nombres.db")
    init_db(db)
    app = dashboard.create_app(db)
    uid = create_user(db, name="Jefe", email="jefe@test.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    cli = app.test_client()
    with cli.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Jefe"

    assert cli.get("/").status_code == 200
    assert cli.get("/api/metrics").status_code == 200
