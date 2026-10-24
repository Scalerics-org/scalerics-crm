"""Monitor de sitios de clientes (services/monitor_sitios.py, routes/monitor_sitios.py)."""

import json
import sqlite3
from datetime import datetime, timedelta

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from routes import monitor_sitios as rutas
from services import monitor_sitios as ms

T0 = datetime(2026, 10, 10, 12, 0, 0)  # UTC: 9:00 en Montevideo
_OBTENER = ms._obtener  # el de verdad, antes de que la fixture lo tape


@pytest.fixture(autouse=True)
def _sin_red(monkeypatch):
    """Ningún test sale a internet: el GET, el SSL y el DNS van falsos."""
    def _no(*a, **k):
        raise AssertionError("Un test intentó salir a internet desde el monitor")
    monkeypatch.setattr(ms, "_obtener", _no)
    monkeypatch.setattr(ms, "_vence_ssl", _no)
    monkeypatch.setattr(ms, "_resolver", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(rutas, "_primer_chequeo", lambda db, sid: None)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("CRM_URL", "https://crm.test")
    monkeypatch.delenv("CRM_PREVIEW", raising=False)
    monkeypatch.delenv("MONITOR_TICK_TOKEN", raising=False)
    r = str(tmp_path / "mon.db")
    init_db(r)
    return r


def _sitio(db, **kw):
    datos = {"nombre": "Tienda Bambú", "url": "tiendabambu.com.uy", "plan": "mantenimiento", **kw}
    s = ms.crear(db, datos, "test")
    if "creado_en" not in kw:
        conn = sqlite3.connect(db)
        conn.execute("UPDATE monitor_sitios SET creado_en = ? WHERE id = ?",
                     (ms._txt(T0 - timedelta(days=40)), s["id"]))
        conn.commit()
        conn.close()
    return s["id"]


BIEN = {"ok": True, "codigo": 200, "ms": 400, "error": None}
MAL = {"ok": False, "codigo": 500, "ms": 900, "error": "Contestó 500"}


# ── tablas y URLs ────────────────────────────────────────────────────────────

def test_init_db_crea_las_tablas(db):
    conn = sqlite3.connect(db)
    nombres = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"monitor_sitios", "monitor_chequeos", "monitor_incidentes", "monitor_dias", "monitor_notas"} <= nombres


def test_normalizar_url():
    assert ms.normalizar_url("  tienda.com.uy ") == "https://tienda.com.uy"
    assert ms.normalizar_url("http://tienda.com.uy/carrito") == "http://tienda.com.uy/carrito"
    for mala in ("", "ftp://tienda.com.uy", "https://", "https://yo:clave@tienda.com.uy"):
        with pytest.raises(ms.NoSePuede):
            ms.normalizar_url(mala)


def test_no_deja_vigilar_la_red_interna(db, monkeypatch):
    for url in ("http://localhost:8080", "http://127.0.0.1", "http://169.254.169.254/latest",
                "http://10.0.0.5", "http://scalerics-crm.internal", "http://[fdaa::3]/"):
        with pytest.raises(ms.NoSePuede, match="red interna"):
            ms.crear(db, {"nombre": "x", "url": url}, "test")
    monkeypatch.setattr(ms, "_resolver", lambda host: ["192.168.1.10"])
    with pytest.raises(ms.NoSePuede, match="red interna"):
        ms.crear(db, {"nombre": "x", "url": "apunta-adentro.com"}, "test")


def test_una_redireccion_a_la_red_interna_no_se_sigue(monkeypatch):
    import requests
    llamadas = []

    class Redirige:
        status_code = 301
        is_redirect = True
        headers = {"Location": "http://169.254.169.254/latest/meta-data"}

        def close(self):
            pass

    monkeypatch.setattr(requests, "get", lambda url, **kw: llamadas.append(url) or Redirige())
    r = _OBTENER("https://tienda.com.uy")
    assert r == {"error": "La dirección apunta a una red interna"}
    assert llamadas == ["https://tienda.com.uy"]


def test_el_mismo_sitio_no_se_carga_dos_veces(db):
    _sitio(db)
    with pytest.raises(ms.NoSePuede, match="ya está"):
        ms.crear(db, {"nombre": "Otra", "url": "https://tiendabambu.com.uy"}, "test")


# ── evaluar una respuesta ────────────────────────────────────────────────────

def test_evaluar():
    assert ms.evaluar({"codigo": 200, "texto": "<h1>Hola</h1>", "ms": 300})["ok"]
    assert ms.evaluar({"codigo": 301, "texto": "", "ms": 300})["ok"]
    r = ms.evaluar({"codigo": 503, "texto": "", "ms": 300})
    assert not r["ok"] and r["error"] == "Contestó 503"
    r = ms.evaluar({"error": "No contestó en 15 s"})
    assert not r["ok"] and r["ms"] is None and r["error"] == "No contestó en 15 s"


def test_un_wordpress_roto_con_200_esta_caido():
    r = ms.evaluar({"codigo": 200, "ms": 200,
                    "texto": "<p>Error establishing a database connection</p>"})
    assert not r["ok"] and "database connection" in r["error"]
    r = ms.evaluar({"codigo": 500, "ms": 200, "texto": "Ha habido un error crítico en esta web."})
    assert not r["ok"] and "error crítico" in r["error"]


def test_la_palabra_tiene_que_aparecer():
    assert ms.evaluar({"codigo": 200, "texto": "Agregar al CARRITO", "ms": 1}, "agregar al carrito")["ok"]
    r = ms.evaluar({"codigo": 200, "texto": "Index of /", "ms": 1}, "Agregar al carrito")
    assert not r["ok"] and r["error"] == "No aparece «Agregar al carrito»"


def test_chequear_mira_la_segunda_direccion():
    sitio = {"url": "https://t.uy", "url_extra": "https://t.uy/carrito", "palabra": None}
    respuestas = {"https://t.uy": {"codigo": 200, "texto": "ok", "ms": 300},
                  "https://t.uy/carrito": {"codigo": 500, "texto": "", "ms": 100}}
    r = ms.chequear(sitio, respuestas.get)
    assert not r["ok"] and r["error"] == "Segunda dirección: Contestó 500"
    assert r["ms"] == 300


# ── incidentes ───────────────────────────────────────────────────────────────

def _incidentes(db, sid):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    filas = [dict(f) for f in conn.execute("SELECT * FROM monitor_incidentes WHERE sitio_id = ?", (sid,))]
    conn.close()
    return filas


def test_una_falla_suelta_no_abre_incidente(db):
    sid = _sitio(db)
    ms.registrar(db, sid, MAL, en=T0)
    assert _incidentes(db, sid) == []
    s = ms.registrar(db, sid, BIEN, en=T0 + timedelta(minutes=10))
    assert s["fallas_seguidas"] == 0 and _incidentes(db, sid) == []


def test_dos_fallas_abren_desde_la_primera_y_el_primer_ok_cierra(db):
    sid = _sitio(db)
    ms.registrar(db, sid, BIEN, en=T0 - timedelta(minutes=10))
    ms.registrar(db, sid, MAL, en=T0)
    ms.registrar(db, sid, MAL, en=T0 + timedelta(minutes=10))
    ms.registrar(db, sid, MAL, en=T0 + timedelta(minutes=20))
    inc = _incidentes(db, sid)
    assert len(inc) == 1 and inc[0]["empezo"] == ms._txt(T0) and inc[0]["termino"] is None
    assert inc[0]["causa"] == "Contestó 500"
    ms.registrar(db, sid, BIEN, en=T0 + timedelta(minutes=30))
    inc = _incidentes(db, sid)
    assert inc[0]["termino"] == ms._txt(T0 + timedelta(minutes=30))


def test_disponibilidad_por_tiempo():
    inc = [{"empezo": ms._txt(T0), "termino": ms._txt(T0 + timedelta(hours=1))}]
    d = ms.disponibilidad(inc, T0 - timedelta(hours=23), T0 + timedelta(hours=1), T0 - timedelta(days=9), T0 + timedelta(hours=1))
    assert d == pytest.approx(23 / 24)
    # Un incidente abierto cuenta hasta ahora.
    abierto = [{"empezo": ms._txt(T0), "termino": None}]
    d = ms.disponibilidad(abierto, T0 - timedelta(hours=1), T0 + timedelta(hours=1), T0 - timedelta(days=1), T0 + timedelta(minutes=30))
    assert d == pytest.approx(1 - 30 / 90)
    # Antes de darlo de alta no hay tiempo vigilado.
    assert ms.disponibilidad([], T0 - timedelta(days=2), T0 - timedelta(days=1), T0, T0) is None


def test_estado():
    base = {"activo": 1, "ultimo_chequeo": ms._txt(T0), "ultimo_ok": 1, "ultimo_ms": 500, "ssl_vence": None}
    assert ms.estado(base, False, T0) == "ok"
    assert ms.estado({**base, "activo": 0}, True, T0) == "pausado"
    assert ms.estado({**base, "ultimo_chequeo": None}, False, T0) == "sin_datos"
    assert ms.estado(base, True, T0) == "caido"
    assert ms.estado({**base, "ultimo_ok": 0}, False, T0) == "falla"
    assert ms.estado({**base, "ssl_vence": ms._txt(T0 + timedelta(days=5))}, False, T0) == "ssl"
    assert ms.estado({**base, "ultimo_ms": 4000}, False, T0) == "lento"


# ── revisar y el intervalo ───────────────────────────────────────────────────

def test_revisar_guarda_el_ssl_cada_seis_horas(db):
    sid = _sitio(db)
    vence = T0 + timedelta(days=60)
    vistos = []

    def ssl_falso(host):
        vistos.append(host)
        return vence

    def ok(url):
        return {"codigo": 200, "texto": "hola", "ms": 250}

    s = ms.revisar(db, sid, obtener=ok, vence_ssl=ssl_falso, ahora=T0)
    assert s["ssl_vence"] == ms._txt(vence) and vistos == ["tiendabambu.com.uy"]
    ms.revisar(db, sid, obtener=ok, vence_ssl=ssl_falso, ahora=T0 + timedelta(hours=1))
    assert len(vistos) == 1
    ms.revisar(db, sid, obtener=ok, vence_ssl=ssl_falso, ahora=T0 + timedelta(hours=7))
    assert len(vistos) == 2


def test_un_ssl_que_no_se_puede_leer_no_frena_el_chequeo(db):
    sid = _sitio(db)

    def roto(host):
        raise OSError("timeout")

    s = ms.revisar(db, sid, obtener=lambda u: {"codigo": 200, "texto": "", "ms": 1}, vence_ssl=roto, ahora=T0)
    assert s["ultimo_ok"] == 1 and s["ssl_vence"] is None and s["ssl_revisado_en"] == ms._txt(T0)


def test_vencidos_respeta_el_intervalo_y_los_pausados(db):
    a = _sitio(db, nombre="A", url="a.com.uy")
    b = _sitio(db, nombre="B", url="b.com.uy")
    c = _sitio(db, nombre="C", url="c.com.uy")
    ms.registrar(db, a, BIEN, en=T0 - timedelta(minutes=3))
    ms.registrar(db, b, BIEN, en=T0 - timedelta(minutes=30))
    ms.editar(db, c, {"activo": False})
    assert ms.vencidos(db, ahora=T0) == [b]
    d = _sitio(db, nombre="D", url="d.com.uy")
    assert ms.vencidos(db, ahora=T0) == [d, b]  # el nunca revisado primero


def test_vuelta_revisa_los_que_tocan(db, monkeypatch):
    a = _sitio(db, nombre="A", url="a.com.uy")
    b = _sitio(db, nombre="B", url="http://b.com.uy")
    vistos = []

    def obtener(url):
        vistos.append(url)
        return {"codigo": 200, "texto": "", "ms": 100}

    assert ms.vuelta(db, obtener=obtener, vence_ssl=lambda h: None) == 2
    assert sorted(vistos) == ["http://b.com.uy", "https://a.com.uy"]
    assert ms.vuelta(db, obtener=obtener, vence_ssl=lambda h: None) == 0  # ya les tocó
    assert {s["id"]: s["estado"] for s in ms.listar(db)} == {a: "ok", b: "ok"}


def test_revisar_manual_tiene_piso(db):
    sid = _sitio(db)
    ms.registrar(db, sid, BIEN)
    with pytest.raises(ms.NoSePuede, match="30 segundos"):
        ms.revisar_manual(db, sid, obtener=lambda u: {}, vence_ssl=lambda h: None)


def test_purgar_borra_chequeos_viejos_pero_no_el_resumen_del_dia(db):
    sid = _sitio(db)
    ms.registrar(db, sid, BIEN, en=T0 - timedelta(days=40))
    ms.registrar(db, sid, BIEN, en=T0)
    assert ms.purgar(db, ahora=T0) == 1
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM monitor_dias WHERE sitio_id = ?", (sid,)).fetchone()[0] == 2
    conn.close()


def test_pausar_cierra_el_incidente(db):
    sid = _sitio(db)
    ms.registrar(db, sid, MAL, en=T0)
    ms.registrar(db, sid, MAL, en=T0 + timedelta(minutes=10))
    ms.editar(db, sid, {"activo": False})
    assert _incidentes(db, sid)[0]["termino"] is not None
    assert ms.listar(db)[0]["estado"] == "pausado"


# ── listar y reporte ─────────────────────────────────────────────────────────

def test_listar_y_resumen(db):
    sid = _sitio(db)
    otro = _sitio(db, nombre="Óptica", url="optica.uy", plan="sin_plan")
    ahora = T0 + timedelta(hours=1)
    for i in range(6):
        ms.registrar(db, sid, {**BIEN, "ms": 200 + i * 100}, en=T0 + timedelta(minutes=10 * i))
    ms.registrar(db, otro, MAL, en=T0)
    ms.registrar(db, otro, MAL, en=T0 + timedelta(minutes=10))
    lista = {s["id"]: s for s in ms.listar(db, ahora=ahora)}
    assert lista[sid]["estado"] == "ok" and lista[sid]["disp_24h"] == 1
    assert lista[sid]["ms_7d"] == 450
    assert len(lista[sid]["barras"]) == 48 and lista[sid]["barras"][-1]["ok"] and lista[sid]["barras"][-2] is None
    assert lista[otro]["estado"] == "caido" and lista[otro]["caido_desde"] == ms._txt(T0)
    assert lista[otro]["disp_24h"] == pytest.approx(1 - 60 / (24 * 60))
    r = ms.resumen(list(lista.values()))
    assert r["activos"] == 2 and r["con_plan"] == 1 and r["por_estado"]["caido"] == 1


def test_reporte_del_mes(db):
    sid = _sitio(db)
    ms.registrar(db, sid, BIEN, en=T0)
    ms.registrar(db, sid, MAL, en=T0 + timedelta(minutes=10))
    ms.registrar(db, sid, MAL, en=T0 + timedelta(minutes=20))
    ms.registrar(db, sid, {**BIEN, "ms": 800}, en=T0 + timedelta(minutes=55))
    ms.guardar_nota(db, sid, "2026-10", "Actualizamos WordPress y 6 plugins.", "Juan")
    r = ms.reporte(db, sid, "2026-10", ahora=datetime(2026, 11, 2, 12))
    assert r["minutos_caido"] == 45 and len(r["incidentes"]) == 1
    assert r["incidentes"][0]["minutos"] == 45
    assert r["disponibilidad"] == pytest.approx(1 - 45 / (31 * 24 * 60))  # octubre en MVD, sin horario de verano
    assert r["chequeos"] == 4 and r["ms_medio"] == 600
    assert len(r["dias"]) == 31 and r["dias"][9]["chequeos"] == 4
    assert r["nota"] == "Actualizamos WordPress y 6 plugins." and not r["en_curso"]
    # Noviembre no tiene la caída.
    assert ms.reporte(db, sid, "2026-11", ahora=datetime(2026, 11, 2, 12))["minutos_caido"] == 0


def test_meses_con_datos(db):
    sid = _sitio(db)
    meses = ms.meses_con_datos(db, sid, ahora=datetime(2026, 10, 24, 12))
    assert meses == ["2026-10", "2026-09", "2026-08"]  # alta 40 días antes del 10/10


def test_nota_vacia_la_borra(db):
    sid = _sitio(db)
    ms.guardar_nota(db, sid, "2026-10", "algo", "Juan")
    ms.guardar_nota(db, sid, "2026-10", "  ", "Juan")
    assert ms.detalle(db, sid)["notas"] == []
    with pytest.raises(ms.NoSePuede):
        ms.guardar_nota(db, sid, "2026-13", "x", "Juan")


def test_sugeridos_son_clientes_con_web_sin_monitor(db):
    conn = sqlite3.connect(db)
    a = conn.execute("INSERT INTO businesses (name, crm_status, website) VALUES ('Bambú','en_desarrollo','bambu.uy')").lastrowid
    conn.execute("INSERT INTO businesses (name, crm_status, website) VALUES ('Lead','sin_contactar','lead.uy')")
    conn.execute("INSERT INTO businesses (name, crm_status, website) VALUES ('Sin web','finalizado','')")
    b = conn.execute("INSERT INTO businesses (name, crm_status, website) VALUES ('Ya está','finalizado','ya.uy')").lastrowid
    conn.commit()
    conn.close()
    _sitio(db, nombre="Ya está", url="ya.uy", business_id=b)
    assert [c["id"] for c in ms.sugeridos(db)] == [a]


def test_borrar_se_lleva_el_historial(db):
    sid = _sitio(db)
    ms.registrar(db, sid, MAL, en=T0)
    ms.registrar(db, sid, MAL, en=T0 + timedelta(minutes=10))
    ms.borrar(db, sid)
    conn = sqlite3.connect(db)
    for t in ("monitor_sitios", "monitor_chequeos", "monitor_incidentes", "monitor_dias"):
        assert conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0
    conn.close()


# ── rutas ────────────────────────────────────────────────────────────────────

def _usuario(db, email, paneles=None):
    uid = create_user(db, name=email.split("@")[0], email=email, phone="099",
                      password_hash=generate_password_hash("x" * 10))
    if paneles is not None:
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


def _equipo(db, email="dev@scalerics.com", paneles=("monitor",)):
    uid = _usuario(db, email, list(paneles))
    c = _app(db).test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Dev"
    return c


def test_sin_el_panel_da_403(db):
    c = _equipo(db, paneles=("clientes",))
    assert c.get("/api/monitor/sitios").status_code == 403
    assert c.post("/api/monitor/sitios", json={"nombre": "x", "url": "x.uy"}).status_code == 403


def test_sin_sesion_la_api_no_responde(db):
    c = _app(db).test_client()
    r = c.get("/api/monitor/sitios")
    assert r.status_code in (302, 401, 403)


def test_alta_lista_edita_y_borra(db):
    c = _equipo(db)
    r = c.post("/api/monitor/sitios", json={"nombre": "Bambú", "url": "bambu.com.uy", "plan": "mantenimiento",
                                             "palabra": "Comprar"})
    assert r.status_code == 201
    s = r.get_json()["sitio"]
    assert s["url"] == "https://bambu.com.uy" and s["reporte_url"] == f"https://crm.test/r/{s['codigo']}"
    d = c.get("/api/monitor/sitios").get_json()
    assert d["resumen"]["total"] == 1 and d["sitios"][0]["estado"] == "sin_datos"
    r = c.patch(f"/api/monitor/sitios/{s['id']}", json={"activo": False})
    assert r.get_json()["sitio"]["activo"] == 0
    assert c.post("/api/monitor/sitios", json={"nombre": "", "url": "x.uy"}).status_code == 400
    assert c.put(f"/api/monitor/sitios/{s['id']}/nota", json={"mes": "2026-10", "texto": "Hola"}).get_json()["ok"]
    det = c.get(f"/api/monitor/sitios/{s['id']}").get_json()["sitio"]
    assert det["notas"][0]["texto"] == "Hola" and det["meses"]
    assert c.delete(f"/api/monitor/sitios/{s['id']}").get_json()["ok"]
    assert c.get(f"/api/monitor/sitios/{s['id']}").status_code == 404


def test_revisar_ahora(db, monkeypatch):
    c = _equipo(db)
    sid = _sitio(db)
    monkeypatch.setattr(ms, "_obtener", lambda url: {"codigo": 200, "texto": "", "ms": 120})
    monkeypatch.setattr(ms, "_vence_ssl", lambda host: T0 + timedelta(days=90))
    r = c.post(f"/api/monitor/sitios/{sid}/revisar")
    assert r.status_code == 200 and r.get_json()["sitio"]["estado"] == "ok"
    assert c.post(f"/api/monitor/sitios/{sid}/revisar").status_code == 429


def test_en_la_vista_previa_no_sale_a_internet(db, monkeypatch):
    c = _equipo(db)
    sid = _sitio(db)
    monkeypatch.setenv("CRM_PREVIEW", "1")
    r = c.post(f"/api/monitor/sitios/{sid}/revisar")
    assert r.status_code == 400 and "vista previa" in r.get_json()["error"]


def test_reporte_publico(db):
    sid = _sitio(db, nombre="Bambú <script>alert(1)</script>")
    ms.registrar(db, sid, BIEN)
    codigo = ms.detalle(db, sid)["codigo"]
    c = _app(db).test_client()  # sin sesión: es lo que abre el cliente
    r = c.get(f"/r/{codigo}")
    assert r.status_code == 200
    cuerpo = r.get_data(as_text=True)
    assert "<script>alert(1)" not in cuerpo and "&lt;script&gt;" in cuerpo
    assert "Plan de mantenimiento" in cuerpo and "Día por día" in cuerpo
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp and "script-src" not in csp
    assert r.headers["X-Robots-Tag"].startswith("noindex")
    assert "<script" not in cuerpo.lower()
    # Un mes que no está en la lista cae en el actual.
    assert c.get(f"/r/{codigo}?mes=1999-01").status_code == 200
    assert c.get("/r/no-existe").status_code == 404


def test_el_reporte_no_muestra_la_ficha_del_cliente(db):
    conn = sqlite3.connect(db)
    b = conn.execute("INSERT INTO businesses (name, crm_status, phone, notes) VALUES "
                     "('Bambú SRL','en_desarrollo','099123456','debe dos cuotas')").lastrowid
    conn.commit()
    conn.close()
    sid = _sitio(db, nombre="Tienda Bambú", business_id=b)
    codigo = ms.detalle(db, sid)["codigo"]
    cuerpo = _app(db).test_client().get(f"/r/{codigo}").get_data(as_text=True)
    assert "099123456" not in cuerpo and "debe dos cuotas" not in cuerpo and "Bambú SRL" not in cuerpo


def test_tick_pide_token(db, monkeypatch):
    c = _app(db).test_client()
    assert c.post("/api/monitor/tick").status_code == 404
    monkeypatch.setenv("MONITOR_TICK_TOKEN", "t" * 32)
    assert c.post("/api/monitor/tick", headers={"Authorization": "Bearer otro"}).status_code == 403
    llamadas = []
    monkeypatch.setattr(ms, "vuelta", lambda db: llamadas.append(db) or 3)
    r = c.post("/api/monitor/tick", headers={"Authorization": "Bearer " + "t" * 32})
    assert r.get_json() == {"ok": True, "revisados": 3} and len(llamadas) == 1


def test_el_panel_esta_en_el_menu_y_el_js_compila(db):
    html = dashboard.DASHBOARD_HTML
    assert 'id="nav-monitor"' in html and 'id="monitor-panel"' in html
    assert "async function monCargar()" in html and "if (name === 'monitor') monCargar();" in html


def test_el_panel_les_llega_a_quienes_ven_clientes(db):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO roles (name, panel_access) VALUES ('dev', ?)", (json.dumps(["clientes"]),))
    conn.execute("INSERT INTO roles (name, panel_access) VALUES ('mkt', ?)", (json.dumps(["meta"]),))
    # Como en producción: los roles ya estaban antes del primer arranque con el monitor.
    conn.execute("DELETE FROM panel_grants_aplicados WHERE panel = 'monitor'")
    conn.commit()
    conn.close()
    init_db(db)
    conn = sqlite3.connect(db)
    accesos = dict(conn.execute("SELECT name, panel_access FROM roles WHERE name IN ('dev','mkt')").fetchall())
    conn.close()
    assert "monitor" in json.loads(accesos["dev"]) and "monitor" not in json.loads(accesos["mkt"])
