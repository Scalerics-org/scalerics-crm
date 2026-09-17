"""Instagram con aprobacion: nada sale sin que una persona lo apruebe.

Ninguna prueba llama a Meta ni manda mail: la publicacion y los avisos se
reemplazan por funciones falsas.
"""

import io
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest
from PIL import Image
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services import ig_render
from services import instagram as ig
from services.ig_banco_semilla import BANCO

# Jueves 17/9/2026, 11:00 de Montevideo. La semana que viene arranca el 21.
JUEVES = datetime(2026, 9, 17, 14, 0, tzinfo=timezone.utc)
LUNES = date(2026, 9, 21)
LUNES_19_UTC = "2026-09-21 22:00"


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    ig.sembrar_banco(ruta)
    return ruta


def _sql(db, q, *a):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    try:
        filas = c.execute(q, a).fetchall()
        c.commit()
        return filas
    finally:
        c.close()


def _semana(db):
    ig.armar_semana(db, LUNES, JUEVES)
    return {p["slot"]: p for p in ig.listar_semana(db, LUNES)}


# ── imagenes ─────────────────────────────────────────────────────────────────

def test_las_imagenes_tienen_el_tamano_de_instagram():
    feed = Image.open(io.BytesIO(ig_render.dibujar({"titulo": "Hola"}, "verde", "feed")))
    historia = Image.open(io.BytesIO(ig_render.dibujar({"titulo": "Hola"}, "degradado", "historia")))
    assert (feed.format, feed.size) == ("JPEG", (1080, 1350))
    assert (historia.format, historia.size) == ("JPEG", (1080, 1920))


def test_el_carrusel_saca_una_imagen_por_diapositiva():
    imgs = ig_render.dibujar_publicacion("carrusel", [{"titulo": "a"}, {"titulo": "b"}, {"titulo": "c"}], "verde")
    assert len(imgs) == 3


@pytest.mark.parametrize("texto,esperado", [
    ("Tu web, ¿*vende* o solo *existe?*",
     [("TU", False), ("WEB,", False), ("¿VENDE", True), ("O", False), ("SOLO", False), ("EXISTE?", True)]),
    ("«Un sistema *es caro*»", [("«UN", False), ("SISTEMA", False), ("ES", True), ("CARO»", True)]),
    ("Si falta *una persona*, ¿se frena?",
     [("SI", False), ("FALTA", False), ("UNA", True), ("PERSONA,", True), ("¿SE", False), ("FRENA?", False)]),
])
def test_los_asteriscos_no_se_ven_y_marcan_el_acento(texto, esperado):
    assert ig_render._palabras(texto, True) == esperado


def test_el_feed_siempre_sale_en_la_familia_oscura():
    """Un estilo de historia pedido para el feed no rompe la grilla."""
    assert ig_render.estilos_para("imagen") == ("verde",)
    oscura = ig_render.dibujar({"titulo": "x"}, "verde", "feed")
    pedida = ig_render.dibujar({"titulo": "x"}, "degradado", "feed")
    assert Image.open(io.BytesIO(oscura)).getpixel((5, 1300)) == Image.open(io.BytesIO(pedida)).getpixel((5, 1300))


# ── banco ────────────────────────────────────────────────────────────────────

def test_el_banco_es_valido():
    assert len({b["clave"] for b in BANCO}) == len(BANCO)
    for b in BANCO:
        ig._validar_slides(b["formato"], b["slides"])
        assert b["pilar"] in ("dolor", "servicio", "objecion", "consejo")
        assert len(b["caption"]) <= 2200 and b["caption"].count("#") <= 30
    assert sum(b["formato"] == "historia" for b in BANCO) >= 10
    assert sum(b["formato"] != "historia" for b in BANCO) >= 20


def test_sembrar_dos_veces_no_duplica(db):
    assert ig.sembrar_banco(db) == 0
    assert _sql(db, "SELECT COUNT(*) n FROM ig_banco")[0]["n"] == len(BANCO)


# ── armar la semana ──────────────────────────────────────────────────────────

def test_arma_tres_de_feed_y_dos_historias_a_las_19(db):
    semana = _semana(db)
    assert set(semana) == {s for s, _, _ in ig.SLOTS}
    assert semana["feed_lun"]["programada_para"] == LUNES_19_UTC
    assert semana["feed_lun"]["formato"] == "carrusel"
    assert semana["feed_mie"]["formato"] == "imagen"
    assert semana["historia_mar"]["formato"] == "historia"
    assert all(p["estado"] == "borrador" and p["imagenes"] >= 1 for p in semana.values())
    assert len({p["clave_banco"] for p in semana.values()}) == 5


def test_armar_de_nuevo_no_duplica(db):
    _semana(db)
    assert ig.armar_semana(db, LUNES, JUEVES) == []


def test_no_arma_dias_que_ya_pasaron(db):
    jueves_noche = datetime(2026, 9, 24, 1, 0, tzinfo=timezone.utc)  # miercoles 23, 22:00 UY
    creadas = ig.armar_semana(db, LUNES, jueves_noche)
    slots = {p["slot"] for p in ig.listar_semana(db, LUNES)}
    assert len(creadas) == 2 and slots == {"historia_jue", "feed_vie"}


def test_las_imagenes_quedan_en_el_volumen(db):
    p = _semana(db)["feed_lun"]
    for n in range(1, p["imagenes"] + 1):
        with open(ig.ruta_imagen(db, p, n), "rb") as f:
            assert f.read(3) == b"\xff\xd8\xff"


# ── editar y aprobar ─────────────────────────────────────────────────────────

def test_editar_redibuja_y_una_aprobada_vuelve_a_borrador(db):
    p = _semana(db)["feed_mie"]
    ig.aprobar(db, p["id"], "Juan", JUEVES)
    slides = [{"titulo": "Otro *título*", "texto": "nuevo"}]
    q = ig.editar(db, p["id"], {"slides": slides, "caption": "Texto nuevo"}, "Juan", JUEVES)
    assert q["estado"] == "borrador" and q["aprobada_por"] is None
    assert q["version"] == p["version"] + 1 and q["slides"] == slides
    assert q["editada_por"] == "Juan"


def test_cambiar_la_fecha_no_redibuja(db):
    p = _semana(db)["feed_mie"]
    q = ig.editar(db, p["id"], {"fecha": "2026-09-24", "hora": "12:30"}, "Juan", JUEVES)
    assert q["programada_para"] == "2026-09-24 15:30"
    assert q["version"] == p["version"]


def test_guardar_sin_cambios_no_toca_la_aprobacion(db):
    p = _semana(db)["feed_mie"]
    ig.aprobar(db, p["id"], "Juan", JUEVES)
    q = ig.editar(db, p["id"], {"caption": p["caption"], "slides": p["slides"],
                                "fecha": "2026-09-23", "hora": "19:00"}, "Juan", JUEVES)
    assert q["estado"] == "aprobada"


@pytest.mark.parametrize("cambios,mensaje", [
    ({"caption": " "}, "vacío"),
    ({"caption": "x" * 2201}, "2.200"),
    ({"caption": "#a " * 31}, "hashtags"),
    ({"slides": [{"titulo": ""}]}, "título"),
    ({"slides": [{"titulo": "a"}, {"titulo": "b"}]}, "una sola"),
    ({"slides": [{"titulo": "x" * 121}]}, "largo"),
    ({"fecha": "2026-09-01"}, "futuro"),
    ({"fecha": "no-es-fecha"}, "inválida"),
    ({"estilo": "degradado"}, "estilo"),
])
def test_editar_valida(db, cambios, mensaje):
    p = _semana(db)["feed_mie"]
    with pytest.raises(ig.NoSePuede, match=mensaje):
        ig.editar(db, p["id"], cambios, "Juan", JUEVES)


def test_no_se_aprueba_con_la_fecha_vencida(db):
    p = _semana(db)["feed_lun"]
    with pytest.raises(ig.NoSePuede, match="ya pasó"):
        ig.aprobar(db, p["id"], "Juan", datetime(2026, 9, 21, 22, 0, tzinfo=timezone.utc))


def test_desaprobar_descartar_y_recuperar(db):
    p = _semana(db)["feed_lun"]
    ig.aprobar(db, p["id"], "Juan", JUEVES)
    assert ig.desaprobar(db, p["id"])["estado"] == "borrador"
    with pytest.raises(ig.NoSePuede):
        ig.desaprobar(db, p["id"])
    assert ig.descartar(db, p["id"])["estado"] == "descartada"
    with pytest.raises(ig.NoSePuede):
        ig.aprobar(db, p["id"], "Juan", JUEVES)
    assert ig.restaurar(db, p["id"])["estado"] == "borrador"


def test_otra_idea_cambia_el_contenido_y_saca_la_aprobacion(db):
    semana = _semana(db)
    p = semana["feed_lun"]
    ig.aprobar(db, p["id"], "Juan", JUEVES)
    q = ig.otra_idea(db, p["id"], JUEVES)
    assert q["clave_banco"] != p["clave_banco"]
    assert q["clave_banco"] not in {x["clave_banco"] for x in semana.values()}
    assert q["estado"] == "borrador" and q["formato"] in ("imagen", "carrusel")


def test_otra_idea_sin_banco_avisa(db):
    p = _semana(db)["historia_mar"]
    _sql(db, "UPDATE ig_banco SET usado_en = '2026-09-17 00:00'")
    with pytest.raises(ig.NoSePuede, match="No quedan ideas"):
        ig.otra_idea(db, p["id"], JUEVES)


# ── publicar ─────────────────────────────────────────────────────────────────

class _Meta:
    def __init__(self, falla=False):
        self.llamadas, self.falla = [], falla

    def __call__(self, formato, caption, urls):
        self.llamadas.append((formato, caption, urls))
        if self.falla:
            raise RuntimeError("code=9004 imagen invalida")
        return {"id": "179", "permalink": "https://instagram.com/p/abc"}


LUNES_19_05 = datetime(2026, 9, 21, 22, 5, tzinfo=timezone.utc)


def test_solo_publica_lo_aprobado_y_a_su_hora(db):
    semana = _semana(db)
    lun, mie = semana["feed_lun"], semana["feed_mie"]
    ig.aprobar(db, lun["id"], "Juan", JUEVES)
    ig.aprobar(db, mie["id"], "Juan", JUEVES)
    meta = _Meta()
    assert ig.publicar_pendientes(db, JUEVES, meta, "https://crm.test") == []
    r = ig.publicar_pendientes(db, LUNES_19_05, meta, "https://crm.test")
    assert r == [{"id": lun["id"], "estado": "publicada"}]
    formato, caption, urls = meta.llamadas[0]
    assert formato == "carrusel" and caption == lun["caption"]
    assert urls[0].startswith(f"https://crm.test/pub/ig/{lun['img_token']}/1.jpg")
    assert len(urls) == lun["imagenes"]
    p = ig.obtener(db, lun["id"])
    assert (p["estado"], p["ig_media_id"], p["permalink"]) == ("publicada", "179", "https://instagram.com/p/abc")
    # El borrador del martes nunca se toca.
    assert ig.obtener(db, semana["historia_mar"]["id"])["estado"] == "borrador"


def test_no_publica_dos_veces(db):
    p = _semana(db)["feed_lun"]
    ig.aprobar(db, p["id"], "Juan", JUEVES)
    meta = _Meta()
    ig.publicar_pendientes(db, LUNES_19_05, meta, "https://crm.test")
    ig.publicar_pendientes(db, LUNES_19_05, meta, "https://crm.test")
    assert len(meta.llamadas) == 1


def test_si_meta_falla_queda_en_error_y_avisa(db):
    p = _semana(db)["feed_lun"]
    ig.aprobar(db, p["id"], "Juan", JUEVES)
    avisos = []
    ig.publicar_pendientes(db, LUNES_19_05, _Meta(falla=True), "https://crm.test",
                           avisar=lambda pid, err: avisos.append((pid, err)))
    q = ig.obtener(db, p["id"])
    assert q["estado"] == "error" and "9004" in q["error"]
    assert avisos == [(p["id"], "code=9004 imagen invalida")]
    # Se puede volver a aprobar con otra fecha.
    ig.editar(db, p["id"], {"fecha": "2026-09-22"}, "Juan", LUNES_19_05)
    assert ig.aprobar(db, p["id"], "Juan", LUNES_19_05)["estado"] == "aprobada"


def test_una_aprobada_vieja_no_sale_tarde(db):
    p = _semana(db)["feed_lun"]
    ig.aprobar(db, p["id"], "Juan", JUEVES)
    meta = _Meta()
    ig.publicar_pendientes(db, LUNES_19_05 + timedelta(hours=8), meta, "https://crm.test")
    assert meta.llamadas == [] and ig.obtener(db, p["id"])["estado"] == "vencida"


def test_sin_credenciales_no_publica(db, monkeypatch):
    monkeypatch.delenv("META_ADS_TOKEN", raising=False)
    p = _semana(db)["feed_lun"]
    ig.aprobar(db, p["id"], "Juan", JUEVES)
    assert ig.publicar_pendientes(db, LUNES_19_05) == []
    assert ig.obtener(db, p["id"])["estado"] == "aprobada"


class _Graph:
    def __init__(self):
        self.posts = []

    def post(self, ruta, **params):
        self.posts.append((ruta, params))
        return {"id": f"c{len(self.posts)}"}

    def get(self, ruta, **params):
        if params.get("fields") == "status_code":
            return {"status_code": "FINISHED"}
        return {"permalink": "https://instagram.com/p/x"}


@pytest.mark.parametrize("formato,urls,esperado", [
    ("imagen", ["u1"], [("IG/media", {"image_url": "u1", "caption": "hola"}),
                        ("IG/media_publish", {"creation_id": "c1"})]),
    ("historia", ["u1"], [("IG/media", {"media_type": "STORIES", "image_url": "u1"}),
                          ("IG/media_publish", {"creation_id": "c1"})]),
    ("carrusel", ["u1", "u2"], [
        ("IG/media", {"image_url": "u1", "is_carousel_item": "true"}),
        ("IG/media", {"image_url": "u2", "is_carousel_item": "true"}),
        ("IG/media", {"media_type": "CAROUSEL", "children": "c1,c2", "caption": "hola"}),
        ("IG/media_publish", {"creation_id": "c3"})]),
])
def test_como_se_le_habla_a_meta(monkeypatch, formato, urls, esperado):
    g = _Graph()
    monkeypatch.setenv("INSTAGRAM_USER_ID", "IG")
    monkeypatch.setattr(ig, "_post", g.post)
    monkeypatch.setattr(ig, "_get", g.get)
    r = ig.publicar_en_meta(formato, "hola", urls, espera=lambda s: None)
    assert g.posts == esperado
    assert r["permalink"] == "https://instagram.com/p/x"


def test_si_meta_rechaza_la_imagen_no_publica(monkeypatch):
    g = _Graph()
    monkeypatch.setenv("INSTAGRAM_USER_ID", "IG")
    monkeypatch.setattr(ig, "_post", g.post)
    monkeypatch.setattr(ig, "_get", lambda ruta, **p: {"status_code": "ERROR"})
    with pytest.raises(RuntimeError, match="rechazó"):
        ig.publicar_en_meta("imagen", "hola", ["u1"], espera=lambda s: None)
    assert all(r != "IG/media_publish" for r, _ in g.posts)


# ── imagen publica ───────────────────────────────────────────────────────────

def test_la_imagen_publica_solo_existe_mientras_esta_aprobada(db):
    p = _semana(db)["feed_mie"]
    assert ig.imagen_publica(db, p["img_token"], 1) is None
    ig.aprobar(db, p["id"], "Juan", JUEVES)
    assert ig.imagen_publica(db, p["img_token"], 1)
    assert ig.imagen_publica(db, p["img_token"], 2) is None
    assert ig.imagen_publica(db, "0" * 32, 1) is None
    assert ig.imagen_publica(db, "corto", 1) is None
    ig.desaprobar(db, p["id"])
    assert ig.imagen_publica(db, p["img_token"], 1) is None


# ── rutina ───────────────────────────────────────────────────────────────────

def test_los_jueves_arma_la_semana_siguiente_una_sola_vez(db):
    avisos = []
    r = ig.rutina(db, JUEVES, avisar_semana=lambda n, lunes: avisos.append((n, lunes)))
    assert len(r["creadas"]) == 5 and avisos == [(5, LUNES)]
    r = ig.rutina(db, JUEVES + timedelta(hours=1), avisar_semana=lambda n, l: avisos.append(n))
    assert "creadas" not in r and len(avisos) == 1


def test_otros_dias_no_arma(db):
    miercoles = datetime(2026, 9, 16, 14, 0, tzinfo=timezone.utc)
    assert "creadas" not in ig.rutina(db, miercoles, avisar_semana=lambda *a: None)
    temprano = datetime(2026, 9, 17, 11, 0, tzinfo=timezone.utc)  # jueves 8:00 UY
    assert "creadas" not in ig.rutina(db, temprano, avisar_semana=lambda *a: None)


def test_avisa_cuando_quedan_pocas_ideas(db, monkeypatch):
    avisos = []
    from services import email_service as es
    monkeypatch.setattr(es, "send_instagram_banco_bajo", lambda f, h: avisos.append((f, h)))
    _sql(db, "UPDATE ig_banco SET usado_en = '2026-09-01 00:00' WHERE id > 12")
    ig.rutina(db, JUEVES, avisar_semana=lambda *a: None)
    assert len(avisos) == 1


def test_apagado_no_arranca(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_AGENTE", "off")
    monkeypatch.setattr(ig.threading, "Thread", lambda *a, **k: pytest.fail("arranco"))
    ig.start_instagram(object())


# ── rutas ────────────────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.setattr(ig, "ahora_utc", lambda: JUEVES)
    ruta = str(tmp_path / "app.db")
    init_db(ruta)
    ig.sembrar_banco(ruta)
    a = dashboard.create_app(ruta)
    a.config["TESTING"] = True
    return a


def _cli(app, email, paneles):
    db = app.config["DB_PATH"]
    uid = create_user(db, name="Juan", email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    c = sqlite3.connect(db)
    rol = c.execute("INSERT INTO roles (name, panel_access) VALUES (?, ?)",
                    (f"rol-{email}", json.dumps(paneles))).lastrowid
    c.execute("UPDATE users SET role_id = ? WHERE id = ?", (rol, uid))
    c.commit()
    c.close()
    cli = app.test_client()
    with cli.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Juan"
    return cli


def test_el_panel_pide_permiso(app):
    sin = _cli(app, "otro@scalerics.com", ["cal"])
    assert sin.get("/api/instagram/semana").status_code == 403
    assert app.test_client().get("/api/instagram/semana").status_code == 401


def test_flujo_completo_desde_el_panel(app):
    db = app.config["DB_PATH"]
    ig.armar_semana(db, LUNES, JUEVES)
    cli = _cli(app, "mkt@scalerics.com", ["instagram"])
    d = cli.get("/api/instagram/semana?semana=2026-09-23").get_json()
    assert d["semana"] == "2026-09-21" and len(d["publicaciones"]) == 5
    p = next(x for x in d["publicaciones"] if x["slot"] == "feed_mie")
    assert "img_token" not in p and p["fecha"] == "2026-09-23" and p["hora"] == "19:00"
    img = cli.get(p["imagenes_url"][0])
    assert img.status_code == 200 and img.mimetype == "image/jpeg"

    r = cli.put(f"/api/instagram/publicaciones/{p['id']}", json={"caption": "Nuevo texto"})
    assert r.get_json()["publicacion"]["caption"] == "Nuevo texto"
    r = cli.put(f"/api/instagram/publicaciones/{p['id']}", json={"caption": ""})
    assert r.status_code == 400 and "vacío" in r.get_json()["error"]

    r = cli.post(f"/api/instagram/publicaciones/{p['id']}/aprobar")
    assert r.get_json()["publicacion"]["estado"] == "aprobada"
    assert ig.obtener(db, p["id"])["aprobada_por"] == "Juan"
    assert cli.post(f"/api/instagram/publicaciones/{p['id']}/borrar").status_code == 404

    token = ig.obtener(db, p["id"])["img_token"]
    publico = app.test_client().get(f"/pub/ig/{token}/1.jpg")
    assert publico.status_code == 200 and publico.headers["Cache-Control"] == "no-store"
    assert app.test_client().get("/pub/ig/" + "0" * 32 + "/1.jpg").status_code == 404


def test_armar_semana_es_solo_para_admin(app):
    cli = _cli(app, "mkt@scalerics.com", ["instagram"])
    assert cli.post("/api/instagram/armar-semana", json={"semana": "2026-09-21"}).status_code == 403


def test_el_panel_esta_en_el_menu_y_sin_barras_invertidas():
    src = open(dashboard.__file__, encoding="utf-8").read()
    js = src[src.index("// ========== Instagram =========="):src.index("// ========== FIN Instagram ==========")]
    assert "\\" not in js
    html = dashboard.DASHBOARD_HTML
    assert html.count('id="instagram-panel"') == 1
    assert "if (name === 'instagram') igCargar();" in html
    assert 'id="nav-instagram"' in html


# ── pedidos de correccion ────────────────────────────────────────────────────

BOT = "b" * 48


def test_pedir_y_resolver_una_correccion(db):
    p = _semana(db)["feed_mie"]
    ig.aprobar(db, p["id"], "Juan", JUEVES)
    ig.pedir_correccion(db, p["id"], "Cambiá el botón por Agendá tu demo", "Juan")
    with pytest.raises(ig.NoSePuede, match="en espera"):
        ig.pedir_correccion(db, p["id"], "otra cosa", "Juan")
    [pend] = ig.pendientes(db)
    assert pend["publicacion_id"] == p["id"] and pend["slides"] == p["slides"]
    slides = [dict(p["slides"][0], cta="Agendá tu demo")]
    q = ig.resolver_correccion(db, pend["id"], {"slides": slides, "fecha": "2030-01-01"}, "Cambié el botón.")
    assert q["slides"] == slides and q["estado"] == "borrador"
    assert q["editada_por"] == "Claude" and q["programada_para"] == p["programada_para"]
    assert ig.pendientes(db) == []
    [c] = ig.correcciones_de(db, [p["id"]])[p["id"]]
    assert (c["estado"], c["respuesta"]) == ("hecha", "Cambié el botón.")
    with pytest.raises(ig.NoSePuede, match="ya no está"):
        ig.resolver_correccion(db, pend["id"], {"caption": "x"}, "")


@pytest.mark.parametrize("pedido,mensaje", [("  ", "Escribí"), ("x" * 1001, "largo")])
def test_el_pedido_se_valida(db, pedido, mensaje):
    p = _semana(db)["feed_mie"]
    with pytest.raises(ig.NoSePuede, match=mensaje):
        ig.pedir_correccion(db, p["id"], pedido, "Juan")


def test_no_se_corrige_lo_publicado(db):
    p = _semana(db)["feed_mie"]
    _sql(db, "UPDATE ig_publicaciones SET estado = 'publicada' WHERE id = ?", p["id"])
    with pytest.raises(ig.NoSePuede, match="ya no se puede"):
        ig.pedir_correccion(db, p["id"], "algo", "Juan")


def test_rechazar_pide_motivo(db):
    p = _semana(db)["feed_mie"]
    ig.pedir_correccion(db, p["id"], "hacelo en video", "Juan")
    [pend] = ig.pendientes(db)
    with pytest.raises(ig.NoSePuede, match="explicar"):
        ig.rechazar_correccion(db, pend["id"], " ")
    ig.rechazar_correccion(db, pend["id"], "Por ahora solo imágenes.")
    assert ig.correcciones_de(db, [p["id"]])[p["id"]][0]["estado"] == "no_se_pudo"


def test_el_robot_necesita_su_token(app, monkeypatch):
    cli = app.test_client()
    monkeypatch.delenv("IG_BOT_TOKEN", raising=False)
    assert cli.get("/api/instagram-bot/pendientes", headers={"x-ig-token": ""}).status_code == 403
    monkeypatch.setenv("IG_BOT_TOKEN", "corto")
    assert cli.get("/api/instagram-bot/pendientes", headers={"x-ig-token": "corto"}).status_code == 403
    monkeypatch.setenv("IG_BOT_TOKEN", BOT)
    assert cli.get("/api/instagram-bot/pendientes", headers={"x-ig-token": "c" * 48}).status_code == 403
    assert cli.get("/api/instagram-bot/pendientes", headers={"x-ig-token": BOT}).status_code == 200
    # El token del robot no abre el panel.
    assert cli.get("/api/instagram/semana", headers={"x-ig-token": BOT}).status_code == 401


def test_flujo_de_correccion_por_http(app, monkeypatch):
    monkeypatch.setenv("IG_BOT_TOKEN", BOT)
    db = app.config["DB_PATH"]
    ig.armar_semana(db, LUNES, JUEVES)
    cli = _cli(app, "mkt@scalerics.com", ["instagram"])
    p = next(x for x in ig.listar_semana(db, LUNES) if x["slot"] == "feed_mie")
    r = cli.post(f"/api/instagram/publicaciones/{p['id']}/correccion", json={"pedido": "botón: Agendá tu demo"})
    assert r.status_code == 200 and r.get_json()["correcciones"][0]["estado"] == "pendiente"
    d = cli.get("/api/instagram/semana?semana=2026-09-21").get_json()
    assert next(x for x in d["publicaciones"] if x["id"] == p["id"])["correcciones"][0]["pedido"] == "botón: Agendá tu demo"

    bot = app.test_client()
    h = {"x-ig-token": BOT}
    [pend] = bot.get("/api/instagram-bot/pendientes", headers=h).get_json()["pendientes"]
    img = bot.get(f"/api/instagram-bot/publicaciones/{p['id']}/imagen/1", headers=h)
    assert img.status_code == 200 and img.mimetype == "image/jpeg"
    malo = bot.post(f"/api/instagram-bot/correcciones/{pend['id']}/resolver", headers=h,
                    json={"cambios": {"slides": [{"titulo": ""}]}})
    assert malo.status_code == 400
    slides = [dict(pend["slides"][0], cta="Agendá tu demo")]
    r = bot.post(f"/api/instagram-bot/correcciones/{pend['id']}/resolver", headers=h,
                 json={"cambios": {"slides": slides}, "respuesta": "Listo"})
    assert r.get_json() == {"ok": True, "version": p["version"] + 1, "estado": "borrador"}


# ── vista del perfil ─────────────────────────────────────────────────────────

VIEJAS = [{"id": f"m{i}", "fecha": "2026-09-0" + str(i), "imagen": f"https://cdn/{i}.jpg",
           "permalink": None, "video": i == 2} for i in range(1, 10)]


def test_la_grilla_pone_lo_nuevo_arriba_y_completa_con_lo_publicado(db):
    semana = _semana(db)
    ig.descartar(db, semana["feed_vie"]["id"])
    g = ig.grilla(db, LUNES, JUEVES, publicadas=VIEJAS)
    assert [n["id"] for n in g["nuevas"]] == [semana["feed_mie"]["id"], semana["feed_lun"]["id"]]
    assert all(n["formato"] != "historia" for n in g["nuevas"])
    assert len(g["publicadas"]) == 7 and g["publicadas"][0]["id"] == "m1"
    assert g["error"] is None


def test_la_grilla_no_muestra_lo_de_otras_semanas_ni_lo_publicado(db):
    semana = _semana(db)
    _sql(db, "UPDATE ig_publicaciones SET estado = 'publicada' WHERE id = ?", semana["feed_lun"]["id"])
    g = ig.grilla(db, LUNES, JUEVES, publicadas=[])
    assert {n["id"] for n in g["nuevas"]} == {semana["feed_mie"]["id"], semana["feed_vie"]["id"]}
    semana_anterior = ig.grilla(db, date(2026, 9, 14), JUEVES, publicadas=[])
    assert semana_anterior["nuevas"] == []


def test_si_meta_falla_muestra_solo_lo_nuevo(db, monkeypatch):
    _semana(db)
    monkeypatch.setattr(ig, "feed_publicado", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    g = ig.grilla(db, LUNES, JUEVES)
    assert len(g["nuevas"]) == 3 and g["publicadas"] == [] and g["error"]


def test_el_feed_publicado_se_guarda_media_hora(monkeypatch):
    llamadas = []

    def traer():
        llamadas.append(1)
        return [{"id": "1", "timestamp": "2026-09-02T10:00:00+0000", "media_type": "VIDEO",
                 "thumbnail_url": "https://cdn/t.jpg", "media_url": "https://cdn/v.mp4"},
                {"id": "2", "timestamp": "2026-09-01T10:00:00+0000", "media_type": "IMAGE"}]

    monkeypatch.setattr(ig, "_CACHE_FEED", {"cuando": 0.0, "items": None})
    items = ig.feed_publicado(traer=traer)
    assert items == [{"id": "1", "fecha": "2026-09-02", "imagen": "https://cdn/t.jpg",
                      "permalink": None, "video": True}]
    assert ig.feed_publicado() == items and llamadas == [1]


def test_la_grilla_por_http(app, monkeypatch):
    db = app.config["DB_PATH"]
    ig.armar_semana(db, LUNES, JUEVES)
    monkeypatch.setattr(ig, "feed_publicado", lambda *a, **k: VIEJAS)
    cli = _cli(app, "mkt@scalerics.com", ["instagram"])
    d = cli.get("/api/instagram/grilla?semana=2026-09-21").get_json()
    assert len(d["nuevas"]) == 3 and len(d["publicadas"]) == 6
    assert d["nuevas"][0]["imagen"].startswith("/api/instagram/publicaciones/")
    assert cli.get("/api/instagram/grilla?semana=mal").status_code == 400
    assert _cli(app, "otro@scalerics.com", ["cal"]).get("/api/instagram/grilla").status_code == 403
