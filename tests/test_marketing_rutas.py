"""Las rutas del modulo de marketing.

Los GET piden el panel `marketing` porque muestran gasto publicitario. Los POST
van por x-admin-token, igual que /api/linkedin/generar, porque los llama el cron.
"""

import re

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


# El workflow se lee a mano en vez de con PyYAML a proposito: pyyaml no esta en
# requirements.txt y agregarlo cargaria una dependencia a la imagen de
# produccion —una maquina de 256 MB— para satisfacer a tres tests. El archivo es
# nuestro y su forma es predecible; si alguien la cambia, estos tests fallan
# ruidosamente en vez de pasar de largo, que es lo que se quiere.

def _texto_del_workflow() -> str:
    import io
    return io.open(".github/workflows/radiografia.yml", encoding="utf-8").read()


def _crones() -> list:
    """Los crons activos. Una linea comentada no cuenta: es el estado anterior."""
    return [l.split("cron:", 1)[1].strip().strip('"\'')
            for l in _texto_del_workflow().splitlines()
            if l.strip().startswith("- cron:")]


def _paso(marca: str) -> str:
    """El bloque de texto de un paso, buscado por algo que aparezca en su `name`
    o en su `id`. Va desde su `- name:` hasta el siguiente."""
    bloques = re.split(r"^      - name: ", _texto_del_workflow(), flags=re.M)[1:]
    encontrados = [b for b in bloques if marca.lower() in b.lower().split("\n")[0]
                   or f"id: {marca}" in b]
    assert encontrados, f"no encontre ningun paso que matchee {marca!r}"
    return encontrados[0]


def _condicion(bloque: str) -> str:
    """El `if:` de un paso, aplanado. Soporta el bloque plegado `>-`."""
    m = re.search(r"^        if:(.*?)^        [a-z]", bloque + "\n        z",
                  re.M | re.S)
    return " ".join(m.group(1).replace(">-", " ").split()) if m else ""


def _dias_del_cron(expr: str) -> set:
    """Los dias de la semana que cubre un cron, como enteros 0..6."""
    campo = expr.split()[4]
    dias = set()
    for parte in campo.split(","):
        if "-" in parte:
            a, b = (int(x) for x in parte.split("-"))
            dias |= set(range(a, b + 1))
        else:
            dias.add(int(parte))
    return dias


def test_el_gasto_se_trae_todos_los_dias_y_el_informe_solo_los_lunes():
    """Las dos cadencias no son un capricho: la diferencia es plata.

    Traer el gasto es gratis y envejece a diario —Meta cobra todos los dias y
    ademas corrige los ultimos hacia atras—. El informe le pide ~21.000 tokens
    a Opus 5. Con una sola cadencia, o el gasto queda viejo seis dias o el
    informe cuesta siete veces mas sin decir nada nuevo.
    """
    crones = _crones()
    assert len(crones) == 2, f"deberian ser dos crons y hay {len(crones)}: {crones}"

    semanal = "0 11 * * 1"
    assert semanal in crones, "falta el cron de los lunes"
    diario = [c for c in crones if c != semanal][0]

    lunes, resto = _dias_del_cron(semanal), _dias_del_cron(diario)
    assert lunes == {1}
    assert not (lunes & resto), (
        f"el cron diario ({diario}) pisa al lunes: los dos dispararian y habria "
        "una sincronizacion al pedo")
    assert lunes | resto == set(range(7)), (
        f"entre los dos crons no se cubren los 7 dias: falta "
        f"{sorted(set(range(7)) - (lunes | resto))}")


def test_el_paso_del_informe_esta_atado_al_cron_semanal():
    """Si alguien saca este `if`, el informe pasa a correr todos los dias y la
    factura se multiplica por siete sin que nadie lo note."""
    condicion = _condicion(_paso("Generar la radiografía"))
    assert "github.event.schedule == '0 11 * * 1'" in condicion, condicion
    assert "workflow_dispatch" in condicion, condicion


def test_el_paso_que_trae_el_gasto_no_esta_condicionado():
    """Es el que tiene que correr todos los dias; una condicion aca lo apagaria
    sin que se vea."""
    condicion = _condicion(_paso("Sincronizar el gasto"))
    assert not condicion, f"el paso del gasto tiene condicion: {condicion}"


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


def test_la_version_dice_que_imagen_corre(cliente, monkeypatch):
    """Existe porque no habia forma de contestar "¿estoy viendo lo ultimo?" sin
    entrar por SSH. Un deploy en Fly es una carrera —la ultima imagen gana— y
    del lado del navegador no quedaba ningun rastro de cual quedo."""
    monkeypatch.setenv("FLY_IMAGE_REF",
                       "registry.fly.io/scalerics-crm:deployment-01ABCDEF")
    monkeypatch.setenv("FLY_MACHINE_ID", "897576f6e60428")
    r = cliente.get("/api/marketing/version", headers=_AUTH)
    assert r.status_code == 200
    d = r.get_json()
    assert d["imagen"] == "deployment-01ABCDEF"
    assert d["maquina"] == "897576f6e60428"


def test_la_version_no_revienta_fuera_de_fly(cliente, monkeypatch):
    """En local no existen esas variables y el panel tiene que cargar igual."""
    monkeypatch.delenv("FLY_IMAGE_REF", raising=False)
    monkeypatch.delenv("FLY_MACHINE_ID", raising=False)
    r = cliente.get("/api/marketing/version", headers=_AUTH)
    assert r.status_code == 200
    assert r.get_json()["imagen"] is None
