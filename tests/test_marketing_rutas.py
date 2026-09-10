"""Las rutas del modulo de marketing.

Los GET piden el panel `marketing` porque muestran gasto publicitario. Los POST
van por x-admin-token, igual que /api/linkedin/generar, porque los llama el cron.
"""

import pytest

from dashboard import create_app
from database import _connect, init_db

_AUTH = {"x-admin-token": "token-de-prueba"}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "token-de-prueba")
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    aplicacion = create_app(ruta)
    aplicacion.config["TESTING"] = True
    return aplicacion


@pytest.fixture
def cliente(app):
    return app.test_client()


def _lead(app, nombre, campana="Leads - UY - 2026"):
    conn = _connect(app.config["DB_PATH"])
    try:
        conn.execute("INSERT INTO businesses (name, phone, source, scraped_at, "
                     "meta_campaign_name) VALUES (?,?,?,?,?)",
                     (nombre, nombre, "meta", "2026-03-05", campana))
        conn.commit()
    finally:
        conn.close()


def test_el_dossier_sin_credenciales_no_se_puede_ver(cliente):
    """Muestra gasto publicitario: no es publico."""
    r = cliente.get("/api/marketing/dossier")
    assert r.status_code in (302, 401, 403)


def test_un_token_equivocado_no_entra(cliente):
    r = cliente.get("/api/marketing/dossier",
                    headers={"x-admin-token": "cualquier-cosa"})
    assert r.status_code in (302, 401, 403)


def test_el_dossier_con_admin_token_contesta(app, cliente):
    _lead(app, "a")
    r = cliente.get("/api/marketing/dossier?desde=2026-03-01&hasta=2026-03-31",
                    headers=_AUTH)
    assert r.status_code == 200
    datos = r.get_json()
    assert datos["periodo"] == {"desde": "2026-03-01", "hasta": "2026-03-31"}
    assert {"campanas", "segmentos", "serie_semanal", "conciliacion",
            "tiempos", "recordatorios"} <= set(datos)


def test_el_dossier_tiene_un_periodo_por_defecto(app, cliente):
    """Sin parametros, los ultimos 90 dias."""
    r = cliente.get("/api/marketing/dossier", headers=_AUTH)
    assert r.status_code == 200
    assert r.get_json()["periodo"]["desde"]


@pytest.mark.parametrize("query", [
    "?desde=ayer&hasta=hoy",
    "?desde=2026-13-01&hasta=2026-03-31",
    "?desde=2026-03-31&hasta=2026-03-01",     # al reves
])
def test_una_fecha_invalida_da_400_y_no_500(app, cliente, query):
    r = cliente.get(f"/api/marketing/dossier{query}", headers=_AUTH)
    assert r.status_code == 400


def test_sync_insights_sin_token_no_corre(cliente):
    r = cliente.post("/api/marketing/sync-insights")
    assert r.status_code in (302, 401, 403)


def test_sync_insights_sin_credenciales_avisa_y_no_rompe(app, cliente):
    r = cliente.post("/api/marketing/sync-insights", headers=_AUTH)
    assert r.status_code == 200
    assert r.get_json()["salteado"] == "sin_credenciales"


def test_sync_insights_rechaza_una_ventana_absurda(app, cliente):
    r = cliente.post("/api/marketing/sync-insights?dias=99999", headers=_AUTH)
    assert r.status_code == 400


def test_backfill_de_campanas_por_ruta(app, cliente):
    conn = _connect(app.config["DB_PATH"])
    try:
        conn.execute("INSERT INTO businesses (name, phone, source, notes) "
                     "VALUES ('a','a','meta','Meta Lead Ad · Leads - UY - 2026')")
        conn.commit()
    finally:
        conn.close()
    r = cliente.post("/api/marketing/backfill-campanas", headers=_AUTH)
    assert r.status_code == 200
    assert r.get_json()["escritos"] == 1


def test_generar_guarda_un_snapshot(app, cliente):
    _lead(app, "a")
    r = cliente.post("/api/marketing/generar?desde=2026-03-01&hasta=2026-03-31",
                     headers=_AUTH)
    assert r.status_code == 200
    assert r.get_json()["status"] == "sin_ia"

    conn = _connect(app.config["DB_PATH"])
    try:
        fila = conn.execute("SELECT status, report_json FROM radiografias").fetchone()
    finally:
        conn.close()
    assert fila["status"] == "sin_ia"
    assert fila["report_json"] is None


def test_el_dossier_es_json_serializable(app, cliente):
    """Un Decimal o un sqlite3.Row adentro rompe el jsonify en produccion."""
    _lead(app, "a")
    r = cliente.get("/api/marketing/dossier", headers=_AUTH)
    assert r.status_code == 200
    assert r.is_json


def test_generar_sin_ia_sigue_guardando_el_dossier(app, cliente, monkeypatch):
    """El panel nunca dependio del informe: los graficos salen del dossier."""
    monkeypatch.delenv("RADIOGRAFIA_IA_ACTIVA", raising=False)
    _lead(app, "a")
    r = cliente.post("/api/marketing/generar", headers=_AUTH)
    assert r.status_code == 200
    d = r.get_json()
    assert d["status"] == "sin_ia"

    conn = _connect(app.config["DB_PATH"])
    try:
        fila = conn.execute("SELECT dossier_json, report_json FROM radiografias"
                            ).fetchone()
    finally:
        conn.close()
    assert fila["dossier_json"]
    assert fila["report_json"] is None


def test_el_workflow_del_cron_nace_con_el_schedule_comentado():
    """No se prende solo: cada corrida cuesta plata. Se descomenta el dia que
    se decida gastar."""
    import io
    y = io.open(".github/workflows/radiografia.yml", encoding="utf-8").read()
    assert "workflow_dispatch" in y
    activos = [l.strip() for l in y.splitlines() if l.strip().startswith("- cron:")]
    assert not activos, f"el schedule esta activo: {activos}"


# ── El informe de la IA ──────────────────────────────────────────────────────

def _snapshot(app, status="ok", informe=None, error=None):
    import json
    conn = _connect(app.config["DB_PATH"])
    try:
        conn.execute(
            "INSERT INTO radiografias (period_start, period_end, dossier_json, "
            "report_json, model, tokens_in, tokens_out, status, error_message) "
            "VALUES ('2026-03-01','2026-09-30','{}',?,?,?,?,?,?)",
            (json.dumps(informe, ensure_ascii=False) if informe else None,
             "claude-opus-5", 31000, 6000, status, error))
        conn.commit()
    finally:
        conn.close()


def test_sin_ninguna_corrida_contesta_vacio_y_no_404(app, cliente):
    """El panel tiene que poder pintar 'todavia no corrio' sin tratar eso como
    un error."""
    r = cliente.get("/api/marketing/radiografia", headers=_AUTH)
    assert r.status_code == 200
    assert r.get_json()["informe"] is None
    assert r.get_json()["status"] is None


def test_devuelve_el_informe_de_la_ultima_corrida(app, cliente):
    _snapshot(app, informe={"resumen": "Viejo", "hallazgos": [],
                            "cambios_desde_la_ultima": []})
    _snapshot(app, informe={"resumen": "Nuevo", "hallazgos": [],
                            "cambios_desde_la_ultima": []})
    d = cliente.get("/api/marketing/radiografia", headers=_AUTH).get_json()
    assert d["informe"]["resumen"] == "Nuevo"
    assert d["status"] == "ok"
    assert d["tokens"]["entrada"] == 31000
    assert d["periodo"] == {"desde": "2026-03-01", "hasta": "2026-09-30"}


def test_una_corrida_sin_ia_se_reporta_como_tal(app, cliente):
    """Y no como un error: la IA apagada es un estado esperado."""
    _snapshot(app, status="sin_ia")
    d = cliente.get("/api/marketing/radiografia", headers=_AUTH).get_json()
    assert d["status"] == "sin_ia"
    assert d["informe"] is None


def test_un_informe_rechazado_no_se_devuelve_pero_si_el_motivo(app, cliente):
    """Nunca se publica un informe sin validar, pero el motivo se puede mirar."""
    _snapshot(app, status="error_validacion",
              error="el número 47,20 no existe en el dossier")
    d = cliente.get("/api/marketing/radiografia", headers=_AUTH).get_json()
    assert d["informe"] is None
    assert d["status"] == "error_validacion"
    assert "47" in d["error"]


def test_el_informe_no_se_puede_ver_sin_credenciales(cliente):
    r = cliente.get("/api/marketing/radiografia")
    assert r.status_code in (302, 401, 403)
