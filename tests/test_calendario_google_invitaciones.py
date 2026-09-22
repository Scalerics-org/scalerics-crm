"""Las reuniones del CRM vuelven a Google Calendar, con invitaciones (Juan, 15/9).

"Si más adelante querés que el CRM vuelva a crear las reuniones en Google y
mande las invitaciones solo... Hacelo".

Ningun test habla con Google: `GoogleFalso` imita `service.events()`, anota
cada llamada y contesta como Google. Lo que se fija:

  crear       el payload exacto (summary, start/end con timeZone, attendees,
              RRULE, sendUpdates="all") y el google_event_id guardado
  editar      "todas" = patch del recurrente; "solo esta" = instances + patch
              de la instancia; "esta y las siguientes" = UNTIL + serie nueva
  borrar      delete del evento, delete de la instancia, o UNTIL; Google primero
  fallas      la reunion queda en el CRM marcada "No sincronizada" y
              "Reintentar" la sube sin crear un evento duplicado
  permisos    sin permiso de escritura degrada con aviso
  import      no duplica ni inventa leads con los eventos del CRM ni sus instancias
  Calendly    intacto
"""

import datetime
import json
import sqlite3
from types import SimpleNamespace

import pytest
from werkzeug.security import generate_password_hash

import dashboard
from database import (create_meeting, create_user, get_meeting, get_reunion_asunto, init_db,
                      insert_business, update_business)
from services import gcal_eventos as gce

_AUTH = {"x-admin-token": "token-de-test"}
VIERNES = {"freq": "semanal", "dias": [4], "fin": "nunca"}
MVD = "America/Montevideo"
INVITADOS = "ana@agencia.com, pablo@estudio.uy, sofi@marca.com"


def _http_error(status, mensaje="falla", reason="backendError"):
    import httplib2
    from googleapiclient.errors import HttpError
    cuerpo = {"error": {"code": status, "message": mensaje, "errors": [{"reason": reason}]}}
    return HttpError(httplib2.Response({"status": status}), json.dumps(cuerpo).encode())


class _Pedido:
    def __init__(self, google, metodo, kw):
        self.google, self.metodo, self.kw = google, metodo, kw

    def execute(self):
        return self.google.responder(self.metodo, self.kw)


class GoogleFalso:
    """Imita service.events(): anota cada llamada y contesta como Google.

    `falla[metodo]` es una lista de excepciones, una por llamada (None = anda).
    """

    def __init__(self):
        self.llamadas = []
        self.items = []
        self.falla = {}

    def events(self):
        return self

    def insert(self, **kw):
        return _Pedido(self, "insert", kw)

    def patch(self, **kw):
        return _Pedido(self, "patch", kw)

    def delete(self, **kw):
        return _Pedido(self, "delete", kw)

    def instances(self, **kw):
        return _Pedido(self, "instances", kw)

    def list(self, **kw):
        return _Pedido(self, "list", kw)

    def responder(self, metodo, kw):
        self.llamadas.append((metodo, kw))
        pendientes = self.falla.get(metodo) or []
        error = pendientes.pop(0) if pendientes else None
        if error:
            raise error
        if metodo == "insert":
            body = kw["body"]
            meet = "https://meet.google.com/abc-defg-hij" if "conferenceData" in body else ""
            return {"id": body["id"], "hangoutLink": meet}
        if metodo == "patch" and "conferenceData" in (kw.get("body") or {}):
            return {"id": kw["eventId"], "hangoutLink": "https://meet.google.com/abc-defg-hij"}
        if metodo == "instances":
            return {"items": [{"id": kw["eventId"] + "_instancia"}]}
        if metodo == "list":
            return {"items": self.items}
        return {}

    def de(self, metodo):
        return [kw for m, kw in self.llamadas if m == metodo]

    def escrituras(self):
        return [(m, kw) for m, kw in self.llamadas if m in ("insert", "patch", "delete")]


@pytest.fixture
def google(monkeypatch, _sin_credenciales_reales):
    """Credenciales de mentira (para que la creacion este prendida) y un Google
    falso en lugar del servicio. Pide el fixture de conftest para correr despues."""
    import routes.calendar as cal
    for clave in gce._CREDENCIALES:
        monkeypatch.setenv(clave, "valor-de-mentira")
    monkeypatch.delenv("GCAL_CREAR_EVENTOS", raising=False)
    falso = GoogleFalso()
    monkeypatch.setattr(cal, "_get_calendar_service", lambda: (falso, None))
    return falso


@pytest.fixture
def app(tmp_path, monkeypatch, google):
    monkeypatch.setenv("ADMIN_TOKEN", "token-de-test")
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    app = dashboard.create_app(ruta)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def cliente(app):
    return app.test_client()


def _crear(cliente, **campos):
    cuerpo = {"tipo": "asunto", "title": "Marketing semanal", "date": "2026-09-18",
              "time": "19:00", "duration_min": 60, "invitados": INVITADOS}
    cuerpo.update(campos)
    return cliente.post("/api/calendar/events", json=cuerpo, headers=_AUTH).get_json()


def _eventos(cliente, desde="2026-09-01", hasta="2026-09-30"):
    return cliente.get(f"/api/calendar/events?start={desde}&end={hasta}",
                       headers=_AUTH).get_json()["events"]


def _filas(db, sql, *params):
    con = sqlite3.connect(db)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def _serie_en_google(cliente, google, **campos):
    d = _crear(cliente, repeticion=VIERNES, **campos)
    assert d["google"]["estado"] == "ok", d
    gid = google.de("insert")[0]["body"]["id"]
    google.llamadas.clear()
    return d["asunto_id"], gid


# ── crear ────────────────────────────────────────────────────────────────────

def test_crear_otro_asunto_crea_el_evento_e_invita(app, cliente, google):
    db = app.config["DB_PATH"]

    d = _crear(cliente, description="Pauta de octubre")

    assert d["ok"] is True and d["google"] == {"estado": "ok", "error": ""}, d
    [pedido] = google.de("insert")
    assert pedido["calendarId"] == "primary"
    assert pedido["sendUpdates"] == "all"
    assert pedido["conferenceDataVersion"] == 1
    body = pedido["body"]
    assert body["summary"] == "Marketing semanal"
    assert body["description"] == "Pauta de octubre"
    assert body["start"] == {"dateTime": "2026-09-18T19:00:00", "timeZone": MVD}
    assert body["end"] == {"dateTime": "2026-09-18T20:00:00", "timeZone": MVD}
    assert body["attendees"] == [{"email": "ana@agencia.com"}, {"email": "pablo@estudio.uy"},
                                 {"email": "sofi@marca.com"}]
    assert "recurrence" not in body
    assert body["conferenceData"]["createRequest"]["conferenceSolutionKey"] == {"type": "hangoutsMeet"}
    fila = get_reunion_asunto(db, d["asunto_id"])
    assert fila["google_event_id"] == body["id"] == d["event_id"]
    assert fila["google_sync"] == "ok" and fila["google_error"] is None
    assert fila["google_meet"] == d["meet_url"] == "https://meet.google.com/abc-defg-hij"
    [ev] = _eventos(cliente)
    assert ev["meeting_url"] == ev["google"]["meet"] == "https://meet.google.com/abc-defg-hij"


def test_todo_evento_del_crm_lleva_meet_aunque_la_reunion_traiga_otro_link(app, cliente, google):
    """Pedido de Juan: el link de Meet va siempre en la invitacion. Si la
    reunion ya tenia un link propio, ese se sigue viendo en el CRM."""
    d = _crear(cliente, meet_link="https://zoom.us/j/123", repeticion=VIERNES)

    [pedido] = google.de("insert")
    assert pedido["conferenceDataVersion"] == 1
    assert pedido["body"]["conferenceData"]["createRequest"]["conferenceSolutionKey"] == {
        "type": "hangoutsMeet"}
    fila = get_reunion_asunto(app.config["DB_PATH"], d["asunto_id"])
    assert fila["google_meet"] == "https://meet.google.com/abc-defg-hij"
    assert fila["meet_link"] == "https://zoom.us/j/123"


def test_presencial_no_lleva_meet(app, cliente, google):
    """Pedido de Juan (22/9): una reunion presencial no tiene link para
    clickear, ni el de Meet ni uno que se haya escrito a mano."""
    d = _crear(cliente, presencial=True, meet_link="https://zoom.us/j/123")

    [pedido] = google.de("insert")
    assert "conferenceData" not in pedido["body"]
    fila = get_reunion_asunto(app.config["DB_PATH"], d["asunto_id"])
    assert fila["presencial"] == 1
    assert fila["meet_link"] is None or fila["meet_link"] == ""
    assert fila["google_meet"] in (None, "")
    assert d["meet_url"] == ""


def test_con_cliente_el_mail_del_cliente_va_como_invitado(app, cliente, google):
    db = app.config["DB_PATH"]
    lid = insert_business(db, {"name": "Optica Luz"})
    update_business(db, lid, email="hola@opticaluz.uy")

    d = _crear(cliente, tipo="cliente", client_id=lid, title="Demo Optica Luz",
               invitados="socio@opticaluz.uy, HOLA@opticaluz.uy")

    body = google.de("insert")[0]["body"]
    assert body["attendees"] == [{"email": "hola@opticaluz.uy"}, {"email": "socio@opticaluz.uy"}]
    assert body["summary"] == "Demo Optica Luz"
    assert get_meeting(db, d["meeting_id"])["google_event_id"] == body["id"]


def test_un_viernes_19_que_se_repite_es_un_solo_evento_con_rrule(cliente, google, monkeypatch):
    """Un evento recurrente, una invitacion. El servidor corre en UTC: la hora
    viaja como hora de pared con su zona, sin corrimiento."""
    import time
    monkeypatch.setenv("TZ", "UTC")
    if hasattr(time, "tzset"):
        time.tzset()

    _crear(cliente, repeticion=VIERNES)
    _crear(cliente, title="Cierre", date="2026-09-25", time="23:30", repeticion=VIERNES)

    primero, segundo = google.de("insert")
    assert primero["body"]["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=FR"]
    assert primero["body"]["start"] == {"dateTime": "2026-09-18T19:00:00", "timeZone": MVD}
    assert primero["sendUpdates"] == "all"
    assert primero["conferenceDataVersion"] == 1 and "conferenceData" in primero["body"]
    assert segundo["body"]["start"] == {"dateTime": "2026-09-25T23:30:00", "timeZone": MVD}
    assert segundo["body"]["end"] == {"dateTime": "2026-09-26T00:30:00", "timeZone": MVD}


@pytest.mark.parametrize("regla,esperada", [
    ({"freq": "semanal", "dias": [4], "fin": "nunca"}, "RRULE:FREQ=WEEKLY;BYDAY=FR"),
    ({"freq": "quincenal", "dias": [0, 2], "fin": "nunca"}, "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE"),
    ({"freq": "diaria", "fin": "veces", "veces": 10}, "RRULE:FREQ=DAILY;COUNT=10"),
    ({"freq": "mensual", "fin": "nunca"}, "RRULE:FREQ=MONTHLY;BYMONTHDAY=18"),
    # Fin el 31/12 en Montevideo: el ultimo segundo de ese dia es 02:59:59 UTC del 1/1.
    ({"freq": "semanal", "dias": [4], "fin": "fecha", "hasta": "2026-12-31"},
     "RRULE:FREQ=WEEKLY;BYDAY=FR;UNTIL=20270101T025959Z"),
])
def test_la_rrule(regla, esperada):
    assert gce.rrule(regla, datetime.datetime(2026, 9, 18, 19, 0)) == esperada


def test_el_fin_por_cantidad_viaja_en_el_evento(cliente, google):
    _crear(cliente, repeticion={"freq": "semanal", "dias": [4], "fin": "veces", "veces": 4})
    assert google.de("insert")[0]["body"]["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=FR;COUNT=4"]


# ── fallas, permisos e interruptor ───────────────────────────────────────────

def test_si_google_falla_queda_marcada_y_reintentar_la_sube(app, cliente, google):
    db = app.config["DB_PATH"]
    google.falla["insert"] = [_http_error(500)]

    d = _crear(cliente, repeticion=VIERNES)

    assert d["ok"] is True
    assert d["google"] == {"estado": "error", "error": "Google respondió con error 500"}
    aid = d["asunto_id"]
    assert get_reunion_asunto(db, aid)["google_sync"] == "error"
    eventos = _eventos(cliente)
    assert len(eventos) == 2   # la reunion esta en el CRM igual: 18 y 25
    assert {e["google"]["estado"] for e in eventos} == {"error"}

    primer_id = google.de("insert")[0]["body"]["id"]
    r = cliente.post(f"/api/calendar/asuntos/{aid}/google", headers=_AUTH)

    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert [p["body"]["id"] for p in google.de("insert")] == [primer_id, primer_id]
    assert get_reunion_asunto(db, aid)["google_sync"] == "ok"
    assert {e["google"]["estado"] for e in _eventos(cliente)} == {"ok"}


def test_reintentar_cuando_el_evento_ya_se_habia_creado_no_lo_duplica(cliente, google):
    """Google lo creo pero la respuesta se perdio: el reintento usa el mismo id,
    Google dice 409 y se actualiza en vez de mandar otra invitacion."""
    google.falla["insert"] = [_http_error(503), _http_error(409, "already exists", "duplicate")]
    d = _crear(cliente)

    r = cliente.post(f"/api/calendar/asuntos/{d['asunto_id']}/google", headers=_AUTH)

    assert r.get_json()["ok"] is True
    primer, segundo = google.de("insert")
    assert primer["body"]["id"] == segundo["body"]["id"]
    [parche] = google.de("patch")
    assert parche["eventId"] == primer["body"]["id"] and parche["sendUpdates"] == "all"
    # El evento que quedo de la vez anterior se completa con su Meet.
    assert parche["conferenceDataVersion"] == 1 and "conferenceData" in parche["body"]


def test_reintentar_que_vuelve_a_fallar_sigue_marcada(app, cliente, google):
    google.falla["insert"] = [_http_error(500), _http_error(502)]
    d = _crear(cliente)

    r = cliente.post(f"/api/calendar/asuntos/{d['asunto_id']}/google", headers=_AUTH)

    assert r.status_code == 502 and r.get_json()["ok"] is False
    assert get_reunion_asunto(app.config["DB_PATH"], d["asunto_id"])["google_sync"] == "error"


def test_sin_permiso_de_escritura_degrada_con_aviso(app, cliente, google):
    google.falla["insert"] = [_http_error(403, "Request had insufficient authentication scopes.",
                                          "insufficientPermissions")]

    d = _crear(cliente)

    assert d["ok"] is True
    assert d["google"] == {"estado": "error", "error": gce.SIN_PERMISO}
    fila = get_reunion_asunto(app.config["DB_PATH"], d["asunto_id"])
    assert fila["google_sync"] == "error" and fila["google_error"] == gce.SIN_PERMISO


def test_si_ya_se_sabe_que_el_token_es_de_solo_lectura_ni_se_intenta(cliente, google):
    google._http = SimpleNamespace(credentials=SimpleNamespace(
        granted_scopes=["https://www.googleapis.com/auth/calendar.readonly"]))

    d = _crear(cliente)

    assert google.de("insert") == []
    assert d["google"]["error"] == gce.SIN_PERMISO


def test_un_token_sin_el_scope_se_reconoce():
    from google.auth.exceptions import RefreshError
    assert gce.mensaje_error(RefreshError("invalid_scope: Bad Request")) == gce.SIN_PERMISO
    assert gce.mensaje_error(RuntimeError("timeout")) == "no hubo respuesta de Google"


@pytest.mark.parametrize("valor", ["off", "OFF", "0", "false", "no"])
def test_con_el_interruptor_apagado_no_se_crea_en_google(app, cliente, google, monkeypatch, valor):
    monkeypatch.setenv("GCAL_CREAR_EVENTOS", valor)

    d = _crear(cliente, repeticion=VIERNES)

    assert d["ok"] is True and d["google"] == {"estado": "", "error": ""}
    assert google.escrituras() == []
    fila = get_reunion_asunto(app.config["DB_PATH"], d["asunto_id"])
    assert fila["google_event_id"] is None and fila["google_sync"] is None


def test_sin_credenciales_no_se_intenta(cliente, google, monkeypatch):
    monkeypatch.delenv("GCAL_REFRESH_TOKEN")
    assert _crear(cliente)["google"]["estado"] == ""
    assert google.escrituras() == []


# ── editar ───────────────────────────────────────────────────────────────────

def test_editar_todas_cambia_el_evento_recurrente(cliente, google):
    aid, gid = _serie_en_google(cliente, google)

    r = cliente.patch(f"/api/calendar/asuntos/{aid}", headers=_AUTH, json={
        "date": "2026-10-02", "time": "18:30", "title": "Marketing", "duration_min": 45,
        "ocurrencia": "2026-10-02", "alcance": "todas"})

    assert r.get_json()["google"]["estado"] == "ok"
    [(metodo, kw)] = google.escrituras()
    assert metodo == "patch" and kw["eventId"] == gid and kw["sendUpdates"] == "all"
    body = kw["body"]
    assert body["summary"] == "Marketing"
    assert body["start"] == {"dateTime": "2026-09-18T18:30:00", "timeZone": MVD}
    assert body["end"] == {"dateTime": "2026-09-18T19:15:00", "timeZone": MVD}
    assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=FR"]
    assert len(body["attendees"]) == 3


def test_editar_solo_esta_es_una_excepcion_de_la_instancia(cliente, google):
    aid, gid = _serie_en_google(cliente, google)

    cliente.patch(f"/api/calendar/asuntos/{aid}", headers=_AUTH, json={
        "date": "2026-09-24", "time": "18:00", "title": "Marketing semanal", "duration_min": 60,
        "ocurrencia": "2026-09-25", "alcance": "esta"})

    [instancia] = google.de("instances")
    assert instancia["eventId"] == gid
    assert instancia["originalStart"] == "2026-09-25T19:00:00-03:00"
    [(metodo, kw)] = google.escrituras()
    assert metodo == "patch" and kw["eventId"] == gid + "_instancia" and kw["sendUpdates"] == "all"
    assert kw["body"]["start"] == {"dateTime": "2026-09-24T18:00:00", "timeZone": MVD}
    assert kw["body"]["end"] == {"dateTime": "2026-09-24T19:00:00", "timeZone": MVD}


def test_editar_esta_y_las_siguientes_corta_la_serie_y_crea_otra(app, cliente, google):
    db = app.config["DB_PATH"]
    aid, gid = _serie_en_google(cliente, google)

    cliente.patch(f"/api/calendar/asuntos/{aid}", headers=_AUTH, json={
        "date": "2026-10-02", "time": "20:00", "title": "Marketing semanal",
        "ocurrencia": "2026-10-02", "alcance": "siguientes"})

    (m1, corte), (m2, nuevo) = google.escrituras()
    assert m1 == "patch" and corte["eventId"] == gid and corte["sendUpdates"] == "all"
    # Termina el jueves 1/10 a las 23:59:59 de Montevideo.
    assert corte["body"] == {"recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=FR;UNTIL=20261002T025959Z"]}
    assert m2 == "insert" and nuevo["sendUpdates"] == "all"
    body = nuevo["body"]
    assert body["id"] != gid
    assert body["start"] == {"dateTime": "2026-10-02T20:00:00", "timeZone": MVD}
    assert nuevo["conferenceDataVersion"] == 1 and "conferenceData" in body   # su propio Meet
    assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=FR"]
    assert [a["email"] for a in body["attendees"]] == ["ana@agencia.com", "pablo@estudio.uy",
                                                       "sofi@marca.com"]
    assert _filas(db, "SELECT google_event_id, google_sync FROM reuniones_asunto ORDER BY id") == [
        (gid, "ok"), (body["id"], "ok")]


def test_mover_una_reunion_suelta_con_cliente(app, cliente, google):
    lid = insert_business(app.config["DB_PATH"], {"name": "Optica Luz"})
    d = _crear(cliente, tipo="cliente", client_id=lid, title="Demo", invitados="")
    gid = google.de("insert")[0]["body"]["id"]
    google.llamadas.clear()

    r = cliente.patch(f"/api/calendar/meetings/{d['meeting_id']}", headers=_AUTH,
                      json={"date": "2026-09-19", "time": "10:00"})

    assert r.get_json()["google"]["estado"] == "ok"
    [(metodo, kw)] = google.escrituras()
    assert metodo == "patch" and kw["eventId"] == gid and kw["sendUpdates"] == "all"
    assert kw["body"]["start"] == {"dateTime": "2026-09-19T10:00:00", "timeZone": MVD}
    assert "recurrence" not in kw["body"]


def test_si_google_falla_al_editar_el_cambio_queda_y_se_puede_reintentar(app, cliente, google):
    db = app.config["DB_PATH"]
    aid, gid = _serie_en_google(cliente, google)
    google.falla["patch"] = [_http_error(500)]

    r = cliente.patch(f"/api/calendar/asuntos/{aid}", headers=_AUTH, json={
        "date": "2026-09-18", "time": "18:00", "ocurrencia": "2026-09-18", "alcance": "todas"})

    d = r.get_json()
    assert d["ok"] is True and d["google"]["estado"] == "error"
    fila = get_reunion_asunto(db, aid)
    assert fila["start_at"] == "2026-09-18T18:00:00" and fila["google_sync"] == "error"

    r = cliente.post(f"/api/calendar/asuntos/{aid}/google", headers=_AUTH)
    assert r.get_json()["ok"] is True
    assert get_reunion_asunto(db, aid)["google_sync"] == "ok"


# ── borrar ───────────────────────────────────────────────────────────────────

def test_borrar_todas_borra_el_evento(app, cliente, google):
    aid, gid = _serie_en_google(cliente, google)

    r = cliente.delete(f"/api/calendar/asuntos/{aid}?alcance=todas", headers=_AUTH)

    assert r.get_json()["ok"] is True
    [(metodo, kw)] = google.escrituras()
    assert metodo == "delete" and kw["eventId"] == gid and kw["sendUpdates"] == "all"
    assert get_reunion_asunto(app.config["DB_PATH"], aid) is None


def test_borrar_solo_esta_cancela_la_instancia(app, cliente, google):
    aid, gid = _serie_en_google(cliente, google)

    cliente.delete(f"/api/calendar/asuntos/{aid}?alcance=esta&ocurrencia=2026-09-25", headers=_AUTH)

    [instancia] = google.de("instances")
    assert instancia["originalStart"] == "2026-09-25T19:00:00-03:00"
    [(metodo, kw)] = google.escrituras()
    assert metodo == "delete" and kw["eventId"] == gid + "_instancia" and kw["sendUpdates"] == "all"
    assert json.loads(get_reunion_asunto(app.config["DB_PATH"], aid)["excepciones"]) == {
        "2026-09-25": None}


def test_borrar_esta_y_las_siguientes_le_pone_fin_a_la_serie(cliente, google):
    aid, gid = _serie_en_google(cliente, google)

    cliente.delete(f"/api/calendar/asuntos/{aid}?alcance=siguientes&ocurrencia=2026-10-02",
                   headers=_AUTH)

    [(metodo, kw)] = google.escrituras()
    assert metodo == "patch" and kw["eventId"] == gid and kw["sendUpdates"] == "all"
    assert kw["body"] == {"recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=FR;UNTIL=20261002T025959Z"]}
    assert _eventos(cliente, "2026-10-01", "2026-10-31") == []


def test_si_google_no_responde_no_se_borra_nada(app, cliente, google):
    db = app.config["DB_PATH"]
    aid, gid = _serie_en_google(cliente, google)
    antes = get_reunion_asunto(db, aid)
    google.falla["delete"] = [_http_error(500)]
    google.falla["patch"] = [_http_error(503)]

    r1 = cliente.delete(f"/api/calendar/asuntos/{aid}?alcance=todas", headers=_AUTH)
    r2 = cliente.delete(f"/api/calendar/asuntos/{aid}?alcance=siguientes&ocurrencia=2026-10-02",
                        headers=_AUTH)

    for r in (r1, r2):
        assert r.status_code == 502 and r.get_json()["ok"] is False
        assert "No se borró" in r.get_json()["error"]
    assert get_reunion_asunto(db, aid) == antes


def test_si_google_ya_no_lo_tenia_se_borra_igual(app, cliente, google):
    aid, gid = _serie_en_google(cliente, google)
    google.falla["delete"] = [_http_error(410)]

    r = cliente.delete(f"/api/calendar/asuntos/{aid}", headers=_AUTH)

    assert r.get_json()["ok"] is True
    assert get_reunion_asunto(app.config["DB_PATH"], aid) is None


def test_borrar_una_reunion_con_cliente(app, cliente, google):
    db = app.config["DB_PATH"]
    lid = insert_business(db, {"name": "Optica Luz"})
    d = _crear(cliente, tipo="cliente", client_id=lid, title="Demo")
    gid = google.de("insert")[0]["body"]["id"]
    google.llamadas.clear()

    cliente.delete(f"/api/calendar/meetings/{d['meeting_id']}", headers=_AUTH)

    assert google.escrituras() == [("delete", {"calendarId": "primary", "eventId": gid,
                                               "sendUpdates": "all"})]
    assert get_meeting(db, d["meeting_id"]) is None


# ── el import de Google ──────────────────────────────────────────────────────

def _evento(eid, summary, dia, hora, invitado, **extra):
    ev = {"id": eid, "summary": summary,
          "start": {"dateTime": f"{dia}T{hora}:00-03:00"},
          "end": {"dateTime": f"{dia}T{hora}:59-03:00"},
          "attendees": [{"email": "yo@scalerics.com", "organizer": True}, {"email": invitado}]}
    ev.update(extra)
    return ev


def test_el_import_no_trae_los_eventos_del_crm_ni_sus_instancias(app, cliente, google):
    """Aunque en Google les cambien el titulo: se reconocen por id."""
    db = app.config["DB_PATH"]
    aid, gid = _serie_en_google(cliente, google)
    google.items = [
        _evento(gid, "Marketing (editado en Google)", "2026-09-18", "19:00", "ana@agencia.com"),
        _evento(f"{gid}_20260925T220000Z", "Otro titulo", "2026-09-25", "19:00",
                "pablo@estudio.uy", recurringEventId=gid),
        _evento("evt-demo", "Demo Vidrieria Norte", "2026-09-18", "15:00", "hola@vidrierianorte.uy"),
    ]

    eventos = _eventos(cliente)

    assert [n for (n,) in _filas(db, "SELECT name FROM businesses")] == ["hola@vidrierianorte.uy"]
    assert _filas(db, "SELECT calendar_event_id FROM meetings") == [("evt-demo",)]
    assert sum(1 for e in eventos if e["tipo"] == "asunto") == 2


def test_el_import_no_trae_las_instancias_de_una_serie_con_cliente(app, cliente, google):
    """El control viejo por hora solo mira filas sueltas de `meetings`: la
    instancia del 25 no coincide con ninguna y se importaba como lead nuevo."""
    db = app.config["DB_PATH"]
    lid = insert_business(db, {"name": "Optica Luz"})
    d = _crear(cliente, tipo="cliente", client_id=lid, title="Seguimiento", invitados="",
               repeticion=VIERNES)
    gid = get_meeting(db, d["meeting_id"])["google_event_id"]
    google.items = [_evento(f"{gid}_20260925T220000Z", "Seguimiento (Google)", "2026-09-25",
                            "19:00", "socio@opticaluz.uy", recurringEventId=gid)]

    _eventos(cliente)

    assert [n for (n,) in _filas(db, "SELECT name FROM businesses")] == ["Optica Luz"]
    assert _filas(db, "SELECT COUNT(*) FROM meetings") == [(1,)]


def test_es_del_crm():
    ids = {"scalericsa1abc"}
    assert gce.es_del_crm({"id": "scalericsa1abc"}, ids)
    assert gce.es_del_crm({"id": "scalericsa1abc_20260925T220000Z"}, ids)
    assert gce.es_del_crm({"id": "otro", "recurringEventId": "scalericsa1abc"}, ids)
    assert not gce.es_del_crm({"id": "evt-demo"}, ids)
    assert not gce.es_del_crm({"id": "evt-demo"}, set())


# ── Calendly ─────────────────────────────────────────────────────────────────

def test_calendly_no_se_toca(app, cliente, google):
    db = app.config["DB_PATH"]
    lid = insert_business(db, {"name": "Ferreteria El Sol"})
    mid = create_meeting(db, lid, title="Consultoria", start_at="2026-09-18T15:00:00",
                         end_at="2026-09-18T15:45:00", status="scheduled",
                         calendar_event_id="https://api.calendly.com/scheduled_events/AAA")

    r1 = cliente.patch(f"/api/calendar/meetings/{mid}", json={"date": "2026-09-19", "time": "10:00"},
                       headers=_AUTH)
    r2 = cliente.post(f"/api/calendar/meetings/{mid}/google", headers=_AUTH)

    assert r1.status_code == 409 and r2.status_code == 400
    assert google.escrituras() == []
    [ev] = _eventos(cliente)
    assert ev["origen"] == "calendly" and ev["google"]["estado"] == ""
    assert get_meeting(db, mid)["google_event_id"] is None


def _llamadas_a_google(google):
    """Todo lo que no es el listado del import (que pasa siempre al abrir el calendario)."""
    return [m for m, _ in google.llamadas if m != "list"]


def _calendly_por_sync(db):
    """Una reserva de Calendly cargada como la carga el sync de Calendly."""
    from services.calendly_gcal import sync_parsed
    sync_parsed(db, [{
        "event_uri": "https://api.calendly.com/scheduled_events/BBB", "name": "Laura",
        "email": "laura@ferreteria.uy", "company": "Ferreteria Norte", "service": "",
        "phone": "", "start_at": "2026-09-18T15:00:00", "end_at": "2026-09-18T15:30:00",
        "meet_link": "", "canceled": False}], now="2026-09-01T00:00:00")
    return _filas(db, "SELECT id FROM meetings")[0][0]


def test_una_reunion_de_calendly_hace_cero_llamadas_a_google(app, cliente, google):
    """Pedido de Juan: Calendly ya crea el evento y manda la invitacion. Ni
    mover, ni enviar, ni borrar desde el CRM le tocan Google."""
    db = app.config["DB_PATH"]
    mid = _calendly_por_sync(db)
    assert get_meeting(db, mid)["origen"] == "calendly"

    [ev] = _eventos(cliente)
    r_mover = cliente.patch(f"/api/calendar/meetings/{mid}", headers=_AUTH,
                            json={"date": "2026-09-19", "time": "10:00"})
    r_enviar = cliente.post(f"/api/calendar/meetings/{mid}/google", headers=_AUTH)
    r_borrar = cliente.delete(f"/api/calendar/meetings/{mid}", headers=_AUTH)

    assert ev["origen"] == "calendly" and ev["google"]["puede_enviar"] is False
    assert r_mover.status_code == 409 and r_enviar.status_code == 400
    assert r_borrar.get_json()["ok"] is True and get_meeting(db, mid) is None
    assert _llamadas_a_google(google) == []


def test_el_webhook_de_calendly_la_guarda_como_de_calendly_sin_tocar_google(tmp_path, monkeypatch, google):
    from flask import Flask
    from routes.calendly import calendly_bp
    db = str(tmp_path / "webhook.db")
    init_db(db)
    monkeypatch.setenv("DB_PATH", db)
    monkeypatch.delenv("CALENDLY_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("RECALL_API_KEY", raising=False)
    app = Flask(__name__)
    app.register_blueprint(calendly_bp)
    app.config["DB_PATH"] = db
    payload = {"event": "invitee.created", "payload": {
        "invitee": {"name": "Laura", "email": "laura@ferreteria.uy",
                    "questions_and_answers": [{"question": "Empresa", "answer": "Ferreteria Norte"}]},
        "event": {"uri": "https://api.calendly.com/scheduled_events/EVT9",
                  "start_time": "2026-09-18T18:00:00.000000Z",
                  "end_time": "2026-09-18T18:30:00.000000Z",
                  "location": {"join_url": "https://meet.google.com/xyz-calendly"}}}}

    r = app.test_client().post("/api/calendly/webhook", json=payload)

    assert r.status_code == 200, r.data
    assert _filas(db, "SELECT origen, calendar_event_id FROM meetings") == [
        ("calendly", "https://api.calendly.com/scheduled_events/EVT9")]
    assert google.llamadas == []


def test_un_evento_de_calendly_que_entra_por_el_import_de_google_tampoco_se_toca(app, cliente, google):
    db = app.config["DB_PATH"]
    google.items = [_evento("gcal-calendly-1", "Laura y Scalerics", "2026-09-18", "15:00",
                            "laura@ferreteria.uy",
                            description="Consultoria\nhttps://calendly.com/events/1234/google_meet")]
    [ev] = _eventos(cliente)
    google.items = []
    mid = int(ev["id"])

    assert ev["origen"] == "calendly" and get_meeting(db, mid)["origen"] == "calendly"
    r_mover = cliente.patch(f"/api/calendar/meetings/{mid}", headers=_AUTH,
                            json={"date": "2026-09-19", "time": "10:00"})
    r_invitar = cliente.post("/api/calendar/events/gcal-calendly-1/attendees", headers=_AUTH,
                             json={"email": "otro@ferreteria.uy"})
    r_borrar = cliente.delete(f"/api/calendar/meetings/{mid}", headers=_AUTH)

    assert r_mover.status_code == 409 and r_invitar.status_code == 409
    assert r_borrar.get_json()["ok"] is True
    assert _llamadas_a_google(google) == []


def test_una_importada_de_google_que_no_es_de_calendly_se_mueve_como_antes(app, cliente, google):
    db = app.config["DB_PATH"]
    google.items = [_evento("gcal-a-mano", "Llamada con proveedor", "2026-09-18", "11:00",
                            "proveedor@imprenta.uy")]
    [ev] = _eventos(cliente)
    google.items = []

    cliente.patch(f"/api/calendar/meetings/{ev['id']}", headers=_AUTH,
                  json={"date": "2026-09-18", "time": "12:00"})

    assert ev["origen"] == "google" and get_meeting(db, int(ev["id"]))["origen"] == "google"
    assert [kw["eventId"] for kw in google.de("patch")] == ["gcal-a-mano"]
    assert google.de("insert") == []


def test_una_reunion_creada_a_mano_hace_una_sola_llamada(app, cliente, google):
    db = app.config["DB_PATH"]
    lid = insert_business(db, {"name": "Optica Luz"})

    d = _crear(cliente, tipo="cliente", client_id=lid, title="Demo", invitados="")

    assert _llamadas_a_google(google) == ["insert"]
    assert get_meeting(db, d["meeting_id"])["origen"] == "crm"


# ── reuniones de antes de publicar: "Enviar a Google Calendar" ───────────────

def test_una_reunion_de_antes_se_envia_una_sola_vez_como_una_serie(app, cliente, google, monkeypatch):
    """Como "Marketing Semanal": creada antes de que el CRM mandara a Google.
    Abrir el calendario no manda nada; el boton manda un solo evento
    recurrente con Meet e invitaciones, y un segundo toque no crea otro."""
    monkeypatch.setenv("GCAL_CREAR_EVENTOS", "off")
    d = _crear(cliente, title="Marketing Semanal", repeticion=VIERNES)
    aid = d["asunto_id"]
    monkeypatch.delenv("GCAL_CREAR_EVENTOS")

    eventos = _eventos(cliente)
    assert len(eventos) == 2
    assert all(e["google"] == {"estado": "", "error": "", "meet": "", "puede_enviar": True}
               for e in eventos)
    assert _llamadas_a_google(google) == []

    r = cliente.post(f"/api/calendar/asuntos/{aid}/google", headers=_AUTH)

    assert r.get_json()["ok"] is True
    [(metodo, kw)] = google.escrituras()
    assert metodo == "insert" and kw["sendUpdates"] == "all" and kw["conferenceDataVersion"] == 1
    assert kw["body"]["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=FR"]
    assert kw["body"]["start"] == {"dateTime": "2026-09-18T19:00:00", "timeZone": MVD}
    assert "conferenceData" in kw["body"] and len(kw["body"]["attendees"]) == 3
    eventos = _eventos(cliente)
    assert all(e["google"]["estado"] == "ok" and e["google"]["puede_enviar"] is False
               for e in eventos)
    assert {e["meeting_url"] for e in eventos} == {"https://meet.google.com/abc-defg-hij"}

    cliente.post(f"/api/calendar/asuntos/{aid}/google", headers=_AUTH)
    assert len(google.de("insert")) == 1


def test_con_el_envio_apagado_no_se_ofrece_ni_se_manda(cliente, google, monkeypatch):
    monkeypatch.setenv("GCAL_CREAR_EVENTOS", "off")
    d = _crear(cliente)

    [ev] = _eventos(cliente)
    r = cliente.post(f"/api/calendar/asuntos/{d['asunto_id']}/google", headers=_AUTH)

    assert ev["google"]["puede_enviar"] is False
    assert r.status_code == 409
    assert _llamadas_a_google(google) == []


def test_arrancar_el_crm_no_manda_nada_a_google(app, cliente, google, monkeypatch):
    monkeypatch.setenv("GCAL_CREAR_EVENTOS", "off")
    _crear(cliente, repeticion=VIERNES)
    monkeypatch.delenv("GCAL_CREAR_EVENTOS")

    dashboard.create_app(app.config["DB_PATH"])

    assert google.llamadas == []


# ── la pagina ────────────────────────────────────────────────────────────────

def test_la_pagina_principal_se_renderiza(tmp_path, monkeypatch):
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
    for pedazo in ('id="cal-aviso-google"', "_calReintentarGoogle", "Reintentar en Google"):
        assert pedazo.encode() in r.data, pedazo
