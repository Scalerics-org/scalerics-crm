"""Invitados automaticos y tipo de proyecto en la reunion (Juan, 16/9).

"Agrega la funcion que cuando agendo una reunion y pongo un lead se ponga
inmediatamente como invitado al lead y a estos mail [...]. Que aparte me deje
poner la opcion si es automatizacion, pagina web, e commerce o desarrollo a
medida u otro".

Aca va el lado del servidor; el del navegador esta en
`tests/test_reunion_invitados_tipo_js.py`. Ningun test habla con Google:
se usa el `GoogleFalso` que ya tiene la suite.

  tipo        las cinco opciones se guardan, una inventada se rechaza, "Otro"
              guarda el texto libre, y todo viaja en la descripcion del evento
  serie       "esta y las siguientes" arranca la serie nueva con el mismo tipo
  invitados   el servidor guarda EXACTAMENTE los mails que le mandan: los tres
              fijos los propone el modal, no se agregan aca (si no, lo que Juan
              saco del campo volveria solo)
  Calendly    sigue sin recibir una sola llamada a Google
  espejos     las constantes del navegador y las del servidor son las mismas
"""

import json
import re

import pytest

import dashboard
from database import (create_meeting, get_meeting, get_reunion_asunto, init_db,
                      insert_business, update_business)
from services import gcal_eventos as gce
from services import recurrencia as rec
from services import tipos_proyecto as tp
from tests.test_calendario_google_invitaciones import GoogleFalso

_AUTH = {"x-admin-token": "token-de-test"}
VIERNES = {"freq": "semanal", "dias": [4], "fin": "nunca"}
HTML = dashboard.DASHBOARD_HTML


@pytest.fixture
def google(monkeypatch, _sin_credenciales_reales):
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
              "time": "19:00", "duration_min": 60}
    cuerpo.update(campos)
    return cliente.post("/api/calendar/events", json=cuerpo, headers=_AUTH).get_json()


def _con_lead(cliente, db, **campos):
    lid = insert_business(db, {"name": "Optica Luz"})
    update_business(db, lid, email="hola@opticaluz.uy")
    d = _crear(cliente, tipo="cliente", client_id=lid, title="Demo Optica Luz", **campos)
    return lid, d


def _eventos(cliente, desde="2026-09-01", hasta="2026-09-30"):
    return cliente.get(f"/api/calendar/events?start={desde}&end={hasta}",
                       headers=_AUTH).get_json()["events"]


# ── el tipo se guarda y llega a la invitacion ────────────────────────────────

@pytest.mark.parametrize("clave,etiqueta", list(tp.TIPOS_PROYECTO.items()))
def test_las_cinco_opciones_se_guardan_y_van_en_la_descripcion(app, cliente, google,
                                                               clave, etiqueta):
    db = app.config["DB_PATH"]

    _, d = _con_lead(cliente, db, tipo_proyecto=clave)

    fila = get_meeting(db, d["meeting_id"])
    assert fila["tipo_proyecto"] == clave
    body = google.de("insert")[0]["body"]
    assert body["description"] == f"Tipo de proyecto: {etiqueta}"
    # El titulo que escribio Juan no se toca.
    assert body["summary"] == "Demo Optica Luz"


def test_el_tipo_va_arriba_de_la_descripcion_que_ya_estaba(app, cliente, google):
    _con_lead(cliente, app.config["DB_PATH"], tipo_proyecto="web",
              description="Repasar la home")

    assert google.de("insert")[0]["body"]["description"] == (
        "Tipo de proyecto: Página web\n\nRepasar la home")


def test_sin_tipo_la_descripcion_queda_como_antes(app, cliente, google):
    _crear(cliente, description="Pauta de octubre")

    assert google.de("insert")[0]["body"]["description"] == "Pauta de octubre"


def test_otro_guarda_el_texto_libre_y_es_lo_que_ven_los_invitados(app, cliente, google):
    db = app.config["DB_PATH"]

    _, d = _con_lead(cliente, db, tipo_proyecto="otro", tipo_otro="Chatbot de WhatsApp")

    fila = get_meeting(db, d["meeting_id"])
    assert fila["tipo_proyecto"] == "otro" and fila["tipo_otro"] == "Chatbot de WhatsApp"
    assert google.de("insert")[0]["body"]["description"] == (
        "Tipo de proyecto: Chatbot de WhatsApp")


def test_otro_sin_texto_queda_como_otro_a_secas(app, cliente, google):
    db = app.config["DB_PATH"]

    _, d = _con_lead(cliente, db, tipo_proyecto="otro", tipo_otro="   ")

    assert get_meeting(db, d["meeting_id"])["tipo_otro"] is None
    assert google.de("insert")[0]["body"]["description"] == "Tipo de proyecto: Otro"


def test_el_texto_de_otro_se_recorta(app, cliente):
    db = app.config["DB_PATH"]

    _, d = _con_lead(cliente, db, tipo_proyecto="otro", tipo_otro="x" * 200)

    assert len(get_meeting(db, d["meeting_id"])["tipo_otro"]) == tp.MAX_OTRO


def test_un_tipo_inventado_se_rechaza_y_no_guarda_nada(app, cliente, google):
    db = app.config["DB_PATH"]
    lid = insert_business(db, {"name": "Optica Luz"})

    d = _crear(cliente, tipo="cliente", client_id=lid, title="Demo",
               tipo_proyecto="criptomonedas")

    assert d["ok"] is False and "Elegí" in d["error"]
    assert google.escrituras() == []


def test_el_texto_de_otro_no_se_guarda_en_los_otros_tipos(app, cliente):
    db = app.config["DB_PATH"]

    _, d = _con_lead(cliente, db, tipo_proyecto="ecommerce", tipo_otro="algo suelto")

    assert get_meeting(db, d["meeting_id"])["tipo_otro"] is None


def test_el_listado_trae_el_tipo_ya_escrito_para_el_calendario(app, cliente):
    db = app.config["DB_PATH"]
    _con_lead(cliente, db, tipo_proyecto="aMedida")

    [ev] = _eventos(cliente)

    assert ev["tipo_proyecto"] == "aMedida"
    assert ev["tipo_texto"] == "Desarrollo a medida"


def test_una_reunion_vieja_sin_tipo_no_muestra_nada(app, cliente):
    db = app.config["DB_PATH"]
    lid = insert_business(db, {"name": "Ferreteria El Sol"})
    create_meeting(db, lid, title="Consultoria", start_at="2026-09-18T15:00:00",
                   end_at="2026-09-18T16:00:00", status="scheduled", origen="crm")

    [ev] = _eventos(cliente)

    assert ev["tipo_proyecto"] == "" and ev["tipo_texto"] == ""


# ── editar ───────────────────────────────────────────────────────────────────

def test_editar_cambia_el_tipo_y_lo_lleva_a_google(app, cliente, google):
    db = app.config["DB_PATH"]
    _, d = _con_lead(cliente, db, tipo_proyecto="web")
    mid = d["meeting_id"]
    google.llamadas.clear()

    r = cliente.patch(f"/api/calendar/meetings/{mid}", headers=_AUTH, json={
        "date": "2026-09-18", "time": "19:00", "tipo_proyecto": "otro",
        "tipo_otro": "Integracion con Meta"})

    assert r.get_json()["ok"] is True
    fila = get_meeting(db, mid)
    assert fila["tipo_proyecto"] == "otro" and fila["tipo_otro"] == "Integracion con Meta"
    [(metodo, kw)] = google.escrituras()
    assert metodo == "patch" and kw["sendUpdates"] == "all"
    assert kw["body"]["description"] == "Tipo de proyecto: Integracion con Meta"


def test_un_patch_que_no_habla_del_tipo_no_lo_borra(app, cliente):
    """El arrastre manda solo fecha y hora."""
    db = app.config["DB_PATH"]
    _, d = _con_lead(cliente, db, tipo_proyecto="ecommerce")

    cliente.patch(f"/api/calendar/meetings/{d['meeting_id']}", headers=_AUTH,
                  json={"date": "2026-09-19", "time": "10:00"})

    assert get_meeting(db, d["meeting_id"])["tipo_proyecto"] == "ecommerce"


def test_se_puede_sacar_el_tipo(app, cliente):
    db = app.config["DB_PATH"]
    _, d = _con_lead(cliente, db, tipo_proyecto="ecommerce")

    cliente.patch(f"/api/calendar/meetings/{d['meeting_id']}", headers=_AUTH,
                  json={"date": "2026-09-18", "time": "19:00", "tipo_proyecto": ""})

    assert get_meeting(db, d["meeting_id"])["tipo_proyecto"] is None


def test_un_tipo_inventado_al_editar_se_rechaza(app, cliente):
    db = app.config["DB_PATH"]
    _, d = _con_lead(cliente, db, tipo_proyecto="web")

    r = cliente.patch(f"/api/calendar/meetings/{d['meeting_id']}", headers=_AUTH,
                      json={"date": "2026-09-18", "time": "19:00",
                            "tipo_proyecto": "criptomonedas"})

    assert r.status_code == 400
    assert get_meeting(db, d["meeting_id"])["tipo_proyecto"] == "web"


# ── series ───────────────────────────────────────────────────────────────────

def test_una_serie_guarda_el_tipo_una_vez_para_todas(app, cliente, google):
    db = app.config["DB_PATH"]

    _, d = _con_lead(cliente, db, tipo_proyecto="automatizacion", repeticion=VIERNES)

    assert get_meeting(db, d["meeting_id"])["tipo_proyecto"] == "automatizacion"
    body = google.de("insert")[0]["body"]
    assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=FR"]
    assert body["description"] == "Tipo de proyecto: Automatización"
    assert all(e["tipo_texto"] == "Automatización" for e in _eventos(cliente))


def test_esta_y_las_siguientes_arranca_la_serie_nueva_con_el_tipo_nuevo(app, cliente, google):
    db = app.config["DB_PATH"]
    _, d = _con_lead(cliente, db, tipo_proyecto="web", repeticion=VIERNES)
    google.llamadas.clear()

    r = cliente.patch(f"/api/calendar/meetings/{d['meeting_id']}", headers=_AUTH, json={
        "date": "2026-10-02", "time": "19:00", "ocurrencia": "2026-10-02",
        "alcance": "siguientes", "tipo_proyecto": "ecommerce"})

    nueva_id = r.get_json()["nueva_id"]
    assert nueva_id
    # La serie vieja conserva el tipo con el que se creo; la nueva lleva el nuevo.
    assert get_meeting(db, d["meeting_id"])["tipo_proyecto"] == "web"
    assert get_meeting(db, nueva_id)["tipo_proyecto"] == "ecommerce"
    nuevo = [kw for m, kw in google.escrituras() if m == "insert"][0]
    assert nuevo["body"]["description"] == "Tipo de proyecto: E-commerce"


def test_esta_y_las_siguientes_sin_tocar_el_tipo_lo_hereda(app, cliente):
    db = app.config["DB_PATH"]
    _, d = _con_lead(cliente, db, tipo_proyecto="otro", tipo_otro="Chatbot",
                     repeticion=VIERNES)

    r = cliente.patch(f"/api/calendar/meetings/{d['meeting_id']}", headers=_AUTH, json={
        "date": "2026-10-02", "time": "20:00", "ocurrencia": "2026-10-02",
        "alcance": "siguientes"})

    nueva = get_meeting(db, r.get_json()["nueva_id"])
    assert nueva["tipo_proyecto"] == "otro" and nueva["tipo_otro"] == "Chatbot"


def test_editar_todas_cambia_el_tipo_de_la_serie_entera(app, cliente, google):
    db = app.config["DB_PATH"]
    aid = _crear(cliente, repeticion=VIERNES, tipo_proyecto="web")["asunto_id"]
    google.llamadas.clear()

    cliente.patch(f"/api/calendar/asuntos/{aid}", headers=_AUTH, json={
        "date": "2026-09-18", "time": "19:00", "ocurrencia": "2026-09-18",
        "alcance": "todas", "tipo_proyecto": "automatizacion"})

    assert get_reunion_asunto(db, aid)["tipo_proyecto"] == "automatizacion"
    [(metodo, kw)] = google.escrituras()
    assert metodo == "patch"
    assert kw["body"]["description"] == "Tipo de proyecto: Automatización"


def test_una_reunion_de_otro_asunto_tambien_guarda_el_tipo(app, cliente):
    db = app.config["DB_PATH"]

    d = _crear(cliente, tipo_proyecto="aMedida")

    assert get_reunion_asunto(db, d["asunto_id"])["tipo_proyecto"] == "aMedida"


# ── los invitados los propone el modal, no el servidor ───────────────────────

def test_el_servidor_guarda_exactamente_los_mails_que_le_mandan(app, cliente):
    """Lo que Juan saco del campo tiene que quedar afuera: si el servidor
    agregara los tres fijos al guardar, volverian solos."""
    db = app.config["DB_PATH"]
    lid = insert_business(db, {"name": "Optica Luz"})
    update_business(db, lid, email="hola@opticaluz.uy")

    d = _crear(cliente, tipo="cliente", client_id=lid, title="Demo",
               invitados=["gonzalosiuciak@gmail.com", "extra@mia.uy"])

    assert json.loads(get_meeting(db, d["meeting_id"])["invitados"]) == [
        "gonzalosiuciak@gmail.com", "extra@mia.uy"]


def test_editar_no_devuelve_los_fijos_que_se_habian_sacado(app, cliente):
    db = app.config["DB_PATH"]
    lid = insert_business(db, {"name": "Optica Luz"})
    d = _crear(cliente, tipo="cliente", client_id=lid, title="Demo",
               invitados=["extra@mia.uy"])

    cliente.patch(f"/api/calendar/meetings/{d['meeting_id']}", headers=_AUTH, json={
        "date": "2026-09-19", "time": "10:00", "invitados": ["extra@mia.uy"]})

    assert json.loads(get_meeting(db, d["meeting_id"])["invitados"]) == ["extra@mia.uy"]


def test_los_mails_repetidos_no_se_duplican_en_la_invitacion(app, cliente, google):
    """El mail del lead tambien viene en la lista: Google lo recibe una vez."""
    db = app.config["DB_PATH"]
    lid = insert_business(db, {"name": "Optica Luz"})
    update_business(db, lid, email="hola@opticaluz.uy")

    _crear(cliente, tipo="cliente", client_id=lid, title="Demo",
           invitados=["HOLA@opticaluz.uy", "gonzalosiuciak@gmail.com"])

    assert google.de("insert")[0]["body"]["attendees"] == [
        {"email": "hola@opticaluz.uy"}, {"email": "gonzalosiuciak@gmail.com"}]


# ── Calendly sigue intacto ───────────────────────────────────────────────────

def test_una_reunion_de_calendly_con_tipo_no_hace_ni_una_llamada_a_google(app, cliente, google):
    db = app.config["DB_PATH"]
    lid = insert_business(db, {"name": "Ferreteria El Sol"})
    mid = create_meeting(db, lid, title="Consultoria", start_at="2026-09-18T15:00:00",
                         end_at="2026-09-18T15:45:00", status="scheduled",
                         origen="calendly", tipo_proyecto="web",
                         calendar_event_id="https://api.calendly.com/scheduled_events/AAA")

    [ev] = _eventos(cliente)
    r_mover = cliente.patch(f"/api/calendar/meetings/{mid}", headers=_AUTH, json={
        "date": "2026-09-19", "time": "10:00", "tipo_proyecto": "ecommerce"})
    r_enviar = cliente.post(f"/api/calendar/meetings/{mid}/google", headers=_AUTH)

    assert ev["origen"] == "calendly" and ev["tipo_texto"] == "Página web"
    assert r_mover.status_code == 409 and r_enviar.status_code == 400
    assert [m for m, _ in google.llamadas if m != "list"] == []


# ── las constantes del navegador y las del servidor ──────────────────────────

def _lista_js(nombre: str):
    m = re.search(r"^const " + nombre + r" = (\[.*\]);$", HTML, re.M)
    assert m, f"no encontre {nombre} en el dashboard"
    return json.loads(m.group(1).replace("'", '"'))


def test_los_tres_fijos_del_navegador_son_los_del_servidor():
    """Si alguien cambia una lista y no la otra, esto avisa."""
    assert _lista_js("CAL_INVITADOS_FIJOS") == list(rec.INVITADOS_FIJOS)


def test_son_los_tres_mails_que_pidio_juan():
    assert list(rec.INVITADOS_FIJOS) == ["juan.pereyra.comunicacion@gmail.com",
                                         "gonzalosiuciak@gmail.com",
                                         "juantomasetti240@gmail.com"]


def test_los_tipos_del_navegador_son_los_del_servidor():
    assert _lista_js("CAL_TIPOS") == [[k, v] for k, v in tp.TIPOS_PROYECTO.items()]


def test_son_las_cinco_opciones_que_pidio_juan():
    assert list(tp.TIPOS_PROYECTO.values()) == [
        "Automatización", "Página web", "E-commerce", "Desarrollo a medida", "Otro"]


def test_las_claves_son_las_del_simulador_para_no_partir_los_reportes():
    """`SIM_TIPOS` reparte ventas por 'web', 'ecommerce' y 'aMedida'. Si el
    calendario usara otras claves, un reporte por tipo tendria que traducir."""
    for clave in ("web", "ecommerce", "aMedida"):
        assert clave in tp.CLAVES
        assert f"clave: '{clave}'" in HTML


def test_el_resumen_con_ia_pide_el_mismo_vocabulario():
    """Antes pedia 'app', que no es ninguno de los tipos del CRM."""
    import routes.calendar as cal
    fuente = __import__("inspect").getsource(cal.api_summarize_meeting)
    assert '"|".join(tp.CLAVES)' in __import__("inspect").getsource(cal)
    assert "app|" not in fuente
