"""Analítica de contactos de los sitios de clientes (services/analitica.py, routes/analitica.py)."""

import json
import sqlite3
from datetime import date, datetime, timedelta

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services import analitica as an

T0 = datetime(2026, 10, 15, 15, 0, 0)  # UTC: 12:00 en Montevideo
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1"


@pytest.fixture(autouse=True)
def _reloj(monkeypatch):
    reloj = {"t": T0}
    monkeypatch.setattr(an, "_ahora", lambda: reloj["t"])
    an._IPS.clear()
    an._CUENTA_DIA.clear()
    an._ULTIMA_PURGA["dia"] = None
    return reloj


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("CRM_URL", "https://crm.test")
    monkeypatch.delenv("CRM_PREVIEW", raising=False)
    r = str(tmp_path / "an.db")
    init_db(r)
    return r


def _sitio(db, **kw):
    datos = {"nombre": "Barbería Don Julio", "dominios": "barberia.com.uy", **kw}
    return an.crear(db, datos, "test")


def _ev(sitio, t="vista", u="https://barberia.com.uy/", **kw):
    return json.dumps({"k": sitio["clave"], "t": t, "u": u, **kw}).encode()


def _mandar(db, sitio, t="vista", ip="1.2.3.4", ua=UA, origin="https://barberia.com.uy", **kw):
    return an.registrar(db, _ev(sitio, t, **kw), ip, ua, origin)


def _eventos(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    filas = [dict(r) for r in conn.execute("SELECT * FROM analitica_eventos ORDER BY id")]
    conn.close()
    return filas


# ── tablas y sitios ──────────────────────────────────────────────────────────

def test_init_db_crea_las_tablas(db):
    conn = sqlite3.connect(db)
    nombres = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"analitica_sitios", "analitica_eventos", "analitica_sal"} <= nombres


def test_normalizar_dominio():
    assert an.normalizar_dominio(" https://www.Barberia.com.uy/contacto ") == "barberia.com.uy"
    assert an.normalizar_dominio("tienda.uy") == "tienda.uy"
    for malo in ("", "localhost", "http://", "no es un dominio", "javascript:alert(1)"):
        with pytest.raises(an.NoSePuede):
            an.normalizar_dominio(malo)


def test_crear_valida_y_genera_claves(db):
    s = _sitio(db, dominios="barberia.com.uy, www.barberia.com.uy, barberia.uy", ticket_medio="1.200",
               cierre_pct=40)
    assert s["dominios"] == ["barberia.com.uy", "barberia.uy"]
    assert s["ticket_medio"] == 1200 and s["cierre_pct"] == 40
    assert len(s["clave"]) >= 16 and len(s["codigo"]) >= 24 and s["clave"] != s["codigo"]
    with pytest.raises(an.NoSePuede, match="nombre"):
        an.crear(db, {"nombre": " ", "dominios": "a.uy"}, "t")
    with pytest.raises(an.NoSePuede, match="dominio"):
        an.crear(db, {"nombre": "x", "dominios": ""}, "t")
    with pytest.raises(an.NoSePuede, match="cierre"):
        an.crear(db, {"nombre": "x", "dominios": "a.uy", "cierre_pct": 150}, "t")


def test_editar_y_nuevo_codigo(db):
    s = _sitio(db)
    e = an.editar(db, s["id"], {"nombre": "Don Julio", "dominios": "donjulio.uy", "activo": False})
    assert e["nombre"] == "Don Julio" and e["dominios"] == ["donjulio.uy"] and e["activo"] == 0
    n = an.nuevo_codigo(db, s["id"])
    assert n["codigo"] != s["codigo"] and an.por_codigo(db, s["codigo"]) is None
    with pytest.raises(an.NoSePuede, match="no existe"):
        an.editar(db, 999, {"nombre": "x"})


# ── recepción ────────────────────────────────────────────────────────────────

def test_registra_una_vista_con_solo_la_ruta(db):
    s = _sitio(db)
    assert _mandar(db, s, u="https://www.barberia.com.uy/precios/?email=juan@x.com&t=abc#arriba", e=1,
                   r="https://www.google.com/", w=390) == "ok"
    ev = _eventos(db)[0]
    assert ev["pagina"] == "/precios"           # sin query: ahí viajan mails y tokens
    assert ev["canal"] == "Google" and ev["entrada"] == 1 and ev["dispositivo"] == "movil"
    assert ev["dia"] == "2026-10-15" and len(ev["visitante"]) == 16
    assert "1.2.3.4" not in json.dumps(ev) and "iPhone" not in json.dumps(ev)
    assert an.obtener(db, s["id"])["ultimo_evento"] == "2026-10-15 15:00:00"


def test_rechaza_otro_dominio_otro_origen_y_basura(db):
    s = _sitio(db)
    assert _mandar(db, s, u="https://otro.com/") == "dominio"
    assert _mandar(db, s, u="https://barberia.com.uy.malo.com/") == "dominio"
    assert _mandar(db, s, origin="https://otro.com") == "origen"
    assert _mandar(db, s, t="hackeo") == "tipo"
    assert an.registrar(db, b"no json", "1.1.1.1", UA, "") == "json"
    assert an.registrar(db, b"x" * 5000, "1.1.1.1", UA, "") == "tamaño"
    assert an.registrar(db, json.dumps({"k": "nada", "t": "vista", "u": "https://barberia.com.uy/"}).encode(),
                        "1.1.1.1", UA, "") == "clave"
    assert _mandar(db, s, ua="Mozilla/5.0 (compatible; Googlebot/2.1)") == "robot"
    assert _mandar(db, s, ua="") == "robot"
    assert _eventos(db) == []


def test_subdominio_cuenta(db):
    s = _sitio(db)
    assert _mandar(db, s, u="https://tienda.barberia.com.uy/", origin="https://tienda.barberia.com.uy") == "ok"


def test_sitio_pausado_no_guarda(db):
    s = _sitio(db)
    an.editar(db, s["id"], {"activo": False})
    assert _mandar(db, s) == "clave"


def test_topes_por_ip_y_por_sitio(db, monkeypatch):
    s = _sitio(db)
    for _ in range(an.TOPE_IP_MINUTO):
        assert _mandar(db, s) == "ok"
    assert _mandar(db, s) == "tope_ip"
    assert _mandar(db, s, ip="5.5.5.5") == "ok"
    monkeypatch.setattr(an, "MAX_EVENTOS_DIA", an.TOPE_IP_MINUTO + 1)
    assert _mandar(db, s, ip="6.6.6.6") == "tope_sitio"


def test_pagina_de_compra_de_woocommerce_suma_una_compra(db):
    s = _sitio(db)
    _mandar(db, s, u="https://barberia.com.uy/finalizar-compra/pedido-recibido/123/?key=wc_order_x")
    assert [e["tipo"] for e in _eventos(db)] == ["vista", "compra"]


def test_formulario_guarda_solo_un_nombre_limpio(db):
    s = _sitio(db)
    _mandar(db, s, t="formulario", d="<img src=x onerror=alert(1)>contacto")
    _mandar(db, s, t="whatsapp", d="+598 99 123 456")
    ev = _eventos(db)
    assert "<" not in ev[0]["detalle"] and ev[1]["detalle"] is None


def test_visitante_cambia_de_un_dia_al_otro(db, _reloj):
    s = _sitio(db)
    _mandar(db, s)
    _mandar(db, s)
    _reloj["t"] = T0 + timedelta(days=1)
    _mandar(db, s)
    v = [e["visitante"] for e in _eventos(db)]
    assert v[0] == v[1] and v[2] != v[0]
    conn = sqlite3.connect(db)
    sales = conn.execute("SELECT dia FROM analitica_sal").fetchall()
    conn.close()
    assert sales == [("2026-10-16",)]   # la sal de ayer se borró: el hash no se puede rehacer


def test_la_pagina_de_prueba_del_crm_cuenta_solo_para_su_sitio(db):
    s = _sitio(db)
    otro = _sitio(db, nombre="Otro", dominios="otro.uy")
    url = f"https://crm.test/a/prueba/{s['clave']}"
    assert an.registrar(db, _ev(s, u=url), "1.1.1.1", UA, "https://crm.test", "crm.test") == "ok"
    assert _eventos(db)[0]["pagina"] == an.PAGINA_PRUEBA
    assert an.registrar(db, _ev(otro, u=url), "1.1.1.1", UA, "https://crm.test", "crm.test") == "dominio"
    assert an.registrar(db, _ev(s, u="https://crm.test/otra"), "1.1.1.1", UA, "https://crm.test",
                        "crm.test") == "dominio"


# ── canales ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("args,canal", [
    (("",), "Directo"),
    (("https://www.google.com.uy/",), "Google"),
    (("https://www.google.com/", "", "", True), "Google Ads"),
    (("", "google", "cpc"), "Google Ads"),
    (("", "facebook", "paid_social"), "Meta Ads"),
    (("https://l.instagram.com/",), "Instagram"),
    (("https://m.facebook.com/",), "Facebook"),
    (("", "", "", False, True), "Facebook"),
    (("https://duckduckgo.com/",), "Otros buscadores"),
    (("", "newsletter", "email"), "Mail"),
    (("https://www.tiktok.com/",), "Otras redes"),
    (("https://guiadelocales.uy/",), "Otros sitios"),
    (("https://barberia.com.uy/precios", "", "", False, False, ("barberia.com.uy",)), "Directo"),
])
def test_clasificar(args, canal):
    assert an.clasificar(*args)[0] == canal


# ── números ──────────────────────────────────────────────────────────────────

def test_numeros_cuenta_contactos_por_persona_y_atribuye_al_origen(db):
    s = _sitio(db, ticket_medio=1000, cierre_pct=50)
    g = {"r": "https://www.google.com/"}
    _mandar(db, s, e=1, **g)
    _mandar(db, s, u="https://barberia.com.uy/precios", **g)
    for _ in range(3):   # tres toques al mismo botón: una persona
        _mandar(db, s, t="whatsapp", u="https://barberia.com.uy/precios", **g)
    _mandar(db, s, t="llamada", u="https://barberia.com.uy/precios", **g)
    _mandar(db, s, ip="9.9.9.9", e=1, r="https://l.instagram.com/")
    n = an.numeros(db, s["id"], date(2026, 10, 1), date(2026, 10, 15))
    assert n["visitas"] == 2 and n["paginas_vistas"] == 3 and n["visitantes"] == 2
    assert n["contactos"] == 2 and n["clics"] == 4
    assert n["por_tipo"]["whatsapp"] == 1 and n["por_tipo"]["llamada"] == 1
    assert n["conversion"] == 1.0
    assert n["canales"][0] == {"canal": "Google", "visitas": 1, "contactos": 2, "conversion": 2.0}
    assert n["paginas"][0]["pagina"] == "/precios" and n["paginas"][0]["contactos"] == 2
    assert n["valor_estimado"] == 1000     # 2 contactos × 50 % × $1000
    assert n["por_hora"][12] == 2 and n["fuera_de_hora"] == 0
    assert len(n["dias"]) == 15 and n["dias"][-1]["contactos"] == 2


def test_listar_estado_y_variacion(db, _reloj):
    s = _sitio(db)
    nuevo = _sitio(db, nombre="Nuevo", dominios="nuevo.uy")
    _reloj["t"] = T0 - timedelta(days=40)
    _mandar(db, s, e=1)
    _reloj["t"] = T0
    _mandar(db, s, e=1)
    _mandar(db, s, e=1, ip="2.2.2.2")
    sitios = {x["nombre"]: x for x in an.listar(db)}
    assert sitios["Barbería Don Julio"]["estado"] == "ok"
    assert sitios["Barbería Don Julio"]["visitas"] == 2 and sitios["Barbería Don Julio"]["var_visitas"] == 1.0
    assert sitios["Nuevo"]["estado"] == "sin_instalar"
    _reloj["t"] = T0 + timedelta(days=an.DIAS_SIN_DATOS + 1)
    assert an.estado(an.obtener(db, s["id"])) == "sin_datos"
    r = an.resumen(an.listar(db))
    assert r["sitios"] == 2 and r["sin_datos"] == 2
    assert nuevo["id"]


def test_reporte_compara_con_los_mismos_dias_del_mes_anterior(db, _reloj):
    s = _sitio(db)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE analitica_sitios SET creado_en = '2026-08-01 12:00:00'")
    conn.commit()
    conn.close()
    _reloj["t"] = datetime(2026, 9, 5, 15)
    _mandar(db, s, e=1)
    _reloj["t"] = datetime(2026, 9, 25, 15)   # después del 15: no entra en la comparación
    _mandar(db, s, e=1)
    _reloj["t"] = T0
    _mandar(db, s, e=1, ip="3.3.3.3")
    _mandar(db, s, e=1, ip="4.4.4.4")
    assert an.meses(an.obtener(db, s["id"])) == ["2026-10", "2026-09", "2026-08"]
    r = an.reporte(db, s["id"], "2026-10")
    assert r["visitas"] == 2 and r["var_visitas"] == 1.0 and r["en_curso"]
    sep = an.reporte(db, s["id"], "2026-09")
    assert sep["visitas"] == 2 and not sep["en_curso"] and len(sep["dias"]) == 30


def test_purgar_borra_lo_de_mas_de_13_meses(db, _reloj):
    s = _sitio(db)
    _reloj["t"] = T0 - timedelta(days=an.RETENCION_DIAS + 5)
    _mandar(db, s)
    _reloj["t"] = T0
    _mandar(db, s, ip="7.7.7.7")
    assert an.purgar(db) == 1 and len(_eventos(db)) == 1


# ── rutas ────────────────────────────────────────────────────────────────────

def _usuario(db, email, paneles):
    uid = create_user(db, name="Dev", email=email, phone="1", password_hash=generate_password_hash("x" * 12))
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO roles (name, panel_access) VALUES (?, ?)", ("rol-" + email, json.dumps(paneles)))
    rid = conn.execute("SELECT id FROM roles WHERE name = ?", ("rol-" + email,)).fetchone()[0]
    conn.execute("UPDATE users SET role_id = ? WHERE id = ?", (rid, uid))
    conn.commit()
    conn.close()
    return uid


def _app(db):
    app = dashboard.create_app(db)
    app.config["TESTING"] = True
    return app


def _equipo(db, email="dev@scalerics.com", paneles=("analitica",)):
    uid = _usuario(db, email, list(paneles))
    c = _app(db).test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Dev"
    return c


def test_sin_el_panel_da_403_y_sin_sesion_no_responde(db):
    c = _equipo(db, paneles=("clientes",))
    assert c.get("/api/analitica/sitios").status_code == 403
    assert c.post("/api/analitica/sitios", json={"nombre": "x", "dominios": "x.uy"}).status_code == 403
    assert _app(db).test_client().get("/api/analitica/sitios").status_code in (302, 401, 403)


def test_alta_lista_detalle_edita_y_borra(db):
    c = _equipo(db)
    r = c.post("/api/analitica/sitios", json={"nombre": "Bambú", "dominios": "bambu.com.uy"})
    assert r.status_code == 201
    s = r.get_json()["sitio"]
    assert s["informe_url"] == f"https://crm.test/informe/{s['codigo']}"
    assert s["snippet"] == f'<script defer src="https://crm.test/a/s.js" data-sitio="{s["clave"]}"></script>'
    d = c.get("/api/analitica/sitios?dias=7").get_json()
    assert d["dias"] == 7 and d["resumen"]["sitios"] == 1 and d["sitios"][0]["estado"] == "sin_instalar"
    det = c.get(f"/api/analitica/sitios/{s['id']}").get_json()["sitio"]
    assert len(det["numeros"]["dias"]) == 30 and det["meses"] == ["2026-10"]
    assert c.patch(f"/api/analitica/sitios/{s['id']}", json={"ticket_medio": "abc"}).status_code == 400
    assert c.patch(f"/api/analitica/sitios/{s['id']}", json={"ticket_medio": 900}).get_json()["sitio"][
        "ticket_medio"] == 900
    nuevo = c.post(f"/api/analitica/sitios/{s['id']}/nuevo-link").get_json()["sitio"]
    assert nuevo["codigo"] != s["codigo"]
    assert c.post("/api/analitica/sitios", json={"nombre": "", "dominios": "x.uy"}).status_code == 400
    assert c.delete(f"/api/analitica/sitios/{s['id']}").get_json()["ok"]
    assert c.get(f"/api/analitica/sitios/{s['id']}").status_code == 404


def test_endpoint_publico_contesta_202_a_todo_con_cors(db):
    s = _sitio(db)
    c = _app(db).test_client()
    r = c.post("/a/e", data=_ev(s), content_type="text/plain",
               headers={"User-Agent": UA, "Origin": "https://barberia.com.uy", "Fly-Client-IP": "8.8.8.8"})
    assert r.status_code == 202 and r.headers["Access-Control-Allow-Origin"] == "*"
    r = c.post("/a/e", data=b"basura", headers={"User-Agent": UA})
    assert r.status_code == 202
    assert c.open("/a/e", method="OPTIONS").status_code == 202
    assert len(_eventos(db)) == 1


def test_el_script_se_sirve_sin_sesion(db):
    r = _app(db).test_client().get("/a/s.js")
    assert r.status_code == 200 and r.mimetype == "application/javascript"
    js = r.get_data(as_text=True)
    assert "sendBeacon" in js and "document.cookie" not in js and "data-sitio" in js


def test_pagina_de_prueba(db):
    s = _sitio(db)
    c = _app(db).test_client()
    r = c.get(f"/a/prueba/{s['clave']}")
    assert r.status_code == 200
    cuerpo = r.get_data(as_text=True)
    assert f"data-sitio='{s['clave']}'" in cuerpo and "wa.me" in cuerpo
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert c.get("/a/prueba/no-existe").status_code == 404


def test_informe_publico(db):
    s = _sitio(db, nombre="<b>Barbería</b>", ticket_medio=1000, cierre_pct=50)
    _mandar(db, s, e=1, r="https://www.google.com/")
    _mandar(db, s, t="whatsapp", r="https://www.google.com/")
    c = _app(db).test_client()
    r = c.get(f"/informe/{s['codigo']}")
    assert r.status_code == 200
    cuerpo = r.get_data(as_text=True)
    assert "&lt;b&gt;Barbería&lt;/b&gt;" in cuerpo and "<b>Barbería</b>" not in cuerpo
    assert "personas te escribieron o llamaron desde la web" in cuerpo
    assert "1 por WhatsApp" in cuerpo and "Valor estimado" in cuerpo and "Google" in cuerpo and "Octubre 2026" in cuerpo
    assert "<script" not in cuerpo
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "script-src" not in csp
    assert r.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert c.get(f"/informe/{s['codigo']}?mes=1999-01").status_code == 200   # mes raro: el actual
    assert c.get("/informe/no-existe").status_code == 404


def test_el_panel_esta_en_el_menu_y_en_los_permisos():
    html = dashboard.DASHBOARD_HTML
    assert 'id="nav-analitica"' in html and 'id="analitica-panel"' in html
    assert "if (name === 'analitica') anCargar();" in html
    assert "async function anCargar()" in html
    assert html.count("'sombra','agentes','analitica'];") >= 1


def test_la_vista_previa_siembra_datos(tmp_path, monkeypatch):
    from scripts import preview_seed
    db = str(tmp_path / "p.db")
    monkeypatch.setattr(an, "_ahora", lambda: datetime(2026, 10, 15, 15))
    preview_seed.sembrar(db, "p@scalerics.com", "x" * 12)
    preview_seed.sembrar(db, "p@scalerics.com", "x" * 12)   # idempotente
    s = an.por_codigo(db, "vista-previa-analitica")
    assert s and an.por_clave(db, "vista-previa-prueba")["id"] == s["id"]
    sitios = {x["nombre"]: x for x in an.listar(db)}
    assert len(sitios) == 4
    assert sitios["Barbería Don Julio"]["contactos"] > 20 and sitios["Barbería Don Julio"]["estado"] == "ok"
    assert sitios["Gimnasio Fuerza"]["estado"] == "sin_datos"
    assert sitios["Óptica Central"]["estado"] == "sin_instalar"
    assert sitios["Tienda Bambú"]["compras"] > 0
