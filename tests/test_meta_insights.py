"""Sincronizacion del gasto de Meta.

Ningun test toca la red: `sincronizar` recibe la funcion que trae los datos.
"""

import io

import pytest

from database import _connect, init_db
from services.meta_insights import hay_credenciales, sincronizar


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def _respuesta(spend="100.50", leads=3):
    """Una fila de Insights con la forma real que devuelve el Graph."""
    return [{
        "date_start": "2026-03-01",
        "campaign_id": "120",
        "campaign_name": "Leads - UY - 2026",
        "spend": spend,
        "account_currency": "UYU",
        "impressions": "4500",
        "clicks": "180",
        "reach": "3900",
        "actions": [
            {"action_type": "post_engagement", "value": "40"},
            {"action_type": "lead", "value": str(leads)},
        ],
    }]


def _filas(db):
    conn = _connect(db)
    try:
        return conn.execute("SELECT * FROM meta_insights ORDER BY date").fetchall()
    finally:
        conn.close()


def test_guarda_una_fila_por_campana_y_dia(db):
    r = sincronizar(db, "2026-03-01", "2026-03-01", fetch=lambda *a, **k: _respuesta())
    assert r["filas"] == 1
    fila = _filas(db)[0]
    assert fila["date"] == "2026-03-01"
    assert fila["campaign_id"] == "120"
    assert fila["spend"] == 100.5
    assert fila["impressions"] == 4500
    assert fila["clicks"] == 180
    assert fila["reach"] == 3900


def test_saca_los_leads_de_actions(db):
    """Los leads no son un campo: hay que buscarlos entre las acciones."""
    sincronizar(db, "2026-03-01", "2026-03-01",
                fetch=lambda *a, **k: _respuesta(leads=7))
    assert _filas(db)[0]["leads"] == 7


def test_sin_accion_de_lead_guarda_cero(db):
    sinlead = [{**_respuesta()[0], "actions": [
        {"action_type": "post_engagement", "value": "40"}]}]
    sincronizar(db, "2026-03-01", "2026-03-01", fetch=lambda *a, **k: sinlead)
    assert _filas(db)[0]["leads"] == 0


def test_sin_actions_no_rompe(db):
    """Una campana sin ninguna accion no trae la clave."""
    sinactions = [{k: v for k, v in _respuesta()[0].items() if k != "actions"}]
    sincronizar(db, "2026-03-01", "2026-03-01", fetch=lambda *a, **k: sinactions)
    assert _filas(db)[0]["leads"] == 0


def test_reconoce_los_nombres_alternativos_del_action_type(db):
    """El nombre cambio entre versiones y conviven en cuentas viejas."""
    otro = [{**_respuesta()[0], "actions": [
        {"action_type": "onsite_conversion.lead_grouped", "value": "5"}]}]
    sincronizar(db, "2026-03-01", "2026-03-01", fetch=lambda *a, **k: otro)
    assert _filas(db)[0]["leads"] == 5


def test_guarda_la_moneda_sin_convertir(db):
    """Convertir gasto de marzo con la cotizacion de hoy da un numero que
    parece preciso y no lo es."""
    sincronizar(db, "2026-03-01", "2026-03-01", fetch=lambda *a, **k: _respuesta())
    assert _filas(db)[0]["currency"] == "UYU"


def test_es_idempotente(db):
    """Correrlo dos veces deja una fila, no dos."""
    def f(*a, **k):
        return _respuesta()
    sincronizar(db, "2026-03-01", "2026-03-01", fetch=f)
    sincronizar(db, "2026-03-01", "2026-03-01", fetch=f)
    assert len(_filas(db)) == 1


def test_resincronizar_actualiza_el_valor(db):
    """Meta ajusta cifras hacia atras: la ultima corrida manda."""
    sincronizar(db, "2026-03-01", "2026-03-01",
                fetch=lambda *a, **k: _respuesta("100.50"))
    sincronizar(db, "2026-03-01", "2026-03-01",
                fetch=lambda *a, **k: _respuesta("133.00"))
    filas = _filas(db)
    assert len(filas) == 1
    assert filas[0]["spend"] == 133.0


def test_una_fila_sin_campaign_id_se_saltea(db):
    """Sin id no hay con que emparejarla ni contra que hacer upsert."""
    sinid = [{k: v for k, v in _respuesta()[0].items() if k != "campaign_id"}]
    sincronizar(db, "2026-03-01", "2026-03-01", fetch=lambda *a, **k: sinid)
    assert _filas(db) == []


def test_sin_credenciales_se_saltea_sin_romper(db, monkeypatch):
    """El resto del modulo tiene que funcionar sin gasto."""
    monkeypatch.delenv("META_ADS_TOKEN", raising=False)
    monkeypatch.delenv("META_AD_ACCOUNT_ID", raising=False)
    assert hay_credenciales() is False
    r = sincronizar(db, "2026-03-01", "2026-03-01")
    assert r["salteado"] == "sin_credenciales"
    assert r["filas"] == 0


def test_una_respuesta_vacia_no_rompe(db):
    r = sincronizar(db, "2026-03-01", "2026-03-01", fetch=lambda *a, **k: [])
    assert r["filas"] == 0


def test_usa_la_misma_version_de_graph_que_el_resto():
    """Una version propia se desincroniza en silencio. Ya paso con el webhook
    de leadgen, que quedo fijado en v25.0 y Meta dejo de entregar.

    Se importa de `meta_config`, que existe justo para eso y no arrastra Flask
    ni la base, no de `routes.meta`."""
    fuente = io.open("services/meta_insights.py", encoding="utf-8").read()
    assert "from meta_config import GRAPH" in fuente
    assert "graph.facebook.com" not in fuente


def test_el_modulo_no_importa_anthropic():
    """La fase 1 no gasta un token."""
    fuente = io.open("services/meta_insights.py", encoding="utf-8").read()
    assert "anthropic" not in fuente.lower()
