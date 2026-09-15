"""Traer los anuncios de Meta y sus fotos, sin salir a internet en los tests.

Mismo patron que `meta_insights.sincronizar`: las funciones que hablan con la
API se inyectan, y el default es la de verdad. Asi los tests prueban la logica
de guardado —que es donde estan los errores— y no la red.

La forma de los fixtures salio de una llamada real a la cuenta
`act_1165635198430883` el 14/9/2026, no de suponer como contesta Meta. Un
fixture inventado prueba tu suposicion, no el codigo.
"""

import os

import pytest

from database import _connect, init_db
from services.meta_anuncios import sincronizar_anuncios


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


# Recortado de la respuesta real de /act_X/ads.
_ADS = [
    {
        "id": "120253602403650249",
        "name": "Web hace ganar - RMKTG",
        "effective_status": "ACTIVE",
        "campaign": {"id": "c1", "name": "Brand - Set26"},
        "adset": {"name": "RMKTG"},
        "creative": {
            "id": "cr1",
            "object_type": "SHARE",
            "title": "Agencia de desarrollo web y software en Uruguay",
            "body": "¿Cuántos visitantes llegan a tu web y no te contactan?",
            "image_url": "https://scontent.xx.fbcdn.net/v/imagen1.jpg?oh=abc",
        },
    },
    {
        "id": "120253602403650250",
        "name": "UGC - 2",
        "effective_status": "ACTIVE",
        "campaign": {"id": "c1", "name": "Brand - Set26"},
        "adset": {"name": "UGC"},
        # Los de video no traen image_url: Meta no da el still con los permisos
        # que tiene la app. Se guarda igual, sin foto.
        "creative": {"id": "cr2", "object_type": "VIDEO", "video_id": "v9",
                     "title": "", "body": "Mirá cómo lo hacemos"},
    },
    {
        "id": "120243368449890249",
        "name": "12/03 - Hiciste lo más difícil",
        "effective_status": "PAUSED",
        "campaign": {"id": "c2", "name": "Leads - Marzo"},
        "adset": {"name": "Amplio"},
        "creative": {"id": "cr3", "object_type": "SHARE",
                     "image_url": "https://scontent.xx.fbcdn.net/v/imagen3.jpg"},
    },
]

_INSIGHTS = [
    {"date_start": "2026-09-01", "ad_id": "120253602403650249",
     "spend": "12.34", "account_currency": "USD", "impressions": "1500",
     "clicks": "40", "reach": "1100",
     "actions": [{"action_type": "lead", "value": "2"}]},
    {"date_start": "2026-09-02", "ad_id": "120253602403650249",
     "spend": "8.66", "account_currency": "USD", "impressions": "900",
     "clicks": "25", "reach": "700", "actions": []},
]


def _correr(db, ads=None, insights=None, bajadas=None, **kw):
    """Corre el sync con la API falsa. `bajadas` junta lo que se intento bajar."""
    if bajadas is None:
        bajadas = []

    def bajar(url, destino):
        bajadas.append((url, destino))
        with open(destino, "wb") as f:
            f.write(b"\xff\xd8falsa")
        return True

    return sincronizar_anuncios(
        db, "2026-09-01", "2026-09-30",
        traer_ads=lambda: list(_ADS if ads is None else ads),
        traer_insights=lambda d, h: list(_INSIGHTS if insights is None else insights),
        bajar=kw.pop("bajar", bajar), **kw)


def _fila(db, ad_id):
    conn = _connect(db)
    try:
        return conn.execute("SELECT * FROM meta_ads WHERE ad_id = ?",
                            (ad_id,)).fetchone()
    finally:
        conn.close()


# ── Los anuncios ───────────────────────────────────────────────────────────

def test_guarda_todos_los_anuncios(db):
    r = _correr(db)
    assert r["anuncios"] == 3
    assert _fila(db, "120253602403650249")["ad_name"] == "Web hace ganar - RMKTG"


def test_guarda_tambien_los_apagados(db):
    """El estado se guarda, no se filtra. Quien decide que mostrar es el panel:
    si aca se tiraran los apagados, apagar un anuncio lo borraria del historial
    y el gasto que hizo dejaria de cuadrar."""
    _correr(db)
    assert _fila(db, "120243368449890249")["effective_status"] == "PAUSED"


def test_guarda_campana_conjunto_y_texto(db):
    _correr(db)
    f = _fila(db, "120253602403650249")
    assert f["campaign_name"] == "Brand - Set26"
    assert f["adset_name"] == "RMKTG"
    assert f["titulo"].startswith("Agencia de desarrollo")
    assert "visitantes" in f["cuerpo"]


def test_correrlo_dos_veces_no_duplica(db):
    _correr(db)
    _correr(db)
    conn = _connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) c FROM meta_ads").fetchone()["c"] == 3
    finally:
        conn.close()


def test_un_anuncio_que_cambio_de_estado_se_actualiza(db):
    _correr(db)
    apagado = [dict(a) for a in _ADS]
    apagado[0]["effective_status"] = "PAUSED"
    _correr(db, ads=apagado)
    assert _fila(db, "120253602403650249")["effective_status"] == "PAUSED"


def test_un_anuncio_sin_id_no_rompe_el_sync(db):
    """Sin id no hay con que emparejarlo ni contra que hacer upsert."""
    r = _correr(db, ads=[{"name": "roto"}] + _ADS)
    assert r["anuncios"] == 3


# ── Las fotos ──────────────────────────────────────────────────────────────

def test_baja_la_imagen_una_sola_vez(db):
    """La URL que da Meta viene firmada y caduca, asi que el archivo se guarda.
    Y se baja una vez: en la segunda corrida ya esta y no se vuelve a pedir."""
    bajadas = []
    _correr(db, bajadas=bajadas)
    assert len(bajadas) == 2          # los dos con image_url, el video no
    _correr(db, bajadas=bajadas)
    assert len(bajadas) == 2          # no bajo nada nuevo


def test_el_archivo_queda_al_lado_de_la_base(db):
    _correr(db)
    ruta = _fila(db, "120253602403650249")["imagen_archivo"]
    assert ruta and os.path.exists(ruta)
    assert os.path.basename(os.path.dirname(ruta)) == "creativos"


def test_un_anuncio_de_video_se_guarda_sin_foto(db):
    """Meta no da el still de los videos con los permisos que tiene la app.
    Guardar el anuncio igual es lo correcto: el gasto existe y hay que verlo."""
    _correr(db)
    f = _fila(db, "120253602403650250")
    assert f is not None
    assert f["imagen_archivo"] is None
    assert f["object_type"] == "VIDEO"


def test_si_la_bajada_falla_el_sync_sigue(db):
    """Una imagen que no baja no puede tumbar el gasto de toda la cuenta."""
    def romper(url, destino):
        raise OSError("se cayo la red")

    r = _correr(db, bajar=romper)
    assert r["anuncios"] == 3
    assert _fila(db, "120253602403650249")["imagen_archivo"] is None


# ── Los insights por anuncio ───────────────────────────────────────────────

def test_guarda_el_gasto_por_dia_y_por_anuncio(db):
    _correr(db)
    conn = _connect(db)
    try:
        filas = conn.execute(
            "SELECT * FROM meta_ad_insights ORDER BY date").fetchall()
        assert len(filas) == 2
        assert filas[0]["spend"] == 12.34
        assert filas[0]["leads"] == 2
        assert filas[1]["leads"] == 0
    finally:
        conn.close()


def test_resincronizar_un_dia_lo_actualiza_no_lo_duplica(db):
    """Meta corrige cifras hacia atras: la ultima corrida manda."""
    _correr(db)
    corregido = [dict(_INSIGHTS[0], spend="20.00")]
    _correr(db, insights=corregido)
    conn = _connect(db)
    try:
        f = conn.execute(
            "SELECT spend FROM meta_ad_insights WHERE date='2026-09-01'"
        ).fetchone()
        assert f["spend"] == 20.0
        assert conn.execute(
            "SELECT COUNT(*) c FROM meta_ad_insights").fetchone()["c"] == 2
    finally:
        conn.close()


def test_los_leads_salen_de_las_acciones(db):
    """No son un campo: hay que buscarlos entre `actions`, igual que en el sync
    de campanas. Si el action_type no esta en la lista, el CPL da None y
    pareceria un bug del codigo."""
    otro = [dict(_INSIGHTS[0], actions=[
        {"action_type": "onsite_conversion.lead_grouped", "value": "7"}])]
    _correr(db, insights=otro)
    conn = _connect(db)
    try:
        assert conn.execute(
            "SELECT leads FROM meta_ad_insights").fetchone()["leads"] == 7
    finally:
        conn.close()


def test_sin_credenciales_avisa_y_no_revienta(db):
    """El resto del modulo tiene que seguir andando sin las credenciales de
    ads, igual que el sync de campanas."""
    import services.meta_anuncios as m

    guardado = dict(os.environ)
    try:
        os.environ.pop("META_ADS_TOKEN", None)
        os.environ.pop("META_AD_ACCOUNT_ID", None)
        r = m.sincronizar_anuncios(db, "2026-09-01", "2026-09-30")
        assert r["salteado"] == "sin_credenciales"
    finally:
        os.environ.clear()
        os.environ.update(guardado)


# ── Cuando Meta contesta mal ───────────────────────────────────────────────
#
# Paso de verdad en la primera corrida contra la cuenta real: los anuncios se
# guardaron y los insights dieron 403 por exceso de llamadas. El sync devolvio
# {"anuncios": 140, "filas": 0} y eso se lee como "no hubo gasto", que es lo
# contrario de lo que habia pasado.

def test_si_el_gasto_no_se_puede_traer_el_resultado_lo_dice(db):
    """"filas: 0" a secas se lee como "no hubo gasto". Hay que poder
    distinguir "fallo" de "no habia nada"."""
    from services.meta_anuncios import _ErrorDeMeta

    def romper(desde, hasta):
        raise _ErrorDeMeta("insights 403: code=17 User request limit reached")

    r = sincronizar_anuncios(
        db, "2026-09-01", "2026-09-30",
        traer_ads=lambda: list(_ADS),
        traer_insights=romper,
        bajar=lambda u, d: False)
    assert "error_gasto" in r
    assert "403" in r["error_gasto"]
    assert r["filas"] == 0


def test_aunque_falle_el_gasto_los_anuncios_quedan_guardados(db):
    """Lo que ya se trajo no se tira: la proxima corrida solo tiene que
    reintentar el gasto, no volver a bajar 67 imagenes."""
    from services.meta_anuncios import _ErrorDeMeta

    def romper(desde, hasta):
        raise _ErrorDeMeta("insights 403")

    sincronizar_anuncios(db, "2026-09-01", "2026-09-30",
                         traer_ads=lambda: list(_ADS),
                         traer_insights=romper,
                         bajar=lambda u, d: False)
    assert _fila(db, "120253602403650249") is not None


def test_una_corrida_sana_no_trae_error_gasto(db):
    """Para que el campo signifique algo tiene que estar ausente cuando todo
    salio bien."""
    assert "error_gasto" not in _correr(db)


def test_el_motivo_recorta_lo_que_dijo_meta(db):
    """Un 403 puede ser falta de permiso o exceso de llamadas, y son dos
    problemas distintos. Con el numero solo hay que salir a averiguarlo."""
    from services.meta_anuncios import _motivo

    class _Falsa:
        def json(self):
            return {"error": {"code": 17, "message": "User request limit reached"}}

    assert "code=17" in _motivo(_Falsa())
    assert "limit reached" in _motivo(_Falsa())


# ── El relleno hacia atras ─────────────────────────────────────────────────
#
# El cron trae 7 dias. Los meses viejos se rellenan a mano, de a uno, con
# tope propio. Ninguno de estos tests sale a la red.

def _borrar_marca(db):
    conn = _connect(db)
    try:
        conn.execute("DELETE FROM corridas")
        conn.commit()
    finally:
        conn.close()


def _insights_de_mayo(pedidos):
    def traer(desde, hasta):
        pedidos.append((desde, hasta))
        return [
            {"date_start": "2026-05-01", "ad_id": "120243368449890249",
             "ad_name": "12/03 - Hiciste lo más difícil", "spend": "4.00",
             "account_currency": "USD", "impressions": "300", "clicks": "5",
             "reach": "250", "actions": [{"action_type": "lead", "value": "1"}]},
            {"date_start": "2026-05-31", "ad_id": "999", "ad_name": "Vieja archivada",
             "spend": "6.00", "account_currency": "USD", "impressions": "400",
             "clicks": "7", "reach": "300", "actions": []},
        ]
    return traer


def _contar(db, tabla):
    conn = _connect(db)
    try:
        return conn.execute(f"SELECT COUNT(*) c FROM {tabla}").fetchone()["c"]
    finally:
        conn.close()


def test_el_relleno_trae_el_mes_entero_y_es_idempotente(db):
    from services.meta_anuncios import rellenar_mes

    pedidos = []
    r = rellenar_mes(db, "2026-05", hoy="2026-09-14",
                     traer_insights=_insights_de_mayo(pedidos))
    assert pedidos == [("2026-05-01", "2026-05-31")]
    assert r["filas"] == 2
    _borrar_marca(db)
    rellenar_mes(db, "2026-05", hoy="2026-09-14",
                 traer_insights=_insights_de_mayo(pedidos))
    assert _contar(db, "meta_ad_insights") == 2


def test_el_relleno_del_mes_en_curso_pide_hasta_hoy(db):
    from services.meta_anuncios import rellenar_mes

    pedidos = []
    rellenar_mes(db, "2026-09", hoy="2026-09-14",
                 traer_insights=_insights_de_mayo(pedidos))
    assert pedidos == [("2026-09-01", "2026-09-14")]


@pytest.mark.parametrize("mes", ["2026-10", "2023-07"])
def test_el_relleno_no_pide_el_futuro_ni_lo_que_meta_ya_no_tiene(db, mes):
    from services.meta_anuncios import rellenar_mes

    pedidos = []
    with pytest.raises(ValueError):
        rellenar_mes(db, mes, hoy="2026-09-14",
                     traer_insights=_insights_de_mayo(pedidos))
    assert pedidos == []


def test_el_relleno_respeta_la_pausa_entre_llamadas(db):
    from services.meta_anuncios import ReintentarMasTarde, rellenar_mes

    pedidos = []
    rellenar_mes(db, "2026-05", hoy="2026-09-14",
                 traer_insights=_insights_de_mayo(pedidos))
    with pytest.raises(ReintentarMasTarde):
        rellenar_mes(db, "2026-06", hoy="2026-09-14",
                     traer_insights=_insights_de_mayo(pedidos))
    assert len(pedidos) == 1, "la segunda no llego a Meta"


def test_si_meta_falla_la_pausa_cuenta_igual(db):
    """Reintentar enseguida despues de un code 17 es justo lo que lo empeora."""
    from services.meta_anuncios import (ReintentarMasTarde, _ErrorDeMeta,
                                        rellenar_mes)

    def romper(desde, hasta):
        raise _ErrorDeMeta("insights 403: code=17 User request limit reached")

    r = rellenar_mes(db, "2026-05", hoy="2026-09-14", traer_insights=romper)
    assert "code=17" in r["error_gasto"] and r["filas"] == 0
    with pytest.raises(ReintentarMasTarde):
        rellenar_mes(db, "2026-05", hoy="2026-09-14", traer_insights=romper)


def test_el_relleno_crea_la_ficha_que_falta_sin_pisar_la_que_hay(db):
    from services.meta_anuncios import rellenar_mes

    _correr(db)                        # la ficha real de 120243368449890249
    rellenar_mes(db, "2026-05", hoy="2026-09-14",
                 traer_insights=_insights_de_mayo([]))
    real = _fila(db, "120243368449890249")
    assert real["effective_status"] == "PAUSED"
    assert real["imagen_archivo"] is not None
    assert _fila(db, "999")["ad_name"] == "Vieja archivada"


def test_el_relleno_sin_credenciales_no_marca_la_pausa(db, monkeypatch):
    from services.meta_anuncios import rellenar_mes

    monkeypatch.delenv("META_ADS_TOKEN", raising=False)
    monkeypatch.delenv("META_AD_ACCOUNT_ID", raising=False)
    assert rellenar_mes(db, "2026-05", hoy="2026-09-14")["salteado"] == "sin_credenciales"
    assert _contar(db, "corridas") == 0


def test_las_paginas_van_con_pausa_y_con_tope(monkeypatch):
    """Cada pagina es una llamada. Una respuesta que pagina sin fin no puede
    convertirse en cien llamadas seguidas."""
    import requests

    import services.meta_anuncios as m

    monkeypatch.setenv("META_ADS_TOKEN", "t")
    monkeypatch.setenv("META_AD_ACCOUNT_ID", "act_1")
    llamadas, pausas = [], []

    class _Pagina:
        ok = True
        status_code = 200

        def json(self):
            return {"data": [{"ad_id": "1"}], "paging": {"next": "https://siguiente"}}

    def get(url, params=None, timeout=None):
        llamadas.append(url)
        return _Pagina()

    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(m._ErrorDeMeta):
        m._traer_insights("2026-05-01", "2026-05-31", pausa=20, max_paginas=3,
                          dormir=pausas.append)
    assert len(llamadas) == 3
    assert pausas == [20, 20]


def test_si_la_respuesta_no_es_json_el_motivo_no_revienta(db):
    """Un 502 del proxy de Meta devuelve HTML. Si `_motivo` reventara ahi, el
    error que se estaba reportando se perderia detras de otro error."""
    from services.meta_anuncios import _motivo

    class _Html:
        text = "<html>502 Bad Gateway</html>"

        def json(self):
            raise ValueError("no es json")

    assert "502" in _motivo(_Html())
