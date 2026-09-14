"""Los anuncios que estan corriendo, con su rendimiento y que hacer con cada uno.

Juan pidio "ver fotos de la pauta que esta corriendo y como va funcionando cada
pauta, cuanto va siendo el costo por lead y una recomendacion tuya si cambiarla
ajustarla o que".

La recomendacion la calculan reglas, no un modelo: la IA esta apagada a pedido
de Juan, y ademas una regla se puede discutir. Cada una cita los numeros que la
sostienen, igual que los hallazgos del panel.

Lo que mas importa de este archivo son los umbrales. Una recomendacion que se
dispara con dos leads no es una recomendacion, es ruido con tono de autoridad —
ya paso una vez en `hallazgos.py`, donde "se traba en cierres" salia siempre
porque se comparaba contra una campana de un solo lead.
"""

import pytest

from database import _connect, init_db
from services.anuncios import (GASTO_MINIMO_PARA_OPINAR, LEADS_MINIMOS,
                               anuncios_en_curso)


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def _anuncio(db, ad_id, nombre="Anuncio", estado="ACTIVE", campana="UY",
             imagen="/data/creativos/x.jpg"):
    conn = _connect(db)
    try:
        conn.execute(
            "INSERT INTO meta_ads (ad_id, ad_name, campaign_id, campaign_name, "
            "adset_name, effective_status, object_type, titulo, cuerpo, "
            "imagen_archivo) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ad_id, nombre, "c1", campana, "conjunto", estado, "SHARE",
             "Un titulo", "Un cuerpo", imagen))
        conn.commit()
    finally:
        conn.close()


def _gasto(db, ad_id, fecha, spend, leads=0, impresiones=1000, clics=30):
    conn = _connect(db)
    try:
        conn.execute(
            "INSERT INTO meta_ad_insights (date, ad_id, spend, currency, "
            "impressions, clicks, reach, leads) VALUES (?,?,?,?,?,?,?,?)",
            (fecha, ad_id, spend, "USD", impresiones, clics,
             int(impresiones * 0.7), leads))
        conn.commit()
    finally:
        conn.close()


def _uno(db, ad_id, **kw):
    todos = anuncios_en_curso(db, "2026-03-01", "2026-12-31", **kw)
    iguales = [a for a in todos if a["ad_id"] == ad_id]
    assert iguales, f"no esta {ad_id}: {[a['ad_id'] for a in todos]}"
    return iguales[0]


# ── Que anuncios entran ────────────────────────────────────────────────────

def test_solo_trae_los_que_estan_corriendo(db):
    """Juan: "las publicaciones que se estan pautando ahora". Un anuncio
    apagado no es una decision que se pueda tomar hoy."""
    _anuncio(db, "1", estado="ACTIVE")
    _anuncio(db, "2", estado="PAUSED")
    _anuncio(db, "3", estado="ADSET_PAUSED")
    _anuncio(db, "4", estado="CAMPAIGN_PAUSED")
    for a in ("1", "2", "3", "4"):
        _gasto(db, a, "2026-09-01", 50.0, leads=4)
    ids = [a["ad_id"] for a in anuncios_en_curso(db, "2026-03-01", "2026-12-31")]
    assert ids == ["1"]


def test_un_activo_sin_gasto_en_el_periodo_no_aparece(db):
    """Esta prendido pero no corrio: no hay nada que mirar ni que decidir."""
    _anuncio(db, "1")
    ids = [a["ad_id"] for a in anuncios_en_curso(db, "2026-03-01", "2026-12-31")]
    assert ids == []


def test_vienen_ordenados_por_gasto(db):
    """El que se lleva la plata primero: es donde una decision mueve mas."""
    for i, plata in enumerate([30.0, 300.0, 100.0], start=1):
        _anuncio(db, str(i))
        _gasto(db, str(i), "2026-09-01", plata, leads=3)
    ids = [a["ad_id"] for a in anuncios_en_curso(db, "2026-03-01", "2026-12-31")]
    assert ids == ["2", "3", "1"]


def test_el_periodo_recorta(db):
    _anuncio(db, "1")
    _gasto(db, "1", "2026-05-01", 500.0, leads=10)
    _gasto(db, "1", "2026-09-01", 100.0, leads=2)
    a = anuncios_en_curso(db, "2026-09-01", "2026-09-30")[0]
    assert a["gasto"] == 100.0
    assert a["leads"] == 2


# ── Los numeros de cada uno ────────────────────────────────────────────────

def test_suma_el_gasto_y_los_leads_del_periodo(db):
    _anuncio(db, "1")
    _gasto(db, "1", "2026-09-01", 60.0, leads=3)
    _gasto(db, "1", "2026-09-02", 40.0, leads=2)
    a = _uno(db, "1")
    assert a["gasto"] == 100.0
    assert a["leads"] == 5
    assert a["cpl"] == 20.0


def test_sin_leads_el_cpl_no_es_cero(db):
    """Cero seria "sale gratis". Es el anuncio mas caro que hay."""
    _anuncio(db, "1")
    _gasto(db, "1", "2026-09-01", 100.0, leads=0)
    assert _uno(db, "1")["cpl"] is None


def test_el_ctr_sale_de_clics_sobre_impresiones(db):
    _anuncio(db, "1")
    _gasto(db, "1", "2026-09-01", 50.0, leads=2, impresiones=2000, clics=40)
    assert _uno(db, "1")["ctr"] == 0.02


def test_la_tasa_de_lead_dice_cuantos_de_los_que_clickearon_dejaron_datos(db):
    """Un CTR alto con tasa de lead baja es un anuncio que promete algo que el
    formulario no cumple: la gente entra y se va."""
    _anuncio(db, "1")
    _gasto(db, "1", "2026-09-01", 50.0, leads=5, impresiones=1000, clics=100)
    assert _uno(db, "1")["tasa_lead"] == 0.05


def test_trae_desde_cuando_viene_gastando(db):
    """"cuanto se va pautando desde tal fecha": la fecha es la del anuncio, no
    la del periodo, porque cada uno arranco cuando arranco."""
    _anuncio(db, "1")
    _gasto(db, "1", "2026-07-14", 10.0, leads=1)
    _gasto(db, "1", "2026-09-01", 10.0, leads=1)
    assert _uno(db, "1")["desde"] == "2026-07-14"


def test_trae_la_foto_y_el_texto(db):
    _anuncio(db, "1", imagen="/data/creativos/1.jpg")
    _gasto(db, "1", "2026-09-01", 50.0, leads=2)
    a = _uno(db, "1")
    assert a["imagen_archivo"] == "/data/creativos/1.jpg"
    assert a["titulo"] == "Un titulo"
    assert a["campana"] == "UY"


# ── Las recomendaciones ────────────────────────────────────────────────────

def _reco(db, ad_id, **kw):
    return _uno(db, ad_id, **kw)["recomendacion"]


def test_gasto_de_sobra_y_ningun_lead_se_apaga(db):
    _anuncio(db, "1")
    _gasto(db, "1", "2026-09-01", GASTO_MINIMO_PARA_OPINAR + 50, leads=0)
    r = _reco(db, "1")
    assert r["accion"] == "apagar"
    assert r["metricas_citadas"], "una recomendacion sin numeros no se audita"


def test_poco_gasto_y_ningun_lead_todavia_no_se_opina(db):
    """El error de calibracion clasico: con 10 dolares gastados, cero leads no
    dice nada. Apagarlo por eso seria apagar un anuncio que no se probo."""
    _anuncio(db, "1")
    _gasto(db, "1", "2026-09-01", 5.0, leads=0)
    assert _reco(db, "1")["accion"] == "esperar"


def test_el_mas_caro_contra_la_mediana_se_ajusta(db):
    """Tres baratos y uno que sale el triple. Se compara contra la mediana de
    los que estan corriendo, no contra un numero fijo: lo que es caro depende
    de la cuenta."""
    for i in (1, 2, 3):
        _anuncio(db, str(i))
        _gasto(db, str(i), "2026-09-01", 100.0, leads=10)   # CPL 10
    _anuncio(db, "4")
    _gasto(db, "4", "2026-09-01", 300.0, leads=10)          # CPL 30
    assert _reco(db, "4")["accion"] == "ajustar"


def test_el_mas_barato_se_le_sube_el_presupuesto(db):
    for i in (1, 2, 3):
        _anuncio(db, str(i))
        _gasto(db, str(i), "2026-09-01", 200.0, leads=10)   # CPL 20
    _anuncio(db, "4")
    _gasto(db, "4", "2026-09-01", 100.0, leads=20)          # CPL 5
    assert _reco(db, "4")["accion"] == "subir"


def test_con_pocos_leads_no_se_opina_aunque_el_cpl_pinte_mal(db):
    """Un anuncio con 1 lead a 40 dolares puede ser mala suerte. Recomendar
    sobre eso es recomendar sobre ruido."""
    for i in (1, 2, 3):
        _anuncio(db, str(i))
        _gasto(db, str(i), "2026-09-01", 100.0, leads=10)
    _anuncio(db, "4")
    _gasto(db, "4", "2026-09-01", 40.0, leads=1)
    assert _uno(db, "4")["leads"] < LEADS_MINIMOS
    assert _reco(db, "4")["accion"] == "esperar"


def test_un_anuncio_que_dejo_de_enganchar_se_marca_como_quemado(db):
    """El CTR de los ultimos dias contra el del propio anuncio desde siempre.

    Es la senal de que la gente ya lo vio: sigue apareciendo y dejo de
    interesar. Se compara contra si mismo y no contra los demas, porque un
    anuncio de video y uno de imagen tienen CTR distintos por naturaleza.
    """
    _anuncio(db, "1")
    for d in range(1, 21):                       # 20 dias buenos
        _gasto(db, "1", f"2026-08-{d:02d}", 10.0, leads=1,
               impresiones=1000, clics=50)       # CTR 5%
    for d in range(7, 14):                       # la ultima semana, mucho peor
        _gasto(db, "1", f"2026-09-{d:02d}", 10.0, leads=1,
               impresiones=1000, clics=10)       # CTR 1%
    r = _uno(db, "1", hoy="2026-09-13")["recomendacion"]
    assert r["accion"] == "renovar"


def test_uno_que_anda_bien_se_deja(db):
    for i in (1, 2, 3):
        _anuncio(db, str(i))
        _gasto(db, str(i), "2026-09-01", 100.0, leads=10)
    assert _reco(db, "2")["accion"] == "dejar"


def test_toda_recomendacion_trae_texto_y_numeros(db):
    _anuncio(db, "1")
    _gasto(db, "1", "2026-09-01", 100.0, leads=8)
    r = _reco(db, "1")
    assert r["texto"] and isinstance(r["texto"], str)
    assert r["accion"] in ("apagar", "ajustar", "subir", "renovar", "esperar",
                           "dejar")


def test_apagar_gana_sobre_las_demas(db):
    """Un anuncio que gasto de sobra y no trajo a nadie no necesita que le
    ajusten el presupuesto: necesita que lo apaguen. Si dos reglas aplican,
    manda la mas grave."""
    for i in (1, 2, 3):
        _anuncio(db, str(i))
        _gasto(db, str(i), "2026-09-01", 100.0, leads=10)
    _anuncio(db, "4")
    _gasto(db, "4", "2026-09-01", GASTO_MINIMO_PARA_OPINAR + 200, leads=0)
    assert _reco(db, "4")["accion"] == "apagar"


# ── El total de la seccion ─────────────────────────────────────────────────

def test_el_resumen_dice_cuanto_se_lleva_gastado_y_desde_cuando(db):
    from services.anuncios import resumen_en_curso

    _anuncio(db, "1")
    _anuncio(db, "2")
    _gasto(db, "1", "2026-08-20", 100.0, leads=5)
    _gasto(db, "2", "2026-09-01", 300.0, leads=10)
    r = resumen_en_curso(db, "2026-03-01", "2026-12-31")
    assert r["gasto"] == 400.0
    assert r["leads"] == 15
    assert r["cpl"] == round(400 / 15, 2)
    assert r["desde"] == "2026-08-20"
    assert r["anuncios"] == 2


def test_sin_anuncios_corriendo_el_resumen_no_inventa(db):
    from services.anuncios import resumen_en_curso

    r = resumen_en_curso(db, "2026-03-01", "2026-12-31")
    assert r["anuncios"] == 0
    assert r["cpl"] is None
    assert r["desde"] is None
