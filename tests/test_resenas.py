"""Reseñas y opiniones: QR para comercios (services/resenas.py, routes/resenas.py)."""

import json
import re
import sqlite3
from datetime import datetime, timedelta

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services import resenas as rs

AHORA = datetime(2026, 10, 26, 13, 0)
GOOGLE = "https://g.page/r/CdEjemplo/review"


@pytest.fixture
def reloj(monkeypatch):
    estado = {"ahora": AHORA}
    monkeypatch.setattr(rs, "ahora_uy", lambda: estado["ahora"])
    return estado


@pytest.fixture
def db(tmp_path, monkeypatch, reloj):
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("CRM_URL", "https://crm.test")
    r = str(tmp_path / "rs.db")
    init_db(r)
    return r


def _negocio(db, **kw):
    return rs.crear_negocio(db, {"nombre": "Parrilla El Fogón", "google_url": GOOGLE, "whatsapp": "099 111 222", **kw},
                            "test")


# ── servicio ─────────────────────────────────────────────────────────────────

def test_init_db_crea_las_tablas(db):
    conn = sqlite3.connect(db)
    nombres = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"rs_negocios", "rs_fuentes", "rs_vistas", "rs_opiniones", "rs_google"} <= nombres


def test_el_link_de_google_solo_acepta_google():
    assert rs.google_link("ChIJN1t_tDeuEmsRUsoyG83frY4").endswith("placeid=ChIJN1t_tDeuEmsRUsoyG83frY4")
    assert rs.google_link(GOOGLE) == GOOGLE
    assert rs.google_link("https://maps.app.goo.gl/abc") == "https://maps.app.goo.gl/abc"
    assert rs.google_link("") == ""
    for malo in ("http://g.page/r/x", "https://evil.com/?g.page", "https://g.page.evil.com/x",
                 "javascript:alert(1)", "https://user:pw@google.com/x", "ChIJ"):
        with pytest.raises(rs.NoSePuede):
            rs.google_link(malo)


def test_el_negocio_arma_un_slug_legible_y_unico(db):
    a = _negocio(db)
    b = _negocio(db)
    assert a["slug"] == "parrilla-el-fogon" and b["slug"] == "parrilla-el-fogon-2"
    assert a["whatsapp"] == "59899111222"
    with pytest.raises(rs.NoSePuede):
        rs.editar_negocio(db, b["id"], {"slug": "parrilla-el-fogon"})
    with pytest.raises(rs.NoSePuede):
        rs.crear_negocio(db, {"nombre": " "}, "t")


def test_opinar_baja_abre_una_queja_y_alta_no(db):
    n = _negocio(db)
    baja = rs.opinar(db, n["id"], 2, ip="1.1.1.1")
    alta = rs.opinar(db, n["id"], 5, ip="1.1.1.2")
    assert baja["estado"] == "abierta" and alta["estado"] == "sin_accion"
    assert len(baja["codigo"]) >= 12 and "ip_hash" not in baja
    for malo in (0, 6, "x", None):
        with pytest.raises(rs.NoSePuede):
            rs.opinar(db, n["id"], malo)
    assert [o["id"] for o in rs.opiniones(db, n["id"], "bandeja")] == [baja["id"]]


def test_un_cinco_que_pide_contacto_tambien_va_a_la_bandeja(db):
    n = _negocio(db)
    o = rs.opinar(db, n["id"], 5)
    o = rs.comentar(db, o["codigo"], {"comentario": "Muy bueno, ¿hacen eventos?", "contacto": "099 333 444",
                                      "quiere_contacto": "1", "nombre": "Ana Pérez"})
    assert o["estado"] == "abierta" and o["quiere_contacto"] == 1
    wa = rs.whatsapp_respuesta(n, o)
    assert wa.startswith("https://wa.me/59899333444?text=Hola%20Ana%2C")


def test_comentar_tiene_plazo_y_pide_algo(db, reloj):
    n = _negocio(db)
    o = rs.opinar(db, n["id"], 1)
    with pytest.raises(rs.NoSePuede):
        rs.comentar(db, o["codigo"], {"comentario": "  "})
    reloj["ahora"] = AHORA + timedelta(minutes=rs.MINUTOS_PARA_COMENTAR + 1)
    with pytest.raises(rs.NoSePuede):
        rs.comentar(db, o["codigo"], {"comentario": "tarde"})


def test_el_tope_por_conexion_frena_el_relleno(db):
    n = _negocio(db)
    for _ in range(rs.MAX_POR_IP_DIA):
        rs.opinar(db, n["id"], 5, ip="9.9.9.9")
    with pytest.raises(rs.NoSePuede):
        rs.opinar(db, n["id"], 5, ip="9.9.9.9")
    rs.opinar(db, n["id"], 5, ip="8.8.8.8")


def test_pausado_no_toma_opiniones(db):
    n = _negocio(db)
    rs.editar_negocio(db, n["id"], {"activo": False})
    with pytest.raises(rs.NoSePuede):
        rs.opinar(db, n["id"], 5)


def test_resolver_pide_que_se_anote_que_se_hizo(db):
    n = _negocio(db)
    o = rs.opinar(db, n["id"], 2)
    with pytest.raises(rs.NoSePuede):
        rs.atender(db, o["id"], {"estado": "resuelta"}, "Juan")
    with pytest.raises(rs.NoSePuede):
        rs.atender(db, o["id"], {"estado": "cualquiera"}, "Juan")
    otro = _negocio(db)
    with pytest.raises(rs.NoSePuede):
        rs.atender(db, o["id"], {"estado": "en_curso"}, "Juan", nid=otro["id"])
    r = rs.atender(db, o["id"], {"estado": "resuelta", "nota": "Lo llamamos y le devolvimos."}, "Juan")
    assert r["estado"] == "resuelta" and r["resuelta_en"] and r["atendido_por"] == "Juan"
    assert rs.opiniones(db, n["id"], "bandeja") == []
    assert rs.atender(db, o["id"], {"estado": "abierta"}, "Juan")["resuelta_en"] is None


def test_metricas_de_embudo_por_qr_y_google(db, reloj):
    n = _negocio(db)
    mesas = rs.crear_fuente(db, n["id"], "Mesas")
    assert rs.crear_fuente(db, n["id"], "Mesas")["codigo"] == "mesas-2"
    for _ in range(10):
        rs.registrar_vista(db, n["id"], "mesas")
    rs.registrar_vista(db, n["id"], None)
    rs.registrar_vista(db, n["id"], "no-existe")   # cuenta como link directo
    a = rs.opinar(db, n["id"], 5, "mesas", ip="1")
    rs.opinar(db, n["id"], 4, "mesas", ip="2")
    q = rs.opinar(db, n["id"], 1, None, ip="3")
    assert rs.ir_a_google(db, a["codigo"]) == GOOGLE
    assert rs.ir_a_google(db, a["codigo"]) == GOOGLE   # el segundo clic no cuenta doble
    reloj["ahora"] = AHORA + timedelta(hours=5)
    rs.atender(db, q["id"], {"estado": "resuelta", "nota": "ok"}, "J")
    rs.cargar_google(db, n["id"], {"rating": "4,1", "cantidad": "86", "fecha": "2026-09-01"}, "J")
    rs.cargar_google(db, n["id"], {"rating": "4.4", "cantidad": "1.131"}, "J")
    m = rs.metricas(db, n["id"])
    assert m["vistas"] == 12 and m["opiniones"] == 3 and m["respuesta_pct"] == 25
    assert m["promedio"] == 3.33 and m["positivas_pct"] == 67 and m["google"] == 1
    assert m["google_de_positivas_pct"] == 50 and m["quejas"] == 1 and m["resueltas_pct"] == 100
    assert m["horas_resolucion"] == 5.0 and m["abiertas"] == 0
    f = {x["id"]: x for x in m["por_fuente"]}
    assert f[mesas["id"]]["vistas"] == 10 and f[mesas["id"]]["opiniones"] == 2 and f[mesas["id"]]["conversion"] == 20
    assert f[None]["vistas"] == 2 and f[None]["opiniones"] == 1
    assert m["google_evolucion"]["nuevas"] == 1045 and m["google_evolucion"]["delta_rating"] == 0.3
    assert sum(s["opiniones"] for s in m["semanas"]) == 3
    # Borrar el QR deja las opiniones como link directo.
    rs.borrar_fuente(db, n["id"], mesas["id"])
    assert rs.metricas(db, n["id"])["por_fuente"][0]["opiniones"] == 3


def test_cargar_google_valida(db):
    n = _negocio(db)
    for malo in ({"rating": "7", "cantidad": "3"}, {"rating": "x", "cantidad": "3"}, {"rating": "4", "cantidad": "-1"},
                 {"rating": "4", "cantidad": "3", "fecha": "ayer"}):
        with pytest.raises(rs.NoSePuede):
            rs.cargar_google(db, n["id"], malo, "J")


def test_borrar_negocio_se_lleva_todo(db):
    n = _negocio(db)
    rs.crear_fuente(db, n["id"], "Mesas")
    rs.opinar(db, n["id"], 3, "mesas")
    rs.registrar_vista(db, n["id"], "mesas")
    rs.borrar_negocio(db, n["id"])
    conn = sqlite3.connect(db)
    for t in ("rs_fuentes", "rs_vistas", "rs_opiniones", "rs_google"):
        assert conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0
    conn.close()


def test_qr_svg():
    pytest.importorskip("segno")
    svg = rs.qr_svg("https://crm.test/opina/x?f=mesas")
    assert svg.startswith("<svg") and "<script" not in svg


# ── rutas ────────────────────────────────────────────────────────────────────

@pytest.fixture
def app(db):
    a = dashboard.create_app(db)
    a.config["TESTING"] = True
    return a


def _login(app, db, email="dev@scalerics.com", paneles=("resenas",)):
    uid = create_user(db, name="Dev", email=email, phone="099", password_hash=generate_password_hash("x" * 12))
    conn = sqlite3.connect(db)
    rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)",
                       ("rol-" + email, json.dumps(list(paneles)))).lastrowid
    conn.execute("UPDATE users SET role_id=? WHERE id=?", (rid, uid))
    conn.commit()
    conn.close()
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Dev"
    return c


def test_la_api_y_el_cartel_piden_sesion_y_panel(app, db):
    n = _negocio(db)
    anon = app.test_client()
    assert anon.get("/api/resenas").status_code == 401
    assert anon.get(f"/resenas/cartel/{n['id']}").status_code in (302, 401)
    assert anon.get(f"/resenas/qr/{n['id']}.svg").status_code in (302, 401)
    sin = _login(app, db, "otro@scalerics.com", paneles=("clientes",))
    assert sin.get("/api/resenas").status_code == 403
    assert sin.post("/api/resenas/negocios", json={"nombre": "x"}).status_code == 403
    assert sin.get(f"/resenas/cartel/{n['id']}").status_code == 403


def test_flujo_del_equipo_por_api(app, db):
    c = _login(app, db)
    r = c.post("/api/resenas/negocios", json={"nombre": "Café Lumi", "google_url": "ChIJabcdefghijkl"})
    assert r.status_code == 201
    n = r.get_json()["negocio"]
    assert n["link"] == "https://crm.test/opina/cafe-lumi"
    assert [f["link"] for f in n["fuentes"]] == ["https://crm.test/opina/cafe-lumi?f=mostrador"]
    assert c.post("/api/resenas/negocios", json={"nombre": "X", "google_url": "https://evil.com"}).status_code == 400
    base = f"/api/resenas/negocios/{n['id']}"
    assert c.post(base + "/fuentes", json={"nombre": "Mesas"}).status_code == 201
    o = rs.opinar(db, n["id"], 2, "mesas")
    rs.comentar(db, o["codigo"], {"comentario": "Tardaron", "contacto": "099 222 333", "quiere_contacto": "1"})
    d = c.get(base).get_json()
    assert d["metricas"]["opiniones"] == 1 and d["metricas"]["abiertas"] == 1
    op = d["opiniones"][0]
    assert "codigo" not in op and op["fuente"] == "Mesas" and op["whatsapp"].startswith("https://wa.me/59899222333")
    assert c.patch(f"{base}/opiniones/{op['id']}", json={"estado": "resuelta"}).status_code == 400
    r = c.patch(f"{base}/opiniones/{op['id']}", json={"estado": "resuelta", "nota": "Llamado"})
    assert r.get_json()["opinion"]["estado"] == "resuelta"
    assert c.get(base + "?filtro=resueltas").get_json()["opiniones"][0]["nota"] == "Llamado"
    assert c.post(base + "/google", json={"rating": "4,2", "cantidad": "50"}).status_code == 201
    assert c.patch(base, json={"pregunta": "¿Qué tal el café?"}).get_json()["negocio"]["pregunta"] == "¿Qué tal el café?"
    cartel = c.get(f"/resenas/cartel/{n['id']}")
    assert cartel.status_code == 200 and "https://crm.test/opina/cafe-lumi" in cartel.get_data(as_text=True)
    assert c.delete(base).get_json()["ok"] and c.get(base).status_code == 404


def test_el_qr_del_panel_sale_como_svg(app, db):
    pytest.importorskip("segno")
    c = _login(app, db)
    n = _negocio(db)
    f = rs.crear_fuente(db, n["id"], "Mesas")
    r = c.get(f"/resenas/qr/{n['id']}.svg?f={f['id']}&bajar=1")
    assert r.status_code == 200 and r.mimetype == "image/svg+xml"
    assert "qr-parrilla-el-fogon-mesas.svg" in r.headers["Content-Disposition"]


def test_la_pagina_publica_abre_sin_sesion_y_sin_scripts(app, db):
    n = _negocio(db, pregunta="¿Qué tal <b>la carne</b>?")
    rs.crear_fuente(db, n["id"], "Mesas")
    r = app.test_client().get(f"/opina/{n['slug']}?f=mesas")
    assert r.status_code == 200
    cuerpo = r.get_data(as_text=True)
    assert "Parrilla El Fogón" in cuerpo and "&lt;b&gt;la carne" in cuerpo
    assert "<script" not in cuerpo and " style=" not in cuerpo and "name='puntaje' value='5'" in cuerpo
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "script-src" not in csp and "frame-ancestors 'none'" in csp
    assert rs.metricas(db, n["id"])["por_fuente"][0]["vistas"] == 1
    assert app.test_client().get("/opina/no-existe").status_code == 404
    assert app.test_client().get("/opina/../etc").status_code == 404


def test_opinar_por_el_link_de_punta_a_punta(app, db):
    n = _negocio(db)
    rs.crear_fuente(db, n["id"], "Mesas")
    c = app.test_client()
    r = c.post(f"/opina/{n['slug']}", data={"puntaje": "2", "f": "mesas"})
    assert r.status_code == 303
    url = r.headers["Location"]
    codigo = re.search(r"/r/([\w-]+)$", url).group(1)
    pagina = c.get(url).get_data(as_text=True)
    # A quien puntuó bajo también se le ofrece Google: nada de "review gating".
    assert "Contale al dueño" in pagina and f"/r/{codigo}/google" in pagina and " style=" not in pagina
    r = c.post(url, data={"comentario": "<script>alert(1)</script>", "nombre": "Ana", "contacto": "099 123 456",
                          "quiere_contacto": "1"})
    assert r.headers["Location"].endswith("?ok=1")
    pagina = c.get(r.headers["Location"]).get_data(as_text=True)
    assert "&lt;script&gt;" in pagina and "<script>" not in pagina
    o = rs.por_codigo(db, codigo)
    assert o["estado"] == "abierta" and o["fuente"] == "Mesas" and o["quiere_contacto"] == 1
    g = c.get(f"{url}/google")
    assert g.status_code == 302 and g.headers["Location"] == GOOGLE
    assert rs.por_codigo(db, codigo)["fue_a_google_en"]
    # Un cinco: Google primero, comentario opcional.
    r = c.post(f"/opina/{n['slug']}", data={"puntaje": "5"})
    pagina = c.get(r.headers["Location"]).get_data(as_text=True)
    assert pagina.index("Google</b>") < pagina.index("opcional")
    # Puntaje inválido vuelve con el error.
    assert "error=" in c.post(f"/opina/{n['slug']}", data={"puntaje": "9"}).headers["Location"]


def test_sin_google_cargado_el_boton_no_aparece_ni_redirige(app, db):
    n = rs.crear_negocio(db, {"nombre": "Sin Google"}, "t")
    o = rs.opinar(db, n["id"], 5)
    c = app.test_client()
    assert "/google" not in c.get(f"/opina/{n['slug']}/r/{o['codigo']}").get_data(as_text=True)
    assert c.get(f"/opina/{n['slug']}/r/{o['codigo']}/google").status_code == 303


def test_el_codigo_no_sirve_en_otro_comercio_ni_inventado(app, db):
    n = _negocio(db)
    otro = rs.crear_negocio(db, {"nombre": "Otro"}, "t")
    o = rs.opinar(db, n["id"], 4)
    c = app.test_client()
    assert c.get(f"/opina/{otro['slug']}/r/{o['codigo']}").status_code == 404
    assert c.post(f"/opina/{otro['slug']}/r/{o['codigo']}", data={"comentario": "x"}).status_code == 404
    assert c.get(f"/opina/{otro['slug']}/r/{o['codigo']}/google").status_code == 404
    assert c.get(f"/opina/{n['slug']}/r/inventado-pero-largo-xxxx").status_code == 404


def test_la_trampa_para_robots_no_cuenta(app, db):
    n = _negocio(db)
    app.test_client().post(f"/opina/{n['slug']}", data={"puntaje": "1", "web": "http://spam"})
    assert rs.metricas(db, n["id"])["opiniones"] == 0


def test_el_panel_esta_en_el_menu_y_en_los_permisos():
    html = dashboard.DASHBOARD_HTML
    assert 'id="nav-resenas"' in html and 'id="resenas-panel"' in html
    assert "if (name === 'resenas') rsCargar();" in html and "async function rsCargar()" in html
    fuente = open(dashboard.__file__, encoding="utf-8").read()
    listas = re.findall(r"const ALL_PANELS = \[([^\]]*)\]", fuente)
    assert len(listas) == 2 and all("'resenas'" in lista for lista in listas)
    assert "resenas:'Reseñas'" in fuente


# ── panel del comercio ───────────────────────────────────────────────────────

def test_el_acceso_guarda_solo_el_hash_y_se_revoca(db):
    n = _negocio(db)
    a = rs.crear_acceso(db, n["id"], "Marcela (dueña)", "test")
    assert len(a["token"]) >= 32 and "token_hash" not in a
    conn = sqlite3.connect(db)
    guardado = conn.execute("SELECT token_hash FROM rs_accesos WHERE id = ?", (a["id"],)).fetchone()[0]
    conn.close()
    assert a["token"] not in guardado and guardado == rs._hash_token(a["token"])
    assert rs.por_token(db, a["token"])["negocio"]["id"] == n["id"]
    assert rs.por_token(db, a["token"] + "x") is None and rs.por_token(db, "../../etc") is None
    with pytest.raises(rs.NoSePuede):
        rs.crear_acceso(db, n["id"], "  ", "test")
    otro = rs.crear_negocio(db, {"nombre": "Otro"}, "t")
    with pytest.raises(rs.NoSePuede):
        rs.revocar_acceso(db, otro["id"], a["id"])
    rs.revocar_acceso(db, n["id"], a["id"])
    assert rs.por_token(db, a["token"]) is None
    assert rs.accesos(db, n["id"])[0]["revocado_en"]
    with pytest.raises(rs.NoSePuede):
        rs.revocar_acceso(db, n["id"], a["id"])


def test_tope_de_links_activos_por_comercio(db):
    n = _negocio(db)
    for i in range(rs.MAX_ACCESOS):
        rs.crear_acceso(db, n["id"], f"Persona {i}", "t")
    with pytest.raises(rs.NoSePuede):
        rs.crear_acceso(db, n["id"], "Una más", "t")


def test_novedades_desde_la_ultima_visita(db, reloj):
    n = _negocio(db)
    a = rs.crear_acceso(db, n["id"], "Dueño", "t")
    assert rs.marcar_visita(db, a["id"]) is None
    reloj["ahora"] = AHORA + timedelta(hours=5)
    rs.opinar(db, n["id"], 2, ip="1")
    o = rs.opinar(db, n["id"], 5, ip="2")
    rs.ir_a_google(db, o["codigo"])
    reloj["ahora"] = AHORA + timedelta(days=1)
    antes = rs.marcar_visita(db, a["id"])
    assert antes == rs._txt(AHORA)
    assert rs.novedades(db, n["id"], antes) == {"desde": antes, "opiniones": 2, "quejas": 1, "google": 1}
    # Recargar o resolver algo enseguida no borra las novedades.
    reloj["ahora"] = AHORA + timedelta(days=1, minutes=10)
    assert rs.marcar_visita(db, a["id"]) == antes


def test_borrar_el_comercio_se_lleva_los_accesos(db):
    n = _negocio(db)
    a = rs.crear_acceso(db, n["id"], "Dueño", "t")
    rs.borrar_negocio(db, n["id"])
    assert rs.por_token(db, a["token"]) is None


def test_el_equipo_crea_y_revoca_links_por_api(app, db):
    c = _login(app, db)
    n = _negocio(db)
    base = f"/api/resenas/negocios/{n['id']}/accesos"
    r = c.post(base, json={"nombre": "Marcela"})
    assert r.status_code == 201
    d = r.get_json()
    assert d["link"].startswith("https://crm.test/comercio/") and "token" not in d["acceso"]
    assert d["whatsapp"].startswith("https://wa.me/59899111222?text=")
    lista = c.get(base).get_json()["accesos"]
    assert len(lista) == 1 and "token_hash" not in lista[0]
    assert c.get(f"/api/resenas/negocios/{n['id']}").get_json()["accesos"][0]["nombre"] == "Marcela"
    assert c.post(base, json={"nombre": ""}).status_code == 400
    sin = _login(app, db, "otro@scalerics.com", paneles=("clientes",))
    assert sin.post(base, json={"nombre": "x"}).status_code == 403
    assert app.test_client().get(base).status_code == 401
    token = d["link"].rsplit("/", 1)[1]
    assert app.test_client().get(f"/comercio/{token}").status_code == 200
    assert c.delete(f"{base}/{lista[0]['id']}").get_json()["ok"]
    assert app.test_client().get(f"/comercio/{token}").status_code == 404


def test_el_panel_del_comercio_abre_con_su_link_y_sin_scripts(app, db):
    n = _negocio(db)
    rs.crear_fuente(db, n["id"], "Mesas")
    a = rs.crear_acceso(db, n["id"], "Marcela", "t")
    o = rs.opinar(db, n["id"], 1, "mesas", ip="9")
    rs.comentar(db, o["codigo"], {"comentario": "<img src=x onerror=alert(1)>", "contacto": "099 222 333",
                                  "quiere_contacto": "1"})
    c = app.test_client()
    r = c.get(f"/comercio/{a['token']}")
    assert r.status_code == 200
    cuerpo = r.get_data(as_text=True)
    assert "Parrilla El Fogón" in cuerpo and "hola, Marcela" in cuerpo
    assert "&lt;img src=x" in cuerpo and "<img src=x" not in cuerpo
    assert "<script" not in cuerpo and " style=" not in cuerpo
    assert "https://wa.me/59899222333" in cuerpo and f"/comercio/{a['token']}/o/{o['id']}" in cuerpo
    assert "default-src 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["Referrer-Policy"] == "no-referrer" and r.headers["Cache-Control"] == "no-store"
    assert c.get("/comercio/inventado-pero-bien-largo-xxxxxxxx").status_code == 404
    qr = c.get(f"/comercio/{a['token']}?v=qr").get_data(as_text=True)
    assert "https://crm.test/opina/parrilla-el-fogon?f=mesas" in qr and f"/comercio/{a['token']}/cartel" in qr
    cartel = c.get(f"/comercio/{a['token']}/cartel")
    assert cartel.status_code == 200 and "¿Cómo te atendimos?" in cartel.get_data(as_text=True)
    # Lo de adentro del CRM sigue pidiendo sesión.
    assert c.get(f"/resenas/cartel/{n['id']}").status_code in (302, 401)


def test_el_comercio_toma_y_resuelve_sus_quejas(app, db):
    n = _negocio(db)
    a = rs.crear_acceso(db, n["id"], "Marcela", "t")
    o = rs.opinar(db, n["id"], 2, ip="1")
    c = app.test_client()
    base = f"/comercio/{a['token']}/o/{o['id']}"
    r = c.post(base, data={"accion": "tomar"})
    assert r.status_code == 303 and "ok=1" in r.headers["Location"]
    assert rs.opinion(db, o["id"])["estado"] == "en_curso"
    r = c.post(base, data={"accion": "resolver", "nota": ""})
    assert "error=" in r.headers["Location"] and rs.opinion(db, o["id"])["estado"] == "en_curso"
    c.post(base, data={"accion": "resolver", "nota": "Lo llamé y vuelve el sábado"})
    hecha = rs.opinion(db, o["id"])
    assert hecha["estado"] == "resuelta" and hecha["atendido_por"] == "Marcela · comercio"
    assert "error=" in c.post(base, data={"accion": "borrar"}).headers["Location"]
    c.post(base, data={"accion": "reabrir"})
    assert rs.opinion(db, o["id"])["estado"] == "abierta"


def test_un_link_no_toca_opiniones_de_otro_comercio(app, db):
    n = _negocio(db)
    otro = rs.crear_negocio(db, {"nombre": "Otro"}, "t")
    a = rs.crear_acceso(db, otro["id"], "Intruso", "t")
    o = rs.opinar(db, n["id"], 1, ip="1")
    r = app.test_client().post(f"/comercio/{a['token']}/o/{o['id']}", data={"accion": "tomar"})
    assert r.status_code == 404 and rs.opinion(db, o["id"])["estado"] == "abierta"
    assert "Opiniones de Parrilla" not in app.test_client().get(f"/comercio/{a['token']}").get_data(as_text=True)
