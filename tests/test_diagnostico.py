"""Diagnóstico web gratuito (services/diagnostico.py, routes/diagnostico.py)."""

import gzip
import json
import socket
import sqlite3
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from scripts.preview_seed import html_de_ejemplo
from services import diagnostico as dg

T0 = datetime(2026, 10, 26, 12, 0, 0)


@pytest.fixture(autouse=True)
def _reloj(monkeypatch):
    reloj = {"t": T0}
    monkeypatch.setattr(dg, "_ahora", lambda: reloj["t"])
    dg._IPS.clear()
    dg._DIA.update(dia=None, n=0)
    return reloj


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("CRM_URL", "https://crm.test")
    monkeypatch.delenv("CRM_PREVIEW", raising=False)
    r = str(tmp_path / "dg.db")
    init_db(r)
    return r


def _pagina(html, url="https://barberia.example/", **kw):
    return {"url": url, "url_final": url, "html": html, "ms": 300, "cabeceras": {"content-encoding": "gzip"},
            "sitemap": True, "http_a_https": True, **kw}


def _estado(res, clave):
    return next(h["estado"] for h in res["hallazgos"] if h["clave"] == clave)


BUENA = html_de_ejemplo("Barbería Don Julio", dict(
    titulo="Barbería Don Julio · Cortes y barba en Pocitos", jsonld=True, og=True, analitica=True, pixel=True))
MALA = html_de_ejemplo("Parrilla", dict(viewport=False, whatsapp=False, titulo="Inicio", relleno=True, anio=2020,
                                        wp="5.8", scripts=35, imgs=14))


@pytest.fixture
def red(monkeypatch):
    """Sin red en los tests: obtener() devuelve la página que se le diga."""
    pedidos = []
    paginas = {}

    def falso(url):
        pedidos.append(url)
        algo = paginas.get(dg.dominio_de(url), BUENA)
        if isinstance(algo, Exception):
            raise algo
        return _pagina(algo, url)

    monkeypatch.setattr(dg, "obtener", falso)
    return {"pedidos": pedidos, "paginas": paginas}


# ── la dirección ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("entrada,sale", [
    ("barberia.com.uy", "https://barberia.com.uy/"),
    (" HTTP://WWW.Barberia.com.uy/contacto?utm=1#x ", "http://www.barberia.com.uy/contacto"),
    ("https://barberia.com.uy:443/", "https://barberia.com.uy/"),
    ("peluquería.uy", "https://" + "peluquería.uy".encode("idna").decode() + "/"),
])
def test_normalizar_url(entrada, sale):
    assert dg.normalizar_url(entrada) == sale


@pytest.mark.parametrize("entrada", [
    "", "   ", "127.0.0.1", "http://10.0.0.1/", "[::1]", "localhost", "http://intranet/", "impresora.local",
    "ftp://a.com.uy", "file:///etc/passwd", "a.com.uy:8080", "http://user:clave@a.com.uy/", "a b.com", "x" * 400,
    "javascript:alert(1)", "http://a.com.uy:99999/",
])
def test_normalizar_url_rechaza(entrada):
    with pytest.raises(dg.NoSePuede):
        dg.normalizar_url(entrada)


def test_dominio_sin_www():
    assert dg.dominio_de("https://www.barberia.com.uy/x") == "barberia.com.uy"


@pytest.mark.parametrize("ips,pasa", [
    (["93.184.216.34"], True),
    (["93.184.216.34", "10.0.0.5"], False),          # una privada alcanza para frenar
    (["127.0.0.1"], False), (["169.254.169.254"], False), (["192.168.1.1"], False),
    (["::1"], False), (["::ffff:127.0.0.1"], False), (["fd00::1"], False), (["100.64.0.1"], False),
    (["2606:4700::6810:84e5"], True),
])
def test_resolver_solo_ips_publicas(monkeypatch, ips, pasa):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", (ip, 0)) for ip in ips])
    if pasa:
        assert dg._resolver("sitio.example") == ips[0]
    else:
        with pytest.raises(dg.NoSePuede):
            dg._resolver("sitio.example")


def test_resolver_dominio_que_no_existe(monkeypatch):
    def no(*a, **k):
        raise socket.gaierror("x")
    monkeypatch.setattr(socket, "getaddrinfo", no)
    with pytest.raises(dg.NoSePuede, match="No encontramos"):
        dg._resolver("noexiste.example")


# ── la red, contra un servidor local ─────────────────────────────────────────

class _Sitio(BaseHTTPRequestHandler):
    rutas = {}

    def do_GET(self):
        estado, cab, cuerpo = self.rutas.get(self.path, (404, {}, b"no"))
        self.send_response(estado)
        for k, v in cab.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *a):
        pass


@pytest.fixture
def sitio(monkeypatch):
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), _Sitio)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    resueltos = []
    monkeypatch.setattr(dg, "_resolver", lambda host: resueltos.append(host) or "127.0.0.1")
    monkeypatch.setitem(dg._PUERTOS, "http", servidor.server_address[1])
    _Sitio.rutas = {}
    yield {"rutas": _Sitio.rutas, "resueltos": resueltos}
    servidor.shutdown()


def test_obtener_sigue_redirecciones_y_descomprime(sitio):
    html = BUENA.encode()
    sitio["rutas"].update({
        "/": (301, {"Location": "/inicio"}, b""),
        "/inicio": (200, {"Content-Type": "text/html; charset=utf-8", "Content-Encoding": "gzip"}, gzip.compress(html)),
        "/sitemap.xml": (200, {"Content-Type": "application/xml"}, b"<?xml version='1.0'?><urlset></urlset>"),
        "/robots.txt": (200, {"Content-Type": "text/plain"}, b"User-agent: *"),
    })
    p = dg.obtener("http://barberia.example/")
    assert p["url_final"] == "http://barberia.example/inicio" and p["saltos"] == 1
    assert "Barbería Don Julio" in p["html"] and p["sitemap"] is True and p["robots"] == "User-agent: *"
    assert p["cabeceras"]["content-encoding"] == "gzip" and p["ssl_invalido"] is False
    assert set(sitio["resueltos"]) == {"barberia.example"}
    res = dg.analizar(p)
    assert _estado(res, "https") == "mal" and _estado(res, "compresion") == "ok"


def test_obtener_valida_cada_redireccion(sitio):
    sitio["rutas"]["/"] = (302, {"Location": "http://127.0.0.1/admin"}, b"")
    with pytest.raises(dg.NoSePuede, match="no una IP"):
        dg.obtener("http://barberia.example/")
    sitio["rutas"]["/"] = (302, {"Location": "http://169.254.169.254.nip.io:8080/"}, b"")
    with pytest.raises(dg.NoSePuede, match="puerto"):
        dg.obtener("http://barberia.example/")


def test_obtener_corta_las_redirecciones_infinitas(sitio):
    sitio["rutas"]["/"] = (302, {"Location": "/"}, b"")
    with pytest.raises(dg.NoSePuede, match="redirige demasiadas"):
        dg.obtener("http://barberia.example/")


def test_obtener_errores_que_ve_el_comercio(sitio):
    sitio["rutas"]["/"] = (500, {"Content-Type": "text/html"}, b"error")
    with pytest.raises(dg.NoSePuede, match=r"error \(500\)"):
        dg.obtener("http://barberia.example/")
    sitio["rutas"]["/"] = (200, {"Content-Type": "application/pdf"}, b"%PDF")
    with pytest.raises(dg.NoSePuede, match="no devuelve una página"):
        dg.obtener("http://barberia.example/")


def test_obtener_tiene_tope_de_bytes(sitio, monkeypatch):
    monkeypatch.setattr(dg, "MAX_BYTES", 1000)
    sitio["rutas"]["/"] = (200, {"Content-Type": "text/html"}, b"<p>" + b"a" * 5000)
    p = dg.obtener("http://barberia.example/")
    assert p["bytes"] == 1000 and p["truncado"] is True


# ── el análisis ──────────────────────────────────────────────────────────────

def test_un_sitio_bien_hecho_saca_mucho():
    res = dg.analizar(_pagina(BUENA))
    assert res["puntaje"] >= 90 and res["nivel"]["clave"] == "bien"
    assert {c["clave"] for c in res["categorias"]} == {"celular", "contacto", "google", "velocidad", "confianza", "medicion"}
    for clave in ("viewport", "whatsapp", "telefono", "formulario", "mapa", "noindex", "titulo", "descripcion", "h1",
                  "datos", "og", "https", "analitica", "pixel"):
        assert _estado(res, clave) == "ok", clave
    assert res["prioridades"] == [] or all(_estado(res, k) != "mal" for k in res["prioridades"])


def test_un_sitio_con_problemas_criticos_queda_en_rojo():
    res = dg.analizar(_pagina(MALA, ms=2400, cabeceras={}), anio=2026)
    assert res["puntaje"] <= 49 and res["nivel"]["clave"] == "mal"
    for clave, estado in (("viewport", "mal"), ("whatsapp", "mal"), ("relleno", "mal"), ("respuesta", "mal"),
                          ("titulo", "aviso"), ("anio", "aviso"), ("version", "aviso"), ("scripts", "mal"),
                          ("compresion", "aviso"), ("lazy", "aviso")):
        assert _estado(res, clave) == estado, clave
    # Primero los "mal" más pesados: celular y WhatsApp.
    assert res["prioridades"][:2] == ["viewport", "whatsapp"]
    assert "Botón de WhatsApp con medición" in res["servicios"]
    assert res["medidas"]["plataforma"] == "WordPress"


def test_un_solo_critico_pone_techo():
    html = html_de_ejemplo("Veterinaria", dict(whatsapp=False, titulo="Veterinaria Patitas · Clínica en Pocitos",
                                                jsonld=True, og=True, analitica=True, pixel=True))
    res = dg.analizar(_pagina(html))
    assert _estado(res, "whatsapp") == "mal" and res["puntaje"] == 64


def test_noindex_por_meta_o_por_cabecera():
    html = BUENA.replace("<head>", '<head><meta name="robots" content="noindex, follow">')
    assert _estado(dg.analizar(_pagina(html)), "noindex") == "mal"
    res = dg.analizar(_pagina(BUENA, cabeceras={"x-robots-tag": "noindex"}))
    assert _estado(res, "noindex") == "mal" and res["prioridades"][0] == "noindex"


@pytest.mark.parametrize("enlace", [
    '<a href="https://wa.me/59899000000">x</a>', '<a href="https://api.whatsapp.com/send?phone=598">x</a>',
    '<a href="//wa.link/abc">x</a>', '<a href="whatsapp://send?phone=598">x</a>',
    '<div class="joinchat"></div>', '<a href="#" data-scalerics="whatsapp">x</a>',
])
def test_detecta_whatsapp_de_varias_formas(enlace):
    html = MALA.replace("<footer>", enlace + "<footer>")
    assert _estado(dg.analizar(_pagina(html)), "whatsapp") == "ok"


def test_https_invalido_y_sin_redireccion():
    assert _estado(dg.analizar(_pagina(BUENA, ssl_invalido=True)), "https") == "mal"
    res = dg.analizar(_pagina(BUENA, http_a_https=False))
    assert _estado(res, "https") == "ok" and _estado(res, "redireccion") == "aviso"
    assert "redireccion" not in [h["clave"] for h in dg.analizar(_pagina(BUENA, http_a_https=None))["hallazgos"]]


def test_contenido_mixto_y_buscador_no_es_formulario():
    html = ('<html><head><meta name="viewport" content="width=device-width"><title>Ferretería Norte en Salto</title>'
            '</head><body><form role="search"><input type="search" name="s"></form>'
            '<img src="http://otro.example/a.jpg"></body></html>')
    res = dg.analizar(_pagina(html))
    assert _estado(res, "mixto") == "aviso" and _estado(res, "formulario") == "aviso"


def test_datos_estructurados_en_grafo_y_json_roto():
    grafo = ('<script type="application/ld+json">{"@graph":[{"@type":"WebSite"},{"@type":["Dentist","Thing"]}]}</script>'
             '<script type="application/ld+json">{roto</script>')
    assert _estado(dg.analizar(_pagina(MALA.replace("</head>", grafo + "</head>"))), "datos") == "ok"


def test_html_roto_o_vacio_no_rompe():
    for html in ("", "<html><body><a href=", "<<<>>>&&&", "\x00\x01<title>x"):
        res = dg.analizar(_pagina(html))
        assert 0 <= res["puntaje"] <= 100


def test_textos_del_sitio_no_se_interpretan():
    """El título lo escribe cualquiera: se guarda como texto y lo escapa quien lo pinta."""
    res = dg.analizar(_pagina('<title><script>alert(1)</script> Barbería en Pocitos</title>'))
    assert res["medidas"]["titulo"].startswith("<script>")


# ── base y pedidos ───────────────────────────────────────────────────────────

def _filas(db, tabla):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {tabla} ORDER BY id")]
    finally:
        conn.close()


def test_init_db_crea_la_tabla_y_reparte_el_panel(db):
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'diagnosticos'").fetchone()
    assert conn.execute("SELECT 1 FROM panel_grants_aplicados WHERE panel = 'diagnostico'").fetchone()
    conn.close()


def test_pedido_publico_crea_el_lead(db, red):
    d = dg.crear_publico(db, {"url": "barberia.example", "nombre": "Barbería", "telefono": "099 123 456"}, "1.1.1.1")
    assert d["estado"] == "ok" and d["puntaje"] >= 90 and d["origen"] == "web" and d["telefono"] == "+59899123456"
    b = [x for x in _filas(db, "businesses") if x["id"] == d["business_id"]][0]
    assert b["source"] == "web_diagnostico" and b["website"] == "https://barberia.example/" and b["name"] == "Barbería"
    assert "diagnóstico web de barberia.example" in b["notes"]


def test_pedido_publico_sobre_un_lead_que_ya_estaba(db, red):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO businesses (name, email, source) VALUES ('Parrilla', 'dueno@parrilla.example', 'meta')")
    conn.commit()
    conn.close()
    d = dg.crear_publico(db, {"url": "parrilla.example", "email": "DUENO@parrilla.example"}, "1.1.1.1")
    leads = _filas(db, "businesses")
    assert len(leads) == 1 and leads[0]["source"] == "meta" and d["business_id"] == leads[0]["id"]
    assert any(a["action"] == "diagnostico_web" for a in _filas(db, "activity_log"))


@pytest.mark.parametrize("datos,error", [
    ({"url": "barberia.example"}, "WhatsApp o un mail"),
    ({"url": "barberia.example", "email": "no-es-mail"}, "mail no parece"),
    ({"url": "barberia.example", "telefono": "12"}, "WhatsApp no parece"),
    ({"url": "10.0.0.1", "email": "a@b.uy"}, "no una IP"),
])
def test_pedido_publico_valida(db, red, datos, error):
    with pytest.raises(dg.NoSePuede, match=error):
        dg.crear_publico(db, datos, "1.1.1.1")
    assert red["pedidos"] == []


def test_pedido_publico_tope_por_ip_y_por_dia(db, red, monkeypatch):
    for i in range(dg.TOPE_POR_IP):
        dg.crear_publico(db, {"url": f"sitio{i}.example", "email": f"a{i}@b.uy"}, "1.1.1.1")
    with pytest.raises(dg.NoSePuede, match="seguidos"):
        dg.crear_publico(db, {"url": "otro.example", "email": "z@b.uy"}, "1.1.1.1")
    dg.crear_publico(db, {"url": "otro.example", "email": "z@b.uy"}, "2.2.2.2")
    monkeypatch.setattr(dg, "TOPE_DIA", dg._DIA["n"])
    with pytest.raises(dg.NoSePuede, match="seguidos"):
        dg.crear_publico(db, {"url": "otro.example", "email": "z@b.uy"}, "3.3.3.3")


def test_el_mismo_dominio_en_menos_de_una_hora_no_se_vuelve_a_bajar(db, red, _reloj):
    dg.crear_publico(db, {"url": "barberia.example", "email": "a@b.uy"}, "1.1.1.1")
    dg.crear_publico(db, {"url": "https://www.barberia.example/", "email": "c@d.uy"}, "2.2.2.2")
    assert len(red["pedidos"]) == 1 and len(_filas(db, "diagnosticos")) == 2
    _reloj["t"] = T0 + timedelta(hours=2)
    dg.crear_publico(db, {"url": "barberia.example", "email": "e@f.uy"}, "3.3.3.3")
    assert len(red["pedidos"]) == 2


def test_un_sitio_que_no_abre_queda_igual_como_lead(db, red):
    red["paginas"]["caido.example"] = dg.NoSePuede("El sitio tardó demasiado en responder: un cliente se iría antes.")
    d = dg.crear_publico(db, {"url": "caido.example", "email": "a@b.uy"}, "1.1.1.1")
    assert d["estado"] == "error" and d["puntaje"] is None and "tardó" in d["error"] and d["business_id"]


def test_listar_resumen_y_filtros(db, red):
    red["paginas"]["parrilla.example"] = MALA
    dg.crear_publico(db, {"url": "parrilla.example", "email": "a@b.uy"}, "1.1.1.1")
    dg.crear_equipo(db, {"url": "barberia.example"}, "Juan")
    d = dg.listar(db)
    r = d["resumen"]
    assert r["mes"] == 2 and r["web"] == 1 and r["perdiendo"] == 1 and r["sin_whatsapp"] == 1 and r["nuevos"] == 1
    primero = d["diagnosticos"][1]
    assert primero["dominio"] == "parrilla.example" and "resultado" not in primero
    assert primero["prioridades"][0]["titulo"] == "No está hecho para el celular"
    assert [x["origen"] for x in dg.listar(db, "equipo")["diagnosticos"]] == ["equipo"]


def test_crear_equipo_con_lead(db, red):
    conn = sqlite3.connect(db)
    bid = conn.execute("INSERT INTO businesses (name) VALUES ('Óptica Central')").lastrowid
    conn.commit()
    conn.close()
    d = dg.crear_equipo(db, {"url": "optica.example", "business_id": bid}, "Juan")
    assert d["business_id"] == bid and d["nombre"] == "Óptica Central" and d["origen"] == "equipo"
    with pytest.raises(dg.NoSePuede, match="no existe"):
        dg.crear_equipo(db, {"url": "optica.example", "business_id": 9999}, "Juan")
    assert [x["id"] for x in dg.leads_sugeridos(db, "optica")] == [bid] == [x["id"] for x in dg.leads_sugeridos(db, "ÓPTI")]
    assert dg.leads_sugeridos(db, "o") == [] and dg.leads_sugeridos(db, "50%") == []


# ── rutas ────────────────────────────────────────────────────────────────────

def _app(db):
    app = dashboard.create_app(db)
    app.config["TESTING"] = True
    return app


def _equipo(db, paneles=("diagnostico",), email="dev@scalerics.com"):
    uid = create_user(db, name="Dev", email=email, phone="1", password_hash=generate_password_hash("x" * 12))
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO roles (name, panel_access) VALUES (?, ?)", ("rol-" + email, json.dumps(list(paneles))))
    rid = conn.execute("SELECT id FROM roles WHERE name = ?", ("rol-" + email,)).fetchone()[0]
    conn.execute("UPDATE users SET role_id = ? WHERE id = ?", (rid, uid))
    conn.commit()
    conn.close()
    c = _app(db).test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Dev"
    return c


def test_formulario_publico_sin_sesion(db):
    r = _app(db).test_client().get("/diagnostico")
    assert r.status_code == 200 and b"Analizar mi sitio" in r.data
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "script-src 'nonce-" in csp and "frame-ancestors 'self' https://scalerics.com" in csp
    assert r.headers["X-Robots-Tag"] == "noindex"


def test_formulario_publico_lleva_al_informe(db, red):
    c = _app(db).test_client()
    r = c.post("/diagnostico", data={"url": "barberia.example", "nombre": "<b>Barbería</b>", "email": "a@b.uy"})
    assert r.status_code == 303 and r.headers["Location"].startswith("/diagnostico/")
    inf = c.get(r.headers["Location"])
    assert inf.status_code == 200 and b"barberia.example" in inf.data
    assert b"<b>Barber" not in inf.data and b"&lt;b&gt;Barber" in inf.data
    assert b"a@b.uy" not in inf.data                      # el informe no muestra el contacto
    assert "script-src 'none'" in inf.headers["Content-Security-Policy"]
    assert "wa.me/59897250713?text=" in inf.data.decode()
    assert _filas(db, "diagnosticos")[0]["aperturas"] == 1


def test_formulario_publico_muestra_el_error(db, red):
    r = _app(db).test_client().post("/diagnostico", data={"url": "barberia.example"})
    assert r.status_code == 400 and "Dejanos un WhatsApp o un mail" in r.data.decode()
    assert 'value="barberia.example"' in r.data.decode()


def test_formulario_desde_otro_sitio_no(db, red):
    r = _app(db).test_client().post("/diagnostico", data={"url": "a.example", "email": "a@b.uy"},
                                     headers={"Origin": "https://malo.example"})
    assert r.status_code == 403 and red["pedidos"] == []


def test_api_publica_con_cors_para_scalerics(db, red):
    c = _app(db).test_client()
    pre = c.options("/diagnostico/api", headers={"Origin": "https://scalerics.com"})
    assert pre.headers["Access-Control-Allow-Origin"] == "https://scalerics.com"
    r = c.post("/diagnostico/api", json={"url": "barberia.example", "telefono": "099123456"},
               headers={"Origin": "https://scalerics.com"})
    assert r.status_code == 201 and r.get_json()["informe"].startswith("https://crm.test/diagnostico/")
    assert r.headers["Access-Control-Allow-Origin"] == "https://scalerics.com"
    mal = c.post("/diagnostico/api", json={"url": "x.example", "email": "a@b.uy"}, headers={"Origin": "https://malo.example"})
    assert mal.status_code == 403 and "Access-Control-Allow-Origin" not in mal.headers


def test_informe_que_no_existe_y_el_que_no_abrio(db, red):
    c = _app(db).test_client()
    assert c.get("/diagnostico/no-existe").status_code == 404
    red["paginas"]["caido.example"] = dg.NoSePuede("El sitio no acepta conexiones: está caído o mal configurado.")
    d = dg.crear_publico(db, {"url": "caido.example", "email": "a@b.uy"}, "1.1.1.1")
    r = c.get(f"/diagnostico/{d['codigo']}")
    assert r.status_code == 200 and "No pudimos abrir" in r.data.decode() and "está caído" in r.data.decode()


def test_el_equipo_no_cuenta_como_apertura(db, red):
    d = dg.crear_equipo(db, {"url": "barberia.example"}, "Juan")
    _equipo(db).get(f"/diagnostico/{d['codigo']}")
    assert _filas(db, "diagnosticos")[0]["aperturas"] == 0


def test_api_del_equipo_pide_el_panel(db):
    c = _equipo(db, paneles=("clientes",))
    assert c.get("/api/diagnosticos").status_code == 403
    assert c.post("/api/diagnosticos", json={"url": "a.example"}).status_code == 403
    assert _app(db).test_client().get("/api/diagnosticos").status_code in (302, 401, 403)


def test_api_del_equipo_de_punta_a_punta(db, red):
    c = _equipo(db)
    r = c.post("/api/diagnosticos", json={"url": "barberia.example"})
    assert r.status_code == 201
    d = r.get_json()["diagnostico"]
    assert d["informe_url"] == f"https://crm.test/diagnostico/{d['codigo']}"
    assert c.post("/api/diagnosticos", json={"url": "10.0.0.1"}).status_code == 400
    lista = c.get("/api/diagnosticos").get_json()
    assert lista["publica_url"] == "https://crm.test/diagnostico" and lista["diagnosticos"][0]["id"] == d["id"]
    assert c.get(f"/api/diagnosticos/{d['id']}").get_json()["diagnostico"]["resultado"]["puntaje"] == d["puntaje"]
    assert c.patch(f"/api/diagnosticos/{d['id']}", json={"seguimiento": "otro"}).status_code == 400
    e = c.patch(f"/api/diagnosticos/{d['id']}", json={"seguimiento": "hablado", "nota": " Le interesa "}).get_json()
    assert e["diagnostico"]["seguimiento"] == "hablado" and e["diagnostico"]["nota"] == "Le interesa"
    red["paginas"]["barberia.example"] = MALA
    rep = c.post(f"/api/diagnosticos/{d['id']}/repetir").get_json()["diagnostico"]
    assert rep["puntaje"] < d["puntaje"] and len(red["pedidos"]) == 2
    nuevo = c.post(f"/api/diagnosticos/{d['id']}/nuevo-link").get_json()["diagnostico"]
    assert nuevo["codigo"] != d["codigo"] and c.get(f"/diagnostico/{d['codigo']}").status_code == 404
    assert c.delete(f"/api/diagnosticos/{d['id']}").get_json()["ok"]
    assert c.get(f"/api/diagnosticos/{d['id']}").status_code == 404


def test_el_panel_esta_en_ventas():
    html = dashboard.DASHBOARD_HTML
    assert 'id="nav-diagnostico"' in html and 'id="diagnostico-panel"' in html
    assert "if (name === 'diagnostico') dgCargar();" in html and "async function dgCargar()" in html


def test_la_vista_previa_siembra_diagnosticos(tmp_path, monkeypatch):
    monkeypatch.setattr(dg, "_ahora", lambda: T0)
    from scripts.preview_seed import sembrar
    base = str(tmp_path / "prev.db")
    sembrar(base, "preview@scalerics.com", "x" * 16)
    sembrar(base, "preview@scalerics.com", "x" * 16)      # dos veces no duplica
    filas = _filas(base, "diagnosticos")
    assert len(filas) == 5 and filas[0]["codigo"] == "vista-previa-diagnostico" and filas[0]["puntaje"] <= 49
    assert {f["estado"] for f in filas} == {"ok", "error"}
