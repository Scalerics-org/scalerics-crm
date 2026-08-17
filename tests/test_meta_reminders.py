import json
import logging
import re
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


def test_las_fechas_se_guardan_en_el_formato_que_compara(db):
    """sent_at y unsubscribed_at tienen que usar '%Y-%m-%d %H:%M:%S' UTC, igual
    que scraped_at: es el formato con el que datetime('now', ...) compara."""
    token = registrar_envio(db, 500)
    dar_de_baja(db, token)

    conn = sqlite3.connect(db)
    try:
        sent_at, unsub_at = conn.execute(
            "SELECT sent_at, unsubscribed_at FROM meta_reminders WHERE business_id = 500"
        ).fetchone()
        (recientes,) = conn.execute(
            "SELECT COUNT(*) FROM meta_reminders WHERE sent_at >= datetime('now','-1 day')"
        ).fetchone()
        (viejos,) = conn.execute(
            "SELECT COUNT(*) FROM meta_reminders WHERE sent_at < datetime('now','-1 day')"
        ).fetchone()
    finally:
        conn.close()

    patron = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"
    assert re.fullmatch(patron, sent_at), f"sent_at no compara: {sent_at!r}"
    assert re.fullmatch(patron, unsub_at), f"unsubscribed_at no compara: {unsub_at!r}"
    assert recientes == 1, "un envio de recien tiene que caer dentro de las ultimas 24 horas"
    assert viejos == 0


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

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="ok") as enviar:
        primera = enviar_recordatorios(db, "https://crm")
        segunda = enviar_recordatorios(db, "https://crm")

    assert primera["enviados"] == 1
    assert segunda["enviados"] == 0, "la segunda corrida no le escribe de nuevo"
    assert enviar.call_count == 1


def _ya_enviado(conn, bid, dias):
    cuando = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO meta_reminders (business_id, token, sent_at) VALUES (?,?,?)",
        (bid, f"token-{bid}", cuando),
    )


def test_el_tope_de_15_es_por_dia_no_por_corrida(db):
    """Sin esto, cada reinicio de la maquina dispara otros 15 mails."""
    conn = sqlite3.connect(db)
    _lead(conn, 90, dias=5)
    for i in range(15):
        _ya_enviado(conn, 1000 + i, dias=0)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="ok") as enviar:
        res = enviar_recordatorios(db, "https://crm")

    assert enviar.called is False, "ya se mandaron 15 en las ultimas 24 horas"
    assert res["enviados"] == 0
    assert res["candidatos"] == 0


def test_pasado_el_dia_la_cuota_se_renueva(db):
    conn = sqlite3.connect(db)
    _lead(conn, 91, dias=5)
    for i in range(15):
        _ya_enviado(conn, 1100 + i, dias=3)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="ok") as enviar:
        res = enviar_recordatorios(db, "https://crm")

    assert enviar.call_count == 1, "los 15 de hace 3 dias no gastan la cuota de hoy"
    assert res["enviados"] == 1


def test_la_cuota_del_dia_es_lo_que_queda(db):
    conn = sqlite3.connect(db)
    for i in range(5):
        _lead(conn, 200 + i, dias=5 + i)
    for i in range(13):
        _ya_enviado(conn, 1200 + i, dias=0)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="ok") as enviar:
        res = enviar_recordatorios(db, "https://crm")

    assert enviar.call_count == 2, "quedaban 2 de los 15 del dia"
    assert res["enviados"] == 2


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

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="fallo"):
        res = enviar_recordatorios(db, "https://crm")

    assert res["enviados"] == 0
    assert res["fallidos"] == 1
    assert leads_a_recordar(db), "si no salio, tiene que poder reintentarse manana"


def test_un_fallo_seguro_borra_el_registro_y_manana_se_reintenta(db):
    """'fallo' es Resend diciendo que no: el mail no salio, se puede reintentar."""
    conn = sqlite3.connect(db)
    _lead(conn, 71, dias=5)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="fallo"):
        res = enviar_recordatorios(db, "https://crm")

    conn = sqlite3.connect(db)
    try:
        (filas,) = conn.execute(
            "SELECT COUNT(*) FROM meta_reminders WHERE business_id = 71"
        ).fetchone()
    finally:
        conn.close()

    assert filas == 0, "un fallo seguro borra el registro"
    assert res["fallidos"] == 1
    assert res.get("inciertos", 0) == 0
    assert [x["id"] for x in leads_a_recordar(db)] == [71], "manana se reintenta"


def test_un_envio_incierto_deja_la_fila_y_el_token_puestos(db, caplog):
    """Un timeout pudo haber mandado el mail igual. Si borramos la fila, manana
    le llega un segundo mail — y si la persona se dio de baja con el link de ese
    primer mail, el token ya no existe y la baja no lo protege."""
    conn = sqlite3.connect(db)
    _lead(conn, 72, dias=5)
    conn.commit()
    conn.close()

    with patch("services.meta_reminders.send_meta_lead_reminder", return_value="desconocido"), \
         caplog.at_level(logging.ERROR, logger="services.meta_reminders"):
        res = enviar_recordatorios(db, "https://crm")

    conn = sqlite3.connect(db)
    try:
        fila = conn.execute(
            "SELECT token FROM meta_reminders WHERE business_id = 72"
        ).fetchone()
    finally:
        conn.close()

    assert fila, "ante la duda la fila se queda: mandar de menos, no de mas"
    assert res["enviados"] == 0
    assert res["fallidos"] == 0, "un incierto no es un fallo"
    assert res["inciertos"] == 1
    assert "72" in caplog.text, "el incierto tiene que quedar en el log con el business_id"
    assert leads_a_recordar(db) == [], "manana NO se le vuelve a escribir"

    assert dar_de_baja(db, fila[0]) is True, "el link de baja de ese mail sigue sirviendo"
    assert esta_dado_de_baja(db, 72) is True


class _ConexionQueFallaAlBorrar:
    """Envuelve una conexion real de sqlite3 y hace fallar solo el DELETE de
    limpieza de meta_reminders, para simular un "database is locked" en ese
    paso puntual sin tocar el resto de las consultas. Todo lo demas (incluido
    row_factory, que leads_a_recordar necesita) se delega a la conexion real."""

    def __init__(self, real):
        object.__setattr__(self, "_real", real)

    def execute(self, sql, *args, **kwargs):
        if sql.strip().startswith("DELETE FROM meta_reminders"):
            raise sqlite3.OperationalError("database is locked")
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __setattr__(self, name, value):
        setattr(self._real, name, value)


def test_si_el_borrado_de_limpieza_tambien_falla_no_aborta_la_tanda(db, caplog):
    conn = sqlite3.connect(db)
    _lead(conn, 80, dias=6)
    _lead(conn, 81, dias=5)
    conn.commit()
    conn.close()

    conectar_real = sqlite3.connect

    def _conn_que_falla(db_path):
        return _ConexionQueFallaAlBorrar(conectar_real(db_path))

    with patch("services.meta_reminders._conn", side_effect=_conn_que_falla), \
         patch("services.meta_reminders.send_meta_lead_reminder", return_value="fallo"), \
         caplog.at_level(logging.ERROR, logger="services.meta_reminders"):
        res = enviar_recordatorios(db, "https://crm")

    assert res["candidatos"] == 2
    assert res["enviados"] == 0
    assert res["fallidos"] == 2, "no exploto: proceso a los dos candidatos aunque el borrado fallara"
    assert "80" in caplog.text and "81" in caplog.text, (
        "el fallo de limpieza tiene que quedar visible en el log, con el id del lead"
    )
