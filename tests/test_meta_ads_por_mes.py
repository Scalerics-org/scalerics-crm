# -*- coding: utf-8 -*-
"""Meta Ads por mes, el semaforo marcado desde el CRM y los envios de formulario.

Pedidos de Juan (15/9):

1. "La seccion meta ads, vamos a distribuirla por meses [...] donde puedas ir
   deslizando por mes y vos me des una opcion de marcarle con color segun el
   semaforo." Y despues: "que toque y que use los del semaforo y se pinten de
   ese color".
2. "Como Meta: si alguien vuelve a llenar el formulario, cuenta tambien en ese
   mes, marcado como 'volvio a escribir'. Setiembre daria 11, igual que Meta."
   El caso real: Adrian Zabaleta lleno el formulario el 12/8 y otra vez el 8/9.

El dia de hoy de las pruebas de pantalla es el 15/9/2026.
"""

import json
import re
import shutil
import sqlite3
import subprocess
import time
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from werkzeug.security import generate_password_hash

import dashboard
import services.dossier as dossier
import services.finanzas as finanzas
from database import (ENVIOS_META_SQL, add_lead_event, create_user, init_db,
                      listar_demos_realizadas, registrar_envio_meta)
from services.planilla_semaforo import (COLOR_A_ESTADO, ESTADO_A_COLOR, RANK,
                                        SEMAFORO, aplicar, color_de_estado,
                                        estado_de_color, marcar_color,
                                        mes_montevideo, sincronizar_demos)

HTML = dashboard.DASHBOARD_HTML
RAIZ = Path(__file__).resolve().parents[1]
SRC = (RAIZ / "dashboard.py").read_text(encoding="utf-8")
HOY = date(2026, 9, 15)
TEL = "+59899111222"

sin_node = pytest.mark.skipif(shutil.which("node") is None, reason="node no esta instalado")


def _entre(texto, desde, hasta):
    i = texto.index(desde)
    return texto[i:texto.index(hasta, i + len(desde))]


CSS = _entre(SRC, "/* ── Meta Ads por mes y semaforo", "/* ── fin Meta Ads por mes */")
JS = _entre(SRC, "// -- Lista por mes (pedido de Juan, 15/9)", "function _fillMetaEstados(")
PANEL = _entre(SRC, '<div id="meta-panel" class="panel">', "<!-- ======= DEMOS PANEL")


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    return ruta


@pytest.fixture
def app(db, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    a = dashboard.create_app(db)
    a.config["TESTING"] = True
    return a


def _usuario(db, email, paneles=None):
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    if paneles is not None:
        conn = sqlite3.connect(db)
        rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)",
                           (f"rol-{email}", json.dumps(paneles))).lastrowid
        conn.execute("UPDATE users SET role_id=? WHERE id=?", (rid, uid))
        conn.commit()
        conn.close()
    return uid


def _cliente(app, uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Juan"
    return c


@pytest.fixture
def cli(app, db):
    return _cliente(app, _usuario(db, "jefe@scalerics.com"))


def _lead(db, bid, nombre=None, phone=None, estado="sin_contactar", source="meta",
          scraped_at="2026-08-12 10:00:00", email=None):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO businesses (id, name, phone, email, crm_status, source, scraped_at) "
                 "VALUES (?,?,?,?,?,?,?)",
                 (bid, nombre or f"Negocio {bid}", phone, email, estado, source, scraped_at))
    conn.commit()
    conn.close()


def _fila(db, bid, *cols):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"SELECT {', '.join(cols)} FROM businesses WHERE id=?", (bid,)).fetchone()
    finally:
        conn.close()


def _demos_planilla(db, bid):
    return [(d["mes_planilla"], d["estado_planilla"]) for d in listar_demos_realizadas(db, client_id=bid)
            if d.get("origen") == "planilla"]


def _sync(db, color, mes="Agosto", tel=TEL):
    """Lo mismo que hace POST /api/meta/sync-planilla: estados y despues demos."""
    filas = [{"tel": tel, "mail": "", "color": color, "mes": mes}]
    r = aplicar(db, filas)
    r["demos"] = sincronizar_demos(db, filas, hoy=HOY)
    return r


def _adrian(db):
    """La ficha de Adrian: primer formulario el 12/8, la vuelta el 8/9."""
    _lead(db, 1, "Adrián Zabaleta", phone=TEL, scraped_at="2026-08-12 10:00:00")
    registrar_envio_meta(db, 1, "L-AGO", "2026-08-12 10:00:00")
    registrar_envio_meta(db, 1, "L-SEP", "2026-09-08 14:03:11")


# ── la leyenda ───────────────────────────────────────────────────────────────

def test_los_colores_del_crm_son_los_de_la_planilla():
    for clave, etiqueta, estado, hexa in SEMAFORO:
        assert COLOR_A_ESTADO[hexa] == estado, clave
        assert estado_de_color("#" + hexa) == estado
        assert color_de_estado(estado) == clave
    assert {e for _, _, e, _ in SEMAFORO} == set(COLOR_A_ESTADO.values()), \
        "todo color de la planilla tiene su opcion en el CRM"


def test_los_nombres_de_demo_son_los_del_registro_de_demos():
    nombres = {clave: etiqueta for clave, etiqueta, _, _ in SEMAFORO}
    for clave, etiqueta_demos in (("verde", "Demo agendada"), ("celeste", "Demo realizada"),
                                  ("violeta", "Hubo demo y no cerró"), ("venta", "Venta concretada")):
        assert nombres[clave] == etiqueta_demos
        assert f"etiqueta: '{etiqueta_demos}'" in HTML


def test_todo_estado_conocido_tiene_color_menos_sin_contactar():
    for estado in RANK:
        assert bool(color_de_estado(estado)) == (estado != "sin_contactar"), estado
    assert color_de_estado(None) == ""
    assert set(ESTADO_A_COLOR.values()) == {c for c, _, _, _ in SEMAFORO}


def test_el_js_tiene_los_mismos_colores_y_nombres_que_python():
    del_js = re.findall(r"\{clave: '(\w+)', etiqueta: '([^']+)'\}",
                        _entre(JS, "const MM_SEMAFORO = [", "];"))
    assert del_js == [(c, e) for c, e, _, _ in SEMAFORO]


def test_los_colores_son_tokens_iguales_en_los_dos_temas():
    osc = re.search(r":root\{([^}]*)\}", HTML).group(1)
    cla = re.search(r"body\.light\{([^}]*)\}", HTML).group(1)
    for clave, _, _, hexa in SEMAFORO:
        token = "--semaforo-" + clave
        assert f"{token}:#{hexa};" in osc and f"{token}:#{hexa};" in cla, token
        assert f".mm-c-{clave}{{--mm-color:var({token})}}" in CSS


# ── el mes en hora de Montevideo ─────────────────────────────────────────────

@pytest.mark.parametrize("fecha,mes", [
    ("2026-09-01 02:59:59", "2026-08"),      # 23:59 del 31/8 aca
    ("2026-09-01 03:00:00", "2026-09"),      # 00:00 del 1/9 aca
    ("2026-08-31T23:30:00+0000", "2026-08"),
    ("2026-01-01 01:00:00", "2025-12"),      # cruza el año
    ("2026-09-01", "2026-09"),               # sin hora no se corre
    ("", None), ("basura", None), ("2026-13-01 10:00:00", None),
])
def test_mes_montevideo(fecha, mes):
    assert mes_montevideo(fecha) == mes


# ── marcar el color: API ─────────────────────────────────────────────────────

def test_marcar_un_color_lo_guarda_donde_lo_lee_demos(cli, db):
    _lead(db, 7, "Óptica Luz", phone=TEL, scraped_at="2026-08-20 15:00:00")
    r = cli.post("/api/meta/leads/7/semaforo", json={"color": "celeste"})
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    assert d["ok"] and d["crm_status"] == "demo_1" and d["semaforo"] == "celeste"
    assert _fila(db, 7, "crm_status", "semaforo_origen") == ("demo_1", "crm")
    assert _fila(db, 7, "semaforo_at")[0], "queda la hora del cambio"

    # El Registro de demos lo lee igual que las que trae la planilla.
    demos = cli.get("/api/demos-realizadas").get_json()["demos"]
    assert [(x["client_id"], x["origen"], x["estado_planilla"], x["mes_planilla"]) for x in demos] \
        == [(7, "planilla", "realizada", "2026-08")]

    # Y la lista de Meta Ads lo trae pintado.
    lead = next(x for x in cli.get("/api/leads?crm_group=meta").get_json() if x["id"] == 7)
    assert lead["semaforo"] == "celeste"


def test_marcar_deja_rastro_como_cualquier_cambio_de_estado(cli, db):
    _lead(db, 7)
    cli.post("/api/meta/leads/7/semaforo", json={"color": "violeta"})
    conn = sqlite3.connect(db)
    evento = conn.execute("SELECT new_status, note, created_by FROM lead_events WHERE lead_id=7").fetchone()
    actividad = conn.execute("SELECT action, detail FROM activity_log WHERE entity_id=7").fetchone()
    conn.close()
    assert evento == ("presupuesto_enviado", "semaforo marcado en el CRM: Hubo demo y no cerró", "Juan")
    assert actividad == ("status_change", "presupuesto_enviado")


@pytest.mark.parametrize("cuerpo", [{"color": "fucsia"}, {"color": ""}, {"color": None},
                                    {"color": 3}, {}, {"color": "VERDE"}])
def test_un_color_invalido_da_400_y_no_toca_nada(cli, db, cuerpo):
    _lead(db, 7, estado="interesado")
    r = cli.post("/api/meta/leads/7/semaforo", json=cuerpo)
    assert r.status_code == 400
    assert _fila(db, 7, "crm_status", "semaforo_origen") == ("interesado", None)


def test_lead_inexistente_404_y_de_otra_cohorte_400(cli, db):
    _lead(db, 8, source="discovery")
    assert cli.post("/api/meta/leads/999/semaforo", json={"color": "verde"}).status_code == 404
    assert cli.post("/api/meta/leads/8/semaforo", json={"color": "verde"}).status_code == 400
    assert _fila(db, 8, "crm_status")[0] == "sin_contactar"


def test_pide_el_panel_meta(app, db):
    _lead(db, 7)
    sin = _cliente(app, _usuario(db, "finanzas@scalerics.com", ["finanzas"]))
    assert sin.post("/api/meta/leads/7/semaforo", json={"color": "verde"}).status_code == 403
    assert _fila(db, 7, "crm_status")[0] == "sin_contactar"
    con = _cliente(app, _usuario(db, "ventas@scalerics.com", ["meta"]))
    assert con.post("/api/meta/leads/7/semaforo", json={"color": "verde"}).status_code == 200
    assert app.test_client().post("/api/meta/leads/7/semaforo",
                                  json={"color": "rojo"}).status_code == 401


def test_un_cliente_no_se_baja_con_un_toque(cli, db):
    _lead(db, 7, estado="en_desarrollo")
    assert cli.post("/api/meta/leads/7/semaforo", json={"color": "verde"}).status_code == 409
    assert cli.post("/api/meta/leads/7/semaforo", json={"color": "sin_color"}).status_code == 409
    r = cli.post("/api/meta/leads/7/semaforo", json={"color": "venta"}).get_json()
    assert r["cambio"] is False and _fila(db, 7, "crm_status")[0] == "en_desarrollo"


def test_el_mismo_color_no_escribe(cli, db):
    _lead(db, 7, estado="demo_2")
    r = cli.post("/api/meta/leads/7/semaforo", json={"color": "celeste"}).get_json()
    assert r["cambio"] is False and r["crm_status"] == "demo_2"
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM lead_events").fetchone()[0] == 0
    conn.close()


def test_sin_color_vuelve_a_sin_contactar_y_saca_la_demo(cli, db):
    _lead(db, 7)
    cli.post("/api/meta/leads/7/semaforo", json={"color": "verde"})
    assert _demos_planilla(db, 7) == [("2026-08", "agendada")]
    r = cli.post("/api/meta/leads/7/semaforo", json={"color": "sin_color"}).get_json()
    assert r["crm_status"] == "sin_contactar" and r["semaforo"] == ""
    assert _demos_planilla(db, 7) == []


def test_la_demo_va_al_mes_de_la_lista_si_escribio_ese_mes(db):
    _adrian(db)
    marcar_color(db, 1, "verde", mes="2026-09")
    assert _demos_planilla(db, 1) == [("2026-09", "agendada")]
    marcar_color(db, 1, "sin_color", mes="2026-09")
    marcar_color(db, 1, "celeste", mes="2026-07")    # julio no escribio: su primer mes
    assert _demos_planilla(db, 1) == [("2026-08", "realizada")]


# ── convivencia con la planilla ──────────────────────────────────────────────

VERDE, CELESTE, VIOLETA, NEGRO, VENTA = "#00ff00", "#00ffff", "#ff00ff", "#000000", "#274e13"


def test_un_sync_con_un_color_anterior_no_pisa_lo_marcado_a_mano(db):
    _lead(db, 1, phone=TEL)
    _sync(db, VERDE)
    assert _fila(db, 1, "crm_status", "semaforo_planilla") == ("demo_agendada", "demo_agendada")
    marcar_color(db, 1, "celeste")
    r = _sync(db, VERDE)                      # la planilla sigue en verde
    assert _fila(db, 1, "crm_status", "semaforo_origen") == ("demo_1", "crm")
    assert r["marcado_a_mano"] == 1 and r["actualizados"] == 0
    assert r["demos"]["marcado_a_mano"] == 1
    assert _demos_planilla(db, 1) == [("2026-08", "realizada")], "la demo tampoco vuelve"


def test_una_correccion_a_mano_para_atras_tambien_se_respeta(db):
    _lead(db, 1, phone=TEL)
    _sync(db, CELESTE)
    marcar_color(db, 1, "verde")              # se pinto celeste por error
    _sync(db, CELESTE)
    assert _fila(db, 1, "crm_status")[0] == "demo_agendada"
    assert _demos_planilla(db, 1) == [("2026-08", "agendada")]


def test_si_la_planilla_se_repinta_con_un_color_posterior_avanza(db):
    _lead(db, 1, phone=TEL)
    _sync(db, VERDE)
    marcar_color(db, 1, "celeste")
    r = _sync(db, VIOLETA)
    assert r["actualizados"] == 1
    assert _fila(db, 1, "crm_status", "semaforo_origen") == ("presupuesto_enviado", "planilla")
    assert _demos_planilla(db, 1) == [("2026-08", "no_cerro")]


def test_si_la_planilla_se_repinta_hacia_atras_no_retrocede(db):
    _lead(db, 1, phone=TEL)
    _sync(db, VERDE)
    marcar_color(db, 1, "violeta")
    r = _sync(db, CELESTE)                    # cambio, pero para atras
    assert r["no_retrocede"] == 1
    assert _fila(db, 1, "crm_status", "semaforo_origen") == ("presupuesto_enviado", "crm")


def test_el_negro_repintado_despues_gana_como_siempre(db):
    _lead(db, 1, phone=TEL)
    _sync(db, VERDE)
    marcar_color(db, 1, "celeste")
    _sync(db, NEGRO)
    assert _fila(db, 1, "crm_status")[0] == "no_interesa"


def test_sin_lectura_previa_de_la_planilla_solo_avanza(db):
    _lead(db, 1, phone=TEL)
    marcar_color(db, 1, "celeste")            # antes del primer sync
    _sync(db, NEGRO)
    assert _fila(db, 1, "crm_status")[0] == "demo_1", "no se sabe si el negro es viejo"
    _sync(db, VENTA)
    assert _fila(db, 1, "crm_status")[0] == "cerrado"


def test_la_segunda_corrida_sigue_sin_escribir(db):
    _lead(db, 1, phone=TEL)
    conn = sqlite3.connect(db)
    _sync(db, CELESTE)
    antes = conn.total_changes
    r = aplicar(db, [{"tel": TEL, "color": CELESTE}])
    assert r["sin_cambio"] == 1 and r["actualizados"] == 0
    assert _fila(db, 1, "semaforo_planilla")[0] == "demo_1"
    assert conn.total_changes == antes
    conn.close()


# ── envios de formulario ─────────────────────────────────────────────────────

def _envios_por_mes(db):
    conn = sqlite3.connect(db)
    try:
        return dict(conn.execute(
            f"SELECT substr(fecha_local, 1, 7), COUNT(*) FROM ({ENVIOS_META_SQL}) GROUP BY 1"))
    finally:
        conn.close()


def test_adrian_cuenta_en_agosto_y_en_setiembre(db):
    _adrian(db)
    assert _envios_por_mes(db) == {"2026-08": 1, "2026-09": 1}


def test_el_envio_se_registra_una_sola_vez_por_meta_lead_id(db):
    _lead(db, 1)
    assert registrar_envio_meta(db, 1, "L-1", "2026-09-08 14:03:11") is True
    assert registrar_envio_meta(db, 1, "L-1", "2026-09-08 14:03:11") is False
    assert _envios_por_mes(db) == {"2026-08": 1, "2026-09": 1}


def test_el_mes_del_envio_es_el_de_montevideo(db):
    _lead(db, 1, scraped_at="2026-07-10 12:00:00")
    registrar_envio_meta(db, 1, "L-BORDE", "2026-09-01 02:59:00")
    registrar_envio_meta(db, 1, "L-SEP", "2026-09-01 03:00:00")
    assert _envios_por_mes(db) == {"2026-07": 1, "2026-08": 1, "2026-09": 1}


def test_una_ficha_sin_envios_cuenta_por_su_scraped_at_y_el_primero_no_se_duplica(db):
    _lead(db, 1, scraped_at="2026-05-20 15:00:00")
    _lead(db, 2, scraped_at="2026-05-21 10:00:10")
    registrar_envio_meta(db, 2, "L-2", "2026-05-21 10:00:00")   # el mismo, re-traido por el import
    assert _envios_por_mes(db) == {"2026-05": 2}


def test_los_contadores_de_marketing_cuentan_envios(db):
    _adrian(db)
    add_lead_event(db, 1, "demo_1")
    serie = {m["periodo"]: m for m in dossier.serie_mensual(db, "2026-08-01", "2026-09-30")}
    assert (serie["2026-08"]["leads"], serie["2026-09"]["leads"]) == (1, 1)
    assert (serie["2026-08"]["demos"], serie["2026-09"]["demos"]) == (1, 0), \
        "la demo cuenta una vez, en el mes del primer contacto"

    todas = next(b for b in dossier.por_campana(db, "2026-09-01", "2026-09-30")
                 if b["campana"] == "todas")
    valores = {m["id"].split(".")[-1]: m["valor"] for m in todas["metricas"]}
    assert valores["leads_crm"] == 1 and valores["demos"] == 0

    assert dossier.embudo_por_campana(db, "2026-09-01", "2026-09-30")[0]["etapas"][0]["n"] == 1
    assert sum(s["leads_crm"] for s in dossier.serie_semanal(db, "2026-09-01", "2026-09-30")) == 1
    assert dossier.llegada_de_leads(db, "2026-08-01", "2026-09-30")["total"] == 2
    assert dossier.historico(db, "2026-09-01")["leads"] == 1

    pauta = {m["periodo"]: m for m in finanzas.rendimiento_pauta(db, "2026-08", "2026-09")["meses"]}
    assert (pauta["2026-08"]["leads"], pauta["2026-09"]["leads"]) == (1, 1)
    assert (pauta["2026-08"]["demos"], pauta["2026-09"]["demos"]) == (1, 0)


def _lead_de_graph(meta_id, creado, telefono, nombre="Adrián Zabaleta"):
    return {"id": meta_id, "created_time": creado, "campaign_name": "Campaña",
            "field_data": [{"name": "full_name", "values": [nombre]},
                           {"name": "phone_number", "values": [telefono]}]}


def test_el_import_diario_rellena_los_envios_sin_crear_fichas(db, monkeypatch):
    from routes import meta

    _lead(db, 1, "Adrián Zabaleta", phone=TEL, scraped_at="2026-08-12 10:00:00")
    monkeypatch.setenv("META_PAGE_TOKEN", "token")
    monkeypatch.setenv("META_PAGE_ID", "PAGINA")

    def falso_get(url, params=None, timeout=None):
        r = MagicMock()
        r.ok = True
        if url.endswith("/leadgen_forms"):
            r.json.return_value = {"data": [{"id": "F1", "name": "Formulario"}]}
        elif url.endswith("/F1/leads"):
            r.json.return_value = {"data": [
                _lead_de_graph("L-AGO", "2026-08-12T10:00:00+0000", TEL),
                _lead_de_graph("L-SEP", "2026-09-08T14:03:11+0000", "+598 99 111 222"),
            ]}
        else:
            r.json.return_value = {"data": []}
        return r

    avisos = MagicMock()
    monkeypatch.setattr(meta.requests, "get", falso_get)
    monkeypatch.setattr(meta, "_notify_new_meta_lead", avisos)
    assert meta._run_import_sync(db) == (0, 2)
    assert meta._run_import_sync(db) == (0, 2), "la segunda pasada no registra nada nuevo"

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0] == 1
    assert conn.execute("SELECT business_id, created_time FROM meta_lead_envios ORDER BY created_time").fetchall() \
        == [(1, "2026-08-12 10:00:00"), (1, "2026-09-08 14:03:11")]
    conn.close()
    assert not avisos.called, "el backfill no avisa a nadie"
    assert _envios_por_mes(db) == {"2026-08": 1, "2026-09": 1}


def test_el_webhook_de_alguien_que_vuelve_guarda_el_envio_una_vez(app, db, monkeypatch):
    from routes import meta

    _lead(db, 1, "Adrián Zabaleta", phone=TEL, scraped_at="2026-08-12 10:00:00")
    respuesta = MagicMock()
    respuesta.raise_for_status.return_value = None
    respuesta.json.return_value = _lead_de_graph("L-SEP", "2026-09-08T14:03:11+0000", TEL)
    avisos = MagicMock()
    monkeypatch.setattr(meta.requests, "get", lambda *a, **k: respuesta)
    monkeypatch.setattr(meta, "_notify_new_meta_lead", avisos)
    monkeypatch.setenv("META_PAGE_TOKEN", "token")

    meta._fetch_and_store_lead(app, "L-SEP", "F1")
    limite = time.time() + 5
    while not avisos.called and time.time() < limite:
        time.sleep(0.02)
    meta._fetch_and_store_lead(app, "L-SEP", "F1")      # Meta reintenta
    time.sleep(0.1)

    assert avisos.call_count == 1
    assert _envios_por_mes(db) == {"2026-08": 1, "2026-09": 1}


def test_la_lista_de_meta_trae_los_envios_de_cada_lead(cli, db):
    _adrian(db)
    _lead(db, 2, phone="+59899000000", scraped_at="2026-07-01 12:00:00", estado="no_interesa")
    leads = {x["id"]: x for x in cli.get("/api/leads?crm_group=meta").get_json()}
    assert leads[1]["envios"] == ["2026-08-12 10:00:00", "2026-09-08 14:03:11"]
    assert leads[2]["envios"] == ["2026-07-01 12:00:00"]
    assert leads[2]["semaforo"] == "negro" and leads[1]["semaforo"] == ""


# ── la pantalla ──────────────────────────────────────────────────────────────

def test_get_raiz_da_200_con_la_lista_por_mes(cli):
    r = cli.get("/")
    assert r.status_code == 200
    for fragmento in ('id="meta-mes-label"', 'id="meta-mes-ant"', 'id="meta-mes-sig"', "Este mes",
                      'id="meta-mes-resumen"', 'id="meta-mes-buscar-todos"', 'id="meta-estado-filter"'):
        assert fragmento.encode() in r.data, fragmento
    assert b'id="meta-month-filter"' not in r.data, "el desplegable de meses lo reemplaza la navegacion"


@pytest.mark.parametrize("nombre,texto", [("css", CSS), ("js", JS), ("panel", PANEL)])
def test_nada_que_jinja_o_python_interpreten(nombre, texto):
    for trampa in ("{#", "{{", "{%"):
        assert trampa not in texto, f"{trampa!r} en el {nombre}"
    assert chr(92) not in texto, f"un backslash en el {nombre}: Python se lo come"


def test_el_css_nuevo_usa_solo_tokens():
    reglas = re.findall(r"([^{}]+)\{([^{}]*)\}", CSS)
    assert len(reglas) > 20
    for selector, cuerpo in reglas:
        assert not re.search(r"#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])|rgba?\(", cuerpo), selector.strip()
    assert "min-height:40px" in CSS and "min-height:44px" in CSS, "opciones comodas para el dedo"


def test_lo_nuevo_del_js_lleva_el_prefijo_mm_y_no_toca_la_carga_inicial():
    nombres = re.findall(r"^(?:async )?function ([A-Za-z_$][\w$]*)\(", JS, re.M)
    nombres += re.findall(r"^(?:const|let) ([A-Za-z_$][\w$]*)", JS, re.M)
    assert len(nombres) > 25
    assert all(n.startswith(("mm", "MM_")) for n in nombres), nombres
    for n in nombres:
        assert len(re.findall(r"^(?:async )?(?:function|const|let) " + re.escape(n) + r"\b", HTML, re.M)) == 1, n
    assert "setInterval" not in JS and "showPanel(" not in JS
    assert HTML.count("\ncalLoaded = true; renderCalendar();") == 1


# ── se pinta de verdad (node, DOM falso: el arnes de tests/test_equipo.py) ───

_ARNES = r"""
const _els = {};
function _el(id) {
  if (!_els[id]) {
    _els[id] = { id, innerHTML: '', textContent: '', value: '', placeholder: '', type: '',
                 className: '', style: {}, dataset: {}, options: [], selectedIndex: 0, disabled: false,
                 classList: { add(){}, remove(){}, toggle(){}, contains: () => false },
                 querySelectorAll: () => [], querySelector: () => null, closest: () => null,
                 setAttribute(){}, getAttribute: () => null, focus(){},
                 addEventListener(){}, appendChild(){}, remove(){} };
  }
  return _els[id];
}
globalThis.document = {
  getElementById: _el, querySelectorAll: () => [], querySelector: () => null,
  body: { classList: { contains: () => false, add(){}, remove(){}, toggle(){} } },
  createElement: () => _el('tmp'), addEventListener(){},
};
globalThis.window = globalThis;
globalThis.localStorage = { getItem: () => null, setItem(){}, removeItem(){} };
globalThis.lucide = { createIcons(){} };
globalThis.alert = (m) => { _alertas.push(String(m)); };
globalThis.confirm = () => true;
globalThis.setInterval = () => 0;
const _alertas = [];
const _RESPUESTAS = __RESPUESTAS__;
const _pedidos = [];
globalThis.fetch = (url, opciones) => {
  _pedidos.push([String(url), (opciones && opciones.method) || 'GET', (opciones && opciones.body) || null]);
  const r = _RESPUESTAS[String(url)];
  if (r === 'falla') return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) });
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(r === undefined ? {} : r) });
};
"""


def _correr_js(tmp_path, respuestas, prueba):
    bloques = re.findall(r"<script>(.*?)</script>", HTML, re.S)
    archivo = tmp_path / "meta_mes.js"
    archivo.write_text(_ARNES.replace("__RESPUESTAS__", json.dumps(respuestas, ensure_ascii=False))
                       + "\n".join(bloques) + "\n" + prueba, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, (r.stderr or "")[:3000]
    return json.loads(r.stdout.strip().splitlines()[-1])


def _leads_de_pantalla(cli, db):
    """Cuatro leads servidos por la API de verdad, con bordes de mes."""
    _adrian(db)                                                         # 12/8 y 8/9
    conn = sqlite3.connect(db)
    conn.execute("UPDATE businesses SET crm_status='demo_1' WHERE id=1")
    conn.commit()
    conn.close()
    _lead(db, 2, "Borde Agosto", phone="+59899000002", estado="interesado",
          scraped_at="2026-09-01 02:30:00")                             # 31/8 23:30 aca
    _lead(db, 3, "Heladeria Sur", phone="+59899000003", scraped_at="2026-09-01 03:00:00")
    registrar_envio_meta(db, 3, "L-3B", "2026-09-10 12:00:00")          # dos veces en setiembre
    _lead(db, 4, "Heladeria Norte", phone="+59899000004", estado="no_interesa",
          scraped_at="2026-07-10 15:00:00")
    return cli.get("/api/leads?crm_group=meta").get_json()


_HOY_JS = "Date.now = () => Date.UTC(2026, 8, 15, 15, 0, 0);\n"


@sin_node
def test_la_lista_va_de_a_un_mes_con_su_resumen(cli, db, tmp_path):
    leads = _leads_de_pantalla(cli, db)
    s = _correr_js(tmp_path, {"/api/leads?crm_group=meta": leads}, _HOY_JS + """
(async () => {
  const s = {};
  const foto = k => { s[k] = {label: _el('meta-mes-label').textContent, body: _el('meta-body').innerHTML,
                              resumen: _el('meta-mes-resumen').innerHTML, cuenta: _el('meta-count').textContent,
                              ant: _el('meta-mes-ant').disabled, sig: _el('meta-mes-sig').disabled}; };
  await loadMetaPanel();
  foto('sep');
  mmMes(1); foto('futuro');
  mmMes(-1); foto('ago');
  mmMes(-1); foto('jul');
  mmMes(-1); foto('antesDelPrimero');
  mmMesVista = '2026-06'; renderMetaTable(); foto('vacio');
  mmMesHoy(); foto('hoy');
  s.conteo = mmConteo([{semaforo: 'verde'}, {semaforo: 'verde'}, {semaforo: 'negro'}, {semaforo: ''}], '');
  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""")
    sep = s["sep"]
    assert sep["label"] == "Setiembre 2026" and sep["sig"] is True and sep["ant"] is False
    assert "Adrián Zabaleta" in sep["body"] and "Heladeria Sur" in sep["body"]
    assert "Borde Agosto" not in sep["body"] and "Heladeria Norte" not in sep["body"]
    assert sep["body"].count("mmAbrirColor(3)") == 1, "una persona, una fila, aunque escribio dos veces"
    assert "Setiembre 2026: 3 leads · 2 personas" in sep["resumen"], sep["resumen"]
    assert 'mm-c-celeste"><span class="mm-punto"></span>1 Demo realizada' in sep["resumen"]
    assert 'mm-c-sin"><span class="mm-punto"></span>1 sin color' in sep["resumen"]
    assert sep["cuenta"] == "2 leads"
    adrian = sep["body"][sep["body"].index("Adrián Zabaleta") - 600:sep["body"].index("Adrián Zabaleta") + 900]
    assert "Volvió a escribir" in adrian and "Primer contacto: 12/08/2026" in adrian

    assert s["futuro"]["label"] == "Setiembre 2026", "no pasa al futuro"
    ago = s["ago"]
    assert ago["label"] == "Agosto 2026" and ago["sig"] is False
    assert "Borde Agosto" in ago["body"] and "Adrián Zabaleta" in ago["body"]
    assert "Volvió a escribir" not in ago["body"], "en su primer mes no es una vuelta"
    assert "Agosto 2026: 2 leads" in ago["resumen"]
    assert s["jul"]["label"] == "Julio 2026" and s["jul"]["ant"] is True
    assert s["antesDelPrimero"]["label"] == "Julio 2026", "no va antes del lead mas viejo"
    assert "No hay leads en Junio 2026" in s["vacio"]["body"]
    assert "Junio 2026: 0 leads" in s["vacio"]["resumen"]
    assert s["hoy"]["label"] == "Setiembre 2026"
    assert s["conteo"] == {"personas": 4, "envios": 4, "por": {"verde": 2, "negro": 1}, "sin": 1}


@sin_node
def test_al_entrar_al_panel_siempre_se_ve_el_mes_actual(cli, db, tmp_path):
    """Juan: "cuando entres a meta ads que lo primero que aparezca sea el mes
    actual no todos los meses". Aunque antes se haya ido a otro mes, buscado en
    todos o dejado un color abierto; y el poll no cambia el mes que se mira."""
    leads = _leads_de_pantalla(cli, db)
    s = _correr_js(tmp_path, {"/api/leads?crm_group=meta": leads}, _HOY_JS + """
(async () => {
  const s = {};
  let poll = null;
  globalThis.setInterval = (fn) => { poll = fn; return 1; };
  showPanel('meta');
  await new Promise(r => setTimeout(r, 20));
  s.primera = {label: _el('meta-mes-label').textContent, body: _el('meta-body').innerHTML};
  mmMes(-1); mmMes(-1);
  metaSearch('heladeria'); mmBuscarTodos(true); mmAbrirColor(4);
  s.navegado = _el('meta-mes-label').textContent;
  s.pedidosAntes = _pedidos.length;
  await poll();
  s.trasPoll = _el('meta-mes-label').textContent;
  metaSearch('');
  showPanel('cal');
  showPanel('meta');
  await new Promise(r => setTimeout(r, 20));
  s.vuelta = {label: _el('meta-mes-label').textContent, body: _el('meta-body').innerHTML,
              todos: mmTodosLosMeses, abierto: mmAbierto};
  await loadMetaPanel();
  s.recarga = _el('meta-mes-label').textContent;
  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""")
    assert s["primera"]["label"] == "Setiembre 2026"
    assert "Heladeria Sur" in s["primera"]["body"] and "Heladeria Norte" not in s["primera"]["body"]
    assert "Borde Agosto" not in s["primera"]["body"]
    assert s["navegado"] == "Julio 2026"
    assert s["trasPoll"] == "Julio 2026", "el poll no cambia de mes"
    assert s["vuelta"]["label"] == "Setiembre 2026"
    assert "Heladeria Norte" not in s["vuelta"]["body"] and "Borde Agosto" not in s["vuelta"]["body"]
    assert s["vuelta"]["todos"] is False and s["vuelta"]["abierto"] is None
    assert s["recarga"] == "Setiembre 2026"


@sin_node
def test_cada_lead_se_pinta_con_su_color_y_tocar_una_opcion_lo_guarda(cli, db, tmp_path):
    leads = _leads_de_pantalla(cli, db)
    guardado = {"ok": True, "cambio": True, "crm_status": "presupuesto_enviado",
                "semaforo": "violeta", "semaforo_origen": "crm"}
    s = _correr_js(tmp_path, {"/api/leads?crm_group=meta": leads,
                              "/api/meta/leads/1/semaforo": guardado,
                              "/api/meta/leads/3/semaforo": "falla"}, _HOY_JS + """
(async () => {
  const s = {};
  await loadMetaPanel();
  s.inicial = _el('meta-body').innerHTML;
  mmAbrirColor(1);
  s.abierto = _el('meta-body').innerHTML;
  await mmMarcarColor(1, 'violeta');
  s.pedido = _pedidos.filter(p => p[1] === 'POST').pop();
  s.despues = _el('meta-body').innerHTML;
  s.resumenDespues = _el('meta-mes-resumen').innerHTML;
  mmAbrirColor(3);
  await mmMarcarColor(3, 'rojo');
  s.alertas = _alertas.slice();
  s.trasFalla = _el('meta-body').innerHTML;
  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""")
    ini = s["inicial"]
    assert 'class="table-row no-cb mm-pintado mm-c-celeste" data-mm-color="celeste"' in ini
    assert 'class="table-row no-cb row-sin_contactar" data-mm-color=""' in ini, "sin color queda como hoy"
    assert '<button class="mm-sem-btn mm-c-celeste" onclick="mmAbrirColor(1)"' in ini
    assert '<button class="mm-sem-btn mm-c-sin" onclick="mmAbrirColor(3)"' in ini and ">Sin color</button>" in ini
    assert "Ver ficha" in ini and "IG/FB" in ini, "lo que ya mostraba cada lead sigue"
    assert "mm-opciones" not in ini

    abierto = s["abierto"]
    assert abierto.count('class="mm-opcion ') == 8
    for clave, etiqueta, _, _ in SEMAFORO:
        assert f"mmMarcarColor(1, '{clave}')\"><span class=\"mm-punto\"></span>{etiqueta}</button>" in abierto
    assert "mm-opcion mm-c-celeste mm-actual" in abierto
    assert "mmMarcarColor(1, 'sin_color')" in abierto

    url, metodo, cuerpo = s["pedido"]
    assert (url, metodo) == ("/api/meta/leads/1/semaforo", "POST")
    assert json.loads(cuerpo) == {"color": "violeta", "mes": "2026-09"}
    assert 'mm-pintado mm-c-violeta" data-mm-color="violeta"' in s["despues"]
    assert "mm-opciones" not in s["despues"], "al elegir, la opcion se cierra"
    assert "Hubo demo y no cerró" in s["resumenDespues"]

    assert s["alertas"] == ["No se pudo guardar el color"]
    assert "mm-pintado mm-c-rojo" not in s["trasFalla"], "si falla no se pinta"
    assert 'mm-c-sin mm-abierto" onclick="mmAbrirColor(3)"' in s["trasFalla"],         "y las opciones quedan abiertas para reintentar"


@sin_node
def test_deslizar_cambia_de_mes_sin_romper_el_scroll_ni_los_toques(cli, db, tmp_path):
    leads = _leads_de_pantalla(cli, db)
    s = _correr_js(tmp_path, {"/api/leads?crm_group=meta": leads}, _HOY_JS + """
(async () => {
  const s = {};
  const gesto = (x0, y0, x1, y1) => {
    mmToqueInicio({touches: [{clientX: x0, clientY: y0}]});
    mmToqueFin({changedTouches: [{clientX: x1, clientY: y1}]});
    return _el('meta-mes-label').textContent;
  };
  await loadMetaPanel();
  s.listo = mmSwipeListo;
  s.izquierdaEnHoy = gesto(300, 200, 150, 210);   // siguiente: no hay futuro
  s.derecha = gesto(100, 200, 230, 215);          // anterior
  s.derecha2 = gesto(100, 200, 230, 185);
  s.izquierda = gesto(300, 200, 180, 190);        // siguiente
  s.vertical = gesto(300, 100, 240, 400);         // scroll: no cambia
  s.corto = gesto(200, 200, 160, 200);            // 40px: no cambia
  s.toque = gesto(200, 200, 203, 201);            // un toque: no cambia
  s.sinInicio = (mmToqueFin({changedTouches: [{clientX: 0, clientY: 0}]}), _el('meta-mes-label').textContent);
  s.dir = [mmDireccionDeslizar(51, 0), mmDireccionDeslizar(-51, 0), mmDireccionDeslizar(50, 0),
           mmDireccionDeslizar(-60, 61), mmDireccionDeslizar(-61, 60)];
  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""")
    assert s["listo"] is True
    assert s["izquierdaEnHoy"] == "Setiembre 2026"
    assert s["derecha"] == "Agosto 2026" and s["derecha2"] == "Julio 2026"
    assert s["izquierda"] == "Agosto 2026"
    assert s["vertical"] == s["corto"] == s["toque"] == s["sinInicio"] == "Agosto 2026"
    assert s["dir"] == [-1, 1, 0, 0, 1]


@sin_node
def test_la_busqueda_y_el_filtro_andan_dentro_del_mes_y_se_puede_buscar_en_todos(cli, db, tmp_path):
    leads = _leads_de_pantalla(cli, db)
    s = _correr_js(tmp_path, {"/api/leads?crm_group=meta": leads}, _HOY_JS + """
(async () => {
  const s = {};
  await loadMetaPanel();
  metaSearch('heladeria');
  s.mes = _el('meta-body').innerHTML; s.aviso = _el('meta-mes-buscar-todos').innerHTML;
  mmBuscarTodos(true);
  s.todos = _el('meta-body').innerHTML; s.avisoTodos = _el('meta-mes-buscar-todos').innerHTML;
  metaSearch('');
  s.limpio = _el('meta-mes-buscar-todos').innerHTML; s.todosTrasLimpiar = mmTodosLosMeses;
  metaEstadoFilter('demo_1');
  s.estado = _el('meta-body').innerHTML; s.opciones = _el('meta-estado-filter').innerHTML;
  mmMes(-1);
  s.estadoAgo = _el('meta-body').innerHTML;
  mmMes(-1);
  s.estadoJul = _el('meta-body').innerHTML; s.opcionesJul = _el('meta-estado-filter').innerHTML;
  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""")
    assert "Heladeria Sur" in s["mes"] and "Heladeria Norte" not in s["mes"]
    assert "Buscar en todos los meses (2)" in s["aviso"]
    assert "Heladeria Sur" in s["todos"] and "Heladeria Norte" in s["todos"]
    assert "Buscando en todos los meses" in s["avisoTodos"]
    assert s["limpio"] == "" and s["todosTrasLimpiar"] is False
    assert "Adrián Zabaleta" in s["estado"] and "Heladeria Sur" not in s["estado"]
    assert 'value="demo_1">demo_1 (1)' in s["opciones"]
    assert "Adrián Zabaleta" in s["estadoAgo"] and "Borde Agosto" not in s["estadoAgo"]
    assert "No hay leads que coincidan con el filtro" in s["estadoJul"]
    assert 'value="demo_1">demo_1 (0)' in s["opcionesJul"], "el filtro elegido no se cae solo"
