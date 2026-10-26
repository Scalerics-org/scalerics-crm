"""Planificador de sitios (services/planos.py, routes/planos.py)."""

import json
import re
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services import planos as pl


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.delenv("CRM_PREVIEW", raising=False)
    r = str(tmp_path / "planos.db")
    init_db(r)
    return r


CLINICA = {"nombre": "Clínica Sonrisa", "rubro": "Odontología", "ciudad": "Salto",
           "servicios": "Implantes\nOrtodoncia\nBlanqueamiento", "whatsapp": "099 123 456", "anios": 12}


# ── familia y brief ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("rubro,familia", [
    ("Odontología", "turnos"), ("Peluquería canina", "turnos"), ("Parrilla", "gastronomia"),
    ("Ferretería", "comercio"), ("Estudio contable", "profesional"), ("Escribanía", "profesional"),
    ("Aire acondicionado", "oficios"), ("Instituto de idiomas", "educacion"), ("Gimnasio", "educacion"),
    ("", None), ("Algo raro", None),
])
def test_adivina_la_familia_por_el_rubro(rubro, familia):
    assert pl.adivinar_familia(rubro) == familia


def test_el_brief_limpia_y_completa_lo_que_falta():
    b = pl.validar_brief({**CLINICA, "servicios": "implantes, Implantes ; ortodoncia\n\n", "dominio": "https://clinica.com.uy/"})
    assert b["familia"] == "turnos"
    assert b["objetivo"] == "reservas"      # el de la familia
    assert b["servicios"] == ["Implantes", "Ortodoncia"]   # sin repetidos, con mayúscula
    assert b["dominio"] == "clinica.com.uy"
    assert pl.validar_brief({"nombre": "Bambú", "rubro": "ropa", "tipo": "ecommerce"})["objetivo"] == "compras"


@pytest.mark.parametrize("cambio,error", [
    ({"nombre": " "}, "nombre"),
    ({"rubro": "algo raro", "familia": ""}, "tipo de negocio"),
    ({"familia": "naves"}, "tipo de negocio"),
    ({"tipo": "app"}, "tipo de sitio"),
    ({"objetivo": "fama"}, "objetivo"),
    ({"tono": "grito"}, "tono"),
    ({"anios": "muchos"}, "número"),
    ({"anios": 200}, "entre"),
    ({"servicios": [f"S{i}" for i in range(pl.MAX_SERVICIOS + 1)]}, "Hasta"),
])
def test_el_brief_rechaza_lo_que_no_sirve(cambio, error):
    with pytest.raises(pl.NoSePuede, match=error):
        pl.validar_brief({**CLINICA, **cambio})


# ── el mapa generado ─────────────────────────────────────────────────────────

BRIEFS = [
    CLINICA,
    {"nombre": "Parrilla El Fogón", "rubro": "parrilla", "servicios": "Asado, Chivitos"},
    {"nombre": "Ferretería Norte", "rubro": "ferretería", "ciudad": "Rivera"},
    {"nombre": "Tienda Bambú", "rubro": "ropa", "tipo": "ecommerce", "servicios": "Remeras, Buzos"},
    {"nombre": "Estudio Pérez", "rubro": "estudio contable", "servicios": "Impuestos\nSueldos", "tono": "profesional"},
    {"nombre": "Climatiza", "rubro": "aire acondicionado", "servicios": "Instalación, Service", "ciudad": "Maldonado"},
    {"nombre": "Inglés Ya", "rubro": "instituto de idiomas", "servicios": "Inglés"},
]


@pytest.mark.parametrize("data", BRIEFS, ids=lambda d: d["nombre"])
def test_cada_familia_da_un_mapa_valido(data):
    paginas = pl.generar(pl.validar_brief(data))
    assert paginas[0]["clave"] == "inicio" and paginas[0]["slug"] == "/"
    assert pl.validar_mapa(paginas) == paginas            # lo generado ya pasa la validación
    assert len({p["slug"] for p in paginas}) == len(paginas)
    assert len({s["id"] for p in paginas for s in p["secciones"]}) == sum(len(p["secciones"]) for p in paginas)
    for p in paginas:
        assert p["h1"] and p["seo"]["titulo"] and p["seo"]["descripcion"] and p["secciones"]
        assert data["nombre"] in p["seo"]["titulo"]
    assert pl.estimar_horas(paginas)["total"] > 0


def test_con_varios_servicios_cada_uno_tiene_su_pagina_colgada_de_servicios():
    paginas = pl.generar(pl.validar_brief(CLINICA))
    hijas = [p for p in paginas if p["padre"] == "servicios"]
    assert [p["nombre"] for p in hijas] == ["Implantes", "Ortodoncia", "Blanqueamiento"]
    assert hijas[0]["slug"] == "/tratamientos/implantes"
    assert hijas[0]["seo"]["titulo"] == "Implantes en Salto | Clínica Sonrisa"
    # Con un solo servicio no se arma una página que repetiría la de servicios.
    uno = pl.generar(pl.validar_brief({**CLINICA, "servicios": "Implantes"}))
    assert not [p for p in uno if p["padre"]]


def test_el_texto_usa_el_brief_y_no_inventa_datos():
    paginas = pl.generar(pl.validar_brief(CLINICA))
    hero = paginas[0]["secciones"][0]
    assert hero["bloque"] == "hero"
    assert hero["titulo"] == "Odontología en Salto, con turno en minutos"
    assert "Más de 12 años" in hero["texto"]
    assert hero["boton"] == "Reservá tu turno"
    sin_anios = pl.generar(pl.validar_brief({**CLINICA, "anios": None}))
    assert "años" not in json.dumps(sin_anios, ensure_ascii=False)
    con_dif = pl.generar(pl.validar_brief({**CLINICA, "diferencial": "Atendemos los sábados"}))
    assert con_dif[0]["secciones"][0]["texto"] == "Atendemos los sábados."


def test_el_mismo_brief_da_siempre_el_mismo_mapa():
    b = pl.validar_brief(BRIEFS[1])
    assert pl.generar(b) == pl.generar(b)


def test_el_tono_profesional_trata_de_usted():
    paginas = pl.generar(pl.validar_brief(BRIEFS[4]))
    texto = json.dumps(paginas, ensure_ascii=False)
    for vos in ("Escribinos", "Reservá", "Pedí tu", "Conocé", "Contanos", "necesitás", "Te respondemos"):
        assert vos not in texto, vos
    assert "Escríbanos" in texto


def test_una_tienda_tiene_tienda_y_envios():
    claves = [p["clave"] for p in pl.generar(pl.validar_brief(BRIEFS[3]))]
    assert "tienda" in claves and "envios" in claves and "productos" not in claves


# ── validar el mapa que manda la pantalla ────────────────────────────────────

def _mapa():
    return pl.generar(pl.validar_brief(CLINICA))


def test_validar_mapa_pone_topes_y_rechaza_lo_desconocido():
    with pytest.raises(pl.NoSePuede, match="al menos una"):
        pl.validar_mapa([])
    with pytest.raises(pl.NoSePuede, match="Hasta"):
        pl.validar_mapa([{"nombre": f"P{i}"} for i in range(pl.MAX_PAGINAS + 1)])
    m = _mapa()
    m[1]["secciones"].append({"bloque": "<script>"})
    with pytest.raises(pl.NoSePuede, match="tipo conocido"):
        pl.validar_mapa(m)
    m = _mapa()
    m[2]["slug"] = m[1]["slug"]
    with pytest.raises(pl.NoSePuede, match="misma dirección"):
        pl.validar_mapa(m)
    m = _mapa()
    m[0]["nombre"] = ""
    with pytest.raises(pl.NoSePuede, match="no tiene nombre"):
        pl.validar_mapa(m)


def test_validar_mapa_arregla_lo_que_puede():
    m = _mapa()
    m[7]["clave"] = m[0]["clave"]                 # clave repetida: se renombra
    m[7]["slug"] = "Mis Servicios / Ácidos!!"     # dirección: se limpia
    m[3]["padre"] = "no-existe"                   # padre que no está: al menú principal
    m[4]["padre"] = m[2]["clave"]                 # nieta: sube un nivel
    m[0]["secciones"][0]["titulo"] = "x" * 999
    m[0]["secciones"][0]["items"] = [{"titulo": "", "texto": ""}, {"titulo": "Uno", "texto": "a"}, "basura"]
    out = pl.validar_mapa(m)
    assert out[7]["clave"] == "inicio-2"
    assert out[7]["slug"] == "/mis-servicios/acidos"
    assert out[3]["padre"] is None
    assert out[4]["padre"] == "servicios"
    assert len(out[0]["secciones"][0]["titulo"]) == pl.LARGO["titulo"]
    assert out[0]["secciones"][0]["items"] == [{"titulo": "Uno", "texto": "a"}]


def test_chequeos_avisan_seo_largo_repetido_y_pendientes():
    m = _mapa()
    m[1]["seo"]["titulo"] = "y" * 70
    m[2]["seo"]["titulo"] = m[3]["seo"]["titulo"]
    m[0]["seo"]["descripcion"] = ""
    textos = [c["texto"] for c in pl.chequeos(pl.validar_mapa(m))]
    assert any("70 caracteres" in t for t in textos)
    assert any("repetido" in t for t in textos)
    assert any("Inicio: falta la descripción" in t for t in textos)
    assert any("para completar con el cliente" in t for t in textos)


def test_estimar_horas_suma_bloques_paginas_y_ajustes():
    m = [{"nombre": "Inicio", "secciones": [{"bloque": "hero"}, {"bloque": "cta"}]}]
    e = pl.estimar_horas(pl.validar_mapa(m))
    assert e["armado"] == 2.5                         # 0,5 de la página + 1,5 + 0,5
    assert e["total"] == 3.0                          # +20 % redondeado a la media hora


# ── en la base ───────────────────────────────────────────────────────────────

def test_crear_listar_y_guardar(db):
    p = pl.crear(db, CLINICA, "Dev")
    assert p["estado"] == "borrador" and p["n_paginas"] == len(p["paginas"]) and len(p["token"]) >= 16
    m = p["paginas"]
    m[0]["secciones"][0]["titulo"] = "Tu sonrisa en Salto"
    p2 = pl.guardar_mapa(db, p["id"], m)
    assert p2["paginas"][0]["secciones"][0]["titulo"] == "Tu sonrisa en Salto"
    assert [x["id"] for x in pl.listar(db)] == [p["id"]]
    with pytest.raises(pl.NoSePuede, match="no existe"):
        pl.guardar_mapa(db, 999, m)


def test_editar_brief_solo_rehace_el_mapa_si_se_pide(db):
    p = pl.crear(db, CLINICA, "Dev")
    m = p["paginas"]
    m[0]["secciones"][0]["titulo"] = "Editado a mano"
    pl.guardar_mapa(db, p["id"], m)
    p2 = pl.editar_brief(db, p["id"], {**CLINICA, "whatsapp": "099 999 999"})
    assert p2["brief"]["whatsapp"] == "099 999 999"
    assert p2["paginas"][0]["secciones"][0]["titulo"] == "Editado a mano"
    p3 = pl.editar_brief(db, p["id"], {**CLINICA, "servicios": "Implantes"}, regenerar=True)
    assert p3["paginas"][0]["secciones"][0]["titulo"] != "Editado a mano"
    assert not [x for x in p3["paginas"] if x["padre"]]


def test_el_cliente_aprueba_pide_cambios_y_el_equipo_resuelve(db):
    p = pl.crear(db, CLINICA, "Dev")
    with pytest.raises(pl.NoSePuede, match="todavía no"):
        pl.responder(db, p["token"], "inicio", "aprobada")
    assert pl.marcar_enviado(db, p["id"])["estado"] == "enviado"
    with pytest.raises(pl.NoSePuede, match="qué querés cambiar"):
        pl.responder(db, p["token"], "inicio", "cambios", "")
    with pytest.raises(pl.NoSePuede, match="página no existe"):
        pl.responder(db, p["token"], "nada", "aprobada")
    with pytest.raises(pl.NoSePuede, match="página por página"):
        pl.responder(db, p["token"], "*", "cambios", "todo mal")
    r = pl.responder(db, p["token"], "inicio", "cambios", "Falta ortodoncia invisible", "Laura")
    assert r["estado"] == "cambios" and r["estado_paginas"]["inicio"] == "cambios" and r["sin_resolver"] == 1
    rid = r["respuestas"][-1]["id"]
    r = pl.resolver_respuesta(db, p["id"], rid)
    assert r["estado"] == "enviado" and r["estado_paginas"]["inicio"] == "pendiente" and r["sin_resolver"] == 0
    r = pl.responder(db, p["token"], "*", "aprobada", "", "Laura")
    assert r["estado"] == "aprobado" and r["aprobado_en"] and r["aprobadas"] == r["n_paginas"]
    # Mandarlo de nuevo no le pisa el estado.
    assert pl.marcar_enviado(db, p["id"])["estado"] == "aprobado"
    with pytest.raises(pl.NoSePuede, match="no existe"):
        pl.resolver_respuesta(db, p["id"], 99999)


def test_editar_el_mapa_recalcula_el_estado(db):
    p = pl.crear(db, CLINICA, "Dev")
    pl.marcar_enviado(db, p["id"])
    pl.responder(db, p["token"], "*", "aprobada")
    m = pl.detalle(db, p["id"])["paginas"]
    m.append({"nombre": "Blog", "secciones": [{"bloque": "hero", "titulo": "Blog"}]})
    r = pl.guardar_mapa(db, p["id"], m)
    assert r["estado"] == "enviado" and r["estado_paginas"]["blog"] == "pendiente" and not r["aprobado_en"]
    r = pl.responder(db, p["token"], "blog", "cambios", "No queremos blog")
    assert r["estado"] == "cambios" and r["sin_resolver"] == 1
    r = pl.guardar_mapa(db, p["id"], [x for x in m if x["nombre"] != "Blog"])
    assert r["estado"] == "aprobado" and r["sin_resolver"] == 0


def test_hay_tope_de_respuestas_por_plano(db, monkeypatch):
    monkeypatch.setattr(pl, "MAX_COMENTARIOS", 3)
    p = pl.crear(db, CLINICA, "Dev")
    pl.marcar_enviado(db, p["id"])
    for _ in range(3):
        pl.responder(db, p["token"], "inicio", "aprobada")
    with pytest.raises(pl.NoSePuede, match="demasiadas"):
        pl.responder(db, p["token"], "inicio", "aprobada")


def test_por_token_no_acepta_cualquier_cosa(db):
    p = pl.crear(db, CLINICA, "Dev")
    assert pl.por_token(db, p["token"])["id"] == p["id"]
    for malo in ("", "corto", "' OR 1=1 --xxxxxxxxxxxx", p["token"] + "x"):
        assert pl.por_token(db, malo) is None


def test_texto_brief_tiene_mapa_seo_y_secciones(db):
    p = pl.crear(db, CLINICA, "Dev")
    t = pl.texto_brief(p)
    assert t.startswith("# Clínica Sonrisa · mapa del sitio")
    assert "  - Implantes `/tratamientos/implantes`" in t
    assert "- Título SEO: Odontología en Salto | Clínica Sonrisa" in t
    assert "[Botón: Reservá tu turno]" in t


def test_borrar_se_lleva_las_respuestas(db):
    p = pl.crear(db, CLINICA, "Dev")
    pl.marcar_enviado(db, p["id"])
    pl.responder(db, p["token"], "inicio", "aprobada")
    pl.borrar(db, p["id"])
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM pl_respuestas").fetchone()[0] == 0
    conn.close()


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


def _app(db):
    app = dashboard.create_app(db)
    app.config["TESTING"] = True
    return app


def _cliente(db, paneles=("planos",)):
    uid = _usuario(db, "dev@scalerics.com", list(paneles))
    c = _app(db).test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Dev"
    return c


def test_la_api_pide_el_panel(db):
    c = _cliente(db, paneles=("tasks",))
    assert c.get("/api/planos").status_code == 403
    assert c.post("/api/planos", json=CLINICA).status_code == 403
    anonimo = _app(db).test_client()
    assert anonimo.get("/api/planos").status_code != 200


def test_la_api_crea_edita_y_manda(db):
    c = _cliente(db)
    r = c.post("/api/planos/vista-previa", json=CLINICA)
    assert r.status_code == 200 and r.get_json()["paginas"][0]["clave"] == "inicio"
    assert pl.listar(db) == []                         # la vista previa no guarda
    assert c.post("/api/planos", json={"nombre": ""}).status_code == 400
    r = c.post("/api/planos", json=CLINICA)
    assert r.status_code == 201
    p = r.get_json()["plano"]
    assert p["link"].endswith("/plano/" + p["token"])
    d = c.get("/api/planos").get_json()
    assert d["resumen"]["total"] == 1 and "turnos" in d["catalogo"]["familias"]
    m = p["paginas"]
    m[0]["h1"] = "Nuevo H1"
    r = c.put(f"/api/planos/{p['id']}/mapa", json={"paginas": m})
    assert r.status_code == 200 and r.get_json()["plano"]["paginas"][0]["h1"] == "Nuevo H1"
    assert c.put(f"/api/planos/{p['id']}/mapa", json={"paginas": "nada"}).status_code == 400
    assert c.put("/api/planos/999/mapa", json={"paginas": m}).status_code == 404
    r = c.put(f"/api/planos/{p['id']}/brief", json={**CLINICA, "ciudad": "Paysandú"})
    assert r.get_json()["plano"]["brief"]["ciudad"] == "Paysandú"
    assert c.post(f"/api/planos/{p['id']}/enviar").get_json()["plano"]["estado"] == "enviado"
    assert "# Clínica Sonrisa" in c.get(f"/api/planos/{p['id']}/texto").get_json()["texto"]
    assert c.delete(f"/api/planos/{p['id']}").status_code == 200
    assert c.get(f"/api/planos/{p['id']}").status_code == 404


def test_la_pagina_del_cliente(db):
    p = pl.crear(db, {**CLINICA, "nombre": "Clínica <b>Sonrisa</b>"}, "Dev")
    c = _app(db).test_client()                          # sin sesión: es pública
    assert c.get(f"/plano/{p['token']}").status_code == 404      # en borrador no se ve
    pl.marcar_enviado(db, p["id"])
    r = c.get(f"/plano/{p['token']}")
    assert r.status_code == 200
    h = r.get_data(as_text=True)
    assert "Clínica &lt;b&gt;Sonrisa&lt;/b&gt;" in h and "<b>Sonrisa</b>" not in h
    assert "<script" not in h
    assert "Aprobar todo el sitio" in h and "Implantes" in h
    assert not re.search(r"\sstyle=", h)                 # la CSP no deja estilos en línea
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "script-src" not in csp
    assert r.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert c.get("/plano/no-existe-este-token-para-nada").status_code == 404


def test_el_cliente_responde_desde_la_pagina(db):
    p = pl.crear(db, CLINICA, "Dev")
    pl.marcar_enviado(db, p["id"])
    c = _app(db).test_client()
    base = f"/plano/{p['token']}"
    r = c.post(base + "/p/inicio", data={"decision": "cambios", "comentario": "<img src=x onerror=alert(1)>", "nombre": "Laura"})
    assert r.status_code == 303 and r.headers["Location"].endswith("?ok=1#inicio")
    h = c.get(base).get_data(as_text=True)
    assert "&lt;img src=x onerror=alert(1)&gt;" in h and "<img src=x" not in h
    assert "Pediste cambios" in h
    r = c.post(base + "/p/inicio", data={"decision": "cambios", "comentario": ""})
    assert r.status_code == 303 and "error=" in r.headers["Location"]
    # La trampa para robots: no guarda nada.
    antes = len(pl.detalle(db, p["id"])["respuestas"])
    c.post(base + "/p/inicio", data={"decision": "aprobada", "web": "spam"})
    assert len(pl.detalle(db, p["id"])["respuestas"]) == antes
    r = c.post(base + "/p/*", data={"decision": "aprobada"})
    assert r.status_code == 303
    assert pl.detalle(db, p["id"])["estado"] == "aprobado"
    assert "¡Aprobaste todo!" in c.get(base).get_data(as_text=True)


def test_el_panel_esta_en_el_menu_y_en_el_html(db):
    h = _cliente(db).get("/").get_data(as_text=True)
    assert 'id="nav-planos"' in h and 'id="planos-panel"' in h
    assert "async function mpCargar()" in h and "/*PLANOS_JS*/" not in h


def test_los_roles_que_ven_proyectos_reciben_el_panel(tmp_path):
    r = str(tmp_path / "roles.db")
    init_db(r)
    conn = sqlite3.connect(r)
    conn.execute("INSERT INTO roles (name, panel_access) VALUES ('pm', ?)", (json.dumps(["projects"]),))
    conn.execute("INSERT INTO roles (name, panel_access) VALUES ('ventas', ?)", (json.dumps(["wa"]),))
    # Como en producción: una base con roles que todavía no recibió el panel nuevo.
    conn.execute("DELETE FROM panel_grants_aplicados WHERE panel = 'planos'")
    conn.commit()
    conn.close()
    init_db(r)
    conn = sqlite3.connect(r)
    roles = {n: json.loads(a) for n, a in conn.execute("SELECT name, panel_access FROM roles WHERE name IN ('pm','ventas')")}
    conn.close()
    assert "planos" in roles["pm"] and "planos" not in roles["ventas"]
