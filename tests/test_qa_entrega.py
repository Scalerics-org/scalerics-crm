"""Control de calidad antes de entregar (services/qa_entrega.py, routes/qa_entrega.py)."""

import json
import sqlite3
from datetime import datetime

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services import qa_entrega as qa

AHORA = datetime(2026, 10, 25, 12, 0, 0)
_PEDIR = qa._pedir  # el de verdad, antes de que la fixture lo tape


@pytest.fixture(autouse=True)
def _sin_red(monkeypatch):
    """Ningún test sale a internet: el pedido, el SSL y el DNS van falsos."""
    def _no(*a, **k):
        raise AssertionError("Un test intentó salir a internet desde el control de calidad")
    monkeypatch.setattr(qa, "_pedir", _no)
    monkeypatch.setattr(qa, "_vence_ssl", _no)
    monkeypatch.setattr(qa, "_resolver", lambda host: ["93.184.216.34"])


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.delenv("CRM_PREVIEW", raising=False)
    r = str(tmp_path / "qa.db")
    init_db(r)
    return r


# ── un sitio falso ───────────────────────────────────────────────────────────

BIEN = """<!doctype html><html lang="es"><head><title>{titulo}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Herramientas en Salto">
<meta property="og:image" content="/og.jpg"><link rel="icon" href="/favicon.png">
<script async src="https://www.googletagmanager.com/gtag/js?id=G-X"></script></head>
<body><h1>{titulo}</h1>{cuerpo}<a href="https://wa.me/59899123456">WhatsApp</a>
<footer>© 2026 Ferretería Norte</footer></body></html>"""


def _pagina(titulo, cuerpo=""):
    return BIEN.format(titulo=titulo, cuerpo=cuerpo)


class Sitio:
    """Un sitio en memoria: url -> (código, html, headers). Anota lo pedido."""

    def __init__(self, paginas, extra=None):
        self.paginas = paginas
        self.extra = extra or {}
        self.pedidos = []

    def __call__(self, url, metodo="GET"):
        self.pedidos.append((metodo, url))
        if url in self.paginas:
            codigo, html, headers = self.paginas[url]
            return {"codigo": codigo, "url": url, "texto": html if metodo == "GET" else "", "ms": 200,
                    "headers": headers, "bytes": len(html)}
        if url in self.extra:
            return {"url": url, **self.extra[url]}
        return {"codigo": 404, "url": url, "texto": "", "headers": {}, "ms": 50}


def _sitio_sano():
    return Sitio({
        "https://ferreteria.uy/": (200, _pagina("Ferretería Norte · Salto",
                                                '<a href="/contacto">Contacto</a><a href="/productos">P</a>'
                                                '<img src="/logo.png" alt="Logo">'), {}),
        "https://ferreteria.uy/contacto": (200, _pagina("Contacto · Ferretería Norte", "<form></form>"), {}),
        "https://ferreteria.uy/productos": (200, _pagina("Productos · Ferretería Norte"), {}),
        "https://ferreteria.uy/robots.txt": (200, "User-agent: *\nDisallow: /wp-admin/\n"
                                                   "Sitemap: https://ferreteria.uy/sitemap.xml", {}),
    }, extra={"http://ferreteria.uy/": {"codigo": 200, "url": "https://ferreteria.uy/", "headers": {}}})


def _auditar(sitio, url="https://ferreteria.uy/", **kw):
    return qa.auditar(url, obtener=sitio, vence_ssl=lambda host: datetime(2027, 1, 1), ahora=AHORA, **kw)


def _claves(resultado):
    return [h["clave"] for h in resultado["hallazgos"]]


# ── la revisión ──────────────────────────────────────────────────────────────

def test_un_sitio_sano_no_tiene_hallazgos():
    r = _auditar(_sitio_sano())
    assert _claves(r) == []
    assert [p["url"] for p in r["paginas"]] == ["https://ferreteria.uy/", "https://ferreteria.uy/contacto",
                                                "https://ferreteria.uy/productos"]
    assert qa.puntaje(r["hallazgos"], set())["puntaje"] == 100


def test_encuentra_lo_que_se_olvida_al_entregar():
    s = _sitio_sano()
    s.paginas["https://ferreteria.uy/productos"] = (200, _pagina(
        "Productos · Ferretería Norte",
        '<meta name="robots" content="noindex"><p>Haz clic en el botón Editar para cambiar este texto.</p>'
        '<a href="https://wa.me/099 123 456">wa</a><a href="https://ferreteria.hostingersite.com/ofertas">o</a>'
        '<a href="/vieja">vieja</a><img src="/foto.jpg">'), {})
    s.extra["https://ferreteria.uy/foto.jpg"] = {"codigo": 200, "headers": {"content-length": "2500000"}}
    r = _auditar(s)
    claves = _claves(r)
    for k in ("noindex", "relleno", "whatsapp_mal", "links_a_prueba", "pagina_rota", "img_sin_alt", "img_pesada"):
        assert k in claves, k
    por = {h["clave"]: h for h in r["hallazgos"]}
    assert por["pagina_rota"]["donde"] == [{"url": "https://ferreteria.uy/vieja", "extra": "Contestó 404"}]
    assert "código de país" in por["whatsapp_mal"]["donde"][0]["extra"] or "símbolos" in por["whatsapp_mal"]["donde"][0]["extra"]
    assert por["img_pesada"]["donde"][0]["extra"] == "2,5 MB"
    # Los bloqueantes van primero.
    niveles = [h["nivel"] for h in r["hallazgos"]]
    assert niveles == sorted(niveles, key=qa.NIVELES.index)


def test_el_texto_dentro_de_scripts_no_es_relleno():
    s = _sitio_sano()
    s.paginas["https://ferreteria.uy/"] = (200, _pagina("Ferretería Norte · Salto",
                                                        "<script>var x = 'lorem ipsum';</script>"), {})
    assert "relleno" not in _claves(_auditar(s))


def test_robots_y_noindex_por_cabecera():
    s = _sitio_sano()
    s.paginas["https://ferreteria.uy/robots.txt"] = (200, "User-agent: *\nDisallow: /\n", {})
    s.paginas["https://ferreteria.uy/contacto"] = (200, _pagina("Contacto · Ferretería Norte", "<form></form>"),
                                                   {"x-robots-tag": "noindex"})
    claves = _claves(_auditar(s))
    assert "robots_bloquea" in claves and "noindex" in claves


def test_robots_que_bloquea_solo_a_un_robot_no_cuenta():
    assert qa._robots_bloquea("User-agent: *\nDisallow: /\n")
    assert qa._robots_bloquea("User-agent: Googlebot\nUser-agent: *\nDisallow: /\n")
    assert not qa._robots_bloquea("User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nDisallow: /wp-admin/\n")
    assert not qa._robots_bloquea("User-agent: *\nDisallow:\n")


def test_sitio_que_no_abre():
    r = _auditar(Sitio({}), url="https://nada.uy/")
    assert _claves(r) == ["inaccesible"]
    s = Sitio({}, extra={"https://roto.uy/": {"error": "ssl"}})
    assert _claves(_auditar(s, url="https://roto.uy/")) == ["ssl_invalido"]


def test_http_sin_redirigir_y_ssl_por_vencer():
    s = _sitio_sano()
    s.extra["http://ferreteria.uy/"] = {"codigo": 200, "url": "http://ferreteria.uy/", "headers": {}}
    r = qa.auditar("https://ferreteria.uy/", obtener=s, vence_ssl=lambda host: datetime(2026, 11, 2), ahora=AHORA)
    assert {"http_sin_redirigir", "ssl_por_vencer"} <= set(_claves(r))
    assert r["ssl_vence"] == "2026-11-02 00:00:00"


def test_links_externos_rotos_pero_no_las_redes_que_bloquean_robots():
    s = _sitio_sano()
    s.paginas["https://ferreteria.uy/contacto"] = (200, _pagina(
        "Contacto · Ferretería Norte",
        '<form></form><a href="https://instagram.com/ferreteria">ig</a><a href="https://viejo.uy/x">v</a>'
        '<a href="https://noexiste.uy">n</a>'), {})
    s.extra.update({"https://instagram.com/ferreteria": {"codigo": 429, "headers": {}},
                    "https://viejo.uy/x": {"codigo": 404, "headers": {}},
                    "https://noexiste.uy": {"error": "El dominio no existe"}})
    h = {x["clave"]: x for x in _auditar(s)["hallazgos"]}["link_externo_roto"]
    assert sorted(d["url"] for d in h["donde"]) == ["https://noexiste.uy", "https://viejo.uy/x"]


def test_no_recorre_carrito_ni_admin_y_respeta_el_tope():
    links = "".join(f'<a href="/p{i}">p</a>' for i in range(40)) + '<a href="/carrito/">c</a><a href="/wp-admin/">a</a>'
    paginas = {"https://ferreteria.uy/": (200, _pagina("Ferretería Norte · Salto", links), {})}
    for i in range(40):
        paginas[f"https://ferreteria.uy/p{i}"] = (200, _pagina(f"Producto {i} · Ferretería"), {})
    s = Sitio(paginas)
    r = _auditar(s)
    assert len(r["paginas"]) == qa.MAX_PAGINAS
    assert r["cortada"] and "revision_cortada" in _claves(r)
    pedidas = {u for _, u in s.pedidos}
    assert "https://ferreteria.uy/carrito/" not in pedidas and "https://ferreteria.uy/wp-admin/" not in pedidas


def test_un_link_que_rompe_el_pedido_no_tira_la_revision():
    s = _sitio_sano()

    def explota(url, metodo="GET"):
        if url.endswith("/productos"):
            raise ValueError("URL inválida")
        return s(url, metodo)
    r = qa.auditar("https://ferreteria.uy/", obtener=explota, vence_ssl=lambda h: datetime(2027, 1, 1), ahora=AHORA)
    assert {"url": "https://ferreteria.uy/productos", "extra": "No se pudo conectar"} in \
        next(h for h in r["hallazgos"] if h["clave"] == "pagina_rota")["donde"]


def test_tope_de_tiempo_corta_la_revision():
    r = _auditar(_sitio_sano(), tiempo_max=0)
    assert r["cortada"]
    assert len(r["paginas"]) == 1


def test_titulos_repetidos_genericos_y_copyright_viejo():
    s = _sitio_sano()
    s.paginas["https://ferreteria.uy/"] = (200, _pagina("Inicio", '<a href="/contacto">c</a><a href="/productos">p</a>')
                                           .replace("© 2026", "© 2019 - 2024"), {})
    s.paginas["https://ferreteria.uy/productos"] = (200, _pagina("Contacto · Ferretería Norte"), {})
    for url, (codigo, html, h) in list(s.paginas.items()):
        s.paginas[url] = (codigo, html.replace("© 2026", "© 2019 - 2024"), h)
    r = _auditar(s)
    por = {h["clave"]: h for h in r["hallazgos"]}
    assert "titulo_generico" in por and "titulo_repetido" in por
    assert por["copyright_viejo"]["detalle"] == "Dice 2024"


def test_canonical_al_sitio_de_prueba_es_bloqueante():
    s = _sitio_sano()
    s.paginas["https://ferreteria.uy/contacto"] = (200, _pagina(
        "Contacto · Ferretería Norte",
        '<form></form><link rel="canonical" href="https://prueba-ferre.example.net/contacto">'), {})
    r = _auditar(s, url_prueba="https://prueba-ferre.example.net")
    assert "links_a_prueba" in _claves(r) and "canonical_otro" not in _claves(r)
    s2 = _sitio_sano()
    s2.paginas["https://ferreteria.uy/contacto"] = (200, _pagina(
        "Contacto · Ferretería Norte", '<form></form><link rel="canonical" href="https://otro.uy/">'), {})
    assert "canonical_otro" in _claves(_auditar(s2))


def test_contenido_mixto_y_sin_viewport():
    s = _sitio_sano()
    s.paginas["https://ferreteria.uy/"] = (200, _pagina("Ferretería Norte · Salto",
                                                        '<img src="http://ferreteria.uy/a.png" alt="">')
                                           .replace('<meta name="viewport" content="width=device-width, initial-scale=1">', ''), {})
    claves = _claves(_auditar(s))
    assert "contenido_mixto" in claves and "sin_viewport" in claves


def test_error_de_php_a_la_vista():
    s = _sitio_sano()
    s.paginas["https://ferreteria.uy/contacto"] = (200, _pagina(
        "Contacto", "<b>Warning</b>:  Undefined variable $x in /home/u1/wp-content/plugins/a.php on line 3<form></form>"), {})
    assert "error_php" in _claves(_auditar(s))


@pytest.mark.parametrize("href,mal", [
    ("https://wa.me/59899123456", False),
    ("https://wa.me/5491155554444", False),
    ("https://api.whatsapp.com/send?phone=59899123456&text=Hola", False),
    ("https://wa.me/message/ABCDEF", False),
    ("https://wa.me/", True),
    ("https://wa.me/099123456", True),
    ("https://wa.me/+59899123456", True),
    ("https://wa.me/598%2099%20123%20456", True),
    ("https://api.whatsapp.com/send?phone=5989912345", True),
    ("whatsapp://send?phone=", True),
])
def test_revisar_whatsapp(href, mal):
    assert (qa.revisar_whatsapp(href) is not None) is mal


def test_revisar_telefono():
    assert qa.revisar_telefono("tel:+59899123456") is None
    assert qa.revisar_telefono("tel:099 123 456") is None
    assert qa.revisar_telefono("tel:") is not None
    assert qa.revisar_telefono("tel:1234") is not None


# ── red interna ──────────────────────────────────────────────────────────────

def test_no_deja_revisar_la_red_interna(db, monkeypatch):
    for url in ("http://localhost:8080", "http://127.0.0.1", "http://169.254.169.254/latest",
                "http://10.0.0.5", "http://scalerics-crm.internal", "http://[fdaa::3]/", "https://sitio.uy:22/"):
        with pytest.raises(qa.NoSePuede, match="red interna"):
            qa.crear(db, {"nombre": "x", "url": url}, "test")
    monkeypatch.setattr(qa, "_resolver", lambda host: ["192.168.1.10"])
    with pytest.raises(qa.NoSePuede, match="red interna"):
        qa.crear(db, {"nombre": "x", "url": "intranet.uy"}, "test")


def test_pedir_valida_cada_salto(monkeypatch):
    import requests

    class Resp:
        status_code, is_redirect, encoding = 302, True, "utf-8"
        headers = {"Location": "http://169.254.169.254/latest/meta-data"}

        def close(self):
            pass

    pedidos = []
    monkeypatch.setattr(requests, "request", lambda metodo, url, **kw: pedidos.append(url) or Resp())
    r = _PEDIR("https://sitio.uy/")
    assert r["error"] == "La dirección apunta a una red interna"
    assert pedidos == ["https://sitio.uy/"]


# ── proyectos, checklist y estado ────────────────────────────────────────────

def test_init_db_crea_las_tablas(db):
    conn = sqlite3.connect(db)
    nombres = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"qa_proyectos", "qa_revisiones", "qa_checklist", "qa_no_aplica"} <= nombres


def test_crear_valida(db):
    with pytest.raises(qa.NoSePuede, match="nombre"):
        qa.crear(db, {"url": "tienda.uy"}, "test")
    with pytest.raises(qa.NoSePuede, match="dirección"):
        qa.crear(db, {"nombre": "Tienda", "tipo": "web"}, "test")
    with pytest.raises(qa.NoSePuede, match="tipo"):
        qa.crear(db, {"nombre": "Tienda", "tipo": "app", "url": "tienda.uy"}, "test")
    with pytest.raises(qa.NoSePuede, match="distinta"):
        qa.crear(db, {"nombre": "Tienda", "url": "tienda.uy", "url_prueba": "www.tienda.uy"}, "test")
    p = qa.crear(db, {"nombre": "Bot", "tipo": "automatizacion"}, "test")
    assert p["url"] == "" and p["estado"] == "falta_checklist"
    assert {i["clave"] for i in p["checklist"]} == {c for c, _, _ in qa.CHECKLISTS["automatizacion"]}


def _proyecto(db, tipo="web"):
    return qa.crear(db, {"nombre": "Ferretería Norte", "url": "ferreteria.uy", "tipo": tipo}, "test")["id"]


def test_del_sin_revisar_al_entregado(db):
    pid = _proyecto(db)
    assert qa.detalle(db, pid)["estado"] == "sin_revisar"
    s = _sitio_sano()
    s.paginas["https://ferreteria.uy/contacto"] = (200, _pagina("Contacto", "<p>Lorem ipsum</p><form></form>"), {})
    qa.guardar_revision(db, pid, _auditar(s), "test")
    d = qa.detalle(db, pid)
    assert d["estado"] == "bloqueado" and d["ultima"]["bloqueante"] == 1
    with pytest.raises(qa.NoSePuede, match="no está listo"):
        qa.entregar(db, pid, "test")
    qa.guardar_revision(db, pid, _auditar(_sitio_sano()), "test")
    d = qa.detalle(db, pid)
    assert d["estado"] == "falta_checklist"
    assert d["ultima"]["cambios"] == {"hay_anterior": True, "nuevos": [], "resueltos": ["relleno"],
                                      "titulos": {"relleno": qa.CATALOGO["relleno"][1]}}
    for i in d["checklist"]:
        if i["obligatorio"]:
            d = qa.marcar(db, pid, i["clave"], True, "Ana")
    assert d["estado"] == "listo"
    assert next(i for i in d["checklist"] if i["hecho"])["por"] == "Ana"
    d = qa.entregar(db, pid, "Ana")
    assert d["estado"] == "entregado" and d["entregado_por"] == "Ana"
    assert qa.entregar(db, pid, "Ana", deshacer=True)["estado"] == "listo"


def test_no_aplica_deja_de_contar_y_pide_motivo(db):
    pid = _proyecto(db)
    s = _sitio_sano()
    s.paginas["https://ferreteria.uy/"] = (200, _pagina("Ferretería Norte · Salto",
                                                        '<a href="/contacto">c</a><a href="/productos">p</a>')
                                           .replace("googletagmanager.com", "nada.example"), {})
    for p in ("contacto", "productos"):
        url = f"https://ferreteria.uy/{p}"
        s.paginas[url] = (200, s.paginas[url][1].replace("googletagmanager.com", "nada.example"), {})
    qa.guardar_revision(db, pid, _auditar(s), "test")
    assert qa.detalle(db, pid)["ultima"]["puntaje"] == 100 - qa.PESOS["aviso"]
    with pytest.raises(qa.NoSePuede, match="por qué"):
        qa.no_aplica(db, pid, "sin_analitica", "  ", "Ana")
    d = qa.no_aplica(db, pid, "sin_analitica", "El cliente no quiere medir", "Ana")
    assert d["ultima"]["puntaje"] == 100 and d["no_aplica"]["sin_analitica"]["motivo"] == "El cliente no quiere medir"
    assert qa.no_aplica(db, pid, "sin_analitica", None, "Ana")["ultima"]["puntaje"] == 96


def test_marcar_solo_puntos_del_checklist_del_tipo(db):
    pid = _proyecto(db, tipo="web")
    with pytest.raises(qa.NoSePuede):
        qa.marcar(db, pid, "compra_prueba", True, "Ana")  # es de e-commerce
    pid2 = _proyecto(db, tipo="ecommerce")
    assert qa.marcar(db, pid2, "compra_prueba", True, "Ana")["checklist_hechos"] == 1


def test_guarda_solo_las_ultimas_revisiones(db, monkeypatch):
    monkeypatch.setattr(qa, "TOPE_REVISIONES", 3)
    pid = _proyecto(db)
    r = _auditar(_sitio_sano())
    for _ in range(5):
        qa.guardar_revision(db, pid, r, "test")
    assert len(qa.detalle(db, pid)["historial"]) == 3


def test_borrar_se_lleva_todo(db):
    pid = _proyecto(db)
    qa.guardar_revision(db, pid, _auditar(_sitio_sano()), "test")
    qa.marcar(db, pid, "backup", True, "Ana")
    qa.no_aplica(db, pid, "sin_og", "no", "Ana")
    qa.borrar(db, pid)
    conn = sqlite3.connect(db)
    for t in ("qa_proyectos", "qa_revisiones", "qa_checklist", "qa_no_aplica"):
        assert conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0
    conn.close()


def test_resumen_y_texto(db):
    pid = _proyecto(db)
    qa.guardar_revision(db, pid, _auditar(_sitio_sano()), "test")
    qa.crear(db, {"nombre": "Otro", "url": "otro.uy"}, "test")
    r = qa.resumen(qa.listar(db))
    assert r["activos"] == 2 and r["por_estado"]["sin_revisar"] == 1 and r["puntaje_medio"] == 100
    texto = qa.texto_resumen(qa.detalle(db, pid))
    assert "puntaje 100/100" in texto and "Falta probar a mano" in texto


# ── rutas ────────────────────────────────────────────────────────────────────

def _usuario(db, email, paneles):
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    conn = sqlite3.connect(db)
    rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)",
                       ("rol-" + email, json.dumps(paneles))).lastrowid
    conn.execute("UPDATE users SET role_id=? WHERE id=?", (rid, uid))
    conn.commit()
    conn.close()
    return uid


def _cliente(db, paneles=("qa",)):
    uid = _usuario(db, "dev@scalerics.com", list(paneles))
    app = dashboard.create_app(db)
    app.config["TESTING"] = True
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Dev"
    return c


def test_sin_el_panel_da_403(db):
    c = _cliente(db, paneles=("clientes",))
    assert c.get("/api/qa/proyectos").status_code == 403
    assert c.post("/api/qa/proyectos", json={"nombre": "x", "url": "x.uy"}).status_code == 403


def test_sin_sesion_la_api_no_responde(db):
    app = dashboard.create_app(db)
    assert app.test_client().get("/api/qa/proyectos").status_code in (302, 401, 403)


def test_api_de_punta_a_punta(db, monkeypatch):
    c = _cliente(db)
    r = c.post("/api/qa/proyectos", json={"nombre": "Ferretería Norte", "url": "ferreteria.uy/"})
    assert r.status_code == 201
    pid = r.get_json()["proyecto"]["id"]
    monkeypatch.setattr(qa, "_pedir", _sitio_sano())
    monkeypatch.setattr(qa, "_vence_ssl", lambda host: datetime(2027, 1, 1))
    d = c.post(f"/api/qa/proyectos/{pid}/revisar").get_json()
    assert d["ok"] and d["proyecto"]["ultima"]["puntaje"] == 100 and "100/100" in d["texto"]
    assert c.post(f"/api/qa/proyectos/{pid}/checklist", json={"clave": "backup", "hecho": True}
                  ).get_json()["proyecto"]["checklist_hechos"] == 1
    assert c.post(f"/api/qa/proyectos/{pid}/checklist", json={"clave": "nada", "hecho": True}).status_code == 400
    assert c.post(f"/api/qa/proyectos/{pid}/entregar").status_code == 400
    assert c.patch(f"/api/qa/proyectos/{pid}", json={"nombre": "Ferretería del Norte"}
                   ).get_json()["proyecto"]["nombre"] == "Ferretería del Norte"
    lista = c.get("/api/qa/proyectos").get_json()
    assert lista["resumen"]["activos"] == 1 and "web" in lista["tipos"]
    assert c.get("/api/qa/proyectos/999").status_code == 404
    assert c.delete(f"/api/qa/proyectos/{pid}").get_json()["ok"]


def test_el_panel_les_llega_a_quienes_ven_proyectos(db):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO roles (name, panel_access) VALUES ('dev', ?)", (json.dumps(["projects"]),))
    conn.execute("INSERT INTO roles (name, panel_access) VALUES ('mkt', ?)", (json.dumps(["meta"]),))
    conn.execute("DELETE FROM panel_grants_aplicados WHERE panel = 'qa'")
    conn.commit()
    conn.close()
    init_db(db)
    conn = sqlite3.connect(db)
    accesos = dict(conn.execute("SELECT name, panel_access FROM roles WHERE name IN ('dev','mkt')").fetchall())
    conn.close()
    assert "qa" in json.loads(accesos["dev"]) and "qa" not in json.loads(accesos["mkt"])


def test_el_menu_tiene_el_panel(db):
    html = dashboard.DASHBOARD_HTML
    assert "id=\"nav-qa\"" in html and 'id="qa-panel"' in html and "function qaCargar" in html
    assert "if (name === 'qa') qaCargar();" in html
