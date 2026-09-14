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
from services.anuncios import (LEADS_MINIMOS, OPORTUNIDAD_PARA_JUZGAR,
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
#
# LA CALIBRACION ES EL TODO, Y SE COMPROBO CONTRA LA CUENTA REAL.
#
# La primera version media la evidencia en leads: "menos de 5 leads, no opino".
# Corrida contra los 19 anuncios que estaban corriendo dejo 17 en "todavia no
# alcanza para opinar", y a uno que habia gastado 26 centavos le decia que
# "faltan unos 5 leads". Una seccion que no opina de nada no sirve para nada.
#
# Ahora la evidencia se mide en `oportunidad`: cuantos leads deberia haber
# comprado lo que gasto, al costo habitual de la cuenta. Los tests de abajo
# fijan los dos extremos —el que gasto casi nada y el que gasto de sobra— que
# son los que la version anterior confundia.

def _reco(db, ad_id, **kw):
    return _uno(db, ad_id, **kw)["recomendacion"]


def _mercado(db, cuantos=3, gasto=100.0, leads=10):
    """Tres anuncios normales, para que exista una mediana contra la que medir.

    Sin esto no hay referencia y las reglas de precio no se pueden disparar:
    cualquier test de "esta caro" pasaria o fallaria por el motivo equivocado.
    """
    for i in range(1, cuantos + 1):
        _anuncio(db, f"m{i}")
        _gasto(db, f"m{i}", "2026-09-01", gasto, leads=leads)


def test_gasto_de_sobra_y_ningun_lead_se_apaga(db):
    _mercado(db)                       # mediana de CPL = 10
    _anuncio(db, "x")
    _gasto(db, "x", "2026-09-01", 10 * OPORTUNIDAD_PARA_JUZGAR + 5, leads=0)
    r = _reco(db, "x")
    assert r["accion"] == "apagar"
    assert r["metricas_citadas"], "una recomendacion sin numeros no se audita"


def test_el_que_casi_no_gasto_no_recibe_un_sermon(db):
    """El caso que rompio la version anterior. Con 26 centavos gastados y la
    cuenta a 16 dolares el lead, decir "faltan 5 leads" es ruido con tono de
    autoridad: todavia no gasto ni lo que cuesta un lead."""
    _mercado(db)                       # mediana = 10
    _anuncio(db, "x")
    _gasto(db, "x", "2026-09-01", 0.26, leads=0)
    r = _reco(db, "x")
    assert r["accion"] == "esperar"
    assert "arranca" in r["texto"].lower()
    assert "5 leads" not in r["texto"]


def test_entre_medio_dice_que_todavia_no_alcanza_pero_sin_inventar(db):
    """Gasto mas de un lead y menos de tres: no se puede juzgar, pero tampoco
    es "recien arranca"."""
    _mercado(db)                       # mediana = 10
    _anuncio(db, "x")
    _gasto(db, "x", "2026-09-01", 18.0, leads=0)   # oportunidad 1,8
    r = _reco(db, "x")
    assert r["accion"] == "esperar"
    assert "arranca" not in r["texto"].lower()


def test_el_mas_caro_contra_la_mediana_se_ajusta(db):
    """Se compara contra la mediana de los que estan corriendo, no contra un
    numero fijo: lo que es caro depende de la cuenta."""
    _mercado(db)                       # mediana = 10
    _anuncio(db, "x")
    _gasto(db, "x", "2026-09-01", 300.0, leads=10)   # CPL 30
    assert _reco(db, "x")["accion"] == "ajustar"


def test_se_puede_decir_que_esta_caro_con_pocos_leads_si_gasto_mucho(db):
    """El otro caso que la version anterior tapaba: 1 lead a 44 dolares con la
    mediana en 10 es informacion, no ruido — la plata gastada ya alcanzaba para
    cuatro leads. Antes quedaba mudo porque miraba solo la cantidad de leads."""
    _mercado(db)                       # mediana = 10
    _anuncio(db, "x")
    _gasto(db, "x", "2026-09-01", 44.0, leads=1)
    assert _uno(db, "x")["leads"] < LEADS_MINIMOS
    assert _reco(db, "x")["accion"] == "ajustar"


def test_para_subir_el_presupuesto_si_se_piden_leads_de_verdad(db):
    """Asimetrico a proposito: recomendar poner MAS plata sobre poca evidencia
    es el mas caro de los dos errores."""
    _mercado(db, gasto=200.0, leads=10)              # mediana = 20
    _anuncio(db, "x")
    _gasto(db, "x", "2026-09-01", 8.0, leads=2)      # CPL 4, pero solo 2 leads
    assert _reco(db, "x")["accion"] != "subir"


def test_el_mas_barato_con_muestra_se_le_sube_el_presupuesto(db):
    _mercado(db, gasto=200.0, leads=10)              # mediana = 20
    _anuncio(db, "x")
    _gasto(db, "x", "2026-09-01", 100.0, leads=20)   # CPL 5
    assert _reco(db, "x")["accion"] == "subir"


def test_un_anuncio_que_dejo_de_enganchar_se_marca_como_quemado(db):
    """El CTR de los ultimos dias contra el del propio anuncio desde siempre.

    Es la senal de que la gente ya lo vio: sigue apareciendo y dejo de
    interesar. Se compara contra si mismo y no contra los demas, porque un
    anuncio de video y uno de imagen tienen CTR distintos por naturaleza.
    """
    _mercado(db)
    _anuncio(db, "x")
    for d in range(1, 21):                       # 20 dias buenos
        _gasto(db, "x", f"2026-08-{d:02d}", 10.0, leads=1,
               impresiones=1000, clics=50)       # CTR 5%
    for d in range(7, 14):                       # la ultima semana, mucho peor
        _gasto(db, "x", f"2026-09-{d:02d}", 10.0, leads=1,
               impresiones=1000, clics=10)       # CTR 1%
    assert _uno(db, "x", hoy="2026-09-13")["recomendacion"]["accion"] == "renovar"


def test_uno_que_anda_bien_se_deja(db):
    _mercado(db)
    assert _reco(db, "m2")["accion"] == "dejar"


def test_apagar_gana_sobre_las_demas(db):
    """Un anuncio que gasto de sobra y no trajo a nadie no necesita que le
    ajusten el presupuesto: necesita que lo apaguen. Si dos reglas aplican,
    manda la mas grave."""
    _mercado(db)
    _anuncio(db, "x")
    _gasto(db, "x", "2026-09-01", 500.0, leads=0)
    assert _reco(db, "x")["accion"] == "apagar"


def test_toda_recomendacion_trae_texto_y_una_accion_conocida(db):
    _mercado(db)
    for a in anuncios_en_curso(db, "2026-03-01", "2026-12-31"):
        r = a["recomendacion"]
        assert r["texto"] and isinstance(r["texto"], str)
        assert r["accion"] in ("apagar", "ajustar", "subir", "renovar",
                               "esperar", "dejar")


def test_sin_ningun_lead_en_toda_la_cuenta_igual_se_opina(db):
    """Si ninguno llego a la muestra minima no hay mediana, y sin referencia
    todas las reglas se caerian a "dejar" — justo cuando la cuenta viene floja,
    que es cuando mas falta hace opinar. El respaldo es el CPL del conjunto."""
    _anuncio(db, "a")
    _gasto(db, "a", "2026-09-01", 100.0, leads=2)    # CPL 50
    _anuncio(db, "b")
    _gasto(db, "b", "2026-09-01", 300.0, leads=0)
    # El conjunto da 400/2 = 200 por lead. `b` gasto 300: oportunidad 1,5.
    assert _uno(db, "b")["mediana_cpl"] == 200.0
    assert _reco(db, "b")["accion"] == "esperar"


def test_la_oportunidad_viaja_al_panel(db):
    """Es el numero que sostiene la recomendacion: tiene que poder mirarse."""
    _mercado(db)                       # mediana = 10
    _anuncio(db, "x")
    _gasto(db, "x", "2026-09-01", 55.0, leads=0)
    assert _uno(db, "x")["oportunidad"] == 5.5


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


def test_con_pocos_leads_no_se_dice_que_va_bien(db):
    """Visto contra la cuenta real: un anuncio con 1 lead a 44,41 con la
    mediana en 16,30 recibia "Por ahora va bien". Un lead que salio el triple
    no va bien — y tampoco va mal, porque con uno solo no se sabe. El texto
    tiene que no opinar, no opinar al reves."""
    _mercado(db)                                     # mediana = 10
    _anuncio(db, "x")
    _gasto(db, "x", "2026-09-01", 27.0, leads=1)     # CPL 27, oportunidad 2,7
    r = _reco(db, "x")
    assert r["accion"] == "esperar"
    assert "va bien" not in r["texto"].lower()
    assert "suerte" in r["texto"].lower()
