"""La lista de direcciones vedadas: rebotes duros y quejas de spam.

Un rebote duro es una direccion que no existe. Seguir mandandole es la forma
mas rapida que hay de quemar un dominio de envio, mas rapida todavia que las
quejas. Y una queja de spam es la senal negativa mas cara que existe.

La lista es una sola para las dos campanas a proposito: las dos salen de la
misma cuenta de Resend, y una direccion que rebota en Meta va a rebotar en
discovery. Tenerla por campana seria repetir el error dos veces.
"""

import pytest

from database import init_db, insert_business
from services.mails_vedados import (esta_vedado, listar_vedados, vedar)


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    return ruta


def test_una_direccion_nueva_no_esta_vedada(db):
    assert esta_vedado(db, "hola@comercio.uy") is False


def test_vedar_la_deja_vedada(db):
    vedar(db, "hola@comercio.uy", "rebote_duro", "550 no such user")
    assert esta_vedado(db, "hola@comercio.uy") is True


def test_la_comparacion_ignora_mayusculas_y_espacios(db):
    """Las direcciones vienen de Resend, de sitios raspados y de formularios de
    Meta: cada fuente las escribe distinto."""
    vedar(db, "  Hola@Comercio.UY ", "rebote_duro", "")
    assert esta_vedado(db, "hola@comercio.uy") is True
    assert esta_vedado(db, "HOLA@COMERCIO.UY") is True


def test_vedar_dos_veces_no_explota(db):
    """Resend reintenta los webhooks: el mismo rebote puede llegar varias veces."""
    vedar(db, "hola@comercio.uy", "rebote_duro", "primera")
    vedar(db, "hola@comercio.uy", "queja_spam", "segunda")
    assert esta_vedado(db, "hola@comercio.uy") is True
    assert len(listar_vedados(db)) == 1


def test_la_queja_de_spam_le_gana_al_rebote(db):
    """Si una direccion rebota y ademas se queja, lo que importa recordar es la
    queja: es la senal mas cara y la que hay que poder contar despues."""
    vedar(db, "hola@comercio.uy", "rebote_duro", "")
    vedar(db, "hola@comercio.uy", "queja_spam", "")
    assert listar_vedados(db)[0]["motivo"] == "queja_spam"


def test_un_rebote_no_pisa_una_queja_previa(db):
    vedar(db, "hola@comercio.uy", "queja_spam", "")
    vedar(db, "hola@comercio.uy", "rebote_duro", "")
    assert listar_vedados(db)[0]["motivo"] == "queja_spam"


def test_una_direccion_vacia_no_se_veda(db):
    """Vedar "" haria que el LOWER(TRIM(email)) de un lead sin mail matchee y se
    lleve puesta media cohorte."""
    for basura in ("", "   ", None):
        vedar(db, basura, "rebote_duro", "")
    assert listar_vedados(db) == []


def test_listar_trae_el_motivo_y_la_fecha(db):
    vedar(db, "hola@comercio.uy", "rebote_duro", "550 mailbox unavailable")
    fila = listar_vedados(db)[0]
    assert fila["email"] == "hola@comercio.uy"
    assert fila["motivo"] == "rebote_duro"
    assert "550" in fila["detalle"]
    assert fila["creado_at"]


# ─── Que las campanas la respeten ─────────────────────────────────────────────

def test_discovery_no_le_escribe_a_una_direccion_vedada(db):
    from services.discovery_emails import comercios_a_contactar
    insert_business(db, {"name": "C1", "phone": "099000001", "email": "rebota@x.uy",
                         "website": "https://x.uy", "source": "discovery",
                         "maps_url": "https://maps.google.com/?cid=1"})
    assert len(comercios_a_contactar(db, 10)) == 1
    vedar(db, "rebota@x.uy", "rebote_duro", "")
    assert comercios_a_contactar(db, 10) == []


def test_discovery_tampoco_le_manda_el_seguimiento(db):
    """El caso que mas importa: el primero salio y rebotó, el segundo no puede
    salir igual.

    El envio se antedata porque el seguimiento pide 7 dias desde el primero.
    Sin eso el test pasaba por la razon equivocada: daba vacio por el piso de
    dias, no por la veda.
    """
    import sqlite3

    from services.discovery_emails import comercios_a_seguir, registrar_envio
    bid = insert_business(db, {"name": "C2", "phone": "099000002", "email": "rebota2@x.uy",
                               "website": "https://x.uy", "source": "discovery",
                               "maps_url": "https://maps.google.com/?cid=2"})
    registrar_envio(db, bid, 1)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE discovery_reminders SET sent_at = datetime('now','-30 days') "
                 "WHERE business_id = ?", (bid,))
    conn.commit()
    conn.close()

    assert [c["id"] for c in comercios_a_seguir(db, 10)] == [bid],         "sin la veda tiene que ser candidato, si no el test no prueba nada"

    vedar(db, "rebota2@x.uy", "rebote_duro", "")
    assert comercios_a_seguir(db, 10) == []


def _lead_meta(db, n, **extra):
    datos = {"name": f"Lead {n}", "phone": f"098000{n:03}", "email": f"lead{n}@x.uy",
             "source": "meta", "scraped_at": "2020-01-01 00:00:00",
             "maps_url": f"https://maps.google.com/?cid=meta{n}"}
    datos.update(extra)
    return insert_business(db, datos)


def test_meta_no_le_escribe_a_una_direccion_vedada(db):
    """La lista es una sola para las dos campanas: lo que rebota escribiendole a
    un lead de Meta rebota igual en discovery, y al reves."""
    from services.meta_reminders import leads_a_recordar
    _lead_meta(db, 1)
    assert len(leads_a_recordar(db)) == 1, "sin la veda tiene que ser candidato"
    vedar(db, "lead1@x.uy", "queja_spam", "")
    assert leads_a_recordar(db) == []


def test_meta_tampoco_le_manda_el_seguimiento(db):
    import sqlite3

    from services.meta_reminders import leads_a_seguir, registrar_envio
    bid = _lead_meta(db, 2)
    registrar_envio(db, bid, 1)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE meta_reminders SET sent_at = datetime('now','-60 days') "
                 "WHERE business_id = ?", (bid,))
    conn.commit()
    conn.close()

    assert [l["id"] for l in leads_a_seguir(db)] == [bid], \
        "sin la veda tiene que ser candidato, si no el test no prueba nada"
    vedar(db, "lead2@x.uy", "rebote_duro", "")
    assert leads_a_seguir(db) == []
