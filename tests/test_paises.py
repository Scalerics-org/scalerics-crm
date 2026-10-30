"""Uruguay, Argentina y Chile en Meta Ads, Inteligencia marketing y Finanzas
(pedido de Juan, 29/10). Ver services/paises.py y database.aplicar_pais."""

import json
import sqlite3
from datetime import date

import pytest

import dashboard
from database import (_connect, con_pais, crear_recurrente, init_db, insert_business,
                      listar_recurrentes, registrar_envio_meta)
from services import paises
from services.finanzas import materializar_recurrentes
from tests.test_finanzas_rutas import _cli, _rol, _usuario


# ── de qué país es cada cosa ────────────────────────────────────────────────

@pytest.mark.parametrize("nombre,esperado", [
    ("AR - Restaurantes", "AR"),
    ("Fidelidad | Argentina | Leads", "AR"),
    ("[ARG] Web", "AR"),
    ("CL - Fidelidad", "CL"),
    ("Leads Chile oct", "CL"),
    ("UY - Web", "UY"),
    ("Campaña de leads web", None),       # sin país: lo decide el resto
    ("Arquitectos", None),                # "ar" adentro de una palabra no cuenta
    ("Clínicas", None),
    ("", None),
    (None, None),
])
def test_el_pais_de_una_campana_sale_del_nombre(nombre, esperado):
    assert paises.de_campana(nombre) == esperado


@pytest.mark.parametrize("telefono,esperado", [
    ("+54 9 11 2345 6789", "AR"),
    ("5491123456789", "AR"),
    ("+56 9 1234 5678", "CL"),
    ("0056912345678", "CL"),
    ("+598 99 123 456", "UY"),
    ("59899123456", "UY"),
    ("099 123 456", None),                # sin prefijo no dice nada
    ("", None),
    (None, None),
])
def test_el_pais_de_un_telefono_sale_del_prefijo(telefono, esperado):
    assert paises.de_telefono(telefono) == esperado


def test_un_lead_toma_el_pais_fijo_despues_la_campana_despues_el_telefono():
    assert paises.de_lead("CL", "AR - Web", "+598 99 123 456") == "CL"
    assert paises.de_lead(None, "AR - Web", "+56 9 1234 5678") == "AR"
    assert paises.de_lead(None, "Web", "+56 9 1234 5678") == "CL"
    assert paises.de_lead(None, None, "099 123 456") == "UY"
    assert paises.de_lead("XX", None, None) == "UY"


# ── la conexión filtrada ────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "p.db")
    init_db(ruta)
    return ruta


def _lead(db, nombre, telefono, campana=None, pais=None):
    bid = insert_business(db, {"name": nombre, "phone": telefono, "source": "meta",
                               "category": "Meta Lead Ad", "scraped_at": "2026-11-03 10:00:00"})
    c = sqlite3.connect(db)
    c.execute("UPDATE businesses SET meta_campaign_name = ?, pais = ? WHERE id = ?", (campana, pais, bid))
    c.commit()
    c.close()
    if campana:
        registrar_envio_meta(db, bid, f"lead-{bid}", "2026-11-03T10:00:00+0000", campaign_name=campana)
    return bid


def _insights(db):
    c = sqlite3.connect(db)
    for campana, gasto in (("Web", 10), ("AR - Web", 20), ("CL - Fidelidad", 40)):
        c.execute("INSERT INTO meta_insights (date, campaign_id, campaign_name, spend) VALUES (?,?,?,?)",
                  ("2026-11-03", campana, campana, gasto))
    c.commit()
    c.close()


def _gasto(db):
    c = _connect(db)
    try:
        return c.execute("SELECT COALESCE(SUM(spend), 0) FROM meta_insights").fetchone()[0]
    finally:
        c.close()


def test_cada_pais_ve_solo_su_pauta_y_sin_pais_se_ve_todo(db):
    _insights(db)
    assert _gasto(db) == 70
    for pais, gasto in (("UY", 10), ("AR", 20), ("CL", 40)):
        with con_pais(pais):
            assert _gasto(db) == gasto, pais
    assert _gasto(db) == 70, "al salir del país la conexión vuelve a ser la de siempre"


def test_cada_pais_ve_solo_sus_leads_y_los_demas_negocios_siguen(db):
    _lead(db, "Uru", "099 111 111")
    _lead(db, "Porteño", "099 222 222", campana="AR - Web")
    _lead(db, "Santiago", "+56 9 1234 5678")
    _lead(db, "Fijo en Chile", "099 333 333", pais="CL")
    otro = insert_business(db, {"name": "Negocio de Google", "phone": "2900 0000", "source": "google"})
    for pais, nombres in (("UY", {"Uru"}), ("AR", {"Porteño"}), ("CL", {"Santiago", "Fijo en Chile"})):
        with con_pais(pais):
            c = _connect(db)
            leads = {f[0] for f in c.execute("SELECT name FROM businesses WHERE source = 'meta'")}
            assert leads == nombres, pais
            assert c.execute("SELECT 1 FROM businesses WHERE id = ?", (otro,)).fetchone()
            envios = c.execute("SELECT COUNT(*) FROM meta_lead_envios").fetchone()[0]
            c.close()
            assert envios == (1 if pais == "AR" else 0), pais


# ── las rutas ───────────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "raiz@scalerics.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    ruta = str(tmp_path / "a.db")
    init_db(ruta)
    a = dashboard.create_app(ruta)
    a.config["_DB"] = ruta
    return a


def _socio(app, pais=None):
    db = app.config["_DB"]
    if "_SOCIO" not in app.config:
        app.config["_SOCIO"] = _usuario(db, "socio@scalerics.com",
                                        _rol(db, "Socio", ["finanzas", "meta", "marketing"]))
    c = _cli(app, app.config["_SOCIO"])
    if pais:
        c.set_cookie(paises.COOKIE, pais)
    return c


def _movimiento(cli, concepto, moneda="USD", tipo_cambio=None):
    hoy = date.today().isoformat()
    r = cli.post("/api/finanzas/movimientos", json={
        "tipo": "egreso", "fecha": hoy, "concepto": concepto, "categoria": "publicidad",
        "monto": 100, "moneda": moneda, "tipo_cambio": tipo_cambio})
    assert r.status_code in (200, 201), r.get_json()


def _conceptos(cli):
    r = cli.get("/api/finanzas/movimientos")
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    filas = d if isinstance(d, list) else (d.get("movimientos") or d.get("items") or [])
    return {m["concepto"] for m in filas}


def test_lo_que_se_carga_en_un_pais_queda_en_ese_pais(app):
    _movimiento(_socio(app), "Hosting")                       # sin cookie: Uruguay
    _movimiento(_socio(app, "AR"), "Pauta Argentina", "ARS", 1000)
    _movimiento(_socio(app, "CL"), "Pauta Chile")
    assert _conceptos(_socio(app, "UY")) == {"Hosting"}
    assert _conceptos(_socio(app, "AR")) == {"Pauta Argentina"}
    assert _conceptos(_socio(app, "CL")) == {"Pauta Chile"}
    c = sqlite3.connect(app.config["_DB"])
    filas = dict(c.execute("SELECT concepto, pais FROM finanzas_movimientos").fetchall())
    c.close()
    assert filas == {"Hosting": "UY", "Pauta Argentina": "AR", "Pauta Chile": "CL"}


def test_la_moneda_local_de_argentina_y_chile_se_acepta(app):
    _movimiento(_socio(app, "AR"), "En pesos argentinos", "ARS", 1000)
    _movimiento(_socio(app, "CL"), "En pesos chilenos", "CLP", 900)
    c = sqlite3.connect(app.config["_DB"])
    usd = dict(c.execute("SELECT concepto, monto_usd FROM finanzas_movimientos").fetchall())
    c.close()
    assert usd == {"En pesos argentinos": 0.1, "En pesos chilenos": 0.11}


def test_cada_fijo_se_materializa_en_su_pais_aunque_se_mire_otro(app):
    db = app.config["_DB"]
    desde = f"{date.today():%Y-%m}"
    crear_recurrente(db, tipo="egreso", concepto="Oficina", categoria="servicios",
                     monto=50, moneda="USD", desde=desde)
    with con_pais("AR", "escribir"):
        crear_recurrente(db, tipo="egreso", concepto="Coworking BA", categoria="servicios",
                         monto=80, moneda="USD", desde=desde)
    assert {r["concepto"]: r["pais"] for r in listar_recurrentes(db)} == {"Oficina": "UY", "Coworking BA": "AR"}
    # El resumen materializa; se pide desde Chile y aun así cada uno va a su país.
    assert _socio(app, "CL").get("/api/finanzas/resumen").status_code == 200
    assert materializar_recurrentes(db) == 0, "el resumen ya los había creado"
    c = sqlite3.connect(db)
    filas = dict(c.execute("SELECT concepto, pais FROM finanzas_movimientos").fetchall())
    c.close()
    assert filas == {"Oficina": "UY", "Coworking BA": "AR"}


def test_las_vistas_de_finanzas_contestan_en_los_tres_paises(app):
    for pais in ("UY", "AR", "CL"):
        cli = _socio(app, pais)
        for ruta in ("/api/finanzas/resumen", "/api/finanzas/por-cobrar", "/api/finanzas/recurrentes",
                     "/api/finanzas/esenciales", f"/api/finanzas/ventas?periodo={date.today():%Y-%m}",
                     "/api/finanzas/balance-datos", "/api/finanzas/meses"):
            assert cli.get(ruta).status_code == 200, (pais, ruta)


def test_el_dossier_de_marketing_es_del_pais_elegido(app):
    _insights(app.config["_DB"])
    gastos = {}
    for pais in ("UY", "AR", "CL"):
        r = _socio(app, pais).get("/api/marketing/dossier?desde=2026-11-01&hasta=2026-11-30")
        assert r.status_code == 200, (pais, r.get_json())
        gastos[pais] = json.dumps(r.get_json())
    assert '"AR - Web"' in gastos["AR"] and '"CL - Fidelidad"' not in gastos["AR"]
    assert '"CL - Fidelidad"' in gastos["CL"] and '"AR - Web"' not in gastos["CL"]
    assert '"AR - Web"' not in gastos["UY"] and '"CL - Fidelidad"' not in gastos["UY"]


def test_meta_ads_manda_el_pais_de_cada_lead_y_el_alta_manual_lo_guarda(app):
    db = app.config["_DB"]
    _lead(db, "Porteño", "099 222 222", campana="AR - Web")
    cli = _socio(app)
    r = cli.post("/api/meta/leads", json={"name": "Desde Chile", "phone": "099 444 444",
                                          "origen": next(iter(__import__("routes.meta", fromlist=["x"]).ORIGENES_MANUALES)),
                                          "fecha": date.today().isoformat(), "pais": "CL"})
    assert r.status_code == 200, r.get_json()
    leads = cli.get("/api/leads?crm_group=meta").get_json()
    leads = leads if isinstance(leads, list) else leads["items"]
    assert {l["name"]: l["pais"] for l in leads} == {"Porteño": "AR", "Desde Chile": "CL"}


def test_el_selector_esta_en_los_tres_paneles():
    html = dashboard.DASHBOARD_HTML
    for panel in ('id="meta-panel"', 'id="marketing-panel"', 'id="finanzas-panel"'):
        i = html.index(panel)
        assert '<div class="pais-sel"></div>' in html[i:i + 1500], panel
    assert html.count('<div class="pais-sel"></div>') == 3
    assert "cuerpo.pais = paisActual;" in html
