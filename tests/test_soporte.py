"""Mesa de pedidos de clientes de mantenimiento (services/soporte.py, routes/soporte.py)."""

import io
import json
import re
import sqlite3
from datetime import timedelta

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services import soporte as sp


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("CRM_URL", "https://crm.test")
    monkeypatch.setenv("SOPORTE_DIR", str(tmp_path / "adjuntos"))
    r = str(tmp_path / "sp.db")
    init_db(r)
    return r


def _plan(db, **kw):
    return sp.crear_plan(db, {"nombre": "Web de Bambú", "horas_mes": 4, "precio_hora": 25, **kw}, "test")


def _pedido(db, pid, de_cliente=True, **kw):
    datos = {"titulo": "Cambiar el precio del menú", "descripcion": "El ejecutivo pasa a 390.", **kw}
    return sp.crear_pedido(db, pid, datos, "cliente" if de_cliente else "Juan", de_cliente=de_cliente)


def _envejecer(db, did, horas):
    conn = sqlite3.connect(db)
    conn.execute("UPDATE sp_pedidos SET creado_en = ? WHERE id = ?",
                 (sp._txt(sp._ahora() - timedelta(hours=horas)), did))
    conn.commit()
    conn.close()


def _f(nombre, datos=b"hola"):
    return (nombre, io.BytesIO(datos))


# ── servicio ─────────────────────────────────────────────────────────────────

def test_init_db_crea_las_tablas(db):
    conn = sqlite3.connect(db)
    nombres = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"sp_planes", "sp_pedidos", "sp_mensajes", "sp_archivos", "sp_horas"} <= nombres


def test_crear_plan_valida_y_da_un_codigo_largo(db):
    p = _plan(db)
    assert len(p["codigo"]) >= 20 and p["horas_mes"] == 4 and p["usadas"] == 0 and p["restantes"] == 4
    with pytest.raises(sp.NoSePuede):
        sp.crear_plan(db, {"nombre": "  "}, "t")
    with pytest.raises(sp.NoSePuede):
        sp.crear_plan(db, {"nombre": "X", "horas_mes": "-1"}, "t")
    with pytest.raises(sp.NoSePuede):
        sp.crear_plan(db, {"nombre": "X", "business_id": 999}, "t")
    assert sp.crear_plan(db, {"nombre": "X", "horas_mes": "2,5"}, "t")["horas_mes"] == 2.5


def test_los_pedidos_se_numeran_por_plan(db):
    a, b = _plan(db), _plan(db, nombre="Otro")
    assert [_pedido(db, a["id"])["numero"] for _ in range(3)] == [1, 2, 3]
    assert _pedido(db, b["id"])["numero"] == 1


def test_pedido_valida_titulo_tipo_y_prioridad(db):
    p = _plan(db)
    with pytest.raises(sp.NoSePuede):
        _pedido(db, p["id"], titulo="")
    with pytest.raises(sp.NoSePuede):
        _pedido(db, p["id"], tipo="hackeo")
    with pytest.raises(sp.NoSePuede):
        _pedido(db, p["id"], prioridad="ya")


def test_tope_de_pedidos_por_dia_solo_para_el_cliente(db, monkeypatch):
    monkeypatch.setattr(sp, "MAX_PEDIDOS_DIA", 2)
    p = _plan(db)
    _pedido(db, p["id"])
    _pedido(db, p["id"])
    with pytest.raises(sp.NoSePuede, match="tope"):
        _pedido(db, p["id"])
    assert _pedido(db, p["id"], de_cliente=False)["numero"] == 3


def test_sla_de_respuesta_y_resolucion_por_prioridad(db):
    p = _plan(db)
    d = _pedido(db, p["id"], prioridad="urgente")
    assert d["sla"]["estado"] == "ok" and d["sla"]["que"] == "respuesta"
    assert d["toca_responder"]
    _envejecer(db, d["id"], 3.5)
    assert sp.pedido(db, d["id"])["sla"]["estado"] == "riesgo"
    _envejecer(db, d["id"], 5)
    assert sp.pedido(db, d["id"])["sla"]["estado"] == "vencido"
    d = sp.responder(db, d["id"], "Lo vemos ya", "Juan", de_cliente=False)
    assert d["primera_respuesta_en"] and d["estado"] == "en_curso"
    assert d["sla"]["que"] == "resolución" and d["sla"]["estado"] == "ok" and d["sla"]["respuesta_tarde"]
    assert not d["toca_responder"]


def test_la_nota_interna_no_cuenta_como_respuesta_ni_la_ve_el_cliente(db):
    p = _plan(db)
    d = _pedido(db, p["id"])
    d = sp.responder(db, d["id"], "Ojo: el menú está en Elementor", "Juan", de_cliente=False, interno=True)
    assert d["primera_respuesta_en"] is None and d["estado"] == "nuevo"
    publico = sp.pedido_por_numero(db, p["id"], d["numero"])
    assert publico["mensajes"] == []
    with pytest.raises(sp.NoSePuede):
        sp.responder(db, d["id"], "x", "cliente", de_cliente=True, interno=True)


def test_esperando_pausa_el_sla_y_el_cliente_lo_reactiva(db):
    p = _plan(db)
    d = _pedido(db, p["id"], prioridad="urgente")
    d = sp.responder(db, d["id"], "¿Qué precio querés?", "Juan", de_cliente=False, estado="esperando")
    assert d["estado"] == "esperando" and d["sla"]["estado"] == "pausado"
    conn = sqlite3.connect(db)
    conn.execute("UPDATE sp_pedidos SET esperando_desde = ? WHERE id = ?",
                 (sp._txt(sp._ahora() - timedelta(hours=30)), d["id"]))
    conn.commit()
    conn.close()
    _envejecer(db, d["id"], 31)
    d = sp.responder(db, d["id"], "390", "cliente", de_cliente=True)
    # Pasaron 31 h desde que lo abrió, pero 30 fueron esperándolo: sigue en plazo (24 h).
    assert d["estado"] == "en_curso" and d["pausado_seg"] >= 30 * 3600 - 5
    assert d["sla"]["estado"] in ("ok", "riesgo") and d["toca_responder"]


def test_hecho_marca_resuelto_y_el_cliente_lo_puede_reabrir(db):
    p = _plan(db)
    d = _pedido(db, p["id"])
    d = sp.cambiar(db, d["id"], {"estado": "hecho"})
    assert d["resuelto_en"] and d["sla"]["estado"] == "cumplido"
    d = sp.responder(db, d["id"], "Quedó mal el precio", "cliente", de_cliente=True)
    assert d["estado"] == "en_curso" and d["resuelto_en"] is None
    d = sp.cambiar(db, d["id"], {"estado": "descartado"})
    with pytest.raises(sp.NoSePuede, match="cerrado"):
        sp.responder(db, d["id"], "hola", "cliente", de_cliente=True)


def test_resuelto_fuera_de_plazo(db):
    p = _plan(db)
    d = _pedido(db, p["id"], prioridad="urgente")
    _envejecer(db, d["id"], 30)
    assert sp.cambiar(db, d["id"], {"estado": "hecho"})["sla"]["estado"] == "vencido"


def test_cambiar_valida(db):
    d = _pedido(db, _plan(db)["id"])
    for datos in ({"estado": "volando"}, {"prioridad": "x"}, {"tipo": "x"}, {"titulo": " "}):
        with pytest.raises(sp.NoSePuede):
            sp.cambiar(db, d["id"], datos)
    d = sp.cambiar(db, d["id"], {"asignado": "Gonzalo", "prioridad": "baja"})
    assert d["asignado"] == "Gonzalo" and d["prioridad"] == "baja"


def test_bolsa_de_horas_del_mes_y_extra(db):
    p = _plan(db)
    d1, d2 = _pedido(db, p["id"]), _pedido(db, p["id"])
    hoy = sp.hoy_uy()
    sp.cargar_horas(db, d1["id"], "2,5", hoy.isoformat(), "Precios", "Juan")
    sp.cargar_horas(db, d2["id"], 3, None, "", "Juan")
    viejo = (hoy.replace(day=1) - timedelta(days=3)).isoformat()
    sp.cargar_horas(db, d2["id"], 10, viejo, "", "Juan")
    p = sp.plan(db, p["id"])
    assert p["usadas"] == 5.5 and p["extra"] == 1.5 and p["restantes"] == 0
    assert p["monto_extra"] == 37.5 and p["pasado"] and p["pct"] == 100
    pasado = sp.plan(db, p["id"], viejo[:7])
    assert pasado["usadas"] == 10
    assert [c["numero"] for c in sp.horas_del_mes(db, p["id"])] == [1, 2]
    assert sp.pedido(db, d2["id"])["horas"] == 13


def test_cargar_horas_valida(db):
    d = _pedido(db, _plan(db)["id"])
    for horas, fecha in ((0, None), ("x", None), (99, None), (1, "ayer"),
                         (1, (sp.hoy_uy() + timedelta(days=5)).isoformat())):
        with pytest.raises(sp.NoSePuede):
            sp.cargar_horas(db, d["id"], horas, fecha, "", "J")
    d = sp.cargar_horas(db, d["id"], 1, None, "", "J")
    pid = sp.borrar_horas(db, d["cargas"][0]["id"])
    assert pid == d["id"] and sp.pedido(db, d["id"])["horas"] == 0


def test_plan_sin_bolsa_cobra_todo_aparte(db):
    p = sp.crear_plan(db, {"nombre": "Por hora", "horas_mes": 0, "precio_hora": 30}, "t")
    d = _pedido(db, p["id"])
    sp.cargar_horas(db, d["id"], 2, None, "", "J")
    p = sp.plan(db, p["id"])
    assert p["extra"] == 2 and p["monto_extra"] == 60 and p["pasado"]


def test_resumen(db):
    p = _plan(db)
    a = _pedido(db, p["id"], prioridad="urgente")
    _envejecer(db, a["id"], 10)
    b = _pedido(db, p["id"])
    sp.responder(db, b["id"], "Dale", "J", de_cliente=False)
    c = _pedido(db, p["id"])
    sp.cambiar(db, c["id"], {"estado": "hecho"})
    r = sp.resumen(sp.pedidos(db), sp.planes(db))
    assert r["abiertos"] == 2 and r["sin_respuesta"] == 1 and r["toca_responder"] == 1
    assert r["vencidos"] == 1 and r["pct_a_tiempo"] == 100 and r["planes"] == 1


def test_orden_de_la_bandeja_pone_lo_vencido_primero(db):
    p = _plan(db)
    baja = _pedido(db, p["id"], prioridad="baja", titulo="baja")
    urg = _pedido(db, p["id"], prioridad="urgente", titulo="urgente")
    venc = _pedido(db, p["id"], prioridad="normal", titulo="vencido")
    _envejecer(db, venc["id"], 30)
    hecho = _pedido(db, p["id"], prioridad="urgente", titulo="hecho")
    sp.cambiar(db, hecho["id"], {"estado": "hecho"})
    orden = [d["titulo"] for d in sorted(sp.pedidos(db), key=sp.orden_bandeja)]
    assert orden == ["vencido", "urgente", "baja", "hecho"]
    assert baja and urg


def test_adjuntos_validan_tipo_tamano_y_se_borran_con_el_plan(db, tmp_path, monkeypatch):
    p = _plan(db)
    with pytest.raises(sp.NoSePuede, match="tipo de archivo"):
        sp.crear_pedido(db, p["id"], {"titulo": "x"}, "cliente", True, archivos=[_f("virus.exe")])
    monkeypatch.setattr(sp, "MAX_ARCHIVO", 10)
    with pytest.raises(sp.NoSePuede, match="pesa"):
        sp.crear_pedido(db, p["id"], {"titulo": "x"}, "cliente", True, archivos=[_f("a.png", b"x" * 20)])
    # Lo que falló no dejó ni el pedido ni el archivo.
    assert sp.pedidos(db) == [] and list((tmp_path / "adjuntos").iterdir()) == []
    d = sp.crear_pedido(db, p["id"], {"titulo": "x"}, "cliente", True, archivos=[_f("../../captura.png")])
    a = d["archivos"][0]
    assert a["nombre"] == "captura.png" and a["mensaje_id"] is None
    assert len(list((tmp_path / "adjuntos").iterdir())) == 1
    sp.borrar_plan(db, p["id"])
    assert list((tmp_path / "adjuntos").iterdir()) == []


def test_ruta_archivo_no_sale_de_la_carpeta(db):
    with pytest.raises(sp.NoSePuede):
        sp.ruta_archivo(db, {"ruta": "../../etc/passwd"})


def test_plan_pausado_no_abre_por_codigo_y_nuevo_link_cambia_el_codigo(db):
    p = _plan(db)
    assert sp.por_codigo(db, p["codigo"])
    q = sp.editar_plan(db, p["id"], {"nuevo_link": True})
    assert q["codigo"] != p["codigo"] and sp.por_codigo(db, p["codigo"]) is None
    sp.editar_plan(db, p["id"], {"activo": False})
    assert sp.por_codigo(db, q["codigo"]) is None
    assert sp.por_codigo(db, "") is None and sp.por_codigo(db, "x" * 100) is None


# ── rutas ────────────────────────────────────────────────────────────────────

@pytest.fixture
def app(db):
    a = dashboard.create_app(db)
    a.config["TESTING"] = True
    return a


def _login(app, db, email="dev@scalerics.com", paneles=("soporte",)):
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


def test_la_api_pide_sesion_y_panel(app, db):
    assert app.test_client().get("/api/soporte").status_code == 401
    sin = _login(app, db, "otro@scalerics.com", paneles=("clientes",))
    assert sin.get("/api/soporte").status_code == 403
    assert sin.post("/api/soporte/planes", json={"nombre": "x"}).status_code == 403
    assert sin.get("/api/soporte/archivos/1").status_code == 403


def test_flujo_del_equipo_por_api(app, db):
    c = _login(app, db)
    r = c.post("/api/soporte/planes", json={"nombre": "Web de Bambú", "horas_mes": 4, "precio_hora": 20})
    assert r.status_code == 201
    plan = r.get_json()["plan"]
    assert plan["link"] == f"https://crm.test/pedidos/{plan['codigo']}"
    r = c.post(f"/api/soporte/planes/{plan['id']}/pedidos", json={"titulo": "Llegó por WhatsApp", "prioridad": "urgente"})
    d = r.get_json()["pedido"]
    assert r.status_code == 201 and d["de_cliente"] == 0 and not d["toca_responder"]
    r = c.post(f"/api/soporte/pedidos/{d['id']}/mensajes", data={"texto": "Ya está", "estado": "hecho",
                                                                "archivos": (io.BytesIO(b"png"), "antes.png")})
    d = r.get_json()["pedido"]
    assert d["estado"] == "hecho" and d["archivos"][0]["nombre"] == "antes.png"
    assert c.post(f"/api/soporte/pedidos/{d['id']}/horas", json={"horas": "5"}).status_code == 200
    todo = c.get("/api/soporte").get_json()
    assert todo["resumen"]["pasados"] == 1 and todo["resumen"]["monto_extra"] == 20
    assert todo["planes"][0]["extra"] == 1 and "urgente" in todo["prioridades"]
    bajada = c.get(f"/api/soporte/archivos/{d['archivos'][0]['id']}")
    assert bajada.status_code == 200 and bajada.headers["Content-Type"] == "application/octet-stream"
    assert "attachment" in bajada.headers["Content-Disposition"]
    assert c.patch(f"/api/soporte/pedidos/{d['id']}", json={"estado": "x"}).status_code == 400
    assert c.get("/api/soporte/pedidos/999").status_code == 404
    assert c.delete(f"/api/soporte/planes/{plan['id']}").status_code == 200
    assert c.get("/api/soporte").get_json()["planes"] == []


def test_pagina_del_cliente_sin_sesion(app, db):
    p = _plan(db, mensaje="¡Hola Ana! <b>acá</b> nos pedís cambios")
    d = _pedido(db, p["id"], titulo="<script>alert(1)</script>")
    sp.responder(db, d["id"], "nota secreta del equipo", "Juan", de_cliente=False, interno=True)
    sp.responder(db, d["id"], "Lo hacemos hoy", "Juan", de_cliente=False)
    sp.cargar_horas(db, d["id"], 1.5, None, "Cambio de precios", "Juan")
    c = app.test_client()
    r = c.get(f"/pedidos/{p['codigo']}")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "<script>" not in html and "&lt;script&gt;" in html and "&lt;b&gt;acá" in html
    assert "1,5 h" in html and "Te quedan" in html and "Cambio de precios" in html
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "script-src" not in csp
    assert r.headers["X-Robots-Tag"].startswith("noindex") and r.headers["Cache-Control"] == "no-store"
    r = c.get(f"/pedidos/{p['codigo']}/1")
    html = r.get_data(as_text=True)
    assert "Lo hacemos hoy" in html and "nota secreta" not in html
    assert c.get(f"/pedidos/{p['codigo']}/99").status_code == 404
    assert c.get("/pedidos/no-existe").status_code == 404
    assert sp.plan(db, p["id"])["ultima_visita"]


def test_el_cliente_pide_y_contesta_desde_su_link(app, db):
    p = _plan(db)
    c = app.test_client()
    r = c.post(f"/pedidos/{p['codigo']}", data={"titulo": "Se cayó el formulario", "descripcion": "No llega nada",
                                                "tipo": "error", "prioridad": "urgente",
                                                "archivos": (io.BytesIO(b"img"), "captura.png")})
    assert r.status_code == 303 and r.headers["Location"].endswith(f"/pedidos/{p['codigo']}/1?ok=nuevo")
    d = sp.pedidos(db)[0]
    assert d["de_cliente"] == 1 and d["prioridad"] == "urgente" and d["toca_responder"]
    sp.responder(db, d["id"], "¿Desde cuándo?", "Juan", de_cliente=False, estado="esperando")
    r = c.post(f"/pedidos/{p['codigo']}/1", data={"texto": "Desde ayer"})
    assert r.status_code == 303 and "ok=1" in r.headers["Location"]
    assert sp.pedido(db, d["id"])["estado"] == "en_curso"
    r = c.post(f"/pedidos/{p['codigo']}", data={"titulo": ""})
    assert r.status_code == 303 and "error=" in r.headers["Location"]
    r = c.post(f"/pedidos/{p['codigo']}", data={"titulo": "x", "archivos": (io.BytesIO(b"x"), "a.exe")})
    assert "error=" in r.headers["Location"]
    assert c.post("/pedidos/no-existe", data={"titulo": "x"}).status_code == 404


def test_el_link_del_cliente_no_abre_la_api(app, db):
    p = _plan(db)
    c = app.test_client()
    c.get(f"/pedidos/{p['codigo']}")
    assert c.get("/api/soporte").status_code == 401
    assert c.get("/api/soporte/archivos/1").status_code == 401


def test_el_panel_esta_en_el_menu_y_en_los_paneles():
    html = dashboard.DASHBOARD_HTML
    assert 'id="nav-soporte"' in html and 'id="soporte-panel"' in html
    assert "if (name === 'soporte') spCargar();" in html
    assert re.search(r"const ALL_PANELS = \[[^\]]*'soporte'", html)
    assert "/*SP_JS*/" not in html and "async function spCargar()" in html
