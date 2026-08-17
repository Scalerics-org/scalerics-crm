import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from database import init_db
from services.meta_reminders import (
    dar_de_baja,
    enviar_recordatorios,
    esta_dado_de_baja,
    leads_a_recordar,
    registrar_envio,
)


@pytest.fixture
def db(tmp_path):
    ruta = str(tmp_path / "leads.db")
    init_db(ruta)
    return ruta


def test_registrar_envio_es_una_sola_vez(db):
    token = registrar_envio(db, 42)
    assert token, "tiene que devolver un token"

    with pytest.raises(sqlite3.IntegrityError):
        registrar_envio(db, 42)


def test_la_baja_marca_al_lead(db):
    token = registrar_envio(db, 7)
    assert esta_dado_de_baja(db, 7) is False

    assert dar_de_baja(db, token) is True
    assert esta_dado_de_baja(db, 7) is True


def test_un_token_que_no_existe_no_rompe(db):
    assert dar_de_baja(db, "token-inventado") is False


def test_la_pagina_de_baja_funciona_sin_login(tmp_path):
    from dashboard import create_app
    from services.meta_reminders import esta_dado_de_baja, registrar_envio

    ruta = str(tmp_path / "baja.db")
    init_db(ruta)  # create_app NO crea las tablas: eso lo hace server.py aparte
    app = create_app(ruta)
    app.config["TESTING"] = True
    token = registrar_envio(ruta, 99)

    r = app.test_client().get(f"/baja/{token}")

    assert r.status_code == 200, "la pagina de baja no puede exigir login"
    assert esta_dado_de_baja(ruta, 99) is True


def test_la_pagina_de_baja_con_token_invalido_no_rompe(tmp_path):
    from dashboard import create_app

    ruta = str(tmp_path / "baja2.db")
    init_db(ruta)
    app = create_app(ruta)
    app.config["TESTING"] = True

    r = app.test_client().get("/baja/no-existe")

    assert r.status_code == 200, "un token viejo o mal copiado muestra una pagina, no un error"


def _lead(conn, bid, dias, **kw):
    campos = {
        "crm_status": "sin_contactar",
        "email": f"lead{bid}@ejemplo.com",
        "source": "meta",
        "form_data": json.dumps({
            "¿cómo_se_llama_tu_negocio?": f"Negocio {bid}",
            "¿que_es_lo_que_buscás_para_tu_negocio?": "una_nueva_página_web",
        }),
    }
    campos.update(kw)
    cuando = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO businesses (id, name, email, source, crm_status, form_data, scraped_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (bid, f"Lead {bid}", campos["email"], campos["source"],
         campos["crm_status"], campos["form_data"], cuando),
    )


def test_elige_solo_a_los_que_corresponde(db):
    conn = sqlite3.connect(db)
    _lead(conn, 1, dias=5)                                  # elegible
    _lead(conn, 2, dias=1)                                  # muy nuevo
    _lead(conn, 3, dias=5, crm_status="reunion_hecha")      # ya lo contactaron
    _lead(conn, 4, dias=5, email=None)                      # sin mail
    _lead(conn, 5, dias=5, source="google")                 # no es de Meta
    conn.commit()
    conn.close()

    elegidos = [x["id"] for x in leads_a_recordar(db)]

    assert elegidos == [1]


def test_no_repite_a_quien_ya_recibio(db):
    conn = sqlite3.connect(db)
    _lead(conn, 10, dias=5)
    conn.commit()
    conn.close()

    assert [x["id"] for x in leads_a_recordar(db)] == [10]
    registrar_envio(db, 10)
    assert leads_a_recordar(db) == []


def test_respeta_el_limite_y_prioriza_a_los_mas_viejos(db):
    conn = sqlite3.connect(db)
    for i, dias in enumerate([5, 40, 20], start=20):
        _lead(conn, i, dias=dias)
    conn.commit()
    conn.close()

    elegidos = [x["id"] for x in leads_a_recordar(db, limite=2)]

    assert elegidos == [21, 22], "primero el de 40 dias, despues el de 20"


def test_trae_los_datos_para_personalizar(db):
    conn = sqlite3.connect(db)
    _lead(conn, 30, dias=5)
    conn.commit()
    conn.close()

    lead = leads_a_recordar(db)[0]

    assert lead["negocio"] == "Negocio 30"
    assert lead["rubro"] == "una nueva página web", "los guiones bajos se limpian"


def test_correr_dos_veces_manda_un_solo_mail(db):
    conn = sqlite3.connect(db)
    _lead(conn, 50, dias=5)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value=True) as enviar:
        primera = enviar_recordatorios(db, "https://crm")
        segunda = enviar_recordatorios(db, "https://crm")

    assert primera["enviados"] == 1
    assert segunda["enviados"] == 0, "la segunda corrida no le escribe de nuevo"
    assert enviar.call_count == 1


def test_dry_run_no_manda_ni_registra(db):
    conn = sqlite3.connect(db)
    _lead(conn, 60, dias=5)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder") as enviar:
        res = enviar_recordatorios(db, "https://crm", dry_run=True)

    assert enviar.called is False
    assert res["candidatos"] == 1
    assert leads_a_recordar(db), "sigue elegible: el dry-run no registro nada"


def test_si_el_mail_falla_no_lo_da_por_enviado(db):
    conn = sqlite3.connect(db)
    _lead(conn, 70, dias=5)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value=False):
        res = enviar_recordatorios(db, "https://crm")

    assert res["enviados"] == 0
    assert res["fallidos"] == 1
    assert leads_a_recordar(db), "si no salio, tiene que poder reintentarse manana"
