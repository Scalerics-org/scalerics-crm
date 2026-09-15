"""Recuperar de que anuncio vino cada lead.

De 245 leads de Meta, solo 3 tenian guardado el `ad_id`. No porque Meta no lo
mande —de los 109 leads que la API todavia conserva, 108 lo traen— sino porque
la importacion diaria pedia el campo a la API y despues no lo guardaba: el
webhook llamaba a `_guardar_campana_del_lead` y el import no.

Sin eso no se puede contestar la unica pregunta que importa para decidir la
proxima campana: que creativo trajo a los que compraron. Se sabe cuanto costo
cada lead y nada sobre cual sirvio.

**Meta guarda los leads 90 dias.** El backfill recupera lo que este adentro de
esa ventana; lo de antes se perdio y no hay de donde sacarlo. Por eso ademas se
arregla el camino de entrada: para que no vuelva a pasar.
"""

import pytest

from database import _connect, init_db, insert_business
from services.meta_atribucion import backfill_atribucion


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "test.db")
    init_db(ruta)
    return ruta


def _lead(db, nombre, telefono=None, email=None, **extra):
    bid = insert_business(db, {
        "name": nombre, "phone": telefono, "email": email,
        "category": "Meta Lead Ad", "status": "scraped", "source": "meta",
        "score": 70, **extra})
    return bid


def _col(db, bid, col):
    conn = _connect(db)
    try:
        return conn.execute(f"SELECT {col} c FROM businesses WHERE id = ?",
                            (bid,)).fetchone()["c"]
    finally:
        conn.close()


# La forma sale de una llamada real a /{form_id}/leads el 15/9/2026.
def _api(*leads):
    return lambda: list(leads)


def _crudo(lead_id, telefono="+59899123456", email="a@b.com", **kw):
    base = {
        "id": lead_id,
        "created_time": "2026-08-13T14:20:00+0000",
        "is_organic": False,
        "ad_id": "120253602403650249",
        "ad_name": "Tu Web - Mayo2026",
        "adset_id": "120249964634630250",
        "campaign_id": "120243368449890000",
        "campaign_name": "Leads - UY - 2026",
        "field_data": [
            {"name": "full_name", "values": ["Quien Sea"]},
            {"name": "phone_number", "values": [telefono]},
            {"name": "email", "values": [email]},
        ],
    }
    base.update(kw)
    return base


# ── Emparejar ──────────────────────────────────────────────────────────────

def test_empareja_por_telefono_y_guarda_el_anuncio(db):
    bid = _lead(db, "Quien Sea", telefono="+59899123456")
    r = backfill_atribucion(db, traer=_api(_crudo("L1")))
    assert r["emparejados"] == 1
    assert _col(db, bid, "meta_ad_id") == "120253602403650249"
    assert _col(db, bid, "meta_ad_name") == "Tu Web - Mayo2026"
    assert _col(db, bid, "meta_adset_id") == "120249964634630250"
    assert _col(db, bid, "meta_campaign_name") == "Leads - UY - 2026"


def test_empareja_aunque_el_telefono_este_escrito_distinto(db):
    """El CRM guarda "099123456" y Meta manda "+59899123456". Son el mismo."""
    bid = _lead(db, "Quien Sea", telefono="099123456")
    backfill_atribucion(db, traer=_api(_crudo("L1", telefono="+59899123456")))
    assert _col(db, bid, "meta_ad_id") == "120253602403650249"


def test_si_no_hay_telefono_empareja_por_mail(db):
    bid = _lead(db, "Quien Sea", telefono=None, email="Juan@Ejemplo.COM")
    backfill_atribucion(db, traer=_api(
        _crudo("L1", telefono="", email="juan@ejemplo.com")))
    assert _col(db, bid, "meta_ad_id") == "120253602403650249"


def test_guarda_el_id_del_lead_de_meta(db):
    """Sin el, un lead no se puede volver a consultar contra la API ni auditar.
    Es lo que faltaba para poder responder cualquier pregunta nueva."""
    bid = _lead(db, "Quien Sea", telefono="+59899123456")
    backfill_atribucion(db, traer=_api(_crudo("L1")))
    assert _col(db, bid, "meta_lead_id") == "L1"


def test_la_segunda_corrida_empareja_por_el_id_y_no_duplica(db):
    bid = _lead(db, "Quien Sea", telefono="+59899123456")
    backfill_atribucion(db, traer=_api(_crudo("L1")))
    r = backfill_atribucion(db, traer=_api(_crudo("L1")))
    assert r["emparejados"] == 1
    assert _col(db, bid, "meta_ad_id") == "120253602403650249"


def test_un_lead_de_la_api_que_no_esta_en_el_crm_se_cuenta_aparte(db):
    """Puede pasar: un lead que entro y se borro del CRM a mano. No es un
    error, pero tiene que verse en el resultado."""
    r = backfill_atribucion(db, traer=_api(_crudo("L1")))
    assert r["emparejados"] == 0
    assert r["sin_emparejar"] == 1


def test_no_toca_leads_que_no_son_de_meta(db):
    """Un negocio de Google con el mismo telefono no es el mismo lead."""
    bid = insert_business(db, {
        "name": "Otro", "phone": "+59899123456", "source": "discovery",
        "status": "scraped", "score": 50})
    backfill_atribucion(db, traer=_api(_crudo("L1")))
    assert _col(db, bid, "meta_ad_id") is None


# ── Lo que NO se pisa ──────────────────────────────────────────────────────

def test_un_dato_ausente_no_borra_uno_que_ya_estaba(db):
    """Meta no siempre manda los cinco campos. Sin esto, una corrida sin
    `campaign_id` le borraria al lead la campana que ya se sabia — que es como
    se perdieron los primeros."""
    bid = _lead(db, "Quien Sea", telefono="+59899123456")
    backfill_atribucion(db, traer=_api(_crudo("L1")))
    backfill_atribucion(db, traer=_api(
        _crudo("L1", ad_id=None, ad_name=None, campaign_name=None)))
    assert _col(db, bid, "meta_ad_id") == "120253602403650249"
    assert _col(db, bid, "meta_campaign_name") == "Leads - UY - 2026"


def test_marca_el_lead_organico(db):
    """Un lead organico es alguien que llego al formulario sin anuncio. No
    tiene anuncio que atribuirle y no es un dato que falte: es otra cosa, y
    mezclarlo con la pauta ensucia el costo por lead."""
    bid = _lead(db, "Quien Sea", telefono="+59899123456")
    backfill_atribucion(db, traer=_api(
        _crudo("L1", is_organic=True, ad_id=None, campaign_name=None)))
    assert _col(db, bid, "meta_organico") == 1
    assert _col(db, bid, "meta_ad_id") is None


def test_el_que_vino_de_un_anuncio_no_queda_marcado_como_organico(db):
    bid = _lead(db, "Quien Sea", telefono="+59899123456")
    backfill_atribucion(db, traer=_api(_crudo("L1")))
    assert _col(db, bid, "meta_organico") == 0


# ── Forma del resultado ────────────────────────────────────────────────────

def test_el_resultado_dice_cuantos_ganaron_anuncio(db):
    """Es el numero que interesa: cuantos leads pasaron de no tener atribucion
    a tenerla."""
    _lead(db, "Con tel", telefono="+59899111111")
    _lead(db, "Otro", telefono="+59899222222")
    r = backfill_atribucion(db, traer=_api(
        _crudo("L1", telefono="+59899111111"),
        _crudo("L2", telefono="+59899222222"),
        _crudo("L3", telefono="+59899333333")))
    assert r["emparejados"] == 2
    assert r["con_anuncio"] == 2
    assert r["sin_emparejar"] == 1


def test_sin_credenciales_avisa_y_no_revienta(db):
    import os

    import services.meta_atribucion as m

    guardado = dict(os.environ)
    try:
        os.environ.pop("META_PAGE_TOKEN", None)
        os.environ.pop("META_PAGE_ID", None)
        r = m.backfill_atribucion(db)
        assert r["salteado"] == "sin_credenciales"
    finally:
        os.environ.clear()
        os.environ.update(guardado)


# ── El agujero de entrada ──────────────────────────────────────────────────

def test_la_importacion_diaria_guarda_el_anuncio(db, monkeypatch):
    """LA CAUSA de que 242 de 245 leads no tuvieran anuncio.

    El import le pedia `ad_id` y `adset_id` a la API —estan en el `fields` de
    la llamada desde siempre— y despues no los escribia: el webhook llamaba a
    `_guardar_campana_del_lead` y el import no. El dato llegaba y se tiraba.
    """
    import routes.meta as meta

    monkeypatch.setenv("META_PAGE_TOKEN", "x")
    monkeypatch.setenv("META_PAGE_ID", "p1")

    crudo = _crudo("L9", telefono="+59899777777")
    llamadas = []

    def falso_ga(url, params):
        llamadas.append(url)
        if "leadgen_forms" in url:
            return [{"id": "F1", "name": "Leads - Junio 2026"}]
        return [crudo]

    meta._run_import_sync(db, traer=falso_ga)

    conn = _connect(db)
    try:
        f = conn.execute(
            "SELECT meta_ad_id, meta_adset_id, meta_campaign_name, meta_lead_id "
            "FROM businesses WHERE source='meta'").fetchone()
    finally:
        conn.close()
    assert f is not None, "no se creo el lead"
    assert f["meta_ad_id"] == "120253602403650249"
    assert f["meta_adset_id"] == "120249964634630250"
    assert f["meta_campaign_name"] == "Leads - UY - 2026"
    assert f["meta_lead_id"] == "L9"


def test_el_import_le_pide_is_organic_a_la_api():
    """Si no se pide, no viene, y un lead organico queda contado como pauta."""
    import inspect

    import routes.meta as meta

    fuente = inspect.getsource(meta)
    assert fuente.count("is_organic") >= 2, (
        "los dos caminos de entrada tienen que pedir is_organic")
