"""Pedidos directos: carta online con pedidos (services/pedidos.py, routes/pedidos.py)."""

import json
import re
import sqlite3
from datetime import datetime, timedelta
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services import pedidos as pd

# Sábado 24/10/2026, 21:00 en Montevideo.
AHORA = datetime(2026, 10, 24, 21, 0)


@pytest.fixture
def reloj(monkeypatch):
    estado = {"ahora": AHORA}
    monkeypatch.setattr(pd, "ahora_uy", lambda: estado["ahora"])
    monkeypatch.setattr(pd, "hoy_uy", lambda: estado["ahora"].date())
    return estado


@pytest.fixture
def db(tmp_path, monkeypatch, reloj):
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("CRM_URL", "https://crm.test")
    r = str(tmp_path / "pd.db")
    init_db(r)
    return r


def _local(db, **kw):
    datos = {"nombre": "Pizzería La Esquina", "whatsapp": "099 111 222", "horarios": "mar-dom 19:00-00:30",
             "pedido_minimo": 500, "pagos": ["efectivo", "transferencia", "mercadopago"],
             "alias_transferencia": "BROU 001234567", "link_mercadopago": "https://mpago.la/abc123", **kw}
    loc = pd.crear_local(db, datos, "test")
    lid = loc["id"]
    pizzas = pd.guardar_categoria(db, lid, {"nombre": "Pizzas"})["id"]
    bebidas = pd.guardar_categoria(db, lid, {"nombre": "Bebidas"})["id"]
    pd.guardar_producto(db, lid, {"nombre": "Muzzarella", "precio": 450, "categoria_id": pizzas,
                                  "opciones": "Tamaño: chica, grande +250\nExtras (varios): huevo +60, jamón +90"})
    pd.guardar_producto(db, lid, {"nombre": "Fainá", "precio": 120, "categoria_id": pizzas})
    pd.guardar_producto(db, lid, {"nombre": "Coca 1.5 L", "precio": 180, "categoria_id": bebidas})
    pd.guardar_zona(db, lid, {"nombre": "Pocitos", "costo": 80})
    pd.guardar_zona(db, lid, {"nombre": "Carrasco", "costo": 150, "minimo": 1200})
    return pd.local(db, lid)


def _prod(loc, nombre):
    return next(p for p in loc["productos"] if p["nombre"] == nombre)


def _zona(loc, nombre):
    return next(z for z in loc["zonas"] if z["nombre"] == nombre)


DATOS = {"cliente": "Martín Silva", "telefono": "099 123 456", "tipo": "delivery", "direccion": "Av. Brasil 2550",
         "pago": "efectivo", "paga_con": "2000"}


# ── servicio ─────────────────────────────────────────────────────────────────

def test_init_db_crea_las_tablas(db):
    conn = sqlite3.connect(db)
    nombres = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"pd_locales", "pd_zonas", "pd_categorias", "pd_productos", "pd_pedidos", "pd_items"} <= nombres


def test_horarios_se_leen_y_se_escriben_igual():
    f = pd.parse_horarios("mar-dom 11:30-15:00 19:00-00:30; lun 19:00-23:00")
    assert pd.horarios_texto(f) == "lun 19:00-23:00; mar-dom 11:30-15:00 19:00-00:30"
    assert pd.parse_horarios("sáb y dom 12-16") == [{"dia": 5, "desde": 720, "hasta": 960},
                                                     {"dia": 6, "desde": 720, "hasta": 960}]
    assert len(pd.parse_horarios("vie-lun 20:00-23:00")) == 4
    for malo in ("todos los dias", "lunes", "lun 25:00-26:00", "xyz 10:00-12:00", "lun 10:00-10:00"):
        with pytest.raises(pd.NoSePuede):
            pd.parse_horarios(malo)


def test_la_apertura_entiende_la_medianoche_y_dice_cuando_abre():
    loc = {"activo": True, "modo": "auto", "horarios": pd.parse_horarios("mar-dom 19:00-00:30")}
    def ap(s):
        return pd.estado_apertura(loc, datetime.strptime(s, "%Y-%m-%d %H:%M"))
    assert ap("2026-10-24 21:00") == {"abierto": True, "texto": "Abierto hasta las 00:30"}
    assert ap("2026-10-25 00:15")["abierto"]               # domingo de madrugada: sigue el sábado
    assert ap("2026-10-26 00:15")["abierto"]               # lunes 00:15: sigue el domingo
    assert ap("2026-10-26 00:45") == {"abierto": False, "texto": "Cerrado. Abre mañana a las 19:00"}
    assert ap("2026-10-26 12:00")["texto"] == "Cerrado. Abre mañana a las 19:00"
    assert ap("2026-10-27 18:00")["texto"] == "Cerrado. Abre hoy a las 19:00"
    assert pd.estado_apertura({**loc, "modo": "cerrado"}, AHORA)["abierto"] is False
    assert pd.estado_apertura({**loc, "modo": "abierto"}, datetime(2026, 10, 26, 10, 0))["abierto"] is True
    assert pd.estado_apertura({**loc, "activo": False}, AHORA)["abierto"] is False


def test_opciones_ida_y_vuelta():
    g = pd.parse_opciones("Tamaño: chica, grande +250\nExtras (varios): huevo +60, jamón + $1.090")
    assert g == [{"nombre": "Tamaño", "varios": False, "items": [{"nombre": "chica", "precio": 0},
                                                                 {"nombre": "grande", "precio": 250}]},
                 {"nombre": "Extras", "varios": True, "items": [{"nombre": "huevo", "precio": 60},
                                                                {"nombre": "jamón", "precio": 1090}]}]
    assert pd.parse_opciones(pd.opciones_texto(g)) == g
    with pytest.raises(pd.NoSePuede):
        pd.parse_opciones("sin dos puntos")


def test_el_local_valida_lo_que_importa(db):
    a = _local(db)
    b = pd.crear_local(db, {"nombre": "Pizzería La Esquina"}, "t")
    assert a["slug"] == "pizzeria-la-esquina" and b["slug"] == "pizzeria-la-esquina-2"
    assert a["whatsapp"] == "59899111222"
    with pytest.raises(pd.NoSePuede):
        pd.editar_local(db, b["id"], {"slug": "pizzeria-la-esquina"})
    for malo in ({"whatsapp": "123"}, {"link_mercadopago": "https://evil.com/pagar"}, {"pagos": []},
                 {"delivery": False, "retiro": False}, {"modo": "siempre"}, {"demora_delivery": 0}):
        with pytest.raises(pd.NoSePuede):
            pd.editar_local(db, a["id"], malo)


def test_el_carrito_va_en_la_url_y_usa_los_precios_de_la_carta(db):
    loc = _local(db)
    muzza, faina = _prod(loc, "Muzzarella"), _prod(loc, "Fainá")
    c = pd.agregar(loc, "", muzza["id"], 2, [(0, 1), (1, 0), (1, 1)])
    assert c == f"{muzza['id']}*2-0.1-1.0-1.1"
    c = pd.agregar(loc, c, faina["id"], 1, [])
    c = pd.agregar(loc, c, muzza["id"], 1, [(1, 1), (0, 1), (1, 0)])   # el mismo armado: se suma
    lineas = pd.leer_carrito(loc, c)
    assert [(ln["nombre"], ln["cantidad"], ln["precio_unit"]) for ln in lineas] == [
        ("Muzzarella", 3, 450 + 250 + 60 + 90), ("Fainá", 1, 120)]
    assert lineas[0]["opciones"] == "grande · Extras: huevo, jamón"
    # Sin elegir, el grupo "elige uno" queda en la primera opción.
    assert pd.leer_carrito(loc, f"{muzza['id']}*1")[0]["opciones"] == "chica"
    # Basura, productos de otro local y cantidades locas se caen o se recortan.
    assert pd.leer_carrito(loc, "abc_999*1_" + f"{faina['id']}*900-7.7") == [
        pd.leer_carrito(loc, f"{faina['id']}*{pd.MAX_CANTIDAD}")[0]]
    c = pd.cambiar_cantidad(loc, c, 1, -1)
    assert [ln["nombre"] for ln in pd.leer_carrito(loc, c)] == ["Muzzarella"]


def test_un_producto_agotado_sale_del_carrito(db):
    loc = _local(db)
    faina = _prod(loc, "Fainá")
    c = pd.agregar(loc, "", faina["id"], 1, [])
    pd.guardar_producto(db, loc["id"], {"disponible": False}, faina["id"])
    loc = pd.local(db, loc["id"])
    assert pd.leer_carrito(loc, c) == []
    with pytest.raises(pd.NoSePuede, match="agotó"):
        pd.agregar(loc, "", faina["id"], 1, [])


def test_pedir_cobra_el_envio_de_la_zona_y_respeta_los_minimos(db):
    loc = _local(db)
    c = f"{_prod(loc, 'Muzzarella')['id']}*1"   # $450, menos que el mínimo de $500
    with pytest.raises(pd.NoSePuede, match="mínimo"):
        pd.pedir(db, loc["id"], c, {**DATOS, "zona_id": _zona(loc, "Pocitos")["id"]})
    c = f"{_prod(loc, 'Muzzarella')['id']}*2"   # $900
    with pytest.raises(pd.NoSePuede, match=r"\$ 1\.200"):
        pd.pedir(db, loc["id"], c, {**DATOS, "zona_id": _zona(loc, "Carrasco")["id"]})
    with pytest.raises(pd.NoSePuede, match="barrio"):
        pd.pedir(db, loc["id"], c, DATOS)
    p = pd.pedir(db, loc["id"], c, {**DATOS, "zona_id": _zona(loc, "Pocitos")["id"]}, "1.2.3.4")
    assert (p["subtotal"], p["envio"], p["total"], p["zona"], p["numero"]) == (900, 80, 980, "Pocitos", 1)
    assert p["telefono"] == "59899123456" and p["estado"] == "nuevo" and p["paga_con"] == 2000
    # Retirar no tiene mínimo ni envío.
    r = pd.pedir(db, loc["id"], f"{_prod(loc, 'Fainá')['id']}*1", {**DATOS, "tipo": "retiro", "pago": "transferencia"})
    assert (r["total"], r["envio"], r["numero"], r["direccion"]) == (120, 0, 2, None)


def test_pedir_valida_los_datos_del_cliente(db):
    loc = _local(db)
    c = f"{_prod(loc, 'Muzzarella')['id']}*2"
    base = {**DATOS, "zona_id": _zona(loc, "Pocitos")["id"]}
    for cambio, error in (({"telefono": "123"}, "celular"), ({"cliente": ""}, "nombre"),
                          ({"direccion": ""}, "dirección"), ({"pago": "tarjeta"}, "pagás"),
                          ({"paga_con": "500"}, "no llega")):
        with pytest.raises(pd.NoSePuede, match=error):
            pd.pedir(db, loc["id"], c, {**base, **cambio})
    with pytest.raises(pd.NoSePuede, match="vacío"):
        pd.pedir(db, loc["id"], "", base)


def test_no_se_pide_con_el_local_cerrado(db, reloj):
    loc = _local(db)
    reloj["ahora"] = datetime(2026, 10, 26, 12, 0)   # lunes
    with pytest.raises(pd.NoSePuede, match="Abre mañana"):
        pd.pedir(db, loc["id"], f"{_prod(loc, 'Fainá')['id']}*1", {**DATOS, "tipo": "retiro"})


def test_tope_de_pedidos_por_ip(db, monkeypatch):
    loc = _local(db)
    monkeypatch.setattr(pd, "MAX_POR_IP_DIA", 2)
    c = f"{_prod(loc, 'Fainá')['id']}*1"
    for _ in range(2):
        pd.pedir(db, loc["id"], c, {**DATOS, "tipo": "retiro"}, "9.9.9.9")
    with pytest.raises(pd.NoSePuede, match="muchos pedidos"):
        pd.pedir(db, loc["id"], c, {**DATOS, "tipo": "retiro"}, "9.9.9.9")
    pd.pedir(db, loc["id"], c, {**DATOS, "tipo": "retiro"}, "8.8.8.8")


def test_el_pedido_avanza_solo_hacia_adelante(db, reloj):
    loc = _local(db)
    p = pd.pedir(db, loc["id"], f"{_prod(loc, 'Muzzarella')['id']}*2",
                 {**DATOS, "zona_id": _zona(loc, "Pocitos")["id"]})
    with pytest.raises(pd.NoSePuede):
        pd.avanzar(db, loc["id"], p["id"], "listo", "Ana")
    reloj["ahora"] = AHORA + timedelta(minutes=2)
    p = pd.avanzar(db, loc["id"], p["id"], "preparando", "Ana", 30)
    assert (p["estado"], p["demora_min"], p["listo_estimado"], p["siguiente"]) == ("preparando", 30, "21:32", "listo")
    reloj["ahora"] = AHORA + timedelta(minutes=25)
    p = pd.avanzar(db, loc["id"], p["id"], "listo", "Ana")
    p = pd.avanzar(db, loc["id"], p["id"], "en_camino", "Ana")
    p = pd.avanzar(db, loc["id"], p["id"], "entregado", "Ana")
    assert p["estado"] == "entregado" and p["siguiente"] is None
    with pytest.raises(pd.NoSePuede):
        pd.avanzar(db, loc["id"], p["id"], "cancelado", "Ana", motivo="x")
    # Un retiro no pasa por "en camino".
    r = pd.pedir(db, loc["id"], f"{_prod(loc, 'Fainá')['id']}*1", {**DATOS, "tipo": "retiro"})
    for e in ("preparando", "listo"):
        r = pd.avanzar(db, loc["id"], r["id"], e, "Ana")
    assert r["siguiente"] == "entregado" and r["estado_texto"] == "Listo para retirar"
    with pytest.raises(pd.NoSePuede, match="por qué"):
        pd.avanzar(db, loc["id"], r["id"], "cancelado", "Ana")
    r = pd.avanzar(db, loc["id"], r["id"], "cancelado", "Ana", motivo="No vino a buscarlo")
    assert r["estado"] == "cancelado" and r["motivo"] == "No vino a buscarlo"


def test_el_cliente_cancela_solo_si_nadie_lo_acepto(db):
    loc = _local(db)
    c = f"{_prod(loc, 'Fainá')['id']}*1"
    a = pd.pedir(db, loc["id"], c, {**DATOS, "tipo": "retiro"})
    assert pd.cancelar_cliente(db, a["codigo"])["estado"] == "cancelado"
    b = pd.pedir(db, loc["id"], c, {**DATOS, "tipo": "retiro"})
    pd.avanzar(db, loc["id"], b["id"], "preparando", "Ana")
    with pytest.raises(pd.NoSePuede, match="WhatsApp"):
        pd.cancelar_cliente(db, b["codigo"])


def test_tablero_historial_y_metricas(db, reloj):
    loc = _local(db, comision_app=30)
    lid = loc["id"]
    muzza, faina = _prod(loc, "Muzzarella")["id"], _prod(loc, "Fainá")["id"]
    reloj["ahora"] = AHORA - timedelta(days=3)
    viejo = pd.pedir(db, lid, f"{muzza}*2", {**DATOS, "zona_id": _zona(loc, "Pocitos")["id"]})
    for e in ("preparando", "listo", "en_camino", "entregado"):
        pd.avanzar(db, lid, viejo["id"], e, "Ana")
    reloj["ahora"] = AHORA
    a = pd.pedir(db, lid, f"{faina}*1", {**DATOS, "tipo": "retiro"})          # repite el mismo celular
    b = pd.pedir(db, lid, f"{faina}*1", {**DATOS, "tipo": "retiro", "telefono": "098 000 000"})
    pd.avanzar(db, lid, b["id"], "preparando", "Ana")
    reloj["ahora"] = AHORA + timedelta(minutes=20)
    pd.avanzar(db, lid, b["id"], "listo", "Ana")
    c = pd.pedir(db, lid, f"{faina}*1", {**DATOS, "tipo": "retiro", "telefono": "097 000 000"})
    pd.avanzar(db, lid, c["id"], "cancelado", "Ana", motivo="Se agotó")
    t = pd.tablero(db, lid)
    assert [p["id"] for p in t["columnas"]["nuevo"]] == [a["id"]]
    assert [p["id"] for p in t["columnas"]["listo"]] == [b["id"]]
    assert [p["id"] for p in t["columnas"]["cerrados"]] == [c["id"]]
    assert t["hoy"] == {"pedidos": 2, "ventas": 240, "entregados": 0}
    assert [p["numero"] for p in pd.historial(db, lid, 7)] == [3, 2, 1, 1]
    assert [p["id"] for p in pd.historial(db, lid, 7, "098")] == [b["id"]]
    m = pd.metricas(db, lid, 7)
    assert (m["pedidos"], m["ventas"], m["ticket"], m["cancelados"]) == (3, 980 + 240, round(1220 / 3), 1)
    assert m["ahorro"] == round((900 + 240) * 0.30)            # sobre la comida, sin el envío
    assert (m["clientes"], m["repiten"]) == (2, 1)
    assert m["minutos_preparacion"] == 10                     # mediana de 0 y 20 minutos
    assert m["top"][0] == {"nombre": "Fainá", "cantidad": 2, "ventas": 240}
    assert m["zonas"] == [{"nombre": "Pocitos", "pedidos": 1, "ventas": 980}]
    assert sum(s["pedidos"] for s in m["serie"]) == 3 and m["horas"][21] == 3


def test_links_de_whatsapp(db):
    loc = _local(db)
    p = pd.pedir(db, loc["id"], f"{_prod(loc, 'Fainá')['id']}*1", {**DATOS, "tipo": "retiro", "pago": "transferencia"})
    al_cliente = pd.whatsapp_cliente("https://crm.test", loc, p)
    assert al_cliente.startswith("https://wa.me/59899123456?text=")
    texto = unquote(al_cliente.split("text=")[1])
    assert "#1" in texto and "BROU 001234567" in texto and f"/pedir/{loc['slug']}/p/{p['codigo']}" in texto
    al_local = unquote(pd.whatsapp_local("https://crm.test", loc, p))
    assert al_local.startswith("https://wa.me/59899111222?text=") and "1 x Fainá" in al_local


# ── rutas ────────────────────────────────────────────────────────────────────

@pytest.fixture
def app(db):
    a = dashboard.create_app(db)
    a.config["TESTING"] = True
    return a


def _login(app, db, email="dev@scalerics.com", paneles=("pedidos",)):
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


def test_la_api_pide_el_panel(app, db):
    loc = _local(db)
    anonimo = app.test_client()
    assert anonimo.get("/api/pedidos").status_code in (302, 401, 403)
    sin = _login(app, db, "otro@scalerics.com", paneles=("clientes",))
    assert sin.get("/api/pedidos").status_code == 403
    assert sin.post("/api/pedidos/locales", json={"nombre": "x"}).status_code == 403
    assert sin.get(f"/pedidos/comanda/{loc['id']}/1").status_code == 403


def test_flujo_del_equipo_por_api(app, db):
    c = _login(app, db)
    r = c.post("/api/pedidos/locales", json={"nombre": "Rotisería Doña Rosa", "whatsapp": "099 222 333"})
    assert r.status_code == 201
    loc = r.get_json()["local"]
    assert loc["link"] == "https://crm.test/pedir/rotiseria-dona-rosa"
    lid = loc["id"]
    cat = c.post(f"/api/pedidos/locales/{lid}/categorias", json={"nombre": "Tartas"}).get_json()["categoria"]
    r = c.post(f"/api/pedidos/locales/{lid}/productos", json={"nombre": "Tarta de jamón y queso", "precio": "390",
                                                             "categoria_id": cat["id"], "opciones": "Porción: entera, media"})
    assert r.status_code == 201
    pid = r.get_json()["producto"]["id"]
    assert c.patch(f"/api/pedidos/locales/{lid}/productos/{pid}", json={"disponible": False}).get_json()["producto"]["disponible"] is False
    assert c.post(f"/api/pedidos/locales/{lid}/productos", json={"nombre": "X", "precio": "abc"}).status_code == 400
    assert c.post(f"/api/pedidos/locales/{lid}/zonas", json={"nombre": "Centro", "costo": 60}).status_code == 201
    assert c.patch(f"/api/pedidos/locales/{lid}", json={"modo": "abierto", "horarios": "lun-sab 11-15"}).status_code == 200
    d = c.get(f"/api/pedidos/locales/{lid}").get_json()
    assert d["local"]["horarios_texto"] == "lun-sáb 11:00-15:00" and d["carta"][0]["nombre"] == "Tartas"
    assert c.patch(f"/api/pedidos/locales/{lid}", json={"horarios": "cuando quiero"}).status_code == 400
    assert c.get("/api/pedidos/locales/999").status_code == 404
    assert c.delete(f"/api/pedidos/locales/{lid}").status_code == 200


def test_tablero_y_comanda_por_api(app, db):
    loc = _local(db)
    p = pd.pedir(db, loc["id"], f"{_prod(loc, 'Fainá')['id']}*2", {**DATOS, "tipo": "retiro", "nota": "<b>sin sal</b>"})
    c = _login(app, db)
    t = c.get(f"/api/pedidos/locales/{loc['id']}/tablero").get_json()
    tarjeta = t["columnas"]["nuevo"][0]
    assert "ip" not in tarjeta and tarjeta["whatsapp"].startswith("https://wa.me/59899123456")
    assert tarjeta["link"] == f"https://crm.test/pedir/{loc['slug']}/p/{p['codigo']}"
    r = c.patch(f"/api/pedidos/locales/{loc['id']}/pedidos/{p['id']}", json={"estado": "preparando", "demora_min": 20})
    assert r.get_json()["pedido"]["demora_min"] == 20
    assert c.patch(f"/api/pedidos/locales/{loc['id']}/pedidos/{p['id']}", json={"estado": "entregado"}).status_code == 400
    html = c.get(f"/pedidos/comanda/{loc['id']}/{p['id']}").get_data(as_text=True)
    assert "#1" in html and "&lt;b&gt;sin sal&lt;/b&gt;" in html and "<b>sin sal</b>" not in html
    assert c.get(f"/api/pedidos/locales/{loc['id']}/metricas?dias=30").get_json()["metricas"]["pedidos"] == 1
    assert c.get(f"/api/pedidos/locales/{loc['id']}/historial?dias=abc").status_code == 400


def _redirige(r):
    assert r.status_code == 303, r.status_code
    return r.headers["Location"]


def test_el_cliente_pide_de_punta_a_punta_sin_sesion(app, db):
    loc = _local(db)
    web = app.test_client()
    r = web.get(f"/pedir/{loc['slug']}")
    assert r.status_code == 200
    h = r.get_data(as_text=True)
    assert "Muzzarella" in h and "Abierto hasta las 00:30" in h and "<script" not in h
    assert "nonce-" in r.headers["Content-Security-Policy"] and "script-src" not in r.headers["Content-Security-Policy"]
    muzza = _prod(loc, "Muzzarella")
    destino = _redirige(web.post(f"/pedir/{loc['slug']}/agregar", data={"c": "", "p": muzza["id"], "n": "2",
                                                                          "o0": "0.1", "o": ["1.0"]}))
    c = parse_qs(urlparse(destino).query)["c"][0]
    assert c == f"{muzza['id']}*2-0.1-1.0"
    h = web.get(destino).get_data(as_text=True)
    assert "Tu pedido" in h and "$ 1.520" in h and "grande · Extras: huevo" in h
    h = web.get(f"/pedir/{loc['slug']}/datos?c={c}").get_data(as_text=True)
    assert "Pocitos · envío $ 80" in h and "Confirmar pedido" in h
    # Un error vuelve a la misma página con lo que ya había escrito.
    r = web.post(f"/pedir/{loc['slug']}/confirmar", data={**DATOS, "c": c, "telefono": "12"})
    assert r.status_code == 400 and "Martín Silva" in r.get_data(as_text=True) and "celular" in r.get_data(as_text=True)
    destino = _redirige(web.post(f"/pedir/{loc['slug']}/confirmar",
                                 data={**DATOS, "c": c, "zona_id": _zona(loc, "Pocitos")["id"]}))
    assert re.fullmatch(rf"/pedir/{loc['slug']}/p/[\w-]+", destino)
    r = web.get(destino)
    h = r.get_data(as_text=True)
    assert "#1" in h and "$ 1.600" in h and "http-equiv='refresh'" in h and "Cancelar el pedido" in h
    assert "wa.me/59899111222" in h
    codigo = destino.rsplit("/", 1)[1]
    _redirige(web.post(destino + "/cancelar"))
    assert pd.por_codigo(db, codigo)["estado"] == "cancelado"


def test_la_trampa_y_los_links_que_no_existen(app, db):
    loc = _local(db)
    web = app.test_client()
    c = f"{_prod(loc, 'Fainá')['id']}*1"
    _redirige(web.post(f"/pedir/{loc['slug']}/confirmar", data={**DATOS, "tipo": "retiro", "c": c, "web": "spam"}))
    assert pd.historial(db, loc["id"]) == []
    assert web.get("/pedir/no-existe").status_code == 404
    assert web.get(f"/pedir/{loc['slug']}/p/codigo-que-no-existe").status_code == 404
    otro = pd.crear_local(db, {"nombre": "Otro local", "modo": "abierto"}, "t")
    p = pd.pedir(db, loc["id"], c, {**DATOS, "tipo": "retiro"})
    # El código de un pedido no se ve desde el link de otro local.
    assert web.get(f"/pedir/{otro['slug']}/p/{p['codigo']}").status_code == 404
    pd.editar_local(db, loc["id"], {"activo": False})
    assert web.get(f"/pedir/{loc['slug']}").status_code == 404


def test_lo_que_escribe_el_cliente_sale_escapado(app, db):
    loc = _local(db)
    web = app.test_client()
    p = pd.pedir(db, loc["id"], f"{_prod(loc, 'Fainá')['id']}*1",
                 {**DATOS, "tipo": "retiro", "cliente": "<img src=x onerror=alert(1)>", "nota": "<script>x</script>"})
    h = web.get(f"/pedir/{loc['slug']}/p/{p['codigo']}").get_data(as_text=True)
    assert "<script>x" not in h and "&lt;script&gt;" in h


def test_el_panel_esta_en_el_menu_y_en_los_roles():
    html = dashboard.DASHBOARD_HTML
    assert 'id="nav-pedidos"' in html and 'id="pedidos-panel"' in html
    assert "pdCargar()" in html and "'pedidos'" in html
    # El editor de roles es otra página: tiene que poder repartir el panel.
    with open(dashboard.__file__, encoding="utf-8") as f:
        assert "pedidos:'Pedidos directos'" in f.read()


def test_la_vista_previa_siembra_una_pizzeria_con_historia(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test")
    from scripts import preview_seed as ps
    r = str(tmp_path / "prev.db")
    ps.sembrar(r, "preview@scalerics.com", "x" * 14)
    ps.sembrar(r, "preview@scalerics.com", "x" * 14)          # dos arranques no duplican
    (loc,) = pd.locales(r)
    assert loc["slug"] == "vista-previa-pizzeria" and loc["nuevos"] == 2
    ejemplo = pd.por_codigo(r, "vista-previa-pedido")
    assert ejemplo["estado"] == "preparando" and ejemplo["local_id"] == loc["id"]
    m = pd.metricas(r, loc["id"], 30)
    assert m["pedidos"] > 100 and m["ahorro"] > 0
