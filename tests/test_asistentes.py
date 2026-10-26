"""Fábrica de asistentes (services/asistentes.py, routes/asistentes.py)."""

import json
import sqlite3
from datetime import datetime

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import create_user, init_db
from services import asistentes as asis


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@scalerics.com")
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.delenv("CRM_PREVIEW", raising=False)
    r = str(tmp_path / "asistentes.db")
    init_db(r)
    return r


# Lunes 26/10/2026, 12:00 en Montevideo (UTC-3).
LUNES_MEDIODIA = datetime(2026, 10, 26, 15, 0)
HORARIO = {"0": "", "1": "10-20", "2": "10-20", "3": "10-20", "4": "10-20", "5": "10-20", "6": "9-14"}
COMPLETAS = {
    "ubicacion": "Estamos en 8 de Octubre 3456, frente a la plaza.",
    "pago": "Efectivo, débito, crédito hasta 3 cuotas y Mercado Pago.",
    "precios": "Corte $450, corte y barba $650.",
    "reserva": "Reservás desde el link o por acá con día y hora.",
    "cancelar": "Podés cancelar hasta 2 horas antes sin costo.",
    "servicios": "Corte, barba, perfilado y color.",
}


def _barberia(db, completar=True, **extra):
    a = asis.crear(db, {"nombre": "Barbería Don Julio", "familia": "turnos", "humano": "Julio",
                        "horario": HORARIO, **extra}, "test")
    if completar:
        for f in a["fichas"]:
            if f["clave"] in COMPLETAS:
                asis.guardar_ficha(db, a["id"], {**f, "contenido": COMPLETAS[f["clave"]]}, f["id"])
    return asis.detalle(db, a["id"])


def _resp(db, aid, texto, **kw):
    d = asis.detalle(db, aid)
    return asis.responder(d["config"], d["fichas"], texto, primero=False, ahora=LUNES_MEDIODIA, **kw)


def _ficha(d, clave):
    return next(f for f in d["fichas"] if f["clave"] == clave)


# ── texto ────────────────────────────────────────────────────────────────────

def test_plural_singular_y_tildes_dan_lo_mismo():
    assert asis.fichas_de("Envíos") == asis.fichas_de("envio")
    assert asis.fichas_de("¿Precios?") == asis.fichas_de("precio")


def test_los_sinonimos_suman_el_concepto():
    assert "#horario" in asis.fichas_de("¿a qué hora abren?")
    assert "#pago" in asis.fichas_de("¿aceptan tarjeta?")
    assert "#envio" in asis.fichas_de("¿hacen delivery?")
    assert asis.fichas_de("hola, ¿qué tal?") == ["tal"] or "#horario" not in asis.fichas_de("hola, ¿qué tal?")


# ── horario ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("texto,rangos", [
    ("9-13, 15:30-19", [["09:00", "13:00"], ["15:30", "19:00"]]),
    ("09:00 a 18:00", [["09:00", "18:00"]]),
    ("10hs-14hs", [["10:00", "14:00"]]),
    ("", []), ("cerrado", []), ("Cerrado", []),
])
def test_lee_el_horario_como_lo_escribe_la_gente(texto, rangos):
    assert asis.leer_dia(texto) == rangos


@pytest.mark.parametrize("texto,error", [
    ("de mañana", "No entiendo"), ("18-9", "después"), ("25-26", "no existe"), ("9-10,11-12,13-14,15-16,17-18", "Hasta 4"),
])
def test_el_horario_rechaza_lo_que_no_entiende(texto, error):
    with pytest.raises(asis.NoSePuede, match=error):
        asis.leer_dia(texto)


def test_el_horario_se_agrupa_por_dias_iguales():
    h = asis.validar_horario(HORARIO)
    assert asis.texto_horario(h) == ("Lunes: cerrado\nMartes a sábado de 10:00 a 20:00\nDomingo de 09:00 a 14:00")
    assert asis.texto_horario(asis.validar_horario({})) == ""


@pytest.mark.parametrize("utc,abierto,texto", [
    (datetime(2026, 10, 27, 15, 0), True, "hasta las 20:00"),          # martes 12:00
    (datetime(2026, 10, 26, 15, 0), False, "abrimos mañana a las 10:00"),  # lunes cerrado
    (datetime(2026, 10, 27, 11, 0), False, "abrimos hoy a las 10:00"),   # martes 08:00
    (datetime(2026, 10, 31, 23, 30), False, "abrimos mañana a las 09:00"),  # sábado 20:30
    (datetime(2026, 11, 2, 1, 0), False, "abrimos el martes a las 10:00"),  # domingo 22:00
])
def test_sabe_si_esta_abierto_en_hora_de_montevideo(utc, abierto, texto):
    e = asis.estado_local(asis.validar_horario(HORARIO), utc)
    assert e["abierto"] is abierto and texto in e["texto"]


# ── el motor ─────────────────────────────────────────────────────────────────

def test_crear_trae_las_fichas_del_rubro_sin_completar(db):
    a = asis.crear(db, {"nombre": "Pizzería La Esquina", "familia": "gastronomia"}, "test")
    claves = [f["clave"] for f in a["fichas"]]
    assert {"horario", "ubicacion", "pago", "precios", "envio", "carta", "reserva", "dietas"} <= set(claves)
    assert all(f["pendiente"] for f in a["fichas"])        # sin horario cargado, también el del horario
    assert a["estado"] == "armando" and not a["listo"]


@pytest.mark.parametrize("texto,tipo,clave", [
    ("¿a qué hora abren?", "horario", "horario"),
    ("¿están abiertos hoy?", "horario", "horario"),
    ("¿cuánto sale el corte?", "respuesta", "precios"),
    ("¿aceptan tarjeta?", "respuesta", "pago"),
    ("¿puedo pagar en cuotas?", "respuesta", "pago"),
    ("donde quedan?", "respuesta", "ubicacion"),
    ("quiero sacar un turno para mañana", "respuesta", "reserva"),
    ("necesito cancelar", "respuesta", "cancelar"),
    ("¿hacen color?", "respuesta", "servicios"),
])
def test_contesta_con_la_ficha_que_corresponde(db, texto, tipo, clave):
    d = _barberia(db)
    r = _resp(db, d["id"], texto)
    assert r["tipo"] == tipo and r["ficha_id"] == _ficha(d, clave)["id"]
    assert not r["derivar"]


def test_el_horario_dice_si_esta_abierto_ahora(db):
    d = _barberia(db)
    r = _resp(db, d["id"], "¿a qué hora abren?")
    assert "Martes a sábado de 10:00 a 20:00" in r["respuesta"]
    assert "Ahora estamos cerrados: abrimos mañana a las 10:00" in r["respuesta"]


@pytest.mark.parametrize("texto", ["¿venden autos 0km?", "¿cuál es la capital de Australia?", "¿hacen depilación?"])
def test_lo_que_no_sabe_no_lo_inventa_y_deriva(db, texto):
    r = _resp(db, _barberia(db)["id"], texto)
    assert r["tipo"] == "no_sabe" and r["derivar"] and r["ficha_id"] is None
    assert "Julio" in r["respuesta"] and "cerrados" in r["respuesta"]   # el lunes está cerrado


@pytest.mark.parametrize("texto", ["Quiero hablar con una persona", "tengo un reclamo", "¿me atiende alguien?",
                                   "¿Cuánto sale? quiero hablar con alguien"])
def test_pedir_una_persona_o_un_reclamo_deriva_siempre(db, texto):
    r = _resp(db, _barberia(db)["id"], texto)
    assert r["tipo"] == "derivar" and r["derivar"]


def test_las_palabras_propias_del_comercio_tambien_derivan(db):
    d = _barberia(db, palabras_derivar="mayorista\nfactura")
    assert _resp(db, d["id"], "¿me hacen factura?")["tipo"] == "derivar"
    assert _resp(db, d["id"], "¿me pasás la lista mayorista?")["tipo"] == "derivar"


def test_saludo_y_gracias(db):
    d = _barberia(db)
    assert _resp(db, d["id"], "Hola, buenas tardes")["tipo"] == "saludo"
    assert _resp(db, d["id"], "¡muchas gracias!")["tipo"] == "gracias"
    # gracias + pregunta: contesta la pregunta
    assert _resp(db, d["id"], "gracias, ¿y aceptan tarjeta?")["tipo"] == "respuesta"


def test_saluda_la_primera_vez_con_su_bienvenida(db):
    d = _barberia(db, bienvenida="¡Hola! Soy Julito, el asistente de la barbería.")
    r = asis.responder(d["config"], d["fichas"], "¿aceptan tarjeta?", primero=True, ahora=LUNES_MEDIODIA)
    assert r["respuesta"].startswith("¡Hola! Soy Julito") and COMPLETAS["pago"] in r["respuesta"]


def test_el_tono_usted_cambia_las_formulas_del_bot(db):
    d = _barberia(db, tono="usted")
    r = _resp(db, d["id"], "quiero hablar con una persona")
    assert "Le paso con Julio" in r["respuesta"] and "Te paso" not in r["respuesta"]


def test_una_ficha_sin_completar_o_apagada_no_se_usa(db):
    d = _barberia(db, completar=False)
    assert _resp(db, d["id"], "¿aceptan tarjeta?")["tipo"] == "no_sabe"     # [Completar] nunca sale al cliente
    d = _barberia(db)
    pago = _ficha(d, "pago")
    asis.guardar_ficha(db, d["id"], {**pago, "activa": False}, pago["id"])
    assert _resp(db, d["id"], "¿aceptan tarjeta?")["tipo"] == "no_sabe"


# ── fichas ───────────────────────────────────────────────────────────────────

def test_una_ficha_nueva_contesta_al_instante(db):
    d = _barberia(db)
    assert _resp(db, d["id"], "¿tienen donde estacionar?")["tipo"] == "no_sabe"
    asis.guardar_ficha(db, d["id"], {"titulo": "Estacionamiento", "contenido": "Hay parking en la esquina.",
                                     "variantes": "¿hay donde estacionar?\n¿tienen parking?"})
    r = _resp(db, d["id"], "¿tienen donde estacionar el auto?")
    assert r["tipo"] == "respuesta" and r["ficha"] == "Estacionamiento"


@pytest.mark.parametrize("data,error", [
    ({"titulo": " ", "contenido": "x"}, "título"),
    ({"titulo": "Algo", "contenido": ""}, "texto"),
])
def test_la_ficha_rechaza_lo_que_no_sirve(db, data, error):
    d = _barberia(db)
    with pytest.raises(asis.NoSePuede, match=error):
        asis.guardar_ficha(db, d["id"], data)


def test_la_ficha_del_horario_sigue_siendo_del_horario(db):
    d = _barberia(db)
    h = _ficha(d, "horario")
    d = asis.guardar_ficha(db, d["id"], {**h, "clave": "otra", "contenido": "En enero cerramos dos semanas."}, h["id"])
    assert _ficha(d, "horario")["contenido"] == "En enero cerramos dos semanas."
    assert "En enero cerramos" in _resp(db, d["id"], "¿abren hoy?")["respuesta"]


def test_no_se_toca_la_ficha_de_otro_asistente(db):
    a, b = _barberia(db), _barberia(db)
    with pytest.raises(asis.NoSePuede, match="no existe"):
        asis.guardar_ficha(db, b["id"], {"titulo": "x", "contenido": "y"}, a["fichas"][0]["id"])
    with pytest.raises(asis.NoSePuede, match="no existe"):
        asis.borrar_ficha(db, b["id"], a["fichas"][0]["id"])


def test_hay_tope_de_fichas(db, monkeypatch):
    d = _barberia(db)
    monkeypatch.setattr(asis, "MAX_FICHAS", len(d["fichas"]))
    with pytest.raises(asis.NoSePuede, match="Hasta"):
        asis.guardar_ficha(db, d["id"], {"titulo": "Una más", "contenido": "x"})


# ── pruebas y chequeos ───────────────────────────────────────────────────────

def test_sugerir_y_correr_pruebas_da_el_puntaje(db):
    d = _barberia(db)
    n = asis.sugerir_pruebas(db, d["id"])
    assert n >= asis.MIN_PRUEBAS
    assert asis.sugerir_pruebas(db, d["id"]) == 0           # no repite
    d = asis.correr_pruebas(db, d["id"], ahora=LUNES_MEDIODIA)
    assert d["ultima"]["total"] == n and d["ultima"]["puntaje"] == 100
    assert all(p["ok"] for p in d["pruebas"])
    assert d["listo"] and d["estado"] == "listo"


def test_una_prueba_que_falla_baja_el_puntaje_y_dice_que_obtuvo(db):
    d = _barberia(db)
    asis.sugerir_pruebas(db, d["id"])
    d = asis.sumar_prueba(db, d["id"], {"pregunta": "¿hacen depilación?", "esperado": "ficha",
                                        "ficha_id": _ficha(d, "servicios")["id"]})
    d = asis.correr_pruebas(db, d["id"], ahora=LUNES_MEDIODIA)
    mal = [p for p in d["pruebas"] if not p["ok"]]
    assert len(mal) == 1 and mal[0]["obtenido"] == "No supo"
    assert d["ultima"]["puntaje"] < 100


def test_cambiar_una_ficha_obliga_a_volver_a_correr(db, monkeypatch):
    d = _barberia(db)
    asis.sugerir_pruebas(db, d["id"])
    monkeypatch.setattr(asis, "_ahora", lambda: datetime(2026, 10, 26, 10, 0))
    assert asis.correr_pruebas(db, d["id"])["listo"]
    monkeypatch.setattr(asis, "_ahora", lambda: datetime(2026, 10, 26, 10, 5))
    pago = _ficha(d, "pago")
    d = asis.guardar_ficha(db, d["id"], {**pago, "contenido": "Solo efectivo."}, pago["id"])
    puntaje = next(c for c in d["chequeos"] if c["clave"] == "puntaje")
    assert not puntaje["ok"] and "volvé a correrlas" in puntaje["texto"] and not d["listo"]


@pytest.mark.parametrize("data,error", [
    ({"pregunta": "", "esperado": "derivar"}, "Falta"),
    ({"pregunta": "x", "esperado": "magia"}, "esperado"),
    ({"pregunta": "x", "esperado": "ficha", "ficha_id": 9999}, "Elegí la ficha"),
])
def test_la_prueba_rechaza_lo_que_no_sirve(db, data, error):
    with pytest.raises(asis.NoSePuede, match=error):
        asis.sumar_prueba(db, _barberia(db)["id"], data)


def test_correr_sin_pruebas_avisa(db):
    with pytest.raises(asis.NoSePuede, match="Sugerir"):
        asis.correr_pruebas(db, _barberia(db)["id"])


def test_los_chequeos_dicen_lo_que_falta(db):
    a = asis.crear(db, {"nombre": "Pizzería", "familia": "gastronomia"}, "test")
    mal = {c["clave"] for c in a["chequeos"] if not c["ok"]}
    assert mal == {"fichas", "pendientes", "horario", "humano", "pruebas", "puntaje"}
    with pytest.raises(asis.NoSePuede, match="Todavía no está listo"):
        asis.conectar(db, a["id"], True)


def test_conectar_pide_la_clave(db):
    d = _barberia(db)
    asis.sugerir_pruebas(db, d["id"])
    asis.correr_pruebas(db, d["id"])
    with pytest.raises(asis.NoSePuede, match="clave"):
        asis.conectar(db, d["id"], True)
    asis.nueva_clave(db, d["id"])
    assert asis.conectar(db, d["id"], True)["estado"] == "conectado"


# ── conversaciones ───────────────────────────────────────────────────────────

def test_no_guarda_el_telefono_y_saluda_una_vez(db):
    d = _barberia(db)
    r1 = asis.conversar(db, d["id"], "¿aceptan tarjeta?", "api", "+598 99 123 456")
    r2 = asis.conversar(db, d["id"], "¿dónde quedan?", "api", "+598 99 123 456")
    assert r1["respuesta"].startswith("¡Hola! Soy el asistente de Barbería Don Julio")
    assert not r2["respuesta"].startswith("¡Hola!") and r1["conversacion"] == r2["conversacion"]
    conn = sqlite3.connect(db)
    filas = conn.execute("SELECT * FROM as_mensajes").fetchall()
    conn.close()
    assert "99 123 456" not in json.dumps(filas) and "+598" not in json.dumps(filas)


def test_lo_que_no_supo_se_vuelve_ficha_y_prueba(db):
    d = _barberia(db)
    asis.conversar(db, d["id"], "¿tienen gift card?", "api", "+598 1")
    d = asis.detalle(db, d["id"])
    assert [m["texto"] for m in d["sin_respuesta"]] == ["¿tienen gift card?"]
    d = asis.ficha_desde_mensaje(db, d["id"], d["sin_respuesta"][0]["id"],
                                 {"titulo": "Gift card", "contenido": "Sí, de $500 en adelante."})
    assert d["sin_respuesta"] == []
    nueva = next(f for f in d["fichas"] if f["titulo"] == "Gift card")
    assert nueva["variantes"][0] == "¿tienen gift card?"
    assert any(p["pregunta"] == "¿tienen gift card?" and p["ficha_id"] == nueva["id"] for p in d["pruebas"])
    assert _resp(db, d["id"], "¿venden gift cards?")["ficha"] == "Gift card"


def test_ignorar_saca_de_sin_respuesta(db):
    d = _barberia(db)
    asis.conversar(db, d["id"], "¿venden autos?", "simulador", "u1")
    mid = asis.detalle(db, d["id"])["sin_respuesta"][0]["id"]
    assert asis.resolver_mensaje(db, d["id"], mid)["sin_respuesta"] == []


def test_el_tope_por_hora_frena(db):
    d = _barberia(db)
    asis.conversar(db, d["id"], "hola", "link", "a", tope=2)
    asis.conversar(db, d["id"], "hola", "link", "b", tope=2)
    with pytest.raises(asis.NoSePuede, match="Demasiados"):
        asis.conversar(db, d["id"], "hola", "link", "c", tope=2)


def test_el_mensaje_tiene_largo_maximo(db):
    with pytest.raises(asis.NoSePuede, match="largo"):
        asis.conversar(db, _barberia(db)["id"], "a" * (asis.MAX_MENSAJE + 1), "link", "a")


def test_resumen_y_semana(db):
    d = _barberia(db)
    for t in ["¿aceptan tarjeta?", "¿venden autos?", "quiero hablar con una persona", "hola"]:
        asis.conversar(db, d["id"], t, "api", "x")
    lista = asis.listar(db)
    assert lista[0]["semana"]["mensajes"] == 4 and lista[0]["semana"]["resolucion"] == 50
    r = asis.resumen(lista)
    assert r["total"] == 1 and r["sin_respuesta"] == 1 and r["resolucion"] == 50


def test_exportar_no_lleva_clave_ni_pendientes(db):
    d = _barberia(db)
    asis.nueva_clave(db, d["id"])
    asis.guardar_ficha(db, d["id"], {"titulo": "Pendiente", "contenido": "[Completar] algo"})
    e = asis.exportar(db, d["id"])
    texto = json.dumps(e)
    assert "asis_" not in texto and "Pendiente" not in texto
    assert {f["titulo"] for f in e["fichas"]} >= {"Formas de pago", "Horario"}


def test_borrar_se_lleva_todo(db):
    d = _barberia(db)
    asis.sugerir_pruebas(db, d["id"])
    asis.conversar(db, d["id"], "hola", "api", "x")
    asis.borrar(db, d["id"])
    conn = sqlite3.connect(db)
    for t in ("as_asistentes", "as_fichas", "as_pruebas", "as_mensajes"):
        assert conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0
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


def _cliente(db, paneles=("asistentes",)):
    uid = _usuario(db, "dev@scalerics.com", list(paneles))
    c = _app(db).test_client()
    with c.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Dev"
    return c


def test_la_api_del_equipo_pide_el_panel(db):
    c = _cliente(db, paneles=("tasks",))
    assert c.get("/api/asistentes").status_code == 403
    assert c.post("/api/asistentes", json={"nombre": "x"}).status_code == 403
    anonimo = _app(db).test_client()
    assert anonimo.get("/api/asistentes").status_code in (302, 401, 403)


def test_el_equipo_arma_prueba_y_conecta(db):
    c = _cliente(db)
    r = c.post("/api/asistentes", json={"nombre": "Barbería", "familia": "turnos", "humano": "Julio"})
    assert r.status_code == 201
    a = r.get_json()["asistente"]
    assert a["link"].endswith("/asistente/" + a["token"]) and a["api"].endswith("/mensaje")
    aid = a["id"]
    assert c.put(f"/api/asistentes/{aid}", json={"horario": HORARIO}).get_json()["ok"]
    for f in a["fichas"]:
        if f["clave"] in COMPLETAS:
            r = c.put(f"/api/asistentes/{aid}/fichas/{f['id']}", json={**f, "contenido": COMPLETAS[f["clave"]]})
            assert r.status_code == 200
    assert c.post(f"/api/asistentes/{aid}/pruebas/sugerir").get_json()["sumadas"] >= 10
    assert c.post(f"/api/asistentes/{aid}/pruebas/correr").get_json()["asistente"]["listo"]
    r = c.post(f"/api/asistentes/{aid}/simular", json={"texto": "¿aceptan tarjeta?", "sesion": "s1"})
    assert r.get_json()["respuesta"]["ficha"] == "Formas de pago"
    assert c.post(f"/api/asistentes/{aid}/conectar", json={"conectado": True}).status_code == 400   # sin clave
    r = c.post(f"/api/asistentes/{aid}/clave")
    clave = r.get_json()["clave"]
    assert clave.startswith("asis_") and r.headers["Cache-Control"] == "no-store"
    assert clave not in json.dumps(c.get(f"/api/asistentes/{aid}").get_json())   # no se vuelve a ver
    assert c.post(f"/api/asistentes/{aid}/conectar", json={"conectado": True}).get_json()["asistente"]["conectado"]
    assert c.get(f"/api/asistentes/{aid}/exportar").get_json()["exportado"]["nombre"] == "Barbería"
    assert c.get("/api/asistentes").get_json()["resumen"]["conectados"] == 1
    assert c.get("/api/asistentes/9999").status_code == 404
    assert c.delete(f"/api/asistentes/{aid}").get_json()["ok"]


def _conectado(db):
    d = _barberia(db)
    asis.sugerir_pruebas(db, d["id"])
    asis.correr_pruebas(db, d["id"])
    clave = asis.nueva_clave(db, d["id"])
    asis.conectar(db, d["id"], True)
    return asis.detalle(db, d["id"]), clave


def test_la_api_de_whatsapp_pide_clave_y_contesta(db):
    d, clave = _conectado(db)
    c = _app(db).test_client()
    url = f"/asistente/api/{d['token']}/mensaje"
    cuerpo = {"de": "+59899123456", "texto": "¿aceptan tarjeta?"}
    assert c.post(url, json=cuerpo).status_code == 401
    assert c.post(url, json=cuerpo, headers={"X-Asistente-Clave": "asis_mala"}).status_code == 401
    assert c.post("/asistente/api/otro/mensaje", json=cuerpo, headers={"X-Asistente-Clave": clave}).status_code == 401
    r = c.post(url, json=cuerpo, headers={"X-Asistente-Clave": clave})
    assert r.status_code == 200
    j = r.get_json()
    assert j["tipo"] == "respuesta" and j["ficha"] == "Formas de pago" and not j["derivar"]
    r = c.post(url, json={"de": "x", "texto": "quiero hablar con una persona"}, headers={"Authorization": "Bearer " + clave})
    assert r.get_json()["derivar"] is True
    assert c.post(url, json={"texto": "hola"}, headers={"X-Asistente-Clave": clave}).status_code == 400


def test_la_api_desconectada_no_contesta_y_el_tope_da_429(db, monkeypatch):
    d, clave = _conectado(db)
    c = _app(db).test_client()
    url = f"/asistente/api/{d['token']}/mensaje"
    monkeypatch.setattr(asis, "TOPE_API_HORA", 1)
    h = {"X-Asistente-Clave": clave}
    assert c.post(url, json={"de": "a", "texto": "hola"}, headers=h).status_code == 200
    assert c.post(url, json={"de": "a", "texto": "hola"}, headers=h).status_code == 429
    asis.conectar(db, d["id"], False)
    assert c.post(url, json={"de": "a", "texto": "hola"}, headers=h).status_code == 409


def test_el_link_de_prueba_chatea_sin_sesion(db):
    d = _barberia(db)
    c = _app(db).test_client()
    assert c.get("/asistente/no-existe").status_code == 404
    r = c.get(f"/asistente/{d['token']}")
    h = r.get_data(as_text=True)
    assert r.status_code == 200 and "Probá tu asistente" in h and "<script" not in h
    assert "default-src 'none'" in r.headers["Content-Security-Policy"] and "noindex" in r.headers["X-Robots-Tag"]
    conv = "conversacion-de-prueba-1"
    r = c.post(f"/asistente/{d['token']}/c/{conv}", data={"texto": "¿aceptan tarjeta?"})
    assert r.status_code == 303 and f"c={conv}" in r.headers["Location"]
    h = c.get(f"/asistente/{d['token']}?c={conv}").get_data(as_text=True)
    assert "¿aceptan tarjeta?" in h and "Mercado Pago" in h
    assert asis.detalle(db, d["id"])["semana"]["mensajes"] == 1


def test_el_link_escapa_lo_que_escribe_cualquiera_y_tiene_trampa(db):
    d = _barberia(db)
    c = _app(db).test_client()
    conv = "conversacion-de-prueba-2"
    c.post(f"/asistente/{d['token']}/c/{conv}", data={"texto": "<script>alert(1)</script>"})
    h = c.get(f"/asistente/{d['token']}?c={conv}").get_data(as_text=True)
    assert "<script>alert" not in h and "&lt;script&gt;" in h
    c.post(f"/asistente/{d['token']}/c/{conv}", data={"texto": "hola", "web": "http://spam"})
    assert asis.detalle(db, d["id"])["semana"]["mensajes"] == 1     # el robot no cuenta
    assert c.post(f"/asistente/{d['token']}/c/corto", data={"texto": "hola"}).status_code == 404


def test_el_panel_esta_en_el_menu_y_en_el_html(db):
    h = _cliente(db).get("/").get_data(as_text=True)
    assert 'id="nav-asistentes"' in h and 'id="asistentes-panel"' in h
    assert "async function faCargar()" in h and "/*ASISTENTES_JS*/" not in h


def test_los_roles_que_ven_proyectos_o_whatsapp_reciben_el_panel(tmp_path):
    r = str(tmp_path / "roles.db")
    init_db(r)
    conn = sqlite3.connect(r)
    for nombre, paneles in (("pm", ["projects"]), ("ventas", ["wa"]), ("contador", ["finanzas"])):
        conn.execute("INSERT INTO roles (name, panel_access) VALUES (?, ?)", (nombre, json.dumps(paneles)))
    conn.execute("DELETE FROM panel_grants_aplicados WHERE panel = 'asistentes'")
    conn.commit()
    conn.close()
    init_db(r)
    conn = sqlite3.connect(r)
    roles = {n: json.loads(a) for n, a in conn.execute(
        "SELECT name, panel_access FROM roles WHERE name IN ('pm','ventas','contador')")}
    conn.close()
    assert "asistentes" in roles["pm"] and "asistentes" in roles["ventas"] and "asistentes" not in roles["contador"]


def test_el_js_del_panel_compila(tmp_path):
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("sin node")
    f = tmp_path / "fa.js"
    f.write_text(dashboard.ASISTENTES_JS)
    r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_el_tono_usted_no_toca_lo_que_escribio_el_equipo(db):
    d = _barberia(db, tono="usted")
    cancelar = _ficha(d, "cancelar")
    assert _resp(db, d["id"], "necesito cancelar")["respuesta"] == COMPLETAS["cancelar"]
    assert cancelar["contenido"].startswith("Podés")
