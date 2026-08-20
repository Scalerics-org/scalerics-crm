"""El pitch de WhatsApp no es para la cohorte de discovery.

Todas las plantillas semilla dicen literalmente "sin sitio web propio" o "no
tiene pagina web propia". Los comercios de discovery si la tienen: el spec
dice que a estos no se les vende una pagina web, ya la tienen.
"""

import pitch_generator
from database import (get_business, init_db, insert_business,
                      seed_pitch_templates)


def _db_con(tmp_path, negocios):
    path = str(tmp_path / "pitches.db")
    init_db(path)
    seed_pitch_templates(path)
    ids = {}
    for n in negocios:
        ids[n["name"]] = insert_business(path, n)
    return path, ids


def test_run_saltea_a_los_de_discovery(tmp_path):
    db, ids = _db_con(tmp_path, [
        {"name": "Sin Web", "phone": "+598 2900 0001",
         "maps_url": "https://maps.google.com/?cid=1",
         "category": "Ferreteria", "city": "Salto"},
        {"name": "Con Web", "phone": "+598 2900 0002",
         "maps_url": "https://maps.google.com/?cid=2",
         "category": "Inmobiliaria", "city": "Salto",
         "website": "https://conweb.com.uy", "source": "discovery"},
    ])

    pitch_generator.run(db)

    assert get_business(db, ids["Sin Web"])["pitch_text"], \
        "el padron de WhatsApp sigue recibiendo su pitch"
    assert not get_business(db, ids["Con Web"])["pitch_text"], \
        "a un comercio con web no se le manda 'vi que no tenes pagina'"


def test_bg_pitch_no_se_dispara_para_discovery(tmp_path):
    """api_create_lead lanza _bg_pitch en cada insercion remota, asi que con
    CRM_URL seteado la cohorte de discovery se autogeneraria el texto sin que
    nadie corra nada."""
    from routes.leads import _bg_pitch

    db, ids = _db_con(tmp_path, [
        {"name": "Con Web", "phone": "+598 2900 0003",
         "maps_url": "https://maps.google.com/?cid=3",
         "category": "Inmobiliaria", "city": "Salto",
         "website": "https://conweb.com.uy", "source": "discovery"},
        {"name": "Sin Web", "phone": "+598 2900 0004",
         "maps_url": "https://maps.google.com/?cid=4",
         "category": "Ferreteria", "city": "Salto"},
    ])

    _bg_pitch(db, ids["Con Web"], {"name": "Con Web", "category": "Inmobiliaria",
                                   "city": "Salto", "source": "discovery"})
    _bg_pitch(db, ids["Sin Web"], {"name": "Sin Web", "category": "Ferreteria",
                                   "city": "Salto"})

    assert not get_business(db, ids["Con Web"])["pitch_text"]
    assert get_business(db, ids["Sin Web"])["pitch_text"], \
        "el padron sin web sigue recibiendo su pitch en la insercion remota"
