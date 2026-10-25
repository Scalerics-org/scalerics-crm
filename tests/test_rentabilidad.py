"""Rentabilidad de proyectos (services/rentabilidad.py, routes/rentabilidad.py)."""

import json
import sqlite3
from datetime import date, datetime

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services import rentabilidad as rt

# Lunes 26/10/2026, 10 de la mañana en Montevideo.
AHORA = datetime(2026, 10, 26, 10, 0)
HOY = AHORA.date()


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setattr(rt, "ahora_uy", lambda: AHORA)
    r = str(tmp_path / "rt.db")
    init_db(r)
    return r


def _persona(db, nombre="Ana", costo=500, horas_dia=6):
    conn = sqlite3.connect(db)
    pid = conn.execute("INSERT INTO equipo_personas (nombre, rol, lleva_horas, horas_por_dia, activo) "
                       "VALUES (?, 'Programadora', 1, ?, 1)", (nombre, horas_dia)).lastrowid
    conn.commit()
    conn.close()
    if costo:
        rt.guardar_costo(db, pid, costo)
    return pid


def _proyecto(db, **kw):
    datos = {"nombre": "Web Bambú", "cliente": "Tienda Bambú", "tipo": "web", "moneda": "UYU", "precio": 40000,
             "horas_estimadas": 40, "inicio": "2026-10-01", "entrega": "2026-10-31", **kw}
    return rt.crear_proyecto(db, datos, "test")


def _de(lista, pid):
    """La fila de esa persona: el organigrama precargado también lleva horas."""
    return next(x for x in lista if x["id"] == pid)


def _horas(db, proyecto, persona, tiempo, fecha="2026-10-20", fase="desarrollo"):
    return rt.cargar_horas(db, {"proyecto_id": proyecto, "persona_id": persona, "tiempo": tiempo,
                                "fecha": fecha, "fase": fase}, "test")


# ── servicio ─────────────────────────────────────────────────────────────────

def test_init_db_crea_las_tablas(db):
    conn = sqlite3.connect(db)
    nombres = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"rt_proyectos", "rt_costos", "rt_horas", "rt_relojes", "rt_ajustes"} <= nombres
    assert rt.ajustes(db) == {"tipo_cambio": 40.0, "margen_objetivo": 40.0}


@pytest.mark.parametrize("texto,minutos", [
    ("1:30", 90), ("1,5", 90), ("1.5", 90), ("2", 120), ("90m", 90), ("2h", 120), ("1h 15m", 75), ("45 min", 45),
    ("0:05", 5),
])
def test_el_tiempo_se_escribe_como_sale(texto, minutos):
    assert rt.minutos_de(texto) == minutos


@pytest.mark.parametrize("malo", ["", "0", "abc", "13", "12:01", "1:75", "-1", "h"])
def test_el_tiempo_raro_se_rechaza(malo):
    with pytest.raises(rt.NoSePuede):
        rt.minutos_de(malo)


def test_margen_consumo_y_tarifa_de_un_proyecto(db):
    ana = _persona(db, costo=500)
    beto = _persona(db, "Beto", costo=300)
    p = _proyecto(db)
    _horas(db, p, ana, "10")
    _horas(db, p, beto, "10", fase="correcciones")
    t = rt.tablero(db, HOY)
    m = t["proyectos"][0]["metricas"]
    assert m["horas"] == 20 and m["consumo_pct"] == 50
    assert m["costo"] == 8000 and m["margen"] == 32000 and m["margen_pct"] == 80
    assert m["tarifa_efectiva"] == 2000 and m["tarifa_vendida"] == 1000
    # 25 de 30 días del plazo: va 83 % del tiempo con 50 % de las horas.
    assert m["avance_pct"] == 83 and m["salud"] == "ok"
    # Proyección: al menos lo estimado, al costo medio del proyecto (400/h).
    assert m["horas_final"] == 40 and m["margen_final"] == 40000 - 16000
    assert t["kpis"]["correcciones_pct"] == 50 and t["kpis"]["horas_mes"] == 20


def test_el_costo_se_congela_en_cada_carga(db):
    ana = _persona(db, costo=500)
    p = _proyecto(db)
    _horas(db, p, ana, "2")
    rt.guardar_costo(db, ana, 1000)
    _horas(db, p, ana, "2")
    assert rt.tablero(db, HOY)["proyectos"][0]["metricas"]["costo"] == 1000 + 2000


def test_horas_sin_costo_se_avisan_y_no_cuentan_como_gratis(db):
    ana = _persona(db, costo=0)
    p = _proyecto(db)
    _horas(db, p, ana, "4")
    m = rt.tablero(db, HOY)["proyectos"][0]["metricas"]
    assert m["horas_sin_costo"] == 4 and m["costo"] == 0


def test_dolares_se_pasan_con_el_tipo_de_cambio(db):
    ana = _persona(db, costo=1000)
    rt.guardar_ajustes(db, {"tipo_cambio": "41,5", "margen_objetivo": 35})
    p = _proyecto(db, moneda="USD", precio=1000)
    _horas(db, p, ana, "10")
    m = rt.tablero(db, HOY)["proyectos"][0]["metricas"]
    assert m["ingreso"] == 41500 and m["margen"] == 31500


@pytest.mark.parametrize("kw,horas,salud", [
    ({}, "8", "ok"),
    ({}, "12:00", "ok"),
    ({"horas_estimadas": 10}, "8", "riesgo"),                     # 80 % usado
    ({"inicio": "2026-10-24", "entrega": "2026-11-23"}, "12", "riesgo"),  # 30 % de horas con 7 % del tiempo
    ({"horas_estimadas": 10}, "11", "pasado"),
    ({"entrega": "2026-10-25"}, "1", "atrasado"),
    ({"estado": "entregado"}, None, "cerrado"),
])
def test_semaforo(db, kw, horas, salud):
    ana = _persona(db)
    p = _proyecto(db, **kw)
    if horas:
        _horas(db, p, ana, horas)
    assert rt.tablero(db, HOY)["proyectos"][0]["metricas"]["salud"] == salud


def test_no_se_cargan_horas_a_futuro_ni_a_proyectos_cerrados(db):
    ana = _persona(db)
    p = _proyecto(db)
    with pytest.raises(rt.NoSePuede, match="futuro"):
        _horas(db, p, ana, "1", fecha="2026-10-27")
    rt.actualizar_proyecto(db, p, {**_datos(db, p), "estado": "entregado"})
    with pytest.raises(rt.NoSePuede, match="entregado"):
        _horas(db, p, ana, "1")
    with pytest.raises(rt.NoSePuede):
        rt.cargar_horas(db, {"proyecto_id": p, "persona_id": 999, "tiempo": "1"}, "t")


def _datos(db, pid):
    d = rt.detalle(db, pid, HOY)["proyecto"]
    return {k: d[k] for k in ("nombre", "cliente", "tipo", "moneda", "precio", "horas_estimadas", "inicio",
                              "entrega", "estado", "notas")}


def test_validacion_del_proyecto(db):
    for malo in ({"nombre": ""}, {"tipo": "app"}, {"moneda": "EUR"}, {"precio": "mucho"},
                 {"inicio": "2026-10-10", "entrega": "2026-10-01"}, {"inicio": "10/10/2026"}):
        with pytest.raises(rt.NoSePuede):
            _proyecto(db, **malo)
    p = _proyecto(db, precio="45.000,50")
    assert rt.detalle(db, p, HOY)["proyecto"]["precio"] == 45000.5


def test_el_reloj_guarda_las_horas_al_frenar(db, monkeypatch):
    ana = _persona(db)
    p = _proyecto(db)
    rt.prender_reloj(db, {"persona_id": ana, "proyecto_id": p, "fase": "diseno"})
    with pytest.raises(rt.NoSePuede, match="ya tiene"):
        rt.prender_reloj(db, {"persona_id": ana, "proyecto_id": p, "fase": "diseno"})
    assert _de(rt.personas(db), ana)["reloj_proyecto"] == p
    monkeypatch.setattr(rt, "ahora_uy", lambda: datetime(2026, 10, 26, 11, 25))
    r = rt.frenar_reloj(db, ana, "test")
    assert r["minutos"] == 85
    c = rt.detalle(db, p, HOY)["cargas"][0]
    assert c["origen"] == "reloj" and c["fase"] == "diseno" and c["horas"] == 1.42
    with pytest.raises(rt.NoSePuede, match="no tiene"):
        rt.frenar_reloj(db, ana, "test")


def test_un_reloj_olvidado_se_corta_en_12_horas(db, monkeypatch):
    ana = _persona(db)
    p = _proyecto(db)
    rt.prender_reloj(db, {"persona_id": ana, "proyecto_id": p, "fase": "desarrollo"})
    monkeypatch.setattr(rt, "ahora_uy", lambda: datetime(2026, 10, 28, 9, 0))
    assert rt.frenar_reloj(db, ana, "test")["minutos"] == 12 * 60
    assert "olvidado" in rt.detalle(db, p, HOY)["cargas"][0]["nota"]


def test_entregar_apaga_el_reloj(db):
    ana = _persona(db)
    p = _proyecto(db)
    rt.prender_reloj(db, {"persona_id": ana, "proyecto_id": p, "fase": "desarrollo"})
    rt.actualizar_proyecto(db, p, {**_datos(db, p), "estado": "entregado"})
    assert _de(rt.personas(db), ana)["reloj_proyecto"] is None
    assert rt.detalle(db, p, HOY)["proyecto"]["cerrado_en"] == HOY.isoformat()


def test_por_tipo_y_estimador(db):
    ana = _persona(db, costo=600)
    for est, real in ((40, "8"), (40, "10")):
        p = _proyecto(db, horas_estimadas=est / 5)
        _horas(db, p, ana, real)
        rt.actualizar_proyecto(db, p, {**_datos(db, p), "estado": "entregado"})
    _proyecto(db, tipo="ecommerce")
    t = rt.tablero(db, HOY)
    web = next(x for x in t["por_tipo"] if x["tipo"] == "web")
    assert web["entregados"] == 2 and web["horas_mediana"] == 9 and web["desvio_pct"] == 12
    assert next(x for x in t["por_tipo"] if x["tipo"] == "ecommerce")["entregados"] == 0
    e = rt.estimar(db, "web", "20", HOY)
    # La mediana real son 9 h; 20 h estimadas con 12 % de desvío dan 22,4 h.
    assert e["horas_sugeridas"] == 9 and e["horas_corregidas"] == 22.4
    # 22,4 h a 600 $/h con 40 % de margen: 22.400, redondeado a 500.
    assert e["precio_uyu"] == 22500 and e["precio_usd"] == 560
    assert rt.estimar(db, "ecommerce", None, HOY)["base"] == 0
    with pytest.raises(rt.NoSePuede):
        rt.estimar(db, "nada", None, HOY)


def test_utilizacion_de_las_ultimas_cuatro_semanas(db):
    ana = _persona(db, horas_dia=4)
    p = _proyecto(db)
    _horas(db, p, ana, "10", fecha="2026-10-26")
    _horas(db, p, ana, "6", fecha="2026-10-06")
    _horas(db, p, ana, "6", fecha="2026-09-20")      # fuera de las 4 semanas
    e = _de(rt.tablero(db, HOY)["equipo"], ana)
    assert e["semanas"] == [6, 0, 0, 10] and e["semana_desde"][0] == "2026-10-05"
    # Tres semanas enteras (60 h) más el lunes de esta (4 h).
    assert e["capacidad"] == 64 and e["horas_4s"] == 16 and e["utilizacion_pct"] == 25


def test_csv_neutraliza_formulas(db):
    ana = _persona(db)
    p = _proyecto(db, nombre="=HYPERLINK(\"x\")")
    rt.cargar_horas(db, {"proyecto_id": p, "persona_id": ana, "tiempo": "1", "fecha": "2026-10-20",
                         "nota": "+cmd"}, "t")
    texto = rt.horas_csv(db, "2026-10-01", "2026-10-31")
    assert "'=HYPERLINK" in texto and "'+cmd" in texto and "1.00" in texto
    assert rt.horas_csv(db, "2026-11-01", None).count("\n") == 1


def test_borrar_proyecto_borra_sus_horas(db):
    ana = _persona(db)
    p = _proyecto(db)
    _horas(db, p, ana, "1")
    rt.borrar_proyecto(db, p)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM rt_horas").fetchone()[0] == 0
    conn.close()


# ── rutas ────────────────────────────────────────────────────────────────────

@pytest.fixture
def app(db):
    a = dashboard.create_app(db)
    a.config["TESTING"] = True
    return a


def _login(db, app, email="dev@scalerics.com", paneles=("rentabilidad",), solo_lectura=()):
    uid = create_user(db, name="Dev", email=email, phone="099", password_hash=generate_password_hash("x" * 12))
    conn = sqlite3.connect(db)
    rid = conn.execute("INSERT INTO roles (name, panel_access, paneles_solo_lectura) VALUES (?,?,?)",
                       ("rol-" + email, json.dumps(list(paneles)), json.dumps(list(solo_lectura)))).lastrowid
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
    assert app.test_client().get("/api/rentabilidad").status_code == 401
    sin = _login(db, app, "otro@scalerics.com", paneles=("clientes", "projects"))
    assert sin.get("/api/rentabilidad").status_code == 403
    assert sin.post("/api/rentabilidad/horas", json={}).status_code == 403
    assert sin.get("/api/rentabilidad/horas.csv").status_code == 403


def test_solo_lectura_ve_pero_no_escribe(app, db):
    c = _login(db, app, "contador@scalerics.com", solo_lectura=("rentabilidad",))
    assert c.get("/api/rentabilidad").status_code == 200
    r = c.post("/api/rentabilidad/proyectos", json={"nombre": "x"})
    assert r.status_code == 403 and r.get_json()["solo_lectura"] is True


def test_el_panel_no_se_reparte_a_los_roles_existentes(db):
    conn = sqlite3.connect(db)
    accesos = [f[0] or "" for f in conn.execute("SELECT panel_access FROM roles")]
    conn.close()
    assert accesos and all("rentabilidad" not in a for a in accesos)


def test_flujo_completo_por_api(app, db):
    ana = _persona(db, costo=500)
    c = _login(db, app)
    r = c.post("/api/rentabilidad/proyectos", json={"nombre": "Tienda", "tipo": "ecommerce", "precio": "60000",
                                                     "horas_estimadas": "50", "inicio": "2026-10-01",
                                                     "entrega": "2026-11-15"})
    assert r.status_code == 201
    pid = r.get_json()["id"]
    assert c.post("/api/rentabilidad/horas", json={"proyecto_id": pid, "persona_id": ana, "tiempo": "3:30",
                                                   "fecha": "2026-10-26", "fase": "desarrollo"}).status_code == 201
    assert c.post("/api/rentabilidad/horas", json={"proyecto_id": pid, "persona_id": ana,
                                                   "tiempo": "nada"}).status_code == 400
    d = c.get("/api/rentabilidad").get_json()
    assert d["ok"] and d["proyectos"][0]["metricas"]["horas"] == 3.5 and _de(d["personas"], ana)["costo_hora"] == 500
    assert d["fases"]["correcciones"] == "Correcciones del cliente" and "web" in d["tipos"]
    det = c.get(f"/api/rentabilidad/proyectos/{pid}").get_json()
    assert det["cargas"][0]["horas"] == 3.5 and det["serie"][-1]["horas"] == 3.5
    assert c.get("/api/rentabilidad/proyectos/999").status_code == 404
    assert c.post("/api/rentabilidad/reloj", json={"persona_id": ana, "proyecto_id": pid,
                                                   "fase": "reuniones"}).status_code == 200
    assert c.post("/api/rentabilidad/reloj/frenar", json={"persona_id": ana}).get_json()["minutos"] == 0
    assert c.put(f"/api/rentabilidad/costos/{ana}", json={"costo_hora": "650"}).status_code == 200
    assert c.put("/api/rentabilidad/ajustes", json={"tipo_cambio": "0", "margen_objetivo": 40}).status_code == 400
    assert c.get("/api/rentabilidad/estimar?tipo=ecommerce").get_json()["estimacion"]["base"] == 0
    csv = c.get("/api/rentabilidad/horas.csv")
    assert csv.status_code == 200 and csv.mimetype == "text/csv" and "3.50" in csv.get_data(as_text=True)
    hid = det["cargas"][0]["id"]
    assert c.delete(f"/api/rentabilidad/horas/{hid}").status_code == 200
    assert c.delete(f"/api/rentabilidad/proyectos/{pid}").status_code == 200


def test_el_panel_esta_en_el_menu_y_en_los_permisos():
    html = dashboard.DASHBOARD_HTML
    with open(dashboard.__file__, encoding="utf-8") as f:
        src = f.read()
    assert 'id="nav-rentabilidad"' in html and 'id="rentabilidad-panel"' in html
    assert "if (name === 'rentabilidad') rtCargar();" in html
    assert "async function rtCargar()" in html
    assert "const PANELES_CON_SOLO_LECTURA = ['finanzas', 'rentabilidad'];" in src
