"""El endpoint publico que recibe las descargas de la guia de scalerics.com.

Es la unica ruta del CRM que puede llamar un navegador cualquiera: no lleva
`x-admin-token` porque el token quedaria a la vista en el JavaScript del sitio.
Por eso casi todos los tests de aca son de rechazo, no de exito.
"""

import sqlite3

import pytest
from flask import Flask

from database import init_db, insert_business
from routes.web import web_bp


ORIGEN = "https://scalerics.com"


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = str(tmp_path / "leads.db")
    init_db(db_path)
    monkeypatch.setenv("DB_PATH", db_path)
    monkeypatch.delenv("WEB_LEAD_ORIGINS", raising=False)
    flask_app = Flask(__name__)
    flask_app.register_blueprint(web_bp)
    flask_app.config["DB_PATH"] = db_path
    return flask_app


@pytest.fixture
def cliente(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _sin_memoria_entre_tests():
    """El tope por IP vive en memoria del proceso: se limpia entre tests."""
    from routes import web
    web._VISITAS.clear()
    yield
    web._VISITAS.clear()


def _filas(app):
    conn = sqlite3.connect(app.config["DB_PATH"])
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM businesses")]
    finally:
        conn.close()


def _post(cliente, cuerpo=None, origen=ORIGEN, ip="1.2.3.4"):
    cuerpo = {"name": "Ana Gomez", "email": "ana@panaderialaflor.uy",
              "project": "sitio"} if cuerpo is None else cuerpo
    headers = {"Origin": origen} if origen else {}
    headers["X-Forwarded-For"] = ip
    return cliente.post("/api/web/lead", json=cuerpo, headers=headers)


# ── Camino feliz ─────────────────────────────────────────────────────────────

def test_crea_el_lead(cliente, app):
    r = _post(cliente)
    assert r.status_code == 201
    filas = _filas(app)
    assert len(filas) == 1
    assert filas[0]["name"] == "Ana Gomez"
    assert filas[0]["email"] == "ana@panaderialaflor.uy"


def test_el_lead_queda_fuera_de_las_campanas_automaticas(cliente, app):
    """La garantia que importa: nadie le escribe solo.

    `services/meta_reminders.py` filtra por `source = 'meta'` y
    `services/discovery_emails.py` por `source = 'discovery'`. Si esta fila
    cayera en cualquiera de los dos, publicar la guia se convertiria en
    mandarle correo automatico a quien la descargo, que es justo lo que la
    pagina promete que no pasa ("Sin spam. Un mail con la guia y nada mas").
    """
    _post(cliente)
    source = _filas(app)[0]["source"]
    assert source == "web_guia"
    assert source not in ("meta", "discovery")


def test_guarda_de_donde_vino_y_que_proyecto_eligio(cliente, app):
    _post(cliente)
    fila = _filas(app)[0]
    assert "guía de precios" in (fila["notes"] or "").lower()
    assert "sitio" in (fila["form_data"] or "")


def test_el_project_es_opcional(cliente, app):
    r = _post(cliente, {"name": "Sin Proyecto", "email": "x@ejemplo.uy"})
    assert r.status_code == 201
    assert len(_filas(app)) == 1


# ── Rechazos ─────────────────────────────────────────────────────────────────

def test_rechaza_un_origen_ajeno(cliente, app):
    r = _post(cliente, origen="https://sitio-que-no-es-nuestro.com")
    assert r.status_code == 403
    assert _filas(app) == []


def test_rechaza_si_no_viene_ningun_origen(cliente, app):
    """Un curl suelto no trae Origin. Es la forma barata de tirar spam."""
    r = _post(cliente, origen=None)
    assert r.status_code == 403
    assert _filas(app) == []


def test_acepta_el_dominio_con_www(cliente, app):
    r = _post(cliente, origen="https://www.scalerics.com")
    assert r.status_code == 201


@pytest.mark.parametrize("mail", ["", "no-es-un-mail", "arroba@", "@dominio.com", None])
def test_rechaza_mails_invalidos(cliente, app, mail):
    r = _post(cliente, {"name": "Alguien", "email": mail})
    assert r.status_code == 400
    assert _filas(app) == []


def test_rechaza_sin_nombre(cliente, app):
    r = _post(cliente, {"name": "  ", "email": "a@b.uy"})
    assert r.status_code == 400
    assert _filas(app) == []


def test_recorta_los_campos_largos(cliente, app):
    r = _post(cliente, {"name": "N" * 5000, "email": "largo@ejemplo.uy"})
    assert r.status_code == 201
    assert len(_filas(app)[0]["name"]) <= 200


# ── Duplicados ───────────────────────────────────────────────────────────────

def test_el_mismo_mail_dos_veces_no_duplica(cliente, app):
    _post(cliente)
    r = _post(cliente)
    assert r.status_code == 200
    assert len(_filas(app)) == 1


def test_no_pisa_un_negocio_que_ya_existia(cliente, app):
    """Alguien que ya es cliente y descarga la guia sigue siendo cliente.

    Meta si le devuelve `source='meta'` a una fila existente, pero ahi el lead
    es la conversion. Aca no: bajar un PDF no convierte a un cliente en un
    lead nuevo ni justifica reescribirle el origen.
    """
    insert_business(app.config["DB_PATH"], {
        "name": "Panaderia La Flor", "email": "ana@panaderialaflor.uy",
        "phone": "099111222", "source": "scraped",
    })
    r = _post(cliente)
    assert r.status_code == 200
    filas = _filas(app)
    assert len(filas) == 1
    assert filas[0]["source"] == "scraped"
    assert filas[0]["name"] == "Panaderia La Flor"


def test_el_duplicado_no_distingue_mayusculas(cliente, app):
    _post(cliente)
    r = _post(cliente, {"name": "Ana", "email": "ANA@PanaderiaLaFlor.uy"})
    assert r.status_code == 200
    assert len(_filas(app)) == 1


# ── Tope por IP ──────────────────────────────────────────────────────────────

def test_corta_despues_del_tope(cliente, app):
    from routes import web
    for i in range(web._TOPE_POR_IP):
        assert _post(cliente, {"name": f"P{i}", "email": f"p{i}@ejemplo.uy"}).status_code == 201
    r = _post(cliente, {"name": "Uno mas", "email": "demas@ejemplo.uy"})
    assert r.status_code == 429
    assert len(_filas(app)) == web._TOPE_POR_IP


def test_el_tope_es_por_ip(cliente, app):
    from routes import web
    for i in range(web._TOPE_POR_IP):
        _post(cliente, {"name": f"P{i}", "email": f"p{i}@ejemplo.uy"}, ip="9.9.9.9")
    r = _post(cliente, {"name": "Otra IP", "email": "otra@ejemplo.uy"}, ip="8.8.8.8")
    assert r.status_code == 201


# ── CORS ─────────────────────────────────────────────────────────────────────

def test_el_preflight_habilita_al_sitio(cliente):
    r = cliente.options("/api/web/lead", headers={
        "Origin": ORIGEN,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert r.status_code == 204
    assert r.headers.get("Access-Control-Allow-Origin") == ORIGEN


def test_el_preflight_no_habilita_a_un_tercero(cliente):
    r = cliente.options("/api/web/lead", headers={
        "Origin": "https://sitio-que-no-es-nuestro.com",
        "Access-Control-Request-Method": "POST",
    })
    assert r.headers.get("Access-Control-Allow-Origin") is None


def test_la_respuesta_del_post_trae_cors(cliente):
    r = _post(cliente)
    assert r.headers.get("Access-Control-Allow-Origin") == ORIGEN


# ── Cableado real ────────────────────────────────────────────────────────────
#
# Los tests de arriba montan un Flask pelado con el blueprint solo. Estos
# levantan la aplicacion de verdad, con el `before_request` que le pide sesion
# a todo. Es lo unico que prueba que la exencion de `/api/web/` esta puesta:
# sin ella el endpoint devuelve 401 y el sitio no puede llamarlo.

@pytest.fixture
def app_completa(tmp_path, monkeypatch):
    import dashboard
    db_path = str(tmp_path / "leads.db")
    init_db(db_path)
    monkeypatch.setenv("DB_PATH", db_path)
    monkeypatch.delenv("WEB_LEAD_ORIGINS", raising=False)
    app = dashboard.create_app(db_path)
    app.config["TESTING"] = True
    return app


def test_anda_sin_sesion_en_la_aplicacion_real(app_completa):
    r = app_completa.test_client().post(
        "/api/web/lead",
        json={"name": "Sin Sesion", "email": "sinsesion@ejemplo.uy"},
        headers={"Origin": ORIGEN, "X-Forwarded-For": "5.5.5.5"},
    )
    assert r.status_code == 201, r.get_json()


def test_el_resto_del_api_sigue_pidiendo_sesion(app_completa):
    """La exencion es de `/api/web/`, no un agujero en todo el API."""
    r = app_completa.test_client().get("/api/leads", headers={"Origin": ORIGEN})
    assert r.status_code == 401
