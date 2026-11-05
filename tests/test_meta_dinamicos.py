"""El desglose por pieza de los anuncios dinamicos (pedido de Juan, 5/11).

El de Argentina es un solo anuncio con varias imagenes y textos adentro que
Meta combina. Mirado como anuncio es una fila; lo que se decide es que pieza
anda. Mismo patron que test_meta_anuncios: la API se inyecta.

La forma de las filas del desglose es la de la documentacion de Meta para
`breakdowns=image_asset|body_asset|title_asset`: la pieza viene como objeto con
su `id` y su `text` o su `url`.
"""

import os

import pytest

from database import _connect, init_db
from services.anuncios import desglose_dinamico, piezas_del_mes
from services.meta_anuncios import es_dinamico, sincronizar_anuncios

AD_DIN = "120240000000000001"
AD_COMUN = "120240000000000002"


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


_ADS = [
    {"id": AD_DIN, "name": "New Leads ad", "effective_status": "ACTIVE",
     "campaign": {"id": "c9", "name": "Leads - ARG - Nov26"},
     "adset": {"name": "ARG - Leads - Nov26", "is_dynamic_creative": True},
     "creative": {"id": "cr9", "object_type": "SHARE"}},
    {"id": AD_COMUN, "name": "Feed 1", "effective_status": "ACTIVE",
     "campaign": {"id": "c1", "name": "Leads - Nov26 - Zafra - CL"},
     "adset": {"name": "Abierto"},
     "creative": {"id": "cr1", "object_type": "SHARE"}},
]

_INSIGHTS = [
    {"date_start": "2026-11-05", "ad_id": AD_DIN, "spend": "10", "impressions": "1000",
     "clicks": "20", "account_currency": "USD",
     "actions": [{"action_type": "lead", "value": "2"}]},
    {"date_start": "2026-11-05", "ad_id": AD_COMUN, "spend": "5", "impressions": "400",
     "clicks": "4", "account_currency": "USD", "actions": []},
]

_DESGLOSE = {
    "image_asset": [
        {"date_start": "2026-11-05", "spend": "7", "impressions": "700", "clicks": "15",
         "actions": [{"action_type": "lead", "value": "2"}],
         "image_asset": {"id": "111", "hash": "h1", "url": "https://x/img1.jpg",
                         "name": "local.jpg"}},
        {"date_start": "2026-11-05", "spend": "3", "impressions": "300", "clicks": "5",
         "image_asset": {"id": "222", "hash": "h2", "url": "https://x/img2.jpg"}},
    ],
    "body_asset": [
        {"date_start": "2026-11-05", "spend": "6", "impressions": "600", "clicks": "12",
         "actions": [{"action_type": "lead", "value": "1"}],
         "body_asset": {"id": "333", "text": "Tu negocio, digital"}},
        {"date_start": "2026-11-05", "spend": "4", "impressions": "400", "clicks": "8",
         "actions": [{"action_type": "lead", "value": "1"}],
         "body_asset": {"id": "444", "text": "Vendé más"}},
    ],
    "title_asset": [],
    "video_asset": [],
}


def _correr(db, desglose=None, llamadas=None):
    llamadas = [] if llamadas is None else llamadas

    def traer_desglose(ad_id, tipo, desde, hasta):
        llamadas.append((ad_id, tipo))
        return list((desglose or _DESGLOSE).get(tipo, []))

    def bajar(url, destino):
        with open(destino, "wb") as f:
            f.write(b"\xff\xd8falsa")
        return True

    return sincronizar_anuncios(
        db, "2026-11-01", "2026-11-05",
        traer_ads=lambda: list(_ADS), traer_insights=lambda d, h: list(_INSIGHTS),
        bajar=bajar, traer_desglose=traer_desglose)


def test_reconoce_dinamico_por_conjunto_o_por_creativo():
    assert es_dinamico({"adset": {"is_dynamic_creative": True}})
    assert es_dinamico({"creative": {"asset_feed_spec": {"images": [{}]}}})
    assert not es_dinamico({"adset": {"name": "x"}, "creative": {}})


def test_marca_el_anuncio_y_solo_desglosa_los_dinamicos(db):
    llamadas = []
    r = _correr(db, llamadas=llamadas)
    assert r["dinamicos"] == 1
    assert {a for a, _ in llamadas} == {AD_DIN}
    conn = _connect(db)
    try:
        marcas = dict(conn.execute("SELECT ad_id, dinamico FROM meta_ads").fetchall())
    finally:
        conn.close()
    assert marcas == {AD_DIN: 1, AD_COMUN: 0}


def test_guarda_piezas_con_su_gasto_y_baja_las_imagenes(db):
    r = _correr(db)
    assert r["filas_piezas"] == 4
    conn = _connect(db)
    try:
        img = conn.execute("SELECT * FROM meta_ad_piezas WHERE pieza_id = '111'").fetchone()
        txt = conn.execute("SELECT * FROM meta_ad_piezas WHERE pieza_id = '333'").fetchone()
    finally:
        conn.close()
    assert img["tipo"] == "imagen" and os.path.exists(img["imagen_archivo"])
    assert txt["tipo"] == "texto" and txt["texto"] == "Tu negocio, digital"
    assert txt["imagen_archivo"] is None


def test_correrlo_dos_veces_no_duplica(db):
    _correr(db)
    _correr(db)
    conn = _connect(db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM meta_ad_pieza_insights").fetchone()[0]
    finally:
        conn.close()
    assert n == 4


def test_si_falla_un_desglose_el_gasto_del_anuncio_queda(db):
    from services.meta_anuncios import _ErrorDeMeta

    def roto(*_):
        raise _ErrorDeMeta("code=17 rate limit")

    r = sincronizar_anuncios(
        db, "2026-11-01", "2026-11-05",
        traer_ads=lambda: list(_ADS), traer_insights=lambda d, h: list(_INSIGHTS),
        bajar=lambda u, d: False, traer_desglose=roto)
    assert r["filas"] == 2 and r["filas_piezas"] == 0


def test_desglose_por_tipo_sin_mezclar(db):
    _correr(db)
    d = desglose_dinamico(db, AD_DIN, "2026-11")
    assert set(d["tipos"]) == {"imagen", "texto"}
    imgs = d["tipos"]["imagen"]
    assert [p["pieza_id"] for p in imgs] == ["111", "222"]   # por gasto
    assert imgs[0]["cpl"] == 3.5 and imgs[1]["cpl"] is None   # sin leads, sin costo
    assert imgs[0]["tiene_imagen"]
    # Cada tipo suma el gasto del anuncio por su lado.
    assert sum(p["gasto"] for p in imgs) == 10
    assert sum(p["gasto"] for p in d["tipos"]["texto"]) == 10


def test_desglose_de_otro_mes_viene_vacio(db):
    _correr(db)
    assert desglose_dinamico(db, AD_DIN, "2026-10")["tipos"] == {}


def test_la_tarjeta_dice_si_es_dinamico(db):
    _correr(db)
    d = piezas_del_mes(db, "2026-11", hoy="2026-11-05")
    por_id = {p["ad_id"]: p for p in d["activas"]}
    assert por_id[AD_DIN]["dinamico"] is True
    assert por_id[AD_COMUN]["dinamico"] is False
