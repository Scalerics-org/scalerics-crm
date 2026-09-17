"""Panel LinkedIn en MARKETING, y Email marketing que pasa a MARKETING.

Pedido de Juan (15/9): "El email marketing mandalo a la seccion marketing, y
arma tambien en la seccion marketing abajo de meta, inteligencia marketing,
email marketing una mas que se llame linkedin. Y ahi todas las semanas arma dos
plantillas nuevas para linkedin, ya nos estan llegando al mail pero quiero que
esten ahi para subir a la pagina de scalerics de linkedin."

Nada acá llama a APIs externas: los borradores salen del banco ya escrito (no
hay IA) y el mail se reemplaza por uno falso.
"""

import base64
import json
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import dashboard
import database
import routes.linkedin_panel as rutas_li
import services.linkedin_borradores as lb
from database import create_linkedin_post, create_user, init_db, seed_linkedin_banco
from services.linkedin_posts import linkedin_job_handler

HTML = dashboard.DASHBOARD_HTML
RAIZ = Path(__file__).resolve().parents[1]
SRC = (RAIZ / "dashboard.py").read_text(encoding="utf-8")

sin_node = pytest.mark.skipif(shutil.which("node") is None, reason="node no esta instalado")


def _entre(texto, desde, hasta):
    i = texto.index(desde)
    return texto[i:texto.index(hasta, i + len(desde))]


PANEL = _entre(SRC, "<!-- ======= LINKEDIN PANEL ======= -->", "<!-- ======= FIN LINKEDIN PANEL ======= -->")
JS = _entre(SRC, "// ========== LinkedIn ==========", "// ========== FIN LinkedIn ==========")
CSS = _entre(SRC, "/* ── LinkedIn", "/* ── Equipo")
FUENTES = {"panel": PANEL, "js": JS, "css": CSS}
PROHIBIDAS = ("monto", "usd", "$", "precio", "pago", "factur", "sueldo")

# Martes 15/9/2026, 10:00 en Montevideo.
AHORA = datetime(2026, 9, 15, 13, 0, tzinfo=timezone.utc)
VIERNES = datetime(2026, 9, 18, 11, 0, tzinfo=timezone.utc)


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "li.db")
    init_db(ruta)
    seed_linkedin_banco(ruta)
    return ruta


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.setenv("ADMIN_TOKEN", "secreto")
    monkeypatch.setenv("LINKEDIN_MAIL_TO", "destino@test.com")
    monkeypatch.setattr(rutas_li, "_ahora", lambda: AHORA)
    ruta = str(tmp_path / "li-app.db")
    init_db(ruta)
    seed_linkedin_banco(ruta)
    a = dashboard.create_app(ruta)
    a.config["TESTING"] = True
    return a


def _cli(app, email, paneles=None):
    db_path = app.config["DB_PATH"]
    uid = create_user(db_path, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    if paneles is not None:
        conn = sqlite3.connect(db_path)
        rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)",
                           ("rol-" + email, json.dumps(paneles))).lastrowid
        conn.execute("UPDATE users SET role_id=? WHERE id=?", (rid, uid))
        conn.commit()
        conn.close()
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Juan"
    return c


@pytest.fixture
def jefe(app):
    return _cli(app, "jefe@scalerics.com")


def _sql(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql, params)
        filas = [dict(f) for f in cur.fetchall()]
        conn.commit()
        return filas
    finally:
        conn.close()


def _post(db_path, texto, tema="Planillas que nadie mira", token=None):
    return create_linkedin_post(
        db_path, job_id=None, lote="lote-prueba", tipo="educativo", texto=texto, angulo="concreto",
        fuente_tipo="banco", fuente_id=None, imagen_tipo="tarjeta", imagen_spec="{}",
        fuente_desc=f"Tema educativo: {tema}", aviso="", marcar_token=token)


def _borrador(db_path, texto, ahora=AHORA, tema="Planillas que nadie mira", token=None):
    pid = _post(db_path, texto, tema, token)
    lb.guardar_borradores(db_path, [{"id": pid, "texto": texto, "fuente_desc": f"Tema educativo: {tema}"}], ahora)
    return _sql(db_path, "SELECT id FROM linkedin_borradores WHERE post_id = ?", (pid,))[0]["id"], pid


TEXTO_1 = "Hola <b>equipo</b>.\n\nSegunda línea del post.\n#Automatizacion #Software"


# ── menú y registro ──────────────────────────────────────────────────────────

def _menu():
    return HTML[HTML.index('<div class="nav-scroll">'):HTML.index('<div class="sidebar-bottom">')]


def test_marketing_tiene_sus_paneles_en_orden_y_captacion_ya_no_tiene_email():
    marketing = _entre(_menu(), 'nav-section-label">MARKETING</div>', 'nav-section-label">FINANZAS</div>')
    items = re.findall(r'id="nav-(\w+)"[^>]*><i data-lucide="([\w-]+)" class="nav-icon"></i> ([^<]+?)(?: <span|</div>)',
                       marketing)
    assert [(p, texto.strip()) for p, _, texto in items] == [
        ("meta", "Meta Ads"), ("marketing", "Inteligencia marketing"),
        ("email_mkt", "Email marketing"), ("linkedin", "LinkedIn"),
        ("instagram", "Instagram"), ("sombra", "Recomendaciones de pauta")]
    assert dict((p, icono) for p, icono, _ in items)["linkedin"] == "linkedin"
    captacion = _menu()[_menu().index('nav-section-label">CAPTACIÓN'):]
    assert re.findall(r'id="nav-(\w+)"', captacion) == ["cola", "metrics", "sdr"]
    assert _menu().count('id="nav-email_mkt"') == 1


def test_esta_registrado_en_todos_lados():
    prioridad = re.findall(r"'(\w+)'", re.search(r"const NAV_PRIORITY = \[([^\]]*)\]", HTML).group(1))
    assert prioridad.index("email_mkt") == prioridad.index("meta") + 1
    assert prioridad.index("linkedin") == prioridad.index("email_mkt") + 1
    assert "linkedin:'linkedin'" in re.search(r"const NAV_ICONS = \{(.*?)\n\}", HTML, re.S).group(1)
    assert "linkedin:'LinkedIn'" in re.search(r"const NAV_LABELS = \{(.*?)\n\}", HTML, re.S).group(1)
    listas = re.findall(r"const ALL_PANELS = \[([^\]]*)\]", SRC)
    assert len(listas) == 2 and all("'linkedin'" in l and "'email_mkt'" in l for l in listas)
    assert "linkedin:'LinkedIn'" in re.search(r"const PANEL_LABELS = \{([^}]*)\}", SRC).group(1)
    assert "if (name === 'linkedin') loadLinkedin();" in HTML
    assert "from routes.linkedin_panel import linkedin_panel_bp" in SRC
    assert "linkedin_panel_bp" in re.search(r"for bp in \((.*?)\):", SRC, re.S).group(1)
    assert HTML.count('id="linkedin-panel"') == 1
    fuente_db = (RAIZ / "database.py").read_text(encoding="utf-8")
    assert '_grant_panel_to_existing_roles(conn, "linkedin", si_tiene=("marketing",))' in fuente_db


def test_el_icono_de_linkedin_tiene_color_propio_en_los_tres_casos():
    for patron in (r"^#nav-(\w+) \.nav-icon\{stroke:(#[0-9a-f]+)\}",
                   r"^#nav-(\w+)\.active \.nav-icon\{stroke:(#[0-9a-f]+)\}",
                   r"^body\.light #nav-(\w+) \.nav-icon\{stroke:(#[0-9a-f]+)\}"):
        colores = dict(re.findall(patron, HTML, re.M))
        propio = colores.pop("linkedin")
        assert propio not in colores.values(), (patron, propio)


# ── guardar al generar ───────────────────────────────────────────────────────

def test_al_generar_se_guardan_y_el_mail_sigue_saliendo(app, monkeypatch):
    db_path = app.config["DB_PATH"]
    monkeypatch.setattr(lb, "ahora_utc", lambda: AHORA)
    res = linkedin_job_handler({"db_path": db_path, "lote": "lote-martes", "ahora": "2026-09-15T08:00:00"})
    assert len(res["borradores"]) == 2, "hoy se arman dos por corrida"

    filas = _sql(db_path, "SELECT * FROM linkedin_borradores ORDER BY orden")
    assert [(f["semana"], f["orden"], f["estado"]) for f in filas] == [("2026-09-14", 1, "borrador"),
                                                                         ("2026-09-14", 2, "borrador")]
    for fila, borrador in zip(filas, res["borradores"]):
        assert fila["post_id"] == borrador["id"] and fila["texto"] == borrador["texto"] == fila["texto_original"]
        assert fila["tema"] == borrador["fuente_desc"].split(":", 1)[1].strip()
        assert fila["hashtags"] == lb.hashtags_de(borrador["texto"])

    mandados = []
    monkeypatch.setattr("routes.linkedin.send_linkedin_drafts",
                        lambda destino, borradores, base, aviso_cooldown=False: mandados.append(borradores) or True)
    r = app.test_client().post("/api/linkedin/enviar", json={"lote": "lote-martes"},
                               headers={"x-admin-token": "secreto"})
    assert r.status_code == 200 and len(mandados) == 1 and len(mandados[0]) == 2

    assert lb.guardar_borradores(db_path, res["borradores"], AHORA) == 0, "idempotente: no duplica"
    monkeypatch.setattr(lb, "ahora_utc", lambda: VIERNES)
    linkedin_job_handler({"db_path": db_path, "lote": "lote-viernes", "ahora": "2026-09-18T08:00:00"})
    filas = _sql(db_path, "SELECT semana, orden FROM linkedin_borradores ORDER BY orden")
    assert filas == [{"semana": "2026-09-14", "orden": n} for n in (1, 2, 3, 4)], \
        "martes y viernes: cuatro por semana, como hace hoy el cron"


# Un PNG chico de mentira: la firma de 8 bytes y un encabezado. Sin barras
# invertidas en los escapes, a proposito.
PNG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + bytes(3) + bytes([13]) + b"IHDR" + bytes([1]) * 40


def test_la_imagen_que_va_al_mail_queda_en_el_panel(app, jefe, monkeypatch):
    db_path = app.config["DB_PATH"]
    monkeypatch.setattr(lb, "ahora_utc", lambda: AHORA)
    res = linkedin_job_handler({"db_path": db_path, "lote": "lote-img", "ahora": "2026-09-15T08:00:00"})
    uno, dos = (b["id"] for b in res["borradores"])
    monkeypatch.setattr("routes.linkedin.send_linkedin_drafts", lambda *a, **k: True)
    imagenes = [{"id": uno, "png_b64": base64.b64encode(PNG).decode()},
                {"id": dos, "png_b64": base64.b64encode(b"<svg>no es png</svg>").decode()}]
    r = app.test_client().post("/api/linkedin/enviar", json={"lote": "lote-img", "imagenes": imagenes},
                               headers={"x-admin-token": "secreto"})
    assert r.status_code == 200

    lista = {b["post_id"]: b for b in jefe.get("/api/linkedin/borradores").get_json()["borradores"]}
    assert lista[uno]["tiene_imagen"] is True and lista[dos]["tiene_imagen"] is False, "solo un PNG de verdad"
    bid = lista[uno]["id"]
    r = jefe.get(f"/api/linkedin/borradores/{bid}/imagen")
    assert r.status_code == 200 and r.mimetype == "image/png" and r.data == PNG
    assert r.headers["X-Content-Type-Options"] == "nosniff" and "Content-Disposition" not in r.headers
    r = jefe.get(f"/api/linkedin/borradores/{bid}/imagen?descargar=1")
    assert r.headers["Content-Disposition"] == 'attachment; filename="linkedin-2026-09-14-1.png"'
    assert jefe.get(f"/api/linkedin/borradores/{lista[dos]['id']}/imagen").status_code == 404
    otro = _cli(app, "sinpanel@scalerics.com", ["marketing"])
    assert otro.get(f"/api/linkedin/borradores/{bid}/imagen").status_code == 403


def test_si_guardar_falla_el_job_igual_devuelve_los_borradores(db, monkeypatch):
    def roto(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(lb, "guardar_borradores", roto)
    res = linkedin_job_handler({"db_path": db, "lote": "l", "ahora": "2026-09-15T08:00:00"})
    assert len(res["borradores"]) == 2
    assert _sql(db, "SELECT COUNT(*) AS n FROM linkedin_borradores")[0]["n"] == 0


def test_la_semana_es_la_de_montevideo(db):
    domingo_noche = datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc)   # dom 13/9 23:00 en Montevideo
    lunes_temprano = datetime(2026, 9, 14, 3, 0, tzinfo=timezone.utc)  # lun 14/9 00:00 en Montevideo
    uno, _ = _borrador(db, "Uno " * 60, domingo_noche)
    dos, _ = _borrador(db, "Dos " * 60, lunes_temprano)
    semanas = {f["id"]: f["semana"] for f in _sql(db, "SELECT id, semana FROM linkedin_borradores")}
    assert semanas == {uno: "2026-09-07", dos: "2026-09-14"}


# ── API ──────────────────────────────────────────────────────────────────────

def test_lista_por_semana(app, jefe):
    db_path = app.config["DB_PATH"]
    _borrador(db_path, TEXTO_1)
    _borrador(db_path, "Otro post " * 30, tema="Stock a mano")
    _borrador(db_path, "De la semana anterior " * 20, datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc))

    d = jefe.get("/api/linkedin/borradores").get_json()
    assert (d["semana"], d["semana_actual"], d["hoy"], d["limite"]) == ("2026-09-14", "2026-09-14", "2026-09-15", 3000)
    assert d["proxima_generacion"] == "viernes 18/09 a las 08:00"
    assert [b["orden"] for b in d["borradores"]] == [1, 2]
    primero = d["borradores"][0]
    assert primero["texto"] == TEXTO_1 and primero["tema"] == "Planillas que nadie mira"
    assert primero["hashtags"] == "#Automatizacion #Software" and primero["estado"] == "borrador"
    assert primero["caracteres"] == len(TEXTO_1) and primero["pasa_limite"] is False
    assert d["puede_generar"] is True

    anterior = jefe.get("/api/linkedin/borradores?semana=2026-09-10").get_json()
    assert anterior["semana"] == "2026-09-07" and len(anterior["borradores"]) == 1
    assert jefe.get("/api/linkedin/borradores?semana=ayer").status_code == 400


def test_editar_guarda_la_version_editada(app, jefe):
    db_path = app.config["DB_PATH"]
    bid, _ = _borrador(db_path, TEXTO_1)
    r = jefe.put(f"/api/linkedin/borradores/{bid}", json={"texto": "Texto nuevo.\n#Nuevo"})
    assert r.status_code == 200
    b = r.get_json()["borrador"]
    assert b["texto"] == "Texto nuevo.\n#Nuevo" and b["hashtags"] == "#Nuevo" and b["editado_por"] == "Juan"
    fila = _sql(db_path, "SELECT texto_original, editado_en FROM linkedin_borradores WHERE id = ?", (bid,))[0]
    assert fila["texto_original"] == TEXTO_1 and fila["editado_en"]

    for cuerpo in ({"texto": "   "}, {"texto": ""}, {}, {"texto": "x" * 10001}):
        assert jefe.put(f"/api/linkedin/borradores/{bid}", json=cuerpo).status_code == 400
    assert jefe.put("/api/linkedin/borradores/999999", json={"texto": "hola"}).status_code == 404


def test_marcar_publicada_y_descartar(app, jefe):
    db_path = app.config["DB_PATH"]
    bid, pid = _borrador(db_path, TEXTO_1)

    r = jefe.post(f"/api/linkedin/borradores/{bid}/estado", json={"estado": "publicado", "fecha": "2026-09-16"})
    assert r.status_code == 200
    assert (r.get_json()["borrador"]["estado"], r.get_json()["borrador"]["publicado_en"]) == ("publicado", "2026-09-16")
    assert _sql(db_path, "SELECT estado FROM linkedin_posts WHERE id = ?", (pid,))[0]["estado"] == "publicado"

    r = jefe.post(f"/api/linkedin/borradores/{bid}/estado", json={"estado": "descartado"})
    assert (r.get_json()["borrador"]["estado"], r.get_json()["borrador"]["publicado_en"]) == ("descartado", None)
    r = jefe.post(f"/api/linkedin/borradores/{bid}/estado", json={"estado": "publicado"})
    assert r.get_json()["borrador"]["publicado_en"] == "2026-09-15", "sin fecha, hoy en Montevideo"
    r = jefe.post(f"/api/linkedin/borradores/{bid}/estado", json={"estado": "borrador"})
    assert r.get_json()["borrador"]["estado"] == "borrador"

    assert jefe.post(f"/api/linkedin/borradores/{bid}/estado", json={"estado": "subido"}).status_code == 400
    assert jefe.post(f"/api/linkedin/borradores/{bid}/estado",
                     json={"estado": "publicado", "fecha": "16/09/2026"}).status_code == 400
    assert jefe.post("/api/linkedin/borradores/999999/estado", json={"estado": "descartado"}).status_code == 404


def test_el_link_ya_lo_publique_del_mail_marca_la_tarjeta(app):
    db_path = app.config["DB_PATH"]
    bid, _ = _borrador(db_path, TEXTO_1, token="token-del-mail")
    r = app.test_client().get("/api/linkedin/marcar?token=token-del-mail")
    assert r.status_code == 200
    fila = _sql(db_path, "SELECT estado, publicado_en FROM linkedin_borradores WHERE id = ?", (bid,))[0]
    assert fila["estado"] == "publicado" and fila["publicado_en"]


def test_contador_de_caracteres_y_limite(db):
    justo = "ñ" * 3000
    pasa = "á" * 2999 + "🙂" + "b"
    uno, _ = _borrador(db, justo)
    dos, _ = _borrador(db, pasa)
    por_id = {b["id"]: b for b in lb.listar_semana(db, "2026-09-14")}
    assert (por_id[uno]["caracteres"], por_id[uno]["pasa_limite"]) == (3000, False)
    assert (por_id[dos]["caracteres"], por_id[dos]["pasa_limite"]) == (3001, True)


@pytest.mark.parametrize("local,esperado", [
    ((2026, 9, 14, 12, 0), "martes 15/09 a las 08:00"),
    ((2026, 9, 15, 7, 59), "martes 15/09 a las 08:00"),
    ((2026, 9, 15, 8, 0), "viernes 18/09 a las 08:00"),
    ((2026, 9, 18, 9, 0), "martes 22/09 a las 08:00"),
    ((2026, 9, 20, 23, 0), "martes 22/09 a las 08:00"),
])
def test_cuando_se_generan_los_proximos(local, esperado):
    momento = lb.MVD.localize(datetime(*local)).astimezone(timezone.utc)
    assert lb.proxima_generacion(momento) == esperado


# ── permisos ─────────────────────────────────────────────────────────────────

def test_sin_el_panel_da_403(app):
    db_path = app.config["DB_PATH"]
    bid, _ = _borrador(db_path, TEXTO_1)
    c = _cli(app, "caller@scalerics.com", ["cola", "email_mkt"])
    assert c.get("/api/linkedin/borradores").status_code == 403
    assert c.put(f"/api/linkedin/borradores/{bid}", json={"texto": "hola"}).status_code == 403
    assert c.post(f"/api/linkedin/borradores/{bid}/estado", json={"estado": "descartado"}).status_code == 403
    assert c.post("/api/linkedin/borradores/generar").status_code == 403
    assert app.test_client().get("/api/linkedin/borradores").status_code in (401, 302)


def test_generar_ahora_es_solo_admin(app, jefe, monkeypatch):
    c = _cli(app, "marketing@scalerics.com", ["linkedin"])
    d = c.get("/api/linkedin/borradores").get_json()
    assert d["puede_generar"] is False
    assert c.post("/api/linkedin/borradores/generar").status_code == 403

    monkeypatch.setattr(rutas_li, "get_worker", lambda: None)
    assert jefe.post("/api/linkedin/borradores/generar").status_code == 503
    monkeypatch.setattr(rutas_li, "get_worker", lambda: object())
    r = jefe.post("/api/linkedin/borradores/generar")
    assert r.status_code == 202
    job = _sql(app.config["DB_PATH"], "SELECT type, payload FROM jobs WHERE id = ?", (r.get_json()["job_id"],))[0]
    assert job["type"] == "linkedin" and "contexto_manual" not in json.loads(job["payload"])


def test_el_panel_se_reparte_una_vez_a_quien_tiene_inteligencia_marketing(db):
    conn = sqlite3.connect(db)
    try:
        conn.execute("DELETE FROM panel_grants_aplicados WHERE panel='linkedin'")
        for nombre, paneles in (("Prueba marketing", ["marketing"]), ("Prueba mails", ["email_mkt", "cal"]),
                                ("Prueba WA", ["wa"]), ("Prueba ya lo tiene", ["linkedin", "marketing"])):
            conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)", (nombre, json.dumps(paneles)))
        conn.commit()
        antes = {n: json.loads(p) for n, p in conn.execute("SELECT name, panel_access FROM roles")}
        tocadas = database._grant_panel_to_existing_roles(conn, "linkedin", si_tiene=("marketing",))
        acceso = {n: json.loads(p) for n, p in conn.execute("SELECT name, panel_access FROM roles")}
        esperadas = [n for n, p in antes.items() if "linkedin" not in p and "marketing" in p]
        assert tocadas == len(esperadas) and "Prueba marketing" in esperadas and "Prueba mails" not in esperadas
        assert acceso["Prueba marketing"] == ["marketing", "linkedin"]
        assert acceso["Prueba mails"] == ["email_mkt", "cal"], "Email marketing solo no alcanza"
        assert acceso["Prueba WA"] == ["wa"] and acceso["Prueba ya lo tiene"] == ["linkedin", "marketing"]
        assert database._grant_panel_to_existing_roles(conn, "linkedin", si_tiene=("marketing",)) == 0
        conn.execute("UPDATE roles SET panel_access=? WHERE name='Prueba marketing'", (json.dumps(["marketing"]),))
        conn.commit()
    finally:
        conn.close()
    init_db(db)
    assert _sql(db, "SELECT panel_access FROM roles WHERE name='Prueba marketing'")[0]["panel_access"] == '["marketing"]'


# ── histórico ────────────────────────────────────────────────────────────────

def test_los_borradores_que_ya_existian_se_copian_una_vez(tmp_path):
    ruta = str(tmp_path / "hist.db")
    init_db(ruta)
    viejo = _post(ruta, "Post viejo publicado " * 15, tema="Tema viejo")
    otro = _post(ruta, "Post viejo sin publicar " * 15, tema="Otro tema")
    nuevo = _post(ruta, "Post de esta semana " * 15, tema="Tema nuevo")
    _sql(ruta, "UPDATE linkedin_posts SET creado_en='2026-09-08 11:00:00', estado='publicado', "
               "publicado_en='2026-09-10T15:30:00' WHERE id=?", (viejo,))
    _sql(ruta, "UPDATE linkedin_posts SET creado_en='2026-09-11 11:00:00', estado='enviado' WHERE id=?", (otro,))
    _sql(ruta, "UPDATE linkedin_posts SET creado_en='2026-09-15 11:00:00' WHERE id=?", (nuevo,))
    _sql(ruta, "DROP TABLE linkedin_borradores")  # una base de antes de esta rama

    init_db(ruta)
    init_db(ruta)
    filas = {f["post_id"]: f for f in _sql(ruta, "SELECT * FROM linkedin_borradores")}
    assert len(filas) == 3
    assert (filas[viejo]["semana"], filas[viejo]["orden"], filas[viejo]["estado"], filas[viejo]["publicado_en"]) == \
        ("2026-09-07", 1, "publicado", "2026-09-10")
    assert (filas[otro]["semana"], filas[otro]["orden"], filas[otro]["estado"]) == ("2026-09-07", 2, "borrador")
    assert (filas[nuevo]["semana"], filas[nuevo]["orden"], filas[nuevo]["tema"]) == ("2026-09-14", 1, "Tema nuevo")


# ── pantalla ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("nombre", sorted(FUENTES))
def test_nada_que_jinja_o_python_interpreten(nombre):
    for trampa in ("{#", "{{", "{%"):
        assert trampa not in FUENTES[nombre], f"{trampa!r} en el {nombre} de LinkedIn"
    assert chr(92) not in FUENTES[nombre], f"un backslash en el {nombre}"


@pytest.mark.parametrize("nombre", sorted(FUENTES))
def test_no_muestra_dinero(nombre):
    bajo = FUENTES[nombre].lower()
    assert not [p for p in PROHIBIDAS if p in bajo], nombre


def test_el_css_usa_solo_tokens_y_en_el_celular_va_en_una_columna():
    reglas = re.findall(r"([^{}]+)\{([^{}]*)\}", CSS)
    assert len(reglas) > 25
    for selector, cuerpo in reglas:
        assert not re.search(r"#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])|rgba?\(", cuerpo), selector.strip()
    assert "body.light" not in CSS.split("*/", 1)[1]
    assert re.search(r"@media\(max-width:768px\)\{\s*\.li-tarjetas\{grid-template-columns:1fr\}", CSS)
    assert re.search(r"\.li-texto\{[^}]*white-space:pre-wrap", CSS)
    assert "style=" not in PANEL and "style=" not in JS


def test_todo_lo_del_js_lleva_el_prefijo_li():
    nombres = re.findall(r"^(?:async )?function ([A-Za-z_$][\w$]*)\(", JS, re.M)
    nombres += re.findall(r"^(?:const|let) ([A-Za-z_$][\w$]*)", JS, re.M)
    assert len(nombres) > 20
    assert not [n for n in nombres if not (n.startswith("li") or n.startswith("LI_") or n == "loadLinkedin")]
    for nombre in nombres:
        assert len(re.findall(r"^(?:async )?(?:function|const|let) " + re.escape(nombre) + r"\b", HTML, re.M)) == 1
    assert "setInterval" not in JS and "showPanel" not in JS


def test_la_pagina_abre_con_el_panel(jefe):
    r = jefe.get("/")
    assert r.status_code == 200 and b'id="linkedin-panel"' in r.data and b'id="nav-linkedin"' in r.data


_ARNES = r"""
const _els = {};
function _el(id) {
  if (!_els[id]) {
    const clases = new Set();
    const el = { id, innerHTML: '', textContent: '', value: '', disabled: false, style: {}, dataset: {},
                 _clases: clases, _attrs: {},
                 classList: { add(c){ clases.add(c); }, remove(c){ clases.delete(c); },
                              toggle(){}, contains: c => clases.has(c) },
                 querySelectorAll: () => [], querySelector: () => null, closest: () => null,
                 getAttribute: () => null, focus(){}, select(){}, addEventListener(){}, appendChild(){}, remove(){} };
    el.setAttribute = (n, v) => { el._attrs[n] = String(v); };
    _els[id] = el;
  }
  return _els[id];
}
globalThis.document = {
  getElementById: _el, querySelectorAll: () => [], querySelector: () => null,
  body: { classList: { contains: () => false, add(){}, remove(){}, toggle(){} }, appendChild(){} },
  createElement: () => _el('tmp'), addEventListener(){}, execCommand: () => false,
};
globalThis.window = globalThis;
globalThis.localStorage = { getItem: () => null, setItem(){}, removeItem(){} };
globalThis.lucide = { createIcons(){} };
globalThis.alert = () => {};
globalThis.confirm = () => true;
globalThis.setInterval = () => 0;
const _copiados = [];
Object.defineProperty(globalThis, 'navigator', {
  value: { clipboard: { writeText: t => { _copiados.push(t); return Promise.resolve(); } } },
  configurable: true, writable: true
});
const _RESPUESTAS = __RESPUESTAS__;
const _pedidos = [];
globalThis.fetch = (url, opciones) => {
  _pedidos.push([String(url), (opciones && opciones.method) || 'GET', (opciones && opciones.body) || null]);
  const r = _RESPUESTAS[String(url)];
  if (r === 'falla') return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) });
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(r === undefined ? {} : r) });
};
"""


@sin_node
def test_el_panel_se_pinta_copia_edita_y_cambia_estados(app, jefe, tmp_path):
    db_path = app.config["DB_PATH"]
    uno, _ = _borrador(db_path, TEXTO_1)
    largo = "x" * 3001
    dos, dos_post = _borrador(db_path, largo, tema="Un post muy largo")
    lb.guardar_imagenes(db_path, {dos_post: base64.b64encode(PNG).decode()})
    semana = jefe.get("/api/linkedin/borradores").get_json()
    anterior = jefe.get("/api/linkedin/borradores?semana=2026-09-07").get_json()

    prueba = """
(async () => {
  const s = {};
  await loadLinkedin();
  s.tarjetas = _el('li-tarjetas').innerHTML;
  s.semana = _el('li-semana-label').textContent;
  s.sigDeshabilitado = _el('li-semana-sig').disabled;
  s.estadoOculto = _el('li-estado')._clases.has('li-oculto');
  s.generarOculto = _el('li-btn-generar')._clases.has('li-oculto');

  await liCopiar(__UNO__);
  s.copiados = _copiados.slice();
  s.botonCopiar = _el('li-copiar-__UNO__').textContent;

  liEditar(__UNO__);
  s.modalAbierto = _el('li-editar-modal')._clases.has('open');
  s.valorEditar = _el('li-editar-texto').value;
  s.contadorEditar = _el('li-editar-contador').textContent;
  _el('li-editar-texto').value = 'y'.repeat(3001);
  liContarEdicion();
  s.contadorLargo = _el('li-editar-contador').textContent;
  s.contadorPasa = _el('li-editar-contador')._clases.has('li-pasa');
  _el('li-editar-texto').value = '   ';
  await liGuardarEdicion();
  s.errorVacio = _el('li-editar-error').textContent;
  s.putsTrasVacio = _pedidos.filter(p => p[1] === 'PUT').length;
  _el('li-editar-texto').value = 'Texto nuevo';
  await liGuardarEdicion();
  s.cerradoTrasGuardar = !_el('li-editar-modal')._clases.has('open');

  liAbrirPublicar(__UNO__);
  s.publicarAbierto = _el('li-publicar-modal')._clases.has('open');
  s.fechaPorDefecto = _el('li-publicar-fecha').value;
  _el('li-publicar-fecha').value = '2026-09-16';
  await liConfirmarPublicar();
  s.publicarCerrado = !_el('li-publicar-modal')._clases.has('open');
  await liCambiarEstado(__DOS__, 'descartado');

  liSemana(-1);
  await new Promise(r => setTimeout(r, 10));
  s.vacio = _el('li-estado').textContent;
  s.vacioVisible = !_el('li-estado')._clases.has('li-oculto');
  s.pedidos = _pedidos;
  console.log(JSON.stringify(s));
  process.exit(0);
})().catch(e => { console.error((e && e.stack) || e); process.exit(1); });
""".replace("__UNO__", str(uno)).replace("__DOS__", str(dos))

    respuestas = {"/api/linkedin/borradores": semana,
                  "/api/linkedin/borradores?semana=2026-09-07": anterior,
                  f"/api/linkedin/borradores/{uno}": {"ok": True},
                  f"/api/linkedin/borradores/{uno}/estado": {"ok": True},
                  f"/api/linkedin/borradores/{dos}/estado": {"ok": True}}
    bloques = re.findall(r"<script>(.*?)</script>", HTML, re.S)
    archivo = tmp_path / "linkedin.js"
    archivo.write_text(_ARNES.replace("__RESPUESTAS__", json.dumps(respuestas, ensure_ascii=False))
                       + "\n".join(bloques) + "\n" + prueba, encoding="utf-8")
    r = subprocess.run(["node", str(archivo)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, (r.stderr or "")[:2000]
    s = json.loads(r.stdout.strip().splitlines()[-1])

    t = s["tarjetas"]
    assert t.count('<article class="li-tarjeta ') == 2
    assert ("Hola &lt;b&gt;equipo&lt;/b&gt;.\n\nSegunda línea del post.\n#Automatizacion #Software" in t), \
        "escapado y con los saltos de línea tal cual"
    assert f'{len(TEXTO_1)} / 3000 caracteres</span>' in t
    assert '<span class="li-contador li-pasa">3001 / 3000 caracteres · pasa el límite de LinkedIn</span>' in t
    assert t.count('li-chip li-chip-azul">Borrador') == 2 and "Planillas que nadie mira" in t
    for boton in (f'onclick="liCopiar({uno})">Copiar texto</button>', f'onclick="liEditar({uno})">Editar',
                  f'onclick="liAbrirPublicar({uno})">Marcar como publicada',
                  f"onclick=\"liCambiarEstado({dos}, 'descartado')\">Descartar"):
        assert boton in t, boton
    assert "Volver a borrador" not in t

    assert s["semana"] == "Semana del lun 14/09 al dom 20/09" and s["sigDeshabilitado"] is True
    assert s["estadoOculto"] is True and s["generarOculto"] is False
    assert s["copiados"] == [TEXTO_1] and s["botonCopiar"] == "Copiado"
    assert t.count('<img class="li-imagen"') == 1
    assert (f'<img class="li-imagen" src="/api/linkedin/borradores/{dos}/imagen" alt="Imagen del post" loading="lazy">'
            in t)
    assert (f'href="/api/linkedin/borradores/{dos}/imagen?descargar=1" download="linkedin-{dos}.png">Descargar imagen</a>'
            in t)

    assert s["modalAbierto"] and s["valorEditar"] == TEXTO_1
    assert s["contadorEditar"] == f"{len(TEXTO_1)} / 3000 caracteres"
    assert s["contadorLargo"] == "3001 / 3000 caracteres · pasa el límite de LinkedIn" and s["contadorPasa"]
    assert s["errorVacio"] == "El texto no puede quedar vacío." and s["putsTrasVacio"] == 0
    assert s["cerradoTrasGuardar"] is True

    assert s["publicarAbierto"] and s["fechaPorDefecto"] == "2026-09-15" and s["publicarCerrado"]
    cuerpos = {(url, metodo): json.loads(cuerpo) for url, metodo, cuerpo in s["pedidos"] if cuerpo}
    assert cuerpos[(f"/api/linkedin/borradores/{uno}", "PUT")] == {"texto": "Texto nuevo"}
    assert cuerpos[(f"/api/linkedin/borradores/{uno}/estado", "POST")] == {"estado": "publicado", "fecha": "2026-09-16"}
    assert cuerpos[(f"/api/linkedin/borradores/{dos}/estado", "POST")] == {"estado": "descartado", "fecha": None}

    assert s["vacioVisible"] is True
    assert s["vacio"] == "No hubo borradores esa semana. Los próximos se generan el viernes 18/09 a las 08:00."
