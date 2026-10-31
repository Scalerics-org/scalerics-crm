"""El socio de Argentina: calendario y la parte de Argentina, solo mirar
(Juan, 30/10). Ver services/socio_pais.py."""

import json
import sqlite3
from datetime import date

import pytest

import dashboard
from database import con_pais, crear_recurrente, init_db
from services import paises, socio_pais
from tests.test_finanzas_rutas import _cli, _rol, _usuario
from tests.test_paises import _insights, _lead


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "raiz@scalerics.com")
    monkeypatch.setenv("SOCIOS_ARGENTINA", "Ciro@Ejemplo.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    ruta = str(tmp_path / "a.db")
    init_db(ruta)
    a = dashboard.create_app(ruta)
    a.config["_DB"] = ruta
    return a


def _ciro(app, cookie=None):
    db = app.config["_DB"]
    if "_CIRO" not in app.config:
        app.config["_CIRO"] = _usuario(db, "ciro@ejemplo.com", None)
        c = sqlite3.connect(db)
        from services.socio_pais import asignar_socios
        asignar_socios(c, {"SOCIOS_ARGENTINA": "ciro@ejemplo.com"})
        c.close()
    cli = _cli(app, app.config["_CIRO"])
    if cookie:
        cli.set_cookie(paises.COOKIE, cookie)
    return cli


def _movimientos_de_los_tres(db):
    hoy = date.today()
    for pais, concepto in (("UY", "Sueldo Uruguay"), ("AR", "Pauta Argentina"), ("CL", "Pauta Chile")):
        with con_pais(pais, "escribir"):
            crear_recurrente(db, tipo="egreso", concepto=concepto, categoria="publicidad",
                             monto=10, moneda="USD", desde=f"{hoy:%Y-%m}")


def test_el_mail_del_socio_entra_con_el_rol_y_el_rol_es_solo_lectura(app):
    db = app.config["_DB"]
    _ciro(app)
    c = sqlite3.connect(db)
    fila = c.execute("SELECT r.name, r.paneles_solo_lectura FROM users u JOIN roles r ON r.id = u.role_id "
                     "WHERE u.email = 'ciro@ejemplo.com'").fetchone()
    c.close()
    assert fila == ("Socio Argentina", '["finanzas"]')
    assert socio_pais.pais_fijo(db, app.config["_CIRO"]) == "AR"


def test_me_le_dice_al_menu_su_pais_y_sus_cuatro_paneles(app):
    d = _ciro(app).get("/api/me").get_json()
    assert d["pais_fijo"] == "AR"
    assert json.loads(d["panel_access"]) == ["cal", "meta", "marketing", "finanzas"]
    assert d["is_admin"] is False
    assert "finanzas" in d["paneles_solo_lectura"]


def test_finanzas_le_muestra_solo_argentina_aunque_la_cookie_diga_otra_cosa(app):
    _movimientos_de_los_tres(app.config["_DB"])
    for cookie in (None, "UY", "CL", "AR"):
        r = _ciro(app, cookie).get("/api/finanzas/recurrentes")
        assert r.status_code == 200, (cookie, r.get_json())
        d = r.get_json()
        filas = d if isinstance(d, list) else (d.get("recurrentes") or d.get("items") or [])
        assert {f["concepto"] for f in filas} == {"Pauta Argentina"}, cookie


def test_meta_ads_le_muestra_solo_los_leads_de_argentina(app):
    db = app.config["_DB"]
    _lead(db, "Porteño", "099 222 222", campana="AR - Web")
    _lead(db, "Montevideano", "099 333 333", campana="Web")
    _lead(db, "Santiaguino", "+56 9 1234 5678")
    for cookie in (None, "UY"):
        d = _ciro(app, cookie).get("/api/leads?crm_group=meta").get_json()
        leads = d if isinstance(d, list) else d["items"]
        assert {l["name"] for l in leads} == {"Porteño"}, cookie


def test_el_dossier_de_marketing_es_solo_de_argentina(app):
    _insights(app.config["_DB"])
    r = _ciro(app, "CL").get("/api/marketing/dossier?desde=2026-11-01&hasta=2026-11-30")
    assert r.status_code == 200, r.get_json()
    texto = json.dumps(r.get_json())
    assert '"AR - Web"' in texto and '"CL - Fidelidad"' not in texto


def test_ve_el_calendario_entero(app):
    assert _ciro(app).get("/api/calendar/events?start=2026-11-01&end=2026-11-30").status_code == 200


@pytest.mark.parametrize("ruta", [
    "/api/leads",                       # sin crm_group: todos los leads
    "/api/leads?crm_group=clientes",
    "/api/finanzas/iva",
    "/api/finanzas/aportes",
    "/api/finanzas/cobros-tarjeta",
    "/api/finanzas/tarjeta/ajustes",
    "/api/marketing/radiografia",
    "/api/calendar/meetings/1/recall-transcript",
    "/api/admin/users",
    "/api/admin/roles",
    "/api/fidelidad/prospectos",
    "/api/tasks",
    "/api/projects",
    "/api/equipo",
])
def test_lo_demas_le_da_403(app, ruta):
    assert _ciro(app).get(ruta).status_code == 403, ruta


@pytest.mark.parametrize("metodo,ruta,cuerpo", [
    ("post", "/api/finanzas/movimientos", {"tipo": "egreso", "concepto": "x", "categoria": "publicidad",
                                           "monto": 1, "moneda": "USD", "fecha": "2026-11-01"}),
    ("post", "/api/calendar/events", {"title": "x"}),
    ("delete", "/api/calendar/meetings/1", None),
    ("post", "/api/meta/leads", {"name": "x", "phone": "1"}),
    ("put", "/api/admin/users/1/role", {"role_id": 1}),
])
def test_no_modifica_nada(app, metodo, ruta, cuerpo):
    r = getattr(_ciro(app), metodo)(ruta, json=cuerpo)
    assert r.status_code == 403, (ruta, r.status_code)


def test_las_paginas_de_admin_lo_mandan_al_crm(app):
    cli = _ciro(app)
    assert cli.get("/").status_code == 200
    r = cli.get("/admin/users")
    assert r.status_code == 302 and r.headers["Location"].endswith("/")


def test_cambiar_el_rol_de_un_usuario_ahora_pide_ser_admin(app):
    db = app.config["_DB"]
    uid = _usuario(db, "programador@scalerics.com", _rol(db, "Programador", ["cal"]))
    admin_rol = _rol(db, "Admin", [])
    r = _cli(app, uid).put(f"/api/admin/users/{uid}/role", json={"role_id": admin_rol})
    assert r.status_code == 403


def test_un_usuario_comun_sigue_eligiendo_el_pais_con_la_cookie(app):
    _movimientos_de_los_tres(app.config["_DB"])
    db = app.config["_DB"]
    uid = _usuario(db, "socio@scalerics.com", _rol(db, "Socio", ["finanzas"]))
    cli = _cli(app, uid)
    cli.set_cookie(paises.COOKIE, "UY")
    d = cli.get("/api/finanzas/recurrentes").get_json()
    filas = d if isinstance(d, list) else (d.get("recurrentes") or d.get("items") or [])
    assert {f["concepto"] for f in filas} == {"Sueldo Uruguay"}
