"""La cola de busqueda de mails, servida por API.

El scraper escribe directo al CRM de produccion, pero el buscador de mails
necesita un navegador y corre en la maquina de casa. Estos dos endpoints son el
puente: uno entrega la cola, el otro recibe lo que dio cada sitio.

Los dos usan las mismas funciones que el job local (seleccionar_pendientes y
aplicar_resultado), a proposito: dos versiones del ORDER BY de la cola es como
se vuelve a caer en que las filas caidas de id bajo se coman el limite para
siempre.
"""

import pytest

import dashboard
from database import init_db, insert_business, get_business


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


_AUTH = {"x-admin-token": "token-de-test"}


def _comercio(db, n, **extra):
    datos = {"name": f"Comercio {n}", "phone": f"09900{n:04}",
             "website": f"https://comercio{n}.com.uy", "source": "discovery",
             "maps_url": f"https://maps.google.com/?cid={n}"}
    datos.update(extra)
    return insert_business(db, datos)


# ─── La cola ──────────────────────────────────────────────────────────────────

def test_sin_token_no_se_entrega_la_cola(cliente):
    assert cliente.get("/api/discovery/pendientes").status_code == 401


def test_entrega_los_pendientes_con_su_sitio(app, cliente):
    db = app.config["DB_PATH"]
    _comercio(db, 1)
    _comercio(db, 2)
    r = cliente.get("/api/discovery/pendientes?limite=10", headers=_AUTH)
    assert r.status_code == 200
    items = r.get_json()["items"]
    assert len(items) == 2
    assert set(items[0]) == {"id", "website"}


def test_no_entrega_los_que_ya_tienen_mail(app, cliente):
    db = app.config["DB_PATH"]
    _comercio(db, 3, email="hola@comercio3.com.uy")
    _comercio(db, 4)
    items = cliente.get("/api/discovery/pendientes", headers=_AUTH).get_json()["items"]
    assert [i["website"] for i in items] == ["https://comercio4.com.uy"]


def test_no_entrega_otras_cohortes(app, cliente):
    """El padron sin web y los leads de Meta no son asunto de este job."""
    db = app.config["DB_PATH"]
    _comercio(db, 5, source="meta")
    _comercio(db, 6, source=None)
    assert cliente.get("/api/discovery/pendientes", headers=_AUTH).get_json()["items"] == []


def test_respeta_el_limite(app, cliente):
    db = app.config["DB_PATH"]
    for n in range(10, 20):
        _comercio(db, n)
    items = cliente.get("/api/discovery/pendientes?limite=3", headers=_AUTH).get_json()["items"]
    assert len(items) == 3


# ─── Los resultados ───────────────────────────────────────────────────────────

def test_sin_token_no_se_aceptan_resultados(cliente):
    r = cliente.post("/api/discovery/mails", json={"resultados": []})
    assert r.status_code == 401


def test_guarda_el_mail_encontrado(app, cliente):
    db = app.config["DB_PATH"]
    bid = _comercio(db, 20)
    r = cliente.post("/api/discovery/mails", headers=_AUTH, json={"resultados": [
        {"id": bid, "email": "info@comercio20.com.uy", "abrio": True}]})
    assert r.status_code == 200
    assert r.get_json()["con_mail"] == 1
    fila = get_business(db, bid)
    assert fila["email"] == "info@comercio20.com.uy"
    assert fila["status"] == "email_found"


def test_marca_el_que_abrio_y_no_publica_mail(app, cliente):
    """No se vuelve a visitar: si no publica direccion, insistir cuesta minutos
    y no cambia el resultado."""
    db = app.config["DB_PATH"]
    bid = _comercio(db, 21)
    r = cliente.post("/api/discovery/mails", headers=_AUTH, json={"resultados": [
        {"id": bid, "email": None, "abrio": True}]})
    assert r.get_json()["sin_mail"] == 1
    assert get_business(db, bid)["status"] == "no_email"
    assert cliente.get("/api/discovery/pendientes", headers=_AUTH).get_json()["items"] == []


def test_el_que_no_abrio_vuelve_a_la_cola(app, cliente):
    """Un sitio caido un rato no es un sitio sin mail: se reintenta, pero al
    fondo."""
    db = app.config["DB_PATH"]
    bid = _comercio(db, 22)
    r = cliente.post("/api/discovery/mails", headers=_AUTH, json={"resultados": [
        {"id": bid, "email": None, "abrio": False, "error": "Timeout"}]})
    assert r.get_json()["no_abrio"] == 1
    fila = get_business(db, bid)
    assert fila["status"] not in ("email_found", "no_email")
    assert "Timeout" in (fila["error_message"] or "")
    assert len(cliente.get("/api/discovery/pendientes", headers=_AUTH).get_json()["items"]) == 1


def test_los_reintentos_van_despues_de_los_nunca_vistos(app, cliente):
    """Sin esto, un par de dominios caidos con id bajo se comen el limite de
    todas las corridas y las filas nuevas no se miran nunca."""
    db = app.config["DB_PATH"]
    caido = _comercio(db, 30)
    nuevo = _comercio(db, 31)
    cliente.post("/api/discovery/mails", headers=_AUTH, json={"resultados": [
        {"id": caido, "email": None, "abrio": False, "error": "Timeout"}]})
    items = cliente.get("/api/discovery/pendientes", headers=_AUTH).get_json()["items"]
    assert [i["id"] for i in items] == [nuevo, caido]


def test_una_tanda_entera_de_una(app, cliente):
    db = app.config["DB_PATH"]
    ids = [_comercio(db, n) for n in range(40, 45)]
    r = cliente.post("/api/discovery/mails", headers=_AUTH, json={"resultados": [
        {"id": ids[0], "email": "a@x.uy", "abrio": True},
        {"id": ids[1], "email": "b@x.uy", "abrio": True},
        {"id": ids[2], "email": None, "abrio": True},
        {"id": ids[3], "email": None, "abrio": False, "error": "DNS"},
        {"id": ids[4], "email": None, "abrio": False, "error": "DNS"},
    ]})
    assert r.get_json() == {"ok": True, "con_mail": 2, "sin_mail": 1, "no_abrio": 2}


def test_un_id_inexistente_no_tumba_la_tanda(app, cliente):
    db = app.config["DB_PATH"]
    bid = _comercio(db, 50)
    r = cliente.post("/api/discovery/mails", headers=_AUTH, json={"resultados": [
        {"id": 999999, "email": "x@y.uy", "abrio": True},
        {"id": bid, "email": "bueno@x.uy", "abrio": True},
    ]})
    assert r.status_code == 200
    assert get_business(db, bid)["email"] == "bueno@x.uy"


def test_un_id_que_no_es_numero_no_tumba_la_tanda(app, cliente):
    """El camino de excepcion tiene que loguear sin explotar: `logging` no
    estaba importado en el modulo y el NameError solo aparecia aca."""
    db = app.config["DB_PATH"]
    bid = _comercio(db, 60)
    r = cliente.post("/api/discovery/mails", headers=_AUTH, json={"resultados": [
        {"id": "no-es-un-numero", "email": "x@y.uy", "abrio": True},
        {"id": bid, "email": "bueno@x.uy", "abrio": True},
    ]})
    assert r.status_code == 200
    assert r.get_json()["con_mail"] == 1
    assert get_business(db, bid)["email"] == "bueno@x.uy"
