"""Seguimiento de leads: la agenda de llamados (pedido de Juan, 14/9).

Criterios de aceptación del PDF, cada uno con su test `test_cN_...`:
1 es el primer ítem de Ventas · 2 un lead sin recordatorio pendiente no aparece
· 3 uno de ayer va a Vencidos con borde rojo · 4 "+1 semana" corre la fecha sin
formulario · 5 "Hecho" no cierra sin próxima fecha o "no hace falta volver a
llamar" · 6 "no hace falta" lo saca de la lista y el llamado queda en el
historial · 7 un segundo recordatorio cierra el anterior · 8 sin teléfono,
Llamar y WhatsApp deshabilitados.

Hoy está fijo en el lunes 14/9/2026, 10:30 de Montevideo.
"""

import json
import re
import shutil
import sqlite3
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import dashboard
import routes.seg_leads as rutas
from database import create_user, delete_business, init_db, merge_business
from services import seg_leads as sl

HTML = dashboard.DASHBOARD_HTML
RAIZ = Path(__file__).resolve().parents[1]
SRC = (RAIZ / "dashboard.py").read_text(encoding="utf-8")

sin_node = pytest.mark.skipif(shutil.which("node") is None,
                              reason="node no esta instalado")

AHORA = sl.MVD.localize(datetime(2026, 9, 14, 10, 30))
HOY = "2026-09-14"          # lunes
AYER = "2026-09-13"
HACE_6 = "2026-09-08"
DOMINGO = "2026-09-20"
PROX_LUNES = "2026-09-21"


def _entre(texto: str, desde: str, hasta: str) -> str:
    i = texto.index(desde)
    return texto[i:texto.index(hasta, i + len(desde))]


PANEL = _entre(SRC, "<!-- ======= SEG LEADS PANEL ======= -->", "<!-- ======= FIN SEG LEADS PANEL ======= -->")
MODALES = _entre(SRC, "<!-- ======= SEG LEADS MODALES ======= -->", "<!-- ======= FIN SEG LEADS MODALES ======= -->")
JS = _entre(SRC, "// ========== Seguimiento de leads ==========", "// ========== FIN Seguimiento de leads ==========")
CSS = _entre(SRC, "/* ── Seguimiento de leads", "/* ── Equipo")
FUENTES = {"panel": PANEL, "modales": MODALES, "js": JS, "css": CSS}


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(rutas, "_ahora", lambda: AHORA)
    db = str(tmp_path / "seg.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


def _exec(db, sql, params=()):
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _filas(db, sql, params=()):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _rol(db, nombre, paneles):
    return _exec(db, "INSERT INTO roles (name, panel_access) VALUES (?,?)",
                 (nombre, json.dumps(paneles)))


def _usuario(db, email, role_id=None):
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    if role_id is not None:
        _exec(db, "UPDATE users SET role_id=? WHERE id=?", (role_id, uid))
    return uid


def _cli(app, uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Juan"
    return c


@pytest.fixture
def cli(app):
    return _cli(app, _usuario(app.config["_DB"], "jefe@scalerics.com"))


def _db(cli):
    return cli.application.config["_DB"]


def _lead(db, nombre, telefono=None, rubro=None):
    return _exec(db, "INSERT INTO businesses (name, phone, category) VALUES (?,?,?)",
                 (nombre, telefono, rubro))


def _crear(cli, lead_id, fecha=HOY, motivo="Preguntar si vio el presupuesto", **extra):
    return cli.post("/api/seg-leads/recordatorios",
                    json={"lead_id": lead_id, "fecha": fecha, "motivo": motivo, **extra})


def _pantalla(cli):
    r = cli.get("/api/seg-leads")
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def _todos(pantalla):
    return [i for g in sl.GRUPOS for i in pantalla["grupos"][g]]


def _pendientes(db, lead_id):
    return _filas(db, "SELECT id FROM seg_recordatorios WHERE lead_id=? AND estado='pendiente'",
                  (lead_id,))


# ── criterio 1: primer ítem de Ventas ────────────────────────────────────────

def test_c1_es_el_primer_item_del_grupo_ventas():
    grupo = _entre(HTML, '<div class="nav-section-label">VENTAS</div>',
                   '<div class="nav-section-label">OPERACIÓN</div>')
    assert re.findall(r'id="nav-(\w+)"', grupo)[:2] == ["seg_leads", "wa"]
    assert re.search(r'id="nav-seg_leads"[^>]*>.*?Seguimiento de leads</div>', grupo)
    prioridad = re.findall(r"'(\w+)'", re.search(r"const NAV_PRIORITY = \[([^\]]*)\]", HTML).group(1))
    assert prioridad.index("seg_leads") + 1 == prioridad.index("wa")


def test_esta_registrado_en_todos_lados():
    assert "seg_leads:'phone-call'" in re.search(r"const NAV_ICONS = \{(.*?)\n\}", HTML, re.S).group(1)
    assert "seg_leads:'Seguimiento'" in re.search(r"const NAV_LABELS = \{(.*?)\n\}", HTML, re.S).group(1)
    listas = re.findall(r"const ALL_PANELS = \[([^\]]*)\]", SRC)
    assert len(listas) == 2 and all("'seg_leads'" in l for l in listas)
    assert "seg_leads:'Seguimiento de leads'" in re.search(r"const PANEL_LABELS = \{([^}]*)\}", SRC).group(1)
    assert "if (name === 'seg_leads') loadSegLeads();" in HTML
    for regla in ("#nav-seg_leads .nav-icon{stroke:", "#nav-seg_leads.active .nav-icon{stroke:",
                  "body.light #nav-seg_leads .nav-icon{stroke:"):
        assert len(re.findall("^" + re.escape(regla), HTML, re.M)) == 1, regla
    assert "from routes.seg_leads import seg_leads_bp" in SRC
    assert "seg_leads_bp" in re.search(r"for bp in \((.*?)\):", SRC, re.S).group(1)
    assert PANEL.count('id="seg_leads-panel" class="panel"') == 1
    assert "<h1>Seguimiento de leads</h1>" in PANEL
    assert 'onclick="slAbrirNuevo()">Nuevo recordatorio</button>' in PANEL


def test_no_revive_la_seccion_vieja():
    """La sección Seguimientos (`seguimientos`) se borró el 14/9. Esta es otra,
    con otro id: no reusa nada de aquella."""
    for resto in ("'seguimientos'", "seguimientos-panel", "loadSeguimientos", "cb-overdue"):
        assert resto not in PANEL + MODALES + JS + CSS, resto


def test_los_roles_con_proceso_de_venta_reciben_el_panel_una_sola_vez(tmp_path):
    db = str(tmp_path / "roles.db")
    init_db(db)
    con = _rol(db, "Ventas NC", ["notion_clients", "wa"])
    sin = _rol(db, "Caller X", ["cola", "wa"])
    # Una base de antes de este deploy: todavía sin las tablas de seguimiento.
    _exec(db, "DROP TABLE seg_llamados")
    _exec(db, "DROP TABLE seg_recordatorios")
    _exec(db, "DELETE FROM panel_grants_aplicados WHERE panel='seg_leads'")
    init_db(db)
    acceso = {rid: json.loads(p) for rid, p in _filas(db, "SELECT id, panel_access FROM roles")}
    assert "seg_leads" in acceso[con]
    assert "seg_leads" not in acceso[sin]
    # Juan se lo saca a mano: el próximo arranque no se lo vuelve a poner.
    _exec(db, "UPDATE roles SET panel_access=? WHERE id=?", (json.dumps(["notion_clients"]), con))
    init_db(db)
    acceso = {rid: json.loads(p) for rid, p in _filas(db, "SELECT id, panel_access FROM roles")}
    assert "seg_leads" not in acceso[con]


def test_la_pagina_abre(cli):
    r = cli.get("/")
    assert r.status_code == 200
    pagina = r.get_data(as_text=True)
    assert pagina.count('id="seg_leads-panel"') == 1
    assert pagina.count('id="nav-seg_leads"') == 1


def test_sin_el_panel_da_403_y_con_el_panel_entra(app):
    db = app.config["_DB"]
    sin = _cli(app, _usuario(db, "caller@x.com", _rol(db, "SinSeg", ["wa", "notion_clients"])))
    assert sin.get("/api/seg-leads").status_code == 403
    assert sin.post("/api/seg-leads/recordatorios", json={}).status_code == 403
    con = _cli(app, _usuario(db, "ventas@x.com", _rol(db, "ConSeg", ["seg_leads"])))
    assert con.get("/api/seg-leads").status_code == 200


def test_sin_sesion_no_entra(app):
    assert app.test_client().get("/api/seg-leads").status_code in (302, 401)


# ── criterio 2: solo lo pendiente ────────────────────────────────────────────

def test_c2_un_lead_sin_recordatorio_pendiente_no_aparece(cli):
    db = _db(cli)
    con = _lead(db, "Con recordatorio", "099111222")
    _lead(db, "Sin recordatorio", "099333444")
    cerrado = _lead(db, "Ya cerrado", "099555666")
    assert _crear(cli, con).status_code == 201
    rid = _crear(cli, cerrado).get_json()["id"]
    assert cli.post(f"/api/seg-leads/recordatorios/{rid}/hecho",
                    json={"resultado": "Listo", "sin_volver": True}).status_code == 200

    pantalla = _pantalla(cli)
    assert [i["nombre"] for i in _todos(pantalla)] == ["Con recordatorio"]
    assert pantalla["pendientes"] == 1


# ── criterio 3: vencidos ─────────────────────────────────────────────────────

def test_c3_un_recordatorio_de_ayer_va_a_vencidos(cli):
    db = _db(cli)
    ayer = _lead(db, "Ayer", "099111222")
    viejo = _lead(db, "Viejo", "099333444")
    _crear(cli, ayer, fecha=AYER)
    _crear(cli, viejo, fecha=HACE_6)

    pantalla = _pantalla(cli)
    vencidos = pantalla["grupos"]["vencidos"]
    assert [i["nombre"] for i in vencidos] == ["Viejo", "Ayer"], "el más viejo primero"
    assert [i["vence_texto"] for i in vencidos] == ["hace 6 días", "ayer"]
    assert all(i["vencido"] for i in vencidos)
    assert pantalla["contadores"]["vencidos"] == 2 and pantalla["vencidos"] == 2
    # El borde rojo de 3px es de la tarjeta vencida (lo pinta el test de node).
    assert ".sl-vencida{border-left:3px solid var(--rojo)}" in CSS


def test_los_grupos_hoy_esta_semana_y_mas_adelante(cli):
    db = _db(cli)
    ids = {n: _lead(db, n, f"09900000{k}") for k, n in enumerate(
        ["Sin hora", "Con hora", "Domingo", "Lunes que viene"])}
    _crear(cli, ids["Sin hora"])
    _crear(cli, ids["Con hora"], hora="15:00")
    _crear(cli, ids["Domingo"], fecha=DOMINGO)
    _crear(cli, ids["Lunes que viene"], fecha=PROX_LUNES, hora="9:05")

    p = _pantalla(cli)
    assert p["hoy"] == HOY and p["hoy_texto"] == "lunes 14/09"
    assert [(i["nombre"], i["vence_texto"]) for i in p["grupos"]["hoy"]] == [
        ("Con hora", "15:00"), ("Sin hora", "sin hora")]
    assert [(i["nombre"], i["vence_texto"]) for i in p["grupos"]["semana"]] == [("Domingo", "dom 20/09")]
    assert [(i["nombre"], i["vence_texto"]) for i in p["grupos"]["despues"]] == [
        ("Lunes que viene", "lun 21/09 · 09:05")]
    assert p["contadores"] == {"vencidos": 0, "hoy": 2, "semana": 1, "despues": 1}


def test_hoy_sale_de_la_fecha_de_montevideo_y_no_de_la_del_servidor(cli, monkeypatch):
    """A las 22:00 de Montevideo del lunes, en UTC ya es martes. Un recordatorio
    del lunes sigue siendo de hoy, no vencido."""
    noche_utc = datetime(2026, 9, 15, 1, 0, tzinfo=timezone.utc)
    assert sl.hoy_mvd(noche_utc) == date(2026, 9, 14)
    monkeypatch.setattr(rutas, "_ahora", lambda: sl.ahora_mvd(noche_utc))
    _crear(cli, _lead(_db(cli), "Nocturno", "099111222"), fecha=HOY)
    p = _pantalla(cli)
    assert p["hoy"] == HOY
    assert [i["nombre"] for i in p["grupos"]["hoy"]] == ["Nocturno"]
    assert not p["grupos"]["vencidos"]


@pytest.mark.parametrize("fecha,grupo", [
    (date(2026, 9, 13), "vencidos"), (date(2026, 9, 14), "hoy"), (date(2026, 9, 15), "semana"),
    (date(2026, 9, 20), "semana"), (date(2026, 9, 21), "despues"),
])
def test_grupo_de_un_lunes(fecha, grupo):
    assert sl.grupo_de(fecha, date(2026, 9, 14)) == grupo


def test_un_domingo_esta_semana_queda_vacia():
    domingo = date(2026, 9, 20)
    assert sl.grupo_de(date(2026, 9, 21), domingo) == "despues"


# ── criterio 4: posponer ─────────────────────────────────────────────────────

def test_c4_mas_una_semana_corre_la_fecha_sin_pedir_nada_mas(cli):
    db = _db(cli)
    rid = _crear(cli, _lead(db, "Pospone", "099111222"), fecha=DOMINGO).get_json()["id"]
    r = cli.post(f"/api/seg-leads/recordatorios/{rid}/posponer", json={"cuanto": "semana"})
    assert r.status_code == 200 and r.get_json()["fecha"] == "2026-09-27"
    r = cli.post(f"/api/seg-leads/recordatorios/{rid}/posponer", json={"cuanto": "mes"})
    assert r.get_json()["fecha"] == "2026-10-27"
    assert _filas(db, "SELECT fecha, estado FROM seg_recordatorios WHERE id=?", (rid,)) == [
        ("2026-10-27", "pendiente")]


def test_posponer_un_vencido_cuenta_desde_hoy(cli):
    """Si contara desde la fecha vieja, "+1 semana" sobre uno de hace 8 días lo
    dejaría vencido igual."""
    rid = _crear(cli, _lead(_db(cli), "Viejo", "099111222"), fecha="2026-09-06").get_json()["id"]
    r = cli.post(f"/api/seg-leads/recordatorios/{rid}/posponer", json={"cuanto": "semana"})
    assert r.get_json()["fecha"] == "2026-09-21"


@pytest.mark.parametrize("desde,esperado", [
    (date(2026, 1, 31), date(2026, 2, 28)), (date(2026, 12, 15), date(2027, 1, 15)),
    (date(2028, 1, 31), date(2028, 2, 29)),
])
def test_mas_un_mes(desde, esperado):
    assert sl.sumar_mes(desde) == esperado


def test_posponer_valida(cli):
    db = _db(cli)
    rid = _crear(cli, _lead(db, "P", "099111222")).get_json()["id"]
    assert cli.post(f"/api/seg-leads/recordatorios/{rid}/posponer", json={"cuanto": "año"}).status_code == 400
    assert cli.post(f"/api/seg-leads/recordatorios/{rid}/posponer", data="x").status_code == 400
    assert cli.post("/api/seg-leads/recordatorios/9999/posponer", json={"cuanto": "semana"}).status_code == 404
    cli.post(f"/api/seg-leads/recordatorios/{rid}/hecho", json={"resultado": "ok", "sin_volver": True})
    assert cli.post(f"/api/seg-leads/recordatorios/{rid}/posponer", json={"cuanto": "semana"}).status_code == 409


# ── criterio 5: Hecho pide la próxima fecha ──────────────────────────────────

@pytest.mark.parametrize("datos,parte", [
    ({"resultado": "No atendió"}, "cuándo volvés a llamar"),
    ({"resultado": "No atendió", "sin_volver": False, "proxima_fecha": ""}, "cuándo volvés a llamar"),
    ({"resultado": "No atendió", "sin_volver": True, "proxima_fecha": PROX_LUNES}, "una sola cosa"),
    ({"sin_volver": True}, "qué pasó"),
    ({"resultado": "   ", "proxima_fecha": PROX_LUNES}, "qué pasó"),
    ({"resultado": "ok", "proxima_fecha": AYER}, "anterior a hoy"),
    ({"resultado": "ok", "proxima_fecha": "21/09/2026"}, "AAAA-MM-DD"),
    ({"resultado": "ok", "proxima_fecha": PROX_LUNES, "proxima_hora": "25:00"}, "HH:MM"),
    ({"resultado": "ok", "sin_volver": "si"}, "true o false"),
])
def test_c5_hecho_no_cierra_sin_proxima_fecha_ni_no_hace_falta(cli, datos, parte):
    db = _db(cli)
    lead = _lead(db, "Hecho", "099111222")
    rid = _crear(cli, lead).get_json()["id"]
    r = cli.post(f"/api/seg-leads/recordatorios/{rid}/hecho", json=datos)
    assert r.status_code == 400 and parte in r.get_json()["error"], r.get_json()
    assert _pendientes(db, lead) == [(rid,)], "sigue pendiente"
    assert not _filas(db, "SELECT 1 FROM seg_llamados")


def test_hecho_con_proxima_fecha_deja_el_llamado_y_crea_el_siguiente(cli):
    db = _db(cli)
    lead = _lead(db, "Sigue", "099111222")
    rid = _crear(cli, lead, motivo="Mandar la propuesta", nota="llamar después de las 18").get_json()["id"]
    r = cli.post(f"/api/seg-leads/recordatorios/{rid}/hecho",
                 json={"resultado": "No atendió", "proxima_fecha": PROX_LUNES, "proxima_hora": "18:30"})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d["ok"] and d["nuevo_id"] and d["lead_id"] == lead

    assert _filas(db, "SELECT estado, cierre FROM seg_recordatorios WHERE id=?", (rid,)) == [("hecho", "llamado")]
    nuevo = _filas(db, "SELECT fecha, hora, motivo, nota, estado FROM seg_recordatorios WHERE id=?",
                   (d["nuevo_id"],))
    assert nuevo == [(PROX_LUNES, "18:30", "Mandar la propuesta", "llamar después de las 18", "pendiente")]
    assert _filas(db, "SELECT lead_id, fecha, resultado, recordatorio_id, creado_por FROM seg_llamados") == [
        (lead, "2026-09-14 10:30", "No atendió", rid, "Juan")]
    item = _todos(_pantalla(cli))[0]
    assert item["ultimo_resultado"] == "No atendió"


def test_hecho_dos_veces_da_409(cli):
    rid = _crear(cli, _lead(_db(cli), "Doble", "099111222")).get_json()["id"]
    datos = {"resultado": "ok", "sin_volver": True}
    assert cli.post(f"/api/seg-leads/recordatorios/{rid}/hecho", json=datos).status_code == 200
    assert cli.post(f"/api/seg-leads/recordatorios/{rid}/hecho", json=datos).status_code == 409
    assert cli.post("/api/seg-leads/recordatorios/9999/hecho", json=datos).status_code == 404


# ── criterio 6: no hace falta volver a llamar ────────────────────────────────

def test_c6_no_hace_falta_lo_saca_de_la_lista_y_queda_en_el_historial(cli):
    db = _db(cli)
    lead = _lead(db, "Cerrado", "099111222")
    rid = _crear(cli, lead).get_json()["id"]
    r = cli.post(f"/api/seg-leads/recordatorios/{rid}/hecho",
                 json={"resultado": "Contrató con otro", "sin_volver": True})
    assert r.status_code == 200 and r.get_json()["nuevo_id"] is None

    assert _pantalla(cli)["pendientes"] == 0
    ficha = cli.get(f"/api/seg-leads/lead/{lead}").get_json()
    assert ficha["pendiente"] is None
    assert [(l["resultado"], l["fecha_texto"]) for l in ficha["llamados"]] == [
        ("Contrató con otro", "lun 14/09/2026 · 10:30")]
    assert ficha["lead"] == {"id": lead, "nombre": "Cerrado"}
    assert cli.get("/api/seg-leads/lead/9999").status_code == 404


# ── criterio 7: un solo pendiente por lead ───────────────────────────────────

def test_c7_un_segundo_recordatorio_cierra_el_anterior(cli):
    db = _db(cli)
    lead = _lead(db, "Dos veces", "099111222")
    primero = _crear(cli, lead, motivo="Primero").get_json()["id"]
    r = _crear(cli, lead, fecha=PROX_LUNES, motivo="Segundo")
    assert r.status_code == 201
    segundo = r.get_json()
    assert segundo["cerrados"] == [primero]

    assert _filas(db, "SELECT estado, cierre FROM seg_recordatorios WHERE id=?", (primero,)) == [
        ("hecho", "reemplazado")]
    assert _pendientes(db, lead) == [(segundo["id"],)]
    assert [i["motivo"] for i in _todos(_pantalla(cli))] == ["Segundo"]
    assert not _filas(db, "SELECT 1 FROM seg_llamados"), "reemplazar no es un llamado"


def test_la_base_no_deja_dos_pendientes_aunque_alguien_saltee_el_codigo(cli):
    db = _db(cli)
    lead = _lead(db, "Indice", "099111222")
    _crear(cli, lead)
    with pytest.raises(sqlite3.IntegrityError):
        _exec(db, "INSERT INTO seg_recordatorios (lead_id, fecha, motivo) VALUES (?,?,?)",
              (lead, HOY, "otro"))


# ── criterio 8: sin teléfono ─────────────────────────────────────────────────

def test_c8_un_lead_sin_telefono_no_trae_numero_para_llamar(cli):
    db = _db(cli)
    _crear(cli, _lead(db, "Sin tel"))
    _crear(cli, _lead(db, "Con tel", "099 123 456"))
    items = {i["nombre"]: i for i in _todos(_pantalla(cli))}
    assert items["Sin tel"]["tel"] is None and items["Sin tel"]["wa"] is None
    assert items["Con tel"]["tel"] == "+59899123456" and items["Con tel"]["wa"] == "59899123456"


@pytest.mark.parametrize("crudo,esperado", [
    ("099 123 456", ("+59899123456", "59899123456")),
    ("+598 99 123 456", ("+59899123456", "59899123456")),
    ("99123456", ("+59899123456", "59899123456")),
    ("00598 99 123 456", ("+59899123456", "59899123456")),
    ("2901 2345", ("+59829012345", "59829012345")),
    ("+54 9 11 5555 4444", ("+5491155554444", "5491155554444")),
    (None, (None, None)), ("", (None, None)), ("123", (None, None)), ("sin dato", (None, None)),
])
def test_telefonos(crudo, esperado):
    assert sl.telefonos(crudo) == esperado


# ── validación al crear ──────────────────────────────────────────────────────

@pytest.mark.parametrize("cambio,parte", [
    ({"motivo": "  "}, "motivo"),
    ({"motivo": None}, "motivo"),
    ({"motivo": "x" * 201}, "una línea"),
    ({"fecha": "14/09/2026"}, "AAAA-MM-DD"),
    ({"fecha": "2026-02-30"}, "AAAA-MM-DD"),
    ({"hora": "9"}, "HH:MM"),
    ({"hora": "24:00"}, "HH:MM"),
    ({"lead_id": "1"}, "lead_id"),
    ({"lead_id": True}, "lead_id"),
    ({"nota": 5}, "texto"),
])
def test_crear_valida_en_el_servidor(cli, cambio, parte):
    lead = _lead(_db(cli), "Valida", "099111222")
    datos = {"lead_id": lead, "fecha": HOY, "motivo": "Llamar", **cambio}
    r = cli.post("/api/seg-leads/recordatorios", json=datos)
    assert r.status_code == 400 and parte in r.get_json()["error"], r.get_json()


def test_crear_para_un_lead_que_no_existe_da_404(cli):
    assert _crear(cli, 9999).status_code == 404
    assert cli.post("/api/seg-leads/recordatorios", data="no json").status_code == 400


def test_el_motivo_queda_en_una_linea(cli):
    db = _db(cli)
    lead = _lead(db, "Linea", "099111222")
    _crear(cli, lead, motivo="  Preguntar\n por   la web ", nota="   ", hora="")
    assert _filas(db, "SELECT motivo, nota, hora FROM seg_recordatorios") == [
        ("Preguntar por la web", None, None)]


# ── fusionar y borrar leads ──────────────────────────────────────────────────

def test_fusionar_leads_lleva_el_seguimiento_y_deja_un_solo_pendiente(cli):
    db = _db(cli)
    origen = _lead(db, "Origen", "099111222")
    destino = _lead(db, "Destino", "099333444")
    r_origen = _crear(cli, origen, motivo="del origen").get_json()["id"]
    cli.post(f"/api/seg-leads/recordatorios/{r_origen}/hecho",
             json={"resultado": "Hablamos", "proxima_fecha": PROX_LUNES})
    r_destino = _crear(cli, destino, motivo="del destino").get_json()["id"]

    merge_business(db, origen, destino)
    assert _pendientes(db, destino) == [(r_destino,)]
    assert _filas(db, "SELECT lead_id FROM seg_llamados") == [(destino,)]
    assert not _filas(db, "SELECT 1 FROM seg_recordatorios WHERE lead_id=?", (origen,))

    delete_business(db, destino)
    assert not _filas(db, "SELECT 1 FROM seg_recordatorios")
    assert not _filas(db, "SELECT 1 FROM seg_llamados")


# ── las trampas de DASHBOARD_HTML ────────────────────────────────────────────

@pytest.mark.parametrize("nombre", sorted(FUENTES))
def test_nada_que_jinja_o_python_interpreten(nombre):
    texto = FUENTES[nombre]
    for trampa in ("{#", "{{", "{%"):
        assert trampa not in texto, f"{trampa!r} en el {nombre} de Seguimiento de leads"
    assert chr(92) not in texto, f"un backslash en el {nombre}: Python se lo come"


def test_el_css_usa_solo_tokens():
    reglas = re.findall(r"([^{}]+)\{([^{}]*)\}", CSS)
    assert len(reglas) > 30
    for selector, cuerpo in reglas:
        assert not re.search(r"#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])|rgba?\(", cuerpo), selector.strip()
    assert "body.light" not in CSS.split("*/", 1)[1]
    for nombre in ("panel", "modales", "js"):
        assert "style=" not in FUENTES[nombre], f"estilo inline en el {nombre}"


def test_todo_lo_del_js_lleva_el_prefijo_sl():
    nombres = re.findall(r"^(?:async )?function ([A-Za-z_$][\w$]*)\(", JS, re.M)
    nombres += re.findall(r"^(?:const|let) ([A-Za-z_$][\w$]*)", JS, re.M)
    assert len(nombres) > 20
    sueltos = [n for n in nombres if not (n.startswith("sl") or n.startswith("SL_") or n == "loadSegLeads")]
    assert not sueltos, f"sin prefijo: {sueltos}"
    for nombre in nombres:
        declaraciones = re.findall(r"^(?:async )?(?:function|const|let) " + re.escape(nombre) + r"\b",
                                   HTML, re.M)
        assert len(declaraciones) == 1, f"{nombre} está declarado {len(declaraciones)} veces"


def test_la_carga_inicial_no_se_toco():
    assert "calLoaded = true; renderCalendar();" in HTML
    assert "setInterval" not in JS


# ── se pinta de verdad ───────────────────────────────────────────────────────

_ARNES = r"""
const _els = {};
function _el(id) {
  if (!_els[id]) {
    _els[id] = { id, innerHTML: '', textContent: '', value: '', placeholder: '', type: '',
                 className: '', style: {}, dataset: {}, options: [], selectedIndex: 0,
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
globalThis.alert = () => {};
globalThis.confirm = () => true;
globalThis.setInterval = () => 0;
const _RESPUESTAS = __RESPUESTAS__;
const _pedidos = [];
globalThis.fetch = (url, opciones) => {
  _pedidos.push([String(url), (opciones && opciones.method) || 'GET']);
  const r = _RESPUESTAS[String(url)];
  if (r === 'falla') return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) });
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(r === undefined ? {} : r) });
};
"""


def _correr_js(tmp_path, respuestas, prueba):
    bloques = re.findall(r"<script>(.*?)</script>", HTML, re.S)
    archivo = tmp_path / "seg_leads.js"
    archivo.write_text(_ARNES.replace("__RESPUESTAS__", json.dumps(respuestas, ensure_ascii=False))
                       + "\n".join(bloques) + "\n" + prueba, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, (r.stderr or "")[:2000]
    return json.loads(r.stdout.strip().splitlines()[-1])


_PRUEBA = """
(async () => {
  const abiertos = [];
  ['sl-modal-hecho', 'sl-modal-nuevo'].forEach(id => {
    _el(id).classList = { add() { abiertos.push(id); }, remove() {}, toggle() {}, contains: () => false };
  });
  const tick = () => new Promise(ok => setTimeout(ok, 10));
  const s = {};

  await loadSegLeads();
  s.resumen = _el('sl-resumen').textContent;
  s.contadores = _el('sl-contadores').innerHTML;
  s.lista = _el('sl-lista').innerHTML;
  _el('sl-grupo-hoy').scrollIntoView = () => { s.scroll = 'hoy'; };
  slIrAGrupo('hoy');

  // Criterio 4: +1 semana es un POST y nada mas; ningun formulario.
  let antes = _pedidos.length;
  await slPosponer(__ID_HOY__, 'semana');
  s.posponer = _pedidos.slice(antes);
  s.abiertosTrasPosponer = abiertos.length;

  // Criterio 5: Hecho no manda nada sin proxima fecha o "no hace falta".
  slAbrirHecho(__ID_VENCIDO__);
  s.abiertos = abiertos.slice();
  s.motivoPrecargado = _el('sl-hecho-motivo').value;
  s.notaPrecargada = _el('sl-hecho-nota').value;
  _el('sl-hecho-resultado').value = 'Atendió, pide precio';
  antes = _pedidos.length;
  await slGuardarHecho();
  s.errorSinFecha = _el('sl-hecho-error').textContent;
  _el('sl-hecho-resultado').value = '  ';
  _el('sl-hecho-fecha').value = '2026-09-21';
  await slGuardarHecho();
  s.errorSinResultado = _el('sl-hecho-error').textContent;
  s.postsInvalidos = _pedidos.slice(antes).filter(p => p[1] === 'POST').length;

  _el('sl-hecho-sin-volver').checked = true;
  slHechoRapido('mes');
  s.rapidoMes = _el('sl-hecho-fecha').value;
  s.sinVolverTrasRapido = _el('sl-hecho-sin-volver').checked;

  _el('sl-hecho-resultado').value = 'Ya contrató';
  _el('sl-hecho-sin-volver').checked = true;
  slSinVolver();
  s.proximoOculto = _el('sl-hecho-proximo').hidden;
  antes = _pedidos.length;
  await slGuardarHecho();
  s.postHecho = _pedidos.slice(antes);

  // Nuevo recordatorio desde la ficha: avisa que el abierto se va a cerrar.
  slAbrirNuevo(__LEAD__, 'Ana Pérez');
  await tick();
  s.nuevoLead = _el('sl-nuevo-lead').textContent;
  s.nuevoFecha = _el('sl-nuevo-fecha').value;
  s.aviso = _el('sl-nuevo-aviso').textContent;
  await slGuardarNuevo();
  s.errorNuevoSinMotivo = _el('sl-nuevo-error').textContent;
  _el('sl-nuevo-motivo').value = 'Mandar la propuesta';
  antes = _pedidos.length;
  await slGuardarNuevo();
  s.postNuevo = _pedidos.slice(antes);
  slAbrirNuevo();
  await tick();
  await slGuardarNuevo();
  s.errorNuevoSinLead = _el('sl-nuevo-error').textContent;
  s.buscador = _el('sl-nuevo-resultados').innerHTML;

  // Proceso de venta y la ficha del lead.
  s.notion = _notionClientCardHtml({id: 3, name: 'Ficha Notion', notion_page_id: 'ab-cd',
                                    business_id: __LEAD__, business_name: 'Ana Pérez'}, false);
  s.notionSinConectar = slBotonNotionHtml({id: 4, name: 'Otra ficha'});
  abiertos.length = 0;
  slAbrirDesdeNotion({dataset: {buscar: 'Otra ficha'}});
  s.abiertosNotion = abiertos.slice();
  s.buscarNotion = _el('sl-nuevo-buscar').value;
  _cpData.seg = __FICHA__;
  _cpData.calls = [];
  s.ficha = _cpRenderCalls();
  s.fichaSinPermiso = slFichaHtml(null);

  // Un grupo vacio no se muestra.
  s.soloHoy = slListaHtml({hoy_texto: 'lunes 14/09', grupos: {hoy: __ESTADO__.grupos.hoy}});
  s.nada = slListaHtml({grupos: {}});

  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
"""


def _escenario(cli):
    """Cuatro leads, uno por grupo, como los devuelve el servidor de verdad."""
    db = _db(cli)
    ana = _lead(db, "Ana Pérez", "099 123 456", "Peluquería")
    bruno = _lead(db, "Bruno Sin Tel", None, "Taller")
    carla = _lead(db, "Carla", "098 765 432")
    diego = _lead(db, "Diego", "2901 2345")
    rid = _crear(cli, ana, motivo="Primer contacto").get_json()["id"]
    cli.post(f"/api/seg-leads/recordatorios/{rid}/hecho",
             json={"resultado": "No atendió", "proxima_fecha": HOY,
                   "proximo_motivo": "Mandar presupuesto", "proxima_nota": "llamar después de las 18"})
    _exec(db, "UPDATE seg_recordatorios SET fecha=? WHERE lead_id=? AND estado='pendiente'", (HACE_6, ana))
    _crear(cli, bruno, motivo="Confirmar demo")
    _crear(cli, carla, fecha=DOMINGO, hora="11:00", motivo="Seguimiento semana")
    _crear(cli, diego, fecha=PROX_LUNES, motivo="Volver a ofrecer")
    return ana


@sin_node
def test_la_pantalla_se_pinta_con_lo_que_devuelve_el_servidor(cli, tmp_path):
    ana = _escenario(cli)
    estado = _pantalla(cli)
    ficha = cli.get(f"/api/seg-leads/lead/{ana}").get_json()
    id_hoy = estado["grupos"]["hoy"][0]["id"]
    id_vencido = estado["grupos"]["vencidos"][0]["id"]
    respuestas = {
        "/api/seg-leads": estado,
        f"/api/seg-leads/recordatorios/{id_hoy}/posponer": {"ok": True, "fecha": PROX_LUNES},
        f"/api/seg-leads/recordatorios/{id_vencido}/hecho": {"ok": True},
        "/api/seg-leads/recordatorios": {"ok": True, "id": 99, "cerrados": [id_vencido]},
        f"/api/seg-leads/lead/{ana}": ficha,
    }
    prueba = (_PRUEBA.replace("__ID_HOY__", str(id_hoy)).replace("__ID_VENCIDO__", str(id_vencido))
              .replace("__LEAD__", str(ana)).replace("__FICHA__", json.dumps(ficha, ensure_ascii=False))
              .replace("__ESTADO__", json.dumps(estado, ensure_ascii=False)))
    s = _correr_js(tmp_path, respuestas, prueba)

    # encabezado y contadores
    assert s["resumen"] == "4 llamados pendientes · 1 vencido"
    assert re.findall(r'sl-contador-num">(\d+)<', s["contadores"]) == ["1", "1", "1", "1"]
    assert re.findall(r'sl-contador-rot">([^<]+)<', s["contadores"]) == [
        "Vencidos", "Hoy", "Esta semana", "Más adelante"]
    assert s["contadores"].count("sl-contador-vencidos") == 1
    assert s["scroll"] == "hoy"

    # los cuatro grupos, en orden y con su encabezado
    lista = s["lista"]
    titulos = [lista.index(t) for t in (">VENCIDOS<", ">HOY · LUNES 14/09<", ">ESTA SEMANA<", ">MÁS ADELANTE<")]
    assert titulos == sorted(titulos)
    assert 'class="sl-grupo-titulo sl-grupo-titulo-vencidos">VENCIDOS<' in lista

    def grupo(clave):
        return _entre(lista, f'id="sl-grupo-{clave}"', "</section>")

    # criterio 3: vencida, con borde rojo y "hace 6 días" en rojo
    vencidos = grupo("vencidos")
    assert re.findall(r'<article class="([^"]*)"', lista) == ["sl-tarjeta sl-vencida", "sl-tarjeta"]
    assert '<div class="sl-cuando sl-cuando-vencido">hace 6 días</div>' in vencidos
    assert "Ana Pérez" in vencidos and "Peluquería" in vencidos
    assert '<span class="sl-ultima">Última vez: No atendió.</span> Mandar presupuesto' in vencidos
    assert '<div class="sl-nota">llamar después de las 18</div>' in vencidos
    assert 'href="tel:+59899123456"' in vencidos
    assert 'href="https://wa.me/59899123456"' in vencidos
    for boton in (">+1 semana</button>", ">+1 mes</button>", ">Hecho</button>"):
        assert boton in vencidos, boton

    # criterio 8: sin teléfono, los dos botones deshabilitados
    hoy = grupo("hoy")
    assert re.search(r'<button type="button" class="sl-btn" disabled[^>]*>Llamar</button>', hoy)
    assert re.search(r'<button type="button" class="sl-btn" disabled[^>]*>WhatsApp</button>', hoy)
    assert "tel:" not in hoy and "wa.me" not in hoy
    assert '<div class="sl-cuando">sin hora</div>' in hoy

    # esta semana y más adelante: una línea, sin botones de acción
    semana, despues = grupo("semana"), grupo("despues")
    for texto in (semana, despues):
        assert 'class="sl-linea"' in texto and "sl-acciones" not in texto and "sl-btn" not in texto
    assert '<div class="sl-linea-motivo">Seguimiento semana</div>' in semana
    assert '<div class="sl-linea-fecha">dom 20/09 · 11:00</div>' in semana
    assert '<div class="sl-linea-fecha">lun 21/09</div>' in despues

    # criterio 4: posponer no abre ningún formulario
    assert s["posponer"][0] == [f"/api/seg-leads/recordatorios/{id_hoy}/posponer", "POST"]
    assert ["/api/seg-leads", "GET"] in s["posponer"]
    assert s["abiertosTrasPosponer"] == 0

    # criterio 5: Hecho pide las dos cosas antes de mandar nada
    assert s["abiertos"] == ["sl-modal-hecho"]
    assert s["motivoPrecargado"] == "Mandar presupuesto"
    assert s["notaPrecargada"] == "llamar después de las 18"
    assert "cuándo volvés a llamar" in s["errorSinFecha"] and "no hace falta" in s["errorSinFecha"]
    assert "qué pasó" in s["errorSinResultado"]
    assert s["postsInvalidos"] == 0
    assert s["rapidoMes"] == "2026-10-14" and s["sinVolverTrasRapido"] is False
    assert s["proximoOculto"] is True
    assert s["postHecho"][0] == [f"/api/seg-leads/recordatorios/{id_vencido}/hecho", "POST"]

    # nuevo recordatorio desde la ficha
    assert s["nuevoLead"] == "Ana Pérez" and s["nuevoFecha"] == HOY
    assert "Ya tiene un recordatorio abierto" in s["aviso"] and "se cierra solo" in s["aviso"]
    assert "motivo" in s["errorNuevoSinMotivo"]
    assert s["postNuevo"][0] == ["/api/seg-leads/recordatorios", "POST"]
    assert "lead" in s["errorNuevoSinLead"]
    assert "al menos 2 letras" in s["buscador"]

    # el botón en Proceso de venta
    assert "Recordatorio de llamado" in s["notion"] and f'data-lead="{ana}"' in s["notion"]
    assert 'data-buscar="Otra ficha"' in s["notionSinConectar"]
    assert s["abiertosNotion"] == ["sl-modal-nuevo"] and s["buscarNotion"] == "Otra ficha"

    # el historial en la ficha del lead, junto a lo que ya había
    assert "Historial de llamados" in s["ficha"] and "No atendió" in s["ficha"]
    assert "lun 14/09/2026 · 10:30" in s["ficha"]
    assert "sl-ficha-pendiente sl-vencida" in s["ficha"] and "Nuevo recordatorio" in s["ficha"]
    assert "Registrar llamada" in s["ficha"]
    assert s["fichaSinPermiso"] == ""

    # un grupo vacío no se muestra
    assert "HOY · LUNES 14/09" in s["soloHoy"]
    assert "VENCIDOS" not in s["soloHoy"] and "ESTA SEMANA" not in s["soloHoy"]
    assert "No hay llamados pendientes" in s["nada"]


@sin_node
def test_si_el_servidor_falla_la_pantalla_lo_dice(tmp_path):
    prueba = """
(async () => {
  await loadSegLeads();
  console.log(JSON.stringify({lista: _el('sl-lista').innerHTML, resumen: _el('sl-resumen').textContent}));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
"""
    s = _correr_js(tmp_path, {"/api/seg-leads": "falla"}, prueba)
    assert "No se pudo cargar el seguimiento" in s["lista"]
    assert s["resumen"] == ""
