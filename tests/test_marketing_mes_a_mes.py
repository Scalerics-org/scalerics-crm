"""El panel de Marketing mes por mes y como una sola pauta (pedido de Juan, 14/9).

Juan, textual: "yo quiero ir mes por mes, y que en cada mes me aparezcan las que
estuvieron activas, que metricas dieron, y las que no estan mas activas y que
metricas tuvieron, pero solo de ese mes, sino me aparecen 300 y no se entiende
nada. Y las que no estan activas que no me aparezcan en gris sin que se vea".

Y: "yo quiero ver todo en una sola en terminos generales, que sea pauta en
general". Mas "fijate si hay errores en las graficas".

**Los tests de recorte miran desde una ventana que NO contiene todos los
datos** (la leccion de G del 14/9): con todas las fechas adentro del mes, un
filtro que no filtra pasa igual.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import dashboard
from dashboard import create_app
from database import _connect, init_db
from services.anuncios import limites_del_mes, piezas_del_mes
from services.dossier import serie_semanal
from services.hallazgos import buscar

RAIZ = Path(__file__).resolve().parent.parent
CHARTS = RAIZ / "static" / "charts.js"

sin_node = pytest.mark.skipif(shutil.which("node") is None,
                              reason="node no esta instalado")

HOY = "2026-09-14"


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def _anuncio(db, ad_id, estado="ACTIVE", nombre=None, campana="Test creativo",
             imagen=None):
    conn = _connect(db)
    try:
        conn.execute(
            "INSERT INTO meta_ads (ad_id, ad_name, campaign_id, campaign_name, "
            "effective_status, object_type, imagen_archivo) VALUES (?,?,?,?,?,?,?)",
            (ad_id, nombre or f"Pieza {ad_id}", "c1", campana, estado, "SHARE",
             imagen))
        conn.commit()
    finally:
        conn.close()


def _dia(db, ad_id, fecha, spend, leads=0, impresiones=1000, clics=20):
    conn = _connect(db)
    try:
        conn.execute(
            "INSERT INTO meta_ad_insights (date, ad_id, spend, currency, "
            "impressions, clicks, reach, leads) VALUES (?,?,?,?,?,?,?,?)",
            (fecha, ad_id, spend, "USD", impresiones, clics, 0, leads))
        conn.commit()
    finally:
        conn.close()


def _campana_dia(db, fecha, spend):
    conn = _connect(db)
    try:
        conn.execute(
            "INSERT INTO meta_insights (date, campaign_id, campaign_name, spend, "
            "currency, impressions, clicks, reach, leads) VALUES (?,?,?,?,?,?,?,?,?)",
            (fecha, "c1", "Test creativo", spend, "USD", 1000, 20, 0, 0))
        conn.commit()
    finally:
        conn.close()


def _ids(grupo):
    return [p["ad_id"] for p in grupo]


# ── Las piezas de un mes ───────────────────────────────────────────────────

def test_los_limites_del_mes_no_se_calculan_a_mano():
    assert limites_del_mes("2026-02") == ("2026-02-01", "2026-02-28")
    assert limites_del_mes("2028-02") == ("2028-02-01", "2028-02-29")
    assert limites_del_mes("2026-12") == ("2026-12-01", "2026-12-31")


def test_los_numeros_son_solo_del_mes(db):
    """La pieza corrio agosto y setiembre: mirando setiembre no entra agosto.

    Los dias pegados al borde (31/8 y 1/10) son los que delatan un filtro mal
    puesto.
    """
    _anuncio(db, "1")
    _dia(db, "1", "2026-08-31", 500.0, leads=20, impresiones=9000, clics=300)
    _dia(db, "1", "2026-09-01", 30.0, leads=2, impresiones=1000, clics=40)
    _dia(db, "1", "2026-09-20", 20.0, leads=1, impresiones=500, clics=10)
    _dia(db, "1", "2026-10-01", 900.0, leads=50, impresiones=9000, clics=300)
    r = piezas_del_mes(db, "2026-09", hoy=HOY)
    p = r["activas"][0]
    assert (p["gasto"], p["leads"], p["impresiones"], p["clics"]) == (50.0, 3, 1500, 50)
    assert p["cpl"] == round(50.0 / 3, 2)
    assert (p["primer_dia"], p["ultimo_dia"]) == ("2026-09-01", "2026-09-20")
    assert r["totales"]["gasto"] == 50.0 and r["totales"]["leads"] == 3


def test_una_pieza_sin_actividad_en_el_mes_no_aparece(db):
    """Esta prendida hoy, pero en setiembre no corrio: en setiembre no va."""
    _anuncio(db, "agosto", estado="ACTIVE")
    _dia(db, "agosto", "2026-08-15", 100.0, leads=5)
    _anuncio(db, "setiembre", estado="ACTIVE")
    _dia(db, "setiembre", "2026-09-02", 10.0)
    r = piezas_del_mes(db, "2026-09", hoy=HOY)
    assert _ids(r["activas"]) == ["setiembre"]
    assert _ids(piezas_del_mes(db, "2026-08", hoy=HOY)["activas"]) == ["agosto"]


def test_una_fila_en_cero_no_es_actividad(db):
    _anuncio(db, "1")
    _dia(db, "1", "2026-09-05", 0.0, impresiones=0, clics=0)
    assert piezas_del_mes(db, "2026-09", hoy=HOY)["totales"]["piezas"] == 0


def test_con_impresiones_y_sin_gasto_igual_estuvo_al_aire(db):
    _anuncio(db, "gratis", estado="PAUSED")
    _dia(db, "gratis", "2026-09-05", 0.0, impresiones=400, clics=2)
    r = piezas_del_mes(db, "2026-09", hoy=HOY)
    assert _ids(r["inactivas"]) == ["gratis"]
    assert r["inactivas"][0]["cpl"] is None


def test_se_parten_en_activas_hoy_y_ya_no(db):
    for ad_id, estado in (("1", "ACTIVE"), ("2", "PAUSED"),
                          ("3", "ADSET_PAUSED"), ("4", "CAMPAIGN_PAUSED")):
        _anuncio(db, ad_id, estado=estado)
        _dia(db, ad_id, "2026-09-03", 10.0 * int(ad_id), leads=1)
    r = piezas_del_mes(db, "2026-09", hoy=HOY)
    assert _ids(r["activas"]) == ["1"]
    assert _ids(r["inactivas"]) == ["4", "3", "2"], "dentro del grupo, por gasto"
    assert all(p["corriendo"] for p in r["activas"])
    assert not any(p["corriendo"] for p in r["inactivas"])
    assert (r["totales"]["activas"], r["totales"]["inactivas"]) == (1, 3)


def test_el_gasto_de_un_anuncio_sin_ficha_igual_cuenta(db):
    """Si la ficha del anuncio no se pudo traer, su plata no desaparece del mes."""
    _dia(db, "huerfano", "2026-09-03", 40.0)
    r = piezas_del_mes(db, "2026-09", hoy=HOY)
    assert r["totales"]["gasto"] == 40.0
    assert _ids(r["inactivas"]) == ["huerfano"]


def test_las_piezas_no_traen_la_campana(db):
    """Juan no quiere ver la pauta partida por campaña."""
    _anuncio(db, "1", campana="Leads UY")
    _dia(db, "1", "2026-09-03", 10.0)
    p = piezas_del_mes(db, "2026-09", hoy=HOY)["activas"][0]
    assert "Leads UY" not in json.dumps(p)


def test_ningun_numero_de_otro_mes_llega_a_mayo(db):
    """Juan: "si 'tu web te hace ganar clientes' se pauto en mayo y setiembre,
    que cuando entre a mayo me muestre lo que fue su costo por lead en mayo".

    Antes la tarjeta activa traia la recomendacion, armada con toda la vida de
    la pieza: el 999 de junio y el 3333 de setiembre llegaban a mayo por ahi.
    Los dias pegados al borde (30/4, 1/5, 31/5, 1/6) delatan el filtro.
    """
    _anuncio(db, "web", nombre="Tu web te hace ganar clientes")
    _dia(db, "web", "2026-04-30", 777.0, leads=77)
    _dia(db, "web", "2026-05-01", 10.0, leads=1)
    _dia(db, "web", "2026-05-31", 40.0, leads=4)
    _dia(db, "web", "2026-06-01", 999.0, leads=99)
    _dia(db, "web", "2026-09-10", 3333.0, leads=33)
    _anuncio(db, "solo_set")
    _dia(db, "solo_set", "2026-06-01", 55.0, leads=5)
    _dia(db, "solo_set", "2026-09-02", 66.0, leads=6)

    r = piezas_del_mes(db, "2026-05", hoy=HOY)
    assert _ids(r["activas"]) == ["web"], "la de setiembre no va en mayo"
    p = r["activas"][0]
    assert (p["gasto"], p["leads"], p["cpl"]) == (50.0, 5, 10.0)
    assert "recomendacion" not in p
    texto = json.dumps(r)
    for ajeno in ("777", "999", "3333", "55.0", "66.0", "77", "99", "33"):
        assert ajeno not in texto, f"{ajeno} es de otro mes y llego a mayo"
    # Y en setiembre, lo de setiembre.
    s = {x["ad_id"]: x for x in piezas_del_mes(db, "2026-09", hoy=HOY)["activas"]}
    assert (s["web"]["gasto"], s["web"]["cpl"]) == (3333.0, 101.0)


# ── Por que un mes no tiene piezas ─────────────────────────────────────────

def test_un_mes_anterior_al_primer_dia_guardado_no_dice_que_no_se_pauto(db):
    _anuncio(db, "1")
    _dia(db, "1", "2026-06-01", 5.0)
    r = piezas_del_mes(db, "2026-05", hoy=HOY)
    assert r["mes"] == "2026-05" and r["nombre"] == "Mayo 2026"
    assert r["estado_datos"] == "sin_datos_por_pieza"
    assert r["primer_dia"] == "2026-06-01"
    assert r["activas"] == [] and r["inactivas"] == []


def test_un_hueco_con_gasto_por_campana_es_falta_de_datos(db):
    _anuncio(db, "1")
    _dia(db, "1", "2026-05-31", 5.0)
    _dia(db, "1", "2026-08-01", 5.0)
    _campana_dia(db, "2026-07-15", 80.0)
    assert piezas_del_mes(db, "2026-07", hoy=HOY)["estado_datos"] == "sin_datos_por_pieza"
    assert piezas_del_mes(db, "2026-06", hoy=HOY)["estado_datos"] == "sin_pauta"
    assert piezas_del_mes(db, "2026-05", hoy=HOY)["estado_datos"] == "con_piezas"


def test_avisa_si_el_mes_arranca_antes_del_primer_dia_guardado(db):
    _anuncio(db, "1")
    _dia(db, "1", "2026-08-31", 5.0)
    _dia(db, "1", "2026-09-01", 5.0)
    assert piezas_del_mes(db, "2026-08", hoy=HOY)["datos_desde"] == "2026-08-31"
    assert piezas_del_mes(db, "2026-09", hoy=HOY)["datos_desde"] is None


def test_sin_nada_guardado_el_estado_lo_dice(db):
    assert piezas_del_mes(db, "2026-05", hoy=HOY)["estado_datos"] == "nada_sincronizado"


# ── Los leads del CRM, aparte de los de Meta ───────────────────────────────

def _lead(db, scraped_at, ad_id=None, source="meta"):
    conn = _connect(db)
    try:
        conn.execute("INSERT INTO businesses (name, source, scraped_at, meta_ad_id) "
                     "VALUES (?,?,?,?)", ("Lead", source, scraped_at, ad_id))
        conn.commit()
    finally:
        conn.close()


def test_los_leads_del_crm_son_los_del_mes_y_van_aparte(db):
    _anuncio(db, "web")
    _dia(db, "web", "2026-05-10", 30.0, leads=7)
    _anuncio(db, "otra")
    _dia(db, "otra", "2026-04-30", 30.0, leads=1)
    _lead(db, "2026-05-31T23:30:00+0000", "web")          # mayo
    _lead(db, "2026-05-01T00:05:00+0000", "web")          # mayo
    _lead(db, "2026-06-01T00:10:00+0000", "web")          # junio: no
    _lead(db, "2026-04-30T22:00:00+0000", "web")          # abril: no
    _lead(db, "2026-05-15T12:00:00+0000", None)           # sin pieza
    _lead(db, "2026-05-16T12:00:00+0000", "otra")         # pieza sin actividad en mayo
    _lead(db, "2026-05-17T12:00:00+0000", "web", source="scraper")  # no es de Meta
    r = piezas_del_mes(db, "2026-05", hoy=HOY)
    p = r["activas"][0]
    assert p["leads"] == 7, "el de Meta no se toca"
    assert p["leads_crm"] == 2
    assert r["totales"]["leads_crm"] == 4
    assert r["totales"]["leads_crm_sin_pieza"] == 2
    assert r["totales"]["leads"] == 7, "no se suman"


def test_si_ningun_lead_del_mes_trae_la_pieza_no_se_inventa_un_cero(db):
    _anuncio(db, "web")
    _dia(db, "web", "2026-05-10", 30.0, leads=7)
    _lead(db, "2026-05-15T12:00:00+0000", None)
    r = piezas_del_mes(db, "2026-05", hoy=HOY)
    assert r["activas"][0]["leads_crm"] is None
    assert r["totales"]["leads_crm"] == 1


def test_avisa_con_que_comparar_el_gasto_del_mes(db):
    _anuncio(db, "1")
    _dia(db, "1", "2026-09-03", 40.0)
    _campana_dia(db, "2026-09-03", 55.0)
    _campana_dia(db, "2026-08-03", 999.0)          # otro mes: no entra
    r = piezas_del_mes(db, "2026-09", hoy=HOY)
    assert r["gasto_pauta"] == 55.0


def test_sin_nada_sincronizado_contesta_vacio_y_no_rompe(db):
    r = piezas_del_mes(db, "2026-09", hoy=HOY)
    assert r["activas"] == [] and r["inactivas"] == []
    assert r["primer_mes"] is None and r["gasto_pauta"] is None
    assert r["totales"]["cpl"] is None
    assert (r["nombre"], r["mes_actual"]) == ("Setiembre 2026", "2026-09")


def test_dice_desde_que_mes_hay_piezas(db):
    _anuncio(db, "1")
    _dia(db, "1", "2026-04-10", 5.0)
    _dia(db, "1", "2026-09-10", 5.0)
    assert piezas_del_mes(db, "2026-09", hoy=HOY)["primer_mes"] == "2026-04"


# ── La ruta ────────────────────────────────────────────────────────────────

@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "token-de-prueba")
    ruta = str(tmp_path / "rutas.db")
    init_db(ruta)
    app = create_app(ruta)
    app.config["TESTING"] = True
    return app, app.test_client()


_AUTH = {"x-admin-token": "token-de-prueba"}


def test_la_ruta_de_piezas_contesta_el_mes_pedido(cliente):
    app, c = cliente
    _anuncio(app.config["DB_PATH"], "1")
    _dia(app.config["DB_PATH"], "1", "2026-06-10", 12.5, leads=1)
    r = c.get("/api/marketing/piezas?mes=2026-06", headers=_AUTH)
    assert r.status_code == 200
    datos = r.get_json()
    assert datos["mes"] == "2026-06"
    assert datos["totales"]["gasto"] == 12.5


def test_la_ruta_sin_mes_usa_el_mes_actual(cliente):
    from datetime import date
    _, c = cliente
    r = c.get("/api/marketing/piezas", headers=_AUTH)
    assert r.status_code == 200
    assert r.get_json()["mes"] == date.today().isoformat()[:7]


@pytest.mark.parametrize("mes", ["2026-13", "2026-9", "setiembre", "2026-09-01"])
def test_un_mes_invalido_da_400(cliente, mes):
    _, c = cliente
    assert c.get(f"/api/marketing/piezas?mes={mes}", headers=_AUTH).status_code == 400


def test_las_piezas_no_se_ven_sin_permiso(cliente):
    _, c = cliente
    assert c.get("/api/marketing/piezas?mes=2026-09").status_code in (302, 401, 403)


def test_la_ruta_de_piezas_no_corre_el_mes_al_primero_con_datos(cliente):
    app, c = cliente
    _anuncio(app.config["DB_PATH"], "1")
    _dia(app.config["DB_PATH"], "1", "2026-08-01", 12.5)
    datos = c.get("/api/marketing/piezas?mes=2026-05", headers=_AUTH).get_json()
    assert datos["mes"] == "2026-05"
    assert datos["estado_datos"] == "sin_datos_por_pieza"


def test_el_relleno_no_corre_sin_permiso(cliente):
    _, c = cliente
    r = c.post("/api/marketing/rellenar-anuncios?mes=2026-05")
    assert r.status_code in (302, 401, 403)


@pytest.mark.parametrize("mes", ["", "2026-5", "mayo", "2026-05-01"])
def test_el_relleno_con_mes_invalido_da_400(cliente, mes):
    _, c = cliente
    r = c.post(f"/api/marketing/rellenar-anuncios?mes={mes}", headers=_AUTH)
    assert r.status_code == 400


def test_el_relleno_de_un_mes_futuro_da_400(cliente):
    _, c = cliente
    r = c.post("/api/marketing/rellenar-anuncios?mes=2999-01", headers=_AUTH)
    assert r.status_code == 400


def test_el_relleno_sin_credenciales_avisa_y_no_llama(cliente, monkeypatch):
    monkeypatch.delenv("META_ADS_TOKEN", raising=False)
    monkeypatch.delenv("META_AD_ACCOUNT_ID", raising=False)
    _, c = cliente
    r = c.post("/api/marketing/rellenar-anuncios?mes=2026-05", headers=_AUTH)
    assert r.status_code == 200
    assert r.get_json()["salteado"] == "sin_credenciales"


def test_el_relleno_pedido_dos_veces_seguidas_da_429(cliente, monkeypatch):
    import services.meta_anuncios as m

    monkeypatch.setattr(m, "hay_credenciales", lambda: True)
    monkeypatch.setattr(m, "_traer_insights", lambda *a, **k: [])
    _, c = cliente
    assert c.post("/api/marketing/rellenar-anuncios?mes=2026-05",
                  headers=_AUTH).status_code == 200
    assert c.post("/api/marketing/rellenar-anuncios?mes=2026-06",
                  headers=_AUTH).status_code == 429


# ── Las semanas vacias del medio ───────────────────────────────────────────

def _gasto_campana(db, fecha, spend):
    _campana_dia(db, fecha, spend)


def test_las_semanas_vacias_del_medio_aparecen(db):
    """EL BUG DEL GRAFICO: un mes sin pauta desaparecia de "Semana a semana".

    La barra de despues quedaba pegada a la de antes y se llamaba "Semana 4"
    siendo la septima. El mes a mes si mostraba el hueco; el semanal no.
    """
    _gasto_campana(db, "2026-06-29", 10.0)
    _gasto_campana(db, "2026-07-27", 20.0)
    inicios = [s["inicio"] for s in serie_semanal(db, "2026-06-01", "2026-08-31")]
    assert inicios == ["2026-06-29", "2026-07-06", "2026-07-13", "2026-07-20",
                       "2026-07-27"]


def test_una_semana_rellenada_no_cuenta_como_semana_con_actividad(db):
    """El historico divide por semanas con actividad: el periodo tiene que poder
    hacer lo mismo, o se comparan promedios con denominadores distintos."""
    _gasto_campana(db, "2026-06-29", 10.0)
    _gasto_campana(db, "2026-07-20", 20.0)
    marcas = [s["con_actividad"] for s in serie_semanal(db, "2026-06-01", "2026-08-31")]
    assert marcas == [True, False, False, True]


def test_el_acumulado_arranca_en_el_periodo_y_nunca_baja(db):
    """Lo de antes del periodo no se suma: la ventana no contiene todos los datos."""
    _gasto_campana(db, "2026-05-04", 999.0)            # antes de la ventana
    _gasto_campana(db, "2026-06-01", 10.0)
    _gasto_campana(db, "2026-06-15", 30.0)
    s = serie_semanal(db, "2026-06-01", "2026-06-30")
    acum = [x["gasto_acum"] for x in s]
    assert acum == [10.0, 10.0, 40.0]
    assert acum == sorted(acum)


# ── Los hallazgos de la pauta entera ───────────────────────────────────────

def _m(mid, valor):
    return {"id": mid, "etiqueta": mid, "valor": valor, "formato": "numero"}


def _bloque(nombre, slug, **vals):
    return {"campana": nombre,
            "metricas": [_m(f"campana.{slug}.{k}", v) for k, v in vals.items()]}


def _dossier(campanas):
    return {"campanas": campanas, "embudo_campanas": [], "conciliacion": []}


def test_los_hallazgos_de_la_pauta_no_nombran_campanas():
    d = _dossier([
        _bloque("Test creativo", "test", gasto=500.0, leads_crm=40, demos=3, cierres=0),
        _bloque("Leads UY", "uy", gasto=400.0, leads_crm=30, demos=2, cierres=0),
        _bloque("todas", "todas", gasto=900.0, leads_crm=70, demos=5, cierres=0,
                interesados=60, agendadas=50, presupuestos=4),
    ])
    hs = buscar(d, por_campana=False)
    assert "pauta_sin_cierres" in [h["tipo"] for h in hs]
    texto = json.dumps(hs, ensure_ascii=False)
    assert "Test creativo" not in texto and "Leads UY" not in texto
    # Y los de siempre siguen existiendo para el informe.
    assert "sin_cierres" in [h["tipo"] for h in buscar(d)]


def test_el_escalon_de_la_pauta_sale_del_total():
    d = _dossier([_bloque("todas", "todas", gasto=900.0, leads_crm=100,
                          interesados=90, agendadas=85, demos=80,
                          presupuestos=20, cierres=18)])
    hs = [h for h in buscar(d, por_campana=False) if h["tipo"] == "pauta_escalon"]
    assert hs and "Presupuestos enviados" in hs[0]["titulo"]
    assert hs[0]["metricas_citadas"] == ["campana.todas.presupuestos"]


# ── Los graficos ───────────────────────────────────────────────────────────

def _node(js, tmp_path):
    archivo = tmp_path / "correr.js"
    archivo.write_text("globalThis.window = globalThis;\n"
                       + CHARTS.read_text(encoding="utf-8")
                       + "\nconst SC = globalThis.SC;\n" + js + "\n",
                       encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True,
                       encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout


@sin_node
def test_las_lineas_no_ordenan_las_semanas_como_texto(tmp_path):
    """EL BUG DEL GRAFICO: `equis.sort()` sobre "Semana N" ponia la 10 y la 11
    entre la 1 y la 2, y un acumulado —que nunca baja— se dibujaba bajando."""
    salida = _node("""
var puntos = [];
for (var i = 1; i <= 12; i++) {
  puntos.push({x: 'Semana ' + i, y: i * 10,
               orden: '2026-06-' + String(i + 10).padStart(2, '0')});
}
var h = SC.serieMulti([{campana: 'Pauta', puntos: puntos}],
                      {etiqueta: 't', formato: 'numero'}, 'oscuro');
var xs = [...h.matchAll(/<title>Pauta · (Semana \\d+)/g)].map(m => m[1]);
console.log(JSON.stringify(xs));
""", tmp_path)
    xs = json.loads(salida)
    assert xs == [f"Semana {i}" for i in range(1, 13)]


@sin_node
def test_sin_orden_las_lineas_respetan_el_orden_en_que_vienen(tmp_path):
    salida = _node("""
var p = ['Semana 1','Semana 2','Semana 10','Semana 11'].map((x, i) => ({x: x, y: i}));
var h = SC.serieMulti([{campana: 'Pauta', puntos: p}], {etiqueta: 't'}, 'oscuro');
console.log(JSON.stringify([...h.matchAll(/<title>Pauta · (Semana \\d+)/g)].map(m => m[1])));
""", tmp_path)
    assert json.loads(salida) == ["Semana 1", "Semana 2", "Semana 10", "Semana 11"]


@sin_node
def test_el_rotulo_del_historico_se_lee_encima_de_una_barra(tmp_path):
    salida = _node("""
var h = SC.barrasAgrupadas([{clave:'a', etiqueta:'A'}],
  [{etiqueta:'Gasto', color:'#0088CC', valores:{a: 100}}],
  {etiqueta:'t', formato:'moneda', referencia:{valor: 90, etiqueta:'histórico'}}, 'oscuro');
console.log(JSON.stringify(h));
""", tmp_path)
    svg = json.loads(salida)
    rotulo = re.search(r"<text[^>]*>histórico", svg).group(0)
    assert 'paint-order="stroke"' in rotulo


# ── El panel ───────────────────────────────────────────────────────────────

_PANEL = dashboard.DASHBOARD_HTML.split('id="marketing-panel"')[1].split(
    'id="sdr-panel"')[0]


def test_no_hay_selector_de_campana():
    assert 'id="mk-campana"' not in dashboard.DASHBOARD_HTML
    assert "mk-campana" not in dashboard.DASHBOARD_HTML


@pytest.mark.parametrize("bloque", ["mk-embudos", "mk-evolucion", "mk-ranking",
                                    "mk-campanas", "mk-dispersion"])
def test_se_fueron_los_bloques_que_parten_la_pauta_por_campana(bloque):
    assert f'id="{bloque}"' not in dashboard.DASHBOARD_HTML


def test_los_titulos_del_panel_no_hablan_de_campanas():
    titulos = re.findall(r"<h3>(.*?)</h3>", _PANEL)
    assert titulos
    assert not [t for t in titulos if "campaña" in t.lower()], titulos


def test_la_seccion_de_piezas_tiene_su_navegador_de_mes():
    assert 'id="mk-piezas"' in _PANEL
    assert 'id="mk-piezas-mes"' in _PANEL
    assert "mkPiezasMes(-1)" in _PANEL and "mkPiezasMes(1)" in _PANEL
    assert "/api/marketing/piezas" in dashboard.DASHBOARD_HTML


def test_las_que_ya_no_estan_activas_no_van_en_gris():
    """Juan: "que no me aparezcan en gris sin que se vea". Ninguna regla de las
    tarjetas baja la opacidad ni pasa la foto a gris."""
    reglas = re.findall(r"^(\.sc-anun[^{]*)\{([^}]*)\}", dashboard.DASHBOARD_HTML, re.M)
    assert reglas
    for selector, cuerpo in reglas:
        assert "grayscale" not in cuerpo and "opacity" not in cuerpo, selector
        assert 'data-corriendo="false"' not in selector, selector


def test_mk_mes_corrido_cruza_el_ano(tmp_path):
    if shutil.which("node") is None:
        pytest.skip("node no esta instalado")
    m = re.search(r"^function _mkMesCorrido\(.*?^\}", dashboard.DASHBOARD_HTML,
                  re.M | re.S)
    assert m, "falta _mkMesCorrido"
    archivo = tmp_path / "mes.js"
    archivo.write_text(m.group(0) + "\nconsole.log(JSON.stringify(["
                       "_mkMesCorrido('2026-01', -1), _mkMesCorrido('2026-12', 1),"
                       "_mkMesCorrido('2026-09', -8), _mkMesCorrido('2026-09', 0)]));\n",
                       encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True,
                       encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == ["2025-12", "2027-01", "2026-01", "2026-09"]


# ── Las reuniones por pieza ────────────────────────────────────────────────
#
# Es lo que ordena distinto que el costo por lead. Contra los datos reales, dos
# piezas que traian leads a 14,91 y a 19,00 daban reuniones a 49,68 y a 126,69:
# casi el mismo costo por lead y cinco veces de diferencia en lo que importa.
# Mirando el Administrador de anuncios esa diferencia no se ve.

def _lead_con_demo(db, scraped_at, ad_id, estado="demo_1"):
    conn = _connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO businesses (name, source, scraped_at, meta_ad_id) "
            "VALUES (?,?,?,?)", ("Lead", "meta", scraped_at, ad_id))
        conn.execute("INSERT INTO lead_events (lead_id, new_status) VALUES (?,?)",
                     (cur.lastrowid, estado))
        conn.commit()
    finally:
        conn.close()


def _de(mes, ad_id):
    todas = mes["activas"] + mes["inactivas"]
    iguales = [p for p in todas if p["ad_id"] == ad_id]
    assert iguales, f"no esta {ad_id}: {[p['ad_id'] for p in todas]}"
    return iguales[0]


def test_cuenta_las_reuniones_que_trajo_cada_pieza(db):
    _anuncio(db, "web")
    _dia(db, "web", "2026-05-10", 100.0, leads=5)
    _lead_con_demo(db, "2026-05-11 10:00:00", "web")
    _lead_con_demo(db, "2026-05-12 10:00:00", "web")
    _lead(db, "2026-05-13 10:00:00", ad_id="web")
    p = _de(piezas_del_mes(db, "2026-05"), "web")
    assert p["leads_crm"] == 3
    assert p["demos"] == 2
    assert p["costo_demo"] == 50.0


def test_un_lead_que_paso_de_largo_la_demo_igual_cuenta(db):
    """Quien hoy figura en `presupuesto_enviado` paso por la reunion. Contar
    solo el estado de hoy perderia a los que avanzaron."""
    _anuncio(db, "web")
    _dia(db, "web", "2026-05-10", 60.0, leads=2)
    _lead_con_demo(db, "2026-05-11 10:00:00", "web", estado="presupuesto_enviado")
    _lead_con_demo(db, "2026-05-12 10:00:00", "web", estado="cerrado")
    assert _de(piezas_del_mes(db, "2026-05"), "web")["demos"] == 2


def test_la_reunion_posterior_al_mes_se_le_acredita_igual(db):
    """Una reunion de un lead de mayo puede hacerse en junio. Se le acredita a
    la pieza que lo trajo, que es de quien habla la tarjeta."""
    _anuncio(db, "web")
    _dia(db, "web", "2026-05-10", 40.0, leads=1)
    _lead_con_demo(db, "2026-05-28 10:00:00", "web")
    assert _de(piezas_del_mes(db, "2026-05"), "web")["demos"] == 1


def test_un_lead_de_otro_mes_no_le_suma_reuniones(db):
    """Todo lo de la tarjeta es del mes: el lead que ENTRO en el mes."""
    _anuncio(db, "web")
    _dia(db, "web", "2026-05-10", 40.0, leads=1)
    _dia(db, "web", "2026-06-10", 40.0, leads=1)
    # Uno en mayo sin reunion —para que mayo tenga la pieza conocida y el cero
    # signifique "no trajo" y no "falta el dato"— y uno en junio con reunion.
    _lead(db, "2026-05-11 10:00:00", ad_id="web")
    _lead_con_demo(db, "2026-06-11 10:00:00", "web")
    assert _de(piezas_del_mes(db, "2026-05"), "web")["demos"] == 0
    assert _de(piezas_del_mes(db, "2026-06"), "web")["demos"] == 1


def test_sin_reuniones_el_costo_por_reunion_no_es_cero(db):
    _anuncio(db, "web")
    _dia(db, "web", "2026-05-10", 90.0, leads=3)
    _lead(db, "2026-05-11 10:00:00", ad_id="web")
    p = _de(piezas_del_mes(db, "2026-05"), "web")
    assert p["demos"] == 0
    assert p["costo_demo"] is None


def test_sin_ninguna_pieza_conocida_las_reuniones_son_none_y_no_cero(db):
    """Mismo criterio que `leads_crm`: un cero diria "no trajo a nadie" cuando
    lo que falta es el dato. Los leads viejos no tienen la pieza guardada."""
    _anuncio(db, "web")
    _dia(db, "web", "2026-05-10", 40.0, leads=2)
    _lead(db, "2026-05-11 10:00:00", ad_id=None)
    p = _de(piezas_del_mes(db, "2026-05"), "web")
    assert p["demos"] is None
    assert p["costo_demo"] is None
