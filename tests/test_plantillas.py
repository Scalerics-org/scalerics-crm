"""Plantillas de mensajes en VENTAS (pedido de Juan, 14/9).

"En la seccion ventas, agrega algo que sea Plantilla, son mensajes que uso
siempre y los quiero tener a mano". Vienen precargadas las cinco del PDF
"Plantillas de mensajes - Scalerics", con el texto exacto, y una sexta que
pidió después: la del lead que no atendió.
"""

import json
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import (create_budget, create_user, init_db, listar_plantillas,
                      upsert_client_info)
from services.plantillas import buscar_leads, validar_plantilla, variables_del_lead

HTML = dashboard.DASHBOARD_HTML
RAIZ = Path(__file__).resolve().parents[1]
SRC = (RAIZ / "dashboard.py").read_text(encoding="utf-8")

sin_node = pytest.mark.skipif(shutil.which("node") is None,
                              reason="node no esta instalado")


def _entre(texto: str, desde: str, hasta: str) -> str:
    i = texto.index(desde)
    return texto[i:texto.index(hasta, i + len(desde))]


PANEL = _entre(SRC, "<!-- ======= PLANTILLAS PANEL ======= -->", "<!-- ======= FIN PLANTILLAS PANEL ======= -->")
MODALES = _entre(SRC, "<!-- ======= PLANTILLAS MODALES ======= -->", "<!-- ======= FIN PLANTILLAS MODALES ======= -->")
JS = _entre(SRC, "// ========== Plantillas ==========", "// ========== Seguimiento de leads ==========")
CSS = _entre(SRC, "/* ── Plantillas", "/* ── Daily Programador")
FUENTES = {"panel": PANEL, "modales": MODALES, "js": JS, "css": CSS}

# Copiado a mano del PDF, a propósito sin importar la precarga: si alguien
# "corrige" un texto en database.py, esto tiene que fallar.
PDF = [
    ("LLAMÉ Y NO ATENDIÓ", "Lead que no atendió", "WhatsApp",
     "¿Cómo estás {nombre}? Te escribe Juan de Scalerics. Respondiste un formulario solicitando "
     "información acerca de {servicio}. Te llamé para que me cuentes un poco y ver cómo te podemos "
     "ayudar en lo que estás buscando. Cuando tengas unos minutos avisame y te llamo. Saludos.",
     "Se manda después de llamar sin respuesta.", "", 0),
    ("DESPUÉS DE LA PRIMERA LLAMADA", "Confirmación de agenda", "WhatsApp",
     "¿Cómo estás {nombre}? Te habla Juan Pereyra de Scalerics.\n\n"
     "Quedamos agendados para el {fecha} a las {hora}. Entrás a la videollamada con el siguiente link: {link}\n\n"
     "El mismo día, un rato antes, te mando recordatorio de la videollamada. En lo posible confirmame con un okey.\n\n"
     "Saludos.",
     "Se manda apenas queda agendada la demo.", "", 0),
    ("EL DÍA DE LA DEMO", "Recordatorio de videollamada", "WhatsApp",
     "¿Cómo estás {nombre}? Este es un recordatorio para la videollamada de hoy a las {hora}.\n\n"
     "Entrás con el link que te pasé arriba.\n\n"
     "Saludos.",
     "Automático: se dispara unas horas antes de la demo, sin que lo mandes vos.", "", 1),
    ("DESPUÉS DE LA DEMO", "Resumen y presupuesto", "WhatsApp o mail",
     "Hola {nombre}, gracias por el rato de hoy.\n\n"
     "Te dejo el presupuesto de la {servicio} como quedamos: {monto}, entrega en {plazo} desde que arrancamos.\n\n"
     "Cualquier duda escribime. Si querés avanzar, con confirmarme por acá alcanza.",
     "Adjunta el PDF del presupuesto.", "", 0),
    ("LEAD FRÍO", "Reactivación", "WhatsApp",
     "¿Cómo estás {nombre}? Avisame si al final seguís interesado en avanzar con el {servicio}.\n\n"
     "Saludos.",
     "", "", 0),
    ("LEAD FRÍO", "Alternativa para el de reactivación", "WhatsApp",
     "¿Cómo estás {nombre}? Te escribo por el {servicio} que habíamos charlado. "
     "¿Lo dejamos para más adelante o lo retomamos?",
     "",
     "Preguntar si sigue interesado obliga al otro a decidir, y lo más fácil es no contestar. "
     "Esta versión ofrece dos salidas y las dos sirven: incluso el “más adelante” deja una fecha "
     "para volver a llamar.", 0),
]
VARIABLES = ["nombre", "empresa", "servicio", "monto", "plazo", "fecha", "hora", "link",
             "saldo", "vencimiento"]


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    db = str(tmp_path / "plantillas.db")
    init_db(db)
    a = dashboard.create_app(db)
    a.config["_DB"] = db
    return a


def _sql(db, sql, params=()):
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _uno(db, sql, params=()):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _rol(db, nombre, paneles):
    return _sql(db, "INSERT INTO roles (name, panel_access) VALUES (?,?)", (nombre, json.dumps(paneles)))


def _usuario(db, email, role_id=None):
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    if role_id is not None:
        _sql(db, "UPDATE users SET role_id=? WHERE id=?", (role_id, uid))
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
    return _cli(app, _usuario(app.config["_DB"], "jefe@scalerics.com"))


def _lead(db, nombre="Panadería Ana", telefono="+59899123456", **extra):
    cols = {"name": nombre, "phone": telefono, "crm_status": "demo_agendada", **extra}
    return _sql(db, f"INSERT INTO businesses ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                tuple(cols.values()))


def _lead_completo(db):
    """Un lead con todas las fuentes cargadas, menos el plazo."""
    lid = _lead(db)
    upsert_client_info(db, lid, lead_name="ana maría", business_name="Panadería Ana SRL",
                       rubro="Página web", meeting_time="2030-02-01 10:00",
                       meeting_url="https://bot.example/link-viejo")
    _sql(db, "INSERT INTO meetings (client_id, start_at, meet_link, status) VALUES (?,?,?,?)",
         (lid, "2020-03-02T09:00:00-03:00", "https://meet.google.com/pasada", "scheduled"))
    _sql(db, "INSERT INTO meetings (client_id, start_at, meet_link, status) VALUES (?,?,?,?)",
         (lid, "2030-01-17T15:00:00-03:00", "https://meet.google.com/abc-defg-hij", "scheduled"))
    _sql(db, "INSERT INTO meetings (client_id, start_at, meet_link, status) VALUES (?,?,?,?)",
         (lid, "2029-12-01T11:00:00-03:00", "https://meet.google.com/cancelada", "canceled"))
    create_budget(db, lid, total_amount=1500)
    _sql(db, "INSERT INTO finanzas_por_cobrar (client_id, concepto, monto_usd, vence) VALUES (?,?,?,?)",
         (lid, "Segunda cuota", 500, "2030-03-10"))
    _sql(db, "INSERT INTO finanzas_por_cobrar (client_id, concepto, monto_usd, vence) VALUES (?,?,?,?)",
         (lid, "Tercera cuota", 250.5, "2030-02-10"))
    return lid


AHORA = datetime(2026, 9, 14, 12, 0)


# ── precarga ─────────────────────────────────────────────────────────────────

def test_la_precarga_trae_las_seis_con_el_texto_exacto(tmp_path):
    db = str(tmp_path / "p.db")
    init_db(db)
    plantillas = listar_plantillas(db)
    obtenido = [(p["momento"], p["titulo"], p["canal"], p["cuerpo"], p["nota"], p["explicacion"],
                 p["automatica"]) for p in plantillas]
    assert obtenido == PDF


def test_la_precarga_no_duplica(tmp_path):
    db = str(tmp_path / "p.db")
    init_db(db)
    init_db(db)
    init_db(db)
    assert len(listar_plantillas(db)) == 6
    assert _uno(db, "SELECT COUNT(*) FROM plantillas_mensajes")[0] == 6


def test_la_precarga_no_pisa_lo_que_juan_edita_ni_resucita_lo_borrado(tmp_path):
    db = str(tmp_path / "p.db")
    init_db(db)
    _sql(db, "UPDATE plantillas_mensajes SET cuerpo='Hola {nombre}, a mi manera.' WHERE clave='reactivacion'")
    _sql(db, "UPDATE plantillas_mensajes SET borrada=1 WHERE clave='resumen_presupuesto'")
    init_db(db)
    por_titulo = {p["titulo"]: p for p in listar_plantillas(db)}
    assert por_titulo["Reactivación"]["cuerpo"] == "Hola {nombre}, a mi manera."
    assert "Resumen y presupuesto" not in por_titulo
    assert len(por_titulo) == 5


def test_una_base_con_las_cinco_viejas_suma_solo_la_nueva(tmp_path):
    """Producción ya va a tener las cinco del PDF (editadas o no) cuando llegue
    la sexta: se agrega sola, primera, sin duplicar ni pisar."""
    db = str(tmp_path / "p.db")
    init_db(db)
    _sql(db, "DELETE FROM plantillas_mensajes WHERE clave='no_atendio'")
    _sql(db, "UPDATE plantillas_mensajes SET titulo='Confirmación (mía)' WHERE clave='confirmacion_agenda'")
    assert len(listar_plantillas(db)) == 5
    init_db(db)
    init_db(db)
    plantillas = listar_plantillas(db)
    assert len(plantillas) == 6
    assert plantillas[0]["titulo"] == "Lead que no atendió"
    assert plantillas[0]["momento"] == "LLAMÉ Y NO ATENDIÓ"
    assert plantillas[1]["titulo"] == "Confirmación (mía)"
    assert _uno(db, "SELECT COUNT(*) FROM plantillas_mensajes WHERE clave='no_atendio'")[0] == 1


def test_no_toca_las_plantillas_viejas_de_whatsapp(app, cli):
    """`wa_templates` son los atajos del chat: no reciben la precarga."""
    assert _uno(app.config["_DB"], "SELECT COUNT(*) FROM wa_templates")[0] == 0
    assert cli.get("/api/wa/templates").get_json() == []
    assert cli.post("/api/wa/templates", json={"name": "Hola", "body": "Hola!"}).status_code == 201
    assert [t["name"] for t in cli.get("/api/wa/templates").get_json()] == ["Hola"]
    assert len(cli.get("/api/plantillas").get_json()["plantillas"]) == 6


# ── roles ────────────────────────────────────────────────────────────────────

def test_el_panel_les_llega_a_los_roles_que_venden(tmp_path):
    db = str(tmp_path / "p.db")
    init_db(db)
    _rol(db, "SoloWA", ["wa"])
    _rol(db, "SoloProceso", ["notion_clients"])
    _rol(db, "SoloFinanzas", ["finanzas", "cal"])
    _sql(db, "INSERT INTO roles (name, panel_access) VALUES ('Roto', 'no es json')")
    init_db(db)
    init_db(db)
    acceso = {n: a for n, a in sqlite3.connect(db).execute("SELECT name, panel_access FROM roles")}
    assert json.loads(acceso["SoloWA"]).count("plantillas") == 1
    assert "plantillas" in json.loads(acceso["SoloProceso"])
    assert "plantillas" not in json.loads(acceso["SoloFinanzas"])
    assert acceso["Roto"] == "no es json"
    # Los de fábrica: Admin y Caller tienen wa; Ventas no tiene ninguno de los dos.
    assert "plantillas" in json.loads(acceso["Admin"])
    assert "plantillas" in json.loads(acceso["Caller"])
    assert "plantillas" not in json.loads(acceso["Ventas"])


# ── API ──────────────────────────────────────────────────────────────────────

def test_lista_las_plantillas_y_las_variables(cli):
    j = cli.get("/api/plantillas").get_json()
    assert [p["titulo"] for p in j["plantillas"]] == [t for _m, t, *_ in PDF]
    assert j["variables"] == VARIABLES


def test_crear_editar_y_borrar(cli):
    r = cli.post("/api/plantillas", json={"momento": "Después de la demo", "titulo": "Seguimiento",
                                          "canal": "Mail", "cuerpo": "  Hola {nombre}\r\n\r\nSaludos.  ",
                                          "nota": "", "automatica": False})
    assert r.status_code == 201, r.get_json()
    pid = r.get_json()["id"]
    nueva = next(p for p in cli.get("/api/plantillas").get_json()["plantillas"] if p["id"] == pid)
    assert nueva["cuerpo"] == "Hola {nombre}\n\nSaludos."
    assert nueva["orden"] > 50, "una nueva va al final"

    r = cli.put(f"/api/plantillas/{pid}", json={"titulo": "Seguimiento 2", "cuerpo": "Chau {nombre}",
                                                "automatica": True})
    assert r.status_code == 200
    editada = next(p for p in cli.get("/api/plantillas").get_json()["plantillas"] if p["id"] == pid)
    assert (editada["titulo"], editada["cuerpo"], editada["automatica"]) == ("Seguimiento 2", "Chau {nombre}", 1)

    assert cli.delete(f"/api/plantillas/{pid}").status_code == 200
    assert pid not in [p["id"] for p in cli.get("/api/plantillas").get_json()["plantillas"]]
    assert cli.delete(f"/api/plantillas/{pid}").status_code == 404
    assert cli.put(f"/api/plantillas/{pid}", json={"titulo": "x", "cuerpo": "y"}).status_code == 404
    acciones = [a for (a,) in sqlite3.connect(cli.application.config["_DB"]).execute(
        "SELECT action FROM activity_log WHERE action LIKE 'plantilla_%' ORDER BY id")]
    assert acciones == ["plantilla_creada", "plantilla_editada", "plantilla_borrada"]


@pytest.mark.parametrize("datos,parte", [
    ({"cuerpo": "Hola"}, "título"),
    ({"titulo": "  ", "cuerpo": "Hola"}, "título"),
    ({"titulo": "T"}, "texto"),
    ({"titulo": "T", "cuerpo": "x" * 4001}, "largo"),
    ({"titulo": 5, "cuerpo": "Hola"}, "texto"),
])
def test_la_plantilla_se_valida_en_el_servidor(cli, datos, parte):
    r = cli.post("/api/plantillas", json=datos)
    assert r.status_code == 400 and parte in r.get_json()["error"]
    pid = cli.get("/api/plantillas").get_json()["plantillas"][0]["id"]
    assert cli.put(f"/api/plantillas/{pid}", json=datos).status_code == 400
    assert len(cli.get("/api/plantillas").get_json()["plantillas"]) == 6


def test_validar_sin_json():
    assert validar_plantilla(None) == (None, "Faltan los datos de la plantilla.")


def test_sin_el_panel_da_403(app):
    db = app.config["_DB"]
    c = _cli(app, _usuario(db, "caller@scalerics.com", _rol(db, "SoloWA2", ["wa"])))
    lid = _lead(db)
    assert c.get("/api/plantillas").status_code == 403
    assert c.post("/api/plantillas", json={"titulo": "a", "cuerpo": "b"}).status_code == 403
    assert c.put("/api/plantillas/1", json={"titulo": "a", "cuerpo": "b"}).status_code == 403
    assert c.delete("/api/plantillas/1").status_code == 403
    assert c.get("/api/plantillas/leads?q=pan").status_code == 403
    assert c.get(f"/api/plantillas/leads/{lid}/variables").status_code == 403
    assert len(listar_plantillas(db)) == 6


def test_con_el_panel_entra(app):
    db = app.config["_DB"]
    c = _cli(app, _usuario(db, "ventas@scalerics.com", _rol(db, "Vende", ["plantillas"])))
    assert c.get("/api/plantillas").status_code == 200


def test_sin_sesion_no_entra(app):
    assert app.test_client().get("/api/plantillas").status_code in (401, 302)


# ── variables ────────────────────────────────────────────────────────────────

def test_las_variables_salen_de_los_datos_del_lead(tmp_path):
    db = str(tmp_path / "p.db")
    init_db(db)
    lid = _lead_completo(db)
    d = variables_del_lead(db, lid, con_finanzas=True, ahora=AHORA)
    assert d["lead"] == {"id": lid, "nombre": "Panadería Ana", "telefono": "+59899123456"}
    assert d["valores"] == {
        "nombre": "Ana", "empresa": "Panadería Ana SRL", "servicio": "página web",
        "monto": "USD 1.500", "plazo": "", "fecha": "jueves 17/01", "hora": "15:00",
        "link": "https://meet.google.com/abc-defg-hij", "saldo": "USD 750,50",
        "vencimiento": "10/02/2030"}
    assert d["faltan"] == ["plazo"]
    assert d["fuentes"]["fecha"] == "Reunión del calendario del CRM (17/01 15:00)"
    assert "a mano" in d["fuentes"]["plazo"]


def test_sin_finanzas_no_se_ven_saldo_ni_vencimiento(tmp_path):
    db = str(tmp_path / "p.db")
    init_db(db)
    d = variables_del_lead(db, _lead_completo(db), con_finanzas=False, ahora=AHORA)
    assert d["valores"]["saldo"] == "" and d["valores"]["vencimiento"] == ""
    assert d["fuentes"]["saldo"] == "Solo se completa con acceso a Finanzas"


def test_si_ya_pasaron_todas_las_reuniones_toma_la_ultima(tmp_path):
    db = str(tmp_path / "p.db")
    init_db(db)
    lid = _lead_completo(db)
    d = variables_del_lead(db, lid, ahora=datetime(2031, 1, 1))
    assert (d["valores"]["fecha"], d["valores"]["link"]) == ("jueves 17/01", "https://meet.google.com/abc-defg-hij")


def test_un_lead_de_meta_usa_su_nombre_y_los_respaldos(tmp_path):
    db = str(tmp_path / "p.db")
    init_db(db)
    lid = _lead(db, nombre="lucía gómez", telefono="099 555 444", source="meta",
                interest="Automatización", monto_pagado=45000, moneda_pagado="UYU")
    upsert_client_info(db, lid, meeting_time="2030-02-01T10:30:00", meeting_url="https://bot.example/x")
    d = variables_del_lead(db, lid, con_finanzas=True, ahora=AHORA)
    v = d["valores"]
    assert v["nombre"] == "Lucía", "de Meta, el nombre del lead es la persona"
    assert v["empresa"] == "", "y no hay nombre de empresa"
    assert v["servicio"] == "automatización"
    assert v["monto"] == "UYU 45.000"
    assert (v["fecha"], v["hora"], v["link"]) == ("viernes 01/02", "10:30", "https://bot.example/x")
    assert v["saldo"] == "" and "a mano" in d["fuentes"]["saldo"]
    assert set(d["faltan"]) == {"empresa", "plazo", "saldo", "vencimiento"}


def test_un_lead_sin_nada(tmp_path):
    db = str(tmp_path / "p.db")
    init_db(db)
    d = variables_del_lead(db, _lead(db, nombre="Ferretería X", telefono=None), ahora=AHORA)
    assert d["valores"]["empresa"] == "Ferretería X" and d["valores"]["nombre"] == ""
    assert d["lead"]["telefono"] == ""
    assert variables_del_lead(db, 9999) is None


def test_la_api_de_variables_respeta_finanzas(app):
    db = app.config["_DB"]
    lid = _lead_completo(db)
    sin = _cli(app, _usuario(db, "v1@scalerics.com", _rol(db, "Vende", ["plantillas"])))
    con = _cli(app, _usuario(db, "v2@scalerics.com", _rol(db, "VendeYCobra", ["plantillas", "finanzas"])))
    assert sin.get(f"/api/plantillas/leads/{lid}/variables").get_json()["valores"]["saldo"] == ""
    j = con.get(f"/api/plantillas/leads/{lid}/variables").get_json()
    assert j["valores"]["saldo"] == "USD 750,50" and j["valores"]["nombre"] == "Ana"
    assert con.get("/api/plantillas/leads/9999/variables").status_code == 404


def test_el_buscador_encuentra_por_nombre_contacto_o_telefono(app, cli):
    db = app.config["_DB"]
    lid = _lead_completo(db)
    otro = _lead(db, nombre="Bar Tito", telefono="2901 1234")
    assert [l["id"] for l in buscar_leads(db, "panader")] == [lid]
    assert [l["id"] for l in buscar_leads(db, "ANA MAR")] == [lid]
    assert [l["id"] for l in buscar_leads(db, "099 123 456")] == [lid]
    assert [l["id"] for l in buscar_leads(db, "29011234")] == [otro]
    assert buscar_leads(db, "a") == []
    j = cli.get("/api/plantillas/leads?q=tito").get_json()
    assert j["leads"][0]["name"] == "Bar Tito" and j["leads"][0]["phone"] == "2901 1234"


# ── la página ────────────────────────────────────────────────────────────────

def test_get_raiz_abre_con_el_panel(cli):
    r = cli.get("/")
    assert r.status_code == 200
    pagina = r.get_data(as_text=True)
    assert pagina.count('id="plantillas-panel"') == 1
    assert pagina.count('id="nav-plantillas"') == 1
    assert "if (name === 'plantillas') plCargar();" in pagina


def test_esta_registrado_en_todos_lados():
    ventas = _entre(HTML, '<div class="nav-section-label">VENTAS</div>',
                    '<div class="nav-section-label">OPERACIÓN</div>')
    assert re.findall(r'id="nav-(\w+)"', ventas)[-1] == "plantillas"
    prioridad = re.findall(r"'(\w+)'", re.search(r"const NAV_PRIORITY = \[([^\]]*)\]", HTML).group(1))
    assert prioridad.index("plantillas") == prioridad.index("notion_clients") + 1
    assert "plantillas:'message-square-text'" in re.search(r"const NAV_ICONS = \{(.*?)\n\}", HTML, re.S).group(1)
    assert "plantillas:'Plantillas'" in re.search(r"const NAV_LABELS = \{(.*?)\n\}", HTML, re.S).group(1)
    listas = re.findall(r"const ALL_PANELS = \[([^\]]*)\]", SRC)
    assert len(listas) == 2 and all("'plantillas'" in l for l in listas)
    assert "plantillas:'Plantillas'" in re.search(r"const PANEL_LABELS = \{([^}]*)\}", SRC).group(1)
    assert "from routes.plantillas import plantillas_bp" in SRC
    assert "plantillas_bp" in re.search(r"for bp in \((.*?)\):", SRC, re.S).group(1)
    assert re.search(r"#nav-plantillas \.nav-icon\{stroke:#", HTML)
    assert re.search(r"body\.light #nav-plantillas \.nav-icon\{stroke:#", HTML)
    fuente_db = (RAIZ / "database.py").read_text(encoding="utf-8")
    assert '_grant_panel_to_existing_roles(conn, "plantillas",' in fuente_db


@pytest.mark.parametrize("nombre", sorted(FUENTES))
def test_nada_que_jinja_o_python_interpreten(nombre):
    texto = FUENTES[nombre]
    for trampa in ("{#", "{{", "{%", "}}"):
        assert trampa not in texto, f"{trampa!r} en el {nombre} de Plantillas"
    assert chr(92) not in texto, f"un backslash en el {nombre}: Python se lo come"
    if nombre == "js":
        assert "`" not in texto, "template literal en el JS de Plantillas"


def test_las_plantillas_no_estan_escritas_en_el_html():
    """Llegan por la API: el texto del PDF no vive en DASHBOARD_HTML."""
    for _m, titulo, _c, cuerpo, *_ in PDF:
        assert cuerpo.split("\n")[0] not in HTML


def test_el_css_usa_solo_tokens_y_anda_en_el_celular():
    reglas = re.findall(r"([^{}]+)\{([^{}]*)\}", CSS)
    assert len(reglas) > 30
    for selector, cuerpo in reglas:
        assert not re.search(r"#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])|rgba?\(", cuerpo), selector.strip()
    assert "body.light" not in CSS.split("*/", 1)[1]
    assert re.search(r"\.pl-var\{color:var\(--azul-claro\)", CSS)
    assert re.search(r"@media\(max-width:768px\)\{ \.pl-grilla\{grid-template-columns:1fr\}", CSS)
    for nombre in ("panel", "modales", "js"):
        assert "style=" not in FUENTES[nombre], f"estilo inline en el {nombre} de Plantillas"


def test_todo_lo_del_js_lleva_el_prefijo_pl():
    nombres = re.findall(r"^(?:async )?function ([A-Za-z_$][\w$]*)\(", JS, re.M)
    nombres += re.findall(r"^(?:const|let) ([A-Za-z_$][\w$]*)", JS, re.M)
    assert len(nombres) > 30
    sueltos = [n for n in nombres if not (n.startswith("pl") or n.startswith("PL_"))]
    assert not sueltos, f"sin prefijo: {sueltos}"
    for nombre in nombres:
        declaraciones = re.findall(r"^(?:async )?(?:function|const|let) " + re.escape(nombre) + r"\b",
                                   HTML, re.M)
        assert len(declaraciones) == 1, f"{nombre} está declarado {len(declaraciones)} veces"


def test_enviar_usa_el_endpoint_existente_y_pide_confirmacion():
    assert "fetch('/api/wa/send'" in JS
    envio = _entre(JS, "async function plEnviar()", "// ── editar")
    assert "phone: plLead.lead.telefono" in envio and "text: plCompletar(" in envio
    assert "plAbrirModal('pl-modal-confirmar')" in _entre(JS, "function plPedirEnvio()", "async function plEnviar()")
    assert 'onclick="plEnviar()"' in MODALES and 'onclick="plPedirEnvio()"' in MODALES


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
const _copiado = [];
Object.defineProperty(globalThis, 'navigator', { configurable: true,
  value: { clipboard: { writeText: t => { _copiado.push(t); return Promise.resolve(); } } } });
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
    archivo = tmp_path / "plantillas.js"
    archivo.write_text(_ARNES.replace("__RESPUESTAS__", json.dumps(respuestas, ensure_ascii=False))
                       + "\n".join(bloques) + "\n" + prueba, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, (r.stderr or "")[:2000]
    return json.loads(r.stdout.strip().splitlines()[-1])


@sin_node
def test_la_pantalla_se_pinta_agrupada_por_momento(cli, tmp_path):
    lista = cli.get("/api/plantillas").get_json()
    prueba = """
(async () => {
  await plCargar();
  console.log(JSON.stringify({lista: _el('pl-lista').innerHTML, variables: _el('pl-variables').innerHTML,
    trozos: plTrozos('a {nombre} {no vale} {Nombre} {x'),
    completo: plCompletar('Hola {nombre}, {plazo}.', {nombre: 'Ana', plazo: '  '}),
    vacio: plListaHtml([])}));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
"""
    s = _correr_js(tmp_path, {"/api/plantillas": lista}, prueba)
    html = s["lista"]
    momentos = re.findall(r'<h2 class="pl-momento">([^<]+)</h2>', html)
    assert momentos == ["LLAMÉ Y NO ATENDIÓ", "DESPUÉS DE LA PRIMERA LLAMADA", "EL DÍA DE LA DEMO",
                        "DESPUÉS DE LA DEMO", "LEAD FRÍO"]
    assert html.count('<article class="pl-card"') == 6
    frio = html[html.index("LEAD FRÍO"):]
    assert frio.count('<article class="pl-card"') == 2
    assert '<span class="pl-var">{nombre}</span>' in html
    assert html.count('class="pl-var"') == sum(c.count("{") for _m, _t, _c, c, *_ in PDF)
    assert '<p class="pl-nota">Adjunta el PDF del presupuesto.</p>' in html
    assert "“más adelante”" in html and 'class="pl-explicacion"' in html
    assert html.count("La manda el bot sola") == 1
    recordatorio = html[html.index("Recordatorio de videollamada"):html.index("DESPUÉS DE LA DEMO")]
    assert "La manda el bot sola" in recordatorio
    assert html.count(">Usar con un lead<") == 6 and html.count(">Borrar<") == 6
    assert '<p class="pl-nota">Se manda después de llamar sin respuesta.</p>' in html
    assert "Te habla Juan Pereyra de Scalerics." in html
    assert s["variables"].count('class="pl-var"') == 10
    assert [t["tipo"] for t in s["trozos"]] == ["texto", "var", "texto"]
    assert s["trozos"][2]["valor"] == " {no vale} {Nombre} {x"
    assert s["completo"] == "Hola Ana, {plazo}."
    assert "No hay plantillas" in s["vacio"]


@sin_node
def test_copiar_usar_con_un_lead_y_enviar(app, cli, tmp_path):
    db = app.config["_DB"]
    lid = _lead_completo(db)
    lista = cli.get("/api/plantillas").get_json()
    por_titulo = {p["titulo"]: p["id"] for p in lista["plantillas"]}
    variables = cli.get(f"/api/plantillas/leads/{lid}/variables").get_json()
    leads = cli.get("/api/plantillas/leads?q=ana").get_json()
    prueba = """
(async () => {
  const s = {};
  await plCargar();
  await plCopiarPlantilla(__RESUMEN__, _el('boton'));
  s.copiado = _copiado.slice();
  s.aviso = _el('pl-aviso').textContent;

  plAbrirUsar(__RESUMEN__);
  s.sinLead = _el('pl-elegido').innerHTML;
  plPedirEnvio();
  s.errorSinLead = _el('pl-usar-error').textContent;

  _el('pl-buscar').value = 'ana';
  await plBuscarAhora();
  s.resultados = _el('pl-resultados').innerHTML;
  await plElegirLead(__LEAD__);
  s.elegido = _el('pl-elegido').innerHTML;
  s.campos = _el('pl-campos').innerHTML;
  s.vista = _el('pl-vista').innerHTML;
  s.faltan = _el('pl-faltan').textContent;
  s.enviarOculto = _el('pl-btn-enviar').hidden;

  plPedirEnvio();
  s.errorFalta = _el('pl-usar-error').textContent;
  s.sendsAntes = _pedidos.filter(p => p[0] === '/api/wa/send').length;

  plEditarValor({getAttribute: () => 'plazo', value: '4 semanas', closest: () => null});
  s.vistaCompleta = _el('pl-vista').innerHTML;
  await plCopiarVista(null);
  s.copiadoVista = _copiado[_copiado.length - 1];
  plPedirEnvio();
  s.confPara = _el('pl-conf-para').innerHTML;
  s.confTexto = _el('pl-conf-texto').textContent;
  s.sendsAlConfirmar = _pedidos.filter(p => p[0] === '/api/wa/send').length;
  await plEnviar();
  s.send = _pedidos.filter(p => p[0] === '/api/wa/send');
  s.avisoEnvio = _el('pl-aviso').textContent;

  plAbrirUsar(__RECORDATORIO__);
  await plElegirLead(__LEAD__);
  s.autoEnviarOculto = _el('pl-btn-enviar').hidden;
  s.autoAvisoOculto = _el('pl-auto-aviso').hidden;
  plPedirEnvio();
  s.autoError = _el('pl-usar-error').textContent;
  s.sendsFinal = _pedidos.filter(p => p[0] === '/api/wa/send').length;

  plAbrirEditor(0);
  _el('pl-ed-titulo').value = '';
  _el('pl-ed-cuerpo').value = 'Hola';
  await plGuardar();
  s.errorEditor = _el('pl-ed-error').textContent;
  s.posts = _pedidos.filter(p => p[1] === 'POST' && p[0] === '/api/plantillas').length;
  plAbrirEditor(__RESUMEN__);
  s.editorCuerpo = _el('pl-ed-cuerpo').value;
  s.editorTitulo = _el('pl-ed-titulo-modal').textContent;
  await plGuardar();
  s.put = _pedidos.filter(p => p[1] === 'PUT');

  await plBorrar(__RESUMEN__);
  s.borrar = _pedidos.filter(p => p[1] === 'DELETE').map(p => p[0]);
  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""".replace("__RESUMEN__", str(por_titulo["Resumen y presupuesto"])) \
   .replace("__RECORDATORIO__", str(por_titulo["Recordatorio de videollamada"])) \
   .replace("__LEAD__", str(lid))
    resumen = por_titulo["Resumen y presupuesto"]
    s = _correr_js(tmp_path, {
        "/api/plantillas": lista,
        "/api/plantillas/leads?q=ana": leads,
        f"/api/plantillas/leads/{lid}/variables": variables,
        "/api/wa/send": {"ok": True},
        f"/api/plantillas/{resumen}": {"ok": True},
    }, prueba)

    cuerpo = PDF[3][3]
    assert s["copiado"][0] == cuerpo and s["aviso"] == "Copiado"
    assert "Elegí un lead" in s["sinLead"] and "Elegí un lead" in s["errorSinLead"]
    assert "Panadería Ana" in s["resultados"] and "+59899123456" in s["resultados"]
    assert "<b>Panadería Ana</b> · +59899123456" in s["elegido"]
    assert 'value="página web"' in s["campos"] and 'value="USD 1.500"' in s["campos"]
    assert s["campos"].count("pl-campo-falta") == 1 and "{plazo} · sin dato" in s["campos"]
    assert "Presupuesto del CRM" in s["campos"]
    assert '<span class="pl-var">Ana</span>' in s["vista"]
    assert '<span class="pl-var pl-var-falta"' in s["vista"] and "{plazo}" in s["vista"]
    assert s["faltan"] == "Falta completar: {plazo}"
    assert s["enviarOculto"] is False
    assert "{plazo}" in s["errorFalta"] and s["sendsAntes"] == 0

    esperado = ("Hola Ana, gracias por el rato de hoy.\n\n"
                "Te dejo el presupuesto de la página web como quedamos: USD 1.500, entrega en 4 semanas "
                "desde que arrancamos.\n\n"
                "Cualquier duda escribime. Si querés avanzar, con confirmarme por acá alcanza.")
    assert "pl-var-falta" not in s["vistaCompleta"]
    assert s["copiadoVista"] == esperado
    assert s["confPara"] == "<b>Panadería Ana</b> · +59899123456"
    assert s["confTexto"] == esperado
    assert s["sendsAlConfirmar"] == 0, "mostrar la confirmación no manda nada"
    assert len(s["send"]) == 1 and s["send"][0][1] == "POST"
    assert json.loads(s["send"][0][2]) == {"phone": "+59899123456", "text": esperado}
    assert s["avisoEnvio"] == "Enviado por WhatsApp a Panadería Ana."

    assert s["autoEnviarOculto"] is True and s["autoAvisoOculto"] is False
    assert "bot sola" in s["autoError"] and s["sendsFinal"] == 1

    assert s["errorEditor"] == "Falta el título." and s["posts"] == 0
    assert s["editorCuerpo"] == cuerpo and s["editorTitulo"] == "Editar plantilla"
    assert len(s["put"]) == 1 and s["put"][0][0] == f"/api/plantillas/{resumen}"
    assert json.loads(s["put"][0][2])["titulo"] == "Resumen y presupuesto"
    assert s["borrar"] == [f"/api/plantillas/{resumen}"]
