"""Seguimiento de clientes (27/10): frentes, de quién es la pelota y desde cuándo.

Lo que importa probar es lo que motivó la sección: que se vea lo que se puede
avanzar mientras se espera al cliente, y que un cliente con todo esperando
salte como proyecto quieto.
"""

import json
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, get_business, init_db, insert_business, update_business
from services import mcp_tools as mt
from services import seguimiento as seg


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    r = str(tmp_path / "s.db")
    init_db(r)
    return r


@pytest.fixture
def jefe(db):
    uid = create_user(db, name="Juan", email="jefe@test.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    return {"id": uid, "name": "Juan"}


@pytest.fixture
def otro(db):
    uid = create_user(db, name="Ana", email="ana@test.com", phone="098",
                      password_hash=generate_password_hash("x" * 10))
    return {"id": uid, "name": "Ana"}


_tel = iter(range(800000, 899999))


def _negocio(db, nombre, etapa):
    bid = insert_business(db, {"name": nombre, "phone": f"+598{next(_tel)}"})
    update_business(db, bid, crm_status=etapa)
    return bid


def _atrasar(db, frente_id, dias):
    conn = sqlite3.connect(db)
    conn.execute("UPDATE seguimiento_frentes SET estado_desde = datetime('now', ?) WHERE id = ?",
                 (f"-{dias} days", frente_id))
    conn.commit()
    conn.close()


def _id_frente(cliente, titulo):
    return next(f["id"] for f in cliente["frentes"] if f["titulo"] == titulo)


def test_almacen_esperando_fotos_muestra_lo_que_se_puede_avanzar(db):
    almacen = _negocio(db, "Almacén Sol", "en_desarrollo")
    c = seg.guardar_frente(db, "Juan", almacen, titulo="Fotos del catálogo",
                           estado="esperando_cliente", espera="Fotos de 80 productos")
    _atrasar(db, _id_frente(c, "Fotos del catálogo"), 20)
    seg.guardar_frente(db, "Juan", almacen, titulo="Facturación", estado="nos_toca",
                       proximo_paso="Elegir proveedor de CFE")

    t = seg.tablero(db)
    assert [f["titulo"] for f in t["te_toca"]] == ["Facturación"]
    assert t["esperando"][0]["titulo"] == "Fotos del catálogo"
    assert t["esperando"][0]["dias_en_estado"] == 20
    assert t["sin_nada_nuestro"] == []


def test_cliente_con_todo_esperando_salta_como_quieto_y_va_primero(db):
    a = _negocio(db, "Aaa tranquilo", "en_desarrollo")
    seg.guardar_frente(db, "Juan", a, titulo="Web", estado="nos_toca")
    z = _negocio(db, "Zeta trabado", "cerrado")
    seg.guardar_frente(db, "Juan", z, titulo="Fotos", estado="esperando_cliente")

    t = seg.tablero(db)
    assert [c["cliente"] for c in t["sin_nada_nuestro"]] == ["Zeta trabado"]
    assert t["clientes"][0]["nombre"] == "Zeta trabado"
    assert t["clientes"][0]["sin_nada_nuestro"] is True


def test_cliente_sin_frentes_no_cuenta_como_quieto(db):
    _negocio(db, "Nuevo", "en_desarrollo")
    t = seg.tablero(db)
    assert t["sin_nada_nuestro"] == []
    assert t["clientes"][0]["frentes"] == []


def test_cambiar_estado_reinicia_dias_y_queda_en_bitacora(db):
    b = _negocio(db, "Estudio Norte", "cerrado")
    c = seg.guardar_frente(db, "Juan", b, titulo="Correo", estado="esperando_cliente")
    fid = _id_frente(c, "Correo")
    _atrasar(db, fid, 10)
    c = seg.cambiar_estado(db, "Juan", fid, "nos_toca")
    f = c["frentes"][0]
    assert f["estado"] == "nos_toca" and f["dias_en_estado"] == 0
    assert any("esperando al cliente → te toca" in x["texto"] for x in c["bitacora"])


def test_actualizar_solo_cambia_lo_que_viene_y_vacio_borra(db):
    b = _negocio(db, "Carnicería", "en_desarrollo")
    c = seg.guardar_frente(db, "Juan", b, titulo="Envíos", espera="x", proximo_paso="Probar mapa",
                           detalle="largo")
    fid = _id_frente(c, "Envíos")
    c = seg.guardar_frente(db, "Juan", b, fid, espera="")
    f = c["frentes"][0]
    assert f["espera"] is None and f["proximo_paso"] == "Probar mapa" and f["detalle"] == "largo"
    with pytest.raises(seg.NoSePuede):
        seg.guardar_frente(db, "Juan", b, fid, titulo="")


def test_hechos_no_aparecen_abiertos_pero_se_cuentan(db):
    b = _negocio(db, "Suplementos Max", "en_desarrollo")
    c = seg.guardar_frente(db, "Juan", b, titulo="Tema", estado="hecho")
    t = seg.tablero(db)
    assert t["clientes"][0]["frentes"] == [] and t["clientes"][0]["hechos"] == 1
    assert seg.ver(db, b)["frentes"][0]["estado"] == "hecho"


def test_no_se_carga_en_un_lead_ni_en_un_archivado(db):
    lead = _negocio(db, "Lead", "demo_1")
    with pytest.raises(seg.NoSePuede):
        seg.guardar_frente(db, "Juan", lead, titulo="x")
    arch = _negocio(db, "Viejo", "cerrado")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE businesses SET archivado_en = '2026-10-01' WHERE id = ?", (arch,))
    conn.commit()
    conn.close()
    with pytest.raises(seg.NoSePuede):
        seg.anotar(db, "Juan", arch, resumen="x")
    assert seg.tablero(db)["clientes"] == []


def test_frente_de_otro_cliente_no_se_toca(db):
    a = _negocio(db, "A", "cerrado")
    b = _negocio(db, "B", "cerrado")
    fid = _id_frente(seg.guardar_frente(db, "Juan", a, titulo="Web"), "Web")
    with pytest.raises(seg.NoSePuede):
        seg.guardar_frente(db, "Juan", b, fid, estado="hecho")
    with pytest.raises(seg.NoSePuede):
        seg.borrar_frente(db, "Juan", b, fid)


def test_anotar_resumen_y_bitacora(db):
    b = _negocio(db, "Pizzería Centro", "cerrado")
    c = seg.anotar(db, "Juan", b, resumen="Demo aprobada", nota="Reunión de avance")
    assert c["resumen"] == "Demo aprobada"
    assert c["bitacora"][0]["texto"] == "Reunión de avance"
    c = seg.anotar(db, "Juan", b, nota="Otra")
    assert c["resumen"] == "Demo aprobada"
    with pytest.raises(seg.NoSePuede):
        seg.anotar(db, "Juan", b)


def test_alta_cliente_desde_lead_y_nueva(db):
    lead = _negocio(db, "Carnicería El Toro", "demo_agendada")
    r = seg.alta_cliente(db, "Juan", lead_id=lead)
    assert r["id"] == lead and get_business(db, lead)["crm_status"] == "en_desarrollo"
    # Ya cliente: no lo toca.
    update_business(db, lead, crm_status="finalizado")
    seg.alta_cliente(db, "Juan", lead_id=lead)
    assert get_business(db, lead)["crm_status"] == "finalizado"

    r = seg.alta_cliente(db, "Juan", nombre="Bicicletería Sur", etapa="cerrado")
    assert r["creado"] and get_business(db, r["id"])["crm_status"] == "cerrado"


# ── conector ─────────────────────────────────────────────────────────────────

def _llamar(db, usuario, nombre, args):
    r = mt.ejecutar(db, usuario, nombre, args)
    texto = r["content"][0]["text"]
    return r["isError"], (texto if r["isError"] else json.loads(texto)["datos"])


def test_mcp_admin_carga_y_lee(db, jefe):
    b = _negocio(db, "Almacén Sol", "en_desarrollo")
    err, d = _llamar(db, jefe, "guardar_frente",
                     {"cliente_id": b, "titulo": "Facturación", "estado": "nos_toca"})
    assert not err and d["frentes"][0]["titulo"] == "Facturación"
    assert d["bitacora"][0]["autor"] == "Juan (Claude)"
    err, d = _llamar(db, jefe, "seguimiento_clientes", {})
    assert not err and d["te_toca"][0]["cliente"] == "Almacén Sol"


def test_mcp_no_admin_lee_pero_no_escribe(db, jefe, otro):
    b = _negocio(db, "Almacén Sol", "en_desarrollo")
    for nombre, args in (("guardar_frente", {"cliente_id": b, "titulo": "x"}),
                         ("anotar_cliente", {"cliente_id": b, "nota": "x"}),
                         ("alta_cliente", {"nombre": "Nuevo"}),
                         ("borrar_frente", {"cliente_id": b, "frente_id": 1})):
        err, texto = _llamar(db, otro, nombre, args)
        assert err and "admin" in texto, nombre
    err, _ = _llamar(db, otro, "seguimiento_clientes", {})
    assert not err


def test_mcp_estado_invalido_es_error_de_argumentos(db, jefe):
    b = _negocio(db, "Almacén Sol", "en_desarrollo")
    with pytest.raises(mt.ArgumentosInvalidos):
        mt.ejecutar(db, jefe, "guardar_frente", {"cliente_id": b, "titulo": "x", "estado": "trabado"})


def test_mcp_cliente_inexistente_es_error_legible(db, jefe):
    err, texto = _llamar(db, jefe, "guardar_frente", {"cliente_id": 999, "titulo": "x"})
    assert err and "999" in texto


# ── rutas ────────────────────────────────────────────────────────────────────

@pytest.fixture
def web(db, jefe):
    app = dashboard.create_app(db)
    c = app.test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = jefe["id"]
        s["user_name"] = "Juan"
    return c


def test_rutas_tablero_y_cambio_de_estado(db, web):
    b = _negocio(db, "Almacén Sol", "en_desarrollo")
    fid = _id_frente(seg.guardar_frente(db, "Juan", b, titulo="Fotos",
                                        estado="esperando_cliente"), "Fotos")
    d = web.get("/api/seguimiento").get_json()
    assert d["ok"] and d["esperando"][0]["titulo"] == "Fotos"
    r = web.put(f"/api/seguimiento/frentes/{fid}/estado", json={"estado": "nos_toca"})
    assert r.status_code == 200 and r.get_json()["frentes"][0]["estado"] == "nos_toca"
    assert web.put(f"/api/seguimiento/frentes/{fid}/estado", json={"estado": "x"}).status_code == 400
    assert web.get("/api/seguimiento/99999").status_code == 404


def test_panel_clientes_trae_la_pestana(web):
    html = web.get("/").get_data(as_text=True)
    assert 'id="cli-tab-seguimiento"' in html and "function segCargar" in html
