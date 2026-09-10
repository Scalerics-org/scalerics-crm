"""El cliente de Google se construye una sola vez.

Cada `build()` de googleapiclient deja ~0,45 MB que no vuelven al heap. Los
syncs de Calendly y de discovery llaman a `build()` en cada corrida, o sea
cada 10 minutos: son ~65 MB por dia de crecimiento en el worker. Eso es lo
que termino en el OOM del 9-9-2026, que dejo la app aceptando TCP sin
contestar nunca.

El objeto service es reusable: las credenciales refrescan solo su access
token cuando vence, asi que no hay motivo para reconstruirlo.
"""

import pytest

from services import google_api


@pytest.fixture(autouse=True)
def cache_limpio():
    google_api.reset_cache()
    yield
    google_api.reset_cache()


@pytest.fixture
def builds(monkeypatch):
    """Cuenta cuantas veces se construye de verdad un cliente."""
    hechos = []

    def fake_build(api, version, credentials):
        hechos.append((api, version, credentials))
        return {"api": api, "version": version, "n": len(hechos)}

    monkeypatch.setattr(google_api, "_build", fake_build)
    return hechos


def test_pedir_el_mismo_service_dos_veces_construye_uno_solo(builds):
    a = google_api.get_service("gmail", "v1", "creds", account="gmail")
    b = google_api.get_service("gmail", "v1", "creds", account="gmail")

    assert a is b
    assert len(builds) == 1


def test_los_syncs_de_todo_un_dia_construyen_un_solo_cliente(builds):
    # 144 corridas = un dia de syncs cada 10 minutos.
    for _ in range(144):
        google_api.get_service("gmail", "v1", "creds", account="gmail")

    assert len(builds) == 1


def test_dos_apis_distintas_no_comparten_cliente(builds):
    cal = google_api.get_service("calendar", "v3", "creds", account="gcal")
    drive = google_api.get_service("drive", "v3", "creds", account="gcal")

    assert cal is not drive
    assert len(builds) == 2


def test_dos_cuentas_distintas_no_comparten_cliente(builds):
    # gmail y gcal se autentican con credenciales distintas (GMAIL_* vs
    # GCAL_*): compartir el cliente le daria a una los permisos de la otra.
    gmail = google_api.get_service("gmail", "v1", "creds-gmail", account="gmail")
    otra = google_api.get_service("gmail", "v1", "creds-gcal", account="gcal")

    assert gmail is not otra
    assert len(builds) == 2


def test_reset_cache_obliga_a_reconstruir(builds):
    google_api.get_service("gmail", "v1", "creds", account="gmail")
    google_api.reset_cache()
    google_api.get_service("gmail", "v1", "creds", account="gmail")

    assert len(builds) == 2


def test_usa_discovery_estatico_para_no_pedirle_el_esquema_a_google(monkeypatch):
    # Sin static_discovery googleapiclient baja el discovery doc por HTTP en
    # cada build: 0,21 MB de JSON y 1,9 MB de heap de mas, cada 10 minutos.
    llamadas = {}

    def fake_build(api, version, **kwargs):
        llamadas.update(kwargs)
        return object()

    modulo = pytest.importorskip("googleapiclient.discovery")
    monkeypatch.setattr(modulo, "build", fake_build)

    google_api._build("gmail", "v1", "creds")

    assert llamadas.get("static_discovery") is True


# --- Los sitios que antes construian de cero en cada llamada ---


@pytest.fixture
def gcal_env(monkeypatch):
    monkeypatch.setenv("GCAL_CLIENT_ID", "id")
    monkeypatch.setenv("GCAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GCAL_REFRESH_TOKEN", "refresh")


def test_el_service_de_calendar_de_las_rutas_se_construye_una_vez(builds, gcal_env):
    from routes import calendar as rutas_calendar

    a, err_a = rutas_calendar._get_calendar_service()
    b, err_b = rutas_calendar._get_drive_service()
    c, err_c = rutas_calendar._get_calendar_service()

    assert (err_a, err_b, err_c) == (None, None, None)
    assert a is c
    assert a is not b
    # calendar y drive: dos clientes, no tres.
    assert len(builds) == 2


MODULOS_QUE_SINCRONIZAN = [
    "services/calendly_gcal.py",
    "services/calendly_gmail.py",
    "services/discovery_respuestas.py",
    "routes/calendar.py",
]


@pytest.mark.parametrize("ruta", MODULOS_QUE_SINCRONIZAN)
def test_ningun_modulo_llama_a_build_por_su_cuenta(ruta):
    # Mismo criterio que el test que vigila que linkedin_posts no importe
    # anthropic: se chequea sobre el fuente. Un `build()` suelto vuelve a
    # abrir la fuga aunque el cache exista, y no lo notariamos hasta el
    # proximo OOM de madrugada.
    import pathlib
    import re

    fuente = pathlib.Path(ruta).read_text(encoding="utf-8")
    # `(?<![\w.])` para que agarre tanto `service = build(` como
    # `return build(`, y no confunda `_build(` ni `google_api.build(`.
    sueltos = re.findall(r"(?<![\w.])build\(", fuente)

    assert sueltos == [], f"{ruta} llama a build() directo: usa services.google_api"


# --- Un cliente por thread ---


def test_dos_threads_no_comparten_el_mismo_cliente(builds):
    # googleapiclient arrastra un httplib2.Http, y la doc de Google dice
    # textual que "The httplib2.Http() objects are not thread-safe": tiene un
    # pool de conexiones en un dict comun. Antes de cachear, cada llamada
    # construia el suyo y quedaban aislados; si ahora compartimos uno solo
    # entre las 4 threads de gunicorn y las de los jobs, aparecen respuestas
    # mezcladas y errores intermitentes.
    import threading

    obtenidos = {}

    def pedir(nombre):
        obtenidos[nombre] = google_api.get_service(
            "gmail", "v1", "creds", account="gmail")

    hilos = [threading.Thread(target=pedir, args=(f"h{i}",)) for i in range(3)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    distintos = {id(s) for s in obtenidos.values()}
    assert len(distintos) == 3, "las tres threads recibieron el mismo cliente"
    assert len(builds) == 3


def test_dentro_de_una_thread_se_sigue_construyendo_una_sola_vez(builds):
    # Que sea por thread no puede reabrir la fuga: el thread de fondo del
    # sync es largo y hace una corrida cada 10 minutos durante dias.
    import threading

    def muchas_corridas():
        for _ in range(144):
            google_api.get_service("gmail", "v1", "creds", account="gmail")

    h = threading.Thread(target=muchas_corridas)
    h.start()
    h.join()

    assert len(builds) == 1
