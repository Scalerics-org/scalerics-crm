"""El buscador de arriba (Juan, 1/10): `GET /api/buscar` trae lo que hay
adentro de las secciones, y solo de las que el usuario puede ver."""

import json
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_task, create_user, init_db, upsert_notion_client
from services import fidelidad as fid


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    ruta = str(tmp_path / "b.db")
    init_db(ruta)
    return ruta


def _exec(db, sql, params=()):
    c = sqlite3.connect(db)
    try:
        cur = c.execute(sql, params)
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def _cli(db, email, paneles=None):
    uid = create_user(db, name=email.split("@")[0], email=email, phone=email[:5],
                      password_hash=generate_password_hash("x" * 10))
    if paneles is not None:
        rid = _exec(db, "INSERT INTO roles (name, panel_access) VALUES (?, ?)", (f"rol-{email}", json.dumps(paneles)))
        _exec(db, "UPDATE users SET role_id = ? WHERE id = ?", (rid, uid))
    c = dashboard.create_app(db).test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = email
    return c


@pytest.fixture
def datos(db):
    _exec(db, "INSERT INTO businesses (name, phone, crm_status) VALUES (?,?,?)", ("Pizzería Rodelú SRL", "099111", "cerrado"))
    _exec(db, "INSERT INTO businesses (name, phone, crm_status) VALUES (?,?,?)", ("Ferretería Nápoles", "099222", "contactado"))
    fid.crear_prospecto(db, {"nombre": "Rodelú", "barrio": "Pocitos", "tipo": "Pizzería", "contacto": "Pablo"})
    upsert_notion_client(db, "pag-1", "Rodelú", status="Esperando Confirmación Presupuesto")
    create_task(db, title="Mandarle presupuesto a Rodelu", status="todo")
    return db


def test_busca_en_todo_sin_importar_tildes(datos):
    cli = _cli(datos, "jefe@scalerics.com")              # admin: ve todo
    d = cli.get("/api/buscar?q=rodelu").get_json()
    grupos = {g["nombre"]: g["items"] for g in d["grupos"]}
    assert [i["titulo"] for i in grupos["Leads y clientes"]] == ["Pizzería Rodelú SRL"]
    assert grupos["Leads y clientes"][0]["detalle"] == "Cliente · 099111"
    assert [i["titulo"] for i in grupos["Comercios (Fidelidad)"]] == ["Rodelú"]
    assert grupos["Proceso de venta"][0]["detalle"] == "Esperando Confirmación Presupuesto"
    assert grupos["Tareas"][0]["titulo"] == "Mandarle presupuesto a Rodelu"


def test_varias_palabras_y_por_otros_campos(datos):
    cli = _cli(datos, "jefe@scalerics.com")
    d = cli.get("/api/buscar?q=napoles 099").get_json()
    assert [i["titulo"] for g in d["grupos"] for i in g["items"]] == ["Ferretería Nápoles"]
    d = cli.get("/api/buscar?q=pablo").get_json()                     # el dueño del comercio
    assert [i["titulo"] for g in d["grupos"] for i in g["items"]] == ["Rodelú"]
    assert cli.get("/api/buscar?q=r").get_json() == {"grupos": []}   # una letra no busca


def test_solo_muestra_lo_de_las_secciones_que_puede_ver(datos):
    cli = _cli(datos, "lucas@test.com", paneles=["cola"])
    d = cli.get("/api/buscar?q=rodelu").get_json()
    assert [g["nombre"] for g in d["grupos"]] == ["Comercios (Fidelidad)"]


def test_la_barra_esta_arriba():
    html = dashboard.DASHBOARD_HTML
    assert 'id="gb-q"' in html and "function gbBuscar" in html and "/*BUSCAR_JS*/" not in html
    assert html.index('id="gb"') < html.index('class="frase-equipo"')
