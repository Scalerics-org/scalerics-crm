"""Rendimiento de la pauta: cuánto costó cada lead, cada demo y cada venta.

Lo que la planilla `Scalerics - Leads - 2026.xlsx` calculaba a mano cada mes.
El CRM ya tenía los leads, las demos y las ventas; lo único que le faltaba era
cuánta plata se gastó en traerlos.

Las etapas se leen de `lead_events`, no del `crm_status` actual: un lead que
llegó a reunión y después se cayó hoy figura como `no_interesa`, y contarlo por
el estado de hoy lo perdería.
"""

import json
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, crear_movimiento, init_db
from services.finanzas import alcanzo, rendimiento_pauta


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "p.db")
    init_db(ruta)
    return ruta


def _lead(db, periodo, source="meta", estados=()):
    """Crea un lead dado de alta en `periodo` que pasó por `estados`."""
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO businesses (name, source, scraped_at) VALUES (?,?,?)",
            (f"lead {periodo}", source, f"{periodo}-15 10:00:00"))
        lid = cur.lastrowid
        for estado in estados:
            conn.execute(
                "INSERT INTO lead_events (lead_id, new_status, created_at) "
                "VALUES (?,?,?)", (lid, estado, f"{periodo}-20 10:00:00"))
        conn.commit()
        return lid
    finally:
        conn.close()


def _pauta(db, periodo, monto):
    return crear_movimiento(db, tipo="egreso", fecha=f"{periodo}-05",
                            periodo=periodo, concepto="Meta Ads",
                            categoria="publicidad", monto=monto,
                            moneda="USD", monto_usd=monto)


# ── alcanzo ───────────────────────────────────────────────────────────────────

def test_alcanzo_es_verdadero_en_la_etapa_exacta():
    assert alcanzo({"reunion_agendada"}, "reunion_agendada") is True


def test_alcanzo_es_verdadero_si_paso_de_largo():
    assert alcanzo({"cliente_cerrado"}, "reunion_agendada") is True


def test_alcanzo_es_falso_si_no_llego():
    assert alcanzo({"interesado"}, "reunion_agendada") is False


def test_no_interesa_no_cuenta_como_etapa():
    """Es una salida del embudo, no un avance."""
    assert alcanzo({"no_interesa"}, "reunion_agendada") is False


def test_un_lead_que_llego_y_despues_se_cayo_sigue_contando():
    """El caso que rompía contar por crm_status actual."""
    assert alcanzo({"reunion_hecha", "no_interesa"}, "reunion_hecha") is True


def test_un_lead_sin_eventos_no_alcanzo_nada():
    assert alcanzo(set(), "reunion_agendada") is False


# ── rendimiento ───────────────────────────────────────────────────────────────

def test_cuenta_los_leads_del_mes_en_que_entraron(db):
    _lead(db, "2026-03")
    _lead(db, "2026-03")
    _lead(db, "2026-04")
    r = rendimiento_pauta(db, "2026-03", "2026-04")
    assert [m["leads"] for m in r["meses"]] == [2, 1]


def test_los_leads_scrapeados_no_cuentan(db):
    """La pauta compra leads de Meta, no el padrón scrapeado."""
    _lead(db, "2026-03", source="meta")
    _lead(db, "2026-03", source=None)
    _lead(db, "2026-03", source="calendly")
    assert rendimiento_pauta(db, "2026-03", "2026-03")["meses"][0]["leads"] == 1


def test_califica_desde_reunion_agendada(db):
    _lead(db, "2026-03", estados=("interesado",))
    _lead(db, "2026-03", estados=("interesado", "reunion_agendada"))
    _lead(db, "2026-03", estados=("reunion_hecha",))
    m = rendimiento_pauta(db, "2026-03", "2026-03")["meses"][0]
    assert m["calificados"] == 2
    assert m["demos"] == 1


def test_la_venta_cuenta_desde_cliente_cerrado(db):
    _lead(db, "2026-03", estados=("negociacion",))
    _lead(db, "2026-03", estados=("cliente_cerrado",))
    _lead(db, "2026-03", estados=("finalizado",))
    assert rendimiento_pauta(db, "2026-03", "2026-03")["meses"][0]["ventas"] == 2


def test_los_costos_dividen_la_inversion_del_mes(db):
    _pauta(db, "2026-03", 300.0)
    for _ in range(3):
        _lead(db, "2026-03")
    _lead(db, "2026-03", estados=("reunion_hecha",))
    m = rendimiento_pauta(db, "2026-03", "2026-03")["meses"][0]
    assert m["inversion_usd"] == 300.0
    assert m["leads"] == 4
    assert m["cpl"] == 75.0
    assert m["costo_demo"] == 300.0


def test_sin_ventas_el_costo_por_venta_es_none_no_cero(db):
    """La planilla mostraba #DIV/0! en esos meses. Cero sería mentira.

    El ROI es distinto: hubo inversión, así que 0.0 es un resultado medido
    (se gastó y no volvió nada todavía), no un dato faltante.
    """
    _pauta(db, "2026-03", 300.0)
    _lead(db, "2026-03")
    m = rendimiento_pauta(db, "2026-03", "2026-03")["meses"][0]
    assert m["costo_venta"] is None
    assert m["roi"] == 0.0


def test_gastar_sin_recuperar_da_roi_cero_no_none(db):
    """Un mes que gasto y no devolvio nada tiene ROI 0, que es un resultado.

    Es distinto de un mes sin inversion, donde el ROI no existe. Mostrar «—»
    en el primer caso escondería justo el mes que hay que mirar.
    """
    _pauta(db, "2026-03", 500.0)
    _lead(db, "2026-03")
    assert rendimiento_pauta(db, "2026-03", "2026-03")["meses"][0]["roi"] == 0.0


def test_un_mes_sin_inversion_no_tiene_roi(db):
    _lead(db, "2026-03")
    assert rendimiento_pauta(db, "2026-03", "2026-03")["meses"][0]["roi"] is None


def test_sin_inversion_los_costos_son_none(db):
    _lead(db, "2026-03")
    m = rendimiento_pauta(db, "2026-03", "2026-03")["meses"][0]
    assert m["inversion_usd"] == 0.0
    assert m["cpl"] is None


def test_el_roi_usa_los_ingresos_atribuidos_a_esos_leads(db):
    """Lo que la planilla nunca pudo calcular: no tenía los ingresos."""
    _pauta(db, "2026-03", 500.0)
    lid = _lead(db, "2026-03", estados=("cliente_cerrado",))
    crear_movimiento(db, tipo="ingreso", fecha="2026-04-10", periodo="2026-04",
                     concepto="Cobro", categoria="desarrollo_web", monto=1500,
                     moneda="USD", monto_usd=1500.0, client_id=lid)
    m = rendimiento_pauta(db, "2026-03", "2026-03")["meses"][0]
    assert m["ingresos_usd"] == 1500.0
    assert m["roi"] == 3.0


def test_un_ingreso_anulado_no_cuenta_en_el_roi(db):
    from database import actualizar_movimiento
    _pauta(db, "2026-03", 500.0)
    lid = _lead(db, "2026-03", estados=("cliente_cerrado",))
    mid = crear_movimiento(db, tipo="ingreso", fecha="2026-04-10",
                           periodo="2026-04", concepto="Cobro",
                           categoria="desarrollo_web", monto=1500,
                           moneda="USD", monto_usd=1500.0, client_id=lid)
    actualizar_movimiento(db, mid, anulado=1)
    m = rendimiento_pauta(db, "2026-03", "2026-03")["meses"][0]
    assert m["ingresos_usd"] == 0.0
    assert m["roi"] == 0.0


def test_solo_cuenta_la_publicidad_no_los_demas_egresos(db):
    _pauta(db, "2026-03", 300.0)
    crear_movimiento(db, tipo="egreso", fecha="2026-03-01", periodo="2026-03",
                     concepto="Fly", categoria="infraestructura", monto=4.18,
                     moneda="USD", monto_usd=4.18)
    m = rendimiento_pauta(db, "2026-03", "2026-03")["meses"][0]
    assert m["inversion_usd"] == 300.0


def test_el_total_agrega_todo_el_rango(db):
    _pauta(db, "2026-03", 300.0)
    _pauta(db, "2026-04", 200.0)
    _lead(db, "2026-03")
    _lead(db, "2026-04")
    t = rendimiento_pauta(db, "2026-03", "2026-04")["total"]
    assert t["inversion_usd"] == 500.0
    assert t["leads"] == 2
    assert t["cpl"] == 250.0


def test_un_mes_sin_nada_aparece_igual(db):
    _pauta(db, "2026-03", 300.0)
    r = rendimiento_pauta(db, "2026-03", "2026-05")
    assert [m["periodo"] for m in r["meses"]] == ["2026-03", "2026-04", "2026-05"]
    assert r["meses"][2]["leads"] == 0


# ── ruta ──────────────────────────────────────────────────────────────────────

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


def _rol(db, nombre, paneles):
    conn = sqlite3.connect(db)
    try:
        fila = conn.execute("SELECT id FROM roles WHERE name=?", (nombre,)).fetchone()
        if fila:
            conn.execute("UPDATE roles SET panel_access=? WHERE id=?",
                         (json.dumps(paneles), fila[0]))
            conn.commit()
            return fila[0]
        rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)",
                           (nombre, json.dumps(paneles))).lastrowid
        conn.commit()
        return rid
    finally:
        conn.close()


def _usuario(db, email, role_id):
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    conn = sqlite3.connect(db)
    conn.execute("UPDATE users SET role_id=? WHERE id=?", (role_id, uid))
    conn.commit()
    conn.close()
    return uid


def _cli(app, uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "test"
    return c


@pytest.fixture
def cli(app):
    db = app.config["_DB"]
    uid = _usuario(db, "socio@scalerics.com",
                   _rol(db, "Socio", ["cola", "finanzas"]))
    return _cli(app, uid)


def test_la_ruta_de_pauta_pide_el_panel(app):
    """Mismo candado que el resto del blueprint."""
    db = app.config["_DB"]
    uid = _usuario(db, "caller@scalerics.com", _rol(db, "Caller", ["cola"]))
    assert _cli(app, uid).get("/api/finanzas/pauta").status_code == 403


def test_la_ruta_de_pauta_rechaza_el_rango_al_reves(cli):
    r = cli.get("/api/finanzas/pauta?desde=2026-09&hasta=2026-08")
    assert r.status_code == 400
