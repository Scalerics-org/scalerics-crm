"""Agentes: lo que proponen las tareas programadas del equipo por MCP
(services/agentes.py, las herramientas en services/mcp_tools.py) y el panel
que lo revisa (routes/agentes.py)."""

import json
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services import agentes as ag
from services import mcp_tools as mt
from services import oauth_tokens as ot


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_URL", "https://crm.test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.setenv("SECRET_KEY", "test")
    r = str(tmp_path / "ag.db")
    init_db(r)
    return r


def _usuario(db, nombre, email, paneles=None):
    uid = create_user(db, name=nombre, email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    if paneles is not None:
        conn = sqlite3.connect(db)
        rid = conn.execute("INSERT INTO roles (name, panel_access) VALUES (?,?)",
                           ("rol-" + email, json.dumps(paneles))).lastrowid
        conn.execute("UPDATE users SET role_id=? WHERE id=?", (rid, uid))
        conn.commit()
        conn.close()
    return {"id": uid, "name": nombre}


@pytest.fixture
def juan(db):
    return _usuario(db, "Juan", "juan@scalerics.com")


@pytest.fixture
def javier(db):
    return _usuario(db, "Javier", "javier@scalerics.com")


def _texto_resultado(r):
    return json.loads(r["content"][0]["text"])["datos"]


# ── servicio ─────────────────────────────────────────────────────────────────

def test_init_db_crea_la_tabla(db):
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='agentes_propuestas'").fetchone()
    conn.close()


def test_proponer_y_listar(db, juan):
    r = ag.proponer(db, juan, "crm", "  Arreglar el título de la home ", "Le falta la palabra clave",
                    detalle="cambia index.html", enlaces=[{"etiqueta": "PR #3", "url": "https://github.com/x/y/pull/3"}])
    assert r == {"id": r["id"], "estado": "propuesta", "repetida": False}
    lista = ag.listar(db)
    assert len(lista) == 1
    p = lista[0]
    assert p["titulo"] == "Arreglar el título de la home"
    assert p["autor_nombre"] == "Juan" and p["autor_id"] == juan["id"]
    assert p["enlaces"] == [{"etiqueta": "PR #3", "url": "https://github.com/x/y/pull/3"}]
    assert "detalle" not in p
    assert ag.ver(db, r["id"])["detalle"] == "cambia index.html"


def test_misma_clave_no_duplica(db, juan, javier):
    a = ag.proponer(db, juan, "producto", "Lista de ópticas", "Ópticas sin web en Montevideo", clave="opticas-mvd")
    b = ag.proponer(db, javier, "producto", "Otra vez ópticas", "Lo mismo de otro agente", clave="opticas-mvd")
    assert b == {"id": a["id"], "estado": "propuesta", "repetida": True}
    assert len(ag.listar(db)) == 1


@pytest.mark.parametrize("enlaces", [
    [{"url": "javascript:alert(1)"}],
    [{"url": "data:text/html,hola"}],
    [{"url": "//evil.com"}],
    [{"url": "https://"}],
    [{"url": "https://a.com", "onclick": "x"}],
    "https://a.com",
    [{"url": f"https://a.com/{i}"} for i in range(6)],
])
def test_enlaces_invalidos(db, juan, enlaces):
    with pytest.raises(ag.NoSePuede):
        ag.proponer(db, juan, "otro", "Título", "Un resumen largo", enlaces=enlaces)


def test_enlace_sin_etiqueta_usa_el_host(db, juan):
    r = ag.proponer(db, juan, "otro", "Título", "Un resumen largo", enlaces=[{"url": "https://vercel.app/x"}])
    assert ag.ver(db, r["id"])["enlaces"] == [{"etiqueta": "vercel.app", "url": "https://vercel.app/x"}]


@pytest.mark.parametrize("campos", [
    {"tipo": "inventado"},
    {"titulo": "   "},
    {"resumen": ""},
    {"titulo": "x" * (ag.LARGO_TITULO + 1)},
])
def test_proponer_rechaza(db, juan, campos):
    base = {"tipo": "otro", "titulo": "Título", "resumen": "Un resumen"}
    base.update(campos)
    with pytest.raises(ag.NoSePuede):
        ag.proponer(db, juan, **base)


def test_completar_solo_la_propia_y_sin_tocar_estado(db, juan, javier):
    pid = ag.proponer(db, juan, "crm", "Título", "Un resumen")["id"]
    with pytest.raises(ag.NoSePuede, match="propia"):
        ag.completar(db, javier, pid, detalle="me meto")
    ag.revisar(db, pid, "aceptar", "Javier", "dale, abrí la PR")
    r = ag.completar(db, juan, pid, enlaces=[{"etiqueta": "PR", "url": "https://github.com/a/b/pull/1"}])
    assert r["estado"] == "aceptada"
    p = ag.ver(db, pid)
    assert p["estado"] == "aceptada" and p["comentario"] == "dale, abrí la PR"
    assert p["enlaces"][0]["etiqueta"] == "PR"


@pytest.mark.parametrize("accion", ["descartar", "hecha"])
def test_completar_cerrada_no_se_puede(db, juan, accion):
    pid = ag.proponer(db, juan, "crm", "Título", "Un resumen")["id"]
    ag.revisar(db, pid, accion, "Javier")
    with pytest.raises(ag.NoSePuede):
        ag.completar(db, juan, pid, detalle="tarde")


def test_completar_sin_cambios(db, juan):
    pid = ag.proponer(db, juan, "crm", "Título", "Un resumen")["id"]
    with pytest.raises(ag.NoSePuede, match="nada"):
        ag.completar(db, juan, pid)


def test_revisar_comentario_vacio_no_borra_el_anterior(db, juan):
    pid = ag.proponer(db, juan, "crm", "Título", "Un resumen")["id"]
    ag.revisar(db, pid, "aceptar", "Javier", "hacelo con tests")
    ag.revisar(db, pid, "hecha", "Juan", "  ")
    p = ag.ver(db, pid)
    assert p["estado"] == "hecha" and p["comentario"] == "hacelo con tests" and p["revisado_por"] == "Juan"
    assert ag.conteos(db) == {"propuesta": 0, "aceptada": 0, "descartada": 0, "hecha": 1}


def test_revisar_accion_desconocida(db, juan):
    pid = ag.proponer(db, juan, "crm", "Título", "Un resumen")["id"]
    with pytest.raises(ag.NoSePuede):
        ag.revisar(db, pid, "borrar", "Javier")


def test_listar_filtra_por_estado(db, juan):
    a = ag.proponer(db, juan, "crm", "Uno", "Un resumen")["id"]
    ag.proponer(db, juan, "crm", "Dos", "Un resumen")
    ag.revisar(db, a, "descartar", "Javier", "no suma")
    assert [p["titulo"] for p in ag.listar(db, "descartada")] == ["Uno"]
    assert [p["titulo"] for p in ag.listar(db, "propuesta")] == ["Dos"]
    with pytest.raises(ag.NoSePuede):
        ag.listar(db, "rara")


# ── herramientas MCP ─────────────────────────────────────────────────────────

def test_proponer_por_mcp_queda_a_nombre_de_quien_llama(db, juan):
    r = mt.ejecutar(db, juan, "proponer", {"tipo": "proceso", "titulo": "Post sobre bots",
                                           "resumen": "Borrador de LinkedIn sobre el bot de WhatsApp",
                                           "detalle": "texto del post", "clave": "post-bots"})
    assert r["isError"] is False
    pid = _texto_resultado(r)["id"]
    assert ag.ver(db, pid)["autor_nombre"] == "Juan"


def test_ver_propuestas_por_mcp_trae_conteos_y_comentario(db, juan):
    pid = ag.proponer(db, juan, "crm", "Título", "Un resumen")["id"]
    ag.revisar(db, pid, "descartar", "Javier", "ya lo hicimos a mano")
    datos = _texto_resultado(mt.ejecutar(db, juan, "ver_propuestas_agentes", {}))
    assert datos["conteos"]["descartada"] == 1
    assert datos["propuestas"][0]["comentario"] == "ya lo hicimos a mano"


def test_error_del_servicio_vuelve_como_iserror(db, juan, javier):
    pid = ag.proponer(db, juan, "crm", "Título", "Un resumen")["id"]
    r = mt.ejecutar(db, javier, "completar_propuesta", {"propuesta_id": pid, "detalle": "x"})
    assert r["isError"] is True and "propia" in r["content"][0]["text"]
    r = mt.ejecutar(db, juan, "ver_propuesta_agente", {"propuesta_id": 999})
    assert r["isError"] is True


def test_enlaces_tiene_que_ser_lista_y_con_tope(db, juan):
    schema = {h["name"]: h for h in mt.listar_herramientas()}["proponer"]["inputSchema"]
    with pytest.raises(mt.ArgumentosInvalidos):
        mt.validar_argumentos(schema, {"tipo": "otro", "titulo": "abc", "resumen": "x" * 10,
                                       "enlaces": "https://a.com"})
    with pytest.raises(mt.ArgumentosInvalidos):
        mt.validar_argumentos(schema, {"tipo": "otro", "titulo": "abc", "resumen": "x" * 10,
                                       "enlaces": [{"url": "https://a.com"}] * 6})


def test_proponer_por_el_endpoint_mcp_con_detalle_largo(db, juan):
    import base64, hashlib, secrets
    app = dashboard.create_app(db)
    app.config["TESTING"] = True
    verifier = secrets.token_urlsafe(40)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    code = ot.emitir_codigo(db, juan["id"], "cli", "https://r/cb", challenge, "crm:leer", "https://crm.test/mcp")
    token = ot.canjear_codigo(db, code, "cli", "https://r/cb", verifier)["access_token"]
    # El peor caso: 20.000 caracteres que json.dumps escapa como \uXXXX.
    detalle = "ñ" * ag.LARGO_DETALLE
    cuerpo = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "proponer", "arguments": {"tipo": "tecnologia", "titulo": "Competencia",
                                          "resumen": "Qué cobran las otras agencias", "detalle": detalle}}})
    r = app.test_client().post("/mcp", data=cuerpo, content_type="application/json",
                               headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    assert r.get_json()["result"]["isError"] is False
    assert ag.listar(db, con_detalle=True)[0]["detalle"] == detalle


# ── panel ────────────────────────────────────────────────────────────────────

def _cliente(db, email, paneles=None):
    u = _usuario(db, email.split("@")[0], email, paneles)
    app = dashboard.create_app(db)
    app.config["TESTING"] = True
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = u["id"]
        s["user_name"] = u["name"]
    return c


def test_panel_sin_permiso_es_403(db, juan):
    c = _cliente(db, "vende@scalerics.com", paneles=["cola"])
    assert c.get("/api/agentes").status_code == 403
    assert c.post("/api/agentes/1/aceptar", json={}).status_code == 403


def test_panel_con_permiso_revisa(db, juan):
    pid = ag.proponer(db, juan, "crm", "Título", "Un resumen", detalle="el detalle")["id"]
    c = _cliente(db, "socio@scalerics.com", paneles=["agentes"])
    d = c.get("/api/agentes?estado=propuesta").get_json()
    assert d["conteos"]["propuesta"] == 1 and d["propuestas"][0]["id"] == pid
    assert "detalle" not in d["propuestas"][0]
    assert c.get(f"/api/agentes/{pid}").get_json()["propuesta"]["detalle"] == "el detalle"
    r = c.post(f"/api/agentes/{pid}/aceptar", json={"comentario": "dale"})
    assert r.get_json() == {"ok": True, "id": pid, "estado": "aceptada"}
    p = ag.ver(db, pid)
    assert p["revisado_por"] == "socio" and p["comentario"] == "dale"
    assert c.post(f"/api/agentes/{pid}/volar", json={}).status_code == 400
    assert c.get("/api/agentes/999").status_code == 404
    assert c.get("/api/agentes?estado=rara").status_code == 400


def test_admin_ve_el_panel(db, juan):
    c = _cliente(db, "jefe@scalerics.com")
    assert c.get("/api/agentes").status_code == 200


def test_el_html_tiene_el_panel(db):
    c = _cliente(db, "jefe@scalerics.com")
    html = c.get("/").get_data(as_text=True)
    assert 'id="agentes-panel"' in html and 'id="nav-agentes"' in html
    assert "if (name === 'agentes') { agCargarBrujula(); agCargar(); }" in html
    assert 'id="ag-brujula-txt"' in html


# ── brújula ──────────────────────────────────────────────────────────────────

def test_brujula_vacia_al_principio(db):
    assert ag.leer_brujula(db) == {"texto": "", "editado_por": None, "actualizado_en": None}


def test_brujula_se_pisa_entera(db):
    ag.guardar_brujula(db, "  Este mes: cerrar presupuestos  ", "Juan")
    b = ag.guardar_brujula(db, "Este mes: tiendas del interior", "Javier")
    assert b["texto"] == "Este mes: tiendas del interior" and b["editado_por"] == "Javier"
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM agentes_brujula").fetchone()[0] == 1
    conn.close()
    assert ag.guardar_brujula(db, "", "Juan")["texto"] == ""


@pytest.mark.parametrize("texto", [None, 5, "x" * (ag.LARGO_BRUJULA + 1)])
def test_brujula_rechaza(db, texto):
    with pytest.raises(ag.NoSePuede):
        ag.guardar_brujula(db, texto, "Juan")


def test_brujula_por_mcp(db, juan):
    ag.guardar_brujula(db, "Prioridad: Bozza", "Javier")
    assert _texto_resultado(mt.ejecutar(db, juan, "brujula", {}))["texto"] == "Prioridad: Bozza"


def test_brujula_por_el_panel(db):
    c = _cliente(db, "socio@scalerics.com", paneles=["agentes"])
    r = c.put("/api/agentes/brujula", json={"texto": "Nada de demos nuevas"})
    assert r.get_json()["texto"] == "Nada de demos nuevas" and r.get_json()["editado_por"] == "socio"
    assert c.get("/api/agentes/brujula").get_json()["texto"] == "Nada de demos nuevas"
    assert c.put("/api/agentes/brujula", json={}).status_code == 400
    otro = _cliente(db, "vende@scalerics.com", paneles=["cola"])
    assert otro.put("/api/agentes/brujula", json={"texto": "x"}).status_code == 403


def test_inspiracion_y_titulo_que_se_ajusta(db, juan):
    pid = ag.proponer(db, juan, "crm", "Portal de clientes", "Cada cliente ve cómo va su proyecto",
                      inspiracion="Linear (estado público), Vercel (previews)")["id"]
    assert ag.ver(db, pid)["inspiracion"] == "Linear (estado público), Vercel (previews)"
    ag.completar(db, juan, pid, titulo="Portal de clientes con previews en vivo")
    assert ag.ver(db, pid)["titulo"] == "Portal de clientes con previews en vivo"
    with pytest.raises(ag.NoSePuede):
        ag.proponer(db, juan, "crm", "Otro", "Un resumen largo", inspiracion="x" * 301)


def test_los_tipos_son_de_proyecto_grande():
    assert ag.TIPOS == ("crm", "producto", "tecnologia", "proceso", "otro")


def test_la_brujula_no_lleva_el_aviso_de_terceros(db, juan):
    ag.guardar_brujula(db, "Prioridad: portal de clientes", "Javier")
    r = json.loads(mt.ejecutar(db, juan, "brujula", {})["content"][0]["text"])
    assert "equipo de Scalerics" in r["aviso"] and "terceros" not in r["aviso"]
    # Lo que escriben los agentes o los leads sigue con el aviso de siempre.
    r = json.loads(mt.ejecutar(db, juan, "ver_propuestas_agentes", {})["content"][0]["text"])
    assert r["aviso"] == mt.AVISO_DATOS_DE_TERCEROS
    assert all("aviso" not in h for h in mt.listar_herramientas())


def test_quien_soy_devuelve_el_usuario_del_conector(db, juan):
    assert _texto_resultado(mt.ejecutar(db, juan, "quien_soy", {})) == {"nombre": "Juan"}
