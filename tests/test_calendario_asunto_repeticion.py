"""Reuniones de otro asunto, invitados y reuniones que se repiten (Juan, 15/9).

"Agrega entonces tambien una opcion de reunion en el calendario que no sea solo
para clientes sino otro asunto que se pueda escribir, y la opcion de agregar
reuniones que se repitan". El caso real: "Marketing semanal", todos los
viernes a las 19:00, con tres invitados de afuera.

Lo que fijan estos tests:

  otro asunto   sin cliente, con el titulo obligatorio. No toca el estado de
                ningun lead ni la actividad de ventas.
  invitados     mails validados, guardados en el CRM. Crear una reunion NO
                crea evento en Google (desde 81f19fb) y no manda nada: el
                Google de mentira de este archivo verifica que no se le pida
                ni un insert.
  repeticion    la regla vive en la fila y el servidor expande las ocurrencias
                del rango pedido. Hora de pared de Montevideo: un viernes
                19:00 no se corre de dia ni de hora, tampoco a las 23:30.
  alcance       editar y borrar "solo esta", "esta y las siguientes" o "todas".
  sync          si la misma reunion tambien esta en Google, el sync no la
                importa como reunion con un lead inventado.
"""

import sqlite3
from unittest.mock import MagicMock

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import (create_user, get_business, get_meeting, get_reunion_asunto, init_db,
                      insert_business)
from services import recurrencia as rec


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "token-de-test")
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    app = dashboard.create_app(ruta)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def cliente(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def google(monkeypatch):
    """Ningun test de aca habla con Google. El servicio de mentira anota lo
    que se le pide y el sync no trae nada, salvo que el test lo cargue."""
    import routes.calendar as cal
    service = MagicMock()
    service.events.return_value.list.return_value.execute.return_value = {"items": []}
    monkeypatch.setattr(cal, "_get_calendar_service", lambda: (service, None))
    return service


_AUTH = {"x-admin-token": "token-de-test"}
VIERNES = {"freq": "semanal", "dias": [4], "fin": "nunca"}


def _crear(cliente, **campos):
    cuerpo = {"tipo": "asunto", "title": "Marketing semanal", "date": "2026-09-04",
              "time": "19:00", "duration_min": 60}
    cuerpo.update(campos)
    return cliente.post("/api/calendar/events", json=cuerpo, headers=_AUTH).get_json()


def _eventos(cliente, desde, hasta):
    r = cliente.get(f"/api/calendar/events?start={desde}&end={hasta}", headers=_AUTH)
    return r.get_json()["events"]


def _cuando(eventos, titulo="Marketing semanal"):
    return [(e["date"], e["time"]) for e in eventos if e["title"] == titulo]


def _filas(db, sql, *params):
    con = sqlite3.connect(db)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


# ── otro asunto ──────────────────────────────────────────────────────────────

def test_otro_asunto_se_crea_sin_cliente(app, cliente):
    db = app.config["DB_PATH"]

    d = _crear(cliente, description="Ideas para la pauta de octubre")

    assert d["ok"] is True, d
    fila = get_reunion_asunto(db, d["asunto_id"])
    assert fila["title"] == "Marketing semanal"
    assert fila["description"] == "Ideas para la pauta de octubre"
    assert fila["start_at"] == "2026-09-04T19:00:00"
    [ev] = _eventos(cliente, "2026-09-01", "2026-09-30")
    assert ev["tipo"] == "asunto" and ev["client_id"] is None
    assert ev["title"] == "Marketing semanal" and ev["id"] == f"asunto-{d['asunto_id']}"
    assert _filas(db, "SELECT COUNT(*) FROM businesses") == [(0,)]
    assert _filas(db, "SELECT COUNT(*) FROM meetings") == [(0,)]


@pytest.mark.parametrize("titulo", ["", "   "])
def test_otro_asunto_sin_titulo_no_se_guarda(app, cliente, titulo):
    d = _crear(cliente, title=titulo)

    assert d["ok"] is False
    assert "asunto" in d["error"]
    assert _filas(app.config["DB_PATH"], "SELECT COUNT(*) FROM reuniones_asunto") == [(0,)]


def test_con_un_cliente_el_cliente_sigue_siendo_obligatorio(cliente):
    d = _crear(cliente, tipo="cliente", title="Demo")
    assert d["ok"] is False and "cliente" in d["error"]


def test_otro_asunto_no_cuenta_como_reunion_de_ventas(app, cliente):
    """Ni estado de lead, ni `meeting_scheduled` (lo que suman las metricas del
    SDR), ni actividad sobre un lead."""
    db = app.config["DB_PATH"]

    _crear(cliente, repeticion=VIERNES)

    assert _filas(db, "SELECT action, entity_type FROM activity_log") == [
        ("asunto_agendado", "asunto")]


def test_un_tipo_desconocido_no_se_guarda(cliente):
    assert _crear(cliente, tipo="fiesta")["ok"] is False


# ── invitados ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("malo", ["pablo@", "sin-arroba.com", "ana@agencia", "@marca.com",
                                  "ana@@agencia.com"])
def test_un_mail_mal_escrito_no_deja_guardar(app, cliente, malo):
    d = _crear(cliente, invitados=f"ana@agencia.com, {malo}")

    assert d["ok"] is False
    assert malo in d["error"]
    assert _filas(app.config["DB_PATH"], "SELECT COUNT(*) FROM reuniones_asunto") == [(0,)]


def test_los_invitados_se_guardan_limpios_y_sin_repetir(cliente):
    _crear(cliente, invitados=["ana@agencia.com", " Pablo@Estudio.uy; ana@AGENCIA.com",
                               "sofi@marca.com.uy"])

    [ev] = _eventos(cliente, "2026-09-01", "2026-09-30")
    assert ev["invitados"] == ["ana@agencia.com", "Pablo@Estudio.uy", "sofi@marca.com.uy"]


def test_una_reunion_con_cliente_tambien_lleva_invitados(app, cliente):
    lid = insert_business(app.config["DB_PATH"], {"name": "Optica Luz"})

    d = _crear(cliente, tipo="cliente", client_id=lid, title="Demo Optica Luz",
               invitados="socio@opticaluz.uy")

    assert d["ok"] is True
    [ev] = _eventos(cliente, "2026-09-01", "2026-09-30")
    assert ev["invitados"] == ["socio@opticaluz.uy"] and ev["tipo"] == "cliente"


def test_crear_no_le_pide_nada_a_google_ni_manda_invitaciones(app, cliente, google):
    """El CRM no crea eventos en Google desde el 1/6/2026: los invitados quedan
    guardados y a nadie le llega un mail. Si esto cambia, que sea a proposito."""
    lid = insert_business(app.config["DB_PATH"], {"name": "Optica Luz"})

    _crear(cliente, invitados="ana@agencia.com", repeticion=VIERNES)
    _crear(cliente, tipo="cliente", client_id=lid, title="Demo", invitados="socio@opticaluz.uy")

    eventos = google.events.return_value
    eventos.insert.assert_not_called()
    eventos.patch.assert_not_called()
    eventos.update.assert_not_called()


def test_cambiar_los_invitados_desde_el_editor(app, cliente):
    d = _crear(cliente, invitados="ana@agencia.com")

    r = cliente.patch(f"/api/calendar/asuntos/{d['asunto_id']}", headers=_AUTH,
                      json={"date": "2026-09-04", "time": "19:00",
                            "invitados": ["ana@agencia.com", "sofi@marca.com"]})
    assert r.get_json()["ok"] is True
    [ev] = _eventos(cliente, "2026-09-01", "2026-09-30")
    assert ev["invitados"] == ["ana@agencia.com", "sofi@marca.com"]

    r = cliente.patch(f"/api/calendar/asuntos/{d['asunto_id']}", headers=_AUTH,
                      json={"date": "2026-09-04", "time": "19:00", "invitados": "sofi@"})
    assert r.status_code == 400


# ── repeticion: viernes 19:00 en Montevideo ──────────────────────────────────

def test_viernes_19_en_un_mes_con_cuatro_y_uno_con_cinco_viernes(cliente):
    d = _crear(cliente, repeticion=VIERNES, invitados="a@x.com, b@y.com, c@z.com")
    assert d["ok"] is True

    setiembre = _eventos(cliente, "2026-09-01", "2026-09-30")
    octubre = _eventos(cliente, "2026-10-01", "2026-10-31")

    assert _cuando(setiembre) == [("2026-09-04", "19:00"), ("2026-09-11", "19:00"),
                                  ("2026-09-18", "19:00"), ("2026-09-25", "19:00")]
    assert _cuando(octubre) == [("2026-10-02", "19:00"), ("2026-10-09", "19:00"),
                                ("2026-10-16", "19:00"), ("2026-10-23", "19:00"),
                                ("2026-10-30", "19:00")]
    for ev in setiembre + octubre:
        assert ev["serie"] is True and ev["ocurrencia"] == ev["date"]
        assert ev["id"] == f"asunto-{d['asunto_id']}@{ev['date']}"
        assert ev["repeticion"] == VIERNES
        assert ev["invitados"] == ["a@x.com", "b@y.com", "c@z.com"]
    assert len({e["id"] for e in setiembre + octubre}) == 9


def test_la_semana_pide_el_mes_y_la_semana_que_cruza(cliente):
    """La vista semana pide la semana entera y su mes: 28/9 al 4/10."""
    _crear(cliente, repeticion=VIERNES)
    assert _cuando(_eventos(cliente, "2026-09-01", "2026-10-04"))[-2:] == [
        ("2026-09-25", "19:00"), ("2026-10-02", "19:00")]


def test_a_las_2330_no_se_pasa_al_sabado_aunque_en_utc_ya_lo_sea(cliente, monkeypatch):
    """Viernes 23:30 en Montevideo es sabado 02:30 en UTC, que es donde corre el
    servidor. La serie es hora de pared: ninguna ocurrencia cae sabado."""
    import time
    monkeypatch.setenv("TZ", "UTC")
    if hasattr(time, "tzset"):
        time.tzset()
    _crear(cliente, date="2026-09-25", time="23:30", repeticion=VIERNES)

    octubre = _eventos(cliente, "2026-10-01", "2026-10-31")

    assert _cuando(octubre) == [(f"2026-10-{d:02d}", "23:30") for d in (2, 9, 16, 23, 30)]
    assert _eventos(cliente, "2026-10-31", "2026-10-31") == []
    assert _cuando(_eventos(cliente, "2026-09-01", "2026-09-30")) == [("2026-09-25", "23:30")]


def test_termina_en_una_fecha(cliente):
    _crear(cliente, repeticion={"freq": "semanal", "dias": [4], "fin": "fecha",
                                "hasta": "2026-09-18"})

    assert [f for f, _ in _cuando(_eventos(cliente, "2026-09-01", "2026-09-30"))] == [
        "2026-09-04", "2026-09-11", "2026-09-18"]
    assert _eventos(cliente, "2026-10-01", "2026-10-31") == []


def test_termina_despues_de_n_veces_aunque_cruce_de_mes(cliente):
    _crear(cliente, date="2026-09-25", repeticion={"freq": "semanal", "dias": [4],
                                                   "fin": "veces", "veces": 3})

    assert [f for f, _ in _cuando(_eventos(cliente, "2026-09-01", "2026-09-30"))] == ["2026-09-25"]
    assert [f for f, _ in _cuando(_eventos(cliente, "2026-10-01", "2026-10-31"))] == [
        "2026-10-02", "2026-10-09"]


@pytest.mark.parametrize("regla,pedazo", [
    ({"freq": "cada tanto"}, "cada cuánto"),
    ({"freq": "semanal", "dias": [9]}, "días"),
    ({"freq": "semanal", "fin": "fecha", "hasta": "2026-08-01"}, "antes"),
    ({"freq": "semanal", "fin": "fecha"}, "fecha"),
    ({"freq": "diaria", "fin": "veces", "veces": 0}, "veces"),
])
def test_una_repeticion_invalida_no_se_guarda(app, cliente, regla, pedazo):
    d = _crear(cliente, repeticion=regla)
    assert d["ok"] is False and pedazo in d["error"], d
    assert _filas(app.config["DB_PATH"], "SELECT COUNT(*) FROM reuniones_asunto") == [(0,)]


def test_las_otras_frecuencias():
    base = {"start_at": "2026-09-04T19:00:00", "end_at": "2026-09-04T20:00:00", "title": "X"}

    def fechas(regla, desde, hasta, **fila):
        f = dict(base, repeticion=__import__("json").dumps(regla), **fila)
        return [o["date"] for o in rec.ocurrencias(f, desde, hasta)]

    assert fechas({"freq": "quincenal", "dias": [4], "fin": "nunca"}, "2026-09-01", "2026-10-31") == [
        "2026-09-04", "2026-09-18", "2026-10-02", "2026-10-16", "2026-10-30"]
    assert fechas({"freq": "diaria", "fin": "veces", "veces": 3}, "2026-09-01", "2026-09-30") == [
        "2026-09-04", "2026-09-05", "2026-09-06"]
    assert fechas({"freq": "semanal", "dias": [0, 4], "fin": "nunca"}, "2026-09-01", "2026-09-14") == [
        "2026-09-04", "2026-09-07", "2026-09-11", "2026-09-14"]
    # Mensual el 31: los meses sin 31 no tienen reunion (como una RRULE).
    assert fechas({"freq": "mensual", "fin": "nunca"}, "2026-01-01", "2026-06-30",
                  start_at="2026-01-31T10:00:00", end_at="2026-01-31T11:00:00") == [
        "2026-01-31", "2026-03-31", "2026-05-31"]


# ── editar y borrar una serie ────────────────────────────────────────────────

def _serie(cliente):
    return _crear(cliente, repeticion=VIERNES)["asunto_id"]


def _patch(cliente, asunto_id, **cuerpo):
    return cliente.patch(f"/api/calendar/asuntos/{asunto_id}", json=cuerpo, headers=_AUTH)


def test_mover_solo_esta(cliente):
    aid = _serie(cliente)

    r = _patch(cliente, aid, date="2026-09-17", time="18:00", title="Marketing semanal",
               duration_min=60, ocurrencia="2026-09-18", alcance="esta")

    assert r.get_json()["ok"] is True
    eventos = _eventos(cliente, "2026-09-01", "2026-09-30")
    assert _cuando(eventos) == [("2026-09-04", "19:00"), ("2026-09-11", "19:00"),
                                ("2026-09-17", "18:00"), ("2026-09-25", "19:00")]
    movida = [e for e in eventos if e["date"] == "2026-09-17"][0]
    assert movida["id"] == f"asunto-{aid}@2026-09-18" and movida["ocurrencia"] == "2026-09-18"
    assert len(_cuando(_eventos(cliente, "2026-10-01", "2026-10-31"))) == 5


def test_renombrar_solo_esta(cliente):
    aid = _serie(cliente)
    _patch(cliente, aid, date="2026-09-11", time="19:00", title="Marketing: cierre de mes",
           ocurrencia="2026-09-11", alcance="esta")
    titulos = [e["title"] for e in _eventos(cliente, "2026-09-01", "2026-09-30")]
    assert titulos == ["Marketing semanal", "Marketing: cierre de mes",
                       "Marketing semanal", "Marketing semanal"]


def test_cambiar_todas(cliente):
    aid = _serie(cliente)

    _patch(cliente, aid, date="2026-09-11", time="18:30", title="Marketing",
           duration_min=45, ocurrencia="2026-09-11", alcance="todas")

    eventos = _eventos(cliente, "2026-09-01", "2026-10-31")
    assert _cuando(eventos, "Marketing") == [(f, "18:30") for f in (
        "2026-09-04", "2026-09-11", "2026-09-18", "2026-09-25", "2026-10-02",
        "2026-10-09", "2026-10-16", "2026-10-23", "2026-10-30")]
    assert {e["duration_min"] for e in eventos} == {45}


def test_mover_todas_a_otro_dia_mueve_el_dia_de_la_semana(cliente):
    aid = _serie(cliente)
    _patch(cliente, aid, date="2026-09-17", time="19:00", ocurrencia="2026-09-18", alcance="todas")
    assert [f for f, _ in _cuando(_eventos(cliente, "2026-09-01", "2026-09-30"))] == [
        "2026-09-03", "2026-09-10", "2026-09-17", "2026-09-24"]


def test_esta_y_las_siguientes(app, cliente):
    aid = _serie(cliente)

    _patch(cliente, aid, date="2026-09-18", time="20:00", title="Marketing semanal",
           ocurrencia="2026-09-18", alcance="siguientes")

    assert _cuando(_eventos(cliente, "2026-09-01", "2026-09-30")) == [
        ("2026-09-04", "19:00"), ("2026-09-11", "19:00"),
        ("2026-09-18", "20:00"), ("2026-09-25", "20:00")]
    assert len(_cuando(_eventos(cliente, "2026-10-01", "2026-10-31"))) == 5
    assert _filas(app.config["DB_PATH"], "SELECT COUNT(*) FROM reuniones_asunto") == [(2,)]


def test_esta_y_las_siguientes_respeta_la_cantidad(cliente):
    """Una serie de 4 veces cortada en la tercera: 2 de la vieja y 2 de la nueva."""
    aid = _crear(cliente, repeticion={"freq": "semanal", "dias": [4], "fin": "veces",
                                      "veces": 4})["asunto_id"]
    _patch(cliente, aid, date="2026-09-18", time="08:00", ocurrencia="2026-09-18",
           alcance="siguientes")
    assert _cuando(_eventos(cliente, "2026-09-01", "2026-10-31")) == [
        ("2026-09-04", "19:00"), ("2026-09-11", "19:00"),
        ("2026-09-18", "08:00"), ("2026-09-25", "08:00")]


def test_borrar_solo_esta(app, cliente):
    aid = _serie(cliente)

    r = cliente.delete(f"/api/calendar/asuntos/{aid}?alcance=esta&ocurrencia=2026-09-11",
                       headers=_AUTH)

    assert r.get_json() == {"ok": True, "borrada": False}
    assert [f for f, _ in _cuando(_eventos(cliente, "2026-09-01", "2026-09-30"))] == [
        "2026-09-04", "2026-09-18", "2026-09-25"]
    assert get_reunion_asunto(app.config["DB_PATH"], aid) is not None


def test_borrar_esta_y_las_siguientes(cliente):
    aid = _serie(cliente)
    cliente.delete(f"/api/calendar/asuntos/{aid}?alcance=siguientes&ocurrencia=2026-09-18",
                   headers=_AUTH)
    assert [f for f, _ in _cuando(_eventos(cliente, "2026-09-01", "2026-09-30"))] == [
        "2026-09-04", "2026-09-11"]
    assert _eventos(cliente, "2026-10-01", "2026-10-31") == []


def test_borrar_todas(app, cliente):
    aid = _serie(cliente)
    r = cliente.delete(f"/api/calendar/asuntos/{aid}?alcance=todas&ocurrencia=2026-09-18",
                       headers=_AUTH)
    assert r.get_json()["borrada"] is True
    assert get_reunion_asunto(app.config["DB_PATH"], aid) is None
    assert _eventos(cliente, "2026-09-01", "2026-10-31") == []


def test_borrar_la_unica_que_quedaba_borra_la_serie(app, cliente):
    aid = _crear(cliente, repeticion={"freq": "semanal", "dias": [4], "fin": "veces",
                                      "veces": 1})["asunto_id"]
    r = cliente.delete(f"/api/calendar/asuntos/{aid}?alcance=esta&ocurrencia=2026-09-04",
                       headers=_AUTH)
    assert r.get_json()["borrada"] is True
    assert get_reunion_asunto(app.config["DB_PATH"], aid) is None


def test_una_fecha_que_no_es_de_la_serie_no_toca_nada(cliente):
    aid = _serie(cliente)
    r = _patch(cliente, aid, date="2026-09-17", time="10:00", ocurrencia="2026-09-17",
               alcance="esta")
    assert r.status_code == 400
    r = cliente.delete(f"/api/calendar/asuntos/{aid}?alcance=esta&ocurrencia=2026-09-16",
                       headers=_AUTH)
    assert r.status_code == 400
    assert len(_cuando(_eventos(cliente, "2026-09-01", "2026-09-30"))) == 4


def test_una_suelta_de_otro_asunto_se_mueve_y_se_borra(app, cliente):
    aid = _crear(cliente)["asunto_id"]
    r = _patch(cliente, aid, date="2026-09-05", time="09:15", duration_min=30)
    assert r.get_json()["ok"] is True
    fila = get_reunion_asunto(app.config["DB_PATH"], aid)
    assert (fila["start_at"], fila["end_at"]) == ("2026-09-05T09:15:00", "2026-09-05T09:45:00")
    cliente.delete(f"/api/calendar/asuntos/{aid}", headers=_AUTH)
    assert get_reunion_asunto(app.config["DB_PATH"], aid) is None


# ── una reunion con cliente que se repite ────────────────────────────────────

def _serie_de_cliente(app, cliente):
    db = app.config["DB_PATH"]
    lid = insert_business(db, {"name": "Optica Luz"})
    d = _crear(cliente, tipo="cliente", client_id=lid, title="Seguimiento Optica Luz",
               repeticion=VIERNES)
    assert d["ok"] is True, d
    return db, lid, d["meeting_id"]


def test_una_reunion_con_cliente_que_se_repite(app, cliente):
    db, lid, mid = _serie_de_cliente(app, cliente)

    eventos = _eventos(cliente, "2026-09-01", "2026-09-30")

    assert _cuando(eventos, "Seguimiento Optica Luz") == [
        (f"2026-09-{d:02d}", "19:00") for d in (4, 11, 18, 25)]
    assert {e["tipo"] for e in eventos} == {"cliente"}
    assert {e["client_name"] for e in eventos} == {"Optica Luz"}
    assert get_business(db, lid)["crm_status"] == "demo_agendada"
    # Se agenda una vez, no una vez por viernes.
    assert _filas(db, "SELECT COUNT(*) FROM activity_log WHERE action='meeting_scheduled'") == [(1,)]
    assert _filas(db, "SELECT COUNT(*) FROM meetings") == [(1,)]


def test_borrar_solo_una_de_cliente_no_saca_al_lead_de_demo_agendada(app, cliente):
    db, lid, mid = _serie_de_cliente(app, cliente)

    r = cliente.delete(f"/api/calendar/meetings/{mid}?alcance=esta&ocurrencia=2026-09-11",
                       headers=_AUTH)

    assert r.get_json()["ok"] is True
    assert get_meeting(db, mid) is not None
    assert get_business(db, lid)["crm_status"] == "demo_agendada"
    assert len(_cuando(_eventos(cliente, "2026-09-01", "2026-09-30"),
                       "Seguimiento Optica Luz")) == 3


def test_borrar_toda_la_serie_de_cliente_hace_lo_de_siempre(app, cliente):
    db, lid, mid = _serie_de_cliente(app, cliente)
    cliente.delete(f"/api/calendar/meetings/{mid}?alcance=todas", headers=_AUTH)
    assert get_meeting(db, mid) is None
    assert get_business(db, lid)["crm_status"] == "contactado"


def test_mover_una_de_cliente_y_las_siguientes(app, cliente):
    db, lid, mid = _serie_de_cliente(app, cliente)

    r = cliente.patch(f"/api/calendar/meetings/{mid}", headers=_AUTH,
                      json={"date": "2026-09-18", "time": "17:00", "ocurrencia": "2026-09-18",
                            "alcance": "siguientes", "title": "Seguimiento Optica Luz"})

    assert r.get_json()["ok"] is True
    assert _cuando(_eventos(cliente, "2026-09-01", "2026-09-30"), "Seguimiento Optica Luz") == [
        ("2026-09-04", "19:00"), ("2026-09-11", "19:00"),
        ("2026-09-18", "17:00"), ("2026-09-25", "17:00")]
    assert _filas(db, "SELECT client_id FROM meetings ORDER BY id") == [(lid,), (lid,)]


# ── el sync de Google ────────────────────────────────────────────────────────

def test_si_la_reunion_tambien_esta_en_google_no_se_importa_como_lead(app, cliente, google):
    """Si alguien crea "Marketing semanal" tambien en Google para que les llegue
    la invitacion, el sync la traia como reunion con un lead nuevo, inventado a
    partir del primer invitado, una vez por viernes. Lo demas se sigue importando."""
    db = app.config["DB_PATH"]
    _crear(cliente, repeticion=VIERNES, invitados="ana@agencia.com")
    google.events.return_value.list.return_value.execute.return_value = {"items": [
        {"id": "gcal_20260918T220000Z", "summary": "Marketing semanal",
         "start": {"dateTime": "2026-09-18T19:00:00-03:00"},
         "end": {"dateTime": "2026-09-18T20:00:00-03:00"},
         "attendees": [{"email": "yo@scalerics.com", "organizer": True},
                       {"email": "ana@agencia.com"}]},
        {"id": "gcal-demo", "summary": "Demo Vidrieria Norte",
         "start": {"dateTime": "2026-09-18T15:00:00-03:00"},
         "end": {"dateTime": "2026-09-18T16:00:00-03:00"},
         "attendees": [{"email": "yo@scalerics.com", "organizer": True},
                       {"email": "hola@vidrierianorte.uy", "displayName": "Vidrieria Norte"}]},
    ]}

    eventos = _eventos(cliente, "2026-09-01", "2026-09-30")

    assert [n for (n,) in _filas(db, "SELECT name FROM businesses")] == ["Vidrieria Norte"]
    assert _cuando(eventos).count(("2026-09-18", "19:00")) == 1
    assert ("2026-09-18", "15:00") in _cuando(eventos, "Demo Vidrieria Norte")


# ── la pagina ────────────────────────────────────────────────────────────────

def test_la_pagina_principal_se_renderiza(tmp_path, monkeypatch):
    """Jinja: un `{#` en el CSS o el JS nuevo daria 500 para todos (paso en v204)."""
    monkeypatch.setenv("SECRET_KEY", "test")
    monkeypatch.setenv("ADMIN_EMAIL", "jefe@test.com")
    db = str(tmp_path / "render.db")
    init_db(db)
    app = dashboard.create_app(db)
    uid = create_user(db, name="Jefe", email="jefe@test.com", phone="099",
                      password_hash=generate_password_hash("x" * 10))
    cli = app.test_client()
    with cli.session_transaction() as s:
        s["logged_in"] = True
        s["user_id"] = uid
        s["user_name"] = "Jefe"

    r = cli.get("/")

    assert r.status_code == 200, r.status_code
    for pedazo in ('id="ev-tipo-asunto"', 'id="ev-rep-freq"', 'id="ev-invitados"',
                   'id="alcance-modal"', 'id="reprog-invitados"', "+ Nueva reunión"):
        assert pedazo.encode() in r.data, pedazo
