"""El panel Metricas pasa a llamarse Outbound y deja de mostrar Meta Ads.

Pedido de Juan (14/9): lo de Meta se mira en Marketing, asi que la pestaña
"Meta Ads" sobraba, y el panel queda con lo del outbound (SDR: cola, llamadas,
rubros, ciudades).

El panel sigue siendo `metrics` por dentro: los permisos de cada rol estan
guardados con ese nombre en la base, y renombrarlo le sacaria el panel a todos
sin avisar. Solo cambia lo que se ve.
"""

import re

from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db

HTML = dashboard.DASHBOARD_HTML


def test_el_panel_se_llama_outbound():
    assert "<h1>Outbound</h1>" in HTML
    assert re.search(r"id=\"nav-metrics\"[^>]*>.*?Outbound</div>", HTML)
    assert "<h1>Métricas</h1>" not in HTML


def test_los_nombres_cortos_tambien_dicen_outbound():
    """La barra del celular (NAV_LABELS) y la pantalla de permisos
    (PANEL_LABELS) tienen su propia copia del nombre."""
    assert "metrics:'Métricas'" not in HTML
    assert HTML.count("metrics:'Outbound'") >= 1


def test_no_queda_nada_de_la_pestaña_meta():
    for resto in ('id="metrics-meta"', 'id="tab-meta-btn"', 'id="tab-sdr-btn"',
                  "/api/metrics/meta", "switchMetricsTab", 'id="mm-'):
        assert resto not in HTML, resto


def test_el_panel_sigue_siendo_metrics_por_dentro():
    """Los permisos guardados dicen 'metrics': cambiar el id se los saca a todos."""
    assert 'id="metrics-panel"' in HTML
    assert "showPanel('metrics')" in HTML
    assert "if (name === 'metrics') loadMetrics();" in HTML
    for lista in re.findall(r"const ALL_PANELS = \[([^\]]*)\]", HTML):
        assert "'metrics'" in lista


def test_lo_del_outbound_sigue(tmp_path, monkeypatch):
    for id_ in ("m-total", "m-contacted", "m-funnel", "m-calls", "m-months",
                "m-rubros", "m-cities"):
        assert f'id="{id_}"' in HTML, id_

    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    db = str(tmp_path / "outbound.db")
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
