"""Modo sombra: recomendaciones de pauta que no tocan Meta y su evaluacion."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services import sombra_meta as sm

LUNES_9 = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
LUNES_SIG = datetime(2026, 9, 28, 12, 30, tzinfo=timezone.utc)
MARTES = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def _f(gasto, imp, clics, leads, frec=1.5, **extra):
    d = {"spend": str(gasto), "impressions": str(imp), "inline_link_clicks": str(clics),
         "frequency": str(frec), "account_currency": "USD",
         "actions": [{"action_type": "lead", "value": str(leads)}]}
    d.update(extra)
    return d


REF = _f(612.0, 80000, 520, 40, frec=2.0)   # CPL 15,30


def _datos(anuncios_7d=(), campanas_7d=(), anuncios=None, campanas=None, conjuntos=()):
    return {
        "referencia": REF,
        "anuncios_7d": list(anuncios_7d),
        "campanas_7d": list(campanas_7d),
        "anuncios": anuncios if anuncios is not None else [
            {"id": f["ad_id"], "effective_status": "ACTIVE", "campaign_id": f["campaign_id"],
             "created_time": "2026-08-01T00:00:00-0300"} for f in anuncios_7d],
        "campanas": campanas if campanas is not None else [
            {"id": "C1", "name": "Leads - UY", "objective": "OUTCOME_LEADS", "effective_status": "ACTIVE",
             "daily_budget": "2000"},
            {"id": "C2", "name": "Brand", "objective": "OUTCOME_AWARENESS", "effective_status": "ACTIVE"},
        ],
        "conjuntos": list(conjuntos),
    }


def _ad(ad_id, gasto, leads, camp="C1", frec=1.5, imp=4000, clics=40):
    return _f(gasto, imp, clics, leads, frec=frec, ad_id=ad_id, ad_name=f"Anuncio {ad_id}",
              campaign_id=camp, campaign_name="Leads - UY" if camp == "C1" else "Brand")


def _camp(cid, gasto, leads, imp=20000, clics=200):
    return _f(gasto, imp, clics, leads, campaign_id=cid, campaign_name={"C1": "Leads - UY", "C2": "Brand"}[cid])


def _tipos(recs):
    return {r["clave"]: r for r in recs}


# ── recomendaciones ──────────────────────────────────────────────────────────

def test_pausa_el_anuncio_que_gasta_sin_leads():
    recs = _tipos(sm.recomendar(_datos([_ad("A1", 30, 0), _ad("A2", 10, 0), _ad("A3", 40, 3)])))
    assert "pausar:A1" in recs
    assert "pausar:A2" not in recs      # gasto chico
    assert "pausar:A3" not in recs
    assert "USD 30,00" in recs["pausar:A1"]["evidencia"]


def test_no_pausa_anuncios_de_campanas_que_no_buscan_leads():
    recs = _tipos(sm.recomendar(_datos([_ad("B1", 60, 0, camp="C2")])))
    assert "pausar:B1" not in recs


def test_no_recomienda_sobre_anuncios_ya_pausados():
    datos = _datos([_ad("A1", 30, 0)], anuncios=[{"id": "A1", "effective_status": "PAUSED"}])
    assert sm.recomendar(datos) == []


def test_renovar_por_frecuencia_o_ctr_bajo():
    recs = _tipos(sm.recomendar(_datos([_ad("A1", 15, 1, frec=3.5), _ad("A2", 15, 1, imp=10000, clics=20)])))
    assert "renovar:A1" in recs and "3,5 veces" in recs["renovar:A1"]["evidencia"]
    assert "renovar:A2" in recs


def test_pocos_clics_en_una_campana_de_marca_no_es_problema():
    recs = _tipos(sm.recomendar(_datos([_ad("B1", 15, 0, camp="C2", imp=10000, clics=20),
                                        _ad("B2", 15, 0, camp="C2", frec=3.5)])))
    assert "renovar:B1" not in recs
    assert "renovar:B2" in recs


def test_escalar_la_campana_barata_y_bajar_la_cara():
    recs = _tipos(sm.recomendar(_datos(campanas_7d=[_camp("C1", 40, 5)])))
    assert "escalar:C1" in recs
    assert "USD 20,00 a USD 24,00" in recs["escalar:C1"]["evidencia"]
    recs = _tipos(sm.recomendar(_datos(campanas_7d=[_camp("C1", 100, 3)])))
    assert "bajar:C1" in recs


def test_confirmar_objetivo_de_campanas_sin_leads():
    recs = _tipos(sm.recomendar(_datos(campanas_7d=[_camp("C2", 25, 0)])))
    assert "confirmar:C2" in recs and "reconocimiento de marca" in recs["confirmar:C2"]["evidencia"]
    assert sm.recomendar(_datos(campanas_7d=[_camp("C2", 5, 0)])) == []


def test_presupuesto_de_campana_o_de_conjuntos():
    datos = _datos(campanas=[{"id": "C3", "objective": "OUTCOME_LEADS", "effective_status": "ACTIVE"}],
                   conjuntos=[{"campaign_id": "C3", "effective_status": "ACTIVE", "daily_budget": "500"},
                              {"campaign_id": "C3", "effective_status": "ACTIVE", "daily_budget": "700"},
                              {"campaign_id": "C3", "effective_status": "PAUSED", "daily_budget": "900"}])
    assert sm.presupuesto_diario(datos, "C3") == 12.0
    assert sm.presupuesto_diario(_datos(), "C1") == 20.0
    assert sm.presupuesto_diario(_datos(), "C2") is None


# ── evaluacion ───────────────────────────────────────────────────────────────

def _rec(tipo, objeto, antes, campana="C1"):
    return {"tipo": tipo, "objeto_id": objeto, "campana_id": campana, "creada_en": "2026-09-21 09:00",
            "antes": {"cpl_ref": 15.3, **antes}}


def test_evaluar_pausar():
    rec = _rec("pausar", "A1", {"gasto": 30, "leads": 0})
    pausado = _datos(anuncios=[{"id": "A1", "effective_status": "PAUSED"}])
    assert sm.evaluar(rec, pausado)[:2] == (True, "coincidencia")
    sigue_mal = _datos([_ad("A1", 25, 0)])
    assert sm.evaluar(rec, sigue_mal)[:2] == (False, "agente")
    le_fue_bien = _datos([_ad("A1", 30, 3)])
    assert sm.evaluar(rec, le_fue_bien)[:2] == (False, "marketing")
    poco = _datos([_ad("A1", 30, 1)])
    assert sm.evaluar(rec, poco)[:2] == (False, "sin_definir")


def test_evaluar_escalar():
    rec = _rec("escalar", "C1", {"presupuesto": 20.0})
    subio = _datos(campanas=[{"id": "C1", "objective": "OUTCOME_LEADS", "effective_status": "ACTIVE",
                              "daily_budget": "2400"}])
    assert sm.evaluar(rec, subio)[:2] == (True, "coincidencia")
    barata = _datos(campanas_7d=[_camp("C1", 40, 5)])
    assert sm.evaluar(rec, barata)[:2] == (False, "agente")
    cara = _datos(campanas_7d=[_camp("C1", 80, 4)])
    assert sm.evaluar(rec, cara)[:2] == (False, "marketing")


def test_evaluar_bajar():
    rec = _rec("bajar", "C1", {"presupuesto": 20.0})
    bajo = _datos(campanas=[{"id": "C1", "objective": "OUTCOME_LEADS", "effective_status": "ACTIVE",
                             "daily_budget": "1500"}])
    assert sm.evaluar(rec, bajo)[:2] == (True, "coincidencia")
    assert sm.evaluar(rec, _datos(campanas_7d=[_camp("C1", 100, 2)]))[:2] == (False, "agente")
    assert sm.evaluar(rec, _datos(campanas_7d=[_camp("C1", 40, 4)]))[:2] == (False, "marketing")


def test_evaluar_renovar_y_confirmar():
    rec = _rec("renovar", "A1", {})
    nuevo = _datos(anuncios=[{"id": "A1", "effective_status": "ACTIVE", "campaign_id": "C1"},
                             {"id": "A9", "effective_status": "ACTIVE", "campaign_id": "C1",
                              "created_time": "2026-09-24T10:00:00-0300"}])
    assert sm.evaluar(rec, nuevo)[:2] == (True, "coincidencia")
    igual = _datos(anuncios=[{"id": "A1", "effective_status": "ACTIVE", "campaign_id": "C1",
                              "created_time": "2026-08-01T00:00:00-0300"}])
    assert sm.evaluar(rec, igual)[:2] == (False, "sin_definir")
    conf = _rec("confirmar", "C2", {}, campana="C2")
    assert sm.evaluar(conf, _datos(campanas_7d=[_camp("C2", 30, 0)]))[:2] == (False, "sin_definir")
    assert sm.evaluar(conf, _datos(campanas_7d=[_camp("C2", 3, 0)]))[:2] == (True, "coincidencia")


# ── corrida ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    sm.fijar_tope_cpl(ruta, None, "test")   # estas pruebas comparan contra el promedio
    return ruta


def _traer(datos):
    return lambda hoy: datos


def test_solo_los_lunes_desde_las_9_y_una_vez(db):
    datos = _datos([_ad("A1", 30, 0)])
    assert sm.corrida(db, MARTES, _traer(datos))["estado"] == "no_es_hora"
    assert sm.corrida(db, LUNES_9.replace(hour=11), _traer(datos))["estado"] == "no_es_hora"
    r = sm.corrida(db, LUNES_9, _traer(datos))
    assert r["estado"] == "ok" and r["recomendaciones"] == 1
    assert sm.corrida(db, LUNES_9, _traer(datos))["estado"] == "ya_corrio"
    [rec] = sm.semana_de(db, "2026-09-21")
    assert rec["tipo_texto"] == "Pausar anuncio" and rec["veredicto"] is None


def test_el_lunes_siguiente_evalua_la_semana_anterior(db):
    sm.corrida(db, LUNES_9, _traer(_datos([_ad("A1", 30, 0)])))
    c = sqlite3.connect(db)
    c.execute("UPDATE corridas SET ultima = datetime('now', '-7 days') WHERE nombre = 'sombra_meta'")
    c.commit()
    c.close()
    r = sm.corrida(db, LUNES_SIG, _traer(_datos([_ad("A1", 25, 0)])))
    assert r["evaluadas"] == 1
    [vieja] = sm.semana_de(db, "2026-09-21")
    assert vieja["veredicto"] == "agente" and vieja["seguida"] == 0
    assert sm.marcador(db) == {"agente": 1}
    assert len(sm.semana_de(db, "2026-09-28")) == 1


def test_calcular_ahora_no_evalua_la_semana_en_curso(db):
    sm.corrida(db, MARTES, _traer(_datos([_ad("A1", 30, 0)])), forzar=True)
    r = sm.corrida(db, MARTES, _traer(_datos([_ad("A1", 30, 0)])), forzar=True)
    assert r["evaluadas"] == 0 and r["nuevas"] == 0


def test_errores_y_credenciales(db, monkeypatch):
    def falla(hoy):
        raise RuntimeError("code=190")
    assert sm.corrida(db, LUNES_9, falla)["estado"] == "error"
    # un error no quema la semana
    assert sm.corrida(db, LUNES_9, _traer(_datos()))["estado"] == "ok"
    monkeypatch.delenv("META_ADS_TOKEN", raising=False)
    assert sm.corrida(db, MARTES, forzar=True)["estado"] == "sin_credenciales"


def test_no_escribe_en_meta():
    fuente = open(sm.__file__, encoding="utf-8").read()
    assert "requests.post" not in fuente and "_post(" not in fuente


def test_apagado(monkeypatch):
    monkeypatch.setenv("SOMBRA_META", "off")
    monkeypatch.setattr(sm.threading, "Thread", lambda *a, **k: pytest.fail("arranco"))
    sm.start_sombra_meta(object())


# ── panel ────────────────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    ruta = str(tmp_path / "app.db")
    init_db(ruta)
    sm.fijar_tope_cpl(ruta, None, "test")
    a = dashboard.create_app(ruta)
    a.config["TESTING"] = True
    return a


def _cli(app, email, paneles=None):
    db = app.config["DB_PATH"]
    uid = create_user(db, name="Juan", email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    if paneles is not None:
        c = sqlite3.connect(db)
        rol = c.execute("INSERT INTO roles (name, panel_access) VALUES (?, ?)",
                        (f"rol-{email}", json.dumps(paneles))).lastrowid
        c.execute("UPDATE users SET role_id = ? WHERE id = ?", (rol, uid))
        c.commit()
        c.close()
    cli = app.test_client()
    with cli.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Juan"
    return cli


def test_marketing_ve_las_recomendaciones_pero_no_la_evaluacion(app):
    db = app.config["DB_PATH"]
    sm.corrida(db, LUNES_9, _traer(_datos([_ad("A1", 30, 0)])))
    c = sqlite3.connect(db)
    c.execute("UPDATE sombra_recomendaciones SET veredicto = 'marketing', detalle = 'x', seguida = 0")
    c.commit()
    c.close()
    jefe = _cli(app, "jefe@scalerics.com")
    mkt = _cli(app, "mkt@scalerics.com", ["sombra"])
    otro = _cli(app, "otro@scalerics.com", ["cal"])
    assert otro.get("/api/sombra/semana").status_code == 403
    assert mkt.post("/api/sombra/calcular").status_code == 403
    d = mkt.get("/api/sombra/semana?semana=2026-09-23").get_json()
    assert d["semana"] == "2026-09-21" and not d["es_admin"] and d["marcador"] == {}
    [r] = d["recomendaciones"]
    assert r["evidencia"] and "veredicto" not in r and "detalle" not in r and "seguida" not in r
    d = jefe.get("/api/sombra/semana?semana=2026-09-23").get_json()
    assert d["es_admin"] and d["marcador"] == {"marketing": 1}
    assert d["recomendaciones"][0]["veredicto"] == "marketing"


def test_lo_recibe_quien_ve_marketing():
    fuente = open("database.py", encoding="utf-8").read()
    assert '_grant_panel_to_existing_roles(conn, "sombra", si_tiene=("marketing",))' in fuente


def test_calcular_desde_el_panel(app, monkeypatch):
    jefe = _cli(app, "jefe@scalerics.com")
    monkeypatch.setattr(sm, "traer_datos", lambda hoy: _datos([_ad("A1", 30, 0)]))
    monkeypatch.setenv("META_ADS_TOKEN", "t")
    monkeypatch.setenv("META_AD_ACCOUNT_ID", "act_1")
    r = jefe.post("/api/sombra/calcular")
    assert r.status_code == 200 and r.get_json()["recomendaciones"] == 1
    assert jefe.post("/api/sombra/calcular").status_code == 429
    d = jefe.get("/api/sombra/semana").get_json()
    assert d["recomendaciones"][0]["objeto_nombre"] == "Anuncio A1"


def test_esta_en_el_menu_sin_barras_invertidas():
    src = open(dashboard.__file__, encoding="utf-8").read()
    js = src[src.index("// ========== Modo sombra =========="):src.index("// ========== FIN Modo sombra ==========")]
    assert "\\" not in js
    html = dashboard.DASHBOARD_HTML
    assert html.count('id="sombra-panel"') == 1
    assert "if (name === 'sombra') soCargar();" in html


# ── formulario de leads ──────────────────────────────────────────────────────

def _leads(db, desde_dias, cantidad, avanzan, hoy=datetime(2026, 9, 21)):
    c = sqlite3.connect(db)
    for i in range(cantidad):
        dia = (hoy - timedelta(days=desde_dias + i % 20)).strftime("%Y-%m-%d 10:00:00")
        c.execute("INSERT INTO businesses (name, phone, source, form_data, crm_status, scraped_at) "
                  "VALUES (?,?,?,?,?,?)",
                  (f"L{desde_dias}-{i}", f"09{desde_dias:02d}{i:05d}", "meta", "{}",
                   "interesado" if i < avanzan else "no_interesa", dia))
    c.commit()
    c.close()


def test_recomienda_revisar_el_formulario_si_caen_los_leads_y_la_calidad_se_sostiene(db):
    _leads(db, 30, 40, 20)   # 28 dias anteriores
    _leads(db, 3, 20, 12)    # ultimos 28 dias
    [rec] = sm.recomendar_formulario(db, LUNES_9.date())
    assert rec["tipo"] == "formulario" and rec["antes"]["leads"] == 20 and rec["antes"]["leads_antes"] == 40
    assert "No se sugiere cambiar preguntas" in rec["evidencia"]


def test_no_recomienda_si_no_cayeron_o_no_hay_datos_o_la_calidad_cayo(db):
    assert sm.recomendar_formulario(db, LUNES_9.date()) == []
    _leads(db, 30, 40, 20)
    _leads(db, 3, 35, 20)
    assert sm.recomendar_formulario(db, LUNES_9.date()) == []       # casi no cayeron


def test_no_recomienda_si_los_que_llegan_no_avanzan(db):
    _leads(db, 30, 40, 20)
    _leads(db, 3, 15, 2)
    assert sm.recomendar_formulario(db, LUNES_9.date()) == []


def test_la_recomendacion_del_formulario_no_se_evalua_contra_meta(db):
    _leads(db, 30, 40, 20)
    _leads(db, 3, 20, 12)
    r = sm.corrida(db, LUNES_9, _traer(_datos()))
    assert r["estado"] == "ok" and r["recomendaciones"] == 1
    [rec] = sm.semana_de(db, "2026-09-21")
    assert rec["tipo_texto"] == "Revisar el formulario"
    seguida, veredicto, _ = sm.evaluar(rec, _datos())
    assert seguida is None and veredicto == "sin_definir"


# ── tope de costo por lead ───────────────────────────────────────────────────

def test_con_tope_los_anuncios_se_comparan_contra_el_tope_y_no_contra_el_promedio():
    caro = _ad("A1", 30, 1)        # CPL 30: mas del doble del promedio (15,30)
    assert "pausar:A1" not in _tipos(sm.recomendar(_datos([caro])))
    camp = _camp("C1", 100, 5)     # CPL 20
    assert "bajar:C1" not in _tipos(sm.recomendar(_datos(campanas_7d=[camp])))
    recs = _tipos(sm.recomendar(_datos(campanas_7d=[camp]), cpl_tope=10.0))
    assert "bajar:C1" in recs and "tope" in recs["bajar:C1"]["evidencia"]
    assert recs["bajar:C1"]["antes"]["cpl_ref"] == 10.0


def test_marketing_y_admin_cambian_el_tope_y_se_usa_en_la_corrida(app):
    db = app.config["DB_PATH"]
    mkt = _cli(app, "mkt@scalerics.com", ["sombra"])
    otro = _cli(app, "otro@scalerics.com", ["cal"])
    assert otro.post("/api/sombra/tope", json={"valor": 10}).status_code == 403
    assert mkt.post("/api/sombra/tope", json={"valor": "abc"}).status_code == 400
    assert mkt.post("/api/sombra/tope", json={"valor": 0}).status_code == 400
    assert mkt.post("/api/sombra/tope", json={"valor": "12,5"}).get_json()["tope_cpl"] == 12.5
    assert mkt.get("/api/sombra/semana").get_json()["tope_cpl"] == 12.5
    assert sm.tope_cpl(db) == 12.5
    sm.corrida(db, LUNES_9, _traer(_datos(campanas_7d=[_camp("C1", 100, 5)])))
    [rec] = sm.semana_de(db, "2026-09-21")
    assert rec["clave"] == "bajar:C1" and rec["antes"]["cpl_ref"] == 12.5
    assert mkt.post("/api/sombra/tope", json={"valor": ""}).get_json()["tope_cpl"] is None
    assert sm.tope_cpl(db) is None


def test_el_tope_arranca_en_25_y_vaciarlo_vuelve_al_promedio(tmp_path):
    db = str(tmp_path / "nuevo.db")
    init_db(db)
    assert sm.tope_cpl(db) == 25.0
    sm.fijar_tope_cpl(db, 18.0, "juan")
    assert sm.tope_cpl(db) == 18.0
    sm.fijar_tope_cpl(db, None, "juan")
    assert sm.tope_cpl(db) is None


# ── estrategia mensual (PDF) ─────────────────────────────────────────────────

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def test_marketing_sube_el_pdf_y_el_agente_lo_lee_y_devuelve_el_plan(app, monkeypatch):
    monkeypatch.setenv("IG_BOT_TOKEN", "t" * 40)
    mkt = _cli(app, "mkt@scalerics.com", ["sombra"])
    otro = _cli(app, "otro@scalerics.com", ["cal"])
    subir = lambda c, **kw: c.post("/api/sombra/estrategias", data=kw, content_type="multipart/form-data")
    from io import BytesIO
    assert subir(otro, mes="2026-10", archivo=(BytesIO(PDF), "e.pdf")).status_code == 403
    assert subir(mkt, mes="octubre", archivo=(BytesIO(PDF), "e.pdf")).status_code == 400
    assert subir(mkt, mes="2026-10", archivo=(BytesIO(b"no soy pdf"), "e.pdf")).status_code == 400
    assert subir(mkt, mes="2026-10").status_code == 400
    r = subir(mkt, mes="2026-10", archivo=(BytesIO(PDF), "Estrategia octubre.pdf"))
    assert r.status_code == 200
    est_id = r.get_json()["id"]
    [e] = mkt.get("/api/sombra/estrategias").get_json()["estrategias"]
    assert e["estado"] == "nueva" and e["plan"] is None and e["subido_por"] == "Juan"
    assert mkt.get(f"/api/sombra/estrategias/{est_id}/pdf").data == PDF

    bot = app.test_client()
    assert bot.get("/api/instagram-bot/estrategias/pendientes").status_code == 403
    h = {"x-ig-token": "t" * 40}
    [p] = bot.get("/api/instagram-bot/estrategias/pendientes", headers=h).get_json()["pendientes"]
    assert p["id"] == est_id and p["mes"] == "2026-10"
    assert bot.get(f"/api/instagram-bot/estrategias/{est_id}/pdf", headers=h).data == PDF
    assert bot.post(f"/api/instagram-bot/estrategias/{est_id}/plan", headers=h, json={"plan": {}}).status_code == 400
    plan = {"linea_de_color": "verde oliva con textura", "piezas": [{"titulo": "Uno"}]}
    assert bot.post(f"/api/instagram-bot/estrategias/{est_id}/plan", headers=h, json={"plan": plan}).status_code == 200
    assert bot.get("/api/instagram-bot/estrategias/pendientes", headers=h).get_json()["pendientes"] == []
    [e] = mkt.get("/api/sombra/estrategias").get_json()["estrategias"]
    assert e["estado"] == "leida" and e["plan"]["linea_de_color"].startswith("verde")
