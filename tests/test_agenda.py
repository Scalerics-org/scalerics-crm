"""Agenda online: reservas con link para comercios (services/agenda.py, routes/agenda.py)."""

import json
import re
import sqlite3
from datetime import date, datetime

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services import agenda as ag

# Lunes 26/10/2026, 8 de la mañana en Montevideo.
AHORA = datetime(2026, 10, 26, 8, 0)
LUNES = date(2026, 10, 26)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("CRM_URL", "https://crm.test")
    monkeypatch.setattr(ag, "ahora_uy", lambda: AHORA)
    r = str(tmp_path / "rv.db")
    init_db(r)
    return r


def _negocio(db, profs=(("Ana", "lun-vie 09:00-12:00"),), **kw):
    n = ag.crear_negocio(db, {"nombre": "Barbería Don Pepe", "rubro": "Barbería", "whatsapp": "099 111 222",
                              "intervalo_min": 30, "anticipacion_min": 120, **kw}, "test")
    corte = ag.guardar_servicio(db, n["id"], {"nombre": "Corte", "duracion_min": 30, "precio": 600})
    barba = ag.guardar_servicio(db, n["id"], {"nombre": "Corte y barba", "duracion_min": 60, "precio": 900})
    ids = [ag.guardar_profesional(db, n["id"], {"nombre": p, "horarios": h})["id"] for p, h in profs]
    return ag.negocio(db, n["id"]), corte["id"], barba["id"], ids


def _reserva(db, n, sid, hora, dia=LUNES, tel="099 000 001", pid=None):
    return ag.reservar(db, n["id"], {"servicio_id": sid, "profesional_id": pid, "fecha": dia.isoformat(),
                                     "hora": hora, "cliente": "Juan Pérez", "telefono": tel})


def _horas(db, n, sid, dia=LUNES, pid=None, **kw):
    return [x["hora"] for x in ag.horarios_libres(db, ag.negocio(db, n["id"]), sid, dia, pid, **kw)]


# ── servicio ─────────────────────────────────────────────────────────────────

def test_init_db_crea_las_tablas(db):
    conn = sqlite3.connect(db)
    nombres = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"rv_negocios", "rv_servicios", "rv_profesionales", "rv_horarios", "rv_bloqueos", "rv_turnos"} <= nombres


def test_el_horario_se_escribe_como_lo_dice_la_gente():
    h = ag._horarios("lun-vie 09:00-13:00 15:00-19:00; sáb 9:00-13:00")
    assert len(h) == 11 and (5, 540, 780) in h and (0, 900, 1140) in h
    texto = ag.horarios_texto([{"dia": d, "desde": ag._min_a_hhmm(a), "hasta": ag._min_a_hhmm(b)} for d, a, b in h])
    assert texto == "lun-vie 09:00-13:00 15:00-19:00; sáb 09:00-13:00"
    for malo in ("lun 10:00-09:00", "lun 09:00-12:00 11:00-13:00", "lunes", "xyz 09:00-10:00", "vie-lun 09:00-10:00"):
        with pytest.raises(ag.NoSePuede):
            ag._horarios(malo)


def test_el_telefono_queda_como_lo_entiende_whatsapp():
    assert ag.tel_normal("099 123 456") == "59899123456"
    assert ag.tel_normal("+598 99 123 456") == "59899123456"
    assert ag.tel_normal("99123456") == "59899123456"
    with pytest.raises(ag.NoSePuede):
        ag.tel_normal("123")


def test_el_negocio_valida_y_arma_un_link_legible(db):
    n, *_ = _negocio(db)
    assert n["slug"] == "barberia-don-pepe" and n["whatsapp"] == "59899111222"
    otro = ag.crear_negocio(db, {"nombre": "Barbería Don Pepe"}, "t")
    assert otro["slug"].startswith("barberia-don-pepe-") and otro["slug"] != n["slug"]
    with pytest.raises(ag.NoSePuede):
        ag.crear_negocio(db, {"nombre": " "}, "t")
    with pytest.raises(ag.NoSePuede):
        ag.crear_negocio(db, {"nombre": "X", "intervalo_min": 7}, "t")
    with pytest.raises(ag.NoSePuede):
        ag.editar_negocio(db, otro["id"], {"slug": "barberia-don-pepe"})
    assert ag.editar_negocio(db, otro["id"], {"color": "javascript:x", "dias_max": 10})["color"] == "#0088cc"


def test_los_horarios_libres_respetan_horario_anticipacion_y_duracion(db):
    n, corte, barba, _ = _negocio(db)
    # Son las 8 y piden 2 horas de anticipación: desde las 10.
    assert _horas(db, n, corte) == ["10:00", "10:30", "11:00", "11:30"]
    # El de 60 minutos no puede arrancar 11:30: terminaría después del cierre.
    assert _horas(db, n, barba) == ["10:00", "10:30", "11:00"]
    assert _horas(db, n, corte, date(2026, 10, 27))[0] == "09:00"
    assert _horas(db, n, corte, date(2026, 11, 1)) == []          # domingo
    assert _horas(db, n, corte, date(2026, 12, 25)) == []         # más allá de dias_max (30)
    assert _horas(db, n, corte, date(2026, 10, 25)) == []         # ayer
    assert _horas(db, n, corte, para_equipo=True)[0] == "09:00"   # el equipo no tiene anticipación


def test_reservar_ocupa_el_lugar_y_no_deja_pisarse(db):
    n, corte, barba, _ = _negocio(db)
    t = _reserva(db, n, barba, "10:00")
    assert t["fin"] == "2026-10-26 11:00" and t["precio"] == 900 and t["origen"] == "link" and len(t["codigo"]) >= 20
    assert _horas(db, n, corte) == ["11:00", "11:30"]
    with pytest.raises(ag.NoSePuede, match="ocupar"):
        _reserva(db, n, corte, "10:30", tel="099 000 002")
    with pytest.raises(ag.NoSePuede, match="ocupar"):
        _reserva(db, n, corte, "10:15", tel="099 000 002")       # no es un arranque válido


def test_el_primero_libre_reparte_entre_profesionales(db):
    n, corte, _, (ana, beto) = _negocio(db, profs=(("Ana", "lun 09:00-12:00"), ("Beto", "lun 09:00-12:00")))
    a = _reserva(db, n, corte, "10:00")
    b = _reserva(db, n, corte, "10:30", tel="099 000 002")
    assert {a["profesional_id"], b["profesional_id"]} == {ana, beto}
    c = _reserva(db, n, corte, "10:00", tel="099 000 003")
    assert c["profesional_id"] != a["profesional_id"]
    assert "10:00" not in _horas(db, n, corte)
    assert "10:00" not in _horas(db, n, corte, pid=ana) and "11:00" in _horas(db, n, corte, pid=ana)


def test_un_profesional_puede_hacer_solo_algunos_servicios(db):
    n, corte, barba, (ana,) = _negocio(db)
    ag.guardar_profesional(db, n["id"], {"nombre": "Ana", "horarios": "lun 09:00-12:00", "servicios": [corte]}, ana)
    assert _horas(db, n, barba) == [] and _horas(db, n, corte)
    with pytest.raises(ag.NoSePuede):
        ag.guardar_profesional(db, n["id"], {"nombre": "X", "servicios": [9999]})


def test_los_bloqueos_sacan_horarios_del_local_o_de_una_persona(db):
    n, corte, _, (ana, beto) = _negocio(db, profs=(("Ana", "lun 09:00-12:00"), ("Beto", "lun 09:00-12:00")))
    b = ag.crear_bloqueo(db, n["id"], {"desde": "2026-10-26", "hora_desde": "10:00", "hora_hasta": "11:00",
                                      "profesional_id": ana, "motivo": "Médico"})
    assert "10:00" not in _horas(db, n, corte, pid=ana) and "10:00" in _horas(db, n, corte, pid=beto)
    ag.crear_bloqueo(db, n["id"], {"desde": "2026-10-26", "motivo": "Feriado"})
    assert _horas(db, n, corte) == []
    ag.borrar_bloqueo(db, n["id"], b["id"])
    with pytest.raises(ag.NoSePuede):
        ag.crear_bloqueo(db, n["id"], {"desde": "2026-10-27", "hasta": "2026-10-26"})


def test_el_bloqueo_avisa_si_choca_con_turnos(db):
    n, corte, _, _ = _negocio(db)
    _reserva(db, n, corte, "10:00")
    assert ag.crear_bloqueo(db, n["id"], {"desde": "2026-10-26"})["turnos_que_chocan"] == 1


def test_el_link_tiene_topes_y_el_equipo_no(db):
    n, corte, _, _ = _negocio(db, profs=(("Ana", "lun-vie 09:00-18:00"),))
    for h in ("10:00", "10:30", "11:00"):
        _reserva(db, n, corte, h)
    with pytest.raises(ag.NoSePuede, match="por delante"):
        _reserva(db, n, corte, "11:30")
    t = ag.reservar(db, n["id"], {"servicio_id": corte, "fecha": "2026-10-26", "hora": "09:00", "cliente": "Tel",
                                  "telefono": "099 000 001"}, origen="equipo", quien="Juan")
    assert t["origen"] == "equipo" and t["creado_por"] == "Juan"


def test_el_comercio_pausado_no_toma_reservas_por_el_link(db):
    n, corte, _, _ = _negocio(db)
    ag.editar_negocio(db, n["id"], {"activo": False})
    assert ag.por_slug(db, n["slug"]) is None
    with pytest.raises(ag.NoSePuede):
        _reserva(db, n, corte, "10:00")


def test_cambiar_mueve_el_turno_y_libera_el_anterior(db):
    n, corte, _, _ = _negocio(db)
    t = _reserva(db, n, corte, "10:00", dia=date(2026, 10, 27))
    nuevo = ag.reservar(db, n["id"], {"servicio_id": corte, "fecha": "2026-10-27", "hora": "11:00"},
                        cambiar=t["codigo"])
    assert nuevo["id"] == t["id"] and nuevo["inicio"] == "2026-10-27 11:00" and nuevo["cliente"] == "Juan Pérez"
    assert "10:00" in _horas(db, n, corte, date(2026, 10, 27))
    # Su propio lugar cuenta como libre al cambiarlo: puede correrlo media hora.
    assert ag.reservar(db, n["id"], {"servicio_id": corte, "fecha": "2026-10-27", "hora": "11:30"},
                       cambiar=t["codigo"])["inicio"] == "2026-10-27 11:30"


def test_cancelar_respeta_el_plazo(db):
    n, corte, _, _ = _negocio(db)
    lejos = _reserva(db, n, corte, "10:00", dia=date(2026, 10, 27))
    assert ag.cancelar(db, lejos["codigo"])["estado"] == "cancelado"
    assert "10:00" in _horas(db, n, corte, date(2026, 10, 27))
    cerca = _reserva(db, n, corte, "10:00", tel="099 000 009")   # en 2 horas; se cancela hasta 3 h antes
    with pytest.raises(ag.NoSePuede, match="escribile"):
        ag.cancelar(db, cerca["codigo"])
    assert ag.cancelar(db, cerca["codigo"], por_cliente=False)["estado"] == "cancelado"


def test_recordatorio_por_whatsapp_y_calendario(db):
    n, corte, _, _ = _negocio(db)
    t = _reserva(db, n, corte, "10:00")
    url = ag.whatsapp_recordatorio("https://crm.test", t)
    assert url.startswith("https://wa.me/59899000001?text=")
    assert "Juan" in url and "lunes%2026%20de%20octubre" in url and t["codigo"] in url
    cal = ag.ics(t, "https://crm.test")
    assert "DTSTART:20261026T130000Z" in cal and "SUMMARY:Corte · Barbería Don Pepe" in cal
    assert ag.marcar_recordado(db, t["id"])["recordado_en"]


def test_metricas_de_ocupacion_y_ausencias(db):
    n, corte, barba, _ = _negocio(db)
    _reserva(db, n, barba, "10:00")
    m = ag.metricas(db, n["id"])
    # Lun a vie de 9 a 12 = 15 h abiertas en la semana; 1 h ocupada.
    assert m["proximos_7"] == 1 and m["hoy"] == 1 and m["ocupacion_7"] == round(100 * 60 / (15 * 60))
    assert m["agendado_7"] == 900 and m["por_link_pct"] == 100 and m["sin_recordar"] == 1
    conn = sqlite3.connect(db)
    for i, estado in enumerate(("asistio", "asistio", "asistio", "falto")):
        conn.execute("INSERT INTO rv_turnos (negocio_id, profesional_id, servicio_id, inicio, fin, cliente, telefono, "
                     "estado, codigo, origen, creado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (n["id"], n["profesionales"][0]["id"], corte, f"2026-10-2{i} 10:00", f"2026-10-2{i} 10:30",
                      "X", "598", estado, f"cod-viejo-{i}-xxxxxxxxxxxx", "equipo", "2026-10-01 10:00"))
    conn.commit()
    conn.close()
    m = ag.metricas(db, n["id"])
    assert m["ausencias_30"] == 25 and m["faltas_30"] == 1


def test_borrar_servicio_o_profesional_con_historia_los_desactiva(db):
    n, corte, barba, (ana,) = _negocio(db)
    _reserva(db, n, corte, "10:00", dia=date(2026, 10, 27))
    ag.borrar_servicio(db, n["id"], barba)
    ag.borrar_servicio(db, n["id"], corte)
    servs = {s["id"]: s for s in ag.negocio(db, n["id"])["servicios"]}
    assert barba not in servs and servs[corte]["activo"] == 0
    with pytest.raises(ag.NoSePuede, match="turnos por delante"):
        ag.borrar_profesional(db, n["id"], ana)


def test_borrar_negocio_se_lleva_todo(db):
    n, corte, _, _ = _negocio(db)
    _reserva(db, n, corte, "10:00")
    ag.borrar_negocio(db, n["id"])
    conn = sqlite3.connect(db)
    assert all(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0
               for t in ("rv_negocios", "rv_servicios", "rv_profesionales", "rv_horarios", "rv_turnos"))
    conn.close()


# ── rutas ────────────────────────────────────────────────────────────────────

@pytest.fixture
def app(db):
    a = dashboard.create_app(db)
    a.config["TESTING"] = True
    return a


def _login(app, db, email="dev@scalerics.com", paneles=("reservas",)):
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
    assert app.test_client().get("/api/agenda").status_code == 401
    sin = _login(app, db, "otro@scalerics.com", paneles=("clientes",))
    assert sin.get("/api/agenda").status_code == 403
    assert sin.post("/api/agenda/negocios", json={"nombre": "x"}).status_code == 403
    assert sin.patch("/api/agenda/turnos/1", json={"estado": "falto"}).status_code == 403


def test_flujo_del_equipo_por_api(app, db):
    c = _login(app, db)
    r = c.post("/api/agenda/negocios", json={"nombre": "Estética Lumi", "whatsapp": "098 765 432"})
    assert r.status_code == 201
    n = r.get_json()["negocio"]
    assert n["link"] == "https://crm.test/reservar/estetica-lumi"
    base = f"/api/agenda/negocios/{n['id']}"
    s = c.post(base + "/servicios", json={"nombre": "Limpieza facial", "duracion_min": "45", "precio": "1500"})
    assert s.status_code == 201
    sid = s.get_json()["servicio"]["id"]
    p = c.post(base + "/profesionales", json={"nombre": "Lucía", "horarios": "lun-sáb 09:00-13:00"})
    assert p.status_code == 201 and p.get_json()["profesional"]["horarios_texto"] == "lun-sáb 09:00-13:00"
    assert c.post(base + "/profesionales", json={"nombre": "X", "horarios": "lun 13:00-09:00"}).status_code == 400
    libres = c.get(base + f"/libres?servicio_id={sid}&fecha=2026-10-26").get_json()["libres"]
    assert libres[0]["hora"] == "09:00"   # el equipo carga sin anticipación
    t = c.post(base + "/turnos", json={"servicio_id": sid, "fecha": "2026-10-26", "hora": "09:00",
                                       "cliente": "María", "telefono": "099 555 666"})
    assert t.status_code == 201
    turno = t.get_json()["turno"]
    assert turno["origen"] == "equipo" and turno["whatsapp"].startswith("https://wa.me/59899555666")
    d = c.get(base + "?dia=2026-10-26").get_json()
    assert [x["cliente"] for x in d["turnos"]] == ["María"] and d["metricas"]["hoy"] == 1
    assert c.patch(f"/api/agenda/turnos/{turno['id']}", json={"estado": "asistio"}).get_json()["turno"]["estado"] == "asistio"
    assert c.patch(f"/api/agenda/turnos/{turno['id']}", json={"estado": "x"}).status_code == 400
    assert c.patch(base, json={"color": "#ff0066", "mensaje": "Hola"}).get_json()["negocio"]["color"] == "#ff0066"
    assert c.post(base + "/bloqueos", json={"desde": "2026-12-25", "motivo": "Navidad"}).status_code == 201
    assert c.get("/api/agenda").get_json()["negocios"][0]["nombre"] == "Estética Lumi"
    assert c.delete(base).get_json()["ok"] and c.get(base).status_code == 404


def test_la_pagina_publica_abre_sin_sesion_y_sin_scripts(app, db):
    n, corte, _, _ = _negocio(db, mensaje="¡Bienvenidos! <b>no es html</b>")
    r = app.test_client().get(f"/reservar/{n['slug']}")
    assert r.status_code == 200
    cuerpo = r.get_data(as_text=True)
    assert "Barbería Don Pepe" in cuerpo and "Corte y barba" in cuerpo and "$ 900" in cuerpo
    assert "<script" not in cuerpo and "&lt;b&gt;no es html" in cuerpo
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "script-src" not in csp and "frame-ancestors 'none'" in csp
    assert "noindex" not in cuerpo   # la portada se puede indexar: el comercio la quiere en Google
    r = app.test_client().get(f"/reservar/{n['slug']}?s={corte}")
    cuerpo = r.get_data(as_text=True)
    assert "value='10:00'" in cuerpo and "name='telefono'" in cuerpo
    assert app.test_client().get("/reservar/no-existe").status_code == 404
    assert app.test_client().get("/reservar/../etc").status_code == 404


def test_reservar_por_el_link_de_punta_a_punta(app, db):
    n, corte, _, _ = _negocio(db)
    c = app.test_client()
    r = c.post(f"/reservar/{n['slug']}", data={"servicio_id": corte, "profesional_id": "", "fecha": "2026-10-27",
                                               "hora": "10:00", "cliente": "<script>alert(1)</script>",
                                               "telefono": "099 123 456"})
    assert r.status_code == 303
    url = r.headers["Location"]
    codigo = re.search(r"/t/([\w-]+)\?ok=nuevo", url).group(1)
    pagina = c.get(url).get_data(as_text=True)
    assert "quedó reservado" in pagina and "&lt;script&gt;" in pagina and "<script>" not in pagina
    assert "martes 27 de octubre, 10:00" in pagina.lower()
    ics = c.get(f"/reservar/{n['slug']}/t/{codigo}/turno.ics")
    assert ics.status_code == 200 and ics.mimetype == "text/calendar"
    # El mismo horario ya no se puede tomar.
    r = c.post(f"/reservar/{n['slug']}", data={"servicio_id": corte, "fecha": "2026-10-27", "hora": "10:00",
                                               "cliente": "Otro", "telefono": "099 999 999"})
    assert "error=" in r.headers["Location"]
    # Cambiar desde el link del turno.
    assert c.get(f"/reservar/{n['slug']}?cambiar={codigo}").status_code == 200
    r = c.post(f"/reservar/{n['slug']}", data={"servicio_id": corte, "fecha": "2026-10-27", "hora": "11:00",
                                               "cambiar": codigo})
    assert r.headers["Location"].endswith(f"/t/{codigo}?ok=cambio")
    assert ag.turno_por_codigo(db, codigo)["inicio"] == "2026-10-27 11:00"
    # Cancelar.
    r = c.post(f"/reservar/{n['slug']}/t/{codigo}/cancelar")
    assert r.headers["Location"].endswith("?ok=cancelado")
    assert ag.turno_por_codigo(db, codigo)["estado"] == "cancelado"


def test_el_link_del_turno_no_sirve_en_otro_comercio_ni_inventado(app, db):
    n, corte, _, _ = _negocio(db)
    otro = ag.crear_negocio(db, {"nombre": "Otro local"}, "t")
    t = _reserva(db, n, corte, "10:00", dia=date(2026, 10, 27))
    c = app.test_client()
    assert c.get(f"/reservar/{otro['slug']}/t/{t['codigo']}").status_code == 404
    assert c.post(f"/reservar/{otro['slug']}/t/{t['codigo']}/cancelar").status_code == 404
    assert c.get(f"/reservar/{n['slug']}/t/inventado-pero-largo-xxxx").status_code == 404
    assert c.get(f"/reservar/{otro['slug']}?cambiar={t['codigo']}").status_code == 404


def test_la_trampa_para_robots_no_reserva(app, db):
    n, corte, _, _ = _negocio(db)
    app.test_client().post(f"/reservar/{n['slug']}", data={"servicio_id": corte, "fecha": "2026-10-27",
                                                           "hora": "10:00", "cliente": "Bot", "telefono": "099 1",
                                                           "web": "http://spam"})
    assert ag.agenda(db, n["id"], date(2026, 10, 27)) == []


def test_el_panel_esta_en_el_menu_y_en_los_permisos():
    html = dashboard.DASHBOARD_HTML
    assert 'id="nav-reservas"' in html and 'id="reservas-panel"' in html
    assert "if (name === 'reservas') rvCargar();" in html
    fuente = open(dashboard.__file__, encoding="utf-8").read()
    listas = re.findall(r"const ALL_PANELS = \[([^\]]*)\]", fuente)
    assert len(listas) == 2 and all("'reservas'" in lista for lista in listas)
    assert "reservas:'Agenda online'" in fuente
